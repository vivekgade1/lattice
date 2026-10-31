#!/usr/bin/env python3
# ruff: noqa: E501
"""claude shim of T1-4 (docs/results/2026-10-30-t14-casko-corrected-citation-prereg.md, section 7).

Runs ``dn_real_agent_shim.main`` unchanged (ANTHROPIC_* stripped, ``--max-budget-usd`` from the remaining budget, one append-only
ledger line per invocation, raw output archived, refusal without a model call near the cap) with this run's margin: a call is refused
when ``cap - 0.07 - spent < 0.01``. 0.07 is 1.5 x the largest later-call cost of the .NET C-ASK; the CLI checks its budget only between
model calls (docs/results/2026-10-30-preflights.md P1), so the margin, not the flag, keeps one ask call inside the cap.
Environment: the DNRA_* variables of dn_real_agent_shim (set by t14_casko_run.shim_env).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dn_real_agent_shim as S  # noqa: E402

MARGIN = 0.07
MIN_REMAINING = 0.01

if __name__ == "__main__":
    S.MARGIN = MARGIN
    S.MIN_REMAINING = MIN_REMAINING
    sys.exit(S.main())
