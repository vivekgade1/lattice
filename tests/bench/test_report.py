from __future__ import annotations

from pathlib import Path

import pytest

from bench.report import (
    ArmTierStats,
    Summary,
    evaluate_gate,
    read_outcomes,
    render_markdown,
    summarize,
    write_outcomes,
)
from bench.runners import TaskOutcome


def _outcome(**overrides: object) -> TaskOutcome:
    defaults: dict[str, object] = dict(
        task_id="s01",
        arm="A",
        tier="single",
        correct=True,
        check_passed=None,
        rounds=1,
        output_tokens=100,
        input_tokens=50,
        latency_s=1.5,
        failure=None,
        detail="",
    )
    defaults.update(overrides)
    return TaskOutcome(**defaults)  # type: ignore[arg-type]


def test_summarize_computes_per_tier_and_all_accuracy() -> None:
    outcomes = [
        _outcome(task_id="s01", tier="single", correct=True),
        _outcome(task_id="s02", tier="single", correct=False, failure="wrong_result"),
        _outcome(task_id="m01", tier="multi", correct=True),
    ]

    summary = summarize(outcomes)

    assert summary.stats[("A", "single")].n == 2
    assert summary.stats[("A", "single")].accuracy == 0.5
    assert summary.stats[("A", "multi")].n == 1
    assert summary.stats[("A", "multi")].accuracy == 1.0
    assert summary.stats[("A", "all")].n == 3
    assert summary.stats[("A", "all")].accuracy == pytest.approx(2 / 3)
    assert summary.failures["A"]["wrong_result"] == 1


def test_summarize_first_shot_pass_is_none_when_check_passed_is_none() -> None:
    summary = summarize([_outcome(check_passed=None)])

    assert summary.stats[("A", "all")].first_shot_pass is None
    assert summary.stats[("A", "all")].check_pass_rate is None


def test_summarize_check_pass_rate_for_arm_with_check_passed_data() -> None:
    outcomes = [
        _outcome(arm="B", tier="single", check_passed=True, correct=True, rounds=1),
        _outcome(arm="B", tier="single", check_passed=False, correct=False, failure="schema"),
    ]

    summary = summarize(outcomes)

    assert summary.stats[("B", "all")].check_pass_rate == 0.5


def test_summarize_first_shot_pass_only_counts_round_one_passes() -> None:
    outcomes = [
        _outcome(arm="B", tier="single", check_passed=True, correct=True, rounds=1),
        _outcome(arm="B", tier="single", check_passed=True, correct=True, rounds=2),
        _outcome(arm="B", tier="single", check_passed=False, correct=False, rounds=3),
    ]

    summary = summarize(outcomes)

    # First-shot pass: only the rounds==1 pass counts, out of 3 checked outcomes.
    assert summary.stats[("B", "all")].first_shot_pass == pytest.approx(1 / 3)
    # Check pass rate: both eventually-passing outcomes count, regardless of round.
    assert summary.stats[("B", "all")].check_pass_rate == pytest.approx(2 / 3)


def test_gate_passes_when_all_thresholds_are_met() -> None:
    summary = Summary(
        stats={
            ("A", "multi"): ArmTierStats(10, 0.40, None, None, 1.0, 100.0, 1.0),
            ("B", "multi"): ArmTierStats(10, 0.60, 0.99, 0.99, 1.5, 200.0, 2.0),
            ("A", "single"): ArmTierStats(10, 0.90, None, None, 1.0, 80.0, 1.0),
            ("B", "single"): ArmTierStats(10, 0.89, 0.99, 0.99, 1.2, 150.0, 1.8),
            ("B", "all"): ArmTierStats(20, 0.75, 0.995, 0.995, 1.35, 175.0, 1.9),
        },
        failures={},
    )

    gate = evaluate_gate(summary)

    assert gate.passed is True
    assert all(ok for _, ok, _ in gate.checks)


