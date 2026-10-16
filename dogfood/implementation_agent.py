"""The real-agent exercise of the implementation diff (PQ): section 3 and Amendment 1 of
``docs/results/2026-10-15-implementation-diff-prereg.md``.

    # 1. the scorer cases, then the freeze (hashes, `claude --version`, the workflows hash)
    python dogfood/implementation_agent.py --write-freeze --out OUT --workflows-dyn S-dyn.json
    # 2. the sessions (refuses when a frozen file or the workflows differ from the freeze)
    python dogfood/implementation_agent.py --scratch DIR --out OUT --workflows-dyn S-dyn.json [--e2]

Model calls go through the ``claude`` CLI only: the child environment has no ``ANTHROPIC*`` variable
and no API key is read. Worlds: S-dyn (Streak a0679e9 with Spike B's dynamic ``workflows.json``) for
Q1-Q8 and S4-stat (Streak 1d00ede, Spike B's static run) for Q9-Q13; each is a ``git archive`` clone
whose ``studio.toml`` gets an ``[implementation]`` table (and ``[state] enabled = false``, so the
read-only MCP server creates no state directory in the clone). Sessions: sonnet, one per question
(13 scored) and two ablations without the three implementation tools (Q3, Q9; information only), at
most USD 0.50 and 8 turns each, USD 5.00 in all, abort if the first four cost more than USD 1.50,
abort above 1.0M tokens counted as input + output + cache creation (cache reads excluded). A usage
or rate limit ends the exercise: the rest is recorded NOT RUN (usage limit), never retried and never
scored as a miss. Arm E2 (``--e2``, report only): the same 13 questions through
``python -m lattice.spec ask --inventory``, at most USD 2.00 in all.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import implementation_agent_scorer as S  # noqa: E402

CASES = json.loads((HERE / "implementation_agent_scorer_cases.json").read_text())
FROZEN = (
    "implementation_agent.py",
    "implementation_agent_scorer.py",
    "implementation_agent_check.py",
    "implementation_agent_scorer_cases.json",
    "contracts/agent_scorer.py",  # imported by the scorer (4c's A5 patterns)
)
FREEZE_FILE = "scorer-freeze.json"
STREAK = Path("/Users/vivekgade/Documents/streak")
REFS = {"S-dyn": "a0679e9", "S4-stat": "1d00ede"}
GO_ENV = {"GOFLAGS": "-mod=mod", "GOPROXY": "off", "GOTOOLCHAIN": "local", "GOWORK": "off"}
MODEL = "sonnet"
MAX_TURNS = "8"
MAX_BUDGET = "0.50"
SESSION_CAP = 0.50
TOTAL_CAP = 5.00
FIRST_FOUR_CAP = 1.50
TOKENS_CAP = 1_000_000
E2_CAP = 2.00
TIMEOUT_S = 420
PROMPT = CASES["prompt"]
ALLOWED_BASE = ["Read", "Grep", "Glob"] + [
    f"mcp__lattice__{n}" for n in ("model_get", "rule_get", "code_links", "graph_get", "spec_lint")
]
IMPL = list(S.IMPL_TOOLS)
DISALLOWED_BASE = ["Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "mcp__lattice__spec_ask"]
SCRUB_EXACT = (
    "CLAUDECODE",
    "AI_AGENT",
    "ENABLE_IDE_INTEGRATION",
    "LATTICE_WORKSPACE",
    "PYTHONPATH",
)
SCRUB_PREFIX = ("ANTHROPIC", "CLAUDE_CODE_")
QUESTIONS = {q["id"]: q for q in CASES["questions"]}
PLAN = [(q["id"], q["id"], True) for q in CASES["questions"]] + [
    (f"ablation-{qid}", qid, False) for qid in CASES["ablation"]
]


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def frozen_hashes() -> dict[str, str]:
    return {n: sha_file(HERE / n) for n in FROZEN}


def claude_version() -> str:
    r = subprocess.run(["claude", "--version"], capture_output=True, text=True, check=False)
    return r.stdout.strip() or r.stderr.strip()


def write_freeze(out: Path, workflows: Path) -> int:
    chk = subprocess.run(
        [sys.executable, str(HERE / "implementation_agent_check.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    last = chk.stdout.strip().splitlines()[-1] if chk.stdout.strip() else ""
    doc = {
        "frozen_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": frozen_hashes(),
        "workflows_dyn_sha256": sha_file(workflows),
        "report_sha256": CASES["report_sha256"],
        "claude_version": claude_version(),
        "scorer_cases": last,
        "scorer_cases_rc": chk.returncode,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / FREEZE_FILE).write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps(doc, indent=1))
    return 0 if chk.returncode == 0 else 1


def verify_freeze(out: Path, workflows: Path) -> str | None:
    p = out / FREEZE_FILE
    if not p.is_file():
        return f"no {FREEZE_FILE} in {out}: run --write-freeze first"
    doc = json.loads(p.read_text())
    if doc.get("scorer_cases_rc") != 0:
        return "the scorer cases did not pass at freeze time"
    now = frozen_hashes()
    bad = [n for n in FROZEN if now[n] != doc["files"].get(n)]
    if bad:
        return f"frozen file(s) changed since the freeze: {bad}"
    if sha_file(workflows) != doc.get("workflows_dyn_sha256"):
        return "workflows.json differs from the freeze"
    return None


# ---- environment, snapshots ------------------------------------------------------------------


def child_env(s_dir: Path) -> dict[str, str]:
    """The `claude` child's environment: no credentials, no ANTHROPIC*, no launching session."""
    from lattice.llm.cli_client import cli_env

    env = cli_env(os.environ, None)
    env = {k: v for k, v in env.items() if not k.startswith(SCRUB_PREFIX) and k not in SCRUB_EXACT}
    env.update(
        MCP_CONNECTION_NONBLOCKING="false", TMPDIR=str(s_dir / "tmp"), PYTHONDONTWRITEBYTECODE="1"
    )
    assert not any(k.startswith("ANTHROPIC") for k in env)
    return env


