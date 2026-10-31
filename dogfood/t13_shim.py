#!/usr/bin/env python3
"""claude shim of the T1-3 run (docs/results/2026-10-30-t13-unfamiliar-python-build-prereg.md).

Modelled on ``dn_real_agent_shim.py`` (not edited). Stands in for the ``claude`` binary (``Rebuilder(claude_bin=...)``).
For every ``-p`` invocation it

* strips every ``ANTHROPIC_*``, ``CLAUDE_CODE_USE_*`` and ``T13_*`` variable from the child's environment (no API key),
* drops any caller-given ``--max-budget-usd`` and ``--max-turns`` and adds its own before the prompt separator:
  ``--max-budget-usd <min(cap - margin - spent, per-call ceiling)>`` and ``--max-turns <T13_MAX_TURNS>``,
* refuses the call (exit 3, no call, one ledger line) when that remainder is below USD 0.05,
* appends ONE line per invocation to the append-only ledger (JSONL): the CLI's own ``total_cost_usd``, turns, subtype,
  exit code, session id, the cap it was given. Never the prompt.

Environment (set by t13_unfamiliar_build_run.py): T13_REAL (the real claude, or the fake one in a dry run), T13_LEDGER,
T13_CAP (total USD), T13_MARGIN, T13_CALL_CAP (USD ceiling of one call), T13_MAX_TURNS, T13_LOGDIR (raw stdout and
stderr of every call).
"""

# ruff: noqa: E501

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

MIN_REMAINING = 0.05
DROP = ("--max-budget-usd", "--max-turns")  # flags the shim owns: a caller's value is dropped with its argument


def read_ledger(ledger: Path) -> list[dict]:
    out: list[dict] = []
    if ledger.is_file():
        for line in ledger.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def spent(ledger: Path) -> float:
    return round(sum(float(d.get("cost_usd") or 0.0) for d in read_ledger(ledger)), 6)


def append(ledger: Path, entry: dict) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def remaining(ledger: Path, cap: float, margin: float, call_cap: float) -> float:
    """The budget of the next call: what is left of the cap (less the margin), at most one call's ceiling."""
    return round(min(cap - margin - spent(ledger), call_cap), 4)


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


def strip_owned(argv: list[str]) -> list[str]:
    """`argv` without the flags the shim owns (and their values); the prompt after ``--`` is left alone."""
    cut = argv.index("--") if "--" in argv else len(argv)
    head, tail = argv[:cut], argv[cut:]
    out: list[str] = []
    skip = False
    for a in head:
        if skip:
            skip = False
            continue
        if a in DROP:
            skip = True
            continue
        if any(a.startswith(f + "=") for f in DROP):
            continue
        out.append(a)
    return out + tail


def main() -> int:
    real = os.environ["T13_REAL"]
    ledger = Path(os.environ["T13_LEDGER"])
    cap = float(os.environ["T13_CAP"])
    margin = float(os.environ["T13_MARGIN"])
    call_cap = float(os.environ["T13_CALL_CAP"])
    max_turns = int(os.environ["T13_MAX_TURNS"])
    logdir = Path(os.environ["T13_LOGDIR"])
    argv = sys.argv[1:]
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_", "T13_")) and k != "AWS_BEARER_TOKEN_BEDROCK"
    }
    if "-p" not in argv and "--print" not in argv:
        os.execve(real, [real, *argv], env)  # --version and the like: no model call
    budget = remaining(ledger, cap, margin, call_cap)
    n = len(read_ledger(ledger)) + 1
    if budget < MIN_REMAINING:
        append(ledger, {"n": n, "refused": "cap reached", "cost_usd": 0.0, "rc": 3, "budget_left_usd": budget, "at": time.time()})
        print(json.dumps({"type": "result", "subtype": "error_cap", "is_error": True,
                          "result": "usage limit: the run's spend cap is reached (t13_shim)"}))  # fmt: skip
        return 3
    cleaned = strip_owned(argv)
    extra = ["--max-budget-usd", f"{budget:.2f}", "--max-turns", str(max_turns)]
    cut = cleaned.index("--") if "--" in cleaned else len(cleaned)
    cmd = [real, *cleaned[:cut], *extra, *cleaned[cut:]]
    logdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=os.getcwd())
    wall = time.time() - t0
    (logdir / f"call-{n:03d}.out").write_text(proc.stdout or "")
    (logdir / f"call-{n:03d}.err").write_text(proc.stderr or "")
    res = result_of(proc.stdout or "") or {}
    append(
        ledger,
        {
            "n": n,
            "cost_usd": float(res.get("total_cost_usd") or 0.0),
            "budget_flag_usd": budget,
            "max_turns_flag": max_turns,
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
            "at": t0,
        },
    )
    sys.stdout.write(proc.stdout or "")
    sys.stdout.flush()
    sys.stderr.write(proc.stderr or "")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
