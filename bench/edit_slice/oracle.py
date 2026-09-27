"""The hidden acceptance oracle of the edit slice (plan 2026-09-27 thin slice, node E1).

Per ticket (:mod:`bench.edit_slice.tickets`) the acceptance suite is

* the **generated suite** (:func:`lattice.importer.suite.generate_suite`) for the routes
  the ticket touches, described as :class:`RouteSpec` values of the *reference*
  implementation (so new parameters and routes get their own boundary, auth, ownership
  and pagination cases), seeded through the reference's own SQLModel classes;
* **ticket-specific cases** (named ``<t>.<what>``, e.g. ``t1.alice_other_users_match``)
  that pin each trap, on database variants that add the rows the trap needs (other
  users' items with matching titles, soft-deleted items, items created an hour / 23 h /
  25 h ago, a user with no items);
* the **pre-declared Lattice policy deviations**, reused from the template proof
  (``tests/template/test_template_generated_suite.py``): 401 for a bad token or a token
  naming no user, a bounded non-negative page window (422), and 422 for a null written
  into a NOT NULL column. A declared deviation is *accepted*, not required
  (``Deviation(accept_original=True)``): a candidate may answer as the reference does or
  as declared (a Python-edited app keeps the template's behaviour, a Lattice-emitted one
  has the policy).

:func:`evaluate` runs the suite with :func:`lattice.importer.verify.verify`, the reference
app as the original and the candidate as the import, each case on a fresh copy of the
same seeded database, and classifies the candidate:

* **correct** -- every case is equal, equal under a representation rule
  (:data:`RULES`: the 422 envelope wording and the datetime wire spelling), or an
  accepted deviation;
* **silent** -- some case got a wrong non-5xx answer or a wrong database effect;
* **loud** -- otherwise, some case got a 5xx where the reference did not, or the app
  failed to import or start (or its login broke, aborting the suite).

Silent dominates loud: one silent case makes the run silent even if another crashed. A
case where the reference itself answers 5xx (the template's IntegrityError on a null
title) is equal when the candidate crashes the same way.

This module describes the template's tables, auth and tickets (t1-t12). A ticket on a
held-out repo (``Ticket.repo``: t19-t24) gets its oracle from :mod:`bench.edit_slice.repos`
in the same :class:`TicketOracle` shape; three knobs cover what differs there: the
password-hash columns the normaliser hides, a frozen clock the whole suite runs under
(JustNotes stamps rows with ``int(time.time())``, which no normaliser can tell apart), and
a record canonicaliser tried on a mismatch (TaskFlow stamps ``updated_at`` with a fresh
``utc_now()`` per write: one or several stamps in one request are equally valid).
"""

from __future__ import annotations

import tempfile
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from functools import cache
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import Engine, text
from sqlmodel import Session

from bench.edit_slice.reference import make_reference_app, reference_variant
from bench.edit_slice.tickets import ALL_TICKETS, TICKETS, ticket
from bench.template_app import sqlite_engine
from bench.template_app.app.core import security
from bench.template_app.app.core.config import settings
from lattice.importer.suite import (
    ACTOR,
    ADMIN,
    INACTIVE,
    MISSING_ID,
    OTHER,
    AuthSpec,
    ColumnSpec,
    ErrorSpec,
    GeneratedSuite,
    ParamSpec,
    RouteSpec,
    SeedPlan,
    TableSpec,
    generate_suite,
    sqlmodel_insert_hook,
)
from lattice.importer.verify import (
    Case,
    CaseRecord,
    DatetimeWire,
    Deviation,
    Login,
    Mismatch,
    ValidationEnvelope,
    agreement,
    classify,
    verify,
)

API = "/api/v1"
AppFactory = Callable[[Path], Any]
Kind = Literal["correct", "loud", "silent"]
Verdict = Literal["equal", "equal_under_rule", "deviation", "silent", "loud"]

# The representation rules: rarely needed (the Lattice profile uses the template's error
# bodies and naive datetime wire), kept so a spelling difference is not a failure.
RULES = (ValidationEnvelope(), DatetimeWire())

# ---- the template's tables and auth, described (as in the template proof) ----------------

C = ColumnSpec
USER = TableSpec(
    "user",
    (
        C("email", "email", unique=True, max_len=255),
        C("is_active", "bool"),
        C("is_superuser", "bool"),
        C("full_name", "str", nullable=True, max_len=255),
        C("id", "uuid", primary_key=True),
        C("hashed_password", "str"),
        C("created_at", "datetime", nullable=True),
    ),
)
# deleted_at (soft-delete tickets) is left out on purpose: the seed keeps every item live
# (the model's default), and the ticket cases add the deleted rows.
ITEM = TableSpec(
    "item",
    (
        C("title", "str", min_len=1, max_len=255),
        C("description", "str", nullable=True, max_len=255),
        C("id", "uuid", primary_key=True),
        C("created_at", "datetime", nullable=True),
        C("owner_id", "uuid", foreign_key="user.id"),
    ),
    owner="owner_id",
)


def mint_token(user_id: uuid.UUID) -> str:
    """An access token for ``user_id``, as the template's login issues it."""
    expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return security.create_access_token(user_id, expires_delta=expires)


AUTH = AuthSpec(
    users_table="user",
    login_path=f"{API}/login/access-token",
    hash_password=security.get_password_hash,
    mint_token=mint_token,
)

