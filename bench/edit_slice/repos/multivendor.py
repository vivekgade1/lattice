"""The oracles of option 1's pilot tickets (t31-t38) on the multi-vendor marketplace.

Unlike TaskFlow's and JustNotes' oracles, these are **not** built on the importer's
generated suite: its seed plan has one owned table, and every trap here lives in the
chain vendor -> product -> order line -> order -> buyer. The cases run on the app's own
seed (``bench/import_repos/multivendor/seed.py``, written before the tickets), plus a few
database variants that add the rows one trap needs; the standard auth cases the generated
suite would contribute (no token, a garbage token, an unknown user's token, an inactive
user's token) are written here for every authenticated route.

The seed, after ``build_seed_db``: users 1 admin, 2 vicky (vendor 1), 3 victor (vendor 2),
4 vera (vendor 3, unverified, no products), 5 carol, 6 dave, 7 erin (inactive), 8 frank
(no orders). Products 1 mug (12.00 now, bought at 10.00 in order 1), 2 candle, 3 notebook,
4 poster (inactive, never sold) belong to vicky; 5 hammer, 6 screwdriver, 7 tape (no
stock, never sold), 8 gloves (never sold) to victor. Orders:

====  =====  =====================  =========  =========================================
id    buyer  created_at (naive UTC) status     lines (product x qty @ price_at_purchase)
====  =====  =====================  =========  =========================================
1     carol  2026-01-05 10:00       delivered  mug x2 @10, hammer x1 @25
2     carol  2026-01-31 23:30       delivered  notebook x4 @5
3     dave   2026-02-01 00:00       shipped    hammer x1 @25, screwdriver x2 @18
4     dave   2026-02-14 12:00       cancelled  candle x3 @8.5
5     carol  2026-02-28 23:59:59    confirmed  mug x1 @12, candle x2 @8.5, screwdriver x1
6     erin   2026-03-01 00:00       delivered  notebook x10 @5
7     dave   2026-03-10 09:00       pending    mug x1 @12
8     carol  2026-03-15 16:45       pending    candle x1 @8.5, hammer x2 @25
9     carol  2026-03-20 08:15       cancelled  screwdriver x1 @18
====  =====  =====================  =========  =========================================

Every case is compared against the ticket's reference. Case names are
``<ticket>.<what>``; :data:`MECHANISMS` tags each trap case with the Lattice guarantee
that would catch its violation (the pilot's mechanism gate; ``none`` where no guarantee
applies), so a silent run can be attributed by the cases it failed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from bench.edit_slice.oracle import TicketOracle
from bench.edit_slice.reference import reference_variant
from bench.edit_slice.repos.taskflow import fresh_timestamps
from bench.edit_slice.variant import Variant
from bench.template_app import sqlite_engine
from lattice.importer.suite import GeneratedSuite, RouteSpec, SeedPlan
from lattice.importer.verify import Case, CaseRecord

MISSING = 999
ADMIN, VICKY, VICTOR, VERA, CAROL, DAVE, ERIN, FRANK = range(1, 9)
HANDLES = {
    "admin": ADMIN, "vicky": VICKY, "victor": VICTOR, "vera": VERA,
    "carol": CAROL, "dave": DAVE, "erin": ERIN, "frank": FRANK,
}  # fmt: skip
MUG, CANDLE, NOTEBOOK, POSTER, HAMMER, SCREWDRIVER, TAPE, GLOVES = range(1, 9)
WRITES = ("orders", "order_items", "products")

# ---- auth -------------------------------------------------------------------------------------


def _password(handle: str) -> tuple[str, str]:
    from bench.import_repos.multivendor.seed import SEED_USER_CREDENTIALS

    email = f"{handle}@example.com"
    return email, SEED_USER_CREDENTIALS[email]


def login(client: Any, handle: str) -> str:
    email, password = _password(handle)
    r = client.post("/auth/login", data={"username": email, "password": password})
    if r.status_code != 200:
        raise RuntimeError(f"login of {handle!r} failed: {r.status_code} {r.text}")
    return r.json()["access_token"]


def mint(user_id: int, role: str = "buyer") -> str:
    """A token as upstream's ``create_access_token`` makes it (the seed's inactive user
    cannot log in, so the inactive-caller case needs one minted)."""
    import bench.import_repos.multivendor  # noqa: F401  (SECRET_KEY)
    from bench.import_repos.multivendor.app.core.security import create_access_token

    return create_access_token({"sub": str(user_id), "role": role})


def auth_cases(tid: str, method: str, path: str, route: str) -> list[Case]:
    """The generated suite's standard auth cases, for one authenticated route."""

    def c(what: str, token: str | None) -> Case:
        return Case(f"{tid}.auth_{what}", method, path, token=token, route=route)

    return [
        c("no_token", None),
        c("garbage_token", "not-a-jwt"),
        c("unknown_user", mint(MISSING)),
        c("inactive_user", mint(ERIN)),
    ]


