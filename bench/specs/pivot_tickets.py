"""The owner's spec deltas for the pivot's held-out tickets t45-t56 (plan
2026-10-05-pivot-spec-authoring.md, items B and D's arm S*).

Written from the ticket texts (``bench/edit_slice/tickets.py``, ``PIVOT_TICKETS``) and the
app's existing behaviour only, as the app's owner would state each ticket: concrete
scenarios on the seed data (expected answers worked out by hand; the arithmetic is in
the comments), in the format of ``bench.specs.multivendor_tickets``. `apply(SPEC,
DELTAS[t])` is the spec a change for ticket t is checked against
(``python -m bench.specs.exit --set pivot``).

Seed ids are listed in ``bench.specs.multivendor``'s docstring. Every scenario starts from
a fresh seed database. Where a ticket leaves a status code or a detail open (a 422's
body), the scenario asserts only what the ticket, or the endpoint it says to behave
like, fixes. Product answers are compared without `version` (the optimistic lock is not
part of any ticket), and new rows' `created_at` is never asserted.

Caveat for the analysis: these deltas were written by the agent that also wrote the
tickets' trap variants (``tests/bench/test_edit_slice_pivot.py``), from the ticket texts,
before any check of them ran. They are the owner arm's deltas, not blind in the Phase 0
sense; the trap coverage they reach is reported, not tuned (no scenario was added after a
check result; the one later addition is t55's approval of the public listing the text
states, after the first reference check flagged it).

None has a program: every ticket either takes a list in its body (t47, t50), writes rows
of more than one table per request or one row per line (t45-t46 and t49 restock or
update other tables' rows, t53 writes two products), answers a nested object built from
several tables (t48, t52) or aggregates rows another owner owns for a vendor (t48, t51)
-- each is an `Unchecked` note. Their scenarios state them.
"""

from __future__ import annotations

from bench.specs.multivendor import line, order_read, product_read
from bench.specs.multivendor_tickets import SpecDelta, _db, _get, _order, _product
from lattice.spec import Accepted, Scenario, Step, Unchecked

__all__ = ["DELTAS"]

DENIED = {"detail": "You do not have permission to perform this action."}
NO_ACCESS = {"detail": "You do not have access to this order"}
ORDER_NOT_FOUND = {"detail": "Order not found"}
NO_VENDOR = {"detail": "No vendor profile found for this user"}
NOT_OWNER = {"detail": "You do not own this product"}
PRODUCT_NOT_FOUND = {"detail": "Product not found"}

ORDER_1 = order_read(
    1, 5, "delivered", 45.0, "2026-01-05T10:00:00", [line(1, 1, 2, 10.0), line(2, 5, 1, 25.0)]
)
ORDER_3 = order_read(
    3, 6, "shipped", 61.0, "2026-02-01T00:00:00", [line(4, 5, 1, 25.0), line(5, 6, 2, 18.0)]
)
ORDER_5 = order_read(
    5,
    5,
    "confirmed",
    47.0,
    "2026-02-28T23:59:59",
    [line(7, 1, 1, 12.0), line(8, 2, 2, 8.5), line(9, 6, 1, 18.0)],
)
ORDER_8 = order_read(
    8, 5, "pending", 58.5, "2026-03-15T16:45:00", [line(12, 2, 1, 8.5), line(13, 5, 2, 25.0)]
)


def _pr(pid: int, **changes: object) -> dict:
    """Seed product `pid` as GET /products/{id} shows it, `changes` applied, no version."""
    row = product_read(pid, **changes)
    del row["version"]
    return row


def _post(path: str, user: str | None, **kw: object) -> Step:
    return Step("POST", path, user, **kw)  # type: ignore[arg-type]


def _no_order_written() -> dict:
    return {"orders": {"count": 9}}


# ======================================================================================
# t45 POST /products/{product_id}/restock -- adds 0 < n <= 1000 units; PATCH's access
# ======================================================================================
#
# vicky: mug (P1) 40 + 10 = 50, + 5 more = 55; her inactive poster (P4, PATCH works on it)
# 0 + 5 = 5. victor: hammer (P5) 15 + 1 = 16; tape (P7) 0 + 1000 = 1000.
# 0, -1 and 1001 are 422s that change nothing. Another vendor's product: 403 "You do not
# own this product"; unknown: 404 "Product not found"; buyers and admins: the permission
# 403 (PATCH needs MANAGE_OWN_PRODUCTS).


def _restock(pid: int, user: str | None, quantity: object, **kw: object) -> Step:
    return _post(f"/products/{pid}/restock", user, json={"quantity": quantity}, **kw)


