"""The MCP tool ``change_impact`` (Spike D, D6): registered only with a studio.toml ``[impact]``
table, read-only, a pure read by default, ``observe: true`` a subprocess that a ``--readonly``
server refuses, errors that start with their code."""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from lattice.mcp.tools import IMPACT_SECTIONS, Toolbox, ToolError, cap_result
from lattice.spec.implementation import sha256_bytes
from tests.mcp.test_implementation_tools import (
    BASE_TOOLS,
    IMPL_TOOLS,
    FakeBackend,
    make_project,
)

FIX = Path(__file__).resolve().parents[1] / "spec" / "fixtures" / "impact"
ROOT = Path(__file__).resolve().parents[2]

EDIT = """
from dataclasses import replace as _r

SPEC = _r(SPEC, scenarios=tuple(
    _r(s, steps=(_r(s.steps[0], expect_status=422),)) if s.name == 'create_project' else s
    for s in SPEC.scenarios))
"""


def with_impact(proj: Path, extra: str = "") -> None:
    toml = proj / "studio.toml"
    toml.write_text(toml.read_text() + "\n[impact]\n" + extra)


def candidate(proj: Path, name: str = "cand") -> Path:
    """A candidate directory inside the project: the fixture spec with one expectation edited."""
    d = proj / name
    d.mkdir()
    shutil.copy(proj / "spec.py", d / "spec.py")
    (d / "spec.py").write_text((proj / "spec.py").read_text() + EDIT)
    shutil.copy(proj / "spec.trace.json", d / "spec.trace.json")
    shutil.copy(proj / "REQUIREMENTS.md", d / "REQUIREMENTS.md")
    return d


def tree(*roots: Path) -> dict[str, str]:
    out = {}
    for root in roots:
        for p in sorted(root.rglob("*")):
            out[str(p)] = "dir" if p.is_dir() else sha256_bytes(p.read_bytes())
    return out


@pytest.fixture
def world(tmp_path: Path) -> tuple[Toolbox, Path, Path, Path]:
    proj, app, inv = make_project(tmp_path)
    with_impact(proj)
    candidate(proj)
    return Toolbox(FakeBackend(proj)), proj, app, inv


# ---- registration ----------------------------------------------------------------------------


def test_the_tool_exists_only_with_the_impact_table(tmp_path: Path) -> None:
    both, _, _ = make_project(tmp_path / "both")
    with_impact(both)
    only_impl, _, _ = make_project(tmp_path / "impl")
    only_impact, _, _ = make_project(tmp_path / "impact", implementation=False)
    with_impact(only_impact)
    plain, _, _ = make_project(tmp_path / "plain", implementation=False)
    assert Toolbox(FakeBackend(plain)).names() == BASE_TOOLS
    assert Toolbox(FakeBackend(only_impl)).names() == BASE_TOOLS + IMPL_TOOLS
    assert Toolbox(FakeBackend(both)).names() == [*BASE_TOOLS, *IMPL_TOOLS, "change_impact"]
    assert Toolbox(FakeBackend(only_impact)).names() == [*BASE_TOOLS, "change_impact"]
    ro = Toolbox(FakeBackend(both), readonly=True)
    assert "change_impact" in ro.names()  # read-only: also listed under --readonly


def test_without_the_table_tools_list_is_byte_identical(tmp_path: Path) -> None:
    """IX12: a project with [impact] differs from one without by exactly one tool."""
    on, _, _ = make_project(tmp_path / "on")
    off, _, _ = make_project(tmp_path / "off")
    with_impact(on)
    a = Toolbox(FakeBackend(off)).list_tools()
    b = Toolbox(FakeBackend(on)).list_tools()
    assert b[:-1] == a and b[-1]["name"] == "change_impact" and len(b) == len(a) + 1


def test_the_tool_is_read_only_and_closed(world) -> None:
    tb = world[0]
    d = next(x for x in tb.list_tools() if x["name"] == "change_impact")
    assert d["annotations"]["readOnlyHint"] is True and d["annotations"]["openWorldHint"] is False
    assert d["inputSchema"]["additionalProperties"] is False
    assert set(d["inputSchema"]["properties"]) == {
        "change",
        "candidate",
        "observe",
        "section",
        "limit",
    }
    assert d["inputSchema"]["properties"]["section"]["enum"] == list(IMPACT_SECTIONS)
    assert tb._tools["change_impact"]["writes"] is False


