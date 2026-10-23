"""The rules-first import for a .NET app (``lattice.importer.dotnet_rules``, the ``rules.py``
branches): the inferences on synthetic models, the K4 skip, and the rows that run ``dncheck`` on
the parity fixture. The D1 end-to-end import is in ``test_dotnet_d1`` below (needs the SDK)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lattice.importer import dotnet_rules as dr
from lattice.importer.appmodel import (
    AuthModel,
    FieldSpec,
    OwnerUse,
    RepoModel,
    Route,
    TableModel,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "dncheck" / "fixtures"
HAS_DOTNET = shutil.which("dotnet") is not None


def col(name, type_="Int", pk=False, fk=None):
    return FieldSpec(name=name, annotation=type_, type=type_, primary_key=pk, foreign_key=fk)


def table(name, sql, *cols):
    return TableModel(name=name, table_name=sql, module="m", columns=tuple(cols), bases=())


def route(*uses):
    return Route(
        name="r", module="m", method="GET", path="/x", params=(), response_model=None,
        response_class=None, status_code=200, dependencies=(), auth="Customer", auth_reason="",
        raises=(), calls=(), tables=(), owner_uses=tuple(uses), body_models=(), line=1,
    )  # fmt: skip


def use(entity, column, how="compare"):
    return OwnerUse(entity, column, how, 3)


def raw(*uses):
    """The extract's routes: ``uses`` are (entity, column, how)."""
    return {"routes": [{"owner_uses": [
        {"model": e, "column": c, "how": h, "file": "A.cs", "line": 3} for e, c, h in uses
    ]}]}  # fmt: skip


def model(*tables, routes=()):
    return RepoModel(
        app_package="x", tables=tuple(tables), models=(), dependencies=(), routes=tuple(routes),
        natives=(), database_url=None, dialect=None, exception_handlers=(),
        custom_serialization=(), auth=AuthModel(users_table="User"),
    )  # fmt: skip


USERS = table("User", "users", col("id", pk=True))


def test_owner_declared_by_ef_core_is_confirmed_without_changing_the_model():
    note = table("Note", "notes", col("id", pk=True), col("user_id", fk="users.id"))
    m = model(USERS, note)
    out, found = dr.infer_owners(raw(("Note", "user_id", "compare")), m)
    assert out.tables == m.tables
    assert found == [{"table": "Note", "column": "user_id", "rule": "owner_uses", "declared": True,
                      "evidence": ["code:A.cs:3"]}]  # fmt: skip


def test_owner_without_a_declared_key_gets_the_inferred_foreign_key():
    note = table("Note", "notes", col("id", pk=True), col("OwnerId"))
    out, found = dr.infer_owners(raw(("Note", "OwnerId", "assign")), model(USERS, note))
    assert [(i["table"], i["column"]) for i in found] == [("Note", "OwnerId")]
    assert next(t for t in out.tables if t.name == "Note").columns[1].foreign_key == "users.id"


def test_owner_confirmed_among_several_users_keys_is_stated_on_the_model():
    # EF Core declares a foreign key for every navigation: a report has its author and its reviewer
    rep = table("Report", "reports", col("id", pk=True), col("user_id", fk="users.id"),
                col("reviewer_id", fk="users.id"))  # fmt: skip
    out, found = dr.infer_owners(raw(("Report", "user_id", "compare")), model(USERS, rep))
    assert found[0]["column"] == "user_id" and found[0]["declared"] is True
    assert next(t for t in out.tables if t.name == "Report").owner == "user_id"
    assert next(t for t in out.tables if t.name == "User").owner is None


def test_two_evidenced_users_keys_state_no_owner():
    rep = table("Report", "reports", col("id", pk=True), col("user_id", fk="users.id"),
                col("reviewer_id", fk="users.id"))  # fmt: skip
    out, found = dr.infer_owners(
        raw(("Report", "user_id", "compare"), ("Report", "reviewer_id", "assign")),
        model(USERS, rep),
    )  # fmt: skip
    assert found[0].get("unclear") and next(t for t in out.tables if t.name == "Report").owner is None


