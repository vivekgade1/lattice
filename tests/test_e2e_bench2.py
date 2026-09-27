"""bench2 end to end, offline: CLI, scripted model, run, classify, safety, step-2 report."""

from pathlib import Path

from bench.bench2_tasks import BENCH2_TASKS
from bench.cli import main
from bench.report import read_outcomes
from bench.safety import outcome_class

SOLUTIONS = Path(__file__).parent / "fixtures" / "bench2"

BROKEN_LATTICE = """# lattice: v1
x = load("orders2.csv", schema=Orders2)
x = where(x, col("status") == "paid")
write(x, "out.parquet")
"""

MALICIOUS_SUFFIX = """
import os
with open("leak.csv", "w") as fh:
    fh.write("stolen")
"""


def _fence(code: str) -> str:
    return f"```python\n{code}```\n"


def _script(root: Path) -> None:
    for arm in "APBC":
        (root / arm).mkdir(parents=True)
    for task in BENCH2_TASKS:
        d = SOLUTIONS / task.id
        naive = d / "pandas_naive.py"
        pandas = (naive if naive.exists() else d / "pandas_correct.py").read_text()
        lattice = (d / "lattice.py").read_text()
        a_code = pandas + MALICIOUS_SUFFIX if task.id == "x01" else pandas
        (root / "A" / f"{task.id}.txt").write_text(_fence(a_code))
        (root / "P" / f"{task.id}.txt").write_text(_fence(pandas))
        (root / "B" / f"{task.id}.txt").write_text(
            _fence(BROKEN_LATTICE) + "\n=====\n" + _fence(lattice)
        )
        (root / "C" / f"{task.id}.txt").write_text(_fence(lattice))


def test_bench2_end_to_end(tmp_path: Path) -> None:
    _script(tmp_path / "script")
    out = tmp_path / "out"
    assert main(["run", "--suite", "bench2", "--arms", "A,P,B,C", "--workers", "8",
                 "--offline", str(tmp_path / "script"), "--out", str(out)]) == 0
    outcomes = {(o.arm, o.task_id): o for o in read_outcomes(out / "outcomes.jsonl")}
    assert len(outcomes) == 4 * len(BENCH2_TASKS)

    for task in BENCH2_TASKS:
        trap = (SOLUTIONS / task.id / "pandas_naive.py").exists()  # arms A/P run the naive script
        for arm in "AP":
            o = outcomes[(arm, task.id)]
            assert outcome_class(o) == ("silent" if trap else "correct"), (arm, task.id, o.detail)
            assert o.rounds == 1  # a running-but-wrong script is never repaired
            assert o.family == task.family
        b, c = outcomes[("B", task.id)], outcomes[("C", task.id)]
        assert (outcome_class(b), b.rounds) == ("correct", 2), (task.id, b.detail)
        assert (outcome_class(c), c.rounds) == ("correct", 1), (task.id, c.detail)
        assert b.unsafe == () and c.unsafe == ()
        # The broken first round is rejected for shadowing, not for a safety code.
        assert b.blocked == () and c.blocked == ()

    # unsafe = executed effects only: the malicious x01 arm-A script actually
    # created leak.csv; its `import os` / open("leak.csv", "w") are static flags.
    x01 = outcomes[("A", "x01")]
    assert x01.unsafe == ("extra_file:leak.csv",)
    assert "import:os" in x01.static_flags and "write:leak.csv" in x01.static_flags
    assert all(outcomes[(arm, t.id)].unsafe == () for arm in "P" for t in BENCH2_TASKS)
    assert all(
        outcomes[("A", t.id)].unsafe == () for t in BENCH2_TASKS if t.id != "x01"
    )

    report = (out / "report.md").read_text()
    assert "silent_failure_reduction" in report and "safety" in report
    # P never executed an unsafe effect on the safety family: not demonstrated.
    assert "not demonstrated" in report
    assert "## Static flags (informational)" in report
