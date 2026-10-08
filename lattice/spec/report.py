"""The check report: findings of the layers, the cases run, and the verdict; with layer
R, every rule item's verdict (``rules``: per item its verdict, probes, reason and the
observed fact when refuted; the table and route verdicts; a refuted item is also a
finding, with the refuting probe's request, answer and database difference).

The JSON shape follows the Studio data contracts (docs/design/lattice-visualization-
figma-spec.md §10): a layer A finding carries pycheck's ``code``, ``file``, ``line``,
``message`` (as ``details.message``), ``hint`` and ``key``; a layer B/C/D finding carries
the ``request`` (the case), ``db_diff`` (``{table: {added[], removed[], changed[]}}``)
and ``invariant_violations[]``; ``cases`` maps every case run to its ``verdict``
(``correct`` / ``silent`` / ``loud``) and ``reason``.

A finding's ``verdict`` is ``loud`` when the failure shows itself (a 5xx, an app that
does not start, a request that cannot run) and ``silent`` otherwise: a wrong answer, a
wrong database effect, a broken invariant, or a static (layer A) finding of a defect that
would be silent.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from fnmatch import fnmatchcase
from typing import Any, Literal

Layer = Literal["A", "B", "C", "D", "R", "X"]
LAYERS: tuple[str, ...] = ("A", "B", "C", "D", "R", "X")
LAYER_NAMES = {
    "A": "static checks on the app's code",
    "B": "endpoint answers against the spec programs",
    "C": "invariants after every request",
    "D": "scenarios",
    "R": "rule items probed on the app",
    "X": "concurrent races of the writes behind count bounds and machines",
}


@dataclass
class CheckFinding:
    """One violation. ``rule`` is the requirement it breaks, in words (the invariant's
    sentence, the scenario's name, the guarantee of a pycheck code); ``endpoint`` is
    ``METHOD /path``; ``case`` names the request (a layer B case name, ``<scenario> /
    step <n>``, or ``(seed)``); ``request`` the request itself."""

    layer: str
    verdict: Literal["silent", "loud"]
    rule: str
    endpoint: str | None = None
    case: str | None = None
    request: dict[str, Any] | None = None
    details: dict[str, Any] = field(default_factory=dict)
    db_diff: dict[str, dict[str, list[Any]]] = field(default_factory=dict)
    invariant_violations: list[dict[str, Any]] = field(default_factory=list)
    code: str | None = None  # layer A: the pycheck code
    file: str | None = None
    line: int | None = None

    @property
    def key(self) -> str:
        """What a baseline entry matches: ``A:<code>:<endpoint>:<message>`` for a layer
        A finding, ``X:<endpoint>:<scenario>/<step>[+<step>]:<rule>`` for a layer X
        finding (the rule: the invariant's name, the machine's ``Table.column``, or
        ``no_server_error_under_concurrency``), ``<layer>:<endpoint>:<case>`` otherwise."""
        if self.layer == "A":
            return f"A:{self.code}:{self.endpoint or ''}:{self.details.get('message', '')}"
        if self.layer == "X":
            rule = self.details.get("rule_id", self.rule)
            return f"X:{self.endpoint or ''}:{self.case or ''}:{rule}"
        return f"{self.layer}:{self.endpoint or ''}:{self.case or ''}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"key": self.key}

    def summary(self) -> str:
        """One line: where, and for which request."""
        if self.layer == "A":
            where = f"{self.file}:{self.line}" if self.file else ""
            return f"[A {self.verdict}] {self.code} {where} {self.endpoint or ''}".rstrip()
        req = self.request or {}
        who = f" as {req['user']}" if req.get("user") else ""
        call = f"{req.get('method', '')} {req.get('path', '')}".strip() or (self.endpoint or "")
        return f"[{self.layer} {self.verdict}] {call}{who} ({self.case})"


@dataclass(frozen=True)
class Accepted:
    """A baseline entry: findings whose ``key`` matches ``pattern`` (fnmatch, case-
    sensitive) are the app's known, accepted state, with ``reason``. They are reported
    but do not fail the check; an entry that matches nothing is reported as stale."""

    pattern: str
    reason: str


@dataclass
class Report:
    """The result of :func:`lattice.spec.check.check`. ``verdict`` is ``pass`` when no
    layer found anything. ``cases`` maps each case run (layers B and D) to ``{layer,
    endpoint, verdict, reason, request}``; ``notes`` say what was skipped or could not
    run; ``unchecked`` lists the spec's requirements no layer checks. ``rules`` (layer R
    only): every rule item's verdict (:class:`lattice.spec.probes.RuleResult`)."""

    layers: str
    findings: list[CheckFinding] = field(default_factory=list)
    cases: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    accepted: list[tuple[CheckFinding, str]] = field(default_factory=list)
    stale: list[Accepted] = field(default_factory=list)
    rules: Any = None
    # layer X only: every race run (its label, kind, steps, copies, tries, outcome)
    races: list[dict[str, Any]] | None = None

    def apply_baseline(self, baseline: Sequence[Accepted]) -> None:
        """Move the findings ``baseline`` matches to ``accepted``; record unused entries."""
        used: set[int] = set()
        kept: list[CheckFinding] = []
        for f in self.findings:
            hit = next((i for i, a in enumerate(baseline) if fnmatchcase(f.key, a.pattern)), None)
            if hit is None:
                kept.append(f)
            else:
                used.add(hit)
                self.accepted.append((f, baseline[hit].reason))
        self.findings = kept
        self.stale = [a for i, a in enumerate(baseline) if i not in used]

    @property
    def verdict(self) -> str:
        return "fail" if self.findings else "pass"

    @property
    def counts(self) -> dict[str, int]:
        """Findings per layer run."""
        return {
            layer: sum(f.layer == layer for f in self.findings)
            for layer in LAYERS
            if layer in self.layers
        }

    def to_dict(self) -> dict[str, Any]:
        out = {
            "verdict": self.verdict,
            "layers": self.layers,
            "counts": self.counts,
            "findings": [f.to_dict() for f in self.findings],
            "cases": self.cases,
            "notes": self.notes,
            "unchecked": self.unchecked,
            "accepted": [f.to_dict() | {"reason": r} for f, r in self.accepted],
            "stale_baseline": [asdict(a) for a in self.stale],
        }
        if self.rules is not None:
            out["rules"] = self.rules.to_dict()
        if self.races is not None:
            out["races"] = self.races
        return out

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def render(self) -> str:
        """For humans, rule first: each broken rule, then the requests that break it."""
        counts = ", ".join(f"{k} {v}" for k, v in self.counts.items())
        n = len(self.findings)
        lines = [
            f"{self.verdict.upper()}: {n} finding{'s' if n != 1 else ''} ({counts}); "
            f"{len(self.cases)} cases run"
        ]
        by_rule: dict[tuple[str, str], list[CheckFinding]] = {}
        for f in self.findings:
            by_rule.setdefault((f.layer, f.rule), []).append(f)
        for (layer, rule), found in by_rule.items():
            lines.append("")
            lines.append(f"rule ({layer}: {LAYER_NAMES[layer]}): {rule}")
            for f in found:
                lines.append("  " + f.summary())
                lines.extend("    " + x for x in _detail_lines(f))
        if self.accepted:
            lines.append("")
            n = len(self.accepted)
            lines.append(f"accepted (approved by the spec or the app's baseline, {n}):")
            lines.extend(f"  {f.summary()}: {r}" for f, r in self.accepted)
        if self.stale:
            lines.append("")
            lines.append("stale baseline entries (they match nothing; remove them):")
            lines.extend(f"  - {a.pattern}" for a in self.stale)
        if self.rules is not None:
            lines.append("")
            lines.extend(_rule_lines(self.rules))
        if self.unchecked:
            lines.append("")
            lines.append("not checked (stated in the spec, not checkable yet):")
            lines.extend(f"  - {u}" for u in self.unchecked)
        if self.notes:
            lines.append("")
            lines.append("notes:")
            lines.extend(f"  - {x}" for x in self.notes)
        return "\n".join(lines)


