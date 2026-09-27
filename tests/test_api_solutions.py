"""Hand-written solutions for every API task (tests/fixtures/api/<id>/).

- ``fastapi_correct.py`` passes every case (classified "correct");
- ``fastapi_naive.py`` (trap tasks) is the natural-but-flawed script: it passes every
  happy case and fails at least one trap case (classified "silent") -- proving the trap
  is real and fair;
- ``lattice.py`` checks with zero errors under ``api_profile()`` and, once the Lattice
  runtime (``lattice.api.runtime``) exists, passes every case too.

Known language gaps (``LATTICE_GAPS``): for these tasks no Lattice program can satisfy
the (fair) spec; ``lattice.py`` is the closest program that checks, its runtime test is a
strict xfail, and ``test_known_language_gaps_still_present`` fails once the gap is closed
(then write the real solution and drop the entry).

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

# task id -> (why no Lattice program can meet the spec, rule code the natural program hits)
LATTICE_GAPS: Mapping[str, tuple[str, str]] = {
    "o07": (
        "the JSON body field `zip` must be an endpoint parameter named zip, "
        "which is rejected as shadowing a Python builtin",
        "L205",
    ),
    "v02": (
        "the JSON body field `zip` must be an endpoint parameter named zip, "
        "which is rejected as shadowing a Python builtin",
        "L205",
    ),
    "f05": (
        "a Public endpoint cannot return anything derived from Reviews (an owned table), "
        "not even per-product aggregates",
        "L802",
    ),
}


def _natural_program(task_id: str) -> str:
    """The program the spec actually calls for, which the checker rejects."""
    source = (SOLUTIONS / task_id / "lattice.py").read_text()
    if task_id in ("o07", "v02"):
        return source.replace("zip_code", "zip")
    if task_id == "f05":
        return source.replace("auth=Admin", "auth=Public").replace(
            "    user: CurrentUser, limit", "    limit"
        )
    raise KeyError(task_id)


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


@pytest.mark.parametrize("task_id", TRAP_IDS)
def test_fastapi_naive_is_silently_wrong(task_id: str, fastapi_runs):
    results = fastapi_runs[(task_id, "fastapi_naive")]
    assert classify(results) == "silent", _describe(results)


@pytest.mark.parametrize("task_id", ALL_IDS)
def test_lattice_solution_checks_clean(task_id: str):
    source = (SOLUTIONS / task_id / "lattice.py").read_text()
    result = check_source(source, api_profile())
    errors = [d for d in result.diagnostics if d.severity is Severity.ERROR]
    assert errors == [], errors
    assert result.ok


@pytest.mark.parametrize("task_id", sorted(LATTICE_GAPS))
def test_known_language_gaps_still_present(task_id: str):
    reason, code = LATTICE_GAPS[task_id]
    result = check_source(_natural_program(task_id), api_profile())
    codes = [d.code for d in result.diagnostics if d.severity is Severity.ERROR]
    assert code in codes, f"gap closed? ({reason}); now: {codes}"


def _lattice_runtime():
    """The runtime module, or None while it is not built yet."""
    try:
        runtime = importlib.import_module("lattice.api.runtime")
    except ModuleNotFoundError as exc:
        if exc.name in ("lattice.api.runtime", "lattice.api"):
            return None
        raise
    return runtime if hasattr(runtime, "serve_source") else None


def _lattice_params() -> list:
    params = []
    for task_id in ALL_IDS:
        marks = []
        if task_id in LATTICE_GAPS:
            reason = "language gap: " + LATTICE_GAPS[task_id][0]
            marks.append(pytest.mark.xfail(reason=reason, strict=True))
        params.append(pytest.param(task_id, marks=marks, id=task_id))
    return params


@pytest.mark.parametrize("task_id", _lattice_params())
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
