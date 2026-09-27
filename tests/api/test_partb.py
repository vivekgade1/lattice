"""Sub-project 1b Part B (spec 2026-09-27 lattice-loops §4): the real-app gaps found by
the template proof.

* B1 ``ApiProfile.active_column``: an inactive caller is refused by auth, after the
  caller lookup (401) and before the role check (403) and parameter validation (422);
* B2 ``ApiProfile.error_body``: ``"lattice"`` (``{"error": code, "detail": ...}``) or
  ``"fastapi"`` (``{"detail": ...}``), with configurable automatic 401/403 texts;
* B3 ``ApiProfile.datetime_wire``: ``"iso_z"`` or ``"naive"`` (pydantic's spelling of a
  naive UTC datetime);
* B4 ``Email`` and ``Str(pattern=...)`` parameters (422 on a mismatch);
* B5 ``unique_or=Conflict("...")`` on insert/update: the store's UNIQUE violation
  answers the declared 409;
* B6 module-level list-of-Str constants in API programs, usable in ``select(cols=)``.

Profile validation and checker rules are unit-tested; behaviour runs end to end through
the runtime and, request by request, through the generated FastAPI app in process
(tests/api/partb.py; the harness-level differential runs the same program too).
"""

from __future__ import annotations

import importlib
import shutil
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest
from starlette.testclient import TestClient

from lattice.api.codegen import generate_fastapi
from lattice.api.ir import lower_api
from lattice.api.runtime import UNIQUE_CONFLICT, build_app
from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.profile import ApiProfile, Profile
from lattice.typesys import Scalar, Schema
from tests.api import partb, realapps
from tests.api.partb import (
    EMAIL_TAKEN,
    FORBIDDEN,
    NATIVES,
    NATIVES_FILE,
    PROFILE,
    PROGRAM,
    SIGNUP_TAKEN,
    TOKENS,
    UNAUTHORIZED,
    build_db,
    uid,
)
from tests.api.test_codegen_differential import normalize

S = Scalar
KIT_PATH = Path(__file__).resolve().parents[2] / "bench" / "api_kit" / "platform_kit.py"
_MODULES = ("platform_kit", "generated_partb", "natives")


def _h(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[who]}"}


def _plans(program: str = PROGRAM, profile: Profile = PROFILE):
    check = check_source(program, profile)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    return lower_api(check.program)


def _profile(**changes) -> Profile:
    return Profile(schemas={}, api=replace(partb.API, **changes))


@pytest.fixture(scope="module")
def pristine(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("partb") / "pristine.db"
    build_db(path)
    return path


@pytest.fixture
def db(pristine: Path, tmp_path: Path) -> Path:
    path = tmp_path / "app.db"
    shutil.copyfile(pristine, path)
    return path


def _client(db: Path, program: str = PROGRAM, profile: Profile = PROFILE) -> TestClient:
    return TestClient(build_app(_plans(program, profile), db, natives=NATIVES))


@pytest.fixture
def client(db: Path) -> Iterator[TestClient]:
    with _client(db) as c:
        yield c


def _users(db: Path) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute('SELECT id, email, full_name FROM "user" ORDER BY rowid').fetchall()
    finally:
        conn.close()


# ---- profile validation ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"active_column": "nope"}, "active_column"),
        ({"active_column": "email"}, "active_column"),  # not a Bool column
        ({"active_column": "is_active", "inactive_status": 404}, "inactive_status"),
        ({"active_column": "is_active", "inactive_detail": ""}, "inactive_detail"),
        ({"error_body": "json"}, "error_body"),
        ({"unauthorized_detail": ""}, "unauthorized_detail"),
        ({"forbidden_detail": 3}, "forbidden_detail"),
        ({"datetime_wire": "local"}, "datetime_wire"),
    ],
)
def test_the_profile_rejects_bad_part_b_settings(changes, message):
    with pytest.raises(ValueError, match=message):
        replace(realapps.API, **changes)


