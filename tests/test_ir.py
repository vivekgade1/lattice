import ast

import pytest

from bench.datasets import ORDERS, bench_profile
from lattice.check import check_source
from lattice.ir import Node, Plan, lower
from lattice.typesys import Scalar, Schema


def _checked(src: str):
    r = check_source(src, bench_profile())
    assert r.ok, r.diagnostics
    return r.program


LINEAR = """# lattice: v1
from lattice.ops import load, where, derive, group, sort, limit, write, col, agg
from lattice.schemas import Orders
raw = load("orders.csv", schema=Orders)
paid = where(raw, col("status") == "paid")
priced = derive(paid, revenue=col("qty") * col("unit_price"))
by_status = group(priced, by=["customer_id"], revenue=agg.sum(col("revenue")), n=agg.count())
ranked = sort(by_status, by=["revenue"], desc=True)
top = limit(ranked, n=3)
write(top, "out.parquet")
"""


def test_linear_pipeline_nodes_in_order():
    plan = lower(_checked(LINEAR))
    assert isinstance(plan, Plan)
    assert [n.id for n in plan.nodes] == [
        "raw",
        "paid",
        "priced",
        "by_status",
        "ranked",
        "top",
        "_write0",
    ]
    assert [n.op for n in plan.nodes] == [
        "load",
        "where",
        "derive",
        "group",
        "sort",
        "limit",
        "write",
    ]
    by_id = {n.id: n for n in plan.nodes}
    assert by_id["raw"].inputs == ()
    assert by_id["raw"].params == {"path": "orders.csv"}
    assert by_id["raw"].schema == ORDERS
    assert by_id["paid"].inputs == ("raw",)
    assert isinstance(by_id["paid"].params["predicate"], ast.Compare)
    assert by_id["priced"].schema == ORDERS.extend([("revenue", Scalar.FLOAT)])
    cols = by_id["priced"].params["columns"]
    assert [name for name, _ in cols] == ["revenue"]
    group = by_id["by_status"]
    assert group.params["by"] == ("customer_id",)
    aggs = group.params["aggs"]
    assert [(n, f) for n, f, _ in aggs] == [("revenue", "sum"), ("n", "count")]
    assert aggs[1][2] is None
    assert group.schema == Schema.from_pairs(
        [("customer_id", Scalar.INT), ("revenue", Scalar.FLOAT), ("n", Scalar.INT)]
    )
    assert by_id["ranked"].params == {"by": ("revenue",), "desc": True}
    assert by_id["top"].params == {"n": 3}
    w = by_id["_write0"]
    assert w.inputs == ("top",)
    assert w.params == {"path": "out.parquet"}
    assert w.schema is None
    assert by_id["paid"].span.line == 5


def test_select_rename_join_params():
    src = """# lattice: v1
from lattice.ops import load, select, rename, join, write
from lattice.schemas import Orders, Customers
orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
slim = select(customers, cols=["customer_id", "region"])
named = rename(slim, region="cust_region")
joined = join(orders, named, on=["customer_id"], how="left")
inner = join(orders, named, on=["customer_id"])
write(joined, "out.parquet")
write(inner, "inner.csv")
"""
    plan = lower(_checked(src))
    by_id = {n.id: n for n in plan.nodes}
    assert by_id["slim"].params == {"cols": ("customer_id", "region")}
    assert by_id["named"].params == {"mapping": (("region", "cust_region"),)}
    assert by_id["joined"].inputs == ("orders", "named")
    assert by_id["joined"].params == {"on": ("customer_id",), "how": "left"}
    assert by_id["inner"].params == {"on": ("customer_id",), "how": "inner"}
    assert by_id["joined"].schema.names()[-1] == "cust_region"
    assert [n.id for n in plan.nodes if n.op == "write"] == ["_write0", "_write1"]
    assert by_id["_write1"].params == {"path": "inner.csv"}


STEP_TWICE = """# lattice: v1
from lattice.ops import load, where, derive, write, col, step, Table, Int
from lattice.schemas import Orders
@step
def big(orders: Table[Orders], min_qty: Int) -> Table:
    kept = where(orders, col("qty") >= min_qty)
    tagged = derive(kept, bump=col("qty") + min_qty)
    return tagged
raw = load("orders.csv", schema=Orders)
a = big(raw, min_qty=2)
b = big(raw, min_qty=7)
write(a, "a.parquet")
write(b, "b.parquet")
"""


