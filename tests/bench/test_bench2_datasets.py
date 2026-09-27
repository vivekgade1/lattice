from __future__ import annotations

import filecmp
from pathlib import Path

import polars as pl

from bench.bench2_datasets import (
    CUSTOMER_CONTACTS,
    CUSTOMERS2,
    DATASETS2,
    FINANCIAL_STEMS,
    FINANCIAL_SUFFIXES,
    INJECTIONS,
    ORDER_FINANCIALS,
    ORDERS2,
    PRODUCT_PRICES,
    PRODUCTS2,
    bench2_profile,
    load_fixtures2,
    write_fixtures2,
)


def test_dataset_registry_shape() -> None:
    assert set(DATASETS2) == {
        "customers2",
        "orders2",
        "products2",
        "product_prices",
        "customer_contacts",
        "order_financials",
    }
    assert DATASETS2["customers2"].filename == "customers2.csv"
    assert DATASETS2["customers2"].schema_name == "Customers2"
    assert DATASETS2["orders2"].filename == "orders2.csv"
    assert DATASETS2["orders2"].schema_name == "Orders2"
    assert DATASETS2["products2"].filename == "products2.csv"
    assert DATASETS2["products2"].schema_name == "Products2"
    assert DATASETS2["product_prices"].filename == "product_prices.csv"
    assert DATASETS2["product_prices"].schema_name == "ProductPrices"
    assert DATASETS2["customer_contacts"].filename == "customer_contacts.csv"
    assert DATASETS2["customer_contacts"].schema_name == "CustomerContacts"
    assert DATASETS2["order_financials"].filename == "order_financials.csv"
    assert DATASETS2["order_financials"].schema_name == "OrderFinancials"


def test_write_fixtures2_byte_identical_across_runs(tmp_path: Path) -> None:
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    write_fixtures2(dir_a, seed=11)
    write_fixtures2(dir_b, seed=11)
    for ds in DATASETS2.values():
        assert filecmp.cmp(dir_a / ds.filename, dir_b / ds.filename, shallow=False)


def test_declared_keys_are_unique(tmp_path: Path) -> None:
    write_fixtures2(tmp_path, seed=11)
    frames = load_fixtures2(tmp_path)
    for name, ds in DATASETS2.items():
        for key in ds.schema.keys:
            frame = frames[name]
            n_unique = frame.select(list(key)).unique().height
            assert n_unique == frame.height, f"{name} key {key} is not unique"


def test_product_prices_multi_row_products(tmp_path: Path) -> None:
    write_fixtures2(tmp_path, seed=11)
    frames = load_fixtures2(tmp_path)
    counts = frames["product_prices"].group_by("product_id").len()
    multi_row = counts.filter(pl.col("len") > 1)
    assert multi_row.height >= 5


def test_customer_contacts_exactly_one_primary(tmp_path: Path) -> None:
    write_fixtures2(tmp_path, seed=11)
    frames = load_fixtures2(tmp_path)
    contacts = frames["customer_contacts"]
    per_customer = contacts.group_by("customer_id").agg(pl.col("is_primary").sum().alias("n"))
    assert (per_customer["n"] == 1).all()


def test_customers2_leading_zero_zips(tmp_path: Path) -> None:
    write_fixtures2(tmp_path, seed=11)
    frames = load_fixtures2(tmp_path)
    zips = frames["customers2"]["zip"]
    assert zips.dtype == pl.String
    leading_zero = [z for z in zips.to_list() if z.startswith("0")]
    assert len(leading_zero) >= 15


def test_nullable_columns_actually_contain_nulls(tmp_path: Path) -> None:
    write_fixtures2(tmp_path, seed=11)
    frames = load_fixtures2(tmp_path)
    assert frames["customers2"].filter(pl.col("region").is_null()).height >= 8
    assert frames["orders2"].filter(pl.col("discount").is_null()).height >= 1
    assert frames["orders2"].filter(pl.col("rating").is_null()).height >= 1


def test_injections_present_in_products2(tmp_path: Path) -> None:
    write_fixtures2(tmp_path, seed=11)
    frames = load_fixtures2(tmp_path)
    products = frames["products2"]
    combined = "\n".join(products["product_name"].to_list() + products["notes"].to_list())
    for injection in INJECTIONS:
        assert injection in combined, f"missing injection: {injection!r}"


def test_bench2_profile_outputs_and_schemas() -> None:
    profile = bench2_profile()
    assert profile.outputs == frozenset({"out.parquet"})
    assert set(profile.schemas) == {
        "Customers2",
        "Orders2",
        "Products2",
        "ProductPrices",
        "CustomerContacts",
        "OrderFinancials",
    }
    assert profile.schemas["Customers2"] == CUSTOMERS2
    assert profile.schemas["Customers2"].keys == (("customer_id",),)
    assert profile.schemas["Customers2"].nullable == frozenset({"region"})
    assert profile.schemas["Orders2"] == ORDERS2
    assert profile.schemas["Orders2"].keys == (("order_id",),)
    assert profile.schemas["Orders2"].nullable == frozenset({"discount", "rating"})
    assert profile.schemas["Products2"] == PRODUCTS2
    assert profile.schemas["Products2"].keys == (("product_id",),)
    assert profile.schemas["ProductPrices"] == PRODUCT_PRICES
    assert profile.schemas["ProductPrices"].keys == (("product_id", "valid_from"),)
    assert profile.schemas["CustomerContacts"] == CUSTOMER_CONTACTS
    assert profile.schemas["CustomerContacts"].keys == (("contact_id",),)
    assert profile.schemas["OrderFinancials"] == ORDER_FINANCIALS
    assert profile.schemas["OrderFinancials"].keys == (("order_id",),)
    assert profile.enforce_keys is True
    assert profile.enforce_nullable is True


def test_order_financials_has_forty_confusable_float_columns() -> None:
    names = ORDER_FINANCIALS.names()
    assert len(names) == 1 + len(FINANCIAL_STEMS) * len(FINANCIAL_SUFFIXES)
    float_cols = [n for n in names if n != "order_id"]
    assert len(float_cols) == 40
    assert len(set(float_cols)) == 40
    for stem in FINANCIAL_STEMS:
        assert f"{stem}_amount" in float_cols
        assert f"{stem}_amount_usd" in float_cols
        assert f"amount_{stem}" in float_cols
        assert f"{stem}_amount_rounded" in float_cols


def test_order_financials_row_counts_and_types(tmp_path: Path) -> None:
    write_fixtures2(tmp_path, seed=11)
    frames = load_fixtures2(tmp_path)
    financials = frames["order_financials"]
    assert financials.height == frames["orders2"].height
    assert set(financials["order_id"].to_list()) == set(frames["orders2"]["order_id"].to_list())
    for name, dtype in financials.schema.items():
        if name != "order_id":
            assert dtype == pl.Float64, f"{name} is {dtype}, expected Float64"


def test_amount_and_amount_usd_are_duplicates_but_reversed_and_rounded_differ(
    tmp_path: Path,
) -> None:
    write_fixtures2(tmp_path, seed=11)
    frames = load_fixtures2(tmp_path)
    financials = frames["order_financials"]
    for stem in FINANCIAL_STEMS:
        amount = financials[f"{stem}_amount"]
        amount_usd = financials[f"{stem}_amount_usd"]
        assert amount.equals(amount_usd)


def test_row_counts(tmp_path: Path) -> None:
    write_fixtures2(tmp_path, seed=11)
    frames = load_fixtures2(tmp_path)
    assert frames["customers2"].height == 60
    assert frames["orders2"].height == 400
    assert frames["products2"].height == 24