def _plan(tbl, rts=()):
    from lattice.importer.profile_gen import _Generator, _TablePlan

    m = model(USERS, tbl, routes=rts)
    g = _Generator(m)
    plan = _TablePlan(tbl, [], [], [])
    g.owner(plan, "users")
    return g, plan


def _scoped_route(table_name, column):
    r = route(use(table_name, column))
    return Route(**{**r.__dict__, "tables": (table_name,)})


def test_the_profile_takes_the_owner_the_front_end_confirmed_among_several_users_keys():
    cols = (col("id", pk=True), col("user_id", fk="users.id"), col("reviewer_id", fk="users.id"))
    unhinted = table("Report", "reports", *cols)
    g, plan = _plan(unhinted, [_scoped_route("Report", "user_id")])
    assert plan.owner is None and "2 foreign keys" in plan.ownership_reason  # as before
    hinted = TableModel("Report", "reports", "m", cols, (), owner="user_id")
    g, plan = _plan(hinted, [_scoped_route("Report", "user_id")])
    assert plan.owner == "user_id" and plan.ownership == "owner"


def test_a_hint_does_not_override_a_route_comparing_another_users_key():
    cols = (col("id", pk=True), col("user_id", fk="users.id"), col("reviewer_id", fk="users.id"))
    hinted = TableModel("Report", "reports", "m", cols, (), owner="user_id")
    g, plan = _plan(hinted, [_scoped_route("Report", "user_id"), _scoped_route("Report", "reviewer_id")])
    assert plan.owner is None and plan.ownership == "unclear"


def test_a_global_query_filter_is_owner_evidence():
    todo = table("Todo", "Todos", col("id", pk=True), col("OwnerId"))
    _, found = dr.infer_owners(raw(("Todo", "OwnerId", "filter")), model(USERS, todo))
    assert found[0]["column"] == "OwnerId"


def test_two_candidates_are_unclear_and_both_named():
    t = table("Note", "notes", col("id", pk=True), col("A"), col("B"))
    out, found = dr.infer_owners(raw(("Note", "A", "compare"), ("Note", "B", "compare")),
                                 model(USERS, t))  # fmt: skip
    assert found[0]["unclear"] and found[0]["columns"] == ["A", "B"]
    assert all(c.foreign_key == "users.id" for c in out.tables[1].columns[1:])


def test_a_column_of_another_type_is_no_ownership_evidence_next_to_the_owner():
    t = table("Device", "devices", col("id", pk=True), col("user_id", fk="users.id"),
              col("installation_id", "Str"))  # fmt: skip
    r = route(use("Device", "user_id"), use("Device", "installation_id"))
    out, _ = dr.infer_owners(raw(("Device", "user_id", "compare"),
                                 ("Device", "installation_id", "compare")),
                             model(USERS, t, routes=[r]))  # fmt: skip
    assert [o.column for o in out.routes[0].owner_uses] == ["user_id"]


def test_the_users_table_and_a_model_without_auth_have_no_owner_inference():
    users_with_uses = raw(("User", "id", "compare"))
    assert dr.infer_owners(users_with_uses, model(USERS))[1] == []
    bare = model(USERS)
    bare = RepoModel(**{**bare.__dict__, "auth": None})
    assert dr.infer_owners(users_with_uses, bare)[1] == []


def test_route_constraints_are_recorded_with_their_evidence():
    data = {"routes": [{"method": "GET", "path_template": "/n/{id}", "params": [
        {"name": "id", "source": "path", "type": "Int", "parse": "Int", "parse_at": "A.cs:8"},
        {"name": "k", "source": "path", "type": "Str", "parse": None},
        {"name": "q", "source": "query", "type": "Int", "parse": "Int"}]}]}  # fmt: skip
    assert dr.infer_constraints(data) == [{"rule": "route_constraint", "route": "GET /n/{id}",
                                           "param": "id", "type": "Int",
                                           "evidence": ["code:A.cs:8"]}]  # fmt: skip


