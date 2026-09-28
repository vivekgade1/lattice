"""The four language gaps the held-out imports found, closed generically (the fixture is
tests/api/solo.py; the generated FastAPI app is compared with the runtime in
test_codegen_differential):

1. single-role auth -- ``roles_from=NoRoles()``: every caller is the one role, no
   caller is ever an Admin, no role column is read; ``auth=<another role>`` is L801;
2. a token subject that is not the users key -- ``NativeToken(native, lookup=col)``
   matches the decoded claim against a unique users column (one statement);
3. natives that need app configuration -- ``NativeSig(settings=(...))`` natives get a
   read-only ``NativeContext`` of profile-declared ``ApiProfile.settings``, supplied at
   start-up (``build_app(..., settings=...)``);
4. enum columns -- ``Schema.from_pairs(..., one_of={col: (...)})``: every write passes a
   listed literal or a ``OneOf`` parameter whose values are listed (else L407), so a
   request with another value is a 422 at the API boundary.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from lattice.api.backend import SQLITE
from lattice.api.ir import lower_api
from lattice.api.runtime import auth_sql, build_app
from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.profile import (
    ApiProfile,
    NativeSig,
    NativeToken,
    NoRoles,
    Profile,
    RoleColumn,
)
from lattice.typesys import Scalar, Schema
from tests.api import solo as F

S = Scalar


def _errors(source: str, profile: Profile = F.PROFILE) -> list[str]:
    result = check_source(source, profile)
    return [d.code for d in result.diagnostics if d.severity is Severity.ERROR]


def _api(**kw) -> ApiProfile:
    return replace(F.API, **kw)


def _h(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {F.TOKENS[who]}"}


@pytest.fixture
def plans():
    check = check_source(F.PROGRAM, F.PROFILE)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    return lower_api(check.program)


@pytest.fixture
def client(tmp_path: Path, plans) -> Iterator[TestClient]:
    db = tmp_path / "app.db"
    F.build_db(db)
    app = build_app(plans, db, natives=F.NATIVES, settings=F.SETTINGS)
    with TestClient(app) as c:
        yield c


# ---- 1. single-role auth ------------------------------------------------------------------


def test_no_roles_names_a_non_admin_role_of_the_profile() -> None:
    assert _api(roles_from=NoRoles()).roles_from == NoRoles("Customer")
    for bad in ("Admin", "Seller"):
        with pytest.raises(ValueError, match="NoRoles"):
            _api(roles_from=NoRoles(bad))


def test_every_caller_is_the_one_role_and_never_an_admin(client: TestClient) -> None:
    for who in ("alice", "bob"):
        me = client.get("/me", headers=_h(who)).json()
        assert me["admin"] is False and me["username"] == who


def test_owned_equals_mine_without_admins(client: TestClient) -> None:
    for who, uid in (("alice", 1), ("bob", 2)):
        owned = client.get("/tasks", headers=_h(who)).json()
        mine = client.get("/tasks/mine", headers=_h(who)).json()
        assert owned == mine and {t["owner_id"] for t in owned["items"]} == {uid}
        assert owned["total"] == 2


def test_the_runtime_reads_no_role_column(plans) -> None:
    for plan in plans:
        assert "role" not in plan.user_columns and plan.fixed_role == "Customer"


def test_an_admin_endpoint_is_l801_without_admins() -> None:
    src = F.PROGRAM + (
        '\n\n@endpoint("GET", "/all", auth=Admin)\n'
        "def all_tasks(user: CurrentUser) -> Page:\n"
        "    tasks = fetch(Task)\n"
        "    result = page(tasks, limit=10)\n"
        "    return result\n"
    )
    assert _errors(src) == ["L801"]
    # the same endpoint is fine where callers have roles (a role column)
    users = Schema.from_pairs(
        [*F.USER.columns, ("role", S.STR)], keys=F.USER.keys, private=["password_hash"]
    )
    with_roles = Profile(
        schemas={},
        api=_api(tables={"User": users, "Task": F.TASK}, roles_from=RoleColumn()),
    )
    assert _errors(src, with_roles) == []


# ---- 2. a token subject that is not the users key -----------------------------------------


def test_the_lookup_column_is_matched_in_one_statement(plans) -> None:
    sql = auth_sql(plans[0], SQLITE)
    assert 'WHERE "username" = native_decode_token(?)' in sql and sql.count("SELECT") == 1


def test_a_token_naming_a_username_authenticates_that_user(client: TestClient) -> None:
    assert client.get("/me", headers=_h("bob")).json()["id"] == 2
    assert client.get("/me", headers=_h("ghost")).status_code == 401  # no such username
    assert client.get("/me", headers=_h("forged")).status_code == 401  # bad signature
    assert client.get("/me", headers=_h("carol")).status_code == 400  # inactive


def test_the_lookup_column_must_be_a_unique_users_column() -> None:
    users = Schema.from_pairs(F.USER.columns, keys=[("id",)], private=["password_hash"])
    with pytest.raises(ValueError, match="unique"):
        _api(tables={"User": users, "Task": F.TASK})
    with pytest.raises(ValueError, match="not a column"):
        _api(auth=NativeToken("decode_token", lookup="nickname"))
    # the native must decode to the lookup column's type (username: Str, not the Int key)
    natives = {**F.API.natives, "decode_token": NativeSig((S.STR,), S.INT, settings=("secret",))}
    with pytest.raises(ValueError, match="Str -> Str"):
        _api(natives=natives)


def test_unique_runtime_allows_a_lookup_column_with_no_declared_constraint() -> None:
    assert F.API_RUNTIME.auth == NativeToken("decode_token", "username", unique="runtime")
    with pytest.raises(ValueError, match="unique"):
        NativeToken("decode_token", lookup="username", unique="maybe")
    with pytest.raises(ValueError, match="lookup"):  # the key is unique by construction
        _api(auth=NativeToken("decode_token", unique="runtime"))


@pytest.fixture
def runtime_client(tmp_path: Path) -> Iterator[TestClient]:
    check = check_source(F.PROGRAM, F.PROFILE_RUNTIME)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    db = tmp_path / "app.db"
    F.build_db_runtime(db)
    app = build_app(lower_api(check.program), db, natives=F.NATIVES, settings=F.SETTINGS)
    with TestClient(app) as c:
        yield c


def test_unique_runtime_one_row_authenticates_none_is_401(runtime_client: TestClient) -> None:
    assert runtime_client.get("/me", headers=_h("bob")).json()["id"] == 2
    assert runtime_client.get("/me", headers=_h("ghost")).status_code == 401


def test_unique_runtime_two_rows_is_a_loud_500_never_one_of_them(
    runtime_client: TestClient,
) -> None:
    got = runtime_client.get("/me", headers=_h("dave"))
    assert got.status_code == 500
    assert "R311" in got.text and "ambiguous caller" in got.text
    # a writing endpoint changes nothing either
    before = runtime_client.get("/tasks", headers=_h("alice")).json()
    assert runtime_client.post("/tasks", headers=_h("dave"), json={"title": "x"}).status_code == 500
    assert runtime_client.get("/tasks", headers=_h("alice")).json() == before


# ---- 3. natives with a NativeContext -------------------------------------------------------


def test_a_context_native_reads_its_setting(client: TestClient) -> None:
    assert client.get("/me", headers=_h("alice")).json()["greeting"] == "Hello, alice!"


def test_settings_are_declared_and_typed() -> None:
    bad_sig = {**F.API.natives, "greet": NativeSig((S.STR,), S.STR, settings=("tone",))}
    with pytest.raises(ValueError, match="undeclared setting"):
        _api(natives=bad_sig)
    with pytest.raises(ValueError, match="settings"):
        _api(settings={"secret": S.STR, "greeting": S.DATE})


def test_a_missing_or_mistyped_setting_fails_at_start_up(tmp_path: Path, plans) -> None:
    db = tmp_path / "app.db"
    F.build_db(db)
    with pytest.raises(ValueError, match="setting 'greeting'"):
        build_app(plans, db, natives=F.NATIVES, settings={"secret": "s"})
    with pytest.raises(ValueError, match="setting 'secret' must be a Str"):
        build_app(plans, db, natives=F.NATIVES, settings={**F.SETTINGS, "secret": 7})


def test_the_context_is_read_only_and_holds_only_declared_settings(tmp_path: Path, plans) -> None:
    seen: list[object] = []

    def spy(ctx, name):
        seen.append(ctx)
        return "x"

    db = tmp_path / "app.db"
    F.build_db(db)
    settings = {**F.SETTINGS, "unrelated": "no"}
    app = build_app(plans, db, natives={**F.NATIVES, "greet": spy}, settings=settings)
    with TestClient(app) as c:
        assert c.get("/me", headers=_h("alice")).status_code == 200
    assert seen and all(c is seen[0] for c in seen)  # one context, built at start-up
    ctx = seen[0]
    assert dict(ctx) == {"greeting": "Hello"}
    with pytest.raises(TypeError):
        ctx["greeting"] = "changed"  # type: ignore[index]


# ---- 4. OneOf columns ----------------------------------------------------------------------


def test_one_of_columns_are_str_columns_with_distinct_values() -> None:
    cols = [("id", S.INT), ("status", S.STR), ("n", S.INT)]
    assert Schema.from_pairs(cols, one_of={"status": ("a", "b")}).choices("status") == ("a", "b")
    for bad, why in (
        ({"missing": ("a",)}, "not in schema"),
        ({"n": ("a",)}, "Str"),
        ({"status": ()}, "at least one"),
        ({"status": ("a", "a")}, "twice"),
    ):
        with pytest.raises(ValueError, match=why):
            Schema.from_pairs(cols, one_of=bad)


def test_a_value_outside_the_set_is_422(client: TestClient) -> None:
    bad = client.post("/tasks", headers=_h("alice"), json={"title": "x", "status": "blocked"})
    assert bad.status_code == 422 and bad.json()["detail"][0]["loc"] == ["body", "status"]
    ok = client.post("/tasks", headers=_h("alice"), json={"title": "x", "priority": "low"})
    assert ok.status_code == 201 and ok.json()["status"] == "todo"
    patch = client.patch("/tasks/1", headers=_h("alice"), json={"priority": "urgent"})
    assert patch.status_code == 422
    assert client.post("/tasks/2/finish", headers=_h("alice")).json()["status"] == "done"


def test_a_column_stored_by_another_spelling_is_decoded_and_encoded(
    client: TestClient, tmp_path: Path
) -> None:
    """``status`` is stored by member name: responses, filters and writes use the
    values, the database keeps its own spellings."""
    listed = client.get("/tasks", headers=_h("alice"), params={"status": "doing"}).json()
    assert [(t["id"], t["status"]) for t in listed["items"]] == [(2, "doing")]
    made = client.post("/tasks", headers=_h("bob"), json={"title": "t", "status": "doing"})
    assert made.json()["status"] == "doing"
    patched = client.patch(
        f"/tasks/{made.json()['id']}", headers=_h("bob"), json={"status": "done"}
    )
    assert patched.json()["status"] == "done"
    import sqlite3

    conn = sqlite3.connect(tmp_path / "app.db")
    try:
        stored = dict(conn.execute("SELECT id, status FROM tasks").fetchall())
    finally:
        conn.close()
    assert stored[made.json()["id"]] == "DONE" and stored[2] == "DOING"


def test_stored_spellings_belong_to_one_of_columns() -> None:
    cols = [("id", S.INT), ("status", S.STR)]
    for bad, why in (
        ({"id": ("A",)}, "not a one_of column"),
        ({"status": ("A",)}, "one distinct spelling per value"),
        ({"status": ("A", "A")}, "one distinct spelling per value"),
    ):
        with pytest.raises(ValueError, match=why):
            Schema.from_pairs(cols, one_of={"status": ("a", "b")}, one_of_stored=bad)


_WRITE = """# lattice: v1
@endpoint("POST", "/t", auth=Customer)
def make(user: CurrentUser, title: Str(max_len=9), {param}) -> Created:
    task = insert(Task, owner=user, title=title, status={value})
    return task
