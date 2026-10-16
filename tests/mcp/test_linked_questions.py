"""The MCP tool ``linked_question_list {consumer, status?}`` (lattice/mcp/projects.py, slice 4b):
listed only with a workspace, read-only and allowed under ``--readonly``, equal to ``lattice
contracts questions list CONSUMER --json``, and the ONLY linked-question tool: no tool resolves or
dismisses a note (the owner's default: only the consumer's owner clears one), over the synthetic
two-provider workspace of tests/studio/contract_world.py."""

from __future__ import annotations

import hashlib
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
from tests.studio.contract_world import make_world

dump = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"))  # noqa: E731


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[World]:
    w = make_world(tmp_path / "w")
    rc, out = w.run("questions", "init", "cons")
    assert rc == 0, out
    yield w


def router(w: World, *, readonly: bool = False, with_ws: bool = True) -> ProjectRouter:
    projects = [
        KnownProject("prov", w.provider.resolve(), "workspace"),
        KnownProject("cons", w.consumer.resolve(), "workspace"),
    ]
    loaded = ws.load(w.root / ws.MANIFEST) if with_ws else None
    return ProjectRouter(
        projects, lambda p: PendingBackend(p.root), readonly=readonly, workspace=loaded
    )


def notify(w: World) -> None:
    rc, out = w.run("notify", "prov", "--candidate", str(w.provider))  # the drifted tree
    assert rc == 0, out
    assert out["consumers"][0]["question"] == "lq1"


