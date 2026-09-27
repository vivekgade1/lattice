"""`mine(t, by=user)`: the caller's own rows only, for every role (Admin included).

The checker treats it like `owned(t, by=user)` (it is at least as restrictive): it
satisfies L802, it takes `by=user`, it needs rows of one fetched table with an owner
column (or owned through a parent), and an owned-through write may look its parent up
through it. `mine` stays usable as a variable name in programs that never call it.
"""

from __future__ import annotations

from textwrap import dedent

from lattice.check import check_source
from lattice.diagnostics import Severity


def _check(src: str):
    from bench.api_datasets import api_profile

    return check_source("# lattice: v1\n" + dedent(src).lstrip("\n"), api_profile())


def errors(src: str) -> list[str]:
    return [d.code for d in _check(src).diagnostics if d.severity is Severity.ERROR]


def diag(src: str, code: str):
    found = [d for d in _check(src).diagnostics if d.code == code]
    assert found, _check(src).diagnostics
    return found[0]


MY_ORDERS = """
@endpoint("GET", "/orders/mine", auth=Customer)
def my_orders(user: CurrentUser, limit: Int(ge=1, le=100) = 20) -> Page:
    orders = fetch(Orders)
    own = mine(orders, by=user)
    ordered = sort(own, by=["id"])
    result = page(ordered, limit=limit)
    return result
"""


def test_mine_is_accepted_and_satisfies_l802():
    assert errors(MY_ORDERS) == []
    leaky = MY_ORDERS.replace("own = mine(orders, by=user)\n    ", "").replace(
        "sort(own,", "sort(orders,"
    )
    assert errors(leaky) == ["L802"]


def test_mine_through_a_parent_is_accepted():
    src = """
    @endpoint("GET", "/order_items/mine", auth=Customer)
    def my_items(user: CurrentUser) -> Page:
        items = fetch(OrderItems)
        own = mine(items, by=user)
        ordered = sort(own, by=["id"])
        result = page(ordered, limit=50)
        return result
    """
    assert errors(src) == []


def test_mine_before_one_update_and_delete_is_ok():
    src = """
    @endpoint("DELETE", "/orders/{order_id}", auth=Customer)
    def remove(order_id: Int(ge=1), user: CurrentUser) -> NoContent | NotFound:
        orders = fetch(Orders)
        own = mine(orders, by=user)
        found = where(own, col("id") == order_id)
        order = one(found, else_=NotFound("order"))
        gone = delete(order)
        return gone
    """
    assert errors(src) == []


def test_mine_looks_up_the_parent_of_an_owned_through_insert():
    src = """
    @endpoint("POST", "/orders/{order_id}/items", auth=Customer)
    def add_item(
        order_id: Int(ge=1), product_id: Int(ge=1), qty: Int(ge=1, le=100), user: CurrentUser
    ) -> Created | NotFound:
        orders = fetch(Orders)
        own = mine(orders, by=user)
        found = where(own, col("id") == order_id)
        order = one(found, else_=NotFound("order"))
        row = insert(OrderItems, order_id=order_id, product_id=product_id, qty=qty, unit_price=1.0)
        return row
    """
    assert errors(src) == []


def test_mine_filters_by_the_caller():
    src = MY_ORDERS.replace("mine(orders, by=user)", "mine(orders, by=limit)")
    d = diag(src, "L404")
    assert "mine()" in d.message and d.hint == "write by=user"


def test_mine_needs_a_table_with_an_owner():
    src = MY_ORDERS.replace("fetch(Orders)", "fetch(Products)")
    d = diag(src, "L404")
    assert d.message.startswith("mine() needs rows of a table with an owner column")
    assert errors(src) == ["L404"]


def test_mine_over_a_join_is_l404():
    src = """
    @endpoint("GET", "/orders/mine", auth=Customer)
    def my_orders(user: CurrentUser) -> Page:
        orders = fetch(Orders)
        products = fetch(Products)
        joined = join(orders, products, on={"id": "id"})
        own = mine(joined, by=user)
        result = page(own, limit=5)
        return result
    """
    d = diag(src, "L404")
    assert "mine() must filter the rows of one fetched table" in d.message
    assert d.hint is not None and "right after fetch, before joining" in d.hint


def test_mine_outside_an_endpoint_is_refused_like_owned():
    src = """
    orders = fetch(Orders)
    own = OP(orders, by=user)
    """
    got = [d.code for d in _check(src.replace("OP", "mine")).diagnostics]
    assert got and got == [d.code for d in _check(src.replace("OP", "owned")).diagnostics]


# ---- `mine` as a variable name ---------------------------------------------------------


def test_mine_stays_a_variable_name_in_programs_that_do_not_call_it():
    src = """
    @endpoint("GET", "/orders", auth=Customer)
    def list_orders(user: CurrentUser) -> Page:
        orders = fetch(Orders)
        mine = owned(orders, by=user)
        ordered = sort(mine, by=["id"])
        result = page(ordered, limit=20)
        return result
    """
    assert errors(src) == []


def test_binding_mine_where_mine_is_called_is_l205():
    src = """
    @endpoint("GET", "/orders", auth=Customer)
    def list_orders(user: CurrentUser) -> Page:
        orders = fetch(Orders)
        mine = mine(orders, by=user)
        result = page(mine, limit=20)
        return result
    """
    d = diag(src, "L205")
    assert "mine" in d.message
    assert "L205" in errors(src)
