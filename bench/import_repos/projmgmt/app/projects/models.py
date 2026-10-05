# ruff: noqa
"""Project model definition.

This module defines the Project model.
"""

from datetime import UTC, datetime

from bench.import_repos.projmgmt.app import db  # LATTICE MOD (was `from app import db`: import path)


class Project(db.Model):  # type: ignore[name-defined, misc]
    """Project model representing a project in the system.

    Each project belongs to a user and can have multiple tasks.

    Attributes:
        id (int): Primary key
        name (str): Project name
        description (str): Project description
        user_id (int): Foreign key to the user who owns this project
        created_at (datetime): Timestamp of project creation
        updated_at (datetime): Timestamp of last update

    Relationships:
        tasks (list[Task]): Tasks belonging to this project
        owner (User): User who owns this project (via backref)
    """

    __tablename__ = 'projects'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id'),
        nullable=False,
        index=True,
    )
    created_at = db.Column(
        db.DateTime,
        default=datetime.now(UTC),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime,
        default=datetime.now(UTC),
        onupdate=datetime.now(UTC),
        nullable=False,
    )

    tasks = db.relationship(
        'Task',
        backref='project',
        lazy='dynamic',
        cascade='all, delete-orphan',
    )

    def to_dict(self, *, include_tasks: bool = False) -> dict[str, object]:
        """Convert project object to dictionary representation.

        Args:
            include_tasks (bool): Whether to include tasks in the response

        Returns:
            dict: Dictionary representation of the project
        """
        result = {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'user_id': self.user_id,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }

        if include_tasks:
            result['tasks'] = [task.to_dict() for task in self.tasks.all()]

        return result

    def __repr__(self) -> str:
        """String representation of Project."""
        return f'<Project {self.name}>'
