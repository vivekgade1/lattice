"""Clean-checkout verification: the verdict of a candidate commit, from the base's protected files.

A rebuild's own gate verdict is made in the agent's worktree, where the agent could have left
untracked files, edited ``check.sh`` or the test harness, or changed the spec. This module
re-derives the verdict without trusting any of that:

1. :func:`protected_paths` -- the set of files the agent must not change, computed from the BASE
   commit (never from the candidate): the spec files, ``studio.toml``, ``check.sh``, the
   ``[mcp] protected`` names, the ``[grpc] protos``, the owner's rationale / glossary notes, any
   extra harness files, and the gate scripts the gate commands name.
2. :func:`diff_protected` -- those files hashed at the base and at the candidate (a commit, or
   a directory) and compared: a :class:`ProtectedDiff` (changed / deleted / added).
3. :func:`clean_tree` -- the candidate commit materialised from ``git archive`` (or a detached
   worktree), so no untracked or ignored file of the agent's tree can leak in; removed again
   even when the body raises.
4. :func:`overlay_protected` -- the base's protected files written over that tree (and any
   protected file the base lacks removed), so a tampered ``check.sh`` or harness cannot affect
   the verdict.
5. :func:`run_gate` -- the gate command in that tree with the scrubbed environment of
   :func:`gate_env`, and :func:`verify` -- all of it, ``runs`` times, as a :class:`CleanVerdict`.

The logic is copied from ``lattice/studio/rebuild.py`` (``Rebuilder._protected_paths``,
``_restore_protected``, ``_restore_tree``, ``_snapshot_tree``, ``integration_worktree``,
``gate_round`` / ``remove_worktree``, ``gate_env``) and from the clean-archive check of
``dogfood/dn_real_agent_run.py`` (``cmd_build``). It does not import ``rebuild``; ``rebuild``
delegates here (R4): :func:`protected_paths` with ``harness=False, config=False,
notes="committed"`` is the rebuild's own set, :func:`overlay_protected` with ``keep_ignored``
its restore, :func:`snapshot_tree` and :func:`add_worktree` its tree snapshot and integration
worktree, and :func:`verify` its opt-in ``[rebuild] clean_verify``. It never calls a model.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import stat as statmod
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.llm.cli_client import cli_env
from lattice.studio.state import terminate_tree

__all__ = [
    "CleanVerdict",
    "CleanVerifyError",
    "ProtectedDiff",
    "add_worktree",
    "clean_tree",
    "diff_protected",
    "dir_digest",
    "gate_env",
    "list_protected",
    "overlay_protected",
    "protected_paths",
    "resolve",
    "run_gate",
    "snapshot_tree",
    "verify",
]

SPEC_FILES = ("REQUIREMENTS.md", "spec.py", "spec.trace.json")
HARNESS_FILES = ("studio.toml", "check.sh")  # always protected, whatever the config says
LATTICE_ROOT = Path(__file__).resolve().parents[2]  # the directory holding the package
DEFAULT_TIMEOUT = 1800.0
DEFAULT_GRACE = 5.0
TAIL_CHARS = 4000
_SCRIPT_RUNNERS = ("sh", "bash", "zsh", "python", "python3")  # `bash scripts/gate.sh`


class CleanVerifyError(RuntimeError):
    """The verification itself could not be done (a bad ref, a git failure): not a verdict."""


# ---- git ---------------------------------------------------------------------------------


def _git(
    root: Path, *args: str, timeout: float = 60, input: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, timeout=timeout, input=input
    )


def _git_ok(root: Path, *args: str) -> bytes:
    r = _git(root, *args)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).decode(errors="replace").strip()
        raise CleanVerifyError(f"git {' '.join(args)}: {msg}")
    return r.stdout


def resolve(repo: Path, ref: str) -> str:
    """The full commit sha of `ref`; :class:`CleanVerifyError` when it names no commit."""
    r = _git(Path(repo), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if r.returncode != 0:
        raise CleanVerifyError(f"{ref!r} is not a commit of {repo}")
    return r.stdout.decode().strip()


def _show(repo: Path, sha: str, path: str) -> bytes | None:
    r = _git(repo, "cat-file", "-p", f"{sha}:{path}")
    return r.stdout if r.returncode == 0 else None


# ---- the protected set -------------------------------------------------------------------


def _norm(raw: str) -> str | None:
    """A repo-relative POSIX path, or None for an empty, absolute or escaping one. A trailing
    ``/`` is kept (a directory name; ``dontAsk`` deny rules read it, R4)."""
    s = str(raw).replace("\\", "/")
    n = posixpath.normpath(s)
    if n in (".", "", "..") or n.startswith(("/", "../")):
        return None
    return n + "/" if s.endswith("/") else n


def _gate_scripts(repo: Path, base: str, gates: Sequence[str]) -> list[str]:
    """The scripts the gate commands name (``./check.sh``, ``bash scripts/gate.sh``) that exist
    at `base`: the command's first path-like token, as ``_protected_paths`` reads it."""
    out: list[str] = []
    for g in gates:
        try:
            toks = shlex.split(g)
        except ValueError:
            continue
        while toks and re.fullmatch(r"\w+=.*", toks[0]):  # leading VAR=value
            toks = toks[1:]
        if len(toks) > 1 and posixpath.basename(toks[0]) in _SCRIPT_RUNNERS:
            toks = toks[1:]
        if toks and "/" in toks[0] and not os.path.isabs(toks[0]):
            p = _norm(toks[0])
            if p and _git(repo, "cat-file", "-e", f"{base}:{p}").returncode == 0:
                out.append(p)
    return out


