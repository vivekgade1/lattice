"""lattice.spec.ask: the digest, the deterministic citation check, the downgrade rule,
verify against the racy runs app, the CLI, and one live run behind
LATTICE_LIVE_LLM=1 (the Claude Code CLI, haiku; never an API key)."""

from __future__ import annotations

import json
import os

import pytest

from lattice.llm.client import ScriptedGenerator
from lattice.spec import ask as ask_mod
from lattice.spec.ask import (
    VerifyConfig,
    approx_tokens,
    ask,
    ask_many,
    build_digest,
    check_citation,
    retrieve,
)
from lattice.spec.model import Scenario, Spec, Step
from lattice.spec.rules import Trace, extract_rules
from tests.spec import racy

SPEC = racy.RUNS_SPEC


def _reply(kind: str, answer: str, citations: list[dict], explanation: str = "x") -> str:
    return json.dumps(
        {"kind": kind, "answer": answer, "explanation": explanation, "citations": citations}
    )


def _cite(target: str, quote: str, step: int | None = None) -> dict:
    return {"target": target, "step": step, "quote": quote}


# ---- the digest -----------------------------------------------------------------------------


def test_digest_is_deterministic_and_indexes_every_element():
    a, b = build_digest(SPEC), build_digest(SPEC)
    assert a.text == b.text
    assert a.elements.keys() == b.elements.keys()
    for target in (
        "cell:ben|POST /configs/{}/runs",
        "scenario:one_live_run_per_config",
        "invariant:one_live_run_per_config",
        "machine:Run.status",
        "profile:Run",
    ):
        assert target in a.elements
        assert f"[{target}]" in a.text
    assert a.elements["cell:ben|POST /configs/{}/runs"].content == "201/409"
    assert "  2. POST /configs/1/runs as ben -> 409" in a.text
    # an unexercised cell is not in the digest
    assert "cell:ben|POST /worker/drain" not in a.elements


def test_digest_with_trace_lists_rules():
    md = "# Runs\n\n- A config has at most one live run.\n- Runs are fast.\n"
    trace = Trace(
        "REQ.md",
        extract_rules(md),
        links={"R1": ["invariant:one_live_run_per_config"]},
        unchecked={"R2": "no timing in the spec"},
    )
    d = build_digest(SPEC, trace)
    assert d.elements["rule:R1"].content == "A config has at most one live run."
    assert "[rule:R2] (unchecked; unchecked because: no timing in the spec)" in d.text


# ---- the citation check ---------------------------------------------------------------------


def test_citation_rules():
    d = build_digest(SPEC)
    ok = check_citation
    assert ok(d, _cite("cell:ann|POST /configs/{}/runs", "201")) is None
    assert ok(d, _cite("cell:ben|POST /configs/{}/runs", "201/409")) is None
    assert "the cell is 201/409" in ok(d, _cite("cell:ben|POST /configs/{}/runs", "409"))
    assert "no element" in ok(d, _cite("cell:zed|POST /x", "200"))
    assert (
        ok(d, _cite("invariant:one_live_run_per_config", "at most one queued or running")) is None
    )
    assert "not in the element" in ok(d, _cite("invariant:one_live_run_per_config", "at most two"))
    assert ok(d, _cite("machine:Run.status", "queued -> cancelled")) is None
    assert "not an allowed move" in ok(d, _cite("machine:Run.status", "succeeded -> queued"))
    assert "a step number is only" in ok(d, _cite("invariant:one_live_run_per_config", "x", 1))


def test_step_number_and_step_quote_are_checked():
    d = build_digest(SPEC)
    t = "scenario:one_live_run_per_config"
    assert check_citation(d, _cite(t, "POST /configs/1/runs as ben -> 409", 2)) is None
    assert check_citation(d, _cite(t, "POST /configs/{}/runs -> 409", 2)) is None
    assert "has no step 9" in check_citation(d, _cite(t, "POST /configs/1/runs -> 409", 9))
    assert "status" in check_citation(d, _cite(t, "POST /configs/1/runs as ben -> 201", 2))
    assert "method" in check_citation(d, _cite(t, "GET /configs/1/runs -> 409", 2))
    assert "path" in check_citation(d, _cite(t, "POST /runs/1/cancel -> 409", 2))
    assert "caller" in check_citation(d, _cite(t, "POST /configs/1/runs as ann -> 409", 2))


