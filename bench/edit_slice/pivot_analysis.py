"""Experiment D's pre-registered analysis (plan 2026-10-05-pivot-spec-authoring.md, item
D): arms S, S+ and S* on the pivot's held-out tickets t45-t56.

* **H1' (Sonnet):** S+ has fewer silent runs than S (one-sided Fisher exact on silent vs
  not-silent runs, p < 0.05); the correctness guard (S+'s correct rate >= S's, no
  tolerance); the spec bar (S+'s pooled spec error rate, sum rejected / sum drafted over
  S+'s runs, <= 25%). CONFIRMED only if all three hold.
* **H2' (Haiku):** the same test as H1'.
* **H3' (descriptive, not tested):** the gaps S+ vs S* and S vs S*.
* per-ticket sign tests (reported, not part of a verdict) for H1' and H2'.

Also reported: the spec rules drafted before vs after S+'s self-review
(``spec_drafted_before_review`` / ``spec_drafted_after_review``) and its failure count
(``spec_review_failed``); catches by layer; false findings; $/correct, mean rounds,
s/run, tokens; per-ticket silent counts.

Reuses Phase 1's statistical primitives (:func:`bench.edit_slice.main_analysis.fisher_less`,
:func:`bench.edit_slice.main_analysis.sign_test_less`, :func:`bench.edit_slice.pilot_gate.wilson`)
and its table style; :mod:`bench.edit_slice.phase1_analysis` itself is untouched (its
``hypothesis`` is bound to Phase 1's own ticket list, so it is not reused directly).

A JSONL line with a (model, arm, ticket, rep) key already seen keeps the LAST one: the
runner's ``--resume`` redoes error records, so a resumed run's output can hold more than
one line per key.

    python -m bench.edit_slice.pivot_analysis OUTCOMES.jsonl [OUTCOMES.jsonl ...]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bench.edit_slice.main_analysis import fisher_less, sign_test_less
from bench.edit_slice.pilot_gate import wilson

TICKETS = (
    "t45_restock", "t46_ship_order", "t47_quote", "t48_vendor_dashboard",
    "t49_remove_order_item", "t50_cancel_batch", "t51_low_stock", "t52_order_by_vendor",
    "t53_move_stock", "t54_order_stats", "t55_vendor_directory", "t56_order_received",
)  # fmt: skip
SONNET, HAIKU = "claude-sonnet-5", "claude-haiku-4-5"
MODEL_NAMES = {SONNET: "Sonnet", HAIKU: "Haiku"}
ARMS = ("S", "S+", "S*")
SPEC_ARMS = ("S", "S+")  # S* has no draft step: nothing to report there
LAYERS = ("A", "B", "C", "D")
ALPHA = 0.05
SPEC_BAR = 0.25

FISHER, GUARD, BAR = "fewer silent (Fisher)", "correctness guard", "spec bar"


@dataclass
class ArmStats:
    runs: int = 0
    correct: int = 0
    silent: int = 0
    loud: int = 0
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    rounds: int = 0
    wall_s: float = 0.0
    # the spec step (S, S+; S* has no drafting step, so these stay 0 there)
    spec_drafted: int = 0
    spec_rejected: int = 0
    spec_failed: int = 0
    spec_rounds: int = 0
    # S+'s self-review: rules drafted before/after it, model calls, and how often the
    # revision was invalid or refused (the first draft kept instead)
    spec_review_rounds: int = 0
    spec_review_failed: int = 0
    spec_drafted_before_review: int = 0
    spec_drafted_after_review: int = 0
    # catches by layer: repair rounds in total, and per layer the repair rounds whose
    # fed-back findings included it
    repair_rounds: int = 0
    repaired_runs: int = 0
    layer_rounds: Counter[str] = field(default_factory=Counter)
    # runs the oracle scores correct whose final candidate still had check findings
    false_findings: int = 0
    false_finding_count: int = 0
    silent_by_ticket: Counter[str] = field(default_factory=Counter)

    def rate(self, k: int) -> float:
        return k / self.runs if self.runs else 0.0

    def ci(self, k: int) -> str:
        lo, hi = wilson(k, self.runs)
        return f"{k}/{self.runs} [{lo:.0%}, {hi:.0%}]"

    def spec_error_rate(self) -> float | None:
        """Pooled: sum rejected / sum drafted over the arm's runs (None: nothing drafted)."""
        return self.spec_rejected / self.spec_drafted if self.spec_drafted else None

    def mean(self, total: float) -> float:
        return total / self.runs if self.runs else 0.0


