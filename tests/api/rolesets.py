"""Exact role sets and stored role values (spec 2026-09-29-lattice-role-sets-design):

* ``auth=Roles(Buyer)`` -- buyers only, the admin refused; ``auth=Roles(Buyer, Vendor)``
  -- either; ``auth=Roles(Admin)`` -- the admin only;
* ``auth=Authenticated`` -- any authenticated caller, whatever the role;
* ``auth=Vendor`` -- unchanged: vendors and the admin;
* ``RoleColumn("role", values=...)`` -- the column stores an enum's member names
  (``"BUYER"``), decoded to role names before any comparison (``is_admin``, ``owned``
  included); ``odd`` stores a value outside the map and may call only
  ``Authenticated`` endpoints.

Shared by tests/api/test_role_sets.py and the codegen differential.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lattice.profile import ApiProfile, Profile, RoleColumn
from lattice.typesys import Scalar, Schema

S = Scalar

USER = Schema.from_pairs(
    [("id", S.INT), ("token", S.STR), ("name", S.STR), ("role", S.STR)],
    keys=[("id",), ("token",)],
    private=["token"],
)
ITEM = Schema.from_pairs(
    [("id", S.INT), ("owner_id", S.INT), ("title", S.STR)],
    keys=[("id",)],
    owner="owner_id",
)
STORED = {"BUYER": "Buyer", "VENDOR": "Vendor", "ADMIN": "Admin"}

API = ApiProfile(
    tables={"User": USER, "Item": ITEM},
    table_names={"User": "users", "Item": "items"},
    roles=frozenset({"Public", "Buyer", "Vendor", "Admin"}),
    users_table="User",
    roles_from=RoleColumn("role", values=STORED),
)
PROFILE = Profile(schemas={}, api=API)

# name -> (id, stored role); odd's role is outside the map
USERS = {"ann": (1, "BUYER"), "ben": (2, "VENDOR"), "ada": (3, "ADMIN"), "odd": (4, "GUEST")}
TOKENS = {name: f"tok-{name}" for name in USERS}
ITEMS = [(1, 1, "ann's"), (2, 2, "ben's"), (3, 3, "ada's")]


def build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL, role TEXT NOT NULL
            );
            CREATE TABLE items (
                id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
                title TEXT NOT NULL
            );
            """
        )
        conn.executemany(
            "INSERT INTO users VALUES (?, ?, ?, ?)",
            [(i, TOKENS[n], n, role) for n, (i, role) in USERS.items()],
        )
        conn.executemany("INSERT INTO items VALUES (?, ?, ?)", ITEMS)
        conn.commit()
    finally:
        conn.close()


PROGRAM = """# lattice: v1
@endpoint("GET", "/buyers", auth=Roles(Buyer))
def buyers_only(user: CurrentUser) -> One:
    shown = select(user, cols=["id", "name"])
    return shown


@endpoint("GET", "/shoppers", auth=Roles(Buyer, Vendor))
def buyers_or_vendors(user: CurrentUser) -> One:
    shown = select(user, cols=["id", "name"])
    return shown


@endpoint("GET", "/staff", auth=Roles(Admin))
def admins_only(user: CurrentUser) -> One:
    shown = select(user, cols=["id", "name"])
    return shown


@endpoint("GET", "/vendors", auth=Vendor)
def vendors_and_admin(user: CurrentUser) -> One:
    shown = select(user, cols=["id", "name"])
    return shown


@endpoint("GET", "/me", auth=Authenticated)
def read_me(user: CurrentUser) -> One:
    flagged = derive(user, admin=is_admin(user))
    shown = select(flagged, cols=["id", "name", "admin"])
    return shown


@endpoint("GET", "/items", auth=Authenticated)
def list_items(user: CurrentUser) -> Page:
    items = fetch(Item)
    visible = owned(items, by=user)
    ordered = sort(visible, by=["id"])
    result = page(ordered, limit=100)
    return result
"""

# path -> the users who may call it
ALLOWED = {
    "/buyers": {"ann"},
    "/shoppers": {"ann", "ben"},
    "/staff": {"ada"},
    "/vendors": {"ben", "ada"},
    "/me": {"ann", "ben", "ada", "odd"},
    "/items": {"ann", "ben", "ada", "odd"},
}
