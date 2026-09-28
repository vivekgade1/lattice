"""The importer end to end on the two held-out repos (``bench/import_repos``) with
``--no-llm``: it completes, writes a consistent ``lattice_import/`` (every route of the
app in the report once, the emitted programs exactly the verified routes, every island
with a reason and a classified gap) and its coverage is the recorded one.

The coverage below is the record (see ``docs/results/2026-09-26-bench-smoke.md``, "Importer
on held-out repos"); a change to it is a change to that record. Nothing here is
repo-specific importer code: these repos only exercise the generic rules.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from lattice.importer.extract import LANGUAGE_GAP, extract
from lattice.importer.lift import check_program
from lattice.importer.llm_lift import load_profile_source

REPO_ROOT = Path(__file__).resolve().parents[2]

# repo -> (routes, verified, islands, needs-llm, {gap: islands})
COVERAGE = {
    "taskflow": (
        10, 1, 2, 7,
        {"unsupported framework feature: a route defined inside an app factory": 2},
    ),
    "justnotes": (30, 1, 0, 29, {}),
}  # fmt: skip
# the gaps each repo's profile records as unclear decisions
PROFILE_GAPS: dict[str, set[str]] = {
    "taskflow": set(),
    "justnotes": set(),
}
# the language gaps the first run recorded, closed since (single-role auth: NoRoles; a
# non-key token subject: NativeToken(lookup=); natives reading app configuration:
# NativeContext settings; enum columns: OneOf; a token subject with no declared unique
# constraint: NativeToken(unique="runtime")) -- none may come back
CLOSED_GAPS = (
    "single-role auth",
    "token subject is not the users key",
    "enum constraint",
    "token subject is not a unique users column",  # NativeToken(unique="runtime")
)


@pytest.fixture(scope="module", params=sorted(COVERAGE))
def run(request, tmp_path_factory) -> tuple[str, Path, dict]:
    repo = request.param
    out = tmp_path_factory.mktemp(repo) / "lattice_import"
    done = subprocess.run(
        [sys.executable, "-m", "lattice.importer", str(REPO_ROOT),
         "--package", f"bench.import_repos.{repo}.app",
         "--app-factory", f"bench.import_repos.{repo}:make_app",
         "--out", str(out), "--no-llm", "--no-corpus"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr[-3000:]
    return repo, out, json.loads((out / "report.json").read_text())


def test_the_import_completes_with_every_route_reported_once(run):
    repo, out, report = run
    model = extract(REPO_ROOT, f"bench.import_repos.{repo}.app")
    keys = [r["key"] for r in report["routes"]]
    assert len(keys) == len(set(keys)) == len(model.routes)
    assert sorted(p.name for p in out.iterdir()) == [
        "natives.py", "profile.py", "report.json", "report.md", "routes",
    ]  # fmt: skip


def test_every_emitted_route_is_verified(run):
    _, out, report = run
    verified = {r["key"] for r in report["routes"] if r["status"] == "verified"}
    emitted: set[str] = set()
    profile = load_profile_source((out / "profile.py").read_text()).profile()
    for path in (out / "routes").iterdir():
        source = path.read_text()
        plans, errors = check_program(source, profile)
        assert plans is not None, (path.name, errors)
        emitted |= {n.name for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}
    assert emitted == verified


def test_every_island_has_a_reason_and_a_classified_gap(run):
    _, _, report = run
    for r in report["routes"]:
        if r["status"] == "island":
            assert r["reason"] and r["gap"] and r["gap"] != "unclassified", r["key"]


def test_the_recorded_coverage(run):
    repo, _, report = run
    routes, verified, islands, needs_llm, gaps = COVERAGE[repo]
    cov = report["coverage"]
    assert (cov["routes"], cov["verified"], cov["R4"], cov["needs-llm"]) == (
        routes, verified, islands, needs_llm,
    )  # fmt: skip
    found: dict[str, int] = {}
    for r in report["routes"]:
        if r["status"] == "island":
            found[r["gap"]] = found.get(r["gap"], 0) + 1
    assert found == gaps


def test_the_language_gaps_are_recorded_as_unclear_decisions(run):
    repo, _, report = run
    reasons = " ".join(u["reason"] for u in report["unclear"])
    for gap in PROFILE_GAPS[repo]:
        assert f"{LANGUAGE_GAP} ({gap})" in reasons, gap


def test_a_subject_not_declared_unique_is_checked_at_runtime(run):
    repo, out, report = run
    if repo != "justnotes":
        return
    profile = load_profile_source((out / "profile.py").read_text()).profile()
    assert (profile.api.auth.lookup, profile.api.auth.unique) == ("username", "runtime")
    reasons = " ".join(u["reason"] for u in report["unclear"])
    assert "User.username, which is not declared unique" in reasons


def test_the_closed_gaps_stay_closed(run):
    _, _, report = run
    reasons = " ".join(
        [u["reason"] for u in report["unclear"]] + [r.get("reason") or "" for r in report["routes"]]
    )
    for gap in CLOSED_GAPS:
        assert f"{LANGUAGE_GAP} ({gap})" not in reasons, gap
    assert "which a native cannot import" not in reasons  # the TaskFlow secret: a setting
