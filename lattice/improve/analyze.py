"""The improvement ranker (spike 3): the I-R method as code, no LLM.

Method (verbatim from ``docs/results/2026-10-12-i-r-improvement-ranking.md``, which applied
spike 3's "clusters recurring signals, ranks by frequency x cost" by hand):

* **cluster** -- one candidate per ``signal_key`` (a finding class, a gate role and layer) or
  ``cause`` (a node that gave up, stopped or failed);
* **frequency** f -- the number of occurrences in the log (a ``finding_class`` event's
  ``count``; a ``snapshot`` count is a level, so the latest one is f);
* **cost** c -- what one occurrence cost: wall seconds first; list-price dollars only when no
  occurrence of the cluster recorded seconds; **never converted** into each other. It is the
  cost of the problem, not the cost to fix (the fix's cost is not an input);
* **score** = f x c. Class **A** (seconds) ranks above class **B** (dollars only) above class
  **C** (no cost recorded: ordered by f alone, which is not a score). Within a class,
  descending.

Closed items stay out: ``<state dir>/improvements.jsonl`` holds ``{id, signal, status:
open|closed, commit, ts}`` lines, appended by the owner or a merge (``python -m lattice.improve
ledger``); a cluster whose key matches a ``signal`` (fnmatch) whose latest entry is
``closed`` is not ranked (occurrences after the close are counted as ``recurred``).

At most N (default 5) **proposals** are emitted: ``{id, title, signal_key, evidence, score,
unit, score_class, frequency, cost_per, kind: change_set | module_draft | experiment,
target_paths, why, status}``. A proposal whose ``target_paths`` touch the protected set
(:data:`PROTECTED`: the checker, the process runtime, the engine registry, the spec
language) is ``status: "needs_owner"`` and never auto-drafted: the owner default of
``docs/design/next-phases-plan.md`` ("proposals target modules and docs, not the checker,
first"). The owner decides every proposal; nothing here merges anything.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.improve.events import EVENTS_FILE, read_events

__all__ = [
    "BLIND_SPOTS",
    "IMPROVEMENTS_FILE",
    "METHOD",
    "PROTECTED",
    "Cluster",
    "analyze",
    "clusters",
    "is_protected",
    "read_ledger",
    "report_text",
]

IMPROVEMENTS_FILE = "improvements.jsonl"
DEFAULT_TOP = 5
LAYERS = ("A", "B", "C", "D", "R", "X")

# never auto-drafted: the checker, the process runtime, the engine registry, the spec language
# (its DSL types and the language rules). A trailing "/" is a directory.
PROTECTED = (
    "lattice/spec/check.py",
    "lattice/spec/process.py",
    "lattice/spec/engines.py",
    "lattice/spec/engines/",
    "lattice/spec/model.py",
    "docs/lattice-language-rules.md",
)

# owner actions that are interventions (a plan needed a hand); approve/run/accept are routine
INTERVENTIONS = frozenset({"resume", "retry", "land", "stop", "forget", "revise", "spec_sync"})

METHOD = (
    "Frequency x cost, the I-R method (docs/results/2026-10-12-i-r-improvement-ranking.md): "
    "events are clustered by signal_key or cause; f = occurrences; c = the mean cost of one "
    "occurrence in wall seconds, or in list-price dollars only when the cluster recorded no "
    "seconds (never converted); score = f x c. Seconds-scored clusters (class A) rank above "
    "dollar-scored ones (B), above clusters with no recorded cost (C, ordered by f, not a "
    "score). Items closed in improvements.jsonl are left out. At most N proposals; one that "
    "touches the checker or the spec language is needs_owner and never drafted. The owner "
    "decides; nothing is merged automatically."
)

BLIND_SPOTS = [
    "human time: approvals, diagnosis and hand merges are not a cost here (human events are "
    "counted, not timed)",
    "risk and correctness: a wrong answer or a hidden bug costs no seconds, so it ranks by "
    "frequency only",
    "recurrence: f counts past occurrences, not future ones; a one-off scores like a habit",
    "cost to fix is not an input (a cheap fix and an expensive one with the same signal rank "
    "the same)",
    "causes an old record cannot state (other) and signals no event records (a workaround that "
    "hides an error)",
    "one project's log: n is small, so a ranking can overfit that project",
]


@dataclass
class Occurrence:
    ref: str
    ts: str
    count: int = 1
    seconds: float | None = None
    usd: float | None = None
    snapshot: bool = False
    x_repeats: int | None = None


@dataclass
class Cluster:
    key: str
    occ: list[Occurrence] = field(default_factory=list)
    closed: dict[str, Any] | None = None

    @property
    def frequency(self) -> int:
        snaps = [o for o in self.occ if o.snapshot]
        if snaps:
            return int(max(snaps, key=lambda o: o.ts).count)
        return sum(o.count for o in self.occ)

    def _mean(self, attr: str) -> float | None:
        have = [(o.count, getattr(o, attr)) for o in self.occ if getattr(o, attr) is not None]
        n = sum(c for c, _ in have)
        if not have or n <= 0:
            return None
        return sum(c * float(v) for c, v in have) / n

    @property
    def score_class(self) -> str:
        if self._mean("seconds") is not None:
            return "A"
        if self._mean("usd") is not None:
            return "B"
        return "C"

    @property
    def unit(self) -> str | None:
        return {"A": "s", "B": "usd", "C": None}[self.score_class]

    @property
    def cost_per(self) -> float | None:
        cls = self.score_class
        return self._mean("seconds") if cls == "A" else self._mean("usd") if cls == "B" else None

    @property
    def score(self) -> float | None:
        c = self.cost_per
        return None if c is None else self.frequency * c

    def sort_key(self) -> tuple[int, float, int, str]:
        rank = {"A": 0, "B": 1, "C": 2}[self.score_class]
        return (
            rank,
            -(self.score if self.score is not None else self.frequency),
            -self.frequency,
            self.key,
        )


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    return float(v)


def _ref(ev: Mapping[str, Any]) -> str:
    return str(ev.get("id") or f"{EVENTS_FILE}:{ev.get('line')}")


def _gate_role(ev: Mapping[str, Any], seen_shas: dict[str, str]) -> str:
    role = str(ev.get("gate") or "gate")
    mode = ev.get("mode")
    if role == "level":
        return f"level_{mode or 'full'}"
    if role == "baseline":
        sha = str(ev.get("sha") or "")
        earlier = _seen_before(sha, str(ev.get("ts") or ""), seen_shas)
        return "baseline_rerun" if earlier else "baseline"
    return role


def _seen_before(sha: str, ts: str, seen: Mapping[str, str]) -> bool:
    if len(sha) < 7:
        return False
    for s, t in seen.items():
        if (s.startswith(sha) or sha.startswith(s)) and t < ts:
            return True
    return False


def occurrences(events: Iterable[Mapping[str, Any]]) -> list[tuple[str, Occurrence]]:
    """(cluster key, occurrence) for every event that is a signal."""
    evs = list(events)
    seen_shas: dict[str, str] = {}  # sha -> earliest ts a gate actually ran on it
    for ev in evs:
        if ev.get("kind") == "gate_run" and not ev.get("reused") and ev.get("sha"):
            s, t = str(ev["sha"]), str(ev.get("ts") or "")
            if len(s) >= 7 and (s not in seen_shas or t < seen_shas[s]):
                seen_shas[s] = t
    out: list[tuple[str, Occurrence]] = []
    for ev in evs:
        kind = ev.get("kind")
        ref, ts = _ref(ev), str(ev.get("ts") or "")
        if kind == "gate_run":
            if ev.get("reused"):
                continue
            role = _gate_role(ev, seen_shas)
            xr = ev.get("x_repeats") if isinstance(ev.get("x_repeats"), int) else None
            layers = ev.get("layer_seconds")
            if isinstance(layers, Mapping) and any(_num(v) is not None for v in layers.values()):
                for layer, s in layers.items():
                    if _num(s) is not None:
                        out.append(
                            (
                                f"gate:{role}:{layer}",
                                Occurrence(ref, ts, seconds=_num(s), x_repeats=xr),
                            )
                        )
            elif _num(ev.get("seconds")) is not None:
                out.append(
                    (
                        f"gate:{role}:total",
                        Occurrence(ref, ts, seconds=_num(ev.get("seconds")), x_repeats=xr),
                    )
                )
        elif kind in ("node_ended",):
            cause = ev.get("cause")
            if not cause or ev.get("status") == "passed":
                continue
            usage = ev.get("usage") if isinstance(ev.get("usage"), Mapping) else {}
            out.append(
                (
                    f"cause:{cause}",
                    Occurrence(
                        ref,
                        ts,
                        seconds=_num(ev.get("wall_s")),
                        usd=_num(usage.get("cost_usd_list_estimate")),
                    ),
                )
            )
        elif kind == "finding_class":
            key = ev.get("signal_key")
            cnt = ev.get("count", 1)
            if not key or not isinstance(cnt, int) or isinstance(cnt, bool) or cnt < 0:
                continue
            out.append(
                (
                    str(key),
                    Occurrence(
                        ref,
                        ts,
                        count=cnt,
                        seconds=_num(ev.get("cost_s")),
                        usd=_num(ev.get("cost_usd")),
                        snapshot=bool(ev.get("snapshot")),
                    ),
                )
            )
        elif kind == "human":
            what = str(ev.get("what") or "")
            if what in INTERVENTIONS:
                out.append((f"human:{what}", Occurrence(ref, ts)))
        elif kind == "change":
            if ev.get("decision") == "rejected":
                out.append(
                    (
                        "change:rejected",
                        Occurrence(
                            ref, ts, seconds=_num(ev.get("cost_s")), usd=_num(ev.get("cost_usd"))
                        ),
                    )
                )
    return out


def read_ledger(path: Path | str) -> tuple[list[dict[str, Any]], list[str]]:
    """The improvement ledger's entries (file order) and warnings; same tolerance as events."""
    p = Path(path)
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return entries, warnings
    except OSError as exc:
        return entries, [f"{p}: {exc}"]
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            warnings.append(f"{p.name} line {i}: not JSON (skipped)")
            continue
        if (
            not isinstance(e, dict)
            or not e.get("signal")
            or e.get("status") not in ("open", "closed")
        ):
            warnings.append(f"{p.name} line {i}: needs signal and status open|closed (skipped)")
            continue
        entries.append(e)
    return entries, warnings


