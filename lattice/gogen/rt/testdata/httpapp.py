"""The small Lattice program whose generated FastAPI app pins the Go runtime's HTTP
behaviour (routing, redirects, auth, errors, transactions, SQL statement counts). Used by
gen_http_answers.py; the Go tests (http_test.go) serve the same endpoints over the same
database with handlers that run the generated app's SQL."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from lattice.profile import ApiProfile, Profile
from lattice.typesys import Scalar, Schema

S = Scalar

USERS = Schema.from_pairs(
    [("id", S.INT), ("token", S.STR), ("role", S.STR), ("active", S.BOOL), ("name", S.STR)],
    keys=[("id",)],
    private=["token"],
    one_of={"role": ("Customer", "Admin")},
)
ITEMS = Schema.from_pairs(
    [
        ("id", S.INT),
        ("name", S.STR),
        ("price", S.FLOAT),
        ("owner_id", S.INT),
        ("created_at", S.DATETIME),
    ],
    keys=[("id",), ("name",)],
)

API = ApiProfile(
    tables={"Users": USERS, "Items": ITEMS},
    table_names={"Users": "users", "Items": "items"},
    roles=frozenset({"Public", "Customer", "Admin"}),
    active_column="active",
    page_shape={"items": "data", "total": "count", "limit": "limit", "offset": ""},
)
PROFILES = {
    "lattice": Profile(schemas={}, api=API),
    "fastapi": Profile(
        schemas={},
        api=replace(
            API,
            error_body="fastapi",
            unauthorized_detail="Not authenticated",
            forbidden_detail="Not enough privileges",
            inactive_status=403,
            inactive_detail="Inactive",
        ),
    ),
}

TOKENS = {"ann": "tok-ann", "bob": "tok-bob", "cy": "tok-cy"}
SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE, role TEXT NOT NULL,
    active INTEGER NOT NULL, name TEXT NOT NULL
);
CREATE TABLE items (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, price REAL NOT NULL,
    owner_id INTEGER NOT NULL, created_at DATETIME NOT NULL
);
INSERT INTO users VALUES
    (1, 'tok-ann', 'Customer', 1, 'Ann'),
    (2, 'tok-bob', 'Admin', 1, 'Bob'),
    (3, 'tok-cy', 'Customer', 0, 'Cy');
INSERT INTO items VALUES
    (1, 'apple', 1.5, 1, '2000-01-02T03:04:05.000000Z'),
    (2, 'pear', 1e16, 2, '2000-01-02 05:04:05+02:00'),
    (3, 'fig', 0.25, 1, '2000-01-03 00:00:00');
"""


def build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


PROGRAM = """# lattice: v1
COLS = ["id", "name", "price", "created_at"]


@endpoint("GET", "/items", auth=Public)
def list_items(n: Int(ge=1, le=100) = 10, skip: Int(ge=0) = 0) -> Page:
    items = fetch(Items)
    ordered = sort(items, by=["id"])
    shown = select(ordered, cols=COLS)
    result = page(shown, limit=n, offset=skip, total=True)
    return result


@endpoint("GET", "/items/mine", auth=Customer)
def my_items(user: CurrentUser) -> List:
    items = fetch(Items)
    mine = where(items, col("owner_id") == user.id)
    ordered = sort(mine, by=["id"])
    shown = select(ordered, cols=COLS)
    few = limit(shown, n=100)
    return few


@endpoint("GET", "/items/{id}", auth=Public)
def get_item(id: Int) -> One | NotFound:
    items = fetch(Items)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("item not found"))
    shown = select(item, cols=COLS)
    return shown


@endpoint("POST", "/items", auth=Customer)
def create_item(
    user: CurrentUser, name: Str(max_len=20), price: Float(ge=0, le=1000)
) -> Created | Conflict:
    item = insert(
        Items,
        owner_id=user.id,
        name=name,
        price=price,
        created_at=fn.now(),
        unique_or=Conflict("an item with this name exists"),
    )
    shown = select(item, cols=COLS)
    return shown


@endpoint("GET", "/admin/ping", auth=Admin)
def admin_ping(user: CurrentUser) -> One:
    out = select(user, cols=["id", "role", "name"])
    return out
"""
