"""Real-agent exercise, round 2 (A3 scorer fix) of the consumer-side draft (slice 4d,
``docs/results/2026-10-16-consumer-draft-4d-round2-prereg.md``, Amendment 1).

    # 1. after the case table and the mutants: the freeze (hashes, `claude --version`), no model
    python dogfood/contracts/draft_agent2.py --write-freeze --out OUT
    # 2. development re-score of round 1 (no model, round-1 data read only)
    python dogfood/contracts/draft_agent2.py --rescore-round1 --scratch DIR --out OUT
    # 3. the argv check (no model), then the sessions (refuse when a frozen file differs)
    python dogfood/contracts/draft_agent2.py --dry-run --scratch DIR --out OUT
    python dogfood/contracts/draft_agent2.py --scratch DIR --out OUT

Model calls go through the `claude` CLI only (``cli_env`` strips every ANTHROPIC* variable, no API
key). Worlds, prompt, argv, A1, A2, A4, A5, A7 and A6 are round 1's (``draft_agent.py`` and
``draft_agent_scorer.py`` are imported, not copied); A3 is replaced by A3b
(``draft_agent2_scorer.py``), the frozen A3 and the informational A3b-strict are reported beside.
Sessions: sonnet, 3 per world with the tool (scored) and 2 W-D2 without it (information), at most
USD 0.50 and 8 turns each, USD 3.00 in all, stop if the first three exceed USD 1.00 and above 400k
tokens (input + output + cache creation). A usage or rate limit ends the exercise: that session
and the rest are recorded NOT RUN (usage limit), never retried and never scored as a miss. Any
tool call outside ``mcp__lattice__*`` is an automatic fail of the session and a finding.
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

NEW_FILES = (
    "draft_agent2_scorer.py",
    "draft_agent2_scorer_cases.json",
    "draft_agent2_check.py",
    "draft_agent2.py",
)
ROUND1_FILES = (
    "draft_agent.py",
    "draft_agent_scorer.py",
    "draft_agent_scorer_cases.json",
    "draft_agent_check.py",
    "agent_scorer.py",
    "agent_probes.py",
    "draft_probes.py",
)
INFO_FILES = ("draft_agent2_mutants.py",)
DATA2 = REPO / "docs" / "results" / "data" / "2026-10-16-consumer-draft-4d-round2"
DEFAULT_FREEZE = DATA2 / "agent" / "freeze.json"
R1_DIR = REPO / "docs" / "results" / "data" / "2026-10-15-consumer-draft-4d"
R1_ARGV = {("W-D2", True): "sonnet-WD2-1", ("W-S6", True): "sonnet-WS6-1",
           ("W-D2", False): "ablation-WD2-1"}  # fmt: skip
PREFIX = "mcp__lattice__"
PLAN = [  # (sid, world, with the tool): the first three decide the early abort
    ("r2-sonnet-WD2-1", "W-D2", True),
    ("r2-sonnet-WS6-1", "W-S6", True),
    ("r2-sonnet-WD2-2", "W-D2", True),
    ("r2-sonnet-WS6-2", "W-S6", True),
    ("r2-sonnet-WD2-3", "W-D2", True),
    ("r2-sonnet-WS6-3", "W-S6", True),
    ("r2-ablation-WD2-1", "W-D2", False),
    ("r2-ablation-WD2-2", "W-D2", False),
]
ROUND1_SETS = (  # (data dir under R1_DIR, session ids)
    ("agent", ["sonnet-WD2-1", "sonnet-WS6-1", "sonnet-WD2-2", "sonnet-WS6-2", "sonnet-WD2-3",
               "sonnet-WS6-3", "ablation-WD2-1", "ablation-WD2-2"]),
    ("agent-partial-run1", ["sonnet-WD2-1", "sonnet-WS6-1"]),
)  # fmt: skip


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def hashes() -> dict[str, str]:
    return {n: sha_file(HERE / n) for n in (*NEW_FILES, *ROUND1_FILES)}


def claude_version() -> str:
    r = subprocess.run(["claude", "--version"], capture_output=True, text=True, check=False)
    return r.stdout.strip() or r.stderr.strip()


def write_freeze(path: Path) -> int:
    chk = subprocess.run([sys.executable, str(HERE / "draft_agent2_check.py")],
                         capture_output=True, text=True, check=False)  # fmt: skip
    last = chk.stdout.strip().splitlines()[-1] if chk.stdout.strip() else ""
    r1 = subprocess.run([sys.executable, str(HERE / "draft_agent_check.py")],
                        capture_output=True, text=True, check=False)  # fmt: skip
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=False)  # fmt: skip
    doc = {
        "frozen_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": hashes(),
        "info_only": {n: sha_file(HERE / n) for n in INFO_FILES},
        "git_head": head.stdout.strip(),
        "claude_version": claude_version(),
        "case_table": last,
        "case_table_rc": chk.returncode,
        "round1_case_table": r1.stdout.strip().splitlines()[-1] if r1.stdout.strip() else "",
        "round1_case_table_rc": r1.returncode,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps(doc, indent=1))
    return 0 if chk.returncode == 0 and r1.returncode == 0 else 1


def verify_freeze(path: Path) -> str | None:
    if not path.is_file():
        return f"no freeze at {path}: run --write-freeze first"
    doc = json.loads(path.read_text())
    if doc.get("case_table_rc") != 0:
        return "the case table did not pass at freeze time"
    now = hashes()
    bad = [n for n in now if now[n] != doc["files"].get(n)]
    return f"frozen file(s) changed since the freeze: {bad}" if bad else None


# ---- helpers shared by the re-score and the sessions ---------------------------------------------


class Kit:
    """The modules and the world facts both modes need."""

    def __init__(self) -> None:
        self.da = _load("lattice_draft_agent_round1", HERE / "draft_agent.py")
        self.ap = self.da._load("lattice_contracts_agent_probes_draft", HERE / "agent_probes.py")
        self.dp = self.da._load("lattice_contracts_draft_probes_agent", HERE / "draft_probes.py")
        self.s2 = _load("draft_agent2_scorer", HERE / "draft_agent2_scorer.py")
        self.lq, self.ep = self.dp.lq, self.dp.ep

    def world(self, env: Any, sid: str, world: str) -> dict[str, Any]:
        """A fresh world plus the facts the allow-list and A3b need (read before any session)."""
        from lattice.spec.rules import extract_rules

        w = self.da.build_world(env, self.dp, self.lq, self.ep, sid, world)
        cd = w["consumer_dir"]
        q = self.lq.sdir(w["ctx"], w["manifest"]) / "linked_questions" / "lq1.json"
        req = cd / "REQUIREMENTS.md"
        w["lock_text"] = (cd / self.lq.LOCK).read_text()
        w["question_text"] = q.read_text() if q.is_file() else ""
        w["rules"] = [r.id for r in extract_rules(req.read_text())] if req.is_file() else []
        return w

    def score(
        self,
        world: str,
        answer: str,
        recs: list[dict[str, Any]],
        w: dict[str, Any],
        with_tool: bool,
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        """(score, allow, trace) of one session: A1, A2, A3b, A4, A5, A7 (+ info A3, A3b-strict)."""
        trace = self.ap.tool_trace(recs)
        res = self.ap.result_of(recs)
        denied = {d.get("tool_use_id") for d in (res or {}).get("permission_denials", []) or []}
        allow = self.s2.allow_from(
            self.ap.init_of(recs),
            [{"name": t.get("name"), "result": t.get("result")} for t in trace],
            w["lock_text"], w["question_text"], w["draft_text"],
        )  # fmt: skip
        sc = self.s2.score2(world, answer, trace, w["corpus"], with_tool, allow, w["rules"], denied)
        return sc, allow, trace


def nonlattice(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every tool call outside mcp__lattice__*: executed (result not an error) or an attempt
    (denied, unknown tool)."""
    return [{"name": t["name"], "input": t.get("input"), "executed": not t.get("is_error"),
             "result": str(t.get("result", ""))[:300]}
            for t in trace if not str(t.get("name", "")).startswith(PREFIX)]  # fmt: skip


