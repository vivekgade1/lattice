"""The pivot's held-out tickets (t45-t56) on the multi-vendor marketplace (plan
2026-10-05-pivot-spec-authoring.md, item B).

* twelve tickets target the vendored app and their references edit its files;
* each reference scores **correct** against its oracle, every raise site of its route
  reached, and never crashes; the unmodified app scores **not correct**;
* trap-violating variants of the references score **silent** (at least three per ticket),
  caught by the case each trap names; the ticket texts do not spell their traps out;
* the cases the text leaves open are compared on status only, or dropped;
* the smoke run and arm P's dry run pass on each reference;
* the owner's deltas (``bench/specs/pivot_tickets.py``) validate; the recorded exit table
  (``python -m bench.specs.exit --set pivot``) has every reference passing against its
  delta and covers the current trap list.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.edit_slice import oracle, run, smoke, tickets
from bench.edit_slice.apps import repo
from bench.edit_slice.reference import REFERENCE_ROOT, make_reference_app, reference_files
from bench.edit_slice.repos import multivendor
from bench.edit_slice.tickets import PIVOT, PIVOT_TICKETS, ticket
from bench.edit_slice.variant import load_variant
from lattice.importer.verify import verify

ALL = PIVOT
ROOT = Path(__file__).resolve().parents[2]
EXIT_TABLE = ROOT / "bench/specs/pivot-exit.json"


def test_twelve_tickets_on_the_multivendor_app():
    assert list(PIVOT) == [
        "t45_restock",
        "t46_ship_order",
        "t47_quote",
        "t48_vendor_dashboard",
        "t49_remove_order_item",
        "t50_cancel_batch",
        "t51_low_stock",
        "t52_order_by_vendor",
        "t53_move_stock",
        "t54_order_stats",
        "t55_vendor_directory",
        "t56_order_received",
    ]
    for t in PIVOT_TICKETS:
        assert t.repo == "multivendor" and t.base is None
        assert t.title and t.text and t.routes and t.files
        root = REFERENCE_ROOT / t.id
        own = {p.relative_to(root).as_posix() for p in root.rglob("*.py")
               if "__pycache__" not in p.parts}  # fmt: skip
        assert set(t.files) == own, t.id
        for rel in t.files:
            assert (repo("multivendor").root / rel).is_file(), (t.id, rel)
    assert run._tickets("pivot") == list(PIVOT)
    assert run._tickets("phase1") == [*tickets.OPTION1, *tickets.OPTION1B]  # unchanged


def test_the_traps_are_recorded_in_the_tickets_module():
    src = open(tickets.__file__).read()
    for tid in ALL:
        assert f"# * {tid}" in src, tid


@pytest.mark.parametrize(
    ("tid", "spelled_out"),
    [
        ("t45_restock", ["inactive", "exclusive", "zero", "replace", "set the stock",
                         "1000 is", "another vendor"]),
        ("t46_ship_order", ["pending", "cancelled", "delivered", "other vendor", "even"]),
        ("t47_quote", ["twice", "repeated", "duplicate", "combined", "summed", "added up",
                       "lines, not", "write"]),
        ("t48_vendor_dashboard", ["once", "zero", "inactive products", "never sold",
                                  "two of", "lines"]),
        ("t49_remove_order_item", ["price_at_purchase", "current price", "another order",
                                   "nothing", "before"]),
        ("t50_cancel_batch", ["nothing", "all or", "atomic", "twice", "once", "shipped",
                              "delivered", "every line", "rolled"]),
        ("t51_low_stock", ["strict", "inactive", "exclusive", "empty", "[]", "equal"]),
        ("t52_order_by_vendor", ["price_at_purchase", "current price", "403", "vendor of",
                                 "appear"]),
        ("t53_move_stock", ["nothing", "before", "atomic", "all of", "exactly", "debit"]),
        ("t54_order_stats", ["0 if", "zero", "0.0", "lines", "latest non"]),
        ("t55_vendor_directory", ["0 and", "no active", "empty", "[]", "inactive",
                                  "duplicate", "unverified"]),
        ("t56_order_received", ["pending", "confirmed", "admin", "vendor", "before"]),
    ],
)  # fmt: skip
def test_the_traps_are_not_spelled_out(tid, spelled_out):
    text = ticket(tid).text.lower()
    assert not [w for w in spelled_out if w in text]


# ---- the oracle ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reference_outcomes() -> dict[str, oracle.Outcome]:
    return {tid: oracle.evaluate(tid, lambda p, tid=tid: make_reference_app(tid, p))
            for tid in ALL}  # fmt: skip


@pytest.mark.parametrize("tid", ALL)
def test_each_reference_is_correct_against_its_own_oracle(tid, reference_outcomes):
    outcome = reference_outcomes[tid]
    assert outcome.kind == "correct", outcome.render()
    assert outcome.unverified == (), outcome.render()
    assert {c.verdict for c in outcome.cases.values()} == {"equal"}
    assert len(outcome.cases) >= 7


@pytest.mark.parametrize("tid", ALL)
def test_references_never_crash(tid):
    o = oracle.ticket_oracle(tid)
    result = verify(o.reference_factory, o.reference_factory, o.db_factory, o.cases,
                    oracle.RULES, {}, login=o.login, hash_columns=o.hash_columns)  # fmt: skip
    assert sorted(n for n, rec in result.original.items() if rec.status >= 500) == []
    assert all(x.kind == "equal" for x in result.outcomes.values())  # deterministic


@pytest.mark.parametrize("tid", ALL)
def test_the_unmodified_app_is_not_correct(tid):
    from bench.import_repos.multivendor import make_app

    assert oracle.evaluate(tid, make_app).kind != "correct"


def test_the_cases_the_text_leaves_open_are_dropped_or_status_only():
    names = {c.name for tid in ALL for c in oracle.ticket_oracle(tid).cases}
    pivot_excluded = {n for n in multivendor.EXCLUDED if n.split(".")[0] in
                      {tid.split("_")[0] for tid in ALL}}  # fmt: skip
    assert pivot_excluded == {
        "t46.vendor_not_in_pending_order", "t46.vendor_role_without_profile", "t50.empty_list"}
    assert not pivot_excluded & names
    status_only = {n for n in multivendor.STATUS_ONLY if n.startswith(("t45.", "t47.", "t51.",
                                                                       "t53."))}  # fmt: skip
    assert status_only <= names
    # option 1's lists are kept as they were
    assert {"t36.admin_shipped", "t42.empty_list", "t44.one_order", "t43.no_products"} <= (
        multivendor.EXCLUDED)


# ---- trap-violating variants --------------------------------------------------------------------

PS, PR = "services/product_service.py", "routers/product_router.py"
OS, OR = "services/order_service.py", "routers/order_router.py"
VS, VR = "services/vendor_service.py", "routers/vendor_router.py"
US = "services/user_service.py"


def _patched(tid: str, *edits: tuple[str, str, str]) -> dict[str, str]:
    """The reference of ``tid`` with each (path, old, new) replacement applied once."""
    files = dict(reference_files(tid))
    for path, old, new in edits:
        assert files[path].count(old) == 1, (tid, path, old)
        files[path] = files[path].replace(old, new)
    return files


TRAPS = {
    # ---- t45: bounds, stock added to, ownership
    "t45-zero-accepted": ("t45_restock", lambda: _patched("t45_restock", (
        PR, "quantity: int = Field(gt=0, le=1000)", "quantity: int = Field(ge=0, le=1000)"))),
    "t45-max-exclusive": ("t45_restock", lambda: _patched("t45_restock", (
        PR, "quantity: int = Field(gt=0, le=1000)", "quantity: int = Field(gt=0, lt=1000)"))),
    "t45-sets-stock": ("t45_restock", lambda: _patched("t45_restock", (
        PS, "product.stock_quantity += quantity", "product.stock_quantity = quantity"))),
    "t45-no-ownership": ("t45_restock", lambda: _patched("t45_restock", (
        PS, '    if product.vendor_id != vendor.id:\n'
            '        raise ProductPermissionError("You do not own this product")\n\n'
            '    product.stock_quantity += quantity\n',
        "    product.stock_quantity += quantity\n"))),
    # ---- t46: the state machine and who may ship
    "t46-any-vendor": ("t46_ship_order", lambda: _patched("t46_ship_order", (
        OS, "        if not any(item.product.vendor_id == vendor_id for item in order.items):\n",
        "        if vendor is None:\n"))),
    "t46-ships-pending": ("t46_ship_order", lambda: _patched("t46_ship_order", (
        OS, "    if order.status != OrderStatus.CONFIRMED:\n",
        "    if order.status not in (OrderStatus.PENDING, OrderStatus.CONFIRMED):\n"))),
    "t46-ships-cancelled": ("t46_ship_order", lambda: _patched("t46_ship_order", (
        OS, "    if order.status != OrderStatus.CONFIRMED:\n",
        "    if order.status in (OrderStatus.PENDING, OrderStatus.SHIPPED, "
        "OrderStatus.DELIVERED):\n"))),
    "t46-admin-needs-products": ("t46_ship_order", lambda: _patched("t46_ship_order", (
        OS, "    if not any_order:\n        vendor = ", "    if True:\n        vendor = "))),
    # ---- t47: duplicates, order, shape, refusals
    "t47-stock-per-entry": ("t47_quote", lambda: _patched("t47_quote", (
        OS, '            raise OrderError(f"Product {product_id} is not available")\n'
            "        if product.stock_quantity < quantity:\n",
        '            raise OrderError(f"Product {product_id} is not available")\n'
        "        if product.stock_quantity < max(i.quantity for i in data.items "
        "if i.product_id == product_id):\n"))),
    "t47-line-per-entry": ("t47_quote", lambda: _patched("t47_quote", (
        OS, "    for product_id, quantity in wanted.items():\n",
        "    for product_id, quantity in [(i.product_id, i.quantity) for i in data.items]:\n"))),
    "t47-sorted-by-product": ("t47_quote", lambda: _patched("t47_quote", (
        OS, "    for product_id, quantity in wanted.items():\n",
        "    for product_id, quantity in sorted(wanted.items()):\n"))),
    "t47-items-counts-lines": ("t47_quote", lambda: _patched("t47_quote", (
        OS, '"items": sum(line["quantity"] for line in lines),', '"items": len(lines),'))),
    "t47-quotes-inactive": ("t47_quote", lambda: _patched("t47_quote", (
        OS, "        if not product.is_active:\n"
            '            raise OrderError(f"Product {product_id} is not available")\n', ""))),
    # ---- t48: counts, zero statuses, aggregates over no rows
    "t48-orders-count-lines": ("t48_vendor_dashboard", lambda: _patched(
        "t48_vendor_dashboard",
        (VS, "            statuses[order_id] = order_status\n",
         "            by_status[order_status.value] += 1\n"),
        (VS, "        for order_status in statuses.values():\n"
             "            by_status[order_status.value] += 1\n", ""))),
    "t48-drops-empty-statuses": ("t48_vendor_dashboard", lambda: _patched(
        "t48_vendor_dashboard",
        (VS, "    by_status = {s.value: 0 for s in OrderStatus}\n", "    by_status = {}\n"),
        (VS, "            by_status[order_status.value] += 1\n",
         "            by_status[order_status.value] = "
         "by_status.get(order_status.value, 0) + 1\n"))),
    "t48-out-of-stock-counts-inactive": ("t48_vendor_dashboard", lambda: _patched(
        "t48_vendor_dashboard", (VS, "if p.is_active and p.stock_quantity <= 0",
                                 "if p.stock_quantity <= 0"))),
    "t48-units-count-cancelled": ("t48_vendor_dashboard", lambda: _patched(
        "t48_vendor_dashboard", (VS, "            if order_status != OrderStatus.CANCELLED:\n",
                                 "            if True:\n"))),
    "t48-best-seller-unsold": ("t48_vendor_dashboard", lambda: _patched(
        "t48_vendor_dashboard",
        (VS, "    if units:\n        product_id = min(units, key=lambda pid: (-units[pid], pid))\n",
         "    if products:\n"
         "        product_id = min(names, key=lambda pid: (-units.get(pid, 0), pid))\n"),
        (VS, '"units": units[product_id]}', '"units": units.get(product_id, 0)}'))),
    # ---- t49: stock and total invariants, the line's order, the last line, check order
    "t49-no-restock": ("t49_remove_order_item", lambda: _patched("t49_remove_order_item", (
        OS, "    product.stock_quantity += item.quantity\n", ""))),
    "t49-current-prices": ("t49_remove_order_item", lambda: _patched("t49_remove_order_item", (
        OS, "sum(line.quantity * line.price_at_purchase for line in remaining)",
        "sum(line.quantity * line.product.price for line in remaining)"))),
    "t49-item-of-any-order": ("t49_remove_order_item", lambda: _patched(
        "t49_remove_order_item", (
            OS, "    item = next((line for line in order.items if line.id == item_id), None)\n",
            "    from bench.import_repos.multivendor.app.models.order_item import OrderItem\n"
            "    item = session.get(OrderItem, item_id)\n"))),
    "t49-last-line-removable": ("t49_remove_order_item", lambda: _patched(
        "t49_remove_order_item", (
            OS, "    if len(order.items) == 1:\n"
                '        raise OrderError("Cannot remove the last item")\n', ""))),
    "t49-status-before-ownership": ("t49_remove_order_item", lambda: _patched(
        "t49_remove_order_item", (
            OS, "    order = get_own_order(session, current_user, order_id)\n\n"
                "    if order.status != OrderStatus.PENDING:\n"
                '        raise OrderError("Only pending orders can be changed")\n',
            "    order = get_order(session, order_id)\n"
            "    if order.status != OrderStatus.PENDING:\n"
            '        raise OrderError("Only pending orders can be changed")\n'
            "    order = get_own_order(session, current_user, order_id)\n"))),
    # ---- t50: atomicity, duplicates, restock, the machine's edges
    "t50-cancels-as-it-goes": ("t50_cancel_batch", lambda: _patched(
        "t50_cancel_batch",
        (OS, "        orders.append(order)\n",
         "        orders.append(order)\n"
         "        for item in order.items:\n"
         "            session.get(Product, item.product_id).stock_quantity += item.quantity\n"
         "        order.status = OrderStatus.CANCELLED\n"
         "        session.commit()\n"),
        (OS, "    for order in orders:\n        for item in order.items:\n",
         "    for order in []:\n        for item in order.items:\n"))),
    "t50-repeated-id-twice": ("t50_cancel_batch", lambda: _patched("t50_cancel_batch", (
        OS, "        if order_id in seen:\n            continue\n", ""))),
    "t50-no-restock": ("t50_cancel_batch", lambda: _patched("t50_cancel_batch", (
        OS, "            product.stock_quantity += item.quantity\n", ""))),
    "t50-cancels-shipped": ("t50_cancel_batch", lambda: _patched("t50_cancel_batch", (
        OS, "        if OrderStatus.CANCELLED not in VALID_TRANSITIONS[order.status]:\n",
        "        if order.status == OrderStatus.CANCELLED:\n"))),
    # ---- t51: strict and exclusive bounds, on sale only
    "t51-at-threshold-included": ("t51_low_stock", lambda: _patched("t51_low_stock", (
        VS, "Product.stock_quantity < threshold", "Product.stock_quantity <= threshold"))),
    "t51-zero-accepted": ("t51_low_stock", lambda: _patched("t51_low_stock", (
        VR, "Query(default=10, gt=0, le=1000)", "Query(default=10, ge=0, le=1000)"))),
    "t51-max-exclusive": ("t51_low_stock", lambda: _patched("t51_low_stock", (
        VR, "Query(default=10, gt=0, le=1000)", "Query(default=10, gt=0, lt=1000)"))),
    "t51-lists-inactive": ("t51_low_stock", lambda: _patched("t51_low_stock", (
        VS, "            Product.is_active == True,\n", ""))),
    # ---- t52: prices paid, ordering, access
    "t52-current-prices": ("t52_order_by_vendor", lambda: _patched(
        "t52_order_by_vendor",
        (OS, '"price": item.price_at_purchase,', '"price": product.price,'),
        (OS, 'group["subtotal"] += item.quantity * item.price_at_purchase',
         'group["subtotal"] += item.quantity * product.price'))),
    "t52-vendors-as-they-come": ("t52_order_by_vendor", lambda: _patched(
        "t52_order_by_vendor", (OS, "[groups[vendor_id] for vendor_id in sorted(groups)]",
                                "list(groups.values())"))),
    "t52-lines-by-product": ("t52_order_by_vendor", lambda: _patched(
        "t52_order_by_vendor", (OS, "sorted(order.items, key=lambda line: line.id)",
                                "sorted(order.items, key=lambda line: line.product_id)"))),
    "t52-order-vendors-allowed": ("t52_order_by_vendor", lambda: _patched(
        "t52_order_by_vendor", (
            OS, "    order = get_order(session, order_id) if any_order else "
                "get_own_order(session, current_user, order_id)\n",
            "    order = get_order(session, order_id)\n"
            "    if not any_order and order.user_id != current_user.id and not any(\n"
            "            i.product.vendor.user_id == current_user.id for i in order.items):\n"
            '        raise OrderPermissionError("You do not have access to this order")\n'))),
    # ---- t53: atomicity, bounds, the same product
    "t53-debits-before-checking-target": ("t53_move_stock", lambda: _patched(
        "t53_move_stock", (
            PS, "    target = _own_product(session, vendor.id, to_product_id)\n"
                "    if source.id == target.id:\n"
                '        raise StockMoveError("Cannot move stock to the same product")\n'
                "    if source.stock_quantity < quantity:\n"
                '        raise NotEnoughStockError("Not enough stock")\n\n'
                "    source.stock_quantity -= quantity\n",
            "    if source.stock_quantity < quantity:\n"
            '        raise NotEnoughStockError("Not enough stock")\n'
            "    source.stock_quantity -= quantity\n"
            "    session.add(source)\n"
            "    session.commit()\n"
            "    target = _own_product(session, vendor.id, to_product_id)\n"
            "    if source.id == target.id:\n"
            '        raise StockMoveError("Cannot move stock to the same product")\n'))),
    "t53-cannot-move-all": ("t53_move_stock", lambda: _patched("t53_move_stock", (
        PS, "    if source.stock_quantity < quantity:\n",
        "    if source.stock_quantity <= quantity:\n"))),
    "t53-same-product-allowed": ("t53_move_stock", lambda: _patched("t53_move_stock", (
        PS, "    if source.id == target.id:\n"
            '        raise StockMoveError("Cannot move stock to the same product")\n', ""))),
    "t53-zero-accepted": ("t53_move_stock", lambda: _patched("t53_move_stock", (
        PR, "quantity: int = Field(gt=0)", "quantity: int = Field(ge=0)"))),
    # ---- t54: aggregates over no rows, cancelled orders, orders not lines
    "t54-spent-null-when-none": ("t54_order_stats", lambda: _patched("t54_order_stats", (
        US, '"spent": float(sum(order.total_amount for order in orders)),',
        '"spent": float(sum(order.total_amount for order in orders)) if orders else None,'))),
    "t54-latest-includes-cancelled": ("t54_order_stats", lambda: _patched("t54_order_stats", (
        US, '"last_order_at": max((order.created_at for order in orders), default=None),',
        '"last_order_at": max((o.created_at for o in session.exec(select(Order).where('
        "Order.user_id == current_user.id)).all()), default=None),"))),
    "t54-units-count-cancelled": ("t54_order_stats", lambda: _patched("t54_order_stats", (
        US, '"units": sum(item.quantity for order in orders for item in order.items),',
        '"units": sum(item.quantity for order in session.exec(select(Order).where('
        "Order.user_id == current_user.id)).all() for item in order.items),"))),
    "t54-orders-count-lines": ("t54_order_stats", lambda: _patched("t54_order_stats", (
        US, '"orders": len(orders),', '"orders": sum(len(order.items) for order in orders),'))),
    # ---- t55: empty shops, order, inactive products, distinct categories, verified only
    "t55-drops-shops-without-products": ("t55_vendor_directory", lambda: _patched(
        "t55_vendor_directory", (
            VS, "        listed.append(\n",
            "        if not active:\n            continue\n        listed.append(\n"))),
    "t55-ordered-by-id": ("t55_vendor_directory", lambda: _patched("t55_vendor_directory", (
        VS, "sorted(vendors, key=lambda v: v.business_name)", "sorted(vendors, key=lambda v: v.id)"
    ))),
    "t55-counts-inactive": ("t55_vendor_directory", lambda: _patched("t55_vendor_directory", (
        VS, '"products": len(active),', '"products": len(vendor.products),'))),
    "t55-repeats-categories": ("t55_vendor_directory", lambda: _patched("t55_vendor_directory", (
        VS, "sorted({p.category for p in active})", "sorted(p.category for p in active)"))),
    "t55-lists-unverified": ("t55_vendor_directory", lambda: _patched("t55_vendor_directory", (
        VS, "select(Vendor).where(Vendor.is_verified == True)", "select(Vendor)"))),
    # ---- t56: the machine's one edge, the buyer only, ownership first
    "t56-from-confirmed": ("t56_order_received", lambda: _patched("t56_order_received", (
        OS, "    if order.status != OrderStatus.SHIPPED:\n",
        "    if order.status not in (OrderStatus.CONFIRMED, OrderStatus.SHIPPED):\n"))),
    "t56-admin-bypass": ("t56_order_received", lambda: _patched("t56_order_received", (
        OS, "    order = get_own_order(session, current_user, order_id)\n\n"
            "    if order.status != OrderStatus.SHIPPED:\n",
        '    order = (get_order(session, order_id) if current_user.role.value == "admin"\n'
        "             else get_own_order(session, current_user, order_id))\n"
        "    if order.status != OrderStatus.SHIPPED:\n"))),
    "t56-status-before-ownership": ("t56_order_received", lambda: _patched(
        "t56_order_received", (
            OS, "    order = get_own_order(session, current_user, order_id)\n\n"
                "    if order.status != OrderStatus.SHIPPED:\n"
                '        raise OrderError("Only shipped orders can be marked received")\n',
            "    order = get_order(session, order_id)\n"
            "    if order.status != OrderStatus.SHIPPED:\n"
            '        raise OrderError("Only shipped orders can be marked received")\n'
            "    order = get_own_order(session, current_user, order_id)\n"))),
    "t56-order-vendors-allowed": ("t56_order_received", lambda: _patched(
        "t56_order_received", (
            OS, "    order = get_own_order(session, current_user, order_id)\n\n"
                "    if order.status != OrderStatus.SHIPPED:\n",
            "    order = get_order(session, order_id)\n"
            "    if order.user_id != current_user.id and not any(\n"
            "            i.product.vendor.user_id == current_user.id for i in order.items):\n"
            '        raise OrderPermissionError("You do not have access to this order")\n'
            "    if order.status != OrderStatus.SHIPPED:\n"))),
}  # fmt: skip

# the case each trap must fail
CAUGHT_BY = {
    "t45-zero-accepted": "t45.zero",
    "t45-max-exclusive": "t45.max_1000",
    "t45-sets-stock": "t45.own_ten",
    "t45-no-ownership": "t45.another_vendors_product",
    "t46-any-vendor": "t46.vendor_not_in_order",
    "t46-ships-pending": "t46.own_pending",
    "t46-ships-cancelled": "t46.cancelled",
    "t46-admin-needs-products": "t46.admin_ships_confirmed",
    "t47-stock-per-entry": "t47.repeated_beyond_stock",
    "t47-line-per-entry": "t47.repeated_product",
    "t47-sorted-by-product": "t47.repeated_product",
    "t47-items-counts-lines": "t47.basket",
    "t47-quotes-inactive": "t47.inactive_product",
    "t48-orders-count-lines": "t48.vicky",
    "t48-drops-empty-statuses": "t48.vendor_without_products",
    "t48-out-of-stock-counts-inactive": "t48.vicky",
    "t48-units-count-cancelled": "t48.victor",
    "t48-best-seller-unsold": "t48.products_never_sold",
    "t49-no-restock": "t49.remove_a_line",
    "t49-current-prices": "t49.total_at_prices_paid",
    "t49-item-of-any-order": "t49.item_of_another_order",
    "t49-last-line-removable": "t49.only_line",
    "t49-status-before-ownership": "t49.another_buyers_shipped",
    "t50-cancels-as-it-goes": "t50.later_unknown_writes_nothing",
    "t50-repeated-id-twice": "t50.repeated_id",
    "t50-no-restock": "t50.two_pending",
    "t50-cancels-shipped": "t50.later_shipped_writes_nothing",
    "t51-at-threshold-included": "t51.victor_at_threshold",
    "t51-zero-accepted": "t51.threshold_zero",
    "t51-max-exclusive": "t51.vicky_1000",
    "t51-lists-inactive": "t51.vicky_default_is_empty",
    "t52-current-prices": "t52.price_changed_since",
    "t52-vendors-as-they-come": "t52.victor_first",
    "t52-lines-by-product": "t52.victor_first",
    "t52-order-vendors-allowed": "t52.vendor_with_a_line",
    "t53-debits-before-checking-target": "t53.target_foreign_writes_nothing",
    "t53-cannot-move-all": "t53.move_all",
    "t53-same-product-allowed": "t53.same_product",
    "t53-zero-accepted": "t53.zero_quantity",
    "t54-spent-null-when-none": "t54.no_orders",
    "t54-latest-includes-cancelled": "t54.carol",
    "t54-units-count-cancelled": "t54.carol",
    "t54-orders-count-lines": "t54.carol",
    "t55-drops-shops-without-products": "t55.verified_shop_without_products",
    "t55-ordered-by-id": "t55.verified_shop_without_products",
    "t55-counts-inactive": "t55.seed",
    "t55-repeats-categories": "t55.seed",
    "t55-lists-unverified": "t55.seed",
    "t56-from-confirmed": "t56.own_confirmed",
    "t56-admin-bypass": "t56.admin_is_not_the_buyer",
    "t56-status-before-ownership": "t56.another_buyers_pending",
    "t56-order-vendors-allowed": "t56.vendor_of_the_order",
}


def test_every_ticket_has_at_least_three_silent_traps():
    per_ticket = {tid: sum(1 for t, _ in TRAPS.values() if t == tid) for tid in ALL}
    assert all(n >= 3 for n in per_ticket.values()), per_ticket
    assert set(CAUGHT_BY) == set(TRAPS)


@pytest.mark.parametrize("name", sorted(TRAPS))
def test_trap_variants_score_silent(name):
    tid, make = TRAPS[name]
    outcome = oracle.evaluate(tid, load_variant(make(), repo="multivendor").make_app)
    assert outcome.kind == "silent", outcome.render()
    assert outcome.cases[CAUGHT_BY[name]].verdict == "silent", outcome.render()


# ---- the canonical comparison -------------------------------------------------------------------


@pytest.mark.parametrize("tid", ["t45_restock", "t53_move_stock"])
def test_a_product_answers_version_is_not_compared(tid):
    """Whether a stock change bumps the optimistic lock is the implementer's call."""
    old = ("    product.stock_quantity += quantity\n" if tid == "t45_restock"
           else "    target.stock_quantity += quantity\n")  # fmt: skip
    new = old + ("    product.version += 1\n" if tid == "t45_restock"
                 else "    target.version += 1\n    source.version += 1\n")  # fmt: skip
    files = _patched(tid, (PS, old, new))
    outcome = oracle.evaluate(tid, load_variant(files, repo="multivendor").make_app)
    assert outcome.kind == "correct", outcome.render()


