"""miguelgrinberg/flasky (the ``app/api`` blueprint), vendored as a Phase 2 dev app.

Flask + Flask-SQLAlchemy + Flask-HTTPAuth. Upstream code lives in
:mod:`bench.import_repos.flasky.app` (models and the ``api`` blueprint) and
:mod:`bench.import_repos.flasky.config`; see ``PROVENANCE.md``. This module adds only
:func:`make_app` and :func:`dispose`.

Upstream's ``create_app(config_name)`` looks its configuration up by name in
``config.config``. :func:`make_app` registers a per-call subclass of upstream's ``Config``
under a private name, builds the app, and removes the name again. Every
environment-derived value is pinned, so the host environment never changes behaviour.
"""

from __future__ import annotations

import itertools
from pathlib import Path

from flask import Flask

from bench.import_repos.flasky import config as upstream_config
from bench.import_repos.flasky.app import create_app, db

_names = itertools.count()


class LatticeConfig(upstream_config.Config):
    """Upstream ``Config`` with every environment-derived value fixed."""

    SECRET_KEY = "lattice-test-secret-flasky-0000000000000000000000000000"
    MAIL_SERVER = "localhost"
    MAIL_PORT = 25
    MAIL_USE_TLS = False
    MAIL_USERNAME = None
    MAIL_PASSWORD = None
    FLASKY_ADMIN = None  # the seed assigns the Administrator role explicitly
    SSL_REDIRECT = False
    SQLALCHEMY_DATABASE_URI = "sqlite://"  # replaced per make_app()


def make_app(db_path: str | Path) -> Flask:
    """Build the API app on the SQLite file at ``db_path``.

    Creates missing tables (upstream uses Flask-Migrate, not vendored) and the three
    roles (``Role.insert_roles()``, as upstream's ``flask deploy`` does).
    """
    from bench.import_repos.flasky.app.models import Role

    url = f"sqlite:///{Path(db_path).resolve()}"
    name = f"_lattice_{next(_names)}"
    upstream_config.config[name] = type(
        "LatticeRunConfig", (LatticeConfig,), {"SQLALCHEMY_DATABASE_URI": url}
    )
    try:
        app = create_app(name)
    finally:
        del upstream_config.config[name]
    with app.app_context():
        db.create_all()
        Role.insert_roles()
    return app


def dispose(app: Flask) -> None:
    """Dispose of the app's engine (before snapshotting the database file)."""
    with app.app_context():
        db.engine.dispose()
