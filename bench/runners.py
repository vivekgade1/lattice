"""Task runners: arm A (pandas baseline), arms B/C (Lattice generate/check/repair)."""

from __future__ import annotations

import dataclasses
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import polars as pl

from bench.compare import frames_match
from bench.datasets import DATASETS, Dataset, bench_profile
from bench.safety import extra_files, sandbox_denials, scan_lattice, scan_pandas
from bench.sandbox import ScriptResult, run_python_script
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
from lattice.profile import Profile
from lattice.runtime import LatticeRuntimeError
from lattice.runtime.exec import run_source

Arm = Literal["A", "P", "B", "C"]

# Files a pandas script's workdir may legitimately contain after a run: the
# output, the script itself, its private temp dir, and (arm B/C only) the
# per-round program dumps. Task input files are added per-task in `_registry`.
_EXTRA_FILES_BASE_ALLOWED = frozenset({"out.parquet", "solution.py", ".tmp", "program_round*.py"})


def _registry(task: Task) -> tuple[Mapping[str, Dataset], Profile]:
    """The dataset registry and profile for ``task.datasets``.

    ``bench.bench2_datasets`` is imported lazily, only for a task that
    actually names the "bench2" registry, so nothing outside a bench2 run
    ever touches that module.
    """
    if task.datasets == "bench2":
        import bench.bench2_datasets as bench2

        return bench2.DATASETS2, bench2.bench2_profile()
    return DATASETS, bench_profile()


def _pandas_unsafe(
    code: str,
    workdir: Path,
    stderr: str,
    task: Task,
    registry: Mapping[str, Dataset],
) -> tuple[str, ...]:
    """Safety findings for a pandas script (arms A and P): static scan of the
    code, files left behind beyond the allowed set, and sandbox denials."""
    allowed = set(_EXTRA_FILES_BASE_ALLOWED)
    allowed.update(registry[name].filename for name in task.inputs)
    found = [*scan_pandas(code), *extra_files(workdir, allowed), *sandbox_denials(stderr)]
    return tuple(dict.fromkeys(found))


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
    refused_attempts: int = 0  # refused calls that were retried and then answered
    # Safety findings (bench.safety): scan_pandas+extra_files+sandbox_denials for
    # arms A/P; scan_lattice on the final program for arms B/C. Defaults keep
    # old JSONL readable.
    unsafe: tuple[str, ...] = ()
    family: str = ""  # bench2 family: null_keys|fanout|aggregates|types|wide|safety


def _wrote_exact(workdir: Path, name: str) -> bool:
    """Exact-case check, so scoring doesn't depend on a case-insensitive filesystem."""
    return name in {p.name for p in workdir.iterdir()}


_STALE_OUTPUTS = (
    "out.parquet",
    "out.csv",
    "solution.py",
    *(f"program_round{i}.py" for i in range(1, 11)),
)
_DETAIL_LIMIT = 2000


def prepare_workdir(task: Task, root: Path, fixtures: Path) -> Path:
    """Create ``root/<task.id>/`` with copies of the task's input CSVs.

    Outputs left over from a previous run (``out.parquet``, ``out.csv``,
    ``solution.py``) are removed so they cannot be scored as this run's.
    """
    registry, _ = _registry(task)
    workdir = root / task.id
    workdir.mkdir(parents=True, exist_ok=True)
    for name in _STALE_OUTPUTS:
        stale = workdir / name
        if stale.is_dir():
            shutil.rmtree(stale)
        else:
            stale.unlink(missing_ok=True)
    for name in task.inputs:
        filename = registry[name].filename
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
        family=task.family,
    )


def _baseline_outcome(
    task: Task,
    generation: Generation,
    *,
    correct: bool,
    failure: str | None,
    detail: str,
    unsafe: tuple[str, ...] = (),
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
        unsafe=unsafe,
        family=task.family,
    )


