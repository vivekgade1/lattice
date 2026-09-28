# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.justnotes.app.api.health by lattice.importer (deterministic lifters and the LLM loop: health; every route verified).
APP_NAME = "JustNotes"
APP_VERSION = "0.0.1"

@endpoint("GET", "/health", auth=Public)
def health() -> One:
    users = fetch(User)
    summary = group(users, by=[], count=agg.count())
    enriched = derive(summary, status="healthy", app_name=APP_NAME, version=APP_VERSION)
    result = select(enriched, cols=["status", "app_name", "version"])
    return result
