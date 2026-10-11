"""Durable state for Lattice Studio (API.md v1.10): plans, build records and change sets
survive a restart or a crash, and a plan that stopped can resume from where it stopped.

**Where.** One directory per project: ``[state] dir`` of ``studio.toml`` (relative to the
project dir), else ``<repo root>/.lattice/state/<project name>/`` where the repo root is
the git top level of ``[rebuild] repo`` (default the project dir), else the project dir.
A directory inside the repository is git-ignored through ``.git/info/exclude`` (never
``.gitignore``, never the tracked tree), before the first file is written.
``[state] enabled = false`` turns the store off (everything lives in memory, as before
v1.10).

**Layout**::

    <state dir>/meta.json            {"schema": 1, "written_by": ..., "counters": {"plans": 3,
                                      "builds": 7, "changes": 2}, "results": {...}}
    <state dir>/plans/p1.json        {"schema": 1, "written_by": ..., "kind": "plans",
    <state dir>/builds/b1.json        "id": "p1", "saved_at": ..., "record": {<the API
    <state dir>/changes/c1.json       record>}, "private": {<what Studio needs to go on>}}

**Writes are atomic**: the bytes go to a temporary file in the same directory, which is
fsynced, then ``os.replace``-d over the record, then the directory is fsynced. A crash at
any point leaves the old file or the new one, never a torn one. Every manager writes
through on every status transition and after every round, level and node event.

**Reads are tolerant**: unknown fields are kept, missing ones defaulted by the managers. A
file that does not parse (or is not a record) is moved aside to ``<name>.bad-<ts>``, a
warning is recorded (``GET /api/projects`` ``state_warnings``) and loading goes on.

**Ids never repeat**: the counters live in ``meta.json`` and are also raised to the
highest id found among the files (``.bad-*`` included) when the store opens.

**Orphans**: a record keeps the process groups it spawned (``pids``: pid, pgid, start time,
command line, a ``match`` string). When a record is found ``running`` after a restart its
orphans are terminated only if the pid exists AND its start time AND its command line
match (:func:`kill_orphan`); a pid alone is never enough.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "SCHEMA",
    "StateStore",
    "atomic_write",
    "kill_orphan",
    "proc_info",
    "state_dir_for",
]

SCHEMA = 1
KINDS = {"plans": "p", "builds": "b", "changes": "c"}
STATE_DIR = ".lattice/state"
EXCLUDE_LINE = "/.lattice/state/"
_ID = re.compile(r"^([pbc])(\d+)\.json(?:\.bad-.*)?$")
_PS = re.compile(r"^\s*(\d+)\s+(\S+\s+\S+\s+\d+\s+[\d:]+\s+\d+)\s+(.*)$")
KILL_GRACE = 3.0


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def written_by() -> str:
    try:
        from importlib.metadata import version

        return f"lattice-studio {version('lattice')}"
    except Exception:  # noqa: BLE001 -- informational only
        return "lattice-studio"


# ---- atomic files ---------------------------------------------------------------------------


def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(str(d), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    """Write `data` to `path` atomically: a temporary file in the same directory, fsync,
    ``os.replace``, fsync of the directory. Interrupted at any point, `path` holds its old
    content or the new one."""
    d = path.parent
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(path))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(str(tmp))
        raise
    _fsync_dir(d)


# ---- the location ---------------------------------------------------------------------------


def _toplevel(path: Path) -> Path | None:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=10)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return Path(r.stdout.strip()).resolve()


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", name).strip("-.") or "project"


def ensure_excluded(repo: Path, line: str, comment: str) -> None:
    """`line` in the repository's ``info/exclude`` (shared by every worktree)."""
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
                           capture_output=True, text=True, timeout=10)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return
    if r.returncode != 0:
        return
    common = r.stdout.strip()
    path = Path(common) if os.path.isabs(common) else repo / common
    exclude = path / "info" / "exclude"
    try:
        text = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    except OSError:
        text = ""
    if line in text.splitlines():
        return
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as f:
        if text and not text.endswith("\n"):
            f.write("\n")
        f.write(f"# {comment}\n{line}\n")