def _run_baseline(
    task: Task,
    generator: Generator,
    workdir: Path,
    fixtures_frames: Mapping[str, pl.DataFrame],
) -> TaskOutcome:
    """Generate one pandas script for ``task``, sandbox it, and score the result."""
    registry, _ = _registry(task)
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
            family=task.family,
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
    unsafe = _pandas_unsafe(code, workdir, result.stderr, task, registry)

    if result.timed_out:
        return _baseline_outcome(
            task,
            generation,
            correct=False,
            failure="timeout",
            detail="script exceeded the time limit",
            unsafe=unsafe,
        )

    if result.returncode != 0:
        return _baseline_outcome(
            task,
            generation,
            correct=False,
            failure="runtime_error",
            detail=result.stderr,
            unsafe=unsafe,
        )

    out_path = workdir / "out.parquet"
    if not _wrote_exact(workdir, "out.parquet"):
        return _baseline_outcome(
            task,
            generation,
            correct=False,
            failure="no_output",
            detail="script did not write out.parquet",
            unsafe=unsafe,
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
            unsafe=unsafe,
        )

    expected = task.reference(fixtures_frames)
    ok, reason = frames_match(actual, expected, ordered=task.ordered)
    if not ok:
        return _baseline_outcome(
            task, generation, correct=False, failure="wrong_result", detail=reason, unsafe=unsafe
        )

    return _baseline_outcome(
        task, generation, correct=True, failure=None, detail="", unsafe=unsafe
    )


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
    unsafe: tuple[str, ...] = (),
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
        unsafe=unsafe,
        family=task.family,
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


def _run_lattice(
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

    _, profile = _registry(task)
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
            family=task.family,
        )

    for r in result.rounds:  # keep every attempt for later inspection
        if r.source is not None:
            (workdir / f"program_round{r.index}.py").write_text(r.source)

    rounds = len(result.rounds)
    totals = _round_totals(result.rounds)
    output_tokens = totals.output_tokens
    input_tokens = totals.input_tokens
    latency_s = totals.latency_s
    cache_read_tokens = totals.cache_read_tokens
    model = _join_models(totals.models)

    # The final program to scan for safety: the passing one, else the last
    # attempted (and rejected) source, if any attempt produced code at all.
    final_source = result.source
    if final_source is None and result.rounds:
        final_source = result.rounds[-1].source
    unsafe = tuple(scan_lattice(final_source, profile)) if final_source is not None else ()

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
            unsafe=unsafe,
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
            unsafe=unsafe,
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
            unsafe=unsafe,
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
            unsafe=unsafe,
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
            unsafe=unsafe,
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
        unsafe=unsafe,
    )


_STDERR_TAIL_LINES = 40


def _repair_message(failure: str, stderr: str) -> str:
    tail = "\n".join(stderr.splitlines()[-_STDERR_TAIL_LINES:])
    return (
        f"The script failed: {failure}. Last lines of stderr:\n{tail}\n"
        "Return the complete corrected script in one ```python block."
    )


