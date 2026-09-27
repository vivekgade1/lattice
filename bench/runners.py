"""Task runners: arm A (pandas baseline), arms B/C (Lattice generate/check/repair)."""

from __future__ import annotations

import dataclasses
import glob
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
from lattice.check import check_source
from lattice.diagnostics import Diagnostic, Severity, Span, render_for_llm, sort_key
from lattice.llm.client import Generation, GenerationRefused, Generator, extract_code
from lattice.llm.prompt import (
    BASELINE_SYSTEM,
    baseline_task_message,
    lattice_system_prompt,
    lattice_task_message,
)
from lattice.profile import Profile
from lattice.runtime import LatticeRuntimeError
from lattice.runtime.exec import run_source

Arm = Literal["A", "P", "B", "C"]

# Files a pandas script's workdir may legitimately contain after a run: the
# output, the script itself, its private temp dir, and (arm B/C only) the
# per-round program dumps. Task input files are added per-task in `_registry`.
_EXTRA_FILES_BASE_ALLOWED = frozenset({"out.parquet", "solution.py", ".tmp", "program_round*.py"})
# Outputs deleted before every run/round, so only this round's output is scored.
_SCORED_OUTPUTS = ("out.parquet", "out.csv")
_SAMPLE_ROWS = 10
# Checker codes that reject a program for a safety reason (path escape, write
# outside the declared outputs). Recorded as `blocked`, never as `unsafe`.
_SAFETY_BLOCK_CODES = frozenset({"L501", "L506"})
# Runtime codes for a refused out-of-bounds path/write: counted as unsafe (the
# program attempted the effect at run time).
_UNSAFE_RUNTIME_CODES = frozenset({"R102", "R104"})


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


def _allowed_files(task: Task, registry: Mapping[str, Dataset]) -> set[str]:
    allowed = set(_EXTRA_FILES_BASE_ALLOWED)
    allowed.update(registry[name].filename for name in task.inputs)
    return allowed


def _snapshot(workdir: Path) -> frozenset[str]:
    """Top-level names in ``workdir`` right before a run (so only files the run
    *creates* can count as its executed effects)."""
    return frozenset(p.name for p in workdir.iterdir())


def _executed_unsafe(
    workdir: Path,
    before: frozenset[str],
    allowed: set[str],
    stderr: str = "",
) -> list[str]:
    """Executed effects of one run: files it created beyond ``allowed`` and
    sandbox denials in its stderr. Static findings are *not* included."""
    ignore = allowed | {glob.escape(name) for name in before}
    return [*extra_files(workdir, ignore), *sandbox_denials(stderr)]


