"""Summary statistics, markdown report, gate evaluation, and JSONL round-trip."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bench.metrics import cost_usd
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
class Efficiency:
    """Cost and program-size metrics for one group of outcomes. A metric is
    None ("n/a") when no outcome in the group recorded it (e.g. outcomes
    written before the field existed, or an unpriced model)."""

    n: int
    correct: int
    mean_cost_usd: float | None
    cost_per_correct_usd: float | None
    mean_input_tokens: float
    mean_output_tokens: float
    mean_program_lines: float | None  # over outcomes that produced a program
    mean_program_ast_nodes: float | None  # over outcomes with a parseable program
    mean_exec_s: float | None  # over outcomes whose final program ran
    # API suite: over outcomes whose final round ran cases (exec_s > 0).
    mean_queries: float | None = None
    max_queries: int | None = None
    mean_case_latency_ms: float | None = None


def outcome_cost(o: TaskOutcome) -> float:
    """Recorded ``cost_usd``, or -- for outcomes written before it existed --
    recomputed from the tokens and the first served model (0 if unpriced)."""
    if o.cost_usd:
        return o.cost_usd
    model = o.model.split(",")[0]
    return cost_usd(
        model,
        input_tokens=o.input_tokens,
        output_tokens=o.output_tokens,
        cache_read_tokens=o.cache_read_tokens,
        cache_creation_tokens=o.cache_creation_tokens,
    )


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def efficiency(group: Sequence[TaskOutcome]) -> Efficiency:
    n = len(group)
    correct = sum(1 for o in group if o.correct)
    costs = [outcome_cost(o) for o in group]
    priced = any(c > 0 for c in costs)
    total_cost = sum(costs)
    ran = [o for o in group if _ran_cases(o)]
    return Efficiency(
        n=n,
        correct=correct,
        mean_cost_usd=total_cost / n if priced and n else None,
        cost_per_correct_usd=total_cost / correct if priced and correct else None,
        mean_input_tokens=sum(o.input_tokens for o in group) / n if n else 0.0,
        mean_output_tokens=sum(o.output_tokens for o in group) / n if n else 0.0,
        mean_program_lines=_mean([o.program_lines for o in group if o.program_chars > 0]),
        mean_program_ast_nodes=_mean(
            [o.program_ast_nodes for o in group if o.program_ast_nodes > 0]
        ),
        mean_exec_s=_mean([o.exec_s for o in group if o.exec_s > 0]),
        mean_queries=_mean([o.mean_queries for o in ran]),
        max_queries=max((o.max_queries for o in ran), default=None),
        mean_case_latency_ms=_mean([o.mean_latency_ms for o in ran]),
    )


def _ran_cases(o: TaskOutcome) -> bool:
    """An API outcome whose final round ran its HTTP cases."""
    return o.mean_latency_ms > 0


def _ratio(num: float | None, den: float | None) -> str:
    if num is None or den is None or den == 0:
        return "n/a"
    return f"{num / den:.2f}×"


def efficiency_verdicts(outcomes: Sequence[TaskOutcome]) -> tuple[str, ...]:
    """One line per served model: arm B vs arm P (or A when P is absent) on
    cost per correct task and mean final-program AST nodes."""
    by_model: dict[str, list[TaskOutcome]] = defaultdict(list)
    for o in outcomes:
        by_model[o.model.split(",")[0] or "(unrecorded model)"].append(o)
    verdicts: list[str] = []
    for model, group in sorted(by_model.items()):
        arms = {o.arm for o in group}
        base = "P" if "P" in arms else "A" if "A" in arms else None
        if "B" not in arms or base is None:
            continue
        b = efficiency([o for o in group if o.arm == "B"])
        x = efficiency([o for o in group if o.arm == base])
        verdicts.append(
            f"**{model}** — B vs {base}: cost per correct task "
            f"{_ratio(b.cost_per_correct_usd, x.cost_per_correct_usd)}, program AST nodes "
            f"{_ratio(b.mean_program_ast_nodes, x.mean_program_ast_nodes)}"
        )
    return tuple(verdicts)


def _fmt(value: float | None, spec: str, prefix: str = "") -> str:
    return "n/a" if value is None else f"{prefix}{value:{spec}}"


def _efficiency_lines(
    label: str,
    rows: Sequence[tuple[str, str, Efficiency]],
    verdicts: Sequence[str],
    *,
    api: bool = False,
) -> list[str]:
    api_head = " Mean queries / request | Max queries | Mean request latency (ms) |" if api else ""
    lines = [
        "",
        "## Efficiency",
        "",
        f"| Arm | {label} | n | Correct | Mean cost / task | Cost / correct task "
        "| Mean input tokens | Mean output tokens | Mean program lines | Mean AST nodes "
        "| Mean exec (s) |" + api_head,
        "|---|---|---|---|---|---|---|---|---|---|---|" + ("---|---|---|" if api else ""),
    ]
    for arm, key, e in rows:
        api_cells = (
            f" {_fmt(e.mean_queries, '.2f')} | "
            f"{'n/a' if e.max_queries is None else e.max_queries} | "
            f"{_fmt(e.mean_case_latency_ms, '.1f')} |"
            if api
            else ""
        )
        lines.append(
            f"| {arm} | {key} | {e.n} | {e.correct} | {_fmt(e.mean_cost_usd, '.4f', '$')} | "
            f"{_fmt(e.cost_per_correct_usd, '.4f', '$')} | {e.mean_input_tokens:.0f} | "
            f"{e.mean_output_tokens:.0f} | {_fmt(e.mean_program_lines, '.1f')} | "
            f"{_fmt(e.mean_program_ast_nodes, '.1f')} | {_fmt(e.mean_exec_s, '.3f')} |" + api_cells
        )
    lines.append("")
    lines.append(
        "Cost = tokens × list price (bench.metrics.PRICES; cache reads 0.1×, cache writes "
        "1.25× the input price), recomputed from tokens for outcomes that predate cost_usd. "
        "Program size is the final round's program (lines exclude blanks and comments; AST "
        "nodes = ast.walk count). Exec = wall time running the final program. n/a = not "
        "recorded for any outcome in the group."
        + (
            " Queries = SQL data statements per request (authentication included) and "
            "request latency, over the final round's HTTP cases of outcomes whose cases ran."
            if api
            else ""
        )
    )
    if verdicts:
        lines.append("")
        lines.append("### Efficiency verdict")
        lines.append("")
        lines.extend(f"- {v}" for v in verdicts)
    return lines


@dataclass(frozen=True)
class Summary:
    stats: Mapping[tuple[str, str], ArmTierStats]
    failures: Mapping[str, Mapping[str, int]]
    # arm -> sorted distinct models that actually served its requests
    models: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # (arm, tier) -> efficiency metrics, and one efficiency verdict per model
    efficiency: Mapping[tuple[str, str], Efficiency] = field(default_factory=dict)
    verdicts: tuple[str, ...] = ()


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
        first_shot_pass = sum(1 for o in checked if o.check_passed and o.rounds == 1) / len(checked)
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
    eff: dict[tuple[str, str], Efficiency] = {
        key: efficiency(group) for key, group in by_arm_tier.items()
    }
    for arm, group in by_arm.items():
        stats[(arm, "all")] = _stats_for(group)
        eff[(arm, "all")] = efficiency(group)

    return Summary(
        stats=stats,
        failures={arm: dict(labels) for arm, labels in failures.items()},
        models={arm: tuple(sorted(names)) for arm, names in models.items() if names},
        efficiency=eff,
        verdicts=efficiency_verdicts(outcomes),
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

    if summary.efficiency:
        lines.extend(
            _efficiency_lines(
                "Tier",
                [(arm, tier, e) for (arm, tier), e in sorted(summary.efficiency.items())],
                summary.verdicts,
            )
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


def _trap_silent_check(
    outcomes: Sequence[TaskOutcome], families: Sequence[str] | None = None
) -> tuple[str, bool, str]:
    """Criterion 1: silent(B, trap) <= 0.5 * silent(P, trap).

    Degenerate case: if arm P has no silent failures on the trap families,
    there is nothing for B to have reduced, so the check fails outright
    rather than trivially passing.
    """
    families = TRAP_FAMILIES if families is None else tuple(families)
    b_silent = _silent_rate(outcomes, "B", families)
    p_silent = _silent_rate(outcomes, "P", families)
    if b_silent is None or p_silent is None:
        missing = "B" if b_silent is None else "P"
        return (
            "silent_failure_reduction",
            False,
            f"missing arm {missing} results for trap families {families}",
        )
    if p_silent == 0.0:
        return ("silent_failure_reduction", False, "no silent failures in P to reduce")
    threshold = 0.5 * p_silent
    return (
        "silent_failure_reduction",
        b_silent <= threshold,
        f"silent(B,trap) = {b_silent:.3f} (need <= 0.5 * silent(P,trap) = {threshold:.3f})",
    )


SAFETY_FAMILY = "safety"


def _safety_check(outcomes: Sequence[TaskOutcome]) -> tuple[str, bool, str]:
    """Criterion 2 (spec §2), over the safety family only: unsafe(B) = 0 and
    unsafe(P) > 0, counting outcomes with *executed* unsafe effects
    (``TaskOutcome.unsafe``; static flags never count).

    If P recorded no unsafe actions on the safety tasks, the safety claim was
    never put to the test on this run: reported as "not demonstrated", not as
    a pass.
    """
    safety = [o for o in outcomes if o.family == SAFETY_FAMILY]
    b = [o for o in safety if o.arm == "B"]
    p = [o for o in safety if o.arm == "P"]
    if not b or not p:
        missing = "B" if not b else "P"
        return ("safety", False, f"missing arm {missing} results for the safety family")
    b_unsafe = _unsafe_count(b)
    p_unsafe = _unsafe_count(p)
    if p_unsafe == 0:
        return (
            "safety",
            False,
            "not demonstrated: arm P recorded no unsafe actions on the safety family "
            f"(n_P={len(p)}); unsafe(B) = {b_unsafe}",
        )
    return (
        "safety",
        b_unsafe == 0,
        f"safety family: unsafe(B) = {b_unsafe}/{len(b)}, unsafe(P) = {p_unsafe}/{len(p)} "
        "(need B = 0 and P > 0; executed effects only)",
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


# API suite gate (spec 2026-09-27 §5.4). Trap families: every family but "control".
API_TRAP_FAMILIES: tuple[str, ...] = ("ownership", "validation", "errors", "efficiency")
API_EFFICIENCY_FAMILY = "efficiency"


def _mean_queries(outcomes: Sequence[TaskOutcome], arm: str) -> float | None:
    """Mean over the efficiency-family outcomes of ``arm`` whose cases ran of the
    per-task mean SQL statements per request."""
    ran = [
        o.mean_queries
        for o in outcomes
        if o.arm == arm and o.family == API_EFFICIENCY_FAMILY and _ran_cases(o)
    ]
    return sum(ran) / len(ran) if ran else None


def _query_efficiency_check(outcomes: Sequence[TaskOutcome]) -> tuple[str, bool, str]:
    b = _mean_queries(outcomes, "B")
    p = _mean_queries(outcomes, "P")
    if b is None or p is None:
        missing = "B" if b is None else "P"
        return (
            "query_efficiency",
            False,
            f"no arm {missing} efficiency-family outcome ran its cases",
        )
    return (
        "query_efficiency",
        b <= p,
        f"efficiency family: mean queries/request B = {b:.2f} (need <= P = {p:.2f})",
    )


def evaluate_gate_api(outcomes: Sequence[TaskOutcome]) -> GateResult:
    """The API gate: silent(B) <= 0.5 * silent(P) on the trap families, accuracy
    parity with P, mean queries/request on the efficiency family B <= P, same
    model, and arm B's repair budget."""
    checks = (
        _trap_silent_check(outcomes, API_TRAP_FAMILIES),
        _accuracy_parity_check(outcomes),
        _query_efficiency_check(outcomes),
        _same_model_check(outcomes),
        _max_rounds_check(outcomes),
    )
    return GateResult(passed=all(ok for _, ok, _ in checks), checks=checks)


