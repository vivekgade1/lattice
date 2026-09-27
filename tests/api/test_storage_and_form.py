"""Two real-app additions made for the template proof (realapps spec §4):

* **Uuid / DateTime storage spellings** (`ApiProfile.uuid_storage` = "canonical" |
  "hex32", `ApiProfile.datetime_storage` = "iso_z" | "sql_space"): a program can run on
  a database another stack wrote (SQLAlchemy on SQLite stores `CHAR(32)` hex ids and
  naive-UTC "YYYY-MM-DD HH:MM:SS.ffffff" datetimes). Only the stored spelling changes:
  the real-apps program answers every request identically on a canonical database and
  on the same data stored the other way, the runtime and the generated FastAPI app
  agree, and the rows it writes use the configured spellings.
* **Form-encoded bodies** (`@endpoint(..., body="form")`): body parameters arrive as
  URL-encoded form fields (an OAuth2 password login), are converted like query text,
  and are validated by the checker (L601) and served identically by the runtime and
  the generated app.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib
import shutil
import sqlite3
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from lattice.api.backend import (
    SQLITE,
    SqliteDialect,
    datetime_text,
    dialect_for,
    parse_datetime,
    stored_datetime,
    stored_uuid,
    uuid_text,
)
from lattice.api.codegen import generate_fastapi
from lattice.api.ir import EndpointPlan, lower_api
from lattice.api.runtime import build_app, form_fields
from lattice.check import check_source
from lattice.profile import ApiProfile, Profile
from lattice.typesys import Scalar
from tests.api.realapps import API, NATIVES, NATIVES_FILE, PROFILE, PROGRAM, TOKENS, build_db, uid
from tests.api.test_codegen_differential import normalize
from tests.api.test_realapps_codegen import RAW

KIT_PATH = Path(__file__).resolve().parents[2] / "bench" / "api_kit" / "platform_kit.py"
SPACED = dataclasses.replace(API, uuid_storage="hex32", datetime_storage="sql_space")
SPACED_PROFILE = Profile(schemas={}, api=SPACED)
INSTANT = dt.datetime(2026, 2, 1, 12, 30, 5, 7, tzinfo=dt.UTC)
U = uuid.UUID("5B3E9C1C-0000-4000-8000-00000000ABCD")


def _plans(source: str, profile: Profile) -> tuple[EndpointPlan, ...]:
    check = check_source(source, profile)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    return lower_api(check.program)


# ==== storage: values ===============================================================


def test_stored_spellings():
    assert stored_uuid(U) == "5b3e9c1c-0000-4000-8000-00000000abcd"
    assert stored_uuid(str(U).replace("-", ""), "hex32") == "5b3e9c1c000040008000" + "00000000abcd"
    assert stored_uuid("not-a-uuid", "hex32") is None
    assert stored_datetime(INSTANT) == "2026-02-01T12:30:05.000007Z"
    assert stored_datetime(INSTANT, "sql_space") == "2026-02-01 12:30:05.000007"
    # a naive value is UTC; an offset is converted
    east = INSTANT.astimezone(dt.timezone(dt.timedelta(hours=2)))
    assert stored_datetime(east, "sql_space") == "2026-02-01 12:30:05.000007"
    assert (
        stored_datetime(INSTANT.replace(tzinfo=None), "sql_space") == "2026-02-01 12:30:05.000007"
    )
    # both spellings read back as the same canonical value
    assert datetime_text(parse_datetime("2026-02-01 12:30:05.000007")) == datetime_text(INSTANT)
    assert uuid_text("5b3e9c1c00004000800000000000abcd") == str(U)


def test_the_dialect_stores_values_in_its_spellings():
    d = SqliteDialect("hex32", "sql_space")
    assert d.to_db(U) == "5b3e9c1c0000400080000000" + "0000abcd"
    assert d.to_db(INSTANT) == "2026-02-01 12:30:05.000007"
    assert d.to_db(True) == 1 and d.to_db(dt.date(2026, 2, 1)) == "2026-02-01"
    # stored values decode to the canonical spellings (responses do not change)
    assert d.from_db("5b3e9c1c00004000800000000000abcd", Scalar.UUID) == str(U)
    assert d.from_db("2026-02-01 12:30:05.000007", Scalar.DATETIME) == datetime_text(INSTANT)
    # a native's Uuid / DateTime result is stored in the dialect's spellings
    assert d.native_result("n", U, Scalar.UUID) == "5b3e9c1c00004000800000000000abcd"
    assert d.native_result("n", INSTANT, Scalar.DATETIME) == "2026-02-01 12:30:05.000007"
    assert SQLITE.native_result("n", U, Scalar.UUID) == str(U)


def test_the_dialects_sql_functions_use_its_spellings(tmp_path: Path):
    d = SqliteDialect("hex32", "sql_space")
    conn = d.connect(tmp_path / "x.db")
    try:
        new, parsed, stamp = conn.execute(
            "SELECT lattice_uuid4(), lattice_to_uuid(?), lattice_to_datetime(?, ?)",
            (str(U), "2026-02-01 12:30", "%Y-%m-%d %H:%M"),
        ).fetchone()
    finally:
        conn.close()
    assert len(new) == 32 and uuid.UUID(new).version == 4
    assert parsed == "5b3e9c1c00004000800000000000abcd"
    assert stamp == "2026-02-01 12:30:00.000000"


def test_dialect_for_resolves_storage():
    assert dialect_for("sqlite") is SQLITE
    assert dialect_for("sqlite", uuid_storage="canonical", datetime_storage="iso_z") is SQLITE
    spaced = dialect_for("sqlite", uuid_storage="hex32", datetime_storage="sql_space")
    assert (spaced.uuid_storage, spaced.datetime_storage) == ("hex32", "sql_space")
    assert spaced is dialect_for("sqlite", uuid_storage="hex32", datetime_storage="sql_space")
    assert spaced.name == "sqlite" and spaced.capabilities == SQLITE.capabilities
    assert "hex32" in repr(spaced) and repr(SQLITE) == "<dialect sqlite>"
    with pytest.raises(ValueError, match="uuid_storage"):
        SqliteDialect("binary")
    with pytest.raises(ValueError, match="datetime_storage"):
        SqliteDialect("canonical", "epoch")


@pytest.mark.parametrize(
    ("field", "value"), [("uuid_storage", "binary"), ("datetime_storage", "epoch")]
)
def test_the_profile_validates_storage(field: str, value: str):
    with pytest.raises(ValueError, match=field):
        dataclasses.replace(API, **{field: value})


def test_plans_carry_the_storage_and_literals_are_stored_in_it():
    canonical = {p.name: p for p in _plans(PROGRAM, PROFILE)}
    spaced = {p.name: p for p in _plans(PROGRAM, SPACED_PROFILE)}
    assert {(p.uuid_storage, p.datetime_storage) for p in spaced.values()} == {
        ("hex32", "sql_space")
    }
    assert {(p.uuid_storage, p.datetime_storage) for p in canonical.values()} == {
        ("canonical", "iso_z")
    }
    # a DateTime parameter default is bound in the storage spelling at request time;
    # the SQL text itself does not depend on the storage
    for name, plan in canonical.items():
        assert [getattr(s, "sql", None) and s.sql.text for s in plan.steps] == [
            getattr(s, "sql", None) and s.sql.text for s in spaced[name].steps
        ]


# ==== storage: the real-apps program on the other spellings ============================


def respell(src: Path, dst: Path, api: ApiProfile) -> Path:
    """A copy of the canonical database `src` with Uuid/DateTime values in `api`'s
    storage spellings."""
    shutil.copyfile(src, dst)
    d = dialect_for(
        api.backend, uuid_storage=api.uuid_storage, datetime_storage=api.datetime_storage
    )
    conn = sqlite3.connect(dst)
    try:
        for schema_name, schema in api.tables.items():
            table = api.table_names[schema_name]
            for col, scalar in schema.columns:
                if scalar is Scalar.UUID:
                    conv = lambda v: d.to_db(uuid.UUID(v))  # noqa: E731
                elif scalar is Scalar.DATETIME:
                    conv = lambda v: d.to_db(parse_datetime(v))  # noqa: E731
                else:
                    continue
                rows = conn.execute(f'SELECT rowid, "{col}" FROM "{table}"').fetchall()
                for rowid, v in rows:
                    if v is not None:
                        conn.execute(
                            f'UPDATE "{table}" SET "{col}" = ? WHERE rowid = ?', (conv(v), rowid)
                        )
        conn.commit()
    finally:
        conn.close()
    return dst


def canonical_rows(path: Path, api: ApiProfile) -> dict[str, list[list[object]]]:
    """Every table's rows with Uuid/DateTime values in the canonical spellings."""
    conn = sqlite3.connect(path)
    try:
        out = {}
        for schema_name, schema in api.tables.items():
            table = api.table_names[schema_name]
            names = [c for c, _ in schema.columns]
            types = dict(schema.columns)
            cols = ", ".join(f'"{c}"' for c in names)
            rows = conn.execute(f'SELECT {cols} FROM "{table}" ORDER BY rowid').fetchall()
            out[table] = [
                [SQLITE.from_db(v, types[c]) for c, v in zip(names, r, strict=True)] for r in rows
            ]
        return out
    finally:
        conn.close()


