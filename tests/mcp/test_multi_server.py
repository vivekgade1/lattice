"""One MCP server, many projects (lattice/mcp/projects.py, multi-project layer 1): who is known,
the `project` argument, the active project, unknown projects, the workspace tools, and the
single-project server's byte compatibility. Stub generators only (no Claude CLI, no API key)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice import workspace as ws
from lattice.llm.client import ScriptedGenerator
from lattice.mcp.projects import KnownProject, ProjectRouter, known_projects
from lattice.mcp.server import INSTRUCTIONS, McpServer
from lattice.mcp.tools import LocalBackend, PendingBackend, Toolbox, ToolError
from tests.mcp.test_server import INIT, call, payload, req
from tests.studio.test_server import make_project

PROJECT_TOOLS = ["project_list", "project_set_active"]


def mark(project: Path, text: str) -> Path:
    """Make rule R1 of `project`'s trace say `text` (so two toy projects differ)."""
    p = project / "spec.trace.json"
    d = json.loads(p.read_text())
    d["rules"][0]["text"] = text
    p.write_text(json.dumps(d))
    return project


@pytest.fixture
def pair(tmp_path: Path) -> tuple[Path, Path]:
    a = mark(make_project(tmp_path / "alpha", repo=False), "Alpha only: notes have owners.")
    b = mark(make_project(tmp_path / "beta", repo=False), "Beta only: notes are public.")
    return a, b


def local(p: KnownProject) -> LocalBackend:
    return LocalBackend(p.root, generator=ScriptedGenerator([]))


def router(projects: list[KnownProject], **kw: Any) -> ProjectRouter:
    return ProjectRouter(projects, local, **kw)


def kp(name: str, root: Path, source: str = "explicit", role: str | None = None) -> KnownProject:
    return KnownProject(name, root.resolve(), source, role)


# ---------------------------------------------------------------- who is known


def test_one_explicit_project_and_no_workspace_is_a_single_project(pair, tmp_path: Path) -> None:
    a, _b = pair
    known = known_projects([a], launch=tmp_path)
    assert known == [KnownProject("alpha", a.resolve(), "explicit")]


def test_no_project_means_the_launch_dir_plus_discovery_and_registry(pair, tmp_path) -> None:
    a, b = pair
    far = make_project(tmp_path / "far" / "gamma", repo=False)
    reg = tmp_path / "projects.toml"
    reg.write_text(f'[[project]]\npath = "{far}"\n')
    launch = tmp_path
    known = known_projects([], launch=launch, registry=reg)
    assert [(k.name, k.source) for k in known] == [
        (tmp_path.name, "launch"), ("alpha", "discovered"), ("beta", "discovered"),
        ("gamma", "registry"),
    ]  # fmt: skip
    assert known[0].spec == "missing" and known[1].spec == "present"


def test_the_workspace_names_and_labels_its_projects(pair, tmp_path: Path) -> None:
    a, b = pair
    m = tmp_path / "lattice.workspace.toml"
    m.write_text(
        '[[project]]\nname = "a-svc"\npath = "alpha"\nrole = "provider"\n'
        '[[project]]\nname = "b-app"\npath = "beta"\nrole = "consumer"\n'
        '[[project]]\nname = "gone"\npath = "nowhere"\n'
        '[[link]]\nfrom = "b-app"\nto = "a-svc"\nkind = "uses"\n'
    )
    w = ws.load(m)
    reg = tmp_path / "none.toml"
    known = known_projects([], launch=tmp_path, workspace=w, registry=reg)
    assert [(k.name, k.source, k.role) for k in known] == [
        (tmp_path.name, "launch", None), ("a-svc", "workspace", "provider"),
        ("b-app", "workspace", "consumer"), ("gone", "workspace", None),
    ]  # fmt: skip
    # explicit: those plus the workspace, the explicit one keeps its source but takes its name
    known = known_projects([a], launch=tmp_path, workspace=w)
    assert [(k.name, k.source) for k in known] == [
        ("a-svc", "explicit"), ("b-app", "workspace"), ("gone", "workspace"),
    ]  # fmt: skip


# ---------------------------------------------------------------- the project argument


