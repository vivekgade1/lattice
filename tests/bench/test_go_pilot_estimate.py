"""The Go cost pilot's character-based cost estimate (bench.analysis.go_pilot_estimate)."""

from __future__ import annotations

import pytest

from bench.analysis import go_pilot_estimate as est
from bench.edit_slice.run import CHARS_PER_TOKEN, FEEDBACK_TOKENS


def test_the_estimate_covers_the_480_runs_with_an_ordered_range() -> None:
    result = est.estimate()
    assert result["runs"] == 40 * 2 * 2 * 3 == 480
    assert sum(c["runs"] for c in result["cells"] if c["scenario"] == "central") == 480
    low, central, high = (result["totals"][s] for s in ("low", "central", "high"))
    assert 0 < low < central < high
    # both arms' real system prompts are used, not a placeholder
    assert result["system_chars"]["G"] > 10_000 and result["system_chars"]["B"] > 10_000


def test_answers_come_from_the_dry_run_go_and_the_reference_lattice() -> None:
    sizes, ratio = est.answer_chars()
    assert sizes[("G", "c01")] == len(est.canned_go("c01") or "")
    # v01's canned file has two rounds (a build error, then the fix): the final one counts
    assert '\t"errors"\n' in (est.canned_go("v01") or "")
    ref = (est.FIXTURES / "o01" / "lattice.py").read_text()
    assert sizes[("B", "o01")] == len(ref)
    assert est.canned_go("c02") is None
    fastapi = len((est.FIXTURES / "c02" / "fastapi_correct.py").read_text())
    assert sizes[("G", "c02")] == round(fastapi * ratio)
    assert ratio > 1  # Go is more verbose than FastAPI


def test_run_cost_follows_the_edit_slice_formula() -> None:
    scenario = est.Scenario("t", False, 1.0, {"claude-sonnet-5": 1.0}, (0.5, 0.0))
    system, user, answer = 3500, 0, 350
    exp_in, exp_out, cost = est.run_cost("claude-sonnet-5", scenario, system, user, answer)
    prompt, out = system / CHARS_PER_TOKEN, answer / CHARS_PER_TOKEN
    assert exp_in == pytest.approx(prompt + 0.5 * (prompt + out + FEEDBACK_TOKENS))
    assert exp_out == pytest.approx(out * 1.5)
    assert cost == pytest.approx((exp_in * 2.0 + exp_out * 10.0) / 1e6)  # Sonnet 5 list
