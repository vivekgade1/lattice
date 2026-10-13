"""``GET /api/contracts`` (API v1.17) on the real Studio server and the mock: the static contract
check of the workspace's opted-in links, read-only, equal to ``lattice contracts check
--static-only --json`` (A1), over a synthetic two-provider workspace built under ``tmp_path``.
No app is started, no LLM is used."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice import workspace as ws
from lattice.studio.mock_server import make_server as make_mock
from lattice.studio.server import StubGenerator, make_server
from tests.contracts.helpers import World
from tests.studio.contract_world import make_world, types, without_elapsed
from tests.studio.test_server import make_project, request

TOP = {"workspace", "consumer", "checked", "links", "problems", "elapsed_s"}
NONE = {"workspace": None, "links": [], "problems": []}


def serve_bg(srv: Any) -> str:
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def stop(srv: Any) -> None:
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def world(tmp_path: Path) -> World:
    return make_world(tmp_path / "w")


@pytest.fixture()
def served(world: World, tmp_path: Path) -> Iterator[str]:
    """A Studio server over a toy project, whose workspace is the contracts world's."""
    solo = make_project(tmp_path / "solo", repo=False)
    srv = make_server(solo, generator=StubGenerator(), workspace=world.root / ws.MANIFEST)
    base = serve_bg(srv)
    try:
        yield base
    finally:
        stop(srv)


def get(base: str, path: str = "/api/contracts") -> Any:
    status, body = request("GET", base + path)
    assert status == 200, body
    return body


# ---------------------------------------------------------------- A1: the real server vs the CLI


def test_the_answer_is_the_cli_static_json_key_for_key(tmp_path: Path) -> None:
    w = make_world(tmp_path / "w", second_link=False)  # no live link: nothing may differ
    solo = make_project(tmp_path / "solo", repo=False)
    srv = make_server(solo, generator=StubGenerator(), workspace=w.root / ws.MANIFEST)
    base = serve_bg(srv)
    try:
        out = get(base)
        rc, cli = w.run("check", "--static-only")
    finally:
        stop(srv)
    assert rc == 1 and set(out) == TOP and out["checked"] == 1
    assert without_elapsed(out) == without_elapsed(cli)


def test_with_a_live_link_only_its_reason_differs_from_the_cli(served: str, world: World) -> None:
    out = get(served)
    assert set(out) == TOP and out["checked"] == 2
    rc, cli = world.run("check", "--static-only")
    assert rc == 1
    a, b = without_elapsed(out), without_elapsed(cli)
    assert a["links"][1]["live"] == {"status": "not_run", "reason": "read-only"}
    assert b["links"][1]["live"] == {"status": "not_run", "reason": "static-only"}
    a["links"][1]["live"] = b["links"][1]["live"]
    assert a == b
    link = out["links"][0]
    assert (link["consumer"], link["provider"], link["contract"], link["status"]) == (
        "cons", "prov", "static", "broken",
    )  # fmt: skip
    assert [(f["class"], f["kind"], f["item"]) for f in link["findings"]] == [
        ("BROKEN", "status_not_observed", "201"),
        ("CHANGED", "status_added", "200"),
    ]
    assert [(g["kind"], g["item"]) for g in link["gaps"]] == [("status_unobserved", "500")]
    assert link["live"] is None


def test_a_live_link_is_never_run_here(served: str, world: World) -> None:
    out = get(served)
    live = out["links"][1]
    assert (live["provider"], live["contract"], live["status"]) == ("prov2", "live", "ok")
    assert live["live"] == {"status": "not_run", "reason": "read-only"}
    rc, cli = world.run("check", "--static-only")
    assert cli["links"][1]["live"] == {"status": "not_run", "reason": "static-only"}
    assert rc == 1


def test_consumer_limits_the_check_and_an_unknown_one_is_a_problem(
    served: str, world: World
) -> None:
    some = get(served, "/api/contracts?consumer=cons")
    assert some["consumer"] == "cons" and some["checked"] == 2
    rc, cli = world.run("check", "cons", "--static-only")
    assert rc == 1 and without_elapsed(some)["links"][0] == without_elapsed(cli)["links"][0]
    none = get(served, "/api/contracts?consumer=prov")  # a provider with no link of its own
    assert none["consumer"] == "prov" and none["checked"] == 0 and none["links"] == []
    ghost = get(served, "/api/contracts?consumer=ghost")
    assert ghost["links"] == [] and ghost["checked"] == 0
    assert [(p["code"], p["severity"]) for p in ghost["problems"]] == [("unknown_project", "error")]
    assert "have prov, cons, prov2" in ghost["problems"][0]["message"]
    assert get(served, "/api/contracts?consumer=")["consumer"] is None  # empty: everyone


