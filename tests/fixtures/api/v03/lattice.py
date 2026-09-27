# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("POST", "/products", auth=Admin)
def create_product(
    name: Str(min_len=1, max_len=80),
    category: OneOf("electronics", "home", "outdoor", "office", "kitchen", "sports"),
    price: Float(ge=0.01, le=10000),
    stock: Int(ge=0, le=100000),
    user: CurrentUser,
) -> Created:
    row = insert(Products, name=name, category=category, price=price, stock=stock, active=True)
    shown = select(row, cols=["id", "name", "category", "price", "stock"])
    return shown
