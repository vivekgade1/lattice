"""Tier 4 of the change-impact report (lattice/spec/impact_tier4.py; Spike E, docs/results/
2026-10-17-implement-verify-prereg.md): the pure logic on synthetic reports and runs. The loop end
to end is tests/studio/test_implement.py. No app, no git, no model here."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from lattice.spec import impact_report as ir
from lattice.spec import impact_tier4 as t4
from lattice.spec.impact_tier4 import Refusal
from lattice.spec.implementation import route_key
from tests.spec.test_impact_report import STATUS_EDIT, _gin_inventory, _side

HASH = {"requirements": "r1", "spec": "s1", "trace": "t1"}
HASH2 = {"requirements": "r2", "spec": "s2", "trace": "t2"}


def report(**kw: Any) -> dict[str, Any]:
    r: dict[str, Any] = {
        "schema": ir.SCHEMA,
        "input": {
            "kind": "candidate",
            "change": None,
            "base": HASH,
            "candidate": HASH2,
            "inventory": None,
            "notes": [],
        },  # fmt: skip
        "diff": {
            "rules": {
                "added": [],
                "changed": [{"old": "R1", "new": "R1", "old_text": "a", "new_text": "b"}],
                "removed": [],
                "unchanged": 0,
            },
            "targets": {},
        },  # fmt: skip
        "tiers": {
            "1": "not_run:no_app",
            "2": "run",
            "3": "not_run:not_requested",
            "4": "not_run:spike_e",
        },
        "tier1": {"failing": [], "resolved": [], "flows": [], "counts": {}},
        "tier2": {"determined": [], "flows": [], "decidability": []},
        "tier3": {"locations": [], "flows": [], "unread": [], "counts": {}},
        "tier4": None,
        "summary": {"tiers": {}},
    }
    r.update(kw)
    return r


def finding(layer: str = "D", rule: str = "scenario: s", case: str = "s / step 2", **kw: Any):
    f = {"layer": layer, "verdict": "silent", "rule": rule, "endpoint": "POST /orders/1/cancel",
         "case": case, "code": None, "summary": f"[{layer} silent] {case}", "details": {},
         "request": None, "flow": None}  # fmt: skip
    f.update(kw)
    return f


def flow_finding(fid: str, step: int, status: int | None = 400, layer: str = "D") -> dict:
    det = {"actual": {"status": status}} if status is not None else {}
    return finding(
        layer, f"scenario: flow_{fid}", f"flow_{fid} / step {step}", flow=fid, details=det
    )


def flow(fid: str, *steps: tuple[str, str, str | None, int]) -> dict[str, Any]:
    return {
        "id": fid,
        "name": fid,
        "status": "verified",
        "steps": [
            {
                "i": i,
                "kind": "request",
                "method": m,
                "path": p,
                "caller": u,
                "json": None,
                "expect_status": st,
            }  # fmt: skip
            for i, (m, p, u, st) in enumerate(steps)
        ],
    }


CANCEL = ("POST", "/orders/1/cancel", "ann", 409)
PASSED = {"status": "passed", "rounds": 1, "agent": "stub", "model": None, "gates": []}


def cstep(
    scenario: str,
    k: int,
    status: int,
    path: str = "/orders/1/cancel",
    user: str | None = "ann",
    method: str = "POST",
) -> dict[str, Any]:
    return {"scenario": scenario, "k": k, "method": method, "path": path, "user": user,
            "expect_status": status}  # fmt: skip


def run(findings: list[dict], flows_run: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"findings": findings, "flows_run": list(flows_run)}


def compute(rep, before, after, flows=(), cand_steps=(), rebuild=PASSED, **kw):
    return t4.compute(rep, before, after, list(flows), rebuild, {"files": []},
                      cand_steps=list(cand_steps), route_key=route_key, **kw)  # fmt: skip


# ---- the report as an input (D2) ---------------------------------------------------------------


def check_ok(rep, **kw):
    args = {"base": HASH, "cand": HASH2, "inventory": None}
    args.update(kw)
    t4.check_report(rep, **args)


def test_a_good_report_is_accepted_and_the_eid_is_stable():
    check_ok(report())
    assert t4.eid_of(HASH, HASH2) == t4.eid_of(dict(HASH), dict(HASH2))
    assert t4.eid_of(HASH, HASH2) != t4.eid_of(HASH2, HASH)
    assert t4.eid_of(HASH, HASH2).startswith("i") and len(t4.eid_of(HASH, HASH2)) == 11


@pytest.mark.parametrize(
    "rep",
    [
        report(schema="lattice.impact/2"),
        report(tier4={"verdict": "pass"}),
        "not a dict",
    ],
)
def test_bad_report(rep):
    with pytest.raises(Refusal) as e:
        check_ok(rep)
    assert e.value.code == "bad_report"


def test_report_mismatch_on_base_candidate_and_inventory_bytes():
    with pytest.raises(Refusal) as e:
        check_ok(report(), base={**HASH, "spec": "other"})
    assert e.value.code == "report_mismatch"
    with pytest.raises(Refusal) as e:
        check_ok(report(), cand={**HASH2, "trace": "other"})
    assert e.value.code == "report_mismatch"
    inv = {"extract_sha256": "e", "workflows_sha256": "w", "stale": False}
    rep = report()
    rep["input"]["inventory"] = inv
    check_ok(rep, inventory={"extract_sha256": "e", "workflows_sha256": "w"})
    with pytest.raises(Refusal) as e:
        check_ok(rep, inventory={"extract_sha256": "e", "workflows_sha256": "different"})
    assert e.value.code == "report_mismatch"
    with pytest.raises(Refusal) as e:
        check_ok(rep, inventory=None)
    assert e.value.code == "report_mismatch"


def test_stale_inventory_and_a_candidate_that_contradicts_itself():
    rep = report()
    rep["input"]["inventory"] = {"extract_sha256": "e", "workflows_sha256": "w", "stale": False}
    with pytest.raises(Refusal) as e:
        check_ok(rep, inventory={"extract_sha256": "e", "workflows_sha256": "w"},
                 inventory_stale=True)  # fmt: skip
    assert e.value.code == "stale_inventory"
    rep = report()
    rep["tier2"]["determined"] = [{"kind": "spec_contradiction", "status": "determined"}]
    with pytest.raises(Refusal) as e:
        check_ok(rep)
    assert e.value.code == "spec_contradiction"


# ---- spec_backed (D6): 8 cases (PSB) -----------------------------------------------------------

FL = flow("wf", CANCEL)
FIND = flow_finding("wf", 1, 400)
GOOD = cstep("sc", 2, 400)


def backed(finding_=FIND, steps=(GOOD,), failing=frozenset()):
    return t4.spec_backed(FL, finding_, list(steps), set(failing), route_key)


def test_spec_backed_the_eight_cases():
    cases = [
        backed(),  # 1 all match -> True
        backed(steps=(cstep("sc", 2, 400, method="GET"),)),  # 2 method differs
        backed(steps=(cstep("sc", 2, 400, path="/orders/2/items"),)),  # 3 route differs
        backed(steps=(cstep("sc", 2, 400, user="ben"),)),  # 4 user differs
        backed(steps=(cstep("sc", 2, 409),)),  # 5 status differs
        backed(finding_=finding(**{"flow": "wf", "case": "flow_wf / step 1"})),  # 6 no details
        backed(failing={("D", "scenario: sc", "sc / step 2", "", "")}),  # 7 the step fails after
        backed(steps=(cstep("sc", 1, 200), cstep("other", 3, 400, path="/orders/7/cancel"), GOOD)),
    ]
    assert [c[0] for c in cases] == [True, False, False, False, False, None, False, True]
    assert cases[5][1]  # a missing observed status says why


def test_spec_backed_matches_a_route_template_and_a_missing_user():
    anon = flow("wf", ("GET", "/orders/5", None, 404))
    f = flow_finding("wf", 1, 401)
    steps = [cstep("sc", 1, 401, path="/orders/9", user=None, method="GET")]
    assert t4.spec_backed(anon, f, steps, set(), route_key)[0] is True


# ---- the classes and the verdict (D6) ----------------------------------------------------------


def test_resolved_still_failing_and_regression_are_classified_by_ident():
    a, b, c = (
        finding(case="a / step 1", rule="scenario: a"),
        finding(case="b / step 1", rule="scenario: b"),
        finding(case="c / step 1", rule="scenario: c"),
    )
    out = compute(report(), run([a, b]), run([b, c]))
    assert [i["case"] for i in out["resolved"]] == ["a / step 1"]
    assert [i["case"] for i in out["still_failing"]] == ["b / step 1"]
    assert [i["case"] for i in out["regressions"]] == ["c / step 1"]
    assert len(out["failing"]) == 2 and out["counts"]["unexpected_regressions"] == 1


def test_regression_fails():
    out = compute(report(), run([]), run([finding()]))
    assert out["verdict"] == "fail" and out["counts"]["regressions"] == 1


def test_still_failing_fails_and_a_clean_build_passes():
    assert compute(report(), run([finding()]), run([finding()]))["verdict"] == "fail"
    out = compute(report(), run([finding()]), run([]))
    assert out["verdict"] == "pass" and out["counts"]["resolved"] == 1
    assert all(i["id"].startswith("t4-") and i["tier"] == 4 for i in out["resolved"])


def test_an_unchanged_flow_and_one_that_failed_before_are_not_judged():
    flows = [flow("ok", CANCEL), flow("bad", CANCEL)]
    before = run([flow_finding("bad", 1)], ("ok", "bad"))
    after = run([flow_finding("bad", 1)], ("ok", "bad"))
    out = compute(report(), before, after, flows)
    kinds = {i["flow"]: i["kind"] for i in out["flows"]}
    assert kinds == {"ok": "flow_unchanged", "bad": "flow_before_failed"}
    assert out["verdict"] == "pass"


def test_a_changed_flow_nobody_reported_is_unexpected_and_fails():
    flows = [flow("wf", CANCEL)]
    out = compute(report(), run([], ("wf",)), run([flow_finding("wf", 1)], ("wf",)), flows,
                  cand_steps=[GOOD])  # fmt: skip
    (item,) = [i for i in out["flows"] if i["kind"] == "flow_changed"]
    assert item["reported_as"] == "unreported" and out["verdict"] == "fail"
    assert out["counts"]["unreported"] == 1 and out["counts"]["unexpected_regressions"] == 1


def t2_flow_claim(fid: str) -> dict[str, Any]:
    return {"kind": "flow_fails_after_change", "target": f"flow:{fid}", "status": "determined",
            "claim": f"flow:{fid} fails at step 0 after the change"}  # fmt: skip


def t3_flow(fid: str) -> dict[str, Any]:
    return {"kind": "may_fail", "target": f"flow:{fid}", "routes": ["POST /orders/{}/cancel"]}


def test_a_t2_determined_flow_change_is_confirmed():
    rep = report(tier2={"determined": [], "flows": [t2_flow_claim("wf")], "decidability": []})
    after = run([flow_finding("wf", 1)], ("wf",))
    out = compute(rep, run([], ("wf",)), after, [flow("wf", CANCEL)], cand_steps=[GOOD])
    (item,) = [i for i in out["flows"] if i["kind"] == "flow_changed"]
    assert item["reported_as"] == "t2_determined" and item["spec_backed"] is True
    assert out["t2_confirmed"] is True and out["verdict"] == "pass"


def test_a_planted_t2_flow_claim_on_an_unchanged_flow_is_contradicted():
    rep = report(tier2={"determined": [], "flows": [t2_flow_claim("wf-add")], "decidability": []})
    flows = [flow("wf-add", ("POST", "/orders/3/items", "ben", 201))]
    out = compute(rep, run([], ("wf-add",)), run([], ("wf-add",)), flows)
    (chk,) = out["t2_check"]
    assert chk["kind"] == "t2_flow_claim" and chk["confirmed"] is False
    assert out["t2_confirmed"] is False and out["counts"]["t2_contradicted"] == 1
    assert out["verdict"] == "fail"


def test_a_t2_scenario_claim_needs_a_before_failure():
    claim = {"kind": "scenario_fails", "target": "scenario:s", "status": "determined",
             "claim": "scenario:s fails at step <= 2", "rules": ["R1"]}  # fmt: skip
    rep = report(tier2={"determined": [claim], "flows": [], "decidability": []})
    ok = compute(rep, run([finding()]), run([]))
    assert ok["t2_check"][0]["confirmed"] is True and ok["verdict"] == "pass"
    bad = compute(rep, run([]), run([]))
    assert bad["t2_check"][0]["confirmed"] is False and bad["verdict"] == "fail"
    conditional = {**claim, "status": "conditional"}
    skipped = compute(report(tier2={"determined": [conditional], "flows": [], "decidability": []}),
                      run([]), run([]))  # fmt: skip
    assert skipped["t2_check"] == []


def test_a_t3_may_fail_change_without_a_backing_step_needs_the_owner_and_with_one_passes():
    rep = report(tier3={"locations": [], "flows": [t3_flow("wf")], "unread": [], "counts": {}})
    fl = [flow("wf", CANCEL)]
    after = run([flow_finding("wf", 1, 400)], ("wf",))
    unbacked = compute(rep, run([], ("wf",)), after, fl, cand_steps=[])
    (item,) = [i for i in unbacked["flows"] if i["kind"] == "flow_changed"]
    assert item["reported_as"] == "t3_may_fail" and item["spec_backed"] is False
    assert (
        unbacked["verdict"] == "needs_owner" and unbacked["counts"]["unexpected_regressions"] == 0
    )
    backed_ = compute(rep, run([], ("wf",)), after, fl, cand_steps=[GOOD])
    assert backed_["verdict"] == "pass"
    unknown = compute(rep, run([], ("wf",)), run([flow_finding("wf", 1, None)], ("wf",)), fl,
                      cand_steps=[GOOD])  # fmt: skip
    (it,) = [i for i in unknown["flows"] if i["kind"] == "flow_changed"]
    assert it["spec_backed"] is None and it["why"] and unknown["verdict"] == "needs_owner"


def test_a_candidate_invariant_broken_on_a_flow_is_a_workflow_fail():
    f = flow_finding("wf", 1, layer="C")
    f["rule"] = "invariant"
    out = compute(report(), run([], ("wf",)), run([f], ("wf",)), [flow("wf", CANCEL)])
    assert out["counts"]["workflow_fails"] == 1 and out["verdict"] == "fail"
    already = compute(report(), run([f], ("wf",)), run([f], ("wf",)), [flow("wf", CANCEL)])
    assert already["counts"]["workflow_fails"] == 0


def test_the_verdict_table():
    base = {"regressions": 0, "still_failing": 0, "workflow_fails": 0, "unreported": 0,
            "t2_contradicted": 0, "may_fail_unbacked": 0}  # fmt: skip
    v = lambda counts=None, status="passed", own=True, mr=True: t4.verdict_of(  # noqa: E731
        {"status": status}, {**base, **(counts or {})}, owner_untouched=own, matches=mr
    )
    assert v() == "pass" and v(mr=None) == "pass"
    assert v({"may_fail_unbacked": 2}) == "needs_owner"
    assert v({"may_fail_unbacked": 2, "regressions": 1}) == "fail"
    for key in ("regressions", "still_failing", "workflow_fails", "unreported", "t2_contradicted"):
        assert v({key: 1}) == "fail", key
    assert v(own=False) == "fail" and v(mr=False) == "fail"
    for s in ("gave_up", "stopped", "interrupted"):
        assert v(status=s) == "not_built"


def test_before_must_match_the_reports_tier1():
    rep = report(
        tiers={"1": "run", "2": "run", "3": "x", "4": "x"},
        tier1={"failing": [{"layer": "D", "rule": "scenario: s", "case": "s / step 2",
                            "endpoint": "POST /orders/1/cancel", "code": None}],
               "resolved": [], "flows": [], "counts": {}},
    )  # fmt: skip
    assert t4.matches_report(rep, run([finding()])) is True
    assert t4.matches_report(rep, run([])) is False
    assert (
        t4.matches_report(rep, run([finding(), finding(case="x / step 1", rule="scenario: x")]))
        is False
    )
    off = report(tiers={"1": "not_run:no_app", "2": "run", "3": "x", "4": "x"})
    assert t4.matches_report(off, run([])) is None
    out = compute(rep, run([]), run([]))
    assert out["before"]["matches_report"] is False and out["verdict"] == "fail"


def test_matches_report_compares_the_flows_when_the_report_ran_them():
    rep = report(tiers={"1": "run", "2": "run", "3": "x", "4": "x"})
    rep["input"]["inventory"] = {"extract_sha256": None, "workflows_sha256": "w", "stale": False}
    rep["tier1"]["flows"] = [{"kind": "baseline_drift", "flow": "wf"}]
    assert t4.matches_report(rep, run([flow_finding("wf", 1)], ("wf",))) is True
    assert t4.matches_report(rep, run([], ("wf",))) is False


# ---- the section is plain, canonical data -----------------------------------------------------


def test_the_tier4_section_is_json_and_deterministic_with_no_absolute_path():
    rep = report(tier2={"determined": [], "flows": [t2_flow_claim("wf")], "decidability": []})
    args = (
        rep,
        run([finding()], ("wf",)),
        run([flow_finding("wf", 1)], ("wf",)),
        [flow("wf", CANCEL)],
    )
    a = compute(*args, cand_steps=[GOOD])
    b = compute(*copy.deepcopy(args), cand_steps=[GOOD])
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    ids = [i["id"] for k in ("resolved", "flows", "t2_check") for i in a[k]]
    assert len(ids) == len(set(ids)) and all(i.startswith("t4-") for i in ids)


def test_merge_changes_exactly_tiers4_and_tier4():
    rep = report()
    out = t4.merge(rep, "run", {"verdict": "pass"})
    assert out["tiers"]["4"] == "run" and out["tier4"] == {"verdict": "pass"}
    assert {k: v for k, v in out.items() if k not in ("tiers", "tier4")} == {
        k: v for k, v in rep.items() if k not in ("tiers", "tier4")
    }
    assert {k: v for k, v in out["tiers"].items() if k != "4"} == {
        k: v for k, v in rep["tiers"].items() if k != "4"
    }
    assert rep["tier4"] is None and rep["tiers"]["4"] == "not_run:spike_e"  # the input is untouched


def test_with_owner_marks_a_changed_checkout_a_fail():
    t = {"verdict": "pass", "owner": {"untouched": True}}
    assert t4.with_owner(t, {"untouched": True})["verdict"] == "pass"
    assert t4.with_owner(t, {"untouched": False})["verdict"] == "fail"
    nb = {"verdict": "not_built"}
    assert t4.with_owner(nb, {"untouched": False})["verdict"] == "not_built"


def test_the_change_dict_carries_the_rules_the_diff_and_the_before_targets():
    before = run([finding(), finding("C", "inv", "(seed)", details={"invariant": "stock"}),
                  finding("A", "x", "", code="PY1")])  # fmt: skip
    ch = t4.change_dict("iabc", report(), request=None, markdown_diff="-a\n+b\n", before=before)
    assert ch["id"] == "iabc" and ch["status"] == "accepted"
    assert (
        ch["request"]
        == "Implement the candidate spec change: 0 rule(s) added, 1 changed, 0 removed."
    )
    assert ch["rules"] == {"added": [], "changed": [{"old": "R1", "new": "R1"}], "removed": []}
    assert [t["target"] for t in ch["checks"]["impact"]["failing"]] == [
        "scenario:s", "invariant:stock"]  # fmt: skip
    assert all(t["label"] == "not_built_yet" for t in ch["checks"]["impact"]["failing"])
    assert (
        t4.change_dict("i", report(), request="Do it", markdown_diff="", before=before)["request"]
        == "Do it"
    )


# ---- t3_vs_edit on the Go fixture (PT3E) -------------------------------------------------------


@pytest.fixture(scope="module")
def go_report(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("go")
    inv = _gin_inventory(tmp)
    inp = ir.build_inputs(project=None, base_side=_side(), cand_side=_side(STATUS_EDIT))
    inp.inventory = inv
    return ir.build_report(inp, tiers="3"), inv


def test_t3_vs_edit_counts_on_four_synthetic_diffs(go_report):
    rep, _inv = go_report
    listed = sorted({i["file"] for i in rep["tier3"]["locations"]})
    assert "api/api.go" in listed and "models/models.go" not in listed
    names = ["api/api.go", "models/models.go", "api/api_test.go", "api/new.go", "REQUIREMENTS.md"]
    kept = t4.edit_files(names, test_only=["stub-rebuild.json"])
    assert kept == [
        "api/api.go",
        "api/new.go",
        "models/models.go",
    ]  # tests and specs are not counted
    one_in = t4.t3_vs_edit(rep, {"files": ["api/api.go"]})["files"]
    assert (one_in["recall"], one_in["precision"], one_in["missed"]) == (
        1.0,
        round(1 / len(listed), 3),
        [],
    )
    outside = t4.t3_vs_edit(rep, {"files": ["models/models.go"]})["files"]
    assert outside["recall"] == 0.0 and [m["file"] for m in outside["missed"]] == [
        "models/models.go"
    ]
    test_only = t4.t3_vs_edit(
        rep,
        {
            "files": t4.edit_files(
                ["api/api_test.go", "stub-rebuild.json", "spec.py"], test_only=["stub-rebuild.json"]
            )
        },
    )["files"]
    assert test_only["touched"] == [] and test_only["recall"] is None and test_only["missed"] == []
    new_file = t4.t3_vs_edit(rep, {"files": ["api/new.go"]})["files"]
    assert new_file["recall"] == 0.0 and [m["file"] for m in new_file["missed"]] == ["api/new.go"]
    assert all(m["id"].startswith("t4-") and m["kind"] == "t3_missed" for m in new_file["missed"])


def test_t3_vs_edit_declarations_use_the_go_tree_and_python_has_none(go_report):
    from lattice.spec.impact_tier3 import GoTree

    rep, inv = go_report
    tree = GoTree(inv)
    loc = rep["tier3"]["locations"][0]
    decl = tree.enclosing(loc["file"], loc["line"])
    assert decl is not None
    hunks = {loc["file"]: [[decl.start, 1]]}
    out = t4.t3_vs_edit(rep, {"files": [loc["file"]], "hunks": hunks}, tree=tree)
    assert out["declarations"]["recall"] == 1.0 and out["declarations"]["touched"]
    assert t4.t3_vs_edit(rep, {"files": [loc["file"]]}, tree=None)["declarations"] == (
        "not_applicable: no extract"
    )
    off = report()
    assert t4.t3_vs_edit(off, {"files": ["a.go"]})["status"] == "not_applicable: tier3 not run"
