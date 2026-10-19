"""`[chat]` (API v1.26) on the real Studio server and the mock: the opt-in table and its refusals,
the map router and the claim checker on `POST /api/ask`, the pre-route of the session path on a real
server, and that the mock answers key for key what the real server does over the same fixture
project (probe PP2). With `[chat]` absent nothing changes (probe PB2)."""

# ruff: noqa: E501 -- expected outputs are data lines

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice.studio import mock_server as mock_mod
from lattice.studio import server as server_mod
from lattice.studio.mock_server import make_server as make_mock
from lattice.studio.session import ChatConfigError
from tests.studio.test_appmap_api import MOCK, Served, make_world
from tests.studio.test_server import request

ROUTED = [
    "Which rules are required by the spec but not built yet?",
    "Which rules are implemented but not verified?",
    "Is POST /api/login implemented?",
    "What would we need to touch if we remove GET /health?",
]


class ModelSays:
    """A scripted model whose every answer states a failure, so the checker has work to do."""

    def __init__(self, answer: str = "Yes, the app will break.") -> None:
        self.calls = 0
        self.answer = answer

    def generate(self, system: str, messages: Any) -> Any:
        from lattice.llm.client import Generation, Usage

        self.calls += 1
        text = json.dumps(
            {"kind": "yes", "answer": self.answer, "explanation": "R3 is static.",
             "citations": [{"target": "rule:R8", "step": None, "quote": "at most 50 tickets"}]}
        )  # fmt: skip
        return Generation(text=text, usage=Usage(0, 0), latency_s=0.0, stop_reason="end_turn")


def set_chat(world: dict[str, Path], body: str | None) -> None:
    """Append `[chat]` (or leave it out) to the world's studio.toml."""
    p = world["project"] / "studio.toml"
    text = p.read_text()
    if body is not None:
        text = text.replace('[llm]\nmodel = "scripted"\n',
                            '[llm]\nmodel = "scripted"\nclaude_bin = "/no/such/claude"\n')  # fmt: skip
        text += "\n[chat]\n" + body
    p.write_text(text)


@pytest.fixture()
def serve() -> Iterator[Any]:
    servers: list[Any] = []

    def start(world: dict[str, Path], gen: Any) -> Served:
        srv = server_mod.make_server(world["project"], 0, generator=gen)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return Served(f"http://127.0.0.1:{srv.server_address[1]}", srv, gen)

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def test_without_a_chat_table_ask_is_the_old_ask(tmp_path: Path, serve) -> None:
    w = make_world(tmp_path)
    gen = ModelSays()
    s = serve(w, gen)
    for q in ROUTED:
        status, body = s.post("/api/ask", {"question": q})
        assert status == 200 and body["answer"] == "Yes, the app will break."
        assert "map" not in body and "check" not in body
    assert gen.calls == len(ROUTED) - 0 or gen.calls >= 3


def test_route_answers_the_map_shapes_without_a_model(tmp_path: Path, serve) -> None:
    w = make_world(tmp_path)
    set_chat(w, "route = true\n")
    gen = ModelSays()
    s = serve(w, gen)
    status, body = s.post("/api/ask", {"question": ROUTED[0]})
    assert status == 200 and body["usage"] == {"seconds": 0, "tokens": 0}
    assert body["map"]["schema"] == "lattice.chatroute/1" and body["map"]["shape"] == "MR3"
    assert body["map"]["impl_status"] == "required_not_implemented"
    status, body = s.post("/api/ask", {"question": ROUTED[3]})
    assert body["kind"] == "not_specified" and body["map"]["shape"] == "MR6"
    assert gen.calls == 0
    # a question that names no shape follows the old path
    status, body = s.post("/api/ask", {"question": "Who may publish a project?"})
    assert body["answer"] == "Yes, the app will break." and "map" not in body
    assert gen.calls == 1


def test_the_impact_router_still_goes_first(tmp_path: Path, serve) -> None:
    w = make_world(tmp_path)
    set_chat(w, "route = true\ncheck = true\n")
    s = serve(w, ModelSays())
    status, body = s.post("/api/ask", {"question": "What would break if we raised the limit?"})
    assert status == 200 and "impact" in body and "map" not in body and "check" not in body


def test_check_replaces_and_marks_only_what_it_changed(tmp_path: Path, serve) -> None:
    w = make_world(tmp_path)
    set_chat(w, "check = true\n")
    s = serve(w, ModelSays())
    status, body = s.post("/api/ask", {"question": "Is a project limited?"})
    assert status == 200
    # a yes whose answer unit was withheld is downgraded to the fixed not_specified text
    assert body["kind"] == "not_specified"
    assert body["answer"] == "Not specified: the answer could not be grounded in the spec."
    assert body["check"]["withheld"] >= 1
    assert "will break" not in json.dumps(body)
    # an unchanged answer is byte-identical to the unchecked server's
    s2 = serve(w, ModelSays("Yes, a project has at most 50 tickets."))
    status, plain = s2.post("/api/ask", {"question": "Is a project limited?"})
    assert "check" not in plain and plain["kind"] == "yes"


