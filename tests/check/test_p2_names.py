import pytest

from lattice.diagnostics import Severity

H = "# lattice: v1\n"
OPS = "from lattice.ops import load, where, derive, write, col, step, Table, Int\n"
SCH = "from lattice.schemas import Orders\n"
PRE = H + OPS + SCH
LOAD = 'raw = load("orders.csv", schema=Orders)\n'  # line 4


def only(diags, src, code):
    return [d for d in diags(src) if d.code == code]


def test_rebinding(diags):
    src = PRE + LOAD + 'df = where(raw, col("a") > 1)\ndf = where(raw, col("b") > 1)\n'
    src += 'write(df, "out.parquet")\n'
    (d,) = only(diags, src, "L201")
    assert d.span.line == 6 and "line 5" in d.message


def test_duplicate_import_is_harmless(codes):
    # imports are optional re-statements of ambient names; repeating one is not a rebinding
    src = H + OPS + "from lattice.ops import load\n" + SCH + LOAD + 'write(raw, "o.csv")\n'
    assert codes(src) == []


def test_param_shadows_import(diags):
    src = (
        PRE
        + LOAD
        + (
            "@step\n"
            "def f(where: Table[Orders]) -> Table:\n"
            '    y = derive(where, a=col("qty"))\n'
            "    return y\n"
        )
    )
    ds = only(diags, src, "L205")
    assert len(ds) == 1 and ds[0].span.line == 6


def test_builtin_shadow(codes):
    src = PRE + LOAD + 'sum = derive(raw, a=col("qty"))\nwrite(sum, "o.csv")\n'
    assert codes(src).count("L205") == 1


def test_use_before_assignment(diags):
    src = PRE + 'a = where(b, col("x") > 1)\nb = load("o.csv", schema=Orders)\n'
    src += 'write(a, "o.csv")\n'
    ds = diags(src)
    l202 = [d for d in ds if d.code == "L202"]
    assert len(l202) == 1 and l202[0].span.line == 4 and "line 5" in l202[0].message
    # the later binding is not additionally reported as unused
    assert not [d for d in ds if d.code == "L204"]


def test_unknown_name(diags):
    src = PRE + LOAD + 'x = pandas(raw)\nwrite(x, "o.csv")\n'
    (d,) = only(diags, src, "L203")
    assert "'pandas'" in d.message


def test_unimported_op_is_ambient(codes):
    # formerly L203 "missing import": every catalog name is predeclared
    src = H + "from lattice.ops import load, write, col\n" + SCH + LOAD
    src += 'x = where(raw, col("qty") > 1)\nwrite(x, "o.csv")\n'
    assert codes(src) == []


def test_unimported_schema_is_ambient(codes):
    src = H + "from lattice.ops import load, write\n" + 'x = load("a.csv", schema=Orders)\n'
    src += 'write(x, "o.csv")\n'
    assert codes(src) == []


def test_no_imports_at_all(diags):
    src = H + LOAD + (
        "@step\n"
        "def big(t: Table[Orders], n: Int) -> Table:\n"
        '    out = where(t, col("qty") > n)\n'
        "    return out\n"
        "x = big(raw, n=2)\n"
        'y = derive(x, total=col("qty") * col("price"), day=fn.year(col("order_date")))\n'
        'write(y, "o.csv")\n'
    )
    assert diags(src) == ()


def test_unknown_name_typo_hint(diags):
    src = H + LOAD + 'x = selct(raw, cols=["qty"])\nwrite(x, "o.csv")\n'
    (d,) = only(diags, src, "L203")
    assert "select" in d.hint


def test_unknown_name_fn_alias_hint(diags):
    src = H + LOAD + 'x = derive(raw, n=length(col("status")))\nwrite(x, "o.csv")\n'
    (d,) = only(diags, src, "L203")
    assert "fn.len" in d.hint


def test_alias_hint(diags):
    src = PRE + LOAD + 'x = filter(raw, col("qty") > 1)\nwrite(x, "o.csv")\n'
    (d,) = only(diags, src, "L203")
    assert "where" in d.hint


def test_unknown_reported_once(codes):
    src = PRE + LOAD + 'x = foo(raw)\ny = foo(x)\nwrite(y, "o.csv")\n'
    assert codes(src).count("L203") == 1


def test_step_reads_module_table(diags):
    src = (
        PRE
        + LOAD
        + (
            "@step\n"
            "def f(t: Table[Orders]) -> Table:\n"
            '    y = where(raw, col("qty") > 1)\n'
            "    return y\n"
            "z = f(raw)\n"
            'write(z, "o.csv")\n'
        )
    )
    (d,) = only(diags, src, "L603")
    assert d.span.line == 7 and "parameter" in d.hint


