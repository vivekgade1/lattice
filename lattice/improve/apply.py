"""``lattice improve apply ID``: put one open config proposal into a branch of its own, for the
owner to review and merge (spike 3, the apply step; no model, no network).

Only the five literal ``[rebuild]`` settings of the draft templates are applied. The command
calls :func:`lattice.improve.draft.draft` on exactly what ``lattice improve draft ID`` reads and
edits ``studio.toml`` with the draft's own line, as the smallest textual change, in a git
worktree of its own on a new branch off the branch the owner has checked out. It commits there,
runs the project's gates there (a smoke check, not a verdict), writes a record under the state
directory, and prints what to do next. It never merges, pushes, edits the ledger or the event
log, or touches the owner's checkout: every refusal is computed before the first write.

Exit codes: 0 applied (gates passed, skipped or none configured) or discarded; 1 applied, a gate
failed or timed out; 2 could not run (nothing is left behind but a record of an interrupted
run); 3 refused (nothing written). The rules are those of
``docs/results/2026-10-15-improve-apply-prereg.md`` (section 2).
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lattice.improve.analyze import IMPROVEMENTS_FILE, read_ledger
from lattice.improve.draft import _literal, draft
from lattice.improve.events import EVENTS_FILE, read_events

__all__ = ["apply", "apply_text", "discard", "discard_text", "run"]

APPLIES_DIR = "applies"
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
OUTPUT_TAIL = 20_000
GATE_SCAN_S = 1.0  # how often a running gate's descendants are recorded
DEFAULT_GATE_TIMEOUT = 900.0
NOT_MERGED = "NOT MERGED: lattice never merges, pushes or closes the ledger. Next, yourself:"

# every git call: no hooks, no fsmonitor program, no gc or signing, so nothing runs that the
# owner's configuration could point at and no packed ref changes behind the owner's back
_GIT = [
    "git",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "gc.auto=0",
    "-c",
    "maintenance.auto=false",
    "-c",
    "commit.gpgsign=false",
]
_HEADER = re.compile(r"^\s*\[\[?\s*[A-Za-z0-9_.\"' -]+\s*\]\]?\s*(#.*)?$")
_REBUILD_HEADER = re.compile(r"^\s*\[\s*rebuild\s*\]\s*(#.*)?$")
_DISCARDED = re.compile(r"\.discarded-\d+\.json$")


class _Refused(Exception):
    """A refusal (exit 3): nothing was written."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class _Failed(Exception):
    """Could not run (exit 2)."""


def _now() -> str:
    return datetime.now(UTC).strftime(TS_FORMAT)


# ---- git ------------------------------------------------------------------------------------


