"""A small real-app fixture (sub-project 1): a template-style profile with Uuid keys,
DateTime columns, an Admin flag column, response models, a custom page shape, natives
and NativeToken auth; a seeded SQLite database; and a Lattice program using every new
language feature. Shared by test_realapps_runtime.py and the codegen differential.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from lattice.profile import AdminFlag, ApiProfile, NativeSig, NativeToken, Profile
from lattice.typesys import Scalar, Schema
from tests.api.realapps_natives import NATIVES as NATIVES
from tests.api.realapps_natives import create_access_token, hash_password

S = Scalar
NATIVES_FILE = Path(__file__).resolve().parent / "realapps_natives.py"

USER = Schema.from_pairs(
    [
        ("id", S.UUID),
        ("email", S.STR),
        ("is_active", S.BOOL),
        ("is_superuser", S.BOOL),
        ("full_name", S.STR),
        ("hashed_password", S.STR),
        ("created_at", S.DATETIME),
    ],
    keys=[("id",)],
    nullable=["full_name", "created_at"],
    private=["hashed_password"],
)
ITEM = Schema.from_pairs(
    [
        ("id", S.UUID),
        ("title", S.STR),
        ("description", S.STR),
        ("owner_id", S.UUID),
        ("created_at", S.DATETIME),
    ],
    keys=[("id",)],
    nullable=["description", "created_at"],
    owner="owner_id",
)
NOTE = Schema.from_pairs(
    [("id", S.UUID), ("item_id", S.UUID), ("body", S.STR)],
    keys=[("id",)],
    owner_via=("item_id", "Item"),
)

ITEM_PUBLIC = ("title", "description", "id", "owner_id", "created_at")
USER_PUBLIC = ("email", "is_active", "is_superuser", "full_name", "id", "created_at")

API = ApiProfile(
    tables={"User": USER, "Item": ITEM, "Note": NOTE},
    table_names={"User": "user", "Item": "item", "Note": "note"},
    roles=frozenset({"Public", "Customer", "Admin"}),
    users_table="User",
    page_shape={"items": "data", "total": "count", "limit": "", "offset": ""},
    responses={
        "ItemPublic": ITEM_PUBLIC,
        "UserPublic": USER_PUBLIC,
        "Token": ("access_token", "token_type"),
    },
    natives={
        "hash_password": NativeSig((S.STR,), S.STR),
        "verify_password": NativeSig((S.STR, S.STR), S.BOOL, reads_private=True),
        "create_access_token": NativeSig((S.UUID,), S.STR),
        "decode_token": NativeSig((S.STR,), S.UUID, nullable=True),
        "shout": NativeSig((S.STR,), S.STR),
        "year_start": NativeSig((S.DATETIME,), S.DATETIME),
        "maybe_len": NativeSig((S.STR,), S.INT, nullable=True),
        "broken": NativeSig((S.STR,), S.STR),
    },
    auth=NativeToken("decode_token"),
    roles_from=AdminFlag("is_superuser", "Customer"),
)
PROFILE = Profile(schemas={}, api=API)

# Seeded ids share a prefix (and seeded instants are in the year 2000), so generated
# values are recognizable: see `normalize`.
SEED_PREFIX = "00000000-0000-4000-8000-"


def uid(n: int) -> str:
    return f"{SEED_PREFIX}{n:012d}"


ADMIN, ALICE, BOB, CAROL = uid(1), uid(2), uid(3), uid(4)
USERS = {"admin": ADMIN, "alice": ALICE, "bob": BOB, "carol": CAROL}
TOKENS = {name: create_access_token(uuid.UUID(u)) for name, u in USERS.items()}
PASSWORDS = {"admin": "admin-pass", "alice": "alice-pass", "bob": "bob-pass", "carol": "carol-pw"}
# items: (id, title, description, owner, created_at)
ITEMS = [
    (uid(101), "Alpha", "first", ALICE, "2000-01-05T10:00:00.000000Z"),
    (uid(102), "Beta", None, ALICE, "2000-01-03T10:00:00.000000Z"),
    (uid(103), "Gamma", "third", BOB, "2000-01-04T10:00:00.000000Z"),
    (uid(104), "Delta", "fourth", ALICE, "2000-01-01T10:00:00.000000Z"),
    (uid(105), "Epsilon", "", BOB, "2000-01-02T10:00:00.000000Z"),
    (uid(106), "Zeta", "sixth", ADMIN, "2000-01-06T10:00:00.000000Z"),
]
NOTES = [
    (uid(201), uid(101), "note on alpha"),
    (uid(202), uid(103), "note on gamma"),
]


def build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE "user" (
                id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, is_active INTEGER NOT NULL,
                is_superuser INTEGER NOT NULL, full_name TEXT, hashed_password TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE item (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT,
                owner_id TEXT NOT NULL REFERENCES "user"(id), created_at TEXT
            );
            CREATE TABLE note (
                id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES item(id),
                body TEXT NOT NULL
            );
            """
        )
        users = [
            (ADMIN, "admin@example.com", 1, 1, "Admin Istrator", "admin", "2000-01-01"),
            (ALICE, "alice@example.com", 1, 0, "Alice Anders", "alice", "2000-01-02"),
            (BOB, "bob@example.com", 1, 0, None, "bob", "2000-01-03"),
            (CAROL, "carol@example.com", 0, 0, "Carol Chen", "carol", "2000-01-04"),
        ]
        conn.executemany(
            'INSERT INTO "user" VALUES (?, ?, ?, ?, ?, ?, ?)',
            [
                (i, e, a, s, f, hash_password(PASSWORDS[h]), f"{d}T09:00:00.000000Z")
                for i, e, a, s, f, h, d in users
            ],
        )
        conn.executemany("INSERT INTO item VALUES (?, ?, ?, ?, ?)", ITEMS)
        conn.executemany("INSERT INTO note VALUES (?, ?, ?)", NOTES)
        conn.commit()
    finally:
        conn.close()


