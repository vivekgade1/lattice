"""A toy Flask + Flask-SQLAlchemy app for the relationship-write tests (pivot plan
2026-10-05, A.2). Not an upstream app: written for the tests.

Three link tables are written only through ORM relationships, never named by a route:
``post_tags`` (``post.tags.append(tag)`` in the view), ``likes`` (``post.likers = [...]``
in the view) and ``stars`` (``post.star(user)``, a model method doing
``self.starrers.append(user)``). ``archive`` is touched by no route at all: the one table
truly outside the API.

``PUT /tags/<id>`` sets fields through ``setattr`` over a literal tuple of names (no
relationship among them: resolved).

With ``REL_TOY_UNRESOLVED=1`` the app also has ``POST /posts/<id>/attach/<field>``,
which appends through ``getattr(post, field)``, and ``PUT /posts/<id>``, which sets
whatever the body names through ``setattr`` on a Post (which has relationship
collections) -- relationship writes the front end cannot name.

The tests copy this directory to a temporary root and import it as ``flask_rel_toy``."""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def make_app(db_path: str | Path) -> Flask:
    from flask_rel_toy.routes import bp, extra

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{Path(db_path).resolve()}"
    db.init_app(app)
    app.register_blueprint(bp)
    if os.environ.get("REL_TOY_UNRESOLVED") == "1":
        app.register_blueprint(extra)
    with app.app_context():
        from flask_rel_toy import models  # noqa: F401

        db.create_all()
    return app


def dispose(app: Flask) -> None:
    with app.app_context():
        db.engine.dispose()
