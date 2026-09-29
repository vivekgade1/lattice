"""The option-1 main experiment's pre-registered tests, applied mechanically
(docs/superpowers/specs/2026-09-29-option1-main-experiment.md): arm Q vs arm P, per model.

* one-sided Fisher exact test on silent vs not-silent runs (Q fewer silent);
* a per-ticket paired sign test on silent counts, ties dropped (Q fewer silent);
* the mechanism test: runs silent on a ``one-statement/atomic`` case;
* the correctness guard (Q's correct rate not lower than P's by more than 10 points);
* Wilson 95% intervals.

``python -m bench.edit_slice.main_analysis RESULTS.jsonl [RESULTS.jsonl ...]`` prints the
verdicts. Exact arithmetic, no scipy.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import comb
from pathlib import Path
from typing import Any

from bench.edit_slice.pilot_gate import wilson
from bench.edit_slice.repos.multivendor import mechanism

TICKETS = (
    "t38_reorder", "t39_bulk_price", "t40_edit_order_items", "t41_close_shop",
    "t42_split_order", "t43_vendor_onboarding", "t44_merge_orders",
)  # fmt: skip
MODELS = ("claude-sonnet-5", "claude-haiku-4-5")
GUARD_POINTS = 10


def fisher_less(a: int, b: int, c: int, d: int) -> float:
    """One-sided Fisher exact p for the table [[a, b], [c, d]] (rows: treatment, control;
    columns: event, no event): P(treatment has <= a events | the margins)."""
    row1, col1, n = a + b, a + c, a + b + c + d
    lo = max(0, col1 - (c + d))
    total = comb(n, row1)
    return sum(comb(col1, k) * comb(n - col1, row1 - k) for k in range(lo, a + 1)) / total


def sign_test_less(wins: int, losses: int) -> float:
    """One-sided sign test p: P(X <= losses) for X ~ Binomial(wins + losses, 1/2), where a
    win is a ticket on which the treatment had fewer events."""
    n = wins + losses
    if n == 0:
        return 1.0
    return sum(comb(n, k) for k in range(losses + 1)) / 2**n


@dataclass
class Arm:
    runs: int = 0
    correct: int = 0
    silent: int = 0
    loud: int = 0
    atomic_silent: int = 0

    def rate(self, k: int) -> str:
        lo, hi = wilson(k, self.runs)
        return f"{k}/{self.runs} [{lo:.0%}, {hi:.0%}]"


def _atomic(run: Mapping[str, Any]) -> bool:
    return any(mechanism(c) == "one-statement/atomic" for c in run.get("silent_cases") or [])


def _rate(a: Arm, k: int) -> float:
    return k / a.runs if a.runs else 0.0


def verdict(records: Iterable[Mapping[str, Any]], model: str) -> dict[str, Any]:
    arms = {"P": Arm(), "Q": Arm()}
    per_ticket = {t: {"P": 0, "Q": 0} for t in TICKETS}
    seen: set[tuple[str, str, int]] = set()
    for r in records:
        if r.get("model") != model or r.get("arm") not in arms or r["ticket"] not in per_ticket:
            continue
        key = (r["arm"], r["ticket"], r["rep"])
        if key in seen:
            raise ValueError(f"duplicate run {key}")
        seen.add(key)
        a = arms[r["arm"]]
        a.runs += 1
        if r["outcome"] == "correct":
            a.correct += 1
        elif r["outcome"] == "silent":
            a.silent += 1
            per_ticket[r["ticket"]][r["arm"]] += 1
            a.atomic_silent += _atomic(r)
        else:
            a.loud += 1
    p, q = arms["P"], arms["Q"]
    fisher = fisher_less(q.silent, q.runs - q.silent, p.silent, p.runs - p.silent)
    wins = sum(1 for t in TICKETS if per_ticket[t]["Q"] < per_ticket[t]["P"])
    losses = sum(1 for t in TICKETS if per_ticket[t]["Q"] > per_ticket[t]["P"])
    sign = sign_test_less(wins, losses)
    if p.runs and q.runs and _rate(q, q.silent) >= _rate(p, p.silent):
        decision = "REFUTED"
    elif fisher < 0.05 and wins > losses:
        guard = _rate(q, q.correct) >= _rate(p, p.correct) - GUARD_POINTS / 100
        decision = "CONFIRMED" if guard else "CONFIRMED-BUT-TRADE"
    else:
        decision = "NOT CONFIRMED"
    atomic = fisher_less(q.atomic_silent, q.runs - q.atomic_silent,
                         p.atomic_silent, p.runs - p.atomic_silent)  # fmt: skip
    return {
        "model": model, "P": p, "Q": q, "fisher_p": fisher, "sign": (wins, losses, sign),
        "decision": decision, "atomic_p": atomic, "per_ticket": per_ticket,
    }  # fmt: skip


def render(v: Mapping[str, Any]) -> str:
    p, q = v["P"], v["Q"]
    wins, losses, sign = v["sign"]
    lines = [
        f"## {v['model']}",
        f"P: correct {p.rate(p.correct)} · silent {p.rate(p.silent)} · loud {p.rate(p.loud)}"
        f" · atomic-silent {p.atomic_silent}",
        f"Q: correct {q.rate(q.correct)} · silent {q.rate(q.silent)} · loud {q.rate(q.loud)}"
        f" · atomic-silent {q.atomic_silent}",
        f"Fisher (Q fewer silent) p = {v['fisher_p']:.4f}; sign test {wins} wins / {losses} "
        f"losses, p = {sign:.4f}; mechanism (atomic) p = {v['atomic_p']:.4f}",
        f"decision: {v['decision']}",
        "per ticket silent (P, Q): "
        + ", ".join(f"{t.split('_')[0]} {c['P']}/{c['Q']}" for t, c in v["per_ticket"].items()),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results", type=Path, nargs="+")
    args = ap.parse_args(argv)
    records = [json.loads(line) for f in args.results for line in f.read_text().splitlines()
               if line.strip()]  # fmt: skip
    print("\n\n".join(render(verdict(records, m)) for m in MODELS))


if __name__ == "__main__":
    main()
