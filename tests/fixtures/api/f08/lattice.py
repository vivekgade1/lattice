# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/admin/users", auth=Admin)
def list_users(
    user: CurrentUser,
    role: OneOf("Customer", "Admin") = "Customer",
    limit: Int(ge=1, le=100) = 20,
    offset: Int(ge=0) = 0,
) -> Page:
    users = fetch(Users)
    matching = where(users, col("role") == role)
    shown = select(matching, cols=["id", "email", "name", "role"])
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