def protected_paths(
    repo: Path,
    base: str,
    *,
    project_rel: str = ".",
    gates: Sequence[str] = (),
    extra: Sequence[str] = (),
    harness: bool = True,
    config: bool = True,
    notes: str = "named",
) -> list[str]:
    """The repo-relative paths the agent must not change, read from the `base` commit.

    Project files (under `project_rel`): ``REQUIREMENTS.md``, ``spec.py``, ``spec.trace.json``,
    ``studio.toml``, ``check.sh``, ``[mcp] protected`` and ``[grpc] protos`` of ``studio.toml``,
    the rationale / glossary notes the trace's ``source`` names, and `extra` (more harness
    files; a directory means everything under it). Repo-relative: the scripts the `gates`
    name that exist at `base`. A name may be a file or a directory.

    A rebuild's own (narrower) set, ``Rebuilder._protected_paths`` (R4): ``harness=False``
    (``studio.toml`` / ``check.sh`` only when `extra` or a gate names them), ``config=False``
    (``[mcp] protected`` and the protos come from Studio's config, in `extra`) and
    ``notes="committed"`` (a note only when the base has it; ``"named"``: whenever the trace
    names it)."""
    if notes not in ("named", "committed"):
        raise ValueError(f"notes must be named or committed, not {notes!r}")
    repo = Path(repo)
    rel = "" if project_rel in (".", "") else project_rel.strip("/") + "/"
    names: list[str] = [*SPEC_FILES, *(HARNESS_FILES if harness else ()), *extra]
    cfg_text = _show(repo, base, f"{rel}studio.toml") if config else None
    if cfg_text is not None:
        try:
            cfg = tomllib.loads(cfg_text.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            cfg = {}
        for table, key in (("mcp", "protected"), ("grpc", "protos")):
            t = cfg.get(table)
            if isinstance(t, dict) and isinstance(t.get(key), list):
                names += [x for x in t[key] if isinstance(x, str)]
    trace = _show(repo, base, f"{rel}spec.trace.json")
    if trace is not None:
        try:
            source = json.loads(trace).get("source")
        except (ValueError, AttributeError):
            source = None
        if isinstance(source, str) and source:
            from lattice.spec.rationale import notes_names

            nn = notes_names(source)
            for n in (nn[0], nn[2]):  # rationale, glossary (the .proposed.json drafts are free)
                if (
                    notes == "named"
                    or _git(repo, "cat-file", "-e", f"{base}:{rel}{n}").returncode == 0
                ):
                    names.append(n)
    out: list[str] = []
    for raw in names:
        n = _norm(raw)
        if n:
            out.append(f"{rel}{n}")
    out += _gate_scripts(repo, base, gates)
    return list(dict.fromkeys(out))


# ---- hashing -----------------------------------------------------------------------------


def _blob_oid(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 (git's id)


def list_protected(
    repo: Path,
    paths: Sequence[str],
    *,
    ref: str | None = None,
    root: Path | None = None,
) -> dict[str, str]:
    """``{file path: "<mode> <git blob id>"}`` of the protected `paths` (a directory entry
    expands to its files) as committed at `ref`, or as they are on disk under `root`. The
    two forms compare equal for equal bytes and mode."""
    if (ref is None) == (root is None):
        raise ValueError("give exactly one of ref and root")
    names = [p.rstrip("/") for p in paths if p.rstrip("/")]
    out: dict[str, str] = {}
    if ref is not None:
        if not names:
            return out
        r = _git(Path(repo), "ls-tree", "-r", "-z", "--full-tree", ref, "--", *names)
        if r.returncode != 0:
            raise CleanVerifyError(f"git ls-tree {ref[:10]}: {r.stderr.decode(errors='replace')}")
        for entry in r.stdout.decode("utf-8", "surrogateescape").split("\0"):
            meta, _, path = entry.partition("\t")
            mode, _kind, oid = (meta.split(" ") + ["", ""])[:3]
            if path and oid:
                out[path] = f"{mode} {oid}"
        return out
    assert root is not None
    for n in names:
        p = root / n
        if p.is_dir() and not p.is_symlink():
            files = [f for f in p.rglob("*") if f.is_file() or f.is_symlink()]
        elif p.is_file() or p.is_symlink():
            files = [p]
        else:
            continue
        for f in files:
            rel = f.relative_to(root).as_posix()
            if f.is_symlink():
                out[rel] = f"120000 {_blob_oid(os.readlink(f).encode())}"
            else:
                mode = "100755" if f.stat().st_mode & statmod.S_IXUSR else "100644"
                out[rel] = f"{mode} {_blob_oid(f.read_bytes())}"
    return out


@dataclass(frozen=True)
class ProtectedDiff:
    """The protected files that differ between the base and a candidate."""

    changed: tuple[str, ...] = ()  # in both, different bytes or mode
    deleted: tuple[str, ...] = ()  # in the base, gone from the candidate
    added: tuple[str, ...] = ()  # in the candidate (inside a protected name), not in the base
    paths: tuple[str, ...] = ()  # the protected set that was compared
    checked: int = 0  # how many base files were hashed

    @property
    def clean(self) -> bool:
        return not (self.changed or self.deleted or self.added)

    @property
    def edited(self) -> list[str]:
        return sorted({*self.changed, *self.deleted, *self.added})

    def as_dict(self) -> dict[str, Any]:
        return {
            "clean": self.clean,
            "changed": list(self.changed),
            "deleted": list(self.deleted),
            "added": list(self.added),
            "paths": list(self.paths),
            "checked": self.checked,
        }


def diff_protected(
    repo: Path,
    base: str,
    tip: str | None = None,
    *,
    root: Path | None = None,
    project_rel: str = ".",
    gates: Sequence[str] = (),
    extra: Sequence[str] = (),
    paths: Sequence[str] | None = None,
) -> ProtectedDiff:
    """Hash the protected set at `base` and at the candidate (the commit `tip`, or the
    directory `root`) and report what differs. The set is computed from `base` unless
    `paths` is given."""
    repo = Path(repo)
    base = resolve(repo, base)
    if paths is None:
        paths = protected_paths(repo, base, project_rel=project_rel, gates=gates, extra=extra)
    have = list_protected(repo, paths, ref=base)
    if tip is not None:
        want = list_protected(repo, paths, ref=resolve(repo, tip))
    else:
        if root is None:
            raise ValueError("give tip or root")
        want = list_protected(repo, paths, root=Path(root))
    return ProtectedDiff(
        changed=tuple(sorted(p for p in have if p in want and have[p] != want[p])),
        deleted=tuple(sorted(p for p in have if p not in want)),
        added=tuple(sorted(p for p in want if p not in have)),
        paths=tuple(paths),
        checked=len(have),
    )


def dir_digest(root: Path) -> str:
    """A hash of the files under `root` (path, kind, exec bit, bytes; never ``.git`` or mtimes):
    equal for two materialisations of the same commit."""
    h = hashlib.sha256()
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath == str(root):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            filenames = [f for f in filenames if f != ".git"]  # a worktree's pointer file
        dirnames.sort()
        # a symlink to a directory is listed in dirnames, and is not followed
        names = filenames + [d for d in dirnames if (Path(dirpath) / d).is_symlink()]
        for name in sorted(names):
            f = Path(dirpath) / name
            rel = f.relative_to(root).as_posix()
            if f.is_symlink():
                h.update(b"L %s\0%s\0" % (rel.encode(), os.readlink(f).encode()))
            elif f.is_file():
                x = b"x" if f.stat().st_mode & statmod.S_IXUSR else b"-"
                h.update(b"F %s\0%s\0" % (rel.encode(), x))
                h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


# ---- a clean tree of the candidate -------------------------------------------------------


def _extract_archive(repo: Path, sha: str, dest: Path) -> None:
    """``git archive sha`` extracted into `dest`: only what is committed, nothing from the
    agent's working tree (the ``dn_real_agent_run.py`` precedent)."""
    with subprocess.Popen(
        ["git", "-C", str(repo), "archive", "--format=tar", sha],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        assert proc.stdout is not None
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tf:
                tf.extractall(dest, filter="tar")
        finally:
            proc.stdout.close()
        err = proc.stderr.read() if proc.stderr else b""
        if proc.wait() != 0:
            raise CleanVerifyError(f"git archive {sha[:10]}: {err.decode(errors='replace')}")


@contextlib.contextmanager
def clean_tree(
    repo: Path, tip: str, *, mode: str = "archive", parent: Path | None = None
) -> Iterator[Path]:
    """A fresh tree of the commit `tip`, removed on exit whatever happens in the body.

    ``mode="archive"`` (default): ``git archive`` into a temp directory (no ``.git``).
    ``mode="worktree"``: a detached ``git worktree`` at `tip` (the ``integration_worktree``
    way; ``git worktree remove`` and ``prune`` afterwards)."""
    repo = Path(repo)
    if mode not in ("archive", "worktree"):
        raise ValueError(f"mode must be archive or worktree, not {mode!r}")
    sha = resolve(repo, tip)
    if parent is not None:
        Path(parent).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="lattice-cleanverify-", dir=parent)).resolve()
    tree = tmp / "tree"
    try:
        if mode == "archive":
            tree.mkdir()
            _extract_archive(repo, sha, tree)
        else:
            add_worktree(repo, sha, tree)
        yield tree
    finally:
        if mode == "worktree":
            _git(repo, "worktree", "remove", "--force", str(tree))
        shutil.rmtree(tmp, ignore_errors=True)
        if mode == "worktree":
            _git(repo, "worktree", "prune")


def add_worktree(repo: Path, sha: str, dest: Path) -> Path:
    """A detached ``git worktree`` of `repo` at `sha` in `dest` (``Rebuilder.
    integration_worktree``); :class:`CleanVerifyError` with git's message when it fails."""
    r = _git(Path(repo), "worktree", "add", "-q", "--detach", str(dest), sha)
    if r.returncode != 0:
        raise CleanVerifyError(r.stderr.decode(errors="replace").strip())
    return Path(dest)


def snapshot_tree(wt: Path, exclude: Sequence[str] = ()) -> str:
    """The git tree id of the worktree's files as they are (``git add -A`` minus the
    `exclude` pathspecs, git-ignored files never), through a throwaway index: the worktree's
    own index is untouched (``rebuild._snapshot_tree``)."""
    with tempfile.TemporaryDirectory(prefix="lattice-rebuild-idx-") as d:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(d) / "index")}
        for args in (("read-tree", "HEAD"), ("add", "-A", "--", ".", *exclude)):
            r = subprocess.run(
                ["git", "-C", str(wt), *args], capture_output=True, env=env, timeout=60
            )
            if r.returncode != 0:
                msg = (r.stderr or r.stdout).decode(errors="replace").strip()
                raise CleanVerifyError(f"git {' '.join(args)}: {msg}")
        r = subprocess.run(
            ["git", "-C", str(wt), "write-tree"], capture_output=True, env=env, timeout=60
        )
        if r.returncode != 0:
            raise CleanVerifyError(f"git write-tree: {r.stderr.decode(errors='replace').strip()}")
        return r.stdout.decode().strip()


