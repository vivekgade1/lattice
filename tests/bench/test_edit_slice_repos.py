"""Round 3 of the edit slice: tickets on the held-out repos (TaskFlow, JustNotes).

* the harness's app-specific parts take a ``repo``: the variant loader builds edited
  copies of TaskFlow and JustNotes in-process beside the originals, as it does for the
  template;
* each reference implementation of t19-t24 scores **correct** against its oracle (every
  raise site of its routes reached), and never crashes;
* the unmodified app scores **not correct** on every ticket, noticed by the
  ticket-specific cases;
* trap-violating variants of the references score **silent** (at least three per ticket);
* arm P's dry run scores each reference correct; arm L's start state is the importer's
  output, not built here (``run.start_lattice`` raises for the held-out repos).
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from bench.edit_slice import oracle, prompts, run, smoke, tickets
from bench.edit_slice.apps import repo
from bench.edit_slice.reference import REFERENCE_ROOT, make_reference_app, reference_files
from bench.edit_slice.tickets import ALL_TICKETS, REPO_TICKETS, TICKETS, ticket
from bench.edit_slice.variant import load_variant
from lattice.importer.verify import verify
from lattice.llm.client import ScriptedGenerator


def _login_and_get(app, login_path, username, password, path):
    with TestClient(app) as client:
        r = client.post(login_path, data={"username": username, "password": password})
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        r = client.get(path, headers={"Authorization": f"Bearer {token}"})
        return r.status_code, r.json()


# ---- the variant loader on the held-out repos ------------------------------------------------


def test_an_empty_taskflow_overlay_behaves_like_taskflow(tmp_path):
    from bench.import_repos.taskflow import make_app
    from bench.import_repos.taskflow.seed import build_seed_db

    db = build_seed_db(tmp_path / "seed.db")
    answers = []
    for factory in (make_app, load_variant({}, repo="taskflow").make_app):
        app = factory(db)
        answers.append(_login_and_get(app, "/api/v1/auth/token", "alice@example.com",
                                      "alicepass123", "/api/v1/tasks"))  # fmt: skip
        app.state.engine.dispose()
    assert answers[0] == answers[1]
    assert answers[0][0] == 200 and answers[0][1]["total"] == 3


def test_an_empty_justnotes_overlay_behaves_like_justnotes(tmp_path):
    from bench.import_repos.justnotes import make_app
    from bench.import_repos.justnotes.seed import build_seed_db

    db = build_seed_db(tmp_path / "seed.db")
    answers = []
    for factory in (make_app, load_variant({}, repo="justnotes").make_app):
        app = factory(db)
        answers.append(_login_and_get(app, "/auth/login", "alice", "alicepass123", "/notes"))
        app.state.engine.dispose()
    assert answers[0] == answers[1]
    assert answers[0][0] == 200 and len(answers[0][1]) == 2


def test_a_held_out_variant_creates_its_tables_on_a_new_database(tmp_path):
    """A TaskFlow app creates its tables in its lifespan; a variant creates them when
    built, so the smoke run can copy the seed in before the first request."""
    app = load_variant({}, repo="taskflow").make_app(tmp_path / "new.db")
    app.state.engine.dispose()
    tables = {r[0] for r in sqlite3.connect(tmp_path / "new.db").execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}  # fmt: skip
    assert {"users", "tasks"} <= tables


def test_variants_of_different_repos_are_cached_apart():
    assert load_variant({}, repo="taskflow") is load_variant({}, repo="taskflow")
    assert load_variant({}, repo="taskflow") is not load_variant({}, repo="justnotes")
    assert load_variant({}) is not load_variant({}, repo="taskflow")


# ---- the tickets ------------------------------------------------------------------------------

REPO_IDS = [t.id for t in REPO_TICKETS]


def test_six_tickets_on_the_held_out_repos():
    assert REPO_IDS == [
        "t19_task_summary",
        "t20_bulk_status",
        "t21_completed_at",
        "t22_copy_note",
        "t23_note_filter",
        "t24_note_stats",
    ]
    assert [ticket(t).repo for t in REPO_IDS] == ["taskflow"] * 3 + ["justnotes"] * 3
    assert all(t.repo == "template" for t in TICKETS)  # t1-t12 unchanged
    assert [t.id for t in ALL_TICKETS] == [*[t.id for t in TICKETS], *REPO_IDS]
    for t in REPO_TICKETS:
        assert t.title and t.text and t.routes and t.files and t.base is None
        root = REFERENCE_ROOT / t.id
        own = {p.relative_to(root).as_posix() for p in root.rglob("*.py")
               if "__pycache__" not in p.parts}  # fmt: skip
        assert set(t.files) == own, t.id
        for rel in t.files:  # every reference file edits a file of the app
            assert (repo(t.repo).root / rel).is_file(), (t.id, rel)


def test_the_repo_tickets_traps_are_recorded_in_the_module_docstring():
    doc = tickets.__doc__ or ""
    for tid in REPO_IDS:
        assert f"``{tid}``" in doc, tid
    assert "t19, t20, t21, t22, t23, t24" in doc  # the order they were written in


@pytest.mark.parametrize(
    ("tid", "spelled_out"),
    [
        ("t19_task_summary", ["route order", "zero", "0", "null", "without a due", "only the"]),
        ("t20_bulk_status", ["atomic", "all or nothing", "nothing is changed", "updated_at",
                             "another user", "403", "rolled back"]),
        ("t21_completed_at", ["post", "new task", "re-stamp", "done to done", "ignored",
                              "list", "updated_at"]),
        ("t22_copy_note", ["403", "public note", "same title", "400", "404", "owner"]),
        ("t23_note_filter", ["other users", "owner", "wildcard", "%", "text or title",
                             "own notes only"]),
        ("t24_note_stats", ["route order", "once", "distinct", "public notes of", "0"]),
    ],
)  # fmt: skip
def test_the_repo_tickets_traps_are_not_spelled_out(tid, spelled_out):
    text = ticket(tid).text.lower()
    assert not [w for w in spelled_out if w in text]


# ---- the oracle: references correct, the unmodified app not ----------------------------------


@pytest.fixture(scope="module")
def reference_outcomes() -> dict[str, oracle.Outcome]:
    return {tid: oracle.evaluate(tid, lambda p, tid=tid: make_reference_app(tid, p))
            for tid in REPO_IDS}  # fmt: skip


@pytest.mark.parametrize("tid", REPO_IDS)
def test_each_reference_is_correct_against_its_own_oracle(tid, reference_outcomes):
    outcome = reference_outcomes[tid]
    assert outcome.kind == "correct", outcome.render()
    assert outcome.unverified == (), outcome.render()
    assert {c.verdict for c in outcome.cases.values()} == {"equal"}


# (generated cases, ticket-specific cases) per ticket
EXPECTED_COUNTS = {
    "t19_task_summary": (6, 9),
    "t20_bulk_status": (6, 12),
    "t21_completed_at": (85, 15),
    "t22_copy_note": (9, 10),
    "t23_note_filter": (7, 16),
    "t24_note_stats": (6, 8),
}


def test_case_counts(reference_outcomes):
    counts = {tid: o.counts() for tid, o in reference_outcomes.items()}
    for tid, (generated, specific) in counts.items():
        assert generated >= 6 and specific >= 8, (tid, generated, specific)
    assert counts == EXPECTED_COUNTS


@pytest.mark.parametrize("tid", REPO_IDS)
def test_references_never_crash(tid):
    """A reference 5xx would be 'equal' to a candidate crashing the same way."""
    o = oracle.ticket_oracle(tid)
    with o.frozen_clock():
        result = verify(o.reference_factory, o.reference_factory, o.db_factory, o.cases,
                        oracle.RULES, {}, login=o.login,
                        hash_columns=o.hash_columns)  # fmt: skip
    crashed = sorted(n for n, rec in result.original.items() if rec.status >= 500)
    assert crashed == []
    assert all(o_.kind == "equal" for o_ in result.outcomes.values())  # deterministic


def _original_app(repo_name: str):
    if repo_name == "taskflow":
        from bench.import_repos.taskflow import make_app
    else:
        from bench.import_repos.justnotes import make_app
    return make_app


@pytest.mark.parametrize("tid", REPO_IDS)
def test_the_unmodified_app_is_not_correct(tid):
    outcome = oracle.evaluate(tid, _original_app(ticket(tid).repo))
    assert outcome.kind != "correct", outcome.render()
    assert any(c.verdict in ("silent", "loud") for c in outcome.specific().values())


# ---- trap-violating variants ------------------------------------------------------------------


def _patched(tid: str, path: str, old: str, new: str, count: int = 1,
             then: tuple[str, str] | None = None) -> dict[str, str]:  # fmt: skip
    """The reference of ``tid`` with ``old`` replaced by ``new`` in ``path`` (and the
    ``then`` pair replaced after it)."""
    files = dict(reference_files(tid))
    for a, b in [(old, new), *([then] if then else [])]:
        assert files[path].count(a) == count, (tid, path, a)
        files[path] = files[path].replace(a, b)
    return files


def _moved_before(tid: str, path: str, block_start: str, block_end: str,
                  anchor: str) -> dict[str, str]:  # fmt: skip
    """``path`` with the block [block_start, block_end) moved to just before ``anchor``
    (declaring a literal route after the parameterised one that swallows it)."""
    files = dict(reference_files(tid))
    src = files[path]
    start, end = src.index(block_start), src.index(block_end)
    block = src[start:end]
    src = src[:start] + src[end:]
    at = src.index(anchor)
    files[path] = src[:at] + block + src[at:]
    return files


TF_ROUTES, NOTES_API = "routes.py", "api/notes.py"

TRAPS = {
    # ---- t19: the summary
    "summary-shadowed-by-task-route": ("t19_task_summary", lambda: _moved_before(
        "t19_task_summary", TF_ROUTES, '@router.get("/summary")',
        '@router.get("/{task_id}"', '@router.patch("/{task_id}"')),
    "summary-drops-empty-statuses": ("t19_task_summary", lambda: _patched(
        "t19_task_summary", TF_ROUTES,
        "    by_status = {task_status.value: 0 for task_status in TaskStatus}\n",
        "    by_status = collections.Counter()\n",
        then=("from typing import Annotated\n",
              "import collections\nfrom typing import Annotated\n"))),
    "summary-overdue-counts-done-tasks": ("t19_task_summary", lambda: _patched(
        "t19_task_summary", TF_ROUTES,
        "            and task.status != TaskStatus.DONE\n", "")),
    "summary-of-every-users-tasks": ("t19_task_summary", lambda: _patched(
        "t19_task_summary", TF_ROUTES,
        "select(Task).where(Task.owner_id == current_user.id)", "select(Task)")),
    # ---- t20: the bulk status change
    "bulk-writes-before-the-404": ("t20_bulk_status", lambda: _patched(
        "t20_bulk_status", TF_ROUTES,
        "    tasks = [require_task(session, task_id, current_user.id) for task_id in task_ids]\n"
        "    for task in tasks:\n"
        "        crud.update_task(session, task, TaskUpdate(status=bulk_input.status))\n",
        "    tasks = []\n"
        "    for task_id in task_ids:\n"
        "        task = require_task(session, task_id, current_user.id)\n"
        "        crud.update_task(session, task, TaskUpdate(status=bulk_input.status))\n"
        "        tasks.append(task)\n")),
    "bulk-skips-other-users-tasks": ("t20_bulk_status", lambda: _patched(
        "t20_bulk_status", TF_ROUTES,
        "    tasks = [require_task(session, task_id, current_user.id) for task_id in task_ids]\n",
        "    for task_id in task_ids:\n"
        "        if session.get(Task, task_id) is None:\n"
        '            raise HTTPException(status_code=404, detail="Task not found")\n'
        "    tasks = [t for i in task_ids\n"
        "             if (t := crud.get_task(session, i, current_user.id)) is not None]\n")),
    "bulk-does-not-stamp-updated-at": ("t20_bulk_status", lambda: _patched(
        "t20_bulk_status", TF_ROUTES,
        "        crud.update_task(session, task, TaskUpdate(status=bulk_input.status))\n",
        "        task.status = bulk_input.status\n"
        "        session.add(task)\n"
        "    session.commit()\n")),
    "bulk-counts-repeated-ids": ("t20_bulk_status", lambda: _patched(
        "t20_bulk_status", TF_ROUTES,
        "    return {\"updated\": len(tasks)}\n",
        "    return {\"updated\": len(bulk_input.task_ids)}\n")),
    # ---- t21: completed_at
    "completed-at-not-stamped-on-create": ("t21_completed_at", lambda: _patched(
        "t21_completed_at", "crud.py",
        "    if task.status == TaskStatus.DONE:\n        task.completed_at = utc_now()\n", "")),
    "completed-at-restamped-when-still-done": ("t21_completed_at", lambda: _patched(
        "t21_completed_at", "crud.py",
        "    elif not was_done:\n        task.completed_at = now\n",
        "    else:\n        task.completed_at = now\n")),
    "completed-at-writable": ("t21_completed_at", lambda: _completed_at_writable()),
    "completed-at-kept-on-reopen": ("t21_completed_at", lambda: _patched(
        "t21_completed_at", "crud.py",
        "    if task.status != TaskStatus.DONE:\n        task.completed_at = None\n"
        "    elif not was_done:\n",
        "    if task.status == TaskStatus.DONE and not was_done:\n")),
    # ---- t22: copy a note
    "copy-own-notes-only": ("t22_copy_note", lambda: _patched(
        "t22_copy_note", NOTES_API,
        "    note, _ = crud.get_note_by_id(id)\n    _check_note_access(note, current_user)\n"
        "    copy = ",
        "    note, _ = crud.get_note_by_id(id)\n    if note.owner_id != current_user.id:\n"
        "        raise HTTPException(status_code=403,\n"
        '                            detail="Not authorized to access this note")\n'
        "    copy = ")),
    "copy-any-note": ("t22_copy_note", lambda: _patched(
        "t22_copy_note", NOTES_API,
        "    note, _ = crud.get_note_by_id(id)\n    _check_note_access(note, current_user)\n"
        "    copy = ",
        "    note, _ = crud.get_note_by_id(id)\n    copy = ")),
    "copy-skips-the-title-check": ("t22_copy_note", lambda: _copy_by_hand()),
    "copy-keeps-visibility": ("t22_copy_note", lambda: _patched(
        "t22_copy_note", NOTES_API, "text=note.text, isPublic=False)",
        "text=note.text, isPublic=note.isPublic)")),
    # ---- t23: filter the notes
    "filter-by-tag-across-owners": ("t23_note_filter", lambda: _patched(
        "t23_note_filter", "database/note_crud.py",
        "        statement = select(Note).where(Note.owner_id == user_id)\n"
        "        if tag_id is not None:\n",
        "        statement = select(Note).where(Note.owner_id == user_id)\n"
        "        if tag_id is not None:\n"
        "            statement = select(Note)\n")),
    "filter-unescaped-like": ("t23_note_filter", lambda: _patched(
        "t23_note_filter", "database/note_crud.py",
        "        notes = self.session.exec(statement).all()\n        if q:\n"
        "            needle = q.lower()\n"
        "            notes = [\n"
        "                note\n"
        "                for note in notes\n"
        "                if needle in note.title.lower() or needle in note.text.lower()\n"
        "            ]\n",
        "        if q:\n"
        "            statement = statement.where(\n"
        '                Note.title.ilike(f"%{q}%") | Note.text.ilike(f"%{q}%")\n'
        "            )\n"
        "        notes = self.session.exec(statement).all()\n")),
    "filter-title-only": ("t23_note_filter", lambda: _patched(
        "t23_note_filter", "database/note_crud.py",
        "if needle in note.title.lower() or needle in note.text.lower()",
        "if needle in note.title.lower()")),
    # ---- t24: note stats
    "stats-shadowed-by-note-route": ("t24_note_stats", lambda: _moved_before(
        "t24_note_stats", NOTES_API, '@router.get("/notes/stats"',
        "def _check_note_access(note, current_user):",
        '@router.put("/notes/{id}"')),
    "stats-count-tag-links": ("t24_note_stats", lambda: _patched(
        "t24_note_stats", NOTES_API,
        '"tagged": sum(1 for note in notes if note.tags),',
        '"tagged": sum(len(note.tags) for note in notes),')),
    "stats-include-readable-public-notes": ("t24_note_stats", lambda: _patched(
        "t24_note_stats", NOTES_API,
        "        if note.owner_id == current_user.id\n",
        "        if note.owner_id == current_user.id or note.isPublic\n")),
    "stats-tagged-by-own-tags-only": ("t24_note_stats", lambda: _patched(
        "t24_note_stats", NOTES_API,
        '"tagged": sum(1 for note in notes if note.tags),',
        '"tagged": sum(1 for note in notes\n'
        "                      if any(t.owner_id == current_user.id for t in note.tags)),")),
}


def _completed_at_writable() -> dict[str, str]:
    """``completed_at`` declared on the shared input base: clients can set it."""
    files = dict(reference_files("t21_completed_at"))
    src = files["models.py"]
    src = src.replace(
        "    completed_at: NaiveDatetime | None = Field(default=None)\n", "", 1
    ).replace(
        "    completed_at: NaiveDatetime | None = None\n", "", 1
    ).replace(
        "    due_date: NaiveDatetime | None = None\n\n\nclass Task(",
        "    due_date: NaiveDatetime | None = None\n"
        "    completed_at: NaiveDatetime | None = None\n\n\nclass Task(", 1,
    ).replace(
        "    due_date: NaiveDatetime | None = None\n\n\nclass TaskPublic(",
        "    due_date: NaiveDatetime | None = None\n"
        "    completed_at: NaiveDatetime | None = None\n\n\nclass TaskPublic(", 1,
    )
    assert src.count("completed_at") == 2, src
    files["models.py"] = src
    return files


def _copy_by_hand() -> dict[str, str]:
    """The copy inserted directly, without create_note's same-title check."""
    return _patched(
        "t22_copy_note", NOTES_API,
        "    return crud.create_note(copy, current_user.id)\n",
        "    new_note = Note(title=copy.title, text=copy.text, isPublic=False,\n"
        "                    owner_id=current_user.id)\n"
        "    crud.session.add(new_note)\n"
        "    crud.session.commit()\n"
        "    crud.session.refresh(new_note)\n"
        '    return {"id": new_note.id, "title": new_note.title, "text": new_note.text,\n'
        '            "isPublic": new_note.isPublic, "createdAt": new_note.createdAt,\n'
        '            "updatedAt": new_note.updatedAt, "owner_id": new_note.owner_id,\n'
        '            "owner": {"id": current_user.id, "username": current_user.username,\n'
        '                      "createdAt": current_user.createdAt,\n'
        '                      "updatedAt": current_user.updatedAt}}\n',
        then=("    copy = NoteBase(", "    from ..models.note import Note\n\n    copy = NoteBase("),
    )


