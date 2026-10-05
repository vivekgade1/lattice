"""lattice.importer.rules (Phase 2 W3): the seed's transforms, the corrections of refuted
items, and the app-factory convention. The whole pipeline on the dev apps is
``test_phase2_dev_apps.py``."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from lattice.importer import rules
from lattice.importer.appmodel import AuthModel, FieldSpec, RepoModel, RoleScheme, TableModel
from lattice.importer.suite import AuthSpec, ColumnSpec, SeedPlan, SeedUser, TableSpec, seed_plan
from lattice.profile import AdminFlag, ApiProfile, Profile, TokenColumn
from lattice.spec import Contract, ContractError, ContractParam, Spec
from lattice.spec.probes import ItemVerdict, RuleItem, RuleResult
from lattice.typesys import Scalar, Schema

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---- the app factory ----------------------------------------------------------------------------


def test_the_app_factory_is_the_nearest_make_app():
    factory, name = rules.find_factory("bench.import_repos.multivendor.app")
    assert name == "bench.import_repos.multivendor:make_app" and callable(factory)
    _, given = rules.find_factory("x", "bench.import_repos.multivendor:make_app")
    assert given == "bench.import_repos.multivendor:make_app"
    with pytest.raises(LookupError):
        rules.find_factory("json")


# ---- the seed -----------------------------------------------------------------------------------

USERS = TableSpec("users", (ColumnSpec("id", "int", primary_key=True),
                            ColumnSpec("email", "email", unique=True),
                            ColumnSpec("hashed_password", "str"),
                            ColumnSpec("is_active", "bool"),
                            ColumnSpec("is_superuser", "bool")))  # fmt: skip
VENDORS = TableSpec("vendors", (ColumnSpec("id", "int", primary_key=True),
                                ColumnSpec("user_id", "int", foreign_key="users.id",
                                           unique=True)), owner="user_id")  # fmt: skip
PRODUCTS = TableSpec("products", (ColumnSpec("id", "int", primary_key=True),
                                  ColumnSpec("vendor_id", "int", foreign_key="vendors.id"),
                                  ColumnSpec("stock", "int", ge=0),
                                  ColumnSpec("price", "float", ge=0)))  # fmt: skip
FOLLOWS = TableSpec("follows", (ColumnSpec("follower_id", "int", primary_key=True,
                                           foreign_key="users.id"),
                                ColumnSpec("followed_id", "int", primary_key=True,
                                           foreign_key="users.id")))  # fmt: skip
AUTH = AuthSpec("users", key_type="int")
TABLES = (USERS, VENDORS, PRODUCTS, FOLLOWS)


def _plan() -> SeedPlan:
    return seed_plan(TABLES, AUTH, rows_per_table=8)


def test_foreign_keys_spread_over_every_parent_row():
    plan = _plan()
    rules._spread_keys(plan, TABLES, AUTH, None)
    vendors = {r["id"] for r in plan.rows["vendors"]}
    assert {r["vendor_id"] for r in plan.rows["products"]} == vendors


def test_a_key_of_two_foreign_keys_is_distinct_pairs_never_a_self_pair():
    plan = _plan()
    rules._spread_keys(plan, TABLES, AUTH, None)
    pairs = [(r["follower_id"], r["followed_id"]) for r in plan.rows["follows"]]
    assert len(pairs) == len(set(pairs)) and pairs
    assert all(a != b for a, b in pairs)
    assert len({a for a, _ in pairs}) >= 2  # owned rows for several users


def test_numbers_sit_well_inside_their_bounds():
    plan = _plan()
    rules._comfortable(plan, TABLES, AUTH)
    assert min(r["stock"] for r in plan.rows["products"]) >= rules.COMFORT
    assert all(isinstance(r["stock"], int) for r in plan.rows["products"])
    assert all(isinstance(r["price"], float) for r in plan.rows["products"])


def test_variant_two_shifts_integer_keys_and_their_references():
    plan = _plan()
    rules._spread_keys(plan, TABLES, AUTH, None)
    before = {r["id"]: r["user_id"] for r in plan.rows["vendors"]}
    rules._shift_keys(plan, TABLES, AUTH, 100, keep=None)
    assert min(r["id"] for r in plan.rows["vendors"]) == 101
    assert min(r["id"] for r in plan.rows["users"]) == 51
    assert {u.id for u in plan.users} == {r["id"] for r in plan.rows["users"]}
    after = {r["id"] - 100: r["user_id"] - 50 for r in plan.rows["vendors"]}
    assert after == before
    assert all(r["vendor_id"] > 100 for r in plan.rows["products"])
    assert all(r["follower_id"] > 50 for r in plan.rows["follows"])


def _role_model() -> RepoModel:
    role = TableModel("Role", "roles", "m", (
        FieldSpec("id", "int", "Int", primary_key=True),
        FieldSpec("name", "str", "Str", unique=True),
        FieldSpec("default", "bool", "Bool"),
        FieldSpec("permissions", "int", "Int"),
    ), ())  # fmt: skip
    user = TableModel("User", "users", "m", (
        FieldSpec("id", "int", "Int", primary_key=True),
        FieldSpec("email", "str", "Str", unique=True),
        FieldSpec("username", "str", "Str", unique=True),
        FieldSpec("role_id", "int", "Int", foreign_key="roles.id"),
    ), ())  # fmt: skip
    auth = AuthModel(users_table="User", email_column="email", username_field="username",
                     role_column="role_id", role_table="Role",
                     permissions=(("P.READ", 1), ("P.WRITE", 2), ("P.ADMIN", 4)),
                     role_permissions=(("User", ("P.READ",)), ("Moderator", ("P.READ", "P.WRITE")),
                                       ("Admin", ("P.READ", "P.WRITE", "P.ADMIN"))))  # fmt: skip
    roles = (("User", "1", "User"), ("Moderator", "2", "Moderator"),
             ("Administrator", "3", "Admin"))  # fmt: skip
    scheme = RoleScheme("role_id", roles, "Admin", 403, "nope")
    return RepoModel("m", (role, user), (), (), (), (), None, "sqlite", (), (), role_scheme=scheme,
                     framework="flask", orm="sqlalchemy", auth=auth)  # fmt: skip


def test_roles_kept_as_rows_get_one_row_per_role_with_its_permissions():
    model = _role_model()
    roles = TableSpec("roles", (ColumnSpec("id", "int", primary_key=True),
                                ColumnSpec("name", "str", unique=True),
                                ColumnSpec("default", "bool"),
                                ColumnSpec("permissions", "int")))  # fmt: skip
    users = TableSpec("users", (ColumnSpec("id", "int", primary_key=True),
                                ColumnSpec("email", "email", unique=True),
                                ColumnSpec("username", "str", unique=True),
                                ColumnSpec("role_id", "int", foreign_key="roles.id"),
                                ColumnSpec("hashed_password", "str")))  # fmt: skip
    auth = AuthSpec("users", key_type="int", active_column=None, admin_column=None)
    seeder = rules.Seeder(model, (roles, users), auth, None, None, None)  # type: ignore[arg-type]
    plan = seeder.plan()
    got = [(r["id"], r["name"], r["permissions"], r["default"]) for r in plan.rows["roles"]]
    assert got == [(1, "User", 1, True), (2, "Moderator", 3, False),
                   (3, "Administrator", 7, False)]  # fmt: skip
    by_handle = {u.handle: row for u, row in zip(plan.users, plan.rows["users"], strict=True)}
    assert by_handle["admin"]["role_id"] == 3 and by_handle["alice"]["role_id"] == 1
    assert by_handle["role_moderator"]["role_id"] == 2
    assert by_handle["alice"]["username"] == "alice"  # the login column holds the handle
    assert seeder.credentials()["alice"][0] == "alice"
    shifted = seeder.plan("variant2")
    assert [r["id"] for r in shifted.rows["roles"]] == [1, 2, 3]  # role keys are spellings


# ---- corrections --------------------------------------------------------------------------------


def _spec(*contracts: Contract) -> Spec:
    users = Schema.from_pairs([("id", Scalar.INT), ("token", Scalar.STR),
                               ("is_admin", Scalar.BOOL), ("secret", Scalar.STR)],
                              keys=[("id",)], private=["secret"])  # fmt: skip
    api = ApiProfile(tables={"User": users}, table_names={"User": "users"},
                     roles=frozenset({"Public", "Customer", "Admin"}), users_table="User",
                     auth=TokenColumn("token"),
                     roles_from=AdminFlag("is_admin", "Customer"))  # fmt: skip
    return Spec(Profile(schemas={}, api=api), contracts=contracts)


def _verdict(item_id: str, kind: str, subject: str, observed, predicted=None, witness=None):
    return ItemVerdict(RuleItem(item_id, kind, subject, "s", predicted or {}), "refuted", "r",
                       ["p"], observed, witness=witness)  # fmt: skip


def test_auth_for_spells_the_admitted_callers():
    api = _spec().profile.api
    assert rules.auth_for(["anonymous", "Admin", "Customer"], api) == "Public"
    assert rules.auth_for(["Admin", "Customer"], api) == "Authenticated"
    assert rules.auth_for(["Admin"], api) == "Admin"
    assert rules.auth_for(["Customer"], api) == "Roles(Customer)"
    assert rules.auth_for(["malformed token"], api) is None


def test_refuted_route_items_are_corrected_from_what_was_observed():
    c = Contract("GET", "/x", auth="Admin", table="User", kind="list", owner_scope="owner",
                 params=(ContractParam("page", "query", Scalar.INT, ge=1),),
                 errors=(ContractError(400, when="invalid"),))  # fmt: skip
    spec = _spec(c)
    result = RuleResult()
    result.items = {
        "R1 GET /x": _verdict("R1 GET /x", "R1", "GET /x", {"allowed": ["Admin", "Customer"]}),
        "R2 GET /x": _verdict("R2 GET /x", "R2", "GET /x", {"other_users_rows": [3]}),
        "R3 GET /x 400": _verdict("R3 GET /x 400", "R3", "GET /x",
                                  {"status": 404, "detail": "Invalid page."}),
        "R3 GET /x 5xx": _verdict("R3 GET /x 5xx", "R3", "GET /x", {"status": 500},
                                  witness="w"),
        "T2 User.secret": _verdict("T2 User.secret", "T2", "User", {"shown_by": ["w"]},
                                   {"private": "secret"}),
    }  # fmt: skip
    result.probes = {"w": {"request": {"method": "GET", "path": "/x", "user": "alice",
                                       "params": {"tags": "x"}}, "body": "boom"}}  # fmt: skip
    fixed, corrections = rules.correct(spec, result)
    got = fixed.contracts[0]
    assert got.auth == "Authenticated" and got.owner_scope == "any"
    assert got.errors[0] == ContractError(404, "Invalid page.", when="invalid")
    assert got.errors[-1].status == 500 and got.errors[-1].example == {
        "path": "/x", "params": {"tags": "x"}, "user": "alice"}  # fmt: skip
    assert "secret" not in fixed.profile.api.tables["User"].private
    by_item = {c.item: c for c in corrections}
    assert by_item["R3 GET /x 400"].corrected == "R3 GET /x 404 Invalid page."
    assert by_item["R3 GET /x 5xx"].corrected == "R3 GET /x 500"
    assert by_item["R3 GET /x 5xx"].baseline["reason"].startswith("upstream defect")
    assert by_item["T2 User.secret"].corrected is None
    assert spec.contracts[0].auth == "Admin"  # the candidate itself is unchanged


def test_uncorrectable_refutations_are_left_alone():
    spec = _spec(Contract("GET", "/x", auth="Admin"))
    result = RuleResult()
    result.items = {"T3 User.id ge 1": _verdict("T3 User.id ge 1", "T3", "User", {"status": 403})}
    fixed, corrections = rules.correct(spec, result)
    assert corrections == [] and fixed.contracts == spec.contracts


def test_seed_users_replace_works_on_frozen_users():
    plan = SeedPlan((SeedUser("a", "a@x", "p", True, False, 1),), {"users": [{"id": 1}]})
    rules._shift_keys(plan, (TableSpec("users", (ColumnSpec("id", "int", primary_key=True),)),),
                      AuthSpec("users", key_type="int"), 10, keep=None)  # fmt: skip
    assert plan.users[0].id == 6 and replace(plan.users[0], id=1).id == 1


# ---- routes that need the network --------------------------------------------------------------


def test_routes_reaching_a_network_client_are_unreachable_by_probe():
    from lattice.importer.extract import extract

    notes = extract(REPO_ROOT, "bench.import_repos.justnotes.app")
    found = rules.network_routes(REPO_ROOT, "bench.import_repos.justnotes.app", notes)
    assert "GET /notes/{id}/summarize" in found
    assert found["GET /notes/{id}/summarize"].startswith("needs the network")
    assert "GET /notes/{id}" not in found
    template = extract(REPO_ROOT, "bench.template_app.app")
    assert rules.network_routes(REPO_ROOT, "bench.template_app.app", template) == {}


def test_a_route_behind_an_earlier_one_of_the_same_shape_is_shadowed():
    from lattice.importer.extract import extract

    notes = extract(REPO_ROOT, "bench.import_repos.justnotes.app")
    found = rules.shadowed_routes(notes)
    assert list(found) == ["GET /notes/{note_id}/keywords"]
    assert "GET /notes/{id}/keywords" in found["GET /notes/{note_id}/keywords"]


def test_a_route_taking_a_file_is_an_upload_and_its_unexercised_items_say_so():
    from lattice.importer.appmodel import DataModel, Route, RouteParam

    image = DataModel("RecipeImageSerializer", "m", (FieldSpec("image", "ImageField", None),), ())
    params = (
        RouteParam("pk", "int", "Int", "path"),
        RouteParam("data", "RecipeImageSerializer", "RecipeImageSerializer", "body"),
    )
    route = Route("upload", "m", "POST", "/recipes/{pk}/upload-image/", params, None, None,
                  None, (), "Customer", "", (), (), (), (), (), 1)  # fmt: skip
    model = RepoModel("m", (), (image,), (), (route,), (), None, "sqlite", (), ())
    assert rules.upload_routes(model) == {"POST /recipes/{pk}/upload-image/": "image"}
    result = RuleResult()
    item = RuleItem("R3 POST /recipes/{pk}/upload-image/ 400", "R3",
                    "POST /recipes/{pk}/upload-image/", "s")  # fmt: skip
    result.items = {item.id: ItemVerdict(item, "unexercised", "no example triggers it")}
    rules.mark_uploads(result, rules.upload_routes(model))
    assert result.items[item.id].reason.startswith("unreachable-by-probe: a multipart file upload")


def test_without_a_usable_login_route_the_front_ends_minter_makes_the_tokens():
    """simplejwt behind an OTP login (drf_blog_api's kind): no login route the probes
    can call, so each user's token comes from the front end's minter, by users key."""
    from types import SimpleNamespace

    minted: list[object] = []

    class FrontEnd:
        def minter(self, auth: AuthModel) -> object:
            def mint(key: object) -> str:
                minted.append(key)
                return f"jwt-{key}"

            return mint

    model = RepoModel("m", (), (), (), (), (), None, "sqlite", (), (), framework="django",
                      auth=AuthModel(users_table="User", scheme="jwt"))  # fmt: skip
    result = SimpleNamespace(model=model, context=SimpleNamespace(frontend=FrontEnd()))
    seeder = rules.Seeder(model, (USERS,), AUTH, None, None, None)  # type: ignore[arg-type]
    who = rules.callers(result, seeder)
    assert who.login is not None and who.login(None, "alice") == "jwt-2"
    assert who.headers("t") == {"Authorization": "JWT t"} and who.inactive_login
