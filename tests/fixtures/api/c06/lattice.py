# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/admin/users/{user_id}", auth=Admin)
def get_user(user_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    users = fetch(Users)
    found = where(users, col("id") == user_id)
    account = one(found, else_=NotFound("user"))
    shown = select(account, cols=["id", "email", "name", "role"])
    return shown