def _run_baseline_repair(
    task: Task,
    generator: Generator,
    workdir: Path,
    fixtures_frames: Mapping[str, pl.DataFrame],
    *,
    max_repairs: int = 2,
) -> TaskOutcome:
    """Arm P: pandas + repair.

    Round 1 is identical to arm A. On ``runtime_error``, ``timeout``,
    ``no_output``, or ``no_code`` the harness sends back the failure class and
    the last ``_STDERR_TAIL_LINES`` of stderr and asks for a corrected script,
    up to ``max_repairs`` times. A script that runs to completion and writes
    output is scored and returned immediately whether the result is correct
    or wrong -- a ``wrong_result`` is never surfaced to the model (no
    reference leakage; Review Focus 5).
    """
    registry, _ = _registry(task)
    messages: list[dict[str, str]] = [
        {"role": "user", "content": baseline_task_message(task)}
    ]
    generations: list[Generation] = []

    def totals() -> _Totals:
        return _Totals(
            output_tokens=sum(g.usage.output_tokens for g in generations),
            input_tokens=sum(g.usage.total_input_tokens for g in generations),
            cache_read_tokens=sum(g.usage.cache_read_input_tokens for g in generations),
            latency_s=sum(g.latency_s for g in generations),
            models=tuple(g.model for g in generations),
        )

    def outcome(
        *, correct: bool, failure: str | None, detail: str, unsafe: tuple[str, ...] = ()
    ) -> TaskOutcome:
        t = totals()
        return TaskOutcome(
            task_id=task.id,
            arm="P",
            tier=task.tier,
            correct=correct,
            check_passed=None,
            rounds=len(generations),
            output_tokens=t.output_tokens,
            input_tokens=t.input_tokens,
            latency_s=t.latency_s,
            failure=failure,
            detail=detail,
            cache_read_tokens=t.cache_read_tokens,
            model=_join_models(t.models),
            unsafe=unsafe,
            family=task.family,
        )

    for attempt in range(max_repairs + 1):
        try:
            generation = generator.generate(BASELINE_SYSTEM, messages)
        except GenerationRefused as exc:
            t = totals()
            return TaskOutcome(
                task_id=task.id,
                arm="P",
                tier=task.tier,
                correct=False,
                check_passed=None,
                rounds=len(generations) + 1,
                output_tokens=t.output_tokens + exc.usage.output_tokens,
                input_tokens=t.input_tokens + exc.usage.total_input_tokens,
                latency_s=t.latency_s + exc.latency_s,
                failure="refused",
                detail=str(exc),
                cache_read_tokens=t.cache_read_tokens + exc.usage.cache_read_input_tokens,
                model=_join_models([*t.models, exc.model]),
                family=task.family,
            )
        generations.append(generation)

        code = extract_code(generation.text)
        result: ScriptResult | None
        if code is None:
            failure: str | None = "no_code"
            stderr = ""
            result = None
        else:
            result = run_python_script(code, workdir)
            if result.timed_out:
                failure = "timeout"
            elif result.returncode != 0:
                failure = "runtime_error"
            elif not _wrote_exact(workdir, "out.parquet"):
                failure = "no_output"
            else:
                failure = None
            stderr = result.stderr

        if failure is None:
            assert code is not None and result is not None
            unsafe = _pandas_unsafe(code, workdir, stderr, task, registry)
            out_path = workdir / "out.parquet"
            try:
                actual = pl.read_parquet(out_path)
            except Exception as exc:
                return outcome(
                    correct=False,
                    failure="no_output",
                    detail=f"could not read out.parquet: {exc}",
                    unsafe=unsafe,
                )
            expected = task.reference(fixtures_frames)
            ok, reason = frames_match(actual, expected, ordered=task.ordered)
            return outcome(
                correct=ok,
                failure=None if ok else "wrong_result",
                detail="" if ok else reason,
                unsafe=unsafe,
            )

        if attempt == max_repairs:
            detail = (
                stderr if code is not None else "response contained no ```python code block"
            )
            unsafe = _pandas_unsafe(code, workdir, stderr, task, registry) if code else ()
            return outcome(correct=False, failure=failure, detail=detail, unsafe=unsafe)

        messages.append({"role": "assistant", "content": generation.text})
        messages.append({"role": "user", "content": _repair_message(failure, stderr)})

    raise AssertionError("unreachable")  # pragma: no cover


class _RefusalCounter:
    """Wraps a Generator to total the refused-then-retried attempts behind each answer."""

    def __init__(self, inner: Generator) -> None:
        self.inner = inner
        self.count = 0

    def generate(self, system: str, messages: Sequence[Mapping[str, str]]) -> Generation:
        generation = self.inner.generate(system, messages)
        self.count += generation.refusals
        return generation


def run_baseline(task: Task, generator: Generator, *args: Any, **kwargs: Any) -> TaskOutcome:
    counter = _RefusalCounter(generator)
    outcome = _run_baseline(task, counter, *args, **kwargs)
    return dataclasses.replace(outcome, refused_attempts=counter.count)


def run_lattice(task: Task, generator: Generator, *args: Any, **kwargs: Any) -> TaskOutcome:
    counter = _RefusalCounter(generator)
    outcome = _run_lattice(task, counter, *args, **kwargs)
    return dataclasses.replace(outcome, refused_attempts=counter.count)


def run_baseline_repair(task: Task, generator: Generator, *args: Any, **kwargs: Any) -> TaskOutcome:
    counter = _RefusalCounter(generator)
    outcome = _run_baseline_repair(task, counter, *args, **kwargs)
    return dataclasses.replace(outcome, refused_attempts=counter.count)
