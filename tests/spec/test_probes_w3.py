"""Layer R fixes of Phase 2 W3 (plan 2026-10-05): what counts as a refusal (the profile's
auth answers, a front end's recorded ones, the inactive answer), Public routes and bad
credentials, ``owner_scope="any"`` lists, a Basic-credential login route, answers that
are not JSON, key spellings, and the ``verified (corrected)`` verdict.

The app here is a toy: a session object answering requests from a small SQLite seed, so
each rule can be pinned without a web framework."""

from __future__ import annotations

import base64
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from lattice.profile import AdminFlag, ApiProfile, Profile, TokenColumn
from lattice.spec import Contract, ContractError, ContractParam, Spec
from lattice.spec.probes import (
    VERIFIED_CORRECTED,
    Answer,
    _refused,
    is_verified,
    run_probes,
)
from lattice.typesys import Scalar, Schema

FORBIDDEN = "You do not have permission to perform this action."
NOT_PROVIDED = "Authentication credentials were not provided."
USERS = {  # handle -> (id, email, password, admin, active)
    "admin": (1, "admin@example.com", "pw-admin", True, True),
    "alice": (2, "alice@example.com", "pw-alice", False, True),
    "bob": (3, "bob@example.com", "pw-bob", False, True),
    "carol": (4, "carol@example.com", "pw-carol", False, False),
}
NOTE_IDS = {  # note key (UUID, stored as 32 hex digits) -> owner id
    uuid.UUID(int=10 + i): owner for i, owner in enumerate((1, 2, 2, 3, 3))
}


def _profile(**api: Any) -> Profile:
    users = Schema.from_pairs(
        [("id", Scalar.INT), ("email", Scalar.STR), ("password", Scalar.STR),
         ("token", Scalar.STR), ("is_admin", Scalar.BOOL), ("active", Scalar.BOOL)],
        keys=[("id",)],
        private=["password"],
    )  # fmt: skip
    notes = Schema.from_pairs(
        [("id", Scalar.UUID), ("owner_id", Scalar.INT), ("title", Scalar.STR)],
        keys=[("id",)],
        owner="owner_id",
    )
    settings: dict[str, Any] = dict(
        tables={"User": users, "Note": notes},
        table_names={"User": "users", "Note": "notes"},
        roles=frozenset({"Public", "Customer", "Admin"}),
        users_table="User",
        auth=TokenColumn("token"),
        roles_from=AdminFlag("is_admin", "Customer"),
        active_column="active",
        inactive_status=403,
        inactive_detail="Inactive user",
        error_body="fastapi",
        forbidden_detail=FORBIDDEN,
        uuid_storage="hex32",
    )
    settings.update(api)
    return Profile(schemas={}, api=ApiProfile(**settings))


def _spec(*contracts: Contract, **api: Any) -> Spec:
    users = {h: (email, pw) for h, (_, email, pw, _, _) in USERS.items()}
    return Spec(_profile(**api), users=users, contracts=contracts)


def make_db(variant: str, path: Path) -> Path:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password TEXT, token TEXT, "
        "is_admin BOOLEAN, active BOOLEAN)"
    )
    con.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, owner_id INTEGER, title TEXT)")
    for h, (i, email, pw, admin, active) in USERS.items():
        con.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                    (i, email, pw, f"tok-{h}", admin, active))  # fmt: skip
    for key, owner in NOTE_IDS.items():
        con.execute("INSERT INTO notes VALUES (?, ?, ?)", (key.hex, owner, f"note {key.int}"))
    con.commit()
    con.close()
    return path


def login(client: Any, handle: str) -> str:
    return f"tok-{handle}"


def mint(key: Any) -> str:
    return next(f"tok-{h}" for h, u in USERS.items() if u[0] == key)


