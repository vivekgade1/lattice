"""Edit-slice gap features fixture (plan 2026-09-27 thin slice): the real-apps profile
(tests/api/realapps.py) with a nullable ``deleted_at`` column on items, and a program
using the six language features the thin-slice tickets needed:

1. ``fn.contains(x, sub=q, ignore_case=True)`` -- case-insensitive substring match;
2. ``sent(p)`` -- whether an optional (``= UNSET``) body field was sent;
3. ``fn.datetime_add(x, days=, hours=, minutes=, seconds=)`` and
   ``fn.datetime_diff(end, start, unit=)`` -- DateTime arithmetic;
4. ``p: T | None = None`` -- an optional parameter that is None when not given;
5. ``update(t, c=None)`` / ``insert(T, c=None)`` -- a null written to a nullable column;
6. ``sort(t, by=[...], desc=[<Bool from parameters>], nulls="smallest")`` -- the sort
   direction from a parameter, with an explicit nulls policy.

Shared by tests/api/test_gap_features.py and the codegen differential.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from lattice.profile import Profile
from lattice.typesys import Scalar, Schema
from tests.api import realapps

NATIVES = realapps.NATIVES
NATIVES_FILE = realapps.NATIVES_FILE
TOKENS = realapps.TOKENS
uid = realapps.uid

ITEM = Schema.from_pairs(
    [*realapps.ITEM.columns, ("deleted_at", Scalar.DATETIME)],
    keys=[("id",)],
    nullable=["description", "created_at", "deleted_at"],
    owner="owner_id",
)
API = replace(realapps.API, tables={**realapps.API.tables, "Item": ITEM})
PROFILE = Profile(schemas={}, api=API)

LOCKED = "Title can only be changed within 24 hours of creation"
# a soft-deleted item of alice's, and an undated one of bob's
DELETED, UNDATED = uid(104), uid(105)
FUTURE = uid(108)  # carol's


def build_db(path: Path) -> None:
    realapps.build_db(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("ALTER TABLE item ADD COLUMN deleted_at TEXT")
        conn.execute(
            "UPDATE item SET deleted_at = '2000-02-01T00:00:00.000000Z' WHERE id = ?", (DELETED,)
        )
        conn.execute("UPDATE item SET created_at = NULL WHERE id = ?", (UNDATED,))
        conn.execute(
            "INSERT INTO item VALUES (?, 'weekly GROCERIES', NULL, ?, "
            "'2000-01-07T10:00:00.000000Z', NULL)",
            (uid(107), realapps.ALICE),
        )
        # created "in the future": inside any title window, whenever the tests run
        conn.execute(
            "INSERT INTO item VALUES (?, 'Future plan', 'later', ?, "
            "'2999-01-01T00:00:00.000000Z', NULL)",
            (FUTURE, realapps.CAROL),
        )
        conn.commit()
    finally:
        conn.close()


PROGRAM = """# lattice: v1
ITEM_PUBLIC = ["title", "description", "id", "owner_id", "created_at"]
TIMES = ["id", "next_day", "back", "hours", "days", "minutes", "seconds", "recent"]


@endpoint("GET", "/items/", auth=Customer, response="ItemPublic")
def read_items(
    user: CurrentUser,
    skip: Int(ge=0) = 0,
    limit: Int(ge=0, le=100) = 100,
    q: Str(max_len=100) | None = None,
    since: DateTime | None = None,
    sort: OneOf("newest", "oldest") = "newest",
) -> Page:
    items = fetch(Item)
    mine = owned(items, by=user)
    live = where(mine, is_null(col("deleted_at")))
    matching = where(live, is_null(q) | fn.contains(col("title"), sub=q, ignore_case=True))
    recent = where(matching, is_null(since) | (col("created_at") >= since))
    ordered = sort(recent, by=["created_at"], desc=[sort == "newest"], nulls="smallest")
    shown = select(ordered, cols=ITEM_PUBLIC)
    result = page(shown, limit=limit, offset=skip, total=True)
    return result


