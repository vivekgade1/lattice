"""Nested response models (W1-B, plan 2026-10-04 phase 0): an endpoint answers each
returned row with a list of its child rows, read in the same SQL statement.

* ``OrderRead`` nests ``items`` (``OrderItemRead``) from ``order_items`` through
  ``order_id``; the items are owned through their order (``owner_via``), carry a private
  column (``cost``, never answered) and a OneOf column stored by member name (``kind``:
  ``"GIFT"`` is answered ``"gift"``); order 12 has no items (``[]``).
* ``ProductRead`` nests ``images`` from the public table ``product_images``.
* Endpoints: One (``read_order``), Page (``list_orders``), List (``recent_orders``), a
  One after an update (``rename_order``: the nested list is read after the write) and a
  Public One (``read_product``).

Shared by tests/api/test_nested_responses.py and the codegen differential.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lattice.profile import ApiProfile, Nested, Profile, RoleColumn
from lattice.typesys import Scalar, Schema

S = Scalar

USER = Schema.from_pairs(
    [("id", S.INT), ("token", S.STR), ("name", S.STR), ("role", S.STR)],
    keys=[("id",), ("token",)],
    private=["token"],
)
ORDER = Schema.from_pairs(
    [("id", S.INT), ("user_id", S.INT), ("status", S.STR), ("total_amount", S.FLOAT),
     ("note", S.STR)],
    keys=[("id",)],
    owner="user_id",
)  # fmt: skip
ORDER_ITEM = Schema.from_pairs(
    [("id", S.INT), ("order_id", S.INT), ("product_id", S.INT), ("quantity", S.INT),
     ("price_at_purchase", S.FLOAT), ("kind", S.STR), ("cost", S.FLOAT)],
    keys=[("id",)],
    owner_via=("order_id", "Order"),
    private=["cost"],
    one_of={"kind": ("standard", "gift")},
    one_of_stored={"kind": ("STANDARD", "GIFT")},
)  # fmt: skip
PRODUCT = Schema.from_pairs(
    [("id", S.INT), ("name", S.STR), ("in_stock", S.BOOL)],
    keys=[("id",)],
)
PRODUCT_IMAGE = Schema.from_pairs(
    [("id", S.INT), ("product_id", S.INT), ("url", S.STR), ("primary", S.BOOL)],
    keys=[("id",)],
)

API = ApiProfile(
    tables={
        "User": USER,
        "Order": ORDER,
        "OrderItem": ORDER_ITEM,
        "Product": PRODUCT,
        "ProductImage": PRODUCT_IMAGE,
    },
    table_names={
        "User": "users",
        "Order": "orders",
        "OrderItem": "order_items",
        "Product": "products",
        "ProductImage": "product_images",
    },
    roles=frozenset({"Public", "Customer", "Admin"}),
    users_table="User",
    roles_from=RoleColumn("role"),
    responses={
        "OrderItemRead": ("id", "product_id", "quantity", "price_at_purchase", "kind"),
        "OrderRead": (
            "id",
            "status",
            Nested("items", "OrderItemRead", table="OrderItem", via="order_id"),
            "total_amount",
        ),
        "ImageRead": ("url", "primary"),
        "ProductRead": (
            "id",
            "name",
            "in_stock",
            Nested("images", "ImageRead", table="ProductImage", via="product_id"),
        ),
    },
)
PROFILE = Profile(schemas={}, api=API)

# name -> (id, role)
USERS = {"ann": (1, "Customer"), "bob": (2, "Customer"), "ada": (3, "Admin")}
TOKENS = {name: f"tok-{name}" for name in USERS}
# (id, user_id, status, total_amount, note)
ORDERS = [
    (10, 1, "paid", 37.5, "ring twice"),
    (11, 1, "shipped", 4.0, ""),
    (12, 1, "new", 0.0, "empty"),
    (13, 2, "paid", 9.25, "bob's"),
]
# (id, order_id, product_id, quantity, price_at_purchase, kind (stored), cost); inserted
# out of key order: the nested lists are ordered by the child key
ITEMS = [
    (103, 10, 3, 1, 12.5, "GIFT", 7.0),
    (101, 10, 1, 2, 10.0, "STANDARD", 6.0),
    (102, 10, 2, 1, 5.0, "STANDARD", 2.0),
    (104, 11, 1, 1, 4.0, "STANDARD", 3.0),
    (105, 13, 2, 3, 3.0833333333333335, "GIFT", 1.0),
]
PRODUCTS = [(1, "lamp", 1), (2, "mug", 0), (3, "card", 1)]
# (id, product_id, url, primary)
IMAGES = [(2, 1, "lamp-side.png", 0), (1, 1, "lamp.png", 1), (3, 2, "mug.png", 1)]

ORDERS_AFTER = "SELECT id, user_id, status, total_amount, note FROM orders ORDER BY id"


def build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL, role TEXT NOT NULL
            );
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                status TEXT NOT NULL, total_amount REAL NOT NULL, note TEXT NOT NULL
            );
            CREATE TABLE order_items (
                id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
                product_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
                price_at_purchase REAL NOT NULL, kind TEXT NOT NULL, cost REAL NOT NULL
            );
            CREATE TABLE products (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, in_stock INTEGER NOT NULL
            );
            CREATE TABLE product_images (
                id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
                url TEXT NOT NULL, "primary" INTEGER NOT NULL
            );
            """
        )
        conn.executemany(
            "INSERT INTO users VALUES (?, ?, ?, ?)",
            [(i, TOKENS[n], n, role) for n, (i, role) in USERS.items()],
        )
        conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", ORDERS)
        conn.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?, ?)", ITEMS)
        conn.executemany("INSERT INTO products VALUES (?, ?, ?)", PRODUCTS)
        conn.executemany("INSERT INTO product_images VALUES (?, ?, ?, ?)", IMAGES)
        conn.commit()
    finally:
        conn.close()


