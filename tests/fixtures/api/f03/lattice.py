# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/catalog", auth=Public)
def catalog(limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0) -> Page:
    products = fetch(Products)
    shown = select(products, cols=["id", "name", "category", "price", "stock"])
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