def _unlink_path(p: Path) -> None:
    if p.is_symlink() or p.is_file():
        p.unlink(missing_ok=True)
    elif p.is_dir():
        shutil.rmtree(p, ignore_errors=True)


def _write_base_file(root: Path, rel: str, mode: str, data: bytes) -> None:
    """Write `data` at `root/rel` without following a symlink the candidate planted: any
    symlink on the way (or at the file) is replaced first, so nothing lands outside `root`."""
    parts = Path(rel).parts
    cur = root
    for part in parts[:-1]:
        cur = cur / part
        if cur.is_symlink() or (cur.exists() and not cur.is_dir()):
            _unlink_path(cur)
        cur.mkdir(exist_ok=True)
    dest = cur / parts[-1]
    if dest.is_symlink() or dest.is_dir() or dest.exists():
        _unlink_path(dest)
    if mode == "120000":
        os.symlink(data.decode("utf-8", "surrogateescape"), dest)
        return
    dest.write_bytes(data)
    dest.chmod(0o755 if mode == "100755" else 0o644)


def overlay_protected(
    repo: Path,
    base: str,
    root: Path,
    *,
    paths: Sequence[str] | None = None,
    project_rel: str = ".",
    gates: Sequence[str] = (),
    extra: Sequence[str] = (),
    keep_ignored: bool = False,
) -> list[str]:
    """Make the protected files under `root` the base commit's: each one rewritten from the
    base, a protected file the base lacks removed. Answers the paths it had to change (what
    ``_restore_protected`` calls edited), so a tampered ``check.sh`` or harness in the
    candidate cannot reach the gate.

    `keep_ignored` (a live git worktree, ``Rebuilder._restore_protected``): a file the base
    lacks under a protected DIRECTORY is left when git ignores it (generated stubs); a
    protected name itself is removed all the same."""
    repo, root = Path(repo), Path(root)
    base = resolve(repo, base)
    if paths is None:
        paths = protected_paths(repo, base, project_rel=project_rel, gates=gates, extra=extra)
    have = list_protected(repo, paths, ref=base)
    cur = list_protected(repo, paths, root=root)
    direct = {p.rstrip("/") for p in paths}
    changed: list[str] = []
    for p in sorted(cur.keys() - have.keys()):  # a protected file the base never had
        if (
            keep_ignored
            and p not in direct
            and _git(root, "check-ignore", "-q", "--", p).returncode == 0
        ):
            continue
        _unlink_path(root / p)
        changed.append(p)
    for p, sig in sorted(have.items()):
        if cur.get(p) == sig:
            continue
        mode = sig.split(" ", 1)[0]
        data = _git_ok(repo, "cat-file", "blob", sig.split(" ", 1)[1])
        _write_base_file(root, p, mode, data)
        changed.append(p)
    return sorted(set(changed))