P = ParamSpec
ID = P("id", "uuid", "path")
PAGE = (
    P("skip", "int", "query", required=False, default=0),
    P("limit", "int", "query", required=False, default=100),
)
NOT_FOUND = ErrorSpec(404, "Item not found", "not_found")
NOT_OWNER = ErrorSpec(403, "Not enough permissions", "not_owner")
NOT_ADMIN = ErrorSpec(403, "The user doesn't have enough privileges", "not_admin")
TITLE = P("title", "str", "body", min_len=1, max_len=255)
DESCRIPTION = P("description", "str", "body", required=False, nullable=True, max_len=255)
UPDATE_TITLE = P("title", "str", "body", required=False, nullable=True, min_len=1, max_len=255)


def _read_items(*extra: ParamSpec) -> RouteSpec:
    return RouteSpec("read_items", "GET", f"{API}/items/", "list", "item",
                     params=(*PAGE, *extra), pagination=("skip", "limit"))  # fmt: skip


READ_ITEM = RouteSpec("read_item", "GET", f"{API}/items/{{id}}", "read", "item", params=(ID,),
                      errors=(NOT_FOUND, NOT_OWNER))  # fmt: skip
CREATE_ITEM = RouteSpec("create_item", "POST", f"{API}/items/", "create", "item",
                        params=(TITLE, DESCRIPTION))  # fmt: skip
UPDATE_ITEM = RouteSpec("update_item", "PUT", f"{API}/items/{{id}}", "update", "item",
                        params=(ID, UPDATE_TITLE, DESCRIPTION),
                        errors=(NOT_FOUND, NOT_OWNER))  # fmt: skip
DELETE_ITEM = RouteSpec("delete_item", "DELETE", f"{API}/items/{{id}}", "delete", "item",
                        params=(ID,), errors=(NOT_FOUND, NOT_OWNER))  # fmt: skip
RESTORE_ITEM = RouteSpec("restore_item", "POST", f"{API}/items/{{id}}/restore", "other", "item",
                         auth="admin", params=(ID,), errors=(NOT_FOUND, NOT_ADMIN))  # fmt: skip
ITEM_STATS = RouteSpec("read_item_stats", "GET", f"{API}/items/stats", "other", "item")

# ---- the held-out tickets' routes
USER_ID = P("user_id", "uuid", "path")
USER_NOT_FOUND = ErrorSpec(404, "User not found", "not_found")
TRANSFER_ITEM = RouteSpec("transfer_item", "POST", f"{API}/items/{{id}}/transfer", "other", "item",
                          params=(ID, P("new_owner_email", "email", "body")),
                          errors=(NOT_FOUND, NOT_OWNER))  # fmt: skip


def _user_switch(name: str, action: str) -> RouteSpec:
    return RouteSpec(name, "POST", f"{API}/users/{{user_id}}/{action}", "other", "user",
                     auth="admin", params=(USER_ID,),
                     errors=(USER_NOT_FOUND, NOT_ADMIN))  # fmt: skip


def _read_users(*extra: ParamSpec) -> RouteSpec:
    return RouteSpec("read_users", "GET", f"{API}/users/", "list", "user", auth="admin",
                     params=(*PAGE, *extra), pagination=("skip", "limit"),
                     errors=(NOT_ADMIN,))  # fmt: skip


USER_ITEM_COUNTS = RouteSpec("read_user_item_counts", "GET", f"{API}/users/item-counts",
                             "list", "user", auth="admin", params=PAGE,
                             pagination=("skip", "limit"), errors=(NOT_ADMIN,),
                             tables=("user", "item"))  # fmt: skip

ROUTES: dict[str, tuple[RouteSpec, ...]] = {
    "t1_search": (_read_items(P("q", "str", "query", required=False)),),
    "t2_since": (
        _read_items(
            P("since", "datetime", "query", required=False),
            P("sort", "str", "query", required=False, default="newest"),
        ),
    ),
    "t3_soft_delete": (_read_items(), READ_ITEM, CREATE_ITEM, UPDATE_ITEM, DELETE_ITEM),
    "t4_restore": (RESTORE_ITEM,),
    "t5_title_window": (UPDATE_ITEM,),
    "t6_stats": (ITEM_STATS,),
    "t7_deactivate": (
        _user_switch("deactivate_user", "deactivate"),
        _user_switch("activate_user", "activate"),
    ),
    "t8_user_filter": (
        _read_users(
            P("is_active", "bool", "query", required=False),
            P("q", "str", "query", required=False),
        ),
    ),
    "t9_item_quota": (CREATE_ITEM,),
    "t10_owner_counts": (USER_ITEM_COUNTS,),
    "t11_title_trim": (CREATE_ITEM, UPDATE_ITEM),
    "t12_transfer": (TRANSFER_ITEM,),
}

# ---- database variants --------------------------------------------------------------------


def extra_id(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://lattice.invalid/edit-slice/{name}")


EXTRA_EPOCH = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)

# name -> (owner handle, title, days after EXTRA_EPOCH / hours before now, deleted)
SEARCH_ROWS = (
    ("search/alice-groceries", ACTOR, "Weekly Groceries", 0),
    ("search/alice-tax", ACTOR, "Tax return 2025", 1),
    ("search/bob-groceries", OTHER, "Groceries for the party", 2),
    ("search/admin-grocery", ADMIN, "grocery run", 3),
)
SOFT_ROWS = (
    ("soft/alice", ACTOR, "Archived note", 0),
    ("soft/bob", OTHER, "Removed by Bob", 1),
    ("soft/admin", ADMIN, "Old admin item", 2),
)
FRESH_ROWS = (  # hours before the database is built
    ("fresh/alice-1h", ACTOR, "Fresh note", 1),
    ("fresh/alice-23h", ACTOR, "Almost a day old", 23),
    ("fresh/alice-25h", ACTOR, "Just over a day old", 25),
    ("fresh/bob-1h", OTHER, "Bob's fresh note", 1),
)


