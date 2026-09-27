"""Nullable columns and the aggregate null policy (L421)."""

from __future__ import annotations

import ast
from pathlib import Path

import polars as pl
import pytest

from lattice.check import check_source
from lattice.check.nullability import expr_nullable
from lattice.profile import Profile
from lattice.runtime.exec import run_source
from lattice.typesys import Scalar, Schema

INT, F, S = Scalar.INT, Scalar.FLOAT, Scalar.STR

ORDERS = Schema.from_pairs(
    [("order_id", INT), ("customer_id", INT), ("amount", F), ("discount", F), ("code", S)],
    keys=[("order_id",)],
    nullable=["discount"],
)
RATINGS = Schema.from_pairs(
    [("customer_id", INT), ("rating", F), ("region", S)], keys=[("customer_id",)]
)
NULLABLE = Profile(schemas={"Orders": ORDERS, "Ratings": RATINGS})
PLAIN = Profile(schemas={"Orders": Schema(ORDERS.columns), "Ratings": Schema(RATINGS.columns)})

H = "# lattice: v1\n"
LOADS = (
    'orders = load("orders.csv", schema=Orders)\nratings = load("ratings.csv", schema=Ratings)\n'
)


def errors(body: str, profile: Profile = NULLABLE):
    diags = check_source(H + LOADS + body, profile).diagnostics
    return [d for d in diags if d.severity.value == "error"]


def codes(body: str, profile: Profile = NULLABLE) -> list[str]:
    return [d.code for d in errors(body, profile)]


def grouped(agg: str, table: str = "orders", by: str = '"customer_id"') -> str:
    return f'g = group({table}, by=[{by}], v={agg})\nwrite(g, "out.parquet")\n'


# ---- expr_nullable ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ('col("d")', True),
        ('col("a")', False),
        ("1.0", False),
        ('coalesce(col("d"), 0.0)', False),
        ('coalesce(col("d"), col("d"))', True),
        ('when(col("a") > 1, then=col("d"), otherwise=0.0)', True),
        ('when(col("d") > 1, then=col("a"), otherwise=0.0)', False),
        ('is_null(col("d"))', False),
        ('when(is_null(col("d")), then=0.0, otherwise=col("d"))', False),
        ('when(~is_null(col("d")), then=col("d"), otherwise=0.0)', False),
        ('when(is_null(col("d")), then=0.0, otherwise=col("d") * 2)', False),
        ('when(is_null(col("d")), then=col("d"), otherwise=0.0)', True),
        ('when(~is_null(col("d")), then=0.0, otherwise=col("d"))', True),
        ('when(is_null(col("d")), then=col("d2"), otherwise=col("d"))', True),
        ('when(is_null(col("a")), then=0.0, otherwise=col("d"))', True),
        ("when(is_null(k), then=0.0, otherwise=k)", False),
        ('when(is_null(col("d") * 2), then=0.0, otherwise=col("d") * 2)', False),
        ('when(is_null(col("d") * 2), then=0.0, otherwise=col("d"))', True),
        ('fn.to_int(col("s"))', True),
        ('fn.abs(col("a"))', False),
        ('fn.abs(col("d"))', True),
        ('col("a") * col("d")', True),
        ('col("a") * 2', False),
        ('-col("d")', True),
        ('col("d") > 1', True),
        ("k", True),
        ("j", False),
    ],
)
def test_expr_nullable(src: str, expected: bool) -> None:
    node = ast.parse(src, mode="eval").body
    assert expr_nullable(node, frozenset({"d", "d2"}), {"k": True, "j": False}) is expected


# ---- L421 ------------------------------------------------------------------------


def test_mean_over_nullable_column_is_one_l421() -> None:
    errs = errors(grouped('agg.mean(col("discount"))'))
    assert [d.code for d in errs] == ["L421"]
    assert errs[0].message == (
        "aggregating 'discount' which can be missing: say how to treat missing values"
    )
    assert errs[0].hint == (
        'use agg.mean(coalesce(col("discount"), 0.0)) to count missing as 0, or '
        'agg.mean(col("discount"), nulls="skip") to ignore missing values'
    )


