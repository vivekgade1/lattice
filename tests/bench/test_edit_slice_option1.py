"""Option 1's pilot tickets (t31-t38) on the multi-vendor marketplace.

* the tickets target the vendored app and their references edit its files;
* each reference scores **correct** against its oracle, every raise site of its route
  reached, and never crashes;
* the unmodified app scores **not correct** on every ticket;
* trap-violating variants of the references score **silent** (at least three per ticket),
  caught by the cases the trap names;
* the smoke run and arm P's dry run pass on each reference; the prompt carries no trap;
* every mechanism tag names a real case.
"""

from __future__ import annotations

import pytest

from bench.edit_slice import oracle, run, smoke, tickets
from bench.edit_slice.apps import repo
from bench.edit_slice.reference import REFERENCE_ROOT, make_reference_app, reference_files
from bench.edit_slice.repos import multivendor
from bench.edit_slice.tickets import OPTION1, OPTION1_TICKETS, OPTION1B, OPTION1B_TICKETS, ticket
from bench.edit_slice.variant import load_variant
from lattice.importer.verify import verify

ALL = (*OPTION1, *OPTION1B)


def test_eight_tickets_on_the_multivendor_app():
    assert list(OPTION1) == [
        "t31_vendor_sales",
        "t32_monthly_report",
        "t33_top_sellers",
        "t34_customer_spend",
        "t35_vendor_order_lines",
        "t36_cancel_order",
        "t37_product_buyers",
        "t38_reorder",
    ]
    for t in OPTION1_TICKETS:
        assert t.repo == "multivendor" and t.base is None
        assert t.title and t.text and t.routes and t.files
        root = REFERENCE_ROOT / t.id
        own = {p.relative_to(root).as_posix() for p in root.rglob("*.py")
               if "__pycache__" not in p.parts}  # fmt: skip
        assert set(t.files) == own, t.id
        for rel in t.files:
            assert (repo("multivendor").root / rel).is_file(), (t.id, rel)
    assert run._tickets("option1") == list(OPTION1)


def test_the_traps_are_recorded_in_the_tickets_module():
    src = (tickets.__file__ and open(tickets.__file__).read()) or ""
    for tid in ALL:
        assert f"# * {tid}" in src, tid


