import time

from lattice.check import check_source
from lattice.typesys import TableT


def error_codes(result):
    return sorted(d.code for d in result.diagnostics if d.severity == "error")


def test_multi_error_program(profile, program):
    src = program(
        'raw = load("orders.csv", schema=Orders)\n'
        'paid = where(raw, col("status") == "paid")\n'
        'paid = where(raw, col("qty") > 1)\n'  # L201
        'w = derive(raw, t=limit(raw, n=1))\n'  # L301
        'bad = derive(raw, t=col("prce") * 2)\n'  # L401
        'nb = where(raw, col("qty"))\n'  # L403
        'write(paid, "/tmp/out.parquet")\n'  # L501
    )
    r = check_source(src, profile)
    assert not r.ok
    assert error_codes(r) == ["L201", "L301", "L401", "L403", "L501"], r.diagnostics


def _big_program(n: int) -> str:
    lines = ['t0 = load("orders.csv", schema=Orders)']
    for i in range(1, n - 1):
        if i % 2:
            lines.append(f't{i} = derive(t{i - 1}, c{i}=col("qty") * {i} + col("price"))')
        else:
            lines.append(f't{i} = where(t{i - 1}, (col("qty") > {i}) | (col("c{i - 1}") < 5.0))')
    lines.append(f'write(t{n - 2}, "out.parquet")')
    return "\n".join(lines) + "\n"


def test_perf_300_statements(profile, program):
    src = program(_big_program(300))
    assert src.count("\n") >= 300
    start = time.perf_counter()
    r = check_source(src, profile)
    elapsed = time.perf_counter() - start
    assert r.ok, r.diagnostics[:3]
    assert elapsed < 1.0, elapsed


SPEC_PIPELINE = (
    'raw_orders       = load("orders.csv", schema=Orders)\n'
    'paid_orders      = where(raw_orders, col("status") == "paid")\n'
    'orders_w_total   = derive(paid_orders, total=col("qty") * col("price"))\n'
    'revenue_by_region = group(orders_w_total, by=["region"], '
    'revenue=agg.sum(col("total")))\n'
    'write(revenue_by_region, "revenue.parquet")\n'
)

SPEC_STEP = (
    "@step\n"
    "def paid_revenue(orders: Table[Orders], min_qty: Int) -> Table[RegionRevenue]:\n"
    '    paid      = where(orders, (col("status") == "paid") & (col("qty") >= min_qty))\n'
    '    w_total   = derive(paid, total=col("qty") * col("price"))\n'
    '    by_region = group(w_total, by=["region"], revenue=agg.sum(col("total")))\n'
    "    return by_region\n"
    "\n"
    'raw_orders = load("orders.csv", schema=Orders)\n'
    "revenue    = paid_revenue(raw_orders, min_qty=2)\n"
    'write(revenue, "revenue.parquet")\n'
)


def test_spec_pipeline_example(profile, program):
    r = check_source(program(SPEC_PIPELINE), profile)
    assert r.diagnostics == ()
    assert r.ok


def test_spec_step_example(profile, program):
    r = check_source(program(SPEC_STEP), profile)
    assert r.diagnostics == ()
    rr = TableT(profile.schemas["RegionRevenue"])
    assert r.program.steps["paid_revenue"].result == rr
    call = r.program.module.body[-2].value
    assert r.program.types[id(call)] == rr


def test_crlf_and_bom(profile, program):
    src = "\ufeff" + program(SPEC_STEP).replace("\n", "\r\n")
    r = check_source(src, profile)
    assert r.ok and r.diagnostics == ()


def test_every_unit_call_is_typed(profile, program):
    import ast

    r = check_source(program(SPEC_STEP), profile)
    units = [
        n
        for n in ast.walk(r.program.module)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id in {"load", "where", "derive", "group", "paid_revenue"}
    ]
    assert len(units) == 5
    assert all(isinstance(r.program.types[id(u)], TableT) for u in units)
