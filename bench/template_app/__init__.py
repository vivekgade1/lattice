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


def create_template_tables(engine: Engine) -> None:
    """Create the template's own tables (``user``, ``item``) on ``engine`` and nothing else.

    ``SQLModel.metadata`` is process-global: any other SQLModel app imported into the same
    process (the job-scheduler that ``tests/spec/test_authoring.py`` imports at collection
    time) registers its tables there too, and a bare ``metadata.create_all`` would put them
    in the template's database, where they read as tables the extractor never saw.
    """
    SQLModel.metadata.create_all(
        engine, tables=[models.User.__table__, models.Item.__table__]  # type: ignore[attr-defined]
    )


def make_app(db_path: str | Path, *, include_api: bool = True) -> FastAPI:
    """Build the template app on the SQLite database at ``db_path``.

    Creates any missing template tables (:func:`create_template_tables`) and routes the
    template's ``get_db`` dependency to a session on that database. The engine is
    kept on ``app.state.engine`` so callers can dispose of it. ``include_api=False``
    leaves the API routes out (settings, CORS, docs and dependency overrides only), for
    a caller that mounts its own route list (:func:`lattice.emit.thin.make_emitted_app`);
    no module state is touched either way, so apps can be built from several threads.
    """
    engine = sqlite_engine(db_path)
    create_template_tables(engine)

    def get_db() -> Generator[Session]:
        with Session(engine) as session:
            yield session

    app = create_app(include_api=include_api)
    app.dependency_overrides[deps.get_db] = get_db
    app.state.engine = engine
    return app
