"""L1: node --test over lattice/tsgen/rt (spec 2026-09-29 ui-a §5.4)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.tsgen.node import run_node

RT = Path(__file__).resolve().parents[2] / "lattice" / "tsgen" / "rt"


def test_runtime_fixtures() -> None:
    done = run_node(["--test", "rt.test.ts"], cwd=RT)
    assert done.returncode == 0, done.stdout[-6000:] + done.stderr[-2000:]


def test_runtime_fixtures_are_fresh(tmp_path: Path) -> None:
    """Final review #11: regenerating ts_answers.json (Python's answers) gives the checked-in
    file, as the L2 corpora's drift check does. The file records the Python version that
    wrote it; another version may answer differently and must regenerate it."""
    out = tmp_path / "ts_answers.json"
    gen = RT / "testdata" / "gen_ts_answers.py"
    done = subprocess.run([sys.executable, str(gen), str(out)], capture_output=True, text=True,
                          check=False, timeout=600)  # fmt: skip
    assert done.returncode == 0, done.stderr[-4000:]
    committed = (RT / "testdata" / "ts_answers.json").read_text()
    assert out.read_text() == committed, "ts_answers.json drifted: run gen_ts_answers.py"
