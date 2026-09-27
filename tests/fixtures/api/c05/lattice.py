# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("DELETE", "/reviews/{review_id}", auth=Customer)
def delete_review(review_id: Int(ge=1), user: CurrentUser) -> NoContent | NotFound:
    reviews = fetch(Reviews)
    mine = owned(reviews, by=user)
    found = where(mine, col("id") == review_id)
    review = one(found, else_=NotFound("review"))
    gone = delete(review)
    return gone
