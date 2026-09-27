# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# The parameter is named `zip` because a parameter's name is its wire name (JSON body key).
@endpoint("PUT", "/addresses/{address_id}", auth=Customer)
def replace_address(
    address_id: Int(ge=1),
    line1: Str(min_len=1, max_len=100),
    city: Str(min_len=1, max_len=60),
    zip: Str(min_len=5, max_len=5),
    user: CurrentUser,
) -> One | NotFound:
    addresses = fetch(Addresses)
    mine = owned(addresses, by=user)
    found = where(mine, col("id") == address_id)
    address = one(found, else_=NotFound("address"))
    done = update(address, line1=line1, city=city, zip=zip)
    shown = select(done, cols=["id", "line1", "city", "zip"])
    return shown
