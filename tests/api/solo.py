"""A single-role app fixture (the held-out import gaps, made generic):

* ``roles_from=NoRoles()`` -- the users table has no role column and the app no admin
  concept: every authenticated caller is a Customer, and ``owned()``/``accessible()``
  equal ``mine()``;
* ``NativeToken("decode_token", lookup="username")`` -- the token's subject is the
  (unique) username, not the users key;
* context natives -- ``decode_token`` and ``greet`` read profile-declared settings (the
  signing secret, a greeting) from their ``NativeContext``, supplied at start-up;
* ``OneOf`` columns -- ``Task.status`` and ``Task.priority`` hold only listed values;
  every write passes them through a ``OneOf`` parameter or a listed literal. The
  database spells ``status`` by member name (``"DOING"`` for ``"doing"``, as an ORM
  stores a Python Enum): reads decode and writes encode it in SQL.

Shared by tests/api/test_import_gap_features.py and the codegen differential.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lattice.profile import ApiProfile, NativeSig, NativeToken, NoRoles, Profile
from lattice.typesys import Scalar, Schema
from tests.api.solo_natives import NATIVES as NATIVES
from tests.api.solo_natives import SETTINGS as SETTINGS
from tests.api.solo_natives import hash_password, mint_token

S = Scalar
NATIVES_FILE = Path(__file__).resolve().parent / "solo_natives.py"
STATUSES = ("todo", "doing", "done")
STORED_STATUSES = ("TODO", "DOING", "DONE")  # how the database spells them
PRIORITIES = ("low", "high")

USER = Schema.from_pairs(
    [("id", S.INT), ("username", S.STR), ("password_hash", S.STR), ("is_active", S.BOOL)],
    keys=[("id",), ("username",)],
    private=["password_hash"],
)
TASK = Schema.from_pairs(
    [
        ("id", S.INT),
        ("owner_id", S.INT),
        ("title", S.STR),
        ("status", S.STR),
        ("priority", S.STR),
    ],
    keys=[("id",)],
    nullable=["priority"],
    owner="owner_id",
    one_of={"status": STATUSES, "priority": PRIORITIES},
    one_of_stored={"status": STORED_STATUSES},
)

API = ApiProfile(
    tables={"User": USER, "Task": TASK},
    table_names={"User": "users", "Task": "tasks"},
    roles=frozenset({"Public", "Customer", "Admin"}),
    users_table="User",
    page_shape={"items": "items", "total": "total", "limit": "", "offset": ""},
    natives={
        "decode_token": NativeSig((S.STR,), S.STR, nullable=True, settings=("secret",)),
        "greet": NativeSig((S.STR,), S.STR, settings=("greeting",)),
        "hash_password": NativeSig((S.STR,), S.STR),
    },
    settings={"secret": S.STR, "greeting": S.STR},
    auth=NativeToken("decode_token", lookup="username"),
    roles_from=NoRoles(),
    active_column="is_active",
)
PROFILE = Profile(schemas={}, api=API)

USERS = {"alice": 1, "bob": 2, "carol": 3}  # carol is inactive
TOKENS = {name: mint_token(SETTINGS["secret"], name) for name in USERS}
TOKENS["ghost"] = mint_token(SETTINGS["secret"], "ghost")  # valid, but no such user
TOKENS["forged"] = mint_token("wrong-secret", "alice")  # signed with another secret
# tasks: (id, owner, title, status, priority)
TASKS = [
    (1, 1, "Write spec", "TODO", "high"),
    (2, 1, "Review", "DOING", None),
    (3, 2, "Deploy", "DONE", "low"),
    (4, 2, "Plan", "TODO", None),
]


def build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL, is_active INTEGER NOT NULL
            );
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
                title TEXT NOT NULL, status TEXT NOT NULL, priority TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO users VALUES (?, ?, ?, ?)",
            [(i, n, hash_password(n), int(n != "carol")) for n, i in USERS.items()],
        )
        conn.executemany("INSERT INTO tasks VALUES (?, ?, ?, ?, ?)", TASKS)
        conn.commit()
    finally:
        conn.close()


PROGRAM = """# lattice: v1
TASK_FIELDS = ["id", "title", "status", "priority", "owner_id"]


@endpoint("GET", "/tasks", auth=Customer)
def list_tasks(user: CurrentUser, status: OneOf("todo", "doing", "done") | None = None) -> Page:
    tasks = fetch(Task)
    visible = owned(tasks, by=user)
    picked = where(visible, is_null(status) | (col("status") == status))
    ordered = sort(picked, by=["id"])
    shown = select(ordered, cols=TASK_FIELDS)
    result = page(shown, limit=100, total=True)
    return result


@endpoint("GET", "/tasks/mine", auth=Customer)
def my_tasks(user: CurrentUser) -> Page:
    tasks = fetch(Task)
    own = mine(tasks, by=user)
    ordered = sort(own, by=["id"])
    shown = select(ordered, cols=TASK_FIELDS)
    result = page(shown, limit=100, total=True)
    return result


@endpoint("GET", "/tasks/{id}", auth=Customer)
def read_task(id: Int(ge=1), user: CurrentUser) -> One | NotFound | Forbidden:
    tasks = fetch(Task)
    found = where(tasks, col("id") == id)
    task = one(found, else_=NotFound("Task not found"))
    allowed = require(task, owner_is(user), else_=Forbidden("Not your task"))
    shown = select(allowed, cols=TASK_FIELDS)
    return shown


@endpoint("POST", "/tasks", auth=Customer)
def create_task(
    user: CurrentUser,
    title: Str(min_len=1, max_len=50),
    status: OneOf("todo", "doing", "done") = "todo",
    priority: OneOf("low", "high") | None = None,
) -> Created:
    task = insert(Task, owner=user, title=title, status=status, priority=priority)
    shown = select(task, cols=TASK_FIELDS)
    return shown


@endpoint("PATCH", "/tasks/{id}", auth=Customer)
def update_task(
    id: Int(ge=1),
    user: CurrentUser,
    status: OneOf("todo", "doing", "done") = UNSET,
    priority: OneOf("low", "high") = UNSET,
) -> One | NotFound | Forbidden:
    tasks = fetch(Task)
    found = where(tasks, col("id") == id)
    task = one(found, else_=NotFound("Task not found"))
    allowed = require(task, owner_is(user), else_=Forbidden("Not your task"))
    done = update(allowed, status=status, priority=priority)
    shown = select(done, cols=TASK_FIELDS)
    return shown


@endpoint("POST", "/tasks/{id}/finish", auth=Customer)
def finish_task(id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    tasks = fetch(Task)
    own = mine(tasks, by=user)
    found = where(own, col("id") == id)
    task = one(found, else_=NotFound("Task not found"))
    done = update(task, status="done")
    shown = select(done, cols=TASK_FIELDS)
    return shown


@endpoint("GET", "/me", auth=Customer)
def read_me(user: CurrentUser) -> One:
    greeted = derive(user, greeting=native.greet(col("username")), admin=is_admin(user))
    shown = select(greeted, cols=["id", "username", "greeting", "admin"])
    return shown
"""
