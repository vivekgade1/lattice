"""The real-model probes PQ1-PQ3 of Spike G: section 3 and Amendment 1 of
``docs/results/2026-10-16-rationale-glossary-prereg.md``.

    # 1. the scorer cases (no model), then the freeze: the hashes, the prompt, `claude --version`
    python dogfood/rationale_agent.py --write-freeze --out OUT
    # 2. the sessions, once (refuses when a frozen file differs from the freeze); NOT RUN on a limit
    python dogfood/rationale_agent.py --pq1 --pq2 --pq3 --scratch DIR --out OUT

* **PQ1 drafter**: ``rationale draft`` on S for R63, R56, R75, R29, R133, R26, one session per rule,
  with an empty proposals file. Gated: in 6/6 the authoritative file is absent afterwards, the
  entry is ``proposed`` and its ``owner`` is null. Reported: the fraction with a valid basis,
  proposals with ``basis []``, USD. Caps: USD 0.25 and 4 turns per rule.
* **PQ2 agent exercise** on W (Appendix B): 13 sessions, one per question, scored by
  ``rationale_agent_scorer.py`` (E1-E6). Pass: at least 11/13 sessions satisfy every applicable
  item and E6 holds 13/13. Caps: USD 0.40 and 8 turns per session, USD 5.00 in all, abort if the
  first four cost more than USD 1.50, at most 1.0M tokens counted as input + output + cache
  creation.
* **PQ3 live ask** on W through ``lattice.spec.ask.main`` (the same code as ``python -m lattice.spec
  ask --trace ... --json``): four questions. Gated 4/4: each answer's ``rationale`` key equals D10's
  function of its valid citations (recomputed here, independently) and no answer holds proposal or
  stale-note text. Caps: USD 0.25 per question.

Model calls go through the ``claude`` CLI only (``lattice.llm.cli_client``): the child
environment has no ``ANTHROPIC*`` variable and no API key is read. USD 7.50 in all. A usage or rate
limit records the rest NOT RUN (usage limit): never retried, never scored as a miss.
"""

from __future__ import annotations

import argparse
import hashlib
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
REPO = HERE.parent
for p in (str(REPO), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)
import implementation_agent as R1  # noqa: E402  (round 1's helpers: stream parser, snapshots)
import rationale_agent_scorer as S  # noqa: E402
import rationale_probes as RP  # noqa: E402

CASES = json.loads((HERE / "rationale_agent_cases.json").read_text())
WORLD_FILE = HERE / "rationale_agent_world.json"
FROZEN = (
    "rationale_agent.py",
    "rationale_agent_scorer.py",
    "rationale_agent_cases.json",
    "rationale_agent_world.json",
    "rationale_probes.py",  # imported: the world builder
    "implementation_agent.py",  # imported: the stream parser, snapshots, the child environment
    "implementation_agent_scorer.py",  # imported by implementation_agent
    "contracts/agent_scorer.py",  # imported by implementation_agent_scorer
)
FREEZE_FILE = "freeze.sha256"
PROMPT = CASES["prompt"]
MODEL = "sonnet"
PQ1_RULES = ("R63", "R56", "R75", "R29", "R133", "R26")
PQ1_CAP, PQ1_TURNS = 0.25, "4"
PQ2_CAP, PQ2_TURNS = 0.40, "8"
PQ2_TOTAL, PQ2_FIRST_FOUR, TOKENS_CAP = 5.00, 1.50, 1_000_000
PQ3_CAP = 0.25
TOTAL_CAP = 7.50
PQ3_QUESTIONS = (
    ("Q1", "Why can't a habit's period be changed after creation?", "R63"),
    ("Q2", "Can a user have 51 active habits?", "R56"),
    ("Q3", "Why is the email lower-cased at registration?", "R8"),
    ("Q4", "What is the active window of a habit?", "term:active-window"),
)
ALLOWED = ["Read", "Grep", "Glob"] + [
    f"mcp__lattice__{n}"
    for n in (
        "model_get",
        "rule_get",
        "rationale_get",
        "glossary_get",
        "spec_lint",
        "code_links",
        "graph_get",
    )
]
DISALLOWED = [
    "Bash",
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
    "mcp__lattice__spec_ask",
]


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ---- the scorer cases and the freeze -------------------------------------------------------------