def test_gate_fails_on_insufficient_multi_step_improvement() -> None:
    summary = Summary(
        stats={
            ("A", "multi"): ArmTierStats(10, 0.40, None, None, 1.0, 100.0, 1.0),
            ("B", "multi"): ArmTierStats(10, 0.45, 0.99, 0.99, 1.5, 200.0, 2.0),
            ("A", "single"): ArmTierStats(10, 0.90, None, None, 1.0, 80.0, 1.0),
            ("B", "single"): ArmTierStats(10, 0.89, 0.99, 0.99, 1.2, 150.0, 1.8),
            ("B", "all"): ArmTierStats(20, 0.67, 0.995, 0.995, 1.35, 175.0, 1.9),
        },
        failures={},
    )

    gate = evaluate_gate(summary)

    assert gate.passed is False
    by_name = {name: ok for name, ok, _ in gate.checks}
    assert by_name["multi_step_improvement"] is False
    assert by_name["single_step_no_regression"] is True


def test_gate_fails_on_single_step_regression() -> None:
    summary = Summary(
        stats={
            ("A", "multi"): ArmTierStats(10, 0.40, None, None, 1.0, 100.0, 1.0),
            ("B", "multi"): ArmTierStats(10, 0.60, 0.99, 0.99, 1.5, 200.0, 2.0),
            ("A", "single"): ArmTierStats(10, 0.90, None, None, 1.0, 80.0, 1.0),
            ("B", "single"): ArmTierStats(10, 0.50, 0.99, 0.99, 1.2, 150.0, 1.8),
            ("B", "all"): ArmTierStats(20, 0.55, 0.995, 0.995, 1.35, 175.0, 1.9),
        },
        failures={},
    )

    gate = evaluate_gate(summary)

    assert gate.passed is False
    by_name = {name: ok for name, ok, _ in gate.checks}
    assert by_name["single_step_no_regression"] is False


def test_gate_fails_on_low_check_pass_rate() -> None:
    summary = Summary(
        stats={
            ("A", "multi"): ArmTierStats(10, 0.40, None, None, 1.0, 100.0, 1.0),
            ("B", "multi"): ArmTierStats(10, 0.60, 0.80, 0.80, 1.5, 200.0, 2.0),
            ("A", "single"): ArmTierStats(10, 0.90, None, None, 1.0, 80.0, 1.0),
            ("B", "single"): ArmTierStats(10, 0.89, 0.80, 0.80, 1.2, 150.0, 1.8),
            ("B", "all"): ArmTierStats(20, 0.75, 0.80, 0.80, 1.35, 175.0, 1.9),
        },
        failures={},
    )

    gate = evaluate_gate(summary)

    assert gate.passed is False
    by_name = {name: ok for name, ok, _ in gate.checks}
    assert by_name["check_pass_rate"] is False


def test_gate_handles_missing_arm_b_gracefully() -> None:
    summary = Summary(
        stats={("A", "multi"): ArmTierStats(10, 0.4, None, None, 1.0, 100.0, 1.0)},
        failures={},
    )

    gate = evaluate_gate(summary)

    assert gate.passed is False
    details = " ".join(detail for _, _, detail in gate.checks)
    assert "B" in details


def test_render_markdown_has_a_row_per_arm_and_a_gate_section() -> None:
    summary = summarize(
        [
            _outcome(arm="A", tier="single", correct=True),
            _outcome(arm="B", tier="single", correct=True, check_passed=True),
        ]
    )
    gate = evaluate_gate(summary)

    md = render_markdown(summary, gate)

    assert "| A |" in md
    assert "| B |" in md
    assert "Gate" in md


def test_render_markdown_without_gate_omits_gate_section() -> None:
    summary = summarize([_outcome()])

    md = render_markdown(summary, None)

    assert "## Gate" not in md


def test_write_and_read_outcomes_round_trip(tmp_path: Path) -> None:
    outcomes = [
        _outcome(task_id="s01"),
        _outcome(task_id="s02", correct=False, failure="wrong_result"),
    ]
    path = tmp_path / "outcomes.jsonl"

    write_outcomes(path, outcomes)
    loaded = read_outcomes(path)

    assert loaded == outcomes


