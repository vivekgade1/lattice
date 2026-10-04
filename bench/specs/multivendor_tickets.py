"""Spec deltas for the multi-vendor tickets t31-t44 (Phase 0, W2-B).

Each `SpecDelta` states one ticket's requirement as the app's owner would, from the
ticket text and the app's existing behaviour: concrete scenarios on the seed data
(expected answers worked out by hand; the arithmetic is in the comments), plus programs
and invariants where Lattice can express them. `apply(SPEC, DELTAS[t])` is the spec a
change for ticket t is checked against.

Seed ids are listed in `bench.specs.multivendor`'s docstring. Every scenario starts from a
fresh seed database. Where a ticket leaves a status code or a detail open, the scenario
asserts only what the ticket (or the endpoint it says to behave like) fixes.

NESTED: endpoints answering an order "as GET /orders/{order_id} does" (OrderRead with
nested items): t36, t38, t40, t42, t44. Nested responses (W1-B) can now say the answer,
but each of these tickets still needs a construct Lattice lacks, so none has a program
(each delta's `unchecked` names it): t36 restocks one product per line (update() of
products joined to the lines is L841); t38 inserts one line per old line and its 409
detail names the product (error details are literals, L404); t40, t42 and t44 take a
list in the body (parameters are scalars). Their scenarios state them; the base spec's
get_order program covers the GET /orders/{order_id} steps they use.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from bench.specs.multivendor import order_read, product_read
from lattice.spec import Accepted, Invariant, Scenario, Spec, Step, Unchecked

__all__ = ["BLIND_DELTAS", "DELTAS", "SpecDelta", "apply"]


@dataclass(frozen=True)
class SpecDelta:
    """What a ticket adds to the spec: endpoint programs (name -> Lattice source),
    invariants, scenarios and unchecked requirements."""

    programs: Mapping[str, str] = field(default_factory=dict)
    invariants: tuple[Invariant, ...] = ()
    scenarios: tuple[Scenario, ...] = ()
    unchecked: tuple[Unchecked, ...] = ()
    approved: tuple[Accepted, ...] = ()


def apply(spec: Spec, delta: SpecDelta) -> Spec:
    """`spec` with `delta` added. Programs are given as pairs, so a delta program named
    like a base program is reported by `validate` (S013), never silently replaced."""
    return replace(
        spec,
        programs=[*spec.programs.items(), *delta.programs.items()],
        invariants=(*spec.invariants, *delta.invariants),
        scenarios=(*spec.scenarios, *delta.scenarios),
        unchecked=(*spec.unchecked, *delta.unchecked),
        approved=(*spec.approved, *delta.approved),
    )


DENIED = {"detail": "You do not have permission to perform this action."}
NO_ACCESS = {"detail": "You do not have access to this order"}
ORDER_NOT_FOUND = {"detail": "Order not found"}
NO_VENDOR = {"detail": "No vendor profile found for this user"}
NO_VENDOR_PROFILE = {"detail": "You do not have a vendor profile"}
NOT_OWNER = {"detail": "You do not own this product"}
PRODUCT_NOT_FOUND = {"detail": "Product not found"}


def _order(oid: int, **equals: object) -> dict:
    return {"orders": {"where": {"id": oid}, "equals": equals}}


def _product(pid: int, **equals: object) -> dict:
    return {"products": {"where": {"id": pid}, "equals": equals}}


def _db(*parts: dict) -> dict:
    """Merge expect_db parts; two checks of one table are not allowed (one key each)."""
    merged: dict = {}
    for part in parts:
        for table, check in part.items():
            assert table not in merged, f"two checks of {table}: split them over two steps"
            merged[table] = check
    return merged


def _get(path: str, user: str | None, **kw: object) -> Step:
    return Step("GET", path, user, **kw)  # type: ignore[arg-type]


# ======================================================================================
# t31 GET /vendors/me/sales -- a vendor's orders, units and revenue, optional start/end
# ======================================================================================
#
# Lines of vicky's products (vendor 1: P1-P4); cancelled orders (4, 9) excluded:
#   item 1  O1 01-05  P1 x2  @10.0 = 20.0     item 8  O5 02-28  P2 x2  @8.5 = 17.0
#   item 3  O2 01-31  P3 x4  @5.0  = 20.0     item 10 O6 03-01  P3 x10 @5.0 = 50.0
#   (item 6 O4 cancelled)                     item 11 O7 03-10  P1 x1  @12.0 = 12.0
#   item 7  O5 02-28  P1 x1  @12.0 = 12.0     item 12 O8 03-15  P2 x1  @8.5 = 8.5
#   all: orders {1,2,5,6,7,8} = 6, units 2+4+1+2+10+1+1 = 21,
#        revenue 20+20+12+17+50+12+8.5 = 139.5 (at the price paid: the mug's 10.00)
#   2026-02-01..2026-02-28: O5 (02-28 23:59:59, inside: end is inclusive) items 7, 8
#        -> orders 1, units 3, revenue 29.0
#   start 2026-03-01: O6, O7, O8 items 10, 11, 12 -> orders 3, units 12, revenue 70.5
#   end 2026-01-31: O1, O2 (01-31 23:30) items 1, 3 -> orders 2, units 6, revenue 40.0
# Victor's (vendor 2: P5-P8): item 2 O1 P5x1 25; item 4 O3 P5x1 25; item 5 O3 P6x2 36;
#   item 9 O5 P6x1 18; item 13 O8 P5x2 50; (item 14 O9 cancelled)
#   -> orders {1,3,5,8} = 4, units 7, revenue 154.0
#   February: O3 items 4, 5 (61.0, 3 units), O5 item 9 (18.0, 1) -> 2, 4, 79.0
# Vera has a shop and no products: zeros. Carol has no shop: 404 as GET /vendors/me.

T31 = SpecDelta(
    scenarios=(
        Scenario(
            "t31_sales_all_time",
            steps=(
                _get(
                    "/vendors/me/sales",
                    "vicky",
                    expect_body={"orders": 6, "units": 21, "revenue": 139.5},
                ),
                _get(
                    "/vendors/me/sales",
                    "victor",
                    expect_body={"orders": 4, "units": 7, "revenue": 154.0},
                ),
                _get(
                    "/vendors/me/sales", "vera", expect_body={"orders": 0, "units": 0, "revenue": 0}
                ),
            ),
        ),
        Scenario(
            "t31_sales_in_a_period",
            steps=(
                _get(
                    "/vendors/me/sales",
                    "vicky",
                    params={"start": "2026-02-01", "end": "2026-02-28"},
                    expect_body={"orders": 1, "units": 3, "revenue": 29.0},
                ),
                _get(
                    "/vendors/me/sales",
                    "victor",
                    params={"start": "2026-02-01", "end": "2026-02-28"},
                    expect_body={"orders": 2, "units": 4, "revenue": 79.0},
                ),
                _get(
                    "/vendors/me/sales",
                    "vicky",
                    params={"start": "2026-03-01"},
                    expect_body={"orders": 3, "units": 12, "revenue": 70.5},
                ),
                _get(
                    "/vendors/me/sales",
                    "vicky",
                    params={"end": "2026-01-31"},
                    expect_body={"orders": 2, "units": 6, "revenue": 40.0},
                ),
                _get(
                    "/vendors/me/sales",
                    "vicky",
                    params={"start": "2026-02-30"},
                    expect_status=422,
                ),
            ),
        ),
        Scenario(
            "t31_sales_access",
            steps=(
                _get("/vendors/me/sales", "carol", expect_status=404, expect_body=NO_VENDOR),
                _get("/vendors/me/sales", "admin", expect_status=404, expect_body=NO_VENDOR),
                _get("/vendors/me/sales", None, expect_status=401),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t31: no program -- a vendor's summary aggregates other buyers' order lines "
            "(OrderItem is owned through Order by its buyer), which Lattice refuses to "
            "show a vendor; see the base spec's note on OrderItem's two owners.",
            ("OrderItem", "Order.created_at"),
        ),
    ),
)

# ======================================================================================
# t32 GET /orders/report?month=YYYY-MM -- admins only
# ======================================================================================
#
# 2026-02: O3 (02-01 00:00, shipped, 61.0), O4 (02-14, cancelled, 25.5),
#          O5 (02-28 23:59:59, confirmed, 47.0) -> orders 3, revenue 61+47 = 108.0
# 2026-01: O1 (delivered 45.0), O2 (01-31 23:30, delivered 20.0) -> 2, 65.0
# 2026-03: O6 (03-01 00:00, delivered 50.0), O7 (pending 12.0), O8 (pending 58.5),
#          O9 (cancelled 18.0) -> 4, 50+12+58.5 = 120.5
# 2025-12: nothing.


def _report(month: str, orders: int, revenue: float, **by: int) -> dict:
    statuses = ("pending", "confirmed", "shipped", "delivered", "cancelled")
    return {
        "month": month,
        "orders": orders,
        "revenue": revenue,
        "by_status": {s: by.get(s, 0) for s in statuses},
    }


T32 = SpecDelta(
    scenarios=(
        Scenario(
            "t32_report_by_month",
            steps=(
                _get(
                    "/orders/report",
                    "admin",
                    params={"month": "2026-02"},
                    expect_body=_report("2026-02", 3, 108.0, confirmed=1, shipped=1, cancelled=1),
                ),
                _get(
                    "/orders/report",
                    "admin",
                    params={"month": "2026-01"},
                    expect_body=_report("2026-01", 2, 65.0, delivered=2),
                ),
                _get(
                    "/orders/report",
                    "admin",
                    params={"month": "2026-03"},
                    expect_body=_report("2026-03", 4, 120.5, pending=2, delivered=1, cancelled=1),
                ),
                _get(
                    "/orders/report",
                    "admin",
                    params={"month": "2025-12"},
                    expect_body=_report("2025-12", 0, 0),
                ),
            ),
        ),
        Scenario(
            "t32_report_bad_month_is_422",
            steps=(
                _get("/orders/report", "admin", params={"month": "2026-13"}, expect_status=422),
                _get("/orders/report", "admin", params={"month": "February"}, expect_status=422),
                _get("/orders/report", "admin", expect_status=422),
            ),
        ),
        Scenario(
            "t32_report_admins_only",
            steps=(
                _get(
                    "/orders/report",
                    "carol",
                    params={"month": "2026-02"},
                    expect_status=403,
                    expect_body=DENIED,
                ),
                _get(
                    "/orders/report",
                    "vicky",
                    params={"month": "2026-02"},
                    expect_status=403,
                    expect_body=DENIED,
                ),
                _get("/orders/report", None, params={"month": "2026-02"}, expect_status=401),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t32: no program -- the answer nests `by_status` as an object of counts; a "
            "Lattice response is a flat row.",
            ("Order.status", "Order.created_at"),
        ),
    ),
)

# ======================================================================================
# t33 GET /products/top-sellers -- public; active products, units sold, best first
# ======================================================================================
#
# Units per product, cancelled orders (O4: item 6; O9: item 14) excluded:
#   P1 2+1+1 = 4 (items 1, 7, 11)    P5 1+1+2 = 4 (items 2, 4, 13)
#   P2 2+1 = 3 (items 8, 12)         P6 2+1 = 3 (items 5, 9)
#   P3 4+10 = 14 (items 3, 10)       P7 0, P8 0; P4 is inactive: not listed
# Units desc, ties by product_id: P3 14, P1 4, P5 4, P2 3, P6 3, P7 0, P8 0.


def _seller(pid: int, name: str, units: int) -> dict:
    return {"product_id": pid, "name": name, "units_sold": units}


T33 = SpecDelta(
    scenarios=(
        Scenario(
            "t33_top_sellers",
            steps=(
                _get(
                    "/products/top-sellers",
                    None,
                    expect_body=[
                        _seller(3, "Dot-grid notebook", 14),
                        _seller(1, "Ceramic mug", 4),
                        _seller(5, "Claw hammer", 4),
                        _seller(2, "Soy candle", 3),
                        _seller(6, "Screwdriver set", 3),
                        _seller(7, "Tape measure", 0),
                        _seller(8, "Work gloves", 0),
                    ],
                ),
                _get(
                    "/products/top-sellers",
                    None,
                    params={"category": "tools"},
                    expect_body=[
                        _seller(5, "Claw hammer", 4),
                        _seller(6, "Screwdriver set", 3),
                        _seller(7, "Tape measure", 0),
                        _seller(8, "Work gloves", 0),
                    ],
                ),
                _get(
                    "/products/top-sellers",
                    None,
                    params={"category": "home"},
                    expect_body=[_seller(1, "Ceramic mug", 4), _seller(2, "Soy candle", 3)],
                ),
                # the only decor product is inactive
                _get("/products/top-sellers", None, params={"category": "decor"}, expect_body=[]),
                # still public with a token
                _get(
                    "/products/top-sellers", "carol", params={"category": "home"}, expect_status=200
                ),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t33: no program -- a public aggregate over order lines needs OrderItem's "
            "quantity and Order's status to be publicly aggregatable (`public_group`), a "
            "profile change a delta cannot make.",
            ("OrderItem.quantity", "Product.is_active"),
        ),
    ),
)

# ======================================================================================
# t34 GET /orders/customers -- admins only; every buyer, orders and spend
# ======================================================================================
#
# Buyers are users with role buyer: carol 5, dave 6, erin 7 (inactive, still a buyer),
# frank 8. Cancelled orders (O4 dave, O9 carol) count in neither number:
#   carol: O1 45 + O2 20 + O5 47 + O8 58.5 = 170.5 over 4 orders
#   dave:  O3 61 + O7 12 = 73.0 over 2      erin: O6 50.0 over 1     frank: 0, 0
# spent desc, ties by user_id: carol, dave, erin, frank.

T34_PROGRAM = """# lattice: v1
@endpoint("GET", "/orders/customers", auth=Roles(Admin))
def customer_spend(user: CurrentUser) -> List:
    users = fetch(User)
    buyers = where(users, col("role") == "buyer")
    all_orders = fetch(Order)
    orders = accessible(all_orders, by=user)
    live = where(orders, col("status") != "cancelled")
    per_buyer = group(live, by=["user_id"], n=agg.count(), total=agg.sum(col("total_amount")))
    joined = join(buyers, per_buyer, on={"id": "user_id"}, how="left")
    counted = derive(joined, orders=coalesce(col("n"), 0), spent=coalesce(col("total"), 0.0))
    ordered = sort(counted, by=["spent", "id"], desc=[True, False])
    named = rename(ordered, id="user_id")
    shown = select(named, cols=["user_id", "email", "orders", "spent"])
    result = limit(shown, n=100)
    return result
