"""The official FastAPI full-stack template backend, vendored as a Lattice proof target.

Upstream code lives in :mod:`bench.template_app.app` (see ``README.md`` for the pinned
commit and every local modification). This module adds only :func:`make_app`, which
builds the upstream app against a given SQLite file.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from bench.template_app.app import models  # noqa: F401  (registers the SQLModel tables)
from bench.template_app.app.api import deps
from bench.template_app.app.main import create_app


def sqlite_engine(db_path: str | Path) -> Engine:
    """An engine for a SQLite file usable from TestClient's worker threads."""
    return create_engine(
        f"sqlite:///{Path(db_path)}", connect_args={"check_same_thread": False}
    )


def make_app(db_path: str | Path) -> FastAPI:
    """Build the template app on the SQLite database at ``db_path``.

    Creates any missing tables (``SQLModel.metadata.create_all``) and routes the
    template's ``get_db`` dependency to a session on that database. The engine is
    kept on ``app.state.engine`` so callers can dispose of it.
    """
    engine = sqlite_engine(db_path)
    SQLModel.metadata.create_all(engine)

    def get_db() -> Generator[Session]:
        with Session(engine) as session:
            yield session

    app = create_app()
    app.dependency_overrides[deps.get_db] = get_db
    app.state.engine = engine
    return app