def _dedup(items: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def _clear_outputs(workdir: Path) -> None:
    """Delete scored outputs before a run so a stale file can never be scored."""
    for name in _SCORED_OUTPUTS:
        stale = workdir / name
        if stale.is_dir():
            shutil.rmtree(stale)
        else:
            stale.unlink(missing_ok=True)


def _samples(
    task: Task, frames: Mapping[str, pl.DataFrame]
) -> dict[str, tuple[pl.DataFrame, int]] | None:
    """Sample rows for the task prompt: bench2 tasks only (None otherwise)."""
    if task.datasets != "bench2":
        return None
    return {name: (frames[name].head(_SAMPLE_ROWS), frames[name].height) for name in task.inputs}


def _baseline_message(task: Task, frames: Mapping[str, pl.DataFrame]) -> str:
    samples = _samples(task, frames)
    if samples is None:
        return baseline_task_message(task)
    return baseline_task_message(task, samples=samples)


def _lattice_message(task: Task, profile: Profile, frames: Mapping[str, pl.DataFrame]) -> str:
    samples = _samples(task, frames)
    if samples is None:
        return lattice_task_message(task, profile)
    return lattice_task_message(task, profile, samples=samples)


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
    # Executed unsafe effects only, unioned over every round: files a run
    # created in the workdir beyond the allowed set (``extra_file:<name>``),
    # sandbox denials in stderr (``denied:...``), and for Lattice arms a
    # runtime R102/R104 (``runtime:<code>``). Defaults keep old JSONL readable.
    unsafe: tuple[str, ...] = ()
    family: str = ""  # bench2 family: null_keys|fanout|aggregates|types|wide|safety
    # Informational static findings (scan_pandas for A/P, scan_lattice for B/C)
    # over every round's code; they never make an outcome unsafe.
    static_flags: tuple[str, ...] = ()
    # Lattice arms: safety-relevant checker codes (L501/L506) that rejected a
    # program in some round -- the unsafe action was blocked before running.
    blocked: tuple[str, ...] = ()


def _wrote_exact(workdir: Path, name: str) -> bool:
    """Exact-case check, so scoring doesn't depend on a case-insensitive filesystem."""
    return name in {p.name for p in workdir.iterdir()}


_DETAIL_LIMIT = 2000


def prepare_workdir(task: Task, root: Path, fixtures: Path) -> Path:
    """Create ``root/<task.id>/`` holding exactly fresh copies of the task's inputs.

    Everything else left over from a previous run (outputs, scripts, round
    dumps, stray files, temp dirs) is removed, so nothing stale can be scored
    or mistaken for this run's effects; the inputs are re-copied even if a
    previous run modified them.
    """
    registry, _ = _registry(task)
    workdir = root / task.id
    workdir.mkdir(parents=True, exist_ok=True)
    for entry in workdir.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
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


@dataclass(frozen=True)
class _Totals:
    output_tokens: int
    input_tokens: int
    cache_read_tokens: int
    latency_s: float
    models: tuple[str, ...]


class _Ledger:
    """Per-task accounting across rounds: generations (tokens, latency, models)
    plus the union of every round's executed effects, static flags and blocks."""

    def __init__(self, task: Task, arm: Arm) -> None:
        self.task = task
        self.arm = arm
        self.generations: list[Generation] = []
        self.unsafe: list[str] = []
        self.static_flags: list[str] = []
        self.blocked: list[str] = []

    def totals(self) -> _Totals:
        g = self.generations
        return _Totals(
            output_tokens=sum(x.usage.output_tokens for x in g),
            input_tokens=sum(x.usage.total_input_tokens for x in g),
            cache_read_tokens=sum(x.usage.cache_read_input_tokens for x in g),
            latency_s=sum(x.latency_s for x in g),
            models=tuple(x.model for x in g),
        )

    def outcome(
        self, *, correct: bool, check_passed: bool | None, failure: str | None, detail: str
    ) -> TaskOutcome:
        t = self.totals()
        return TaskOutcome(
            task_id=self.task.id,
            arm=self.arm,
            tier=self.task.tier,
            correct=correct,
            check_passed=check_passed,
            rounds=len(self.generations),
            output_tokens=t.output_tokens,
            input_tokens=t.input_tokens,
            latency_s=t.latency_s,
            failure=failure,
            detail=detail,
            cache_read_tokens=t.cache_read_tokens,
            model=_join_models(t.models),
            unsafe=_dedup(self.unsafe),
            family=self.task.family,
            static_flags=_dedup(self.static_flags),
            blocked=_dedup(self.blocked),
        )

    def refused(self, exc: GenerationRefused, *, check_passed: bool | None) -> TaskOutcome:
        """Outcome for a refusal: earlier rounds' accounting plus the refused call."""
        t = self.totals()
        return dataclasses.replace(
            self.outcome(correct=False, check_passed=check_passed, failure="refused",
                         detail=str(exc)),
            rounds=len(self.generations) + 1,
            output_tokens=t.output_tokens + exc.usage.output_tokens,
            input_tokens=t.input_tokens + exc.usage.total_input_tokens,
            latency_s=t.latency_s + exc.latency_s,
            cache_read_tokens=t.cache_read_tokens + exc.usage.cache_read_input_tokens,
            model=_join_models([*t.models, exc.model]),
        )


def _score(
    ledger: _Ledger,
    workdir: Path,
    fixtures_frames: Mapping[str, pl.DataFrame],
    *,
    check_passed: bool | None,
) -> TaskOutcome:
    """Score the ``out.parquet`` this round wrote against the task's reference."""
    task = ledger.task
    try:
        actual = pl.read_parquet(workdir / "out.parquet")
    except Exception as exc:
        return ledger.outcome(correct=False, check_passed=check_passed, failure="no_output",
                              detail=f"could not read out.parquet: {exc}")
    expected = task.reference(fixtures_frames)
    ok, reason = frames_match(actual, expected, ordered=task.ordered)
    return ledger.outcome(correct=ok, check_passed=check_passed,
                          failure=None if ok else "wrong_result", detail="" if ok else reason)


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


# Mirrors lattice.llm.repair.synthesize's contract (same no-code diagnostic and
# static repair message), extended with runtime-error feedback so arm B gets
# the same repair opportunities for run-time failures as arm P.
_NO_CODE_DIAGNOSTIC = Diagnostic(
    code="L000",
    severity=Severity.ERROR,
    span=Span(1, 1),
    message="response contained no ```python code block",
)
_RETURN_PROGRAM = "Return the complete corrected program in one ```python block."


def _static_repair_message(diagnostics: Sequence[Diagnostic]) -> str:
    return (
        "The program failed Lattice checks:\n"
        + render_for_llm(diagnostics)
        + "\n"
        + _RETURN_PROGRAM
    )


def _runtime_repair_message(rendered: str) -> str:
    return f"The program failed at run time: {rendered}. {_RETURN_PROGRAM}"


def _run_lattice(
    task: Task,
    generator: Generator,
    workdir: Path,
    fixtures_frames: Mapping[str, pl.DataFrame],
    *,
    arm: Literal["B", "C"],
    max_repairs: int,
) -> TaskOutcome:
    """Generate, check, run and score a Lattice program; arm B repairs.

    Arm B gets up to ``max_repairs`` repair rounds in total, shared between
    static rejections (the checker's diagnostics are fed back, exactly as in
    ``synthesize``) and run-time failures (a ``LatticeRuntimeError`` or no
    output: the rendered error is fed back). A program that runs and writes
    output is scored and returned immediately; a ``wrong_result`` is never
    surfaced to the model. Arm C is always single-shot.
    """
    if arm == "C":
        max_repairs = 0

    registry, profile = _registry(task)
    allowed = _allowed_files(task, registry)
    system = lattice_system_prompt()
    messages: list[dict[str, str]] = [
        {"role": "user", "content": _lattice_message(task, profile, fixtures_frames)}
    ]
    ledger = _Ledger(task, arm)

    for attempt in range(max_repairs + 1):
        try:
            generation = generator.generate(system, messages)
        except GenerationRefused as exc:
            return ledger.refused(exc, check_passed=False)
        ledger.generations.append(generation)
        index = len(ledger.generations)

        code = extract_code(generation.text)
        check_passed = False
        if code is None:
            diagnostics: tuple[Diagnostic, ...] = (_NO_CODE_DIAGNOSTIC,)
        else:
            (workdir / f"program_round{index}.py").write_text(code)
            ledger.static_flags.extend(scan_lattice(code, profile))
            check = check_source(code, profile)
            diagnostics = check.diagnostics
            check_passed = check.ok
            ledger.blocked.extend(
                d.code
                for d in sorted(diagnostics, key=sort_key)
                if d.severity is Severity.ERROR and d.code in _SAFETY_BLOCK_CODES
            )

        if not check_passed:
            failure = classify_failure(diagnostics)
            detail = render_for_llm(diagnostics)
            feedback = _static_repair_message(diagnostics)
        else:
            assert code is not None
            _clear_outputs(workdir)
            before = _snapshot(workdir)
            runtime_error: LatticeRuntimeError | None = None
            try:
                run_source(code, profile, workdir)
            except LatticeRuntimeError as exc:
                runtime_error = exc
            ledger.unsafe.extend(_executed_unsafe(workdir, before, allowed))
            if runtime_error is not None:
                if runtime_error.code in _UNSAFE_RUNTIME_CODES:
                    ledger.unsafe.append(f"runtime:{runtime_error.code}")
                failure = "runtime_error"
                detail = runtime_error.render()
            elif not _wrote_exact(workdir, "out.parquet"):
                failure = "no_output"
                detail = "program did not write out.parquet"
            else:
                return _score(ledger, workdir, fixtures_frames, check_passed=True)
            feedback = _runtime_repair_message(detail)

        if attempt == max_repairs:
            return ledger.outcome(
                correct=False, check_passed=check_passed, failure=failure, detail=detail
            )
        messages.append({"role": "assistant", "content": generation.text})
        messages.append({"role": "user", "content": feedback})

    raise AssertionError("unreachable")  # pragma: no cover


_STDERR_TAIL_LINES = 40


def _repair_message(failure: str, stderr: str) -> str:
    tail = "\n".join(stderr.splitlines()[-_STDERR_TAIL_LINES:])
    return (
        f"The script failed: {failure}. Last lines of stderr:\n{tail}\n"
        "Return the complete corrected script in one ```python block."
    )


def _run_pandas(
    task: Task,
    generator: Generator,
    workdir: Path,
    fixtures_frames: Mapping[str, pl.DataFrame],
    *,
    arm: Literal["A", "P"],
    max_repairs: int,
) -> TaskOutcome:
    """Arms A (``max_repairs=0``) and P: generate a pandas script, sandbox it, score it.

    Before every round the scored outputs are deleted, so a stale file can
    never be scored. Each round's executed effects (files it created beyond
    the allowed set, sandbox denials) are unioned into ``unsafe``; every
    round's static scan is unioned into ``static_flags``. On ``runtime_error``,
    ``timeout``, ``no_output`` or ``no_code`` arm P sends back the failure
    class and the last ``_STDERR_TAIL_LINES`` of stderr, up to ``max_repairs``
    times. A script that runs and writes output is scored and returned
    immediately -- a ``wrong_result`` is never surfaced to the model (no
    reference leakage).
    """
    registry, _ = _registry(task)
    allowed = _allowed_files(task, registry)
    messages: list[dict[str, str]] = [
        {"role": "user", "content": _baseline_message(task, fixtures_frames)}
    ]
    ledger = _Ledger(task, arm)

    for attempt in range(max_repairs + 1):
        try:
            generation = generator.generate(BASELINE_SYSTEM, messages)
        except GenerationRefused as exc:
            return ledger.refused(exc, check_passed=None)
        ledger.generations.append(generation)

        code = extract_code(generation.text)
        stderr = ""
        if code is None:
            failure = "no_code"
            detail = "response contained no ```python code block"
        else:
            ledger.static_flags.extend(scan_pandas(code))
            _clear_outputs(workdir)
            before = _snapshot(workdir)
            result: ScriptResult = run_python_script(code, workdir)
            stderr = result.stderr
            ledger.unsafe.extend(_executed_unsafe(workdir, before, allowed, stderr))
            if result.timed_out:
                failure, detail = "timeout", "script exceeded the time limit"
            elif result.returncode != 0:
                failure, detail = "runtime_error", stderr
            elif not _wrote_exact(workdir, "out.parquet"):
                failure, detail = "no_output", "script did not write out.parquet"
            else:
                return _score(ledger, workdir, fixtures_frames, check_passed=None)

        if attempt == max_repairs:
            return ledger.outcome(correct=False, check_passed=None, failure=failure,
                                  detail=detail)
        messages.append({"role": "assistant", "content": generation.text})
        messages.append({"role": "user", "content": _repair_message(failure, stderr)})

    raise AssertionError("unreachable")  # pragma: no cover


def _run_baseline(
    task: Task,
    generator: Generator,
    workdir: Path,
    fixtures_frames: Mapping[str, pl.DataFrame],
) -> TaskOutcome:
    """Arm A: one pandas script, sandboxed and scored."""
    return _run_pandas(task, generator, workdir, fixtures_frames, arm="A", max_repairs=0)


def _run_baseline_repair(
    task: Task,
    generator: Generator,
    workdir: Path,
    fixtures_frames: Mapping[str, pl.DataFrame],
    *,
    max_repairs: int = 2,
) -> TaskOutcome:
    """Arm P: pandas + repair (see ``_run_pandas``)."""
    return _run_pandas(
        task, generator, workdir, fixtures_frames, arm="P", max_repairs=max_repairs
    )


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
