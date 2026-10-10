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
  keeps a change drafting for 2 seconds, long enough to cancel or reject it.

Studio has no database and no login: there are no tables, invariants, machines or users,
and layers B, C and R have nothing to bind to (the harness's marker database is never
read). Self-contained on purpose: Studio loads this module in isolation.
"""

from lattice.profile import ApiProfile, NoRoles, Profile
from lattice.spec import Scenario, Spec, Step, Unchecked

# ---- the seed project (dogfood/studio/project_seed; harness.py checks they agree) ---------

SEED_MD = """\
# Notes app

- A signed-in user creates a note with POST /notes {"text": ...}; it answers 201 with the note.
- A note's text is 1 to 200 characters; anything else answers 422.
- GET /notes/{id} answers the caller's own note; another user's note answers 404.
- Requests without a token answer 401.
"""
SEED_HEAD = "6e48fed01710059caef55ee168e31c09c0e230e7"
STARRED = "Notes can be starred."
STARRED_MD = SEED_MD + f"- {STARRED}\n"
STARRED_COMMIT = "85623dd6a516b45acd8aab413c3936e4c3aca168"
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


READY_STARRED = wait(
    "c1", "ready", parent=None, error=None, superseded_by=None, checks={"valid": True},
    rules={"added": ["R5"], "changed": [], "removed": []},
)  # fmt: skip
UNCHANGED = model(markdown=SEED_MD, project={"name": "notes", "git_head": SEED_HEAD[:7]})

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
             "capabilities": {"rebuild": False}},
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
