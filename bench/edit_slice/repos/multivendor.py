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
    # option 1b: nothing is being fulfilled any more (orders 3 and 5 delivered)
    "fulfilled": _sql("UPDATE orders SET status = 'DELIVERED' WHERE id IN (3, 5)"),
    # option 1b: two more pending orders of carol's; order 10 bought the mug at 10.00
    "multi_pending": _sql(_order(10, CAROL, "2026-03-25 10:00:00", "PENDING", 43.0),
                          _line(20, 10, MUG, 2, 10.0),
                          _line(21, 10, NOTEBOOK, 1, 5.0),
                          _line(22, 10, GLOVES, 3, 6.0),
                          _order(11, CAROL, "2026-03-26 11:00:00", "PENDING", 6.0),
                          _line(23, 11, GLOVES, 1, 6.0)),
    # pivot (t45-t56): dave's order 3 (victor's products only) is confirmed, not shipped
    "victor_confirmed": _sql("UPDATE orders SET status = 'CONFIRMED' WHERE id = 3"),
    # pivot: vera lists a product that never sold
    "vera_stocked": _sql(
        "INSERT INTO products (id, vendor_id, name, description, category, price, "
        "stock_quantity, version, is_active) VALUES "
        "(9, 3, 'Brass lamp', NULL, 'decor', 30.0, 2, 0, 1)"),
    # pivot: a pending order of carol's whose first line is victor's
    "victor_first": _sql(_order(10, CAROL, "2026-03-25 10:00:00", "PENDING", 43.0),
                         _line(20, 10, GLOVES, 1, 6.0),
                         _line(21, 10, MUG, 1, 12.0),
                         _line(22, 10, HAMMER, 1, 25.0)),
    # pivot: frank's only order was cancelled
    "frank_cancelled": _sql(_order(10, FRANK, "2026-03-30 10:00:00", "CANCELLED", 12.0),
                            _line(20, 10, GLOVES, 2, 6.0)),
    # pivot: vera's shop (no products) is verified
    "vera_verified": _sql("UPDATE vendors SET is_verified = 1 WHERE id = 3"),
    # pivot: victor delisted every product he sells
    "victor_delisted": _sql("UPDATE products SET is_active = 0 WHERE vendor_id = 2"),
    # pivot: no shop is verified
    "none_verified": _sql("UPDATE vendors SET is_verified = 0"),
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


# Corrected after the pilot (2026-09-29, results log "Option 1 pilot"), as t14/t17 were
# after round 3: cases whose error *body* the ticket never states are compared on status
# and database effects only (t32 says only "an invalid month is a 422"; t36 never gives
# the message for an order that can no longer be cancelled) ...
# ... and after the Phase 1 oracle review (2026-10-05, results log "Phase 1"): t36 and t38
# state no message for a caller who may not act or for an unknown order either.
STATUS_ONLY: frozenset[str] = frozenset({
    "t32.month_13", "t32.month_zero", "t32.one_digit_month", "t32.not_a_month",
    "t32.month_missing",
    "t36.own_shipped", "t36.own_delivered", "t36.own_already_cancelled",
    "t36.another_buyers_pending", "t36.another_buyers_shipped", "t36.vendor_someone_elses",
    "t36.unknown_order",
    "t38.another_buyers_order", "t38.unknown_order", "t38.vendor_forbidden",
    "t38.admin_forbidden",
})  # fmt: skip
# ... and a case the ticket text does not determine is dropped: "admins can cancel any
# order" also reads as "whatever its status". Corrected after the atomicity pilot (results
# log "Option 1b"): an empty `item_ids` (t42) and a single order id (t44, "at least two")
# have no stated status or answer either; after the main experiment (results log "Option 1
# main"): an empty ``products`` list (t43) is neither allowed nor refused by the text.
EXCLUDED: frozenset[str] = frozenset(
    {"t36.admin_shipped", "t42.empty_list", "t44.one_order", "t43.no_products"}
)

# The pivot's held-out set (t45-t56): written with the tickets, before any model run.
# A 422 whose body the ticket does not give is compared on its status (and database).
STATUS_ONLY = STATUS_ONLY | frozenset({
    "t45.zero", "t45.negative", "t45.over_max",
    "t47.empty_items", "t47.zero_quantity",
    "t51.threshold_zero", "t51.threshold_negative", "t51.threshold_over_max",
    "t51.threshold_not_a_number",
    "t53.zero_quantity", "t53.negative_quantity",
})  # fmt: skip
# ... and the cases the text leaves open are dropped:
# * t46: a vendor without products in an order that is not confirmed fails both stated
#   checks, and the text does not order them; a vendor-role user with no vendor profile
#   has no products (the 403) or no profile (GET /vendors/me's 404), not stated;
# * t50: an empty `order_ids` is neither allowed nor refused by the text.
EXCLUDED = EXCLUDED | frozenset(
    {"t46.vendor_not_in_pending_order", "t46.vendor_role_without_profile", "t50.empty_list"}
)

# product answers (ProductRead) whose optimistic-lock `version` the ticket leaves open
_PRODUCT_BODIES = ("t45.", "t53.")


def _without_version(body: Any) -> Any:
    if isinstance(body, dict):
        return {k: _without_version(v) for k, v in body.items()
                if not (k == "version" and "stock_quantity" in body)}  # fmt: skip
    if isinstance(body, list):
        return [_without_version(v) for v in body]
    return body


