"""Lattice Studio's own server (lattice/studio/server.py) as a Lattice spec: the dogfood
check. The app is the real server process, started per scenario by ``harness.py`` on a
fresh git copy of ``project_seed/`` (a toy notes spec) with the TEST-ONLY stub LLM
(``--stub-llm``), and checked as a black box over HTTP (layer D; ``check.sh``).

What makes the answers exact:
- the stub LLM appends the request as one rule (R5 on the seed's R1..R4) and drafts no new
  check (the new rule becomes context), so a change is deterministic;
- the harness runs git with fixed author, committer and dates and no user config, so the
  seed commit (``SEED_HEAD``) and the commit an accept makes are reproducible ids:
  ``STARRED_COMMIT`` is the one commit, parent ``SEED_HEAD``, touching REQUIREMENTS.md and
  spec.trace.json (spec.py is written unchanged) and not the untracked SCRATCH.txt --
  recorded from the real server on 2026-10-10 and checked by hand with ``git log
  --name-status`` (see docs/results/2026-10-10-dogfood-results.md);
- ``GET /api/changes/{id}?wait=30`` holds the request until the change's background job
  has finished, so no scenario races the drafting thread; ``stub:hold=2`` in a request
  keeps a change drafting for 2 seconds, long enough to cancel or reject it;
- the rebuild loop (API.md v1.7) runs the seed's TEST-ONLY stub agent (``[rebuild] agent =
  "stub"``): round 1 of ``stub-rebuild.json`` holds 2 seconds (long enough to send a second
  POST or a DELETE), writes ``progress.txt`` and tries to overwrite REQUIREMENTS.md; round 2
  writes ``writing.txt`` and ``reading.txt``; the gate ``./gate.sh`` passes iff both exist
  (each missing file is one finding, for ``scenario:create_a_note`` and
  ``scenario:read_own_and_other``). So a rebuild of the starred change passes in round 2,
  and its commit on ``rebuild/c1`` is the reproducible ``REBUILT_COMMIT`` (parent
  ``STARRED_COMMIT``, adding ``progress.txt``, ``reading.txt`` and ``writing.txt`` only) --
  recorded from the real server on 2026-10-10 (re-recorded for the two-section seed, see
  docs/results/2026-10-10-build-from-spec-results.md) and checked by hand with ``git log
  --all --name-status`` (docs/results/2026-10-10-rebuild-results.md);
  ``GET /api/rebuild/{id}?wait=30`` holds the request until the rebuild has finished.
- build from spec (API.md v1.8): the seed's requirements have two sections, so two areas
  (``writing-notes``: R1, R2; ``reading-notes``: R3, R4) plus ``all``. A scoped build plays
  ``stub-rebuild.json``'s rounds for its scope: ``writing-notes`` writes a draft, then
  ``writing.txt`` (passes in round 2; ``reading.txt`` stays missing, out of scope);
  ``reading-notes`` writes ``reading.txt`` and deletes ``writing.txt`` (a regression), then
  restores it. Builds land on the default target ``build/notes`` (or a named branch) and
  their commits (``WRITING_COMMIT``, ``READING_COMMIT``) are reproducible like the others.
  Only ``release`` is protected in the seed, so the checked-out ``main`` is refused as
  checked out; ``SCRATCH.txt`` (untracked) makes the owner's worktree dirty.
- build plans (API.md v1.9): the seed's TEST-ONLY stub planner (``[rebuild] planner =
  "stub"``) answers one planner document per plan scope from ``stub-plan.json``, and each
  node plays ``stub-rebuild.json``'s rounds under its node id. The scope ``all`` is a
  scaffold, two independent nodes (``writing``: the area writing-notes; ``reading``: R3),
  one dependent node (``tokens``: R4, after ``reading``) and the integration node; the
  scope of both areas declares two siblings writing one file (serialised); the scope
  ``rules R1, R3`` covers R1 twice and R3 never (the fallback); the two areas in the other
  order are two siblings that each pass alone but clash once merged (``gate.sh`` fails
  ``scenario:no_token`` when ``left.txt`` and ``right.txt`` both exist: a regression at the
  level's gate) and one of which writes a racy ``race.txt`` (``scenario:create_a_note``
  fails only with layer X, so the scoped node gate ``--layers CD`` cannot see it); each
  repairs its part in its round 2 (API.md v1.11: the level repair loop) and the level
  lands at ``REPAIR_TIP``; ``rules R1, R3, R4`` has a node that gives up (three rounds without
  ``writing.txt``) with a dependent and a sibling; ``rules R3`` holds 30 seconds (stop).
  The merge commits are reproducible like the others: ``PLAN_TIP`` (recorded from the
  real server and checked by hand with ``git log --graph build/notes``) pins the level
  merges and their order.
- build performance (API.md v1.11): the seed's ``[rebuild]`` sets ``scoped_gate_args``
  (``--layers CD --x-repeats 1 --await-cap 5``) and ``level_gate = "regression"``, and
  ``gate.sh`` takes those flags and ``--only-file`` like ``lattice spec check``: plan nodes
  run their gate on their scope plus the regression set, levels end with the regression
  gate, the last level with the full gate (``final_gate``).
- spec sync (API.md v1.12): the seed's ``[rebuild]`` sets ``sync_spec = "auto"``. Adding that
  line to the committed seed changed the seed commit, so every recorded commit id above
  (``SEED_HEAD`` to ``REPAIR_TIP``) was re-recorded from the real server on 2026-10-11 and
  checked by hand with ``git log --graph --all --name-status`` (same parents, files and order
  as before; see docs/results/2026-10-11-spec-sync-results.md). A scoped build makes a build
  branch at the seed, accepting the starred change moves main's spec on, and the build branch's
  spec is then behind: ``SYNC_COMMIT`` (parent ``WRITING_COMMIT``, modifying REQUIREMENTS.md and
  spec.trace.json only, message ``spec: sync from main <sha7>`` with the
  ``Lattice-Spec-Sync`` trailer) is recorded the same way.

Studio has no database and no login: there are no tables, invariants, machines or users,
and layers B, C and R have nothing to bind to (the harness's marker database is never
read). Self-contained on purpose: Studio loads this module in isolation.
"""

from lattice.profile import ApiProfile, NoRoles, Profile
from lattice.spec import Scenario, Spec, Step, Unchecked

# ---- the seed project (dogfood/studio/project_seed; harness.py checks they agree) ---------

SEED_MD = """\
# Notes app

## Writing notes

- A signed-in user creates a note with POST /notes {"text": ...}; it answers 201 with the note.
- A note's text is 1 to 200 characters; anything else answers 422.

## Reading notes

- GET /notes/{id} answers the caller's own note; another user's note answers 404.
- Requests without a token answer 401.
"""
SEED_HEAD = "6ad9f48ca3578194cc702af980b84da259a86d16"
STARRED = "Notes can be starred."
STARRED_MD = SEED_MD + f"- {STARRED}\n"
STARRED_COMMIT = "f372a593ce33766b430348683f614469494eec06"
FIRST = "Starred notes come first."
TRAILER = (
    "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
    "Claude-Session: https://claude.ai/code/session_01DkfBU2ur5VhLPgqfejszsZ"
)
EDIT_FAILED = (
    "RuntimeError: the markdown edit failed: the reply has no document between the lines "
    "'<<<BEGIN REQUIREMENTS.md' and 'END REQUIREMENTS.md>>>'"
)
SEED_RULES = [
    {"id": "R1", "status": "checked", "links": ["scenario:create_a_note"]},
    {"id": "R2", "status": "checked", "links": ["scenario:text_bounds"]},
    {"id": "R3", "status": "checked", "links": ["scenario:read_own_and_other"]},
    {"id": "R4", "status": "checked", "links": ["scenario:no_token"]},
]
NO_CHANGE = "change {} is {}, not ready"
REBUILT_COMMIT = "37163703f2580004853d91dc422f824f4c172f62"
REBUILT_MESSAGE = (
    f"build: {STARRED}\n\nRebuilt by Lattice Studio from the accepted change(s) c1; the "
    "gates passed in round 2 of 3.\n\nLattice-Rebuild: b1 (notes)\n"
)
SPEC_EDITED = "spec files were edited (restored from the base commit): REQUIREMENTS.md"
W_FINDING = "FAIL writing.txt is missing (scenario:create_a_note)"
R_FINDING = "FAIL reading.txt is missing (scenario:read_own_and_other)"
W_TARGET, R_TARGET = "scenario:create_a_note", "scenario:read_own_and_other"
ROUND_1 = {
    "n": 1,
    "agent_summary": "round 1: started the build, and tried to edit the requirements",
    "files_changed": ["progress.txt"],
    "spec_edited": ["REQUIREMENTS.md"],
    "check": {
        "verdict": "fail",
        "counts": {"G": 2},
        "findings": [
            {"id": "f1", "title": SPEC_EDITED, "target": "spec"},
            {"id": "f2", "title": W_FINDING, "target": W_TARGET},
            {"id": "f3", "title": R_FINDING, "target": R_TARGET},
        ],
    },
    "gates": [{"command": "./gate.sh", "exit_code": 1}],
}
ROUND_2 = {
    "n": 2,
    "agent_summary": "round 2: wrote writing.txt and reading.txt",
    "files_changed": ["reading.txt", "writing.txt"],
    "spec_edited": [],
    "check": {"verdict": "pass", "counts": {"G": 0}, "findings": []},
    "gates": [{"command": "./gate.sh", "exit_code": 0}],
}


def post_change(request, parent=None, cid="c1"):
    body = {"request": request} if parent is None else {"request": request, "parent": parent}
    return Step("POST", "/api/changes", json=body, expect_body={"id": cid})


def wait(cid, status, **fields):
    """The change once its background job has finished."""
    return Step("GET", f"/api/changes/{cid}", params={"wait": 30},
                expect_body={"id": cid, "status": status, **fields})  # fmt: skip


def model(**fields):
    return Step("GET", "/api/model", expect_body=fields)


def accept(cid, status=200, **body):
    return Step("POST", f"/api/changes/{cid}/accept", expect_status=status,
                expect_body=body or None)  # fmt: skip


def refused(method, path, status, detail, json=None, params=None):
    return Step(method, path, json=json, params=params, expect_status=status,
                expect_body={"detail": detail})  # fmt: skip


