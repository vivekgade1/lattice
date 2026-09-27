"""Minimal seed data for the vendored TaskFlow API (``bench/import_repos/taskflow``).

``build_seed_db(path)`` writes a SQLite database with the upstream schema (created via
this app's isolated registry, see ``app/_lattice_registry.py``) holding two users and
five tasks split between them, so a held-out import run has an owned resource
(``Task.owner_id``) with rows to exercise list/read/update/delete against.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import Session

from bench.import_repos.taskflow.app._lattice_registry import metadata_registry
from bench.import_repos.taskflow.app.auth import hash_password
from bench.import_repos.taskflow.app.database import build_engine
from bench.import_repos.taskflow.app.models import Task, TaskPriority, TaskStatus, User

USER_EPOCH = datetime(2026, 1, 1, 9, 0, 0)
TASK_EPOCH = datetime(2026, 2, 1, 12, 0, 0)

# (email, full_name, password)
SEED_USERS: tuple[tuple[str, str, str], ...] = (
    ("alice@example.com", "Alice Anders", "alicepass123"),
    ("bob@example.com", "Bob Baker", "bobpass12345"),
)

# (owner index into SEED_USERS, title, description, status, priority, days after TASK_EPOCH)
SEED_TASKS: tuple[tuple[int, str, str | None, TaskStatus, TaskPriority, int], ...] = (
    (
        0,
        "Write onboarding doc",
        "Draft the new-hire runbook.",
        TaskStatus.TODO,
        TaskPriority.MEDIUM,
        0,
    ),
    (
        0,
        "Ship v2 API",
        "Cut the release and tag it.",
        TaskStatus.IN_PROGRESS,
        TaskPriority.HIGH,
        1,
    ),
    (0, "Retire legacy endpoint", None, TaskStatus.DONE, TaskPriority.LOW, 2),
    (1, "Review PR #42", "Focus on the auth changes.", TaskStatus.TODO, TaskPriority.HIGH, 0),
    (1, "Plan sprint", None, TaskStatus.TODO, TaskPriority.MEDIUM, 1),
)

SEED_USER_CREDENTIALS: dict[str, str] = {email: password for email, _, password in SEED_USERS}


def build_seed_db(path: str | Path) -> Path:
    """Create (or overwrite) the SQLite file at ``path`` with the seed data above."""
    path = Path(path)
    if path.exists():
        path.unlink()
    engine = build_engine(f"sqlite:///{path}")
    try:
        metadata_registry.metadata.create_all(engine)
        with Session(engine) as session:
            users = []
            for k, (email, full_name, password) in enumerate(SEED_USERS):
                user = User(
                    email=email,
                    full_name=full_name,
                    password_hash=hash_password(password),
                    created_at=USER_EPOCH + timedelta(days=k),
                )
                session.add(user)
                users.append(user)
            session.commit()
            for owner_idx, title, description, status, priority, days in SEED_TASKS:
                created = TASK_EPOCH + timedelta(days=days)
                session.add(
                    Task(
                        title=title,
                        description=description,
                        status=status,
                        priority=priority,
                        owner_id=users[owner_idx].id,
                        created_at=created,
                        updated_at=created,
                    )
                )
            session.commit()
    finally:
        engine.dispose()
    return path
