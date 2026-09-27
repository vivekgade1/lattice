"""Sub-project 1b node N3 fixture: an API profile with effect natives (`for_each`), a
seeded SQLite database and a program sending side effects through the outbox.

Shared by the checker tests (tests/check/test_loops_n3_checker.py), the for_each
runtime / codegen tests (tests/api/test_for_each.py) and the codegen differential.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lattice.profile import ApiProfile, NativeSig, Profile
from lattice.typesys import Scalar, Schema
from tests.api.effects_natives import NATIVES as NATIVES

S = Scalar
NATIVES_FILE = Path(__file__).resolve().parent / "effects_natives.py"

USERS = Schema.from_pairs(
    [("id", S.INT), ("email", S.STR), ("full_name", S.STR), ("role", S.STR), ("token", S.STR)],
    keys=[("id",)],
    private=["token"],
)
ITEMS = Schema.from_pairs(
    [("id", S.INT), ("owner_id", S.INT), ("title", S.STR), ("due", S.DATE), ("done", S.BOOL)],
    keys=[("id",)],
    nullable=["due"],
    owner="owner_id",
)

API = ApiProfile(
    tables={"Users": USERS, "Items": ITEMS},
    table_names={"Users": "users", "Items": "items"},
    roles=frozenset({"Public", "Customer", "Admin"}),
    natives={
        "send_welcome": NativeSig((S.STR, S.STR), effect=True),
        "item_created": NativeSig((S.INT, S.STR, S.BOOL), effect=True),
        "item_viewed": NativeSig((S.INT, S.STR, S.STR), effect=True),
        "item_done": NativeSig((S.INT, S.DATE), effect=True),
        "shout": NativeSig((S.STR,), S.STR),
    },
)
PROFILE = Profile(schemas={}, api=API)

TOKENS = {"admin": "tok-admin", "alice": "tok-alice", "bob": "tok-bob", "carol": "tok-carol"}
USER_ROWS = [
    (1, "admin@example.com", "Admin", "Admin", TOKENS["admin"]),
    (2, "alice@example.com", "Alice", "Customer", TOKENS["alice"]),
    (3, "bob@example.com", "Bob", "Customer", TOKENS["bob"]),
    (4, "carol@example.com", "Carol", "Customer", TOKENS["carol"]),  # owns no items
]
# (id, owner, title, due, done)
ITEM_ROWS = [
    (1, 2, "alpha", "2000-01-05", 0),
    (2, 2, "beta", None, 0),
    (3, 3, "gamma", "2000-01-07", 1),
    (4, 2, "boom", "2000-01-09", 0),
]


def build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, full_name TEXT NOT NULL,
                role TEXT NOT NULL, token TEXT NOT NULL UNIQUE
            );
            CREATE TABLE items (
                id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
                title TEXT NOT NULL, due TEXT, done INTEGER NOT NULL
            );
            """
        )
        conn.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?)", USER_ROWS)
        conn.executemany("INSERT INTO items VALUES (?, ?, ?, ?, ?)", ITEM_ROWS)
        conn.commit()
    finally:
        conn.close()


PROGRAM = """# lattice: v1
@endpoint("POST", "/users/signup", auth=Public)
def signup(email: Str(min_len=3, max_len=100), full_name: Str(max_len=100)) -> Created | Conflict:
    created = insert(
        Users,
        email=email,
        full_name=full_name,
        role="Customer",
        token=email,
        unique_or=Conflict("email taken"),
    )
    for_each(created, native.send_welcome, col("email"), col("full_name"))
    shown = select(created, cols=["id", "email", "full_name"])
    return shown


@endpoint("POST", "/items", auth=Customer)
def create_item(title: Str(max_len=50), user: CurrentUser) -> Created | BadRequest:
    made = insert(Items, owner=user, title=title, done=False)
    for_each(made, native.item_created, col("id"), fn.lower(col("title")), col("done"))
    checked = require(made, col("title") != "forbidden", else_=BadRequest("title refused"))
    return checked


@endpoint("GET", "/items/recent", auth=Customer)
def recent_items(
    user: CurrentUser, note: Str(max_len=20) = "seen", limit: Int(ge=1, le=100) = 3
) -> Page:
    items = fetch(Items)
    mine = owned(items, by=user)
    ordered = sort(mine, by=["id"], desc=True)
    shown = page(ordered, limit=limit)
    for_each(shown, native.item_viewed, col("id"), fn.upper(col("title")), note)
    return shown


@endpoint("PATCH", "/items/{item_id}/done", auth=Customer)
def finish_item(item_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    items = fetch(Items)
    mine = owned(items, by=user)
    found = where(mine, col("id") == item_id)
    item = one(found, else_=NotFound("item"))
    finished = update(item, done=True)
    for_each(finished, native.item_done, col("id"), col("due"))
    return finished
"""

# created_at and claimed_at are instants of the run: only whether a row was claimed counts
OUTBOX_AFTER = (
    "SELECT endpoint, native, args, status, error, attempts, claimed_at IS NOT NULL "
    "FROM lattice_outbox ORDER BY id"
)