def state_dir_for(
    project: Path,
    name: str,
    state_table: Mapping[str, Any] | None = None,
    rebuild_table: Mapping[str, Any] | None = None,
) -> Path:
    """The project's state directory (see the module docstring); made git-ignored when it
    lies inside a repository."""
    project = Path(project).resolve()
    st = dict(state_table or {})
    rb = dict(rebuild_table or {})
    if isinstance(st.get("dir"), str) and st["dir"].strip():
        d = (project / st["dir"].strip()).resolve()
    else:
        root = _toplevel((project / str(rb.get("repo") or ".")).resolve()) or project
        d = root / STATE_DIR / _slug(name)
    top = _toplevel(d.parent if not d.exists() else d) or _toplevel(project)
    if top is not None and d.is_relative_to(top):
        rel = d.relative_to(top).as_posix()
        line = EXCLUDE_LINE if rel.startswith(STATE_DIR + "/") else f"/{rel}/"
        ensure_excluded(top, line, "Lattice Studio durable state")
    return d


# ---- processes ------------------------------------------------------------------------------


def _ps(pid: int) -> tuple[int, str, str] | None:
    """(pgid, start time, command line) of a live process, or None."""
    try:
        r = subprocess.run(["ps", "-ww", "-o", "pgid=,lstart=,command=", "-p", str(int(pid))],
                           capture_output=True, text=True, timeout=10)  # fmt: skip
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    line = r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else ""
    m = _PS.match(line)
    if not m:
        return None
    return int(m.group(1)), " ".join(m.group(2).split()), m.group(3).strip()


def proc_info(pid: int, role: str, match: str) -> dict[str, Any]:
    """What a record keeps about a child it spawned (in its own process group): enough to
    recognise it after a restart (start time and command line), never the pid alone."""
    seen = _ps(pid)
    pgid, started, command = seen if seen is not None else (pid, None, None)
    return {"pid": int(pid), "pgid": pgid, "started": started, "command": command,
            "match": match, "role": role, "at": _now()}  # fmt: skip


def orphan_matches(info: Mapping[str, Any]) -> bool:
    """True when `info`'s process still runs: the pid exists, its start time is the
    recorded one, and its command line is the recorded one (or still contains the
    recorded ``match``: a ``/bin/sh -c`` that exec'd its command keeps it)."""
    try:
        pid = int(info.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 1 or not info.get("started"):
        return False
    cur = _ps(pid)
    if cur is None:
        return False
    _pgid, started, command = cur
    if started != info.get("started"):
        return False
    match = str(info.get("match") or "")
    return command == info.get("command") or (bool(match) and match in command)


def kill_orphan(info: Mapping[str, Any], log: Callable[[str], None] | None = None) -> bool:
    """Terminate the process group of a recorded child left running by a Studio that died,
    only when :func:`orphan_matches`. True when it was killed."""
    if not orphan_matches(info):
        return False
    pgid = int(info.get("pgid") or info["pid"])
    pid = int(info["pid"])
    if pgid <= 1 or pgid == os.getpgid(0):
        return False
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGTERM)
    deadline = time.monotonic() + KILL_GRACE
    while time.monotonic() < deadline and _ps(pid) is not None:
        time.sleep(0.05)
    if _ps(pid) is not None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)
    if log is not None:
        log(f"state: killed orphan process group {pgid} ({info.get('role')}: "
            f"{str(info.get('command') or '')[:120]})")  # fmt: skip
    return True


# ---- the store ------------------------------------------------------------------------------


