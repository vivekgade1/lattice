# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("PUT", "/reviews/{review_id}", auth=Customer)
def update_review(
    review_id: Int(ge=1),
    rating: Int(ge=1, le=5),
    body: Str(min_len=1, max_len=500),
    user: CurrentUser,
) -> One | NotFound:
    reviews = fetch(Reviews)
    mine = owned(reviews, by=user)
    found = where(mine, col("id") == review_id)
    review = one(found, else_=NotFound("review"))
    done = update(review, rating=rating, body=body)
    shown = select(done, cols=["id", "product_id", "rating", "body"])
    return shown