def _h(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[who]}"}


WRITES = [
    ("POST", "/items/", _h("bob"), b'{"title": "fresh", "description": "d"}'),
    ("PUT", f"/items/{uid(101)}", _h("alice"), b'{"title": "renamed"}'),
    ("PUT", f"/items/{uid(103)}", _h("alice"), b'{"title": "not mine"}'),
    ("DELETE", f"/items/{uid(102)}", _h("alice"), None),
    ("PATCH", "/users/me", _h("bob"), b'{"full_name": "Bob B"}'),
    ("POST", "/users/signup", {}, b'{"email": "dan@example.com", "password": "dan-pass-1"}'),
    ("GET", f"/items/{uid(101).replace('-', '')}", _h("alice"), None),  # 32-hex path id
    ("GET", "/admin/alice-items", _h("admin"), None),  # fn.to_uuid / fn.to_datetime literals
    ("GET", "/items/since?after=2000-01-03%2010:00:00", _h("alice"), None),
]


@pytest.fixture(scope="module")
def canonical_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("storage") / "canonical.db"
    build_db(path)
    return path


@pytest.mark.parametrize(("method", "url", "headers", "body"), RAW + WRITES)
def test_storage_does_not_change_what_the_program_answers(
    canonical_db: Path, tmp_path: Path, method, url, headers, body
):
    a_db = tmp_path / "a.db"
    shutil.copyfile(canonical_db, a_db)
    b_db = respell(canonical_db, tmp_path / "b.db", SPACED)
    with (
        TestClient(build_app(_plans(PROGRAM, PROFILE), a_db, natives=NATIVES)) as a,
        TestClient(build_app(_plans(PROGRAM, SPACED_PROFILE), b_db, natives=NATIVES)) as b,
    ):
        ra = a.request(method, url, headers=headers, content=body, follow_redirects=False)
        rb = b.request(method, url, headers=headers, content=body, follow_redirects=False)
    assert ra.status_code == rb.status_code
    if ra.content and ra.headers.get("content-type") == "application/json":
        assert normalize(ra.json()) == normalize(rb.json())
    else:
        assert ra.content == rb.content
    assert normalize(canonical_rows(a_db, API)) == normalize(canonical_rows(b_db, SPACED))


