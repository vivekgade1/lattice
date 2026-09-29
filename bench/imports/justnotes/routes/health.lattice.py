# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.justnotes.app.api.health by lattice.importer (deterministic lifters and the LLM loop: health; every route verified).
@endpoint("GET", "/health", auth=Public)
def health() -> One:
    users = fetch(User)
    base = group(users, by=[], n=agg.count())
    info = derive(base, status="healthy", app_name="JustNotes", version="0.0.1")
    slim = select(info, cols=["status", "app_name", "version"])
    return slim