# ---- the gate ----------------------------------------------------------------------------


def _main_venv(repo: Path) -> Path | None:
    v = repo / ".venv"
    if (v / "bin").is_dir():
        return v
    if sys.prefix != sys.base_prefix:
        return Path(sys.prefix)
    return None


def gate_env(
    base: Mapping[str, str],
    *,
    repo: Path,
    worktree: Path,
    project_rel: str = ".",
    sys_path: Sequence[str] = (),
    extra: Mapping[str, str] | None = None,
    lattice_root: Path = LATTICE_ROOT,
) -> dict[str, str]:
    """The environment of a gate command: `base` without API credentials and provider
    switches (``cli_env`` and every ``ANTHROPIC_*``), ``PYTHONPATH`` = the tree, its project
    directory, `sys_path` and Lattice itself, ``UV_NO_SYNC=1``, the main repository's venv, no
    bytecode files in the tree. A copy of ``rebuild.gate_env``."""
    env = {k: v for k, v in cli_env(base, None).items() if not k.startswith("ANTHROPIC_")}
    wt_project = (Path(worktree) / project_rel).resolve()
    paths = [str(worktree), str(wt_project), *(str((wt_project / s).resolve()) for s in sys_path)]
    paths.append(str(lattice_root))
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(p for p in paths if p))
    env["UV_NO_SYNC"] = "1"
    venv = _main_venv(Path(repo))
    if venv is not None:
        env.setdefault("UV_PROJECT_ENVIRONMENT", str(venv))
        env["VIRTUAL_ENV"] = env["UV_PROJECT_ENVIRONMENT"]
        env["PATH"] = os.pathsep.join(
            p for p in (str(Path(env["UV_PROJECT_ENVIRONMENT"]) / "bin"), env.get("PATH")) if p
        )
    env.pop("PYTHONHOME", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra or {})
    return env