def _selected(records: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The records in scope (a known model, arm and ticket). A (model, arm, ticket, rep)
    key seen more than once keeps the LAST record (the runner's ``--resume`` redoes error
    records, so a resumed run's JSONL can hold more than one line per key)."""
    by_key: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for r in records:
        if r.get("model") not in MODEL_NAMES or r.get("arm") not in ARMS:
            continue
        if r.get("ticket") not in TICKETS:
            continue
        key = (r["model"], r["arm"], r["ticket"], r["rep"])
        by_key[key] = r
    return list(by_key.values())


def collect(records: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], ArmStats]:
    """(model, arm) -> totals. Outcome kinds: correct, silent, else loud."""
    stats: dict[tuple[str, str], ArmStats] = {}
    for r in _selected(records):
        a = stats.setdefault((r["model"], r["arm"]), ArmStats())
        a.runs += 1
        outcome = r.get("outcome")
        if outcome == "correct":
            a.correct += 1
        elif outcome == "silent":
            a.silent += 1
            a.silent_by_ticket[r["ticket"]] += 1
        else:
            a.loud += 1
        a.cost += r.get("cost_usd") or 0.0
        a.input_tokens += r.get("input_tokens") or 0
        a.output_tokens += r.get("output_tokens") or 0
        a.rounds += r.get("rounds") or 0
        a.wall_s += r.get("wall_s") or 0.0
        a.spec_drafted += r.get("spec_drafted") or 0
        a.spec_rejected += r.get("spec_rejected") or 0
        a.spec_failed += bool(r.get("spec_failed"))
        a.spec_rounds += r.get("spec_rounds") or 0
        a.spec_review_rounds += r.get("spec_review_rounds") or 0
        a.spec_review_failed += bool(r.get("spec_review_failed"))
        a.spec_drafted_before_review += r.get("spec_drafted_before_review") or 0
        a.spec_drafted_after_review += r.get("spec_drafted_after_review") or 0
        fed = r.get("layers_fed_back") or []
        a.repair_rounds += len(fed)
        a.repaired_runs += bool(fed)
        for layers in fed:
            a.layer_rounds.update(set(layers))
        findings = r.get("final_check_findings") or 0
        if outcome == "correct" and findings > 0:
            a.false_findings += 1
            a.false_finding_count += findings
    return stats


def per_ticket_silent(records: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, dict]]:
    """model -> ticket -> arm -> silent runs."""
    stats = collect(records)
    return {m: {t: {arm: stats[(m, arm)].silent_by_ticket[t] if (m, arm) in stats else 0
                    for arm in ARMS} for t in TICKETS} for m in MODEL_NAMES}  # fmt: skip


def hypothesis(
    stats: Mapping[tuple[str, str], ArmStats],
    model: str,
    treat: str,
    control: str,
    *,
    full: bool,
) -> dict[str, Any]:
    """``treat`` has fewer silent runs than ``control`` on ``model``. ``full`` adds the
    correctness guard and the spec bar (H1', H2' both use it). The per-ticket sign test
    (ties dropped) is reported, not part of the decision."""
    t, c = stats.get((model, treat), ArmStats()), stats.get((model, control), ArmStats())
    fisher = fisher_less(t.silent, t.runs - t.silent, c.silent, c.runs - c.silent)
    tw, cw = t.silent_by_ticket, c.silent_by_ticket
    wins = sum(1 for k in TICKETS if tw[k] < cw[k])
    losses = sum(1 for k in TICKETS if tw[k] > cw[k])
    v: dict[str, Any] = {
        "model": model, "treat": treat, "control": control, "full": full,
        treat: t, control: c, "fisher_p": fisher,
        "sign": (wins, losses, sign_test_less(wins, losses)),
        "spec_error_rate": t.spec_error_rate(), "failed": [],
    }  # fmt: skip
    if not (t.runs and c.runs):
        v["decision"] = "NO DATA"
        return v
    if not fisher < ALPHA:
        v["failed"].append(FISHER)
    if full:
        if not t.rate(t.correct) >= c.rate(c.correct):
            v["failed"].append(GUARD)
        rate = t.spec_error_rate()
        if rate is None or rate > SPEC_BAR:
            v["failed"].append(BAR)
    v["decision"] = "NOT CONFIRMED" if v["failed"] else "CONFIRMED"
    return v


