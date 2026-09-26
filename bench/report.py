"""Summary statistics, markdown report, gate evaluation, and JSONL round-trip."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from bench.runners import TaskOutcome


@dataclass(frozen=True)
class ArmTierStats:
    n: int
    accuracy: float
    first_shot_pass: float | None
    check_pass_rate: float | None
    mean_rounds: float
    mean_output_tokens: float
    mean_latency_s: float


@dataclass(frozen=True)
class Summary:
    stats: Mapping[tuple[str, str], ArmTierStats]
    failures: Mapping[str, Mapping[str, int]]


@dataclass(frozen=True)
class GateResult:
    passed: bool
    checks: tuple[tuple[str, bool, str], ...]


def _stats_for(outcomes: Sequence[TaskOutcome]) -> ArmTierStats:
    n = len(outcomes)
    if n == 0:
        return ArmTierStats(
            n=0,
            accuracy=0.0,
            first_shot_pass=None,
            check_pass_rate=None,
            mean_rounds=0.0,
            mean_output_tokens=0.0,
            mean_latency_s=0.0,
        )

    accuracy = sum(1 for o in outcomes if o.correct) / n

    # `check_passed` is None for arm A (no static checker runs there); for
    # arms B/C it records whether the program ultimately passed Lattice's
    # checks (possibly after repairs). `first_shot_pass` is the share that
    # passed on round 1 (no repair needed); `check_pass_rate` is the share
    # that passed at all, in any round.
    checked = [o for o in outcomes if o.check_passed is not None]
    if checked:
        first_shot_pass = sum(1 for o in checked if o.check_passed and o.rounds == 1) / len(
            checked
        )
        check_pass_rate = sum(1 for o in checked if o.check_passed) / len(checked)
    else:
        first_shot_pass = None
        check_pass_rate = None

    mean_rounds = sum(o.rounds for o in outcomes) / n
    mean_output_tokens = sum(o.output_tokens for o in outcomes) / n
    mean_latency_s = sum(o.latency_s for o in outcomes) / n

    return ArmTierStats(
        n=n,
        accuracy=accuracy,
        first_shot_pass=first_shot_pass,
        check_pass_rate=check_pass_rate,
        mean_rounds=mean_rounds,
        mean_output_tokens=mean_output_tokens,
        mean_latency_s=mean_latency_s,
    )


def summarize(outcomes: Sequence[TaskOutcome]) -> Summary:
    by_arm_tier: dict[tuple[str, str], list[TaskOutcome]] = defaultdict(list)
    by_arm: dict[str, list[TaskOutcome]] = defaultdict(list)
    failures: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for outcome in outcomes:
        by_arm_tier[(outcome.arm, outcome.tier)].append(outcome)
        by_arm[outcome.arm].append(outcome)
        if outcome.failure is not None:
            failures[outcome.arm][outcome.failure] += 1

    stats: dict[tuple[str, str], ArmTierStats] = {
        key: _stats_for(group) for key, group in by_arm_tier.items()
    }
    for arm, group in by_arm.items():
        stats[(arm, "all")] = _stats_for(group)

    return Summary(
        stats=stats,
        failures={arm: dict(labels) for arm, labels in failures.items()},
    )


def evaluate_gate(summary: Summary) -> GateResult:
    """Phase-3 go/no-go gate (thresholds finalized in Task 24).

    Checks: (1) multi-step accuracy improves by >= 0.15 over the baseline;
    (2) single-step accuracy does not regress by more than 0.02; (3) at
    least 99% of arm B's programs ultimately pass Lattice's static checks.
    Missing arms are handled gracefully: the affected check fails with a
    detail naming which arm's results are absent.
    """
    stats = summary.stats
    checks: list[tuple[str, bool, str]] = []

    a_multi = stats.get(("A", "multi"))
    b_multi = stats.get(("B", "multi"))
    if a_multi is None or b_multi is None:
        missing = "A" if a_multi is None else "B"
        checks.append(
            ("multi_step_improvement", False, f"missing arm {missing} results for tier 'multi'")
        )
    else:
        delta = b_multi.accuracy - a_multi.accuracy
        checks.append(
            (
                "multi_step_improvement",
                delta >= 0.15,
                f"acc(B,multi) - acc(A,multi) = {delta:.3f} (need >= 0.15)",
            )
        )

    a_single = stats.get(("A", "single"))
    b_single = stats.get(("B", "single"))
    if a_single is None or b_single is None:
        missing = "A" if a_single is None else "B"
        checks.append(
            (
                "single_step_no_regression",
                False,
                f"missing arm {missing} results for tier 'single'",
            )
        )
    else:
        threshold = a_single.accuracy - 0.02
        checks.append(
            (
                "single_step_no_regression",
                b_single.accuracy >= threshold,
                f"acc(B,single) = {b_single.accuracy:.3f} (need >= {threshold:.3f})",
            )
        )

    b_all = stats.get(("B", "all"))
    if b_all is None:
        checks.append(("check_pass_rate", False, "missing arm B results"))
    elif b_all.check_pass_rate is None:
        checks.append(("check_pass_rate", False, "arm B has no check_passed data"))
    else:
        checks.append(
            (
                "check_pass_rate",
                b_all.check_pass_rate >= 0.99,
                f"share of B outcomes with check_passed = {b_all.check_pass_rate:.3f}"
                " (need >= 0.99)",
            )
        )

    passed = all(ok for _, ok, _ in checks)
    return GateResult(passed=passed, checks=tuple(checks))


def render_markdown(summary: Summary, gate: GateResult | None) -> str:
    lines = ["# Lattice Benchmark Report", ""]
    lines.append(
        "| Arm | Tier | n | Accuracy | First-shot pass | Check pass rate | Mean rounds "
        "| Mean output tokens | Mean latency (s) |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for (arm, tier), stat in sorted(summary.stats.items()):
        fsp = "-" if stat.first_shot_pass is None else f"{stat.first_shot_pass:.2%}"
        cpr = "-" if stat.check_pass_rate is None else f"{stat.check_pass_rate:.2%}"
        lines.append(
            f"| {arm} | {tier} | {stat.n} | {stat.accuracy:.2%} | {fsp} | {cpr} | "
            f"{stat.mean_rounds:.2f} | {stat.mean_output_tokens:.1f} | {stat.mean_latency_s:.2f} |"
        )

    lines.append("")
    lines.append("## Failure taxonomy")
    lines.append("")
    if summary.failures:
        for arm in sorted(summary.failures):
            lines.append(f"- **{arm}**")
            for label, count in sorted(summary.failures[arm].items()):
                lines.append(f"  - {label}: {count}")
    else:
        lines.append("(no failures recorded)")

    if gate is not None:
        lines.append("")
        lines.append("## Gate")
        lines.append("")
        lines.append(f"**Result: {'PASS' if gate.passed else 'FAIL'}**")
        lines.append("")
        for name, ok, detail in gate.checks:
            mark = "PASS" if ok else "FAIL"
            lines.append(f"- [{mark}] `{name}`: {detail}")

    return "\n".join(lines) + "\n"


def write_outcomes(path: Path, outcomes: Sequence[TaskOutcome]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for outcome in outcomes:
            f.write(json.dumps(asdict(outcome)) + "\n")


def read_outcomes(path: Path) -> list[TaskOutcome]:
    outcomes: list[TaskOutcome] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            outcomes.append(TaskOutcome(**json.loads(line)))
    return outcomes
