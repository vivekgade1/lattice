"""lattice.perf.budget: the side file's validation, the exact fit, the verdict, the report
schema and the text table (spike 1a)."""

from __future__ import annotations

import copy
import json
from fractions import Fraction

import pytest

from lattice.perf.budget import (
    BudgetError,
    SizeResult,
    expand,
    fit,
    judge,
    load,
    overall_verdict,
    parse,
    render,
)
from lattice.perf.measure import app_statements, shape, shapes_of

HABIT = {"method": "POST", "path": "/v1/habits", "user": "ann", "count": "N",
         "json": {"id": "{uuid}", "name": "habit-{i}", "period": "week"}}  # fmt: skip
GOOD = {
    "version": 1,
    "budgets": [{
        "id": "home", "endpoint": "GET /v1/home", "user": "ann",
        "seed": {"items": "N", "steps": [HABIT]},
        "sizes": [25, 5], "queries": {"constant": 8, "per_item": 0}, "latency_ms": None,
    }],
}  # fmt: skip


def _with(**changes):
    data = copy.deepcopy(GOOD)
    data["budgets"][0].update(changes)
    return data


def _budget(constant=6, per_item=1, latency=None):
    data = _with(queries={"constant": constant, "per_item": per_item}, latency_ms=latency)
    return parse(data).budgets[0]


def _sizes(counts: dict[int, list[int]], lat=None):
    return [SizeResult(n, list(c), list(lat or [1.0] * len(c))) for n, c in counts.items()]


# ---- parsing and validation ---------------------------------------------------------------


def test_parse_the_documented_example(tmp_path):
    p = tmp_path / "perf.budget.json"
    p.write_text(json.dumps(GOOD))
    bf = load(p)
    (b,) = bf.budgets
    assert (b.id, b.method, b.path, b.user) == ("home", "GET", "/v1/home", "ann")
    assert b.sizes == (5, 25)  # sorted
    assert (b.constant, b.per_item) == (8, 0)
    assert b.latency is None and bf.prepare is None
    assert b.steps[0].times(25) == 25 and b.steps[0].json["name"] == "habit-{i}"
    assert b.allowed(25) == 8


@pytest.mark.parametrize(
    ("data", "needle"),
    [
        ({**GOOD, "version": 2}, "version: must be 1"),
        (_with(sizes=[5]), "two or more distinct"),
        (_with(sizes=[5, 5]), "two or more distinct"),
        (_with(sizes=[5, -1]), "two or more distinct"),
        (_with(endpoint="FETCH /x"), "endpoint"),
        (_with(endpoint="GET x"), "endpoint"),
        (_with(queries={"constant": -1, "per_item": 0}), "queries.constant"),
        (_with(queries={"constant": 3, "per_item": "1"}), "queries.per_item"),
        (_with(queries=5), "queries"),
        (_with(latency_ms={"median": 10, "size": 7, "margin": 0.3}), "latency_ms.size"),
        (_with(latency_ms={"median": 10, "size": 5}), "latency_ms.margin"),
        (_with(latency_ms={"median": 0, "size": 5, "margin": 0.3}), "latency_ms.median"),
        (_with(bogus=1), "unknown key"),
        (_with(id="no spaces"), ".id"),
        (_with(seed={"items": "N", "steps": [{**HABIT, "count": 3}]}), "does not grow"),
        (_with(seed={"items": "N", "steps": [{**HABIT, "count": "M"}]}), ".count"),
        (_with(seed={"items": "N", "steps": [{**HABIT, "method": "GRAB"}]}), ".method"),
        (_with(seed={"items": 5, "steps": [HABIT]}), "seed.items"),
        ({**GOOD, "budgets": []}, "non-empty list"),
        ({**GOOD, "budgets": GOOD["budgets"] * 2}, "used twice"),
        ({**GOOD, "prepare": ""}, "prepare"),
    ],
)
def test_invalid_files_are_refused_with_the_reason(data, needle):
    with pytest.raises(BudgetError) as err:
        parse(data)
    assert needle in str(err.value)