# ---- database variants ------------------------------------------------------------------------


def _sql(*statements: str) -> Callable[[Engine], None]:
    def write(engine: Engine) -> None:
        with engine.begin() as conn:
            for s in statements:
                conn.execute(text(s))

    return write


def _order(oid: int, user: int, created: str, status: str, total: float) -> str:
    return (
        "INSERT INTO orders (id, user_id, status, total_amount, created_at) VALUES "
        f"({oid}, {user}, '{status}', {total}, '{created}.000000')"
    )


def _line(lid: int, oid: int, product: int, qty: int, price: float) -> str:
    return (
        "INSERT INTO order_items (id, order_id, product_id, quantity, price_at_purchase) "
        f"VALUES ({lid}, {oid}, {product}, {qty}, {price})"
    )


WRITERS: dict[str, Callable[[Engine], None]] = {
    # the inactive poster sold (delivered) to dave
    "poster_sold": _sql(_order(10, DAVE, "2026-03-05 12:00:00", "DELIVERED", 40.0),
                        _line(20, 10, POSTER, 2, 20.0)),
    # orders on either side of the new year
    "december": _sql(_order(10, CAROL, "2025-12-31 23:00:00", "DELIVERED", 5.0),
                     _line(20, 10, NOTEBOOK, 1, 5.0),
                     _order(11, DAVE, "2026-01-01 00:00:00", "PENDING", 6.0),
                     _line(21, 11, GLOVES, 1, 6.0)),
    # one hammer left: reordering order 8 (candle x1, hammer x2) must fail and write nothing
    "hammer_low": _sql(f"UPDATE products SET stock_quantity = 1 WHERE id = {HAMMER}"),
    # vera keeps the vendor role but has no vendor profile any more
    "vendorless": _sql("DELETE FROM vendors WHERE id = 3"),
}  # fmt: skip


def db_factory(variant: str, path: Path) -> Path:
    from bench.import_repos.multivendor.seed import build_seed_db

    build_seed_db(path)
    if variant != "seed":
        engine = sqlite_engine(path)
        try:
            WRITERS[variant](engine)
        finally:
            engine.dispose()
    return path


# ---- canonical comparison ---------------------------------------------------------------------


def canon(record: CaseRecord) -> CaseRecord:
    """Fresh timestamps are "a new timestamp" (as for TaskFlow), and a product's optimistic
    lock ``version`` is not part of any ticket's contract (whether restoring stock bumps
    it is the implementer's call)."""
    record = fresh_timestamps(record)
    db = {
        table: [{k: v for k, v in row.items() if not (table == "products" and k == "version")}
                for row in rows]
        for table, rows in record.db.items()
    }  # fmt: skip
    return replace(record, db=db)


# ---- the cases --------------------------------------------------------------------------------


def _case(tid: str, route: str, method: str, path: str, what: str, user: str | None,
          db: str = "seed", **kw: Any) -> Case:  # fmt: skip
    return Case(f"{tid}.{what}", method, path, user=user, db=db, route=route, **kw)


def t31_cases() -> list[Case]:
    tid, route, path = "t31", "read_own_sales", "/vendors/me/sales"

    def c(what: str, user: str, params: dict[str, str] | None = None) -> Case:
        return _case(tid, route, "GET", path, what, user, params=params)

    return [
        c("vicky_all_time", "vicky"),
        c("victor_all_time", "victor"),
        c("vendor_without_sales", "vera"),
        c("buyer_has_no_vendor_profile", "carol"),
        c("february", "vicky", {"start": "2026-02-01", "end": "2026-02-28"}),
        c("end_is_inclusive", "vicky", {"end": "2026-01-31"}),
        c("start_on_a_boundary", "vicky", {"start": "2026-03-01"}),
        c("victor_march_excludes_cancelled", "victor", {"start": "2026-03-01"}),
        c("single_day", "victor", {"start": "2026-02-01", "end": "2026-02-01"}),
        c("empty_period", "vicky", {"start": "2027-01-01"}),
        c("malformed_start", "vicky", {"start": "yesterday"}),
        c("impossible_date", "vicky", {"end": "2026-02-30"}),
        *auth_cases(tid, "GET", path, route),
    ]


