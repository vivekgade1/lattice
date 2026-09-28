"""Regression: the edit-slice harness tests leave the template app intact for the tests
after them, in one pytest session.

``test_edit_slice_run.py``'s ``--workers`` tests build emitted apps from several threads;
``lattice.emit.thin.make_emitted_app`` used to swap the template's module-global
``api_router`` while doing so, and an interleaving left it empty for the rest of the
session (every later template test: ``login as 'alice' failed: 404``). The order matters,
so this runs the two in a pytest subprocess, in that order.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_edit_slice_workers_then_template_in_one_session():
    cmd = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:warnings",
        "tests/bench/test_edit_slice_run.py::test_workers_parallel_output_matches_sequential_order",
        "tests/bench/test_edit_slice_run.py::test_workers_run_concurrently_and_write_safely",
        "tests/bench/test_template_app.py::test_app_builds_on_sqlite",
        "tests/bench/test_template_app.py::test_seed_users_and_passwords",
    ]  # fmt: skip
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=900)
    tail = proc.stdout[-3000:] + proc.stderr[-2000:]
    assert proc.returncode == 0, tail
    assert " passed" in proc.stdout and "failed" not in proc.stdout, tail