class Toy:
    """The toy app. ``public_refuses_bad_tokens``: a bad credential is refused on every
    route (as DRF does); ``html_404``: a missing note is an HTML page."""

    def __init__(self, db: Path, **flags: bool) -> None:
        self.db = db
        self.flags = flags
        self.client = None
        self.cookies: dict[str, str] = {}

    def set_cookie(self, name: str, value: str) -> None:
        self.cookies[name] = value

    def _caller(self, headers: dict[str, str]) -> tuple[str, Any]:
        auth = headers.get("Authorization")
        if auth is None and "sid" in self.cookies:
            auth = f"Session {self.cookies['sid']}"
        if auth is None:
            return "none", None
        scheme, _, value = auth.partition(" ")
        con = sqlite3.connect(self.db)
        try:
            if scheme == "Basic":
                try:
                    email, _, pw = base64.b64decode(value).decode().partition(":")
                except ValueError:
                    return "bad", None
                row = con.execute(
                    "SELECT id, is_admin, active FROM users WHERE email=? AND password=?",
                    (email, pw),
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT id, is_admin, active FROM users WHERE token=?", (value,)
                ).fetchone()
        finally:
            con.close()
        if row is None:
            return "bad", None
        return ("inactive" if not row[2] else "ok"), row

    def request(self, method: str, path: str, *, params: Any = None, json: Any = None,
                form: Any = None, headers: Any = None) -> Answer:  # fmt: skip
        headers = dict(headers or {})
        state, row = self._caller(headers)
        public = path in ("/public",) or path.startswith("/html/")
        if state == "bad" and (not public or self.flags.get("public_refuses_bad_tokens")):
            return Answer(403, {}, {"detail": "Invalid token."})
        if path == "/public":
            return Answer(200, {}, {"ok": True})
        if path.startswith("/html/"):
            key = path.rsplit("/", 1)[1]
            if self._note(key) is None:
                return Answer(404, {}, "<html><h1>Not Found</h1></html>")
            return Answer(200, {}, "<html>a note</html>")
        if path == "/token" and method == "POST":
            if state != "ok" or not headers.get("Authorization", "").startswith("Basic "):
                return Answer(401, {}, {"detail": "Invalid credentials"})
            return Answer(200, {}, {"token": "t"})
        if state == "none":
            return Answer(403, {}, {"detail": NOT_PROVIDED})
        if state == "inactive":
            return Answer(403, {}, {"detail": "Inactive user"})
        if path == "/notes" and method == "GET":
            con = sqlite3.connect(self.db)
            rows = con.execute(
                "SELECT id, owner_id, title FROM notes" + ("" if self.flags.get("leak") else
                                                           " WHERE owner_id=?"),
                () if self.flags.get("leak") else (row[0],),
            ).fetchall()  # fmt: skip
            con.close()
            return Answer(200, {}, [{"id": str(uuid.UUID(k)), "owner_id": o, "title": t}
                                    for k, o, t in rows])  # fmt: skip
        if path == "/admin-only":
            if not row[1]:
                return Answer(403, {}, {"detail": FORBIDDEN})
            return Answer(200, {}, {"ok": True})
        return Answer(404, {}, {"detail": "Not Found"})

    def _note(self, key: str) -> Any:
        con = sqlite3.connect(self.db)
        try:
            return con.execute(
                "SELECT owner_id FROM notes WHERE id=?", (uuid.UUID(key).hex,)
            ).fetchone()
        except ValueError:
            return None
        finally:
            con.close()


def toy(**flags: bool):
    @contextmanager
    def sessions(db: Path) -> Iterator[Toy]:
        yield Toy(db, **flags)

    return sessions


def _run(spec: Spec, flags: dict[str, bool] | None = None, **kw: Any):
    return run_probes(spec, toy(**(flags or {})), make_db, login=login, mint_token=mint, **kw)


# ---- what is a refusal ------------------------------------------------------------------------

API = _profile().api
ADMIN_ONLY = Contract("GET", "/admin-only", auth="Admin", errors=(ContractError(403),))


def test_the_forbidden_detail_is_a_refusal_even_when_the_route_states_a_403():
    """A 403 carrying the profile's forbidden detail is the role check's, though the route
    also states a 403 of unknown detail (multi-vendor's ``POST /products``)."""
    answer = Answer(403, {}, {"detail": FORBIDDEN})
    assert _refused(answer, ADMIN_ONLY, API, True) is True
    other = Answer(403, {}, {"detail": "You do not own this product"})
    assert _refused(other, ADMIN_ONLY, API, True) is False


def test_a_front_ends_recorded_auth_answer_is_a_refusal():
    """DRF answers an anonymous caller 403 "credentials were not provided" (the
    AuthModel's ``unauthenticated_answer``)."""
    answer = Answer(403, {}, {"detail": NOT_PROVIDED})
    plain = _profile(forbidden_detail=None).api
    contract = Contract("GET", "/notes", auth="Authenticated")
    assert _refused(answer, contract, plain, False, refusals=((403, NOT_PROVIDED),)) is True
    assert _refused(answer, contract, plain, False) is None


