"""Opt-in ``[rebuild] permission_mode = "dontAsk"`` (gap G4): the rebuild agent's edits are
scoped to the worktree and the protected files are denied up front, instead of
``acceptEdits`` (the default, unchanged), which lets the agent edit anywhere in scope and run
rm/mv/cp in the worktree. A denial does not fail the CLI's run (pre-flights P2 and R3), so
every claude step records the denials it saw on the round (``agent.denials``,
``agent.denial_count``). A FAKE claude; no LLM.

The real stream ``tests/studio/fixtures/claude-2.1.309-dontask-denials.jsonl`` is the R3
pre-flight run B3 (2026-10-31, CLI 2.1.309, haiku), copied verbatim: two deny-rule denials
(``spec.py``, ``sub/spec.py``), one mode denial (a write outside the cwd) and an allowed
write, ending ``subtype: success``."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice.studio.rebuild import (
    _MODEL_TOOLS,
    DEFAULT_PERMISSION_MODE,
    RebuildConfig,
    Rebuilder,
    allowed_tools,
    build_rebuild_argv,
    disallowed_tools,
    load_table,
)
from lattice.studio.session import Transcript
from tests.studio.seed import cleanup_worktrees, seed_project

ROOT = Path(__file__).resolve().parents[2]
API_MD = ROOT / "lattice" / "studio" / "API.md"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DONTASK = FIXTURES / "claude-2.1.309-dontask-denials.jsonl"
GUARD = FIXTURES / "claude-2.1.296-guard-deny.jsonl"


def argv(**kw: Any) -> list[str]:
    base: dict[str, Any] = dict(claude_bin="claude", text="go", model="sonnet",
                                mcp_config=Path("m.json"), settings=Path("s.json"),
                                gates=["./gate.sh"])  # fmt: skip
    return build_rebuild_argv(**{**base, **kw})


def opt(a: list[str], flag: str) -> str:
    return a[a.index(flag) + 1]


# ---- [rebuild] permission_mode -------------------------------------------------------------


def test_permission_mode_defaults_to_accept_edits(tmp_path: Path) -> None:
    assert DEFAULT_PERMISSION_MODE == "acceptEdits"
    assert RebuildConfig(tmp_path, {}).permission_mode == "acceptEdits"
    assert RebuildConfig(tmp_path, {"permission_mode": "dontAsk"}).permission_mode == "dontAsk"
    assert RebuildConfig(tmp_path, {"permission_mode": "acceptEdits"}).permission_mode == (
        "acceptEdits"
    )


@pytest.mark.parametrize(
    "bad", ["", "bypassPermissions", "plan", "default", "dontask", "auto", 1, None, ["dontAsk"]]
)
def test_an_invalid_permission_mode_falls_back_to_the_default(tmp_path: Path, bad: Any) -> None:
    assert RebuildConfig(tmp_path, {"permission_mode": bad}).permission_mode == "acceptEdits"


def test_permission_mode_is_not_a_plan_default(tmp_path: Path) -> None:
    cfg = RebuildConfig(tmp_path, {"permission_mode": "dontAsk"})
    assert "permission_mode" not in cfg.plan_defaults()
    assert "dontAsk" not in json.dumps(cfg.plan_defaults())


# ---- the argv ------------------------------------------------------------------------------


def test_the_default_argv_is_unchanged() -> None:
    plain = argv()
    assert opt(plain, "--permission-mode") == "acceptEdits"
    assert opt(plain, "--disallowedTools") == "NotebookEdit"
    assert opt(plain, "--allowedTools").split(",") == [
        "Read", "Grep", "Glob", "Edit", "Write", "Bash(./gate.sh:*)", *_MODEL_TOOLS,
    ]  # fmt: skip
    # acceptEdits ignores the protected list: byte-identical argv
    assert argv(permission_mode="acceptEdits", protected=["spec.py", "gate.sh"]) == plain
    assert allowed_tools(["./gate.sh"]) == allowed_tools(["./gate.sh"], "acceptEdits")
    assert disallowed_tools(["spec.py"]) == ["NotebookEdit"]


def test_an_invalid_mode_in_the_argv_builder_is_accept_edits() -> None:
    assert argv(permission_mode="bypassPermissions", protected=["spec.py"]) == argv()


def test_dont_ask_argv_scopes_edits_to_the_worktree() -> None:
    a = argv(permission_mode="dontAsk", protected=["spec.py"], gates=["./gate.sh", "make"])
    assert opt(a, "--permission-mode") == "dontAsk" and "acceptEdits" not in a
    allowed = opt(a, "--allowedTools").split(",")
    assert allowed == [
        "Read", "Grep", "Glob", "Edit(./**)", "Write(./**)", "Bash(./gate.sh:*)",
        "Bash(make:*)", *_MODEL_TOOLS,
    ]  # fmt: skip
    assert "Edit" not in allowed and "Write" not in allowed and "Bash" not in allowed
    assert "mcp__lattice__change_propose" not in allowed
    # everything else is as in the default mode
    plain = argv(gates=["./gate.sh", "make"])
    for flag in ("--tools", "--setting-sources", "--mcp-config", "--settings", "--model",
                 "--append-system-prompt"):  # fmt: skip
        assert opt(a, flag) == opt(plain, flag)
    assert a[:6] == plain[:6] and a[-2:] == plain[-2:] == ["--", "go"]


def test_dont_ask_denies_every_protected_path_anchored_at_the_worktree() -> None:
    a = argv(
        permission_mode="dontAsk",
        protected=["REQUIREMENTS.md", "app/spec.py", "app/spec.trace.json", "check.sh", "protos/"],
    )
    deny = opt(a, "--disallowedTools").split(",")
    assert deny == [
        "NotebookEdit",
        "Edit(/REQUIREMENTS.md)", "Write(/REQUIREMENTS.md)",
        "Edit(/app/spec.py)", "Write(/app/spec.py)",
        "Edit(/app/spec.trace.json)", "Write(/app/spec.trace.json)",
        "Edit(/check.sh)", "Write(/check.sh)",
        "Edit(/protos/**)", "Write(/protos/**)",  # a protected directory: all below it
    ]  # fmt: skip
    # a bare `name` or `./name` would match the name at ANY depth (pre-flight R3, B1/B3)
    assert not [r for r in deny if r.startswith(("Edit(./", "Write(./", "Edit(spec", "Write(s"))]


def test_deny_rules_normalize_the_paths() -> None:
    deny = disallowed_tools(
        ["./check.sh", "app/../spec.py", "../outside.txt", "", "spec.py", "/abs/x.proto"],
        "dontAsk",
    )
    assert deny == [
        "NotebookEdit", "Edit(/check.sh)", "Write(/check.sh)", "Edit(/spec.py)",
        "Write(/spec.py)", "Edit(//abs/x.proto)", "Write(//abs/x.proto)",
    ]  # fmt: skip


def test_dont_ask_without_protected_paths_keeps_notebook_edit_only() -> None:
    a = argv(permission_mode="dontAsk")
    assert opt(a, "--disallowedTools") == "NotebookEdit"


def test_dont_ask_keeps_the_caps_and_resume() -> None:
    a = argv(permission_mode="dontAsk", protected=["spec.py"], resume="s1", max_turns=9,
             max_budget_usd=0.5)  # fmt: skip
    assert opt(a, "--resume") == "s1" and opt(a, "--max-turns") == "9"
    assert opt(a, "--max-budget-usd") == "0.5000" and a[-2] == "--"


# ---- denials in the transcript -------------------------------------------------------------


def feed(t: Transcript, path: Path, *, result: bool = True) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("type") == "result" and not result:
            continue
        t.feed(rec)


def test_the_real_dont_ask_stream_has_three_denials() -> None:
    t = Transcript()
    feed(t, DONTASK)
    got = [(d["tool"], os.path.basename(d["target"])) for d in t.denials]
    assert got == [("Edit", "spec.py"), ("Edit", "spec.py"), ("Write", "outside.txt")]
    assert [d["target"].endswith("/repo/sub/spec.py") for d in t.denials] == [False, True, False]
    # the run itself succeeded: a denial is not an error
    res = [e for e in t.events_after(0) if e["type"] == "result"]
    assert res and res[-1]["ok"] is True
    assert not [e for e in t.events_after(0) if e["type"] == "error"]


def test_denials_are_counted_from_tool_results_when_the_result_is_missing() -> None:
    """A killed step has no result event: both denial texts (a deny rule and dontAsk's mode
    denial) still count."""
    t = Transcript()
    feed(t, DONTASK, result=False)
    assert [d["tool"] for d in t.denials] == ["Edit", "Edit", "Write"]


def test_a_denial_is_counted_once() -> None:
    t = Transcript()
    feed(t, DONTASK)
    feed(t, DONTASK)  # the same tool_use ids again
    assert len(t.denials) == 3


def test_the_guard_hook_denial_counts() -> None:
    """The lattice guard's PreToolUse deny (CLI 2.1.296, acceptEdits) is in
    ``permission_denials`` and its tool_result says ``hook error: Blocked``."""
    t = Transcript()
    feed(t, GUARD)
    assert [(d["tool"], d["target"]) for d in t.denials] == [("Edit", "/PROJECT/spec.py")]
    t2 = Transcript()
    feed(t2, GUARD, result=False)
    assert [(d["tool"], d["target"]) for d in t2.denials] == [("Edit", "/PROJECT/spec.py")]


def _use(tid: str, name: str, inp: dict[str, Any]) -> dict[str, Any]:
    return {"type": "assistant", "message": {"id": "m" + tid, "content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}  # fmt: skip


def _res(tid: str, text: str, is_error: bool) -> dict[str, Any]:
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text,
         "is_error": is_error}]}}  # fmt: skip


def test_other_tool_errors_are_not_denials() -> None:
    t = Transcript()
    t.feed(_use("t1", "Edit", {"file_path": "/w/a.py"}))
    t.feed(_res("t1", "<tool_use_error>File has not been read yet.</tool_use_error>", True))
    t.feed(_use("t2", "Bash", {"command": "./gate.sh"}))
    t.feed(_res("t2", "Exit code 1\nPermission to use the db was refused by the app", True))
    t.feed(_use("t3", "Bash", {"command": "rm x"}))
    t.feed(_res("t3", "Permission to use Bash has been denied because Claude Code is running "
                      "in don't ask mode.", False))  # not an error: not a denial  # fmt: skip
    assert t.denials == []


def test_result_permission_denials_without_a_tool_result() -> None:
    t = Transcript()
    t.feed({"type": "result", "subtype": "success", "is_error": False, "result": "ok",
            "permission_denials": [
                {"tool_name": "Bash", "tool_use_id": "x1", "tool_input": {"command": "mv a b"}},
                {"tool_name": "Write", "tool_use_id": "x2",
                 "tool_input": {"file_path": "/w/spec.py", "content": "x"}},
                "junk",
            ]})  # fmt: skip
    assert t.denials == [{"tool": "Bash", "target": "mv a b"},
                         {"tool": "Write", "target": "/w/spec.py"}]  # fmt: skip
    assert "permission_denials" not in [e for e in t.events_after(0) if e["type"] == "result"][0]


# ---- the rebuild loop with a fake claude ---------------------------------------------------

FAKE_CLAUDE = """#!{python}
import json, os, sys
if "--version" in sys.argv:
    print("2.0.0"); sys.exit(0)
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
spec = os.path.join(os.getcwd(), "spec.py")
def out(r):
    print(json.dumps(r), flush=True)
