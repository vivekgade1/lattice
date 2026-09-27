"""The thin emitter (thin-slice plan node E2): Lattice programs -> a generated FastAPI
router package mounted into the vendored template app (``lattice.emit.thin``).

The end-to-end gates (the template differential through the emitted app, and a schema
edit on an existing template database) are in ``tests/template/test_template_emitted.py``.
"""

from __future__ import annotations

import ast
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bench.template_app.seed import build_template_db
from bench.template_profile import template_profile, template_program, template_sources
from lattice.api.codegen import compile_source_to_fastapi
from lattice.emit import EmitError, EmitResult, SchemaEvolutionError, emit_into_template
from lattice.emit.thin import evolve_schema, make_emitted_app
from lattice.typesys import Scalar, Schema

PROFILE_SRC = (Path(__file__).resolve().parents[2] / "bench" / "template_profile.py").read_text()


# ---- the codegen option -------------------------------------------------------------------


def _generate(**kw):
    result, code = compile_source_to_fastapi(
        template_program(), template_profile(), natives_module="bench.template_profile", **kw
    )
    assert code is not None, [d.render() for d in result.diagnostics]
    return code


def test_the_app_target_is_the_default_and_unchanged():
    assert _generate() == _generate(target="app", kit_module="platform_kit")


def test_the_router_target_registers_on_an_api_router_without_app_handlers():
    code = _generate(target="router", kit_module=".kit")
    ast.parse(code)
    assert "\nrouter = APIRouter()\n" in code
    assert "from .kit import get_db\n" in code
    assert "platform_kit" not in code
    assert "@app." not in code and "exception_handler" not in code
    assert '@router.get("/api/v1/items/", name="read_items")' in code


def test_an_unknown_target_is_rejected():
    with pytest.raises(Exception, match="target"):
        _generate(target="mount")


# ---- emitting -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def emitted(tmp_path_factory: pytest.TempPathFactory) -> EmitResult:
    out = tmp_path_factory.mktemp("emit") / "template_emit"
    return emit_into_template(template_sources(), template_profile(), out)


def test_emit_writes_a_package(emitted: EmitResult):
    names = sorted(p.name for p in emitted.files)
    assert names == ["__init__.py", "kit.py", "natives.py", "routes.py", "schema.json"]
    assert all(p.parent == emitted.out_dir and p.exists() for p in emitted.files)
    natives = (emitted.out_dir / "natives.py").read_text()
    assert "from bench.template_profile import NATIVES" in natives
    routes = (emitted.out_dir / "routes.py").read_text()
    assert "from .natives import" in routes and "from .kit import get_db" in routes


def test_emit_reports_size_and_routes(emitted: EmitResult):
    routes = (emitted.out_dir / "routes.py").read_text()
    assert emitted.lines == sum(
        len(p.read_text().splitlines()) for p in emitted.files if p.suffix == ".py"
    )
    assert emitted.lines > len(routes.splitlines()) > 0
    assert emitted.ast_nodes > 1000
    assert set(emitted.routes_emitted) == {
        "GET /api/v1/items/",
        "GET /api/v1/items/{id}",
        "GET /api/v1/users/",
        "GET /api/v1/users/me",
        "PATCH /api/v1/users/me",
        "POST /api/v1/items/",
        "POST /api/v1/login/access-token",
        "PUT /api/v1/items/{id}",
        "DELETE /api/v1/items/{id}",
    }
    # every route of the template's login, users and items routers is replaced
    assert "POST /api/v1/users/signup" in emitted.routes_replaced
    assert "POST /api/v1/password-recovery/{email}" in emitted.routes_replaced
    assert "DELETE /api/v1/items/{id}" in emitted.routes_replaced
    assert not any("/utils/" in r or "/private/" in r for r in emitted.routes_replaced)


