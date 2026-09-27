"""Sub-project 1 (real apps), runtime side: Uuid and DateTime values, fn.now() (one
instant per request), fn.date / fn.to_uuid / fn.to_datetime, page totals in the
profile's page shape (one statement), response models, UNSET partial updates,
owner_is() 403s, natives (reads, requires, nullability), NativeToken auth with an
Admin flag column, and trailing-slash routes (FastAPI's 307 redirect).

Every feature runs end to end through the runtime (TestClient) on a small SQLite
database with Uuid keys (tests/api/realapps.py). The last section is a SQL-vs-polars
differential for Uuid equality and DateTime ordering."""

from __future__ import annotations

import datetime as dt
import random
import re
import shutil
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from starlette.testclient import TestClient

import lattice.api.runtime as runtime
from lattice.api.backend import SQLITE, datetime_text, parse_datetime, uuid_text
from lattice.api.ir import EndpointPlan, ReadStep, WriteStep, lower_api
from lattice.api.runtime import build_app, serve_source
from lattice.api.sql import ParamRef, Rel, bind_values, compile_select, compile_update
from lattice.check import check_source
from lattice.typesys import Scalar, Schema
from tests.api.realapps import (
    ADMIN,
    ALICE,
    BOB,
    ITEM_PUBLIC,
    NATIVES,
    PROFILE,
    PROGRAM,
    TOKENS,
    USER_PUBLIC,
    build_db,
    uid,
)
from tests.api.test_runtime import data_statements

ISO_Z = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z")


@pytest.fixture(scope="module")
def pristine(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("realapps") / "pristine.db"
    build_db(path)
    return path


@pytest.fixture
def db(pristine: Path, tmp_path: Path) -> Path:
    path = tmp_path / "app.db"
    shutil.copyfile(pristine, path)
    return path


@pytest.fixture(scope="module")
def plans() -> tuple[EndpointPlan, ...]:
    check = check_source(PROGRAM, PROFILE)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    return lower_api(check.program)


@pytest.fixture
def client(plans, db: Path) -> Iterator[TestClient]:
    with TestClient(build_app(plans, db, natives=NATIVES)) as c:
        yield c


def plan(plans, name: str) -> EndpointPlan:
    return next(p for p in plans if p.name == name)


def auth(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[who]}"}


def rows(db: Path, sql: str, *args: object) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


@contextmanager
def counting() -> Iterator[list[str]]:
    statements: list[str] = []
    previous = runtime.on_connect
    runtime.on_connect = lambda conn: conn.set_trace_callback(statements.append)
    try:
        yield statements
    finally:
        runtime.on_connect = previous


# ---- Uuid ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad", ["not-a-uuid", "123", "00000000-0000-4000-8000-00000000010", "{" + uid(101) + "}"]
)
def test_a_malformed_uuid_is_422(client: TestClient, bad: str):
    r = client.get(f"/items/{bad}", headers=auth("alice"))
    assert r.status_code == 422
    assert r.json() == {
        "error": "validation",
        "detail": [{"loc": ["path", "id"], "msg": "must be a UUID"}],
    }


def test_a_malformed_body_uuid_is_422(client: TestClient):
    r = client.post(f"/items/{uid(101)}/copy", headers=auth("alice"), json={"new_id": 7})
    assert r.status_code == 422
    assert r.json()["detail"] == [{"loc": ["body", "new_id"], "msg": "must be a UUID"}]


@pytest.mark.parametrize(
    "spelling", [uid(101).upper(), uid(101).replace("-", ""), uid(101).replace("-", "").upper()]
)
def test_uuid_spellings_are_canonicalized(client: TestClient, spelling: str):
    r = client.get(f"/items/{spelling}", headers=auth("alice"))
    assert r.status_code == 200 and r.json()["id"] == uid(101)


