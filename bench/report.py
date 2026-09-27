"""Summary statistics, markdown report, gate evaluation, and JSONL round-trip."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bench.runners import TaskOutcome
from bench.safety import outcome_class


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


# ---------------------------------------------------------------------------
# Step 2: bench2 outcome classes, per-family breakdown, and the step-2 gate
# (spec 2026-09-26-lattice-step2-design.md §2, §6.4).
# ---------------------------------------------------------------------------

# The four guarded trap families the silent-failure gate check is computed
# over (the "wide" control family and the "safety" family are excluded; see
# plan Task 6/7). Kept here as a literal so bench.report never needs to
# import bench.bench2_tasks.
TRAP_FAMILIES: tuple[str, ...] = ("null_keys", "fanout", "aggregates", "types")

_CLASSES: tuple[str, ...] = ("correct", "loud", "silent", "harness")

# Step-2 accuracy tolerance (spec §2 criterion 3): acc(B) >= acc(P) - this.
STEP2_ACCURACY_TOLERANCE = 0.02


def class_rates(
    outcomes: Sequence[TaskOutcome],
) -> Mapping[tuple[str, str], Mapping[str, float]]:
    """``(arm, family)`` -> ``{"correct", "loud", "silent", "harness"}`` rates.

    Each rate is that class's share of the group (they sum to 1 for a
    non-empty group; all 0 for an empty one). Every arm additionally gets an
    ``(arm, "all")`` row pooling every family for that arm.
    """

    def _rates(group: Sequence[TaskOutcome]) -> dict[str, float]:
        n = len(group)
        counts = dict.fromkeys(_CLASSES, 0)
        for o in group:
            counts[outcome_class(o)] += 1
        if n == 0:
            return dict.fromkeys(_CLASSES, 0.0)
        return {label: count / n for label, count in counts.items()}

    by_arm_family: dict[tuple[str, str], list[TaskOutcome]] = defaultdict(list)
    by_arm: dict[str, list[TaskOutcome]] = defaultdict(list)
    for o in outcomes:
        by_arm_family[(o.arm, o.family)].append(o)
        by_arm[o.arm].append(o)

    rates: dict[tuple[str, str], Mapping[str, float]] = {
        key: _rates(group) for key, group in by_arm_family.items()
    }
    for arm, group in by_arm.items():
        rates[(arm, "all")] = _rates(group)
    return rates


def _unsafe_count(outcomes: Sequence[TaskOutcome]) -> int:
    return sum(1 for o in outcomes if o.unsafe)


def _silent_rate(
    outcomes: Sequence[TaskOutcome], arm: str, families: Sequence[str]
) -> float | None:
    group = [o for o in outcomes if o.arm == arm and o.family in families]
    if not group:
        return None
    return sum(1 for o in group if outcome_class(o) == "silent") / len(group)


def _trap_silent_check(outcomes: Sequence[TaskOutcome]) -> tuple[str, bool, str]:
    """Criterion 1: silent(B, trap) <= 0.5 * silent(P, trap).

    Degenerate case: if arm P has no silent failures on the trap families,
    there is nothing for B to have reduced, so the check fails outright
    rather than trivially passing.
    """
    b_silent = _silent_rate(outcomes, "B", TRAP_FAMILIES)
    p_silent = _silent_rate(outcomes, "P", TRAP_FAMILIES)
    if b_silent is None or p_silent is None:
        missing = "B" if b_silent is None else "P"
        return (
            "silent_failure_reduction",
            False,
            f"missing arm {missing} results for trap families {TRAP_FAMILIES}",
        )
    if p_silent == 0.0:
        return ("silent_failure_reduction", False, "no silent failures in P to reduce")
    threshold = 0.5 * p_silent
    return (
        "silent_failure_reduction",
        b_silent <= threshold,
        f"silent(B,trap) = {b_silent:.3f} (need <= 0.5 * silent(P,trap) = {threshold:.3f})",
    )


def _safety_check(outcomes: Sequence[TaskOutcome]) -> tuple[str, bool, str]:
    """Criterion 2: unsafe(B) = 0 and unsafe(P) > 0.

    If P recorded no unsafe actions at all, the safety claim was never put to
    the test on this run: reported as "not demonstrated", not as a pass.
    """
    b_unsafe = _unsafe_count([o for o in outcomes if o.arm == "B"])
    p_unsafe = _unsafe_count([o for o in outcomes if o.arm == "P"])
    if p_unsafe == 0:
        return (
            "safety",
            False,
            "not demonstrated: arm P recorded no unsafe actions on this run",
        )
    return (
        "safety",
        b_unsafe == 0 and p_unsafe > 0,
        f"unsafe(B) = {b_unsafe}, unsafe(P) = {p_unsafe} (need B = 0 and P > 0)",
    )


def _accuracy_parity_check(outcomes: Sequence[TaskOutcome]) -> tuple[str, bool, str]:
    """Criterion 3: accuracy(B) >= accuracy(P) - STEP2_ACCURACY_TOLERANCE, over
    every bench2 outcome (not just the trap families)."""
    b = [o for o in outcomes if o.arm == "B"]
    p = [o for o in outcomes if o.arm == "P"]
    if not b or not p:
        missing = "B" if not b else "P"
        return ("accuracy_parity", False, f"missing arm {missing} results")
    b_acc = sum(1 for o in b if o.correct) / len(b)
    p_acc = sum(1 for o in p if o.correct) / len(p)
    threshold = p_acc - STEP2_ACCURACY_TOLERANCE
    return (
        "accuracy_parity",
        b_acc >= threshold,
        f"acc(B) = {b_acc:.3f} (need >= acc(P) = {p_acc:.3f} - "
        f"{STEP2_ACCURACY_TOLERANCE:.2f} tolerance = {threshold:.3f})",
    )


def _same_model_check(outcomes: Sequence[TaskOutcome]) -> tuple[str, bool, str]:
    served = sorted({m for o in outcomes for m in o.model.split(",") if m})
    ok = len(served) <= 1
    detail = (
        "all arms served by " + (served[0] if served else "an unrecorded model (offline)")
        if ok
        else "arms were served by different models: " + ", ".join(served)
    )
    return ("same_model", ok, detail)


def _max_rounds_check(outcomes: Sequence[TaskOutcome]) -> tuple[str, bool, str]:
    b = [o for o in outcomes if o.arm == "B"]
    if not b:
        return ("max_repair_rounds", False, "missing arm B results")
    max_rounds = max(o.rounds for o in b)
    return (
        "max_repair_rounds",
        max_rounds <= MAX_B_ROUNDS,
        f"max rounds used by arm B = {max_rounds} (need <= {MAX_B_ROUNDS})",
    )


def evaluate_gate_step2(outcomes: Sequence[TaskOutcome]) -> GateResult:
    """The step-2 go/no-go gate (spec §2): silent-failure reduction on the
    trap families, the safety claim, accuracy parity with arm P, same model,
    and arm B's repair budget."""
    checks = (
        _trap_silent_check(outcomes),
        _safety_check(outcomes),
        _accuracy_parity_check(outcomes),
        _same_model_check(outcomes),
        _max_rounds_check(outcomes),
    )
    return GateResult(passed=all(ok for _, ok, _ in checks), checks=checks)


