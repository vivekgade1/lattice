"""Multi-Vendor Inventory & Order Booking System, vendored as a held-out repo.

Upstream code lives in :mod:`bench.import_repos.multivendor.app` (see ``PROVENANCE.md``
for the pinned commit and every local modification). This module adds only
:func:`make_app` and :func:`sqlite_engine`, mirroring ``bench/import_repos/justnotes``.

Upstream reads ``DATABASE_URL`` and ``SECRET_KEY`` from the environment at import time
(``app/core/config.py``: ``settings = Settings()``) and builds a module-level engine
from them (``app/core/database.py``). Both are set here, before any upstream import,
to test-safe placeholders; the placeholder engine is never used, because
:func:`make_app` routes ``get_session`` to the caller's own SQLite file.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_KEY", "held-out-test-secret-key-000000000000")

from fastapi import FastAPI  # noqa: E402
from sqlalchemy import Engine  # noqa: E402
from sqlmodel import Session, create_engine  # noqa: E402

from bench.import_repos.multivendor.app import models  # noqa: E402,F401  (registers the tables)
from bench.import_repos.multivendor.app._lattice_registry import metadata_registry  # noqa: E402
from bench.import_repos.multivendor.app.core import database  # noqa: E402
from bench.import_repos.multivendor.app.main import create_app  # noqa: E402


def sqlite_engine(db_path: str | Path) -> Engine:
    """An engine for a SQLite file usable from TestClient's worker threads."""
    return create_engine(f"sqlite:///{Path(db_path)}", connect_args={"check_same_thread": False})


def make_app(db_path: str | Path) -> FastAPI:
    """Build the app on the SQLite database at ``db_path``.

    Creates any missing tables (upstream creates them with Alembic, not vendored) and
    routes the upstream ``core.database.get_session`` dependency to a session on that
    database. The engine is kept on ``app.state.engine`` so callers can dispose of it.
    """
    engine = sqlite_engine(db_path)
    metadata_registry.metadata.create_all(engine)

    def get_session() -> Generator[Session]:
        with Session(engine) as session:
            yield session

    app = create_app()
    app.dependency_overrides[database.get_session] = get_session
    app.state.engine = engine
    return app
