"""The 5b stage-1 Go cost pilot's pre-registered analysis.

Pre-registered in ``docs/superpowers/specs/2026-09-29-lattice-go-pilot.md``,
"Tests and decision" (binding). Turns run records (JSONL outcomes with the
fields an API-bench CLI run writes: ``task_id``, ``arm``, ``model``, ``rep``,
``correct``, ``cost_usd``, ``cost_basis``, ``family``, ``failure``, ``rounds``,
``output_tokens``, ``program_lines``, ``program_ast_nodes``) into the spec's
verdict:

- **P1** (primary, Sonnet only): the cost-per-correct-task ratio R = B / G,
  with a 95% percentile bootstrap interval over 10,000 task-clustered
  resamples. Confirmed if R <= 0.6 and the interval's upper bound < 1.0;
  refuted if R >= 1.0; otherwise not confirmed.
- **P2** (co-primary, Sonnet only): non-inferiority of arm B's correct rate,
  via the Newcombe-Wilson hybrid-score interval for a difference of two
  proportions (Newcombe 1998, method 10) on correct(B) - correct(G); holds
  when the lower limit of the **two-sided 90%** interval (a one-sided 95%
  bound) is > -0.10. Revised from an original 5-point/95%-two-sided margin
  before any run -- see the spec's "Revision before freeze" note -- because
  that margin was unattainable at this pilot's sample size even for a true
  tie between the arms.
- **Stage-1 verdict:** GO iff P1 is confirmed AND P2 holds; STOP otherwise.
- **S1** (secondary, reported but not decisive): the same P1/P2 measurements
  on claude-haiku-4-5.
- **S2** (secondary, descriptive): correct/silent/loud/harness rates with
  Wilson 95% intervals, program size, repair rounds, output tokens, and a
  per-family breakdown.

``python -m bench.analysis.go_pilot <out_dir>`` reads ``<out_dir>/outcomes.jsonl``,
renders the report and writes ``<out_dir>/go_pilot_report.md``.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from bench.edit_slice.pilot_gate import wilson
from bench.safety import outcome_class

SONNET = "claude-sonnet-5"
HAIKU = "claude-haiku-4-5"

# P2 non-inferiority: margin and confidence, revised before any run (see the
# spec's "Revision before freeze" note). A one-sided 95% bound is the lower
# limit of a two-sided 90% Newcombe-Wilson interval; z is the two-sided-90%
# critical value (scipy.stats.norm.ppf(0.95) / the standard normal table).
P2_MARGIN = 0.10
P2_Z = 1.644854

_REQUIRED_FIELDS = (
    "task_id",
    "arm",
    "model",
    "rep",
    "correct",
    "cost_usd",
    "cost_basis",
    "family",
    "failure",
    "rounds",
    "output_tokens",
    "program_lines",
    "program_ast_nodes",
)


@dataclass(frozen=True)
class Record:
    """One run record, as scored by the API bench. ``failure`` is None for a
    correct run, else a taxonomy label (see ``bench.safety.outcome_class``:
    "harness_error" -> harness, "wrong_result" -> silent, anything else loud)."""

    task_id: str
    arm: str
    model: str
    rep: int
    correct: bool
    cost_usd: float
    cost_basis: str
    family: str
    failure: str | None
    rounds: int
    output_tokens: int
    program_lines: int
    program_ast_nodes: int


Records = Sequence[Record]


def load(outcomes_path: Path) -> list[Record]:
    """Read run records from an outcomes JSONL file.

    Raises ``ValueError`` naming the missing field(s) when a line lacks a
    field this analysis depends on (the API bench's CLI-transport work adds
    ``rep`` and ``cost_basis`` to the outcome record; a run written before
    that lands, or a stray file, is rejected clearly rather than silently
    misread).
    """
    records: list[Record] = []
    with Path(outcomes_path).open() as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            missing = [name for name in _REQUIRED_FIELDS if name not in raw]
            if missing:
                raise ValueError(
                    f"{outcomes_path}:{lineno}: outcome record missing required "
                    f"field(s) {', '.join(missing)} (needed by bench.analysis.go_pilot): {raw!r}"
                )
            records.append(
                Record(
                    task_id=raw["task_id"],
                    arm=raw["arm"],
                    model=raw["model"],
                    rep=raw["rep"],
                    correct=bool(raw["correct"]),
                    cost_usd=float(raw["cost_usd"]),
                    cost_basis=raw["cost_basis"],
                    family=raw["family"],
                    failure=raw["failure"],
                    rounds=raw["rounds"],
                    output_tokens=raw["output_tokens"],
                    program_lines=raw["program_lines"],
                    program_ast_nodes=raw["program_ast_nodes"],
                )
            )
    return records


def _group(records: Records, arm: str, model: str) -> list[Record]:
    return [r for r in records if r.arm == arm and r.model == model]


def _cost_and_correct(records: Records, arm: str, model: str) -> tuple[float, int, int]:
    """(total cost, correct count, run count) for one arm/model slice."""
    group = _group(records, arm, model)
    correct = sum(1 for r in group if r.correct)
    return sum(r.cost_usd for r in group), correct, len(group)


def cost_per_correct(records: Records, arm: str, model: str) -> float:
    """Total CLI cost of the arm's runs (on ``model``) divided by the number
    of correct runs, per the spec's "Cost per correct task" definition."""
    cost, correct, _n = _cost_and_correct(records, arm, model)
    if correct == 0:
        raise ValueError(
            f"no correct runs for arm={arm!r} model={model!r}; cost per correct task is undefined"
        )
    return cost / correct


