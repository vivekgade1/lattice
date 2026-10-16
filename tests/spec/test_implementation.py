"""lattice.spec.implementation (Spike C, D1-D7): the inventory loader and its staleness, the
workflows adapter, the report (statuses, orphans, exclusions, determinism), the CLI, and that
reading writes nothing. The app is the fixture in tests/spec/fixtures/implementation/."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice.importer.inventory import capture
from lattice.spec import implementation as impl
from lattice.spec.implementation import (
    ImplementationError,
    build_report,
    canonical_json,
    load_inventory,
    report_json,
    route_key,
    sha256_bytes,
    template_path,
)
from lattice.spec.rules import Rule

FIX = Path(__file__).resolve().parent / "fixtures" / "implementation"
APP = FIX / "app"
EXTRACT = json.loads((FIX / "extract.json").read_text())
WF = json.loads((FIX / "workflows.json").read_text())
ROOT = Path(__file__).resolve().parents[2]
REAL_WF = ROOT / "docs/results/data/2026-10-15-workflow-layer/workflows.json"


def _fixture_spec():
    sp = importlib.util.spec_from_file_location("impl_fixture_spec", FIX / "fixture_spec.py")
    mod = importlib.util.module_from_spec(sp)
    sys.modules["impl_fixture_spec"] = mod
    sp.loader.exec_module(mod)
    return mod


FS = _fixture_spec()


def tree(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        out[p.relative_to(root).as_posix()] = "dir" if p.is_dir() else sha256_bytes(p.read_bytes())
    return out


def observed(doc: dict[str, Any], *, verify=lambda f: True, machines="confirmed") -> dict[str, Any]:
    """The static workflows document as a dynamic run would write it: mode observed, the flows
    `verify` accepts verified, the machines `machines`."""
    d = copy.deepcopy(doc)
    d["run"]["mode"] = "observed"
    for f in d["flows"]:
        f["status"] = "verified" if verify(f) else "failed"
    for m in d["machines"]:
        m["status"] = machines
    return d


@pytest.fixture
def world(tmp_path: Path):
    """app copy + a function making an inventory directory from an extract and workflows."""
    app = tmp_path / "app"
    shutil.copytree(APP, app)

    def make(workflows: Any = WF, extract: Any = EXTRACT, name: str = "inv") -> Path:
        out = tmp_path / name
        wf_path = None
        if workflows is not None:
            wf_path = tmp_path / f"{name}.wf.json"
            wf_path.write_text(json.dumps(workflows))
        capture(app, out, extract=extract, workflows=wf_path)
        return out

    return app, make


def report_of(world, workflows: Any = WF, trace: Any = "fixture", **kw: Any) -> dict[str, Any]:
    app, make = world
    inv = load_inventory(make(workflows, **kw), app)
    tr = FS.trace() if trace == "fixture" else trace
    return build_report(FS.SPEC, tr, inv)


def by_id(report: dict[str, Any], section: str = "rules") -> dict[str, dict[str, Any]]:
    return {e["id"]: e for e in report[section]}


# ---- keys ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "want"),
    [
        ("/v1/habits/:id/completions", "/v1/habits/{}/completions"),
        ("/files/*filepath", "/files/{}"),
        ("/v1/habits/{habit_id}", "/v1/habits/{}"),
        ("/v1/habits/123", "/v1/habits/{}"),
        (
            "/v1/habits/3f2504e0-4f89-41d3-9a0c-0305e82c3301/completions",
            "/v1/habits/{}/completions",
        ),
        ("/v1/me/", "/v1/me"),
        ("/v1/me?x=1", "/v1/me"),
        ("/", "/"),
        ("/health", "/health"),
    ],
)
def test_template_path(path: str, want: str) -> None:
    assert template_path(path) == want


def test_route_key_upper_cases_the_method() -> None:
    assert route_key("get", "/a/:b") == "GET /a/{}"


# ---- the loader and staleness (D1) --------------------------------------------------------


def test_missing_directory_and_missing_inventory_json(tmp_path: Path) -> None:
    with pytest.raises(ImplementationError) as e:
        load_inventory(tmp_path / "nope", tmp_path)
    assert e.value.code == "inventory_missing" and str(e.value).startswith("inventory_missing:")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ImplementationError) as e2:
        load_inventory(tmp_path / "empty", tmp_path)
    assert e2.value.code == "inventory_missing"


def test_a_tampered_or_broken_inventory_is_bad_inventory(world) -> None:
    app, make = world
    inv = make()
    ex = inv / "extract.json"
    ex.write_text(ex.read_text() + " ")
    with pytest.raises(ImplementationError) as e:
        load_inventory(inv, app)
    assert e.value.code == "bad_inventory"
    inv2 = make(name="inv2")
    (inv2 / "inventory.json").write_text("{not json")
    with pytest.raises(ImplementationError) as e2:
        load_inventory(inv2, app)
    assert e2.value.code == "bad_inventory"
    inv3 = make(name="inv3")
    meta = json.loads((inv3 / "inventory.json").read_text())
    meta["schema"] = "other/9"
    (inv3 / "inventory.json").write_text(canonical_json(meta))
    with pytest.raises(ImplementationError) as e3:
        load_inventory(inv3, app)
    assert e3.value.code == "bad_inventory"
    inv4 = make(name="inv4")
    (inv4 / "workflows.json").write_text("{}")
    with pytest.raises(ImplementationError) as e4:
        load_inventory(inv4, app)
    assert e4.value.code == "bad_inventory" and "workflows" in str(e4.value)


def test_a_fresh_inventory_is_not_stale(world) -> None:
    app, make = world
    assert load_inventory(make(), app).stale is None


def test_stale_names_changed_added_and_removed(world) -> None:
    app, make = world
    inv_dir = make()
    (app / "main.go").write_text((app / "main.go").read_text() + "\n// edit\n")
    (app / "extra.go").write_text("package main\n")
    (app / "models" / "models.go").unlink()
    inv = load_inventory(inv_dir, app)
    assert inv.stale["changed"] == ["main.go"]
    assert inv.stale["added"] == ["extra.go"]
    assert inv.stale["removed"] == ["models/models.go"]


def test_stale_lists_are_sorted_and_capped_with_counts(world) -> None:
    app, make = world
    inv_dir = make()
    for i in range(25):
        (app / f"gen{i:02d}.go").write_text(f"package main // {i}\n")
    st = load_inventory(inv_dir, app).stale
    assert st["counts"]["added"] == 25 and len(st["added"]) == 20
    assert st["added"] == sorted(st["added"])


def test_a_non_source_edit_is_not_stale(world) -> None:
    app, make = world
    inv_dir = make()
    (app / "README.md").write_text("hello")
    (app / "vendor").mkdir()
    (app / "vendor" / "v.go").write_text("package v\n")
    assert load_inventory(inv_dir, app).stale is None


def test_stale_turns_every_verified_into_unverified_with_the_reason(world) -> None:
    app, make = world
    inv_dir = make(observed(WF))
    fresh = build_report(FS.SPEC, FS.trace(), load_inventory(inv_dir, app))
    assert by_id(fresh)["R2"]["status"] == "implemented_verified"
    (app / "api" / "api.go").write_text((app / "api" / "api.go").read_text() + "\n// edit\n")
    rep = build_report(FS.SPEC, FS.trace(), load_inventory(inv_dir, app))
    assert rep["inventory"]["stale"]["changed"] == ["api/api.go"]
    assert rep["summary"]["requirements"]["implemented_verified"] == 0
    assert by_id(rep)["R2"]["status"] == "implemented_unverified"
    assert by_id(rep)["R2"]["reasons"] == ["stale"]
    assert rep["summary"]["flows"].get("verified", 0) == 0
    # the facts themselves still read from the inventory (labelled stale), not guessed
    assert by_id(rep)["R4"]["status"] == "required_not_implemented"


# ---- the workflows adapter (D2) -----------------------------------------------------------


def test_the_adapter_compares_the_route_set_when_the_hash_is_the_extractors(world) -> None:
    app, make = world
    inv = load_inventory(make(), app)
    assert inv.consistency == "route-set" and inv.mismatch is None
    assert any("route set" in n for n in inv.notes)


def test_the_adapter_accepts_a_matching_extract_hash(world) -> None:
    app, make = world
    inv_dir = make()
    sha = json.loads((inv_dir / "inventory.json").read_text())["extract_sha256"]
    doc = copy.deepcopy(WF)
    doc["app"]["extract_sha256"] = sha
    doc["flows"][0]["steps"][0]["route"] = "GET /not/in/the/extract"
    inv = load_inventory(make(doc, name="inv2"), app)
    assert inv.consistency == "sha256" and inv.mismatch is None


def test_a_workflows_file_of_another_module_is_a_mismatch(world) -> None:
    app, make = world
    doc = copy.deepcopy(WF)
    doc["app"]["module"] = "other"
    inv = load_inventory(make(observed(doc)), app)
    assert "module" in inv.mismatch


def test_a_step_route_the_extract_lacks_is_a_mismatch_and_nothing_verifies(world) -> None:
    app, make = world
    doc = observed(WF)
    doc["flows"][0]["steps"][0]["route"] = "POST /api/never"
    rep = build_report(FS.SPEC, FS.trace(), load_inventory(make(doc), app))
    assert "POST /api/never" in rep["inventory"]["workflows_mismatch"]
    assert rep["summary"]["requirements"]["implemented_verified"] == 0
    assert by_id(rep)["R2"]["reasons"] == ["workflows_mismatch"]
    assert rep["summary"]["flows"].get("verified", 0) == 0  # flows are shown, never verified


def test_a_registration_line_that_moved_is_a_mismatch(world) -> None:
    app, make = world
    doc = observed(WF)
    for s in doc["flows"][0]["steps"]:
        for ev in s.get("evidence", []):
            if ev["what"] == "route":
                ev["at"] = ev["at"].rsplit(":", 1)[0] + ":999"
    inv = load_inventory(make(doc), app)
    assert "at another line" in inv.mismatch


def test_the_adapter_falls_back_to_the_skeleton_when_a_step_has_no_route(world) -> None:
    app, make = world
    doc = observed(WF)
    for f in doc["flows"]:
        for s in f["steps"]:
            s.pop("route", None)
    inv = load_inventory(make(doc), app)
    assert inv.mismatch is None
    f0 = inv.workflows["flows"][0]
    assert [s["route"] for s in f0["steps"] if s["kind"] == "request"] == f0["skeleton"]
    assert any("taken from the skeleton" in n for n in inv.notes)


def test_a_status_the_adapter_does_not_know_is_not_verified(world) -> None:
    app, make = world
    doc = observed(WF)
    for f in doc["flows"]:
        f["status"] = "replayed-ok"
    rep = build_report(FS.SPEC, FS.trace(), load_inventory(make(doc), app))
    assert rep["summary"]["requirements"]["implemented_verified"] == 0
    assert by_id(rep)["R2"]["reasons"] == ["no verified flow"]


def test_a_machine_without_spec_object_matches_by_table_and_column(world) -> None:
    app, make = world
    doc = observed(WF)
    for m in doc["machines"]:
        m.pop("spec_object", None)
    rep = build_report(FS.SPEC, FS.trace(), load_inventory(make(doc), app))
    el = {e["key"]: e for e in by_id(rep)["R3"]["elements"]}["impl:machine:projects.status"]
    assert el["present"] and el["verified"]


def test_a_workflows_file_of_another_schema_is_bad_workflows(world) -> None:
    app, make = world
    inv_dir = make()
    doc = copy.deepcopy(WF)
    doc["schema"] = "lattice.workflows/2"
    raw = json.dumps(doc).encode()
    (inv_dir / "workflows.json").write_bytes(raw)
    meta = json.loads((inv_dir / "inventory.json").read_text())
    meta["workflows_sha256"] = sha256_bytes(raw)
    (inv_dir / "inventory.json").write_text(canonical_json(meta))
    with pytest.raises(ImplementationError) as e:
        load_inventory(inv_dir, app)
    assert e.value.code == "bad_workflows"


# ---- the report (D3-D6) -------------------------------------------------------------------


def test_static_report_statuses_on_the_fixture(world) -> None:
    rep = report_of(world)
    r = by_id(rep)
    assert {k: v["status"] for k, v in r.items()} == {
        "R1": "implemented_unverified",
        "R2": "implemented_unverified",
        "R3": "implemented_unverified",
        "R4": "required_not_implemented",
        "R5": "required_not_implemented",
        "R6": "no_element",
        "R7": "implemented_unverified",
        "R8": "implemented_unverified",
        "R9": "implemented_unverified",
        "R10": "implemented_unverified",
        "R11": "implemented_unverified",
        "R12": "implemented_unverified",
    }
    assert r["R2"]["reasons"] == ["static run"]
    assert r["R7"]["reasons"] == ["static schema"] == r["R8"]["reasons"]
    assert r["R4"]["missing"] == ["impl:route:GET /api/tickets/export"]
    assert r["R4"]["partial"] is False and r["R4"]["impact_label"] == "not_built_yet"
    assert r["R5"]["missing"] == ["impl:route:POST /api/projects/{}/restore"]
    assert r["R5"]["partial"] is True  # its setup step exists
    assert r["R6"]["spec_status"] == "context"
    s = rep["summary"]["requirements"]
    assert s == {
        "implemented_verified": 0,
        "implemented_unverified": 9,
        "required_not_implemented": 2,
        "no_element": 1,
    }


def test_absence_is_inferred_and_presence_is_static_without_a_replay(world) -> None:
    r = by_id(report_of(world))
    for el in r["R5"]["elements"]:
        assert el["basis"] == ("inferred" if not el["present"] else "static")
    assert all(el["basis"] == "static" for el in r["R2"]["elements"])


def test_every_element_has_evidence_when_present(world) -> None:
    for e in report_of(world)["rules"]:
        for el in e["elements"]:
            if el["present"]:
                assert el["evidence"], el
                assert all(re.fullmatch(r"code:[^:]+:\d+", x) for x in el["evidence"])


def test_links_record_how_they_were_made_and_the_focus_step(world) -> None:
    r = by_id(report_of(world))
    els = {e["key"]: e for e in r["R5"]["elements"]}
    assert els["impl:route:POST /api/projects"]["via"] == ["scenario"]
    assert els["impl:route:POST /api/projects/{}/restore"].get("focus") is True
    assert "focus" not in els["impl:route:POST /api/projects"]
    assert by_id(report_of(world))["R4"]["elements"][0]["via"] == ["text"]
    r12 = {e["key"]: e for e in r["R12"]["elements"]}
    assert r12["impl:table:projects"]["via"] == ["invariant"]
    assert r["R7"]["elements"][1]["via"] == ["profile"]


def test_an_unparsable_invariant_is_listed_and_links_nothing(world) -> None:
    r = by_id(report_of(world))
    assert r["R12"]["unparsed_links"] == ["invariant:garbled"]
    assert [e["key"] for e in r["R12"]["elements"]] == ["impl:table:projects"]


def test_text_route_ignores_sentence_punctuation(world) -> None:
    r = by_id(report_of(world))
    assert [e["key"] for e in r["R1"]["elements"]] == ["impl:route:POST /api/login"]
    assert impl.routes_in_text("see GET /a/b. Then POST /c: and PUT /d!") == [
        "GET /a/b",
        "POST /c",
        "PUT /d",
    ]


def test_conditional_routes_link_no_rule_through_a_scenario(world) -> None:
    rep = report_of(world)
    r = by_id(rep)
    assert [e["key"] for e in r["R11"]["elements"]] == ["impl:route:POST /api/projects"]
    assert "impl:route:PUT /test/clock" not in rep["by_element"]
    ex = [x for x in rep["excluded"] if x["kind"] == "route"]
    assert [(x["key"], x["reason"]) for x in ex] == [("PUT /test/clock", "test_only")]
    ep = {e["key"]: e for e in rep["endpoints"]}
    assert ep["PUT /test/clock"]["status"] == "test_only" and ep["PUT /test/clock"]["in_spec"]


def test_orphans_are_the_routes_no_rule_lists(world) -> None:
    rep = report_of(world)
    orphans = sorted(x["key"] for x in rep["implemented_not_required"] if x["kind"] == "route")
    assert orphans == sorted(
        [
            "GET /api/projects/{}",
            "GET /api/stats",
            "GET /api/tickets",
            "GET /api/tickets/{}",
            "POST /api/batch",
            "POST /api/jobs/{}/runs",
            "POST /api/projects/{}/archive",
            "POST /api/tickets",
            "DELETE /api/tickets/{}",
        ]
    )
    stats = {r["key"]: r for r in rep["routes"]}["GET /api/stats"]
    assert stats["in_spec"] is True and stats["rules"] == []  # a scenario names it, no rule does
    assert {r["key"]: r for r in rep["routes"]}["GET /api/tickets"]["in_spec"] is False


def test_a_linked_plumbing_route_stays_required_an_unlinked_one_is_excluded(world) -> None:
    rep = report_of(world)
    assert {r["key"]: r for r in rep["routes"]}["GET /health"]["status"] == "implemented_unverified"
    assert not any(x["key"] == "GET /health" for x in rep["excluded"])
    tr = FS.trace()
    tr.links.pop("R9")
    tr.rules = [
        Rule(r.id, "The service is up." if r.id == "R9" else r.text, r.section, r.line)
        for r in tr.rules
    ]
    rep2 = build_report(FS.SPEC, tr, load_inventory(world[1](), world[0]))
    gone = [x for x in rep2["excluded"] if x["kind"] == "route"]
    assert sorted((x["key"], x["reason"]) for x in gone) == [
        ("GET /health", "plumbing"),
        ("PUT /test/clock", "test_only"),
    ]
    assert not any(x["key"] == "GET /health" for x in rep2["implemented_not_required"])


def test_framework_routes_are_excluded_with_a_reason_and_are_not_elements(world) -> None:
    ex = copy.deepcopy(EXTRACT)
    ex["excluded"] = [
        {"file": "main.go", "kind": "framework", "line": 12, "method": "ANY", "path": "*",
         "reason": "gin NoRoute handler", "what": "route"}
    ]  # fmt: skip
    rep = report_of(world, extract=ex)
    fw = [x for x in rep["excluded"] if x["kind"] == "framework"]
    assert [x["reason"] for x in fw] == ["gin NoRoute handler"]
    assert not any(r["key"].startswith("ANY") for r in rep["routes"])


def test_head_and_options_are_ignored(world) -> None:
    ex = copy.deepcopy(EXTRACT)
    base = ex["routes"][0]
    for m in ("HEAD", "OPTIONS"):
        ex["routes"].append({**base, "method": m, "path": "/api/ghost"})
    rep = report_of(world, extract=ex)
    assert not any("ghost" in r["key"] for r in rep["routes"])


def test_an_any_route_matches_every_method_on_its_path(world) -> None:
    ex = copy.deepcopy(EXTRACT)
    ex["routes"] = [
        r for r in ex["routes"] if not (r["method"] == "POST" and r["path"] == "/api/login")
    ]
    ex["routes"].append({**EXTRACT["routes"][0], "method": "ANY", "path": "/api/login"})
    rep = report_of(world, extract=ex)
    assert by_id(rep)["R1"]["status"] == "implemented_unverified"
    row = {r["key"]: r for r in rep["routes"]}["ANY /api/login"]
    assert row["via_any"] is True and row["rules"] == ["R1"]


def test_two_routes_with_one_key_are_both_kept_and_flagged(world) -> None:
    ex = copy.deepcopy(EXTRACT)
    dup = copy.deepcopy([r for r in ex["routes"] if r["path"] == "/api/projects/:id"][0])
    dup["path"] = "/api/projects/:pid"
    ex["routes"].append(dup)
    rep = report_of(world, extract=ex)
    rows = [r for r in rep["routes"] if r["key"] == "GET /api/projects/{}"]
    assert len(rows) == 2 and all(r["collision"] for r in rows)


def test_a_missing_table_or_column_makes_a_rule_required_not_implemented(world) -> None:
    ex = copy.deepcopy(EXTRACT)
    ex["tables"] = [t for t in ex["tables"] if t["table"] != "tickets"]
    rep = report_of(world, extract=ex)
    r = by_id(rep)
    assert r["R7"]["status"] == "required_not_implemented"
    assert "impl:table:tickets" in r["R7"]["missing"]
    assert r["R8"]["status"] == "required_not_implemented"
    ex2 = copy.deepcopy(EXTRACT)
    for t in ex2["tables"]:
        if t["table"] == "tickets":
            t["columns"] = [c for c in t["columns"] if c["name"] != "project_id"]
    r2 = by_id(report_of(world, extract=ex2))
    assert r2["R7"]["missing"] == ["impl:table:tickets.project_id"] and r2["R7"]["partial"] is True
    assert r2["R8"]["status"] == "implemented_unverified"


def test_a_machine_absent_from_workflows_and_writes_is_required_not_implemented(world) -> None:
    doc = copy.deepcopy(WF)
    doc["machines"] = [m for m in doc["machines"] if m["table"] != "jobs"]
    # still written in the extract: the (table, column) write is the static fallback
    rep = report_of(world, doc)
    assert by_id(rep)["R10"]["status"] == "implemented_unverified"
    ex = copy.deepcopy(EXTRACT)
    ex["status_writes"] = [w for w in ex["status_writes"] if w["table"] != "jobs"]
    rep = report_of(world, doc, extract=ex)
    assert by_id(rep)["R10"]["status"] == "required_not_implemented"
    assert by_id(rep)["R10"]["missing"] == ["impl:machine:jobs.status"]


# ---- verification (D5) --------------------------------------------------------------------


def test_a_verified_flow_through_every_route_makes_a_rule_verified(world) -> None:
    rep = report_of(world, observed(WF))
    r = by_id(rep)
    assert r["R2"]["status"] == "implemented_verified"
    assert r["R3"]["status"] == "implemented_verified"  # its routes and its confirmed machine
    assert all(el["basis"] == "observed" for el in r["R2"]["elements"])
    # tables are static schema facts: a table-only rule is never verified
    for rid in ("R7", "R8", "R12"):
        assert r[rid]["status"] == "implemented_unverified"
    assert r["R4"]["status"] == "required_not_implemented"
    assert rep["inventory"]["run_mode"] == "observed"


def test_a_machine_that_is_only_static_keeps_the_rule_unverified(world) -> None:
    r = by_id(report_of(world, observed(WF, machines="static")))
    assert r["R3"]["status"] == "implemented_unverified"
    assert r["R3"]["reasons"] == ["machine static"]
    assert r["R2"]["status"] == "implemented_verified"


def test_failed_flows_leave_the_route_unverified_with_that_reason(world) -> None:
    doc = observed(WF, verify=lambda f: False)
    r = by_id(report_of(world, doc))
    assert r["R2"]["status"] == "implemented_unverified"
    assert r["R2"]["reasons"] == ["flow failed"]


def test_a_route_with_no_flow_is_unverified_with_no_verified_flow(world) -> None:
    doc = observed(WF)
    doc["flows"] = [f for f in doc["flows"] if "POST /api/login" not in f["skeleton"]]
    r = by_id(report_of(world, doc))
    assert r["R1"]["status"] == "implemented_unverified"
    assert r["R1"]["reasons"] == ["no verified flow"]


def test_no_workflows_at_all_leaves_everything_unverified(world) -> None:
    rep = report_of(world, None)
    assert rep["inventory"]["workflows_sha256"] is None and rep["flows"] == []
    assert by_id(rep)["R2"]["reasons"] == ["no verified flow"]
    assert rep["summary"]["requirements"]["implemented_verified"] == 0


def test_flows_list_their_rules_orphans_and_spec_matches(world) -> None:
    rep = report_of(world, observed(WF))
    flows = {f["id"]: f for f in rep["flows"]}
    pub = next(
        f
        for f in flows.values()
        if f["routes"] == ["POST /api/projects", "POST /api/projects/{}/publish"]
        and f["kind"] == "pair"
    )
    assert pub["effective"] == "verified" and pub["basis"] == "observed"
    assert pub["rules"] == ["R2", "R3", "R5", "R11"] and pub["orphan_routes"] == []
    assert pub["inferred_name"] is True
    assert (
        pub["spec_matches"]["strict"]
        and rep["spec_skeletons"][pub["spec_matches"]["strict"][0]] == pub["routes"]
    )
    orphan_flow = next(f for f in flows.values() if "GET /api/stats" in f["routes"])
    assert (
        orphan_flow["implemented_not_required"] and "GET /api/stats" in orphan_flow["orphan_routes"]
    )
    assert all(f["effective"] == "verified" for f in flows.values())


def test_a_machine_no_rule_links_is_implemented_not_required(world) -> None:
    tr = FS.trace()
    tr.links["R10"] = ["scenario:activate_job"]  # drop the machine link
    rep = report_of(world, observed(WF), trace=tr)
    jobs = next(m for m in rep["machines"] if m["key"] == "impl:machine:jobs.status")
    assert jobs["implemented_not_required"] is True and jobs["rules"] == []
    projects = next(m for m in rep["machines"] if m["key"] == "impl:machine:projects.status")
    assert projects["rules"] == ["R3"] and not projects["implemented_not_required"]


def test_last_check_is_shown_beside_but_never_merged_into_verified(world) -> None:
    app, make = world
    inv = load_inventory(make(), app)
    rep = build_report(
        FS.SPEC, FS.trace(), inv, last_check={"targets": {"rule:R2": "pass", "rule:R3": "fail"}}
    )
    r = by_id(rep)
    assert (r["R2"]["last_check"], r["R3"]["last_check"], r["R1"]["last_check"]) == (
        "pass",
        "fail",
        None,
    )
    assert r["R2"]["status"] == "implemented_unverified"
    assert "last_check" not in by_id(build_report(FS.SPEC, FS.trace(), inv))["R2"]


# ---- no trace: the spec's cells and scenarios ---------------------------------------------


def test_without_a_trace_the_requirements_are_cells_and_scenarios(world) -> None:
    rep = report_of(world, trace=None)
    assert rep["requirement_basis"] == "spec" and "rules" not in rep
    ids = [e["id"] for e in rep["requirements"]]
    assert any(i.startswith("cell:") for i in ids) and "scenario:restore_project" in ids
    r = by_id(rep, "requirements")
    assert r["scenario:restore_project"]["status"] == "required_not_implemented"
    assert r["scenario:create_project"]["status"] == "implemented_unverified"
    assert any(k.startswith("impl:route:") for k in rep["by_element"])
    assert {e["key"]: e for e in rep["endpoints"]}["POST /api/projects/{}/restore"][
        "status"
    ] == "spec_only"


# ---- the report as data (D7) --------------------------------------------------------------


def test_report_is_deterministic_sorted_and_relative(world, tmp_path: Path) -> None:
    app, make = world
    a = report_json(build_report(FS.SPEC, FS.trace(), load_inventory(make(observed(WF)), app)))
    b = report_json(build_report(FS.SPEC, FS.trace(), load_inventory(make(observed(WF)), app)))
    assert a == b
    other = tmp_path / "x" / "app"
    shutil.copytree(app, other)
    c = report_json(build_report(FS.SPEC, FS.trace(), load_inventory(make(observed(WF)), other)))
    assert a == c
    assert "/Users" not in a and "/private" not in a and str(tmp_path) not in a
    assert not re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", a)
    assert json.loads(a)["schema"] == "lattice.implementation-report/1"
    rep = json.loads(a)
    assert [r["key"] for r in rep["routes"]] == sorted(
        (r["key"] for r in rep["routes"]), key=lambda k: (k.split(" ", 1)[1], k.split(" ", 1)[0])
    )
    assert list(rep["by_element"]) == sorted(rep["by_element"])


def test_render_prints_one_evidence_line_per_element(world) -> None:
    app, make = world
    rep = build_report(FS.SPEC, FS.trace(), load_inventory(make(), app))
    text = impl.render(rep)
    assert "R4 [unchecked] required_not_implemented missing route:GET /api/tickets/export" in text
    assert "route:POST /api/projects present static code:api/api.go:18" in text
    assert "excluded:" in text and "PUT /test/clock: test_only" in text
    assert "R4 [" not in impl.render(rep, rule="R2")


def test_building_a_report_writes_nothing(world, tmp_path: Path) -> None:
    app, make = world
    inv_dir = make(observed(WF))
    before = (tree(app), tree(inv_dir), tree(tmp_path))
    for _ in range(3):
        build_report(FS.SPEC, FS.trace(), load_inventory(inv_dir, app))
    assert (tree(app), tree(inv_dir), tree(tmp_path)) == before


# ---- the CLI ------------------------------------------------------------------------------


def _spec_ref(tmp_path: Path) -> str:
    mod = tmp_path / "specmod"
    mod.mkdir(exist_ok=True)
    shutil.copy(FIX / "fixture_spec.py", mod / "impl_cli_spec.py")
    sys.path.insert(0, str(mod))
    return "impl_cli_spec:SPEC"


def test_cli_json_text_and_filters(
    world, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app, make = world
    inv_dir = make()
    trace = tmp_path / "t.json"
    trace.write_text(FS.trace().to_json())
    ref = _spec_ref(tmp_path)
    before = (tree(app), tree(inv_dir))
    argv = [
        "--spec",
        ref,
        "--trace",
        str(trace),
        "--inventory",
        str(inv_dir),
        "--app-root",
        str(app),
    ]
    assert impl.main([*argv, "--json"]) == 0
    full = json.loads(capsys.readouterr().out)
    assert full["schema"] == "lattice.implementation-report/1"
    assert impl.main([*argv, "--json", "--status", "required_not_implemented"]) == 0
    assert [e["id"] for e in json.loads(capsys.readouterr().out)["rules"]] == ["R4", "R5"]
    assert impl.main([*argv, "--rule", "R5"]) == 0
    out = capsys.readouterr().out
    assert "R5 [checked] required_not_implemented" in out and "R4 [" not in out
    assert impl.main([*argv]) == 0
    assert "implementation report (rules basis)" in capsys.readouterr().out
    assert (tree(app), tree(inv_dir)) == before


def test_cli_exit_2_names_the_code(
    world, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app, _ = world
    ref = _spec_ref(tmp_path)
    assert (
        impl.main(["--spec", ref, "--inventory", str(tmp_path / "none"), "--app-root", str(app)])
        == 2
    )
    assert capsys.readouterr().err.startswith("error: inventory_missing:")
    assert impl.main(["--spec", "no_such_mod:SPEC", "--inventory", str(tmp_path)]) == 2
    assert impl.main([]) == 2


def test_python_m_lattice_spec_dispatches_implementation(world, tmp_path: Path) -> None:
    import subprocess

    app, make = world
    inv_dir = make()
    ref = _spec_ref(tmp_path)
    env = {"PYTHONPATH": f"{ROOT}:{tmp_path / 'specmod'}", "PATH": "/usr/bin:/bin"}
    cmd = [sys.executable, "-m", "lattice.spec", "implementation", "--spec", ref]
    cmd += ["--inventory", str(inv_dir), "--app-root", str(app), "--json"]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["requirement_basis"] == "spec"


# ---- the config and the schema contract ---------------------------------------------------


def test_implementation_config_resolves_against_the_project(tmp_path: Path) -> None:
    assert impl.implementation_config(tmp_path) is None
    (tmp_path / "studio.toml").write_text('[app]\napp = "h:a"\n')
    assert impl.implementation_config(tmp_path) is None
    (tmp_path / "studio.toml").write_text('[implementation]\ninventory = "../inv"\n')
    cfg = impl.implementation_config(tmp_path)
    assert cfg["inventory"] == tmp_path / "../inv" and cfg["app_root"] == tmp_path / "."
    assert cfg["package"] is None
    (tmp_path / "studio.toml").write_text(
        '[implementation]\ninventory = "/abs/inv"\napp_root = "src"\npackage = "go"\n'
    )
    cfg = impl.implementation_config(tmp_path)
    assert cfg["inventory"] == Path("/abs/inv") and cfg["app_root"] == tmp_path / "src"
    assert cfg["package"] == "go"


D9_TOP = {"app", "capped", "edges", "flows", "machines", "run", "schema", "summary", "unread"}
D9_FLOW = {"id", "name", "inferred_name", "kind", "skeleton", "routes", "focus_from", "status",
           "source", "reason", "machine_refs", "edge_refs", "steps"}  # fmt: skip
D9_STEP = {"i", "kind", "method", "route", "path", "caller", "role", "json", "expect_status",
           "expect_from", "code_statuses", "bindings", "value_sources", "inserted", "attempts",
           "refusal", "evidence"}  # fmt: skip
D9_MACHINE = {"id", "table", "column", "states", "initial", "edges", "status", "spec_object",
              "sweeps", "unread"}  # fmt: skip


def _conforms(doc: dict[str, Any]) -> None:
    assert doc["schema"] == "lattice.workflows/1"
    assert D9_TOP <= set(doc)
    assert {"module", "framework", "extract_sha256", "workflow_extract_sha256"} <= set(doc["app"])
    assert {"mode", "harness", "users", "services_swept", "caps"} <= set(doc["run"])
    assert doc["run"]["mode"] in ("observed", "static")
    for f in doc["flows"]:
        assert D9_FLOW <= set(f)
        for s in f["steps"]:
            if s["kind"] == "request":
                assert D9_STEP <= set(s)
                assert {"what", "at"} <= set(s["evidence"][0]) if s["evidence"] else True
    for m in doc["machines"]:
        assert D9_MACHINE <= set(m)
        assert all({"from", "to", "source", "evidence"} <= set(e) for e in m["edges"])


def test_the_fixture_workflows_conform_to_the_workflows_schema() -> None:
    _conforms(WF)


@pytest.mark.skipif(not REAL_WF.is_file(), reason="Spike B's committed run is not in this tree")
def test_spike_bs_real_workflows_conform_and_read_through_the_adapter(tmp_path: Path) -> None:
    real = json.loads(REAL_WF.read_text())
    _conforms(real)
    assert set(real) >= set(WF)  # the fixture is the same shape as the real thing
    inv = impl.Inventory(
        directory=tmp_path,
        app_root=tmp_path,
        package=".",
        meta={"extract_sha256": "x"},
        extract={"module": real["app"]["module"], "routes": []},
        workflows=None,
        stale=None,
        source_paths=frozenset(),
    )
    out, mismatch, how, _ = impl.read_workflows(real, inv)
    assert how == "route-set" and "not in the extract" in (mismatch or "")  # no routes given
    assert len(out["flows"]) == len(real["flows"]) and out["mode"] == real["run"]["mode"]
