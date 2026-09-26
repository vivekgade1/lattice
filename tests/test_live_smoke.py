"""Live smoke test against the real Claude API. Run with: uv run pytest -m live"""

import os
from pathlib import Path

import pytest

from bench.cli import main
from bench.report import read_outcomes

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
    reason="no Anthropic credentials in the environment",
)
def test_live_single_task_arms_a_and_b(tmp_path: Path) -> None:
    assert main(["run", "--arms", "A,B", "--tasks", "s01", "--out", str(tmp_path)]) == 0
    outcomes = read_outcomes(tmp_path / "outcomes.jsonl")
    assert {o.arm for o in outcomes} == {"A", "B"}
    assert all(o.output_tokens > 0 for o in outcomes)