def test_the_part_b_defaults_are_todays_behaviour():
    api = realapps.API
    assert api.active_column is None
    assert (api.inactive_status, api.inactive_detail) == (400, "Inactive user")
    assert api.error_body == "lattice" and api.datetime_wire == "iso_z"
    assert api.unauthorized_detail is None and api.forbidden_detail is None


def test_the_part_b_settings_reach_every_plan():
    for plan in _plans():
        assert plan.active_column == "is_active"
        assert (plan.inactive_status, plan.inactive_detail) == (400, "Inactive user")
        assert plan.error_body == "fastapi" and plan.datetime_wire == "naive"
        assert (plan.unauthorized_detail, plan.forbidden_detail) == (UNAUTHORIZED, FORBIDDEN)


# ---- checker: B4 parameter types ---------------------------------------------------------

USERS = Schema.from_pairs(
    [("id", S.INT), ("email", S.STR), ("role", S.STR), ("token", S.STR), ("code", S.STR)],
    keys=[("id",)],
    private=["token"],
)
CHECK_PROFILE = Profile(
    schemas={},
    api=ApiProfile(
        tables={"Users": USERS},
        table_names={"Users": "users"},
        roles=frozenset({"Public", "Customer", "Admin"}),
    ),
)


def _check(src: str, profile: Profile = CHECK_PROFILE):
    return check_source("# lattice: v1\n" + dedent(src).lstrip("\n"), profile)


def _errors(src: str, profile: Profile = CHECK_PROFILE) -> list[str]:
    return [d.code for d in _check(src, profile).diagnostics if d.severity is Severity.ERROR]


def _find(param: str) -> str:
    return f"""
    @endpoint("GET", "/users", auth=Admin)
    def find(user: CurrentUser, {param}) -> One | NotFound:
        users = fetch(Users)
        found = where(users, col("email") == email)
        row = one(found, else_=NotFound("no such user"))
        shown = select(row, cols=["id", "email"])
        return shown
    """


@pytest.mark.parametrize(
    "param",
    [
        "email: Email",
        "email: Email(max_len=255)",
        "email: Email(min_len=3, max_len=255)",
        'email: Str(pattern="[a-z]+@[a-z]+")',
        'email: Str(max_len=20, pattern="^.+$")',
        'email: Email(max_len=255) = "a@b.co"',
    ],
)
def test_email_and_pattern_parameters_are_accepted(param):
    assert _errors(_find(param)) == []


@pytest.mark.parametrize(
    ("param", "message"),
    [
        ('email: Str(pattern="[a-z")', "pattern"),  # not a regular expression
        ("email: Str(pattern=3)", "pattern"),
        ('email: Int(pattern="[0-9]+")', "no constraint 'pattern'"),
        ("email: Email(ge=1)", "no constraint 'ge'"),
        ('email: Email(pattern="x")', "no constraint 'pattern'"),
        ('email: Email = "not-an-email"', "default of 'email'"),
        ('email: Str(pattern="[a-z]+") = "ABC"', "default of 'email'"),
    ],
)
def test_bad_email_and_pattern_parameters_are_l601(param, message):
    result = _check(_find(param))
    found = [d for d in result.diagnostics if d.code == "L601"]
    assert found and message in found[0].message, result.diagnostics


def test_an_email_parameter_is_a_str():
    src = _find("email: Email").replace('col("email") == email', 'col("id") == email')
    assert "L402" in _errors(src) or "L403" in _errors(src)


def test_an_email_parameter_written_needs_a_bound():
    src = """
    @endpoint("PATCH", "/users/me", auth=Customer)
    def set_email(user: CurrentUser, email: Email) -> One:
        done = update(user, email=email)
        shown = select(done, cols=["id", "email"])
        return shown
    """
    assert _errors(src) == ["L811"]
    assert _errors(src.replace("email: Email", "email: Email(max_len=255)")) == []


# ---- checker: B5 unique_or ----------------------------------------------------------------