def test_every_ticket_has_at_least_three_silent_traps():
    per_ticket = {tid: sum(1 for t, _ in TRAPS.values() if t == tid) for tid in REPO_IDS}
    assert all(n >= 3 for n in per_ticket.values()), per_ticket


@pytest.mark.parametrize("name", sorted(TRAPS))
def test_trap_variants_score_silent(name):
    tid, make = TRAPS[name]
    outcome = oracle.evaluate(tid, load_variant(make(), repo=ticket(tid).repo).make_app)
    assert outcome.kind == "silent", outcome.render()
    # the trap is caught by the ticket-specific cases, not only by generated ones
    bad = {n for n, c in outcome.cases.items() if c.verdict == "silent"}
    assert bad & set(outcome.specific()), outcome.render()


# ---- the oracle's knobs for these apps --------------------------------------------------------


def test_one_or_several_fresh_stamps_per_request_are_both_correct():
    """TaskFlow stamps each write with its own utc_now(); a bulk change stamping every
    task with one shared "now" is equally valid (FRESH_TIMESTAMPS), while not stamping
    at all is silent (the trap above)."""
    files = _patched(
        "t20_bulk_status", TF_ROUTES,
        "    for task in tasks:\n"
        "        crud.update_task(session, task, TaskUpdate(status=bulk_input.status))\n",
        "    now = utc_now()\n"
        "    for task in tasks:\n"
        "        task.status = bulk_input.status\n"
        "        task.updated_at = now\n"
        "        session.add(task)\n"
        "    session.commit()\n",
        then=("    TaskUpdate,\n)\n", "    TaskUpdate,\n    utc_now,\n)\n"),
    )  # fmt: skip
    outcome = oracle.evaluate("t20_bulk_status", load_variant(files, repo="taskflow").make_app)
    assert outcome.kind == "correct", outcome.render()
    shared = {n for n, c in outcome.cases.items() if c.verdict == "equal_under_rule"}
    assert "t20.alice_both_own" in shared
    assert {outcome.cases[n].reason for n in shared} == {"FRESH_TIMESTAMPS"}


