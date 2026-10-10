"""The real Studio server (lattice/studio/server.py) over a tiny project: the incremental
toy's notes spec (tests/spec/incremental_toy.py) as a temporary git repo, with no app, or
with tests/studio/notes_app.py hooked up through studio.toml.

Scripted (ScriptedGenerator, injected through make_server) except the one live smoke
run, which runs only with LATTICE_LIVE_LLM=1 (the Claude Code CLI, haiku; never an API
key)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from lattice.llm.client import Generation, ScriptedGenerator
from lattice.spec.rules import Question, Trace, extract_rules
from lattice.studio import server as server_mod
from lattice.studio.server import BEGIN, COMMIT_TRAILER, END, make_server
from tests.spec.incremental_toy import (
    NOTES_BASE_MODULE,
    NOTES_BASE_TRACE,
    NOTES_CONTEXT,
    NOTES_V1,
    NOTES_V2,
)
from tests.spec.test_incremental import NEW_MODULE, NEW_TRACE
from tests.studio.test_web_mock import CHANGE_STATUSES, check_answer, check_change, check_model

# ---------------------------------------------------------------- the project


def git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


Q1 = {
    "id": "Q1",
    "text": "Is the 404 for another user's note also for ids that do not exist?",
    "default": "yes",
    "rules": ["R3"],
    "answer": None,
}
Q2 = {
    "id": "Q2",
    "text": "May a user edit a note to empty text?",
    "default": "no: 422",
    "rules": ["R6"],
    "answer": None,
}


def make_project(root: Path, *, app_limit: int | None = None, repo: bool = True) -> Path:
    """The toy notes project in `root`; with `app_limit`, the notes app (that limit)
    hooked up in studio.toml (layers CD)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "REQUIREMENTS.md").write_text(NOTES_V1)
    (root / "spec.py").write_text(NOTES_BASE_MODULE)
    trace = Trace(
        "REQUIREMENTS.md",
        extract_rules(NOTES_V1),
        {k: list(v) for k, v in NOTES_BASE_TRACE["links"].items()},
        questions=[Question(**Q1)],
    )
    (root / "spec.trace.json").write_text(trace.to_json())
    (root / "CONTEXT.md").write_text(NOTES_CONTEXT)
    toml = '[llm]\nmodel = "scripted"\ncontext = "CONTEXT.md"\n'
    if app_limit is not None:
        harness = f"studio_harness_{root.name}_{app_limit}"
        (root / f"{harness}.py").write_text(
            "from tests.studio.notes_app import build_db, login, notes_app\n\n"
            f"make_app = notes_app({app_limit})\n"
        )
        toml += (
            f'\n[app]\napp = "{harness}:make_app"\ndb = "{harness}:build_db"\n'
            f'login = "{harness}:login"\n\n[check]\nlayers = "CD"\n'
        )
    (root / "studio.toml").write_text(toml)
    if repo:
        git(root, "init", "-q")
        git(root, "config", "user.email", "studio@example.com")
        git(root, "config", "user.name", "Studio Test")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "init")
    return root


# ---------------------------------------------------------------- scripted answers


def edit_reply(md: str) -> str:
    return f"Here it is.\n{BEGIN}\n{md}{END}\n"


def draft_reply(module: str, trace: Mapping[str, Any]) -> str:
    return f"```python\n{module}```\n\n```json\n{json.dumps(trace, indent=2)}\n```\n"


CONFLICTING = """        Scenario("ben_reads_anns_note", given=ANN_NOTE, steps=(
            Step("GET", "/notes/10", "ben", expect_status=200),
        )),
    ),
)
"""
assert NEW_MODULE.endswith("    ),\n)\n")
CONFLICT_MODULE = NEW_MODULE[: -len("    ),\n)\n")] + CONFLICTING
CONFLICT_TRACE = {
    **NEW_TRACE,
    "links": {
        **NEW_TRACE["links"],
        "R3": ["scenario:read_own_and_other", "scenario:ben_reads_anns_note"],
    },
    "questions": [Q1, Q2],
}

