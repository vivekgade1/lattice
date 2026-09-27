# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("DELETE", "/products/{product_id}", auth=Admin)
def delete_product(product_id: Int(ge=1), user: CurrentUser) -> NoContent | NotFound:
    products = fetch(Products)
    found = where(products, col("id") == product_id)
    product = one(found, else_=NotFound("product"))
    gone = delete(product)
    return gone
