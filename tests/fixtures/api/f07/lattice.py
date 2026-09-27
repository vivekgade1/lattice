# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/orders/{order_id}/lines", auth=Customer)
def order_lines(
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
    of_order = where(items, col("order_id") == order_id)
    products = fetch(Products)
    names = select(products, cols=["id", "name"])
    keyed = rename(names, id="product_id", name="product_name")
    joined = join(of_order, keyed, on=["product_id"], how="left")
    shown = select(joined, cols=["id", "product_id", "product_name", "qty", "unit_price"])
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
