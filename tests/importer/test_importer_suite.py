"""lattice.importer.suite: request suites and seeded databases from route and table specs."""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

from lattice.importer.suite import (
    AuthSpec,
    ColumnSpec,
    ErrorSpec,
    ParamSpec,
    RouteSpec,
    TableSpec,
    generate_suite,
    seed_plan,
    sql_seed,
)

C = ColumnSpec
USER = TableSpec(
    "user",
    (
        C("id", "uuid", primary_key=True),
        C("email", "email", unique=True, max_len=255),
        C("hashed_password", "str"),
        C("is_active", "bool"),
        C("is_superuser", "bool"),
        C("full_name", "str", nullable=True, max_len=255),
        C("created_at", "datetime", nullable=True),
    ),
)
NOTE = TableSpec(
    "note",
    (
        C("id", "uuid", primary_key=True),
        C("title", "str", min_len=1, max_len=20),
        C("body", "str", nullable=True, max_len=50),
        C("stars", "int", ge=0, le=5),
        C("owner_id", "uuid", foreign_key="user.id"),
        C("created_at", "datetime", nullable=True),
    ),
    owner="owner_id",
)
AUTH = AuthSpec(
    users_table="user",
    login_path="/login",
    hash_password=lambda pw: "hash:" + pw,
    mint_token=lambda uid: f"tok-{uid}",
)
P = ParamSpec
TITLE = P("title", "str", "body", min_len=1, max_len=20)
BODY = P("body", "str", "body", required=False, nullable=True, max_len=50)
STARS = P("stars", "int", "body", required=False, ge=0, le=5)
ID = P("id", "uuid", "path")
ROUTES = [
    RouteSpec("list_notes", "GET", "/notes/", "list", "note",
              params=(P("skip", "int", "query", required=False, default=0),
                      P("limit", "int", "query", required=False, default=100)),
              pagination=("skip", "limit")),
    RouteSpec("read_note", "GET", "/notes/{id}", "read", "note", params=(ID,),
              errors=(ErrorSpec(404, "Note not found", "not_found"),
                      ErrorSpec(403, "Not yours", "not_owner"))),
    RouteSpec("create_note", "POST", "/notes/", "create", "note", params=(TITLE, BODY, STARS)),
    RouteSpec("update_note", "PATCH", "/notes/{id}", "update", "note",
              params=(ID, P("title", "str", "body", required=False, min_len=1, max_len=20),
                      BODY, STARS)),
    RouteSpec("update_me", "PATCH", "/me", "update_me", "user",
              params=(P("email", "email", "body", required=False, max_len=255,
                        column="email"),),
              errors=(ErrorSpec(409, "Email taken", "unique", column="email"),)),
    RouteSpec("list_users", "GET", "/users/", "list", "user", auth="admin",
              errors=(ErrorSpec(403, "Not admin", "not_admin"),)),
    RouteSpec("login", "POST", "/login", "login", "user", auth="public", body="form",
              params=(P("username", "str", "body"), P("password", "str", "body")),
              errors=(ErrorSpec(400, "Incorrect email or password", "bad_credentials"),
                      ErrorSpec(400, "Inactive user", "inactive_login"))),
]  # fmt: skip


def suite():
    return generate_suite(ROUTES, [USER, NOTE], AUTH, seed=7, hypothesis_bodies=4)


def by_name(s):
    return {c.name: c for c in s.cases}


# ---- the seed ------------------------------------------------------------------------------


def test_the_seed_has_a_superuser_active_and_inactive_users():
    plan = seed_plan([USER, NOTE], AUTH, seed=7)
    kinds = {(u.admin, u.active) for u in plan.users}
    assert kinds == {(True, True), (False, True), (False, False)}
    users = plan.rows["user"]
    assert len(users) == len(plan.users) >= 4
    assert all(
        r["hashed_password"] == "hash:" + u.password for r, u in zip(users, plan.users, strict=True)
    )


def test_ownership_is_spread_and_nullable_columns_are_both_null_and_set():
    plan = seed_plan([USER, NOTE], AUTH, seed=7)
    owners = Counter(r["owner_id"] for r in plan.rows["note"])
    assert set(owners) == {u.id for u in plan.users}
    assert min(owners.values()) >= 2
    for table in (USER, NOTE):
        for col in table.columns:
            if col.nullable:
                values = [r[col.name] for r in plan.rows[table.name]]
                assert None in values and any(v is not None for v in values), col.name
    for r in plan.rows["note"]:
        assert 1 <= len(r["title"]) <= 20 and 0 <= r["stars"] <= 5


def test_sql_seed_writes_the_storage_spellings(tmp_path: Path):
    plan = seed_plan([USER, NOTE], AUTH, seed=7)
    db = sql_seed(tmp_path / "s.db", [USER, NOTE], plan, uuid_storage="hex32",
                  datetime_storage="sql_space")  # fmt: skip
    conn = sqlite3.connect(db)
    ids = [r[0] for r in conn.execute("SELECT id FROM note")]
    stamps = [r[0] for r in conn.execute("SELECT created_at FROM note") if r[0]]
    conn.close()
    assert len(ids) == len(plan.rows["note"]) and all(len(i) == 32 for i in ids)
    assert stamps and all(s[10] == " " for s in stamps)


def test_the_seed_is_deterministic():
    assert seed_plan([USER, NOTE], AUTH, seed=7) == seed_plan([USER, NOTE], AUTH, seed=7)


# ---- the cases -----------------------------------------------------------------------------


