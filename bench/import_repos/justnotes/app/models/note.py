# ruff: noqa
from pydantic import BaseModel
from sqlmodel import Field, SQLModel, Relationship
import time
from typing import Optional, TYPE_CHECKING

from .._lattice_registry import TableBase  # LATTICE MOD: see _lattice_registry.py
from ..models.user import UserResponse

if TYPE_CHECKING:
    from .user import User
    from .tag import Tag, Keyword


def get_current_time() -> int:
    """
    Get current Unix timestamp.

    Returns:
        int: Current Unix timestamp in seconds
    """
    return int(time.time())


class NoteBase(BaseModel):
    """
    Base model for note data (without database fields).

    Attributes:
        title: Title of the note
        text: Content/body of the note
        isPublic: Whether the note is publicly accessible
    """

    title: str
    text: str
    isPublic: bool


class Note(NoteBase, TableBase, table=True):  # LATTICE MOD: TableBase, not SQLModel
    """
    Complete note model with database fields.

    Inherits from NoteBase and adds database-specific fields.
    Represents the notes table in the database.

    Attributes:
        id: Primary key (auto-generated)
        createdAt: Unix timestamp when note was created
        updatedAt: Unix timestamp when note was last updated
        owner: Relationship to the User who created this note
    """

    id: int | None = Field(default=None, primary_key=True)
    createdAt: int = Field(default_factory=get_current_time)
    updatedAt: int = Field(default_factory=get_current_time)

    # Foreign Key to User table
    owner_id: int = Field(foreign_key="user.id")

    # Relationship to User model
    owner: Optional["User"] = Relationship(back_populates="notes")

    # Relationships to Tags and Keywords (defined after Note class to avoid circular imports)
    tags: list["Tag"] = Relationship(
        back_populates="notes",
        sa_relationship_kwargs={"secondary": "notetaglink"},
    )
    keywords: list["Keyword"] = Relationship(
        back_populates="notes",
        sa_relationship_kwargs={"secondary": "notekeywordlink"},
    )


class NoteResponse(NoteBase):
    """
    Response model for note data (includes database fields and owner).

    Used for API responses to include all note information
    including timestamps, ID, and owner details.

    Attributes:
        id: Note ID
        createdAt: Creation timestamp
        updatedAt: Last update timestamp
        owner: User who created this note (optional)
    """

    id: int
    createdAt: int
    updatedAt: int
    owner: Optional[UserResponse] = None
