"""Summary statistics, markdown report, gate evaluation, and JSONL round-trip."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
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
    max_rounds: int = 0


@dataclass(frozen=True)
class Summary:
    stats: Mapping[tuple[str, str], ArmTierStats]
    failures: Mapping[str, Mapping[str, int]]
    # arm -> sorted distinct models that actually served its requests
    models: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    checks: tuple[tuple[str, bool, str], ...]
    notes: tuple[str, ...] = ()  # informational lines; never affect `passed`


# Documented tolerance: arm B may trail arm A on single-step tasks by at most
# this much accuracy (sampling noise on a small tier), and still pass the gate.
SINGLE_TIER_TOLERANCE = 0.02
# Arm B may use at most 2 repairs, i.e. at most 3 generation rounds per task.
MAX_B_ROUNDS = 3
# Tiers pooled for the gate's multi-step check: the 3-6 step "multi" tier plus
# the 5-8 step "deep" tier (added because a strong baseline saturates "multi").
MULTI_STEP_TIERS: tuple[str, ...] = ("multi", "deep")


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
    # Lattice arms: a task that never produced a checked verdict (harness error,
    # refusal) counts against the pass rate instead of dropping out of it.
    checked = [o for o in outcomes if o.check_passed is not None or o.arm != "A"]
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
    max_rounds = max(o.rounds for o in outcomes)

    return ArmTierStats(
        n=n,
        accuracy=accuracy,
        first_shot_pass=first_shot_pass,
        check_pass_rate=check_pass_rate,
        mean_rounds=mean_rounds,
        mean_output_tokens=mean_output_tokens,
        mean_latency_s=mean_latency_s,
        max_rounds=max_rounds,
    )


def summarize(outcomes: Sequence[TaskOutcome]) -> Summary:
    by_arm_tier: dict[tuple[str, str], list[TaskOutcome]] = defaultdict(list)
    by_arm: dict[str, list[TaskOutcome]] = defaultdict(list)
    failures: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    models: dict[str, set[str]] = defaultdict(set)

    for outcome in outcomes:
        by_arm_tier[(outcome.arm, outcome.tier)].append(outcome)
        by_arm[outcome.arm].append(outcome)
        models[outcome.arm].update(m for m in outcome.model.split(",") if m)
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
        models={arm: tuple(sorted(names)) for arm, names in models.items() if names},
    )


def pooled_accuracy(
    stats: Mapping[tuple[str, str], ArmTierStats], arm: str, tiers: Sequence[str]
) -> tuple[float, int] | None:
    """Accuracy of `arm` over the union of `tiers` (n-weighted), with the pooled n;
    None when the arm has no outcomes in any of those tiers."""
    parts = [stats[(arm, tier)] for tier in tiers if (arm, tier) in stats]
    n = sum(part.n for part in parts)
    if n == 0:
        return None
    return sum(part.accuracy * part.n for part in parts) / n, n


def evaluate_gate(summary: Summary) -> GateResult:
    """Phase-3 go/no-go gate (thresholds finalized in Task 24).

    Checks: (1) multi-step accuracy -- pooled over the "multi" and "deep" tiers
    (``MULTI_STEP_TIERS``) -- improves by >= 0.15 over the baseline;
    (2) single-step accuracy does not regress by more than the documented
    ``SINGLE_TIER_TOLERANCE`` (0.02); (3) at least 99% of arm B's programs
    ultimately pass Lattice's static checks; (4) arm B never used more than
    2 repairs (max rounds <= 3); (5) every arm was served by the same model.
    The deep-tier delta alone and the join-tier delta are reported as
    informational notes only.
    Missing arms are handled gracefully: the affected check fails with a
    detail naming which arm's results are absent.
    """
    stats = summary.stats
    checks: list[tuple[str, bool, str]] = []

    tiers_label = "+".join(MULTI_STEP_TIERS)
    a_multi = pooled_accuracy(stats, "A", MULTI_STEP_TIERS)
    b_multi = pooled_accuracy(stats, "B", MULTI_STEP_TIERS)
    if a_multi is None or b_multi is None:
        missing = "A" if a_multi is None else "B"
        checks.append(
            (
                "multi_step_improvement",
                False,
                f"missing arm {missing} results for tiers "
                + "/".join(f"'{t}'" for t in MULTI_STEP_TIERS),
            )
        )
    else:
        (a_acc, a_n), (b_acc, b_n) = a_multi, b_multi
        delta = b_acc - a_acc
        checks.append(
            (
                "multi_step_improvement",
                delta >= 0.15,
                f"acc(B,{tiers_label}) - acc(A,{tiers_label}) = {delta:.3f} (need >= 0.15; "
                f"n_A={a_n}, n_B={b_n})",
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
        threshold = a_single.accuracy - SINGLE_TIER_TOLERANCE
        checks.append(
            (
                "single_step_no_regression",
                b_single.accuracy >= threshold,
                f"acc(B,single) = {b_single.accuracy:.3f} (need >= acc(A,single) - "
                f"{SINGLE_TIER_TOLERANCE:.2f} tolerance = {threshold:.3f})",
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

    if b_all is None:
        checks.append(("max_repair_rounds", False, "missing arm B results"))
    else:
        checks.append(
            (
                "max_repair_rounds",
                b_all.max_rounds <= MAX_B_ROUNDS,
                f"max rounds used by arm B = {b_all.max_rounds} "
                f"(need <= {MAX_B_ROUNDS}, i.e. <= {MAX_B_ROUNDS - 1} repairs)",
            )
        )

    served = sorted({m for names in summary.models.values() for m in names})
    checks.append(
        (
            "same_model",
            len(served) <= 1,
            "all arms served by " + (served[0] if served else "an unrecorded model (offline)")
            if len(served) <= 1
            else "arms were served by different models: "
            + "; ".join(f"{arm}: {', '.join(n)}" for arm, n in sorted(summary.models.items())),
        )
    )

    notes: list[str] = []
    a_deep = stats.get(("A", "deep"))
    b_deep = stats.get(("B", "deep"))
    if a_deep is not None and b_deep is not None:
        notes.append(
            f"deep tier (informational): acc(B,deep) - acc(A,deep) = "
            f"{b_deep.accuracy - a_deep.accuracy:.3f}"
        )
    a_join = stats.get(("A", "join"))
    b_join = stats.get(("B", "join"))
    if a_join is not None and b_join is not None:
        notes.append(
            f"join tier (informational): acc(B,join) - acc(A,join) = "
            f"{b_join.accuracy - a_join.accuracy:.3f}"
        )

    passed = all(ok for _, ok, _ in checks)
    return GateResult(passed=passed, checks=tuple(checks), notes=tuple(notes))


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

    if summary.models:
        lines.append("")
        lines.append("## Served models")
        lines.append("")
        for arm in sorted(summary.models):
            lines.append(f"- **{arm}**: {', '.join(summary.models[arm])}")

    if gate is not None:
        lines.append("")
        lines.append("## Gate")
        lines.append("")
        lines.append(f"**Result: {'PASS' if gate.passed else 'FAIL'}**")
        lines.append("")
        lines.append(
            f"Single-step tolerance: arm B may trail arm A by at most "
            f"{SINGLE_TIER_TOLERANCE:.2f} accuracy on the single tier. Arm B may use "
            f"at most {MAX_B_ROUNDS - 1} repairs ({MAX_B_ROUNDS} rounds) per task. "
            f"The multi-step check pools the {' and '.join(MULTI_STEP_TIERS)} tiers."
        )
        lines.append("")
        for name, ok, detail in gate.checks:
            mark = "PASS" if ok else "FAIL"
            lines.append(f"- [{mark}] `{name}`: {detail}")
        for note in gate.notes:
            lines.append(f"- [INFO] {note}")

    return "\n".join(lines) + "\n"


def write_outcomes(path: Path, outcomes: Sequence[TaskOutcome]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for outcome in outcomes:
            f.write(json.dumps(asdict(outcome)) + "\n")


def append_outcome(path: Path, outcome: TaskOutcome) -> None:
    """Append one outcome as a JSONL line and flush it to disk immediately."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(asdict(outcome)) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_outcomes(path: Path) -> list[TaskOutcome]:
    outcomes: list[TaskOutcome] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            outcomes.append(TaskOutcome(**json.loads(line)))
    return outcomes
