"""Hand-written solutions for every bench2 task (tests/fixtures/bench2/<id>/).

- ``lattice.py`` checks clean under ``bench2_profile()`` and its output matches the reference;
- ``pandas_correct.py`` runs in the sandbox (fixture CSVs in its workdir) and matches;
- ``pandas_naive.py`` (trap tasks and the "distinct" control family) is the
  natural-but-wrong script: it runs cleanly, writes out.parquet, and does NOT match —
  proving the trap is real.

The sandbox runs are done once per session, concurrently, and cached.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pytest

from bench.bench2_datasets import DATASETS2, bench2_profile, load_fixtures2, write_fixtures2
from bench.bench2_tasks import BENCH2_TASKS
from bench.compare import frames_match
from bench.sandbox import run_python_script
from lattice.check import check_source
from lattice.runtime.exec import run_source

SOLUTIONS = Path(__file__).parent / "fixtures" / "bench2"
TASKS = {t.id: t for t in BENCH2_TASKS}
ALL_IDS = [t.id for t in BENCH2_TASKS]
# Trap tasks, plus the "distinct" control family (f08, f09): its naive pandas script is
# still wrong, but Lattice does not guard it, so it is scored as a control.
NAIVE_IDS = [t.id for t in BENCH2_TASKS if t.tier == "trap" or t.family == "distinct"]


@dataclass(frozen=True)
class ScriptRun:
    returncode: int
    stderr: str
    output: pl.DataFrame | None


def _copy_inputs(fixtures: Path, workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    for dataset in DATASETS2.values():
        shutil.copy(fixtures / dataset.filename, workdir / dataset.filename)
    return workdir


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("bench2_fixtures")
    write_fixtures2(directory)
    return directory


@pytest.fixture(scope="session")
def frames(fixtures_dir: Path) -> Mapping[str, pl.DataFrame]:
    return load_fixtures2(fixtures_dir)


@pytest.fixture(scope="session")
def script_runs(
    fixtures_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> Mapping[tuple[str, str], ScriptRun]:
    """(task id, "pandas_correct" | "pandas_naive") -> sandbox result, run concurrently."""
    root = tmp_path_factory.mktemp("bench2_runs")
    jobs = [
        (task_id, kind)
        for task_id in ALL_IDS
        for kind in ("pandas_correct", "pandas_naive")
        if (SOLUTIONS / task_id / f"{kind}.py").is_file()
    ]

    def run(job: tuple[str, str]) -> ScriptRun:
        task_id, kind = job
        workdir = _copy_inputs(fixtures_dir, root / f"{task_id}_{kind}")
        code = (SOLUTIONS / task_id / f"{kind}.py").read_text()
        result = run_python_script(code, workdir, timeout_s=120.0)
        out = workdir / "out.parquet"
        output = pl.read_parquet(out) if out.is_file() else None
        return ScriptRun(result.returncode, result.stderr, output)

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(jobs, pool.map(run, jobs), strict=True))


def test_every_task_has_exactly_its_solution_files() -> None:
    assert sorted(p.name for p in SOLUTIONS.iterdir() if p.is_dir()) == sorted(ALL_IDS)
    for task in BENCH2_TASKS:
        files = {p.name for p in (SOLUTIONS / task.id).iterdir() if p.suffix == ".py"}
        expected = {"lattice.py", "pandas_correct.py"}
        if task.id in NAIVE_IDS:
            expected.add("pandas_naive.py")
        assert files == expected, task.id


@pytest.mark.parametrize("task_id", ALL_IDS)
def test_lattice_solution_checks_clean_and_matches(
    task_id: str, fixtures_dir: Path, frames: Mapping[str, pl.DataFrame], tmp_path: Path
) -> None:
    task = TASKS[task_id]
    source = (SOLUTIONS / task_id / "lattice.py").read_text()
    assert source.startswith("# lattice: v1\n")
    checked = check_source(source, bench2_profile())
    assert checked.ok and checked.diagnostics == (), [d.render() for d in checked.diagnostics]
    workdir = _copy_inputs(fixtures_dir, tmp_path / "run")
    result, run = run_source(source, bench2_profile(), workdir)
    assert result.ok and run is not None
    assert run.warnings == ()
    assert set(run.outputs) == {"out.parquet"}
    actual = pl.read_parquet(workdir / "out.parquet")
    ok, why = frames_match(actual, task.reference(frames), ordered=task.ordered)
    assert ok, why


@pytest.mark.parametrize("task_id", ALL_IDS)
def test_pandas_correct_matches(
    task_id: str,
    frames: Mapping[str, pl.DataFrame],
    script_runs: Mapping[tuple[str, str], ScriptRun],
) -> None:
    task = TASKS[task_id]
    run = script_runs[(task_id, "pandas_correct")]
    assert run.returncode == 0, run.stderr
    assert run.output is not None, "pandas_correct.py wrote no out.parquet"
    ok, why = frames_match(run.output, task.reference(frames), ordered=task.ordered)
    assert ok, why


@pytest.mark.parametrize("task_id", NAIVE_IDS)
def test_pandas_naive_runs_but_is_wrong(
    task_id: str,
    frames: Mapping[str, pl.DataFrame],
    script_runs: Mapping[tuple[str, str], ScriptRun],
) -> None:
    task = TASKS[task_id]
    run = script_runs[(task_id, "pandas_naive")]
    assert run.returncode == 0, run.stderr
    assert run.output is not None, "pandas_naive.py wrote no out.parquet"
    ok, _why = frames_match(run.output, task.reference(frames), ordered=task.ordered)
    assert not ok, f"{task_id}: the naive pandas script matches the reference (no trap)"
