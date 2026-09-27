# lattice: v1
# ruff: noqa: E501, F821, F841 -- Lattice names are ambient; some bindings exist for their effect
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


@endpoint("POST", "/api/v1/items/{id}/transfer", auth=Customer, response="ItemPublic")
def transfer_item(
    id: Uuid, user: CurrentUser, new_owner_email: Email(max_len=255)
) -> One | NotFound | Forbidden | BadRequest:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    allowed = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    users = fetch(User)
    matched = where(users, col("email") == new_owner_email)
    target = one(matched, else_=NotFound("User not found"))
    active = require(
        target, col("is_active"), else_=BadRequest("Cannot transfer an item to an inactive user")
    )
    new_id = select(active, cols=["id"])
    renamed = rename(new_id, id="new_owner_id")
    paired = join(allowed, renamed, on=[])
    fresh = require(
        paired,
        col("owner_id") != col("new_owner_id"),
        else_=BadRequest("Item already belongs to this user"),
    )
    moved = update(allowed, owner=active)
    return moved
