"""Task runners: arm A (pandas baseline), arms B/C (Lattice generate/check/repair)."""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import polars as pl

from bench.compare import frames_match
from bench.datasets import DATASETS, bench_profile
from bench.sandbox import run_python_script
from bench.tasks import Task
from lattice.diagnostics import Diagnostic, Severity, render_for_llm, sort_key
from lattice.llm.client import Generation, GenerationRefused, Generator, extract_code
from lattice.llm.prompt import (
    BASELINE_SYSTEM,
    baseline_task_message,
    lattice_system_prompt,
    lattice_task_message,
)
from lattice.llm.repair import Round, SynthesisRefused, synthesize
from lattice.runtime import LatticeRuntimeError
from lattice.runtime.exec import run_source

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
    # input_tokens above is the total billed prompt size (raw + cache read +
    # cache creation); cache_read_tokens is the cached share of it.
    cache_read_tokens: int = 0
    model: str = ""  # comma-separated distinct served models, in call order


def _wrote_exact(workdir: Path, name: str) -> bool:
    """Exact-case check, so scoring doesn't depend on a case-insensitive filesystem."""
    return name in {p.name for p in workdir.iterdir()}


_STALE_OUTPUTS = ("out.parquet", "out.csv", "solution.py")
_DETAIL_LIMIT = 2000


def prepare_workdir(task: Task, root: Path, fixtures: Path) -> Path:
    """Create ``root/<task.id>/`` with copies of the task's input CSVs.

    Outputs left over from a previous run (``out.parquet``, ``out.csv``,
    ``solution.py``) are removed so they cannot be scored as this run's.
    """
    workdir = root / task.id
    workdir.mkdir(parents=True, exist_ok=True)
    for name in _STALE_OUTPUTS:
        stale = workdir / name
        if stale.is_dir():
            shutil.rmtree(stale)
        else:
            stale.unlink(missing_ok=True)
    for name in task.inputs:
        filename = DATASETS[name].filename
        shutil.copyfile(fixtures / filename, workdir / filename)
    return workdir


def _join_models(models: Sequence[str]) -> str:
    seen: list[str] = []
    for model in models:
        if model and model not in seen:
            seen.append(model)
    return ",".join(seen)


def harness_error_outcome(task: Task, arm: Arm, exc: BaseException) -> TaskOutcome:
    """Outcome for a task whose run crashed inside the harness itself.

    Counts as incorrect; ``check_passed`` is None because no verdict exists.
    """
    detail = f"{type(exc).__name__}: {exc}"
    return TaskOutcome(
        task_id=task.id,
        arm=arm,
        tier=task.tier,
        correct=False,
        check_passed=None,
        rounds=0,
        output_tokens=0,
        input_tokens=0,
        latency_s=0.0,
        failure="harness_error",
        detail=detail[:_DETAIL_LIMIT],
    )


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
        input_tokens=generation.usage.total_input_tokens,
        latency_s=generation.latency_s,
        failure=failure,
        detail=detail,
        cache_read_tokens=generation.usage.cache_read_input_tokens,
        model=generation.model,
    )


def run_baseline(
    task: Task,
    generator: Generator,
    workdir: Path,
    fixtures_frames: Mapping[str, pl.DataFrame],
) -> TaskOutcome:
    """Generate one pandas script for ``task``, sandbox it, and score the result."""
    message = baseline_task_message(task)
    try:
        generation = generator.generate(
            BASELINE_SYSTEM, [{"role": "user", "content": message}]
        )
    except GenerationRefused as exc:
        return TaskOutcome(
            task_id=task.id,
            arm="A",
            tier=task.tier,
            correct=False,
            check_passed=None,
            rounds=1,
            output_tokens=exc.usage.output_tokens,
            input_tokens=exc.usage.total_input_tokens,
            latency_s=exc.latency_s,
            failure="refused",
            detail=str(exc),
            cache_read_tokens=exc.usage.cache_read_input_tokens,
            model=exc.model,
        )

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
    if not _wrote_exact(workdir, "out.parquet"):
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


_SCHEMA_CODE_PREFIX = "L4"


def classify_failure(diags: Sequence[Diagnostic]) -> str:
    """Taxonomy label for the first error in ``diags`` (already source-ordered)."""
    errors = [d for d in sorted(diags, key=sort_key) if d.severity is Severity.ERROR]
    if not errors:
        return "other_static"
    code = errors[0].code
    if code == "L201":
        return "shadowing"
    if code in ("L202", "L604"):
        return "sequencing"
    if code in ("L301", "L302"):
        return "chaining"
    if code == "L000":
        return "no_code"
    if code.startswith(_SCHEMA_CODE_PREFIX):
        return "schema"
    return "other_static"


def _lattice_outcome(
    task: Task,
    arm: Literal["B", "C"],
    *,
    correct: bool,
    check_passed: bool | None,
    rounds: int,
    output_tokens: int,
    input_tokens: int,
    latency_s: float,
    failure: str | None,
    detail: str,
    cache_read_tokens: int = 0,
    model: str = "",
) -> TaskOutcome:
    return TaskOutcome(
        task_id=task.id,
        arm=arm,
        tier=task.tier,
        correct=correct,
        check_passed=check_passed,
        rounds=rounds,
        output_tokens=output_tokens,
        input_tokens=input_tokens,
        latency_s=latency_s,
        failure=failure,
        detail=detail,
        cache_read_tokens=cache_read_tokens,
        model=model,
    )