PROGRAM = """# lattice: v1
@endpoint("GET", "/orders/{order_id}", auth=Customer, response="OrderRead")
def read_order(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    orders = fetch(Order)
    visible = owned(orders, by=user)
    found = where(visible, col("id") == order_id)
    order = one(found, else_=NotFound("order not found"))
    shown = select(order, cols=["id", "status", "total_amount"])
    return shown


@endpoint("GET", "/orders", auth=Customer, response="OrderRead")
def list_orders(
    user: CurrentUser, limit: Int(ge=1, le=50) = 2, offset: Int(ge=0) = 0
) -> Page:
    orders = fetch(Order)
    visible = owned(orders, by=user)
    ordered = sort(visible, by=["id"])
    shown = select(ordered, cols=["id", "status", "total_amount"])
    result = page(shown, limit=limit, offset=offset)
    return result


@endpoint("GET", "/recent-orders", auth=Customer, response="OrderRead")
def recent_orders(user: CurrentUser) -> List:
    orders = fetch(Order)
    visible = owned(orders, by=user)
    ordered = sort(visible, by=["id"], desc=True)
    shown = select(ordered, cols=["id", "status", "total_amount"])
    recent = limit(shown, n=3)
    return recent


@endpoint("PATCH", "/orders/{order_id}", auth=Customer, response="OrderRead")
def rename_order(order_id: Int(ge=1), status: Str(max_len=20), user: CurrentUser) -> One | NotFound:
    orders = fetch(Order)
    visible = owned(orders, by=user)
    found = where(visible, col("id") == order_id)
    order = one(found, else_=NotFound("order not found"))
    changed = update(order, status=status)
    shown = select(changed, cols=["id", "status", "total_amount"])
    return shown


@endpoint("GET", "/products/{product_id}", auth=Public, response="ProductRead")
def read_product(product_id: Int(ge=1)) -> One | NotFound:
    products = fetch(Product)
    found = where(products, col("id") == product_id)
    product = one(found, else_=NotFound("product not found"))
    return product
"""
