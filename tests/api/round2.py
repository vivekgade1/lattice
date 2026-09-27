"""Round-2 fixes fixture (spec 2026-09-27 lattice-t2 §6): the real-apps profile
(tests/api/realapps.py) with ``null_order="smallest"`` and a program exercising

1. the profile's null order: `sort` without `nulls=` and `window` order over the
   nullable ``Item.description`` (Beta's is null), next to an explicit `nulls="last"`;
2. caller references in T0 expressions: ``user.id``, ``user.is_admin``,
   ``is_admin(user)`` and public user columns (``user.email``);
3. ``accessible(t, by=user)`` (the preferred spelling of ``owned``);
4. owner transfer: ``update(t, owner=<one-row users table>)`` and
   ``update(t, owner=<Uuid>, else_=NotFound(...))``.

Shared by tests/api/test_round2_runtime.py and the codegen differential.
"""

from __future__ import annotations

import dataclasses

from lattice.profile import Profile
from tests.api import realapps

API = dataclasses.replace(realapps.API, null_order="smallest")
PROFILE = Profile(schemas={}, api=API)
LAST_PROFILE = realapps.PROFILE  # the same profile with the default null order
NATIVES = realapps.NATIVES
NATIVES_FILE = realapps.NATIVES_FILE
TOKENS = realapps.TOKENS
build_db = realapps.build_db
uid = realapps.uid
ADMIN, ALICE, BOB, CAROL = realapps.ADMIN, realapps.ALICE, realapps.BOB, realapps.CAROL
ALPHA, BETA, GAMMA, ZETA = uid(101), uid(102), uid(103), uid(106)
NOBODY = uid(999)

PROGRAM = """# lattice: v1
ITEM_PUBLIC = ["title", "description", "id", "owner_id", "created_at"]


@endpoint("GET", "/sorted/descriptions", auth=Admin)
def by_description(user: CurrentUser, newest: Bool = False) -> Page:
    items = fetch(Item)
    ordered = sort(items, by=["description"], desc=newest)
    shown = select(ordered, cols=["title", "description"])
    result = page(shown, limit=100)
    return result


@endpoint("GET", "/sorted/descriptions/last", auth=Admin)
def by_description_last(user: CurrentUser) -> Page:
    items = fetch(Item)
    ordered = sort(items, by=["description"], nulls="last")
    shown = select(ordered, cols=["title", "description"])
    result = page(shown, limit=100)
    return result


@endpoint("GET", "/sorted/numbered", auth=Admin)
def numbered(user: CurrentUser) -> Page:
    items = fetch(Item)
    ranked = window(items, order=["description", "id"], n=win.row_number())
    back = window(ranked, order=["description", "id"], desc=["description"], m=win.row_number())
    shown = select(back, cols=["title", "n", "m"])
    result = page(shown, limit=100)
    return result


@endpoint("GET", "/me/items", auth=Customer, response="ItemPublic")
def my_items_by_id(user: CurrentUser) -> Page:
    items = fetch(Item)
    visible = accessible(items, by=user)
    own = where(visible, col("owner_id") == user.id)
    ordered = sort(own, by=["title"])
    shown = select(ordered, cols=ITEM_PUBLIC)
    result = page(shown, limit=100, total=True)
    return result


@endpoint("GET", "/me/flags", auth=Customer)
def flags(user: CurrentUser) -> Page:
    items = fetch(Item)
    visible = accessible(items, by=user)
    marked = derive(
        visible,
        mine=col("owner_id") == user.id,
        admin=is_admin(user),
        admin2=user.is_admin,
        who=user.email,
        name=coalesce(user.full_name, "?"),
        since=user.created_at,
        scope=when(user.is_admin, then="all", otherwise="own"),
    )
    shown = select(
        marked, cols=["title", "mine", "admin", "admin2", "who", "name", "since", "scope"]
    )
    ordered = sort(shown, by=["title"])
    result = page(ordered, limit=100)
    return result


@endpoint("PUT", "/items/{id}/stamp", auth=Customer, response="ItemPublic")
def stamp(id: Uuid, user: CurrentUser) -> One | NotFound | Forbidden:
    items = fetch(Item)
    visible = accessible(items, by=user)
    found = where(visible, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    ok = require(item, ~is_admin(user), else_=Forbidden("Admins do not stamp"))
    updated = update(item, description=fn.lower(user.email))
    shown = select(updated, cols=ITEM_PUBLIC)
    return shown


@endpoint("POST", "/items/signed", auth=Customer, response="ItemPublic")
def create_signed(user: CurrentUser, title: Str(min_len=1, max_len=255)) -> One:
    item = insert(Item, owner=user, title=title, description=user.email, created_at=fn.now())
    shown = select(item, cols=ITEM_PUBLIC)
    return shown


@endpoint("POST", "/items/{id}/transfer", auth=Customer, response="ItemPublic")
def transfer_item(
    id: Uuid, user: CurrentUser, new_owner_email: Email(max_len=255)
) -> One | NotFound | Forbidden | BadRequest:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    allowed = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    users = fetch(User)
    matched = where(users, col("email") == new_owner_email)
    target = one(matched, else_=NotFound("User not found"))
    active = require(target, col("is_active"), else_=BadRequest("Inactive user"))
    new_id = select(active, cols=["id"])
    renamed = rename(new_id, id="new_owner_id")
    paired = join(allowed, renamed, on=[])
    fresh = require(
        paired, col("owner_id") != col("new_owner_id"), else_=BadRequest("Already theirs")
    )
    moved = update(allowed, owner=active)
    shown = select(moved, cols=ITEM_PUBLIC)
    return shown


@endpoint("PUT", "/items/{id}/owner", auth=Customer, response="ItemPublic")
def give_item(id: Uuid, user: CurrentUser, owner_id: Uuid) -> One | NotFound:
    items = fetch(Item)
    visible = accessible(items, by=user)
    found = where(visible, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    moved = update(
        item, owner_id=owner_id, title=fn.upper(col("title")), else_=NotFound("User not found")
    )
    shown = select(moved, cols=ITEM_PUBLIC)
    return shown


@endpoint("PUT", "/items/{id}/claim", auth=Admin, response="ItemPublic")
def claim_item(id: Uuid, user: CurrentUser) -> One | NotFound:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    moved = update(item, owner=user)
    shown = select(moved, cols=ITEM_PUBLIC)
    return shown


@endpoint("PUT", "/items/mine/owner", auth=Customer)
def give_all(user: CurrentUser, owner_id: Uuid) -> NoContent | Conflict:
    items = fetch(Item)
    own = mine(items, by=user)
    moved = update(own, owner=owner_id, else_=Conflict("No such user"))
    return moved
"""
