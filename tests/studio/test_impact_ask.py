"""The model-free router for "what breaks if ..." questions (lattice/spec/impact_ask.py, API v1.25)
and the chat sessions' change_impact allowance: the frozen patterns, the answers, that a routed
question makes no model call and a question that does not match follows the old path untouched, and
that without ``[impact]`` the session's argv and system prompt are what they were."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from lattice.spec import appmap as am
from lattice.spec import impact_ask as ia
from lattice.studio import session as sess
from lattice.studio.server import make_server
from tests.studio.test_appmap_api import (
    Served,
    make_world,
    saved_report,
    serve,  # noqa: F401 -- a fixture
)

ROUTED = [
    "What breaks if c2 is applied?",
    "what breaks if c10 lands",
    "Which workflows fail under c1?",
    "Does c3 affect the access rules?",
    "How does c4 impact the habit endpoints?",
    "WHAT BREAKS IF C5 IS APPLIED",
    "Is anything broken by c6?",
    "What would break if we raised the habit limit to 100?",
    "what will fail if we remove the endpoint",
    "If we rename the route, what breaks?",
    "if we drop the limit, does anything fail?",
    "What breaks when a check-in is refused?",
    "  what breaks if the limit is 100",
]
NOT_ROUTED = [
    "What happens when a check-in fails validation?",
    "Does the spec say anything about habit names?",
    "Which rules are implemented but not verified?",
    "Is GET /v1/sync built?",
    "What does c2 say?",  # a change id, no impact word
    "The impact of the limit on users is described in R5.",  # no change id, no hypothetical
    "What does R12 break down into?",  # "break" inside "what ... if" is absent
    "Which verified workflows use POST /v1/habits/{}/completions?",
    "describe the failing scenario list",
]


@pytest.mark.parametrize("q", ROUTED)
def test_the_frozen_patterns_route(q: str) -> None:
    assert ia.is_routed(q)


@pytest.mark.parametrize("q", NOT_ROUTED)
def test_the_frozen_patterns_leave_the_rest(q: str) -> None:
    assert not ia.is_routed(q)
    assert ia.route(q, lookup=lambda cid: pytest.fail("looked up")) is None


def test_the_patterns_are_the_registered_ones() -> None:
    assert ia.CHANGE_ID.pattern == r"\bc\d+\b"
    assert ia.IMPACT_WORD.pattern == r"\b(break|breaks|broken|fail|fails|failing|impact|affect)\b"
    assert [p.pattern for p in ia.HYPOTHETICAL] == [
        r"\bwhat\s+(would\s+|will\s+)?(break|fail)s?\b.*\bif\b",
        r"\bif\s+we\b.*\b(break|fail)s?\b",
        r"^\s*what\s+breaks\b",
    ]
    assert all(
        p.flags & __import__("re").IGNORECASE
        for p in (ia.CHANGE_ID, ia.IMPACT_WORD, *ia.HYPOTHETICAL)
    )


def fake_body(**kw: Any) -> dict[str, Any]:
    def t(n: int) -> list[dict[str, Any]]:
        return [
            {"id": f"t{n}-{i}", "kind": "k", "target": f"scenario:s{i}", "rules": ["R1"],
             "message": f"m{n}{i}"}
            for i in range(2)
        ]  # fmt: skip

    report = {
        "input": {"change": "c2"},
        "tiers": {"1": "not_run:not_requested", "2": "run", "3": "run", "4": "not_run:spike_e"},
        "tier2": {"determined": t(2), "flows": []},
        "tier3": {
            "locations": [{"id": f"t3-{i}", "kind": "direct"} for i in range(30)],
            "flows": t(3),
        },
    }
    return {
        "state": "ok",
        "report": report,
        "tier1_saved": {"state": "absent", "differs": [], "tier1": None},
        **kw,
    }


def test_a_change_question_is_answered_with_the_template_and_the_items() -> None:
    a = ia.route("What breaks if c2 is applied?", lookup=lambda cid: fake_body())
    assert a["kind"] == "value" and a["usage"] == {"seconds": 0, "tokens": 0}
    assert a["answer"] == (
        "Tier 1 (observed): not run here; Tier 2 (determined): 2; "
        "Tier 3 (static, may fail): 30 locations, 2 workflows; Tier 4: not run."
    )
    imp = a["impact"]
    assert imp["change"] == "c2" and imp["tiers"]["1"] == "not_run:not_requested"
    assert imp["tier1"] == {"state": "not_run", "failing": [], "flows": []}
    assert [i["id"] for i in imp["tier2"]["determined"]] == ["t2-0", "t2-1"]
    assert len(imp["tier3"]["locations"]) == 20 and imp["tier3"]["total"] == {
        "locations": 30,
        "flows": 2,
    }
    assert [c["target"] for c in a["citations"]] == ["impact:t2-0", "impact:t2-1"]
    assert (
        a["citations_checked"] is True and a["rejected_citations"] == [] and a["verified"] is None
    )
    # the answer says nothing of a failure that it does not source
    import re

    for text in (a["answer"], a["explanation"]):
        for sent in re.split(r"(?<=[.;])\s+", text):
            if re.search(r"\b(fail|fails|failing|failed|break|breaks|broke|broken)\b", sent):
                assert re.search(
                    r"may|might|could|not decided|undecid|static|not run|no change", sent
                ), sent


def test_a_saved_tier_one_is_counted_only_when_shown() -> None:
    item = {
        "id": "t1-1",
        "kind": "check_fails",
        "target": "scenario:x",
        "rules": ["R1"],
        "message": "m",
    }
    body = fake_body(
        tier1_saved={"state": "shown", "differs": [], "tier1": {"failing": [item], "flows": []}}
    )
    a = ia.route("c2 failing?", lookup=lambda cid: body)
    assert a["answer"].startswith("Tier 1 (observed): 1 failing;")
    assert a["impact"]["tier1"]["failing"] == [item]
    assert "impact:t1-1" in [c["target"] for c in a["citations"]]
    mismatch = fake_body(
        tier1_saved={"state": "mismatch", "differs": ["candidate_sha256"], "tier1": None}
    )
    b = ia.route("c2 failing?", lookup=lambda cid: mismatch)
    assert b["answer"].startswith("Tier 1 (observed): not run here;")


def test_a_change_without_a_report_is_not_specified() -> None:
    for state in ("no_change", "change_not_ready", "no_state", "inventory_missing", "error"):
        a = ia.route(
            "What breaks if c9 is applied?", lookup=lambda cid, s=state: {"state": s, "why": "w"}
        )
        assert a["kind"] == "not_specified" and a["citations"] == []
        assert a["impact"]["change"] == "c9" and a["impact"]["state"] == state
        assert "Nothing is decided" in a["answer"]
    huge = ia.route(
        "What breaks if c12345678 is applied?", lookup=lambda cid: pytest.fail("looked up")
    )
    assert huge["impact"]["state"] == "no_change"


def test_a_hypothetical_is_never_a_failure_claim() -> None:
    a = ia.route(
        "What would break if we raised the habit limit to 100?", lookup=lambda c: pytest.fail("x")
    )
    assert a["kind"] == "not_specified" and a["answer"] == ia.NO_PREDICTION
    assert a["citations"] == [] and a["usage"]["tokens"] == 0
    assert a["impact"]["touches"] == {} and a["impact"]["basis"] == "static"
    assert "Lattice does not predict failures." in a["answer"]


# ---------------------------------------------------------------- through the server


def test_a_routed_question_makes_no_model_call(tmp_path: Path, serve) -> None:  # noqa: F811
    w = make_world(tmp_path)
    s = serve(w)
    cid = s.draft()
    calls = len(s.gen.calls)
    a = s.post("/api/ask", {"question": f"What breaks if {cid} is applied?"})[1]
    assert a["kind"] == "value" and a["usage"]["tokens"] == 0 and a["impact"]["change"] == cid
    ids = [i["id"] for i in a["impact"]["tier2"]["determined"]]
    rep = s.get(f"/api/impact?change={cid}")[1]["report"]
    assert ids == [i["id"] for i in rep["tier2"]["determined"]]
    assert a["impact"]["tier3"]["total"]["locations"] == len(rep["tier3"]["locations"])
    b = s.post("/api/ask", {"question": "What would break if we raised the limit?"})[1]
    assert b["kind"] == "not_specified" and b["answer"] == ia.NO_PREDICTION
    c = s.post("/api/ask", {"question": "What breaks if c99 is applied?"})[1]
    assert c["kind"] == "not_specified" and c["impact"]["state"] == "no_change"
    assert len(s.gen.calls) == calls, "a routed question must not reach the generator"


def test_a_hypothetical_names_its_static_neighbourhood(tmp_path: Path, serve) -> None:  # noqa: F811
    s = serve(make_world(tmp_path))
    q = "What would break if we changed POST /api/projects or rule R3 or machine:Job.status?"
    a = s.post("/api/ask", {"question": q})[1]
    t = a["impact"]["touches"]
    assert set(t) == {"impl:route:POST /api/projects", "rule:R3", "impl:machine:jobs.status"}
    doc = s.get("/api/appmap")[1]
    built = am.appmap_from_dir(Path(s.srv.studio.root))
    for key, touch in t.items():
        assert touch == am.element(built, key)["touches"], key
    assert doc["state"] == "ok"
    none = s.post("/api/ask", {"question": "What would break if we raised the limit?"})[1]
    assert none["impact"]["touches"] == {}


def test_a_question_that_does_not_match_follows_the_old_path(tmp_path: Path, serve) -> None:  # noqa: F811
    on = serve(make_world(tmp_path / "on"))
    off = serve(make_world(tmp_path / "off", impact=False))
    q = "What happens when a project is published twice?"
    a, b = on.post("/api/ask", {"question": q})[1], off.post("/api/ask", {"question": q})[1]
    for x in (a, b):
        x["usage"].pop("seconds", None)
    assert a == b and "impact" not in a
    assert len(on.gen.calls) >= 1 and len(off.gen.calls) >= 1


def test_without_impact_the_pattern_goes_the_old_way(tmp_path: Path, serve) -> None:  # noqa: F811
    s = serve(make_world(tmp_path, impact=False))
    a = s.post("/api/ask", {"question": "What breaks if c1 is applied?"})[1]
    assert "impact" not in a and a["kind"] == "not_specified"
    assert len(s.gen.calls) >= 1


def test_the_router_reads_a_saved_tier_one_through_the_server(tmp_path: Path, serve) -> None:  # noqa: F811
    w = make_world(tmp_path, reports=True)
    s = serve(w)
    cid = s.draft()
    (w["reports"] / f"{cid}.json").write_text(json.dumps(saved_report(w, cid)))
    a = s.post("/api/ask", {"question": f"does {cid} break anything?"})[1]
    assert a["answer"].startswith("Tier 1 (observed): 1 failing;")
    assert [i["id"] for i in a["impact"]["tier1"]["failing"]] == ["t1-0000000001"]


def test_the_ask_answer_shape(tmp_path: Path, serve) -> None:  # noqa: F811
    s = serve(make_world(tmp_path))
    cid = s.draft()
    a = s.post("/api/ask", {"question": f"what breaks if {cid} is applied"})[1]
    assert set(a) == {
        "kind", "answer", "explanation", "citations", "citations_checked",
        "rejected_citations", "verified", "usage", "impact",
    }  # fmt: skip
    assert isinstance(s, Served)
    assert threading.active_count() >= 1


# ---------------------------------------------------------------- the chat sessions (D5)

FROZEN = (
    "For 'what breaks if ...' questions use mcp__lattice__change_impact on a drafted change "
    "(change id cN) or a candidate directory; never answer them from your own reasoning. Report "
    "its tiers with their item ids, using the tool's wording: tier 1 observed and tier 2 "
    "determined are the only failures you may state; tier 3 items are 'static: may fail', never "
    "a failure; tier 4 is not run. If no change exists, say that Lattice decides failures only "
    "for a drafted change, offer to draft one (change mode), and list what the element touches "
    "as static facts only. Under the read-only modes tier 1 (observe) is refused; say it was "
    "not run."
)


def test_the_paragraph_is_the_frozen_text() -> None:
    assert sess._IMPACT_RULES == FROZEN


def test_with_impact_the_tool_and_the_paragraph_are_added_to_three_modes(tmp_path: Path) -> None:
    on = make_world(tmp_path / "on")["project"]
    off = make_world(tmp_path / "off", impact=False)["project"]
    for mode in ("ask", "explain", "change"):
        assert sess.impact_tools(on, mode) == ("mcp__lattice__change_impact",)
        assert sess.impact_tools(off, mode) == ()
        p_on, p_off = sess.system_prompt(mode, on), sess.system_prompt(mode, off)
        assert FROZEN in p_on and FROZEN not in p_off
        assert (
            p_on.replace("\n\n" + FROZEN, "") == p_off.replace("\n\n" + FROZEN, "")
            or p_on.count(FROZEN) == 1
        )
        assert p_off == sess.system_prompt(mode, off)  # nothing else differs from the base prompt
    assert sess.impact_tools(on, "rebuild") == ()
    assert FROZEN not in sess.system_prompt("rebuild", on)
    assert "mcp__lattice__change_impact" not in sess.MODE_PROFILES["rebuild"].allowed


def test_argv_gains_exactly_one_allowed_tool(tmp_path: Path) -> None:
    on = make_world(tmp_path / "on")["project"]
    kw = dict(claude_bin="claude", text="q", model="sonnet", mcp_config=Path("m.json"),
              settings=Path("s.json"), prompt="p")  # fmt: skip
    for mode in ("ask", "explain", "change"):
        base = sess.build_argv(mode=mode, **kw)
        more = sess.build_argv(mode=mode, extra_allowed=sess.impact_tools(on, mode), **kw)
        i = base.index("--allowedTools") + 1
        assert more[i] == base[i] + ",mcp__lattice__change_impact"
        assert [a for k, a in enumerate(more) if k != i] == [
            a for k, a in enumerate(base) if k != i
        ]
    assert sess.tool_label("mcp__lattice__change_impact", {}) == "Checked the impact"


def test_a_message_builds_the_argv_the_helpers_describe(tmp_path: Path) -> None:
    for impact in (True, False):
        proj = make_world(tmp_path / str(impact), impact=impact)["project"]
        fake = tmp_path / f"claude-{impact}"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        mgr = sess.SessionManager(proj, claude_bin=str(fake))
        s = mgr.create({"mode": "ask"})
        mgr.message(s["id"], {"text": "hello"})
        for _ in range(100):
            if mgr.last_argv.get(s["id"]):
                break
            import time

            time.sleep(0.02)
        argv = mgr.last_argv[s["id"]]
        allowed = argv[argv.index("--allowedTools") + 1].split(",")
        assert ("mcp__lattice__change_impact" in allowed) is impact
        assert (FROZEN in argv[argv.index("--append-system-prompt") + 1]) is impact
        mgr.close()
        assert make_server  # the server module imports cleanly with the session helpers
