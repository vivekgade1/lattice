"""Hand-written Lattice solutions of the edit-slice tickets (plan 2026-09-27, node E3).

``lattice_reference/<ticket_id>/`` holds the ticket's edited copies of the imported
Lattice files (:func:`lattice.importer.lift.lift_repo` on the template): the router
program ``items.lattice.py`` and, when the ticket changes tables or response models,
the complete ``profile.py``. Each is the imported file with the ticket's edit applied
(plus a wider ``ruff: noqa`` line). A ticket built on another (``Ticket.base``) takes the
base's files first, as the Python references do.

They prove each ticket is expressible and give arm L's dry run. The tickets current
Lattice cannot express are in :data:`GAPS` (no solution is written for them).
"""

from __future__ import annotations

from pathlib import Path

from bench.edit_slice.tickets import ticket

LATTICE_REFERENCE_ROOT = Path(__file__).resolve().parent

# ticket -> what current Lattice is missing to express it (see the E3 report).
GAPS: dict[str, str] = {
    "t1_search": (
        "case-insensitive substring match against a parameter: fn.contains(x, sub=) takes "
        "only a literal, a parameter or a constant bound to a literal, so the needle cannot "
        "be case-folded (sub=fn.lower(q) is L404 'computed'), and there is no "
        "case-insensitive contains/LIKE"
    ),
    "t5_title_window": (
        "(a) a condition on whether an optional body field was sent: `title: ... = UNSET` "
        "may only be an update()/insert() value (L404), so 'the request sets title' cannot "
        "be tested; (b) DateTime arithmetic: 'created more than 24 hours ago' needs "
        "fn.now() minus a duration ('-' on DateTime is L402; no interval/date_add for "
        "DateTime in API programs)"
    ),
}


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
