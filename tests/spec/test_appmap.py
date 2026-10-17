"""lattice.spec.appmap (Spike F, docs/results/2026-10-16-goto-place-prereg.md, D1-D2): the app map
is a pure join of Spike C's report and the review data. Every value is copied from the report that
holds it; a join is an exact-key lookup on the template form; the map never decides agreement; the
states other than ``ok`` carry only ``schema``, ``state``, ``inputs`` and ``why``; reading writes
nothing. The app is the fixture of tests/spec/fixtures/implementation/ (and the committed mock
project lattice/studio/mock/appmap/ for the CLI)."""

from __future__ import annotations

import copy
import dataclasses
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice.importer.inventory import capture
from lattice.spec import appmap as am
from lattice.spec import implementation as impl
from lattice.spec.implementation import load_inventory, sha256_bytes
from lattice.spec.model import Scenario, Step
from lattice.spec.review import _build

ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).resolve().parent / "fixtures" / "implementation"
MOCK = ROOT / "lattice" / "studio" / "mock" / "appmap"
EXTRACT = json.loads((FIX / "extract.json").read_text())
WF = json.loads((FIX / "workflows.json").read_text())


def _fixture_spec():
    sp = importlib.util.spec_from_file_location("appmap_fixture_spec", FIX / "fixture_spec.py")
    mod = importlib.util.module_from_spec(sp)
    sys.modules["appmap_fixture_spec"] = mod
    sp.loader.exec_module(mod)
    return mod


FS = _fixture_spec()


def observed(doc: dict[str, Any], *, verify=lambda f: True) -> dict[str, Any]:
    d = copy.deepcopy(doc)
    d["run"]["mode"] = "observed"
    for f in d["flows"]:
        f["status"] = "verified" if verify(f) else "failed"
    for m in d["machines"]:
        m["status"] = "confirmed"
    return d


def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): "dir" if p.is_dir() else sha256_bytes(p.read_bytes())
        for p in sorted(root.rglob("*"))
    }


@pytest.fixture
def world(tmp_path: Path):
    app = tmp_path / "app"
    shutil.copytree(FIX / "app", app)

    def make(workflows: Any = WF, name: str = "inv") -> Path:
        out = tmp_path / name
        wf = None
        if workflows is not None:
            wf = tmp_path / f"{name}.wf.json"
            wf.write_text(json.dumps(workflows))
        capture(app, out, extract=EXTRACT, workflows=wf)
        return out

    return app, make


def build(world, workflows: Any = WF, spec: Any = None, **kw: Any) -> am.AppMap:
    app, make = world
    inv = load_inventory(make(workflows), app)
    return am.build(spec or FS.SPEC, FS.trace(), inv, **kw)


def cell(doc: dict[str, Any], row: str, col: str) -> dict[str, Any]:
    return next(c for c in doc["access"]["cells"] if c["row"] == row and c["col"] == col)


# ---- the shape --------------------------------------------------------------------------------


def test_the_map_has_exactly_the_documented_keys(world) -> None:
    doc = build(world).doc
    assert set(doc) == {
        "schema", "state", "inputs", "summary", "rules", "workflows", "machines", "access",
        "endpoints", "excluded", "unread",
    }  # fmt: skip
    assert doc["schema"] == "lattice.appmap/1" and doc["state"] == "ok"
    assert set(doc["inputs"]) == {
        "spec_sha256", "trace_sha256", "requirements_sha256", "inventory", "last_check",
    }  # fmt: skip
    assert set(doc["inputs"]["inventory"]) == {
        "extract_sha256", "workflows_sha256", "run_mode", "stale",
    }  # fmt: skip
    assert set(doc["access"]) == {"groups", "cells"}
    r = doc["rules"][0]
    assert set(r) == {
        "id", "text", "spec_status", "impl_status", "reasons", "missing", "impact_label",
        "basis", "elements", "evidence", "workflows", "machines", "cells", "last_check",
    }  # fmt: skip
    assert set(doc["access"]["cells"][0]) == {
        "row", "col", "spec", "route", "auth", "refusals", "route_status", "observed", "evidence",
    }  # fmt: skip


