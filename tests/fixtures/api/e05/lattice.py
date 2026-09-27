# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("POST", "/orders/{order_id}/ship", auth=Admin)
def ship_order(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound | Conflict:
    orders = fetch(Orders)
    found = where(orders, col("id") == order_id)
    order = one(found, else_=NotFound("order"))
    paid = require(order, col("status") == "paid", else_=Conflict("order is not paid"))
    done = update(paid, status="shipped")
    shown = select(done, cols=["id", "status"])
    return shown
