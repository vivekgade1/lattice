# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("POST", "/orders/{order_id}/cancel", auth=Customer)
def cancel_order(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound | Conflict:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    found = where(mine, col("id") == order_id)
    order = one(found, else_=NotFound("order"))
    pending = require(order, col("status") == "pending", else_=Conflict("order is not pending"))
    done = update(pending, status="cancelled")
    shown = select(done, cols=["id", "status"])
    return shown
