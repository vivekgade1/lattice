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

import time as time_mod
from pathlib import Path

import pytest

from bench.api_tasks import API_TASKS
from bench.cli import main
from bench.report import read_outcomes
from lattice.llm import cli_client
from lattice.llm.client import Generation, Usage

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "api"


def _fence(code: str) -> str:
    return f"```python\n{code}```\n"


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


def test_prepare_api_workdir_reps_are_independent_sibling_directories(tmp_path: Path) -> None:
    """Deterministic, timing-independent proof of the fix: _prepare_api_workdir's reps
    (including rep 0) must never be nested inside one another, so re-preparing any one
    rep can never delete or corrupt another rep's directory -- regardless of thread
    scheduling. An earlier version nested rep >= 1 inside rep 0's directory (task_id/
    rep1 under task_id itself), so re-preparing rep 0 (shutil.rmtree(root/task_id))
    deleted an in-flight rep 1/2 directory out from under it."""
    from bench.cli import _prepare_api_workdir

    root = tmp_path / "arm"
    rep0 = _prepare_api_workdir(root, "task1", 0)
    rep1 = _prepare_api_workdir(root, "task1", 1)
    rep2 = _prepare_api_workdir(root, "task1", 2)
    for rep_dir in (rep0, rep1, rep2):
        (rep_dir / "marker.txt").write_text("still here")

    # re-preparing rep 0 (the one whose path used to equal the shared parent) must not
    # touch rep 1's or rep 2's directory or its contents
    _prepare_api_workdir(root, "task1", 0)

    assert (rep1 / "marker.txt").read_text() == "still here"
    assert (rep2 / "marker.txt").read_text() == "still here"
    assert not rep1.is_relative_to(rep0)
    assert not rep2.is_relative_to(rep0)


def test_workers_4_reps_3_have_no_workdir_races(tmp_path: Path, monkeypatch) -> None:
    """Regression, at the bench.cli.main level: an earlier _prepare_api_workdir nested
    rep >= 1's directory inside rep 0's, so preparing rep 0 concurrently could delete an
    in-flight rep 1/2 directory out from under another worker (a real TOCTOU race
    between the exists() check and the mkdir/rmtree that follows it, reproduced as a
    FileExistsError harness_error under plain --workers 4 --reps 3). A sleep in the fake
    CLI, plus a delay patched into Path.exists (widening the exact TOCTOU window so the
    race is forced rather than left to luck), make the 3 reps of the one task overlap
    reliably under --workers 4. Every one of the 3 runs must score correct (not just be
    present) -- a corrupted/vanished workdir would silently score as a model failure,
    not necessarily a harness_error."""
    import pathlib

    real_exists = pathlib.Path.exists

    def _slow_exists(self: pathlib.Path, *a: object, **kw: object) -> bool:
        result = real_exists(self, *a, **kw)
        time_mod.sleep(0.02)  # widen the exists-check-then-mkdir/rmtree TOCTOU window
        return result

    monkeypatch.setattr(pathlib.Path, "exists", _slow_exists)

    task = API_TASKS[0]
    correct_code = (FIXTURES / task.id / "fastapi_correct.py").read_text()
    text = _fence(correct_code)

    class _SlowFakeCli:
        def __init__(self, model: str) -> None:
            self.model = model

        def generate(self, system: str, messages: object) -> Generation:
            time_mod.sleep(0.15)  # force the 3 reps to overlap under --workers 4
            return Generation(
                text=text,
                usage=Usage(10, 5),
                latency_s=0.0,
                stop_reason="end_turn",
                model=self.model,
                cost_usd=0.001,
                cost_basis="cli_list_estimate",
            )

    monkeypatch.setattr(cli_client, "ClaudeCliClient", _SlowFakeCli)
    out_dir = tmp_path / "out"

    exit_code = main(
        ["run", "--suite", "api", "--arms", "A", "--tasks", task.id,
         "--model", "m", "--reps", "3", "--workers", "4", "--out", str(out_dir)]
    )  # fmt: skip

    assert exit_code == 0
    outcomes = read_outcomes(out_dir / "outcomes.jsonl")
    assert sorted(o.rep for o in outcomes) == [0, 1, 2]
    assert all(o.failure != "harness_error" for o in outcomes), [
        (o.rep, o.failure, o.detail) for o in outcomes
    ]
    assert all(o.correct for o in outcomes), [(o.rep, o.failure, o.detail) for o in outcomes]


def test_models_flag_runs_every_model_in_one_invocation_with_one_writer(
    tmp_path: Path, monkeypatch
) -> None:
    fake_cli, _ = _fake_cli()
    monkeypatch.setattr(cli_client, "ClaudeCliClient", fake_cli)
    out_dir = tmp_path / "out"

    exit_code = main(
        ["run", "--suite", "api", "--arms", "A", "--tasks", "o01",
         "--models", "claude-sonnet-5,claude-haiku-4-5", "--out", str(out_dir)]
    )  # fmt: skip

    assert exit_code == 0
    outcomes = read_outcomes(out_dir / "outcomes.jsonl")
    assert sorted(o.requested_model for o in outcomes) == ["claude-haiku-4-5", "claude-sonnet-5"]
    assert len(outcomes) == 2  # one writer, one file: no per-model duplication


