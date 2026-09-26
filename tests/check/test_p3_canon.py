import pytest

H = "# lattice: v1\n"
PRE = (
    H
    + "from lattice.ops import load, where, derive, select, group, sort, write, col, when, agg\n"
    + "from lattice.schemas import Orders\n"
)
LOAD = 'raw = load("orders.csv", schema=Orders)\n'  # line 4


def p3(diags, src):
    """P3 diagnostics only (L306 is reported by P2)."""
    return [
        (d.code, d.span.line, d.span.col)
        for d in diags(src)
        if d.code.startswith(("L30", "L7")) and d.code != "L306"
    ]


def prog(*lines: str) -> str:
    return PRE + LOAD + "".join(line + "\n" for line in lines)


def test_valid_program(diags):
    src = prog(
        'paid = where(raw, col("status") == "paid")',
        'w = derive(paid, total=col("qty") * col("price"), '
        'tier=when(col("qty") > 5, then="big", otherwise=when(col("qty") > 1, then="mid", '
        'otherwise="small")))',
        'g = group(w, by=["region", "tier"], revenue=agg.sum(col("total")))',
        'o = sort(g, by=["revenue"], desc=True)',
        'o2 = select(o, cols=["region", "revenue"])',
        'write(o2, "out.parquet")',
    )
    assert diags(src) == ()


def test_chained_ops(diags):
    src = prog('x = where(derive(raw, a=col("qty")), col("a") > 1)', 'write(x, "o.csv")')
    assert p3(diags, src) == [("L301", 5, 11)]


def test_table_arg_must_be_name(diags):
    src = prog('x = where("raw", col("a") > 1)', 'write(x, "o.csv")')
    assert [c for c, *_ in p3(diags, src)] == ["L301"]


def test_not_a_unit_call(diags):
    src = prog('x = col("a")', 'write(raw, "o.csv")')
    assert [c for c, *_ in p3(diags, src)] == ["L302"]


def test_unknown_callee_not_double_reported(codes):
    src = prog('x = filter(raw, col("qty") > 1)', 'write(x, "o.csv")')
    assert "L302" not in codes(src)


def test_bare_non_terminal(diags):
    src = prog('derive(raw, a=col("qty"))', 'write(raw, "o.csv")')
    ((code, line, _),) = p3(diags, src)
    assert (code, line) == ("L302", 5)
    assert "must be assigned" in [d for d in diags(src) if d.code == "L302"][0].message


def test_assigned_write_is_left_to_p5(codes):
    src = prog('x = write(raw, "o.csv")')
    assert "L302" not in codes(src)
    assert codes(src).count("L502") == 1


def test_where_predicate_by_keyword(diags):
    src = prog('x = where(raw, pred=col("a") > 1)', 'write(x, "o.csv")')
    (d,) = [d for d in diags(src) if d.code == "L303"]
    assert "positional" in d.message and d.hint


def test_select_missing_cols(diags):
    src = prog("x = select(raw)", 'write(x, "o.csv")')
    assert [c for c, *_ in p3(diags, src)] == ["L303"]


def test_sort_unexpected_keyword(diags):
    src = prog('x = sort(raw, by=["a"], reverse=True)', 'write(x, "o.csv")')
    assert [c for c, *_ in p3(diags, src)] == ["L303"]


@pytest.mark.parametrize(
    "stmt",
    [
        "x = where(raw, True)",
        "x = derive(raw)",
        "x = select(raw, cols=[])",
        "x = group(raw, by=[])",
    ],
)
def test_noop_steps(diags, stmt):
    src = prog(stmt, 'write(x, "o.csv")')
    assert [c for c, *_ in p3(diags, src)] == ["L304"]


def test_two_consecutive_wheres_ok(diags):
    src = prog(
        'a = where(raw, col("qty") > 1)', 'b = where(a, col("qty") < 9)', 'write(b, "o.csv")'
    )
    assert p3(diags, src) == []


def test_when_nested_in_then(diags):
    src = prog(
        'x = derive(raw, t=when(col("a") > 1, then=when(col("b") > 1, then=1, otherwise=2), '
        "otherwise=3))",
        'write(x, "o.csv")',
    )
    assert [c for c, *_ in p3(diags, src)] == ["L701"]


STEP = (
    H
    + "from lattice.ops import load, where, write, col, step, Table, Int\n"
    + "from lattice.schemas import Orders\n"
    + "@step\n"
    + "def f(t: Table[Orders], n: Int) -> Table:\n"
    + '    y = where(t, col("qty") > n)\n'
    + "    return y\n"
    + LOAD
)


def test_step_scalar_positional(diags):
    src = STEP + 'z = f(raw, 2)\nwrite(z, "o.csv")\n'
    assert [c for c, *_ in p3(diags, src)] == ["L303"]


def test_step_missing_scalar_keyword(diags):
    src = STEP + 'z = f(raw)\nwrite(z, "o.csv")\n'
    assert [c for c, *_ in p3(diags, src)] == ["L303"]


def test_step_unknown_keyword(diags):
    src = STEP + 'z = f(raw, n=2, m=3)\nwrite(z, "o.csv")\n'
    assert [c for c, *_ in p3(diags, src)] == ["L303"]


def test_step_call_ok(diags):
    assert diags(STEP + 'z = f(raw, n=2)\nwrite(z, "o.csv")\n') == ()


def test_step_body_is_checked(diags):
    src = STEP.replace(
        'y = where(t, col("qty") > n)', 'y = where(where(t, col("qty") > n), col("qty") < 9)'
    )
    src += 'z = f(raw, n=2)\nwrite(z, "o.csv")\n'
    assert [c for c, *_ in p3(diags, src)] == ["L301"]
