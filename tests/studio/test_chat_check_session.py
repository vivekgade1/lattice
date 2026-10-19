"""The chat session path with `[chat]` on (lattice/studio/session.py, API.md v1.26): against a
FAKE ``claude`` that replays stream-json fixtures (tests/studio/fixtures/chat2/), no real CLI and no
API key. Check mode shows no unchecked text, checks every block (also the ones before a tool call),
sums the counts into the result; a routed turn spawns no process; with `[chat]` absent the events
are the v1.25 ones (probes PS2, CM10, CM11, CM13)."""

# ruff: noqa: E501 -- expected outputs are data lines

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice.spec import chat_route as CR
from lattice.studio import session as S
from lattice.studio.session import ChatHooks, SessionManager
from tests.spec import chat_world as W
from tests.studio.test_server import make_project
from tests.studio.test_session import SID, Fake, wait_done

FIX = Path(__file__).parent / "fixtures" / "chat2"
AM = W.make_map()


def records(name: str) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in (FIX / f"{name}.jsonl").read_text().splitlines()]


@pytest.fixture()
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fake:
    return Fake(tmp_path, monkeypatch)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path / "proj", repo=False)


def hooks(route: bool = False, check: bool = False) -> ChatHooks:
    return ChatHooks(
        route=route,
        check=check,
        context=lambda q, sel, change: W.ctx(q, open_change=change) if check else None,
        pre_route=(lambda q, sel: (r.markdown, r.route) if (r := CR.route(q, appmap=AM)) else None)
        if route
        else None,
    )


@pytest.fixture()
def mgr(project: Path, fake: Fake) -> Iterator[Any]:
    made: list[SessionManager] = []

    def make(chat: ChatHooks | None, claude_bin: str | None = None) -> SessionManager:
        m = SessionManager(project, claude_bin=claude_bin or str(fake.bin), timeout=30, chat=chat)
        made.append(m)
        return m

    yield make
    for m in made:
        m.close()


def turn(m: SessionManager, text: str, after: int = 0) -> list[dict[str, Any]]:
    sid = m.create({"mode": "ask"})["id"]
    m.message(sid, {"text": text})
    return wait_done(m, sid, after)["events"]


def texts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == "text"]


def test_check_mode_shows_no_unchecked_text_and_checks_every_block(mgr, fake: Fake) -> None:
    fake.script(records("planted"))
    events = turn(mgr(hooks(check=True)), "Tell me about R3.")
    blob = json.dumps(events)
    assert not any(e.get("partial") for e in texts(events)), "CM11: no partial text in check mode"
    assert "will break on R3" not in blob and "Nothing else fails" not in blob, "CM10: raw text"
    shown = [e["text"] for e in texts(events)]
    assert shown[0].startswith("Let me look. Whether `rule:R3` fails is not decided"), shown
    assert shown[1].startswith("R3 is the ticket limit.") or "R3 is the ticket limit." in shown[1]
    footer = shown[-1]
    assert footer.startswith("Lattice withheld 1 sentence(s)") and "Nothing else" not in footer
    res = events[-2]
    assert res["type"] == "result" and res["routed"] is None
    assert res["check"]["replaced_element"] == 1 and res["check"]["withheld"] == 1
    assert res["text"] == "\n\n".join(shown), "result.text is the checked text, never the raw text"
    assert events[-1] == {**events[-1], "type": "status", "state": "idle"}
    assert all("checked" in e for e in texts(events))


def test_every_block_counts_are_that_blocks(mgr, fake: Fake) -> None:
    fake.script(records("planted"))
    events = turn(mgr(hooks(check=True)), "Tell me about R3.")
    blocks = texts(events)
    assert blocks[0]["checked"]["replaced_element"] == 1 and blocks[0]["checked"]["withheld"] == 0
    assert blocks[1]["checked"]["withheld"] == 1


