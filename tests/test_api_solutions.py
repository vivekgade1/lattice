"""Hand-written solutions for every API task (tests/fixtures/api/<id>/).

- ``fastapi_correct.py`` passes every case (classified "correct");
- ``fastapi_naive.py`` (trap tasks) is the natural-but-flawed script: it passes every
  happy case and fails at least one trap case (classified "silent", or "loud" when the
  slip is a 500 -- ``LOUD_NAIVE``) -- proving the trap is real and fair;
- ``lattice.py`` checks with zero errors under ``api_profile()`` and, once the Lattice
  runtime (``lattice.api.runtime``) exists, passes every case too.

Formerly known language gaps are closed (language spec §5.11 item 20); the regression
tests at the end prove that the programs they needed now check, and that the soundness
rules that replaced them still reject the unsafe variants.

The FastAPI runs are done once per session, concurrently, and cached.
"""

from __future__ import annotations

import importlib
import shutil
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from bench.api_datasets import api_profile, build_db
from bench.api_runner import CaseResults, classify, run_cases_fastapi, run_cases_lattice
from bench.api_tasks import API_TASKS, TRAP_FAMILIES
from lattice.check import check_source
from lattice.diagnostics import Severity

SOLUTIONS = Path(__file__).parent / "fixtures" / "api"
KIT_PATH = Path(__file__).resolve().parents[1] / "bench" / "api_kit" / "platform_kit.py"
TASKS = {t.id: t for t in API_TASKS}
ALL_IDS = [t.id for t in API_TASKS]
TRAP_IDS = [t.id for t in API_TASKS if t.family in TRAP_FAMILIES]


@pytest.fixture(scope="session")
def pristine_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("api_db") / "pristine.db"
    build_db(path)
    return path


def _copier(pristine: Path):
    def build(target: Path) -> None:
        shutil.copyfile(pristine, target)

    return build


@pytest.fixture(scope="session")
def fastapi_runs(
    pristine_db: Path, tmp_path_factory: pytest.TempPathFactory
) -> Mapping[tuple[str, str], CaseResults]:
    """(task id, "fastapi_correct" | "fastapi_naive") -> case results, run concurrently."""
    root = tmp_path_factory.mktemp("api_runs")
    jobs = [
        (task_id, kind)
        for task_id in ALL_IDS
        for kind in ("fastapi_correct", "fastapi_naive")
        if (SOLUTIONS / task_id / f"{kind}.py").is_file()
    ]

    def run(job: tuple[str, str]) -> CaseResults:
        task_id, kind = job
        return run_cases_fastapi(
            (SOLUTIONS / task_id / f"{kind}.py").read_text(),
            root / f"{task_id}_{kind}",
            TASKS[task_id],
            db_builder=_copier(pristine_db),
            kit_path=KIT_PATH,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(jobs, pool.map(run, jobs), strict=True))


def _describe(results: CaseResults) -> str:
    if not results.started:
        return "did not start: " + results.start_error
    return "\n".join(
        f"{c.kind} {c.name}: {'ok' if c.passed else c.reason} (status {c.status}, "
        f"{c.queries} queries)"
        for c in results.cases
    )


def test_every_task_has_its_solutions():
    for task in API_TASKS:
        folder = SOLUTIONS / task.id
        assert (folder / "lattice.py").is_file(), task.id
        assert (folder / "fastapi_correct.py").is_file(), task.id
        assert (folder / "fastapi_naive.py").is_file() == (task.id in TRAP_IDS), task.id
    assert sorted(p.name for p in SOLUTIONS.iterdir() if p.is_dir()) == sorted(ALL_IDS)


@pytest.mark.parametrize("task_id", ALL_IDS)
def test_fastapi_correct_passes_every_case(task_id: str, fastapi_runs):
    results = fastapi_runs[(task_id, "fastapi_correct")]
    assert classify(results) == "correct", _describe(results)
    assert len(results.cases) == len(TASKS[task_id].cases)


# Naive scripts whose slip is an unhandled exception on a trap case (a missing row
# -> 500): a server error is loud (spec §5.4: silent = a wrong non-5xx answer).
LOUD_NAIVE = frozenset({"e01", "e05"})


@pytest.mark.parametrize("task_id", TRAP_IDS)
def test_fastapi_naive_fails_a_trap_case(task_id: str, fastapi_runs):
    results = fastapi_runs[(task_id, "fastapi_naive")]
    assert results.started, _describe(results)
    assert all(c.passed for c in results.cases if c.kind == "happy"), _describe(results)
    failed = [c for c in results.cases if not c.passed]
    assert failed and all(c.kind == "trap" for c in failed), _describe(results)
    if task_id in LOUD_NAIVE:
        assert classify(results) == "loud", _describe(results)
        assert any(c.status >= 500 for c in failed), _describe(results)
    else:
        assert classify(results) == "silent", _describe(results)


@pytest.mark.parametrize("task_id", ALL_IDS)
def test_lattice_solution_checks_clean(task_id: str):
    source = (SOLUTIONS / task_id / "lattice.py").read_text()
    result = check_source(source, api_profile())
    errors = [d for d in result.diagnostics if d.severity is Severity.ERROR]
    assert errors == [], errors
    assert result.ok


def _errors(source: str) -> list[str]:
    result = check_source(source, api_profile())
    return [d.code for d in result.diagnostics if d.severity is Severity.ERROR]