V2_DRAFT = draft_reply(CONFLICT_MODULE, CONFLICT_TRACE)

# the follow-up: R3 restated, the conflicting scenario dropped
V3 = NOTES_V2.replace(
    "another user's note answers 404.", "another user's note always answers 404, even to read it."
)
assert V3 != NOTES_V2
V3_TRACE = {**NEW_TRACE, "retired": {}, "questions": [Q1, Q2]}
V3_DRAFT = draft_reply(NEW_MODULE, V3_TRACE)


class GatedGenerator:
    """Replays texts like ScriptedGenerator, but each call waits for `gate`."""

    def __init__(self, texts: Sequence[str], gate: threading.Event) -> None:
        self.inner = ScriptedGenerator(texts)
        self.gate = gate

    def generate(self, system: str, messages: Sequence[Mapping[str, str]]) -> Generation:
        assert self.gate.wait(30)
        return self.inner.generate(system, messages)


# ---------------------------------------------------------------- HTTP


def request(method: str, url: str, body: Any = None, raw: bytes | None = None) -> tuple[int, Any]:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def get_raw(url: str) -> tuple[int, str, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


class Served:
    def __init__(self, base: str, srv: Any, gen: Any) -> None:
        self.base, self.srv, self.gen = base, srv, gen

    def get(self, path: str) -> tuple[int, Any]:
        return request("GET", self.base + path)

    def post(self, path: str, body: Any = None, raw: bytes | None = None) -> tuple[int, Any]:
        return request("POST", self.base + path, body if body is not None else {}, raw)

    def delete(self, path: str) -> tuple[int, Any]:
        return request("DELETE", self.base + path)

    def wait_change(self, cid: str, limit: float = 60) -> dict[str, Any]:
        end = time.monotonic() + limit
        while True:
            status, c = self.get(f"/api/changes/{cid}")
            assert status == 200, c
            if c["status"] not in ("drafting", "checking") or time.monotonic() > end:
                return c
            time.sleep(0.05)

    def wait_check(self, limit: float = 60) -> dict[str, Any]:
        end = time.monotonic() + limit
        while True:
            _, m = self.get("/api/model")
            if not m["app"]["check_running"] and m["app"]["last_check"] is not None:
                return m
            assert time.monotonic() < end, "the check did not finish"
            time.sleep(0.05)


@pytest.fixture()
def serve() -> Iterator[Callable[..., Served]]:
    servers = []

    def start(project: Path, gen: Any = None) -> Served:
        gen = gen if gen is not None else ScriptedGenerator([])
        srv = make_server(project, 0, generator=gen)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return Served(f"http://127.0.0.1:{srv.server_address[1]}", srv, gen)

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def check_model_v11(m: dict[str, Any]) -> None:
    """check_model, with the v1.1 change statuses (cancelled) allowed."""
    changes = m["changes"]
    m = {**m, "changes": [c for c in changes if c["status"] != "cancelled"]}
    check_model(m)
    for c in changes:
        assert c["status"] in CHANGE_STATUSES | {"cancelled"}


# ---------------------------------------------------------------- the model


def test_model_matches_the_contract(tmp_path: Path, serve) -> None:
    s = serve(make_project(tmp_path / "p"))
    status, m = s.get("/api/model")
    assert status == 200
    check_model(m)
    assert m["project"]["name"] == "p" and m["project"]["git_head"] == git(
        tmp_path / "p", "rev-parse", "--short", "HEAD"
    )
    assert m["markdown"] == NOTES_V1
    assert [r["id"] for r in m["rules"]] == [f"R{i}" for i in range(1, 7)]
    r3 = m["rules"][2]
    assert r3["status"] == "checked" and r3["links"] == ["scenario:read_own_and_other"]
    assert r3["questions"] == ["Q1"]
    assert m["views"]["questions"][0]["id"] == "Q1"
    cells = m["views"]["access_groups"][0]["cells"]
    assert cells["ben|GET /notes/{}"] == "404" and cells["ann|POST /notes"] == "201/409/422"
    s6 = m["views"]["section6"]
    assert s6["invariants"] == [
        ["invariant:notes_at_most_3", "notes_at_most_3", "A user has at most 3 notes."]
    ]
    assert {t["name"] for t in m["data_model"]["tables"]} == {"User", "Note"}
    user = next(t for t in m["data_model"]["tables"] if t["name"] == "User")
    token = next(c for c in user["columns"] if c["name"] == "token")
    assert token == {
        "name": "token",
        "type": "str",
        "nullable": False,
        "one_of": None,
        "unique": True,
        "private": True,
        "bounds": None,
    }
    assert isinstance(m["app"].pop("session"), bool)  # v1.5: is the claude binary there
    assert m["app"] == {"configured": False, "check_running": False, "last_check": None}
    assert m["changes"] == []


def test_model_cache_follows_the_files(tmp_path: Path, serve) -> None:
    root = make_project(tmp_path / "p")
    s = serve(root)
    _, m1 = s.get("/api/model")
    _, m2 = s.get("/api/model")
    assert m1 == m2
    (root / "REQUIREMENTS.md").write_text(NOTES_V1 + "- One more rule.\n")
    _, m3 = s.get("/api/model")
    assert m3["markdown"].endswith("- One more rule.\n")


def test_data_model_bounds_from_contracts(tmp_path: Path) -> None:
    from lattice.spec.draft import load_spec

    module = NOTES_BASE_MODULE.replace(
        "from lattice.spec import Invariant, Scenario, Spec, Step",
        "from lattice.spec import Invariant, Scenario, Spec, Step\n"
        "from lattice.spec.model import Contract, ContractParam",
    ).replace(
        "SPEC = Spec(\n    profile=PROFILE,",
        "SPEC = Spec(\n    profile=PROFILE,\n    contracts=(Contract('POST', '/notes', "
        "'Authenticated', params=(ContractParam('text', min_len=1, max_len=200, "
        "column='Note.text'),)),),",
    )
    spec, errors = load_spec(module)
    assert spec is not None, errors
    note = next(t for t in server_mod.data_model(spec)["tables"] if t["name"] == "Note")
    assert next(c for c in note["columns"] if c["name"] == "text")["bounds"] == "1-200 chars"


# ---------------------------------------------------------------- ask


def _reply(kind: str, answer: str, citations: list[dict]) -> str:
    return json.dumps({"kind": kind, "answer": answer, "explanation": "x", "citations": citations})


BEN_404 = {
    "target": "scenario:read_own_and_other",
    "step": 2,
    "quote": "GET /notes/10 as ben -> 404",
}


def test_ask_with_a_valid_citation(tmp_path: Path, serve) -> None:
    gen = ScriptedGenerator([_reply("no", "No: ben gets 404.", [BEN_404])])
    s = serve(make_project(tmp_path / "p"), gen)
    status, a = s.post(
        "/api/ask",
        {
            "question": "Can ben read ann's note?",
            "selected": ["cell:ben|GET /notes/{}"],
            "verify": True,
        },
    )
    assert status == 200, a
    check_answer(a)
    assert a["kind"] == "no" and a["citations"][0]["target"] == "scenario:read_own_and_other"
    assert a["verified"] is None, "no app configured: no verification"
    prompt = gen.calls[0][1][0]["content"]
    assert "cell:ben|GET /notes/{}" in prompt and "rule:R3" in prompt


def test_ask_downgrades_an_ungrounded_answer(tmp_path: Path, serve) -> None:
    bad = _reply("yes", "Yes.", [{"target": "scenario:nope", "step": 1, "quote": "x"}])
    s = serve(make_project(tmp_path / "p"), ScriptedGenerator([bad, bad]))
    status, a = s.post("/api/ask", {"question": "Can ben delete ann's note?", "selected": []})
    assert status == 200
    check_answer(a)
    assert a["kind"] == "not_specified" and a["citations"] == []
    assert a["rejected_citations"][0]["target"] == "scenario:nope"
    assert a["usage"]["rounds"] == 2


def test_ask_verifies_against_the_configured_app(tmp_path: Path, serve) -> None:
    scenario = json.dumps(
        {
            "steps": [
                {
                    "method": "POST",
                    "path": "/notes",
                    "user": "ann",
                    "json": {"text": "hi"},
                    "expect_status": 201,
                },
                {"method": "GET", "path": "/notes/1", "user": "ben", "expect_status": 404},
            ]
        }
    )
    gen = ScriptedGenerator([_reply("no", "No: ben gets 404.", [BEN_404]), scenario])
    s = serve(make_project(tmp_path / "p", app_limit=3), gen)
    status, a = s.post("/api/ask", {"question": "Can ben read ann's note?", "verify": True})
    assert status == 200, a
    assert a["verified"]["status"] == "agrees", a["verified"]
    assert "GET /notes/1 as ben -> 404" in a["verified"]["scenario"]


def test_bad_input(tmp_path: Path, serve) -> None:
    s = serve(make_project(tmp_path / "p"))
    assert s.post("/api/ask", raw=b"not json")[0] == 400
    assert s.post("/api/ask", raw=b"[1, 2]")[0] == 400
    assert s.post("/api/ask", {"question": "  "})[0] == 422
    assert s.post("/api/changes", {"request": ""})[0] == 422
    assert s.get("/api/changes/c99")[0] == 404
    assert s.post("/api/changes/c99/accept")[0] == 404
    assert s.post("/api/changes/c99/reject")[0] == 404
    assert s.delete("/api/changes/c99")[0] == 404
    assert s.get("/api/nope")[0] == 404
    assert s.post("/api/changes", {"request": "x", "parent": "c42"})[0] == 404


# ---------------------------------------------------------------- changes


def test_change_lifecycle_to_ready(tmp_path: Path, serve) -> None:
    gen = ScriptedGenerator([edit_reply(NOTES_V2), V2_DRAFT])
    s = serve(make_project(tmp_path / "p"), gen)
    status, out = s.post(
        "/api/changes",
        {
            "request": "allow 5 notes, edit instead of delete",
            "selected": ["rule:R4", "cell:ann|DELETE /notes/{}"],
        },
    )
    assert status == 200 and re.fullmatch(r"c\d+", out["id"])
    c = s.wait_change(out["id"])
    assert c["status"] == "ready", c["error"]
    check_change(c)
    # the edit prompt shows what the user pointed at
    edit_prompt = gen.calls[0][1][0]["content"]
    assert "[rule:R4] A user has at most 3 notes" in edit_prompt
    assert "[cell:ann|DELETE /notes/{}] 204" in edit_prompt
    assert "Requests without a token" in gen.calls[1][1][0]["content"]  # draft_since's prompt

    assert c["rules"] == {
        "added": ["R6"],
        "changed": [{"old": "R4", "new": "R4"}],
        "removed": ["R6"],
    }
    assert c["markdown_diff"].startswith("--- a/REQUIREMENTS.md\n+++ b/REQUIREMENTS.md\n")
    assert "\n-- A user has at most 3 notes; a fourth answers 409.\n" in c["markdown_diff"]
    assert "+- A user has at most 5 notes; a sixth answers 409." in c["markdown_diff"]
    assert c["checks"]["valid"] and c["checks"]["problems"] == []
    assert c["checks"]["impact"] is None, "no app configured"
    [conflict] = c["checks"]["conflicts"]
    assert {conflict["a"], conflict["b"]} == {
        "scenario:read_own_and_other",
        "scenario:ben_reads_anns_note",
    }
    assert conflict["message"] == "ben GET /notes/{}: 404 vs 200"
    assert conflict["question"].endswith("Which one holds?")
    cells = {(x["row"], x["col"]): (x["old"], x["new"]) for x in c["view_diff"]["cells"]}
    assert cells[("ben", "GET /notes/{}")] == ("404", "200/404")
    assert cells[("ann", "DELETE /notes/{}")] == ("204", "·")
    assert cells[("ann", "PATCH /notes/{}")] == ("·", "200")
    inv = c["view_diff"]["invariants"]
    assert inv["added"] == [
        {
            "target": "invariant:notes_at_most_5",
            "name": "notes_at_most_5",
            "sentence": "A user has at most 5 notes.",
        }
    ]
    assert [x["target"] for x in inv["removed"]] == ["invariant:notes_at_most_3"]
    sc = c["view_diff"]["scenarios"]
    assert {x["target"] for x in sc["added"]} == {
        "scenario:the_sixth_note_is_refused",
        "scenario:edit_own_note",
        "scenario:ben_reads_anns_note",
    }
    edit = next(x for x in sc["added"] if x["name"] == "edit_own_note")
    assert edit["steps"][0].startswith("PATCH /notes/10 as ann -> 200")
    assert {x["target"] for x in sc["removed"]} == {
        "scenario:the_fourth_note_is_refused",
        "scenario:delete_own_note",
    }
    assert c["view_diff"]["machines"] == {"changed": []}
    assert "scenario:edit_own_note" in c["targets"]["added"]
    assert not any(t.startswith("contract:") for v in c["targets"].values() for t in v)
    assert c["questions"] == [{"id": "Q2", "text": Q2["text"], "default": Q2["default"]}]
    assert set(c["timings"]) >= {"edit_s", "draft_s", "lint_s", "total_s"}

    _, m = s.get("/api/model")
    check_model_v11(m)
    assert m["changes"] == [{"id": out["id"], "request": c["request"], "status": "ready"}]


def test_accept_writes_and_commits(tmp_path: Path, serve) -> None:
    root = make_project(tmp_path / "p")
    s = serve(root, ScriptedGenerator([edit_reply(NOTES_V2), V2_DRAFT]))
    _, out = s.post("/api/changes", {"request": "allow 5 notes", "selected": []})
    assert s.wait_change(out["id"])["status"] == "ready"
    before = git(root, "rev-parse", "HEAD")
    status, acc = s.post(f"/api/changes/{out['id']}/accept")
    assert status == 200, acc
    assert re.fullmatch(r"[0-9a-f]{40}", acc["commit"])
    assert git(root, "rev-parse", "HEAD") == acc["commit"] != before
    assert git(root, "rev-parse", "HEAD~1") == before
    msg = git(root, "log", "-1", "--format=%B")
    assert msg.startswith("spec: allow 5 notes\n\n") and msg.endswith(COMMIT_TRAILER)
    assert acc["message"].strip() == msg
    assert set(git(root, "show", "--name-only", "--format=", "HEAD").split()) == {
        "REQUIREMENTS.md",
        "spec.py",
        "spec.trace.json",
    }
    assert git(root, "status", "--porcelain") == ""
    assert (root / "REQUIREMENTS.md").read_text() == NOTES_V2
    assert "ben_reads_anns_note" in (root / "spec.py").read_text()
    trace = Trace.load(root / "spec.trace.json")
    assert trace.replaces == {"R4": "R4"} and "R6" in trace.retired
    _, m = s.get("/api/model")
    check_model_v11(m)
    assert m["project"]["git_head"] == acc["commit"][:7]
    assert m["markdown"] == NOTES_V2 and m["changes"][0]["status"] == "accepted"
    assert s.post(f"/api/changes/{out['id']}/accept")[0] == 409, "accepted once only"


def test_accept_a_stale_proposal_is_409(tmp_path: Path, serve) -> None:
    root = make_project(tmp_path / "p")
    s = serve(root, ScriptedGenerator([edit_reply(NOTES_V2), V2_DRAFT]))
    _, out = s.post("/api/changes", {"request": "allow 5 notes"})
    assert s.wait_change(out["id"])["status"] == "ready"
    (root / "REQUIREMENTS.md").write_text(NOTES_V1 + "- Edited by hand.\n")
    status, err = s.post(f"/api/changes/{out['id']}/accept")
    assert status == 409 and err["detail"] == "the spec changed since this proposal"
    assert (root / "REQUIREMENTS.md").read_text().endswith("- Edited by hand.\n")


def test_accept_outside_git_writes_the_files(tmp_path: Path, serve) -> None:
    root = make_project(tmp_path / "p", repo=False)
    s = serve(root, ScriptedGenerator([edit_reply(NOTES_V2), V2_DRAFT]))
    _, out = s.post("/api/changes", {"request": "allow 5 notes"})
    assert s.wait_change(out["id"])["status"] == "ready"
    status, acc = s.post(f"/api/changes/{out['id']}/accept")
    assert status == 200 and acc["commit"] is None and "not a git repository" in acc["note"]
    assert (root / "REQUIREMENTS.md").read_text() == NOTES_V2
    _, m = s.get("/api/model")
    assert m["project"]["git_head"] == ""


def test_reject(tmp_path: Path, serve) -> None:
    root = make_project(tmp_path / "p")
    s = serve(root, ScriptedGenerator([edit_reply(NOTES_V2), V2_DRAFT]))
    _, out = s.post("/api/changes", {"request": "allow 5 notes"})
    assert s.wait_change(out["id"])["status"] == "ready"
    assert s.post(f"/api/changes/{out['id']}/reject") == (200, {"ok": True})
    _, m = s.get("/api/model")
    assert m["changes"][0]["status"] == "rejected"
    assert s.post(f"/api/changes/{out['id']}/accept")[0] == 409
    assert (root / "REQUIREMENTS.md").read_text() == NOTES_V1


def test_cancel_a_drafting_change(tmp_path: Path, serve) -> None:
    gate = threading.Event()
    s = serve(make_project(tmp_path / "p"), GatedGenerator([edit_reply(NOTES_V2), V2_DRAFT], gate))
    _, out = s.post("/api/changes", {"request": "allow 5 notes"})
    status, c = s.get(f"/api/changes/{out['id']}")
    assert c["status"] == "drafting"
    assert s.delete(f"/api/changes/{out['id']}") == (200, {"ok": True, "status": "cancelled"})
    gate.set()
    time.sleep(0.5)
    _, c = s.get(f"/api/changes/{out['id']}")
    assert c["status"] == "cancelled"
    _, m = s.get("/api/model")
    check_model_v11(m)
    assert m["changes"][0]["status"] == "cancelled"
    assert s.delete(f"/api/changes/{out['id']}")[0] == 409
    assert s.post(f"/api/changes/{out['id']}/accept")[0] == 409


def test_a_bad_markdown_edit_fails_the_change(tmp_path: Path, serve) -> None:
    gen = ScriptedGenerator(["no markers here", edit_reply(NOTES_V1)])
    s = serve(make_project(tmp_path / "p"), gen)
    _, out = s.post("/api/changes", {"request": "allow 5 notes"})
    c = s.wait_change(out["id"])
    assert c["status"] == "failed"
    assert "no rule changed" in c["error"]
    assert "no document between" in gen.calls[1][1][-1]["content"]


def test_follow_up_drafts_on_the_parent_and_supersedes_it(tmp_path: Path, serve) -> None:
    root = make_project(tmp_path / "p")
    gen = ScriptedGenerator([edit_reply(NOTES_V2), V2_DRAFT, edit_reply(V3), V3_DRAFT])
    s = serve(root, gen)
    _, c1 = s.post("/api/changes", {"request": "allow 5 notes"})
    assert s.wait_change(c1["id"])["status"] == "ready"
    status, c2 = s.post(
        "/api/changes", {"request": "ben gets 404: drop the 200", "parent": c1["id"]}
    )
    assert status == 200
    c = s.wait_change(c2["id"])
    assert c["status"] == "ready", c["error"]
    assert c["parent"] == c1["id"]
    prompt = gen.calls[2][1][0]["content"]
    assert "follows up an earlier proposal" in prompt and "ben GET /notes/{}: 404 vs 200" in prompt
    assert NOTES_V2.splitlines()[5] in prompt, "the follow-up edits the parent's markdown"
    # drafted on top of the parent: only R3 changed against the parent's proposal
    assert c["rules"] == {"added": [], "changed": [{"old": "R3", "new": "R3"}], "removed": []}
    assert c["checks"]["conflicts"] == []
    assert [x["target"] for x in c["view_diff"]["scenarios"]["removed"]] == [
        "scenario:ben_reads_anns_note"
    ]
    _, p = s.get(f"/api/changes/{c1['id']}")
    assert p["status"] == "rejected" and p["superseded_by"] == c2["id"]
    assert s.post("/api/changes", {"request": "x", "parent": c1["id"]})[0] == 409
    status, acc = s.post(f"/api/changes/{c2['id']}/accept")
    assert status == 200, acc
    assert (root / "REQUIREMENTS.md").read_text() == V3
    assert "ben_reads_anns_note" not in (root / "spec.py").read_text()


def test_change_impact_with_the_configured_app(tmp_path: Path, serve) -> None:
    s = serve(
        make_project(tmp_path / "p", app_limit=3),
        ScriptedGenerator([edit_reply(NOTES_V2), V2_DRAFT]),
    )
    _, out = s.post("/api/changes", {"request": "allow 5 notes, edit instead of delete"})
    c = s.wait_change(out["id"])
    assert c["status"] == "ready", c["error"]
    check_change(c)
    imp = c["checks"]["impact"]
    assert imp["verdict"] == "fail"
    labels = {f["target"]: f["label"] for f in imp["failing"]}
    assert labels["scenario:edit_own_note"] == "not_built_yet"  # no PATCH in the app yet
    assert labels["scenario:ben_reads_anns_note"] == "contradiction"  # the L011 pair
    assert "impact_s" in c["timings"]


# ---------------------------------------------------------------- the full check


def test_check_without_an_app_reports_the_error(tmp_path: Path, serve) -> None:
    s = serve(make_project(tmp_path / "p"))
    assert s.post("/api/check") == (200, {"started": True})
    m = s.wait_check()
    lc = m["app"]["last_check"]
    assert "no app is configured" in lc["error"]
    check_model(m)


def test_check_with_the_configured_app_passes(tmp_path: Path, serve) -> None:
    s = serve(make_project(tmp_path / "p", app_limit=3))
    _, m = s.get("/api/model")
    assert m["app"]["configured"] is True and m["app"]["last_check"] is None
    assert s.post("/api/check") == (200, {"started": True})
    m = s.wait_check()
    check_model(m)
    lc = m["app"]["last_check"]
    assert lc["verdict"] == "pass" and lc["error"] is None and lc["layers"] == "CD"
    assert lc["counts"] == {"A": 0, "B": 0, "C": 0, "D": 0, "X": 0}
    assert lc["targets"]["scenario:delete_own_note"] == "pass"
    assert lc["targets"]["invariant:notes_at_most_3"] == "pass"


def test_check_maps_findings_to_targets(tmp_path: Path, serve) -> None:
    s = serve(make_project(tmp_path / "p", app_limit=5))
    s.post("/api/check")
    m = s.wait_check()
    check_model(m)
    lc = m["app"]["last_check"]
    assert lc["verdict"] == "fail"
    assert lc["counts"]["C"] == 1 and lc["counts"]["D"] == 1
    assert lc["targets"]["scenario:the_fourth_note_is_refused"] == "fail"
    assert lc["targets"]["invariant:notes_at_most_3"] == "fail"
    assert lc["targets"]["scenario:create_a_note"] == "pass"
    by_layer = {f["layer"]: f for f in lc["findings"]}
    assert by_layer["D"]["target"] == "scenario:the_fourth_note_is_refused"
    assert "expected 409" in by_layer["D"]["message"]
    assert by_layer["C"]["target"] == "invariant:notes_at_most_3"


# ---------------------------------------------------------------- static files


def test_static_files_and_the_traversal_guard(tmp_path: Path, serve) -> None:
    s = serve(make_project(tmp_path / "p"))
    status, ctype, body = get_raw(s.base + "/")
    assert status == 200 and ctype.startswith("text/html") and b'src="js/app.js"' in body
    status, ctype, _ = get_raw(s.base + "/js/api.js")
    assert status == 200 and ctype.startswith("text/javascript")
    status, ctype, _ = get_raw(s.base + "/css/app.css")
    assert status == 200 and ctype.startswith("text/css")
    for path in (
        "/../server.py",
        "/%2e%2e/server.py",
        "/js/../../API.md",
        "/%2e%2e/%2e%2e/pyproject.toml",
        "/no-such-file.js",
    ):
        assert get_raw(s.base + path)[0] == 404, path


def test_main_module_dispatch(monkeypatch) -> None:
    from lattice.studio import __main__ as entry
    from lattice.studio import mock_server

    seen: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(mock_server, "main", lambda argv: seen.append(("mock", argv)))
    monkeypatch.setattr(server_mod, "main", lambda argv: seen.append(("real", argv)) or 0)
    assert entry.main(["--mock", "--port", "1"]) == 0
    assert entry.main(["--mock", "/fx", "--port", "2"]) == 0
    assert entry.main(["proj", "--port", "3"]) == 0
    assert seen == [
        ("mock", ["--port", "1"]),
        ("mock", ["--fixtures", "/fx", "--port", "2"]),
        ("real", ["proj", "--port", "3"]),
    ]


def test_main_refuses_a_dir_without_the_project_files(tmp_path: Path, capsys) -> None:
    assert server_mod.main([str(tmp_path)]) == 2
    assert "has no REQUIREMENTS.md" in capsys.readouterr().err


# ---------------------------------------------------------------- the live smoke run


@pytest.mark.skipif(
    os.environ.get("LATTICE_LIVE_LLM") != "1", reason="live LLM run: set LATTICE_LIVE_LLM=1"
)
def test_live_haiku_ask_and_change(tmp_path: Path) -> None:
    from lattice.llm.cli_client import ClaudeCliClient

    root = make_project(tmp_path / "toy", app_limit=3)
    gen = ClaudeCliClient(model="haiku", max_tokens=32000, effort="", timeout=900, full_text=True)
    srv = make_server(root, 0, model="haiku", generator=gen)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s = Served(f"http://127.0.0.1:{srv.server_address[1]}", srv, gen)
    try:
        t = time.perf_counter()
        status, a = s.post(
            "/api/ask",
            {"question": "Can ben read a note that ann wrote?", "selected": [], "verify": True},
        )
        ask_s = round(time.perf_counter() - t, 2)
        assert status == 200, a
        status, out = s.post(
            "/api/changes",
            {
                "request": "a user may have at most 5 notes (a sixth answers 409), not 3",
                "selected": ["rule:R4"],
            },
        )
        assert status == 200
        c = s.wait_change(out["id"], limit=1800)
        record = {"ask_seconds": ask_s, "answer": a, "change": c}
        dest = Path(os.environ.get("LATTICE_LIVE_OUT", tmp_path)) / "studio_live.json"
        dest.write_text(json.dumps(record, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "ask": {k: a[k] for k in ("kind", "answer", "citations", "verified", "usage")},
                    "change": {
                        k: c[k]
                        for k in (
                            "status",
                            "error",
                            "rules",
                            "checks",
                            "timings",
                            "usage",
                            "markdown_diff",
                        )
                    },
                },
                indent=2,
            )
        )
        assert a["kind"] != "not_specified" and a["citations"], a
        assert c["status"] == "ready", c["error"]
        check_change(c)
    finally:
        srv.shutdown()
        srv.server_close()
