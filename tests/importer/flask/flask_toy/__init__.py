"""A toy Flask + Flask-SQLAlchemy app for the importer's post-hoc tests (2026-10-05).

Not an upstream app: written for the tests, with the shapes the held-out run showed the
importer mishandled, none of a held-out app's code: a JSON login route (``POST
/auth/login`` taking ``email`` and ``password`` from ``request.get_json()``, answering
``access_token``), protected routes behind a package decorator the front end cannot
read, and an ``Enum`` column (SQLAlchemy stores the member name, ``MANAGER``, though the
value is ``manager``).

The tests copy this directory to a temporary root and import it as ``flask_toy``."""

from __future__ import annotations

from pathlib import Path

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def make_app(db_path: str | Path) -> Flask:
    from flask_toy.routes import bp

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{Path(db_path).resolve()}"
    db.init_app(app)
    app.register_blueprint(bp)
    with app.app_context():
        from flask_toy import models  # noqa: F401

        db.create_all()
    return app


def dispose(app: Flask) -> None:
    with app.app_context():
        db.engine.dispose()