def test_the_project_argument_picks_the_project(pair) -> None:
    a, b = pair
    r = router([kp("alpha", a), kp("beta", b)])
    ma = r.call("model_get", {"project": "alpha"})
    mb = r.call("model_get", {"project": "beta"})
    assert ma["rules"][0]["text"].startswith("Alpha only") and ma["project"] == "alpha"
    assert mb["rules"][0]["text"].startswith("Beta only") and mb["project"] == "beta"
    assert mb["project_info"]["root"] == str(b.resolve()), "the old value is kept"
    # by path, absolute or relative to the launch directory
    assert r.call("rule_get", {"id": "R1", "project": str(b)})["text"].startswith("Beta only")
    r2 = router([kp("alpha", a), kp("beta", b)], launch=b.parent)
    assert r2.call("rule_get", {"id": "R1", "project": "beta/"})["text"].startswith("Beta")
    # absent: the active project (the first); a call never changes it
    assert r.call("model_get", {})["project"] == "alpha"
    assert r.active == "alpha"
    status = r.call("project_status", {"project": "beta"})
    assert status["project"] == "beta" and status["project_info"] == str(b.resolve())


def test_an_unknown_project_is_a_tool_error_listing_the_names(pair) -> None:
    a, b = pair
    r = router([kp("alpha", a), kp("beta", b)])
    for args in ({"project": "nope"}, {"project": "/no/such/dir"}):
        with pytest.raises(ToolError) as ei:
            r.call("model_get", args)
        assert "unknown project" in str(ei.value) and "alpha, beta" in str(ei.value)
    with pytest.raises(ToolError, match="must be a string"):
        r.call("model_get", {"project": 3})
    with pytest.raises(ToolError, match="known projects: alpha, beta"):
        r.call("project_set_active", {"name": "nope"})
    with pytest.raises(KeyError):
        r.call("no_such_tool", {})


def test_tool_errors_name_the_project_when_several_are_known(pair) -> None:
    a, b = pair
    r = router([kp("alpha", a), kp("beta", b)])
    with pytest.raises(ToolError, match=r"no rule 'R999'.*\(project: beta\)"):
        r.call("rule_get", {"id": "R999", "project": "beta"})


def test_project_set_active_persists_for_the_process(pair) -> None:
    a, b = pair
    r = router([kp("alpha", a), kp("beta", b)])
    out = r.call("project_set_active", {"name": "beta"})
    assert out["active"] == "beta" and out["previous"] == "alpha" and out["spec"] == "present"
    for _ in range(2):
        assert r.call("model_get", {})["rules"][0]["text"].startswith("Beta only")
    assert r.call("rule_get", {"id": "R1", "project": "alpha"})["text"].startswith("Alpha")
    assert r.call("model_get", {})["project"] == "beta", "a project argument changes nothing"
    assert r.call("project_set_active", {"path": str(a)})["active"] == "alpha"
    assert r.call("model_get", {})["project"] == "alpha"
    lst = r.call("project_list", {})
    assert lst["active"] == "alpha" and [p["active"] for p in lst["projects"]] == [True, False]
    with pytest.raises(ToolError, match="exactly one"):
        r.call("project_set_active", {})
    with pytest.raises(ToolError, match="exactly one"):
        r.call("project_set_active", {"name": "alpha", "path": str(a)})


def test_project_list_rows(pair, tmp_path: Path) -> None:
    a, b = pair
    (tmp_path / "plain").mkdir()
    r = router([kp("alpha", a), kp("plain", tmp_path / "plain", "launch")])
    lst = r.call("project_list", {})
    assert lst == {
        "active": "alpha",
        "projects": [
            {"name": "alpha", "path": str(a.resolve()), "spec": "present", "active": True,
             "source": "explicit"},
            {"name": "plain", "path": str((tmp_path / "plain").resolve()), "spec": "missing",
             "active": False, "source": "launch"},
        ],
    }  # fmt: skip


# ---------------------------------------------------------------- the workspace


@pytest.fixture
def shop(pair, tmp_path: Path) -> tuple[ProjectRouter, Path, Path]:
    a, b = pair
    lib = make_project(tmp_path / "lib", repo=False)
    m = tmp_path / "lattice.workspace.toml"
    m.write_text(
        '[workspace]\nname = "shop"\n'
        '[[project]]\nname = "alpha"\npath = "alpha"\nrole = "provider"\n'
        '[[project]]\nname = "beta"\npath = "beta"\nrole = "consumer"\n'
        '[[project]]\nname = "lib"\npath = "lib"\nrole = "library"\n'
        '[[link]]\nfrom = "beta"\nto = "alpha"\nkind = "uses"\nnote = "GET /notes"\n'
        '[[link]]\nfrom = "alpha"\nto = "lib"\nkind = "depends_on"\n'
    )
    w = ws.load(m)
    projects = [kp("alpha", a, "workspace", "provider"), kp("beta", b, "workspace", "consumer"),
                kp("lib", lib, "workspace", "library")]  # fmt: skip
    return router(projects, workspace=w, workspace_path=m), a, lib


