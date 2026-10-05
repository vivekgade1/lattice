"""The Flask + SQLAlchemy front end on the vendored flasky API (Phase 2 plan, W2-B):
Flask-SQLAlchemy's live metadata, the ``api`` blueprint (prefix ``/api/v1``) guarded by a
``before_request`` under ``HTTPBasicAuth.login_required`` plus an unconfirmed-account
check, the package's own ``permission_required(Permission.X)`` decorator (probed per
role), the ``roles`` table's permission bitmask mapped to role names, owner evidence on
``g.current_user`` and the AuthModel (Basic login, the token sent as the Basic username)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from lattice.importer.appmodel import Guard
from lattice.importer.frontends.flask_app import FRONT_END

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE = "bench.import_repos.flasky.app"

LOGIN = Guard("authenticated", "auth.login_required", status=401, detail="Invalid credentials")
CONFIRMED = Guard("active", "not g.current_user.confirmed", status=403,
                  detail="Unconfirmed account")  # fmt: skip
EVERY_ROLE = ("User", "Moderator", "Admin")


@pytest.fixture(scope="module")
def model():
    return FRONT_END.extract(REPO_ROOT, PACKAGE)


def _route(model, method, path):
    return next(r for r in model.routes if r.method == method and r.path == path)


def _guards(route):
    out = []
    for g in route.guards:
        assert g.line > 0, g
        out.append(dataclasses.replace(g, line=0))
    return out


def test_tables(model):
    assert (model.framework, model.orm) == ("flask", "sqlalchemy")
    assert {t.name: t.table_name for t in model.tables} == {
        "Role": "roles", "Follow": "follows", "User": "users", "Post": "posts",
        "Comment": "comments",
    }  # fmt: skip
    users = next(t for t in model.tables if t.name == "User")
    cols = {c.name: c for c in users.columns}
    assert cols["role_id"].foreign_key == "roles.id"
    assert (cols["email"].max_length, cols["email"].unique) == (64, True)
    assert (cols["confirmed"].type, cols["confirmed"].default) == ("Bool", "False")
    assert cols["about_me"].type == "Str" and cols["about_me"].max_length is None
    follows = next(t for t in model.tables if t.name == "Follow")
    assert [c.name for c in follows.columns if c.primary_key] == ["follower_id", "followed_id"]


FLASKY_ROUTES = {
    ("POST", "/api/v1/tokens/"), ("GET", "/api/v1/posts/"), ("GET", "/api/v1/posts/{id}"),
    ("POST", "/api/v1/posts/"), ("PUT", "/api/v1/posts/{id}"), ("GET", "/api/v1/users/{id}"),
    ("GET", "/api/v1/users/{id}/posts/"), ("GET", "/api/v1/users/{id}/timeline/"),
    ("GET", "/api/v1/comments/"), ("GET", "/api/v1/comments/{id}"),
    ("GET", "/api/v1/posts/{id}/comments/"), ("POST", "/api/v1/posts/{id}/comments/"),
}  # fmt: skip


def test_routes(model):
    assert {(r.method, r.path) for r in model.routes} == FLASKY_ROUTES
    assert [(e.method, e.path, e.kind) for e in model.excluded_routes] == [
        ("GET", "/static/{filename}", "framework")
    ]
    posts = _route(model, "GET", "/api/v1/posts/")
    assert [(p.name, p.type, p.source, p.default) for p in posts.params] == [
        ("page", "Int", "query", "1")
    ]


def test_role_bitmask(model):
    scheme = model.role_scheme
    assert scheme is not None
    assert scheme.column == "role_id"
    assert [(value, name) for value, _, name in scheme.roles] == [
        ("User", "User"), ("Moderator", "Moderator"), ("Administrator", "Admin"),
    ]  # fmt: skip
    assert [stored for _, stored, _ in scheme.roles] == ["1", "2", "3"]  # roles.id
    assert scheme.admin == "Admin"
    auth = model.auth
    assert (auth.role_table, auth.role_column) == ("Role", "role_id")
    assert dict(auth.permissions) == {
        "Permission.FOLLOW": 1, "Permission.COMMENT": 2, "Permission.WRITE": 4,
        "Permission.MODERATE": 8, "Permission.ADMIN": 16,
    }  # fmt: skip
    grants = dict(auth.role_permissions)
    assert grants["User"] == ("Permission.FOLLOW", "Permission.COMMENT", "Permission.WRITE")
    assert grants["Moderator"] == (*grants["User"], "Permission.MODERATE")
    assert grants["Admin"] == (*grants["Moderator"], "Permission.ADMIN")


def test_guards(model):
    for method, path in FLASKY_ROUTES - {
        ("POST", "/api/v1/posts/"), ("PUT", "/api/v1/posts/{id}"),
        ("POST", "/api/v1/posts/{id}/comments/"),
    }:  # fmt: skip
        route = _route(model, method, path)
        assert _guards(route) == [LOGIN, CONFIRMED], (method, path)
        assert route.auth == "Authenticated", (method, path)
    write = Guard("role", "permission_required(Permission.WRITE)", status=403,
                  detail="Insufficient permissions", roles=EVERY_ROLE)  # fmt: skip
    assert _guards(_route(model, "POST", "/api/v1/posts/")) == [LOGIN, CONFIRMED, write]
    comment = dataclasses.replace(write, source="permission_required(Permission.COMMENT)")
    new_comment = _route(model, "POST", "/api/v1/posts/{id}/comments/")
    assert _guards(new_comment) == [LOGIN, CONFIRMED, comment]
    assert new_comment.auth == "Authenticated"  # every role holds COMMENT
    edit = _route(model, "PUT", "/api/v1/posts/{id}")
    owner = Guard("object_owner", "g.current_user != post.author", status=403,
                  detail="Insufficient permissions", roles=("Admin",))  # fmt: skip
    assert _guards(edit) == [LOGIN, CONFIRMED, write, owner]


def test_owner_evidence(model):
    def uses(method, path):
        return {(u.table, u.column, u.how) for u in _route(model, method, path).owner_uses}

    assert uses("POST", "/api/v1/posts/") == {("Post", "author_id", "assign")}
    assert uses("PUT", "/api/v1/posts/{id}") == {("Post", "author_id", "compare")}
    assert uses("POST", "/api/v1/posts/{id}/comments/") == {("Comment", "author_id", "assign")}
    assert uses("GET", "/api/v1/posts/") == set()


def test_error_sites(model):
    get = _route(model, "GET", "/api/v1/posts/{id}")
    assert [(s.status, s.kind) for s in get.raises] == [(404, "abort")]
    new = _route(model, "POST", "/api/v1/posts/")
    assert (400, "post does not have a body", "handler") in [
        (s.status, s.detail, s.kind) for s in new.raises
    ]
    edit = _route(model, "PUT", "/api/v1/posts/{id}")
    assert [(s.status, s.detail, s.kind) for s in edit.raises] == [
        (404, None, "abort"), (403, "Insufficient permissions", "return"),
    ]  # fmt: skip
    token = _route(model, "POST", "/api/v1/tokens/")
    assert [(s.status, s.detail, s.kind) for s in token.raises] == [
        (401, "Invalid credentials", "return")
    ]


def test_auth_model(model):
    auth = model.auth
    assert (auth.login_path, auth.login_encoding, auth.token_path, auth.scheme) == (
        "/api/v1/tokens/", "basic", ("token",), "basic_token",
    )  # fmt: skip
    assert (auth.users_table, auth.username_field, auth.email_column) == ("User", "email", "email")
    assert (auth.password_column, auth.active_column) == ("password_hash", "confirmed")
    assert auth.hasher == "werkzeug.security:generate_password_hash"
