"""A fixture for three review findings, shared by tests/api/test_hardening_runtime.py and
the codegen differential (program "hardening"):

* the caller's row decoded from its stored spellings -- the users columns ``role``
  (a ``RoleColumn``) and ``plan`` are ``OneOf`` columns the database spells in capitals
  (``"PRO"`` for ``"pro"``), so ``user.plan`` in SQL, ``derive(p=user.plan)``, the role
  check and ``caller.plan`` in a procedure all see ``"pro"``;
* ``insert_many(Notes, rows=..., owner=user)`` in a Customer endpoint;
* size-aware fuel: string / list repetition, padding, powers, shifts, concatenation,
  format specs and ``json.dumps`` are charged their result's size before they run (R620),
  and no value may exceed ``ApiProfile.max_value_size`` (R622).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lattice.profile import ApiProfile, Profile
from lattice.typesys import Scalar, Schema

S = Scalar
FUEL = 50_000
MAX_VALUE_SIZE = 100_000

USERS = Schema.from_pairs(
    [("id", S.INT), ("email", S.STR), ("role", S.STR), ("plan", S.STR), ("token", S.STR)],
    keys=[("id",)],
    private=["token"],
    one_of={"role": ("Customer", "Admin"), "plan": ("free", "pro")},
    one_of_stored={"role": ("CUSTOMER", "ADMIN"), "plan": ("FREE", "PRO")},
)
OFFERS = Schema.from_pairs([("id", S.INT), ("tier", S.STR), ("title", S.STR)], keys=[("id",)])
NOTES = Schema.from_pairs(
    [("id", S.INT), ("owner_id", S.INT), ("title", S.STR)], keys=[("id",)], owner="owner_id"
)

API = ApiProfile(
    tables={"Users": USERS, "Offers": OFFERS, "Notes": NOTES},
    table_names={"Users": "users", "Offers": "offers", "Notes": "notes"},
    roles=frozenset({"Public", "Customer", "Admin"}),
    fuel=FUEL,
    max_value_size=MAX_VALUE_SIZE,
)
PROFILE = Profile(schemas={}, api=API)

TOKENS = {"admin": "tok-admin", "alice": "tok-alice", "bob": "tok-bob"}
USER_ROWS = [
    (1, "admin@example.com", "ADMIN", "PRO", TOKENS["admin"]),
    (2, "alice@example.com", "CUSTOMER", "PRO", TOKENS["alice"]),
    (3, "bob@example.com", "CUSTOMER", "FREE", TOKENS["bob"]),
]
OFFER_ROWS = [(1, "free", "Basic"), (2, "pro", "Gold"), (3, "pro", "Platinum")]
NOTE_ROWS = [(1, 2, "first")]


def build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, email TEXT NOT NULL, role TEXT NOT NULL,
                plan TEXT NOT NULL, token TEXT NOT NULL UNIQUE
            );
            CREATE TABLE offers (id INTEGER PRIMARY KEY, tier TEXT NOT NULL, title TEXT NOT NULL);
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, title TEXT NOT NULL
            );
            """
        )
        conn.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?)", USER_ROWS)
        conn.executemany("INSERT INTO offers VALUES (?, ?, ?)", OFFER_ROWS)
        conn.executemany("INSERT INTO notes VALUES (?, ?, ?)", NOTE_ROWS)
        conn.commit()
    finally:
        conn.close()


PROGRAM = """# lattice: v1


@record
class Draft:
    title: Str


@procedure
def drafts(title: Str, n: Int) -> list[Draft]:
    return [Draft(title=f"{title} {i}") for i in range(n)]


@procedure
def plan_of(caller: Caller) -> Str:
    return caller.plan


@procedure
def padded(s: Str, n: Int) -> Str:
    return s.ljust(n, ".")


@procedure
def grown(n: Int, k: Int) -> Int:
    total = 0
    for _ in range(k):
        total += len("-" * n)
    return total


@procedure
def power(n: Int) -> Int:
    return len(str(1 << n))


@endpoint("GET", "/pad", auth=Customer)
def pad(user: CurrentUser, s: Str(max_len=20), n: Int(ge=0)) -> One:
    shown = derive(user, padded=padded(s, n))
    out = select(shown, cols=["id", "padded"])
    return out


@endpoint("GET", "/grow", auth=Customer)
def grow(user: CurrentUser, n: Int(ge=0), k: Int(ge=0, le=100)) -> One:
    shown = derive(user, total=grown(n, k), digits=power(k))
    out = select(shown, cols=["id", "total", "digits"])
    return out


@endpoint("GET", "/shift", auth=Customer)
def shift(user: CurrentUser, n: Int(ge=0)) -> One:
    shown = derive(user, digits=power(n))
    out = select(shown, cols=["id", "digits"])
    return out


@endpoint("GET", "/offers", auth=Customer)
def my_offers(user: CurrentUser) -> List:
    offers = fetch(Offers)
    mine = where(offers, col("tier") == user.plan)
    ordered = sort(mine, by=["id"])
    shown = select(ordered, cols=["id", "title"])
    few = limit(shown, n=10)
    return few


@endpoint("GET", "/me/plan", auth=Customer)
def my_plan(user: CurrentUser) -> One:
    shown = derive(user, p=user.plan, q=plan_of(user))
    out = select(shown, cols=["id", "role", "plan", "p", "q"])
    return out


@endpoint("GET", "/admin/ping", auth=Admin)
def admin_ping(user: CurrentUser) -> One:
    out = select(user, cols=["id", "role", "plan"])
    return out


@endpoint("POST", "/notes", auth=Customer)
def add_notes(user: CurrentUser, title: Str(max_len=20), n: Int(ge=0, le=3)) -> List:
    rows = call(drafts, title=title, n=n)
    written = insert_many(Notes, rows=rows, owner=user)
    shown = select(written, cols=["id", "owner_id", "title"])
    return shown
"""
