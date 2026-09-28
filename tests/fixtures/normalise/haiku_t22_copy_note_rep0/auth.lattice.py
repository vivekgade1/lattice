# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.justnotes.app.api.auth by lattice.importer (deterministic lifters, verified).
@endpoint("GET", "/auth/me", auth=Customer, response="UserResponse")
def read_users_me(user: CurrentUser) -> One:
    shown = select(user, cols=["id", "username", "createdAt", "updatedAt"])
    return shown