def _ratio_or_none(records: Records, model: str) -> float | None:
    cost_b, correct_b, _ = _cost_and_correct(records, "B", model)
    cost_g, correct_g, _ = _cost_and_correct(records, "G", model)
    if correct_b == 0 or correct_g == 0:
        return None
    return (cost_b / correct_b) / (cost_g / correct_g)


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile (numpy's default 'linear' method) of an
    already-sorted sequence."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = q * (len(sorted_values) - 1)
    lo_idx = math.floor(idx)
    hi_idx = math.ceil(idx)
    if lo_idx == hi_idx:
        return sorted_values[int(idx)]
    frac = idx - lo_idx
    return sorted_values[lo_idx] + frac * (sorted_values[hi_idx] - sorted_values[lo_idx])


def ratio_bootstrap(
    records: Records, model: str, *, n: int = 10_000, seed: int = 0
) -> tuple[float, float, float]:
    """(R, lo, hi): the cost-per-correct ratio B/G on ``model``, with a 95%
    percentile bootstrap interval over ``n`` resamples of the model's tasks
    (with replacement), each resample keeping all of a sampled task's runs
    -- both arms, every rep -- together. Seeded, so a given (records, model,
    seed) always returns the same interval.
    """
    group = [r for r in records if r.model == model]
    if not group:
        raise ValueError(f"no records for model {model!r}")
    r_point = _ratio_or_none(group, model)
    if r_point is None:
        raise ValueError(f"no correct runs in one or both arms for model {model!r}")

    by_task: dict[str, list[Record]] = defaultdict(list)
    for r in group:
        by_task[r.task_id].append(r)
    task_ids = sorted(by_task)

    rng = random.Random(seed)
    boots: list[float] = []
    for _ in range(n):
        sampled_tasks = rng.choices(task_ids, k=len(task_ids))
        resample = [r for t in sampled_tasks for r in by_task[t]]
        ratio = _ratio_or_none(resample, model)
        if ratio is not None:
            boots.append(ratio)
    if not boots:
        raise ValueError(
            f"bootstrap for model {model!r} produced no valid resample "
            "(every resample had zero correct runs in some arm)"
        )
    boots.sort()
    lo = _percentile(boots, 0.025)
    hi = _percentile(boots, 0.975)
    return r_point, lo, hi


def newcombe_wilson_diff(
    k1: int, n1: int, k2: int, n2: int, *, z: float = 1.959964
) -> tuple[float, float]:
    """The Newcombe-Wilson hybrid-score interval for p1 - p2 (Newcombe 1998,
    "Interval Estimation for the Difference Between Independent Proportions:
    Comparison of Eleven Methods", Statistics in Medicine 17:873-890, method
    10), built from the two proportions' independent Wilson score intervals
    at the same ``z``::

        L = (p1 - p2) - sqrt((p1 - l1)^2 + (u2 - p2)^2)
        U = (p1 - p2) + sqrt((u1 - p1)^2 + (p2 - l2)^2)

    ``z`` defaults to 1.959964, the two-sided-95% critical value, and the
    default-argument behaviour is validated in tests against Newcombe's own
    published worked example. ``bench.analysis.go_pilot.verdict`` calls this
    with ``z=P2_Z`` (the two-sided-90% value) for the pilot's non-inferiority
    test, per the spec's "Revision before freeze" note.
    """
    if n1 <= 0 or n2 <= 0:
        raise ValueError(f"newcombe_wilson_diff requires n1>0 and n2>0, got n1={n1}, n2={n2}")
    p1, p2 = k1 / n1, k2 / n2
    l1, u1 = wilson(k1, n1, z)
    l2, u2 = wilson(k2, n2, z)
    diff = p1 - p2
    lo = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return lo, hi


def verdict(records: Records) -> dict[str, object]:
    """P1/P2/stage-1, computed on Sonnet only per the spec."""
    r_point, _lo, hi = ratio_bootstrap(records, SONNET)
    if r_point <= 0.6 and hi < 1.0:
        p1 = "confirmed"
    elif r_point >= 1.0:
        p1 = "refuted"
    else:
        p1 = "not confirmed"

    _cost_b, correct_b, n_b = _cost_and_correct(records, "B", SONNET)
    _cost_g, correct_g, n_g = _cost_and_correct(records, "G", SONNET)
    diff_lo, _diff_hi = newcombe_wilson_diff(correct_b, n_b, correct_g, n_g, z=P2_Z)
    p2 = diff_lo > -P2_MARGIN

    stage1 = "GO" if p1 == "confirmed" and p2 else "STOP"
    return {"P1": p1, "P2": p2, "stage1": stage1}


def _wilson_rate_cell(k: int, n: int) -> str:
    if n == 0:
        return "n/a"
    lo, hi = wilson(k, n)
    return f"{k}/{n} [{lo:.2f}, {hi:.2f}]"


_CLASSES: tuple[str, ...] = ("correct", "silent", "loud", "harness")


def _class_counts(group: Sequence[Record]) -> dict[str, int]:
    """correct/silent/loud/harness counts for one group of records, via
    ``bench.safety.outcome_class`` (duck-typed: it only reads ``.correct``
    and ``.failure``, which ``Record`` also carries)."""
    counts = dict.fromkeys(_CLASSES, 0)
    for r in group:
        counts[outcome_class(r)] += 1
    return counts


def _loud_cell(counts: dict[str, int]) -> str:
    loud, harness = counts["loud"], counts["harness"]
    return f"{loud} (+{harness} harness)" if harness else str(loud)


# Preferred display order for the API suite's families (spec: "control,
# ownership, validation, errors, efficiency"); any other family sorts after,
# alphabetically, so this never hides an unrecognised family.
_FAMILY_ORDER: tuple[str, ...] = ("control", "ownership", "validation", "errors", "efficiency")


def _family_sort_key(name: str) -> tuple[int, str]:
    try:
        return (_FAMILY_ORDER.index(name), name)
    except ValueError:
        return (len(_FAMILY_ORDER), name)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def render(records: Records) -> str:
    """A markdown section in the ``docs/results/2026-09-26-bench-smoke.md``
    "Confirmation run" style: a correct/silent/loud table with Wilson
    intervals and cost per correct task per (model, arm), the pre-registered
    verdicts, S1 (the same measurements on Haiku, reported not decisive), S2
    (program size, repair rounds, output tokens) and a per-family breakdown."""
    models = sorted({r.model for r in records})
    arms = sorted({r.arm for r in records})

    lines = [
        "## Go cost pilot (stage 1, pre-registered)",
        "",
        "Spec: `docs/superpowers/specs/2026-09-29-lattice-go-pilot.md`.",
        "",
        "| model | arm | n | correct [95% CI] | silent [95% CI] | loud | $/correct |",
        "|---|---|---|---|---|---|---:|",
    ]
    for model in models:
        for arm in arms:
            group = _group(records, arm, model)
            n = len(group)
            if n == 0:
                continue
            counts = _class_counts(group)
            try:
                cpc = f"{cost_per_correct(records, arm, model):.4f}"
            except ValueError:
                cpc = "n/a"
            lines.append(
                f"| {model} | {arm} | {n} | {_wilson_rate_cell(counts['correct'], n)} | "
                f"{_wilson_rate_cell(counts['silent'], n)} | {_loud_cell(counts)} | {cpc} |"
            )

    lines += ["", "### Pre-registered verdicts", ""]
    try:
        r_point, lo, hi = ratio_bootstrap(records, SONNET)
        v = verdict(records)
        _cost_b, correct_b, n_b = _cost_and_correct(records, "B", SONNET)
        _cost_g, correct_g, n_g = _cost_and_correct(records, "G", SONNET)
        diff_lo, diff_hi = newcombe_wilson_diff(correct_b, n_b, correct_g, n_g, z=P2_Z)
        lines += [
            f"- **P1** (cost ratio R = B/G on {SONNET}): R = {r_point:.3f} "
            f"[{lo:.3f}, {hi:.3f}] (95% bootstrap, 10,000 task-clustered resamples) "
            f"-> **{v['P1']}**.",
            f"- **P2** (non-inferiority of correct(B) on {SONNET}, margin {P2_MARGIN:.0%}): "
            f"correct(B) - correct(G) = {diff_lo:.3f} to {diff_hi:.3f} "
            "(Newcombe-Wilson, two-sided 90% => one-sided 95%) -> "
            + ("**holds**" if v["P2"] else "**fails**")
            + ".",
            f"- **Stage-1 verdict: {v['stage1']}**.",
        ]
    except ValueError as exc:
        lines.append(f"- Verdict not computable: {exc}")

    lines += ["", f"### S1 (secondary, reported not decisive): {HAIKU}", ""]
    try:
        r_h, lo_h, hi_h = ratio_bootstrap(records, HAIKU)
        _cost_b, correct_b_h, n_b_h = _cost_and_correct(records, "B", HAIKU)
        _cost_g, correct_g_h, n_g_h = _cost_and_correct(records, "G", HAIKU)
        diff_lo_h, diff_hi_h = newcombe_wilson_diff(correct_b_h, n_b_h, correct_g_h, n_g_h, z=P2_Z)
        lines += [
            f"- P1 measurement: R = {r_h:.3f} [{lo_h:.3f}, {hi_h:.3f}] "
            "(95% bootstrap) -- not applied to the stage-1 verdict.",
            f"- P2 measurement: correct(B) - correct(G) = {diff_lo_h:.3f} to {diff_hi_h:.3f} "
            "(Newcombe-Wilson, two-sided 90%) -- not applied to the stage-1 verdict.",
        ]
    except ValueError as exc:
        lines.append(f"- Not computable: {exc}")

    lines += [
        "",
        "### S2 (secondary, descriptive)",
        "",
        "| model | arm | n | mean program lines | mean AST nodes | mean rounds "
        "| mean output tokens |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for model in models:
        for arm in arms:
            group = _group(records, arm, model)
            n = len(group)
            if n == 0:
                continue
            lines.append(
                f"| {model} | {arm} | {n} "
                f"| {_mean([r.program_lines for r in group]):.1f} "
                f"| {_mean([r.program_ast_nodes for r in group]):.0f} "
                f"| {_mean([r.rounds for r in group]):.2f} "
                f"| {_mean([r.output_tokens for r in group]):.0f} |"
            )

    families = sorted({r.family for r in records if r.family}, key=_family_sort_key)
    if families:
        lines += [
            "",
            "### Per-family breakdown",
            "",
            "| model | arm | family | n | correct [95% CI] | silent [95% CI] | loud |",
            "|---|---|---|---|---|---|---|",
        ]
        for model in models:
            for arm in arms:
                for family in families:
                    group = [
                        r
                        for r in records
                        if r.model == model and r.arm == arm and r.family == family
                    ]
                    n = len(group)
                    if n == 0:
                        continue
                    counts = _class_counts(group)
                    lines.append(
                        f"| {model} | {arm} | {family} | {n} "
                        f"| {_wilson_rate_cell(counts['correct'], n)} "
                        f"| {_wilson_rate_cell(counts['silent'], n)} "
                        f"| {_loud_cell(counts)} |"
                    )

    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path, help="run directory holding outcomes.jsonl")
    args = parser.parse_args(argv)

    records = load(args.out_dir / "outcomes.jsonl")
    report_path = args.out_dir / "go_pilot_report.md"
    report_path.write_text(render(records))
    print(str(report_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
