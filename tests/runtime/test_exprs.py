import ast
import datetime

import polars as pl
import pytest

from lattice.runtime import LatticeInternalError
from lattice.runtime.exprs import DTYPES, Compiled, compile_agg, compile_expr
from lattice.typesys import Scalar

DF = pl.DataFrame(
    {
        "i": [1, -2, 0, None],
        "f": [1.25, -2.5, 0.0, None],
        "s": ["  Ab ", "cd", "x-1", None],
        "b": [True, False, True, None],
        "d": [
            datetime.date(2024, 1, 31),
            datetime.date(2023, 12, 1),
            datetime.date(2024, 2, 29),
            None,
        ],
        "n": ["12", "x", "3.5", None],
        "unit price": [1.0, 2.0, 3.0, 4.0],
    },
    schema={
        "i": pl.Int64,
        "f": pl.Float64,
        "s": pl.String,
        "b": pl.Boolean,
        "d": pl.Date,
        "n": pl.String,
        "unit price": pl.Float64,
    },
)


def ev(src: str, df: pl.DataFrame = DF) -> pl.Series:
    compiled = compile_expr(ast.parse(src, mode="eval").body)
    assert isinstance(compiled, Compiled)
    return df.with_columns(compiled.expr.alias("__out"))["__out"]


def guard_counts(src: str, df: pl.DataFrame = DF) -> dict[str, int]:
    compiled = compile_expr(ast.parse(src, mode="eval").body)
    out: dict[str, int] = {}
    for code, g in compiled.guards:
        n = int(df.with_columns(g.fill_null(False).alias("__g"))["__g"].sum())
        out[code] = out.get(code, 0) + n
    return out


def test_dtypes_mapping():
    assert DTYPES[Scalar.INT] == pl.Int64
    assert DTYPES[Scalar.FLOAT] == pl.Float64
    assert DTYPES[Scalar.STR] == pl.String
    assert DTYPES[Scalar.BOOL] == pl.Boolean
    assert DTYPES[Scalar.DATE] == pl.Date


@pytest.mark.parametrize(
    ("src", "dtype", "value"),
    [
        ("True", pl.Boolean, True),
        ("7", pl.Int64, 7),
        ("2.5", pl.Float64, 2.5),
        ('"a"', pl.String, "a"),
    ],
)
def test_constants(src, dtype, value):
    s = ev(src)
    assert s.dtype == dtype
    assert s.to_list() == [value] * 4


def test_literal_only_arithmetic_is_int64():
    s = ev("1 + 2")
    assert s.dtype == pl.Int64
    assert s.to_list() == [3] * 4


def test_col_including_non_identifier():
    assert ev('col("i")').to_list() == [1, -2, 0, None]
    assert ev('col("unit price") * 2').to_list() == [2.0, 4.0, 6.0, 8.0]


def test_arithmetic_and_null_propagation():
    s = ev('col("i") + 1')
    assert s.dtype == pl.Int64
    assert s.to_list() == [2, -1, 1, None]
    assert ev('col("i") - col("f")').to_list() == [-0.25, 0.5, 0.0, None]
    assert ev('col("i") * 3').to_list() == [3, -6, 0, None]
    assert ev('col("i") * 1.5').dtype == pl.Float64


def test_division_by_zero_is_null_with_guard():
    s = ev('col("i") / col("i")')
    assert s.dtype == pl.Float64
    assert s.to_list() == [1.0, 1.0, None, None]
    assert guard_counts('col("i") / col("i")') == {"R301": 1}
    assert ev("7 / 2").to_list() == [3.5] * 4
    assert guard_counts('col("f") / 0') == {"R301": 4}


def test_modulo_by_zero_is_null_with_guard():
    s = ev('7 % col("i")')
    assert s.dtype == pl.Int64
    assert s.to_list()[2] is None
    assert s.to_list()[0] == 0
    assert guard_counts('7 % col("i")') == {"R301": 1}


