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
    ask,
    ask_many,
    build_digest,
    check_citation,
)
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
    assert "[cell:ann|POST /configs/{}/runs] 201" in messages[0]["content"]
    assert a.usage["rounds"] == 1 and a.usage["model"] == "m"


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
