from __future__ import annotations

from pathlib import Path

import pytest

import bench.runners
from bench.cli import main
from bench.report import read_outcomes

_S01_SOLUTION = (
    "```python\n"
    "import pandas as pd\n\n"
    'orders = pd.read_csv("orders.csv", parse_dates=["order_date"])\n'
    'result = orders[orders["status"] == "paid"]\n'
    'result.to_parquet("out.parquet", index=False)\n'
    "```\n"
)

_S02_SOLUTION = (
    "```python\n"
    "import pandas as pd\n\n"
    'orders = pd.read_csv("orders.csv", parse_dates=["order_date"])\n'
    'orders["revenue"] = orders["qty"] * orders["unit_price"]\n'
    'orders.to_parquet("out.parquet", index=False)\n'
    "```\n"
)


def _write_offline_arm_a(offline_dir: Path) -> None:
    arm_dir = offline_dir / "A"
    arm_dir.mkdir(parents=True, exist_ok=True)
    (arm_dir / "s01.txt").write_text(_S01_SOLUTION)
    (arm_dir / "s02.txt").write_text(_S02_SOLUTION)


def test_cli_offline_run_arm_a_reports_full_accuracy(tmp_path: Path) -> None:
    offline_dir = tmp_path / "offline"
    _write_offline_arm_a(offline_dir)
    out_dir = tmp_path / "out"

    exit_code = main(
        [
            "run",
            "--arms",
            "A",
            "--tasks",
            "s01,s02",
            "--offline",
            str(offline_dir),
            "--out",
            str(out_dir),
        ]
    )

    assert exit_code == 0

    report = (out_dir / "report.md").read_text()
    assert "100.00%" in report
    assert "| A | single |" in report

    assert (out_dir / "outcomes.jsonl").exists()
    assert (out_dir / "fixtures" / "orders.csv").exists()
    assert (out_dir / "work" / "A" / "s01" / "orders.csv").exists()


def test_cli_restricts_to_requested_tasks_only(tmp_path: Path) -> None:
    offline_dir = tmp_path / "offline"
    _write_offline_arm_a(offline_dir)
    out_dir = tmp_path / "out"

    main(
        [
            "run",
            "--arms",
            "A",
            "--tasks",
            "s01",
            "--offline",
            str(offline_dir),
            "--out",
            str(out_dir),
        ]
    )

    assert (out_dir / "work" / "A" / "s01").exists()
    assert not (out_dir / "work" / "A" / "s02").exists()


_M01_PANDAS_SOLUTION = (
    "```python\n"
    "import pandas as pd\n\n"
    'orders = pd.read_csv("orders.csv", parse_dates=["order_date"])\n'
    'paid = orders[orders["status"] == "paid"]\n'
    'paid = paid.assign(revenue=paid["qty"] * paid["unit_price"])\n'
    'result = paid.groupby("customer_id", as_index=False)["revenue"].sum()\n'
    'result = result.rename(columns={"revenue": "total_revenue"})\n'
    'result.to_parquet("out.parquet", index=False)\n'
    "```\n"
)

_S01_LATTICE_SOLUTION = (
    "```python\n"
    "# lattice: v1\n"
    "from lattice.ops import col, load, where, write\n"
    "from lattice.schemas import Orders\n\n"
    'orders = load("orders.csv", schema=Orders)\n'
    'paid = where(orders, col("status") == "paid")\n'
    'write(paid, "out.parquet")\n'
    "```\n"
)

_M01_LATTICE_SOLUTION = (
    "```python\n"
    "# lattice: v1\n"
    "from lattice.ops import Str, Table, agg, col, group, load, step, where, write\n"
    "from lattice.schemas import Orders\n\n\n"
    "@step\n"
    "def revenue_by_customer(orders: Table[Orders], wanted: Str) -> Table:\n"
    '    kept = where(orders, col("status") == wanted)\n'
    "    totals = group(\n"
    '        kept, by=["customer_id"], total_revenue=agg.sum(col("qty") * col("unit_price"))\n'
    "    )\n"
    "    return totals\n\n\n"
    'orders = load("orders.csv", schema=Orders)\n'
    'paid_revenue = revenue_by_customer(orders, wanted="paid")\n'
    'write(paid_revenue, "out.parquet")\n'
    "```\n"
)


def _write_offline_all_arms(offline_dir: Path) -> None:
    _write_offline_arm_a(offline_dir)
    (offline_dir / "A" / "m01.txt").write_text(_M01_PANDAS_SOLUTION)
    for arm in ("B", "C"):
        arm_dir = offline_dir / arm
        arm_dir.mkdir(parents=True, exist_ok=True)
        (arm_dir / "s01.txt").write_text(_S01_LATTICE_SOLUTION)
        (arm_dir / "m01.txt").write_text(_M01_LATTICE_SOLUTION)


