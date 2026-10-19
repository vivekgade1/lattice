"""The map router of the chat (lattice/spec/chat_route.py): the six shapes (MR1-MR6) on a synthetic
app map, what it must not route, the conflicts between shapes, the Markdown of the impact router's
answers, and that a routed answer passes the claim checker unchanged (probes PR2, PC2)."""

# ruff: noqa: E501 -- expected outputs are data lines

from __future__ import annotations

import pytest

from lattice.spec import chat_route as CR
from lattice.spec import claim_check as cc
from lattice.spec import impact_ask
from tests.spec import chat_world as W

AM = W.make_map()
SOURCE = (
    "Source: the app map (static facts from the code and recorded statuses from the capture); "
    "whether anything fails after a change is not decided."
)


def route(q: str) -> CR.Routed | None:
    return CR.route(q, appmap=AM)


def test_mr1_built_and_not_built() -> None:
    r = route("Is GET /v1/export built?")
    assert r is not None and r.shape == "MR1" and r.kind == "no"
    assert (
        r.answer == "`GET /v1/export` is not built: it is in the spec, not in the code (spec_only)."
    )
    assert "Rules that need it and are required_not_implemented (1): R4." in r.explanation
    r = route("Is POST /v1/tickets implemented?")
    assert r is not None and r.kind == "yes"
    assert (
        r.answer
        == "`POST /v1/tickets` is implemented: it is in the spec and in the code (implemented)."
    )
    assert "Code: code:tickets.go:12, code:tickets.go:30." in r.explanation
    r = route("Does GET /v1/nothing exist in the code?")
    assert r is not None and r.kind == "no" and "neither in the spec nor in the code" in r.answer


def test_mr1_lists_the_endpoints_that_are_only_in_the_spec() -> None:
    r = route("Which endpoints are in the spec but not in the code?")
    assert r is not None and r.shape == "MR1"
    assert r.answer == (
        "1 endpoints are in the spec and not in the code (spec_only):\n- `GET /v1/export`"
    )
    assert route("Which endpoints are in the spec?") is None


def test_mr2_workflows_by_route_and_status() -> None:
    r = route("Which workflows call POST /v1/tickets?")
    assert r is not None and r.shape == "MR2"
    assert r.rows["keys"] == [W.WF_FAILED, W.WF_OK]
    r = route("Which verified workflows use POST /v1/tickets?")
    assert r is not None and r.rows == {
        "keys": [W.WF_OK],
        "route": "POST /v1/tickets",
        "status": "verified",
    }
    r = route("Which workflows failed?")
    assert r is not None and r.rows["keys"] == [W.WF_FAILED]
    assert "step 1 (GET /v1/tickets/{}) answered 404, expected 200" in r.answer
    r = route("Which workflows are unverified?")
    assert r is not None and r.rows["keys"] == [W.WF_UNVER] and "answered 401" in r.answer
    r = route("Which workflows passed when replayed and use POST /v1/tickets?")
    assert r is not None and r.rows["keys"] == [W.WF_OK]


def test_mr3_rules_by_status() -> None:
    r = route("Which rules are required by the spec but not built?")
    assert r is not None and r.shape == "MR3"
    assert r.answer == (
        "2 rule(s) have impl_status `required_not_implemented` (required by the spec and not "
        "built): R4, R6"
    )
    assert route("Which rules are implemented but not verified?").rows["keys"] == [
        "rule:R2",
        "rule:R3",
    ]
    assert route("Which rules have no element at all?").rows["keys"] == ["rule:R5"]
    assert route("Which rules are verified?").rows["keys"] == ["rule:R1"]
    # a rule that is named, a why, or no status phrase is not a list
    for q in ("Is rule R2 verified?", "Why are rules not built?", "Which rules cover tickets?"):
        assert route(q) is None, q


def test_mr4_a_lifecycle() -> None:
    r = route("What is the jobs delivery lifecycle?")
    assert r is not None and r.shape == "MR4"
    assert r.answer.split("\n")[0].startswith(
        "Lifecycle `impl:machine:jobs.delivery` (basis: static"
    )
    for part in ("Spec target: `machine:Job.delivery`.", "Rules: R2.", "Code: code:jobs.go:10, code:jobs.go:20.",
                 "Basis: static (read from the code, not observed)."):  # fmt: skip
        assert part in r.answer
    assert route("Is machine:Job.delivery observed?") is not None
    assert route("What states can a job be in?") is None, "a state word alone names no machine"


def test_mr5_an_access_cell() -> None:
    r = route("What does the spec expect for ann on POST /v1/tickets, and what was observed?")
    assert r is not None and r.shape == "MR5"
    assert r.answer == (
        "Access cell `ann | POST /v1/tickets` (cell:ann|POST /v1/tickets):\n"
        "- The spec expects 201/401.\n"
        "- Code: the route is implemented (implemented); auth Customer; refusals 401 "
        "not_authenticated, 422 validation_failed.\n"
        "- Observed at capture by verified workflows: status 201 in wf-0c6bdd7ab0 step 0."
    )
    r = route("What does the spec expect for ben on GET /v1/export?")
    assert r is not None and "it is not built (spec_only)" in r.answer
    r = route("What does the spec say for an anonymous caller on POST /v1/tickets?")
    assert r is not None and r.rows["keys"] == ["cell:no token|POST /v1/tickets"]
    r = route("What does the spec say for ann on GET /health?")
    assert (
        r is not None
        and "(not specified)" in r.answer
        and "No verified workflow observed it" in r.answer
    )
    # a caller and a route without an access word is not an access question
    assert route("What does POST /v1/tickets return for ann?") is None


