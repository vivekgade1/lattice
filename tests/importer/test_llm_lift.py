"""The close-the-gap loop (importer I5, spec §3.4) with a fake LLM -- no live calls.

The template's ``read_item`` is lifted by a lifter made subtly wrong (the owner check
becomes an ``owned`` filter: other users' items answer 404 where the original answers
403); the checker accepts it and verification refuses it, so it reaches the loop with
its unverified Lattice and the diff. The fake LLM answers with the correct endpoint (the
unpatched lifters' output) or the wrong one, per rung, and every rung is checked:

* R1 repairs the lifted Lattice in one conversation (up to 3 rounds);
* R2 re-lifts from scratch on the strong generator (fresh conversations, the diff
  history);
* R3 carves out (Lattice + a typed native over an original helper), or is skipped with
  the reason when there is no simple helper;
* R4 makes the route an island with a reason -- a wrong program is never emitted.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from bench.template_app import make_app
from bench.template_app.app import utils as template_utils
from lattice.importer.carve import SKIP_TODO, CarveNative, NativeCarveOut
from lattice.importer.lift import ImportResult, check_program, lift_repo
from lattice.importer.lifters import get_or_404, owner_check
from lattice.importer.lifters.base import Step
from lattice.importer.llm_lift import (
    CloseResult,
    CorpusLog,
    LoopConfig,
    close_gap,
    parse_candidate,
)
from lattice.llm.client import Generation, GenerationRefused, Usage

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "bench.template_app.app"
USAGE = Usage(1000, 400)  # claude-opus-5: $0.005 + $0.010 = $0.015 per call
CALL_COST = 0.015


def _lift(**kw: Any) -> ImportResult:
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    template_utils.OUTBOX.clear()
    return lift_repo(REPO_ROOT, PACKAGE, make_app, **kw)


def _patched(module: Any, change) -> Any:
    real = module.match

    def match(route, model):
        f = real(route, model)
        return None if f is None else change(f)

    return match


def _owned_filter(f):
    fetch, where, one = f.steps
    mine = Step("mine", "owned", (f"{{#{fetch.name}}}",), (("by", "user"),))
    where = dataclasses.replace(where, args=("{#mine}", *where.args[1:]))
    return dataclasses.replace(f, steps=(fetch, mine, where, one))


def _drop(f):
    return dataclasses.replace(f, steps=(), errors=())


def _buggy(only: set[str]) -> ImportResult:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(get_or_404, "match", _patched(get_or_404, _owned_filter))
        mp.setattr(owner_check, "match", _patched(owner_check, _drop))
        return _lift(only=only)


@pytest.fixture(scope="module")
def good() -> dict[str, str]:
    """The correct endpoints (the unpatched lifters' verified output)."""
    result = _lift(only={"read_item", "delete_item"})
    return {r.key: r.source for r in result.verified}  # type: ignore[misc]


@pytest.fixture(scope="module")
def buggy() -> ImportResult:
    result = _buggy({"read_item"})
    assert result.result("read_item").status == "lifted-unverified"
    return result


class FakeLLM:
    """Answers from a script: a string, or a callable (system, messages) -> str; an
    exception instance is raised. Every call is recorded."""

    def __init__(self, *script: Any, model: str = "claude-opus-5", stop: str = "end_turn"):
        self.script = list(script)
        self.model = model
        self.stop = stop
        self.calls: list[tuple[str, list[dict]]] = []

    def generate(self, system, messages):
        self.calls.append((system, [dict(m) for m in messages]))
        if not self.script:
            raise AssertionError("FakeLLM: script exhausted")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        text = item(system, messages) if callable(item) else item
        stop = self.stop
        if isinstance(text, tuple):
            text, stop = text
        return Generation(text, USAGE, 0.0, stop, model=self.model)


def answer(source: str) -> str:
    return f"Here it is.\n\n```python\n# lattice: v1\n{source}```\n"


def wrong(buggy: ImportResult, key: str = "read_item") -> str:
    source = buggy.result(key).source
    assert source is not None
    return answer(source)


def _run(result: ImportResult, gen: FakeLLM, tmp_path: Path, **kw: Any) -> CloseResult:
    config = kw.pop("config", LoopConfig(method="M1"))
    kw.setdefault("carve", NativeCarveOut())  # the I5 tests; the T2 carve-out has its own
    corpus = CorpusLog(tmp_path / "imports.jsonl", repo="template")
    return close_gap(result, gen, config=config, corpus=corpus, **kw)


def _emitted(close: CloseResult) -> set[str]:
    import ast

    return {
        n.name
        for src in close.programs.values()
        for n in ast.parse(src).body
        if isinstance(n, ast.FunctionDef)
    }


def _corpus(tmp_path: Path) -> list[dict]:
    return [json.loads(line) for line in (tmp_path / "imports.jsonl").read_text().splitlines()]


# ---- R1 --------------------------------------------------------------------------------------


def test_r1_repairs_the_lifted_lattice_from_the_verification_diff(buggy, good, tmp_path):
    gen = FakeLLM(answer(good["read_item"]))
    close = _run(buggy, gen, tmp_path)
    o = close.outcome("read_item")
    assert (o.status, o.rung, o.by) == ("verified", "R1", "llm")
    assert [a.outcome for a in o.attempts] == ["verified"]
    assert o.source == good["read_item"]
    assert _emitted(close) == {"read_item"}
    assert set(close.programs) == {"items"}
    # the prompt: the lifters' wrong Lattice, the diff (403 original, 404 lattice), M1 source
    ((system, messages),) = gen.calls
    prompt = messages[0]["content"]
    assert "owned(" in prompt and "403 != 404" in prompt
    assert "def read_item" in prompt and "Route source (S1)" in prompt
    assert "TABLE_ITEM" in system and "## Lifting a route of an imported app" in system
    # the emitted program checks against the emitted profile
    plans, errors = check_program(close.programs["items"], buggy.lattice_profile)
    assert plans is not None, errors
    assert close.cost_usd == pytest.approx(CALL_COST)


def test_r1_keeps_repairing_in_one_conversation_up_to_three_rounds(buggy, good, tmp_path):
    gen = FakeLLM(wrong(buggy), "no code at all", answer(good["read_item"]))
    close = _run(buggy, gen, tmp_path)
    o = close.outcome("read_item")
    assert (o.status, o.rung) == ("verified", "R1")
    assert [(a.rung, a.round, a.outcome) for a in o.attempts] == [
        ("R1", 1, "mismatch"),
        ("R1", 2, "no_code"),
        ("R1", 3, "verified"),
    ]
    # one conversation: round 3 sees both earlier answers and their feedback
    msgs = gen.calls[2][1]
    assert len(msgs) == 5 and msgs[1]["role"] == "assistant"
    assert "403 != 404" in msgs[2]["content"]
    assert "no ```python code block" in msgs[4]["content"]
    rounds = [r for r in _corpus(tmp_path) if r["type"] == "round"]
    assert [r["outcome"] for r in rounds] == ["mismatch", "no_code", "verified"]
    assert rounds[-1]["lattice"] == good["read_item"]


# ---- R2 --------------------------------------------------------------------------------------


def test_r2_relifts_from_scratch_on_the_strong_generator(buggy, good, tmp_path):
    weak = FakeLLM(wrong(buggy), wrong(buggy), wrong(buggy))
    strong = FakeLLM(wrong(buggy), answer(good["read_item"]), model="claude-opus-5-5")
    config = LoopConfig(method="M2", strong_model="claude-opus-5-5")
    close = _run(buggy, weak, tmp_path, strong_generator=strong, config=config)
    o = close.outcome("read_item")
    assert (o.status, o.rung) == ("verified", "R2")
    assert [(a.rung, a.round) for a in o.attempts] == [
        ("R1", 1), ("R1", 2), ("R1", 3), ("R2", 1), ("R2", 2),
    ]  # fmt: skip
    assert o.attempts[-1].model == "claude-opus-5-5"
    # fresh conversations with the diff history, without the lifters' partial output
    for _, messages in strong.calls:
        assert len(messages) == 1
        prompt = messages[0]["content"]
        assert "Earlier attempts" in prompt and "403 != 404" in prompt
        assert "## Lifter output" not in prompt
        assert "Code-graph slice (S2)" in prompt  # arm M2
    assert "Attempt R2 round 1" in strong.calls[1][1][0]["content"]
    assert _emitted(close) == {"read_item"}


# ---- R3 --------------------------------------------------------------------------------------


class OneHelper(NativeCarveOut):
    """A carve-out strategy offering one helper (the template's routes reach no simple
    one: its helpers are natives already, or take a session)."""

    def natives(self, graph, route, known):
        return (CarveNative("carve_title", f"{__name__}:shout", (("text", "STR"),), "STR"),), ""


def shout(text: str) -> str:
    return text.upper()


def test_r3_carves_out_with_a_typed_native(buggy, good, tmp_path):
    gen = FakeLLM(*[wrong(buggy)] * 5, answer(good["read_item"]))
    close = _run(buggy, gen, tmp_path, carve=OneHelper())
    o = close.outcome("read_item")
    assert (o.status, o.rung) == ("verified", "R3")
    assert [a.rung for a in o.attempts] == ["R1"] * 3 + ["R2"] * 2 + ["R3"]
    system, messages = gen.calls[-1]
    assert "native.carve_title(text: Str) -> Str" in messages[0]["content"]
    assert "natives['carve_title'] = NativeSig((Scalar.STR,), Scalar.STR)" in system
    assert o.natives == ()  # not called: not emitted
    assert "carve_title" not in close.profile_source
    assert _emitted(close) == {"read_item"}


def test_r3_emits_the_native_it_uses_into_the_profile(buggy, tmp_path):
    from lattice.importer.llm_lift import _join, load_profile_source

    o = _run(buggy, FakeLLM(*[wrong(buggy)] * 6), tmp_path, carve=OneHelper())
    assert o.outcome("read_item").rung == "R4"  # (the R3 answer was wrong too)
    native = CarveNative("carve_title", f"{__name__}:shout", (("text", "STR"),), "STR")
    out = o.outcome("read_item")
    out.status, out.rung, out.natives = "verified", "R3", (native,)
    out.source = buggy.result("read_item").source  # (checks; stands in for a verified one)
    _, profile_source, kept = _join(o.outcomes, buggy.profile.source, {"read_item": 0})
    assert kept == (native,)
    module = load_profile_source(profile_source)
    assert module.NATIVES["carve_title"]("abc") == "ABC"
    assert "carve_title" in module.profile().api.natives


def test_r3_is_skipped_with_the_reason_without_a_simple_helper(buggy, tmp_path):
    gen = FakeLLM(*[wrong(buggy)] * 5)
    close = _run(buggy, gen, tmp_path)
    o = close.outcome("read_item")
    assert len(gen.calls) == 5  # R1 x3, R2 x2; no R3 call
    skipped = [a for a in o.attempts if a.outcome == "skipped"]
    assert [a.rung for a in skipped] == ["R3"]
    assert SKIP_TODO in skipped[0].reason
    assert any(n.startswith("R3 skipped") for n in o.notes)


# ---- R4 and the soundness gate ---------------------------------------------------------------


def test_a_subtly_wrong_program_is_never_emitted(buggy, tmp_path):
    """The fake LLM answers every rung with the subtly wrong endpoint (the checker
    accepts it; other users' rows answer 404 instead of 403): the route is an island
    with the reason, never Lattice."""
    gen = FakeLLM(*[wrong(buggy)] * 5)
    close = _run(buggy, gen, tmp_path)
    o = close.outcome("read_item")
    assert (o.status, o.rung, o.by) == ("island", "R4", None)
    assert o.source is None and o.verified is None
    assert "verification failed" in o.reason and "other_user_row" in o.reason
    assert o.gap == "behaviour not reproduced"
    assert all(a.outcome in ("mismatch", "skipped") for a in o.attempts)
    assert close.programs == {} and _emitted(close) == set()
    route = [r for r in _corpus(tmp_path) if r["type"] == "route"]
    assert route[0]["status"] == "island" and route[0]["lattice"] is None


def test_candidates_that_are_not_one_endpoint_for_the_route_are_refused(buggy, good, tmp_path):
    extra = (
        good["read_item"]
        + '\n\n@endpoint("GET", "/api/v1/other", auth=Public)\n'
        + ("def other() -> One:\n    return fetch(Item)\n")
    )
    renamed = good["read_item"].replace("def read_item", "def fetch_item")
    moved = good["read_item"].replace('"/api/v1/items/{id}"', '"/api/v1/things/{id}"')
    sneaky = "import os\n" + good["read_item"]
    gen = FakeLLM(*(answer(s) for s in (extra, renamed, moved, sneaky)), "")
    close = _run(buggy, gen, tmp_path)
    o = close.outcome("read_item")
    assert o.status == "island" and _emitted(close) == set()
    assert [a.outcome for a in o.attempts if a.outcome != "skipped"] == ["invalid"] * 4 + [
        "no_code"
    ]
    reasons = [a.reason for a in o.attempts]
    assert "exactly one @endpoint, found 2" in reasons[0]
    assert "must be named `read_item`" in reasons[1]
    assert '@endpoint("GET", "/api/v1/items/{id}", ...)' in reasons[2]
    assert "Import is not allowed" in reasons[3]


def test_truncated_and_refused_answers_are_never_built(buggy, good, tmp_path):
    gen = FakeLLM(
        (answer(good["read_item"]), "max_tokens"),  # cut off: not built even though correct
        GenerationRefused("no", usage=USAGE, model="claude-opus-5"),
    )
    strong = FakeLLM(answer(good["read_item"]))
    close = _run(buggy, gen, tmp_path, strong_generator=strong)
    o = close.outcome("read_item")
    assert [(a.rung, a.outcome) for a in o.attempts] == [
        ("R1", "truncated"),
        ("R1", "refused"),
        ("R2", "verified"),
    ]
    assert o.attempts[0].source is None
    assert close.cost_usd == pytest.approx(3 * CALL_COST)  # the refused call is charged


def test_parse_candidate_strips_the_header():
    src = '@endpoint("GET", "/x", auth=Public)\ndef x() -> One:\n    return fetch(T)\n'
    got, problem = parse_candidate(answer(src), "x", "GET", "/x")
    assert problem == "" and got == src


def test_a_route_without_a_request_suite_is_an_island_without_llm_calls(buggy, tmp_path):
    ctx = buggy.context
    assert ctx is not None
    spec = ctx.inputs.routes.pop("read_item")
    ctx.inputs.skipped["read_item"] = "an unsupported body"
    try:
        gen = FakeLLM()
        close = _run(buggy, gen, tmp_path)
    finally:
        ctx.inputs.routes["read_item"] = spec
        del ctx.inputs.skipped["read_item"]
    o = close.outcome("read_item")
    assert gen.calls == [] and o.status == "island"
    assert o.reason == "no request suite: an unsupported body"
    assert o.gap == "unverifiable: no request suite"


# ---- budgets ---------------------------------------------------------------------------------


def test_the_route_round_cap_makes_an_island_with_reason_budget(buggy, tmp_path):
    gen = FakeLLM(*[wrong(buggy)] * 2)
    close = _run(buggy, gen, tmp_path, config=LoopConfig(method="M1", max_route_rounds=2))
    o = close.outcome("read_item")
    assert len(gen.calls) == 2 and o.status == "island"
    assert o.reason.startswith("budget: the route's round cap (2)")
    assert o.gap == "budget"


@pytest.fixture(scope="module")
def two_broken() -> ImportResult:
    """read_items verified by the lifters; read_item and delete_item lifted wrong."""
    result = _buggy({"read_items", "read_item", "delete_item"})
    assert [r.key for r in result.needs_llm] == ["read_item", "delete_item"]
    return result


def test_repo_cost_exhaustion_leaves_a_consistent_import(two_broken, tmp_path):
    from lattice.importer.report import load_report, write_import

    gen = FakeLLM(*[wrong(two_broken)] * 2)
    config = LoopConfig(method="M1", max_repo_cost_usd=2 * CALL_COST - 0.001)
    close = _run(two_broken, gen, tmp_path, config=config)
    assert len(gen.calls) == 2  # the cap is reached after the second call
    assert close.budget is not None and close.budget.exhausted_by is not None
    # each route gets its share of the repo budget (one call each), cheapest first
    first, second = close.outcome("read_item"), close.outcome("delete_item")
    assert first.status == second.status == "island"
    assert len(first.attempts) == len(second.attempts) == 1
    reasons = sorted(o.reason for o in (first, second))
    assert reasons[0].startswith("budget: the repo's cost cap")
    assert reasons[1].startswith("budget: the route's share of the repo budget")
    assert close.outcome("read_items").rung == "R0"

    out = write_import(close, tmp_path / "lattice_import", repo_root=REPO_ROOT, package=PACKAGE)
    report = load_report(out)
    assert report["budget"]["exhausted"] is True
    assert [r["key"] for r in report["routes"]] == ["read_items", "read_item", "delete_item"]
    assert {r["key"]: r["status"] for r in report["routes"]} == {
        "read_items": "verified",
        "read_item": "island",
        "delete_item": "island",
    }
    assert report["coverage"]["R4"] == 2 and report["coverage"]["R0"] == 1
    # the files agree with the report: only verified routes are Lattice
    assert sorted(p.name for p in (out / "routes").iterdir()) == ["items.lattice.py"]
    items = (out / "routes" / "items.lattice.py").read_text()
    assert "def read_items" in items and "def read_item(" not in items
    assert "island_read_item" in (out / "natives.py").read_text()
    assert "budget" in (out / "report.md").read_text()


def test_the_loop_does_not_modify_the_import(buggy, good, tmp_path):
    before = (buggy.result("read_item").status, buggy.result("read_item").source)
    _run(buggy, FakeLLM(answer(good["read_item"])), tmp_path)
    assert (buggy.result("read_item").status, buggy.result("read_item").source) == before
    assert buggy.programs == {}


def test_artefacts_are_saved(buggy, good, tmp_path):
    close = _run(buggy, FakeLLM(answer(good["read_item"])), tmp_path, artefacts=tmp_path / "a")
    assert close.outcome("read_item").rung == "R1"
    d = tmp_path / "a" / "read_item" / "R1-repair-1"
    assert {p.name for p in d.iterdir()} == {
        "prompt.md",
        "response.md",
        "candidate.lattice.py",
        "round.json",
    }