def test_load_refuses_missing_and_non_json(tmp_path):
    with pytest.raises(BudgetError, match="cannot read"):
        load(tmp_path / "nope.json")
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(BudgetError, match="not JSON"):
        load(tmp_path / "bad.json")


def test_latency_budget_parsed_with_its_margin():
    b = _budget(latency={"median": 40, "size": 25, "margin": 0.5})
    assert (b.latency.median, b.latency.size, b.latency.margin) == (40.0, 25, 0.5)
    assert b.latency.limit() == 60.0


def test_expand_placeholders():
    seq = iter(["u1", "u2"])
    value = {"id": "{uuid}", "name": "h-{i}", "tags": ["{i}", 3], "n": 7}
    out = expand(value, 4, lambda: next(seq))
    assert out == {"id": "u1", "name": "h-4", "tags": ["4", 3], "n": 7}
    assert expand("/items/{i}", 2, lambda: "x") == "/items/2"


# ---- the fit (exact arithmetic) -----------------------------------------------------------


def test_fit_of_the_p0_numbers_is_n_plus_6():
    f = fit({5: [11] * 10, 25: [31] * 10})
    assert (f.constant, f.per_item, f.exact) == (6, 1, True)


def test_fit_constant_is_the_intercept_not_a_count():
    f = fit({5: [9], 25: [9]})
    assert (f.constant, f.per_item) == (9, 0)
    f = fit({10: [14], 30: [34]})
    assert f.constant == 4  # not 14 (smaller size's count) nor 34 (larger size's)


def test_fit_is_rational_and_exact():
    f = fit({0: [3], 4: [5], 8: [7]})
    assert f.per_item == Fraction(1, 2) and f.constant == 3 and f.exact
    assert f.to_dict() == {"constant": 3, "per_item": 0.5, "exact": True}


def test_fit_not_exact_when_runs_differ_or_not_linear():
    assert not fit({5: [11, 11, 12], 25: [31] * 3}).exact
    curved = fit({5: [10], 10: [30], 25: [60]})
    assert not curved.exact and curved.per_item == Fraction(50, 20)


def test_fit_needs_two_sizes():
    with pytest.raises(ValueError):
        fit({5: [11]})


# ---- the verdict --------------------------------------------------------------------------


def test_exactly_at_budget_passes():
    r = judge(_budget(6, 1), _sizes({5: [11] * 10, 25: [31] * 10}))
    assert r.verdict == "pass", r.notes
    assert r.delta() == {"constant": 0, "per_item": 0}
    assert not any("better" in n for n in r.notes)


def test_zero_per_item_budget_met_exactly_passes():
    r = judge(_budget(4, 0), _sizes({5: [4] * 10, 25: [4] * 10}))
    assert r.verdict == "pass", r.notes


def test_per_item_over_budget_fails():
    r = judge(_budget(4, 0), _sizes({5: [9] * 10, 25: [29] * 10}))
    assert r.verdict == "fail"
    assert any("per_item 1 > budget 0" in n for n in r.notes)


def test_constant_over_budget_fails():
    r = judge(_budget(3, 0), _sizes({5: [4] * 3, 25: [4] * 3}))
    assert r.verdict == "fail" and any("constant 4 > budget 3" in n for n in r.notes)


def test_better_than_budget_is_reported():
    r = judge(_budget(6, 1), _sizes({5: [6] * 10, 25: [6] * 10}))
    assert r.verdict == "pass"
    assert r.delta() == {"constant": 0, "per_item": -1}
    assert any(n.startswith("better than the budget") and "per_item 0 < 1" in n
               for n in r.notes)  # fmt: skip


