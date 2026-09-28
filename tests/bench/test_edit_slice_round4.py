"""N4's fresh held-out set (t25-t30, benchmark hygiene:
``docs/superpowers/specs/2026-09-27-lattice-surface-design.md`` §5), written before any
normalisation work was visible to their author: three more tickets on the template
(t25-t27) and three on the held-out repos (t28-t30, spread across TaskFlow and
JustNotes). Held out like t13-t24: no Lattice reference is written for any of them.

* each reference scores correct against its own oracle, every raise site reached;
* the unmodified app is not correct, noticed by the ticket-specific cases;
* trap-violating variants of the references score silent (at least 3, covering all
  six tickets);
* the visible smoke run passes on each reference;
* arm P's dry run scores each reference correct in one round; arm L's has no reference
  (``no_reference``);
* ``--tickets round4`` runs t1-t30.
"""

from __future__ import annotations

import pytest

from bench.edit_slice import oracle, prompts, run, smoke, tickets
from bench.edit_slice.apps import repo
from bench.edit_slice.lattice_reference import GAPS, has_lattice_reference
from bench.edit_slice.reference import REFERENCE_ROOT, make_reference_app, reference_files
from bench.edit_slice.tickets import (
    ALL_TICKETS,
    ROUND4_NEW,
    ROUND4_REPO_TICKETS,
    ROUND4_TEMPLATE,
    ROUND4_TICKETS,
    ticket,
)
from bench.edit_slice.variant import load_variant
from bench.template_app import make_app as template_make_app
from lattice.llm.client import ScriptedGenerator

TEMPLATE_IDS = [t.id for t in ROUND4_TEMPLATE]
REPO_IDS = [t.id for t in ROUND4_REPO_TICKETS]
NEW_IDS = [*TEMPLATE_IDS, *REPO_IDS]


# ---- the tickets --------------------------------------------------------------------------


def test_six_fresh_tickets_written_after_t1_t24():
    assert NEW_IDS == list(ROUND4_NEW)
    assert TEMPLATE_IDS == [
        "t25_bulk_delete_items",
        "t26_move_items",
        "t27_item_description_coverage",
    ]
    assert REPO_IDS == ["t28_task_reassign", "t29_tag_summary", "t30_bulk_delete_notes"]
    assert [ticket(t).repo for t in REPO_IDS] == ["taskflow", "justnotes", "justnotes"]
    assert all(ticket(t).repo == "template" for t in TEMPLATE_IDS)
    assert [t.id for t in ROUND4_TICKETS] == [*[t.id for t in ALL_TICKETS], *NEW_IDS]
    for t in (*ROUND4_TEMPLATE, *ROUND4_REPO_TICKETS):
        assert t.title and t.text and t.routes and t.files and t.base is None
        root = REFERENCE_ROOT / t.id
        own = {p.relative_to(root).as_posix() for p in root.rglob("*.py")
               if "__pycache__" not in p.parts}  # fmt: skip
        assert set(t.files) == own, t.id
        for rel in t.files:
            assert (repo(t.repo).root / rel).is_file(), (t.id, rel)


def test_no_lattice_reference_is_written_for_any_of_them():
    for tid in NEW_IDS:
        assert not has_lattice_reference(tid)
        assert tid not in GAPS


def test_the_order_written_is_recorded_in_the_module_docstring():
    doc = tickets.__doc__ or ""
    for tid in NEW_IDS:
        assert f"``{tid}``" in doc, tid
    assert "t25, t26, t27, t28, t29, t30" in " ".join(doc.split())


@pytest.mark.parametrize(
    ("tid", "spelled_out"),
    [
        ("t25_bulk_delete_items", ["atomic", "all or nothing", "rolled back", "once",
                                   "repeated"]),  # fmt: skip
        ("t26_move_items", ["atomic", "both ids", "before anything", "self-lock",
                            "own account is fine"]),  # fmt: skip
        ("t27_item_description_coverage", ["route order", "swallowed", "own items only",
                                           "admin does not"]),  # fmt: skip
        ("t28_task_reassign", ["task not found", "403", "owner-scoped", "stranger"]),
        ("t29_tag_summary", ["own notes", "link routes", "whose tag", "independent"]),
        ("t30_bulk_delete_notes", ["atomic", "all or nothing", "existence before",
                                   "counted once"]),  # fmt: skip
    ],
)
def test_the_traps_are_not_spelled_out(tid, spelled_out):
    text = ticket(tid).text.lower()
    assert not [w for w in spelled_out if w in text]


