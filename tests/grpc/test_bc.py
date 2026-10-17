"""P0-BC, the fast part: the extract JSON of every HTTP fixture, the Gin front end's model JSON
and ``select()``'s answers are byte-identical to ``git archive 6a318e5``'s (the hashes were
recorded from that tree by ``dogfood/grpc_probes.py --make-baseline``). The rest of the promise
(reports, goldens, CLI help, MCP, the suites' failing ids) is the probe's."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests.grpc import oracle
from tests.grpc.conftest import needs_go

pytestmark = needs_go
REPO = oracle.FIXTURES.parents[2]
BASELINE = REPO / "docs/results/data/2026-10-17-grpc/baseline-hashes.json"


@pytest.fixture(scope="module")
def hashes() -> dict[str, str]:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "dogfood/grpc_probes.py"),
            "emit-bc",
            str(REPO),
            "extract,model,select",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.splitlines()[-1])  # type: ignore[no-any-return]


def test_http_outputs_are_byte_identical_to_the_baseline(hashes: dict[str, str]) -> None:
    assert BASELINE.is_file(), "run dogfood/grpc_probes.py --make-baseline first"
    base = json.loads(BASELINE.read_text())
    base.pop("tree")
    assert sorted(hashes) == sorted(base)
    assert [k for k in base if hashes[k] != base[k]] == []
    assert sum(k.startswith("extract:") for k in base) == 15  # 13 fixtures, goapp, client-only
    assert base["select:gin_client_only"] == "gin"
