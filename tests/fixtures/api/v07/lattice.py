# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("PATCH", "/me/name", auth=Customer)
def rename_me(name: Str(min_len=1, max_len=60), user: CurrentUser) -> One:
    done = update(user, name=name)
    shown = select(done, cols=["id", "email", "name"])
    return shown