def test_cli_offline_run_over_three_arms_produces_a_report(tmp_path: Path) -> None:
    offline_dir = tmp_path / "offline"
    _write_offline_all_arms(offline_dir)
    out_dir = tmp_path / "out3"

    exit_code = main(
        [
            "run",
            "--arms",
            "A,B,C",
            "--tasks",
            "s01,m01",
            "--offline",
            str(offline_dir),
            "--out",
            str(out_dir),
        ]
    )

    assert exit_code == 0

    report = (out_dir / "report.md").read_text()
    assert "| A |" in report
    assert "| B |" in report
    assert "| C |" in report

    outcomes = read_outcomes(out_dir / "outcomes.jsonl")
    arms = {o.arm for o in outcomes}
    assert arms == {"A", "B", "C"}
    assert all(o.correct for o in outcomes), [
        (o.arm, o.task_id, o.failure, o.detail) for o in outcomes if not o.correct
    ]


# --- Regression tests for review finding M2 -------------------------------


def _raise_on_call(n: int, exc: BaseException):  # type: ignore[no-untyped-def]
    real = bench.runners.run_source
    calls = {"n": 0}

    def fake(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == n:
            raise exc
        return real(*args, **kwargs)

    return fake


def test_crash_in_one_task_is_isolated_as_harness_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    offline_dir = tmp_path / "offline"
    _write_offline_all_arms(offline_dir)
    out_dir = tmp_path / "out"
    monkeypatch.setattr(bench.runners, "run_source", _raise_on_call(1, RuntimeError("kaboom")))

    exit_code = main(
        ["run", "--arms", "B", "--tasks", "s01,m01", "--offline", str(offline_dir),
         "--out", str(out_dir)]
    )

    assert exit_code == 0
    outcomes = {o.task_id: o for o in read_outcomes(out_dir / "outcomes.jsonl")}
    assert set(outcomes) == {"s01", "m01"}
    assert outcomes["s01"].failure == "harness_error"
    assert outcomes["s01"].correct is False
    assert "RuntimeError" in outcomes["s01"].detail and "kaboom" in outcomes["s01"].detail
    assert outcomes["m01"].correct is True
    assert "harness_error" in (out_dir / "report.md").read_text()


def test_harness_error_detail_is_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    offline_dir = tmp_path / "offline"
    _write_offline_all_arms(offline_dir)
    out_dir = tmp_path / "out"
    monkeypatch.setattr(bench.runners, "run_source", _raise_on_call(1, RuntimeError("x" * 50_000)))

    main(["run", "--arms", "C", "--tasks", "s01", "--offline", str(offline_dir),
          "--out", str(out_dir)])

    [outcome] = read_outcomes(out_dir / "outcomes.jsonl")
    assert outcome.failure == "harness_error"
    assert len(outcome.detail) <= 2000


def test_missing_offline_response_is_a_harness_error_not_a_crash(tmp_path: Path) -> None:
    offline_dir = tmp_path / "offline"
    _write_offline_arm_a(offline_dir)  # has s01, s02 but not s09
    out_dir = tmp_path / "out"

    exit_code = main(["run", "--arms", "A", "--tasks", "s01,s09", "--offline",
                      str(offline_dir), "--out", str(out_dir)])

    assert exit_code == 0
    outcomes = {o.task_id: o for o in read_outcomes(out_dir / "outcomes.jsonl")}
    assert outcomes["s01"].correct is True
    assert outcomes["s09"].failure == "harness_error"
    assert "FileNotFoundError" in outcomes["s09"].detail


def test_outcomes_are_written_as_each_task_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    offline_dir = tmp_path / "offline"
    _write_offline_all_arms(offline_dir)
    out_dir = tmp_path / "out"
    # A non-Exception abort on the second task (e.g. Ctrl-C) must not lose the first.
    monkeypatch.setattr(bench.runners, "run_source", _raise_on_call(2, KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        main(["run", "--arms", "B", "--tasks", "s01,m01", "--offline", str(offline_dir),
              "--out", str(out_dir)])

    outcomes = read_outcomes(out_dir / "outcomes.jsonl")
    assert [(o.task_id, o.correct) for o in outcomes] == [("s01", True)]


def test_rerun_into_same_out_dir_starts_a_fresh_outcomes_file(tmp_path: Path) -> None:
    offline_dir = tmp_path / "offline"
    _write_offline_arm_a(offline_dir)
    out_dir = tmp_path / "out"
    argv = ["run", "--arms", "A", "--tasks", "s01", "--offline", str(offline_dir),
            "--out", str(out_dir)]

    main(argv)
    main(argv)

    assert len(read_outcomes(out_dir / "outcomes.jsonl")) == 1