# ---- ask ------------------------------------------------------------------------------------


def test_yes_with_a_valid_cell_citation():
    gen = ScriptedGenerator(
        [_reply("yes", "Yes, with 201.", [_cite("cell:ann|POST /configs/{}/runs", "201")])]
    )
    a = ask(SPEC, "Can ann queue a run?", generator=gen, model="m")
    assert a.kind == "yes"
    assert a.citations == [_cite("cell:ann|POST /configs/{}/runs", "201")]
    assert a.rejected_citations == [] and a.citations_checked
    assert len(gen.calls) == 1
    system, messages = gen.calls[0]
    assert "general knowledge" in system
    # a small access matrix is part of the core, in the (cached) system prompt
    assert "[cell:ann|POST /configs/{}/runs] 201" in system
    assert "Question: Can ann queue a run?" in messages[0]["content"]
    assert a.usage["rounds"] == 1 and a.usage["model"] == "m"
    assert a.usage["digest_elements"] == len(build_digest(SPEC).elements)  # all fit


def test_made_up_target_is_rejected_and_the_answer_downgraded_after_one_repair():
    bad = _reply("yes", "Yes.", [_cite("cell:admin|DELETE /runs/{}", "204")])
    gen = ScriptedGenerator([bad, bad, bad])
    a = ask(SPEC, "Can an admin delete a run?", generator=gen)
    assert len(gen.calls) == 2  # one repair round, no more
    repair = gen.calls[1][1][-1]["content"]
    assert "cell:admin|DELETE /runs/{}" in repair and "no element" in repair
    assert a.kind == "not_specified"
    assert "not grounded" in a.explanation and "yes" in a.explanation
    assert a.citations == []
    assert a.rejected_citations == [
        {
            "target": "cell:admin|DELETE /runs/{}",
            "reason": "no element 'cell:admin|DELETE /runs/{}' in the spec",
        }
    ]


def test_wrong_quote_is_rejected():
    wrong = _reply("yes", "Ben gets 200.", [_cite("cell:ben|POST /configs/{}/runs", "200")])
    gen = ScriptedGenerator([wrong, wrong])
    a = ask(SPEC, "Does ben get 200 queueing a run?", generator=gen)
    assert a.kind == "not_specified"
    assert a.rejected_citations[0]["reason"] == "the quote says 200; the cell is 201/409"


def test_repair_round_can_fix_the_citation():
    first = _reply("no", "No.", [_cite("cell:ben|POST /configs/{}/runs", "403")])
    fixed = _reply(
        "no",
        "No: 409.",
        [_cite("scenario:one_live_run_per_config", "POST /configs/1/runs as ben -> 409", 2)],
    )
    gen = ScriptedGenerator([first, fixed])
    a = ask(SPEC, "Can ben queue a second live run for a config?", generator=gen)
    assert a.kind == "no" and a.rejected_citations == []
    assert a.citations[0]["step"] == 2


def test_partial_rejection_keeps_the_answer():
    reply = _reply(
        "yes",
        "Yes.",
        [_cite("cell:ann|POST /configs/{}/runs", "201"), _cite("invariant:nope", "x")],
    )
    gen = ScriptedGenerator([reply, reply])
    a = ask(SPEC, "Can ann queue a run?", generator=gen)
    assert a.kind == "yes" and len(a.citations) == 1
    assert a.rejected_citations[0]["target"] == "invariant:nope"


def test_not_specified_passes_through():
    gen = ScriptedGenerator([_reply("not_specified", "The spec does not say.", [])])
    a = ask(SPEC, "Can runs be scheduled by cron?", generator=gen)
    assert a.kind == "not_specified" and a.answer == "The spec does not say."
    assert len(gen.calls) == 1


def test_unparsable_reply_is_not_specified():
    gen = ScriptedGenerator(["yes of course", "still prose"])
    a = ask(SPEC, "Can ann queue a run?", generator=gen)
    assert a.kind == "not_specified" and "could not be read" in a.explanation