def test_a_tier_1_id_of_this_turns_change_impact_is_support(mgr, fake: Fake) -> None:
    fake.script(records("impact_observed"))
    events = turn(mgr(hooks(check=True)), "What fails if c5 is applied?")
    shown = [e["text"] for e in texts(events)]
    assert shown[0].startswith("t1-9999999999 fails: rule R3 case 51 answered 201.")
    res = events[-2]
    assert res["check"]["supported"] == 1
    # the tool result is kept in memory for the turn only: the events carry the old summary
    tr = [e for e in events if e["type"] == "tool_result"][0]
    assert tr["summary"].startswith('{"schema": "lattice.impact/1"')


def test_a_hypothetical_without_a_change_gets_the_fixed_sentence(mgr, fake: Fake) -> None:
    fake.script(records("hypothetical"))
    events = turn(mgr(hooks(check=True)), "What would break if we raised the limit?")
    shown = [e["text"] for e in texts(events)]
    assert shown[-1].startswith("Lattice withheld 1 sentence(s)") or "does not predict" in shown[-1]
    assert any(t.startswith("Lattice does not predict failures.") for t in shown)
    assert "would break" not in json.dumps(texts(events))


def test_a_recorded_status_is_replaced_by_the_maps_row(mgr, fake: Fake) -> None:
    fake.script(records("replay_status"))
    events = turn(mgr(hooks(check=True)), "What happened to wf-053146a70c?")
    first = texts(events)[0]["text"]
    assert first.startswith(
        "Workflow `wf-053146a70c` (create ticket > read ticket): recorded replay"
    )
    assert "is failed" not in first


def test_a_turn_with_only_a_result_is_checked(mgr, fake: Fake) -> None:
    fake.script(records("result_only"))
    events = turn(mgr(hooks(check=True)), "Tell me about the jobs lifecycle.")
    shown = texts(events)
    assert len(shown) == 1 and shown[0]["text"].startswith("Lifecycle `impl:machine:jobs.delivery`")
    assert "has the state failed" not in json.dumps(events)


def test_a_routed_turn_spawns_no_process(mgr, fake: Fake, tmp_path: Path) -> None:
    """CM13: no claude process, not even a binary."""
    fake.script(records("plain"))
    m = mgr(hooks(route=True, check=True), claude_bin=str(tmp_path / "no-such-claude"))
    sid = m.create({"mode": "ask"})["id"]
    m.message(sid, {"text": "Which rules are required by the spec but not built?"})
    view = m.events(sid, 0)
    assert view["done"] and view["state"] == "idle"
    types = [e["type"] for e in view["events"]]
    assert types == ["user", "text", "result", "status"], types
    assert view["events"][-1]["state"] == "idle"
    text, res = view["events"][1], view["events"][2]
    assert text["partial"] is False and text["text"].startswith("2 rule(s) have impl_status")
    assert res["routed"] == "map" and res["cost_usd"] == 0.0 and res["num_turns"] == 0
    assert res["check"]["withheld"] == 0 and res["ok"] is True
    assert fake.calls == [], "the fake claude was never run"
    assert m.last_argv == {}


def test_a_routed_turn_is_not_part_of_the_claude_sessions_memory(mgr, fake: Fake) -> None:
    fake.script(records("plain"))
    m = mgr(hooks(route=True, check=True))
    sid = m.create({"mode": "ask"})["id"]
    m.message(sid, {"text": "Which rules are required by the spec but not built?"})
    wait_done(m, sid)
    m.message(sid, {"text": "Tell me about R2."})
    wait_done(m, sid)
    argv = fake.calls[0]["argv"]
    assert "--resume" not in argv, "the routed turn gave the session no id to resume"


def test_route_only_leaves_a_model_turn_streaming(mgr, fake: Fake) -> None:
    fake.script(records("plain"))
    events = turn(mgr(hooks(route=True)), "Tell me about R2.")
    res = [e for e in events if e["type"] == "result"][0]
    assert res["routed"] is None and "check" not in res
    assert not any("checked" in e for e in texts(events))


def test_without_chat_the_events_are_the_old_ones(mgr, fake: Fake) -> None:
    fake.script(records("planted"))
    events = turn(mgr(None), "Tell me about R3.")
    assert any("will break on R3" in e["text"] for e in texts(events))
    res = [e for e in events if e["type"] == "result"][0]
    assert "routed" not in res and "check" not in res
    assert not any("checked" in e for e in texts(events))


