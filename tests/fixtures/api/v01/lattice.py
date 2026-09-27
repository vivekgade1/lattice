# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("POST", "/reviews", auth=Customer)
def create_review(
    product_id: Int(ge=1),
    rating: Int(ge=1, le=5),
    body: Str(min_len=1, max_len=500),
    user: CurrentUser,
) -> Created | NotFound:
    products = fetch(Products)
    found = where(products, col("id") == product_id)
    product = one(found, else_=NotFound("product"))
    row = insert(Reviews, owner=user, product_id=product_id, rating=rating, body=body)
    shown = select(row, cols=["id", "product_id", "customer_id", "rating", "body"])
    return shown
