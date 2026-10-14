"""The MCP tool ``contract_impact {provider, candidate}`` (lattice/mcp/projects.py, slice 4a):
listed only with a workspace, allowed under ``--readonly``, both arguments required, and equal to
``lattice contracts impact PROVIDER --candidate X --json`` (the manifest re-read on every call, a
relative candidate directory relative to the manifest's directory), over the synthetic
two-provider workspace of tests/studio/contract_world.py."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from lattice import workspace as ws
from lattice.mcp.projects import KnownProject, ProjectRouter
from lattice.mcp.tools import PendingBackend, ToolError
from tests.contracts.helpers import World
from tests.mcp.test_multi_server import serve
from tests.mcp.test_server import INIT, call, payload, req
from tests.studio.contract_world import make_world, types, without_elapsed

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


def files(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------- listing


def test_listed_only_with_a_workspace_and_read_only(world: World) -> None:
    r = router(world)
    d = next(t for t in r.list_tools() if t["name"] == "contract_impact")
    assert d["annotations"]["readOnlyHint"] is True
    assert set(d["inputSchema"]["properties"]) == {"provider", "candidate"}
    assert d["inputSchema"]["required"] == ["provider", "candidate"]
    assert d["inputSchema"]["additionalProperties"] is False
    plain = router(world, with_ws=False)
    assert "contract_impact" not in plain.names()
    with pytest.raises(KeyError):
        plain.call("contract_impact", {"provider": "prov", "candidate": "HEAD"})


def test_readonly_lists_and_allows_it(world: World) -> None:
    ro = router(world, readonly=True)
    assert "contract_impact" in ro.names() and "change_propose" not in ro.names()
    out = ro.call("contract_impact", {"provider": "prov", "candidate": "HEAD"})
    assert out["checked"] == 1 and out["advisory"] is True


# ---------------------------------------------------------------- the tool is the CLI


def test_the_result_is_the_cli_json_byte_for_byte(world: World) -> None:
    r = router(world)
    for cand in ("HEAD", str(world.provider)):
        rc, cli = world.run("impact", "prov", "--candidate", cand)
        out = r.call("contract_impact", {"provider": "prov", "candidate": cand})
        assert set(out) == set(cli) and out["exit_code"] == rc
        assert without_elapsed(out) == without_elapsed(cli)
        assert dump(without_elapsed(out)) == dump(without_elapsed(cli))
        assert types(out) == types(cli)
    assert [row["label"] for row in out["consumers"]] == ["contradiction"]
    assert out["consumers"][0]["live"] is None


def test_a_live_link_is_listed_and_never_run(world: World) -> None:
    cand = str(world.root / "prov2")  # not a git repository: a directory candidate
    rc, cli = world.run("impact", "prov2", "--candidate", cand)
    out = router(world).call("contract_impact", {"provider": "prov2", "candidate": cand})
    assert rc == 0 and without_elapsed(out) == without_elapsed(cli)
    row = out["consumers"][0]
    assert (row["contract"], row["label"]) == ("live", "unaffected")
    assert row["live"] == {"status": "not_run", "reason": "static-only"}
    assert not (world.root / "prov2" / "app.db").exists()


def test_an_unknown_provider_is_a_problem_like_the_cli(world: World) -> None:
    rc, cli = world.run("impact", "ghost", "--candidate", "HEAD")
    out = router(world).call("contract_impact", {"provider": "ghost", "candidate": "HEAD"})
    assert rc == 2 and without_elapsed(out) == without_elapsed(cli)
    assert out["problems"][-1]["code"] == "unknown_project" and out["consumers"] == []


def test_a_relative_directory_is_relative_to_the_manifest(world: World) -> None:
    out = router(world).call("contract_impact", {"provider": "prov", "candidate": "prov"})
    assert out["candidate"]["kind"] == "path"
    assert out["candidate"]["resolved"] == str(world.provider.resolve())
    assert out["consumers"][0]["label"] == "contradiction"  # the drifted working tree


@pytest.mark.parametrize(
    "args",
    [{}, {"provider": "prov"}, {"candidate": "HEAD"}, {"provider": "", "candidate": "HEAD"},
     {"provider": "prov", "candidate": ""}, {"provider": 3, "candidate": "HEAD"},
     {"provider": "prov", "candidate": ["HEAD"]}],
)  # fmt: skip
def test_both_arguments_are_required_strings(world: World, args: dict[str, Any]) -> None:
    with pytest.raises(ToolError, match="is required"):
        router(world).call("contract_impact", args)


def test_unknown_arguments_are_refused(world: World) -> None:
    with pytest.raises(ToolError, match="unknown argument"):
        router(world).call(
            "contract_impact", {"provider": "prov", "candidate": "HEAD", "project": "prov"}
        )


def test_the_manifest_is_read_on_every_call_and_nothing_is_written(world: World) -> None:
    r = router(world)
    before = files(world.root)
    args = {"provider": "prov", "candidate": "HEAD"}
    assert r.call("contract_impact", args)["checked"] == 1
    assert files(world.root) == before
    (world.root / ws.MANIFEST).write_text("[[[")
    gone = r.call("contract_impact", args)
    assert gone["workspace"] is None and gone["consumers"] == [] and gone["exit_code"] == 2
    assert [p["code"] for p in gone["problems"]] == ["unreadable"]
    assert gone["candidate"]["input"] == "HEAD" and gone["current"] is None


# ---------------------------------------------------------------- over the real stdio protocol


def test_over_stdio_the_tool_equals_the_cli(world: World) -> None:
    msgs: list[Any] = [
        INIT,
        req(1, "tools/list"),
        call(2, "contract_impact", provider="prov", candidate="HEAD"),
        call(3, "contract_impact", provider="prov"),
    ]
    out = serve(world.root, msgs, "--readonly")
    by = {o["id"]: o for o in out}
    names = [t["name"] for t in by[1]["result"]["tools"]]
    assert "contract_impact" in names and "change_propose" not in names
    rc, cli = world.run("impact", "prov", "--candidate", "HEAD")
    assert without_elapsed(payload(by[2])) == without_elapsed(cli) and rc == 0
    assert by[3]["result"]["isError"] is True
