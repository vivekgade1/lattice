"""The 5b Go cost pilot's live-run cost estimate (Task 7), before the freeze.

``python -m bench.analysis.go_pilot_estimate`` prints a markdown estimate of the 480-run
pilot (40 API tasks x arms G, B x claude-sonnet-5, claude-haiku-4-5 x 3 reps) with a
low / central / high range. No model is called.

Project policy: token counts are estimated from characters (the CLI has no
``count_tokens``; no API key is used), with ``bench.edit_slice.run``'s constants and
formula (``CHARS_PER_TOKEN``, ``FEEDBACK_TOKENS``, ``OUTPUT_OVERHEAD``, and its expected
prompt of repair round k: the first prompt plus k earlier answers and feedbacks). Every
input is the real one where it exists:

- **Prompts:** each arm's actual system prompt (``api_go_system_prompt`` /
  ``api_lattice_system_prompt``, hints on, as the pilot runs) and each task's actual
  task message (``api_task_message``), for all 40 tasks.
- **Answers:** arm B's answer is the task's reference Lattice program
  (``tests/fixtures/api/<id>/lattice.py``, all 40). Arm G's is the hand-written Go of the
  dry run (``bench/offline/go_pilot/G``, final round) for its 5 tasks; for the other 35,
  the task's reference FastAPI program scaled by the mean Go/FastAPI character ratio of
  those 5 (an extrapolation: no Go reference exists for them).

Assumptions that vary by scenario (``SCENARIOS``):

- **Tokenizer:** ``low`` takes chars / 3.5 at face value; ``central``/``high`` multiply the
  prompt by ``TOKENIZER_FACTOR``, the ratio of CLI-billed input tokens to the chars / 3.5
  estimate measured on the earlier CLI-transport runs (option-1 edit-slice runs,
  ``bench/results/option1*_*.jsonl``, 1-round runs: 1.42 on Sonnet 5, n = 103; 1.09 on
  Haiku 4.5, n = 44).
- **Input price:** ``low`` bills the prompt at the list input price; ``central``/``high``
  at 2x, what the CLI's ``total_cost_usd`` (the pilot's ``cli_list_estimate``) was found to
  charge: a least-squares fit of cost on token classes over 210 earlier CLI runs gives
  exactly the list output price and 2x the list input price on cache-creation tokens,
  and the CLI writes the whole prompt to the cache on every call (no cache reads were
  ever recorded: each call is a fresh ``claude -p`` process).
- **Output:** tokens per round = answer chars / 3.5 x ``output_multiplier``: ``low`` 1.25
  (``OUTPUT_OVERHEAD``: fences and a sentence of prose); ``central`` the ratio measured for
  arm B on the earlier explicit API runs (output tokens / reference chars x 3.5: 1.90
  Sonnet, 1.51 Haiku); ``high`` the same ratio for arm P, FastAPI written directly (2.59,
  2.04). The same multiplier is applied to both arms, so they differ only by the size of
  their reference answer.
- **Repairs:** the share of runs needing a 1st / 2nd repair round: ``low`` (0.15, 0.05),
  about arm B's rate on Sonnet in the earlier API runs; ``central`` (0.30, 0.15),
  ``bench.edit_slice.run.REPAIR_RATE``; ``high`` (0.50, 0.25). Both arms get the same rate
  (arm G's is unknown; compile errors could make it higher). Feedback per repair:
  ``FEEDBACK_TOKENS`` (600).
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from bench.api_tasks import API_TASKS
from bench.edit_slice.run import CHARS_PER_TOKEN, FEEDBACK_TOKENS, OUTPUT_OVERHEAD
from bench.metrics import price_for

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "api"
OFFLINE_G = REPO / "bench" / "offline" / "go_pilot" / "G"
MODELS = ("claude-sonnet-5", "claude-haiku-4-5")
ARMS = ("G", "B")
REPS = 3

# CLI-billed input tokens / (chars / 3.5), measured on earlier CLI runs (module docstring).
TOKENIZER_FACTOR = {"claude-sonnet-5": 1.42, "claude-haiku-4-5": 1.09}
# Output tokens per round / (reference answer chars / 3.5), measured on the earlier
# explicit API runs: arm B (Lattice) and arm P (FastAPI written directly).
OUTPUT_MULTIPLIER_B = {"claude-sonnet-5": 1.90, "claude-haiku-4-5": 1.51}
OUTPUT_MULTIPLIER_P = {"claude-sonnet-5": 2.59, "claude-haiku-4-5": 2.04}
CLI_INPUT_PRICE_FACTOR = 2.0  # the CLI's estimate bills every prompt as a cache write


@dataclass(frozen=True)
class Scenario:
    name: str
    tokenizer: bool  # apply TOKENIZER_FACTOR to the prompt
    input_price_factor: float
    output_multiplier: Mapping[str, float]
    repair_rate: tuple[float, float]


SCENARIOS = (
    Scenario("low", False, 1.0, dict.fromkeys(MODELS, OUTPUT_OVERHEAD), (0.15, 0.05)),
    Scenario("central", True, CLI_INPUT_PRICE_FACTOR, OUTPUT_MULTIPLIER_B, (0.30, 0.15)),
    Scenario("high", True, CLI_INPUT_PRICE_FACTOR, OUTPUT_MULTIPLIER_P, (0.50, 0.25)),
)

_GO_BLOCK_RE = re.compile(r"```go\n(.*?)```", re.DOTALL)


def canned_go(task_id: str) -> str | None:
    """The dry run's final-round Go for ``task_id``, or None when it has none."""
    path = OFFLINE_G / f"{task_id}.txt"
    if not path.exists():
        return None
    blocks = _GO_BLOCK_RE.findall(path.read_text())
    return blocks[-1] if blocks else None


