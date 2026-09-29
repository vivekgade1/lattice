# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.multivendor.app.routers.user_router by lattice.importer (deterministic lifters, verified).
@endpoint("GET", "/users/me", auth=Authenticated, response="UserRead")
def read_own_profile(user: CurrentUser) -> One:
    shown = select(user, cols=["id", "full_name", "email", "role", "is_active", "created_at"])
    return shown
