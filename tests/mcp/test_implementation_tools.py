"""The three implementation tools (Spike C, D10): implementation_get, workflow_get and
workflow_find. Listed only when studio.toml has [implementation] (also under --readonly),
read-only, each error starting with its code; sessions list them and carry one extra rule."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice.importer.inventory import capture
from lattice.mcp.server import INSTRUCTIONS
from lattice.mcp.tools import Toolbox, ToolError
from lattice.spec.implementation import sha256_bytes
from lattice.studio import session as sess
from lattice.studio.mcp_intake import parse_source

FIX = Path(__file__).resolve().parents[1] / "spec" / "fixtures" / "implementation"
EXTRACT = json.loads((FIX / "extract.json").read_text())
WF = json.loads((FIX / "workflows.json").read_text())

BASE_TOOLS = [
    "project_status",
    "model_get",
    "rule_get",
    "spec_ask",
    "spec_lint",
    "check_run",
    "code_links",
    "graph_get",
    "change_propose",
    "change_get",
    "change_list",
]
IMPL_TOOLS = ["implementation_get", "workflow_get", "workflow_find"]


class FakeBackend:
    """Enough of a backend for tools that read files only; records what change_propose sends."""

    kind = "fake"
    configured = False

    def __init__(self, root: Path) -> None:
        self.root = root
        self.new: list[dict[str, Any]] = []

    def model(self) -> dict[str, Any]:
        raise AssertionError("the implementation tools never call the model endpoint")

    def new_change(self, body: dict[str, Any]) -> dict[str, Any]:
        self.new.append(body)
        return {"id": "c1"}

    def get_change(self, cid: str) -> dict[str, Any]:
        return {"id": cid, "status": "drafting"}


def observed(doc: dict[str, Any]) -> dict[str, Any]:
    d = copy.deepcopy(doc)
    d["run"]["mode"] = "observed"
    for f in d["flows"]:
        f["status"] = "verified"
    for m in d["machines"]:
        m["status"] = "confirmed"
    return d


def make_project(tmp_path: Path, implementation: bool = True) -> tuple[Path, Path, Path]:
    """(project, app, inventory): the fixture spec as spec.py, its trace, an inventory."""
    import importlib.util

    sp = importlib.util.spec_from_file_location("tool_fixture_spec", FIX / "fixture_spec.py")
    mod = importlib.util.module_from_spec(sp)
    sys.modules["tool_fixture_spec"] = mod
    sp.loader.exec_module(mod)
    app = tmp_path / "app"
    shutil.copytree(FIX / "app", app)
    wf = tmp_path / "wf.json"
    wf.write_text(json.dumps(observed(WF)))
    inv = tmp_path / "inv"
    capture(app, inv, extract=EXTRACT, workflows=wf)
    proj = tmp_path / "proj"
    proj.mkdir()
    shutil.copy(FIX / "fixture_spec.py", proj / "spec.py")
    (proj / "spec.trace.json").write_text(mod.trace().to_json())
    (proj / "REQUIREMENTS.md").write_text("# Fixture\n")
    toml = f'[implementation]\ninventory = "{inv}"\napp_root = "{app}"\n' if implementation else ""
    (proj / "studio.toml").write_text(toml + '[llm]\nbackend = "stub"\n')
    return proj, app, inv


@pytest.fixture
def world(tmp_path: Path) -> tuple[Toolbox, Path, Path, Path]:
    proj, app, inv = make_project(tmp_path)
    return Toolbox(FakeBackend(proj)), proj, app, inv


def tree(*roots: Path) -> dict[str, str]:
    out = {}
    for root in roots:
        for p in sorted(root.rglob("*")):
            out[str(p)] = "dir" if p.is_dir() else sha256_bytes(p.read_bytes())
    return out


# ---- listing ------------------------------------------------------------------------------


def test_the_tools_exist_only_with_the_implementation_table(tmp_path: Path) -> None:
    proj, _, _ = make_project(tmp_path / "on")
    off, _, _ = make_project(tmp_path / "off", implementation=False)
    assert Toolbox(FakeBackend(off)).names() == BASE_TOOLS
    on = Toolbox(FakeBackend(proj))
    assert on.names() == BASE_TOOLS + IMPL_TOOLS  # right after change_list
    assert Toolbox(FakeBackend(off), readonly=True).names() == [
        n for n in BASE_TOOLS if n not in ("check_run", "change_propose")
    ]
    ro = Toolbox(FakeBackend(proj), readonly=True)
    assert [n for n in ro.names() if n in IMPL_TOOLS] == IMPL_TOOLS  # also under --readonly


def test_a_project_without_a_studio_toml_lists_the_base_tools(tmp_path: Path) -> None:
    (tmp_path / "p").mkdir()
    assert Toolbox(FakeBackend(tmp_path / "p")).names() == BASE_TOOLS


def test_the_tools_are_read_only_annotated(world) -> None:
    tb = world[0]
    for d in tb.list_tools():
        if d["name"] in IMPL_TOOLS:
            assert d["annotations"]["readOnlyHint"] is True
            assert d["inputSchema"]["additionalProperties"] is False
    assert all(not tb._tools[n]["writes"] for n in IMPL_TOOLS)


# ---- implementation_get -------------------------------------------------------------------


def test_the_summary_is_under_4k_and_carries_findings_with_proposals(world) -> None:
    tb = world[0]
    out = tb.call("implementation_get", {})
    assert len(json.dumps(out)) < 4000
    assert out["section"] == "summary" and out["requirement_basis"] == "rules"
    s = out["summary"]["requirements"]
    assert s["implemented_verified"] > 0 and s["required_not_implemented"] == 2
    assert out["findings_total"] == 2 + 9  # two unimplemented rules, nine orphan routes
    f = out["findings"][0]
    assert f["kind"] == "required_not_implemented" and f["target"] == "R4"
    p = f["proposal"]
    assert p["source"]["server"] == "extractor" and len(p["source"]["item"]) <= 200
    assert p["selected"] == ["rule:R4"] and "R4" in p["request"]
    assert parse_source(p["source"]) == p["source"]  # change_propose takes it as it is


def test_the_summary_is_under_4k_even_for_a_large_report(world, tmp_path: Path) -> None:
    tb, proj, app, inv = world
    ex = copy.deepcopy(EXTRACT)
    for i in range(120):  # many orphans
        ex["routes"].append(
            {**EXTRACT["routes"][0], "method": "GET", "path": f"/api/orphan{i}/:id"}
        )
    capture(app, inv, extract=ex)
    assert len(json.dumps(tb.call("implementation_get", {}))) < 4000


def test_sections_and_filters(world) -> None:
    tb = world[0]
    rules = tb.call("implementation_get", {"section": "rules"})
    assert rules["total"] == 12 and rules["returned"] == 12
    one = tb.call("implementation_get", {"section": "rules", "rule": "R5"})
    assert [r["id"] for r in one["items"]] == ["R5"]
    assert one["items"][0]["status"] == "required_not_implemented" and one["items"][0]["elements"]
    assert one["items"][0]["finding"]["proposal"]["selected"] == ["rule:R5"]
    miss = tb.call("implementation_get", {"section": "rules", "status": "required_not_implemented"})
    assert [r["id"] for r in miss["items"]] == ["R4", "R5"]
    el = tb.call(
        "implementation_get", {"section": "rules", "element": "impl:route:POST /api/projects"}
    )
    assert {r["id"] for r in el["items"]} == {"R2", "R3", "R5", "R11"}
    limited = tb.call("implementation_get", {"section": "rules", "limit": 3})
    assert limited["returned"] == 3 and limited["total"] == 12
    routes = tb.call(
        "implementation_get", {"section": "routes", "status": "implemented_not_required"}
    )
    assert routes["total"] == 9 and all(r["rules"] == [] for r in routes["items"])
    flows = tb.call("implementation_get", {"section": "flows"})
    assert flows["total"] == 24 and "evidence" not in flows["items"][0]
    assert tb.call("implementation_get", {"section": "machines"})["total"] == 2
    orphans = tb.call("implementation_get", {"section": "orphans"})
    assert orphans["total"] == 9 and orphans["items"][0]["proposal"]["selected"] == []
    ex = tb.call("implementation_get", {"section": "excluded"})
    assert [(x["key"], x["reason"]) for x in ex["items"]] == [("PUT /test/clock", "test_only")]


def test_bad_arguments_start_with_their_code(world) -> None:
    tb = world[0]
    with pytest.raises(ToolError, match=r"^bad_section:"):
        tb.call("implementation_get", {"section": "nope"})
    for lim in (0, 201, "5", True):
        with pytest.raises(ToolError, match=r"^bad_limit:"):
            tb.call("implementation_get", {"limit": lim})
    with pytest.raises(ToolError, match="unknown argument"):
        tb.call("implementation_get", {"bogus": 1})
    with pytest.raises(ToolError, match=r"^bad_limit:"):
        tb.call("workflow_find", {"question": "x", "limit": 11})
    with pytest.raises(ToolError, match="missing required"):
        tb.call("workflow_find", {})


# ---- workflow_get -------------------------------------------------------------------------


def test_workflow_get_a_flow_and_a_machine(world) -> None:
    tb = world[0]
    flows = tb.call("implementation_get", {"section": "flows"})["items"]
    fid = next(
        f["id"] for f in flows if f["routes"] == ["POST /api/jobs", "POST /api/jobs/{}/activate"]
    )
    out = tb.call("workflow_get", {"id": fid})
    assert (
        out["type"] == "flow"
        and out["kind"] == "owner_guard"
        and out["id"] == fid
        and out["effective"] == "verified"
    )
    assert out["inferred_name"] is True and out["basis"] == "observed"
    assert [s["method"] for s in out["steps"]] == ["POST", "POST"]
    assert out["steps"][1]["path"].startswith("/api/jobs/")
    assert out["rules"] == ["R10"] and out["step_evidence"]
    assert tb.call("workflow_get", {"id": f"impl:flow:{fid}"})["id"] == fid
    m = tb.call("workflow_get", {"id": "jobs.status"})
    assert m["type"] == "machine" and m["rules"] == ["R10"] and "queued -> active" in m["edges"]


def test_workflow_get_unknown_id_names_the_five_nearest(world) -> None:
    tb = world[0]
    with pytest.raises(ToolError) as e:
        tb.call("workflow_get", {"id": "wf-doesnotexis"})
    msg = str(e.value)
    assert msg.startswith("unknown_id:") and "nearest:" in msg
    assert len(msg.split("nearest:")[1].split(",")) == 5


# ---- workflow_find ------------------------------------------------------------------------


def test_workflow_find_exact_mention_ranks_first_and_says_exists(world) -> None:
    tb = world[0]
    out = tb.call("workflow_find", {"question": "What does POST /api/jobs do?"})
    assert out["hint"] == "exists"
    top = out["candidates"][0]
    assert top["target"] == "impl:route:POST /api/jobs" and top["exact"] is True
    assert "lexical match, not understanding" in out["note"]


def test_workflow_find_a_spec_only_route_is_spec_only(world) -> None:
    out = world[0].call(
        "workflow_find",
        {"question": "Can a project be restored with POST /api/projects/{id}/restore?"},
    )
    assert out["hint"] == "spec_only" and out["candidates"][0]["status"] == "spec_only"


def test_workflow_find_no_match_and_several(world) -> None:
    tb = world[0]
    assert (
        tb.call("workflow_find", {"question": "quantum entanglement of zebras"})["hint"]
        == "no_match"
    )
    assert tb.call("workflow_find", {"question": "?"})["hint"] == "no_match"
    out = tb.call("workflow_find", {"question": "ticket"})
    assert out["hint"] == "several" and len(out["candidates"]) >= 2


def test_workflow_find_is_deterministic_and_bounded(world) -> None:
    tb = world[0]
    a = tb.call("workflow_find", {"question": "publish a project", "limit": 3})
    for _ in range(5):
        assert tb.call("workflow_find", {"question": "publish a project", "limit": 3}) == a
    assert len(a["candidates"]) <= 3
    assert [c["score"] for c in a["candidates"]] == sorted(
        (c["score"] for c in a["candidates"]), reverse=True
    )


# ---- errors and writes --------------------------------------------------------------------


def test_errors_start_with_their_code(tmp_path: Path) -> None:
    proj, app, inv = make_project(tmp_path)
    tb = Toolbox(FakeBackend(proj))
    shutil.rmtree(inv)
    for name, args in (
        ("implementation_get", {}),
        ("workflow_get", {"id": "x"}),
        ("workflow_find", {"question": "x"}),
    ):
        with pytest.raises(ToolError, match=r"^inventory_missing:"):
            tb.call(name, args)
    (tmp_path / "inv").mkdir()
    (tmp_path / "inv" / "inventory.json").write_text("{")
    with pytest.raises(ToolError, match=r"^bad_inventory:"):
        tb.call("implementation_get", {})
    (proj / "studio.toml").write_text("[implementation]\n")
    with pytest.raises(ToolError, match=r"^inventory_missing: studio.toml"):
        tb.call("implementation_get", {})
    shutil.rmtree(tmp_path / "inv")
    capture(app, inv, extract=EXTRACT)
    (proj / "studio.toml").write_text(f'[implementation]\ninventory = "{inv}"\n')
    (proj / "spec.py").unlink()
    with pytest.raises(ToolError, match=r"^no_spec:"):
        tb.call("implementation_get", {})


def test_a_stale_inventory_is_shown_and_verified_turns_unverified(world) -> None:
    tb, proj, app, inv = world
    before = tb.call("implementation_get", {})
    assert (
        before["inventory"]["stale"] is False
        and before["summary"]["requirements"]["implemented_verified"] > 0
    )
    (app / "main.go").write_text((app / "main.go").read_text() + "\n// edit\n")
    after = tb.call("implementation_get", {})
    assert after["inventory"]["stale"] is True
    assert after["summary"]["requirements"]["implemented_verified"] == 0


def test_no_tool_writes_anything(world, tmp_path: Path) -> None:
    tb, proj, app, inv = world
    before = tree(proj, app, inv, tmp_path)
    for _ in range(10):
        tb.call("implementation_get", {})
        tb.call("implementation_get", {"section": "rules"})
        tb.call("workflow_get", {"id": "projects.status"})
        tb.call("workflow_find", {"question": "publish a project"})
    assert tree(proj, app, inv, tmp_path) == before


def test_a_proposal_goes_to_change_propose_as_it_is_and_nothing_is_accepted(tmp_path: Path) -> None:
    proj, app, inv = make_project(tmp_path)
    be = FakeBackend(proj)
    tb = Toolbox(be)
    finding = tb.call("implementation_get", {"section": "rules", "rule": "R5"})["items"][0][
        "finding"
    ]
    out = tb.call("change_propose", finding["proposal"])
    assert out["id"] == "c1" and "never applied from here" in out["note"]
    sent = be.new[0]
    assert sent["request"] == finding["proposal"]["request"]
    assert sent["selected"] == ["rule:R5"] and sent["source"] == finding["proposal"]["source"]
    assert not any("accept" in n for n in tb.names())  # no tool accepts


# ---- over stdio ---------------------------------------------------------------------------


def rpc(project: Path, msgs: list[dict[str, Any]], *args: str) -> list[dict[str, Any]]:
    r = subprocess.run(
        [sys.executable, "-m", "lattice.mcp", "serve", "--project", str(project), *args],
        input="\n".join(json.dumps(m) for m in msgs) + "\n",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    return [json.loads(x) for x in r.stdout.splitlines()]


def test_over_stdio_readonly_lists_and_calls_the_tools_and_instructions_are_unchanged(
    tmp_path: Path,
) -> None:
    proj, app, inv = make_project(tmp_path)
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    lst = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "implementation_get", "arguments": {}}}  # fmt: skip
    before = tree(proj, app, inv)
    out = rpc(proj, [init, lst, call], "--readonly")
    assert out[0]["result"]["instructions"] == INSTRUCTIONS
    names = [t["name"] for t in out[1]["result"]["tools"]]
    assert [n for n in names if n in IMPL_TOOLS] == IMPL_TOOLS
    assert "change_propose" not in names and "check_run" not in names
    body = json.loads(out[2]["result"]["content"][0]["text"])
    assert body["section"] == "summary" and not out[2]["result"].get("isError")
    assert tree(proj, app, inv) == before


def test_over_stdio_without_the_table_there_are_no_such_tools(tmp_path: Path) -> None:
    proj, _, _ = make_project(tmp_path, implementation=False)
    out = rpc(proj, [{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    assert not {t["name"] for t in out[0]["result"]["tools"]} & set(IMPL_TOOLS)


# ---- sessions -----------------------------------------------------------------------------


def test_session_allowlists_are_the_base_plus_the_three_tools() -> None:
    impl = tuple(f"mcp__lattice__{n}" for n in IMPL_TOOLS)
    assert sess._IMPL_TOOLS == impl
    for mode in ("ask", "explain", "change"):
        allowed = sess.MODE_PROFILES[mode].allowed
        assert allowed[allowed.index(impl[0]) : allowed.index(impl[0]) + 3] == impl
        i = allowed.index(sess._MULTI_TOOLS[-1])
        assert allowed[i + 1 : i + 4] == impl  # after the multi-project tools
    assert sess.MODE_PROFILES["change"].allowed == (
        *sess.MODE_PROFILES["ask"].allowed,
        *sess._CHANGE_TOOLS,
    )
    assert not set(impl) & set(sess.MODE_PROFILES["rebuild"].allowed)  # rebuild's list is unchanged
    assert not set(impl) & set(sess._MODEL_TOOLS)


def test_labels_and_the_extra_rule_only_with_the_table(tmp_path: Path) -> None:
    on, _, _ = make_project(tmp_path / "on")
    off, _, _ = make_project(tmp_path / "off", implementation=False)
    for n in IMPL_TOOLS:
        assert sess.tool_label(f"mcp__lattice__{n}", {}) == sess._MCP_LABELS[n] != n
    for mode in ("ask", "explain", "change"):
        p_on, p_off = sess.system_prompt(mode, on), sess.system_prompt(mode, off)
        assert p_on == p_off + "\n\n" + sess._IMPL_RULES
        assert sess._IMPL_RULES not in p_off
    assert sess.system_prompt("rebuild", on) == sess.system_prompt("rebuild", off)
    assert sess._IMPL_RULES.count(".") == 1  # one sentence... plus the abbreviation-free text
    assert sess.system_prompt("ask", off).startswith(sess._BASE_RULES)
