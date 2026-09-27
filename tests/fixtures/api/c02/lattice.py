# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/me/addresses", auth=Customer)
def my_addresses(user: CurrentUser, limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0) -> Page:
    addresses = fetch(Addresses)
    mine = owned(addresses, by=user)
    shown = select(mine, cols=["id", "line1", "city", "zip"])
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
