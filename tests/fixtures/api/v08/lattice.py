# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("POST", "/products/{product_id}/restock", auth=Admin)
def restock(product_id: Int(ge=1), qty: Int(ge=1, le=1000), user: CurrentUser) -> One | NotFound:
    products = fetch(Products)
    found = where(products, col("id") == product_id)
    product = one(found, else_=NotFound("product"))
    done = update(product, stock=col("stock") + qty)
    shown = select(done, cols=["id", "stock"])
    return shown
