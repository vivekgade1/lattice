"""Hand-written Lattice solutions of the edit-slice tickets (plan 2026-09-27, node E3).

``lattice_reference/<ticket_id>/`` holds the ticket's edited copies of the imported
Lattice files (:func:`lattice.importer.lift.lift_repo` on the template): the router
program ``items.lattice.py`` and, when the ticket changes tables or response models,
the complete ``profile.py``. Each is the imported file with the ticket's edit applied
(plus a wider ``ruff: noqa`` line). A ticket built on another (``Ticket.base``) takes the
base's files first, as the Python references do.

They prove each ticket is expressible and give arm L's dry run. A ticket Lattice cannot
express would be listed in :data:`GAPS` (no solution is written for it); none is.
"""

from __future__ import annotations

from pathlib import Path

from bench.edit_slice.tickets import ticket

LATTICE_REFERENCE_ROOT = Path(__file__).resolve().parent

# ticket -> what current Lattice is missing to express it. Empty since the gap features
# (case-insensitive contains, sent(), DateTime arithmetic, `| None` parameters, null
# writes, the sort direction from a parameter) closed the E3 report's two gaps
# (t1_search, t5_title_window).
GAPS: dict[str, str] = {}


def _own_files(ticket_id: str) -> dict[str, Path]:
    root = LATTICE_REFERENCE_ROOT / ticket_id
    if not root.is_dir():
        raise KeyError(f"no Lattice reference for ticket {ticket_id!r}")
    return {p.name: p for p in sorted(root.iterdir()) if p.suffix == ".py"}


def lattice_reference_paths(ticket_id: str) -> dict[str, Path]:
    """File name -> reference file, the base ticket's files first."""
    base = ticket(ticket_id).base
    return {**(lattice_reference_paths(base) if base else {}), **_own_files(ticket_id)}


def lattice_reference_files(ticket_id: str) -> dict[str, str]:
    """The edited Lattice files (``items.lattice.py``, maybe ``profile.py``) -> source."""
    return {name: p.read_text() for name, p in lattice_reference_paths(ticket_id).items()}


def has_lattice_reference(ticket_id: str) -> bool:
    return ticket_id not in GAPS and (LATTICE_REFERENCE_ROOT / ticket_id).is_dir()


__all__ = [
    "GAPS",
    "has_lattice_reference",
    "lattice_reference_files",
    "lattice_reference_paths",
]