def mask_argv(cmd: list[str]) -> list[str]:
    a = list(cmd)
    if "--mcp-config" in a:
        a[a.index("--mcp-config") + 1] = "<mcp.json>"
    return a


def argv_equals_round1(cmd: list[str], world: str, with_tool: bool) -> bool:
    ref = R1_DIR / "agent" / R1_ARGV[(world, with_tool)] / "argv.json"
    return mask_argv(cmd) == mask_argv(json.loads(ref.read_text()))


MARK = {True: "ok", False: "FAIL", None: "-"}


# ---- V3: development re-score of round 1 --------------------------------------------------------


def rescore_round1(ns: argparse.Namespace) -> int:
    kit = Kit()
    out = ns.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = kit.ap.Env(ns.scratch, out)
    worlds = {
        "W-D2": kit.world(env, "rescore-D2", "W-D2"),
        "W-S6": kit.world(env, "rescore-S6", "W-S6"),
    }
    rows: list[dict[str, Any]] = []
    for setname, sids in ROUND1_SETS:
        stored_sessions = {
            s["sid"]: s for s in json.loads((R1_DIR / setname / "sessions.json").read_text())
        }
        for sid in sids:
            sdir = R1_DIR / setname / sid
            st = stored_sessions[sid]
            world, with_tool = st["world"], st["with_tool"]
            row: dict[str, Any] = {
                "set": setname,
                "sid": sid,
                "world": world,
                "with_tool": with_tool,
            }
            w = worlds[world]
            stored_draft = (sdir / "draft-text.txt").read_text()
            if stored_draft != w["draft_text"] + "\n":
                row["status"] = "NOT RESCORED (world differs)"
                rows.append(row)
                continue
            recs = kit.ap.parse_stream(sdir / "transcript.jsonl")
            answer = kit.ap.answer_of(recs)
            row["answer_equals_stored"] = answer.rstrip("\n") == (
                sdir / "answer.txt"
            ).read_text().rstrip("\n")
            sc, allow, trace = kit.score(world, answer, recs, w, with_tool)
            old = json.loads((sdir / "score.json").read_text())
            items = dict(sc["items"])
            items["A6"] = old["items"].get("A6")
            same = {k: items[k] == old["items"].get(k) for k in ("A1", "A2", "A4", "A5", "A7")}
            applicable = {k: v for k, v in items.items() if v is not None}
            row.update(
                status="RESCORED", items=items, info=sc["info"],
                equals_stored_items=same, stored_A3=old["items"].get("A3"),
                A3_recomputed_equals_stored=sc["info"]["A3"] == old["items"].get("A3"),
                every_item=all(applicable.values()),
                nonlattice=nonlattice(trace),
                A3b_invented=sc["detail"]["A3b"]["invented"],
                A3b_strict_invented=sc["detail"]["A3b-strict"]["invented"],
                A3b_proposals=len(sc["detail"]["A3b"]["proposals"]),
                allow_sizes={k: len(v) for k, v in allow.items()},
            )  # fmt: skip
            rows.append(row)
    (out / "rescore.json").write_text(json.dumps(rows, indent=1, default=str) + "\n")
    lines = [
        f"{'set':20} {'session':15} {'status':10} A1 A2 A3b A4 A5 A6 A7 | A3(frozen) A3b-strict "
        "| every-item  equals-stored(A1,A2,A4,A5,A7)"
    ]
    for r in rows:
        if r["status"] != "RESCORED":
            lines.append(f"{r['set']:20} {r['sid']:15} {r['status']}")
            continue
        i = r["items"]
        cols = " ".join(f"{MARK[i.get(k)]:>4}" for k in ("A1", "A2", "A3b", "A4", "A5", "A6", "A7"))
        inf = f"{MARK[r['info']['A3']]:>4} {MARK[r['info']['A3b-strict']]:>4}"
        lines.append(
            f"{r['set']:20} {r['sid']:15} {r['status']:10} {cols} | {inf} | "
            f"{MARK[r['every_item']]:>4}  {all(r['equals_stored_items'].values())}"
        )
    txt = "\n".join(lines)
    (out / "rescore.txt").write_text(txt + "\n")
    print(txt)
    return 0


