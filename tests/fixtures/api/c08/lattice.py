# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/me/reviews", auth=Customer)
def my_reviews(user: CurrentUser, limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0) -> Page:
    reviews = fetch(Reviews)
    mine = owned(reviews, by=user)
    shown = select(mine, cols=["id", "product_id", "rating", "body"])
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