T45 = SpecDelta(
    scenarios=(
        Scenario(
            "t45_restock_adds_to_the_stock",
            steps=(
                _restock(1, "vicky", 10, expect_body=_pr(1, stock_quantity=50),
                         expect_db=_product(1, stock_quantity=50)),
                _restock(1, "vicky", 5, expect_body=_pr(1, stock_quantity=55),
                         expect_db=_product(1, stock_quantity=55)),
                _restock(4, "vicky", 5, expect_body=_pr(4, stock_quantity=5),
                         expect_db=_product(4, stock_quantity=5, is_active=False)),
            ),
        ),
        Scenario(
            "t45_restock_bounds",
            steps=(
                _restock(5, "victor", 1, expect_body=_pr(5, stock_quantity=16),
                         expect_db=_product(5, stock_quantity=16)),
                _restock(7, "victor", 1000, expect_body=_pr(7, stock_quantity=1000),
                         expect_db=_product(7, stock_quantity=1000)),
                _restock(1, "vicky", 0, expect_status=422,
                         expect_db=_product(1, stock_quantity=40)),
                _restock(1, "vicky", -1, expect_status=422,
                         expect_db=_product(1, stock_quantity=40)),
                _restock(1, "vicky", 1001, expect_status=422,
                         expect_db=_product(1, stock_quantity=40)),
            ),
        ),
        Scenario(
            "t45_restock_access",
            steps=(
                _restock(5, "vicky", 5, expect_status=403, expect_body=NOT_OWNER,
                         expect_db=_product(5, stock_quantity=15)),
                _restock(1, "vera", 5, expect_status=403, expect_body=NOT_OWNER,
                         expect_db=_product(1, stock_quantity=40)),
                _restock(999, "vicky", 5, expect_status=404, expect_body=PRODUCT_NOT_FOUND),
                _restock(1, "carol", 5, expect_status=403, expect_body=DENIED),
                _restock(1, "admin", 5, expect_status=403, expect_body=DENIED,
                         expect_db=_product(1, stock_quantity=40)),
                _restock(1, None, 5, expect_status=401),
            ),
        ),
    ),
    unchecked=(
        Unchecked(
            "t45: no program -- the vendor's write to a product is checked through its "
            "vendor (Product has no owner in the profile: the catalogue is public).",
            ("Product.stock_quantity",),
        ),
    ),
)  # fmt: skip

# ======================================================================================
# t46 POST /orders/{order_id}/ship -- NESTED (OrderRead); confirmed -> shipped only
# ======================================================================================
#
# O5 (carol, confirmed) has vicky's P1, P2 and victor's P6: either of them, or the admin,
# ships it. A confirmed order with none of vicky's products: carol orders victor's gloves
# (P8) -> order 10, the admin confirms it; vicky gets 403 "You have no products in this
# order" and it stays confirmed. vera (no products) on O5: the same 403.
# Not confirmed: O7 pending (vicky's mug), O3 shipped, O1 delivered, O9 and O4 cancelled
# -> 400 "Only confirmed orders can be shipped", the status unchanged (also for the admin).

SHIPPED_5 = {**ORDER_5, "status": "shipped"}


def _ship(oid: int, user: str | None, **kw: object) -> Step:
    return _post(f"/orders/{oid}/ship", user, **kw)


T46 = SpecDelta(
    scenarios=(
        Scenario("t46_vendor_ships_a_confirmed_order", steps=(
            _ship(5, "vicky", expect_body=SHIPPED_5, expect_db=_order(5, status="shipped")),
            # shipped -> shipped is no edge
            _ship(5, "victor", expect_status=400,
                  expect_body={"detail": "Only confirmed orders can be shipped"},
                  expect_db=_order(5, status="shipped")),
        )),
        Scenario("t46_other_vendor_ships", steps=(
            _ship(5, "victor", expect_body=SHIPPED_5, expect_db=_order(5, status="shipped")),
        )),
        Scenario("t46_admin_ships_any_order", steps=(
            _ship(5, "admin", expect_body=SHIPPED_5, expect_db=_order(5, status="shipped")),
        )),
        Scenario("t46_vendor_without_products_in_the_order", steps=(
            _post("/orders", "carol", json={"items": [{"product_id": 8, "quantity": 1}]},
                  expect_status=201),
            Step("PATCH", "/orders/10/status", "admin", json={"status": "confirmed"}),
            _ship(10, "vicky", expect_status=403,
                  expect_body={"detail": "You have no products in this order"},
                  expect_db=_order(10, status="confirmed")),
            _ship(5, "vera", expect_status=403,
                  expect_body={"detail": "You have no products in this order"},
                  expect_db=_order(5, status="confirmed")),
            _ship(10, "victor", expect_db=_order(10, status="shipped")),
        )),
        Scenario("t46_only_confirmed_orders", steps=(
            _ship(7, "vicky", expect_status=400,
                  expect_body={"detail": "Only confirmed orders can be shipped"},
                  expect_db=_order(7, status="pending")),
            _ship(8, "admin", expect_status=400,
                  expect_body={"detail": "Only confirmed orders can be shipped"},
                  expect_db=_order(8, status="pending")),
            _ship(3, "victor", expect_status=400,
                  expect_body={"detail": "Only confirmed orders can be shipped"},
                  expect_db=_order(3, status="shipped")),
            _ship(1, "vicky", expect_status=400,
                  expect_body={"detail": "Only confirmed orders can be shipped"},
                  expect_db=_order(1, status="delivered")),
            _ship(9, "victor", expect_status=400,
                  expect_body={"detail": "Only confirmed orders can be shipped"},
                  expect_db=_order(9, status="cancelled")),
            _ship(4, "admin", expect_status=400,
                  expect_body={"detail": "Only confirmed orders can be shipped"},
                  expect_db=_order(4, status="cancelled")),
        )),
        Scenario("t46_ship_access", steps=(
            _ship(999, "vicky", expect_status=404, expect_body=ORDER_NOT_FOUND),
            _ship(5, "carol", expect_status=403, expect_body=DENIED,
                  expect_db=_order(5, status="confirmed")),
            _ship(5, None, expect_status=401),
        )),
    ),
    unchecked=(
        Unchecked(
            "t46: no program -- a vendor's right to ship depends on the order's lines' "
            "products' vendor (OrderItem -> Product -> Vendor), which Lattice's single-owner "
            "model does not express for a write to Order.",
            ("Order.status", "OrderItem"),
        ),
    ),
)  # fmt: skip