def test_trading_constant_for_per_item_passes_and_shows_the_crossover():
    r = judge(_budget(6, 1), _sizes({5: [7] * 10, 25: [7] * 10}))
    assert r.verdict == "pass", r.notes
    assert r.delta() == {"constant": 1, "per_item": -1}
    assert len(r.notes) == 2
    assert "constant traded for per-item" in r.notes[0]
    assert "constant 7 > budget 6" in r.notes[0] and "N* = 1" in r.notes[0]
    assert r.notes == [
        "constant traded for per-item: constant 7 > budget 6 accepted (per_item 0 < budget 1, "
        "every measured size within its allowed count); at or under the budget for N >= N* = 1, "
        "over it for N < 1",
        "better than the budget: per_item 0 < 1",
    ]
    assert not any(n.startswith("over budget") for n in r.notes)


def test_no_trade_when_a_size_is_over_its_allowed_count():
    r = judge(_budget(6, 1), _sizes({5: [12] * 10, 25: [12] * 10}))
    assert r.verdict == "fail"
    assert "over budget: constant 12 > budget 6" in r.notes
    assert "over budget: N=5: 12 statements > allowed 11" in r.notes
    assert any(n.startswith("no trade:") for n in r.notes)
    assert not any(n.startswith("constant traded") for n in r.notes)


def test_constant_over_with_equal_per_item_still_fails():
    r = judge(_budget(6, 1), _sizes({5: [12] * 10, 25: [32] * 10}))
    assert r.verdict == "fail"
    assert r.delta() == {"constant": 1, "per_item": 0}
    assert any("constant 7 > budget 6" in n for n in r.notes)
    assert not any(n.startswith(("no trade", "constant traded")) for n in r.notes)


def test_reverse_trade_is_refused():
    # a cheaper constant bought with a per-item cost the budget does not allow
    r = judge(_budget(30, 0), _sizes({5: [5] * 10, 25: [25] * 10}))
    assert r.verdict == "fail"
    assert r.delta() == {"constant": -30, "per_item": 1}
    assert any("per_item 1 > budget 0" in n for n in r.notes)
    assert not any(n.startswith(("no trade", "constant traded")) for n in r.notes)


def test_trade_shows_a_fractional_crossover():
    r = judge(_budget(4, 3), _sizes({5: [12] * 10, 25: [32] * 10}))
    assert (r.fit.constant, r.fit.per_item) == (7, 1)
    assert r.verdict == "pass", r.notes
    assert any("constant 7 > budget 4" in n and "N* = 1.5" in n for n in r.notes)


def test_trade_at_the_boundary_size_passes():
    # 11 statements at N=5 is exactly the allowed 6 + 1*5; the crossover is the size itself
    r = judge(_budget(6, 1), _sizes({5: [11] * 10, 25: [11] * 10}))
    assert r.verdict == "pass", r.notes
    assert any("constant 11 > budget 6" in n and "N* = 5" in n for n in r.notes)


def test_trade_does_not_excuse_latency():
    slow = _sizes({5: [7] * 3, 25: [7] * 3}, lat=[100.0, 120.0, 110.0])
    r = judge(_budget(6, 1, {"median": 50, "size": 25, "margin": 0.5}), slow)
    assert r.verdict == "fail"
    assert any(n.startswith("over budget: latency: median 110.00 ms") for n in r.notes)
    assert any(n.startswith("constant traded for per-item") for n in r.notes)
    assert not any(n.startswith("over budget: constant") for n in r.notes)


def test_falling_count_trades_against_a_zero_per_item_budget():
    r = judge(_budget(8, 0), _sizes({5: [8] * 10, 25: [4] * 10}))
    assert (r.fit.constant, r.fit.per_item) == (9, Fraction(-1, 5))
    assert r.verdict == "pass", r.notes
    assert any("constant 9 > budget 8" in n and "N* = 5" in n for n in r.notes)


def test_a_count_off_the_line_fails_per_size():
    # smallest and largest sizes fit 4 + 0*N, the middle size does not
    r = judge(_budget(4, 0), _sizes({5: [4], 10: [12], 25: [4]}))
    assert r.verdict == "fail" and any("N=10: 12 statements > allowed 4" in n for n in r.notes)
    assert not r.fit.exact