# the held-out tickets' extra users: name -> (email, active, admin, days after EXTRA_EPOCH)
EXTRA_USERS = {
    "dave": ("dave@example.com", True, True, 0),  # a second admin
    "erin": ("Erin.Stone@example.com", True, False, 1),
    "frank": ("frank@alice-corp.com", False, False, 2),
    "gina": ("gina@example.com", True, False, 3),  # owns nothing
}
QUOTA = 50  # t9_item_quota's limit


def _add_items(engine: Engine, ticket_id: str, rows: Sequence[dict[str, Any]]) -> None:
    item_model = reference_variant(ticket_id).module("models").Item
    with Session(engine) as session:
        for row in rows:
            session.add(item_model(**row))
        session.commit()


def _add_users(engine: Engine, ticket_id: str, names: Sequence[str]) -> None:
    user_model = reference_variant(ticket_id).module("models").User
    with Session(engine) as session:
        for name in names:
            email, active, admin, days = EXTRA_USERS[name]
            session.add(user_model(
                id=extra_id(f"user/{name}"), email=email, is_active=active,
                is_superuser=admin, full_name=name.title(), hashed_password="!unusable",
                created_at=EXTRA_EPOCH + timedelta(days=days),
            ))  # fmt: skip
        session.commit()


def _fill(engine: Engine, ticket_id: str, owner: uuid.UUID, tag: str, n: int) -> None:
    """Give ``owner`` ``n`` more items."""
    _add_items(engine, ticket_id, [
        {"id": extra_id(f"{tag}/{k}"), "title": f"Bulk item {k + 1}", "owner_id": owner,
         "created_at": EXTRA_EPOCH + timedelta(hours=k)}
        for k in range(n)
    ])  # fmt: skip


def _variant_writer(ticket_id: str, plan: SeedPlan) -> dict[str, Callable[[Engine], None]]:
    uid = {u.handle: u.id for u in plan.users}

    def search(engine: Engine) -> None:
        _add_items(engine, ticket_id, [
            {"id": extra_id(n), "title": t, "owner_id": uid[h],
             "created_at": EXTRA_EPOCH + timedelta(days=d)}
            for n, h, t, d in SEARCH_ROWS
        ])  # fmt: skip

    def soft(engine: Engine) -> None:
        _add_items(engine, ticket_id, [
            {"id": extra_id(n), "title": t, "owner_id": uid[h],
             "created_at": EXTRA_EPOCH + timedelta(days=d),
             "deleted_at": EXTRA_EPOCH + timedelta(days=10 + d)}
            for n, h, t, d in SOFT_ROWS
        ])  # fmt: skip

    def fresh(engine: Engine) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        _add_items(engine, ticket_id, [
            {"id": extra_id(n), "title": t, "owner_id": uid[h],
             "created_at": now - timedelta(hours=hours)}
            for n, h, t, hours in FRESH_ROWS
        ])  # fmt: skip

    def no_bob_items(engine: Engine) -> None:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM item WHERE owner_id = :o"), {"o": uid[OTHER].hex})

    def no_items(engine: Engine) -> None:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM item"))

    def undated(engine: Engine) -> None:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE item SET created_at = NULL WHERE owner_id = :o"),
                {"o": uid[ACTOR].hex},
            )

    def with_users(*names: str) -> Callable[[Engine], None]:
        return lambda engine: _add_users(engine, ticket_id, names)

    def owning(**extra: int) -> Callable[[Engine], None]:
        """Each seed user ``handle`` owns ``extra[handle]`` more items."""

        def write(engine: Engine) -> None:
            for handle, n in extra.items():
                _fill(engine, ticket_id, uid[handle], f"bulk/{handle}", n)

        return write

    def counts(engine: Engine) -> None:
        _add_users(engine, ticket_id, ["erin", "frank", "gina"])
        owning(alice=3, bob=1)(engine)
        _fill(engine, ticket_id, extra_id("user/frank"), "bulk/frank", 1)

    own = {h: len(plan.owned("item", h)) for h in uid}
    return {"search": search, "soft": soft, "fresh": fresh, "no_bob_items": no_bob_items,
            "undated": undated, "no_items": no_items,
            "second_admin": with_users("dave"),
            "more_users": with_users("dave", "erin", "frank", "gina"),
            "counts": counts,
            "alice_at_limit": owning(alice=QUOTA - own[ACTOR]),
            "alice_below_limit": owning(alice=QUOTA - 1 - own[ACTOR]),
            "admin_at_limit": owning(admin=QUOTA - own[ADMIN]),
            }  # fmt: skip


# ---- ticket-specific cases ----------------------------------------------------------------


def _row(plan: SeedPlan, handle: str, k: int = 0) -> dict[str, Any]:
    return plan.owned("item", handle)[k]


def _wire(dt: datetime) -> str:
    """A seed datetime as the template writes it (naive UTC, ISO)."""
    return dt.astimezone(UTC).replace(tzinfo=None).isoformat()


def _t1_cases(plan: SeedPlan) -> list[Case]:
    items = f"{API}/items/"

    def c(what: str, user: str, db: str = "search", **params: Any) -> Case:
        return Case(f"t1.{what}", "GET", items, user=user, params=params, db=db,
                    route="read_items")  # fmt: skip

    return [
        c("alice_other_users_match", ACTOR, q="grocer"),
        c("alice_case_insensitive", ACTOR, q="GROCER"),
        c("bob_own_match_only", OTHER, q="Groceries"),
        c("admin_sees_all_matches", ADMIN, q="grocer"),
        c("alice_count_is_filtered", ACTOR, q="title"),
        c("alice_paged", ACTOR, q="title", skip=1, limit=1),
        c("admin_window", ADMIN, q="title", skip=2, limit=3),
        c("no_match", ACTOR, q="zzz"),
        c("empty_q", ACTOR, q=""),
        c("limit_huge", ACTOR, q="grocer", limit=1_000_000),
    ]