def _solution(task_id: str) -> str:
    return (SOLUTIONS / task_id / "lattice.py").read_text()


@pytest.mark.parametrize("task_id", ["o07", "v02"])
def test_gap_closed_zip_parameter(task_id: str):
    source = _solution(task_id)
    assert "zip: Str(" in source  # the body field's wire name, as the spec requires
    assert _errors(source) == []


def test_gap_closed_public_review_aggregates():
    source = _solution("f05")
    assert "auth=Public" in source and "fetch(Reviews)" in source
    assert _errors(source) == []
    # an aggregate that picks a customer's id keeps the rows per-owner, even once dropped
    exposing = source.replace(
        "review_count=agg.count(),", 'review_count=agg.count(), who=agg.max(col("customer_id")),'
    )
    assert _errors(exposing) == ["L802"]


@pytest.mark.parametrize("task_id", ["f01", "f07", "o05", "o08"])
def test_gap_closed_items_need_owned(task_id: str):
    source = _solution(task_id)
    assert "owned(items, by=user)" in source
    assert _errors(source) == []
    unchecked = source.replace("my_items = owned(items, by=user)\n    ", "").replace(
        "(my_items,", "(items,"
    )
    assert _errors(unchecked) == ["L802"]


def _lattice_runtime():
    """The runtime module, or None while it is not built yet."""
    try:
        runtime = importlib.import_module("lattice.api.runtime")
    except ModuleNotFoundError as exc:
        if exc.name in ("lattice.api.runtime", "lattice.api"):
            return None
        raise
    return runtime if hasattr(runtime, "serve_source") else None


@pytest.mark.parametrize("task_id", ALL_IDS)
def test_lattice_solution_passes_every_case(task_id: str, pristine_db: Path, tmp_path: Path):
    if _lattice_runtime() is None:
        pytest.skip("lattice.api.runtime (serve_source) is not built yet")
    results = run_cases_lattice(
        (SOLUTIONS / task_id / "lattice.py").read_text(),
        tmp_path,
        TASKS[task_id],
        db_builder=_copier(pristine_db),
        profile=api_profile(),
    )
    assert classify(results) == "correct", _describe(results)


# ------------------------------------------------ implicit-requirements condition --
# (spec §5.6) The same solutions under each task's implicit view: the policy sentences
# are gone from the spec, the hidden cases still expect the policy (403 or 404 for
# another customer's row). The reference solutions stay correct; the naive ownership
# scripts are still caught.

IMPLICIT_IDS = [t.id for t in API_TASKS if t.implicit_spec is not None]
IMPLICIT_OWNERSHIP_IDS = [t for t in IMPLICIT_IDS if TASKS[t].family == "ownership"]


@pytest.fixture(scope="session")
def fastapi_implicit_runs(
    pristine_db: Path, tmp_path_factory: pytest.TempPathFactory
) -> Mapping[tuple[str, str], CaseResults]:
    root = tmp_path_factory.mktemp("api_implicit_runs")
    jobs = [(task_id, "fastapi_correct") for task_id in IMPLICIT_IDS]
    jobs += [(task_id, "fastapi_naive") for task_id in IMPLICIT_OWNERSHIP_IDS]

    def run(job: tuple[str, str]) -> CaseResults:
        task_id, kind = job
        return run_cases_fastapi(
            (SOLUTIONS / task_id / f"{kind}.py").read_text(),
            root / f"{task_id}_{kind}",
            TASKS[task_id].implicit_view(),
            db_builder=_copier(pristine_db),
            kit_path=KIT_PATH,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(jobs, pool.map(run, jobs), strict=True))


def test_implicit_condition_covers_ownership_and_efficiency():
    assert len(IMPLICIT_IDS) == 16 and len(IMPLICIT_OWNERSHIP_IDS) == 8


@pytest.mark.parametrize("task_id", IMPLICIT_IDS)
def test_implicit_fastapi_correct_passes_every_case(task_id: str, fastapi_implicit_runs):
    results = fastapi_implicit_runs[(task_id, "fastapi_correct")]
    assert classify(results) == "correct", _describe(results)
    assert len(results.cases) == len(TASKS[task_id].cases)


@pytest.mark.parametrize("task_id", IMPLICIT_OWNERSHIP_IDS)
def test_implicit_fastapi_naive_still_fails_an_ownership_trap(task_id: str, fastapi_implicit_runs):
    results = fastapi_implicit_runs[(task_id, "fastapi_naive")]
    assert results.started, _describe(results)
    assert all(c.passed for c in results.cases if c.kind == "happy"), _describe(results)
    failed = [c for c in results.cases if not c.passed]
    assert failed and all(c.kind == "trap" for c in failed), _describe(results)
    assert classify(results) == "silent", _describe(results)


@pytest.mark.parametrize("task_id", IMPLICIT_IDS)
def test_implicit_lattice_solution_passes_every_case(
    task_id: str, pristine_db: Path, tmp_path: Path
):
    if _lattice_runtime() is None:
        pytest.skip("lattice.api.runtime (serve_source) is not built yet")
    results = run_cases_lattice(
        (SOLUTIONS / task_id / "lattice.py").read_text(),
        tmp_path,
        TASKS[task_id].implicit_view(),
        db_builder=_copier(pristine_db),
        profile=api_profile(),
    )
    assert classify(results) == "correct", _describe(results)
