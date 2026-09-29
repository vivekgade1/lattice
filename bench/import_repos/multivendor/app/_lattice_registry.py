"""Lattice-owned: an isolated SQLAlchemy registry for this vendored app.

Not part of upstream multi-vendor-inventory-booking-system; added so this held-out repo
can share a Python process with the other vendored apps under ``bench/``
(``bench/template_app``, ``bench/import_repos/taskflow``, ``bench/import_repos/justnotes``).
See the identical note in ``bench/import_repos/justnotes/app/_lattice_registry.py`` and
this app's ``PROVENANCE.md``, "Local modifications" -- this app's ``users`` table name
is TaskFlow's too, and every one of these apps has a class named ``User``; SQLModel's
table classes share one global registry/``MetaData`` by default, so they would collide.
"""

from __future__ import annotations

from sqlalchemy.orm import registry as _registry
from sqlmodel import SQLModel

metadata_registry = _registry()


class TableBase(SQLModel, registry=metadata_registry):
    """Base for this app's ``table=True`` models. Schemas keep using ``SQLModel``."""