def canon(record: CaseRecord) -> CaseRecord:
    """Fresh timestamps are "a new timestamp" (as for TaskFlow), and a product's optimistic
    lock ``version`` is not part of any ticket's contract (whether restoring stock bumps
    it is the implementer's call; for the pivot's product answers, in the body too); a
    :data:`STATUS_ONLY` case's error body is not compared."""
    record = fresh_timestamps(record)
    if record.name in STATUS_ONLY and record.status >= 400:
        record = replace(record, body="<error body not stated by the ticket>")
    if record.name.startswith(_PRODUCT_BODIES):
        record = replace(record, body=_without_version(record.body))
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


# ---- option 1b: atomicity (t39-t44) ------------------------------------------------------------
# seed order lines: order 5 has lines 7-9, order 7 line 11, order 8 lines 12 (candle x1)
# and 13 (hammer x2); "multi_pending" adds order 10 (lines 20-22) and order 11 (line 23)


def t39_cases() -> list[Case]:
    tid, route, path = "t39", "bulk_update_prices", "/products/bulk-price"

    def c(what: str, user: str, changes: list[tuple[int, float]], db: str = "seed") -> Case:
        body = {"changes": [{"product_id": pid, "price": price} for pid, price in changes]}
        return _case(tid, route, "POST", path, what, user, db, json=body, tables=("products",))

    return [
        c("two_own", "vicky", [(MUG, 15.0), (CANDLE, 9.0)]),
        c("victor_own", "victor", [(HAMMER, 30.0), (GLOVES, 7.0)]),
        c("repeated_id", "vicky", [(MUG, 15.0), (MUG, 15.0)]),
        c("inactive_product", "vicky", [(POSTER, 25.0)]),
        c("own_then_foreign", "vicky", [(MUG, 15.0), (HAMMER, 1.0)]),
        c("own_then_missing", "vicky", [(MUG, 15.0), (MISSING, 1.0)]),
        c("foreign_first", "vicky", [(HAMMER, 1.0), (MUG, 15.0)]),
        c("zero_price_anywhere", "vicky", [(MUG, 15.0), (CANDLE, 0.0)]),
        c("vendor_without_products", "vera", [(MUG, 1.0)]),
        c("vendor_role_without_profile", "vera", [(MUG, 1.0)], "vendorless"),
        c("admin_forbidden", "admin", [(MUG, 1.0)]),
        c("buyer_forbidden", "carol", [(MUG, 1.0)]),
        *auth_cases(tid, "POST", path, route),
    ]


def _items(*lines: tuple[int, int]) -> dict[str, Any]:
    return {"items": [{"product_id": pid, "quantity": qty} for pid, qty in lines]}


def t40_cases() -> list[Case]:
    tid, route = "t40", "edit_order_items"

    def c(what: str, user: str, order: int, body: dict[str, Any], db: str = "seed") -> Case:
        return _case(tid, route, "PATCH", f"/orders/{order}/items", what, user, db,
                     json=body, tables=WRITES)  # fmt: skip

    return [
        c("replace_lines", "carol", 8, _items((CANDLE, 3))),
        c("new_product_at_current_price", "carol", 8, _items((MUG, 1), (HAMMER, 2))),
        c("held_stock_counts", "carol", 8, _items((CANDLE, 1), (HAMMER, 3)), "hammer_low"),
        c("not_enough_writes_nothing", "carol", 8, _items((MUG, 5), (HAMMER, 4)), "hammer_low"),
        c("repriced_line", "carol", 10, _items((MUG, 2), (NOTEBOOK, 1)), "multi_pending"),
        c("another_buyers_pending", "carol", 7, _items((MUG, 1))),
        c("another_buyers_shipped", "carol", 3, _items((MUG, 1))),
        c("own_confirmed", "carol", 5, _items((MUG, 1))),
        c("dave_own_shipped", "dave", 3, _items((MUG, 1))),
        c("unknown_order", "carol", MISSING, _items((MUG, 1))),
        c("admin_is_not_the_buyer", "admin", 8, _items((MUG, 1))),
        *auth_cases(tid, "PATCH", "/orders/8/items", route),
    ]


def t41_cases() -> list[Case]:
    tid, route, path = "t41", "close_own_shop", "/vendors/me/close"

    def c(what: str, user: str, db: str = "seed") -> Case:
        return _case(tid, route, "POST", path, what, user, db, tables=WRITES)

    return [
        c("vicky_busy_writes_nothing", "vicky"),
        c("victor_busy_writes_nothing", "victor"),
        c("vicky_closes", "vicky", "fulfilled"),
        c("victor_closes_multi_vendor_order", "victor", "fulfilled"),
        c("vendor_without_products", "vera"),
        c("buyer_has_no_vendor_profile", "carol"),
        c("admin_has_no_vendor_profile", "admin"),
        *auth_cases(tid, "POST", path, route),
    ]


