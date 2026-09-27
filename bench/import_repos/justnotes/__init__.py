"""JustNotes, vendored as a Lattice importer held-out repo.

Upstream code lives in :mod:`bench.import_repos.justnotes.app` (see ``PROVENANCE.md``
for the pinned commit and every local modification). This module adds only
:func:`make_app`, which builds the upstream app against a given SQLite file, mirroring
``bench/template_app/__init__.py``.
"""

from __future__ import annotations

import os
import time
from collections.abc import Generator
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from bench.import_repos.justnotes.app import models  # noqa: F401  (registers the tables)
from bench.import_repos.justnotes.app._lattice_registry import metadata_registry
from bench.import_repos.justnotes.app.database import connection
from bench.import_repos.justnotes.app.main import create_app


def sqlite_engine(db_path: str | Path) -> Engine:
    """An engine for a SQLite file usable from TestClient's worker threads."""
    return create_engine(
        f"sqlite:///{Path(db_path)}", connect_args={"check_same_thread": False}
    )


def _ensure_process_tz_utc() -> None:
    """Pin the process timezone to UTC (env patch, no vendored file touched).

    ``app/utils/jwt.py`` mints tokens with ``datetime.now() + timedelta(...)`` (local
    time, no tzinfo) and python-jose's ``jwt.encode`` treats a naive datetime's "exp"
    claim as already being UTC. On a server configured for UTC (the common case for a
    deployed container) that is harmless; off UTC, every minted token is stamped with
    the wrong epoch and ``jwt.decode`` immediately raises "Signature has expired" --
    every authenticated route 401s. Setting ``TZ=UTC`` for this process is exactly the
    "env" half of the allowed patch surface, and leaves the vendored code untouched.
    """
    if os.environ.get("TZ") != "UTC":
        os.environ["TZ"] = "UTC"
        if hasattr(time, "tzset"):
            time.tzset()


def make_app(db_path: str | Path) -> FastAPI:
    """Build the JustNotes app on the SQLite database at ``db_path``.

    Creates any missing tables (``SQLModel.metadata.create_all``) and routes the
    upstream ``database.connection.get_db_session`` dependency to a session on that
    database. The engine is kept on ``app.state.engine`` so callers can dispose of it.
    """
    _ensure_process_tz_utc()
    engine = sqlite_engine(db_path)
    metadata_registry.metadata.create_all(engine)

    def get_db_session() -> Generator[Session]:
        with Session(engine) as session:
            yield session

    app = create_app()
    app.dependency_overrides[connection.get_db_session] = get_db_session
    app.state.engine = engine
    return app
