"""TaskFlow API, vendored as a Lattice importer held-out repo.

Upstream code lives in :mod:`bench.import_repos.taskflow.app` (see ``PROVENANCE.md``
for the pinned commit and every local modification -- there are none beyond import
rewrites, since the upstream ``create_app(settings)`` factory already accepts a
SQLite URL and a test-safe secret key). This module adds only :func:`make_app`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from bench.import_repos.taskflow.app.config import Settings
from bench.import_repos.taskflow.app.main import create_app


def make_app(
    db_path: str | Path, secret_key: str = "held-out-test-secret-key-000000000000"
) -> FastAPI:
    """Build the TaskFlow app on the SQLite database at ``db_path``.

    Tables are created by the upstream lifespan the first time the app is used as a
    context manager (``with TestClient(app) as client:``); see ``app/main.py``.
    """
    settings = Settings(database_url=f"sqlite:///{Path(db_path)}", secret_key=secret_key)
    return create_app(settings)
