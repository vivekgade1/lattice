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
) -> One | BadRequest:
    check = fetch(Item)
    visible = accessible(check, by=user)
    with_stripped = derive(visible, stripped_title=fn.strip(title))
    validated = require(with_stripped, fn.len(col("stripped_title")) > 0, else_=BadRequest("Title must not be blank"))
    item = insert(Item, owner=user, title=fn.strip(title), description=description, created_at=fn.now())
    return item


@endpoint("PUT", "/api/v1/items/{id}", auth=Customer, response="ItemPublic")
def update_item(
    id: Uuid,
    user: CurrentUser,
    title: Str(min_len=1, max_len=255) = UNSET,
    description: Str(max_len=255) = UNSET,
) -> One | NotFound | Forbidden | BadRequest:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    allowed = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    with_stripped = derive(allowed, stripped_title=when(sent(title), then_=fn.strip(title), otherwise_=col("title")))
    validated = require(with_stripped, ~sent(title) | (fn.len(col("stripped_title")) > 0), else_=BadRequest("Title must not be blank"))
    updated = update(validated, title=when(sent(title), then_=fn.strip(title), otherwise_=col("title")), description=description)
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
