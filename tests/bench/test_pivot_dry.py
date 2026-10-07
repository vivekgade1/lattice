"""Experiment D's spec arms (plan 2026-10-05-pivot-spec-authoring.md, item D) on the
pivot's held-out tickets t45-t56: arm S*'s owner delta dispatches to the pivot's blind
deltas, arms S/S+'s draft prompts carry no held-out content, and ``approve`` /
``check_candidate`` / ``coverage`` all work for the pivot's tickets.

* the draft prompts (S and S+) of every pivot ticket name none of the ticket's owner
  scenarios/invariants, its traps, an oracle or a reference;
* arms S, S+ and S* end to end on t45 with a scripted model: draft (and review for S+),
  approval, code, check, correct (and the same through the no-model-call dry run);
* the owner's blind deltas approve clean on their own reference, for every pivot ticket;
* the blind deltas' offline trap coverage (``tests/bench/test_edit_slice_pivot.TRAPS``).
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor

import pytest

from bench.edit_slice import run, sdraft
from bench.edit_slice.tickets import PIVOT
from bench.specs.pivot_blind import BLIND_PIVOT_DELTAS
from lattice.llm.client import ScriptedGenerator

T45 = "t45_restock"


# ---- owner_delta dispatches to the pivot's blind deltas -------------------------------------


def test_owner_delta_dispatches_to_the_pivot_s_blind_deltas():
    for tid in PIVOT:
        assert sdraft.owner_delta(tid) is BLIND_PIVOT_DELTAS[tid]


# ---- the draft prompts carry no held-out content ---------------------------------------------


@pytest.mark.parametrize("tid", PIVOT)
def test_the_draft_prompts_have_the_ticket_and_no_held_out_data(tid):
    from tests.bench.test_edit_slice_pivot import TRAPS

    owner = sdraft.owner_delta(tid)
    held_out = (
        {s.name for s in owner.scenarios}
        | {i.name for i in owner.invariants}
        | {n for n, (t, _) in TRAPS.items() if t == tid}
    )
    for checklist in (False, True):
        system, user = sdraft.draft_prompt(tid, checklist=checklist)
        assert run.ticket(tid).text.strip() in user
        text = system + user
        found = [n for n in held_out if n in text]
        assert not found, (tid, checklist, found)
        assert "oracle" not in text.lower() and "reference" not in text.lower()


# ---- arms S, S+ and S* end to end on t45 -----------------------------------------------------


def _draft_block(tid: str) -> str:
    return f"```python\n{sdraft.render_delta(sdraft.owner_delta(tid))}```"


def test_arm_s_drafts_approves_and_codes_on_t45():
    draft = _draft_block(T45)
    reference = run.reference_answer("P", T45)
    gen = ScriptedGenerator([draft, reference])
    record = run.run_arm("S", T45, gen, model="scripted")

    owner = sdraft.owner_delta(T45)
    assert record.spec_rounds == 1 and not record.spec_failed
    assert record.spec_drafted == sdraft.drafted_rules(owner)
    assert record.spec_rejected == 0
    code_user = gen.calls[1][1][0]["content"]
    assert sdraft.SPEC_SECTION in code_user
    assert all(s.name in code_user for s in owner.scenarios)
    assert record.outcome == "correct", (record.failure, record.detail)
    assert record.rounds == 1


def test_arm_splus_drafts_reviews_and_codes_on_t45():
    draft = _draft_block(T45)  # the owner's delta standing in for both calls
    reference = run.reference_answer("P", T45)
    gen = ScriptedGenerator([draft, draft, reference])
    record = run.run_arm("S+", T45, gen, model="scripted")

    assert record.spec_rounds == 1 and not record.spec_failed
    assert record.spec_review_rounds == 1 and not record.spec_review_failed
    assert record.spec_drafted_before_review == record.spec_drafted_after_review > 0
    assert record.outcome == "correct", (record.failure, record.detail)


def test_arm_s_star_uses_the_owner_s_blind_delta_on_t45():
    reference = run.reference_answer("P", T45)
    gen = ScriptedGenerator([reference])
    record = run.run_arm("S*", T45, gen, model="scripted")

    assert len(gen.calls) == 1  # no spec step
    owner = sdraft.owner_delta(T45)
    assert all(s.name in gen.calls[0][1][0]["content"] for s in owner.scenarios)
    assert record.spec_rounds == 0 and record.spec_drafted == 0 and record.spec_rejected == 0
    assert record.outcome == "correct", (record.failure, record.detail)


@pytest.mark.parametrize("arm", ["S", "S+", "S*"])
def test_the_pivot_dry_run_is_correct_on_t45(arm):
    record = run.dry_run(arm, T45)
    assert record.outcome == "correct", (record.failure, record.detail)
    if arm == "S+":
        assert record.spec_review_rounds == 1 and not record.spec_review_failed


# ---- approve / check_candidate / coverage on every pivot ticket -----------------------------


def _approve(tid: str) -> tuple[str, sdraft.Approval]:
    return tid, sdraft.approve(tid, sdraft.owner_delta(tid))


def _coverage(tid: str) -> tuple[int, int]:
    return sdraft.coverage(tid, sdraft.owner_delta(tid))


def test_approve_keeps_every_pivot_blind_delta_on_its_reference():
    with ProcessPoolExecutor(max_workers=4) as pool:
        results = dict(pool.map(_approve, PIVOT))
    for tid, approval in results.items():
        delta = sdraft.owner_delta(tid)
        assert approval.rejected == (), (tid, approval.rejected)
        assert approval.approved.scenarios == delta.scenarios
        assert approval.approved.invariants == delta.invariants
        assert approval.error_rate == 0.0


def test_check_candidate_passes_every_pivot_reference_against_its_blind_delta():
    from bench.edit_slice.reference import reference_files

    for tid in PIVOT:
        report = sdraft.check_candidate(tid, reference_files(tid), sdraft.owner_delta(tid))
        assert report.verdict == "pass", (tid, report.render())


@pytest.mark.slow
def test_the_pivot_s_blind_deltas_offline_trap_coverage():
    with ProcessPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_coverage, PIVOT))
    caught, total = sum(c for c, _ in results), sum(n for _, n in results)
    # the pivot's blind deltas were written from the ticket texts only, before any check
    # ran (bench/specs/pivot_blind.py); not every trap need be caught -- this pins the
    # observed number so a regression in approve/coverage/check_candidate is noticed.
    assert (caught, total) == (45, 52)
