"""Cache-inclusive cost accounting of the rebuild agent (gap G2, spike WP5): a price table
(:mod:`lattice.studio.costing`, all four token kinds, unknown model -> None), a killed step
records the cost its stream showed (``partial``, ``cost_basis: stream_estimate``) instead of
0, and ``[rebuild] node_max_usd`` stops a plan node's agent in-stream on that estimate
(``reason: node_max_usd``, cause ``budget_cap``). A FAKE claude; no LLM.

The replay tests use REAL ``claude -p`` streams (copied verbatim, not edited):

* ``tests/fixtures/cost_replay/pf-p1-*-haiku-2.1.309.jsonl``: the pre-flight P1 raw streams
  (2026-10-30, CLI 2.1.309, claude-haiku-5-5; plain stream-json as the rebuild runs it).
* ``tests/fixtures/cost_replay/chat-r2-abl-R2-{N2,MM3}-sonnet-2.1.303.jsonl``: chat round 2
  streams from ``docs/results/data/2026-10-18-goto-chat-round2/scored/agent/streams/``
  (CLI 2.1.303, claude-sonnet-5-5, with ``--include-partial-messages``).
* ``tests/studio/fixtures/claude-2.1.296-{ask,guard-deny}.jsonl`` (already in the repo).

Finding (every archived real stream with a result event, 2026-10-31): the assistant
records' input and cache token counts equal the result's exactly, but their
``output_tokens`` is the count when the record was written, before the message ended; only
the ``message_delta`` stream events of ``--include-partial-messages`` carry the final count.
With them the stream estimate equals ``total_cost_usd`` (64/64 streams), so the rebuild now
passes that flag; legacy streams without it give a lower bound, within 5% on 69/126 streams
and up to 42% under on short haiku runs. Two more plain streams are ``--resume`` steps of
one session (``docs/results/data/2026-10-18-grpc-arms-m-l/armL-run1/ledger/logs/``
``agent-0{2,3}.jsonl``): their ``total_cost_usd`` and ``modelUsage`` are the session's
cumulative figures, while the top-level ``usage`` is the step's own; the R2 pre-flight
(``tests/fixtures/cost_replay/resume/``, CLI 2.1.309) confirms it and shows that the CLI's
``--max-budget-usd`` on a resumed session counts only the new step."""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from lattice.improve.events import CAUSES
from lattice.improve.signals import build_cause
from lattice.studio.costing import (
    COST_BASIS_STREAM,
    PRICES,
    TOKEN_KINDS,
    estimate_cost,
    price_for,
)
from lattice.studio.rebuild import (
    RebuildConfig,
    Rebuilder,
    StreamUsage,
    build_rebuild_argv,
    load_table,
)
from tests.studio.seed import cleanup_worktrees, finish, manager, seed_project

ROOT = Path(__file__).resolve().parents[2]
API_MD = ROOT / "lattice" / "studio" / "API.md"
REPLAY = sorted((ROOT / "tests" / "fixtures" / "cost_replay").glob("*.jsonl")) + [
    ROOT / "tests" / "studio" / "fixtures" / "claude-2.1.296-ask.jsonl",
    ROOT / "tests" / "studio" / "fixtures" / "claude-2.1.296-guard-deny.jsonl",
]
CAMEL = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")


# ---- the price table -----------------------------------------------------------------------


def test_price_rows_by_longest_prefix() -> None:
    assert price_for("claude-sonnet-5-5") == PRICES["claude-sonnet-5-5"]
    assert price_for("claude-sonnet-5-5-20261001") == PRICES["claude-sonnet-5-5"]
    assert price_for("claude-opus-5-5") == PRICES["claude-opus-5-5"]
    assert price_for("claude-opus-5-1") == PRICES["claude-opus-5"]
    assert PRICES["claude-sonnet-5-5"].verified and PRICES["claude-haiku-5-5"].verified
    assert not PRICES["claude-opus-5-5"].verified
    # input/output of the unverified rows are bench/metrics.py's
    assert PRICES["claude-opus-5-5"][:2] == (4.00, 20.00)
    assert PRICES["claude-haiku-4-5"][:2] == (1.00, 5.00)


