"""Deterministic seed data for the vendored FastAPI template.

``build_template_db(path, seed=31)`` writes a SQLite database with the template's own
schema (``create_template_tables``) holding one superuser, three normal users and
twenty items. Everything is a pure function of ``seed``: ids are ``uuid5`` values under
a seed-derived namespace, ``created_at`` values are fixed instants, item owners are
drawn from ``random.Random(seed)``, and password hashes are produced by the template's
own ``core.security.password_hash`` (Argon2) with a salt derived from the seed, so the
file content is reproducible byte-for-byte at the row level.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlmodel import Session

from bench.template_app import create_template_tables, sqlite_engine
from bench.template_app.app.core import security
from bench.template_app.app.models import Item, User

DEFAULT_SEED = 31
USER_EPOCH = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
ITEM_EPOCH = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
N_ITEMS = 20


@dataclass(frozen=True)
class SeedUser:
    handle: str
    email: str
    password: str
    full_name: str | None
    is_superuser: bool


@dataclass(frozen=True)
class SeedItem:
    index: int
    id: uuid.UUID
    title: str
    description: str | None
    owner: str  # user handle
    created_at: datetime


_USERS: tuple[SeedUser, ...] = (
    SeedUser("admin", "admin@example.com", "adminpass123", "Admin Istrator", True),
    SeedUser("alice", "alice@example.com", "alicepass123", "Alice Anders", False),
    SeedUser("bob", "bob@example.com", "bobpass12345", None, False),
    SeedUser("carol", "carol@example.com", "carolpass123", "Carol Chen", False),
)


def namespace(seed: int = DEFAULT_SEED) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://lattice.invalid/template-app/{seed}")


def user_id(handle: str, seed: int = DEFAULT_SEED) -> uuid.UUID:
    return uuid.uuid5(namespace(seed), f"user/{handle}")


def item_id(index: int, seed: int = DEFAULT_SEED) -> uuid.UUID:
    return uuid.uuid5(namespace(seed), f"item/{index}")


def template_users(seed: int = DEFAULT_SEED) -> dict[str, tuple[str, str, uuid.UUID]]:
    """handle -> (email, password, id)."""
    return {u.handle: (u.email, u.password, user_id(u.handle, seed)) for u in _USERS}


def template_items(seed: int = DEFAULT_SEED) -> list[SeedItem]:
    """The seeded items in insertion order.

    Owners are drawn from ``random.Random(seed)`` but every user owns at least two items;
    ``created_at`` values are distinct hours after ``ITEM_EPOCH`` in a shuffled order so
    that insertion order, id order and ``created_at`` order all differ.
    """
    rng = random.Random(seed)
    handles = [u.handle for u in _USERS]
    owners = handles * 2 + [rng.choice(handles) for _ in range(N_ITEMS - 2 * len(handles))]
    rng.shuffle(owners)
    hours = list(range(N_ITEMS))
    rng.shuffle(hours)
    items = []
    for i in range(N_ITEMS):
        description = None if i % 4 == 3 else f"Seeded item {i + 1:02d} ({owners[i]})"
        items.append(
            SeedItem(
                index=i,
                id=item_id(i, seed),
                title=f"Item {i + 1:02d}",
                description=description,
                owner=owners[i],
                created_at=ITEM_EPOCH + timedelta(hours=hours[i]),
            )
        )
    return items


def _password_hash(handle: str, password: str, seed: int) -> str:
    # The template's own Argon2 hasher (first in security.password_hash) with a
    # deterministic salt, so the seed is reproducible; verify_password accepts it.
    salt = hashlib.sha256(f"{seed}/{handle}".encode()).digest()[:16]
    return security.password_hash.hashers[0].hash(password, salt=salt)


def build_template_db(path: str | Path, seed: int = DEFAULT_SEED) -> Path:
    """Create (or overwrite) the SQLite file at ``path`` with the seed data."""
    path = Path(path)
    if path.exists():
        path.unlink()
    engine = sqlite_engine(path)
    try:
        create_template_tables(engine)
        with Session(engine) as session:
            for k, u in enumerate(_USERS):
                session.add(
                    User(
                        id=user_id(u.handle, seed),
                        email=u.email,
                        full_name=u.full_name,
                        is_active=True,
                        is_superuser=u.is_superuser,
                        hashed_password=_password_hash(u.handle, u.password, seed),
                        created_at=USER_EPOCH + timedelta(days=k),
                    )
                )
            session.commit()
            for it in template_items(seed):
                session.add(
                    Item(
                        id=it.id,
                        title=it.title,
                        description=it.description,
                        owner_id=user_id(it.owner, seed),
                        created_at=it.created_at,
                    )
                )
            session.commit()
    finally:
        engine.dispose()
    return path


TEMPLATE_USERS: dict[str, tuple[str, str, uuid.UUID]] = template_users()
TEMPLATE_ITEMS: list[SeedItem] = template_items()
