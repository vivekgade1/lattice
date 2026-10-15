"""Chat sessions and the multi-project tools (lattice/studio/session.py, API.md v1.20, F2 of
docs/results/2026-10-14-agent-run-3-prereg.md): ask, explain and change allow the five read-only
multi-project tools, no mode allows ``project_set_active`` or a write tool of the workspace,
rebuild's list is unchanged, and the system prompt says so when the spec files are missing."""

from __future__ import annotations

from pathlib import Path

import pytest

from lattice.studio.rebuild import allowed_tools, build_rebuild_argv
from lattice.studio.session import _BASE_RULES, MODE_PROFILES, system_prompt

MULTI = [
    f"mcp__lattice__{n}"
    for n in (
        "project_list",
        "workspace_impact",
        "contract_check",
        "contract_impact",
        "linked_question_list",
    )
]
MODEL = [
    f"mcp__lattice__{n}"
    for n in ("model_get", "rule_get", "spec_ask", "code_links", "graph_get", "spec_lint")
]
SENTENCE = (
    "This project has no Lattice spec yet (missing: {names}): the spec files named above do not "
    "exist; say so instead of answering from the spec."
)


@pytest.mark.parametrize("mode", ["ask", "explain", "change"])
def test_the_five_multi_project_tools_are_allowed(mode: str) -> None:
    allowed = MODE_PROFILES[mode].allowed
    assert set(MULTI) <= set(allowed)
    assert not {"mcp__lattice__project_set_active", "mcp__lattice__check_run"} & set(allowed)
    assert ("mcp__lattice__change_propose" in allowed) == (mode == "change")


@pytest.mark.parametrize("mode", ["ask", "explain"])
def test_the_read_modes_have_no_write_tool(mode: str) -> None:
    allowed = set(MODE_PROFILES[mode].allowed)
    assert not {f"mcp__lattice__{n}" for n in ("change_propose", "check_run")} & allowed
    assert not {"Edit", "Write", "Bash"} & allowed


def test_change_is_ask_plus_the_change_tools() -> None:
    ask, change = MODE_PROFILES["ask"].allowed, MODE_PROFILES["change"].allowed
    extra = [t for t in change if t not in ask]
    assert extra == [f"mcp__lattice__{n}" for n in ("change_propose", "change_get", "change_list")]


def test_rebuild_is_disabled_and_its_agent_list_is_unchanged() -> None:
    assert MODE_PROFILES["rebuild"].enabled is False
    assert not set(MULTI) & set(MODE_PROFILES["rebuild"].allowed)
    assert allowed_tools(["make"]) == [
        "Read",
        "Grep",
        "Glob",
        "Edit",
        "Write",
        "Bash(make:*)",
        *MODEL,
    ]
    argv = build_rebuild_argv(
        claude_bin="claude",
        text="t",
        model="m",
        mcp_config=Path("m.json"),
        settings=Path("s.json"),
        gates=["make"],
    )
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    assert allowed == ["Read", "Grep", "Glob", "Edit", "Write", "Bash(make:*)", *MODEL]


def test_base_rules_are_untouched() -> None:
    assert _BASE_RULES.startswith(
        "You are the chat of Lattice Studio, working on one Lattice project"
    )
    assert _BASE_RULES.endswith("you have no tools for it. Be brief.")
    assert "multi" not in _BASE_RULES and "workspace" not in _BASE_RULES


def spec_dir(root: Path, *names: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for n in names:
        (root / n).write_text("x")
    return root


def test_the_sentence_is_added_only_when_a_spec_file_is_missing(tmp_path: Path) -> None:
    full = spec_dir(tmp_path / "full", "REQUIREMENTS.md", "spec.py", "spec.trace.json")
    for mode in ("ask", "explain", "change"):
        assert "no Lattice spec yet" not in system_prompt(mode, full)
    bare = spec_dir(tmp_path / "bare")
    text = system_prompt("ask", bare, ["rule:R1"])
    names = "REQUIREMENTS.md, spec.py, spec.trace.json"
    assert SENTENCE.format(names=names) in text
    assert text.startswith(_BASE_RULES)
    part = spec_dir(tmp_path / "part", "REQUIREMENTS.md")
    assert SENTENCE.format(names="spec.py, spec.trace.json") in system_prompt("change", part)


def test_with_every_spec_file_the_prompt_is_the_old_one(tmp_path: Path) -> None:
    full = spec_dir(tmp_path / "full", "REQUIREMENTS.md", "spec.py", "spec.trace.json")
    (full / "STUDIO_RULES.md").write_text("Be kind.")
    text = system_prompt("ask", full, ["rule:R1"], "c3")
    parts = text.split("\n\n")
    assert parts[0] == _BASE_RULES
    assert parts[1] == MODE_PROFILES["ask"].prompt
    assert parts[-1].startswith("The owner has selected these targets")
    assert "Project rules:\nBe kind." in text and "The open change is c3." in text
