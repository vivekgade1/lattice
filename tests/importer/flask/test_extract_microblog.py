"""The Flask + SQLAlchemy front end on the vendored microblog-api (Phase 2 plan, W2-B):
the AppModel facts -- tables from Alchemical's live metadata, routes from the url_map
with blueprint prefixes, APIFairy / Flask-HTTPAuth guards, owner evidence, error sites,
marshmallow bounds and the AuthModel (HTTP Basic login answering a Bearer token)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lattice.importer.appmodel import Guard, OwnerUse
from lattice.importer.frontends import flask_app
from lattice.importer.frontends.flask_app import FRONT_END

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE = "bench.import_repos.microblog.api"


@pytest.fixture(scope="module")
def model():
    return FRONT_END.extract(REPO_ROOT, PACKAGE)


def _route(model, method, path):
    return next(r for r in model.routes if r.method == method and r.path == path)


def _table(model, name):
    return next(t for t in model.tables if t.name == name)


def _col(table, name):
    return next(c for c in table.columns if c.name == name)


def test_detect_and_registry():
    from lattice.importer.frontends import front_ends, select

    assert FRONT_END.detect(REPO_ROOT, PACKAGE)
    assert not FRONT_END.detect(REPO_ROOT, "bench.import_repos.taskflow.app")
    assert FRONT_END in front_ends()
    assert select("flask") is FRONT_END


def test_framework_and_tables(model):
    assert (model.framework, model.orm) == ("flask", "sqlalchemy")
    assert {t.name: t.table_name for t in model.tables} == {
        "Token": "tokens",
        "User": "users",
        "Post": "posts",
        "followers": "followers",  # an association table (no class)
    }
    users = _table(model, "User")
    assert [c.name for c in users.columns] == [
        "id", "username", "email", "password_hash", "about_me", "first_seen", "last_seen",
    ]  # fmt: skip
    pk = _col(users, "id")
    assert (pk.type, pk.primary_key, pk.nullable, pk.required) == ("Int", True, False, False)
    username = _col(users, "username")
    assert (username.type, username.max_length, username.unique, username.index) == (
        "Str", 64, True, True,
    )  # fmt: skip
    assert (username.nullable, username.required) == (False, True)
    about = _col(users, "about_me")
    assert (about.max_length, about.nullable, about.required) == (140, True, False)
    seen = _col(users, "first_seen")
    assert (seen.type, seen.required, seen.default) == ("DateTime", False, "naive_utcnow()")
    assert users.bases == ("bench.import_repos.microblog.api.models:Updateable",)

    posts = _table(model, "Post")
    assert _col(posts, "user_id").foreign_key == "users.id"
    assert _col(posts, "text").max_length == 280
    followers = _table(model, "followers")
    assert [(c.name, c.primary_key, c.foreign_key) for c in followers.columns] == [
        ("follower_id", True, "users.id"),
        ("followed_id", True, "users.id"),
    ]
    assert model.constraints == ()  # no CheckConstraint, no composite unique


MICROBLOG_ROUTES = {
    ("POST", "/api/tokens"), ("PUT", "/api/tokens"), ("DELETE", "/api/tokens"),
    ("POST", "/api/tokens/reset"), ("PUT", "/api/tokens/reset"),
    ("GET", "/api/tokens/oauth2/{provider}"), ("POST", "/api/tokens/oauth2/{provider}"),
    ("POST", "/api/users"), ("GET", "/api/users"), ("GET", "/api/users/{id}"),
    ("GET", "/api/users/{username}"), ("GET", "/api/me"), ("PUT", "/api/me"),
    ("GET", "/api/me/following"), ("GET", "/api/me/followers"),
    ("GET", "/api/me/following/{id}"), ("POST", "/api/me/following/{id}"),
    ("DELETE", "/api/me/following/{id}"), ("GET", "/api/users/{id}/following"),
    ("GET", "/api/users/{id}/followers"), ("POST", "/api/posts"), ("GET", "/api/posts"),
    ("GET", "/api/posts/{id}"), ("GET", "/api/users/{id}/posts"), ("PUT", "/api/posts/{id}"),
    ("DELETE", "/api/posts/{id}"), ("GET", "/api/feed"),
}  # fmt: skip


def test_routes_and_exclusions(model):
    assert {(r.method, r.path) for r in model.routes} == MICROBLOG_ROUTES
    assert len(model.routes) == 27
    framework = {(e.method, e.path) for e in model.excluded_routes if e.kind == "framework"}
    assert framework == {
        ("GET", "/"), ("GET", "/docs"), ("GET", "/apispec.json"), ("GET", "/static/{filename}"),
    }  # fmt: skip
    unreachable = {(e.method, e.path): e.reason for e in model.excluded_routes
                   if e.kind == "unreachable"}  # fmt: skip
    assert set(unreachable) == {
        ("GET", "/api/tokens/oauth2/{provider}"), ("POST", "/api/tokens/oauth2/{provider}"),
    }  # fmt: skip
    assert all("network" in reason for reason in unreachable.values())

    get = _route(model, "GET", "/api/posts/{id}")
    assert (get.name, get.module) == ("get", "bench.import_repos.microblog.api.posts")
    assert get.view == "bench.import_repos.microblog.api.posts:get"
    assert [(p.name, p.type, p.source) for p in get.params] == [("id", "Int", "path")]
    by_name = _route(model, "GET", "/api/users/{username}")
    assert [(p.name, p.type, p.source) for p in by_name.params] == [("username", "Str", "path")]


def test_guards_and_auth(model):
    token = Guard("authenticated", "apifairy.authenticate(token_auth)", status=401)
    for method, path in MICROBLOG_ROUTES - {
        ("POST", "/api/tokens"), ("PUT", "/api/tokens"), ("POST", "/api/tokens/reset"),
        ("PUT", "/api/tokens/reset"), ("GET", "/api/tokens/oauth2/{provider}"),
        ("POST", "/api/tokens/oauth2/{provider}"), ("POST", "/api/users"),
    }:  # fmt: skip
        route = _route(model, method, path)
        guards = [dataclasses_no_line(g) for g in route.guards if g.kind != "object_owner"]
        assert guards == [token], (method, path)
        assert route.auth == "Customer", (method, path)
    register = _route(model, "POST", "/api/users")
    assert (register.guards, register.auth) == ((), "Public")
    login = _route(model, "POST", "/api/tokens")
    assert [dataclasses_no_line(g) for g in login.guards] == [
        Guard("authenticated", "apifairy.authenticate(basic_auth)", status=401)
    ]
    assert model.role_scheme is None  # one role: any registered user


def dataclasses_no_line(guard: Guard) -> Guard:
    import dataclasses

    assert guard.line > 0
    return dataclasses.replace(guard, line=0)


def test_owner_evidence(model):
    def uses(method, path):
        return {(u.table, u.column, u.how) for u in _route(model, method, path).owner_uses}

    assert uses("POST", "/api/posts") == {("Post", "user_id", "assign")}
    assert uses("PUT", "/api/posts/{id}") == {("Post", "user_id", "compare")}
    assert uses("DELETE", "/api/posts/{id}") == {("Post", "user_id", "compare")}
    assert uses("GET", "/api/me/following") == {("followers", "follower_id", "compare")}
    assert uses("GET", "/api/me/followers") == {("followers", "followed_id", "compare")}
    assert uses("GET", "/api/posts") == set()
    assert all(isinstance(u, OwnerUse) and u.line > 0
               for u in _route(model, "PUT", "/api/posts/{id}").owner_uses)  # fmt: skip
    put = _route(model, "PUT", "/api/posts/{id}")
    # the author check in the body: its not-owned answer, no role bypassing it
    owner = [dataclasses_no_line(g) for g in put.guards if g.kind == "object_owner"]
    assert owner == [Guard("object_owner", "post.author != token_auth.current_user()", status=403)]
    assert "Post" in put.tables


def test_error_sites(model):
    put = _route(model, "PUT", "/api/posts/{id}")
    assert [(s.status, s.detail, s.kind) for s in put.raises] == [
        (404, None, "abort"), (403, None, "abort"),
    ]  # fmt: skip
    follow = _route(model, "POST", "/api/me/following/{id}")
    assert [(s.status, s.kind) for s in follow.raises] == [(404, "abort"), (409, "abort")]
    me = _route(model, "PUT", "/api/me")
    assert [(s.status, s.kind) for s in me.raises] == [(400, "abort")]


def test_body_models_and_bounds(model):
    new = _route(model, "POST", "/api/posts")
    assert new.body_models == ("PostSchema",)
    assert (new.response_model, new.status_code) == ("PostSchema", 201)
    schemas = {m.name: m for m in model.models}
    post = {f.name: f for f in schemas["PostSchema"].fields}
    assert (post["text"].min_length, post["text"].max_length, post["text"].required) == (
        1, 280, True,
    )  # fmt: skip
    assert "id" not in post  # dump_only: never read from a body
    user = {f.name: f for f in schemas["UserSchema"].fields}
    assert (user["username"].min_length, user["username"].max_length) == (3, 64)
    assert (user["email"].type, user["email"].max_length) == ("Email", 120)
    assert (user["password"].min_length, user["password"].required) == (3, True)


def test_auth_model(model):
    auth = model.auth
    assert auth is not None
    assert (auth.login_path, auth.login_encoding, auth.token_path, auth.scheme) == (
        "/api/tokens", "basic", ("access_token",), "bearer",
    )  # fmt: skip
    assert (auth.users_table, auth.username_field, auth.email_column) == (
        "User", "username", "email",
    )  # fmt: skip
    assert auth.password_column == "password_hash"
    assert auth.hasher == "werkzeug.security:generate_password_hash"
    assert (auth.active_column, auth.admin_column, auth.role_column) == (None, None, None)


def test_fastapi_untouched():
    """The FastAPI front end is still the default and still first."""
    from lattice.importer.frontends import front_ends

    assert front_ends()[0].name == "fastapi"
    assert flask_app.FRONT_END.name == "flask"