# ---- the oracle: references correct, the unmodified app not -------------------------------


@pytest.fixture(scope="module")
def reference_outcomes() -> dict[str, oracle.Outcome]:
    return {tid: oracle.evaluate(tid, lambda p, tid=tid: make_reference_app(tid, p))
            for tid in NEW_IDS}  # fmt: skip


@pytest.mark.parametrize("tid", NEW_IDS)
def test_each_reference_is_correct_against_its_own_oracle(tid, reference_outcomes):
    outcome = reference_outcomes[tid]
    assert outcome.kind == "correct", outcome.render()
    assert outcome.unverified == (), outcome.render()
    assert {c.verdict for c in outcome.cases.values()} == {"equal"}


EXPECTED_COUNTS = {
    "t25_bulk_delete_items": (6, 7),
    "t26_move_items": (17, 8),
    "t27_item_description_coverage": (6, 5),
    "t28_task_reassign": (17, 6),
    "t29_tag_summary": (6, 4),
    "t30_bulk_delete_notes": (6, 6),
}


def test_case_counts(reference_outcomes):
    counts = {tid: o.counts() for tid, o in reference_outcomes.items()}
    for tid, (generated, specific) in counts.items():
        assert generated >= 6 and specific >= 4, (tid, generated, specific)
    assert counts == EXPECTED_COUNTS


def _original_app(repo_name: str):
    if repo_name == "template":
        return template_make_app
    if repo_name == "taskflow":
        from bench.import_repos.taskflow import make_app

        return make_app
    from bench.import_repos.justnotes import make_app

    return make_app


@pytest.mark.parametrize("tid", NEW_IDS)
def test_the_unmodified_app_is_not_correct(tid):
    outcome = oracle.evaluate(tid, _original_app(ticket(tid).repo))
    assert outcome.kind != "correct", outcome.render()
    assert any(c.verdict in ("silent", "loud") for c in outcome.specific().values())


# ---- trap-violating variants ----------------------------------------------------------------


def _patched(tid: str, path: str, old: str, new: str, count: int = 1) -> dict[str, str]:
    files = dict(reference_files(tid))
    assert files[path].count(old) == count, (tid, path, old)
    files[path] = files[path].replace(old, new)
    return files


def _moved_before(tid: str, path: str, block_start: str, block_end: str,
                  anchor: str) -> dict[str, str]:  # fmt: skip
    """``path`` with the block [block_start, block_end) moved to just before ``anchor``."""
    files = dict(reference_files(tid))
    src = files[path]
    start, end = src.index(block_start), src.index(block_end)
    block = src[start:end]
    src = src[:start] + src[end:]
    at = src.index(anchor)
    files[path] = src[:at] + block + src[at:]
    return files


