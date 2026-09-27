# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/products/{product_id}/stock", auth=Customer)
def product_stock(product_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    products = fetch(Products)
    found = where(products, col("id") == product_id)
    product = one(found, else_=NotFound("product"))
    shown = select(product, cols=["id", "name", "stock"])
    return shown
