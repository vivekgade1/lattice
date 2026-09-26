"""Benchmark task model and suite registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

import polars as pl

Tier = Literal["single", "multi", "join", "deep"]


@dataclass(frozen=True)
class Task:
    id: str
    tier: Tier
    prompt: str
    inputs: tuple[str, ...]
    reference: Callable[[Mapping[str, pl.DataFrame]], pl.DataFrame]
    ordered: bool = False


def suite(name: str) -> tuple[Task, ...]:
    """Named task suites: "seed" (30), "deep" (30 hard multi-stage tasks), and
    "all" = seed + more + deep."""
    from bench.seed_tasks import SEED_TASKS

    if name == "seed":
        return SEED_TASKS
    if name == "deep":
        from bench.deep_tasks import DEEP_TASKS

        return DEEP_TASKS
    if name == "all":
        from bench.deep_tasks import DEEP_TASKS
        from bench.more_tasks import MORE_TASKS

        return SEED_TASKS + MORE_TASKS + DEEP_TASKS
    raise ValueError(f"unknown suite: {name!r}")