def test_bool_ops_and_comparisons():
    assert ev('col("b") & (col("i") > 0)').to_list() == [True, False, False, None]
    assert ev('col("b") | (col("i") > 0)').to_list() == [True, False, True, None]
    assert ev('~col("b")').to_list() == [False, True, False, None]
    assert ev('col("i") == 1').to_list() == [True, False, False, None]
    assert ev('col("i") != 1').to_list() == [False, True, True, None]
    assert ev('col("i") < 0').to_list() == [False, True, False, None]
    assert ev('col("i") <= 0').to_list() == [False, True, True, None]
    assert ev('col("f") > 0').to_list() == [True, False, False, None]
    assert ev('col("f") >= 0').to_list() == [True, False, True, None]
    assert ev('col("s") > "b"').to_list() == [False, True, True, None]
    neg = ev('-col("i")')
    assert neg.dtype == pl.Int64 and neg.to_list() == [-1, 2, 0, None]


def test_when_coalesce_is_null():
    s = ev('when(col("i") > 0, then=1, otherwise=2.5)')
    assert s.dtype == pl.Float64
    assert s.to_list() == [1.0, 2.5, 2.5, 2.5]
    nested = ev(
        'when(col("i") > 0, then="p", otherwise=when(col("i") < 0, then="n", otherwise="z"))'
    )
    assert nested.to_list() == ["p", "n", "z", "z"]
    c = ev('coalesce(col("i"), 9)')
    assert c.dtype == pl.Int64 and c.to_list() == [1, -2, 0, 9]
    assert ev('coalesce(col("i"), 0.5)').dtype == pl.Float64
    n = ev('is_null(col("s"))')
    assert n.dtype == pl.Boolean and n.to_list() == [False, False, False, True]


def test_guards_in_untaken_when_branch_are_not_counted():
    src = 'when(col("i") != 0, then=10 / col("i"), otherwise=0.0)'
    assert ev(src).to_list() == [10.0, -5.0, 0.0, 0.0]
    assert guard_counts(src) == {"R301": 0}


def test_math_functions():
    assert ev('fn.abs(col("i"))').to_list() == [1, 2, 0, None]
    assert ev('fn.abs(col("i"))').dtype == pl.Int64
    assert ev('fn.round(col("f") * 1.1, digits=1)').to_list() == [1.4, -2.8, 0.0, None]
    assert ev('fn.floor(col("f"))').to_list() == [1.0, -3.0, 0.0, None]
    assert ev('fn.ceil(col("f"))').to_list() == [2.0, -2.0, 0.0, None]
    sq = ev('fn.sqrt(col("i"))')
    assert sq.dtype == pl.Float64 and sq.to_list() == [1.0, None, 0.0, None]
    assert guard_counts('fn.sqrt(col("i"))') == {"R302": 1}
    clip = ev('fn.clip(col("i"), lo=-1.0, hi=0.5)')
    assert clip.dtype == pl.Float64  # a Float bound makes the result Float
    assert ev('fn.clip(col("i"), lo=-1, hi=1)').dtype == pl.Int64
    assert ev('fn.clip(col("f"), lo=-1, hi=1)').to_list() == [1.0, -1.0, 0.0, None]


def test_log_domain_error():
    s = ev('fn.log(col("i"))')
    assert s.dtype == pl.Float64
    assert s.to_list() == [0.0, None, None, None]
    assert guard_counts('fn.log(col("i"))') == {"R302": 2}


def test_string_functions():
    assert ev('fn.lower(col("s"))').to_list() == ["  ab ", "cd", "x-1", None]
    assert ev('fn.upper(col("s"))').to_list() == ["  AB ", "CD", "X-1", None]
    assert ev('fn.strip(col("s"))').to_list() == ["Ab", "cd", "x-1", None]
    ln = ev('fn.len(col("s"))')
    assert ln.dtype == pl.Int64 and ln.to_list() == [5, 2, 3, None]
    assert ev('fn.contains(col("s"), sub="-")').to_list() == [False, False, True, None]
    assert ev('fn.contains(col("s"), sub=".")').to_list() == [False, False, False, None]
    assert ev('fn.starts_with(col("s"), prefix="c")').to_list() == [False, True, False, None]
    assert ev('fn.replace(col("s"), old="-", new="+")').to_list() == ["  Ab ", "cd", "x+1", None]
    assert ev('fn.concat(col("s"), "!")').to_list() == ["  Ab !", "cd!", "x-1!", None]


