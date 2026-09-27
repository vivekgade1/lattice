"""Semantics of `iterate(step, t, until=..., max=N)` (spec 2026-09-27 lattice-loops §3.3)
in the polars executor.

A round applies the step once. With until="fixpoint" the loop stops at the first round
whose output equals its input as a multiset of rows (row order and duplicates: a
multiset); with a Bool predicate it stops as soon as every row satisfies it (checked
before each round, and after the last). A loop still running after `max` rounds fails
loudly with R610 -- never a partial answer.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from lattice.check import check_source
from lattice.ir import lower
from lattice.profile import Profile
from lattice.runtime import LatticeRuntimeError
from lattice.runtime.exec import run_source
from lattice.typesys import Scalar, Schema

S = Scalar
COUNTERS = Schema.from_pairs([("id", S.INT), ("x", S.INT), ("flag", S.BOOL)], keys=[("id",)])
EDGES = Schema.from_pairs([("src", S.INT), ("dst", S.INT)])
PROFILE = Profile(schemas={"Counters": COUNTERS, "Edges": EDGES})

H = """# lattice: v1
from lattice.ops import load, derive, join, select, group, rename, sort, iterate, step, write
from lattice.ops import agg, col, when, Table
from lattice.schemas import Counters, Edges
"""

# x climbs by one per round until it reaches 3 (rows starting at 0 need 3 rounds)
CLIMB = """
@step
def climb(t: Table[Counters]) -> Table[Counters]:
    out = derive(t, x=when(col("x") < 3, then=col("x") + 1, otherwise=col("x")))
    return out
"""
# x flips between 0 and 1 forever
FLIP = """
@step
def flip(t: Table[Counters]) -> Table[Counters]:
    out = derive(t, x=1 - col("x"))
    return out
"""


def _data(tmp_path: Path, rows: list[tuple[int, int, bool]]) -> Path:
    pl.DataFrame(
        rows, schema={"id": pl.Int64, "x": pl.Int64, "flag": pl.Boolean}, orient="row"
    ).write_csv(tmp_path / "c.csv")
    return tmp_path


def run(tmp_path: Path, steps: str, call: str, rows: list[tuple[int, int, bool]]) -> pl.DataFrame:
    source = (
        H
        + steps
        + '\nc = load("c.csv", schema=Counters)\n'
        + f"done = {call}\n"
        + 'write(done, "out.csv")\n'
    )
    root = _data(tmp_path, rows)
    check, result = run_source(source, PROFILE, root)
    assert result is not None, [d.render() for d in check.diagnostics]
    return result.outputs["out.csv"].sort("id")


ROWS = [(1, 0, False), (2, 2, True), (3, 5, False)]


def test_converges_in_k_rounds(tmp_path: Path):
    # rounds 1..3 change x; round 4 returns its input: the fixpoint is seen in round 4
    out = run(tmp_path, CLIMB, 'iterate(climb, c, until="fixpoint", max=4)', ROWS)
    assert out["x"].to_list() == [3, 3, 5]


def test_one_round_short_of_the_fixpoint_is_r610(tmp_path: Path):
    with pytest.raises(LatticeRuntimeError) as err:
        run(tmp_path, CLIMB, 'iterate(climb, c, until="fixpoint", max=3)', ROWS)
    assert err.value.code == "R610"
    assert "iterate did not converge in 3 rounds" in err.value.message


def test_an_oscillating_step_is_r610_never_an_answer(tmp_path: Path):
    for cap in (1, 2, 3, 50):
        with pytest.raises(LatticeRuntimeError) as err:
            run(tmp_path, FLIP, f'iterate(flip, c, until="fixpoint", max={cap})', ROWS)
        assert err.value.code == "R610"
        assert f"did not converge in {cap} rounds" in err.value.message
        assert err.value.span.line > 0


def test_a_table_already_at_its_fixpoint_stops_in_round_one(tmp_path: Path):
    rows = [(1, 3, False), (2, 7, True)]
    out = run(tmp_path, CLIMB, 'iterate(climb, c, until="fixpoint", max=1)', rows)
    assert out.rows() == [(1, 3, False), (2, 7, True)]


def test_a_predicate_stop(tmp_path: Path):
    # stops once every x >= 2: after two rounds (0 -> 2), before x reaches 3
    rows = [(1, 0, False), (2, 1, True)]
    out = run(tmp_path, CLIMB, 'iterate(climb, c, until=col("x") >= 2, max=10)', rows)
    assert out["x"].to_list() == [2, 3]


def test_a_predicate_already_true_runs_no_round(tmp_path: Path):
    out = run(tmp_path, FLIP, 'iterate(flip, c, until=col("x") >= 0, max=1)', ROWS)
    assert out["x"].to_list() == [0, 2, 5]


def test_a_predicate_met_after_the_last_round(tmp_path: Path):
    rows = [(1, 0, False)]
    out = run(tmp_path, CLIMB, 'iterate(climb, c, until=col("x") == 2, max=2)', rows)
    assert out["x"].to_list() == [2]


def test_a_predicate_never_met_is_r610(tmp_path: Path):
    with pytest.raises(LatticeRuntimeError) as err:
        run(tmp_path, CLIMB, 'iterate(climb, c, until=col("x") > 3, max=20)', ROWS)
    assert err.value.code == "R610"
    assert "iterate did not converge in 20 rounds" in err.value.message


def test_a_null_predicate_value_does_not_count_as_satisfied(tmp_path: Path):
    # x / 0 is null on every row: the predicate is never known to hold
    with pytest.raises(LatticeRuntimeError) as err:
        run(tmp_path, CLIMB, 'iterate(climb, c, until=col("x") / 0 >= 0, max=5)', ROWS)
    assert err.value.code == "R610"


def test_fixpoint_compares_rows_as_a_multiset(tmp_path: Path):
    # the step reverses the row order (and keeps the rows): a fixpoint in round one
    steps = """