def test_insert_generates_a_uuid4_key(client: TestClient, db: Path):
    r = client.post("/items/", headers=auth("alice"), json={"title": "Made"})
    assert r.status_code == 201
    made = r.json()
    new_id = uuid.UUID(made["id"])
    assert new_id.version == 4 and made["id"] == str(new_id)
    assert made["owner_id"] == ALICE
    stored = rows(db, "SELECT id, title, description, owner_id FROM item WHERE id = ?", made["id"])
    assert stored == [(made["id"], "Made", None, ALICE)]
    again = client.post("/items/", headers=auth("alice"), json={"title": "Made"}).json()
    assert again["id"] != made["id"]


def test_insert_keeps_a_given_uuid_key_canonical(client: TestClient, db: Path):
    given = "AAAAAAAA-0000-4000-8000-000000000999"
    r = client.post(f"/items/{uid(101)}/copy", headers=auth("alice"), json={"new_id": given})
    assert r.status_code == 201 and r.json()["id"] == given.lower()
    assert rows(db, "SELECT title FROM item WHERE id = ?", given.lower()) == [("copy",)]


def test_signup_generates_the_users_key(client: TestClient, db: Path):
    r = client.post("/users/signup", json={"email": "d@x.io", "password": "longpassword"})
    assert r.status_code == 201
    body = r.json()
    assert list(body) == list(USER_PUBLIC) and uuid.UUID(body["id"]).version == 4
    assert body["full_name"] is None and body["is_superuser"] is False
    (hashed,) = rows(db, 'SELECT hashed_password FROM "user" WHERE id = ?', body["id"])[0]
    assert hashed == NATIVES["hash_password"]("longpassword")


# ---- DateTime and fn.now() ------------------------------------------------------------------


def test_now_is_one_instant_per_request(client: TestClient):
    before = dt.datetime.now(dt.UTC)
    body = client.get(f"/clock/{uid(101)}", headers=auth("alice")).json()
    after = dt.datetime.now(dt.UTC)
    assert body["a"] == body["b"] and ISO_Z.fullmatch(body["a"])
    instant = parse_datetime(body["a"])
    assert instant is not None and before <= instant <= after
    assert body["day"] == body["a"][:10]  # fn.date(fn.now())
    assert body["older"] is True and body["created_day"] == "2000-01-05"
    later = client.get(f"/clock/{uid(101)}", headers=auth("alice")).json()
    assert later["a"] > body["a"]  # a new request, a new instant


def test_now_is_one_bind_shared_by_every_use(plans):
    (read,) = [s for s in plan(plans, "clock").steps if isinstance(s, ReadStep)]
    assert read.sql.now and read.sql.text.count(":__now") == 4
    binds = bind_values(
        read.sql, params={"id": uid(101)}, now=dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
    )
    assert binds["__now"] == "2024-01-01T00:00:00.000000Z"


def test_created_at_is_stored_as_utc_text_with_z(client: TestClient, db: Path):
    made = client.post("/items/", headers=auth("bob"), json={"title": "t"}).json()
    (stored,) = rows(db, "SELECT created_at FROM item WHERE id = ?", made["id"])[0]
    assert ISO_Z.fullmatch(stored) and stored == made["created_at"]


def test_datetime_params_compare_in_time_order(client: TestClient):
    def titles(after: str | None) -> list[str]:
        params = {} if after is None else {"after": after}
        r = client.get("/items/since", headers=auth("alice"), params=params)
        assert r.status_code == 200, r.text
        return [i["title"] for i in r.json()["data"]]

    assert titles(None) == ["Beta", "Alpha"]  # the default, 2000-01-02T00:00:00Z
    assert titles("2000-01-03T11:00:00+02:00") == ["Beta", "Alpha"]  # 09:00 UTC
    assert titles("2000-01-03T12:00:00+02:00") == ["Alpha"]  # 10:00 UTC: not after
    assert titles("2000-01-03T10:00:00") == ["Alpha"]  # naive is UTC
    assert titles("1999-12-31T23:00:00Z") == ["Delta", "Beta", "Alpha"]


