"""``python -m lattice.spec check --rules`` (Spike J, node J2; E7) on the compiled Go app of
``tests/spec/goapp`` (arm S1). The default rule is unchanged: a Go 422 body is still compared
as FastAPI's (finding F2). Skipped when ``go`` is missing or the binary cannot be built."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GOAPP = Path(__file__).parent / "goapp"
BASE = [sys.executable, "-m", "lattice.spec", "check", "--spec", "tests.spec.go_harness_rb:SPEC",
        "--app", "tests.spec.go_harness_rb:make_app", "--db", "tests.spec.go_harness_rb:build_db",
        "--runtime", "tests.spec.go_harness_rb:RUNTIME", "--login",
        "tests.spec.go_harness_rb:LOGIN", "--layers", "B", "--json"]  # fmt: skip


@pytest.fixture(scope="module")
def binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    go = shutil.which("go")
    if go is None:
        pytest.skip("go is not installed")
    out = tmp_path_factory.mktemp("goapp-cli") / "goapp"
    done = subprocess.run([go, "build", "-o", str(out), "."], cwd=GOAPP, capture_output=True,
                          text=True, timeout=600, check=False)  # fmt: skip
    if done.returncode != 0 or not out.exists():
        pytest.skip(f"cannot build the Go app: {done.stderr[-300:]}")
    return out


def cli(binary: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "LATTICE_GOAPP_BIN": str(binary)}
    return subprocess.run([*BASE, *extra], cwd=REPO, env=env, capture_output=True, text=True,
                          timeout=600, check=False)  # fmt: skip


def test_rules_passes_the_apps_rule_and_the_default_still_flags_the_422_body(binary):
    with_rules = cli(binary, "--rules", "tests.spec.go_harness_rb:RULES")
    assert with_rules.returncode == 0, with_rules.stderr[-400:]
    assert json.loads(with_rules.stdout)["findings"] == []
    default = cli(binary)
    assert default.returncode == 1
    assert len(json.loads(default.stdout)["findings"]) == 6  # F2, as in round 1


def test_a_rules_reference_that_does_not_load_is_an_error(binary):
    done = cli(binary, "--rules", "tests.spec.go_harness_rb:NOPE")
    assert done.returncode == 2 and "NOPE" in done.stderr


def test_help_names_the_option():
    done = subprocess.run([sys.executable, "-m", "lattice.spec", "check", "--help"], cwd=REPO,
                          capture_output=True, text=True, check=False)  # fmt: skip
    assert "--rules" in done.stdout