def t42_cases() -> list[Case]:
    tid, route = "t42", "split_order"

    def c(what: str, user: str, order: int, ids: list[int], db: str = "seed") -> Case:
        return _case(tid, route, "POST", f"/orders/{order}/split", what, user, db,
                     json={"item_ids": ids}, tables=WRITES)  # fmt: skip

    return [
        c("one_line", "carol", 8, [13]),
        c("totals_at_purchase_prices", "carol", 10, [20, 22], "multi_pending"),
        c("every_item", "carol", 8, [12, 13]),
        c("later_item_missing_writes_nothing", "carol", 8, [13, MISSING]),
        c("item_of_another_order", "carol", 8, [11]),
        c("another_buyers_order", "carol", 7, [11]),
        c("own_confirmed", "carol", 5, [7]),
        c("unknown_order", "carol", MISSING, [1]),
        c("empty_list", "carol", 8, []),
        *auth_cases(tid, "POST", "/orders/8/split", route),
    ]


def _product(name: str, price: float = 10.0) -> dict[str, Any]:
    return {"name": name, "description": None, "category": "home", "price": price,
            "stock_quantity": 5}  # fmt: skip


def t43_cases() -> list[Case]:
    tid, route, path = "t43", "onboard_vendor", "/vendors/onboard"
    tables = ("users", "vendors", "products")

    def c(what: str, user: str, products: list[dict[str, Any]]) -> Case:
        body = {"business_name": "Fresh Shop", "products": products}
        return _case(tid, route, "POST", path, what, user, json=body, tables=tables)

    return [
        c("two_products", "frank", [_product("Lamp"), _product("Rug", 40.0)]),
        c("no_products", "carol", []),
        c("duplicate_name_writes_nothing", "frank",
          [_product("Lamp"), _product("Rug"), _product("Lamp")]),
        c("names_differ_by_case", "frank", [_product("Lamp"), _product("lamp")]),
        c("existing_vendor", "vicky", [_product("Lamp")]),
        c("zero_price", "frank", [_product("Lamp", 0.0)]),
        *auth_cases(tid, "POST", path, route),
    ]  # fmt: skip


def t44_cases() -> list[Case]:
    tid, route, path = "t44", "merge_orders", "/orders/merge"

    def c(what: str, user: str, ids: list[int], db: str = "multi_pending") -> Case:
        return _case(tid, route, "POST", path, what, user, db, json={"order_ids": ids},
                     tables=WRITES)  # fmt: skip

    return [
        c("two_orders", "carol", [8, 10]),
        c("three_orders", "carol", [10, 11, 8]),
        c("later_foreign_writes_nothing", "carol", [10, 8, 7]),
        c("later_unknown_writes_nothing", "carol", [8, 10, MISSING]),
        c("later_not_pending_writes_nothing", "carol", [8, 10, 5]),
        c("first_foreign", "carol", [7, 8]),
        c("dave_on_carols", "dave", [7, 8]),
        c("one_order", "carol", [8]),
        *auth_cases(tid, "POST", path, route),
    ]


# ---- the pivot's held-out set (t45-t56) ----------------------------------------------------------
# "victor_confirmed": order 3 (dave; hammer x1, screwdriver x2, victor's) is confirmed;
# "victor_first": carol's pending order 10, lines 20 gloves x1 @6, 21 mug x1 @12, 22 hammer
# x1 @25; "frank_cancelled": frank's order 10, cancelled; "vera_stocked": vera's product
# 9, never sold; "vera_verified": vera's shop (no products) is verified.


def t45_cases() -> list[Case]:
    tid, route = "t45", "restock_product"

    def c(what: str, user: str, product: int, quantity: Any, db: str = "seed") -> Case:
        return _case(tid, route, "POST", f"/products/{product}/restock", what, user, db,
                     json={"quantity": quantity}, tables=("products",))  # fmt: skip

    return [
        c("own_ten", "vicky", MUG, 10),
        c("one_unit", "victor", HAMMER, 1),
        c("max_1000", "victor", TAPE, 1000),
        c("inactive_own", "vicky", POSTER, 5),
        c("zero", "vicky", MUG, 0),
        c("negative", "vicky", MUG, -5),
        c("over_max", "vicky", MUG, 1001),
        c("another_vendors_product", "vicky", HAMMER, 5),
        c("unknown_product", "vicky", MISSING, 5),
        c("vendor_without_products", "vera", MUG, 5),
        c("vendor_role_without_profile", "vera", MUG, 5, "vendorless"),
        c("admin_forbidden", "admin", MUG, 5),
        c("buyer_forbidden", "carol", MUG, 5),
        *auth_cases(tid, "POST", f"/products/{MUG}/restock", route),
    ]


def t46_cases() -> list[Case]:
    tid, route = "t46", "ship_order"

    def c(what: str, user: str, order: int, db: str = "seed") -> Case:
        return _case(tid, route, "POST", f"/orders/{order}/ship", what, user, db,
                     tables=("orders",))  # fmt: skip

    return [
        c("vicky_ships_confirmed", "vicky", 5),
        c("victor_ships_confirmed", "victor", 5),
        c("admin_ships_confirmed", "admin", 5),
        c("victor_ships_his_order", "victor", 3, "victor_confirmed"),
        c("vendor_not_in_order", "vicky", 3, "victor_confirmed"),
        c("vendor_without_products", "vera", 5),
        c("own_pending", "vicky", 7),
        c("admin_pending", "admin", 8),
        c("already_shipped", "victor", 3),
        c("delivered", "vicky", 1),
        c("cancelled", "victor", 9),
        c("admin_cancelled", "admin", 4),
        c("unknown_order", "vicky", MISSING),
        c("buyer_forbidden", "carol", 5),
        c("vendor_not_in_pending_order", "vera", 7),
        c("vendor_role_without_profile", "vera", 5, "vendorless"),
        *auth_cases(tid, "POST", "/orders/5/ship", route),
    ]


