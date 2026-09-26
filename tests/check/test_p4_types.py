import ast

import pytest

from lattice.catalog import IMPORTABLE
from lattice.check import check_source, p1_nodes, p2_names, p3_canon, p4_types
from lattice.check.context import CheckContext
from lattice.parse import parse
from lattice.typesys import UNKNOWN, ColT, LitT, Scalar, ScalarParamT, Schema, StepT, TableT

INT, F, S, B, D = Scalar.INT, Scalar.FLOAT, Scalar.STR, Scalar.BOOL, Scalar.DATE

H = "# lattice: v1\n"
IMPORTS = (
    "from lattice.ops import load, where, derive, select, rename, group, join, sort, limit, "
    "write, col, agg, step, Table, Int, Float, Str\n"
    "from lattice.schemas import Orders, Customers, RegionRevenue\n"
)
PRE = H + IMPORTS
LOAD = 'raw = load("orders.csv", schema=Orders)\n'
SCHEMA_NAMES = {"Orders", "Customers", "RegionRevenue"}
CUST = 'cust = load("customers.csv", schema=Customers)\n'


def full(src):
    """Replace the PRE placeholder imports with exactly the names the body uses."""
    if not src.startswith(PRE):
        return src
    body = src[len(PRE) :]
    used = {n.id for n in ast.walk(ast.parse(body)) if isinstance(n, ast.Name)}
    out = H + f"from lattice.ops import {', '.join(sorted(used & IMPORTABLE))}\n"
    if used & SCHEMA_NAMES:
        out += f"from lattice.schemas import {', '.join(sorted(used & SCHEMA_NAMES))}\n"
    return out + body


def check(profile, src):
    return check_source(full(src), profile)


def run_p4(profile, src):
    """Run P1-P4 directly; return (ctx, module, steps)."""
    parsed = parse(full(src))
    assert parsed.module is not None
    ctx = CheckContext(profile, diagnostics=list(parsed.diagnostics))
    p1_nodes.run(parsed.module, ctx)
    assert ctx.error_count() == 0, ctx.diagnostics
    p2_names.run(parsed.module, ctx)
    p3_canon.run(parsed.module, ctx)
    steps = p4_types.run(parsed.module, ctx)
    return ctx, parsed.module, steps


def type_of_assign(ctx, module, name):
    for stmt in module.body:
        if isinstance(stmt, ast.Assign) and stmt.targets[0].id == name:
            return ctx.types[id(stmt.value)]
    raise AssertionError(name)


def schema(profile, name):
    return profile.schemas[name]


def errors(profile, src):
    return [d.code for d in check(profile, src).diagnostics if d.severity == "error"]


def only(profile, src, code):
    return [d for d in check(profile, src).diagnostics if d.code == code]


# ---- schema propagation -----------------------------------------------------


def test_load(profile):
    ctx, m, _ = run_p4(profile, PRE + LOAD)
    assert type_of_assign(ctx, m, "raw") == TableT(schema(profile, "Orders"))


def test_where_sort_limit_preserve(profile):
    src = PRE + LOAD + 'a = where(raw, col("qty") > 1)\n'
    src += 'b = sort(a, by=["qty"], desc=True)\nc = limit(b, n=3)\n'
    ctx, m, _ = run_p4(profile, src)
    orders = TableT(schema(profile, "Orders"))
    assert [type_of_assign(ctx, m, n) for n in "abc"] == [orders, orders, orders]
    # the predicate expression is typed too
    where_call = m.body[-3].value
    assert ctx.types[id(where_call.args[1])] == ColT(B)


def test_derive_extends(profile):
    src = PRE + LOAD + 'd = derive(raw, total=col("qty") * col("price"), one=1)\n'
    ctx, m, _ = run_p4(profile, src)
    expected = schema(profile, "Orders").extend([("total", F), ("one", INT)])
    assert type_of_assign(ctx, m, "d") == TableT(expected)
    assert [d.code for d in ctx.diagnostics] == ["L204"]  # 'd' unused


def test_derive_overwrites_in_place_same_type(profile):
    src = PRE + LOAD + 'd = derive(raw, qty=col("qty") + 1, extra=2)\n'
    ctx, m, _ = run_p4(profile, src)
    expected = schema(profile, "Orders").extend([("extra", INT)])
    assert type_of_assign(ctx, m, "d") == TableT(expected)
    assert [d.code for d in ctx.diagnostics] == ["L204"]  # no L405


