# ruff: noqa
from sqlmodel import Field, SQLModel, Relationship
from typing import TYPE_CHECKING
import time

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


class NoteTagLink(TableBase, table=True):  # LATTICE MOD: TableBase, not SQLModel
    """
    Association table linking Notes and Tags (many-to-many relationship).

    Attributes:
        note_id: Foreign key to Note
        tag_id: Foreign key to Tag
    """

    note_id: int = Field(foreign_key="note.id", primary_key=True)
    tag_id: int = Field(foreign_key="tag.id", primary_key=True)


class Tag(TableBase, table=True):  # LATTICE MOD: TableBase, not SQLModel
    """
    Tag model for categorizing and labeling notes.

    Attributes:
        id: Primary key (auto-generated)
        name: Tag name (e.g., "work", "personal", "urgent")
        category: Optional category (e.g., "Work", "Personal", "Ideas")
        owner_id: User who created this tag
        createdAt: Unix timestamp when tag was created
        notes: Relationship to notes that have this tag
    """

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    category: str | None = Field(default=None)
    owner_id: int = Field(foreign_key="user.id")
    createdAt: int = Field(default_factory=get_current_time)

    notes: list["Note"] = Relationship(back_populates="tags", link_model=NoteTagLink)


class NoteKeywordLink(TableBase, table=True):  # LATTICE MOD: TableBase, not SQLModel
    """
    Association table linking Notes and Keywords (many-to-many relationship).

    Attributes:
        note_id: Foreign key to Note
        keyword_id: Foreign key to Keyword
    """

    note_id: int = Field(foreign_key="note.id", primary_key=True)
    keyword_id: int = Field(foreign_key="keyword.id", primary_key=True)


class Keyword(TableBase, table=True):  # LATTICE MOD: TableBase, not SQLModel
    """
    Keyword model for extracting key terms from notes.

    Attributes:
        id: Primary key (auto-generated)
        term: Keyword term (e.g., "machine learning", "budget")
        is_main_topic: Whether this is a main topic/theme
        owner_id: User who owns notes with this keyword
        createdAt: Unix timestamp when keyword was created
        notes: Relationship to notes that have this keyword
    """

    id: int | None = Field(default=None, primary_key=True)
    term: str = Field(index=True)
    is_main_topic: bool = Field(default=False)
    owner_id: int = Field(foreign_key="user.id")
    createdAt: int = Field(default_factory=get_current_time)

    notes: list["Note"] = Relationship(
        back_populates="keywords", link_model=NoteKeywordLink
    )