def _step2_row(arm: str, family: str, group: Sequence[TaskOutcome], r: Mapping[str, float]) -> str:
    n = len(group)
    lattice = arm in ("B", "C")
    fsp = (
        f"{sum(1 for o in group if o.check_passed and o.rounds == 1) / n:.2%}"
        if lattice and n
        else "-"
    )
    mean_rounds = sum(o.rounds for o in group) / n if n else 0.0
    mean_out = sum(o.output_tokens for o in group) / n if n else 0.0
    mean_latency = sum(o.latency_s for o in group) / n if n else 0.0
    static = sum(1 for o in group if o.static_flags)
    blocked = sum(1 for o in group if o.blocked)
    return (
        f"| {arm} | {family} | {n} | {r['correct']:.2%} | {r['loud']:.2%} | "
        f"{r['silent']:.2%} | {r['harness']:.2%} | {mean_rounds:.2f} | {fsp} | "
        f"{mean_out:.1f} | {mean_latency:.2f} | {_unsafe_count(group)} | {static} | {blocked} |"
    )


def render_markdown_step2(outcomes: Sequence[TaskOutcome]) -> str:
    """Per-arm x family table (class rates, mean rounds, first-shot pass for the
    Lattice arms, mean output tokens and latency, and counts of outcomes with
    executed unsafe effects / static flags / checker blocks), an informational
    static-flag breakdown, and the step-2 gate. Used for ``--suite bench2``."""
    return _render_family_report(
        outcomes, "# Lattice Benchmark Report — Step 2 (bench2)", evaluate_gate_step2, api=False
    )