def test_date_functions():
    y = ev('fn.year(col("d"))')
    assert y.dtype == pl.Int64 and y.to_list() == [2024, 2023, 2024, None]
    assert ev('fn.month(col("d"))').to_list() == [1, 12, 2, None]
    assert ev('fn.day(col("d"))').dtype == pl.Int64
    assert ev('fn.day(col("d"))').to_list() == [31, 1, 29, None]
    dd = ev('fn.date_diff(fn.to_date("2024-03-01", format="%Y-%m-%d"), col("d"), unit="days")')
    assert dd.dtype == pl.Int64 and dd.to_list() == [30, 91, 1, None]
    da = ev('fn.date_add(col("d"), days=1)')
    assert da.dtype == pl.Date
    assert da.to_list()[0] == datetime.date(2024, 2, 1)


def test_casts():
    td = ev('fn.to_date(col("n"), format="%Y-%m-%d")')
    assert td.dtype == pl.Date and td.to_list() == [None] * 4
    assert guard_counts('fn.to_date(col("n"), format="%Y-%m-%d")') == {"R303": 3}
    ti = ev('fn.to_int(col("n"))')
    assert ti.dtype == pl.Int64 and ti.to_list() == [12, None, None, None]
    assert guard_counts('fn.to_int(col("n"))') == {"R303": 2}
    assert guard_counts('fn.to_int("x")') == {"R303": 4}
    tf = ev('fn.to_float(col("n"))')
    assert tf.dtype == pl.Float64 and tf.to_list() == [12.0, None, 3.5, None]
    assert ev('fn.to_int(col("f"))').to_list() == [1, -2, 0, None]
    ts = ev('fn.to_str(col("i"))')
    assert ts.dtype == pl.String and ts.to_list() == ["1", "-2", "0", None]


def test_keyword_arguments_must_be_literals():
    # the checker rejects computed keyword values (L404); only literals reach the runtime
    assert ev('fn.round(col("f"), digits=--1)').to_list() == [1.2, -2.5, 0.0, None]
    with pytest.raises(LatticeInternalError):
        ev('fn.round(col("f"), digits=1 + 0)')


def test_aggregates():
    df = DF.with_columns(pl.lit(1).alias("k"))

    def agg(func: str, src: str | None) -> pl.DataFrame:
        arg = None if src is None else ast.parse(src, mode="eval").body
        c = compile_agg(func, arg)
        return df.group_by("k").agg(c.expr.alias("v"))

    assert agg("sum", 'col("i")')["v"].to_list() == [-1]
    assert agg("sum", 'col("i")')["v"].dtype == pl.Int64
    assert agg("mean", 'col("f")')["v"].to_list() == [pytest.approx(-1.25 / 3)]
    assert agg("min", 'col("s")')["v"].to_list() == ["  Ab "]
    assert agg("max", 'col("d")')["v"].to_list() == [datetime.date(2024, 2, 29)]
    cnt = agg("count", None)
    assert cnt["v"].dtype == pl.Int64 and cnt["v"].to_list() == [4]
    guarded = compile_agg("sum", ast.parse('1 / col("i")', mode="eval").body)
    assert [code for code, _ in guarded.guards] == ["R301"]


def test_unknown_node_is_internal_error():
    with pytest.raises(LatticeInternalError):
        compile_expr(ast.parse("x", mode="eval").body)
    with pytest.raises(LatticeInternalError):
        compile_expr(ast.parse("[1, 2]", mode="eval").body)
    with pytest.raises(LatticeInternalError):
        compile_agg("median", None)