def test_derive_overwrite_changes_type_keeps_position(profile):
    src = PRE + LOAD + 'd = derive(raw, qty=col("qty") * 1.5)\n'
    ctx, m, _ = run_p4(profile, src)
    cols = type_of_assign(ctx, m, "d").schema.columns
    orders = schema(profile, "Orders").columns
    assert [n for n, _ in cols] == [n for n, _ in orders]
    assert dict(cols)["qty"] == F


def test_derive_kwargs_read_input_table(profile):
    # 'b' reads the INPUT 'qty' (Int), not the Str value assigned by the earlier keyword
    src = PRE + LOAD + 'd = derive(raw, qty=col("status"), b=col("qty") + 1)\n'
    ctx, m, _ = run_p4(profile, src)
    t = type_of_assign(ctx, m, "d").schema
    assert t.get("qty") == S and t.get("b") == INT
    # and a keyword cannot read a column that only an earlier keyword creates
    src = PRE + LOAD + 'x = derive(raw, a=1, b=col("a"))\nwrite(x, "o.csv")\n'
    assert errors(profile, src) == ["L401"]


def test_derive_overwrite_type_error_still_reported(profile):
    src = PRE + LOAD + 'x = derive(raw, qty=col("status") + 1)\nwrite(x, "o.csv")\n'
    assert errors(profile, src) == ["L402"]


def test_select_projects(profile):
    ctx, m, _ = run_p4(profile, PRE + LOAD + 's = select(raw, cols=["region", "qty"])\n')
    assert type_of_assign(ctx, m, "s") == TableT(Schema.from_pairs([("region", S), ("qty", INT)]))


def test_rename(profile):
    ctx, m, _ = run_p4(profile, PRE + CUST + 'r = rename(cust, name="customer_name")\n')
    assert type_of_assign(ctx, m, "r") == TableT(
        Schema.from_pairs([("customer_id", INT), ("customer_name", S), ("city", S)])
    )


def test_group(profile):
    src = PRE + LOAD
    src += 'g = group(raw, by=["region"], revenue=agg.sum(col("price")), n=agg.count())\n'
    ctx, m, _ = run_p4(profile, src)
    assert type_of_assign(ctx, m, "g") == TableT(
        Schema.from_pairs([("region", S), ("revenue", F), ("n", INT)])
    )


def test_join(profile):
    src = PRE + LOAD + CUST + 'j = join(raw, cust, on=["customer_id"], how="left")\n'
    ctx, m, _ = run_p4(profile, src)
    expected = schema(profile, "Orders").extend([("name", S), ("city", S)])
    assert type_of_assign(ctx, m, "j") == TableT(expected)


def test_write_has_no_type(profile):
    ctx, m, _ = run_p4(profile, PRE + LOAD + 'write(raw, "out.parquet")\n')
    assert id(m.body[-1].value) not in ctx.types


def test_checked_program_carries_types_and_steps(profile):
    src = (
        PRE
        + "@step\n"
        + "def big(t: Table[Orders], n: Int) -> Table:\n"
        + '    y = where(t, col("qty") > n)\n'
        + "    return y\n"
        + LOAD
        + "b = big(raw, n=2)\n"
        + 'write(b, "out.parquet")\n'
    )
    r = check(profile, src)
    assert r.ok, r.diagnostics
    orders = TableT(schema(profile, "Orders"))
    assert r.program.steps == {"big": StepT((("t", orders), ("n", ScalarParamT(INT))), orders)}
    call = r.program.module.body[-2].value
    assert r.program.types[id(call)] == orders
    (fn,) = [x for x in r.program.module.body if isinstance(x, ast.FunctionDef)]
    assert r.program.types[id(fn)] == r.program.steps["big"]
    step_where = fn.body[0].value
    assert r.program.types[id(step_where)] == orders
    assert r.program.types[id(step_where.args[1].comparators[0])] == LitT(INT)


# ---- steps ------------------------------------------------------------------

STEP_INFERRED = (
    "@step\n"
    "def totals(t: Table[Orders], min_qty: Int) -> Table:\n"
    '    paid = where(t, (col("status") == "paid") & (col("qty") >= min_qty))\n'
    '    w = derive(paid, total=col("qty") * col("price"))\n'
    '    g = group(w, by=["region"], revenue=agg.sum(col("total")))\n'
    "    return g\n"
)


