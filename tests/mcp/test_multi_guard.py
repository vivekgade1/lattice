"""The guard over many projects (lattice/mcp/guard.py ``MultiGuard``, multi-project layer 1): the
owning project of each path is the nearest directory holding spec.trace.json; explicit
``--project``s keep their protection; symlinks, ``../`` and the shell forms still count."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice.mcp.guard import Guard, MultiGuard, owner_of

SPEC = ("REQUIREMENTS.md", "spec.py", "spec.trace.json")


def project(d: Path, name: str | None = None, protected: tuple[str, ...] = ()) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for n in (*SPEC, "app.go", "notes.md"):
        (d / n).write_text("x")
    toml = ""
    if name:
        toml += f'[project]\nname = "{name}"\n'
    if protected:
        toml += "[mcp]\nprotected = [" + ", ".join(f'"{p}"' for p in protected) + "]\n"
    if toml:
        (d / "studio.toml").write_text(toml)
    return d


@pytest.fixture
def two(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Two projects (`a`, `b`, b named "billing" with CONTEXT.md protected) and a plain dir."""
    a = project(tmp_path / "a")
    b = project(tmp_path / "b", name="billing", protected=("CONTEXT.md",))
    (b / "CONTEXT.md").write_text("ctx")
    plain = tmp_path / "plain"
    plain.mkdir()
    return a, b, plain


def ev(tool: str, cwd: Path, **ti: Any) -> dict[str, Any]:
    return {"tool_name": tool, "tool_input": ti, "cwd": str(cwd), "hook_event_name": "PreToolUse"}


def decide(g: MultiGuard, e: dict[str, Any]) -> tuple[str | None, Path | None]:
    hit = g.decide(e)
    return hit, (g.last_project if hit else None)


def test_owner_is_the_nearest_trace_and_stops_at_a_git_boundary(two, tmp_path: Path) -> None:
    a, b, plain = two
    assert owner_of(str(a / "spec.py")) == a
    assert owner_of(str(a / "deep" / "new" / "file.py")) == a, "a path that does not exist yet"
    assert owner_of(str(a)) == a, "a project directory owns itself"
    assert owner_of(str(plain / "spec.py")) is None
    nested = project(a / "inner")
    assert owner_of(str(nested / "spec.py")) == nested
    # a repository below a trace: the walk stops at its .git
    repo = a / "vendor" / "lib"
    (repo / ".git").mkdir(parents=True)
    (repo / "pkg").mkdir()
    assert owner_of(str(repo / "pkg" / "spec.py")) is None
    # a repository root that is itself a project owns its files
    (b / ".git").mkdir()
    assert owner_of(str(b / "spec.trace.json")) == b


@pytest.mark.parametrize("explicit", ["none", "first", "both"])
def test_each_project_protects_its_own_spec(two, explicit: str) -> None:
    a, b, plain = two
    g = MultiGuard({"none": [], "first": [a], "both": [a, b]}[explicit])
    for proj in (a, b):
        for n in SPEC:
            assert decide(g, ev("Edit", plain, file_path=str(proj / n))) == (n, proj)
    assert decide(g, ev("Write", plain, file_path=str(b / "CONTEXT.md"))) == ("CONTEXT.md", b)
    assert g.decide(ev("Edit", a, file_path=str(a / "app.go"))) is None
    assert g.decide(ev("Write", b, file_path=str(b / "notes.md"))) is None
    assert g.decide(ev("Write", plain, file_path=str(plain / "spec.py"))) is None
    assert g.decide(ev("Write", plain, file_path="spec.py")) is None


def test_relative_and_dotdot_paths_resolve_to_the_owner(two) -> None:
    a, b, plain = two
    g = MultiGuard([a])
    assert decide(g, ev("Edit", a, file_path="../b/spec.py")) == ("spec.py", b)
    assert decide(g, ev("Edit", plain, file_path=str(a / "x" / ".." / "spec.trace.json")))[0]


def test_symlink_aliases_resolve_to_the_real_owner(two, tmp_path: Path) -> None:
    a, b, plain = two
    alias = a / "alias.py"  # inside project a, pointing at b's spec
    alias.symlink_to(b / "spec.py")
    g = MultiGuard([a])
    assert decide(g, ev("Write", a, file_path=str(alias))) == ("spec.py", b)
    d = plain / "dalias"
    d.symlink_to(b, target_is_directory=True)
    assert decide(g, ev("Edit", plain, file_path=str(d / "spec.trace.json"))) == (
        "spec.trace.json", b,
    )  # fmt: skip
    assert decide(MultiGuard(), ev("Bash", plain, command=f"echo x > {alias}")) == ("spec.py", b)