@pytest.mark.parametrize("bad", ["yesterday", "2000-13-01T00:00:00Z", "1"])
def test_a_malformed_datetime_is_422(client: TestClient, bad: str):
    r = client.get("/items/since", headers=auth("alice"), params={"after": bad})
    assert r.status_code == 422
    assert r.json()["detail"] == [{"loc": ["query", "after"], "msg": "must be an ISO date-time"}]


def test_to_uuid_and_to_datetime(client: TestClient):
    r = client.get("/admin/alice-items", headers=auth("admin"))
    assert r.status_code == 200
    assert r.json() == {
        "data": [
            {"id": uid(104), "title": "Delta", "created_at": "2000-01-01T10:00:00.000000Z"},
            {"id": uid(102), "title": "Beta", "created_at": "2000-01-03T10:00:00.000000Z"},
        ]
    }


def test_value_helpers():
    assert uuid_text("5B3E9C1C00004000800000000000000A") == "5b3e9c1c-0000-4000-8000-00000000000a"
    assert uuid_text("5b3e9c1c-0000-40008000-00000000000a") is None
    when = parse_datetime("2024-02-29T23:30:00-01:00")
    assert when is not None and datetime_text(when) == "2024-03-01T00:30:00.000000Z"
    assert SQLITE.from_db("2026-01-01 09:00:00.000000", Scalar.DATETIME) == (
        "2026-01-01T09:00:00.000000Z"
    )
    assert SQLITE.from_db("5b3e9c1c000040008000000000000000", Scalar.UUID) == (
        "5b3e9c1c-0000-4000-8000-000000000000"
    )


# ---- page totals, the page shape and response models ----------------------------------------


