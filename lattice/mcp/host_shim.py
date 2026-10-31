"""A second-host adapter: Lattice's spec guard as a Cursor hook.

``lattice.mcp.guard`` reads Claude Code's ``PreToolUse`` JSON and answers in Claude's
``hookSpecificOutput`` format. This module sits in front of it for **Cursor**: it reads Cursor's
hook payload, hands the guard the event shape it already understands, and answers in Cursor's
block/allow format. It adds no protection logic of its own; every decision is the guard's.

    {"version": 1, "hooks": {
        "beforeShellExecution": [{"command": "python -m lattice.mcp.host_shim --host cursor"}],
        "preToolUse": [{"matcher": "Write|Delete|Shell",
                        "command": "python -m lattice.mcp.host_shim --host cursor"}]}}

Why Cursor (chosen 2026-10-30 from the vendors' own documentation, fetched 2026-10-30)
---------------------------------------------------------------------------------------
- Cursor, https://cursor.com/docs/agent/hooks: a per-event table of input and output JSON, a
  documented block (``"permission": "deny"``, or exit code 2), a ``failClosed`` switch, and a
  shell hook (``beforeShellExecution``: ``command``, ``cwd``) whose schema is complete.
  MCP config: https://cursor.com/docs/context/mcp; rules and AGENTS.md:
  https://cursor.com/docs/context/rules; headless output:
  https://cursor.com/docs/cli/reference/output-format.
- OpenAI Codex, https://developers.openai.com/codex/hooks (redirects to
  https://learn.chatgpt.com/docs/hooks): also documented, and its ``PreToolUse`` payload and
  deny output are Claude Code's own (``tool_name``, ``tool_input``, ``hookSpecificOutput``,
  exit 2), so ``guard.py`` nearly works as is; the one gap is ``apply_patch``, whose
  ``tool_input.command`` is patch text. Its headless stream is documented at
  https://learn.chatgpt.com/docs/non-interactive-mode. Codex is the cheaper second port; Cursor
  was chosen because its schema differs from Claude's in every field, which is what a
  translation layer has to prove.
- Gemini CLI was not read.

Facts this module relies on, as the docs state them
---------------------------------------------------
- Every hook gets ``conversation_id, generation_id, model, hook_event_name, cursor_version,
  workspace_roots, user_email, transcript_path`` (hooks page, "Common Input Fields").
- ``beforeShellExecution`` input: ``command``, ``cwd``, ``sandbox``. Output:
  ``{"permission": "allow"|"deny"|"ask", "user_message", "agent_message"}``.
- ``preToolUse`` input: ``tool_name`` ("Shell", "Read", "Write", "Grep", "Delete", "Task",
  "MCP:<tool>"), ``tool_input``, ``tool_use_id``, ``cwd``; Shell's ``tool_input`` is
  ``{"command", "working_directory"}``. Output: ``permission`` ("allow"|"deny"),
  ``user_message``, ``agent_message``, ``updated_input``.
- Exit code 2 blocks (same as ``permission: "deny"``); any other non-zero exit fails open
  unless the hook sets ``"failClosed": true``.
- ``afterFileEdit`` and ``postToolUse`` run after the edit and cannot block; ``beforeReadFile``
  can block reads, which Lattice does not need to.

Facts that could NOT be verified (do not rely on them)
------------------------------------------------------
- The shape of ``tool_input`` for the ``Write`` and ``Delete`` tools in ``preToolUse``. The hooks
  page documents ``file_path`` only for ``afterFileEdit``. A Cursor forum thread
  (https://forum.cursor.com/t/165962, July 2026) shows ``Write``'s ``tool_input`` carrying
  ``content`` and says ``file_path`` is the field ``updated_input`` can change, so ``file_path``
  is read first and ``path`` second; both are guesses for ``Delete``. Until a real payload is
  captured, a Write/Delete that names its target under another key is **allowed** (fail-open),
  and the shell hook is the verified line of defence for ``cat > spec.py`` style writes.
- What Cursor does with an empty stdout on allow (this shim prints nothing, and
  ``--emit-allow`` prints ``{"permission": "allow"}``; the latter may skip an approval prompt).
- Whether Cursor's own editor tool calls (Tab, inline edit) run ``preToolUse`` at all.
- Whether ``tool_input`` is ever a JSON string rather than an object (``beforeMCPExecution``
  documents ``"<json params>"``); a string is parsed here, anything else is allowed.
- A relative ``file_path`` is resolved against the payload's ``cwd``, else the first
  ``workspace_roots`` entry; the docs do not say which Cursor sends relative paths in.

Failure policy: malformed input, an unknown event or tool, or a bug here exits 0 with a note on
stderr (allow), so the shim never blocks work on its own faults even under ``failClosed``; a
*recognised* write to a protected file is always denied. The guard text is reused verbatim by
calling ``guard.main`` and reading its deny reason, so a change to the reason text in
``guard.py`` follows automatically.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from lattice.mcp import guard as _guard

# Cursor tool names (hooks page, "Tool Name Values") mapped to the guard's vocabulary
_SHELL_TOOLS = {"Shell"}
_FILE_WRITE_TOOLS = {"Write", "Delete"}
_FILE_PATH_KEYS = ("file_path", "path")  # see the module docstring: unverified for Write/Delete


def _tool_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    ti = payload.get("tool_input")
    if isinstance(ti, str):
        try:
            ti = json.loads(ti)
        except ValueError:
            return None
    return ti if isinstance(ti, dict) else None


def _cwd(payload: dict[str, Any], *candidates: Any) -> str:
    """The working directory of the call: an explicit one, else the payload's `cwd`, else the
    first workspace root, else the hook process's own."""
    roots = payload.get("workspace_roots")
    first_root = roots[0] if isinstance(roots, list) and roots else None
    for c in (*candidates, payload.get("cwd"), first_root):
        if isinstance(c, str) and c:
            return c
    return os.getcwd()