def test_project_list_carries_roles_and_links(shop) -> None:
    r, _a, _lib = shop
    lst = r.call("project_list", {})
    rows = {p["name"]: p for p in lst["projects"]}
    assert rows["alpha"]["role"] == "provider" and rows["lib"]["role"] == "library"
    assert rows["alpha"]["links"] == [
        {"from": "beta", "to": "alpha", "kind": "uses", "note": "GET /notes"},
        {"from": "alpha", "to": "lib", "kind": "depends_on", "note": ""},
    ]
    assert lst["workspace"]["name"] == "shop" and lst["workspace"]["problems"] == []


def test_workspace_impact_is_transitive_and_listed_only_with_a_workspace(shop, pair) -> None:
    r, _a, lib = shop
    assert "workspace_impact" in r.names()
    out = r.call("workspace_impact", {"project": "lib"})
    assert out["impacted"] == ["alpha", "beta"] and out["project"] == "lib"
    assert {(v["from"], v["to"]) for v in out["via"]} == {("alpha", "lib"), ("beta", "alpha")}
    assert r.call("workspace_impact", {"project": "beta"})["impacted"] == []
    assert r.call("workspace_impact", {})["impacted"] == ["beta"], "default: the active (alpha)"
    assert r.call("workspace_impact", {"project": str(lib)})["project"] == "lib"
    a, b = pair
    plain = router([kp("alpha", a), kp("beta", b)])
    assert "workspace_impact" not in plain.names()
    with pytest.raises(KeyError):
        plain.call("workspace_impact", {})


def test_readonly_lists_the_project_tools_but_no_writes(shop) -> None:
    r, a, lib = shop
    ro = ProjectRouter(list(r.projects.values()), local, readonly=True, workspace=r.workspace)
    names = ro.names()
    assert {*PROJECT_TOOLS, "workspace_impact"} <= set(names)
    assert "change_propose" not in names and "check_run" not in names
    with pytest.raises(KeyError):
        ro.call("check_run", {"project": "lib"})


def test_with_several_projects_every_schema_advertises_project(pair) -> None:
    a, b = pair
    r = router([kp("alpha", a), kp("beta", b)])
    for d in r.list_tools():
        if d["name"] not in PROJECT_TOOLS:
            assert d["inputSchema"]["properties"]["project"]["type"] == "string", d["name"]
    assert r.instructions(INSTRUCTIONS).startswith(INSTRUCTIONS)
    assert "project_list" in r.instructions(INSTRUCTIONS)


# ---------------------------------------------------------------- single-project compatibility


def test_a_single_project_router_is_the_old_toolbox(pair) -> None:
    a, _b = pair
    old = Toolbox(PendingBackend(a))
    r = ProjectRouter([kp("alpha", a)], lambda p: PendingBackend(p.root))
    assert r.list_tools()[: len(old.list_tools())] == old.list_tools()
    assert [d["name"] for d in r.list_tools()[len(old.list_tools()) :]] == PROJECT_TOOLS
    dump = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    for name, args in (
        ("model_get", {}),
        ("project_status", {}),
        ("model_get", {"section": "rules"}),
        ("rule_get", {"id": "R3"}),
        ("change_list", {}),
        ("spec_lint", {}),
    ):
        assert dump(r.call(name, args)) == dump(old.call(name, args)), name
    assert "project" not in r.call("rule_get", {"id": "R3"})
    with pytest.raises(ToolError) as e_new:
        r.call("rule_get", {"id": "R999"})
    with pytest.raises(ToolError) as e_old:
        old.call("rule_get", {"id": "R999"})
    assert str(e_new.value) == str(e_old.value)
    assert r.instructions(INSTRUCTIONS) == INSTRUCTIONS
    # the argument is accepted (and checked) even when not advertised
    assert r.call("model_get", {"project": "alpha"}) == old.call("model_get", {})
    with pytest.raises(ToolError, match="known projects: alpha"):
        r.call("model_get", {"project": "beta"})


def test_initialize_keeps_the_instructions_for_one_project(pair) -> None:
    a, b = pair
    one = McpServer(ProjectRouter([kp("alpha", a)], lambda p: PendingBackend(p.root)))
    two = McpServer(router([kp("alpha", a), kp("beta", b)]))
    i1 = one.handle(INIT)["result"]["instructions"]
    i2 = two.handle(INIT)["result"]["instructions"]
    assert i1 == INSTRUCTIONS and i2 == INSTRUCTIONS + i2[len(INSTRUCTIONS) :] and i2 != i1


