# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/orders/{order_id}/units", auth=Customer)
def order_units(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    found = where(mine, col("id") == order_id)
    order = one(found, else_=NotFound("order"))
    items = fetch(OrderItems)
    my_items = owned(items, by=user)
    of_order = where(my_items, col("order_id") == order_id)
    summed = group(of_order, by=[], units=agg.sum(col("qty")))
    labelled = derive(summed, order_id=order_id, units=coalesce(col("units"), 0))
    shown = select(labelled, cols=["order_id", "units"])
    return shown
