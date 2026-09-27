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


@endpoint("GET", "/api/v1/users/item-counts", auth=Admin, response="UserItemCount")
def read_user_item_counts(
    user: CurrentUser, skip: Int(ge=0) = 0, limit: Int(ge=0, le=100) = 100
) -> Page:
    users = fetch(User)
    items = fetch(Item)
    per_owner = group(items, by=["owner_id"], item_count=agg.count())
    counted = join(users, per_owner, on={"id": "owner_id"}, how="left")
    filled = derive(counted, item_count=coalesce(col("item_count"), 0))
    shown = select(filled, cols=["id", "email", "item_count"])
    ordered = sort(shown, by=["item_count", "email"], desc=[True, False])
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
