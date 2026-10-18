"""``lattice.gocheck.derive`` (Spike J, node J1; D3, D4): layer A's policy derived from a Go
app, the spec laid over it, and the opt-in policy extract it reads. Needs ``go`` (the
extractor and the checker are Go programs); none of it builds or runs a fixture app."""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from lattice.gocheck import GoPolicy, build_gocheck, run_gocheck
from lattice.gocheck.derive import derive_go_policy, go_spec_policy, policy_extract
from lattice.importer.frontends.go_gin import GoGinFrontEnd
from lattice.profile import ApiProfile, Profile, RoleColumn
from lattice.spec import Machine, Spec
from lattice.typesys import Scalar, Schema

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

REPO = Path(__file__).resolve().parents[2]
GINAPP = REPO / "tests" / "spec" / "goginapp"
GOEXTRACT = REPO / "tests" / "gocheck" / "fixtures" / "goextract"
S = Scalar


# ---- D3: derive_go_policy -----------------------------------------------------------------------


def test_the_policy_of_goginapp():
    p = derive_go_policy(GINAPP)
    assert p.tables == {"User": "users", "Config": "configs", "Run": "runs"}
    assert p.users_table == "User"  # a schema name: the checker compares schema names
    assert p.owners == {"Run": "user_id"} and p.owner_via == {}
    assert p.private == {"User": ("password", "token")}
    assert p.admin_routes == () and p.open_routes == () and p.machines == {}


def test_the_baseline_is_the_keys_of_a_separate_run():
    p = derive_go_policy(GINAPP)
    again = run_gocheck(GINAPP, dataclasses.replace(p, baseline=frozenset()))
    assert p.baseline == frozenset(f.key for f in again)
    assert run_gocheck(GINAPP, p) == []  # accepted: the original's own findings


def test_the_goextract_fixture_owners_and_private_columns():
    p = derive_go_policy(GOEXTRACT)
    assert p.owners == {"Item": "owner_id", "Audit": "actor"}
    assert p.private == {"User": ("password_hash",)}
    assert p.admin_routes == (("GET", "/api/admin/audit"),)


def test_given_ownership_replaces_the_inference():
    p = derive_go_policy(GOEXTRACT, ownership={"Item": "owner_id"})
    assert p.owners == {"Item": "owner_id"}
    assert derive_go_policy(GOEXTRACT, ownership={}).owners == {}


def test_a_private_column_is_the_json_dash_never_a_name(tmp_path):
    """JM12: ``json:"password"`` exports the column, so it is not private whatever its name;
    ``json:"-"`` on an innocently named column is."""
    (tmp_path / "go.mod").write_text("module x\n\ngo 1.22\n")
    (tmp_path / "main.go").write_text(
        textwrap.dedent("""\
        package main

        type User struct {
            ID       uint   `gorm:"primaryKey" json:"id"`
            Password string `json:"password"`
            Token    string `json:"token,omitempty"`
            Secret   string `json:"-"`
            Plain    string
        }

        func (User) TableName() string { return "users" }

        func main() {}
        """)
    )
    p = derive_go_policy(tmp_path)
    assert p.private == {"User": ("secret",)}
    cols = {c["name"]: c["json"] for c in policy_extract(tmp_path)["tables"][0]["columns"]}
    assert cols == {"id": "id", "password": "password", "token": "token", "secret": "-",
                    "plain": None}  # fmt: skip


# ---- D3: go_spec_policy -------------------------------------------------------------------------


def _spec(run: Schema, machines: tuple[Machine, ...] = ()) -> Spec:
    user = Schema.from_pairs(
        [("id", S.INT), ("name", S.STR), ("password", S.STR), ("token", S.STR)],
        keys=[("id",), ("token",)],
        private=["password", "token"],
    )
    config = Schema.from_pairs([("id", S.INT), ("name", S.STR)], keys=[("id",)])
    api = ApiProfile(
        tables={"User": user, "Config": config, "Run": run},
        table_names={"User": "users", "Config": "configs", "Run": "runs"},
        roles=frozenset({"Public", "Member"}),
        users_table="User",
        roles_from=RoleColumn("name"),
    )
    return Spec(profile=Profile(schemas={}, api=api), machines=machines)


