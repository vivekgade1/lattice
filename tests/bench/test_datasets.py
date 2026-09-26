from __future__ import annotations

import filecmp
from pathlib import Path

import polars as pl

from bench.datasets import (
    CUSTOMERS,
    DATASETS,
    ORDERS,
    PRODUCTS,
    Dataset,
    bench_profile,
    load_fixtures,
    polars_schema,
    write_fixtures,
)
from lattice.typesys import Scalar


def test_dataset_registry_shape() -> None:
    assert set(DATASETS) == {"customers", "orders", "products"}
    assert DATASETS["customers"] == Dataset(
        name="customers", filename="customers.csv", schema_name="Customers", schema=CUSTOMERS
    )
    assert DATASETS["orders"].schema_name == "Orders"
    assert DATASETS["products"].schema_name == "Products"


def test_schema_columns() -> None:
    assert CUSTOMERS.names() == ("customer_id", "name", "region", "signup_date", "tier")
    assert ORDERS.names() == (
        "order_id",
        "customer_id",
        "product_id",
        "qty",
        "unit_price",
        "status",
        "order_date",
        "discount",
    )
    assert PRODUCTS.names() == ("product_id", "product_name", "category", "cost")


def test_polars_schema_mapping() -> None:
    mapping = polars_schema(CUSTOMERS)
    assert mapping["customer_id"] == pl.Int64
    assert mapping["name"] == pl.String
    assert mapping["region"] == pl.String
    assert mapping["signup_date"] == pl.Date
    assert mapping["tier"] == pl.String

    order_mapping = polars_schema(ORDERS)
    assert order_mapping["qty"] == pl.Int64
    assert order_mapping["unit_price"] == pl.Float64


def test_write_fixtures_byte_identical_across_runs(tmp_path: Path) -> None:
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    write_fixtures(dir_a, seed=7)
    write_fixtures(dir_b, seed=7)
    for ds in DATASETS.values():
        assert filecmp.cmp(dir_a / ds.filename, dir_b / ds.filename, shallow=False)


def test_row_counts(tmp_path: Path) -> None:
    write_fixtures(tmp_path, seed=7)
    frames = load_fixtures(tmp_path)
    assert frames["customers"].height == 40
    assert frames["orders"].height == 300
    assert frames["products"].height == 15


def test_load_fixtures_dtypes(tmp_path: Path) -> None:
    write_fixtures(tmp_path, seed=7)
    frames = load_fixtures(tmp_path)
    for name, ds in DATASETS.items():
        expected = polars_schema(ds.schema)
        actual = dict(frames[name].schema)
        assert actual == expected, f"{name}: {actual} != {expected}"


def test_orders_content_rules(tmp_path: Path) -> None:
    write_fixtures(tmp_path, seed=7)
    frames = load_fixtures(tmp_path)
    orders = frames["orders"]
    assert orders.filter(pl.col("qty") == 0).height >= 5
    assert orders.filter(pl.col("discount").is_null()).height >= 1


def test_customers_null_regions(tmp_path: Path) -> None:
    write_fixtures(tmp_path, seed=7)
    frames = load_fixtures(tmp_path)
    customers = frames["customers"]
    assert customers.filter(pl.col("region").is_null()).height == 2


def test_referential_integrity(tmp_path: Path) -> None:
    write_fixtures(tmp_path, seed=7)
    frames = load_fixtures(tmp_path)
    customer_ids = set(frames["customers"]["customer_id"].to_list())
    product_ids = set(frames["products"]["product_id"].to_list())
    orders = frames["orders"]
    assert set(orders["customer_id"].to_list()) <= customer_ids
    assert set(orders["product_id"].to_list()) <= product_ids


def test_bench_profile() -> None:
    profile = bench_profile()
    assert set(profile.schemas) == {"Customers", "Orders", "Products"}
    assert profile.schemas["Customers"] == CUSTOMERS
    assert profile.schemas["Orders"] == ORDERS
    assert profile.schemas["Products"] == PRODUCTS


def test_scalar_types_used() -> None:
    assert CUSTOMERS.get("customer_id") == Scalar.INT
    assert ORDERS.get("unit_price") == Scalar.FLOAT
    assert PRODUCTS.get("cost") == Scalar.FLOAT