@pytest.mark.parametrize("fn", ["min", "max"])
def test_min_max_over_nullable_column_is_l421(fn: str) -> None:
    assert codes(grouped(f'agg.{fn}(col("discount"))')) == ["L421"]


def test_nulls_skip_is_ok() -> None:
    assert codes(grouped('agg.mean(col("discount"), nulls="skip")')) == []


def test_coalesce_is_ok() -> None:
    assert codes(grouped('agg.mean(coalesce(col("discount"), 0.0))')) == []


@pytest.mark.parametrize("agg", ['agg.sum(col("discount"))', 'agg.count(col("discount"))'])
def test_sum_and_count_need_no_policy(agg: str) -> None:
    assert codes(grouped(agg)) == []


def test_non_nullable_column_needs_no_policy() -> None:
    assert codes(grouped('agg.mean(col("amount"))')) == []


def test_expression_message_uses_source_text() -> None:
    errs = errors(grouped('agg.max(col("amount") - col("discount"))'))
    assert [d.code for d in errs] == ["L421"]
    assert '\'col("amount") - col("discount")\'' in errs[0].message


@pytest.mark.parametrize("value", ['"drop"', "True", '"SKIP"'])
def test_nulls_must_be_skip(value: str) -> None:
    assert codes(grouped(f'agg.mean(col("discount"), nulls={value})')) == ["L404"]


@pytest.mark.parametrize("fn", ["sum", "count"])
def test_nulls_only_on_mean_min_max(fn: str) -> None:
    assert codes(grouped(f'agg.{fn}(col("amount"), nulls="skip")')) == ["L404"]


def test_nulls_skip_allowed_on_non_nullable_and_plain_profile() -> None:
    assert codes(grouped('agg.mean(col("amount"), nulls="skip")')) == []
    assert codes(grouped('agg.mean(col("amount"), nulls="skip")'), PLAIN) == []


def test_left_join_right_columns_become_nullable() -> None:
    # Review focus 2
    body = (
        'j = join(orders, ratings, on=["customer_id"], how="left")\n'
        'g = group(j, by=["customer_id"], v=agg.mean(col("rating")))\n'
        'write(g, "out.parquet")\n'
    )
    assert codes(body) == ["L421"]


def test_left_join_key_column_is_not_nullable() -> None:
    body = (
        'j = join(orders, ratings, on=["customer_id"], how="left")\n'
        'g = group(j, by=["code"], v=agg.max(col("customer_id")))\n'
        'write(g, "out.parquet")\n'
    )
    assert codes(body) == []


def test_inner_join_preserves_nullability() -> None:
    body = (
        'j = join(orders, ratings, on=["customer_id"], how="inner")\n'
        'g = group(j, by=["region"], v=agg.mean(col("rating")), w=agg.mean(col("discount")))\n'
        'write(g, "out.parquet")\n'
    )
    errs = errors(body)
    assert [d.code for d in errs] == ["L421"]
    assert "'discount'" in errs[0].message


def test_cross_left_join_right_columns_nullable() -> None:
    body = (
        'tot = group(ratings, by=[], best=agg.max(col("customer_id")))\n'
        'j = join(orders, tot, on=[], how="left")\n'
        'g = group(j, by=["code"], v=agg.max(col("best")))\n'
        'write(g, "out.parquet")\n'
    )
    assert codes(body) == ["L421"]


def test_derive_null_producing_fn_then_mean_is_l421() -> None:
    body = (
        'd = derive(orders, n=fn.to_int(col("code")))\n'
        'g = group(d, by=["customer_id"], v=agg.mean(col("n")))\n'
        'write(g, "out.parquet")\n'
    )
    assert codes(body) == ["L421"]


