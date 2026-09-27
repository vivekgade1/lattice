# ruff: noqa
"""Database and API models for users and tasks."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import EmailStr, NaiveDatetime  # LATTICE MOD: see note below
from sqlmodel import Field, SQLModel

from bench.import_repos.taskflow.app._lattice_registry import (  # LATTICE MOD
    TableBase,
)

# LATTICE MOD: every field below that upstream typed plain ``datetime`` is retyped
# ``NaiveDatetime`` (pydantic's alias for the same naive value, no behaviour change).
# This repo pins sqlmodel==0.0.39; the sqlmodel installed here (>=0.0.4x) added a
# ``UTCDateTime`` column type for bare ``datetime`` fields on table models that
# rejects naive values at insert time ("Datetime values must have timezone
# information... or annotate the field with NaiveDatetime for naive storage" --
# exactly what upstream's own ``utc_now()`` produces, by design, "for database
# compatibility"). ``NaiveDatetime`` is the fix the error message itself names.


def utc_now() -> datetime:
    """Return a timezone-naive UTC timestamp for database compatibility."""

    return datetime.now(UTC).replace(tzinfo=None)


class TaskStatus(StrEnum):
    """Supported workflow states."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class TaskPriority(StrEnum):
    """Supported task priorities."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class User(TableBase, table=True):  # LATTICE MOD: TableBase, not SQLModel
    """Persisted application user."""

    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True, max_length=320)
    full_name: str = Field(min_length=2, max_length=120)
    password_hash: str
    is_active: bool = Field(default=True)
    created_at: NaiveDatetime = Field(default_factory=utc_now, nullable=False)


class UserCreate(SQLModel):
    """Payload accepted when registering a user."""

    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class UserPublic(SQLModel):
    """Safe user representation returned to clients."""

    id: int
    email: EmailStr
    full_name: str
    is_active: bool
    created_at: NaiveDatetime


class Token(SQLModel):
    """OAuth2 bearer token response."""

    access_token: str
    token_type: str = "bearer"


class TaskBase(SQLModel):
    """Fields shared by task input and output models."""

    title: str = Field(min_length=3, max_length=120, index=True)
    description: str | None = Field(default=None, max_length=1_000)
    status: TaskStatus = Field(default=TaskStatus.TODO, index=True)
    priority: TaskPriority = Field(default=TaskPriority.MEDIUM, index=True)
    due_date: NaiveDatetime | None = None


class Task(TaskBase, TableBase, table=True):  # LATTICE MOD: + TableBase, not SQLModel
    """Persisted task record."""

    __tablename__ = "tasks"

    id: int | None = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="users.id", index=True)
    created_at: NaiveDatetime = Field(default_factory=utc_now, nullable=False)
    updated_at: NaiveDatetime = Field(default_factory=utc_now, nullable=False)
    completed_at: NaiveDatetime | None = Field(default=None)


class TaskCreate(TaskBase):
    """Payload accepted when creating a task."""


class TaskUpdate(SQLModel):
    """Payload accepted when partially updating a task."""

    title: str | None = Field(default=None, min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=1_000)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_date: NaiveDatetime | None = None


class TaskPublic(TaskBase):
    """Task representation returned to API clients."""

    id: int
    owner_id: int
    created_at: NaiveDatetime
    updated_at: NaiveDatetime
    completed_at: NaiveDatetime | None = None


class TaskList(SQLModel):
    """Paginated task collection."""

    items: list[TaskPublic]
    total: int
    limit: int
    offset: int
