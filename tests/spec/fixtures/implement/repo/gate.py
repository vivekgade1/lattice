#!/usr/bin/env python3
"""The implement fixture's gate: the project's own check (layers A, C, D) on the spec in this
worktree and the app next to it. Prints Lattice's `PASS|FAIL: N findings (...)` line and exits 0
iff nothing is found. Runs in the gate's own process, in-process, no model."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
if os.environ.get("IMPLEMENT_PROBE_ENVLOG"):  # test hook: did the gate see an API key?
    with open(os.environ["IMPLEMENT_PROBE_ENVLOG"], "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"who": "gate", "key": any(k.startswith("ANTHROPIC") for k in os.environ)})
            + "\n"
        )

from lattice.spec.impact_report import harness_runner, read_side  # noqa: E402

here = Path(__file__).resolve().parent
run = harness_runner(here)
report = run(read_side(here).spec, "ACD")
for f in report.findings:
    print(f"  [{f.layer} {f.verdict}] {f.summary()}")
n = Counter(f.layer for f in report.findings)
detail = ", ".join(f"{k} {v}" for k, v in sorted(n.items()))
print(f"{'PASS' if not report.findings else 'FAIL'}: {len(report.findings)} findings ({detail})")
sys.exit(1 if report.findings else 0)
