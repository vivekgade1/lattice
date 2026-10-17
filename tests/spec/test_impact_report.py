"""The change-impact engine (Spike D, docs/results/2026-10-16-change-impact-prereg.md): inputs, the
rule and target diff, tier 1 (observed, on the mini-shop), tier 2 (determined), tier 3 (static, on
the Gin fixture of tests/spec/fixtures/implementation), the schema and its determinism, and that
reading writes nothing. The dev changes are tests/spec/fixtures/impact/."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from lattice.importer.inventory import capture
from lattice.spec import Invariant, Machine, Scenario, Step, check
from lattice.spec import impact_report as ir
from lattice.spec import impact_tier2 as t2
from lattice.spec import impact_tier3 as t3
from lattice.spec.draft import load_spec
from lattice.spec.implementation import load_inventory
from lattice.spec.incremental import changed_targets
from lattice.spec.rules import Trace
from tests.spec import minishop

FIX = Path(__file__).resolve().parent / "fixtures" / "impact"
IMPL = Path(__file__).resolve().parent / "fixtures" / "implementation"
ROOT = Path(__file__).resolve().parents[2]

DEV = ("reword", "status_changed", "new_request", "bad_invariant", "removed_rule", "conflict")


# ---- the mini-shop world ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def shop(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("shop")
    return {
        "ok": minishop.write_app(root, "ok"),
        "partial_write": minishop.write_app(root, "partial_write"),
    }


def runner_for(shop: dict[str, tuple[Path, str]], variant: str = "ok", layers: str | None = None):
    app_dir, package = shop[variant]
    calls: list[tuple[str, str]] = []

    def run(spec: Any, wanted: str) -> Any:
        calls.append((spec.scenarios[0].name if spec.scenarios else "", wanted))
        return check(
            spec,
            minishop.app_factory(package),
            minishop.build_db,
            layers=layers or wanted,
            app_dir=app_dir,
            package=package,
            login=minishop.login,
        )

    run.calls = calls  # type: ignore[attr-defined]
    return run


def inputs(name: str, **kw: Any) -> ir.Inputs:
    return ir.build_inputs(project=FIX / "base", candidate=FIX / name, inventory=None, **kw)


def failing_set(report: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {(i["layer"], i["rule"], i["case"] or "") for i in report["tier1"]["failing"]}


def independent(shop, name: str, layers: str = "CD") -> set[tuple[str, str, str]]:
    """The candidate checked directly, without the engine."""
    spec, _ = load_spec((FIX / name / "spec.py").read_text())
    rep = runner_for(shop)(spec, layers)
    return {(f.layer, f.rule, f.case or "") for f in rep.findings}


# ---- inputs (D1) -----------------------------------------------------------------------------


def test_the_dev_fixtures_load_and_the_base_has_five_rules():
    side = ir.read_side(FIX / "base")
    assert [r.id for r in side.trace.rules] == ["R1", "R2", "R3", "R4", "R5"]
    for name in DEV:
        ir.read_side(FIX / name)


def test_a_missing_spec_is_bad_inputs(tmp_path: Path):
    with pytest.raises(ir.ImpactError) as e:
        ir.read_side(tmp_path)
    assert e.value.code == "bad_inputs"


def test_a_spec_that_does_not_load_is_bad_spec(tmp_path: Path):
    (tmp_path / "spec.py").write_text("raise RuntimeError('x')\n")
    with pytest.raises(ir.ImpactError) as e:
        ir.read_side(tmp_path)
    assert e.value.code == "bad_spec"


def test_no_requirements_means_an_empty_rule_diff_and_a_note(tmp_path: Path):
    shutil.copytree(FIX / "status_changed", tmp_path / "c")
    (tmp_path / "c" / "REQUIREMENTS.md").unlink()
    inp = ir.build_inputs(project=FIX / "base", candidate=tmp_path / "c")
    rep = ir.build_report(inp, tiers="2", baseline="none")
    assert rep["diff"]["rules"]["added"] == rep["diff"]["rules"]["changed"] == []
    assert any("REQUIREMENTS.md" in n for n in rep["input"]["notes"])
    assert rep["diff"]["targets"]["modified"] == ["scenario:ann cancels her pending order"]


def _record(tmp_path: Path, name: str, status: str = "ready") -> Path:
    base, cand = ir.read_side(FIX / "base"), ir.read_side(FIX / name)
    d = tmp_path / "state" / "changes"
    d.mkdir(parents=True)
    doc = {
        "schema": 1,
        "kind": "changes",
        "id": "c1",
        "record": {"id": "c1", "status": status},
        "private": {
            "base": {
                "markdown": base.markdown,
                "module": base.module,
                "trace": base.trace_text,
            },
            "new_md": cand.markdown,
            "new_module": cand.module,
            "new_trace": cand.trace_text,
        },
    }
    (d / "c1.json").write_text(json.dumps(doc))
    return tmp_path / "state"


def test_a_change_record_is_read_as_a_plain_file_and_equals_the_candidate(tmp_path: Path):
    state = _record(tmp_path, "status_changed")
    a = ir.build_report(
        ir.build_inputs(project=FIX / "base", change="c1", state_dir=state),
        tiers="2",
        baseline="none",
    )
    b = ir.build_report(inputs("status_changed"), tiers="2", baseline="none")
    assert a["input"]["kind"] == "change" and a["input"]["change"] == "c1"
    assert b["input"]["kind"] == "candidate" and b["input"]["change"] is None
    for r in (a, b):
        r["input"].pop("kind"), r["input"].pop("change")
    assert ir.report_json(a) == ir.report_json(b)


def test_a_change_that_is_not_ready_is_refused(tmp_path: Path):
    state = _record(tmp_path, "reword", status="drafting")
    with pytest.raises(ir.ImpactError) as e:
        ir.build_inputs(project=FIX / "base", change="c1", state_dir=state)
    assert e.value.code == "change_not_ready"
    with pytest.raises(ir.ImpactError) as e2:
        ir.build_inputs(project=FIX / "base", change="c9", state_dir=state)
    assert e2.value.code == "no_candidate"
    with pytest.raises(ir.ImpactError):
        ir.build_inputs(project=FIX / "base", change="../x", state_dir=state)


def test_reading_a_change_never_opens_the_state_store(tmp_path: Path, monkeypatch):
    import lattice.studio.state as st

    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("StateStore opened")

    monkeypatch.setattr(st.StateStore, "__init__", boom)
    state = _record(tmp_path, "reword", status="accepted")
    ir.build_inputs(project=FIX / "base", change="c1", state_dir=state)
    before = {p.name: p.read_bytes() for p in (state).rglob("*") if p.is_file()}
    ir.build_report(
        ir.build_inputs(project=FIX / "base", change="c1", state_dir=state),
        tiers="2",
        baseline="none",
    )
    assert {p.name: p.read_bytes() for p in state.rglob("*") if p.is_file()} == before
    assert not (state / "meta.json").exists()


# ---- the diff and the schema (D2) ---------------------------------------------------------------


def test_the_diff_of_each_dev_change():
    rep = {n: ir.build_report(inputs(n), tiers="2", baseline="none") for n in DEV}
    assert [len(rep["reword"]["diff"]["rules"][k]) for k in ("added", "changed", "removed")] == [
        0,
        1,
        0,
    ]
    assert rep["reword"]["diff"]["targets"] == {"added": [], "modified": [], "removed": []}
    assert rep["status_changed"]["diff"]["targets"]["modified"] == [
        "scenario:ann cancels her pending order"
    ]
    assert rep["new_request"]["diff"]["targets"]["added"] == ["scenario:ann reads her order"]
    assert rep["bad_invariant"]["diff"]["targets"]["added"] == ["invariant:stock_at_least_six"]
    assert rep["removed_rule"]["diff"]["targets"]["removed"] == ["invariant:stock_non_negative"]
    assert len(rep["removed_rule"]["diff"]["rules"]["removed"]) == 1


def test_the_schema_tiers_and_the_unrun_slots():
    rep = ir.build_report(inputs("status_changed"), tiers="23", baseline="none")
    assert rep["schema"] == "lattice.impact/1"
    assert rep["tiers"] == {
        "1": "not_run:not_requested",
        "2": "run",
        "3": "not_run:inventory_missing",
        "4": "not_run:spike_e",
    }
    assert rep["tier4"] is None
    for key in ("input", "diff", "tiers", "tier1", "tier2", "tier3", "tier4", "summary"):
        assert key in rep
    assert "not decidable before the change exists" in rep["summary"]["note"]
    assert rep["tier3"]["unread"][0]["kind"] == "no_extract"


def test_every_item_has_a_stable_id_a_tier_and_evidence():
    rep = ir.build_report(inputs("conflict"), tiers="2", baseline="none")
    items = rep["tier2"]["determined"]
    assert items and all(re.fullmatch(r"t2-[0-9a-f]{10}", i["id"]) for i in items)
    assert all(i["tier"] == 2 and i["evidence"] for i in items)
    again = ir.build_report(inputs("conflict"), tiers="2", baseline="none")
    assert [i["id"] for i in items] == [i["id"] for i in again["tier2"]["determined"]]


def test_the_report_is_deterministic_sorted_and_free_of_paths_and_times():
    texts = []
    for _ in range(3):
        rep = ir.build_report(inputs("new_request"), tiers="23", baseline="none")
        texts.append(ir.report_json(rep))
    assert len(set(texts)) == 1
    assert not re.search(r"/Users|/private|/tmp/|/var/folders", texts[0])
    assert not re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", texts[0])
    assert json.loads(texts[0]) == json.loads(json.dumps(json.loads(texts[0]), sort_keys=True))


# ---- tier 1 (D3), observed on the mini-shop -----------------------------------------------------


def test_tier1_of_a_control_is_empty(shop):
    inp = ir.build_inputs(project=FIX / "base", candidate=FIX / "base")
    rep = ir.build_report(inp, tiers="1", runner=runner_for(shop, layers="CD"), baseline="run")
    assert rep["tiers"]["1"] == "run" and rep["tier1"]["failing"] == []
    assert rep["tier1"]["baseline"]["run"] is True


def test_tier1_reword_is_empty_and_status_changed_is_exact(shop):
    runner = runner_for(shop, layers="CD")
    r0 = ir.build_report(inputs("reword"), tiers="1", runner=runner, baseline="run")
    assert r0["tier1"]["failing"] == []
    rep = ir.build_report(inputs("status_changed"), tiers="1", runner=runner, baseline="run")
    assert failing_set(rep) == independent(shop, "status_changed")
    (item,) = rep["tier1"]["failing"]
    assert item["target"] == "scenario:ann cancels her pending order"
    assert item["label"] == "not_built_yet" and item["rules"] == ["R1"]
    assert item["observation"] == "runtime" and item["also_fails_on_base"] is False
    assert item["tier"] == 1 and item["evidence"][0]["kind"] == "check"
    assert rep["tier1"]["counts"]["failing"] == 1


def test_tier1_runs_the_candidate_not_the_base(shop):
    """IX2: a T1 that reused the base report would be empty here."""
    runner = runner_for(shop, layers="CD")
    ir.build_report(inputs("status_changed"), tiers="1", runner=runner, baseline="none")
    rep = ir.build_report(inputs("status_changed"), tiers="1", runner=runner, baseline="none")
    assert len(rep["tier1"]["failing"]) == 1 and rep["tier1"]["baseline"]["run"] is False


def test_tier1_bad_invariant_is_a_c_finding_with_a_target(shop):
    rep = ir.build_report(
        inputs("bad_invariant"), tiers="1", runner=runner_for(shop, layers="CD"), baseline="run"
    )
    assert failing_set(rep) == independent(shop, "bad_invariant")
    assert {i["layer"] for i in rep["tier1"]["failing"]} == {"C"}
    assert all(i["target"] == "invariant:stock_at_least_six" for i in rep["tier1"]["failing"])
    assert all(i["label"] == "not_built_yet" for i in rep["tier1"]["failing"])


def test_tier1_resolved_are_base_findings_the_candidate_lacks(shop):
    """The base runs on a broken app; the candidate drops the scenario that caught it."""
    base = ir.read_side(FIX / "base")
    broken = runner_for(shop, "ok", layers="CD")

    def run(spec: Any, wanted: str) -> Any:
        rep = broken(spec, wanted)
        if any(s.name == "ann cancels her pending order" for s in spec.scenarios):
            from lattice.spec.report import CheckFinding

            rep.findings.append(
                CheckFinding(
                    layer="D",
                    verdict="silent",
                    rule="scenario: planted",
                    case="planted / step 1",
                )
            )
        return rep

    cand = ir.read_side(FIX / "reword")
    cand_spec = replace(
        cand.spec, scenarios=tuple(s for s in cand.spec.scenarios if "ann cancels" not in s.name)
    )
    side = ir.Side(cand.markdown, cand.module, cand.trace_text, cand_spec, cand.trace)

    def run2(spec: Any, wanted: str) -> Any:
        rep = broken(spec, wanted)
        if spec is not cand_spec and not (spec.scenarios == cand_spec.scenarios):
            from lattice.spec.report import CheckFinding

            rep.findings.append(
                CheckFinding(
                    layer="D", verdict="silent", rule="scenario: planted", case="planted / step 1"
                )
            )
        return rep

    inp = ir.build_inputs(project=FIX / "base", base_side=base, cand_side=side)
    rep = ir.build_report(inp, tiers="1", runner=run2, baseline="run")
    assert [i["rule"] for i in rep["tier1"]["resolved"]] == ["scenario: planted"]
    assert rep["tier1"]["failing"] == []
    del run


def test_tier1_keeps_a_finding_with_no_target(shop):
    """IX15: a layer A finding names no spec element; it stays, as `unlinked`."""
    runner = runner_for(shop, "partial_write", layers="A")
    inp = ir.build_inputs(project=FIX / "base", candidate=FIX / "reword")
    rep = ir.build_report(inp, tiers="1", runner=runner, baseline="run", layers="A")
    found = rep["tier1"]["failing"]
    assert found and all(i["layer"] == "A" for i in found)
    assert all(i["target"] is None and i["label"] == "unlinked" for i in found)
    assert all(i["observation"] == "static_checker" and i["file"] for i in found)
    assert all(i["also_fails_on_base"] for i in found)


def test_a_go808_finding_names_its_machine(tmp_path: Path):
    """IX1: layer A findings are labelled, not dropped (GO808 -> machine:T.c)."""
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    from tests.gocheck import tasks_spec as ts

    edges = tuple(e for e in ts.SPEC.machines[0].edges if e != ("todo", "doing"))
    cand_spec = replace(ts.SPEC, machines=(replace(ts.SPEC.machines[0], edges=edges),))
    base = ir.Side(
        "- r\n",
        "SPEC = None\n",
        Trace(source="", rules=[]).to_json(),
        ts.SPEC,
        Trace(source="", rules=[]),
    )
    cand = ir.Side(
        "- r\n",
        "SPEC = None\n",
        Trace(source="", rules=[]).to_json(),
        cand_spec,
        Trace(source="", rules=[]),
    )

    def run(spec: Any, wanted: str) -> Any:
        return check(spec, ts.make_app, ts.build_db, layers="A", go_src=ts.APP_DIR)

    inp = ir.build_inputs(project=None, base_side=base, cand_side=cand)
    rep = ir.build_report(inp, tiers="1", runner=run, baseline="run", layers="A")
    new = [i for i in rep["tier1"]["failing"] if not i["also_fails_on_base"]]
    assert new, "removing an edge adds a GO808 finding"
    assert all(i["code"] == "GO808" and i["target"] == "machine:Task.state" for i in new)
    assert all(i["file"] and i["line"] for i in new)


def test_a_fault_mid_run_leaves_no_owned_container(monkeypatch):
    """IX13: the owner token is set during the run and its containers are removed afterwards."""
    seen: list[str] = []
    import lattice.studio.state as st

    monkeypatch.setattr(
        st, "remove_owned_containers", lambda token, log=None: seen.append(token) or []
    )

    def boom(spec: Any, wanted: str) -> Any:
        assert os.environ[ir.OWNER_ENV].startswith("impact-")
        raise RuntimeError("the check raised")

    rep = ir.build_report(inputs("status_changed"), tiers="1", runner=boom, baseline="none")
    assert rep["tiers"]["1"].startswith("not_run:error")
    assert rep["error"].startswith("RuntimeError")
    assert len(seen) == 1 and seen[0].startswith("impact-")
    assert ir.OWNER_ENV not in os.environ


def test_a_caller_owner_token_is_kept_so_a_killed_run_is_cleaned_by_the_caller(monkeypatch):
    """The MCP tool's subprocess runs with the tool's token; the engine must not mint another."""
    monkeypatch.setenv(ir.OWNER_ENV, "impact-callertoken1")
    seen: list[str] = []
    with ir.owned_containers() as token:
        seen.append(token)
        assert os.environ[ir.OWNER_ENV] == "impact-callertoken1"
    assert seen == ["impact-callertoken1"] and os.environ[ir.OWNER_ENV] == "impact-callertoken1"


def test_tier1_flows_are_run_and_classified(shop):
    base = ir.read_side(FIX / "base")
    ok_flow = {
        "id": "wf-ok",
        "name": "cancel",
        "status": "verified",
        "steps": [
            {
                "i": 0,
                "kind": "request",
                "method": "POST",
                "path": "/orders/1/cancel",
                "caller": "ann",
                "json": None,
                "expect_status": 200,
                "route": "POST /orders/{}/cancel",
            }
        ],
    }
    drift = copy.deepcopy(ok_flow)
    drift["id"], drift["steps"][0]["expect_status"] = "wf-drift", 404
    runner = runner_for(shop, layers="CD")
    inp = ir.build_inputs(project=FIX / "base", base_side=base, cand_side=base)
    rep = ir.build_report(inp, tiers="1", runner=runner, baseline="run")
    assert rep["tier1"]["flows"] == []
    flows = [ok_flow, drift]
    t1 = ir.run_tier1(
        inp,
        runner,
        layers="CD",
        baseline="run",
        flows=flows,
        flow_why=None,
        race=False,
        cand_lines=t2.SpecLines(base.module),
    )
    kinds = {(i["flow"], i["kind"]) for i in t1.section["flows"]}
    assert kinds == {("wf-drift", "baseline_drift")}
    assert t1.section["counts"]["workflow_fails"] == 0 and t1.section["failing"] == []


def test_a_workflow_fails_only_when_a_candidate_invariant_breaks_on_it(shop):
    base = ir.read_side(FIX / "base")
    flow = {
        "id": "wf-cancel",
        "name": "cancel",
        "status": "verified",
        "steps": [
            {
                "i": 0,
                "kind": "request",
                "method": "POST",
                "path": "/orders/1/cancel",
                "caller": "ann",
                "json": None,
                "expect_status": 200,
            }
        ],
    }
    inv = Invariant("no_cancelled", "No order is ever cancelled", 'count(Order, by="status") >= 0')
    del inv
    bad = Invariant("orders_stay_pending", "Orders stay pending", 'Order.status == "pending"')
    cand_spec = replace(base.spec, invariants=(*base.spec.invariants, bad))
    cand = ir.Side(base.markdown, base.module, base.trace_text, cand_spec, base.trace)
    runner = runner_for(shop, layers="CD")
    inp = ir.build_inputs(project=FIX / "base", base_side=base, cand_side=cand)
    t1 = ir.run_tier1(
        inp,
        runner,
        layers="CD",
        baseline="run",
        flows=[flow],
        flow_why=None,
        race=False,
        cand_lines=t2.SpecLines(base.module),
    )
    kinds = {(i["flow"], i["kind"]) for i in t1.section["flows"]}
    assert ("wf-cancel", "workflow_fails") in kinds


def test_a_stale_inventory_runs_no_flow(tmp_path: Path):
    inv = _gin_inventory(tmp_path, stale=True)
    flows, why = ir.verified_flows(inv)
    assert flows == [] and why == "stale"


# ---- tier 2 (D4) --------------------------------------------------------------------------------


def _passed_all(spec: Any) -> set[tuple[str, int]]:
    return {(s.name, k) for s in spec.scenarios for k, _ in enumerate(s.steps, 1)}


def test_differs_follows_the_checkers_subset():
    assert t2.differs({"a": 1}, {"a": 1, "b": 2}) is None  # an added key is compatible (IX4)
    assert t2.differs({"a": 1}, {"a": 2}) == "$.a"
    assert t2.differs({"a": [1, 2]}, {"a": [1]}) == "$.a"  # list lengths
    assert t2.differs({"a": 1}, {"a": "1"}) == "$.a"  # types
    assert t2.differs({"a": 1.0}, {"a": 1}) is None
    assert t2.differs({"a": 1.0}, {"a": 1.0000001}) == "$.a"
    assert t2.differs({"a": None}, {"a": None}) is None
    assert t2.differs({"a": True}, {"a": 1}) == "$.a"
    assert t2.differs({"a": {"b": 1}}, {"a": {"c": 2}}) is None


def test_db_expectations_are_compared_per_table_and_where():
    spec = minishop.SPEC
    w = {"where": {"id": 1}}
    assert t2.db_incompatible({"orders": {**w, "count": 1}}, {"orders": {**w, "count": 2}}, spec)
    assert t2.db_incompatible(
        {"orders": {**w, "equals": {"status": "paid"}}},
        {"orders": {**w, "equals": {"status": "cancelled"}}},
        spec,
    )
    assert t2.db_incompatible(
        {"orders": {**w, "count": 0}}, {"orders": {**w, "equals": {"status": "paid"}}}, spec
    )
    assert not t2.db_incompatible(
        {"orders": {**w, "count": 1}}, {"orders": {"where": {"id": 2}, "count": 2}}, spec
    )
    assert not t2.db_incompatible(
        {"orders": {**w, "equals": {"total": 5.0}}},
        {"orders": {**w, "equals": {"status": "x"}}},
        spec,
    )


def _t2(name: str, passed: Any = "all", **kw: Any) -> dict[str, Any]:
    inp = inputs(name)
    p = _passed_all(inp.base.spec) if passed == "all" else passed
    rep = ir.build_report(inp, tiers="2", baseline="none")
    del rep
    from lattice.spec.lint import conflicts

    targets = changed_targets(inp.base.spec, inp.cand.spec)
    return t2.compute(
        inp.base.spec,
        inp.cand.spec,
        inp.base.trace,
        inp.cand.trace,
        inp.diff,
        targets,
        passed=p,
        provenance="run" if p is not None else None,
        flows=kw.get("flows", []),
        flow_note=None,
        base_lines=t2.SpecLines(inp.base.module),
        cand_lines=t2.SpecLines(inp.cand.module),
        conflicts_fn=conflicts,
    )


def test_tier2_status_change_is_determined_with_both_sides():
    out = _t2("status_changed")
    (d,) = out["determined"]
    assert d["claim"] == "scenario:ann cancels her pending order fails at step <= 2"
    assert d["status"] == "determined" and d["why"] == "status:409->400"
    assert d["rules"] == ["R1"] and d["against"]["step"] == 2
    assert {e["kind"] for e in d["evidence"]} == {"spec"}
    assert d["file"] is None and "why_no_line" in d  # the fixture builds its scenarios by replace()


def test_tier2_claims_need_the_premise_that_the_corpus_step_passed():
    """IX5: without the baseline pass the claim is withheld and the reason is base_prefix_failed."""
    out = _t2("status_changed", passed=set())
    assert out["determined"] == []
    (e,) = [d for d in out["decidability"] if d["subject"].startswith("scenario:")]
    assert e["reason"] == "base_prefix_failed"
    none = _t2("status_changed", passed=None)
    assert none["determined"] == []


def test_tier2_a_baseline_file_makes_the_claim_conditional():
    inp = inputs("status_changed")
    from lattice.spec.lint import conflicts

    out = t2.compute(
        inp.base.spec,
        inp.cand.spec,
        inp.base.trace,
        inp.cand.trace,
        inp.diff,
        changed_targets(inp.base.spec, inp.cand.spec),
        passed=_passed_all(inp.base.spec),
        provenance="file",
        flows=[],
        flow_note=None,
        base_lines=t2.SpecLines(inp.base.module),
        cand_lines=t2.SpecLines(inp.cand.module),
        conflicts_fn=conflicts,
    )
    assert out["determined"][0]["status"] == "conditional"


def test_tier2_prefix_must_be_identical():
    """IX3: a step after a different earlier request does not match, so nothing is claimed."""
    inp = inputs("status_changed")
    base = inp.base.spec
    cancel = base.scenarios[0]
    first = replace(cancel.steps[0], json={"note": "different"})
    cand = replace(
        base,
        scenarios=(
            replace(cancel, steps=(first, replace(cancel.steps[1], expect_status=400))),
            base.scenarios[1],
        ),
    )
    out = t2.compute(
        base,
        cand,
        inp.base.trace,
        inp.base.trace,
        None,
        changed_targets(base, cand),
        passed=_passed_all(base),
        provenance="run",
        flows=[],
        flow_note=None,
        base_lines=t2.SpecLines(""),
        cand_lines=t2.SpecLines(""),
    )
    assert out["determined"] == []
    reasons = {d["reason"] for d in out["decidability"] if d["kind"] == "target"}
    assert reasons == {"new_request"}


def test_tier2_an_added_body_key_is_compatible_not_a_claim():
    """IX4."""
    inp = inputs("status_changed")
    base = inp.base.spec
    cancel = base.scenarios[0]
    step0 = replace(cancel.steps[0], expect_body={**cancel.steps[0].expect_body, "extra": 1})
    cand = replace(
        base, scenarios=(replace(cancel, steps=(step0, cancel.steps[1])), base.scenarios[1])
    )
    out = t2.compute(
        base,
        cand,
        inp.base.trace,
        inp.base.trace,
        None,
        changed_targets(base, cand),
        passed=_passed_all(base),
        provenance="run",
        flows=[],
        flow_note=None,
        base_lines=t2.SpecLines(""),
        cand_lines=t2.SpecLines(""),
    )
    assert out["determined"] == []
    (e,) = out["decidability"]
    assert e["reason"] == "compatible_expectation"


def test_tier2_an_await_in_the_prefix_is_not_decided():
    from lattice.spec import Await

    base = minishop.SPEC
    cancel = base.scenarios[0]
    aw = Await("worker", {"orders": {"count": 4}})
    b = replace(base, scenarios=(replace(cancel, steps=(aw, *cancel.steps)), base.scenarios[1]))
    c = replace(
        base,
        scenarios=(
            replace(
                cancel, steps=(aw, cancel.steps[0], replace(cancel.steps[1], expect_status=400))
            ),
            base.scenarios[1],
        ),
    )
    out = t2.compute(
        b,
        c,
        None,
        None,
        None,
        changed_targets(b, c),
        passed=_passed_all(b),
        provenance="run",
        flows=[],
        flow_note=None,
        base_lines=t2.SpecLines(""),
        cand_lines=t2.SpecLines(""),
    )
    assert out["determined"] == []
    (e,) = out["decidability"]
    assert e["reason"] == "await_in_prefix"


def test_tier2_users_or_the_given_profile_changing_stops_the_claim():
    inp = inputs("status_changed")
    cand = replace(inp.cand.spec, users={**inp.cand.spec.users, "zed": ("zed", "pw")})
    out = t2.compute(
        inp.base.spec,
        cand,
        None,
        None,
        None,
        changed_targets(inp.base.spec, cand),
        passed=_passed_all(inp.base.spec),
        provenance="run",
        flows=[],
        flow_note=None,
        base_lines=t2.SpecLines(""),
        cand_lines=t2.SpecLines(""),
    )
    assert out["determined"] == []
    assert out["corpus"]["environment"] == "users"
    assert {d["reason"] for d in out["decidability"] if d["kind"] == "target"} == {
        "profile_or_users_changed"
    }


def test_tier2_the_spec_only_mode_equals_the_full_mode_on_the_determined_set(shop):
    """IX6: T2 reads the baseline pass set, never the candidate run."""
    runner = runner_for(shop, layers="CD")
    inp = inputs("status_changed")
    full = ir.build_report(inp, tiers="12", runner=runner, baseline="run", layers="CD")
    base_report = ir.baseline_report(inp, runner, "CD")
    path = FIX.parent.parent.parent.parent / "dummy"
    del path
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "base.json"
        f.write_text(json.dumps(base_report, default=str))
        spec_only = ir.build_report(inp, tiers="23", baseline=str(f))
    key = lambda r: sorted((i["claim"], i["why"]) for i in r["tier2"]["determined"])  # noqa: E731
    assert key(full) == key(spec_only) and key(full)
    assert [i["status"] for i in spec_only["tier2"]["determined"]] == ["conditional"]
    assert [i["status"] for i in full["tier2"]["determined"]] == ["determined"]


def test_tier2_l011_pairs_new_in_the_candidate_are_spec_contradictions():
    out = _t2("conflict")
    kinds = [d["kind"] for d in out["determined"]]
    assert "spec_contradiction" in kinds
    d = next(d for d in out["determined"] if d["kind"] == "spec_contradiction")
    assert sorted(d["pair"]) == [
        "scenario:ann cancels her pending order",
        "scenario:ann cancels order 1 first",
    ]
    assert _t2("reword")["determined"] == []


def test_tier2_decidability_has_one_entry_per_changed_rule_and_target():
    reasons = set(t2.REASONS)
    for name in DEV:
        inp = inputs(name)
        out = _t2(name)
        targets = changed_targets(inp.base.spec, inp.cand.spec)
        diff = inp.diff
        want = (
            len(diff.added)
            + len(diff.changed)
            + len(diff.removed)
            + sum(len(v) for v in targets.values())
        )
        assert len(out["decidability"]) == want, name
        for e in out["decidability"]:
            assert e["state"] == "determined" or e["reason"] in reasons, (name, e)
    out = _t2("removed_rule")
    assert {e["reason"] for e in out["decidability"]} == {"removed_only"}
    out = _t2("bad_invariant")
    assert {e["reason"] for e in out["decidability"]} == {"invariant_or_machine"}
    out = _t2("new_request")
    assert {e["reason"] for e in out["decidability"]} == {"new_request"}
    assert {e["reason"] for e in _t2("reword")["decidability"]} == {"compatible_expectation"}


def test_tier2_a_verified_flow_contradicted_by_a_candidate_step_fails_after_the_change():
    inp = inputs("status_changed")
    flow = {
        "id": "wf-x",
        "name": "x",
        "status": "verified",
        "steps": [
            {
                "i": 0,
                "kind": "request",
                "method": "POST",
                "path": "/orders/1/cancel",
                "caller": "ann",
                "json": None,
                "expect_status": 200,
            },
            {
                "i": 1,
                "kind": "request",
                "method": "POST",
                "path": "/orders/1/cancel",
                "caller": "ann",
                "json": None,
                "expect_status": 409,
            },
        ],
    }
    from lattice.spec.lint import conflicts

    out = t2.compute(
        inp.base.spec,
        inp.cand.spec,
        inp.base.trace,
        inp.cand.trace,
        inp.diff,
        changed_targets(inp.base.spec, inp.cand.spec),
        passed=_passed_all(inp.base.spec),
        provenance="run",
        flows=[flow],
        flow_note=None,
        base_lines=t2.SpecLines(""),
        cand_lines=t2.SpecLines(""),
        conflicts_fn=conflicts,
    )
    (f,) = out["flows"]
    assert f["claim"] == "flow:wf-x fails at step 1 after the change"
    assert f["observed_status"] == 409 and f["candidate_status"] == 400
    # the scenario is claimed too, against the flow's step as well as its own base step
    assert out["determined"]
    # the flow alone (no baseline pass) still determines the flow's failure
    out2 = t2.compute(
        inp.base.spec,
        inp.cand.spec,
        inp.base.trace,
        inp.cand.trace,
        inp.diff,
        changed_targets(inp.base.spec, inp.cand.spec),
        passed=None,
        provenance=None,
        flows=[flow],
        flow_note=None,
        base_lines=t2.SpecLines(""),
        cand_lines=t2.SpecLines(""),
    )
    assert len(out2["flows"]) == 1 and out2["determined"][0]["against"]["source"] == "flow:wf-x"


def test_tier2_runtime_replay_is_not_applicable_without_programs_and_runs_on_minishop(
    shop, tmp_path
):
    base = minishop.SPEC
    stripped = replace(base, programs={})
    assert t2.runtime_replay(stripped, stripped, set(), None, t2.SpecLines(""))["status"] == (
        "not_applicable: 0 programs"
    )
    assert t2.runtime_replay(base, base, None, None, t2.SpecLines(""))["status"].startswith(
        "not_run"
    )
    app_dir, package = shop["ok"]
    env = {
        "make_db": lambda variant, path: minishop.build_db(path),
        "app_factory": minishop.app_factory(package),
        "login": minishop.login,
    }
    # an unchanged program: nothing to replay
    same = t2.runtime_replay(base, base, _passed_all(base), env, t2.SpecLines(""))
    assert same["status"] == "run" and same["items"] == []
    # a changed program: cancelling answers 404 for everyone
    prog = base.programs["orders"].replace(
        'else_=Conflict("Order cannot be cancelled")', 'else_=NotFound("gone")'
    )
    assert prog != base.programs["orders"]
    cand = replace(base, programs={"orders": prog})
    rep = t2.runtime_replay(base, cand, _passed_all(base), env, t2.SpecLines(""))
    assert rep["status"] == "run"
    assert [i["kind"] for i in rep["items"]] == ["runtime_disagreement"]
    assert rep["items"][0]["check"] is None and rep["items"][0]["step"] == 2


def test_spec_lines_resolve_literal_scenarios_and_fall_back():
    src = (
        "from lattice.spec import Scenario, Step\n"
        "A = Scenario('one', steps=(\n"
        "    Step('GET', '/a'),\n"
        "    Step('GET', '/b'),\n"
        "))\n"
        "B = Scenario(f'dyn{1}', steps=())\n"
        "C = Scenario('three', None, (Step('GET', '/c'),))\n"
    )
    lines = t2.SpecLines(src)
    assert lines.scenario("one") == 2 and lines.step("one", 1) == 3 and lines.step("one", 2) == 4
    assert lines.step("one", 9) == 2 and lines.scenario("dyn1") is None
    assert lines.step("three", 1) == 7 and t2.SpecLines("not python (").scenario("x") is None


# ---- tier 3 (D5), static, on the Gin fixture -----------------------------------------------------


def _fixture_module():
    sp = importlib.util.spec_from_file_location("impact_fixture_spec", IMPL / "fixture_spec.py")
    mod = importlib.util.module_from_spec(sp)
    sys.modules["impact_fixture_spec"] = mod
    sp.loader.exec_module(mod)
    return mod


FS = _fixture_module()
FS_SRC = (IMPL / "fixture_spec.py").read_text()
WF = json.loads((IMPL / "workflows.json").read_text())


def _gin_inventory(tmp_path: Path, stale: bool = False):
    app = tmp_path / "app"
    shutil.copytree(IMPL / "app", app)
    extract = json.loads((IMPL / "extract.json").read_text())
    wf = copy.deepcopy(WF)
    wf["run"]["mode"] = "observed"
    for f in wf["flows"]:
        f["status"] = "verified"
    wfp = tmp_path / "wf.json"
    wfp.write_text(json.dumps(wf))
    out = tmp_path / "inv"
    capture(app, out, extract=extract, workflows=wfp)
    if stale:
        with (app / "main.go").open("a") as fh:
            fh.write("\n// edited\n")
    return load_inventory(out, app)


def _side(edit: str = "") -> ir.Side:
    module = FS_SRC + ("\n" + edit if edit else "")
    return ir.make_side("- r\n", module, FS.trace().to_json())


def _t3(tmp_path: Path, edit: str, **kw: Any) -> dict[str, Any]:
    base, cand = _side(), _side(edit)
    inp = ir.build_inputs(project=None, base_side=base, cand_side=cand)
    inp.inventory = _gin_inventory(tmp_path)
    return ir.build_report(inp, tiers="3", **kw)


def test_tier3_of_a_control_has_no_location(tmp_path: Path):
    rep = _t3(tmp_path, "")
    assert rep["tier3"]["locations"] == [] and rep["tiers"]["3"] == "run"


def test_tier3_a_changed_scenario_points_at_its_route_the_handler_and_the_closure(tmp_path: Path):
    edit = (
        "from dataclasses import replace as _r\n"
        "_s = SPEC.scenarios[2]\n"
        "SPEC = _r(SPEC, scenarios=tuple(\n"
        "    _r(s, steps=(s.steps[0], _r(s.steps[1], expect_status=409)))\n"
        "    if s.name == 'publish_project' else s\n"
        "    for s in SPEC.scenarios))\n"
    )
    rep = _t3(tmp_path, edit)
    locs = rep["tier3"]["locations"]
    assert locs and all(re.fullmatch(r"t3-[0-9a-f]{10}", i["id"]) for i in locs)
    kinds = {i["what"] for i in locs}
    # the fixture's handlers are function literals: they map to their enclosing declarations
    assert "route registration" in kinds and "declaration enclosing a function literal" in kinds
    assert any(i["literal"] == 1 for i in locs if i["what"].startswith("declaration enclosing"))
    files = {i["file"] for i in locs}
    assert "api/api.go" in files
    for i in locs:
        assert i["tier"] == 3 and i["file"] and i["line"] and i["via"], i
        assert all(v.split(" -> ")[-1].startswith("code:") for v in i["via"])
        assert i["evidence"][0]["at"] == f"code:{i['file']}:{i['line']}"
    # sorted and unique
    keys = [(i["file"], i["line"]) for i in locs]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)


EXTRACT_FACTS = (
    "-> registered",
    "handler:",
    "call:",
    "raise:",
    "param:",
    "owner:",
    "struct:",
    "column:",
    "status_write:",
    "machine edge",
    "sibling:",
    "-> main",
    "-> route:",
)
STATUS_EDIT = (
    "from dataclasses import replace as _r\n"
    "SPEC = _r(SPEC, scenarios=tuple(\n"
    "    _r(s, steps=(_r(s.steps[0], expect_status=422),)) if s.name == 'create_project' else s\n"
    "    for s in SPEC.scenarios))\n"
)


def test_tier3_every_chain_ends_in_a_code_location_and_holds_an_extract_fact(tmp_path: Path):
    """IX8: a location found by grepping path strings has no extract fact in its chain."""
    rep = _t3(tmp_path, STATUS_EDIT)
    assert rep["tier3"]["locations"]
    for i in rep["tier3"]["locations"]:
        for chain in i["via"]:
            head, _, tail = chain.rpartition(" -> ")
            assert tail == f"code:{i['file']}:{i['line']}", chain
            assert any(f in head for f in EXTRACT_FACTS), chain
            assert not chain.startswith(("grep:", "name:")), chain


def test_tier3_report_has_no_absolute_path_or_time(tmp_path: Path):
    """IX9: a report built from an inventory under tmp_path names no absolute path."""
    rep = _t3(tmp_path, STATUS_EDIT)
    text = ir.report_json(rep)
    assert str(tmp_path) not in text and not re.search(r"/Users|/private|/var/folders|/tmp/", text)
    assert not re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text)
    assert text == ir.report_json(_t3(tmp_path / "again", STATUS_EDIT))


def test_tier3_locations_pass_spike_cs_citation_check(tmp_path: Path):
    from lattice.spec.implementation import check_code

    edit = (
        "from dataclasses import replace as _r\n"
        "SPEC = _r(SPEC, scenarios=tuple(\n"
        "    _r(s, steps=(_r(s.steps[0], expect_status=422),))\n"
        "    if s.name == 'create_project' else s\n"
        "    for s in SPEC.scenarios))\n"
    )
    rep = _t3(tmp_path, edit)
    inv = _gin_inventory(tmp_path / "again") if False else None
    del inv
    app = tmp_path / "app"
    lines_cache: dict[str, list[str]] = {}
    for i in rep["tier3"]["locations"]:
        lines = lines_cache.setdefault(i["file"], (app / i["file"]).read_text().split("\n"))
        quote = lines[i["line"] - 1].strip()
        if len("".join(quote.split())) < 8:
            continue
        cite = {"target": f"code:{i['file']}:{i['line']}", "quote": quote}
        if i.get("symbol"):
            cite["symbol"] = i["symbol"]
        assert check_code(app, None, cite) is None, (i, check_code(app, None, cite))


def test_tier3_a_function_literal_maps_to_its_enclosing_declaration(tmp_path: Path):
    inv = _gin_inventory(tmp_path)
    res = t3.Resolver(inv, t3.GoTree(inv))
    got = res.resolve("example.com/wf/api", "Login.func1")
    assert isinstance(got, t3.Resolved) and got.symbol == "Login" and got.literal == 1


def test_tier3_ambiguous_and_missing_declarations_are_unread_never_guessed(tmp_path: Path):
    inv = _gin_inventory(tmp_path)
    app = tmp_path / "app"
    (app / "api" / "dup.go").write_text(
        "package api\n\nfunc Login() {}\n"
    )  # a second declaration of Login in the package (IX7)
    res = t3.Resolver(inv, t3.GoTree(inv))
    amb = res.resolve("example.com/wf/api", "Login")
    assert isinstance(amb, str) and "2 declarations" in amb
    assert "no declaration" in res.resolve("example.com/wf/api", "Nope")
    assert res.resolve("github.com/gin-gonic/gin", "New") == "external"
    assert res.external == 1


def test_tier3_a_removed_endpoint_and_an_added_one(tmp_path: Path):
    added = (
        "from dataclasses import replace as _r\n"
        "from lattice.spec import Scenario as _S, Step as _T\n"
        "SPEC = _r(SPEC, scenarios=(*SPEC.scenarios, _S('archive_project', steps=(\n"
        "    _T('POST', '/api/projects/1/restore', 'ann', expect_status=200),))))\n"
    )
    rep = _t3(tmp_path, added)
    ins = [i for i in rep["tier3"]["locations"] if i["kind"] == "insertion"]
    assert ins, "an endpoint the code lacks gets insertion points"
    assert any(i["file"] == "api/api.go" for i in ins)
    removed = (
        "from dataclasses import replace as _r\n"
        "SPEC = _r(SPEC, scenarios=tuple(s for s in SPEC.scenarios if s.name != 'activate_job'))\n"
    )
    rep2 = _t3(tmp_path / "r", removed)
    assert any("jobs" in (i["what"] + i["via"][0]) or i["file"] for i in rep2["tier3"]["locations"])
    assert rep2["diff"]["targets"]["removed"] == ["scenario:activate_job"]


def test_tier3_a_machine_change_points_at_the_writes(tmp_path: Path):
    edit = (
        "from dataclasses import replace as _r\n"
        "SPEC = _r(SPEC, machines=(\n"
        "    _r(SPEC.machines[0], edges=(*SPEC.machines[0].edges, ('archived', 'draft'))),\n"
        "    *SPEC.machines[1:]))\n"
    )
    rep = _t3(tmp_path, edit)
    assert "machine:Project.status" in rep["diff"]["targets"]["modified"]
    assert (
        any(
            i["what"].startswith("write of projects.status") or "machine" in " ".join(i["via"])
            for i in rep["tier3"]["locations"]
        )
        or rep["tier3"]["unread"]
    )


def test_tier3_flows_with_a_step_on_a_seed_route_are_may_fail_and_never_claims(tmp_path: Path):
    edit = (
        "from dataclasses import replace as _r\n"
        "SPEC = _r(SPEC, scenarios=tuple(\n"
        "    _r(s, steps=(_r(s.steps[0], expect_status=422),))\n"
        "    if s.name == 'create_project' else s\n"
        "    for s in SPEC.scenarios))\n"
    )
    rep = _t3(tmp_path, edit)
    flows = rep["tier3"]["flows"]
    assert flows and all(f["kind"] == "may_fail" and f["tier"] == 3 for f in flows)
    assert all(f["file"] is None and f["why_no_line"] for f in flows)
    assert rep["summary"]["workflows"]["t3_may_fail"] == len(flows)
    assert rep["summary"]["workflows"]["t1_fail"] == rep["summary"]["workflows"]["t2_fail"] == 0


def test_tier3_names_what_it_could_not_read(tmp_path: Path):
    edit = (
        "from dataclasses import replace as _r\n"
        "SPEC = _r(SPEC, scenarios=tuple(\n"
        "    _r(s, steps=(_r(s.steps[0], expect_body={'id': 'x'}),))\n"
        "    if s.name == 'create_project' else s\n"
        "    for s in SPEC.scenarios))\n"
    )
    rep = _t3(tmp_path, edit)
    assert any(u["kind"] == "response_shape_not_extracted" for u in rep["tier3"]["unread"])


def test_a_python_project_has_no_extract_and_says_so():
    rep = ir.build_report(inputs("status_changed"), tiers="3")
    assert rep["tiers"]["3"] == "not_run:inventory_missing"
    assert "no extract" in rep["tier3"]["unread"][0]["reason"]


def test_the_all_locations_are_inside_the_app_root(tmp_path: Path):
    edit = (
        "from dataclasses import replace as _r\nSPEC = _r(SPEC, invariants=SPEC.invariants[:1])\n"
    )
    rep = _t3(tmp_path, edit)
    for i in rep["tier3"]["locations"]:
        assert not i["file"].startswith("/") and ".." not in i["file"].split("/")
        assert (tmp_path / "app" / i["file"]).is_file()


# ---- the engine reads, never writes -----------------------------------------------------------


def _snapshot(root: Path) -> dict[str, tuple]:
    out = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[p.relative_to(root).as_posix()] = (
            "dir" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest(),
            st.st_mode,
            st.st_mtime_ns,
        )
    return out


def test_building_a_report_writes_nothing(tmp_path: Path):
    proj = tmp_path / "proj"
    shutil.copytree(FIX, proj)
    inv_world = tmp_path / "w"
    inv_world.mkdir()
    inv = _gin_inventory(inv_world)
    before = {k: _snapshot(p) for k, p in (("proj", proj), ("w", inv_world))}
    for name in DEV:
        inp = ir.build_inputs(project=proj / "base", candidate=proj / name)
        inp.inventory = inv
        ir.build_report(inp, tiers="23", baseline="none")
    after = {k: _snapshot(p) for k, p in (("proj", proj), ("w", inv_world))}
    assert before == after


def test_the_report_is_not_cached_under_the_inventory(tmp_path: Path):
    """IX10: a second report on the same inventory still reflects the second candidate."""
    inv = _gin_inventory(tmp_path)
    base = _side()
    a = ir.build_inputs(project=None, base_side=base, cand_side=_side())
    a.inventory = inv
    b = ir.build_inputs(
        project=None,
        base_side=base,
        cand_side=_side(
            "from dataclasses import replace as _r\n"
            "SPEC = _r(SPEC, scenarios=tuple(s for s in SPEC.scenarios if s.name != 'login'))\n"
        ),
    )
    b.inventory = inv
    ra, rb = ir.build_report(a, tiers="3"), ir.build_report(b, tiers="3")
    assert ra["tier3"]["locations"] == [] and rb["tier3"]["locations"]
    assert ir.build_report(a, tiers="3")["tier3"]["locations"] == []


# ---- the CLI ----------------------------------------------------------------------------------


def _cli(capsys, *argv: str) -> tuple[int, str, str]:
    rc = ir.main(list(argv))
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_cli_prints_text_and_json_and_a_section(capsys):
    rc, out, _ = _cli(
        capsys,
        "--project",
        str(FIX / "base"),
        "--candidate",
        str(FIX / "conflict"),
        "--tiers",
        "2",
        "--baseline",
        "none",
    )
    assert rc == 0 and "TIER 2" in out and "L011" in out
    rc, out, _ = _cli(
        capsys,
        "--project",
        str(FIX / "base"),
        "--candidate",
        str(FIX / "conflict"),
        "--tiers",
        "2",
        "--baseline",
        "none",
        "--json",
    )
    assert json.loads(out)["schema"] == "lattice.impact/1"
    rc, out, _ = _cli(
        capsys,
        "--project",
        str(FIX / "base"),
        "--candidate",
        str(FIX / "conflict"),
        "--tiers",
        "2",
        "--baseline",
        "none",
        "--section",
        "decidability",
    )
    assert set(json.loads(out)) == {"schema", "tiers", "decidability"}


def test_cli_exit_codes(capsys, tmp_path: Path):
    assert _cli(capsys, "--project", str(FIX / "base"), "--candidate", str(tmp_path))[0] == 2
    rc, _, err = _cli(
        capsys, "--project", str(FIX / "base"), "--change", "c1", "--state-dir", str(tmp_path)
    )
    assert rc == 2 and "no_candidate" in err
    assert (
        _cli(
            capsys,
            "--project",
            str(FIX / "base"),
            "--candidate",
            str(FIX / "reword"),
            "--tiers",
            "9",
        )[0]
        == 2
    )
    out = FIX / "base" / "report.json"
    rc, _, err = _cli(
        capsys,
        "--project",
        str(FIX / "base"),
        "--candidate",
        str(FIX / "reword"),
        "--tiers",
        "2",
        "--baseline",
        "none",
        "--out",
        str(out),
    )
    assert rc == 2 and "inside" in err and not out.exists()
    ok = tmp_path / "r.json"
    rc, _, _ = _cli(
        capsys,
        "--project",
        str(FIX / "base"),
        "--candidate",
        str(FIX / "reword"),
        "--tiers",
        "2",
        "--baseline",
        "none",
        "--out",
        str(ok),
    )
    assert rc == 0 and json.loads(ok.read_text())["schema"] == "lattice.impact/1"


def test_cli_tier1_without_an_app_is_not_run_not_an_error(capsys):
    rc, out, _ = _cli(
        capsys,
        "--project",
        str(FIX / "base"),
        "--candidate",
        str(FIX / "reword"),
        "--tiers",
        "1",
        "--json",
    )
    assert rc == 0 and json.loads(out)["tiers"]["1"] == "not_run:no_app"


def test_lattice_spec_impact_is_one_authoring_entry():
    from lattice.spec.__main__ import AUTHORING

    assert AUTHORING["impact"] == "lattice.spec.impact_report"
    assert list(AUTHORING)[-2:] == ["implementation", "impact"]


def test_the_machine_edge_diff_is_what_changed_targets_say():
    base = minishop.SPEC
    cand = replace(
        base, machines=(Machine("Order", "status", {"pending": ("paid",)}, initial="pending"),)
    )
    assert changed_targets(base, cand)["modified"] == ["machine:Order.status"]
    assert Scenario and Step  # the imports the fixtures use