def test_fewer_than_two_measured_sizes_is_unmeasured():
    r = judge(_budget(), [SizeResult(5, [11]), SizeResult(25, [])])
    assert r.verdict == "unmeasured" and r.fit is None


def test_latency_compared_only_when_set():
    slow = _sizes({5: [11] * 3, 25: [31] * 3}, lat=[100.0, 120.0, 110.0])
    assert judge(_budget(6, 1), slow).verdict == "pass"
    r = judge(_budget(6, 1, {"median": 50, "size": 25, "margin": 0.5}), slow)
    assert r.verdict == "fail" and any("latency: median 110.00 ms" in n for n in r.notes)
    r = judge(_budget(6, 1, {"median": 80, "size": 25, "margin": 0.5}), slow)
    assert r.verdict == "pass" and any("within 120.00 ms" in n for n in r.notes)


def test_overall_verdict():
    assert overall_verdict(["pass", "pass"]) == "pass"
    assert overall_verdict(["pass", "fail", "unmeasured"]) == "fail"
    assert overall_verdict(["pass", "unmeasured"]) == "unmeasured"
    assert overall_verdict([]) == "unmeasured"


# ---- the report ---------------------------------------------------------------------------


def test_report_schema():
    r = judge(_budget(8, 0), _sizes({5: [11] * 2, 25: [31] * 2}, lat=[2.0, 4.0]))
    d = r.to_dict()
    assert list(d) == ["budget_id", "endpoint", "sizes", "fit", "budget", "verdict", "delta",
                       "notes"]  # fmt: skip
    assert d["budget_id"] == "home" and d["endpoint"] == "GET /v1/home"
    assert d["sizes"][0] == {"n": 5, "statements": [11, 11],
                             "latency_ms": {"min": 2.0, "median": 3.0, "max": 4.0}}  # fmt: skip
    assert d["fit"] == {"constant": 6, "per_item": 1, "exact": True}
    assert d["budget"] == {"queries": {"constant": 8, "per_item": 0}, "latency_ms": None}
    assert d["verdict"] == "fail"
    json.dumps(d)  # serialisable


def test_render_table():
    r = judge(_budget(4, 0), _sizes({5: [9] * 10, 25: [29] * 10}))
    text = render([r])
    assert "home: GET /v1/home  budget 4 + 0*N statements  ->  FAIL" in text
    assert "9 x10" in text and "29 x10" in text
    assert "fit: 4 + 1*N (exact)" in text
    assert text.splitlines()[-1] == "perf: fail (0 pass, 1 fail, 0 unmeasured)"


# ---- counting helpers ---------------------------------------------------------------------


def test_app_statements_excludes_the_tools_connections_and_other_commands():
    rows = [
        (7, b"Query", b"TRUNCATE TABLE mysql.general_log"),
        (12, b"Prepare", b"SELECT * FROM users WHERE id = ?"),
        (12, b"Execute", b"SELECT * FROM users WHERE id = 1"),
        (12, b"Close stmt", b""),
        (12, "Query", "SELECT 1"),
        (7, "Query", "SELECT thread_id, command_type, argument FROM mysql.general_log"),
        (9, "Execute", "SELECT 2"),
        (12, "Connect", "root@localhost on db"),
    ]
    assert app_statements(rows, {7}) == ["SELECT * FROM users WHERE id = 1", "SELECT 1",
                                         "SELECT 2"]  # fmt: skip
    assert len(app_statements(rows, {7, 9})) == 2


def test_shapes():
    assert shape("SELECT  * FROM t WHERE id = 12 AND name = 'x''y'") == \
        "SELECT * FROM t WHERE id = ? AND name = ?"  # fmt: skip
    assert shapes_of(["SELECT 1", "SELECT a FROM b WHERE c = 2", "SELECT 3"]) == [
        (2, "SELECT ?"), (1, "SELECT a FROM b WHERE c = ?")]  # fmt: skip
