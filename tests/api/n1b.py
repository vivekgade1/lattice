"""N1b fixture (spec 2026-09-27 lattice-surface-design §1): a program on the gap-features
profile (tests/api/gaps.py: Uuid keys, DateTime, nullable columns) using the three
accepted spellings that need more than a syntactic rewrite:

1. a named column expression (`copy_title = allowed.title + " (copy)"`, `age = ...`):
   a macro, substituted at each use;
2. values read from a one-row table in insert/update/derive (`allowed.title`): a scalar
   subquery over that row's materialized step;
3. `when(..., then=None)` / `otherwise=None`: a typed null (the result is nullable).

Shared by tests/check/test_normalise_n1b.py and the codegen differential.
"""

from __future__ import annotations

from tests.api import gaps

PROFILE = gaps.PROFILE
NATIVES = gaps.NATIVES
NATIVES_FILE = gaps.NATIVES_FILE
TOKENS = gaps.TOKENS
build_db = gaps.build_db
uid = gaps.uid

PROGRAM = """# lattice: v1
ITEM_PUBLIC = ["title", "description", "id", "owner_id", "created_at"]


@endpoint("POST", "/items/{id}/duplicate", auth=Customer, response="ItemPublic")
def duplicate_item(id: Uuid, user: CurrentUser) -> Created | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    allowed = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    copy_title = allowed.title + " (copy)"
    copied = insert(
        Item,
        owner=user,
        title=copy_title,
        description=allowed.description,
        created_at=allowed.created_at,
    )
    shown = select(copied, cols=ITEM_PUBLIC)
    return shown


@endpoint("PUT", "/items/{id}/title-from/{other}", auth=Customer, response="ItemPublic")
def title_from(id: Uuid, other: Uuid, user: CurrentUser) -> One | NotFound:
    items = fetch(Item)
    mine = owned(items, by=user)
    source_rows = where(mine, col("id") == other)
    source = one(source_rows, else_=NotFound("Source not found"))
    target_rows = where(mine, col("id") == id)
    target = one(target_rows, else_=NotFound("Item not found"))
    updated = update(
        target,
        title=source.title,
        description=when(is_null(source.description), then=None, otherwise=source.description),
    )
    shown = select(updated, cols=ITEM_PUBLIC)
    return shown


@endpoint("GET", "/items/{id}/around", auth=Customer)
def around(id: Uuid, user: CurrentUser) -> Page | NotFound:
    items = fetch(Item)
    mine = owned(items, by=user)
    anchor_rows = where(mine, col("id") == id)
    anchor = one(anchor_rows, else_=NotFound("Item not found"))
    age = fn.datetime_diff(col("created_at"), anchor.created_at, unit="days")
    is_anchor = col("id") == id
    marked = derive(
        mine,
        anchor_title=anchor.title,
        days=age,
        other_title=when(is_anchor, then=None, otherwise=col("title")),
    )
    ordered = sort(marked, by=["id"])
    shown = select(ordered, cols=["id", "anchor_title", "days", "other_title"])
    result = page(shown, limit=100)
    return result


@endpoint("POST", "/items/{id}/stamp", auth=Customer, response="ItemPublic")
def stamp(id: Uuid, user: CurrentUser, clear: Bool = False) -> One | NotFound:
    items = fetch(Item)
    mine = owned(items, by=user)
    found = where(mine, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    stamped = update(item, created_at=when(clear, then=None, otherwise=fn.now()))
    shown = select(stamped, cols=ITEM_PUBLIC)
    return shown
"""
