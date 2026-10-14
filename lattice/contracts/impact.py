"""The cross-project impact PREVIEW (multi-project slice 4a): what would a candidate version of a
provider do to the consumers that rely on it?

    lattice contracts impact PROVIDER --candidate REF|PATH [--workspace PATH] [--json]

**Advisory and read-only.** Nothing waits on the answer (no gate, pin, check, accept or build),
and it writes nothing outside its own temporary directory (removed before it returns): no lock,
workspace file, state directory, ledger, git object, ref or index. Its subprocesses are read-only
``git`` calls (``rev-parse``, ``ls-tree``, ``cat-file``) and at most two export children
(:func:`lattice.contracts.export.compute_export`: the candidate's, then the provider's current
one). No network, no app, no LLM, static only.

**The candidate** is a directory (a *path*, used as the provider's project) or else a git ref of
the provider's repository. A ref is materialised without a checkout: the project's subtree of the
commit is written into a temporary directory from ``ls-tree`` and ``cat-file`` (raw blobs;
symlinks kept, submodules skipped), so the working tree, index, HEAD and refs are never touched.
A directory shadows a same-named ref. The candidate is exported with the provider's manifest
``spec`` and ``exclude``, the recipe of ``export NAME``.

**The baseline is each consumer's pin.** For each link with ``to = PROVIDER`` and a ``contract``
key (one row per link, sorted by consumer and contract) the row is exactly what ``check`` would
say if the candidate shipped and nobody re-pinned: ``evaluate(imports, candidate, pinned)`` and
``changed_of(pinned, candidate, broken, imports)``. The label folds that into the words of
Studio's change impact:

* ``contradiction``: some import is BROKEN (an excluded endpoint included);
* ``regression``: no BROKEN, but a CHANGED finding with ``relied`` (an endpoint some import names);
* ``unaffected``: anything else (unrelied CHANGED, gaps, nothing).

The exit code is 0 (no contradiction; a ``regression`` alone exits 0), 1 (at least one
contradiction) or 2 (it cannot run: a manifest error, an unknown provider or candidate, a failing
export of the candidate, any row in error). ``current_label`` is the same label for the provider's
*current* export against the same pin: it tells a stale pin (the whole pin -> candidate difference
is then in the row) from a change the candidate brings.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lattice.contracts import check as ck
from lattice.contracts.export import DEFAULT_SPEC, ExportFailed, compute_export
from lattice.workspace import ERROR, WARNING, Link, Problem, Workspace

FORMAT = 1
CONTRADICTION, REGRESSION, UNAFFECTED = "contradiction", "regression", "unaffected"
LABELS = (CONTRADICTION, REGRESSION, UNAFFECTED)
STATIC_ONLY = {"status": "not_run", "reason": "static-only"}
GIT_TIMEOUT = 120


class CandidateError(Exception):
    """A candidate that cannot be resolved or loaded: ``code`` (``unknown_candidate``,
    ``candidate_failed``) and a message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---- the report ------------------------------------------------------------------------------


@dataclasses.dataclass
class Impact:
    """The answer: ``consumers`` (one row per opted-in link), ``problems``, the candidate and the
    provider's current version. ``to_dict`` is the CLI's ``--json`` (every key always present)."""

    workspace: str | None
    provider: str
    candidate: dict[str, Any]
    current: dict[str, Any] | None = None
    consumers: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    problems: list[Problem] = dataclasses.field(default_factory=list)
    elapsed_s: float = 0.0
    cannot_run: bool = False  # no usable workspace at all: exit 2 whatever `problems` says

    @property
    def counts(self) -> dict[str, int]:
        out = {CONTRADICTION: 0, REGRESSION: 0, UNAFFECTED: 0, "error": 0}
        for row in self.consumers:
            out[row["label"] or "error"] += 1
        return out

    @property
    def exit_code(self) -> int:
        if (
            self.cannot_run
            or any(p.severity == ERROR for p in self.problems)
            or any(row["error"] is not None for row in self.consumers)
        ):
            return 2
        if any(row["label"] == CONTRADICTION for row in self.consumers):
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "workspace": self.workspace,
            "provider": self.provider,
            "candidate": self.candidate,
            "current": self.current,
            "advisory": True,
            "static_only": True,
            "baseline": "pin",
            "checked": len(self.consumers),
            "consumers": self.consumers,
            "counts": self.counts,
            "problems": [p.to_dict() for p in self.problems],
            "exit_code": self.exit_code,
            "elapsed_s": round(self.elapsed_s, 3),
        }