def t32_cases() -> list[Case]:
    tid, route, path = "t32", "read_monthly_report", "/orders/report"

    def c(what: str, user: str, month: str | None, db: str = "seed") -> Case:
        params = None if month is None else {"month": month}
        return _case(tid, route, "GET", path, what, user, db, params=params)

    return [
        c("january", "admin", "2026-01"),
        c("february_boundaries", "admin", "2026-02"),
        c("march", "admin", "2026-03"),
        c("empty_month", "admin", "2026-04"),
        c("december_rolls_over", "admin", "2025-12", "december"),
        c("january_after_december", "admin", "2026-01", "december"),
        c("month_13", "admin", "2026-13"),
        c("month_zero", "admin", "2026-00"),
        c("one_digit_month", "admin", "2026-2"),
        c("not_a_month", "admin", "february"),
        c("month_missing", "admin", None),
        c("vendor_forbidden", "vicky", "2026-02"),
        c("buyer_forbidden", "carol", "2026-02"),
        _case(tid, route, "GET", "/orders/3", "order_route_still_works", "admin"),
        *auth_cases(tid, "GET", f"{path}?month=2026-02", route),
    ]


def t33_cases() -> list[Case]:
    tid, route, path = "t33", "list_top_sellers", "/products/top-sellers"

    def c(what: str, params: dict[str, str] | None = None, db: str = "seed") -> Case:
        return _case(tid, route, "GET", path, what, None, db, params=params)

    return [
        c("all"),
        c("tools", {"category": "tools"}),
        c("home", {"category": "home"}),
        c("decor_only_inactive", {"category": "decor"}),
        c("unknown_category", {"category": "garden"}),
        c("inactive_product_that_sold", db="poster_sold"),
        _case(tid, route, "GET", f"/products/{MUG}", "product_route_still_works", None),
    ]


def t34_cases() -> list[Case]:
    tid, route, path = "t34", "read_customer_spend", "/orders/customers"

    def c(what: str, user: str, db: str = "seed") -> Case:
        return _case(tid, route, "GET", path, what, user, db)

    return [
        c("admin", "admin"),
        c("with_another_order", "admin", "poster_sold"),
        c("across_the_new_year", "admin", "december"),
        c("vendor_forbidden", "vicky"),
        c("buyer_forbidden", "carol"),
        _case(tid, route, "GET", "/orders/1", "order_route_still_works", "admin"),
        *auth_cases(tid, "GET", path, route),
    ]


def t35_cases() -> list[Case]:
    tid, route, path = "t35", "read_own_order_lines", "/vendors/me/order-lines"

    def c(what: str, user: str, db: str = "seed") -> Case:
        return _case(tid, route, "GET", path, what, user, db)

    return [
        c("vicky", "vicky"),
        c("victor", "victor"),
        c("vendor_without_sales", "vera"),
        c("buyer_has_no_vendor_profile", "carol"),
        c("inactive_product_line", "vicky", "poster_sold"),
        *auth_cases(tid, "GET", path, route),
    ]


def t36_cases() -> list[Case]:
    tid, route = "t36", "cancel_order"

    def c(what: str, user: str, order: int) -> Case:
        return _case(tid, route, "POST", f"/orders/{order}/cancel", what, user, tables=WRITES)

    return [
        c("own_pending", "carol", 8),
        c("own_confirmed_three_lines", "carol", 5),
        c("own_shipped", "dave", 3),
        c("own_delivered", "carol", 1),
        c("own_already_cancelled", "carol", 9),
        c("another_buyers_pending", "carol", 7),
        c("another_buyers_shipped", "carol", 3),
        c("admin_any_order", "admin", 7),
        c("admin_shipped", "admin", 3),
        c("vendor_someone_elses", "vicky", 7),
        c("unknown_order", "carol", MISSING),
        *auth_cases(tid, "POST", "/orders/8/cancel", route),
    ]


