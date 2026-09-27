# ruff: noqa
from pydantic import BaseModel
from sqlmodel import Field, SQLModel, Relationship
import time
from typing import List, TYPE_CHECKING

from .._lattice_registry import TableBase  # LATTICE MOD: see _lattice_registry.py

if TYPE_CHECKING:
    from .note import Note


def get_current_time() -> int:
    """
    Get current Unix timestamp.

    Returns:
        int: Current Unix timestamp in seconds
    """
    return int(time.time())


class UserBase(BaseModel):
    """
    Base model for user data (without database fields).

    Attributes:
        username: Unique username for the user
        password: User's password (should be hashed when stored)
    """

    username: str
    password: str


class User(UserBase, TableBase, table=True):  # LATTICE MOD: TableBase, not SQLModel
    """
    Complete user model with database fields.

    Inherits from UserBase and adds database-specific fields.
    Represents the users table in the database.

    Attributes:
        id: Primary key (auto-generated)
        createdAt: Unix timestamp when user was created
        updatedAt: Unix timestamp when user was last updated
        notes: Relationship to all notes created by this user
    """

    id: int | None = Field(default=None, primary_key=True)
    createdAt: int = Field(default_factory=get_current_time)
    updatedAt: int = Field(default_factory=get_current_time)

    # Relationship to Note model
    notes: List["Note"] = Relationship(back_populates="owner")


class UserResponse(BaseModel):
    """
    Response model for user data (excludes password).

    Used for API responses to include user information
    without exposing the password hash.

    Attributes:
        id: User ID
        username: User's username
        createdAt: Creation timestamp
        updatedAt: Last update timestamp
    """

    id: int
    username: str
    createdAt: int
    updatedAt: int
