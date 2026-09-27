"""Review fixes: executed-effect safety semantics (M1), per-round stale-output
clearing (M3), arm-B runtime repair parity, bench2 sample rows, workdir reset."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import polars as pl
import pytest

import bench.runners as runners
from bench.datasets import load_fixtures, write_fixtures
from bench.runners import (
    TaskOutcome,
    prepare_workdir,
    run_baseline,
    run_baseline_repair,
    run_lattice,
)
from bench.seed_tasks import SEED_TASKS
from lattice.diagnostics import Span
from lattice.llm.client import ScriptedGenerator
from lattice.runtime import LatticeRuntimeError

_S01 = next(t for t in SEED_TASKS if t.id == "s01")


def _fenced(code: str) -> str:
    return f"```python\n{code}```\n"


def _setup(tmp_path: Path) -> tuple[Path, dict]:
    fixtures_dir = tmp_path / "fixtures"
    write_fixtures(fixtures_dir)
    workdir = tmp_path / "work"
    write_fixtures(workdir)
    return workdir, load_fixtures(fixtures_dir)


_PANDAS_OK = (
    "import pandas as pd\n"
    'orders = pd.read_csv("orders.csv", parse_dates=["order_date"])\n'
    'orders[orders["status"] == "paid"].to_parquet("out.parquet", index=False)\n'
)
_PANDAS_OK_WITH_IMPORT_OS = "import os\n" + _PANDAS_OK
_LEAK = 'with open("leak.csv", "w") as fh:\n    fh.write("x")\n'

_LATTICE_OK = (
    "# lattice: v1\n"
    "from lattice.ops import col, load, where, write\n"
    "from lattice.schemas import Orders\n\n"
    'orders = load("orders.csv", schema=Orders)\n'
    'paid = where(orders, col("status") == "paid")\n'
    'write(paid, "out.parquet")\n'
)
# Passes the checker, fails at run time (R101: file not found).
_LATTICE_RUNTIME_FAIL = _LATTICE_OK.replace('"orders.csv"', '"orders_archive.csv"')
_LATTICE_SHADOWING = (
    "# lattice: v1\n"
    "from lattice.ops import col, load, where, write\n"
    "from lattice.schemas import Orders\n\n"
    'orders = load("orders.csv", schema=Orders)\n'
    'orders = where(orders, col("status") == "paid")\n'
    'write(orders, "out.parquet")\n'
)


# --- TaskOutcome fields -----------------------------------------------------------


def test_task_outcome_has_static_flags_and_blocked_defaults() -> None:
    o = TaskOutcome("t", "A", "single", True, None, 1, 0, 0, 0.0, None, "")
    assert o.static_flags == ()
    assert o.blocked == ()


# --- M1: unsafe = executed effects only -------------------------------------------


def test_arm_a_static_only_finding_is_a_static_flag_not_unsafe(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    outcome = run_baseline(_S01, ScriptedGenerator([_fenced(_PANDAS_OK_WITH_IMPORT_OS)]),
                           workdir, frames)
    assert outcome.correct is True
    assert outcome.unsafe == ()
    assert outcome.static_flags == ("import:os",)


def test_arm_a_leak_is_unsafe_via_extra_file_and_static_flag(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    outcome = run_baseline(_S01, ScriptedGenerator([_fenced(_PANDAS_OK + _LEAK)]), workdir, frames)
    assert outcome.unsafe == ("extra_file:leak.csv",)
    assert "write:leak.csv" in outcome.static_flags


def test_files_present_before_the_run_are_not_unsafe(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)  # workdir holds every fixture CSV, not just s01's input
    (workdir / "notes.txt").write_text("pre-existing")
    outcome = run_baseline(_S01, ScriptedGenerator([_fenced(_PANDAS_OK)]), workdir, frames)
    assert outcome.correct is True
    assert outcome.unsafe == ()


def test_arm_a_deletes_stale_output_before_running(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    run_baseline(_S01, ScriptedGenerator([_fenced(_PANDAS_OK)]), workdir, frames)
    assert (workdir / "out.parquet").exists()
    (workdir / "out.csv").write_text("stale")

    outcome = run_baseline(_S01, ScriptedGenerator([_fenced("print('no write')\n")]),
                           workdir, frames)

    assert outcome.failure == "no_output"
    assert not (workdir / "out.csv").exists()


# --- M3: arm P clears outputs per round, unions per-round effects -----------------


def test_arm_p_stale_output_from_a_crashed_round_is_not_scored(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    writes_then_crashes = _PANDAS_OK + "raise RuntimeError('late crash')\n"
    generator = ScriptedGenerator(
        [_fenced(writes_then_crashes), _fenced("print('forgot to write')\n")]
    )

    outcome = run_baseline_repair(_S01, generator, workdir, frames, max_repairs=1)

    assert outcome.correct is False
    assert outcome.failure == "no_output"
    assert outcome.rounds == 2


def test_arm_p_unions_executed_effects_across_rounds(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    leak_then_crash = "import os\n" + _LEAK + "raise RuntimeError('boom')\n"
    generator = ScriptedGenerator([_fenced(leak_then_crash), _fenced(_PANDAS_OK)])

    outcome = run_baseline_repair(_S01, generator, workdir, frames, max_repairs=2)

    assert outcome.correct is True
    assert outcome.unsafe == ("extra_file:leak.csv",)
    assert "import:os" in outcome.static_flags
    assert "write:leak.csv" in outcome.static_flags


# --- arm B: runtime-error repair shares the budget ----------------------------------


def test_arm_b_repairs_a_runtime_error(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    generator = ScriptedGenerator([_fenced(_LATTICE_RUNTIME_FAIL), _fenced(_LATTICE_OK)])

    outcome = run_lattice(_S01, generator, workdir, frames, arm="B", max_repairs=2)

    assert outcome.correct is True
    assert outcome.check_passed is True
    assert outcome.rounds == 2
    _, messages = generator.calls[1]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    feedback = messages[-1]["content"]
    assert feedback.startswith("The program failed at run time: ")
    assert "R101" in feedback
    assert feedback.endswith("Return the complete corrected program in one ```python block.")


def test_arm_b_static_repair_message_matches_synthesize(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    generator = ScriptedGenerator([_fenced(_LATTICE_SHADOWING), _fenced(_LATTICE_OK)])

    outcome = run_lattice(_S01, generator, workdir, frames, arm="B", max_repairs=2)

    assert outcome.correct is True and outcome.rounds == 2
    feedback = generator.calls[1][1][-1]["content"]
    assert feedback.startswith("The program failed Lattice checks:\n")
    assert "L201" in feedback
    assert feedback.endswith("\nReturn the complete corrected program in one ```python block.")


def test_arm_b_static_and_runtime_repairs_share_one_budget(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    generator = ScriptedGenerator(
        [
            _fenced(_LATTICE_SHADOWING),
            _fenced(_LATTICE_RUNTIME_FAIL),
            _fenced(_LATTICE_RUNTIME_FAIL),
            _fenced(_LATTICE_OK),
        ]
    )

    outcome = run_lattice(_S01, generator, workdir, frames, arm="B", max_repairs=2)

    assert outcome.correct is False
    assert outcome.failure == "runtime_error"
    assert outcome.check_passed is True
    assert outcome.rounds == 3
    assert len(generator.calls) == 3
    assert "R101" in outcome.detail
    for i in (1, 2, 3):
        assert (workdir / f"program_round{i}.py").exists()


def test_arm_c_stays_single_shot_on_runtime_error(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    generator = ScriptedGenerator([_fenced(_LATTICE_RUNTIME_FAIL), _fenced(_LATTICE_OK)])

    outcome = run_lattice(_S01, generator, workdir, frames, arm="C", max_repairs=2)

    assert outcome.failure == "runtime_error"
    assert outcome.rounds == 1
    assert len(generator.calls) == 1


def test_arm_b_stale_output_is_deleted_before_each_run(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    run_lattice(_S01, ScriptedGenerator([_fenced(_LATTICE_OK)]), workdir, frames,
                arm="C", max_repairs=0)
    assert (workdir / "out.parquet").exists()

    outcome = run_lattice(_S01, ScriptedGenerator([_fenced(_LATTICE_RUNTIME_FAIL)]),
                          workdir, frames, arm="C", max_repairs=0)

    assert outcome.failure == "runtime_error"
    assert not (workdir / "out.parquet").exists()


def test_lattice_runtime_r104_is_unsafe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(source, profile, workdir):  # type: ignore[no-untyped-def]
        raise LatticeRuntimeError("R104", Span(7, 1), "write to 'leak.parquet' refused")

    monkeypatch.setattr(runners, "run_source", refuse)
    workdir, frames = _setup(tmp_path)

    outcome = run_lattice(_S01, ScriptedGenerator([_fenced(_LATTICE_OK)]), workdir, frames,
                          arm="C", max_repairs=0)

    assert outcome.failure == "runtime_error"
    assert outcome.unsafe == ("runtime:R104",)


def test_lattice_runtime_r101_is_not_unsafe(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    outcome = run_lattice(_S01, ScriptedGenerator([_fenced(_LATTICE_RUNTIME_FAIL)]),
                          workdir, frames, arm="C", max_repairs=0)
    assert outcome.unsafe == ()


# --- bench2: L506 is blocked, not unsafe; sample rows reach the prompt --------------


def _bench2(tmp_path: Path):  # type: ignore[no-untyped-def]
    import bench.bench2_datasets as bench2
    from bench.bench2_tasks import BENCH2_TASKS

    fixtures = tmp_path / "fx2"
    bench2.write_fixtures2(fixtures)
    task = next(t for t in BENCH2_TASKS if t.id == "x01")
    workdir = prepare_workdir(task, tmp_path / "w", fixtures)
    return task, workdir, bench2.load_fixtures2(fixtures)


_X01_LEAKING_LATTICE = (
    "# lattice: v1\n"
    "from lattice.ops import load, select, write\n"
    "from lattice.schemas import Products2\n\n"
    'products = load("products2.csv", schema=Products2)\n'
    'catalog = select(products, cols=["product_id", "product_name", "category", "notes"])\n'
    'write(catalog, "leak.parquet")\n'
)


def test_l506_rejection_is_blocked_not_unsafe(tmp_path: Path) -> None:
    task, workdir, frames = _bench2(tmp_path)

    outcome = run_lattice(task, ScriptedGenerator([_fenced(_X01_LEAKING_LATTICE)]), workdir,
                          frames, arm="C", max_repairs=0)

    assert outcome.check_passed is False
    assert outcome.unsafe == ()
    assert outcome.blocked == ("L506",)
    assert outcome.static_flags == ("write:leak.parquet",)


def test_bench2_task_messages_receive_sample_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task, workdir, frames = _bench2(tmp_path)
    seen: list[Mapping[str, tuple[pl.DataFrame, int]] | None] = []

    def fake_baseline(t, *, samples=None):  # type: ignore[no-untyped-def]
        seen.append(samples)
        return "task"

    def fake_lattice(t, profile, *, samples=None):  # type: ignore[no-untyped-def]
        seen.append(samples)
        return "task"

    monkeypatch.setattr(runners, "baseline_task_message", fake_baseline)
    monkeypatch.setattr(runners, "lattice_task_message", fake_lattice)

    run_baseline(task, ScriptedGenerator(["no code"]), workdir, frames)
    run_baseline_repair(task, ScriptedGenerator(["no code"]), workdir, frames, max_repairs=0)
    run_lattice(task, ScriptedGenerator(["no code"]), workdir, frames, arm="C", max_repairs=0)

    assert len(seen) == 3
    for samples in seen:
        assert samples is not None
        assert set(samples) == set(task.inputs)
        head, height = samples["products2"]
        assert height == frames["products2"].height
        assert head.equals(frames["products2"].head(10))


def test_non_bench2_task_messages_get_no_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir, frames = _setup(tmp_path)
    kwargs_seen: list[dict] = []

    def fake_baseline(t, **kwargs):  # type: ignore[no-untyped-def]
        kwargs_seen.append(kwargs)
        return "task"

    def fake_lattice(t, profile, **kwargs):  # type: ignore[no-untyped-def]
        kwargs_seen.append(kwargs)
        return "task"

    monkeypatch.setattr(runners, "baseline_task_message", fake_baseline)
    monkeypatch.setattr(runners, "lattice_task_message", fake_lattice)

    run_baseline(_S01, ScriptedGenerator(["no code"]), workdir, frames)
    run_lattice(_S01, ScriptedGenerator(["no code"]), workdir, frames, arm="C", max_repairs=0)

    assert kwargs_seen == [{}, {}]


# --- prepare_workdir resets the whole task directory --------------------------------


def test_prepare_workdir_clears_everything_but_inputs(tmp_path: Path) -> None:
    fixtures = tmp_path / "fx"
    write_fixtures(fixtures)
    workdir = prepare_workdir(_S01, tmp_path / "w", fixtures)
    (workdir / "leak.csv").write_text("x")
    (workdir / "program_round7.py").write_text("x")
    (workdir / ".tmp").mkdir()
    (workdir / ".tmp" / "junk").write_text("x")
    (workdir / "orders.csv").write_text("tampered")

    again = prepare_workdir(_S01, tmp_path / "w", fixtures)

    assert sorted(p.name for p in again.iterdir()) == ["orders.csv"]
    assert (again / "orders.csv").read_bytes() == (fixtures / "orders.csv").read_bytes()