def _closed_for(key: str, ledger: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The ledger entry that closes `key`: the latest entry of each matching signal pattern,
    when it is ``closed``."""
    latest: dict[str, Mapping[str, Any]] = {}
    for e in ledger:
        if fnmatch.fnmatchcase(key, str(e["signal"])):
            latest[str(e["signal"])] = e
    for e in latest.values():
        if e.get("status") == "closed":
            return dict(e)
    return None


def clusters(
    events: Iterable[Mapping[str, Any]], ledger: Sequence[Mapping[str, Any]] = ()
) -> list[Cluster]:
    """Every cluster, ranked (closed ones included, marked)."""
    by: dict[str, Cluster] = {}
    for key, o in occurrences(events):
        by.setdefault(key, Cluster(key)).occ.append(o)
    for c in by.values():
        c.closed = _closed_for(c.key, ledger)
    return sorted(by.values(), key=Cluster.sort_key)


def is_protected(path: str) -> bool:
    p = str(path).lstrip("./")
    return any(p == x or (x.endswith("/") and p.startswith(x)) for x in PROTECTED)


def _fmt(v: float | None, unit: str | None) -> str:
    if v is None:
        return "-"
    return f"${v:.2f}" if unit == "usd" else f"{v:.1f} s"


def _template(c: Cluster) -> dict[str, Any]:
    """Title, kind, targets and why for a cluster (a fixed table; no model)."""
    key = c.key
    f = c.frequency
    head, _, tail = key.partition(":")
    if head == "gate":
        role, _, layer = tail.partition(":")
        where = {
            "level_regression": "level gates (regression)",
            "level_full": "full level gates",
            "node": "node gates",
            "baseline": "start baselines",
            "baseline_rerun": "start baselines on an already-gated tip",
            "final": "final full gates",
            "build": "build gates",
            "build_baseline": "build baselines",
        }.get(role, f"{role} gates")
        if role == "baseline_rerun":
            return {
                "title": f"Reuse the recorded result of a tip that was already gated "
                f"({f} baseline(s) re-ran it)",
                "kind": "change_set",
                "targets": ["lattice/studio/plan.py", "scripts/import_plan_record.py"],
                "why": "a start baseline ran the full gate on a commit whose gate result "
                "the log already holds; seeding results/<sha>.json (also when a "
                "pre-v1.10 plan record is imported) skips it",
            }
        if layer == "X" and role.startswith("level"):
            out = {
                "title": f"Fewer layer X repeats at {where}: level_gate_args = "
                '["--x-repeats", "2"]',
                "kind": "change_set",
                "targets": ["studio.toml"],
                "why": "layer X is the measured cost of these gates; the final full gate "
                "keeps the project's repeats, so coverage is traded only at level "
                "gates",
            }
            out["expected"] = _x_expectation(c, 2)
            if out["expected"] is None:
                out["kind"] = "experiment"
            return out
        if layer == "X" and role == "node":
            out = {
                "title": "Fewer layer X repeats at node gates: scoped_gate_args += "
                '["--x-repeats", "1"]',
                "kind": "change_set",
                "targets": ["studio.toml"],
                "why": "layer X dominates the node gates; the level and final gates still "
                "race every write",
            }
            out["expected"] = _x_expectation(c, 1)
            return out
        if layer == "D" and role in ("node", "level_regression", "level_full"):
            arg = "scoped_gate_args" if role == "node" else "level_gate_args"
            return {
                "title": f'Cap Await waits at {where}: {arg} += ["--await-cap", "5"]',
                "kind": "change_set",
                "targets": ["studio.toml"],
                "why": "layer D (scenarios) is the measured cost; waits on unbuilt services "
                "are the usual reason (I-06)",
            }
        if layer == "total":
            return {
                "title": f"Attribute the time of {where} to layers ({f} run(s), no "
                "per-layer seconds logged)",
                "kind": "experiment",
                "targets": ["studio.toml"],
                "why": "the log has only whole-gate seconds; with [rebuild] findings_json "
                "the gate's JSON timings are logged per layer and the next report "
                "names the layer",
            }
        return {
            "title": f"Layer {layer} at {where} costs {_fmt(c.cost_per, c.unit)} a run",
            "kind": "experiment",
            "targets": ["lattice/spec/check.py"],
            "why": f"no configuration knob exists for layer {layer}; making it faster means "
            "changing the checker",
        }
    if head == "cause":
        table = {
            "spec_unsatisfiable": (
                "Catch unsatisfiable checks before a build (lint, L012 family)",
                "change_set",
                ["lattice/spec/lint.py", "lattice/spec/preconditions.py"],
                "nodes gave up on checks no implementation can satisfy",
            ),
            "no_progress": (
                "Stop a node whose failing set does not change, and say why",
                "experiment",
                ["lattice/studio/rebuild.py"],
                "rounds repeated the same failures; the later rounds bought nothing",
            ),
            "timeout": (
                "Raise or split work hitting node_timeout",
                "change_set",
                ["studio.toml"],
                "nodes ran out of wall time",
            ),
            "token_budget": (
                "Raise node_max_tokens or split the node",
                "change_set",
                ["studio.toml"],
                "nodes ran out of output tokens",
            ),
            "regression": (
                "Find why merged nodes break each other",
                "experiment",
                ["lattice/studio/plan.py"],
                "level gates failed on a check that passed before",
            ),
            "merge_conflict": (
                "Make declared touches serialise the nodes that conflict",
                "experiment",
                ["lattice/studio/plan.py"],
                "nodes of one level could not merge",
            ),
            "usage_limit": (
                "Fewer parallel nodes near the account's usage limit",
                "change_set",
                ["studio.toml"],
                "the CLI's usage limit stopped nodes",
            ),
            "stopped_by_owner": (
                "Ask why the owner stopped these nodes",
                "experiment",
                [],
                "the owner stopped them; the log does not record why",
            ),
            "repair_exhausted": (
                "Find why nodes use every round",
                "experiment",
                ["lattice/studio/rebuild.py"],
                "nodes used every round",
            ),
            "other": (
                "Classify give-ups whose cause was not recorded",
                "experiment",
                [],
                "the records do not say why (records older than the cause field)",
            ),
        }
        title, kind, targets, why = table.get(tail, (f"Cause {tail}", "experiment", [], ""))
        return {"title": title, "kind": kind, "targets": targets, "why": why}
    if head == "lint":
        targets = ["lattice/spec/lint.py"]
        if tail == "L012":
            targets.append("lattice/spec/preconditions.py")
        return {
            "title": f"Lint {tail}: tune it (it fired {f} time(s) at a cost)",
            "kind": "change_set",
            "targets": targets,
            "why": f"the {tail} lint's findings carry the cost; precision first",
        }
    if head == "unchecked":
        if tail == "stub_observability":
            return {
                "title": "Service stubs that record what they were sent",
                "kind": "module_draft",
                "targets": ["lattice/spec/services.py"],
                "why": f"{f} unchecked rule(s) need a stub that records calls",
            }
        if tail in ("request_header", "response_header", "carried_value", "pattern_match"):
            return {
                "title": f"Spec language: express {tail.replace('_', ' ')}s",
                "kind": "experiment",
                "targets": ["lattice/spec/model.py"],
                "why": f"{f} rule(s) are unchecked because the DSL cannot say this "
                "(docs/lattice-language-rules.md: all targets or gated)",
            }
        return {
            "title": f"Unchecked rules ({tail.replace('_', ' ')})",
            "kind": "experiment",
            "targets": ["docs/lattice-reference.md"],
            "why": f"{f} rule(s) unchecked for this reason",
        }
    if head == "human":
        if tail == "spec_sync":
            return {
                "title": 'Sync the spec before runs: sync_spec = "auto"',
                "kind": "change_set",
                "targets": ["studio.toml"],
                "why": f"the owner synced the spec by hand {f} time(s)",
            }
        return {
            "title": f"Owner had to {tail} {f} time(s)",
            "kind": "experiment",
            "targets": ["lattice/studio/plan.py"],
            "why": "each is a plan that needed a hand; the log counts them, it cannot time them",
        }
    if head == "change":
        return {
            "title": f"{f} change set(s) rejected",
            "kind": "experiment",
            "targets": ["lattice/spec/draft.py"],
            "why": "drafts the owner did not take",
        }
    if head == "land":
        return {
            "title": f"Warn before a run when the target cannot land ({tail})",
            "kind": "change_set",
            "targets": ["lattice/studio/plan.py"],
            "why": "a level passed its gate and could not land",
        }
    if tail == "spec_problem":
        return {
            "title": "Lint the checks the checker calls a spec problem",
            "kind": "change_set",
            "targets": ["lattice/spec/lint.py", "lattice/spec/preconditions.py"],
            "why": "the check found expectations no implementation can satisfy",
        }
    if head in LAYERS:
        return {
            "title": f"Layer {head} findings of class {tail} ({f})",
            "kind": "experiment",
            "targets": ["lattice/spec/check.py"],
            "why": "a recurring check finding class; its fix is in the checker",
        }
    return {"title": key, "kind": "experiment", "targets": [], "why": ""}


def _x_expectation(c: Cluster, to: int) -> dict[str, Any] | None:
    """The saving the logged signal predicts for fewer X repeats: X falls with the number of
    tries per race, ``c x (1 - to / r)``, r the repeats the logged gates ran with."""
    rs = [o.x_repeats for o in c.occ if o.x_repeats]
    if not rs or c.cost_per is None:
        return None
    r = max(set(rs), key=rs.count)
    if r <= to:
        return None
    drop = 1 - to / r
    return {
        "x_repeats_from": r,
        "x_repeats_to": to,
        "x_seconds_now": round(c.cost_per, 1),
        "saving_s_per_gate": round(c.cost_per * drop, 1),
        "drop": round(drop, 3),
        "basis": "layer X seconds scale with tries per race (x_repeats)",
    }


def _proposal(c: Cluster) -> dict[str, Any]:
    t = _template(c)
    targets = list(t["targets"])
    protected = [p for p in targets if is_protected(p)]
    status = "needs_owner" if protected else "open"
    out = {
        "id": "imp-" + hashlib.sha1(c.key.encode()).hexdigest()[:8],
        "title": t["title"],
        "signal_key": c.key,
        "evidence": [o.ref for o in c.occ][:25],
        "score": round(c.score, 2) if c.score is not None else None,
        "unit": c.unit,
        "score_class": c.score_class,
        "frequency": c.frequency,
        "cost_per": round(c.cost_per, 2) if c.cost_per is not None else None,
        "kind": t["kind"],
        "target_paths": targets,
        "why": t["why"],
        "status": status,
        "draftable": status == "open",
    }
    if protected:
        out["why"] += (
            f" -- touches {', '.join(protected)} (the checker or the spec language): "
            "the owner decides; never drafted automatically"
        )
    if t.get("expected"):
        out["expected"] = t["expected"]
    return out


def analyze(
    events: Sequence[Mapping[str, Any]],
    ledger: Sequence[Mapping[str, Any]] = (),
    top: int = DEFAULT_TOP,
) -> dict[str, Any]:
    """The report: ``{proposals, events, method, blind_spots, closed, clusters}``."""
    cl = clusters(events, ledger)
    open_ = [c for c in cl if c.closed is None]
    closed = []
    for c in cl:
        if c.closed is None:
            continue
        at = str(c.closed.get("ts") or "")
        closed.append(
            {
                "signal_key": c.key,
                "id": c.closed.get("id"),
                "commit": c.closed.get("commit"),
                "frequency": c.frequency,
                "recurred": sum(o.count for o in c.occ if at and o.ts > at),
            }
        )
    return {
        "proposals": [_proposal(c) for c in open_[: max(0, int(top))]],
        "events": len(events),
        "method": METHOD,
        "blind_spots": list(BLIND_SPOTS),
        "closed": closed,
        "clusters": [
            {
                "signal_key": c.key,
                "score_class": c.score_class,
                "frequency": c.frequency,
                "cost_per": c.cost_per,
                "score": c.score,
                "unit": c.unit,
                "closed": c.closed is not None,
            }
            for c in cl
        ],
    }


def analyze_dir(state_dir: Path | str, top: int = DEFAULT_TOP) -> dict[str, Any]:
    """:func:`analyze` of a state directory's ``events.jsonl`` and ``improvements.jsonl``
    (``warnings`` added)."""
    d = Path(state_dir)
    res = read_events(d / EVENTS_FILE)
    ledger, lw = read_ledger(d / IMPROVEMENTS_FILE)
    out = analyze(res.events, ledger, top)
    out["warnings"] = res.warnings + lw
    return out


def report_text(rep: Mapping[str, Any]) -> str:
    """The report for a terminal: the method, its blind spots, the proposals, the closed."""
    lines = ["Lattice improve: proposals from the event log (no model; the owner decides)", ""]
    lines.append("Method: " + str(rep["method"]))
    lines.append("")
    lines.append("Blind spots:")
    lines += [f"  - {b}" for b in rep["blind_spots"]]
    lines.append("")
    lines.append(f"Events read: {rep['events']}")
    props = rep["proposals"]
    if not props:
        lines.append("No proposals: no signal in the log (or every one is closed).")
    for i, p in enumerate(props, 1):
        sc = (
            f"{p['frequency']} x {_fmt(p['cost_per'], p['unit'])} = {_fmt(p['score'], p['unit'])}"
            if p["score"] is not None
            else f"f = {p['frequency']} (no cost recorded)"
        )
        lines.append("")
        lines.append(f"{i}. [{p['score_class']}] {p['title']}")
        lines.append(f"   signal {p['signal_key']}: {sc}")
        lines.append(
            f"   kind {p['kind']}; targets: {', '.join(p['target_paths']) or '-'}; "
            f"status {p['status']}"
        )
        if p.get("expected"):
            e = p["expected"]
            lines.append(
                f"   expected: X {e['x_seconds_now']} s -> saving "
                f"{e['saving_s_per_gate']} s a gate ({e['drop']:.0%}; x_repeats "
                f"{e['x_repeats_from']} -> {e['x_repeats_to']})"
            )
        lines.append(f"   why: {p['why']}")
        lines.append(
            f"   evidence: {', '.join(p['evidence'][:6])}"
            + (" ..." if len(p["evidence"]) > 6 else "")
        )
    if rep.get("closed"):
        lines.append("")
        lines.append("Closed (left out):")
        for c in rep["closed"]:
            rec = f", recurred {c['recurred']} time(s) after closing" if c["recurred"] else ""
            lines.append(
                f"  - {c['signal_key']} ({c.get('id') or '-'}, {c.get('commit') or '-'})"
                f": f = {c['frequency']}{rec}"
            )
    for w in rep.get("warnings") or []:
        lines.append(f"warning: {w}")
    return "\n".join(lines) + "\n"
