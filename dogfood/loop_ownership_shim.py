#!/usr/bin/env python3
"""claude shim of the loop-ownership pilot (docs/results/2026-10-30-loop-ownership-prereg.md, section 8).

Modelled on ``t13_shim.py`` / ``dn_real_agent_shim.py`` (not edited). Stands in for the ``claude`` binary of every arm
(``Rebuilder(claude_bin=...)`` in arm A, the driver's own ``claude -p`` session in arms B and C). For every ``-p`` call:

* strips every ``ANTHROPIC_*``, ``CLAUDE_CODE_USE_*`` and ``LOSHIM_*`` variable (and ``AWS_BEARER_TOKEN_BEDROCK``) from
  the child's environment: no API key, ever;
* drops any caller-given ``--max-budget-usd`` / ``--max-turns`` (with their values) and adds its own before the prompt
  separator: ``--max-budget-usd min(run cap - spent in this run, pilot cap - spent in the pilot - margin)`` and
  ``--max-turns $LOSHIM_MAX_TURNS`` (40 for an arm-A step, 120 for a B or C session);
* refuses the call (exit 3, one ledger line, no model call) when that budget is below USD 0.02, or when this is the
  run's FIRST call and its prompt (the text after ``--``) or its ``--append-system-prompt`` text does not have the
  sha256 the driver captured from arm A's round 1 (``$LOSHIM_EXPECT_PROMPT_SHA256`` / ``..._RULES_SHA256``);
* streams the child's stdout through (the rebuild reads it live) and keeps every call's raw stdout and stderr
  (``$LOSHIM_LOGDIR/call-NNN.out|.err``);
* appends ONE line per invocation to the append-only ledger (JSONL; refusals included): the run (cell), the CLI's own
  ``total_cost_usd`` as reported (``cost_usd_reported``) and the call's own cost (``cost_usd``: on ``--resume`` the CLI
  reports the session's cumulative cost, pre-flight R2, so the call's own is the reported figure minus the session's
  last reported one), turns, subtype, session id, the caps it was given, the prompt hashes. Never the prompt.

The ledger's sum of ``cost_usd`` is therefore the sum over sessions of each session's last ``total_cost_usd`` (the
prereg's cost rule, section 6.1). A call killed with SIGTERM is terminated with its child and still gets its line; a
SIGKILLed one does not (its cost then shows up in the session's next reported total, or nowhere).

Environment (set by loop_ownership_run.py): LOSHIM_REAL, LOSHIM_LEDGER, LOSHIM_CELL, LOSHIM_RUN_CAP, LOSHIM_PILOT_CAP,
LOSHIM_MARGIN, LOSHIM_MIN, LOSHIM_MAX_TURNS, LOSHIM_LOGDIR, and optionally LOSHIM_EXPECT_PROMPT_SHA256,
LOSHIM_EXPECT_RULES_SHA256 and LOSHIM_PREPARE (a command run in the cwd before the run's first call, after its checks:
``./generate.sh``, so ``gen/`` exists before the first agent step in every arm; its exit code goes on the ledger line).
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DROP = ("--max-budget-usd", "--max-turns")  # flags the shim owns: a caller's value is dropped with its argument
SCRUB = ("ANTHROPIC_", "CLAUDE_CODE_USE_", "LOSHIM_")


def read_ledger(ledger: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if ledger.is_file():
        for line in ledger.read_text().splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append(d)
    return out


def _cost(d: dict[str, Any]) -> float:
    try:
        return float(d.get("cost_usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def spent(ledger: Path, cell: str | None = None) -> float:
    """USD spent: every ledger line (the canary's rows included), or the lines of one run (cell)."""
    return round(sum(_cost(d) for d in read_ledger(ledger) if cell is None or d.get("cell") == cell), 6)


def append(ledger: Path, entry: dict[str, Any]) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def budget(ledger: Path, cell: str, run_cap: float, pilot_cap: float, margin: float) -> float:
    """The ``--max-budget-usd`` of the run's next call (prereg section 8)."""
    return round(min(run_cap - spent(ledger, cell), pilot_cap - spent(ledger) - margin), 4)


def session_reported(ledger: Path, session_id: str) -> float:
    """The session's last reported cumulative cost (0 for a new session)."""
    best = 0.0
    for d in read_ledger(ledger):
        if session_id and d.get("session_id") == session_id:
            v = d.get("cost_usd_reported", d.get("cost_usd"))
            try:
                best = max(best, float(v or 0.0))
            except (TypeError, ValueError):
                continue
    return best


def result_of(stdout: str) -> dict[str, Any] | None:
    """The CLI's result object: the last ``type == result`` line of a stream."""
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
    return found


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


def texts(argv: list[str]) -> tuple[str, str]:
    """(the prompt after ``--``, the ``--append-system-prompt`` text) of a ``claude -p`` argv."""
    cut = argv.index("--") if "--" in argv else len(argv)
    head = argv[:cut]
    prompt = argv[cut + 1] if cut + 1 < len(argv) else ""
    rules = head[head.index("--append-system-prompt") + 1] if "--append-system-prompt" in head else ""
    return prompt, rules


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def refuse(ledger: Path, entry: dict[str, Any], message: str) -> int:
    append(ledger, {**entry, "cost_usd": 0.0, "rc": 3, "at": time.time()})
    # "usage limit" makes the rebuild stop instead of starting another round (error kind usage_limit)
    print(json.dumps({"type": "result", "subtype": "error_cap", "is_error": True, "result": f"usage limit: {message} (loop_ownership_shim)"}), flush=True)
    return 3


def main() -> int:
    real = os.environ["LOSHIM_REAL"]
    ledger = Path(os.environ["LOSHIM_LEDGER"])
    cell = os.environ["LOSHIM_CELL"]
    run_cap = float(os.environ["LOSHIM_RUN_CAP"])
    pilot_cap = float(os.environ["LOSHIM_PILOT_CAP"])
    margin = float(os.environ["LOSHIM_MARGIN"])
    min_call = float(os.environ["LOSHIM_MIN"])
    max_turns = int(os.environ["LOSHIM_MAX_TURNS"])
    logdir = Path(os.environ["LOSHIM_LOGDIR"])
    want_prompt = os.environ.get("LOSHIM_EXPECT_PROMPT_SHA256") or ""
    want_rules = os.environ.get("LOSHIM_EXPECT_RULES_SHA256") or ""
    argv = sys.argv[1:]
    env = {k: v for k, v in os.environ.items() if not k.startswith(SCRUB) and k != "AWS_BEARER_TOKEN_BEDROCK"}
    if "-p" not in argv and "--print" not in argv:
        os.execve(real, [real, *argv], env)  # --version and the like: no model call
    lines = read_ledger(ledger)
    n = len(lines) + 1
    first = not any(d.get("cell") == cell for d in lines)
    prompt, rules = texts(argv)
    hashes = {"prompt_sha256": sha(prompt), "rules_sha256": sha(rules)}
    left = budget(ledger, cell, run_cap, pilot_cap, margin)
    base = {"n": n, "cell": cell, "first_call": first, "budget_left_usd": left, "resume": "--resume" in argv, **hashes}
    if left < min_call:
        return refuse(ledger, {**base, "refused": "cap reached"}, f"the run's or the pilot's spend cap is reached (USD {left:.4f} left)")
    if first and (want_prompt or want_rules) and (hashes["prompt_sha256"] != want_prompt or hashes["rules_sha256"] != want_rules):
        return refuse(ledger, {**base, "refused": "prompt hash mismatch", "expected_prompt_sha256": want_prompt, "expected_rules_sha256": want_rules},
                      "the run's first prompt is not byte-identical to arm A's captured round-1 texts")  # fmt: skip
    prepared: dict[str, Any] = {}
    prepare = os.environ.get("LOSHIM_PREPARE") or ""
    if first and prepare:  # gen/ before the run's first agent step (prereg section 3); arm A's rebuild generates only before its gates
        t_p = time.time()
        p = subprocess.run(["/bin/sh", "-c", prepare], cwd=os.getcwd(), env=env, capture_output=True, text=True, timeout=900)
        prepared = {"prepare": prepare, "prepare_rc": p.returncode, "prepare_s": round(time.time() - t_p, 2)}
    cleaned = strip_owned(argv)
    extra = ["--max-budget-usd", f"{left:.2f}", "--max-turns", str(max_turns)]
    cut = cleaned.index("--") if "--" in cleaned else len(cleaned)
    cmd = [real, *cleaned[:cut], *extra, *cleaned[cut:]]
    logdir.mkdir(parents=True, exist_ok=True)
    out_path, err_path = logdir / f"call-{n:03d}.out", logdir / f"call-{n:03d}.err"
    t0 = time.time()
    with out_path.open("w") as out_f, err_path.open("w") as err_f:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_f, stdin=subprocess.DEVNULL, text=True, env=env, cwd=os.getcwd())
        killed: list[int] = []

        def on_term(signum: int, _frame: Any) -> None:
            killed.append(signum)
            if proc.poll() is None:
                proc.terminate()

        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGINT, on_term)
        chunks: list[str] = []
        for line in proc.stdout or ():
            chunks.append(line)
            out_f.write(line)
            out_f.flush()
            try:
                sys.stdout.write(line)
                sys.stdout.flush()
            except (BrokenPipeError, OSError):
                pass
        proc.wait()
    wall = time.time() - t0
    res = result_of("".join(chunks)) or {}
    sid = str(res.get("session_id") or "")
    reported = float(res.get("total_cost_usd") or 0.0)
    own = round(max(reported - session_reported(ledger, sid), 0.0), 8) if res else 0.0
    append(
        ledger,
        {
            **base,
            **prepared,
            "cost_usd": own,
            "cost_usd_reported": reported,
            "budget_flag_usd": left,
            "max_turns_flag": max_turns,
            "rc": proc.returncode,
            "terminated_by_signal": killed[0] if killed else None,
            "is_error": bool(res.get("is_error")) if res else None,
            "subtype": res.get("subtype"),
            "terminal_reason": res.get("terminal_reason"),
            "stop_reason": res.get("stop_reason"),
            "num_turns": res.get("num_turns"),
            "session_id": sid or None,
            "duration_ms": res.get("duration_ms"),
            "wall_s": round(wall, 2),
            "models": sorted((res.get("modelUsage") or {}).keys()),
            "raw": out_path.name,
            "at": t0,
        },
    )
    try:
        sys.stderr.write(err_path.read_text())
    except OSError:
        pass
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