def rebuild(changes, rid="b1", **body):
    return Step("POST", "/api/rebuild", json={"changes": changes, **body},
                expect_body={"id": rid})  # fmt: skip


def rebuilt(rid, status, **fields):
    """The rebuild once it has finished."""
    return Step("GET", f"/api/rebuild/{rid}", params={"wait": 30},
                expect_body={"id": rid, "status": status, **fields})  # fmt: skip


READY_STARRED = wait(
    "c1", "ready", parent=None, error=None, superseded_by=None, checks={"valid": True},
    rules={"added": ["R5"], "changed": [], "removed": []},
)  # fmt: skip
UNCHANGED = model(markdown=SEED_MD, project={"name": "notes", "git_head": SEED_HEAD[:7]})

# ---- connected MCP servers (API.md v1.13) ----------------------------------------------------
# the stub LLM runs no Claude Code session: GET /api/mcp answers this and starts no process
MCP_STUB = {"servers": [], "refreshed_at": None,
            "allowed_in": {"ask": 0, "explain": 0, "change": 0},
            "note": "The stub LLM backend (test only) runs no Claude Code session, so no connected "
                    "MCP server is discovered or used."}  # fmt: skip
SOURCE = {"server": "claude.ai Linear", "item": "PROJ-7", "url": "https://tracker.example.com/PROJ-7",
          "title": "Notes can be starred"}  # fmt: skip

# ---- build from spec (API.md v1.8) ---------------------------------------------------------
WRITING_COMMIT = "c15cbb303a97efa936a6018111f52b3926a6f845"
READING_COMMIT = "04e0ae929beb561410a4d2cef4873fdc568f6776"
WRITING = {"kind": "area", "id": "writing-notes", "title": "Writing notes", "rules": ["R1", "R2"]}
READING = {"kind": "area", "id": "reading-notes", "title": "Reading notes", "rules": ["R3", "R4"]}
NOTHING = "nothing to build: the checks in this scope already pass"

# ---- build plans (API.md v1.9) ---------------------------------------------------------------
PLAN_TIP = "569ffcaf70f9190700b918ad8546610c158d9ffd"
# API.md v1.11: the clash plan's level 0, first merged (w-clash + r-clash: the race and the
# clash fail the level gate), then repaired by both nodes and merged again (recorded from the
# real server and checked by hand with `git log --graph --all`, see the results doc)
CLASH_FAILED_TIP = "e4b250526acd7a24c98f9d7f71f689d59b266cb0"
REPAIR_TIP = "85edcac51d657a188f63822c38bd087417b4fb4f"
SCOPED_ARGS = ["--layers", "CD", "--x-repeats", "1", "--await-cap", "5"]
T_TARGET, N_TARGET = "scenario:text_bounds", "scenario:no_token"
NOT_APPROVED = "plan p1 is ready, not approved: approve it first"
NO_TOUCH = {"tables": [], "packages": [], "files": []}
NOTES_FILE = {"tables": [], "packages": [], "files": ["notes.py"]}
RETRY_NOTE = (
    "Node w-broken gave up. Run retries it (attempt 2 of 3) with the earlier "
    "findings, from level 0; the 1 node that passed is kept."
)
CAPPED_NOTE = (
    "Node w-broken already had 3 attempts (the cap is [rebuild] max_attempts = 3); "
    "revise the plan or raise the cap to try again."
)
ALL_RULES = ["R1", "R2", "R3", "R4"]

# ---- spec sync (API.md v1.12) -----------------------------------------------------------------
# the sync commit of STARRED_COMMIT's spec onto WRITING_COMMIT (the same on build/notes and on
# feature/w: same parent, tree, message and fixed dates), recorded from the real server
SYNC_COMMIT = "94da6cdf11f0b765bf8c687052d5be6ed3821854"
SYNC_FILES = ["REQUIREMENTS.md", "spec.trace.json"]
SYNC_DRIFT = {"head": STARRED_COMMIT, "files": SYNC_FILES}
BEHIND = {"in_sync": False, "files": SYNC_FILES, "head": STARRED_COMMIT}
IN_SYNC = {"in_sync": True, "files": [], "head": STARRED_COMMIT}
NOTHING_TO_SYNC = ("the spec on {} is the same as on main at " + STARRED_COMMIT[:7]
                   + ": nothing to sync")


def drift_warning(target):
    return (f"The spec on main differs from {target} in REQUIREMENTS.md, spec.trace.json: "
            "sync it first")


def sync(name, status=200, **body):
    return Step("POST", f"/api/branches/{name}/sync-spec", json={}, expect_status=status,
                expect_body=body or None)  # fmt: skip


ACCEPT_STARRED = (post_change(STARRED), READY_STARRED, accept("c1", commit=STARRED_COMMIT))


def post_plan(body, pid="p1"):
    return Step("POST", "/api/plan", json=body, expect_body={"id": pid, "status": "planning"})


def planned(pid, status, **fields):
    """The plan once it is neither planning nor running."""
    return Step("GET", f"/api/plan/{pid}", params={"wait": 30},
                expect_body={"id": pid, "status": status, **fields})  # fmt: skip


def plan_step(pid, verb, code=200, **body):
    return Step("POST", f"/api/plan/{pid}/{verb}", json={}, expect_status=code,
                expect_body=body or None)  # fmt: skip


def approve_and_run(pid="p1"):
    return (plan_step(pid, "approve", id=pid, status="approved"),
            plan_step(pid, "run", id=pid, status="running"))  # fmt: skip


def area(aid, title, rules, scenarios, failing, status, branch="build/notes"):
    return {"id": aid, "title": title, "rules": rules, "scenarios": scenarios,
            "checks": scenarios, "failing": failing, "status": status,
            "target_branch": branch}  # fmt: skip


def areas(w, r, a, branch="build/notes", params=None):
    """GET /api/areas: (failing, status) of writing-notes, reading-notes and all."""
    return Step("GET", "/api/areas", params=params, expect_body=[
        area("writing-notes", "Writing notes", ["R1", "R2"], 2, *w, branch=branch),
        area("reading-notes", "Reading notes", ["R3", "R4"], 2, *r, branch=branch),
        area("all", "All", ["R1", "R2", "R3", "R4"], 4, *a, branch=branch),
    ])  # fmt: skip


def build(scope, rid="b1", **body):
    return Step("POST", "/api/rebuild", json={"scope": scope, **body},
                expect_body={"id": rid})  # fmt: skip


def coded(method, path, status, code, json=None):
    return Step(method, path, json=json, expect_status=status, expect_body={"code": code})


def finding(fid, title, target, in_scope, regression=False):
    return {"id": fid, "title": title, "target": target, "in_scope": in_scope,
            "regression": regression}  # fmt: skip


W_ROUND_1 = {
    "n": 1, "agent_summary": "writing 1: a draft only", "files_changed": ["draft.txt"],
    "check": {"verdict": "fail", "counts": {"G": 2}, "findings": [
        finding("f1", W_FINDING, W_TARGET, True), finding("f2", R_FINDING, R_TARGET, False)]},
    "scope_passing": 0, "scope_total": 1, "regressions": [], "newly_fixed": [],
}  # fmt: skip
W_ROUND_2 = {
    "n": 2, "agent_summary": "writing 2: wrote writing.txt", "files_changed": ["writing.txt"],
    "check": {"verdict": "pass", "counts": {"G": 1}, "findings": [
        finding("f1", R_FINDING, R_TARGET, False)]},
    "scope_passing": 1, "scope_total": 1, "regressions": [], "newly_fixed": [W_TARGET],
}  # fmt: skip
R_ROUND_1 = {
    "n": 1, "agent_summary": "reading 1: wrote reading.txt, deleted writing.txt",
    "files_changed": ["reading.txt", "writing.txt"],
    "check": {"verdict": "fail", "counts": {"G": 1}, "findings": [
        finding("f1", W_FINDING, W_TARGET, False, regression=True)]},
    "scope_passing": 1, "scope_total": 1, "regressions": [W_TARGET], "newly_fixed": [R_TARGET],
}  # fmt: skip
R_ROUND_2 = {
    "n": 2, "agent_summary": "reading 2: restored writing.txt", "files_changed": ["writing.txt"],
    "check": {"verdict": "pass", "counts": {"G": 0}, "findings": []},
    "scope_passing": 1, "scope_total": 1, "regressions": [], "newly_fixed": [R_TARGET],
}  # fmt: skip
W_BASELINE = {
    "verdict": "fail", "counts": {"G": 2}, "unmapped": [],
    "findings": [
        {"id": "f1", "title": W_FINDING, "target": W_TARGET, "rules": ["R1"], "in_scope": True},
        {"id": "f2", "title": R_FINDING, "target": R_TARGET, "rules": ["R3"], "in_scope": False},
    ],
}  # fmt: skip
R_BASELINE = {
    "verdict": "fail", "counts": {"G": 1}, "unmapped": [],
    "findings": [
        {"id": "f1", "title": R_FINDING, "target": R_TARGET, "rules": ["R3"], "in_scope": True},
    ],
}  # fmt: skip