def files(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------- listing


def test_listed_only_with_a_workspace_and_read_only(world: World) -> None:
    r = router(world)
    d = next(t for t in r.list_tools() if t["name"] == "linked_question_list")
    assert d["annotations"]["readOnlyHint"] is True
    assert set(d["inputSchema"]["properties"]) == {"consumer", "status"}
    assert d["inputSchema"]["required"] == []  # API v1.20 (F3): defaults to the active consumer
    assert d["inputSchema"]["additionalProperties"] is False
    plain = router(world, with_ws=False)
    assert "linked_question_list" not in plain.names()
    with pytest.raises(KeyError):
        plain.call("linked_question_list", {"consumer": "cons"})


def test_readonly_lists_and_allows_it(world: World) -> None:
    ro = router(world, readonly=True)
    assert "linked_question_list" in ro.names() and "change_propose" not in ro.names()
    assert ro.call("linked_question_list", {"consumer": "cons"})["state"] == "present"


def test_no_tool_resolves_or_dismisses_a_question(world: World) -> None:
    for readonly in (False, True):
        r = router(world, readonly=readonly)
        ours = [n for n in r.names() if "question" in n or "linked" in n]
        # slice 4d (disclosed): the one added tool is the read-only draft, which clears nothing
        assert ours == ["linked_question_list", "linked_question_draft"]
        assert not [n for n in r.names() if "resolve" in n or "dismiss" in n]
        for name in ("linked_question_resolve", "linked_question_dismiss", "questions_resolve"):
            with pytest.raises(KeyError):
                r.call(name, {"consumer": "cons", "id": "lq1"})


# ---------------------------------------------------------------- the tool is the CLI


def test_the_result_is_the_cli_json_byte_for_byte(world: World) -> None:
    r = router(world)
    empty = r.call("linked_question_list", {"consumer": "cons"})
    _rc, cli = world.run("questions", "list", "cons")
    assert dump(empty) == dump(cli) and empty["questions"] == []
    notify(world)
    for status in (None, "all", "open", "resolved"):
        args = {"consumer": "cons", **({"status": status} if status else {})}
        _rc, cli = world.run("questions", "list", "cons", "--status", status or "all")
        out = r.call("linked_question_list", args)
        assert dump(out) == dump(cli)
    out = r.call("linked_question_list", {"consumer": "cons"})
    assert [q["id"] for q in out["questions"]] == ["lq1"]
    assert out["counts"] == {"open": 1, "resolved": 0, "dismissed": 0, "superseded": 0}
    assert out["consumer"] == "cons" and out["state"] == "present"


def test_a_cleared_question_shows_the_clearing(world: World) -> None:
    notify(world)
    world.run("questions", "resolve", "cons", "lq1", "--note", "adapted")
    out = router(world).call("linked_question_list", {"consumer": "cons", "status": "resolved"})
    assert out["questions"][0]["resolution"]["note"] == "adapted"
    still_open = router(world).call("linked_question_list", {"consumer": "cons", "status": "open"})
    assert still_open["questions"] == []


def test_a_missing_state_is_listed_as_missing_and_nothing_is_created(tmp_path: Path) -> None:
    w = make_world(tmp_path / "w")
    out = router(w).call("linked_question_list", {"consumer": "cons"})
    assert out["state"] == "missing" and out["questions"] == []
    assert not (w.consumer / ".lattice").exists()


def test_it_writes_nothing_and_rereads_the_manifest(world: World) -> None:
    notify(world)
    r = router(world)
    before = files(world.root)
    assert r.call("linked_question_list", {"consumer": "cons"})["counts"]["open"] == 1
    assert files(world.root) == before
    (world.root / ws.MANIFEST).write_text("[[[")
    with pytest.raises(ToolError, match="manifest_error"):
        r.call("linked_question_list", {"consumer": "cons"})


# ---------------------------------------------------------------- errors


@pytest.mark.parametrize("args", [{}, {"consumer": ""}, {"status": "open"}])
def test_the_consumer_is_required_when_the_active_project_is_not_one(
    world: World, args: dict[str, Any]
) -> None:
    # the fixture's active project is `prov`, not a consumer (API v1.20, F3)
    with pytest.raises(ToolError, match="is required"):
        router(world).call("linked_question_list", args)


def test_a_non_string_consumer_is_an_error(world: World) -> None:
    with pytest.raises(ToolError, match="argument 'consumer' must be a string"):
        router(world).call("linked_question_list", {"consumer": 3})


def test_errors_name_their_code(world: World) -> None:
    r = router(world)
    with pytest.raises(ToolError, match="^unknown_project"):
        r.call("linked_question_list", {"consumer": "ghost"})
    with pytest.raises(ToolError, match="^bad_status"):
        r.call("linked_question_list", {"consumer": "cons", "status": "bogus"})
    with pytest.raises(ToolError, match="must be a string"):
        r.call("linked_question_list", {"consumer": "cons", "status": 3})
    with pytest.raises(ToolError, match="unknown argument"):
        r.call("linked_question_list", {"consumer": "cons", "project": "cons"})


# ---------------------------------------------------------------- over the real stdio protocol


def test_over_stdio_the_tool_equals_the_cli_and_nothing_clears(world: World) -> None:
    notify(world)
    msgs: list[Any] = [
        INIT,
        req(1, "tools/list"),
        call(2, "linked_question_list", consumer="cons"),
        call(3, "linked_question_list", consumer="cons", status="open"),
        call(4, "linked_question_list"),
        call(5, "linked_question_resolve", consumer="cons", id="lq1"),
    ]
    out = serve(world.root, msgs, "--readonly")
    by = {o["id"]: o for o in out}
    names = [t["name"] for t in by[1]["result"]["tools"]]
    assert "linked_question_list" in names and "change_propose" not in names
    assert not [n for n in names if "resolve" in n or "dismiss" in n]
    _rc, cli = world.run("questions", "list", "cons")
    assert payload(by[2]) == cli
    _rc, cli_open = world.run("questions", "list", "cons", "--status", "open")
    assert payload(by[3]) == cli_open
    assert by[4]["result"]["isError"] is True
    assert "error" in by[5] or by[5]["result"]["isError"] is True
    assert (world.consumer / ".lattice" / "state" / "cons" / "linked_questions"
            / "lq1.json").is_file()  # fmt: skip
    assert (
        json.loads((world.consumer / ".lattice/state/cons/linked_questions/lq1.json").read_text())[
            "status"
        ]
        == "open"
    )
