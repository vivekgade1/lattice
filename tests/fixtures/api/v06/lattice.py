# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/products", auth=Public)
def browse(
    category: OneOf("electronics", "home", "outdoor", "office", "kitchen", "sports"),
    limit: Int(ge=1, le=100) = 20,
    offset: Int(ge=0) = 0,
) -> Page:
    products = fetch(Products)
    active = where(products, col("active") & (col("category") == category))
    shown = select(active, cols=["id", "name", "price"])
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