def t37_cases() -> list[Case]:
    tid, route = "t37", "list_product_buyers"

    def c(what: str, user: str, product: int, db: str = "seed") -> Case:
        return _case(tid, route, "GET", f"/products/{product}/buyers", what, user, db)

    return [
        c("mug", "vicky", MUG),
        c("candle_cancelled_excluded", "vicky", CANDLE),
        c("notebook_inactive_buyer", "vicky", NOTEBOOK),
        c("never_sold", "victor", GLOVES),
        c("screwdriver", "victor", SCREWDRIVER),
        c("another_vendors_product", "vicky", HAMMER),
        c("vendor_without_products", "vera", MUG),
        c("unknown_product", "vicky", MISSING),
        c("unknown_product_other_vendor", "victor", MISSING),
        c("vendor_role_without_profile", "vera", MUG, "vendorless"),
        c("admin_forbidden", "admin", MUG),
        c("buyer_forbidden", "carol", MUG),
        *auth_cases(tid, "GET", f"/products/{MUG}/buyers", route),
    ]


def t38_cases() -> list[Case]:
    tid, route = "t38", "reorder"

    def c(what: str, user: str, order: int, db: str = "seed") -> Case:
        return _case(tid, route, "POST", f"/orders/{order}/reorder", what, user, db,
                     tables=WRITES)  # fmt: skip

    return [
        c("price_changed_since", "carol", 1),
        c("single_line", "carol", 2),
        c("three_lines", "carol", 5),
        c("cancelled_order", "carol", 9),
        c("another_buyers_order", "carol", 7),
        c("unknown_order", "carol", MISSING),
        c("not_enough_stock_writes_nothing", "carol", 8, "hammer_low"),
        c("enough_stock_elsewhere", "dave", 7, "hammer_low"),
        c("out_of_stock_product", "dave", 3, "hammer_low"),
        c("vendor_forbidden", "vicky", 1),
        c("admin_forbidden", "admin", 1),
        *auth_cases(tid, "POST", "/orders/1/reorder", route),
    ]


SPECIFIC: dict[str, Callable[[], list[Case]]] = {
    "t31_vendor_sales": t31_cases,
    "t32_monthly_report": t32_cases,
    "t33_top_sellers": t33_cases,
    "t34_customer_spend": t34_cases,
    "t35_vendor_order_lines": t35_cases,
    "t36_cancel_order": t36_cases,
    "t37_product_buyers": t37_cases,
    "t38_reorder": t38_cases,
}

# ticket -> (route spec, [(module, function)] whose raise sites the cases must reach)
ROUTES: dict[str, tuple[RouteSpec, tuple[tuple[str, str], ...]]] = {
    "t31_vendor_sales": (
        RouteSpec("read_own_sales", "GET", "/vendors/me/sales", "other", None),
        (("routers.vendor_router", "read_own_sales"),
         ("services.vendor_service", "vendor_sales"),
         ("services.vendor_service", "get_own_vendor_profile")),
    ),
    "t32_monthly_report": (
        RouteSpec("read_monthly_report", "GET", "/orders/report", "other", None, auth="admin"),
        (("routers.order_router", "read_monthly_report"),
         ("services.order_service", "monthly_report")),
    ),
    "t33_top_sellers": (
        RouteSpec("list_top_sellers", "GET", "/products/top-sellers", "other", None,
                  auth="public"),
        (("routers.product_router", "list_top_sellers"),
         ("services.product_service", "top_sellers")),
    ),
    "t34_customer_spend": (
        RouteSpec("read_customer_spend", "GET", "/orders/customers", "other", None,
                  auth="admin"),
        (("routers.order_router", "read_customer_spend"),
         ("services.order_service", "customer_spend")),
    ),
    "t35_vendor_order_lines": (
        RouteSpec("read_own_order_lines", "GET", "/vendors/me/order-lines", "other", None),
        (("routers.vendor_router", "read_own_order_lines"),
         ("services.vendor_service", "vendor_order_lines"),
         ("services.vendor_service", "get_own_vendor_profile")),
    ),
    "t36_cancel_order": (
        RouteSpec("cancel_order", "POST", "/orders/{order_id}/cancel", "other", "orders",
                  tables=WRITES),
        (("routers.order_router", "cancel_order"),
         ("services.order_service", "cancel_order"),
         ("services.order_service", "get_order"),
         ("services.order_service", "get_own_order")),
    ),
    "t37_product_buyers": (
        RouteSpec("list_product_buyers", "GET", "/products/{product_id}/buyers", "other",
                  None),
        (("routers.product_router", "list_product_buyers"),
         ("services.product_service", "product_buyers")),
    ),
    "t38_reorder": (
        RouteSpec("reorder", "POST", "/orders/{order_id}/reorder", "other", "orders",
                  tables=WRITES),
        (("routers.order_router", "reorder"),
         ("services.order_service", "reorder"),
         ("services.order_service", "get_order"),
         ("services.order_service", "get_own_order")),
    ),
}  # fmt: skip

