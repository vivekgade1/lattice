"""A small multi-vendor-like profile and a hand-built SQLite database for the spec tests.

Order.status and User.role are one_of columns stored by member name ("PENDING" for
"pending"), as an ORM storing a Python Enum would."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lattice.profile import ApiProfile, Profile, RoleColumn
from lattice.typesys import Scalar, Schema

ORDER_STATES = ("pending", "confirmed", "shipped", "delivered", "cancelled")

USER = Schema.from_pairs(
    [
        ("id", Scalar.INT),
        ("email", Scalar.STR),
        ("role", Scalar.STR),
        ("token", Scalar.STR),
        ("is_active", Scalar.BOOL),
    ],
    keys=[("id",)],
    one_of={"role": ("buyer", "vendor", "admin")},
    one_of_stored={"role": ("BUYER", "VENDOR", "ADMIN")},
)
VENDOR = Schema.from_pairs(
    [("id", Scalar.INT), ("user_id", Scalar.INT), ("business_name", Scalar.STR)],
    keys=[("id",)],
    nullable=["user_id"],
    owner="user_id",
)
PRODUCT = Schema.from_pairs(
    [
        ("id", Scalar.INT),
        ("vendor_id", Scalar.INT),
        ("name", Scalar.STR),
        ("description", Scalar.STR),
        ("category", Scalar.STR),
        ("price", Scalar.FLOAT),
        ("stock_quantity", Scalar.INT),
        ("is_active", Scalar.BOOL),
        ("created_at", Scalar.DATETIME),
    ],
    keys=[("id",)],
    nullable=["description", "stock_quantity"],
    owner_via=("vendor_id", "Vendor"),
)
ORDER = Schema.from_pairs(
    [
        ("id", Scalar.INT),
        ("user_id", Scalar.INT),
        ("status", Scalar.STR),
        ("total_amount", Scalar.FLOAT),
    ],
    keys=[("id",)],
    nullable=["total_amount"],
    owner="user_id",
    one_of={"status": ORDER_STATES},
    one_of_stored={"status": tuple(s.upper() for s in ORDER_STATES)},
)
ORDER_ITEM = Schema.from_pairs(
    [
        ("id", Scalar.INT),
        ("order_id", Scalar.INT),
        ("product_id", Scalar.INT),
        ("quantity", Scalar.INT),
        ("price_at_purchase", Scalar.FLOAT),
    ],
    keys=[("id",)],
    owner_via=("order_id", "Order"),
)


def profile() -> Profile:
    api = ApiProfile(
        tables={
            "User": USER,
            "Vendor": VENDOR,
            "Product": PRODUCT,
            "Order": ORDER,
            "OrderItem": ORDER_ITEM,
        },
        table_names={
            "User": "users",
            "Vendor": "vendors",
            "Product": "products",
            "Order": "orders",
            "OrderItem": "order_items",
        },
        roles=frozenset({"Public", "Buyer", "Vendor", "Admin"}),
        users_table="User",
        roles_from=RoleColumn("role", values={"buyer": "Buyer", "vendor": "Vendor"}),
    )
    return Profile(schemas={}, api=api)


DDL = """
CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, role TEXT, token TEXT,
                    is_active INTEGER);
CREATE TABLE vendors (id INTEGER PRIMARY KEY, user_id INTEGER, business_name TEXT);
CREATE TABLE products (id INTEGER PRIMARY KEY, vendor_id INTEGER, name TEXT,
                       description TEXT, category TEXT, price REAL,
                       stock_quantity INTEGER, is_active INTEGER, created_at TEXT);
CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT,
                     total_amount REAL);
CREATE TABLE order_items (id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER,
                          quantity INTEGER, price_at_purchase REAL);
"""


def build_db(path: Path, rows: dict[str, list[tuple]] | None = None) -> sqlite3.Connection:
    """A fresh database at `path` with `rows` ({sql table: [row tuple, ...]})."""
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    for table, values in (rows or {}).items():
        for row in values:
            marks = ", ".join("?" for _ in row)
            conn.execute(f"INSERT INTO {table} VALUES ({marks})", row)
    conn.commit()
    return conn
