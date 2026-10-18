# ruff: noqa: E501
"""Typed rule conditions, round 3 (Spike H3): the per-cause changes of D0 and Amendment 2 on the mini-shop.

One dev case per change (``tests/spec/fixtures/forms_r3``) and the named kill tests of the round-3
mutants HZ1-HZ15 of ``dogfood/forms_mutants3.py``
(docs/results/2026-10-18-typed-conditions-round3-prereg.md, sections 2 and 6, Amendment 2).
"""

from __future__ import annotations

import dataclasses
import importlib.util
from pathlib import Path

import pytest

from lattice.spec import forms as F
from lattice.spec import forms_tier2 as T2
from lattice.spec.model import Step
from lattice.spec.rules import Trace
from tests.spec import minishop as M

FIX = Path(__file__).parent / "fixtures" / "forms_r3" / "minishop"
REPO = Path(__file__).resolve().parents[2]
SPEC = M.SPEC
ORDER1 = {"orders": [{"id": 1, "user_id": 1, "status": "pending", "total": 0.0}]}
NESTED_SRC = '@endpoint("GET", "/orders/{order_id}/items/{item_id}", auth=Authenticated)\ndef read_item(order_id: Int, item_id: Int, user: CurrentUser) -> One:\n    pass\n'
NESTED = dataclasses.replace(SPEC, programs={**SPEC.programs, "nested": NESTED_SRC})


def mk(kind: str, form: dict, fid: str = "F90", rule: str = "R6") -> F.Form:
    return F.Form(fid, rule, "0" * 64, kind, form, "approved", "hand", "t", F.form_sha(kind, form))


def codes(forms, spec=SPEC) -> list[str]:
    return sorted({d.code for d in F.check_forms(spec, None, forms)})


def path_bound(**kw) -> dict:
    form = {
        "field": {"param": "GET /orders/{order_id}", "path": "path.order_id"},
        "cond": {"node": "Cmp", "op": "ge", "value": 1}, "caller": "ann",
        "request": {"path": "/orders/1"}, "given": ORDER1,
        "accepted": {"status": 200}, "refused": {"status": 404},
    }  # fmt: skip
    form.update(kw)
    return form


@pytest.fixture(scope="module")
def trace() -> Trace:
    return Trace.load(FIX / "spec.trace.json")


@pytest.fixture(scope="module")
def fset(trace) -> F.FormSet:
    fs = F.load_set(trace, F.forms_paths(FIX / "spec.trace.json"))
    assert fs is not None
    return fs


@pytest.fixture(scope="module")
def gen(fset) -> F.Generated:
    return F.generate(SPEC, fset.generating())


def first(g: F.Generated, name: str) -> Step:
    st = next(s for s in g.scenarios if s.name == name).steps[0]
    assert isinstance(st, Step)
    return st


# ---- the fixture as a whole ----------------------------------------------------------------------


def test_the_round3_fixture_type_checks_and_every_form_is_current(fset, trace):
    assert F.check_forms(SPEC, trace, fset.approved) == []
    assert {r.state for r in fset.resolved} == {"current"}


# ---- cause 8: a bound on a path placeholder or a query key ------------------------------------------


def test_pc3_a_path_placeholder_bound_substitutes_the_value_at_the_placeholder(gen):
    at, beyond = first(gen, "form:F1:at_bound"), first(gen, "form:F1:beyond")
    assert (at.path, at.expect_status) == ("/orders/1", 200)
    assert (beyond.path, beyond.expect_status) == ("/orders/0", 404)
    assert not [n for n in gen.notes if n.startswith("F1:")]


def test_hz2_beyond_is_the_value_one_step_past_the_bound(gen):
    assert first(gen, "form:F1:beyond").path == "/orders/0"
    assert first(gen, "form:F1:at_bound").path != first(gen, "form:F1:beyond").path
    g = F.generate(SPEC, [mk("bound", path_bound(cond={"node": "Cmp", "op": "lt", "value": 100}))])
    assert (first(g, "form:F90:at_bound").path, first(g, "form:F90:beyond").path) == (
        "/orders/99",
        "/orders/100",
    )


def test_hz4_the_at_bound_case_of_a_path_bound_expects_the_accepted_outcome(gen):
    assert first(gen, "form:F1:at_bound").expect_status == 200
    assert first(gen, "form:F1:beyond").expect_status == 404


def test_hz5_the_beyond_case_of_a_path_bound_asserts_the_derived_locus(gen):
    assert first(gen, "form:F1:beyond").expect_db == {"Order": {"where": {"id": 0}, "count": 0}}
    assert first(gen, "form:F1:at_bound").expect_db is None