# fmt: off
SCENARIOS = (
    Scenario("the_model_answers_the_project", steps=(
        model(
            project={"name": "notes", "git_head": SEED_HEAD[:7]},
            markdown=SEED_MD,
            rules=SEED_RULES,
            data_model={"tables": [{"name": "User", "sql": "users"},
                                   {"name": "Note", "sql": "notes"}]},
            app={"configured": False, "check_running": False, "last_check": None},
            changes=[],
        ),
    )),
    Scenario("a_change_is_drafted_to_ready", steps=(
        post_change(STARRED),
        READY_STARRED,
        model(changes=[{"id": "c1", "request": STARRED, "status": "ready"}],
              markdown=SEED_MD, project={"git_head": SEED_HEAD[:7]}),
        post_change(FIRST, cid="c2"),
        wait("c2", "ready", rules={"added": ["R5"], "changed": [], "removed": []}),
    )),
    Scenario("accept_commits_once", steps=(
        post_change(STARRED),
        READY_STARRED,
        accept("c1", commit=STARRED_COMMIT, message=f"spec: {STARRED}\n\n{TRAILER}\n"),
        model(markdown=STARRED_MD,
              project={"name": "notes", "git_head": STARRED_COMMIT[:7]},
              changes=[{"id": "c1", "status": "accepted"}],
              rules=[*SEED_RULES, {"id": "R5", "status": "context"}]),
        wait("c1", "accepted"),
    )),
    Scenario("a_second_accept_is_409", steps=(
        post_change(STARRED),
        READY_STARRED,
        accept("c1", commit=STARRED_COMMIT),
        refused("POST", "/api/changes/c1/accept", 409, NO_CHANGE.format("c1", "accepted")),
        model(project={"git_head": STARRED_COMMIT[:7]}, markdown=STARRED_MD),
    )),
    Scenario("a_stale_proposal_is_409", steps=(
        post_change(STARRED),
        post_change(FIRST, cid="c2"),
        READY_STARRED,
        wait("c2", "ready"),
        accept("c1", commit=STARRED_COMMIT),
        refused("POST", "/api/changes/c2/accept", 409, "the spec changed since this proposal"),
        model(markdown=STARRED_MD, project={"git_head": STARRED_COMMIT[:7]},
              changes=[{"id": "c1", "status": "accepted"}, {"id": "c2", "status": "ready"}]),
    )),
    Scenario("reject_then_accept_is_409", steps=(
        post_change(STARRED),
        READY_STARRED,
        Step("POST", "/api/changes/c1/reject", expect_body={"ok": True}),
        refused("POST", "/api/changes/c1/accept", 409, NO_CHANGE.format("c1", "rejected")),
        model(changes=[{"id": "c1", "status": "rejected"}], markdown=SEED_MD,
              project={"git_head": SEED_HEAD[:7]}),
    )),
    Scenario("an_accepted_change_cannot_be_rejected", steps=(
        post_change(STARRED),
        READY_STARRED,
        accept("c1", commit=STARRED_COMMIT),
        refused("POST", "/api/changes/c1/reject", 409, "change c1 is already accepted"),
        wait("c1", "accepted"),
    )),
    Scenario("a_change_rejected_while_drafting_stays_rejected", steps=(
        post_change("Notes can be archived. stub:hold=2"),
        Step("GET", "/api/changes/c1", expect_body={"status": "drafting"}),
        Step("POST", "/api/changes/c1/reject", expect_body={"ok": True}),
        wait("c1", "rejected"),
        refused("POST", "/api/changes/c1/accept", 409, NO_CHANGE.format("c1", "rejected")),
        UNCHANGED,
    )),
    Scenario("cancel_while_drafting", steps=(
        post_change("Notes can be pinned. stub:hold=2"),
        Step("GET", "/api/changes/c1", expect_body={"status": "drafting"}),
        Step("DELETE", "/api/changes/c1", expect_body={"ok": True, "status": "cancelled"}),
        wait("c1", "cancelled"),
        refused("DELETE", "/api/changes/c1", 409,
                "change c1 is cancelled; only a change still drafting or checking can be "
                "cancelled"),
        refused("POST", "/api/changes/c1/accept", 409, NO_CHANGE.format("c1", "cancelled")),
        UNCHANGED,
    )),
    Scenario("cancel_after_ready_is_409", steps=(
        post_change(STARRED),
        READY_STARRED,
        refused("DELETE", "/api/changes/c1", 409,
                "change c1 is ready; only a change still drafting or checking can be "
                "cancelled"),
        wait("c1", "ready"),
        accept("c1", commit=STARRED_COMMIT),
        refused("DELETE", "/api/changes/c1", 409,
                "change c1 is accepted; only a change still drafting or checking can be "
                "cancelled"),
    )),
    Scenario("a_failed_change_cannot_be_accepted_or_cancelled", steps=(
        post_change("stub:fail Notes can be shared."),
        wait("c1", "failed", error=EDIT_FAILED),
        refused("POST", "/api/changes/c1/accept", 409, NO_CHANGE.format("c1", "failed")),
        refused("DELETE", "/api/changes/c1", 409,
                "change c1 is failed; only a change still drafting or checking can be "
                "cancelled"),
        refused("POST", "/api/changes", 409, NO_CHANGE.format("c1", "failed"),
                json={"request": FIRST, "parent": "c1"}),
        UNCHANGED,
    )),
    Scenario("a_follow_up_supersedes_its_parent", steps=(
        post_change(STARRED),
        READY_STARRED,
        post_change(FIRST, parent="c1", cid="c2"),
        wait("c2", "ready", parent="c1", superseded_by=None,
             rules={"added": ["R6"], "changed": [], "removed": []}),
        wait("c1", "rejected", superseded_by="c2"),
        refused("POST", "/api/changes", 409, NO_CHANGE.format("c1", "rejected"),
                json={"request": "Another try.", "parent": "c1"}),
        refused("POST", "/api/changes/c1/accept", 409, NO_CHANGE.format("c1", "rejected")),
        accept("c2", message=f"spec: {FIRST}\n\n{TRAILER}\n"),
        model(markdown=STARRED_MD + f"- {FIRST}\n",
              changes=[{"id": "c1", "status": "rejected"}, {"id": "c2", "status": "accepted"}],
              rules=[*SEED_RULES, {"id": "R5", "status": "context"},
                     {"id": "R6", "status": "context"}]),
    )),
    Scenario("bad_input", steps=(
        refused("POST", "/api/changes", 422, "request is empty", json={"request": ""}),
        refused("POST", "/api/changes", 422, "request is empty", json={"request": "   "}),
        refused("POST", "/api/changes", 422, "request is empty"),
        refused("POST", "/api/changes", 400, "the body must be a JSON object", json=[1, 2]),
        refused("POST", "/api/ask", 422, "question is empty", json={"question": " "}),
        refused("POST", "/api/ask", 400, "the body must be a JSON object", json="a question"),
        post_change(STARRED),
        refused("GET", "/api/changes/c1", 422, "wait must be a number of seconds, 0 to 60",
                params={"wait": "soon"}),
        refused("GET", "/api/changes/c1", 422, "wait must be a number of seconds, 0 to 60",
                params={"wait": 61}),
        refused("GET", "/api/changes/c1", 422, "wait must be a number of seconds, 0 to 60",
                params={"wait": -1}),
        READY_STARRED,
        model(changes=[{"id": "c1", "status": "ready"}]),
    )),
    Scenario("unknown_ids_and_routes_are_404", steps=(
        refused("GET", "/api/changes/c99", 404, "no change c99"),
        refused("GET", "/api/changes/c99", 404, "no change c99", params={"wait": 1}),
        refused("POST", "/api/changes/c99/accept", 404, "no change c99"),
        refused("POST", "/api/changes/c99/reject", 404, "no change c99"),
        refused("DELETE", "/api/changes/c99", 404, "no change c99"),
        refused("POST", "/api/changes", 404, "no change c42",
                json={"request": "x", "parent": "c42"}),
        refused("GET", "/api/nope", 404, "no such route"),
        refused("POST", "/api/nope", 404, "no such route"),
        refused("DELETE", "/api/nope", 404, "no such route"),
        model(changes=[]),
    )),
    Scenario("ask_answers_in_the_contract_shape", steps=(
        Step("POST", "/api/ask", json={"question": "Can a user star a note?"},
             expect_body={"kind": "not_specified", "citations": [], "verified": None}),
    )),
    Scenario("projects_lists_the_served_project", steps=(
        Step("GET", "/api/projects", expect_body=[
            {"name": "notes", "git_head": SEED_HEAD[:7], "spec": True,
             "app_configured": False, "last_check": None, "default": True,
             "capabilities": {"rebuild": True},
             "plan_defaults": {"max_parallel": 2, "level_gate": "regression",
                               "scoped_gate": SCOPED_ARGS, "node_timeout": 1800.0,
                               "level_repair_attempts": 2,
                               "models": {"fast": "haiku", "mid": "sonnet",
                                          "frontier": "opus"}}},
        ]),
        Step("GET", "/p/notes/api/projects", expect_body=[{"name": "notes", "default": True}]),
    )),
    Scenario("a_project_prefix_serves_the_same_project", steps=(
        Step("GET", "/p/notes/api/model", expect_body={
            "project": {"name": "notes", "git_head": SEED_HEAD[:7]},
            "markdown": SEED_MD, "rules": SEED_RULES, "changes": []}),
        Step("POST", "/p/notes/api/changes", json={"request": STARRED},
             expect_body={"id": "c1"}),
        READY_STARRED,
        Step("GET", "/p/notes/api/changes/c1", params={"wait": 30},
             expect_body={"id": "c1", "status": "ready", "request": STARRED}),
        refused("GET", "/p/notes/api/changes/c1", 422,
                "wait must be a number of seconds, 0 to 60", params={"wait": "soon"}),
        Step("GET", "/p/notes/api/model",
             expect_body={"changes": [{"id": "c1", "request": STARRED, "status": "ready"}]}),
        accept("c1", commit=STARRED_COMMIT),
        Step("GET", "/p/notes/api/model", expect_body={
            "markdown": STARRED_MD, "project": {"git_head": STARRED_COMMIT[:7]},
            "changes": [{"id": "c1", "status": "accepted"}]}),
        refused("GET", "/p/notes/api/nope", 404, "no such route"),
    )),
    Scenario("an_unknown_project_is_404", steps=(
        refused("GET", "/p/nope/api/model", 404, "no such project"),
        refused("GET", "/p/nope/api/changes/c1", 404, "no such project", params={"wait": 1}),
        refused("POST", "/p/nope/api/changes", 404, "no such project",
                json={"request": STARRED}),
        refused("DELETE", "/p/nope/api/changes/c1", 404, "no such project"),
        refused("GET", "/p/nope/", 404, "no such project"),
        model(changes=[]),
    )),
    Scenario("a_rebuild_passes_in_round_2_on_its_own_branch", steps=(
        post_change(STARRED),
        READY_STARRED,
        accept("c1", commit=STARRED_COMMIT),
        model(changes=[{"id": "c1", "status": "accepted", "build": {"state": "not_built"}}]),
        rebuild(["c1"]),
        refused("POST", "/api/rebuild", 409, "change c1 is already rebuilding",
                json={"changes": ["c1"]}),
        rebuilt("b1", "passed", changes=["c1"], agent="stub", max_rounds=3,
                base=STARRED_COMMIT, branch="rebuild/c1", rounds=[ROUND_1, ROUND_2],
                targets=[], error=None,
                commit={"sha": REBUILT_COMMIT, "message": REBUILT_MESSAGE,
                        "files_changed": ["progress.txt", "reading.txt", "writing.txt"]}),
        model(markdown=STARRED_MD, project={"name": "notes", "git_head": STARRED_COMMIT[:7]},
              changes=[{"id": "c1", "status": "accepted", "build": {
                  "state": "built", "rebuild_id": "b1", "branch": "rebuild/c1",
                  "commit": REBUILT_COMMIT}}]),
        # post hoc (docs/results/2026-10-10-rebuild-results.md): the live HEAD, as
        # /api/projects reads it, did not move either
        Step("GET", "/api/projects", expect_body=[{"name": "notes",
                                                   "git_head": STARRED_COMMIT[:7]}]),
        Step("GET", "/p/notes/api/rebuild/b1",
             expect_body={"id": "b1", "status": "passed", "commit": {"sha": REBUILT_COMMIT}}),
        refused("DELETE", "/api/rebuild/b1", 409, "cannot stop a rebuild that is passed"),
        refused("POST", "/api/rebuild", 409, "change c1 is already built",
                json={"changes": ["c1"]}),
    )),
    Scenario("a_rebuild_gives_up_at_max_rounds_and_can_run_again", steps=(
        post_change(STARRED),
        READY_STARRED,
        accept("c1", commit=STARRED_COMMIT),
        rebuild(["c1"], max_rounds=1),
        rebuilt("b1", "gave_up", max_rounds=1, branch="rebuild/c1", merge_hint=None,
                rounds=[ROUND_1]),
        model(project={"git_head": STARRED_COMMIT[:7]}, markdown=STARRED_MD,
              changes=[{"id": "c1", "build": {"state": "gave_up", "rebuild_id": "b1",
                                              "branch": "rebuild/c1"}}]),
        rebuild(["c1"], rid="b2"),
        rebuilt("b2", "passed", branch="rebuild/c1-2", base=STARRED_COMMIT,
                rounds=[ROUND_1, ROUND_2]),
        model(project={"git_head": STARRED_COMMIT[:7]},
              changes=[{"id": "c1", "build": {"state": "built", "rebuild_id": "b2",
                                              "branch": "rebuild/c1-2"}}]),
    )),
    Scenario("one_rebuild_at_a_time_and_stop", steps=(
        post_change(STARRED),
        READY_STARRED,
        accept("c1", commit=STARRED_COMMIT),
        post_change(FIRST, cid="c2"),
        wait("c2", "ready"),
        accept("c2"),
        rebuild(["c1"]),
        refused("POST", "/api/rebuild", 409, "a rebuild is already running in this project (b1)",
                json={"changes": ["c2"]}),
        Step("DELETE", "/api/rebuild/b1", expect_body={"id": "b1", "status": "stopped"}),
        rebuilt("b1", "stopped", rounds=[], branch=None, worktree=None),
        model(changes=[{"id": "c1", "build": {"state": "not_built", "rebuild_id": "b1"}},
                       {"id": "c2", "build": {"state": "not_built"}}]),
        refused("DELETE", "/api/rebuild/b1", 409, "cannot stop a rebuild that is stopped"),
        rebuild(["c2"], rid="b2"),
        rebuilt("b2", "passed", changes=["c2"], branch="rebuild/c2"),
    )),
    Scenario("rebuild_refusals", steps=(
        refused("POST", "/api/rebuild", 404, "no change c9", json={"changes": ["c9"]}),
        refused("POST", "/api/rebuild", 422, "changes must be a non-empty list of ids", json={}),
        refused("POST", "/api/rebuild", 422, "changes must be a non-empty list of ids",
                json={"changes": []}),
        refused("POST", "/api/rebuild", 422, "max_rounds must be 1 to 50",
                json={"changes": ["c1"], "max_rounds": 0}),
        post_change(STARRED),
        READY_STARRED,
        refused("POST", "/api/rebuild", 409, "change c1 is ready, not accepted",
                json={"changes": ["c1"]}),
        refused("GET", "/api/rebuild/b9", 404, "no rebuild b9"),
        refused("DELETE", "/api/rebuild/b9", 404, "no rebuild b9"),
        refused("GET", "/api/rebuild/b9", 422, "wait must be a number of seconds, 0 to 60",
                params={"wait": "soon"}),
        UNCHANGED,
    )),
    Scenario("areas_and_branches_of_a_new_project", steps=(
        areas((None, "not_built"), (None, "not_built"), (None, "not_built")),
        Step("GET", "/p/notes/api/areas", expect_body=[{"id": "writing-notes"},
                                                       {"id": "reading-notes"}, {"id": "all"}]),
        Step("GET", "/api/branches", expect_body={
            "branches": [{"name": "main", "head": SEED_HEAD, "current": True,
                          "protected": False, "ahead_of_base": 0, "is_integration": False}],
            "current_branch": "main", "protected": ["release"],
            "default_target": "build/notes"}),
        Step("GET", "/api/projects", expect_body=[
            {"name": "notes", "capabilities": {"rebuild": True, "build_from_spec": True}}]),
    )),
    Scenario("scoped_builds_accumulate_on_the_integration_branch", steps=(
        build({"area": "writing-notes"}),
        rebuilt("b1", "passed", scope=WRITING, changes=[], agent="stub", max_rounds=3,
                branch="rebuild/writing-notes-1", target_branch="build/notes",
                integration_branch="build/notes", created_branch=True, base_ref="HEAD",
                base=SEED_HEAD, integration_base=SEED_HEAD, merged_head=None,
                baseline=W_BASELINE, rounds=[W_ROUND_1, W_ROUND_2],
                targets=[{"id": W_TARGET, "label": W_FINDING, "done": True}],
                commit={"sha": WRITING_COMMIT, "files_changed": ["draft.txt", "writing.txt"]},
                commit_range={"from": SEED_HEAD, "to": WRITING_COMMIT},
                error=None, warnings=[]),
        areas((0, "built"), (1, "partial"), (1, "partial")),
        # the owner's branch and checkout never move
        model(markdown=SEED_MD, project={"name": "notes", "git_head": SEED_HEAD[:7]}),
        Step("GET", "/api/branches", expect_body={"current_branch": "main", "branches": [
            {"name": "build/notes", "head": WRITING_COMMIT, "current": False,
             "ahead_of_base": 1, "is_integration": True},
            {"name": "main", "head": SEED_HEAD, "current": True},
            {"name": "rebuild/writing-notes-1", "head": WRITING_COMMIT, "current": False},
        ]}),
        build({"area": "reading-notes"}, rid="b2"),
        rebuilt("b2", "passed", scope=READING, branch="rebuild/reading-notes-1",
                target_branch="build/notes", created_branch=False, base_ref="build/notes",
                base=WRITING_COMMIT, integration_base=WRITING_COMMIT, baseline=R_BASELINE,
                rounds=[R_ROUND_1, R_ROUND_2],
                commit={"sha": READING_COMMIT, "files_changed": ["reading.txt"]},
                commit_range={"from": WRITING_COMMIT, "to": READING_COMMIT}),
        areas((0, "built"), (0, "built"), (0, "built")),
        refused("POST", "/api/rebuild", 409, NOTHING, json={"scope": {"area": "writing-notes"}}),
        refused("POST", "/api/rebuild", 409, NOTHING, json={"scope": "all"}),
        Step("GET", "/api/rebuild", expect_body=[{"id": "b1"}, {"id": "b2"}]),
        Step("GET", "/api/projects", expect_body=[{"name": "notes",
                                                   "git_head": SEED_HEAD[:7]}]),
    )),
    Scenario("scoped_build_refusals", steps=(
        refused("POST", "/api/rebuild", 404, "no area nope", json={"scope": {"area": "nope"}}),
        refused("POST", "/api/rebuild", 404, "no rule R9",
                json={"scope": {"rules": ["R1", "R9"]}}),
        refused("POST", "/api/rebuild", 422,
                'scope must be "all", {"area": "<id>"} or {"rules": ["<id>", ...]}',
                json={"scope": "writing-notes"}),
        refused("POST", "/api/rebuild", 422, "branch needs a scope",
                json={"changes": ["c1"], "branch": {"name": "x"}}),
        coded("POST", "/api/rebuild", 409, "branch_checked_out",
              json={"scope": "all", "branch": {"name": "main"}}),
        coded("POST", "/api/rebuild", 409, "tree_dirty",
              json={"scope": "all", "branch": {"name": "main"}, "confirm_unsafe_branch": True}),
        coded("POST", "/api/rebuild", 409, "branch_protected",
              json={"scope": "all", "branch": {"name": "release", "create": True}}),
        coded("POST", "/api/rebuild", 404, "no_branch",
              json={"scope": "all", "branch": {"name": "nope"}}),
        coded("POST", "/api/rebuild", 422, "branch_name_invalid",
              json={"scope": "all", "branch": {"name": "bad..name", "create": True}}),
        coded("POST", "/api/rebuild", 404, "no_ref",
              json={"scope": "all", "branch": {"name": "x", "create": True, "from": "nosuch"}}),
        Step("GET", "/api/branches", expect_body={"branches": [{"name": "main"}]}),
        Step("GET", "/api/rebuild", expect_body=[]),
        UNCHANGED,
    )),
    Scenario("a_named_target_branch_is_created_once", steps=(
        build({"area": "writing-notes"}, branch={"name": "feature/w", "create": True}),
        rebuilt("b1", "passed", target_branch="feature/w", created_branch=True,
                base_ref="HEAD", base=SEED_HEAD, commit={"sha": WRITING_COMMIT}),
        coded("POST", "/api/rebuild", 409, "branch_exists",
              json={"scope": {"area": "reading-notes"},
                    "branch": {"name": "feature/w", "create": True}}),
        Step("GET", "/api/branches", expect_body={"branches": [
            {"name": "feature/w", "head": WRITING_COMMIT, "is_integration": True},
            {"name": "main", "head": SEED_HEAD},
            {"name": "rebuild/writing-notes-1", "head": WRITING_COMMIT},
        ]}),
        build({"area": "reading-notes"}, rid="b2", branch={"name": "feature/w"}),
        rebuilt("b2", "passed", target_branch="feature/w", created_branch=False,
                base_ref="feature/w", base=WRITING_COMMIT, commit={"sha": READING_COMMIT}),
        areas((0, "built"), (0, "built"), (0, "built"), branch="feature/w",
              params={"branch": "feature/w"}),
        areas((None, "not_built"), (None, "not_built"), (None, "not_built")),
        UNCHANGED,
    )),
    Scenario("a_plan_runs_level_by_level", steps=(
        post_plan({"scope": "all"}),
        planned("p1", "ready", source="agent", fallback_reason=None, version=1,
                target_branch="build/notes", max_parallel=2,
                scope={"kind": "all", "rules": ALL_RULES},
                levels=[["scaffold"], ["writing", "reading"], ["tokens"], ["integration"]],
                validation={"ok": True, "issues": [], "auto_edits": []},
                nodes=[
                    {"id": "scaffold", "kind": "scaffold", "scope": {"kind": "rules", "rules": []},
                     "depends_on": [], "tier": "mid", "model": "sonnet", "level": 0,
                     "touches": {**NO_TOUCH, "files": ["scaffold.txt"]}, "status": "pending",
                     "gate": {"kind": "scoped", "regressions": "none", "decided_by": "studio"}},
                    {"id": "writing", "kind": "area",
                     "scope": {"kind": "area", "area": "writing-notes", "rules": ["R1", "R2"]},
                     "depends_on": ["scaffold"], "tier": "mid", "model": "sonnet", "level": 1,
                     "gate": {"kind": "scoped", "rules": ["R1", "R2"],
                              "checks": [W_TARGET, "scenario:text_bounds"]}},
                    {"id": "reading", "scope": {"kind": "rules", "rules": ["R3"]},
                     "depends_on": ["scaffold"], "tier": "frontier", "model": "opus", "level": 1},
                    {"id": "tokens", "depends_on": ["reading"], "tier": "fast", "model": "haiku",
                     "level": 2},
                    {"id": "integration", "kind": "integration",
                     "scope": {"kind": "all", "rules": ALL_RULES},
                     "depends_on": ["writing", "tokens"], "level": 3, "gate": {"kind": "full"}},
                ]),
        plan_step("p1", "run", 409, detail=NOT_APPROVED),
        *approve_and_run(),
        planned("p1", "passed", target_branch="build/notes", created_branch=True,
                target_tip=PLAN_TIP, error=None,
                baseline={"sha": SEED_HEAD, "failing": [W_TARGET, R_TARGET]},
                nodes=[
                    {"id": "scaffold", "status": "passed", "nothing_to_build": False,
                     "merged": True, "branch": "rebuild/p1-scaffold",
                     "files_changed": ["scaffold.txt"], "rounds": 1},
                    {"id": "writing", "status": "passed", "merged": True,
                     "branch": "rebuild/p1-writing", "files_changed": ["writing.txt"]},
                    {"id": "reading", "status": "passed", "merged": True,
                     "branch": "rebuild/p1-reading", "files_changed": ["reading.txt"]},
                    {"id": "tokens", "status": "passed", "nothing_to_build": True,
                     "merged": False, "build_id": None},
                    {"id": "integration", "status": "passed", "nothing_to_build": True},
                ],
                level_runs=[
                    {"level": 0, "status": "passed", "base": SEED_HEAD, "merged": ["scaffold"],
                     "gate": {"failing": [W_TARGET, R_TARGET], "regressions": [],
                              "mode": "regression", "selected": 2}},
                    {"level": 1, "status": "passed", "merged": ["writing", "reading"],
                     "tip": PLAN_TIP, "gate": {"failing": [], "regressions": [],
                                               "newly_fixed": [W_TARGET, R_TARGET],
                                               "mode": "regression", "selected": 4}},
                    {"level": 2, "status": "passed", "merged": [], "gate": None},
                    {"level": 3, "status": "passed", "merged": [], "gate": None},
                ],
                final_gate={"sha": PLAN_TIP, "failing": []},
                gate_config={"scoped_gate_args": SCOPED_ARGS, "level_gate": "regression",
                             "level_repair_attempts": 2},
                planner_usage={"calls": 1, "tokens_in": 0, "tokens_out": 0},
                totals={"rounds": 3, "lines_changed": 3,
                        "shape": {"nodes": 5, "levels": 4, "max_level_width": 2}}),
        # the writing node ran its gate scoped: its scope plus the scenarios passing at
        # the start of its level (the regression set); the gate's flags come last
        Step("GET", "/api/rebuild/b2", expect_body={
            "id": "b2", "status": "passed", "plan": {"id": "p1", "node": "writing"},
            "scoped_gate": {"args": SCOPED_ARGS, "in_scope": [W_TARGET, T_TARGET],
                            "regression_set": [T_TARGET, N_TARGET], "scenarios": 3}}),
        Step("GET", "/api/branches", expect_body={"current_branch": "main", "branches": [
            {"name": "build/notes", "head": PLAN_TIP, "current": False, "is_integration": True},
            {"name": "main", "head": SEED_HEAD, "current": True},
            {"name": "rebuild/p1-reading"}, {"name": "rebuild/p1-scaffold"},
            {"name": "rebuild/p1-writing"},
        ]}),
        areas((0, "built"), (0, "built"), (0, "built")),
        areas((0, "built"), (0, "built"), (0, "built"), params={"branch": "build/notes"}),
        UNCHANGED,
        Step("GET", "/p/notes/api/plans", expect_body=[{"id": "p1", "status": "passed", "nodes": [
            {"id": "scaffold", "status": "passed"}, {"id": "writing", "status": "passed"},
            {"id": "reading", "status": "passed"}, {"id": "tokens", "status": "passed"},
            {"id": "integration", "status": "passed"}]}]),
    )),
    Scenario("overlapping_siblings_are_serialised", steps=(
        post_plan({"scope": {"areas": ["writing-notes", "reading-notes"]}}),
        planned("p1", "ready", source="agent",
                levels=[["writing"], ["reading"], ["integration"]],
                nodes=[
                    {"id": "writing", "depends_on": [], "level": 0, "touches": NOTES_FILE},
                    {"id": "reading", "depends_on": ["writing"], "level": 1,
                     "serialised_because": {"node": "writing", "overlap": NOTES_FILE}},
                    {"id": "integration", "depends_on": ["writing", "reading"], "level": 2},
                ],
                edges=[
                    {"from": "writing", "to": "reading", "kind": "serialised", "auto": True},
                    {"from": "writing", "to": "integration", "kind": "declared", "auto": False},
                    {"from": "reading", "to": "integration", "kind": "declared", "auto": False},
                ],
                validation={"ok": True, "issues": [], "auto_edits": [
                    {"kind": "serialise", "from": "writing", "to": "reading",
                     "overlap": NOTES_FILE}]}),
        plan_step("p1", "approve", id="p1", status="approved", version=1),
        # the owner removes the edge: the overlap puts it back; the tier edit sticks
        Step("POST", "/api/plan/p1/revise", json={"edits": {
            "remove_edges": [["writing", "reading"]], "nodes": {"reading": {"tier": "frontier"}}}},
            expect_body={"id": "p1", "status": "ready", "version": 2, "approved": None,
                         "levels": [["writing"], ["reading"], ["integration"]],
                         "nodes": [{"id": "writing"},
                                   {"id": "reading", "tier": "frontier", "model": "opus",
                                    "depends_on": ["writing"],
                                    "serialised_because": {"node": "writing"}},
                                   {"id": "integration"}]}),
        plan_step("p1", "run", 409, detail=NOT_APPROVED),
        post_plan({"scope": "all", "max_parallel": 1}, pid="p2"),
        planned("p2", "ready", max_parallel=1,
                levels=[["scaffold"], ["writing"], ["reading"], ["tokens"], ["integration"]],
                validation={"ok": True, "issues": [], "auto_edits": [
                    {"kind": "level", "node": "reading", "from": 1, "to": 2}]}),
        UNCHANGED,
    )),
    Scenario("an_invalid_plan_falls_back", steps=(
        post_plan({"scope": {"rules": ["R1", "R3"]}}),
        planned("p1", "ready", source="fallback",
                fallback_reason="the planner's plan was invalid twice: R1 is covered by 2 "
                "nodes (a, b); R3 is covered by no node",
                planner={"kind": "stub", "attempts": 2},
                scope={"kind": "rules", "rules": ["R1", "R3"]},
                levels=[["writing-notes"], ["reading-notes"], ["integration"]],
                nodes=[
                    {"id": "writing-notes", "kind": "area",
                     "scope": {"kind": "rules", "rules": ["R1"]}, "depends_on": []},
                    {"id": "reading-notes", "kind": "area",
                     "scope": {"kind": "rules", "rules": ["R3"]},
                     "depends_on": ["writing-notes"]},
                    {"id": "integration", "kind": "integration",
                     "depends_on": ["reading-notes"]},
                ]),
        post_plan({"scope": {"areas": ["writing-notes"]}, "planner": "fallback"}, pid="p2"),
        planned("p2", "ready", source="fallback",
                fallback_reason="requested: the request asked for the fallback planner",
                levels=[["writing-notes"], ["integration"]]),
        plan_step("p1", "run", 409, detail=NOT_APPROVED),
    )),
    Scenario("a_level_gate_failure_is_repaired_and_lands", steps=(
        post_plan({"scope": {"areas": ["reading-notes", "writing-notes"]}}),
        planned("p1", "ready", levels=[["w-clash", "r-clash"], ["integration"]]),
        *approve_and_run(),
        # the merged level fails its gate: no_token passed before the level (the clash of
        # left.txt and right.txt, a regression) and create_a_note passed in w-clash's scoped
        # gate (--layers CD) but fails with the full layers (race.txt is racy). Each node
        # repairs what its scope owns, the level merges again and lands.
        planned("p1", "passed", target_tip=REPAIR_TIP, repair_attempts=1, error=None,
                nodes=[{"id": "w-clash", "status": "passed", "merged": True, "repairs": 1,
                        "rounds": 2},
                       {"id": "r-clash", "status": "passed", "merged": True, "repairs": 1,
                        "rounds": 2},
                       {"id": "integration", "status": "passed", "nothing_to_build": True}],
                level_runs=[{"level": 0, "status": "passed", "merged": ["w-clash", "r-clash"],
                             "tip": REPAIR_TIP, "landed": True, "regressions": [],
                             "gate": {"failing": [], "regressions": [], "mode": "regression"},
                             "repairs": [{"attempt": 1, "of": 2, "tip": CLASH_FAILED_TIP,
                                          "targets": [W_TARGET, N_TARGET],
                                          "attributed": {
                                              W_TARGET: {"by": "scope", "nodes": ["w-clash"]},
                                              N_TARGET: {"by": "scope", "nodes": ["r-clash"]}},
                                          "nodes": [{"id": "w-clash", "verify": "pass"},
                                                    {"id": "r-clash", "verify": "pass"}],
                                          "outcome": "passed"}]},
                            {"level": 1, "status": "passed", "merged": [], "gate": None}],
                final_gate={"sha": REPAIR_TIP, "failing": []},
                resume={"available": False, "kind": None}),
        # each node passed its own gate in round 1 and its repair round (2), on its branch
        Step("GET", "/api/rebuild/b1", expect_body={
            "id": "b1", "status": "passed", "plan": {"id": "p1", "node": "w-clash"},
            "repairs": 1, "rounds": [{"n": 1, "check": {"verdict": "pass"}},
                                     {"n": 2, "check": {"verdict": "pass"},
                                      "agent_summary": "w-clash repair: race.txt is calm"}]}),
        Step("GET", "/api/rebuild/b2", expect_body={
            "id": "b2", "status": "passed", "plan": {"id": "p1", "node": "r-clash"},
            "repairs": 1, "scoped_gate": {"in_scope": [R_TARGET, N_TARGET],
                                          "regression_set": [T_TARGET, N_TARGET],
                                          "scenarios": 3}}),
        Step("GET", "/api/branches", expect_body={"branches": [
            {"name": "build/notes", "head": REPAIR_TIP}, {"name": "main", "head": SEED_HEAD},
            {"name": "rebuild/p1-r-clash"}, {"name": "rebuild/p1-w-clash"}]}),
        areas((0, "built"), (0, "built"), (0, "built")),
        UNCHANGED,
    )),
    Scenario("a_failed_node_blocks_its_dependents_not_its_siblings", steps=(
        post_plan({"scope": {"rules": ["R1", "R3", "R4"]}}),
        planned("p1", "ready", levels=[["w-broken", "r-ok"], ["t-dep"], ["integration"]]),
        *approve_and_run(),
        planned("p1", "partial",
                nodes=[{"id": "w-broken", "status": "gave_up", "rounds": 3, "merged": False},
                       {"id": "r-ok", "status": "passed", "merged": True},
                       {"id": "t-dep", "status": "blocked", "blocked_by": ["w-broken"],
                        "build_id": None},
                       {"id": "integration", "status": "blocked", "blocked_by": ["t-dep"],
                        "build_id": None}],
                level_runs=[{"level": 0, "status": "passed", "merged": ["r-ok"],
                             "gate": {"failing": [W_TARGET], "regressions": []}}]),
        Step("GET", "/api/rebuild/b1", expect_body={
            "id": "b1", "status": "gave_up", "plan": {"id": "p1", "node": "w-broken"}}),
        areas((1, "partial"), (0, "built"), (1, "partial")),
        UNCHANGED,
    )),
    Scenario("a_running_plan_can_be_stopped", steps=(
        post_plan({"scope": {"rules": ["R3"]}}),
        planned("p1", "ready", levels=[["slow"], ["integration"]]),
        *approve_and_run(),
        refused("POST", "/api/rebuild", 409, "a build plan is running in this project (p1)",
                json={"scope": "all"}),
        Step("DELETE", "/api/plan/p1", expect_body={"id": "p1", "status": "stopped"}),
        Step("GET", "/api/plan/p1", expect_body={"id": "p1", "status": "stopped"}),
        refused("DELETE", "/api/plan/p1", 409, "cannot stop a plan that is stopped"),
        refused("POST", "/api/plan/p1/approve", 409, "plan p1 is stopped, not ready", json={}),
        UNCHANGED,
    )),
    Scenario("plan_refusals", steps=(
        refused("GET", "/api/plan/p9", 404, "no plan p9"),
        refused("GET", "/api/plan/p1", 422, "wait must be a number of seconds, 0 to 60",
                params={"wait": "soon"}),
        refused("POST", "/api/plan", 422,
                'scope must be "all", {"areas": ["<id>", ...]} or {"rules": ["<id>", ...]}',
                json={"scope": "writing-notes"}),
        refused("POST", "/api/plan", 404, "no area nope", json={"scope": {"areas": ["nope"]}}),
        refused("POST", "/api/plan", 404, "no rule R9", json={"scope": {"rules": ["R1", "R9"]}}),
        refused("POST", "/api/plan", 422, "max_parallel must be 1 to 8",
                json={"scope": "all", "max_parallel": 9}),
        coded("POST", "/api/plan", 409, "branch_checked_out",
              json={"scope": "all", "branch": {"name": "main"}}),
        coded("POST", "/api/plan", 404, "no_branch",
              json={"scope": "all", "branch": {"name": "nope"}}),
        refused("DELETE", "/api/plan/p9", 404, "no plan p9"),
        refused("POST", "/api/plan/p9/approve", 404, "no plan p9", json={}),
        Step("GET", "/api/plans", expect_body=[]),
        Step("GET", "/api/branches", expect_body={"branches": [{"name": "main"}]}),
        UNCHANGED,
    )),
    # ---- durable state and resume (API.md v1.10); restarts themselves are Unchecked (R63)
    Scenario("a_plan_is_listed_with_its_resume_state", steps=(
        post_plan({"scope": {"rules": ["R3"]}}),
        planned("p1", "ready", interrupted=False, interrupted_at=None, attempts=0,
                resume={"available": False, "kind": None, "from_level": None, "will_skip": [],
                        "will_run": ["slow", "integration"],
                        "note": "The plan has not run yet: approve it, then run it."}),
        Step("GET", "/api/plans", expect_body=[
            {"id": "p1", "status": "ready", "interrupted": False, "attempts": 0,
             "resume": {"available": False, "kind": None},
             "nodes": [{"id": "slow", "status": "pending"},
                       {"id": "integration", "status": "pending"}]}]),
        post_plan({"scope": {"areas": ["writing-notes"]}, "planner": "fallback"}, pid="p2"),
        planned("p2", "ready"),
        Step("GET", "/api/plans", expect_body=[{"id": "p1", "status": "ready"},
                                               {"id": "p2", "status": "ready"}]),
        Step("GET", "/api/plan/p1", expect_body={"id": "p1", "status": "ready", "version": 1}),
    )),
    Scenario("land_is_409_when_nothing_waits", steps=(
        post_plan({"scope": "all"}),
        planned("p1", "ready"),
        coded("POST", "/api/plan/p1/land", 409, "nothing_to_land", json={}),
        refused("POST", "/api/plan/p1/land", 409, "nothing is waiting to land in plan p1",
                json={}),
        *approve_and_run(),
        planned("p1", "passed", target_tip=PLAN_TIP,
                resume={"available": False, "kind": None,
                        "note": "Nothing to resume: every node passed and landed."}),
        refused("POST", "/api/plan/p1/land", 409, "nothing is waiting to land in plan p1",
                json={}),
        refused("POST", "/api/plan/p9/land", 404, "no plan p9", json={}),
        refused("POST", "/api/plan/p1/run", 409, "plan p1 is passed, not approved", json={}),
        UNCHANGED,
    )),
    Scenario("forget_drops_a_plan_and_its_anchors", steps=(
        post_plan({"scope": "all"}),
        planned("p1", "ready"),
        *approve_and_run(),
        planned("p1", "passed", target_tip=PLAN_TIP, anchors={"L1": PLAN_TIP}),
        refused("DELETE", "/api/plan/p1", 409, "cannot stop a plan that is passed"),
        refused("DELETE", "/api/plan/p1", 422, "forget must be true or false",
                params={"forget": "maybe"}),
        Step("DELETE", "/api/plan/p1", params={"forget": "true"}, expect_body={
            "id": "p1", "status": "forgotten", "refs_removed": [
                "refs/lattice/plans/p1/L0", "refs/lattice/plans/p1/L1",
                "refs/lattice/plans/p1/reading", "refs/lattice/plans/p1/scaffold",
                "refs/lattice/plans/p1/writing"]}),
        refused("GET", "/api/plan/p1", 404, "no plan p1"),
        Step("GET", "/api/plans", expect_body=[]),
        refused("DELETE", "/api/plan/p1", 404, "no plan p1", params={"forget": "true"}),
        post_plan({"scope": {"areas": ["writing-notes"]}, "planner": "fallback"}, pid="p2"),
        # the node branches stay, and the target keeps the landed work
        Step("GET", "/api/branches", expect_body={"branches": [
            {"name": "build/notes", "head": PLAN_TIP}, {"name": "main", "head": SEED_HEAD},
            {"name": "rebuild/p1-reading"}, {"name": "rebuild/p1-scaffold"},
            {"name": "rebuild/p1-writing"}]}),
        UNCHANGED,
    )),
    Scenario("a_partial_plan_retries_up_to_the_cap", steps=(
        post_plan({"scope": {"rules": ["R1", "R3", "R4"]}}),
        planned("p1", "ready", levels=[["w-broken", "r-ok"], ["t-dep"], ["integration"]]),
        *approve_and_run(),
        planned("p1", "partial", attempts=1,
                nodes=[{"id": "w-broken", "status": "gave_up", "attempts": 1, "builds": ["b1"]},
                       {"id": "r-ok", "status": "passed", "attempts": 1, "builds": ["b2"]},
                       {"id": "t-dep", "status": "blocked", "attempts": 0},
                       {"id": "integration", "status": "blocked", "attempts": 0}],
                resume={"available": True, "kind": "retry", "from_level": 0,
                        "will_skip": ["r-ok"], "will_run": ["w-broken", "t-dep", "integration"],
                        "note": RETRY_NOTE}),
        plan_step("p1", "run", id="p1", status="running"),
        planned("p1", "partial", attempts=2,
                nodes=[{"id": "w-broken", "status": "gave_up", "attempts": 2, "rounds": 6,
                        "builds": ["b1", "b3"]},
                       {"id": "r-ok", "status": "passed", "attempts": 1, "builds": ["b2"]},
                       {"id": "t-dep", "status": "blocked"},
                       {"id": "integration", "status": "blocked"}]),
        plan_step("p1", "run", id="p1", status="running"),
        planned("p1", "partial", attempts=3,
                nodes=[{"id": "w-broken", "status": "gave_up", "attempts": 3},
                       {"id": "r-ok", "attempts": 1, "builds": ["b2"]},
                       {"id": "t-dep"}, {"id": "integration"}],
                resume={"available": False, "kind": "retry", "note": CAPPED_NOTE}),
        refused("POST", "/api/plan/p1/run", 409, CAPPED_NOTE, json={}),
        UNCHANGED,
    )),
    Scenario("a_stopped_plan_offers_to_continue", steps=(
        post_plan({"scope": {"rules": ["R3"]}}),
        planned("p1", "ready", levels=[["slow"], ["integration"]]),
        *approve_and_run(),
        Step("DELETE", "/api/plan/p1", expect_body={"id": "p1", "status": "stopped"}),
        Step("GET", "/api/plan/p1", expect_body={"id": "p1", "status": "stopped", "resume": {
            "available": True, "kind": "continue", "from_level": 0, "will_skip": [],
            "will_run": ["slow", "integration"],
            "note": "The plan was stopped at level 0. Run continues from there; no node had "
                    "passed yet."}}),
        UNCHANGED,
    )),
    # ---- spec sync (API.md v1.12): the seed's [rebuild] sync_spec is "auto"
    Scenario("the_committed_spec_is_synced_onto_the_target_branch", steps=(
        build({"area": "writing-notes"}),
        rebuilt("b1", "passed", target_branch="build/notes", commit={"sha": WRITING_COMMIT}),
        *ACCEPT_STARRED,
        # main's spec moved on; the build branches keep the seed's
        Step("GET", "/api/branches", expect_body={"current_branch": "main", "branches": [
            {"name": "build/notes", "head": WRITING_COMMIT, "spec": BEHIND},
            {"name": "main", "head": STARRED_COMMIT, "spec": None},
            {"name": "rebuild/writing-notes-1", "head": WRITING_COMMIT, "spec": BEHIND},
        ]}),
        post_plan({"scope": {"areas": ["writing-notes"]}, "planner": "fallback"}),
        planned("p1", "ready", target_branch="build/notes", spec_drift=SYNC_DRIFT,
                warnings=[drift_warning("build/notes")]),
        sync("build/notes", branch="build/notes", synced_from=STARRED_COMMIT, files=SYNC_FILES,
             commit=SYNC_COMMIT, previous_tip=WRITING_COMMIT),
        Step("GET", "/api/branches", expect_body={"current_branch": "main", "branches": [
            {"name": "build/notes", "head": SYNC_COMMIT, "spec": IN_SYNC},
            {"name": "main", "head": STARRED_COMMIT, "spec": None},
            {"name": "rebuild/writing-notes-1", "head": WRITING_COMMIT, "spec": BEHIND},
        ]}),
        planned("p1", "ready", spec_drift=None, warnings=[]),
        coded("POST", "/api/branches/build/notes/sync-spec", 409, "nothing_to_sync", json={}),
        refused("POST", "/api/branches/build%2Fnotes/sync-spec", 409,
                NOTHING_TO_SYNC.format("build/notes"), json={}),
        # the owner's branch and working copy never move
        model(markdown=STARRED_MD, project={"name": "notes", "git_head": STARRED_COMMIT[:7]}),
    )),
    Scenario("spec_sync_refusals", steps=(
        coded("POST", "/api/branches/main/sync-spec", 409, "branch_checked_out", json={}),
        coded("POST", "/api/branches/release/sync-spec", 409, "branch_protected", json={}),
        coded("POST", "/api/branches/nope/sync-spec", 404, "no_branch", json={}),
        refused("POST", "/api/branches/nope/sync-spec", 404, "no branch nope", json={}),
        coded("POST", "/api/branches/bad..name/sync-spec", 422, "branch_name_invalid", json={}),
        refused("POST", "/api/branches/main/sync-spec", 422, "unknown field(s): force",
                json={"force": True}),
        Step("GET", "/api/branches", expect_body={"branches": [
            {"name": "main", "head": SEED_HEAD, "spec": None}]}),
        UNCHANGED,
    )),
    Scenario("spec_sync_is_refused_while_a_plan_runs", steps=(
        build({"area": "writing-notes"}),
        rebuilt("b1", "passed", commit={"sha": WRITING_COMMIT}),
        post_plan({"scope": {"rules": ["R3"]}}),
        planned("p1", "ready", levels=[["slow"], ["integration"]], spec_drift=None),
        *approve_and_run(),
        coded("POST", "/api/branches/build/notes/sync-spec", 409, "build_running", json={}),
        Step("DELETE", "/api/plan/p1", expect_body={"id": "p1", "status": "stopped"}),
        coded("POST", "/api/branches/build/notes/sync-spec", 409, "nothing_to_sync", json={}),
        Step("GET", "/api/branches", expect_body={"branches": [
            {"name": "build/notes", "head": WRITING_COMMIT, "spec": {"in_sync": True}},
            {"name": "main"}, {"name": "rebuild/writing-notes-1"}]}),
        UNCHANGED,
    )),
    Scenario("a_plan_run_syncs_the_spec_first_when_configured", steps=(
        build({"area": "writing-notes"}, branch={"name": "feature/w", "create": True}),
        rebuilt("b1", "passed", target_branch="feature/w", commit={"sha": WRITING_COMMIT}),
        *ACCEPT_STARRED,
        post_plan({"scope": {"areas": ["writing-notes"]}, "planner": "fallback",
                   "branch": {"name": "feature/w"}}),
        planned("p1", "ready", target_branch="feature/w", spec_drift=SYNC_DRIFT,
                warnings=[drift_warning("feature/w")]),
        *approve_and_run(),
        # the start baseline ran on the sync commit: the spec was synced before it
        planned("p1", "passed", target_branch="feature/w", baseline={"sha": SYNC_COMMIT},
                spec_drift=None, warnings=[]),
        Step("GET", "/api/branches", expect_body={"branches": [
            {"name": "feature/w", "spec": IN_SYNC}, {"name": "main", "head": STARRED_COMMIT},
            {"name": "rebuild/writing-notes-1", "spec": BEHIND}]}),
        model(markdown=STARRED_MD, project={"name": "notes", "git_head": STARRED_COMMIT[:7]}),
    )),
    Scenario("connected_tools_with_the_stub_llm", steps=(
        Step("GET", "/api/mcp", expect_body=MCP_STUB),
        Step("GET", "/api/mcp", params={"refresh": "1"}, expect_body=MCP_STUB),
        Step("GET", "/api/mcp", params={"refresh": "true"}, expect_body=MCP_STUB),
        refused("GET", "/api/mcp", 422, "refresh must be 1 or 0", params={"refresh": "maybe"}),
        refused("POST", "/api/mcp", 404, "no such route", json={}),
        refused("POST", "/api/mcp", 404, "no such route",
                json={"server": "claude.ai Gmail", "tool": "send_message"}),
        UNCHANGED,
    )),
    Scenario("a_change_records_its_source", steps=(
        Step("POST", "/api/changes", json={"request": STARRED, "source": SOURCE},
             expect_body={"id": "c1"}),
        wait("c1", "ready", source=SOURCE, request=STARRED),
        model(changes=[{"id": "c1", "status": "ready", "source": SOURCE}]),
        # a follow-up that gives no source keeps its parent's
        post_change(FIRST, parent="c1", cid="c2"),
        wait("c2", "ready", source=SOURCE, parent="c1"),
        # a change without a source has `source` null, and the model lists none for it
        Step("POST", "/api/changes", json={"request": "Notes can be pinned.",
                                           "source": None}, expect_body={"id": "c3"}),
        wait("c3", "ready", source=None),
        model(changes=[{"id": "c1", "status": "rejected", "source": SOURCE},
                       {"id": "c2", "status": "ready", "source": SOURCE},
                       {"id": "c3", "status": "ready"}]),
        # whitespace is trimmed; empty optional fields are dropped
        Step("POST", "/api/changes", json={"request": "Notes can be shared.", "source": {
             "server": " claude.ai Linear ", "item": "PROJ-9", "url": "", "title": ""}},
             expect_body={"id": "c4"}),
        wait("c4", "ready", source={"server": "claude.ai Linear", "item": "PROJ-9"}),
        UNCHANGED,
    )),
    Scenario("an_invalid_source_is_refused", steps=(
        refused("POST", "/api/changes", 422,
                "source must be an object {server, item, url?, title?}",
                json={"request": STARRED, "source": "PROJ-7"}),
        refused("POST", "/api/changes", 422, "source.server is required",
                json={"request": STARRED, "source": {"item": "PROJ-7"}}),
        refused("POST", "/api/changes", 422, "source.item is required",
                json={"request": STARRED, "source": {"server": "claude.ai Linear", "item": "  "}}),
        refused("POST", "/api/changes", 422, "source.item must be a string",
                json={"request": STARRED, "source": {"server": "claude.ai Linear", "item": 7}}),
        refused("POST", "/api/changes", 422, "source.item is longer than 200 characters",
                json={"request": STARRED, "source": {"server": "t", "item": "x" * 201}}),
        refused("POST", "/api/changes", 422, "source.url must start with http:// or https://",
                json={"request": STARRED, "source": {"server": "t", "item": "PROJ-7",
                                                     "url": "javascript:alert(1)"}}),
        refused("POST", "/api/changes", 422, "source.title holds control characters",
                json={"request": STARRED, "source": {"server": "t", "item": "PROJ-7",
                                                     "title": "a\nb"}}),
        refused("POST", "/api/changes", 422, "source: unknown field(s): token",
                json={"request": STARRED, "source": {"server": "t", "item": "PROJ-7",
                                                     "token": "secret"}}),
        model(changes=[]),
        refused("GET", "/api/changes/c1", 404, "no change c1"),
        UNCHANGED,
    )),
    Scenario("static_files_stay_in_the_web_directory", steps=(
        Step("GET", "/"),
        Step("GET", "/index.html"),
        refused("GET", "/..%2fserver.py", 404, "not found"),
        refused("GET", "/%2e%2e/server.py", 404, "not found"),
        refused("GET", "/..%2f..%2f..%2fpyproject.toml", 404, "not found"),
        refused("GET", "/no-such-file.js", 404, "not found"),
    )),
)

