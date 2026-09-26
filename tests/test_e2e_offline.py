"""End-to-end: CLI, scripted model, check/repair, execute, compare, report — all offline."""

from pathlib import Path

from bench.cli import main
from bench.report import read_outcomes
from bench.seed_tasks import SEED_TASKS

SOLUTIONS = Path(__file__).parent / "fixtures" / "lattice_solutions"

PANDAS = {
    "s01": """import pandas as pd
orders = pd.read_csv("orders.csv", parse_dates=["order_date"])
orders[orders["status"] == "paid"].to_parquet("out.parquet", index=False)
""",
    "s09": """import pandas as pd
orders = pd.read_csv("orders.csv")
out = orders.groupby("status").size().reset_index(name="n_orders")
out.to_parquet("out.parquet", index=False)
""",
    "m01": """import pandas as pd
orders = pd.read_csv("orders.csv")
paid = orders[orders["status"] == "paid"].copy()
paid["revenue"] = paid["qty"] * paid["unit_price"]
out = paid.groupby("customer_id")["revenue"].sum().reset_index(name="total_revenue")
out.to_parquet("out.parquet", index=False)
""",
}

# A plausible first-shot mistake: rebinding a name (shadowing), which the checker rejects.
SHADOWING = """# lattice: v1
from lattice.ops import col, load, where, write
from lattice.schemas import Orders

df = load("orders.csv", schema=Orders)
df = where(df, col("status") == "paid")
write(df, "out.parquet")
"""


def _fence(code: str) -> str:
    return f"Here is the program.\n\n```python\n{code}```\n"


def _script(root: Path) -> list[str]:
    ids = [t.id for t in SEED_TASKS]
    for arm in "ABC":
        (root / arm).mkdir(parents=True)
    for task_id in ids:
        solution = (SOLUTIONS / f"{task_id}.py").read_text()
        (root / "C" / f"{task_id}.txt").write_text(_fence(solution))
        (root / "B" / f"{task_id}.txt").write_text(
            _fence(SHADOWING) + "\n=====\n" + _fence(solution)
        )
    for task_id, code in PANDAS.items():
        (root / "A" / f"{task_id}.txt").write_text(_fence(code))
    return ids


def test_all_seed_tasks_end_to_end(tmp_path: Path) -> None:
    ids = _script(tmp_path / "script")
    out = tmp_path / "out"

    assert main(["run", "--arms", "B,C", "--suite", "seed", "--offline",
                 str(tmp_path / "script"), "--out", str(out)]) == 0
    outcomes = read_outcomes(out / "outcomes.jsonl")

    assert {(o.arm, o.task_id) for o in outcomes} == {(a, i) for a in "BC" for i in ids}
    wrong = [(o.arm, o.task_id, o.failure, o.detail[:200]) for o in outcomes if not o.correct]
    assert wrong == []
    assert all(o.rounds == 2 for o in outcomes if o.arm == "B")
    assert all(o.rounds == 1 for o in outcomes if o.arm == "C")
    report = (out / "report.md").read_text()
    assert "| B |" in report and "| C |" in report


def test_baseline_arm_end_to_end(tmp_path: Path) -> None:
    _script(tmp_path / "script")
    out = tmp_path / "out"

    assert main(["run", "--arms", "A", "--tasks", ",".join(PANDAS), "--offline",
                 str(tmp_path / "script"), "--out", str(out)]) == 0
    outcomes = read_outcomes(out / "outcomes.jsonl")

    assert [(o.task_id, o.correct, o.failure) for o in outcomes] == [
        (i, True, None) for i in ("s01", "s09", "m01")
    ]


def test_arm_c_reports_shadowing_without_repair(tmp_path: Path) -> None:
    script = tmp_path / "script"
    (script / "C").mkdir(parents=True)
    (script / "C" / "s01.txt").write_text(_fence(SHADOWING))
    out = tmp_path / "out"

    assert main(["run", "--arms", "C", "--tasks", "s01", "--offline", str(script),
                 "--out", str(out)]) == 0
    [outcome] = read_outcomes(out / "outcomes.jsonl")

    assert (outcome.correct, outcome.check_passed, outcome.failure) == (False, False, "shadowing")


def test_parallel_workers_give_the_same_outcomes(tmp_path: Path) -> None:
    _script(tmp_path / "script")
    runs = {}
    for workers in ("1", "6"):
        out = tmp_path / f"out{workers}"
        assert main(["run", "--arms", "B,C", "--suite", "seed", "--offline",
                     str(tmp_path / "script"), "--out", str(out), "--workers", workers]) == 0
        runs[workers] = sorted(
            (o.arm, o.task_id, o.correct, o.rounds, o.failure)
            for o in read_outcomes(out / "outcomes.jsonl")
        )
    assert runs["1"] == runs["6"] and len(runs["1"]) == 60
