"""Studio chat sessions (lattice/studio/session.py) against a FAKE ``claude`` executable
that replays canned stream-json: argv, environment, event normalization, polling,
interrupt, errors; then the same routes on the real server and on the mock. The real CLI
and any API key are never involved."""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice.studio.mock_server import make_server as make_mock
from lattice.studio.server import make_server
from lattice.studio.session import (
    MODE_PROFILES,
    SessionError,
    SessionManager,
    Transcript,
    claude_available,
    tool_label,
)
from tests.studio.test_server import get_raw, make_project, request

FAKE = """#!{python}
import json, os, sys, time
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    env = {{k: v for k, v in os.environ.items() if k.startswith(("ANTHROPIC", "CLAUDE_CODE"))}}
    with open(log, "a") as f:
        f.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd(), "env": env,
                            "pythonpath": os.environ.get("PYTHONPATH", "")}}) + "\\n")
for line in open(os.environ["FAKE_CLAUDE_SCRIPT"]):
    rec = json.loads(line)
    if "_sleep" in rec:
        time.sleep(rec["_sleep"])
    elif "_exit" in rec:
        sys.stderr.write(rec.get("_stderr", ""))
        sys.exit(rec["_exit"])
    else:
        print(json.dumps(rec), flush=True)
"""

SID = "11111111-2222-3333-4444-555555555555"


def se(event: dict[str, Any]) -> dict[str, Any]:
    return {"type": "stream_event", "event": event}


def delta(text: str, index: int = 0) -> dict[str, Any]:
    return se({"type": "content_block_delta", "index": index,
               "delta": {"type": "text_delta", "text": text}})  # fmt: skip


def ask_turn(answer: str = "R47 says owners and admins.", sid: str = SID) -> list[dict[str, Any]]:
    half = len(answer) // 2
    return [
        {"type": "system", "subtype": "init", "session_id": sid},
        se({"type": "message_start", "message": {"id": "m1"}}),
        se({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}),
        delta(answer[:half]),
        delta(answer[half:]),
        se({"type": "content_block_stop", "index": 0}),
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": answer}]}},  # fmt: skip
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__lattice__spec_ask",
             "input": {"question": "who may cancel?"}}]}},  # fmt: skip
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "yes (R47)"}]}},  # fmt: skip
        {"type": "result", "subtype": "success", "is_error": False, "result": answer,
         "session_id": sid, "total_cost_usd": 0.05, "duration_ms": 1200, "num_turns": 2,
         "usage": {"input_tokens": 100, "output_tokens": 20}},
    ]  # fmt: skip


class Fake:
    """The fake claude binary of one test: set `.script(...)` per turn, read `.calls`."""

    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.bin = tmp / "claude"
        self.bin.write_text(FAKE.format(python=sys.executable))
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IXUSR)
        self.log = tmp / "calls.jsonl"
        self.path = tmp / "script.jsonl"
        monkeypatch.setenv("FAKE_CLAUDE_LOG", str(self.log))
        monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(self.path))

    def script(self, records: list[dict[str, Any]]) -> None:
        self.path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    @property
    def calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


@pytest.fixture()
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fake:
    return Fake(tmp_path, monkeypatch)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path / "proj", repo=False)


@pytest.fixture()
def mgr(project: Path, fake: Fake) -> Iterator[SessionManager]:
    m = SessionManager(project, model="sonnet", claude_bin=str(fake.bin), timeout=30)
    yield m
    m.close()


def wait_done(m: SessionManager, sid: str, after: int = 0, limit: float = 20) -> dict[str, Any]:
    end = time.monotonic() + limit
    while True:
        view = m.events(sid, after)
        if view["done"] and view["state"] != "starting":
            return view
        assert time.monotonic() < end, f"turn did not finish: {view}"
        time.sleep(0.02)


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


# ---------------------------------------------------------------- argv, env


