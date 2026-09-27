# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# CLOSEST EXPRESSIBLE PROGRAM (language finding): the JSON body field is `zip`, but an
# endpoint parameter's name is its wire name and `zip` is rejected with L205 ("shadows a
# Python builtin"). Renaming the parameter checks clean but changes the API contract
# (the body must then carry `zip_code`), so this program cannot pass the task's cases.
@endpoint("POST", "/addresses", auth=Customer)
def create_address(
    line1: Str(min_len=1, max_len=100),
    city: Str(min_len=1, max_len=60),
    zip_code: Str(min_len=5, max_len=5),
    user: CurrentUser,
) -> Created:
    row = insert(Addresses, owner=user, line1=line1, city=city, zip=zip_code)
    shown = select(row, cols=["id", "customer_id", "line1", "city", "zip"])
    return shown
