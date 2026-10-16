"""Agent exercise 2 of the implementation diff (PQ2): D8 and Amendment 1 of
``docs/results/2026-10-16-implementation-diff-round2-prereg.md``.

    # 0. the keys, read off the frozen reports by K1-K4 (no model); a differing key is a finding
    python dogfood/implementation_agent2.py --derive --scratch DIR --out OUT --workflows-dyn WF
    # 1. the scorer cases, then the freeze (hashes, `claude --version`, the workflows hash)
    python dogfood/implementation_agent2.py --write-freeze --out OUT --workflows-dyn WF
    # 2. the sessions (refuses when a frozen file or the workflows differ from the freeze)
    python dogfood/implementation_agent2.py --scratch DIR --out OUT --workflows-dyn WF

Model calls go through the ``claude`` CLI only: the child environment has no ``ANTHROPIC*`` variable
and no API key is read. Round 1's runner (``implementation_agent.py``, frozen) supplies the helpers
(worlds, snapshots, the stream parser, usage-limit detection); this file adds the round-2 prompt,
questions, keys, scorer (``implementation_agent2_scorer.py``) and caps: USD 0.50 and 8 turns per
session, USD 5.00 in all, abort if the first four sessions cost more than USD 1.50, abort above
1.0M tokens counted as input + output + cache creation (cache reads excluded). A usage or rate
limit records the rest NOT RUN (usage limit): never retried, never scored as a miss.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for p in (str(REPO), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)
import implementation_agent as R1  # noqa: E402  (round 1's runner: frozen, imported)
import implementation_agent2_scorer as S  # noqa: E402

CASES = json.loads((HERE / "implementation_agent2_scorer_cases.json").read_text())
QUESTIONS_FILE = (
    REPO / "docs/results/data/2026-10-16-implementation-diff-round2/agent2_questions.json"
)
QUESTIONS_SHA = "bf06b9e92defaa7ed38365f056492e400651ba4bd0c359100f79e37e4a21a694"
FROZEN = (
    "implementation_agent2.py",
    "implementation_agent2_scorer.py",
    "implementation_agent2_check.py",
    "implementation_agent2_scorer_cases.json",
    "implementation_agent.py",  # round 1's runner, imported
    "implementation_agent_scorer.py",
    "contracts/agent_scorer.py",
)
FREEZE_FILE = "freeze2.json"
PROMPT = CASES["prompt"]
MODEL = "sonnet"
MAX_TURNS = "8"
MAX_BUDGET = "0.50"
SESSION_CAP = 0.50
TOTAL_CAP = 5.00
FIRST_FOUR_CAP = 1.50
TOKENS_CAP = 1_000_000
QUESTIONS = {q["id"]: q for q in CASES["questions"]}
PLAN = [(q["id"], q["id"], True) for q in CASES["questions"]] + [
    (f"ablation-{qid}", qid, False) for qid in CASES["ablation"]
]


def sha_file(p: Path) -> str:
    return R1.sha_file(p)


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def questions_file_ok() -> str | None:
    doc = json.loads(QUESTIONS_FILE.read_text())
    got = sha_file_bytes(canon({"questions": doc["questions"], "ablation": doc["ablation"]}))
    return None if got == QUESTIONS_SHA else f"agent2_questions.json hashes to {got}"


def sha_file_bytes(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def frozen_hashes() -> dict[str, str]:
    out = {n: sha_file(HERE / n) for n in FROZEN}
    out["agent2_questions.json"] = sha_file(QUESTIONS_FILE)
    return out


# ---- the keys (K1-K4), read off the frozen reports --------------------------------------------


def world_report(world: dict[str, Any]) -> dict[str, Any]:
    from lattice.spec.draft import load_spec
    from lattice.spec.implementation import build_report, load_inventory
    from lattice.spec.rules import Trace

    clone = world["clone"]
    spec, errs = load_spec((clone / "spec.py").read_text())
    assert spec is not None, errs
    trace = Trace.from_json((clone / "spec.trace.json").read_text())
    return build_report(spec, trace, load_inventory(world["inventory"], clone))


def derive_keys(worlds: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """K1 one implemented referent: VERIFIED iff its route is implemented_verified in the frozen
    report, else UNVERIFIED. K2 a spec_only referent: REQUIRED-NOT-IMPLEMENTED, key rules = the
    rules whose `missing` has it. K3 neither an endpoint, a machine nor a rule covers it:
    NOT-SPECIFIED-NOT-IMPLEMENTED (the questions with no referent and no key elements). K4 the
    key elements named in the question file are all implemented endpoints: AMBIGUOUS."""
    reports = {name: world_report(w) for name, w in worlds.items()}
    doc = json.loads(QUESTIONS_FILE.read_text())
    out = []
    for q in doc["questions"]:
        rep = reports[q["world"]]
        row: dict[str, Any] = {
            "id": q["id"], "world": q["world"], "text": q["text"],
            "predicted": q["predicted_key"],
        }  # fmt: skip
        ref = q.get("referent")
        routes = {r["key"]: r for r in rep["routes"]}
        eps = {e["key"]: e["status"] for e in rep["endpoints"]}
        if ref:
            row["referent"] = ref
            if eps.get(ref) == "spec_only":
                row["key"] = "REQUIRED-NOT-IMPLEMENTED"
                row["key_rules"] = sorted(
                    (r["id"] for r in rep["rules"] if f"impl:route:{ref}" in r.get("missing", [])),
                    key=lambda x: int(x[1:]),
                )
            else:
                st = routes[ref]["status"]
                row["route_status"] = st
                row["key"] = (
                    "IMPLEMENTED-VERIFIED"
                    if st == "implemented_verified"
                    else "IMPLEMENTED-UNVERIFIED"
                )
        elif q.get("key_elements"):
            els = q["key_elements"]
            row["key"] = "AMBIGUOUS"
            row["key_elements"] = els
            row["elements_status"] = {e: eps.get(e) for e in els}
        else:
            row["key"] = "NOT-SPECIFIED-NOT-IMPLEMENTED"
        row["differs_from_prediction"] = row["key"] != q["predicted_key"]
        out.append(row)
    return out


# ---- the freeze ---------------------------------------------------------------------------------


def write_freeze(out: Path, workflows: Path) -> int:
    chk = subprocess.run(
        [sys.executable, str(HERE / "implementation_agent2_check.py")],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    last = chk.stdout.strip().splitlines()[-1] if chk.stdout.strip() else ""
    doc = {
        "frozen_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": frozen_hashes(),
        "workflows_dyn_sha256": sha_file(workflows),
        "report_sha256": CASES["report_sha256"],
        "questions_file_problem": questions_file_ok(),
        "claude_version": R1.claude_version(),
        "scorer_cases": last,
        "scorer_cases_rc": chk.returncode,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / FREEZE_FILE).write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps(doc, indent=1))
    return 0 if chk.returncode == 0 and doc["questions_file_problem"] is None else 1


def verify_freeze(out: Path, workflows: Path) -> str | None:
    p = out / FREEZE_FILE
    if not p.is_file():
        return f"no {FREEZE_FILE} in {out}: run --write-freeze first"
    doc = json.loads(p.read_text())
    if doc.get("scorer_cases_rc") != 0 or doc.get("questions_file_problem"):
        return "the scorer cases or the questions file did not pass at freeze time"
    now = frozen_hashes()
    bad = [n for n, h in now.items() if h != doc["files"].get(n)]
    if bad:
        return f"frozen file(s) changed since the freeze: {bad}"
    if sha_file(workflows) != doc.get("workflows_dyn_sha256"):
        return "workflows.json differs from the freeze"
    return questions_file_ok()


# ---- facts, argv --------------------------------------------------------------------------------


def world_facts(world: dict[str, Any]) -> dict[str, Any]:
    """Round 1's facts, plus what C4/C5/C8 need: each key's registration (extract file and line)
    and each rule's trace line."""
    from lattice.spec.implementation import load_inventory, route_key
    from lattice.spec.rules import Trace

    facts = R1.world_facts(world)
    clone = world["clone"]
    inv = load_inventory(world["inventory"], clone)
    reg: dict[str, tuple[str, int]] = {}
    for r in inv.extract["routes"]:
        reg.setdefault(
            route_key(r["method"], r["path"]), (inv.evidence_path(r["file"]), int(r["line"]))
        )
    trace = Trace.from_json((clone / "spec.trace.json").read_text())
    facts.update(
        reg=reg,
        rule_lines={r.id: r.line for r in trace.rules},
        trace_source=trace.source,
    )
    return facts


