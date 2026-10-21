"""Stage 3 of the .NET spike (prereg 5.4): layer A (`dncheck check`) on the Arm 0 fixture under its truth policy (A0-DN, exact),
the sibling fixture `dnparity1bad` (DN800), the gated rules (A0-GATE) and D1 with its six dev mutants (A-D1, A-DM).

The helper is built once per session into a temporary cache; targets are only read. Needs the .NET SDK (skips without it)."""

# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest

from lattice import dncheck
from lattice.dncheck.policy import (
    DN_CODES,
    DN_GATED,
    DnPolicy,
    all_dn_findings,
    gated_note,
    run_dncheck,
)
from lattice.gocheck import GoPolicy

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "dncheck" / "fixtures"
DATA = ROOT / "docs" / "results" / "data" / "2026-10-18-dotnet"
D1_TRUTH = ROOT / "tests" / "dncheck" / "d1" / "truth.json"

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


def policy_of(layer_a_policy: dict[str, Any], **over: Any) -> DnPolicy:
    """The truth's policy as a DnPolicy (the truth writes owner_via as [parent, fk]; GoPolicy keeps (fk, parent))."""
    p = layer_a_policy
    fields: dict[str, Any] = {
        "tables": p["tables"],
        "users_table": p.get("users_table"),
        "owners": p["owners"],
        "owner_via": {k: (v[1], v[0]) for k, v in p["owner_via"].items()},
        "private": {k: tuple(v) for k, v in p["private"].items()},
        "machines": {
            t: (m["column"], frozenset(map(tuple, m["edges"]))) for t, m in p["machines"].items()
        },
        "admin_routes": tuple(map(tuple, p["admin_routes"])),
        "open_routes": tuple(map(tuple, p["open_routes"])),
        "admin_role": p.get("admin_role", "Admin"),
    }
    return DnPolicy(**{**fields, **over})


@pytest.fixture(scope="session")
def truth() -> dict[str, Any]:
    return json.loads((FIXTURES / "dnparity1" / "truth.json").read_text())["layer_a"]


def where(f: Any) -> tuple[str, str, int]:
    return f.code, f.file, f.line


# ---- A0-DN ------------------------------------------------------------------------------------


def test_a0_dn_exact_on_the_fixture_under_its_truth_policy(truth: dict[str, Any]) -> None:
    found = all_dn_findings(FIXTURES / "dnparity1", policy_of(truth["policy"]))
    assert sorted(where(f) for f in found) == sorted(
        (t["code"], t["file"], t["line"]) for t in truth["findings"]
    )
    keyed = {t["key"] for t in truth["findings"] if t["key"]}
    assert keyed <= {f.key for f in found}
    assert all(f.code in DN_CODES and f.hint and f.message for f in found)


def test_each_finding_names_its_route(truth: dict[str, Any]) -> None:
    found = all_dn_findings(FIXTURES / "dnparity1", policy_of(truth["policy"]))
    by_site = {(f.file, f.line): f for f in found}
    for t in truth["findings"]:
        assert (
            t["route"]
            in by_site[(t["file"], t["line"])].key + by_site[(t["file"], t["line"])].message
        )


def test_dn806_fires_on_the_first_case_only(truth: dict[str, Any]) -> None:
    found = [
        f
        for f in all_dn_findings(FIXTURES / "dnparity1", policy_of(truth["policy"]))
        if f.code == "DN806"
    ]
    assert [(f.file, f.line) for f in found] == [("Endpoints/TodoEndpoints.cs", 26)]


def test_dn807_ignores_the_transaction_case(truth: dict[str, Any]) -> None:
    found = [
        f
        for f in all_dn_findings(FIXTURES / "dnparity1", policy_of(truth["policy"]))
        if f.code == "DN807"
    ]
    assert [f.line for f in found] == [106] and "split-safe" not in found[0].key


def test_dn808_needs_the_policys_machine(truth: dict[str, Any]) -> None:
    bare = policy_of(truth["policy"], machines={})
    assert not [f for f in all_dn_findings(FIXTURES / "dnparity1", bare) if f.code == "DN808"]
    wide = policy_of(
        truth["policy"],
        machines={
            "Note": (
                "Status",
                frozenset(
                    {("Draft", "Published"), ("Published", "Archived"), ("Archived", "Draft")}
                ),
            )
        },
    )
    assert not [f for f in all_dn_findings(FIXTURES / "dnparity1", wide) if f.code == "DN808"]


def test_open_and_admin_routes_are_not_judged(truth: dict[str, Any]) -> None:
    pol = policy_of(truth["policy"], open_routes=(), admin_routes=())
    got = {(f.code, f.key.split("|")[1]) for f in all_dn_findings(FIXTURES / "dnparity1", pol)}
    assert ("DN801", "GET /api/Notes/public/{id:int}") in got  # the policy no longer calls it open
    assert not any(
        k == "GET /v1/admin/notes" for _, k in got
    )  # RequireRole("Admin") is admin-only either way


