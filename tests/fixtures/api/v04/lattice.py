# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("PATCH", "/products/{product_id}/price", auth=Admin)
def set_price(
    product_id: Int(ge=1), price: Float(ge=0.01, le=10000), user: CurrentUser
) -> One | NotFound:
    products = fetch(Products)
    found = where(products, col("id") == product_id)
    product = one(found, else_=NotFound("product"))
    done = update(product, price=price)
    shown = select(done, cols=["id", "price"])
    return shown
