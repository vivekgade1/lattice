"""The option-1 pilot's gates (docs/superpowers/specs/2026-09-29-option1-pilot.md),
applied mechanically to run records."""

from __future__ import annotations

import pytest

from bench.edit_slice.pilot_gate import FAMILIES, evaluate_gates, wilson


def _run(ticket: str, outcome: str, silent: tuple[str, ...] = (), rep: int = 0) -> dict:
    return {"ticket": ticket, "arm": "P", "model": "claude-sonnet-5", "rep": rep,
            "outcome": outcome, "silent_cases": list(silent)}  # fmt: skip


def _correct_family(tickets: tuple[str, ...]) -> list[dict]:
    return [_run(t, "correct", rep=r) for t in tickets for r in range(3)]


def test_the_families_are_as_registered():
    assert FAMILIES == {
        "A": ("t31_vendor_sales", "t32_monthly_report", "t33_top_sellers",
              "t34_customer_spend"),
        "O": ("t35_vendor_order_lines", "t36_cancel_order", "t37_product_buyers",
              "t38_reorder"),
    }  # fmt: skip


def test_wilson_interval():
    lo, hi = wilson(0, 12)
    assert lo == 0.0 and hi == pytest.approx(0.2425, abs=1e-3)
    lo, hi = wilson(6, 12)
    assert lo == pytest.approx(0.2538, abs=1e-3) and hi == pytest.approx(0.7462, abs=1e-3)


def test_no_silent_runs_is_a_no_go():
    runs = _correct_family(FAMILIES["A"]) + _correct_family(FAMILIES["O"])
    report = evaluate_gates(runs)
    assert not report.families["A"].silent_gate and not report.families["O"].silent_gate
    assert report.decision == "NO-GO"


def test_covered_silent_runs_pass_both_gates():
    runs = _correct_family(FAMILIES["O"]) + [
        _run("t31_vendor_sales", "silent", ("t31.vicky_all_time",), 0),
        _run("t34_customer_spend", "silent", ("t34.admin",), 0),
        *[_run("t32_monthly_report", "correct", rep=r) for r in range(3)],
        *[_run("t33_top_sellers", "correct", rep=r) for r in range(3)],
        *[_run("t31_vendor_sales", "correct", rep=r) for r in (1, 2)],
        *[_run("t34_customer_spend", "correct", rep=r) for r in (1, 2)],
    ]
    report = evaluate_gates(runs)
    a = report.families["A"]
    assert (a.runs, a.silent, a.covered, a.silent_gate, a.mechanism_gate) == (12, 2, 2, True, True)
    assert report.decision == "GO: A"


def test_a_silent_run_is_covered_only_if_every_silent_case_has_a_mechanism():
    runs = _correct_family(FAMILIES["O"]) + [
        # fan-out caught, but also a route-order bug no guarantee catches: not covered
        _run("t32_monthly_report", "silent", ("t32.empty_month", "t32.february_boundaries")),
        _run("t32_monthly_report", "silent", ("t32.february_boundaries",), 1),
        *[_run(t, "correct", rep=r) for t in FAMILIES["A"] if t != "t32_monthly_report"
          for r in range(3)],
        _run("t32_monthly_report", "correct", rep=2),
    ]  # fmt: skip
    a = evaluate_gates(runs).families["A"]
    assert (a.silent, a.covered, a.silent_gate, a.mechanism_gate) == (2, 0, True, False)
    assert evaluate_gates(runs).decision == "SILENT-BUT-UNCOVERED"


def test_the_control_ticket_counts_for_the_rate_but_not_the_mechanism_gate():
    runs = (
        _correct_family(FAMILIES["A"])
        + [_run("t35_vendor_order_lines", "silent", ("t35.vicky",), r) for r in range(3)]
        + [_run(t, "correct", rep=r) for t in FAMILIES["O"][1:] for r in range(3)]
    )
    o = evaluate_gates(runs).families["O"]
    assert o.silent == 3 and o.silent_gate
    assert o.silent_outside_control == 0 and not o.mechanism_gate


def test_loud_runs_are_reported_but_are_not_silent():
    runs = _correct_family(FAMILIES["O"]) + [
        _run(t, "loud", rep=r) for t in FAMILIES["A"] for r in range(3)
    ]
    a = evaluate_gates(runs).families["A"]
    assert (a.loud, a.silent, a.silent_gate) == (12, 0, False)


def test_other_tickets_and_arms_are_ignored_and_duplicates_refused():
    runs = _correct_family(FAMILIES["A"]) + _correct_family(FAMILIES["O"])
    extra = [{**_run("t7_deactivate", "silent"), "arm": "P"},
             {**_run("t31_vendor_sales", "silent"), "arm": "L"}]  # fmt: skip
    assert evaluate_gates(runs + extra).families["A"].silent == 0
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_gates(runs + [_run("t31_vendor_sales", "correct", rep=0)])