def t47_cases() -> list[Case]:
    tid, route, path = "t47", "quote_order", "/orders/quote"

    def c(what: str, user: str, *lines: tuple[int, int], body: Any = None) -> Case:
        return _case(tid, route, "POST", path, what, user,
                     json=_items(*lines) if body is None else body, tables=WRITES)  # fmt: skip

    return [
        c("basket", "carol", (MUG, 2), (HAMMER, 1)),
        c("repeated_product", "carol", (CANDLE, 2), (MUG, 1), (CANDLE, 3)),
        c("repeated_beyond_stock", "carol", (HAMMER, 10), (HAMMER, 6)),
        c("all_the_stock", "dave", (HAMMER, 15)),
        c("out_of_stock", "carol", (TAPE, 1)),
        c("inactive_product", "carol", (POSTER, 1)),
        c("unknown_product", "carol", (MUG, 1), (MISSING, 1)),
        c("vendor_quotes", "vicky", (GLOVES, 3)),
        c("admin_quotes", "admin", (NOTEBOOK, 4), (SCREWDRIVER, 1)),
        c("empty_items", "carol", body={"items": []}),
        c("zero_quantity", "carol", (MUG, 0)),
        *auth_cases(tid, "POST", path, route),
    ]


def t48_cases() -> list[Case]:
    tid, route, path = "t48", "read_own_dashboard", "/vendors/me/dashboard"

    def c(what: str, user: str, db: str = "seed") -> Case:
        return _case(tid, route, "GET", path, what, user, db)

    return [
        c("vicky", "vicky"),
        c("victor", "victor"),
        c("vendor_without_products", "vera"),
        c("products_never_sold", "vera", "vera_stocked"),
        c("inactive_product_sold", "vicky", "poster_sold"),
        c("buyer_has_no_vendor_profile", "carol"),
        c("admin_has_no_vendor_profile", "admin"),
        *auth_cases(tid, "GET", path, route),
    ]


def t49_cases() -> list[Case]:
    tid, route = "t49", "remove_order_item"

    def c(what: str, user: str, order: int, item: int, db: str = "seed") -> Case:
        return _case(tid, route, "DELETE", f"/orders/{order}/items/{item}", what, user, db,
                     tables=WRITES)  # fmt: skip

    return [
        c("remove_a_line", "carol", 8, 13),
        c("total_at_prices_paid", "carol", 10, 22, "multi_pending"),
        c("first_of_three", "carol", 10, 20, "multi_pending"),
        c("only_line", "carol", 11, 23, "multi_pending"),
        c("dave_only_line", "dave", 7, 11),
        c("item_of_another_order", "carol", 8, 11),
        c("unknown_item", "carol", 8, MISSING),
        c("another_buyers_pending", "carol", 7, 11),
        c("another_buyers_shipped", "carol", 3, 4),
        c("own_confirmed", "carol", 5, 7),
        c("own_delivered", "carol", 1, 1),
        c("unknown_order", "carol", MISSING, 1),
        c("admin_is_not_the_buyer", "admin", 8, 13),
        c("vendor_is_not_the_buyer", "vicky", 8, 12),
        *auth_cases(tid, "DELETE", "/orders/8/items/13", route),
    ]


def t50_cases() -> list[Case]:
    tid, route, path = "t50", "cancel_batch", "/orders/cancel-batch"

    def c(what: str, user: str, ids: list[int], db: str = "seed") -> Case:
        return _case(tid, route, "POST", path, what, user, db, json={"order_ids": ids},
                     tables=WRITES)  # fmt: skip

    return [
        c("two_pending", "admin", [7, 8]),
        c("confirmed_multi_vendor", "admin", [5]),
        c("repeated_id", "admin", [7, 7]),
        c("later_unknown_writes_nothing", "admin", [7, MISSING]),
        c("later_shipped_writes_nothing", "admin", [8, 3]),
        c("already_cancelled", "admin", [4]),
        c("delivered", "admin", [1]),
        c("buyer_forbidden", "carol", [8]),
        c("vendor_forbidden", "vicky", [8]),
        c("empty_list", "admin", []),
        *auth_cases(tid, "POST", path, route),
    ]


def t51_cases() -> list[Case]:
    tid, route, path = "t51", "read_own_low_stock", "/vendors/me/low-stock"

    def c(what: str, user: str, threshold: Any = None, db: str = "seed") -> Case:
        params = None if threshold is None else {"threshold": str(threshold)}
        return _case(tid, route, "GET", path, what, user, db, params=params)

    return [
        c("victor_default", "victor"),
        c("vicky_default_is_empty", "vicky"),
        c("victor_at_threshold", "victor", 15),
        c("victor_twenty", "victor", 20),
        c("victor_51", "victor", 51),
        c("vicky_41", "vicky", 41),
        c("vicky_1000", "vicky", 1000),
        c("vendor_without_products", "vera", 1000),
        c("threshold_zero", "vicky", 0),
        c("threshold_negative", "vicky", -1),
        c("threshold_over_max", "vicky", 1001),
        c("threshold_not_a_number", "vicky", "few"),
        c("buyer_has_no_vendor_profile", "carol"),
        *auth_cases(tid, "GET", path, route),
    ]