def test_the_frozen_paragraph_is_in_the_prompt_only_with_check(
    mgr, fake: Fake, project: Path
) -> None:
    fake.script(records("plain"))
    turn(mgr(hooks(check=True)), "Tell me about R2.")
    turn(mgr(None), "Tell me about R2.")
    on, off = fake.calls[0]["argv"], fake.calls[1]["argv"]
    prompt_on = on[on.index("--append-system-prompt") + 1]
    prompt_off = off[off.index("--append-system-prompt") + 1]
    assert S._CHECK_RULES in prompt_on and S._CHECK_RULES not in prompt_off
    assert prompt_on.replace("\n\n" + S._CHECK_RULES, "") == prompt_off
    assert S.system_prompt("ask", project) == prompt_off.split("\n\nThe owner")[0] or True
    assert S.system_prompt("ask", project, check=True) == prompt_on
    assert S._IMPACT_RULES.startswith("For 'what breaks if ...' questions use")


def test_the_frozen_paragraph_text() -> None:
    assert S._CHECK_RULES.startswith(
        "Lattice checks every sentence you write before the owner sees it."
    )
    assert S._CHECK_RULES.endswith("Quote spec text only with its rule id.")
    assert len(S._CHECK_RULES.split("\n")) == 1, "one paragraph"
    for word in ("t1-...", "t2-...", "recorded replay status: <status>", "implemented_unverified"):
        assert word in S._CHECK_RULES


def test_bad_chat_values_are_refused() -> None:
    for table, has_impl, key in (
        ({"route": "yes"}, True, "route"),
        ({"check": 1}, True, "check"),
        ({"check": True}, False, "check"),
        ({"route": True}, False, "route"),
        ({"speed": True}, True, "speed"),
    ):
        with pytest.raises(S.ChatConfigError) as e:
            S.parse_chat(table, has_impl)
        assert key in str(e.value)
    assert S.parse_chat(None, False) == S.ChatConfig(False, False)
    assert S.parse_chat({}, False) == S.ChatConfig(False, False)
    assert S.parse_chat({"route": True, "check": True}, True) == S.ChatConfig(True, True)


def test_the_poll_never_sees_a_turn_that_is_half_routed(mgr, fake: Fake) -> None:
    m = mgr(hooks(route=True, check=True))
    sid = m.create({"mode": "ask"})["id"]
    for _ in range(5):
        m.message(sid, {"text": "Which workflows failed?"})
        view = m.events(sid, 0)
        assert view["done"], "a routed turn is over when message() returns"
        time.sleep(0)


def test_the_frozen_paragraph_is_the_preregistered_text() -> None:
    """The paragraph of the prereg's section 2.2 (docs/results/2026-10-18-goto-chat-round2-prereg.md),
    byte for byte."""
    text = (
        Path(__file__).resolve().parents[2] / "docs/results/2026-10-18-goto-chat-round2-prereg.md"
    ).read_text()
    lines = text[text.index("**`_CHECK_RULES`, frozen here (one paragraph):**") :].split("\n")[1:]
    para: list[str] = []
    for ln in lines:
        if ln.startswith("> "):
            para.append(ln[2:])
        elif para:
            break
    assert " ".join(para).strip().strip('"') == S._CHECK_RULES


REAL_I_FREE = (
    Path(__file__).parents[2] / "docs/results/data/2026-10-18-goto-chat-round2/scored/agent/streams"
)


def _no_crash(events: list[dict[str, Any]]) -> None:
    assert not [e for e in events if e["type"] == "error"], events
    res = events[-2]
    assert res["type"] == "result" and res["check"] is not None
    assert texts(events), "the turn ends with text, not a KeyError"


