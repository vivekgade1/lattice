"""SQLModel tables for the synthetic app of test_trace.py: authors and their books.

No ``from __future__ import annotations`` here: SQLModel resolves ``Relationship``
annotations at class creation. The tables live in their own registry, so the template's
``SQLModel.metadata.create_all`` never creates them.
"""

from typing import Optional

from sqlalchemy.orm import registry
from sqlmodel import Field, Relationship, SQLModel


class Library(SQLModel, registry=registry()):
    """The base of the synthetic tables (its own metadata)."""


class Author(Library, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    books: list["Book"] = Relationship(back_populates="author")


class Book(Library, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
    author_id: int = Field(foreign_key="author.id")
    author: Optional[Author] = Relationship(back_populates="books")  # noqa: UP045