def test_overwrite_nullable_column_with_coalesce_then_mean_ok() -> None:
    body = (
        'd = derive(orders, discount=coalesce(col("discount"), 0.0))\n'
        'g = group(d, by=["customer_id"], v=agg.mean(col("discount")))\n'
        'write(g, "out.parquet")\n'
    )
    assert codes(body) == []


def test_select_and_rename_carry_nullability() -> None:
    body = (
        'd = select(orders, cols=["customer_id", "discount"])\n'
        'r = rename(d, discount="disc")\n'
        'g = group(r, by=["customer_id"], v=agg.mean(col("disc")))\n'
        'write(g, "out.parquet")\n'
    )
    assert codes(body) == ["L421"]


def test_group_mean_result_is_nullable_and_sum_is_not() -> None:
    body = (
        'g = group(orders, by=["customer_id"], m=agg.mean(col("amount")), '
        's=agg.sum(col("discount")))\n'
        'h = group(g, by=[], a=agg.max(col("m")), b=agg.max(col("s")))\n'
        'write(h, "out.parquet")\n'
    )
    errs = errors(body)
    assert [d.code for d in errs] == ["L421"]
    assert "'m'" in errs[0].message


def test_nullable_named_constant() -> None:
    body = (
        'k = fn.to_float("x")\n'
        'd = derive(orders, z=col("amount") + k)\n'
        'g = group(d, by=["customer_id"], v=agg.mean(col("z")))\n'
        'write(g, "out.parquet")\n'
    )
    assert codes(body) == ["L421"]


def test_step_param_uses_declared_nullability() -> None:
    body = (
        "@step\n"
        "def f(o: Table[Orders]) -> Table:\n"
        '    g = group(o, by=["customer_id"], v=agg.mean(col("discount")))\n'
        "    return g\n"
        "r = f(orders)\n"
        'write(r, "out.parquet")\n'
    )
    assert codes(body) == ["L421"]


def test_plain_profile_never_reports_l421() -> None:
    assert codes(grouped('agg.mean(col("discount"))'), PLAIN) == []


# ---- runtime ------------------------------------------------------------------------


def test_nulls_skip_runtime_equals_polars_mean(tmp_path: Path) -> None:
    (tmp_path / "orders.csv").write_text(
        "order_id,customer_id,amount,discount,code\n"
        "1,1,10.0,0.5,a\n2,1,20.0,,b\n3,2,5.0,,c\n4,2,6.0,1.5,d\n5,3,1.0,,e\n"
    )
    (tmp_path / "ratings.csv").write_text("customer_id,rating,region\n1,4.0,x\n")
    body = (
        'g = group(orders, by=["customer_id"], v=agg.mean(col("discount"), nulls="skip"), '
        'w=agg.min(col("discount"), nulls="skip"))\n'
        'write(g, "out.parquet")\n'
    )
    result, run = run_source(H + LOADS + body, NULLABLE, tmp_path)
    assert result.ok, result.diagnostics
    assert run is not None
    got = run.outputs["out.parquet"].sort("customer_id")
    assert got["v"].to_list() == [0.5, 1.5, None]
    assert got["w"].to_list() == [0.5, 1.5, None]
    expected = (
        pl.read_csv(tmp_path / "orders.csv")
        .group_by("customer_id", maintain_order=True)
        .agg(pl.col("discount").mean())
        .sort("customer_id")
    )
    assert got["v"].to_list() == expected["discount"].to_list()


@pytest.mark.parametrize(
    "pred",
    [
        '~is_null(col("discount"))',
        'col("discount") > 0.0',
        '(col("amount") > 1.0) & (col("discount") < 1.0)',
    ],
)
def test_where_guaranteeing_presence_clears_nullability(pred: str) -> None:
    body = (
        f"w = where(orders, {pred})\n"
        'g = group(w, by=["customer_id"], v=agg.mean(col("discount")))\n'
        'write(g, "out.parquet")\n'
    )
    assert codes(body) == []