def test_mr5_beats_mr1_and_mr2_when_a_caller_is_named() -> None:
    r = route("Is POST /v1/tickets available to ann, and what did a workflow observe?")
    assert r is not None and r.shape == "MR5"


def test_two_shapes_that_disagree_are_not_routed() -> None:
    assert route("Which workflows call POST /v1/tickets, and which rules are not built?") is None
    assert route("Is POST /v1/tickets built, and which workflows failed?") is None


def test_mr6_the_broad_hypothetical_and_what_it_must_not_catch() -> None:
    r = route("What would we need to touch if we remove GET /v1/export?")
    assert (
        r is not None
        and r.shape == "MR6"
        and r.kind == "not_specified"
        and r.route == "hypothetical"
    )
    assert r.answer == impact_ask.NO_PREDICTION
    assert "`impl:route:GET /v1/export` touches cells:" in r.explanation
    assert route("What if we raised the limit?") is not None
    for q in (
        "What if a user sends a check-in for tomorrow?",
        "What if I delete my account?",
        "If I register twice, what status do I get?",
        "What happens when a check-in fails validation?",
        "What does GET /v1/tickets/{id} return?",
        "What would c1 do to the app?",
    ):
        assert route(q) is None, q


def test_a_change_id_is_never_routed_here() -> None:
    assert route("What if we remove GET /v1/export in c2?") is None
    assert route("Is c1 built?") is None


def test_no_map_means_no_map_shapes() -> None:
    assert CR.route("Is GET /v1/export built?", appmap=None) is None
    assert CR.route("What if we remove GET /v1/export?", appmap=None) is not None


def test_the_ask_answer_shape() -> None:
    a = route("Which rules are required by the spec but not built?").to_ask()
    assert a["usage"] == {"seconds": 0, "tokens": 0}
    assert a["kind"] == "value" and a["citations_checked"] is True and a["verified"] is None
    assert a["map"]["schema"] == "lattice.chatroute/1" and a["map"]["shape"] == "MR3"
    assert all(set(c) == {"target", "step", "quote"} for c in a["citations"])
    h = route("What if we raised the limit?").to_ask()
    assert h["impact"]["touches"] == {} and h["impact"]["basis"] == "static"


QUESTIONS = [
    "Is GET /v1/export built?", "Is POST /v1/tickets implemented?",
    "Which endpoints are in the spec but not in the code?", "Which workflows call POST /v1/tickets?",
    "Which workflows failed?", "Which workflows are unverified?",
    "Which rules are required by the spec but not built?", "Which rules have no element at all?",
    "What is the jobs delivery lifecycle?",
    "What does the spec expect for ann on POST /v1/tickets, and what was observed?",
    "What does the spec expect for ben on GET /v1/export?",
    "What would we need to touch if we remove GET /v1/export?", "What if we raised the limit?",
]  # fmt: skip


@pytest.mark.parametrize("q", QUESTIONS)
def test_a_routed_answer_passes_the_checker_unchanged(q: str) -> None:
    r = route(q)
    assert r is not None
    res = cc.check_text(r.markdown, W.ctx(q))
    assert (
        res.text == r.markdown
        and res.canonical() == cc.check_text(r.markdown, W.ctx(q)).canonical()
    )
    assert cc.scorer_claims(r.markdown, set()) == []


def test_the_impact_routers_answers_as_markdown() -> None:
    ans = impact_ask.route("What breaks if c1 is applied?", lookup=W.lookup, appmap=lambda: AM)
    assert ans is not None and CR.routed_kind(ans) == "impact"
    md = CR.answer_markdown(ans)
    assert md.startswith("Lattice record (c1): Tier 1 (observed): 1 failing;")
    for iid in (W.T1, W.T2, W.T2B):
        assert iid in md
    assert W.T3 not in md
    res = cc.check_text(md, W.ctx("What breaks if c1 is applied?"))
    assert res.text == md
    miss = impact_ask.route("What breaks if c9 is applied?", lookup=W.lookup, appmap=lambda: AM)
    assert miss is not None and CR.answer_markdown(miss).startswith(
        "Lattice has no impact report for c9: there is no such change. Nothing is decided."
    )
    hyp = impact_ask.route("What would break if we raised the limit?", lookup=W.lookup,
                           appmap=lambda: AM)  # fmt: skip
    assert hyp is not None and CR.routed_kind(hyp) == "hypothetical"
    assert CR.answer_markdown(hyp).startswith("Lattice does not predict failures.")
