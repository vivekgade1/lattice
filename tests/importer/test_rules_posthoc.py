"""Importer fixes of the post-hoc round after the Phase 2 held-out run (2026-10-05), on
toy models (no held-out app):

* the seed's table order follows the foreign keys (a users table declared after the
  tables referencing it is inserted first); a cycle is broken at a nullable foreign key
  (set after every row is in), else reported, never a crash;
* a table is set outside the API only on complete evidence: a route whose handler body
  the front end did not read (``Route.unread``) vetoes every exclusion, and says why."""

from __future__ import annotations

from lattice.importer import rules
from lattice.importer.appmodel import RepoModel, Route, TableModel
from lattice.importer.suite import ColumnSpec, TableSpec, fk_order


def _t(name: str, *fks: tuple[str, str, bool]) -> TableSpec:
    cols = [ColumnSpec("id", "int", primary_key=True)]
    cols += [ColumnSpec(col, "int", nullable=null, foreign_key=f"{target}.id")
             for col, target, null in fks]  # fmt: skip
    return TableSpec(name, tuple(cols))


def test_tables_are_seeded_after_the_tables_they_reference():
    blog = _t("blog_blog", ("author_id", "account_user", False))
    comment = _t(
        "blog_comment", ("blog_id", "blog_blog", False), ("user_id", "account_user", False)
    )
    users = _t("account_user")
    order = fk_order([blog, comment, users])
    assert order.order == ["account_user", "blog_blog", "blog_comment"]
    assert order.deferred == [] and order.cycle is None


def test_a_self_reference_does_not_block_the_order():
    tree = _t("node", ("parent_id", "node", True))
    assert fk_order([tree]).order == ["node"]


def test_a_cycle_is_broken_at_a_nullable_foreign_key():
    a = _t("a", ("b_id", "b", False))
    b = _t("b", ("a_id", "a", True))
    order = fk_order([a, b])
    assert order.order == ["b", "a"]
    assert order.deferred == [("b", "a_id")]
    assert order.cycle is None


def test_a_cycle_without_a_nullable_foreign_key_is_reported_not_raised():
    a = _t("a", ("b_id", "b", False))
    b = _t("b", ("a_id", "a", False))
    order = fk_order([a, b])
    assert sorted(order.order) == ["a", "b"]
    assert order.cycle == ["a", "b"]


def test_every_role_a_caller_can_hold_admitted_is_authenticated():
    from dataclasses import replace

    from lattice.profile import AdminFlag, ApiProfile, NoRoles, TokenColumn

    api = ApiProfile(tables={}, table_names={}, roles=frozenset({"Public", "Customer", "Admin"}),
                     auth=TokenColumn("token"), roles_from=NoRoles("Customer"))  # fmt: skip
    assert rules.auth_for(["Customer"], api) == "Authenticated"
    flag = replace(api, roles_from=AdminFlag("is_admin", "Customer"))
    assert rules.auth_for(["Customer"], flag) == "Roles(Customer)"


# ---- enum columns in their stored spelling -----------------------------------------------------


def test_the_raw_sql_seed_writes_an_enum_column_in_its_stored_spelling(tmp_path):
    """SQLAlchemy stores an ``Enum`` column by member name (``MANAGER``) though its
    values are ``manager``: the seed writes what the app stores, so the T4 check of
    stored values reads the app's spelling (H2's false refutation was the seed's)."""
    import sqlite3

    from lattice.importer.adapt import table_spec
    from lattice.importer.appmodel import FieldSpec
    from lattice.importer.suite import AuthSpec, seed_plan, sql_seed
    from lattice.profile import ApiProfile

    users = TableModel("User", "users", "m", (
        FieldSpec("id", "int", "Int", primary_key=True),
        FieldSpec("email", "str", "Str"),
        FieldSpec("user_type", "UserType", "Str", choices=("manager", "employee"),
                  choice_names=("MANAGER", "EMPLOYEE")),
    ), ())  # fmt: skip
    spec = table_spec(users, ApiProfile(tables={}, table_names={}, roles=frozenset({"Public"})))
    assert spec.column("user_type").stored == ("MANAGER", "EMPLOYEE")
    auth = AuthSpec("users", password_column="password", admin_column=None, active_column=None,
                    key_type="int")  # fmt: skip
    plan = seed_plan([spec], auth)
    db = sql_seed(tmp_path / "seed.db", [spec], plan)
    con = sqlite3.connect(db)
    stored = {r[0] for r in con.execute("SELECT user_type FROM users")}
    con.close()
    assert stored == {"MANAGER", "EMPLOYEE"}


# ---- "outside API" needs complete evidence ----------------------------------------------------


def _model(*routes: Route) -> RepoModel:
    tables = tuple(TableModel(n, n.lower(), "m", (), ()) for n in ("User", "Note", "Role"))
    return RepoModel(app_package="toy", tables=tables, models=(), dependencies=(),
                     routes=routes, natives=(), database_url=None, dialect="sqlite",
                     exception_handlers=(), custom_serialization=())  # fmt: skip


def _route(path: str, tables: tuple[str, ...], unread: str | None = None) -> Route:
    return Route(name=path, module="m", method="GET", path=path, params=(), response_model=None,
                 response_class=None, status_code=200, dependencies=(), auth="Public",
                 auth_reason="", raises=(), calls=(), tables=tables, owner_uses=(),
                 body_models=(), line=1, unread=unread)  # fmt: skip


def test_tables_no_route_touches_are_outside_on_complete_evidence():
    model = _model(_route("/users", ("User",)), _route("/notes", ("Note",)))
    assert set(rules.outside_api(model)) == {"Role"}
    assert rules.outside_gaps(model) == []


def test_an_unread_handler_vetoes_every_exclusion_and_says_why():
    model = _model(
        _route("/users", ("User",)),
        _route("/notes", (), unread="calls drf_toy.helpers:Service.run, not read"),
    )
    assert rules.outside_api(model) == {}
    gaps = rules.outside_gaps(model)
    assert gaps and "GET /notes" in gaps[0] and "Service.run" in gaps[0]
