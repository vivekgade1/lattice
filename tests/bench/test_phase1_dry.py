"""Phase 1's dry run (docs/superpowers/specs/2026-10-04-phase1-loop-experiment.md, P1-E):
arms S and S* end to end with a scripted model, at no cost.

* arm S on t39: a drafted spec with one correct and one wrong scenario (the wrong one is
  rejected by the simulated approval), then a trap variant (caught by the approved
  scenario, repaired), then the reference (correct);
* arm S* on t39: the owner's blind delta in the prompt, the reference answer;
* a draft that never validates: S goes on with the base spec only (``spec_failed``);
* ``parse_delta`` refuses imports and dunder access, and invalid specs;
* the approval approves every owner's blind delta on its own reference (14/14, nothing
  rejected), and the blind deltas catch 44 of Phase 0's 50 traps (offline coverage).
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor

import pytest

from bench.edit_slice import prompts, run, sdraft
from bench.edit_slice.tickets import OPTION1, OPTION1B
from bench.specs.multivendor_tickets import BLIND_DELTAS, DELTAS, SpecDelta
from lattice.llm.client import ScriptedGenerator
from lattice.spec import CheckFinding, Report, Scenario, Step

TICKETS = (*OPTION1, *OPTION1B)
T39 = "t39_bulk_price"

# ---- the scripted answers -------------------------------------------------------------------

SPEC_ANSWER = """Here is the delta.