def test_the_suite_is_deterministic_including_hypothesis_bodies():
    a, b = suite(), suite()
    assert [c for c in a.cases] == [c for c in b.cases]
    assert any(".hyp." in c.name for c in a.cases)


def test_every_case_names_its_route_and_names_are_unique():
    s = suite()
    names = [c.name for c in s.cases]
    assert len(names) == len(set(names))
    routes = {r.name for r in ROUTES}
    assert all(c.route in routes and c.name.startswith(c.route + ".") for c in s.cases)


def test_id_routes_get_own_other_admin_missing_and_malformed_ids():
    s = suite()
    cases = by_name(s)
    plan = s.plan
    alice = plan.user("alice")
    happy = cases["read_note.happy"]
    own = str(plan.owned("note", "alice")[0]["id"])
    assert happy.user == "alice" and happy.path == f"/notes/{own}"
    other = cases["read_note.other_user_row"]
    assert other.user == "alice" and other.path != happy.path
    row_id = other.path.rsplit("/", 1)[1]
    assert any(str(r["id"]) == row_id and r["owner_id"] != alice.id for r in plan.rows["note"])
    assert cases["read_note.admin"].user == "admin"
    assert cases["read_note.missing_id"].path.endswith("00000000-0000-4000-8000-000000000000")
    assert cases["read_note.malformed_id"].path.endswith("/not-a-uuid")


def test_tokens_no_bad_inactive_and_unknown():
    cases = by_name(suite())
    assert cases["read_note.no_token"].user is None and cases["read_note.no_token"].token is None
    assert cases["read_note.bad_token"].token == AUTH.bad_token
    carol = suite().plan.user("carol")
    assert not carol.active
    assert cases["read_note.inactive_token"].token == f"tok-{carol.id}"
    assert cases["read_note.unknown_user_token"].token.startswith("tok-")
    assert "login.no_token" not in cases  # public


def test_boundaries_missing_and_extra_fields():
    cases = by_name(suite())
    assert len(cases["create_note.boundary.title.max"].json["title"]) == 20
    assert len(cases["create_note.boundary.title.above_max"].json["title"]) == 21
    assert cases["create_note.boundary.title.below_min"].json["title"] == ""
    assert cases["create_note.boundary.stars.above_max"].json["stars"] == 6
    assert cases["create_note.boundary.stars.below_min"].json["stars"] == -1
    assert "title" not in cases["create_note.missing.title"].json
    assert "create_note.missing.body" not in cases  # optional
    assert cases["create_note.extra.unknown"].json["unexpected_field"] == "x"
    extra = cases["create_note.extra.columns"].json
    assert {"id", "owner_id", "created_at"} <= set(extra)


def test_partial_updates():
    cases = by_name(suite())
    assert cases["update_note.partial.title"].json == {
        "title": cases["update_note.partial.title"].json["title"]
    }
    assert cases["update_note.partial.body_null"].json == {"body": None}
    assert cases["update_note.partial.empty"].json == {}


def test_pagination_edges():
    cases = by_name(suite())
    params = {n.split(".", 2)[2]: c.params for n, c in cases.items() if ".page." in n}
    assert params["limit_0"] == {"limit": 0}
    assert params["limit_huge"] == {"limit": 1000000}
    assert params["skip_past_end"]["skip"] > 8
    assert params["skip_negative"] == {"skip": -1}
    assert params["limit_malformed"] == {"limit": "many"}


def test_one_case_per_declared_error():
    s = suite()
    cases = by_name(s)
    taken = cases["update_me.error.409.unique"]
    assert taken.user == "alice" and taken.json["email"] == s.plan.user("bob").email
    assert cases["list_users.error.403.not_admin"].user == "alice"
    wrong = cases["login.error.400.bad_credentials"]
    assert wrong.form["username"] == s.plan.user("alice").email
    assert wrong.form["password"] != s.plan.user("alice").password
    inactive = cases["login.error.400.inactive_login"]
    assert inactive.form["username"] == s.plan.user("carol").email
    # a declared error the generic cases already cover is not repeated
    assert "read_note.error.404.not_found" not in cases
    assert "read_note.missing_id" in cases


def test_hypothesis_bodies_stay_within_bounds():
    for c in suite().cases:
        if ".hyp." not in c.name:
            continue
        body = c.json
        if c.route == "create_note":
            assert 1 <= len(body["title"]) <= 20
        if "stars" in body:
            assert 0 <= body["stars"] <= 5
        if body.get("body") is not None:
            assert len(body["body"]) <= 50


def test_login_is_a_form_login_with_the_seed_credentials():
    s = suite()
    assert s.credentials["alice"] == (s.plan.user("alice").email, s.plan.user("alice").password)
    assert by_name(s)["login.happy"].form == {
        "username": s.plan.user("alice").email,
        "password": s.plan.user("alice").password,
    }


def test_hypothesis_bodies_do_not_depend_on_other_loaded_code(tmp_path, monkeypatch):
    import importlib
    import sys

    from lattice.importer.suite import hypothesis_bodies_for

    route = ROUTES[2]  # create_note: str, str and int fields
    before = hypothesis_bodies_for(route, 60, 31)
    # a newly loaded local module full of literals hypothesis would otherwise mine
    (tmp_path / "noise_constants.py").write_text(
        "WORDS = " + repr([f"noise-{i}" for i in range(200)]) + "\nN = (12345, 67890, -4242)\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.import_module("noise_constants")
    try:
        after = hypothesis_bodies_for(route, 60, 31)
        assert not [b for b in after if "noise-" in repr(b)]
        assert after == before
    finally:
        sys.modules.pop("noise_constants", None)