def test_selected_targets_are_shown_and_ask_many_reuses_one_digest(monkeypatch):
    built = []
    real = ask_mod.build_digest
    monkeypatch.setattr(ask_mod, "build_digest", lambda *a, **k: built.append(1) or real(*a, **k))
    gen = ScriptedGenerator([_reply("not_specified", "?", [])] * 2)
    answers = ask_many(SPEC, ["q1", "q2"], generator=gen)
    assert [a.kind for a in answers] == ["not_specified"] * 2 and len(built) == 1
    gen = ScriptedGenerator([_reply("not_specified", "?", [])])
    ask(SPEC, "q", selected=["machine:Run.status"], generator=gen)
    assert "selected these elements" in gen.calls[0][1][0]["content"]


# ---- retrieval and cost -------------------------------------------------------------------

TOPICS = (
    "invoice shipment coupon ticket badge folder alarm survey voucher pallet "
    "lesson garden recipe kiosk parcel trophy beacon ledger meadow quiver"
).split()
VERBS = "approve archive refund close reopen share lock export rename audit".split()


def _big_spec() -> Spec:
    """A synthetic spec the size of a real one: 200 scenarios (20 topics x 10 verbs),
    each with given rows and seven steps, on the runs profile."""
    scenarios = []
    for topic in TOPICS:
        for j, verb in enumerate(VERBS):
            given = {
                "configs": [
                    {"id": k, "name": f"{topic} config {k}", "notes": f"seeded for {verb} " * 3}
                    for k in range(1, 7)
                ]
            }
            other = 403 + j % 2
            scenarios.append(
                Scenario(
                    f"{topic}_{verb}_by_owner_and_stranger",
                    given=given,
                    steps=(
                        Step(
                            "POST",
                            f"/{topic}s",
                            "ann",
                            json={"title": f"{topic} one", "note": "x" * 30},
                            expect_status=201,
                            expect_db={"configs": {"count": 6}},
                        ),  # fmt: skip
                        Step("GET", f"/{topic}s/1", "ann", expect_body={"id": 1, "state": "new"}),
                        Step(
                            "POST",
                            f"/{topic}s/1/{verb}",
                            "ann",
                            expect_status=200,
                            expect_body={"id": 1, "state": f"{verb}d"},
                        ),  # fmt: skip
                        Step("POST", f"/{topic}s/1/{verb}", "ben", expect_status=other),
                        Step("GET", f"/{topic}s/1", "ben", expect_status=404),
                        Step("GET", f"/{topic}s", "ben", expect_body=[]),
                        Step(
                            "POST",
                            f"/{topic}s/1/{verb}",
                            "ann",
                            expect_status=409,
                            expect_db={"configs": {"where": {"id": 1}, "count": 1}},
                        ),  # fmt: skip
                    ),
                )
            )
    scenarios[57] = Scenario(
        "password_reset_needs_the_emailed_token",
        steps=(
            Step("POST", "/password/reset", None, json={"email": "ann"}, expect_status=202),
            Step(
                "POST", "/password/reset/confirm", None, json={"token": "bad"}, expect_status=400
            ),  # fmt: skip
        ),
    )
    return Spec(profile=racy.RUNS_PROFILE, users=racy.USERS, scenarios=tuple(scenarios))


BIG = _big_spec()


def _big_trace() -> Trace:
    """65 owner rules over BIG (a real spec's count), each linked to its scenario."""
    lines, links = [], {}
    for t in TOPICS:
        for v in (*VERBS[:3], VERBS[6]):
            if len(lines) == 65:
                break
            lines.append(f"- Only the owner of a {t} may {v} it; anyone else is refused.")
            links[f"R{len(lines)}"] = [f"scenario:{t}_{v}_by_owner_and_stranger"]
    return Trace("REQ.md", extract_rules("# Big\n\n" + "\n".join(lines) + "\n"), links=links)