def test_values_are_copied_from_the_report_and_the_review_data(world) -> None:
    built = build(world)
    doc, rep = built.doc, built.report
    review = _build(FS.SPEC, FS.trace(), None, None, "x")
    rows = {r.id: r for r in review.rule_rows}
    by = {e["id"]: e for e in rep["rules"]}
    assert [r["id"] for r in doc["rules"]] == [e["id"] for e in rep["rules"]]
    for r in doc["rules"]:
        e = by[r["id"]]
        assert (r["impl_status"], r["spec_status"]) == (e["status"], e["spec_status"])
        assert r["text"] == rows[r["id"]].text and r["spec_status"] == rows[r["id"]].status
        assert r["evidence"] == sorted({x for el in e["elements"] for x in el["evidence"]})
        assert [el["key"] for el in r["elements"]] == [el["key"] for el in e["elements"]]
        assert r["reasons"] == (e.get("reasons") or []) and r["missing"] == (e.get("missing") or [])
    assert doc["endpoints"] == rep["endpoints"] and doc["excluded"] == rep["excluded"]
    flows = {f["id"]: f for f in doc["workflows"]}
    for f in rep["flows"]:
        assert {k: v for k, v in flows[f["id"]].items() if k != "steps"} == f
    for g, src in zip(doc["access"]["groups"], review.access_groups, strict=True):
        assert g == {"prefix": src.prefix, "rows": list(src.rows), "columns": list(src.columns)}
    for c in doc["access"]["cells"]:
        src = next(g for g in review.access_groups if (c["row"], c["col"]) in g.cells)
        assert c["spec"] == src.cells[c["row"], c["col"]]


def test_basis_words_are_fixed(world) -> None:
    doc = build(world).doc
    basis = {r["id"]: r["basis"] for r in doc["rules"]}
    status = {r["id"]: r["impl_status"] for r in doc["rules"]}
    want = {
        "implemented_verified": "observed", "implemented_unverified": "static",
        "required_not_implemented": "inferred", "no_element": "none",
    }  # fmt: skip
    assert {k: want[v] for k, v in status.items()} == basis
    assert {"static", "inferred", "none"} <= set(basis.values())
    assert "observed" not in basis.values()  # a static capture verifies nothing
    assert doc["summary"]["rules"]["basis"] == {"inferred": 2, "none": 1, "static": 9}


def test_a_static_capture_says_so_and_has_no_observed_cell(world) -> None:
    doc = build(world).doc
    assert [n["code"] for n in doc["summary"]["notes"]] == ["static_capture"]
    assert all(c["observed"] == [] for c in doc["access"]["cells"])
    assert doc["inputs"]["inventory"]["run_mode"] == "static"


def test_an_inventory_without_workflows_has_the_note_and_no_flows_or_machines(world) -> None:
    doc = build(world, workflows=None).doc
    assert [n["code"] for n in doc["summary"]["notes"]] == ["no_workflows_capture"]
    assert "machines and flows come from a workflows capture" in doc["summary"]["notes"][0]["text"]
    assert doc["workflows"] == [] and doc["machines"] == []
    assert doc["inputs"]["inventory"]["workflows_sha256"] is None


def test_unread_items_are_listed_with_their_code_location(world) -> None:
    app, make = world
    ext = copy.deepcopy(EXTRACT)
    ext["unread"] = [
        {"file": "api/api.go", "line": 7, "what": "call", "reason": "interface dispatch"},
        {"file": "main.go", "line": 3, "what": "bound", "reason": "not a constant"},
    ]
    out = app.parent / "inv-unread"
    capture(app, out, extract=ext, workflows=None)
    doc = am.build(FS.SPEC, FS.trace(), load_inventory(out, app)).doc
    assert doc["summary"]["unread"] == 2
    assert [(u["file"], u["line"], u["at"]) for u in doc["unread"]] == [
        ("api/api.go", 7, "code:api/api.go:7"),
        ("main.go", 3, "code:main.go:3"),
    ]
    assert build(world).doc["unread"] == [] and build(world).doc["summary"]["unread"] == 0