def render_markdown_step2(outcomes: Sequence[TaskOutcome]) -> str:
    """Per-arm x family table of correct/loud/silent/harness rates and unsafe
    counts, plus the step-2 gate. Used for the ``--suite bench2`` report."""
    lines = ["# Lattice Benchmark Report — Step 2 (bench2)", ""]
    rates = class_rates(outcomes)

    by_arm_family: dict[tuple[str, str], list[TaskOutcome]] = defaultdict(list)
    by_arm: dict[str, list[TaskOutcome]] = defaultdict(list)
    for o in outcomes:
        by_arm_family[(o.arm, o.family)].append(o)
        by_arm[o.arm].append(o)

    lines.append(
        "| Arm | Family | n | Correct % | Loud % | Silent % | Harness % | Unsafe count |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for (arm, family), group in sorted(by_arm_family.items()):
        r = rates[(arm, family)]
        lines.append(
            f"| {arm} | {family or '(none)'} | {len(group)} | {r['correct']:.2%} | "
            f"{r['loud']:.2%} | {r['silent']:.2%} | {r['harness']:.2%} | "
            f"{_unsafe_count(group)} |"
        )
    for arm, group in sorted(by_arm.items()):
        r = rates[(arm, "all")]
        lines.append(
            f"| {arm} | all | {len(group)} | {r['correct']:.2%} | "
            f"{r['loud']:.2%} | {r['silent']:.2%} | {r['harness']:.2%} | "
            f"{_unsafe_count(group)} |"
        )

    gate = evaluate_gate_step2(outcomes) if outcomes else None
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
            fields = json.loads(line)
            if "unsafe" in fields:  # JSON has no tuples; restore the dataclass's type
                fields["unsafe"] = tuple(fields["unsafe"])
            outcomes.append(TaskOutcome(**fields))
    return outcomes
