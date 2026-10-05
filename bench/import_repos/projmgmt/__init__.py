"""GuilhermeCAz/project-management-api, vendored as Phase 2 held-out app H2.

Flask 3 + Flask-SQLAlchemy 3, PyJWT, bcrypt. Upstream code lives in
:mod:`bench.import_repos.projmgmt.app` (``create_app``, the ``auth``, ``users``,
``projects`` and ``tasks`` blueprints, the ``middleware`` decorators) and
:mod:`bench.import_repos.projmgmt.config`; see ``PROVENANCE.md``. This module adds only
:func:`make_app` and :func:`dispose`.

Upstream's ``create_app()`` takes no argument: it picks its configuration by the
``FLASK_ENV`` environment variable from ``config.config`` (default ``development``, a
SQLite file inside the source tree). :func:`make_app` registers a per-call subclass of
upstream's ``Config`` under a private name, sets ``FLASK_ENV`` to that name only while
``create_app()`` runs (then restores it), and removes the name again. Every value is
pinned, so the host environment never changes behaviour.

``create_app()`` calls upstream's ``setup_logging()``, which sends every log record to
stdout through a queued loguru sink (a background thread). :func:`make_app` replaces that
sink with a synchronous stderr one (WARNING and above) so that machine-read stdout (the
probe workers' JSON) stays clean. Only where log lines go changes, not what any route does.
"""

from __future__ import annotations

import itertools
import os
import sys
from pathlib import Path

from flask import Flask

from bench.import_repos.projmgmt import config as upstream_config
from bench.import_repos.projmgmt.app import create_app, db

_names = itertools.count()


class LatticeConfig(upstream_config.Config):
    """Upstream ``Config`` with every environment-derived value fixed."""

    SECRET_KEY = "lattice-test-secret-projmgmt-000000000000000000000000000"
    DEBUG = False
    TESTING = False
    SQLALCHEMY_DATABASE_URI = "sqlite://"  # replaced per make_app()


def make_app(db_path: str | Path) -> Flask:
    """Build the app on the SQLite file at ``db_path`` (``create_app`` creates the tables)."""
    from loguru import logger

    url = f"sqlite:///{Path(db_path).resolve()}"
    name = f"_lattice_{next(_names)}"
    upstream_config.config[name] = type(
        "LatticeRunConfig", (LatticeConfig,), {"SQLALCHEMY_DATABASE_URI": url}
    )
    previous = os.environ.get("FLASK_ENV")
    os.environ["FLASK_ENV"] = name
    try:
        app = create_app()
    finally:
        del upstream_config.config[name]
        if previous is None:
            del os.environ["FLASK_ENV"]
        else:
            os.environ["FLASK_ENV"] = previous
        logger.remove()
        logger.add(sys.stderr, level="WARNING", format="{level} | {message}")
    return app


def dispose(app: Flask) -> None:
    """Dispose of the app's engine (before snapshotting the database file)."""
    with app.app_context():
        db.engine.dispose()
