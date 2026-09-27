"""Lattice-owned: an isolated SQLAlchemy registry for this vendored app.

Not part of upstream TaskFlow; added so this held-out repo can share a Python
process with the other vendored apps under ``bench/`` (``bench/template_app``,
``bench/import_repos/justnotes``). See the identical note in
``bench/import_repos/justnotes/app/_lattice_registry.py`` and this app's
``PROVENANCE.md``, "Local modifications" -- every one of those apps happens to have
a ``User`` table, and SQLModel's table classes share one global registry/``MetaData``
by default, so two of them in one process collide on the literal table name.
"""

from __future__ import annotations

from sqlalchemy.orm import registry as _registry
from sqlmodel import SQLModel

metadata_registry = _registry()


class TableBase(SQLModel, registry=metadata_registry):
    """Base for this app's ``table=True`` models. Schemas keep using ``SQLModel``."""
