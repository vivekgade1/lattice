"""``GET /api/workspace`` (API v1.16) on the real Studio server and the mock, the workspace as a
Studio discovery source, and the web app's switcher and Map code (MP-3)."""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice import workspace as ws
from lattice.studio.hub import discover, discover_sourced
from lattice.studio.mock_server import make_server as make_mock
from lattice.studio.server import StubGenerator, make_hub_server, make_server
from tests.studio.test_server import make_project, request

WEB = Path(__file__).resolve().parents[2] / "lattice" / "studio" / "web" / "js"
KEYS = {"workspace", "projects", "links", "problems"}
ROW_KEYS = {"name", "path", "role", "in_hub", "hub_name", "spec"}


def manifest(root: Path, extra: str = "") -> Path:
    """billing-api (a project, served), web-app (a project, not served), shared-auth (missing)."""
    p = root / ws.MANIFEST
    p.write_text(
        '[workspace]\nname = "shop"\n'
        '[[project]]\nname = "billing-api"\npath = "billing"\nrole = "provider"\n'
        '[[project]]\nname = "web-app"\npath = "web"\nrole = "consumer"\n'
        '[[project]]\nname = "shared-auth"\npath = "auth"\nrole = "library"\n'
        '[[link]]\nfrom = "web-app"\nto = "billing-api"\nkind = "uses"\nnote = "POST /invoices"\n'
        '[[link]]\nfrom = "web-app"\nto = "shared-auth"\nkind = "depends_on"\n'
        '[[link]]\nfrom = "billing-api"\nto = "shared-auth"\nkind = "depends_on"\n' + extra
    )
    return p


def serve_bg(srv: Any) -> str:
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def stop(srv: Any) -> None:
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def shop(tmp_path: Path) -> Path:
    make_project(tmp_path / "billing", repo=False)
    make_project(tmp_path / "web", repo=False)
    return tmp_path


@pytest.fixture()
def served(shop: Path) -> Iterator[tuple[str, Path]]:
    srv = make_server(shop / "billing", generator=StubGenerator(), workspace=manifest(shop))
    base = serve_bg(srv)
    try:
        yield base, shop
    finally:
        stop(srv)


def test_the_real_server_answers_the_manifest(served) -> None:
    base, shop = served
    status, out = request("GET", base + "/api/workspace")
    assert status == 200 and set(out) == KEYS
    assert out["workspace"] == {"name": "shop", "path": str((shop / ws.MANIFEST).resolve())}
    rows = {r["name"]: r for r in out["projects"]}
    assert all(set(r) == ROW_KEYS for r in out["projects"])
    assert rows["billing-api"] == {
        "name": "billing-api", "path": str((shop / "billing").resolve()), "role": "provider",
        "in_hub": True, "hub_name": "billing", "spec": True,
    }  # fmt: skip
    assert rows["web-app"]["in_hub"] is False and rows["web-app"]["hub_name"] is None
    assert rows["web-app"]["spec"] is True and rows["shared-auth"]["spec"] is False
    assert out["links"][0] == {"from": "web-app", "to": "billing-api", "kind": "uses",
                               "note": "POST /invoices"}  # fmt: skip
    assert [p["code"] for p in out["problems"]] == ["missing_path"]
    assert out["problems"][0]["severity"] == "warning"
    # the same under the project prefix; no write route; reading changes nothing on disk
    assert request("GET", base + "/p/billing/api/workspace") == (200, out)
    before = (shop / ws.MANIFEST).read_text()
    status, body = request("POST", base + "/api/workspace", {})
    assert status == 404 and body["detail"] == "no such route"
    assert (shop / ws.MANIFEST).read_text() == before


def test_impact_matches_the_manifest(shop: Path) -> None:
    w = ws.load(manifest(shop))
    assert set(w.impact_of("shared-auth")) == {"web-app", "billing-api"}
    assert w.impact_of("web-app") == []


def test_no_manifest_answers_null(shop: Path) -> None:
    srv = make_server(shop / "billing", generator=StubGenerator())
    base = serve_bg(srv)
    try:
        assert request("GET", base + "/api/workspace") == (
            200, {"workspace": None, "projects": [], "links": [], "problems": []},
        )  # fmt: skip
    finally:
        stop(srv)


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ('[[link]]\nfrom = "web-app"\nto = "ghost"\nkind = "uses"\n', "unknown_project"),
        ('[[project]]\nname = "web-app"\npath = "web"\n', "duplicate_name"),
        ('[[link]]\nfrom = "web-app"\nto = "web-app"\nkind = "uses"\n', "self_link"),
        (
            '[[link]]\nfrom = "shared-auth"\nto = "billing-api"\nkind = "depends_on"\n',
            "depends_on_cycle",
        ),
    ],
)
def test_errors_are_listed_and_the_rest_still_answers(shop: Path, extra: str, code: str) -> None:
    srv = make_server(shop / "billing", generator=StubGenerator(), workspace=manifest(shop, extra))
    base = serve_bg(srv)
    try:
        status, out = request("GET", base + "/api/workspace")
    finally:
        stop(srv)
    assert status == 200 and out["workspace"]["name"] == "shop"
    assert [r["name"] for r in out["projects"]] == ["billing-api", "web-app", "shared-auth"]
    errs = [p["code"] for p in out["problems"] if p["severity"] == "error"]
    assert errs == [code]