def t52_cases() -> list[Case]:
    tid, route = "t52", "read_order_by_vendor"

    def c(what: str, user: str, order: int, db: str = "seed") -> Case:
        return _case(tid, route, "GET", f"/orders/{order}/by-vendor", what, user, db)

    return [
        c("price_changed_since", "carol", 1),
        c("three_lines", "carol", 5),
        c("cancelled_order", "carol", 9),
        c("victor_first", "carol", 10, "victor_first"),
        c("admin_any_order", "admin", 6),
        c("another_buyers_order", "carol", 3),
        c("vendor_with_a_line", "vicky", 1),
        c("unknown_order", "carol", MISSING),
        *auth_cases(tid, "GET", "/orders/1/by-vendor", route),
    ]


def t53_cases() -> list[Case]:
    tid, route = "t53", "move_stock"

    def c(what: str, user: str, src: int, dst: int, qty: Any, db: str = "seed") -> Case:
        return _case(tid, route, "POST", f"/products/{src}/move-stock", what, user, db,
                     json={"to_product_id": dst, "quantity": qty},
                     tables=("products",))  # fmt: skip

    return [
        c("move_five", "vicky", MUG, CANDLE, 5),
        c("move_all", "victor", HAMMER, GLOVES, 15),
        c("into_inactive_own", "vicky", MUG, POSTER, 3),
        c("too_many", "victor", HAMMER, GLOVES, 16),
        c("from_empty", "victor", TAPE, GLOVES, 1),
        c("target_foreign_writes_nothing", "vicky", MUG, HAMMER, 5),
        c("target_unknown_writes_nothing", "vicky", MUG, MISSING, 5),
        c("source_foreign", "vicky", HAMMER, MUG, 5),
        c("source_unknown", "vicky", MISSING, MUG, 5),
        c("same_product", "vicky", MUG, MUG, 5),
        c("zero_quantity", "vicky", MUG, CANDLE, 0),
        c("negative_quantity", "vicky", MUG, CANDLE, -2),
        c("vendor_without_products", "vera", MUG, CANDLE, 1),
        c("vendor_role_without_profile", "vera", MUG, CANDLE, 1, "vendorless"),
        c("admin_forbidden", "admin", MUG, CANDLE, 1),
        c("buyer_forbidden", "carol", MUG, CANDLE, 1),
        *auth_cases(tid, "POST", f"/products/{MUG}/move-stock", route),
    ]


def t54_cases() -> list[Case]:
    tid, route, path = "t54", "read_own_order_stats", "/users/me/order-stats"

    def c(what: str, user: str, db: str = "seed") -> Case:
        return _case(tid, route, "GET", path, what, user, db)

    return [
        c("carol", "carol"),
        c("dave", "dave"),
        c("no_orders", "frank"),
        c("only_a_cancelled_order", "frank", "frank_cancelled"),
        c("vendor_placed_none", "vicky"),
        c("admin_placed_none", "admin"),
        *auth_cases(tid, "GET", path, route),
    ]


def t55_cases() -> list[Case]:
    tid, route, path = "t55", "list_vendors", "/vendors"

    def c(what: str, db: str = "seed", user: str | None = None) -> Case:
        return _case(tid, route, "GET", path, what, user, db)

    return [
        c("seed"),
        c("verified_shop_without_products", "vera_verified"),
        c("shop_with_only_delisted_products", "victor_delisted"),
        c("no_verified_shop", "none_verified"),
        c("inactive_product_sold", "poster_sold"),
        c("signed_in_buyer", user="carol"),
        _case(tid, route, "GET", "/vendors/me", "own_profile_still_works", "vicky"),
    ]


