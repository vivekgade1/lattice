from __future__ import annotations

from pathlib import Path

from bench.datasets import load_fixtures, write_fixtures
from bench.runners import prepare_workdir, run_baseline
from bench.seed_tasks import SEED_TASKS
from lattice.llm.client import ScriptedGenerator

_S01 = next(t for t in SEED_TASKS if t.id == "s01")
_J01 = next(t for t in SEED_TASKS if t.id == "j01")


def _fenced(code: str) -> str:
    return f"```python\n{code}\n```\n"


_CORRECT_S01 = _fenced(
    "import pandas as pd\n\n"
    'orders = pd.read_csv("orders.csv", parse_dates=["order_date"])\n'
    'result = orders[orders["status"] == "paid"]\n'
    'result.to_parquet("out.parquet", index=False)\n'
)

_WRONG_S01 = _fenced(
    "import pandas as pd\n\n"
    'orders = pd.read_csv("orders.csv", parse_dates=["order_date"])\n'
    'result = orders[orders["status"] == "pending"]\n'
    'result.to_parquet("out.parquet", index=False)\n'
)

_RUNTIME_ERROR_SCRIPT = _fenced("import pandas as pd\n\nraise RuntimeError('boom')\n")

_NO_CODE_TEXT = "Sorry, I can't help with that request."


def _setup(tmp_path: Path) -> tuple[Path, dict]:
    fixtures_dir = tmp_path / "fixtures"
    write_fixtures(fixtures_dir)
    workdir = tmp_path / "work"
    write_fixtures(workdir)  # deterministic; byte-identical to fixtures_dir
    fixtures_frames = load_fixtures(fixtures_dir)
    return workdir, fixtures_frames


def test_correct_solution_is_marked_correct(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_CORRECT_S01])

    outcome = run_baseline(_S01, generator, workdir, fixtures_frames)

    assert outcome.correct is True
    assert outcome.failure is None
    assert outcome.arm == "A"
    assert outcome.tier == "single"
    assert outcome.check_passed is None
    assert outcome.rounds == 1


def test_wrong_solution_is_wrong_result(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_WRONG_S01])

    outcome = run_baseline(_S01, generator, workdir, fixtures_frames)

    assert outcome.correct is False
    assert outcome.failure == "wrong_result"


def test_no_code_block_is_no_code(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_NO_CODE_TEXT])

    outcome = run_baseline(_S01, generator, workdir, fixtures_frames)

    assert outcome.correct is False
    assert outcome.failure == "no_code"


def test_runtime_exception_is_runtime_error(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_RUNTIME_ERROR_SCRIPT])

    outcome = run_baseline(_S01, generator, workdir, fixtures_frames)

    assert outcome.correct is False
    assert outcome.failure == "runtime_error"
    assert "boom" in outcome.detail


def test_prepare_workdir_copies_a_single_input(tmp_path: Path) -> None:
    fixtures_dir = tmp_path / "fixtures"
    write_fixtures(fixtures_dir)
    root = tmp_path / "work"

    workdir = prepare_workdir(_S01, root, fixtures_dir)

    assert workdir == root / "s01"
    assert (workdir / "orders.csv").exists()
    assert not (workdir / "customers.csv").exists()


def test_prepare_workdir_copies_every_input_for_join_tasks(tmp_path: Path) -> None:
    fixtures_dir = tmp_path / "fixtures"
    write_fixtures(fixtures_dir)
    root = tmp_path / "work"

    workdir = prepare_workdir(_J01, root, fixtures_dir)

    assert (workdir / "orders.csv").exists()
    assert (workdir / "customers.csv").exists()
