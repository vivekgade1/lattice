"""scripts/restart_drill.py as a test: a REAL Studio server process killed with SIGKILL in
the middle of an agent round (A), a level gate (B) and a level-repair round (C, API v1.11),
and a new one that resumes without running a completed agent round again. Stub planner and
agent, a throwaway project per drill; no LLM, no API key, no Docker."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "restart_drill.py"


@pytest.mark.parametrize("drill", ["A", "B", "C", "F"])
def test_the_restart_drill(drill: str) -> None:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), drill], capture_output=True, text=True, timeout=600
    )
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert f"Drill {drill}" in r.stdout and "PASS" in r.stdout
