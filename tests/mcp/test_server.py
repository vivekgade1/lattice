"""The MCP server (lattice/mcp): protocol over a real subprocess pipe, the tools over the
toy notes project, stub generators only (never the Claude CLI or an API key)."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from lattice.llm.client import ScriptedGenerator
from lattice.mcp.server import McpServer
from lattice.mcp.tools import (
    HttpBackend,
    LocalBackend,
    Toolbox,
    ToolError,
    cap_result,
    norm_route,
)
from lattice.studio.server import make_server
from tests.spec.incremental_toy import NOTES_V2
from tests.studio.test_server import (
    BEN_404,
    V2_DRAFT,
    _reply,
    edit_reply,
    make_project,
)

READ_TOOLS = [
    "model_get",
    "rule_get",
    "spec_ask",
    "spec_lint",
    "code_links",
    "graph_get",
    "change_get",
    "change_list",
]
WRITE_TOOLS = ["check_run", "change_propose"]
FORBIDDEN = {"change_accept", "change_reject", "change_delete", "accept", "reject", "write_file"}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = make_project(tmp_path / "p")
    (p / "notes_routes.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n\n"
        '@app.get("/notes/{note_id}")\ndef read_note(note_id: int): ...\n'
    )
    return p


def rpc(project: Path, msgs: list[Any], *args: str) -> list[dict[str, Any]]:
    """Run the server, send `msgs` (one JSON per line), close stdin, return the replies."""
    lines = [m if isinstance(m, str) else json.dumps(m) for m in msgs]
    r = subprocess.run(
        [sys.executable, "-m", "lattice.mcp", "serve", "--project", str(project), *args],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    out = []
    for line in r.stdout.splitlines():
        out.append(json.loads(line))  # stdout carries only protocol messages
    return out


def req(i: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {"jsonrpc": "2.0", "id": i, "method": method}
    if params is not None:
        m["params"] = params
    return m


def call(i: int, name: str, **args: Any) -> dict[str, Any]:
    return req(i, "tools/call", {"name": name, "arguments": args})


def payload(resp: dict[str, Any]) -> Any:
    assert "result" in resp, resp
    return json.loads(resp["result"]["content"][0]["text"])


INIT = req(
    0,
    "initialize",
    {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t"}},
)
NOTE = {"jsonrpc": "2.0", "method": "notifications/initialized"}


# ---------------------------------------------------------------- protocol


def test_initialize_ping_list_and_notifications(project: Path) -> None:
    out = rpc(
        project,
        [INIT, NOTE, req(1, "ping"), req(2, "tools/list"), req(3, "nope/method")],
    )
    assert [o["id"] for o in out] == [0, 1, 2, 3]  # the notification got no reply
    init = out[0]["result"]
    assert init["protocolVersion"] == "2024-11-05"
    assert init["capabilities"]["tools"] == {"listChanged": False}
    assert out[1]["result"] == {}
    tools = out[2]["result"]["tools"]
    names = [t["name"] for t in tools]
    assert sorted(names) == sorted(READ_TOOLS + WRITE_TOOLS)
    assert not FORBIDDEN & set(names)
    for t in tools:
        assert t["inputSchema"]["type"] == "object" and t["description"]
    assert out[3]["error"]["code"] == -32601


def test_protocol_version_echo_and_fallback(project: Path) -> None:
    mk = lambda i, v: req(i, "initialize", {"protocolVersion": v})  # noqa: E731
    out = rpc(project, [mk(1, "2025-06-18"), mk(2, "2099-01-01"), mk(3, "2025-03-26")])
    assert [o["result"]["protocolVersion"] for o in out] == [
        "2025-06-18",
        "2024-11-05",
        "2025-03-26",
    ]


def test_parse_error_and_bad_requests(project: Path) -> None:
    out = rpc(
        project,
        ["{not json", json.dumps([1, 2]), json.dumps({"id": 9, "method": "ping"}), req(4, "ping")],
    )
    assert out[0]["error"]["code"] == -32700 and out[0]["id"] is None
    assert out[-1] == {"jsonrpc": "2.0", "id": 4, "result": {}}
    flat = [x for o in out[1:-1] for x in (o if isinstance(o, list) else [o])]
    assert any(x.get("error", {}).get("code") == -32600 for x in flat)


def test_tools_call_protocol_errors(project: Path) -> None:
    out = rpc(
        project,
        [
            req(1, "tools/call", {"name": "does_not_exist", "arguments": {}}),
            req(2, "tools/call", {"arguments": {}}),
            req(3, "tools/call", {"name": "model_get", "arguments": [1]}),
        ],
    )
    assert [o["error"]["code"] for o in out] == [-32602, -32602, -32602]


# ---------------------------------------------------------------- read tools


def test_read_tools_round_trip(project: Path) -> None:
    out = rpc(
        project,
        [
            INIT,
            call(1, "model_get"),
            call(2, "model_get", section="rules"),
            call(3, "rule_get", id="R3"),
            call(4, "spec_lint"),
            call(5, "code_links", target="rule:R3"),
            call(6, "graph_get", focus="R3", depth=1),
            call(7, "change_list"),
            call(8, "model_get", section="data_model"),
        ],
    )
    by = {o["id"]: o for o in out}
    for i in range(1, 9):
        assert by[i]["result"]["isError"] is False, by[i]
        assert by[i]["result"]["structuredContent"] == payload(by[i])
    overview = payload(by[1])
    assert overview["rule_count"] >= 4 and overview["rules"][0]["id"] == "R1"
    assert "markdown" not in overview and "views" not in overview
    assert {r["id"] for r in payload(by[2])["data"]} >= {"R1", "R3"}
    rule = payload(by[3])
    assert rule["id"] == "R3" and rule["status"] and "text" in rule
    assert rule["questions"] == ["Q1"] and rule["question_details"][0]["id"] == "Q1"
    assert isinstance(payload(by[4])["findings"], list)
    cl = payload(by[5])
    assert cl["source"] == "trace+grep" and cl["target"] == "rule:R3"
    assert any(x["path"] == "notes_routes.py" and x["line_from"] == 4 for x in cl["links"]), cl
    g = payload(by[6])
    assert g["focus"] == "rule:R3" and {n["id"] for n in g["nodes"]} >= {"rule:R3"}
    assert all(e["source"] in {n["id"] for n in g["nodes"]} for e in g["edges"])
    assert payload(by[7]) == {"count": 0, "changes": []}
    assert payload(by[8])["data"]["tables"]


def test_tool_failures_are_is_error_results(project: Path) -> None:
    out = rpc(
        project,
        [
            INIT,
            call(1, "rule_get", id="R999"),
            call(2, "rule_get"),
            call(3, "rule_get", id=5),
            call(4, "model_get", section="bogus"),
            call(5, "spec_ask", question="  "),
            call(6, "graph_get", depth=99),
            call(7, "graph_get", focus="nothing:here"),
            call(8, "code_links", target="rule:R999"),
            call(9, "change_get", id="c77"),
            call(10, "rule_get", id="R3", extra=1),
            call(11, "check_run", layers="Z"),
            call(12, "check_run"),  # no app configured in the toy project
            call(13, "spec_ask", question="x", selected="rule:R3"),
        ],
    )
    for o in out[1:]:
        r = o["result"]
        assert r["isError"] is True, o
        assert r["content"][0]["type"] == "text" and r["content"][0]["text"]
    texts = {o["id"]: o["result"]["content"][0]["text"] for o in out[1:]}
    assert "no rule" in texts[1] and "missing required" in texts[2]
    assert "unknown argument" in texts[10] and "no app is configured" in texts[12]


def test_readonly_hides_change_propose_and_check_run(project: Path) -> None:
    out = rpc(
        project,
        [
            INIT,
            req(1, "tools/list"),
            call(2, "change_propose", request="x"),
            call(3, "check_run"),
        ],
        "--readonly",
    )
    names = [t["name"] for t in out[1]["result"]["tools"]]
    assert sorted(names) == sorted(READ_TOOLS)
    assert out[2]["error"]["code"] == -32602 and out[3]["error"]["code"] == -32602


def test_serve_refuses_a_directory_without_the_project_files(tmp_path: Path) -> None:
    r = subprocess.run(
        [sys.executable, "-m", "lattice.mcp", "serve", "--project", str(tmp_path)],
        input="",
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 2 and "REQUIREMENTS.md" in r.stderr


# ---------------------------------------------------------------- LLM-backed tools (stubs)


def make_box(project: Path, gen: Any, **kw: Any) -> Toolbox:
    return Toolbox(LocalBackend(project, generator=gen), **kw)


def wait_change(box: Toolbox, cid: str) -> dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < 60:
        c = box.call("change_get", {"id": cid})
        if c["status"] not in ("drafting", "checking"):
            return c
        time.sleep(0.05)
    raise AssertionError("change did not finish")


def test_spec_ask_with_a_stub_generator(project: Path) -> None:
    gen = ScriptedGenerator([_reply("no", "No: ben gets 404.", [BEN_404])])
    box = make_box(project, gen)
    a = box.call(
        "spec_ask",
        {"question": "Can ben read ann's note?", "selected": ["rule:R3"], "verify": True},
    )
    assert a["kind"] == "no" and a["citations"][0]["target"] == "scenario:read_own_and_other"
    assert a["verified"] is None and "no app is configured" in a["verify_note"]
    assert "[rule:R3]" in gen.calls[0][1][0]["content"]


def test_change_propose_creates_a_pending_change_and_never_accepts(project: Path) -> None:
    gen = ScriptedGenerator([edit_reply(NOTES_V2), V2_DRAFT])
    box = make_box(project, gen)
    before = (project / "REQUIREMENTS.md").read_text()
    out = box.call("change_propose", {"request": "allow 5 notes", "selected": ["rule:R4"]})
    assert out["id"] == "c1" and out["status"] in ("drafting", "checking", "ready")
    c = wait_change(box, "c1")
    assert c["status"] == "ready", c.get("error")
    assert c["markdown_diff"] and c["rules"]["changed"] or c["rules"]["added"]
    assert box.call("change_list", {})["changes"] == [
        {"id": "c1", "request": "allow 5 notes", "status": "ready"}
    ]
    assert (project / "REQUIREMENTS.md").read_text() == before  # nothing written
    assert not FORBIDDEN & set(box.names())
    with pytest.raises(KeyError):
        box.call("change_accept", {"id": "c1"})
    with pytest.raises(ToolError):
        box.call("change_propose", {"request": " "})


def test_check_run_with_the_configured_app(tmp_path: Path) -> None:
    p = make_project(tmp_path / "p", app_limit=3)
    box = make_box(p, ScriptedGenerator([]))
    r = box.call("check_run", {"timeout": 120})
    assert r["verdict"] == "pass" and r["finding_count"] == 0 and r["failing_targets"] == []
    assert r["counts"] == {"A": 0, "B": 0, "C": 0, "D": 0, "X": 0}
    with pytest.raises(ToolError, match="timeout"):
        box.call("check_run", {"timeout": -1})


def test_http_backend_puts_the_change_in_a_running_studio(project: Path) -> None:
    gen = ScriptedGenerator([edit_reply(NOTES_V2), V2_DRAFT])
    srv = make_server(project, 0, generator=gen)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        box = Toolbox(HttpBackend(url, project))
        assert box.call("model_get", {})["rule_count"] >= 4
        out = box.call("change_propose", {"request": "allow 5 notes"})
        assert out["id"] == "c1"
        assert wait_change(box, "c1")["status"] == "ready"
        # the owner sees it in Studio's own records, still pending
        assert srv.studio.changes["c1"]["status"] == "ready"
        assert box.call("change_list", {})["changes"][0]["id"] == "c1"
        with pytest.raises(ToolError, match="404"):
            box.call("change_get", {"id": "c99"})
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------- output cap


def test_cap_result_truncates_with_a_flag() -> None:
    small, t = cap_result({"a": 1})
    assert small == '{"a":1}' and t is False
    big, t = cap_result({"rows": ["x" * 100] * 1000}, 20_000)
    assert t is True and len(big) <= 20_000
    env = json.loads(big)
    assert env["truncated"] is True and env["original_chars"] > 100_000 and env["preview"]


def test_server_caps_a_large_result(project: Path) -> None:
    box = make_box(project, ScriptedGenerator([]))
    srv = McpServer(box, cap=300)
    resp = srv.handle(call(1, "model_get", section="views"))
    assert resp is not None
    res = resp["result"]
    assert res["isError"] is False
    assert len(res["content"][0]["text"]) <= 300
    assert json.loads(res["content"][0]["text"])["truncated"] is True
    assert res["structuredContent"]["truncated"] is True


def test_norm_route() -> None:
    assert norm_route("post", "/a/1/runs/{run_id}/cancel/") == "POST /a/{}/runs/{}/cancel"
    assert norm_route("GET", "/x/:id?q=1") == "GET /x/{}"