def test_step_inferred_result_used_downstream(profile):
    src = PRE + STEP_INFERRED + LOAD + "r = totals(raw, min_qty=2)\n"
    src += 's = where(r, col("revenue") > 10.0)\n'
    ctx, m, steps = run_p4(profile, src)
    rr = TableT(schema(profile, "RegionRevenue"))
    assert steps["totals"].result == rr
    assert type_of_assign(ctx, m, "s") == rr
    assert [d.code for d in ctx.diagnostics if d.severity == "error"] == []


def test_step_downstream_unknown_column(profile):
    src = PRE + STEP_INFERRED + LOAD + "r = totals(raw, min_qty=2)\n"
    src += 's = where(r, col("qty") > 1)\nwrite(s, "o.csv")\n'
    assert errors(profile, src) == ["L401"]


def test_step_concrete_return_ok(profile):
    src = PRE + STEP_INFERRED.replace("-> Table:", "-> Table[RegionRevenue]:") + LOAD
    src += 'r = totals(raw, min_qty=2)\nwrite(r, "o.csv")\n'
    assert check(profile, src).diagnostics == ()


def test_step_return_mismatch_L605(profile):
    src = PRE + STEP_INFERRED.replace("-> Table:", "-> Table[Customers]:") + LOAD
    src += 'r = totals(raw, min_qty=2)\nwrite(r, "o.csv")\n'
    (d,) = only(profile, src, "L605")
    assert "totals" in d.message and "revenue" in d.message
    assert errors(profile, src) == ["L605"]


def test_step_param_schema_mismatch_L418(profile):
    src = PRE + STEP_INFERRED + CUST + 'r = totals(cust, min_qty=2)\nwrite(r, "o.csv")\n'
    (d,) = only(profile, src, "L418")
    assert "'t'" in d.message
    assert errors(profile, src) == ["L418"]


@pytest.mark.parametrize("value", ['"two"', "2.5", "True"])
def test_step_scalar_arg_type_L402(profile, value):
    src = PRE + STEP_INFERRED + LOAD + f'r = totals(raw, min_qty={value})\nwrite(r, "o.csv")\n'
    assert errors(profile, src) == ["L402"]


def test_step_float_param_accepts_int_and_forwarded_param(profile):
    src = (
        PRE
        + "@step\n"
        + "def f(t: Table[Orders], lo: Float) -> Table:\n"
        + '    y = where(t, col("price") > lo)\n'
        + "    return y\n"
        + "@step\n"
        + "def g(t: Table[Orders], lo: Float) -> Table:\n"
        + "    y = f(t, lo=lo)\n"
        + "    return y\n"
        + LOAD
        + "a = f(raw, lo=1)\n"
        + "b = g(a, lo=-2.5)\n"
        + 'write(b, "o.csv")\n'
    )
    assert errors(profile, src) == []


def test_scalar_in_table_position_L419(profile):
    src = PRE + (
        "@step\n"
        "def f(t: Table[Orders], n: Int) -> Table:\n"
        '    y = where(n, col("qty") > 1)\n'
        "    return y\n"
    )
    src += LOAD + 'r = f(raw, n=1)\nwrite(r, "o.csv")\n'
    assert "L419" in errors(profile, src)


def test_step_literal_predicate_L304(profile):
    src = PRE + (
        "@step\ndef f(t: Table[Orders], n: Int) -> Table:\n    y = where(t, n > 1)\n    return y\n"
    )
    src += LOAD + 'r = f(raw, n=1)\nwrite(r, "o.csv")\n'
    assert errors(profile, src) == ["L304"]


def test_step_param_not_a_schema_L414(profile):
    src = PRE + (
        '@step\ndef f(t: Table[Int]) -> Table:\n    y = where(t, col("qty") > 1)\n    return y\n'
    )
    src += LOAD + 'r = f(raw)\nwrite(r, "o.csv")\n'
    assert errors(profile, src) == ["L414"]


