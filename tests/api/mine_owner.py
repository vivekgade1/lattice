"""`mine(t, by=user)` fixture: the real-apps profile (tests/api/realapps.py; the admin
owns item Zeta) with one more note, on the admin's item, and a program contrasting
``mine`` (the caller's own rows, Admin included) with ``owned`` (the rows the caller may
access: an Admin sees every row), directly (``Item.owner_id``) and through a parent
(``Note`` is owned through ``item_id -> Item``).

Shared by tests/api/test_mine_runtime.py and the codegen differential.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.api import realapps

PROFILE = realapps.PROFILE
NATIVES = realapps.NATIVES
NATIVES_FILE = realapps.NATIVES_FILE
TOKENS = realapps.TOKENS
uid = realapps.uid

ADMIN_NOTE = uid(203)  # a note on Zeta, the admin's item
ZETA = uid(106)
ALPHA = uid(101)  # alice's


def build_db(path: Path) -> None:
    realapps.build_db(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("INSERT INTO note VALUES (?, ?, 'note on zeta')", (ADMIN_NOTE, ZETA))
        conn.commit()
    finally:
        conn.close()


PROGRAM = """# lattice: v1
ITEM_PUBLIC = ["title", "description", "id", "owner_id", "created_at"]


@endpoint("GET", "/items/mine", auth=Customer, response="ItemPublic")
def my_items(user: CurrentUser, skip: Int(ge=0) = 0, limit: Int(ge=0, le=100) = 100) -> Page:
    items = fetch(Item)
    own = mine(items, by=user)
    ordered = sort(own, by=["created_at"], desc=True)
    shown = select(ordered, cols=ITEM_PUBLIC)
    result = page(shown, limit=limit, offset=skip, total=True)
    return result


@endpoint("GET", "/items/visible", auth=Customer, response="ItemPublic")
def visible_items(user: CurrentUser, skip: Int(ge=0) = 0, limit: Int(ge=0, le=100) = 100) -> Page:
    items = fetch(Item)
    visible = owned(items, by=user)
    ordered = sort(visible, by=["created_at"], desc=True)
    shown = select(ordered, cols=ITEM_PUBLIC)
    result = page(shown, limit=limit, offset=skip, total=True)
    return result


@endpoint("GET", "/items/mine/stats", auth=Customer)
def my_item_stats(user: CurrentUser) -> One:
    items = fetch(Item)
    own = mine(items, by=user)
    stats = group(
        own, by=[], count=agg.count(), latest_created_at=agg.max(col("created_at"), nulls="skip")
    )
    return stats


@endpoint("GET", "/notes/mine", auth=Customer)
def my_notes(user: CurrentUser) -> Page:
    notes = fetch(Note)
    own = mine(notes, by=user)
    ordered = sort(own, by=["id"])
    result = page(ordered, limit=100)
    return result


@endpoint("GET", "/notes/visible", auth=Customer)
def visible_notes(user: CurrentUser) -> Page:
    notes = fetch(Note)
    visible = owned(notes, by=user)
    ordered = sort(visible, by=["id"])
    result = page(ordered, limit=100)
    return result


@endpoint("PUT", "/items/mine/{id}", auth=Customer, response="ItemPublic")
def retitle_my_item(
    id: Uuid, user: CurrentUser, title: Str(min_len=1, max_len=255)
) -> One | NotFound:
    items = fetch(Item)
    own = mine(items, by=user)
    found = where(own, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    updated = update(item, title=title)
    shown = select(updated, cols=ITEM_PUBLIC)
    return shown
"""
