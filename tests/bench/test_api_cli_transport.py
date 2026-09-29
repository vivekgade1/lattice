"""The api suite's live transport is the Claude CLI (``lattice.llm.cli_client.
ClaudeCliClient``), never the Anthropic API-key transport (project policy). These tests
monkeypatch ``ClaudeCliClient`` to a fake so no real ``claude -p`` process runs, and use
arm A with a response that has no fenced code block -- a "no_code" outcome that never
touches the FastAPI sandbox, keeping the tests fast while still exercising the full
``bench.cli.main`` -> ``_run_one_api`` -> ``bench.runners`` path (fixtures, DB, ledger,
JSONL round-trip). ``--reps``, ``--resume`` and ``--workers`` mirror
``bench.edit_slice.run``'s design of the same flags (see ``tests/bench/
test_edit_slice_run.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.cli import main
from bench.report import read_outcomes
from lattice.llm import cli_client
from lattice.llm.client import Generation, Usage


def _fake_cli(model_text: str | None = None, *, fail_on: set[int] | None = None):
    """A fake ``ClaudeCliClient``: every call answers with no fenced code (a fast,
    sandbox-free "no_code" outcome for arm A), unless its call number is in ``fail_on``,
    in which case it raises ``CliUsageLimitError``. Returns (class, call counter dict)."""
    calls = {"n": 0}

    class _FakeCli:
        def __init__(self, model: str) -> None:
            self.model = model

        def generate(self, system: str, messages: object) -> Generation:
            calls["n"] += 1
            if fail_on and calls["n"] in fail_on:
                raise cli_client.CliUsageLimitError("plan usage limit reached")
            return Generation(
                text="Sorry, no fenced code block here.",
                usage=Usage(20, 10),
                latency_s=0.001,
                stop_reason="end_turn",
                model=model_text or self.model,
                cost_usd=0.0123,
                cost_basis="cli_list_estimate",
            )

    return _FakeCli, calls


def test_live_api_run_uses_the_claude_cli_transport(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    class _FakeCli:
        def __init__(self, model: str) -> None:
            seen["model"] = model

        def generate(self, system: str, messages: object) -> Generation:
            return Generation(
                text="Sorry, no fenced code block here.",
                usage=Usage(20, 10),
                latency_s=0.02,
                stop_reason="end_turn",
                # A served model that differs from the requested one (a dated variant,
                # as the CLI can report): requested_model and model must stay distinct.
                model="claude-sonnet-5-20260115",
                cost_usd=0.0123,
                cost_basis="cli_list_estimate",
            )

    monkeypatch.setattr(cli_client, "ClaudeCliClient", _FakeCli)
    out_dir = tmp_path / "out"

    exit_code = main(
        ["run", "--suite", "api", "--arms", "A", "--tasks", "o01",
         "--model", "claude-sonnet-5", "--out", str(out_dir)]
    )  # fmt: skip

    assert exit_code == 0
    assert seen == {"model": "claude-sonnet-5"}  # ClaudeCliClient(model=...), not the API SDK
    [outcome] = read_outcomes(out_dir / "outcomes.jsonl")
    assert outcome.task_id == "o01"
    assert outcome.requested_model == "claude-sonnet-5"  # the CLI's --model argument
    assert outcome.model == "claude-sonnet-5-20260115"  # the served model, kept separate
    assert outcome.cost_usd == pytest.approx(0.0123)
    assert outcome.cost_basis == "cli_list_estimate"
    assert outcome.rep == 0
    assert outcome.failure == "no_code"


def test_harness_error_records_also_carry_the_requested_model(tmp_path: Path, monkeypatch) -> None:
    """A harness crash (not a model failure) must still carry ``requested_model``:
    ``bench.analysis.go_pilot.load`` requires the field on every record, including
    ``harness_error`` ones."""
    import bench.cli as bench_cli

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(bench_cli, "run_api_baseline", _boom)
    fake_cli, _ = _fake_cli()
    monkeypatch.setattr(cli_client, "ClaudeCliClient", fake_cli)
    out_dir = tmp_path / "out"

    exit_code = main(
        ["run", "--suite", "api", "--arms", "A", "--tasks", "o01",
         "--model", "claude-sonnet-5", "--out", str(out_dir)]
    )  # fmt: skip

    assert exit_code == 0
    [outcome] = read_outcomes(out_dir / "outcomes.jsonl")
    assert outcome.failure == "harness_error"
    assert outcome.requested_model == "claude-sonnet-5"


def test_reps_3_produces_3_records_per_arm_task(tmp_path: Path, monkeypatch) -> None:
    fake_cli, _ = _fake_cli()
    monkeypatch.setattr(cli_client, "ClaudeCliClient", fake_cli)
    out_dir = tmp_path / "out"

    exit_code = main(
        ["run", "--suite", "api", "--arms", "A", "--tasks", "o01",
         "--model", "m", "--reps", "3", "--out", str(out_dir)]
    )  # fmt: skip

    assert exit_code == 0
    outcomes = read_outcomes(out_dir / "outcomes.jsonl")
    assert len(outcomes) == 3
    assert sorted(o.rep for o in outcomes) == [0, 1, 2]
    assert {(o.arm, o.task_id) for o in outcomes} == {("A", "o01")}


def test_resume_after_a_usage_limit_stop_produces_exactly_the_missing_records(
    tmp_path: Path, monkeypatch
) -> None:
    # The 2nd model call (task o02's) hits a simulated plan usage limit; the run must
    # stop cleanly after o01 is written, without recording o02 as a failure.
    fake_cli, calls = _fake_cli(fail_on={2})
    monkeypatch.setattr(cli_client, "ClaudeCliClient", fake_cli)
    out_dir = tmp_path / "out"
    argv = [
        "run", "--suite", "api", "--arms", "A", "--tasks", "o01,o02",
        "--model", "m", "--out", str(out_dir),
    ]  # fmt: skip

    assert main(argv) == 3
    first = read_outcomes(out_dir / "outcomes.jsonl")
    assert [o.task_id for o in first] == ["o01"]

    # Resume: the 3rd call (o02's retry) no longer falls in fail_on, so it succeeds.
    assert main([*argv, "--resume"]) == 0
    assert calls["n"] == 3  # o01 (not redone) + the interrupted o02 + o02's retry

    second = read_outcomes(out_dir / "outcomes.jsonl")
    assert sorted(o.task_id for o in second) == ["o01", "o02"]
    assert len(second) == 2  # o01 kept, not duplicated; o02 appended exactly once


def test_workers_4_gives_the_same_record_set_as_workers_1(tmp_path: Path, monkeypatch) -> None:
    fake_cli, _ = _fake_cli()
    monkeypatch.setattr(cli_client, "ClaudeCliClient", fake_cli)
    common = [
        "run", "--suite", "api", "--arms", "A", "--tasks", "o01,o02",
        "--model", "m", "--reps", "2",
    ]  # fmt: skip
    out1 = tmp_path / "out1"
    out4 = tmp_path / "out4"

    assert main([*common, "--out", str(out1), "--workers", "1"]) == 0
    assert main([*common, "--out", str(out4), "--workers", "4"]) == 0

    keys1 = [(o.arm, o.task_id, o.rep) for o in read_outcomes(out1 / "outcomes.jsonl")]
    keys4 = [(o.arm, o.task_id, o.rep) for o in read_outcomes(out4 / "outcomes.jsonl")]
    assert len(keys1) == 4
    assert keys1 == keys4  # same set, in the same deterministic (submission) order
