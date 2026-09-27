# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/admin/customers", auth=Admin)
def customer_stats(user: CurrentUser, limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0) -> Page:
    users = fetch(Users)
    customers = where(users, col("role") == "Customer")
    orders = fetch(Orders)
    live = where(orders, col("status") != "cancelled")
    per_customer = group(
        live, by=["customer_id"], order_count=agg.count(), total_spent=agg.sum(col("total"))
    )
    keyed = rename(per_customer, customer_id="id")
    joined = join(customers, keyed, on=["id"], how="left")
    filled = derive(
        joined,
        order_count=coalesce(col("order_count"), 0),
        total_spent=coalesce(col("total_spent"), 0.0),
    )
    shown = select(filled, cols=["id", "name", "email", "order_count", "total_spent"])
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
