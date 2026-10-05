"""Seed data for the vendored conduit-drf (``bench/import_repos/conduit``).

``build_seed_db(path)`` migrates a fresh SQLite file and fills it through the app's own
ORM (``UserManager.create_user`` / ``create_superuser``, so passwords are hashed by
Django's ``make_password``). Roles: staff (``admin``, a superuser) and plain users;
``carol`` is inactive (simplejwt refuses her). ``alice`` and ``bob`` each own topics and
comments, and each favorites the other's topic.

Must run in a process whose Django project is this one (see ``_django_runtime``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bench.import_repos.conduit import dispose, make_app

# handle -> (email, username, password, superuser, active)
SEED_USERS: dict[str, tuple[str, str, str, bool, bool]] = {
    "admin": ("admin@example.com", "admin", "adminpass1", True, True),
    "alice": ("alice@example.com", "alice", "alicepass1", False, True),
    "bob": ("bob@example.com", "bob", "bobpass1", False, True),
    "carol": ("carol@example.com", "carol", "carolpass1", False, False),
}
SEED_TAGS: tuple[str, ...] = ("python", "django", "web")  # ids 1-3
# (author, title, content, tag ids)
SEED_TOPICS: tuple[tuple[str, str, str, tuple[int, ...]], ...] = (
    ("alice", "Alice on typing", "Gradual typing notes.", (1,)),  # 1
    ("alice", "Alice on testing", "Pytest fixtures.", ()),  # 2
    ("bob", "Bob on Django", "Views and viewsets.", (2, 3)),  # 3
)
# (author, topic id, content)
SEED_COMMENTS: tuple[tuple[str, int, str], ...] = (
    ("bob", 1, "Nice notes, Alice."),  # 1
    ("alice", 3, "Thanks Bob."),  # 2
)
# (user, topic id): each also counts in Topic.favorite, as the favor route keeps it
SEED_FAVORITES: tuple[tuple[str, int], ...] = (("alice", 3), ("bob", 1))

USER_IDS = {"admin": 1, "alice": 2, "bob": 3, "carol": 4}

# role -> the seed user who holds it (Django: staff/superuser vs plain user)
ROLES: dict[str, str] = {"admin": "admin", "user": "alice"}
# seed users who exist but must be refused, with the reason
REFUSED: dict[str, str] = {"carol": "is_active=False"}
# a route only an authenticated caller passes: (method, path, json)
AUTH_PROBE: tuple[str, str, dict | None] = ("GET", "/api/settings/", None)


def build_seed_db(path: str | Path) -> Path:
    """Create (or overwrite) the SQLite file at ``path`` with the seed data above."""
    path = Path(path)
    if path.exists():
        path.unlink()
    make_app(path)
    from bench.import_repos.conduit.api.models import Comment, Tag, Topic, User

    try:
        users = {}
        for handle, (email, username, password, superuser, active) in SEED_USERS.items():
            if superuser:
                user = User.objects.create_superuser(username, email, password)
            else:
                user = User.objects.create_user(username, email, password, is_active=active)
            users[handle] = user
        tags = [Tag.objects.create(tag=t) for t in SEED_TAGS]
        topics = []
        for author, title, content, tag_ids in SEED_TOPICS:
            topic = Topic.objects.create(user=users[author], title=title, content=content)
            topic.tags.set([tags[i - 1] for i in tag_ids])
            topics.append(topic)
        for author, topic_id, content in SEED_COMMENTS:
            Comment.objects.create(user=users[author], topic=topics[topic_id - 1], content=content)
        for handle, topic_id in SEED_FAVORITES:
            topic = topics[topic_id - 1]
            users[handle].favorites.add(topic)
            topic.favorite += 1
            topic.save()
    finally:
        dispose()
    return path


def login_headers(client: Any, handle: str) -> dict[str, str]:
    """Log in through the app's own route (``POST /api/token/`` with JSON ``email`` and
    ``password``, simplejwt with upstream's ``MyTokenObtainPairSerializer``; answer
    ``{"access": ...}``) and return the ``Authorization: Bearer <access>`` header."""
    email, _, password, _, _ = SEED_USERS[handle]
    answer = client.post("/api/token/", json={"email": email, "password": password})
    if answer.status_code != 200:
        raise RuntimeError(f"login as {handle!r} answered {answer.status_code}: {answer.text}")
    return {"Authorization": f"Bearer {answer.json()['access']}"}