def test_a_profile_source_is_written_and_its_natives_reexported(tmp_path: Path):
    result = emit_into_template(template_sources(), PROFILE_SRC, tmp_path / "from_src")
    assert (result.out_dir / "profile.py").read_text() == PROFILE_SRC
    assert "from .profile import NATIVES" in (result.out_dir / "natives.py").read_text()
    db = build_template_db(tmp_path / "seed.db")
    app = make_emitted_app(result.out_dir, db)
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/login/access-token",
            data={"username": "admin@example.com", "password": "adminpass123"},
        )
    assert r.status_code == 200 and r.json()["token_type"] == "bearer"
    app.state.engine.dispose()


def test_a_rejected_program_is_an_emit_error(tmp_path: Path):
    bad = dict(template_sources())
    bad["items.lattice.py"] = bad["items.lattice.py"].replace("fetch(Item)", "fetch(Nope)", 1)
    with pytest.raises(EmitError) as err:
        emit_into_template(bad, template_profile(), tmp_path / "bad")
    assert err.value.diagnostics
    assert not (tmp_path / "bad" / "routes.py").exists()


# ---- the mounted app ------------------------------------------------------------------------


def test_the_mounted_app_keeps_the_rest_of_the_template(emitted: EmitResult, tmp_path: Path):
    db = build_template_db(tmp_path / "seed.db")
    app = make_emitted_app(emitted.out_dir, db)
    try:
        with TestClient(app) as client:
            doc = client.get("/api/v1/openapi.json").json()
            assert client.get("/api/v1/utils/health-check/").json() is True
            # the template's own 404 (not the generated app's error body)
            assert client.get("/api/v1/nowhere").json() == {"detail": "Not Found"}
        paths = {(m.upper(), p) for p, ops in doc["paths"].items() for m in ops}
        assert ("GET", "/api/v1/utils/health-check/") in paths  # an untouched router
        assert ("POST", "/api/v1/users/signup") not in paths  # replaced, not emitted
        assert {tuple(r.split(" ")) for r in emitted.routes_emitted} <= paths
        replaced_only = {tuple(r.split(" ")) for r in emitted.routes_replaced} - paths
        assert ("POST", "/api/v1/login/test-token") in replaced_only
        ids = {op["operationId"] for ops in doc["paths"].values() for op in ops.values()}
        # the template's operation ids (custom_generate_unique_id: f"{tag}-{name}")
        assert {"items-read_items", "login-login_access_token", "users-read_user_me"} <= ids
    finally:
        app.state.engine.dispose()


def test_the_template_module_is_left_as_it_was(emitted: EmitResult, tmp_path: Path):
    from bench.template_app import make_app
    from bench.template_app.app import main as template_main

    before = template_main.api_router
    app = make_emitted_app(emitted.out_dir, build_template_db(tmp_path / "e.db"))
    app.state.engine.dispose()
    assert template_main.api_router is before
    plain = make_app(tmp_path / "e.db")
    try:
        with TestClient(plain) as client:
            doc = client.get("/api/v1/openapi.json").json()
        assert "/api/v1/users/signup" in doc["paths"]
    finally:
        plain.state.engine.dispose()


def test_each_app_has_its_own_database(emitted: EmitResult, tmp_path: Path):
    a = make_emitted_app(emitted.out_dir, build_template_db(tmp_path / "a.db"))
    b = make_emitted_app(emitted.out_dir, build_template_db(tmp_path / "b.db"))
    try:
        for db in ("a", "b"):
            conn = sqlite3.connect(tmp_path / f"{db}.db")
            conn.execute("UPDATE user SET full_name = ?", (db,))
            conn.commit()
            conn.close()
        for app, name in ((a, "a"), (b, "b")):
            with TestClient(app) as client:
                token = client.post(
                    "/api/v1/login/access-token",
                    data={"username": "admin@example.com", "password": "adminpass123"},
                ).json()["access_token"]
                me = client.get(
                    "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
                ).json()
            assert me["full_name"] == name
    finally:
        a.state.engine.dispose()
        b.state.engine.dispose()