SET_EMAIL = """
@endpoint("PATCH", "/users/me", auth=Customer)
def set_email(user: CurrentUser, email: Email(max_len=255)) -> One | Conflict:
    done = update(user, email=email, unique_or=Conflict("taken"))
    shown = select(done, cols=["id", "email"])
    return shown
"""
SIGN_UP = """
@endpoint("POST", "/users", auth=Public)
def sign_up(email: Email(max_len=255), code: Str(max_len=9)) -> Created | Conflict:
    made = insert(Users, email=email, role="Customer", token=code, code=code, unique_or=Conflict())
    shown = select(made, cols=["id", "email"])
    return shown
"""


def test_unique_or_is_accepted_on_update_and_insert():
    assert _errors(SET_EMAIL) == []
    assert _errors(SIGN_UP) == []
    assert [d.code for d in _check(SET_EMAIL).diagnostics] == []


@pytest.mark.parametrize(
    "value", ['NotFound("taken")', '"taken"', "Conflict(3)", 'Conflict("a", "b")', "Conflict"]
)
def test_unique_or_must_be_a_conflict(value):
    src = SET_EMAIL.replace('Conflict("taken")', value)
    result = _check(src)
    found = [d for d in result.diagnostics if d.code == "L404"]
    assert found and "unique_or" in found[0].message, result.diagnostics


def test_unique_or_needs_conflict_declared():
    src = SET_EMAIL.replace("-> One | Conflict:", "-> One:")
    assert _errors(src) == ["L821"]


def test_a_declared_conflict_raised_only_by_unique_or_is_not_l822():
    assert "L822" not in [d.code for d in _check(SET_EMAIL).diagnostics]


def test_unique_or_alone_is_a_no_op_update():
    src = SET_EMAIL.replace("email=email, unique_or", "unique_or")
    assert "L304" in _errors(src)


def test_unique_or_on_delete_is_rejected():
    src = """
    @endpoint("DELETE", "/users/me", auth=Customer)
    def leave(user: CurrentUser) -> NoContent | Conflict:
        gone = delete(user, unique_or=Conflict("x"))
        return gone
    """
    assert _errors(src) != []


def test_unique_or_is_not_a_column():
    src = SET_EMAIL.replace("unique_or=", "unique_ok=")
    assert "L401" in _errors(src)


# ---- checker: B6 list constants ---------------------------------------------------------

SHOW_ME = """
SHOWN = ["id", "email"]


@endpoint("GET", "/users/me", auth=Customer)
def me(user: CurrentUser) -> One:
    shown = select(user, cols=SHOWN)
    return shown
"""


def test_a_list_constant_is_accepted_in_select_cols():
    assert [d.code for d in _check(SHOW_ME).diagnostics] == []


def test_a_list_constant_lowers_to_its_columns():
    (plan,) = lower_api(_check(SHOW_ME).program)
    assert plan.schema.names() == ("id", "email")


def test_a_list_constant_in_a_step_is_expanded():
    src = """
    SHOWN = ["id", "email"]


    @step
    def public(t: Table[Users]) -> Table:
        shown = select(t, cols=SHOWN)
        return shown


    @endpoint("GET", "/users/me", auth=Customer)
    def me(user: CurrentUser) -> One:
        shown = public(user)
        return shown
    """
    result = _check(src)
    assert result.program is not None, result.diagnostics
    (plan,) = lower_api(result.program)
    assert plan.schema.names() == ("id", "email")


def test_a_list_constant_is_single_assignment():
    src = SHOW_ME.replace('SHOWN = ["id", "email"]\n', 'SHOWN = ["id", "email"]\nSHOWN = ["id"]\n')
    assert "L201" in _errors(src)


@pytest.mark.parametrize(
    "use",
    [
        'shown = where(user, col("email") == SHOWN)',
        "shown = sort(user, by=SHOWN)",
        "shown = derive(user, x=SHOWN)",
    ],
)
def test_a_list_constant_is_usable_only_as_select_cols(use):
    src = SHOW_ME.replace("shown = select(user, cols=SHOWN)", use)
    result = _check(src)
    found = [d for d in result.diagnostics if d.code == "L404"]
    assert found and "SHOWN" in found[0].message, result.diagnostics