```python
TWICE = {"changes": [{"product_id": 1, "price": 13.0}, {"product_id": 1, "price": 14.0}]}

DELTA = SpecDelta(
    scenarios=(
        # correct: a product listed twice is one product changed
        Scenario(
            "t39_listed_twice",
            steps=(Step("POST", "/products/bulk-price", "vicky", json=TWICE,
                        expect_body={"updated": 1}),),
        ),
        # wrong: another vendor's product is 403, not 404
        Scenario(
            "t39_foreign_is_404",
            steps=(Step("POST", "/products/bulk-price", "vicky",
                        json={"changes": [{"product_id": 5, "price": 1.0}]},
                        expect_status=404),),
        ),
    ),
    unchecked=(Unchecked("t39: the body is a list", ("Product.price",)),),
)
```
"""

INVALID_SPEC_ANSWER = """```python
DELTA = SpecDelta(scenarios=(Scenario("t39_x", steps=(Step("GET", "/products", "nobody"),)),))
```"""


def _answer(files: dict[str, str], tid: str = T39) -> str:
    """``files`` (relative to the app) as an arm-P answer: the files that differ from the
    start state."""
    start = run.p_start(run.ticket(tid))
    repo = run.ticket(tid).repo
    return prompts.render_files(
        {
            prompts.APP_PREFIX + rel: src
            for rel, src in files.items()
            if src != run.p_view(start, rel, repo)
        }
    )


def _trap(name: str) -> dict[str, str]:
    from tests.bench.test_edit_slice_option1 import TRAPS

    return TRAPS[name][1]()


# ---- parse_delta ----------------------------------------------------------------------------


def test_parse_delta_reads_a_valid_draft():
    delta = sdraft.parse_delta(SPEC_ANSWER)
    assert [s.name for s in delta.scenarios] == ["t39_listed_twice", "t39_foreign_is_404"]
    assert len(delta.unchecked) == 1 and not delta.programs and not delta.approved


@pytest.mark.parametrize(
    "body",
    [
        "import os\nDELTA = SpecDelta()",
        "from os import path\nDELTA = SpecDelta()",
        "DELTA = SpecDelta()\nX = __import__('os')",
        "DELTA = SpecDelta()\nX = ().__class__.__bases__",
        "DELTA = SpecDelta()\nX = open('/etc/passwd')",
        "DELTA = SpecDelta()\nX = Step._private",
    ],
)
def test_parse_delta_refuses_imports_dunders_and_unknown_names(body):
    with pytest.raises(sdraft.DraftError) as err:
        sdraft.parse_delta(f"```python\n{body}\n```")
    assert err.value.feedback


def test_parse_delta_reports_validation_problems_and_missing_answers():
    with pytest.raises(sdraft.DraftError, match="S011"):
        sdraft.parse_delta(INVALID_SPEC_ANSWER)
    with pytest.raises(sdraft.DraftError, match="python"):
        sdraft.parse_delta("no code here")
    with pytest.raises(sdraft.DraftError, match="DELTA"):
        sdraft.parse_delta("```python\nX = 1\n```")
    with pytest.raises(sdraft.DraftError, match="S010"):  # a base scenario's name
        sdraft.parse_delta(
            '```python\nDELTA = SpecDelta(scenarios=(Scenario("catalogue_is_public"),))\n```'
        )


def test_a_rendered_delta_parses_back_to_itself():
    for tid in TICKETS:
        delta = BLIND_DELTAS[tid]
        text = "```python\n" + sdraft.render_delta(delta) + "\n```"
        assert sdraft.parse_delta(text) == delta, tid


# ---- the prompts ----------------------------------------------------------------------------


@pytest.mark.parametrize("tid", TICKETS)
def test_the_draft_prompt_has_the_ticket_and_base_spec_and_no_held_out_data(tid):
    from tests.bench.test_edit_slice_option1 import TRAPS

    system, user = sdraft.draft_prompt(tid)
    assert "DELTA = SpecDelta(" in system and "expect_db" in system
    assert "Vicky's Crafts" in system  # the seed id table
    assert run.ticket(tid).text.strip() in user
    assert "ORDER_MACHINE" in user and "INVARIANTS" in user and "Schema.from_pairs" in user
    owner = {s.name for s in DELTAS[tid].scenarios} | {i.name for i in DELTAS[tid].invariants}
    held_out = owner | {n for n, (t, _) in TRAPS.items() if t == tid}
    text = system + user
    assert not [n for n in held_out if n in text]
    assert "oracle" not in text.lower() and "reference" not in text.lower()


def test_the_code_prompt_adds_the_approved_spec_to_arm_q_s_prompt():
    q_system, q_user = run.arm_prompt("Q", T39)
    system, user = run.arm_prompt("S*", T39)
    assert system.startswith(q_system.rstrip("\n")) and user.startswith(q_user)
    assert sdraft.SPEC_SECTION in user
    assert all(s.name in user for s in BLIND_DELTAS[T39].scenarios)
    # an empty delta adds no section
    assert sdraft.code_prompt(T39, SpecDelta())[1] == q_user


# ---- the feedback ---------------------------------------------------------------------------


def test_spec_feedback_is_rule_first_capped_and_never_names_held_out_data():
    findings = [
        CheckFinding(
            layer="D",
            verdict="silent",
            rule=f"scenario: s{i % 2}",
            endpoint="POST /x",
            case=f"s{i % 2} / step {i}",
            request={"method": "POST", "path": "/x", "user": "vicky", "json": {"a": i}},
            details={
                "problems": ["status: expected 200, got 403"],
                "expected": {"status": 200, "body": {"updated": 1}},
                "actual": {"status": 403, "body": {"detail": "x" * 500}},
            },
            db_diff={"products": {"changed": [{"id": 1, "price": [12.0, 1.0]}]}},
        )
        for i in range(10)
    ]
    findings.append(
        CheckFinding(
            layer="C",
            verdict="silent",
            rule="A product's price is positive.",
            case="s0 / step 1",
            request={"method": "POST", "path": "/x"},
            invariant_violations=[{"invariant": "price_positive", "rows": [{"id": 1}]}],
        )
    )
    text = sdraft.spec_feedback(Report(layers="ABCD", findings=findings))
    lines = text.splitlines()
    rules = [i for i, line in enumerate(lines) if line.startswith("rule")]
    assert rules and rules[0] < next(i for i, line in enumerate(lines) if "POST /x" in line)
    assert text.count("POST /x as vicky") == 8  # at most 8 findings shown
    assert "3 more findings" in text
    assert "expected 200" in text and "got 403" in text and "db products" in text
    assert len(max(lines, key=len)) < 400  # bodies are shortened
    assert "oracle" not in text.lower() and "reference" not in text.lower()


# ---- arm S and S* end to end ----------------------------------------------------------------


def test_arm_s_drafts_approves_checks_and_repairs_on_t39():
    trap = _answer(_trap("t39-counts-entries"))
    reference = run.reference_answer("P", T39)
    gen = ScriptedGenerator([SPEC_ANSWER, trap, reference])
    record = run.run_arm("S", T39, gen, model="scripted")

    assert record.spec_drafted == 2 and record.spec_rejected == 1
    assert [r[:2] for r in record.spec_rejected_rules] == [["scenario", "t39_foreign_is_404"]]
    assert record.spec_rounds == 1 and not record.spec_failed
    # the code prompt carries the approved scenario only
    code_user = gen.calls[1][1][0]["content"]
    assert sdraft.SPEC_SECTION in code_user
    assert "t39_listed_twice" in code_user and "t39_foreign_is_404" not in code_user
    # the trap is caught by the approved scenario (layer D) and repaired
    assert record.round_problems == ["check", None]
    assert record.layers_fed_back == [["D"]]
    feedback = gen.calls[2][1][-1]["content"]
    assert "t39_listed_twice" in feedback
    assert "oracle" not in feedback.lower() and "reference" not in feedback.lower()
    assert record.final_check_findings == 0
    assert record.outcome == "correct", (record.failure, record.detail)
    assert record.rounds == 2


def test_arm_s_star_uses_the_owner_s_blind_delta():
    gen = ScriptedGenerator([run.reference_answer("P", T39)])
    record = run.run_arm("S*", T39, gen, model="scripted")
    assert len(gen.calls) == 1  # no spec step
    assert all(s.name in gen.calls[0][1][0]["content"] for s in BLIND_DELTAS[T39].scenarios)
    assert record.spec_rounds == 0 and record.spec_drafted == 0 and record.spec_rejected == 0
    assert record.round_problems == [None] and record.layers_fed_back == []
    assert record.final_check_findings == 0
    assert record.outcome == "correct", (record.failure, record.detail)


def test_arm_s_goes_on_with_the_base_spec_when_the_draft_never_validates():
    reference = run.reference_answer("P", T39)
    gen = ScriptedGenerator([INVALID_SPEC_ANSWER] * 3 + [reference])
    record = run.run_arm("S", T39, gen, model="scripted")
    assert record.spec_failed and record.spec_rounds == 3
    assert "S011" in gen.calls[1][1][-1]["content"]  # the validation feedback
    assert sdraft.SPEC_SECTION not in gen.calls[3][1][0]["content"]
    assert record.spec_drafted == 0 and record.final_check_findings == 0
    assert record.outcome == "correct"


def test_the_spec_arms_are_registered_and_python_arms():
    from bench.edit_slice import rescore

    assert run.PHASE1_ARMS == ("P", "Q", "S", "S*")
    assert {"S", "S*"} <= set(run.ALL_ARMS) and {"S", "S*"} <= rescore.PYTHON_ARMS
    assert run.language("S") == run.language("S*") == "P"


# ---- the approval and coverage on every ticket ----------------------------------------------


def _approve(tid: str) -> tuple[str, sdraft.Approval]:
    return tid, sdraft.approve(tid, BLIND_DELTAS[tid])


def _coverage(tid: str) -> tuple[int, int]:
    return sdraft.coverage(tid, BLIND_DELTAS[tid])


def test_approve_rejects_only_the_rules_the_reference_breaks():
    wrong = Scenario(
        "t39_wrong", steps=(Step("GET", "/products/1", None, expect_body={"price": 1.0}),)
    )
    delta = SpecDelta(scenarios=(*BLIND_DELTAS[T39].scenarios, wrong))
    approval = sdraft.approve(T39, delta)
    assert approval.drafted == 3 and [r[:2] for r in approval.rejected] == [
        ("scenario", "t39_wrong")
    ]
    assert approval.approved.scenarios == BLIND_DELTAS[T39].scenarios
    assert approval.error_rate == pytest.approx(1 / 3)


def test_approve_rejects_a_program_the_reference_disagrees_with_as_a_whole():
    from dataclasses import replace

    from bench.specs.multivendor import SPEC
    from bench.specs.multivendor_tickets import apply
    from lattice.spec import validate

    tid = "t34_customer_spend"
    owner = BLIND_DELTAS[tid]
    wrong = owner.programs[tid].replace('!= "cancelled"', '!= "pending"')
    approval = sdraft.approve(tid, replace(owner, programs={tid: wrong}))
    assert [r[:2] for r in approval.rejected] == [("program", tid)]
    assert approval.approved.scenarios == owner.scenarios and not approval.approved.programs
    # its unchecked entry names the rejected program: dropped with it
    assert approval.approved.unchecked == ()
    assert not validate(apply(SPEC, approval.approved))


def test_approve_keeps_every_blind_delta_on_its_reference():
    with ProcessPoolExecutor(max_workers=8) as pool:
        results = dict(pool.map(_approve, TICKETS))
    for tid, approval in results.items():
        delta = BLIND_DELTAS[tid]
        assert approval.rejected == (), (tid, approval.rejected)
        assert approval.approved == delta, tid
        assert approval.drafted == len(delta.scenarios) + len(delta.invariants) + len(
            delta.programs
        )
        assert approval.error_rate == 0.0


@pytest.mark.slow
def test_the_blind_deltas_cover_44_of_the_50_traps():
    with ProcessPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_coverage, TICKETS))
    assert (sum(c for c, _ in results), sum(n for _, n in results)) == (44, 50)


def test_the_owner_s_approvals_apply_to_every_candidate_check():
    from bench.edit_slice.reference import reference_files

    report = sdraft.check_candidate(
        "t33_top_sellers", reference_files("t33_top_sellers"), sdraft.owner_delta("t33_top_sellers")
    )
    assert report.verdict == "pass", report.render()
