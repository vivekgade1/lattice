# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# CLOSEST EXPRESSIBLE PROGRAM (language finding): the JSON body field is `zip`, but an
# endpoint parameter's name is its wire name and `zip` is rejected with L205 ("shadows a
# Python builtin"). Renaming the parameter checks clean but changes the API contract
# (the body must then carry `zip_code`), so this program cannot pass the task's cases.
@endpoint("PUT", "/addresses/{address_id}", auth=Customer)
def replace_address(
    address_id: Int(ge=1),
    line1: Str(min_len=1, max_len=100),
    city: Str(min_len=1, max_len=60),
    zip_code: Str(min_len=5, max_len=5),
    user: CurrentUser,
) -> One | NotFound:
    addresses = fetch(Addresses)
    mine = owned(addresses, by=user)
    found = where(mine, col("id") == address_id)
    address = one(found, else_=NotFound("address"))
    done = update(address, line1=line1, city=city, zip=zip_code)
    shown = select(done, cols=["id", "line1", "city", "zip"])
    return shown
