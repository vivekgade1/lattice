"""Render a :class:`~lattice.security.schema.Report` for humans (a text table) and as JSON.
The output is for a person reading it; a finding never edits the spec or the code."""

from __future__ import annotations

import json

from lattice.security.run import distinct_findings
from lattice.security.schema import BLIND_SPOTS, SEVERITY, Report


def to_json(report: Report) -> str:
    return json.dumps(report.to_dict(), indent=2)


def to_text(report: Report) -> str:
    lines: list[str] = []
    lines.append("lattice attack -- for the owner's own systems only")
    lines.append(f"target: {report.target} (host {report.host})")
    lines.append(f"scope: {report.scope}   classes: {', '.join(report.classes)}")
    lines.append(
        f"planned {report.attacks_planned} attacks; ran in {report.wall_seconds:.1f}s; "
        f"out dir: {report.out_dir or '(none)'}"
    )
    lines.append("")
    lines.append(
        f"{'class':<8} {'sev':<7} {'run':>4} {'refuted':>8} {'findings':>9} {'not_refuted':>12}"
    )
    lines.append("-" * 54)
    for c in report.classreports:
        lines.append(
            f"{c.cls:<8} {SEVERITY.get(c.cls, '?'):<7} {c.run:>4} {c.refuted:>8} "
            f"{c.findings:>9} {c.not_refuted:>12}"
        )
    confirmed = report.confirmed
    lines.append("")
    lines.append(
        f"confirmed findings: {len(confirmed)} "
        f"({distinct_findings(report)} distinct by class/endpoint/rule); "
        f"unreproduced claims: {len(report.findings) - len(confirmed)}"
    )
    if confirmed:
        lines.append("")
        lines.append("CONFIRMED (reproduced on a fresh database and seed):")
        for f in confirmed:
            lines.append(f"  [{f.severity}] {f.cls}: {f.title}")
            lines.append(f"      rule: {f.rule}; expected: {f.expected}")
            lines.append(f"      observed: {f.detail}")
    unrep = [f for f in report.findings if not f.confirmed]
    if unrep:
        lines.append("")
        lines.append(f"not confirmed ({len(unrep)} claim(s) did not reproduce):")
        for f in unrep:
            lines.append(f"  {f.cls}: {f.title} -- {f.detail}")
    lines.append("")
    lines.append("A spec-driven attack cannot see:")
    for spot in BLIND_SPOTS:
        lines.append(f"  - {spot}")
    lines.append('"no confirmed findings" means "not refuted by these attacks", never "secure".')
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


__all__ = ["to_json", "to_text"]