RUN_OWNED = Schema.from_pairs(
    [("id", S.INT), ("config_id", S.INT), ("user_id", S.INT), ("status", S.STR)],
    keys=[("id",)], owner="user_id",
)  # fmt: skip
RUN_SHARED = Schema.from_pairs(
    [("id", S.INT), ("config_id", S.INT), ("user_id", S.INT), ("status", S.STR)], keys=[("id",)]
)
QUEUED = Machine("Run", "status", {"queued": ("cancelled",)}, initial=("queued",))


def test_the_spec_policy_has_an_empty_baseline_and_the_spec_wins_for_declared_tables():
    derived = derive_go_policy(GINAPP)
    assert derived.baseline  # the original has a finding of its own (login's token)
    spec = _spec(RUN_SHARED, (QUEUED,))
    p = go_spec_policy(spec, GINAPP, derived)
    assert p.baseline == frozenset()  # JM9: not the derived baseline
    assert "Run" not in p.owners  # JM10: the spec declares Run with no owner: not owned
    assert p.machines["Run"][0] == "status"
    assert ("queued", "cancelled") in p.machines["Run"][1]
    assert p.private["User"] == ("password", "token")


def test_a_table_the_spec_does_not_declare_keeps_its_derived_policy():
    derived = derive_go_policy(GOEXTRACT)
    spec = _spec(RUN_OWNED)  # declares User, Config, Run: none of the fixture's tables
    p = go_spec_policy(spec, GOEXTRACT, derived)
    assert p.owners["Item"] == "owner_id" and p.owners["Audit"] == "actor"
    assert p.owners["Run"] == "user_id"
    assert "Item" in p.tables and "Run" in p.tables
    assert p.admin_routes == derived.admin_routes


def test_the_spec_policy_is_derived_when_no_policy_is_given():
    p = go_spec_policy(_spec(RUN_OWNED), GINAPP)
    assert p.owners == {"Run": "user_id"} and p.baseline == frozenset()


def test_from_spec_stays_the_default_policy_of_a_check():
    """Ruling Q3: ``GoPolicy.from_spec`` is unchanged (no derivation, no baseline)."""
    p = GoPolicy.from_spec(_spec(RUN_OWNED, (QUEUED,)))
    assert p.owners == {"Run": "user_id"} and p.baseline == frozenset()


# ---- D4: the opt-in policy extract --------------------------------------------------------------


def _default_extract(module_dir: Path) -> bytes:
    proc = subprocess.run(
        [str(build_gocheck()), "extract"], input=json.dumps({"root": str(module_dir)}).encode(),
        capture_output=True, check=True,
    )  # fmt: skip
    return proc.stdout


def test_the_default_extract_has_no_json_key():
    for module in (GINAPP, GOEXTRACT):
        data = json.loads(_default_extract(module))
        assert all("json" not in c for t in data["tables"] for c in t["columns"])


def test_the_policy_extract_only_adds_the_opt_in_keys():
    """The json key (D3) and, in round 2, params[].parse and raises[].bind (E1, E3)."""
    for module in (GINAPP, GOEXTRACT):
        default = json.loads(_default_extract(module))
        policy = policy_extract(module)
        assert all("json" in c for t in policy["tables"] for c in t["columns"])
        for t in policy["tables"]:
            for c in t["columns"]:
                del c["json"]
        for r in policy["routes"]:
            for x in (*r["params"], *r["raises"]):
                x.pop("parse", None)
                x.pop("parse_at", None)
                x.pop("bind", None)
        assert policy == default


def test_the_json_fact_is_the_tag_dash_or_null():
    data = policy_extract(GINAPP)
    user = next(t for t in data["tables"] if t["struct"] == "User")
    assert {c["name"]: c["json"] for c in user["columns"]} == {
        "id": "id", "name": "name", "password": "-", "token": "-",
    }  # fmt: skip
    audit = next(t for t in policy_extract(GOEXTRACT)["tables"] if t["struct"] == "Audit")
    assert all(c["json"] is None or isinstance(c["json"], str) for c in audit["columns"])


def test_the_front_end_extract_is_unchanged_by_the_option():
    fe = GoGinFrontEnd()
    default = fe.extract_json(GINAPP.parent, "goginapp")
    assert all("json" not in c for t in default["tables"] for c in t["columns"])