def check_cases() -> tuple[bool, str]:
    """Every synthetic case scores as its author expected (no model): (ok, one line)."""
    bad = []
    for c in CASES["cases"]:
        r = S.score(
            {"key": c["key"], "referent": c["referent"]},
            c["answer"],
            CASES["world"],
            c["tree_unchanged"],
        )
        if r["items"] != c["expect"]["items"] or r["pass"] != c["expect"]["pass"]:
            bad.append(c["id"])
    n = len(CASES["cases"])
    return (not bad and n >= 30), f"{n - len(bad)}/{n} scorer cases as expected" + (
        f"; wrong: {bad}" if bad else ""
    )


def freeze_lines() -> list[str]:
    lines = [f"{R1.sha_file(HERE / n)}  dogfood/{n}" for n in FROZEN]
    lines.append(f"{sha_text(PROMPT)}  prompt")
    lines.append(f"{sha_text(R1.claude_version())}  claude --version: {R1.claude_version()}")
    return lines


def write_freeze(out: Path) -> int:
    ok, line = check_cases()
    print(line)
    if not ok:
        return 1
    out.mkdir(parents=True, exist_ok=True)
    body = freeze_lines()
    (out / FREEZE_FILE).write_text("\n".join(body) + "\n")
    print("\n".join(body))
    return 0


def verify_freeze(out: Path) -> str | None:
    p = out / FREEZE_FILE
    if not p.is_file():
        return f"no {FREEZE_FILE} in {out}: run --write-freeze first"
    if p.read_text().splitlines() != freeze_lines():
        return "a frozen file, the prompt or `claude --version` differs from the freeze"
    ok, line = check_cases()
    return None if ok else line


# ---- the worlds ----------------------------------------------------------------------------------


def build_world(scratch: Path) -> dict[str, Path]:
    """S (git archive a0679e9) and W (S + Appendix B's files), studio.toml pointed at the clone
    with a state directory outside it. Fresh on every run."""
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    s, w = scratch / "S", scratch / "W"
    R1.archive("a0679e9", s)
    P = RP.P1
    P.rewrite_go_src(s)
    world = json.loads(WORLD_FILE.read_text())
    ks = {k: scratch / "unused" / k for k in ("K0",)}
    RP.build_fixture_worlds(s, w, ks, world)
    shutil.rmtree(scratch / "unused", ignore_errors=True)
    P.rewrite_go_src(w)
    toml = w / "studio.toml"
    toml.write_text(toml.read_text().rstrip("\n") + f'\n\n[state]\ndir = "{scratch / "state"}"\n')
    pq1 = scratch / "pq1"
    shutil.copytree(s, pq1, symlinks=True)
    return {"S": s, "W": w, "PQ1": pq1}


def world_data(w: Path) -> dict[str, Any]:
    """What the scorer needs, read from W's files with plain json (not the library)."""
    auth = json.loads((w / RP.RAT).read_text())["notes"]
    props = json.loads((w / RP.PROP).read_text())["notes"]
    gl = json.loads((w / RP.GLOS).read_text())["terms"]
    trace = json.loads((w / "spec.trace.json").read_text())
    anchors = {r["id"]: RP.anchor(r["text"]) for r in trace["rules"]}
    current = {
        n["rule"]: [n["why"], n["tradeoff"]] for n in auth if anchors.get(n["rule"]) == n["anchor"]
    }
    stale = [n["why"] for n in auth if anchors.get(n["rule"]) != n["anchor"]]
    return {
        "current": current,
        "proposal_texts": [n["why"] for n in props if n["status"] == "proposed"],
        "stale_texts": stale,
        "terms": {
            re.sub(r"[^a-z0-9]+", "-", t["term"].lower()).strip("-"): t["definition"] for t in gl
        },
    }


# ---- PQ1 and PQ3: the capped CLI client ----------------------------------------------------------


def capped_client(cap: float, turns: str, tmp: Path):
    from lattice.llm.cli_client import ClaudeCliClient

    class Capped(ClaudeCliClient):
        def _argv(self, system: str) -> list[str]:
            return [*super()._argv(system), "--max-budget-usd", f"{cap:.2f}", "--max-turns", turns]

    (tmp / "tmp").mkdir(parents=True, exist_ok=True)
    return Capped(model=MODEL, effort="", full_text=True, env=R1.child_env(tmp))


