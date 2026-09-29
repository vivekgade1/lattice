"""lattice.gogen.proxy (plan 5a Task 13): a built Go app as a process (GoServer) and as
an ASGI app for the harnesses (go_app_factory)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from bench.api_datasets import TOKENS, api_profile, build_db
from lattice.gogen.build import build_go_app
from lattice.gogen.proxy import GoServer, go_app_factory
from tests.api import edges

SRC = (Path(__file__).resolve().parents[1] / "fixtures" / "api" / "c01" / "lattice.py").read_text()


@pytest.fixture(scope="module")
def binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_go_app(SRC, api_profile(), tmp_path_factory.mktemp("proxy")).binary


def test_go_server_starts_and_stops(binary: Path, tmp_path: Path):
    db = tmp_path / "app.db"
    build_db(db)
    with GoServer(binary, db) as server:
        proc = server.proc
        assert proc is not None and server.base_url.startswith("http://127.0.0.1:")
        r = httpx.get(server.base_url + "/no-such-route")
        assert r.status_code == 404 and r.headers["x-lattice-sql-count"] == "0"
    assert proc.poll() is not None  # terminated on exit


def test_go_server_without_trace_sends_no_count(binary: Path, tmp_path: Path):
    db = tmp_path / "app.db"
    build_db(db)
    with GoServer(binary, db, trace_sql=False) as server:
        assert "x-lattice-sql-count" not in httpx.get(server.base_url + "/x").headers


def test_the_factory_serves_through_asgi_and_stops_at_shutdown(binary: Path, tmp_path: Path):
    db = tmp_path / "app.db"
    build_db(db)
    counter = [0]
    app = go_app_factory(binary, sql_counter=counter)(db)
    with TestClient(app, raise_server_exceptions=False) as client:
        proc = app.server.proc
        assert proc is not None
        r = client.get("/products/1/stock", headers={"Authorization": f"Bearer {TOKENS['admin']}"})
        assert r.status_code in (200, 404, 405)  # whatever c01 answers: passed unchanged
        assert r.headers["content-type"] == "application/json"
        assert counter[0] == int(r.headers["x-lattice-sql-count"])
    assert proc.poll() is not None  # lifespan shutdown stopped the server


def test_redirects_reach_the_client_unfollowed(tmp_path: Path, tmp_path_factory):
    """A 307 passes through with Starlette's Location (the client's own host); a client
    that follows it lands on the route, and both requests' statements are counted."""
    built = build_go_app(edges.PROGRAM, edges.PROFILE, tmp_path_factory.mktemp("edges"))
    db = tmp_path / "app.db"
    edges.build_db(db)
    counter = [0]
    app = go_app_factory(built.binary, sql_counter=counter)(db)
    auth = {"Authorization": f"Bearer {TOKENS['admin']}"}
    with TestClient(app) as client:
        r = client.get("/reviews", params={"limit": 2}, headers=auth, follow_redirects=False)
        assert r.status_code == 307
        assert r.headers["location"] == "http://testserver/reviews/?limit=2"
        counter[0] = 0
        r = client.get("/reviews", params={"limit": 2}, headers=auth)
        assert r.status_code == 200 and len(r.json()["items"]) == 2
        assert counter[0] == 2  # the page and its total; the 307 ran none


def test_a_binary_that_does_not_start_is_an_error(tmp_path: Path):
    fake = tmp_path / "app"
    fake.write_text("#!/bin/sh\necho nope\n")
    fake.chmod(0o755)
    with pytest.raises(RuntimeError, match="did not start"), GoServer(fake, tmp_path / "x.db"):
        pass
