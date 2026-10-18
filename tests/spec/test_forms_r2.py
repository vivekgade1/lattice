# ruff: noqa: E501
"""Typed rule conditions, round 2 (Spike H2): the per-cause fixes of D0 on the mini-shop.

One dev case per cause 0-7 (``tests/spec/fixtures/forms_r2``) and the named kill tests of the round-2
mutants HY1-HY16 of ``dogfood/forms_mutants2.py``
(docs/results/2026-10-17-typed-conditions-round2-prereg.md, sections 2 and 4).
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import pytest

from lattice.spec import forms as F
from lattice.spec import forms_tier2 as T2
from lattice.spec import impact_report as ir
from lattice.spec.check import check
from lattice.spec.model import Scenario, Step
from lattice.spec.rules import Trace
from tests.spec import minishop as M

FIX = Path(__file__).parent / "fixtures" / "forms_r2" / "minishop"
IMPACT = Path(__file__).parent / "fixtures" / "impact"
SPEC = M.SPEC
ADD = {"path": "/orders/20/items", "json": {"product_id": 1, "quantity": 1}}
PARENT = {"orders": [{"id": 20, "user_id": 1, "status": "pending", "total": 0.0}]}


def mk(kind: str, form: dict, fid: str = "F90", rule: str = "R6") -> F.Form:
    return F.Form(fid, rule, "0" * 64, kind, form, "approved", "hand", "t", F.form_sha(kind, form))


def limit(**kw) -> dict:
    form = {
        "table": "OrderItem", "by": ["order_id"], "max": 5, "endpoint": "POST /orders/{}/items",
        "caller": "ann", "request": ADD, "given": PARENT, "refused": {"status": 409},
    }  # fmt: skip
    form.update(kw)
    return form


def codes(forms) -> list[str]:
    return sorted({d.code for d in F.check_forms(SPEC, None, forms)})


@pytest.fixture(scope="module")
def trace() -> Trace:
    return Trace.load(FIX / "spec.trace.json")


@pytest.fixture(scope="module")
def fset(trace) -> F.FormSet:
    paths = F.forms_paths(FIX / "spec.trace.json")
    fs = F.load_set(trace, paths)
    assert fs is not None
    return fs


@pytest.fixture(scope="module")
def gen(fset) -> F.Generated:
    return F.generate(SPEC, fset.generating())


def case(gen: F.Generated, name: str) -> Scenario:
    return next(s for s in gen.scenarios if s.name == name)


def first(gen: F.Generated, name: str) -> Step:
    st = case(gen, name).steps[0]
    assert isinstance(st, Step)
    return st


# ---- the fixture as a whole -------------------------------------------------------------------


def test_the_round2_fixture_type_checks_and_every_form_is_current(fset, trace):
    assert F.check_forms(SPEC, trace, fset.approved) == []
    assert {r.state for r in fset.resolved} == {"current"}
    assert len(fset.generating()) == 7
    assert F.self_check(SPEC, F.generate(SPEC, fset.generating())) == []


@pytest.fixture(scope="module")
def ok_app(tmp_path_factory):
    return M.write_app(tmp_path_factory.mktemp("forms_r2_apps"), "ok")


def test_generated_checks_pass_on_the_ok_app_except_the_limit_the_app_lacks(ok_app, gen):
    rep = check(
        F.effective_spec(SPEC, gen),
        M.app_factory(ok_app[1]),
        M.build_db,
        layers="CD",
        login=M.login,
    )
    fails = sorted(str(f.rule) for f in rep.findings)
    assert fails == [
        "at most 5 OrderItem rows per order_id (form F5)",
        "scenario: form:F5:at_limit",
    ]


# ---- cause 0: the node tag is optional ----------------------------------------------------------


def test_pc0_an_untagged_condition_is_accepted_current_and_generates(fset, gen, trace):
    f1 = next(f for f in fset.approved if f.id == "F1")
    assert "node" not in f1.form["cond"] and f1.sha_ok
    assert F.check_forms(SPEC, trace, [f1]) == []
    assert "form:F1:invariant" in gen.names()
    assert next(r.state for r in fset.resolved if r.form.id == "F1") == "current"


def test_pc0_the_tag_is_inferred_from_the_keys():
    assert F.norm_cond({"op": "ge", "value": 1})["node"] == "Cmp"
    assert F.norm_cond({"op": "max_len", "value": 3})["node"] == "Len"
    assert F.norm_cond({"values": [1, 2]})["node"] == "In"
    assert F.norm_cond({"node": "Cmp", "op": "ge", "value": 1})["node"] == "Cmp"
    assert "node" not in F.norm_cond({"any": []})


def test_pc0_a_tag_that_disagrees_with_the_keys_is_f001():
    cond = {"node": "Len", "op": "ge", "value": 1}
    assert codes([mk("bound", {"field": {"column": "OrderItem.quantity"}, "cond": cond})]) == [
        "F001"
    ]
    cond = {"node": "Cmp", "values": [1]}
    assert codes([mk("bound", {"field": {"column": "OrderItem.quantity"}, "cond": cond})]) == [
        "F001"
    ]


def test_hy1_untagged_conditions_are_not_refused(fset):
    bad = [d for d in F.check_forms(SPEC, None, fset.approved) if d.code in ("F001", "F017")]
    assert bad == []


def test_hy2_approved_sha_is_over_the_form_as_written(fset):
    assert all(f.sha_ok for f in fset.approved)
    assert {r.state for r in fset.resolved} == {"current"}
    f1 = next(f for f in fset.approved if f.id == "F1")
    assert f1.approved_sha == F.form_sha(f1.kind, f1.form)
    assert F.form_sha(f1.kind, F.normalised(f1).form) != f1.approved_sha  # the tagged text differs


# ---- cause 1: the accepted status is derived ----------------------------------------------------


def test_pc1_the_accepted_status_comes_from_the_specs_own_step(gen):
    assert F.accepted_status(SPEC, "POST /orders/{}/items") == 201
    assert F.accepted_status(SPEC, "POST /orders/{}/cancel") == 200
    assert first(gen, "form:F2:at_bound").expect_status == 201  # F2 gives no `accepted`
    assert not [n for n in gen.notes if n.startswith("accepted_default")]


def test_pc1_a_program_endpoint_with_no_step_is_200_without_a_note():
    assert F.accepted_status(SPEC, "GET /orders/{}") == 200


def _with_steps(*steps: Step):
    sc = Scenario("extra", steps=tuple(steps))
    return dataclasses.replace(SPEC, scenarios=(*SPEC.scenarios, sc))


def test_hy3_the_derived_status_is_used_not_a_fixed_200(gen):
    assert first(gen, "form:F2:at_bound").expect_status != 200
    assert first(gen, "form:F5:below").expect_status == 201


def test_hy4_two_different_2xx_statuses_fall_back_to_200_and_a_note():
    spec = _with_steps(Step("POST", "/orders/10/items", user="ann", json={}, expect_status=202))
    assert F.accepted_status(spec, "POST /orders/{}/items") is None
    form = mk("bound", {"field": {"column": "OrderItem.quantity"}, "cond": {"op": "ge", "value": 1},
                        "endpoint": "POST /orders/{}/items", "caller": "ann",
                        "request": {"path": "/orders/10/items", "json": {"product_id": 1, "quantity": 1}},
                        "refused": {"status": 422}})  # fmt: skip
    g = F.generate(spec, [form])
    assert next(s for s in g.scenarios if s.name.endswith("at_bound")).steps[0].expect_status == 200
    assert g.notes == ("accepted_default:POST /orders/{}/items",)


def test_an_endpoint_the_spec_never_exercises_is_200_with_a_note():
    spec = dataclasses.replace(SPEC, scenarios=(), programs={})
    assert F.accepted_status(spec, "GET /orders/{}") is None


# ---- cause 2: an ownership row is a key or a request path ---------------------------------------


def test_pc2_a_request_path_row_is_the_key_of_every_endpoint(gen):
    assert first(gen, "form:F3:owner_get_orders").path == "/orders/1"
    assert first(gen, "form:F3:owner_post_orders_cancel").path == "/orders/1/cancel"
    assert first(gen, "form:F3:other_post_orders_cancel").path == "/orders/1/cancel"


def test_pc2_a_literal_key_row_still_works():
    form = {"table": "Order", "scope": "owner", "endpoints": ["GET /orders/{}"], "owner": "ann", "other": "ben",
            "row": 1, "refused_other": {"status": 404}, "refused_anon": {"status": 401}}  # fmt: skip
    assert codes([mk("ownership", form)]) == []
    assert (
        first(F.generate(SPEC, [mk("ownership", form)]), "form:F90:owner_get_orders").path
        == "/orders/1"
    )


def test_pc2_a_path_matching_no_endpoint_is_f014():
    form = {"table": "Order", "scope": "owner", "endpoints": ["GET /orders/{}"], "owner": "ann", "other": "ben",
            "row": "/products/1", "refused_other": {"status": 404}, "refused_anon": {"status": 401}}  # fmt: skip
    assert codes([mk("ownership", form)]) == ["F014"]


def test_hy5_a_path_row_is_not_used_as_a_value(gen):
    assert "//" not in first(gen, "form:F3:owner_get_orders").path
    assert "orders/orders" not in first(gen, "form:F3:owner_get_orders").path


def test_hy6_the_key_is_the_value_bound_to_the_first_placeholder():
    assert F._row_key("/orders/7/items/9", ["GET /orders/{}/items/{}"]) == 7  # noqa: SLF001
    assert F._row_key("/orders/7", ["GET /orders/{}/items/{}"]) == 7  # noqa: SLF001
    assert F._row_key("/orders/x", ["GET /orders/{}"]) == "x"  # noqa: SLF001
    assert F._row_key("/nope/7", ["GET /orders/{}"]) is None  # noqa: SLF001
    assert F._row_key(5, ["GET /orders/{}"]) == 5  # noqa: SLF001


# ---- cause 3: a transition row is {key: Lit}; Lattice completes the rest -------------------------


def test_pc3_the_transition_row_is_completed(gen):
    row = case(gen, "form:F4:from_pending").given["Order"][0]
    assert row == {"id": 20, "user_id": 1, "total": 0.0, "status": "pending"}
    assert case(gen, "form:F4:refused_paid").given["Order"][0]["status"] == "paid"


def test_pc3_a_full_row_is_still_accepted():
    form = {"machine": "Order.status", "trigger": "POST /orders/{}/cancel", "caller": "ann",
            "request": {"path": "/orders/20/cancel"}, "row": {"id": 20, "user_id": 2, "total": 7.5},
            "from": ["pending"], "to": "cancelled", "accepted": {"status": 200}, "refused": {"status": 409}}  # fmt: skip
    g = F.generate(SPEC, [mk("transition", form)])
    assert case(g, "form:F90:from_pending").given["Order"][0] == {
        "id": 20,
        "user_id": 2,
        "total": 7.5,
        "status": "pending",
    }


def test_hy7_the_transition_row_is_completed_so_the_given_row_inserts(ok_app, gen):
    rep = check(
        F.effective_spec(SPEC, gen), M.app_factory(ok_app[1]), M.build_db, layers="D", login=M.login
    )
    assert not [f for f in rep.findings if "form:F4:" in str(f.rule)]
    assert all(
        c["user_id"] == 1
        for c in (case(gen, n).given["Order"][0] for n in ("form:F4:from_pending",))
    )


def test_hy8_the_owner_column_gets_the_callers_id():
    for caller, uid in (("ann", 1), ("ben", 2)):
        form = {"machine": "Order.status", "trigger": "POST /orders/{}/cancel", "caller": caller,
                "request": {"path": "/orders/20/cancel"}, "row": {"id": 20}, "from": ["pending"],
                "to": "cancelled", "accepted": {"status": 200}, "refused": {"status": 409}}  # fmt: skip
        g = F.generate(SPEC, [mk("transition", form)])
        assert case(g, "form:F90:from_pending").given["Order"][0]["user_id"] == uid


# ---- cause 4: row_limit counted rows are synthesised --------------------------------------------


def test_pc4_counted_rows_are_synthesised_from_a_parent_only_given(gen):
    below, at = case(gen, "form:F5:below"), case(gen, "form:F5:at_limit")
    assert [len(below.given["OrderItem"]), len(at.given["OrderItem"])] == [4, 5]
    assert below.given["orders"] == PARENT["orders"]
    rows = at.given["OrderItem"]
    assert {r["order_id"] for r in rows} == {20} and {r["product_id"] for r in rows} == {1}
    assert len({r["id"] for r in rows}) == 5 and min(r["id"] for r in rows) >= 1000
    assert first(gen, "form:F5:below").expect_db == {
        "OrderItem": {"where": {"order_id": 20}, "count": 5}
    }


def test_pc4_given_counted_rows_count_toward_the_limit_and_are_kept():
    two = [
        {"id": 100 + i, "order_id": 20, "product_id": 1, "quantity": 1, "price": 0.0}
        for i in range(2)
    ]
    g = F.generate(SPEC, [mk("row_limit", limit(given={**PARENT, "order_items": two}, max=5))])
    below, at = case(g, "form:F90:below"), case(g, "form:F90:at_limit")
    assert [len(below.given["OrderItem"]), len(at.given["OrderItem"])] == [4, 5]
    assert {100, 101} <= {r["id"] for r in below.given["OrderItem"]}
    assert "order_items" not in below.given  # folded into the schema-name key


def test_pc4_more_matching_given_rows_than_the_limit_allows_is_f011():
    rows = [
        {"id": 100 + i, "order_id": 20, "product_id": 1, "quantity": 1, "price": 0.0}
        for i in range(5)
    ]
    assert codes([mk("row_limit", limit(given={**PARENT, "order_items": rows}))]) == ["F011"]
    assert codes([mk("row_limit", limit(given={**PARENT, "order_items": rows[:4]}))]) == []


def test_pc4_a_row_limit_needs_no_counted_row_template_and_no_given_at_all():
    assert codes([mk("row_limit", limit())]) == []
    bare = limit()
    del bare["given"]
    assert codes([mk("row_limit", bare)]) == []


def test_pc4_a_column_nothing_binds_is_f021():
    assert codes([mk("row_limit", limit(by=["price"]))]) == ["F021"]
    no_product = limit(request={"path": "/orders/20/items", "json": {"quantity": 1}})
    assert codes([mk("row_limit", no_product)]) == ["F021"]


def test_hy9_given_counted_rows_are_not_replaced():
    test_pc4_given_counted_rows_count_toward_the_limit_and_are_kept()


def test_hy10_a_by_column_is_bound_to_the_first_placeholder():
    w = F._witness_of(
        "POST /orders/{}/items", {"path": "/orders/20/items", "json": {"quantity": 1}}
    )  # noqa: SLF001
    order_item = SPEC.profile.api.tables["OrderItem"]
    assert F.bind_column(SPEC, order_item, "order_id", w, "POST /orders/{}/items", "ann") == 20
    assert F.bind_column(SPEC, order_item, "price", w, "POST /orders/{}/items", "ann") is None
    order = SPEC.profile.api.tables["Order"]
    assert F.bind_column(SPEC, order, "user_id", w, "POST /orders/{}/items", "ben") == 2
    assert (
        codes(
            [
                mk(
                    "row_limit",
                    limit(
                        request={
                            "path": "/orders/20/items",
                            "json": {"quantity": 1, "product_id": 1},
                        }
                    ),
                )
            ]
        )
        == []
    )


def test_hy11_an_incomplete_form_is_f021_at_check_and_never_a_raise_at_generate():
    bad = mk("row_limit", limit(request={"path": "/orders/20/items", "json": {"quantity": 1}}))
    assert codes([bad]) == ["F021"]
    with pytest.raises(F.FormsError) as ei:
        F.generate(SPEC, [bad])
    assert ei.value.code == "F021"
    notes: list[str] = []
    assert T2.contradictions(SPEC, [bad, mk("row_limit", limit(), fid="F91")], notes) == []
    assert notes == ["pair_undecided:F90:F021"]


# ---- cause 5: a param bound needs no `stores` ----------------------------------------------------


def test_pc5_a_param_bound_without_stores_is_accepted_and_asserts_unchanged_on_the_derived_table(
    fset, gen, trace
):
    f6 = next(f for f in fset.approved if f.id == "F6")
    assert "stores" not in f6.form and F.check_forms(SPEC, trace, [f6]) == []
    assert first(gen, "form:F6:beyond").expect_db == {
        "OrderItem": {"where": {"product_id": 1, "quantity": 0}, "count": 0}
    }
    assert first(gen, "form:F6:at_bound").expect_db is None


def test_pc5_the_locus_falls_back_to_the_given_rows_with_a_note():
    form = {"field": {"param": "POST /orders/{}/items", "path": "body.quantity"}, "cond": {"op": "ge", "value": 1},
            "endpoint": "POST /orders/{}/items", "caller": "ann",
            "request": {"path": "/orders/20/items", "json": {"product_id": 1, "quantity": 1, "note": "x"}},
            "given": PARENT, "accepted": {"status": 201}, "refused": {"status": 422}}  # fmt: skip
    assert codes([mk("bound", form)]) == []
    g = F.generate(SPEC, [mk("bound", form)])
    assert g.notes == ("F90: db_locus:given_only",)
    assert first(g, "form:F90:beyond").expect_db == {
        "Order": {
            "where": {"id": 20},
            "equals": {"id": 20, "user_id": 1, "status": "pending", "total": 0.0},
        }
    }


def test_pc5_a_program_endpoint_names_its_table():
    named = "GET /orders/{}"
    w = F._witness_of(named, {"path": "/orders/1"})  # noqa: SLF001
    assert F.derive_table(SPEC, named, w) == "Order"
    assert (
        F.derive_table(
            M.attack_spec(), "POST /orders/{}/items", F._witness_of("POST /orders/{}/items", ADD)
        )
        == "OrderItem"
    )  # noqa: SLF001


def test_hy12_a_param_bound_without_stores_is_not_refused(fset, trace):
    assert [d for d in F.check_forms(SPEC, trace, fset.approved) if d.code == "F002"] == []
    form = {
        "field": {"param": "POST /orders/{}/items", "path": "body.quantity"},
        "cond": {"op": "ge", "value": 1}, "endpoint": "POST /orders/{}/items", "caller": "ann",
        "request": ADD, "given": PARENT, "refused": {"status": 422, "db": "unchanged"},
        "accepted": {"status": 201, "db": {"count": 1}},
    }  # fmt: skip
    assert codes([mk("bound", form)]) == []


def test_hy13_the_derived_table_is_used(gen):
    assert first(gen, "form:F6:beyond").expect_db is not None
    assert first(gen, "form:F6:beyond").expect_db.keys() == {"OrderItem"}


# ---- cause 6: a column-only bound is an invariant only (class K6) ---------------------------------


def test_pc6_a_column_only_bound_generates_an_invariant_and_no_scenario(gen):
    assert "form:F7:invariant" in gen.names()
    assert not [s for s in gen.scenarios if s.name.startswith("form:F7:")]


# ---- cause 7: node and key validity before the literal's type ------------------------------------


def _cond_codes(cond: dict) -> list[str]:
    return codes([mk("bound", {"field": {"column": "Product.stock"}, "cond": cond})])


def test_pc7_a_value_that_is_a_node_name_is_f017():
    for v in ({"param": "POST /orders/{}/items", "path": "body.quantity"}, {"column": "Product.price"},
              {"sum": ["a"]}, {"now": True}, {"count": 1}, {"add": [1, 2]}):  # fmt: skip
        assert _cond_codes({"op": "ge", "value": v}) == ["F017"], v
    assert _cond_codes({"values": [1, {"any": []}]}) == ["F017"]
    form = {"field": {"column": "User.name"}, "cond": {"op": "min_len", "value": {"sum": []}}}
    assert codes([mk("bound", form)]) == ["F017"]


def test_pc7_any_other_non_literal_value_is_f001_and_a_literal_keeps_its_type_code():
    assert _cond_codes({"op": "ge", "value": [1, 2]}) == ["F001"]
    assert _cond_codes({"op": "ge", "value": {"foo": 1}}) == ["F001"]
    assert _cond_codes({"op": "ge", "value": None}) == ["F001"]
    assert _cond_codes({"op": "ge", "value": 1.5}) == ["F004"]
    assert _cond_codes({"op": "ge", "value": "x"}) == ["F004"]


def test_pc7_an_untagged_node_that_names_a_construct_is_f017():
    assert _cond_codes({"any": [{"op": "ge", "value": 1}]}) == ["F017"]
    assert _cond_codes({"gizmo": 1}) == ["F001"]


def test_hy14_the_node_is_checked_before_the_literal_is_typed():
    assert _cond_codes({"op": "ge", "value": {"param": "POST /x", "path": "body.q"}}) == ["F017"]
    assert "F004" not in _cond_codes({"op": "ge", "value": {"column": "Product.price"}})


# ---- D6: the impact report gains the form items ---------------------------------------------------


def _project(tmp_path: Path, name: str, extra: bool) -> Path:
    d = tmp_path / name
    shutil.copytree(Path(__file__).parent / "fixtures" / "forms" / "minishop", d)
    if extra:
        import json

        fp = d / "REQUIREMENTS.forms.json"
        data = json.loads(fp.read_text())
        e = json.loads(json.dumps(next(f for f in data["forms"] if f["id"] == "F3")))
        e.update(id="F90", rule="R7")
        e["form"]["cond"] = {"node": "Cmp", "op": "lt", "value": 1}
        e["approved_sha"] = F.form_sha(e["kind"], e["form"])
        data["forms"].append(e)
        fp.write_text(json.dumps(data, indent=2) + "\n")
    return d


def _report(project: Path, candidate: Path) -> dict:
    inp = ir.build_inputs(project=project, candidate=candidate)
    return ir.build_report(inp, tiers="2", baseline="none", flows_on=False)


def test_hy15_the_impact_report_gains_exactly_the_candidates_new_form_items(tmp_path):
    base, cand = _project(tmp_path, "base", False), _project(tmp_path, "cand", True)
    items = [
        i for i in _report(base, cand)["tier2"]["determined"] if i["kind"] == "form_contradiction"
    ]
    assert items and {i["tier"] for i in items} == {2}
    assert all("F90" in i["forms"] for i in items)
    same = _report(base, base)["tier2"]["determined"]
    assert not [i for i in same if i["kind"] == "form_contradiction"]


def test_hy16_without_a_forms_file_the_report_is_the_plain_one():
    base, cand = ir.read_side(IMPACT / "base"), ir.read_side(IMPACT / "reword")
    assert base.forms is None and cand.forms is None and ir.forms_items(base, cand) is None
    rep = ir.build_report(ir.build_inputs(project=IMPACT / "base", candidate=IMPACT / "reword"),
                          tiers="2", baseline="none", flows_on=False)  # fmt: skip
    assert not [i for i in rep["tier2"]["determined"] if i.get("kind") == "form_contradiction"]
