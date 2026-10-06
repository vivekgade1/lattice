"""Blind spec deltas for the pivot's held-out tickets t45-t56 (the app owner's own draft).

Written from the ticket texts (``bench.edit_slice.tickets.PIVOT_TICKETS``) and the app's
existing behaviour (``bench/import_repos/multivendor``) only, without the references,
their oracle cases or traps, or another author's deltas. Each `SpecDelta` gives scenarios
on the seed data (expected answers worked out by hand; the arithmetic is in the
comments) and `Unchecked` entries for what the ticket leaves open or Lattice cannot say.

Seed ids are listed in `bench.specs.multivendor`'s docstring. Every scenario starts from a
fresh seed database; state a scenario needs beyond the seed is made through the app's
existing endpoints (POST /orders, PATCH /orders/{id}/status, PATCH /products/{id},
PATCH /vendors/{id}/verify), never with `given` rows.

Conventions:
- ``expect_body`` is a subset match for objects (lists element-wise, same length), so a
  product answer is stated without ``version``: no ticket here says whether a stock
  change bumps it (each such delta lists that as unchecked).
- Where a ticket leaves a status or detail open (an error's precedence, an empty list,
  whether a failing batch writes part of itself), no step asserts it.

Machines: none added. Every status change these tickets make (ship: confirmed ->
shipped; received: shipped -> delivered; cancel: pending/confirmed -> cancelled) is an
edge of the base spec's ORDER_MACHINE, and a second machine for Order.status is a
validate error (S006). Invariants: none added; the base invariants these tickets lean on
(order_total for t49, stock_never_negative for t45/t49/t50/t53) already run after every
request.

Programs: none. Each ticket needs a construct Lattice lacks (each delta's `unchecked`
names it).
"""

from __future__ import annotations

from bench.specs.multivendor import order_read, product_read
from bench.specs.multivendor_tickets import SpecDelta
from lattice.spec import Accepted, Scenario, Step, Unchecked

__all__ = ["BLIND_PIVOT_DELTAS", "CORRECTIONS"]

DENIED = {"detail": "You do not have permission to perform this action."}
NO_ACCESS = {"detail": "You do not have access to this order"}
ORDER_NOT_FOUND = {"detail": "Order not found"}
NO_VENDOR = {"detail": "No vendor profile found for this user"}
NOT_OWNER = {"detail": "You do not own this product"}
PRODUCT_NOT_FOUND = {"detail": "Product not found"}

VICKY_VENDOR = {"id": 1, "user_id": 2, "business_name": "Vicky's Crafts", "is_verified": True}
VICTOR_VENDOR = {"id": 2, "user_id": 3, "business_name": "Victor Tools", "is_verified": True}
VERA_VENDOR = {"id": 3, "user_id": 4, "business_name": "Vera Vintage", "is_verified": False}


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


def _post(path: str, user: str | None, **kw: object) -> Step:
    return Step("POST", path, user, **kw)  # type: ignore[arg-type]


def _stock(pid: int, n: int) -> Step:
    """A public read of product `pid`, checking its stock in the database."""
    return _get(f"/products/{pid}", None, expect_db=_product(pid, stock_quantity=n))


def _prod(pid: int, **changes: object) -> dict:
    """Seed product `pid` as GET /products/{id} shows it, with `changes`, without
    `version` (left open by every ticket that changes stock)."""
    row = product_read(pid, **changes)
    del row["version"]
    return row


def line(item_id: int, product_id: int, quantity: int, price: float) -> dict:
    """An OrderItemRead."""
    return {"id": item_id, "product_id": product_id, "quantity": quantity,
            "price_at_purchase": price}  # fmt: skip


# The seed orders as GET /orders/{order_id} shows them (status changed by the caller).
def O1(status: str = "delivered") -> dict:
    return order_read(1, 5, status, 45.0, "2026-01-05T10:00:00",
                      [line(1, 1, 2, 10.0), line(2, 5, 1, 25.0)])  # fmt: skip


def O3(status: str = "shipped") -> dict:
    return order_read(3, 6, status, 61.0, "2026-02-01T00:00:00",
                      [line(4, 5, 1, 25.0), line(5, 6, 2, 18.0)])  # fmt: skip


def O5(status: str = "confirmed") -> dict:
    return order_read(5, 5, status, 47.0, "2026-02-28T23:59:59",
                      [line(7, 1, 1, 12.0), line(8, 2, 2, 8.5), line(9, 6, 1, 18.0)])  # fmt: skip


def O7(status: str = "pending") -> dict:
    return order_read(7, 6, status, 12.0, "2026-03-10T09:00:00", [line(11, 1, 1, 12.0)])


def O8(status: str = "pending") -> dict:
    return order_read(8, 5, status, 58.5, "2026-03-15T16:45:00",
                      [line(12, 2, 1, 8.5), line(13, 5, 2, 25.0)])  # fmt: skip


def _status(oid: int, user: str, status: str) -> Step:
    """Move order `oid` with the existing PATCH /orders/{id}/status."""
    return Step("PATCH", f"/orders/{oid}/status", user, json={"status": status},
                expect_db=_order(oid, status=status))  # fmt: skip


# ======================================================================================
# t45 POST /products/{product_id}/restock {"quantity": n} -- adds n to the stock
# ======================================================================================
#
# 0 < n <= 1000, else 422. Access and errors as PATCH /products/{id}: the vendor role
# (MANAGE_OWN_PRODUCTS: buyers and the admin get the permission 403), a vendor profile,
# 404 "Product not found", 403 "You do not own this product". Answer: the product.
#   vicky P1 (40): +10 -> 50; +5 more -> 55
#   victor P7 (0, the tape measure): +1000 (the upper bound itself) -> 1000
#   vicky P4 (0, inactive): +1 -> 1 (PATCH /products/{id} works on inactive products,
#     and the ticket names no extra rule)
# Every refused restock leaves the stock as it was.

