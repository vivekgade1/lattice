"""Seed data for the vendored flask-realworld-example-app (``bench/import_repos/realworld``).

``build_seed_db(path)`` creates the tables (``make_app``) and fills them through the app's
own models (``User.set_password`` hashes with Flask-Bcrypt). RealWorld has no roles and no
active flag: every registered user is a plain ``user``. Each user has a ``userprofile`` row
(created with the user, as ``POST /api/users`` does); articles and comments are owned
through ``author_id``, which references ``userprofile.id`` (not ``users.id``). ``alice``
owns two articles and ``bob`` one; each has commented on the other's article; ``alice``
follows ``bob``; ``bob`` has favorited ``alice``'s first article. ``carol`` owns nothing.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from bench.import_repos.realworld import dispose, make_app

# handle -> (username, email, password, bio)
SEED_USERS: dict[str, tuple[str, str, str, str | None]] = {
    "alice": ("alice", "alice@example.com", "alicepass1", "I write about dragons."),
    "bob": ("bob", "bob@example.com", "bobpass1", None),
    "carol": ("carol", "carol@example.com", "carolpass1", None),
}
# (author, title, description, body, tags, created)
SEED_ARTICLES: tuple[tuple[str, str, str, str, tuple[str, ...], datetime], ...] = (
    (
        "alice",
        "How to train your dragon",
        "Ever wonder how?",
        "It takes a Jacobian.",
        ("dragons", "training"),
        datetime(2026, 2, 1, 9, 0),
    ),  # 1: how-to-train-your-dragon
    (
        "alice",
        "Alice in wonderland",
        "A short story",
        "Down the rabbit hole.",
        ("stories",),
        datetime(2026, 2, 2, 9, 0),
    ),  # 2: alice-in-wonderland
    (
        "bob",
        "Bob builds things",
        "Construction notes",
        "Can we fix it?",
        ("dragons",),
        datetime(2026, 2, 3, 10, 0),
    ),  # 3: bob-builds-things
)
# (author, article number, body)
SEED_COMMENTS: tuple[tuple[str, int, str], ...] = (
    ("bob", 1, "Great dragon advice."),  # 1
    ("alice", 3, "Yes we can!"),  # 2
)
# (follower, followed)
SEED_FOLLOWS: tuple[tuple[str, str], ...] = (("alice", "bob"),)
# (user, article number)
SEED_FAVORITES: tuple[tuple[str, int], ...] = (("bob", 1),)

USER_IDS = {"alice": 1, "bob": 2, "carol": 3}
# profile ids equal user ids in this seed (profiles are created in the same order)
PROFILE_IDS = {"alice": 1, "bob": 2, "carol": 3}
SLUGS = ("how-to-train-your-dragon", "alice-in-wonderland", "bob-builds-things")

# role -> the seed user who holds it (one role: any registered user)
ROLES: dict[str, str] = {"user": "alice"}
# seed users who exist but must be refused, with the reason: none can be (no active flag)
REFUSED: dict[str, str] = {}
# a route only an authenticated caller passes: (method, path, json)
AUTH_PROBE: tuple[str, str, dict | None] = ("GET", "/api/user", None)


def build_seed_db(path: str | Path) -> Path:
    """Create (or overwrite) the SQLite file at ``path`` with the seed data above."""
    from bench.import_repos.realworld.conduit.articles.models import Article, Comment, Tags
    from bench.import_repos.realworld.conduit.extensions import db
    from bench.import_repos.realworld.conduit.profile.models import UserProfile
    from bench.import_repos.realworld.conduit.user.models import User

    path = Path(path)
    if path.exists():
        path.unlink()
    app = make_app(path)
    try:
        with app.app_context():
            profiles: dict[str, Any] = {}
            for handle, (username, email, password, bio) in SEED_USERS.items():
                user = User(
                    username,
                    email,
                    password=password,
                    bio=bio,
                    created_at=datetime(2026, 1, 1),
                    updated_at=datetime(2026, 1, 1),
                )
                db.session.add(user)
                db.session.flush()
                profile = UserProfile(user)
                db.session.add(profile)
                db.session.flush()
                profiles[handle] = profile
            tags: dict[str, Any] = {}
            articles = []
            for author, title, description, body, tag_names, created in SEED_ARTICLES:
                article = Article(
                    author=profiles[author],
                    title=title,
                    description=description,
                    body=body,
                    createdAt=created,
                    updatedAt=created,
                )
                for name in tag_names:
                    if name not in tags:
                        tags[name] = Tags(name)
                        db.session.add(tags[name])
                    article.add_tag(tags[name])
                db.session.add(article)
                db.session.flush()
                articles.append(article)
            for author, number, body in SEED_COMMENTS:
                db.session.add(
                    Comment(
                        articles[number - 1],
                        profiles[author],
                        body,
                        createdAt=datetime(2026, 3, 1),
                        updatedAt=datetime(2026, 3, 1),
                    )
                )
            for follower, followed in SEED_FOLLOWS:
                profiles[follower].follows.append(profiles[followed])
            for user, number in SEED_FAVORITES:
                articles[number - 1].favoriters.append(profiles[user])
            db.session.commit()
    finally:
        dispose(app)
    return path


def login_headers(client: Any, handle: str) -> dict[str, str]:
    """Log in through the app's own route (``POST /api/users/login`` with JSON
    ``{"user": {"email", "password"}}``; answer ``{"user": {..., "token"}}``) and return
    the ``Authorization: Token <JWT>`` header (``JWT_HEADER_TYPE = 'Token'``)."""
    _, email, password, _ = SEED_USERS[handle]
    answer = client.post("/api/users/login", json={"user": {"email": email, "password": password}})
    if answer.status_code != 200:
        raise RuntimeError(f"login as {handle!r} answered {answer.status_code}: {answer.text}")
    return {"Authorization": f"Token {answer.json()['user']['token']}"}