# --- Regression tests for review findings minor 7 / minor 8 -----------------


def _passing_stats(**b_all_overrides: object) -> dict:
    b_all = dict(n=20, accuracy=0.75, first_shot_pass=0.995, check_pass_rate=0.995,
                 mean_rounds=1.35, mean_output_tokens=175.0, mean_latency_s=1.9, max_rounds=3)
    b_all.update(b_all_overrides)
    return {
        ("A", "multi"): ArmTierStats(10, 0.40, None, None, 1.0, 100.0, 1.0),
        ("B", "multi"): ArmTierStats(10, 0.60, 0.99, 0.99, 1.5, 200.0, 2.0),
        ("A", "single"): ArmTierStats(10, 0.90, None, None, 1.0, 80.0, 1.0),
        ("B", "single"): ArmTierStats(10, 0.89, 0.99, 0.99, 1.2, 150.0, 1.8),
        ("B", "all"): ArmTierStats(**b_all),  # type: ignore[arg-type]
    }


def test_summarize_records_max_rounds() -> None:
    summary = summarize(
        [_outcome(arm="B", rounds=1, check_passed=True), _outcome(arm="B", rounds=3,
                                                                   check_passed=True)]
    )
    assert summary.stats[("B", "all")].max_rounds == 3


def test_gate_passes_with_at_most_three_rounds() -> None:
    gate = evaluate_gate(Summary(stats=_passing_stats(max_rounds=3), failures={}))
    by_name = {name: ok for name, ok, _ in gate.checks}
    assert by_name["max_repair_rounds"] is True
    assert gate.passed is True


def test_gate_fails_when_arm_b_used_more_than_two_repairs() -> None:
    gate = evaluate_gate(Summary(stats=_passing_stats(max_rounds=4), failures={}))
    by_name = {name: ok for name, ok, _ in gate.checks}
    assert by_name["max_repair_rounds"] is False
    assert gate.passed is False


def test_join_tier_delta_is_informational_only() -> None:
    stats = _passing_stats()
    stats[("A", "join")] = ArmTierStats(10, 0.90, None, None, 1.0, 100.0, 1.0)
    stats[("B", "join")] = ArmTierStats(10, 0.10, 0.99, 0.99, 1.0, 100.0, 1.0)

    gate = evaluate_gate(Summary(stats=stats, failures={}))

    assert gate.passed is True
    assert "join" not in {name for name, _, _ in gate.checks}
    assert any("acc(B,join) - acc(A,join) = -0.800" in note for note in gate.notes)
    md = render_markdown(Summary(stats=stats, failures={}), gate)
    assert "-0.800" in md


def test_report_states_single_tier_tolerance() -> None:
    gate = evaluate_gate(Summary(stats=_passing_stats(), failures={}))
    md = render_markdown(Summary(stats=_passing_stats(), failures={}), gate)
    assert "0.02" in md
    assert "tolerance" in md.lower()


def test_report_lists_distinct_served_models_per_arm() -> None:
    summary = summarize(
        [
            _outcome(arm="A", model="claude-opus-5"),
            _outcome(arm="A", task_id="s02", model="claude-opus-5"),
            _outcome(arm="B", check_passed=True, model="claude-opus-5,claude-opus-4-8"),
        ]
    )

    assert summary.models == {
        "A": ("claude-opus-5",),
        "B": ("claude-opus-4-8", "claude-opus-5"),
    }
    md = render_markdown(summary, None)
    assert "Served models" in md
    assert "claude-opus-4-8" in md


def test_read_outcomes_accepts_legacy_lines_without_new_fields(tmp_path: Path) -> None:
    path = tmp_path / "legacy.jsonl"
    path.write_text(
        '{"task_id": "s01", "arm": "A", "tier": "single", "correct": true, '
        '"check_passed": null, "rounds": 1, "output_tokens": 1, "input_tokens": 2, '
        '"latency_s": 0.1, "failure": null, "detail": ""}\n'
    )
    [outcome] = read_outcomes(path)
    assert outcome.model == "" and outcome.cache_read_tokens == 0
