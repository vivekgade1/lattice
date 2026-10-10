"""lattice.spec.incremental: the rule diff, the trace's change record, draft_since (scripted),
L011 conflicting expectations, impact labelling and the change review.

Scripted (ScriptedGenerator) except the one live run, which runs only with
LATTICE_LIVE_LLM=1 (the Claude Code CLI, haiku; never an API key)."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

import pytest

from lattice.llm.client import ScriptedGenerator
from lattice.spec import Invariant, Scenario, Step
from lattice.spec.draft import draft_json_path, load_spec, main
from lattice.spec.incremental import (
    ImpactItem,
    RuleDiff,
    carry_trace,
    changed_targets,
    diff_rules,
    draft_since,
    impact,
    label_impact,
)
from lattice.spec.lint import Conflict, conflicts, lint
from lattice.spec.report import CheckFinding, Report
from lattice.spec.review import review_change_markdown
from lattice.spec.rules import Question, Rule, Trace, extract_rules, trace_path
from tests.spec.incremental_toy import (
    NOTES_BASE_MODULE,
    NOTES_BASE_TRACE,
    NOTES_CONTEXT,
    NOTES_V1,
    NOTES_V2,
)


def _sub(text: str, *pairs: tuple[str, str]) -> str:
    for old, new in pairs:
        assert old in text, old
        text = text.replace(old, new)
    return text


NEW_MODULE = _sub(
    NOTES_BASE_MODULE,
    ('"""Spec of the notes app (v1), R1..R6."""', '"""Spec of the notes app (v2), R1..R6."""'),
    (
        'Invariant("notes_at_most_3", "A user has at most 3 notes.",\n'
        "                  'count(Note, by=\"user_id\") <= 3'),",
        'Invariant("notes_at_most_5", "A user has at most 5 notes.",\n'
        "                  'count(Note, by=\"user_id\") <= 5'),",
    ),
    (
        'THREE = {"notes": [{"id": 10 + i, "user_id": 1, "text": f"n{i}"} for i in range(3)]}',
        'FIVE = {"notes": [{"id": 10 + i, "user_id": 1, "text": f"n{i}"} for i in range(5)]}',
    ),
    (
        'Scenario("the_fourth_note_is_refused", given=THREE,',
        'Scenario("the_sixth_note_is_refused", given=FIVE,',
    ),
    (
        'expect_db={"notes": {"where": {"user_id": 1}, "count": 3}}',
        'expect_db={"notes": {"where": {"user_id": 1}, "count": 5}}',
    ),
    (
        """        Scenario("delete_own_note", given=ANN_NOTE, steps=(
            Step("DELETE", "/notes/10", "ann", expect_status=204,
                 expect_db={"notes": {"count": 0}}),
        )),""",
        """        Scenario("edit_own_note", given=ANN_NOTE, steps=(
            Step("PATCH", "/notes/10", "ann", json={"text": "oat milk"}, expect_status=200,
                 expect_body={"text": "oat milk"},
                 expect_db={"notes": {"where": {"id": 10}, "equals": {"text": "oat milk"}}}),
        )),""",
    ),
)
NEW_TRACE = {
    "links": {
        "R1": ["scenario:create_a_note"],
        "R2": ["scenario:text_bounds"],
        "R3": ["scenario:read_own_and_other"],
        "R4": ["scenario:the_sixth_note_is_refused", "invariant:notes_at_most_5"],
        "R5": ["scenario:no_token"],
        "R6": ["scenario:edit_own_note"],
    },
    "unchecked": {},
    "context": [],
    "questions": [],
    "retired": {"R6": "the delete endpoint is dropped"},
}


def base_trace() -> Trace:
    return Trace(
        "NOTES.md",
        extract_rules(NOTES_V1),
        {k: list(v) for k, v in NOTES_BASE_TRACE["links"].items()},
        questions=[
            Question(
                "Q1",
                "Is the 404 for another user's note also for ids that do not exist?",
                "yes",
                ["R3"],
            )
        ],  # fmt: skip
    )


def answer(module: str = NEW_MODULE, trace: dict | None = None) -> str:
    trace = NEW_TRACE if trace is None else trace
    q1 = {"id": "Q1", "text": "Is the 404 for another user's note also for ids that do not "
          "exist?", "default": "yes", "rules": ["R3"], "answer": None}  # fmt: skip
    trace = {**trace, "questions": trace.get("questions") or [q1]}
    return f"```python\n{module}```\n\n```json\n{json.dumps(trace, indent=2)}\n```\n"


def run_since(*texts: str, max_repairs: int = 3):
    gen = ScriptedGenerator(list(texts))
    result = draft_since(
        NOTES_V1,
        NOTES_V2,
        base_module=NOTES_BASE_MODULE,
        base_trace=base_trace(),
        context_md=NOTES_CONTEXT,
        generator=gen,
        model="scripted",
        max_repairs=max_repairs,
    )
    return result, gen


# ---- the rule diff --------------------------------------------------------------------------


def test_diff_identical_documents():
    d = diff_rules(NOTES_V1, NOTES_V1)
    assert d.unchanged == [(f"R{i}", f"R{i}") for i in range(1, 7)]
    assert d.changed == d.added == d.removed == [] and d.empty


def test_diff_toy_change_add_remove():
    d = diff_rules(NOTES_V1, NOTES_V2)
    assert d.unchanged == [("R1", "R1"), ("R2", "R2"), ("R3", "R3"), ("R5", "R5")]
    assert d.changed == [("R4", "R4")]
    # DELETE and PATCH rules share wording but name different endpoints: never paired
    assert d.added == ["R6"] and d.removed == ["R6"]
    assert d.old_to_new == {"R1": "R1", "R2": "R2", "R3": "R3", "R5": "R5", "R4": "R4"}
    assert d.to_dict()["changed"][0]["new_text"].startswith("A user has at most 5 notes")


def test_diff_reordering_maps_ids():
    old = "# A\n\n- one rule here.\n- two rule there.\n- three more rules.\n"
    new = "# A\n\n- three more rules.\n- one rule here.\n- two rule there.\n"
    d = diff_rules(old, new)
    assert sorted(d.unchanged) == [("R1", "R2"), ("R2", "R3"), ("R3", "R1")]
    assert d.changed == d.added == d.removed == []
    assert d.old_to_new == {"R1": "R2", "R2": "R3", "R3": "R1"}


def test_diff_rewrapped_rule_is_unchanged_and_a_wrapped_edit_is_changed():
    old = "# A\n\n- A member has at most 50 open todos;\n  one more answers 409.\n- Other.\n"
    rewrapped = "# A\n\n- A member has at most 50\n  open todos; one more answers 409.\n- Other.\n"
    d = diff_rules(old, rewrapped)
    assert d.unchanged == [("R1", "R1"), ("R2", "R2")] and d.empty
    edited = "# A\n\n- A member has at most 20\n  open todos; one more answers 409.\n- Other.\n"
    d = diff_rules(old, edited)
    assert d.changed == [("R1", "R1")] and d.unchanged == [("R2", "R2")]


def test_diff_split_rule_is_one_change_and_one_add():
    old = ("# Todos\n\n- A title is 1 to 100 characters and a member has at most 50 open "
           "todos.\n- Done todos stay done.\n")  # fmt: skip
    new = ("# Todos\n\n- A title is 1 to 100 characters.\n- A member has at most 50 open "
           "todos.\n- Done todos stay done.\n")  # fmt: skip
    d = diff_rules(old, new)
    assert d.unchanged == [("R2", "R3")]
    assert len(d.changed) == 1 and d.changed[0][0] == "R1"
    assert d.changed[0][1] in ("R1", "R2")
    assert len(d.added) == 1 and d.removed == []
    assert diff_rules(old, new) == d  # deterministic


def test_diff_unrelated_rule_is_removed_and_added():
    old = "# A\n\n- A done todo is archived by the sweeper within a minute.\n- Keep.\n"
    new = "# A\n\n- Members get a weekly e-mail digest.\n- Keep.\n"
    d = diff_rules(old, new)
    assert d.unchanged == [("R2", "R2")] and d.changed == []
    assert d.added == ["R1"] and d.removed == ["R1"]


def test_diff_prefers_the_same_section_and_handles_duplicates():
    old = "# A\n\n- Answers 401.\n\n# B\n\n- Answers 401.\n- At most 3 items in B.\n"
    new = "# B\n\n- Answers 401.\n- At most 4 items in B.\n\n# A\n\n- Answers 401.\n"
    d = diff_rules(old, new)
    assert sorted(d.unchanged) == [("R1", "R3"), ("R2", "R1")]
    assert d.changed == [("R3", "R2")]


# ---- the trace's change record --------------------------------------------------------------


def test_old_trace_json_loads_with_empty_change_record():
    old_json = json.dumps({
        "source": "R.md",
        "rules": [{"id": "R1", "text": "x", "section": "", "line": 1}],
        "links": {"R1": ["scenario:a"]},
        "unchecked": {}, "context": [], "questions": [],
    })  # fmt: skip
    t = Trace.from_json(old_json)
    assert t.replaces == {} and t.retired == {} and t.retired_targets == []
    assert Trace.from_json(t.to_json()) == t


def test_trace_change_record_round_trips(tmp_path: Path):
    t = base_trace()
    t.replaces = {"R4": "R4"}
    t.retired = {"R6": "dropped"}
    t.retired_targets = ["scenario:delete_own_note"]
    path = tmp_path / "x.trace.json"
    t.save(path)
    back = Trace.load(path)
    assert back == t and back.retired_targets == ["scenario:delete_own_note"]
    assert json.loads(path.read_text())["replaces"] == {"R4": "R4"}


def test_carry_trace_keeps_unchanged_links_on_new_ids():
    old = "# A\n\n- one rule here.\n- two rule there.\n"
    new = "# A\n\n- zero is new and different.\n- one rule here.\n- two rule there.\n"
    base = Trace("A.md", extract_rules(old), {"R1": ["scenario:one"]}, {"R2": "why"},
                 questions=[Question("Q1", "?", "d", ["R2"])])  # fmt: skip
    carried = carry_trace(diff_rules(old, new), base, "A.md")
    assert carried.links == {"R2": ["scenario:one"]}
    assert carried.unchecked == {"R3": "why"}
    assert carried.questions[0].rules == ["R3"]


# ---- draft_since ------------------------------------------------------------------------------


def test_draft_since_change_add_remove():
    result, gen = run_since(answer())
    assert result.valid, result.problems
    assert result.rounds == 1
    assert isinstance(result.diff, RuleDiff)
    t = result.trace
    assert t.replaces == {"R4": "R4"}
    assert t.retired == {"R6": "the delete endpoint is dropped"}
    assert t.retired_targets == ["invariant:notes_at_most_3", "scenario:delete_own_note",
                                 "scenario:the_fourth_note_is_refused"]  # fmt: skip
    assert result.changed_targets == {
        "added": ["invariant:notes_at_most_5", "scenario:edit_own_note",
                  "scenario:the_sixth_note_is_refused"],
        "removed": ["invariant:notes_at_most_3", "scenario:delete_own_note",
                    "scenario:the_fourth_note_is_refused"],
        "modified": [],
    }  # fmt: skip
    assert [q.id for q in t.questions] == ["Q1"]
    first = gen.calls[0][1][0]["content"]
    assert "- R4 (was R4)" in first and "old: A user has at most 3 notes" in first
    assert "old links: scenario:the_fourth_note_is_refused, invariant:notes_at_most_3" in first
    assert "## Removed rules" in first and "DELETE /notes/{id} deletes" in first
    assert "## Added rules" in first and "R6: PATCH /notes/{id}" in first
    assert "# The base spec module" in first and "notes_at_most_3" in first
    carried = first[first.index("# The base trace, carried over") :]
    assert '"R1": [' in carried and "the_fourth_note_is_refused" not in carried
    assert "Keep the rest of the module unchanged" in first


def test_draft_since_unchanged_links_carry_over_through_the_id_mapping():
    # v2' inserts a rule first: every old id moves by one
    v2 = NOTES_V2.replace("# Notes app\n\n", "# Notes app\n\n- Notes are private.\n")
    diff = diff_rules(NOTES_V1, v2)
    assert ("R1", "R2") in diff.unchanged and ("R5", "R6") in diff.unchanged
    trace = {
        "links": {f"R{int(k[1:]) + 1}": v for k, v in NEW_TRACE["links"].items()},
        "unchecked": {}, "context": ["R1"], "questions": [],
    }  # fmt: skip
    gen = ScriptedGenerator([answer(trace=trace)])
    result = draft_since(NOTES_V1, v2, base_module=NOTES_BASE_MODULE, base_trace=base_trace(),
                         generator=gen, model="scripted")  # fmt: skip
    assert result.valid, result.problems
    assert result.trace.replaces == {"R5": "R4"}
    assert result.trace.retired == {"R6": "removed from the requirements"}
    # Q1 asked about R3, which is now R4
    assert carry_trace(diff, base_trace(), "x").questions[0].rules == ["R4"]
    first = gen.calls[0][1][0]["content"]
    assert '"R2": [\n      "scenario:create_a_note"' in first


def test_draft_since_a_dropped_unchanged_link_is_repaired():
    dropped = {**NEW_TRACE, "links": {**NEW_TRACE["links"], "R3": ["scenario:create_a_note"]}}
    result, gen = run_since(answer(trace=dropped), answer())
    assert result.valid and result.rounds == 2
    repair = gen.calls[1][1][-1]["content"]
    assert "R3 is unchanged (was R3) but lost its link 'scenario:read_own_and_other'" in repair


def test_draft_since_a_removed_rules_check_left_behind_is_repaired():
    kept = _sub(NEW_MODULE, ('        Scenario("edit_own_note"',
                             '        Scenario("delete_own_note", given=ANN_NOTE, steps=(\n'
                             '            Step("DELETE", "/notes/10", "ann", expect_status=204,\n'
                             '                 expect_db={"notes": {"count": 0}}),\n'
                             '        )),\n        Scenario("edit_own_note"'))  # fmt: skip
    result, gen = run_since(answer(kept), answer())
    assert result.valid and result.rounds == 2
    assert (
        "scenario:delete_own_note checked only the removed rule R6"
        in gen.calls[1][1][-1]["content"]
    )


def test_draft_since_rejects_a_bogus_replaces_and_notes_an_unrewritten_change():
    bogus = {**NEW_TRACE, "replaces": {"R1": "R6"}}
    result, gen = run_since(answer(trace=bogus), answer())
    assert result.valid and result.rounds == 2
    assert "replaces 'R1' -> 'R6'" in gen.calls[1][1][-1]["content"]
    # a model that keeps R4's old checks as they are: valid, with a note
    old_r4 = _sub(NEW_MODULE, ('Scenario("the_sixth_note_is_refused", given=FIVE,',
                               'Scenario("the_fourth_note_is_refused", given=FIVE,'),
                  ('"notes_at_most_5", "A user has at most 5 notes.",\n'
                   "                  'count(Note, by=\"user_id\") <= 5'",
                   '"notes_at_most_3", "A user has at most 3 notes.",\n'
                   "                  'count(Note, by=\"user_id\") <= 3'"),
                  ("FIVE = {\"notes\": [{\"id\": 10 + i, \"user_id\": 1, \"text\": f\"n{i}\"} "
                   "for i in range(5)]}",
                   "FIVE = {\"notes\": [{\"id\": 10 + i, \"user_id\": 1, \"text\": f\"n{i}\"} "
                   "for i in range(3)]}"),
                  ('"count": 5}}', '"count": 3}}'))  # fmt: skip
    trace = {
        **NEW_TRACE,
        "links": {
            **NEW_TRACE["links"],
            "R4": ["scenario:the_fourth_note_is_refused", "invariant:notes_at_most_3"],
        },
    }
    result, _ = run_since(answer(old_r4, trace))
    assert result.valid, result.problems
    assert any("R4 changed (was R4) but none of its old checks changed" in n for n in result.notes)


def test_draft_since_rejects_a_base_that_does_not_import():
    with pytest.raises(ValueError, match="base module"):
        draft_since(NOTES_V1, NOTES_V2, base_module="SPEC = 1/0\n", base_trace=base_trace(),
                    generator=ScriptedGenerator([]), model="x")  # fmt: skip


def test_changed_targets_sees_a_modified_scenario():
    base, _ = load_spec(NOTES_BASE_MODULE)
    new, _ = load_spec(NOTES_BASE_MODULE.replace('json={"text": "tea"}', 'json={"text": "te"}'))
    assert changed_targets(base, new) == {
        "added": [],
        "removed": [],
        "modified": ["scenario:create_a_note"],
    }
    assert changed_targets(base, base) == {"added": [], "removed": [], "modified": []}


# ---- the CLI --------------------------------------------------------------------------------


def test_cli_since_writes_module_trace_and_record(tmp_path: Path, capsys):
    (tmp_path / "v1.md").write_text(NOTES_V1)
    (tmp_path / "v2.md").write_text(NOTES_V2)
    base = tmp_path / "spec_v1.py"
    base.write_text(NOTES_BASE_MODULE)
    base_trace().save(trace_path(base))
    out = tmp_path / "spec_v2.py"
    code = main(["draft", str(tmp_path / "v2.md"), "--since", str(tmp_path / "v1.md"),
                 "--base", str(base), "--out", str(out)],
                generator=ScriptedGenerator([answer()]))  # fmt: skip
    assert code == 0, capsys.readouterr()
    assert "edit_own_note" in out.read_text()
    trace = Trace.load(trace_path(out))
    assert trace.replaces == {"R4": "R4"} and "R6" in trace.retired
    record = json.loads(draft_json_path(out).read_text())
    assert record["mode"] == "since" and record["valid"]
    assert record["diff"]["changed"][0]["old"] == "R4"
    assert record["changed_targets"]["added"][0] == "invariant:notes_at_most_5"
    assert record["conflicts"] == []
    assert not out.with_name("spec_v2.impact.json").exists()
    assert "1 changed, 1 added, 1 removed" in capsys.readouterr().out


def test_cli_since_needs_base(tmp_path: Path):
    (tmp_path / "v2.md").write_text(NOTES_V2)
    code = main([str(tmp_path / "v2.md"), "--since", str(tmp_path / "v2.md"),
                 "--out", str(tmp_path / "o.py")], generator=ScriptedGenerator([]))  # fmt: skip
    assert code == 2


# ---- L011 -----------------------------------------------------------------------------------

ANN_NOTE = {"notes": [{"id": 10, "user_id": 1, "text": "milk"}]}


def _spec(*scenarios: Scenario):
    base, _ = load_spec(NOTES_BASE_MODULE)
    return dataclasses.replace(base, scenarios=scenarios)


def test_l011_fires_on_a_real_contradiction():
    spec = _spec(
        Scenario("ben_reads_404", given=ANN_NOTE, steps=(
            Step("GET", "/notes/10", "ben", expect_status=404),)),
        Scenario("ben_reads_403", given=dict(ANN_NOTE), steps=(
            Step("GET", "/notes/10", "ben", expect_status=403),)),
        Scenario("anon_post_401", steps=(
            Step("POST", "/notes", None, json={"text": "a"}, expect_status=401,
                 expect_db={"notes": {"count": 0}}),)),
        Scenario("anon_post_201", steps=(
            Step("POST", "/notes", None, json={"text": "b"}, expect_status=201),)),
    )  # fmt: skip
    found = conflicts(spec)
    assert [(c.scenario_a, c.scenario_b) for c in found] == [
        ("ben_reads_404", "ben_reads_403"),
        ("anon_post_401", "anon_post_201"),
    ]
    trace = Trace(
        "x",
        extract_rules("- a\n- b\n"),
        {"R1": ["scenario:ben_reads_404"], "R2": ["scenario:ben_reads_403"]},
    )
    l011 = [f for f in lint(spec, trace) if f.code == "L011"]
    assert len(l011) == 2
    f = l011[0]
    assert f.element == "scenario:ben_reads_404" and f.rule_id == "R1"
    assert "scenario:ben_reads_403" in f.message and "GET /notes/{} as ben" in f.message
    assert f.fix.startswith("Which holds?") and "404 (R1)" in f.fix and "403 (R2)" in f.fix


def test_l011_silent_after_a_state_change_and_on_other_setups():
    spec = _spec(
        Scenario("read_it", given=ANN_NOTE, steps=(
            Step("GET", "/notes/10", "ann", expect_status=200),)),
        # after a successful delete the same read answers 404: not a contradiction
        Scenario("delete_then_read", given=ANN_NOTE, steps=(
            Step("DELETE", "/notes/10", "ann", expect_status=204),
            Step("GET", "/notes/10", "ann", expect_status=404),)),
        # a different setup
        Scenario("read_nothing", steps=(Step("GET", "/notes/10", "ann", expect_status=404),)),
        # a different id under the same template
        Scenario("read_other", given=ANN_NOTE, steps=(
            Step("GET", "/notes/11", "ann", expect_status=404),)),
        # a different body deciding validation
        Scenario("bad_text", steps=(
            Step("POST", "/notes", "ann", json={"text": ""}, expect_status=422),)),
        Scenario("good_text", steps=(
            Step("POST", "/notes", "ann", json={"text": "x"}, expect_status=201),)),
        # the second step on a template in its scenario is not compared
        Scenario("twice", steps=(
            Step("POST", "/notes", "ben", json={"text": "x"}, expect_status=422),
            Step("POST", "/notes", "ben", json={"text": "x"}, expect_status=201),)),
    )  # fmt: skip
    assert conflicts(spec) == []


def test_l011_silent_on_the_base_specs():
    base, _ = load_spec(NOTES_BASE_MODULE)
    assert conflicts(base) == []
    from tests.spec.test_draft import MODULE

    example, _ = load_spec(MODULE)
    assert conflicts(example) == []


# ---- impact ---------------------------------------------------------------------------------


def _new_spec_and_trace():
    result, _ = run_since(answer())
    assert result.valid
    return result.spec, result.diff, result.trace


def _d(scenario: str) -> CheckFinding:
    return CheckFinding("D", "silent", f"scenario: {scenario}", "GET /x", f"{scenario} / step 1",
                        {"method": "GET", "path": "/x"})  # fmt: skip


def test_impact_labels():
    spec, diff, trace = _new_spec_and_trace()
    trace.links["R2"] = ["scenario:text_bounds", "scenario:the_sixth_note_is_refused"]
    report = Report("CD", findings=[
        _d("edit_own_note"),                    # R6 added -> not built yet
        _d("text_bounds"),                      # R2 unchanged -> regression
        _d("the_sixth_note_is_refused"),        # R4 changed + R2 unchanged -> contradiction
        CheckFinding("C", "silent", "A user has at most 5 notes.", details={
            "invariant": "notes_at_most_5"}),   # R4 changed -> not built yet
        _d("no_such_scenario"),                 # no rule -> unlinked
        CheckFinding("A", "silent", "PY001", code="PY001"),  # no target: left out
    ])  # fmt: skip
    items = label_impact(report, spec, diff, trace)
    got = {i.target: (i.label, i.rules) for i in items}
    assert got == {
        "scenario:the_sixth_note_is_refused": ("contradiction", ["R2", "R4"]),
        "scenario:text_bounds": ("regression", ["R2"]),
        "invariant:notes_at_most_5": ("not_built_yet", ["R4"]),
        "scenario:edit_own_note": ("not_built_yet", ["R6"]),
        "scenario:no_such_scenario": ("unlinked", []),
    }
    assert [i.label for i in items] == ["contradiction", "regression", "not_built_yet",
                                        "not_built_yet", "unlinked"]  # fmt: skip
    # a conflict pair makes both its targets contradictions
    pair = Conflict("text_bounds", 1, 422, "create_a_note", 1, 201, "POST /notes", "ann")
    items = label_impact(Report("D", findings=[_d("text_bounds")]), spec, diff, trace,
                         conflict_pairs=[pair])  # fmt: skip
    assert items[0].label == "contradiction"


# ---- impact labelling with base_spec: by what actually changed ------------------------------


def _access_specs(current_status: int, new_status: int) -> tuple[Any, Any]:
    """A scenario linked to both a changed and an unchanged rule: the base app still
    answers `current_status`, the change asks for `new_status` (the real case: an
    executor gets 403 cancelling, but the current app still answers 200)."""
    base_spec = _spec(
        Scenario("access__cancel__as_eve", steps=(
            Step("POST", "/jobs/1/cancel", "eve", expect_status=current_status),)),
    )  # fmt: skip
    new_spec = _spec(
        Scenario("access__cancel__as_eve", steps=(
            Step("POST", "/jobs/1/cancel", "eve", expect_status=new_status),)),
    )  # fmt: skip
    return base_spec, new_spec


def _access_diff_and_trace() -> tuple[RuleDiff, Trace]:
    r1 = Rule("R1", "An executor gets 403 cancelling a job.", "Access", 1)
    r2 = Rule("R2", "Roles are enforced on every endpoint.", "Access", 2)
    diff = RuleDiff([r1, r2], [r1, r2], unchanged=[("R2", "R2")], changed=[("R1", "R1")])
    trace = Trace("ACCESS.md", [r1, r2], links={
        "R1": ["scenario:access__cancel__as_eve"],
        "R2": ["scenario:access__cancel__as_eve"],
    })  # fmt: skip
    return diff, trace


def test_impact_not_built_yet_when_the_failing_step_itself_changed():
    """The real case: a scenario linked to both a changed rule (R1) and an unchanged one
    (R2) fails on the step the change touched -- not_built_yet, not contradiction."""
    base_spec, new_spec = _access_specs(current_status=200, new_status=403)
    diff, trace = _access_diff_and_trace()
    report = Report("D", findings=[_d("access__cancel__as_eve")])
    items = label_impact(report, new_spec, diff, trace, base_spec=base_spec)
    assert [(i.target, i.label) for i in items] == [
        ("scenario:access__cancel__as_eve", "not_built_yet")
    ]


def test_impact_label_fallback_without_base_spec_keeps_the_old_rule_based_logic():
    """The same real case, but without `base_spec`: the old purely rule-based logic
    applies (mixed changed/unchanged rules -> contradiction), unchanged behaviour."""
    base_spec, new_spec = _access_specs(current_status=200, new_status=403)
    diff, trace = _access_diff_and_trace()
    report = Report("D", findings=[_d("access__cancel__as_eve")])
    items = label_impact(report, new_spec, diff, trace)
    assert items[0].label == "contradiction"


def test_impact_regression_when_the_failing_step_is_unchanged():
    """Linked only to an unchanged rule and the failing step itself did not change:
    regression, same as the rule-based fallback."""
    base_spec, new_spec = _access_specs(current_status=200, new_status=200)
    r2 = Rule("R2", "Roles are enforced on every endpoint.", "Access", 1)
    diff = RuleDiff([r2], [r2], unchanged=[("R2", "R2")])
    trace = Trace("ACCESS.md", [r2], links={"R2": ["scenario:access__cancel__as_eve"]})
    report = Report("D", findings=[_d("access__cancel__as_eve")])
    items = label_impact(report, new_spec, diff, trace, base_spec=base_spec)
    assert items[0].label == "regression"


def test_impact_conflict_pair_stays_contradiction_even_if_the_step_changed():
    """An L011 conflict wins over the changed-step check: real conflicts stay
    contradictions whatever the base-spec comparison says."""
    base_spec, new_spec = _access_specs(current_status=200, new_status=403)
    diff, trace = _access_diff_and_trace()
    pair = Conflict("access__cancel__as_eve", 1, 403, "other_scenario", 1, 200,
                    "POST /jobs/{}/cancel", "eve")  # fmt: skip
    report = Report("D", findings=[_d("access__cancel__as_eve")])
    items = label_impact(report, new_spec, diff, trace, base_spec=base_spec,
                         conflict_pairs=[pair])  # fmt: skip
    assert items[0].label == "contradiction"


def test_impact_not_built_yet_for_a_new_invariant():
    """A C finding on an invariant that is new in the spec (not in the base) is
    not_built_yet even though no rule yet links to anything in the base trace."""
    base_spec = _spec()
    new_spec = dataclasses.replace(base_spec, invariants=(
        Invariant("notes_at_most_5", "A user has at most 5 notes.",
                  'count(Note, by="user_id") <= 5'),
    ))  # fmt: skip
    r1 = Rule("R1", "A user has at most 5 notes.", "Notes", 1)
    diff = RuleDiff([], [r1], added=["R1"])
    trace = Trace("NOTES.md", [r1], links={"R1": ["invariant:notes_at_most_5"]})
    report = Report("C", findings=[
        CheckFinding("C", "silent", "A user has at most 5 notes.",
                    details={"invariant": "notes_at_most_5"}),  # fmt: skip
    ])
    items = label_impact(report, new_spec, diff, trace, base_spec=base_spec)
    assert items[0].label == "not_built_yet"


def test_impact_runs_layers_d_and_c_through_check():
    spec, diff, trace = _new_spec_and_trace()
    seen = {}

    def fake_check(spec_, app_factory, db_factory, *, layers, **kw):
        seen.update(layers=layers, kw=kw)
        return Report(layers, findings=[_d("create_a_note")])

    items = impact(spec, diff, trace, app_factory=lambda p: None, db_factory=lambda p: p,
                   check=fake_check, login="L")  # fmt: skip
    assert seen == {"layers": "CD", "kw": {"login": "L"}}
    assert [(i.target, i.label) for i in items] == [("scenario:create_a_note", "regression")]
    impact(spec, diff, trace, app_factory=None, db_factory=None, check=fake_check, race=True)
    assert seen["layers"] == "CDX"


# ---- the change review ----------------------------------------------------------------------


def test_review_change_renders_only_the_change():
    base, _ = load_spec(NOTES_BASE_MODULE)
    spec, diff, trace = _new_spec_and_trace()
    conflict = {"code": "L011", "message": "scenario:a step 1 expects 404 but scenario:b",
                "fix": "Which holds? GET /notes/{} as ben answers 404, or 403?"}  # fmt: skip
    summary = "[D silent] PATCH /notes/10 as ann (edit_own_note / step 1)"
    items = [ImpactItem("scenario:edit_own_note", "not_built_yet", ["R6"], [{"summary": summary}]),
             ImpactItem("scenario:text_bounds", "regression", ["R2"], [])]  # fmt: skip
    lint_found = [conflict, {"code": "L006", "message": "other"}]
    md = review_change_markdown(base, spec, base_trace(), trace, diff, lint_found, items)
    assert md.startswith("# Change review\n")
    assert "- Rules: 1 added, 1 changed, 1 removed (4 unchanged)." in md
    assert "**R4 → R4**" in md
    assert "  - was: A user has at most 3 notes; a fourth answers 409." in md
    assert "  - now: A user has at most 5 notes; a sixth answers 409." in md
    assert "**R6**: PATCH /notes/{id}" in md and "`scenario:edit_own_note`" in md
    assert "**R6**: DELETE /notes/{id} deletes the caller's own note; it answers 204." in md
    assert "retired: the delete endpoint is dropped" in md
    assert "### Removed" in md and "DELETE /notes/10 as ann -> 204" in md
    assert "PATCH /notes/10 as ann -> 200" in md
    assert "**Which holds? GET /notes/{} as ben answers 404, or 403?**" in md
    assert "other" not in md  # L006 is review_markdown's
    assert "### Not built yet (1)" in md and "### Regression (1)" in md
    # unchanged rules and elements are not shown
    assert "A note's text is 1 to 200 characters" not in md
    assert "scenario:read_own_and_other" not in md
    md2 = review_change_markdown(base, spec, base_trace(), trace, diff, [conflict], items)
    assert md2 == review_change_markdown(base, spec, base_trace(), trace, diff, [conflict], items)
    assert "Impact" not in review_change_markdown(base, spec, base_trace(), trace, diff)


# ---- the live run ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("LATTICE_LIVE_LLM") != "1", reason="live LLM run: set LATTICE_LIVE_LLM=1"
)
def test_live_haiku_draft_since_validates(tmp_path: Path):
    from lattice.llm.cli_client import ClaudeCliClient

    gen = ClaudeCliClient(model="haiku", max_tokens=32000, effort="", timeout=900, full_text=True)
    result = draft_since(NOTES_V1, NOTES_V2, base_module=NOTES_BASE_MODULE,
                         base_trace=base_trace(), context_md=NOTES_CONTEXT, generator=gen,
                         model="haiku")  # fmt: skip
    record = {
        "valid": result.valid,
        "rounds": result.rounds,
        "problems": result.problems,
        "notes": result.notes,
        "usage": result.usage.as_dict(),
        "replaces": result.trace.replaces,
        "retired": result.trace.retired,
        "retired_targets": result.trace.retired_targets,
        "changed_targets": result.changed_targets,
        "conflicts": [c.__dict__ for c in conflicts(result.spec)] if result.spec else None,
    }
    out = Path(os.environ.get("LATTICE_LIVE_OUT", tmp_path)) / "incremental_live.json"
    out.write_text(json.dumps(record, indent=2) + "\n")
    (out.parent / "incremental_live_spec.py").write_text(result.module_source)
    print(json.dumps(record, indent=2))
    assert result.problems == [], result.problems
    assert result.trace.replaces.get("R4") == "R4"
    assert "R6" in result.trace.retired


def test_cli_since_impact_writes_the_impact_record(tmp_path: Path, monkeypatch):
    import lattice.spec.incremental as inc

    (tmp_path / "v1.md").write_text(NOTES_V1)
    (tmp_path / "v2.md").write_text(NOTES_V2)
    base = tmp_path / "spec_v1.py"
    base.write_text(NOTES_BASE_MODULE)
    base_trace().save(trace_path(base))
    seen = {}

    def fake_impact(spec, diff, trace, **kw):
        seen.update(kw)
        return [ImpactItem("scenario:edit_own_note", "not_built_yet", ["R6"], [])]

    monkeypatch.setattr(inc, "impact", fake_impact)
    out = tmp_path / "spec_v2.py"
    argv = [str(tmp_path / "v2.md"), "--since", str(tmp_path / "v1.md"), "--base", str(base),
            "--out", str(out), "--impact", "--app", "os:getcwd", "--db", "os:getcwd"]  # fmt: skip
    assert main(argv, generator=ScriptedGenerator([answer()])) == 0
    record = json.loads(out.with_name("spec_v2.impact.json").read_text())
    assert record["items"][0]["label"] == "not_built_yet"
    assert seen["database"] == "sqlite" and seen["race"] is False and seen["runtime"] is None


def test_l011_silent_on_a_public_login_answering_good_and_bad_credentials():
    spec = _spec(
        Scenario("login_ok", steps=(
            Step("POST", "/auth/login", None, json={"email": "a", "password": "good"},
                 expect_status=200),)),
        Scenario("login_bad", steps=(
            Step("POST", "/auth/login", None, json={"email": "a", "password": "bad"},
                 expect_status=401),)),
    )  # fmt: skip
    assert conflicts(spec) == []
