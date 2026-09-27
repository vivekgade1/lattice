"""The template proof (spec 2026-09-27 realapps §4): hand-written Lattice reproduces the
FastAPI template's items, users (me / update_me / admin list) and login routes.

The request suite ``bench/template_suite.py`` (53 cases) plus ``EXTRA_CASES`` (an
inactive user and a token for a user that does not exist) runs against

* the vendored template app (``bench/template_app``, live, not the stored golden),
* the Lattice runtime serving ``tests/fixtures/template/*.lattice.py`` with
  ``bench/template_profile.py``, and
* the FastAPI app generated from the same program,

each case on a fresh copy of the same seeded SQLite database (the template's own schema
and spellings: the profile stores Uuids as 32 hex digits and DateTimes as naive-UTC
``"YYYY-MM-DD HH:MM:SS.ffffff"``). Every record is status + JSON body + the affected
tables after the request, with generated UUIDs, timestamps and tokens as placeholders.

Comparison, per case:

* **Generated app vs runtime:** identical records, every case.
* **Lattice vs template:** identical status and DB effects, and an identical body up to
  three named, asserted *representation rules* (``DATETIME_WIRE``, ``ERROR_ENVELOPE``,
  ``VALIDATION_ENVELOPE``) -- unless the case is listed in ``DEVIATIONS``, where the
  Lattice status, body and DB outcome are asserted exactly, together with the fact
  that they really differ from the template's. Nothing is skipped or tolerated
  silently: a case that starts matching (or diverging) fails the test.
"""

from __future__ import annotations

import importlib
import re
import shutil
import sqlite3
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from bench.template_app import make_app
from bench.template_app.app import utils as template_utils
from bench.template_app.seed import TEMPLATE_USERS, build_template_db
from bench.template_profile import (
    NATIVES,
    create_access_token,
    template_plans,
    template_profile,
    template_program,
)
from bench.template_suite import (
    CAROL_ITEM,
    MISSING_ID,
    TEMPLATE_SUITE,
    CaseRecord,
    TemplateCase,
    datetime_spelling,
    run_template_suite,
)
from lattice.api.backend import datetime_text, parse_datetime
from lattice.api.codegen import compile_source_to_fastapi
from lattice.api.runtime import UNIQUE_CONFLICT, build_app

REPO = Path(__file__).resolve().parents[2]
KIT_PATH = REPO / "bench" / "api_kit" / "platform_kit.py"
GENERATED = "generated_template_app"

# ---- extra cases (not in the golden suite) ------------------------------------------------
#
# Run on a variant of the seed where carol is inactive. Tokens are minted with the
# template's own create_access_token (carol cannot log in).

INACTIVE = "carol"
_C = TemplateCase
_carol_token = create_access_token(TEMPLATE_USERS[INACTIVE][2])
_ghost_token = create_access_token(uuid.UUID(MISSING_ID))
EXTRA_CASES: list[TemplateCase] = [
    _C("inactive_login", "POST", "/login/access-token", tables=("user",),
       form={"username": TEMPLATE_USERS[INACTIVE][0], "password": TEMPLATE_USERS[INACTIVE][1]}),
    _C("inactive_items_list", "GET", "/items/", token=_carol_token),
    _C("inactive_item_read", "GET", f"/items/{CAROL_ITEM}", token=_carol_token),
    _C("inactive_item_delete", "DELETE", f"/items/{CAROL_ITEM}", token=_carol_token),
    _C("inactive_users_me", "GET", "/users/me", token=_carol_token, tables=("user",)),
    _C("inactive_update_me", "PATCH", "/users/me", token=_carol_token, tables=("user",),
       json={"full_name": "Not Allowed"}),
    _C("token_for_unknown_user", "GET", "/users/me", token=_ghost_token, tables=("user",)),
    _C("inactive_users_list", "GET", "/users/", token=_carol_token, tables=("user",)),
    _C("inactive_items_list_bad_limit", "GET", "/items/", token=_carol_token,
       params={"limit": "many"}),
]  # fmt: skip

# ---- representation rules ------------------------------------------------------------------