def h3(stats: Mapping[tuple[str, str], ArmStats], model: str) -> dict[str, Any]:
    """S+ vs S* and S vs S* on ``model``: the numbers only (pre-registered as reported,
    not tested)."""
    return {
        "model": model,
        "S": stats.get((model, "S"), ArmStats()),
        "S+": stats.get((model, "S+"), ArmStats()),
        "S*": stats.get((model, "S*"), ArmStats()),
    }


def verdicts(records: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    stats = collect(records)
    return {
        "H1": hypothesis(stats, SONNET, "S+", "S", full=True),
        "H2": hypothesis(stats, HAIKU, "S+", "S", full=True),
        "H3_sonnet": h3(stats, SONNET),
        "H3_haiku": h3(stats, HAIKU),
    }


# ---- rendering ---------------------------------------------------------------------------


def _money(a: ArmStats) -> str:
    return f"{a.cost / a.correct:.3f}" if a.correct else "—"


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.1%}"


def _rows(stats: Mapping[tuple[str, str], ArmStats], arms: Iterable[str]):
    arms = tuple(arms)
    for m, name in MODEL_NAMES.items():
        for arm in arms:
            if (m, arm) in stats:
                yield name, arm, stats[(m, arm)]


def _render_hypothesis(label: str, v: Mapping[str, Any]) -> list[str]:
    t, c = v[v["treat"]], v[v["control"]]
    wins, losses, sp = v["sign"]
    head = f"**{label}: {v['decision']}**"
    if v["failed"]:
        head += f" (failed: {', '.join(v['failed'])})"
    lines = [
        head,
        f"- silent {v['treat']} {t.ci(t.silent)} vs {v['control']} {c.ci(c.silent)}; "
        f"Fisher one-sided p = {v['fisher_p']:.4f} (needs < {ALPHA})",
        f"- per-ticket sign test (reported): {wins} wins / {losses} losses, p = {sp:.4f}",
    ]
    if v["full"]:
        rate = v["spec_error_rate"]
        lines += [
            f"- correctness guard: {v['treat']} {t.rate(t.correct):.1%} >= "
            f"{v['control']} {c.rate(c.correct):.1%}",
            f"- spec bar: {v['treat']} pooled spec error rate {_pct(rate)} "
            f"({t.spec_rejected}/{t.spec_drafted}) <= {SPEC_BAR:.0%}",
        ]
    return lines