def test_the_inactive_answer_is_a_refusal_whatever_its_status():
    api = _profile(inactive_status=400).api
    contract = Contract("GET", "/notes", auth="Authenticated")
    assert _refused(Answer(400, {}, {"detail": "Inactive user"}), contract, api, True) is True
    assert _refused(Answer(400, {}, {"detail": "bad page"}), contract, api, True) is False


def test_the_detail_is_read_under_the_profiles_error_key():
    api = _profile(error_body="json_key(message)", forbidden_detail="Insufficient permissions").api
    answer = Answer(403, {}, {"error": "forbidden", "message": "Insufficient permissions"})
    assert _refused(answer, ADMIN_ONLY, api, True) is True


def test_r1_with_recorded_refusals_on_the_toy():
    spec = _spec(Contract("GET", "/admin-only", auth="Admin", errors=(ContractError(403),)))
    result = _run(spec, refusals=((403, NOT_PROVIDED), (403, "Invalid token.")))
    assert result.items["R1 GET /admin-only"].verdict == "verified", result.items[
        "R1 GET /admin-only"
    ].reason


# ---- Public routes and bad credentials ----------------------------------------------------------


def test_a_public_route_refusing_bad_credentials_is_still_public():
    """An app that checks credentials on every request (DRF) refuses a malformed token
    and an inactive user even on a Public route: that is the auth layer, not the route's
    rule, so those callers decide nothing; anonymous and every role still do."""
    spec = _spec(Contract("GET", "/public", auth="Public"))
    flags = {"public_refuses_bad_tokens": True}
    result = _run(spec, flags, refusals=((403, "Invalid token."),))
    item = result.items["R1 GET /public"]
    assert item.verdict == "verified", item.reason
    assert _run(spec).items["R1 GET /public"].verdict == "verified"


def test_a_public_route_refusing_anonymous_is_refuted():
    spec = _spec(Contract("GET", "/notes", auth="Public"))
    result = _run(spec, refusals=((403, NOT_PROVIDED),))
    assert result.items["R1 GET /notes"].verdict == "refuted"


# ---- owner scope "any" lists, key spellings -----------------------------------------------------


def test_an_owner_list_compares_keys_whatever_their_spelling():
    """The seed stores UUID keys as 32 hex digits, the answer spells them with dashes."""
    spec = _spec(Contract("GET", "/notes", auth="Authenticated", table="Note", kind="list"))
    result = _run(spec, refusals=((403, NOT_PROVIDED),))
    assert result.items["R2 GET /notes"].verdict == "verified", result.items["R2 GET /notes"]
    leaky = _run(spec, {"leak": True}, refusals=((403, NOT_PROVIDED),))
    assert leaky.items["R2 GET /notes"].verdict == "refuted"


def test_an_any_scope_list_is_not_probed_as_owner_scoped():
    """``owner_scope="any"`` states the list shows anyone's rows: a non-admin's list
    holding another user's row verifies it (multi-vendor's ``GET /products``)."""
    spec = _spec(
        Contract("GET", "/notes", auth="Authenticated", table="Note", kind="list",
                 owner_scope="any")
    )  # fmt: skip
    result = _run(spec, {"leak": True}, refusals=((403, NOT_PROVIDED),))
    item = result.items["R2 GET /notes"]
    assert item.verdict == "verified", item.reason
    assert not any(p["request"]["path"] == "/notes" and "own Note rows" in name
                   for name, p in result.probes.items())  # fmt: skip
    scoped = _run(spec, refusals=((403, NOT_PROVIDED),))
    assert scoped.items["R2 GET /notes"].verdict == "refuted"


# ---- the login route -------------------------------------------------------------------------


def test_a_basic_credential_login_route_is_called_with_each_users_credentials():
    """A login route taking HTTP Basic credentials (Flask-HTTPAuth's) carries an
    ``authenticated`` guard: its R1 callers present their login and password, not a
    token."""
    spec = _spec(
        Contract("POST", "/token", auth="Authenticated", kind="action",
                 errors=(ContractError(401, "Invalid credentials"),))
    )  # fmt: skip
    plain = _run(spec, refusals=((403, NOT_PROVIDED),))
    assert plain.items["R1 POST /token"].verdict == "refuted"
    refusals = ((403, NOT_PROVIDED), (403, "Invalid token."), (401, "Invalid credentials"))
    result = _run(spec, refusals=refusals, login_route=("POST", "/token"))
    item = result.items["R1 POST /token"]
    assert item.verdict == "verified", item.reason
    sent = [p["request"] for n, p in result.probes.items() if n.startswith("POST /token | alice")]
    assert sent and all(r["user"] == "alice" for r in sent)


