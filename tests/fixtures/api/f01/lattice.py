# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/orders", auth=Customer)
def list_orders(user: CurrentUser, limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0) -> Page:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    items = fetch(OrderItems)
    my_items = owned(items, by=user)
    per_order = group(my_items, by=["order_id"], units=agg.sum(col("qty")))
    keyed = rename(per_order, order_id="id")
    joined = join(mine, keyed, on=["id"], how="left")
    filled = derive(joined, units=coalesce(col("units"), 0))
    shown = select(filled, cols=["id", "status", "created_at", "total", "units"])
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
