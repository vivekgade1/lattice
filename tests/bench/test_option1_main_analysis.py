"""The option-1 main experiment's tests (bench/edit_slice/main_analysis.py)."""

from __future__ import annotations

import pytest

from bench.edit_slice.main_analysis import (
    TICKETS,
    fisher_less,
    sign_test_less,
    verdict,
)


def test_fisher_matches_known_values():
    # Fisher's tea-tasting table: one-sided p = 17/70
    assert fisher_less(1, 3, 3, 1) == pytest.approx(17 / 70)
    assert fisher_less(0, 10, 10, 0) == pytest.approx(1 / 184756)
    assert fisher_less(5, 5, 5, 5) == pytest.approx(0.6718, abs=1e-4)
    assert fisher_less(3, 3, 3, 3) == pytest.approx(662 / 924)  # (1 + 36 + 225 + 400) / C(12, 6)


def test_sign_test_matches_known_values():
    assert sign_test_less(7, 0) == pytest.approx(1 / 128)
    assert sign_test_less(5, 2) == pytest.approx(29 / 128)
    assert sign_test_less(0, 0) == 1.0


def _run(arm: str, ticket: str, rep: int, outcome: str, silent: tuple[str, ...] = ()) -> dict:
    return {"model": "claude-sonnet-5", "arm": arm, "ticket": ticket, "rep": rep,
            "outcome": outcome, "silent_cases": list(silent)}  # fmt: skip


def _runs(arm: str, silent_per_ticket: dict[str, int]) -> list[dict]:
    out = []
    for t in TICKETS:
        k = silent_per_ticket.get(t, 0)
        for rep in range(6):
            if rep < k:
                out.append(_run(arm, t, rep, "silent", ("t38.not_enough_stock_writes_nothing",)))
            else:
                out.append(_run(arm, t, rep, "correct"))
    return out


def test_fewer_silent_runs_on_most_tickets_is_confirmed():
    p = _runs("P", {t: 3 for t in TICKETS})
    q = _runs("Q", {t: 0 for t in TICKETS})
    v = verdict(p + q, "claude-sonnet-5")
    assert v["decision"] == "CONFIRMED" and v["fisher_p"] < 0.05 and v["atomic_p"] < 0.05
    assert v["sign"][:2] == (7, 0)


def test_as_many_silent_runs_is_refuted():
    runs = _runs("P", {"t38_reorder": 2}) + _runs("Q", {"t39_bulk_price": 2})
    assert verdict(runs, "claude-sonnet-5")["decision"] == "REFUTED"


def test_a_small_difference_is_not_confirmed():
    runs = _runs("P", {"t38_reorder": 3}) + _runs("Q", {"t38_reorder": 1})
    assert verdict(runs, "claude-sonnet-5")["decision"] == "NOT CONFIRMED"


def test_fewer_silent_but_far_fewer_correct_is_a_trade():
    p = _runs("P", {t: 3 for t in TICKETS})
    q = [dict(r, outcome="loud") if r["rep"] >= 2 else r for r in _runs("Q", {})]  # 33% vs 50%
    assert verdict(p + q, "claude-sonnet-5")["decision"] == "CONFIRMED-BUT-TRADE"


def test_duplicates_are_refused():
    runs = _runs("P", {}) + _runs("Q", {})
    with pytest.raises(ValueError, match="duplicate"):
        verdict(runs + [runs[0]], "claude-sonnet-5")
