"""Deterministic benchmark fixtures: schemas, generation, and loading."""

from __future__ import annotations

import datetime
import random
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from lattice.profile import Profile
from lattice.typesys import Scalar, Schema

CUSTOMERS: Schema = Schema.from_pairs(
    [
        ("customer_id", Scalar.INT),
        ("name", Scalar.STR),
        ("region", Scalar.STR),
        ("signup_date", Scalar.DATE),
        ("tier", Scalar.STR),
    ]
)

ORDERS: Schema = Schema.from_pairs(
    [
        ("order_id", Scalar.INT),
        ("customer_id", Scalar.INT),
        ("product_id", Scalar.INT),
        ("qty", Scalar.INT),
        ("unit_price", Scalar.FLOAT),
        ("status", Scalar.STR),
        ("order_date", Scalar.DATE),
        ("discount", Scalar.FLOAT),
    ]
)

PRODUCTS: Schema = Schema.from_pairs(
    [
        ("product_id", Scalar.INT),
        ("product_name", Scalar.STR),
        ("category", Scalar.STR),
        ("cost", Scalar.FLOAT),
    ]
)


@dataclass(frozen=True)
class Dataset:
    name: str
    filename: str
    schema_name: str
    schema: Schema


DATASETS: Mapping[str, Dataset] = {
    "customers": Dataset(
        name="customers", filename="customers.csv", schema_name="Customers", schema=CUSTOMERS
    ),
    "orders": Dataset(name="orders", filename="orders.csv", schema_name="Orders", schema=ORDERS),
    "products": Dataset(
        name="products", filename="products.csv", schema_name="Products", schema=PRODUCTS
    ),
}

_POLARS_TYPES: Mapping[Scalar, pl.DataType] = {
    Scalar.INT: pl.Int64,
    Scalar.FLOAT: pl.Float64,
    Scalar.STR: pl.String,
    Scalar.BOOL: pl.Boolean,
    Scalar.DATE: pl.Date,
}


def polars_schema(schema: Schema) -> dict[str, pl.DataType]:
    return {name: _POLARS_TYPES[scalar] for name, scalar in schema.columns}


_REGIONS = ("north", "south", "east", "west")
_TIERS = ("bronze", "silver", "gold")
_CATEGORIES = ("electronics", "home", "outdoor", "office")
_STATUSES = ("paid", "pending", "cancelled", "refunded")
_STATUS_WEIGHTS = (0.60, 0.15, 0.15, 0.10)

_NUM_CUSTOMERS = 40
_NUM_PRODUCTS = 15
_NUM_ORDERS = 300
# Reserve the last few customer ids so they never receive an order — guarantees
# bench tasks like j05 ("customers with no orders") have non-trivial output.
_NO_ORDER_CUSTOMER_COUNT = 3

_FIRST_NAMES = (
    "Ava",
    "Noah",
    "Mia",
    "Liam",
    "Zoe",
    "Ethan",
    "Ivy",
    "Leo",
    "Nora",
    "Owen",
    "Ruby",
    "Finn",
    "Sara",
    "Kai",
    "Luna",
    "Theo",
    "Elle",
    "Max",
    "Ada",
    "Jude",
)
_LAST_NAMES = (
    "Chen",
    "Patel",
    "Garcia",
    "Kim",
    "Novak",
    "Silva",
    "Haddad",
    "Nguyen",
    "Fischer",
    "Rossi",
    "Okafor",
    "Larsen",
    "Costa",
    "Ivanov",
    "Diaz",
    "Weber",
    "Suzuki",
    "Ali",
    "Moreau",
    "Reyes",
)
_PRODUCT_WORDS = (
    "Aero",
    "Nimbus",
    "Solid",
    "Crest",
    "Vivid",
    "Quartz",
    "Ember",
    "Drift",
    "Pulse",
    "Ridge",
    "Arc",
    "Halo",
    "Terra",
    "Nova",
    "Onyx",
)
_PRODUCT_NOUNS = (
    "Chair",
    "Lamp",
    "Desk",
    "Speaker",
    "Backpack",
    "Bottle",
    "Jacket",
    "Monitor",
    "Tent",
    "Grill",
    "Blender",
    "Headset",
    "Mat",
    "Shelf",
    "Cooler",
)


def _random_date(rng: random.Random, start: datetime.date, end: datetime.date) -> datetime.date:
    span = (end - start).days
    return start + datetime.timedelta(days=rng.randint(0, span))


