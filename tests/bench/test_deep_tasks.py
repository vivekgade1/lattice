from __future__ import annotations

import pytest

from bench.compare import frames_match
from bench.datasets import DATASETS, load_fixtures, write_fixtures
from bench.deep_tasks import DEEP_TASKS
from bench.more_tasks import MORE_TASKS
from bench.seed_tasks import SEED_TASKS
from bench.tasks import Task, suite


@pytest.fixture(scope="module")
def fixtures_frames(tmp_path_factory: pytest.TempPathFactory) -> dict:
    directory = tmp_path_factory.mktemp("deep_fixtures")
    write_fixtures(directory, seed=7)
    return load_fixtures(directory)


def test_thirty_deep_tasks_with_expected_ids() -> None:
    assert len(DEEP_TASKS) == 30
    assert [t.id for t in DEEP_TASKS] == [f"d{i:02d}" for i in range(1, 31)]


def test_all_deep_tasks_are_deep_tier_with_valid_inputs() -> None:
    for task in DEEP_TASKS:
        assert isinstance(task, Task)
        assert task.tier == "deep"
        assert task.inputs, task.id
        for name in task.inputs:
            assert name in DATASETS, f"{task.id} references unknown dataset {name!r}"


def test_deep_tasks_cover_all_three_datasets() -> None:
    used = {name for task in DEEP_TASKS for name in task.inputs}
    assert used == set(DATASETS)
    assert sum(1 for t in DEEP_TASKS if len(t.inputs) == 3) >= 3


def test_suites_include_deep_tier() -> None:
    assert suite("deep") == DEEP_TASKS
    assert suite("all") == SEED_TASKS + MORE_TASKS + DEEP_TASKS
    ids = [t.id for t in suite("all")]
    assert len(ids) == len(set(ids))


def test_every_deep_reference_returns_at_least_one_row(fixtures_frames: dict) -> None:
    for task in DEEP_TASKS:
        result = task.reference(fixtures_frames)
        assert result.height >= 1, f"{task.id} produced an empty result"


def test_every_output_column_named_in_prompt(fixtures_frames: dict) -> None:
    for task in DEEP_TASKS:
        result = task.reference(fixtures_frames)
        for column in result.columns:
            assert column in task.prompt, f"{task.id}: output column {column!r} not in prompt"


def test_prompts_never_mention_files() -> None:
    for task in DEEP_TASKS:
        lowered = task.prompt.lower()
        assert ".csv" not in lowered
        assert ".parquet" not in lowered


def test_ordered_tasks_state_the_order_and_others_say_it_does_not_matter() -> None:
    for task in DEEP_TASKS:
        lowered = task.prompt.lower()
        if task.ordered:
            assert "sort rows by" in lowered, task.id
        else:
            assert "row order does not matter" in lowered, task.id


@pytest.mark.parametrize("task", DEEP_TASKS, ids=lambda t: t.id)
def test_reference_is_insensitive_to_input_row_order(fixtures_frames: dict, task: Task) -> None:
    """Sorts, tie-breaks and limits fully determine the output: shuffling every input
    table must not change the (ordered) result."""
    shuffled = {
        name: frame.sample(fraction=1.0, shuffle=True, seed=11)
        for name, frame in fixtures_frames.items()
    }
    ok, why = frames_match(
        task.reference(shuffled), task.reference(fixtures_frames), ordered=task.ordered
    )
    assert ok, why