# ======================================================================================
# t47 POST /orders/quote -- writes nothing
# ======================================================================================
#
# mug x2 @12.00 = 24.0, hammer x1 @25.00 = 25.0 -> items 3, total 49.0.
# candle x2, mug x1, candle x3: one line per product, first appearance first ->
#   candle x5 @8.50 = 42.5, mug x1 = 12.0 -> items 6, total 54.5.
# hammer has 15 in stock: x15 quotes (375.0); x16 is the 409, also asked as 10 + 6.
# tape has none (409); the poster is inactive (400); product 999 does not exist (404).

def _quote(user: str | None, *lines: tuple[int, int], **kw: object) -> Step:
    body = {"items": [{"product_id": pid, "quantity": qty} for pid, qty in lines]}
    return _post("/orders/quote", user, json=body, **kw)


def _ql(pid: int, name: str, qty: int, price: float) -> dict:
    return {"product_id": pid, "name": name, "quantity": qty, "unit_price": price,
            "line_total": qty * price}


T47 = SpecDelta(
    scenarios=(
        Scenario("t47_quote", steps=(
            _quote("carol", (1, 2), (5, 1),
                   expect_body={"lines": [_ql(1, "Ceramic mug", 2, 12.0),
                                          _ql(5, "Claw hammer", 1, 25.0)],
                                "items": 3, "total": 49.0},
                   expect_db=_db(_no_order_written(), _product(1, stock_quantity=40))),
            _get("/products/5", None, expect_db=_db({"order_items": {"count": 14}},
                                                    _product(5, stock_quantity=15))),
        )),
        Scenario("t47_quote_one_line_per_product", steps=(
            _quote("carol", (2, 2), (1, 1), (2, 3),
                   expect_body={"lines": [_ql(2, "Soy candle", 5, 8.5),
                                          _ql(1, "Ceramic mug", 1, 12.0)],
                                "items": 6, "total": 54.5},
                   expect_db=_no_order_written()),
        )),
        Scenario("t47_quote_stock", steps=(
            _quote("dave", (5, 15),
                   expect_body={"lines": [_ql(5, "Claw hammer", 15, 25.0)],
                                "items": 15, "total": 375.0}),
            _quote("dave", (5, 16), expect_status=409,
                   expect_body={"detail": "Insufficient stock for product 5"}),
            _quote("dave", (5, 10), (5, 6), expect_status=409,
                   expect_body={"detail": "Insufficient stock for product 5"},
                   expect_db=_product(5, stock_quantity=15)),
            _quote("carol", (7, 1), expect_status=409,
                   expect_body={"detail": "Insufficient stock for product 7"}),
        )),
        Scenario("t47_quote_refused", steps=(
            _quote("carol", (4, 1), expect_status=400,
                   expect_body={"detail": "Product 4 is not available"}),
            _quote("carol", (1, 1), (999, 1), expect_status=404,
                   expect_body={"detail": "Product 999 not found"},
                   expect_db=_no_order_written()),
            # the same body as POST /orders: at least one item, quantities above 0
            _post("/orders/quote", "carol", json={"items": []}, expect_status=422),
            _quote("carol", (1, 0), expect_status=422),
        )),
        Scenario("t47_quote_anyone_signed_in", steps=(
            _quote("vicky", (8, 3), expect_body={"lines": [_ql(8, "Work gloves", 3, 6.0)],
                                                 "items": 3, "total": 18.0}),
            _quote("admin", (3, 4), expect_body={"lines": [_ql(3, "Dot-grid notebook", 4, 5.0)],
                                                 "items": 4, "total": 20.0},
                   expect_db=_no_order_written()),
            _quote(None, (1, 1), expect_status=401),
        )),
    ),
    unchecked=(
        Unchecked(
            "t47: no program -- the body is a list of (product, quantity) pairs (Lattice "
            "parameters are scalars), merged per product.",
            ("Product",),
        ),
    ),
)  # fmt: skip

# ======================================================================================
# t48 GET /vendors/me/dashboard -- NESTED
# ======================================================================================
#
# vicky (vendor 1): products P1-P4, active P1-P3, none of them out of stock (P4 has no
#   stock but is inactive). Orders with her products: O1 delivered, O2 delivered, O4
#   cancelled, O5 confirmed, O6 delivered, O7 pending, O8 pending -> pending 2,
#   confirmed 1, shipped 0, delivered 3, cancelled 1 (O5 has two of her lines: one order).
#   Units, cancelled O4 excluded: P1 2+1+1 = 4, P2 2+1 = 3, P3 4+10 = 14 -> 21; best P3.
# victor (vendor 2): P5-P8 all active, P7 out of stock. Orders: O1 delivered, O3 shipped,
#   O5 confirmed, O8 pending, O9 cancelled -> one each. Units: P5 1+1+2 = 4, P6 2+1 = 3
#   (O9's 1 cancelled) -> 7; best P5 (4).
# vera (vendor 3): nothing -> zeros, best_seller null; after listing a product (P9, never
#   sold): total 1, active 1, still null.


