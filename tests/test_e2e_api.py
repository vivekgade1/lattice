"""API suite end to end, offline: CLI, scripted model, FastAPI sandbox, Lattice runtime, report."""

from pathlib import Path

from bench.api_tasks import API_TASKS
from bench.cli import main
from bench.report import read_outcomes
from bench.safety import outcome_class

FIXTURES = Path(__file__).parent / "fixtures" / "api"

# Rejected by the checker (L802: an owned table returned without owned()), then repaired.
BROKEN_LATTICE = """# lattice: v1
@endpoint("GET", "/orders/{order_id}", auth=Customer)
def get_order(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    orders = fetch(Orders)
    found = where(orders, col("id") == order_id)
    order = one(found, else_=NotFound("order"))
    return order
"""


def _fence(code: str) -> str:
    return f"```python\n{code}```\n"


def _script(root: Path) -> None:
    for arm in "APBC":
        (root / arm).mkdir(parents=True)
    for task in API_TASKS:
        d = FIXTURES / task.id
        naive = d / "fastapi_naive.py"
        fastapi = (naive if naive.exists() else d / "fastapi_correct.py").read_text()
        lattice = (d / "lattice.py").read_text()
        (root / "A" / f"{task.id}.txt").write_text(_fence(fastapi))
        (root / "P" / f"{task.id}.txt").write_text(_fence(fastapi))
        (root / "B" / f"{task.id}.txt").write_text(
            _fence(BROKEN_LATTICE) + "\n=====\n" + _fence(lattice)
        )
        (root / "C" / f"{task.id}.txt").write_text(_fence(lattice))


def test_api_suite_end_to_end(tmp_path: Path) -> None:
    _script(tmp_path / "script")
    out = tmp_path / "out"
    assert main(["run", "--suite", "api", "--arms", "A,P,B,C", "--workers", "8",
                 "--offline", str(tmp_path / "script"), "--out", str(out)]) == 0
    outcomes = {(o.arm, o.task_id): o for o in read_outcomes(out / "outcomes.jsonl")}
    assert len(outcomes) == 4 * len(API_TASKS)

    for task in API_TASKS:
        trap = (FIXTURES / task.id / "fastapi_naive.py").exists()
        for arm in "AP":
            o = outcomes[(arm, task.id)]
            assert outcome_class(o) == ("silent" if trap else "correct"), (arm, task.id, o.detail)
            assert o.rounds == 1  # a running-but-wrong app is never repaired
        b, c = outcomes[("B", task.id)], outcomes[("C", task.id)]
        assert (outcome_class(b), b.rounds) == ("correct", 2), (task.id, b.detail)
        assert (outcome_class(c), c.rounds) == ("correct", 1), (task.id, c.detail)
        assert c.mean_queries > 0 and c.program_ast_nodes > 0

    report = (out / "report.md").read_text()
    assert "silent_failure_reduction" in report and "query_efficiency" in report