def test_hz1_a_path_bound_emits_both_cases_and_no_note():
    g = F.generate(SPEC, [mk("bound", path_bound())])
    assert {s.name for s in g.scenarios} == {"form:F90:at_bound", "form:F90:beyond"}
    assert g.notes == ()


def test_hz6_a_query_key_bound_is_placed_in_the_query(gen):
    a, b = first(gen, "form:F2:at_bound"), first(gen, "form:F2:beyond")
    assert (a.path, a.params) == ("/orders", {"limit": 1})
    assert (b.path, b.params) == ("/orders", {"limit": 0})


NESTED_FORM = {
    "field": {"param": "GET /orders/{order_id}/items/{item_id}", "path": "path.item_id"},
    "cond": {"node": "Cmp", "op": "ge", "value": 1}, "caller": "ann",
    "request": {"path": "/orders/5/items/7"}, "refused": {"status": 404}, "accepted": {"status": 200},
}  # fmt: skip


def test_hz3_the_named_placeholder_is_substituted_not_the_last_or_the_first():
    assert codes([mk("bound", NESTED_FORM)], NESTED) == []
    g = F.generate(NESTED, [mk("bound", NESTED_FORM)])
    assert first(g, "form:F90:at_bound").path == "/orders/5/items/1"
    assert first(g, "form:F90:beyond").path == "/orders/5/items/0"
    first_ph = dict(
        NESTED_FORM,
        field={"param": NESTED_FORM["field"]["param"], "path": "path.order_id"},
        cond={"node": "Cmp", "op": "ge", "value": 2},
    )
    g2 = F.generate(NESTED, [mk("bound", first_ph)])
    assert first(g2, "form:F90:at_bound").path == "/orders/2/items/7"
    assert first(g2, "form:F90:beyond").path == "/orders/1/items/7"


def test_hz7_a_path_value_the_placeholder_cannot_hold_is_f021_at_check_not_a_raise():
    empty = dict(NESTED_FORM, cond={"node": "Len", "op": "min_len", "value": 0})
    assert codes([mk("bound", empty)], NESTED) == ["F021"]
    slash = dict(NESTED_FORM, cond={"node": "In", "values": ["a/b"]})
    assert codes([mk("bound", slash)], NESTED) == ["F021"]
    assert codes([mk("bound", path_bound())]) == []


# ---- cause 7 over every value position ---------------------------------------------------------------


def _db_codes(v, where="accepted") -> list[str]:
    form = path_bound(
        **{where: {"status": 200 if where == "accepted" else 404, "db": {"equals": {"total": v}}}}
    )
    return codes([mk("bound", form)])


def test_pc3_an_outcome_db_value_that_is_a_node_name_is_f017_and_another_non_literal_f001():
    assert _db_codes({"param": "GET /orders/{order_id}", "path": "path.order_id"}) == ["F017"]
    assert _db_codes({"sub": [1]}) == ["F017"]
    assert _db_codes([1, 2]) == ["F001"]
    assert _db_codes([1, 2], "refused") == ["F001"]
    assert _db_codes({"count": 1}, "refused") == ["F017"]
    assert _db_codes(1.5) == []


def test_hz8_the_node_is_checked_before_the_outcome_value_is_called_a_literal():
    assert "F004" not in _db_codes({"param": "x", "path": "path.y"})
    assert "F004" not in _db_codes([1, 2])


def test_hz9_given_row_values_follow_the_same_order():
    def given(v) -> list[str]:
        return codes([mk("bound", path_bound(given={"orders": [{"id": 1, "user_id": v}]}))])

    assert given({"param": "GET /orders", "path": "query.x"}) == ["F017"]
    assert given([1, 2]) == ["F001"]
    assert given(1) == []
    assert codes([mk("bound", path_bound(given={"orders": [{"id": 1, "user_id": "x"}]}))]) == [
        "F004"
    ]


# ---- Amendment 2 (b): a refusal whose db cannot be asserted is noted, not dropped ----------------------------


def test_hz10_a_refusal_with_no_assertable_db_is_a_note(gen):
    notes = [n for n in gen.notes if n.startswith("F3:")]
    assert notes == [
        "F3: db_unchecked:other_post_orders_order_id_cancel",
        "F3: db_unchecked:anon_post_orders_order_id_cancel",
    ]
    assert first(gen, "form:F3:other_post_orders_order_id_cancel").expect_db is None
    own = {"table": "Order", "scope": "owner", "endpoints": ["POST /orders/{order_id}/cancel"], "owner": "ann", "other": "ben",
           "row": "/orders/1", "given": ORDER1, "refused_other": {"status": 404}, "refused_anon": {"status": 401}}  # fmt: skip
    g = F.generate(SPEC, [mk("ownership", own)])
    assert g.notes == ()
    assert first(g, "form:F90:other_post_orders_order_id_cancel").expect_db is not None


