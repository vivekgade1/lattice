"""Seed data for the vendored microblog-api (``bench/import_repos/microblog``).

``build_seed_db(path)`` creates the tables and fills them through the app's own models
(``User.password`` hashes with werkzeug's ``generate_password_hash``). The app has one
role (any registered user) and no admin or inactive flag. ``alice`` and ``bob`` each own
posts; ``carol`` owns none. ``alice`` follows ``bob``; ``carol`` follows ``alice``.
No tokens are seeded: logging in creates them.
"""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from typing import Any

from bench.import_repos.microblog import dispose, make_app

# handle -> (username, email, password, about_me)
SEED_USERS: dict[str, tuple[str, str, str, str | None]] = {
    "alice": ("alice", "alice@example.com", "alicepass1", "I write about tea."),
    "bob": ("bob", "bob@example.com", "bobpass1", None),
    "carol": ("carol", "carol@example.com", "carolpass1", "Lurker."),
}

# (author handle, text, timestamp) -- naive UTC, as upstream stores them
SEED_POSTS: tuple[tuple[str, str, datetime], ...] = (
    ("alice", "First cup of the day.", datetime(2026, 1, 5, 8, 0)),  # 1
    ("alice", "Oolong is underrated.", datetime(2026, 1, 6, 9, 30)),  # 2
    ("bob", "Hello, microblog!", datetime(2026, 1, 7, 12, 0)),  # 3
    ("bob", "Second post, same as the first.", datetime(2026, 1, 8, 18, 45)),  # 4
)

# (follower, followed)
SEED_FOLLOWS: tuple[tuple[str, str], ...] = (("alice", "bob"), ("carol", "alice"))

USER_IDS = {"alice": 1, "bob": 2, "carol": 3}

# role -> the seed user who holds it (one role: any registered user)
ROLES: dict[str, str] = {"user": "alice"}
# seed users who exist but must be refused (the app has no inactive flag)
REFUSED: dict[str, str] = {}
# a route only an authenticated caller passes: (method, path, json)
AUTH_PROBE: tuple[str, str, dict | None] = ("GET", "/api/me", None)


def build_seed_db(path: str | Path) -> Path:
    """Create (or overwrite) the SQLite file at ``path`` with the seed data above."""
    from bench.import_repos.microblog.api.app import db
    from bench.import_repos.microblog.api.models import Post, User

    path = Path(path)
    if path.exists():
        path.unlink()
    app = make_app(path)
    try:
        with app.app_context():
            users = {}
            for handle, (username, email, password, about_me) in SEED_USERS.items():
                user = User(username=username, email=email, password=password, about_me=about_me)
                db.session.add(user)
                users[handle] = user
            db.session.flush()
            for author, text, timestamp in SEED_POSTS:
                db.session.add(Post(author=users[author], text=text, timestamp=timestamp))
            for follower, followed in SEED_FOLLOWS:
                users[follower].follow(users[followed])
            db.session.commit()
    finally:
        dispose(app)
    return path


def login_headers(client: Any, handle: str) -> dict[str, str]:
    """Log in through the app's own route (``POST /api/tokens`` with HTTP Basic
    username/password; answer ``{"access_token": ...}``) and return the
    ``Authorization: Bearer <jwt>`` header."""
    username, _, password, _ = SEED_USERS[handle]
    basic = base64.b64encode(f"{username}:{password}".encode()).decode()
    answer = client.post("/api/tokens", headers={"Authorization": f"Basic {basic}"})
    if answer.status_code != 200:
        raise RuntimeError(f"login as {handle!r} answered {answer.status_code}: {answer.text}")
    return {"Authorization": f"Bearer {answer.json()['access_token']}"}
