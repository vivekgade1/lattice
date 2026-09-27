# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Reviews is an owned table, but group(by=["product_id"]) mixes every customer's reviews,
# so the per-product statistics are not owner-private rows and a Public endpoint may
# return them.
@endpoint("GET", "/products/ratings", auth=Public)
def product_ratings(limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0) -> Page:
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
