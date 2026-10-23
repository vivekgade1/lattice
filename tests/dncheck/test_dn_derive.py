"""A-DERIVE (prereg 5.4): `derive_dn_policy` on the Arm 0 fixture and D1 reads owners and private columns exactly as their
truths state them; the derived baseline is empty on D1; `dn_spec_policy` lays a spec over it (K6's table identity)."""

# ruff: noqa: E501
from __future__ import annotations

import json
import os
import shutil
import tarfile
from pathlib import Path
from typing import Any

import pytest

from lattice import dncheck
from lattice.dncheck.derive import (
    derive_dn_policy,
    dn_spec_policy,
    policy_extract,
    undeclared_tables,
)
from lattice.dncheck.policy import DnPolicy, run_dncheck

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "dncheck" / "fixtures" / "dnparity1"
D1_TGZ = ROOT / "docs" / "results" / "data" / "2026-10-18-dotnet" / "d1.tgz"
D1_TRUTH = ROOT / "tests" / "dncheck" / "d1" / "truth.json"
H1_TGZ = ROOT / "docs" / "results" / "data" / "2026-10-18-dotnet" / "h1.tgz"
H1_TRUTH = ROOT / "docs" / "results" / "data" / "2026-10-18-dotnet" / "h1-truth.json"

pytestmark = pytest.mark.skipif(shutil.which("dotnet") is None, reason="needs the .NET SDK")


@pytest.fixture(scope="session", autouse=True)
def helper(tmp_path_factory: pytest.TempPathFactory) -> Path:
    cache = tmp_path_factory.mktemp("dncheck-cache")
    old = os.environ.get("LATTICE_DNCHECK_CACHE")
    os.environ["LATTICE_DNCHECK_CACHE"] = str(cache)
    try:
        yield dncheck.build_dncheck()
    finally:
        if old is None:
            os.environ.pop("LATTICE_DNCHECK_CACHE", None)
        else:
            os.environ["LATTICE_DNCHECK_CACHE"] = old


@pytest.fixture(scope="session")
def d1_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("d1")
    with tarfile.open(D1_TGZ) as tf:
        tf.extractall(out, filter="data")
    for junk in out.rglob("._*"):
        junk.unlink()
    return next(out.rglob("dnstreak.csproj")).parent


@pytest.fixture(scope="session")
def h1_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("h1")
    with tarfile.open(H1_TGZ) as tf:
        tf.extractall(out, filter="data")
    for junk in out.rglob("._*"):
        junk.unlink()
    return next(out.rglob("outlay.csproj")).parent


def test_derive_on_the_fixture_is_the_truths_policy() -> None:
    truth = json.loads((FIXTURE / "truth.json").read_text())["layer_a"]["policy"]
    p = derive_dn_policy(FIXTURE)
    assert dict(p.tables) == truth["tables"] and p.users_table == truth["users_table"]
    assert dict(p.owners) == truth["owners"]
    assert {t: list(c) for t, c in p.private.items()} == truth["private"]
    assert [list(r) for r in p.admin_routes] == truth["admin_routes"]
    assert dict(p.owner_via) == {
        "Tag": ("NoteId", "Note")
    }  # (fk, parent), GoPolicy's order; the truth writes [parent, fk]
    assert not p.machines and not p.open_routes


def test_the_fixtures_own_findings_are_its_baseline() -> None:
    p = derive_dn_policy(FIXTURE)
    assert (
        "DN801|DELETE /api/Notes/{id:int}|Note" in p.baseline
        and "DN801|GET /v1/todos/all|Todo" in p.baseline
    )
    assert run_dncheck(FIXTURE, p) == []


def test_derive_on_d1_is_the_truths_owners_and_private_and_the_baseline_is_empty(
    d1_dir: Path,
) -> None:
    truth = json.loads(D1_TRUTH.read_text())
    p = derive_dn_policy(d1_dir)
    assert dict(p.owners) == truth["owners"]
    assert dict(p.owner_via) == {k: (v[1], v[0]) for k, v in truth["owner_via"].items()}
    # D1 keeps columns off the wire by its response records, not by [JsonIgnore]: derived from what no response reads
    hidden = {(e, c) for e, cols in p.private.items() for c in cols}
    want = {(n["entity"], n["column"]) for n in truth["not_returned"]}
    assert want <= hidden
    assert {c for c in hidden if c not in want} <= {
        ("TestClockRow", "now")
    }  # echoed back from a local, not the row
    assert p.baseline == frozenset() and p.users_table == "User"
    assert not p.machines


def test_derived_private_on_h1_names_no_ordinary_report_column(h1_dir: Path) -> None:
    """H-PA1: a column is private only when the code supports it. H1 answers every `expense_reports` column through
    `Summaries(IQueryable<ExpenseReport>)`, so none is private; the truth's four (a password, a join-less timestamp, a
    payout method's owner and account number) are still derived, and nothing else is."""
    truth = json.loads(H1_TRUTH.read_text())
    p = derive_dn_policy(h1_dir)
    hidden = {(e, c) for e, cols in p.private.items() for c in cols}
    want = {(n["entity"], n["column"]) for n in truth["not_returned"]}
    assert "ExpenseReport" not in p.private and "ExpenseLine" not in p.private
    assert hidden == want


def test_an_ownership_given_replaces_the_rule() -> None:
    p = derive_dn_policy(FIXTURE, ownership={"Note": "OwnerId"})
    assert dict(p.owners) == {"Note": "OwnerId"} and not p.owner_via


def test_the_policy_extract_carries_the_json_facts() -> None:
    cols = [c for t in policy_extract(FIXTURE)["tables"] for c in t["columns"]]
    assert all("json" in c for c in cols)


def test_a_spec_policy_wins_over_the_derived_one() -> None:
    """The spec calls the entity `Note` (table `notes`) `Memo`: K6's identity by SQL name, the spec's owner column wins."""
    from lattice.profile import ApiProfile, Profile, RoleColumn
    from lattice.spec import Spec
    from lattice.typesys import Scalar, Schema

    user = Schema.from_pairs(
        [("id", Scalar.INT), ("email", Scalar.STR), ("role", Scalar.STR), ("token", Scalar.STR)],
        keys=[("id",)],
    )
    memo = Schema.from_pairs(
        [("id", Scalar.INT), ("owner_id", Scalar.INT)], keys=[("id",)], owner="owner_id"
    )
    api = ApiProfile(
        tables={"User": user, "Memo": memo},
        table_names={"User": "Users", "Memo": "notes"},
        roles=frozenset({"Public", "Member"}),
        users_table="User",
        roles_from=RoleColumn("role"),
        error_body="fastapi",
    )
    spec: Any = Spec(profile=Profile(schemas={}, api=api))
    p = dn_spec_policy(spec, FIXTURE)
    assert isinstance(p, DnPolicy) and p.baseline == frozenset()
    assert p.owners["Memo"] == "owner_id" and "Note" not in p.owners and "Note" not in p.tables
    assert (
        p.owners["Todo"] == "OwnerId" and "Tag" in p.tables
    )  # derived facts of undeclared tables stay
    assert undeclared_tables(spec, FIXTURE) == []
