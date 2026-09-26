"""Differential tests: hand-written Lattice solutions vs the seed tasks' polars references."""

from pathlib import Path

import polars as pl
import pytest

from bench.compare import frames_match
from bench.datasets import ORDERS, bench_profile, load_fixtures, polars_schema, write_fixtures
from bench.seed_tasks import SEED_TASKS
from lattice.check import check_source
from lattice.runtime.exec import run_source

SOLUTIONS = Path(__file__).parent / "fixtures" / "lattice_solutions"
TASKS = {t.id: t for t in SEED_TASKS}
SOLVED = sorted(p.stem for p in SOLUTIONS.glob("*.py"))
REQUIRED = {"s05", "s08", "s09", "m02", "m03", "m04", "m07", "m12", "j02", "j04", "j05", "j07"}


def _source(task_id: str) -> str:
    return (SOLUTIONS / f"{task_id}.py").read_text()


def test_required_tasks_have_solutions():
    assert REQUIRED <= set(SOLVED)
    assert set(SOLVED) <= set(TASKS)
    assert {TASKS[t].tier for t in SOLVED} == {"single", "multi", "join"}
    assert any("@step" in _source(t) for t in SOLVED)
    assert all(_source(t).startswith("# lattice: v1\n") for t in SOLVED)


@pytest.mark.parametrize("task_id", SOLVED)
def test_solution_matches_reference(task_id: str, tmp_path: Path):
    task = TASKS[task_id]
    write_fixtures(tmp_path)
    result, run = run_source(_source(task_id), bench_profile(), tmp_path)
    assert result.ok and result.diagnostics == (), [d.render() for d in result.diagnostics]
    assert run is not None
    assert run.warnings == ()
    actual = pl.read_parquet(tmp_path / "out.parquet")
    assert actual.equals(run.outputs["out.parquet"])
    expected = task.reference(load_fixtures(tmp_path))
    ok, why = frames_match(actual, expected, ordered=task.ordered)
    assert ok, why


def test_m08_ties_break_by_customer_id_ascending(tmp_path: Path):
    """Two customers with equal revenue: the lower customer_id comes first."""
    (tmp_path / "orders.csv").write_text(
        "order_id,customer_id,product_id,qty,unit_price,status,order_date,discount\n"
        "1,7,1,10,100.0,paid,2024-01-01,\n"
        "2,3,1,10,100.0,paid,2024-01-02,\n"
        "3,5,1,20,100.0,paid,2024-01-03,\n"
        "4,9,1,1,5.0,paid,2024-01-04,\n"
    )
    result, run = run_source(_source("m08"), bench_profile(), tmp_path)
    assert result.ok and run is not None
    actual = run.outputs["out.parquet"]
    assert actual["customer_id"].to_list() == [5, 3, 7]
    frames = {"orders": pl.read_csv(tmp_path / "orders.csv", schema=polars_schema(ORDERS))}
    ok, why = frames_match(actual, TASKS["m08"].reference(frames), ordered=True)
    assert ok, why


def test_solutions_are_canonical_enough_to_recheck():
    """Every solution checks cleanly on its own (no warnings either)."""
    for task_id in SOLVED:
        r = check_source(_source(task_id), bench_profile())
        assert r.ok and not r.diagnostics, (task_id, r.diagnostics)
