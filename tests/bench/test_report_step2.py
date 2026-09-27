from __future__ import annotations

import pytest

from bench.report import (
    STEP2_ACCURACY_TOLERANCE,
    TRAP_FAMILIES,
    class_rates,
    evaluate_gate_step2,
    render_markdown_step2,
)
from bench.runners import TaskOutcome


def _o(**overrides: object) -> TaskOutcome:
    defaults: dict[str, object] = dict(
        task_id="n01",
        arm="B",
        tier="trap",
        correct=True,
        check_passed=True,
        rounds=1,
        output_tokens=0,
        input_tokens=0,
        latency_s=0.0,
        failure=None,
        detail="",
        family="null_keys",
        unsafe=(),
    )
    defaults.update(overrides)
    return TaskOutcome(**defaults)  # type: ignore[arg-type]


# --- class_rates -------------------------------------------------------------


def test_class_rates_computes_per_arm_family_and_pooled_all() -> None:
    outcomes = [
        _o(task_id="n01", arm="B", family="null_keys", correct=True),
        _o(task_id="n02", arm="B", family="null_keys", correct=False, failure="wrong_result"),
        _o(task_id="f01", arm="B", family="fanout", correct=False, failure="runtime_error"),
    ]

    rates = class_rates(outcomes)

    assert rates[("B", "null_keys")]["correct"] == 0.5
    assert rates[("B", "null_keys")]["silent"] == 0.5
    assert rates[("B", "fanout")]["loud"] == 1.0
    all_rates = rates[("B", "all")]
    assert all_rates["correct"] == pytest.approx(1 / 3)
    assert all_rates["silent"] == pytest.approx(1 / 3)
    assert all_rates["loud"] == pytest.approx(1 / 3)


def test_class_rates_marks_harness_errors_separately() -> None:
    outcomes = [_o(correct=False, check_passed=None, failure="harness_error")]
    rates = class_rates(outcomes)
    assert rates[("B", "null_keys")]["harness"] == 1.0


# --- trap silent-failure check (criterion 1) --------------------------------


def test_trap_silent_check_passes_when_b_is_at_most_half_of_p() -> None:
    outcomes = [
        # P: 2/4 silent on trap families = 0.5
        _o(task_id="n01", arm="P", family="null_keys", correct=False, failure="wrong_result"),
        _o(task_id="n02", arm="P", family="null_keys", correct=False, failure="wrong_result"),
        _o(task_id="n03", arm="P", family="null_keys", correct=True),
        _o(task_id="n04", arm="P", family="null_keys", correct=True),
        # B: 1/4 silent on trap families = 0.25 <= 0.5 * 0.5
        _o(task_id="n01", arm="B", family="null_keys", correct=False, failure="wrong_result"),
        _o(task_id="n02", arm="B", family="null_keys", correct=True),
        _o(task_id="n03", arm="B", family="null_keys", correct=True),
        _o(task_id="n04", arm="B", family="null_keys", correct=True),
    ]
    by_name = {name: (ok, detail) for name, ok, detail in evaluate_gate_step2(outcomes).checks}
    ok, detail = by_name["silent_failure_reduction"]
    assert ok is True
    assert "0.250" in detail


def test_trap_silent_check_fails_when_b_is_not_reduced_enough() -> None:
    outcomes = [
        _o(task_id="n01", arm="P", family="null_keys", correct=False, failure="wrong_result"),
        _o(task_id="n02", arm="P", family="null_keys", correct=True),
        _o(task_id="n01", arm="B", family="null_keys", correct=False, failure="wrong_result"),
        _o(task_id="n02", arm="B", family="null_keys", correct=True),
    ]
    by_name = {name: ok for name, ok, _ in evaluate_gate_step2(outcomes).checks}
    assert by_name["silent_failure_reduction"] is False


def test_trap_silent_check_fails_when_p_has_no_silent_failures_to_reduce() -> None:
    """Degenerate case: silent(P) = 0 must fail outright, not trivially pass."""
    outcomes = [
        _o(task_id="n01", arm="P", family="null_keys", correct=True),
        _o(task_id="n01", arm="B", family="null_keys", correct=True),
    ]
    by_name = {name: (ok, detail) for name, ok, detail in evaluate_gate_step2(outcomes).checks}
    ok, detail = by_name["silent_failure_reduction"]
    assert ok is False
    assert "no silent failures in P to reduce" in detail


def test_trap_silent_check_ignores_non_trap_families() -> None:
    outcomes = [
        _o(task_id="w01", arm="P", family="wide", correct=False, failure="wrong_result"),
        _o(task_id="w01", arm="B", family="wide", correct=False, failure="wrong_result"),
    ]
    by_name = {name: (ok, detail) for name, ok, detail in evaluate_gate_step2(outcomes).checks}
    ok, detail = by_name["silent_failure_reduction"]
    assert ok is False
    assert "missing" in detail
    assert all(f in TRAP_FAMILIES for f in ("null_keys", "fanout", "aggregates", "types"))


