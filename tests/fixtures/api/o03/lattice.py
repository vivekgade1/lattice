# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("DELETE", "/addresses/{address_id}", auth=Customer)
def delete_address(address_id: Int(ge=1), user: CurrentUser) -> NoContent | NotFound:
    addresses = fetch(Addresses)
    mine = owned(addresses, by=user)
    found = where(mine, col("id") == address_id)
    address = one(found, else_=NotFound("address"))
    gone = delete(address)
    return gone
