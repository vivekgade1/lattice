"""System prompts and task messages for LLM generation.

Only the baseline (pandas) arm lives here for now; Task 21 adds the Lattice
system prompt and task message. ``lattice`` must never import ``bench`` at
module import time, so any lookup into the benchmark's dataset registry is
a lazy import inside the function that needs it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bench.tasks import Task

BASELINE_SYSTEM = (
    "You are an expert data engineer. Write one Python 3 script using pandas "
    "that reads the listed CSV files from the current directory, computes "
    "exactly what is requested, and writes the final result to out.parquet "
    "with exactly the requested columns, using index=False. "
    "Output exactly one ```python code block containing the complete script "
    "and nothing else runnable."
)


def dataset_description(name: str) -> str:
    """Render one input dataset as ``"<file>.csv — columns: c1 (T1), c2 (T2), ..."``."""
    from bench.datasets import DATASETS

    dataset = DATASETS[name]
    columns = ", ".join(
        f"{col_name} ({scalar.value})" for col_name, scalar in dataset.schema.columns
    )
    return f"{dataset.filename} — columns: {columns}"


def baseline_task_message(task: Task) -> str:
    """The user-turn message for the pandas baseline arm."""
    lines = [task.prompt, "", "Input files (read them from the current directory):"]
    for name in task.inputs:
        lines.append(f"- {dataset_description(name)}")
    lines.append("")
    lines.append('Write the final result to out.parquet with index=False.')
    return "\n".join(lines)