TRAPS = {
    # ---- t25: bulk-delete items
    "bulk-delete-items-skips-ownership-check": ("t25_bulk_delete_items", lambda: _patched(
        "t25_bulk_delete_items", "api/routes/items.py",
        "        if not current_user.is_superuser and (item.owner_id != current_user.id):\n"
        '            raise HTTPException(status_code=403, detail="Not enough permissions")\n',
        "")),
    "bulk-delete-items-not-atomic": ("t25_bulk_delete_items", lambda: _patched(
        "t25_bulk_delete_items", "api/routes/items.py",
        "    items: list[Item] = []\n"
        "    for item_id in unique_ids:\n"
        "        item = session.get(Item, item_id)\n"
        "        if not item:\n"
        '            raise HTTPException(status_code=404, detail="Item not found")\n'
        "        if not current_user.is_superuser and (item.owner_id != current_user.id):\n"
        '            raise HTTPException(status_code=403, detail="Not enough permissions")\n'
        "        items.append(item)\n"
        "\n"
        "    for item in items:\n"
        "        session.delete(item)\n"
        "    session.commit()\n"
        '    return {"deleted": len(items)}\n',
        "    deleted = 0\n"
        "    for item_id in unique_ids:\n"
        "        item = session.get(Item, item_id)\n"
        "        if not item:\n"
        '            raise HTTPException(status_code=404, detail="Item not found")\n'
        "        if not current_user.is_superuser and (item.owner_id != current_user.id):\n"
        '            raise HTTPException(status_code=403, detail="Not enough permissions")\n'
        "        session.delete(item)\n"
        "        session.commit()\n"
        "        deleted += 1\n"
        '    return {"deleted": deleted}\n')),
    "bulk-delete-items-counts-repeats": ("t25_bulk_delete_items", lambda: _patched(
        "t25_bulk_delete_items", "api/routes/items.py",
        '    return {"deleted": len(items)}\n', '    return {"deleted": len(body.item_ids)}\n')),
    # ---- t26: move items
    "move-items-skips-inactive-check": ("t26_move_items", lambda: _patched(
        "t26_move_items", "api/routes/users.py",
        "    if not to_user.is_active:\n"
        "        raise HTTPException(\n"
        '            status_code=400, detail="Cannot move items to an inactive user"\n'
        "        )\n", "")),
    "move-items-skips-same-user-check": ("t26_move_items", lambda: _patched(
        "t26_move_items", "api/routes/users.py",
        "    if from_id == body.to_user_id:\n"
        "        raise HTTPException(\n"
        '            status_code=400, detail="Cannot move items to the same user"\n'
        "        )\n", "")),
    "move-items-checks-inactive-after-moving": ("t26_move_items", lambda: _patched(
        "t26_move_items", "api/routes/users.py",
        "    if from_id == body.to_user_id:\n"
        "        raise HTTPException(\n"
        '            status_code=400, detail="Cannot move items to the same user"\n'
        "        )\n"
        "    if not to_user.is_active:\n"
        "        raise HTTPException(\n"
        '            status_code=400, detail="Cannot move items to an inactive user"\n'
        "        )\n"
        "\n"
        "    statement = select(Item).where(Item.owner_id == from_id)\n"
        "    items = session.exec(statement).all()\n"
        "    for item in items:\n"
        "        item.owner_id = body.to_user_id\n"
        "        session.add(item)\n"
        "    session.commit()\n",
        "    if from_id == body.to_user_id:\n"
        "        raise HTTPException(\n"
        '            status_code=400, detail="Cannot move items to the same user"\n'
        "        )\n"
        "\n"
        "    statement = select(Item).where(Item.owner_id == from_id)\n"
        "    items = session.exec(statement).all()\n"
        "    for item in items:\n"
        "        item.owner_id = body.to_user_id\n"
        "        session.add(item)\n"
        "    session.commit()\n"
        "    if not to_user.is_active:\n"
        "        raise HTTPException(\n"
        '            status_code=400, detail="Cannot move items to an inactive user"\n'
        "        )\n")),
    # ---- t27: item description coverage
    "description-coverage-shadowed-by-item-route": ("t27_item_description_coverage",
        lambda: _moved_before(
            "t27_item_description_coverage", "api/routes/items.py",
            '@router.get("/description-coverage")', '@router.get("/{id}"', '@router.post("/"')),
    "description-coverage-admin-sees-everyone": ("t27_item_description_coverage",
        lambda: _patched(
            "t27_item_description_coverage", "api/routes/items.py",
            "    statement = select(Item.description).where(Item.owner_id == current_user.id)\n",
            "    if current_user.is_superuser:\n"
            "        statement = select(Item.description)\n"
            "    else:\n"
            "        statement = select(Item.description).where(\n"
            "            Item.owner_id == current_user.id\n"
            "        )\n"),
    ),
    "description-coverage-blank-counts-as-with": ("t27_item_description_coverage",
        lambda: _patched(
            "t27_item_description_coverage", "api/routes/items.py",
            "    with_description = sum(1 for d in descriptions if d)\n",
            "    with_description = sum(1 for d in descriptions if d is not None)\n"),
    ),
    # ---- t28: reassign a task (TaskFlow)
    "reassign-ignores-ownership-scope": ("t28_task_reassign", lambda: _patched(
        "t28_task_reassign", "routes.py",
        "    task = require_task(session, task_id, current_user.id)\n"
        "    new_owner = crud.get_user_by_email(session, body.new_owner_email)\n",
        "    task = session.get(Task, task_id)\n"
        "    new_owner = crud.get_user_by_email(session, body.new_owner_email)\n")),
    "reassign-skips-inactive-check": ("t28_task_reassign", lambda: _patched(
        "t28_task_reassign", "routes.py",
        "    if not new_owner.is_active:\n"
        "        raise HTTPException(\n"
        "            status_code=status.HTTP_400_BAD_REQUEST,\n"
        '            detail="Cannot reassign a task to an inactive user",\n'
        "        )\n", "")),
    "reassign-skips-already-belongs-check": ("t28_task_reassign", lambda: _patched(
        "t28_task_reassign", "routes.py",
        "    if task.owner_id == new_owner.id:\n"
        "        raise HTTPException(\n"
        "            status_code=status.HTTP_400_BAD_REQUEST,\n"
        '            detail="Task already belongs to this user",\n'
        "        )\n", "")),
    # ---- t29: tag usage summary (JustNotes)
    "tag-summary-counts-any-linked-note": ("t29_tag_summary", lambda: _patched(
        "t29_tag_summary", "api/tags.py",
        "            .where(NoteTagLink.tag_id == tag.id, Note.owner_id == current_user.id)\n",
        "            .where(NoteTagLink.tag_id == tag.id)\n")),
    "tag-summary-orders-by-id-not-name": ("t29_tag_summary", lambda: _patched(
        "t29_tag_summary", "api/tags.py",
        "select(Tag).where(Tag.owner_id == current_user.id).order_by(Tag.name)",
        "select(Tag).where(Tag.owner_id == current_user.id).order_by(Tag.id)")),
    "tag-summary-count-is-sum-not-tags": ("t29_tag_summary", lambda: _patched(
        "t29_tag_summary", "api/tags.py",
        '    return {"data": data, "count": len(tags)}\n',
        '    return {"data": data, "count": sum(d["note_count"] for d in data)}\n')),
    # ---- t30: bulk-delete notes (JustNotes)
    "bulk-delete-notes-skips-ownership-check": ("t30_bulk_delete_notes", lambda: _patched(
        "t30_bulk_delete_notes", "api/notes.py",
        "        note, _ = crud.get_note_by_id(note_id)\n"
        "        if note.owner_id != current_user.id:\n"
        "            raise HTTPException(\n"
        '                status_code=403, detail="Not authorized to delete this note"\n'
        "            )\n"
        "        notes.append(note)\n",
        "        note, _ = crud.get_note_by_id(note_id)\n"
        "        notes.append(note)\n")),
    "bulk-delete-notes-counts-repeats": ("t30_bulk_delete_notes", lambda: _patched(
        "t30_bulk_delete_notes", "api/notes.py",
        '    return {"deleted": len(notes)}\n', '    return {"deleted": len(body.note_ids)}\n')),
    "bulk-delete-notes-not-atomic": ("t30_bulk_delete_notes", lambda: _patched(
        "t30_bulk_delete_notes", "api/notes.py",
        "    notes = []\n"
        "    for note_id in unique_ids:\n"
        "        note, _ = crud.get_note_by_id(note_id)\n"
        "        if note.owner_id != current_user.id:\n"
        "            raise HTTPException(\n"
        '                status_code=403, detail="Not authorized to delete this note"\n'
        "            )\n"
        "        notes.append(note)\n"
        "\n"
        "    for note in notes:\n"
        "        crud.session.delete(note)\n"
        "    crud.session.commit()\n"
        '    return {"deleted": len(notes)}\n',
        "    deleted = 0\n"
        "    for note_id in unique_ids:\n"
        "        note, _ = crud.get_note_by_id(note_id)\n"
        "        if note.owner_id != current_user.id:\n"
        "            raise HTTPException(\n"
        '                status_code=403, detail="Not authorized to delete this note"\n'
        "            )\n"
        "        crud.session.delete(note)\n"
        "        crud.session.commit()\n"
        "        deleted += 1\n"
        '    return {"deleted": deleted}\n')),
}

