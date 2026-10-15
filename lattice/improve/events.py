"""The improvement event log (spike 3): one append-only JSONL file per project.

**Where.** ``<state dir>/events.jsonl``, next to the durable state of
:mod:`lattice.studio.state` (``.lattice/state/<project>/`` by default; git-ignored there).
Local only; nothing is exported.

**Format.** One JSON object per line, schema ``v: 1``::

    {"v": 1, "ts": "2026-10-12T20:00:00Z", "project": "streak", "kind": "gate_run",
     "id": "e-3f2a...", ...the kind's fields...}

Kinds and their fields (any other field is kept; a reader ignores what it does not know):

* ``plan_started`` -- ``plan``, ``run``, ``resume`` (bool), ``target_branch``.
* ``plan_ended`` -- ``plan``, ``status``, ``cause`` (when not passed), ``wall_s``, ``totals``.
* ``node_ended`` -- ``plan``, ``node``, ``build``, ``status``, ``cause`` (not passed: one of
  :data:`CAUSES`), ``rounds``, ``wall_s`` (the node's own wall), ``usage`` (``tokens_in``,
  ``tokens_out``, ``cache_read_input_tokens``, ``cache_creation_input_tokens``,
  ``cost_usd_list_estimate``: the list-price estimate).
* ``gate_run`` -- ``gate`` (``baseline``, ``node``, ``level``, ``final``, ``build``,
  ``build_baseline``), ``mode`` (``full``/``regression``/``scoped``), ``command``, ``exit_code``,
  ``seconds``, ``layer_seconds`` (``{A, B, C, D, X}`` from the check's JSON ``timings``; absent
  when the gate wrote none), ``setup`` (the numeric entries of the report's ``timings.setup``,
  keys verbatim and in report order; a ``_s`` suffix is seconds, ``db_clones`` is a count;
  absent when no report was written during the gate or it has none; recorded, not ranked),
  ``counts``, ``selected`` (scenarios selected, or null), ``reused`` (a recorded per-tip result
  was used: no gate ran), ``sha`` (integration runs), ``x_repeats``.
* ``human`` -- ``what`` (``approve``, ``run``, ``resume``, ``retry``, ``land``, ``stop``,
  ``forget``, ``revise``, ``change_accept``, ``change_reject``, ``spec_sync``), ``actor``
  (``"owner"``), and the object (``plan``, ``change``, ``branch``).
* ``finding_class`` -- ``signal_key`` (``<layer>:<class>``), ``count``; ``snapshot: true`` when
  ``count`` is a level (how many exist now, e.g. unchecked rules) rather than new occurrences.
* ``change`` -- ``change``, ``decision`` (``accepted``/``rejected``), ``source``.

**Writes never block a build.** :meth:`EventLog.append` opens the file, takes an advisory
``flock`` on it (below), writes the event as ONE ``O_APPEND`` write, fsyncs and closes; any
error becomes a warning (the ``log`` callback) and the event is dropped. A lock that cannot be
had within :data:`LOCK_TIMEOUT_S` (2.0 s) degrades: the event is written WITHOUT the lock and a
warning says so; a filesystem without ``flock`` degrades at once (one warning per log).

**Concurrency (one host, a local filesystem with working ``flock(2)``).** Writers of this
version serialise on an advisory ``flock`` of the log file itself (per open file description, so
two threads or two :class:`EventLog` objects of one process exclude each other; dropped when its
holder dies, so a SIGKILL leaves nothing stale; the descriptor is not inheritable, so a gate
child never holds it). Under the lock a log whose last byte is not a newline (a torn tail left
by a crash) first gets one newline: the fragment becomes its own bad line and the new event
survives. An import (:meth:`EventLog.extend`) re-reads the ids of the file UNDER the lock, so
concurrent imports of one plan write it once. A line is at most :data:`MAX_LINE_BYTES` (65536
with its newline; larger is dropped with a warning). NOT covered: NFS, SMB, FUSE, synced
folders, several hosts (the lock is advisory and the client computes ``O_APPEND`` offsets),
power loss (macOS ``fsync`` does not flush the drive cache), writers of an older version (they
take no lock), a holder that is stopped (SIGSTOP: every append then waits 2.0 s). Order: per
writer, program order; across writers, lock order, not ``ts`` order (``ts`` is taken before the
lock, one second resolution).

**Reads are tolerant and take no lock.** :func:`read_events` skips a line that does not parse
(a torn last line after a crash is the expected case; a reader racing a write can see one) with
a warning, keeps unknown fields and unknown kinds.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:  # POSIX only; without it appends are unlocked (one warning)
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

__all__ = [
    "CAUSES",
    "EVENTS_FILE",
    "HUMAN_WHATS",
    "KINDS",
    "LOCK_TIMEOUT_S",
    "MAX_LINE_BYTES",
    "SCHEMA",
    "EventLog",
    "ReadResult",
    "append_lines",
    "event_id",
    "read_events",
]

SCHEMA = 1
EVENTS_FILE = "events.jsonl"
LOCK_TIMEOUT_S = 2.0  # how long an append waits for the file lock before writing without it
LOCK_POLL_S = 0.005
MAX_LINE_BYTES = 65536  # one event line, newline included; a larger one is dropped
_NO_FLOCK = (errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOLCK)  # the filesystem has no flock
_BUSY = (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES)  # another holder
_NOLOCK_WARNING = "events: no file lock on "
KINDS = ("plan_started", "plan_ended", "node_ended", "gate_run", "human", "finding_class", "change")
CAUSES = (
    "spec_unsatisfiable",  # the check's "spec problem" line was in the last round's findings
    "no_progress",  # the same failing set in two consecutive rounds
    "timeout",  # node_timeout / round timeout
    "token_budget",  # node_max_tokens
    "regression",  # the level gate failed on a check that passed before
    "merge_conflict",
    "usage_limit",  # the CLI's usage limit
    "stopped_by_owner",
    "repair_exhausted",  # every round (or level repair attempt) used, none of the above
    "other",
)
HUMAN_WHATS = (
    "approve",
    "run",
    "resume",
    "retry",
    "land",
    "stop",
    "forget",
    "revise",
    "change_accept",
    "change_reject",
    "spec_sync",
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def event_id(*parts: Any) -> str:
    """A deterministic id from `parts` (imports: importing twice adds nothing), or a random
    one with no parts."""
    if not parts:
        return "e-" + uuid.uuid4().hex[:16]
    h = hashlib.sha1("\x1f".join(str(p) for p in parts).encode()).hexdigest()
    return "e-" + h[:16]


def _write_all(fd: int, data: bytes) -> None:
    """Write all of `data` (a short write is completed; a write of 0 bytes is an error)."""
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            raise OSError(errno.EIO, "write made no progress")
        view = view[n:]


def append_lines(
    path: Path | str,
    lines: Iterable[bytes | str] | Callable[[Path], Iterable[bytes | str]],
    *,
    timeout: float | None = None,
    degrade: bool = True,
    what: str = "lines",
) -> tuple[int, list[str]]:
    """Append whole lines to `path` under an advisory ``flock``: (lines written, warnings).
    Never raises. `lines` is an iterable of lines (a newline is added when missing), or a
    callable that gets the path UNDER the lock and returns them (an import re-reads the file
    there).

    The file is opened ``O_APPEND`` (created ``0o644``), the lock polled every 5 ms for
    `timeout` seconds (default :data:`LOCK_TIMEOUT_S`); a tail that does not end in a newline
    is healed (one newline first); each line is one ``write`` (a short write is completed),
    then one ``fsync``. With the lock out of reach: `degrade` True (the default) writes
    anyway with a warning, False writes nothing and warns. A filesystem without ``flock``
    always writes, with the ``events: no file lock`` warning. Any other OSError: a warning,
    and the lines written so far are counted."""
    path = Path(path)
    wait = LOCK_TIMEOUT_S if timeout is None else timeout
    warnings: list[str] = []
    done = 0
    batch: list[bytes] = []
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
    except OSError as exc:
        return 0, [f"events: could not append {what} to {path}: {exc}"]
    try:
        if fcntl is None:
            warnings.append(f"{_NOLOCK_WARNING}{path} (no fcntl); appending without it")
        else:
            deadline = time.monotonic() + wait
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno in _NO_FLOCK:
                        reason = errno.errorcode.get(exc.errno or 0, str(exc))
                        warnings.append(f"{_NOLOCK_WARNING}{path} ({reason}); appending without it")
                        break
                    if exc.errno not in _BUSY:
                        raise
                    if time.monotonic() >= deadline:
                        tail = "appended without it" if degrade else "nothing written"
                        warnings.append(
                            f"events: waited {wait:.1f} s for the lock on {path}; {tail}"
                        )
                        if not degrade:
                            return 0, warnings
                        break
                    time.sleep(LOCK_POLL_S)
        for b in lines(path) if callable(lines) else lines:
            raw = b if isinstance(b, bytes) else b.encode("utf-8")
            batch.append(raw if raw.endswith(b"\n") else raw + b"\n")
        if not batch:
            return 0, warnings
        size = os.fstat(fd).st_size
        torn = size > 0 and os.pread(fd, 1, size - 1) != b"\n"
        if torn:
            batch[0] = b"\n" + batch[0]
        for b in batch:
            _write_all(fd, b)
            done += 1
        os.fsync(fd)
    except (OSError, TypeError, ValueError) as exc:
        warnings.append(f"events: could not append {what} to {path}: {exc}")
        if batch and done == len(batch):
            done = 0  # every line written but the sync failed: dropped, as before
    finally:
        os.close(fd)  # releases the lock
    return done, warnings


class EventLog:
    """The append side. ``path`` None: a log that records nothing (no state directory)."""

    def __init__(
        self,
        path: Path | str | None,
        project: str,
        *,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.project = project
        self.log = log or (lambda _m: None)
        self.lock = threading.Lock()
        self.warnings: list[str] = []
        self._nolock_warned = False

    @classmethod
    def in_dir(
        cls, state_dir: Path | str | None, project: str, log: Callable[[str], None] | None = None
    ) -> EventLog:
        return cls(
            Path(state_dir) / EVENTS_FILE if state_dir is not None else None, project, log=log
        )

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def _warn(self, msg: str) -> None:
        """Record a warning; the ``events: no file lock`` one once per log."""
        if msg.startswith(_NOLOCK_WARNING):
            if self._nolock_warned:
                return
            self._nolock_warned = True
        self.warnings.append(msg)
        with contextlib.suppress(Exception):
            self.log(msg)

    def _line(self, kind: str, fields: dict[str, Any]) -> tuple[dict[str, Any], bytes | None]:
        """The event and its encoded line (None: over :data:`MAX_LINE_BYTES`, warned)."""
        ev: dict[str, Any] = {
            "v": SCHEMA,
            "ts": fields.pop("ts", None) or _now(),
            "project": self.project,
            "kind": kind,
            "id": fields.pop("id", None) or event_id(),
        }
        ev.update({k: v for k, v in fields.items() if v is not None})
        line = (json.dumps(ev, sort_keys=False, default=str, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        if len(line) > MAX_LINE_BYTES:
            self._warn(
                f"events: a {kind} event of {len(line)} bytes is over {MAX_LINE_BYTES}; not written"
            )
            return ev, None
        return ev, line

    def _write(self, lines: Any, what: str, *, degrade: bool = True) -> int:
        assert self.path is not None
        with self.lock:
            n, warns = append_lines(self.path, lines, degrade=degrade, what=what)
        for w in warns:
            self._warn(w)
        return n

    def append(self, kind: str, **fields: Any) -> dict[str, Any] | None:
        """Append one event; never raises. Answers the event written, or None."""
        if self.path is None:
            return None
        try:
            ev, line = self._line(kind, fields)
        except (TypeError, ValueError) as exc:
            self._warn(f"events: could not append a {kind} event to {self.path}: {exc}")
            return None
        if line is None:
            return None
        return ev if self._write([line], f"a {kind} event") == 1 else None

    def extend(self, events: Iterable[Mapping[str, Any]], known: set[str] | None = None) -> int:
        """Append ready-made events (an import), skipping ids in `known` and ids already in
        the file (re-read under the lock, so concurrent imports write each event once); how
        many were written. The lock out of reach for 2.0 s: nothing is written, a warning."""
        if self.path is None:
            return 0
        pending = []
        for e in events:
            e = dict(e)
            kind = str(e.pop("kind"))
            for k in ("v", "project"):
                e.pop(k, None)
            pending.append((kind, e))
        if not pending:
            return 0

        def lines(path: Path) -> list[bytes]:
            seen = set(known or ()) | {str(e.get("id")) for e in read_events(path).events}
            out: list[bytes] = []
            for kind, e in pending:
                if e.get("id") in seen:
                    continue
                try:
                    ev, line = self._line(kind, dict(e))
                except (TypeError, ValueError) as exc:
                    self._warn(f"events: could not append a {kind} event to {self.path}: {exc}")
                    continue
                if line is not None:
                    out.append(line)
                    seen.add(str(ev["id"]))
            return out

        return self._write(lines, "events", degrade=False)


@dataclass
class ReadResult:
    events: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def read_events(path: Path | str) -> ReadResult:
    """Every event of `path` in file order (``line`` added: its 1-based line number). A line
    that does not parse is skipped with a warning (``torn last line`` when it is the last and
    has no newline); unknown fields and kinds are kept. A missing file is an empty log."""
    p = Path(path)
    out = ReadResult()
    try:
        data = p.read_bytes()
    except FileNotFoundError:
        return out
    except OSError as exc:
        out.warnings.append(f"{p}: {exc}")
        return out
    lines = data.split(b"\n")
    last_torn = not data.endswith(b"\n") and bool(data)
    for i, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            ev = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            ev = None
        if not isinstance(ev, dict) or "kind" not in ev:
            torn = last_torn and i == len(lines)
            out.warnings.append(
                f"{p.name} line {i}: "
                + ("torn last line (skipped)" if torn else "not an event (skipped)")
            )
            continue
        ev["line"] = i
        out.events.append(ev)
    return out