def translate_cursor(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Cursor's hook payload as the event ``Guard.decide`` consumes (Claude Code's
    ``{tool_name, tool_input, cwd}``), or None when the call is not one the guard judges
    (reads, MCP calls, post-hooks, unknown events or tools)."""
    event = payload.get("hook_event_name")
    if event == "beforeShellExecution" or (event is None and "command" in payload):
        cmd = payload.get("command")  # hooks page: beforeShellExecution input
        if not isinstance(cmd, str):
            return None
        return {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": _cwd(payload)}
    if event not in (None, "preToolUse"):
        return None  # afterFileEdit / postToolUse / beforeReadFile ... cannot or need not block
    tool = payload.get("tool_name")
    ti = _tool_input(payload)
    if ti is None:
        return None
    if tool in _SHELL_TOOLS:
        cmd = ti.get("command")
        if not isinstance(cmd, str):
            return None
        cwd = _cwd(payload, ti.get("working_directory"))
        return {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": cwd}
    if tool in _FILE_WRITE_TOOLS:
        for key in _FILE_PATH_KEYS:
            target = ti.get(key)
            if isinstance(target, str) and target:
                return {"tool_name": "Write", "tool_input": {"file_path": target},
                        "cwd": _cwd(payload)}  # fmt: skip
    return None


HOSTS: dict[str, Callable[[dict[str, Any]], dict[str, Any] | None]] = {"cursor": translate_cursor}


def deny_reason(event: dict[str, Any], projects: Sequence[Path] = ()) -> str | None:
    """The guard's deny reason for a guard-shaped `event`, or None to allow. Runs
    ``guard.main`` (the public entry point) so the decision and its text are the guard's own."""
    out = io.StringIO()
    argv = [a for p in projects for a in ("--project", str(p))]
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        _guard.main(argv, stdin=io.StringIO(json.dumps(event)))
    text = out.getvalue().strip()
    if not text:
        return None
    try:
        return str(json.loads(text)["hookSpecificOutput"]["permissionDecisionReason"])
    except (ValueError, KeyError, TypeError):
        return None


def encode_cursor(
    reason: str | None, mode: str = "json", emit_allow: bool = False
) -> tuple[int, str, str]:
    """(exit code, stdout, stderr) for Cursor: a deny is ``{"permission": "deny", ...}`` on
    stdout with exit 0 (mode ``json``) or the reason on stderr with exit 2 (mode ``exit2``); an
    allow is silence (or ``{"permission": "allow"}`` with `emit_allow`)."""
    if reason is None:
        return 0, ('{"permission": "allow"}' if emit_allow else ""), ""
    if mode == "exit2":
        return 2, "", reason
    body = {
        "permission": "deny",
        "user_message": "Lattice blocked an edit to a spec file; propose the change instead.",
        "agent_message": reason,
    }
    return 0, json.dumps(body), ""


def run(
    raw: str,
    host: str = "cursor",
    projects: Sequence[Path] = (),
    mode: str = "json",
    emit_allow: bool = False,
) -> tuple[int, str, str]:
    """The whole hook: host payload text in, (exit code, stdout, stderr) out. Never raises."""
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("the hook input is not an object")
        event = HOSTS[host](payload)
        reason = None if event is None else deny_reason(event, projects)
        return encode_cursor(reason, mode, emit_allow)
    except Exception as e:  # noqa: BLE001 - fail open on our own faults, never on a recognised write
        return 0, "", f"lattice host_shim: allowing, could not judge the call: {e}\n"


def main(argv: Sequence[str] | None = None, stdin: Any = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lattice.mcp.host_shim", description=__doc__)
    ap.add_argument("--host", choices=sorted(HOSTS), default="cursor")
    ap.add_argument("--project", type=Path, action="append", default=[],
                    help="a project to protect (repeatable; as for lattice.mcp.guard)")  # fmt: skip
    ap.add_argument("--mode", choices=("json", "exit2"), default="json")
    ap.add_argument("--emit-allow", action="store_true",
                    help='print {"permission": "allow"} when allowing')  # fmt: skip
    ns = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        raw = (stdin or sys.stdin).read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"lattice host_shim: unreadable hook input: {e}", file=sys.stderr)
        return 0
    code, out, err = run(raw, ns.host, ns.project, ns.mode, ns.emit_allow)
    if out:
        print(out)
    if err:
        print(err, end="" if err.endswith("\n") else "\n", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
