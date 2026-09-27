"""Declared keys and the join fan-out check (L420)."""

from __future__ import annotations

import pytest

from lattice.check import check_source
from lattice.profile import Profile
from lattice.typesys import Scalar, Schema, TableT

INT, F, S, D = Scalar.INT, Scalar.FLOAT, Scalar.STR, Scalar.DATE

ORDERS = Schema.from_pairs(
    [("order_id", INT), ("product_id", INT), ("customer_id", INT), ("qty", INT)],
    keys=[("order_id",)],
)
PRODUCTS = Schema.from_pairs(
    [("product_id", INT), ("name", S), ("category", S)], keys=[("product_id",)]
)
PRICES = Schema.from_pairs(
    [("product_id", INT), ("valid_from", D), ("price", F)],
    keys=[("product_id", "valid_from")],
)
CONTACTS = Schema.from_pairs([("customer_id", INT), ("email", S)])

KEYED = Profile(
    schemas={"Orders": ORDERS, "Products": PRODUCTS, "Prices": PRICES, "Contacts": CONTACTS}
)
UNKEYED = Profile(
    schemas={
        "Orders": Schema(ORDERS.columns),
        "Products": Schema(PRODUCTS.columns),
        "Prices": Schema(PRICES.columns),
        "Contacts": CONTACTS,
    }
)

H = "# lattice: v1\n"
LOADS = (
    'orders = load("orders.csv", schema=Orders)\n'
    'products = load("products.csv", schema=Products)\n'
    'prices = load("prices.csv", schema=Prices)\n'
    'contacts = load("contacts.csv", schema=Contacts)\n'
)


def diags(body: str, profile: Profile = KEYED):
    return check_source(H + LOADS + body, profile).diagnostics


def errors(body: str, profile: Profile = KEYED):
    return [d for d in diags(body, profile) if d.severity.value == "error"]


def codes(body: str, profile: Profile = KEYED) -> list[str]:
    return [d.code for d in errors(body, profile)]


def test_schema_metadata_excluded_from_equality() -> None:
    a = Schema.from_pairs([("x", INT)], keys=[("x",)], nullable=["x"])
    b = Schema.from_pairs([("x", INT)])
    assert a == b and hash(a) == hash(b)
    assert a.keys == (("x",),) and a.nullable == frozenset({"x"})
    assert TableT(a, keys=(("x",),)) == TableT(b)


def test_schema_rejects_keys_naming_unknown_columns() -> None:
    with pytest.raises(ValueError):
        Schema.from_pairs([("x", INT)], keys=[("y",)])
    with pytest.raises(ValueError):
        Schema.from_pairs([("x", INT)], nullable=["y"])


def test_profile_enforce_flags() -> None:
    assert KEYED.enforce_keys and not UNKEYED.enforce_keys
    assert not KEYED.enforce_nullable


def test_keyed_right_side_is_ok() -> None:
    body = 'j = join(orders, products, on=["product_id"], how="left")\nwrite(j, "out.parquet")\n'
    assert codes(body) == []


def test_non_unique_right_side_is_one_l420_naming_keys() -> None:
    body = 'j = join(orders, prices, on=["product_id"], how="left")\nwrite(j, "out.parquet")\n'
    errs = errors(body)
    assert [d.code for d in errs] == ["L420"]
    d = errs[0]
    assert d.message == (
        "join can repeat rows of the left table: 'prices' is not unique on (product_id); "
        "known keys of it: (product_id, valid_from)"
    )
    assert d.hint is not None and 'fanout="allow"' in d.hint
    assert "group(prices, by=[...], ...)" in d.hint
    assert d.span.line == 6  # the join call


def test_unkeyed_right_side_reports_none() -> None:
    body = 'j = join(orders, contacts, on=["customer_id"], how="inner")\nwrite(j, "out.parquet")\n'
    errs = errors(body)
    assert [d.code for d in errs] == ["L420"]
    assert errs[0].message.endswith("known keys of it: none")


def test_group_then_join_is_ok() -> None:
    body = (
        'latest = group(prices, by=["product_id"], price=agg.max(col("price")))\n'
        'j = join(orders, latest, on=["product_id"], how="left")\n'
        'write(j, "out.parquet")\n'
    )
    assert codes(body) == []


def test_composite_key_joined_on_prefix_is_l420() -> None:
    body = 'j = join(products, prices, on=["product_id"], how="inner")\nwrite(j, "out.parquet")\n'
    assert codes(body) == ["L420"]


def test_composite_key_fully_covered_is_ok() -> None:
    body = (
        'p2 = rename(prices, price="p2")\n'
        'j = join(prices, p2, on=["product_id", "valid_from"], how="inner")\n'
        'write(j, "out.parquet")\n'
    )
    assert codes(body) == []


def test_fanout_allow_suppresses() -> None:
    body = (
        'j = join(orders, prices, on=["product_id"], how="left", fanout="allow")\n'
        'write(j, "out.parquet")\n'
    )
    assert codes(body) == []


@pytest.mark.parametrize("value", ['"deny"', "True", "1", '"ALLOW"'])
def test_fanout_must_be_allow(value: str) -> None:
    body = (
        f'j = join(orders, prices, on=["product_id"], how="left", fanout={value})\n'
        'write(j, "out.parquet")\n'
    )
    assert codes(body) == ["L404"]


def test_fanout_allowed_on_unkeyed_profile_too() -> None:
    body = (
        'j = join(orders, prices, on=["product_id"], how="left", fanout="allow")\n'
        'write(j, "out.parquet")\n'
    )
    assert codes(body, UNKEYED) == []