def test_page_with_total_in_the_profiles_shape(client: TestClient):
    r = client.get("/items/", headers=auth("alice"), params={"limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert list(body) == ["data", "count"]  # limit/offset are mapped to "": omitted
    assert body["count"] == 3
    assert [i["title"] for i in body["data"]] == ["Alpha", "Beta"]  # created_at desc
    assert all(list(i) == list(ITEM_PUBLIC) for i in body["data"])


def test_the_total_survives_an_empty_page(client: TestClient):
    r = client.get("/items/", headers=auth("alice"), params={"skip": 50})
    assert r.json() == {"data": [], "count": 3}
    r = client.get("/items/", headers=auth("admin"), params={"skip": 2, "limit": 3})
    assert len(r.json()["data"]) == 3 and r.json()["count"] == 6


def test_the_total_of_an_empty_table_is_zero(client: TestClient, db: Path):
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM note")
    conn.execute("DELETE FROM item")
    conn.commit()
    conn.close()
    assert client.get("/items/", headers=auth("alice")).json() == {"data": [], "count": 0}


def test_page_with_total_is_one_statement(client: TestClient, plans):
    with counting() as statements:
        r = client.get("/users/", headers=auth("admin"), params={"limit": 2, "skip": 1})
    assert r.json()["count"] == 4 and len(r.json()["data"]) == 2
    assert len(data_statements(statements)) == 2  # auth + one SELECT with its total
    read = plan(plans, "read_users").steps[0]
    assert isinstance(read, ReadStep) and read.sql.total
    assert "COUNT(*) AS __total" in read.sql.text and "LEFT JOIN" in read.sql.text


def test_a_page_without_total_omits_the_count(client: TestClient):
    body = client.get("/admin/alice-items", headers=auth("admin")).json()
    assert list(body) == ["data"]


def test_response_models_fix_field_order(client: TestClient):
    one = client.get(f"/items/{uid(102)}", headers=auth("alice")).json()
    assert list(one) == list(ITEM_PUBLIC) and one["description"] is None
    me = client.get("/users/me", headers=auth("bob")).json()
    assert list(me) == list(USER_PUBLIC)
    assert me == {
        "email": "bob@example.com",
        "is_active": True,
        "is_superuser": False,
        "full_name": None,
        "id": BOB,
        "created_at": "2000-01-03T09:00:00.000000Z",
    }
    token = client.post(
        "/login/access-token", json={"username": "bob@example.com", "password": "bob-pass"}
    ).json()
    assert list(token) == ["access_token", "token_type"]


def test_the_response_schema_is_the_models_fields(plans):
    assert plan(plans, "read_item").schema.names() == ITEM_PUBLIC
    assert plan(plans, "read_item").response == "ItemPublic"
    assert plan(plans, "delete_item").response is None


# ---- UNSET partial updates -------------------------------------------------------------------


def _item(db: Path, n: int) -> tuple[Any, ...]:
    return rows(db, "SELECT title, description FROM item WHERE id = ?", uid(n))[0]


def test_update_sets_only_the_sent_fields(client: TestClient, db: Path):
    r = client.put(f"/items/{uid(101)}", headers=auth("alice"), json={"title": "New"})
    assert r.status_code == 200
    assert (r.json()["title"], r.json()["description"]) == ("New", "first")
    assert _item(db, 101) == ("New", "first")
    r = client.put(f"/items/{uid(101)}", headers=auth("alice"), json={"description": "d2"})
    assert _item(db, 101) == ("New", "d2") and r.json()["description"] == "d2"


def test_update_with_nothing_sent_changes_nothing(client: TestClient, db: Path):
    r = client.put(f"/items/{uid(101)}", headers=auth("alice"), json={})
    assert r.status_code == 200 and r.json()["title"] == "Alpha"
    assert _item(db, 101) == ("Alpha", "first")


def test_explicit_null_sets_a_nullable_column(client: TestClient, db: Path):
    r = client.put(f"/items/{uid(101)}", headers=auth("alice"), json={"description": None})
    assert r.status_code == 200 and r.json()["description"] is None
    assert _item(db, 101) == ("Alpha", None)


def test_explicit_null_for_a_non_nullable_column_is_422(client: TestClient, db: Path):
    r = client.put(
        f"/items/{uid(101)}", headers=auth("alice"), json={"title": None, "description": "x"}
    )
    assert r.status_code == 422
    assert r.json()["detail"] == [{"loc": ["body", "title"], "msg": "must not be null"}]
    assert _item(db, 101) == ("Alpha", "first")


def test_a_sent_optional_value_is_still_validated(client: TestClient):
    r = client.put(f"/items/{uid(101)}", headers=auth("alice"), json={"title": ""})
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"] == "must have at least 1 characters"


def test_partial_update_is_one_statement(client: TestClient, plans):
    with counting() as statements:
        client.put(f"/items/{uid(101)}", headers=auth("alice"), json={"title": "x"})
    data = data_statements(statements)
    assert len(data) == 3  # auth, the read (with the owner_is flag), the update
    update = next(s for s in plan(plans, "update_item").steps if isinstance(s, WriteStep))
    assert update.sql.sent == ("title", "description")
    assert 'CASE WHEN :__sent_title THEN s."__v0" ELSE "item"."title" END' in update.sql.text


def test_partial_update_of_the_caller(client: TestClient, db: Path):
    r = client.patch("/users/me", headers=auth("bob"), json={"full_name": "Bobby"})
    assert r.status_code == 200 and r.json()["full_name"] == "Bobby"
    assert r.json()["email"] == "bob@example.com"
    assert rows(db, 'SELECT email, full_name FROM "user" WHERE id = ?', BOB) == [
        ("bob@example.com", "Bobby")
    ]
    r = client.patch("/users/me", headers=auth("bob"), json={"email": None})
    assert r.status_code == 422


def test_unsent_optional_insert_value_is_null(client: TestClient, db: Path):
    made = client.post("/items/", headers=auth("alice"), json={"title": "a"}).json()
    assert made["description"] is None
    made = client.post(
        "/items/", headers=auth("alice"), json={"title": "b", "description": None}
    ).json()
    assert made["description"] is None
    made = client.post(
        "/items/", headers=auth("alice"), json={"title": "c", "description": "set"}
    ).json()
    assert rows(db, "SELECT description FROM item WHERE id = ?", made["id"]) == [("set",)]


def test_compile_update_with_an_optional_param():
    q = compile_update(
        "t", ("id",), ("s", ("id", "a", "b")), [("a", ParamRef("x", True)), ("b", ParamRef("y"))]
    )
    assert q.sent == ("x",) and q.params == ("x", "y")
    assert '"a" = CASE WHEN :__sent_x THEN s."__v0" ELSE "t"."a" END' in q.text
    assert '"b" = s."__v1"' in q.text


# ---- owner_is(user): 403 -------------------------------------------------------------------


def test_owner_is_answers_403_for_another_users_row(client: TestClient, db: Path):
    r = client.get(f"/items/{uid(103)}", headers=auth("alice"))
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "detail": "Not enough permissions"}
    r = client.put(f"/items/{uid(103)}", headers=auth("alice"), json={"title": "mine"})
    assert r.status_code == 403 and _item(db, 103)[0] == "Gamma"
    assert client.delete(f"/items/{uid(103)}", headers=auth("alice")).status_code == 403
    assert rows(db, "SELECT COUNT(*) FROM item WHERE id = ?", uid(103)) == [(1,)]


def test_owner_is_lets_the_owner_and_admin_through(client: TestClient, db: Path):
    assert client.get(f"/items/{uid(103)}", headers=auth("bob")).status_code == 200
    assert client.get(f"/items/{uid(103)}", headers=auth("admin")).status_code == 200
    assert client.delete(f"/items/{uid(103)}", headers=auth("admin")).status_code == 204
    assert rows(db, "SELECT COUNT(*) FROM item WHERE id = ?", uid(103)) == [(0,)]


def test_a_missing_row_is_still_404(client: TestClient):
    r = client.get(f"/items/{uid(999)}", headers=auth("alice"))
    assert r.status_code == 404 and r.json()["detail"] == "Item not found"


def test_owner_is_through_the_parent_row(client: TestClient):
    assert client.get(f"/notes/{uid(201)}", headers=auth("alice")).status_code == 200
    r = client.get(f"/notes/{uid(202)}", headers=auth("alice"))
    assert r.status_code == 403 and r.json()["detail"] == "Not your note"
    assert client.get(f"/notes/{uid(202)}", headers=auth("bob")).status_code == 200


def test_owner_is_costs_no_statement(client: TestClient, plans):
    with counting() as statements:
        client.get(f"/items/{uid(103)}", headers=auth("alice"))
    assert len(data_statements(statements)) == 2  # auth + the read computing the flag
    (read,) = [s for s in plan(plans, "read_item").steps if isinstance(s, ReadStep)]
    assert read.flags and read.sql.user
    assert '(:__is_admin OR "owner_id" = :__user_id)' in read.sql.text


# ---- natives ------------------------------------------------------------------------------


def test_natives_in_reads(client: TestClient):
    r = client.get(f"/items/{uid(101)}/loud", headers=auth("alice"))
    assert r.json() == {
        "id": uid(101),
        "loud": "ALPHA!",
        "size": 5,
        "year": "2000-01-01T00:00:00.000000Z",
    }
    # a nullable native's null (and a null argument) is JSON null
    r = client.get(f"/items/{uid(105)}/loud", headers=auth("bob"))
    assert r.status_code == 200 and r.json()["size"] is None  # description ""


def test_natives_in_requires(client: TestClient):
    r = client.get(f"/items/{uid(102)}/loud", headers=auth("alice"))
    assert r.status_code == 400 and r.json()["detail"] == "no beta"


def test_login_through_natives(client: TestClient):
    ok = client.post(
        "/login/access-token", json={"username": "alice@example.com", "password": "alice-pass"}
    )
    assert ok.status_code == 200
    assert ok.json() == {"access_token": TOKENS["alice"], "token_type": "bearer"}
    for username, password, detail in [
        ("alice@example.com", "wrong", "Incorrect email or password"),
        ("nobody@example.com", "alice-pass", "Incorrect email or password"),
        ("carol@example.com", "carol-pw", "Inactive user"),
    ]:
        r = client.post("/login/access-token", json={"username": username, "password": password})
        assert (r.status_code, r.json()["detail"]) == (400, detail)


def test_a_null_from_a_non_nullable_native_is_a_500(client: TestClient):
    r = client.get(f"/items/{uid(101)}/broken", headers=auth("alice"))
    assert r.status_code == 500 and r.json()["error"] == "internal"


def test_natives_are_registered_per_plan(plans):
    assert dict(plan(plans, "login").natives).keys() == {"verify_password", "create_access_token"}
    # every authenticated endpoint calls the token decoder
    assert "decode_token" in dict(plan(plans, "read_item").natives)
    assert plan(plans, "register_user").natives[0][0] == "hash_password"


def test_a_native_without_an_implementation_is_refused(plans, db: Path):
    with pytest.raises(ValueError, match="native 'hash_password' has no implementation"):
        build_app(plans, db, natives={k: v for k, v in NATIVES.items() if k != "hash_password"})
    check, app = serve_source(PROGRAM, PROFILE, db, natives=NATIVES)
    assert check.ok and app is not None


def test_native_results_are_type_checked(tmp_path: Path, pristine: Path):
    db = tmp_path / "x.db"
    shutil.copyfile(pristine, db)
    natives = {**NATIVES, "shout": lambda text: 42}
    with TestClient(serve_source(PROGRAM, PROFILE, db, natives=natives)[1]) as c:
        r = c.get(f"/items/{uid(101)}/loud", headers=auth("alice"))
    assert r.status_code == 500


# ---- NativeToken auth and the Admin flag --------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        None,
        "Bearer",
        "Bearer not-a-token",
        f"Bearer tok-{uid(777)}",  # decodes, but no such user
        "Bearer tok-zzz",  # the native returns null
        f"Basic {TOKENS['alice']}",
    ],
)
def test_native_token_failures_are_401(client: TestClient, header: str | None):
    headers = {} if header is None else {"Authorization": header}
    r = client.get("/users/me", headers=headers)
    assert (r.status_code, r.json()) == (401, {"error": "unauthorized"})


