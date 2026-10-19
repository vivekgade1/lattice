"""``lattice.gocheck.derive``, round 3 (Spike J, node G3; K6): the spec's tables are matched to the
derived ones by SQL name, and a derived table the spec does not declare and that carries no fact
is dropped from the policy and listed by :func:`undeclared_tables`. Needs ``go`` (the extractor
and the checker are Go programs); none of it builds or runs a fixture app."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lattice.gocheck import GoPolicy
from lattice.gocheck.derive import derive_go_policy, go_spec_policy, undeclared_tables
from lattice.profile import ApiProfile, Profile, RoleColumn
from lattice.spec import Spec
from lattice.typesys import Scalar, Schema

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

REPO = Path(__file__).resolve().parents[2]
GINAPP = REPO / "tests" / "spec" / "goginapp"
GOEXTRACT = REPO / "tests" / "gocheck" / "fixtures" / "goextract"
S = Scalar


def spec_with(tables: dict[str, Schema], names: dict[str, str], users: str = "User") -> Spec:
    api = ApiProfile(
        tables=tables,
        table_names=names,
        roles=frozenset({"Public", "Member"}),
        users_table=users,
        roles_from=RoleColumn("name"),
    )
    return Spec(profile=Profile(schemas={}, api=api))


USER = Schema.from_pairs(
    [("id", S.INT), ("name", S.STR), ("password", S.STR), ("token", S.STR)],
    keys=[("id",), ("token",)],
    private=["password", "token"],
)
CONFIG = Schema.from_pairs([("id", S.INT), ("name", S.STR)], keys=[("id",)])
RUN = Schema.from_pairs(
    [("id", S.INT), ("config_id", S.INT), ("user_id", S.INT), ("status", S.STR)],
    keys=[("id",)],
    owner="user_id",
)


def test_a_derived_table_whose_sql_name_is_a_declared_tables_is_that_table():
    """The spec calls the struct ``Job`` what the code calls ``Run`` (both ``runs``)."""
    spec = spec_with(
        {"User": USER, "Config": CONFIG, "Job": RUN},
        {"User": "users", "Config": "configs", "Job": "runs"},
    )
    p = go_spec_policy(spec, GINAPP)
    assert p.tables == {"User": "users", "Config": "configs", "Job": "runs"}  # no `Run`
    assert p.owners == {"Job": "user_id"}  # the derived owner of `Run` yields to the spec's
    assert "Run" not in p.owners and "Run" not in p.machines
    assert undeclared_tables(spec, GINAPP) == []


def test_a_derived_table_the_spec_does_not_declare_and_that_has_no_fact_is_dropped():
    spec = spec_with({"User": USER, "Run": RUN}, {"User": "users", "Run": "runs"})
    assert undeclared_tables(spec, GINAPP) == ["Config"]  # configs: shared, no owner, no private
    p = go_spec_policy(spec, GINAPP)
    assert "Config" not in p.tables and set(p.tables) == {"User", "Run"}


def test_undeclared_owned_table_is_kept():
    """A derived owner is a fact: the check on ``Item`` (owner_id) stays alive."""
    derived = derive_go_policy(GOEXTRACT)
    assert "Item" in derived.owners
    spec = spec_with({"User": USER, "Run": RUN}, {"User": "users", "Run": "runs"})
    p = go_spec_policy(spec, GOEXTRACT, derived)
    assert p.owners["Item"] == "owner_id" and "Item" in p.tables
    assert "Item" not in undeclared_tables(spec, GOEXTRACT, derived)


def test_an_undeclared_table_with_a_private_column_is_kept():
    """The spec's users table is ``Account`` (accounts); the derived ``User`` (users) is not
    declared but hides password and token: its check stays alive."""
    derived = derive_go_policy(GINAPP)
    spec = spec_with(
        {"Account": USER, "Config": CONFIG, "Run": RUN},
        {"Account": "accounts", "Config": "configs", "Run": "runs"},
        users="Account",
    )
    assert derived.private == {"User": ("password", "token")}
    assert undeclared_tables(spec, GINAPP, derived) == []
    p = go_spec_policy(spec, GINAPP, derived)
    assert p.tables["User"] == "users" and p.private["User"] == ("password", "token")


def test_the_tables_of_a_fully_declared_spec_are_unchanged():
    spec = spec_with(
        {"User": USER, "Config": CONFIG, "Run": RUN},
        {"User": "users", "Config": "configs", "Run": "runs"},
    )
    p = go_spec_policy(spec, GINAPP)
    stated = GoPolicy.from_spec(spec)
    assert p.tables == stated.tables and undeclared_tables(spec, GINAPP) == []