# ---- Amendment 2 (c): counted rows distinct on the columns a sibling limit counts by -------------------------


def test_hz11_counted_rows_are_pairwise_distinct_on_a_column_a_sibling_row_limit_counts_by(gen):
    f4 = first_given(gen, "form:F4:at_limit")
    f5 = first_given(gen, "form:F5:at_limit")
    assert len({r["product_id"] for r in f4}) == len(f4) == 3
    assert len({r["order_id"] for r in f5}) == len(f5) == 2
    assert all(r["order_id"] == 20 for r in f4)
    assert all(r["product_id"] == 1 for r in f5)


def first_given(g: F.Generated, name: str) -> list[dict]:
    return next(s for s in g.scenarios if s.name == name).given["OrderItem"]


def test_hz11_a_lone_row_limit_is_generated_as_before(fset):
    only = [r for r in fset.generating() if r.form.id == "F4"]
    g = F.generate(SPEC, only)
    assert {r["product_id"] for r in first_given(g, "form:F4:at_limit")} == {1}


# ---- (e) follows from (a): class e sees the new path-bound cases ----------------------------------------------


def _own(accepted: int = 200) -> dict:
    return {"table": "Order", "scope": "owner", "endpoints": ["GET /orders/{order_id}"], "owner": "ann", "other": "ben",
            "row": "/orders/1", "given": ORDER1, "accepted": {"GET /orders/{order_id}": {"status": accepted}},
            "refused_other": {"status": 404}, "refused_anon": {"status": 401}}  # fmt: skip


def test_hz12_class_e_compares_a_path_bound_case_with_an_ownership_case():
    items = T2.contradictions(
        SPEC,
        [
            mk("bound", path_bound(accepted={"status": 202}), "F1"),
            mk("ownership", _own(), "F2", "R7"),
        ],
    )
    e = [i for i in items if i["class"] == "e"]
    assert e and e[0]["witness"]["request"] == ["GET", "/orders/1"]
    assert not [
        i
        for i in T2.contradictions(
            SPEC, [mk("bound", path_bound(), "F1"), mk("ownership", _own(), "F2", "R7")]
        )
        if i["class"] == "e"
    ]


def test_hz13_two_cases_on_different_paths_are_not_one_request():
    a = mk("bound", path_bound(cond={"node": "Cmp", "op": "ge", "value": 1}), "F1")
    b = mk(
        "bound",
        path_bound(cond={"node": "Cmp", "op": "ge", "value": 5}, request={"path": "/orders/5"}),
        "F2",
        "R7",
    )
    assert [i for i in T2.contradictions(SPEC, [a, b]) if i["class"] == "e"] == []


# ---- byte-compat: no new key, a form without a path or query bound generates what it did -----------------------


def test_hz14_the_generated_view_has_no_new_key(gen):
    view = F.generated_view(gen)
    assert set(view) == {"scenarios", "invariants", "links", "no_invariant", "notes"}
    for sc in view["scenarios"]:
        assert set(sc) == {"name", "given", "steps"}
        for st in sc["steps"]:
            assert set(st) == {
                "method",
                "path",
                "user",
                "json",
                "params",
                "expect_status",
                "expect_db",
            }
    assert "path_bound" not in repr(view)


def test_a_body_bound_is_generated_exactly_as_in_round_2():
    form = {
        "field": {"param": "POST /orders/{}/items", "path": "body.quantity"}, "cond": {"op": "ge", "value": 1},
        "endpoint": "POST /orders/{}/items", "caller": "ann",
        "request": {"path": "/orders/20/items", "json": {"product_id": 1, "quantity": 1}},
        "given": {"orders": [{"id": 20, "user_id": 1, "status": "pending", "total": 0.0}]},
        "accepted": {"status": 201}, "refused": {"status": 422},
    }  # fmt: skip
    g = F.generate(SPEC, [mk("bound", form)])
    assert first(g, "form:F90:beyond").expect_db == {
        "OrderItem": {"where": {"product_id": 1, "quantity": 0}, "count": 0}
    }
    assert first(g, "form:F90:beyond").path == "/orders/20/items"
    assert g.notes == ()


# ---- HZ15: the scorer is hashed; an edit after the freeze is caught by PH ------------------------------------------


def test_hz15_the_runner_files_match_runner3_sha256():
    data = REPO / "docs/results/data/2026-10-18-typed-conditions-round3/runner3.sha256"
    assert data.is_file(), "runner3.sha256 is written at the freeze, before the mutants run"
    spec = importlib.util.spec_from_file_location(
        "forms_probes3_t", REPO / "dogfood/forms_probes3.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.REPO == REPO
    assert mod.check_hash_file(data) == []