def test_retrieval_includes_the_relevant_scenario_and_excludes_irrelevant_ones():
    d = build_digest(BIG)
    r = retrieve(d, "Can ben refund a voucher?")
    assert r.scenarios[0] == "scenario:voucher_refund_by_owner_and_stranger"
    assert "[scenario:voucher_refund_by_owner_and_stranger]" in r.context
    assert "  4. POST /vouchers/1/refund as ben -> 403" in r.context
    assert "scenario:alarm_lock_by_owner_and_stranger" not in r.sent
    assert "[scenario:alarm_lock_by_owner_and_stranger]" not in r.context
    # the index still names every scenario, in the system prompt
    assert "alarm_lock_by_owner_and_stranger" in r.system
    assert "[scenario:" not in r.system
    # word forms meet: "cancelled" / "cancellation", "resetting" / "reset"
    r = retrieve(d, "What happens when resetting a forgotten password?")
    assert r.scenarios[0] == "scenario:password_reset_needs_the_emailed_token"
    # a selected cell pulls in the scenarios that exercise it
    r = retrieve(d, "what is this?", ["cell:ben|POST /gardens/{}/share"])
    assert "scenario:garden_share_by_owner_and_stranger" in r.scenarios


def test_retrieval_follows_rule_links():
    md = "# Big\n\n- Only the owner may refund.\n"
    trace = Trace("REQ.md", extract_rules(md),
                  links={"R1": ["scenario:quiver_refund_by_owner_and_stranger"]})  # fmt: skip
    d = build_digest(BIG, trace)
    r = retrieve(d, "Who may refund things, and is that only the owner?")
    assert "scenario:quiver_refund_by_owner_and_stranger" in r.scenarios[:3]
    assert "(checks rule:R1)" in r.context
    assert "[rule:R1] (checked; checked by scenario:quiver_refund" in r.system


def test_prompt_stays_under_the_token_budget_on_a_large_spec():
    d = build_digest(BIG)
    assert len(d.of_kind("scenario")) == 200
    full = approx_tokens(ask_mod.SYSTEM + d.text)
    r = retrieve(d, "Can ben lock a parcel that ann created?")
    prompt = approx_tokens(r.system) + approx_tokens(r.context)
    print(f"full digest prompt ~{full} tokens; retrieved prompt ~{prompt} tokens")
    assert full > 40_000
    assert prompt < 25_000
    assert approx_tokens(r.context) <= ask_mod.SCENARIO_BUDGET + ask_mod.CELLS_BUDGET + 200
    # the matrix is too large for the core: the question's cells come with it
    assert "[cell:ben|POST /parcels/{}/lock] 403" in r.context
    assert "[cell:" not in r.system


def test_system_prompt_is_identical_across_questions():
    gen = ScriptedGenerator([_reply("not_specified", "?", [])] * 2)
    answers = ask_many(BIG, ["Can ben lock a parcel?", "Who can export a ledger?"], generator=gen)
    (s1, m1), (s2, m2) = gen.calls
    assert s1 == s2
    assert m1[0]["content"] != m2[0]["content"]
    assert "parcel_lock" in m1[0]["content"] and "ledger_export" in m2[0]["content"]
    for a in answers:
        assert 0 < a.usage["digest_elements"] < len(build_digest(BIG).elements)


def test_need_more_round_sends_the_asked_elements_once():
    target = "scenario:meadow_audit_by_owner_and_stranger"
    need = json.dumps({"need": [target, "scenario:nope"], "search": ["trophy rename"]})
    final = _reply("no", "No: 403.", [_cite(target, "POST /meadows/1/audit as ben -> 404", 4)])
    gen = ScriptedGenerator([need, final])
    a = ask(BIG, "Can ben approve a kiosk?", generator=gen)
    assert len(gen.calls) == 2
    first = gen.calls[0][1][0]["content"]
    assert f"[{target}]" not in first
    more = gen.calls[1][1][-1]["content"]
    assert f"[{target}]" in more and "  4. POST /meadows/1/audit as ben -> 404" in more
    assert "[scenario:trophy_rename_by_owner_and_stranger]" in more  # the keyword search
    assert "No such elements in the spec: scenario:nope" in more
    assert gen.calls[1][0] == gen.calls[0][0]  # same system prompt
    assert a.kind == "no" and a.citations[0]["target"] == target
    assert a.usage["rounds"] == 2