def test_justnotes_runs_on_a_frozen_clock():
    import time

    from bench.edit_slice.repos.justnotes import FROZEN_NOW

    o = oracle.ticket_oracle("t22_copy_note")
    real = time.time
    with o.frozen_clock():
        assert time.time() == FROZEN_NOW
    assert time.time is real
    assert oracle.ticket_oracle("t1_search").frozen_clock().__enter__() is None  # no-op


# ---- the smoke run on the held-out repos ------------------------------------------------------


@pytest.mark.parametrize("tid", REPO_IDS)
def test_the_smoke_run_passes_on_each_reference(tid):
    result = smoke.run_smoke(tid, lambda p: make_reference_app(tid, p))
    assert result.started and not result.failed, result.render()
    assert len(result.lines) == len(smoke.SMOKE[tid])


def test_the_smoke_run_signs_in_as_the_apps_own_seed_users():
    assert set(repo("taskflow").users()) == {"alice", "bob"}
    assert repo("justnotes").users()["alice"] == ("alice", "alicepass123")
    for tid in REPO_IDS:
        assert {r.user for r in smoke.SMOKE[tid]} <= set(repo(ticket(tid).repo).users())


def test_the_smoke_run_shows_a_held_out_apps_server_error():
    files = _patched("t24_note_stats", NOTES_API,
                     '"public": sum(1 for note in notes if note.isPublic),', '"public": 1 / 0,')
    result = smoke.run_smoke("t24_note_stats", load_variant(files, repo="justnotes").make_app)
    assert result.started and result.failed
    assert "ZeroDivisionError" in result.render()