def test_native_token_auth_is_one_statement(client: TestClient, plans):
    with counting() as statements:
        r = client.get("/users/me", headers=auth("alice"))
    assert r.status_code == 200 and r.json()["id"] == ALICE
    data = data_statements(statements)
    assert len(data) == 1
    assert 'FROM "user" WHERE "id" = native_decode_token(' in data[0]


def test_the_admin_flag_gives_the_role(client: TestClient):
    assert client.get("/users/", headers=auth("alice")).status_code == 403
    assert client.get("/users/", headers=auth("admin")).status_code == 200
    assert client.get("/admin/alice-items", headers=auth("bob")).json() == {"error": "forbidden"}
    # the admin passes every ownership check
    assert client.get(f"/items/{uid(101)}", headers=auth("admin")).json()["owner_id"] == ALICE
    assert client.get("/items/", headers=auth("admin")).json()["count"] == 6


def test_an_admin_created_item_is_owned_by_the_admin(client: TestClient):
    made = client.post("/items/", headers=auth("admin"), json={"title": "boss"}).json()
    assert made["owner_id"] == ADMIN


# ---- trailing slashes (FastAPI's redirect_slashes) ----------------------------------------------


def test_a_trailing_slash_route_matches_exactly(client: TestClient):
    r = client.get("/items/", headers=auth("alice"))
    assert r.status_code == 200