UNCHECKED = (
    Unchecked("R1: the server's purpose and setting (one project directory, 127.0.0.1, no "
              "login) is context; binding to 127.0.0.1 only is not observable over HTTP from "
              "the same host."),
    Unchecked("R3 is checked only for the paths the scenarios walk (ready, rejected, "
              "cancelled, failed changes leave markdown and git_head alone); a write to the "
              "files by a non-accept path the scenarios never take would go unseen."),
    Unchecked("R4: the `checking` status in between is not observed: with the stub LLM the "
              "pipeline is too fast to catch it, and a scenario has no conditional poll."),
    Unchecked("R7: 'exactly one commit of those files' is checked through a reproducible "
              "commit id (fixed git identity and dates in the harness): the id pins the tree, "
              "the parent and the message. The spec cannot read the repository itself; the "
              "id was recorded from the real server once and verified by hand.",
              ),
    Unchecked("R16: the window before a follow-up is ready is not covered: while the first "
              "follow-up is drafting its parent is still ready, and the server accepts a "
              "second follow-up on it (see the results doc)."),
    Unchecked("R17: one commit for a follow-up's accept is checked by the written markdown and "
              "the message, not by a commit id."),
    Unchecked("R20: checked on the error answers the scenarios provoke, not on every route."),
    Unchecked("R21/R22: what an answer says comes from the LLM; the stub always answers "
              "not_specified, so only the route and the answer's shape are checked.",
              ),
    Unchecked("R24, R25, R26, R27: the full check (POST /api/check), the Claude Code chat "
              "sessions, the change impact against a configured app, and accept outside git or "
              "on a git failure need an app, the claude binary or a broken repository; they "
              "stay with tests/studio/test_server.py and test_session.py."),
    Unchecked("R28: `capabilities.session` depends on whether the claude binary is on the "
              "host's PATH, so the scenarios state only `capabilities.rebuild`; a project's "
              "`last_check` after a check needs an app (R24)."),
    Unchecked("R29: only the routes the scenarios walk are checked under the prefix (model, "
              "changes, wait, accept, an unknown route); the session routes need the claude "
              "binary (R25)."),
    Unchecked("R31: the harness serves one project directory, so two projects' change sets "
              "cannot be compared here; tests/studio/test_projects.py checks the isolation."),
    Unchecked("R32-R39: the agent here is the TEST-ONLY stub (a scripted edit); the real "
              "Claude Code agent's argv, isolation (acceptEdits, Bash limited to the gate "
              "commands, the guard on the worktree, no API credentials), --resume and the "
              "findings-as-prompt are checked against a fake claude in "
              "tests/studio/test_rebuild.py, as are the gate's import path, the process-group "
              "kill on DELETE and usage-limit rounds."),
    Unchecked("R32/R39: the worktree's location and its removal on DELETE, and that the "
              "owner's working tree stays untouched, are seen here only through the record "
              "(`worktree: null`, `branch: null`) and `git_head`; the spec cannot read the "
              "repository. R35's commit is pinned by a reproducible id (REBUILT_COMMIT), "
              "recorded once and verified by hand."),
    Unchecked("R40: `capabilities.rebuild` is checked true for the seed (stub agent, "
              "./gate.sh); the false cases (no git, no gates, no claude) are in "
              "tests/studio/test_rebuild.py."),
    Unchecked("R41-R47 (build from spec) are checked with the stub agent and a two-file gate. "
              "In tests/studio/test_build_scope.py only: the owner's new commits merged into "
              "the default target (and a merge conflict), a target that moves during the build "
              "(branch_moved), a confirmed build onto the checked-out branch (the ff-only merge "
              "in a clean worktree) or a protected one, a branch checked out in another "
              "worktree, the metrics hook, findings_json, the token and cost fields, gates that "
              "cannot run at the baseline, unmapped findings and the scope `all`, and areas "
              "status `building`. WRITING_COMMIT and READING_COMMIT were recorded from the "
              "real server and checked by hand with `git log --name-status build/notes`."),
    Unchecked("R49-R57 (build plans) are checked with the TEST-ONLY stub planner and stub "
              "agent. The real planner (claude -p with read-only tools, its argv, isolation "
              "and the re-ask after an invalid answer) runs only against a fake claude in "
              "tests/studio/test_build_plan.py, as do: the concurrency of siblings (start and "
              "finish timestamps overlap), merge conflicts, resume, the metrics hook per node "
              "and per plan, revise with feedback, owner edits that reverse an edge, branch "
              "creation at run time, a node started after DELETE, and the fallback on Streak's "
              "real trace. Here the timing of a stop is not pinned: the plan is stopped, "
              "whether its node had started or not. PLAN_TIP was recorded from the real "
              "server and checked by hand with `git log --graph build/notes`."),
    Unchecked("R63 (durable state across a restart) is not expressible here: a scenario cannot "
              "kill the server and start it again on the same state directory, check out the "
              "target branch in the owner's worktree in the middle of a run, or read the "
              "repository. tests/studio/test_resume.py covers it with a real Studio server "
              "killed with SIGKILL mid-plan (the plan comes back interrupted, its orphan agent "
              "is terminated, the resume runs no passed node again), the land of a level that "
              "could not land (no agent) and the re-merge after the target moved, the orphan "
              "match (pid AND start time AND command line), the attempts cap and a build "
              "continued from its next round; tests/studio/test_state_store.py covers atomic "
              "writes, quarantine, id counters, round trips, git-ignore and two projects. "
              "R58-R62 are checked only on plans that never met a restart (`interrupted` is "
              "false, `kind` is never `land` here)."),
    Unchecked("R54 (a level that still fails after its repair attempts, or with the repair "
              "loop off) needs a stub that never repairs or a second configuration of the "
              "seed; tests/studio/test_level_repair.py covers the cap (never more attempts "
              "than `level_repair_attempts`), the `partial` end with `resume.kind` `repair`, "
              "the further repair pass on run, the dedicated repair node for a failure no "
              "node owns and the loop off; tests/studio/test_build_plan.py the v1.9 stop."),
    Unchecked("R65-R67 (build performance) are checked with the seed's gate.sh, which takes "
              "the flags of `lattice spec check` but is a shell script: the real check's "
              "`--await-cap` and `--only-file` are in tests/spec (test_services.py, "
              "test_selection_timings.py), and the selection file's content (the worktree is "
              "removed after a pass) through the record's `scoped_gate`. The attribution by "
              "files, the reuse of a gated tip's results as the next start baseline, the "
              "planner's `built` areas and the nodes dropped at planning are in "
              "tests/studio/test_scoped_gates.py and test_level_repair.py. CLASH_FAILED_TIP "
              "and REPAIR_TIP were recorded from the real server and checked by hand with "
              "`git log --graph --all`."),
    Unchecked("R69 (stop and budget hygiene) is not observable over HTTP: a scenario cannot "
              "see processes, containers or an agent's token stream. "
              "tests/studio/test_stop_cleanup.py covers it with a gate whose app ignores "
              "SIGTERM in its own session, a fake docker CLI (only the gate's labelled "
              "container is removed) and a fake claude (a timeout and node_max_tokens keep "
              "the usage seen)."),
    Unchecked("R70-R74 (spec sync) are checked with the seed's `sync_spec = \"auto\"` and "
              "a build branch left behind by an accepted change. Not observable here, and "
              "covered by tests/studio/test_spec_sync.py: that uncommitted edits of the owner's "
              "working copy are neither compared nor synced (a scenario cannot edit the working "
              "copy), `[mcp] protected` files (the seed has none), a branch checked out in a "
              "linked worktree (here only the owner's own checkout), the compare-and-swap "
              "refusal when the branch moves during a sync, the removal of the sync's "
              "temporary worktree, the default `off`, and an auto sync refused as the run's "
              "409. SYNC_COMMIT pins the commit's parent, tree and message; it was recorded "
              "from the real server and checked by hand with `git log --name-status`."),
    Unchecked("R75 (the Sync spec banner of the Build view and the plan view) is web UI, not "
              "observable over HTTP; tests/studio/test_spec_sync.py checks its code and the "
              "mock's fields, and the browser check is in "
              "docs/results/2026-10-11-spec-sync-results.md."),
    Unchecked("R76 (connected MCP servers): only the stub answer is observable here (the seed "
              "runs the stub LLM; that no process starts is seen through the answer, refreshed_at "
              "null). "
              "The discovery from a real init record, the cache, `refresh`, the per-server counts "
              "and `[mcp] allow_writes` are in tests/studio/test_mcp_intake.py against a fake "
              "claude."),
    Unchecked("R80 (what a chat session sees and may run) needs the claude binary and the owner's "
              "account: tests/studio/test_mcp_intake.py checks the chat argv per mode (no "
              "--strict-mcp-config, --setting-sources \"\"), the allowlist (read tools of "
              "connected servers only, unknown treated as write), allow_writes, and the hermetic "
              "rebuild and planner argv; the real-session probes (prompt injection K-3, end to "
              "end K-4) are in docs/results/2026-10-12-spike4-results.md."),
    Unchecked("R81 (the Connected tools list and the source on a change card) is web UI, not "
              "observable over HTTP; tests/studio/test_mcp_intake.py checks its code and the "
              "mock's fields, and the browser check is in "
              "docs/results/2026-10-12-spike4-results.md."),
    Unchecked("Concurrency: requests racing the drafting thread (e.g. accept vs. a follow-up "
              "finishing) are not raced; Studio keeps no database, so layer X has no table "
              "to race on."),
    Unchecked("LLM content: the markdown edit, the drafted spec and the questions a real model "
              "writes are not checked; the stub writes them."),
)

# No tables and no callers: Studio keeps no database and has no login. Errors are
# FastAPI-style {"detail": ...} bodies.
PROFILE = Profile(
    schemas={},
    api=ApiProfile(tables={}, table_names={}, roles=frozenset({"Public"}),
                   roles_from=NoRoles("Public"), error_body="fastapi"),
)  # fmt: skip
# fmt: on
SPEC = Spec(profile=PROFILE, scenarios=SCENARIOS, unchecked=UNCHECKED)