def test_bind_status_is_the_refusals_then_the_modules_own():
    refusals = {"routes": [{"raises": [{"status": 422, "bind": True, "file": "A.cs", "line": 1}]}],
                "dotnet": {"bind_status": 400}}  # fmt: skip
    assert dr.infer_bind(refusals)[0] == 422
    assert dr.infer_bind({"routes": [], "dotnet": {"bind_status": 400}}) == (
        400, [{"rule": "bind_status", "status": 400, "evidence": ["dotnet.bind_status"]}],
    )  # fmt: skip
    assert dr.infer_bind({"routes": [], "dotnet": {"bind_status": 418}}) == (None, [])
    assert dr.infer_bind({"routes": []}) == (None, [])


@pytest.mark.parametrize(
    ("name", "policy", "wire"),
    [
        ("PasswordHash", "CamelCase", "passwordHash"),
        ("ID", "CamelCase", "id"),
        ("PasswordHash", "SnakeCaseLower", "password_hash"),
        ("HTTPStatus", "SnakeCaseLower", "http_status"),
        ("PasswordHash", "SnakeCaseUpper", "PASSWORD_HASH"),
        ("PasswordHash", "KebabCaseLower", "password-hash"),
        ("PasswordHash", "KebabCaseUpper", "PASSWORD-HASH"),
        ("PasswordHash", None, "PasswordHash"),
        ("PasswordHash", "Pascal", "PasswordHash"),
    ],
)
def test_wire_name_follows_the_naming_policy(name, policy, wire):
    assert dr.wire_name(name, policy) == wire


def entity(struct, *cols):
    return {"struct": struct, "file": "E.cs", "columns": [
        {"name": n, "field": f, "wire": w, "private": p, "primary_key": False, "line": 5,
         "choices": ch} for n, f, w, p, ch in cols]}  # fmt: skip


def test_private_columns_and_json_naming():
    pol = {"dotnet": {"json_naming": "CamelCase", "json_naming_by_style": {}},
           "tables": [entity("User", ("PasswordHash", "PasswordHash", None, True, None),
                             ("Email", "Email", "email", False, None))]}  # fmt: skip
    private, found = dr.infer_private(pol)
    assert private == {"User": ["PasswordHash"]}
    assert found[0]["rule"] == "json_ignore" and found[0]["evidence"] == ["code:E.cs:5"]
    (naming,) = dr.infer_naming(pol)
    assert naming["naming"] == "CamelCase"
    assert naming["renamed"] == {"User": {"PasswordHash": "passwordHash", "Email": "email"}}
    assert dr.infer_naming({"dotnet": {}, "tables": []}) == []


def test_columns_no_response_returns_are_private_next_to_json_ignore():
    pol = {"tables": [{"struct": "User", "file": "E.cs", "columns": [
        {"name": "id", "field": "Id", "wire": "id", "json": "id", "private": False,
         "primary_key": True, "line": 4, "choices": None},
        {"name": "password", "field": "Password", "wire": None, "json": "-", "private": False,
         "primary_key": False, "line": 5, "choices": None},
        {"name": "token", "field": "Token", "wire": None, "json": "-", "private": True,
         "primary_key": False, "line": 6, "choices": None},
        {"name": "email", "field": "Email", "wire": "email", "json": "email", "private": False,
         "primary_key": False, "line": 7, "choices": None}]}]}  # fmt: skip
    private, found = dr.infer_private(pol)
    assert private == {"User": ["password", "token"]}
    assert {f["rule"]: f["columns"] for f in found} == {"json_ignore": ["token"], "json_dash": ["password"]}
    assert next(f for f in found if f["rule"] == "json_dash")["evidence"] == ["code:E.cs:5"]


def test_enum_members_are_recorded_as_choices():
    data = {"tables": [entity("Note", ("Status", "Status", "status", False,
                                       ["Draft", "Published"]))],
            "status_writes": [], "status_facts": []}  # fmt: skip
    _, found = dr.infer_choices_dn(data, data, model(table("Note", "notes", col("Status", "Str"))))
    assert found[0]["rule"] == "enum_choices" and found[0]["choices"] == ["Draft", "Published"]


