"""The MCP tool ``contract_check {consumer?}`` (lattice/mcp/projects.py, multi-project layer 3):
listed only with a workspace, allowed under ``--readonly``, static only, and equal to
``lattice contracts check --static-only --json`` (A1) on a synthetic two-provider workspace built
under ``tmp_path``. The single-project server's ``tools/list`` stays a byte prefix."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lattice import workspace as ws
from lattice.mcp.projects import KnownProject, ProjectRouter
from lattice.mcp.server import INSTRUCTIONS
from lattice.mcp.tools import PendingBackend, Toolbox, ToolError
from tests.contracts.helpers import World
from tests.mcp.test_multi_server import PROJECT_TOOLS, serve
from tests.mcp.test_server import INIT, call, payload, req
from tests.studio.contract_world import make_world, types, without_elapsed
from tests.studio.test_server import make_project

dump = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"))  # noqa: E731


@pytest.fixture()
def world(tmp_path: Path) -> World:
    return make_world(tmp_path / "w")


def router(w: World, *, readonly: bool = False, with_ws: bool = True) -> ProjectRouter:
    projects = [
        KnownProject("prov", w.provider.resolve(), "workspace"),
        KnownProject("cons", w.consumer.resolve(), "workspace"),
    ]
    loaded = ws.load(w.root / ws.MANIFEST) if with_ws else None
    return ProjectRouter(
        projects, lambda p: PendingBackend(p.root), readonly=readonly, workspace=loaded
    )


# ---------------------------------------------------------------- listed only with a workspace


def test_listed_only_with_a_workspace_and_a_read_only_tool(world: World) -> None:
    r = router(world)
    names = r.names()
    assert names[-5:] == [
        "workspace_impact",
        "contract_check",
        "contract_impact",
        "linked_question_list",
        "linked_question_draft",  # slice 4d (disclosed)
    ]
    d = next(t for t in r.list_tools() if t["name"] == "contract_check")
    assert d["annotations"]["readOnlyHint"] is True
    assert d["inputSchema"]["properties"] == {
        "consumer": {"type": "string", "description": "a project name of the workspace"}
    }
    assert d["inputSchema"]["required"] == [] and d["inputSchema"]["additionalProperties"] is False
    plain = router(world, with_ws=False)
    assert "contract_check" not in plain.names() and "workspace_impact" not in plain.names()
    with pytest.raises(KeyError):
        plain.call("contract_check", {})


def test_readonly_still_lists_and_allows_it(world: World) -> None:
    ro = router(world, readonly=True)
    names = ro.names()
    assert "contract_check" in names and "change_propose" not in names
    out = ro.call("contract_check", {})
    assert out["checked"] == 2 and out["links"][0]["status"] == "broken"


# ---------------------------------------------------------------- A1: the tool vs the CLI


def test_the_result_is_the_cli_static_json(world: World) -> None:
    r = router(world)
    rc, cli = world.run("check", "--static-only")
    out = r.call("contract_check", {})
    assert rc == 1 and set(out) == set(cli)
    assert without_elapsed(out) == without_elapsed(cli)
    assert dump(without_elapsed(out)) == dump(without_elapsed(cli)), "byte for byte"
    assert out["links"][1]["live"] == {"status": "not_run", "reason": "static-only"}
    rc, cli1 = world.run("check", "cons", "--static-only")
    one = r.call("contract_check", {"consumer": "cons"})
    assert without_elapsed(one) == without_elapsed(cli1) and one["consumer"] == "cons"
    assert types(one) == types(cli1)


def test_an_unknown_consumer_is_a_problem_like_the_cli(world: World) -> None:
    r = router(world)
    rc, cli = world.run("check", "ghost", "--static-only")
    out = r.call("contract_check", {"consumer": "ghost"})
    assert rc == 2 and without_elapsed(out) == without_elapsed(cli)
    assert out["links"] == [] and out["problems"][0]["code"] == "unknown_project"
    with pytest.raises(ToolError, match="must be a string"):
        r.call("contract_check", {"consumer": 3})
    with pytest.raises(ToolError, match="unknown argument"):
        r.call("contract_check", {"project": "prov"})


def test_static_only_never_runs_the_live_part(world: World) -> None:
    out = router(world).call("contract_check", {})
    live = next(lk for lk in out["links"] if lk["contract"] == "live")
    assert live["live"] == {"status": "not_run", "reason": "static-only"}
    assert not (world.root / "prov2" / "app.db").exists()


def test_the_manifest_is_read_on_every_call_and_nothing_is_written(world: World) -> None:
    r = router(world)
    files = sorted(p.relative_to(world.root) for p in world.root.rglob("*") if p.is_file())
    blobs = {
        p: p.read_bytes() for p in world.root.rglob("*") if p.is_file() and ".git" not in p.parts
    }
    assert r.call("contract_check", {})["checked"] == 2
    after = sorted(p.relative_to(world.root) for p in world.root.rglob("*") if p.is_file())
    assert after == files
    assert {p: p.read_bytes() for p in blobs} == blobs
    world.contract = None
    world.write_manifest(
        '\n[[project]]\nname = "prov2"\npath = "prov2"\nrole = "provider"\nspec = "spec:SPEC"\n'
        '\n[[link]]\nfrom = "cons"\nto = "prov2"\nkind = "uses"\ncontract = "live"\n'
    )
    assert r.call("contract_check", {})["checked"] == 1
    (world.root / ws.MANIFEST).write_text("[[[")
    gone = r.call("contract_check", {})
    assert gone["workspace"] is None and gone["links"] == []
    assert [p["code"] for p in gone["problems"]] == ["unreadable"]


# ---------------------------------------------------------------- single-project compatibility


def test_a_single_project_router_without_a_workspace_is_unchanged(tmp_path: Path) -> None:
    a = make_project(tmp_path / "alpha", repo=False)
    old = Toolbox(PendingBackend(a))
    r = ProjectRouter(
        [KnownProject("alpha", a.resolve(), "explicit")], lambda p: PendingBackend(p.root)
    )
    assert dump(r.list_tools()[: len(old.list_tools())]) == dump(old.list_tools())
    assert [d["name"] for d in r.list_tools()[len(old.list_tools()) :]] == PROJECT_TOOLS
    assert r.instructions(INSTRUCTIONS) == INSTRUCTIONS


def test_a_workspace_appends_the_tool_after_the_old_ones(world: World, tmp_path: Path) -> None:
    solo = make_project(tmp_path / "solo", repo=False)
    old = Toolbox(PendingBackend(solo)).list_tools()
    loaded = ws.load(world.root / ws.MANIFEST)
    r = ProjectRouter(
        [KnownProject("solo", solo.resolve(), "explicit")],
        lambda p: PendingBackend(p.root),
        workspace=loaded,
    )
    tools = r.list_tools()
    assert dump(tools[: len(old)]) == dump(old)
    assert [t["name"] for t in tools[len(old) :]] == [
        *PROJECT_TOOLS,
        "workspace_impact",
        "contract_check",
        "contract_impact",
        "linked_question_list",
        "linked_question_draft",  # slice 4d (disclosed)
    ]
    assert r.instructions(INSTRUCTIONS) == INSTRUCTIONS


# ---------------------------------------------------------------- over the real stdio protocol


def test_over_stdio_the_tool_equals_the_cli(world: World) -> None:
    msgs: list[Any] = [
        INIT,
        req(1, "tools/list"),
        call(2, "contract_check"),
        call(3, "contract_check", consumer="cons"),
        call(4, "contract_check", consumer=7),
    ]
    out = serve(world.root, msgs, "--readonly")
    by = {o["id"]: o for o in out}
    names = [t["name"] for t in by[1]["result"]["tools"]]
    assert names[-5:] == [
        "workspace_impact",
        "contract_check",
        "contract_impact",
        "linked_question_list",
        "linked_question_draft",  # slice 4d (disclosed)
    ]
    assert "change_propose" not in names and "check_run" not in names
    rc, cli = world.run("check", "--static-only")
    assert rc == 1
    assert without_elapsed(payload(by[2])) == without_elapsed(cli)
    _rc, cli1 = world.run("check", "cons", "--static-only")
    assert without_elapsed(payload(by[3])) == without_elapsed(cli1)
    assert by[4]["result"]["isError"] is True


def test_over_stdio_one_project_and_no_workspace_is_the_old_list(tmp_path: Path) -> None:
    a = make_project(tmp_path / "alpha", repo=False)
    out = serve(
        tmp_path, [INIT, req(1, "tools/list"), call(2, "contract_check")], "--project", str(a)
    )
    old = Toolbox(PendingBackend(a)).list_tools()
    tools = out[1]["result"]["tools"]
    assert dump(tools[: len(old)]) == dump(old)
    assert [t["name"] for t in tools[len(old) :]] == PROJECT_TOOLS
    assert out[0]["result"]["instructions"] == INSTRUCTIONS
    assert "result" not in out[2] and "contract_check" in out[2]["error"]["message"]
