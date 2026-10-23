#!/usr/bin/env python3
"""claude shim of the .NET real-agent run (docs/results/2026-10-23-dotnet-real-agent-prereg.md).

Stands in for the ``claude`` binary (``Rebuilder(claude_bin=...)`` and ``ClaudeCliClient(binary=...)``). For every
``-p`` invocation it

* strips every ``ANTHROPIC_*`` and ``CLAUDE_CODE_USE_*`` variable from the child's environment (no API key, ever),
* passes ``--max-budget-usd <min(remaining total, remaining of the phase, per-call ceiling)>`` (and ``--max-turns 40``
  for the build phase; any caller-given
  ``--max-budget-usd`` is dropped), and refuses the call (exit 3, no model call) when the remainder is below USD 0.05,
* appends ONE line per invocation to the append-only ledger (JSONL): the CLI's own ``total_cost_usd`` of its JSON or
  stream-json result, turns, exit code, duration, session id, the cap it was given. Never the prompt.

Environment (set by dn_real_agent_run.py): DNRA_REAL (the real claude), DNRA_LEDGER (the ledger file), DNRA_CAP (total
USD), DNRA_PHASE ("build" | "ask"), DNRA_PHASE_CAP (USD for this phase), DNRA_CALL_CAP (USD ceiling of one call),
DNRA_LOGDIR (raw stdout of every call is archived there).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

MIN_REMAINING = 0.05
MARGIN = 0.10  # kept back from the total so a small overshoot of one call stays inside the cap


def spent(ledger: Path, phase: str | None = None) -> float:
    total = 0.0
    if ledger.is_file():
        for line in ledger.read_text().splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if phase is None or d.get("phase") == phase:
                total += float(d.get("cost_usd") or 0.0)
    return round(total, 6)


def append(ledger: Path, entry: dict) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def result_of(stdout: str) -> dict | None:
    """The CLI's result object: the last ``type == result`` line of a stream, or the one JSON document."""
    found = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "result":
            found = ev
    if found is not None:
        return found
    try:
        doc = json.loads(stdout)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def main() -> int:
    real = os.environ["DNRA_REAL"]
    ledger = Path(os.environ["DNRA_LEDGER"])
    cap = float(os.environ["DNRA_CAP"])
    phase = os.environ["DNRA_PHASE"]
    phase_cap = float(os.environ["DNRA_PHASE_CAP"])
    call_cap = float(os.environ["DNRA_CALL_CAP"])
    logdir = Path(os.environ["DNRA_LOGDIR"])
    argv = sys.argv[1:]
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_", "DNRA_")) and k != "AWS_BEARER_TOKEN_BEDROCK"
    }
    if "-p" not in argv and "--print" not in argv:
        os.execve(real, [real, *argv], env)  # --version and the like: no model call
    remaining = round(min(cap - MARGIN - spent(ledger), phase_cap - spent(ledger, phase), call_cap), 4)  # the CLI may overshoot a budget slightly
    n = 1 + sum(1 for _ in ledger.open()) if ledger.is_file() else 1
    if remaining < MIN_REMAINING:
        append(
            ledger,
            {"n": n, "phase": phase, "refused": "cap reached", "cost_usd": 0.0, "rc": 3, "at": time.time()},
        )
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error_cap",
                    "is_error": True,
                    "result": "usage limit: the run's spend cap is reached (dn_real_agent_shim)",
                }
            )
        )
        return 3
    # drop a caller-given budget flag, then add ours before the prompt separator ("--") when there is one
    cleaned: list[str] = []
    skip = False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == "--max-budget-usd":
            skip = True
            continue
        cleaned.append(a)
    extra = ["--max-budget-usd", f"{remaining:.2f}"]
    if phase == "build" and "--max-turns" not in cleaned:
        extra += ["--max-turns", "40"]  # the Arm L precedent (accepted by the CLI although absent from --help)
    cut = len(cleaned) - 1 - cleaned[::-1].index("--") if "--" in cleaned else len(cleaned)
    cmd = [real, *cleaned[:cut], *extra, *cleaned[cut:]]
    logdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=os.getcwd())
    wall = time.time() - t0
    (logdir / f"call-{n:03d}.out").write_text(proc.stdout or "")
    (logdir / f"call-{n:03d}.err").write_text(proc.stderr or "")
    res = result_of(proc.stdout or "") or {}
    append(
        ledger,
        {
            "n": n,
            "phase": phase,
            "cost_usd": float(res.get("total_cost_usd") or 0.0),
            "budget_flag_usd": remaining,
            "rc": proc.returncode,
            "is_error": bool(res.get("is_error")) if res else None,
            "subtype": res.get("subtype"),
            "stop_reason": res.get("stop_reason"),
            "num_turns": res.get("num_turns"),
            "session_id": res.get("session_id"),
            "duration_ms": res.get("duration_ms"),
            "wall_s": round(wall, 2),
            "models": sorted((res.get("modelUsage") or {}).keys()),
            "resume": "--resume" in cleaned,
            "output_format": cleaned[cleaned.index("--output-format") + 1] if "--output-format" in cleaned else None,
            "at": t0,
        },
    )
    sys.stdout.write(proc.stdout or "")
    sys.stdout.flush()
    sys.stderr.write(proc.stderr or "")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
