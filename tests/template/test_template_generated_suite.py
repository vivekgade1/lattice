"""The template proof re-verified with a *generated* request suite (importer plan, node I2).

The template's routes are described by hand as :class:`~lattice.importer.suite.RouteSpec`
and :class:`~lattice.importer.suite.TableSpec` values (what the extractor will produce);
:func:`~lattice.importer.suite.generate_suite` derives the request suite and the seeded
database (written through the template's own SQLModel classes); and
:func:`~lattice.importer.verify.verify` runs it against the vendored template app and the
hand-written Lattice programs (``tests/fixtures/template/*.lattice.py`` with
``bench/template_profile.py``) served by the Lattice runtime.

Every case must be equal, equal under the one representation rule (the 422 wording), or
a named deviation (``DEVIATIONS``, keyed by case name); the verify result also demands
that every ``raise HTTPException`` in the routes and their auth dependencies is reached
by a case of that route. Differences the generated suite found that the hand-written
suite did not are recorded in ``FINDINGS`` (named, with the reason) and asserted exactly,
like deviations.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from bench.template_app import make_app, sqlite_engine
from bench.template_app.app import utils as template_utils
from bench.template_app.app.api import deps
from bench.template_app.app.api.routes import items, login, users
from bench.template_app.app.core import security
from bench.template_app.app.models import Item, User
from bench.template_profile import NATIVES, create_access_token, template_plans
from lattice.api.runtime import build_app
from lattice.importer.suite import (
    AuthSpec,
    ColumnSpec,
    ErrorSpec,
    GeneratedSuite,
    ParamSpec,
    RouteSpec,
    TableSpec,
    generate_suite,
    sqlmodel_insert_hook,
)
from lattice.importer.verify import Case, Deviation, ValidationEnvelope, VerifyResult, verify

API = "/api/v1"

# ---- the template, described (bench/template_app/app/models.py and api/routes) -------------

C = ColumnSpec
USER = TableSpec(
    "user",
    (
        C("email", "email", unique=True, max_len=255),
        C("is_active", "bool"),
        C("is_superuser", "bool"),
        C("full_name", "str", nullable=True, max_len=255),
        C("id", "uuid", primary_key=True),
        C("hashed_password", "str"),
        C("created_at", "datetime", nullable=True),
    ),
)
ITEM = TableSpec(
    "item",
    (
        C("title", "str", min_len=1, max_len=255),
        C("description", "str", nullable=True, max_len=255),
        C("id", "uuid", primary_key=True),
        C("created_at", "datetime", nullable=True),
        C("owner_id", "uuid", foreign_key="user.id"),
    ),
    owner="owner_id",
)
AUTH = AuthSpec(
    users_table="user",
    login_path=f"{API}/login/access-token",
    hash_password=security.get_password_hash,
    mint_token=create_access_token,
)

P = ParamSpec
ID = P("id", "uuid", "path")
PAGE = (
    P("skip", "int", "query", required=False, default=0),
    P("limit", "int", "query", required=False, default=100),
)
NOT_FOUND = ErrorSpec(404, "Item not found", "not_found")
NOT_OWNER = ErrorSpec(403, "Not enough permissions", "not_owner")
TITLE = P("title", "str", "body", min_len=1, max_len=255)
DESCRIPTION = P("description", "str", "body", required=False, nullable=True, max_len=255)
ROUTES: list[RouteSpec] = [
    RouteSpec("read_items", "GET", f"{API}/items/", "list", "item", params=PAGE,
              pagination=("skip", "limit")),
    RouteSpec("read_item", "GET", f"{API}/items/{{id}}", "read", "item", params=(ID,),
              errors=(NOT_FOUND, NOT_OWNER)),
    RouteSpec("create_item", "POST", f"{API}/items/", "create", "item",
              params=(TITLE, DESCRIPTION)),
    RouteSpec("update_item", "PUT", f"{API}/items/{{id}}", "update", "item",
              params=(ID, P("title", "str", "body", required=False, nullable=True, min_len=1,
                            max_len=255), DESCRIPTION),
              errors=(NOT_FOUND, NOT_OWNER)),
    RouteSpec("delete_item", "DELETE", f"{API}/items/{{id}}", "delete", "item", params=(ID,),
              errors=(NOT_FOUND, NOT_OWNER)),
    RouteSpec("read_users", "GET", f"{API}/users/", "list", "user", auth="admin", params=PAGE,
              pagination=("skip", "limit"),
              errors=(ErrorSpec(403, "The user doesn't have enough privileges", "not_admin"),)),
    RouteSpec("read_user_me", "GET", f"{API}/users/me", "me", "user"),
    RouteSpec("update_user_me", "PATCH", f"{API}/users/me", "update_me", "user",
              params=(P("full_name", "str", "body", required=False, nullable=True,
                        max_len=255),
                      P("email", "email", "body", required=False, nullable=True, max_len=255,
                        column="email")),
              errors=(ErrorSpec(409, "User with this email already exists", "unique",
                                column="email"),)),
    RouteSpec("login_access_token", "POST", f"{API}/login/access-token", "login", "user",
              auth="public", body="form",
              params=(P("username", "str", "body"), P("password", "str", "body")),
              errors=(ErrorSpec(400, "Incorrect email or password", "bad_credentials"),
                      ErrorSpec(400, "Inactive user", "inactive_login"))),
]  # fmt: skip

# The original functions behind each route: the route and its auth dependencies.
USER_DEPS = [deps.get_current_user]
BRANCHES: dict[str, list[Any]] = {
    "read_items": [items.read_items, *USER_DEPS],
    "read_item": [items.read_item, *USER_DEPS],
    "create_item": [items.create_item, *USER_DEPS],
    "update_item": [items.update_item, *USER_DEPS],
    "delete_item": [items.delete_item, *USER_DEPS],
    "read_users": [users.read_users, *USER_DEPS, deps.get_current_active_superuser],
    "read_user_me": [users.read_user_me, *USER_DEPS],
    "update_user_me": [users.update_user_me, *USER_DEPS],
    "login_access_token": [login.login_access_token],
}

RULES = (ValidationEnvelope(),)


def generated() -> GeneratedSuite:
    return generate_suite(
        ROUTES,
        [USER, ITEM],
        AUTH,
        insert_hook=sqlmodel_insert_hook({"user": User, "item": Item}, sqlite_engine),
    )


def runtime_factory(path: Path) -> Any:
    return build_app(template_plans(), path, natives=NATIVES)


# ---- deviations and findings ---------------------------------------------------------------

_UNAUTHORIZED = {"detail": "Not authenticated"}
INVALID_TOKEN = Deviation(
    "invalid-token-401",
    "policy",
    "The template answers an invalid bearer token with 403 'Could not validate "
    "credentials'; Lattice's auth answers every missing or invalid credential with 401.",
    401,
    _UNAUTHORIZED,
)
UNKNOWN_USER = Deviation(
    "unknown-user-token",
    "policy",
    "A valid token naming a user that does not exist: the template answers 404 'User not "
    "found'; Lattice's NativeToken auth answers 401 like any token naming no caller.",
    401,
    _UNAUTHORIZED,
)


def _page_deviation(name: str, loc: str, msg: str) -> Deviation:
    return Deviation(
        name,
        "policy",
        "Lattice requires a bounded, non-negative page window (limit: Int(ge=0, le=100), "
        "skip: Int(ge=0)); the template passes any integer to SQL (SQLite reads LIMIT -1 "
        "as no limit and a negative OFFSET as 0), so it answers 200 with every row.",
        422,
        {"detail": [{"loc": ["query", loc], "msg": msg}]},
    )


BOUNDED_LIMIT = _page_deviation("bounded-page-size", "limit", "must be <= 100")
NEGATIVE_LIMIT = _page_deviation("non-negative-page-window", "limit", "must be >= 0")
NEGATIVE_SKIP = _page_deviation("non-negative-page-window", "skip", "must be >= 0")


def _null_deviation(field: str) -> Deviation:
    return Deviation(
        "null-into-required-column",
        "policy",
        "An explicit null for a field the table stores as NOT NULL (ItemUpdate.title and "
        "UserUpdateMe.email are Optional): the template writes it and crashes with 500 "
        "(IntegrityError); Lattice refuses it with 422 and writes nothing.",
        422,
        {"detail": [{"loc": ["body", field], "msg": "must not be null"}]},
    )


NOT_NULL = {("update_item", "title"), ("update_user_me", "email")}


def deviations_for(cases: list[Case]) -> dict[str, Deviation]:
    """The declared deviations, by case: every case of a kind, not a hand-picked list (so
    a hypothesis body that happens to send a null is covered by the same rule)."""
    out: dict[str, Deviation] = {}
    for c in cases:
        what = c.name.removeprefix(f"{c.route}.")
        params = c.params or {}
        nulls = [k for k, v in (c.json or {}).items() if v is None and (c.route, k) in NOT_NULL]
        if what == "bad_token":
            out[c.name] = INVALID_TOKEN
        elif what == "unknown_user_token":
            out[c.name] = UNKNOWN_USER
        elif params.get("limit") == 1_000_000:
            out[c.name] = BOUNDED_LIMIT
        elif params.get("limit") == -1:
            out[c.name] = NEGATIVE_LIMIT
        elif params.get("skip") == -1:
            out[c.name] = NEGATIVE_SKIP
        elif nulls:
            out[c.name] = _null_deviation(nulls[0])
    return out


# Mismatches the generated suite found that are *not* policy: open findings against the
# template proof, asserted exactly so that fixing one (or finding another) fails the test.
FINDINGS: dict[str, str] = {}
# Closed: "update_user_me.boundary.email.max" -- Lattice's Email accepted a 255-character
# address that pydantic's EmailStr refuses (RFC 5321: 254 at most). Email now caps every
# address at 254 characters (lattice.catalog.EMAIL_MAX_LEN), so both answer 422.


@pytest.fixture(scope="module")
def suite() -> GeneratedSuite:
    return generated()


@pytest.fixture(scope="module")
def result(suite: GeneratedSuite) -> VerifyResult:
    template_utils.OUTBOX.clear()
    return verify(
        make_app,
        runtime_factory,
        suite.db_factory,
        suite.cases,
        RULES,
        deviations_for(suite.cases),
        login=suite.login,
        branches=BRANCHES,
    )


# ---- the suite ----------------------------------------------------------------------------

EXPECTED_SIZES = {
    "read_items": 23,
    "read_item": 10,
    "create_item": 20,
    "update_item": 25,
    "delete_item": 9,
    "read_users": 15,
    "read_user_me": 7,
    "update_user_me": 22,
    "login_access_token": 7,
}


def test_the_generated_suite_covers_every_route(suite):
    sizes = Counter(c.route for c in suite.cases)
    assert dict(sizes) == EXPECTED_SIZES
    assert suite.cases == generated().cases  # deterministic, hypothesis bodies included


def test_the_seed_has_nulls_and_values_through_the_templates_models(suite, tmp_path):
    db = suite.db_factory("seed", tmp_path / "seed.db")
    conn = sqlite3.connect(db)
    try:
        for table, column in (("item", "created_at"), ("item", "description"),
                              ("user", "full_name"), ("user", "created_at")):  # fmt: skip
            values = [r[0] for r in conn.execute(f'SELECT {column} FROM "{table}"')]
            assert None in values and any(v is not None for v in values), (table, column)
        flags = set(conn.execute('SELECT is_active, is_superuser FROM "user"'))
        assert flags == {(1, 1), (1, 0), (0, 0)}
        owners = {r[0] for r in conn.execute("SELECT owner_id FROM item")}
        assert len(owners) == 4 and all(len(o) == 32 for o in owners)  # hex32, the model's
    finally:
        conn.close()


# ---- the verdict --------------------------------------------------------------------------


def test_every_case_is_equal_a_named_deviation_or_a_recorded_finding(result):
    mismatches = set(result.mismatches)
    assert mismatches == set(FINDINGS), result.render()
    assert result.unknown_deviations == ()
    assert result.unneeded_rules == ()


def test_the_tally(result, suite):
    assert result.tally() == (91, 18, 29, 0)
    kinds = Counter(o.deviation for o in result.outcomes.values() if o.kind == "deviation")
    assert kinds == {
        "invalid-token-401": 8,
        "unknown-user-token": 8,
        "bounded-page-size": 3,
        "non-negative-page-window": 6,
        "null-into-required-column": 4,
    }
    assert all(d.kind == "policy" for d in deviations_for(suite.cases).values())


def test_the_closed_email_finding_is_equal_under_the_validation_rule(result):
    name = "update_user_me.boundary.email.max"
    original, lattice = result.original[name], result.lattice[name]
    assert original.status == 422 and "too long" in original.body["detail"][0]["msg"]
    assert lattice.status == 422
    assert lattice.body == {
        "detail": [{"loc": ["body", "email"], "msg": "must have at most 254 characters"}]
    }
    assert result.outcomes[name].kind == "equal_under_rule"


def test_every_raise_site_of_the_template_routes_is_hit(result):
    assert result.unverified == ()
    # the routes' own raise sites and their auth dependencies', each reached
    reached = {(s.function, s.status, s.detail) for s, hits in result.hits.items() if hits}
    assert reached == {
        ("read_item", 404, "Item not found"),
        ("read_item", 403, "Not enough permissions"),
        ("update_item", 404, "Item not found"),
        ("update_item", 403, "Not enough permissions"),
        ("delete_item", 404, "Item not found"),
        ("delete_item", 403, "Not enough permissions"),
        ("update_user_me", 409, "User with this email already exists"),
        ("login_access_token", 400, "Incorrect email or password"),
        ("login_access_token", 400, "Inactive user"),
        ("get_current_user", 403, "Could not validate credentials"),
        ("get_current_user", 404, "User not found"),
        ("get_current_user", 400, "Inactive user"),
        ("get_current_active_superuser", 403, "The user doesn't have enough privileges"),
    }
    assert all(result.hits.values())


def test_a_suite_without_the_error_cases_leaves_branches_unverified(suite):
    """Review focus #3: drop the cases that reach the ownership 403 and the verdict
    names that branch as unverified, instead of calling the route equal."""
    cases = [c for c in suite.cases if c.route == "read_item" and "other_user_row" not in c.name]
    cases = [c for c in cases if c.name != "read_item.error.403.not_owner"]
    template_utils.OUTBOX.clear()
    partial = verify(
        make_app,
        runtime_factory,
        suite.db_factory,
        cases,
        RULES,
        deviations_for(cases),
        login=suite.login,
        branches={"read_item": BRANCHES["read_item"]},
    )
    assert [(r, s.function, s.status) for r, s in partial.unverified] == [
        ("read_item", "read_item", 403)
    ]
    assert not partial.ok