# ---------------------------------------------------------------- over the real stdio protocol


def serve(cwd: Path, msgs: list[Any], *args: str, env: dict[str, str] | None = None) -> list[dict]:
    e = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    e.pop(ws.ENV, None)
    e["LATTICE_PROJECTS_REGISTRY"] = str(cwd / "no-registry.toml")
    e.update(env or {})
    r = subprocess.run(
        [sys.executable, "-m", "lattice.mcp", "serve", *args],
        input="\n".join(json.dumps(m) for m in msgs) + "\n",
        capture_output=True, text=True, timeout=120, check=False, cwd=cwd, env=e,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]


def test_one_server_with_no_project_and_a_workspace(pair, tmp_path: Path) -> None:
    launch = tmp_path / "launch"
    launch.mkdir()
    (launch / "lattice.workspace.toml").write_text(
        '[[project]]\nname = "a"\npath = "../alpha"\n[[project]]\nname = "b"\npath = "../beta"\n'
        '[[link]]\nfrom = "b"\nto = "a"\nkind = "uses"\n'
    )
    out = serve(launch, [
        INIT,
        req(1, "tools/list"),
        call(2, "project_list"),
        call(3, "model_get", project="a"),
        call(4, "model_get", project="b"),
        req(5, "tools/call", {"name": "project_set_active", "arguments": {"name": "b"}}),
        call(6, "rule_get", id="R1"),
        call(7, "model_get", project="nope"),
        call(8, "workspace_impact", project="a"),
        call(9, "project_status"),
    ])  # fmt: skip
    by = {o["id"]: o for o in out}
    names = [t["name"] for t in by[1]["result"]["tools"]]
    assert {*PROJECT_TOOLS, "workspace_impact", "model_get"} <= set(names)
    lst = payload(by[2])
    assert [(p["name"], p["source"], p["spec"]) for p in lst["projects"]] == [
        ("launch", "launch", "missing"),
        ("a", "workspace", "present"),
        ("b", "workspace", "present"),
    ]  # fmt: skip
    assert payload(by[3])["rules"][0]["text"].startswith("Alpha only")
    assert payload(by[4])["rules"][0]["text"].startswith("Beta only")
    assert payload(by[5])["active"] == "b"
    assert payload(by[6])["text"].startswith("Beta only") and payload(by[6])["project"] == "b"
    assert by[7]["result"]["isError"] is True
    assert "known projects: launch, a, b" in by[7]["result"]["content"][0]["text"]
    assert payload(by[8])["impacted"] == ["b"]
    assert payload(by[9])["project"] == "b", "the active project, set two calls before"


def test_repeated_project_flags_and_a_relative_project_path(pair, tmp_path: Path) -> None:
    a, b = pair
    msgs = [
        INIT,
        call(1, "project_list"),
        call(2, "model_get", project="beta"),
        req(3, "tools/list"),
    ]
    out = serve(tmp_path, msgs, "--project", "alpha", "--project", str(b))
    lst = payload(out[1])
    assert [(p["name"], p["source"]) for p in lst["projects"]] == [
        ("alpha", "explicit"),
        ("beta", "explicit"),
    ]
    assert payload(out[2])["rules"][0]["text"].startswith("Beta only")
    assert "workspace_impact" not in [t["name"] for t in out[3]["result"]["tools"]]


def test_a_single_project_server_lists_the_old_tools_first(pair, tmp_path: Path) -> None:
    a, _b = pair
    out = serve(tmp_path, [INIT, req(1, "tools/list"), call(2, "model_get")], "--project", str(a))
    old = Toolbox(PendingBackend(a)).list_tools()
    tools = out[1]["result"]["tools"]
    dump = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    assert dump(tools[: len(old)]) == dump(old)
    assert [t["name"] for t in tools[len(old) :]] == PROJECT_TOOLS
    assert out[0]["result"]["instructions"] == INSTRUCTIONS
    model = payload(out[2])
    assert model["project"] == Toolbox(PendingBackend(a)).call("model_get", {})["project"]
    assert "project_info" not in model


def test_a_bad_workspace_does_not_stop_the_server(pair, tmp_path: Path) -> None:
    a, _b = pair
    (tmp_path / "lattice.workspace.toml").write_text("[[[")
    out = serve(tmp_path, [INIT, call(1, "project_list")], "--project", str(a))
    lst = payload(out[1])
    assert lst["workspace"]["name"] is None
    assert [p["code"] for p in lst["workspace"]["problems"]] == ["unreadable"]