def test_the_slashless_path_redirects_307(client: TestClient):
    r = client.get("/items?limit=1", headers=auth("alice"), follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "http://testserver/items/?limit=1"
    r = client.get("/items?limit=1", headers=auth("alice"))  # followed
    assert r.status_code == 200 and len(r.json()["data"]) == 1
    r = client.get("/users/me/", headers=auth("alice"), follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "http://testserver/users/me"


# ---- SQL vs polars: Uuid equality and DateTime ordering ------------------------------------------


EVENTS = Schema.from_pairs(
    [("id", Scalar.INT), ("who", Scalar.UUID), ("at", Scalar.DATETIME)], keys=[("id",)]
)


def _events(n: int, seed: int) -> list[tuple[int, uuid.UUID, dt.datetime]]:
    rng = random.Random(seed)
    people = [uuid.UUID(int=rng.getrandbits(128), version=4) for _ in range(4)]
    base = dt.datetime(1999, 12, 31, 20, 0, tzinfo=dt.UTC)
    out = []
    for i in range(n):
        offset = dt.timedelta(minutes=rng.randrange(0, 5 * 24 * 60), microseconds=rng.randrange(10))
        zone = dt.timezone(dt.timedelta(hours=rng.randrange(-11, 12)))
        out.append((i + 1, rng.choice(people), (base + offset).astimezone(zone)))
    return out


@pytest.mark.parametrize("seed", range(5))
def test_sql_matches_polars_for_uuid_and_datetime(tmp_path: Path, seed: int):
    events = _events(60, seed)
    path = tmp_path / "events.db"
    conn = SQLITE.connect(path)
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, who TEXT, at TEXT)")
    conn.executemany(
        "INSERT INTO events VALUES (?, ?, ?)",
        [(i, SQLITE.to_db(w), SQLITE.to_db(a)) for i, w, a in events],
    )
    frame = pl.DataFrame(
        {
            "id": [i for i, _, _ in events],
            "who": [str(w) for _, w, _ in events],
            "at": [a.astimezone(dt.UTC) for _, _, a in events],
        }
    )
    rng = random.Random(seed)
    person = rng.choice(events)[1]
    cutoff = rng.choice(events)[2] + dt.timedelta(microseconds=rng.choice([-1, 0, 1]))
    import ast

    def rel_where(pred: ast.expr) -> dict[str, Rel]:
        fetch = Rel("e", "fetch", (), {"table": "events", "order": ("id",)}, EVENTS)
        where = Rel("w", "where", ("e",), {"predicate": pred}, EVENTS)
        srt = Rel("s", "sort", ("w",), {"by": ("at",), "desc": False}, EVENTS)
        return {"e": fetch, "w": where, "s": srt}

    def col(name: str) -> ast.expr:
        return ast.Call(ast.Name("col"), [ast.Constant(name)], [])

    def cmp(left: ast.expr, op: ast.cmpop, right: ast.expr) -> ast.expr:
        return ast.Compare(left, [op], [right])

    cases = [
        (
            cmp(col("who"), ast.Eq(), ParamRef("p")),
            {"p": person},
            frame.filter(pl.col("who") == str(person)),
        ),
        (
            cmp(col("who"), ast.NotEq(), ParamRef("p")),
            {"p": uuid_text(str(person).upper())},
            frame.filter(pl.col("who") != str(person)),
        ),
        (
            cmp(col("at"), ast.Lt(), ParamRef("p")),
            {"p": cutoff},
            frame.filter(pl.col("at") < cutoff),
        ),
        (
            cmp(col("at"), ast.GtE(), ParamRef("p")),
            {"p": cutoff},
            frame.filter(pl.col("at") >= cutoff),
        ),
    ]
    for pred, params, expected in cases:
        query = compile_select(rel_where(pred), "s")
        got = conn.execute(query.text, bind_values(query, params=params)).fetchall()
        want = expected.sort(["at", "id"])
        assert [r[0] for r in got] == want["id"].to_list()
        assert [parse_datetime(r[2]) for r in got] == want["at"].to_list()
    conn.close()
