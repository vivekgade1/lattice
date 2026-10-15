"""``consumer`` defaults to the active project (lattice/mcp/projects.py, API.md v1.20, F3 of
docs/results/2026-10-14-agent-run-3-prereg.md): ``contract_check`` and ``linked_question_list``
over the synthetic workspace of tests/studio/contract_world.py (provider ``prov``, consumer
``cons`` with a static link to ``prov`` and a live one to ``prov2``), in process and over stdio.
The result is exactly the CLI's for that consumer."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice import workspace as ws
from lattice.mcp.projects import KnownProject, ProjectRouter
from lattice.mcp.tools import PendingBackend, ToolError
from tests.contracts.helpers import World
from tests.mcp.test_multi_server import serve
from tests.mcp.test_server import INIT, call, payload, req
from tests.studio.contract_world import make_world, without_elapsed

dump = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"))  # noqa: E731


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[World]:
    w = make_world(tmp_path / "w")
    rc, out = w.run("questions", "init", "cons")
    assert rc == 0, out
    rc, out = w.run("notify", "prov", "--candidate", str(w.provider))
    assert rc == 0, out
    yield w


def router(w: World, *, active: str) -> ProjectRouter:
    roots = {"prov": w.provider, "cons": w.consumer}
    order = [active, *(n for n in roots if n != active)]
    projects = [KnownProject(n, roots[n].resolve(), "workspace") for n in order]
    return ProjectRouter(
        projects, lambda p: PendingBackend(p.root), workspace=ws.load(w.root / ws.MANIFEST)
    )


def test_an_active_consumer_is_the_default_of_both_tools(world: World) -> None:
    r = router(world, active="cons")
    _rc, cli_check = world.run("check", "cons", "--static-only")
    _rc, cli_list = world.run("questions", "list", "cons")
    assert cli_list["questions"], "the fixture notified one question"
    for args in ({}, {"consumer": ""}, {"consumer": "cons"}):
        got = r.call("contract_check", args)
        assert without_elapsed(got) == without_elapsed(cli_check)
        assert got["consumer"] == "cons"
        assert dump(r.call("linked_question_list", args)) == dump(cli_list)
    assert dump(r.call("linked_question_list", {"status": "open"})) == dump(
        world.run("questions", "list", "cons", "--status", "open")[1]
    )


def test_a_non_consumer_keeps_the_old_default_of_contract_check(world: World) -> None:
    r = router(world, active="prov")
    _rc, cli_all = world.run("check", "--static-only")
    got = r.call("contract_check", {})
    assert without_elapsed(got) == without_elapsed(cli_all) and got["consumer"] is None


@pytest.mark.parametrize("args", [{}, {"consumer": ""}, {"status": "open"}])
def test_linked_question_list_names_the_consumers_when_the_active_is_not_one(
    world: World, args: dict[str, Any]
) -> None:
    with pytest.raises(ToolError) as e:
        router(world, active="prov").call("linked_question_list", args)
    assert str(e.value) == (
        "argument 'consumer' is required: the active project prov is not a contract consumer "
        "of the workspace (consumers: cons)"
    )


@pytest.mark.parametrize("tool", ["contract_check", "linked_question_list"])
def test_a_non_string_consumer_is_an_error_whoever_is_active(world: World, tool: str) -> None:
    for active in ("prov", "cons"):
        with pytest.raises(ToolError, match="argument 'consumer' must be a string"):
            router(world, active=active).call(tool, {"consumer": 3})


def test_an_explicit_consumer_wins_over_the_active_project(world: World) -> None:
    r = router(world, active="cons")
    out = r.call("linked_question_list", {"consumer": "prov"})
    assert out["consumer"] == "prov" and out["questions"] == []
    assert r.call("contract_check", {"consumer": "prov"})["consumer"] == "prov"


def test_the_schema_and_descriptions(world: World) -> None:
    tools = {t["name"]: t for t in router(world, active="cons").list_tools()}
    lq, cc = tools["linked_question_list"], tools["contract_check"]
    assert lq["inputSchema"]["required"] == []
    assert set(lq["inputSchema"]["properties"]) == {"consumer", "status"}
    assert lq["inputSchema"]["properties"]["consumer"] == {
        "type": "string", "description": "a project name of the workspace"}  # fmt: skip
    assert (
        "`consumer`: a workspace project name (default: the active project when it is a "
        "consumer with an opted-in link); `status`" in lq["description"]
    )
    assert (
        "`consumer`: a project name (default: the active project when it is a consumer with an "
        "opted-in link, else every consumer with one)." in cc["description"]
    )


def test_over_stdio_the_active_consumer_is_the_default(world: World) -> None:
    msgs: list[Any] = [
        INIT,
        call(1, "contract_check"),
        call(2, "contract_check", consumer="cons"),
        call(3, "linked_question_list"),
        call(4, "linked_question_list", consumer="cons"),
        call(5, "project_list"),
    ]
    out = serve(world.root, msgs, "--project", str(world.consumer), "--readonly")
    by = {o["id"]: o for o in out}
    assert payload(by[5])["active"] == "cons"
    _rc, cli_check = world.run("check", "cons", "--static-only")
    _rc, cli_list = world.run("questions", "list", "cons")
    assert without_elapsed(payload(by[1])) == without_elapsed(cli_check)
    assert without_elapsed(payload(by[2])) == without_elapsed(cli_check)
    assert payload(by[3]) == cli_list == payload(by[4])


def test_over_stdio_a_workspace_root_launch_has_no_default(world: World) -> None:
    out = serve(world.root, [INIT, req(1, "tools/list"), call(2, "linked_question_list")])
    by = {o["id"]: o for o in out}
    lq = next(t for t in by[1]["result"]["tools"] if t["name"] == "linked_question_list")
    assert lq["inputSchema"]["required"] == []
    assert by[2]["result"]["isError"] is True
