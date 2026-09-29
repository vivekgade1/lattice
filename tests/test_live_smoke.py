"""Live smoke test against the real Claude API. Run with: uv run pytest -m live"""

from pathlib import Path

import pytest

from bench.cli import main
from bench.envfile import load_env_file
from bench.report import read_outcomes

pytestmark = pytest.mark.live

load_env_file()


@pytest.mark.skip(
    reason="the tabular bench's live transport is the Anthropic API-key SDK, disabled in "
    "this project (lattice.llm.client.ApiKeyTransportDisabled); no CLI transport there yet"
)
def test_live_single_task_arms_a_and_b(tmp_path: Path) -> None:
    assert main(["run", "--arms", "A,B", "--tasks", "s01", "--out", str(tmp_path)]) == 0
    outcomes = read_outcomes(tmp_path / "outcomes.jsonl")
    assert {o.arm for o in outcomes} == {"A", "B"}
    assert all(o.output_tokens > 0 for o in outcomes)
