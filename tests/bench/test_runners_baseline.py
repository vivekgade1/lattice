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


# --- Regression tests for review findings M2 / minor 3 / minor 7 -----------

from lattice.llm.client import Generation, GenerationRefused, Usage  # noqa: E402


class _UsageGenerator:
    def __init__(self, text: str, usage: Usage, model: str) -> None:
        self._text, self._usage, self._model = text, usage, model

    def generate(self, system, messages):  # type: ignore[no-untyped-def]
        return Generation(self._text, self._usage, 0.25, "end_turn", model=self._model)


class _RefusingGenerator:
    def generate(self, system, messages):  # type: ignore[no-untyped-def]
        raise GenerationRefused("declined", usage=Usage(9, 2, 30, 4), latency_s=0.5, model="m1")


def test_prepare_workdir_removes_stale_outputs(tmp_path: Path) -> None:
    fixtures_dir = tmp_path / "fixtures"
    write_fixtures(fixtures_dir)
    workdir = prepare_workdir(_S01, tmp_path / "work", fixtures_dir)
    for name in ("out.parquet", "out.csv", "solution.py"):
        (workdir / name).write_text("stale from a previous run")

    again = prepare_workdir(_S01, tmp_path / "work", fixtures_dir)

    assert again == workdir
    for name in ("out.parquet", "out.csv", "solution.py"):
        assert not (workdir / name).exists(), name
    assert (workdir / "orders.csv").exists()


def test_stale_out_parquet_cannot_make_a_no_output_script_correct(tmp_path: Path) -> None:
    fixtures_dir = tmp_path / "fixtures"
    write_fixtures(fixtures_dir)
    fixtures_frames = load_fixtures(fixtures_dir)
    workdir = prepare_workdir(_S01, tmp_path / "work", fixtures_dir)
    run_baseline(_S01, ScriptedGenerator([_CORRECT_S01]), workdir, fixtures_frames)
    assert (workdir / "out.parquet").exists()

    workdir = prepare_workdir(_S01, tmp_path / "work", fixtures_dir)
    outcome = run_baseline(
        _S01, ScriptedGenerator([_fenced("print('forgot to write')")]), workdir, fixtures_frames
    )

    assert outcome.correct is False
    assert outcome.failure == "no_output"


def test_refusal_in_arm_a_is_a_refused_outcome_with_tokens(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)

    outcome = run_baseline(_S01, _RefusingGenerator(), workdir, fixtures_frames)

    assert outcome.correct is False
    assert outcome.failure == "refused"
    assert outcome.input_tokens == 9 + 30 + 4
    assert outcome.cache_read_tokens == 30
    assert outcome.output_tokens == 2
    assert outcome.model == "m1"


def test_arm_a_input_tokens_include_cache_and_model_is_recorded(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = _UsageGenerator(_CORRECT_S01, Usage(10, 20, 300, 4000), "claude-opus-5")

    outcome = run_baseline(_S01, generator, workdir, fixtures_frames)

    assert outcome.correct is True
    assert outcome.input_tokens == 4310
    assert outcome.cache_read_tokens == 300
    assert outcome.output_tokens == 20
    assert outcome.model == "claude-opus-5"
