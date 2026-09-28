# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.justnotes.app.api.users by lattice.importer (deterministic lifters and the LLM loop: list_users; every route verified).
@endpoint("GET", "/user/list", auth=Customer, response="UserResponse")
def list_users(user: CurrentUser) -> List:
    users = fetch(User)
    ordered = sort(users, by=["id"])
    slim = select(ordered, cols=["id", "username", "createdAt", "updatedAt"])
    result = limit(slim, n=100)
    return result
