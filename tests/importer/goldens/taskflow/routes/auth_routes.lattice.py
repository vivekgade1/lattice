# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.taskflow.app.auth_routes by lattice.importer (deterministic lifters, verified).
@endpoint("GET", "/api/v1/auth/me", auth=Customer, response="UserPublic")
def read_current_user(user: CurrentUser) -> One:
    shown = select(user, cols=["id", "email", "full_name", "is_active", "created_at"])
    return shown
