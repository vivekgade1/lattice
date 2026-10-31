"""Turn and budget caps of the rebuild agent (API.md v1.11, gap G1): ``[rebuild] max_turns``,
``step_max_usd`` and ``max_budget_usd`` become ``--max-turns`` / ``--max-budget-usd`` on
each ``claude -p`` (the build budget minus the cost already recorded), a step is not spawned
when less than 0.05 of the build budget is left, and the CLI's ``error_max_turns`` /
``error_max_budget_usd`` results (shapes from docs/results/2026-10-30-preflights.md P1)
become errors with a ``reason`` and a distinct ``cause``. A FAKE claude. No LLM."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice.improve.events import CAUSES
from lattice.improve.signals import build_cause
from lattice.studio.rebuild import (
    MIN_STEP_USD,
    RebuildConfig,
    Rebuilder,
    build_rebuild_argv,
    load_table,
)
from lattice.studio.session import Transcript
from tests.studio.seed import cleanup_worktrees, seed_project

API_MD = Path(__file__).resolve().parents[2] / "lattice" / "studio" / "API.md"

# the P1 result events, verbatim but for the fields P1 already dropped as uninformative
P1_BUDGET = {
    "duration_api_ms": 0, "stop_reason": "tool_use", "session_id": "sess-b",
    "total_cost_usd": 0.00021693,
    "usage": {"output_tokens_details": {"thinking_tokens": 0}, "input_tokens": 0,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
              "output_tokens": 0, "iterations": []},
    "modelUsage": {"claude-haiku-5-5": {"inputTokens": 2, "outputTokens": 185,
                                        "cacheReadInputTokens": 4383,
                                        "cacheCreationInputTokens": 402,
                                        "costUSD": 0.00021693, "costBasis": "list"}},
    "permission_denials": [], "terminal_reason": "budget_exhausted", "is_error": True,
    "num_turns": 1, "subtype": "error_max_budget_usd",
    "errors": ["Reached maximum budget ($0.0001)"], "type": "result", "duration_ms": 1652,
}  # fmt: skip
P1_TURNS = {
    "duration_api_ms": 1242, "stop_reason": "tool_use", "session_id": "sess-t",
    "total_cost_usd": 0.00018253,
    "usage": {"input_tokens": 2, "cache_creation_input_tokens": 400,
              "cache_read_input_tokens": 4383, "output_tokens": 117},
    "modelUsage": {"claude-haiku-5-5": {"inputTokens": 2, "outputTokens": 117,
                                        "cacheReadInputTokens": 4383,
                                        "cacheCreationInputTokens": 400,
                                        "costUSD": 0.00018253}},
    "terminal_reason": "max_turns", "is_error": True, "num_turns": 2,
    "subtype": "error_max_turns", "errors": ["Reached maximum number of turns (1)"],
    "type": "result", "duration_ms": 1269,
}  # fmt: skip

FAKE_CLAUDE = """#!{python}
import json, os, sys
if "--version" in sys.argv:
    print("2.0.0"); sys.exit(0)
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
print(json.dumps({{"type": "system", "subtype": "init", "session_id": "sess-1"}}), flush=True)
print(json.dumps({{"type": "assistant", "session_id": "sess-1", "message": {{"id": "m1",
      "content": [{{"type": "text", "text": "working"}}],
      "usage": {{"input_tokens": 1, "output_tokens": 1}}}}}}), flush=True)
res = os.environ.get("FAKE_RESULT")
if res:
    rec = json.loads(res)
else:
    rec = {{"type": "result", "subtype": "success", "is_error": False, "result": "done",
            "session_id": "sess-1", "num_turns": 1, "duration_ms": 5,
            "total_cost_usd": float(os.environ.get("FAKE_COST", "0.01")),
            "usage": {{"input_tokens": 10, "output_tokens": 20}}}}
