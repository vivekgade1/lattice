"""Minimal seed data for the vendored JustNotes app (``bench/import_repos/justnotes``).

``build_seed_db(path)`` writes a SQLite database with the upstream schema (created via
this app's isolated registry, see ``app/_lattice_registry.py``) holding two users, a
handful of notes (some private and owned, one public), and a tag/keyword attached to a
note -- enough to exercise ownership checks (``Note.owner_id``) and the many-to-many
link tables.
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session

from bench.import_repos.justnotes import sqlite_engine
from bench.import_repos.justnotes.app._lattice_registry import metadata_registry
from bench.import_repos.justnotes.app.models.note import Note
from bench.import_repos.justnotes.app.models.tag import Keyword, Tag
from bench.import_repos.justnotes.app.models.user import User
from bench.import_repos.justnotes.app.utils.auth import hash_password

# (username, password)
SEED_USERS: tuple[tuple[str, str], ...] = (
    ("alice", "alicepass123"),
    ("bob", "bobpass12345"),
)

SEED_USER_CREDENTIALS: dict[str, str] = dict(SEED_USERS)


def build_seed_db(path: str | Path) -> Path:
    """Create (or overwrite) the SQLite file at ``path`` with the seed data above."""
    path = Path(path)
    if path.exists():
        path.unlink()
    engine = sqlite_engine(path)
    try:
        metadata_registry.metadata.create_all(engine)
        with Session(engine) as session:
            alice = User(username="alice", password=hash_password("alicepass123"))
            bob = User(username="bob", password=hash_password("bobpass12345"))
            session.add(alice)
            session.add(bob)
            session.commit()

            note1 = Note(
                title="Grocery list",
                text="Eggs, milk, bread.",
                owner_id=alice.id,
                isPublic=False,
            )
            note2 = Note(
                title="Trip notes",
                text="Pack sunscreen and a charger.",
                owner_id=alice.id,
                isPublic=True,
            )
            note3 = Note(
                title="Standup notes",
                text="Ship the release.",
                owner_id=bob.id,
                isPublic=False,
            )
            session.add(note1)
            session.add(note2)
            session.add(note3)
            session.commit()

            tag = Tag(name="personal", owner_id=alice.id)
            keyword = Keyword(term="travel", owner_id=alice.id)
            session.add(tag)
            session.add(keyword)
            session.commit()

            note2.tags.append(tag)
            note2.keywords.append(keyword)
            session.add(note2)
            session.commit()
    finally:
        engine.dispose()
    return path
