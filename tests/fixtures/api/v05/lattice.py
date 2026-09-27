# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("POST", "/orders/{order_id}/status", auth=Admin)
def set_status(
    order_id: Int(ge=1),
    status: OneOf("pending", "paid", "shipped", "cancelled"),
    user: CurrentUser,
) -> One | NotFound:
    orders = fetch(Orders)
    found = where(orders, col("id") == order_id)
    order = one(found, else_=NotFound("order"))
    done = update(order, status=status)
    shown = select(done, cols=["id", "status"])
    return shown
