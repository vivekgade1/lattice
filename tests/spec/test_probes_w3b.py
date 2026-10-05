"""Layer R's two owner scopes of Phase 2 W3b (plan 2026-10-05, "Measurement decisions
after the dev-app gate"):

* ``owner_or_public(<column>)`` -- the owner, or anyone when the row's Bool ``<column>``
  is true (JustNotes' ``isPublic``): another user's row is readable only when the column
  is true; writes stay owner-only;
* a link between users with a composite key (microblog's ``followers``): the profile's
  owner is the side that writes a link (``follower_id``), and a route reading the other
  side states ``owner(<column>)`` (``/me/followers``: ``followed_id``). Rows are found by
  the whole key; a caller creates and deletes links on its own side only.

The app is a toy (a session object over a small SQLite seed), so each rule and each
mutant that breaks it can be pinned without a web framework."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from lattice.profile import AdminFlag, ApiProfile, Profile, TokenColumn
from lattice.spec import Contract, ContractError, ContractParam, Spec, validate
from lattice.spec.model import scope_parts
from lattice.spec.probes import Answer, rule_items, run_probes
from lattice.typesys import Scalar, Schema

USERS = {  # handle -> (id, email, admin, active)
    "admin": (1, "admin@example.com", True, True),
    "alice": (2, "alice@example.com", False, True),
    "bob": (3, "bob@example.com", False, True),
    "carol": (4, "carol@example.com", False, False),
}
NOTES = [  # (id, owner, public)
    (10, 2, False), (11, 2, True), (12, 3, False), (13, 3, True), (14, 1, False),
]  # fmt: skip
# alice follows bob; bob follows admin; admin follows alice -- each caller has a link of
# its own and a link on the other side it does not mirror
FOLLOWS = [(2, 3), (3, 1), (1, 2)]


def _profile() -> Profile:
    users = Schema.from_pairs(
        [("id", Scalar.INT), ("email", Scalar.STR), ("token", Scalar.STR),
         ("is_admin", Scalar.BOOL), ("active", Scalar.BOOL)],
        keys=[("id",)],
    )  # fmt: skip
    notes = Schema.from_pairs(
        [("id", Scalar.INT), ("owner_id", Scalar.INT), ("title", Scalar.STR),
         ("is_public", Scalar.BOOL)],
        keys=[("id",)],
        owner="owner_id",
    )  # fmt: skip
    follows = Schema.from_pairs(
        [("follower_id", Scalar.INT), ("followed_id", Scalar.INT)],
        keys=[("follower_id", "followed_id")],
        owner="follower_id",
    )
    api = ApiProfile(
        tables={"User": users, "Note": notes, "Follow": follows},
        table_names={"User": "users", "Note": "notes", "Follow": "follows"},
        roles=frozenset({"Public", "Customer", "Admin"}),
        users_table="User",
        auth=TokenColumn("token"),
        roles_from=AdminFlag("is_admin", "Customer"),
        active_column="active",
        inactive_status=403,
        inactive_detail="Inactive user",
        error_body="fastapi",
    )
    return Profile(schemas={}, api=api)


def _spec(*contracts: Contract) -> Spec:
    users = {h: (email, f"pw-{h}") for h, (_, email, _, _) in USERS.items()}
    return Spec(_profile(), users=users, contracts=contracts)


def make_db(variant: str, path: Path) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, token TEXT, "
                "is_admin BOOLEAN, active BOOLEAN)")  # fmt: skip
    con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, owner_id INTEGER, title TEXT, "
                "is_public BOOLEAN)")  # fmt: skip
    con.execute("CREATE TABLE follows (follower_id INTEGER, followed_id INTEGER, "
                "PRIMARY KEY (follower_id, followed_id))")  # fmt: skip
    for h, (i, email, admin, active) in USERS.items():
        con.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?)", (i, email, f"tok-{h}", admin,
                                                                  active))  # fmt: skip
    for i, owner, public in NOTES:
        con.execute("INSERT INTO notes VALUES (?, ?, ?, ?)", (i, owner, f"note {i}", public))
    for a, b in FOLLOWS:
        con.execute("INSERT INTO follows VALUES (?, ?)", (a, b))
    con.commit()
    con.close()
    return path


def login(client: Any, handle: str) -> str:
    return f"tok-{handle}"


def mint(key: Any) -> str:
    return next(f"tok-{h}" for h, u in USERS.items() if u[0] == key)


class Toy:
    """The toy app. Mutant flags: ``leak_private`` (another user's private note is
    readable), ``hide_public`` (another user's public note is refused), ``list_all`` (the
    note list shows private notes of others), ``public_writable`` (anyone edits a public
    note), ``wrong_side`` (``/me/following`` answers the caller's followers),
    ``follow_reversed`` (a follow inserts the link the other way round),
    ``unfollow_either`` (an unfollow also deletes the link the other way round)."""

    def __init__(self, db: Path, **flags: bool) -> None:
        self.db = db
        self.flags = flags

    def _sql(self, query: str, args: tuple = ()) -> list[tuple]:
        con = sqlite3.connect(self.db)
        try:
            rows = con.execute(query, args).fetchall()
            con.commit()
            return rows
        finally:
            con.close()

    def request(self, method: str, path: str, *, params: Any = None, json: Any = None,
                form: Any = None, headers: Any = None) -> Answer:  # fmt: skip
        auth = (headers or {}).get("Authorization", "")
        found = self._sql("SELECT id, active FROM users WHERE token=?", (auth.partition(" ")[2],))
        if not found:
            return Answer(401, {}, {"detail": "Not authenticated"})
        me, active = found[0]
        if not active:
            return Answer(403, {}, {"detail": "Inactive user"})
        f = self.flags
        if m := re.fullmatch(r"/notes/(\d+)", path):
            note = self._sql("SELECT id, owner_id, title, is_public FROM notes WHERE id=?",
                             (int(m.group(1)),))  # fmt: skip
            if not note:
                return Answer(404, {}, {"detail": "Note not found"})
            nid, owner, title, public = note[0]
            if method == "GET":
                mine_or_public = owner == me or (public and not f.get("hide_public"))
                if not mine_or_public and not f.get("leak_private"):
                    return Answer(403, {}, {"detail": "Not authorized to access this note"})
                return Answer(200, {}, {"id": nid, "owner_id": owner, "title": title,
                                        "is_public": bool(public)})  # fmt: skip
            if owner != me and not (public and f.get("public_writable")):
                return Answer(403, {}, {"detail": "Not authorized to access this note"})
            self._sql("UPDATE notes SET title=? WHERE id=?", ((json or {}).get("title"), nid))
            return Answer(200, {}, {"id": nid})
        if path == "/notes" and method == "GET":
            where = "" if f.get("list_all") else " WHERE owner_id=? OR is_public"
            rows = self._sql("SELECT id, owner_id, title, is_public FROM notes" + where,
                             () if f.get("list_all") else (me,))  # fmt: skip
            return Answer(200, {}, [{"id": i, "owner_id": o, "title": t, "is_public": bool(p)}
                                    for i, o, t, p in rows])  # fmt: skip
        if path in ("/me/following", "/me/followers"):
            following = path == "/me/following" and not f.get("wrong_side")
            mine, other = ("follower_id", "followed_id") if following else (
                "followed_id", "follower_id")  # fmt: skip
            rows = self._sql(f"SELECT u.id, u.email FROM follows JOIN users u ON u.id = {other} "
                             f"WHERE {mine}=?", (me,))  # fmt: skip
            return Answer(200, {}, {"data": [{"id": i, "email": e} for i, e in rows]})
        if m := re.fullmatch(r"/me/following/(\d+)", path):
            them = int(m.group(1))
            if not self._sql("SELECT id FROM users WHERE id=?", (them,)):
                return Answer(404, {}, {"detail": "User not found"})
            linked = self._sql("SELECT 1 FROM follows WHERE follower_id=? AND followed_id=?",
                               (me, them))  # fmt: skip
            if method == "POST":
                if linked:
                    return Answer(409, {}, {"detail": "User already followed."})
                pair = (them, me) if f.get("follow_reversed") else (me, them)
                self._sql("INSERT OR IGNORE INTO follows VALUES (?, ?)", pair)
                return Answer(204, {}, None)
            if method == "DELETE":
                either = self._sql("SELECT 1 FROM follows WHERE follower_id=? AND followed_id=?",
                                   (them, me)) if f.get("unfollow_either") else []  # fmt: skip
                if not linked and not either:
                    return Answer(409, {}, {"detail": "User is not followed."})
                self._sql("DELETE FROM follows WHERE follower_id=? AND followed_id=?", (me, them))
                if either:  # the mutant: any link between the two users goes
                    self._sql("DELETE FROM follows WHERE follower_id=? AND followed_id=?",
                              (them, me))  # fmt: skip
                return Answer(204, {}, None)
        return Answer(404, {}, {"detail": "Not Found"})


def toy(**flags: bool):
    @contextmanager
    def sessions(db: Path) -> Iterator[Toy]:
        yield Toy(db, **flags)

    return sessions


def _run(spec: Spec, **flags: bool):
    return run_probes(spec, toy(**flags), make_db, login=login, mint_token=mint)


NOT_AUTHORIZED = "Not authorized to access this note"
NOTE_ID = ContractParam("id", "path", Scalar.INT, ref="Note")
USER_ID = ContractParam("id", "path", Scalar.INT, ref="User")
GET_NOTE = Contract("GET", "/notes/{id}", auth="Authenticated", table="Note", kind="read",
                    owner_scope="owner_or_public(is_public)", params=(NOTE_ID,),
                    errors=(ContractError(404, "Note not found", when="not_found"),
                            ContractError(403, NOT_AUTHORIZED, when="not_owned")))  # fmt: skip
PUT_NOTE = Contract("PUT", "/notes/{id}", auth="Authenticated", table="Note", kind="update",
                    owner_scope="owner_or_public(is_public)",
                    params=(NOTE_ID, ContractParam("title", "body", Scalar.STR)),
                    errors=(ContractError(404, "Note not found", when="not_found"),
                            ContractError(403, NOT_AUTHORIZED, when="not_owned")))  # fmt: skip
LIST_NOTES = Contract("GET", "/notes", auth="Authenticated", table="Note", kind="list",
                      owner_scope="owner_or_public(is_public)")  # fmt: skip
FOLLOWING = Contract("GET", "/me/following", auth="Authenticated", table="Follow", kind="list")
FOLLOWERS = Contract("GET", "/me/followers", auth="Authenticated", table="Follow", kind="list",
                     owner_scope="owner(followed_id)")  # fmt: skip
FOLLOW = Contract("POST", "/me/following/{id}", auth="Authenticated", table="Follow",
                  kind="create", params=(USER_ID,),
                  errors=(ContractError(404, when="not_found"),
                          ContractError(409, when="duplicate")))  # fmt: skip
UNFOLLOW = Contract("DELETE", "/me/following/{id}", auth="Authenticated", table="Follow",
                    kind="delete", params=(USER_ID,),
                    errors=(ContractError(404, when="not_found"), ContractError(409)))  # fmt: skip
NOTE_ROUTES = (GET_NOTE, PUT_NOTE, LIST_NOTES)
LINK_ROUTES = (FOLLOWING, FOLLOWERS, FOLLOW, UNFOLLOW)


def _verdicts(result, *prefixes: str) -> dict[str, str]:
    return {i: v.verdict for i, v in result.items.items() if i.startswith(prefixes)}


# ---- the scopes and their validation ------------------------------------------------------------


def test_scope_parts_reads_every_scope_spelling():
    assert scope_parts("owner") == ("owner", None)
    assert scope_parts("owner(followed_id)") == ("owner", "followed_id")
    assert scope_parts("owner_or_public(is_public)") == ("owner_or_public", "is_public")
    assert scope_parts("any") == ("any", None)
    assert scope_parts(None) == (None, None)
    assert scope_parts("owner(") == ("owner(", None)  # malformed: S017


def _codes(*contracts: Contract) -> list[str]:
    return [p.code for p in validate(_spec(*contracts))]


def test_the_new_scopes_validate():
    assert _codes(*NOTE_ROUTES, *LINK_ROUTES) == []


@pytest.mark.parametrize(
    ("scope", "table", "code"),
    [
        ("owner_or_public(title)", "Note", "S023"),  # not a Bool column
        ("owner_or_public(nope)", "Note", "S023"),  # no such column
        ("owner_or_public(is_admin)", "User", "S023"),  # nobody owns the users table
        ("owner(title)", "Note", "S023"),  # a column that holds no user key
        ("owner(nope)", "Follow", "S023"),
        ("owner(followed_id", "Follow", "S017"),  # malformed
        ("public(is_public)", "Note", "S017"),
    ],
)
def test_a_bad_scope_is_refused(scope, table, code):
    c = Contract("GET", "/x", auth="Authenticated", table=table, kind="list", owner_scope=scope)
    assert _codes(c) == [code]


def test_the_items_state_the_scopes():
    items = {i.id: i for i in rule_items(_spec(*NOTE_ROUTES, *LINK_ROUTES))}
    note = items["T1 Note"]
    assert note.predicted == {"ownership": "owner", "column": "owner_id", "public": "is_public"}
    assert "is_public" in note.sentence
    follow = items["T1 Follow"]
    assert follow.predicted == {"ownership": "owner", "column": "follower_id",
                                "link": ["follower_id", "followed_id"]}  # fmt: skip
    assert items["R2 GET /me/followers"].predicted["scope"] == "owner(followed_id)"
    assert "followed_id" in items["R2 GET /me/followers"].sentence
    assert "is_public" in items["R2 GET /notes/{id}"].sentence


# ---- owner_or_public ----------------------------------------------------------------------------


def test_owner_or_public_is_verified_on_the_app_that_keeps_it():
    result = _run(_spec(*NOTE_ROUTES))
    got = _verdicts(result, "T1 Note", "R2 ", "R3 GET /notes/{id} 403")
    assert got == {
        "T1 Note": "verified",
        "R2 GET /notes/{id}": "verified",
        "R2 PUT /notes/{id}": "verified",
        "R2 GET /notes": "verified",
        f"R3 GET /notes/{{id}} 403 {NOT_AUTHORIZED}": "verified",
    }, {i: result.items[i].reason for i in got}
    names = [n for n in result.probes if n.startswith("GET /notes/{id} | alice")]
    # alice reads bob's private note (refused) and bob's public one (answered)
    assert any("public" in n for n in names) and any("private" in n for n in names)


@pytest.mark.parametrize(
    ("flag", "refuted"),
    [
        ("leak_private", {"T1 Note", "R2 GET /notes/{id}"}),
        ("hide_public", {"T1 Note", "R2 GET /notes/{id}"}),
        ("list_all", {"T1 Note", "R2 GET /notes"}),
        ("public_writable", {"T1 Note", "R2 PUT /notes/{id}"}),
    ],
)
def test_owner_or_public_mutants_are_refuted(flag, refuted):
    result = _run(_spec(*NOTE_ROUTES), **{flag: True})
    got = {i for i, v in _verdicts(result, "T1 Note", "R2 ").items() if v == "refuted"}
    assert got == refuted, {i: result.items[i].reason for i in got | refuted}


# ---- links between users ------------------------------------------------------------------------


def test_a_users_link_is_verified_on_the_app_that_keeps_it():
    result = _run(_spec(*LINK_ROUTES))
    got = _verdicts(result, "T1 Follow", "T5 Follow", "R2 ", "R3 POST")
    assert got == {
        "T1 Follow": "verified",
        "T5 Follow (follower_id, followed_id)": "verified",
        "R2 GET /me/following": "verified",
        "R2 GET /me/followers": "verified",
        "R2 POST /me/following/{id}": "verified",
        "R2 DELETE /me/following/{id}": "verified",
        "R3 POST /me/following/{id} 404": "verified",
        "R3 POST /me/following/{id} 409": "verified",
        "R3 POST /me/following/{id} 5xx": "verified",
    }, {i: result.items[i].reason for i in got}
    unfollow = [n for n in result.probes if n.startswith("DELETE /me/following/{id} | alice")]
    # alice unfollows bob (her own link) and admin (whose link to her is not hers)
    assert any("own side" in n for n in unfollow) and any("other side" in n for n in unfollow)


@pytest.mark.parametrize(
    ("flag", "refuted"),
    [
        ("wrong_side", {"T1 Follow", "R2 GET /me/following"}),
        ("follow_reversed", {"T1 Follow", "R2 POST /me/following/{id}"}),
        ("unfollow_either", {"T1 Follow", "R2 DELETE /me/following/{id}"}),
    ],
)
def test_users_link_mutants_are_refuted(flag, refuted):
    result = _run(_spec(*LINK_ROUTES), **{flag: True})
    got = {i for i, v in _verdicts(result, "T1 Follow", "R2 ").items() if v == "refuted"}
    assert got == refuted, {i: result.items[i].reason for i in got | refuted}


def test_the_other_side_read_on_the_owner_side_is_refuted():
    """``/me/followers`` stated as the owner side (plain ``owner``): the answer holds
    users the caller does not follow."""
    plain = Contract("GET", "/me/followers", auth="Authenticated", table="Follow", kind="list")
    result = _run(_spec(plain))
    assert result.items["R2 GET /me/followers"].verdict == "refuted"


def test_the_decision_log_states_that_no_target_compiles_the_new_scopes():
    log = (Path(__file__).resolve().parents[2] / "docs" / "lattice-language-rules.md").read_text()
    row = next(line for line in log.splitlines() if "owner_or_public(<column>)" in line)
    assert row.startswith("| 2026-10-05 | Target gate (R4) |")
    assert "compiled by no target" in row and "owner(<column>)" in row
