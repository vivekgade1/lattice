# ruff: noqa: F811 -- tests take the imported fixtures as parameters
"""Many projects behind one Studio server (lattice/studio/hub.py, API.md v1.6): discovery,
``GET /api/projects``, ``/p/<name>/api/...`` routing, isolation between projects, the
chat's prefixed ``--studio-url``, the CLI, and the mock's parity."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice.llm.client import ScriptedGenerator
from lattice.mcp.tools import HttpBackend, Toolbox
from lattice.studio import hub as hub_mod
from lattice.studio import server as server_mod
from lattice.studio.hub import StudioHub, discover, registry_dirs
from lattice.studio.mock_server import make_server as make_mock
from lattice.studio.server import StubGenerator, Studio, make_hub_server, make_server
from tests.spec.incremental_toy import NOTES_V1
from tests.studio.test_server import make_project, request
from tests.studio.test_session import (
    Fake,
    ask_turn,
    flag,
    poll_http,
)
from tests.studio.test_session import (
    fake as fake,  # noqa: F401 -- a fixture
)
from tests.studio.test_web_mock import check_model

PROJECT_KEYS = {
    "name",
    "root",
    "git_head",
    "spec",
    "app_configured",
    "last_check",
    "capabilities",
    "default",
    "state_warnings",  # API v1.10
}


def bare(d: Path, toml: str | None = None) -> Path:
    """A directory with only a studio.toml (a project, but no spec)."""
    d.mkdir(parents=True, exist_ok=True)
    if toml is not None:
        (d / "studio.toml").write_text(toml)
    return d


# ---------------------------------------------------------------- discovery


def test_discover_explicit_dirs_in_order(tmp_path: Path) -> None:
    a = make_project(tmp_path / "a", repo=False)
    b = make_project(tmp_path / "b", repo=False)
    assert discover([b, a]) == [("b", b.resolve()), ("a", a.resolve())]


def test_an_explicit_dir_that_is_no_project_contributes_its_children(tmp_path: Path) -> None:
    a = make_project(tmp_path / "group" / "a", repo=False)
    b = make_project(tmp_path / "group" / "b", repo=False)
    (tmp_path / "group" / "not-a-project").mkdir()
    assert [n for n, _ in discover([tmp_path / "group"])] == ["a", "b"]
    # a project directory is taken as is: its project children are not added
    make_project(a / "inner", repo=False)
    assert discover([a]) == [("a", a.resolve())]
    assert discover([tmp_path / "missing"]) == []
    assert [n for n, _ in discover([a, b, a])] == ["a", "b"], "deduplicated by path"
    assert b.exists()


def test_discover_from_the_cwd_and_nested_dogfood(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    make_project(repo / "app1", repo=False)
    make_project(repo / "dogfood" / "studio", repo=False)
    make_project(repo / "dogfood" / "studio" / "project_seed", repo=False)  # two levels: no
    bare(repo / "dogfood" / "other", '[project]\nname = "renamed"\n')
    got = discover(cwd=repo, registry=tmp_path / "none.toml")
    assert [n for n, _ in got] == ["app1", "renamed", "studio"]
    assert got[2][1] == (repo / "dogfood" / "studio").resolve()


def test_discover_finds_nothing(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert discover(cwd=tmp_path / "empty", registry=tmp_path / "none.toml") == []


def test_a_broken_studio_toml_is_still_a_project(tmp_path: Path) -> None:
    p = make_project(tmp_path / "p", repo=False)
    (p / "studio.toml").write_text("[app\nthis is not toml")
    assert discover([p]) == [("p", p.resolve())]
    srv = make_hub_server(discover([p]), 0)
    serve_bg(srv)
    try:
        base = url(srv)
        status, lst = request("GET", base + "/api/projects")
        assert status == 200 and lst[0]["app_configured"] is False and lst[0]["spec"] is True
        status, out = request("GET", base + "/p/p/api/model")
        assert status == 500 and "does not load" in out["detail"]
    finally:
        stop(srv)


def test_duplicate_names_get_a_numeric_suffix(tmp_path: Path) -> None:
    x = make_project(tmp_path / "one" / "notes", repo=False)
    y = make_project(tmp_path / "two" / "notes", repo=False)
    z = make_project(tmp_path / "three" / "notes", repo=False)
    assert discover([x, y, z]) == [
        ("notes", x.resolve()),
        ("notes-2", y.resolve()),
        ("notes-3", z.resolve()),
    ]


def test_the_registry_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = tmp_path / "cwd"
    make_project(cwd / "here", repo=False)
    far = make_project(tmp_path / "elsewhere" / "far", repo=False)
    reg = tmp_path / "cfg" / "projects.toml"
    reg.parent.mkdir()
    reg.write_text(
        f'[[project]]\npath = "{far}"\n\n[[project]]\npath = "../cwd/here"\n\n'
        '[[project]]\npath = "/does/not/exist"\n\n[[project]]\nnot_a_path = 1\n'
    )
    assert registry_dirs(reg)[0] == far
    got = discover(cwd=cwd, registry=reg)
    assert [n for n, _ in got] == ["here", "far"], "the cwd first; duplicates dropped"
    # explicit dirs ignore the registry
    assert [n for n, _ in discover([far], registry=reg)] == ["far"]
    # missing or broken files are ignored
    reg.write_text("[[project]\nbroken")
    assert registry_dirs(reg) == []
    assert registry_dirs(tmp_path / "nope.toml") == []
    # the default location honours XDG_CONFIG_HOME (and LATTICE_PROJECTS_REGISTRY)
    monkeypatch.delenv(hub_mod.REGISTRY_ENV, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert hub_mod.registry_path() == tmp_path / "xdg" / "lattice" / "projects.toml"
    monkeypatch.setenv(hub_mod.REGISTRY_ENV, str(reg))
    assert hub_mod.registry_path() == reg


def test_project_name_from_studio_toml(tmp_path: Path) -> None:
    p = make_project(tmp_path / "dir", repo=False)
    (p / "studio.toml").write_text('[project]\nname = "pretty"\n')
    assert discover([p]) == [("pretty", p.resolve())]
    srv = make_server(p, 0, generator=ScriptedGenerator([]))
    serve_bg(srv)
    try:
        assert request("GET", url(srv) + "/api/model")[1]["project"]["name"] == "pretty"
        assert request("GET", url(srv) + "/p/pretty/api/model")[0] == 200
    finally:
        stop(srv)


# ---------------------------------------------------------------- serving


def serve_bg(srv: Any) -> Any:
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def stop(srv: Any) -> None:
    srv.shutdown()
    srv.server_close()


def url(srv: Any) -> str:
    return f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture()
def two(tmp_path: Path) -> Iterator[tuple[str, Any, Path, Path]]:
    """A hub over projects `a` and `b` (both the toy notes project, stub LLM)."""
    a = make_project(tmp_path / "a")
    b = make_project(tmp_path / "b")
    srv = serve_bg(
        make_hub_server(discover([a, b]), 0, generator_factory=lambda _m: StubGenerator())
    )
    yield url(srv), srv, a, b
    stop(srv)


def test_projects_list_is_cheap_and_complete(two) -> None:
    base, srv, a, b = two
    status, lst = request("GET", base + "/api/projects")
    assert status == 200
    assert [p["name"] for p in lst] == ["a", "b"]
    for p in lst:
        assert set(p) == PROJECT_KEYS
        assert p["spec"] is True and p["app_configured"] is False and p["last_check"] is None
        assert len(p["git_head"]) >= 7 and set(p["capabilities"]) == {
            "session",
            "rebuild",
            "build_from_spec",
            "build_plan",
        }
        assert p["capabilities"]["rebuild"] is p["capabilities"]["build_from_spec"] is False
    assert [p["default"] for p in lst] == [True, False]
    assert lst[0]["root"] == str(a.resolve())
    # no Studio was built to answer it
    assert srv.hub.loaded("a") is None and srv.hub.loaded("b") is None
    assert request("GET", base + "/p/b/api/projects")[1] == lst


def test_projects_list_does_not_construct_studios(tmp_path: Path, monkeypatch) -> None:
    built: list[str] = []
    hub = StudioHub(
        [("a", make_project(tmp_path / "a", repo=False))],
        lambda root, name: built.append(name),
    )
    out = hub.list()
    assert built == [] and out[0]["git_head"] == "" and out[0]["spec"] is True


def test_prefix_routing_and_the_bare_default(two) -> None:
    base, srv, a, _b = two
    status, m_bare = request("GET", base + "/api/model")
    status2, m_a = request("GET", base + "/p/a/api/model")
    assert status == status2 == 200
    assert m_bare == m_a and m_a["project"]["name"] == "a"
    assert srv.hub.loaded("b") is None, "only the touched project is built"
    _, m_b = request("GET", base + "/p/b/api/model")
    assert m_b["project"] == {**m_a["project"], "name": "b", "root": m_b["project"]["root"]}
    assert m_b["project"]["root"] != m_a["project"]["root"]
    # every API route is served under the prefix, query strings included
    assert request("POST", base + "/p/b/api/changes", {"request": "Notes can be starred."}) == (
        200,
        {"id": "c1"},
    )
    t0 = time.monotonic()
    status, c = request("GET", base + "/p/b/api/changes/c1?wait=30")
    assert status == 200 and c["status"] == "ready" and time.monotonic() - t0 < 30
    assert request("GET", base + "/p/b/api/changes/c1?wait=soon")[0] == 422
    assert request("GET", base + "/p/b/api/nope") == (404, {"detail": "no such route"})
    assert request("POST", base + "/p/b/api/nope", {}) == (404, {"detail": "no such route"})
    assert request("DELETE", base + "/p/b/api/changes/c1")[0] == 409
    status, acc = request("POST", base + "/p/b/api/changes/c1/accept", {})
    assert status == 200 and acc["commit"]
    assert request("GET", base + "/p/b/api/model")[1]["project"]["git_head"] == acc["commit"][:7]


def test_unknown_project_is_404(two) -> None:
    base, srv, *_ = two
    nope = (404, {"detail": "no such project"})
    assert request("GET", base + "/p/nope/api/model") == nope
    assert request("GET", base + "/p/nope/api/changes/c1?wait=1") == nope
    assert request("POST", base + "/p/nope/api/changes", {"request": "x"}) == nope
    assert request("DELETE", base + "/p/nope/api/changes/c1") == nope
    assert request("GET", base + "/p/nope/") == nope
    assert request("GET", base + "/api/nope") == (404, {"detail": "no such route"})


def test_projects_do_not_share_change_sets(two) -> None:
    base, srv, a, b = two
    assert request("POST", base + "/p/a/api/changes", {"request": "Notes can be starred."})[1] == {
        "id": "c1"
    }
    assert request("POST", base + "/p/a/api/changes", {"request": "Notes can be pinned."})[1] == {
        "id": "c2"
    }
    assert request("GET", base + "/p/a/api/changes/c2?wait=30")[1]["status"] == "ready"
    # b numbers its own changes and sees none of a's
    assert request("GET", base + "/p/b/api/changes/c1") == (404, {"detail": "no change c1"})
    assert request("GET", base + "/p/b/api/model")[1]["changes"] == []
    assert request("POST", base + "/p/b/api/changes", {"request": "Notes can be shared."})[1] == {
        "id": "c1"
    }
    assert request("GET", base + "/p/b/api/changes/c1?wait=30")[1]["request"] == (
        "Notes can be shared."
    )
    assert [c["id"] for c in request("GET", base + "/p/a/api/model")[1]["changes"]] == [
        "c1",
        "c2",
    ]
    # accepting in a writes only a's files
    assert request("POST", base + "/p/a/api/changes/c1/accept", {})[0] == 200
    assert "starred" in (a / "REQUIREMENTS.md").read_text()
    assert (b / "REQUIREMENTS.md").read_text() == NOTES_V1
    assert srv.hub.get("a") is not srv.hub.get("b")


def test_default_flag(tmp_path: Path) -> None:
    a = make_project(tmp_path / "a", repo=False)
    b = make_project(tmp_path / "b", repo=False)
    srv = serve_bg(make_hub_server(discover([a, b]), 0, default="b"))
    try:
        assert request("GET", url(srv) + "/api/model")[1]["project"]["name"] == "b"
        lst = request("GET", url(srv) + "/api/projects")[1]
        assert [p["default"] for p in lst] == [False, True]
        assert srv.studio.name == "b"
    finally:
        stop(srv)
    with pytest.raises(ValueError, match="no project named"):
        make_hub_server(discover([a, b]), 0, default="zz")


def test_last_check_shows_once_the_project_is_loaded(two) -> None:
    base, srv, *_ = two
    st = srv.hub.get("b")
    st.last_check = {"verdict": "pass", "at": "2026-10-10T00:00:00Z", "targets": {}}
    lst = request("GET", base + "/api/projects")[1]
    assert lst[1]["last_check"] == {"verdict": "pass", "at": "2026-10-10T00:00:00Z"}
    assert lst[0]["last_check"] is None


def test_same_named_harness_modules_do_not_cross(tmp_path: Path) -> None:
    """Two projects whose studio.toml both say ``harness:make_app`` each get their own."""
    roots = []
    for name, limit in (("h1", 3), ("h2", 5)):
        p = make_project(tmp_path / name, repo=False)
        (p / "harness.py").write_text(
            "from tests.studio.notes_app import build_db, notes_app\n\n"
            f"make_app = notes_app({limit})\n\n\ndef login(client, handle):\n    return {limit}\n"
        )
        (p / "studio.toml").write_text(
            '[app]\napp = "harness:make_app"\ndb = "harness:build_db"\nlogin = "harness:login"\n'
        )
        roots.append(p)
    hub = StudioHub(discover(roots), lambda root, name: Studio(root, name=name))
    try:
        r1 = hub.get("h1").harness.resolve()
        r2 = hub.get("h2").harness.resolve()
        assert r1["app_factory"] is not r2["app_factory"]
        assert r1["login"](None, "x") == 3 and r2["login"](None, "x") == 5
    finally:
        hub.close()


# ---------------------------------------------------------------- single-directory mode


def test_single_dir_mode_is_unchanged(tmp_path: Path) -> None:
    p = make_project(tmp_path / "p")
    srv = serve_bg(make_server(p, 0, generator=ScriptedGenerator([])))
    try:
        base = url(srv)
        assert srv.studio.base_url == base, "no prefix in single-directory mode"
        assert srv.hub.prefixed is False
        m = request("GET", base + "/api/model")[1]
        assert m["project"]["name"] == "p"
        assert request("GET", base + "/p/p/api/model")[1] == m
        lst = request("GET", base + "/api/projects")[1]
        assert [(x["name"], x["default"]) for x in lst] == [("p", True)]
    finally:
        stop(srv)


# ---------------------------------------------------------------- sessions and MCP


def test_mcp_config_has_the_prefixed_studio_url(tmp_path: Path, fake: Fake) -> None:
    a = make_project(tmp_path / "a", repo=False)
    b = make_project(tmp_path / "b", repo=False)
    srv = serve_bg(make_hub_server(discover([a, b]), 0, claude_bin=str(fake.bin)))
    try:
        base = url(srv)
        fake.script(ask_turn())
        status, s = request("POST", base + "/p/b/api/session", {"mode": "ask"})
        assert status == 201
        request("POST", f"{base}/p/b/api/session/{s['id']}/message", {"text": "q"})
        poll_http(base + "/p/b", s["id"])
        argv = fake.calls[-1]["argv"]
        cfg = json.loads(Path(flag(argv, "--mcp-config")).read_text())
        args = cfg["mcpServers"]["lattice"]["args"]
        assert flag(args, "--studio-url") == base + "/p/b"
        assert flag(args, "--project") == str(b.resolve())
        # sessions are per project too
        assert request("GET", base + "/p/a/api/session")[1] == []
        assert [x["id"] for x in request("GET", base + "/p/b/api/session")[1]] == [s["id"]]
        lst = request("GET", base + "/api/projects")[1]
        assert all(p["capabilities"]["session"] for p in lst), "the fake claude is runnable"
    finally:
        stop(srv)


def test_mcp_http_backend_works_under_a_prefix(two) -> None:
    base, srv, _a, b = two
    box = Toolbox(HttpBackend(base + "/p/b/", b))
    assert box.call("model_get", {})["rule_count"] >= 4
    assert box.call("change_propose", {"request": "Notes can be starred."})["id"] == "c1"
    assert srv.hub.get("b").changes["c1"]["request"] == "Notes can be starred."
    assert srv.hub.loaded("a") is None, "the other project never saw it"
    assert box.call("change_get", {"id": "c1"})["id"] == "c1"


# ---------------------------------------------------------------- the CLI


def test_main_zero_projects_exits_2(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(hub_mod.REGISTRY_ENV, str(tmp_path / "none.toml"))
    assert server_mod.main(["--port", "0"]) == 2
    err = capsys.readouterr().err
    assert "no Lattice projects found" in err and "studio.toml" in err


def test_main_discovers_and_passes_default(tmp_path, monkeypatch, capsys) -> None:
    seen: dict[str, Any] = {}

    def fake_hub(projects, port, **kw):
        seen.update(kw, projects=projects, port=port)
        raise SystemExit(0)

    monkeypatch.setattr(server_mod, "make_hub_server", fake_hub)
    monkeypatch.setenv(hub_mod.REGISTRY_ENV, str(tmp_path / "none.toml"))
    a = make_project(tmp_path / "a", repo=False)
    b = make_project(tmp_path / "b", repo=False)
    with pytest.raises(SystemExit):
        server_mod.main([str(a), str(b), "--port", "0", "--default", "b", "--stub-llm"])
    assert [n for n, _ in seen["projects"]] == ["a", "b"] and seen["default"] == "b"
    assert isinstance(seen["generator_factory"]("m"), StubGenerator)
    assert server_mod.main([str(a), str(b), "--port", "0", "--default", "zz"]) == 2
    assert "no such project" in capsys.readouterr().err
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        server_mod.main(["--port", "0"])
    assert [n for n, _ in seen["projects"]] == ["a", "b"]
    assert seen["generator_factory"] is None


def test_main_single_dir_still_goes_through_make_server(tmp_path, monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_make_server(root, port, **kw):
        seen.update(kw, root=root)
        raise SystemExit(0)

    monkeypatch.setattr(server_mod, "make_server", fake_make_server)
    p = make_project(tmp_path / "p", repo=False)
    with pytest.raises(SystemExit):
        server_mod.main([str(p), "--port", "0"])
    assert seen["root"] == p.resolve()
    assert server_mod.main([str(p), "--port", "0", "--default", "other"]) == 2
    assert server_mod.main([str(tmp_path / "nothing"), "--port", "0"]) == 2


def test_lattice_studio_dispatch_forwards_dirs(monkeypatch) -> None:
    from lattice import cli

    seen: list[list[str]] = []
    monkeypatch.setattr(server_mod, "main", lambda argv: seen.append(list(argv)) or 0)
    assert cli.main(["studio"]) == 0
    assert cli.main(["studio", "x", "y", "--default", "y"]) == 0
    assert seen == [[], ["x", "y", "--default", "y"]]


# ---------------------------------------------------------------- the mock


@pytest.fixture()
def mock() -> Iterator[Callable[..., tuple[int, Any]]]:
    srv = serve_bg(make_mock(0))
    base = url(srv)
    yield lambda method, path, body=None: request(method, base + path, body)
    stop(srv)


def test_mock_mirrors_projects_and_prefix_routes(two, mock) -> None:
    real_base = two[0]
    status, lst = mock("GET", "/api/projects")
    assert status == 200 and [p["name"] for p in lst] == ["job-scheduler-studio", "lattice-studio"]
    real = request("GET", real_base + "/api/projects")[1]
    for p, r in zip(lst, real, strict=False):
        assert set(p) == set(r) == PROJECT_KEYS
        assert set(p["capabilities"]) == set(r["capabilities"])
        assert {k: type(v) for k, v in p.items() if k != "last_check"} == {
            k: type(v) for k, v in r.items() if k != "last_check"
        }
    assert [p["default"] for p in lst] == [True, False]
    _, bare_m = mock("GET", "/api/model")
    _, js = mock("GET", "/p/job-scheduler-studio/api/model")
    _, ls = mock("GET", "/p/lattice-studio/api/model")
    check_model(bare_m)
    check_model(ls)
    assert bare_m == js and js["project"]["name"] == "job-scheduler-studio"
    assert ls["project"]["name"] == "lattice-studio"
    assert len(ls["rules"]) != len(js["rules"]) and len(ls["rules"]) > 0
    assert ls["changes"] == [] and [c["id"] for c in js["changes"]] == ["c0"]
    assert mock("GET", "/p/nope/api/model") == (404, {"detail": "no such project"})
    # isolated change sets, ?wait-free polling as before
    assert mock("POST", "/p/lattice-studio/api/changes", {"request": "x"})[1] == {"id": "c1"}
    assert mock("GET", "/p/lattice-studio/api/changes/c1")[0] == 200
    assert mock("GET", "/api/changes/c1")[0] == 404
