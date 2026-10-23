"""Layer R on an app whose bearer tokens belong to one process (node F1, found on the held-out
.NET service H1: its token signer takes a random key at start-up, and the probes start a new app
process per request): ``run_probes(relogin=True)`` logs the caller in again inside each probe's own
session when a token minted in one session is refused in the next, and takes its database
baseline after that login. Tokens that survive a restart are left alone.

The app is a toy (a session object over a small SQLite seed), as in tests/spec/test_probes_soundness."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from lattice.profile import AdminFlag, ApiProfile, Profile, TokenColumn
from lattice.spec import Contract, Spec
from lattice.spec.probes import Answer, run_probes
from lattice.typesys import Scalar, Schema

USERS = {  # handle -> (id, email, password, admin)
    "admin": (1, "admin@example.com", "pw-admin", True),
    "alice": (2, "alice@example.com", "pw-alice", False),
    "bob": (3, "bob@example.com", "pw-bob", False),
}


def _spec() -> Spec:
    users = Schema.from_pairs(
        [("id", Scalar.INT), ("email", Scalar.STR), ("password", Scalar.STR),
         ("token", Scalar.STR), ("is_admin", Scalar.BOOL)],
        keys=[("id",)],
        private=["password"],
    )  # fmt: skip
    notes = Schema.from_pairs(
        [("id", Scalar.INT), ("owner_id", Scalar.INT), ("title", Scalar.STR)],
        keys=[("id",)],
        owner="owner_id",
    )
    api = ApiProfile(
        tables={"User": users, "Note": notes},
        table_names={"User": "users", "Note": "notes"},
        roles=frozenset({"Public", "Customer", "Admin"}),
        users_table="User",
        auth=TokenColumn("token"),
        roles_from=AdminFlag("is_admin", "Customer"),
        error_body="json_key(error)",
    )
    contract = Contract("GET", "/notes", auth="Authenticated", table="Note", kind="list")
    return Spec(
        Profile(schemas={}, api=api),
        users={h: (email, pw) for h, (_, email, pw, _) in USERS.items()},
        contracts=(contract,),
    )


def make_db(variant: str, path: Path) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password TEXT, "
                "token TEXT, is_admin BOOLEAN)")  # fmt: skip
    con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, owner_id INTEGER, title TEXT)")
    for h, (i, email, pw, admin) in USERS.items():
        con.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?)", (i, email, pw, None, admin))
    for i, owner in [(10, 1), (11, 2), (12, 3)]:
        con.execute("INSERT INTO notes VALUES (?, ?, ?)", (i, owner, f"note {i}"))
    con.commit()
    con.close()
    return path


class Toy:
    """``GET /notes`` for a bearer token ``<key>.<handle>`` whose key is this session's own
    (``per_process``) or one fixed for ever; the login writes a row (``audit``) when asked."""

    def __init__(self, db: Path, key: str, logins: list[str], audit: bool) -> None:
        self.db, self.key, self.logins, self.audit = db, key, logins, audit
        self.client = self

    def login(self, handle: str) -> str:
        self.logins.append(handle)
        if self.audit:
            con = sqlite3.connect(self.db)
            con.execute("UPDATE users SET token=? WHERE email=?",
                        (f"seen-{len(self.logins)}", USERS[handle][1]))  # fmt: skip
            con.commit()
            con.close()
        return f"{self.key}.{handle}"

    def request(self, method: str, path: str, *, params: Any = None, json: Any = None,
                form: Any = None, headers: Any = None) -> Answer:  # fmt: skip
        token = (headers or {}).get("Authorization", "").partition(" ")[2]
        key, _, handle = token.partition(".")
        if key != self.key or handle not in USERS:
            return Answer(401, {}, {"error": "a valid bearer token is required"})
        con = sqlite3.connect(self.db)
        rows = con.execute("SELECT id, owner_id, title FROM notes WHERE owner_id=?",
                           (USERS[handle][0],)).fetchall()  # fmt: skip
        con.close()
        return Answer(200, {}, [{"id": i, "owner_id": o, "title": t} for i, o, t in rows])


def run(*, per_process: bool, relogin: bool, audit: bool = False) -> tuple[Any, list[str]]:
    logins: list[str] = []
    counter = iter(range(1000))

    @contextmanager
    def sessions(db: Path) -> Iterator[Toy]:
        yield Toy(db, f"k{next(counter)}" if per_process else "fixed", logins, audit)

    def login(client: Any, handle: str) -> str:
        return client.login(handle)

    extra = {"relogin": True} if relogin else {}
    result = run_probes(_spec(), sessions, make_db, login=login, **extra)
    return result, logins


def test_tokens_of_one_process_verify_nothing_without_relogin() -> None:
    result, _ = run(per_process=True, relogin=False)
    assert result.items["R1 GET /notes"].verdict != "verified"


def test_relogin_logs_in_inside_each_probe_session_and_the_rule_is_verified() -> None:
    result, logins = run(per_process=True, relogin=True)
    v = result.items["R1 GET /notes"]
    assert v.verdict == "verified", v.reason
    assert len(logins) > len(USERS)  # the seed session's logins, then one per caller probe


def test_tokens_that_survive_a_restart_are_not_logged_in_again() -> None:
    result, logins = run(per_process=False, relogin=True)
    assert result.items["R1 GET /notes"].verdict == "verified"
    assert sorted(logins) == sorted(USERS)  # the seed session only


def test_a_login_that_writes_a_row_is_not_a_write_of_the_probe() -> None:
    result, _ = run(per_process=True, relogin=True, audit=True)
    probes = [p for n, p in result.probes.items() if "| alice |" in n]
    assert probes and all(p["db_diff"] == {} for p in probes)