@pytest.mark.parametrize(
    ("tid", "spelled_out"),
    [
        ("t31_vendor_sales", ["price_at_purchase", "current price", "whole day", "null",
                              "once", "other vendor", "422", "404"]),
        ("t32_monthly_report", ["route", "boundar", "december", "zero", "403", "00:00"]),
        ("t33_top_sellers", ["never", "zero", "0", "inactive", "route"]),
        ("t34_customer_spend", ["no orders", "inactive", "vendor", "0", "join"]),
        ("t35_vendor_order_lines", ["other vendor", "inactive", "cancelled", "404"]),
        ("t36_cancel_order", ["403", "400", "404", "shipped", "delivered", "before"]),
        ("t37_product_buyers", ["403", "404", "admin", "once", "inactive", "empty"]),
        ("t38_reorder", ["current price", "price_at_purchase", "403", "nothing",
                         "cancelled", "atomic", "partial"]),
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


def test_every_mechanism_tag_names_a_case():
    names = {c.name for tid in ALL for c in oracle.ticket_oracle(tid).cases}
    assert set(multivendor.MECHANISMS) <= names
    assert set(multivendor.MECHANISMS.values()) <= {
        "L420", "L421", "L802", "one-statement/atomic", "typed-params"}  # fmt: skip
    assert multivendor.mechanism("t32.february_boundaries") == "none"


# ---- trap-violating variants --------------------------------------------------------------------

VS, OS, PS = (
    "services/vendor_service.py",
    "services/order_service.py",
    "services/product_service.py",
)
OR, PR = "routers/order_router.py", "routers/product_router.py"
PKG = "bench.import_repos.multivendor.app"


def _patched(tid: str, *edits: tuple[str, str, str]) -> dict[str, str]:
    """The reference of ``tid`` with each (path, old, new) replacement applied once."""
    files = dict(reference_files(tid))
    for path, old, new in edits:
        assert files[path].count(old) == 1, (tid, path, old)
        files[path] = files[path].replace(old, new)
    return files


def _moved_after(tid: str, path: str, start: str, end: str, anchor: str) -> dict[str, str]:
    """``path`` with the block [start, end) moved to just before ``anchor`` (a literal
    route declared after the parameterised route that swallows it)."""
    files = dict(reference_files(tid))
    src = files[path]
    a, b = src.index(start), src.index(end)
    block, src = src[a:b], src[:a] + src[b:]
    at = src.index(anchor)
    files[path] = src[:at] + block + src[at:]
    return files


TRAPS = {
    # ---- t31
    "t31-counts-cancelled": ("t31_vendor_sales", lambda: _patched("t31_vendor_sales", (
        VS, ", Order.status != OrderStatus.CANCELLED)", ")"))),
    "t31-end-exclusive": ("t31_vendor_sales", lambda: _patched("t31_vendor_sales", (
        VS, "datetime.combine(end + timedelta(days=1), time.min)",
        "datetime.combine(end, time.min)"))),
    "t31-current-price": ("t31_vendor_sales", lambda: _patched("t31_vendor_sales", (
        VS, "OrderItem.quantity, OrderItem.price_at_purchase)",
        "OrderItem.quantity, Product.price)"))),
    "t31-orders-counts-lines": ("t31_vendor_sales", lambda: _patched("t31_vendor_sales", (
        VS, "len({order_id for order_id, _, _ in rows})", "len(rows)"))),
    "t31-null-when-no-sales": ("t31_vendor_sales", lambda: _patched("t31_vendor_sales", (
        VS, '"revenue": float(sum(quantity * price for _, quantity, price in rows)),',
        '"revenue": float(sum(quantity * price for _, quantity, price in rows)) if rows '
        "else None,"))),
    # ---- t32
    "t32-shadowed-by-order-route": ("t32_monthly_report", lambda: _moved_after(
        "t32_monthly_report", OR, '@router.get("/report")', '@router.get("/{order_id}"',
        '@router.patch("/{order_id}/status"')),
    "t32-month-end-inclusive": ("t32_monthly_report", lambda: _patched("t32_monthly_report", (
        OS, "Order.created_at < end", "Order.created_at <= end"))),
    "t32-drops-empty-statuses": ("t32_monthly_report", lambda: _patched(
        "t32_monthly_report",
        (OS, "by_status = {s.value: 0 for s in OrderStatus}", "by_status = {}"),
        (OS, "by_status[order.status.value] += 1",
         "by_status[order.status.value] = by_status.get(order.status.value, 0) + 1"))),
    "t32-revenue-counts-cancelled": ("t32_monthly_report", lambda: _patched(
        "t32_monthly_report", (OS, " if o.status != OrderStatus.CANCELLED)", ")"))),
    # ---- t33
    "t33-drops-never-sold": ("t33_top_sellers", lambda: _patched("t33_top_sellers", (
        PS, "        for p in products\n    ]",
        "        for p in products if p.id in units\n    ]"))),
    "t33-lists-inactive": ("t33_top_sellers", lambda: _patched("t33_top_sellers", (
        PS, "statement = select(Product).where(Product.is_active == True)",
        "statement = select(Product)"))),
    "t33-counts-cancelled": ("t33_top_sellers", lambda: _patched("t33_top_sellers", (
        PS, "        .where(Order.status != OrderStatus.CANCELLED)\n    ).all()", "    ).all()"))),
    "t33-shadowed-by-product-route": ("t33_top_sellers", lambda: _moved_after(
        "t33_top_sellers", PR, '@router.get("/top-sellers")',
        '@router.get("/{product_id}", response_model=ProductRead)',
        '@router.patch("/{product_id}"')),
    # ---- t34
    "t34-join-fan-out": ("t34_customer_spend", lambda: _patched(
        "t34_customer_spend",
        (OS, "orders = session.exec(select(Order).where(",
         "from bench.import_repos.multivendor.app.models.order_item import OrderItem\n"
         "    orders = session.exec(select(Order).join(OrderItem, "
         "OrderItem.order_id == Order.id).where("))),
    "t34-drops-buyers-without-orders": ("t34_customer_spend", lambda: _patched(
        "t34_customer_spend",
        (OS, "return sorted(rows.values(),",
         "return sorted([r for r in rows.values() if r[\"orders\"]],"))),
    "t34-includes-vendors": ("t34_customer_spend", lambda: _patched("t34_customer_spend", (
        OS, "select(User).where(User.role == Role.BUYER)",
        "select(User).where(User.role != Role.ADMIN)"))),
    "t34-drops-inactive-buyers": ("t34_customer_spend", lambda: _patched("t34_customer_spend", (
        OS, "select(User).where(User.role == Role.BUYER)",
        "select(User).where(User.role == Role.BUYER, User.is_active == True)"))),
    # ---- t35 (control)
    "t35-whole-orders": ("t35_vendor_order_lines", lambda: _patched(
        "t35_vendor_order_lines",
        (VS, "        .where(Product.vendor_id == vendor.id)\n",
         "        .where(Order.id.in_(select(OrderItem.order_id).join(Product, "
         "Product.id == OrderItem.product_id).where(Product.vendor_id == vendor.id)))\n"))),
    "t35-drops-inactive-products": ("t35_vendor_order_lines", lambda: _patched(
        "t35_vendor_order_lines", (VS, ".where(Product.vendor_id == vendor.id)",
                                   ".where(Product.vendor_id == vendor.id, "
                                   "Product.is_active == True)"))),
    "t35-oldest-first": ("t35_vendor_order_lines", lambda: _patched(
        "t35_vendor_order_lines", (VS, ".order_by(Order.created_at.desc(), OrderItem.id)",
                                   ".order_by(Order.created_at, OrderItem.id)"))),
    # ---- t36
    "t36-status-before-ownership": ("t36_cancel_order", lambda: _patched(
        "t36_cancel_order",
        (OS, "    order = get_order(session, order_id) if any_order else "
             "get_own_order(session, current_user, order_id)\n",
         "    order = get_order(session, order_id)\n"),
        (OS, "    for item in order.items:\n        product = session.get(Product",
         "    if not any_order and order.user_id != current_user.id:\n"
         '        raise OrderPermissionError("You do not have access to this order")\n\n'
         "    for item in order.items:\n        product = session.get(Product"))),
    "t36-no-stock-restore": ("t36_cancel_order", lambda: _patched("t36_cancel_order", (
        OS, "        product.stock_quantity += item.quantity\n", ""))),
    "t36-no-ownership": ("t36_cancel_order", lambda: _patched("t36_cancel_order", (
        OS, "get_order(session, order_id) if any_order else "
            "get_own_order(session, current_user, order_id)",
        "get_order(session, order_id)"))),
    "t36-any-status": ("t36_cancel_order", lambda: _patched("t36_cancel_order", (
        OS, "    if OrderStatus.CANCELLED not in VALID_TRANSITIONS[order.status]:",
        "    if False:"))),
    # ---- t37
    "t37-no-ownership-check": ("t37_product_buyers", lambda: _patched("t37_product_buyers", (
        PS, '    if product.vendor_id != vendor.id:\n        raise ProductPermissionError('
            '"You do not own this product")\n\n    lines',
        "    lines"))),
    "t37-counts-cancelled": ("t37_product_buyers", lambda: _patched("t37_product_buyers", (
        PS, "OrderItem.product_id == product_id, Order.status != OrderStatus.CANCELLED)",
        "OrderItem.product_id == product_id)"))),
    "t37-one-row-per-line": ("t37_product_buyers", lambda: _patched("t37_product_buyers", (
        PS, "buyers.setdefault(user.id,", "buyers.setdefault(len(buyers),"))),
    "t37-drops-inactive-buyers": ("t37_product_buyers", lambda: _patched("t37_product_buyers", (
        PS, "OrderItem.product_id == product_id, Order.status != OrderStatus.CANCELLED)",
        "OrderItem.product_id == product_id, Order.status != OrderStatus.CANCELLED, "
        "User.is_active == True)"))),
    # ---- t38
    "t38-copies-old-prices": ("t38_reorder", lambda: _patched(
        "t38_reorder",
        (OS, "            price_at_purchase=product.price,\n        )\n        total +=",
         "            price_at_purchase=item.price_at_purchase,\n        )\n        total +="),
        (OS, "total += product.price * item.quantity\n    order_repository",
         "total += item.price_at_purchase * item.quantity\n    order_repository"))),
    "t38-reuses-place-order": ("t38_reorder", lambda: _patched("t38_reorder", (
        OS, "    lines = sorted(original.items, key=lambda item: item.id)\n",
        "    lines = sorted(original.items, key=lambda item: item.id)\n"
        f"    from {PKG}.schemas.order import OrderItemCreate\n"
        "    return place_order(session, current_user, OrderCreate(items=[\n"
        "        OrderItemCreate(product_id=i.product_id, quantity=i.quantity)\n"
        "        for i in lines]))\n"))),
    "t38-no-ownership": ("t38_reorder", lambda: _patched("t38_reorder", (
        OS, "    original = get_own_order(session, current_user, order_id)",
        "    original = get_order(session, order_id)"))),
}  # fmt: skip

# the case each trap must fail (so the gate attributes it to the right mechanism)
CAUGHT_BY = {
    "t31-counts-cancelled": "t31.victor_march_excludes_cancelled",
    "t31-end-exclusive": "t31.end_is_inclusive",
    "t31-current-price": "t31.vicky_all_time",
    "t31-orders-counts-lines": "t31.february",
    "t31-null-when-no-sales": "t31.vendor_without_sales",
    "t32-shadowed-by-order-route": "t32.february_boundaries",
    "t32-month-end-inclusive": "t32.february_boundaries",
    "t32-drops-empty-statuses": "t32.empty_month",
    "t32-revenue-counts-cancelled": "t32.february_boundaries",
    "t33-drops-never-sold": "t33.all",
    "t33-lists-inactive": "t33.inactive_product_that_sold",
    "t33-counts-cancelled": "t33.all",
    "t33-shadowed-by-product-route": "t33.all",
    "t34-join-fan-out": "t34.admin",
    "t34-drops-buyers-without-orders": "t34.admin",
    "t34-includes-vendors": "t34.admin",
    "t34-drops-inactive-buyers": "t34.admin",
    "t35-whole-orders": "t35.vicky",
    "t35-drops-inactive-products": "t35.inactive_product_line",
    "t35-oldest-first": "t35.vicky",
    "t36-status-before-ownership": "t36.another_buyers_shipped",
    "t36-no-stock-restore": "t36.own_pending",
    "t36-no-ownership": "t36.another_buyers_pending",
    "t36-any-status": "t36.own_shipped",
    "t37-no-ownership-check": "t37.another_vendors_product",
    "t37-counts-cancelled": "t37.candle_cancelled_excluded",
    "t37-one-row-per-line": "t37.mug",
    "t37-drops-inactive-buyers": "t37.notebook_inactive_buyer",
    "t38-copies-old-prices": "t38.price_changed_since",
    "t38-reuses-place-order": "t38.not_enough_stock_writes_nothing",
    "t38-no-ownership": "t38.another_buyers_order",
}

VR = "routers/vendor_router.py"

# ---- option 1b (t39-t44): trap-violating variants of the atomicity references
TRAPS_1B = {
    # t39: per-item commit, count of entries, no ownership check
    "t39-commits-each-item": ("t39_bulk_price", lambda: _patched("t39_bulk_price", (
        PS, "        products[product_id] = product\n",
        "        products[product_id] = product\n"
        "        product.price = dict(changes)[product_id]\n"
        "        session.add(product)\n        session.commit()\n"))),
    "t39-counts-entries": ("t39_bulk_price", lambda: _patched("t39_bulk_price", (
        PS, 'return {"updated": len(products)}', 'return {"updated": len(changes)}'))),
    "t39-no-ownership": ("t39_bulk_price", lambda: _patched("t39_bulk_price", (
        PS, '        if product.vendor_id != vendor.id:\n'
            '            raise ProductPermissionError("You do not own this product")\n'
            '        products[product_id] = product\n',
        "        products[product_id] = product\n"))),
    # t40: stock written before the check, held stock ignored, old prices kept, status first
    "t40-writes-before-checking": ("t40_edit_order_items", lambda: _patched(
        "t40_edit_order_items", (
            OS, "    for pid, quantity in needed.items():\n"
                "        if products[pid].stock_quantity + held.get(pid, 0) < quantity:\n"
                "            raise InsufficientStockError(pid)\n\n"
                "    for pid, product in products.items():\n"
                "        product.stock_quantity += held.get(pid, 0) - needed.get(pid, 0)\n"
                "        session.add(product)\n",
            "    for pid, product in sorted(products.items()):\n"
            "        product.stock_quantity += held.get(pid, 0) - needed.get(pid, 0)\n"
            "        if product.stock_quantity < 0:\n"
            "            raise InsufficientStockError(pid)\n"
            "        session.add(product)\n        session.commit()\n"))),
    "t40-ignores-held-stock": ("t40_edit_order_items", lambda: _patched(
        "t40_edit_order_items", (
            OS, "if products[pid].stock_quantity + held.get(pid, 0) < quantity:",
            "if products[pid].stock_quantity < quantity:"))),
    "t40-keeps-old-prices": ("t40_edit_order_items", lambda: _patched(
        "t40_edit_order_items",
        (OS, "    order.items.clear()",
         "    old_prices = {i.product_id: i.price_at_purchase for i in order.items}\n"
         "    order.items.clear()"),
        (OS, "        price = products[item.product_id].price\n",
         "        price = old_prices.get(item.product_id, products[item.product_id].price)\n"))),
    "t40-status-before-ownership": ("t40_edit_order_items", lambda: _patched(
        "t40_edit_order_items", (
            OS, "    order = get_own_order(session, current_user, order_id)\n"
                "    if order.status != OrderStatus.PENDING:\n"
                '        raise OrderError("Only pending orders can be changed")\n',
            "    order = get_order(session, order_id)\n"
            "    if order.status != OrderStatus.PENDING:\n"
            '        raise OrderError("Only pending orders can be changed")\n'
            "    order = get_own_order(session, current_user, order_id)\n"))),
    # t41: deactivates before refusing, restores only its own lines, counts inactive products
    "t41-deactivates-before-refusing": ("t41_close_shop", lambda: _patched(
        "t41_close_shop",
        (VS, "    if any(o.status in (OrderStatus.CONFIRMED, OrderStatus.SHIPPED) "
             "for o in orders.values()):\n"
             '        raise ShopBusyError("Shop has orders being fulfilled")\n\n', ""),
        (VS, "    cancelled = 0\n",
         "    session.commit()\n"
         "    if any(o.status in (OrderStatus.CONFIRMED, OrderStatus.SHIPPED) "
         "for o in orders.values()):\n"
         '        raise ShopBusyError("Shop has orders being fulfilled")\n'
         "    cancelled = 0\n"))),
    "t41-restores-only-own-lines": ("t41_close_shop", lambda: _patched("t41_close_shop", (
        VS, "        for item in order.items:\n",
        "        for item in order.items:\n"
        "            if item.product_id not in product_ids:\n                continue\n"))),
    "t41-counts-inactive-products": ("t41_close_shop", lambda: _patched("t41_close_shop", (
        VS, "        if product.is_active:\n            product.is_active = False\n"
            "            deactivated += 1\n",
        "        deactivated += 1\n        if product.is_active:\n"
        "            product.is_active = False\n"))),
    # t42: new order created before validating, current prices, no every-item check
    "t42-creates-before-validating": ("t42_split_order", lambda: _patched(
        "t42_split_order",
        (OS, "    lines = {item.id: item for item in order.items}\n",
         "    new_order = order_repository.create_order(session, current_user.id)\n"
         "    session.commit()\n"
         "    lines = {item.id: item for item in order.items}\n"),
        (OS, "    new_order = order_repository.create_order(session, current_user.id)\n"
             "    for item_id in sorted(moving):",
         "    for item_id in sorted(moving):"))),
    "t42-current-prices": ("t42_split_order", lambda: _patched("t42_split_order", (
        OS, "        i.price_at_purchase * i.quantity for iid, i in lines.items() if iid in moving",
        "        session.get(Product, i.product_id).price * i.quantity\n"
        "        for iid, i in lines.items() if iid in moving"))),
    "t42-no-every-item-check": ("t42_split_order", lambda: _patched("t42_split_order", (
        OS, '    if moving == set(lines):\n        raise OrderError("Cannot move every item")\n',
        ""))),
    # t43: registers (and commits) before checking names, role unchanged, names by case
    "t43-registers-before-checking": ("t43_vendor_onboarding", lambda: _patched(
        "t43_vendor_onboarding",
        (VS, "    seen: set[str] = set()\n    for data in products:\n"
             "        if data.name in seen:\n"
             '            raise VendorError(f"Duplicate product name: {data.name}")\n'
             "        seen.add(data.name)\n\n", ""),
        (VS, "    session.add(current_user)\n    created = [",
         "    session.add(current_user)\n    session.commit()\n"
         "    seen: set[str] = set()\n    for data in products:\n"
         "        if data.name in seen:\n"
         '            raise VendorError(f"Duplicate product name: {data.name}")\n'
         "        seen.add(data.name)\n    created = ["))),
    "t43-role-unchanged": ("t43_vendor_onboarding", lambda: _patched(
        "t43_vendor_onboarding",
        (VS, "    session.flush()\n    current_user.role = Role.VENDOR\n",
         "    session.flush()\n"))),
    "t43-names-ignore-case": ("t43_vendor_onboarding", lambda: _patched(
        "t43_vendor_onboarding",
        (VS, "        if data.name in seen:", "        if data.name.lower() in seen:"),
        (VS, "        seen.add(data.name)", "        seen.add(data.name.lower())"))),
    # t44: merges order by order with commits, restores stock, keeps the others' totals
    "t44-merges-as-it-validates": ("t44_merge_orders", lambda: _patched("t44_merge_orders", (
        OS, "    orders = []\n    for order_id in order_ids:\n"
            "        order = get_own_order(session, current_user, order_id)\n"
            "        if order.status != OrderStatus.PENDING:\n"
            '            raise OrderError("Only pending orders can be merged")\n'
            "        orders.append(order)\n",
        "    orders = []\n    for order_id in order_ids:\n"
        "        order = get_own_order(session, current_user, order_id)\n"
        "        if order.status != OrderStatus.PENDING:\n"
        '            raise OrderError("Only pending orders can be merged")\n'
        "        if orders and order.id != orders[0].id:\n"
        "            for item in list(order.items):\n"
        "                item.order_id = orders[0].id\n"
        "                session.add(item)\n"
        "            order.status = OrderStatus.CANCELLED\n"
        "            order.total_amount = 0\n"
        "            session.add(order)\n"
        "            session.commit()\n"
        "        orders.append(order)\n"))),
    "t44-restores-stock": ("t44_merge_orders", lambda: _patched("t44_merge_orders", (
        OS, "        for item in list(other.items):\n",
        "        for item in list(other.items):\n"
        "            session.get(Product, item.product_id).stock_quantity += item.quantity\n"))),
    "t44-keeps-other-totals": ("t44_merge_orders", lambda: _patched("t44_merge_orders", (
        OS, "        other.total_amount = 0\n", ""))),
}  # fmt: skip
TRAPS.update(TRAPS_1B)
CAUGHT_BY.update(
    {
        "t39-commits-each-item": "t39.own_then_foreign",
        "t39-counts-entries": "t39.repeated_id",
        "t39-no-ownership": "t39.foreign_first",
        "t40-writes-before-checking": "t40.not_enough_writes_nothing",
        "t40-ignores-held-stock": "t40.held_stock_counts",
        "t40-keeps-old-prices": "t40.repriced_line",
        "t40-status-before-ownership": "t40.another_buyers_shipped",
        "t41-deactivates-before-refusing": "t41.vicky_busy_writes_nothing",
        "t41-restores-only-own-lines": "t41.victor_closes_multi_vendor_order",
        "t41-counts-inactive-products": "t41.vicky_closes",
        "t42-creates-before-validating": "t42.later_item_missing_writes_nothing",
        "t42-current-prices": "t42.totals_at_purchase_prices",
        "t42-no-every-item-check": "t42.every_item",
        "t43-registers-before-checking": "t43.duplicate_name_writes_nothing",
        "t43-role-unchanged": "t43.two_products",
        "t43-names-ignore-case": "t43.names_differ_by_case",
        "t44-merges-as-it-validates": "t44.later_foreign_writes_nothing",
        "t44-restores-stock": "t44.two_orders",
        "t44-keeps-other-totals": "t44.two_orders",
    }
)


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


def test_restoring_stock_with_or_without_a_version_bump_is_correct():
    files = _patched("t36_cancel_order", (
        OS, "        product.stock_quantity += item.quantity\n",
        "        product.stock_quantity += item.quantity\n"
        "        product.version += 1\n"))  # fmt: skip
    outcome = oracle.evaluate("t36_cancel_order", load_variant(files, repo="multivendor").make_app)
    assert outcome.kind == "correct", outcome.render()


@pytest.mark.parametrize(
    ("tid", "path", "old", "new"),
    [
        ("t32_monthly_report", OR, 'month: str = Query(pattern=r"^\\d{4}-(0[1-9]|1[0-2])$"),',
         "month: str = Query(),"),
        ("t36_cancel_order", OS,
         'f"Cannot transition order from {order.status.value} to {OrderStatus.CANCELLED.value}"',
         'f"Cannot cancel order with status {order.status.value}"'),
    ],
)  # fmt: skip
def test_an_unstated_error_body_is_not_compared(tid, path, old, new):
    """The pilot's correction: t32 states only the 422, t36 no message at all."""
    files = _patched(tid, (path, old, new))
    if tid == "t32_monthly_report":  # validate by hand, with the candidates' own message
        files = _patched_files(
            files,
            OS,
            '    year, number = (int(part) for part in month.split("-"))\n',
            "    import re\n    from fastapi import HTTPException\n"
            '    if not re.fullmatch(r"\\d{4}-(0[1-9]|1[0-2])", month):\n'
            "        raise HTTPException(\n"
            '            status_code=422, detail="Invalid month format, expected YYYY-MM")\n'
            '    year, number = (int(part) for part in month.split("-"))\n',
        )
    outcome = oracle.evaluate(tid, load_variant(files, repo="multivendor").make_app)
    assert outcome.kind == "correct", outcome.render()


def test_the_ambiguous_admin_case_is_excluded():
    names = {c.name for c in oracle.ticket_oracle("t36_cancel_order").cases}
    assert "t36.admin_any_order" in names and "t36.admin_shipped" not in names


def _patched_files(files: dict[str, str], path: str, old: str, new: str) -> dict[str, str]:
    assert files[path].count(old) == 1, (path, old)
    return {**files, path: files[path].replace(old, new)}


# ---- the smoke run and arm P --------------------------------------------------------------------


@pytest.mark.parametrize("tid", ALL)
def test_the_smoke_run_passes_on_each_reference(tid):
    result = smoke.run_smoke(tid, lambda p: make_reference_app(tid, p))
    assert result.started and not result.failed, result.render()
    assert len(result.lines) == len(smoke.SMOKE[tid])
    assert {r.user for r in smoke.SMOKE[tid]} - {None} <= set(repo("multivendor").users())


@pytest.mark.parametrize("tid", ALL)
def test_arm_p_prompt_is_about_the_marketplace_and_carries_no_trap(tid):
    t = ticket(tid)
    app = repo("multivendor")
    system, user = run.p_prompt(tid)
    assert app.package in system and "full-stack FastAPI template" not in system
    assert t.text.strip() in user
    for rel in (*t.files, *app.context):
        assert f"### app/{rel}\n```python\n" in user, rel
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


@pytest.mark.parametrize(
    ("tid", "spelled_out"),
    [
        ("t39_bulk_price", ["all or nothing", "atomic", "nothing changes", "once", "rolled"]),
        ("t40_edit_order_items", ["nothing", "atomic", "already hold", "held"]),
        ("t41_close_shop", ["nothing", "before", "other vendor", "every line", "once"]),
        ("t42_split_order", ["nothing", "before", "price_at_purchase", "current price",
                             "stock"]),
        ("t43_vendor_onboarding", ["nothing", "role", "atomic", "rolled", "case"]),
        ("t44_merge_orders", ["nothing", "before", "stock", "atomic", "validated"]),
    ],
)  # fmt: skip
def test_the_atomicity_traps_are_not_spelled_out(tid, spelled_out):
    text = ticket(tid).text.lower()
    assert not [w for w in spelled_out if w in text]


def test_six_atomicity_tickets():
    assert [t.repo for t in OPTION1B_TICKETS] == ["multivendor"] * 6
    assert run._tickets("option1b") == list(OPTION1B)
    for t in OPTION1B_TICKETS:
        root = REFERENCE_ROOT / t.id
        own = {p.relative_to(root).as_posix() for p in root.rglob("*.py")
               if "__pycache__" not in p.parts}  # fmt: skip
        assert set(t.files) == own, t.id
