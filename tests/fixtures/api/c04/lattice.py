# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/categories/{category}/products", auth=Public)
def category_products(
    category: Str(max_len=40), limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0
) -> Page:
    products = fetch(Products)
    in_category = where(products, col("category") == category)
    shown = select(in_category, cols=["id", "name", "price"])
    ordered = sort(shown, by=["price", "id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
