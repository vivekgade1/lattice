"""lattice.importer.outside (spec 2026-09-27 importer §3.6, S4 + S5): OpenAPI and SQLite
facts, and their reconciliation with the extractor's facts, on the template and on
synthetic apps and databases."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from bench.template_app import make_app
from bench.template_app.seed import build_template_db
from lattice.importer.extract import RepoModel, extract
from lattice.importer.outside import (
    DbFacts,
    Disagreement,
    OpenApiFacts,
    db_facts,
    openapi_facts,
    reconcile,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG = "bench.template_app.app"
API = "/api/v1"


@pytest.fixture(scope="module")
def template_db(tmp_path_factory) -> Path:
    return build_template_db(tmp_path_factory.mktemp("outside") / "seed.db")


@pytest.fixture(scope="module")
def template_openapi(template_db) -> OpenApiFacts:
    app = make_app(template_db)
    try:
        return openapi_facts(app)
    finally:
        app.state.engine.dispose()


@pytest.fixture(scope="module")
def template_dbf(template_db) -> DbFacts:
    return db_facts(template_db)


@pytest.fixture(scope="module")
def model() -> RepoModel:
    return extract(REPO_ROOT, PKG)


# ---- S4: OpenAPI -----------------------------------------------------------------------------


class Thing(BaseModel):
    name: str = Field(min_length=1, max_length=20)
    note: str | None = None


def test_endpoints_params_bounds_and_status_codes():
    app = FastAPI()

    @app.get("/things/{id}", response_model=Thing, responses={404: {"description": "gone"}})
    def read(id: int, q: str | None = Query(None, max_length=5), n: int = Query(3, ge=0, le=9)):
        return None

    @app.post("/things/", status_code=201)
    def create(body: Thing) -> Thing:
        return body

    facts = openapi_facts(app)
    read_ep = facts.endpoint("GET", "/things/{id}")
    assert read_ep.operation == "read"
    params = {p.name: p for p in read_ep.params}
    assert (params["id"].location, params["id"].type, params["id"].required) == (
        "path",
        "integer",
        True,
    )
    assert params["q"].nullable and params["q"].bounds == {"maxLength": 5}
    assert params["n"].default == 3 and params["n"].bounds == {"minimum": 0, "maximum": 9}
    assert read_ep.statuses == (200, 404, 422)
    assert read_ep.responses[200] == "Thing"
    create_ep = facts.endpoint("POST", "/things/")
    assert (create_ep.body, create_ep.body_media) == ("Thing", "application/json")
    assert create_ep.success == 201
    thing = {p.name: p for p in facts.schemas["Thing"].properties}
    assert thing["name"].required and thing["name"].bounds == {"minLength": 1, "maxLength": 20}
    assert thing["note"].nullable and not thing["note"].required


def test_the_template_openapi(template_openapi):
    login = template_openapi.endpoint("POST", f"{API}/login/access-token")
    assert login.body_media == "application/x-www-form-urlencoded" and not login.secured
    items = template_openapi.endpoint("GET", f"{API}/items/")
    assert items.secured and items.responses[200] == "ItemsPublic"
    assert {p.name: p.default for p in items.params} == {"skip": 0, "limit": 100}
    email = {p.name: p for p in template_openapi.schemas["UserUpdateMe"].properties}["email"]
    assert (email.type, email.format, email.nullable) == ("string", "email", True)


# ---- S5: the database ------------------------------------------------------------------------


def test_the_template_db(template_dbf):
    user, item = template_dbf.table("user"), template_dbf.table("item")
    assert user.primary_key == ("id",) and item.primary_key == ("id",)
    assert user.unique == (("email",),)
    fk = item.foreign_keys[0]
    assert (fk.columns, fk.table, fk.ref_columns, fk.on_delete) == (
        ("owner_id",), "user", ("id",), "CASCADE")  # fmt: skip
    cols = {c.name: c for c in user.columns}
    assert cols["id"].type == "CHAR(32)" and not cols["id"].nullable
    assert cols["full_name"].nullable and cols["email"].type == "VARCHAR(255)"
    assert cols["id"].storage == "hex32" and cols["created_at"].storage == "sql_space"
    assert {c.name: c.storage for c in item.columns}["owner_id"] == "hex32"
    assert user.rows == 4 and item.rows == 20


def test_storage_spellings_are_sampled(tmp_path: Path):
    db = tmp_path / "s.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE t (id TEXT PRIMARY KEY, at TEXT, at2 TEXT, mixed TEXT, word TEXT);
        INSERT INTO t VALUES ('3f2a7c1e-0000-4000-8000-000000000001', '2026-01-01T09:00:00Z',
            '2026-01-01T09:00:00+02:00', '3f2a7c1e000040008000000000000001', 'x');
        INSERT INTO t VALUES ('3f2a7c1e-0000-4000-8000-000000000002', '2026-01-02T09:00:00Z',
            NULL, '3f2a7c1e-0000-4000-8000-000000000002', 'y');
        """
    )
    conn.commit()
    conn.close()
    cols = {c.name: c for c in db_facts(db).table("t").columns}
    assert cols["id"].storage == "canonical"
    assert cols["at"].storage == "iso_z"
    assert cols["at2"].storage == "iso_offset"
    assert cols["mixed"].storage == "mixed(canonical,hex32)"
    assert cols["word"].storage is None


# ---- reconciliation --------------------------------------------------------------------------


def test_the_template_reconciles_with_one_explained_disagreement(
    model, template_openapi, template_dbf
):
    found = reconcile(model, template_openapi, template_dbf)
    # the private router is mounted only when FASTAPI_ENV == "development"
    # (bench/template_app/app/api/main.py); the extractor lists it, the served app does not.
    assert found == [
        Disagreement(
            "openapi",
            f"POST {API}/private/users/",
            "endpoint",
            "present",
            "absent",
            "the route is in the source but the running app does not serve it "
            "(a conditional include_router?)",
        )
    ]


def test_reconcile_reports_each_kind_of_difference(model, template_openapi, template_dbf):
    from dataclasses import replace

    user = next(t for t in model.tables if t.name == "User")
    cols = tuple(
        replace(c, nullable=True) if c.name == "email" else
        replace(c, unique=False) if c.name == "id" else c
        for c in user.columns
    )  # fmt: skip
    tables = tuple(replace(t, columns=cols) if t.name == "User" else t for t in model.tables)
    routes = tuple(
        replace(r, response_model="Message") if r.name == "read_items" else r for r in model.routes
    )
    upd = next(m for m in model.models if m.name == "UserUpdateMe")
    fields = tuple(replace(f, max_length=100) if f.name == "email" else f for f in upd.fields)
    models = tuple(replace(m, fields=fields) if m.name == "UserUpdateMe" else m
                   for m in model.models)  # fmt: skip
    bad = replace(model, tables=tables, routes=routes, models=models)
    found = {(d.source, d.subject, d.field) for d in reconcile(bad, template_openapi, template_dbf)}
    assert ("db", "user.email", "nullable") in found
    assert ("openapi", f"GET {API}/items/", "response") in found
    assert ("openapi", "UserUpdateMe.email", "maxLength") in found
    assert ("db", "user.id", "unique") not in found  # a primary key is unique anyway
