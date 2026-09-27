"""SQL compilation: every table op and expression of the API subset, run through the
tabular (polars) runtime over CSVs and through the compiled SQL over the same data in
SQLite; the frames must match, in order."""

from __future__ import annotations

import ast
import datetime as dt
import sqlite3
from pathlib import Path

import polars as pl
import pytest

from bench.api_datasets import _TABLES as API_TABLES
from bench.api_datasets import build_db
from bench.compare import frames_match
from lattice.api.sql import (
    ParamRef,
    Rel,
    bind_values,
    compile_select,
    decode,
    register_functions,
    rels_from_plan,
)
from lattice.catalog import IMPORTABLE
from lattice.ir import lower
from lattice.profile import Profile
from lattice.runtime.exec import run_source
from lattice.runtime.exprs import DTYPES
from lattice.typesys import Scalar, Schema

S = Scalar

ITEMS = Schema.from_pairs(
    [
        ("id", S.INT),
        ("name", S.STR),
        ("grp", S.STR),
        ("qty", S.INT),
        ("price", S.FLOAT),
        ("day", S.DATE),
        ("flag", S.BOOL),
    ],
    keys=[("id",)],
)
TAGS = Schema.from_pairs([("id", S.INT), ("tag", S.STR), ("weight", S.INT)])
ITEM_ROWS = [
    (1, "apple", "a", 3, 1.5, "2024-01-02", True),
    (2, "Pear", "b", 0, 2.0, "2024-02-03", False),
    (3, "fig", "a", 5, None, "2024-03-04", True),
    (4, None, "b", None, 4.0, None, None),
    (5, "apple", None, 2, 1.0, "2024-01-05", False),
    (6, "ÄPFEL", None, -7, -2.5, "2023-12-31", True),
    (7, " kiwi ", "c", 7, 0.0, "2024-02-29", None),
    (8, "fig", "a", 3, 1.5, "2024-01-02", False),
]
TAG_ROWS = [
    (3, "x", 1),
    (1, "y", 2),
    (1, "z", None),
    (9, "w", 4),
    (3, "v", 5),
]


def _csv_cell(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _write_csv(path: Path, schema: Schema, rows: list[tuple]) -> None:
    lines = [",".join(schema.names())]
    lines += [",".join(_csv_cell(v) for v in r) for r in rows]
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, sqlite3.Connection]:
    root = tmp_path_factory.mktemp("sqldiff")
    _write_csv(root / "items.csv", ITEMS, ITEM_ROWS)
    _write_csv(root / "tags.csv", TAGS, TAG_ROWS)
    conn = sqlite3.connect(str(root / "db.sqlite"))
    register_functions(conn)
    conn.execute(
        "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, grp TEXT, qty INTEGER, "
        "price REAL, day TEXT, flag INTEGER)"
    )
    conn.execute("CREATE TABLE tags (id INTEGER, tag TEXT, weight INTEGER)")
    conn.executemany("INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?)", ITEM_ROWS)
    conn.executemany("INSERT INTO tags VALUES (?, ?, ?)", TAG_ROWS)
    conn.commit()
    return root, conn


PROFILE = Profile(schemas={"Items": ITEMS, "Tags": TAGS})
TABLES = {"items.csv": ("items", ITEMS), "tags.csv": ("tags", TAGS)}


def program(body: str, profile: Profile = PROFILE) -> str:
    used = {n.id for n in ast.walk(ast.parse(body)) if isinstance(n, ast.Name)}
    ops = sorted(used & IMPORTABLE)
    schemas = sorted(used & set(profile.schemas))
    out = "# lattice: v1\n"
    if ops:
        out += f"from lattice.ops import {', '.join(ops)}\n"
    if schemas:
        out += f"from lattice.schemas import {', '.join(schemas)}\n"
    return out + body


def sql_frame(conn: sqlite3.Connection, rels: dict[str, Rel], target: str) -> pl.DataFrame:
    query = compile_select(rels, target)
    rows = conn.execute(query.text, bind_values(query)).fetchall()
    schema = rels[target].schema
    cols: dict[str, list[object]] = {}
    for i, (name, scalar) in enumerate(schema.columns):
        values = [decode(r[i], scalar) for r in rows]
        if scalar is S.DATE:
            values = [None if v is None else dt.date.fromisoformat(str(v)) for v in values]
        cols[name] = values
    return pl.DataFrame(cols, schema={n: DTYPES[s] for n, s in schema.columns})


