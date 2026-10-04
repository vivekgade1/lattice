"""Phase 0 exit (docs/superpowers/plans/2026-10-04-phase0-spec-checker.md).

(1) The multi-vendor spec checks clean against the original app, with the app's accepted
baseline; (2) every t31-t44 reference passes against its ticket's spec delta and every
trap variant is caught by at least one layer: ``python -m bench.specs.exit`` runs those
(minutes), and its recorded table (``bench/results/phase0-exit.json``) is checked here
against the current trap list.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.import_repos.multivendor import make_app
from bench.specs import exit as phase0
from bench.specs.multivendor import BASELINE, NATIVES, SPEC
from lattice.spec import Accepted, CheckFinding, Report, check, spec_policy

ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "bench/import_repos/multivendor/app"
PKG = "bench.import_repos.multivendor.app"
TABLE = ROOT / "bench/results/phase0-exit.json"


def _finding(layer: str, **kw: object) -> CheckFinding:
    return CheckFinding(layer=layer, verdict="silent", rule="r", **kw)  # type: ignore[arg-type]


def test_a_baseline_accepts_matching_findings_and_reports_stale_entries():
    a = _finding("A", code="PY803", endpoint="POST /x", details={"message": "unbounded"})
    b = _finding("B", endpoint="GET /y", case="GET /y | admin | query limit below_min")
    c = _finding("D", endpoint="GET /z", case="s / step 1")
    report = Report(layers="ABCD", findings=[a, b, c])
    report.apply_baseline(
        (Accepted("A:PY803:*", "no bounds"), Accepted("B:*below_min", "x"), Accepted("C:*", "y"))
    )
    assert report.findings == [c]
    assert [(f.key, r) for f, r in report.accepted] == [
        ("A:PY803:POST /x:unbounded", "no bounds"),
        ("B:GET /y:GET /y | admin | query limit below_min", "x"),
    ]
    assert [s.pattern for s in report.stale] == ["C:*"]
    assert report.verdict == "fail"
    report.findings = []
    assert report.verdict == "pass"
    assert report.to_dict()["accepted"][0]["reason"] == "no bounds"


def test_a_table_the_spec_declares_without_an_owner_is_shared_not_a_link():
    policy = spec_policy(SPEC, APP_DIR, PKG)
    assert "Product" in policy.shared
    assert "Product" not in policy.links
    assert policy.owner_via == {"OrderItem": ("order_id", "Order")}


def test_exit_1_the_spec_checks_clean_against_the_original_app():
    from bench.edit_slice.repos import multivendor

    report = check(
        SPEC,
        make_app,
        multivendor.db_factory,
        app_dir=APP_DIR,
        package=PKG,
        login=multivendor.login,
        mint_token=multivendor.mint,
        natives=NATIVES,
        baseline=BASELINE,
    )
    assert report.verdict == "pass", report.render()
    assert not report.stale, report.render()
    assert report.accepted  # the baseline is real: upstream has these gaps


@pytest.fixture(scope="module")
def table() -> list[dict]:
    if not TABLE.exists():
        pytest.skip("run `python -m bench.specs.exit` first")
    return json.loads(TABLE.read_text())


def test_exit_2_the_recorded_run_covers_every_reference_and_trap(table):
    assert sorted((r["name"], r["ticket"]) for r in table) == sorted(phase0.jobs())


def test_exit_2_every_reference_passes_against_its_ticket_delta(table):
    failing = {r["ticket"]: r for r in table if r["name"] == "reference" and r["verdict"] != "pass"}
    assert not failing, json.dumps(failing, indent=2)


def test_exit_2_every_trap_is_caught_by_at_least_one_layer(table):
    missed = {r["name"]: r for r in table if r["name"] != "reference" and r["verdict"] != "fail"}
    assert not missed, json.dumps(missed, indent=2)
    assert all(r["layers"] for r in table if r["name"] != "reference")