def test_written_rows_use_the_storage_spellings(canonical_db: Path, tmp_path: Path):
    db = respell(canonical_db, tmp_path / "b.db", SPACED)
    with TestClient(build_app(_plans(PROGRAM, SPACED_PROFILE), db, natives=NATIVES)) as c:
        made = c.post("/items/", headers=_h("bob"), json={"title": "fresh"})
    assert made.status_code == 201
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT id, owner_id, created_at FROM item WHERE title = 'fresh'"
        ).fetchone()
    finally:
        conn.close()
    new_id, owner, created = row
    assert len(new_id) == 32 and uuid_text(new_id) == made.json()["id"]
    assert owner == uid(3).replace("-", "")  # bob, as the auth native decoded him
    assert created[10] == " " and not created.endswith("Z")
    assert datetime_text(parse_datetime(created)) == made.json()["created_at"]


@pytest.fixture
def spaced_apps(tmp_path: Path, canonical_db: Path, monkeypatch) -> Iterator[tuple]:
    plans = _plans(PROGRAM, SPACED_PROFILE)
    gen_dir = tmp_path / "gen"
    gen_dir.mkdir()
    shutil.copyfile(KIT_PATH, gen_dir / "platform_kit.py")
    shutil.copyfile(NATIVES_FILE, gen_dir / "natives.py")
    code = generate_fastapi(plans, PROGRAM)
    assert 'UUID_STORAGE = "hex32"' in code and 'DATETIME_STORAGE = "sql_space"' in code
    (gen_dir / "generated_spaced.py").write_text(code)
    respell(canonical_db, gen_dir / "app.db", SPACED)
    rt_db = respell(canonical_db, tmp_path / "rt.db", SPACED)
    monkeypatch.chdir(gen_dir)
    monkeypatch.syspath_prepend(str(gen_dir))
    modules = ("platform_kit", "generated_spaced", "natives")
    for name in modules:
        sys.modules.pop(name, None)
    module = importlib.import_module("generated_spaced")
    try:
        with (
            TestClient(build_app(plans, rt_db, natives=NATIVES)) as rt,
            TestClient(module.app) as gen,
        ):
            yield rt, gen, rt_db, gen_dir / "app.db"
    finally:
        for name in modules:
            sys.modules.pop(name, None)