def render(records: Iterable[Mapping[str, Any]]) -> str:
    records = list(records)
    stats, vs = collect(records), verdicts(records)
    out = ["## Experiment D: outcomes", "",
           "| model | arm | n | correct [95% CI] | silent [95% CI] | loud [95% CI] | $/correct "
           "| mean rounds | s/run | tokens in/out per run |",
           "|---|---|---:|---|---|---|---:|---:|---:|---:|"]  # fmt: skip
    for name, arm, a in _rows(stats, ARMS):
        out.append(
            f"| {name} | {arm} | {a.runs} | {a.ci(a.correct)} | {a.ci(a.silent)} | "
            f"{a.ci(a.loud)} | {_money(a)} | {a.mean(a.rounds):.2f} | {a.mean(a.wall_s):.0f} | "
            f"{a.mean(a.input_tokens):.0f}/{a.mean(a.output_tokens):.0f} |"
        )

    short = [t.split("_")[0] for t in TICKETS]
    out += ["", "### Silent runs per ticket", "",
            "| model | arm | " + " | ".join(short) + " | total |",
            "|---|---|" + "---:|" * (len(TICKETS) + 1)]  # fmt: skip
    for name, arm, a in _rows(stats, ARMS):
        counts = [a.silent_by_ticket[t] for t in TICKETS]
        out.append(f"| {name} | {arm} | " + " | ".join(map(str, counts)) + f" | {sum(counts)} |")

    out += ["", "### The spec step (S, S+)", "",
            "| model | arm | n | spec error rate (rejected/drafted) | spec_failed | "
            "mean spec_rounds |", "|---|---|---:|---|---:|---:|"]  # fmt: skip
    for name, arm, a in _rows(stats, SPEC_ARMS):
        out.append(f"| {name} | {arm} | {a.runs} | {_pct(a.spec_error_rate())} "
                   f"({a.spec_rejected}/{a.spec_drafted}) | {a.spec_failed} | "
                   f"{a.mean(a.spec_rounds):.2f} |")  # fmt: skip

    out += ["", "### S+'s self-review: rules drafted before vs after", "",
            "| model | n reviewed | drafted before | drafted after | spec_review_failed |",
            "|---|---:|---:|---:|---:|"]  # fmt: skip
    for m, name in MODEL_NAMES.items():
        a = stats.get((m, "S+"), ArmStats())
        reviewed = a.spec_review_rounds  # one review call per run that reached it
        out.append(f"| {name} | {reviewed} | {a.spec_drafted_before_review} | "
                   f"{a.spec_drafted_after_review} | {a.spec_review_failed} |")  # fmt: skip

    out += ["", "### Catches by layer (repair rounds whose fed-back findings included it)", "",
            "| model | arm | runs repaired | repair rounds | " + " | ".join(LAYERS) + " |",
            "|---|---|---:|---:|" + "---:|" * len(LAYERS)]  # fmt: skip
    for name, arm, a in _rows(stats, ARMS):
        out.append(f"| {name} | {arm} | {a.repaired_runs} | {a.repair_rounds} | "
                   + " | ".join(str(a.layer_rounds[x]) for x in LAYERS) + " |")  # fmt: skip

    out += ["", "### False findings (oracle-correct final candidate, check findings > 0)", "",
            "| model | arm | correct | false-finding runs | findings on them |",
            "|---|---|---:|---:|---:|"]  # fmt: skip
    for name, arm, a in _rows(stats, ARMS):
        out.append(f"| {name} | {arm} | {a.correct} | {a.false_findings} | "
                   f"{a.false_finding_count} |")  # fmt: skip

    out += ["", "## Pre-registered verdicts", ""]
    out += _render_hypothesis("H1' (Sonnet, S+ vs S)", vs["H1"]) + [""]
    out += _render_hypothesis("H2' (Haiku, S+ vs S)", vs["H2"]) + [""]
    for key, label in (("H3_sonnet", "Sonnet"), ("H3_haiku", "Haiku")):
        s, sp, ss = vs[key]["S"], vs[key]["S+"], vs[key]["S*"]
        out += [
            f"**H3' ({label}): descriptive, not tested**",
            f"- S*: correct {ss.ci(ss.correct)}, silent {ss.ci(ss.silent)}, "
            f"$/correct {_money(ss)}",
            f"- S+: correct {sp.ci(sp.correct)}, silent {sp.ci(sp.silent)}, "
            f"$/correct {_money(sp)}",
            f"- S: correct {s.ci(s.correct)}, silent {s.ci(s.silent)}, $/correct {_money(s)}",
            "",
        ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results", type=Path, nargs="+")
    args = ap.parse_args(argv)
    records = [json.loads(line) for f in args.results for line in f.read_text().splitlines()
               if line.strip()]  # fmt: skip
    print(render(records))


if __name__ == "__main__":
    main()