def snapshot(root: Path) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    for dp, dns, fns in os.walk(root):
        for name in sorted([*dns, *fns]):
            p = Path(dp) / name
            rel = p.relative_to(root).as_posix()
            st = p.lstat()
            if p.is_symlink():
                out[rel] = ("link", os.readlink(p), st.st_mode, st.st_mtime_ns)
            elif p.is_dir():
                out[rel] = ("dir", "", st.st_mode, st.st_mtime_ns)
            else:
                out[rel] = ("file", sha_file(p), st.st_mode, st.st_mtime_ns)
    return out


def changed(a: dict[str, tuple], b: dict[str, tuple]) -> list[str]:
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


# ---- the worlds ---------------------------------------------------------------------------------


def archive(ref: str, dest: Path) -> None:
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    tar = subprocess.run(
        ["git", "-C", str(STREAK), "archive", ref], capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=tar, check=True)


def cli(args: list[str], tmp: Path) -> subprocess.CompletedProcess:
    tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, **GO_ENV)
    env.update(TMPDIR=str(tmp), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(REPO))
    env.pop("ANTHROPIC_API_KEY", None)
    return subprocess.run(
        [sys.executable, "-m", "lattice.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
        check=False,
    )


def build_worlds(scratch: Path, workflows: Path) -> dict[str, dict[str, Any]]:
    """S-dyn and S4-stat: a clone, an inventory outside it, an [implementation] table, no state."""
    worlds: dict[str, dict[str, Any]] = {}
    for name, ref in REFS.items():
        clone = scratch / "worlds" / name
        archive(ref, clone)
        toml = clone / "studio.toml"
        toml.write_text(
            re.sub(r'(?m)^go_src\s*=\s*"[^"]*"', f'go_src = "{clone}"', toml.read_text())
        )
        wf = workflows
        if name == "S4-stat":
            out = scratch / "worlds" / "S4-stat-wf"
            shutil.rmtree(out, ignore_errors=True)
            r = cli(["import", "workflows", str(clone), "--out", str(out), "--static",
                     "--user", "ann", "--user", "ben"], scratch / "tmp" / "wf")  # fmt: skip
            assert r.returncode == 0, r.stderr
            wf = out / "workflows.json"
        inv = scratch / "worlds" / f"{name}-inv"
        shutil.rmtree(inv, ignore_errors=True)
        r = cli(["import", "inventory", str(clone), "--out", str(inv), "--workflows", str(wf)],
                scratch / "tmp" / "inv")  # fmt: skip
        assert r.returncode == 0, r.stderr
        toml.write_text(
            toml.read_text().rstrip("\n")
            + f'\n\n[implementation]\ninventory = "{inv}"\napp_root = "{clone}"\n'
            + "\n[state]\nenabled = false\n"
        )
        worlds[name] = {"clone": clone, "inventory": inv}
    return worlds


def world_facts(world: dict[str, Any]) -> dict[str, Any]:
    """What the scorer needs from a world: D8 checks, evidence locations, keys, rule ids, and the
    report hash (it must equal the one the keys were derived from)."""
    from lattice.spec.draft import load_spec
    from lattice.spec.implementation import (
        build_report,
        check_code,
        evidence_locations,
        load_inventory,
        report_json,
    )
    from lattice.spec.rules import Trace

    clone = world["clone"]
    spec, errs = load_spec((clone / "spec.py").read_text())
    assert spec is not None, errs
    trace = Trace.from_json((clone / "spec.trace.json").read_text())
    inv = load_inventory(world["inventory"], clone)
    rep = build_report(spec, trace, inv)
    ev = evidence_locations(inv)

    def check(path: str, line: int) -> bool:
        f = clone / path
        text = ""
        with contextlib.suppress(OSError):
            text = f.read_text(errors="replace").split("\n")[line - 1] if line >= 1 else ""
        why = check_code(clone, inv, {"target": f"code:{path}:{line}", "quote": text.strip()})
        return why is None or why.startswith("the quote has fewer than")

    return {
        "check": check,
        "evidence": lambda p, n: f"{p}:{n}" in ev,
        "keys": {e["key"] for e in rep["endpoints"]},
        "rules": {r.id for r in trace.rules},
        "report_sha256": hashlib.sha256(report_json(rep).encode()).hexdigest(),
    }


# ---- the claude sessions (helpers after dogfood/contracts/agent_probes.py) ----------------------


def parse_stream(path: Path) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    if not path.exists():
        return recs
    for ln in path.read_text(errors="replace").splitlines():
        try:
            d = json.loads(ln.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict):
            recs.append(d)
    return recs


def stop_process(p: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(p.pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        p.wait(timeout=10)


def launch(argv: list[str], env: dict[str, str], cwd: Path, sdir: Path) -> dict[str, Any]:
    t0 = time.time()
    timed_out = False
    with (sdir / "transcript.jsonl").open("wb") as fo, (sdir / "stderr.txt").open("wb") as fe:
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=fo,
                             stderr=fe, start_new_session=True)  # fmt: skip
        try:
            rc = p.wait(timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_process(p)
            rc = p.returncode if p.returncode is not None else -9
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(p.pid, signal.SIGKILL)
    return {"rc": rc, "timed_out": timed_out, "wall_s": round(time.time() - t0, 1)}


def result_of(recs: list[dict[str, Any]]) -> dict[str, Any] | None:
    res = [r for r in recs if r.get("type") == "result"]
    return res[-1] if res else None


def init_of(recs: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((r for r in recs if r.get("type") == "system" and r.get("subtype") == "init"), None)


def infra_failure(recs: list[dict[str, Any]], run: dict[str, Any]) -> str | None:
    from lattice.llm.cli_client import _RATE_LIMIT_RE

    if run["timed_out"]:
        return "timeout"
    res, ini = result_of(recs), init_of(recs)
    if res is None:
        return "no_result_record"
    if ini is not None:
        st = {s.get("name"): s.get("status") for s in ini.get("mcp_servers", [])}
        if st.get("lattice") != "connected":
            return f"lattice_not_connected:{st.get('lattice')}"
    if res.get("is_error") and _RATE_LIMIT_RE.search(str(res.get("result", ""))):
        return "rate_limit_or_overloaded"
    return None


def usage_limit(recs: list[dict[str, Any]], stderr: str) -> bool:
    from lattice.llm.cli_client import _USAGE_LIMIT_RE

    if _USAGE_LIMIT_RE.search(stderr):
        return True
    res = result_of(recs)
    return bool(
        res is not None
        and (res.get("is_error") or res.get("subtype") != "success")
        and _USAGE_LIMIT_RE.search(str(res.get("result", "")))
    )


def answer_of(recs: list[dict[str, Any]]) -> str:
    res = result_of(recs)
    if res is not None and isinstance(res.get("result"), str) and res["result"].strip():
        return str(res["result"])
    last = ""
    for r in recs:
        if r.get("type") == "assistant":
            texts = [
                b.get("text", "")
                for b in r.get("message", {}).get("content", [])
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if texts:
                last = "\n".join(texts)
    return last


def tool_trace(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    uses: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in recs:
        content = (
            r.get("message", {}).get("content") if r.get("type") in ("assistant", "user") else None
        )
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if r["type"] == "assistant" and b.get("type") == "tool_use":
                uses[b["id"]] = {"name": b.get("name"), "input": b.get("input"), "id": b["id"]}
                order.append(b["id"])
            elif r["type"] == "user" and b.get("type") == "tool_result":
                u = uses.get(b.get("tool_use_id"))
                if u is None:
                    continue
                c = b.get("content")
                if isinstance(c, list):
                    c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                u["is_error"] = bool(b.get("is_error"))
                u["result"] = str(c)[:200] if c is not None else ""
    return [uses[i] for i in order]


def tokens_of(res: dict[str, Any] | None, cache_reads: bool = False) -> int | None:
    """Input + output + cache creation; cache reads only with `cache_reads` (reported beside)."""
    u = (res or {}).get("usage")
    if not isinstance(u, dict):
        return None
    keys = ["input_tokens", "cache_creation_input_tokens", "output_tokens"]
    if cache_reads:
        keys.append("cache_read_input_tokens")
    return sum(int(u.get(k) or 0) for k in keys)


def mcp_config(clone: Path) -> dict[str, Any]:
    return {
        "mcpServers": {
            "lattice": {
                "command": sys.executable,
                "args": ["-m", "lattice.mcp", "serve", "--project", str(clone), "--readonly"],
                "env": {"PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"},
            }
        }
    }


def argv_for(mcp_json: Path, question: str, with_tools: bool) -> list[str]:
    allowed = ALLOWED_BASE + (IMPL if with_tools else [])
    disallowed = DISALLOWED_BASE + ([] if with_tools else IMPL)
    return [
        "claude", "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
        "--setting-sources", "", "--disable-slash-commands", "--mcp-config", str(mcp_json),
        "--tools", "Read,Grep,Glob", "--permission-mode", "dontAsk",
        "--allowedTools", ",".join(allowed), "--disallowedTools", ",".join(disallowed),
        "--max-turns", MAX_TURNS, "--max-budget-usd", MAX_BUDGET, "--model", MODEL, "--",
        PROMPT.format(Q=question),
    ]  # fmt: skip


# ---- arm E2: python -m lattice.spec ask --inventory ---------------------------------------------

E2_LABEL = {
    "implemented_verified": "IMPLEMENTED-VERIFIED",
    "implemented_unverified": "IMPLEMENTED-UNVERIFIED",
    "required_not_implemented": "REQUIRED-NOT-IMPLEMENTED",
    "not_specified_not_implemented": "NOT-SPECIFIED-NOT-IMPLEMENTED",
    "ambiguous": "AMBIGUOUS",
}


def run_e2(
    worlds: dict[str, dict[str, Any]], facts: dict[str, dict[str, Any]], out: Path
) -> dict[str, Any]:
    """The 13 questions through the ask CLI (ClaudeCliClient, sonnet), scored on B2-B5."""
    from lattice.llm.cli_client import _USAGE_LIMIT_RE

    rows, spent, stop = [], 0.0, None
    for q in CASES["questions"]:
        row: dict[str, Any] = {"id": q["id"], "status": "NOT RUN", "reason": None}
        rows.append(row)
        if stop:
            row["reason"] = stop
            continue
        if spent + 0.20 > E2_CAP:
            stop = "cap (USD 2.00)"
            row["reason"] = stop
            continue
        w = worlds[q["world"]]
        env = child_env(out / "e2" / q["id"])
        (out / "e2" / q["id"] / "tmp").mkdir(parents=True, exist_ok=True)
        env["PYTHONPATH"] = f"{REPO}{os.pathsep}{w['clone']}"
        cmd = [sys.executable, "-m", "lattice.spec", "ask", "--spec", "spec:SPEC", "--trace",
               str(w["clone"] / "spec.trace.json"), "--inventory", str(w["inventory"]),
               "--app-root", str(w["clone"]), "--model", MODEL, "--json", q["text"]]  # fmt: skip
        before = snapshot(w["clone"])
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            cwd=w["clone"],
            timeout=TIMEOUT_S,
            check=False,
        )
        row["world_changed"] = changed(before, snapshot(w["clone"]))
        if p.returncode != 0:
            limit = bool(_USAGE_LIMIT_RE.search(p.stderr))
            row.update(
                status="NOT RUN (usage limit)" if limit else "ERROR", reason=p.stderr.strip()[-300:]
            )
            if limit:
                stop = "NOT RUN (usage limit)"
            continue
        a = json.loads(p.stdout)
        spent += float(a["usage"].get("cost_usd") or 0)
        label = E2_LABEL.get(a.get("implementation") or "")
        codes = [c["target"] for c in a["citations"] if c["target"].startswith("code:")]
        text = "\n".join([label or "", a["answer"], a["explanation"], *codes])
        fx = facts[q["world"]]
        items = {
            "B2": label == q["key"],
            "B3": S.b3(text, label, fx["check"], fx["evidence"])["pass"],
            "B4": S.b4(text, fx["keys"])["pass"],
            "B5": S.b5(text, q["key"], fx["rules"], q.get("key_rules", []))["pass"],
        }
        row.update(
            status="SCORED",
            label=label,
            kind=a["kind"],
            items=items,
            cost_usd=a["usage"].get("cost_usd"),
            tokens=a["usage"].get("tokens"),
            rounds=a["usage"].get("rounds"),
            rejected=len(a.get("rejected_citations") or []),
            answer=a["answer"][:300],
        )
        (out / "e2" / f"{q['id']}.json").write_text(json.dumps(a, indent=1) + "\n")
    scored = [r for r in rows if r["status"] == "SCORED"]
    return {
        "rows": rows,
        "scored": len(scored),
        "B2": f"{sum(r['items']['B2'] for r in scored)}/{len(scored)}",
        "cost_usd": round(spent, 4),
        "stop": stop,
    }


# ---- main ---------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap_ = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap_.add_argument("--scratch", type=Path, default=None)
    ap_.add_argument("--out", required=True, type=Path)
    ap_.add_argument("--workflows-dyn", required=True, type=Path)
    ap_.add_argument("--write-freeze", action="store_true")
    ap_.add_argument("--e2", action="store_true", help="also run arm E2 (report only)")
    ap_.add_argument(
        "--dry-run", action="store_true", help="build the worlds, print argv, call no model"
    )
    ns = ap_.parse_args(argv)
    out = ns.out.resolve()
    wf = ns.workflows_dyn.resolve()
    if ns.write_freeze:
        return write_freeze(out, wf)
    why = verify_freeze(out, wf)
    if why:
        print(f"refused: {why}", file=sys.stderr)
        return 2
    if ns.scratch is None:
        print("--scratch DIR is required", file=sys.stderr)
        return 2
    scratch = ns.scratch.resolve()
    out.mkdir(parents=True, exist_ok=True)
    worlds = build_worlds(scratch, wf)
    facts = {name: world_facts(w) for name, w in worlds.items()}
    for name, f in facts.items():
        if f["report_sha256"] != CASES["report_sha256"][name]:
            print(
                f"refused: the {name} report differs from the one the keys come from",
                file=sys.stderr,
            )
            return 2
    freeze = json.loads((out / FREEZE_FILE).read_text())
    version = claude_version()
    state: dict[str, Any] = {"spent": 0.0, "tokens": 0, "stop": None}
    sessions: list[dict[str, Any]] = []
    t0 = time.time()

    def save() -> None:
        (out / "sessions.json").write_text(json.dumps(sessions, indent=1, default=str) + "\n")

    for idx, (sid, qid, with_tools) in enumerate(PLAN):
        q = QUESTIONS[qid]
        rec: dict[str, Any] = {
            "sid": sid,
            "question": qid,
            "world": q["world"],
            "key": q["key"],
            "with_tools": with_tools,
            "status": "NOT RUN",
            "reason": None,
        }
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
        mcp_json.write_text(json.dumps(mcp_config(w["clone"]), indent=1))
        shutil.copy2(mcp_json, sdir / "mcp.json")
        cmd = argv_for(mcp_json, q["text"], with_tools)
        (sdir / "argv.json").write_text(json.dumps(cmd, indent=1) + "\n")
        if ns.dry_run:
            print(json.dumps(cmd, indent=1))
            rec["reason"] = "dry run"
            save()
            break
        env = child_env(s_run)
        before = {
            k: snapshot(v) for k, v in {"clone": w["clone"], "inventory": w["inventory"]}.items()
        }
        run = launch(cmd, env, w["clone"], sdir)
        stderr = (sdir / "stderr.txt").read_text(errors="replace")
        recs = parse_stream(sdir / "transcript.jsonl")
        subprocess.run(["pkill", "-f", "--", f"--project {w['clone']}"], check=False)
        after = {
            k: snapshot(v) for k, v in {"clone": w["clone"], "inventory": w["inventory"]}.items()
        }
        res, ini = result_of(recs), init_of(recs)
        cost = res.get("total_cost_usd") if res else None
        limit, infra = usage_limit(recs, stderr), infra_failure(recs, run)
        state["spent"] += cost if cost is not None else SESSION_CAP
        toks = tokens_of(res)
        state["tokens"] += toks or 0
        diffs = {k: changed(before[k], after[k]) for k in before}
        rec.update(
            run=run, cost_usd=cost, tokens=toks, tokens_incl_cache_reads=tokens_of(res, True),
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
        answer = answer_of(recs)
        (sdir / "answer.txt").write_text(answer + "\n")
        trace = tool_trace(recs)
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
    e2 = run_e2(worlds, facts, out) if ns.e2 and not state["stop"] else None
    scored = [s for s in sessions if s["status"] == "SCORED"]
    main_s = [s for s in scored if s["with_tools"]]
    abl = [s for s in scored if not s["with_tools"]]
    n_pass = sum(1 for s in main_s if s["score"]["pass"])

    def tally(rows: list[dict[str, Any]], item: str) -> str:
        rel = [s for s in rows if s["score"]["items"].get(item) is not None]
        return f"{sum(1 for s in rel if s['score']['items'][item])}/{len(rel)}"

    b7 = [s["score"]["items"]["B7"] for s in scored]
    evaluated = len(main_s) == 13
    rule = (n_pass >= 11 and all(b7) and len(scored) == 15) if evaluated else None
    summary = {
        "experiment": "2026-10-15-implementation-diff PQ",
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
            }
            for s in sessions
        ],  # fmt: skip
        "scored": len(scored),
        "main_sessions_scored": len(main_s),
        "main_sessions_pass": n_pass,
        "items_main": {
            i: tally(main_s, i) for i in ("B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8")
        },
        "items_ablation": {i: tally(abl, i) for i in ("B2", "B3", "B4", "B5", "B6", "B7", "B8")},
        "B7": f"{sum(1 for x in b7 if x)}/{len(b7)}",
        "pass_rule_evaluated": evaluated,
        "pass_rule_met": rule,
        "n": len(main_s),
        "total_cost_usd": round(state["spent"], 4),
        "total_tokens": state["tokens"],
        "token_definition": "input + output + cache creation (cache reads excluded); cap 1000000",
        "total_tokens_incl_cache_reads": sum(
            s.get("tokens_incl_cache_reads") or 0 for s in sessions
        ),
        "e2": e2,
        "stop": state["stop"],
        "wall_s": round(time.time() - t0, 1),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "freeze"}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