def test_it_answers_under_a_project_prefix_and_writes_nothing(served: str, world: World) -> None:
    lock = world.lock
    before = {
        p: p.read_bytes() for p in (lock, world.root / ws.MANIFEST, world.provider / "spec.py")
    }
    mtime = lock.stat().st_mtime_ns
    first = get(served)
    again = get(served, "/p/solo/api/contracts")
    assert without_elapsed(first) == without_elapsed(again)
    status, body = request("POST", served + "/api/contracts", {})
    assert status == 404 and body["detail"] == "no such route"
    assert {p: p.read_bytes() for p in before} == before and lock.stat().st_mtime_ns == mtime
    assert sorted(p.name for p in world.consumer.iterdir()) == [
        "lattice.contracts.lock.json", "lattice.imports.toml",
    ]  # fmt: skip


def test_the_manifest_is_read_on_every_request(served: str, world: World) -> None:
    assert get(served)["checked"] == 2
    world.contract = None  # the consumer's link stops opting in; the second link stays
    world.write_manifest(
        '\n[[project]]\nname = "prov2"\npath = "prov2"\nrole = "provider"\nspec = "spec:SPEC"\n'
        '\n[[link]]\nfrom = "cons"\nto = "prov2"\nkind = "uses"\ncontract = "live"\n'
    )
    out = get(served)
    assert out["checked"] == 1 and out["links"][0]["provider"] == "prov2"
    (world.root / ws.MANIFEST).write_text("[[[")
    out = get(served)
    assert out["workspace"] is None and out["links"] == [] and set(out) == set(NONE)
    assert [(p["code"], p["severity"]) for p in out["problems"]] == [("unreadable", "error")]


def test_a_manifest_with_an_error_answers_its_problem_and_no_links(
    served: str, world: World
) -> None:
    text = (world.root / ws.MANIFEST).read_text().replace('contract = "static"', 'contract = "yes"')
    (world.root / ws.MANIFEST).write_text(text)
    out = get(served)
    assert out["workspace"] == "w" and out["links"] == [] and out["checked"] == 0
    assert "bad_contract" in [p["code"] for p in out["problems"]]


def test_an_error_link_is_listed_with_its_code(served: str, world: World) -> None:
    world.lock.unlink()
    out = get(served)
    assert [lk["status"] for lk in out["links"]] == ["error", "error"]
    assert out["links"][0]["error"]["code"] == "no_pin"
    assert out["links"][0]["findings"] == [] and out["links"][0]["live"] is None


def test_no_workspace_answers_null(tmp_path: Path) -> None:
    srv = make_server(make_project(tmp_path / "solo", repo=False), generator=StubGenerator())
    base = serve_bg(srv)
    try:
        assert request("GET", base + "/api/contracts") == (200, NONE)
        assert request("GET", base + "/api/contracts?consumer=cons") == (200, NONE)
    finally:
        stop(srv)


# ---------------------------------------------------------------- the mock


def test_the_mock_has_the_real_shape(served: str) -> None:
    real = get(served)
    srv = make_mock()
    mbase = serve_bg(srv)
    try:
        status, mock = request("GET", mbase + "/api/contracts")
        assert request("GET", mbase + "/p/lattice-studio/api/contracts")[1] == mock
        assert request("POST", mbase + "/api/contracts", {})[0] == 404
        one = request("GET", mbase + "/api/contracts?consumer=scheduler-web")[1]
        ghost = request("GET", mbase + "/api/contracts?consumer=ghost")[1]
        srv.workspace_enabled = False
        none = request("GET", mbase + "/api/contracts")[1]
    finally:
        stop(srv)
    assert status == 200 and set(mock) == set(real) == TOP
    assert types(mock) == types(real)
    assert mock["checked"] == len(mock["links"]) == 2 and one["links"] == mock["links"]
    for row in mock["links"]:
        assert set(row) == set(real["links"][0])
    static, live = mock["links"]
    rstatic, rlive = real["links"]
    assert types(static) == types(rstatic) and types(live) == {**types(rlive), "pinned": "dict"}
    assert set(static["pinned"]) == set(rstatic["pinned"])
    assert [set(f) for f in static["findings"]] == [set(f) for f in rstatic["findings"]]
    assert [f["class"] for f in static["findings"]] == ["BROKEN", "CHANGED"]
    assert [set(g) for g in static["gaps"]] == [set(g) for g in rstatic["gaps"]]
    assert live["live"] == rlive["live"] == {"status": "not_run", "reason": "read-only"}
    assert static["status"] == "broken" and static["live"] is None
    assert [set(p) for p in mock["problems"]] == [{"code", "severity", "message"}]
    assert one["consumer"] == "scheduler-web" and one["checked"] == 2
    assert ghost["links"] == [] and ghost["problems"][-1]["code"] == "unknown_project"
    assert none == NONE