def test_recursive_step(diags):
    src = (
        PRE
        + ("@step\ndef f(t: Table[Orders]) -> Table:\n    y = f(t)\n    return y\n")
        + LOAD.replace("raw", "r0")
        + 'z = f(r0)\nwrite(z, "o.csv")\n'
    )
    (d,) = only(diags, src, "L604")
    assert d.span.line == 6 and "recursion" in d.message


def test_step_used_before_defined(diags):
    src = (
        PRE
        + LOAD
        + (
            "@step\n"
            "def f(t: Table[Orders]) -> Table:\n"
            "    y = g(t)\n"
            "    return y\n"
            "@step\n"
            "def g(t: Table[Orders]) -> Table:\n"
            '    y = where(t, col("qty") > 1)\n'
            "    return y\n"
            "z = f(raw)\n"
            'write(z, "o.csv")\n'
        )
    )
    (d,) = only(diags, src, "L604")
    assert d.span.line == 7 and "before it is defined" in d.message
    assert not only(diags, src, "L204")


def test_module_call_before_step_defined(codes):
    src = (
        PRE.replace("derive, ", "").replace(", Int", "")
        + LOAD
        + 'z = f(raw)\nwrite(z, "o.csv")\n'
        + (
            "@step\n"
            "def f(t: Table[Orders]) -> Table:\n"
            '    y = where(t, col("qty") > 1)\n'
            "    return y\n"
        )
    )
    assert codes(src) == ["L604"]


def test_unused_import_is_fine(diags):
    # L306 is gone: imports are optional, so an unused one is neither error nor warning
    src = PRE + LOAD + 'write(raw, "o.csv")\n'
    assert diags(src) == ()


def test_unknown_import_still_l112(diags):
    src = H + "from lattice.ops import load, write, filter\n" + SCH + LOAD
    src += 'write(raw, "o.csv")\n'
    (d,) = only(diags, src, "L112")
    assert "where" in d.hint


def test_unknown_schema_import_still_l112(codes):
    src = H + "from lattice.schemas import Nope\n" + LOAD + 'write(raw, "o.csv")\n'
    assert codes(src) == ["L112"]


@pytest.mark.parametrize(
    "stmt, line",
    [
        ('where = load("a.csv", schema=Orders)\nwrite(where, "o.csv")\n', 3),
        ('Orders = load("a.csv", schema=Orders)\nwrite(Orders, "o.csv")\n', 3),
        ('Int = load("a.csv", schema=Orders)\nwrite(Int, "o.csv")\n', 3),
    ],
)
def test_binding_ambient_name_is_l205(diags, stmt, line):
    src = H + LOAD + stmt
    ds = only(diags, src, "L205")
    assert len(ds) == 1 and ds[0].span.line == line
    assert "built-in Lattice name" in ds[0].message


def test_step_param_named_col_is_l205(diags):
    src = H + LOAD + (
        "@step\n"
        "def f(col: Table[Orders]) -> Table:\n"
        '    y = where(col, fn.len(fn.lower(fn.strip(fn.upper(fn.to_str(1))))) > 0)\n'
        "    return y\n"
        "x = f(raw)\n"
        'write(x, "o.csv")\n'
    )
    ds = only(diags, src, "L205")
    assert len(ds) == 1 and ds[0].span.line == 4


def test_step_named_like_op_is_l205(codes):
    src = H + LOAD + (
        "@step\n"
        "def limit(t: Table[Orders]) -> Table:\n"
        '    y = where(t, col("qty") > 1)\n'
        "    return y\n"
        'write(raw, "o.csv")\n'
    )
    assert "L205" in codes(src)


def test_unused_table_is_warning(diags):
    src = H + "from lattice.ops import load, write\n" + SCH + LOAD
    src += 'other = load("b.csv", schema=Orders)\nwrite(raw, "o.csv")\n'
    ds = diags(src)
    assert [(d.code, d.severity, d.span.line) for d in ds] == [("L204", Severity.WARNING, 5)]


VALID_STEPS = (
    PRE.replace("derive, ", "")
    + (
        "@step\n"
        "def paid(t: Table[Orders], n: Int) -> Table:\n"
        '    y = where(t, col("qty") > n)\n'
        "    return y\n"
        "@step\n"
        "def twice(t: Table[Orders], n: Int) -> Table:\n"
        "    first = paid(t, n=n)\n"
        "    second = paid(first, n=n)\n"
        "    return second\n"
    )
    + LOAD
    + 'z = twice(raw, n=2)\nwrite(z, "o.csv")\n'
)


def test_valid_step_program(diags):
    assert diags(VALID_STEPS) == ()


def test_step_local_may_reuse_module_name(diags):
    src = (
        PRE.replace("derive, ", "").replace(", Int", "")
        + (
            "@step\n"
            "def f(t: Table[Orders]) -> Table:\n"
            '    raw = where(t, col("qty") > 1)\n'
            "    return raw\n"
        )
        + LOAD
        + 'z = f(raw)\nwrite(z, "o.csv")\n'
    )
    assert diags(src) == ()
