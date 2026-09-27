"""Semantics of `window(...)` + `win.*` and `traverse(...)` (spec 2026-09-27 lattice-loops
§3.1, §3.2), on both targets: every program runs through the polars executor over CSVs
and through the compiled SQL (one statement) over the same rows in SQLite; the two
frames must be equal, and equal to the expected values.

window: ties, nulls sort last, partition edges, lag/lead past the edge give null, a
moving frame is shorter at the start, cum_* skip nulls.
traverse: a cycle, a diamond (each node once, at its minimum depth), the max_depth
cut-off, the `up` direction, output order (depth, key).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import polars as pl
import pytest

from bench.compare import frames_match
from lattice.api.sql import compile_select, rels_from_plan
from lattice.ir import lower
from lattice.profile import Profile
from lattice.runtime.exec import run_source
from lattice.typesys import Scalar, Schema
from tests.api.test_sql import _write_csv, sql_frame

S = Scalar

SALES = Schema.from_pairs(
    [("id", S.INT), ("region", S.STR), ("day", S.INT), ("amount", S.INT), ("price", S.FLOAT)],
    keys=[("id",)],
    nullable=["region", "day", "amount", "price"],
)
SALES_ROWS = [
    (1, "a", 3, 10, 1.0),
    (2, "a", 1, 20, None),
    (3, "b", 2, 30, 2.0),
    (4, "a", None, 40, 3.0),
    (5, "a", 3, None, 4.0),
    (6, "b", 1, 60, None),
    (7, None, 5, 70, 5.0),
    (8, "a", 2, 80, 6.0),
]
TREE = Schema.from_pairs(
    [("id", S.INT), ("parent_id", S.INT), ("name", S.STR)],
    keys=[("id",)],
    nullable=["parent_id"],
)
# 1 -> {2, 3}; 2 -> 4; 3 -> 5; 4 -> {6, 7}; 7 -> 8;  10 -> 11 -> 12 -> 10 is a cycle
TREE_ROWS = [
    (1, None, "root"),
    (2, 1, "b"),
    (3, 1, "c"),
    (4, 2, "d"),
    (5, 3, "e"),
    (6, 4, "f"),
    (7, 4, "g"),
    (8, 7, "h"),
    (10, 12, "x"),
    (11, 10, "y"),
    (12, 11, "z"),
    (13, None, "alone"),
]
PROFILE = Profile(schemas={"Sales": SALES, "Tree": TREE})
TABLES = {"sales.csv": ("sales", SALES), "tree.csv": ("tree", TREE)}


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, sqlite3.Connection]:
    root = tmp_path_factory.mktemp("loops")
    _write_csv(root / "sales.csv", SALES, SALES_ROWS)
    _write_csv(root / "tree.csv", TREE, TREE_ROWS)
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE sales (id INTEGER PRIMARY KEY, region TEXT, day INTEGER, "
        "amount INTEGER, price REAL)"
    )
    conn.execute("CREATE TABLE tree (id INTEGER PRIMARY KEY, parent_id INTEGER, name TEXT)")
    conn.executemany("INSERT INTO sales VALUES (?, ?, ?, ?, ?)", SALES_ROWS)
    conn.executemany("INSERT INTO tree VALUES (?, ?, ?)", TREE_ROWS)
    return root, conn


def both(body: str, data: tuple[Path, sqlite3.Connection]) -> pl.DataFrame:
    """Run `body` (its result bound to `x`) with polars and as one SQL statement; the
    frames must match exactly (in order). Returns the polars frame."""
    root, conn = data
    src = (
        "# lattice: v1\n"
        's = load("sales.csv", schema=Sales)\n'
        't = load("tree.csv", schema=Tree)\n' + body + 'write(x, "out.csv")\n'
    )
    check, run = run_source(src, PROFILE, root)
    assert check.program is not None and run is not None, [d.render() for d in check.diagnostics]
    expected = run.outputs["out.csv"]
    plan = lower(check.program)
    written = next(n for n in plan.nodes if n.op == "write")
    rels = rels_from_plan(plan.nodes, TABLES)
    query = compile_select(rels, written.inputs[0])
    assert ";" not in query.text  # one statement
    actual = sql_frame(conn, rels, written.inputs[0])
    ok, why = frames_match(actual, expected, ordered=True, rtol=0, atol=1e-12)
    assert ok, f"{why}\nsql:\n{actual}\npolars:\n{expected}\n{query.text}"
    return expected


def col(df: pl.DataFrame, name: str) -> list[object]:
    return df[name].to_list()


# ---- window ---------------------------------------------------------------------------


def test_window_keeps_the_input_row_order(data) -> None:
    df = both('x = window(s, order=["amount", "id"], n=win.row_number())\n', data)
    assert col(df, "id") == [1, 2, 3, 4, 5, 6, 7, 8]
    # amount ascending, nulls last: 10, 20, 30, 40, 60, 70, 80, null
    assert col(df, "n") == [1, 2, 3, 4, 8, 5, 6, 7]


def test_window_partitions_and_nulls_sort_last(data) -> None:
    df = both(
        'x = window(s, by=["region"], order=["day", "id"], n=win.row_number(),\n'
        '    prev=win.lag(col("amount")), nxt=win.lead(col("amount"), offset=2))\n',
        data,
    )
    # region a in (day, id) order, null day last: 2 (d1), 8 (d2), 1 (d3), 5 (d3), 4 (null)
    # region b: 6 (d1), 3 (d2); region null: 7
    assert col(df, "n") == [3, 1, 2, 5, 4, 1, 1, 2]
    # lag past the partition start is null; lag of a null amount is null
    assert col(df, "prev") == [80, None, 60, None, 10, None, None, 20]
    # lead by 2 past the partition end is null
    assert col(df, "nxt") == [40, 10, None, None, None, None, None, None]


def test_window_desc_orders_descending_with_nulls_still_last(data) -> None:
    df = both(
        'x = window(s, by=["region"], order=["day", "id"], desc=["day"], n=win.row_number())\n',
        data,
    )
    # region a: day desc (3: ids 1, 5), 2 (8), 1 (2), null (4)
    assert col(df, "n") == [1, 4, 1, 5, 2, 2, 1, 3]


def test_window_rankings_share_ranks_on_ties(data) -> None:
    df = both(
        'x = window(s, by=["region"], order=["day"], r=win.rank(), dr=win.dense_rank())\n',
        data,
    )
    # region a: days 3, 1, null, 3, 2 -> ranks by day with null last
    # sorted: 1 (id 2), 2 (id 8), 3 (ids 1, 5), null (id 4)
    assert col(df, "r") == [3, 1, 2, 5, 3, 1, 1, 2]
    assert col(df, "dr") == [3, 1, 2, 4, 3, 1, 1, 2]


def test_window_ties_in_order_are_broken_by_input_order(data) -> None:
    """With ties (a warning, L431), row_number follows the input order of tied rows on
    both targets (a stable sort)."""
    df = both('x = window(s, by=["region"], order=["day"], n=win.row_number())\n', data)
    assert col(df, "n") == [3, 1, 2, 5, 4, 1, 1, 2]


def test_window_cumulative_functions_skip_nulls(data) -> None:
    df = both(
        'x = window(s, order=["id"], run=win.cum_sum(col("amount")),\n'
        '    runp=win.cum_sum(col("price")), rows=win.cum_count(),\n'
        '    seen=win.cum_count(col("price")), lo=win.cum_min(col("price")),\n'
        '    hi=win.cum_max(col("region")))\n',
        data,
    )
    assert col(df, "run") == [10, 30, 60, 100, 100, 160, 230, 310]
    assert col(df, "runp") == [1.0, 1.0, 3.0, 6.0, 10.0, 10.0, 15.0, 21.0]
    assert col(df, "rows") == [1, 2, 3, 4, 5, 6, 7, 8]
    assert col(df, "seen") == [1, 1, 2, 3, 4, 4, 5, 6]
    assert col(df, "lo") == [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    assert col(df, "hi") == ["a", "a", "b", "b", "b", "b", "b", "b"]


def test_window_cum_min_is_null_until_the_first_value(data) -> None:
    df = both('x = window(s, order=["id"], desc=["id"], lo=win.cum_min(col("price")))\n', data)
    # descending id: 8 (6.0), 7 (5.0), 6 (null), 5 (4.0), 4 (3.0), 3 (2.0), 2 (null), 1 (1.0)
    assert col(df, "lo") == [1.0, 2.0, 2.0, 3.0, 4.0, 5.0, 5.0, 6.0]
    df = both(
        'x = window(s, by=["region"], order=["day", "id"], lo=win.cum_min(col("price")))\n',
        data,
    )
    # region b starts with id 6, whose price is null
    assert col(df, "lo") == [1.0, None, 2.0, 1.0, 1.0, None, 5.0, 6.0]
    df = both('x = window(s, order=["id"], lo=win.cum_min(col("day")))\n', data)
    assert col(df, "lo") == [3, 1, 1, 1, 1, 1, 1, 1]
    df = both('x = window(s, order=["id"], desc=["id"], lo=win.cum_max(col("region")))\n', data)
    # 8 a, 7 null, 6 b, ...
    assert col(df, "lo") == ["b"] * 6 + ["a", "a"]


def test_window_moving_frame_is_shorter_at_the_start(data) -> None:
    df = both(
        'x = window(s, by=["region"], order=["id"], m=win.mean(col("amount"), rows=2),\n'
        '    t=win.sum(col("price"), rows=3))\n',
        data,
    )
    # region a in id order: 1 (10), 2 (20), 4 (40), 5 (null), 8 (80)
    assert col(df, "m") == [10.0, 15.0, 30.0, 30.0, 40.0, 45.0, 70.0, 80.0]
    # prices a: 1.0, null, 3.0, 4.0, 6.0 -> 1.0, 1.0, 4.0, 7.0, 13.0; b: 2.0, 2.0; null: 5.0
    assert col(df, "t") == [1.0, 1.0, 2.0, 4.0, 7.0, 2.0, 5.0, 13.0]


def test_window_moving_mean_of_only_nulls_is_null(data) -> None:
    df = both('x = window(s, order=["id"], m=win.mean(col("price"), rows=1))\n', data)
    assert col(df, "m") == [1.0, None, 2.0, 3.0, 4.0, None, 5.0, 6.0]
    df = both('x = window(s, order=["id"], m=win.sum(col("price"), rows=1))\n', data)
    assert col(df, "m") == [1.0, 0.0, 2.0, 3.0, 4.0, 0.0, 5.0, 6.0]


def test_window_on_an_expression_and_whole_table(data) -> None:
    df = both('x = window(s, order=["id"], d=win.lag(col("amount") * 2, offset=3))\n', data)
    assert col(df, "d") == [None, None, None, 20, 40, 60, 80, None]


def test_window_over_a_window(data) -> None:
    df = both(
        'w = window(s, order=["id"], n=win.row_number())\n'
        'x = window(w, by=["region"], order=["n"], prev=win.lag(col("n")))\n',
        data,
    )
    assert col(df, "prev") == [None, 1, None, 2, 4, 3, None, 5]


# ---- traverse -------------------------------------------------------------------------


def walk(start: str, direction: str = "down", depth: int = 10) -> str:
    return (
        f"root = where(t, {start})\n"
        f'x = traverse(t, root, parent="parent_id", child="id", max_depth={depth}, '
        f'direction="{direction}")\n'
    )


def test_traverse_down_in_depth_then_key_order(data) -> None:
    df = both(walk('col("id") == 1'), data)
    assert col(df, "id") == [1, 2, 3, 4, 5, 6, 7, 8]
    assert col(df, "depth") == [0, 1, 1, 2, 2, 3, 3, 4]
    assert df.columns == ["id", "parent_id", "name", "depth"]


def test_traverse_max_depth_cuts_off(data) -> None:
    df = both(walk('col("id") == 1', depth=2), data)
    assert col(df, "id") == [1, 2, 3, 4, 5]
    assert col(df, "depth") == [0, 1, 1, 2, 2]
    df = both(walk('col("id") == 1', depth=1), data)
    assert col(df, "id") == [1, 2, 3]


def test_traverse_cycle_visits_each_node_once(data) -> None:
    df = both(walk('col("id") == 11', depth=1000), data)
    assert col(df, "id") == [11, 12, 10]
    assert col(df, "depth") == [0, 1, 2]
    df = both(walk('col("id") == 11', direction="up", depth=1000), data)
    assert col(df, "id") == [11, 10, 12]
    assert col(df, "depth") == [0, 1, 2]


def test_traverse_diamond_keeps_the_minimum_depth(data) -> None:
    # start at 1 and 4: 6/7 are 3 steps from 1 but 1 step from 4
    df = both(walk('(col("id") == 1) | (col("id") == 4)'), data)
    assert col(df, "id") == [1, 4, 2, 3, 6, 7, 5, 8]
    assert col(df, "depth") == [0, 0, 1, 1, 1, 1, 2, 2]


def test_traverse_up_walks_to_the_ancestors(data) -> None:
    df = both(walk('col("id") == 8', direction="up"), data)
    assert col(df, "id") == [8, 7, 4, 2, 1]
    assert col(df, "depth") == [0, 1, 2, 3, 4]
    # a diamond upwards: 6 and 3 both reach 1 (at depth 3 and 1)
    df = both(walk('(col("id") == 6) | (col("id") == 3)', direction="up"), data)
    assert col(df, "id") == [3, 6, 1, 4, 2]
    assert col(df, "depth") == [0, 0, 1, 1, 2]


def test_traverse_empty_start_and_leaves(data) -> None:
    df = both(walk('col("id") == 99'), data)
    assert df.height == 0
    df = both(walk('col("id") == 13'), data)
    assert col(df, "id") == [13] and col(df, "depth") == [0]


def test_traverse_start_is_any_table_with_the_child_column(data) -> None:
    df = both(
        'ids = select(t, cols=["id"])\n'
        'top = where(ids, col("id") <= 2)\n'
        'x = traverse(t, top, parent="parent_id", child="id", max_depth=1)\n',
        data,
    )
    assert col(df, "id") == [1, 2, 3, 4]
    assert col(df, "depth") == [0, 0, 1, 1]


def test_traverse_then_ops(data) -> None:
    df = both(
        walk('col("id") == 2').replace("x = ", "w = ")
        + 'x = window(w, by=["depth"], order=["id"], n=win.row_number())\n',
        data,
    )
    assert col(df, "n") == [1, 1, 1, 2, 1]