@dataclass(frozen=True)
class _Totals:
    output_tokens: int
    input_tokens: int
    cache_read_tokens: int
    latency_s: float
    models: tuple[str, ...]


def _round_totals(rounds: Sequence[Round]) -> _Totals:
    return _Totals(
        output_tokens=sum(r.generation.usage.output_tokens for r in rounds),
        input_tokens=sum(r.generation.usage.total_input_tokens for r in rounds),
        cache_read_tokens=sum(r.generation.usage.cache_read_input_tokens for r in rounds),
        latency_s=sum(r.generation.latency_s for r in rounds),
        models=tuple(r.generation.model for r in rounds),
    )


def run_lattice(
    task: Task,
    generator: Generator,
    workdir: Path,
    fixtures_frames: Mapping[str, pl.DataFrame],
    *,
    arm: Literal["B", "C"],
    max_repairs: int,
) -> TaskOutcome:
    """Generate, check, and (arm B) repair a Lattice program, then run and score it."""
    if arm == "C":
        max_repairs = 0

    profile = bench_profile()
    message = lattice_task_message(task, profile)
    system = lattice_system_prompt()

    try:
        result = synthesize(message, profile, generator, system=system, max_repairs=max_repairs)
    except GenerationRefused as exc:
        # Keep the accounting of rounds completed before the refusal, plus the
        # refused call itself (which still consumed tokens).
        prior: tuple[Round, ...] = exc.rounds if isinstance(exc, SynthesisRefused) else ()
        totals = _round_totals(prior)
        return TaskOutcome(
            task_id=task.id,
            arm=arm,
            tier=task.tier,
            correct=False,
            check_passed=False,
            rounds=len(prior) + 1,
            output_tokens=totals.output_tokens + exc.usage.output_tokens,
            input_tokens=totals.input_tokens + exc.usage.total_input_tokens,
            latency_s=totals.latency_s + exc.latency_s,
            failure="refused",
            detail=str(exc),
            cache_read_tokens=totals.cache_read_tokens + exc.usage.cache_read_input_tokens,
            model=_join_models([*totals.models, exc.model]),
        )

    rounds = len(result.rounds)
    totals = _round_totals(result.rounds)
    output_tokens = totals.output_tokens
    input_tokens = totals.input_tokens
    latency_s = totals.latency_s
    cache_read_tokens = totals.cache_read_tokens
    model = _join_models(totals.models)

    if not result.ok:
        last_diags = result.rounds[-1].diagnostics if result.rounds else ()
        return _lattice_outcome(
            task,
            arm,
            correct=False,
            check_passed=False,
            rounds=rounds,
            output_tokens=output_tokens,
            input_tokens=input_tokens,
            latency_s=latency_s,
            failure=classify_failure(last_diags),
            detail=render_for_llm(last_diags),
            cache_read_tokens=cache_read_tokens,
            model=model,
        )

    assert result.source is not None

    try:
        _, _run_result = run_source(result.source, profile, workdir)
    except LatticeRuntimeError as exc:
        return _lattice_outcome(
            task,
            arm,
            correct=False,
            check_passed=True,
            rounds=rounds,
            output_tokens=output_tokens,
            input_tokens=input_tokens,
            latency_s=latency_s,
            failure="runtime_error",
            detail=exc.render(),
            cache_read_tokens=cache_read_tokens,
            model=model,
        )

    out_path = workdir / "out.parquet"
    if not _wrote_exact(workdir, "out.parquet"):
        return _lattice_outcome(
            task,
            arm,
            correct=False,
            check_passed=True,
            rounds=rounds,
            output_tokens=output_tokens,
            input_tokens=input_tokens,
            latency_s=latency_s,
            failure="no_output",
            detail="program did not write out.parquet",
            cache_read_tokens=cache_read_tokens,
            model=model,
        )

    try:
        actual = pl.read_parquet(out_path)
    except Exception as exc:
        return _lattice_outcome(
            task,
            arm,
            correct=False,
            check_passed=True,
            rounds=rounds,
            output_tokens=output_tokens,
            input_tokens=input_tokens,
            latency_s=latency_s,
            failure="no_output",
            detail=f"could not read out.parquet: {exc}",
            cache_read_tokens=cache_read_tokens,
            model=model,
        )

    expected = task.reference(fixtures_frames)
    ok, reason = frames_match(actual, expected, ordered=task.ordered)
    if not ok:
        return _lattice_outcome(
            task,
            arm,
            correct=False,
            check_passed=True,
            rounds=rounds,
            output_tokens=output_tokens,
            input_tokens=input_tokens,
            latency_s=latency_s,
            failure="wrong_result",
            detail=reason,
            cache_read_tokens=cache_read_tokens,
            model=model,
        )

    return _lattice_outcome(
        task,
        arm,
        correct=True,
        check_passed=True,
        rounds=rounds,
        output_tokens=output_tokens,
        input_tokens=input_tokens,
        latency_s=latency_s,
        failure=None,
        detail="",
        cache_read_tokens=cache_read_tokens,
        model=model,
    )
