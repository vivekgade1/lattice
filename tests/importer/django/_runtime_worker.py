"""Worker for ``test_runtime.py`` (one Django project per process):

    python tests/importer/django/_runtime_worker.py APP OUTDIR

Seeds the vendored app (its own ``seed.build_seed_db``), extracts its AppModel with the
Django front end, takes the front end's ``AppRuntime`` and, through it alone (its
transport, its dispose hooks and an ``AuthDriver`` built from the AppModel's
``AuthModel``), sends the app's ``seed.AUTH_PROBE`` route anonymously, as every role and
as every refused user, each on a fresh copy of the seed. Prints one JSON object.
"""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any


def run(app: str, out: Path) -> dict[str, Any]:
    import lattice.importer.frontends.django_drf  # noqa: F401 -- registers the front end
    from lattice.importer.frontends import select
    from lattice.importer.transport import AuthDriver

    root = Path(__file__).resolve().parents[3]
    package_name = f"bench.import_repos.{app}"
    package = importlib.import_module(package_name)
    seed = importlib.import_module(f"{package_name}.seed")
    out.mkdir(parents=True, exist_ok=True)
    template = seed.build_seed_db(out / "template.db")
    copies = iter(range(1000))

    def fresh() -> Path:
        db = out / f"case-{next(copies)}.db"
        shutil.copyfile(template, db)
        return db

    fe = select(None, root, package_name)
    model = fe.extract(root, package_name)
    runtime = fe.runtime(root, package_name)
    credentials = {h: (u[0], u[1]) for h, u in seed.SEED_USERS.items()}
    driver = AuthDriver.from_model(model.auth, credentials)
    result: dict[str, Any] = {
        "front_end": fe.name,
        "framework": runtime.framework,
        "openapi": runtime.openapi is not None,
        "scheme": driver.scheme,
        "runtime_scheme": runtime.auth.scheme,
        "calls": {},
    }
    method, path, body = seed.AUTH_PROBE

    def call(who: str, token_of: Any) -> None:
        with runtime.serve(package.make_app, fresh()) as (_, transport):
            try:
                token = token_of(transport)
            except RuntimeError as err:
                result["calls"][who] = {"login_failed": str(err)[:120]}
                return
            headers = driver.present(transport, token)
            answer = transport.request(method, path, json=body, headers=headers)
            result["calls"][who] = {"status": answer.status, "body": answer.body}
        from django.db import connections

        result["disposed"] = connections["default"].connection is None

    call("anonymous", lambda t: None)
    for role, handle in seed.ROLES.items():
        call(role, lambda t, h=handle: driver.token(t, h))
    for handle in seed.REFUSED:
        call(handle, lambda t, h=handle: driver.token(t, h))
    mint = fe.minter(model.auth)
    result["minter"] = mint is not None
    if mint is not None:
        for handle in seed.REFUSED:
            call(f"{handle}-minted", lambda t, h=handle: mint(seed.USER_IDS[h]))
    # a bad credential, answered as the AuthModel's invalid_answer says
    call("bad-token", lambda t: "bm9ib2R5Ondyb25n")  # base64 "nobody:wrong"
    # the not-found answer of the generic get_object (the implicit 404 site's detail)
    method, path, body = NOT_FOUND[app]
    call("not-found", lambda t: driver.token(t, "alice"))
    result["model"] = {
        "invalid_answer": model.auth.invalid_answer,
        "not_found": next(
            s.detail
            for r in model.routes
            if r.method == method and r.path == PATTERN[app]
            for s in r.raises
            if s.status == 404
        ),
    }
    result["seeded"] = _seed_through_hook(runtime, model, out / "hooked.db")
    return result


# per app: a request answered by the generic get_object's 404, and its route pattern
NOT_FOUND = {
    "snippets": ("GET", "/snippets/999/", None),
    "recipes": ("GET", "/api/recipe/recipes/3/", None),  # bob's recipe, as alice
}
PATTERN = {"snippets": "/snippets/{pk}/", "recipes": "/api/recipe/recipes/{pk}/"}


def _seed_through_hook(runtime: Any, model: Any, path: Path) -> dict[str, int]:
    """The runtime's seed hook writes a plan through the Django models (rows keyed by
    SQL column; the schema migrated first): row counts per table after."""
    import sqlite3
    from datetime import UTC, datetime

    from lattice.importer.suite import SeedPlan

    users = next(t for t in model.tables if t.name == model.auth.users_table)
    login = model.auth.email_column
    now = datetime(2026, 1, 2, tzinfo=UTC)
    user = {"id": 7, "password": "x", "last_login": None, "is_superuser": False,
            login: "zed@example.com", "is_staff": False, "is_active": True}  # fmt: skip
    for c in users.columns:
        if c.name not in user:
            user[c.name] = now if c.type == "DateTime" else ""
    rows = {users.table_name: [user]}
    owned = next(t for t in model.tables if t.name in ("Snippet", "Tag"))
    row: dict[str, Any] = {}
    for c in owned.columns:
        row[c.name] = 7 if c.foreign_key else (now if c.type == "DateTime" else (
            False if c.type == "Bool" else ("python" if c.choices else "x")))  # fmt: skip
    row["id"] = 1
    rows[owned.table_name] = [row]
    hook = runtime.seed_hook(model.tables)
    hook(path, SeedPlan(users=(), rows=rows))
    con = sqlite3.connect(path)
    try:
        return {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in rows}
    finally:
        con.close()


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], Path(sys.argv[2])), default=str))