out({{"type": "system", "subtype": "init", "session_id": "sess-1"}})
out({{"type": "assistant", "session_id": "sess-1", "message": {{"id": "m1", "content": [
    {{"type": "tool_use", "id": "t1", "name": "Edit",
      "input": {{"file_path": spec, "old_string": "a", "new_string": "b"}}}}]}}}})
out({{"type": "user", "message": {{"content": [{{"type": "tool_result", "tool_use_id": "t1",
    "is_error": True, "content": "<tool_use_error>File is in a directory that is denied by "
    "your permission settings.</tool_use_error>"}}]}}}})
out({{"type": "assistant", "session_id": "sess-1", "message": {{"id": "m2", "content": [
    {{"type": "tool_use", "id": "t2", "name": "Bash", "input": {{"command": "rm gate.sh"}}}}]}}}})
out({{"type": "user", "message": {{"content": [{{"type": "tool_result", "tool_use_id": "t2",
    "is_error": True, "content": "Permission to use Bash has been denied because Claude Code "
    "is running in don't ask mode."}}]}}}})
out({{"type": "result", "subtype": "success", "is_error": False, "result": "done",
     "session_id": "sess-1", "num_turns": 3, "total_cost_usd": 0.01,
     "usage": {{"input_tokens": 10, "output_tokens": 5}},
     "permission_denials": [
        {{"tool_name": "Edit", "tool_use_id": "t1", "tool_input": {{"file_path": spec}}}},
        {{"tool_name": "Bash", "tool_use_id": "t2", "tool_input": {{"command": "rm gate.sh"}}}}]}})
