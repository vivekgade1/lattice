"""The real-agent exercise of the go-to place (docs/results/2026-10-16-goto-place-prereg.md, section 5).

    uv run python dogfood/goto_agent.py --questions "$SCRATCH/questions" --world OUT/qset/world.json \\
        --usd-cap 6 --out OUT/agent

One fresh ``claude -p --output-format stream-json`` session per question, with the exact argv, MCP
settings and system prompt that Studio's ``session.py`` builds for the ask mode (``--readonly``), on the
Streak scratch project of the question-set stage (``[implementation]`` and ``[impact]``, the six change
records already in its state directory). Model ``sonnet``, ``--max-turns 12``, 600 s per question, and a
total USD cap on the CLI's list estimate: no further question starts once the cap is reached. The
environment carries no API key (``cli_env`` strips every credential variable); the CLI uses the
owner's own login. A usage limit stops the exercise: the answers so far are kept, the rest are
reported ``not_run``, and the run is partial. It is **never** scored as a pass.

The answers are scored by the frozen scorer (``goto_score.py --surface agent``). Up to two extra
questions run in the change mode with ``observe: true`` (tier 1, Docker); they are not frozen, their
expected ids come from the CLI at ``--tiers 123`` and they are reported separately.
"""

# ruff: noqa: E501 -- probe tables and mutant texts are data lines
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("GOTO_AGENT_REPO", HERE.parent)).resolve()
sys.path.insert(0, str(REPO))

MODEL = "sonnet"
MAX_TURNS = 12
TIMEOUT_S = 600
OBSERVE_TIMEOUT_S = (
    1500  # a tier 1 run starts Docker: the 600 s of the frozen questions would cut it
)
OBSERVE = (
    "What fails on today's app if {cid} is applied? Run the observed check "
    "(change_impact with observe true) and report the failing items with their ids."
)


def cli_argv(
    project: Path, mode: str, text: str, scratch: Path, python: str, cap_turns: int
) -> list[str]:
    from lattice.studio import session as S  # noqa: PLC0415

    cfg = scratch / "cfg" / f"{mode}-{abs(hash(text)) % 10**8}"
    mcp, settings = S.write_configs(cfg, mode, project, python)
    prompt = S.system_prompt(mode, project, [], None)
    argv = S.build_argv(
        claude_bin=shutil.which("claude") or "claude", mode=mode, text=text, model=MODEL,
        mcp_config=mcp, settings=settings, prompt=prompt, extra_allowed=S.impact_tools(project, mode),
    )  # fmt: skip
    i = argv.index("--")
    argv[i:i] = ["--max-turns", str(cap_turns)]
    return argv


def child_env(python: str) -> dict[str, str]:
    from lattice.llm.cli_client import cli_env  # noqa: PLC0415
    from lattice.studio import session as S  # noqa: PLC0415

    env = cli_env(dict(os.environ), S.DEFAULT_MAX_TOKENS)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(REPO), env.get("PYTHONPATH", "")) if p)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for k, v in S.CONNECT_ENV.items():
        env.setdefault(k, v)
    assert not any(k.startswith("ANTHROPIC_") for k in env), "no API key reaches the CLI"
    return env