TEMPLATE_TRAPS = sorted(n for n, (tid, _) in TRAPS.items() if tid in TEMPLATE_IDS)
REPO_TRAPS = sorted(n for n, (tid, _) in TRAPS.items() if tid in REPO_IDS)


def test_every_new_ticket_has_at_least_two_traps():
    per_ticket = {tid: sum(1 for t, _ in TRAPS.values() if t == tid) for tid in NEW_IDS}
    assert all(n >= 2 for n in per_ticket.values()), per_ticket


def test_at_least_three_trap_variants_score_silent():
    silent = 0
    for tid, make in TRAPS.values():
        candidate_repo = ticket(tid).repo
        outcome = oracle.evaluate(tid, load_variant(make(), repo=candidate_repo).make_app)
        if outcome.kind == "silent":
            silent += 1
    assert silent >= 3
    assert {tid for tid, _ in TRAPS.values()} == set(NEW_IDS)


@pytest.mark.parametrize("name", sorted(TRAPS))
def test_trap_variants_score_silent(name):
    tid, make = TRAPS[name]
    outcome = oracle.evaluate(tid, load_variant(make(), repo=ticket(tid).repo).make_app)
    assert outcome.kind == "silent", outcome.render()
    bad = {n for n, c in outcome.cases.items() if c.verdict == "silent"}
    assert bad & set(outcome.specific()), outcome.render()