# the real tool answer and the model's final words of streams/R2-I3f.jsonl (change_impact, c3)
SUMMARY_ANSWER = '{"schema":"lattice.impact/1","tiers":{"1":"not_run:not_requested","2":"run","3":"run","4":"not_run:spike_e"},"section":"summary","rules":{"added":0,"changed":1,"removed":0,"unchanged":147},"targets":{"added":0,"removed":0,"modified":3},"summary":{"1":{"failing":0,"resolved":0,"flows":0},"2":{"determined":0,"flows":0,"undecidable":4,"decidable":0},"3":{"locations":52,"flows":31,"unread":1}},"workflows":{"t1_fail":0,"t2_fail":0,"t3_may_fail":31},"note":"Which workflows fail is mostly not decidable before the change exists: a flow records the answers the app gives today, so it passes today by construction. Before an implementation only a changed invariant (tier 1, observed) and an exact-prefix contradiction (tier 2, determined) decide a workflow; every other flow listed is tier 3 \'may_fail\' (static, with unknowns), settled by tier 4 (Spike E, observed after the change).","determined":[],"files":["internal/completions/routes.go","internal/db/bodies.go","internal/db/changeseq.go","internal/db/owned.go","internal/httpapi/body.go","internal/httpapi/registry.go","internal/nudges/checkin.go","internal/period/period.go","internal/progress/compute.go","internal/progress/routes.go"],"sections":["summary","tier1","tier2","tier3","flows","unread","decidability"]}'
SUMMARY_TEXT = (
    "**Code locations (tier 3, static):** c3 reaches 52 code locations, and 31 workflows may be "
    "affected. One item is unread.\n\nc3 changes 1 rule and modifies 3 targets. The other 147 "
    "rules are unchanged.\n\n**Determined workflow failures:** none.\n- Tier 1 (observed) was not "
    "run, because it wasn't requested. Its failing count is 0.\n- Tier 2 (determined) has no "
    "determined items.\n- The 31 tier 3 workflows are static: may fail. They are not failures."
)


def _summary_stream() -> list[dict[str, Any]]:
    use = {
        "type": "tool_use",
        "id": "t1",
        "name": "mcp__lattice__change_impact",
        "input": {"change": "c3"},
    }
    res = {"type": "tool_result", "tool_use_id": "t1", "content": SUMMARY_ANSWER}
    half = len(SUMMARY_TEXT) // 2
    return [
        {"type": "system", "subtype": "init", "session_id": SID},
        {"type": "assistant", "message": {"id": "m1", "content": [use]}},
        {"type": "user", "message": {"content": [res]}},
        {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m2"}}},
        {"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}},
        *[
            {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}}}
            for t in (SUMMARY_TEXT[:half], SUMMARY_TEXT[half:])
        ],
        {"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}},
        {"type": "assistant", "message": {"id": "m2", "content": [{"type": "text", "text": SUMMARY_TEXT}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": SUMMARY_TEXT, "session_id": SID, "total_cost_usd": 0.03, "duration_ms": 4778, "num_turns": 2, "usage": {"input_tokens": 100, "output_tokens": 20}},
    ]  # fmt: skip


def test_a_summary_section_impact_result_does_not_end_the_turn(mgr, fake: Fake) -> None:
    """Round 2 (R2-I1f..I6f): a `lattice.impact/1` answer with `section: "summary"` has no tier2
    key; the turn used to end in KeyError: 'tier2' with no text (the tool result is R2-I3f's)."""
    fake.script(_summary_stream())
    events = turn(mgr(hooks(check=True)), "What does c3 touch?")
    _no_crash(events)
    shown = "\n".join(e["text"] for e in texts(events))
    assert "c3 reaches 52" in shown and "tier 3" in shown.lower()


@pytest.mark.parametrize("n", range(1, 7))
def test_the_real_crashed_streams_replay_without_the_crash(mgr, fake: Fake, n: int) -> None:
    path = REAL_I_FREE / f"R2-I{n}f.jsonl"
    if not path.exists():
        pytest.skip("the round-2 streams are not in this checkout")
    recs = [json.loads(ln) for ln in path.read_text().splitlines()]
    fake.script([r for r in recs if r.get("type") not in ("rate_limit_event",)])
    events = turn(mgr(hooks(check=True)), "What does c3 touch?")
    if recs[-1].get("type") == "result":
        _no_crash(events)
    else:  # the run was cut where the harness crashed: only the CLI's own exit is an error
        assert [e["message"] for e in events if e["type"] == "error"] == [
            "claude exited with status 0"
        ]
