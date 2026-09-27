"""Benchmark task model and suite registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

import polars as pl

Tier = Literal["single", "multi", "join", "deep", "trap", "control", "safety"]


@dataclass(frozen=True)
class Task:
    id: str
    tier: Tier
    prompt: str
    inputs: tuple[str, ...]
    reference: Callable[[Mapping[str, pl.DataFrame]], pl.DataFrame]
    ordered: bool = False
    family: str = ""  # bench2 family: null_keys|fanout|aggregates|types|wide|safety
    datasets: str = "bench"  # dataset registry: "bench" or "bench2"


def suite(name: str) -> tuple[Task, ...]:
    """Named task suites: "seed" (30), "deep" (30 hard multi-stage tasks), and
    "all" = seed + more + deep; "bench2" (trap & safety suite) is separate."""
    from bench.seed_tasks import SEED_TASKS

    if name == "seed":
        return SEED_TASKS
    if name == "deep":
        from bench.deep_tasks import DEEP_TASKS

        return DEEP_TASKS
    if name == "bench2":
        try:
            from bench.bench2_tasks import BENCH2_TASKS
        except ModuleNotFoundError as exc:  # until the bench2 tasks module exists
            if exc.name != "bench.bench2_tasks":
                raise
            return ()
        return BENCH2_TASKS
    if name == "api":
        # API suite: a tuple of bench.api_model.ApiTask (not Task).
        try:
            from bench.api_tasks import API_TASKS
        except ModuleNotFoundError as exc:  # until the api tasks module exists
            if exc.name != "bench.api_tasks":
                raise
            return ()
        return API_TASKS  # type: ignore[return-value]
    if name == "all":
        from bench.deep_tasks import DEEP_TASKS
        from bench.more_tasks import MORE_TASKS

        return SEED_TASKS + MORE_TASKS + DEEP_TASKS
    raise ValueError(f"unknown suite: {name!r}")