def test_argv_per_mode(mgr: SessionManager, fake: Fake, project: Path, capsys) -> None:
    fake.script(ask_turn())
    shown = {}
    for mode in ("ask", "explain", "change"):
        sid = mgr.create({"mode": mode})["id"]
        mgr.message(sid, {"text": "hello -- there", "selected": ["rule:R47"]})
        wait_done(mgr, sid)
        argv = mgr.last_argv[sid]
        shown[mode] = argv
        assert argv[0] == str(fake.bin) and argv[1] == "-p"
        assert flag(argv, "--output-format") == "stream-json"
        assert "--verbose" in argv and "--include-partial-messages" in argv
        assert "--strict-mcp-config" in argv and "--resume" not in argv
        # the owner's user/project settings (plugins, hooks, skills) stay out
        assert flag(argv, "--setting-sources") == "" and "--disable-slash-commands" in argv
        assert flag(argv, "--model") == "sonnet"
        assert flag(argv, "--permission-mode") == "dontAsk"
        allowed = flag(argv, "--allowedTools").split(",")
        assert "mcp__lattice__spec_ask" in allowed and "Bash" not in allowed
        assert ("mcp__lattice__change_propose" in allowed) == (mode == "change")
        denied = flag(argv, "--disallowedTools").split(",")
        assert {"Edit", "Write", "Bash", "NotebookEdit"} <= set(denied)
        assert argv[-2:] == ["--", "hello -- there"]
        assert "rule:R47" in flag(argv, "--append-system-prompt")
        mcp = json.loads(Path(flag(argv, "--mcp-config")).read_text())
        srv = mcp["mcpServers"]["lattice"]
        assert srv["command"] == sys.executable
        assert srv["args"][:3] == ["-m", "lattice.mcp", "serve"]
        assert srv["args"][3:5] == ["--project", str(project.resolve())]
        assert ("--readonly" in srv["args"]) == (mode != "change")
        settings = json.loads(Path(flag(argv, "--settings")).read_text())
        hook = settings["hooks"]["PreToolUse"][0]
        assert hook["matcher"] == "Edit|Write|MultiEdit|NotebookEdit|Bash"
        assert "-m lattice.mcp.guard --project" in hook["hooks"][0]["command"]
    assert fake.calls[0]["cwd"] == str(project.resolve())
    for mode, argv in shown.items():
        print(f"\n[{mode}] " + " ".join(repr(a) if " " in a else a for a in argv))
    assert capsys.readouterr().out  # printed for the report (pytest -s)
    assert set(MODE_PROFILES) == {"ask", "explain", "change", "rebuild"}


def test_resume_on_second_turn(mgr: SessionManager, fake: Fake) -> None:
    fake.script(ask_turn())
    sid = mgr.create({"mode": "ask"})["id"]
    mgr.message(sid, {"text": "one"})
    first = wait_done(mgr, sid)
    assert first["claude_session_id"] == SID
    assert first["resume_command"] == f"claude --resume {SID}"
    mgr.message(sid, {"text": "two"})
    wait_done(mgr, sid, first["next"])
    argvs = [c["argv"] for c in fake.calls]
    assert "--resume" not in argvs[0]
    assert flag(argvs[1], "--resume") == SID
    assert argvs[1][-1] == "two"