def run_pq1(dirs: dict[str, Path], out: Path, state: dict[str, Any]) -> dict[str, Any]:
    from lattice.spec import rationale as rt

    d = dirs["PQ1"]
    prop = d / RP.PROP
    prop.write_text(
        json.dumps(
            {"schema": "lattice.rationale/1", "source": "REQUIREMENTS.md", "notes": []}, indent=2
        )
        + "\n"
    )
    rows = []
    for rid in PQ1_RULES:
        row: dict[str, Any] = {"rule": rid, "status": "NOT RUN", "reason": None}
        rows.append(row)
        if state["stop"]:
            row["reason"] = state["stop"]
            continue
        if state["spent"] + PQ1_CAP > TOTAL_CAP:
            state["stop"] = f"total cap (USD {TOTAL_CAP:.2f})"
            row["reason"] = state["stop"]
            continue
        before = {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()}
        client = capped_client(PQ1_CAP, PQ1_TURNS, out / "pq1-tmp" / rid)
        code, so, se = RP.run_main(
            rt.main,
            [
                "draft",
                "--trace",
                str(d / "spec.trace.json"),
                "--spec",
                str(d / "spec.py"),
                "--rule",
                rid,
                "--out",
                str(prop),
                "--model",
                MODEL,
                "--json",
            ],
            generator=client,
        )
        try:
            rep = json.loads(so) if so.strip() else {}
        except json.JSONDecodeError:
            rep = {}
        cost = sum((r.get("cost_usd") or 0.0) for r in rep.get("rules", []))
        state["spent"] += cost if cost else PQ1_CAP
        stopped = rep.get("stopped") or ""
        if "UsageLimit" in stopped or "usage limit" in (se + stopped).lower():
            row.update(
                status="NOT RUN (usage limit)", reason="usage limit; not retried, not scored"
            )
            state["stop"] = "NOT RUN (usage limit)"
            continue
        notes = json.loads(prop.read_text())["notes"] if prop.is_file() else []
        mine = [n for n in notes if n["rule"] == rid]
        auth_absent = not (d / RP.RAT).exists()
        entry = mine[-1] if mine else None
        row.update(
            status="RAN",
            exit=code,
            cost_usd=round(cost, 4),
            stopped=stopped or None,
            authoritative_absent=auth_absent,
            others_unchanged={k: v for k, v in before.items() if k != RP.PROP}
            == {p.name: p.read_bytes() for p in d.iterdir() if p.is_file() and p.name != RP.PROP},
            entry=entry,
            stderr=se[-300:],
        )
        row["gated_ok"] = bool(
            entry and entry["status"] == "proposed" and entry["owner"] is None and auth_absent
        )
        row["basis"] = len(entry["basis"]) if entry else None
        row["rejected_basis"] = len(entry.get("rejected_basis", [])) if entry else None
        row["valid_basis"] = bool(entry and entry["basis"] and not entry.get("rejected_basis"))
        row["basis_empty"] = bool(entry is not None and not entry["basis"])
        (out / "pq1").mkdir(exist_ok=True)
        (out / "pq1" / f"{rid}.json").write_text(json.dumps(row, indent=1, default=str) + "\n")
    ran = [r for r in rows if r["status"] == "RAN"]
    return {
        "rows": rows,
        "n": len(ran),
        "of": len(PQ1_RULES),
        "gated_pass": f"{sum(r['gated_ok'] for r in ran)}/{len(ran)}",
        "gate_evaluated": len(ran) == len(PQ1_RULES),
        "gate_met": all(r["gated_ok"] for r in ran) if len(ran) == len(PQ1_RULES) else None,
        "valid_basis": f"{sum(r['valid_basis'] for r in ran)}/{len(ran)}",
        "basis_empty": sum(r["basis_empty"] for r in ran),
        "usd": round(sum(r.get("cost_usd") or 0.0 for r in ran), 4),
    }


