# ruff: noqa
"""Task model definition.

This module defines the Task model.
"""

from datetime import UTC, datetime
from typing import ClassVar

from bench.import_repos.projmgmt.app import db  # LATTICE MOD (was `from app import db`: import path)


class Task(db.Model):  # type: ignore[name-defined, misc]
    """Task model representing a task within a project.

    Each task belongs to a project and has a status indicating its completion
    state.

    Attributes:
        id (int): Primary key
        title (str): Task title
        description (str): Task description
        status (str): Task status (pending, in_progress, completed)
        project_id (int): Foreign key to the project this task belongs to
        created_at (datetime): Timestamp of task creation
        updated_at (datetime): Timestamp of last update

    Relationships:
        project (Project): Project this task belongs to (via backref)
    """

    __tablename__ = 'tasks'

    VALID_STATUSES: ClassVar[list[str]] = [
        'pending',
        'in_progress',
        'completed',
    ]

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='pending', nullable=False)
    project_id = db.Column(
        db.Integer,
        db.ForeignKey('projects.id'),
        nullable=False,
        index=True,
    )
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    def to_dict(self) -> dict[str, object]:
        """Convert task object to dictionary representation.

        Returns:
            dict: Dictionary representation of the task
        """
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'status': self.status,
            'project_id': self.project_id,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }

    def __repr__(self) -> str:
        """String representation of Task."""
        return f'<Task {self.title} - {self.status}>'