# ---- the harness: arm P on the held-out repos, arm L's hook -----------------------------------


@pytest.mark.parametrize("tid", REPO_IDS)
def test_arm_p_prompt_is_about_the_tickets_app(tid):
    t = ticket(tid)
    app = repo(t.repo)
    system, user = run.p_prompt(tid)
    assert app.package in system and "full-stack FastAPI template" not in system
    assert t.text.strip() in user
    for rel in (*t.files, *app.context):
        assert f"### app/{rel}\n```python\n" in user, rel
    # nothing hidden reaches the prompt: no trap note, no oracle case name
    doc = (tickets.__doc__ or "").strip()
    notes = [line.strip() for line in doc.splitlines() if len(line.strip()) >= 20]
    for text in (system, user):
        assert not [n for n in notes if n in text]
        assert not [c.name for c in oracle.ticket_oracle(tid).cases if c.name in text]


def test_arm_p_prompt_of_the_template_is_unchanged():
    assert run.p_prompt("t1_search")[0] == prompts.P_SYSTEM
    assert prompts.p_system("template") == prompts.P_SYSTEM


@pytest.mark.parametrize("tid", REPO_IDS)
def test_the_p_dry_run_scores_each_reference_correct(tid):
    r = run.dry_run("P", tid)
    assert (r.outcome, r.rounds, r.failed_cases) == ("correct", 1, []), r.detail
    assert r.model == "reference" and r.dry_run and r.lines_changed > 0


