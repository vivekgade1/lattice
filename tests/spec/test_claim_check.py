"""The claim checker (lattice/spec/claim_check.py, `lattice.chatcheck/1`): the dispositions D0-D7,
the enforcement E1-E3, the pre-pass E2, the fixed-point and idempotence properties, and the
equality of its unit and claim rule with the frozen round-1 scorer (probes PC1, PC2, PC4 of
docs/results/2026-10-18-goto-chat-round2-prereg.md). Every case asserts the exact output bytes."""

# ruff: noqa: E501 -- expected outputs are data lines

from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

from lattice.spec import claim_check as cc
from tests.spec import chat_world as W

ROOT = Path(__file__).resolve().parents[2]
SCORER = ROOT / "docs/results/data/2026-10-18-goto-chat-round2/frozen-r1/goto_score.py"


def _scorer():
    spec = importlib.util.spec_from_file_location("goto_score_frozen", SCORER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["goto_score_frozen"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


GS = _scorer()
FOOT1 = (
    "Lattice withheld 1 sentence(s) that stated a failure without a tier 1 or tier 2 item id. "
    "Lattice does not predict failures; it decides them only for a drafted change."
)
FOOT_ID1 = "Lattice also withheld 1 sentence(s) that named an id Lattice did not mint."
REC_C1 = (
    "Lattice record (c1): Tier 1 (observed): 1 failing; Tier 2 (determined): 2; Tier 3 (static, "
    "may fail): 1 locations, 1 workflows; Tier 4: not run.\n"
    "- `t1-aaaaaaaaaa` (tier 1, observed, c1): rule R3 case 51 answered 201\n"
    "- `t2-bbbbbbbbbb` (tier 2, determined, c1): scenario:ticket_limit fails at step 2\n"
    "- `t2-cccccccccc` (tier 2, determined, c1): flow:wf-0c6bdd7ab0 fails at step 0 after the change"
)
ITEM_T2 = "`t2-bbbbbbbbbb` (tier 2, determined, c1): scenario:ticket_limit fails at step 2"
ITEM_T2B = (
    "`t2-cccccccccc` (tier 2, determined, c1): flow:wf-0c6bdd7ab0 fails at step 0 after the change"
)
T3_A = (
    "`t3-dddddddddd` (tier 3, static: may fail; not a claim, c1): invariant:at_most_50_tickets: "
    "route on a changed table (tickets.go:12)"
)
T3_B = (
    "`t3-eeeeeeeeee` (tier 3, static: may fail; not a claim, c1): flow:wf-053146a70c (a workflow on "
    "POST /v1/tickets)"
)
LIFECYCLE = (
    "Lifecycle `impl:machine:jobs.delivery` (basis: static; its states are code facts; whether "
    "anything fails is not decided): states failed, pending, sent, edges (2) pending -> failed "
    "(spec, code), pending -> sent (spec, code)"
)
NO_PRED = (
    "Lattice does not predict failures. It decides them only for a drafted change (tier 1 observed, "
    "tier 2 determined). Draft this change, then ask about its id."
)
ELEMENT = (
    "Whether `{label}` fails is not decided: Lattice decides failures only for a drafted change "
    "(tier 1 observed, tier 2 determined). {tail}"
)


def check(text: str, question: str = "") -> cc.CheckResult:
    return cc.check_text(text, W.ctx(question))


# (id, question, text, exact output)
CASES = [
    # D0: a tier 1 or tier 2 id of a relevant report
    (
        "D0a",
        "",
        f"c1: {W.T1} fails. {W.T2} fails. {W.T2B} fails.",
        f"c1: {W.T1} fails. {W.T2} fails. {W.T2B} fails.",
    ),
    ("D0b", "", f"The check {W.T1} failed on c1.", f"The check {W.T1} failed on c1."),
    (
        "D0c",
        "",
        f"Observed: {W.T2B} - a workflow breaks (c1).",
        f"Observed: {W.T2B} - a workflow breaks (c1).",
    ),
    # D1: an owner label (may, might and could are not labels)
    ("D1a", "", "This is static, so it may fail.", "This is static, so it may fail."),
    ("D1b", "", "Whether R3 fails is not decided.", "Whether R3 fails is not decided."),
    (
        "D1c",
        "",
        "The replay is undecidable here, it could break.",
        "The replay is undecidable here, it could break.",
    ),
    ("D1d", "", "It fails, but tier 1 was not run.", "It fails, but tier 1 was not run."),
    # D2: the exact target of exactly one relevant tier 1 or tier 2 item
    (
        "D2a",
        "What breaks if c1 is applied?",
        "scenario:ticket_limit fails when the limit changes.",
        f"{ITEM_T2}\n\n{REC_C1}",
    ),
    ("D2b", "", "In c1, flow:wf-0c6bdd7ab0 breaks.", ITEM_T2B),
    (
        "D2c",
        "",
        "c3: scenario:other would fail.",
        "`t2-1111111111` (tier 2, determined, c3): scenario:other fails at step 1",
    ),
    # D3: a tier 3 id or target, or the words "tier 3"
    ("D3a", "", f"c1 has {W.T3} which fails.", T3_A),
    (
        "D3b",
        "",
        "The tier 3 items will break.",
        "Tier 3 items are static: they may fail and decide nothing.",
    ),
    ("D3c", "", "invariant:at_most_50_tickets breaks in c1.", T3_A),
    ("D3d", "", f"c1: {W.T3} and {W.T3B} fail.", f"{T3_A}\n{T3_B}"),
    # D4: spec text of a rule the unit names, or a lifecycle's states and edges
    (
        "D4a",
        "",
        "R2 says a check-in that fails validation is refused.",
        'R2 (spec text, quoted; whether anything fails at run time is not decided here): "A check-in '
        'that fails validation is refused with 422 and writes nothing."',
    ),
    ("D4b", "", "States are pending, sent, failed.", LIFECYCLE),
    ("D4c", "", "The lifecycle jobs.delivery goes pending -> failed.", LIFECYCLE),
    # D5: a workflow of the map: its recorded replay status at capture time
    (
        "D5a",
        "",
        f"{W.WF_FAILED} failed.",
        "Workflow `wf-053146a70c` (create ticket > read ticket): recorded replay status failed at "
        "capture time, step 1 (GET /v1/tickets/{}) answered 404, expected 200; what any change does "
        "to it is not decided.",
    ),
    (
        "D5b",
        "",
        f"{W.WF_UNVER} is unverified because replay failed at step 0.",
        "Workflow `wf-0da61e11ce` (login): recorded replay status unverified at capture time, step 0 "
        "(POST /v1/login) answered 401, expected 200; what any change does to it is not decided.",
    ),
    (
        "D5c",
        "",
        f"The replay of {W.WF_OK} broke.",
        "Workflow `wf-0c6bdd7ab0` (create ticket): recorded replay status verified at capture time; "
        "what any change does to it is not decided.",
    ),
    # D6: a named element: its static neighbourhood from the map
    (
        "D6a",
        "",
        "GET /v1/tickets/{id} will fail.",
        ELEMENT.format(
            label="GET /v1/tickets/{}", tail="Static: it touches `rule:R2`, `wf-053146a70c` (2)."
        ),
    ),
    (
        "D6b",
        "",
        "R3 will break.",
        ELEMENT.format(
            label="rule:R3", tail="Static: the app map shows nothing that references it."
        ),
    ),
    (
        "D6c",
        "",
        "c9 breaks everything.",
        ELEMENT.format(label="c9", tail="Static: the app map has no such element."),
    ),
    # D7: anything else is withheld (negations, speculation, echoes)
    ("D7a", "", "Everything will break.", FOOT1),
    ("D7b", "", "It could fail, maybe.", FOOT1),
    ("D7c", "", "Nothing fails here.", FOOT1),
    # E1: the Lattice record, or the fixed sentence for a change without a report
    ("E1a", "What breaks if c1 is applied?", "Several things.", f"Several things.\n\n{REC_C1}"),
    ("E1b", "What breaks if c2 is applied?", "Several things.", "Several things."),
    (
        "E1c",
        "Tell me about c9.",
        "It is new.",
        "It is new.\n\nLattice has no impact report for c9: there is no such change. Nothing is decided. Whether anything fails is not decided.",
    ),
    (
        "E1d",
        "What breaks if c1 is applied?",
        f"{W.T1}, {W.T2} and {W.T2B} are the items.",
        f"{W.T1}, {W.T2} and {W.T2B} are the items.",
    ),
    # E2: an id Lattice did not mint withholds its unit
    ("E2a", "", "t2-ffffffffff fails.", FOOT_ID1),
    ("E2b", "", "wf-ffffffffff is great.", FOOT_ID1),
    ("E2c", "", "R999 is a rule.", FOOT_ID1),
    # E3: a hypothetical that names no change
    (
        "E3a",
        "What would break if we raised the limit?",
        "I cannot say.",
        f"I cannot say.\n\n{NO_PRED}",
    ),
    (
        "E3b",
        "What would break if we raised the limit?",
        "Lattice does not predict failures. Draft a change.",
        "Lattice does not predict failures. Draft a change.",
    ),
    ("E3c", "What if we remove GET /v1/export?", "Probably fine.", f"Probably fine.\n\n{NO_PRED}"),
    # a flagged table row is removed and the footer says so
    (
        "T1",
        "",
        "| name | note |\n|---|---|\n| a | this fails |\n| b | fine |",
        f"| name | note |\n|---|---|\n| b | fine |\n\n{FOOT1}",
    ),
]


@pytest.mark.parametrize(("cid", "question", "text", "expected"), CASES, ids=[c[0] for c in CASES])
def test_the_exact_output(cid: str, question: str, text: str, expected: str) -> None:
    assert check(text, question).text == expected


@pytest.mark.parametrize(("cid", "question", "text", "expected"), CASES, ids=[c[0] for c in CASES])
def test_idempotent_and_no_claim_left(cid: str, question: str, text: str, expected: str) -> None:
    res = check(text, question)
    again = check(res.text, question)
    assert again.text == res.text, "check(check(x)) == check(x)"
    relevant = W.ctx(question).support_ids(res.text)
    assert GS.failure_claims(res.text, relevant) == [], "PC2: the frozen scorer counts 0 claims"


def test_a_replacement_is_never_the_models_sentence_with_a_label() -> None:
    """CM6: label laundering. The model's own words do not survive a replacement."""
    out = check("GET /v1/tickets/{id} will fail under load.").text
    assert "under load" not in out and "will fail" not in out
    out = check(f"{W.WF_FAILED} failed badly, sadly.").text
    assert "badly" not in out and "sadly" not in out


def test_a_synonym_is_not_a_way_around_the_rule() -> None:
    """CM19: the checker never rewrites a failure word; a sentence it cannot support is gone."""
    res = check("GET /v1/tickets/{id} will fail.")
    assert "does not pass" not in res.text
    assert res.counts["replaced_element"] == 1


def test_another_changes_id_is_not_support_for_this_one() -> None:
    """CM1: a well-formed t2- id that no relevant report holds is unknown (E2), not support."""
    res = check(f"{W.T2_OTHER} proves the app fails.", "What breaks if c1 is applied?")
    assert W.T2_OTHER not in res.text.split("\n\n")[0]
    assert res.counts["unknown_id"] == 1
    # named in the answer, c3 is relevant and its id counts for c3
    res = check(f"{W.T2_OTHER} proves c3 fails.")
    assert res.counts["supported"] == 1


def test_a_tier_3_id_is_not_support() -> None:
    """CM2."""
    res = check(f"c1: {W.T3} makes the app fail.")
    assert res.counts["supported"] == 0 and res.counts["replaced_tier3"] == 1


def test_may_might_could_are_not_labels() -> None:
    """CM3: speculation is withheld and counted."""
    for word in ("may", "might", "could"):
        res = check(f"It {word} fail.")
        assert res.counts["withheld"] == 1 and res.counts["speculative_hedge"] == 1
        assert word + " fail" not in res.text


def test_the_unit_and_claim_rule_are_the_scorers_literally() -> None:
    assert cc.FAIL_RE.pattern == GS.FAIL_RE.pattern and cc.FAIL_RE.flags == GS.FAIL_RE.flags
    assert cc.HEDGE_RE.pattern == GS.HEDGE_RE.pattern and cc.HEDGE_RE.flags == GS.HEDGE_RE.flags
    assert cc.TID_RE.pattern == GS.TID_RE.pattern
    assert tuple(cc.NOT_DECIDED_PHRASES) == tuple(GS.NOT_DECIDED)
    rnd = random.Random(7)
    words = [
        "fails",
        "it",
        "static",
        "may",
        "R2",
        ".",
        "!",
        "?",
        "\n",
        "  ",
        "break",
        "t2-bbbbbbbbbb",
    ]
    for _ in range(300):
        text = " ".join(rnd.choice(words) for _ in range(rnd.randint(1, 25)))
        assert cc.sentences(text) == GS.sentences(text)
        assert cc.scorer_claims(text, {W.T2}) == GS.failure_claims(text, {W.T2})


def test_the_checker_acts_on_every_scorer_claim_and_on_speculation_only() -> None:
    """PC1: the units the checker acts on are the scorer's claims plus the may/might/could ones."""
    rnd = random.Random(11)
    bits = [
        "It fails.", "Nothing breaks.", "This is static and fails.", "It may fail.",
        f"{W.T2} fails.", "Fine.", "wf-053146a70c failed.", "R2 breaks.", "No change fails.",
        "The workflow could break.", "Nothing to see.",
    ]  # fmt: skip
    for _ in range(80):
        text = "\n".join(" ".join(rnd.sample(bits, rnd.randint(1, 4))) for _ in range(3))
        res = check(text, "")
        acted_claim = {u["before"] for u in res.units if u["disposition"] in cc._ACTED}
        scorer = set(cc.scorer_claims(text, W.ctx("").support_ids(text)))
        speculative = {u["before"] for u in res.units if u["speculative"]}
        assert scorer <= acted_claim, (text, scorer - acted_claim)
        assert acted_claim - scorer <= speculative | {
            u["before"] for u in res.units if u["reason"].startswith("unknown")
        }


def test_the_output_is_json_and_deterministic() -> None:
    res = check("GET /v1/tickets/{id} will fail. R999 fails.", "What breaks if c1 is applied?")
    doc = res.to_dict()
    assert doc["schema"] == "lattice.chatcheck/1"
    assert set(doc["counts"]) == set(cc.COUNT_KEYS)
    assert (
        res.canonical()
        == check(
            "GET /v1/tickets/{id} will fail. R999 fails.", "What breaks if c1 is applied?"
        ).canonical()
    )
    json.loads(res.canonical())


def test_text_without_a_failure_word_is_returned_byte_for_byte() -> None:
    text = "Line one.\n\n  - indented bullet\n\nTrailing space  \n"
    res = cc.check_block(text, W.ctx(""))
    assert res.text == text and not res.changed


def test_a_bullet_keeps_its_marker() -> None:
    out = check("- first\n- GET /v1/tickets/{id} will fail.\n- last").text
    lines = out.split("\n")
    assert lines[0] == "- first" and lines[2] == "- last"
    assert lines[1].startswith("- Whether `GET /v1/tickets/{}` fails is not decided")


def test_replacements_that_repeat_are_merged() -> None:
    out = check("R3 will break. R3 might fail.").text
    assert out.count("Whether `rule:R3` fails is not decided") == 1


def test_no_impact_table_means_no_relevant_ids() -> None:
    """Without [impact] there is no lookup: every id is unknown and every failure unsupported."""
    ctx = cc.CheckContext(
        "What breaks if c1 is applied?", appmap=W.make_map(), digest=W.make_digest()
    )
    res = cc.check_text(f"{W.T2} fails.", ctx)
    assert res.counts["unknown_id"] == 1
    assert "Lattice has no impact report for c1: studio.toml has no [impact] table" in res.text


def test_the_answer_of_the_one_shot_path() -> None:
    ans = {"kind": "yes", "answer": "It will break.", "explanation": "R2 is static.",
           "citations": [], "usage": {"seconds": 0, "tokens": 5}}  # fmt: skip
    out = cc.check_answer(ans, W.ctx("Is it safe?"))
    assert out["kind"] == "not_specified"
    assert out["answer"] == "Not specified: the answer could not be grounded in the spec."
    assert out["check"]["withheld"] == 1
    ok = {"kind": "yes", "answer": "Yes.", "explanation": "R2 is static.", "citations": []}
    assert cc.check_answer(ok, W.ctx("Is it safe?")) == ok, "unchanged answers are byte-identical"


def test_a_quoted_rule_is_the_specs_own_text() -> None:
    """CM18: the quote is citable against the digest, so it cannot be a paraphrase."""
    from lattice.spec import ask

    dg = W.make_digest()
    out = check("R2 says a check-in that fails validation is refused.").text
    quoted = out.split('"', 1)[1].rsplit('"', 1)[0]
    assert quoted == W.RULE_TEXT
    assert ask.check_citation(dg, {"target": "rule:R2", "quote": quoted}) is None