def test_auth_and_refusals_are_the_route_rows_not_a_guess(world) -> None:
    built = build(world)
    routes = {r["key"]: r for r in built.report["routes"]}
    seen = set()
    for c in built.doc["access"]["cells"]:
        if c["route"] is None:
            continue
        assert c["auth"] == routes[c["route"]]["auth"], c["col"]
        assert c["refusals"] == routes[c["route"]]["refusals"]
        seen.add(c["auth"])
    assert {"Public", "Customer"} <= seen
    # a route whose name says nothing about its auth: the cell still carries the row's
    assert (
        cell(built.doc, "no token", "POST /api/login")["auth"] == routes["POST /api/login"]["auth"]
    )


# ---- machines ---------------------------------------------------------------------------------


def test_machine_edges_are_set_membership_only(world) -> None:
    doc = build(world).doc
    m = {x["key"]: x for x in doc["machines"]}
    jobs = m["impl:machine:jobs.status"]
    assert jobs["spec_target"] == "machine:Job.status"
    got = {(e["from"], e["to"]): (e["in_spec"], e["in_code"]) for e in jobs["edges_detail"]}
    assert got == {("queued", "active"): (True, True), ("active", "done"): (True, False)}
    assert {k: v for k, v in jobs.items() if k not in ("spec_target", "edges_detail")} == next(
        r for r in build(world).report["machines"] if r["key"] == jobs["key"]
    )


# ---- access: the join -------------------------------------------------------------------------


def test_a_cell_shows_spec_code_and_route_status_side_by_side(world) -> None:
    doc = build(world).doc
    c = cell(doc, "ann", "POST /api/projects")
    assert c["spec"] == "201" and c["route"] == "POST /api/projects"
    assert c["auth"] == "Customer" and c["route_status"] == "implemented"
    assert c["evidence"] and all(e.startswith("code:") for e in c["evidence"])
    restore = cell(doc, "ann", "POST /api/projects/{}/restore")
    assert restore["spec"] == "200" and restore["route"] is None
    assert restore["auth"] is None and restore["refusals"] is None
    assert restore["route_status"] == "spec_only"
    anon = cell(doc, "no token", "POST /api/login")
    assert anon["spec"] == "200"
    assert cell(doc, "ben", "POST /api/login")["spec"] == "·"  # unspecified is kept as the dot


def test_the_map_never_compares_a_cell_with_the_code(world) -> None:
    for c in build(world).doc["access"]["cells"]:
        assert not (set(c) & {"agree", "agrees", "match", "matches", "verdict", "ok"})


def test_observed_answers_join_on_the_verified_flows_caller_and_route(world) -> None:
    doc = build(world, observed(WF)).doc
    seen = {(c["row"], c["col"]): c["observed"] for c in doc["access"]["cells"] if c["observed"]}
    assert seen
    raw = observed(WF)
    want: dict[tuple[str, str], int] = {}
    for f in raw["flows"]:
        for s in f["steps"]:
            if s.get("kind", "request") == "await":
                continue
            m, _, p = (s.get("route") or "").partition(" ")
            key = (str(s.get("caller") or "no token"), f"{m} {impl.template_path(p)}")
            want[key] = want.get(key, 0) + 1
    got_rows = {(r, impl.route_key(*c.partition(" ")[::2])): len(v) for (r, c), v in seen.items()}
    for k, n in got_rows.items():
        assert want[k] == n, k
    first = doc["workflows"][0]
    assert first["steps"] and set(first["steps"][0]) == {
        "i", "method", "route", "caller", "expect_status", "evidence",
    }  # fmt: skip


