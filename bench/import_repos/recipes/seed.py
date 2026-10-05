"""Seed data for the vendored recipe-app-api (``bench/import_repos/recipes``).

``build_seed_db(path)`` migrates a fresh SQLite file and fills it through the app's own
ORM (``UserManager.create_user`` / ``create_superuser``, so passwords are hashed by
Django's ``make_password``). Roles: the superuser/staff ``admin`` and plain users;
``carol`` is inactive. ``alice`` and ``bob`` each own tags, ingredients and recipes
(many-to-many links included); ``carol`` owns one tag; ``admin`` owns nothing.

Must run in a process whose Django project is this one (see ``_django_runtime``).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from bench.import_repos.recipes import dispose, make_app

# handle -> (email, password, name, is_superuser, is_active)
SEED_USERS: dict[str, tuple[str, str, str, bool, bool]] = {
    "admin": ("admin@example.com", "adminpass1", "Ada Admin", True, True),
    "alice": ("alice@example.com", "alicepass1", "Alice Archer", False, True),
    "bob": ("bob@example.com", "bobpass1", "Bob Baker", False, True),
    "carol": ("carol@example.com", "carolpass1", "Carol Cook", False, False),
}

# (owner, name)
SEED_TAGS: tuple[tuple[str, str], ...] = (
    ("alice", "Vegan"),  # 1
    ("alice", "Dessert"),  # 2
    ("bob", "Breakfast"),  # 3
    ("bob", "Quick"),  # 4
    ("carol", "Spicy"),  # 5
)
SEED_INGREDIENTS: tuple[tuple[str, str], ...] = (
    ("alice", "Kale"),  # 1
    ("alice", "Salt"),  # 2
    ("bob", "Eggs"),  # 3
    ("bob", "Flour"),  # 4
    ("bob", "Butter"),  # 5
    ("alice", "Pepper"),  # 6 -- on no recipe (assigned_only filtering)
)
# (owner, title, time_minutes, price, link, tag ids, ingredient ids)
SEED_RECIPES: tuple[tuple[str, str, int, str, str, tuple[int, ...], tuple[int, ...]], ...] = (
    ("alice", "Kale chips", 20, "4.50", "", (1,), (1, 2)),  # 1
    ("alice", "Vegan brownies", 45, "9.00", "https://example.com/brownies", (1, 2), ()),  # 2
    ("bob", "Pancakes", 15, "3.25", "", (3, 4), (3, 4, 5)),  # 3
    ("bob", "Omelette", 10, "2.00", "", (3,), (3,)),  # 4
)

USER_IDS = {"admin": 1, "alice": 2, "bob": 3, "carol": 4}

# role -> the seed user who holds it (Django: superuser/staff vs plain user)
ROLES: dict[str, str] = {"admin": "admin", "user": "alice"}
# seed users who exist but must be refused, with the reason
REFUSED: dict[str, str] = {"carol": "is_active=False"}
# a route only an authenticated caller passes: (method, path, json)
AUTH_PROBE: tuple[str, str, dict | None] = ("GET", "/api/user/me/", None)


def build_seed_db(path: str | Path) -> Path:
    """Create (or overwrite) the SQLite file at ``path`` with the seed data above."""
    path = Path(path)
    if path.exists():
        path.unlink()
    make_app(path)
    from django.contrib.auth import get_user_model

    from bench.import_repos.recipes.app.core.models import Ingredient, Recipe, Tag

    User = get_user_model()
    try:
        users = {}
        for handle, (email, password, name, superuser, active) in SEED_USERS.items():
            if superuser:
                user = User.objects.create_superuser(email, password)
                user.name = name
                user.save()
            else:
                user = User.objects.create_user(email, password, name=name, is_active=active)
            users[handle] = user
        tags = [Tag.objects.create(user=users[o], name=n) for o, n in SEED_TAGS]
        ingredients = [
            Ingredient.objects.create(user=users[o], name=n) for o, n in SEED_INGREDIENTS
        ]
        for owner, title, minutes, price, link, tag_ids, ingredient_ids in SEED_RECIPES:
            recipe = Recipe.objects.create(
                user=users[owner],
                title=title,
                time_minutes=minutes,
                price=Decimal(price),
                link=link,
            )
            recipe.tags.set([tags[i - 1] for i in tag_ids])
            recipe.ingredients.set([ingredients[i - 1] for i in ingredient_ids])
    finally:
        dispose()
    return path


def login_headers(client: Any, handle: str) -> dict[str, str]:
    """Log in through the app's own route (``POST /api/user/token/`` with JSON
    ``email``/``password``; answer ``{"token": ...}``) and return the
    ``Authorization: Token <key>`` header (DRF ``TokenAuthentication``)."""
    email, password, _, _, _ = SEED_USERS[handle]
    answer = client.post("/api/user/token/", json={"email": email, "password": password})
    if answer.status_code != 200:
        raise RuntimeError(f"login as {handle!r} answered {answer.status_code}: {answer.text}")
    return {"Authorization": f"Token {answer.json()['token']}"}
