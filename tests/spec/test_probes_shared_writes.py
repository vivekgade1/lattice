"""Layer R soundness (pivot plan 2026-10-05, item A.1): a "shared" T1 verdict must not
rest on reads alone.

A table the profile gives no owner is predicted ``shared``. Reads answering 200 show
that anyone may *read* its rows -- not that anyone may write them: the reserve app's
``Article`` was "verified shared" on two anonymous reads although only the author may
update or delete one. So, when the table has routes updating or deleting its rows:

* ``verified`` needs a decisive *write* probe: two different non-admin callers each
  update (or delete) the same row, the write admitted with its database effect -- each
  is the other's control (one row has at most one owner, so one of them is not it);
* a caller refused (403/404, nothing written) on a row another caller wrote by the very
  same request, the caller having got past the route's check by some request, refutes
  ``shared``: the row is its owner's;
* reads alone leave T1 unexercised, saying so;
* with no such route, reads verify it, and the reason says there is no write route.

:func:`check_verdicts` refuses a verified ``shared`` T1 without that evidence.

The app is a toy (a session object over a small SQLite seed), as in
test_probes_soundness2."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from lattice.profile import AdminFlag, ApiProfile, Profile, TokenColumn
from lattice.spec import Contract, ContractParam, Spec
from lattice.spec.probes import (
    Answer,
    ItemVerdict,
    RuleResult,
    VerdictInvariantError,
    check_verdicts,
    rule_items,
    run_probes,
)
from lattice.typesys import Scalar, Schema

USERS = {  # handle -> (id, email, password, admin)
    "admin": (1, "admin@example.com", "pw-admin", True),
    "alice": (2, "alice@example.com", "pw-alice", False),
    "bob": (3, "bob@example.com", "pw-bob", False),
}
ARTICLES = [(10, 2), (11, 3), (12, 1)]  # (id, author_id): an author the profile does not know
TAGS = [(30, "red"), (31, "blue")]


def _profile() -> Profile:
    users = Schema.from_pairs(
        [("id", Scalar.INT), ("email", Scalar.STR), ("password", Scalar.STR),
         ("token", Scalar.STR), ("is_admin", Scalar.BOOL)],
        keys=[("id",)],
        private=["password"],
    )  # fmt: skip
    articles = Schema.from_pairs(
        [("id", Scalar.INT), ("author_id", Scalar.INT), ("title", Scalar.STR)], keys=[("id",)]
    )
    tags = Schema.from_pairs([("id", Scalar.INT), ("name", Scalar.STR)], keys=[("id",)])
    api = ApiProfile(
        tables={"User": users, "Article": articles, "Tag": tags},
        table_names={"User": "users", "Article": "articles", "Tag": "tags"},
        roles=frozenset({"Public", "Customer", "Admin"}),
        users_table="User",
        auth=TokenColumn("token"),
        roles_from=AdminFlag("is_admin", "Customer"),
        error_body="json_key(message)",
    )
    return Profile(schemas={}, api=api)


def make_db(variant: str, path: Path) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password TEXT, "
                "token TEXT, is_admin BOOLEAN)")  # fmt: skip
    con.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, author_id INTEGER, title TEXT)")
    con.execute("CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT)")
    for h, (i, email, pw, admin) in USERS.items():
        con.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?)", (i, email, pw, f"tok-{h}", admin))
    for i, author in ARTICLES:
        con.execute("INSERT INTO articles VALUES (?, ?, ?)", (i, author, f"article {i}"))
    for i, name in TAGS:
        con.execute("INSERT INTO tags VALUES (?, ?)", (i, name))
    con.commit()
    con.close()
    return path


def login(client: Any, handle: str) -> str:
    return f"tok-{handle}"


class Toy:
    """``GET /articles/{id}`` and ``GET /tags/{id}``: anyone (no token needed);
    ``PUT /articles/{id}`` (a token needed) sets the title -- ``mode``:

    * ``"shared"``: any signed-in user's update goes through;
    * ``"author"``: only the author's (the realworld app's ``filter_by(slug=...,
      author_id=<caller's profile>)``): anyone else gets 404, nothing written;
    * ``"broken"``: every update answers 500, writing nothing."""

    def __init__(self, db: Path, mode: str) -> None:
        self.db = db
        self.mode = mode

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
        headers = dict(headers or {})
        parts = path.strip("/").split("/")
        key = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        if method == "GET" and parts[0] in ("articles", "tags") and key is not None:
            cols = "id, author_id, title" if parts[0] == "articles" else "id, name"
            found = self._sql(f"SELECT {cols} FROM {parts[0]} WHERE id=?", (key,))
            if not found:
                return Answer(404, {}, {"message": "Not found"})
            names = cols.split(", ")
            return Answer(200, {}, dict(zip(names, found[0], strict=True)))
        if method == "PUT" and parts[0] == "articles":
            token = headers.get("Authorization", "").partition(" ")[2]
            user = self._sql("SELECT id FROM users WHERE token=?", (token,))
            if not user:
                return Answer(401, {}, {"message": "Not authenticated"})
            me = user[0][0]
            if self.mode == "broken":
                return Answer(500, {}, "Internal Server Error")
            row = self._sql("SELECT author_id FROM articles WHERE id=?", (key,))
            if not row or (self.mode == "author" and row[0][0] != me):
                return Answer(404, {}, {"message": "Article not found"})
            title = (json or {}).get("title")
            self._sql("UPDATE articles SET title=? WHERE id=?", (title, key))
            return Answer(200, {}, {"id": key, "author_id": row[0][0], "title": title})
        return Answer(404, {}, {"message": "Not Found"})


def toy(mode: str):
    @contextmanager
    def sessions(db: Path) -> Iterator[Toy]:
        yield Toy(db, mode)

    return sessions


def _id(name: str) -> ContractParam:
    return ContractParam("id", "path", Scalar.INT, ref=name)


READ_ARTICLE = Contract("GET", "/articles/{id}", auth="Public", table="Article", kind="read",
                        params=(_id("Article"),))  # fmt: skip
EDIT_ARTICLE = Contract(
    "PUT", "/articles/{id}", auth="Authenticated", table="Article", kind="update",
    params=(_id("Article"), ContractParam("title", "body", Scalar.STR)),
)  # fmt: skip
READ_TAG = Contract("GET", "/tags/{id}", auth="Public", table="Tag", kind="read",
                    params=(_id("Tag"),))  # fmt: skip


def _spec(*contracts: Contract, only: tuple[str, ...] | None = None) -> Spec:
    users = {h: (email, pw) for h, (_, email, pw, _) in USERS.items()
             if only is None or h in only}  # fmt: skip
    return Spec(_profile(), users=users, contracts=contracts)


def _run(spec: Spec, mode: str) -> RuleResult:
    return run_probes(spec, toy(mode), make_db, login=login)


ALL = (READ_ARTICLE, EDIT_ARTICLE, READ_TAG)


def test_a_shared_tables_write_routes_are_part_of_its_prediction():
    items = {i.id: i for i in rule_items(_spec(*ALL))}
    assert items["T1 Article"].predicted == {"ownership": "shared",
                                             "writes": ["PUT /articles/{id}"]}  # fmt: skip
    assert items["T1 Tag"].predicted == {"ownership": "shared"}


def test_an_author_only_update_on_a_publicly_readable_table_is_not_verified_shared():
    """The reserve app's Article: anyone reads it, only the author updates it. Alice's
    update of bob's article answers 404 and writes nothing while bob's same request
    writes it: the row is bob's -- "shared" is refuted, never verified on the reads."""
    result = _run(_spec(*ALL), "author")
    v = result.items["T1 Article"]
    assert v.verdict == "refuted", v.reason
    assert v.observed and v.observed.get("ownership") == "owned", v.observed
    witness = result.probes[v.witness]
    assert witness["request"]["method"] == "PUT" and witness["status"] == 404
    assert result.tables["Article"]["verdict"] == "unverified"


def test_a_truly_shared_table_verifies_on_writes_by_two_callers():
    result = _run(_spec(*ALL), "shared")
    v = result.items["T1 Article"]
    assert v.verdict == "verified", v.reason
    writes = [n for n in v.decisive if result.probes[n]["request"]["method"] == "PUT"]
    assert writes == v.decisive and len(writes) >= 2, v.decisive
    users = {result.probes[n]["request"]["user"] for n in writes}
    assert {"alice", "bob"} <= users, users


def test_reads_alone_do_not_verify_shared_when_the_writes_decide_nothing():
    """Every update answers 500: the reads still answer 200, but no write was decisive,
    so T1 is unexercised, and the reason says reads alone do not verify it."""
    v = _run(_spec(*ALL), "broken").items["T1 Article"]
    assert v.verdict == "unexercised", v.reason
    assert "reads alone" in v.reason and "PUT /articles/{id}" in v.reason


def test_one_non_admin_caller_cannot_show_a_non_owner_write():
    """With alice the only non-admin caller, her update of an article could be her
    own: no second caller writes the same row, so nothing is decisive."""
    v = _run(_spec(*ALL, only=("admin", "alice")), "shared").items["T1 Article"]
    assert v.verdict == "unexercised", v.reason
    assert "reads alone" in v.reason


def test_without_write_routes_reads_verify_shared_and_the_reason_says_so():
    v = _run(_spec(*ALL), "author").items["T1 Tag"]
    assert v.verdict == "verified", v.reason
    assert "no route updates or deletes Tag rows" in v.reason


# ---- the self-check -----------------------------------------------------------------------------


def _items() -> dict[str, Any]:
    return {i.id: i for i in rule_items(_spec(*ALL))}


def _record(method: str, path: str, **extra: Any) -> dict[str, Any]:
    return {"route": f"{method} {path}", "items": [], "status": 200, "body": {},
            "request": {"method": method, "path": path, "user": "alice"}, **extra}  # fmt: skip


def test_the_self_check_refuses_a_shared_verdict_resting_on_reads_alone():
    items = _items()
    result = RuleResult()
    result.probes["read"] = _record("GET", "/articles/10")
    result.items["T1 Article"] = ItemVerdict(items["T1 Article"], "verified", "answered 200",
                                             ["read"], decisive=["read"])  # fmt: skip
    with pytest.raises(VerdictInvariantError, match="T1 Article.*write"):
        check_verdicts(result)
    result.probes["write"] = _record("PUT", "/articles/10", shared_write=True)
    result.items["T1 Article"].decisive.append("write")
    check_verdicts(result)  # a decisive write: accepted


def test_the_self_check_wants_a_read_only_shared_verdict_to_say_so():
    items = _items()
    result = RuleResult()
    result.probes["read"] = _record("GET", "/tags/30")
    result.items["T1 Tag"] = ItemVerdict(items["T1 Tag"], "verified", "answered 200", ["read"],
                                         decisive=["read"])  # fmt: skip
    with pytest.raises(VerdictInvariantError, match="T1 Tag.*no route updates or deletes"):
        check_verdicts(result)
    result.items["T1 Tag"].reason = "answered 200; no route updates or deletes Tag rows: reads only"
    check_verdicts(result)