def test_a_failed_flow_is_not_observed(world) -> None:
    all_ok = build(world, observed(WF)).doc
    none_ok = build(world, observed(WF, verify=lambda f: False)).doc
    assert any(c["observed"] for c in all_ok["access"]["cells"])
    assert not any(c["observed"] for c in none_ok["access"]["cells"])
    assert {f["effective"] for f in none_ok["workflows"]} == {"failed"}


def test_a_step_without_a_caller_joins_the_no_token_row(world) -> None:
    doc = observed(WF)
    for f in doc["flows"]:
        for s in f["steps"]:
            if s.get("route") == "POST /api/projects/{id}/publish":
                s["caller"] = None
    out = build(world, doc).doc
    col = "POST /api/projects/{}/publish"
    assert cell(out, "no token", col)["observed"], "a null caller is the no-token row"
    assert not cell(out, "ann", col)["observed"]


def test_routes_join_on_the_template_form(world) -> None:
    step = Step("POST", "/api/projects/{project_id}/publish", "ann", expect_status=200)
    sc = dataclasses.replace(FS.SPEC.scenarios[0], name="publish_braces", steps=(step,))
    spec = dataclasses.replace(FS.SPEC, scenarios=(*FS.SPEC.scenarios, sc))
    doc = build(world, spec=spec).doc
    c = cell(doc, "ann", "POST /api/projects/{project_id}/publish")
    assert c["col"] == "POST /api/projects/{project_id}/publish"  # the spec's own spelling is kept
    assert c["route"] == "POST /api/projects/{}/publish"  # and joined on the template
    assert c["route_status"] == "implemented" and c["auth"] == "Customer"
    assert isinstance(sc, Scenario)


# ---- staleness, last check --------------------------------------------------------------------


def test_stale_downgrades_and_says_so(world) -> None:
    app, make = world
    inv_dir = make(observed(WF))
    fresh = am.build(FS.SPEC, FS.trace(), load_inventory(inv_dir, app)).doc
    assert fresh["summary"]["rules"]["impl_status"].get("implemented_verified", 0) > 0
    assert fresh["summary"]["notes"] == []
    (app / "api" / "api.go").write_text((app / "api" / "api.go").read_text() + "\n// edit\n")
    stale = am.build(FS.SPEC, FS.trace(), load_inventory(inv_dir, app)).doc
    assert stale["inputs"]["inventory"]["stale"]["counts"] == {
        "added": 0,
        "changed": 1,
        "removed": 0,
    }
    assert "implemented_verified" not in stale["summary"]["rules"]["impl_status"]
    assert [n["code"] for n in stale["summary"]["notes"]] == ["stale"]
    assert not any(c["observed"] for c in stale["access"]["cells"])
    assert "observed" not in {r["basis"] for r in stale["rules"]}


def test_last_check_marks_rules_and_is_fingerprinted(world) -> None:
    last = {
        "verdict": "fail",
        "at": "2026-01-01T00:00:00Z",
        "targets": {"rule:R2": "fail", "rule:R1": "pass"},
    }
    doc = build(world, last_check=last).doc
    got = {r["id"]: r["last_check"] for r in doc["rules"]}
    assert got["R2"] == "fail" and got["R1"] == "pass" and got["R3"] is None
    assert doc["inputs"]["last_check"]["verdict"] == "fail"
    assert len(doc["inputs"]["last_check"]["sha256"]) == 64
    assert "2026-01-01" not in json.dumps(doc)  # no time in the map


# ---- the states other than ok -----------------------------------------------------------------


def test_the_non_ok_states_carry_only_schema_state_inputs_and_why() -> None:
    for state in ("not_configured", "inventory_missing", "bad_inventory", "no_spec"):
        doc = am.non_ok(state, "x: no inventory directory at /Users/someone/inv")
        assert set(doc) == {"schema", "state", "inputs", "why"} and doc["state"] == state
        assert "/Users" not in json.dumps(doc) and "<path>" in doc["why"]
    with pytest.raises(AssertionError):
        am.non_ok("ok", "x")