def test_derive_overwriting_key_column_drops_key() -> None:
    # Review focus 1: overwriting product_id makes it no longer a known key
    body = (
        'p2 = derive(products, product_id=col("product_id") % 3)\n'
        'j = join(orders, p2, on=["product_id"], how="left")\n'
        'write(j, "out.parquet")\n'
    )
    assert codes(body) == ["L420"]


def test_derive_of_other_columns_keeps_key() -> None:
    body = (
        'p2 = derive(products, name2=fn.upper(col("name")))\n'
        'j = join(orders, p2, on=["product_id"], how="left")\n'
        'write(j, "out.parquet")\n'
    )
    assert codes(body) == []


def test_select_dropping_key_column_removes_key() -> None:
    body = (
        'p2 = select(prices, cols=["product_id", "price"])\n'
        'j = join(orders, p2, on=["product_id"], how="left")\n'
        'write(j, "out.parquet")\n'
    )
    assert codes(body) == ["L420"]


def test_select_keeping_key_keeps_it() -> None:
    body = (
        'p2 = select(products, cols=["category", "product_id"])\n'
        'j = join(orders, p2, on=["product_id"], how="left")\n'
        'write(j, "out.parquet")\n'
    )
    assert codes(body) == []


def test_rename_renames_keys() -> None:
    body = (
        'p2 = rename(products, product_id="pid")\n'
        'o2 = rename(orders, product_id="pid")\n'
        'j = join(o2, p2, on=["pid"], how="left")\n'
        'write(j, "out.parquet")\n'
    )
    assert codes(body) == []


def test_where_sort_limit_preserve_keys() -> None:
    body = (
        'a = where(products, col("category") == "x")\n'
        'b = sort(a, by=["name"])\n'
        "c = limit(b, n=5)\n"
        'j = join(orders, c, on=["product_id"], how="inner")\n'
        'write(j, "out.parquet")\n'
    )
    assert codes(body) == []


def test_join_result_keeps_left_keys_only_when_fanout_safe() -> None:
    # orders ⋈ products (safe) keeps order_id as a key: joining on it later is fine
    safe = (
        'j = join(orders, products, on=["product_id"], how="left")\n'
        'o2 = select(orders, cols=["order_id", "qty"])\n'
        'o3 = rename(o2, qty="qty2")\n'
        'k = join(o3, j, on=["order_id"], how="inner")\n'
        'write(k, "out.parquet")\n'
    )
    assert codes(safe) == []
    # an allowed fan-out join has no known keys
    fanned = (
        'j = join(orders, prices, on=["product_id"], how="left", fanout="allow")\n'
        'o2 = select(orders, cols=["order_id"])\n'
        'k = join(o2, j, on=["order_id"], how="inner")\n'
        'write(k, "out.parquet")\n'
    )
    assert codes(fanned) == ["L420"]


def test_group_by_empty_right_is_single_row_and_fine() -> None:
    body = (
        "tot = group(orders, by=[], n=agg.count())\n"
        'j = join(orders, tot, on=[], how="inner")\n'
        'write(j, "out.parquet")\n'
    )
    assert codes(body) == []


def test_step_param_accepts_table_with_different_keys() -> None:
    body = (
        "@step\n"
        "def f(p: Table[Products]) -> Table:\n"
        '    q = where(p, col("category") == "x")\n'
        "    return q\n"
        'p2 = derive(products, product_id=col("product_id") + 0)\n'
        "r = f(p2)\n"
        'write(r, "out.parquet")\n'
    )
    assert codes(body) == []


def test_step_result_carries_body_keys() -> None:
    body = (
        "@step\n"
        "def latest(p: Table[Prices]) -> Table:\n"
        '    g = group(p, by=["product_id"], price=agg.max(col("price")))\n'
        "    return g\n"
        "lp = latest(prices)\n"
        'j = join(orders, lp, on=["product_id"], how="left")\n'
        'write(j, "out.parquet")\n'
    )
    assert codes(body) == []


def test_step_params_have_no_known_keys() -> None:
    # a step body is checked once for every caller, so it cannot assume the
    # argument's keys: a join on a table parameter needs a key it derives itself
    body = (
        "@step\n"
        "def f(o: Table[Orders], p: Table[Products]) -> Table:\n"
        '    j = join(o, p, on=["product_id"], how="left")\n'
        "    return j\n"
        "r = f(orders, products)\n"
        'write(r, "out.parquet")\n'
    )
    assert codes(body) == ["L420"]


def test_unkeyed_profile_never_reports_l420() -> None:
    body = 'j = join(orders, prices, on=["product_id"], how="left")\nwrite(j, "out.parquet")\n'
    assert codes(body, UNKEYED) == []


def test_bench_profile_declares_no_keys() -> None:
    from bench.datasets import bench_profile

    p = bench_profile()
    assert not p.enforce_keys and not p.enforce_nullable and p.outputs is None


def test_accepted_fanout_program_runs(tmp_path) -> None:
    from lattice.runtime.exec import run_source

    (tmp_path / "orders.csv").write_text("order_id,product_id,customer_id,qty\n1,1,1,2\n2,2,1,3\n")
    (tmp_path / "products.csv").write_text("product_id,name,category\n1,a,x\n2,b,y\n")
    (tmp_path / "prices.csv").write_text(
        "product_id,valid_from,price\n1,2024-01-01,1.0\n1,2024-02-01,2.0\n2,2024-01-01,3.0\n"
    )
    (tmp_path / "contacts.csv").write_text("customer_id,email\n1,a@x\n")
    src = (
        H
        + LOADS
        + 'j = join(orders, prices, on=["product_id"], how="left", fanout="allow")\n'
        + 'write(j, "out.parquet")\n'
    )
    result, run = run_source(src, KEYED, tmp_path)
    assert result.ok and run is not None
    assert run.outputs["out.parquet"].height == 3
