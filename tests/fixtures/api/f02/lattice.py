# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/admin/orders", auth=Admin)
def all_orders(user: CurrentUser, limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0) -> Page:
    orders = fetch(Orders)
    users = fetch(Users)
    names = select(users, cols=["id", "name"])
    customers = rename(names, id="customer_id", name="customer_name")
    joined = join(orders, customers, on=["customer_id"], how="left")
    shown = select(joined, cols=["id", "customer_id", "customer_name", "status", "total"])
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