def _constants(node: ast.AST) -> list[object]:
    """Constant values in source order."""
    found = [n for n in ast.walk(node) if isinstance(n, ast.Constant)]
    found.sort(key=lambda n: (n.lineno, n.col_offset))
    return [n.value for n in found]


def test_step_called_twice_gives_two_subgraphs():
    plan = lower(_checked(STEP_TWICE))
    ids = [n.id for n in plan.nodes]
    assert ids == ["raw", "a/kept", "a/tagged", "b/kept", "b/tagged", "_write0", "_write1"]
    by_id = {n.id: n for n in plan.nodes}
    assert by_id["a/kept"].inputs == ("raw",)
    assert by_id["a/tagged"].inputs == ("a/kept",)
    assert by_id["b/kept"].inputs == ("raw",)
    assert _constants(by_id["a/kept"].params["predicate"]) == ["qty", 2]
    assert _constants(by_id["b/kept"].params["predicate"]) == ["qty", 7]
    ((_, bump_a),) = by_id["a/tagged"].params["columns"]
    assert _constants(bump_a) == ["qty", 2]
    # no Names survive substitution
    pred = by_id["b/kept"].params["predicate"]
    assert not any(isinstance(n, ast.Name) and n.id == "min_qty" for n in ast.walk(pred))
    # write consumes the returned local of each call
    assert by_id["_write0"].inputs == ("a/tagged",)
    assert by_id["_write1"].inputs == ("b/tagged",)
    assert by_id["a/tagged"].schema == ORDERS.extend([("bump", Scalar.INT)])
    # spans point into the step body
    assert by_id["a/kept"].span.line == 6


def test_nested_step_prefixes_compose():
    src = """# lattice: v1
from lattice.ops import load, where, write, col, step, Table, Int
from lattice.schemas import Orders
@step
def inner(orders: Table[Orders], k: Int) -> Table[Orders]:
    x = where(orders, col("qty") > k)
    return x
@step
def outer(orders: Table[Orders], m: Int) -> Table[Orders]:
    inner_out = inner(orders, k=m)
    y = where(inner_out, col("qty") < 100)
    return y
raw = load("orders.csv", schema=Orders)
r = outer(raw, m=4)
write(r, "out.parquet")
"""
    plan = lower(_checked(src))
    ids = [n.id for n in plan.nodes]
    assert ids == ["raw", "r/inner_out/x", "r/y", "_write0"]
    by_id = {n.id: n for n in plan.nodes}
    assert _constants(by_id["r/inner_out/x"].params["predicate"]) == ["qty", 4]
    assert by_id["r/y"].inputs == ("r/inner_out/x",)
    assert by_id["_write0"].inputs == ("r/y",)


def test_float_param_gets_float_constant():
    src = """# lattice: v1
from lattice.ops import load, derive, write, col, step, Table, Float
from lattice.schemas import Orders
@step
def scale(orders: Table[Orders], f: Float) -> Table:
    s = derive(orders, scaled=col("qty") * f, lit=f)
    return s
raw = load("orders.csv", schema=Orders)
r = scale(raw, f=2)
q = scale(raw, f=-1.5)
write(r, "out.parquet")
write(q, "q.parquet")
"""
    plan = lower(_checked(src))
    by_id = {n.id: n for n in plan.nodes}
    (_, scaled), (_, lit) = by_id["r/s"].params["columns"]
    assert isinstance(lit, ast.Constant) and lit.value == 2.0 and isinstance(lit.value, float)
    (_, _), (_, lit_q) = by_id["q/s"].params["columns"]
    assert isinstance(lit_q, ast.Constant) and lit_q.value == -1.5


def test_lowering_does_not_mutate_module():
    program = _checked(STEP_TWICE)
    before = ast.dump(program.module, include_attributes=True)
    lower(program)
    lower(program)
    assert ast.dump(program.module, include_attributes=True) == before


def test_node_is_frozen():
    plan = lower(_checked(LINEAR))
    with pytest.raises(AttributeError):
        plan.nodes[0].id = "x"  # type: ignore[misc]
    assert isinstance(plan.nodes[0], Node)
