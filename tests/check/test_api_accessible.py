"""`accessible(t, by=user)`: the preferred spelling of `owned(t, by=user)` (the rows the
caller may access; an Admin keeps every row). `owned` stays an alias. The checker treats
the two identically: accessible() satisfies L802, takes `by=user`, needs rows of one
fetched table with an owner column (L404) and may look up the parent row of an
owned-through write. Hints name `accessible`."""

from __future__ import annotations

from textwrap import dedent

import pytest

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


LIST = """
@endpoint("GET", "/orders", auth=Customer)
def list_orders(user: CurrentUser, limit: Int(ge=1, le=100) = 20) -> Page:
    orders = fetch(Orders)
    visible = OP(orders, by=user)
    ordered = sort(visible, by=["id"])
    result = page(ordered, limit=limit)
    return result
"""


@pytest.mark.parametrize("op", ["accessible", "owned"])
def test_accessible_and_owned_are_accepted(op: str) -> None:
    assert errors(LIST.replace("OP", op)) == []


def test_without_accessible_it_is_l802_and_the_hint_names_accessible() -> None:
    leaky = LIST.replace("visible = OP(orders, by=user)\n    ", "").replace(
        "sort(visible,", "sort(orders,"
    )
    d = diag(leaky, "L802")
    assert d.hint is not None and "orders = accessible(all_orders, by=user)" in d.hint


def test_accessible_filters_by_the_caller() -> None:
    d = diag(LIST.replace("OP(orders, by=user)", "accessible(orders, by=limit)"), "L404")
    assert "accessible()" in d.message and d.hint == "write by=user"


def test_accessible_needs_a_table_with_an_owner() -> None:
    src = LIST.replace("OP", "accessible").replace("fetch(Orders)", "fetch(Products)")
    d = diag(src, "L404")
    assert d.message.startswith("accessible() needs rows of a table with an owner column")


def test_accessible_over_a_join_is_l404() -> None:
    src = """
    @endpoint("GET", "/orders", auth=Customer)
    def list_orders(user: CurrentUser) -> Page:
        orders = fetch(Orders)
        products = fetch(Products)
        joined = join(orders, products, on={"id": "id"})
        visible = accessible(joined, by=user)
        result = page(visible, limit=5)
        return result
    """
    d = diag(src, "L404")
    assert "accessible() must filter the rows of one fetched table" in d.message


def test_accessible_looks_up_the_parent_of_an_owned_through_insert() -> None:
    src = """
    @endpoint("POST", "/orders/{order_id}/items", auth=Customer)
    def add_item(
        order_id: Int(ge=1), product_id: Int(ge=1), qty: Int(ge=1, le=100), user: CurrentUser
    ) -> Created | NotFound:
        orders = fetch(Orders)
        visible = accessible(orders, by=user)
        found = where(visible, col("id") == order_id)
        order = one(found, else_=NotFound("order"))
        row = insert(OrderItems, order_id=order_id, product_id=product_id, qty=qty, unit_price=1.0)
        return row
    """
    assert errors(src) == []


def test_accessible_stays_a_variable_name_in_programs_that_do_not_call_it() -> None:
    src = LIST.replace("visible", "accessible").replace("OP(", "owned(")
    assert errors(src) == []
