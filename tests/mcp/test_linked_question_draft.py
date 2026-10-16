"""The MCP tool ``linked_question_draft {consumer?, id}`` (lattice/mcp/projects.py, slice 4d):
listed only with a workspace (also ``--readonly``), appended after the old tools, read-only, equal
to ``lattice contracts questions draft CONSUMER ID --json``, writing nothing, and not named by any
instruction string (the byte rule: ``WORKSPACE_NOTE`` and ``READONLY_NOTE`` are unchanged). Over the
synthetic two-provider workspace of tests/studio/contract_world.py."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice import workspace as ws
from lattice.contracts.__main__ import main as contracts_main
from lattice.mcp import projects as proj
from lattice.mcp.projects import KnownProject, ProjectRouter
from lattice.mcp.tools import PendingBackend, ToolError
from tests.contracts.helpers import World
from tests.mcp.test_multi_server import serve
from tests.mcp.test_server import INIT, call, payload, req
from tests.studio.contract_world import make_world

TOOL = "linked_question_draft"
OLD = ("workspace_impact", "contract_check", "contract_impact", "linked_question_list")


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[World]:
    w = make_world(tmp_path / "w")
    rc, out = w.run("questions", "init", "cons")
    assert rc == 0, out
    rc, out = w.run("notify", "prov", "--candidate", str(w.provider))
    assert rc == 0 and out["consumers"][0]["question"] == "lq1", out
    yield w


def router(w: World, *, readonly: bool = False, with_ws: bool = True,
           active: str | None = None) -> ProjectRouter:  # fmt: skip
    projects = [
        KnownProject("prov", w.provider.resolve(), "workspace"),
        KnownProject("cons", w.consumer.resolve(), "workspace"),
    ]
    loaded = ws.load(w.root / ws.MANIFEST) if with_ws else None
    r = ProjectRouter(
        projects, lambda p: PendingBackend(p.root), readonly=readonly, workspace=loaded,
        workspace_path=w.root / ws.MANIFEST,
    )  # fmt: skip
    if active:
        r.active = active
    return r


def cli_draft(w: World, *argv: str) -> tuple[int, dict[str, Any]]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = contracts_main(["questions", "draft", *argv, "--json", "--workspace", str(w.root)])
    return rc, json.loads(out.getvalue())


def files(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}  # fmt: skip


def test_listed_after_the_old_tools_only_with_a_workspace_and_read_only(world: World) -> None:
    r = router(world)
    names = r.names()
    assert names[-5:] == [*OLD, TOOL]
    d = next(t for t in r.list_tools() if t["name"] == TOOL)
    assert d["annotations"] == {"readOnlyHint": True, "openWorldHint": False}
    assert set(d["inputSchema"]["properties"]) == {"consumer", "id"}
    assert d["inputSchema"]["required"] == ["id"]
    assert d["inputSchema"]["additionalProperties"] is False
    plain = router(world, with_ws=False)
    assert TOOL not in plain.names()
    with pytest.raises(KeyError):
        plain.call(TOOL, {"consumer": "cons", "id": "lq1"})
    assert TOOL in proj.WORKSPACE_TOOLS


def test_readonly_lists_and_allows_it(world: World) -> None:
    ro = router(world, readonly=True)
    assert TOOL in ro.names() and "change_propose" not in ro.names()
    assert ro.call(TOOL, {"consumer": "cons", "id": "lq1"})["applied"] is False


def test_no_instruction_names_the_tool_and_the_notes_are_unchanged(world: World) -> None:
    assert TOOL not in proj.WORKSPACE_NOTE and TOOL not in proj.READONLY_NOTE
    assert TOOL not in proj.MULTI_NOTE
    base = "BASE."
    out = router(world, readonly=True).instructions(base)
    assert out == base + proj.MULTI_NOTE + proj.WORKSPACE_NOTE + proj.READONLY_NOTE
    assert TOOL not in out


def test_the_result_is_the_cli_json(world: World) -> None:
    rc, cli = cli_draft(world, "cons", "lq1")
    assert rc == 0
    out = router(world).call(TOOL, {"consumer": "cons", "id": "lq1"})
    assert json.dumps(out, sort_keys=True) == json.dumps(cli, sort_keys=True)
    assert out["text"].startswith("# Draft: adapt cons to prov (linked question lq1)")
    assert out["kind"] == "consumer_draft" and out["edits"]


def test_the_consumer_defaults_to_the_active_project(world: World) -> None:
    r = router(world, active="cons")
    assert r.call(TOOL, {"id": "lq1"}) == r.call(TOOL, {"consumer": "cons", "id": "lq1"})
    with pytest.raises(ToolError, match="'consumer' is required"):
        router(world, active="prov").call(TOOL, {"id": "lq1"})


def test_nothing_is_written(world: World) -> None:
    before = files(world.root)
    r = router(world)
    for _ in range(3):
        r.call(TOOL, {"consumer": "cons", "id": "lq1"})
    assert files(world.root) == before
    assert not list(world.root.rglob("changes"))


def test_refusals_are_tool_errors_with_the_code(world: World) -> None:
    r = router(world)
    with pytest.raises(ToolError, match=r"^unknown_question:"):
        r.call(TOOL, {"consumer": "cons", "id": "lq9"})
    with pytest.raises(ToolError, match=r"^unknown_project:"):
        r.call(TOOL, {"consumer": "ghost", "id": "lq1"})
    with pytest.raises(ToolError, match="'id' is required"):
        r.call(TOOL, {"consumer": "cons"})
    with pytest.raises(ToolError, match="'consumer' must be a string"):
        r.call(TOOL, {"consumer": 3, "id": "lq1"})
    with pytest.raises(ToolError, match="unknown argument"):
        r.call(TOOL, {"consumer": "cons", "id": "lq1", "apply": True})
    rc, out = world.run("questions", "dismiss", "cons", "lq1")
    assert rc == 0
    with pytest.raises(ToolError, match=r"^not_open:"):
        r.call(TOOL, {"consumer": "cons", "id": "lq1"})


def test_over_stdio_the_tool_equals_the_cli(world: World) -> None:
    msgs: list[Any] = [
        INIT,
        req(1, "tools/list"),
        call(2, TOOL, consumer="cons", id="lq1"),
        call(3, TOOL, id="lq1"),
        call(4, TOOL, consumer="cons", id="lq9"),
    ]
    before = files(world.root)
    out = serve(world.root, msgs, "--readonly")
    by = {o["id"]: o for o in out}
    names = [t["name"] for t in by[1]["result"]["tools"]]
    assert names[-5:] == [*OLD, TOOL] and "change_propose" not in names
    _rc, cli = cli_draft(world, "cons", "lq1")
    assert payload(by[2]) == cli
    assert by[3]["result"]["isError"] is True  # the launch directory is not a consumer
    assert by[4]["result"]["isError"] is True
    assert files(world.root) == before


def test_the_instructions_of_the_server_do_not_change(world: World) -> None:
    out = serve(world.root, [INIT], "--readonly")
    text = out[0]["result"]["instructions"]
    assert TOOL not in text
    assert text.endswith(proj.READONLY_NOTE)
