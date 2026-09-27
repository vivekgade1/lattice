"""Sub-project 1b node N2 fixture: a small API profile with a self-referencing category
tree (public, with a cycle), an owned folder tree and owned expenses; a seeded SQLite
database; and a program reading them with `window(...)` + `win.*` and `traverse(...)`.

Shared by the checker tests (tests/check/test_loops_checker.py), the runtime tests
(tests/api/test_loops_api.py) and the codegen differential.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lattice.profile import ApiProfile, Profile
from lattice.typesys import Scalar, Schema

S = Scalar

USERS = Schema.from_pairs(
    [("id", S.INT), ("name", S.STR), ("role", S.STR), ("token", S.STR)],
    keys=[("id",)],
    owner="id",
    private=["token"],
)
# A public category tree: parent_id is null for a root. 20 -> 21 -> 22 -> 20 is a cycle.
CATEGORIES = Schema.from_pairs(
    [("id", S.INT), ("parent_id", S.INT), ("name", S.STR)],
    keys=[("id",)],
    nullable=["parent_id"],
)
# Per-user folder trees.
FOLDERS = Schema.from_pairs(
    [("id", S.INT), ("parent_id", S.INT), ("owner_id", S.INT), ("name", S.STR)],
    keys=[("id",)],
    nullable=["parent_id"],
    owner="owner_id",
)
EXPENSES = Schema.from_pairs(
    [
        ("id", S.INT),
        ("owner_id", S.INT),
        ("category_id", S.INT),
        ("day", S.DATE),
        ("amount", S.INT),
        ("note", S.STR),
    ],
    keys=[("id",)],
    nullable=["amount", "note"],
    owner="owner_id",
)

API = ApiProfile(
    tables={"Users": USERS, "Categories": CATEGORIES, "Folders": FOLDERS, "Expenses": EXPENSES},
    table_names={
        "Users": "users",
        "Categories": "categories",
        "Folders": "folders",
        "Expenses": "expenses",
    },
    roles=frozenset({"Public", "Customer", "Admin"}),
)
PROFILE = Profile(schemas={}, api=API)

TOKENS = {"admin": "tok-admin", "alice": "tok-alice", "bob": "tok-bob"}
USER_ROWS = [
    (1, "Admin", "Admin", TOKENS["admin"]),
    (2, "Alice", "Customer", TOKENS["alice"]),
    (3, "Bob", "Customer", TOKENS["bob"]),
]
# 1 root; 2, 3 under 1; 4 under 2; 5, 6 under 4; 7 under 3; 8 a second root;
# 20 -> 21 -> 22 -> 20 a cycle (no root).
CATEGORY_ROWS = [
    (1, None, "All"),
    (2, 1, "Food"),
    (3, 1, "Travel"),
    (4, 2, "Groceries"),
    (5, 4, "Fruit"),
    (6, 4, "Bread"),
    (7, 3, "Trains"),
    (8, None, "Other"),
    (20, 22, "Loop A"),
    (21, 20, "Loop B"),
    (22, 21, "Loop C"),
]
FOLDER_ROWS = [
    (1, None, 2, "alice root"),
    (2, 1, 2, "alice docs"),
    (3, 2, 2, "alice deep"),
    (4, None, 3, "bob root"),
    (5, 4, 3, "bob docs"),
    # a folder of bob's filed under alice's: invisible to alice's walk
    (6, 2, 3, "bob stray"),
]
# (id, owner, category, day, amount, note) -- ties on (category, day), a null amount
EXPENSE_ROWS = [
    (1, 2, 5, "2024-01-03", 10, "apples"),
    (2, 2, 5, "2024-01-01", 4, None),
    (3, 2, 6, "2024-01-02", 7, "bread"),
    (4, 2, 5, "2024-01-03", None, "refund?"),
    (5, 2, 6, "2024-01-05", 3, None),
    (6, 2, 7, "2024-01-04", 40, "train"),
    (7, 3, 5, "2024-01-01", 99, "bob fruit"),
    (8, 2, 5, "2024-01-06", 6, None),
]


def build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
                token TEXT NOT NULL UNIQUE
            );
            CREATE TABLE categories (
                id INTEGER PRIMARY KEY, parent_id INTEGER, name TEXT NOT NULL
            );
            CREATE TABLE folders (
                id INTEGER PRIMARY KEY, parent_id INTEGER,
                owner_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL
            );
            CREATE TABLE expenses (
                id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
                category_id INTEGER NOT NULL, day TEXT NOT NULL, amount INTEGER, note TEXT
            );
            """
        )
        conn.executemany("INSERT INTO users VALUES (?, ?, ?, ?)", USER_ROWS)
        conn.executemany("INSERT INTO categories VALUES (?, ?, ?)", CATEGORY_ROWS)
        conn.executemany("INSERT INTO folders VALUES (?, ?, ?, ?)", FOLDER_ROWS)
        conn.executemany("INSERT INTO expenses VALUES (?, ?, ?, ?, ?, ?)", EXPENSE_ROWS)
        conn.commit()
    finally:
        conn.close()


PROGRAM = """# lattice: v1
@endpoint("GET", "/expenses/running", auth=Customer)
def running(
    user: CurrentUser, limit: Int(ge=1, le=100) = 50, offset: Int(ge=0) = 0
) -> Page:
    expenses = fetch(Expenses)
    mine = owned(expenses, by=user)
    ranked = window(
        mine,
        by=["category_id"],
        order=["day", "id"],
        desc=["day"],
        n=win.row_number(),
        prev_amount=win.lag(col("amount"), offset=1),
        next_amount=win.lead(col("amount"), offset=2),
        running=win.cum_sum(col("amount")),
        seen=win.cum_count(col("amount")),
        rows_so_far=win.cum_count(),
        low=win.cum_min(col("amount")),
        high=win.cum_max(col("day")),
        avg2=win.mean(col("amount"), rows=2),
        sum3=win.sum(col("amount") * 2, rows=3),
    )
    days = window(ranked, order=["day"], day_rank=win.rank(), day_dense=win.dense_rank())
    shown = select(
        days,
        cols=[
            "id", "category_id", "day", "amount", "n", "prev_amount", "next_amount",
            "running", "seen", "rows_so_far", "low", "high", "avg2", "sum3", "day_rank",
            "day_dense",
        ],
    )
    result = page(shown, limit=limit, offset=offset)
    return result


@endpoint("GET", "/categories/{id}/tree", auth=Public)
def category_tree(id: Int(ge=1), depth: Int(ge=1, le=10) = 10) -> Page:
    categories = fetch(Categories)
    root = where(categories, col("id") == id)
    tree = traverse(categories, root, parent="parent_id", child="id", max_depth=depth)
    result = page(tree, limit=100)
    return result


@endpoint("GET", "/categories/{id}/path", auth=Public)
def category_path(id: Int(ge=1)) -> Page:
    categories = fetch(Categories)
    leaf = where(categories, col("id") == id)
    path = traverse(
        categories, leaf, parent="parent_id", child="id", max_depth=20, direction="up"
    )
    named = select(path, cols=["id", "name", "depth"])
    result = page(named, limit=100)
    return result


@endpoint("GET", "/folders/{id}/subtree", auth=Customer)
def folder_subtree(id: Int(ge=1), user: CurrentUser) -> Page:
    folders = fetch(Folders)
    mine = owned(folders, by=user)
    root = where(mine, col("id") == id)
    tree = traverse(mine, root, parent="parent_id", child="id", max_depth=50)
    result = page(tree, limit=100)
    return result
"""
