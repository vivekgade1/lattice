"""Server database fixtures for tests/spec/test_db.py and test_multidb.py: one throwaway
container per dialect for the session (``lattice.spec.dbserver``), skipped when Docker is
not available; after the session none of the containers it started may be left in
``docker ps -a --filter name=lattice-`` (containers of another session running at the
same time, e.g. another xdist worker's, are that session's)."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator

import pytest

from lattice.spec.dbserver import (
    DbServer,
    docker_available,
    live_containers,
    start_container,
    started_containers,
)

_SERVERS: dict[str, DbServer] = {}
_DOCKER: list[bool] = []


def _docker() -> bool:
    if not _DOCKER:
        _DOCKER.append(docker_available())
    return _DOCKER[0]


def lattice_containers() -> list[str]:
    out = subprocess.run(  # noqa: S603, S607
        ["docker", "ps", "-a", "--filter", "name=lattice-", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [n for n in out.stdout.split() if n.startswith("lattice-")]


@pytest.fixture(scope="session")
def db_servers() -> Iterator[dict[str, DbServer]]:
    """The session's servers (started on first use); removed at the end, and then no
    ``lattice-`` container may exist."""
    if not _docker():
        pytest.skip("Docker is not available")
    yield _SERVERS
    for server in _SERVERS.values():
        server.stop()
    _SERVERS.clear()
    assert live_containers() == [], "a container outlived the session"
    left = set(lattice_containers()) & set(started_containers())
    assert not left, f"lattice- container(s) left behind: {sorted(left)}"


def server(db_servers: dict[str, DbServer], dialect: str) -> DbServer:
    if dialect not in db_servers:
        db_servers[dialect] = start_container(dialect)
    return db_servers[dialect]


@pytest.fixture
def pg(db_servers: dict[str, DbServer]) -> DbServer:
    return server(db_servers, "postgres")


@pytest.fixture
def my(db_servers: dict[str, DbServer]) -> DbServer:
    return server(db_servers, "mysql")


@pytest.fixture
def ch(db_servers: dict[str, DbServer]) -> DbServer:
    return server(db_servers, "clickhouse")


@pytest.fixture(params=["postgres", "mysql", "clickhouse"])
def any_server(request: pytest.FixtureRequest, db_servers: dict[str, DbServer]) -> DbServer:
    return server(db_servers, request.param)