@pytest.mark.parametrize("value", ["[1, 2]", "[]", '["id", 2]', '("id", "email")', '[["id"]]'])
def test_other_lists_stay_l840(value):
    src = SHOW_ME.replace('["id", "email"]', value, 1)
    assert "L840" in _errors(src)


def test_a_list_constant_of_unknown_columns_is_l401():
    src = SHOW_ME.replace('["id", "email"]', '["id", "nope"]', 1)
    assert _errors(src) == ["L401"]


def test_an_unused_list_constant_is_reported_like_any_constant():
    src = 'EXTRA = ["id"]\n' + SHOW_ME
    unused = [d.code for d in _check(src).diagnostics if d.severity is not Severity.ERROR]
    assert unused and _errors(src) == []


# ---- B1: the active check in auth ---------------------------------------------------------


def test_an_inactive_caller_is_refused_by_auth(client):
    r = client.get("/items/", headers=_h("carol"))
    assert (r.status_code, r.json()) == (400, {"detail": "Inactive user"})


def test_the_active_check_runs_before_the_role_check(client):
    r = client.get("/users/", headers=_h("carol"))  # an Admin endpoint
    assert (r.status_code, r.json()) == (400, {"detail": "Inactive user"})


def test_the_active_check_runs_before_parameter_validation(client):
    r = client.get("/items/", params={"limit": "many"}, headers=_h("carol"))
    assert (r.status_code, r.json()) == (400, {"detail": "Inactive user"})
    r = client.patch("/users/me", json={"email": "not-an-email"}, headers=_h("carol"))
    assert (r.status_code, r.json()) == (400, {"detail": "Inactive user"})


def test_an_unknown_caller_is_still_401_before_the_active_check(client):
    token = "tok-" + uid(777)
    r = client.get("/items/", headers={"Authorization": f"Bearer {token}"})
    assert (r.status_code, r.json()) == (401, {"detail": UNAUTHORIZED})


def test_an_active_caller_passes(client):
    assert client.get("/items/", headers=_h("alice")).status_code == 200


def test_a_public_endpoint_has_no_active_check(client):
    r = client.post("/users/signup", json={"email": "new@example.com", "password": "longpassword"})
    assert r.status_code == 201


def test_the_inactive_status_and_detail_are_configurable(db: Path):
    profile = _profile(inactive_status=403, inactive_detail="Account disabled")
    with _client(db, profile=profile) as c:
        r = c.get("/items/", headers=_h("carol"))
    assert (r.status_code, r.json()) == (403, {"detail": "Account disabled"})


def test_the_inactive_body_in_the_lattice_style(db: Path):
    profile = _profile(error_body="lattice")
    with _client(db, profile=profile) as c:
        r = c.get("/items/", headers=_h("carol"))
    assert (r.status_code, r.json()) == (400, {"error": "bad_request", "detail": "Inactive user"})


def test_without_an_active_column_an_inactive_caller_is_served(db: Path):
    with _client(db, profile=_profile(active_column=None)) as c:
        assert c.get("/items/", headers=_h("carol")).status_code == 200


# ---- B2: error bodies ------------------------------------------------------------------------


def test_fastapi_error_bodies(client):
    cases = [
        (client.get("/items/"), 401, {"detail": UNAUTHORIZED}),
        (client.get("/items/", headers={"Authorization": "Bearer junk"}), 401,
         {"detail": UNAUTHORIZED}),
        (client.get("/users/", headers=_h("alice")), 403, {"detail": FORBIDDEN}),
        (client.get(f"/items/{uid(999)}", headers=_h("alice")), 404,
         {"detail": "Item not found"}),
        (client.get(f"/items/{uid(103)}", headers=_h("alice")), 403,
         {"detail": "Not enough permissions"}),
        (client.get("/nowhere", headers=_h("alice")), 404, {"detail": "Not Found"}),
        (client.delete("/users/", headers=_h("admin")), 405, {"detail": "Method Not Allowed"}),
    ]  # fmt: skip
    for response, status, body in cases:
        assert (response.status_code, response.json()) == (status, body)