class StateStore:
    """The JSON files of one project's durable state (see the module docstring)."""

    def __init__(self, root: Path, *, log: Callable[[str], None] | None = None) -> None:
        self.root = Path(root)
        self.log = log or (lambda _m: None)
        self.lock = threading.RLock()
        self._file_locks: dict[str, threading.RLock] = {}
        self.warnings: list[dict[str, Any]] = []
        self.written_by = written_by()
        for k in KINDS:
            (self.root / k).mkdir(parents=True, exist_ok=True)
        self._meta = self._load_meta()
        counters = self._meta.setdefault("counters", {})
        for kind in KINDS:
            counters[kind] = max(int(counters.get(kind) or 0), self._scan_max(kind))
        self._sweep_tmp()

    # -- files
    def _lock_for(self, key: str) -> threading.RLock:
        with self.lock:
            lk = self._file_locks.get(key)
            if lk is None:
                lk = self._file_locks[key] = threading.RLock()
            return lk

    def path(self, kind: str, rid: str) -> Path:
        return self.root / kind / f"{rid}.json"

    def warn(self, file: Path | str, message: str, moved_to: Path | str | None = None) -> None:
        w = {"at": _now(), "file": str(file), "message": message}
        if moved_to is not None:
            w["moved_to"] = str(moved_to)
        with self.lock:
            self.warnings.append(w)
        self.log(f"state: {message} ({file})")

    def _quarantine(self, path: Path, why: str) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        bad = path.with_name(f"{path.name}.bad-{stamp}")
        try:
            os.replace(str(path), str(bad))
        except OSError as exc:
            self.warn(path, f"{why}; could not move it aside: {exc}")
            return
        self.warn(path, f"{why}; moved aside", moved_to=bad)

    def _sweep_tmp(self) -> None:
        """Temporary files of writes a crash interrupted (the record itself is intact)."""
        for d in (self.root, *(self.root / k for k in KINDS)):
            for p in d.glob(".*.tmp-*"):
                with contextlib.suppress(OSError):
                    p.unlink()

    def _scan_max(self, kind: str) -> int:
        top = 0
        for p in (self.root / kind).iterdir():
            m = _ID.match(p.name)
            if m and m.group(1) == KINDS[kind]:
                top = max(top, int(m.group(2)))
        return top

    def _load_meta(self) -> dict[str, Any]:
        path = self.root / "meta.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            return data
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            self._quarantine(path, f"meta.json is unreadable ({type(exc).__name__}: {exc})")
            return {}

    def _write_meta(self) -> None:
        with self.lock:
            self._meta.update(schema=SCHEMA, written_by=self.written_by)
            data = json.dumps(self._meta, indent=1, sort_keys=True, default=str)
            atomic_write(self.root / "meta.json", data.encode("utf-8"))

    # -- ids
    def allocate(self, kind: str) -> int:
        """The next id number of `kind` (persisted before it is handed out)."""
        with self.lock:
            counters = self._meta.setdefault("counters", {})
            n = int(counters.get(kind) or 0) + 1
            counters[kind] = n
            self._write_meta()
            return n

    def release(self, kind: str, n: int) -> None:
        """Give back id `n` when it was the last handed out and nothing was kept under it
        (a refused build leaves no record and no id, as before v1.10)."""
        with self.lock:
            counters = self._meta.setdefault("counters", {})
            if (
                int(counters.get(kind) or 0) == n
                and not self.path(kind, f"{KINDS[kind]}{n}").exists()
            ):
                counters[kind] = n - 1
                self._write_meta()

    # -- meta values
    def meta_get(self, key: str, default: Any = None) -> Any:
        with self.lock:
            return json.loads(json.dumps(self._meta.get(key, default), default=str))

    def meta_set(self, key: str, value: Any) -> None:
        with self.lock:
            self._meta[key] = json.loads(json.dumps(value, default=str))
            self._write_meta()

    # -- records
    def save(
        self,
        kind: str,
        rid: str,
        record: Mapping[str, Any],
        private: Mapping[str, Any] | None = None,
    ) -> None:
        doc = {"schema": SCHEMA, "written_by": self.written_by, "kind": kind, "id": rid,
               "saved_at": _now(), "record": record, "private": private or {}}  # fmt: skip
        data = json.dumps(doc, indent=1, default=str, ensure_ascii=False).encode("utf-8")
        with self._lock_for(f"{kind}/{rid}"):
            atomic_write(self.path(kind, rid), data)

    def delete(self, kind: str, rid: str) -> None:
        with self._lock_for(f"{kind}/{rid}"):
            with contextlib.suppress(FileNotFoundError):
                self.path(kind, rid).unlink()
            _fsync_dir(self.root / kind)

    def load(self, kind: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Every record of `kind` as ``(record, private)``, in id order; a broken file is
        quarantined with a warning."""
        out: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
        for p in sorted((self.root / kind).glob("*.json")):
            m = _ID.match(p.name)
            if not m or m.group(1) != KINDS[kind]:
                continue
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(doc, dict) or not isinstance(doc.get("record"), dict):
                    raise ValueError("not a state record")
                rec = doc["record"]
                if rec.get("id") != p.stem:
                    raise ValueError(f"its record id {rec.get('id')!r} is not {p.stem!r}")
                schema = doc.get("schema")
                if isinstance(schema, int) and schema > SCHEMA:
                    self.warn(p, f"written with schema {schema} (this Studio reads {SCHEMA}); "
                              "unknown fields are kept as they are")  # fmt: skip
                priv = doc.get("private") if isinstance(doc.get("private"), dict) else {}
            except (OSError, ValueError, UnicodeDecodeError) as exc:
                self._quarantine(p, f"unreadable state file ({type(exc).__name__}: {exc})")
                continue
            out.append((int(m.group(2)), rec, priv))
        out.sort(key=lambda t: t[0])
        return [(r, pr) for _n, r, pr in out]
