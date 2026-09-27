"""Regression tests for review finding M4: prompt fairness between arms.

Spec §2: "same model, same prompt budget". The Lattice arm's worked examples
must not leak benchmark answers, and the pandas arm must get an equivalent
guide that works the very same example problems.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import polars as pl
import pytest

from bench.compare import frames_match
from bench.datasets import DATASETS, bench_profile, load_fixtures, write_fixtures
from bench.sandbox import run_python_script
from bench.tasks import Task, suite
from lattice.llm.prompt import (
    BASELINE_SYSTEM,
    baseline_task_message,
    dataset_description,
    lattice_system_prompt,
    lattice_task_message,
)
from lattice.runtime.exec import run_source

_FENCE_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)
# Example 2 is a top-n with an explicit order; the others are unordered.
_ORDERED = (False, True, False)


def _blocks(text: str) -> list[str]:
    return _FENCE_RE.findall(text)


@pytest.fixture(scope="module")
def fixtures_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("fixtures")
    write_fixtures(path)
    return path


@pytest.fixture(scope="module")
def lattice_example_outputs(
    fixtures_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> list[pl.DataFrame]:
    outputs = []
    for i, block in enumerate(_blocks(lattice_system_prompt())):
        workdir = tmp_path_factory.mktemp(f"lattice_example_{i}")
        for ds in DATASETS.values():
            shutil.copyfile(fixtures_dir / ds.filename, workdir / ds.filename)
        run_source(block, bench_profile(), workdir)
        outputs.append(pl.read_parquet(workdir / "out.parquet"))
    return outputs


def test_lattice_examples_do_not_reproduce_any_benchmark_task_output(
    fixtures_dir: Path, lattice_example_outputs: list[pl.DataFrame]
) -> None:
    frames = load_fixtures(fixtures_dir)
    task_columns = {t.id: frozenset(t.reference(frames).columns) for t in suite("all")}

    assert len(lattice_example_outputs) == 3
    for i, out in enumerate(lattice_example_outputs, start=1):
        cols = frozenset(out.columns)
        clashes = sorted(tid for tid, tcols in task_columns.items() if tcols == cols)
        assert not clashes, f"example {i} output columns {sorted(cols)} match tasks {clashes}"
        assert out.height > 0, f"example {i} produces an empty result"


def test_lattice_examples_do_not_match_any_task_reference_frame(
    fixtures_dir: Path, lattice_example_outputs: list[pl.DataFrame]
) -> None:
    frames = load_fixtures(fixtures_dir)
    for out in lattice_example_outputs:
        for task in suite("all"):
            ok, _ = frames_match(out, task.reference(frames), ordered=False)
            assert not ok, f"an example reproduces task {task.id}'s answer"


def test_baseline_examples_solve_exactly_the_lattice_example_problems(
    fixtures_dir: Path,
    lattice_example_outputs: list[pl.DataFrame],
    tmp_path: Path,
) -> None:
    pandas_blocks = _blocks(BASELINE_SYSTEM)
    assert len(pandas_blocks) == 3

    for i, (code, expected) in enumerate(
        zip(pandas_blocks, lattice_example_outputs, strict=True), start=1
    ):
        workdir = tmp_path / f"pandas_example_{i}"
        workdir.mkdir()
        for ds in DATASETS.values():
            shutil.copyfile(fixtures_dir / ds.filename, workdir / ds.filename)
        result = run_python_script(code, workdir, timeout_s=60.0)
        assert result.returncode == 0, result.stderr
        actual = pl.read_parquet(workdir / "out.parquet")
        ok, reason = frames_match(actual, expected, ordered=_ORDERED[i - 1])
        assert ok, f"pandas example {i} differs from Lattice example {i}: {reason}"


def test_baseline_prompt_budget_is_comparable_to_lattice_prompt() -> None:
    lattice_len = len(lattice_system_prompt())
    assert 0.7 * lattice_len <= len(BASELINE_SYSTEM) <= 1.3 * lattice_len, (
        len(BASELINE_SYSTEM),
        lattice_len,
    )


def test_baseline_prompt_has_pandas_specific_guidance() -> None:
    assert "parse_dates" in BASELINE_SYSTEM
    assert "index=False" in BASELINE_SYSTEM
    assert "exactly" in BASELINE_SYSTEM
    assert "out.parquet" in BASELINE_SYSTEM


def _task(**overrides: object) -> Task:
    defaults: dict[str, object] = dict(
        id="t01",
        tier="join",
        prompt="Do the thing with the data.",
        inputs=("orders", "customers"),
        reference=lambda frames: frames["orders"],
        ordered=False,
    )
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


def test_lattice_task_message_does_not_claim_inputs_are_preloaded() -> None:
    message = lattice_task_message(_task(), bench_profile())
    assert "already available" not in message
    assert "exactly as loaded" not in message


def test_both_task_messages_list_the_same_file_schema_and_output_info() -> None:
    task = _task()
    lattice = lattice_task_message(task, bench_profile())
    baseline = baseline_task_message(task)

    for name in task.inputs:
        line = f"- {dataset_description(name)}"
        assert line in lattice
        assert line in baseline
    for message in (lattice, baseline):
        assert message.startswith(task.prompt)
        assert "Input files (in the current directory):" in message
        assert "Write the final result to out.parquet" in message


def test_both_arms_get_the_loop_guidance_with_equal_substance() -> None:
    """Sub-project 1b: the Lattice guide's loop constructs have a pandas counterpart of
    comparable length covering the same three shapes, in both hint conditions."""
    from lattice.llm.prompt import _LOOPS_SECTION, _PANDAS_LOOPS_SECTION, baseline_system_prompt

    for hints in (True, False):
        assert _LOOPS_SECTION in lattice_system_prompt(hints=hints)
        assert _PANDAS_LOOPS_SECTION in baseline_system_prompt(hints=hints)
    assert 0.7 <= len(_PANDAS_LOOPS_SECTION) / len(_LOOPS_SECTION) <= 1.3
    # running / previous / rank / moving frame; tree walk; repeat until stable
    for needle in ("cumsum()", "shift(1)", 'rank(method="min")', "rolling(N", "unique key"):
        assert needle in _PANDAS_LOOPS_SECTION, needle
    for needle in ("visited", "smallest depth", "maximum depth"):
        assert needle in _PANDAS_LOOPS_SECTION, needle
    for needle in ("at most N rounds", "raise", "same rows"):
        assert needle in _PANDAS_LOOPS_SECTION, needle
    # the same worked computation in both arms
    for needle in ('"customer_id"', '"order_date", "order_id"', '"est"', '"target"', "0.5"):
        assert needle in _LOOPS_SECTION and needle in _PANDAS_LOOPS_SECTION, needle