def t56_cases() -> list[Case]:
    tid, route = "t56", "mark_order_received"

    def c(what: str, user: str, order: int) -> Case:
        return _case(tid, route, "POST", f"/orders/{order}/received", what, user,
                     tables=("orders",))  # fmt: skip

    return [
        c("own_shipped", "dave", 3),
        c("own_pending", "carol", 8),
        c("own_confirmed", "carol", 5),
        c("own_delivered", "carol", 1),
        c("own_cancelled", "carol", 9),
        c("another_buyers_shipped", "carol", 3),
        c("another_buyers_pending", "carol", 7),
        c("admin_is_not_the_buyer", "admin", 3),
        c("vendor_of_the_order", "victor", 3),
        c("unknown_order", "dave", MISSING),
        *auth_cases(tid, "POST", "/orders/3/received", route),
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
    "t39_bulk_price": t39_cases,
    "t40_edit_order_items": t40_cases,
    "t41_close_shop": t41_cases,
    "t42_split_order": t42_cases,
    "t43_vendor_onboarding": t43_cases,
    "t44_merge_orders": t44_cases,
    "t45_restock": t45_cases,
    "t46_ship_order": t46_cases,
    "t47_quote": t47_cases,
    "t48_vendor_dashboard": t48_cases,
    "t49_remove_order_item": t49_cases,
    "t50_cancel_batch": t50_cases,
    "t51_low_stock": t51_cases,
    "t52_order_by_vendor": t52_cases,
    "t53_move_stock": t53_cases,
    "t54_order_stats": t54_cases,
    "t55_vendor_directory": t55_cases,
    "t56_order_received": t56_cases,
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
    "t39_bulk_price": (
        RouteSpec("bulk_update_prices", "POST", "/products/bulk-price", "other", "products"),
        (("routers.product_router", "bulk_update_prices"),
         ("services.product_service", "bulk_update_prices")),
    ),
    "t40_edit_order_items": (
        RouteSpec("edit_order_items", "PATCH", "/orders/{order_id}/items", "other", "orders",
                  tables=WRITES),
        (("routers.order_router", "edit_order_items"),
         ("services.order_service", "edit_order_items"),
         ("services.order_service", "get_order"),
         ("services.order_service", "get_own_order")),
    ),
    "t41_close_shop": (
        RouteSpec("close_own_shop", "POST", "/vendors/me/close", "other", "products",
                  tables=WRITES),
        (("routers.vendor_router", "close_own_shop"),
         ("services.vendor_service", "close_shop"),
         ("services.vendor_service", "get_own_vendor_profile")),
    ),
    "t42_split_order": (
        RouteSpec("split_order", "POST", "/orders/{order_id}/split", "other", "orders",
                  tables=WRITES),
        (("routers.order_router", "split_order"),
         ("services.order_service", "split_order"),
         ("services.order_service", "get_order"),
         ("services.order_service", "get_own_order")),
    ),
    "t43_vendor_onboarding": (
        RouteSpec("onboard_vendor", "POST", "/vendors/onboard", "other", "vendors",
                  tables=("users", "vendors", "products")),
        (("routers.vendor_router", "onboard_vendor"),
         ("services.vendor_service", "onboard_vendor")),
    ),
    "t44_merge_orders": (
        RouteSpec("merge_orders", "POST", "/orders/merge", "other", "orders", tables=WRITES),
        (("routers.order_router", "merge_orders"),
         ("services.order_service", "merge_orders"),
         ("services.order_service", "get_order"),
         ("services.order_service", "get_own_order")),
    ),
    # ---- the pivot's held-out set
    "t45_restock": (
        RouteSpec("restock_product", "POST", "/products/{product_id}/restock", "other",
                  "products"),
        (("routers.product_router", "restock_product"),
         ("services.product_service", "restock_own_product")),
    ),
    "t46_ship_order": (
        RouteSpec("ship_order", "POST", "/orders/{order_id}/ship", "other", "orders"),
        (("routers.order_router", "ship_order"),
         ("services.order_service", "ship_order"),
         ("services.order_service", "get_order")),
    ),
    "t47_quote": (
        RouteSpec("quote_order", "POST", "/orders/quote", "other", None, tables=WRITES),
        (("routers.order_router", "quote_order"),
         ("services.order_service", "quote")),
    ),
    "t48_vendor_dashboard": (
        RouteSpec("read_own_dashboard", "GET", "/vendors/me/dashboard", "other", None),
        (("routers.vendor_router", "read_own_dashboard"),
         ("services.vendor_service", "vendor_dashboard"),
         ("services.vendor_service", "get_own_vendor_profile")),
    ),
    "t49_remove_order_item": (
        RouteSpec("remove_order_item", "DELETE", "/orders/{order_id}/items/{item_id}",
                  "other", "orders", tables=WRITES),
        (("routers.order_router", "remove_order_item"),
         ("services.order_service", "remove_order_item"),
         ("services.order_service", "get_order"),
         ("services.order_service", "get_own_order")),
    ),
    "t50_cancel_batch": (
        RouteSpec("cancel_batch", "POST", "/orders/cancel-batch", "other", "orders",
                  auth="admin", tables=WRITES),
        (("routers.order_router", "cancel_batch"),
         ("services.order_service", "cancel_batch")),
    ),
    "t51_low_stock": (
        RouteSpec("read_own_low_stock", "GET", "/vendors/me/low-stock", "other", None),
        (("routers.vendor_router", "read_own_low_stock"),
         ("services.vendor_service", "low_stock"),
         ("services.vendor_service", "get_own_vendor_profile")),
    ),
    "t52_order_by_vendor": (
        RouteSpec("read_order_by_vendor", "GET", "/orders/{order_id}/by-vendor", "other",
                  None),
        (("routers.order_router", "read_order_by_vendor"),
         ("services.order_service", "order_by_vendor"),
         ("services.order_service", "get_order"),
         ("services.order_service", "get_own_order")),
    ),
    "t53_move_stock": (
        RouteSpec("move_stock", "POST", "/products/{product_id}/move-stock", "other",
                  "products"),
        (("routers.product_router", "move_stock"),
         ("services.product_service", "move_stock"),
         ("services.product_service", "_own_product")),
    ),
    "t54_order_stats": (
        RouteSpec("read_own_order_stats", "GET", "/users/me/order-stats", "other", None),
        (("routers.user_router", "read_own_order_stats"),
         ("services.user_service", "order_stats")),
    ),
    "t55_vendor_directory": (
        RouteSpec("list_vendors", "GET", "/vendors", "other", None, auth="public"),
        (("routers.vendor_router", "list_vendors"),
         ("services.vendor_service", "vendor_directory")),
    ),
    "t56_order_received": (
        RouteSpec("mark_order_received", "POST", "/orders/{order_id}/received", "other",
                  "orders"),
        (("routers.order_router", "mark_order_received"),
         ("services.order_service", "mark_received"),
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
    # option 1b (tagged with the cases, before its run)
    "t39.own_then_foreign": "one-statement/atomic",
    "t39.own_then_missing": "one-statement/atomic",
    "t39.zero_price_anywhere": "typed-params",
    "t39.foreign_first": "L802",
    "t39.vendor_without_products": "L802",
    "t40.not_enough_writes_nothing": "one-statement/atomic",
    "t40.another_buyers_pending": "L802",
    "t40.another_buyers_shipped": "L802",
    "t40.admin_is_not_the_buyer": "L802",
    "t41.vicky_busy_writes_nothing": "one-statement/atomic",
    "t41.victor_busy_writes_nothing": "one-statement/atomic",
    "t42.later_item_missing_writes_nothing": "one-statement/atomic",
    "t42.another_buyers_order": "L802",
    "t43.duplicate_name_writes_nothing": "one-statement/atomic",
    "t43.existing_vendor": "one-statement/atomic",
    "t43.zero_price": "typed-params",
    "t44.later_foreign_writes_nothing": "one-statement/atomic",
    "t44.later_unknown_writes_nothing": "one-statement/atomic",
    "t44.later_not_pending_writes_nothing": "one-statement/atomic",
    "t44.first_foreign": "L802",
    "t44.dave_on_carols": "L802",
}
CONTROL_TICKETS = frozenset({"t35_vendor_order_lines"})


def mechanism(case_name: str) -> str:
    return MECHANISMS.get(case_name, "none")


# ---- arm L's start state: the imported app answers as the original, route by route -----------
#
# The JustNotes lesson (round 5): an import that passed its own generated suite was
# already wrong on requests that suite never made. Before any run, the app arm L starts
# from (bench/imports/multivendor emitted into the original, islands kept as Python) is
# compared with the original on these requests: every existing route, as every kind of
# caller, on other users' rows, bad ids and bodies, and each auth failure.

FIDELITY_WRITES = ("users", "vendors", "products", "orders", "order_items")


def _fid(what: str, method: str, path: str, user: str | None = None, db: str = "seed",
         **kw: Any) -> Case:  # fmt: skip
    return Case(f"start.{what}", method, path, user=user, db=db, route=None, **kw)


def fidelity_cases() -> list[Case]:
    w = {"tables": FIDELITY_WRITES}
    out: list[Case] = []
    # the auth routes
    reg = {"full_name": "New Person", "email": "new@example.com", "password": "pw123456"}
    out += [
        _fid("register", "POST", "/auth/register", json=reg, **w),
        _fid(
            "register_taken",
            "POST",
            "/auth/register",
            json={**reg, "email": "carol@example.com"},
            **w,
        ),
        _fid("register_bad_email", "POST", "/auth/register", json={**reg, "email": "nope"}, **w),
        _fid("register_missing", "POST", "/auth/register", json={"email": "x@example.com"}, **w),
    ]
    for who, pw in (
        ("carol", "carolpass1"),
        ("carol", "wrong"),
        ("erin", "erinpass1"),
        ("nobody", "whatever1"),
    ):
        out.append(
            _fid(
                f"login_{who}_{pw}",
                "POST",
                "/auth/login",
                form={"username": f"{who}@example.com", "password": pw},
            )
        )
    # the caller's own profile
    for who in HANDLES:
        if who != "erin":
            out.append(_fid(f"me_{who}", "GET", "/users/me", who))
    out += [
        _fid("me_rename", "PATCH", "/users/me", "carol", json={"full_name": "Carol C."}, **w),
        _fid("me_bad_email", "PATCH", "/users/me", "carol", json={"email": "nope"}, **w),
    ]
    # vendors
    out += [
        _fid(
            "vendor_register_buyer", "POST", "/vendors", "carol", json={"business_name": "C"}, **w
        ),
        _fid(
            "vendor_register_again", "POST", "/vendors", "vicky", json={"business_name": "V"}, **w
        ),
        *(
            _fid(f"vendor_me_{who}", "GET", "/vendors/me", who)
            for who in ("vicky", "vera", "carol", "admin")
        ),
        _fid("vendor_rename", "PATCH", "/vendors/me", "vicky", json={"business_name": "VV"}, **w),
        _fid(
            "vendor_rename_buyer", "PATCH", "/vendors/me", "carol", json={"business_name": "X"}, **w
        ),
        _fid(
            "verify_admin", "PATCH", "/vendors/3/verify", "admin", json={"is_verified": True}, **w
        ),
        _fid(
            "verify_vendor", "PATCH", "/vendors/3/verify", "vicky", json={"is_verified": True}, **w
        ),
        _fid(
            "verify_missing",
            "PATCH",
            f"/vendors/{MISSING}/verify",
            "admin",
            json={"is_verified": True},
            **w,
        ),
    ]
    # products
    new = {"name": "Lamp", "category": "home", "price": 30.0, "stock_quantity": 5}
    out += [
        _fid("product_create", "POST", "/products", "vicky", json=new, **w),
        _fid("product_create_buyer", "POST", "/products", "carol", json=new, **w),
        _fid("product_create_admin", "POST", "/products", "admin", json=new, **w),
        _fid("product_create_no_profile", "POST", "/products", "vera", "vendorless", json=new, **w),
        _fid(
            "product_create_zero_price", "POST", "/products", "vicky", json={**new, "price": 0}, **w
        ),
        _fid("products", "GET", "/products"),
        _fid("products_tools", "GET", "/products", params={"category": "tools"}),
        _fid("products_price", "GET", "/products", params={"min_price": 8, "max_price": 20}),
        _fid("products_window", "GET", "/products", params={"skip": 2, "limit": 3}),
        _fid("products_limit_101", "GET", "/products", params={"limit": 101}),
        _fid("products_negative_price", "GET", "/products", params={"min_price": -1}),
        _fid("products_inactive_sold", "GET", "/products", db="poster_sold"),
        *(_fid(f"product_{pid}", "GET", f"/products/{pid}") for pid in (MUG, POSTER, MISSING)),
        _fid("product_bad_id", "GET", "/products/abc"),
        _fid("product_update", "PATCH", f"/products/{MUG}", "vicky", json={"price": 13.0}, **w),
        _fid(
            "product_deactivate",
            "PATCH",
            f"/products/{MUG}",
            "vicky",
            json={"is_active": False},
            **w,
        ),
        _fid(
            "product_update_foreign",
            "PATCH",
            f"/products/{HAMMER}",
            "vicky",
            json={"price": 1.0},
            **w,
        ),
        _fid(
            "product_update_missing",
            "PATCH",
            f"/products/{MISSING}",
            "vicky",
            json={"price": 1.0},
            **w,
        ),
        _fid(
            "product_update_buyer", "PATCH", f"/products/{MUG}", "carol", json={"price": 1.0}, **w
        ),
        _fid(
            "product_update_admin", "PATCH", f"/products/{MUG}", "admin", json={"price": 1.0}, **w
        ),
    ]
    # orders
    one = {"items": [{"product_id": MUG, "quantity": 2}]}
    out += [
        _fid("order_place", "POST", "/orders", "carol", json=one, **w),
        _fid(
            "order_place_two",
            "POST",
            "/orders",
            "dave",
            json={
                "items": [
                    {"product_id": CANDLE, "quantity": 1},
                    {"product_id": GLOVES, "quantity": 3},
                ]
            },
            **w,
        ),
        _fid(
            "order_place_no_stock",
            "POST",
            "/orders",
            "carol",
            json={"items": [{"product_id": TAPE, "quantity": 1}]},
            **w,
        ),
        _fid("order_place_vendor", "POST", "/orders", "vicky", json=one, **w),
        _fid(
            "order_place_zero",
            "POST",
            "/orders",
            "carol",
            json={"items": [{"product_id": MUG, "quantity": 0}]},
            **w,
        ),
        _fid("order_place_empty", "POST", "/orders", "carol", json={"items": []}, **w),
        *(
            _fid(f"orders_me_{who}", "GET", "/orders/me", who)
            for who in ("carol", "dave", "vicky", "admin", "frank")
        ),
        *(
            _fid(f"order_{oid}_{who}", "GET", f"/orders/{oid}", who)
            for oid, who in (
                (1, "carol"),
                (3, "carol"),
                (3, "admin"),
                (MISSING, "carol"),
                (1, "vicky"),
            )
        ),
        _fid(
            "status_confirm",
            "PATCH",
            "/orders/7/status",
            "admin",
            json={"status": "confirmed"},
            **w,
        ),
        _fid(
            "status_ship_vendor",
            "PATCH",
            "/orders/5/status",
            "vicky",
            json={"status": "shipped"},
            **w,
        ),
        _fid(
            "status_buyer", "PATCH", "/orders/7/status", "carol", json={"status": "confirmed"}, **w
        ),
        _fid(
            "status_bad_transition",
            "PATCH",
            "/orders/7/status",
            "admin",
            json={"status": "delivered"},
            **w,
        ),
        _fid(
            "status_bad_value", "PATCH", "/orders/7/status", "admin", json={"status": "lost"}, **w
        ),
        _fid(
            "status_missing",
            "PATCH",
            f"/orders/{MISSING}/status",
            "admin",
            json={"status": "confirmed"},
            **w,
        ),
        _fid(
            "flash",
            "POST",
            "/orders/flash-sale-purchase",
            "carol",
            json={"product_id": HAMMER, "quantity": 1},
            **w,
        ),
        _fid(
            "flash_no_stock",
            "POST",
            "/orders/flash-sale-purchase",
            "carol",
            json={"product_id": TAPE, "quantity": 1},
            **w,
        ),
        _fid(
            "flash_vendor",
            "POST",
            "/orders/flash-sale-purchase",
            "vicky",
            json={"product_id": HAMMER, "quantity": 1},
            **w,
        ),
    ]
    # each auth failure, on one route of each router
    for path in ("/users/me", "/vendors/me", "/orders/me"):
        for what, token in (
            ("no_token", None),
            ("garbage", "not-a-jwt"),
            ("unknown_user", mint(MISSING)),
            ("inactive", mint(ERIN)),
        ):
            out.append(
                Case(f"start.{path.strip('/').replace('/', '_')}_{what}", "GET", path, token=token)
            )
    return out


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
    cases = tuple(c for c in SPECIFIC[ticket_id]() if c.name not in EXCLUDED)
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
        canon_name="FRESH_TIMESTAMPS_NO_VERSION_STATUS_ONLY",
    )


__all__ = [
    "CONTROL_TICKETS",
    "FIDELITY_WRITES",
    "fidelity_cases",
    "EXCLUDED",
    "STATUS_ONLY",
    "MECHANISMS",
    "SPECIFIC",
    "WRITERS",
    "canon",
    "db_factory",
    "login",
    "mechanism",
    "multivendor_ticket_oracle",
]
