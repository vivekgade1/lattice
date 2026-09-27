# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/categories", auth=Public)
def categories(limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0) -> Page:
    products = fetch(Products)
    counts = group(products, by=["category"], product_count=agg.count())
    ordered = sort(counts, by=["category"])
    result = page(ordered, limit=limit, offset=offset)
    return result