def test_models_flag_gives_each_model_its_own_workdir(tmp_path: Path, monkeypatch) -> None:
    """Two models' runs of the same (arm, task, rep) must not share -- and race on -- one
    workdir (the same class of bug as the --reps workdir race)."""
    task = API_TASKS[0]
    correct_code = (FIXTURES / task.id / "fastapi_correct.py").read_text()
    text = _fence(correct_code)

    class _SlowFakeCli:
        def __init__(self, model: str) -> None:
            self.model = model

        def generate(self, system: str, messages: object) -> Generation:
            time_mod.sleep(0.15)
            return Generation(
                text=text, usage=Usage(10, 5), latency_s=0.0, stop_reason="end_turn",
                model=self.model, cost_usd=0.001, cost_basis="cli_list_estimate",
            )  # fmt: skip

    monkeypatch.setattr(cli_client, "ClaudeCliClient", _SlowFakeCli)
    out_dir = tmp_path / "out"

    exit_code = main(
        ["run", "--suite", "api", "--arms", "A", "--tasks", task.id,
         "--models", "claude-sonnet-5,claude-haiku-4-5", "--workers", "4",
         "--out", str(out_dir)]
    )  # fmt: skip

    assert exit_code == 0
    outcomes = read_outcomes(out_dir / "outcomes.jsonl")
    assert len(outcomes) == 2
    assert all(o.correct for o in outcomes), [(o.requested_model, o.failure) for o in outcomes]


def test_outcomes_file_with_runs_is_refused_without_resume(tmp_path: Path, monkeypatch) -> None:
    fake_cli, _ = _fake_cli()
    monkeypatch.setattr(cli_client, "ClaudeCliClient", fake_cli)
    out_dir = tmp_path / "out"
    argv = [
        "run", "--suite", "api", "--arms", "A", "--tasks", "o01", "--model", "m",
        "--out", str(out_dir),
    ]  # fmt: skip

    assert main(argv) == 0
    with pytest.raises(SystemExit):
        main(argv)
    # not silently truncated
    assert len(read_outcomes(out_dir / "outcomes.jsonl")) == 1


def test_three_consecutive_cli_errors_stop_the_run(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _FailingCli:
        def __init__(self, model: str) -> None:
            self.model = model

        def generate(self, system: str, messages: object) -> Generation:
            raise cli_client.CliError("claude -p exited 1: model not found")

    monkeypatch.setattr(cli_client, "ClaudeCliClient", _FailingCli)
    out_dir = tmp_path / "out"
    argv = [
        "run", "--suite", "api", "--arms", "A", "--tasks", "o01,o02,o03,o04,o05",
        "--model", "m", "--out", str(out_dir),
    ]  # fmt: skip

    assert main(argv) == 4
    outcomes = read_outcomes(out_dir / "outcomes.jsonl")
    assert len(outcomes) == 3  # stopped after the 3rd, didn't burn through all 5 tasks
    assert all(o.failure == "harness_error" for o in outcomes)
    err = capsys.readouterr().err
    assert "3 consecutive CLI errors" in err and "stopping" in err
    # --resume redoes them (harness_error records are never "done"); the same failing
    # CLI still fails every call, so it hits the guard again.
    assert main([*argv, "--resume"]) == 4


def test_cli_errors_that_are_not_consecutive_do_not_stop_the_run(
    tmp_path: Path, monkeypatch
) -> None:
    calls = {"n": 0}

    class _FlakyCli:
        def __init__(self, model: str) -> None:
            self.model = model

        def generate(self, system: str, messages: object) -> Generation:
            calls["n"] += 1
            if calls["n"] in {1, 2, 4, 5}:
                raise cli_client.CliError("claude -p exited 1: model not found")
            return Generation(
                text="Sorry, no fenced code block here.",
                usage=Usage(1, 1), latency_s=0.0, stop_reason="end_turn",
                model=self.model, cost_usd=0.0, cost_basis="cli_list_estimate",
            )  # fmt: skip

    monkeypatch.setattr(cli_client, "ClaudeCliClient", _FlakyCli)
    out_dir = tmp_path / "out"

    exit_code = main(
        ["run", "--suite", "api", "--arms", "A", "--tasks", "o01,o02,o03,o04,o05",
         "--model", "m", "--out", str(out_dir)]
    )  # fmt: skip

    assert exit_code == 0
    outcomes = read_outcomes(out_dir / "outcomes.jsonl")
    assert len(outcomes) == 5
    assert sum(1 for o in outcomes if o.failure == "harness_error") == 4
