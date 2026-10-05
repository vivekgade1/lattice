"""gothinkster/flask-realworld-example-app, vendored as the Phase 2 held-out reserve app.

Flask + Flask-SQLAlchemy, flask-jwt-extended (``Authorization: Token <JWT>``),
flask-apispec / webargs / marshmallow, Flask-Bcrypt. Upstream code lives in
:mod:`bench.import_repos.realworld.conduit` (``create_app``, the ``user``, ``profile`` and
``articles`` blueprints); see ``PROVENANCE.md``. This module adds only :func:`make_app`
and :func:`dispose`.

Upstream's ``create_app(config_object)`` takes a configuration class. :func:`make_app`
passes a per-call subclass of upstream's ``Config`` with every environment-derived value
pinned (upstream reads ``CONDUIT_SECRET`` and ``DATABASE_URL`` from the process
environment; it has no ``.env`` loading of its own), then creates the tables
(``db.create_all()``, as upstream's tests do; upstream ships no migrations).
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask

from bench.import_repos.realworld.conduit.app import create_app
from bench.import_repos.realworld.conduit.extensions import db
from bench.import_repos.realworld.conduit.settings import Config


class LatticeConfig(Config):
    """Upstream ``Config`` with every environment-derived value fixed."""

    SECRET_KEY = "lattice-test-secret-realworld-00000000000000000000000000"
    ENV = "prod"
    DEBUG = False
    TESTING = False
    # upstream's TestConfig value: bcrypt cost 4 instead of 13 (speed only; hashes still
    # verify the same way)
    BCRYPT_LOG_ROUNDS = 4
    # upstream's identity is the integer user id; PyJWT >= 2.10 rejects a non-string `sub`
    # unless this flask-jwt-extended 4.7 option is off (PyJWT 1.6, upstream's pin, did
    # not check it)
    JWT_VERIFY_SUB = False
    # Flask-Caching 2 renamed upstream's CACHE_TYPE 'simple' to 'SimpleCache' (the same
    # in-process cache; no route uses the cache)
    CACHE_TYPE = "SimpleCache"
    SQLALCHEMY_DATABASE_URI = "sqlite://"  # replaced per make_app()


def make_app(db_path: str | Path) -> Flask:
    """Build the app on the SQLite file at ``db_path`` and create any missing tables."""
    url = f"sqlite:///{Path(db_path).resolve()}"
    config = type("LatticeRunConfig", (LatticeConfig,), {"SQLALCHEMY_DATABASE_URI": url})
    app = create_app(config)
    with app.app_context():
        db.create_all()
    return app


def dispose(app: Flask) -> None:
    """Dispose of the app's engine (before snapshotting the database file)."""
    with app.app_context():
        db.engine.dispose()
