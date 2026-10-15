"""The MCP server's ``initialize`` instructions (lattice/mcp/projects.py,
``ProjectRouter.instructions``; API.md v1.20, F1 of docs/results/2026-10-14-agent-run-3-prereg.md):
one known project answers the base unchanged (with or without a workspace, also ``--readonly``);
several answer ``base + MULTI_NOTE``, then ``WORKSPACE_NOTE`` when a workspace is known, then
``READONLY_NOTE`` when ``--readonly``. The appended text names only tools that router lists."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lattice import workspace as ws
from lattice.mcp.projects import (
    MULTI_NOTE,
    READONLY_NOTE,
    WORKSPACE_NOTE,
    KnownProject,
    ProjectRouter,
)
from lattice.mcp.server import INSTRUCTIONS
from lattice.mcp.tools import PendingBackend
from tests.contracts.helpers import World
from tests.studio.contract_world import make_world

WORKSPACE_NOTE_TEXT = (
    " A workspace manifest links these projects; project_list shows each one's role and links. "
    'For "has anything a provider changed affected this project?": call project_list, then BOTH '
    "contract_check and linked_question_list for the consumer: contract_check shows what changed "
    "since its pin (BROKEN, CHANGED), linked_question_list the notes a provider left when an "
    "accepted change broke it (open, resolved or dismissed). Both take `consumer` and default to "
    "the active project when it is a consumer. workspace_impact lists who relies on a project; "
    "contract_impact previews a provider's candidate version against its consumers. These five "
    "tools are read-only."
)
READONLY_NOTE_TEXT = (
    " This server is read-only: it has no tool that proposes a spec change or runs the checks; "
    "tell the owner what to change."
)
MULTI_NOTE_TEXT = (
    " Several projects are known: project_list lists them; every tool takes an optional "
    "`project` (a name or path from project_list; default: the active project, which "
    "project_set_active changes)."
)
FIVE = ("project_list", "contract_check", "linked_question_list", "workspace_impact",
        "contract_impact")  # fmt: skip
SNAKE = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")


@pytest.fixture()
def world(tmp_path: Path) -> World:
    return make_world(tmp_path / "w")


def router(
    w: World, *, projects: tuple[str, ...], workspace: bool, readonly: bool = False
) -> ProjectRouter:
    roots = {"prov": w.provider, "cons": w.consumer}
    known = [KnownProject(n, roots[n].resolve(), "workspace") for n in projects]
    loaded = ws.load(w.root / ws.MANIFEST) if workspace else None
    return ProjectRouter(
        known, lambda p: PendingBackend(p.root), readonly=readonly, workspace=loaded
    )


def test_the_note_constants_are_the_registered_text() -> None:
    assert WORKSPACE_NOTE == WORKSPACE_NOTE_TEXT
    assert READONLY_NOTE == READONLY_NOTE_TEXT
    assert MULTI_NOTE == MULTI_NOTE_TEXT


@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("readonly", [False, True])
def test_one_known_project_answers_the_base_unchanged(
    world: World, workspace: bool, readonly: bool
) -> None:
    r = router(world, projects=("cons",), workspace=workspace, readonly=readonly)
    assert r.instructions(INSTRUCTIONS) == INSTRUCTIONS


def test_several_projects_without_a_workspace(world: World) -> None:
    r = router(world, projects=("prov", "cons"), workspace=False)
    assert r.instructions(INSTRUCTIONS) == INSTRUCTIONS + MULTI_NOTE_TEXT
    ro = router(world, projects=("prov", "cons"), workspace=False, readonly=True)
    assert ro.instructions(INSTRUCTIONS) == INSTRUCTIONS + MULTI_NOTE_TEXT + READONLY_NOTE_TEXT


def test_several_projects_with_a_workspace(world: World) -> None:
    r = router(world, projects=("prov", "cons"), workspace=True)
    assert r.instructions(INSTRUCTIONS) == INSTRUCTIONS + MULTI_NOTE_TEXT + WORKSPACE_NOTE_TEXT


def test_several_projects_with_a_workspace_and_readonly(world: World) -> None:
    r = router(world, projects=("prov", "cons"), workspace=True, readonly=True)
    assert r.instructions(INSTRUCTIONS) == (
        INSTRUCTIONS + MULTI_NOTE_TEXT + WORKSPACE_NOTE_TEXT + READONLY_NOTE_TEXT
    )


@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("readonly", [False, True])
def test_the_appended_text_names_only_listed_tools(
    world: World, workspace: bool, readonly: bool
) -> None:
    r = router(world, projects=("prov", "cons"), workspace=workspace, readonly=readonly)
    appended = r.instructions(INSTRUCTIONS)[len(INSTRUCTIONS) :]
    listed = set(r.names())
    named = set(SNAKE.findall(appended))
    assert named <= listed, f"suggests a tool the server does not list: {named - listed}"
    if readonly:
        assert not {"change_propose", "check_run"} & named


def test_the_workspace_note_names_the_five_tools_and_they_are_listed(world: World) -> None:
    assert set(SNAKE.findall(WORKSPACE_NOTE)) == set(FIVE)
    for readonly in (False, True):
        r = router(world, projects=("prov", "cons"), workspace=True, readonly=readonly)
        assert set(FIVE) <= set(r.names())
