# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/admin/reviews", auth=Admin)
def moderation_queue(
    user: CurrentUser, limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0
) -> Page:
    reviews = fetch(Reviews)
    users = fetch(Users)
    user_names = select(users, cols=["id", "name"])
    reviewers = rename(user_names, id="customer_id", name="reviewer_name")
    products = fetch(Products)
    product_names = select(products, cols=["id", "name"])
    reviewed = rename(product_names, id="product_id", name="product_name")
    with_reviewer = join(reviews, reviewers, on=["customer_id"], how="left")
    with_product = join(with_reviewer, reviewed, on=["product_id"], how="left")
    shown = select(with_product, cols=["id", "rating", "body", "reviewer_name", "product_name"])
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
