"""The importer CLI end to end (plan I4/I5): ``python -m lattice.importer`` on the
template with ``--no-llm`` writes ``lattice_import/`` holding the lifters' verified
routes -- the 9 proof routes and ``test_token`` -- and reports every other route as
needing the LLM; the report follows its schema; the loop is wired through the CLI."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from lattice.importer.lift import check_program
from lattice.importer.llm_lift import load_profile_source
from lattice.importer.report import SCHEMA
from tests.importer.test_lift_template import ROUTES, _lift
from tests.importer.test_llm_lift import FakeLLM

REPO_ROOT = Path(__file__).resolve().parents[2]
ARGS = [str(REPO_ROOT), "--package", "bench.template_app.app",
        "--app-factory", "bench.template_app:make_app"]  # fmt: skip
VERIFIED = {*ROUTES, "test_token"}


@pytest.fixture(scope="module")
def no_llm_run(tmp_path_factory) -> tuple[Path, Path, str]:
    tmp = tmp_path_factory.mktemp("cli")
    out, corpus = tmp / "lattice_import", tmp / "imports.jsonl"
    done = subprocess.run(
        [sys.executable, "-m", "lattice.importer", *ARGS, "--out", str(out), "--no-llm",
         "--corpus", str(corpus)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr[-3000:]
    return out, corpus, done.stdout


def _endpoints(source: str) -> set[str]:
    return {n.name for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}


def test_the_no_llm_import_holds_the_verified_routes(no_llm_run):
    out, _, stdout = no_llm_run
    assert sorted(p.name for p in out.iterdir()) == [
        "natives.py", "profile.py", "report.json", "report.md", "routes",
    ]  # fmt: skip
    files = {p.name: p.read_text() for p in (out / "routes").iterdir()}
    assert set(files) == {"items.lattice.py", "login.lattice.py", "users.lattice.py"}
    emitted = set().union(*(_endpoints(s) for s in files.values()))
    assert emitted == VERIFIED
    # the same programs and profile the importer produces in-process
    result = _lift()
    assert files == {f"{k}.lattice.py": v for k, v in result.programs.items()}
    assert (out / "profile.py").read_text() == result.profile.source
    profile = load_profile_source((out / "profile.py").read_text()).profile()
    for name, source in files.items():
        plans, errors = check_program(source, profile)
        assert plans is not None, (name, errors)
    assert "10 verified (R0 10" in stdout and "13 need the LLM" in stdout


def test_the_report_marks_the_rest_as_needing_the_llm(no_llm_run):
    out, _, _ = no_llm_run
    report = json.loads((out / "report.json").read_text())
    status = {r["key"]: r["status"] for r in report["routes"]}
    assert {k for k, s in status.items() if s == "verified"} == VERIFIED
    pending = {k for k, s in status.items() if s == "needs-llm"}
    assert len(pending) == 13 and "read_user_by_id" in pending
    assert not any(s == "island" for s in status.values())
    for r in report["routes"]:
        if r["status"] == "needs-llm":
            assert r["rung"] == "needs-llm" and r["lifter_reason"].startswith("no lifter")
        else:
            assert (r["rung"], r["by"]) == ("R0", "lifter")
    cov = report["coverage"]
    assert (cov["routes"], cov["R0"], cov["needs-llm"], cov["R4"]) == (23, 10, 13, 0)
    assert cov["complete"] is False
    natives = (out / "natives.py").read_text()
    assert "NEEDS_LLM" in natives and "'read_user_by_id'" in natives
    compile(natives, "natives.py", "exec")
    md = (out / "report.md").read_text()
    assert "LLM loop: off (--no-llm)" in md and "| read_item |" in md


def test_the_report_json_schema(no_llm_run):
    out, _, _ = no_llm_run
    report = json.loads((out / "report.json").read_text())
    assert report["schema"] == SCHEMA
    assert set(report) == {
        "schema", "repo", "package", "app_factory", "llm", "method", "model", "coverage",
        "budget", "cost", "routes", "behaviour_changes", "islands", "carve_out_natives",
        "unclear", "notes_disagreements", "rules",
    }  # fmt: skip
    # the rule layer (schema 2) runs in a --rules-only import; a behaviour import has none
    assert report["rules"] is None and report["coverage"]["rules"] is None
    assert set(report["coverage"]) >= {
        "routes", "R0", "R1", "R2", "R3", "R4", "needs-llm", "verified", "lattice_pct",
        "carve_out_pct", "island_pct", "verified_pct", "complete", "lines", "ast_nodes",
        "tiers", "carve_kinds",
    }  # fmt: skip
    route_keys = {
        "key", "name", "module", "router", "method", "path", "auth", "status", "rung", "by",
        "lifters", "lifter_reason", "reason", "gap", "tally", "deviations", "rounds",
        "cost_usd", "attempts", "natives", "notes", "original", "tier", "carve_kind",
        "procedure_count", "procedure_lines", "procedures",
    }  # fmt: skip
    for r in report["routes"]:
        assert set(r) == route_keys
        assert r["original"]["lines"] > 0 and r["original"]["ast_nodes"] > 0
    read_items = next(r for r in report["routes"] if r["key"] == "read_items")
    tally = read_items["tally"]
    assert set(tally) == {"equal", "equal_under_rule", "deviation", "mismatch"}
    assert tally["mismatch"] == 0 and tally["deviation"] > 0 and tally["equal"] > 0
    ids = {(c["route"], c["id"]) for c in report["behaviour_changes"]}
    assert ("read_items", "bounded-page-size") in ids
    assert report["cost"]["usd"] == 0 and report["budget"] is None


def test_the_no_llm_run_logs_routes_to_the_corpus(no_llm_run):
    _, corpus, _ = no_llm_run
    lines = [json.loads(x) for x in corpus.read_text().splitlines()]
    assert {x["type"] for x in lines} == {"route"} and len(lines) == 23
    miss = next(x for x in lines if x["route"] == "read_user_by_id")
    assert miss["lifter_miss"].startswith("no lifter") and "session.get" in miss["route_source"]
    hit = next(x for x in lines if x["route"] == "read_item")
    assert hit["status"] == "verified" and "@endpoint" in hit["lattice"]


def test_the_cli_runs_the_loop_with_a_budget(tmp_path):
    """The loop through the CLI (fake generators): the repo round cap stops it; the rest
    are islands with reason budget, and the output is written."""
    from lattice.importer.cli import main

    gen = FakeLLM("no code", "no code")
    out = tmp_path / "li"
    code = main(
        [*ARGS, "--out", str(out), "--method", "M1", "--max-repo-rounds", "2",
         "--corpus", str(tmp_path / "c.jsonl")],
        generators=(gen, gen, gen),
    )  # fmt: skip
    assert code == 0 and len(gen.calls) == 2
    report = json.loads((out / "report.json").read_text())
    assert report["llm"] is True and report["method"] == "M1"
    assert report["budget"]["exhausted"] is True
    islands = [r for r in report["routes"] if r["status"] == "island"]
    assert len(islands) == 13
    assert all(r["gap"] == "budget" for r in islands if r["key"] != islands[0]["key"])
    assert report["coverage"]["R0"] == 10
