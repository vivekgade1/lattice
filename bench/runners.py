"""Task runners: arm A (pandas baseline) now; arms B/C (Lattice) in Task 22."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import polars as pl

from bench.compare import frames_match
from bench.datasets import DATASETS
from bench.sandbox import run_python_script
from bench.tasks import Task
from lattice.llm.client import Generation, Generator, extract_code
from lattice.llm.prompt import BASELINE_SYSTEM, baseline_task_message

Arm = Literal["A", "B", "C"]


@dataclass(frozen=True)
class TaskOutcome:
    task_id: str
    arm: Arm
    tier: str
    correct: bool
    check_passed: bool | None
    rounds: int
    output_tokens: int
    input_tokens: int
    latency_s: float
    failure: str | None  # None when correct; else a taxonomy label
    detail: str


def prepare_workdir(task: Task, root: Path, fixtures: Path) -> Path:
    """Create ``root/<task.id>/`` with copies of the task's input CSVs."""
    workdir = root / task.id
    workdir.mkdir(parents=True, exist_ok=True)
    for name in task.inputs:
        filename = DATASETS[name].filename
        shutil.copyfile(fixtures / filename, workdir / filename)
    return workdir


def _baseline_outcome(
    task: Task,
    generation: Generation,
    *,
    correct: bool,
    failure: str | None,
    detail: str,
) -> TaskOutcome:
    return TaskOutcome(
        task_id=task.id,
        arm="A",
        tier=task.tier,
        correct=correct,
        check_passed=None,
        rounds=1,
        output_tokens=generation.usage.output_tokens,
        input_tokens=generation.usage.input_tokens,
        latency_s=generation.latency_s,
        failure=failure,
        detail=detail,
    )


def run_baseline(
    task: Task,
    generator: Generator,
    workdir: Path,
    fixtures_frames: Mapping[str, pl.DataFrame],
) -> TaskOutcome:
    """Generate one pandas script for ``task``, sandbox it, and score the result."""
    message = baseline_task_message(task)
    generation = generator.generate(BASELINE_SYSTEM, [{"role": "user", "content": message}])

    code = extract_code(generation.text)
    if code is None:
        return _baseline_outcome(
            task,
            generation,
            correct=False,
            failure="no_code",
            detail="response contained no ```python code block",
        )

    result = run_python_script(code, workdir)

    if result.timed_out:
        return _baseline_outcome(
            task,
            generation,
            correct=False,
            failure="timeout",
            detail="script exceeded the time limit",
        )

    if result.returncode != 0:
        return _baseline_outcome(
            task,
            generation,
            correct=False,
            failure="runtime_error",
            detail=result.stderr,
        )

    out_path = workdir / "out.parquet"
    if not out_path.exists():
        return _baseline_outcome(
            task,
            generation,
            correct=False,
            failure="no_output",
            detail="script did not write out.parquet",
        )

    try:
        actual = pl.read_parquet(out_path)
    except Exception as exc:
        return _baseline_outcome(
            task,
            generation,
            correct=False,
            failure="no_output",
            detail=f"could not read out.parquet: {exc}",
        )

    expected = task.reference(fixtures_frames)
    ok, reason = frames_match(actual, expected, ordered=task.ordered)
    if not ok:
        return _baseline_outcome(
            task, generation, correct=False, failure="wrong_result", detail=reason
        )

    return _baseline_outcome(task, generation, correct=True, failure=None, detail="")


def run_lattice(
    task: Task,
    generator: Generator,
    workdir: Path,
    fixtures_frames: Mapping[str, pl.DataFrame],
    *,
    arm: Literal["B", "C"],
    max_repairs: int,
) -> TaskOutcome:
    """Generate, check, and (arm B) repair a Lattice program. Lands in Task 22."""
    raise NotImplementedError("arm B lands in Task 22")