DATETIME_WIRE = "DATETIME_WIRE"
ERROR_ENVELOPE = "ERROR_ENVELOPE"
VALIDATION_ENVELOPE = "VALIDATION_ENVELOPE"
RULES: dict[str, str] = {
    DATETIME_WIRE: (
        "A DateTime is the same instant, spelled differently. Lattice always answers UTC "
        "ISO-8601 with microseconds and 'Z' ('2026-02-02T06:00:00.000000Z'); the template "
        "on SQLite answers the naive stored value with pydantic's shortest fraction "
        "('2026-02-02T06:00:00') because SQLite keeps no zone (on Postgres it would say "
        "'...:00Z'). The rule maps each template datetime to Lattice's spelling of it as "
        "UTC, and a generated one's placeholder to Lattice's spelling class "
        "('<datetime:1:T>' -> '<datetime:1:TZ>'); in the database the spellings must "
        "match."
    ),
    ERROR_ENVELOPE: (
        "Same status and the same detail message; Lattice's declared errors add the error "
        'code: {"error": "not_found", "detail": "Item not found"} where FastAPI\'s '
        'HTTPException answers {"detail": "Item not found"}.'
    ),
    VALIDATION_ENVELOPE: (
        "Both answer 422 flagging the same fields (the same loc lists, in order); the "
        'envelope and wording differ: Lattice {"error": "validation", "detail": [{"loc", '
        '"msg"}]}, pydantic {"detail": [{"type", "loc", "msg", "input", "ctx"}]}.'
    ),
}
ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
}
_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_DATETIME_PLACEHOLDER = re.compile(r"<datetime:(\d+):[^>]*>")
_WIRE_SPELLING = datetime_spelling(datetime_text(datetime(2000, 1, 1)))  # "TZ"


