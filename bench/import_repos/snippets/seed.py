"""Seed data for the vendored DRF tutorial (``bench/import_repos/snippets``).

``build_seed_db(path)`` migrates a fresh SQLite file and fills it through the app's own
ORM (passwords hashed by Django's ``make_password``). Roles: Django's superuser
(``admin``) and plain users; ``carol`` is inactive. ``alice`` and ``bob`` each own
snippets; ``admin`` owns one, ``carol`` none.

Must run in a process whose Django project is this one (see ``_django_runtime``).
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from bench.import_repos.snippets import dispose, make_app

# handle -> (username, password, is_superuser, is_active)
SEED_USERS: dict[str, tuple[str, str, bool, bool]] = {
    "admin": ("admin", "adminpass1", True, True),
    "alice": ("alice", "alicepass1", False, True),
    "bob": ("bob", "bobpass1", False, True),
    "carol": ("carol", "carolpass1", False, False),
}

# (owner handle, title, code, language, linenos)
SEED_SNIPPETS: tuple[tuple[str, str, str, str, bool], ...] = (
    ("alice", "hello", 'print("hello, world")\n', "python", False),
    ("alice", "", "SELECT 1;\n", "sql", True),
    (
        "bob",
        "fib",
        "def fib(n):\n    return n if n < 2 else fib(n - 1) + fib(n - 2)\n",
        "python",
        True,
    ),
    ("bob", "greet", 'echo "hi"\n', "bash", False),
    ("admin", "notice", "# maintenance at noon\n", "python", False),
)

# Primary keys as build_seed_db assigns them (fresh database, insertion order).
USER_IDS = {"admin": 1, "alice": 2, "bob": 3, "carol": 4}

# role -> the seed user who holds it (Django: superuser vs plain user)
ROLES: dict[str, str] = {"admin": "admin", "user": "alice"}
# seed users who exist but must be refused, with the reason
REFUSED: dict[str, str] = {"carol": "is_active=False"}
# a route only an authenticated caller passes: (method, path, json)
AUTH_PROBE: tuple[str, str, dict | None] = ("POST", "/snippets/", {"code": "x = 1"})
SNIPPET_IDS = {"alice": (1, 2), "bob": (3, 4), "admin": (5,)}


def build_seed_db(path: str | Path) -> Path:
    """Create (or overwrite) the SQLite file at ``path`` with the seed data above."""
    path = Path(path)
    if path.exists():
        path.unlink()
    make_app(path)
    from django.contrib.auth.models import User

    from bench.import_repos.snippets.snippets.models import Snippet

    try:
        users = {}
        for handle, (username, password, superuser, active) in SEED_USERS.items():
            make = User.objects.create_superuser if superuser else User.objects.create_user
            user = make(username=username, email=f"{username}@example.com", password=password)
            if not active:
                user.is_active = False
                user.save()
            users[handle] = user
        for owner, title, code, language, linenos in SEED_SNIPPETS:
            Snippet.objects.create(
                owner=users[owner], title=title, code=code, language=language, linenos=linenos
            )
    finally:
        dispose()
    return path


def login_headers(client: Any, handle: str) -> dict[str, str]:
    """Request headers that authenticate as ``handle``: HTTP Basic, DRF's default
    (``BasicAuthentication``; the browsable API's session login also exists)."""
    username, password, _, _ = SEED_USERS[handle]
    raw = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}