@pytest.mark.parametrize(
    "pred", ['is_null(col("discount"))', '(col("amount") > 1.0) | (col("discount") < 1.0)']
)
def test_where_not_guaranteeing_presence_keeps_nullability(pred: str) -> None:
    body = (
        f"w = where(orders, {pred})\n"
        'g = group(w, by=["customer_id"], v=agg.mean(col("discount")))\n'
        'write(g, "out.parquet")\n'
    )
    assert codes(body) == ["L421"]


# ---- when(is_null(E), ...) narrows E --------------------------------------------------


def test_when_is_null_fill_in_aggregate_is_ok() -> None:
    fill = 'when(is_null(col("discount")), then=0.0, otherwise=col("discount"))'
    assert codes(grouped(f"agg.mean({fill})")) == []


def test_when_is_null_fill_in_derive_clears_nullability() -> None:
    body = (
        'd = derive(orders, disc=when(~is_null(col("discount")), then=col("discount"), '
        "otherwise=0.0))\n"
        'g = group(d, by=["customer_id"], v=agg.mean(col("disc")))\n'
        'write(g, "out.parquet")\n'
    )
    assert codes(body) == []


def test_when_is_null_on_other_branch_keeps_nullability() -> None:
    fill = 'when(is_null(col("discount")), then=col("discount"), otherwise=0.0)'
    assert codes(grouped(f"agg.mean({fill})")) == ["L421"]


# ---- agg.first / agg.last ---------------------------------------------------------------


def _then_mean(agg: str, by: str = '"customer_id"') -> str:
    return (
        f"g = group(orders, by=[{by}], v={agg})\n"
        'h = group(g, by=[], m=agg.mean(col("v")))\n'
        'write(h, "out.parquet")\n'
    )


@pytest.mark.parametrize("fn", ["first", "last"])
def test_first_last_over_nullable_value_needs_no_policy(fn: str) -> None:
    assert codes(grouped(f'agg.{fn}(col("discount"), order=col("amount"))')) == []


@pytest.mark.parametrize(
    ("agg", "nullable"),
    [
        ('agg.last(col("amount"), order=col("order_id"))', False),
        ('agg.last(col("discount"), order=col("order_id"))', True),
        ('agg.first(col("amount"), order=col("discount"))', True),
    ],
)
def test_first_last_result_nullability(agg: str, nullable: bool) -> None:
    assert codes(_then_mean(agg)) == (["L421"] if nullable else [])


def test_first_last_whole_table_is_nullable() -> None:
    # by=[] over an empty input yields one row with a missing value
    assert codes(_then_mean('agg.first(col("amount"), order=col("order_id"))', by="")) == ["L421"]


def test_first_last_runtime(tmp_path: Path) -> None:
    (tmp_path / "orders.csv").write_text(
        "order_id,customer_id,amount,discount,code\n"
        "5,1,10.0,0.5,a\n1,1,20.0,,b\n5,1,30.0,,c\n1,1,40.0,,d\n"
        "7,2,1.0,,e\n3,2,,,f\n"
    )
    (tmp_path / "ratings.csv").write_text("customer_id,rating,region\n1,4.0,x\n")
    body = (
        'g = group(orders, by=["customer_id"], '
        'f=agg.first(col("code"), order=col("order_id")), '
        'l=agg.last(col("code"), order=col("order_id")), '
        'fd=agg.first(col("code"), order=col("discount")), '
        'la=agg.last(col("amount"), order=col("order_id") * -1))\n'
        'write(g, "out.parquet")\n'
    )
    result, run = run_source(H + LOADS + body, NULLABLE, tmp_path)
    assert result.ok, result.diagnostics
    assert run is not None
    got = run.outputs["out.parquet"].sort("customer_id")
    assert got["f"].to_list() == ["b", "f"]  # tie on 1: earliest row wins
    assert got["l"].to_list() == ["a", "e"]  # tie on 5: earliest row wins
    assert got["fd"].to_list() == ["a", None]  # null orders ignored; all-null -> null
    assert got["la"].to_list() == [20.0, None]  # value at the extreme row may be null
    assert got.schema["la"] == pl.Float64