def answer_chars() -> tuple[dict[tuple[str, str], int], float]:
    """{(arm, task_id): answer chars} for every task, and the Go/FastAPI ratio used to
    extrapolate arm G's answers (see the module docstring)."""
    fastapi = {t.id: len((FIXTURES / t.id / "fastapi_correct.py").read_text()) for t in API_TASKS}
    go = {t.id: code for t in API_TASKS if (code := canned_go(t.id)) is not None}
    if not go:
        raise ValueError(f"no canned Go answers in {OFFLINE_G}")
    ratio = sum(len(code) for code in go.values()) / sum(fastapi[t] for t in go)
    sizes: dict[tuple[str, str], int] = {}
    for t in API_TASKS:
        sizes[("B", t.id)] = len((FIXTURES / t.id / "lattice.py").read_text())
        sizes[("G", t.id)] = len(go[t.id]) if t.id in go else round(fastapi[t.id] * ratio)
    return sizes, ratio


def prompt_chars() -> dict[tuple[str, str], tuple[int, int]]:
    """{(arm, task_id): (system chars, task message chars)}, the arms' real prompts."""
    from lattice.llm import api_prompt

    system = {
        "G": len(api_prompt.api_go_system_prompt(hints=True)),
        "B": len(api_prompt.api_lattice_system_prompt(hints=True)),
    }
    kind = {"G": "go", "B": "lattice"}
    return {
        (arm, t.id): (system[arm], len(api_prompt.api_task_message(t, kind[arm])))  # type: ignore[arg-type]
        for arm in ARMS
        for t in API_TASKS
    }


def run_cost(
    model: str, scenario: Scenario, system: int, user: int, answer: int
) -> tuple[float, float, float]:
    """(expected input tokens, expected output tokens, expected $) of one run -- the
    formula of ``bench.edit_slice.run.estimate``: repair round k's prompt is the first
    prompt plus k earlier answers and feedbacks, and happens in ``repair_rate[k-1]`` of
    the runs."""
    p_in, p_out = price_for(model) or (0.0, 0.0)
    factor = TOKENIZER_FACTOR[model] if scenario.tokenizer else 1.0
    prompt = (system + user) / CHARS_PER_TOKEN * factor
    out = answer / CHARS_PER_TOKEN * scenario.output_multiplier[model]
    rates = scenario.repair_rate
    expected_in = prompt + sum(
        rate * (prompt + k * (out + FEEDBACK_TOKENS)) for k, rate in enumerate(rates, start=1)
    )
    expected_out = out * (1 + sum(rates))
    cost = (expected_in * p_in * scenario.input_price_factor + expected_out * p_out) / 1e6
    return expected_in, expected_out, cost


def estimate(models: Sequence[str] = MODELS, reps: int = REPS) -> dict:
    """Per (scenario, model, arm): runs, mean prompt and answer tokens, $ per run and
    total; plus each scenario's total and the inputs used."""
    sizes, ratio = answer_chars()
    prompts = prompt_chars()
    cells = []
    totals: dict[str, float] = {}
    for scenario in SCENARIOS:
        total = 0.0
        for model in models:
            for arm in ARMS:
                rows = [
                    run_cost(model, scenario, *prompts[(arm, t.id)], sizes[(arm, t.id)])
                    for t in API_TASKS
                ]
                cost = sum(c for _, _, c in rows) * reps
                total += cost
                cells.append(
                    {
                        "scenario": scenario.name,
                        "model": model,
                        "arm": arm,
                        "runs": len(rows) * reps,
                        "in_tokens": sum(i for i, _, _ in rows) / len(rows),
                        "out_tokens": sum(o for _, o, _ in rows) / len(rows),
                        "usd_per_run": cost / (len(rows) * reps),
                        "usd": cost,
                    }
                )
        totals[scenario.name] = total
    n = len(API_TASKS)
    return {
        "cells": cells,
        "totals": totals,
        "runs": n * len(ARMS) * len(models) * reps,
        "go_fastapi_ratio": ratio,
        "system_chars": {arm: prompts[(arm, API_TASKS[0].id)][0] for arm in ARMS},
        "mean_user_chars": {
            arm: sum(prompts[(arm, t.id)][1] for t in API_TASKS) / n for arm in ARMS
        },
        "mean_answer_chars": {arm: sum(sizes[(arm, t.id)] for t in API_TASKS) / n for arm in ARMS},
    }


def render(est: Mapping) -> str:
    lines = [
        f"{est['runs']} runs. System prompt chars: G {est['system_chars']['G']:,}, "
        f"B {est['system_chars']['B']:,}; mean task message chars: G "
        f"{est['mean_user_chars']['G']:,.0f}, B {est['mean_user_chars']['B']:,.0f}; mean answer "
        f"chars: G {est['mean_answer_chars']['G']:,.0f}, B {est['mean_answer_chars']['B']:,.0f} "
        f"(G extrapolated for 35 tasks at {est['go_fastapi_ratio']:.2f}x FastAPI).",
        "",
        "| scenario | model | arm | runs | exp. input tok/run | exp. output tok/run | $/run | $ |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for c in est["cells"]:
        lines.append(
            f"| {c['scenario']} | {c['model']} | {c['arm']} | {c['runs']} | {c['in_tokens']:,.0f} "
            f"| {c['out_tokens']:,.0f} | {c['usd_per_run']:.4f} | {c['usd']:.2f} |"
        )
    lines.append("")
    lines += [f"- **{name}**: ${total:.2f}" for name, total in est["totals"].items()]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reps", type=int, default=REPS)
    args = parser.parse_args(argv)
    print(render(estimate(reps=args.reps)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