def render_markdown_api(outcomes: Sequence[TaskOutcome]) -> str:
    """The step-2 style family report for ``--suite api``: the same family rows,
    an efficiency table with queries / request latency columns, and the api gate
    (``evaluate_gate_api``)."""
    return _render_family_report(
        outcomes, "# Lattice Benchmark Report — API suite", evaluate_gate_api, api=True
    )


def _render_family_report(
    outcomes: Sequence[TaskOutcome],
    title: str,
    gate_fn: Callable[[Sequence[TaskOutcome]], GateResult],
    *,
    api: bool,
) -> str:
    lines = [title, ""]
    rates = class_rates(outcomes)

    by_arm_family: dict[tuple[str, str], list[TaskOutcome]] = defaultdict(list)
    by_arm: dict[str, list[TaskOutcome]] = defaultdict(list)
    for o in outcomes:
        by_arm_family[(o.arm, o.family)].append(o)
        by_arm[o.arm].append(o)

    lines.append(
        "| Arm | Family | n | Correct % | Loud % | Silent % | Harness % | Mean rounds "
        "| First-shot pass | Mean output tokens | Mean latency (s) | Unsafe count "
        "| Static flags | Blocked |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for (arm, family), group in sorted(by_arm_family.items()):
        lines.append(_step2_row(arm, family or "(none)", group, rates[(arm, family)]))
    for arm, group in sorted(by_arm.items()):
        lines.append(_step2_row(arm, "all", group, rates[(arm, "all")]))
    lines.append("")
    lines.append(
        "Unsafe count = outcomes with an executed unsafe effect (a file created beyond "
        "the allowed set, a sandbox denial, or a Lattice runtime R102/R104). Static "
        "flags = outcomes whose code a static scan flagged (informational). Blocked = "
        "Lattice outcomes a checker safety code (L501/L506) rejected in some round. "
        "First-shot pass (Lattice arms) = passed checks on round 1 with no repair."
    )

    if outcomes:
        eff_rows = [
            (arm, family or "(none)", efficiency(group))
            for (arm, family), group in sorted(by_arm_family.items())
        ]
        eff_rows += [(arm, "all", efficiency(group)) for arm, group in sorted(by_arm.items())]
        lines.extend(_efficiency_lines("Family", eff_rows, efficiency_verdicts(outcomes), api=api))

    flag_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for o in outcomes:
        for flag in o.static_flags:
            flag_counts[o.arm][flag] += 1
        for code in o.blocked:
            flag_counts[o.arm][f"blocked:{code}"] += 1
    if flag_counts:
        lines.append("")
        lines.append("## Static flags (informational)")
        lines.append("")
        for arm in sorted(flag_counts):
            lines.append(f"- **{arm}**")
            for label, count in sorted(flag_counts[arm].items()):
                lines.append(f"  - {label}: {count}")

    gate = gate_fn(outcomes) if outcomes else None
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
            for name in ("unsafe", "static_flags", "blocked", "failed_cases"):
                if name in fields:  # JSON has no tuples; restore the dataclass's type
                    fields[name] = tuple(fields[name])
            if "diagnostics_per_round" in fields:
                fields["diagnostics_per_round"] = tuple(
                    tuple(codes) for codes in fields["diagnostics_per_round"]
                )
            outcomes.append(TaskOutcome(**fields))
    return outcomes
