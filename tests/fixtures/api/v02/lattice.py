# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# The parameter is named `zip` because a parameter's name is its wire name (JSON body key).
@endpoint("POST", "/addresses", auth=Customer)
def create_address(
    line1: Str(min_len=1, max_len=100),
    city: Str(min_len=1, max_len=60),
    zip: Str(min_len=5, max_len=5),
    user: CurrentUser,
) -> Created:
    row = insert(Addresses, owner=user, line1=line1, city=city, zip=zip)
    shown = select(row, cols=["id", "customer_id", "line1", "city", "zip"])
    return shown