@step
def shuffle(t: Table[Counters]) -> Table[Counters]:
    out = sort(t, by=["id"], desc=True)
    return out
"""
    out = run(tmp_path, steps, 'iterate(shuffle, c, until="fixpoint", max=1)', ROWS)
    assert out.rows() == [(1, 0, False), (2, 2, True), (3, 5, False)]


def test_a_multi_op_step_body_reaches_its_fixpoint(tmp_path: Path):
    # hop: the pairs two edges apart (join + group); on a chain it empties, then the
    # empty table is its own fixpoint
    steps = """
@step
def hop(r: Table[Edges]) -> Table[Edges]:
    firsts = rename(r, dst="mid")
    seconds = rename(r, src="mid", dst="far")
    paths = join(firsts, seconds, on=["mid"], fanout="allow")
    longer = select(paths, cols=["src", "far"])
    as_edges = rename(longer, far="dst")
    pairs = group(as_edges, by=["src", "dst"], n=agg.count())
    out = select(pairs, cols=["src", "dst"])
    return out
"""
    edges = [(1, 2), (2, 3), (3, 4)]
    pl.DataFrame(edges, schema={"src": pl.Int64, "dst": pl.Int64}, orient="row").write_csv(
        tmp_path / "e.csv"
    )
    program = (
        H
        + steps
        + '\ne = load("e.csv", schema=Edges)\n'
        + 'done = iterate(hop, e, until="fixpoint", max=10)\n'
        + 'write(done, "out.csv")\n'
    )
    check = check_source(program, PROFILE)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    _, result = run_source(program, PROFILE, tmp_path)
    assert result is not None
    assert result.outputs["out.csv"].height == 0


def test_iterate_lowers_to_one_node_with_its_body():
    source = (
        H
        + CLIMB
        + '\nc = load("c.csv", schema=Counters)\n'
        + 'done = iterate(climb, c, until="fixpoint", max=4)\n'
        + 'write(done, "out.csv")\n'
    )
    check = check_source(source, PROFILE)
    assert check.program is not None
    plan = lower(check.program)
    node = next(n for n in plan.nodes if n.op == "iterate")
    assert node.params["max"] == 4 and node.params["until"] == "fixpoint"
    body = node.params["body"]
    assert [n.op for n in body.nodes] == ["derive"]
    assert body.nodes[0].inputs == (node.params["param"],)
    assert node.params["result"] == body.nodes[0].id


def test_nested_iterate_inside_a_step(tmp_path: Path):
    steps = (
        CLIMB
        + """
@step
def settle(t: Table[Counters]) -> Table[Counters]:
    inner = iterate(climb, t, until="fixpoint", max=10)
    out = derive(inner, x=col("x") + 0)
    return out
"""
    )
    out = run(tmp_path, steps, 'iterate(settle, c, until="fixpoint", max=2)', ROWS)
    assert out["x"].to_list() == [3, 3, 5]
