# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("POST", "/products/{product_id}/my-review", auth=Customer)
def review_product(
    product_id: Int(ge=1),
    rating: Int(ge=1, le=5),
    body: Str(min_len=1, max_len=500),
    user: CurrentUser,
) -> Created | NotFound | Conflict:
    products = fetch(Products)
    found = where(products, col("id") == product_id)
    product = one(found, else_=NotFound("product"))
    me = select(user, cols=["id"])
    me_as_author = rename(me, id="customer_id")
    reviews = fetch(Reviews)
    of_product = where(reviews, col("product_id") == product_id)
    by_me = join(of_product, me_as_author, on=["customer_id"], how="inner")
    counted = group(by_me, by=[], n=agg.count())
    first = require(counted, col("n") == 0, else_=Conflict("already reviewed"))
    row = insert(Reviews, owner=user, product_id=product_id, rating=rating, body=body)
    shown = select(row, cols=["id", "product_id", "customer_id", "rating", "body"])
    return shown