def differential(
    body: str,
    root: Path,
    conn: sqlite3.Connection,
    profile: Profile = PROFILE,
    tables: dict = TABLES,
) -> pl.DataFrame:
    """Run `body` (ending in write(<name>, "out.csv")) both ways; return the frame."""
    src = program(body, profile)
    check, run = run_source(src, profile, root)
    assert check.ok, [d.render() for d in check.diagnostics]
    assert run is not None
    expected = run.outputs["out.csv"]
    plan = lower(check.program)
    written = next(n for n in plan.nodes if n.op == "write")
    rels = rels_from_plan(plan.nodes, tables)
    actual = sql_frame(conn, rels, written.inputs[0])
    ok, why = frames_match(actual, expected, ordered=True)
    assert ok, f"{why}\nsql:\n{actual}\npolars:\n{expected}"
    return actual


LOAD = 't = load("items.csv", schema=Items)\n'
TAGS_LOAD = 'g = load("tags.csv", schema=Tags)\n'

CASES = {
    "fetch_order": 'x = select(t, cols=["id", "name", "day", "flag"])',
    "where_cmp": 'x = where(t, col("qty") > 2)',
    "where_null_logic": 'x = where(t, (col("qty") > 0) | ~col("flag"))',
    "where_and": 'x = where(t, (col("qty") >= 0) & (col("price") < 2.0))',
    "where_ne_str": 'x = where(t, col("name") != "apple")',
    "where_date": 'x = where(t, col("day") > col("day"))',
    "where_bool_col": 'x = where(t, col("flag"))',
    "derive_arith": (
        'x = derive(t, a=col("qty") * 2 + 1, b=col("price") / col("qty"), '
        'c=col("qty") % 3, d=-col("price"), e=col("qty") - col("id"), f=col("qty") % -4, '
        'h=col("price") % 2)'
    ),
    "derive_div_int": 'x = derive(t, r=col("qty") / col("id"), z=col("id") / 0)',
    "derive_replace": 'x = derive(t, qty=col("qty") + 1, name=fn.upper(col("name")))',
    "derive_fns": (
        'x = derive(t, lo=fn.lower(col("name")), up=fn.upper(col("name")), '
        'st=fn.strip(col("name")), n=fn.len(col("name")), '
        'has=fn.contains(col("name"), sub="pp"), pre=fn.starts_with(col("name"), prefix="ap"), '
        'y=fn.year(col("day")), m=fn.month(col("day")), d=fn.day(col("day")))'
    ),
    "derive_empty_affixes": (
        'x = derive(t, a=fn.contains(col("name"), sub=""), '
        'b=fn.starts_with(col("name"), prefix=""))'
    ),
    "derive_when_coalesce": (
        'x = derive(t, w=when(col("qty") > 2, then=col("price"), otherwise=0.5), '
        'c=coalesce(col("price"), -1.0), n=is_null(col("name")), '
        'k=when(col("flag"), then="yes", otherwise="no"))'
    ),
    "derive_bool": 'x = derive(t, b=(col("qty") > 1) & col("flag"), nb=~col("flag"))',
    "select_rename": ('s = select(t, cols=["name", "id"])\nx = rename(s, name="label", id="key")'),
    "rename_swap": 'x = rename(t, name="grp", grp="name")',
    "group_all_aggs": (
        'x = group(t, by=["grp"], total=agg.sum(col("qty")), n=agg.count(), '
        'nq=agg.count(col("qty")), nd=agg.count_distinct(col("name")), '
        'lo=agg.min(col("price")), hi=agg.max(col("name")), avg=agg.mean(col("price")), '
        'fs=agg.sum(col("price")))'
    ),
    "group_two_keys": 'x = group(t, by=["grp", "flag"], n=agg.count(), s=agg.sum(col("qty")))',
    "group_literal": 'x = group(t, by=["grp"], ones=agg.sum(1), c1=agg.count(1))',
    "group_whole": (
        'x = group(t, by=[], total=agg.sum(col("qty")), n=agg.count(), avg=agg.mean(col("qty")))'
    ),
    "group_empty": (
        'e = where(t, col("id") > 100)\n'
        'x = group(e, by=[], total=agg.sum(col("qty")), n=agg.count(), '
        'fs=agg.sum(col("price")), avg=agg.mean(col("qty")), lo=agg.min(col("qty")))'
    ),
    "group_all_null_sum": (
        'e = where(t, col("id") == 4)\nx = group(e, by=["grp"], s=agg.sum(col("qty")))'
    ),
    "group_at": (
        'x = group(t, by=["grp"], cheapest=agg.at_min(col("name"), of=col("price")), '
        'latest=agg.at_max(col("id"), of=col("day")), '
        'tie=agg.at_max(col("id"), of=col("qty")))'
    ),
    "group_at_whole": (
        'x = group(t, by=[], first=agg.at_min(col("id"), of=col("day")), '
        'last=agg.at_max(col("name"), of=col("qty")))'
    ),
    "group_expr_arg": (
        'x = group(t, by=["grp"], v=agg.sum(col("qty") * col("price")), '
        'm=agg.max(when(col("flag"), then=col("qty"), otherwise=0)))'
    ),
    "sort_asc_nulls_last": 'x = sort(t, by=["qty"])',
    "sort_desc_nulls_last": 'x = sort(t, by=["price"], desc=True)',
    "sort_mixed_stable": 'x = sort(t, by=["grp", "name"], desc=[True, False])',
    "sort_str_unicode": 'x = sort(t, by=["name"])',
    "sort_bool": 'x = sort(t, by=["flag"], desc=True)',
    "sort_then_limit": 's = sort(t, by=["price"], desc=True)\nx = limit(s, n=3)',
    "limit_no_sort": "x = limit(t, n=2)",
    "join_inner_fanout": (TAGS_LOAD + 'x = join(t, g, on=["id"], how="inner", fanout="allow")'),
    "join_left_fanout": TAGS_LOAD + 'x = join(t, g, on=["id"], how="left", fanout="allow")',
    "join_right_first": (TAGS_LOAD + 'x = join(g, t, on=["id"], how="left")'),
    "join_null_keys": (
        'a = select(t, cols=["grp", "id"])\n'
        'b0 = group(t, by=["grp"], n=agg.count())\n'
        'x = join(a, b0, on=["grp"], how="left")'
    ),
    "join_mapped_left": (
        TAGS_LOAD + 'k = rename(t, id="item_id")\nx = join(g, k, on={"id": "item_id"}, how="left")'
    ),
    "join_mapped_inner_fanout": (
        TAGS_LOAD + 'h = rename(g, id="item_id")\n'
        'x = join(t, h, on={"id": "item_id"}, how="inner", fanout="allow")'
    ),
    "join_mapped_per_parent_totals": (
        TAGS_LOAD + 'h = rename(g, id="item_id")\n'
        'w = group(h, by=["item_id"], weight=agg.sum(col("weight")), n=agg.count())\n'
        'x = join(t, w, on={"id": "item_id"}, how="left")'
    ),
    "join_mapped_two_keys_null": (
        'a = select(t, cols=["id", "grp", "name"])\n'
        'b0 = group(t, by=["grp", "id"], n=agg.count())\n'
        'b = rename(b0, grp="g2", id="i2")\n'
        'x = join(a, b, on={"grp": "g2", "id": "i2"}, how="left")'
    ),
    "join_cross_one_row": (
        'tot = group(t, by=[], all_qty=agg.sum(col("qty")))\n'
        'j = join(t, tot, on=[], how="inner")\n'
        'x = derive(j, share=col("qty") / col("all_qty"))'
    ),
    "join_cross_empty_left": (
        'e = where(t, col("id") > 100)\n'
        'z = derive(e, z=col("qty"))\n'
        'zs = select(z, cols=["z"])\n'
        "one_row = limit(zs, n=1)\n"
        'x = join(t, one_row, on=[], how="left")'
    ),
    "chain": (
        'd = derive(t, value=col("qty") * col("price"))\n'
        'w = where(d, col("value") > 0)\n'
        'gr = group(w, by=["name"], total=agg.sum(col("value")), n=agg.count())\n'
        's = sort(gr, by=["total", "name"], desc=[True, False])\n'
        "x = limit(s, n=3)"
    ),
    "sort_after_join_then_group": (
        TAGS_LOAD + 'j = join(g, t, on=["id"], how="inner")\n'
        'gr = group(j, by=["tag"], w=agg.sum(col("weight")), q=agg.max(col("qty")))\n'
        'x = sort(gr, by=["w"], desc=True)'
    ),
    "constants": (
        "cut = 2\nfactor = cut * 1.5\n"
        'x = derive(where(t, col("qty") > cut), scaled=col("qty") * factor)'
    ),
    "step_inlined": (
        "@step\ndef big(r: Table[Items], at_least: Int) -> Table[Items]:\n"
        '    out = where(r, col("qty") >= at_least)\n'
        "    return out\n"
        "x = big(t, at_least=3)"
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_differential(name: str, data: tuple[Path, sqlite3.Connection]) -> None:
    root, conn = data
    body = CASES[name]
    if name == "constants":  # canonical form: no nested calls
        body = (
            "cut = 2\nfactor = cut * 1.5\n"
            'w = where(t, col("qty") > cut)\n'
            'x = derive(w, scaled=col("qty") * factor)'
        )
    differential(LOAD + body + '\nwrite(x, "out.csv")\n', root, conn)


def test_differential_cases_return_rows(data: tuple[Path, sqlite3.Connection]) -> None:
    """Guard against vacuous comparisons: key cases produce the expected shapes."""
    root, conn = data
    df = differential(LOAD + 'x = sort(t, by=["qty"])\nwrite(x, "out.csv")\n', root, conn)
    assert df["id"].to_list() == [6, 2, 5, 1, 8, 3, 7, 4]  # stable ties (1, 8), null last
    df = differential(
        LOAD + 'x = group(t, by=["grp"], n=agg.count())\nwrite(x, "out.csv")\n', root, conn
    )
    assert df.rows() == [("a", 3), ("b", 2), (None, 2), ("c", 1)]


def test_mapped_join_sql_pairs_left_and_right_names(data: tuple[Path, sqlite3.Connection]) -> None:
    root, conn = data
    body = LOAD + CASES["join_mapped_left"] + '\nwrite(x, "out.csv")\n'
    df = differential(body, root, conn)
    assert df.columns == ["id", "tag", "weight", "name", "grp", "qty", "price", "day", "flag"]
    assert df.height == len(TAG_ROWS)
    check, _ = run_source(program(body), PROFILE, root)
    assert check.program is not None
    plan = lower(check.program)
    join = next(n for n in plan.nodes if n.op == "join")
    rels = rels_from_plan(plan.nodes, TABLES)
    text = compile_select(rels, join.id).text
    assert 'ON l."id" = r."item_id"' in text


# ---- the benchmark database ------------------------------------------------------------


@pytest.fixture(scope="module")
def commerce(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("commerce")
    db = root / "shop.db"
    build_db(db)
    conn = sqlite3.connect(str(db))
    register_functions(conn)
    schemas: dict[str, Schema] = {}
    tables: dict[str, tuple[str, Schema]] = {}
    for name, sql_name, schema in API_TABLES:
        cols = ", ".join(schema.names())
        rows = conn.execute(f"SELECT {cols} FROM {sql_name} ORDER BY id").fetchall()
        cells = {n: [r[i] for r in rows] for i, n in enumerate(schema.names())}
        frame = pl.DataFrame(cells, schema={n: DTYPES[s] for n, s in schema.columns}, strict=False)
        frame.write_csv(root / f"{sql_name}.csv")
        plain = Schema.from_pairs(schema.columns, keys=schema.keys)
        schemas[name] = plain
        tables[f"{sql_name}.csv"] = (sql_name, plain)
    return root, conn, Profile(schemas=schemas), tables


COMMERCE = {
    "revenue_by_status": (
        'o = load("orders.csv", schema=Orders)\n'
        'x = group(o, by=["status"], revenue=agg.sum(col("total")), n=agg.count(), '
        'avg=agg.mean(col("total")), first=agg.min(col("created_at")))'
    ),
    "items_with_products": (
        'i = load("order_items.csv", schema=OrderItems)\n'
        'p = load("products.csv", schema=Products)\n'
        'pr = rename(p, id="product_id")\n'
        'j = join(i, pr, on=["product_id"], how="inner")\n'
        'l = derive(j, line=col("qty") * col("unit_price"))\n'
        'gr = group(l, by=["category"], sales=agg.sum(col("line")), '
        'orders=agg.count_distinct(col("order_id")), top=agg.at_max(col("product_id"), '
        'of=col("line")))\n'
        'x = sort(gr, by=["sales"], desc=True)'
    ),
    "monthly": (
        'o = load("orders.csv", schema=Orders)\n'
        'm = derive(o, y=fn.year(col("created_at")), mo=fn.month(col("created_at")))\n'
        'gr = group(m, by=["y", "mo"], n=agg.count(), t=agg.sum(col("total")))\n'
        'x = sort(gr, by=["y", "mo"])'
    ),
    "top_customers": (
        'o = load("orders.csv", schema=Orders)\n'
        'u = load("users.csv", schema=Users)\n'
        'paid = where(o, col("status") != "cancelled")\n'
        'gr = group(paid, by=["customer_id"], spent=agg.sum(col("total")))\n'
        'r = rename(u, id="customer_id")\n'
        'names = select(r, cols=["customer_id", "name"])\n'
        'j = join(gr, names, on=["customer_id"], how="left")\n'
        's = sort(j, by=["spent", "customer_id"], desc=[True, False])\n'
        "x = limit(s, n=10)"
    ),
    "active_products": (
        'p = load("products.csv", schema=Products)\n'
        'a = where(p, col("active") & (col("stock") > 0))\n'
        'x = derive(a, low=fn.lower(col("name")), pricey=col("price") >= 50.0)'
    ),
}


@pytest.mark.parametrize("name", sorted(COMMERCE))
def test_differential_commerce(name: str, commerce) -> None:
    root, conn, profile, tables = commerce
    df = differential(COMMERCE[name] + '\nwrite(x, "out.csv")\n', root, conn, profile, tables)
    assert df.height > 0


# ---- API ops and bindings ---------------------------------------------------------------


def _orders_rels() -> dict[str, Rel]:
    orders = Schema.from_pairs([("id", S.INT), ("customer_id", S.INT), ("status", S.STR)])
    return {
        "o": Rel("o", "fetch", (), {"table": "orders", "order": ("id",)}, orders),
        "m": Rel("m", "owned", ("o",), {"owner": "customer_id"}, orders),
        "s": Rel("s", "sort", ("m",), {"by": ("status",), "desc": False}, orders),
        "p": Rel(
            "p",
            "page",
            ("s",),
            {"limit": ParamRef("limit"), "offset": ParamRef("offset")},
            orders,
        ),
    }


def test_owned_and_page_bind_user_and_params(commerce) -> None:
    _, conn, _, _ = commerce
    rels = _orders_rels()
    query = compile_select(rels, "p")
    assert query.params == ("limit", "offset")
    assert query.user
    binds = bind_values(query, params={"limit": 5, "offset": 2}, user_id=1)
    rows = conn.execute(query.text, binds).fetchall()
    every = conn.execute(
        "SELECT id, customer_id, status FROM orders WHERE customer_id = 1 ORDER BY status, id"
    ).fetchall()
    assert rows == every[2:7]
    admin = bind_values(query, params={"limit": 100, "offset": 0}, user_id=1, is_admin=True)
    owners = {r[1] for r in conn.execute(query.text, admin).fetchall()}
    assert len(owners) > 1


def test_page_without_sort_orders_by_key(commerce) -> None:
    _, conn, _, _ = commerce
    rels = _orders_rels()
    rels["p"] = Rel("p", "page", ("m",), {"limit": 3, "offset": 1}, rels["m"].schema)
    query = compile_select(rels, "p")
    rows = conn.execute(query.text, bind_values(query, user_id=2)).fetchall()
    ids = [r[0] for r in conn.execute("SELECT id FROM orders WHERE customer_id = 2 ORDER BY id")]
    assert [r[0] for r in rows] == ids[1:4]


def test_literals_are_bound_not_inlined() -> None:
    rels = _orders_rels()
    pred = ast.parse('col("status") == "x\' OR 1=1 --"', mode="eval").body
    rels["w"] = Rel("w", "where", ("o",), {"predicate": pred}, rels["o"].schema)
    query = compile_select(rels, "w")
    assert "OR 1=1" not in query.text
    assert ("__l0", "x' OR 1=1 --") in query.binds


def test_materialized_rows_are_read_from_json(commerce) -> None:
    _, conn, _, _ = commerce
    rels = _orders_rels()
    pred = ast.parse('col("status") == "pending"', mode="eval").body
    rels["w"] = Rel("w", "where", ("o",), {"predicate": pred}, rels["o"].schema)
    cols = ("id", "customer_id", "status")
    query = compile_select(rels, "w", materialized={"o": ("step_o", cols)})
    assert "orders" not in query.text
    results = {"step_o": (cols, [(1, 1, "paid"), (2, 1, "pending"), (3, 2, "pending")])}
    rows = conn.execute(query.text, bind_values(query, results=results)).fetchall()
    assert rows == [(2, 1, "pending"), (3, 2, "pending")]


def test_every_query_is_one_statement(commerce) -> None:
    _, conn, _, _ = commerce
    rels = _orders_rels()
    query = compile_select(rels, "p")
    assert sqlite3.complete_statement(query.text + ";")
    count = [0]
    conn.set_trace_callback(lambda sql: count.__setitem__(0, count[0] + 1))
    try:
        conn.execute(query.text, bind_values(query, params={"limit": 5, "offset": 0}, user_id=1))
    finally:
        conn.set_trace_callback(None)
    assert count[0] == 1


def test_owned_through_a_parent_is_one_statement(commerce) -> None:
    _, conn, _, _ = commerce
    items = Schema.from_pairs([("id", S.INT), ("order_id", S.INT), ("qty", S.INT)])
    rels = {
        "i": Rel("i", "fetch", (), {"table": "order_items", "order": ("id",)}, items),
        "m": Rel("m", "owned", ("i",), {"via": ("order_id", "orders", "id", "customer_id")}, items),
    }
    query = compile_select(rels, "m")
    assert (
        '(:__is_admin OR "order_id" IN (SELECT "id" FROM "orders" WHERE "customer_id" = '
        ":__user_id))" in query.text
    )
    assert sqlite3.complete_statement(query.text + ";")
    mine = conn.execute(query.text, bind_values(query, user_id=1)).fetchall()
    expected = conn.execute(
        "SELECT i.id, i.order_id, i.qty FROM order_items i JOIN orders o ON o.id = i.order_id "
        "WHERE o.customer_id = 1 ORDER BY i.id"
    ).fetchall()
    assert mine == expected and mine
    every = conn.execute(query.text, bind_values(query, user_id=1, is_admin=True)).fetchall()
    assert len(every) == conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0]


def test_mine_keeps_only_the_callers_rows_even_for_an_admin(commerce) -> None:
    _, conn, _, _ = commerce
    rels = _orders_rels()
    rels["m"] = Rel("m", "mine", ("o",), {"owner": "customer_id"}, rels["o"].schema)
    query = compile_select(rels, "m")
    assert query.user and "__is_admin" not in query.text
    expected = conn.execute(
        "SELECT id, customer_id, status FROM orders WHERE customer_id = 1 ORDER BY id"
    ).fetchall()
    assert expected
    for admin in (False, True):
        rows = conn.execute(query.text, bind_values(query, user_id=1, is_admin=admin)).fetchall()
        assert rows == expected


def test_mine_through_a_parent_follows_the_owned_chain(commerce) -> None:
    _, conn, _, _ = commerce
    items = Schema.from_pairs([("id", S.INT), ("order_id", S.INT), ("qty", S.INT)])
    rels = {
        "i": Rel("i", "fetch", (), {"table": "order_items", "order": ("id",)}, items),
        "m": Rel("m", "mine", ("i",), {"via": ("order_id", "orders", "id", "customer_id")}, items),
    }
    query = compile_select(rels, "m")
    assert (
        '"order_id" IN (SELECT "id" FROM "orders" WHERE "customer_id" = :__user_id)'
        in query.text
    )
    assert "__is_admin" not in query.text and sqlite3.complete_statement(query.text + ";")
    expected = conn.execute(
        "SELECT i.id, i.order_id, i.qty FROM order_items i JOIN orders o ON o.id = i.order_id "
        "WHERE o.customer_id = 1 ORDER BY i.id"
    ).fetchall()
    assert expected
    for admin in (False, True):
        rows = conn.execute(query.text, bind_values(query, user_id=1, is_admin=admin)).fetchall()
        assert rows == expected