def expected_attachment(answer: dict[str, Any], w: Path) -> list[dict[str, Any]]:
    """D10's `rationale` key from the answer's valid citations, written independently of ask.py."""
    trace = json.loads((w / "spec.trace.json").read_text())
    rules = {r["id"]: r["text"] for r in trace["rules"]}
    links = trace.get("links", {})
    auth = {n["rule"]: n for n in json.loads((w / RP.RAT).read_text())["notes"]}
    props = [n for n in json.loads((w / RP.PROP).read_text())["notes"] if n["status"] == "proposed"]

    def status(rid: str) -> str:
        n = auth.get(rid)
        if n is not None:
            if n["approved_sha"] != RP.approved_sha_of(n["why"], n["tradeoff"], n["owner"]):
                return "edited"
            if RP.anchor(rules.get(rid, "")) == n["anchor"]:
                return "approved"
            return "stale"
        return "proposed_only" if any(p["rule"] == rid for p in props) else "none"

    cites = [c["target"] for c in answer.get("citations", [])]
    direct = []
    for t in cites:
        if t.startswith("rule:") and t[5:] in rules and t[5:] not in [d[0] for d in direct]:
            direct.append((t[5:], None))
    kinds = ("scenario", "invariant", "machine", "service", "profile")
    cited = [t for t in cites if t.partition(":")[0] in kinds]
    seen = {d[0] for d in direct}
    via = []
    for rid in rules:
        if rid in seen:
            continue
        for t in cited:
            if any(
                lk == t or (t.startswith("profile:") and "." not in t and lk.partition(".")[0] == t)
                for lk in links.get(rid, [])
            ):
                via.append((int(rid[1:]), rid, t))
                break
    entries = direct + [(rid, t) for _n, rid, t in sorted(via)]
    out = []
    for rid, v in entries[:5]:
        item: dict[str, Any] = {"rule": rid, "status": status(rid), "via": v}
        if item["status"] == "approved":
            n = auth[rid]
            item.update(
                why=n["why"], tradeoff=n["tradeoff"], owner=n["owner"], approved_by=n["approved_by"]
            )
        out.append(item)
    return out


def run_pq3(dirs: dict[str, Path], out: Path, state: dict[str, Any]) -> dict[str, Any]:
    from lattice.spec import ask as A

    w = dirs["W"]
    world = world_data(w)
    sys.path.insert(0, str(w))
    rows = []
    try:
        for qid, text, referent in PQ3_QUESTIONS:
            row: dict[str, Any] = {"id": qid, "question": text, "status": "NOT RUN", "reason": None}
            rows.append(row)
            if state["stop"]:
                row["reason"] = state["stop"]
                continue
            if state["spent"] + PQ3_CAP > TOTAL_CAP:
                state["stop"] = f"total cap (USD {TOTAL_CAP:.2f})"
                row["reason"] = state["stop"]
                continue
            snap = R1.snapshot(w)
            client = capped_client(PQ3_CAP, "4", out / "pq3-tmp" / qid)
            code, so, se = RP.run_main(
                A.main,
                [
                    "--spec",
                    "spec:SPEC",
                    "--trace",
                    str(w / "spec.trace.json"),
                    "--model",
                    MODEL,
                    "--json",
                    text,
                ],
                generator=client,
            )
            from lattice.llm.cli_client import _USAGE_LIMIT_RE

            if code != 0 and _USAGE_LIMIT_RE.search(so + se):
                row.update(status="NOT RUN (usage limit)", reason=se[-300:])
                state["stop"] = "NOT RUN (usage limit)"
                continue
            try:
                ans = json.loads(so)
            except json.JSONDecodeError:
                row.update(status="NOT SCORED", reason=(se or so)[-300:])
                continue
            cost = (ans.get("usage") or {}).get("cost_usd")
            state["spent"] += cost if cost is not None else PQ3_CAP
            want = expected_attachment(ans, w)
            dump = json.dumps(ans, ensure_ascii=False)
            leak = S.leaked(dump, world)
            cited = [c["target"] for c in ans.get("citations", [])]
            row.update(
                status="RAN",
                cost_usd=cost,
                kind=ans.get("kind"),
                citations=cited,
                rationale=ans.get("rationale"),
                expected_rationale=want,
                key_equals_function=ans.get("rationale") == want,
                leaked=leak,
                world_unchanged=not R1.changed(snap, R1.snapshot(w)),
                cited_referent=(
                    f"rule:{referent}" in cited
                    or referent in cited
                    or f"rationale:{referent}" in cited
                ),
                answer=ans.get("answer"),
                explanation=ans.get("explanation"),
            )
            row["gated_ok"] = bool(row["key_equals_function"] and not leak)
            (out / "pq3").mkdir(exist_ok=True)
            (out / "pq3" / f"{qid}.json").write_text(json.dumps(row, indent=1, default=str) + "\n")
    finally:
        sys.path.remove(str(w))
    ran = [r for r in rows if r["status"] == "RAN"]
    return {
        "rows": rows,
        "n": len(ran),
        "of": len(PQ3_QUESTIONS),
        "gated_pass": f"{sum(r['gated_ok'] for r in ran)}/{len(ran)}",
        "gate_evaluated": len(ran) == len(PQ3_QUESTIONS),
        "gate_met": all(r["gated_ok"] for r in ran) if len(ran) == len(PQ3_QUESTIONS) else None,
        "cited_referent": sum(bool(r.get("cited_referent")) for r in ran),
        "usd": round(sum(r.get("cost_usd") or 0.0 for r in ran), 4),
    }