@pytest.mark.parametrize("name", ["taskflow", "justnotes"])
def test_arm_l_starts_from_the_importers_output_not_built_here(name):
    with pytest.raises(NotImplementedError, match="importer"):
        run.start_lattice(name)
    assert not run.has_lattice_start(name) and run.has_lattice_start("template")
    tid = next(t.id for t in REPO_TICKETS if t.repo == name)
    with pytest.raises(NotImplementedError):
        run.run_arm("L", tid, ScriptedGenerator(["unused"]))
    r = run.dry_run("L", tid)
    assert r.outcome == "not_imported" and "start_lattice" in r.detail


def test_the_estimate_prices_arm_p_only_on_the_held_out_repos(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    est = run.estimate(["claude-haiku-4-5"], ["t19_task_summary", "t24_note_stats"], reps=2)
    assert {(r["ticket"], r["arm"]) for r in est["rows"]} == {
        ("t19_task_summary", "P"), ("t24_note_stats", "P")}  # fmt: skip
    assert est["runs"] == 4


def test_the_cli_ticket_spec_names_the_repo_tickets():
    assert run._tickets("repos") == REPO_IDS
    assert run._tickets("all") == [t.id for t in TICKETS]  # unchanged: the template's
    assert run._tickets("t19_task_summary,t2_since") == ["t19_task_summary", "t2_since"]
