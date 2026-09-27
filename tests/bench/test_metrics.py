"""Efficiency / program-quality metrics: program_metrics, cost, runner fields,
the report's efficiency table and verdict, old-JSONL compatibility, and the
``report`` CLI subcommand."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

import pytest

from bench.cli import main
from bench.datasets import load_fixtures, write_fixtures
from bench.metrics import PRICES, ProgramMetrics, cost_usd, price_for, program_metrics
from bench.report import (
    read_outcomes,
    render_markdown,
    render_markdown_step2,
    summarize,
    write_outcomes,
)
from bench.runners import TaskOutcome, run_baseline, run_baseline_repair, run_lattice
from bench.seed_tasks import SEED_TASKS
from lattice.llm.client import Generation, ScriptedGenerator, Usage

_S01 = next(t for t in SEED_TASKS if t.id == "s01")

# ---------------------------------------------------------------------------
# program_metrics
# ---------------------------------------------------------------------------


def test_program_metrics_excludes_blank_and_comment_only_lines() -> None:
    code = "# header comment\n\nx = 1\n   # indented comment\ny = x + 2  # trailing\n\n"
    m = program_metrics(code)
    assert m == ProgramMetrics(lines=2, statements=2, ast_nodes=m.ast_nodes, chars=len(code))
    assert m.ast_nodes > 0


def test_program_metrics_counts_nested_statements() -> None:
    code = "for i in range(3):\n    if i:\n        x = i\n    else:\n        pass\n"
    m = program_metrics(code)
    # for, if, assign, pass
    assert m.statements == 4
    assert m.lines == 5


def test_program_metrics_ast_nodes_matches_ast_walk() -> None:
    import ast

    code = "a = [1, 2, 3]\nprint(sum(a))\n"
    assert program_metrics(code).ast_nodes == sum(1 for _ in ast.walk(ast.parse(code)))


def test_program_metrics_unparseable_code_measures_lines_and_chars_only() -> None:
    code = "def broken(:\n    return 1\n"
    m = program_metrics(code)
    assert m == ProgramMetrics(lines=2, statements=0, ast_nodes=0, chars=len(code))


def test_program_metrics_empty() -> None:
    assert program_metrics("") == ProgramMetrics(0, 0, 0, 0)


# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------


def test_price_for_matches_by_prefix() -> None:
    assert price_for("claude-haiku-4-5-20251001") == PRICES["claude-haiku-4-5"]
    assert price_for("claude-opus-5") == PRICES["claude-opus-5"]
    # the longest prefix wins: opus-5-5 is not priced as opus-5
    assert price_for("claude-opus-5-5-20260901") == PRICES["claude-opus-5-5"]
    assert price_for("gpt-9") is None
    assert price_for("") is None


def test_cost_formula_without_cache() -> None:
    # haiku: $1 / $5 per million
    cost = cost_usd("claude-haiku-4-5", input_tokens=1_000_000, output_tokens=200_000)
    assert cost == pytest.approx(1.0 + 1.0)


def test_cost_formula_with_cache_read_and_creation() -> None:
    # opus-5: $5 in / $25 out. input_tokens is the total prompt size.
    cost = cost_usd(
        "claude-opus-5",
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_tokens=500_000,
        cache_creation_tokens=200_000,
    )
    uncached = 300_000
    expected = (
        uncached * 5 / 1e6 + 500_000 * 0.1 * 5 / 1e6 + 200_000 * 1.25 * 5 / 1e6 + 100_000 * 25 / 1e6
    )
    assert cost == pytest.approx(expected)


def test_cost_unknown_model_is_zero() -> None:
    assert cost_usd("mystery", input_tokens=1000, output_tokens=1000) == 0.0


# ---------------------------------------------------------------------------
# runners record the new fields
# ---------------------------------------------------------------------------


class _PricedGenerator:
    """ScriptedGenerator with real-looking usage and a served model id."""

    def __init__(self, texts: Sequence[str], model: str = "claude-haiku-4-5-20251001") -> None:
        self._inner = ScriptedGenerator(texts)
        self.model = model

    def generate(self, system: str, messages: Sequence[Mapping[str, str]]) -> Generation:
        g = self._inner.generate(system, messages)
        usage = Usage(
            input_tokens=1000,
            output_tokens=200,
            cache_read_input_tokens=400,
            cache_creation_input_tokens=100,
        )
        return Generation(
            text=g.text, usage=usage, latency_s=0.01, stop_reason="end_turn", model=self.model
        )


def _fenced(code: str) -> str:
    return f"```python\n{code}```\n"


_PANDAS_CRASH = "import pandas as pd\n\nraise RuntimeError('boom')\n"
_PANDAS_OK = (
    "import pandas as pd\n\n"
    "# keep paid orders\n"
    'orders = pd.read_csv("orders.csv", parse_dates=["order_date"])\n'
    'result = orders[orders["status"] == "paid"]\n'
    'result.to_parquet("out.parquet", index=False)\n'
)
_LATTICE_OK = (
    "# lattice: v1\n"
    "from lattice.ops import col, load, where, write\n"
    "from lattice.schemas import Orders\n\n"
    'orders = load("orders.csv", schema=Orders)\n'
    'paid = where(orders, col("status") == "paid")\n'
    'write(paid, "out.parquet")\n'
)
_LATTICE_SHADOW = (
    "# lattice: v1\n"
    "from lattice.ops import col, load, where, write\n"
    "from lattice.schemas import Orders\n\n"
    'orders = load("orders.csv", schema=Orders)\n'
    'orders = where(orders, col("status") == "paid")\n'
    'write(orders, "out.parquet")\n'
)


def _setup(tmp_path: Path) -> tuple[Path, dict]:
    fixtures_dir = tmp_path / "fixtures"
    write_fixtures(fixtures_dir)
    workdir = tmp_path / "work"
    write_fixtures(workdir)
    return workdir, load_fixtures(fixtures_dir)


def test_arm_a_records_program_metrics_exec_and_diagnostics(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    outcome = run_baseline(_S01, ScriptedGenerator([_fenced(_PANDAS_OK)]), workdir, frames)
    assert outcome.correct
    expected = program_metrics(_PANDAS_OK)
    assert outcome.program_lines == expected.lines == 4
    assert outcome.program_statements == expected.statements
    assert outcome.program_ast_nodes == expected.ast_nodes
    assert outcome.program_chars == len(_PANDAS_OK)
    assert outcome.exec_s > 0
    assert outcome.diagnostics_per_round == ((),)


def test_arm_p_measures_final_round_program_and_labels_rounds(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    gen = _PricedGenerator([_fenced(_PANDAS_CRASH), _fenced(_PANDAS_OK)])
    outcome = run_baseline_repair(_S01, gen, workdir, frames, max_repairs=2)
    assert outcome.correct and outcome.rounds == 2
    assert outcome.program_chars == len(_PANDAS_OK)
    assert outcome.program_ast_nodes == program_metrics(_PANDAS_OK).ast_nodes
    assert outcome.diagnostics_per_round == (("runtime_error",), ())
    assert outcome.exec_s > 0
    # two rounds of priced usage
    assert outcome.input_tokens == 2 * 1500
    assert outcome.cache_read_tokens == 800
    assert outcome.cache_creation_tokens == 200
    per_round = cost_usd(
        "claude-haiku-4-5",
        input_tokens=1500,
        output_tokens=200,
        cache_read_tokens=400,
        cache_creation_tokens=100,
    )
    assert outcome.cost_usd == pytest.approx(2 * per_round)


def test_arm_b_static_fail_then_pass_records_diagnostics_per_round(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    gen = ScriptedGenerator([_fenced(_LATTICE_SHADOW), _fenced(_LATTICE_OK)])
    outcome = run_lattice(_S01, gen, workdir, frames, arm="B", max_repairs=2)
    assert outcome.correct and outcome.rounds == 2
    assert outcome.diagnostics_per_round == (("L201",), ())
    assert outcome.program_chars == len(_LATTICE_OK)
    assert outcome.program_statements == program_metrics(_LATTICE_OK).statements
    assert outcome.exec_s > 0
    assert outcome.cost_usd == 0.0  # scripted generator: no model, no tokens


def test_arm_c_static_failure_is_measured_but_not_executed(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    gen = ScriptedGenerator([_fenced(_LATTICE_SHADOW)])
    outcome = run_lattice(_S01, gen, workdir, frames, arm="C", max_repairs=2)
    assert not outcome.correct
    assert outcome.diagnostics_per_round == (("L201",),)
    assert outcome.program_lines == program_metrics(_LATTICE_SHADOW).lines
    assert outcome.exec_s == 0.0


def test_no_code_round_has_zero_program_metrics(tmp_path: Path) -> None:
    workdir, frames = _setup(tmp_path)
    outcome = run_baseline(_S01, ScriptedGenerator(["no code here"]), workdir, frames)
    assert outcome.program_lines == outcome.program_ast_nodes == outcome.program_chars == 0
    assert outcome.diagnostics_per_round == (("no_code",),)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _outcome(**overrides: object) -> TaskOutcome:
    defaults: dict[str, object] = dict(
        task_id="t",
        arm="A",
        tier="single",
        correct=True,
        check_passed=None,
        rounds=1,
        output_tokens=100,
        input_tokens=1000,
        latency_s=1.0,
        failure=None,
        detail="",
        model="claude-haiku-4-5-20251001",
    )
    defaults.update(overrides)
    return TaskOutcome(**defaults)  # type: ignore[arg-type]


def _efficiency_outcomes() -> list[TaskOutcome]:
    return [
        _outcome(arm="P", family="fanout", cost_usd=0.02, program_lines=10,
                 program_ast_nodes=100, program_chars=300, exec_s=0.5),
        _outcome(arm="P", family="fanout", correct=False, failure="wrong_result",
                 cost_usd=0.02, program_lines=12, program_ast_nodes=100,
                 program_chars=320, exec_s=0.7),
        _outcome(arm="B", family="fanout", check_passed=True, cost_usd=0.03,
                 program_lines=6, program_ast_nodes=50, program_chars=200, exec_s=0.1),
        _outcome(arm="B", family="fanout", check_passed=True, cost_usd=0.03,
                 program_lines=8, program_ast_nodes=70, program_chars=250, exec_s=0.1),
    ]  # fmt: skip


def test_step2_report_renders_efficiency_table_and_verdict() -> None:
    md = render_markdown_step2(_efficiency_outcomes())
    assert "## Efficiency" in md
    # P: total 0.04 over 1 correct; B: 0.06 over 2 correct
    assert "$0.0200" in md  # P mean cost / task
    assert "$0.0400" in md  # P cost per correct
    assert "$0.0300" in md  # B mean cost / task and cost per correct
    # verdict: cost per correct 0.03/0.04 = 0.75x; AST nodes 60/100 = 0.60x
    assert "B vs P: cost per correct task 0.75×, program AST nodes 0.60×" in md


def test_step1_report_renders_efficiency_table_and_verdict_vs_a() -> None:
    outcomes = [
        _outcome(arm="A", tier="multi", cost_usd=0.01, program_ast_nodes=100,
                 program_chars=300, program_lines=10, exec_s=0.4),
        _outcome(arm="B", tier="multi", check_passed=True, cost_usd=0.02,
                 program_ast_nodes=80, program_chars=250, program_lines=8, exec_s=0.1),
    ]  # fmt: skip
    md = render_markdown(summarize(outcomes), None)
    assert "## Efficiency" in md
    assert "B vs A: cost per correct task 2.00×, program AST nodes 0.80×" in md


def test_cost_per_correct_is_na_when_nothing_correct() -> None:
    outcomes = [
        _outcome(arm="P", family="fanout", correct=False, failure="wrong_result", cost_usd=0.02)
    ]
    md = render_markdown_step2(outcomes)
    eff = md.split("## Efficiency", 1)[1]
    row = next(line for line in eff.splitlines() if line.startswith("| P | fanout"))
    assert "n/a" in row


def test_old_jsonl_without_new_fields_loads_and_renders_na(tmp_path: Path) -> None:
    old = {
        "task_id": "n01", "arm": "P", "tier": "trap", "correct": True, "check_passed": None,
        "rounds": 1, "output_tokens": 87, "input_tokens": 2426, "latency_s": 1.7,
        "failure": None, "detail": "", "cache_read_tokens": 0, "model": "mystery-model",
        "refused_attempts": 0, "unsafe": [], "family": "null_keys", "static_flags": [],
        "blocked": [],
    }  # fmt: skip
    path = tmp_path / "outcomes.jsonl"
    path.write_text(json.dumps(old) + "\n" + json.dumps({**old, "arm": "B"}) + "\n")
    outcomes = read_outcomes(path)
    assert outcomes[0].program_ast_nodes == 0
    assert outcomes[0].diagnostics_per_round == ()
    md = render_markdown_step2(outcomes)
    eff = md.split("## Efficiency", 1)[1]
    row = next(line for line in eff.splitlines() if line.startswith("| P | null_keys"))
    cells = [c.strip() for c in row.strip("|").split("|")]
    # cost, cost/correct, program lines, AST nodes and exec time are n/a; tokens are real
    assert cells.count("n/a") == 5
    assert "2426" in row


def test_old_jsonl_cost_is_recomputed_from_tokens_for_a_known_model(tmp_path: Path) -> None:
    o = _outcome(arm="P", family="fanout", input_tokens=1_000_000, output_tokens=0)
    assert o.cost_usd == 0.0
    md = render_markdown_step2([o])
    assert "$1.0000" in md  # haiku: $1 per million input tokens


def test_diagnostics_per_round_round_trips_as_tuples(tmp_path: Path) -> None:
    o = _outcome(arm="B", diagnostics_per_round=(("L201", "L203"), ()))
    path = tmp_path / "o.jsonl"
    write_outcomes(path, [o])
    (back,) = read_outcomes(path)
    assert back == o
    assert back.diagnostics_per_round == (("L201", "L203"), ())


# ---------------------------------------------------------------------------
# `report` subcommand
# ---------------------------------------------------------------------------


def test_report_subcommand_rerenders_from_outcomes(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    write_outcomes(out_dir / "outcomes.jsonl", _efficiency_outcomes()[:2])
    other = tmp_path / "other.jsonl"
    write_outcomes(other, _efficiency_outcomes()[2:])

    assert main(["report", str(out_dir), "--merge", str(other)]) == 0
    md = (out_dir / "report.md").read_text()
    assert "Step 2" in md  # bench2 outcomes (family set) use the step-2 renderer
    assert "B vs P: cost per correct task 0.75×" in md


def test_report_subcommand_step1_outcomes(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    write_outcomes(
        out_dir / "outcomes.jsonl",
        [_outcome(arm="A", tier="single"), _outcome(arm="B", tier="single", check_passed=True)],
    )
    assert main(["report", str(out_dir)]) == 0
    md = (out_dir / "report.md").read_text()
    assert md.startswith("# Lattice Benchmark Report\n")
    assert "## Efficiency" in md
    assert "## Gate" in md


def test_asdict_contains_new_fields() -> None:
    fields = asdict(_outcome())
    for name in (
        "cache_creation_tokens", "program_lines", "program_statements", "program_ast_nodes",
        "program_chars", "exec_s", "diagnostics_per_round", "cost_usd",
    ):  # fmt: skip
        assert name in fields
