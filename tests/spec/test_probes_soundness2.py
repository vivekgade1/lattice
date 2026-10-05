"""Layer R soundness, second post-hoc round (2026-10-05): "verified" must be earned. A
probe whose answer has more than one explanation is no evidence.

* **An ambiguous 403.** A refusal on a row that is not the caller's own (another user's,
  or one whose owner the profile does not know) cannot tell a role refusal from an
  ownership refusal: it counts for neither verified nor refuted, on R1 and R2. A
  request on a missing row tells them apart (no row to own): a 403 there, while another
  caller gets past to the 404, is a role refusal. Every role a probed caller holds must
  be decided for R1 to be verified.
* **Reaching a row takes more than a status.** A 2xx whose JSON body is an error
  envelope (``{"code": 404, "msg": ...}``) reaches nothing, and a write must show its
  effect on the row.
* **Controls.** A boundary value (T3), a listed value (T4) or a second create (T5)
  refused while the same request with a plain valid value is refused too says nothing.
* **Vacuous evidence.** No unstated 5xx (R3) only from probes refused before the
  handler ran; a stored value the importer's own seed wrote (T4); a 404 or a 5xx to a
  request addressing a row (the router's, a crash) as an admission.
* **The self-check** refuses a ``verified`` item without a decisive supporting probe;
  each probe's bearing on each item is recorded.

The app is a toy (a session object over a small SQLite seed), as in
test_probes_soundness."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from lattice.profile import AdminFlag, ApiProfile, Profile, TokenColumn
from lattice.spec import Contract, ContractError, ContractParam, Spec
from lattice.spec.probes import (
    Answer,
    ItemVerdict,
    RuleResult,
    VerdictInvariantError,
    rule_items,
    run_probes,
    summarise,
)
from lattice.typesys import Scalar, Schema

FORBIDDEN = "You do not have permission to perform this action."
MANAGER = "Access denied. Manager role required."
USERS = {  # handle -> (id, email, password, admin, active)
    "admin": (1, "admin@example.com", "pw-admin", True, True),
    "alice": (2, "alice@example.com", "pw-alice", False, True),
    "bob": (3, "bob@example.com", "pw-bob", False, True),
    "carol": (4, "carol@example.com", "pw-carol", False, False),
}
MANAGERS = {1, 3}  # a distinction the profile does not model (H2's manager / employee)
NOTES = [(10, 1), (11, 2), (12, 2), (13, 3)]  # (id, owner)
PROJECTS = [(20, 1), (21, 2), (22, 3)]  # (id, user_id): an owner the profile does not know
TAGS = [(30, "red-one", "red"), (31, "blue-one", "blue")]


def _profile(*, forbidden: str | None = None, tag_kinds: tuple[str, ...] = ("red", "blue")
             ) -> Profile:  # fmt: skip
    users = Schema.from_pairs(
        [("id", Scalar.INT), ("email", Scalar.STR), ("password", Scalar.STR),
         ("token", Scalar.STR), ("is_admin", Scalar.BOOL), ("active", Scalar.BOOL),
         ("name", Scalar.STR)],
        keys=[("id",)],
        private=["password"],
    )  # fmt: skip
    notes = Schema.from_pairs(
        [("id", Scalar.INT), ("owner_id", Scalar.INT), ("title", Scalar.STR)],
        keys=[("id",)],
        owner="owner_id",
    )
    projects = Schema.from_pairs(
        [("id", Scalar.INT), ("user_id", Scalar.INT), ("name", Scalar.STR)], keys=[("id",)]
    )
    tags = Schema.from_pairs(
        [("id", Scalar.INT), ("name", Scalar.STR), ("kind", Scalar.STR)],
        keys=[("id",), ("name",)],
        one_of={"kind": tag_kinds},
    )
    api = ApiProfile(
        tables={"User": users, "Note": notes, "Project": projects, "Tag": tags},
        table_names={"User": "users", "Note": "notes", "Project": "projects", "Tag": "tags"},
        roles=frozenset({"Public", "Customer", "Admin"}),
        users_table="User",
        auth=TokenColumn("token"),
        roles_from=AdminFlag("is_admin", "Customer"),
        active_column="active",
        inactive_status=403,
        inactive_detail="Inactive user",
        error_body="json_key(message)",
        forbidden_detail=forbidden,
    )
    return Profile(schemas={}, api=api)


def _spec(*contracts: Contract, only: tuple[str, ...] | None = None, **kw: Any) -> Spec:
    users = {h: (email, pw) for h, (_, email, pw, _, _) in USERS.items()
             if only is None or h in only}  # fmt: skip
    return Spec(_profile(**kw), users=users, contracts=contracts)


def make_db(variant: str, path: Path, *, kinds: tuple[str, ...] = ()) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password TEXT, "
                "token TEXT, is_admin BOOLEAN, active BOOLEAN, name TEXT)")  # fmt: skip
    con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, owner_id INTEGER, title TEXT)")
    con.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT)")
    con.execute("CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT UNIQUE, kind TEXT)")
    for h, (i, email, pw, admin, active) in USERS.items():
        con.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (i, email, pw, f"tok-{h}", admin, active, f"name {h}"))  # fmt: skip
    for i, owner in NOTES:
        con.execute("INSERT INTO notes VALUES (?, ?, ?)", (i, owner, f"note {i}"))
    for i, owner in PROJECTS:
        con.execute("INSERT INTO projects VALUES (?, ?, ?)", (i, owner, f"project {i}"))
    for i, name, kind in TAGS:
        con.execute("INSERT INTO tags VALUES (?, ?, ?)", (i, name, kind))
    for k, kind in enumerate(kinds):  # extra seed rows (the importer's seed, not the app)
        con.execute("INSERT INTO tags VALUES (?, ?, ?)", (40 + k, f"seeded-{k}", kind))
    con.commit()
    con.close()
    return path


def login(client: Any, handle: str) -> str:
    return f"tok-{handle}"


def mint(key: Any) -> str:
    return next(f"tok-{h}" for h, u in USERS.items() if u[0] == key)


HTML_404 = "<!doctype html><title>Not Found</title><h1>Not Found</h1>"


class Toy:
    """The toy app (a token decorator answering ``{"error": ...}`` 401s):

    * ``PUT /projects/{id}`` and ``PUT /users/{id}``: manager-only (a role the profile
      does not model: alice is an employee), the role checked before the lookup --
      ``lookup_first``: after it; ``owner_check``: instead an owner-or-admin check on
      ``projects.user_id`` (an owner the profile does not know), answering ``FORBIDDEN``;
    * ``DELETE /env/{id}``: answers 200 ``{"code": 404, "msg": ...}`` (an error
      envelope), deleting nothing; ``DELETE /noop/{id}``: 204, deleting nothing;
    * ``GET /ghost/{id}``: 404 to every caller (the row is never reachable there);
    * ``GET /pinned``: alice's notes, to every caller;
    * ``GET /slugged/{id}``: the router's HTML 404 (a path value it does not match), to
      anyone; ``GET /crash/{id}``: a 500 past the token check;
    * ``POST /tags``: a shared table's create (``name`` at most 10 characters, unique;
      ``kind`` one of red, blue: 422 otherwise) -- ``tags_down``: 404 to every body;
      ``shout``: the kind stored upper-cased;
    * ``GET /locked``: 401 to every caller."""

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

    def _user(self, headers: dict[str, str]) -> tuple[int, bool, bool] | None:
        token = headers.get("Authorization", "").partition(" ")[2]
        found = self._sql("SELECT id, is_admin, active FROM users WHERE token=?", (token,))
        return found[0] if found else None

    def request(self, method: str, path: str, *, params: Any = None, json: Any = None,
                form: Any = None, headers: Any = None) -> Answer:  # fmt: skip
        headers = dict(headers or {})
        body = json or {}
        if path.startswith("/slugged/"):
            return Answer(404, {}, HTML_404)
        if "Authorization" not in headers:
            return Answer(401, {}, {"error": "Authorization header is required"})
        user = self._user(headers)
        if user is None:
            return Answer(401, {}, {"error": "Invalid or expired token"})
        me, admin, active = user
        if not active:
            return Answer(403, {}, {"message": "Inactive user"})
        parts = path.strip("/").split("/")
        key = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        if path == "/locked":
            return Answer(401, {}, {"error": "Locked"})
        if parts[0] in ("projects", "users") and method == "PUT":
            table = parts[0]
            owner_col = "user_id" if table == "projects" else "id"
            found = self._sql(f"SELECT id, {owner_col} FROM {table} WHERE id=?", (key,))
            if self.flags.get("owner_check"):
                if not found:
                    return Answer(404, {}, {"message": "Not found"})
                if found[0][1] != me and not admin:
                    return Answer(403, {}, {"message": FORBIDDEN})
            else:
                if self.flags.get("lookup_first") and not found:
                    return Answer(404, {}, {"message": "Not found"})
                if me not in MANAGERS:
                    return Answer(403, {}, {"error": MANAGER})
                if not found:
                    return Answer(404, {}, {"message": "Not found"})
            col = "name"
            self._sql(f"UPDATE {table} SET {col}=? WHERE id=?", (body.get("name"), key))
            return Answer(200, {}, {"id": key, "name": body.get("name")})
        if parts[0] == "env":
            return Answer(200, {}, {"code": 404, "msg": "Note not found."})
        if parts[0] == "noop":
            return Answer(204, {}, None)
        if parts[0] == "ghost":
            return Answer(404, {}, {"message": "Not found"})
        if path == "/pinned":
            rows = self._sql("SELECT id, owner_id, title FROM notes WHERE owner_id=2")
            return Answer(200, {}, [{"id": i, "owner_id": o, "title": t} for i, o, t in rows])
        if parts[0] == "crash":
            return Answer(500, {}, "Internal Server Error")
        if path == "/tags" and method == "POST":
            if self.flags.get("tags_down"):
                return Answer(404, {}, {"message": "Not found"})
            name, kind = body.get("name"), body.get("kind")
            if not isinstance(name, str) or len(name) > 10:
                return Answer(422, {}, {"message": "name: at most 10 characters"})
            if kind not in ("red", "blue"):
                return Answer(422, {}, {"message": "kind: not a choice"})
            if self._sql("SELECT id FROM tags WHERE name=?", (name,)):
                return Answer(409, {}, {"message": "Tag exists"})
            stored = kind.upper() if self.flags.get("shout") else kind
            self._sql("INSERT INTO tags (name, kind) VALUES (?, ?)", (name, stored))
            return Answer(201, {}, {"name": name, "kind": kind})
        return Answer(404, {}, {"message": "Not Found"})


def toy(**flags: bool):
    @contextmanager
    def sessions(db: Path) -> Iterator[Toy]:
        yield Toy(db, **flags)

    return sessions


def _run(spec: Spec, *, flags: dict[str, bool] | None = None, kinds: tuple[str, ...] = (),
         **kw: Any) -> RuleResult:  # fmt: skip
    kw.setdefault("login", login)
    kw.setdefault("mint_token", mint)

    def seed(variant: str, path: Path) -> Path:
        return make_db(variant, path, kinds=kinds)

    return run_probes(spec, toy(**(flags or {})), seed, **kw)


def _id(name: str) -> ContractParam:
    return ContractParam("id", "path", Scalar.INT, ref=name)


EDIT_PROJECT = Contract(
    "PUT", "/projects/{id}", auth="Authenticated", table="Project", kind="update",
    params=(_id("Project"), ContractParam("name", "body", Scalar.STR)),
)  # fmt: skip
EDIT_USER = Contract(
    "PUT", "/users/{id}", auth="Authenticated", table="User", kind="update",
    params=(_id("User"), ContractParam("name", "body", Scalar.STR)),
)  # fmt: skip


# ---- 1. an ambiguous 403 is no evidence -------------------------------------------------------


def test_a_manager_only_route_on_an_unknown_owners_table_is_not_verified_authenticated():
    """H2's ``PUT /projects/{id}``: alice (an employee) gets 403 on projects that are
    not hers (their owner unknown), bob (a manager) gets through. The 403s alone cannot
    tell a role refusal from an ownership refusal; on a missing project alice still
    gets 403 while bob gets past to the 404: a role refusal -- callers of one role
    answered differently, so R1 is refuted, never verified as Authenticated."""
    result = _run(_spec(EDIT_PROJECT))
    v = result.items["R1 PUT /projects/{id}"]
    assert v.verdict == "refuted", v.reason
    assert "differently" in v.reason
    missing = [n for n in v.probes if "missing" in n]
    assert missing, v.probes


def test_without_a_missing_row_to_tell_them_apart_the_ambiguous_403_leaves_r1_open():
    """The lookup before the role check: every caller gets 404 on a missing project, so
    nothing tells alice's 403s apart -- R1 is not verified (unclear: bob is admitted, and
    alice's 403s may be the role's refusal)."""
    v = _run(_spec(EDIT_PROJECT), flags={"lookup_first": True}).items["R1 PUT /projects/{id}"]
    assert v.verdict == "unclear", v.reason
    assert "ownership" in v.reason


def test_a_manager_only_users_route_is_refuted_on_the_callers_own_row():
    """H2's ``PUT /users/{id}``: a users row is its user's own; alice is refused on hers,
    which no ownership rule explains."""
    result = _run(_spec(EDIT_USER))
    v = result.items["R1 PUT /users/{id}"]
    assert v.verdict == "refuted", v.reason
    paths = {result.probes[n]["request"]["path"] for n in v.probes if "| alice |" in n}
    assert "/users/2" in paths, paths


def test_an_owner_check_with_the_forbidden_detail_does_not_verify_admin_only():
    """conduit's ``IsAdminOrOwner`` on a table whose owner the profile does not know:
    alice's ``FORBIDDEN`` 403s on other users' projects are not role refusals for sure
    (an owner check answers the same detail), so "admits exactly: Admin" is not
    verified on them."""
    spec = _spec(replace(EDIT_PROJECT, auth="Admin"), forbidden=FORBIDDEN)
    v = _run(spec, flags={"owner_check": True}).items["R1 PUT /projects/{id}"]
    assert v.verdict != "verified", v.reason


def test_an_ambiguous_403_bears_on_no_r2_item():
    """R2 ``owner``: a 403 on another user's note, from a caller the route refuses by a
    role the profile does not know, is no ownership evidence."""
    from lattice.spec.probes import _refused

    c = Contract("PUT", "/notes/{id}", auth="Authenticated", table="Note", kind="update")
    api = _profile().api
    assert _refused(Answer(403, {}, {"error": MANAGER}), c, api, False) is None
    assert _refused(Answer(403, {}, {"error": MANAGER}), c, api, True) is True
    api_drf = _profile(forbidden=FORBIDDEN).api
    assert _refused(Answer(403, {}, {"message": FORBIDDEN}), c, api_drf, False) is None


# ---- 2. reaching a row: an error envelope, a write without its effect ----------------------------


def _owner_or_admin(path: str) -> Contract:
    return Contract("DELETE", path, auth="Authenticated", table="Note", kind="delete",
                    owner_scope="owner_or_admin", params=(_id("Note"),))  # fmt: skip


@pytest.mark.parametrize("path", ["/env/{id}", "/noop/{id}"], ids=["envelope", "no-effect"])
def test_an_envelope_or_a_write_without_its_effect_reaches_no_row(path):
    """conduit's comment DELETE answered 200 ``{"code": 404, ...}`` and deleted nothing:
    neither R1 (admitted) nor R2 (the admin reaches another user's row) is verified."""
    result = _run(_spec(_owner_or_admin(path)))
    for kind in ("R1", "R2"):
        v = result.items[f"{kind} DELETE {path}"]
        assert v.verdict != "verified", (kind, v.reason)


def test_an_error_envelope_is_read_from_the_body():
    from lattice.spec.probes import _envelope

    api = _profile().api
    assert _envelope(Answer(200, {}, {"code": 404, "msg": "Comment not found."}), api) == 404
    assert _envelope(Answer(200, {}, {"status": "500", "error": "boom"}), api) == 500
    assert _envelope(Answer(200, {}, {"success": False}), api) is not None
    assert _envelope(Answer(200, {}, {"code": 200, "data": {"id": 1}}), api) is None
    assert _envelope(Answer(200, {}, {"message": "Project deleted successfully"}), api) is None
    # the profile's error body, holding an answer the profile states as an error
    assert _envelope(Answer(200, {}, {"message": "Inactive user"}), api) is not None
    assert _envelope(Answer(404, {}, {"code": 404}), api) is None  # not a 2xx


# ---- 3. 404 and 5xx answers to a request addressing a row admit no one -----------------------


def test_the_routers_404_admits_no_caller():
    """H1's ``{slug}`` routes: a path value the router does not match answers its HTML
    404 to anyone -- not "anonymous admitted" (refuted, then "corrected" to Public)."""
    c = Contract("GET", "/slugged/{id}", auth="Authenticated", table="Note", kind="read",
                 params=(_id("Note"),))  # fmt: skip
    v = _run(_spec(c)).items["R1 GET /slugged/{id}"]
    assert v.verdict == "unexercised", v.reason


def test_a_crash_admits_no_caller():
    c = Contract("GET", "/crash/{id}", auth="Admin", table="Note", kind="read",
                 params=(_id("Note"),))  # fmt: skip
    v = _run(_spec(c)).items["R1 GET /crash/{id}"]
    assert v.verdict != "refuted", v.reason
    assert v.verdict != "verified", v.reason


# ---- 4. R2 needs a witness ------------------------------------------------------------


def test_a_row_no_one_reaches_is_no_ownership_evidence():
    """``GET /ghost/{id}`` answers 404 to the row's owner too: another user's 404 there
    does not show an ownership rule."""
    c = Contract("GET", "/ghost/{id}", auth="Authenticated", table="Note", kind="read",
                 params=(_id("Note"),))  # fmt: skip
    result = _run(_spec(c))
    for item in ("R2 GET /ghost/{id}", "T1 Note"):
        assert result.items[item].verdict != "verified", (item, result.items[item].reason)


def test_a_list_every_caller_gets_alike_shows_no_owner_scope():
    """``GET /pinned`` answers alice's notes to every caller: with alice the only
    non-admin, her answer holds only her rows -- but the admin gets the same answer, so
    the request, not the caller, selects them."""
    c = Contract("GET", "/pinned", auth="Authenticated", table="Note", kind="list")
    result = _run(_spec(c, only=("admin", "alice")))
    v = result.items["R2 GET /pinned"]
    assert v.verdict != "verified", v.reason


# ---- 5. controls: T3, T4, T5 ----------------------------------------------------------

TAG_CREATE = Contract(
    "POST", "/tags", auth="Authenticated", table="Tag", kind="create",
    params=(ContractParam("name", "body", Scalar.STR, max_len=10, column="Tag.name"),
            ContractParam("kind", "body", Scalar.STR, one_of=("red", "blue"),
                          column="Tag.kind")),
)  # fmt: skip
TAG_ITEMS = ("T3 Tag.name max_len 10", "T4 Tag.kind", "T5 Tag (name)")


def test_bounds_choices_and_keys_verify_on_the_app_that_keeps_them():
    result = _run(_spec(TAG_CREATE))
    for item in TAG_ITEMS:
        assert result.items[item].verdict == "verified", (item, result.items[item].reason)


def test_a_route_refusing_every_body_decides_no_bound_choice_or_key():
    """``tags_down``: every body gets 404 -- the boundary value's refusal, a listed
    value's non-422 and the second create's refusal all have another explanation (the
    plain valid request is refused too)."""
    result = _run(_spec(TAG_CREATE), flags={"tags_down": True})
    for item in TAG_ITEMS:
        v = result.items[item]
        assert v.verdict not in ("verified", "refuted"), (item, v.verdict, v.reason)


def test_a_value_the_importers_seed_wrote_refutes_no_choice():
    """T4 refuted on the seed alone blamed the app for the importer's own rows (H2)."""
    result = _run(_spec(TAG_CREATE), kinds=("green",))
    v = result.items["T4 Tag.kind"]
    assert v.verdict != "refuted", v.reason


def test_a_value_a_probe_stored_still_refutes_the_choice():
    """``shout``: the app stores the kind upper-cased -- a value outside the list that
    a probe's create wrote, not the seed."""
    v = _run(_spec(TAG_CREATE), flags={"shout": True}).items["T4 Tag.kind"]
    assert v.verdict == "refuted", v.reason
    assert "RED" in v.reason


# ---- 6. R3 5xx needs a probe past the auth check --------------------------------------


def test_no_unstated_5xx_is_not_verified_by_refused_probes_alone():
    c = Contract("GET", "/locked", auth="Authenticated", errors=(ContractError(401),))
    v = _run(_spec(c)).items["R3 GET /locked 5xx"]
    assert v.verdict == "unexercised", v.reason


# ---- 7. the self-check and the recorded bearing -------------------------------------------------


def _item(item_id: str):
    return next(i for i in rule_items(_spec(EDIT_PROJECT)) if i.id == item_id)


def test_summarise_refuses_a_verified_item_without_a_decisive_probe():
    result = RuleResult()
    item = _item("R1 PUT /projects/{id}")
    result.items[item.id] = ItemVerdict(item, "verified", "forced", ["p"])
    with pytest.raises(VerdictInvariantError, match="no decisive"):
        summarise(_spec(EDIT_PROJECT), result)


def test_every_verified_item_names_its_decisive_probes_and_each_probe_its_bearing():
    result = _run(_spec(TAG_CREATE, EDIT_USER))
    verified = [v for v in result.items.values() if v.verdict == "verified"]
    assert verified
    for v in verified:
        assert v.decisive, v.item.id
        for name in v.decisive:
            if name.startswith("(login)"):  # T1 users: every role logged in (no request)
                continue
            assert result.probes[name]["bearing"][v.item.id]["decisive"] is True
    ambiguous = [b for rec in result.probes.values() for b in rec["bearing"].values()
                 if not b["decisive"]]  # fmt: skip
    assert ambiguous and all(b["why"] for b in ambiguous)
