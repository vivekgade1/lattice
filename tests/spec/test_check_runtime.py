"""``lattice.spec.check`` through an app runtime (Phase 2 W3): layers D and R serve a WSGI
(Flask) app with the runtime's transport, log callers in with its auth driver, send
tokens in its scheme and dispose of every app with its hooks. Without a runtime nothing
changes (``tests/spec`` pins the FastAPI default)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from lattice.importer.transport import AppRuntime, AuthDriver, WsgiTransport
from lattice.profile import AdminFlag, ApiProfile, Profile, TokenColumn
from lattice.spec import Contract, ContractError, Scenario, Spec, Step, check
from lattice.typesys import Scalar, Schema

flask = pytest.importorskip("flask")

USERS = {"admin": (1, "admin@example.com", True), "alice": (2, "alice@example.com", False)}


def make_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, token TEXT, "
                "is_admin BOOLEAN)")  # fmt: skip
    for h, (i, email, admin) in USERS.items():
        con.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (i, email, f"tok-{h}", admin))
    con.commit()
    con.close()
    return path


def make_app(db: Path) -> Any:
    app = flask.Flask("toy")

    def caller() -> Any:
        auth = (flask.request.headers.get("X-Token") or "").split(" ")[-1]
        con = sqlite3.connect(db)
        try:
            return con.execute("SELECT id, is_admin FROM users WHERE token=?", (auth,)).fetchone()
        finally:
            con.close()

    @app.get("/admin-only")
    def admin_only() -> Any:
        row = caller()
        if row is None:
            return {"detail": "Not authenticated"}, 401
        if not row[1]:
            return {"detail": "Forbidden"}, 403
        return {"ok": True}

    return app


def _spec() -> Spec:
    users = Schema.from_pairs([("id", Scalar.INT), ("email", Scalar.STR), ("token", Scalar.STR),
                               ("is_admin", Scalar.BOOL)], keys=[("id",)])  # fmt: skip
    api = ApiProfile(tables={"User": users}, table_names={"User": "users"},
                     roles=frozenset({"Public", "Customer", "Admin"}), users_table="User",
                     auth=TokenColumn("token"), roles_from=AdminFlag("is_admin", "Customer"),
                     error_body="fastapi", forbidden_detail="Forbidden",
                     unauthorized_detail="Not authenticated")  # fmt: skip
    scenario = Scenario("only the admin", steps=(
        Step("GET", "/admin-only", expect_status=401),
        Step("GET", "/admin-only", user="alice", expect_status=403),
        Step("GET", "/admin-only", user="admin", expect_body={"ok": True}),
    ))  # fmt: skip
    return Spec(
        Profile(schemas={}, api=api),
        scenarios=(scenario,),
        users={h: (email, "pw") for h, (_, email, _) in USERS.items()},
        contracts=(
            Contract(
                "GET",
                "/admin-only",
                auth="Admin",
                errors=(ContractError(403, "Forbidden", when="forbidden"),),
            ),
        ),  # fmt: skip
    )


def _runtime(closed: list[Any]) -> AppRuntime:
    def mint(handle: str) -> str:
        return f"tok-{handle}"

    return AppRuntime(framework="flask", transport=WsgiTransport,
                      auth=AuthDriver(scheme="bearer", header="X-Token", mint=mint),
                      dispose=(closed.append,), seed_hook=None, openapi=None)  # fmt: skip


def test_scenarios_run_through_the_runtime(tmp_path):
    closed: list[Any] = []
    report = check(_spec(), make_app, make_db, layers="D", runtime=_runtime(closed))
    assert report.verdict == "pass", report.render()
    assert closed, "the runtime's dispose hooks ran"


def test_layer_r_runs_through_the_runtime(tmp_path):
    closed: list[Any] = []
    rt = _runtime(closed)

    def login(client: Any, handle: str) -> str:
        return f"tok-{handle}"

    report = check(_spec(), make_app, make_db, layers="R", runtime=rt, login=login,
                   probe_options={"auth_headers": lambda t: {"X-Token": t}})  # fmt: skip
    assert report.rules is not None
    assert report.rules.items["R1 GET /admin-only"].verdict == "verified"
    assert closed


def test_without_a_runtime_a_flask_app_is_not_served_as_asgi(tmp_path):
    """The default stays FastAPI's TestClient: a WSGI app cannot run there."""
    with pytest.raises(TypeError):
        check(_spec(), make_app, make_db, layers="D")
