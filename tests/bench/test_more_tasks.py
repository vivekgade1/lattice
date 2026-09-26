from __future__ import annotations

import pytest

from bench.datasets import DATASETS, load_fixtures, write_fixtures
from bench.more_tasks import MORE_TASKS
from bench.seed_tasks import SEED_TASKS
from bench.tasks import Task, suite

_TIER_TOLERANCE = 0.05


@pytest.fixture(scope="module")
def fixtures_frames(tmp_path_factory: pytest.TempPathFactory) -> dict:
    directory = tmp_path_factory.mktemp("more_fixtures")
    write_fixtures(directory, seed=7)
    return load_fixtures(directory)


def test_at_least_seventy_more_tasks() -> None:
    assert len(MORE_TASKS) >= 70


def test_suite_all_has_at_least_one_hundred_tasks() -> None:
    assert len(suite("all")) >= 100


def test_ids_unique_across_suites() -> None:
    all_tasks = suite("all")
    ids = [t.id for t in all_tasks]
    assert len(ids) == len(set(ids))


def test_more_tasks_ids_continue_seed_ranges() -> None:
    seed_ids = {t.id for t in SEED_TASKS}
    for task in MORE_TASKS:
        assert task.id not in seed_ids


def test_all_more_tasks_are_task_instances_with_valid_inputs() -> None:
    for task in MORE_TASKS:
        assert isinstance(task, Task)
        for name in task.inputs:
            assert name in DATASETS, f"{task.id} references unknown dataset {name!r}"


def test_tier_shares_within_bounds() -> None:
    all_tasks = suite("all")
    total = len(all_tasks)
    counts: dict[str, int] = {}
    for task in all_tasks:
        counts[task.tier] = counts.get(task.tier, 0) + 1

    targets = {"single": 0.33, "multi": 0.40, "join": 0.27}
    for tier, target in targets.items():
        share = counts.get(tier, 0) / total
        assert abs(share - target) <= _TIER_TOLERANCE, (
            f"tier {tier!r} share {share:.3f} not within {_TIER_TOLERANCE} of {target}"
        )


def test_every_more_reference_returns_at_least_one_row(fixtures_frames: dict) -> None:
    for task in MORE_TASKS:
        result = task.reference(fixtures_frames)
        assert result.height >= 1, f"{task.id} produced an empty result"


def test_every_all_suite_reference_returns_at_least_one_row(fixtures_frames: dict) -> None:
    for task in suite("all"):
        result = task.reference(fixtures_frames)
        assert result.height >= 1, f"{task.id} produced an empty result"


def test_ordered_tasks_mention_order_in_prompt() -> None:
    for task in MORE_TASKS:
        if task.ordered:
            lowered = task.prompt.lower()
            assert "sorted" in lowered or "order" in lowered, (
                f"{task.id} is ordered but prompt doesn't say so: {task.prompt!r}"
            )


def test_prompts_never_mention_files() -> None:
    for task in MORE_TASKS:
        lowered = task.prompt.lower()
        assert ".csv" not in lowered
        assert ".parquet" not in lowered


def test_every_output_column_named_in_prompt(fixtures_frames: dict) -> None:
    for task in MORE_TASKS:
        result = task.reference(fixtures_frames)
        for column in result.columns:
            assert column in task.prompt, (
                f"{task.id} output column {column!r} not named in prompt: {task.prompt!r}"
            )


def test_no_duplicate_task_ids_within_more_tasks() -> None:
    ids = [t.id for t in MORE_TASKS]
    assert len(ids) == len(set(ids))