def _git(
    cwd: Path,
    *args: str,
    env: Mapping[str, str] | None = None,
    input: str | None = None,
    timeout: float = 300,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [*_GIT, "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            errors="replace",
            input=input,
            env=dict(env) if env is not None else None,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _Failed(f"git {' '.join(args[:2])}: {exc}") from exc


def _git_ok(
    cwd: Path, *args: str, env: Mapping[str, str] | None = None, input: str | None = None
) -> str:
    r = _git(cwd, *args, env=env, input=input)
    if r.returncode != 0:
        raise _Failed(f"git {' '.join(args[:3])} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def _git_bytes(cwd: Path, *args: str) -> bytes | None:
    try:
        r = subprocess.run([*_GIT, "-C", str(cwd), *args], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _toplevel(path: Path) -> Path | None:
    try:
        r = _git(path, "rev-parse", "--show-toplevel", timeout=10)
    except _Failed:
        return None
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 and r.stdout.strip() else None


def _rebuild_table(cfg: Mapping[str, Any]) -> dict[str, Any]:
    t = cfg.get("rebuild")
    return t if isinstance(t, dict) else {}


def _repo_top(project: Path, cfg: Mapping[str, Any]) -> Path | None:
    return _toplevel((project / str(_rebuild_table(cfg).get("repo") or ".")).resolve())


# ---- the edit -------------------------------------------------------------------------------


@dataclass
class _Edit:
    action: str  # replaced | inserted | created_table | created_file
    text: str  # the new file text
    line_no: int  # 1-based, in the new file
    warnings: list[str] = field(default_factory=list)


def _lines(text: str) -> list[str]:
    """Lines with their endings (only ``\\n`` and ``\\r\\n`` end a line)."""
    return re.findall(r"[^\n]*\n|[^\n]+", text)


def _body(line: str) -> str:
    return line.rstrip("\n").removesuffix("\r")


def _ending(line: str) -> str:
    return line[len(_body(line)) :]


def _split_value(rest: str) -> tuple[str, str] | None:
    """(the value with the whitespace after it, the trailing ``# comment`` or "") of the text
    after ``=``, when the value is complete on this line; None otherwise."""
    for i in [*(k for k, ch in enumerate(rest) if ch == "#"), len(rest)]:
        value = rest[:i]
        if not value.strip():
            continue
        try:
            tomllib.loads("v = " + value)
        except tomllib.TOMLDecodeError:
            continue
        return value, rest[i:]
    return None


def _holds_run(cur: Any, items: Sequence[Any]) -> bool:
    n = len(items)
    return isinstance(cur, list) and any(cur[i : i + n] == list(items) for i in range(len(cur)))


def _plan_edit(
    head_text: str | None,
    orig: dict[str, Any],
    key: str,
    line: str,
    toml: str,
    op: str,
    literal: Any,
    new_value: Any,
) -> _Edit:
    """The new text of ``studio.toml`` for the draft's `line` (``key = value``), from HEAD's
    bytes alone (`head_text` None: no such file)."""
    rb = _rebuild_table(orig)
    cur = rb.get(key)
    if key in rb and (cur == literal if op == "=" else _holds_run(cur, literal)):
        raise _Refused("already_set", f"[rebuild] {key} is already {json.dumps(cur)}")
    warnings = [
        f"{key} is also set in [{t}]; the rebuild loop reads [rebuild] only"
        for t, v in orig.items()
        if t != "rebuild" and isinstance(v, dict) and key in v
    ]
    if head_text is None:
        return _Edit("created_file", toml + "\n", 2, warnings)
    lines = _lines(head_text)
    eol = _ending(lines[0]) or "\n" if lines else "\n"
    heads = [i for i, ln in enumerate(lines) if _REBUILD_HEADER.match(_body(ln))]
    if len(heads) != 1 and "rebuild" in orig:
        raise _Refused(
            "unsupported_layout",
            f"[rebuild] is defined in a layout this edit does not handle ({len(heads)} headers)",
        )
    if not heads:
        text = head_text
        if text and not text.endswith("\n"):
            text += eol
        text += f"{eol}[rebuild]{eol}{line}{eol}"
        return _Edit("created_table", text, len(_lines(head_text)) + 3, warnings)
    h = heads[0]
    esc = re.escape(key)
    key_re = re.compile(rf"^(\s*)(?:{esc}|\"{esc}\"|'{esc}')\s*=")
    for k in range(h + 1, len(lines)):
        body = _body(lines[k])
        if _HEADER.match(body):
            break
        m = key_re.match(body)
        if m is None:
            continue
        parts = _split_value(body[m.end() :])
        if parts is None:
            raise _Refused(
                "unsupported_layout", f"[rebuild] {key} has a value that is not on one line"
            )
        value, comment = parts
        new_lines = list(lines)
        new_lines[k] = (
            m.group(1) + line + value[len(value.rstrip()) :] + comment + _ending(lines[k])
        )
        return _Edit("replaced", "".join(new_lines), k + 1, warnings)
    head_end = _ending(lines[h])
    new_lines = list(lines)
    if head_end:
        new_lines.insert(h + 1, line + head_end)
    else:  # the header is the last line and has no ending: give it one, the new line has none
        new_lines[h] = lines[h] + eol
        new_lines.append(line)
    return _Edit("inserted", "".join(new_lines), h + 2, warnings)


def _check_edit(
    old: str | None, edit: _Edit, orig: dict[str, Any], key: str, new_value: Any
) -> None:
    """The edit parses, differs from the original by the one key, and by 1 line (3 for a new
    table, 2 for a new file): else ``edit_check_failed``."""
    try:
        got = tomllib.loads(edit.text)
    except tomllib.TOMLDecodeError as exc:
        raise _Refused(
            "edit_check_failed", f"the edited studio.toml does not parse: {exc}"
        ) from exc
    want = json.loads(json.dumps(orig))
    want.setdefault("rebuild", {})[key] = new_value
    if got != want:
        raise _Refused(
            "edit_check_failed", f"the edited studio.toml is not the original plus [rebuild] {key}"
        )
    a = [_body(x) for x in _lines(old or "")]
    b = [_body(x) for x in _lines(edit.text)]
    ops = [
        (t, i2 - i1, j2 - j1)
        for t, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
        if t != "equal"
    ]
    ok = {
        "replaced": [("replace", 1, 1)],
        "inserted": [("insert", 0, 1)],
        "created_table": [("insert", 0, 3)],
        "created_file": [("insert", 0, 2)],
    }[edit.action]
    if ops != ok:
        raise _Refused("edit_check_failed", f"the edit changes more than one line: {ops}")


# ---- gates ----------------------------------------------------------------------------------


class _Interrupt:
    """SIGINT and SIGTERM set a flag while the gates run (main thread only)."""

    def __init__(self) -> None:
        self.hit = False
        self._old: dict[int, Any] = {}

    def __enter__(self) -> _Interrupt:
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                self._old[sig] = signal.signal(sig, self._handler)
        return self

    def __exit__(self, *exc: object) -> None:
        for sig, old in self._old.items():
            signal.signal(sig, old)

    def _handler(self, signum: int, frame: Any) -> None:
        self.hit = True


def _run_gate(
    cmd: str,
    worktree: Path,
    env: Mapping[str, str],
    timeout: float,
    grace: float,
    interrupt: _Interrupt,
) -> dict[str, Any]:
    """One gate command in a session of its own: a timeout or an interrupt terminates its whole
    process tree and removes the containers it labelled (as the rebuild loop does), and nothing
    is logged anywhere."""
    from lattice.studio.state import (
        OWNER_ENV,
        descendants,
        ps_table,
        remove_owned_containers,
        terminate_tree,
    )

    token = f"apply-{os.getpid()}-{os.urandom(6).hex()}"
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        ["/bin/sh", "-c", cmd],
        cwd=str(worktree),
        env={**env, OWNER_ENV: token},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    chunks: list[str] = []
    drainer = threading.Thread(target=lambda: chunks.extend(proc.stdout or ()), daemon=True)
    drainer.start()
    children: dict[int, dict[str, Any]] = {}

    def scan() -> None:
        table = ps_table()
        for pid, c in list(children.items()):
            live = table.get(pid)
            if live is None or live[2] != c.get("started"):
                del children[pid]
        for c in descendants(proc.pid, table):
            children.setdefault(c["pid"], c)

    deadline = time.monotonic() + timeout
    next_scan = time.monotonic() + 0.3
    why: str | None = None
    rc: int | None = None
    try:
        while True:
            try:
                rc = proc.wait(0.2)
                break
            except subprocess.TimeoutExpired:
                pass
            now = time.monotonic()
            if interrupt.hit:
                why = "interrupted"
            elif now >= deadline:
                why = "timeout"
            if why:
                scan()
                terminate_tree(proc, list(children.values()), grace=grace)
                rc = proc.poll()
                break
            if now >= next_scan:
                scan()
                next_scan = now + GATE_SCAN_S
        if why is None and children:
            terminate_tree(None, list(children.values()), grace=grace)
    finally:
        drainer.join(10)
        output = "".join(chunks)
        if why == "timeout":
            output += f"\n[lattice improve apply] the gate timed out after {timeout:g}s\n"
            rc = 124
        elif rc is None:
            rc = proc.poll() if proc.poll() is not None else -signal.SIGKILL
        if why is not None or rc not in (0, 1):
            remove_owned_containers(token)
    return {
        "command": cmd,
        "exit_code": rc,
        "passed": rc == 0,
        "timed_out": why == "timeout",
        "seconds": round(time.perf_counter() - t0, 2),
        "output_tail": output[-OUTPUT_TAIL:],
    }


def _run_gates(
    cmds: Sequence[str],
    worktree: Path,
    top: Path,
    project_rel: str,
    cfg: Mapping[str, Any],
    rebuild_cfg: Any,
) -> tuple[str, list[dict[str, Any]]]:
    """(status, results): all the gates, in order, from the worktree root."""
    from lattice.studio.rebuild import gate_env

    app = cfg.get("app") if isinstance(cfg.get("app"), dict) else {}
    env = gate_env(
        os.environ,
        repo=top,
        worktree=worktree,
        project_rel=project_rel,
        sys_path=[str(p) for p in app.get("sys_path") or []],
    )
    results: list[dict[str, Any]] = []
    with _Interrupt() as interrupt:
        for cmd in cmds:
            if interrupt.hit:
                break
            results.append(
                _run_gate(
                    cmd, worktree, env, rebuild_cfg.gate_timeout, rebuild_cfg.stop_grace, interrupt
                )
            )
    if interrupt.hit:
        return "interrupted", results
    failed = any(not r["passed"] for r in results)
    return ("failed" if failed else "passed"), results


def _untracked(worktree: Path) -> list[str]:
    r = _git(
        worktree, "--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )
    if r.returncode != 0:
        return []
    return sorted(e[3:] for e in r.stdout.split("\0") if e.startswith("?? "))


# ---- the record -----------------------------------------------------------------------------


def _write_record(path: Path, rec: Mapping[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise _Failed(f"cannot write {path}: {exc}") from exc


def _live_records(applies: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted(applies.glob("*.json")) if applies.is_dir() else []:
        if _DISCARDED.search(p.name):
            continue
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _message(
    ident: str, p: Mapping[str, Any], as_of: str, line: str, current: str, expected: str | None
) -> str:
    predicted = expected or "no prediction recorded (the draft has no Expected section)"
    return (
        f"improve: [rebuild] {line}\n\n"
        f"Proposal {ident} ({p['kind']}), signal {p['signal_key']}.\n"
        f"Draft as of {as_of}; {current}.\n"
        f"Predicted: {predicted}\n"
        "Applied by `lattice improve apply`; not merged. The owner reviews and merges.\n\n"
        f"Lattice-Improve: {ident}\n"
    )


def _next_steps(
    ident: str,
    key: str,
    repo: Path,
    branch: str,
    base_sha: str,
    commit: str,
    worktree: Path,
    project: Path,
    state_flag: str | None,
) -> list[dict[str, str]]:
    q = shlex.quote
    where = f"--project {q(str(project))}" + (f" --state {q(state_flag)}" if state_flag else "")
    r = q(str(repo))
    return [
        {"step": "review", "command": f"git -C {r} diff {base_sha} {q(branch)}"},
        {"step": "merge", "command": f"git -C {r} merge {q(branch)}"},
        {
            "step": "close",
            "command": f"lattice improve ledger {where} --signal {q(key)} --status closed "
            f"--id {ident} --commit {commit}",
        },
        {
            "step": "verify",
            "command": f"lattice improve verify {ident} {where} "
            "--at <YYYY-MM-DDTHH:MM:SSZ the merge landed>",
        },
        {
            "step": "tidy",
            "command": f"git -C {r} worktree remove {q(str(worktree))} && "
            f"git -C {r} branch -d {q(branch)}",
        },
        {"step": "drop", "command": f"lattice improve apply --discard {ident} {where}"},
    ]


# ---- apply ----------------------------------------------------------------------------------


def _read_inputs(
    project: Path, state: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    for p in (state / EVENTS_FILE, state / IMPROVEMENTS_FILE):
        if p.exists() and not p.is_file():
            raise _Failed(f"cannot read {p}")
        try:
            if p.exists():
                p.open("rb").close()
        except OSError as exc:
            raise _Failed(f"cannot read {p}: {exc}") from exc
    events = read_events(state / EVENTS_FILE).events
    ledger, _ = read_ledger(state / IMPROVEMENTS_FILE)
    cfg: dict[str, Any] = {}
    toml = project / "studio.toml"
    if toml.is_file():
        try:
            cfg = tomllib.loads(toml.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise _Failed(f"cannot read {toml}: {exc}") from exc
    return events, ledger, cfg  # type: ignore[return-value]


def _knob_pending(state: Path, key: str, head_sha: str | None, top: Path | None) -> None:
    """Another live record edits the same key and its commit is not in the base tip."""
    if head_sha is None or top is None:
        return
    for rec in _live_records(state / APPLIES_DIR):
        edit = rec.get("edit") if isinstance(rec.get("edit"), dict) else {}
        commit = (
            (rec.get("commit") or {}).get("sha") if isinstance(rec.get("commit"), dict) else None
        )
        if edit.get("key") != key or not commit:
            continue
        r = _git(top, "merge-base", "--is-ancestor", str(commit), head_sha)
        if r.returncode == 1:
            other = (rec.get("proposal") or {}).get("id", "?")
            raise _Refused(
                "knob_pending",
                f"{other} (branch {rec.get('branch')}, commit {str(commit)[:7]}) already edits "
                f"[rebuild] {key} and is not merged into the base",
            )


def _apply(
    project: Path,
    state: Path,
    create_state: Callable[[], Path],
    state_flag: str | None,
    ident: str,
    branch_arg: str | None,
    no_gates: bool,
) -> tuple[int, dict[str, Any]]:
    from lattice.studio.rebuild import RebuildConfig

    events, ledger, cfg = _read_inputs(project, state)
    # (1) what `lattice improve draft ID` reads and refuses
    code, doc = draft(events, ledger, ident, cfg)
    if doc.get("refused"):
        raise _Refused(doc["reason"], doc["detail"])
    # (2) only a literal [rebuild] setting is applied
    if doc["template"] != "config":
        raise _Refused(
            "not_config",
            f"{ident} drafts as a {doc['template']}, not a literal [rebuild] setting: the owner "
            "decides it",
        )
    p = doc["proposal"]
    key = p["signal_key"]
    name, op, literal = _literal(key, p)  # type: ignore[misc]
    rebuild = _rebuild_table(cfg)
    cur = rebuild.get(name)
    want = [*cur, *literal] if op == "+=" and isinstance(cur, list) else literal
    toml_text = doc["toml"]
    tl = toml_text.split("\n")
    try:
        parsed = tomllib.loads(tl[1]) if len(tl) == 2 else None
    except tomllib.TOMLDecodeError:
        parsed = None
    if tl[0] != "[rebuild]" or parsed != {name: want}:
        raise _Failed(
            f"draft_mismatch: the draft's line {toml_text!r} is not [rebuild] {name} = "
            f"{json.dumps(want)}"
        )
    line = tl[1]
    # (3) one apply per proposal
    if (state / APPLIES_DIR / f"{ident}.json").exists():
        raise _Refused(
            "already_applied", f"{ident} was already applied (record in {state / APPLIES_DIR})"
        )
    # (4) another unmerged change to the same knob
    rcfg = RebuildConfig(project, rebuild)
    top = _repo_top(project, cfg)
    head_probe = _git(top, "rev-parse", "--verify", "-q", "HEAD") if top else None
    head_sha = head_probe.stdout.strip() if head_probe and head_probe.returncode == 0 else None
    _knob_pending(state, name, head_sha, top)
    # (5) can it run at all
    if top is None:
        raise _Failed(f"{rcfg.repo_dir} is not a git repository")
    if not project.is_relative_to(top):
        raise _Failed(f"the project is not inside the repository {top}")
    if head_sha is None:
        raise _Failed("the repository has no commit yet")
    ident_cfg = {k: _git(top, "config", k).stdout.strip() for k in ("user.name", "user.email")}
    if not all(ident_cfg.values()):
        raise _Failed("no git identity configured (user.name and user.email)")
    # (6) the base: the branch checked out in the owner's main worktree
    sym = _git(top, "symbolic-ref", "-q", "--short", "HEAD")
    if sym.returncode != 0 or not sym.stdout.strip():
        raise _Refused("detached_head", "HEAD is detached: apply needs a branch to base on")
    base_branch = sym.stdout.strip()
    # (7) a clean tracked tree (untracked files are the owner's)
    status = _git_ok(top, "--no-optional-locks", "status", "--porcelain=v1", "--untracked-files=no")
    if status.strip():
        raise _Refused("dirty_checkout", "the checkout has uncommitted changes to tracked files")
    # (8) studio.toml is what HEAD holds
    project_rel = "." if project == top else project.relative_to(top).as_posix()
    rel = "studio.toml" if project_rel == "." else f"{project_rel}/studio.toml"
    head_bytes = _git_bytes(top, "cat-file", "blob", f"HEAD:{rel}")
    toml_path = project / "studio.toml"
    if toml_path.is_file() and head_bytes is None:
        raise _Refused("studio_toml_untracked", f"{rel} is not committed in HEAD")
    if head_bytes is not None and (not toml_path.is_file() or toml_path.read_bytes() != head_bytes):
        raise _Refused("studio_toml_differs", f"{rel} in the working tree is not HEAD's")
    # (9) the branch and the worktree
    branch = branch_arg or f"improve/{ident}"
    fmt = _git(top, "check-ref-format", "--branch", branch)
    if fmt.returncode != 0 or fmt.stdout.strip() != branch or branch in rcfg.protected_branches:
        why = "protected" if branch in rcfg.protected_branches else "not a valid branch name"
        raise _Refused("bad_branch", f"{branch!r} is {why}")
    if _git(top, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0:
        raise _Refused("branch_exists", f"branch {branch} already exists")
    worktree = top.parent / f"{top.name}.improve" / ident
    if os.path.lexists(worktree):
        raise _Refused("worktree_exists", f"{worktree} already exists")
    # (10) the edit, in memory
    head_text = head_bytes.decode("utf-8") if head_bytes is not None else None
    orig = tomllib.loads(head_text) if head_text is not None else {}
    edit = _plan_edit(head_text, orig, name, line, toml_text, op, literal, want)
    _check_edit(head_text, edit, orig, name, want)
    new_bytes = edit.text.encode("utf-8")

    sections = {s["heading"]: s["body"] for s in doc["sections"]}
    current = next(
        (ln for ln in sections["What to change"].split("\n") if ln.startswith("current:")), ""
    )
    expected = sections.get("Expected")
    applied_at = _now()
    message = _message(ident, p, doc["as_of"], line, current, expected)
    warnings = list(edit.warnings)
    integration = rebuild.get("integration_branch")
    integration = (
        integration.strip() if isinstance(integration, str) and integration.strip() else None
    )
    if integration and integration != base_branch:
        warnings.append(
            f"[rebuild] integration_branch is {integration}; this change is based on "
            f"{base_branch}, the branch checked out (Studio reads studio.toml there)"
        )
    gates = [] if no_gates else rcfg.gates

    # ---- the first write: the worktree, its commit and the record ----
    created_parent = not worktree.parent.exists()
    made = False
    try:
        _git_ok(top, "worktree", "add", "-q", "-b", branch, str(worktree), head_sha)
        made = True
        target = worktree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(new_bytes)
        if target.read_bytes() != new_bytes:
            raise _Failed(f"{rel} in the worktree is not the computed edit")
        _git_ok(worktree, "add", "--", rel)
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": ident_cfg["user.name"],
            "GIT_AUTHOR_EMAIL": ident_cfg["user.email"],
            "GIT_COMMITTER_NAME": ident_cfg["user.name"],
            "GIT_COMMITTER_EMAIL": ident_cfg["user.email"],
            "GIT_AUTHOR_DATE": applied_at,
            "GIT_COMMITTER_DATE": applied_at,
        }
        _git_ok(
            worktree,
            "commit",
            "-q",
            "--no-verify",
            "--cleanup=verbatim",
            "-F",
            "-",
            env=env,
            input=message,
        )
        sha = _git_ok(worktree, "rev-parse", "HEAD").strip()
        tree = _git_ok(worktree, "rev-parse", "HEAD^{tree}").strip()
        changed = _git_ok(top, "diff", "--name-only", head_sha, sha).split("\n")
        if [c for c in changed if c] != [rel]:
            raise _Failed(f"the commit changes {changed!r}, not only {rel}")
        diff = _git_ok(
            top,
            "diff",
            "--no-color",
            "--no-ext-diff",
            "-U3",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            head_sha,
            sha,
        )
        if _git_bytes(top, "cat-file", "blob", f"{sha}:{rel}") != new_bytes:
            warnings.append("the committed bytes differ from the edit")
        rec: dict[str, Any] = {
            "format": 1,
            "command": "apply",
            "proposal": {
                "id": ident,
                "kind": p["kind"],
                "signal_key": key,
                "title": p["title"],
            },
            "draft": {
                "template": doc["template"],
                "as_of": doc["as_of"],
                "toml": toml_text,
                "current": current,
                "expected": expected,
                "sha256": hashlib.sha256(doc["text"].encode("utf-8")).hexdigest(),
            },
            "edit": {
                "file": rel,
                "table": "rebuild",
                "key": name,
                "op": op,
                "old": cur,
                "new": want,
                "line": line,
                "line_no": edit.line_no,
                "action": edit.action,
                "diff": diff,
            },
            "base": {"branch": base_branch, "sha": head_sha},
            "branch": branch,
            "worktree": str(worktree),
            "commit": {"sha": sha, "tree": tree, "message": message},
            "applied_at": applied_at,
            "gates": {
                "status": "running",
                "timeout_s": rcfg.gate_timeout,
                "results": [],
                "untracked_after": [],
            },
            "integration_branch": integration,
            "next": _next_steps(
                ident, key, top, branch, head_sha, sha, worktree, project, state_flag
            ),
            "warnings": warnings,
        }
        applies = create_state() / APPLIES_DIR
        applies.mkdir(parents=True, exist_ok=True)
        record_path = applies / f"{ident}.json"
        _write_record(record_path, rec)
    except BaseException:
        if made:
            _undo(top, worktree, branch, created_parent)
        raise

    # ---- the gates, then the record again ----
    if no_gates:
        status_, results = "skipped", []
    elif not gates:
        status_, results = "none_configured", []
        warnings.append("no gate ran: no [rebuild] gates and no check.sh in the project")
    else:
        status_, results = _run_gates(gates, worktree, top, project_rel, cfg, rcfg)
    rec["gates"] = {
        "status": status_,
        "timeout_s": rcfg.gate_timeout,
        "results": results,
        "untracked_after": _untracked(worktree),
    }
    _write_record(record_path, rec)
    rec["_record_path"] = str(record_path)
    code = {"failed": 1, "interrupted": 2}.get(status_, 0)
    return code, rec


def _undo(top: Path, worktree: Path, branch: str, created_parent: bool) -> None:
    """Remove exactly what this run created (best effort: the original error is reported)."""
    with contextlib.suppress(Exception):
        if _git(top, "worktree", "remove", "--force", str(worktree)).returncode != 0:
            shutil.rmtree(worktree, ignore_errors=True)
            _git(top, "worktree", "prune")
        _git(top, "branch", "-D", branch)
        if created_parent:
            worktree.parent.rmdir()


def apply(
    project: Path,
    state: Path,
    create_state: Callable[[], Path],
    ident: str,
    *,
    branch: str | None = None,
    no_gates: bool = False,
    state_flag: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Apply proposal `ident`: ``(exit code, document)``. 0 applied, 1 a gate failed, 2 could
    not run (the document has ``error``; or ``gates.status == "interrupted"``), 3 refused.
    `state` is the state directory (never created here; `create_state` makes it at the first
    write)."""
    try:
        return _apply(project, state, create_state, state_flag, ident, branch, no_gates)
    except _Refused as r:
        return 3, _refusal("apply", ident, r)
    except _Failed as exc:
        return 2, {"error": str(exc)}


def _refusal(command: str, ident: str, r: _Refused) -> dict[str, Any]:
    return {
        "format": 1,
        "command": command,
        "id": ident,
        "refused": True,
        "reason": r.reason,
        "detail": r.detail,
    }


# ---- discard --------------------------------------------------------------------------------


def _discard(project: Path, state: Path, ident: str) -> tuple[int, dict[str, Any]]:
    record_path = state / APPLIES_DIR / f"{ident}.json"
    if not record_path.is_file():
        raise _Refused("not_applied", f"no apply record for {ident}: not a branch apply made")
    try:
        rec = json.loads(record_path.read_text(encoding="utf-8"))
        branch, worktree = str(rec["branch"]), Path(rec["worktree"])
        commit = str(rec["commit"]["sha"])
        untracked_ok = set(rec["gates"].get("untracked_after") or [])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise _Failed(f"cannot read {record_path}: {exc}") from exc
    cfg: dict[str, Any] = {}
    with contextlib.suppress(OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        cfg = tomllib.loads((project / "studio.toml").read_text(encoding="utf-8"))
    top = _repo_top(project, cfg)
    if top is None:
        raise _Failed(f"{project} is not in a git repository")
    tip = _git(top, "rev-parse", "--verify", "-q", f"refs/heads/{branch}")
    if tip.returncode != 0 or tip.stdout.strip() != commit:
        raise _Refused(
            "branch_moved",
            f"branch {branch} is not at the applied commit {commit[:7]}"
            + (" (it no longer exists)" if tip.returncode != 0 else ""),
        )
    refs = _git_ok(top, "for-each-ref", "--contains", commit, "--format=%(refname)").split()
    others = [r for r in refs if r != f"refs/heads/{branch}"]
    if others:
        raise _Refused("merged", f"{commit[:7]} is reachable from {', '.join(others)}")
    warnings: list[str] = []
    has_worktree = worktree.is_dir()
    if has_worktree:
        r = _git(
            worktree,
            "--no-optional-locks",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        )
        if r.returncode != 0:
            raise _Failed(f"git status in {worktree}: {r.stderr.strip()}")
        dirty = [
            e
            for e in r.stdout.split("\0")
            if e and not (e.startswith("?? ") and e[3:] in untracked_ok)
        ]
        if dirty:
            raise _Refused("worktree_dirty", f"{worktree} has changes: {dirty[0][3:]}")
        _git_ok(top, "worktree", "remove", "--force", str(worktree))
    elif _git(top, "worktree", "remove", "--force", str(worktree)).returncode != 0:
        warnings.append(f"{worktree} is missing: run git worktree prune yourself")
    _git_ok(top, "branch", "-D", branch)
    n = 1
    while (record_path.with_name(f"{ident}.discarded-{n}.json")).exists():
        n += 1
    kept = record_path.with_name(f"{ident}.discarded-{n}.json")
    try:
        os.rename(record_path, kept)
    except OSError as exc:
        raise _Failed(f"cannot rename {record_path}: {exc}") from exc
    return 0, {
        "format": 1,
        "command": "discard",
        "id": ident,
        "branch": branch,
        "worktree": str(worktree),
        "commit": commit,
        "record": str(kept),
        "removed": {"worktree": has_worktree, "branch": True},
        "warnings": warnings,
    }


def discard(project: Path, state: Path, ident: str) -> tuple[int, dict[str, Any]]:
    """Remove an applied-but-unmerged proposal: its worktree, branch and (renamed) record.
    ``(exit code, document)``: 0 done, 2 could not, 3 refused."""
    try:
        return _discard(project, state, ident)
    except _Refused as r:
        return 3, _refusal("discard", ident, r)
    except _Failed as exc:
        return 2, {"error": str(exc)}


# ---- output ---------------------------------------------------------------------------------


def apply_text(doc: Mapping[str, Any]) -> str:
    e, g, c = doc["edit"], doc["gates"], doc["commit"]
    lines = [
        f"Lattice improve apply: {doc['proposal']['id']} ({doc['proposal']['signal_key']})",
        f"Edit: {e['file']}, line {e['line_no']} ({e['action']}): {e['line']}",
        f"  {doc['draft']['current']}",
        f"Branch: {doc['branch']} (base {doc['base']['branch']} {doc['base']['sha'][:7]} -> "
        f"commit {c['sha'][:7]})",
        f"Worktree: {doc['worktree']}",
        f"Gates: {g['status']}",
    ]
    for r in g["results"]:
        lines.append(f"  exit {r['exit_code']} in {r['seconds']:.2f} s: {r['command']}")
    if g["untracked_after"]:
        lines.append(f"  untracked after the gates: {', '.join(g['untracked_after'])}")
    lines.append(f"Record: {doc.get('_record_path', '-')}")
    lines.append(NOT_MERGED)
    for i, s in enumerate(doc["next"], 1):
        lines.append(f"  {i}. {s['step']}: {s['command']}")
    lines += [f"warning: {w}" for w in doc["warnings"]]
    return "\n".join(lines) + "\n"


def discard_text(doc: Mapping[str, Any]) -> str:
    lines = [
        f"Lattice improve apply --discard: {doc['id']}",
        f"Removed: worktree {doc['worktree']}"
        + ("" if doc["removed"]["worktree"] else " (missing)"),
        f"Removed: branch {doc['branch']} (was {doc['commit'][:7]})",
        f"Record: {doc['record']}",
    ]
    lines += [f"warning: {w}" for w in doc["warnings"]]
    return "\n".join(lines) + "\n"


def run(
    project: Path,
    state: Path,
    create_state: Callable[[], Path],
    *,
    ident: str | None,
    discard_id: str | None,
    branch: str | None,
    no_gates: bool,
    as_json: bool,
    state_flag: str | None,
) -> int:
    """The command: prints and answers the exit code (see the module docstring)."""

    def err(msg: str) -> int:
        print(f"lattice improve: {msg}", file=sys.stderr)
        return 2

    if discard_id is not None:
        if ident is not None or branch is not None or no_gates:
            return err("--discard takes only its ID (not another ID, --branch or --no-gates)")
        code, doc = discard(project, state, discard_id)
        text = discard_text
    else:
        if ident is None:
            return err("apply needs a proposal ID (or --discard ID)")
        code, doc = apply(
            project,
            state,
            create_state,
            ident,
            branch=branch,
            no_gates=no_gates,
            state_flag=state_flag,
        )
        text = apply_text
    if doc.get("refused"):
        print(f"lattice improve: refused: {doc['detail']}", file=sys.stderr)
        if as_json:
            print(json.dumps(doc, indent=1))
        return code
    if "error" in doc:
        return err(str(doc["error"]))
    shown = {k: v for k, v in doc.items() if k != "_record_path"}
    print(json.dumps(shown, indent=1) if as_json else text(doc), end="\n" if as_json else "")
    if code == 2:
        print(
            "lattice improve: interrupted; the branch, worktree and record are kept",
            file=sys.stderr,
        )
    return code
