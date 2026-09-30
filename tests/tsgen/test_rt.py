"""L1: node --test over lattice/tsgen/rt (spec 2026-09-29 ui-a §5.4)."""

from __future__ import annotations

from pathlib import Path

from tests.tsgen.node import run_node

RT = Path(__file__).resolve().parents[2] / "lattice" / "tsgen" / "rt"


def test_runtime_fixtures() -> None:
    done = run_node(["--test", "rt.test.ts"], cwd=RT)
    assert done.returncode == 0, done.stdout[-6000:] + done.stderr[-2000:]