def test_fastapi_validation_body(client):
    r = client.get("/items/", params={"limit": "many", "skip": -1}, headers=_h("alice"))
    assert r.status_code == 422
    assert r.json() == {
        "detail": [
            {"loc": ["query", "skip"], "msg": "must be >= 0"},
            {"loc": ["query", "limit"], "msg": "must be an integer"},
        ]
    }


def test_lattice_error_bodies_are_unchanged_by_default(db: Path):
    profile = _profile(error_body="lattice", unauthorized_detail=None, forbidden_detail=None)
    with _client(db, profile=profile) as c:
        assert c.get("/items/").json() == {"error": "unauthorized"}
        assert c.get("/users/", headers=_h("alice")).json() == {"error": "forbidden"}
        assert c.get("/nowhere").json() == {"error": "not_found"}
        r = c.get("/items/", params={"limit": "x"}, headers=_h("alice"))
        assert r.json() == {
            "error": "validation",
            "detail": [{"loc": ["query", "limit"], "msg": "must be an integer"}],
        }


def test_lattice_error_bodies_carry_configured_auth_details(db: Path):
    profile = _profile(error_body="lattice")
    with _client(db, profile=profile) as c:
        assert c.get("/items/").json() == {"error": "unauthorized", "detail": UNAUTHORIZED}
        r = c.get("/users/", headers=_h("alice"))
        assert r.json() == {"error": "forbidden", "detail": FORBIDDEN}


def test_fastapi_auth_details_default_to_the_status_phrase(db: Path):
    profile = _profile(unauthorized_detail=None, forbidden_detail=None)
    with _client(db, profile=profile) as c:
        assert c.get("/items/").json() == {"detail": "Unauthorized"}
        assert c.get("/users/", headers=_h("alice")).json() == {"detail": "Forbidden"}


# ---- B3: DateTime on the wire ----------------------------------------------------------------


def test_naive_datetime_wire(client):
    r = client.get(f"/items/{uid(101)}", headers=_h("alice"))
    assert r.json()["created_at"] == "2000-01-05T10:00:00"
    made = client.post("/items/", json={"title": "Fresh"}, headers=_h("alice")).json()
    stamp = made["created_at"]
    assert "T" in stamp and not stamp.endswith("Z") and "+" not in stamp


def test_naive_wire_keeps_microseconds(db: Path):
    conn = sqlite3.connect(db)
    conn.execute("UPDATE item SET created_at = ? WHERE id = ?", ("2000-01-05T10:00:00.120000Z",
                                                                 uid(101)))  # fmt: skip
    conn.commit()
    conn.close()
    with _client(db) as c:
        r = c.get(f"/items/{uid(101)}", headers=_h("alice"))
    assert r.json()["created_at"] == "2000-01-05T10:00:00.120000"


def test_iso_z_wire_by_default(db: Path):
    with _client(db, profile=_profile(datetime_wire="iso_z")) as c:
        r = c.get(f"/items/{uid(101)}", headers=_h("alice"))
    assert r.json()["created_at"] == "2000-01-05T10:00:00.000000Z"


# ---- B4: Email and pattern= ------------------------------------------------------------------


def test_an_invalid_email_is_422(client, db: Path):
    before = _users(db)
    r = client.patch("/users/me", json={"email": "not-an-email"}, headers=_h("bob"))
    assert r.status_code == 422
    assert r.json() == {"detail": [{"loc": ["body", "email"], "msg": "must be an email address"}]}
    assert _users(db) == before


@pytest.mark.parametrize(
    "email", ["a@b", "a b@example.com", "@example.com", "a@@example.com", "a@example..com", ""]
)
def test_email_rejects(client, email):
    r = client.post("/users/signup", json={"email": email, "password": "longpassword"})
    assert r.status_code == 422, email


