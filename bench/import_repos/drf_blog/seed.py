"""Seed data for the vendored drf_blog_api (``bench/import_repos/drf_blog``).

``build_seed_db(path)`` migrates a fresh SQLite file and fills it through the app's own
ORM (``CustomUserManager.create_user`` / ``create_superuser``: phone users with unusable
passwords, as the OTP login creates them). Roles: the superuser ``admin``; authors
(``author=True``: ``alice``, ``bob``), who may create blogs and edit their own; and plain
users (``role_user``), who may comment and like. The ``User`` model has no ``is_active``
column (``AbstractBaseUser.is_active`` is the constant ``True``), so no seed user can be
refused. ``alice`` and ``bob`` each own blogs (one published, one draft) and comments;
``role_user`` owns a comment and a like.

Must run in a process whose Django project is this one (see ``_django_runtime``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bench.import_repos.drf_blog import dispose, make_app

# handle -> (phone, first_name, last_name, superuser, author)
SEED_USERS: dict[str, tuple[str, str, str, bool, bool]] = {
    "admin": ("989100000001", "Ada", "Admin", True, False),
    "alice": ("989100000002", "Alice", "Archer", False, True),
    "bob": ("989100000003", "Bob", "Baker", False, True),
    "role_user": ("989100000004", "Uma", "User", False, False),
}

# (title, slug, status, parent index or None)
SEED_CATEGORIES: tuple[tuple[str, str, bool, int | None], ...] = (
    ("Technology", "technology", True, None),  # 1
    ("Lifestyle", "lifestyle", True, None),  # 2
    ("Archived", "archived", False, None),  # 3 -- inactive
    ("Python", "python-lang", True, 1),  # 4 -- child of 1
)
# (author, title, slug, status, special, category ids, publish day)
SEED_BLOGS: tuple[tuple[str, str, str, str, bool, tuple[int, ...], int], ...] = (
    ("alice", "Alice on Django", "alice-on-django", "p", False, (1,), 1),  # 1
    ("alice", "Alice draft notes", "alice-draft-notes", "d", False, (2,), 2),  # 2
    ("bob", "Bob on Python", "bob-on-python", "p", True, (1, 4), 3),  # 3
    ("bob", "Bob draft notes", "bob-draft-notes", "d", False, (), 4),  # 4
    ("admin", "Archived news", "archived-news", "p", False, (3,), 5),  # 5
)
# (user, blog id, name, body, parent comment id)
SEED_COMMENTS: tuple[tuple[str, int, str, str, int | None], ...] = (
    ("bob", 1, "bob", "Nice post, Alice.", None),  # 1
    ("alice", 3, "alice", "Thanks for the Python tips.", None),  # 2
    ("role_user", 1, "uma", "Agreed with Bob.", 1),  # 3
)
# (user, blog id)
SEED_LIKES: tuple[tuple[str, int], ...] = (("role_user", 1), ("alice", 3))

USER_IDS = {"admin": 1, "alice": 2, "bob": 3, "role_user": 4}

# role -> the seed user who holds it (superuser / author flag / plain user)
ROLES: dict[str, str] = {"admin": "admin", "author": "alice", "user": "role_user"}
# seed users who exist but must be refused, with the reason: none can be (no is_active)
REFUSED: dict[str, str] = {}
# a route only an authenticated caller passes: (method, path, json)
AUTH_PROBE: tuple[str, str, dict | None] = ("GET", "/account/api/profile/", None)


def build_seed_db(path: str | Path) -> Path:
    """Create (or overwrite) the SQLite file at ``path`` with the seed data above."""
    path = Path(path)
    if path.exists():
        path.unlink()
    make_app(path)
    from django.contrib.auth import get_user_model
    from django.contrib.contenttypes.models import ContentType

    from bench.import_repos.drf_blog.src.blog.models import Blog, Category
    from bench.import_repos.drf_blog.src.comment.models import Comment

    User = get_user_model()
    try:
        users = {}
        for handle, (phone, first, last, superuser, author) in SEED_USERS.items():
            make = User.objects.create_superuser if superuser else User.objects.create_user
            users[handle] = make(phone, first_name=first, last_name=last, author=author)
        categories: list[Any] = []
        for title, slug, status, parent in SEED_CATEGORIES:
            categories.append(
                Category.objects.create(
                    title=title,
                    slug=slug,
                    status=status,
                    parent=categories[parent - 1] if parent else None,
                )
            )
        blogs = []
        for author, title, slug, status, special, cats, day in SEED_BLOGS:
            blog = Blog.objects.create(
                author=users[author],
                title=title,
                slug=slug,
                body=f"{title}: the body.",
                summary=f"{title}: a summary.",
                status=status,
                special=special,
                publish=datetime(2026, 1, day, 9, 0, tzinfo=UTC),
            )
            blog.category.set([categories[i - 1] for i in cats])
            blogs.append(blog)
        for user, blog_id in SEED_LIKES:
            blogs[blog_id - 1].likes.add(users[user])
        blog_type = ContentType.objects.get_for_model(Blog)
        comments: list[Any] = []
        for user, blog_id, name, body, parent in SEED_COMMENTS:
            comments.append(
                Comment.objects.create(
                    user=users[user],
                    name=name,
                    content_type=blog_type,
                    object_id=blogs[blog_id - 1].id,
                    parent=comments[parent - 1] if parent else None,
                    body=body,
                )
            )
    finally:
        dispose()
    return path


def login_headers(client: Any, handle: str) -> dict[str, str]:
    """Log in through the app's own OTP routes: ``POST /account/api/login/`` with JSON
    ``phone`` answers ``{"otp": ...}`` (upstream returns the code instead of sending an
    SMS), then ``POST /account/api/verify/`` with JSON ``code`` answers ``{"access": ...}``
    (simplejwt). Returns the ``Authorization: Bearer <access>`` header."""
    phone = SEED_USERS[handle][0]
    answer = client.post("/account/api/login/", json={"phone": phone})
    if answer.status_code != 200:
        raise RuntimeError(f"login as {handle!r} answered {answer.status_code}: {answer.text}")
    answer = client.post("/account/api/verify/", json={"code": answer.json()["otp"]})
    if answer.status_code != 200 or "access" not in answer.json():
        raise RuntimeError(f"verify as {handle!r} answered {answer.status_code}: {answer.text}")
    return {"Authorization": f"Bearer {answer.json()['access']}"}