def _maybe_pad(rng: random.Random, s: str) -> str:
    """Occasionally add leading/trailing whitespace to a name."""
    choice = rng.random()
    if choice < 0.1:
        return f"  {s}"
    if choice < 0.2:
        return f"{s}  "
    return s


def _generate_customers(rng: random.Random) -> list[dict[str, object]]:
    null_region_indices = set(rng.sample(range(_NUM_CUSTOMERS), 2))
    rows: list[dict[str, object]] = []
    for i in range(_NUM_CUSTOMERS):
        customer_id = i + 1
        first = rng.choice(_FIRST_NAMES)
        last = rng.choice(_LAST_NAMES)
        name = _maybe_pad(rng, f"{first} {last}")
        region = None if i in null_region_indices else rng.choice(_REGIONS)
        signup_date = _random_date(rng, datetime.date(2022, 1, 1), datetime.date(2024, 12, 31))
        tier = rng.choice(_TIERS)
        rows.append(
            {
                "customer_id": customer_id,
                "name": name,
                "region": region,
                "signup_date": signup_date,
                "tier": tier,
            }
        )
    return rows


def _generate_products(rng: random.Random) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for i in range(_NUM_PRODUCTS):
        product_id = i + 1
        product_name = f"{rng.choice(_PRODUCT_WORDS)} {rng.choice(_PRODUCT_NOUNS)}"
        category = rng.choice(_CATEGORIES)
        cost = round(rng.uniform(2.0, 60.0), 2)
        rows.append(
            {
                "product_id": product_id,
                "product_name": product_name,
                "category": category,
                "cost": cost,
            }
        )
    return rows


def _generate_orders(
    rng: random.Random, products: list[dict[str, object]]
) -> list[dict[str, object]]:
    eligible_customer_ids = list(range(1, _NUM_CUSTOMERS - _NO_ORDER_CUSTOMER_COUNT + 1))
    rows: list[dict[str, object]] = []
    zero_qty_indices = set(rng.sample(range(_NUM_ORDERS), 6))
    null_discount_indices = set(i for i in range(_NUM_ORDERS) if rng.random() < 0.20)
    for i in range(_NUM_ORDERS):
        order_id = i + 1
        customer_id = rng.choice(eligible_customer_ids)
        product = rng.choice(products)
        product_id = product["product_id"]
        cost = float(product["cost"])
        qty = 0 if i in zero_qty_indices else rng.randint(1, 12)
        unit_price = round(cost * rng.uniform(1.3, 2.6), 2)
        status = rng.choices(_STATUSES, weights=_STATUS_WEIGHTS, k=1)[0]
        order_date = _random_date(rng, datetime.date(2023, 1, 1), datetime.date(2024, 12, 31))
        discount = None if i in null_discount_indices else round(rng.uniform(0.0, 0.3), 2)
        rows.append(
            {
                "order_id": order_id,
                "customer_id": customer_id,
                "product_id": product_id,
                "qty": qty,
                "unit_price": unit_price,
                "status": status,
                "order_date": order_date,
                "discount": discount,
            }
        )
    return rows


def _rows_to_frame(rows: list[dict[str, object]], schema: Schema) -> pl.DataFrame:
    columns: dict[str, list[object]] = {name: [] for name in schema.names()}
    for row in rows:
        for name in schema.names():
            columns[name].append(row[name])
    return pl.DataFrame(columns, schema=polars_schema(schema))


def _write_csv(frame: pl.DataFrame, path: Path) -> None:
    frame.write_csv(path, datetime_format="%Y-%m-%d")


def write_fixtures(directory: Path, seed: int = 7) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    customer_rows = _generate_customers(rng)
    product_rows = _generate_products(rng)
    order_rows = _generate_orders(rng, product_rows)

    customers = _rows_to_frame(customer_rows, CUSTOMERS)
    products = _rows_to_frame(product_rows, PRODUCTS)
    orders = _rows_to_frame(order_rows, ORDERS)

    _write_csv(customers, directory / DATASETS["customers"].filename)
    _write_csv(products, directory / DATASETS["products"].filename)
    _write_csv(orders, directory / DATASETS["orders"].filename)


def load_fixtures(directory: Path) -> dict[str, pl.DataFrame]:
    return {
        name: pl.read_csv(directory / ds.filename, schema=polars_schema(ds.schema))
        for name, ds in DATASETS.items()
    }


def bench_profile() -> Profile:
    return Profile(
        schemas={
            "Customers": CUSTOMERS,
            "Orders": ORDERS,
            "Products": PRODUCTS,
        }
    )
