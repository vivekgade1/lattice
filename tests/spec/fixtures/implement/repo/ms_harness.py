"""The implement fixture's check harness: the app package `shop`, a seeded sqlite database and the
app's own login. Self-contained (nothing from the lattice test tree)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from shop.main import make_app  # noqa: F401  (the app factory the check uses)

DDL = """
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, password TEXT NOT NULL,
                    token TEXT NOT NULL UNIQUE, role TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL);
CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, price FLOAT NOT NULL,
                       stock INTEGER NOT NULL);
CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                     status TEXT NOT NULL, total FLOAT NOT NULL);
CREATE TABLE order_items (id INTEGER PRIMARY KEY,
                          order_id INTEGER NOT NULL REFERENCES orders(id),
                          product_id INTEGER NOT NULL REFERENCES products(id),
                          quantity INTEGER NOT NULL, price FLOAT NOT NULL);
"""

# handle -> (id, password, role, active)
USERS = {
    "ann": (1, "pw-ann", "Buyer", 1),
    "ben": (2, "pw-ben", "Buyer", 1),
    "ada": (3, "pw-ada", "Admin", 1),
    "eve": (4, "pw-eve", "Buyer", 0),
}
SEED = {
    "users": [
        (1, "ann", "pw-ann", "tok-ann", "Buyer", 1),
        (2, "ben", "pw-ben", "tok-ben", "Buyer", 1),
        (3, "ada", "pw-ada", "tok-ada", "Admin", 1),
        (4, "eve", "pw-eve", "tok-eve", "Buyer", 0),
    ],
    "products": [(1, "pen", 2.5, 10), (2, "ink", 4.0, 5)],
    "orders": [
        (1, 1, "pending", 5.0),
        (2, 1, "paid", 4.0),
        (3, 2, "pending", 0.0),
        (4, 3, "pending", 2.5),
    ],
    "order_items": [(1, 1, 1, 2, 2.5), (2, 2, 2, 1, 4.0), (3, 4, 1, 1, 2.5)],
}


def build_db(path: Path) -> Path:
    """The seeded database at `path`."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        for table, rows in SEED.items():
            for row in rows:
                marks = ", ".join("?" for _ in row)
                conn.execute(f"INSERT INTO {table} VALUES ({marks})", row)
        conn.commit()
    finally:
        conn.close()
    return path


def login(client: Any, handle: str) -> str:
    """Log in through the app's own `POST /login`."""
    _, password, _, _ = USERS[handle]
    r = client.post("/login", data={"username": handle, "password": password})
    if r.status_code != 200:
        raise RuntimeError(f"login of {handle!r} failed: {r.status_code} {r.text}")
    return r.json()["access_token"]
