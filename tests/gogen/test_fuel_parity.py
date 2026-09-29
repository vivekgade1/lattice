"""Fuel parity at the HTTP level (plan 5a Task 13): a procedure's loop sized to exhaust
the request's budget fails at the same step on both targets, with the same R620 message
and the same 500 body (and the same statement count and database after it).

Per program (t2go's /burn at its budget of 20,000 steps; hardening's /grow at a budget
of 5,000, so that one iteration can outgrow the budget below hardening's value size
cap), three requests around the budget:
* "step 1": the loop's first iteration alone costs more than the budget;
* "exact": the largest loop the budget covers (it ends with less fuel left than one
  more size costs: t2go's 9,999 iterations of 2 steps, hardening's 4,993 16-character
  units, each plus the calls' few steps);
* "one after": that loop plus one iteration, which fails.
The sizes are found on the runtime (a bisection), then the three requests run through
the equivalence gate's comparison (tests/gogen/test_go_differential.py).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

import lattice.api.runtime as runtime
from bench.api_model import ApiTask, HttpCase
from tests.api import hardening, t2prog
from tests.api.test_codegen_differential import PROGRAMS, Setup, setup_of
from tests.gogen.test_go_differential import compare

R620 = "error R620: procedure ran out of fuel"


def _case(name: str, path: str, token: str, query: dict[str, Any], status: int) -> HttpCase:
    return HttpCase(
        name=name, kind="happy", family="control", method="GET", path=path, token=token,
        query=query, expect_status=status,
    )  # fmt: skip


@dataclasses.dataclass(frozen=True)
class Loop:
    """An endpoint running a procedure's loop: `query(size)` sizes it (one more size is
    one more iteration of 2 steps for /burn -- the iteration and the len() call -- and
    one more step of the one iteration for /grow), `first(budget)` makes its first
    iteration exceed the budget."""

    pid: str
    path: str
    token: str
    budget: int
    query: Callable[[int], dict[str, Any]]
    first: Callable[[int], dict[str, Any]]


LOOPS = {
    "t2go": Loop(
        "t2go", "/burn", t2prog.TOKENS["alice"], t2prog.FUEL,
        query=lambda n: {"n": n, "w": 0},
        first=lambda b: {"n": 3, "w": 16 * (b + 1)},
    ),
    # grown(n, k): one iteration (k = 1) building a text of n characters, charged one
    # step per 16 characters (T2_SIZE_UNIT): 16 more characters, one more step
    "hardening": Loop(
        "hardening", "/grow", hardening.TOKENS["alice"], 5_000,
        query=lambda s: {"n": 16 * s, "k": 1},
        first=lambda b: {"n": 16 * (b + 1), "k": 3},
    ),
}  # fmt: skip


def _setup(loop: Loop) -> Setup:
    """The program's setup, its profile's fuel set to the loop's budget."""
    setup = setup_of(loop.pid)
    api = dataclasses.replace(setup.profile.api, fuel=loop.budget)
    return dataclasses.replace(setup, profile=dataclasses.replace(setup.profile, api=api))


def _status(pid: str, loop: Loop, query: dict[str, Any], db: Path) -> tuple[int, Any]:
    source, _ = PROGRAMS[pid]
    setup = _setup(loop)
    setup.build(db)
    try:
        _, app = runtime.serve_source(source, setup.profile, db, natives=setup.natives)
        assert app is not None
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get(
                loop.path, params=query, headers={"Authorization": f"Bearer {loop.token}"}
            )
        return r.status_code, r.json()
    finally:
        db.unlink(missing_ok=True)


def _largest_covered(pid: str, loop: Loop, tmp: Path) -> int:
    """The largest size whose loop the budget covers, on the runtime."""
    lo, hi = 0, loop.budget + 1  # size 0 fits; a size past the budget cannot
    assert _status(pid, loop, loop.query(lo), tmp / "s.db")[0] == 200
    assert _status(pid, loop, loop.query(hi), tmp / "s.db")[0] == 500
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if _status(pid, loop, loop.query(mid), tmp / "s.db")[0] == 200:
            lo = mid
        else:
            hi = mid
    return lo


@pytest.mark.parametrize("pid", sorted(LOOPS))
def test_fuel_runs_out_at_the_same_step(pid: str, tmp_path: Path, tmp_path_factory):
    loop = LOOPS[pid]
    size = _largest_covered(pid, loop, tmp_path)
    assert 0 < size <= loop.budget
    cases = (
        _case("step 1", loop.path, loop.token, loop.first(loop.budget), 500),
        _case("exact", loop.path, loop.token, loop.query(size), 200),
        _case("one after", loop.path, loop.token, loop.query(size + 1), 500),
    )
    source, _ = PROGRAMS[pid]
    task = ApiTask(f"fuel_{pid}", "control", "fuel parity", cases)
    go_out = tmp_path_factory.getbasetemp() / "gofuel"
    problems, runs = compare(pid, source, task, _setup(loop), go_out, tmp_path / "run")
    assert problems == [], "\n".join(problems)
    rt_results, rt_captured, go_results, go_captured = runs
    assert [c.status for c in rt_results.cases] == [c.status for c in go_results.cases]
    assert [c.status for c in go_results.cases] == [500, 200, 500]
    for captured in (rt_captured, go_captured):
        bodies = {c["name"]: c["body"] for c in captured}
        for name in ("step 1", "one after"):
            assert bodies[name]["error"] == "internal"
            assert R620 in bodies[name]["detail"], bodies[name]
            assert f"budget of {loop.budget} steps" in bodies[name]["detail"]
