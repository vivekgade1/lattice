import ast

import pytest

from lattice.check.astutil import (
    annotation_schema_name,
    call_name,
    is_table_annotation,
    is_valid_annotation,
    step_param_kinds,
    steps_of,
)

H = "# lattice: v1\n"
IMPORTS = (
    "from lattice.ops import load, where, derive, group, write, col, agg, fn, step, Table, Int\n"
    "from lattice.schemas import Orders\n"
)

PIPELINE = (
    H
    + IMPORTS.replace(", step, Table, Int", "")
    + 'raw = load("orders.csv", schema=Orders)\n'
    + 'paid = where(raw, (col("status") == "paid") & ~(col("qty") < 1))\n'
    + 'w_total = derive(paid, total=col("qty") * col("price"), up=fn.upper(col("region")))\n'
    + 'by_region = group(w_total, by=["region"], revenue=agg.sum(col("total")))\n'
    + 'write(by_region, "out.parquet")\n'
)

STEP = (
    H
    + IMPORTS.replace("derive, ", "").replace(", fn", "")
    + "@step\n"
    + "def paid_revenue(orders: Table[Orders], min_qty: Int) -> Table:\n"
    + '    paid = where(orders, col("qty") >= min_qty)\n'
    + '    by_region = group(paid, by=["region"], revenue=agg.sum(col("qty")))\n'
    + "    return by_region\n"
    + 'raw = load("orders.csv", schema=Orders)\n'
    + "rev = paid_revenue(raw, min_qty=2)\n"
    + 'write(rev, "out.parquet")\n'
)


def p1(diags, src):
    return [(d.code, d.span.line) for d in diags(src) if d.code.startswith(("L1", "L6"))]


def test_valid_pipeline(diags):
    assert diags(PIPELINE) == ()


def test_valid_step(diags):
    assert diags(STEP) == ()


PRE = H + "from lattice.ops import load, col\n"


@pytest.mark.parametrize(
    ("body", "code", "line"),
    [
        ("x = a ** b\n", "L101", 3),
        ("x = +a\n", "L101", 3),
        ("x = f(*a)\n", "L101", 3),
        ("x = f(**a)\n", "L101", 3),
        ("x = {1: 2}\n", "L101", 3),
        ("x = 1\nfrom lattice.ops import where\n", "L102", 4),
        ("import polars\n", "L103", 3),
        ("from pandas import read_csv\n", "L103", 3),
        ("from . import x\n", "L103", 3),
        ("from lattice.ops import *\n", "L103", 3),
        ("from lattice.ops import where as w\n", "L104", 3),
        ("x = t.filter(col('a') > 1)\n", "L105", 3),
        ("x = f(t.status)\n", "L106", 3),
        ("x = y = load('a')\n", "L108", 3),
        ("a, b = load('a')\n", "L108", 3),
        ("x = f(a < b in c)\n", "L109", 3),
        ("def f(t: Table[Orders]) -> Table:\n    y = g(t)\n    return y\n", "L110", 3),
        ("@other\ndef f(t: Table[Orders]) -> Table:\n    y = g(t)\n    return y\n", "L110", 3),
        ("from lattice.ops import filtre\n", "L112", 3),
        ("from lattice.schemas import Nope\n", "L112", 3),
        ("x = f(a is b)\n", "L113", 3),
        ("x = f(lambda r: r)\n", "L120", 3),
        ("x = f({a for a in b})\n", "L121", 3),
        ("x = f({a: 1 for a in b})\n", "L121", 3),
        ("x = f(a for a in b)\n", "L121", 3),
        ("x = f(t['a'])\n", "L123", 3),
        ("x = f(f'{a}')\n", "L124", 3),
        ("x = f((y := 1))\n", "L125", 3),
        ("x += 1\n", "L126", 3),
        ("x: int = 1\n", "L126", 3),
        ("for a in b:\n    x = 1\n", "L127", 3),
        ("while a:\n    x = 1\n", "L127", 3),
        ("if a:\n    x = 1\n", "L127", 3),
        ("class C:\n    x = 1\n", "L128", 3),
        ("try:\n    x = 1\nexcept E:\n    x = 2\n", "L129", 3),
        ("with a:\n    x = 1\n", "L129", 3),
        ("raise E\n", "L129", 3),
        ("assert a\n", "L129", 3),
        ("del a\n", "L129", 3),
        ("pass\n", "L129", 3),
        ("global a\n", "L130", 3),
        ("async def f():\n    return 1\n", "L131", 3),
        ("x = f(await a)\n", "L131", 3),
    ],
)
def test_p1_codes(diags, body, code, line):
    assert p1(diags, PRE + body) == [(code, line)]


def test_unknown_op_import_hints_canonical(diags):
    (d,) = [d for d in diags(PRE + "from lattice.ops import filter\n") if d.code == "L112"]
    assert "where" in d.hint