@pytest.mark.parametrize("model", ["", None, "sonnet", "gpt-5", "<synthetic>", "claude-x"])
def test_unknown_model_has_no_estimate(model: str | None) -> None:
    assert price_for(model) is None
    assert estimate_cost(model, {"input_tokens": 10, "output_tokens": 10}) is None
    assert estimate_cost(model, {}) == 0.0  # nothing used costs nothing on any model


def test_estimate_includes_all_four_token_kinds() -> None:
    m = 1_000_000
    p = PRICES["claude-sonnet-5-5"]
    for i, k in enumerate(TOKEN_KINDS):
        assert estimate_cost("claude-sonnet-5-5", {k: m}) == pytest.approx(p[i])
    assert estimate_cost("claude-sonnet-5-5", dict.fromkeys(TOKEN_KINDS, m)) == pytest.approx(
        2.00 + 10.00 + 0.10 + 4.00
    )
    # the cache dominates an agent's cost: ignoring it understates by far more than 2x
    agentic = {"input_tokens": 6, "output_tokens": 2_000, "cache_read_input_tokens": 900_000,
               "cache_creation_input_tokens": 60_000}  # fmt: skip
    full = estimate_cost("claude-sonnet-5-5", agentic) or 0.0
    no_cache = estimate_cost("claude-sonnet-5-5", {"input_tokens": 6, "output_tokens": 2_000})
    assert full > 10 * (no_cache or 0.0)


# ---- replay over real streams --------------------------------------------------------------


