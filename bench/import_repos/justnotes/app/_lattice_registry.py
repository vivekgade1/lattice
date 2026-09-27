"""Lattice-owned: an isolated SQLAlchemy registry for this vendored app.

Not part of upstream JustNotes; added so this held-out repo can share a Python
process with the other vendored apps under ``bench/`` (``bench/template_app``,
``bench/import_repos/taskflow``). SQLModel's table classes share one global
registry/``MetaData`` by default, and every one of those apps happens to have a
``User`` table -- two of them colliding in one process shows up either as
"Table 'user' is already defined for this MetaData instance" (same default table
name) or, once any ``Relationship`` needs to resolve a string like ``"User"`` or
``"Note"``, as "Multiple classes found for path 'User' in the registry of this
declarative base". Giving this app's ``table=True`` models their own
``sqlalchemy.orm.registry()`` via ``TableBase`` isolates them from every other
vendored app without changing any model field, route, or behaviour. See
``PROVENANCE.md``, "Local modifications".
"""

from __future__ import annotations

from sqlalchemy.orm import registry as _registry
from sqlmodel import SQLModel

metadata_registry = _registry()


class TableBase(SQLModel, registry=metadata_registry):
    """Base for this app's ``table=True`` models. Schemas keep using ``SQLModel``."""
