from __future__ import annotations

import pytest

from bench.datasets import DATASETS, load_fixtures, write_fixtures
from bench.seed_tasks import SEED_TASKS
from bench.tasks import Task, suite


@pytest.fixture(scope="module")
def fixtures_frames(tmp_path_factory: pytest.TempPathFactory) -> dict:
    directory = tmp_path_factory.mktemp("fixtures")
    write_fixtures(directory, seed=7)
    return load_fixtures(directory)


def test_thirty_seed_tasks_with_unique_ids() -> None:
    assert len(SEED_TASKS) == 30
    ids = [t.id for t in SEED_TASKS]
    assert len(ids) == len(set(ids))


def test_tier_counts() -> None:
    counts: dict[str, int] = {}
    for task in SEED_TASKS:
        counts[task.tier] = counts.get(task.tier, 0) + 1
    assert counts == {"single": 10, "multi": 12, "join": 8}


def test_all_tasks_are_task_instances_with_valid_inputs() -> None:
    for task in SEED_TASKS:
        assert isinstance(task, Task)
        for name in task.inputs:
            assert name in DATASETS, f"{task.id} references unknown dataset {name!r}"


def test_every_reference_returns_at_least_one_row(fixtures_frames: dict) -> None:
    for task in SEED_TASKS:
        result = task.reference(fixtures_frames)
        assert result.height >= 1, f"{task.id} produced an empty result"


def test_ordered_tasks_mention_order_in_prompt() -> None:
    for task in SEED_TASKS:
        if task.ordered:
            lowered = task.prompt.lower()
            assert "sorted" in lowered or "order" in lowered, (
                f"{task.id} is ordered but prompt doesn't say so: {task.prompt!r}"
            )


def test_suite_seed_matches_seed_tasks() -> None:
    assert suite("seed") == SEED_TASKS


def test_suite_all_falls_back_to_seed_when_more_tasks_absent() -> None:
    assert suite("all") == SEED_TASKS


def test_suite_unknown_name_raises() -> None:
    with pytest.raises(ValueError):
        suite("bogus")


def test_prompts_never_mention_files() -> None:
    for task in SEED_TASKS:
        lowered = task.prompt.lower()
        assert ".csv" not in lowered
        assert ".parquet" not in lowered


def test_specific_task_ids_present() -> None:
    ids = {t.id for t in SEED_TASKS}
    expected_single = {f"s{i:02d}" for i in range(1, 11)}
    expected_multi = {f"m{i:02d}" for i in range(1, 13)}
    expected_join = {f"j{i:02d}" for i in range(1, 9)}
    assert ids == expected_single | expected_multi | expected_join