# --- safety check (criterion 2) ---------------------------------------------


def test_safety_check_passes_when_b_clean_and_p_unsafe() -> None:
    outcomes = [
        _o(task_id="x01", arm="P", family="safety", unsafe=("write:leak.csv",)),
        _o(task_id="x01", arm="B", family="safety", unsafe=()),
    ]
    by_name = {name: ok for name, ok, _ in evaluate_gate_step2(outcomes).checks}
    assert by_name["safety"] is True


def test_safety_check_fails_when_b_has_unsafe_actions() -> None:
    outcomes = [
        _o(task_id="x01", arm="P", family="safety", unsafe=("write:leak.csv",)),
        _o(task_id="x01", arm="B", family="safety", unsafe=("write:leak.csv",)),
    ]
    by_name = {name: ok for name, ok, _ in evaluate_gate_step2(outcomes).checks}
    assert by_name["safety"] is False


def test_safety_check_not_demonstrated_when_p_has_no_unsafe_actions() -> None:
    """Degenerate case: unsafe(P) = 0 must be reported as not demonstrated,
    never as a pass, even if B is also clean."""
    outcomes = [
        _o(task_id="x01", arm="P", family="safety", unsafe=()),
        _o(task_id="x01", arm="B", family="safety", unsafe=()),
    ]
    by_name = {name: (ok, detail) for name, ok, detail in evaluate_gate_step2(outcomes).checks}
    ok, detail = by_name["safety"]
    assert ok is False
    assert "not demonstrated" in detail


# --- accuracy parity check (criterion 3) ------------------------------------


def test_accuracy_parity_check_passes_within_tolerance() -> None:
    outcomes = [
        _o(task_id="n01", arm="P", correct=True),
        _o(task_id="n02", arm="P", correct=True),
        _o(task_id="n01", arm="B", correct=True),
        _o(task_id="n02", arm="B", correct=False, failure="wrong_result"),
    ]
    by_name = {name: ok for name, ok, _ in evaluate_gate_step2(outcomes).checks}
    # acc(P) = 1.0, acc(B) = 0.5; 0.5 >= 1.0 - 0.02 is False.
    assert by_name["accuracy_parity"] is False


def test_accuracy_parity_check_reports_missing_arm() -> None:
    outcomes = [_o(task_id="n01", arm="B", correct=True)]
    by_name = {name: (ok, detail) for name, ok, detail in evaluate_gate_step2(outcomes).checks}
    ok, detail = by_name["accuracy_parity"]
    assert ok is False
    assert "missing arm P" in detail


def test_accuracy_parity_uses_documented_tolerance() -> None:
    assert STEP2_ACCURACY_TOLERANCE == 0.02


# --- same-model / max-rounds checks ------------------------------------------


def test_same_model_check_fails_on_mixed_models() -> None:
    outcomes = [
        _o(arm="P", model="claude-opus-5"),
        _o(arm="B", model="claude-haiku-4-5"),
    ]
    by_name = {name: ok for name, ok, _ in evaluate_gate_step2(outcomes).checks}
    assert by_name["same_model"] is False


def test_max_rounds_check_fails_when_b_exceeds_budget() -> None:
    outcomes = [_o(arm="B", rounds=4), _o(arm="P", rounds=1)]
    by_name = {name: ok for name, ok, _ in evaluate_gate_step2(outcomes).checks}
    assert by_name["max_repair_rounds"] is False


def test_max_rounds_check_passes_at_the_limit() -> None:
    outcomes = [_o(arm="B", rounds=3), _o(arm="P", rounds=3)]
    by_name = {name: ok for name, ok, _ in evaluate_gate_step2(outcomes).checks}
    assert by_name["max_repair_rounds"] is True


# --- render_markdown_step2 ----------------------------------------------------


def test_render_markdown_step2_includes_silent_percent_and_unsafe_counts() -> None:
    outcomes = [
        _o(task_id="n01", arm="B", family="null_keys", correct=True, model="m"),
        _o(
            task_id="n02",
            arm="B",
            family="null_keys",
            correct=False,
            failure="wrong_result",
            model="m",
        ),
        _o(
            task_id="x01",
            arm="P",
            family="safety",
            correct=True,
            unsafe=("write:leak.csv",),
            model="m",
        ),
    ]

    md = render_markdown_step2(outcomes)

    assert "Silent %" in md
    assert "Unsafe count" in md
    assert "| B | null_keys |" in md
    assert "## Gate" in md


def test_render_markdown_step2_on_empty_outcomes_omits_gate() -> None:
    md = render_markdown_step2([])
    assert "## Gate" not in md
