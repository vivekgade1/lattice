"""`python -m bench.cli run --suite api --lattice-backend generated`: arms B/C served by the
FastAPI app generated from the Lattice program (offline, scripted model)."""

from __future__ import annotations

from pathlib import Path

from bench.api_tasks import API_TASKS
from bench.cli import main
from bench.report import read_outcomes
from bench.safety import outcome_class

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "api"

# Rejected by the checker (L802: an owned table returned without owned()), then repaired.
BROKEN = """# lattice: v1
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
    for arm in "BC":
        (root / arm).mkdir(parents=True)
    for task in API_TASKS:
        lattice = (FIXTURES / task.id / "lattice.py").read_text()
        (root / "B" / f"{task.id}.txt").write_text(_fence(BROKEN) + "\n=====\n" + _fence(lattice))
        (root / "C" / f"{task.id}.txt").write_text(_fence(lattice))


def _run(tmp_path: Path, *extra: str) -> dict:
    out = tmp_path / "out"
    argv = ["run", "--suite", "api", "--arms", "B,C", "--workers", "8"]
    argv += ["--offline", str(tmp_path / "script"), "--out", str(out), *extra]
    assert main(argv) == 0
    return {(o.arm, o.task_id): o for o in read_outcomes(out / "outcomes.jsonl")}


def test_lattice_arms_on_the_generated_app(tmp_path: Path) -> None:
    _script(tmp_path / "script")
    outcomes = _run(tmp_path, "--lattice-backend", "generated")
    assert len(outcomes) == 2 * len(API_TASKS)
    for task in API_TASKS:
        b, c = outcomes[("B", task.id)], outcomes[("C", task.id)]
        assert (outcome_class(b), b.rounds) == ("correct", 2), (task.id, b.detail)
        assert (outcome_class(c), c.rounds) == ("correct", 1), (task.id, c.detail)
        for o in (b, c):
            assert o.detail.startswith("backend=generated"), o.detail
            assert o.mean_queries > 0
    # the generated app was written next to each round's cases (work_root/model/arm/
    # task_id/rep{N}; no --model given, so the default "claude-opus-5", and rep0 since
    # --reps defaults to 1)
    work = tmp_path / "out" / "work" / "claude-opus-5" / "C" / API_TASKS[0].id / "rep0"
    assert (
        (work / "generated_round1" / "solution.py")
        .read_text()
        .startswith('"""Generated from a Lattice API program')
    )


def test_the_runtime_stays_the_default_backend(tmp_path: Path) -> None:
    _script(tmp_path / "script")
    outcomes = _run(tmp_path, "--tasks", API_TASKS[0].id)
    c = outcomes[("C", API_TASKS[0].id)]
    assert outcome_class(c) == "correct"
    assert not c.detail.startswith("backend=")