def _wire_datetimes(value: Any) -> Any:
    """DATETIME_WIRE: every (seeded) datetime string re-spelled as Lattice answers it."""
    if isinstance(value, dict):
        return {k: _wire_datetimes(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_wire_datetimes(v) for v in value]
    if isinstance(value, str) and _DATETIME.fullmatch(value):
        parsed = parse_datetime(value)
        assert parsed is not None
        return datetime_text(parsed)
    if isinstance(value, str) and (m := _DATETIME_PLACEHOLDER.fullmatch(value)):
        return f"<datetime:{m.group(1)}:{_WIRE_SPELLING}>"
    return value


def agreement(template: CaseRecord, lattice: CaseRecord) -> tuple[str, ...]:
    """The representation rules under which `lattice` reproduces `template` (empty: an
    exact match). AssertionError when it does not reproduce it."""
    assert lattice.status == template.status, (template.status, lattice.status)
    assert lattice.db == template.db, "database effects differ"
    t, b = template.body, lattice.body
    if template.status == 422:
        assert isinstance(b, dict) and set(b) == {"error", "detail"}
        assert b["error"] == "validation"
        assert [d["loc"] for d in b["detail"]] == [d["loc"] for d in t["detail"]]
        return (VALIDATION_ENVELOPE,)
    if template.status >= 400 and isinstance(t, dict) and set(t) == {"detail"}:
        assert b == {"error": ERROR_CODES[template.status], "detail": t["detail"]}, b
        return (ERROR_ENVELOPE,)
    expected = _wire_datetimes(t)
    assert b == expected
    return (DATETIME_WIRE,) if expected != t else ()


# ---- deviations ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Deviation:
    """An intentional difference or a known language gap: what Lattice answers instead."""

    id: str
    kind: str  # "policy" (Lattice's contract differs on purpose) | "gap" (not expressible)
    reason: str
    status: int  # Lattice's status
    body: Any  # Lattice's (normalized) body
    db_same: bool = True  # Lattice's database effects equal the template's


_UNAUTHORIZED = {"error": "unauthorized"}
_BOUNDED_LIMIT = Deviation(
    "bounded-page-size",
    "policy",
    "The template takes an unbounded `limit` (limit=1000000 returns every row). Lattice "
    "requires a page size bound (L831: an Int parameter with le <= 100), so the "
    "programs declare limit: Int(ge=0, le=100) = 100 and a larger limit is a 422.",
    422,
    {"error": "validation", "detail": [{"loc": ["query", "limit"], "msg": "must be <= 100"}]},
)
_BAD_TOKEN = Deviation(
    "invalid-token-401",
    "policy",
    "The template answers an invalid bearer token with 403 'Could not validate "
    "credentials'; Lattice's auth answers every missing or invalid credential with 401 "
    "(RFC 6750 invalid_token), keeping 403 for a caller without the required role.",
    401,
    _UNAUTHORIZED,
)
_NO_TOKEN = Deviation(
    "automatic-401-body",
    "policy",
    "Same status. Lattice's automatic 401 body is {'error': 'unauthorized'} without a "
    "detail; the template's OAuth2PasswordBearer says {'detail': 'Not authenticated'}.",
    401,
    _UNAUTHORIZED,
)
DEVIATIONS: dict[str, Deviation] = {
    "items_list_unbounded_limit": _BOUNDED_LIMIT,
    "users_list_unbounded_limit": _BOUNDED_LIMIT,
    "items_list_bad_token": _BAD_TOKEN,
    "users_me_bad_token": _BAD_TOKEN,
    "items_list_no_token": _NO_TOKEN,
    "users_me_no_token": _NO_TOKEN,
    "users_list_normal_user": Deviation(
        "automatic-403-body",
        "policy",
        "Same status. Lattice's automatic role check (auth=Admin) answers "
        "{'error': 'forbidden'} without a detail; the template's "
        "get_current_active_superuser says 'The user doesn't have enough privileges'.",
        403,
        {"error": "forbidden"},
    ),
    "token_for_unknown_user": Deviation(
        "unknown-user-token",
        "policy",
        "A valid token naming a user that does not exist: the template answers 404 'User "
        "not found' (from get_current_user); Lattice's NativeToken auth answers 401 like "
        "any token that does not identify a caller.",
        401,
        _UNAUTHORIZED,
    ),
    "users_update_me_email_taken": Deviation(
        "email-conflict-detail",
        "gap",
        "Same status (409) and database (unchanged). The template checks `if "
        "user_in.email:` for another user with that email and answers 409 'User with "
        "this email already exists'. Lattice cannot express this check: `email` is an "
        "UNSET-able partial-update parameter and any expression over it is L404, so the "
        "program cannot compare it with other rows before the update. The UPDATE hits "
        "the UNIQUE index instead, which Lattice answers with a generic 409 (rolled back) "
        "whose detail names no table or column -- so not the template's message.",
        409,
        {"error": "conflict", "detail": UNIQUE_CONFLICT},
    ),
    "inactive_users_list": Deviation(
        "inactive-before-role",
        "gap",
        "An inactive non-superuser lists users: the template's get_current_active_user "
        "rejects the caller as inactive (400 'Inactive user') before the superuser check. "
        "Lattice has no auth-level active predicate: `is_active` is checked by a require() "
        "in the program body, which runs after the automatic auth=Admin role check, so "
        "the caller gets that check's 403. (An auth-level active predicate is planned "
        "for sub-project 1b.)",
        403,
        {"error": "forbidden"},
    ),
    "inactive_items_list_bad_limit": Deviation(
        "inactive-before-validation",
        "gap",
        "An inactive user sends an invalid parameter (limit=many): the template resolves "
        "the active-user dependency first and answers 400 'Inactive user'. In Lattice "
        "the active check is a require() in the program body, which runs only after the "
        "parameters validate, so the caller gets 422. Lattice has no auth-level active "
        "predicate (planned for sub-project 1b).",
        422,
        {
            "error": "validation",
            "detail": [{"loc": ["query", "limit"], "msg": "must be an integer"}],
        },
    ),
    "users_update_me_bad_email": Deviation(
        "email-format",
        "gap",
        "The template's UserUpdateMe.email is a pydantic EmailStr (422 for 'not-an-email'). "
        "Lattice has no email (or pattern) parameter type, so the value is stored as given.",
        200,
        {
            "email": "not-an-email",
            "is_active": True,
            "is_superuser": False,
            "full_name": "Carol Chen",
            "id": "b33cbfa5-3598-5545-b110-d26f8f619d1e",
            "created_at": "2026-01-04T09:00:00.000000Z",
        },
        db_same=False,
    ),
}

ALL_CASES = [*TEMPLATE_SUITE, *EXTRA_CASES]
CASE_NAMES = [c.name for c in ALL_CASES]

# The expected tally over the 53 suite cases and the 9 extra ones (documented in
# docs/results/2026-09-26-bench-smoke.md): exact matches, matches that need a
# representation rule, and deviations.
EXPECTED_TALLY = {"suite": (6, 38, 9), "extra": (0, 6, 3)}

# ---- fixtures -----------------------------------------------------------------------------


def _inactive_variant(seed: Path, path: Path) -> Path:
    shutil.copyfile(seed, path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            'UPDATE "user" SET is_active = 0 WHERE email = ?', (TEMPLATE_USERS[INACTIVE][0],)
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _run(factory: Any, seed: Path, inactive: Path) -> dict[str, CaseRecord]:
    records = run_template_suite(factory, seed, TEMPLATE_SUITE)
    records += run_template_suite(factory, inactive, EXTRA_CASES)
    return {r.name: r for r in records}


@pytest.fixture(scope="module")
def dbs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("template-diff")
    seed = build_template_db(root / "seed.db")
    return seed, _inactive_variant(seed, root / "inactive.db")


@pytest.fixture(scope="module")
def template_records(dbs) -> dict[str, CaseRecord]:
    template_utils.OUTBOX.clear()
    return _run(make_app, *dbs)


@pytest.fixture(scope="module")
def runtime_records(dbs) -> dict[str, CaseRecord]:
    plans = template_plans()
    return _run(lambda path: build_app(plans, path, natives=NATIVES), *dbs)


@pytest.fixture(scope="module")
def generated_records(dbs, tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict]:
    result, code = compile_source_to_fastapi(
        template_program(), template_profile(), natives_module="bench.template_profile"
    )
    assert code is not None, [d.render() for d in result.diagnostics]
    gen_dir = tmp_path_factory.mktemp("template-gen")
    shutil.copyfile(KIT_PATH, gen_dir / "platform_kit.py")
    (gen_dir / f"{GENERATED}.py").write_text(code)
    for name in ("platform_kit", GENERATED):
        sys.modules.pop(name, None)
    sys.path.insert(0, str(gen_dir))
    try:
        module = importlib.import_module(GENERATED)
        kit = sys.modules["platform_kit"]

        def factory(path: Path) -> Any:
            kit.DB_FILENAME = str(path)  # the kit's get_db opens this file per request
            return module.app

        yield _run(factory, *dbs)
    finally:
        sys.path.remove(str(gen_dir))
        for name in ("platform_kit", GENERATED):
            sys.modules.pop(name, None)


# ---- the program ---------------------------------------------------------------------------


def test_the_program_is_accepted_with_only_the_expected_warnings():
    from lattice.check import check_source

    result = check_source(template_program(), template_profile())
    assert result.program is not None
    codes = sorted(d.code for d in result.diagnostics)
    # L823 x3: require(..., owner_is(user)) answers 403 for another user's item -- the
    # template's contract (a policy finding: 403 discloses that the id exists).
    # L833: create_item answers 200, not 201 -- again the template's contract.
    assert codes == ["L823", "L823", "L823", "L833"]


@pytest.mark.parametrize("column", ["is_superuser", "is_active"])
def test_a_customer_cannot_update_their_own_admin_columns(column):
    from lattice.check import check_source

    src = template_program().replace(
        "update(user, full_name=full_name, email=email)",
        f"update(user, full_name=full_name, email=email, {column}=True)",
    )
    assert src != template_program()
    result = check_source(src, template_profile())
    errors = [d for d in result.diagnostics if d.code == "L802"]
    assert len(errors) == 1 and column in errors[0].message


# ---- Lattice vs template -------------------------------------------------------------------


@pytest.mark.parametrize("name", CASE_NAMES)
def test_lattice_reproduces_the_template(name, template_records, runtime_records):
    t, lat = template_records[name], runtime_records[name]
    dev = DEVIATIONS.get(name)
    if dev is None:
        agreement(t, lat)
        return
    assert (lat.status, lat.body) == (dev.status, dev.body)
    assert (lat.db == t.db) is dev.db_same
    with pytest.raises(AssertionError):  # the deviation is real
        agreement(t, lat)


def test_the_tally(template_records, runtime_records):
    used: set[str] = set()
    tally = {}
    for group, cases in (("suite", TEMPLATE_SUITE), ("extra", EXTRA_CASES)):
        exact = under_rules = deviations = 0
        for case in cases:
            if case.name in DEVIATIONS:
                deviations += 1
                continue
            rules = agreement(template_records[case.name], runtime_records[case.name])
            used.update(rules)
            exact += not rules
            under_rules += bool(rules)
        tally[group] = (exact, under_rules, deviations)
    assert tally == EXPECTED_TALLY
    assert used == set(RULES)  # every rule is needed by some case


def test_every_deviation_names_a_case():
    assert set(DEVIATIONS) <= set(CASE_NAMES)
    assert len(set(CASE_NAMES)) == len(CASE_NAMES)
    assert all(d.kind in ("policy", "gap") and d.reason for d in DEVIATIONS.values())


def test_a_write_in_another_spelling_is_caught(dbs, template_records):
    """The placeholders keep each generated value's spelling: Lattice storing the new
    item's created_at as its canonical "...T...Z" in this "YYYY-MM-DD HH:MM:SS" database
    answers the same status and body but fails on the database effects."""
    from dataclasses import replace

    from lattice.api.ir import lower_api
    from lattice.check import check_source

    profile = template_profile()
    assert profile.api is not None
    iso = replace(profile, api=replace(profile.api, datetime_storage="iso_z"))
    result = check_source(template_program(), iso)
    assert result.program is not None
    plans = lower_api(result.program)
    case = next(c for c in TEMPLATE_SUITE if c.name == "item_create")
    (record,) = run_template_suite(lambda p: build_app(plans, p, natives=NATIVES), dbs[0], [case])
    template = template_records["item_create"]
    assert record.status == template.status
    with pytest.raises(AssertionError, match="database effects differ"):
        agreement(template, record)


# ---- generated FastAPI app vs runtime ------------------------------------------------------


@pytest.mark.parametrize("name", CASE_NAMES)
def test_the_generated_app_answers_like_the_runtime(name, runtime_records, generated_records):
    assert generated_records[name].to_dict() == runtime_records[name].to_dict()