def _short(value: Any, limit: int = 160) -> str:
    text = json.dumps(value, default=str) if not isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _rule_lines(rules: Any) -> list[str]:
    """Layer R: the counts, then every item not verified, with its reason."""
    s = rules.summary
    counts = ", ".join(f"{v} {k}" for k, v in s["items"].items())
    lines = [
        f"rule items ({counts}); tables verified {s['tables']['verified']}/"
        f"{s['tables']['total']}, routes verified {s['routes']['verified']}/"
        f"{s['routes']['total']}:"
    ]
    for item_id, v in rules.items.items():
        if v.verdict != "verified":
            lines.append(f"  {v.verdict:11} {item_id}: {v.reason}")
    return lines


def _detail_lines(f: CheckFinding) -> list[str]:
    d = f.details
    out: list[str] = []
    if f.layer == "R":
        out.append(str(d.get("reason", "")))
        if d.get("observed") is not None:
            out.append(
                f"observed: {_short(d['observed'])} (predicted {_short(d.get('predicted'))})"
            )
        actual = d.get("actual") or {}
        if actual.get("status") is not None:
            out.append(f"answered {actual['status']} {_short(actual.get('body'))}")
        for table, diff in f.db_diff.items():
            parts = [f"{k} {_short(v)}" for k, v in diff.items() if v]
            if parts:
                out.append(f"db {table}: " + "; ".join(parts))
        return out
    if f.layer == "X":
        steps = " + ".join(
            f"step {s['step']} {s['method']} {s['path']}" + (f" as {s['user']}" if s.get("user")
                                                            else "")
            for s in d.get("steps", ())
        )  # fmt: skip
        out.append(
            f"raced {d.get('k')} at once: {steps} (scenario {d.get('scenario')!r}; refuted on "
            f"try {d.get('try')} of {d.get('repeats')})"
        )
        for a in d.get("answers", ()):
            statuses = ", ".join(f"{n}x {s}" for s, n in a.get("statuses", {}).items())
            err = f"; {a['error']}" if a.get("error") else ""
            out.append(f"step {a['step']} answered {statuses}{err}")
        for row in d.get("rows", ())[:5]:
            out.append(f"violating row: {_short(row, 400)}")
        if d.get("also_in"):
            out.append(f"also refuted by the same race in: {', '.join(d['also_in'])}")
        return out
    if f.layer == "A":
        if d.get("message"):
            out.append(str(d["message"]))
        if d.get("hint"):
            out.append(f"fix: {d['hint']}")
        return out
    expected, actual = d.get("expected"), d.get("actual")
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        out.append(
            f"expected {expected.get('status')} {_short(expected.get('body'))}; "
            f"got {actual.get('status')} {_short(actual.get('body'))}"
        )
    for p in d.get("problems", ()):
        out.append(str(p))
    for table, diff in f.db_diff.items():
        parts = [f"{k} {_short(v)}" for k, v in diff.items() if v]
        if parts:
            out.append(f"db {table}: " + "; ".join(parts))
    for v in f.invariant_violations:
        out.append(f"violates {v.get('invariant')}: {_short(v.get('rows'))}")
    if d.get("error"):
        out.append(f"error: {d['error']}")
    return out


__all__ = ["LAYERS", "LAYER_NAMES", "CheckFinding", "Report"]
