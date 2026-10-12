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
  when the gate wrote none), ``counts``, ``selected`` (scenarios selected, or null), ``reused``
  (a recorded per-tip result was used: no gate ran), ``sha`` (integration runs), ``x_repeats``.
* ``human`` -- ``what`` (``approve``, ``run``, ``resume``, ``retry``, ``land``, ``stop``,
  ``forget``, ``revise``, ``change_accept``, ``change_reject``, ``spec_sync``), ``actor``
  (``"owner"``), and the object (``plan``, ``change``, ``branch``).
* ``finding_class`` -- ``signal_key`` (``<layer>:<class>``), ``count``; ``snapshot: true`` when
  ``count`` is a level (how many exist now, e.g. unchecked rules) rather than new occurrences.
* ``change`` -- ``change``, ``decision`` (``accepted``/``rejected``), ``source``.

**Writes never block a build.** :meth:`EventLog.append` opens the file in append mode, writes one
line, flushes and fsyncs; any error becomes a warning (the ``log`` callback) and the event is
dropped. Appends from Studio's threads are serialised by a lock.

**Reads are tolerant.** :func:`read_events` skips a line that does not parse (a torn last line
after a crash is the expected case) with a warning, keeps unknown fields and unknown kinds.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "CAUSES",
    "EVENTS_FILE",
    "HUMAN_WHATS",
    "KINDS",
    "SCHEMA",
    "EventLog",
    "ReadResult",
    "event_id",
    "read_events",
]

SCHEMA = 1
EVENTS_FILE = "events.jsonl"
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

    def append(self, kind: str, **fields: Any) -> dict[str, Any] | None:
        """Append one event; never raises. Answers the event written, or None."""
        if self.path is None:
            return None
        ev: dict[str, Any] = {
            "v": SCHEMA,
            "ts": fields.pop("ts", None) or _now(),
            "project": self.project,
            "kind": kind,
            "id": fields.pop("id", None) or event_id(),
        }
        ev.update({k: v for k, v in fields.items() if v is not None})
        try:
            line = json.dumps(ev, sort_keys=False, default=str, separators=(",", ":")) + "\n"
            with self.lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
                try:
                    os.write(fd, line.encode("utf-8"))
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except (OSError, TypeError, ValueError) as exc:
            msg = f"events: could not append a {kind} event to {self.path}: {exc}"
            self.warnings.append(msg)
            with contextlib.suppress(Exception):
                self.log(msg)
            return None
        return ev

    def extend(self, events: Iterable[Mapping[str, Any]], known: set[str] | None = None) -> int:
        """Append ready-made events (an import), skipping ids in `known`; how many were written."""
        n = 0
        seen = set(known or ())
        for e in events:
            e = dict(e)
            if e.get("id") in seen:
                continue
            kind = str(e.pop("kind"))
            for k in ("v", "project"):
                e.pop(k, None)
            if self.append(kind, **e) is not None:
                seen.add(str(e.get("id")))
                n += 1
        return n


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
