"""Benchmark task model and suite registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

import polars as pl

Tier = Literal["single", "multi", "join"]


@dataclass(frozen=True)
class Task:
    id: str
    tier: Tier
    prompt: str
    inputs: tuple[str, ...]
    reference: Callable[[Mapping[str, pl.DataFrame]], pl.DataFrame]
    ordered: bool = False


def suite(name: str) -> tuple[Task, ...]:
    from bench.seed_tasks import SEED_TASKS

    if name == "seed":
        return SEED_TASKS
    if name == "all":
        try:
            from bench.more_tasks import MORE_TASKS
        except ModuleNotFoundError:
            return SEED_TASKS
        return SEED_TASKS + MORE_TASKS
    raise ValueError(f"unknown suite: {name!r}")
