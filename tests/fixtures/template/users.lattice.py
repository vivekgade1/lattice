# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# The FastAPI template's users routes read_users, read_user_me and update_user_me
# (bench/template_app/app/api/routes/users.py).
@endpoint("GET", "/api/v1/users/", auth=Admin, response="UserPublic")
def read_users(
    user: CurrentUser, skip: Int(ge=0) = 0, limit: Int(ge=0, le=100) = 100
) -> Page | BadRequest:
    active = require(user, col("is_active"), else_=BadRequest("Inactive user"))
    users = fetch(User)
    public = select(
        users, cols=["email", "is_active", "is_superuser", "full_name", "id", "created_at"]
    )
    ordered = sort(public, by=["created_at"], desc=True)
    result = page(ordered, limit=limit, offset=skip, total=True)
    return result


@endpoint("GET", "/api/v1/users/me", auth=Customer, response="UserPublic")
def read_user_me(user: CurrentUser) -> One | BadRequest:
    active = require(user, col("is_active"), else_=BadRequest("Inactive user"))
    shown = select(
        user, cols=["email", "is_active", "is_superuser", "full_name", "id", "created_at"]
    )
    return shown


@endpoint("PATCH", "/api/v1/users/me", auth=Customer, response="UserPublic")
def update_user_me(
    user: CurrentUser, full_name: Str(max_len=255) = UNSET, email: Str(max_len=255) = UNSET
) -> One | BadRequest:
    active = require(user, col("is_active"), else_=BadRequest("Inactive user"))
    updated = update(user, full_name=full_name, email=email)
    shown = select(
        updated, cols=["email", "is_active", "is_superuser", "full_name", "id", "created_at"]
    )
    return shown