def test_an_unstated_422_body_is_not_compared():
    files = _patched("t51_low_stock", (
        VR, "    threshold: int = Query(default=10, gt=0, le=1000),\n",
        "    threshold: int = Query(default=10),\n"), (
        VS, "    vendor = get_own_vendor_profile(session, current_user)\n    statement",
        "    if not 0 < threshold <= 1000:\n"
        "        from fastapi import HTTPException\n"
        '        raise HTTPException(status_code=422, detail="threshold must be 1-1000")\n'
        "    vendor = get_own_vendor_profile(session, current_user)\n    statement"))  # fmt: skip
    outcome = oracle.evaluate("t51_low_stock", load_variant(files, repo="multivendor").make_app)
    assert outcome.kind == "correct", outcome.render()


# ---- the smoke run and arm P --------------------------------------------------------------------


@pytest.mark.parametrize("tid", ALL)
def test_the_smoke_run_passes_on_each_reference(tid):
    result = smoke.run_smoke(tid, lambda p: make_reference_app(tid, p))
    assert result.started and not result.failed, result.render()
    assert len(result.lines) == len(smoke.SMOKE[tid])


@pytest.mark.parametrize("tid", ALL)
def test_arm_p_prompt_carries_no_trap(tid):
    t = ticket(tid)
    system, user = run.p_prompt(tid)
    assert t.text.strip() in user
    src = open(tickets.__file__).read()
    notes = [line.lstrip("# ").strip() for line in src.splitlines()
             if line.startswith("# ") and len(line) >= 30]  # fmt: skip
    for text in (system, user):
        assert not [n for n in notes if n in text]
        assert not [c.name for c in oracle.ticket_oracle(tid).cases if c.name in text]