def test_need_more_only_once():
    need = json.dumps({"need": ["scenario:meadow_audit_by_owner_and_stranger"]})
    gen = ScriptedGenerator([need, need, need])
    a = ask(BIG, "Can ben approve a kiosk?", generator=gen)
    assert len(gen.calls) == 3  # the answer round, one need round, one repair round
    assert a.kind == "not_specified"


def test_a_citation_of_an_element_not_sent_is_checked_against_the_full_digest():
    target = "scenario:beacon_close_by_owner_and_stranger"
    d = build_digest(BIG)
    assert target not in retrieve(d, "Can ben reopen a survey?").sent
    good = _reply("no", "No.", [_cite(target, "POST /beacons/1/close as ben -> 404", 4)])
    gen = ScriptedGenerator([good])
    a = ask(BIG, "Can ben reopen a survey?", generator=gen)
    assert a.kind == "no" and a.citations[0]["target"] == target
    bad = _reply("no", "No.", [_cite(target, "POST /beacons/1/close as ben -> 200", 4)])
    gen = ScriptedGenerator([bad, bad])
    a = ask(BIG, "Can ben reopen a survey?", generator=gen)
    assert a.kind == "not_specified"
    assert "status (the step expects 404)" in a.rejected_citations[0]["reason"]


# ---- verify ---------------------------------------------------------------------------------


def _cfg() -> VerifyConfig:
    return VerifyConfig(app=racy.runs_app(), db=racy.build_runs_db, login=racy.login)


YES = _reply(
    "yes",
    "No: the second gets 409.",
    [_cite("scenario:one_live_run_per_config", "POST /configs/1/runs as ben -> 409", 2)],
)


def _scenario(*steps: dict) -> str:
    return json.dumps({"steps": list(steps)})


def _step(method, path, user, status, **kw) -> dict:
    return {"method": method, "path": path, "user": user, "expect_status": status, **kw}


def _claim(scenario: str, claim: int) -> str:
    return json.dumps({**json.loads(scenario), "claim_step": claim})


def test_verify_agrees():
    gen = ScriptedGenerator(
        [
            YES,
            _scenario(
                _step("POST", "/configs/1/runs", "ann", 201),
                _step("POST", "/configs/1/runs", "ben", 409, expect_db={"runs": {"count": 1}}),
            ),
        ]
    )
    a = ask(SPEC, "Can ben queue a second live run?", generator=gen, verify=_cfg())
    assert a.verified["status"] == "agrees", a.verified
    assert "2. POST /configs/1/runs as ben -> 409" in a.verified["scenario"]
    assert a.usage["rounds"] == 2


def test_verify_disagrees():
    gen = ScriptedGenerator(
        [
            YES,
            _scenario(
                _step("POST", "/configs/1/runs", "ann", 201),
                _step("POST", "/configs/1/runs", "ben", 201),
            ),
        ]
    )
    a = ask(SPEC, "q", generator=gen, verify=_cfg())
    assert a.verified["status"] == "disagrees"
    assert "step 2" in a.verified["detail"] and "expected 201, got 409" in a.verified["detail"]
    assert a.verified["claim_step"] == 2  # the default: the last step


BAD_SETUP = _scenario(
    _step("POST", "/configs/9/runs", "ann", 201),  # no config 9: the app answers 404
    _step("POST", "/configs/9/runs", "ben", 409),
)
GOOD_SETUP = _scenario(
    _step("POST", "/configs/1/runs", "ann", 201), _step("POST", "/configs/1/runs", "ben", 409)
)


def test_verify_setup_failure_is_retried_once_then_agrees():
    gen = ScriptedGenerator([YES, _claim(BAD_SETUP, 2), _claim(GOOD_SETUP, 2)])
    a = ask(SPEC, "Can ben queue a second live run?", generator=gen, verify=_cfg())
    assert a.verified["status"] == "agrees", a.verified
    assert a.verified["claim_step"] == 2
    retry = gen.calls[2][1][-1]["content"]
    assert "step 1 is setup" in retry and "expected 201, got 404" in retry
    assert a.usage["rounds"] == 3