# ---- V4: the fresh sessions ----------------------------------------------------------------------


def run_sessions(ns: argparse.Namespace, freeze_path: Path) -> int:
    kit = Kit()
    da, ap = kit.da, kit.ap
    out = ns.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = ap.Env(ns.scratch, out)
    freeze = json.loads(freeze_path.read_text())
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
        if idx == 3 and state["spent"] > da.FIRST_THREE_CAP and not state["stop"]:
            state["stop"] = f"first three sessions cost USD {state['spent']:.2f} > 1.00"
        if state["tokens"] > da.TOKENS_CAP and not state["stop"]:
            state["stop"] = f"tokens {state['tokens']} > {da.TOKENS_CAP}"
        if state["stop"]:
            rec["reason"] = state["stop"]
            save()
            continue
        if state["spent"] + da.SESSION_CAP > da.TOTAL_CAP:
            state["stop"] = "total cap (USD 3.00)"
            rec["reason"] = state["stop"]
            save()
            continue
        sdir = out / sid
        if sdir.exists():
            shutil.rmtree(sdir)
        sdir.mkdir(parents=True)
        w = kit.world(env, sid, world)
        rec["pre"] = w["pre"]
        if not w["ok"]:
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
        before = kit.dp.fsnap(roots)
        outside_before = ap.snapshot_outside(out, s_run / "cwd", sid)
        cmd = da.argv_for(ap, mcp_json, w["consumer"], with_tool, state["max_turns"])
        (sdir / "argv.json").write_text(json.dumps(cmd, indent=1) + "\n")
        rec["argv_equals_round1"] = argv_equals_round1(cmd, world, with_tool)
        rec["child_env_has_anthropic_key"] = any(k.startswith("ANTHROPIC") for k in cenv)
        if ns.dry_run:
            print(json.dumps(cmd, indent=1))
            print(f"argv equals round 1 ({R1_ARGV[(world, with_tool)]}) after masking mcp.json: "
                  f"{rec['argv_equals_round1']}; ANTHROPIC* in child env: "
                  f"{rec['child_env_has_anthropic_key']}")  # fmt: skip
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
            cmd = da.argv_for(ap, mcp_json, w["consumer"], with_tool, False)
            run = ap.launch(cmd, cenv, s_run / "cwd", sdir)
            stderr = (sdir / "stderr.txt").read_text(errors="replace")
            recs = ap.parse_stream(sdir / "transcript.jsonl")
        subprocess.run(["pkill", "-f", "--", f"--project {w['consumer_dir']}"], check=False)
        after = kit.dp.fsnap(roots)
        outside_after = ap.snapshot_outside(out, s_run / "cwd", sid)
        res, ini = ap.result_of(recs), ap.init_of(recs)
        cost = res.get("total_cost_usd") if res else None
        toks = da.tokens_of(res)
        limit = ap.usage_limit(recs, stderr)
        infra = ap.infra_failure(recs, run)
        state["spent"] += cost if cost is not None else da.SESSION_CAP
        state["tokens"] += toks or 0
        rec.update(
            run=run, cost_usd=cost, tokens=toks,
            tokens_incl_cache_reads=da.tokens_of(res, cache_reads=True),
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
        sc, allow, trace = kit.score(world, answer, recs, w, with_tool)
        sc["items"]["A6"] = not rec["world_changed"] and rec["outside_same"]
        sc["detail"]["A6"] = {"world_changed": rec["world_changed"][:10],
                              "outside_same": rec["outside_same"]}  # fmt: skip
        applicable = {k: v for k, v in sc["items"].items() if v is not None}
        sc["applicable"] = sorted(applicable)
        sc["pass_items"] = all(applicable.values())
        nl = nonlattice(trace)
        sc["nonlattice_fail"] = bool(nl)
        sc["pass"] = sc["pass_items"] and not nl
        (sdir / "allow.json").write_text(json.dumps(allow, indent=1) + "\n")
        (sdir / "nonlattice_tools.json").write_text(json.dumps(nl, indent=1) + "\n")
        tools_full = [
            {"name": t["name"], "is_error": bool(t.get("is_error")), "input": t.get("input"),
             "result": str(t.get("result", ""))[:600]}
            for t in trace
        ]  # fmt: skip
        (sdir / "tools.json").write_text(json.dumps(tools_full, indent=1) + "\n")
        rec.update(status="SCORED", score=sc, nonlattice=nl,
                   tools=[{"name": t["name"], "is_error": bool(t.get("is_error")),
                           "input": t.get("input")} for t in trace])  # fmt: skip
        (sdir / "score2.json").write_text(json.dumps(sc, indent=1, default=str) + "\n")
        (sdir / "session.json").write_text(json.dumps(rec, indent=1, default=str) + "\n")
        save()
    save()
    scored = [s for s in sessions if s["status"] == "SCORED"]
    tool_sessions = [s for s in scored if s["with_tool"]]
    abl = [s for s in scored if not s["with_tool"]]

    def count(group: list[dict[str, Any]], item: str, where: str = "items") -> str:
        rel = [s for s in group if s["score"][where].get(item) is not None]
        return f"{sum(1 for s in rel if s['score'][where][item])}/{len(rel)}"

    items = {i: count(tool_sessions, i) for i in ("A1", "A2", "A3b", "A4", "A5", "A6", "A7")}
    info = {i: count(tool_sessions, i, "info") for i in ("A3", "A3b-strict")}
    abl_items = {i: count(abl, i) for i in ("A2", "A3b", "A4", "A5", "A6", "A7")}
    abl_info = {i: count(abl, i, "info") for i in ("A3", "A3b-strict")}
    n_pass = sum(1 for s in tool_sessions if s["score"]["pass"])
    n_pass_items = sum(1 for s in tool_sessions if s["score"]["pass_items"])
    n_pass_executed = sum(
        1 for s in tool_sessions
        if s["score"]["pass_items"] and not any(t["executed"] for t in s["nonlattice"])
    )  # fmt: skip
    a6 = [s["score"]["items"]["A6"] for s in scored]
    evaluated = len(tool_sessions) == 6
    summary = {
        "experiment": "2026-10-16-consumer-draft-4d round 2",
        "claude_version": version,
        "freeze": freeze,
        "hashes_now": hashes(),
        "sessions": [
            {k: v for k, v in s.items() if k not in ("score", "tools", "run", "nonlattice")}
            | {
                "items": (s.get("score") or {}).get("items"),
                "info": (s.get("score") or {}).get("info"),
                "pass": (s.get("score") or {}).get("pass"),
                "tools": [
                    t["name"].replace(PREFIX, "") + ("!" if t["is_error"] else "")
                    for t in s.get("tools", [])
                ],
                "nonlattice": s.get("nonlattice", []),
            }  # fmt: skip
            for s in sessions
        ],
        "scored": len(scored),
        "tool_sessions_scored": len(tool_sessions),
        "items_with_tool": items,
        "info_with_tool": info,
        "items_ablation": abl_items,
        "info_ablation": abl_info,
        "tool_sessions_pass_every_item_and_no_nonlattice": n_pass,
        "tool_sessions_pass_every_item_ignoring_nonlattice": n_pass_items,
        "tool_sessions_pass_every_item_ignoring_denied_attempts": n_pass_executed,
        "A6": f"{sum(1 for x in a6 if x)}/{len(a6)}",
        "pass_rule_evaluated": evaluated,
        "pass_rule_met": (n_pass >= 5 and all(a6)) if evaluated else None,
        "pass_rule_met_ignoring_denied_attempts": (n_pass_executed >= 5 and all(a6))
        if evaluated
        else None,  # fmt: skip
        "total_cost_usd": round(state["spent"], 4),
        "total_tokens": state["tokens"],
        "token_definition": "input + output + cache creation (cache reads excluded); cap 400000",
        "total_tokens_incl_cache_reads": sum(
            s.get("tokens_incl_cache_reads") or 0 for s in sessions
        ),
        "stop": state["stop"],
        "wall_s": round(time.time() - t0, 1),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("freeze", "hashes_now")},
                     indent=1, default=str))  # fmt: skip
    return 0


def main(argv: list[str] | None = None) -> int:
    ap_ = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap_.add_argument("--scratch", type=Path, default=None)
    ap_.add_argument("--out", required=True, type=Path)
    ap_.add_argument(
        "--freeze", type=Path, default=None, help="freeze file (default: the data dir's)"
    )
    ap_.add_argument("--write-freeze", action="store_true")
    ap_.add_argument("--rescore-round1", action="store_true", help="development re-score, no model")
    ap_.add_argument(
        "--dry-run", action="store_true", help="build the first world, check its argv; no model"
    )
    ns = ap_.parse_args(argv)
    if ns.write_freeze:
        return write_freeze(ns.freeze or (ns.out.resolve() / "freeze.json"))
    freeze_path = ns.freeze or DEFAULT_FREEZE
    why = verify_freeze(freeze_path)
    if why:
        print(f"refused: {why}", file=sys.stderr)
        return 2
    if ns.scratch is None:
        print("--scratch DIR is required", file=sys.stderr)
        return 2
    if ns.rescore_round1:
        return rescore_round1(ns)
    return run_sessions(ns, freeze_path)


if __name__ == "__main__":
    sys.exit(main())
