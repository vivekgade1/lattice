# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("POST", "/products/{product_id}/reserve", auth=Customer)
def reserve(
    product_id: Int(ge=1), qty: Int(ge=1, le=100), user: CurrentUser
) -> One | NotFound | Conflict:
    products = fetch(Products)
    found = where(products, col("id") == product_id)
    product = one(found, else_=NotFound("product"))
    enough = require(product, col("stock") >= qty, else_=Conflict("not enough stock"))
    done = update(enough, stock=col("stock") - qty)
    shown = select(done, cols=["id", "stock"])
    return shown
