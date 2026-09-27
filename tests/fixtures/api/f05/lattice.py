# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# CLOSEST EXPRESSIBLE PROGRAM (language finding): the endpoint is Public, but review
# statistics are derived from Reviews, an owned table, and L802 rejects any rows derived
# from an owned table reaching a non-Admin response without owned(..., by=user) -- even
# aggregated per product. The only way to make the program check is auth=Admin, which
# changes the contract (anonymous callers get 401), so it cannot pass the task's cases.
@endpoint("GET", "/products/ratings", auth=Admin)
def product_ratings(
    user: CurrentUser, limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0
) -> Page:
    products = fetch(Products)
    reviews = fetch(Reviews)
    per_product = group(
        reviews, by=["product_id"], review_count=agg.count(), avg_rating=agg.mean(col("rating"))
    )
    keyed = rename(per_product, product_id="id")
    joined = join(products, keyed, on=["id"], how="left")
    filled = derive(joined, review_count=coalesce(col("review_count"), 0))
    shown = select(filled, cols=["id", "name", "review_count", "avg_rating"])
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
