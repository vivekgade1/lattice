"""The option-1 pilot's gates, applied mechanically to arm P's run records.

Pre-registered in ``docs/superpowers/specs/2026-09-29-option1-pilot.md``; the mechanism
tags are :data:`bench.edit_slice.repos.multivendor.MECHANISMS`. Per family:

1. **silent-rate gate** -- at least 2 of its 12 runs silent (>= 15%);
2. **mechanism gate** -- a silent run is *covered* when every case it failed silently has
   a tag other than ``none``; the gate passes when at least half the family's silent runs
   outside the control ticket (t35) are covered, and fails when there are none.

``python -m bench.edit_slice.pilot_gate RESULTS.jsonl`` prints the report.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bench.edit_slice.repos.multivendor import CONTROL_TICKETS, mechanism

FAMILIES: dict[str, tuple[str, ...]] = {
    "A": ("t31_vendor_sales", "t32_monthly_report", "t33_top_sellers", "t34_customer_spend"),
    "O": ("t35_vendor_order_lines", "t36_cancel_order", "t37_product_buyers", "t38_reorder"),
}
MIN_SILENT = 2  # of 12 runs: >= 15%
ARM, MODEL = "P", "claude-sonnet-5"


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """The Wilson score 95% interval of k successes in n trials."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def covered(run: Mapping[str, Any]) -> bool:
    cases = run.get("silent_cases") or []
    return bool(cases) and all(mechanism(c) != "none" for c in cases)


@dataclass
class FamilyReport:
    name: str
    runs: int = 0
    correct: int = 0
    silent: int = 0
    loud: int = 0
    silent_outside_control: int = 0
    covered: int = 0
    silent_runs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def silent_gate(self) -> bool:
        return self.silent >= MIN_SILENT

    @property
    def mechanism_gate(self) -> bool:
        return self.silent_outside_control > 0 and 2 * self.covered >= self.silent_outside_control

    def render(self) -> str:
        def rate(k: int) -> str:
            lo, hi = wilson(k, self.runs)
            return f"{k}/{self.runs} [{lo:.0%}, {hi:.0%}]"

        lines = [
            f"family {self.name}: correct {rate(self.correct)} · silent {rate(self.silent)}"
            f" · loud {rate(self.loud)}",
            f"  silent-rate gate (>= {MIN_SILENT}): {'PASS' if self.silent_gate else 'FAIL'}",
            f"  mechanism gate: {self.covered}/{self.silent_outside_control} silent runs "
            f"outside the control covered: {'PASS' if self.mechanism_gate else 'FAIL'}",
        ]
        for r in self.silent_runs:
            tags = ", ".join(f"{c} [{mechanism(c)}]" for c in r.get("silent_cases") or [])
            mark = "covered" if covered(r) else "not covered"
            lines.append(f"  - {r['ticket']} rep {r['rep']} ({mark}): {tags}")
        return "\n".join(lines)


@dataclass
class GateReport:
    families: dict[str, FamilyReport]

    @property
    def decision(self) -> str:
        go = [f for f, r in self.families.items() if r.silent_gate and r.mechanism_gate]
        if go:
            return "GO: " + " + ".join(go)
        if any(r.silent_gate for r in self.families.values()):
            return "SILENT-BUT-UNCOVERED"
        return "NO-GO"

    def render(self) -> str:
        parts = [r.render() for r in self.families.values()]
        return "\n\n".join([*parts, f"decision: {self.decision}"])


def evaluate_gates(records: Iterable[Mapping[str, Any]]) -> GateReport:
    families = {name: FamilyReport(name) for name in FAMILIES}
    family_of = {t: name for name, ts in FAMILIES.items() for t in ts}
    seen: set[tuple[str, int]] = set()
    for r in records:
        if r.get("arm") != ARM or r.get("model") != MODEL or r["ticket"] not in family_of:
            continue
        key = (r["ticket"], r["rep"])
        if key in seen:
            raise ValueError(f"duplicate run {key}")
        seen.add(key)
        f = families[family_of[r["ticket"]]]
        f.runs += 1
        outcome = r["outcome"]
        if outcome == "correct":
            f.correct += 1
        elif outcome == "silent":
            f.silent += 1
            f.silent_runs.append(dict(r))
            if r["ticket"] not in CONTROL_TICKETS:
                f.silent_outside_control += 1
                f.covered += covered(r)
        else:
            f.loud += 1
    return GateReport(families)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("results", type=Path)
    args = p.parse_args(argv)
    lines = args.results.read_text().splitlines()
    print(evaluate_gates(json.loads(line) for line in lines if line.strip()).render())


if __name__ == "__main__":
    main()