def _dash(vendor: dict, products: tuple[int, int, int], orders: tuple[int, ...],
          units: int, best: dict | None) -> dict:
    statuses = ("pending", "confirmed", "shipped", "delivered", "cancelled")
    return {
        "vendor": vendor,
        "products": dict(zip(("total", "active", "out_of_stock"), products, strict=True)),
        "orders": dict(zip(statuses, orders, strict=True)),
        "units_sold": units,
        "best_seller": best,
    }


VICKY_SHOP = {"id": 1, "user_id": 2, "business_name": "Vicky's Crafts", "is_verified": True}
VICTOR_SHOP = {"id": 2, "user_id": 3, "business_name": "Victor Tools", "is_verified": True}
VERA_SHOP = {"id": 3, "user_id": 4, "business_name": "Vera Vintage", "is_verified": False}

T48 = SpecDelta(
    scenarios=(
        Scenario("t48_dashboard", steps=(
            _get("/vendors/me/dashboard", "vicky", expect_body=_dash(
                VICKY_SHOP, (4, 3, 0), (2, 1, 0, 3, 1), 21,
                {"product_id": 3, "name": "Dot-grid notebook", "units": 14})),
            _get("/vendors/me/dashboard", "victor", expect_body=_dash(
                VICTOR_SHOP, (4, 4, 1), (1, 1, 1, 1, 1), 7,
                {"product_id": 5, "name": "Claw hammer", "units": 4})),
        )),
        Scenario("t48_dashboard_of_a_shop_that_never_sold", steps=(
            _get("/vendors/me/dashboard", "vera",
                 expect_body=_dash(VERA_SHOP, (0, 0, 0), (0, 0, 0, 0, 0), 0, None)),
            _post("/products", "vera", json={"name": "Brass lamp", "category": "decor",
                                             "price": 30.0, "stock_quantity": 2},
                  expect_status=201),
            _get("/vendors/me/dashboard", "vera",
                 expect_body=_dash(VERA_SHOP, (1, 1, 0), (0, 0, 0, 0, 0), 0, None)),
        )),
        Scenario("t48_dashboard_access", steps=(
            _get("/vendors/me/dashboard", "carol", expect_status=404, expect_body=NO_VENDOR),
            _get("/vendors/me/dashboard", "admin", expect_status=404, expect_body=NO_VENDOR),
            _get("/vendors/me/dashboard", None, expect_status=401),
        )),
    ),
    unchecked=(
        Unchecked(
            "t48: no program -- the vendor's counts aggregate other buyers' orders and lines "
            "(Order and OrderItem are owned by the buyer), and the answer nests three "
            "aggregates.",
            ("Order.status", "OrderItem.quantity", "Product.stock_quantity"),
        ),
    ),
)  # fmt: skip

# ======================================================================================
# t49 DELETE /orders/{order_id}/items/{item_id} -- NESTED (OrderRead)
# ======================================================================================
#
# carol removes item 13 (hammer x2 @25) from O8 (pending): O8 keeps item 12 (candle x1 @8.5),
# total 8.5; hammer stock 15 + 2 = 17. After vicky raises the candle to 10.00, removing
# item 13 still leaves 8.5: the total is what the remaining lines were bought at.
# dave's O7 has one line (item 11): 400 "Cannot remove the last item", nothing changes.
# Item 11 is not a line of O8: 404 "Item not found", O7 keeps it.

def _remove(oid: int, item: int, user: str | None, **kw: object) -> Step:
    return Step("DELETE", f"/orders/{oid}/items/{item}", user, **kw)  # type: ignore[arg-type]


ORDER_8_AFTER = {**ORDER_8, "total_amount": 8.5, "items": [line(12, 2, 1, 8.5)]}