def argv_for(mcp_json: Path, question: str, with_tools: bool) -> list[str]:
    allowed = R1.ALLOWED_BASE + (R1.IMPL if with_tools else [])
    disallowed = R1.DISALLOWED_BASE + ([] if with_tools else R1.IMPL)
    return [
        "claude", "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
        "--setting-sources", "", "--disable-slash-commands", "--mcp-config", str(mcp_json),
        "--tools", "Read,Grep,Glob", "--permission-mode", "dontAsk",
        "--allowedTools", ",".join(allowed), "--disallowedTools", ",".join(disallowed),
        "--max-turns", MAX_TURNS, "--max-budget-usd", MAX_BUDGET, "--model", MODEL, "--",
        PROMPT.replace("{Q}", question),
    ]  # fmt: skip


# ---- main ---------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scratch", type=Path, default=None)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--workflows-dyn", required=True, type=Path)
    ap.add_argument("--write-freeze", action="store_true")
    ap.add_argument("--derive", action="store_true", help="print the keys read off the reports")
    ap.add_argument("--dry-run", action="store_true", help="build the worlds, print argv, no model")
    ns = ap.parse_args(argv)
    out = ns.out.resolve()
    wf = ns.workflows_dyn.resolve()
    if ns.write_freeze:
        return write_freeze(out, wf)
    if ns.derive:
        if ns.scratch is None:
            print("--scratch DIR is required", file=sys.stderr)
            return 2
        worlds = R1.build_worlds(ns.scratch.resolve(), wf)
        print(json.dumps(derive_keys(worlds), indent=1))
        return 0
    why = verify_freeze(out, wf)
    if why:
        print(f"refused: {why}", file=sys.stderr)
        return 2
    if ns.scratch is None:
        print("--scratch DIR is required", file=sys.stderr)
        return 2
    scratch = ns.scratch.resolve()
    worlds = R1.build_worlds(scratch, wf)
    facts = {name: world_facts(w) for name, w in worlds.items()}
    for name, f in facts.items():
        if f["report_sha256"] != CASES["report_sha256"][name]:
            print(
                f"refused: the {name} report differs from the one the keys come from",
                file=sys.stderr,
            )
            return 2
    derived = {d["id"]: d for d in derive_keys(worlds)}
    for qid, q in QUESTIONS.items():
        if derived[qid]["key"] != q["key"]:
            print(f"refused: the key of {qid} differs from the report's", file=sys.stderr)
            return 2
    freeze = json.loads((out / FREEZE_FILE).read_text())
    version = R1.claude_version()
    state: dict[str, Any] = {"spent": 0.0, "tokens": 0, "stop": None}
    sessions: list[dict[str, Any]] = []
    t0 = time.time()

    def save() -> None:
        (out / "sessions.json").write_text(json.dumps(sessions, indent=1, default=str) + "\n")

    for idx, (sid, qid, with_tools) in enumerate(PLAN):
        q = QUESTIONS[qid]
        rec: dict[str, Any] = {
            "sid": sid, "question": qid, "world": q["world"], "key": q["key"],
            "with_tools": with_tools, "status": "NOT RUN", "reason": None,
        }  # fmt: skip
        sessions.append(rec)
        if state["stop"]:
            rec["reason"] = state["stop"]
            save()
            continue
        if state["spent"] + SESSION_CAP > TOTAL_CAP:
            state["stop"] = f"total cap (USD {TOTAL_CAP:.2f})"
            rec["reason"] = state["stop"]
            save()
            continue
        w = worlds[q["world"]]
        sdir = out / sid
        shutil.rmtree(sdir, ignore_errors=True)
        sdir.mkdir(parents=True)
        s_run = scratch / "sessions" / sid
        (s_run / "tmp").mkdir(parents=True, exist_ok=True)
        (s_run / "cwd").mkdir(parents=True, exist_ok=True)
        mcp_json = s_run / "mcp.json"
        mcp_json.write_text(json.dumps(R1.mcp_config(w["clone"]), indent=1))
        shutil.copy2(mcp_json, sdir / "mcp.json")
        cmd = argv_for(mcp_json, q["text"], with_tools)
        (sdir / "argv.json").write_text(json.dumps(cmd, indent=1) + "\n")
        if ns.dry_run:
            print(json.dumps(cmd, indent=1))
            rec["reason"] = "dry run"
            save()
            break
        env = R1.child_env(s_run)
        before = {
            k: R1.snapshot(v) for k, v in {"clone": w["clone"], "inventory": w["inventory"]}.items()
        }
        run = R1.launch(cmd, env, w["clone"], sdir)
        stderr = (sdir / "stderr.txt").read_text(errors="replace")
        recs = R1.parse_stream(sdir / "transcript.jsonl")
        subprocess.run(["pkill", "-f", "--", f"--project {w['clone']}"], check=False)
        after = {
            k: R1.snapshot(v) for k, v in {"clone": w["clone"], "inventory": w["inventory"]}.items()
        }
        res, ini = R1.result_of(recs), R1.init_of(recs)
        cost = res.get("total_cost_usd") if res else None
        limit, infra = R1.usage_limit(recs, stderr), R1.infra_failure(recs, run)
        state["spent"] += cost if cost is not None else SESSION_CAP
        toks = R1.tokens_of(res)
        state["tokens"] += toks or 0
        diffs = {k: R1.changed(before[k], after[k]) for k in before}
        rec.update(
            run=run, cost_usd=cost, tokens=toks, tokens_incl_cache_reads=R1.tokens_of(res, True),
            turns=(res or {}).get("num_turns"), model_resolved=(ini or {}).get("model"),
            claude_version=version, usage_limit=limit, infra_failure=infra,
            world_changed=diffs, started_utc=datetime.now(UTC).isoformat(timespec="seconds"),
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
        answer = R1.answer_of(recs)
        (sdir / "answer.txt").write_text(answer + "\n")
        trace = R1.tool_trace(recs)
        denied = {d.get("tool_use_id") for d in (res or {}).get("permission_denials", []) or []}
        unchanged = not any(diffs.values())
        sc = S.score(q, answer, trace, facts[q["world"]], with_tools, denied, unchanged)
        rec.update(status="SCORED", score=sc,
                   tools=[{"name": t["name"], "is_error": bool(t.get("is_error")),
                           "input": t.get("input")} for t in trace])  # fmt: skip
        (sdir / "score.json").write_text(json.dumps(sc, indent=1, default=str) + "\n")
        (sdir / "session.json").write_text(json.dumps(rec, indent=1, default=str) + "\n")
        save()
        if idx == 3 and state["spent"] > FIRST_FOUR_CAP:
            state["stop"] = f"first four sessions cost USD {state['spent']:.2f} > {FIRST_FOUR_CAP}"
        if state["tokens"] > TOKENS_CAP:
            state["stop"] = f"tokens {state['tokens']} > {TOKENS_CAP}"
    save()
    scored = [s for s in sessions if s["status"] == "SCORED"]
    main_s = [s for s in scored if s["with_tools"]]
    abl = [s for s in scored if not s["with_tools"]]
    n_pass = sum(1 for s in main_s if s["score"]["pass"])

    def tally(rows: list[dict[str, Any]], item: str) -> str:
        rel = [s for s in rows if s["score"]["items"].get(item) is not None]
        return f"{sum(1 for s in rel if s['score']['items'][item])}/{len(rel)}"

    c7 = [s["score"]["items"]["C7"] for s in scored]
    evaluated = len(main_s) == 13
    rule = (n_pass >= 11 and all(c7) and len(scored) == 15) if evaluated else None
    summary = {
        "experiment": "2026-10-16-implementation-diff-round2 PQ2",
        "claude_version": version,
        "freeze": freeze,
        "frozen_hashes_now": frozen_hashes(),
        "sessions": [
            {k: v for k, v in s.items() if k not in ("score", "tools", "run")}
            | {
                "items": (s.get("score") or {}).get("items"),
                "tools": [
                    t["name"].replace("mcp__lattice__", "") + ("!" if t["is_error"] else "")
                    for t in s.get("tools", [])
                ],
            }
            for s in sessions
        ],  # fmt: skip
        "scored": len(scored),
        "main_sessions_scored": len(main_s),
        "main_sessions_pass": n_pass,
        "items_main": {i: tally(main_s, i) for i in S.ITEMS},
        "items_ablation": {i: tally(abl, i) for i in S.ITEMS},
        "C7": f"{sum(1 for x in c7 if x)}/{len(c7)}",
        "pass_rule": ">= 11 of 13 scored sessions satisfy every applicable C-item; C7 in 15 of 15",
        "pass_rule_evaluated": evaluated,
        "pass_rule_met": rule,
        "n": len(main_s),
        "total_cost_usd": round(state["spent"], 4),
        "total_tokens": state["tokens"],
        "token_definition": "input + output + cache creation (cache reads excluded); cap 1000000",
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
