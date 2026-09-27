# lattice: v1
# ruff: noqa: E501, F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.template_app.app.api.routes.items by lattice.importer (deterministic lifters, verified).
@endpoint("GET", "/api/v1/items/", auth=Customer, response="ItemPublic")
def read_items(
    user: CurrentUser,
    skip: Int(ge=0) = 0,
    limit: Int(ge=0, le=100) = 100,
    since: DateTime = "0001-01-01T00:00:00Z",
    sort: OneOf("newest", "oldest") = "newest",
) -> Page:
    no_since = fn.to_datetime("0001-01-01 00:00", format="%Y-%m-%d %H:%M")
    items = fetch(Item)
    mine = owned(items, by=user)
    recent = where(mine, (col("created_at") >= since) | (since == no_since))
    keyed = derive(
        recent,
        undated_first=when((sort == "oldest") & ~is_null(col("created_at")), then=1, otherwise=0),
        oldest_key=when(sort == "oldest", then=col("created_at"), otherwise=no_since),
        newest_key=when(sort == "newest", then=col("created_at"), otherwise=no_since),
    )
    ordered = sort(keyed, by=["undated_first", "oldest_key", "newest_key"], desc=[False, False, True])
    shown = select(ordered, cols=["title", "description", "id", "owner_id", "created_at"])
    result = page(shown, limit=limit, offset=skip, total=True)
    return result


@endpoint("GET", "/api/v1/items/{id}", auth=Customer, response="ItemPublic")
def read_item(id: Uuid, user: CurrentUser) -> One | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    allowed = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    return allowed


@endpoint("POST", "/api/v1/items/", auth=Customer, response="ItemPublic")
def create_item(
    user: CurrentUser,
    title: Str(min_len=1, max_len=255),
    description: Str(max_len=255) = UNSET,
) -> One:
    item = insert(Item, owner=user, title=title, description=description, created_at=fn.now())
    return item


@endpoint("PUT", "/api/v1/items/{id}", auth=Customer, response="ItemPublic")
def update_item(
    id: Uuid,
    user: CurrentUser,
    title: Str(min_len=1, max_len=255) = UNSET,
    description: Str(max_len=255) = UNSET,
) -> One | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    allowed = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    updated = update(allowed, title=title, description=description)
    return updated


@endpoint("DELETE", "/api/v1/items/{id}", auth=Customer, response="Message")
def delete_item(id: Uuid, user: CurrentUser) -> One | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    allowed = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    gone = delete(allowed)
    said = derive(gone, message="Item deleted successfully")
    message = select(said, cols=["message"])
    return message