# ---- the default: a pure read of tiers 2 and 3 --------------------------------------------------


def test_the_summary_is_under_4k_and_names_the_tiers(world) -> None:
    tb = world[0]
    out = tb.call("change_impact", {"candidate": "cand"})
    assert len(json.dumps(out)) < 4000 and out["section"] == "summary"
    assert out["schema"] == "lattice.impact/1"
    assert out["tiers"]["1"] == "not_run:not_requested" and out["tiers"]["4"] == "not_run:spike_e"
    assert out["tiers"]["2"] == "run" and out["tiers"]["3"] == "run"
    assert out["summary"]["3"]["locations"] > 0
    assert "not decidable before the change exists" in out["note"]
    assert out["files"], "the files the change touches"
    assert len(cap_result(out)[0]) < 4200


def test_sections_page_and_limit(world) -> None:
    tb = world[0]
    t3 = tb.call("change_impact", {"candidate": "cand", "section": "tier3", "limit": 5})
    assert t3["returned"] == 5 and t3["total"] > 5
    assert all(i["tier"] == 3 and i["file"] and i["line"] for i in t3["items"])
    for s in IMPACT_SECTIONS:
        out = tb.call("change_impact", {"candidate": "cand", "section": s})
        assert out["section"] == s and out["schema"] == "lattice.impact/1"
    flows = tb.call("change_impact", {"candidate": "cand", "section": "flows"})
    assert flows["items"] and all(f["kind"] == "may_fail" for f in flows["items"])
    dec = tb.call("change_impact", {"candidate": "cand", "section": "decidability"})
    assert dec["total"] >= 1 and all("state" in i for i in dec["items"])
    unread = tb.call("change_impact", {"candidate": "cand", "section": "unread"})
    assert "items" in unread


def test_the_answer_is_deterministic(world) -> None:
    tb = world[0]
    a = tb.call("change_impact", {"candidate": "cand", "section": "tier3"})
    for _ in range(3):
        assert tb.call("change_impact", {"candidate": "cand", "section": "tier3"}) == a


def test_a_change_is_read_from_the_state_directory(world) -> None:
    tb, proj, _, _ = world
    base_md = (proj / "REQUIREMENTS.md").read_text()
    module, trace = (proj / "spec.py").read_text(), (proj / "spec.trace.json").read_text()
    new_module = (proj / "cand" / "spec.py").read_text()
    with_state = proj / "studio.toml"
    with_state.write_text(with_state.read_text() + '\n[state]\ndir = "st"\n')
    (proj / "st" / "changes").mkdir(parents=True)

    def put(status: str) -> None:
        doc = {
            "schema": 1,
            "kind": "changes",
            "id": "c1",
            "record": {"id": "c1", "status": status},
            "private": {
                "base": {"markdown": base_md, "module": module, "trace": trace},
                "new_md": base_md,
                "new_module": new_module,
                "new_trace": trace,
            },
        }
        (proj / "st" / "changes" / "c1.json").write_text(json.dumps(doc))

    tb = Toolbox(FakeBackend(proj))
    put("drafting")
    with pytest.raises(ToolError, match=r"^change_not_ready:"):
        tb.call("change_impact", {"change": "c1"})
    put("ready")
    before = tree(proj)
    by_change = tb.call("change_impact", {"change": "c1", "section": "tier3"})
    by_dir = tb.call("change_impact", {"candidate": "cand", "section": "tier3"})
    assert by_change["items"] == by_dir["items"] and by_change["total"] > 0
    assert tree(proj) == before, "nothing is written, not even a meta.json"
    assert not (proj / "st" / "meta.json").exists()
    with pytest.raises(ToolError, match=r"^no_candidate:"):
        tb.call("change_impact", {"change": "c9"})


# ---- errors start with their code ---------------------------------------------------------------