# ---- the smoke run ---------------------------------------------------------------------------


@pytest.mark.parametrize("tid", NEW_IDS)
def test_the_smoke_run_passes_on_each_reference(tid):
    result = smoke.run_smoke(tid, lambda p, tid=tid: make_reference_app(tid, p))
    assert result.started and not result.failed, result.render()
    assert len(result.lines) == len(smoke.SMOKE[tid])


def test_the_smoke_requests_sign_in_as_seed_users_of_the_tickets_own_app():
    for tid in NEW_IDS:
        assert {r.user for r in smoke.SMOKE[tid] if r.user} <= set(repo(ticket(tid).repo).users())


# ---- the harness: --tickets round4, the P dry run, arm L's no_reference ---------------------


def test_cli_ticket_spec_round4_is_t1_through_t30():
    assert run._tickets("round4") == [t.id for t in ROUND4_TICKETS]
    assert run._tickets("round4")[:24] == run._tickets("round3")
    assert run._tickets("round4")[24:] == NEW_IDS


@pytest.mark.parametrize("tid", NEW_IDS)
def test_the_p_dry_run_scores_each_reference_correct(tid):
    r = run.dry_run("P", tid)
    assert (r.outcome, r.rounds, r.failed_cases) == ("correct", 1, []), r.detail
    assert r.model == "reference" and r.dry_run and r.lines_changed > 0


@pytest.mark.parametrize("tid", NEW_IDS)
def test_arm_l_dry_run_has_no_reference(tid):
    r = run.dry_run("L", tid)
    assert r.outcome == "no_reference"
    assert "held out" in r.detail


@pytest.mark.parametrize("tid", REPO_IDS)
def test_a_fake_llm_arm_l_run_is_end_to_end(tid):
    """As ``tests/bench/test_edit_slice_imports.py``'s version for t19-t24: the start
    programs returned unchanged build, emit into the original app and smoke-run for
    t28-t30 too (the round-5 CLI re-import)."""
    programs, _ = run.start_lattice(ticket(tid).repo)
    answer = prompts.render_files(programs)
    r = run.run_arm("L", tid, ScriptedGenerator([answer]), model="fake", max_repairs=0)
    assert r.round_problems == ["smoke"], r.detail
    assert r.outcome in ("loud", "silent") and sum(r.cases.values()) > 0


# t28-t30's routes are all new endpoints (reassign, tag summary, bulk-delete) that don't
# exist in the original apps, so they are "new" in arm L's start state whatever the import
# covers -- recorded here the same way t19-t24's LIFT_STATUS is, in
# ``tests/bench/test_edit_slice_imports.py`` (the round-5 CLI re-import).
LIFT_STATUS: dict[str, dict[str, str]] = {
    "t28_task_reassign": {"POST /api/v1/tasks/{task_id}/reassign": "new"},
    "t29_tag_summary": {"GET /tags/summary": "new"},
    "t30_bulk_delete_notes": {"POST /notes/bulk-delete": "new"},
}


@pytest.mark.parametrize("tid", REPO_IDS)
def test_each_tickets_routes_are_lifted_islands_or_new(tid):
    assert run.lift_status(tid) == LIFT_STATUS[tid]