def test_an_unreadable_manifest_says_where_and_why(shop: Path) -> None:
    bad = shop / "broken.toml"
    bad.write_text("[[[")
    srv = make_server(shop / "billing", generator=StubGenerator(), workspace=bad)
    base = serve_bg(srv)
    try:
        status, out = request("GET", base + "/api/workspace")
    finally:
        stop(srv)
    assert status == 200
    assert out["workspace"] == {"name": None, "path": str(bad)}
    assert out["projects"] == [] and [p["code"] for p in out["problems"]] == ["unreadable"]


def test_a_hub_marks_every_project_it_serves(shop: Path) -> None:
    srv = make_hub_server(
        [("bill", shop / "billing"), ("web", shop / "web")],
        generator=StubGenerator(),
        workspace=manifest(shop),
    )
    base = serve_bg(srv)
    try:
        out = request("GET", base + "/api/workspace")[1]
    finally:
        stop(srv)
    assert [(r["name"], r["in_hub"], r["hub_name"]) for r in out["projects"]] == [
        ("billing-api", True, "bill"), ("web-app", True, "web"), ("shared-auth", False, None),
    ]  # fmt: skip


# ---------------------------------------------------------------- discovery


def test_the_workspace_joins_discovery_under_its_names(shop: Path, tmp_path: Path) -> None:
    w = ws.load(manifest(shop))
    empty = tmp_path / "cwd"
    empty.mkdir()
    reg = tmp_path / "none.toml"
    assert discover(cwd=empty, registry=reg) == []
    assert discover(cwd=empty, registry=reg, workspace=w) == [
        ("billing-api", (shop / "billing").resolve()),
        ("web-app", (shop / "web").resolve()),
    ], "projects only (the missing shared-auth is not), named by the manifest"
    # discovered from the cwd first, then labelled as the workspace's
    found = discover_sourced(cwd=shop, registry=reg, workspace=w)
    assert [(n, s) for n, _r, s in found] == [
        ("billing-api", "workspace"),
        ("web-app", "workspace"),
    ]
    # explicit DIRs: the hub is exactly the DIRs (a declared one keeps its manifest name)
    assert discover([shop / "billing"], workspace=w) == [
        ("billing-api", (shop / "billing").resolve())
    ]
    assert discover([shop / "billing"]) == [("billing", (shop / "billing").resolve())]
    # a non-workspace project never takes a manifest name
    make_project(tmp_path / "x" / "web-app", repo=False)
    names = [n for n, _ in discover(cwd=tmp_path / "x", registry=reg, workspace=w)]
    assert names == ["web-app-2", "billing-api", "web-app"]


def test_the_studio_cli_finds_the_manifest(shop: Path, monkeypatch) -> None:
    from lattice.studio import server as server_mod

    seen: dict[str, Any] = {}

    def fake_single(args: Any) -> int:
        seen["ws"] = args.workspace_path
        return 7

    monkeypatch.setattr(server_mod, "_single", fake_single)
    monkeypatch.delenv(ws.ENV, raising=False)
    p = manifest(shop)
    monkeypatch.chdir(shop / "web")
    assert server_mod.main([str(shop / "billing")]) == 7 and seen["ws"] == p.resolve()
    assert server_mod.main([str(shop / "billing"), "--workspace", str(shop)]) == 7
    assert seen["ws"] == shop / ws.MANIFEST
    monkeypatch.chdir(shop.parent)
    assert server_mod.main([str(shop / "billing")]) == 7 and seen["ws"] is None


# ---------------------------------------------------------------- the mock


def test_the_mock_has_the_real_shape(served) -> None:
    base, _shop = served
    real = request("GET", base + "/api/workspace")[1]
    srv = make_mock()
    mbase = serve_bg(srv)
    try:
        status, mock = request("GET", mbase + "/api/workspace")
        assert request("GET", mbase + "/p/lattice-studio/api/workspace")[1] == mock
        assert request("POST", mbase + "/api/workspace", {})[0] == 404
        srv.workspace_enabled = False
        none = request("GET", mbase + "/api/workspace")[1]
    finally:
        stop(srv)
    assert status == 200 and set(mock) == set(real) == KEYS
    assert set(mock["workspace"]) == set(real["workspace"])
    for row in mock["projects"]:
        assert set(row) == ROW_KEYS
        ref = real["projects"][0]
        assert {k: type(v) for k, v in row.items() if k != "hub_name"} == {
            k: type(v) for k, v in ref.items() if k != "hub_name"
        }
    assert [(r["name"], r["in_hub"], r["hub_name"]) for r in mock["projects"]] == [
        ("scheduler", True, "job-scheduler-studio"),
        ("studio", True, "lattice-studio"),
        ("scheduler-web", False, None),
    ]
    assert all(set(lk) == set(real["links"][0]) for lk in mock["links"])
    assert [set(p) for p in mock["problems"]] == [set(real["problems"][0])]
    assert mock["problems"][0]["code"] == "missing_path"
    assert none == {"workspace": None, "projects": [], "links": [], "problems": []}


# ---------------------------------------------------------------- the web app (static)


def test_the_switcher_and_the_map_read_the_workspace() -> None:
    api = (WEB / "api.js").read_text()
    app = (WEB / "app.js").read_text()
    mp = (WEB / "views" / "map.js").read_text()
    assert 'fetch("/api/workspace"' in api
    assert "api.workspace()" in app and "projectLabel(p)" in app
    assert "(not in this Studio)" in app and "Workspace ${ws.workspace.name}" in app
    assert re.search(r'\["workspace", "Workspace"\]', mp) and "function paintWorkspace" in mp
    assert 'kind: "project"' in mp and "mountGraph(canvas" in mp