def test_rules_restrict_the_codes_run(truth: dict[str, Any]) -> None:
    found = all_dn_findings(FIXTURES / "dnparity1", policy_of(truth["policy"], rules=("DN802",)))
    assert {f.code for f in found} == {"DN802"}


def test_baseline_accepts_a_finding_by_key(truth: dict[str, Any]) -> None:
    base = policy_of(truth["policy"])
    keys = frozenset(
        f.key for f in all_dn_findings(FIXTURES / "dnparity1", base) if f.code == "DN801"
    )
    left = run_dncheck(FIXTURES / "dnparity1", policy_of(truth["policy"], baseline=keys))
    assert not [f for f in left if f.code == "DN801"] and [f for f in left if f.code == "DN802"]


# ---- DN800 and the gate -------------------------------------------------------------------------


def test_dn800_on_the_sibling_fixture_and_its_freeze() -> None:
    t = json.loads((FIXTURES / "dnparity1bad" / "truth.json").read_text())["layer_a"]
    found = all_dn_findings(FIXTURES / "dnparity1bad", DnPolicy())
    assert [(f.code, f.file, f.line, f.key) for f in found] == [
        (x["code"], x["file"], x["line"], x["key"]) for x in t["findings"]
    ]
    assert "CS1525" in found[0].message
    for line in (DATA / "freeze-dnparity1bad.sha256").read_text().splitlines():
        digest, rel = line.split("  ")
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest


def test_a0_gate_texts() -> None:
    assert set(DN_GATED) == {"DN804", "DN805"} and not set(DN_GATED) & set(DN_CODES)
    note = gated_note()
    assert (
        "DN804" in note and "DN805" in note and "K8" in note and "AmbiguousMatchException" in note
    )


def test_dnpolicy_is_gopolicys_fields_and_algorithm() -> None:
    assert issubclass(DnPolicy, GoPolicy) and DnPolicy().to_json() == GoPolicy().to_json()


def test_a_missing_directory_is_an_error() -> None:
    with pytest.raises(dncheck.DnCheckError):
        run_dncheck(FIXTURES / "nope", DnPolicy())


# ---- D1 and its dev mutants (A-D1, A-DM) -------------------------------------------------------------


@pytest.fixture(scope="session")
def d1_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("d1")
    with tarfile.open(DATA / "d1.tgz") as tf:
        tf.extractall(out, filter="data")
    for junk in out.rglob("._*"):
        junk.unlink()
    return next(out.rglob("dnstreak.csproj")).parent


@pytest.fixture(scope="session")
def d1_policy() -> DnPolicy:
    """What Streak's SPEC states for D1 (spec.py at a0679e9): owners, owner_via (fk, parent), private columns, the machine."""
    t = json.loads(D1_TRUTH.read_text())
    return DnPolicy(
        tables={e["entity"]: e["table"] for e in t["tables"]},
        users_table="User",
        owners=t["owners"],
        owner_via={k: (v[1], v[0]) for k, v in t["owner_via"].items()},
        private={"User": ("hashed_password",), "RefreshToken": ("token_hash",)},
        machines={
            "Nudge": ("delivery", frozenset(map(tuple, t["machines"]["Nudge.delivery"]["edges"])))
        },
    )


def test_a_d1_the_correct_service_has_no_finding(d1_dir: Path, d1_policy: DnPolicy) -> None:
    assert all_dn_findings(d1_dir, d1_policy) == []


MUTANTS = {
    1: ("DN801", "GET /v1/habits/{id}"),
    2: ("DN801", "PATCH /v1/habits/{id}"),
    3: ("DN802", "GET /v1/me"),
    4: None,
    5: None,
    6: None,
}


@pytest.mark.skipif(shutil.which("patch") is None, reason="needs patch")
@pytest.mark.parametrize("n", sorted(MUTANTS))
def test_a_dm_d1_dev_mutants(n: int, d1_dir: Path, d1_policy: DnPolicy, tmp_path: Path) -> None:
    work = tmp_path / "dm"
    shutil.copytree(d1_dir, work)
    subprocess.run(
        ["patch", "-p1", "-s", "-i", str(DATA / "d1-mutants" / f"dm{n}.diff")], cwd=work, check=True
    )
    found = all_dn_findings(work, d1_policy)
    want = MUTANTS[n]
    if (
        want is None
    ):  # DM4 (layer R), DM5 (DN805 is gated) and DM6 (no route) are not layer A's: reported, silent here
        assert found == []
    else:
        assert [(f.code, f.key.split("|")[1]) for f in found] == [want]
