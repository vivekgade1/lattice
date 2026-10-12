"""GET /api/engines (API v1.14, spike 2): the database engines a project can check on and
its modules, on the real server and the mock; no write route; the API.md section."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any

import pytest

from lattice.modules import install
from lattice.spec.engines import REGISTRY
from lattice.studio.mock_server import make_server as make_mock
from lattice.studio.server import StubGenerator, make_server
from tests.studio.test_server import make_project, request

ROOT = Path(__file__).resolve().parents[2]
MARIADB = ROOT / "docs/examples/engines/mariadb.toml"
ROW_KEYS = {"name", "source", "used", "server", "adapter", "requires"}
BUILTIN = [
    {
        "name": "sqlite",
        "source": "builtin",
        "used": True,
        "server": None,
        "adapter": "sqlite",
        "requires": [],
    },
    {
        "name": "postgres",
        "source": "builtin",
        "used": False,
        "server": {"image": "postgres:16-alpine"},
        "adapter": "postgres",
        "requires": ["psycopg[binary]>=3.2"],
    },
    {
        "name": "mysql",
        "source": "builtin",
        "used": False,
        "server": {"image": "mysql:8.4"},
        "adapter": "mysql",
        "requires": ["pymysql>=1.1"],
    },
    {
        "name": "clickhouse",
        "source": "builtin",
        "used": False,
        "server": {"image": "clickhouse/clickhouse-server:24.8-alpine"},
        "adapter": "clickhouse",
        "requires": ["clickhouse-connect>=0.8"],
    },
]


@pytest.fixture(autouse=True)
def _scoped_registry():
    with REGISTRY.scoped():
        yield


def serve(srv: Any) -> str:
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def stop(srv: Any) -> None:
    srv.shutdown()
    srv.server_close()


def test_a_plain_project_lists_the_builtins(tmp_path: Path) -> None:
    project = make_project(tmp_path / "proj", repo=False)
    srv = make_server(project, 0, generator=StubGenerator())
    base = serve(srv)
    try:
        status, out = request("GET", base + "/api/engines")
        assert status == 200
        assert out == {"engines": BUILTIN, "modules": [], "errors": []}
        assert request("POST", base + "/api/engines", {})[0] == 404
        assert request("DELETE", base + "/api/engines")[0] == 404
    finally:
        stop(srv)


def test_project_engines_and_modules_are_listed(tmp_path: Path) -> None:
    project = make_project(tmp_path / "proj", repo=False)
    (project / "engines").mkdir()
    shutil.copy(MARIADB, project / "engines" / "mariadb.toml")
    with (project / "studio.toml").open("a") as f:
        f.write('\n[app]\ndatabase = "mariadb"\n\n[engines]\npaths = ["engines/mariadb.toml"]\n')
    # an installed engine module (pg-fast: postgres under another name) and a custom one
    mod = tmp_path / "pg-fast"
    mod.mkdir()
    text = (ROOT / "lattice/spec/engines/postgres.toml").read_text()
    text = (
        text.replace('name = "postgres"', 'name = "pgfast"')
        .replace('aliases = ["postgresql"]', "aliases = []")
        .replace('url_schemes = ["postgres", "postgresql"]', 'url_schemes = ["pgfast"]')
    )
    (mod / "engine.toml").write_text(text)
    (mod / "lattice-module.toml").write_text(
        'name = "pg-fast"\nversion = "1.2.0"\nkind = "engine"\ndescriptor = "engine.toml"\n'
    )
    entry = install(project, str(mod), yes=True, out=lambda s: None)
    srv = make_server(project, 0, generator=StubGenerator())
    base = serve(srv)
    try:
        status, out = request("GET", base + "/api/engines")
        assert status == 200 and out["errors"] == []
        rows = {r["name"]: r for r in out["engines"]}
        assert list(rows) == ["sqlite", "postgres", "mysql", "clickhouse", "mariadb", "pgfast"]
        assert all(set(r) == ROW_KEYS for r in out["engines"])
        assert rows["mariadb"] == {
            "name": "mariadb",
            "source": "project",
            "used": True,
            "server": {"image": "mariadb:11"},
            "adapter": "mysql",
            "requires": ["pymysql>=1.1"],
        }
        assert rows["sqlite"]["used"] is False
        assert rows["pgfast"]["source"] == "module"
        assert out["modules"] == [
            {"name": "pg-fast", "version": "1.2.0", "sha256": entry.sha256, "status": "ok"}
        ]
        # the project's checks can name its engine (registered in Studio's process)
        assert REGISTRY.entry("mariadb").source == "project"
        # one byte of the installed tree changed: re-hashed on the next read, refused
        f = project / ".lattice/modules/pg-fast/engine.toml"
        f.write_text(f.read_text().replace("pgfast", "pgfasT", 1))
        out = request("GET", base + "/api/engines")[1]
        assert [r["name"] for r in out["engines"]][-1] == "mariadb"
        assert out["modules"][0]["status"] == "changed"
        assert out["errors"][0].startswith("module 'pg-fast' (1.2.0) is changed")
        assert REGISTRY.entry("pgfast") is None
    finally:
        stop(srv)


def test_a_broken_engine_file_is_reported_not_fatal(tmp_path: Path) -> None:
    project = make_project(tmp_path / "proj", repo=False)
    with (project / "studio.toml").open("a") as f:
        f.write('\n[engines]\npaths = ["engines/missing.toml"]\n')
    srv = make_server(project, 0, generator=StubGenerator())
    base = serve(srv)
    try:
        out = request("GET", base + "/api/engines")[1]
        assert out["engines"] == BUILTIN
        assert (
            len(out["errors"]) == 1 and "engines/missing.toml: cannot read it" in out["errors"][0]
        )
        assert request("GET", base + "/api/model")[0] == 200
    finally:
        stop(srv)


def test_the_mock_serves_the_same_shape() -> None:
    srv = make_mock(0)
    base = serve(srv)
    try:
        status, out = request("GET", base + "/api/engines")
        assert status == 200 and set(out) == {"engines", "modules", "errors"}
        assert out["engines"][:4] == BUILTIN
        assert out["engines"][4]["name"] == "mariadb" and out["engines"][4]["source"] == "project"
        assert all(set(r) == ROW_KEYS for r in out["engines"])
        assert [(m["name"], m["status"]) for m in out["modules"]] == [
            ("acme-audit", "ok"),
            ("pg-tuned", "changed"),
        ]
        assert all(set(m) == {"name", "version", "sha256", "status"} for m in out["modules"])
        assert "pg-tuned" in out["errors"][0]
        assert request("POST", base + "/api/engines", {})[0] == 404
    finally:
        stop(srv)


def test_api_md_documents_v1_14() -> None:
    text = (ROOT / "lattice" / "studio" / "API.md").read_text()
    sec = text[text.index("## v1.14 additions") :]
    for s in (
        "GET /api/engines",
        "[engines]",
        "paths",
        "lattice.modules.lock",
        "changed",
        "used",
        "no write",
        "web UI",
        "--engine-file",
        "mock",
    ):
        assert s in sec, s