def test_the_project_states(tmp_path: Path) -> None:
    proj = tmp_path / "p"
    shutil.copytree(MOCK / "project", proj)
    shutil.copytree(MOCK / "app", tmp_path / "app")
    shutil.copytree(MOCK / "inventory", tmp_path / "inventory")
    assert am.appmap_from_dir(proj).state == "ok"
    (proj / "studio.toml").write_text("[impact]\n")
    assert am.appmap_from_dir(proj).state == "not_configured"
    (proj / "studio.toml").write_text(
        '[implementation]\ninventory = "../nope"\napp_root = "../app"\n'
    )
    miss = am.appmap_from_dir(proj)
    assert miss.state == "inventory_missing" and "/Users" not in json.dumps(miss.doc)
    (proj / "studio.toml").write_text(
        '[implementation]\ninventory = "../inventory"\napp_root = "../app"\n'
    )
    ex = tmp_path / "inventory" / "extract.json"
    ex.write_text(ex.read_text() + " ")
    assert am.appmap_from_dir(proj).state == "bad_inventory"
    (proj / "spec.py").unlink()
    assert am.appmap_from_dir(proj).state == "no_spec"
    assert am.appmap_from_dir(proj).doc["inputs"]["spec_sha256"] is None


# ---- elements and their neighbourhood ---------------------------------------------------------


def test_an_element_has_its_row_and_a_static_neighbourhood(world) -> None:
    built = build(world, observed(WF))
    keys = am.element_keys(built)
    kinds: dict[str, str] = {}
    for k in keys:
        e = am.element(built, k)
        kinds.setdefault(e["kind"], k)
        assert set(e) == {"schema", "state", "key", "kind", "row", "touches", "basis", "meaning"}
        assert e["basis"] == "static" and "not decided" in e["meaning"]
        assert set(e["touches"]) == {"rules", "workflows", "machines", "cells", "routes"}
        assert all(k2 in keys for vals in e["touches"].values() for k2 in vals), k
    assert set(kinds) == {"rule", "workflow", "machine", "cell", "route"}
    r3 = am.element(built, "rule:R3")
    assert r3["row"]["id"] == "R3"
    assert "impl:machine:projects.status" in r3["touches"]["machines"]
    assert any(
        k.startswith("impl:route:POST /api/projects/{}/publish") for k in r3["touches"]["routes"]
    )
    route = am.element(built, "impl:route:POST /api/projects")
    assert "rule:R2" in route["touches"]["rules"]
    assert "cell:ann|POST /api/projects" in route["touches"]["cells"]
    assert route["row"]["endpoint"]["status"] == "implemented" and route["row"]["route"]["auth"]


def test_a_workflow_touches_what_it_names(world) -> None:
    built = build(world, observed(WF))
    f = built.doc["workflows"][0]
    t = am.element(built, f["id"])["touches"]
    assert t["rules"] == sorted(f"rule:{r}" for r in f["rules"])
    assert t["routes"] == sorted(f"impl:route:{r}" for r in f["routes"])


def test_an_unknown_key_answers_five_nearest_keys(world) -> None:
    built = build(world)
    with pytest.raises(am.ElementError) as e:
        am.element(built, "rule:R99")
    assert e.value.code == "unknown_key" and len(e.value.nearest) == 5
    assert "rule:R9" in e.value.nearest
    assert all(k in am.element_keys(built) for k in e.value.nearest)
    for bad in ("", "x" * 301):
        with pytest.raises(am.ElementError) as e2:
            am.element(built, bad)
        assert e2.value.code == "bad_key"


def test_sections(world) -> None:
    doc = build(world).doc
    for s in am.SECTIONS:
        out = am.section_of(doc, s)
        assert {"schema", "state", "inputs", s} <= set(out)
    assert "excluded" in am.section_of(doc, "endpoints")
    assert am.section_of(doc, "summary")["summary"] == doc["summary"]
    with pytest.raises(ValueError):
        am.section_of(doc, "nope")
    assert am.refuse_section("nope")[1] == "bad_section" and am.refuse_section("rules") is None
    assert am.refuse_key("")[1] == "bad_key" and am.refuse_key("x") is None