def test_verify_setup_failure_twice_is_inconclusive():
    gen = ScriptedGenerator([YES, _claim(BAD_SETUP, 2), _claim(BAD_SETUP, 2)])
    a = ask(SPEC, "Can ben queue a second live run?", generator=gen, verify=_cfg())
    v = a.verified
    assert v["status"] == "inconclusive", v
    assert "setup step 1 failed before the claim step 2" in v["detail"]
    assert "expected 201, got 404" in v["detail"]
    assert v["claim_step"] == 2


def test_verify_claim_status_held_but_db_assertion_failed_is_inconclusive():
    # the Q06 shape: the claim step answers 409 as expected, but its expect_db reads state
    # a setup step was meant to produce (the app holds one run, not two)
    scenario = _scenario(
        _step("POST", "/configs/1/runs", "ann", 201),
        _step("POST", "/configs/1/runs", "ben", 409, expect_db={"runs": {"count": 2}}),
    )
    gen = ScriptedGenerator([YES, _claim(scenario, 2)])
    a = ask(SPEC, "Can ben queue a second live run?", generator=gen, verify=_cfg())
    v = a.verified
    assert v["status"] == "inconclusive", v
    assert "setup step 1 (" in v["detail"] and "POST /configs/1/runs as ann" in v["detail"]
    assert v["claim_step"] == 2


def test_verify_db_assertion_failure_on_a_lone_claim_step_still_disagrees():
    scenario = _scenario(
        _step("POST", "/configs/1/runs", "ann", 201, expect_db={"runs": {"count": 5}})
    )
    gen = ScriptedGenerator([YES, _claim(scenario, 1)])
    a = ask(SPEC, "q", generator=gen, verify=_cfg())
    assert a.verified["status"] == "disagrees", a.verified


def test_verify_claim_step_failure_disagrees_without_retry():
    claim_fails = _scenario(
        _step("POST", "/configs/1/runs", "ann", 201),
        _step("POST", "/configs/1/runs", "ben", 201),  # the claim: the app says 409
        _step("GET", "/runs/1", "ann", 200),
    )
    gen = ScriptedGenerator([YES, _claim(claim_fails, 2)])
    a = ask(SPEC, "q", generator=gen, verify=_cfg())
    assert a.verified["status"] == "disagrees" and a.verified["claim_step"] == 2
    assert "step 2" in a.verified["detail"] and len(gen.calls) == 2


def test_verify_retry_may_not_change_the_claim():
    cheat = _scenario(
        _step("POST", "/configs/1/runs", "ann", 201), _step("POST", "/configs/1/runs", "ben", 201)
    )
    gen = ScriptedGenerator([YES, _claim(BAD_SETUP, 2), _claim(cheat, 2)])
    a = ask(SPEC, "q", generator=gen, verify=_cfg())
    assert a.verified["status"] == "inconclusive"
    assert "changed the claim step" in a.verified["detail"]


def test_verify_prompt_shows_setup_from_the_spec_and_caches_its_system():
    gen = ScriptedGenerator([YES, _claim(GOOD_SETUP, 2)])
    ask(SPEC, "Can ben queue a second live run?", generator=gen, verify=_cfg())
    system, messages = gen.calls[1]
    assert "CLAIM STEP" in system and "[machine:Run.status]" in system
    prompt = messages[0]["content"]
    assert "Setup that works in this spec" in prompt
    assert "[scenario:one_live_run_per_config]" in prompt
    assert (
        '{"method": "POST", "path": "/configs/1/runs", "user": "ann", "expect_status": 201}'
        in prompt
    )


def test_verify_invalid_scenario_is_an_error():
    gen = ScriptedGenerator([YES, _scenario(_step("POST", "/configs/1/runs", "zed", 201))])
    a = ask(SPEC, "q", generator=gen, verify=_cfg())
    assert a.verified["status"] == "error" and "S011" in a.verified["detail"]
    gen = ScriptedGenerator([YES, "no json here"])
    a = ask(SPEC, "q", generator=gen, verify=_cfg())
    assert a.verified["status"] == "error" and "no valid scenario" in a.verified["detail"]


