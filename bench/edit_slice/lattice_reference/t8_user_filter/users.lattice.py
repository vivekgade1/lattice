# lattice: v1
# ruff: noqa: E501, F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.template_app.app.api.routes.users by lattice.importer (deterministic lifters, verified).
@endpoint("GET", "/api/v1/users/", auth=Admin, response="UserPublic")
def read_users(
    user: CurrentUser,
    skip: Int(ge=0) = 0,
    limit: Int(ge=0, le=100) = 100,
    is_active: Bool | None = None,
    q: Str | None = None,
) -> Page:
    users = fetch(User)
    matching = where(
        users,
        (is_null(is_active) | (col("is_active") == is_active))
        & (is_null(q) | fn.contains(col("email"), sub=q, ignore_case=True)),
    )
    public = select(
        matching,
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