# ---- schema evolution -----------------------------------------------------------------------

S = Scalar


def _with_item_column(name: str, scalar: Scalar, nullable: bool):
    profile = template_profile()
    assert profile.api is not None
    item = profile.api.tables["Item"]
    cols = (*item.columns, (name, scalar))
    new = Schema(
        columns=cols,
        keys=item.keys,
        nullable=item.nullable | ({name} if nullable else set()),
        owner=item.owner,
    )
    tables = {**profile.api.tables, "Item": new}
    return replace(profile, api=replace(profile.api, tables=tables))


def _columns(db: Path, table: str) -> dict[str, tuple[str, int]]:
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    finally:
        conn.close()
    return {r[1]: (r[2], r[3]) for r in rows}


def test_the_template_schema_needs_no_change(tmp_path: Path):
    db = build_template_db(tmp_path / "seed.db")
    before = _columns(db, "item"), _columns(db, "user")
    assert evolve_schema(db, template_profile()) == []
    assert (_columns(db, "item"), _columns(db, "user")) == before


def test_a_nullable_column_is_added(tmp_path: Path):
    db = build_template_db(tmp_path / "seed.db")
    changes = evolve_schema(db, _with_item_column("deleted_at", S.DATETIME, nullable=True))
    assert changes == ['ALTER TABLE "item" ADD COLUMN "deleted_at" DATETIME']
    assert _columns(db, "item")["deleted_at"] == ("DATETIME", 0)
    assert evolve_schema(db, _with_item_column("deleted_at", S.DATETIME, nullable=True)) == []


def test_a_required_new_column_is_an_error(tmp_path: Path):
    db = build_template_db(tmp_path / "seed.db")
    with pytest.raises(SchemaEvolutionError, match="deleted_at.*nullable"):
        evolve_schema(db, _with_item_column("deleted_at", S.DATETIME, nullable=False))
    assert "deleted_at" not in _columns(db, "item")


def test_a_changed_column_type_is_an_error(tmp_path: Path):
    db = build_template_db(tmp_path / "seed.db")
    profile = template_profile()
    assert profile.api is not None
    item = profile.api.tables["Item"]
    cols = tuple((n, S.INT if n == "title" else s) for n, s in item.columns)
    tables = {**profile.api.tables, "Item": replace(item, columns=cols)}
    with pytest.raises(SchemaEvolutionError, match="title"):
        evolve_schema(db, replace(profile, api=replace(profile.api, tables=tables)))


def test_a_dropped_required_column_is_an_error(tmp_path: Path):
    db = build_template_db(tmp_path / "seed.db")
    profile = template_profile()
    assert profile.api is not None
    item = profile.api.tables["Item"]
    cols = tuple((n, s) for n, s in item.columns if n != "title")
    tables = {**profile.api.tables, "Item": replace(item, columns=cols)}
    with pytest.raises(SchemaEvolutionError, match="title"):
        evolve_schema(db, replace(profile, api=replace(profile.api, tables=tables)))


def test_a_missing_table_is_created(tmp_path: Path):
    db = build_template_db(tmp_path / "seed.db")
    profile = template_profile()
    assert profile.api is not None
    tag = Schema.from_pairs(
        [("id", S.UUID), ("name", S.STR), ("note", S.STR)], keys=[("id",)], nullable=["note"]
    )
    api = replace(
        profile.api,
        tables={**profile.api.tables, "Tag": tag},
        table_names={**profile.api.table_names, "Tag": "tag"},
    )
    changes = evolve_schema(db, replace(profile, api=api))
    assert len(changes) == 1 and changes[0].startswith('CREATE TABLE "tag"')
    assert _columns(db, "tag") == {
        "id": ("CHAR(32)", 1),
        "name": ("VARCHAR", 1),
        "note": ("VARCHAR", 0),
    }
