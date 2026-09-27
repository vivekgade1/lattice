# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/admin/customers/{user_id}/orders", auth=Admin)
def customer_orders(
    user_id: Int(ge=1),
    user: CurrentUser,
    limit: Int(ge=1, le=100) = 20,
    offset: Int(ge=0) = 0,
) -> Page:
    orders = fetch(Orders)
    theirs = where(orders, col("customer_id") == user_id)
    shown = select(theirs, cols=["id", "status", "created_at", "total"])
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
