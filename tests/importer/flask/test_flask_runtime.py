"""The Flask front end's ``AppRuntime`` (Phase 2 plan §3, W2-B): the original app served
over WSGI, every role logged in through the ``AuthDriver`` the AuthModel describes (HTTP
Basic login; a Bearer token for microblog, the token as the Basic username for flasky),
one route answered, and the engine disposed of so the database file is released.

(Named ``test_flask_runtime``, not ``test_runtime``: these test directories have no
``__init__``, so two ``test_runtime.py`` basenames under ``tests/importer`` would clash.)"""

from __future__ import annotations

import base64
import shutil
import sqlite3
from pathlib import Path

import pytest

from lattice.importer import transport
from lattice.importer.frontends.flask_app import FRONT_END, auth_driver

REPO_ROOT = Path(__file__).resolve().parents[3]


def _fresh(template: Path, tmp_path: Path, name: str) -> Path:
    db = tmp_path / name
    shutil.copyfile(template, db)
    return db


def test_basic_token_scheme_and_basic_login():
    driver = transport.AuthDriver(scheme="basic_token")
    raw = base64.b64encode(b"tok123:").decode()
    assert driver.headers("tok123") == {"Authorization": f"Basic {raw}"}
    assert transport.AuthDriver().headers("t") == {"Authorization": "Bearer t"}  # unchanged

    seen = {}

    class Client:
        def post(self, path, headers):
            seen["call"] = (path, headers)

            class R:
                status_code = 200
                text = ""

                def json(self):
                    return {"data": {"token": "T"}}

            return R()

    login = transport.basic_token_login(
        "/login", {"u": ("name", "pw")}, token_path=("data", "token")
    )
    assert login(Client(), "u") == "T"
    basic = base64.b64encode(b"name:pw").decode()
    assert seen["call"] == ("/login", {"Authorization": f"Basic {basic}"})
    built = transport.AuthDriver.basic_route(
        "/login", {}, token_path=("token",), scheme="basic_token"
    )
    assert (built.scheme, built.can_login) == ("basic_token", True)


@pytest.mark.parametrize("app", ["microblog", "flasky"])
def test_runtime_logs_in_every_role(app, tmp_path):
    import importlib

    seed = importlib.import_module(f"bench.import_repos.{app}.seed")
    package = {"microblog": "bench.import_repos.microblog.api",
               "flasky": "bench.import_repos.flasky.app"}[app]  # fmt: skip
    model = FRONT_END.extract(REPO_ROOT, package)
    runtime = FRONT_END.runtime(REPO_ROOT, package, model=model)
    assert runtime.framework == "flask"
    assert runtime.openapi is None
    template = seed.build_seed_db(tmp_path / "template.db")

    # the seed's credentials, as the AuthModel names them (Basic username column)
    column = model.auth.username_field
    if app == "microblog":
        creds = {h: (u if column == "username" else e, p)
                 for h, (u, e, p, _) in seed.SEED_USERS.items()}  # fmt: skip
    else:
        creds = {h: (e if column == "email" else u, p)
                 for h, (e, u, p, _, _) in seed.SEED_USERS.items()}  # fmt: skip
    driver = auth_driver(model.auth, creds)
    assert driver.can_login

    method, path, body = seed.AUTH_PROBE
    db = _fresh(template, tmp_path, "case.db")
    with runtime.serve(importlib.import_module(f"bench.import_repos.{app}").make_app, db) as (
        _,
        client,
    ):
        assert client.request(method, path, json=body).status == 401
        for role, handle in seed.ROLES.items():
            token = driver.token(client, handle)
            answer = client.request(method, path, json=body, headers=driver.present(client, token))
            assert answer.status == 200, (role, answer.status, answer.text[:200])
        for handle in seed.REFUSED:  # flasky's unconfirmed carol: refused at login
            with pytest.raises(RuntimeError, match="403"):
                driver.token(client, handle)
        bad = driver.headers("not-a-token")
        assert client.request(method, path, json=body, headers=bad).status == 401
    # disposed: the database file is released and readable
    with sqlite3.connect(db) as conn:
        assert conn.execute("select count(*) from users").fetchone()[0] == len(seed.SEED_USERS)


def test_error_site_predicate():
    import ast

    rt = FRONT_END.runtime(REPO_ROOT, "bench.import_repos.flasky.app")
    site = rt.error_site

    def first(src):
        return next(r for n in ast.walk(ast.parse(src)) if (r := site(n)) is not None)

    assert first("abort(404)") == (404, None)
    assert first("x = db.session.get(P, 1) or abort(403, 'nope')") == (403, "nope")
    assert first("Post.query.get_or_404(id)") == (404, None)
    assert first("raise Forbidden()") == (403, None)
    assert first("return forbidden('Insufficient permissions')") == (
        403,
        "Insufficient permissions",
    )
    assert first("return {'x': 1}, 409") == (409, None)
    assert first("raise ValidationError('post does not have a body')") == (
        400,
        "post does not have a body",
    )
    assert site(ast.parse("return {}, 200").body[0]) is None
    assert rt.error_class("werkzeug.exceptions:NotFound")
    assert not rt.error_class("builtins:ValueError")
