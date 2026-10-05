"""Error conventions of non-FastAPI apps (phase 2 plan, W1-D): ``ApiProfile.error_body``
``"drf"`` / ``"json_key(<key>)"`` and ``ApiProfile.validation_status``.

One program, run on three profiles of one database:

* ``DRF`` -- ``error_body="drf"``, ``validation_status=400``: every error is
  ``{"detail": ...}`` (the HTTP status phrase when there is no detail), a validation
  error is 400 ``{field: [msgs]}`` (``non_field_errors`` for a body that is not an
  object), as Django REST framework answers;
* ``JSON_KEY`` -- ``error_body="json_key(message)"``, ``validation_status=400``: every
  error is ``{"message": ...}``, a validation error 400 ``{"message": [{loc, msg}]}``,
  as a Flask app's ``jsonify(message=...)`` handlers answer;
* ``LATTICE_400`` -- the default ``"lattice"`` body with ``validation_status=400``.

The program answers every kind of error: 401 (no or unknown token), 403 (a Customer on
an Admin endpoint), 404 (a declared NotFound and an unknown route), 405, 400 (a
declared BadRequest), 409 (a unique conflict), and validation errors of the path, the
query and the body (several fields at once, and a body that is not an object).

Shared by tests/api/test_error_styles.py and the codegen differential.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from lattice.profile import ApiProfile, Profile
from lattice.typesys import Scalar, Schema

S = Scalar

USER = Schema.from_pairs(
    [("id", S.INT), ("token", S.STR), ("name", S.STR), ("role", S.STR)],
    keys=[("id",), ("token",)],
    private=["token"],
)
NOTE = Schema.from_pairs(
    [("id", S.INT), ("owner_id", S.INT), ("title", S.STR), ("rank", S.INT)],
    keys=[("id",), ("title",)],
    owner="owner_id",
)

BASE = ApiProfile(
    tables={"User": USER, "Note": NOTE},
    table_names={"User": "users", "Note": "notes"},
    roles=frozenset({"Public", "Customer", "Admin"}),
    users_table="User",
)
UNAUTHORIZED = "Authentication credentials were not provided."
DRF = replace(BASE, error_body="drf", validation_status=400, unauthorized_detail=UNAUTHORIZED)
JSON_KEY = replace(BASE, error_body="json_key(message)", validation_status=400)
LATTICE_400 = replace(BASE, validation_status=400)
PROFILES = {
    "drf": Profile(schemas={}, api=DRF),
    "json_key": Profile(schemas={}, api=JSON_KEY),
    "lattice_400": Profile(schemas={}, api=LATTICE_400),
}

# name -> (id, role)
USERS = {"ann": (1, "Customer"), "bob": (2, "Customer"), "ada": (3, "Admin")}
TOKENS = {name: f"tok-{name}" for name in USERS}
NOTES = [(1, 1, "ann's", 1), (2, 2, "bob's", 2), (3, 1, "ann's too", 3)]


def build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL, role TEXT NOT NULL
            );
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
                title TEXT NOT NULL UNIQUE, rank INTEGER NOT NULL
            );
            """
        )
        conn.executemany(
            "INSERT INTO users VALUES (?, ?, ?, ?)",
            [(i, TOKENS[n], n, role) for n, (i, role) in USERS.items()],
        )
        conn.executemany("INSERT INTO notes VALUES (?, ?, ?, ?)", NOTES)
        conn.commit()
    finally:
        conn.close()


PROGRAM = """# lattice: v1
@endpoint("GET", "/notes/{note_id}", auth=Customer)
def read_note(note_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    notes = fetch(Note)
    mine = owned(notes, by=user)
    found = where(mine, col("id") == note_id)
    note = one(found, else_=NotFound("Note not found"))
    shown = select(note, cols=["id", "title", "rank"])
    return shown


@endpoint("GET", "/notes", auth=Customer)
def list_notes(user: CurrentUser, limit: Int(ge=1, le=50) = 10, offset: Int(ge=0) = 0) -> Page:
    notes = fetch(Note)
    mine = owned(notes, by=user)
    ordered = sort(mine, by=["id"])
    shown = select(ordered, cols=["id", "title", "rank"])
    result = page(shown, limit=limit, offset=offset)
    return result


@endpoint("POST", "/notes", auth=Customer)
def create_note(
    user: CurrentUser, title: Str(min_len=1, max_len=20), rank: Int(ge=0, le=9) = 0
) -> Created | BadRequest | Conflict:
    made = insert(Note, owner=user, title=title, rank=rank, unique_or=Conflict("Title taken"))
    checked = require(made, col("title") != "nope", else_=BadRequest("title refused"))
    shown = select(checked, cols=["id", "title", "rank"])
    return shown


@endpoint("DELETE", "/notes/{note_id}", auth=Admin)
def delete_note(note_id: Int(ge=1), user: CurrentUser) -> NoContent | NotFound:
    notes = fetch(Note)
    found = where(notes, col("id") == note_id)
    note = one(found, else_=NotFound("Note not found"))
    gone = delete(note)
    return gone
"""

NOTES_AFTER = "SELECT id, owner_id, title, rank FROM notes ORDER BY id"
