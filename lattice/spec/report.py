"""The check report: findings of the four layers, the cases run, and the verdict.

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

Layer = Literal["A", "B", "C", "D"]
LAYERS: tuple[str, ...] = ("A", "B", "C", "D")
LAYER_NAMES = {
    "A": "static checks on the app's code",
    "B": "endpoint answers against the spec programs",
    "C": "invariants after every request",
    "D": "scenarios",
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
        A finding, ``<layer>:<endpoint>:<case>`` otherwise."""
        if self.layer == "A":
            return f"A:{self.code}:{self.endpoint or ''}:{self.details.get('message', '')}"
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
    run; ``unchecked`` lists the spec's requirements no layer checks."""

    layers: str
    findings: list[CheckFinding] = field(default_factory=list)
    cases: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    accepted: list[tuple[CheckFinding, str]] = field(default_factory=list)
    stale: list[Accepted] = field(default_factory=list)

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
        return {
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


def _detail_lines(f: CheckFinding) -> list[str]:
    d = f.details
    out: list[str] = []
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
