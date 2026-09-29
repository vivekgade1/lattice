"""Arm L on the held-out repos (round 3's t19-t24): its start state is the importer's
committed output (``bench/imports/<repo>``, written by ``python -m lattice.importer``,
never edited by hand), and its edits are emitted into the original app with the
route-preserving merge (:func:`lattice.emit.thin.emit_into_app`): the import's verified
routes are Lattice, its islands stay the original code.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bench.edit_slice import prompts, run, smoke
from bench.edit_slice.apps import repo
from bench.edit_slice.tickets import REPO_TICKETS, ticket
from lattice.emit.thin import app_routes, make_emitted_app
from lattice.llm.client import ScriptedGenerator

REPOS = ("taskflow", "justnotes")
REPO_IDS = [t.id for t in REPO_TICKETS]

# per ticket, whether the routes it touches are Lattice in arm L's start state (the
# import's verified routes), islands (kept as the original code) or new -- the record of
# the round-5 CLI re-import (32k tokens, effort medium, per-route budgets, sound owner
# inference df8c1c5; spec 2026-09-27 surface §4)
LIFT_STATUS: dict[str, dict[str, str]] = {
    "t19_task_summary": {"GET /api/v1/tasks/summary": "new"},
    "t20_bulk_status": {"POST /api/v1/tasks/bulk-status": "new"},
    "t21_completed_at": {
        "POST /api/v1/tasks": "lifted",
        "GET /api/v1/tasks": "lifted",
        "GET /api/v1/tasks/{task_id}": "lifted",
        "PATCH /api/v1/tasks/{task_id}": "island",
    },
    "t22_copy_note": {"POST /notes/{id}/copy": "new"},
    "t23_note_filter": {"GET /notes": "island"},
    "t24_note_stats": {"GET /notes/stats": "new"},
}


@pytest.mark.parametrize("name", REPOS)
def test_arm_l_starts_from_the_committed_import(name):
    out = run.import_dir(name)
    programs, profile = run.start_lattice(name)
    assert run.has_lattice_start(name)
    assert profile == (out / "profile.py").read_text()
    assert programs == {p.name: p.read_text() for p in (out / "routes").glob("*.lattice.py")}
    assert programs, "the import verified no route"


@pytest.mark.parametrize("name", REPOS)
def test_the_start_state_emits_into_the_original_app(name, tmp_path):
    """Every route of the original app is still served: the lifted ones by the emitted
    Lattice, the islands by the original code; the app's own login works."""
    programs, profile = run.start_lattice(name)
    arm = run._LatticeArm(next(t for t in REPO_TICKETS if t.repo == name))
    lifted = arm.lifted()
    emitted = run.emit_lattice(name, programs, profile, tmp_path / "out", lifted)
    assert set(emitted.routes_emitted) == set(lifted)
    original = app_routes(run.APP_FACTORIES[name])
    assert len(emitted.routes_replaced) + len(emitted.routes_kept) == len(original)
    app = smoke.build_app(lambda p: make_emitted_app(tmp_path / "out", p), tmp_path, name)
    with TestClient(app) as client:
        token = smoke._login(client, "alice", repo(name))
        assert token


@pytest.mark.parametrize("tid", REPO_IDS)
def test_each_tickets_routes_are_lifted_islands_or_new(tid):
    assert run.lift_status(tid) == LIFT_STATUS[tid]


@pytest.mark.parametrize("tid", REPO_IDS)
def test_the_arm_l_prompt_shows_the_imported_programs(tid):
    t = ticket(tid)
    programs, _ = run.start_lattice(t.repo)
    system, user = run.l_prompt(tid)
    assert prompts.L_EDITING_IMPORTED.splitlines()[0] in system
    for name in prompts.l_program_files(t, programs):
        assert f"### {name}\n" in user
    assert "cannot be edited here" in user


