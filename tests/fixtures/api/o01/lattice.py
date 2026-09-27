# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/orders/{order_id}", auth=Customer)
def get_order(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    found = where(mine, col("id") == order_id)
    order = one(found, else_=NotFound("order"))
    shown = select(order, cols=["id", "status", "created_at", "total"])
    return shown