# ---- answers that are not JSON ------------------------------------------------------------------


def test_a_detail_in_an_html_answer_is_unreachable_by_probe():
    spec = _spec(
        Contract("GET", "/html/{note_id}", auth="Public", table="Note", kind="read",
                 owner_scope="any",
                 params=(ContractParam("note_id", "path", Scalar.UUID, ref="Note"),),
                 errors=(ContractError(404, "No Note matches the given query.",
                                       when="not_found"),))
    )  # fmt: skip
    result = _run(spec)
    item = result.items["R3 GET /html/{note_id} 404 No Note matches the given query."]
    assert item.verdict == "unexercised"
    assert item.reason.startswith("unreachable-by-probe"), item.reason
    assert result.routes["GET /html/{note_id}"]["verdict"] == "verified"


# ---- auth headers, observers, the corrected verdict ---------------------------------------------


def test_probes_take_an_auth_header_function_and_an_observer():
    spec = _spec(Contract("GET", "/admin-only", auth="Admin", errors=(ContractError(403),)))
    seen: list[tuple[str, int, bool]] = []

    def observe(probe: Any, seed: Path, after: Path, answer: Answer) -> None:
        seen.append((probe.name, answer.status, seed.exists() and after.exists()))

    result = _run(spec, refusals=((403, NOT_PROVIDED), (403, "Invalid token.")),
                  auth_headers=lambda token: {"Authorization": f"Bearer {token}"},
                  observe=observe)  # fmt: skip
    assert result.items["R1 GET /admin-only"].verdict == "verified"
    assert seen and all(ok for _, _, ok in seen)


def test_verified_corrected_counts_as_verified():
    assert is_verified("verified") and is_verified(VERIFIED_CORRECTED)
    assert not is_verified("unexercised") and not is_verified("refuted")


def test_the_toy_seed_is_sound(tmp_path):
    path = make_db("seed", tmp_path / "x.db")
    con = sqlite3.connect(path)
    assert con.execute("SELECT count(*) FROM notes").fetchone()[0] == len(NOTE_IDS)
    con.close()
    assert json.dumps(sorted(USERS))


def test_the_inactive_caller_can_be_logged_in_while_active():
    """``inactive_login``: no minter -- the inactive user is made active for its login
    only, its token then refused (a token the app keeps as a row stays in the seed)."""
    spec = _spec(Contract("GET", "/admin-only", auth="Admin", errors=(ContractError(403),)))
    result = run_probes(spec, toy(), make_db, login=login, inactive_login=True,
                        refusals=((403, NOT_PROVIDED), (403, "Invalid token.")))  # fmt: skip
    assert any("| inactive |" in name for name in result.probes)
    assert result.items["R1 GET /admin-only"].verdict == "verified"
    assert not any("no mint_token" in n for n in result.notes)


def test_an_unreachable_route_is_never_called_and_its_items_say_why():
    spec = _spec(
        Contract("GET", "/admin-only", auth="Admin", errors=(ContractError(403),)),
        Contract("GET", "/public", auth="Public"),
    )
    result = _run(spec, unreachable={"GET /admin-only": "calls the network"})
    assert not any(p["route"] == "GET /admin-only" for p in result.probes.values())
    item = result.items["R1 GET /admin-only"]
    assert item.verdict == "unexercised"
    assert item.reason == "unreachable-by-probe: calls the network"
    assert result.routes["GET /admin-only"]["verdict"] == "unverified"


def test_a_session_cookie_is_presented_on_each_probes_session():
    """``auth_present``: a token that is a session cookie goes in the probe's session
    (its cookie jar), not in a header."""
    spec = _spec(Contract("GET", "/admin-only", auth="Admin", errors=(ContractError(403),)))

    def present(session: Any, token: str) -> dict[str, str]:
        session.set_cookie("sid", token)
        return {}

    result = _run(spec, refusals=((403, NOT_PROVIDED), (403, "Invalid token.")),
                  auth_present=present)  # fmt: skip
    assert result.items["R1 GET /admin-only"].verdict == "verified"
    sent = [p["request"] for n, p in result.probes.items() if "| admin |" in n]
    assert sent and result.probes[next(n for n in result.probes if "| admin |" in n)][
        "status"] == 200  # fmt: skip
