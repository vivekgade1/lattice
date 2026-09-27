"""Differential: join(l, r, on={"a": "b"}) behaves exactly like renaming r's key
columns to the left names and joining with the list form (polars runtime)."""

from __future__ import annotations

import ast
from pathlib import Path

import polars as pl
import pytest

from bench.datasets import bench_profile, write_fixtures
from lattice.catalog import IMPORTABLE
from lattice.ir import lower
from lattice.runtime import ops
from lattice.runtime.exec import run_source

H = "# lattice: v1\n"
LOADS = (
    'orders = load("orders.csv", schema=Orders)\n'
    'customers = load("customers.csv", schema=Customers)\n'
    'products = load("products.csv", schema=Products)\n'
)


def prog(body: str) -> str:
    used = {n.id for n in ast.walk(ast.parse(body)) if isinstance(n, ast.Name)}
    out = H + f"from lattice.ops import {', '.join(sorted(used & IMPORTABLE))}\n"
    return out + "from lattice.schemas import Customers, Orders, Products\n" + body


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("mapped")
    write_fixtures(path)
    return path


def output(body: str, root: Path) -> pl.DataFrame:
    result, rr = run_source(prog(LOADS + body + 'write(x, "out.parquet")\n'), bench_profile(), root)
    assert result.ok, [d.render() for d in result.diagnostics]
    assert rr is not None
    return rr.outputs["out.parquet"]


CASES = {
    "left": (
        'c = rename(customers, customer_id="cid")\n'
        'x = join(orders, c, on={"customer_id": "cid"}, how="left")\n',
        'x = join(orders, customers, on=["customer_id"], how="left")\n',
    ),
    "inner": (
        'c = rename(customers, customer_id="cid")\n'
        'x = join(orders, c, on={"customer_id": "cid"}, how="inner")\n',
        'x = join(orders, customers, on=["customer_id"], how="inner")\n',
    ),
    "aggregate_child_to_parent": (
        # the smoke-run shape: per-parent totals keyed by a differently-named column
        'totals = group(orders, by=["customer_id"], units=agg.sum(col("qty")))\n'
        'c = rename(customers, customer_id="id")\n'
        'x = join(c, totals, on={"id": "customer_id"}, how="left")\n',
        'totals = group(orders, by=["customer_id"], units=agg.sum(col("qty")))\n'
        't2 = rename(totals, customer_id="id")\n'
        'c = rename(customers, customer_id="id")\n'
        'x = join(c, t2, on=["id"], how="left")\n',
    ),
    "two_keys_mixed": (
        'c = rename(customers, customer_id="cid")\n'
        'c2 = derive(c, pid=col("cid") % 7)\n'
        'o2 = derive(orders, product_id=col("product_id") % 7)\n'
        'x = join(o2, c2, on={"customer_id": "cid", "product_id": "pid"}, how="left", '
        'fanout="allow")\n',
        'c = rename(customers, customer_id="cid")\n'
        'c2 = derive(c, pid=col("cid") % 7)\n'
        'c3 = rename(c2, cid="customer_id", pid="product_id")\n'
        'o2 = derive(orders, product_id=col("product_id") % 7)\n'
        'x = join(o2, c3, on=["customer_id", "product_id"], how="left", fanout="allow")\n',
    ),
    "fanout": (
        'o = select(orders, cols=["customer_id", "qty"])\n'
        'o2 = rename(o, customer_id="who")\n'
        'x = join(customers, o2, on={"customer_id": "who"}, how="left", fanout="allow")\n',
        'o = select(orders, cols=["customer_id", "qty"])\n'
        'x = join(customers, o, on=["customer_id"], how="left", fanout="allow")\n',
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_dict_join_matches_rename_and_list_join(name: str, root: Path) -> None:
    mapped, listed = CASES[name]
    got, want = output(mapped, root), output(listed, root)
    assert got.height > 0
    assert got.equals(want), f"\n{got}\n{want}"


def test_dict_join_lowers_right_on(root: Path) -> None:
    from lattice.check import check_source

    body = 'c = rename(customers, customer_id="cid")\n'
    body += 'x = join(orders, c, on={"customer_id": "cid"})\nwrite(x, "out.parquet")\n'
    program = check_source(prog(LOADS + body), bench_profile()).program
    assert program is not None
    (node,) = [n for n in lower(program).nodes if n.op == "join"]
    assert node.params["on"] == ("customer_id",)
    assert node.params["right_on"] == ("cid",)
    # a list form (and a dict pairing equal names) lowers exactly as before
    same = LOADS + 'x = join(orders, customers, on={"customer_id": "customer_id"})\n'
    program = check_source(prog(same + 'write(x, "out.parquet")\n'), bench_profile()).program
    assert program is not None
    (node,) = [n for n in lower(program).nodes if n.op == "join"]
    assert node.params == {"on": ("customer_id",), "how": "inner"}


def test_join_size_with_right_on() -> None:
    left = pl.DataFrame({"id": [1, 2, 3, None]})
    right = pl.DataFrame({"order_id": [1, 1, 2, None], "v": [1, 2, 3, 4]})
    for how in ("inner", "left"):
        joined = ops.join(left, right, ["id"], how, right_on=["order_id"])
        assert joined.columns == ["id", "v"]
        assert ops.join_size(left, right, ["id"], how, right_on=["order_id"]) == joined.height
    assert ops.join_size(left, right, ["id"], "inner", right_on=["order_id"]) == 3
    assert ops.join_size(left, right, ["id"], "left", right_on=["order_id"]) == 5
