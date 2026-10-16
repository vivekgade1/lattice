"""Real-agent exercise P3 of the consumer-side draft (slice 4d, ``lattice contracts questions
draft`` / the MCP tool ``linked_question_draft``): section 3, P3, and Amendment 1 C, D of
``docs/results/2026-10-15-consumer-draft-4c-prereg.md``.

    # 1. the scorer cases, then the freeze (hashes and `claude --version`), before any session
    python dogfood/contracts/draft_agent.py --write-freeze --out OUT
    # 2. the sessions (refuses when a frozen file differs from the freeze)
    python dogfood/contracts/draft_agent.py --scratch DIR --out OUT

Model calls go through the `claude` CLI only (``cli_env`` strips every ANTHROPIC* variable, no API
key). Worlds: W-D2 (ci-bot with the planted spec, the D2 question lq1) and W-S6 (streak-widget, no
spec, the Streak D6 question lq1). Sessions: sonnet, 3 per world with the tool (scored) and 2 W-D2
without it (ablation, information only), at most USD 0.50 and 8 turns each, USD 3.00 in all, abort
if the first three exceed USD 1.00, abort above 400k tokens in total. A usage-limit or rate-limit
answer ends the exercise: the remaining sessions are recorded NOT RUN (usage limit), never retried
and never scored as a miss.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
FROZEN = ("draft_agent_scorer.py", "draft_agent_scorer_cases.json", "draft_agent_check.py")
FREEZE_FILE = "scorer-freeze.json"
PROMPT = (
    "I own the {consumer} project. A provider's change broke it. Tell me exactly what to change "
    "in {consumer}, including its spec. Do not change anything."
)
DRAFT_TOOL = "mcp__lattice__linked_question_draft"
MODEL = "sonnet"
SESSION_CAP = 0.50
TOTAL_CAP = 3.0
FIRST_THREE_CAP = 1.0
TOKENS_CAP = 400_000
MAX_TURNS = "8"
MAX_BUDGET = "0.50"
PLAN = [  # (sid, world, with the tool): the first three decide the early abort
    ("sonnet-WD2-1", "W-D2", True),
    ("sonnet-WS6-1", "W-S6", True),
    ("sonnet-WD2-2", "W-D2", True),
    ("sonnet-WS6-2", "W-S6", True),
    ("sonnet-WD2-3", "W-D2", True),
    ("sonnet-WS6-3", "W-S6", True),
    ("ablation-WD2-1", "W-D2", False),
    ("ablation-WD2-2", "W-D2", False),
]


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def frozen_hashes() -> dict[str, str]:
    return {n: sha_file(HERE / n) for n in FROZEN}


def claude_version() -> str:
    r = subprocess.run(["claude", "--version"], capture_output=True, text=True, check=False)
    return r.stdout.strip() or r.stderr.strip()


def write_freeze(out: Path) -> int:
    chk = subprocess.run([sys.executable, str(HERE / "draft_agent_check.py")],
                         capture_output=True, text=True, check=False)  # fmt: skip
    last = chk.stdout.strip().splitlines()[-1] if chk.stdout.strip() else ""
    doc = {
        "frozen_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": frozen_hashes(),
        "runner_info_only": {
            n: sha_file(HERE / n) for n in ("draft_agent.py", "draft_probes.py")
        },  # fmt: skip
        "claude_version": claude_version(),
        "scorer_cases": last,
        "scorer_cases_rc": chk.returncode,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / FREEZE_FILE).write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps(doc, indent=1))
    return 0 if chk.returncode == 0 else 1


def verify_freeze(out: Path) -> str | None:
    p = out / FREEZE_FILE
    if not p.is_file():
        return f"no {FREEZE_FILE} in {out}: run --write-freeze first"
    doc = json.loads(p.read_text())
    if doc.get("scorer_cases_rc") != 0:
        return "the scorer cases did not pass at freeze time"
    now = frozen_hashes()
    bad = [n for n in FROZEN if now[n] != doc["files"].get(n)]
    return f"frozen file(s) changed since the freeze: {bad}" if bad else None


# ---- the worlds ---------------------------------------------------------------------------------


def build_world(env: Any, dp: Any, lq: Any, ep: Any, sid: str, world: str) -> dict[str, Any]:
    name = f"A-{sid}"
    if world == "W-D2":
        ctx, provider, cand = env.jc, "scheduler", env.h.jref("D2")
        m = ctx.workspace(name, ep.shared(ctx))
        spec = True
    else:
        ctx, provider, cand = env.sc, "streak", env.h.sref("D6")
        m = ctx.workspace(name, ep.shared(ctx))
        spec = False
    consumer_dir = lq.cdir(ctx, m)
    dp.consumer_files(consumer_dir, spec)
    lq.init_state(ctx, m)
    n = lq.notify(ctx, m, provider, cand)
    row = lq.first_row(n)
    d = ctx.contracts(m, "questions", "draft", ctx.consumer, "lq1", "--json")
    draft = json.loads(d.out)["text"] if d.rc == 0 and d.out.strip().startswith("{") else ""
    corpus = "\n".join(
        [
            draft,
            (consumer_dir / lq.IMPORTS).read_text(),
            (consumer_dir / "REQUIREMENTS.md").read_text() if spec else "",
        ]
    )
    ok = row["action"] == "created" and row["question"] == "lq1" and bool(draft)
    return {"name": name, "ctx": ctx, "manifest": m, "consumer_dir": consumer_dir,
            "consumer": ctx.consumer, "world_dir": ctx.work / name, "provider": provider,
            "provider_clone": ep.shared(ctx), "draft_text": draft, "corpus": corpus,
            "ok": ok, "pre": {"action": row["action"], "question": row["question"],
                              "draft_rc": d.rc}}  # fmt: skip


def argv_for(ap: Any, mcp_json: Path, consumer: str, with_tool: bool, max_turns: bool) -> list[str]:
    allowed = ap.ALLOWED + (f",{DRAFT_TOOL}" if with_tool else "")
    disallowed = ap.DISALLOWED + ("" if with_tool else f",{DRAFT_TOOL}")
    argv = [
        "claude", "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
        "--setting-sources", "", "--disable-slash-commands", "--mcp-config", str(mcp_json),
        "--tools", "", "--permission-mode", "dontAsk", "--allowedTools", allowed,
        "--disallowedTools", disallowed,
    ]  # fmt: skip
    if max_turns:
        argv += ["--max-turns", MAX_TURNS]
    argv += ["--max-budget-usd", MAX_BUDGET, "--model", MODEL, "--",
             PROMPT.format(consumer=consumer)]  # fmt: skip
    return argv


def tokens_of(res: dict[str, Any] | None, cache_reads: bool = False) -> int | None:
    """The tokens the abort rule counts (Amendment 2): input + output + cache creation; cache
    reads only with `cache_reads` (reported beside, never counted against the 400k cap)."""
    u = (res or {}).get("usage")
    if not isinstance(u, dict):
        return None
    keys = ["input_tokens", "cache_creation_input_tokens", "output_tokens"]
    if cache_reads:
        keys.append("cache_read_input_tokens")
    return sum(int(u.get(k) or 0) for k in keys)


def main(argv: list[str] | None = None) -> int:
    ap_ = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap_.add_argument("--scratch", type=Path, default=None)
    ap_.add_argument("--out", required=True, type=Path)
    ap_.add_argument("--write-freeze", action="store_true")
    ap_.add_argument("--dry-run", action="store_true",
                     help="build the first world and print its argv; call no model")  # fmt: skip
    ns = ap_.parse_args(argv)
    out = ns.out.resolve()
    if ns.write_freeze:
        return write_freeze(out)
    why = verify_freeze(out)
    if why:
        print(f"refused: {why}", file=sys.stderr)
        return 2
    if ns.scratch is None:
        print("--scratch DIR is required", file=sys.stderr)
        return 2
    ap = _load("lattice_contracts_agent_probes_draft", HERE / "agent_probes.py")
    dp = _load("lattice_contracts_draft_probes_agent", HERE / "draft_probes.py")
    ds = _load("lattice_contracts_draft_agent_scorer", HERE / "draft_agent_scorer.py")
    lq, ep = dp.lq, dp.ep
    out.mkdir(parents=True, exist_ok=True)
    env = ap.Env(ns.scratch, out)
    freeze = json.loads((out / FREEZE_FILE).read_text())
    version = claude_version()
    state: dict[str, Any] = {"spent": 0.0, "tokens": 0, "max_turns": True, "stop": None}
    sessions: list[dict[str, Any]] = []
    t0 = time.time()

    def save() -> None:
        (out / "sessions.json").write_text(json.dumps(sessions, indent=1, default=str) + "\n")

    for idx, (sid, world, with_tool) in enumerate(PLAN):
        rec: dict[str, Any] = {"sid": sid, "world": world, "with_tool": with_tool,
                               "status": "NOT RUN", "reason": None}  # fmt: skip
        sessions.append(rec)
        if state["stop"]:
            rec["reason"] = state["stop"]
            save()
            continue
        if state["spent"] + SESSION_CAP > TOTAL_CAP:
            state["stop"] = "total cap (USD 3.00)"
            rec["reason"] = state["stop"]
            save()
            continue
        sdir = out / sid
        if sdir.exists():
            shutil.rmtree(sdir)
        sdir.mkdir(parents=True)
        w = build_world(env, dp, lq, ep, sid, world)
        rec["pre"] = w["pre"]
        if not w["ok"]:
            rec["status"] = "NOT RUN"
            rec["reason"] = f"world defect: {w['pre']}"
            state["stop"] = rec["reason"]
            save()
            continue
        (sdir / "draft-text.txt").write_text(w["draft_text"] + "\n")
        s_run = env.root / "sessions" / sid
        (s_run / "cwd").mkdir(parents=True, exist_ok=True)
        (s_run / "tmp").mkdir(parents=True, exist_ok=True)
        mcp_json = s_run / "mcp.json"
        mcp_json.write_text(json.dumps(ap.mcp_config(w["manifest"], w["consumer_dir"]), indent=1))
        shutil.copy2(mcp_json, sdir / "mcp.json")
        cenv = ap.child_env(s_run)
        assert not any(k.startswith("ANTHROPIC") for k in cenv)
        roots = [w["world_dir"], w["provider_clone"]]
        before = dp.fsnap(roots)
        outside_before = ap.snapshot_outside(out, s_run / "cwd", sid)
        cmd = argv_for(ap, mcp_json, w["consumer"], with_tool, state["max_turns"])
        (sdir / "argv.json").write_text(json.dumps(cmd, indent=1) + "\n")
        if ns.dry_run:
            print(json.dumps(cmd, indent=1))
            rec["reason"] = "dry run"
            save()
            break
        run = ap.launch(cmd, cenv, s_run / "cwd", sdir)
        stderr = (sdir / "stderr.txt").read_text(errors="replace")
        recs = ap.parse_stream(sdir / "transcript.jsonl")
        if (
            state["max_turns"]
            and ap.init_of(recs) is None
            and run["rc"] != 0
            and re.search(r"unknown option|max-turns", stderr, re.I)
            and not ap.usage_limit(recs, stderr)
        ):
            state["max_turns"] = False  # the pre-registered fallback of round 3
            cmd = argv_for(ap, mcp_json, w["consumer"], with_tool, False)
            run = ap.launch(cmd, cenv, s_run / "cwd", sdir)
            stderr = (sdir / "stderr.txt").read_text(errors="replace")
            recs = ap.parse_stream(sdir / "transcript.jsonl")
        subprocess.run(["pkill", "-f", "--", f"--project {w['consumer_dir']}"], check=False)
        after = dp.fsnap(roots)
        outside_after = ap.snapshot_outside(out, s_run / "cwd", sid)
        res, ini = ap.result_of(recs), ap.init_of(recs)
        cost = res.get("total_cost_usd") if res else None
        toks = tokens_of(res)
        toks_all = tokens_of(res, cache_reads=True)
        limit = ap.usage_limit(recs, stderr)
        infra = ap.infra_failure(recs, run)
        state["spent"] += cost if cost is not None else SESSION_CAP
        state["tokens"] += toks or 0
        rec.update(
            run=run, cost_usd=cost, tokens=toks,
            tokens_incl_cache_reads=toks_all,
            turns=(res or {}).get("num_turns"),
            model_resolved=(ini or {}).get("model"),
            claude_version=version, usage_limit=limit, infra_failure=infra,
            world_changed=sorted(
                k for k in set(before) | set(after) if before.get(k) != after.get(k)
            ),
            outside_same=outside_before == outside_after,
            started_utc=datetime.now(UTC).isoformat(timespec="seconds"),
        )  # fmt: skip
        if limit or infra == "rate_limit_or_overloaded":
            rec["status"] = "NOT RUN (usage limit)"
            rec["reason"] = "usage/rate limit answer; recorded, not retried, not scored"
            state["stop"] = "NOT RUN (usage limit)"
            save()
            continue
        if infra is not None:
            rec["status"] = "NOT SCORED (infra)"
            rec["reason"] = infra
            save()
            continue
        answer = ap.answer_of(recs)
        (sdir / "answer.txt").write_text(answer + "\n")
        trace = ap.tool_trace(recs)
        denied = {d.get("tool_use_id") for d in (res or {}).get("permission_denials", []) or []}
        sc = ds.score(world, answer, trace, w["corpus"], with_tool, denied)
        sc["items"]["A6"] = not rec["world_changed"] and rec["outside_same"]
        sc["detail"]["A6"] = {"world_changed": rec["world_changed"][:10],
                              "outside_same": rec["outside_same"]}  # fmt: skip
        applicable = {k: v for k, v in sc["items"].items() if v is not None}
        sc["applicable"] = sorted(applicable)
        sc["pass"] = all(applicable.values())
        rec.update(status="SCORED", score=sc,
                   tools=[{"name": t["name"], "is_error": bool(t.get("is_error")),
                           "input": t.get("input")} for t in trace])  # fmt: skip
        (sdir / "score.json").write_text(json.dumps(sc, indent=1, default=str) + "\n")
        (sdir / "session.json").write_text(json.dumps(rec, indent=1, default=str) + "\n")
        save()
        if idx == 2 and state["spent"] > FIRST_THREE_CAP:
            state["stop"] = f"first three sessions cost USD {state['spent']:.2f} > 1.00"
        if state["tokens"] > TOKENS_CAP:
            state["stop"] = f"tokens {state['tokens']} > {TOKENS_CAP}"
    save()
    scored = [s for s in sessions if s["status"] == "SCORED"]
    tool_sessions = [s for s in scored if s["with_tool"]]
    abl = [s for s in scored if not s["with_tool"]]
    n_pass = sum(1 for s in tool_sessions if s["score"]["pass"])
    a6 = [s["score"]["items"]["A6"] for s in scored]
    items: dict[str, str] = {}
    for item in ("A1", "A2", "A3", "A4", "A5", "A7"):
        rel = [s for s in tool_sessions if s["score"]["items"].get(item) is not None]
        items[item] = f"{sum(1 for s in rel if s['score']['items'][item])}/{len(rel)}"
    abl_items = {}
    for item in ("A2", "A3", "A4", "A5", "A7"):
        rel = [s for s in abl if s["score"]["items"].get(item) is not None]
        abl_items[item] = f"{sum(1 for s in rel if s['score']['items'][item])}/{len(rel)}"
    evaluated = len(tool_sessions) == 6
    rule = (n_pass >= 5 and all(a6)) if evaluated else None
    summary = {
        "experiment": "2026-10-15-consumer-draft-4d P3",
        "claude_version": version,
        "freeze": freeze,
        "scorer_hashes_now": frozen_hashes(),
        "sessions": [
            {k: v for k, v in s.items() if k not in ("score", "tools", "run")}
            | {
                "items": (s.get("score") or {}).get("items"),
                "tools": [
                    t["name"].replace("mcp__lattice__", "") + ("!" if t["is_error"] else "")
                    for t in s.get("tools", [])
                ],
            }  # fmt: skip
            for s in sessions
        ],
        "scored": len(scored),
        "tool_sessions_scored": len(tool_sessions),
        "tool_sessions_pass": n_pass,
        "items_with_tool": items,
        "items_ablation": abl_items,
        "A6": f"{sum(1 for x in a6 if x)}/{len(a6)}",
        "pass_rule_evaluated": evaluated,
        "pass_rule_met": rule,
        "total_cost_usd": round(state["spent"], 4),
        "total_tokens": state["tokens"],
        "token_definition": "input + output + cache creation (cache reads excluded), "
        "Amendment 2; cap 400000",
        "total_tokens_incl_cache_reads": sum(
            s.get("tokens_incl_cache_reads") or 0 for s in sessions
        ),
        "stop": state["stop"],
        "wall_s": round(time.time() - t0, 1),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "freeze"}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
