# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/addresses/{address_id}", auth=Customer)
def get_address(address_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    addresses = fetch(Addresses)
    mine = owned(addresses, by=user)
    found = where(mine, col("id") == address_id)
    address = one(found, else_=NotFound("address"))
    shown = select(address, cols=["id", "line1", "city", "zip"])
    return shown
