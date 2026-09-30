"""Running Node from pytest (spec 2026-09-29 ui-a §5.4): skip without node, unless
LATTICE_REQUIRE_NODE=1, where a missing node fails the test."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

NODE_FLAGS = ["--experimental-strip-types", "--no-warnings=ExperimentalWarning"]


def need_node() -> str:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("LATTICE_REQUIRE_NODE") == "1":
            pytest.fail("node is required (LATTICE_REQUIRE_NODE=1)")
        pytest.skip("node is not installed")
    return node


def run_node(
    args: list[str], *, cwd: Path, stdin: str | None = None, timeout: float = 600
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [need_node(), *NODE_FLAGS, *args],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def node_json(script: str, payload: object, *, cwd: Path) -> object:
    """Run `script` (an ES module reading JSON from stdin, printing JSON) on `payload`."""
    path = cwd / "_script.mjs"
    path.write_text(script)
    done = run_node([str(path)], cwd=cwd, stdin=json.dumps(payload))
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)
