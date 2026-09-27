# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/orders/{order_id}/items", auth=Customer)
def order_items(
    order_id: Int(ge=1),
    user: CurrentUser,
    limit: Int(ge=1, le=100) = 20,
    offset: Int(ge=0) = 0,
) -> Page | NotFound:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    found = where(mine, col("id") == order_id)
    order = one(found, else_=NotFound("order"))
    items = fetch(OrderItems)
    my_items = owned(items, by=user)
    of_order = where(my_items, col("order_id") == order_id)
    shown = select(of_order, cols=["id", "product_id", "qty", "unit_price"])
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