@pytest.mark.parametrize("email", ["bob.new@example.com", "x+tag@sub.example.org", "Z_9@a.io"])
def test_email_accepts(client, email):
    r = client.patch("/users/me", json={"email": email}, headers=_h("bob"))
    assert (r.status_code, r.json()["email"]) == (200, email)


def _email_of_len(n: int) -> str:
    from lattice.importer.suite import _email_of_len as make

    email = make(n)
    assert len(email) == n
    return email


def test_an_email_longer_than_254_characters_is_422(client, db: Path):
    """RFC 5321 caps an address at 254 characters (pydantic's EmailStr answers 422), so
    Email refuses a longer one even under a larger max_len (here 255)."""
    before = _users(db)
    r = client.patch("/users/me", json={"email": _email_of_len(255)}, headers=_h("bob"))
    assert r.status_code == 422
    assert r.json() == {
        "detail": [{"loc": ["body", "email"], "msg": "must have at most 254 characters"}]
    }
    assert _users(db) == before
    ok = client.patch("/users/me", json={"email": _email_of_len(254)}, headers=_h("bob"))
    assert (ok.status_code, len(ok.json()["email"])) == (200, 254)


def test_a_smaller_email_max_len_still_applies(client):
    r = client.post(
        "/users/signup", json={"email": _email_of_len(256), "password": "longpassword"}
    )
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"] == "must have at most 255 characters"


def test_a_pattern_mismatch_is_422(client):
    r = client.post("/items/", json={"title": "lower case"}, headers=_h("alice"))
    assert r.status_code == 422
    assert r.json() == {
        "detail": [{"loc": ["body", "title"], "msg": "must match the pattern '[A-Z][A-Za-z ]*'"}]
    }
    ok = client.post("/items/", json={"title": "Upper Case"}, headers=_h("alice"))
    assert ok.status_code == 201


def test_a_pattern_must_match_the_whole_value(client):
    r = client.post("/items/", json={"title": "Upper 1"}, headers=_h("alice"))
    assert r.status_code == 422


# ---- B5: unique_or ---------------------------------------------------------------------------


def test_unique_or_answers_the_declared_conflict_on_update(client, db: Path):
    before = _users(db)
    r = client.patch("/users/me", json={"email": "alice@example.com"}, headers=_h("bob"))
    assert (r.status_code, r.json()) == (409, {"detail": EMAIL_TAKEN})
    assert _users(db) == before


def test_unique_or_answers_the_declared_conflict_on_insert(client, db: Path):
    before = _users(db)
    r = client.post(
        "/users/signup", json={"email": "alice@example.com", "password": "longpassword"}
    )
    assert (r.status_code, r.json()) == (409, {"detail": SIGNUP_TAKEN})
    assert _users(db) == before


def test_unique_or_in_the_lattice_style(db: Path):
    with _client(db, profile=_profile(error_body="lattice")) as c:
        r = c.patch("/users/me", json={"email": "alice@example.com"}, headers=_h("bob"))
    assert (r.status_code, r.json()) == (409, {"error": "conflict", "detail": EMAIL_TAKEN})


def test_without_unique_or_the_conflict_is_generic(db: Path):
    program = PROGRAM.replace(
        '        unique_or=Conflict("User with this email already exists"),\n', ""
    ).replace("-> One | Conflict:", "-> One:", 1)
    assert program != PROGRAM
    with _client(db, program=program) as c:
        r = c.patch("/users/me", json={"email": "alice@example.com"}, headers=_h("bob"))
    assert (r.status_code, r.json()) == (409, {"detail": UNIQUE_CONFLICT})


def test_the_same_email_is_no_conflict(client):
    r = client.patch("/users/me", json={"email": "bob@example.com"}, headers=_h("bob"))
    assert r.status_code == 200


# ---- B6: list constants ----------------------------------------------------------------------


def test_list_constants_select_the_listed_columns(client):
    me = client.get("/users/me", headers=_h("alice")).json()
    assert list(me) == ["email", "is_active", "is_superuser", "full_name", "id", "created_at"]
    brief = client.get(f"/items/{uid(101)}/brief", headers=_h("alice")).json()
    assert brief == {"id": uid(101), "title": "Alpha", "created_at": "2000-01-05T10:00:00"}


