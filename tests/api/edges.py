"""A fixture for the endpoint features no other Go-eligible registry program reaches
(plan 5a Task 13, from the Task 12 review's probe), shared by the codegen differential
(program "edges", FastAPI vs the runtime) and the Go equivalence gate (Go vs the
runtime):

* page totals: ``page(..., total=True)`` in a page shape with a ``total`` field;
* ``update(user, ..., unique_or=Conflict(...))`` hitting a UNIQUE index (409);
* an owner transfer, ``update(t, owner=..., else_=NotFound(...))``, to an existing and
  to a missing user (the ``missing`` check);
* trailing-slash routes: ``/reviews/`` is served, ``/reviews`` answers 307 (followed by
  the client, like any redirect), and a 307 to a route of another method;
* UNSET partial updates (``merge_update``): no field, one field, every field, and an
  explicit null written to a nullable column (and refused for a NOT NULL one);
* a 204 delete, and R310 (``one()`` of several rows) as a 500.

It runs on the benchmark dataset (bench.api_datasets) with a UNIQUE index on users'
email, and one more table, Notes, whose ``memo`` column is nullable.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

from bench.api_datasets import api_profile
from bench.api_datasets import build_db as build_dataset
from lattice.profile import Profile
from lattice.typesys import Scalar, Schema

S = Scalar

NOTES = Schema.from_pairs(
    [("id", S.INT), ("customer_id", S.INT), ("title", S.STR), ("memo", S.STR)],
    keys=[("id",)],
    owner="customer_id",
    nullable=["memo"],
)


def _profile() -> Profile:
    base = api_profile()
    assert base.api is not None
    api = dataclasses.replace(
        base.api,
        tables={**base.api.tables, "Notes": NOTES},
        table_names={**base.api.table_names, "Notes": "notes"},
        page_shape={"items": "items", "limit": "limit", "offset": "offset", "total": "total"},
    )
    return dataclasses.replace(base, api=api)


PROFILE = _profile()

# (id, customer_id, title, memo): alice is user 1, bob user 2
NOTE_ROWS = [
    (1, 1, "groceries", "milk"),
    (2, 1, "call mom", None),
    (3, 2, "bob's", "private"),
]
NOTES_AFTER = "SELECT id, customer_id, title, memo FROM notes ORDER BY id"
USERS_AFTER = "SELECT id, email, name FROM users ORDER BY id"
REVIEWS_AFTER = "SELECT id, customer_id, rating, body FROM reviews WHERE id IN (8, 111) ORDER BY id"


def build_db(path: Path) -> None:
    build_dataset(path)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE UNIQUE INDEX ux_users_email ON users(email);
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES users(id),
                title TEXT NOT NULL, memo TEXT
            );
            """
        )
        conn.executemany("INSERT INTO notes VALUES (?, ?, ?, ?)", NOTE_ROWS)
        conn.commit()
    finally:
        conn.close()


PROGRAM = """# lattice: v1
@endpoint("GET", "/reviews/", auth=Admin)
def list_reviews(user: CurrentUser, skip: Int(ge=0) = 0, limit: Int(ge=1, le=100) = 10) -> Page:
    reviews = fetch(Reviews)
    ordered = sort(reviews, by=["id"])
    shown = select(ordered, cols=["id", "rating", "body"])
    result = page(shown, limit=limit, offset=skip, total=True)
    return result


@endpoint("PATCH", "/users/me", auth=Customer)
def patch_me(
    user: CurrentUser, name: Str(max_len=50) = UNSET, email: Str(max_len=80) = UNSET
) -> One | Conflict:
    done = update(user, name=name, email=email, unique_or=Conflict("email taken"))
    shown = select(done, cols=["id", "email", "name"])
    return shown


@endpoint("PATCH", "/reviews/{review_id}", auth=Customer)
def patch_review(
    review_id: Int(ge=1),
    user: CurrentUser,
    rating: Int(ge=1, le=5) = UNSET,
    body: Str(min_len=1, max_len=100) = UNSET,
) -> One | NotFound:
    reviews = fetch(Reviews)
    mine = owned(reviews, by=user)
    found = where(mine, col("id") == review_id)
    review = one(found, else_=NotFound("review"))
    done = update(review, rating=rating, body=body)
    shown = select(done, cols=["id", "rating", "body"])
    return shown


@endpoint("PUT", "/reviews/{review_id}/owner", auth=Admin)
def give(review_id: Int(ge=1), user: CurrentUser, owner_id: Int(ge=1)) -> One | NotFound:
    reviews = fetch(Reviews)
    found = where(reviews, col("id") == review_id)
    review = one(found, else_=NotFound("review"))
    moved = update(review, owner=owner_id, else_=NotFound("User not found"))
    shown = select(moved, cols=["id", "customer_id"])
    return shown


@endpoint("GET", "/any/{pid}", auth=Admin)
def any_review(pid: Int, user: CurrentUser) -> One | NotFound:
    reviews = fetch(Reviews)
    found = where(reviews, col("product_id") == pid)
    r = one(found, else_=NotFound("review"))
    return r


@endpoint("GET", "/reviews/mine", auth=Customer)
def mine_r(user: CurrentUser) -> List:
    reviews = fetch(Reviews)
    m = owned(reviews, by=user)
    few = limit(m, n=3)
    return few


@endpoint("GET", "/reviews/{review_id}", auth=Customer)
def get_r(review_id: Int, user: CurrentUser) -> One | NotFound:
    reviews = fetch(Reviews)
    m = owned(reviews, by=user)
    found = where(m, col("id") == review_id)
    r = one(found, else_=NotFound("review"))
    return r


@endpoint("DELETE", "/reviews/{review_id}", auth=Customer)
def del_review(review_id: Int(ge=1), user: CurrentUser) -> NoContent | NotFound:
    reviews = fetch(Reviews)
    mine = owned(reviews, by=user)
    found = where(mine, col("id") == review_id)
    review = one(found, else_=NotFound("review"))
    gone = delete(review)
    return gone


@endpoint("GET", "/notes/", auth=Customer)
def list_notes(user: CurrentUser, limit: Int(ge=1, le=10) = 5, offset: Int(ge=0) = 0) -> Page:
    notes = fetch(Notes)
    mine = owned(notes, by=user)
    ordered = sort(mine, by=["id"])
    result = page(ordered, limit=limit, offset=offset, total=True)
    return result


@endpoint("PATCH", "/notes/{note_id}", auth=Customer)
def patch_note(
    note_id: Int(ge=1),
    user: CurrentUser,
    title: Str(min_len=1, max_len=40) = UNSET,
    memo: Str(max_len=40) = UNSET,
) -> One | NotFound:
    notes = fetch(Notes)
    mine = owned(notes, by=user)
    found = where(mine, col("id") == note_id)
    note = one(found, else_=NotFound("note"))
    done = update(note, title=title, memo=memo)
    return done
"""