"""


@pytest.mark.parametrize(
    ("param", "value"),
    [
        ('s: OneOf("todo", "done") = "todo"', "s"),  # a subset of the column's values
        ('s: OneOf("todo", "doing", "done") = "todo"', "s"),
        ("s: Int(ge=0) = 0", '"doing"'),  # a listed literal
    ],
)
def test_a_write_from_listed_values_is_accepted(param: str, value: str) -> None:
    assert _errors(_WRITE.format(param=param, value=value)) == []


@pytest.mark.parametrize(
    ("param", "value"),
    [
        ("s: Int(ge=0) = 0", '"blocked"'),  # an unlisted literal
        ("s: Str(max_len=9)", "s"),  # any text
        ('s: OneOf("todo", "blocked") = "todo"', "s"),  # a value the column does not list
        ('s: OneOf("todo", "done") = "todo"', "fn.lower(s)"),  # a computed value
    ],
)
def test_a_write_that_may_leave_the_set_is_l407(param: str, value: str) -> None:
    assert _errors(_WRITE.format(param=param, value=value)) == ["L407"]


def test_an_update_to_an_unlisted_literal_is_l407() -> None:
    src = F.PROGRAM.replace('update(task, status="done")', 'update(task, status="finished")')
    assert _errors(src) == ["L407"]
