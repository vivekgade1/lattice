"""Hand-written Lattice solutions of the edit-slice tickets (plan 2026-09-27, node E3).

``lattice_reference/<ticket_id>/`` holds the ticket's edited copies of the imported
Lattice files (:func:`lattice.importer.lift.lift_repo` on the template): the router
program it touches (``items.lattice.py`` or ``users.lattice.py``) and, when the ticket
changes tables or response models, the complete ``profile.py``. Each is the imported
file with the ticket's edit applied (plus a wider ``ruff: noqa`` line). A ticket built
on another (``Ticket.base``) takes the base's files first, as the Python references do.

They prove each ticket is expressible and give arm L's dry run. A ticket Lattice cannot
express is listed in :data:`GAPS` instead (no solution is written for it).

The expressible held-out tickets (t7-t11) were solved with the language as it stood,
without changing the language, runtime, codegen, checker or guides. Notes on their
solutions: t7's self-deactivation check joins the caller's id in (``rename`` + ``join(on=[])``);
t9 counts with ``mine`` (an admin's own items only); t10 left-joins a per-owner
``group`` onto the users and ``coalesce``s the missing counts to 0; t11 cannot compute
with the ``= UNSET`` title (``fn.strip(title)`` is refused), so it writes the sent title
and then strips the column in a second ``update`` guarded by ``sent(title)``, with the
blank check as a ``require`` after it (the transaction rolls both back on a 400).
"""

from __future__ import annotations

from pathlib import Path

from bench.edit_slice.tickets import ticket

LATTICE_REFERENCE_ROOT = Path(__file__).resolve().parent

# ticket -> what current Lattice is missing to express it. The baseline six have no gap
# since the gap features (case-insensitive contains, sent(), DateTime arithmetic,
# `| None` parameters, null writes, the sort direction from a parameter) closed the E3
# report's two (t1_search, t5_title_window). Held-out gaps are recorded as found.
GAPS: dict[str, str] = {
    "t12_transfer": (
        "Changing a row's owner. update() refuses to write the owner column (L802 "
        "'update() changes the owner column owner_id of Item ... rows keep their "
        "owner'), and an update() value cannot come from another table's row (the new "
        "owner's id, found by email, can only be attached with a join, and update() "
        "after a join is L841). insert() only takes owner=user (the caller), so "
        "delete + re-insert cannot hand the item to someone else either (and would "
        "change its id). Missing: an owner-transfer write, e.g. update(t, "
        "owner=<one-row users table>) checked like owner_is, or update() values read "
        "from a joined one-row table."
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
