"""The guard also protects the gate: check.sh and studio.toml always, and the harness files the
project's studio.toml names (``[mcp] protected`` / ``[mcp] harness``). Run through the real hook
process (the helper of test_guard.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.mcp.test_guard import ev, run_guard


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / "tests").mkdir(parents=True)
    for n in ("REQUIREMENTS.md", "spec.py", "spec.trace.json", "app.go", "check.sh", "studio.toml"):
        (p / n).write_text("x\n")
    (p / "tests" / "spec_harness.py").write_text("x\n")
    (p / "tests" / "test_app.py").write_text("x\n")
    return p


def denied(proj: Path, event: dict[str, Any]) -> bool:
    r = run_guard(proj, event)
    assert r.returncode == 0, r.stderr
    return bool(r.stdout.strip()) and (
        json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    )


@pytest.mark.parametrize("name", ["check.sh", "studio.toml"])
@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_gate_files_are_denied_to_the_edit_tools(proj: Path, name: str, tool: str) -> None:
    assert denied(proj, ev(tool, proj, file_path=str(proj / name)))
    assert denied(proj, ev(tool, proj, file_path=name))


@pytest.mark.parametrize(
    "cmd",
    [
        "echo 'exit 0' > check.sh",
        "echo x >> studio.toml",
        "rm check.sh",
        "rm -f studio.toml",
        "mv check.sh check.sh.old",
        "cp /tmp/evil.sh check.sh",
        "sed -i s/exit/true/ check.sh",
        "tee studio.toml",
        "git checkout -- check.sh",
        "python3 -c \"open('check.sh','w').write('exit 0')\"",
    ],
)
def test_gate_files_are_denied_to_bash_writes(proj: Path, cmd: str) -> None:
    assert denied(proj, ev("Bash", proj, command=cmd)), cmd


@pytest.mark.parametrize(
    "cmd",
    [
        "./check.sh",
        "bash check.sh",
        "cat check.sh",
        "cat studio.toml",
        "grep -n mcp studio.toml",
        "cp check.sh /tmp/check.sh.bak",
        "chmod +x check.sh",
        "git diff -- check.sh",
        "echo x > app.go",
        "rm -f app.go",
    ],
)
def test_reads_runs_and_code_edits_stay_allowed(proj: Path, cmd: str) -> None:
    assert not denied(proj, ev("Bash", proj, command=cmd)), cmd


def test_reading_the_gate_files_is_allowed(proj: Path) -> None:
    assert not denied(proj, ev("Read", proj, file_path=str(proj / "check.sh")))
    assert not denied(proj, ev("Edit", proj, file_path=str(proj / "app.go")))


def test_the_harness_module_named_in_studio_toml_is_protected(proj: Path) -> None:
    (proj / "studio.toml").write_text('[mcp]\nharness = ["tests/spec_harness.py"]\n')
    assert denied(proj, ev("Write", proj, file_path=str(proj / "tests" / "spec_harness.py")))
    assert denied(proj, ev("Bash", proj, command="echo x > tests/spec_harness.py"))
    assert not denied(proj, ev("Write", proj, file_path=str(proj / "tests" / "test_app.py")))


def test_a_directory_named_as_harness_protects_everything_under_it(proj: Path) -> None:
    (proj / "studio.toml").write_text('[mcp]\nharness = ["tests"]\n')
    assert denied(proj, ev("Write", proj, file_path=str(proj / "tests" / "test_app.py")))
    assert denied(proj, ev("Bash", proj, command="rm -rf tests"))


def test_mcp_protected_still_extends(proj: Path) -> None:
    (proj / "studio.toml").write_text(
        '[mcp]\nprotected = ["CONTEXT.md", "tests/spec_harness.py"]\n'
    )
    assert denied(proj, ev("Write", proj, file_path=str(proj / "CONTEXT.md")))
    assert denied(proj, ev("Write", proj, file_path=str(proj / "tests" / "spec_harness.py")))


def test_a_malformed_studio_toml_keeps_check_sh_protected(proj: Path) -> None:
    (proj / "studio.toml").write_text("[mcp\nprotected = nope")
    assert denied(proj, ev("Write", proj, file_path=str(proj / "check.sh")))
    assert denied(proj, ev("Write", proj, file_path=str(proj / "studio.toml")))


def test_non_string_extras_are_ignored(proj: Path) -> None:
    (proj / "studio.toml").write_text('[mcp]\nharness = [1, "tests/spec_harness.py", {a = 1}]\n')
    assert denied(proj, ev("Write", proj, file_path=str(proj / "tests" / "spec_harness.py")))
    (proj / "studio.toml").write_text('[mcp]\nharness = "tests"\n')
    assert not denied(proj, ev("Write", proj, file_path=str(proj / "tests" / "test_app.py")))


def test_inert_without_a_drafted_spec(tmp_path: Path) -> None:
    p = tmp_path / "plain"
    p.mkdir()
    (p / "check.sh").write_text("x\n")
    (p / "studio.toml").write_text("x\n")
    assert not denied(p, ev("Write", p, file_path=str(p / "check.sh")))
    assert not denied(p, ev("Bash", p, command="rm check.sh studio.toml"))


def test_fails_open_on_unreadable_input(proj: Path) -> None:
    r = run_guard(proj, "{not json")
    assert r.returncode == 1 and r.stdout.strip() == ""