def run_gate(
    cmd: str | Sequence[str],
    cwd: Path,
    env: Mapping[str, str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    grace: float = DEFAULT_GRACE,
) -> dict[str, Any]:
    """One gate command in its own session, stdout and stderr merged. A timeout terminates the
    whole process tree (SIGTERM to the group, `grace`, SIGKILL) and answers exit code 124.
    Answers ``{"exit_code", "output", "timed_out", "seconds"}``."""
    t0 = time.perf_counter()
    argv = ["/bin/sh", "-c", cmd] if isinstance(cmd, str) else list(cmd)
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_tree(proc, [], grace=grace)
        out, _ = proc.communicate()
    finally:
        if proc.poll() is None:  # an exception on the way out: do not leave it running
            terminate_tree(proc, [], grace=0.0)
    rc = 124 if timed_out else (proc.returncode if proc.returncode is not None else 1)
    return {
        "exit_code": rc,
        "output": out or "",
        "timed_out": timed_out,
        "seconds": round(time.perf_counter() - t0, 2),
    }


@dataclass
class CleanVerdict:
    """The verdict of a candidate commit on a clean checkout with the base's protected files."""

    passed: bool  # every run exited 0 AND (when `strict`) the protected set was untouched
    gate_passed: bool  # every run exited 0 on the overlaid clean tree
    exit_code: int  # 0, or the first non-zero run's
    output_tail: str  # the last characters of the first failing run, else the last run
    protected_diff: ProtectedDiff  # candidate commit vs base, before the overlay
    overlaid: list[str]  # the protected files rewritten from the base in the clean tree
    base: str
    tip: str
    runs: int
    exit_codes: list[int] = field(default_factory=list)
    timed_out: bool = False
    tree_digest: str = ""  # of the overlaid tree the gate ran in (same tip: same digest)
    tree_path: str = ""  # where it was (gone now)
    tree_cleaned: bool = False
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "protected_diff"}
        d["protected_diff"] = self.protected_diff.as_dict()
        return d


