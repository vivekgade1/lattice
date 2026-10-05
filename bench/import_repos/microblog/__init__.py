"""miguelgrinberg/microblog-api, vendored as a Phase 2 dev app (Flask + Alchemical + APIFairy).

Upstream code lives in :mod:`bench.import_repos.microblog.api` and
:mod:`bench.import_repos.microblog.config`; see ``PROVENANCE.md``. This module adds only
:func:`make_app` and :func:`dispose`.

Upstream's ``Config`` reads environment variables at import (``DATABASE_URL``,
``SECRET_KEY``, ``DISABLE_AUTH``, ...). :class:`LatticeConfig` pins every one of them to
upstream's documented default (or a test-safe value), so the host environment never
changes the app's behaviour, and nothing reads a ``.env`` file (PROVENANCE, mod 1).

The database extension (``api.app.db``, an ``Alchemical``) is a module-level singleton
whose engine is re-initialised by every ``create_app``: build one app at a time and
dispose of it before building the next.

Flask-Mail builds every message's ``Message-ID`` with ``email.utils.make_msgid()``, which
calls ``socket.getfqdn()`` -- a reverse DNS lookup that can block for tens of seconds
(35 s on the machine this was vendored on) in the password-reset mail thread, and the
interpreter waits for that thread at exit. Mail is never sent here
(``MAIL_SUPPRESS_SEND``), so importing this package gives Flask-Mail a ``make_msgid``
with a fixed domain; nothing else about the message changes.
"""

from __future__ import annotations

import functools
from email.utils import make_msgid
from pathlib import Path

import flask_mail
from flask import Flask

from bench.import_repos.microblog.api.app import create_app, db
from bench.import_repos.microblog.config import Config

flask_mail.make_msgid = functools.partial(make_msgid, domain="microblog.example.com")


class LatticeConfig(Config):
    """Upstream ``Config`` with every environment-derived value fixed."""

    ALCHEMICAL_DATABASE_URL = "sqlite://"  # replaced per make_app()
    ALCHEMICAL_ENGINE_OPTIONS = {"echo": False}
    SECRET_KEY = "lattice-test-secret-microblog-000000000000000000000000"
    DISABLE_AUTH = False
    ACCESS_TOKEN_MINUTES = 15
    REFRESH_TOKEN_DAYS = 7
    REFRESH_TOKEN_IN_COOKIE = True
    REFRESH_TOKEN_IN_BODY = False
    RESET_TOKEN_MINUTES = 15
    PASSWORD_RESET_URL = "http://localhost:3000/reset"
    USE_CORS = True
    OAUTH2_REDIRECT_URI = "http://localhost:3000/oauth2/{provider}/callback"
    APIFAIRY_UI = "elements"
    MAIL_SERVER = "localhost"
    MAIL_PORT = 25
    MAIL_USE_TLS = False
    MAIL_USERNAME = None
    MAIL_PASSWORD = None
    MAIL_DEFAULT_SENDER = "donotreply@microblog.example.com"
    MAIL_SUPPRESS_SEND = True  # password-reset mail is built but never sent
    OAUTH2_PROVIDERS = {
        name: {**provider, "client_id": None, "client_secret": None}
        for name, provider in Config.OAUTH2_PROVIDERS.items()
    }


def make_app(db_path: str | Path) -> Flask:
    """Build the app on the SQLite file at ``db_path``, creating missing tables.

    Upstream creates tables with Alembic (not vendored); ``db.create_all()`` builds the
    same models' tables.
    """
    url = f"sqlite:///{Path(db_path).resolve()}"
    config = type("LatticeRunConfig", (LatticeConfig,), {"ALCHEMICAL_DATABASE_URL": url})
    app = create_app(config)
    with app.app_context():
        db.create_all()
    return app


def dispose(app: Flask | None = None) -> None:
    """Dispose of the engine (before snapshotting the database file)."""
    db.get_engine().dispose()