def records(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def result_of(recs: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((r for r in reversed(recs) if r.get("type") == "result"), None)


def replay(path: Path) -> tuple[StreamUsage, dict[str, Any], bool]:
    su = StreamUsage()
    recs = records(path)
    for r in recs:
        su.feed(r)
    partial = any(
        r.get("type") == "stream_event" and (r.get("event") or {}).get("type") == "message_delta"
        for r in recs
    )
    res = result_of(recs)
    assert res is not None and su.messages, path.name
    return su, res, partial


def test_the_replay_streams_are_real_and_small() -> None:
    assert len(REPLAY) == 7
    for p in REPLAY:
        assert p.stat().st_size < 64_000, p.name
        init = next(r for r in records(p) if r.get("subtype") == "init")
        assert str(init.get("claude_code_version") or "").startswith("2.1."), p.name


def test_table_matches_the_cli_cost_of_real_result_events() -> None:
    """The table on each result's own ``modelUsage`` tokens gives the CLI's ``costUSD``."""
    seen: dict[str, int] = {}
    for p in REPLAY:
        res = result_of(records(p))
        assert res is not None
        for model, mu in res["modelUsage"].items():
            usage = {k: mu.get(c) for k, c in zip(TOKEN_KINDS, CAMEL, strict=True)}
            assert estimate_cost(model, usage) == pytest.approx(mu["costUSD"], rel=1e-9), p.name
            seen[model] = seen.get(model, 0) + 1
    # at least 3 real result events per verified row (fewer: UNVERIFIED, not a pass)
    assert seen.get("claude-sonnet-5-5", 0) >= 3 and seen.get("claude-haiku-5-5", 0) >= 3, seen


def test_replay_with_partial_messages_is_within_5_percent() -> None:
    """Streams with ``message_delta`` events: the stream estimate is the CLI's cost."""
    usable = [p for p in REPLAY if replay(p)[2]]
    if len(usable) < 3:
        pytest.skip(f"UNVERIFIED: only {len(usable)} real partial-message stream(s)")
    for p in usable:
        su, res, _ = replay(p)
        est = su.cost_estimate()
        assert est is not None, p.name
        assert est == pytest.approx(res["total_cost_usd"], rel=0.05), p.name


def test_replay_legacy_streams_without_partial_messages_is_a_lower_bound() -> None:
    """Legacy streams without ``--include-partial-messages`` (the rebuild's before R2's
    follow-up): input and cache tokens are exact, output tokens are what the assistant
    records showed, so the estimate never exceeds the CLI's cost."""
    usable = [p for p in REPLAY if not replay(p)[2]]
    assert len(usable) >= 3
    for p in usable:
        su, res, _ = replay(p)
        t = su.totals()
        assert (
            t["input_tokens"] == res["usage"]["input_tokens"] or not res["usage"]["input_tokens"]
        ), p.name
        (mu,) = res["modelUsage"].values()
        assert t["cache_read_input_tokens"] == mu["cacheReadInputTokens"], p.name
        assert t["cache_creation_input_tokens"] == mu["cacheCreationInputTokens"], p.name
        assert 0 < t["output_tokens"] <= mu["outputTokens"], p.name
        est = su.cost_estimate()
        assert est is not None and 0 < est <= res["total_cost_usd"] * (1 + 1e-9), p.name


@pytest.mark.xfail(strict=True, reason="legacy streams without partial messages: the "
                   "assistant records' output_tokens are counted before the message ends "
                   "(up to 42% under on these haiku runs); the rebuild now passes "
                   "--include-partial-messages, see the module docstring")  # fmt: skip
def test_replay_legacy_streams_without_partial_messages_within_5_percent() -> None:
    for p in REPLAY:
        su, res, partial = replay(p)
        if not partial:
            assert su.cost_estimate() == pytest.approx(res["total_cost_usd"], rel=0.05), p.name


def test_the_rebuild_argv_asks_for_partial_messages() -> None:
    a = build_rebuild_argv(claude_bin="claude", text="go", model="sonnet",
                           mcp_config=Path("/m.json"), settings=Path("/s.json"),
                           gates=["./check.sh"])  # fmt: skip
    # right after --verbose: the pinned 4-element prefix of test_rebuild/test_mcp_intake holds
    assert a[1:6] == ["-p", "--output-format", "stream-json", "--verbose",
                      "--include-partial-messages"]  # fmt: skip
    assert a[-2:] == ["--", "go"]


# ---- StreamUsage ---------------------------------------------------------------------------


def asst(mid: str, model: str | None, **u: int) -> dict[str, Any]:
    msg: dict[str, Any] = {"id": mid, "usage": u}
    if model:
        msg["model"] = model
    return {"type": "assistant", "message": msg}


def test_stream_usage_prices_each_message_on_its_served_model() -> None:
    su = StreamUsage()
    su.feed({"type": "system", "subtype": "init", "model": "claude-haiku-5-5"})
    su.feed(asst("m1", "claude-sonnet-5-5", input_tokens=1, output_tokens=10,
                 cache_read_input_tokens=1000, cache_creation_input_tokens=100))  # fmt: skip
    su.feed(asst("m2", None, output_tokens=100))  # no model: the init record's
    su.feed({"type": "assistant", "parent_tool_use_id": "t1",  # a subagent's: not ours
             "message": {"id": "s1", "model": "claude-sonnet-5-5",
                         "usage": {"output_tokens": 10_000}}})  # fmt: skip
    want = (1 * 2 + 10 * 10 + 1000 * 0.1 + 100 * 4) / 1e6 + 100 * 0.5 / 1e6
    assert su.cost_estimate() == pytest.approx(want)
    assert su.output_tokens() == 110


def test_stream_usage_takes_the_final_output_count_of_a_message_delta() -> None:
    su = StreamUsage()
    su.feed(asst("m1", "claude-haiku-5-5", input_tokens=2, output_tokens=6,
                 cache_read_input_tokens=100))  # fmt: skip
    su.feed({"type": "stream_event", "api_message_id": "m1",
             "event": {"type": "message_delta", "usage": {"output_tokens": 173}}})  # fmt: skip
    su.feed(asst("m1", "claude-haiku-5-5", input_tokens=2, output_tokens=6,
                 cache_read_input_tokens=100))  # a later block of the same message  # fmt: skip
    assert su.totals()["output_tokens"] == 173


def test_stream_usage_unknown_model_is_none() -> None:
    su = StreamUsage()
    su.feed(asst("m1", None, output_tokens=5))
    assert su.cost_estimate() is None and su.cost_estimate("sonnet") is None
    assert su.cost_estimate("claude-sonnet-5-5") == pytest.approx(5 * 10 / 1e6)
    su.feed(asst("m2", "<synthetic>"))  # the CLI's synthetic message: no usage, no cost
    assert su.cost_estimate("claude-sonnet-5-5") == pytest.approx(5 * 10 / 1e6)


# ---- the rebuild loop with a fake claude ---------------------------------------------------

FAKE_CLAUDE = """#!{python}
import json, os, sys, time
if "--version" in sys.argv:
    print("2.0.0"); sys.exit(0)
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
model = os.environ.get("FAKE_MODEL", "")
print(json.dumps({{"type": "system", "subtype": "init", "session_id": "sess-1"}}), flush=True)
for i in range(3):
    u = {{"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1000,
         "cache_creation_input_tokens": 10}}
    msg = {{"id": "m%d" % i, "content": [{{"type": "text", "text": "working"}}], "usage": u}}
    if model:
        msg["model"] = model
    print(json.dumps({{"type": "assistant", "session_id": "sess-1", "message": msg}}),
          flush=True)
    time.sleep(0.2)
if os.environ.get("FAKE_FINISH"):
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
                      "result": "done", "session_id": "sess-1", "num_turns": 3,
                      "total_cost_usd": 0.5, "usage": {{"input_tokens": 300,
                      "output_tokens": 150}}}}), flush=True)
    sys.exit(0)
time.sleep(60)
"""

PER_MSG = (100 * 2.00 + 50 * 10.00 + 1000 * 0.10 + 10 * 4.00) / 1e6  # claude-sonnet-5-5


def fake_claude(d: Path) -> str:
    exe = d / "claude"
    exe.write_text(FAKE_CLAUDE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def invocations(log: Path) -> list[list[str]]:
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def build(tmp_path: Path, env: dict[str, str], **table: Any) -> dict[str, Any]:
    root = seed_project(tmp_path)
    exe = fake_claude(tmp_path)
    t = {**load_table(root), "agent": "claude", "max_rounds": 1, **table}
    rb = Rebuilder(root, t, name="notes", model="sonnet", claude_bin=exe,
                   env={**os.environ, "FAKE_LOG": str(tmp_path / "claude.log"), **env})  # fmt: skip
    rid = rb.start([], scope={"area": "writing-notes"}, baseline_wait=60)["id"]
    rec = rb.get(rid, wait=60)
    cleanup_worktrees(root)
    return rec


def test_a_killed_step_records_its_stream_estimate(tmp_path: Path) -> None:
    rec = build(tmp_path, {"FAKE_MODEL": "claude-sonnet-5-5"}, round_timeout=1.5)
    rnd = rec["rounds"][0]
    assert rnd["error"]["reason"] == "round_timeout"
    a = rnd["agent"]
    assert a["partial"] is True and a["cost_basis"] == COST_BASIS_STREAM == "stream_estimate"
    assert a["cost_usd_list_estimate"] == pytest.approx(3 * PER_MSG)
    assert a["cost_usd"] == pytest.approx(3 * PER_MSG)  # the same figure, as for a result
    assert rec["usage"]["cost_usd_list_estimate"] == pytest.approx(3 * PER_MSG)


def test_a_killed_step_on_an_unknown_model_is_unpriced(tmp_path: Path) -> None:
    rec = build(tmp_path, {}, round_timeout=1.5)  # no served model id; "sonnet" is an alias
    a = rec["rounds"][0]["agent"]
    assert a["partial"] is True and a["output_tokens"] == 150
    assert a["cost_basis"] == "unpriced" and a["cost_usd_list_estimate"] is None
    assert rec["usage"]["cost_usd_list_estimate"] == 0.0


def test_a_finished_step_keeps_the_cli_cost(tmp_path: Path) -> None:
    rec = build(tmp_path, {"FAKE_MODEL": "claude-sonnet-5-5", "FAKE_FINISH": "1"})
    a = rec["rounds"][0]["agent"]
    assert a["cost_basis"] == "cli_list_estimate" and "partial" not in a
    assert a["cost_usd_list_estimate"] == pytest.approx(0.5)


# ---- [rebuild] node_max_usd ----------------------------------------------------------------


def test_node_max_usd_config(tmp_path: Path) -> None:
    assert RebuildConfig(tmp_path, {}).node_max_usd is None
    assert RebuildConfig(tmp_path, {"node_max_usd": 0}).node_max_usd is None
    assert RebuildConfig(tmp_path, {"node_max_usd": "x"}).node_max_usd is None
    cfg = RebuildConfig(tmp_path, {"node_max_usd": 0.75})
    assert cfg.node_max_usd == 0.75
    assert "node_max_usd" not in cfg.plan_defaults()  # pinned by the projects API


def test_node_max_usd_stops_the_node_agent_in_stream(tmp_path: Path) -> None:
    root = seed_project(tmp_path)
    exe = fake_claude(tmp_path)
    log = tmp_path / "claude.log"
    cap = 1.5 * PER_MSG  # passed with the second message
    pm = manager(root, env={**os.environ, "FAKE_LOG": str(log),
                            "FAKE_MODEL": "claude-sonnet-5-5"},
                 agent="claude", node_max_usd=cap, max_rounds=3, node_timeout=30)  # fmt: skip
    pm.rb.claude_bin = exe
    pid = pm.create({"scope": {"rules": ["R3"]}})["id"]
    assert pm.get(pid, wait=30)["status"] == "ready"
    pm.approve(pid)
    pm.run(pid)
    rec = finish(pm, pid)
    slow = next(n for n in rec["nodes"] if n["id"] == "slow")
    b = pm.rb.get(slow["build_id"])
    rnd = b["rounds"][0]
    assert rnd["status"] == "timeout"
    assert rnd["error"]["kind"] == "timeout" and rnd["error"]["reason"] == "node_max_usd"
    assert "node_max_usd" in rnd["error"]["message"]
    assert rnd["agent"]["partial"] and rnd["agent"]["cost_basis"] == "stream_estimate"
    assert rnd["agent"]["cost_usd_list_estimate"] == pytest.approx(2 * PER_MSG)
    # no further round: the next one would pay for a model call before it could stop
    assert len(b["rounds"]) == 1 and b["status"] == "gave_up"
    assert b["error"]["reason"] == "node_max_usd"
    assert b["cause"] == "budget_cap" and slow["cause"] == "budget_cap"
    # a later step of the same node (a level repair) is not even spawned
    n_before = len(invocations(log))
    run = pm.rb.runs[slow["build_id"]]
    out = pm.rb._claude_step(run, 2, "repair")
    assert out["error"]["reason"] == "node_max_usd" and "not started" in out["error"]["message"]
    assert len(invocations(log)) == n_before
    cleanup_worktrees(root)


def test_node_max_usd_is_off_for_standalone_builds(tmp_path: Path) -> None:
    rec = build(tmp_path, {"FAKE_MODEL": "claude-sonnet-5-5", "FAKE_FINISH": "1"},
                node_max_usd=PER_MSG / 10)  # fmt: skip
    assert rec["rounds"][0].get("error") is None or (
        rec["rounds"][0]["error"].get("reason") != "node_max_usd"
    )


def test_build_cause_maps_node_max_usd_to_budget_cap() -> None:
    rounds = [{"check": {"findings": [{"target": "a"}]}, "status": "timeout",
               "error": {"kind": "timeout", "reason": "node_max_usd"}}]  # fmt: skip
    got = build_cause({"status": "gave_up", "max_rounds": 3, "rounds": rounds})
    assert got == "budget_cap" and got in CAUSES
    # node_max_tokens keeps its own reason and cause
    rounds[0]["error"] = {"kind": "timeout", "reason": "node_max_tokens"}
    assert build_cause({"status": "gave_up", "max_rounds": 3, "rounds": rounds}) == "token_budget"


def test_api_md_documents_node_max_usd() -> None:
    doc = API_MD.read_text(encoding="utf-8")
    assert "node_max_usd = 0" in doc and "`node_max_usd`" in doc
    assert "node_max_tokens` counts output tokens only" in doc
    assert 'cost_basis: "stream_estimate"' in doc and '"cli_list_estimate"' in doc
    assert "--verbose --include-partial-messages` in the worktree" in doc
    assert "session_totals" in doc and "counts only the new step" in doc
    assert 'reason: "node_max_usd"' in doc and "`node_max_usd`)" in doc  # the cause


# ---- the rebuild loop on REAL streams ------------------------------------------------------

RESUME = sorted((ROOT / "tests" / "fixtures" / "cost_replay" / "resume").glob("*.jsonl"))
PARTIAL = ROOT / "tests" / "fixtures" / "cost_replay" / "chat-r2-abl-R2-N2-sonnet-2.1.303.jsonl"

# replays round n's stream file verbatim (FAKE_STREAMS, ':'-separated); FAKE_CUT drops the
# result event and then hangs, as an agent that is killed before it finishes
REPLAY_CLAUDE = """#!{python}
import json, os, sys, time
if "--version" in sys.argv:
    print("2.0.0"); sys.exit(0)
d = os.environ["FAKE_DIR"]
n_path = os.path.join(d, "count")
n = int(open(n_path).read()) + 1 if os.path.exists(n_path) else 1
open(n_path, "w").write(str(n))
with open(os.path.join(d, "claude.log"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
files = os.environ["FAKE_STREAMS"].split(":")
code = 0
for line in open(files[min(n, len(files)) - 1]):
    rec = json.loads(line)
    if rec.get("type") == "result":
        if os.environ.get("FAKE_CUT"):
            time.sleep(60)
        code = 1 if rec.get("is_error") else 0
    print(line.strip(), flush=True)
sys.exit(code)
"""


def replay_build(tmp_path: Path, streams: list[Path], env: dict[str, str] | None = None,
                 **table: Any) -> tuple[dict[str, Any], list[list[str]]]:  # fmt: skip
    root = seed_project(tmp_path)
    exe = tmp_path / "claude"
    exe.write_text(REPLAY_CLAUDE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    t = {**load_table(root), "agent": "claude", **table}
    rb = Rebuilder(root, t, name="notes", model="haiku", claude_bin=str(exe),
                   env={**os.environ, "FAKE_DIR": str(tmp_path),
                        "FAKE_STREAMS": ":".join(map(str, streams)), **(env or {})})  # fmt: skip
    rid = rb.start([], scope={"area": "writing-notes"}, baseline_wait=60)["id"]
    rec = rb.get(rid, wait=90)
    cleanup_worktrees(root)
    return rec, invocations(tmp_path / "claude.log")


def test_a_killed_partial_message_step_books_the_cli_cost(tmp_path: Path) -> None:
    """End to end on a real partial-message stream cut before its result event: the
    in-process estimate is the CLI's own cost (the 5% bound, met here exactly)."""
    res = result_of(records(PARTIAL))
    assert res is not None
    rec, _ = replay_build(tmp_path, [PARTIAL], {"FAKE_CUT": "1"}, max_rounds=1,
                          round_timeout=3)  # fmt: skip
    a = rec["rounds"][0]["agent"]
    assert rec["rounds"][0]["error"]["reason"] == "round_timeout"
    assert a["partial"] is True and a["cost_basis"] == "stream_estimate"
    assert a["cost_usd_list_estimate"] == pytest.approx(res["total_cost_usd"], rel=0.05)


def test_resumed_steps_book_only_their_own_cost(tmp_path: Path) -> None:
    """The R2 pre-flight streams: A, then B and C resuming A's session. The CLI's
    ``total_cost_usd`` / ``modelUsage`` are cumulative on --resume; each round books the
    difference to the session's last total (= ``model_usage_since_last_result``)."""
    assert [p.name[:16] for p in RESUME] == ["pf-r2-resume-a-h", "pf-r2-resume-b-h",
                                            "pf-r2-resume-c-h"]  # fmt: skip
    results = [result_of(records(p)) for p in RESUME]
    rec, argvs = replay_build(tmp_path, RESUME, max_rounds=3, max_budget_usd=1.0)
    assert len(argvs) == 3 and [len(r) for r in [rec["rounds"]]] == [3]
    sid = results[0]["session_id"]
    assert "--resume" not in argvs[0] and argvs[1][argvs[1].index("--resume") + 1] == sid
    for rnd, res in zip(rec["rounds"], results, strict=True):
        since = next(iter((res.get("model_usage_since_last_result") or {}).values()), None)
        own = since["costUSD"] if since else res["total_cost_usd"]  # A: its own total
        assert rnd["agent"]["cost_usd_list_estimate"] == pytest.approx(own, abs=1e-8)
        assert rnd["agent"]["cost_basis"] == "cli_list_estimate"
        if since:  # tokens too, also where the top-level usage is all zeros (C)
            assert rnd["agent"]["output_tokens"] == since["outputTokens"]
            assert rnd["agent"]["cache_read_input_tokens"] == since["cacheReadInputTokens"]
    assert rec["rounds"][2]["error"]["reason"] == "max_budget_usd"
    assert rec["usage"]["cost_usd_list_estimate"] == pytest.approx(
        results[-1]["total_cost_usd"], abs=1e-6
    )  # the session's real total, not 3 sums
    assert rec["session_totals"][sid]["cost_usd"] == pytest.approx(results[-1]["total_cost_usd"])
    # the CLI's --max-budget-usd counts the new step only (pre-flight R2): a resumed round's
    # cap is the build budget left, no session cost added
    left = 1.0 - results[0]["total_cost_usd"]
    assert float(argvs[1][argvs[1].index("--max-budget-usd") + 1]) == pytest.approx(left, abs=1e-4)


def test_a_result_without_cumulative_figures_is_the_steps_own(tmp_path: Path) -> None:
    """A producer whose results carry no cumulative ``modelUsage`` (the suites' fakes) keeps
    booking each result as it is, even on a resumed session."""
    rec = build(tmp_path, {"FAKE_MODEL": "claude-sonnet-5-5", "FAKE_FINISH": "1"},
                max_rounds=2)  # fmt: skip
    assert [r["agent"]["cost_usd_list_estimate"] for r in rec["rounds"]] == [0.5, 0.5]
    assert rec["usage"]["cost_usd_list_estimate"] == pytest.approx(1.0)


# ---- the planner -----------------------------------------------------------------------------

FAKE_PLANNER = """#!{python}
import json, time
print(json.dumps({{"type": "system", "subtype": "init", "session_id": "s"}}), flush=True)
print(json.dumps({{"type": "assistant", "message": {{"id": "p1", "model": "claude-sonnet-5-5",
      "content": [{{"type": "text", "text": "thinking"}}],
      "usage": {{"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1000,
                 "cache_creation_input_tokens": 10}}}}}}), flush=True)
time.sleep(60)
"""


def test_a_cut_off_planner_call_books_its_stream_estimate(tmp_path: Path) -> None:
    root = seed_project(tmp_path)
    exe = tmp_path / "claude"
    exe.write_text(FAKE_PLANNER.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    pm = manager(root, planner="agent", planner_timeout=1.5)
    pm.rb.claude_bin = str(exe)
    pid = pm.create({"scope": "all"})["id"]
    rec = pm.get(pid, wait=60)
    t0 = time.time()
    while rec["status"] == "planning" and time.time() - t0 < 60:
        rec = pm.get(pid, wait=30)
    pu = rec["planner_usage"]
    assert pu["calls"] >= 1 and pu["tokens_out"] == 50 * pu["calls"]
    assert pu["cost_usd_list_estimate"] == pytest.approx(PER_MSG * pu["calls"], abs=1e-6)
    assert pu["cost_usd_list_estimate"] > 0