def candidate_info(text: str, kind: str | None = None) -> dict[str, Any]:
    """The ``candidate`` record of a candidate that was not (or could not be) resolved."""
    return {"input": text, "kind": kind or _kind(text, None), "resolved": None, "spec_ref": None,
            "version": None, "endpoints": None}  # fmt: skip


def unavailable(provider: str, candidate: str, problems: Sequence[Problem] = (),
                workspace: str | None = None) -> Impact:  # fmt: skip
    """An answer with every key and no rows, for a call that cannot even read a workspace (or
    lacks an argument): exit 2."""
    return Impact(workspace, provider, candidate_info(candidate), None, [], list(problems),
                  cannot_run=True)  # fmt: skip


# ---- the candidate ---------------------------------------------------------------------------


def _kind(text: str, base: Path | None) -> str:
    """``path`` when `text` names an existing directory (relative to `base`, default: the cwd),
    else ``ref``. A directory shadows a same-named ref."""
    try:
        return "path" if text and _where(text, base).is_dir() else "ref"
    except (OSError, ValueError, RuntimeError):
        return "ref"


def _where(text: str, base: Path | None) -> Path:
    return Path(base or Path.cwd()) / Path(text).expanduser()


def _git(
    project: Path, *args: str, stdin: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    """A read-only ``git -C project --no-optional-locks ARGS``; never raises on a git failure."""
    try:
        return subprocess.run(["git", "-C", str(project), "--no-optional-locks", *args],
                              input=stdin, capture_output=True, timeout=GIT_TIMEOUT,
                              check=False)  # fmt: skip
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(args, 127, b"", str(e).encode())


def resolve_ref(project: Path, text: str, provider: str) -> str:
    """The commit SHA of the ref `text` in the repository holding `project`. Raises
    :class:`CandidateError` (``unknown_candidate``): a name that starts with ``-`` is refused
    without calling git, and so is anything that is not a commit of that repository."""
    why = ""
    if not text or "\0" in text:
        why = "an empty or malformed name"
    elif text.startswith("-"):
        why = "a name that starts with '-' is never taken as a ref"
    else:
        r = _git(project, "rev-parse", "--verify", "--quiet", f"{text}^{{commit}}")
        sha = r.stdout.decode("utf-8", "replace").strip()
        if r.returncode == 0 and len(sha) >= 40:
            return sha
        why = f"not a commit of the repository holding {project}"
    raise CandidateError("unknown_candidate",
                         f"candidate {text!r} is not a directory or a git ref: {why}")  # fmt: skip


def materialise(project: Path, sha: str, dest: Path) -> int:
    """Write the subtree of `project` at commit `sha` into the empty directory `dest`, with no
    checkout: ``rev-parse --show-prefix``, ``ls-tree -r -z`` and ``cat-file --batch`` (raw blobs,
    symlinks kept, submodules skipped). Returns the number of files. Raises
    :class:`CandidateError` (``unknown_candidate``) when git cannot read the commit."""

    def fail(what: str) -> CandidateError:
        return CandidateError("unknown_candidate", f"cannot read {sha[:12]} of the repository "
                              f"holding {project}: {what}")  # fmt: skip

    r = _git(project, "rev-parse", "--show-prefix")
    if r.returncode != 0:
        raise fail("not a git repository")
    prefix = r.stdout.decode("utf-8", "surrogateescape").strip("\n")
    args = ["ls-tree", "-r", "-z", "--full-tree", sha]
    if prefix:
        args += ["--", prefix]
    r = _git(project, *args)
    if r.returncode != 0:
        raise fail("ls-tree failed")
    cut = len(prefix.encode("utf-8", "surrogateescape"))
    entries: list[tuple[str, str, str]] = []  # (mode, object id, path relative to the project)
    for raw in r.stdout.split(b"\0"):
        if not raw:
            continue
        meta, _, path = raw.partition(b"\t")
        mode, typ, oid = meta.decode("ascii").split(" ")
        if typ != "blob":  # a submodule (commit) has no content here
            continue
        entries.append((mode, oid, os.fsdecode(path[cut:])))
    blobs: dict[str, bytes] = {}
    if entries:
        want = sorted({oid for _m, oid, _p in entries})
        r = _git(project, "cat-file", "--batch", stdin=("\n".join(want) + "\n").encode("ascii"))
        if r.returncode != 0:
            raise fail("cat-file failed")
        out, pos = r.stdout, 0
        for oid in want:
            nl = out.index(b"\n", pos)
            head = out[pos:nl].split(b" ")
            if len(head) != 3 or head[0].decode("ascii") != oid or head[1] != b"blob":
                raise fail(f"object {oid[:12]} is not a blob")
            size = int(head[2])
            blobs[oid] = out[nl + 1 : nl + 1 + size]
            pos = nl + 1 + size + 1
    root = dest.resolve()
    links: list[tuple[str, Path]] = []
    count = 0
    for mode, oid, rel in sorted(entries, key=lambda e: e[2]):
        parts = Path(rel).parts
        if not parts or Path(rel).is_absolute() or ".." in parts:
            continue  # git never stores these; never write outside `dest` either
        target = root.joinpath(*parts)
        if mode == "120000":
            links.append((blobs[oid].decode("utf-8", "surrogateescape"), target))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.resolve().is_relative_to(root):
            continue  # a parent that is a symlink out of `dest`
        target.write_bytes(blobs[oid])
        if mode == "100755":
            target.chmod(0o755)
        count += 1
    for text, target in links:  # last: a symlink never redirects a file written above
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(text, target)
            count += 1
        except OSError:
            continue
    return count


def _scrub(message: str, temp: Path, candidate: str) -> str:
    for p in {str(temp), str(temp.resolve())}:
        message = message.replace(p, f"candidate {candidate}")
    return message


# ---- the run ---------------------------------------------------------------------------------


def label_of(broken: Sequence[Mapping[str, Any]], changed: Sequence[Mapping[str, Any]]) -> str:
    """The label of a link's BROKEN and CHANGED findings (see the module docstring)."""
    if broken:
        return CONTRADICTION
    if any(f.get("relied") for f in changed):
        return REGRESSION
    return UNAFFECTED


def _live(link: Link) -> dict[str, str] | None:
    return dict(STATIC_ONLY) if link.contract == "live" else None


def _error_row(link: Link, err: ck.ContractError) -> dict[str, Any]:
    return {"consumer": link.source, "provider": link.target, "contract": link.contract,
            "label": None, "error": {"code": err.code, "message": err.message}, "imports": 0,
            "pinned": None, "version_changed": None, "spec_hash_changed": None,
            "spec_ref_changed": None, "current_label": None, "findings": [], "gaps": [],
            "counts": {"broken": 0, "changed_relied": 0, "changed_unrelied": 0, "gaps": 0},
            "live": _live(link)}  # fmt: skip


def _row(session: ck._Session, link: Link, cand: Mapping[str, Any],
         cur: Mapping[str, Any] | None) -> dict[str, Any]:  # fmt: skip
    imports = session.imports(link)
    lock_path, lock = session.lock(link.source)
    entry = (lock or {}).get("providers", {}).get(link.target)
    if entry is None:
        why = "has no lock" if lock is None else f"has no pin for {link.target}"
        raise ck.ContractError("no_pin", f"{link.source} {why} ({lock_path}): run "
                               f"`lattice contracts pin {link.source}`")  # fmt: skip
    pinned_v = ck.version_of(entry)
    c_exp, c_ver = cand["export"], cand["version"]
    broken, gaps = ck.evaluate(imports, c_exp, entry["export"])
    changed = ck.changed_of(entry["export"], c_exp, broken, imports)
    findings = sorted([*broken, *changed], key=ck._sort_key)
    current_label = None
    if cur is not None:
        c_broken, _gaps = ck.evaluate(imports, cur["export"], entry["export"])
        c_changed = ck.changed_of(entry["export"], cur["export"], c_broken, imports)
        current_label = label_of(c_broken, c_changed)
    relied = sum(1 for f in changed if f.get("relied"))
    return {
        "consumer": link.source, "provider": link.target, "contract": link.contract,
        "label": label_of(broken, changed), "error": None, "imports": len(imports),
        "pinned": pinned_v,
        "version_changed": (pinned_v["git_ref"], pinned_v["dirty"])
        != (c_ver["git_ref"], c_ver["dirty"]),
        "spec_hash_changed": pinned_v["spec_hash"] != c_ver["spec_hash"],
        "spec_ref_changed": pinned_v["spec_ref"] != cand["spec_ref"],
        "current_label": current_label, "findings": findings, "gaps": gaps,
        "counts": {"broken": len(broken), "changed_relied": relied,
                   "changed_unrelied": len(changed) - relied, "gaps": len(gaps)},
        "live": _live(link),
    }  # fmt: skip


def run_impact(ws: Workspace, provider: str, candidate: str, *, base: Path | None = None) -> Impact:
    """The impact of `candidate` (a directory, relative to `base` (default: the cwd), or a git
    ref of the provider's repository) on the consumers of the workspace project `provider`. Never
    raises on content; errors are in ``Impact.problems`` and the rows' ``error``. Order: manifest,
    provider, links, candidate, candidate export, current export, rows."""
    t0 = time.monotonic()
    base = Path(base) if base is not None else Path.cwd()
    out = Impact(ws.name, provider, candidate_info(candidate, _kind(candidate, base)))
    try:
        out.problems = list(ws.problems)
        if ws.errors:
            return out
        project = ws.project(provider)
        if project is None:
            have = ", ".join(ws.names()) or "none"
            out.problems.append(Problem("unknown_project", f"no project {provider!r} in the "
                                        f"workspace (have {have})"))  # fmt: skip
            return out
        spec_ref = project.spec or DEFAULT_SPEC
        exclude = tuple(project.exclude or ())
        out.candidate["spec_ref"] = spec_ref
        links = sorted((lk for lk in ws.links if lk.target == provider and lk.contract),
                       key=lambda lk: (lk.source, lk.contract or ""))  # fmt: skip
        if not links:
            out.problems.append(Problem("no_consumers", f"no link with a contract key points to "
                                        f"{provider} (set contract = \"static\" on a [[link]] "
                                        "to opt in)", WARNING))  # fmt: skip
            return out
        cand = _candidate_export(out, project.path, spec_ref, exclude, candidate, base)
        if cand is None:
            return out
        cur: dict[str, Any] | None = None
        try:
            cur = compute_export(project.path, spec_ref, exclude)
            out.current = ck.version_of(cur)
        except ExportFailed as e:
            out.problems.append(Problem("current_export_failed", f"{provider}: {e}", WARNING))
        for key in cand["exclude_unmatched"]:
            out.problems.append(Problem("exclude_unmatched", f"{provider}: exclude names {key}, "
                                        "which its export does not hold (no 2xx step in its "
                                        "spec)", WARNING))  # fmt: skip
        session = ck._Session(ws)
        for link in links:
            try:
                out.consumers.append(_row(session, link, cand, cur))
            except ck.ContractError as e:
                out.consumers.append(_error_row(link, e))
        return out
    finally:
        out.elapsed_s = time.monotonic() - t0


def _candidate_export(out: Impact, project: Path, spec_ref: str, exclude: tuple[str, ...],
                      text: str, base: Path) -> dict[str, Any] | None:  # fmt: skip
    """The candidate's export (filling ``out.candidate``), or None after recording the problem.
    The temporary directory of a ref is removed before this returns."""
    info = out.candidate
    temp: Path | None = None
    try:
        if info["kind"] == "path":
            where = _where(text, base).resolve()
            info["resolved"] = str(where)
            got = compute_export(where, spec_ref, exclude)
            version = {"spec_hash": got["version"]["spec_hash"],
                       "git_ref": got["version"]["git_ref"],
                       "dirty": got["version"]["dirty"]}  # fmt: skip
        else:
            sha = resolve_ref(project, text, out.provider)
            info["resolved"] = sha
            temp = Path(tempfile.mkdtemp(prefix="lattice-impact-"))
            materialise(project, sha, temp)
            try:
                got = compute_export(temp, spec_ref, exclude)
            except ExportFailed as e:
                raise ExportFailed(_scrub(str(e), temp, text)) from None
            version = {"spec_hash": got["version"]["spec_hash"], "git_ref": sha, "dirty": False}
    except CandidateError as e:
        out.problems.append(Problem(e.code, e.message))
        return None
    except ExportFailed as e:
        out.problems.append(Problem("candidate_failed", str(e)))
        return None
    finally:
        if temp is not None:
            shutil.rmtree(temp, ignore_errors=True)
    info["version"] = version
    info["endpoints"] = len(got["export"]["endpoints"])
    return {"export": got["export"], "version": version, "spec_ref": spec_ref,
            "exclude_unmatched": got.get("exclude_unmatched", [])}  # fmt: skip