def test_determinism_and_purity(world, monkeypatch) -> None:
    a = build(world, observed(WF)).doc
    b = build(world, observed(WF)).doc
    assert am.map_json(a) == am.map_json(b)
    text = am.map_json(a)
    assert not re.search(r"/Users|/private|/tmp|\d{4}-\d{2}-\d{2}T", text)

    def boom(*args: Any, **kw: Any) -> None:
        raise AssertionError("the map starts no process")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    assert am.map_json(build(world, observed(WF)).doc) == text


def test_the_modules_start_nothing_and_call_no_model() -> None:
    import ast

    banned_imports = {
        "subprocess",
        "socket",
        "urllib",
        "http",
        "anthropic",
        "openai",
        "shutil",
        "tempfile",
    }
    banned_calls = {
        "write_text",
        "write_bytes",
        "mkdir",
        "unlink",
        "rmdir",
        "system",
        "Popen",
        "touch",
    }
    for name in ("appmap.py", "impact_ask.py"):
        tree_ = ast.parse((ROOT / "lattice" / "spec" / name).read_text())
        for node in ast.walk(tree_):
            if isinstance(node, ast.Import):
                assert not {a.name.split(".")[0] for a in node.names} & banned_imports, name
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in banned_imports, name
                assert "state" not in (node.module or "").split(".")[-1:], name
            if isinstance(node, ast.Attribute):
                assert node.attr not in banned_calls, (name, node.attr)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
                assert len(node.args) < 2 and not node.keywords, (name, "open() with a mode")


# ---- the CLI ----------------------------------------------------------------------------------


def run_cli(
    *args: str, seed: str = "0", cwd: Path = MOCK / "project"
) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": seed,
           "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/nonexistent"}  # fmt: skip
    return subprocess.run(
        [sys.executable, "-m", "lattice.spec", "appmap", *args],
        capture_output=True, text=True, env=env, cwd=cwd, timeout=120, check=False,
    )  # fmt: skip


CLI = ("--spec", "spec:SPEC", "--trace", "spec.trace.json", "--inventory", "../inventory",
       "--app-root", "../app")  # fmt: skip


def test_the_cli_prints_the_map_and_writes_nothing() -> None:
    before = tree(MOCK)
    r = run_cli(*CLI, "--json")
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    assert doc["state"] == "ok" and doc == am.appmap_from_dir(MOCK / "project").doc
    assert tree(MOCK) == before
    assert not re.search(r"/Users|/private|/tmp", r.stdout)


def test_the_cli_is_identical_across_hash_seeds() -> None:
    outs = {run_cli(*CLI, "--json", seed=s).stdout for s in ("0", "1", "12345")}
    assert len(outs) == 1


def test_the_cli_section_text_and_exit_codes(tmp_path: Path) -> None:
    r = run_cli(*CLI, "--section", "summary")
    assert r.returncode == 0 and set(json.loads(r.stdout)) == {
        "schema",
        "state",
        "inputs",
        "summary",
    }
    text = run_cli(*CLI)
    assert text.returncode == 0 and text.stdout.startswith("app map (lattice.appmap/1)")
    assert "R4" in text.stdout and "required_not_implemented" in text.stdout
    bad = run_cli("--spec", "spec:SPEC", "--inventory", str(tmp_path / "none"))
    assert bad.returncode == 2 and "inventory_missing" in bad.stderr
    assert run_cli("--spec", "nonsense", "--inventory", "../inventory").returncode == 2


def test_the_cli_is_one_authoring_entry_and_help_is_unchanged() -> None:
    from lattice.spec.__main__ import AUTHORING

    assert AUTHORING["appmap"] == "lattice.spec.appmap"
    assert list(AUTHORING)[-4:] == ["implementation", "rationale", "glossary", "impact"]
