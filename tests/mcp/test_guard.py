"""The PreToolUse guard: allow/deny matrix, path traversal, symlinks, output format."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice.mcp import guard
from lattice.mcp.guard import Guard


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / "sub").mkdir(parents=True)
    for n in ("REQUIREMENTS.md", "spec.py", "spec.trace.json", "app.go", "notes.md"):
        (p / n).write_text("x")
    (p / "sub" / "spec.py").write_text("other project's spec")
    return p


def ev(tool: str, cwd: Path, **ti: Any) -> dict[str, Any]:
    return {"tool_name": tool, "tool_input": ti, "cwd": str(cwd), "hook_event_name": "PreToolUse"}


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
@pytest.mark.parametrize("name", ["REQUIREMENTS.md", "spec.py", "spec.trace.json"])
def test_denies_edit_tools_on_protected_files(proj: Path, tool: str, name: str) -> None:
    g = Guard(proj)
    assert g.decide(ev(tool, proj, file_path=str(proj / name))) == name
    assert g.decide(ev(tool, proj / "sub", file_path=f"../{name}")) == name  # relative + ..
    assert g.decide(ev(tool, proj, file_path=name)) == name


def test_notebook_edit_and_allowed_edits(proj: Path) -> None:
    g = Guard(proj)
    assert g.decide(ev("NotebookEdit", proj, notebook_path=str(proj / "spec.py"))) == "spec.py"
    assert g.decide(ev("Edit", proj, file_path=str(proj / "app.go"))) is None
    assert g.decide(ev("Write", proj, file_path=str(proj / "notes.md"))) is None
    assert g.decide(ev("Edit", proj, file_path=str(proj / "sub" / "spec.py"))) is None
    assert g.decide(ev("Read", proj, file_path=str(proj / "spec.py"))) is None
    assert g.decide(ev("Edit", proj, file_path=str(proj.parent / "spec.py"))) is None


def test_path_traversal_and_dot_segments(proj: Path) -> None:
    g = Guard(proj)
    tricky = str(proj / "sub" / ".." / "sub" / ".." / "spec.py")
    assert g.decide(ev("Write", proj, file_path=tricky)) == "spec.py"
    assert g.decide(ev("Write", proj, file_path=str(proj / "sub/../REQUIREMENTS.md")))


def test_symlink_to_a_protected_file_is_denied(proj: Path, tmp_path: Path) -> None:
    link = tmp_path / "innocent.txt"
    link.symlink_to(proj / "spec.py")
    g = Guard(proj)
    assert g.decide(ev("Write", tmp_path, file_path=str(link))) == "spec.py"
    # a symlinked directory
    d = tmp_path / "alias"
    d.symlink_to(proj, target_is_directory=True)
    assert g.decide(ev("Edit", tmp_path, file_path=str(d / "spec.trace.json"))) == "spec.trace.json"
    # a protected name that is itself a symlink to a file elsewhere
    other = tmp_path / "elsewhere.md"
    other.write_text("y")
    (proj / "REQUIREMENTS.md").unlink()
    (proj / "REQUIREMENTS.md").symlink_to(other)
    g2 = Guard(proj)
    assert g2.decide(ev("Write", proj, file_path=str(other))) == "REQUIREMENTS.md"


DENY = [
    "echo x > spec.py",
    "echo x >> REQUIREMENTS.md",
    "cat a >| spec.trace.json",
    "cat <<EOF > spec.py\nhello\nEOF",
    "echo x>spec.py",
    "ls && echo y 1> spec.py",
    "echo x | tee spec.py",
    "echo x | tee -a REQUIREMENTS.md",
    "sed -i 's/a/b/' spec.py",
    "sed -i.bak 's/a/b/' REQUIREMENTS.md",
    "sed -ni 's/a/b/p' spec.trace.json",
    "sed --in-place s/a/b/ spec.py",
    "perl -pi -e 's/a/b/' spec.py",
    "mv spec.py /tmp/gone",
    "mv /tmp/new.py spec.py",
    "cp /tmp/x.json spec.trace.json",
    "cp -f x REQUIREMENTS.md",
    "rm spec.py",
    "rm -f REQUIREMENTS.md spec.trace.json",
    "rm -rf .",
    "truncate -s 0 spec.py",
    "dd if=/dev/zero of=spec.py",
    "git checkout -- spec.py",
    "git checkout HEAD~1 -- REQUIREMENTS.md",
    "git checkout .",
    "git restore spec.trace.json",
    "git restore .",
    "git stash",
    "git stash push -- spec.py",
    "git reset --hard",
    "git rm spec.py",
    "git -C . checkout -- spec.py",
    "cd sub && sed -i s/a/b/ ../spec.py",
    "cd /tmp && echo x > PROJ/spec.py",
    "python3 -c \"open('spec.py','w').write('x')\"",
    'python -c \'import pathlib; pathlib.Path("REQUIREMENTS.md").write_text("x")\'',
    "echo hi; rm spec.py",
    "echo hi | cat > $(pwd)/spec.py",
]
ALLOW = [
    "cat spec.py",
    "grep -n Scenario spec.py",
    "ls -la",
    "echo x > notes.md",
    "echo x > sub/spec.py",
    "sed -i s/a/b/ app.go",
    "sed -n 1,5p spec.py",
    "cp spec.py /tmp/backup.py",
    "cp spec.py spec.py.bak",
    "mv notes.md notes2.md",
    "rm -f app.go",
    "git status",
    "git diff -- spec.py",
    "git log --oneline -- REQUIREMENTS.md",
    "git stash list",
    "git checkout -b feature",
    "git commit -m 'x'",
    "python -m pytest tests",
    "python3 -c 'print(open(\"spec.py\").read())'",
    "echo 2>&1 done",
    "wc -l REQUIREMENTS.md spec.trace.json",
    "diff spec.py spec.py.bak > /tmp/d.txt",
]


@pytest.mark.parametrize("cmd", DENY)
def test_bash_deny_matrix(proj: Path, cmd: str) -> None:
    cmd = cmd.replace("PROJ", str(proj))
    cmd = cmd.replace("$(pwd)", str(proj))
    assert Guard(proj).decide(ev("Bash", proj, command=cmd)) is not None, cmd


@pytest.mark.parametrize("cmd", ALLOW)
def test_bash_allow_matrix(proj: Path, cmd: str) -> None:
    assert Guard(proj).decide(ev("Bash", proj, command=cmd)) is None, cmd


def test_extra_protected_names_from_studio_toml(proj: Path) -> None:
    (proj / "studio.toml").write_text('[mcp]\nprotected = ["CONTEXT.md"]\n')
    g = Guard(proj)
    assert g.decide(ev("Write", proj, file_path=str(proj / "CONTEXT.md"))) == "CONTEXT.md"


def run_guard(proj: Path, event: Any, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "lattice.mcp.guard", "--project", str(proj), *args],
        input=event if isinstance(event, str) else json.dumps(event),
        capture_output=True,
        text=True,
        check=False,
    )


def test_deny_output_is_hook_json(proj: Path) -> None:
    r = run_guard(proj, ev("Write", proj, file_path=str(proj / "spec.py"), content="x"))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and out["permissionDecision"] == "deny"
    assert "change_propose" in out["permissionDecisionReason"]
    assert "spec.py" in out["permissionDecisionReason"]


def test_deny_exit2_mode_and_allow_output(proj: Path) -> None:
    r = run_guard(proj, ev("Bash", proj, command="rm spec.py"), "--mode", "exit2")
    assert r.returncode == 2 and r.stdout == "" and "change_propose" in r.stderr
    ok = run_guard(proj, ev("Edit", proj, file_path=str(proj / "app.go")))
    assert ok.returncode == 0 and ok.stdout == ""


def test_unreadable_input_is_a_non_blocking_error(proj: Path) -> None:
    r = run_guard(proj, "not json")
    assert r.returncode == 1 and "unreadable" in r.stderr
    assert guard.main(["--project", str(proj)], stdin=io.StringIO("[]")) == 1


def test_event_without_cwd_uses_process_cwd(proj: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(proj)
    e = {"tool_name": "Write", "tool_input": {"file_path": "spec.py"}}
    assert Guard(proj).decide(e) == "spec.py"


def test_the_hook_is_inert_in_a_directory_with_no_drafted_spec(tmp_path: Path) -> None:
    """Installed at user level the hook runs everywhere: another project's own spec.py and
    the owner's REQUIREMENTS.md before drafting must stay editable."""
    (tmp_path / "spec.py").write_text("# somebody else's file\n")
    (tmp_path / "REQUIREMENTS.md").write_text("# rules\n")
    event = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(tmp_path / "spec.py")},
        "cwd": str(tmp_path),
    }
    out = subprocess.run(
        [sys.executable, "-m", "lattice.mcp.guard", "--project", str(tmp_path)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0 and out.stdout.strip() == ""
    (tmp_path / "spec.trace.json").write_text("{}")  # now it is a Lattice project
    out = subprocess.run(
        [sys.executable, "-m", "lattice.mcp.guard", "--project", str(tmp_path)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        check=False,
    )
    assert "permissionDecision" in out.stdout and '"deny"' in out.stdout
