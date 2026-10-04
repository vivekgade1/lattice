"""Checker rule L835 (W1-B nested response models): a Nested field of a response model
reads public child rows of the returned rows. One test per diagnostic, plus the
profile's own validation of Nested entries."""

from __future__ import annotations

from dataclasses import replace

import pytest

from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.profile import ApiProfile, Nested, Profile
from lattice.typesys import Scalar, Schema
from tests.api import nested as F

ITEMS = Nested("items", "OrderItemRead", table="OrderItem", via="order_id")

READ_ORDER = """# lattice: v1
@endpoint("GET", "/orders/{order_id}", auth=Customer, response="OrderRead")
def read_order(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    orders = fetch(Order)
    visible = owned(orders, by=user)
    found = where(visible, col("id") == order_id)
    order = one(found, else_=NotFound("order not found"))
    shown = select(order, cols=["id", "status", "total_amount"])
    return shown
"""


def _api(**responses: tuple) -> ApiProfile:
    return replace(F.API, responses={**F.API.responses, **responses})


def _diags(source: str, api: ApiProfile) -> list[tuple[str, str, str | None]]:
    result = check_source(source, Profile(schemas={}, api=api))
    return [(d.code, d.message, d.hint) for d in result.diagnostics if d.severity is Severity.ERROR]


def _one_l835(source: str, api: ApiProfile) -> tuple[str, str | None]:
    diags = _diags(source, api)
    assert [c for c, _, _ in diags] == ["L835"], diags
    return diags[0][1], diags[0][2]


def test_the_fixture_checks() -> None:
    assert _diags(F.PROGRAM, F.API) == []


def test_unknown_model() -> None:
    api = _api(OrderRead=("id", "status", replace(ITEMS, model="OrderItemReed"), "total_amount"))
    message, hint = _one_l835(READ_ORDER, api)
    assert "unknown response model 'OrderItemReed'" in message
    assert hint is not None and "OrderItemRead" in hint


def test_nested_model_nests_again() -> None:
    api = _api(
        OrderRead=("id", "status", replace(ITEMS, model="Deep"), "total_amount"),
        Deep=("id", Nested("more", "OrderItemRead", table="OrderItem", via="order_id")),
    )
    message, hint = _one_l835(READ_ORDER, api)
    assert "has a nested field itself ('more')" in message
    assert hint == "remove Nested('more', ...) from responses['Deep']"


def test_unknown_table() -> None:
    api = _api(OrderRead=("id", "status", replace(ITEMS, table="OrderItems"), "total_amount"))
    message, hint = _one_l835(READ_ORDER, api)
    assert "unknown table 'OrderItems'" in message
    assert hint is not None and "OrderItem" in hint


def test_model_field_not_a_child_column() -> None:
    api = _api(OrderItemRead=("id", "sku"))
    message, hint = _one_l835(READ_ORDER, api)
    assert "lists 'sku', not column(s) of OrderItem" in message
    assert hint is not None and hint.startswith("remove them from responses['OrderItemRead']")


def test_private_child_column() -> None:
    api = _api(OrderItemRead=("id", "quantity", "cost"))
    message, hint = _one_l835(READ_ORDER, api)
    assert "would answer private column(s) of OrderItem: cost" in message
    assert hint == "list only public columns: responses['OrderItemRead'] = ('id', 'quantity',)"


def test_via_not_a_column() -> None:
    api = _api(OrderRead=("id", "status", replace(ITEMS, via="orderid"), "total_amount"))
    message, hint = _one_l835(READ_ORDER, api)
    assert "via='orderid' is not a column of OrderItem" in message
    assert hint is not None and "order_id" in hint


def test_child_with_its_own_owner() -> None:
    owned_items = replace(F.ORDER_ITEM, owner="product_id", owner_via=None)
    api = replace(_api(), tables={**F.API.tables, "OrderItem": owned_items})
    message, hint = _one_l835(READ_ORDER, api)
    assert "have their own owner column ('product_id')" in message
    assert hint is not None and "owner_via=('order_id', <parent table>)" in hint


READ_PRODUCT = """# lattice: v1
@endpoint("GET", "/products/{product_id}", auth=Public, response="ProductRead")
def read_product(product_id: Int(ge=1)) -> One | NotFound:
    products = fetch(Product)
    found = where(products, col("id") == product_id)
    product = one(found, else_=NotFound("product not found"))
    return product
"""


def test_parent_rows_are_computed() -> None:
    source = READ_PRODUCT.replace(
        "    return product",
        '    moved = derive(product, id=col("id") + 1)\n    return moved',
    )
    message, hint = _one_l835(source, F.API)
    assert "needs the returned rows to be rows of one table" in message
    assert hint is not None and hint.startswith("return the parent table's rows")


def test_parent_without_a_one_column_key() -> None:
    keyless = replace(F.PRODUCT, keys=())
    api = replace(F.API, tables={**F.API.tables, "Product": keyless})
    message, hint = _one_l835(READ_PRODUCT, api)
    assert "the parent table Product has no one-column key" in message
    assert hint == "declare a one-column key first in Product's schema (keys=[('id',), ...])"


def test_parent_key_not_returned() -> None:
    api = _api(OrderRead=("status", ITEMS, "total_amount"))
    source = READ_ORDER.replace(
        'cols=["id", "status", "total_amount"]', 'cols=["status", "total_amount"]'
    )
    message, hint = _one_l835(source, api)
    assert "matched on the parent key 'id', which the returned rows do not carry" in message
    assert hint == 'keep "id" in the returned columns and in the response model\'s fields'


def test_child_belongs_to_another_parent() -> None:
    api = _api(
        ProductRead=("id", "name", "in_stock",
                     Nested("lines", "OrderItemRead", table="OrderItem", via="product_id")),
    )  # fmt: skip
    message, hint = _one_l835(READ_PRODUCT, api)
    assert "rows of OrderItem belong to Order through 'order_id', not to Product" in message
    assert hint == "nest them under rows of Order with via='order_id'"


def test_link_column_of_another_type() -> None:
    images = Schema.from_pairs(
        [("id", Scalar.INT), ("product_id", Scalar.STR), ("url", Scalar.STR),
         ("primary", Scalar.BOOL)],
        keys=[("id",)],
    )  # fmt: skip
    api = replace(F.API, tables={**F.API.tables, "ProductImage": images})
    message, hint = _one_l835(READ_PRODUCT, api)
    assert "ProductImage.product_id (Str) cannot reference Product.id (Int)" in message
    assert hint == "use a column of ProductImage holding Product's key as via="


def test_the_model_s_columns_are_still_l834() -> None:
    """The column fields keep L834: a Nested field is not a returned column."""
    source = READ_ORDER.replace('cols=["id", "status", "total_amount"]', 'cols=["id", "status"]')
    diags = _diags(source, F.API)
    assert [c for c, _, _ in diags] == ["L834"]
    assert "missing 'total_amount'" in diags[0][1] and "Nested" not in diags[0][1]


@pytest.mark.parametrize(
    "fields, error",
    [
        (("id", Nested("id", "OrderItemRead", table="OrderItem", via="order_id")), "twice"),
        (("id", Nested("1x", "OrderItemRead", table="OrderItem", via="order_id")), "identifier"),
        (("id", Nested("items", "", table="OrderItem", via="order_id")), "non-empty"),
        (("id", 3), "names or Nested"),
    ],
)
def test_profile_validates_nested_entries(fields: tuple, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        replace(F.API, responses={"OrderRead": fields})