def test_status_facts_choices_are_relabelled_enum_choices():
    status = FieldSpec(name="delivery", annotation="string", type="Str")
    t = TableModel("Nudge", "nudges", "m", (status,), ())
    data = {"tables": [entity("Nudge", ("delivery", "Delivery", "delivery", False, None))],
            "status_writes": [
                {"model": "Nudge", "column": "delivery", "value": "suppressed",
                 "file": "N.cs", "line": 2}
            ]}  # fmt: skip
    workflow = {"status_facts": [{"model": "Nudge", "column": "delivery", "from": ["pending"],
                                  "file": "N.cs", "line": 4}]}  # fmt: skip
    out, found = dr.infer_choices_dn(data, workflow, model(t))
    assert out.tables[0].columns[0].choices == ("pending", "suppressed")
    assert [i["rule"] for i in found] == ["enum_choices"]


def test_a_server_database_skips_layer_r(tmp_path):
    (tmp_path / "studio.toml").write_text('[app]\ndatabase = "mysql"\n')
    assert dr.server_dialect(tmp_path, ".") == "mysql"
    (tmp_path / "studio.toml").write_text('[app]\ndb = "m:build_db"\n')
    assert dr.server_dialect(tmp_path, ".") is None
    assert dr.harness_factory(tmp_path, ".") is None


# ---- with the SDK: the parity fixture's inferences and the D1 import --------------------------

needs_dotnet = pytest.mark.skipif(not HAS_DOTNET, reason="needs the .NET SDK")
D1_TGZ = REPO / "docs" / "results" / "data" / "2026-10-18-dotnet" / "d1.tgz"


@pytest.fixture(scope="module", autouse=True)
def _cache(tmp_path_factory):
    import os

    os.environ.setdefault("LATTICE_DNCHECK_CACHE", str(tmp_path_factory.mktemp("dncheck-cache")))


@needs_dotnet
def test_the_parity_fixture_inferences():
    from lattice.importer.frontends import select

    fe = dr.InferringFrontEnd(select("aspnetcore"))
    fe.extract(FIXTURE, "dnparity1")
    by_rule = {}
    for i in fe.inferences:
        by_rule.setdefault(i["rule"], []).append(i)
    owners = {(i["table"], i["column"]) for i in by_rule["owner_uses"]}
    assert owners == {("Note", "OwnerId"), ("Todo", "OwnerId")}  # Todo: the query filter
    assert fe.private == {"User": ["PasswordHash"], "Note": ["ModerationNote"]}
    assert fe.validation_status == 400
    assert {i["route"] for i in by_rule["route_constraint"]} >= {"GET /api/Notes/{id}"}
    assert len(by_rule["json_naming"]) == 1
    assert {"Note"} == {i["table"] for i in by_rule["enum_choices"]}


