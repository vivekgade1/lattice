"""Hand-written reference implementations of the edit-slice tickets.

``reference/<ticket_id>/`` holds the ticket's patched copies of the template files it
changes, laid out as under ``bench/template_app/app`` (``models.py``,
``api/routes/items.py``). Each file is the template's own file with the ticket's edit
applied -- a plain diff against the template shows the change. A ticket built on another
(``Ticket.base``: the restore ticket needs soft delete) takes the base's files first.

The references are loaded as :mod:`bench.edit_slice.variant` variants: the template
package with these files swapped in, imported beside the template with its own SQLModel
metadata (so ``deleted_at`` exists only in the soft-delete variants' ``item`` table).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from fastapi import FastAPI

from bench.edit_slice.tickets import ticket
from bench.edit_slice.variant import Variant, load_variant

REFERENCE_ROOT = Path(__file__).resolve().parent


def _own_files(ticket_id: str) -> dict[str, Path]:
    root = REFERENCE_ROOT / ticket_id
    if not root.is_dir():
        raise KeyError(f"no reference for ticket {ticket_id!r}")
    return {
        p.relative_to(root).as_posix(): p
        for p in sorted(root.rglob("*.py"))
        if "__pycache__" not in p.parts
    }


def reference_paths(ticket_id: str) -> dict[str, Path]:
    """Overlay path -> reference file, the base ticket's files first."""
    base = ticket(ticket_id).base
    return {**(reference_paths(base) if base else {}), **_own_files(ticket_id)}


def reference_files(ticket_id: str) -> dict[str, str]:
    """The reference overlay: path (relative to ``bench/template_app/app``) -> source."""
    return {rel: p.read_text() for rel, p in reference_paths(ticket_id).items()}


@cache
def reference_variant(ticket_id: str) -> Variant:
    return load_variant(reference_files(ticket_id), origins=reference_paths(ticket_id))


def make_reference_app(ticket_id: str, db_path: str | Path) -> FastAPI:
    """The template app with ticket ``ticket_id`` implemented, on the SQLite file at
    ``db_path`` (tables created from the reference's models when missing)."""
    return reference_variant(ticket_id).make_app(db_path)
