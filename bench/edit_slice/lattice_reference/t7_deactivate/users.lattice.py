# lattice: v1
# ruff: noqa: E501, F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.template_app.app.api.routes.users by lattice.importer (deterministic lifters, verified).
@endpoint("GET", "/api/v1/users/", auth=Admin, response="UserPublic")
def read_users(user: CurrentUser, skip: Int(ge=0) = 0, limit: Int(ge=0, le=100) = 100) -> Page:
    users = fetch(User)
    public = select(
        users,
        cols=["email", "is_active", "is_superuser", "full_name", "id", "created_at"],
    )
    ordered = sort(public, by=["created_at"], desc=True)
    result = page(ordered, limit=limit, offset=skip, total=True)
    return result


@endpoint("PATCH", "/api/v1/users/me", auth=Customer, response="UserPublic")
def update_user_me(
    user: CurrentUser,
    full_name: Str(max_len=255) = UNSET,
    email: Email(max_len=255) = UNSET,
) -> One | Conflict:
    updated = update(
        user,
        full_name=full_name,
        email=email,
        unique_or=Conflict("User with this email already exists"),
    )
    shown = select(
        updated,
        cols=["email", "is_active", "is_superuser", "full_name", "id", "created_at"],
    )
    return shown


@endpoint("GET", "/api/v1/users/me", auth=Customer, response="UserPublic")
def read_user_me(user: CurrentUser) -> One:
    shown = select(
        user,
        cols=["email", "is_active", "is_superuser", "full_name", "id", "created_at"],
    )
    return shown


@endpoint("POST", "/api/v1/users/{user_id}/deactivate", auth=Admin, response="UserPublic")
def deactivate_user(user_id: Uuid, user: CurrentUser) -> One | NotFound | Forbidden:
    users = fetch(User)
    found = where(users, col("id") == user_id)
    target = one(found, else_=NotFound("User not found"))
    me = select(user, cols=["id"])
    caller = rename(me, id="caller_id")
    judged = join(target, caller, on=[])
    not_self = require(
        judged,
        col("id") != col("caller_id"),
        else_=Forbidden("Super users are not allowed to deactivate themselves"),
    )
    updated = update(target, is_active=False)
    shown = select(
        updated,
        cols=["email", "is_active", "is_superuser", "full_name", "id", "created_at"],
    )
    return shown


@endpoint("POST", "/api/v1/users/{user_id}/activate", auth=Admin, response="UserPublic")
def activate_user(user_id: Uuid, user: CurrentUser) -> One | NotFound:
    users = fetch(User)
    found = where(users, col("id") == user_id)
    target = one(found, else_=NotFound("User not found"))
    updated = update(target, is_active=True)
    shown = select(
        updated,
        cols=["email", "is_active", "is_superuser", "full_name", "id", "created_at"],
    )
    return shown