print(json.dumps(rec), flush=True)
sys.exit(1 if rec.get("is_error") else 0)
"""


def fake_claude(d: Path) -> str:
    exe = d / "claude"
    exe.write_text(FAKE_CLAUDE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def invocations(log: Path) -> list[list[str]]:
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def flag(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def run_build(tmp_path: Path, env: dict[str, str], **table: Any) -> tuple[dict[str, Any], Path]:
    root = seed_project(tmp_path)
    exe = fake_claude(tmp_path)
    log = tmp_path / "claude.log"
    t = {**load_table(root), "agent": "claude", **table}
    rb = Rebuilder(root, t, name="notes", model="sonnet", claude_bin=exe,
                   env={**os.environ, "FAKE_LOG": str(log), **env})  # fmt: skip
    rid = rb.start([], scope={"area": "writing-notes"}, baseline_wait=60)["id"]
    rec = rb.get(rid, wait=60)
    cleanup_worktrees(root)
    return rec, log


# ---- configuration -------------------------------------------------------------------------


def test_config_defaults_and_overrides(tmp_path: Path) -> None:
    cfg = RebuildConfig(tmp_path, {})
    assert cfg.max_turns == 150 and cfg.step_max_usd == 5.0 and cfg.max_budget_usd is None
    cfg = RebuildConfig(tmp_path, {"max_turns": 0, "step_max_usd": 0, "max_budget_usd": 0})
    assert cfg.max_turns is None and cfg.step_max_usd is None and cfg.max_budget_usd is None
    cfg = RebuildConfig(tmp_path, {"max_turns": 40, "step_max_usd": 1.5, "max_budget_usd": 3})
    assert cfg.max_turns == 40 and cfg.step_max_usd == 1.5 and cfg.max_budget_usd == 3.0
    cfg = RebuildConfig(tmp_path, {"max_turns": "x", "step_max_usd": "y", "max_budget_usd": "z"})
    assert cfg.max_turns == 150 and cfg.step_max_usd == 5.0 and cfg.max_budget_usd is None
    # the caps are not plan_defaults (that dict is pinned by the projects API)
    assert not {"max_turns", "step_max_usd", "max_budget_usd"} & set(cfg.plan_defaults())


def test_api_md_documents_the_keys_and_defaults() -> None:
    doc = API_MD.read_text(encoding="utf-8")
    assert "max_turns = 150" in doc
    assert "step_max_usd = 5.0" in doc
    assert "max_budget_usd" in doc and "budget_exhausted" in doc
    assert "`max_turns`" in doc and "`budget_cap`" in doc  # the causes


# ---- argv ----------------------------------------------------------------------------------


def argv(**kw: Any) -> list[str]:
    return build_rebuild_argv(claude_bin="claude", text="the prompt", model="sonnet",
                              mcp_config=Path("/m.json"), settings=Path("/s.json"),
                              gates=["./check.sh"], **kw)  # fmt: skip


def test_argv_without_caps_is_unchanged() -> None:
    a = argv()
    assert "--max-turns" not in a and "--max-budget-usd" not in a
    assert a[-2:] == ["--", "the prompt"]
    assert argv(max_turns=None, max_budget_usd=None) == a


def test_argv_caps_go_before_the_separator() -> None:
    a = argv(max_turns=150, max_budget_usd=0.7)
    sep = a.index("--")
    assert a[-2:] == ["--", "the prompt"]
    assert a.index("--max-turns") < sep and a.index("--max-budget-usd") < sep
    assert flag(a, "--max-turns") == "150"
    assert float(flag(a, "--max-budget-usd") or "nan") == pytest.approx(0.7)
    b = argv(max_turns=7)
    assert flag(b, "--max-turns") == "7" and "--max-budget-usd" not in b


# ---- the CLI's error results ---------------------------------------------------------------


def feed(rec: dict[str, Any]) -> list[dict[str, Any]]:
    t = Transcript()
    t.add_user("go", [])
    t.feed(rec)
    return t.events_after(0)


def test_transcript_maps_the_budget_subtype() -> None:
    ev = feed(P1_BUDGET)
    err = next(e for e in ev if e["type"] == "error")
    assert err["kind"] == "cli" and err["reason"] == "max_budget_usd"
    assert err["terminal_reason"] == "budget_exhausted"
    assert err["errors"] == ["Reached maximum budget ($0.0001)"]
    assert "Reached maximum budget" in err["message"]
    res = next(e for e in ev if e["type"] == "result")
    # the top-level usage is all zeros here: tokens and cost come from modelUsage
    assert res["ok"] is False and res["cost_usd"] == pytest.approx(0.00021693)
    assert res["output_tokens"] == 185 and res["input_tokens"] == 2
    assert res["cache_read_input_tokens"] == 4383 and res["cache_creation_input_tokens"] == 402


def test_transcript_maps_the_turns_subtype() -> None:
    ev = feed(P1_TURNS)
    err = next(e for e in ev if e["type"] == "error")
    assert err["kind"] == "cli" and err["reason"] == "max_turns"
    assert err["terminal_reason"] == "max_turns"
    res = next(e for e in ev if e["type"] == "result")
    assert res["output_tokens"] == 117 and res["cost_usd"] == pytest.approx(0.00018253)


def test_transcript_cost_from_model_usage_when_total_is_missing() -> None:
    rec = {k: v for k, v in P1_BUDGET.items() if k != "total_cost_usd"}
    res = next(e for e in feed(rec) if e["type"] == "result")
    assert res["cost_usd"] == pytest.approx(0.00021693)


def test_other_errors_have_no_reason() -> None:
    err = next(
        e
        for e in feed(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "session_id": "s",
            }
        )
        if e["type"] == "error"
    )
    assert err["kind"] == "cli" and "reason" not in err and err["message"] == "claude failed"


# ---- the rebuild loop with a fake claude ---------------------------------------------------


def test_default_caps_reach_the_agent(tmp_path: Path) -> None:
    rec, log = run_build(tmp_path, {}, max_rounds=1)
    (a,) = invocations(log)
    sep = a.index("--")
    assert flag(a, "--max-turns") == "150" and a.index("--max-turns") < sep
    assert float(flag(a, "--max-budget-usd") or "nan") == pytest.approx(5.0)
    assert rec["status"] == "gave_up"


def test_caps_off_reach_no_flag(tmp_path: Path) -> None:
    _, log = run_build(tmp_path, {}, max_rounds=1, max_turns=0, step_max_usd=0)
    (a,) = invocations(log)
    assert "--max-turns" not in a and "--max-budget-usd" not in a


def test_the_step_budget_is_the_build_budget_left(tmp_path: Path) -> None:
    rec, log = run_build(tmp_path, {"FAKE_COST": "0.30"}, max_rounds=2, max_budget_usd=1.0)
    a1, a2 = invocations(log)
    assert float(flag(a1, "--max-budget-usd") or "nan") == pytest.approx(1.0)
    assert float(flag(a2, "--max-budget-usd") or "nan") == pytest.approx(0.70)
    assert rec["usage"]["cost_usd_list_estimate"] == pytest.approx(0.60)


def test_no_spawn_below_the_minimum_left(tmp_path: Path) -> None:
    assert MIN_STEP_USD == 0.05
    rec, log = run_build(tmp_path, {"FAKE_COST": "0.97"}, max_rounds=3, max_budget_usd=1.0)
    assert len(invocations(log)) == 1  # 0.03 left after round 1: round 2 never spawns
    assert rec["status"] == "gave_up" and len(rec["rounds"]) == 2  # and no round 3
    err = rec["rounds"][1]["error"]
    assert err["reason"] == "budget_exhausted" and err["kind"] == "timeout"
    assert rec["error"]["reason"] == "budget_exhausted"
    assert rec["cause"] == "budget_cap"


def test_cli_budget_result_becomes_a_reasoned_round(tmp_path: Path) -> None:
    rec, log = run_build(tmp_path, {"FAKE_RESULT": json.dumps(P1_BUDGET)}, max_rounds=1)
    assert len(invocations(log)) == 1
    rnd = rec["rounds"][0]
    assert rnd["error"]["kind"] == "cli" and rnd["error"]["reason"] == "max_budget_usd"
    assert rnd["error"]["terminal_reason"] == "budget_exhausted"
    assert rnd["error"]["errors"] == ["Reached maximum budget ($0.0001)"]
    assert rnd["agent"]["cost_usd_list_estimate"] == pytest.approx(0.00021693)
    assert rnd["agent"]["output_tokens"] == 185
    assert rec["usage"]["cost_usd_list_estimate"] > 0
    assert rec["cause"] == "budget_cap"


def test_cli_turns_result_becomes_a_reasoned_round(tmp_path: Path) -> None:
    rec, _ = run_build(tmp_path, {"FAKE_RESULT": json.dumps(P1_TURNS)}, max_rounds=1)
    rnd = rec["rounds"][0]
    assert rnd["error"]["kind"] == "cli" and rnd["error"]["reason"] == "max_turns"
    assert "Reached maximum number of turns" in rnd["error"]["message"]
    assert rec["cause"] == "max_turns"


# ---- causes --------------------------------------------------------------------------------


def _rec(error: dict[str, Any], **rnd: Any) -> dict[str, Any]:
    rounds = [{"check": {"findings": [{"target": "a"}]}, "error": error, **rnd}]
    return {"status": "gave_up", "max_rounds": 3, "rounds": rounds}


@pytest.mark.parametrize(
    ("error", "rnd", "cause"),
    [
        ({"kind": "cli", "reason": "max_turns"}, {}, "max_turns"),
        ({"kind": "cli", "reason": "max_budget_usd"}, {}, "budget_cap"),
        ({"kind": "timeout", "reason": "budget_exhausted"}, {"status": "timeout"}, "budget_cap"),
        ({"kind": "timeout", "reason": "node_max_tokens"}, {"status": "timeout"}, "token_budget"),
        ({"kind": "timeout", "reason": "round_timeout"}, {"status": "timeout"}, "timeout"),
        ({"kind": "cli"}, {}, "other"),
    ],
)
def test_build_cause_names_the_caps(error: dict[str, Any], rnd: dict[str, Any],
                                    cause: str) -> None:  # fmt: skip
    got = build_cause(_rec(error, **rnd))
    assert got == cause and got in CAUSES