@endpoint("GET", "/items/ordered", auth=Customer)
def ordered_items(user: CurrentUser, newest_first: Bool = False) -> Page:
    items = fetch(Item)
    mine = owned(items, by=user)
    ordered = sort(mine, by=["created_at", "title"], desc=[newest_first, False], nulls="first")
    shown = select(ordered, cols=["id", "title", "created_at"])
    result = page(shown, limit=100)
    return result


@endpoint("GET", "/items/ordered-last", auth=Customer)
def ordered_last(user: CurrentUser, newest_first: Bool = False) -> Page:
    items = fetch(Item)
    mine = owned(items, by=user)
    ordered = sort(mine, by=["created_at", "title"], desc=newest_first)
    shown = select(ordered, cols=["id", "title", "created_at"])
    result = page(shown, limit=100)
    return result


@endpoint("PUT", "/items/{id}", auth=Customer, response="ItemPublic")
def update_item(
    id: Uuid,
    user: CurrentUser,
    title: Str(min_len=1, max_len=255) = UNSET,
    description: Str(max_len=255) = UNSET,
) -> One | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, (col("id") == id) & is_null(col("deleted_at")))
    item = one(found, else_=NotFound("Item not found"))
    allowed = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    me = select(user, cols=["is_superuser"])
    judged = join(allowed, me, on=[])
    in_window = require(
        judged,
        col("is_superuser")
        | ~sent(title)
        | is_null(col("created_at"))
        | (col("created_at") >= fn.datetime_add(fn.now(), hours=-24)),
        else_=Forbidden("LOCKED"),
    )
    updated = update(allowed, title=title, description=description)
    shown = select(updated, cols=ITEM_PUBLIC)
    return shown


@endpoint("POST", "/items/", auth=Customer, response="ItemPublic")
def create_item(user: CurrentUser, title: Str(min_len=1, max_len=255)) -> Created:
    item = insert(Item, owner=user, title=title, description=None, created_at=fn.now())
    shown = select(item, cols=ITEM_PUBLIC)
    return shown


@endpoint("DELETE", "/items/{id}", auth=Customer)
def delete_item(id: Uuid, user: CurrentUser) -> NoContent | NotFound:
    items = fetch(Item)
    mine = owned(items, by=user)
    found = where(mine, (col("id") == id) & is_null(col("deleted_at")))
    item = one(found, else_=NotFound("Item not found"))
    gone = update(item, deleted_at=fn.now())
    return gone


@endpoint("POST", "/items/{id}/restore", auth=Admin, response="ItemPublic")
def restore_item(id: Uuid, user: CurrentUser) -> One | NotFound | BadRequest:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    deleted = require(item, ~is_null(col("deleted_at")), else_=BadRequest("Item is not deleted"))
    restored = update(deleted, deleted_at=None)
    shown = select(restored, cols=ITEM_PUBLIC)
    return shown


@endpoint("GET", "/items/{id}/times", auth=Customer)
def item_times(
    id: Uuid, user: CurrentUser, start: DateTime = "2000-01-01T00:00:00Z"
) -> One | NotFound:
    items = fetch(Item)
    mine = owned(items, by=user)
    found = where(mine, col("id") == id)
    timed = derive(
        found,
        next_day=fn.datetime_add(col("created_at"), days=1, minutes=-30),
        back=fn.datetime_add(col("created_at"), seconds=-90),
        hours=fn.datetime_diff(col("created_at"), start, unit="hours"),
        days=fn.datetime_diff(start, col("created_at"), unit="days"),
        minutes=fn.datetime_diff(col("created_at"), start, unit="minutes"),
        seconds=fn.datetime_diff(col("created_at"), start, unit="seconds"),
        recent=col("created_at") >= fn.datetime_add(fn.now(), days=-1),
    )
    shown = select(timed, cols=TIMES)
    item = one(shown, else_=NotFound("Item not found"))
    return item
""".replace('"LOCKED"', f'"{LOCKED}"')