def _t2_cases(plan: SeedPlan) -> list[Case]:
    items = f"{API}/items/"
    dated = sorted(r["created_at"] for r in plan.rows["item"] if r["created_at"] is not None)
    mid = dated[len(dated) // 2]
    plus2 = mid.astimezone(timezone(timedelta(hours=2)))

    def c(what: str, user: str, **params: Any) -> Case:
        return Case(f"t2.{what}", "GET", items, user=user, params=params, route="read_items")

    return [
        c("alice_since_before_all", ACTOR, since="2026-01-01T00:00:00"),
        c("bob_since_before_all", OTHER, since="2026-01-01T00:00:00Z"),
        c("admin_since_before_all", ADMIN, since="2026-01-01T00:00:00"),
        c("admin_since_inclusive", ADMIN, since=_wire(mid)),
        c("admin_since_z", ADMIN, since=_wire(mid) + "Z"),
        c("admin_since_offset", ADMIN, since=plus2.isoformat()),
        c("admin_since_future", ADMIN, since="2030-01-01T00:00:00Z"),
        c("since_malformed", ACTOR, since="yesterday"),
        # date-only: verify's normaliser cannot parse an impossible date echoed back in
        # the 422 body when it is shaped like a full datetime ("2026-02-30T00:00:00")
        c("since_impossible_date", ACTOR, since="2026-02-30"),
        c("alice_oldest", ACTOR, sort="oldest"),
        c("admin_oldest_window", ADMIN, sort="oldest", skip=1, limit=3),
        c("admin_newest_explicit", ADMIN, sort="newest", limit=3),
        c("sort_invalid", ACTOR, sort="random"),
        c("admin_since_oldest_paged", ADMIN, since=_wire(mid), sort="oldest", limit=2),
    ]


def _t3_cases(plan: SeedPlan) -> list[Case]:
    items = f"{API}/items/"
    s_alice, s_bob = extra_id("soft/alice"), extra_id("soft/bob")
    live = _row(plan, ACTOR)["id"]
    tables = ("item",)

    def c(what: str, method: str, path: str, user: str, route: str, **kw: Any) -> Case:
        return Case(f"t3.{what}", method, path, user=user, db="soft", route=route, **kw)

    return [
        c("alice_list", "GET", items, ACTOR, "read_items"),
        c("admin_list", "GET", items, ADMIN, "read_items"),
        c("admin_list_tail", "GET", items, ADMIN, "read_items", params={"skip": 6}),
        c("alice_read_deleted", "GET", f"{items}{s_alice}", ACTOR, "read_item"),
        c("admin_read_deleted", "GET", f"{items}{s_bob}", ADMIN, "read_item"),
        c("alice_update_deleted", "PUT", f"{items}{s_alice}", ACTOR, "update_item",
          json={"title": "Back again"}, tables=tables),
        c("update_cannot_undelete", "PUT", f"{items}{s_alice}", ACTOR, "update_item",
          json={"deleted_at": None}, tables=tables),
        c("alice_delete_deleted", "DELETE", f"{items}{s_alice}", ACTOR, "delete_item",
          tables=tables),
        c("admin_delete_deleted", "DELETE", f"{items}{s_bob}", ADMIN, "delete_item",
          tables=tables),
        c("alice_delete_keeps_row", "DELETE", f"{items}{live}", ACTOR, "delete_item",
          tables=tables),
        c("create_ignores_deleted_at", "POST", items, ACTOR, "create_item",
          json={"title": "New", "deleted_at": "2020-01-01T00:00:00"}, tables=tables),
        c("update_ignores_deleted_at", "PUT", f"{items}{live}", ACTOR, "update_item",
          json={"deleted_at": "2020-01-01T00:00:00"}, tables=tables),
    ]  # fmt: skip


def _t4_cases(plan: SeedPlan) -> list[Case]:
    items = f"{API}/items"
    s_alice, s_bob = extra_id("soft/alice"), extra_id("soft/bob")
    live = _row(plan, ADMIN)["id"]

    def c(what: str, item: uuid.UUID, user: str) -> Case:
        return Case(f"t4.{what}", "POST", f"{items}/{item}/restore", user=user, db="soft",
                    tables=("item",), route="restore_item")  # fmt: skip

    return [
        c("admin_restores", s_alice, ADMIN),
        c("admin_restores_other_users", s_bob, ADMIN),
        c("owner_cannot_restore", s_alice, ACTOR),
        c("other_user_cannot_restore", s_alice, OTHER),
        c("admin_restore_live", live, ADMIN),
    ]


def _t5_cases(plan: SeedPlan) -> list[Case]:
    items = f"{API}/items"
    f = {n.split("/", 1)[1]: extra_id(n) for n, *_ in FRESH_ROWS}
    old_seed = _row(plan, ACTOR)["id"]

    def c(what: str, item: uuid.UUID | str, user: str, body: dict[str, Any]) -> Case:
        return Case(f"t5.{what}", "PUT", f"{items}/{item}", user=user, json=body, db="fresh",
                    tables=("item",), route="update_item")  # fmt: skip

    return [
        c("fresh_title", f["alice-1h"], ACTOR, {"title": "Renamed"}),
        c("almost_a_day", f["alice-23h"], ACTOR, {"title": "Renamed", "description": "d"}),
        c("just_over_a_day", f["alice-25h"], ACTOR, {"title": "Renamed"}),
        c("old_title_and_description", f["alice-25h"], ACTOR,
          {"title": "Renamed", "description": "changed too"}),
        c("old_description_only", f["alice-25h"], ACTOR, {"description": "Updated"}),
        c("admin_old_title", f["alice-25h"], ADMIN, {"title": "Fixed typo"}),
        c("admin_seed_title", old_seed, ADMIN, {"title": "Fixed typo"}),
        c("other_users_fresh_item", f["bob-1h"], ACTOR, {"title": "Mine now"}),
        c("missing_item", MISSING_ID, ACTOR, {"title": "Renamed"}),
    ]  # fmt: skip


def _t6_cases(plan: SeedPlan) -> list[Case]:
    stats = f"{API}/items/stats"

    def c(what: str, user: str, db: str = "seed") -> Case:
        return Case(f"t6.{what}", "GET", stats, user=user, db=db, route="read_item_stats")

    return [
        c("alice", ACTOR),
        c("bob_with_an_undated_item", OTHER),
        c("admin_own_only", ADMIN),
        c("no_items", OTHER, "no_bob_items"),
        c("only_undated_items", ACTOR, "undated"),
        Case(
            "t6.item_route_still_works",
            "GET",
            f"{API}/items/{_row(plan, ACTOR)['id']}",
            user=ACTOR,
            route="read_item_stats",
        ),
    ]


def _t7_cases(plan: SeedPlan) -> list[Case]:
    users = f"{API}/users"
    uid = {h: plan.user(h).id for h in (ACTOR, OTHER, ADMIN, INACTIVE)}
    dave = extra_id("user/dave")

    def c(what: str, action: str, target: uuid.UUID | str, user: str,
          db: str = "seed") -> Case:  # fmt: skip
        return Case(f"t7.{what}", "POST", f"{users}/{target}/{action}", user=user, db=db,
                    tables=("user",), route=f"{action}_user")  # fmt: skip

    return [
        c("admin_deactivates_alice", "deactivate", uid[ACTOR], ADMIN),
        c("admin_activates_carol", "activate", uid[INACTIVE], ADMIN),
        c("deactivate_already_inactive", "deactivate", uid[INACTIVE], ADMIN),
        c("activate_already_active", "activate", uid[OTHER], ADMIN),
        c("admin_deactivates_self", "deactivate", uid[ADMIN], ADMIN),
        c("admin_activates_self", "activate", uid[ADMIN], ADMIN),
        c("admin_deactivates_other_admin", "deactivate", dave, ADMIN, "second_admin"),
        c("alice_deactivates_self", "deactivate", uid[ACTOR], ACTOR),
        c("alice_deactivates_bob", "deactivate", uid[OTHER], ACTOR),
        c("alice_activates_carol", "activate", uid[INACTIVE], ACTOR),
        c("activate_unknown", "activate", MISSING_ID, ADMIN),
    ]


def _t8_cases(plan: SeedPlan) -> list[Case]:
    users = f"{API}/users/"

    def c(what: str, user: str = ADMIN, db: str = "more_users", **params: Any) -> Case:
        return Case(f"t8.{what}", "GET", users, user=user, params=params, db=db,
                    route="read_users")  # fmt: skip

    return [
        c("inactive_only", is_active="false"),
        c("active_only", is_active="true"),
        c("inactive_only_seed", db="seed", is_active="false"),
        c("q_ignores_case", q="ALICE"),
        c("q_mixed_case_email", q="erin.stone"),
        c("q_domain_count", q="example.com"),
        c("q_and_inactive", q="alice", is_active="false"),
        c("q_and_active", q="alice", is_active="true"),
        c("active_paged", is_active="true", skip=1, limit=2),
        c("q_paged", q="example", skip=2, limit=2),
        c("no_match", q="zzz"),
        c("empty_q", q=""),
        c("is_active_malformed", is_active="maybe"),
        c("alice_cannot_filter", user=ACTOR, is_active="false"),
    ]


def _t9_cases(plan: SeedPlan) -> list[Case]:
    items = f"{API}/items/"

    def c(what: str, user: str, db: str, body: Any = None) -> Case:
        return Case(f"t9.{what}", "POST", items, user=user, db=db,
                    json=body if body is not None else {"title": "One more"},
                    tables=("item",), route="create_item")  # fmt: skip

    return [
        c("alice_at_limit", ACTOR, "alice_at_limit"),
        c("alice_at_limit_with_description", ACTOR, "alice_at_limit",
          {"title": "One more", "description": "please"}),
        c("alice_reaches_limit", ACTOR, "alice_below_limit"),
        c("bob_while_alice_is_full", OTHER, "alice_at_limit"),
        c("admin_counts_own_items_only", ADMIN, "alice_at_limit"),
        c("admin_at_limit", ADMIN, "admin_at_limit"),
        c("alice_while_admin_is_full", ACTOR, "admin_at_limit"),
    ]  # fmt: skip


def _t10_cases(plan: SeedPlan) -> list[Case]:
    path = f"{API}/users/item-counts"

    def c(what: str, user: str = ADMIN, db: str = "counts", **params: Any) -> Case:
        return Case(f"t10.{what}", "GET", path, user=user, params=params or None, db=db,
                    route="read_user_item_counts")  # fmt: skip

    return [
        c("admin_counts"),
        c("admin_ties_by_email", db="seed"),
        c("admin_window", skip=2, limit=3),
        c("admin_tail", skip=5),
        c("admin_no_items_anywhere", db="no_items"),
        c("alice_not_admin", user=ACTOR),
        # the new literal route must not take over /users/{user_id} either (as t6 checks
        # for /items/{id}); arm L's emitted app keeps that template route as it is
        Case("t10.user_route_still_works", "GET", f"{API}/users/{plan.user(ACTOR).id}",
             user=ADMIN, route="read_user_item_counts"),
    ]  # fmt: skip


def _t11_cases(plan: SeedPlan) -> list[Case]:
    items = f"{API}/items/"
    own, bobs = _row(plan, ACTOR)["id"], _row(plan, OTHER)["id"]
    tables = ("item",)

    def create(what: str, title: str) -> Case:
        return Case(f"t11.{what}", "POST", items, user=ACTOR, json={"title": title},
                    tables=tables, route="create_item")  # fmt: skip

    def put(what: str, item: uuid.UUID, body: dict[str, Any], user: str = ACTOR) -> Case:
        return Case(f"t11.{what}", "PUT", f"{items}{item}", user=user, json=body,
                    tables=tables, route="update_item")  # fmt: skip

    return [
        create("create_padded", "  Groceries  "),
        create("create_tabs_and_newlines", "\tWeekly report\n"),
        create("create_inner_spaces", "Tax   return 2025"),
        create("create_blank", "   "),
        create("create_blank_mixed", " \t\n "),
        create("create_empty", ""),
        put("update_padded", own, {"title": " Renamed "}),
        put("update_blank", own, {"title": "    ", "description": "changed"}),
        put("update_description_only", own, {"description": "  keep my spaces  "}),
        put("update_title_and_description", own, {"title": "\tBoth ", "description": " d "}),
        put("admin_update_blank_other", bobs, {"title": " "}, user=ADMIN),
        put("admin_update_padded_other", bobs, {"title": " Fixed "}, user=ADMIN),
        put("blank_title_other_users_item", bobs, {"title": " "}),
    ]


def _t12_cases(plan: SeedPlan) -> list[Case]:
    items = f"{API}/items"
    own, bobs = _row(plan, ACTOR)["id"], _row(plan, OTHER)["id"]
    email = {h: plan.user(h).email for h in (ACTOR, OTHER, ADMIN, INACTIVE)}

    def c(what: str, item: uuid.UUID | str, user: str, to: Any) -> Case:
        body = {"new_owner_email": to} if isinstance(to, str) else to
        return Case(f"t12.{what}", "POST", f"{items}/{item}/transfer", user=user, json=body,
                    tables=("item",), route="transfer_item")  # fmt: skip

    return [
        c("alice_to_bob", own, ACTOR, email[OTHER]),
        c("alice_to_admin", own, ACTOR, email[ADMIN]),
        c("admin_moves_bobs_item", bobs, ADMIN, email[ACTOR]),
        c("admin_to_self", bobs, ADMIN, email[ADMIN]),
        c("bob_takes_alices_item", own, OTHER, email[OTHER]),
        c("bob_gives_alices_item_away", own, OTHER, email[ADMIN]),
        c("bob_probes_unknown_email", own, OTHER, "nobody@example.com"),
        c("bob_probes_inactive", own, OTHER, email[INACTIVE]),
        c("alice_to_unknown", own, ACTOR, "nobody@example.com"),
        c("alice_to_inactive", own, ACTOR, email[INACTIVE]),
        c("alice_to_self", own, ACTOR, email[ACTOR]),
        c("missing_item", MISSING_ID, ACTOR, email[OTHER]),
        c("invalid_email", own, ACTOR, "not-an-email"),
        c("no_body_field", own, ACTOR, {}),
    ]


SPECIFIC: dict[str, Callable[[SeedPlan], list[Case]]] = {
    "t1_search": _t1_cases,
    "t2_since": _t2_cases,
    "t3_soft_delete": _t3_cases,
    "t4_restore": _t4_cases,
    "t5_title_window": _t5_cases,
    "t6_stats": _t6_cases,
    "t7_deactivate": _t7_cases,
    "t8_user_filter": _t8_cases,
    "t9_item_quota": _t9_cases,
    "t10_owner_counts": _t10_cases,
    "t11_title_trim": _t11_cases,
    "t12_transfer": _t12_cases,
}

# ---- declared deviations ------------------------------------------------------------------

_UNAUTHORIZED = {"detail": "Not authenticated"}
INVALID_TOKEN = Deviation(
    "invalid-token-401", "policy",
    "The template answers an invalid bearer token with 403 'Could not validate "
    "credentials'; Lattice's auth answers every missing or invalid credential with 401.",
    401, _UNAUTHORIZED, accept_original=True,
)  # fmt: skip
UNKNOWN_USER = Deviation(
    "unknown-user-token", "policy",
    "A valid token naming a user that does not exist: the template answers 404 'User not "
    "found'; Lattice's NativeToken auth answers 401 like any token naming no caller.",
    401, _UNAUTHORIZED, accept_original=True,
)  # fmt: skip


def _page_deviation(name: str, loc: str, msg: str) -> Deviation:
    return Deviation(
        name, "policy",
        "Lattice requires a bounded, non-negative page window (limit: Int(ge=0, le=100), "
        "skip: Int(ge=0)); the template passes any integer to SQL, so it answers 200.",
        422, {"detail": [{"loc": ["query", loc], "msg": msg}]}, accept_original=True,
    )  # fmt: skip


BOUNDED_LIMIT = _page_deviation("bounded-page-size", "limit", "must be <= 100")
NEGATIVE_LIMIT = _page_deviation("non-negative-page-window", "limit", "must be >= 0")
NEGATIVE_SKIP = _page_deviation("non-negative-page-window", "skip", "must be >= 0")


def _null_deviation(field: str) -> Deviation:
    return Deviation(
        "null-into-required-column", "policy",
        "An explicit null for a field the table stores as NOT NULL (ItemUpdate.title is "
        "Optional): the template writes it and crashes with 500 (IntegrityError); Lattice "
        "refuses it with 422 and writes nothing.",
        422, {"detail": [{"loc": ["body", field], "msg": "must not be null"}]},
        accept_original=True,
    )  # fmt: skip


TITLE_LOCKED = Deviation(
    "null-title-on-a-locked-item", "ambiguity",
    "A null title on an item past the 24-hour window: the reference skips the window "
    "check for a null title and crashes like the template (500); refusing it with the "
    "window's 403 first is an equally valid reading of the ticket.",
    403, {"detail": "Title can only be changed within 24 hours of creation"},
    accept_original=True,
)  # fmt: skip

NOT_NULL = {("update_item", "title")}

BLANK_TITLE = Deviation(
    "blank-title-400", "ambiguity",
    "An empty title: the reference keeps the template's validation (min_length=1, a 422 "
    "before the handler runs); answering the ticket's 400 'Title must not be blank' is an "
    "equally valid reading of 'a title that is empty after stripping'.",
    400, {"detail": "Title must not be blank"}, accept_original=True,
)  # fmt: skip
BLANK_BEFORE_OWNER = Deviation(
    "blank-title-before-ownership", "ambiguity",
    "A blank title for another user's item: the reference checks ownership first (403), "
    "as the template orders its checks; validating the title first (400) is an equally "
    "valid reading and reveals nothing about the item.",
    400, {"detail": "Title must not be blank"}, accept_original=True,
)  # fmt: skip
# ticket ambiguities: ticket -> case name -> accepted alternatives
AMBIGUITIES: dict[str, dict[str, tuple[Deviation, ...]]] = {
    "t11_title_trim": {
        "create_item.boundary.title.below_min": (BLANK_TITLE,),
        "update_item.boundary.title.below_min": (BLANK_TITLE,),
        "t11.create_empty": (BLANK_TITLE,),
        "t11.blank_title_other_users_item": (BLANK_BEFORE_OWNER,),
    },
}


def accepted_for(ticket_id: str, cases: Sequence[Case]) -> dict[str, tuple[Deviation, ...]]:
    """The alternative answers accepted per case (besides the reference's own): the
    template proof's policy deviations, by kind of case, plus ticket ambiguities. Each
    is an ``accept_original`` deviation; the first is declared to :func:`verify`, the
    others are tried when the candidate matches neither."""
    out: dict[str, tuple[Deviation, ...]] = {}
    for c in cases:
        what = c.name.removeprefix(f"{c.route}.")
        params = c.params or {}
        nulls = [k for k, v in (c.json or {}).items() if v is None and (c.route, k) in NOT_NULL]
        alts: list[Deviation] = []
        if what == "bad_token":
            alts.append(INVALID_TOKEN)
        elif what == "unknown_user_token":
            alts.append(UNKNOWN_USER)
        elif params.get("limit") == 1_000_000:
            alts.append(BOUNDED_LIMIT)
        elif params.get("limit") == -1:
            alts.append(NEGATIVE_LIMIT)
        elif params.get("skip") == -1:
            alts.append(NEGATIVE_SKIP)
        elif nulls:
            alts.append(_null_deviation(nulls[0]))
            if ticket_id == "t5_title_window" and c.user == ACTOR:
                alts.append(TITLE_LOCKED)
        alts += AMBIGUITIES.get(ticket_id, {}).get(c.name, ())
        if alts:
            out[c.name] = tuple(alts)
    return out


# ---- the oracle ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TicketOracle:
    ticket_id: str
    routes: tuple[RouteSpec, ...]
    suite: GeneratedSuite
    cases: tuple[Case, ...]
    specific: frozenset[str]
    accepted: Mapping[str, tuple[Deviation, ...]]
    branches: Mapping[str, Sequence[Callable[..., Any]]]
    db_factory: Callable[[str, Path], Path]
    login: Login | None
    # the columns holding password hashes (the normaliser's <hash:seed>/<hash:new>)
    hash_columns: tuple[str, ...] = ("hashed_password",)
    # the clock both apps run under (None: the real one); see the module docstring
    clock: Callable[[], AbstractContextManager[Any]] | None = None
    # tried on a mismatch: records equal once canonicalised are "equal_under_rule"
    canon: Callable[[CaseRecord], CaseRecord] | None = None
    canon_name: str = ""

    def reference_factory(self, db_path: Path) -> Any:
        return make_reference_app(self.ticket_id, db_path)

    def frozen_clock(self) -> AbstractContextManager[Any]:
        """The clock the suite runs under (a no-op for the template)."""
        return self.clock() if self.clock is not None else nullcontext()


@cache
def ticket_oracle(ticket_id: str) -> TicketOracle:
    """The acceptance suite of ``ticket_id`` (built once per process)."""
    if ticket(ticket_id).repo != "template":  # KeyError for an unknown ticket
        from bench.edit_slice.repos import repo_ticket_oracle

        return repo_ticket_oracle(ticket_id)
    ref = reference_variant(ticket_id)
    models = ref.module("models")
    routes = ROUTES[ticket_id]

    def engine_factory(path: Path) -> Engine:
        engine = sqlite_engine(path)
        ref.metadata.create_all(engine)  # the reference's schema (e.g. deleted_at)
        return engine

    suite = generate_suite(
        routes,
        [USER, ITEM],
        AUTH,
        insert_hook=sqlmodel_insert_hook({"user": models.User, "item": models.Item},
                                         engine_factory),
    )  # fmt: skip
    writers = _variant_writer(ticket_id, suite.plan)

    def db_factory(variant: str, path: Path) -> Path:
        suite.db_factory("seed", path)
        if variant != "seed":
            engine = sqlite_engine(path)
            try:
                writers[variant](engine)
            finally:
                engine.dispose()
        return path

    specific = SPECIFIC[ticket_id](suite.plan)
    cases = (*suite.cases, *specific)
    deps = ref.module("api.deps")
    branches: dict[str, list[Callable[..., Any]]] = {}
    for r in routes:
        router = "users" if r.path.startswith(f"{API}/users") else "items"
        fs = [getattr(ref.module(f"api.routes.{router}"), r.name), deps.get_current_user]
        if r.auth == "admin":
            fs.append(deps.get_current_active_superuser)
        branches[r.name] = fs
    return TicketOracle(
        ticket_id=ticket_id,
        routes=routes,
        suite=suite,
        cases=cases,
        specific=frozenset(c.name for c in specific),
        accepted=accepted_for(ticket_id, cases),
        branches=branches,
        db_factory=db_factory,
        login=suite.login,
    )


@dataclass(frozen=True)
class CaseVerdict:
    name: str
    verdict: Verdict
    deviation: str | None = None
    reason: str = ""
    diff: str = ""


@dataclass
class Outcome:
    """A candidate's verdict on one ticket, with the per-case details. ``unverified``
    lists raise sites of the reference no case of their route reaches (an oracle gap,
    empty for every ticket); ``error`` is set when the suite could not run."""

    ticket_id: str
    kind: Kind
    cases: dict[str, CaseVerdict] = field(default_factory=dict)
    specific_names: frozenset[str] = frozenset()
    error: str | None = None
    unverified: tuple[str, ...] = ()

    def specific(self) -> dict[str, CaseVerdict]:
        """The ticket-specific (trap) cases."""
        return {n: v for n, v in self.cases.items() if n in self.specific_names}

    def counts(self) -> tuple[int, int]:
        """(generated cases, ticket-specific cases)."""
        k = len(self.specific())
        return len(self.cases) - k, k

    def tally(self) -> Counter[str]:
        return Counter(v.verdict for v in self.cases.values())

    def render(self) -> str:
        lines = [f"{self.ticket_id}: {self.kind} {dict(sorted(self.tally().items()))}"]
        if self.error:
            lines.append(f"error: {self.error}")
        for v in self.cases.values():
            if v.verdict in ("silent", "loud"):
                lines.append(f"{v.verdict.upper()} {v.name}: {v.reason}")
                lines.extend("    " + x for x in v.diff.splitlines())
        lines += [f"unverified branch: {u}" for u in self.unverified]
        return "\n".join(lines)


def _dispose(app: Any) -> None:
    engine = getattr(getattr(app, "state", None), "engine", None)
    if engine is not None:
        engine.dispose()


def _agree(original: CaseRecord, candidate: CaseRecord) -> bool:
    try:
        agreement(original, candidate, RULES)
    except Mismatch:
        return False
    return True


def evaluate(ticket_id: str, candidate_app_factory: AppFactory) -> Outcome:
    """Run ticket ``ticket_id``'s acceptance suite against ``candidate_app_factory``
    (``db_path -> ASGI app``), the reference as the original; see the module docstring
    for the classification."""
    o = ticket_oracle(ticket_id)
    try:
        with o.frozen_clock(), tempfile.TemporaryDirectory(prefix="edit-slice-probe-") as tmp:
            _dispose(candidate_app_factory(o.db_factory("seed", Path(tmp) / "probe.db")))
    except Exception as e:  # noqa: BLE001 -- any import/startup failure is loud
        return Outcome(ticket_id, "loud", specific_names=o.specific,
                       error=f"app failed to start: {type(e).__name__}: {e}")  # fmt: skip
    declared = {name: alts[0] for name, alts in o.accepted.items()}
    try:
        with o.frozen_clock():
            result = verify(
                o.reference_factory,
                candidate_app_factory,
                o.db_factory,
                o.cases,
                RULES,
                declared,
                login=o.login,
                hash_columns=o.hash_columns,
                branches=o.branches,
            )
    except Exception as e:  # noqa: BLE001 -- e.g. the candidate's login broke
        return Outcome(ticket_id, "loud", specific_names=o.specific,
                       error=f"suite aborted: {type(e).__name__}: {e}")  # fmt: skip
    cases: dict[str, CaseVerdict] = {}
    for name, oc in result.outcomes.items():
        if oc.kind != "mismatch":
            cases[name] = CaseVerdict(name, oc.kind, deviation=oc.deviation, reason=oc.reason)
            continue
        original, candidate = result.original[name], result.lattice[name]
        if o.canon is not None and _agree(o.canon(original), o.canon(candidate)):
            cases[name] = CaseVerdict(name, "equal_under_rule", reason=o.canon_name)
            continue
        for dev in o.accepted.get(name, ())[1:]:
            if classify(original, candidate, RULES, dev).kind == "deviation":
                cases[name] = CaseVerdict(name, "deviation", deviation=dev.id, reason=dev.reason)
                break
        else:
            if name in declared:  # the reason and diff of the plain comparison
                oc = classify(original, candidate, RULES, None)
            diff = oc.diff.render() if oc.diff else ""
            verdict: Verdict = "loud" if candidate.status >= 500 else "silent"
            cases[name] = CaseVerdict(name, verdict, reason=oc.reason, diff=diff)
    verdicts = {v.verdict for v in cases.values()}
    kind: Kind = "silent" if "silent" in verdicts else "loud" if "loud" in verdicts else "correct"
    return Outcome(
        ticket_id,
        kind,
        cases,
        o.specific,
        unverified=tuple(f"{r}: {s.render()}" for r, s in result.unverified),
    )


__all__ = [
    "ALL_TICKETS",
    "TICKETS",
    "CaseVerdict",
    "Outcome",
    "TicketOracle",
    "evaluate",
    "ticket_oracle",
]