def test_unknown_schema_import_hints_available(diags):
    (d,) = [d for d in diags(PRE + "from lattice.schemas import Ordrs\n") if d.code == "L112"]
    assert "Orders" in d.hint


def test_nested_def_L111(diags):
    src = (
        H
        + "from lattice.ops import step, Table, where, col\n"
        + "@step\n"
        + "def f(t: Table[Orders]) -> Table:\n"
        + "    def g(x):\n"
        + "        return x\n"
        + "    y = where(t, col('a') > 1)\n"
        + "    return y\n"
    )
    assert p1(diags, src) == [("L111", 5)]


STEP_PRE = H + "from lattice.ops import step, Table, Int, where, col\n"


@pytest.mark.parametrize(
    ("sig", "line"),
    [
        ("def f(t: Table[Orders], /) -> Table:", 4),
        ("def f(t: Table[Orders], *, n: Int) -> Table:", 4),
        ("def f(t: Table[Orders], *rest) -> Table:", 4),
        ("def f(t: Table[Orders], **kw) -> Table:", 4),
        ("def f(t: Table[Orders], n: Int = 3) -> Table:", 4),
        ("def f(t) -> Table:", 4),
        ("def f(t: list) -> Table:", 4),
        ("def f(t: Table) -> Table:", 4),
        ("def f(t: Table[Orders]):", 4),
        ("def f(t: Table[Orders]) -> Int:", 4),
    ],
)
def test_L601(diags, sig, line):
    src = STEP_PRE + "@step\n" + sig + "\n    y = where(t, col('a') > 1)\n    return y\n"
    assert p1(diags, src) == [("L601", line)]


@pytest.mark.parametrize(
    ("body", "line"),
    [
        ("    return t\n", 4),  # no assignment
        ("    y = where(t, col('a') > 1)\n", 4),  # no return
        ("    y = where(t, col('a') > 1)\n    return y.a\n", 6),
        ("    y = where(t, col('a') > 1)\n    return y\n    z = where(y, col('b') > 1)\n", 6),
        ("    y = where(t, col('a') > 1)\n    write(y, 'o.csv')\n    return y\n", 6),
    ],
)
def test_L602(diags, body, line):
    src = STEP_PRE + "@step\ndef f(t: Table[Orders]) -> Table:\n" + body
    assert p1(diags, src) == [("L602", line)]


def test_comprehension_no_cascade(codes):
    assert codes(PRE + "df = [x for x in y]\n") == ["L121"]


def test_ifexp_is_normalised_to_when(diags):
    # N1: no L122 -- the conditional expression is when(c, then=b, otherwise=d)
    assert "L122" not in [d.code for d in diags(PRE + "a = b if c else d\n")]


def test_method_call(codes):
    assert codes(PRE + "x = t.filter(col('a') > 1)\n") == ["L105"]


def test_boolop_is_normalised(codes):
    # N1: `and` is `&`; what remains are the ordinary errors of `load('a') & y`
    assert "L107" not in codes(PRE + "x = load('a') and y\n")


def test_three_independent_violations(codes):
    src = PRE + "x = a[0]\ny = [a for a in b]\nimport os\n"
    assert codes(src) == ["L123", "L121", "L103"]


def test_annotations_not_walked(codes):
    # Subscript inside the annotation must not be reported as L123.
    assert "L123" not in codes(STEP)


# ---- astutil ----


def _ann(src: str) -> ast.expr:
    return ast.parse(src, mode="eval").body


def test_astutil_annotations():
    assert is_table_annotation(_ann("Table"))
    assert is_table_annotation(_ann("Table[Orders]"))
    assert not is_table_annotation(_ann("Int"))
    assert not is_table_annotation(None)
    assert annotation_schema_name(_ann("Table[Orders]")) == "Orders"
    assert annotation_schema_name(_ann("Table")) is None
    assert is_valid_annotation(_ann("Int"))
    assert is_valid_annotation(_ann("Table[Orders]"))
    assert not is_valid_annotation(_ann("list"))
    assert not is_valid_annotation(_ann("Table[Orders, X]"))
    assert not is_valid_annotation(None)


def test_astutil_steps_and_calls():
    mod = ast.parse(STEP)
    steps = steps_of(mod)
    assert list(steps) == ["paid_revenue"]
    assert step_param_kinds(steps["paid_revenue"]) == (("orders", "table"), ("min_qty", "scalar"))
    call = ast.parse("f(agg.sum(x), fn.upper(y), where(t, p))", mode="eval").body
    assert call_name(call) == "f"
    assert call_name(call.args[0]) == "agg.sum"
    assert call_name(call.args[1]) == "fn.upper"
    assert call_name(ast.parse("a.b.c()", mode="eval").body) is None