# ---- the generated app answers like the runtime ---------------------------------------------


@pytest.fixture
def apps(tmp_path: Path, monkeypatch) -> Iterator[tuple[TestClient, TestClient]]:
    plans = _plans()
    gen_dir = tmp_path / "gen"
    gen_dir.mkdir()
    shutil.copyfile(KIT_PATH, gen_dir / "platform_kit.py")
    shutil.copyfile(NATIVES_FILE, gen_dir / "natives.py")
    (gen_dir / "generated_partb.py").write_text(generate_fastapi(plans, PROGRAM))
    build_db(gen_dir / "app.db")
    rt_db = tmp_path / "rt.db"
    build_db(rt_db)
    monkeypatch.chdir(gen_dir)
    monkeypatch.syspath_prepend(str(gen_dir))
    for name in _MODULES:
        sys.modules.pop(name, None)
    module = importlib.import_module("generated_partb")
    try:
        with (
            TestClient(build_app(plans, rt_db, natives=NATIVES)) as rt,
            TestClient(module.app) as gen,
        ):
            yield rt, gen
    finally:
        for name in _MODULES:
            sys.modules.pop(name, None)


RAW = [
    ("GET", "/items/", {}, None),
    ("GET", "/items/", {"Authorization": "Bearer junk"}, None),
    ("GET", "/items/", _h("carol"), None),
    ("GET", "/items/?limit=many", _h("carol"), None),
    ("GET", "/users/", _h("carol"), None),
    ("GET", "/users/", _h("alice"), None),
    ("GET", "/users/?limit=2", _h("admin"), None),
    ("GET", "/items/?limit=many&skip=-1", _h("alice"), None),
    ("GET", "/items/", _h("alice"), None),
    ("GET", f"/items/{uid(101)}", _h("alice"), None),
    ("GET", f"/items/{uid(103)}", _h("alice"), None),
    ("GET", f"/items/{uid(999)}", _h("alice"), None),
    ("GET", f"/items/{uid(101)}/brief", _h("alice"), None),
    ("GET", "/users/me", _h("alice"), None),
    ("GET", "/nowhere", {}, None),
    ("DELETE", "/users/", _h("admin"), None),
    ("PATCH", "/users/me", _h("bob"), b'{"email": "not-an-email"}'),
    ("PATCH", "/users/me", _h("bob"), b'{"email": "alice@example.com"}'),
    ("PATCH", "/users/me", _h("bob"), b'{"email": "bobby@example.com", "full_name": null}'),
    ("PATCH", "/users/me", _h("carol"), b'{"email": "x"}'),
    ("POST", "/users/signup", {}, b'{"email": "alice@example.com", "password": "longpassword"}'),
    ("POST", "/users/signup", {}, b'{"email": "new@example.com", "password": "longpassword"}'),
    ("POST", "/users/signup", {}, b'{"email": "a@b", "password": "longpassword"}'),
    ("POST", "/items/", _h("alice"), b'{"title": "lower"}'),
    ("POST", "/items/", _h("alice"), b'{"title": "Upper"}'),
    ("POST", "/items/", _h("alice"), b"{"),
]


@pytest.mark.parametrize(("method", "url", "headers", "body"), RAW)
def test_the_generated_app_answers_like_the_runtime(apps, method, url, headers, body):
    rt, gen = apps
    a = rt.request(method, url, headers=headers, content=body, follow_redirects=False)
    b = gen.request(method, url, headers=headers, content=body, follow_redirects=False)
    assert a.status_code == b.status_code
    assert normalize(a.json()) == normalize(b.json())


def test_the_raw_requests_reach_every_part_b_status(apps):
    rt, _ = apps
    statuses = {
        rt.request(m, u, headers=h, content=b, follow_redirects=False).status_code
        for m, u, h, b in RAW
    }
    assert {200, 201, 400, 401, 403, 404, 405, 409, 422} <= statuses