"""

T34 = SpecDelta(
    programs={"t34_customer_spend": T34_PROGRAM},
    scenarios=(
        Scenario(
            "t34_customer_spend",
            steps=(
                _get(
                    "/orders/customers",
                    "admin",
                    expect_body=[
                        {"user_id": 5, "email": "carol@example.com", "orders": 4, "spent": 170.5},
                        {"user_id": 6, "email": "dave@example.com", "orders": 2, "spent": 73.0},
                        {"user_id": 7, "email": "erin@example.com", "orders": 1, "spent": 50.0},
                        {"user_id": 8, "email": "frank@example.com", "orders": 0, "spent": 0},
                    ],
                ),
            ),
        ),
        Scenario(
            "t34_customer_spend_admins_only",
            steps=(
                _get("/orders/customers", "carol", expect_status=403, expect_body=DENIED),
                _get("/orders/customers", "vicky", expect_status=403, expect_body=DENIED),
                _get("/orders/customers", None, expect_status=401),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t34: the program answers at most 100 buyers (a Lattice List is bounded); the "
            "ticket lists every buyer.",
            ("t34_customer_spend",),
        ),
    ),
)

# ======================================================================================
# t35 GET /vendors/me/order-lines -- the vendor's packing list
# ======================================================================================
#
# Every line of the vendor's products, any status, newest order first, then line id.
# vicky (P1-P4): O8 item 12; O7 item 11; O6 item 10; O5 items 7, 8; O4 item 6;
#                O2 item 3; O1 item 1.
# victor (P5-P8): O9 item 14; O8 item 13; O5 item 9; O3 items 4, 5; O1 item 2.


def _pl(oid: int, status: str, pid: int, name: str, qty: int, price: float, at: str) -> dict:
    return {
        "order_id": oid,
        "status": status,
        "product_id": pid,
        "product_name": name,
        "quantity": qty,
        "price_at_purchase": price,
        "ordered_at": at,
    }


T35 = SpecDelta(
    scenarios=(
        Scenario(
            "t35_packing_list",
            steps=(
                _get(
                    "/vendors/me/order-lines",
                    "vicky",
                    expect_body=[
                        _pl(8, "pending", 2, "Soy candle", 1, 8.5, "2026-03-15T16:45:00"),
                        _pl(7, "pending", 1, "Ceramic mug", 1, 12.0, "2026-03-10T09:00:00"),
                        _pl(6, "delivered", 3, "Dot-grid notebook", 10, 5.0, "2026-03-01T00:00:00"),
                        _pl(5, "confirmed", 1, "Ceramic mug", 1, 12.0, "2026-02-28T23:59:59"),
                        _pl(5, "confirmed", 2, "Soy candle", 2, 8.5, "2026-02-28T23:59:59"),
                        _pl(4, "cancelled", 2, "Soy candle", 3, 8.5, "2026-02-14T12:00:00"),
                        _pl(2, "delivered", 3, "Dot-grid notebook", 4, 5.0, "2026-01-31T23:30:00"),
                        _pl(1, "delivered", 1, "Ceramic mug", 2, 10.0, "2026-01-05T10:00:00"),
                    ],
                ),
                _get(
                    "/vendors/me/order-lines",
                    "victor",
                    expect_body=[
                        _pl(9, "cancelled", 6, "Screwdriver set", 1, 18.0, "2026-03-20T08:15:00"),
                        _pl(8, "pending", 5, "Claw hammer", 2, 25.0, "2026-03-15T16:45:00"),
                        _pl(5, "confirmed", 6, "Screwdriver set", 1, 18.0, "2026-02-28T23:59:59"),
                        _pl(3, "shipped", 5, "Claw hammer", 1, 25.0, "2026-02-01T00:00:00"),
                        _pl(3, "shipped", 6, "Screwdriver set", 2, 18.0, "2026-02-01T00:00:00"),
                        _pl(1, "delivered", 5, "Claw hammer", 1, 25.0, "2026-01-05T10:00:00"),
                    ],
                ),
                _get("/vendors/me/order-lines", "vera", expect_body=[]),
            ),
        ),
        Scenario(
            "t35_packing_list_access",
            steps=(
                _get("/vendors/me/order-lines", "carol", expect_status=404, expect_body=NO_VENDOR),
                _get("/vendors/me/order-lines", None, expect_status=401),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t35: no program -- the vendor reads other buyers' order lines (OrderItem is "
            "owned through Order by its buyer).",
            ("OrderItem", "Order.status"),
        ),
    ),
)

# ======================================================================================
# t36 POST /orders/{order_id}/cancel -- NESTED (answers OrderRead)
# ======================================================================================
#
# A buyer cancels their own pending or confirmed order; an admin, anyone's (still only
# pending/confirmed: shipped -> cancelled is not an edge of the Order.status machine).
# Cancelling restocks every line:
#   O8 (carol, pending): item 12 P2 x1, item 13 P5 x2 -> P2 30+1 = 31, P5 15+2 = 17
#   O5 (carol, confirmed): items 7 P1 x1, 8 P2 x2, 9 P6 x1 -> P1 41, P2 32, P6 21
#   O7 (dave, pending, by the admin): item 11 P1 x1 -> P1 41
# A refused cancel writes nothing (no restock).

T36 = SpecDelta(
    scenarios=(
        Scenario(
            "t36_buyer_cancels_pending_order",
            steps=(
                Step(
                    "POST",
                    "/orders/8/cancel",
                    "carol",
                    expect_body=order_read(
                        8,
                        5,
                        "cancelled",
                        58.5,
                        "2026-03-15T16:45:00",
                        [
                            {"id": 12, "product_id": 2, "quantity": 1, "price_at_purchase": 8.5},
                            {"id": 13, "product_id": 5, "quantity": 2, "price_at_purchase": 25.0},
                        ],
                    ),
                    expect_db=_db(_order(8, status="cancelled"), _product(2, stock_quantity=31)),
                ),
                _get("/products/5", None, expect_db=_product(5, stock_quantity=17)),
                # cancelling again: cancelled -> cancelled is not an edge; no second restock
                Step(
                    "POST",
                    "/orders/8/cancel",
                    "carol",
                    expect_status=400,
                    expect_db=_product(2, stock_quantity=31),
                ),
            ),
        ),
        Scenario(
            "t36_buyer_cancels_confirmed_order",
            steps=(
                Step(
                    "POST",
                    "/orders/5/cancel",
                    "carol",
                    expect_db=_db(_order(5, status="cancelled"), _product(1, stock_quantity=41)),
                ),
                _get("/products/2", None, expect_db=_product(2, stock_quantity=32)),
                _get("/products/6", None, expect_db=_product(6, stock_quantity=21)),
            ),
        ),
        Scenario(
            "t36_admin_cancels_any_order",
            steps=(
                Step(
                    "POST",
                    "/orders/7/cancel",
                    "admin",
                    expect_db=_db(_order(7, status="cancelled"), _product(1, stock_quantity=41)),
                ),
                # O3 is shipped: not cancellable, even by the admin
                Step(
                    "POST",
                    "/orders/3/cancel",
                    "admin",
                    expect_status=400,
                    expect_db=_db(_order(3, status="shipped"), _product(5, stock_quantity=15)),
                ),
            ),
        ),
        Scenario(
            "t36_cancel_refused",
            steps=(
                # delivered (O1) and already cancelled (O9) orders stay as they are
                Step(
                    "POST",
                    "/orders/1/cancel",
                    "carol",
                    expect_status=400,
                    expect_db=_db(_order(1, status="delivered"), _product(1, stock_quantity=40)),
                ),
                Step(
                    "POST",
                    "/orders/9/cancel",
                    "carol",
                    expect_status=400,
                    expect_db=_product(6, stock_quantity=20),
                ),
                # someone else's order: 403 as GET /orders/{id}; unknown: 404
                Step(
                    "POST",
                    "/orders/8/cancel",
                    "dave",
                    expect_status=403,
                    expect_body=NO_ACCESS,
                    expect_db=_db(_order(8, status="pending"), _product(2, stock_quantity=30)),
                ),
                Step(
                    "POST",
                    "/orders/8/cancel",
                    "vicky",
                    expect_status=403,
                    expect_db=_order(8, status="pending"),
                ),
                Step(
                    "POST",
                    "/orders/999/cancel",
                    "carol",
                    expect_status=404,
                    expect_body=ORDER_NOT_FOUND,
                ),
                Step("POST", "/orders/8/cancel", None, expect_status=401),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t36: no program -- the answer (OrderRead, nested items) is expressible, but "
            "cancelling restocks each product by its line's quantity: update() of products "
            "joined to the order's lines is L841 (update needs rows of one table), and "
            "there is no per-row loop of writes. The ticket also leaves the 400 detail open.",
            ("Order", "OrderItem", "Product.stock_quantity"),
        ),
    ),
)

# ======================================================================================
# t37 GET /products/{product_id}/buyers -- the vendor's product's customers
# ======================================================================================
#
# Access as PATCH /products/{id}: vendor role (403 permission otherwise), a vendor profile
# (403 "You do not have a vendor profile"), the product exists (404), it is theirs (403).
# Units per buyer, cancelled orders excluded:
#   P1 (vicky): carol O1 2 + O5 1 = 3; dave O7 1          -> carol 3, dave 1
#   P2 (vicky): dave O4 is cancelled; carol O5 2 + O8 1 = 3 -> carol 3
#   P3 (vicky): carol O2 4; erin O6 10                    -> erin 10, carol 4
#   P5 (victor): carol O1 1 + O8 2 = 3; dave O3 1         -> carol 3, dave 1
#   P6 (victor): dave O3 2; carol O5 1 (O9 cancelled)     -> dave 2, carol 1
#   P8 (victor): never sold                               -> []


def _buyer(uid: int, name: str, units: int) -> dict:
    return {"user_id": uid, "full_name": name, "units": units}


CAROL, DAVE, ERIN = (5, "Carol Chen"), (6, "Dave Diaz"), (7, "Erin Evans")

T37 = SpecDelta(
    scenarios=(
        Scenario(
            "t37_product_buyers",
            steps=(
                _get(
                    "/products/1/buyers", "vicky", expect_body=[_buyer(*CAROL, 3), _buyer(*DAVE, 1)]
                ),
                _get("/products/2/buyers", "vicky", expect_body=[_buyer(*CAROL, 3)]),
                _get(
                    "/products/3/buyers",
                    "vicky",
                    expect_body=[_buyer(*ERIN, 10), _buyer(*CAROL, 4)],
                ),
                _get(
                    "/products/5/buyers",
                    "victor",
                    expect_body=[_buyer(*CAROL, 3), _buyer(*DAVE, 1)],
                ),
                _get(
                    "/products/6/buyers",
                    "victor",
                    expect_body=[_buyer(*DAVE, 2), _buyer(*CAROL, 1)],
                ),
                _get("/products/8/buyers", "victor", expect_body=[]),
            ),
        ),
        Scenario(
            "t37_product_buyers_access",
            steps=(
                _get("/products/1/buyers", "victor", expect_status=403, expect_body=NOT_OWNER),
                _get("/products/1/buyers", "vera", expect_status=403, expect_body=NOT_OWNER),
                _get(
                    "/products/999/buyers",
                    "victor",
                    expect_status=404,
                    expect_body=PRODUCT_NOT_FOUND,
                ),
                _get("/products/1/buyers", "carol", expect_status=403, expect_body=DENIED),
                _get("/products/1/buyers", "admin", expect_status=403, expect_body=DENIED),
                _get("/products/1/buyers", None, expect_status=401),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t37: no program -- the vendor reads other buyers' order lines and names "
            "(OrderItem is owned through Order by its buyer).",
            ("OrderItem", "User.full_name"),
        ),
    ),
)

# ======================================================================================
# t38 POST /orders/{order_id}/reorder -- NESTED (answers OrderRead, 201)
# ======================================================================================
#
# A new pending order (id 10) for the caller with the old order's products and
# quantities, priced now (as POST /orders), stock taken:
#   carol reorders O1 (P1 x2, P5 x1): total 2*12 + 1*25 = 49.0 (not O1's 45.0: the mug
#     was 10.00 then); P1 40-2 = 38, P5 15-1 = 14; 14 + 2 = 16 lines.
#   carol reorders O9 (cancelled; P6 x1): total 18.0, P6 19.
# Out of stock: carol first buys all 40 mugs (order 10, P1 0); dave's reorder of O7
#   (P1 x1) is then 409 "Insufficient stock for product 1", nothing written.

T38 = SpecDelta(
    scenarios=(
        Scenario(
            "t38_reorder",
            steps=(
                Step(
                    "POST",
                    "/orders/1/reorder",
                    "carol",
                    expect_status=201,
                    expect_db=_db(
                        _order(10, user_id=5, status="pending", total_amount=49.0),
                        _product(1, stock_quantity=38),
                        {"order_items": {"where": {"order_id": 10}, "count": 2}},
                    ),
                ),
                _get(
                    "/products/5",
                    None,
                    expect_db={
                        "products": {"where": {"id": 5}, "equals": {"stock_quantity": 14}},
                        "order_items": {
                            "where": {"order_id": 10, "product_id": 1},
                            "equals": {"quantity": 2, "price_at_purchase": 12.0},
                        },
                    },
                ),
                # the original order is untouched
                _get(
                    "/orders/1", "carol", expect_db=_order(1, status="delivered", total_amount=45.0)
                ),
            ),
        ),
        Scenario(
            "t38_reorder_a_cancelled_order",
            steps=(
                Step(
                    "POST",
                    "/orders/9/reorder",
                    "carol",
                    expect_status=201,
                    expect_db=_db(
                        _order(10, user_id=5, status="pending", total_amount=18.0),
                        _product(6, stock_quantity=19),
                    ),
                ),
            ),
        ),
        Scenario(
            "t38_reorder_out_of_stock",
            steps=(
                Step(
                    "POST",
                    "/orders",
                    "carol",
                    json={"items": [{"product_id": 1, "quantity": 40}]},
                    expect_status=201,
                    expect_db=_product(1, stock_quantity=0),
                ),
                Step(
                    "POST",
                    "/orders/7/reorder",
                    "dave",
                    expect_status=409,
                    expect_body={"detail": "Insufficient stock for product 1"},
                    expect_db=_db({"orders": {"count": 10}}, _product(1, stock_quantity=0)),
                ),
            ),
        ),
        Scenario(
            "t38_reorder_access",
            steps=(
                Step(
                    "POST",
                    "/orders/1/reorder",
                    "dave",
                    expect_status=403,
                    expect_body=NO_ACCESS,
                    expect_db={"orders": {"count": 9}},
                ),
                Step(
                    "POST",
                    "/orders/999/reorder",
                    "carol",
                    expect_status=404,
                    expect_body=ORDER_NOT_FOUND,
                ),
                Step(
                    "POST",
                    "/orders/1/reorder",
                    "admin",
                    expect_status=403,
                    expect_body=DENIED,
                    expect_db={"orders": {"count": 9}},
                ),
                Step("POST", "/orders/1/reorder", "vicky", expect_status=403, expect_body=DENIED),
                Step("POST", "/orders/1/reorder", None, expect_status=401),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t38: no program -- the answer (OrderRead, nested items) is expressible, but a "
            "reorder inserts one line per old line (insert() writes one row; no per-row "
            "loop of writes), takes each product's stock by its line (update() of products "
            "joined to the lines is L841), and its 409 detail names the product (error "
            "details are literals, L404).",
            ("Order", "OrderItem", "Product.stock_quantity"),
        ),
    ),
)

# ======================================================================================
# t39 POST /products/bulk-price -- several PATCH /products/{id} price changes at once
# ======================================================================================
#
# vicky: P1 12.0 -> 13.5, P2 8.5 -> 9.0 -> {"updated": 2}. Errors as PATCH: a refused
# request changes nothing (here the bad change comes first, so this holds whatever the
# order of checking).

T39 = SpecDelta(
    scenarios=(
        Scenario(
            "t39_bulk_price",
            steps=(
                Step(
                    "POST",
                    "/products/bulk-price",
                    "vicky",
                    json={
                        "changes": [
                            {"product_id": 1, "price": 13.5},
                            {"product_id": 2, "price": 9.0},
                        ]
                    },
                    expect_body={"updated": 2},
                    expect_db=_product(1, price=13.5, stock_quantity=40, is_active=True),
                ),
                _get("/products/2", None, expect_body=product_read(2, price=9.0)),
                Step(
                    "POST",
                    "/products/bulk-price",
                    "vicky",
                    json={"changes": []},
                    expect_body={"updated": 0},
                ),
            ),
        ),
        Scenario(
            "t39_bulk_price_errors",
            steps=(
                Step(
                    "POST",
                    "/products/bulk-price",
                    "vicky",
                    json={"changes": [{"product_id": 5, "price": 1.0}]},
                    expect_status=403,
                    expect_body=NOT_OWNER,
                    expect_db=_product(5, price=25.0),
                ),
                Step(
                    "POST",
                    "/products/bulk-price",
                    "vicky",
                    json={
                        "changes": [
                            {"product_id": 999, "price": 1.0},
                            {"product_id": 1, "price": 1.0},
                        ]
                    },
                    expect_status=404,
                    expect_body=PRODUCT_NOT_FOUND,
                    expect_db=_product(1, price=12.0),
                ),
                Step(
                    "POST",
                    "/products/bulk-price",
                    "vicky",
                    json={
                        "changes": [
                            {"product_id": 5, "price": 1.0},
                            {"product_id": 1, "price": 1.0},
                        ]
                    },
                    expect_status=403,
                    expect_body=NOT_OWNER,
                    expect_db=_product(1, price=12.0),
                ),
                # price must be positive, as for PATCH
                Step(
                    "POST",
                    "/products/bulk-price",
                    "vicky",
                    json={"changes": [{"product_id": 1, "price": 0}]},
                    expect_status=422,
                    expect_db=_product(1, price=12.0),
                ),
                Step(
                    "POST",
                    "/products/bulk-price",
                    "vera",
                    json={"changes": [{"product_id": 1, "price": 1.0}]},
                    expect_status=403,
                    expect_body=NOT_OWNER,
                ),
                Step(
                    "POST",
                    "/products/bulk-price",
                    "carol",
                    json={"changes": [{"product_id": 1, "price": 1.0}]},
                    expect_status=403,
                    expect_body=DENIED,
                ),
                Step(
                    "POST",
                    "/products/bulk-price",
                    "admin",
                    json={"changes": [{"product_id": 1, "price": 1.0}]},
                    expect_status=403,
                    expect_body=DENIED,
                    expect_db=_product(1, price=12.0),
                ),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t39: no program -- the body is a list of changes; Lattice parameters are scalars.",
            ("Product.price",),
        ),
        Unchecked(
            "t39: a request with a refused change (404/403) should change nothing, even "
            "when the refused change comes after accepted ones (the ticket does not say).",
            ("Product.price",),
        ),
    ),
)

# ======================================================================================
# t40 PATCH /orders/{order_id}/items -- NESTED; replace a pending order's lines
# ======================================================================================
#
# O8 (carol, pending: item 12 P2 x1 @8.5, item 13 P5 x2 @25.0, total 58.5) becomes
#   [P1 x2, P5 x1]: stock back P2 30+1 = 31, P5 15+2 = 17; then taken P1 40-2 = 38,
#   P5 17-1 = 16; total 2*12 + 1*25 = 49.0; 2 lines; 14 lines in all.
# Priced now: vicky raises P2 to 9.5 first; O8 becomes [P2 x2]: total 2*9.5 = 19.0,
#   P2 30+1-2 = 29, P5 15+2 = 17.
# The order's own units count as available: O7 (dave: P1 x1, P1 stock 40) becomes
#   [P1 x41]: 40+1 = 41 available -> P1 0, total 41*12 = 492.0.

T40 = SpecDelta(
    scenarios=(
        Scenario(
            "t40_edit_order_items",
            steps=(
                Step(
                    "PATCH",
                    "/orders/8/items",
                    "carol",
                    json={
                        "items": [
                            {"product_id": 1, "quantity": 2},
                            {"product_id": 5, "quantity": 1},
                        ]
                    },
                    expect_db=_db(
                        _order(8, status="pending", total_amount=49.0),
                        _product(1, stock_quantity=38),
                        {"order_items": {"where": {"order_id": 8}, "count": 2}},
                    ),
                ),
                _get(
                    "/products/2",
                    None,
                    expect_db=_db(_product(2, stock_quantity=31), {"order_items": {"count": 14}}),
                ),
                _get(
                    "/products/5",
                    None,
                    expect_db={
                        "products": {"where": {"id": 5}, "equals": {"stock_quantity": 16}},
                        "order_items": {
                            "where": {"order_id": 8, "product_id": 5},
                            "equals": {"quantity": 1, "price_at_purchase": 25.0},
                        },
                    },
                ),
            ),
        ),
        Scenario(
            "t40_lines_priced_now",
            steps=(
                Step("PATCH", "/products/2", "vicky", json={"price": 9.5}),
                Step(
                    "PATCH",
                    "/orders/8/items",
                    "carol",
                    json={"items": [{"product_id": 2, "quantity": 2}]},
                    expect_db=_db(
                        _order(8, total_amount=19.0),
                        _product(2, stock_quantity=29),
                        {
                            "order_items": {
                                "where": {"order_id": 8},
                                "equals": {
                                    "product_id": 2,
                                    "quantity": 2,
                                    "price_at_purchase": 9.5,
                                },
                            }
                        },
                    ),
                ),
                _get("/products/5", None, expect_db=_product(5, stock_quantity=17)),
            ),
        ),
        Scenario(
            "t40_own_units_count_as_available",
            steps=(
                Step(
                    "PATCH",
                    "/orders/7/items",
                    "dave",
                    json={"items": [{"product_id": 1, "quantity": 41}]},
                    expect_db=_db(_order(7, total_amount=492.0), _product(1, stock_quantity=0)),
                ),
            ),
        ),
        Scenario(
            "t40_edit_refused",
            steps=(
                # P7 is out of stock: 409 as POST /orders, nothing written
                Step(
                    "PATCH",
                    "/orders/8/items",
                    "carol",
                    json={"items": [{"product_id": 7, "quantity": 1}]},
                    expect_status=409,
                    expect_body={"detail": "Insufficient stock for product 7"},
                    expect_db=_db(
                        _order(8, total_amount=58.5),
                        _product(2, stock_quantity=30),
                        {"order_items": {"where": {"order_id": 8}, "count": 2}},
                    ),
                ),
                _get("/products/5", None, expect_db=_product(5, stock_quantity=15)),
                Step(
                    "PATCH",
                    "/orders/5/items",
                    "carol",
                    json={"items": [{"product_id": 1, "quantity": 1}]},
                    expect_status=400,
                    expect_body={"detail": "Only pending orders can be changed"},
                    expect_db=_db(_order(5, total_amount=47.0), _product(1, stock_quantity=40)),
                ),
                Step(
                    "PATCH",
                    "/orders/8/items",
                    "dave",
                    json={"items": [{"product_id": 1, "quantity": 1}]},
                    expect_status=403,
                    expect_body=NO_ACCESS,
                    expect_db=_order(8, total_amount=58.5),
                ),
                Step(
                    "PATCH",
                    "/orders/8/items",
                    "admin",
                    json={"items": [{"product_id": 1, "quantity": 1}]},
                    expect_status=403,
                    expect_body=NO_ACCESS,
                    expect_db=_order(8, total_amount=58.5),
                ),
                Step(
                    "PATCH",
                    "/orders/999/items",
                    "carol",
                    json={"items": [{"product_id": 1, "quantity": 1}]},
                    expect_status=404,
                    expect_body=ORDER_NOT_FOUND,
                ),
                Step(
                    "PATCH",
                    "/orders/8/items",
                    "carol",
                    json={"items": [{"product_id": 1, "quantity": 0}]},
                    expect_status=422,
                    expect_db=_order(8, total_amount=58.5),
                ),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t40: no program -- the answer (OrderRead, nested items) is expressible, but "
            "the body is a list of lines (Lattice parameters are scalars), each line is "
            "its own insert and stock change (no per-row loop of writes; L841), and the "
            "409 detail names the product (error details are literals, L404).",
            ("Order", "OrderItem"),
        ),
    ),
)

# ======================================================================================
# t41 POST /vendors/me/close
# ======================================================================================
#
# vicky's products appear in O5 (confirmed): 409 until it is delivered. After the admin
# moves O5 confirmed -> shipped -> delivered, vicky closes:
#   products deactivated: her active ones P1, P2, P3 (P4 already inactive) = 3
#   pending orders with her products: O7 (P1 x1), O8 (P2 x1 and victor's P5 x2) = 2,
#   cancelled whole, stock back: P1 40+1 = 41, P2 30+1 = 31, P5 15+2 = 17.
# victor: O3 shipped and O5 confirmed contain his products -> 409.
# vera: no products -> 0 and 0.

T41 = SpecDelta(
    scenarios=(
        Scenario(
            "t41_close_shop",
            steps=(
                Step("PATCH", "/orders/5/status", "admin", json={"status": "shipped"}),
                Step("PATCH", "/orders/5/status", "admin", json={"status": "delivered"}),
                Step(
                    "POST",
                    "/vendors/me/close",
                    "vicky",
                    expect_body={"products_deactivated": 3, "orders_cancelled": 2},
                    expect_db=_db(
                        {"products": {"where": {"vendor_id": 1}, "equals": {"is_active": False}}},
                        _order(7, status="cancelled"),
                    ),
                ),
                _get(
                    "/products/1",
                    None,
                    expect_db=_db(_product(1, stock_quantity=41), _order(8, status="cancelled")),
                ),
                _get("/products/2", None, expect_db=_product(2, stock_quantity=31)),
                _get(
                    "/products/5",
                    None,
                    expect_db=_db(
                        _product(5, stock_quantity=17, is_active=True),
                        {"vendors": {"count": 3}},
                    ),
                ),
                # orders that were not pending stay as they were
                _get("/orders/1", "carol", expect_db=_order(1, status="delivered")),
            ),
        ),
        Scenario(
            "t41_close_refused_while_fulfilling",
            steps=(
                Step(
                    "POST",
                    "/vendors/me/close",
                    "vicky",
                    expect_status=409,
                    expect_body={"detail": "Shop has orders being fulfilled"},
                    expect_db=_db(
                        _product(1, is_active=True, stock_quantity=40), _order(7, status="pending")
                    ),
                ),
                Step(
                    "POST",
                    "/vendors/me/close",
                    "victor",
                    expect_status=409,
                    expect_body={"detail": "Shop has orders being fulfilled"},
                    expect_db=_db(_product(8, is_active=True), _order(8, status="pending")),
                ),
            ),
        ),
        Scenario(
            "t41_close_empty_shop_and_access",
            steps=(
                Step(
                    "POST",
                    "/vendors/me/close",
                    "vera",
                    expect_body={"products_deactivated": 0, "orders_cancelled": 0},
                ),
                Step(
                    "POST", "/vendors/me/close", "carol", expect_status=404, expect_body=NO_VENDOR
                ),
                Step("POST", "/vendors/me/close", None, expect_status=401),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t41: no program -- closing cancels other buyers' orders and restocks their "
            "lines (rows owned by other users, one update per line).",
            ("Order.status", "Product.is_active", "Product.stock_quantity"),
        ),
    ),
)

# ======================================================================================
# t42 POST /orders/{order_id}/split -- NESTED (answers the new OrderRead, 201)
# ======================================================================================
#
# carol splits item 13 (P5 x2 @25.0) off O8: new order 10 (carol, pending) total 50.0,
# O8 keeps item 12 (P2 x1 @8.5) total 8.5; stock unchanged; lines keep their prices.

T42 = SpecDelta(
    scenarios=(
        Scenario(
            "t42_split_order",
            steps=(
                Step(
                    "POST",
                    "/orders/8/split",
                    "carol",
                    json={"item_ids": [13]},
                    expect_status=201,
                    expect_db=_db(
                        _order(10, user_id=5, status="pending", total_amount=50.0),
                        {
                            "order_items": {
                                "where": {"id": 13},
                                "equals": {
                                    "order_id": 10,
                                    "quantity": 2,
                                    "price_at_purchase": 25.0,
                                },
                            }
                        },
                        _product(5, stock_quantity=15),
                    ),
                ),
                _get(
                    "/orders/8",
                    "carol",
                    expect_body=order_read(
                        8,
                        5,
                        "pending",
                        8.5,
                        "2026-03-15T16:45:00",
                        [{"id": 12, "product_id": 2, "quantity": 1, "price_at_purchase": 8.5}],
                    ),
                    expect_db=_db({"orders": {"count": 10}}, _product(2, stock_quantity=30)),
                ),
            ),
        ),
        Scenario(
            "t42_split_refused",
            steps=(
                Step(
                    "POST",
                    "/orders/8/split",
                    "carol",
                    json={"item_ids": [12, 13]},
                    expect_status=400,
                    expect_body={"detail": "Cannot move every item"},
                    expect_db=_db(
                        {"orders": {"count": 9}},
                        {"order_items": {"where": {"id": 13}, "equals": {"order_id": 8}}},
                    ),
                ),
                Step(
                    "POST",
                    "/orders/7/split",
                    "dave",
                    json={"item_ids": [11]},
                    expect_status=400,
                    expect_body={"detail": "Cannot move every item"},
                ),
                Step(
                    "POST",
                    "/orders/8/split",
                    "carol",
                    json={"item_ids": [1]},
                    expect_status=400,
                    expect_body={"detail": "Item 1 is not in this order"},
                    expect_db=_db(
                        {"orders": {"count": 9}},
                        {"order_items": {"where": {"id": 1}, "equals": {"order_id": 1}}},
                    ),
                ),
                Step(
                    "POST",
                    "/orders/8/split",
                    "carol",
                    json={"item_ids": [999]},
                    expect_status=400,
                    expect_body={"detail": "Item 999 is not in this order"},
                ),
                Step(
                    "POST",
                    "/orders/5/split",
                    "carol",
                    json={"item_ids": [9]},
                    expect_status=400,
                    expect_body={"detail": "Only pending orders can be split"},
                    expect_db=_db(
                        {"orders": {"count": 9}},
                        {"order_items": {"where": {"id": 9}, "equals": {"order_id": 5}}},
                    ),
                ),
                Step(
                    "POST",
                    "/orders/8/split",
                    "dave",
                    json={"item_ids": [13]},
                    expect_status=403,
                    expect_body=NO_ACCESS,
                    expect_db={"orders": {"count": 9}},
                ),
                Step(
                    "POST",
                    "/orders/999/split",
                    "carol",
                    json={"item_ids": [13]},
                    expect_status=404,
                    expect_body=ORDER_NOT_FOUND,
                ),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t42: no program -- the answer (OrderRead, nested items) is expressible, but "
            "the body is a list of item ids (Lattice parameters are scalars), and the 400 "
            "'Item <id> is not in this order' names the item (error details are "
            "literals, L404).",
            ("Order", "OrderItem"),
        ),
    ),
)

# ======================================================================================
# t43 POST /vendors/onboard -- a shop and its first products in one step (201)
# ======================================================================================
#
# frank (user 8) opens vendor 4 with products 9 and 10 (8 seeded); he becomes a vendor.
# A refused request (already a vendor, duplicate name, invalid product) writes nothing.

PICKET = {"name": "Picket", "category": "garden", "price": 3.5, "stock_quantity": 100}
GATE = {
    "name": "Gate",
    "description": "Oak.",
    "category": "garden",
    "price": 120.0,
    "stock_quantity": 2,
}


def _new_product(pid: int, spec: dict) -> dict:
    return {
        "id": pid,
        "vendor_id": 4,
        "name": spec["name"],
        "description": spec.get("description"),
        "category": spec["category"],
        "price": spec["price"],
        "stock_quantity": spec["stock_quantity"],
        "version": 0,
        "is_active": True,
    }


NOTHING_WRITTEN = _db(
    {"vendors": {"count": 3}},
    {"products": {"count": 8}},
    {"users": {"where": {"id": 8}, "equals": {"role": "buyer"}}},
)

T43 = SpecDelta(
    scenarios=(
        Scenario(
            "t43_onboard",
            steps=(
                Step(
                    "POST",
                    "/vendors/onboard",
                    "frank",
                    json={"business_name": "Frank's Fences", "products": [PICKET, GATE]},
                    expect_status=201,
                    expect_body={
                        "vendor": {
                            "id": 4,
                            "user_id": 8,
                            "business_name": "Frank's Fences",
                            "is_verified": False,
                        },
                        "products": [_new_product(9, PICKET), _new_product(10, GATE)],
                    },
                    expect_db=_db(
                        {"users": {"where": {"id": 8}, "equals": {"role": "vendor"}}},
                        {"products": {"where": {"vendor_id": 4}, "count": 2}},
                        {"vendors": {"where": {"user_id": 8}, "count": 1}},
                    ),
                ),
                _get("/products/9", None, expect_body=_new_product(9, PICKET)),
                _get(
                    "/vendors/me",
                    "frank",
                    expect_body={
                        "id": 4,
                        "user_id": 8,
                        "business_name": "Frank's Fences",
                        "is_verified": False,
                    },
                ),
            ),
        ),
        Scenario(
            "t43_onboard_refused",
            steps=(
                Step(
                    "POST",
                    "/vendors/onboard",
                    "frank",
                    json={
                        "business_name": "Frank's Fences",
                        "products": [PICKET, {**GATE, "name": "Picket"}],
                    },
                    expect_status=400,
                    expect_body={"detail": "Duplicate product name: Picket"},
                    expect_db=NOTHING_WRITTEN,
                ),
                Step(
                    "POST",
                    "/vendors/onboard",
                    "frank",
                    json={
                        "business_name": "Frank's Fences",
                        "products": [PICKET, {**GATE, "price": 0}],
                    },
                    expect_status=422,
                    expect_db=NOTHING_WRITTEN,
                ),
                Step(
                    "POST",
                    "/vendors/onboard",
                    "frank",
                    json={
                        "business_name": "Frank's Fences",
                        "products": [{**PICKET, "stock_quantity": -1}],
                    },
                    expect_status=422,
                    expect_db=NOTHING_WRITTEN,
                ),
                Step(
                    "POST",
                    "/vendors/onboard",
                    "vicky",
                    json={"business_name": "Second shop", "products": [PICKET]},
                    expect_status=400,
                    expect_body={"detail": "This user is already registered as a vendor"},
                    expect_db=_db({"vendors": {"count": 3}}, {"products": {"count": 8}}),
                ),
                Step(
                    "POST",
                    "/vendors/onboard",
                    None,
                    json={"business_name": "Nobody", "products": [PICKET]},
                    expect_status=401,
                ),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t43: no program -- the body holds a list of products and the answer nests "
            "the vendor and a list of products.",
            ("Vendor", "Product"),
        ),
    ),
)

# ======================================================================================
# t44 POST /orders/merge -- NESTED (answers the merged OrderRead)
# ======================================================================================
#
# carol has one pending order (O8: items 12 P2 x1 @8.5, 13 P5 x2 @25.0, total 58.5), so
# she first places order 10 (P3 x2 @5.0 = 10.0; P3 100-2 = 98).
#   merge [8, 10]: O8 gets item 15 too, total 58.5 + 10.0 = 68.5; O10 cancelled, no
#     lines, total 0. Merging moves lines: no restock (P3 stays 98, P2 30, P5 15).
#   merge [10, 8]: O10 gets items 12, 13 + its own, total 68.5; O8 cancelled, empty, 0.

PLACE_ORDER_10 = Step(
    "POST",
    "/orders",
    "carol",
    json={"items": [{"product_id": 3, "quantity": 2}]},
    expect_status=201,
    expect_db=_db(_order(10, total_amount=10.0), _product(3, stock_quantity=98)),
)

T44 = SpecDelta(
    scenarios=(
        Scenario(
            "t44_merge_into_first",
            steps=(
                PLACE_ORDER_10,
                Step(
                    "POST",
                    "/orders/merge",
                    "carol",
                    json={"order_ids": [8, 10]},
                    expect_db=_db(
                        _order(8, status="pending", total_amount=68.5),
                        {"order_items": {"where": {"order_id": 8}, "count": 3}},
                        _product(3, stock_quantity=98),
                    ),
                ),
                _get(
                    "/orders/10",
                    "carol",
                    expect_db=_db(
                        _order(10, status="cancelled", total_amount=0.0),
                        {"order_items": {"where": {"order_id": 10}, "count": 0}},
                        _product(2, stock_quantity=30),
                    ),
                ),
                _get("/products/5", None, expect_db=_product(5, stock_quantity=15)),
            ),
        ),
        Scenario(
            "t44_merge_order_of_ids",
            steps=(
                PLACE_ORDER_10,
                Step(
                    "POST",
                    "/orders/merge",
                    "carol",
                    json={"order_ids": [10, 8]},
                    expect_db=_db(
                        _order(10, status="pending", total_amount=68.5),
                        {"order_items": {"where": {"order_id": 10}, "count": 3}},
                    ),
                ),
                _get(
                    "/orders/8",
                    "carol",
                    expect_body=order_read(8, 5, "cancelled", 0.0, "2026-03-15T16:45:00", []),
                ),
            ),
        ),
        Scenario(
            "t44_merge_refused",
            steps=(
                PLACE_ORDER_10,
                Step(
                    "POST",
                    "/orders/merge",
                    "carol",
                    json={"order_ids": [8]},
                    expect_status=422,
                    expect_db=_order(8, total_amount=58.5),
                ),
                Step(
                    "POST",
                    "/orders/merge",
                    "carol",
                    json={"order_ids": [8, 5]},
                    expect_status=400,
                    expect_body={"detail": "Only pending orders can be merged"},
                    expect_db=_db(
                        _order(8, total_amount=58.5),
                        {"order_items": {"where": {"order_id": 5}, "count": 3}},
                    ),
                ),
                Step(
                    "POST",
                    "/orders/merge",
                    "carol",
                    json={"order_ids": [8, 7]},
                    expect_status=403,
                    expect_body=NO_ACCESS,
                    expect_db=_db(
                        _order(7, status="pending", total_amount=12.0),
                        {"order_items": {"where": {"order_id": 8}, "count": 2}},
                    ),
                ),
                Step(
                    "POST",
                    "/orders/merge",
                    "carol",
                    json={"order_ids": [8, 999]},
                    expect_status=404,
                    expect_body=ORDER_NOT_FOUND,
                    expect_db=_order(8, total_amount=58.5),
                ),
                Step(
                    "POST",
                    "/orders/merge",
                    "dave",
                    json={"order_ids": [7, 8]},
                    expect_status=403,
                    expect_body=NO_ACCESS,
                    expect_db=_order(7, total_amount=12.0),
                ),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t44: no program -- the answer (OrderRead, nested items) is expressible, but "
            "the body is a list of order ids (Lattice parameters are scalars), and the "
            "first listed order is the target (no list indexing).",
            ("Order", "OrderItem"),
        ),
    ),
)

# The deltas as written from the ticket texts, before any check ran (arm S* of Phase 1).
BLIND_DELTAS: dict[str, SpecDelta] = {
    "t31_vendor_sales": T31,
    "t32_monthly_report": T32,
    "t33_top_sellers": T33,
    "t34_customer_spend": T34,
    "t35_vendor_order_lines": T35,
    "t36_cancel_order": T36,
    "t37_product_buyers": T37,
    "t38_reorder": T38,
    "t39_bulk_price": T39,
    "t40_edit_order_items": T40,
    "t41_close_shop": T41,
    "t42_split_order": T42,
    "t43_vendor_onboarding": T43,
    "t44_merge_orders": T44,
}


# ======================================================================================
# Added after the first exit run (2026-10-04)
# ======================================================================================
#
# Everything above was written from the ticket texts before any check ran: that run
# caught 44 of the 50 trap variants (bench/specs/phase0-exit-blind.json). The
# additions below close the six requirements those deltas left unstated. Each one is
# stated by its ticket's text, but was written *after* seeing the misses, so the blind
# number is the honest one for "a spec written from a ticket". The t33 approval states
# the ticket's "It is public, like GET /products" for its aggregate over orders.

T33 = replace(
    T33,
    approved=(
        Accepted(
            "A:PY801:GET /products/top-sellers:*",
            "t33: 'It is public, like GET /products': units sold are counted over every "
            "buyer's orders; only per-product totals are shown",
        ),
    ),
)

T35 = replace(
    T35,
    scenarios=(
        *T35.scenarios,
        # "every order line for the vendor's products": a delisted product is still hers
        Scenario(
            "t35_lines_of_a_delisted_product",
            steps=(
                Step("PATCH", "/products/2", "vicky", json={"is_active": False}),
                _get(
                    "/vendors/me/order-lines",
                    "vicky",
                    expect_body=[
                        _pl(8, "pending", 2, "Soy candle", 1, 8.5, "2026-03-15T16:45:00"),
                        _pl(7, "pending", 1, "Ceramic mug", 1, 12.0, "2026-03-10T09:00:00"),
                        _pl(6, "delivered", 3, "Dot-grid notebook", 10, 5.0, "2026-03-01T00:00:00"),
                        _pl(5, "confirmed", 1, "Ceramic mug", 1, 12.0, "2026-02-28T23:59:59"),
                        _pl(5, "confirmed", 2, "Soy candle", 2, 8.5, "2026-02-28T23:59:59"),
                        _pl(4, "cancelled", 2, "Soy candle", 3, 8.5, "2026-02-14T12:00:00"),
                        _pl(2, "delivered", 3, "Dot-grid notebook", 4, 5.0, "2026-01-31T23:30:00"),
                        _pl(1, "delivered", 1, "Ceramic mug", 2, 10.0, "2026-01-05T10:00:00"),
                    ],
                ),
            ),
        ),
    ),
)

T36 = replace(
    T36,
    scenarios=(
        *T36.scenarios,
        # "A buyer can cancel their own order": another buyer's order is refused as
        # GET /orders/{order_id} refuses it, whatever its status, and stays as it is
        Scenario(
            "t36_another_buyers_order",
            steps=(
                Step(
                    "POST",
                    "/orders/3/cancel",
                    "carol",
                    expect_status=403,
                    expect_body=NO_ACCESS,
                    expect_db=_db(_order(3, status="shipped"), _product(5, stock_quantity=15)),
                ),
            ),
        ),
    ),
)

T39 = replace(
    T39,
    scenarios=(
        *T39.scenarios,
        # "{"updated": <number of products changed>}": a product listed twice is one
        Scenario(
            "t39_a_product_listed_twice",
            steps=(
                Step(
                    "POST",
                    "/products/bulk-price",
                    "vicky",
                    json={
                        "changes": [
                            {"product_id": 1, "price": 13.0},
                            {"product_id": 1, "price": 14.0},
                        ]
                    },
                    expect_body={"updated": 1},
                ),
            ),
        ),
    ),
)

T40 = replace(
    T40,
    scenarios=(
        *T40.scenarios,
        # "Only the buyer who placed the order can change it (anyone else gets 403 ...)":
        # also when the order is not pending (O3: dave's, shipped)
        Scenario(
            "t40_another_buyers_shipped_order",
            steps=(
                Step(
                    "PATCH",
                    "/orders/3/items",
                    "carol",
                    json={"items": [{"product_id": 1, "quantity": 1}]},
                    expect_status=403,
                    expect_body=NO_ACCESS,
                    expect_db=_db(_order(3, status="shipped", total_amount=61.0)),
                ),
            ),
        ),
    ),
)

T42 = replace(
    T42,
    scenarios=(
        *T42.scenarios,
        # "the listed lines ... move": a moved line keeps its price, even after the
        # product's price changed (victor raises P5 to 30.0 first)
        Scenario(
            "t42_moved_lines_keep_their_price",
            steps=(
                Step("PATCH", "/products/5", "victor", json={"price": 30.0}),
                Step(
                    "POST",
                    "/orders/8/split",
                    "carol",
                    json={"item_ids": [13]},
                    expect_status=201,
                    expect_db=_db(
                        _order(10, total_amount=50.0),
                        {
                            "order_items": {
                                "where": {"id": 13},
                                "equals": {"order_id": 10, "price_at_purchase": 25.0},
                            }
                        },
                    ),
                ),
            ),
        ),
    ),
)

T43 = replace(
    T43,
    scenarios=(
        *T43.scenarios,
        # "Product names must be unique within the request": "Picket" and "picket" differ
        Scenario(
            "t43_names_differing_in_case",
            steps=(
                Step(
                    "POST",
                    "/vendors/onboard",
                    "frank",
                    json={
                        "business_name": "Frank's Fences",
                        "products": [PICKET, {**PICKET, "name": "picket"}],
                    },
                    expect_status=201,
                    expect_db=_db({"products": {"where": {"vendor_id": 4}, "count": 2}}),
                ),
            ),
        ),
    ),
)

DELTAS: dict[str, SpecDelta] = {
    "t31_vendor_sales": T31,
    "t32_monthly_report": T32,
    "t33_top_sellers": T33,
    "t34_customer_spend": T34,
    "t35_vendor_order_lines": T35,
    "t36_cancel_order": T36,
    "t37_product_buyers": T37,
    "t38_reorder": T38,
    "t39_bulk_price": T39,
    "t40_edit_order_items": T40,
    "t41_close_shop": T41,
    "t42_split_order": T42,
    "t43_vendor_onboarding": T43,
    "t44_merge_orders": T44,
}