# ---- the mechanism gate (docs/superpowers/specs/2026-09-29-option1-pilot.md) ------------------
#
# trap case -> the Lattice guarantee that makes its violation impossible or refused:
#   "L420"  join fan-out (a join multiplying the rows an aggregate sees)
#   "L421"  null policy (an aggregate over no rows is null unless coalesced)
#   "L802"  ownership scoping (rows reached through an owner, owner_via, accessible/mine)
#   "one-statement/atomic"  one statement per read, one transaction per write
#   "typed-params"  typed, bounded parameters (dates, patterns) refused before the handler
#   "none"  no Lattice guarantee applies (route order, boundaries, business rules)
# Tagged when the cases were written, before any model run; a case not listed is "none".
MECHANISMS: Mapping[str, str] = {
    "t31.vicky_all_time": "L420",  # order totals include the other vendor's lines
    "t31.victor_all_time": "L420",
    "t31.vendor_without_sales": "L421",
    "t31.malformed_start": "typed-params",
    "t31.impossible_date": "typed-params",
    "t32.empty_month": "L421",
    "t32.month_13": "typed-params",
    "t32.month_zero": "typed-params",
    "t32.one_digit_month": "typed-params",
    "t32.not_a_month": "typed-params",
    "t33.all": "L421",  # never-sold products: an outer join's null sum
    "t33.tools": "L421",
    "t33.home": "L421",
    "t34.admin": "L420",  # joining order lines multiplies order totals
    "t34.with_another_order": "L420",
    "t34.across_the_new_year": "L420",
    "t35.vicky": "L802",
    "t35.victor": "L802",
    "t36.another_buyers_pending": "L802",
    "t36.another_buyers_shipped": "L802",
    "t36.vendor_someone_elses": "L802",
    "t37.another_vendors_product": "L802",
    "t37.vendor_without_products": "L802",
    "t38.another_buyers_order": "L802",
    "t38.not_enough_stock_writes_nothing": "one-statement/atomic",
}
CONTROL_TICKETS = frozenset({"t35_vendor_order_lines"})


def mechanism(case_name: str) -> str:
    return MECHANISMS.get(case_name, "none")


# ---- the oracle -------------------------------------------------------------------------------

_EMPTY_PLAN = SeedPlan(users=())


def _branches(ticket_id: str) -> Callable[[Variant], dict[str, list[Callable[..., Any]]]]:
    spec, functions = ROUTES[ticket_id]

    def of(ref: Variant) -> dict[str, list[Callable[..., Any]]]:
        return {spec.name: [getattr(ref.module(m), f) for m, f in functions]}

    return of


def multivendor_ticket_oracle(ticket_id: str) -> TicketOracle:
    from bench.edit_slice.apps import repo

    spec, _ = ROUTES[ticket_id]
    cases = tuple(SPECIFIC[ticket_id]())
    suite = GeneratedSuite(cases=[], plan=_EMPTY_PLAN, credentials={},
                           db_factory=db_factory, login=login)  # fmt: skip
    return TicketOracle(
        ticket_id=ticket_id,
        routes=(spec,),
        suite=suite,
        cases=cases,
        specific=frozenset(c.name for c in cases),
        accepted={},
        branches=_branches(ticket_id)(reference_variant(ticket_id)),
        db_factory=db_factory,
        login=login,
        hash_columns=repo("multivendor").hash_columns,
        canon=canon,
        canon_name="FRESH_TIMESTAMPS_NO_VERSION",
    )


__all__ = [
    "CONTROL_TICKETS",
    "MECHANISMS",
    "SPECIFIC",
    "WRITERS",
    "canon",
    "db_factory",
    "login",
    "mechanism",
    "multivendor_ticket_oracle",
]
