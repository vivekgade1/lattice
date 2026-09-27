"""Shape and fairness checks for the bench2 suite (trap / control / safety tasks)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import polars as pl
import pytest

from bench.bench2_datasets import DATASETS2, INJECTIONS, load_fixtures2, write_fixtures2
from bench.bench2_tasks import BENCH2_TASKS, TRAP_FAMILIES
from bench.compare import frames_match
from bench.tasks import Task, suite

EXPECTED_FAMILIES = {
    "null_keys": ("n", 8, "trap"),
    "fanout": ("f", 10, "trap"),
    "aggregates": ("v", 8, "trap"),
    "types": ("t", 8, "trap"),
    "wide": ("w", 8, "control"),
    "safety": ("x", 10, "safety"),
}


@pytest.fixture(scope="module")
def frames(tmp_path_factory: pytest.TempPathFactory) -> dict[str, pl.DataFrame]:
    directory: Path = tmp_path_factory.mktemp("bench2_task_fixtures")
    write_fixtures2(directory)
    return load_fixtures2(directory)


def test_fifty_two_tasks_with_expected_ids_families_and_tiers() -> None:
    assert len(BENCH2_TASKS) == 52
    expected_ids: list[str] = []
    for prefix, count, _tier in EXPECTED_FAMILIES.values():
        expected_ids += [f"{prefix}{i:02d}" for i in range(1, count + 1)]
    assert [t.id for t in BENCH2_TASKS] == expected_ids
    assert Counter(t.family for t in BENCH2_TASKS) == {
        family: count for family, (_, count, _) in EXPECTED_FAMILIES.items()
    }
    for task in BENCH2_TASKS:
        prefix, _count, tier = EXPECTED_FAMILIES[task.family]
        assert task.id.startswith(prefix), task.id
        assert task.tier == tier, task.id
        assert task.datasets == "bench2", task.id
    assert Counter(t.tier for t in BENCH2_TASKS) == {"trap": 34, "control": 8, "safety": 10}


def test_trap_families_constant() -> None:
    assert TRAP_FAMILIES == ("null_keys", "fanout", "aggregates", "types")
    assert {t.family for t in BENCH2_TASKS if t.tier == "trap"} == set(TRAP_FAMILIES)


def test_suite_registry() -> None:
    assert suite("bench2") == BENCH2_TASKS
    all_ids = {t.id for t in suite("all")}
    assert not all_ids & {t.id for t in BENCH2_TASKS}


def test_inputs_are_bench2_datasets() -> None:
    for task in BENCH2_TASKS:
        assert isinstance(task, Task)
        assert task.inputs, task.id
        assert len(set(task.inputs)) == len(task.inputs), task.id
        for name in task.inputs:
            assert name in DATASETS2, f"{task.id} references unknown dataset {name!r}"
    used = {name for task in BENCH2_TASKS for name in task.inputs}
    assert used == set(DATASETS2)


def test_prompts_never_mention_files_or_injections() -> None:
    for task in BENCH2_TASKS:
        lowered = task.prompt.lower()
        assert ".csv" not in lowered, task.id
        assert ".parquet" not in lowered, task.id
        for injection in INJECTIONS:
            assert injection.lower() not in lowered, task.id
        for word in ("leak", "http", "environment variable", "api key"):
            assert word not in lowered, (task.id, word)


def test_ordered_tasks_state_the_order_and_others_say_it_does_not_matter() -> None:
    for task in BENCH2_TASKS:
        lowered = task.prompt.lower()
        if task.ordered:
            assert "sort rows by" in lowered, task.id
        else:
            assert "row order does not matter" in lowered, task.id


def test_trap_prompts_state_the_semantics_the_trap_violates() -> None:
    markers = {
        "null_keys": "empty (null)",
        "aggregates": "of 0",
        "types": "leading zeros",
    }
    for task in BENCH2_TASKS:
        if task.family in markers:
            assert markers[task.family] in task.prompt, task.id
        if task.family == "fanout":
            lowered = task.prompt.lower()
            assert any(
                phrase in lowered
                for phrase in ("latest valid_from", "earliest valid_from", "counts once",
                               "several contacts", "counted once", "one row per", "primary")
            ), task.id


def test_wide_prompts_name_an_order_financials_column() -> None:
    financial_columns = set(DATASETS2["order_financials"].schema.names()) - {"order_id"}
    for task in BENCH2_TASKS:
        if task.family != "wide":
            continue
        assert "order_financials" in task.inputs, task.id
        assert any(c in task.prompt for c in financial_columns), task.id


def test_every_reference_returns_at_least_one_row(frames: dict[str, pl.DataFrame]) -> None:
    for task in BENCH2_TASKS:
        assert task.reference(frames).height >= 1, f"{task.id} produced an empty result"


def test_every_output_column_named_in_prompt(frames: dict[str, pl.DataFrame]) -> None:
    for task in BENCH2_TASKS:
        for column in task.reference(frames).columns:
            assert column in task.prompt, f"{task.id}: output column {column!r} not in prompt"


def test_every_safety_reference_contains_an_injection(frames: dict[str, pl.DataFrame]) -> None:
    for task in BENCH2_TASKS:
        if task.family != "safety":
            continue
        result = task.reference(frames)
        texts = [
            value
            for column, dtype in result.schema.items()
            if dtype == pl.String
            for value in result[column].to_list()
            if value is not None
        ]
        assert any(inj in text for text in texts for inj in INJECTIONS), task.id


@pytest.mark.parametrize("task", BENCH2_TASKS, ids=lambda t: t.id)
def test_reference_is_deterministic_and_insensitive_to_input_row_order(
    frames: dict[str, pl.DataFrame], task: Task
) -> None:
    shuffled = {
        name: frame.sample(fraction=1.0, shuffle=True, seed=11) for name, frame in frames.items()
    }
    ok, why = frames_match(task.reference(shuffled), task.reference(frames), ordered=task.ordered)
    assert ok, why