# ---- PQ2 -----------------------------------------------------------------------------------------


def argv_for(mcp_json: Path, question: str) -> list[str]:
    return [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--mcp-config",
        str(mcp_json),
        "--tools",
        "Read,Grep,Glob",
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        ",".join(ALLOWED),
        "--disallowedTools",
        ",".join(DISALLOWED),
        "--max-turns",
        PQ2_TURNS,
        "--max-budget-usd",
        f"{PQ2_CAP:.2f}",
        "--model",
        MODEL,
        "--",
        PROMPT.replace("{Q}", question),
    ]


def run_pq2(
    dirs: dict[str, Path], scratch: Path, out: Path, state: dict[str, Any]
) -> dict[str, Any]:
    w = dirs["W"]
    world = world_data(w)
    questions = json.loads(WORLD_FILE.read_text())["questions"]
    version = R1.claude_version()
    sessions: list[dict[str, Any]] = []
    spent = 0.0
    tokens = 0
    stop: str | None = state["stop"]
    for idx, q in enumerate(questions):
        rec: dict[str, Any] = {
            "id": q["id"],
            "key": q["key"],
            "referent": q["referent"],
            "question": q["text"],
            "status": "NOT RUN",
            "reason": None,
        }
        sessions.append(rec)
        if stop:
            rec["reason"] = stop
            continue
        if state["spent"] + PQ2_CAP > TOTAL_CAP or spent + PQ2_CAP > PQ2_TOTAL:
            stop = f"total cap (USD {PQ2_TOTAL:.2f} for PQ2 / {TOTAL_CAP:.2f} in all)"
            rec["reason"] = stop
            continue
        sdir = out / "pq2" / q["id"]
        shutil.rmtree(sdir, ignore_errors=True)
        sdir.mkdir(parents=True)
        run_dir = scratch / "sessions" / q["id"]
        (run_dir / "tmp").mkdir(parents=True, exist_ok=True)
        mcp_json = run_dir / "mcp.json"
        mcp_json.write_text(json.dumps(R1.mcp_config(w), indent=1))
        cmd = argv_for(mcp_json, q["text"])
        (sdir / "argv.json").write_text(json.dumps(cmd, indent=1) + "\n")
        env = R1.child_env(run_dir)
        before = R1.snapshot(w)
        run = R1.launch(cmd, env, w, sdir)
        stderr = (sdir / "stderr.txt").read_text(errors="replace")
        recs = R1.parse_stream(sdir / "transcript.jsonl")
        subprocess.run(["pkill", "-f", "--", f"--project {w}"], check=False)
        diffs = R1.changed(before, R1.snapshot(w))
        res, ini = R1.result_of(recs), R1.init_of(recs)
        cost = res.get("total_cost_usd") if res else None
        limit, infra = R1.usage_limit(recs, stderr), R1.infra_failure(recs, run)
        spent += cost if cost is not None else PQ2_CAP
        state["spent"] += cost if cost is not None else PQ2_CAP
        toks = R1.tokens_of(res)
        tokens += toks or 0
        rec.update(
            run=run,
            cost_usd=cost,
            tokens=toks,
            turns=(res or {}).get("num_turns"),
            model_resolved=(ini or {}).get("model"),
            claude_version=version,
            usage_limit=limit,
            infra_failure=infra,
            world_changed=diffs,
            started_utc=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        if limit or infra == "rate_limit_or_overloaded":
            rec.update(
                status="NOT RUN (usage limit)",
                reason="usage/rate limit answer; recorded, not retried, not scored",
            )
            stop = state["stop"] = "NOT RUN (usage limit)"
            continue
        if infra is not None:
            rec.update(status="NOT SCORED (infra)", reason=infra)
            continue
        answer = R1.answer_of(recs)
        (sdir / "answer.txt").write_text(answer + "\n")
        sc = S.score(q, answer, world, not diffs)
        trace = R1.tool_trace(recs)
        rec.update(
            status="SCORED",
            score=sc,
            tools=[
                t["name"].replace("mcp__lattice__", "") + ("!" if t.get("is_error") else "")
                for t in trace
            ],
        )
        (sdir / "score.json").write_text(json.dumps(sc, indent=1, default=str) + "\n")
        if idx == 3 and spent > PQ2_FIRST_FOUR:
            stop = f"first four sessions cost USD {spent:.2f} > {PQ2_FIRST_FOUR}"
        if tokens > TOKENS_CAP:
            stop = f"tokens {tokens} > {TOKENS_CAP}"
    scored = [s for s in sessions if s["status"] == "SCORED"]
    n_pass = sum(1 for s in scored if s["score"]["pass"])
    e6 = sum(1 for s in scored if s["score"]["items"]["E6"])
    evaluated = len(scored) == len(questions)

    def tally(item: str) -> str:
        rel = [s for s in scored if s["score"]["items"].get(item) is not None]
        return f"{sum(1 for s in rel if s['score']['items'][item])}/{len(rel)}"

    return {
        "sessions": sessions,
        "n": len(scored),
        "of": len(questions),
        "pass": n_pass,
        "e6": f"{e6}/{len(scored)}",
        "items": {i: tally(i) for i in S.ITEMS},
        "pass_rule": ">= 11/13 sessions satisfy every applicable item and E6 holds 13/13",
        "evaluated": evaluated,
        "met": (n_pass >= 11 and e6 == len(questions)) if evaluated else None,
        "usd": round(spent, 4),
        "tokens": tokens,
        "stop": stop,
    }


# ---- main ----------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--scratch", type=Path, default=None)
    ap.add_argument("--write-freeze", action="store_true")
    ap.add_argument("--check-cases", action="store_true", help="the scorer cases only (no model)")
    ap.add_argument(
        "--dry-run", action="store_true", help="build the worlds, print PQ2's argv; no model"
    )
    ap.add_argument("--pq1", action="store_true")
    ap.add_argument("--pq2", action="store_true")
    ap.add_argument("--pq3", action="store_true")
    ns = ap.parse_args(argv)
    out = ns.out.resolve()
    if ns.check_cases:
        ok, line = check_cases()
        print(line)
        return 0 if ok else 1
    if ns.write_freeze:
        return write_freeze(out)
    if not (ns.pq1 or ns.pq2 or ns.pq3):
        print("give --pq1, --pq2 and/or --pq3", file=sys.stderr)
        return 2
    why = verify_freeze(out)
    if why:
        print(f"refused: {why}", file=sys.stderr)
        return 2
    if ns.scratch is None:
        print("--scratch DIR is required", file=sys.stderr)
        return 2
    scratch = ns.scratch.resolve()
    dirs = build_world(scratch)
    if ns.dry_run:
        wd = world_data(dirs["W"])
        qs = json.loads(WORLD_FILE.read_text())["questions"]
        mcp = scratch / "dry-mcp.json"
        mcp.write_text(json.dumps(R1.mcp_config(dirs["W"]), indent=1))
        print(json.dumps({
            "current": sorted(wd["current"]), "proposals": len(wd["proposal_texts"]),
            "stale": len(wd["stale_texts"]), "terms": sorted(wd["terms"]),
            "argv_first": argv_for(mcp, qs[0]["text"]),
        }, indent=1))  # fmt: skip
        return 0
    (out / "pq2").mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {"spent": 0.0, "stop": None}
    t0 = time.time()
    summary: dict[str, Any] = {
        "claude_version": R1.claude_version(),
        "freeze": (out / FREEZE_FILE).read_text().splitlines(),
        "budget_usd": TOTAL_CAP,
        "model": MODEL,
    }
    summary["PQ1"] = run_pq1(dirs, out, state) if ns.pq1 else "not requested"
    summary["PQ2"] = run_pq2(dirs, scratch, out, state) if ns.pq2 else "not requested"
    summary["PQ3"] = run_pq3(dirs, out, state) if ns.pq3 else "not requested"
    summary["total_usd"] = round(state["spent"], 4)
    summary["stop"] = state["stop"]
    summary["wall_s"] = round(time.time() - t0, 1)
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str) + "\n")
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k not in ("freeze",)}
            | {
                "PQ1": {k: v for k, v in summary["PQ1"].items() if k != "rows"} if ns.pq1 else None,
                "PQ2": {k: v for k, v in summary["PQ2"].items() if k != "sessions"}
                if ns.pq2
                else None,
                "PQ3": {k: v for k, v in summary["PQ3"].items() if k != "rows"} if ns.pq3 else None,
            },
            indent=1,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