# ---- op errors ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("stmt", "code"),
    [
        ("x = load(5, schema=Orders)", "L404"),
        ('x = load("o.csv", schema="Orders")', "L414"),
        ('x = load("o.csv", schema=Int)', "L414"),
        ('x = where(raw, col("qty"))', "L403"),
        ('x = where(raw, col("qty") + 1)', "L403"),
        ('x = derive(raw, a=col("nope"))', "L401"),
        ("x = select(raw, cols=[1])", "L404"),
        ('x = select(raw, cols="qty")', "L404"),
        ('x = select(raw, cols=["nope"])', "L401"),
        ('x = select(raw, cols=["qty", "qty"])', "L415"),
        ('x = rename(raw, nope="a")', "L401"),
        ("x = rename(raw, qty=1)", "L404"),
        ('x = rename(raw, qty="price")', "L405"),
        ('x = rename(raw, qty="a", price="a")', "L405"),
        ('x = group(raw, by=["nope"], n=agg.count())', "L401"),
        ('x = group(raw, by=["region", "region"], n=agg.count())', "L415"),
        ('x = group(raw, by=["region"], region=agg.count())', "L405"),
        ('x = group(raw, by=["region"], n=col("qty"))', "L413"),
        ('x = group(raw, by=["region"], n=agg.sum(col("status")))', "L402"),
        ('x = sort(raw, by=["nope"])', "L401"),
        ('x = sort(raw, by=["qty"], desc="yes")', "L404"),
        ("x = limit(raw, n=0)", "L417"),
        ("x = limit(raw, n=True)", "L417"),
        ('x = limit(raw, n="5")', "L417"),
    ],
)
def test_op_errors(profile, stmt, code):
    src = PRE + LOAD + stmt + '\nwrite(x, "o.csv")\n'
    assert errors(profile, src) == [code], check(profile, src).diagnostics


def test_write_path_not_string(profile):
    assert errors(profile, PRE + LOAD + "write(raw, 5)\n") == ["L404"]


JOIN_SETUP = (
    # sid: customer_id as Str
    'c1 = derive(cust, cid=fn.to_str(col("customer_id")))\n'
    'c2 = select(c1, cols=["cid", "name"])\n'
    'sid = rename(c2, cid="customer_id")\n'
    # clash: a non-key column also present in Orders
    'clash = derive(cust, region=col("city"))\n'
)


@pytest.mark.parametrize(
    ("stmt", "code"),
    [
        ('x = join(raw, cust, on=["name"])', "L401"),
        ('x = join(raw, cust, on=["customer_id"], how="outer")', "L404"),
        ('x = join(raw, cust, on=["customer_id"], how=1)', "L404"),
        ('x = join(raw, cust, on="customer_id")', "L404"),
        ('x = join(raw, sid, on=["customer_id"])', "L402"),
        ('x = join(raw, clash, on=["customer_id"])', "L416"),
    ],
)
def test_join_errors(profile, stmt, code):
    src = PRE + LOAD + CUST + JOIN_SETUP
    src += stmt + '\nwrite(x, "o.csv")\n'
    ds = [d for d in check(profile, src).diagnostics if d.severity == "error"]
    assert [d.code for d in ds] == [code], ds
    if code == "L401":
        assert "left" in ds[0].message


def test_join_collision_message(profile):
    src = PRE + LOAD + CUST + 'c3 = derive(cust, region=col("city"))\n'
    src += 'x = join(raw, c3, on=["customer_id"])\nwrite(x, "o.csv")\n'
    (d,) = only(profile, src, "L416")
    assert "'region'" in d.message and "rename" in d.message


# ---- cascade suppression ------------------------------------------------------


def test_unknown_schema_suppresses_downstream(profile):
    src = PRE + (
        'raw = load("orders.csv", schema=Ordrs)\n'
        'a = where(raw, col("qty") > 1)\n'
        'b = derive(a, t=col("nope") * 2)\n'
        'c = select(b, cols=["zz"])\n'
        'write(c, "out.parquet")\n'
    )
    ds = check(profile, src).diagnostics
    assert [d.code for d in ds] == ["L203"], ds


def test_bad_column_suppresses_downstream(profile):
    src = PRE + LOAD + 'a = derive(raw, t=col("nope") * 2)\n'
    src += 'b = where(a, col("t") > 1)\nwrite(b, "o.csv")\n'
    assert errors(profile, src) == ["L401"]


def test_expression_types_recorded(profile):
    src = PRE + LOAD + 'd = derive(raw, total=col("qty") * col("price"))\n'
    ctx, m, _ = run_p4(profile, src)
    kw = m.body[-1].value.keywords[0].value
    assert ctx.types[id(kw)] == ColT(F)
    assert ctx.types[id(kw.left)] == ColT(INT)
    assert UNKNOWN not in ctx.types.values()
