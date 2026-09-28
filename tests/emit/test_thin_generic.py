"""The thin emitter mounted into an app that is not the template
(``lattice.emit.thin.emit_into_app``): the same route-preserving merge, over the routes
of any app a factory builds (included routers with their prefixes, routes on the app
itself), with the profile's NativeContext settings read from that app
(``settings_from_app``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lattice.emit.thin import app_routes, emit_into_app, make_emitted_app
from tests.emit import generic_app as G

FACTORY = "tests.emit.generic_app:make_app"
LIFTED = ("GET /api/things/{thing_id}", "DELETE /api/things/{thing_id}")


def _seed(path: Path) -> Path:
    G.make_app(path)  # creates the table
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO things (id, name) VALUES (?, ?)", [(1, "a"), (2, "b")])
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def emitted(tmp_path: Path):
    return emit_into_app(
        {"things.lattice.py": G.PROGRAM}, G.PROFILE_SOURCE, tmp_path / "out",
        app_factory=FACTORY, lifted=LIFTED,
    )  # fmt: skip


def test_the_apps_routes_are_listed_with_their_full_paths():
    routes = app_routes(FACTORY)
    for r in ("GET /api/things/", "GET /api/things/{thing_id}", "DELETE /api/things/{thing_id}",
              "GET /health"):  # fmt: skip
        assert r in routes


def test_emit_reports_the_replaced_and_kept_routes(emitted):
    assert set(emitted.routes_emitted) == {"GET /api/things/{id}", "GET /api/things/count"}
    # the lifted DELETE is gone even though the edited program no longer defines it
    assert set(emitted.routes_replaced) == {
        "GET /api/things/{thing_id}", "DELETE /api/things/{thing_id}"}  # fmt: skip
    assert "GET /api/things/" in emitted.routes_kept and "GET /health" in emitted.routes_kept


def test_the_merged_app_serves_emitted_and_kept_routes(emitted, tmp_path: Path):
    app = make_emitted_app(emitted.out_dir, _seed(tmp_path / "app.db"))
    with TestClient(app) as c:
        # an emitted route, its native reading the app's own setting
        assert c.get("/api/things/1").json() == {"id": 1, "name": "a", "greeting": "Hello, a"}
        assert c.get("/api/things/9").status_code == 404
        # a new literal route is not shadowed by the kept parameter route
        assert c.get("/api/things/count").json() == {"n": 2}
        # kept routes: the original's code (under its router prefix), and the app's own
        assert c.get("/api/things/").json()[0]["source"] == "original"
        assert c.get("/health").json() == {"status": "ok"}
        # a dropped lifted route is gone (405: the path still has a GET)
        assert c.delete("/api/things/1").status_code == 405


def test_a_new_nullable_column_is_added_to_the_apps_database(emitted, tmp_path: Path):
    db = _seed(tmp_path / "app.db")
    app = make_emitted_app(emitted.out_dir, db)
    assert app.state.schema_changes == ['ALTER TABLE "things" ADD COLUMN "note" VARCHAR']
    cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(things)")]
    assert cols == ["id", "name", "note"]


def test_each_app_is_independent(emitted, tmp_path: Path):
    a = make_emitted_app(emitted.out_dir, _seed(tmp_path / "a.db"))
    b_db = _seed(tmp_path / "b.db")
    sqlite3.connect(b_db).execute("DELETE FROM things WHERE id = 2").connection.commit()
    b = make_emitted_app(emitted.out_dir, b_db)
    with TestClient(a) as ca, TestClient(b) as cb:
        assert ca.get("/api/things/count").json() == {"n": 2}
        assert cb.get("/api/things/count").json() == {"n": 1}
    # the original module-level router is untouched
    assert {r.path for r in G.router.routes} == {"/things/", "/things/{thing_id}"}
