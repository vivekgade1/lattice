"""Context assembly per method arm (importer I5, spec §3.6): each arm M1-M6 includes its
evidence layers and nothing else; the base context (route identity, lifter output, diff
history) is in every arm. Built on the template's ``read_item`` with a wrong lifter (so
there is lifter output and a diff); S3 notes come from a fake LLM."""

from __future__ import annotations

import pytest

from lattice.importer.context import LAYERS, METHODS, TITLES, Evidence, assemble
from lattice.importer.intent import CODE_WINS, IntentNotes
from lattice.importer.llm_lift import CloseResult, LoopConfig, close_gap
from lattice.llm.client import Generation, Usage
from tests.importer.test_llm_lift import REPO_ROOT, FakeLLM, _buggy, answer

NOTES = "Items belong to their owner; admins see every item.\n\n" + (
    '```json\n{"routes": {"read_item": {"auth": "Public", "statuses": [200, 404]}}}\n```'
)

# a marker per layer that only that layer's section holds
MARKERS = {
    "S1": "def read_item(",  # the route's own source
    "S2": "# bench.template_app.app.api.deps:get_current_user",  # the slice's callees
    "S3": "Items belong to their owner",
    "S4": "- GET /api/v1/items/{id}, secured: True",
    "S5": "Table `item`:",
    "S6": "SELECT",
}


class NotesLLM:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, system, messages):
        self.calls += 1
        return Generation(NOTES, Usage(2000, 300), 0.0, "end_turn", model="claude-opus-5")


@pytest.fixture(scope="module")
def buggy():
    return _buggy({"read_item"})


@pytest.fixture(scope="module")
def evidence(buggy, tmp_path_factory):
    notes = IntentNotes(
        REPO_ROOT,
        buggy.model.app_package,
        NotesLLM(),
        cache_dir=tmp_path_factory.mktemp("notes"),
        commit="test",
    )
    ev = Evidence(buggy, intent=notes)
    yield ev
    ev.close()


@pytest.mark.parametrize("method", sorted(METHODS))
def test_each_arm_holds_exactly_its_layers(evidence, buggy, method):
    r = buggy.result("read_item")
    ctx = assemble(evidence, r, method, history=["### Attempt R1 round 1: mismatch\n..."])
    assert ctx.layers == METHODS[method]
    text = ctx.render()
    for layer in LAYERS:
        present = f"## {TITLES[layer]}" in text
        assert present == (layer in METHODS[method]), (method, layer)
        if layer in METHODS[method]:
            assert MARKERS[layer] in ctx.section(TITLES[layer]).body, (method, layer)
    # the base context, in every arm
    assert "## Route" in text and "`read_item`: GET /api/v1/items/{id}" in text
    assert "## Lifter output" in text and "owned(" in text
    assert "## Earlier attempts" in text


@pytest.mark.parametrize("method", ["M3", "M5"])
def test_arms_without_source_show_no_code(evidence, buggy, method):
    text = assemble(evidence, buggy.result("read_item"), method, include_partial=False).render()
    assert "def read_item(" not in text and "session.get(" not in text
    assert "def get_current_user" not in text


def test_the_notes_carry_the_code_wins_rule(evidence, buggy):
    body = evidence.layer("S3", buggy.result("read_item"))
    assert CODE_WINS in body


def test_the_s6_trace_is_lifted_where_it_can_be(evidence, buggy):
    body = evidence.layer("S6", buggy.result("read_item"))
    assert "read_item.happy" in body or "-> 200" in body
    assert "-- lifts to: fetch(Item)" in body


def test_unknown_arm_is_refused(evidence, buggy):
    with pytest.raises(ValueError, match="unknown method arm"):
        assemble(evidence, buggy.result("read_item"), "M7")


def test_the_loop_uses_the_arm_and_records_notes_disagreements(buggy, tmp_path):
    """M4 through the loop: the notes are generated once (charged to the budget) and
    their claims are compared with the code (auth) and the verified behaviour."""
    notes_llm = NotesLLM()
    notes = IntentNotes(REPO_ROOT, buggy.model.app_package, notes_llm,
                        cache_dir=tmp_path / "notes", commit="test")  # fmt: skip
    correct = _correct_read_item()
    gen = FakeLLM(answer(correct))
    close: CloseResult = close_gap(buggy, gen, config=LoopConfig(method="M4"), intent=notes)
    assert close.outcome("read_item").rung == "R1"
    assert notes_llm.calls == 1
    prompt = gen.calls[0][1][0]["content"]
    assert "## Intent notes (S3)" in prompt and CODE_WINS in prompt
    assert close.notes_cost_usd > 0 and close.budget is not None
    assert close.budget.cost_usd == pytest.approx(close.cost_usd)
    found = {(d.route, d.field): d for d in close.disagreements}
    assert found[("read_item", "auth")].behaviour == "Customer"
    assert found[("read_item", "statuses")].notes == "200, 404"
    assert "403" in found[("read_item", "statuses")].behaviour


def _correct_read_item() -> str:
    from tests.importer.test_llm_lift import _lift

    source = _lift(only={"read_item"}).result("read_item").source
    assert source is not None
    return source