@pytest.mark.parametrize("tid", ["t19_task_summary", "t24_note_stats"])
def test_a_fake_llm_arm_l_run_is_end_to_end(tid):
    """The start programs returned unchanged: checked, emitted into the app, smoke-run
    and scored by the oracle (the ticket is not done, so not correct)."""
    programs, _ = run.start_lattice(ticket(tid).repo)
    answer = prompts.render_files(programs)
    r = run.run_arm("L", tid, ScriptedGenerator([answer]), model="fake", max_repairs=0)
    assert r.round_problems == ["smoke"], r.detail
    assert r.outcome in ("loud", "silent") and sum(r.cases.values()) > 0


@pytest.mark.parametrize("tid", REPO_IDS)
def test_the_l_dry_run_has_no_reference_for_the_held_out_tickets(tid):
    r = run.dry_run("L", tid)
    assert r.outcome == "no_reference"


@pytest.mark.parametrize("name", REPOS)
def test_the_committed_import_emits_exactly_its_verified_routes(name):
    """Every route in the committed programs was verified by the importer (and checks),
    and every route of the app is in the report once."""
    import ast
    import json

    from lattice.importer.lift import check_program
    from lattice.importer.llm_lift import load_profile_source

    out = run.import_dir(name)
    report = json.loads((out / "report.json").read_text())
    verified = {r["name"] for r in report["routes"] if r["status"] == "verified"}
    profile = load_profile_source((out / "profile.py").read_text()).profile()
    emitted: set[str] = set()
    for path in (out / "routes").iterdir():
        plans, errors = check_program(path.read_text(), profile)
        assert plans is not None, (path.name, errors)
        tree = ast.parse(path.read_text())
        emitted |= {  # the endpoints (a @step is a procedure, not a route)
            n.name
            for n in tree.body
            if isinstance(n, ast.FunctionDef)
            and any(
                isinstance(d, ast.Call) and getattr(d.func, "id", None) == "endpoint"
                for d in n.decorator_list
            )
        }
    assert emitted == verified
    assert len(report["routes"]) == len(app_routes(run.APP_FACTORIES[name])) - len(
        [
            r
            for r in app_routes(run.APP_FACTORIES[name])
            if r.split(" ", 1)[1].startswith(("/docs", "/redoc", "/openapi"))
        ]
    )


# the committed live import (round 5, CLI backend, owner inference df8c1c5): repo ->
# (routes, verified, R0, R1, R2, R3, R4)
LIVE_COVERAGE = {
    "taskflow": (10, 6, 1, 4, 1, 0, 4),
    "justnotes": (30, 10, 1, 9, 0, 0, 20),
}
# the original's answers to a bad token and to one naming no user (spec §4.1)
TOKEN_ANSWERS = {
    "taskflow": ((401, "Could not validate credentials"), (401, "Could not validate credentials")),
    "justnotes": ((401, "Could not validate credentials"), (401, "User not found")),
}


@pytest.mark.parametrize("name", REPOS)
def test_the_committed_live_coverage(name):
    import json

    cov = json.loads((run.import_dir(name) / "report.json").read_text())["coverage"]
    got = tuple(cov[k] for k in ("routes", "verified", "R0", "R1", "R2", "R3", "R4"))
    assert got == LIVE_COVERAGE[name]


@pytest.mark.parametrize("name", REPOS)
def test_the_committed_profile_answers_tokens_like_the_original(name):
    """Profile-level, so a route an edit adds answers them too (round 3's t24)."""
    import json

    from lattice.importer.llm_lift import load_profile_source

    out = run.import_dir(name)
    auth = load_profile_source((out / "profile.py").read_text()).profile().api.auth
    assert (auth.invalid, auth.unknown) == TOKEN_ANSWERS[name]
    report = json.loads((out / "report.json").read_text())
    declared = [json.dumps(r.get("deviations", [])) for r in report["routes"]]
    assert "token" not in "".join(declared)