def test_bad_chat_tables_refuse_to_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = {
        'route = "yes"\n': "route",
        "check = 3\n": "check",
        "speed = true\n": "speed",
    }
    for body, key in cases.items():
        w = make_world(tmp_path / key)
        set_chat(w, body)
        with pytest.raises(ChatConfigError) as err:
            server_mod.make_server(w["project"], 0)
        assert key in str(err.value)
        assert server_mod.main([str(w["project"]), "--port", "0", "--stub-llm"]) == 2
        err = capsys.readouterr().err
        assert err.startswith("studio: studio.toml: [chat]") and key in err
    w = make_world(tmp_path / "noimpl", implementation=False, impact=False)
    set_chat(w, "check = true\n")
    with pytest.raises(ChatConfigError, match=r"\[chat\] check needs an \[implementation\]"):
        server_mod.make_server(w["project"], 0)


def test_a_routed_session_turn_on_the_real_server(tmp_path: Path, serve) -> None:
    w = make_world(tmp_path)
    set_chat(w, "route = true\ncheck = true\n")
    s = serve(w, ModelSays())
    status, made = s.post("/api/session", {"mode": "ask"})
    assert status == 201
    sid = made["id"]
    status, _ = s.post(f"/api/session/{sid}/message", {"text": ROUTED[0]})
    assert status in (200, 202)
    status, view = s.get(f"/api/session/{sid}/events")
    assert view["done"] is True
    types = [e["type"] for e in view["events"]]
    assert types == ["user", "text", "result", "status"]
    res = view["events"][2]
    assert res["routed"] == "map" and res["num_turns"] == 0 and "check" in res


def test_the_mock_answers_key_for_key_what_the_real_server_does(tmp_path: Path, serve) -> None:
    w = make_world(tmp_path / "real")
    set_chat(w, "route = true\ncheck = true\n")
    s = serve(w, ModelSays())
    fixtures = tmp_path / "mockfix"
    shutil.copytree(mock_mod.FIXTURE_DIR, fixtures)
    toml = fixtures / "appmap" / "project" / "studio.toml"
    toml.write_text(toml.read_text() + "\n[chat]\nroute = true\ncheck = true\n")
    srv = make_mock(0, fixture_dir=fixtures)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        for q in ROUTED:
            real = s.post("/api/ask", {"question": q})[1]
            fake = request("POST", base + "/api/ask", {"question": q})[1]
            assert json.dumps(real, sort_keys=True) == json.dumps(fake, sort_keys=True), q
        # the session path: a routed turn
        made = request("POST", base + "/api/session", {"mode": "ask"})[1]
        request("POST", base + f"/api/session/{made['id']}/message", {"text": ROUTED[0]})
        view = request("GET", base + f"/api/session/{made['id']}/events")[1]
        assert [e["type"] for e in view["events"]] == ["user", "text", "result", "status"]
        assert view["events"][2]["routed"] == "map"
        # a model turn of the fixture is checked: no partial text
        made = request("POST", base + "/api/session", {"mode": "ask"})[1]
        request("POST", base + f"/api/session/{made['id']}/message", {"text": "Who may cancel?"})
        events: list[dict[str, Any]] = []
        for _ in range(10):
            view = request("GET", base + f"/api/session/{made['id']}/events")[1]
            events = view["events"]
            if view["done"]:
                break
        assert not any(e.get("partial") for e in events if e["type"] == "text")
        assert all("checked" in e for e in events if e["type"] == "text")
    finally:
        srv.shutdown()
        srv.server_close()
    assert (MOCK / "project" / "studio.toml").read_text().count("[chat]") == 0, (
        "the fixture is untouched"
    )


def test_the_committed_mock_has_no_chat_table() -> None:
    srv = make_mock(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        body = request("POST", base + "/api/ask", {"question": ROUTED[0]})[1]
        assert "map" not in body and "check" not in body
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_documented_shapes_are_the_real_ones(tmp_path: Path, serve) -> None:
    """PAPI2: the fenced JSON of `## v1.26` has exactly the keys of the real and the mock answers."""
    from tests.studio.test_appmap_api import API_MD, blocks_under, shape

    assert API_MD.read_text().startswith("# Lattice Studio API contract (v1.26)")
    ask_sample, text_sample, result_sample = blocks_under("## v1.26")
    w = make_world(tmp_path / "real")
    set_chat(w, "route = true\ncheck = true\n")
    s = serve(w, ModelSays())
    for q in ROUTED:
        shape(s.post("/api/ask", {"question": q})[1], {**ask_sample, "impact?": {"...": "..."}})
    sid = s.post("/api/session", {"mode": "ask"})[1]["id"]
    s.post(f"/api/session/{sid}/message", {"text": ROUTED[0]})
    events = s.get(f"/api/session/{sid}/events")[1]["events"]
    shape([e for e in events if e["type"] == "text"][0], text_sample)
    shape([e for e in events if e["type"] == "result"][0], result_sample)
    text = (API_MD.read_text().split("## v1.26")[1]).split("\n## v")[0]
    for word in (
        "route",
        "check",
        "MR1",
        "MR6",
        "D0",
        "E1",
        "E2",
        "E3",
        "routed",
        "checked",
        "[chat]",
    ):
        assert word in text, word
    assert "result.routed" in text and "byte-identical to v1.25" in text


def test_a_bad_chat_table_also_refuses_a_many_project_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = make_world(tmp_path / "good")
    bad = make_world(tmp_path / "bad")
    set_chat(bad, 'check = "sometimes"\n')
    code = server_mod.main([str(good["project"]), str(bad["project"]), "--port", "0", "--stub-llm"])
    assert code == 2
    assert "[chat] check must be true or false" in capsys.readouterr().err