T49 = SpecDelta(
    scenarios=(
        Scenario("t49_remove_a_line", steps=(
            _remove(8, 13, "carol", expect_body=ORDER_8_AFTER,
                    expect_db=_db(_order(8, total_amount=8.5), _product(5, stock_quantity=17),
                                  {"order_items": {"where": {"id": 13}, "count": 0}})),
        )),
        Scenario("t49_total_at_the_prices_paid", steps=(
            Step("PATCH", "/products/2", "vicky", json={"price": 10.0}),
            _remove(8, 13, "carol", expect_body=ORDER_8_AFTER,
                    expect_db=_order(8, total_amount=8.5)),
        )),
        Scenario("t49_remove_refused", steps=(
            _remove(7, 11, "dave", expect_status=400,
                    expect_body={"detail": "Cannot remove the last item"},
                    expect_db=_db({"order_items": {"where": {"order_id": 7}, "count": 1}},
                                  _product(1, stock_quantity=40))),
            _remove(8, 11, "carol", expect_status=404, expect_body={"detail": "Item not found"},
                    expect_db=_db({"order_items": {"where": {"id": 11}, "count": 1}},
                                  _product(1, stock_quantity=40))),
            _remove(8, 999, "carol", expect_status=404, expect_body={"detail": "Item not found"},
                    expect_db=_order(8, total_amount=58.5)),
            _remove(5, 7, "carol", expect_status=400,
                    expect_body={"detail": "Only pending orders can be changed"},
                    expect_db=_db({"order_items": {"where": {"order_id": 5}, "count": 3}},
                                  _product(1, stock_quantity=40))),
            _remove(1, 1, "carol", expect_status=400,
                    expect_body={"detail": "Only pending orders can be changed"},
                    expect_db={"order_items": {"where": {"order_id": 1}, "count": 2}}),
        )),
        Scenario("t49_remove_access", steps=(
            _remove(7, 11, "carol", expect_status=403, expect_body=NO_ACCESS,
                    expect_db={"order_items": {"where": {"order_id": 7}, "count": 1}}),
            _remove(3, 4, "carol", expect_status=403, expect_body=NO_ACCESS,
                    expect_db={"order_items": {"where": {"order_id": 3}, "count": 2}}),
            _remove(8, 13, "admin", expect_status=403, expect_body=NO_ACCESS,
                    expect_db={"order_items": {"where": {"order_id": 8}, "count": 2}}),
            _remove(8, 12, "vicky", expect_status=403, expect_body=NO_ACCESS),
            _remove(999, 1, "carol", expect_status=404, expect_body=ORDER_NOT_FOUND),
            _remove(8, 13, None, expect_status=401),
        )),
    ),
    unchecked=(
        Unchecked(
            "t49: no program -- removing a line also restocks its product and rewrites the "
            "order's total (writes to three tables; update() needs rows of one table).",
            ("OrderItem", "Order.total_amount", "Product.stock_quantity"),
        ),
    ),
)  # fmt: skip

# ======================================================================================
# t50 POST /orders/cancel-batch -- admins only
# ======================================================================================
#
# [7, 8]: both pending -> cancelled; stock back: mug (O7) 41, candle (O8 x1) 31, hammer
# (O8 x2) 17. [5]: confirmed -> cancelled; mug 41, candle 32, screwdriver 21.
# [7, 999] and [8, 3]: refused (999 unknown, 3 shipped): nothing is cancelled or restocked.
# [4] is already cancelled and [1] delivered: 400. [7, 7] is one order: cancelled 1, mug 41.

def _batch(ids: list[int], user: str | None = "admin", **kw: object) -> Step:
    return _post("/orders/cancel-batch", user, json={"order_ids": ids}, **kw)


T50 = SpecDelta(
    scenarios=(
        Scenario("t50_cancel_two_pending_orders", steps=(
            _batch([7, 8], expect_body={"cancelled": 2},
                   expect_db=_db({"orders": {"where": {"status": "cancelled"}, "count": 4}},
                                 _product(1, stock_quantity=41))),
            _get("/products/2", None, expect_db=_db(_order(7, status="cancelled"),
                                                    _product(2, stock_quantity=31))),
            _get("/products/5", None, expect_db=_db(_order(8, status="cancelled"),
                                                    _product(5, stock_quantity=17))),
        )),
        Scenario("t50_cancel_a_confirmed_order", steps=(
            _batch([5], expect_body={"cancelled": 1},
                   expect_db=_db(_order(5, status="cancelled"), _product(1, stock_quantity=41))),
            _get("/products/2", None, expect_db=_product(2, stock_quantity=32)),
            _get("/products/6", None, expect_db=_product(6, stock_quantity=21)),
        )),
        Scenario("t50_an_order_listed_twice", steps=(
            _batch([7, 7], expect_body={"cancelled": 1},
                   expect_db=_db(_order(7, status="cancelled"), _product(1, stock_quantity=41))),
        )),
        Scenario("t50_a_refused_batch_cancels_nothing", steps=(
            _batch([7, 999], expect_status=404, expect_body={"detail": "Order 999 not found"},
                   expect_db=_db(_order(7, status="pending"), _product(1, stock_quantity=40))),
            _batch([8, 3], expect_status=400,
                   expect_body={"detail": "Order 3 cannot be cancelled"},
                   expect_db=_db(_order(8, status="pending"), _product(5, stock_quantity=15))),
            _batch([4], expect_status=400, expect_body={"detail": "Order 4 cannot be cancelled"},
                   expect_db=_product(2, stock_quantity=30)),
            _batch([1], expect_status=400, expect_body={"detail": "Order 1 cannot be cancelled"},
                   expect_db=_db(_order(1, status="delivered"), _product(1, stock_quantity=40))),
        )),
        Scenario("t50_admins_only", steps=(
            _batch([8], "carol", expect_status=403, expect_body=DENIED,
                   expect_db=_order(8, status="pending")),
            _batch([8], "vicky", expect_status=403, expect_body=DENIED),
            _batch([8], None, expect_status=401),
        )),
    ),
    unchecked=(
        Unchecked(
            "t50: no program -- the body is a list of order ids (Lattice parameters are "
            "scalars), and each cancellation restocks its lines' products.",
            ("Order.status", "Product.stock_quantity"),
        ),
    ),
)  # fmt: skip

# ======================================================================================
# t51 GET /vendors/me/low-stock?threshold=N -- 0 < N <= 1000, default 10
# ======================================================================================
#
# victor (on sale: hammer 15, screwdriver 20, tape 0, gloves 50): default 10 -> tape;
# 15 -> tape (the hammer has 15, not fewer); 20 -> tape, hammer; 1 -> tape.
# vicky (on sale: mug 40, candle 30, notebook 100; the poster has 0 but is not on sale):
# default -> []; 1000 -> candle 30, mug 40, notebook 100.