@pytest.mark.parametrize("tid", ALL)
def test_the_p_dry_run_scores_each_reference_correct(tid):
    r = run.dry_run("P", tid)
    assert (r.outcome, r.rounds, r.failed_cases) == ("correct", 1, []), r.detail
    assert r.model == "reference" and r.dry_run and r.lines_changed > 0


# ---- the owner's deltas -------------------------------------------------------------------------


@pytest.mark.parametrize("tid", ALL)
def test_each_owner_delta_validates(tid):
    from bench.specs.multivendor import SPEC
    from bench.specs.multivendor_tickets import apply
    from bench.specs.pivot_tickets import DELTAS
    from lattice.spec import validate

    assert validate(apply(SPEC, DELTAS[tid])) == []


@pytest.fixture(scope="module")
def exit_table() -> list[dict]:
    if not EXIT_TABLE.exists():
        pytest.skip("run `python -m bench.specs.exit --set pivot` first")
    return json.loads(EXIT_TABLE.read_text())


def test_the_recorded_exit_table_covers_the_current_traps(exit_table):
    assert {r["name"] for r in exit_table if r["name"] != "reference"} == set(TRAPS)
    assert {r["ticket"] for r in exit_table if r["name"] == "reference"} == set(ALL)


def test_every_reference_checks_clean_against_its_owner_delta(exit_table):
    bad = [(r["ticket"], r.get("findings") or r.get("error"))
           for r in exit_table if r["name"] == "reference" and r["verdict"] != "pass"]  # fmt: skip
    assert not bad


@pytest.mark.slow
def test_one_reference_checks_clean_live():
    from bench.specs.exit import check_ticket
    from bench.specs.pivot_tickets import DELTAS

    tid = "t56_order_received"
    report = check_ticket(tid, reference_files(tid), deltas=DELTAS)
    assert report.verdict == "pass", report.render()


def test_minted_tokens_outlive_a_long_oracle_process():
    """Experiment D's review: an oracle cached in a long process saw a minted token expire
    between a reference and its candidate (the app's tokens last 30 minutes)."""
    from datetime import datetime, timedelta

    from bench.import_repos.multivendor.app.core import security

    token = multivendor.mint(7)
    claims = security.jwt.decode(
        token, security.settings.secret_key, algorithms=[security.settings.algorithm]
    )
    assert claims["sub"] == "7"
    assert datetime.utcfromtimestamp(claims["exp"]) > datetime.utcnow() + timedelta(days=3650)