def run_question(
    argv: list[str], env: dict[str, str], cwd: Path, timeout: int, raw_path: Path
) -> dict[str, Any]:
    from lattice.llm.cli_client import _USAGE_LIMIT_RE  # noqa: PLC0415

    t0 = time.perf_counter()
    proc = subprocess.Popen(
        argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True,
    )  # fmt: skip
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
    wall = round(time.perf_counter() - t0, 1)
    raw_path.write_text(out)
    result: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = []
    texts: list[str] = []
    for line in out.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") == "result":
            result = rec
        elif rec.get("type") == "assistant" and not rec.get("parent_tool_use_id"):
            for b in (rec.get("message") or {}).get("content") or []:
                if b.get("type") == "tool_use":
                    tools.append({"name": b.get("name"), "input": b.get("input")})
                elif b.get("type") == "text":
                    texts.append(str(b.get("text") or ""))
    row: dict[str, Any] = {"wall_s": wall, "tools": tools, "timed_out": timed_out}
    blob = (err or "") + "\n" + json.dumps(result or {})
    if result is None:
        row.update(status="usage_limit" if _USAGE_LIMIT_RE.search(blob) else "error",
                   text="", error=(err or out)[-500:] or f"exit {proc.returncode}")  # fmt: skip
        if timed_out:
            row.update(status="error", error=f"no answer within {timeout} s")
        return row
    u = result.get("usage") or {}
    row.update(
        cost_usd=float(result.get("total_cost_usd") or 0.0),
        turns=int(result.get("num_turns") or 0),
        input_tokens=int(u.get("input_tokens") or 0),
        output_tokens=int(u.get("output_tokens") or 0),
        cache_read_tokens=int(u.get("cache_read_input_tokens") or 0),
        cache_creation_tokens=int(u.get("cache_creation_input_tokens") or 0),
        subtype=result.get("subtype"),
    )  # fmt: skip
    text = str(result.get("result") or "") or (texts[-1] if texts else "")
    if result.get("is_error"):
        row.update(
            status="usage_limit" if _USAGE_LIMIT_RE.search(blob) else "error",
            text=text,
            error=text[-500:],
        )
    elif result.get("subtype") == "error_max_turns":
        row.update(status="error", text=text, error="max turns reached")
    else:
        row.update(status="ok", text=text)
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--questions", required=True)
    ap.add_argument(
        "--world", required=True, help="world.json written by goto_probes.py --stage qset"
    )
    ap.add_argument("--out", required=True)
    ap.add_argument("--usd-cap", type=float, default=6.0)
    ap.add_argument(
        "--observe", type=int, default=2, help="extra tier 1 questions (change mode), at most 2"
    )
    ap.add_argument("--only", help="comma list of question ids (a smoke run)")
    ap.add_argument(
        "--observe-only", action="store_true", help="run only the extra tier 1 questions"
    )
    args = ap.parse_args(argv)
    qdir = Path(args.questions).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    world = json.loads(Path(args.world).read_text())
    project = Path(world["project"])
    qs = json.loads((qdir / "qset.json").read_text())
    scratch = Path(world["scratch"]) / "agent"
    shutil.rmtree(scratch, ignore_errors=True)
    (scratch / "raw").mkdir(parents=True)
    if shutil.which("claude") is None:
        (out / "agent-usage.json").write_text(
            json.dumps({"status": "not_run", "why": "the claude CLI is not installed"})
        )
        print("NOT RUN: the claude CLI is not installed")
        return 2
    python = world.get("python") or sys.executable
    env = child_env(python)
    questions = [q for q in qs["questions"] if not args.only or q["id"] in args.only.split(",")]
    if args.observe_only:
        questions = []
    # The cap cannot cover 44 sessions (about USD 0.3 each), so the ones the hard gate is about run
    # first: impact, hypothetical, control, then the map questions. The order is stable within a set.
    priority = {"I_impact": 0, "H_hypothetical": 1, "C_control": 2}
    questions.sort(key=lambda q: priority.get(q["set"], 3))
    answers: dict[str, Any] = {}
    detail: dict[str, Any] = {}
    spent = 0.0
    stopped: str | None = None
    for q in questions:
        if spent >= args.usd_cap:
            stopped = f"cap: USD {spent:.2f} >= {args.usd_cap:.2f} before {q['id']}"
            break
        cmd = cli_argv(project, "ask", q["text"], scratch, python, MAX_TURNS)
        row = run_question(cmd, env, project, TIMEOUT_S, scratch / "raw" / f"{q['id']}.jsonl")
        spent += row.get("cost_usd", 0.0)
        answers[q["id"]] = {"text": row.get("text", ""), "status": row["status"]}
        detail[q["id"]] = {**{k: v for k, v in row.items() if k != "text"}, "set": q["set"],
                           "final_text_chars": len(row.get("text", ""))}  # fmt: skip
        print(
            f"{q['id']:<10} {row['status']:<12} ${row.get('cost_usd', 0):.3f} {row.get('turns', 0)} turns {row['wall_s']} s",
            flush=True,
        )
        if row["status"] == "usage_limit":
            stopped = f"usage limit at {q['id']}"
            break
    (out / "answers-agent.json").write_text(json.dumps({"answers": answers}, indent=1))
    # the extra tier 1 questions (change mode, observe), only if the frozen ones all ran
    extra: dict[str, Any] = {}
    if stopped is None and args.observe and (args.observe_only or not args.only):
        for cid in ("c1", "c2")[: min(args.observe, 2)]:
            if spent >= args.usd_cap:
                stopped = f"cap before the observe question for {cid}"
                break
            text = OBSERVE.format(cid=cid)
            cmd = cli_argv(project, "change", text, scratch, python, MAX_TURNS)
            token = f"gotop-agent-{os.getpid()}"
            env2 = {**env, "LATTICE_CONTAINER_OWNER": token}
            row = run_question(
                cmd, env2, project, OBSERVE_TIMEOUT_S, scratch / "raw" / f"observe-{cid}.jsonl"
            )
            with contextlib.suppress(Exception):
                from lattice.studio.state import remove_owned_containers  # noqa: PLC0415

                remove_owned_containers(token)
            spent += row.get("cost_usd", 0.0)
            extra[cid] = {**row, "question": text}
            print(
                f"observe {cid} {row['status']} ${row.get('cost_usd', 0):.3f} {row.get('turns', 0)} turns {row['wall_s']} s",
                flush=True,
            )
            if row["status"] == "usage_limit":
                stopped = f"usage limit at observe {cid}"
                break
    ok_rows = [d for d in detail.values() if d["status"] == "ok"]
    usage = {
        "model": MODEL,
        "max_turns": MAX_TURNS,
        "timeout_s": TIMEOUT_S,
        "usd_cap": args.usd_cap,
        "spent_usd_list_estimate": round(spent, 4),
        "stopped": stopped,
        "asked": len(detail),
        "ok": len(ok_rows),
        "of": len(questions),
        "input_tokens": sum(d.get("input_tokens", 0) for d in detail.values()),
        "output_tokens": sum(d.get("output_tokens", 0) for d in detail.values()),
        "cache_read_tokens": sum(d.get("cache_read_tokens", 0) for d in detail.values()),
        "cache_creation_tokens": sum(d.get("cache_creation_tokens", 0) for d in detail.values()),
        "turns": sum(d.get("turns", 0) for d in detail.values()),
        "wall_s": round(sum(d.get("wall_s", 0) for d in detail.values()), 1),
        "change_impact_calls": sum(
            1
            for d in detail.values()
            for t in d["tools"]
            if str(t["name"]).endswith("change_impact")
        ),
        "per_question": detail,
        "extra_observe": {
            k: {kk: vv for kk, vv in v.items() if kk != "tools"}
            | {"tool_names": [t["name"] for t in v["tools"]]}
            for k, v in extra.items()
        },  # noqa: E501
    }
    (out / "agent-usage.json").write_text(json.dumps(usage, indent=1, default=str))
    if extra:
        (out / "extra-observe.json").write_text(json.dumps(extra, indent=1, default=str))
    # the frozen scorer, once
    r = subprocess.run(
        [sys.executable, str(qdir / "goto_score.py"), "--qset", str(qdir / "qset.json"), "--surface", "agent",
         "--answers", str(out / "answers-agent.json"), "--reports", str(world["reports"]), "--json"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    (out / "score-agent.json").write_text(r.stdout)
    if r.returncode == 0:
        s = json.loads(r.stdout)["summary"]
        print(
            json.dumps(
                {
                    k: s.get(k)
                    for k in (
                        "n",
                        "correct",
                        "not_run",
                        "unsupported_failure_claims",
                        "unsupported_failure_claims_lenient",
                        "i_verbatim_id_compliance",
                        "gate",
                    )
                }
            )
        )
    else:
        print("scorer rc", r.returncode, r.stderr[-300:])
    print(f"spent USD {spent:.2f}; stopped: {stopped}")
    return 0 if stopped is None else 3


if __name__ == "__main__":
    sys.exit(main())