PROGRAM = """# lattice: v1
@endpoint("GET", "/items/", auth=Customer, response="ItemPublic")
def read_items(user: CurrentUser, skip: Int(ge=0) = 0, limit: Int(ge=1, le=100) = 100) -> Page:
    items = fetch(Item)
    mine = owned(items, by=user)
    ordered = sort(mine, by=["created_at"], desc=True)
    result = page(ordered, limit=limit, offset=skip, total=True)
    return result


@endpoint("GET", "/items/{id}", auth=Customer, response="ItemPublic")
def read_item(id: Uuid, user: CurrentUser) -> One | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    mine = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    return mine


@endpoint("POST", "/items/", auth=Customer, response="ItemPublic")
def create_item(
    user: CurrentUser, title: Str(min_len=1, max_len=255), description: Str(max_len=255) = UNSET
) -> Created:
    item = insert(Item, owner=user, title=title, description=description, created_at=fn.now())
    return item


@endpoint("PUT", "/items/{id}", auth=Customer, response="ItemPublic")
def update_item(
    id: Uuid,
    user: CurrentUser,
    title: Str(min_len=1, max_len=255) = UNSET,
    description: Str(max_len=255) = UNSET,
) -> One | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    mine = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    done = update(mine, title=title, description=description)
    return done


@endpoint("DELETE", "/items/{id}", auth=Customer)
def delete_item(id: Uuid, user: CurrentUser) -> NoContent | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    mine = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    gone = delete(mine)
    return gone


@endpoint("GET", "/notes/{id}", auth=Customer)
def read_note(id: Uuid, user: CurrentUser) -> One | NotFound | Forbidden:
    notes = fetch(Note)
    found = where(notes, col("id") == id)
    note = one(found, else_=NotFound("Note not found"))
    mine = require(note, owner_is(user), else_=Forbidden("Not your note"))
    return mine


@endpoint("GET", "/users/me", auth=Customer, response="UserPublic")
def read_user_me(user: CurrentUser) -> One:
    shown = select(user, cols=USER_PUBLIC_COLS)
    return shown


@endpoint("PATCH", "/users/me", auth=Customer, response="UserPublic")
def update_user_me(
    user: CurrentUser, full_name: Str(max_len=255) = UNSET, email: Str(max_len=255) = UNSET
) -> One:
    done = update(user, full_name=full_name, email=email)
    shown = select(done, cols=USER_PUBLIC_COLS)
    return shown


@endpoint("GET", "/users/", auth=Admin, response="UserPublic")
def read_users(user: CurrentUser, skip: Int(ge=0) = 0, limit: Int(ge=1, le=100) = 100) -> Page:
    users = fetch(User)
    public = select(users, cols=USER_PUBLIC_COLS)
    ordered = sort(public, by=["created_at"])
    result = page(ordered, limit=limit, offset=skip, total=True)
    return result


@endpoint("POST", "/users/signup", auth=Public, response="UserPublic")
def register_user(
    email: Str(max_len=255),
    password: Str(min_len=8, max_len=40),
    full_name: Str(max_len=255) = UNSET,
) -> Created:
    hashed = native.hash_password(password)
    created = insert(
        User,
        email=email,
        hashed_password=hashed,
        full_name=full_name,
        is_active=True,
        is_superuser=False,
        created_at=fn.now(),
    )
    shown = select(created, cols=USER_PUBLIC_COLS)
    return shown


@endpoint("POST", "/login/access-token", auth=Public, response="Token")
def login(username: Str(max_len=255), password: Str(max_len=255)) -> One | BadRequest:
    users = fetch(User)
    found = where(users, col("email") == username)
    account = one(found, else_=BadRequest("Incorrect email or password"))
    verified = require(
        account,
        native.verify_password(password, col("hashed_password")),
        else_=BadRequest("Incorrect email or password"),
    )
    active = require(verified, col("is_active"), else_=BadRequest("Inactive user"))
    token = derive(active, access_token=native.create_access_token(col("id")), token_type="bearer")
    shown = select(token, cols=["access_token", "token_type"])
    return shown


@endpoint("GET", "/clock/{id}", auth=Customer)
def clock(id: Uuid, user: CurrentUser) -> One | NotFound:
    items = fetch(Item)
    mine = owned(items, by=user)
    found = where(mine, col("id") == id)
    stamped = derive(
        found,
        a=fn.now(),
        b=fn.now(),
        day=fn.date(fn.now()),
        older=col("created_at") < fn.now(),
        created_day=fn.date(col("created_at")),
    )
    shown = select(stamped, cols=["id", "a", "b", "day", "older", "created_day"])
    item = one(shown, else_=NotFound("Item not found"))
    return item


@endpoint("GET", "/items/since", auth=Customer, response="ItemPublic")
def items_since(
    user: CurrentUser, after: DateTime = "2000-01-02T00:00:00Z", limit: Int(ge=1, le=10) = 10
) -> Page:
    items = fetch(Item)
    mine = owned(items, by=user)
    recent = where(mine, col("created_at") > after)
    ordered = sort(recent, by=["created_at"])
    result = page(ordered, limit=limit, total=True)
    return result


@endpoint("GET", "/admin/alice-items", auth=Admin)
def alice_items(user: CurrentUser) -> Page:
    items = fetch(Item)
    hers = where(items, col("owner_id") == fn.to_uuid("00000000-0000-4000-8000-000000000002"))
    early = where(
        hers, col("created_at") <= fn.to_datetime("2000-01-03 10:00", format="%Y-%m-%d %H:%M")
    )
    ordered = sort(early, by=["created_at"])
    shown = select(ordered, cols=["id", "title", "created_at"])
    result = page(shown, limit=10)
    return result


@endpoint("GET", "/items/{id}/loud", auth=Customer)
def loud_item(id: Uuid, user: CurrentUser) -> One | NotFound | BadRequest:
    items = fetch(Item)
    mine = owned(items, by=user)
    found = where(mine, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    long = require(item, native.shout(col("title")) != "BETA!", else_=BadRequest("no beta"))
    loud = derive(
        long,
        loud=native.shout(col("title")),
        size=native.maybe_len(col("description")),
        year=native.year_start(col("created_at")),
    )
    shown = select(loud, cols=["id", "loud", "size", "year"])
    return shown


@endpoint("GET", "/items/{id}/broken", auth=Customer)
def broken_item(id: Uuid, user: CurrentUser) -> One | NotFound:
    items = fetch(Item)
    mine = owned(items, by=user)
    found = where(mine, col("id") == id)
    bad = derive(found, oops=native.broken(col("title")))
    shown = select(bad, cols=["id", "oops"])
    item = one(shown, else_=NotFound("Item not found"))
    return item


@endpoint("POST", "/items/{id}/copy", auth=Customer, response="ItemPublic")
def copy_item(id: Uuid, user: CurrentUser, new_id: Uuid) -> Created | NotFound:
    items = fetch(Item)
    mine = owned(items, by=user)
    found = where(mine, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    made = insert(Item, id=new_id, owner=user, title="copy", created_at=fn.now())
    return made
""".replace(
    "USER_PUBLIC_COLS", '["email", "is_active", "is_superuser", "full_name", "id", "created_at"]'
)
