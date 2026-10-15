"""The plugin and the workspace (lattice/mcp/server.py ``serve``, plumbing D1): the plugin's
config is unchanged, and a server whose launch directory has no manifest anchors the walk-up at
its first ``--project``, using the manifest only when it loads and declares that project. Real
``python -m lattice.mcp serve`` processes; no LLM, no API key."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import lattice
from lattice.mcp.projects import MULTI_NOTE, WORKSPACE_NOTE
from lattice.mcp.server import INSTRUCTIONS
from tests.mcp.plumbing_support import INIT, clean_env, rpc, run_server, tool_call, tool_result
from tests.studio.test_server import make_project

CODE = Path(lattice.__file__).resolve().parents[1]
PLUGIN = CODE / "lattice-claude-plugin"
WS_TOOLS = ["workspace_impact", "contract_check", "contract_impact", "linked_question_list"]


def expand(arg: str, env: dict[str, str]) -> str:
    """The host's ``${V}`` / ``${V:-default}`` expansion."""
    return re.sub(
        r"\$\{(\w+)(?::-([^}]*))?\}", lambda m: env.get(m.group(1)) or (m.group(2) or ""), arg
    )


def manifest(root: Path, *names: tuple[str, str]) -> Path:
    body = '[workspace]\nname = "plumb"\n'
    for name, path in names:
        body += f'\n[[project]]\nname = "{name}"\npath = "{path}"\n'
    m = root / "lattice.workspace.toml"
    m.write_text(body)
    return m


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    make_project(root / "proj", repo=False)
    make_project(root / "other", repo=False)
    manifest(root, ("proj", "proj"), ("other", "other"))
    (tmp_path / "elsewhere").mkdir()
    return root


def list_projects(argv: list[str], cwd: Path, env: dict[str, str], via_cli: bool = False):
    reqs = [rpc(1, "initialize", INIT), rpc(2, "tools/list"), tool_call(3, "project_list")]
    lines, err = run_server(argv, reqs, cwd=cwd, env=env, via_cli=via_cli)
    init = json.loads(lines[0])["result"]["instructions"]
    names = [t["name"] for t in json.loads(lines[1])["result"]["tools"]]
    return init, names, tool_result(lines[2])[1], err


def test_the_plugin_config_still_passes_one_project_with_the_host_default() -> None:
    if not PLUGIN.is_dir():  # a scratch copy of lattice/, tests/ and dogfood/ only
        pytest.skip("lattice-claude-plugin/ is not in this tree")
    cfg = json.loads((PLUGIN / ".mcp.json").read_text())
    args = cfg["mcpServers"]["lattice"]["args"]
    assert cfg["mcpServers"]["lattice"]["command"] == "lattice"
    assert args == ["mcp", "serve", "--project", "${CLAUDE_PROJECT_DIR:-.}"]
    assert expand(args[3], {}) == "."
    assert expand(args[3], {"CLAUDE_PROJECT_DIR": "/p/q"}) == "/p/q"
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())
    cmd = hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert cmd == 'lattice mcp guard --project "$CLAUDE_PROJECT_DIR"'


def test_p3_the_plugin_shape_in_a_workspace_dir_is_a_workspace_server(ws: Path) -> None:
    init, names, rows, _ = list_projects(
        ["--project", "."], ws / "proj", clean_env(CODE), via_cli=True
    )
    assert init == INSTRUCTIONS + MULTI_NOTE + WORKSPACE_NOTE
    assert names[-4:] == WS_TOOLS
    src = {p["name"]: p["source"] for p in rows["projects"]}
    assert src == {"proj": "explicit", "other": "workspace"}


def test_p4_the_manifest_is_found_from_the_project_when_the_launch_dir_has_none(ws: Path) -> None:
    init, names, rows, err = list_projects(
        ["--project", str(ws / "proj")], ws.parent / "elsewhere", clean_env(CODE)
    )
    assert init == INSTRUCTIONS + MULTI_NOTE + WORKSPACE_NOTE
    assert names[-4:] == WS_TOOLS
    assert {p["name"]: p["source"] for p in rows["projects"]} == {
        "proj": "explicit", "other": "workspace",
    }  # fmt: skip
    assert rows["workspace"]["name"] == "plumb" and "not used" not in err


def test_p5_a_manifest_that_does_not_declare_the_project_is_not_used(ws: Path) -> None:
    manifest(ws, ("other", "other"))  # proj is no longer declared
    init, names, rows, err = list_projects(
        ["--project", str(ws / "proj")], ws.parent / "elsewhere", clean_env(CODE)
    )
    assert init == INSTRUCTIONS and not set(WS_TOOLS) & set(names)
    assert [p["name"] for p in rows["projects"]] == ["proj"]
    assert "not used" in err and "lattice.workspace.toml" in err


def test_p6_the_env_workspace_is_used_as_before(ws: Path) -> None:
    env = clean_env(CODE, LATTICE_WORKSPACE=str(ws))
    init, names, _rows, err = list_projects(
        ["--project", str(ws / "proj")], ws.parent / "elsewhere", env
    )
    assert names[-4:] == WS_TOOLS and "not used" not in err and init.endswith(WORKSPACE_NOTE)


def test_p7_an_unreadable_manifest_above_the_project_changes_nothing(ws: Path) -> None:
    (ws / "lattice.workspace.toml").write_text("[[[")
    init, names, rows, err = list_projects(
        ["--project", str(ws / "proj")], ws.parent / "elsewhere", clean_env(CODE)
    )
    assert init == INSTRUCTIONS and not set(WS_TOOLS) & set(names)
    assert "workspace" not in rows and "not used" in err


def test_a_manifest_found_from_the_launch_dir_wins_even_when_it_does_not_declare(
    ws: Path, tmp_path: Path
) -> None:
    near = tmp_path / "launch"
    near.mkdir()
    manifest(near, ("nobody", "x"))
    _init, _names, rows, _ = list_projects(["--project", str(ws / "proj")], near, clean_env(CODE))
    assert rows["workspace"]["path"] == str(near.resolve() / "lattice.workspace.toml")
    assert "other" not in [p["name"] for p in rows["projects"]], "the far manifest is not used"


def test_the_flag_wins_over_the_fallback(ws: Path, tmp_path: Path) -> None:
    other_ws = tmp_path / "ws2"
    shutil.copytree(ws / "other", other_ws / "solo")
    manifest(other_ws, ("solo", "solo"))
    _i, _n, rows, _ = list_projects(
        ["--project", str(ws / "proj"), "--workspace", str(other_ws)],
        ws.parent / "elsewhere",
        clean_env(CODE),
    )
    assert rows["workspace"]["path"] == str(other_ws.resolve() / "lattice.workspace.toml")


def test_no_project_flag_means_no_fallback(ws: Path) -> None:
    _i, names, rows, _ = list_projects([], ws.parent / "elsewhere", clean_env(CODE))
    assert "workspace" not in rows and not set(WS_TOOLS) & set(names)


def test_the_instructions_are_base_for_a_single_project(tmp_path: Path) -> None:
    p = make_project(tmp_path / "solo", repo=False)
    init, _names, _rows, err = list_projects(["--project", str(p)], tmp_path, clean_env(CODE))
    assert init == INSTRUCTIONS and err == ""


def test_the_server_never_reads_claude_project_dir(ws: Path) -> None:
    env = clean_env(CODE, CLAUDE_PROJECT_DIR=str(ws / "other"))
    _i, _n, rows, _ = list_projects(["--project", str(ws / "proj")], ws.parent / "elsewhere", env)
    assert rows["active"] == "proj"