def test_the_generated_app_honours_the_storage(spaced_apps):
    rt, gen, rt_db, gen_db = spaced_apps
    for method, url, headers, body in RAW + WRITES:
        a = rt.request(method, url, headers=headers, content=body, follow_redirects=False)
        b = gen.request(method, url, headers=headers, content=body, follow_redirects=False)
        assert a.status_code == b.status_code, (method, url)
        if a.content and a.headers.get("content-type") == "application/json":
            assert normalize(a.json()) == normalize(b.json()), (method, url)
    assert normalize(canonical_rows(rt_db, SPACED)) == normalize(canonical_rows(gen_db, SPACED))


# ==== form bodies ======================================================================

FORM_PROGRAM = """# lattice: v1
@endpoint("POST", "/login/form", auth=Public, response="Token", body="form")
def login_form(username: Str(max_len=255), password: Str(max_len=255)) -> One | BadRequest:
    users = fetch(User)
    found = where(users, col("email") == username)
    account = one(found, else_=BadRequest("Incorrect email or password"))
    verified = require(
        account,
        native.verify_password(password, col("hashed_password")),
        else_=BadRequest("Incorrect email or password"),
    )
    token = derive(
        verified, access_token=native.create_access_token(col("id")), token_type="bearer"
    )
    shown = select(token, cols=["access_token", "token_type"])
    return shown


@endpoint("PATCH", "/items/{id}/form", auth=Customer, response="ItemPublic", body="form")
def retitle(
    id: Uuid,
    user: CurrentUser,
    title: Str(min_len=1, max_len=255) = UNSET,
    copies: Int(ge=0, le=5) = 0,
    loud: Bool = False,
) -> One | NotFound:
    items = fetch(Item)
    mine = owned(items, by=user)
    found = where(mine, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    done = update(item, title=title)
    return done
"""
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def test_form_fields_parse_only_urlencoded_bodies():
    body = b"username=a%40b.com&password=p+w&password=last&empty="
    assert form_fields(body, "application/x-www-form-urlencoded; charset=utf-8") == {
        "username": "a@b.com",
        "password": "last",  # a repeated field keeps its last value
        "empty": "",
    }
    assert form_fields(b'{"username": "x"}', "application/json") == {}
    assert form_fields(body, None) == {}


def test_the_plan_records_the_body_format():
    plans = {p.name: p for p in _plans(FORM_PROGRAM, PROFILE)}
    assert plans["login_form"].body == "form" and plans["retitle"].body == "form"
    assert {p.body for p in _plans(PROGRAM, PROFILE)} == {"json"}


FORM_REQUESTS = [
    ("POST", "/login/form", {**FORM}, b"username=bob%40example.com&password=bob-pass"),
    ("POST", "/login/form", {**FORM}, b"username=bob%40example.com&password=nope"),
    ("POST", "/login/form", {**FORM}, b"username=bob%40example.com"),
    (
        "POST",
        "/login/form",
        {"Content-Type": "application/json"},
        b'{"username": "x", "password": "y"}',
    ),
    ("POST", "/login/form", {}, b""),
    (
        "PATCH",
        f"/items/{uid(101)}/form",
        {**FORM, **_h("alice")},
        b"title=From+a+form&copies=3&loud=on",
    ),
    ("PATCH", f"/items/{uid(101)}/form", {**FORM, **_h("alice")}, b"copies=5"),
    ("PATCH", f"/items/{uid(101)}/form", {**FORM, **_h("alice")}, b"copies=x&loud=maybe"),
    ("PATCH", f"/items/{uid(101)}/form", {**FORM, **_h("alice")}, b"title=&copies=9"),
    ("PATCH", f"/items/{uid(103)}/form", {**FORM, **_h("alice")}, b"title=t"),
]