def _import(root: Path, out: Path, scratch: Path, timeout: int):
    import os
    import subprocess
    import sys

    env = {
        **os.environ,
        "PYTHONPATH": f"{scratch}{os.pathsep}{REPO}",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    code = "import sys; from lattice.importer.cli import main; sys.exit(main(sys.argv[1:]))"
    return subprocess.run(
        [sys.executable, "-c", code, str(root), "--package", ".", "--rules-only",
         "--framework", "aspnetcore", "--out", str(out)],
        capture_output=True, text=True, cwd=scratch, env=env, timeout=timeout, check=False,
    )  # fmt: skip


@pytest.fixture(scope="module")
def d1(tmp_path_factory) -> Path:
    """D1 extracted where ``tests.spec.dnstreak_harness`` resolves (a scratch ``tests/spec``)."""
    import tarfile

    if not D1_TGZ.is_file():
        pytest.skip("d1.tgz is not in this checkout")
    scratch = tmp_path_factory.mktemp("d1")
    with tarfile.open(D1_TGZ) as tar:
        # the archive carries macOS AppleDouble files (``._X.cs``): a build would compile them
        members = [m for m in tar.getmembers() if not Path(m.name).name.startswith("._")]
        tar.extractall(scratch, members=members, filter="data")
    tree = scratch / "out"
    (tree / "tests" / "spec" / "__init__.py").write_text("")
    return tree


@needs_dotnet
def test_a_server_database_skips_layer_r_for_d1(d1, tmp_path):
    import json
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(d1 / "tests" / "spec" / "dnstreak", copy / "dnstreak")
    with (copy / "dnstreak" / "studio.toml").open("a") as f:
        f.write('database = "mysql"\n')
    out = tmp_path / "out"
    proc = _import(copy / "dnstreak", out, d1, 300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    rules = json.loads((out / "report.json").read_text())["rules"]
    assert rules["status"] == "not_run:server_dialect" and rules["dialect"] == "mysql"
    assert rules["items"] == {}
    assert (out / "candidate_spec.py").read_text().count("\n    Contract(") == 26
    assert not list(out.rglob("*.db")) and not list(out.rglob("*.sqlite"))


@pytest.mark.slow
@needs_dotnet
def test_the_rules_first_import_of_d1_on_sqlite(d1, tmp_path):
    import json

    out = tmp_path / "out"
    proc = _import(d1 / "tests" / "spec" / "dnstreak", out, d1, 1500)
    assert proc.returncode == 0, proc.stderr[-2000:]
    rules = json.loads((out / "report.json").read_text())["rules"]
    assert rules["framework"] == "aspnetcore"
    assert (out / "candidate_spec.py").read_text().count("\n    Contract(") == 26
    assert rules["summary"]["tables"]["total"] == 9
    kinds = [i["rule"] for i in rules["inferences"]]
    assert kinds.count("owner_uses") == 4 and kinds.count("enum_choices") == 1
    assert rules["summary"]["items"]["refuted"] <= 4


H1_TGZ = REPO / "docs" / "results" / "data" / "2026-10-18-dotnet" / "h1.tgz"


@pytest.fixture(scope="module")
def h1(tmp_path_factory) -> Path:
    """The held-out service (``outlay``) where ``tests.spec.outlay_harness`` resolves."""
    import tarfile

    if not H1_TGZ.is_file():
        pytest.skip("h1.tgz is not in this checkout")
    scratch = tmp_path_factory.mktemp("h1")
    with tarfile.open(H1_TGZ) as tar:
        members = [m for m in tar.getmembers() if not Path(m.name).name.startswith("._")]
        tar.extractall(scratch, members=members, filter="data")
    (scratch / "tests" / "spec" / "__init__.py").write_text("")
    return scratch


@pytest.mark.slow
@needs_dotnet
def test_the_rules_first_import_of_h1_exercises_most_items(h1, tmp_path):
    """Node F1: H1 verified 3 of 77 items (its login route went unseen, its tokens belong to one
    process, its auth handler's 401 and 403 were not the unauthenticated and forbidden answers, its
    admin could not be seeded, its account number has a pattern). Development data: the share is
    read, not gated at the held-out value."""
    import json

    out = tmp_path / "out"
    proc = _import(h1 / "tests" / "spec" / "outlay", out, h1, 1500)
    assert proc.returncode == 0, proc.stderr[-2000:]
    rules = json.loads((out / "report.json").read_text())["rules"]
    items = rules["items"]
    verified = [i for i, v in items.items() if v["verdict"] in ("verified", "verified (corrected)")]
    assert len(items) >= 77 and len(verified) / len(items) >= 0.50
    assert rules["summary"]["items"]["refuted"] == 0
    # node F5: the owner the code confirms among the report's two users keys (author, reviewer),
    # its lines' owner through it, and the columns no response reads, are in the candidate spec
    profile = (out / "profile.py").read_text()
    assert "owner='user_id'" in profile and "private=['created_at', 'password']" in profile
    assert "private=['account_number', 'user_id']" in profile
    assert "owner_via=('report_id', 'ExpenseReport')" in profile
    # the Member routes' login requirement is verified (node F5: HM7-8 are judged by these items); the two admin
    # routes' R1 items are unexercised on this tree (they were already on the merged base, before F5)
    assert items["R1 GET /reports"]["verdict"] == "verified"
    assert items["R1 POST /payout-methods"]["verdict"] == "verified"