def test_verify_observes_a_not_specified_answer():
    gen = ScriptedGenerator(
        [
            _reply("not_specified", "Not said.", []),
            _scenario(
                _step("GET", "/runs/7", "ann", 200), _step("POST", "/runs/7/cancel", "ben", 200)
            ),
        ]
    )
    a = ask(SPEC, "What happens reading a run that does not exist?", generator=gen, verify=_cfg())
    assert a.verified["status"] == "observed", a.verified
    assert "1. GET /runs/7 as ann -> 404" in a.verified["scenario"]
    assert "2. POST /runs/7/cancel as ben -> 404" in a.verified["scenario"]


# ---- CLI ------------------------------------------------------------------------------------


def test_cli_json(capsys):
    gen = ScriptedGenerator(
        [_reply("yes", "Yes.", [_cite("cell:ann|POST /configs/{}/runs", "201")])]
    )
    code = ask_mod.main(
        ["ask", "--spec", "tests.spec.racy:RUNS_SPEC", "Can ann queue a run?", "--json"],
        generator=gen,
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kind"] == "yes" and out["citations_checked"] is True
    assert set(out) == {
        "kind",
        "answer",
        "explanation",
        "citations",
        "citations_checked",
        "rejected_citations",
        "verified",
        "usage",
    }


def test_cli_verify(capsys):
    gen = ScriptedGenerator(
        [
            YES,
            _scenario(
                _step("POST", "/configs/1/runs", "ann", 201),
                _step("POST", "/configs/1/runs", "ben", 409),
            ),
        ]
    )
    code = ask_mod.main(
        [
            "--spec",
            "tests.spec.racy:RUNS_SPEC",
            "q",
            "--verify",
            "--app",
            "tests.spec.racy:SAFE_APP",
            "--db",
            "tests.spec.racy:build_runs_db",
            "--login",
            "tests.spec.racy:login",
        ],
        generator=gen,
    )
    assert code == 0
    assert "verified: agrees" in capsys.readouterr().out


# ---- live -----------------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("LATTICE_LIVE_LLM") != "1", reason="live LLM smoke test: set LATTICE_LIVE_LLM=1"
)
def test_live_haiku_one_cited_answer_one_not_specified():
    from lattice.llm.cli_client import ClaudeCliClient

    gen = ClaudeCliClient(model="haiku", effort="", full_text=True, timeout=600)
    answers = ask_many(
        SPEC,
        [
            "Can ben queue a run for a config that already has a queued run?",
            "Can ben read a run with GET /runs/{id}?",
        ],
        generator=gen,
        model="haiku",
    )
    print(json.dumps([a.to_dict() for a in answers], indent=2))
    first, second = answers
    assert first.kind in ("no", "yes", "value") and first.citations, first
    assert second.kind == "not_specified", second


@pytest.mark.skipif(
    os.environ.get("LATTICE_LIVE_LLM") != "1", reason="live LLM smoke test: set LATTICE_LIVE_LLM=1"
)
def test_live_haiku_large_spec_reuses_the_cached_system_prompt():
    """Two questions on the 200-scenario spec with 65 rules: each sends well under 25k
    input tokens, and the second reads the core (system prompt) from the CLI's cache.
    (The toy spec's core is below Haiku's minimum cacheable prompt, so it never caches.)"""
    from lattice.llm.cli_client import ClaudeCliClient

    gen = ClaudeCliClient(model="haiku", effort="", full_text=True, timeout=600)
    first, second = ask_many(
        BIG,
        ["Can ben lock a parcel that ann created?", "What does ben get exporting a ledger?"],
        trace=_big_trace(),
        generator=gen,
        model="haiku",
    )
    for a in (first, second):
        print(json.dumps({"kind": a.kind, "answer": a.answer, "usage": a.usage}))
        assert a.usage["input_tokens"] < 25_000 * a.usage["rounds"]
    assert first.kind == "no" and first.citations, first
    assert second.usage["cache_read_tokens"] > 0, second.usage