BASH_DENY = [
    "echo x > spec.py",
    "echo x >> ../b/spec.trace.json",
    "sed -i s/a/b/ REQUIREMENTS.md",
    "cp /tmp/x spec.py",
    "mv spec.py /tmp/gone",
    "rm -rf ../b",
    "rm -rf .",
    "git checkout -- spec.py",
    "git reset --hard",
    "git stash",
    "python3 -c \"open('spec.py','w').write('x')\"",
    "cd ../b && tee spec.py < /dev/null",
]
BASH_ALLOW = [
    "echo x > notes.md",
    "cat spec.py",
    "cp spec.py /tmp/backup.py",
    "git status",
    "python3 -c 'print(open(\"spec.py\").read())'",
]


@pytest.mark.parametrize("cmd", BASH_DENY)
def test_bash_forms_with_no_explicit_project(two, cmd: str) -> None:
    a, _b, _plain = two
    assert MultiGuard().decide(ev("Bash", a, command=cmd)) is not None, cmd


@pytest.mark.parametrize("cmd", BASH_ALLOW)
def test_bash_allowed_forms(two, cmd: str) -> None:
    a, _b, _plain = two
    assert MultiGuard().decide(ev("Bash", a, command=cmd)) is None, cmd


@pytest.mark.parametrize("cmd", ["echo x > spec.py", "git reset --hard", "git stash",
                                 "python3 -c \"open('spec.py','w').write('x')\""])  # fmt: skip
def test_commands_outside_every_project_are_allowed(two, cmd: str) -> None:
    _a, _b, plain = two
    assert MultiGuard().decide(ev("Bash", plain, command=cmd)) is None, cmd


def test_whole_tree_commands_name_the_cwd_project(two) -> None:
    a, b, _plain = two
    g = MultiGuard([a])
    assert decide(g, ev("Bash", b, command="git reset --hard")) == ("REQUIREMENTS.md", a), (
        "the explicit project first, as before"
    )
    g = MultiGuard()
    assert decide(g, ev("Bash", b, command="git reset --hard")) == ("REQUIREMENTS.md", b)
    assert decide(g, ev("Bash", b, command="python -c \"open('CONTEXT.md','w')\"")) == (
        "CONTEXT.md", b,
    )  # fmt: skip


def test_the_old_single_project_guard_is_unchanged(two) -> None:
    a, b, plain = two
    g = Guard(a)
    assert g.decide(ev("Edit", plain, file_path=str(b / "spec.py"))) is None
    assert g.decide(ev("Edit", plain, file_path=str(a / "spec.py"))) == "spec.py"


def test_an_explicit_project_without_a_spec_is_inert_but_owners_still_count(two, tmp_path) -> None:
    _a, b, plain = two
    (plain / "spec.py").write_text("somebody else's")
    g = MultiGuard([plain])
    assert g.static == []
    assert g.decide(ev("Edit", plain, file_path=str(plain / "spec.py"))) is None
    assert decide(g, ev("Edit", plain, file_path=str(b / "spec.py"))) == ("spec.py", b)


def hook(cwd: Path, event: dict[str, Any], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "lattice.mcp", "guard", *args],
        input=json.dumps(event), capture_output=True, text=True, check=False, cwd=cwd,
        env={k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")},
    )  # fmt: skip


def test_the_hook_names_the_project_in_its_reason(two) -> None:
    a, b, plain = two
    r = hook(plain, ev("Edit", plain, file_path=str(b / "spec.py")), "--project", str(a))
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    reason = out["permissionDecisionReason"]
    assert f"spec.py of project billing ({b.resolve()})" in reason
    assert f'project: "{b.resolve()}"' in reason and "change_propose" in reason
    r = hook(plain, ev("Edit", plain, file_path=str(a / "spec.trace.json")))  # no --project
    assert (
        f"of project a ({a.resolve()})"
        in json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    )
    ok = hook(plain, ev("Write", plain, file_path=str(plain / "spec.py")), "--project", str(a))
    assert ok.returncode == 0 and ok.stdout == ""
    r = hook(plain, ev("Bash", a, command="rm spec.py"), "--mode", "exit2")
    assert r.returncode == 2 and "of project a" in r.stderr
