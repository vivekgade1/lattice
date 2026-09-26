from __future__ import annotations

from pathlib import Path

import pytest

from bench.cli import main

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


def test_cli_arm_b_raises_not_implemented(tmp_path: Path) -> None:
    offline_dir = tmp_path / "offline"
    arm_dir = offline_dir / "B"
    arm_dir.mkdir(parents=True, exist_ok=True)
    (arm_dir / "s01.txt").write_text("```python\npass\n```\n")
    out_dir = tmp_path / "out2"

    with pytest.raises(NotImplementedError):
        main(
            [
                "run",
                "--arms",
                "B",
                "--tasks",
                "s01",
                "--offline",
                str(offline_dir),
                "--out",
                str(out_dir),
            ]
        )