def _low(pid: int, name: str, stock: int) -> dict:
    return {"product_id": pid, "name": name, "stock_quantity": stock}


TAPE, HAMMER = _low(7, "Tape measure", 0), _low(5, "Claw hammer", 15)

T51 = SpecDelta(
    scenarios=(
        Scenario("t51_low_stock", steps=(
            _get("/vendors/me/low-stock", "victor", expect_body=[TAPE]),
            _get("/vendors/me/low-stock", "victor", params={"threshold": 15},
                 expect_body=[TAPE]),
            _get("/vendors/me/low-stock", "victor", params={"threshold": 20},
                 expect_body=[TAPE, HAMMER]),
            _get("/vendors/me/low-stock", "victor", params={"threshold": 1},
                 expect_body=[TAPE]),
            _get("/vendors/me/low-stock", "vicky", expect_body=[]),
            _get("/vendors/me/low-stock", "vicky", params={"threshold": 1000},
                 expect_body=[_low(2, "Soy candle", 30), _low(1, "Ceramic mug", 40),
                              _low(3, "Dot-grid notebook", 100)]),
            _get("/vendors/me/low-stock", "vera", expect_body=[]),
        )),
        Scenario("t51_low_stock_threshold_bounds", steps=(
            _get("/vendors/me/low-stock", "vicky", params={"threshold": 0}, expect_status=422),
            _get("/vendors/me/low-stock", "vicky", params={"threshold": -3},
                 expect_status=422),
            _get("/vendors/me/low-stock", "vicky", params={"threshold": 1001},
                 expect_status=422),
        )),
        Scenario("t51_low_stock_access", steps=(
            _get("/vendors/me/low-stock", "carol", expect_status=404, expect_body=NO_VENDOR),
            _get("/vendors/me/low-stock", None, expect_status=401),
        )),
    ),
    unchecked=(
        Unchecked(
            "t51: no program -- a vendor's products are reached through the caller's vendor "
            "profile (Product has no owner in the profile).",
            ("Product.stock_quantity", "Product.is_active"),
        ),
    ),
)  # fmt: skip

# ======================================================================================
# t52 GET /orders/{order_id}/by-vendor -- NESTED; GET /orders/{order_id}'s access
# ======================================================================================
#
# O1: vicky's mug x2 bought at 10.00 (now 12.00) -> subtotal 20.0; victor's hammer 25.0.
# O5: vicky mug x1 @12 + candle x2 @8.5 = 29.0 (items 7, 8); victor screwdriver 18.0.
# A new order of carol's, gloves (victor), mug (vicky), hammer (victor) -> order 10 with
# items 15, 16, 17: vicky [16] 12.0, victor [15, 17] 6 + 25 = 31.0, total 43.0.

def _vl(item: int, pid: int, name: str, qty: int, price: float) -> dict:
    return {"item_id": item, "product_id": pid, "name": name, "quantity": qty, "price": price}


def _vg(vid: int, name: str, subtotal: float, lines: list[dict]) -> dict:
    return {"vendor_id": vid, "business_name": name, "subtotal": subtotal, "lines": lines}


VICKY, VICTOR = "Vicky's Crafts", "Victor Tools"

T52 = SpecDelta(
    scenarios=(
        Scenario("t52_order_by_vendor", steps=(
            _get("/orders/1/by-vendor", "carol", expect_body={
                "order_id": 1, "status": "delivered", "total": 45.0, "vendors": [
                    _vg(1, VICKY, 20.0, [_vl(1, 1, "Ceramic mug", 2, 10.0)]),
                    _vg(2, VICTOR, 25.0, [_vl(2, 5, "Claw hammer", 1, 25.0)])]}),
            _get("/orders/5/by-vendor", "carol", expect_body={
                "order_id": 5, "status": "confirmed", "total": 47.0, "vendors": [
                    _vg(1, VICKY, 29.0, [_vl(7, 1, "Ceramic mug", 1, 12.0),
                                         _vl(8, 2, "Soy candle", 2, 8.5)]),
                    _vg(2, VICTOR, 18.0, [_vl(9, 6, "Screwdriver set", 1, 18.0)])]}),
            _get("/orders/3/by-vendor", "admin", expect_body={
                "order_id": 3, "status": "shipped", "total": 61.0, "vendors": [
                    _vg(2, VICTOR, 61.0, [_vl(4, 5, "Claw hammer", 1, 25.0),
                                          _vl(5, 6, "Screwdriver set", 2, 18.0)])]}),
        )),
        Scenario("t52_vendors_and_lines_in_order", steps=(
            _post("/orders", "carol", json={"items": [{"product_id": 8, "quantity": 1},
                                                      {"product_id": 1, "quantity": 1},
                                                      {"product_id": 5, "quantity": 1}]},
                  expect_status=201),
            _get("/orders/10/by-vendor", "carol", expect_body={
                "order_id": 10, "status": "pending", "total": 43.0, "vendors": [
                    _vg(1, VICKY, 12.0, [_vl(16, 1, "Ceramic mug", 1, 12.0)]),
                    _vg(2, VICTOR, 31.0, [_vl(15, 8, "Work gloves", 1, 6.0),
                                          _vl(17, 5, "Claw hammer", 1, 25.0)])]}),
        )),
        Scenario("t52_order_by_vendor_access", steps=(
            _get("/orders/3/by-vendor", "carol", expect_status=403, expect_body=NO_ACCESS),
            _get("/orders/1/by-vendor", "vicky", expect_status=403, expect_body=NO_ACCESS),
            _get("/orders/999/by-vendor", "carol", expect_status=404,
                 expect_body=ORDER_NOT_FOUND),
            _get("/orders/1/by-vendor", None, expect_status=401),
        )),
    ),
    unchecked=(
        Unchecked(
            "t52: no program -- the answer groups the order's lines by their products' "
            "vendor (a nested list per group).",
            ("OrderItem", "Product.vendor_id", "Vendor.business_name"),
        ),
    ),
)  # fmt: skip