@pytest.fixture
def form_apps(tmp_path: Path, monkeypatch) -> Iterator[tuple]:
    plans = _plans(FORM_PROGRAM, PROFILE)
    gen_dir = tmp_path / "gen"
    gen_dir.mkdir()
    shutil.copyfile(KIT_PATH, gen_dir / "platform_kit.py")
    shutil.copyfile(NATIVES_FILE, gen_dir / "natives.py")
    (gen_dir / "generated_form.py").write_text(generate_fastapi(plans, FORM_PROGRAM))
    build_db(gen_dir / "app.db")
    rt_db = tmp_path / "rt.db"
    build_db(rt_db)
    monkeypatch.chdir(gen_dir)
    monkeypatch.syspath_prepend(str(gen_dir))
    modules = ("platform_kit", "generated_form", "natives")
    for name in modules:
        sys.modules.pop(name, None)
    module = importlib.import_module("generated_form")
    try:
        with (
            TestClient(build_app(plans, rt_db, natives=NATIVES)) as rt,
            TestClient(module.app) as gen,
        ):
            yield rt, gen
    finally:
        for name in modules:
            sys.modules.pop(name, None)


def test_form_requests(form_apps):
    rt, _ = form_apps
    answers = [rt.request(m, u, headers=h, content=b) for m, u, h, b in FORM_REQUESTS]
    assert [r.status_code for r in answers] == [200, 400, 422, 422, 422, 200, 200, 422, 422, 404]
    assert answers[0].json()["token_type"] == "bearer"
    assert answers[2].json()["detail"] == [{"loc": ["body", "password"], "msg": "field required"}]
    # a JSON (or empty) body has no form fields
    locs = [["body", "username"], ["body", "password"]]
    assert [d["loc"] for d in answers[3].json()["detail"]] == locs
    assert [d["loc"] for d in answers[4].json()["detail"]] == locs
    assert answers[5].json()["title"] == "From a form"
    assert answers[6].json()["title"] == "From a form"  # an unsent UNSET field is kept
    assert answers[7].json()["detail"] == [
        {"loc": ["body", "copies"], "msg": "must be an integer"},  # form text converts like a query
        {"loc": ["body", "loud"], "msg": "must be a boolean"},
    ]


@pytest.mark.parametrize(("method", "url", "headers", "body"), FORM_REQUESTS)
def test_the_generated_app_reads_forms_like_the_runtime(form_apps, method, url, headers, body):
    rt, gen = form_apps
    a = rt.request(method, url, headers=headers, content=body)
    b = gen.request(method, url, headers=headers, content=body)
    assert a.status_code == b.status_code
    assert normalize(a.json()) == normalize(b.json())


def _errors(src: str) -> list[str]:
    result = check_source(src, PROFILE)
    return [d.code for d in result.diagnostics if d.severity.name == "ERROR"]


@pytest.mark.parametrize(
    ("decl", "codes"),
    [
        ('"POST", "/login/form", auth=Public, response="Token", body="form"', []),
        ('"POST", "/login/form", auth=Public, response="Token", body="json"', []),
        ('"GET", "/login/form", auth=Public, response="Token", body="form"', ["L601"]),
        ('"DELETE", "/login/form", auth=Public, response="Token", body="form"', ["L601"]),
        ('"POST", "/login/form", auth=Public, response="Token", body="xml"', ["L601"]),
        ('"POST", "/login/form", auth=Public, response="Token", body=form', ["L601"]),
        ('"POST", "/login/form", auth=Public, response="Token", body=None', ["L601"]),
    ],
)
def test_the_checker_validates_body(decl: str, codes: list[str]):
    login = FORM_PROGRAM.split("\n\n\n")[0]
    src = login.replace('"POST", "/login/form", auth=Public, response="Token", body="form"', decl)
    assert _errors(src) == codes
