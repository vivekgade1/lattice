"""lattice.mcp.host_shim: Cursor hook payloads in, the guard's decision out in Cursor's format.

Every payload is SYNTHETIC, built from https://cursor.com/docs/agent/hooks (fetched 2026-10-30).
Field provenance is cited per builder. Not documented there (see the shim docstring): the
`tool_input` of the Write and Delete tools; `file_path` is a guess from a forum thread, so the
Write/Delete tests pin that guess, not a documented fact."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice.mcp import host_shim
from lattice.mcp.host_shim import run, translate_cursor

PROTECTED = ["REQUIREMENTS.md", "spec.py", "spec.trace.json", "check.sh", "studio.toml"]


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    for n in ("REQUIREMENTS.md", "spec.py", "spec.trace.json", "check.sh", "app.go", "notes.md"):
        (p / n).write_text("x")
    # the guard's own way to protect more files: studio.toml [mcp] protected
    (p / "studio.toml").write_text('[mcp]\nprotected = ["check.sh", "studio.toml"]\n')
    return p


def common(event: str, root: Path) -> dict[str, Any]:
    # hooks page, "Common Input Fields": conversation_id, generation_id, hook_event_name,
    # cursor_version, workspace_roots, ...
    return {"conversation_id": "c1", "generation_id": "g1", "hook_event_name": event,
            "cursor_version": "x", "workspace_roots": [str(root)]}  # fmt: skip


def shell_hook(root: Path, command: str, cwd: Path | None = None) -> dict[str, Any]:
    # hooks page, beforeShellExecution input: {"command", "cwd", "sandbox"}
    return {**common("beforeShellExecution", root), "command": command,
            "cwd": str(cwd or root), "sandbox": False}  # fmt: skip


def pre_tool(
    root: Path, tool: str, tool_input: dict[str, Any], cwd: Path | None = None
) -> dict[str, Any]:
    # hooks page, preToolUse input: {"tool_name", "tool_input", "tool_use_id", "cwd", ...};
    # tool_name is one of "Shell" | "Read" | "Write" | "Grep" | "Delete" | "Task" | "MCP:<tool>"
    return {**common("preToolUse", root), "tool_name": tool, "tool_input": tool_input,
            "tool_use_id": "t1", "cwd": str(cwd or root)}  # fmt: skip


def go(payload: Any, proj: Path, **kw: Any) -> tuple[int, str, str]:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return run(raw, "cursor", [proj], **kw)


def denied(res: tuple[int, str, str]) -> bool:
    code, out, _ = res
    return code == 0 and bool(out) and json.loads(out)["permission"] == "deny"


# ------------------------------------------------------------------------------ the deny matrix
@pytest.mark.parametrize("name", PROTECTED)
def test_shell_hook_denies_writes_to_each_protected_file(proj: Path, name: str) -> None:
    for cmd in (f"echo hi > {name}", f"sed -i s/a/b/ {name}", f"rm {name}", f"tee {name}"):
        assert denied(go(shell_hook(proj, cmd), proj)), cmd


@pytest.mark.parametrize("name", PROTECTED)
def test_pretooluse_shell_and_write_deny(proj: Path, name: str) -> None:
    # hooks page: Shell's tool_input is {"command", "working_directory"}
    shell = pre_tool(
        proj, "Shell", {"command": f"echo hi >> {name}", "working_directory": str(proj)}
    )
    assert denied(go(shell, proj))
    assert denied(
        go(pre_tool(proj, "Write", {"file_path": str(proj / name), "content": "x"}), proj)
    )
    assert denied(
        go(pre_tool(proj, "Delete", {"file_path": str(proj / name)}), proj)
    )  # unverified shape


def test_relative_write_path_resolves_against_cwd(proj: Path) -> None:
    sub = proj / "sub"
    sub.mkdir()
    assert denied(go(pre_tool(proj, "Write", {"file_path": "../spec.py"}, cwd=sub), proj))
    assert denied(go(pre_tool(proj, "Write", {"file_path": "spec.py"}), proj))


def test_cwd_falls_back_to_workspace_roots(proj: Path) -> None:
    payload = pre_tool(proj, "Write", {"file_path": "spec.py"})
    del payload["cwd"]
    assert denied(go(payload, proj))


def test_deny_works_without_explicit_project_via_owning_project(proj: Path) -> None:
    code, out, _ = run(json.dumps(shell_hook(proj, "echo > spec.py")), "cursor", [])
    assert code == 0 and json.loads(out)["permission"] == "deny"  # spec.trace.json marks the owner


# ------------------------------------------------------------------------------ allow
def test_allows_everything_else(proj: Path) -> None:
    ok = [
        shell_hook(proj, "go test ./..."),
        shell_hook(proj, "echo hi > app.go"),
        shell_hook(proj, "cat spec.py"),  # reads are fine
        pre_tool(proj, "Write", {"file_path": str(proj / "app.go"), "content": "x"}),
        pre_tool(proj, "Write", {"file_path": str(proj / "notes.md")}),
        pre_tool(proj, "Shell", {"command": "ls", "working_directory": str(proj)}),
        pre_tool(proj, "Read", {"file_path": str(proj / "spec.py")}),
        pre_tool(proj, "Grep", {"pattern": "spec.py"}),
        pre_tool(proj, "MCP:change_propose", {"request": "edit spec.py"}),
        # hooks page: afterFileEdit is fire-and-forget and cannot block
        {**common("afterFileEdit", proj), "file_path": str(proj / "spec.py"),
         "edits": [{"old_string": "a", "new_string": "b"}]},
        # beforeReadFile can block, but Lattice does not guard reads
        {**common("beforeReadFile", proj), "file_path": str(proj / "spec.py"), "content": "x",
         "attachments": []},
    ]  # fmt: skip
    for p in ok:
        assert go(p, proj) == (0, "", ""), p


def test_a_file_outside_any_project_is_allowed(proj: Path, tmp_path: Path) -> None:
    stray = tmp_path / "other" / "spec.py"
    stray.parent.mkdir()
    stray.write_text("x")
    assert go(pre_tool(proj, "Write", {"file_path": str(stray)}), proj) == (0, "", "")


# ------------------------------------------------------------------------------ fail-open
@pytest.mark.parametrize(
    "raw",
    ["", "not json", "[]", "null", "42", '{"hook_event_name": "preToolUse"}',
     '{"hook_event_name": "preToolUse", "tool_name": "Write", "tool_input": "{{"}',
     '{"hook_event_name": "preToolUse", "tool_name": "Write", "tool_input": {"file_path": 7}}',
     '{"hook_event_name": "beforeShellExecution", "command": 5}'],
)  # fmt: skip
def test_malformed_or_unknown_input_is_allowed(proj: Path, raw: str) -> None:
    code, out, _ = go(raw, proj)
    assert code == 0 and out == ""


def test_a_bug_in_the_shim_fails_open_with_a_note(
    proj: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_: dict[str, Any]) -> None:
        raise RuntimeError("bug")

    monkeypatch.setitem(host_shim.HOSTS, "cursor", boom)
    code, out, err = go(shell_hook(proj, "echo > spec.py"), proj)
    assert (code, out) == (0, "") and "allowing" in err


def test_tool_input_as_json_string_is_parsed(proj: Path) -> None:
    # hooks page documents "<json params>" for beforeMCPExecution's tool_input; accept it here too
    p = pre_tool(proj, "Write", {})
    p["tool_input"] = json.dumps({"file_path": str(proj / "spec.py")})
    assert denied(go(p, proj))


# ------------------------------------------------------------------------------ block encoding
def test_deny_json_encoding(proj: Path) -> None:
    code, out, err = go(shell_hook(proj, "echo > spec.py"), proj)
    body = json.loads(out)
    # hooks page output: {"permission": "deny", "user_message": ..., "agent_message": ...}
    assert (code, err) == (0, "")
    assert set(body) == {"permission", "user_message", "agent_message"}
    assert body["permission"] == "deny"
    assert "change_propose" in body["agent_message"] and "spec.py" in body["agent_message"]
    assert body["user_message"]


def test_deny_exit2_encoding(proj: Path) -> None:
    # hooks page: "Exit code 2 - Block the action (equivalent to returning permission: deny)"
    code, out, err = go(shell_hook(proj, "echo > spec.py"), proj, mode="exit2")
    assert (code, out) == (2, "") and "change_propose" in err


def test_allow_encoding(proj: Path) -> None:
    assert go(shell_hook(proj, "ls"), proj) == (0, "", "")
    code, out, _ = go(shell_hook(proj, "ls"), proj, emit_allow=True)
    assert code == 0 and json.loads(out) == {"permission": "allow"}


def test_reason_is_the_guards_own_text(proj: Path) -> None:
    from lattice.mcp.guard import Guard

    assert Guard(proj).decide({"tool_name": "Bash", "tool_input": {"command": "echo > spec.py"},
                               "cwd": str(proj)}) == "spec.py"  # fmt: skip
    reason = host_shim.deny_reason(
        {"tool_name": "Bash", "tool_input": {"command": "echo > spec.py"}, "cwd": str(proj)}, [proj]
    )
    assert reason and reason.startswith("Blocked: spec.py")


# ------------------------------------------------------------------------------ translation + CLI
def test_translation_shapes(proj: Path) -> None:
    assert translate_cursor(shell_hook(proj, "ls", proj)) == {
        "tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(proj)}  # fmt: skip
    ev = translate_cursor(pre_tool(proj, "Shell", {"command": "ls", "working_directory": "/w"}))
    assert ev and ev["cwd"] == "/w"  # working_directory beats the payload cwd
    assert translate_cursor(pre_tool(proj, "Write", {"file_path": "a"}))["tool_name"] == "Write"  # type: ignore[index]
    assert translate_cursor(pre_tool(proj, "Read", {"file_path": "a"})) is None


def test_main_reads_stdin_and_prints(proj: Path, capsys: pytest.CaptureFixture[str]) -> None:
    stdin = io.StringIO(json.dumps(shell_hook(proj, "echo > spec.py")))
    assert host_shim.main(["--project", str(proj)], stdin=stdin) == 0
    assert json.loads(capsys.readouterr().out)["permission"] == "deny"
    assert host_shim.main(["--project", str(proj)], stdin=io.StringIO("garbage")) == 0
    assert capsys.readouterr().out == ""
    stdin = io.StringIO(json.dumps(shell_hook(proj, "echo > spec.py")))
    assert host_shim.main(["--project", str(proj), "--mode", "exit2"], stdin=stdin) == 2


def test_subprocess_end_to_end(proj: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    r = subprocess.run(
        [sys.executable, "-m", "lattice.mcp.host_shim", "--project", str(proj)],
        input=json.dumps(pre_tool(proj, "Write", {"file_path": str(proj / "check.sh")})),
        capture_output=True, text=True, cwd=root, check=False,
    )  # fmt: skip
    assert r.returncode == 0 and json.loads(r.stdout)["permission"] == "deny"


def test_shipped_config_examples_are_valid() -> None:
    d = Path(__file__).resolve().parents[2] / "lattice-host-adapters" / "cursor"
    hooks = json.loads((d / ".cursor" / "hooks.json").read_text())
    assert hooks["version"] == 1  # hooks page: required format
    for ev in ("beforeShellExecution", "preToolUse"):
        assert "lattice.mcp.host_shim" in hooks["hooks"][ev][0]["command"]
    mcp = json.loads((d / ".cursor" / "mcp.json").read_text())["mcpServers"]["lattice"]
    assert mcp["args"][:3] == ["mcp", "serve", "--project"]
    agents = (d / "AGENTS.md").read_text()
    assert "change_propose" in agents and "contract" in agents
