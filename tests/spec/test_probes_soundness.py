"""Layer R soundness fixes after the Phase 2 held-out run (post-hoc round, 2026-10-05):
the verifier must never say "verified" when its own probes contradict the claim.

* R1 with no callers: with no user logged in, an R1 item is ``unexercised`` ("no
  callers"), never verified on the anonymous probe alone;
* a ``Public`` contract whose anonymous probe answers 401/403 is refuted, even when the
  route states a 401 (a decorator's "Authorization header is required");
* a probe the app refused for want of a caller (a 401, or a 403 to a request carrying no
  credentials) bears on no other item: no 404 -> 401 "correction" from it;
* a route taking credentials in its body (a login or token-refresh route) answers the
  probes' invented credentials, not their caller: those answers decide nothing, and with
  the JSON login route known each caller presents its own;
* the self-check in ``summarise``: an item ``verified`` while one of its recorded probes
  contradicts it is an internal error.

The app is a toy (a session object over a small SQLite seed), as in test_probes_w3."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
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
USERS = {  # handle -> (id, email, password, admin, active)
    "admin": (1, "admin@example.com", "pw-admin", True, True),
    "alice": (2, "alice@example.com", "pw-alice", False, True),
    "bob": (3, "bob@example.com", "pw-bob", False, True),
    "carol": (4, "carol@example.com", "pw-carol", False, False),
}
NOTES = [(10, 1), (11, 2), (12, 2), (13, 3)]  # (id, owner)


def _profile() -> Profile:
    users = Schema.from_pairs(
        [("id", Scalar.INT), ("email", Scalar.STR), ("password", Scalar.STR),
         ("token", Scalar.STR), ("is_admin", Scalar.BOOL), ("active", Scalar.BOOL)],
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
        active_column="active",
        inactive_status=403,
        inactive_detail="Inactive user",
        error_body="json_key(message)",
        forbidden_detail=FORBIDDEN,
    )
    return Profile(schemas={}, api=api)


def _spec(*contracts: Contract) -> Spec:
    users = {h: (email, pw) for h, (_, email, pw, _, _) in USERS.items()}
    return Spec(_profile(), users=users, contracts=contracts)


def make_db(variant: str, path: Path) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password TEXT, "
                "token TEXT, is_admin BOOLEAN, active BOOLEAN)")  # fmt: skip
    con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, owner_id INTEGER, title TEXT)")
    for h, (i, email, pw, admin, active) in USERS.items():
        con.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                    (i, email, pw, f"tok-{h}", admin, active))  # fmt: skip
    for i, owner in NOTES:
        con.execute("INSERT INTO notes VALUES (?, ?, ?)", (i, owner, f"note {i}"))
    con.commit()
    con.close()
    return path


def login(client: Any, handle: str) -> str:
    return f"tok-{handle}"


def mint(key: Any) -> str:
    return next(f"tok-{h}" for h, u in USERS.items() if u[0] == key)


class Toy:
    """The toy app: ``/open`` answers anyone; ``/guarded`` and ``/guarded/{id}`` sit
    behind a token decorator answering ``{"error": ...}`` 401s (projmgmt's kind: its
    detail is under another key than the profile's error key); ``POST /auth/token``
    (JSON email + password) and ``POST /auth/refresh`` (a refresh token) are public
    credential routes; ``PUT /notes/{id}`` is owner-or-admin."""

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
        if path == "/open":
            return Answer(200, {}, {"ok": True})
        if path == "/auth/token" and method == "POST":
            body = json or {}
            found = self._sql("SELECT token, active FROM users WHERE email=? AND password=?",
                              (body.get("email"), body.get("password")))  # fmt: skip
            if not found or not found[0][1]:
                return Answer(401, {}, {"message": "No active account found"})
            return Answer(200, {}, {"access": found[0][0], "refresh": "ref-" + found[0][0]})
        if path == "/auth/refresh" and method == "POST":
            refresh = (json or {}).get("refresh", "")
            if not self._sql("SELECT id FROM users WHERE 'ref-' || token = ?", (refresh,)):
                return Answer(401, {}, {"message": "Token is invalid"})
            return Answer(200, {}, {"access": refresh.removeprefix("ref-")})
        if path.startswith("/guarded") or path.startswith("/notes/"):
            if "Authorization" not in headers:
                return Answer(401, {}, {"error": "Authorization header is required"})
            user = self._user(headers)
            if user is None:
                return Answer(401, {}, {"error": "Invalid or expired token"})
            me, admin, active = user
            if not active:
                return Answer(403, {}, {"message": "Inactive user"})
            if path == "/guarded":
                return Answer(200, {}, {"ok": True})
            if path == "/guarded/staff":
                # a distinction the profile does not know: bob (3) is refused
                if me == 3:
                    return Answer(403, {}, {"error": "Access denied. Manager role required."})
                if method == "POST":  # past the check: the body is refused
                    return Answer(400, {}, {"message": "name is required"})
                return Answer(200, {}, {"ok": True})
            key = int(path.rsplit("/", 1)[1])
            note = self._sql("SELECT id, owner_id, title FROM notes WHERE id=?", (key,))
            if not note:
                return Answer(404, {}, {"message": "Note not found"})
            nid, owner, title = note[0]
            if path.startswith("/notes/") and method == "PUT":
                if owner != me and (not admin or self.flags.get("owner_only")):
                    return Answer(403, {}, {"message": FORBIDDEN})
                self._sql("UPDATE notes SET title=? WHERE id=?", ((json or {}).get("title"), nid))
            return Answer(200, {}, {"id": nid, "owner_id": owner, "title": title})
        return Answer(404, {}, {"message": "Not Found"})

    def post(self, path: str, json: Any = None) -> Any:
        """The client a login uses: an httpx-like response."""
        answer = self.request("POST", path, json=json)

        class Response:
            status_code = answer.status
            cookies: dict[str, str] = {}

            def json(self) -> Any:
                return answer.body

        return Response()


def login_route(client: Any, handle: str) -> str:
    """Logging in through ``POST /auth/token`` (its answer has the refresh token too)."""
    _, email, pw, _, _ = USERS[handle]
    return client.post("/auth/token", json={"email": email, "password": pw}).json()["access"]


def toy(**flags: bool):
    @contextmanager
    def sessions(db: Path) -> Iterator[Toy]:
        yield Toy(db, **flags)

    return sessions


def _run(spec: Spec, *, callers: bool = True, flags: dict[str, bool] | None = None,
         **kw: Any) -> RuleResult:  # fmt: skip
    if callers:
        kw.setdefault("login", login)
        kw.setdefault("mint_token", mint)
    return run_probes(spec, toy(**(flags or {})), make_db, **kw)


OPEN = Contract("GET", "/open", auth="Public")
GUARDED = Contract("GET", "/guarded", auth="Public", errors=(ContractError(401),))
GUARDED_ROW = Contract(
    "GET", "/guarded/{id}", auth="Public", table="Note", kind="read",
    params=(ContractParam("id", "path", Scalar.INT, ref="Note"),),
    errors=(ContractError(404, when="not_found"), ContractError(401)),
)  # fmt: skip
TOKEN = Contract(
    "POST", "/auth/token", auth="Public", kind="action",
    params=(ContractParam("email", "body", Scalar.STR),
            ContractParam("password", "body", Scalar.STR)),
)  # fmt: skip
REFRESH = Contract(
    "POST", "/auth/refresh", auth="Public", kind="create",
    params=(ContractParam("refresh", "body", Scalar.STR),),
)  # fmt: skip


# ---- R1 with no callers ---------------------------------------------------------------------


def test_without_callers_r1_is_unexercised_never_verified():
    result = _run(_spec(OPEN), callers=False)
    v = result.items["R1 GET /open"]
    assert v.verdict == "unexercised", v.reason
    assert v.reason.startswith("no callers")
    assert "layer R: no login given; probes run without callers" in result.notes


def test_with_callers_the_same_route_is_verified():
    v = _run(_spec(OPEN)).items["R1 GET /open"]
    assert v.verdict == "verified", v.reason


def test_without_callers_an_authenticated_route_refusing_anonymous_is_not_verified():
    """The anonymous refusal agrees with the prediction, but the prediction is about
    who *is* admitted: with no caller, nothing says so."""
    spec = _spec(Contract("GET", "/guarded", auth="Authenticated"))
    v = _run(spec, callers=False).items["R1 GET /guarded"]
    assert v.verdict == "unexercised", v.reason


# ---- Public contracts refused to anonymous ----------------------------------------------------


@pytest.mark.parametrize("callers", [True, False])
def test_a_public_contract_answering_anonymous_401_is_refuted(callers):
    """The route states a 401 (its decorator's), yet the anonymous 401 is a refusal of
    the caller: a Public contract is refuted, with or without callers."""
    v = _run(_spec(GUARDED), callers=callers).items["R1 GET /guarded"]
    assert v.verdict == "refuted", v.reason
    assert "anonymous refused" in v.reason


def test_with_callers_the_refuted_public_contract_is_corrected_to_authenticated():
    from lattice.importer.rules import correct

    spec = _spec(GUARDED)
    result = _run(spec)
    fixed, corrections = correct(spec, result)
    assert [c.change for c in corrections] == ["auth Public -> Authenticated"]


def test_without_callers_a_refuted_public_contract_proposes_no_auth():
    from lattice.importer.rules import correct

    spec = _spec(GUARDED)
    _, corrections = correct(spec, _run(spec, callers=False))
    assert corrections == []


# ---- the correction cascade -------------------------------------------------------------------


def test_a_missing_id_probe_refused_for_want_of_a_caller_corrects_nothing():
    """H2's 404 -> 401: the not-found probe ran with no caller and was refused; it never
    reached the lookup, so the stated 404 is neither confirmed nor contradicted."""
    from lattice.importer.rules import correct

    spec = _spec(GUARDED_ROW)
    result = _run(spec, callers=False)
    v = result.items["R3 GET /guarded/{id} 404"]
    assert v.verdict == "unexercised", v.reason
    assert "refused" in v.reason
    _, corrections = correct(spec, result)
    assert not [c for c in corrections if c.item.startswith("R3 ")]


def test_with_callers_the_missing_id_probe_confirms_the_404():
    spec = _spec(replace_auth(GUARDED_ROW, "Authenticated"))
    v = _run(spec).items["R3 GET /guarded/{id} 404"]
    assert v.verdict == "verified", v.reason


def replace_auth(c: Contract, auth: str) -> Contract:
    from dataclasses import replace

    return replace(c, auth=auth)


# ---- credential routes ------------------------------------------------------------------------


@pytest.mark.parametrize("contract", [TOKEN, REFRESH], ids=["token", "refresh"])
def test_answers_to_invented_credentials_refuse_no_caller(contract):
    """H3's simplejwt routes: the probes' invented credentials get 401; that is the
    credentials' answer, not a refusal of anonymous, Admin or Customer."""
    v = _run(_spec(contract)).items[f"R1 {contract.endpoint}"]
    assert v.verdict != "refuted", v.reason


def test_a_refresh_route_is_called_with_each_callers_own_refresh_token():
    """What a caller's login answered (its ``refresh``) is presented on the refresh
    route: the answer is then about the caller, and the Public contract verifies."""
    result = _run(_spec(REFRESH), login=login_route)
    v = result.items["R1 POST /auth/refresh"]
    assert v.verdict == "verified", v.reason
    bodies = [result.probes[n]["request"]["json"] for n in v.probes]
    assert {"refresh": "ref-tok-alice"} in bodies


def test_on_a_credential_route_a_refusal_never_decides():
    """Without what the login answered (a minted token), the refresh route still gets
    invented credentials: their 401s neither refute nor verify."""
    v = _run(_spec(REFRESH)).items["R1 POST /auth/refresh"]
    assert v.verdict == "unexercised", v.reason


def test_the_json_login_route_is_called_with_each_callers_own_credentials():
    result = _run(_spec(TOKEN), json_login=("POST", "/auth/token", "email", "password"))
    v = result.items["R1 POST /auth/token"]
    assert v.verdict == "verified", v.reason
    bodies = [result.probes[n]["request"]["json"] for n in v.probes]
    assert {"email": "alice@example.com", "password": "pw-alice"} in bodies


# ---- the self-check -----------------------------------------------------------------------------


def _item(item_id: str):
    return next(i for i in rule_items(_spec(OPEN, GUARDED)) if i.id == item_id)


def test_summarise_refuses_a_verified_item_a_recorded_probe_contradicts():
    result = RuleResult()
    item = _item("R1 GET /guarded")
    result.items[item.id] = ItemVerdict(item, "verified", "forced", ["p"])
    result.contradicted[item.id] = ["p"]
    with pytest.raises(VerdictInvariantError, match="R1 GET /guarded"):
        summarise(_spec(OPEN, GUARDED), result)


def test_a_run_records_the_contradicting_probes_of_a_refuted_item():
    result = _run(_spec(GUARDED))
    assert result.contradicted["R1 GET /guarded"]
    assert all(not result.contradicted.get(i) for i, v in result.items.items()
               if v.verdict == "verified")  # fmt: skip


# ---- owner_or_admin (an owner-or-staff object permission) ------------------------------------

EDIT_NOTE = Contract(
    "PUT", "/notes/{id}", auth="Authenticated", table="Note", kind="update",
    owner_scope="owner_or_admin",
    params=(ContractParam("id", "path", Scalar.INT, ref="Note"),
            ContractParam("title", "body", Scalar.STR)),
)  # fmt: skip


def test_owner_or_admin_validates_on_an_owned_table_only():
    from dataclasses import replace

    from lattice.spec import validate

    assert validate(_spec(EDIT_NOTE)) == []
    on_users = replace(EDIT_NOTE, path="/users/{id}", table="User",
                       params=(ContractParam("id", "path", Scalar.INT, ref="User"),))  # fmt: skip
    assert [p.code for p in validate(_spec(on_users))] == ["S023"]


def test_owner_or_admin_is_verified_on_the_app_that_keeps_it():
    result = _run(_spec(EDIT_NOTE))
    for item in ("R1 PUT /notes/{id}", "R2 PUT /notes/{id}"):
        assert result.items[item].verdict == "verified", (item, result.items[item].reason)
    r2 = result.items["R2 PUT /notes/{id}"].probes
    assert any("| admin |" in n and "another user's" in n for n in r2), r2


def test_owner_or_admin_is_refuted_when_the_admin_is_refused_too():
    v = _run(_spec(EDIT_NOTE), flags={"owner_only": True}).items["R2 PUT /notes/{id}"]
    assert v.verdict == "refuted", v.reason


def test_an_owner_or_staff_r1_refutation_is_corrected_to_owner_or_admin_not_admin():
    from dataclasses import replace

    from lattice.importer.rules import correct

    spec = _spec(replace(EDIT_NOTE, owner_scope=None))
    result = _run(spec)
    item = result.items["R1 PUT /notes/{id}"]
    # forced: as if the non-admins' probes had all hit other users' notes
    result.items[item.item.id] = ItemVerdict(item.item, "refuted", "Customer refused",
                                             item.probes, {"allowed": ["Admin"]})  # fmt: skip
    fixed, corrections = correct(spec, result, owner_or_admin={"PUT /notes/{id}"})
    assert [c.change for c in corrections] == [
        "owner_scope -> owner_or_admin (not auth Admin: an owner-or-admin object permission)"
    ]
    (contract,) = fixed.contracts
    assert contract.auth == "Authenticated" and contract.owner_scope == "owner_or_admin"
    # without the front end's evidence, the old correction stands
    _, plain = correct(spec, result)
    assert [c.change for c in plain] == ["auth Authenticated -> Admin"]


def test_the_decision_log_states_that_no_target_compiles_owner_or_admin():
    log = (Path(__file__).resolve().parents[2] / "docs" / "lattice-language-rules.md").read_text()
    row = next(line for line in log.splitlines() if '"owner_or_admin"' in line)
    assert row.startswith("| 2026-10-05 | Target gate (R4) |")
    assert "compiled by no target" in row and "S023" in row


def test_callers_of_one_role_answered_differently_refute_the_auth_without_a_correction():
    """Two Customers, one admitted and one refused (a role column the profile does not
    model: H2's manager / employee): R1 is refuted -- never "verified" on the one
    admitted caller -- and no auth is proposed (none spells it)."""
    from lattice.importer.rules import correct

    spec = _spec(Contract("GET", "/guarded/staff", auth="Authenticated"))
    result = _run(spec)
    v = result.items["R1 GET /guarded/staff"]
    assert v.verdict == "refuted", v.reason
    assert "Customer" in v.reason and "differently" in v.reason
    assert correct(spec, result)[1] == []


def test_a_split_shows_through_an_answer_past_the_check_that_is_no_2xx():
    """The same request (no row addressed): admin and alice get a 400 (past the role
    check), bob a 403 -- that is a split too."""
    spec = _spec(Contract("POST", "/guarded/staff", auth="Authenticated", kind="action"))
    v = _run(spec).items["R1 POST /guarded/staff"]
    assert v.verdict == "refuted", v.reason
    assert "differently" in v.reason


def test_the_check_reads_the_raw_answers_of_r1_too():
    """Independent of the refusal rules: a Public R1 forced to verified while its
    anonymous probe answered 401 is caught from the recorded answer itself."""
    result = _run(_spec(GUARDED))
    item = result.items["R1 GET /guarded"]
    result.items[item.item.id] = ItemVerdict(item.item, "verified", "forced", item.probes)
    result.contradicted.clear()
    with pytest.raises(VerdictInvariantError):
        summarise(_spec(GUARDED), result)


def test_a_malformed_tokens_401_is_a_refusal_though_the_route_states_a_401():
    """microblog's ``DELETE /api/tokens`` states the 401 of its own ``abort(401)``: a
    malformed token's 401 (the auth layer's) is still a refusal, never "admitted"."""
    spec = _spec(Contract("GET", "/guarded", auth="Authenticated", errors=(ContractError(401),)))
    v = _run(spec).items["R1 GET /guarded"]
    assert v.verdict == "verified", v.reason