T45 = SpecDelta(
    scenarios=(
        Scenario(
            "t45_restock_adds_to_stock",
            steps=(
                _post("/products/1/restock", "vicky", json={"quantity": 10},
                      expect_body=_prod(1, stock_quantity=50),
                      expect_db=_product(1, stock_quantity=50)),  # fmt: skip
                _post("/products/1/restock", "vicky", json={"quantity": 5},
                      expect_body=_prod(1, stock_quantity=55),
                      expect_db=_product(1, stock_quantity=55)),  # fmt: skip
                _get("/products/1", None, expect_body=_prod(1, stock_quantity=55)),
            ),
        ),
        Scenario(
            "t45_restock_bounds",
            steps=(
                _post("/products/7/restock", "victor", json={"quantity": 1000},
                      expect_body=_prod(7, stock_quantity=1000),
                      expect_db=_product(7, stock_quantity=1000)),  # fmt: skip
                _post("/products/4/restock", "vicky", json={"quantity": 1},
                      expect_body=_prod(4, stock_quantity=1),
                      expect_db=_product(4, stock_quantity=1)),  # fmt: skip
                _post("/products/1/restock", "vicky", json={"quantity": 0},
                      expect_status=422, expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                _post("/products/1/restock", "vicky", json={"quantity": 1001},
                      expect_status=422, expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                _post("/products/1/restock", "vicky", json={"quantity": -5},
                      expect_status=422, expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                _post("/products/1/restock", "vicky", json={"quantity": 2.5},
                      expect_status=422, expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                _post("/products/1/restock", "vicky", json={},
                      expect_status=422, expect_db=_product(1, stock_quantity=40)),  # fmt: skip
            ),
        ),
        Scenario(
            "t45_restock_access",
            steps=(
                _post("/products/1/restock", "victor", json={"quantity": 10},
                      expect_status=403, expect_body=NOT_OWNER,
                      expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                _post("/products/999/restock", "victor", json={"quantity": 10},
                      expect_status=404, expect_body=PRODUCT_NOT_FOUND),  # fmt: skip
                _post("/products/1/restock", "carol", json={"quantity": 10},
                      expect_status=403, expect_body=DENIED,
                      expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                _post("/products/1/restock", "admin", json={"quantity": 10},
                      expect_status=403, expect_body=DENIED,
                      expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                _post("/products/1/restock", None, json={"quantity": 10},
                      expect_status=401, expect_db=_product(1, stock_quantity=40)),  # fmt: skip
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t45: no program -- the stock grows by the parameter (update() sets a column to "
            "a value; an increment of a column by a parameter is not a Lattice write).",
            ("Product.stock_quantity",),
        ),
        Unchecked(
            "t45: the ticket does not say whether a restock bumps Product.version (the "
            "optimistic-lock counter POST /orders bumps), nor the precedence of a 422 over "
            "the 404/403 of PATCH /products/{id}'s service checks; neither is asserted.",
            ("Product.version",),
        ),
        Unchecked(
            "t45: the vendor-without-a-vendor-profile 403 ('You do not have a vendor "
            "profile', as PATCH) has no seed user to show it.",
            ("Product",),
        ),
    ),
)

# ======================================================================================
# t46 POST /orders/{order_id}/ship -- NESTED (answers OrderRead)
# ======================================================================================
#
# Vendors and the admin (UPDATE_ORDER_STATUS: buyers get the permission 403). A vendor
# needs a product of theirs in the order (else 403 "You have no products in this
# order"); the admin ships any order. Only confirmed -> shipped (else 400 "Only
# confirmed orders can be shipped"); unknown 404 "Order not found".
#   O5 (carol, confirmed): P1, P2 (vicky), P6 (victor) -> vicky, victor or the admin may
#     ship it; vera (no products) may not.
#   O7 (dave, pending): P1 only (vicky). Confirmed by the admin first, victor (no
#     products in it) gets 403, vicky ships it.
#   O8 pending, O1 delivered, O3 shipped, O9 cancelled: 400.
# Shipping moves no stock (as the existing status change).

NO_PRODUCTS = {"detail": "You have no products in this order"}
NOT_CONFIRMED = {"detail": "Only confirmed orders can be shipped"}

T46 = SpecDelta(
    scenarios=(
        Scenario(
            "t46_vendor_ships_confirmed_order",
            steps=(
                _post("/orders/5/ship", "vicky", expect_body=O5("shipped"),
                      expect_db=_db(_order(5, status="shipped"),
                                    _product(1, stock_quantity=40))),  # fmt: skip
                _get("/orders/5", "carol", expect_body=O5("shipped")),
                # shipping twice: it is no longer confirmed
                _post("/orders/5/ship", "vicky", expect_status=400, expect_body=NOT_CONFIRMED,
                      expect_db=_order(5, status="shipped")),  # fmt: skip
            ),
        ),
        Scenario(
            "t46_other_vendor_with_products_ships",
            steps=(
                _post("/orders/5/ship", "victor", expect_body=O5("shipped"),
                      expect_db=_order(5, status="shipped")),  # fmt: skip
            ),
        ),
        Scenario(
            "t46_admin_ships_any_order",
            steps=(
                _post("/orders/5/ship", "admin", expect_body=O5("shipped"),
                      expect_db=_order(5, status="shipped")),  # fmt: skip
            ),
        ),
        Scenario(
            "t46_vendor_without_products_in_order",
            steps=(
                _post("/orders/5/ship", "vera", expect_status=403, expect_body=NO_PRODUCTS,
                      expect_db=_order(5, status="confirmed")),  # fmt: skip
                _status(7, "admin", "confirmed"),
                _post("/orders/7/ship", "victor", expect_status=403, expect_body=NO_PRODUCTS,
                      expect_db=_order(7, status="confirmed")),  # fmt: skip
                _post("/orders/7/ship", "vicky", expect_body=O7("shipped"),
                      expect_db=_order(7, status="shipped")),  # fmt: skip
            ),
        ),
        Scenario(
            "t46_only_confirmed_orders_ship",
            steps=(
                _post("/orders/8/ship", "vicky", expect_status=400, expect_body=NOT_CONFIRMED,
                      expect_db=_order(8, status="pending")),  # fmt: skip
                _post("/orders/1/ship", "admin", expect_status=400, expect_body=NOT_CONFIRMED,
                      expect_db=_order(1, status="delivered")),  # fmt: skip
                _post("/orders/3/ship", "victor", expect_status=400, expect_body=NOT_CONFIRMED,
                      expect_db=_order(3, status="shipped")),  # fmt: skip
                _post("/orders/9/ship", "admin", expect_status=400, expect_body=NOT_CONFIRMED,
                      expect_db=_order(9, status="cancelled")),  # fmt: skip
            ),
        ),
        Scenario(
            "t46_ship_access",
            steps=(
                _post("/orders/999/ship", "admin", expect_status=404,
                      expect_body=ORDER_NOT_FOUND),  # fmt: skip
                _post("/orders/999/ship", "victor", expect_status=404,
                      expect_body=ORDER_NOT_FOUND),  # fmt: skip
                _post("/orders/5/ship", "carol", expect_status=403, expect_body=DENIED,
                      expect_db=_order(5, status="confirmed")),  # fmt: skip
                _post("/orders/5/ship", None, expect_status=401,
                      expect_db=_order(5, status="confirmed")),  # fmt: skip
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t46: no program -- a vendor's right to ship depends on the order's lines' "
            "products (OrderItem -> Product -> Vendor), and Order is owned by its buyer: "
            "only an Admin-only endpoint may write rows the caller does not own (L802).",
            ("Order.status", "OrderItem"),
        ),
        Unchecked(
            "t46: the ticket does not fix which comes first for a vendor with no products "
            "in an order that is not confirmed (403 or 400); not asserted.",
            ("Order.status",),
        ),
    ),
)

# ======================================================================================
# t47 POST /orders/quote -- price a basket, write nothing
# ======================================================================================
#
# Anyone signed in (a buyer, a vendor, the admin). Body as POST /orders (items: at least
# one, quantity > 0, else 422). unit_price is the product's current price.
#   carol: P1 x2 @12.0 = 24.0, P5 x1 @25.0 = 25.0 -> items 3, total 49.0
#   P2 x1, P1 x1, P2 x2: one line per product, in first-appearance order:
#     P2 x3 @8.5 = 25.5, P1 x1 @12.0 = 12.0 -> items 4, total 37.5
#   P5 x15: exactly its stock (15) -> 375.0
#   P5 x10 + P5 x10: one line of 20 > 15 in stock -> 409
# Errors: P999 -> 404 "Product 999 not found"; P1 made inactive by vicky -> 400
# "Product 1 is not available" (P4 is inactive *and* out of stock, so its error would
# depend on the order of checks, which the ticket leaves open); P7 (stock 0) x1 -> 409
# "Insufficient stock for product 7". Nothing is ever written: 9 orders, 14 lines.

NOTHING_ORDERED = {"orders": {"count": 9}, "order_items": {"count": 14}}


def _ql(pid: int, name: str, qty: int, price: float, total: float) -> dict:
    return {"product_id": pid, "name": name, "quantity": qty, "unit_price": price,
            "line_total": total}  # fmt: skip


def _items(*pairs: tuple[int, int]) -> dict:
    return {"items": [{"product_id": p, "quantity": q} for p, q in pairs]}


T47 = SpecDelta(
    scenarios=(
        Scenario(
            "t47_quote_a_basket",
            steps=(
                _post("/orders/quote", "carol", json=_items((1, 2), (5, 1)),
                      expect_body={"lines": [_ql(1, "Ceramic mug", 2, 12.0, 24.0),
                                             _ql(5, "Claw hammer", 1, 25.0, 25.0)],
                                   "items": 3, "total": 49.0},
                      expect_db=NOTHING_ORDERED),  # fmt: skip
                _stock(1, 40),
                _stock(5, 15),
            ),
        ),
        Scenario(
            "t47_quote_one_line_per_product",
            steps=(
                _post("/orders/quote", "dave", json=_items((2, 1), (1, 1), (2, 2)),
                      expect_body={"lines": [_ql(2, "Soy candle", 3, 8.5, 25.5),
                                             _ql(1, "Ceramic mug", 1, 12.0, 12.0)],
                                   "items": 4, "total": 37.5},
                      expect_db=NOTHING_ORDERED),  # fmt: skip
                _post("/orders/quote", "dave", json=_items((5, 15)),
                      expect_body={"lines": [_ql(5, "Claw hammer", 15, 25.0, 375.0)],
                                   "items": 15, "total": 375.0}),  # fmt: skip
                _post("/orders/quote", "dave", json=_items((5, 10), (5, 10)),
                      expect_status=409,
                      expect_body={"detail": "Insufficient stock for product 5"},
                      expect_db=NOTHING_ORDERED),  # fmt: skip
            ),
        ),
        Scenario(
            "t47_quote_for_anyone_signed_in",
            steps=(
                _post("/orders/quote", "vicky", json=_items((6, 1)),
                      expect_body={"lines": [_ql(6, "Screwdriver set", 1, 18.0, 18.0)],
                                   "items": 1, "total": 18.0}),  # fmt: skip
                _post("/orders/quote", "admin", json=_items((8, 4)),
                      expect_body={"lines": [_ql(8, "Work gloves", 4, 6.0, 24.0)],
                                   "items": 4, "total": 24.0},
                      expect_db=NOTHING_ORDERED),  # fmt: skip
                _post("/orders/quote", None, json=_items((8, 4)), expect_status=401),
            ),
        ),
        Scenario(
            "t47_quote_errors",
            steps=(
                _post("/orders/quote", "carol", json=_items((999, 1)), expect_status=404,
                      expect_body={"detail": "Product 999 not found"},
                      expect_db=NOTHING_ORDERED),  # fmt: skip
                _post("/orders/quote", "carol", json=_items((7, 1)), expect_status=409,
                      expect_body={"detail": "Insufficient stock for product 7"},
                      expect_db=NOTHING_ORDERED),  # fmt: skip
                _post("/orders/quote", "carol", json=_items((1, 41)), expect_status=409,
                      expect_body={"detail": "Insufficient stock for product 1"}),  # fmt: skip
                _post("/orders/quote", "carol", json=_items((1, 0)), expect_status=422),
                _post("/orders/quote", "carol", json={"items": []}, expect_status=422),
                Step("PATCH", "/products/1", "vicky", json={"is_active": False},
                     expect_db=_product(1, is_active=False)),  # fmt: skip
                _post("/orders/quote", "carol", json=_items((1, 1)), expect_status=400,
                      expect_body={"detail": "Product 1 is not available"},
                      expect_db=NOTHING_ORDERED),  # fmt: skip
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t47: no program -- the body is a list of lines (parameters are scalars) and "
            "the error details name the product (error details are literals, L404).",
            ("Product", "Order"),
        ),
        Unchecked(
            "t47: the ticket does not fix which error wins when a basket has several "
            "faulty lines, or one product is both inactive and short of stock; not asserted.",
            ("Product.is_active", "Product.stock_quantity"),
        ),
    ),
)

# ======================================================================================
# t48 GET /vendors/me/dashboard
# ======================================================================================
#
# vicky (vendor 1, P1-P4): total 4, active 3 (P4 inactive), out_of_stock 0 (P4 has 0
#   but is inactive).
#   Orders with her products: O1 (P1) delivered, O2 (P3) delivered, O4 (P2) cancelled,
#   O5 (P1, P2) confirmed, O6 (P3) delivered, O7 (P1) pending, O8 (P2) pending
#   -> pending 2 (O7, O8), confirmed 1, shipped 0, delivered 3, cancelled 1.
#   Units sold (O4 cancelled excluded): P1 2 (O1) + 1 (O5) + 1 (O7) = 4;
#   P2 2 (O5) + 1 (O8) = 3; P3 4 (O2) + 10 (O6) = 14 -> 21; best P3 with 14.
# victor (vendor 2, P5-P8): total 4, active 4, out_of_stock 1 (P7).
#   Orders: O1 (P5) delivered, O3 (P5, P6) shipped, O5 (P6) confirmed, O8 (P5) pending,
#   O9 (P6) cancelled -> 1 of each status.
#   Units (O9 excluded): P5 1 + 1 + 2 = 4; P6 2 + 1 = 3 -> 7; best P5 with 4.
# vera (vendor 3, no products): zeros, best_seller null.
# Tie: dave orders 10 mugs (P1 40 -> 30): P1 4 + 10 = 14 = P3 14 -> the lower id, P1;
#   units 31, pending 3 (O7, O8, the new O10); P1 stock 40 - 10 = 30 (active, not out).
# Cancelling O8 (admin): P2 3 - 1 = 2; units 21 - 1 = 20; pending 1, cancelled 2.


def _dash(vendor: dict, products: tuple[int, int, int], orders: tuple[int, ...], units: int,
          best: dict | None) -> dict:  # fmt: skip
    total, active, out = products
    return {
        "vendor": vendor,
        "products": {"total": total, "active": active, "out_of_stock": out},
        "orders": dict(zip(("pending", "confirmed", "shipped", "delivered", "cancelled"),
                           orders, strict=True)),  # fmt: skip
        "units_sold": units,
        "best_seller": best,
    }


T48 = SpecDelta(
    scenarios=(
        Scenario(
            "t48_dashboard",
            steps=(
                _get("/vendors/me/dashboard", "vicky",
                     expect_body=_dash(VICKY_VENDOR, (4, 3, 0), (2, 1, 0, 3, 1), 21,
                                       {"product_id": 3, "name": "Dot-grid notebook",
                                        "units": 14})),  # fmt: skip
                _get("/vendors/me/dashboard", "victor",
                     expect_body=_dash(VICTOR_VENDOR, (4, 4, 1), (1, 1, 1, 1, 1), 7,
                                       {"product_id": 5, "name": "Claw hammer",
                                        "units": 4})),  # fmt: skip
                _get("/vendors/me/dashboard", "vera",
                     expect_body=_dash(VERA_VENDOR, (0, 0, 0), (0, 0, 0, 0, 0), 0, None)),
            ),
        ),
        Scenario(
            "t48_dashboard_best_seller_tie",
            steps=(
                _post("/orders", "dave", json=_items((1, 10)), expect_status=201),
                _get("/vendors/me/dashboard", "vicky",
                     expect_body=_dash(VICKY_VENDOR, (4, 3, 0), (3, 1, 0, 3, 1), 31,
                                       {"product_id": 1, "name": "Ceramic mug",
                                        "units": 14})),  # fmt: skip
            ),
        ),
        Scenario(
            "t48_dashboard_cancelled_units_not_sold",
            steps=(
                _status(8, "admin", "cancelled"),
                _get("/vendors/me/dashboard", "vicky",
                     expect_body=_dash(VICKY_VENDOR, (4, 3, 0), (1, 1, 0, 3, 2), 20,
                                       {"product_id": 3, "name": "Dot-grid notebook",
                                        "units": 14})),  # fmt: skip
            ),
        ),
        Scenario(
            "t48_dashboard_access",
            steps=(
                _get("/vendors/me/dashboard", "carol", expect_status=404, expect_body=NO_VENDOR),
                _get("/vendors/me/dashboard", "admin", expect_status=404, expect_body=NO_VENDOR),
                _get("/vendors/me/dashboard", None, expect_status=401),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t48: no program -- the dashboard aggregates other buyers' order lines "
            "(OrderItem is owned through Order by its buyer), and the answer nests objects "
            "that are not rows of one table.",
            ("OrderItem", "Order.status", "Product.is_active"),
        ),
        Unchecked(
            "t48: units in pending, confirmed, shipped and delivered orders all count as "
            "sold (the ticket excludes only cancelled ones); this reading is asserted.",
            ("OrderItem.quantity",),
        ),
    ),
)

# ======================================================================================
# t49 DELETE /orders/{order_id}/items/{item_id} -- NESTED (answers OrderRead)
# ======================================================================================
#
# The buyer only (anyone else, the admin included: 403 "You do not have access to this
# order"); unknown order 404 "Order not found"; only pending (400 "Only pending orders
# can be changed"); a line of another order or none: 404 "Item not found"; the only line:
# 400 "Cannot remove the last item".
#   O8 (carol, pending, 58.5): item 12 P2 x1 @8.5, item 13 P5 x2 @25.0.
#     remove 13: total 58.5 - 50.0 = 8.5; P5 15 + 2 = 17; 13 lines left.
#     then remove 12 (now the only line): 400, nothing changes.
#   or remove 12 first: total 58.5 - 8.5 = 50.0; P2 30 + 1 = 31.
#   O7 (dave, pending): only item 11 -> 400 "Cannot remove the last item".
#   O5 (carol, confirmed), O1 (carol, delivered): 400 "Only pending orders ...".

LAST_ITEM = {"detail": "Cannot remove the last item"}
NOT_PENDING = {"detail": "Only pending orders can be changed"}
ITEM_NOT_FOUND = {"detail": "Item not found"}

T49 = SpecDelta(
    scenarios=(
        Scenario(
            "t49_remove_a_line",
            steps=(
                Step("DELETE", "/orders/8/items/13", "carol",
                     expect_body=order_read(8, 5, "pending", 8.5, "2026-03-15T16:45:00",
                                            [line(12, 2, 1, 8.5)]),
                     expect_db=_db(_order(8, total_amount=8.5, status="pending"),
                                   _product(5, stock_quantity=17),
                                   {"order_items": {"count": 13}})),  # fmt: skip
                _get("/orders/8", "carol",
                     expect_body=order_read(8, 5, "pending", 8.5, "2026-03-15T16:45:00",
                                            [line(12, 2, 1, 8.5)])),  # fmt: skip
                # now item 12 is the order's only line
                Step("DELETE", "/orders/8/items/12", "carol", expect_status=400,
                     expect_body=LAST_ITEM,
                     expect_db=_db(_order(8, total_amount=8.5),
                                   _product(2, stock_quantity=30),
                                   {"order_items": {"count": 13}})),  # fmt: skip
            ),
        ),
        Scenario(
            "t49_remove_the_other_line",
            steps=(
                Step("DELETE", "/orders/8/items/12", "carol",
                     expect_body=order_read(8, 5, "pending", 50.0, "2026-03-15T16:45:00",
                                            [line(13, 5, 2, 25.0)]),
                     expect_db=_db(_order(8, total_amount=50.0),
                                   _product(2, stock_quantity=31),
                                   {"order_items": {"where": {"order_id": 8}, "count": 1}})),
            ),
        ),
        Scenario(
            "t49_remove_refused",
            steps=(
                Step("DELETE", "/orders/7/items/11", "dave", expect_status=400,
                     expect_body=LAST_ITEM,
                     expect_db=_db(_order(7, total_amount=12.0),
                                   _product(1, stock_quantity=40),
                                   {"order_items": {"count": 14}})),  # fmt: skip
                Step("DELETE", "/orders/5/items/7", "carol", expect_status=400,
                     expect_body=NOT_PENDING,
                     expect_db=_db(_order(5, total_amount=47.0),
                                   _product(1, stock_quantity=40),
                                   {"order_items": {"count": 14}})),  # fmt: skip
                Step("DELETE", "/orders/1/items/1", "carol", expect_status=400,
                     expect_body=NOT_PENDING,
                     expect_db={"order_items": {"count": 14}}),  # fmt: skip
                # item 1 is a line of O1, not of O8
                Step("DELETE", "/orders/8/items/1", "carol", expect_status=404,
                     expect_body=ITEM_NOT_FOUND,
                     expect_db=_db(_order(8, total_amount=58.5),
                                   _product(1, stock_quantity=40),
                                   {"order_items": {"count": 14}})),  # fmt: skip
                Step("DELETE", "/orders/8/items/999", "carol", expect_status=404,
                     expect_body=ITEM_NOT_FOUND,
                     expect_db={"order_items": {"count": 14}}),  # fmt: skip
            ),
        ),
        Scenario(
            "t49_remove_access",
            steps=(
                Step("DELETE", "/orders/8/items/13", "dave", expect_status=403,
                     expect_body=NO_ACCESS,
                     expect_db=_db(_order(8, total_amount=58.5),
                                   _product(5, stock_quantity=15),
                                   {"order_items": {"count": 14}})),  # fmt: skip
                Step("DELETE", "/orders/8/items/13", "admin", expect_status=403,
                     expect_body=NO_ACCESS,
                     expect_db=_db(_order(8, total_amount=58.5),
                                   {"order_items": {"count": 14}})),  # fmt: skip
                Step("DELETE", "/orders/8/items/13", "victor", expect_status=403,
                     expect_body=NO_ACCESS,
                     expect_db={"order_items": {"count": 14}}),  # fmt: skip
                Step("DELETE", "/orders/999/items/1", "carol", expect_status=404,
                     expect_body=ORDER_NOT_FOUND),  # fmt: skip
                Step("DELETE", "/orders/8/items/13", None, expect_status=401,
                     expect_db={"order_items": {"count": 14}}),  # fmt: skip
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t49: no program -- removing a line restocks its product by the line's "
            "quantity and recomputes the order's total from the remaining lines (a write "
            "of one table derived from another: L841); the base invariant order_total "
            "checks the total after every request.",
            ("Order.total_amount", "OrderItem", "Product.stock_quantity"),
        ),
        Unchecked(
            "t49: the precedence of the 403 / 400 / 404 'Item not found' / 400 'last item' "
            "checks when several apply is not fixed by the ticket (beyond 'unknown order "
            "is 404'), nor whether a removal bumps Product.version; not asserted.",
            ("OrderItem", "Product.version"),
        ),
    ),
)

# ======================================================================================
# t50 POST /orders/cancel-batch {"order_ids": [...]} -- admins only
# ======================================================================================
#
# Admin (VIEW_ALL_ORDERS: buyers and vendors get the permission 403). Each order is
# cancelled as PATCH status would (pending or confirmed -> cancelled) and restocked.
#   [7, 8, 5]: O7 P1 x1; O8 P2 x1, P5 x2; O5 P1 x1, P2 x2, P6 x1
#     -> cancelled 3; P1 40 + 1 + 1 = 42, P2 30 + 1 + 2 = 33, P5 15 + 2 = 17,
#        P6 20 + 1 = 21; P3, P8 untouched.
#   [1] delivered, [3] shipped, [9] already cancelled: 400 "Order <id> cannot be
#     cancelled" -- a one-order batch that fails writes nothing (O9 is not restocked:
#     P6 stays 20).
#   [999]: 404 "Order 999 not found".
#   [7, 1] and [7, 999]: the batch fails with O1's 400 / O999's 404 (whether O7 was
#     cancelled before is left open by the ticket).

T50 = SpecDelta(
    scenarios=(
        Scenario(
            "t50_cancel_batch",
            steps=(
                _post("/orders/cancel-batch", "admin", json={"order_ids": [7, 8, 5]},
                      expect_body={"cancelled": 3},
                      expect_db=_db(_product(1, stock_quantity=42),
                                    {"orders": {"where": {"status": "cancelled"},
                                                "count": 5}})),  # fmt: skip
                _get("/orders/7", "admin", expect_body=O7("cancelled")),
                _get("/orders/8", "admin", expect_body=O8("cancelled")),
                _get("/orders/5", "admin", expect_body=O5("cancelled")),
                _stock(2, 33),
                _stock(5, 17),
                _stock(6, 21),
                _stock(3, 100),
                _stock(8, 50),
            ),
        ),
        Scenario(
            "t50_cancel_batch_of_one",
            steps=(
                _post("/orders/cancel-batch", "admin", json={"order_ids": [7]},
                      expect_body={"cancelled": 1},
                      expect_db=_db(_order(7, status="cancelled"),
                                    _product(1, stock_quantity=41))),  # fmt: skip
                # cancelled -> cancelled is not an edge: no second restock
                _post("/orders/cancel-batch", "admin", json={"order_ids": [7]},
                      expect_status=400, expect_body={"detail": "Order 7 cannot be cancelled"},
                      expect_db=_db(_order(7, status="cancelled"),
                                    _product(1, stock_quantity=41))),  # fmt: skip
            ),
        ),
        Scenario(
            "t50_cancel_batch_refused",
            steps=(
                _post("/orders/cancel-batch", "admin", json={"order_ids": [1]},
                      expect_status=400, expect_body={"detail": "Order 1 cannot be cancelled"},
                      expect_db=_db(_order(1, status="delivered"),
                                    _product(1, stock_quantity=40))),  # fmt: skip
                _post("/orders/cancel-batch", "admin", json={"order_ids": [3]},
                      expect_status=400, expect_body={"detail": "Order 3 cannot be cancelled"},
                      expect_db=_db(_order(3, status="shipped"),
                                    _product(5, stock_quantity=15))),  # fmt: skip
                _post("/orders/cancel-batch", "admin", json={"order_ids": [9]},
                      expect_status=400, expect_body={"detail": "Order 9 cannot be cancelled"},
                      expect_db=_product(6, stock_quantity=20)),  # fmt: skip
                _post("/orders/cancel-batch", "admin", json={"order_ids": [999]},
                      expect_status=404, expect_body={"detail": "Order 999 not found"},
                      expect_db={"orders": {"where": {"status": "cancelled"}, "count": 2}}),
                _post("/orders/cancel-batch", "admin", json={"order_ids": [7, 1]},
                      expect_status=400, expect_body={"detail": "Order 1 cannot be cancelled"},
                      expect_db=_order(1, status="delivered")),  # fmt: skip
            ),
        ),
        Scenario(
            "t50_cancel_batch_unknown_in_a_list",
            steps=(
                _post("/orders/cancel-batch", "admin", json={"order_ids": [7, 999]},
                      expect_status=404, expect_body={"detail": "Order 999 not found"}),
            ),
        ),
        Scenario(
            "t50_cancel_batch_admins_only",
            steps=(
                _post("/orders/cancel-batch", "carol", json={"order_ids": [8]},
                      expect_status=403, expect_body=DENIED,
                      expect_db=_db(_order(8, status="pending"),
                                    _product(2, stock_quantity=30))),  # fmt: skip
                _post("/orders/cancel-batch", "vicky", json={"order_ids": [8]},
                      expect_status=403, expect_body=DENIED,
                      expect_db=_order(8, status="pending")),  # fmt: skip
                _post("/orders/cancel-batch", None, json={"order_ids": [8]},
                      expect_status=401, expect_db=_order(8, status="pending")),  # fmt: skip
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t50: no program -- the body is a list of ids (parameters are scalars), each "
            "order restocks its lines' products (L841), and the details name the order "
            "(error details are literals, L404).",
            ("Order.status", "OrderItem", "Product.stock_quantity"),
        ),
        Unchecked(
            "t50: the ticket does not say whether a batch with one bad order cancels the "
            "others (all-or-nothing or not), what an empty list or a repeated id answers, "
            "which error wins when several orders fail, or whether a restock bumps "
            "Product.version; none is asserted.",
            ("Order.status", "Product.version"),
        ),
    ),
)

# ======================================================================================
# t51 GET /vendors/me/low-stock?threshold=n (1..1000, default 10)
# ======================================================================================
#
# The vendor's active products with stock < threshold, stock ascending, ties by id.
# vicky: P1 40, P2 30, P3 100 (P4 inactive, stock 0: never listed)
#   default 10 -> []; 35 -> [P2 30]; 41 -> [P2 30, P1 40]; 1000 -> [P2, P1, P3]
# victor: P5 15, P6 20, P7 0, P8 50
#   default 10 -> [P7 0]; 1 -> [P7]; 20 -> [P7, P5] (P6 20 is not < 20);
#   21 -> [P7, P5, P6]
#   tie: dave orders 30 gloves (P8 50 -> 20 = P6 20); 21 -> [P7 0, P5 15, P6 20, P8 20]
# vera: []. Not a vendor: 404 as GET /vendors/me.


def _ls(pid: int, name: str, stock: int) -> dict:
    return {"product_id": pid, "name": name, "stock_quantity": stock}


P1_LS, P2_LS, P3_LS = _ls(1, "Ceramic mug", 40), _ls(2, "Soy candle", 30), _ls(
    3, "Dot-grid notebook", 100
)
P5_LS, P6_LS, P7_LS = _ls(5, "Claw hammer", 15), _ls(6, "Screwdriver set", 20), _ls(
    7, "Tape measure", 0
)

T51 = SpecDelta(
    scenarios=(
        Scenario(
            "t51_low_stock",
            steps=(
                _get("/vendors/me/low-stock", "vicky", expect_body=[]),
                _get("/vendors/me/low-stock", "vicky", params={"threshold": 35},
                     expect_body=[P2_LS]),  # fmt: skip
                _get("/vendors/me/low-stock", "vicky", params={"threshold": 41},
                     expect_body=[P2_LS, P1_LS]),  # fmt: skip
                _get("/vendors/me/low-stock", "vicky", params={"threshold": 1000},
                     expect_body=[P2_LS, P1_LS, P3_LS]),  # fmt: skip
                _get("/vendors/me/low-stock", "victor", expect_body=[P7_LS]),
                _get("/vendors/me/low-stock", "victor", params={"threshold": 1},
                     expect_body=[P7_LS]),  # fmt: skip
                _get("/vendors/me/low-stock", "victor", params={"threshold": 20},
                     expect_body=[P7_LS, P5_LS]),  # fmt: skip
                _get("/vendors/me/low-stock", "victor", params={"threshold": 21},
                     expect_body=[P7_LS, P5_LS, P6_LS]),  # fmt: skip
                _get("/vendors/me/low-stock", "vera", expect_body=[]),
            ),
        ),
        Scenario(
            "t51_low_stock_ties_by_id",
            steps=(
                _post("/orders", "dave", json=_items((8, 30)), expect_status=201,
                      expect_db=_product(8, stock_quantity=20)),  # fmt: skip
                _get("/vendors/me/low-stock", "victor", params={"threshold": 21},
                     expect_body=[P7_LS, P5_LS, P6_LS, _ls(8, "Work gloves", 20)]),  # fmt: skip
            ),
        ),
        Scenario(
            "t51_low_stock_threshold_bounds",
            steps=(
                _get("/vendors/me/low-stock", "victor", params={"threshold": 0},
                     expect_status=422),  # fmt: skip
                _get("/vendors/me/low-stock", "victor", params={"threshold": 1001},
                     expect_status=422),  # fmt: skip
                _get("/vendors/me/low-stock", "victor", params={"threshold": -1},
                     expect_status=422),  # fmt: skip
                _get("/vendors/me/low-stock", "victor", params={"threshold": "ten"},
                     expect_status=422),  # fmt: skip
                _get("/vendors/me/low-stock", "victor", params={"threshold": "2.5"},
                     expect_status=422),  # fmt: skip
            ),
        ),
        Scenario(
            "t51_low_stock_access",
            steps=(
                _get("/vendors/me/low-stock", "carol", expect_status=404, expect_body=NO_VENDOR),
                _get("/vendors/me/low-stock", "admin", expect_status=404, expect_body=NO_VENDOR),
                _get("/vendors/me/low-stock", None, expect_status=401),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t51: no program -- Product's ownership through Vendor is not declared in the "
            "profile (the catalogue is public, see the base spec), so 'the caller's "
            "products' is a join through the caller's vendor row; the scenarios state it.",
            ("Product.vendor_id", "Product.stock_quantity"),
        ),
    ),
)

# ======================================================================================
# t52 GET /orders/{order_id}/by-vendor -- access as GET /orders/{order_id}
# ======================================================================================
#
# O5 (carol, confirmed, 47.0): item 7 P1 x1 @12.0, item 8 P2 x2 @8.5 (vicky, vendor 1:
#   12 + 17 = 29.0); item 9 P6 x1 @18.0 (victor, vendor 2: 18.0).
# O1 (carol, delivered, 45.0): item 1 P1 x2 @10.0 (the price paid, not today's 12.00:
#   vicky 20.0); item 2 P5 x1 @25.0 (victor 25.0).
# O3 (dave, shipped, 61.0): items 4 P5 x1 @25, 5 P6 x2 @18 -> victor only, 25 + 36 = 61.0.
# O8 (carol, pending, 58.5): item 12 P2 x1 @8.5 (vicky 8.5), item 13 P5 x2 @25 (victor 50.0).


def _bl(item: int, pid: int, name: str, qty: int, price: float) -> dict:
    return {"item_id": item, "product_id": pid, "name": name, "quantity": qty, "price": price}


def _bv(vid: int, name: str, subtotal: float, lines: list[dict]) -> dict:
    return {"vendor_id": vid, "business_name": name, "subtotal": subtotal, "lines": lines}


BY_VENDOR_5 = {
    "order_id": 5, "status": "confirmed", "total": 47.0,
    "vendors": [
        _bv(1, "Vicky's Crafts", 29.0, [_bl(7, 1, "Ceramic mug", 1, 12.0),
                                        _bl(8, 2, "Soy candle", 2, 8.5)]),
        _bv(2, "Victor Tools", 18.0, [_bl(9, 6, "Screwdriver set", 1, 18.0)]),
    ],
}  # fmt: skip
BY_VENDOR_1 = {
    "order_id": 1, "status": "delivered", "total": 45.0,
    "vendors": [
        _bv(1, "Vicky's Crafts", 20.0, [_bl(1, 1, "Ceramic mug", 2, 10.0)]),
        _bv(2, "Victor Tools", 25.0, [_bl(2, 5, "Claw hammer", 1, 25.0)]),
    ],
}  # fmt: skip
BY_VENDOR_3 = {
    "order_id": 3, "status": "shipped", "total": 61.0,
    "vendors": [
        _bv(2, "Victor Tools", 61.0, [_bl(4, 5, "Claw hammer", 1, 25.0),
                                      _bl(5, 6, "Screwdriver set", 2, 18.0)]),
    ],
}  # fmt: skip
BY_VENDOR_8 = {
    "order_id": 8, "status": "pending", "total": 58.5,
    "vendors": [
        _bv(1, "Vicky's Crafts", 8.5, [_bl(12, 2, "Soy candle", 1, 8.5)]),
        _bv(2, "Victor Tools", 50.0, [_bl(13, 5, "Claw hammer", 2, 25.0)]),
    ],
}  # fmt: skip

T52 = SpecDelta(
    scenarios=(
        Scenario(
            "t52_order_by_vendor",
            steps=(
                _get("/orders/5/by-vendor", "carol", expect_body=BY_VENDOR_5),
                _get("/orders/1/by-vendor", "carol", expect_body=BY_VENDOR_1),
                _get("/orders/8/by-vendor", "carol", expect_body=BY_VENDOR_8),
                _get("/orders/3/by-vendor", "dave", expect_body=BY_VENDOR_3),
                # the admin reads any order
                _get("/orders/3/by-vendor", "admin", expect_body=BY_VENDOR_3),
            ),
        ),
        Scenario(
            "t52_order_by_vendor_access",
            steps=(
                _get("/orders/5/by-vendor", "dave", expect_status=403, expect_body=NO_ACCESS),
                _get("/orders/5/by-vendor", "vicky", expect_status=403, expect_body=NO_ACCESS),
                _get("/orders/999/by-vendor", "dave", expect_status=404,
                     expect_body=ORDER_NOT_FOUND),  # fmt: skip
                _get("/orders/999/by-vendor", "admin", expect_status=404,
                     expect_body=ORDER_NOT_FOUND),  # fmt: skip
                _get("/orders/5/by-vendor", None, expect_status=401),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t52: no program -- the answer nests vendors and, in each, lines (a two-level "
            "nested response grouped through OrderItem -> Product -> Vendor), which "
            "Lattice's one-level Nested cannot say.",
            ("Order", "OrderItem", "Vendor"),
        ),
    ),
)

# ======================================================================================
# t53 POST /products/{product_id}/move-stock {"to_product_id": id, "quantity": n}
# ======================================================================================
#
# Both products the caller's own, access and errors as PATCH /products/{id} (vendor role,
# 404 "Product not found", 403 "You do not own this product") whichever fails; n > 0
# (else 422) and n <= the source's stock (else 409 "Not enough stock"); same product:
# 400 "Cannot move stock to the same product".
#   vicky P1 (40) -> P2 (30), 10: P1 30, P2 40.
#   vicky P3 (100) -> P1 (40), 100 (all of it): P3 0, P1 140.
#   victor P5 (15) -> P8 (50), 15 (all of it): P5 0, P8 65.
#   P1 -> P2, 41 > 40: 409. Every refused move leaves both stocks as they were.

T53 = SpecDelta(
    scenarios=(
        Scenario(
            "t53_move_stock",
            steps=(
                _post("/products/1/move-stock", "vicky", json={"to_product_id": 2, "quantity": 10},
                      expect_body={"from": _prod(1, stock_quantity=30),
                                   "to": _prod(2, stock_quantity=40)},
                      expect_db=_product(1, stock_quantity=30)),  # fmt: skip
                _stock(2, 40),
                _get("/products/1", None, expect_body=_prod(1, stock_quantity=30)),
            ),
        ),
        Scenario(
            "t53_move_all_stock",
            steps=(
                _post("/products/3/move-stock", "vicky", json={"to_product_id": 1, "quantity": 100},
                      expect_body={"from": _prod(3, stock_quantity=0),
                                   "to": _prod(1, stock_quantity=140)},
                      expect_db=_product(3, stock_quantity=0)),  # fmt: skip
                _stock(1, 140),
                _post("/products/5/move-stock", "victor", json={"to_product_id": 8, "quantity": 15},
                      expect_body={"from": _prod(5, stock_quantity=0),
                                   "to": _prod(8, stock_quantity=65)},
                      expect_db=_product(5, stock_quantity=0)),  # fmt: skip
                _stock(8, 65),
            ),
        ),
        Scenario(
            "t53_move_stock_refused",
            steps=(
                _post("/products/1/move-stock", "vicky", json={"to_product_id": 2, "quantity": 41},
                      expect_status=409, expect_body={"detail": "Not enough stock"},
                      expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                _stock(2, 30),
                _post("/products/7/move-stock", "victor", json={"to_product_id": 5, "quantity": 1},
                      expect_status=409, expect_body={"detail": "Not enough stock"},
                      expect_db=_product(5, stock_quantity=15)),  # fmt: skip
                _post("/products/1/move-stock", "vicky", json={"to_product_id": 1, "quantity": 5},
                      expect_status=400,
                      expect_body={"detail": "Cannot move stock to the same product"},
                      expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                _post("/products/1/move-stock", "vicky", json={"to_product_id": 2, "quantity": 0},
                      expect_status=422, expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                _post("/products/1/move-stock", "vicky", json={"to_product_id": 2, "quantity": -3},
                      expect_status=422, expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                _stock(2, 30),
            ),
        ),
        Scenario(
            "t53_move_stock_access",
            steps=(
                # the source is not theirs
                _post("/products/5/move-stock", "vicky", json={"to_product_id": 1, "quantity": 1},
                      expect_status=403, expect_body=NOT_OWNER,
                      expect_db=_product(5, stock_quantity=15)),  # fmt: skip
                # the target is not theirs
                _post("/products/1/move-stock", "vicky", json={"to_product_id": 5, "quantity": 1},
                      expect_status=403, expect_body=NOT_OWNER,
                      expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                _stock(5, 15),
                # unknown source / unknown target
                _post("/products/999/move-stock", "vicky",
                      json={"to_product_id": 1, "quantity": 1},
                      expect_status=404, expect_body=PRODUCT_NOT_FOUND,
                      expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                _post("/products/1/move-stock", "vicky",
                      json={"to_product_id": 999, "quantity": 1},
                      expect_status=404, expect_body=PRODUCT_NOT_FOUND,
                      expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                # not a vendor (the permission 403 of PATCH /products/{id})
                _post("/products/1/move-stock", "carol", json={"to_product_id": 2, "quantity": 1},
                      expect_status=403, expect_body=DENIED,
                      expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                _post("/products/1/move-stock", "admin", json={"to_product_id": 2, "quantity": 1},
                      expect_status=403, expect_body=DENIED,
                      expect_db=_product(1, stock_quantity=40)),  # fmt: skip
                _post("/products/1/move-stock", None, json={"to_product_id": 2, "quantity": 1},
                      expect_status=401, expect_db=_product(1, stock_quantity=40)),  # fmt: skip
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t53: no program -- two rows change by a parameter (a decrement and an "
            "increment; Lattice's update() sets values), and 'stock is conserved by a "
            "move' is a before/after rule, not an invariant form.",
            ("Product.stock_quantity",),
        ),
        Unchecked(
            "t53: the ticket does not fix the precedence among the 403/404, 400 "
            "same-product, 409 and 422 answers when several apply, whether moving to an "
            "inactive listing is allowed, or whether a move bumps Product.version; none "
            "is asserted.",
            ("Product.version", "Product.is_active"),
        ),
    ),
)

# ======================================================================================
# t54 GET /users/me/order-stats -- anyone signed in, their own orders, cancelled excluded
# ======================================================================================
#
# carol: O1 45.0 (2 + 1 = 3 units), O2 20.0 (4), O5 47.0 (1 + 2 + 1 = 4), O8 58.5
#   (1 + 2 = 3); O9 (03-20) cancelled -> orders 4, units 14, spent 170.5,
#   last_order_at O8's 2026-03-15T16:45:00 (not the cancelled O9's 03-20)
# dave: O3 61.0 (3), O7 12.0 (1); O4 cancelled -> 2, 4, 73.0, O7 2026-03-10T09:00:00
# frank, the admin, vicky: no orders -> 0, 0, 0, null
# After the admin cancels O8: carol 3 orders, 14 - 3 = 11 units, 170.5 - 58.5 = 112.0,
#   last O5 2026-02-28T23:59:59.


def _stats(orders: int, units: int, spent: float, last: str | None) -> dict:
    return {"orders": orders, "units": units, "spent": spent, "last_order_at": last}


T54 = SpecDelta(
    scenarios=(
        Scenario(
            "t54_order_stats",
            steps=(
                _get("/users/me/order-stats", "carol",
                     expect_body=_stats(4, 14, 170.5, "2026-03-15T16:45:00")),  # fmt: skip
                _get("/users/me/order-stats", "dave",
                     expect_body=_stats(2, 4, 73.0, "2026-03-10T09:00:00")),  # fmt: skip
                _get("/users/me/order-stats", "frank", expect_body=_stats(0, 0, 0, None)),
                _get("/users/me/order-stats", "admin", expect_body=_stats(0, 0, 0, None)),
                _get("/users/me/order-stats", "vicky", expect_body=_stats(0, 0, 0, None)),
                _get("/users/me/order-stats", None, expect_status=401),
            ),
        ),
        Scenario(
            "t54_order_stats_cancelled_dont_count",
            steps=(
                _status(8, "admin", "cancelled"),
                _get("/users/me/order-stats", "carol",
                     expect_body=_stats(3, 11, 112.0, "2026-02-28T23:59:59")),  # fmt: skip
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t54: no program -- 'units' sums the quantities of the caller's orders' lines "
            "and the answer is one object of several aggregates (with a null max for no "
            "orders); the scenarios state it.",
            ("Order", "OrderItem.quantity", "Order.created_at"),
        ),
    ),
)

# ======================================================================================
# t55 GET /vendors -- public: every verified vendor, by business_name
# ======================================================================================
#
# Verified: Vicky's Crafts (1), Victor Tools (2); Vera Vintage (3) is not.
#   "Vicky's Crafts" < "Victor Tools" ('k' < 't' after "Vic").
#   vicky: active P1 home, P2 home, P3 stationery (P4 decor is inactive) -> 3,
#     ["home", "stationery"]
#   victor: P5-P8, all active tools -> 4, ["tools"]
# The admin verifies Vera: "Vera Vintage" < "Vicky's Crafts" ('e' < 'i'): first, 0
#   products, []. The admin unverifies Victor: gone. Vicky deactivates P3: 2, ["home"].

VICKY_DIR = {"vendor_id": 1, "business_name": "Vicky's Crafts", "products": 3,
             "categories": ["home", "stationery"]}  # fmt: skip
VICTOR_DIR = {"vendor_id": 2, "business_name": "Victor Tools", "products": 4,
              "categories": ["tools"]}  # fmt: skip
VERA_DIR = {"vendor_id": 3, "business_name": "Vera Vintage", "products": 0, "categories": []}

T55 = SpecDelta(
    scenarios=(
        Scenario(
            "t55_vendor_directory",
            steps=(
                _get("/vendors", None, expect_body=[VICKY_DIR, VICTOR_DIR]),
                _get("/vendors", "carol", expect_body=[VICKY_DIR, VICTOR_DIR]),
            ),
        ),
        Scenario(
            "t55_vendor_directory_follows_verification",
            steps=(
                Step("PATCH", "/vendors/3/verify", "admin", json={"is_verified": True},
                     expect_db={"vendors": {"where": {"id": 3},
                                            "equals": {"is_verified": True}}}),  # fmt: skip
                _get("/vendors", None, expect_body=[VERA_DIR, VICKY_DIR, VICTOR_DIR]),
                Step("PATCH", "/vendors/2/verify", "admin", json={"is_verified": False},
                     expect_db={"vendors": {"where": {"id": 2},
                                            "equals": {"is_verified": False}}}),  # fmt: skip
                _get("/vendors", None, expect_body=[VERA_DIR, VICKY_DIR]),
            ),
        ),
        Scenario(
            "t55_vendor_directory_active_products_only",
            steps=(
                Step("PATCH", "/products/3", "vicky", json={"is_active": False},
                     expect_db=_product(3, is_active=False)),  # fmt: skip
                _get("/vendors", None,
                     expect_body=[{**VICKY_DIR, "products": 2, "categories": ["home"]},
                                  VICTOR_DIR]),  # fmt: skip
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t55: no program -- 'categories' is a list of distinct values per vendor (a "
            "list-valued aggregate), and counting a vendor's products publicly needs "
            "Product to be publicly aggregatable by vendor; the scenarios state it.",
            ("Vendor.is_verified", "Product.category", "Product.is_active"),
        ),
        Unchecked(
            "t55: the ticket orders by business_name but does not say how ties or case "
            "are ordered; the seed has neither, so nothing is asserted about them.",
            ("Vendor.business_name",),
        ),
    ),
    approved=(
        # The ticket makes the directory public ("public like GET /products"): reading
        # Vendor rows (owned by Vendor.user_id) with no caller is the requirement here,
        # limited to verified vendors' public fields.
        Accepted(
            "A:PY801:GET /vendors:*",
            "t55: GET /vendors is public by the ticket; it lists verified vendors' "
            "id, business_name and active-product summary to anyone",
        ),
    ),
)

# ======================================================================================
# t56 POST /orders/{order_id}/received -- NESTED (answers OrderRead)
# ======================================================================================
#
# The buyer only (anyone else, the admin and vendors included: 403 "You do not have
# access to this order"); unknown 404 "Order not found"; only shipped -> delivered (else
# 400 "Only shipped orders can be marked received").
#   O3 (dave, shipped): dave -> delivered; a second time -> 400.
#   O8 (carol, pending): confirmed and shipped by the admin, then carol -> delivered.
#   carol's O5 confirmed, O8 pending, O1 delivered, O9 cancelled: 400.

NOT_SHIPPED = {"detail": "Only shipped orders can be marked received"}

T56 = SpecDelta(
    scenarios=(
        Scenario(
            "t56_buyer_confirms_delivery",
            steps=(
                _post("/orders/3/received", "dave", expect_body=O3("delivered"),
                      expect_db=_db(_order(3, status="delivered"),
                                    _product(5, stock_quantity=15))),  # fmt: skip
                _get("/orders/3", "dave", expect_body=O3("delivered")),
                _post("/orders/3/received", "dave", expect_status=400, expect_body=NOT_SHIPPED,
                      expect_db=_order(3, status="delivered")),  # fmt: skip
            ),
        ),
        Scenario(
            "t56_received_after_shipping",
            steps=(
                _status(8, "admin", "confirmed"),
                _post("/orders/8/received", "carol", expect_status=400, expect_body=NOT_SHIPPED,
                      expect_db=_order(8, status="confirmed")),  # fmt: skip
                _status(8, "admin", "shipped"),
                _post("/orders/8/received", "carol", expect_body=O8("delivered"),
                      expect_db=_order(8, status="delivered")),  # fmt: skip
            ),
        ),
        Scenario(
            "t56_only_shipped_orders",
            steps=(
                _post("/orders/5/received", "carol", expect_status=400, expect_body=NOT_SHIPPED,
                      expect_db=_order(5, status="confirmed")),  # fmt: skip
                _post("/orders/8/received", "carol", expect_status=400, expect_body=NOT_SHIPPED,
                      expect_db=_order(8, status="pending")),  # fmt: skip
                _post("/orders/1/received", "carol", expect_status=400, expect_body=NOT_SHIPPED,
                      expect_db=_order(1, status="delivered")),  # fmt: skip
                _post("/orders/9/received", "carol", expect_status=400, expect_body=NOT_SHIPPED,
                      expect_db=_order(9, status="cancelled")),  # fmt: skip
            ),
        ),
        Scenario(
            "t56_received_access",
            steps=(
                _post("/orders/3/received", "carol", expect_status=403, expect_body=NO_ACCESS,
                      expect_db=_order(3, status="shipped")),  # fmt: skip
                _post("/orders/3/received", "admin", expect_status=403, expect_body=NO_ACCESS,
                      expect_db=_order(3, status="shipped")),  # fmt: skip
                _post("/orders/3/received", "victor", expect_status=403, expect_body=NO_ACCESS,
                      expect_db=_order(3, status="shipped")),  # fmt: skip
                _post("/orders/999/received", "dave", expect_status=404,
                      expect_body=ORDER_NOT_FOUND),  # fmt: skip
                _post("/orders/3/received", None, expect_status=401,
                      expect_db=_order(3, status="shipped")),  # fmt: skip
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t56: no program -- left to the scenarios, as the other order-status "
            "endpoints. The ticket does not fix whether another buyer's order that is not "
            "shipped answers 403 or 400; not asserted.",
            ("Order.status",),
        ),
    ),
)


BLIND_PIVOT_DELTAS: dict[str, SpecDelta] = {
    "t45_restock": T45,
    "t46_ship_order": T46,
    "t47_quote": T47,
    "t48_vendor_dashboard": T48,
    "t49_remove_order_item": T49,
    "t50_cancel_batch": T50,
    "t51_low_stock": T51,
    "t52_order_by_vendor": T52,
    "t53_move_stock": T53,
    "t54_order_stats": T54,
    "t55_vendor_directory": T55,
    "t56_order_received": T56,
}

# Corrections made after checking against the references: (ticket, rule, what changed,
# why). Each is a decision the ticket text supports; rules the text supports that a
# reference fails are kept and reported as disagreements instead.
CORRECTIONS: list[dict[str, str]] = [
    {
        "ticket": "t55_vendor_directory",
        "rule": "layer A PY801 on GET /vendors (owned Vendor rows read with no caller)",
        "change": "added approved=Accepted('A:PY801:GET /vendors:*', ...) to the delta",
        "why": "the ticket makes the directory public ('public like GET /products'), so "
        "an anonymous read of verified vendors is the requirement, not a leak; the first "
        "draft omitted the approval the spec needs to state that. No scenario changed.",
    },
]