def test_child_env_has_no_api_credentials(
    mgr: SessionManager, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SECRET")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    fake.script(ask_turn())
    sid = mgr.create({"mode": "ask"})["id"]
    mgr.message(sid, {"text": "hi"})
    wait_done(mgr, sid)
    env = fake.calls[0]["env"]
    assert not any(k.startswith("ANTHROPIC") for k in env)
    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "32000"
    assert str(Path(__file__).resolve().parents[2]) in fake.calls[0]["pythonpath"].split(os.pathsep)
    assert "sk-ant-SECRET" not in json.dumps(fake.calls)


# ---------------------------------------------------------------- events


def test_event_normalization_and_partial_merge(mgr: SessionManager, fake: Fake) -> None:
    fake.script(ask_turn("Owners and admins may cancel (R47)."))
    sid = mgr.create({"mode": "ask"})["id"]
    mgr.message(sid, {"text": "who cancels?", "selected": ["rule:R47"]})
    view = wait_done(mgr, sid)
    ev = view["events"]
    assert [e["seq"] for e in ev] == sorted(e["seq"] for e in ev)
    assert len({e["seq"] for e in ev}) == len(ev) and ev[0]["seq"] == 1
    assert all("ts" in e for e in ev)
    kinds = [e["type"] for e in ev]
    assert kinds[0] == "user" and ev[0]["selected"] == ["rule:R47"]
    texts = [e for e in ev if e["type"] == "text"]
    assert len(texts) == 1, "deltas are merged into one event per message"
    assert texts[0] == {**texts[0], "text": "Owners and admins may cancel (R47).",
                        "partial": False, "message_id": "m1"}  # fmt: skip
    use = next(e for e in ev if e["type"] == "tool_use")
    assert use["name"] == "mcp__lattice__spec_ask" and use["label"] == "Asked the spec"
    assert use["input_summary"] == "who may cancel?" and use["id"] == "t1"
    res = next(e for e in ev if e["type"] == "tool_result")
    assert res == {**res, "tool_use_id": "t1", "ok": True, "summary": "yes (R47)"}
    states = [e["state"] for e in ev if e["type"] == "status"]
    assert states == ["starting", "running", "idle"]
    result = next(e for e in ev if e["type"] == "result")
    assert result["ok"] is True and result["cost_usd"] == 0.05
    assert result["input_tokens"] == 100 and result["output_tokens"] == 20
    assert result["num_turns"] == 2 and result["claude_session_id"] == SID
    assert view["cost_usd_total"] == 0.05 and view["next"] == ev[-1]["seq"]
    print("\nevent samples:", json.dumps(ev[:3] + [use, res, result], indent=1))


def test_transcript_partial_text_replaces_in_place() -> None:
    t = Transcript()
    t.add_user("q", [])
    t.feed(se({"type": "message_start", "message": {"id": "m9"}}))
    t.feed(se({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}))
    t.feed(delta("Hel"))
    mid = t.view(0)
    assert [e["text"] for e in mid["events"] if e["type"] == "text"] == ["Hel"]
    cursor = mid["next"]
    t.feed(delta("lo"))
    later = t.view(cursor)
    # the poll after the cursor carries the whole latest text, once
    got = [e for e in later["events"] if e["type"] == "text"]
    assert len(got) == 1 and got[0]["text"] == "Hello" and got[0]["partial"] is True
    assert got[0]["seq"] > cursor
    assert len([e for e in t.view(0)["events"] if e["type"] == "text"]) == 1
    # without a streamed delta, the assistant record's text still appears
    t.feed({"type": "assistant", "message": {"id": "m10", "content": [
        {"type": "text", "text": "plain"}]}})  # fmt: skip
    assert t.view(0)["events"][-1]["text"] == "plain"


def test_cursor_polling(mgr: SessionManager, fake: Fake) -> None:
    fake.script(ask_turn())
    sid = mgr.create({"mode": "ask"})["id"]
    mgr.message(sid, {"text": "q"})
    view = wait_done(mgr, sid)
    assert mgr.events(sid, view["next"])["events"] == []
    first = mgr.events(sid, 2)["events"]
    assert first and first[0]["seq"] == 3
    fake.script(ask_turn("again"))
    mgr.message(sid, {"text": "q2"})
    nxt = wait_done(mgr, sid, view["next"])
    assert nxt["events"][0]["type"] == "user" and nxt["events"][0]["seq"] == view["next"] + 1


def test_tool_labels() -> None:
    assert tool_label("Read", {"file_path": "/p/src/runs.go"}) == "Read runs.go"
    assert tool_label("mcp__lattice__spec_ask", {}) == "Asked the spec"
    assert tool_label("mcp__lattice__change_propose", {}) == "Proposed a change"
    assert tool_label("Grep", {"pattern": "cancel"}) == "Searched for 'cancel'"
    assert tool_label("Grep", {"pattern": r"func \(a"}) == r"Searched for 'func \(a'"


# ---------------------------------------------------------------- control


def test_interrupt_and_409(mgr: SessionManager, fake: Fake) -> None:
    fake.script([{"type": "system", "subtype": "init", "session_id": SID}, {"_sleep": 60}])
    sid = mgr.create({"mode": "ask"})["id"]
    with pytest.raises(SessionError) as e:
        mgr.interrupt(sid)
    assert e.value.status == 409  # idle
    mgr.message(sid, {"text": "slow"})
    with pytest.raises(SessionError) as e:
        mgr.message(sid, {"text": "second"})
    assert e.value.status == 409
    end = time.monotonic() + 10
    while mgr.events(sid)["state"] != "running":
        assert time.monotonic() < end
        time.sleep(0.02)
    assert mgr.interrupt(sid)["state"] == "interrupted"
    view = wait_done(mgr, sid)
    assert view["state"] == "interrupted"
    assert [e["state"] for e in view["events"] if e["type"] == "status"][-1] == "interrupted"
    assert not any(e["type"] == "error" for e in view["events"])
    fake.script(ask_turn())
    mgr.message(sid, {"text": "again"})  # the session survives an interrupt
    assert wait_done(mgr, sid, view["next"])["state"] == "idle"


def test_error_kinds(mgr: SessionManager, fake: Fake) -> None:
    def run(records: list[dict[str, Any]]) -> dict[str, Any]:
        fake.script(records)
        sid = mgr.create({"mode": "ask"})["id"]
        mgr.message(sid, {"text": "q"})
        return wait_done(mgr, sid)

    limit = run([{"type": "result", "is_error": True, "subtype": "success",
                  "result": "You've hit your usage limit. Resets at 9pm.",
                  "session_id": SID}])  # fmt: skip
    err = next(e for e in limit["events"] if e["type"] == "error")
    assert err["kind"] == "usage_limit" and "usage limit" in err["message"]
    assert next(e for e in limit["events"] if e["type"] == "result")["ok"] is False

    crash = run([{"_stderr": "boom: bad flag\n", "_exit": 2}])
    err = next(e for e in crash["events"] if e["type"] == "error")
    assert err["kind"] == "cli" and "boom" in err["message"]

    quota = run([{"_stderr": "Claude AI usage limit reached\n", "_exit": 1}])
    assert next(e for e in quota["events"] if e["type"] == "error")["kind"] == "usage_limit"

    other = run([{"type": "result", "is_error": True, "result": "model overloaded",
                  "session_id": SID}])  # fmt: skip
    assert next(e for e in other["events"] if e["type"] == "error")["kind"] == "cli"


def test_timeout(project: Path, fake: Fake) -> None:
    m = SessionManager(project, claude_bin=str(fake.bin), timeout=0.5)
    try:
        fake.script([{"type": "system", "subtype": "init", "session_id": SID}, {"_sleep": 60}])
        sid = m.create({"mode": "ask"})["id"]
        m.message(sid, {"text": "q"})
        view = wait_done(m, sid)
        err = next(e for e in view["events"] if e["type"] == "error")
        assert err["kind"] == "timeout" and view["state"] == "idle"
    finally:
        m.close()


def test_validation_and_missing_binary(project: Path, mgr: SessionManager) -> None:
    for body, status in (
        ({}, 422),
        ({"mode": "nope"}, 422),
        ({"mode": "ask", "change": 3}, 422),
        ({"mode": "rebuild"}, 501),
    ):
        with pytest.raises(SessionError) as e:
            mgr.create(body)
        assert e.value.status == status
    sid = mgr.create({"mode": "ask"})["id"]
    for body in (
        {"text": "  "},
        {},
        {"text": "x", "selected": "rule:R1"},
        {"text": "x", "selected": [1]},
    ):
        with pytest.raises(SessionError) as e:
            mgr.message(sid, body)
        assert e.value.status == 422
    with pytest.raises(SessionError) as e:
        mgr.events("nope")
    assert e.value.status == 404
    missing = SessionManager(project, claude_bin="/nonexistent/claude")
    assert not missing.available() and not claude_available("definitely-not-a-claude-binary")
    try:
        s2 = missing.create({"mode": "ask"})["id"]
        with pytest.raises(SessionError) as e:
            missing.message(s2, {"text": "x"})
        assert e.value.status == 503
    finally:
        missing.close()  # removes its temp dir


# ---------------------------------------------------------------- the real server


@pytest.fixture()
def served(project: Path, fake: Fake) -> Iterator[str]:
    srv = make_server(project, 0, claude_bin=str(fake.bin))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def poll_http(base: str, sid: str, after: int = 0) -> dict[str, Any]:
    end = time.monotonic() + 20
    while True:
        status, view = request("GET", f"{base}/api/session/{sid}/events?after={after}")
        assert status == 200, view
        if view["done"] and view["state"] != "starting":
            return view
        assert time.monotonic() < end
        time.sleep(0.02)


def test_server_routes(served: str, fake: Fake) -> None:
    fake.script(ask_turn())
    status, s = request("POST", served + "/api/session", {"mode": "ask"})
    assert status == 201
    assert s["state"] == "idle" and s["claude_session_id"] is None and s["resume_command"] is None
    sid = s["id"]
    status, out = request("POST", f"{served}/api/session/{sid}/message", {"text": "who?"})
    assert (status, out) == (202, {"turn": 1})
    status, out = request("POST", f"{served}/api/session/{sid}/message", {"text": "again"})
    assert status in (409, 202)
    view = poll_http(served, sid)
    assert view["claude_session_id"] == SID and view["resume_command"].endswith(SID)
    assert any(e["type"] == "result" for e in view["events"])
    status, lst = request("GET", served + "/api/session")
    assert status == 200 and [x["id"] for x in lst] == [sid]
    assert request("GET", served + "/api/session/zz/events")[0] == 404
    assert request("GET", f"{served}/api/session/{sid}/events?after=x")[0] == 422
    assert request("POST", served + "/api/session", {"mode": "rebuild"})[0] == 501
    assert request("POST", served + "/api/session", {"mode": "x"})[0] == 422
    assert request("DELETE", f"{served}/api/session/{sid}")[0] == 409
    assert request("DELETE", f"{served}/api/session/zz")[0] == 404


def test_mcp_config_points_at_the_studio_server(served: str, fake: Fake, project: Path) -> None:
    fake.script(ask_turn())
    for mode in ("ask", "change"):
        sid = request("POST", served + "/api/session", {"mode": mode})[1]["id"]
        request("POST", f"{served}/api/session/{sid}/message", {"text": "q"})
        poll_http(served, sid)
        argv = fake.calls[-1]["argv"]
        args = json.loads(Path(flag(argv, "--mcp-config")).read_text())["mcpServers"]["lattice"][
            "args"
        ]
        assert flag(args, "--studio-url") == served
    # a manager that does not know its server's URL (no server around it) omits the flag
    m = SessionManager(project, claude_bin=str(fake.bin))
    try:
        sid = m.create({"mode": "ask"})["id"]
        m.message(sid, {"text": "q"})
        wait_done(m, sid)
        argv = fake.calls[-1]["argv"]
        args = json.loads(Path(flag(argv, "--mcp-config")).read_text())["mcpServers"]["lattice"][
            "args"
        ]
        assert "--studio-url" not in args
    finally:
        m.close()


def test_server_interrupt(served: str, fake: Fake) -> None:
    fake.script([{"type": "system", "subtype": "init", "session_id": SID}, {"_sleep": 60}])
    sid = request("POST", served + "/api/session", {"mode": "ask"})[1]["id"]
    assert request("POST", f"{served}/api/session/{sid}/message", {"text": "slow"})[0] == 202
    end = time.monotonic() + 10
    while request("GET", f"{served}/api/session/{sid}/events")[1]["state"] != "running":
        assert time.monotonic() < end
        time.sleep(0.02)
    assert request("POST", f"{served}/api/session/{sid}/message", {"text": "x"})[0] == 409
    status, out = request("DELETE", f"{served}/api/session/{sid}")
    assert status == 200 and out["state"] == "interrupted"
    assert poll_http(served, sid)["state"] == "interrupted"


def test_model_capability_flag(project: Path, fake: Fake) -> None:
    for binary, expected in ((str(fake.bin), True), ("/nonexistent/claude", False)):
        srv = make_server(project, 0, claude_bin=binary)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            status, m = request("GET", base + "/api/model")
            assert status == 200 and m["app"]["session"] is expected
        finally:
            srv.shutdown()
            srv.server_close()
    assert not fake.calls  # building the model never calls claude


def test_no_claude_call_at_import_or_start(project: Path, fake: Fake) -> None:
    srv = make_server(project, 0, claude_bin=str(fake.bin))
    try:
        assert srv.studio._sessions is None and fake.calls == []
    finally:
        srv.server_close()


# ---------------------------------------------------------------- the mock


@pytest.fixture()
def mock_base() -> Iterator[str]:
    srv = make_mock(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_mock_session_replay(mock_base: str) -> None:
    assert request("GET", mock_base + "/api/model")[1]["app"]["session"] is True
    status, s = request("POST", mock_base + "/api/session", {"mode": "ask"})
    assert status == 201 and s["state"] == "idle" and s["resume_command"] is None
    sid = s["id"]
    assert (
        request("POST", f"{mock_base}/api/session/{sid}/message", {"text": "who cancels?"})[0]
        == 202
    )
    assert request("POST", f"{mock_base}/api/session/{sid}/message", {"text": "x"})[0] == 409
    after, seen, polls = 0, [], 0
    while True:
        status, view = request("GET", f"{mock_base}/api/session/{sid}/events?after={after}")
        assert status == 200
        polls += 1
        seen += view["events"]
        after = view["next"]
        if view["done"]:
            break
        assert polls < 20
    assert polls >= 3
    types = [e["type"] for e in seen]
    assert types[0] == "user" and "tool_use" in types and "tool_result" in types
    assert types[-2:] == ["result", "status"] and seen[-1]["state"] == "idle"
    use = next(e for e in seen if e["type"] == "tool_use")
    assert use["name"] == "mcp__lattice__spec_ask" and use["label"] == "Asked the spec"
    final = [e for e in seen if e["type"] == "text"][-1]
    assert final["partial"] is False and "R47" in final["text"]
    assert view["claude_session_id"] and view["resume_command"].startswith("claude --resume ")
    assert view["cost_usd_total"] > 0
    assert [x["id"] for x in request("GET", mock_base + "/api/session")[1]] == [sid]


def test_mock_error_interrupt_and_rejections(mock_base: str) -> None:
    sid = request("POST", mock_base + "/api/session", {"mode": "ask"})[1]["id"]
    assert request("DELETE", f"{mock_base}/api/session/{sid}")[0] == 409
    request("POST", f"{mock_base}/api/session/{sid}/message", {"text": "what is the limit?"})
    view: dict[str, Any] = {}
    for _ in range(10):
        view = request("GET", f"{mock_base}/api/session/{sid}/events")[1]
        if view["done"]:
            break
    err = next(e for e in view["events"] if e["type"] == "error")
    assert err["kind"] == "usage_limit"
    request("POST", f"{mock_base}/api/session/{sid}/message", {"text": "again"})
    assert request("DELETE", f"{mock_base}/api/session/{sid}")[1]["state"] == "interrupted"
    assert request("POST", mock_base + "/api/session", {"mode": "rebuild"})[0] == 501
    assert request("POST", mock_base + "/api/session", {"mode": "?"})[0] == 422
    assert request("POST", f"{mock_base}/api/session/{sid}/message", {"text": ""})[0] == 422
    assert request("GET", mock_base + "/api/session/zz/events")[0] == 404
    assert request("POST", mock_base + "/api/session/zz/message", {"text": "x"})[0] == 404
    assert request("GET", f"{mock_base}/api/session/{sid}/events?after=-1")[0] == 422
    status, _, _ = get_raw(mock_base + "/api/session/nope/other")
    assert status == 404


# ---------------------------------------------------------------- recorded real transcripts

FIXTURES = Path(__file__).parent / "fixtures"


def _records(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines() if line]


def test_real_ask_transcript_normalizes() -> None:
    """A real Claude Code 2.1.296 Ask turn (redacted): hook/status/thinking_tokens system
    records, thinking blocks, one ``assistant`` record per content block, tool_use blocks
    streamed with empty input, rate_limit_event. Events must be: one tool_use per call
    (labelled), its tool_result, one final text merged from the deltas, the result."""
    t = Transcript()
    t.add_user("Who may cancel a run?", [])
    t.set_state("starting")
    for rec in _records("claude-2.1.296-ask.jsonl"):
        t.feed(rec)
    kinds = [e["type"] for e in t.events]
    assert kinds == ["user", "status", "status", "tool_use", "tool_use",
                     "tool_result", "tool_result", "text", "result"]  # fmt: skip
    tools = [e for e in t.events if e["type"] == "tool_use"]
    assert [e["name"] for e in tools] == ["mcp__lattice__spec_ask", "mcp__lattice__model_get"]
    assert [e["label"] for e in tools] == ["Asked the spec", "Read the model"]
    assert all(e["ok"] for e in t.events if e["type"] == "tool_result")
    text = next(e for e in t.events if e["type"] == "text")
    assert not text["partial"] and text["text"].startswith("Only an **app admin")
    assert text["message_id"].startswith("msg_")
    res = t.events[-1]
    assert res["ok"] and res["cost_usd"] > 0 and res["output_tokens"] > 0
    assert res["claude_session_id"] == t.claude_session_id
    assert t.view()["resume_command"] == f"claude --resume {t.claude_session_id}"


def test_real_guard_denial_is_a_failed_tool_result() -> None:
    """A real turn where the PreToolUse guard denied an Edit of spec.py: the hook's JSON
    deny comes back as an ``is_error`` tool_result carrying the change_propose hint."""
    t = Transcript()
    t.add_user("Add a comment line to spec.py", [])
    for rec in _records("claude-2.1.296-guard-deny.jsonl"):
        t.feed(rec)
    edit = next(e for e in t.events if e["type"] == "tool_use" and e["name"] == "Edit")
    res = next(e for e in t.events if e["type"] == "tool_result" and e["tool_use_id"] == edit["id"])
    assert res["ok"] is False and "Lattice spec file" in res["summary"]
    raw = (FIXTURES / "claude-2.1.296-guard-deny.jsonl").read_text()
    assert "hook error: Blocked: spec.py" in raw and "change_propose" in raw
    assert t.events[-1]["type"] == "result" and t.events[-1]["ok"]


def test_real_transcript_through_the_fake_binary(mgr: SessionManager, fake: Fake) -> None:
    fake.script(_records("claude-2.1.296-ask.jsonl"))
    sid = mgr.create({"mode": "ask"})["id"]
    mgr.message(sid, {"text": "Who may cancel a run?"})
    view = wait_done(mgr, sid)
    assert view["state"] == "idle" and view["claude_session_id"]
    assert [e["type"] for e in view["events"]].count("text") == 1
    assert view["cost_usd_total"] > 0
