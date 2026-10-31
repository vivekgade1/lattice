#!/usr/bin/env python3
"""Run the live Claude Code CLI canary and print a one-line verdict (stdlib only).

It runs ``pytest -m live tests/llm/test_cli_canary.py`` (CLI version vs the known-good list,
every flag in ``claude --help``, one capped haiku turn with the full rebuild flags, and the
``error_max_turns`` / ``error_max_budget_usd`` subtypes). Total spend is about USD 0.003; every
call is capped inside the test module and uses haiku without an API key.

Output: one line ``CLI-CANARY PASS|FAIL claude=<version> passed=<n> failed=<n> skipped=<n>``;
exit 0 on pass, 1 on fail (a failing case, an unknown version, or pytest/claude not found).
Set ``LATTICE_CANARY_LEDGER=<file.jsonl>`` to append one spend row per call.

Schedule it (daily, from the repo root, with the project venv)::

    # crontab -e
    17 7 * * * cd /path/to/lattice && .venv/bin/python scripts/cli_canary.py \
        >> ~/.cache/lattice-cli-canary.log

Stdout (the verdict line) goes to the log; stderr, which this script fills only on failure
with the pytest tail, is mailed by cron.

Cron has a minimal PATH: make sure ``claude`` resolves (put its directory on ``PATH=`` in the
crontab) and that the logged-in plan is usable from cron. Re-run by hand after a CLI upgrade.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_FILE = "tests/llm/test_cli_canary.py"


def _version() -> str:
    claude = shutil.which("claude")
    if claude is None:
        return "not-found"
    try:
        out = subprocess.run(
            [claude, "--version"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return (out.split() or ["unknown"])[0]


def main() -> int:
    version = _version()
    if version == "not-found":
        print("CLI-CANARY FAIL claude=not-found (no `claude` binary on PATH)")
        return 1
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "live",
            TEST_FILE,
            "-q",
            "-rf",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    tail = proc.stdout.strip().splitlines()[-1:] or [proc.stderr.strip()[-200:]]
    counts = {
        k: int(m.group(1)) if (m := re.search(rf"(\d+) {k}", tail[0])) else 0
        for k in ("passed", "failed", "skipped", "error")
    }
    ok = proc.returncode == 0 and counts["passed"] > 0
    print(
        f"CLI-CANARY {'PASS' if ok else 'FAIL'} claude={version} passed={counts['passed']} "
        f"failed={counts['failed'] + counts['error']} skipped={counts['skipped']}"
    )
    if not ok:  # the reason goes to stderr so cron mails it
        print(proc.stdout[-3000:], file=sys.stderr)
        print(proc.stderr[-1000:], file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
