"""Seed data for the vendored flasky API (``bench/import_repos/flasky``).

``build_seed_db(path)`` creates the tables and the three roles, then fills them through
the app's own models (``User.password`` hashes with werkzeug's
``generate_password_hash``). Roles are rows of ``roles`` with a permission bitmask:
``User`` (follow, comment, write), ``Moderator`` (+ moderate), ``Administrator``
(+ admin). ``carol`` is unconfirmed (the API answers 403 "Unconfirmed account").
``alice`` and ``bob`` each own posts and comments.
"""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from typing import Any

from bench.import_repos.flasky import dispose, make_app

# handle -> (email, username, password, role name, confirmed)
SEED_USERS: dict[str, tuple[str, str, str, str, bool]] = {
    "admin": ("admin@example.com", "admin", "adminpass1", "Administrator", True),
    "role_moderator": ("mod@example.com", "mod", "modpass1", "Moderator", True),
    "alice": ("alice@example.com", "alice", "alicepass1", "User", True),
    "bob": ("bob@example.com", "bob", "bobpass1", "User", True),
    "carol": ("carol@example.com", "carol", "carolpass1", "User", False),
}

# (author, body, timestamp)
SEED_POSTS: tuple[tuple[str, str, datetime], ...] = (
    ("alice", "Hello from *Alice*.", datetime(2026, 2, 1, 9, 0)),  # 1
    ("alice", "Second post with a [link](https://example.com).", datetime(2026, 2, 2, 9, 0)),  # 2
    ("bob", "Bob's **bold** post.", datetime(2026, 2, 3, 10, 0)),  # 3
    ("bob", "Bob again.", datetime(2026, 2, 4, 11, 0)),  # 4
)
# (author, post id, body, disabled)
SEED_COMMENTS: tuple[tuple[str, int, str, bool], ...] = (
    ("bob", 1, "Nice one, Alice.", False),  # 1
    ("alice", 3, "Thanks Bob!", False),  # 2
    ("role_moderator", 3, "Please keep it civil.", True),  # 3
)
# (follower, followed) -- on top of everyone following themselves (User.__init__)
SEED_FOLLOWS: tuple[tuple[str, str], ...] = (("alice", "bob"),)

USER_IDS = {"admin": 1, "role_moderator": 2, "alice": 3, "bob": 4, "carol": 5}

# role (a `roles` row) -> the seed user who holds it
ROLES: dict[str, str] = {"Administrator": "admin", "Moderator": "role_moderator", "User": "alice"}
# seed users who exist but must be refused, with the reason
REFUSED: dict[str, str] = {"carol": "confirmed=False (403 Unconfirmed account)"}
# a route only an authenticated caller passes: (method, path, json)
AUTH_PROBE: tuple[str, str, dict | None] = ("GET", "/api/v1/posts/", None)


def build_seed_db(path: str | Path) -> Path:
    """Create (or overwrite) the SQLite file at ``path`` with the seed data above."""
    from bench.import_repos.flasky.app import db
    from bench.import_repos.flasky.app.models import Comment, Post, Role, User

    path = Path(path)
    if path.exists():
        path.unlink()
    app = make_app(path)
    try:
        with app.app_context():
            users = {}
            for handle, (email, username, password, role, confirmed) in SEED_USERS.items():
                user = User(
                    email=email,
                    username=username,
                    password=password,
                    confirmed=confirmed,
                    role=db.session.scalar(db.select(Role).filter_by(name=role)),
                    member_since=datetime(2026, 1, 1),
                )
                db.session.add(user)
                users[handle] = user
            db.session.flush()
            posts = []
            for author, body, timestamp in SEED_POSTS:
                post = Post(body=body, author=users[author], timestamp=timestamp)
                db.session.add(post)
                posts.append(post)
            db.session.flush()
            for author, post_id, body, disabled in SEED_COMMENTS:
                db.session.add(
                    Comment(
                        body=body, author=users[author], post=posts[post_id - 1], disabled=disabled
                    )
                )
            for follower, followed in SEED_FOLLOWS:
                users[follower].follow(users[followed])
            db.session.commit()
    finally:
        dispose(app)
    return path


def _basic(user: str, password: str) -> dict[str, str]:
    raw = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def raw_credentials_headers(handle: str) -> dict[str, str]:
    """HTTP Basic with ``handle``'s email and password (flasky accepts them on every API
    route, not only on the token route)."""
    email, _, password, _, _ = SEED_USERS[handle]
    return _basic(email, password)


def login_headers(client: Any, handle: str) -> dict[str, str]:
    """Log in through the app's own route (``POST /api/v1/tokens/`` with HTTP Basic
    email/password; answer ``{"token": ..., "expiration": 3600}``) and return the
    header flasky expects for a token: HTTP Basic with the token as the username and an
    empty password."""
    email, _, password, _, _ = SEED_USERS[handle]
    answer = client.post("/api/v1/tokens/", headers=_basic(email, password))
    if answer.status_code != 200:
        raise RuntimeError(f"login as {handle!r} answered {answer.status_code}: {answer.text}")
    return _basic(answer.json()["token"], "")
