"""Hypothesis differential (spec 2026-09-27 lattice-loops §6.3): `window(...)` and
`traverse(...)` on generated tables give the same frame, in the same order, through the
polars executor and through the compiled SQL (one statement, SQLite).

Tables have nulls and ties in the order column (row order among tied rows is the input
order on both targets), random partitions, and random parent links (cycles, several
roots, dangling parents, several start rows).
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import polars as pl
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bench.compare import frames_match
from lattice.api.sql import compile_select, rels_from_plan
from lattice.ir import lower
from lattice.profile import Profile
from lattice.runtime.exec import run_source
from lattice.typesys import Scalar, Schema
from tests.api.test_sql import _write_csv, sql_frame

S = Scalar
ROWS = Schema.from_pairs(
    [("id", S.INT), ("grp", S.STR), ("o", S.INT), ("v", S.INT), ("f", S.FLOAT), ("d", S.DATE)],
    keys=[("id",)],
    nullable=["grp", "o", "v", "f", "d"],
)
TREE = Schema.from_pairs(
    [("id", S.INT), ("parent_id", S.INT), ("label", S.STR)],
    keys=[("id",)],
    nullable=["parent_id"],
)
PROFILE = Profile(schemas={"Rows": ROWS, "Tree": TREE})
TABLES = {"rows.csv": ("rows", ROWS), "tree.csv": ("tree", TREE)}
SETTINGS = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


def _run(body: str, rows: list[tuple], tree: list[tuple]) -> None:
    src = (
        "# lattice: v1\n"
        'r = load("rows.csv", schema=Rows)\n'
        't = load("tree.csv", schema=Tree)\n' + body + 'write(x, "out.csv")\n'
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_csv(root / "rows.csv", ROWS, rows)
        _write_csv(root / "tree.csv", TREE, tree)
        check, run = run_source(src, PROFILE, root)
        assert check.program is not None and run is not None, [
            d.render() for d in check.diagnostics
        ]
        expected = run.outputs["out.csv"]
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE rows (id INTEGER PRIMARY KEY, grp TEXT, o INTEGER, v INTEGER, f REAL, d TEXT)"
    )
    conn.execute("CREATE TABLE tree (id INTEGER PRIMARY KEY, parent_id INTEGER, label TEXT)")
    conn.executemany("INSERT INTO rows VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.executemany("INSERT INTO tree VALUES (?, ?, ?)", tree)
    plan = lower(check.program)
    written = next(n for n in plan.nodes if n.op == "write")
    rels = rels_from_plan(plan.nodes, TABLES)
    actual = sql_frame(conn, rels, written.inputs[0])
    ok, why = frames_match(actual, expected, ordered=True)
    query = compile_select(rels, written.inputs[0]).text
    assert ok, f"{why}\n{body}\nsql:\n{actual}\npolars:\n{expected}\n{query}"


def _maybe(strategy: st.SearchStrategy) -> st.SearchStrategy:
    return st.one_of(st.none(), strategy)


@st.composite
def row_tables(draw: st.DrawFn) -> list[tuple]:
    n = draw(st.integers(0, 14))
    ids = sorted(draw(st.permutations(range(1, 40)))[:n])  # SQL reads in key order
    out = []
    for i in ids:
        out.append(
            (
                i,
                draw(_maybe(st.sampled_from(["a", "b", "c"]))),
                draw(_maybe(st.integers(-2, 3))),
                draw(_maybe(st.integers(-50, 50))),
                draw(_maybe(st.integers(-8, 8).map(lambda k: k / 2))),
                draw(_maybe(st.sampled_from(["2024-01-01", "2024-02-29", "2023-12-31"]))),
            )
        )
    return out


@st.composite
def trees(draw: st.DrawFn) -> list[tuple]:
    n = draw(st.integers(0, 14))
    ids = sorted(draw(st.permutations(range(1, 30)))[:n])
    targets = [*ids, 99]  # 99: a dangling parent
    return [(i, draw(_maybe(st.sampled_from(targets))), f"n{i}") for i in ids]


ALL_FUNCS = (
    "n=win.row_number(), rk=win.rank(), drk=win.dense_rank(), "
    'lg=win.lag(col("v")), ld=win.lead(col("f"), offset=2), '
    'cs=win.cum_sum(col("v")), csf=win.cum_sum(col("f")), cc=win.cum_count(), '
    'ccv=win.cum_count(col("f")), cmin=win.cum_min(col("d")), cmax=win.cum_max(col("grp")), '
    'm=win.mean(col("v"), rows={k}), sm=win.sum(col("f") * 2, rows={k})'
)


@SETTINGS
@given(
    rows=row_tables(),
    by=st.sampled_from([[], ["grp"], ["grp", "d"]]),
    order=st.sampled_from([["o"], ["o", "id"], ["d", "o"], ["v", "id"], ["f"]]),
    desc_first=st.booleans(),
    k=st.integers(1, 4),
)
def test_window_sql_matches_polars(
    rows: list[tuple], by: list[str], order: list[str], desc_first: bool, k: int
) -> None:
    desc = f", desc={[order[0]]!r}" if desc_first else ""
    funcs = ALL_FUNCS.format(k=k)
    body = f"x = window(r, by={by!r}, order={order!r}{desc}, {funcs})\n".replace("'", '"')
    _run(body, rows, [])


@SETTINGS
@given(
    tree=trees(),
    starts=st.lists(st.integers(1, 30), min_size=0, max_size=3),
    depth=st.integers(1, 6),
    direction=st.sampled_from(["down", "up"]),
)
def test_traverse_sql_matches_polars(
    tree: list[tuple], starts: list[int], depth: int, direction: str
) -> None:
    cond = " | ".join(f'(col("id") == {s})' for s in starts) or 'col("id") < 0'
    body = (
        f"root = where(t, {cond})\n"
        f'x = traverse(t, root, parent="parent_id", child="id", max_depth={depth}, '
        f'direction="{direction}")\n'
    )
    _run(body, [], tree)


@SETTINGS
@given(tree=trees(), depth=st.integers(1, 4))
def test_traverse_then_window_sql_matches_polars(tree: list[tuple], depth: int) -> None:
    body = (
        'root = where(t, is_null(col("parent_id")))\n'
        f'w = traverse(t, root, parent="parent_id", child="id", max_depth={depth})\n'
        'x = window(w, by=["depth"], order=["id"], n=win.row_number(), '
        'up=win.lag(col("parent_id")))\n'
    )
    _run(body, [], tree)


def test_polars_frame_helper_roundtrip() -> None:
    """The harness itself: an empty table on both sides compares equal."""
    _run('x = window(r, order=["id"], n=win.row_number())\n', [], [])
    assert pl.DataFrame().height == 0


def test_api_reads_with_window_and_traverse_are_one_statement() -> None:
    from lattice.api.ir import ReadStep, lower_api
    from lattice.check import check_source
    from tests.api import loops

    check = check_source(loops.PROGRAM, loops.PROFILE)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    plans = {p.name: p for p in lower_api(check.program)}
    for name, plan in plans.items():
        reads = [s for s in plan.steps if isinstance(s, ReadStep)]
        assert len(reads) == 1, name
        text = reads[0].sql.text
        assert ";" not in text
        if name == "running":
            assert text.startswith("WITH ") and "ROW_NUMBER() OVER (PARTITION BY" in text
            assert "ROWS BETWEEN" in text and "RECURSIVE" not in text
        else:
            assert text.startswith("WITH RECURSIVE ") and " UNION " in text
            assert "MIN(" in text