# ======================================================================================
# t53 POST /products/{product_id}/move-stock -- both products the vendor's own
# ======================================================================================
#
# vicky: mug -> candle x5: mug 35, candle 35. victor: hammer -> gloves x15 (all of it):
# hammer 0, gloves 65; x16 is 409 "Not enough stock". A refused move changes neither
# product: target another vendor's (403), unknown (404), the same product (400), 0 (422).

def _move(src: int, dst: int, qty: object, user: str | None = "vicky", **kw: object) -> Step:
    return _post(f"/products/{src}/move-stock", user,
                 json={"to_product_id": dst, "quantity": qty}, **kw)  # fmt: skip


T53 = SpecDelta(
    scenarios=(
        Scenario("t53_move_stock", steps=(
            _move(1, 2, 5, expect_body={"from": _pr(1, stock_quantity=35),
                                        "to": _pr(2, stock_quantity=35)},
                  expect_db=_product(1, stock_quantity=35)),
            _get("/products/2", None, expect_db=_product(2, stock_quantity=35)),
        )),
        Scenario("t53_move_all_of_it", steps=(
            _move(5, 8, 15, "victor", expect_body={"from": _pr(5, stock_quantity=0),
                                                   "to": _pr(8, stock_quantity=65)},
                  expect_db=_product(5, stock_quantity=0)),
            _get("/products/8", None, expect_db=_product(8, stock_quantity=65)),
        )),
        Scenario("t53_move_refused", steps=(
            _move(5, 8, 16, "victor", expect_status=409,
                  expect_body={"detail": "Not enough stock"},
                  expect_db=_product(5, stock_quantity=15)),
            _move(1, 5, 5, expect_status=403, expect_body=NOT_OWNER,
                  expect_db=_product(1, stock_quantity=40)),
            _get("/products/5", None, expect_db=_product(5, stock_quantity=15)),
            _move(1, 999, 5, expect_status=404, expect_body=PRODUCT_NOT_FOUND,
                  expect_db=_product(1, stock_quantity=40)),
            _move(5, 1, 5, expect_status=403, expect_body=NOT_OWNER,
                  expect_db=_product(5, stock_quantity=15)),
            _move(999, 1, 5, expect_status=404, expect_body=PRODUCT_NOT_FOUND,
                  expect_db=_product(1, stock_quantity=40)),
            _move(1, 1, 5, expect_status=400,
                  expect_body={"detail": "Cannot move stock to the same product"},
                  expect_db=_product(1, stock_quantity=40)),
            _move(1, 2, 0, expect_status=422, expect_db=_product(1, stock_quantity=40)),
            _move(1, 2, -1, expect_status=422, expect_db=_product(2, stock_quantity=30)),
        )),
        Scenario("t53_move_access", steps=(
            _move(1, 2, 1, "vera", expect_status=403, expect_body=NOT_OWNER),
            _move(1, 2, 1, "carol", expect_status=403, expect_body=DENIED),
            _move(1, 2, 1, "admin", expect_status=403, expect_body=DENIED,
                  expect_db=_product(1, stock_quantity=40)),
            _move(1, 2, 1, None, expect_status=401),
        )),
    ),
    unchecked=(
        Unchecked(
            "t53: no program -- one request writes two products' stock (update() writes the "
            "rows one filter selects, with one set of values).",
            ("Product.stock_quantity",),
        ),
    ),
)  # fmt: skip

# ======================================================================================
# t54 GET /users/me/order-stats
# ======================================================================================
#
# carol: O1 45, O2 20, O5 47, O8 58.5 (O9 cancelled) -> 4 orders, units 3+4+4+3 = 14,
#   spent 170.5, latest 2026-03-15 16:45 (O9's 03-20 is cancelled).
# dave: O3 61, O7 12 (O4 cancelled) -> 2, units 3+1 = 4, 73.0, latest 2026-03-10 09:00.
# frank, and vicky (a vendor who placed none): 0, 0, 0, null.

def _stats(orders: int, units: int, spent: float, last: str | None) -> dict:
    return {"orders": orders, "units": units, "spent": spent, "last_order_at": last}


