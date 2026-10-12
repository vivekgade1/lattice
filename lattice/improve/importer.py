"""``import-plan``: an existing plan record (``GET /api/plan/{id}``, or a saved one like
``docs/results/data/streak-plans-2026-10-11/p2-final.json``) as events, with only the fields
the record has. Deterministic ids: importing a record twice adds nothing.

What a record gives (and what it cannot):

* ``gate_run`` per level run with a gate (``gate: level``; ``mode`` from the record, else
  ``regression`` when the command carries ``--only-file``, else ``full``), its seconds; per
  start baseline: the record's ``baseline`` and the earlier ones that survive only as
  timeline lines (``start baseline on B at SHA: N failing``), their seconds derived as the gap
  from the preceding ``resume N started`` / ``run N started`` line (``derived: true``). No
  ``layer_seconds`` unless the record has them (records before spike 3 never do).
* ``node_ended`` per build end in the timeline (``node X gave_up (bN, k round(s))``; wall from
  ``node X started as bN``); a node with no timeline entries gets one from its fields. A
  record's ``cause`` is used when it has one; otherwise a give-up is ``other`` (``cause_detail:
  "not recorded"``) and a node ``stopped`` in a ``stopped`` plan ``stopped_by_owner``.
* ``human``: ``approve`` (the record's ``approved`` time) and one per ``resume N started`` /
  ``land N started`` line; timeline entries with ``actor: "owner"`` are taken as they are.
* ``finding_class`` ``land:<kind>`` for a level run's ``land_error``.
* ``plan_started`` / ``plan_ended``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from lattice.improve.events import event_id

__all__ = ["plan_events"]

_BUILD_END = re.compile(r"^node (\S+) (passed|gave_up|stopped|interrupted) \((b\d+), (\d+) round")
_BUILD_START = re.compile(r"^node (\S+) (?:started as|resumed its build) (b\d+)")
_BASELINE = re.compile(r"^start baseline on (\S+) at ([0-9a-f]{7,40}): (\d+) failing")
_RUN = re.compile(r"^(run|resume|land) (\d+) started")


def _ts(v: Any) -> str | None:
    if isinstance(v, int | float) and not isinstance(v, bool) and v > 0:
        return datetime.fromtimestamp(float(v), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(v) if v else None


def _secs(a: str | None, b: str | None) -> float | None:
    try:
        ta = datetime.strptime(str(a), "%Y-%m-%dT%H:%M:%SZ")
        tb = datetime.strptime(str(b), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return round((tb - ta).total_seconds(), 2)


def _mode(gate: Mapping[str, Any], cmd: str) -> str:
    if gate.get("mode") in ("full", "regression"):
        return str(gate["mode"])
    return "regression" if "--only-file" in cmd else "full"


def _usage(u: Mapping[str, Any] | None) -> dict[str, Any]:
    u = u or {}
    return {
        k: u.get(k)
        for k in (
            "tokens_in",
            "tokens_out",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "cost_usd_list_estimate",
        )
        if u.get(k) is not None
    }


def plan_events(rec: Mapping[str, Any], source: str = "") -> list[dict[str, Any]]:
    """The events a plan record holds (each with ``kind``, ``ts``, ``id``, ``plan``,
    ``imported_from``)."""
    pid = str(rec.get("id") or "plan")
    out: list[dict[str, Any]] = []

    def add(kind: str, key: Any, ts: str | None, **f: Any) -> None:
        out.append(
            {
                "kind": kind,
                "id": event_id("import", pid, kind, key),
                "ts": ts or "",
                "plan": pid,
                "imported_from": source or None,
                **f,
            }
        )

    timeline = [e for e in rec.get("timeline") or [] if isinstance(e, Mapping)]
    if rec.get("started"):
        add(
            "plan_started",
            "started",
            _ts(rec.get("started")),
            run=rec.get("runs"),
            target_branch=rec.get("target_branch"),
        )
    if rec.get("approved"):
        add("human", "approve", _ts(rec.get("approved")), what="approve", actor="owner")
    # level gates
    for lr in rec.get("level_runs") or []:
        if not isinstance(lr, Mapping):
            continue
        gate = lr.get("gate") or {}
        for i, g in enumerate(gate.get("gates") or []):
            cmd = str(g.get("command") or "")
            add(
                "gate_run",
                ("level", lr.get("level"), lr.get("run"), i),
                _ts(lr.get("finished") or lr.get("started")),
                gate="level",
                mode=_mode(gate, cmd),
                command=cmd,
                exit_code=g.get("exit_code"),
                seconds=g.get("seconds"),
                layer_seconds=g.get("layer_seconds")
                or (gate.get("layer_seconds") if i == 0 else None),
                counts=gate.get("counts"),
                selected=gate.get("selected"),
                reused=False,
                sha=gate.get("sha"),
                level=lr.get("level"),
            )
        if isinstance(lr.get("land_error"), Mapping):
            kind = str(lr["land_error"].get("kind") or "error")
            add(
                "finding_class",
                ("land", lr.get("level"), lr.get("run")),
                _ts(lr.get("finished")),
                signal_key=f"land:{kind}",
                count=1,
            )
    # baselines: the timeline's, then the record's own (when it is not one of them)
    last_start: str | None = None
    seen: set[str] = set()
    base = rec.get("baseline") if isinstance(rec.get("baseline"), Mapping) else None
    for e in timeline:
        ev, at = str(e.get("event") or ""), _ts(e.get("at"))
        m = _RUN.match(ev)
        if m:
            last_start = at
            add(
                "human",
                ("run", m.group(2)),
                at,
                what=m.group(1),
                actor="owner",
                derived=None if e.get("actor") else True,
            )
            continue
        m = _BASELINE.match(ev)
        if m:
            sha = m.group(2)
            secs = None
            if base and str(base.get("sha") or "").startswith(sha) and base.get("seconds"):
                secs = base.get("seconds")
            reused = "not gated again" in ev or "recorded results" in ev
            derived = secs is None
            if secs is None and last_start:
                secs = _secs(last_start, at)
            add(
                "gate_run",
                ("baseline", sha, at),
                at,
                gate="baseline",
                mode="full",
                seconds=0.0 if reused else secs,
                reused=reused,
                sha=sha,
                derived=derived or None,
                failing=int(m.group(3)),
            )
            seen.add(sha)
            continue
        if e.get("actor") == "owner" and not ev.startswith("approved"):
            add(
                "human",
                ("timeline", at, ev),
                at,
                what=str(e.get("what") or ev.split()[0]),
                actor="owner",
            )
    if base and base.get("sha") and not any(str(base["sha"]).startswith(s) for s in seen):
        add(
            "gate_run",
            ("baseline", base.get("sha"), base.get("at")),
            _ts(base.get("at")),
            gate="baseline",
            mode="full",
            seconds=0.0 if base.get("reused") else base.get("seconds"),
            reused=bool(base.get("reused")),
            sha=base.get("sha"),
            layer_seconds=base.get("layer_seconds"),
            counts=base.get("counts"),
        )
    # node ends
    starts: dict[tuple[str, str], str | None] = {}
    with_builds: set[str] = set()
    ends: dict[str, list[dict[str, Any]]] = {}
    for e in timeline:
        ev, at = str(e.get("event") or ""), _ts(e.get("at"))
        m = _BUILD_START.match(ev)
        if m:
            starts[(m.group(1), m.group(2))] = at
            continue
        m = _BUILD_END.match(ev)
        if m:
            nid, status, bid, rounds = m.group(1), m.group(2), m.group(3), int(m.group(4))
            with_builds.add(nid)
            ends.setdefault(nid, []).append(
                {
                    "status": status,
                    "build": bid,
                    "rounds": rounds,
                    "at": at,
                    "started": starts.get((nid, bid)),
                    "cause": e.get("cause"),
                }
            )
    nodes = {str(n.get("id")): n for n in rec.get("nodes") or [] if isinstance(n, Mapping)}
    plan_stopped = rec.get("status") == "stopped"
    for nid, items in ends.items():
        n = nodes.get(nid, {})
        for j, it in enumerate(items):
            cause = it["cause"]
            if cause is None and it["status"] != "passed":
                cause = (
                    "stopped_by_owner" if it["status"] == "stopped" and plan_stopped else "other"
                )
            last = j == len(items) - 1
            add(
                "node_ended",
                ("node", nid, it["build"]),
                it["at"],
                node=nid,
                build=it["build"],
                status=it["status"],
                cause=cause,
                cause_detail="not recorded" if cause == "other" and not it["cause"] else None,
                rounds=it["rounds"],
                wall_s=_secs(it["started"], it["at"]),
                usage=_usage(n.get("usage")) if last else None,
                usage_scope="node total" if last else None,
            )
    for nid, n in nodes.items():
        if nid in with_builds or not n.get("builds"):
            continue
        status = str(n.get("status") or "")
        cause = n.get("cause")
        if cause is None and status not in ("passed", "pending", "blocked"):
            cause = "stopped_by_owner" if status == "stopped" and plan_stopped else "other"
        add(
            "node_ended",
            ("node", nid, ",".join(n.get("builds") or [])),
            _ts(n.get("finished_ts")) or _ts(rec.get("finished")),
            node=nid,
            build=(n.get("builds") or [None])[-1],
            status=status,
            cause=cause,
            cause_detail="not recorded" if cause == "other" and not n.get("cause") else None,
            rounds=n.get("rounds"),
            wall_s=n.get("wall_s"),
            usage=_usage(n.get("usage")),
        )
    if rec.get("status") not in (None, "ready", "approved", "planning", "running"):
        add(
            "plan_ended",
            "ended",
            _ts(rec.get("finished")),
            status=rec.get("status"),
            wall_s=(rec.get("totals") or {}).get("wall_s"),
            totals=rec.get("totals"),
        )
    return out