"""


def build(tmp_path: Path, **table: Any) -> tuple[dict[str, Any], list[list[str]]]:
    root = seed_project(tmp_path)
    exe = tmp_path / "claude"
    exe.write_text(FAKE_CLAUDE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    t = {**load_table(root), "agent": "claude", "max_rounds": 1, **table}
    rb = Rebuilder(root, t, name="notes", model="sonnet", claude_bin=str(exe),
                   env={**os.environ, "FAKE_LOG": str(tmp_path / "claude.log")})  # fmt: skip
    rid = rb.start([], scope={"area": "writing-notes"}, baseline_wait=60)["id"]
    rec = rb.get(rid, wait=60)
    cleanup_worktrees(root)
    log = tmp_path / "claude.log"
    return rec, [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def test_dont_ask_build_denies_the_protected_set(tmp_path: Path) -> None:
    rec, calls = build(tmp_path, permission_mode="dontAsk")
    assert calls, rec
    a = calls[0]
    assert opt(a, "--permission-mode") == "dontAsk"
    allowed = opt(a, "--allowedTools").split(",")
    assert allowed[:6] == ["Read", "Grep", "Glob", "Edit(./**)", "Write(./**)",
                           "Bash(./gate.sh:*)"]  # fmt: skip
    deny = opt(a, "--disallowedTools").split(",")
    assert deny[0] == "NotebookEdit"
    for p in ("REQUIREMENTS.md", "spec.py", "spec.trace.json", "gate.sh"):  # the seed's set
        assert f"Edit(/{p})" in deny and f"Write(/{p})" in deny


def test_every_claude_step_records_its_denials(tmp_path: Path) -> None:
    rec, calls = build(tmp_path, permission_mode="dontAsk")
    rnd = rec["rounds"][0]
    ag = rnd["agent"]
    assert ag["denial_count"] == 2
    # targets inside the worktree are relative to it; a command stays as it is
    assert ag["denials"] == [{"tool": "Edit", "target": "spec.py"},
                             {"tool": "Bash", "target": "rm gate.sh"}]  # fmt: skip
    assert not (rnd.get("error") or {})  # a denial does not fail the step
    assert "denials" not in rec["usage"] and "denial_count" not in rec["usage"]


def test_the_default_mode_records_denials_too(tmp_path: Path) -> None:
    rec, calls = build(tmp_path)
    assert opt(calls[0], "--permission-mode") == "acceptEdits"
    assert opt(calls[0], "--disallowedTools") == "NotebookEdit"
    assert rec["rounds"][0]["agent"]["denial_count"] == 2


# ---- the docs ------------------------------------------------------------------------------


def test_api_md_documents_permission_mode() -> None:
    doc = API_MD.read_text(encoding="utf-8")
    assert 'permission_mode = "acceptEdits"' in doc
    assert "`Edit(./**)`" in doc and "`Edit(/<path>)`" in doc
    assert "`agent.denials`" in doc and "`agent.denial_count`" in doc
    assert "permission_denials" in doc
    assert "_restore_protected" in doc and "backstop" in doc