T54 = SpecDelta(
    scenarios=(
        Scenario("t54_order_stats", steps=(
            _get("/users/me/order-stats", "carol",
                 expect_body=_stats(4, 14, 170.5, "2026-03-15T16:45:00")),
            _get("/users/me/order-stats", "dave",
                 expect_body=_stats(2, 4, 73.0, "2026-03-10T09:00:00")),
            _get("/users/me/order-stats", "frank", expect_body=_stats(0, 0, 0, None)),
            _get("/users/me/order-stats", "vicky", expect_body=_stats(0, 0, 0, None)),
            _get("/users/me/order-stats", None, expect_status=401),
        )),
    ),
    unchecked=(
        Unchecked(
            "t54: no program -- `units` sums the caller's orders' lines (a join through "
            "Order) next to aggregates of Order in one answer.",
            ("Order", "OrderItem.quantity"),
        ),
    ),
)  # fmt: skip

# ======================================================================================
# t55 GET /vendors -- public
# ======================================================================================
#
# Verified: Vicky's Crafts (active P1-P3: home, home, stationery) and Victor Tools (P5-P8,
# all tools). Vera Vintage is not verified. Once the admin verifies her: listed first by
# name, with 0 products and no categories.

VICKY_ROW = {"vendor_id": 1, "business_name": VICKY, "products": 3,
             "categories": ["home", "stationery"]}  # fmt: skip
VICTOR_ROW = {"vendor_id": 2, "business_name": VICTOR, "products": 4, "categories": ["tools"]}

T55 = SpecDelta(
    scenarios=(
        Scenario("t55_vendor_directory", steps=(
            _get("/vendors", None, expect_body=[VICKY_ROW, VICTOR_ROW]),
            _get("/vendors", "carol", expect_body=[VICKY_ROW, VICTOR_ROW]),
        )),
        Scenario("t55_vendor_directory_new_shop", steps=(
            Step("PATCH", "/vendors/3/verify", "admin", json={"is_verified": True}),
            _get("/vendors", None, expect_body=[
                {"vendor_id": 3, "business_name": "Vera Vintage", "products": 0,
                 "categories": []},
                VICKY_ROW, VICTOR_ROW]),
        )),
    ),
    # Added after the first reference check (the only change after a check; an approval,
    # not a scenario): the text says "public like GET /products", as t33's approval.
    approved=(
        Accepted(
            "A:PY801:GET /vendors:*",
            "t55: 'public like GET /products': verified shops' names and counts are listed "
            "to anyone; no owned column beyond the shop's public name is shown",
        ),
    ),
    unchecked=(
        Unchecked(
            "t55: no program -- per-vendor counts and distinct category lists of their "
            "active products (a nested list per row).",
            ("Vendor", "Product.category", "Product.is_active"),
        ),
    ),
)  # fmt: skip

# ======================================================================================
# t56 POST /orders/{order_id}/received -- NESTED (OrderRead); shipped -> delivered only
# ======================================================================================
#
# dave's O3 (shipped) -> delivered; again: 400. carol's O8 pending, O5 confirmed, O1
# delivered, O9 cancelled: 400, unchanged. Anyone but the buyer: 403, unchanged.

def _received(oid: int, user: str | None, **kw: object) -> Step:
    return _post(f"/orders/{oid}/received", user, **kw)


NOT_SHIPPED = {"detail": "Only shipped orders can be marked received"}

T56 = SpecDelta(
    scenarios=(
        Scenario("t56_buyer_marks_received", steps=(
            _received(3, "dave", expect_body={**ORDER_3, "status": "delivered"},
                      expect_db=_order(3, status="delivered")),
            _received(3, "dave", expect_status=400, expect_body=NOT_SHIPPED,
                      expect_db=_order(3, status="delivered")),
        )),
        Scenario("t56_only_shipped_orders", steps=(
            _received(8, "carol", expect_status=400, expect_body=NOT_SHIPPED,
                      expect_db=_order(8, status="pending")),
            _received(5, "carol", expect_status=400, expect_body=NOT_SHIPPED,
                      expect_db=_order(5, status="confirmed")),
            _received(1, "carol", expect_status=400, expect_body=NOT_SHIPPED,
                      expect_db=_order(1, status="delivered")),
            _received(9, "carol", expect_status=400, expect_body=NOT_SHIPPED,
                      expect_db=_order(9, status="cancelled")),
        )),
        Scenario("t56_only_the_buyer", steps=(
            _received(3, "carol", expect_status=403, expect_body=NO_ACCESS,
                      expect_db=_order(3, status="shipped")),
            _received(7, "carol", expect_status=403, expect_body=NO_ACCESS,
                      expect_db=_order(7, status="pending")),
            _received(3, "admin", expect_status=403, expect_body=NO_ACCESS,
                      expect_db=_order(3, status="shipped")),
            _received(3, "victor", expect_status=403, expect_body=NO_ACCESS,
                      expect_db=_order(3, status="shipped")),
            _received(999, "dave", expect_status=404, expect_body=ORDER_NOT_FOUND),
            _received(3, None, expect_status=401),
        )),
    ),
    unchecked=(
        Unchecked(
            "t56: no program -- the answer is OrderRead with nested items, and the write is "
            "a guarded state change on the caller's own order (expressible, not written: "
            "the base spec has no program for the order routes' writes either).",
            ("Order.status",),
        ),
    ),
)  # fmt: skip

# The owner's deltas, as written from the ticket texts (no scenario added after a check).
DELTAS: dict[str, SpecDelta] = {
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
