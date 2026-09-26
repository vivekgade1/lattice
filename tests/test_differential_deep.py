"""Differential tests for the deep tier: every dNN task has a hand-written Lattice
solution that checks cleanly, runs without warnings, matches the polars reference,
and is a genuinely deep pipeline (5-8 transformation steps besides load/write)."""

import ast
from pathlib import Path

import polars as pl
import pytest

from bench.compare import frames_match
from bench.datasets import bench_profile, load_fixtures, write_fixtures
from bench.deep_tasks import DEEP_TASKS
from lattice.catalog import IO_OPS, TABLE_OPS
from lattice.check import check_source
from lattice.runtime.exec import run_source

SOLUTIONS = Path(__file__).parent / "fixtures" / "lattice_solutions"
TASKS = {t.id: t for t in DEEP_TASKS}
MIN_STEPS, MAX_STEPS = 5, 8


def _source(task_id: str) -> str:
    return (SOLUTIONS / f"{task_id}.py").read_text()


def op_step_count(source: str) -> int:
    """Assignments (module level and inside @step bodies) whose value calls a table
    op or a user step, excluding `load` (and `write`, which is never assigned)."""
    module = ast.parse(source)
    steps = {n.name for n in module.body if isinstance(n, ast.FunctionDef)}
    counted = (TABLE_OPS - IO_OPS) | steps
    bodies = [module.body] + [n.body for n in module.body if isinstance(n, ast.FunctionDef)]
    return sum(
        1
        for body in bodies
        for stmt in body
        if isinstance(stmt, ast.Assign)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Name)
        and stmt.value.func.id in counted
    )


def test_every_deep_task_has_a_solution():
    solved = {p.stem for p in SOLUTIONS.glob("d*.py")}
    assert solved == set(TASKS)
    assert all(_source(t).startswith("# lattice: v1\n") for t in TASKS)


@pytest.mark.parametrize("task_id", sorted(TASKS))
def test_deep_solution_checks_cleanly(task_id: str):
    r = check_source(_source(task_id), bench_profile())
    assert r.ok and not r.diagnostics, [d.render() for d in r.diagnostics]


@pytest.mark.parametrize("task_id", sorted(TASKS))
def test_deep_solution_has_five_to_eight_steps(task_id: str):
    n = op_step_count(_source(task_id))
    assert MIN_STEPS <= n <= MAX_STEPS, f"{task_id}: {n} op steps"


def test_op_step_count_counts_step_calls_and_bodies_but_not_io():
    src = (
        "# lattice: v1\n"
        "@step\n"
        "def f(t: Table[Orders]) -> Table:\n"
        "    a = where(t, col('qty') > 1)\n"
        "    return a\n"
        "orders = load('orders.csv', schema=Orders)\n"
        "b = f(orders)\n"
        "c = limit(b, n=3)\n"
        "write(c, 'out.parquet')\n"
    )
    assert op_step_count(src) == 3


@pytest.mark.parametrize("task_id", sorted(TASKS))
def test_deep_solution_matches_reference(task_id: str, tmp_path: Path):
    task = TASKS[task_id]
    write_fixtures(tmp_path)
    result, run = run_source(_source(task_id), bench_profile(), tmp_path)
    assert result.ok and result.diagnostics == (), [d.render() for d in result.diagnostics]
    assert run is not None
    assert run.warnings == ()
    actual = pl.read_parquet(tmp_path / "out.parquet")
    assert actual.equals(run.outputs["out.parquet"])
    expected = task.reference(load_fixtures(tmp_path))
    assert expected.height >= 1
    ok, why = frames_match(actual, expected, ordered=task.ordered)
    assert ok, why
