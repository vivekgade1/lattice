# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.template_app.app.api.routes.items by lattice.importer (deterministic lifters, verified).
@endpoint("GET", "/api/v1/items/", auth=Customer, response="ItemPublic")
def read_items(user: CurrentUser, skip: Int(ge=0) = 0, limit: Int(ge=0, le=100) = 100) -> Page:
    items = fetch(Item)
    visible = accessible(items, by=user)
    ordered = sort(visible, by=["created_at"], desc=True)
    result = page(ordered, limit=limit, offset=skip, total=True)
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


@endpoint("POST", "/api/v1/items/{id}/duplicate", auth=Customer, response="ItemPublic")
def duplicate_item(id: Uuid, user: CurrentUser) -> One | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    allowed = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    prepped = derive(allowed, new_title=col("title") + " (copy)")
    copy = insert(
        Item,
        owner=user,
        title=col("new_title"),
        description=col("description"),
        created_at=fn.now(),
    )
    return copy
