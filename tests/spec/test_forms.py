"""Typed rule conditions (Spike H): the type checker, the generator, the states, the approval
guard, the Tier 2 contradictions and the byte-compat promise, on the mini-shop.

The ``test_hx*`` tests are the named kill tests of the implementation mutants of
``dogfood/forms_mutants.py`` (docs/results/2026-10-17-typed-conditions-prereg.md, section 4).
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lattice.spec import forms as F
from lattice.spec import forms_tier2 as T2
from lattice.spec import rationale as R
from lattice.spec.check import check
from lattice.spec.model import Machine, Scenario, Spec, Step
from lattice.spec.rules import Trace
from tests.spec import minishop as M

FIX = Path(__file__).parent / "fixtures" / "forms" / "minishop"
SPEC = M.SPEC
ROW = {"id": 1, "order_id": 10, "product_id": 50, "quantity": 1, "price": 0.0}
ADD = {"path": "/orders/10/items", "json": {"product_id": 50, "quantity": 5}}
GIVEN = {
    "orders": [{"id": 10, "user_id": 1, "status": "pending", "total": 0.0}],
    "products": [{"id": 50, "name": "bulk", "price": 1.0, "stock": 1000}],
}


def mk(kind: str, form: dict, fid: str = "F9", rule: str = "R6", **kw) -> F.Form:
    return F.Form(
        fid, rule, "a" * 64, kind, form, kw.pop("status", "approved"), "hand",
        "t", F.form_sha(kind, form), **kw,
    )  # fmt: skip


def bound(cond: dict, column: str = "OrderItem.quantity", **extra) -> dict:
    form = {"field": {"column": column}, "cond": cond}
    form.update(extra)
    return form


def ge(v):
    return {"node": "Cmp", "op": "ge", "value": v}


def with_endpoint(cond: dict, **extra) -> dict:
    return bound(
        cond, endpoint="POST /orders/{}/items", caller="ann", request=ADD, given=GIVEN,
        accepted={"status": 201}, refused={"status": 422}, **extra,
    )  # fmt: skip


def codes(forms, trace=None) -> list[str]:
    return [d.code for d in F.check_forms(SPEC, trace, forms)]


@pytest.fixture(scope="module")
def trace() -> Trace:
    return Trace.load(FIX / "spec.trace.json")


@pytest.fixture(scope="module")
def paths() -> F.FormsPaths:
    p = F.forms_paths(FIX / "spec.trace.json")
    assert p is not None
    return p


@pytest.fixture(scope="module")
def fset(trace, paths) -> F.FormSet:
    fs = F.load_set(trace, paths)
    assert fs is not None
    return fs


@pytest.fixture(scope="module")
def gen(fset) -> F.Generated:
    return F.generate(SPEC, fset.generating())


# ---- the dev forms ---------------------------------------------------------------------------


def test_the_dev_forms_type_check_and_are_all_current(fset, trace):
    assert F.check_forms(SPEC, trace, fset.approved) == []
    assert {r.state for r in fset.resolved} == {"current"}
    assert len(fset.generating()) == 7


def test_the_generated_elements_pass_the_generator_self_check(gen):
    assert F.self_check(SPEC, gen) == []


def test_generation_is_deterministic_and_pure(fset):
    a = F.generate(SPEC, fset.generating())
    b = F.generate(SPEC, list(reversed(fset.generating())))
    assert F.generated_view(a) == F.generated_view(b)
    assert [s.name for s in a.scenarios] == [s.name for s in b.scenarios]


def test_generated_names_are_reserved_and_linked_to_the_rule(gen):
    assert all(n.startswith("form:") for n in gen.names())
    assert list(gen.links["R6"]) == [
        "invariant:form:F3:invariant",
        "scenario:form:F3:at_bound",
        "scenario:form:F3:beyond",
    ]


def test_effective_spec_adds_without_changing_the_spec(gen):
    eff = F.effective_spec(SPEC, gen)
    assert len(eff.scenarios) == len(SPEC.scenarios) + len(gen.scenarios)
    assert len(SPEC.scenarios) == 2 and len(SPEC.invariants) == 2


def test_the_effective_trace_merges_links_in_memory(trace, gen):
    t2 = F.effective_trace(trace, gen)
    assert "scenario:form:F3:beyond" in t2.links["R6"]
    assert "R6" not in trace.links


# ---- the type checker --------------------------------------------------------------------------


def test_f001_unknown_key_and_node():
    assert "F001" in codes([mk("bound", bound(ge(1), color="red"))])
    assert "F001" in codes([mk("bound", bound({"node": "Nope", "value": 1}))])
    assert "F001" in codes([mk("nothing", {})])


def test_f002_unknown_table_and_column():
    assert "F002" in codes([mk("bound", bound(ge(1), column="Nope.x"))])
    assert "F002" in codes([mk("bound", bound(ge(1), column="OrderItem.nope"))])


def test_f003_unknown_endpoint():
    f = with_endpoint(ge(1))
    f["endpoint"] = "POST /nowhere"
    assert "F003" in codes([mk("bound", f)])


def test_f004_float_for_an_int_field():
    assert "F004" in codes([mk("bound", bound(ge(1.5)))])
    assert codes([mk("bound", bound(ge(1), column="Product.price"))]) == []


def test_f005_in_outside_one_of():
    cond = {"node": "In", "values": ["pending", "lost"]}
    assert codes([mk("bound", bound(cond, column="Order.status"))]) == ["F005"]


def test_f006_ordering_a_one_of_column():
    assert "F006" in codes([mk("bound", bound(ge(1), column="Order.status"))])


def test_f007_unknown_user_handle():
    f = with_endpoint(ge(1))
    f["caller"] = "zed"
    assert "F007" in codes([mk("bound", f)])


def _tr(**kw):
    form = {
        "machine": "Order.status", "trigger": "POST /orders/{}/cancel", "caller": "ann",
        "request": {"path": "/orders/10/cancel"}, "row": {"id": 10, "user_id": 1, "total": 0.0},
        "from": ["pending"], "to": "cancelled", "accepted": {"status": 200},
        "refused": {"status": 409},
    }  # fmt: skip
    form.update(kw)
    return mk("transition", form, rule="R1")


def test_f008_unknown_state():
    assert "F008" in codes([_tr(to="vanished")])
    assert "F008" in codes([_tr(**{"from": ["pending", "lost"]})])


def test_f009_empty_from_and_to_among_from():
    assert "F009" in codes([_tr(**{"from": []})])
    assert "F009" in codes([_tr(**{"from": ["pending", "cancelled"]})])


def test_f010_reserved_name_in_the_hand_written_spec():
    bad = Spec(
        profile=SPEC.profile, programs=dict(SPEC.programs), scenarios=(Scenario("form:F1:x"),),
        users=dict(SPEC.users),
    )  # fmt: skip
    assert "F010" in [d.code for d in F.check_forms(bad, None, [])]


def test_f011_limit_below_one_and_negative_length():
    row = {
        "table": "OrderItem", "by": ["order_id"], "max": 0, "endpoint": "POST /orders/{}/items",
        "caller": "ann", "request": ADD, "refused": {"status": 409}, "given": {
            "order_items": [ROW]
        },
    }  # fmt: skip
    assert "F011" in codes([mk("row_limit", row)])
    neg = bound({"node": "Len", "op": "max_len", "value": -1}, column="User.name")
    assert "F011" in codes([mk("bound", neg)])


def test_f012_where_on_a_column_not_in_the_table():
    row = {
        "table": "OrderItem", "by": ["order_id"], "max": 2, "where": {"nope": 1},
        "endpoint": "POST /orders/{}/items", "caller": "ann", "request": ADD,
        "refused": {"status": 409},
        "given": {"order_items": [ROW]},
    }  # fmt: skip
    assert "F012" in codes([mk("row_limit", row)])


def test_f013_ownership_scope_on_a_table_without_an_owner():
    own = {
        "table": "Product", "scope": "owner", "endpoints": ["GET /orders/{}"], "owner": "ann",
        "other": "ben", "row": 1, "refused_other": {"status": 404}, "refused_anon": {"status": 401},
    }  # fmt: skip
    assert "F013" in codes([mk("ownership", own)])


def test_f014_witness_request_that_does_not_match_the_endpoint():
    f = with_endpoint(ge(1))
    f["request"] = {"path": "/orders/10/cancel"}
    assert "F014" in codes([mk("bound", f)])


def test_f015_two_forms_with_one_id():
    a = mk("bound", bound(ge(1)), fid="F1")
    assert "F015" in codes([a, a])


def test_f016_a_form_for_a_rule_not_in_the_trace(trace):
    assert "F016" in codes([mk("bound", bound(ge(1)), rule="R99")], trace)


def test_f017_a_form_the_grammar_does_not_express():
    assert codes([mk("bound", bound({"node": "now"}))]) == ["F017"]
    assert "F017" in codes([mk("bound", bound(ge(1), sum="x"))])
    assert "F017" in codes([mk("bound", bound(ge(1), **{"or": []}))])


def test_f018_status_outside_100_599():
    f = with_endpoint(ge(1))
    f["refused"] = {"status": 99}
    assert "F018" in codes([mk("bound", f)])


def test_f019_an_edge_the_machine_does_not_have():
    assert "F019" in codes([_tr(**{"from": ["shipped"], "to": "pending"})])


def test_valid_forms_of_every_kind_are_accepted(fset):
    assert F.check_forms(SPEC, None, fset.approved) == []


# ---- values around a bound ---------------------------------------------------------------------


def test_bound_values_int_float_in_len():
    cmp = lambda op, v: {"node": "Cmp", "op": op, "value": v}  # noqa: E731
    assert F.bound_values(cmp("ge", 1), F.Scalar.INT) == (1, 0)
    assert F.bound_values(cmp("gt", 3), F.Scalar.INT) == (4, 3)
    assert F.bound_values(cmp("le", 100), F.Scalar.INT) == (100, 101)
    assert F.bound_values(cmp("lt", 4), F.Scalar.INT) == (3, 4)
    at, beyond = F.bound_values(cmp("ge", 2.5), F.Scalar.FLOAT)
    assert at == 2.5 and 2.5 - beyond > 2e-9 * 2.5
    assert F.bound_values({"node": "In", "values": ["a", "~"]}, None) == ("a", "~~")
    assert F.bound_values({"node": "Len", "op": "max_len", "value": 3}, None) == ("aaa", "aaaa")
    assert F.bound_values({"node": "Len", "op": "min_len", "value": 0}, None) == ("", None)


# ---- generation: the cases ---------------------------------------------------------------------


def _by_name(gen, name):
    return next(s for s in gen.scenarios if s.name == name)


def test_bound_generates_at_bound_and_beyond(gen):
    at = _by_name(gen, "form:F3:at_bound").steps[0]
    bey = _by_name(gen, "form:F3:beyond").steps[0]
    assert (at.json["quantity"], at.expect_status) == (1, 201)
    assert (bey.json["quantity"], bey.expect_status) == (0, 422)
    assert bey.expect_db == {"OrderItem": {"where": {"product_id": 50, "quantity": 0}, "count": 0}}


def test_a_bound_on_a_column_without_an_endpoint_is_an_invariant_only():
    g = F.generate(SPEC, [mk("bound", bound(ge(0), column="Product.stock"), rule="R4")])
    assert [i.expr for i in g.invariants] == ["Product.stock >= 0"]
    assert g.scenarios == ()


def test_a_length_bound_has_no_invariant():
    cond = {"node": "Len", "op": "max_len", "value": 5}
    g = F.generate(SPEC, [mk("bound", bound(cond, column="User.name"))])
    assert g.invariants == () and g.no_invariant == {"F9": "length"}


def test_row_limit_generates_a_count_bound_and_two_cases(gen):
    inv = next(i for i in gen.invariants if i.name == "form:F7:invariant")
    assert inv.expr == "count(OrderItem, by='order_id') <= 5"
    below = _by_name(gen, "form:F7:below")
    at = _by_name(gen, "form:F7:at_limit")
    assert len(below.given["OrderItem"]) == 4 and len(at.given["OrderItem"]) == 5
    assert at.steps[0].expect_status == 409


def test_ownership_generates_owner_other_and_anon_per_endpoint(gen):
    names = {s.name for s in gen.scenarios if s.name.startswith("form:F6:")}
    assert len(names) == 6
    anon = _by_name(gen, "form:F6:anon_get_orders").steps[0]
    assert anon.user is None and anon.expect_status == 401


def test_transition_generates_from_and_refused_states(gen):
    names = {s.name for s in gen.scenarios if s.name.startswith("form:F1:")}
    assert names == {
        "form:F1:from_pending", "form:F1:refused_paid", "form:F1:refused_shipped",
        "form:F1:refused_cancelled",
    }  # fmt: skip
    ok = _by_name(gen, "form:F1:from_pending").steps[0]
    assert ok.expect_db == {"Order": {"where": {"id": 10}, "equals": {"status": "cancelled"}}}


def test_the_generated_invariants_compile_for_sqlite_and_mysql(gen):
    from lattice.spec.invariants import compile_invariant

    for inv in gen.invariants:
        for dialect in ("sqlite", "mysql"):
            assert compile_invariant(inv, SPEC.profile, dialect)


# ---- a check run -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def apps(tmp_path_factory):
    root = tmp_path_factory.mktemp("forms_apps")
    out = {v: M.write_app(root, v) for v in ("ok", "wrong_answer")}
    out["sec_auth"] = M.write_sec_app(root, "auth")
    return out


def _run(spec, app, layers="CD"):
    return check(spec, M.app_factory(app[1]), M.build_db, layers=layers, login=M.login)


def _g_fails(report):
    return sorted(
        str(f.rule) for f in report.findings if "form:" in str(f.rule) or "(form F" in str(f.rule)
    )


def test_generated_checks_pass_on_the_ok_app_except_the_limit_the_app_lacks(apps, gen):
    rep = _run(F.effective_spec(SPEC, gen), apps["ok"])
    assert _g_fails(rep) == [
        "at most 5 OrderItem rows per order_id (form F7)",
        "scenario: form:F7:at_limit",
    ]
    assert len(rep.findings) == 2


def test_a_generated_ownership_check_catches_a_mutant_the_hand_written_misses(apps, gen):
    eff = F.effective_spec(SPEC, gen)
    assert _g_fails(_run(eff, apps["wrong_answer"])) == [
        "at most 5 OrderItem rows per order_id (form F7)",
        "scenario: form:F6:other_get_orders",
        "scenario: form:F7:at_limit",
    ]
    assert _run(SPEC, apps["wrong_answer"]).findings == []


def test_check_with_forms_without_a_forms_file_is_the_plain_check(apps, tmp_path, trace):
    nofile = F.FormsPaths(tmp_path / "x.forms.json", tmp_path / "x.forms.proposed.json")
    rep, g = F.check_with_forms(
        SPEC, trace, nofile, M.app_factory(apps["ok"][1]), M.build_db, layers="CD", login=M.login
    )
    assert g is None
    plain = _run(SPEC, apps["ok"])
    assert set(F.report_dict(rep, g)) == set(plain.to_dict())
    assert "forms" not in F.report_dict(rep, g)


# ---- states and approval -----------------------------------------------------------------------


def test_edited_form_is_not_current_and_generates_nothing(fset, trace):
    f = copy.deepcopy(fset.approved[2])
    form = copy.deepcopy(dict(f.form))
    form["cond"] = ge(2)
    edited = F.Form(
        f.id, f.rule, f.anchor, f.kind, form, f.status, f.author, f.approved_by, f.approved_sha
    )
    r = F.resolve(trace, [edited])
    assert [x.state for x in r] == ["edited"] and F.usable(r) == []


def test_a_reworded_rule_makes_the_form_stale_or_moved(fset, trace):
    rules = list(trace.rules)
    rules[5] = type(rules[5])(rules[5].id, "A line's quantity is positive.", "s", 1)
    t = Trace(trace.source, rules)
    st = {r.form.id: r.state for r in F.resolve(t, fset.approved)}
    assert st["F3"] == "stale" and st["F4"] == "current"
    moved_rules = [rules[0], *[trace.rules[5]], *rules[1:5], *rules[6:]]
    ren = [type(r)(f"R{i + 1}", r.text, r.section, r.line) for i, r in enumerate(moved_rules)]
    st2 = {r.form.id: (r.state, r.rule) for r in F.resolve(Trace(trace.source, ren), fset.approved)}
    assert st2["F3"] == ("moved", "R2")


def test_two_rules_with_one_text_make_the_form_ambiguous(fset, trace):
    rules = [*trace.rules, type(trace.rules[5])("R11", trace.rules[5].text, "s", 99)]
    st = {r.form.id: r.state for r in F.resolve(Trace(trace.source, rules), fset.approved)}
    assert st["F3"] == "current"
    rules2 = list(rules)
    rules2[5] = type(rules[5])("R6", "something else", "s", 1)
    rules2.append(type(rules[5])("R12", trace.rules[5].text, "s", 100))
    st2 = {r.form.id: r.state for r in F.resolve(Trace(trace.source, rules2), fset.approved)}
    assert st2["F3"] == "ambiguous"


def _copy_fixture(tmp_path) -> Path:
    d = tmp_path / "proj"
    shutil.copytree(FIX, d)
    return d


def _cli(*argv):
    return F.main([*argv], generator=None, prompt=None)


def test_a_proposal_generates_nothing_hx7(tmp_path, trace):
    d = _copy_fixture(tmp_path)
    auth = d / "REQUIREMENTS.forms.json"
    prop = d / "REQUIREMENTS.forms.proposed.json"
    prop.write_text(auth.read_text().replace('"status": "approved"', '"status": "proposed"'))
    auth.unlink()
    fs = F.load_set(trace, F.forms_paths(d / "spec.trace.json"))
    assert fs is not None and fs.generating() == [] and len(fs.proposals) == 7
    assert F.generated_for(SPEC, trace, F.forms_paths(d / "spec.trace.json")) is None


def test_an_edited_approved_form_generates_nothing_hx8(tmp_path, trace):
    d = _copy_fixture(tmp_path)
    auth = d / "REQUIREMENTS.forms.json"
    data = json.loads(auth.read_text())
    data["forms"][1]["form"]["cond"]["value"] = 1  # F2 hand-edited: ge 0 -> ge 1
    auth.write_text(json.dumps(data))
    fs = F.load_set(trace, F.forms_paths(d / "spec.trace.json"))
    assert fs is not None
    assert {r.form.id: r.state for r in fs.resolved}["F2"] == "edited"
    assert "F2" not in [r.form.id for r in fs.generating()]
    g = F.generate(SPEC, fs.generating())
    assert "form:F2:invariant" not in g.names()


def test_approve_refuses_without_a_terminal_and_inside_claude_code(
    tmp_path, monkeypatch, trace, capsys
):
    d = _copy_fixture(tmp_path)
    prop = d / "REQUIREMENTS.forms.proposed.json"
    src = json.loads((d / "REQUIREMENTS.forms.json").read_text())
    for f in src["forms"]:
        f.update(status="proposed", approved_by=None, approved_sha=None)
    prop.write_text(json.dumps(src))
    (d / "REQUIREMENTS.forms.json").unlink()
    argv = ["approve", "--trace", str(d / "spec.trace.json"), "--spec", "tests.spec.minishop:SPEC",
            "--from", str(prop), "--form", "F2", "--approver", "vivek"]  # fmt: skip
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr(R, "_terminal", lambda: False)
    assert _cli(*argv) == 2
    assert "approve_needs_terminal" in capsys.readouterr().err
    monkeypatch.setattr(R, "_terminal", lambda: True)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert _cli(*argv) == 2
    assert not (d / "REQUIREMENTS.forms.json").exists()
    monkeypatch.delenv("CLAUDECODE")
    assert _cli(*argv) == 0
    fs = F.load_set(trace, F.forms_paths(d / "spec.trace.json"))
    assert fs is not None and [r.state for r in fs.resolved] == ["current"]


def test_draft_writes_only_the_proposals_file(tmp_path, trace):
    d = _copy_fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in d.iterdir()}
    paths = F.forms_paths(d / "spec.trace.json")
    assert paths is not None

    class Gen:
        def generate(self, system, messages):
            from types import SimpleNamespace

            reply = {
                "kind": "bound",
                "form": bound(ge(1)),
                "status": "approved",
                "approved_by": "x",
            }
            return SimpleNamespace(text=json.dumps(reply), cost_usd=0.0)

    out = d / "REQUIREMENTS.forms.proposed.json"
    out.unlink(missing_ok=True)
    rep = F.draft_forms(SPEC, trace, paths, rules=["R6"], out=out, generator=Gen(), model="m")
    assert rep["drafted"] == 1
    after = {p.name: p.read_bytes() for p in d.iterdir()}
    assert set(after) - set(before) == {out.name}
    assert {k: v for k, v in after.items() if k != out.name} == before
    prop = json.loads(out.read_text())["forms"][0]
    assert (
        prop["status"] == "proposed"
        and prop["approved_by"] is None
        and prop["approved_sha"] is None
    )
    with pytest.raises(F.FormsError):
        F.draft_forms(SPEC, trace, paths, rules=["R6"], out=paths.forms, generator=Gen(), model="m")


def test_coverage_counts_formed_not_expressible_and_english(fset, trace):
    cov = F.coverage(trace, fset)
    states = {x["rule"]: x["state"] for x in cov["rules"]}
    assert states["R6"] == "formed" and states["R2"] == "not_expressible:effect_value"
    assert states["R5"] == "english"
    assert cov["counts"] == {"english": 1, "formed": 7, "not_expressible": 2}


def test_the_cli_commands_run_and_write_nothing(tmp_path, capsys):
    d = _copy_fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in d.iterdir()}
    base = ["--trace", str(d / "spec.trace.json")]
    spec = ["--spec", "tests.spec.minishop:SPEC"]
    assert _cli("show", *base) == 0
    assert _cli("check", *base, *spec) == 0
    assert _cli("generate", *base, *spec) == 0
    assert _cli("contradictions", *base, *spec) == 0
    assert _cli("coverage", *base) == 0
    capsys.readouterr()
    assert {p.name: p.read_bytes() for p in d.iterdir()} == before


def test_generate_is_identical_under_different_hash_seeds_hx10(tmp_path):
    outs = set()
    for seed in ("0", "1", "2", "3", "7", "11"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1"}
        r = subprocess.run(
            [sys.executable, "-m", "lattice.spec", "forms", "generate", "--trace",
             str(FIX / "spec.trace.json"), "--spec", "tests.spec.minishop:SPEC"],
            capture_output=True, text=True, env=env, check=True,
        )  # fmt: skip
        outs.add(r.stdout)
    assert len(outs) == 1


def test_generate_writes_no_cache_file_hx16(tmp_path):
    d = _copy_fixture(tmp_path)
    before = sorted(p.name for p in d.rglob("*"))
    _cli("generate", "--trace", str(d / "spec.trace.json"), "--spec", "tests.spec.minishop:SPEC")
    assert sorted(p.name for p in d.rglob("*")) == before
    assert not list(FIX.rglob("*.cache")) and not list(FIX.rglob(".*"))


# ---- Tier 2: contradictions --------------------------------------------------------------------


def items(forms):
    return T2.contradictions(SPEC, forms)


def bnd(fid, cond, column="OrderItem.quantity", rule="R6"):
    return mk("bound", bound(cond, column=column), fid=fid, rule=rule)


def cmp_(op, v):
    return {"node": "Cmp", "op": op, "value": v}


def test_class_a_empty_intersections_and_near_misses():
    assert [i["class"] for i in items([bnd("F1", cmp_("ge", 5)), bnd("F2", cmp_("le", 3))])] == [
        "a"
    ]
    assert items([bnd("F1", cmp_("ge", 1)), bnd("F2", cmp_("le", 1))]) == []
    assert items([bnd("F1", cmp_("ge", 1)), bnd("F2", cmp_("le", 100))]) == []


def test_class_a_int_tightening_gt3_lt4_hx3():
    got = items([bnd("F1", cmp_("gt", 3)), bnd("F2", cmp_("lt", 4))])
    assert [i["class"] for i in got] == ["a"]
    assert (
        items(
            [bnd("F1", cmp_("gt", 3), "Product.price"), bnd("F2", cmp_("lt", 4), "Product.price")]
        )
        == []
    )


def test_class_a_sets_in_and_ne():
    inn = {"node": "In", "values": ["pending", "paid"]}
    other = {"node": "In", "values": ["shipped"]}
    assert len(items([bnd("F1", inn, "Order.status"), bnd("F2", other, "Order.status")])) == 1
    assert (
        items([bnd("F1", inn, "Order.status"), bnd("F2", cmp_("ne", "paid"), "Order.status")]) == []
    )
    assert len(items([bnd("F1", cmp_("eq", "paid"), "Order.status"),
                      bnd("F2", cmp_("ne", "paid"), "Order.status")])) == 1  # fmt: skip


def test_class_a_every_item_has_a_witness():
    it = items([bnd("F1", cmp_("ge", 5)), bnd("F2", cmp_("le", 3))])[0]
    assert it["witness"] == {"value": 5, "holds": "F1", "fails": "F2"}
    assert it["kind"] == "form_contradiction" and it["status"] == "determined"


def test_class_a_witness_self_check_suppresses_a_false_pair_hx12(monkeypatch):
    monkeypatch.setattr(T2, "empty_intersection", lambda a, b: True)
    assert items([bnd("F1", cmp_("ge", 1)), bnd("F2", cmp_("le", 100))]) == []


def _limit(fid, mx, where=None, rule="R10"):
    form = {
        "table": "OrderItem", "by": ["order_id"], "max": mx, "endpoint": "POST /orders/{}/items",
        "caller": "ann", "request": ADD, "refused": {"status": 409}, "accepted": {"status": 201},
        "given": {"order_items": [{"id": 1, "order_id": 10, "product_id": 50, "quantity": 1,
                                    "price": 0.0}]},
    }  # fmt: skip
    if where:
        form["where"] = where
    return mk("row_limit", form, fid=fid, rule=rule)


def test_class_b_same_count_different_max_and_equal_limits():
    got = items([_limit("F1", 50), _limit("F2", 60)])
    assert [i["class"] for i in got if i["class"] == "b"] == ["b"]
    assert [i for i in items([_limit("F1", 5), _limit("F2", 5)]) if i["class"] == "b"] == []


def test_class_b_different_where_is_a_different_count_hx4():
    got = items([_limit("F1", 5, {"product_id": 50}), _limit("F2", 7, {"product_id": 51})])
    assert [i for i in got if i["class"] == "b"] == []
    got = items([_limit("F1", 5, {"product_id": 50}), _limit("F2", 7, {"product_id": 50})])
    assert [i["class"] for i in got if i["class"] == "b"] == ["b"]


def _own(fid, scope="owner", other=404, rule="R9"):
    return mk(
        "ownership",
        {
            "table": "Order", "scope": scope, "endpoints": ["GET /orders/{}"], "owner": "ann",
            "other": "ben", "row": 1, "refused_other": {"status": other},
            "refused_anon": {"status": 401},
        },
        fid=fid, rule=rule,
    )  # fmt: skip


def test_class_c_scope_and_status_differences():
    assert [i["class"] for i in items([_own("F1"), _own("F2", "any")]) if i["class"] == "c"] == [
        "c"
    ]
    assert [
        i["class"] for i in items([_own("F1"), _own("F2", other=403)]) if i["class"] == "c"
    ] == ["c"]
    assert [i for i in items([_own("F1"), _own("F2")]) if i["class"] == "c"] == []


def _trf(fid, frm, to, acc=200, ref=409):
    return mk(
        "transition",
        {
            "machine": "Order.status", "trigger": "POST /orders/{}/cancel", "caller": "ann",
            "request": {"path": "/orders/10/cancel"}, "row": {"id": 10, "user_id": 1, "total": 0.0},
            "from": frm, "to": to, "accepted": {"status": acc}, "refused": {"status": ref},
        },
        fid=fid, rule="R1",
    )  # fmt: skip


def test_class_d_overlapping_from_with_different_outcomes_and_disjoint_from():
    a = _trf("F1", ["pending"], "cancelled")
    assert [
        i["class"] for i in items([a, _trf("F2", ["pending"], "paid")]) if i["class"] == "d"
    ] == ["d"]
    assert [
        i["class"]
        for i in items([a, _trf("F2", ["pending"], "cancelled", acc=202)])
        if i["class"] == "d"
    ] == ["d"]
    assert [
        i for i in items([a, _trf("F2", ["paid"], "shipped", ref=409)]) if i["class"] == "d"
    ] != []
    both_refuse = items(
        [_trf("F1", ["pending"], "cancelled"), _trf("F2", ["pending"], "cancelled")]
    )
    assert [i for i in both_refuse if i["class"] == "d"] == []


def test_class_d_prime_a_transition_edge_the_machine_lacks():
    bad = _trf("F1", ["shipped"], "pending")
    assert [i["class"] for i in items([bad])] == ["d'"]


def test_class_e_form_against_a_hand_written_step():
    hand = Scenario(
        "hand adds zero", given=copy.deepcopy(GIVEN),
        steps=(Step("POST", "/orders/10/items", user="ann", json={"product_id": 50, "quantity": 0},
                    expect_status=201),),
    )  # fmt: skip
    spec = Spec(
        profile=SPEC.profile, programs=dict(SPEC.programs), machines=SPEC.machines,
        scenarios=(hand,), users=dict(SPEC.users),
    )  # fmt: skip
    # the form's given is the same as the hand-written scenario's (canonically equal)
    f = mk("bound", with_endpoint(ge(1)))
    got = T2.contradictions(spec, [f])
    assert [(i["class"], i["forms"]) for i in got] == [("e", ["F9", "scenario:hand adds zero"])]
    assert T2.contradictions(SPEC, [f]) == []


def test_two_dev_forms_that_disagree_through_their_generated_steps_class_e():
    a = mk("bound", with_endpoint(ge(1)), fid="F1")
    b = mk("bound", with_endpoint(cmp_("le", 0)), fid="F2")  # at_bound 0 vs a's beyond 0
    got = [i for i in items([a, b]) if i["class"] == "e"]
    assert got and got[0]["forms"] == ["F1", "F2"]


def test_the_dev_forms_have_no_contradiction(fset):
    assert T2.contradictions(SPEC, fset.generating()) == []


def test_tier2_additive_branch_extends_determined_only_when_given():
    from lattice.spec import impact_tier2

    kw = dict(
        base=SPEC, cand=SPEC, base_trace=None, cand_trace=None, diff=None, targets={}, passed=None,
        provenance=None, flows=[], flow_note=None, base_lines=None, cand_lines=None,
    )  # fmt: skip
    a = impact_tier2.compute(**kw)
    b = impact_tier2.compute(**kw, forms_items=None)
    assert a == b and a["determined"] == []
    it = items([bnd("F1", cmp_("ge", 5)), bnd("F2", cmp_("le", 3))])
    c = impact_tier2.compute(**kw, forms_items=lambda: it)
    assert c["determined"] == it


# ---- the implementation mutants' kill tests ------------------------------------------------------


def test_hx1_a_refused_step_expects_the_database_unchanged(gen):
    assert _by_name(gen, "form:F4:beyond").steps[0].expect_db is not None
    assert _by_name(gen, "form:F6:other_post_orders_cancel").steps[0].expect_status == 404


def test_hx2_ge_is_generated_at_the_bound_not_past_it(gen):
    assert _by_name(gen, "form:F3:at_bound").steps[0].json["quantity"] == 1
    assert _by_name(gen, "form:F3:beyond").steps[0].json["quantity"] == 0
    assert F.bound_values(cmp_("ge", 1), F.Scalar.INT)[0] == 1
    assert [i.expr for i in gen.invariants if i.name == "form:F3:invariant"] == [
        "OrderItem.quantity >= 1"
    ]


def test_hx5_the_ownership_generator_has_the_anon_case_and_catches_the_auth_hole(apps, gen):
    assert any(s.name.startswith("form:F6:anon_") for s in gen.scenarios)
    rep = _run(F.effective_spec(SPEC, gen), apps["sec_auth"])
    assert any("form:F6:anon_" in str(f.rule) for f in rep.findings)


def test_hx6_the_transition_generator_refuses_every_other_state(gen):
    refused = {s.name for s in gen.scenarios if s.name.startswith("form:F1:refused_")}
    assert refused == {f"form:F1:refused_{s}" for s in ("paid", "shipped", "cancelled")}
    machine = SPEC.machines[0]
    assert set(machine.states()) == {"pending", "paid", "shipped", "cancelled"}


def test_hx11_a_now_or_sum_node_is_refused():
    assert "F017" in codes([mk("bound", bound(ge(1), now=True))])
    assert "F017" in codes([mk("bound", bound(ge(1), sum=["a"]))])
    assert codes([mk("bound", bound({"node": "sum", "value": 1}))]) == ["F017"]


def test_hx13_the_below_case_has_one_row_fewer_than_the_limit(gen):
    assert len(_by_name(gen, "form:F7:below").given["OrderItem"]) == 4


def test_hx14_the_count_invariant_is_at_most_not_below(gen):
    inv = next(i for i in gen.invariants if i.name == "form:F7:invariant")
    assert "<= 5" in inv.expr and "< 5" not in inv.expr.replace("<= 5", "")


def test_hx15_a_one_of_value_outside_the_column_is_refused():
    cond = {"node": "In", "values": ["pending", "teapot"]}
    assert codes([mk("bound", bound(cond, column="Order.status"))]) == ["F005"]


def test_machine_import_is_used():
    assert Machine