def test_bad_arguments_and_errors_start_with_their_code(world, tmp_path: Path) -> None:
    tb, proj, _, inv = world
    with pytest.raises(ToolError, match=r"^no_candidate:"):
        tb.call("change_impact", {})
    with pytest.raises(ToolError, match=r"^no_candidate:"):
        tb.call("change_impact", {"change": "c1", "candidate": "cand"})
    with pytest.raises(ToolError, match=r"^no_candidate:"):
        tb.call("change_impact", {"candidate": ".."})
    with pytest.raises(ToolError, match=r"^no_candidate:"):
        tb.call("change_impact", {"candidate": "nowhere"})
    with pytest.raises(ToolError, match=r"^bad_section:"):
        tb.call("change_impact", {"candidate": "cand", "section": "nope"})
    for lim in (0, 201, "5", True):
        with pytest.raises(ToolError, match=r"^bad_limit:"):
            tb.call("change_impact", {"candidate": "cand", "limit": lim})
    with pytest.raises(ToolError, match=r"^bad_observe:"):
        tb.call("change_impact", {"candidate": "cand", "observe": "yes"})
    with pytest.raises(ToolError, match="unknown argument"):
        tb.call("change_impact", {"candidate": "cand", "bogus": 1})
    shutil.rmtree(inv)
    with pytest.raises(ToolError, match=r"^inventory_missing:"):
        tb.call("change_impact", {"candidate": "cand"})


def test_a_project_with_no_inventory_says_inventory_missing(tmp_path: Path) -> None:
    proj, _, _ = make_project(tmp_path, implementation=False)
    with_impact(proj)
    candidate(proj)
    with pytest.raises(ToolError, match=r"^inventory_missing:"):
        Toolbox(FakeBackend(proj)).call("change_impact", {"candidate": "cand"})


# ---- observe ------------------------------------------------------------------------------------


def test_observe_is_refused_under_readonly_and_starts_nothing(world, monkeypatch) -> None:
    _, proj, _, _ = world
    import subprocess

    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("a process was started")

    monkeypatch.setattr(subprocess, "Popen", boom)
    ro = Toolbox(FakeBackend(proj), readonly=True)
    with pytest.raises(ToolError, match=r"^readonly:"):
        ro.call("change_impact", {"candidate": "cand", "observe": True})
    # readonly still answers the pure read
    assert ro.call("change_impact", {"candidate": "cand"})["tiers"]["2"] == "run"


def test_observe_without_an_app_says_no_app(world) -> None:
    with pytest.raises(ToolError, match=r"^no_app:"):
        world[0].call("change_impact", {"candidate": "cand", "observe": True})


HARNESS = """\
import tempfile
from pathlib import Path

from tests.spec import minishop

_dir, _pkg = minishop.write_app(Path(tempfile.mkdtemp()), "ok")
make_app = minishop.app_factory(_pkg)
build_db = minishop.build_db
login = minishop.login
"""


def shop_project(tmp_path: Path, slow: bool = False) -> Path:
    proj = tmp_path / "shop"
    shutil.copytree(FIX / "base", proj)
    cand = proj / "cand"
    shutil.copytree(FIX / "status_changed", cand)
    (proj / "harness.py").write_text(HARNESS + ("import time\ntime.sleep(60)\n" if slow else ""))
    (proj / "studio.toml").write_text(
        '[project]\nname = "shop"\n[app]\napp = "harness:make_app"\ndb = "harness:build_db"\n'
        'login = "harness:login"\n[check]\nlayers = "CD"\n[impact]\n'
    )
    return proj


def test_observe_runs_tier_one_in_a_subprocess(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    proj = shop_project(tmp_path)
    tb = Toolbox(FakeBackend(proj))
    assert "change_impact" in tb.names()
    before = tree(proj)
    sys_path = list(sys.path)
    out = tb.call("change_impact", {"candidate": "cand", "observe": True, "section": "tier1"})
    assert out["tiers"]["1"] == "run" and out["total"] == 1
    assert out["items"][0]["target"] == "scenario:ann cancels her pending order"
    assert out["items"][0]["label"] == "not_built_yet"
    assert sys.path == sys_path, "the harness's sys.path never touches the server"
    assert tree(proj) == before


def test_a_timeout_kills_the_group_and_removes_the_owners_containers(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    import lattice.mcp.tools as tools
    import lattice.studio.state as st

    removed: list[str] = []
    monkeypatch.setattr(
        st, "remove_owned_containers", lambda token, log=None: removed.append(token) or []
    )
    monkeypatch.setattr(tools, "IMPACT_OBSERVE_TIMEOUT", 3.0)
    proj = shop_project(tmp_path, slow=True)
    t0 = time.monotonic()
    with pytest.raises(ToolError, match=r"^timeout:"):
        Toolbox(FakeBackend(proj)).call("change_impact", {"candidate": "cand", "observe": True})
    assert time.monotonic() - t0 < 30
    assert len(removed) == 1 and removed[0].startswith("impact-")