def verify(
    repo: Path,
    base: str,
    tip: str,
    gate_cmd: str | Sequence[str],
    runs: int = 1,
    *,
    project_rel: str = ".",
    gates: Sequence[str] | None = None,
    extra_protected: Sequence[str] = (),
    mode: str = "archive",
    parent: Path | None = None,
    env: Mapping[str, str] | None = None,
    env_extra: Mapping[str, str] | None = None,
    sys_path: Sequence[str] = (),
    timeout: float = DEFAULT_TIMEOUT,
    grace: float = DEFAULT_GRACE,
    strict: bool = True,
    tail_chars: int = TAIL_CHARS,
) -> CleanVerdict:
    """Verify the commit `tip` against `base`: diff the protected set, materialise `tip` clean,
    overlay the base's protected files, run `gate_cmd` there `runs` times, remove the tree.

    `gates` are the gate commands whose scripts are protected (default: `gate_cmd` when it is a
    string). `strict` (default): a changed protected file fails the verdict even though the
    gate ran on the base's copy; ``gate_passed`` is the gate alone. The gate runs with the
    tree root as its working directory, as a rebuild's gates do."""
    repo = Path(repo)
    base, tip = resolve(repo, base), resolve(repo, tip)
    if gates is None:
        gates = [gate_cmd] if isinstance(gate_cmd, str) else []
    paths = protected_paths(repo, base, project_rel=project_rel, gates=gates, extra=extra_protected)
    pdiff = diff_protected(repo, base, tip, paths=paths)
    t0 = time.perf_counter()
    results: list[dict[str, Any]] = []
    overlaid: list[str] = []
    digest = ""
    with clean_tree(repo, tip, mode=mode, parent=parent) as tree:
        tree_path = str(tree)
        overlaid = overlay_protected(repo, base, tree, paths=paths)
        post = diff_protected(repo, base, root=tree, paths=paths)
        if not post.clean:  # the overlay must leave the base's bytes; anything else is a bug
            raise CleanVerifyError(f"overlay left protected files different: {post.edited}")
        digest = dir_digest(tree)
        genv = gate_env(
            os.environ if env is None else env,
            repo=repo,
            worktree=tree,
            project_rel=project_rel,
            sys_path=sys_path,
            extra=env_extra,
        )
        for _ in range(max(1, runs)):
            results.append(run_gate(gate_cmd, tree, genv, timeout=timeout, grace=grace))
    codes = [r["exit_code"] for r in results]
    failing = next((r for r in results if r["exit_code"] != 0), None)
    shown = failing or results[-1]
    gate_passed = failing is None
    return CleanVerdict(
        passed=gate_passed and (pdiff.clean or not strict),
        gate_passed=gate_passed,
        exit_code=failing["exit_code"] if failing else 0,
        output_tail=shown["output"][-tail_chars:],
        protected_diff=pdiff,
        overlaid=overlaid,
        base=base,
        tip=tip,
        runs=len(results),
        exit_codes=codes,
        timed_out=any(r["timed_out"] for r in results),
        tree_digest=digest,
        tree_path=tree_path,
        tree_cleaned=not Path(tree_path).exists(),
        seconds=round(time.perf_counter() - t0, 2),
    )
