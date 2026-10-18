#!/usr/bin/env python3
"""claude shim for Arm L: adds --max-turns 40 and --max-budget-usd <remaining of the USD 5.00 cap>
to every agent step, tees the stream-json to a log, and keeps a ledger of the CLI's list-estimate cost.
Env: ARML_DIR (ledger.json, logs/), ARML_CAP (default 5.00), ARML_REAL (the real claude binary)."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

real = os.environ["ARML_REAL"]
d = Path(os.environ["ARML_DIR"])
cap = float(os.environ.get("ARML_CAP", "5.00"))
argv = sys.argv[1:]
if "-p" not in argv:
    os.execv(real, [real, *argv])
d.mkdir(parents=True, exist_ok=True)
(d / "logs").mkdir(exist_ok=True)
led_p = d / "ledger.json"
led = json.loads(led_p.read_text()) if led_p.exists() else {"calls": [], "spent_usd": 0.0}
remaining = round(cap - led["spent_usd"], 4)
n = len(led["calls"]) + 1
if remaining <= 0.05:
    led["calls"].append({"n": n, "refused": "cap reached", "at": time.time()})
    led_p.write_text(json.dumps(led, indent=1))
    print(json.dumps({"type": "result", "subtype": "error_cap", "is_error": True, "result": "Arm L cap reached"}))
    sys.exit(3)
extra = ["--max-turns", "40", "--max-budget-usd", f"{remaining:.2f}"]
i = len(argv) - 1 - argv[::-1].index("--") if "--" in argv else len(argv)
cmd = [real, *argv[:i], *extra, *argv[i:]]
log = d / "logs" / f"agent-{n:02d}.jsonl"
cost, turns, result = 0.0, None, None
p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=os.getcwd(), env=os.environ)
with log.open("w") as f:
    for line in p.stdout:
        f.write(line)
        f.flush()
        sys.stdout.write(line)
        sys.stdout.flush()
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            cost = float(ev.get("total_cost_usd") or 0.0)
            turns = ev.get("num_turns")
            result = {k: ev.get(k) for k in ("subtype", "is_error", "num_turns", "total_cost_usd", "duration_ms", "stop_reason")}
err = p.stderr.read()
rc = p.wait()
sys.stderr.write(err)
led = json.loads(led_p.read_text()) if led_p.exists() else led
led["calls"].append({"n": n, "rc": rc, "cost_usd": cost, "turns": turns, "result": result, "cwd": os.getcwd(), "resume": "--resume" in argv, "budget_flag": remaining, "stderr_tail": err[-300:]})
led["spent_usd"] = round(sum(c.get("cost_usd") or 0.0 for c in led["calls"]), 4)
led_p.write_text(json.dumps(led, indent=1))
sys.exit(rc)
