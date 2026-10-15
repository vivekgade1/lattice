"""The improvement loop over HTTP (API v1.21): draft, verify, the apply job and discard on the
real Studio server and the mock. Synthetic git repositories under ``tmp_path`` (identity in the
repository config, the first nine events of the spike-3 log: one open config proposal
``imp-3e1cd7f9``); gates are shell one-liners. No LLM, no API key, no network, no Docker."""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import io
import json
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice.improve.__main__ import main as improve_main
from lattice.studio.mock_server import make_server as make_mock
from lattice.studio.server import make_hub_server, make_server

ROOT = Path(__file__).resolve().parents[2]
EVENTS = ROOT / "docs/results/data/2026-10-12-spike3/i3-events.jsonl"
ID = "imp-3e1cd7f9"
OTHER = "imp-9bade1d1"  # change:rejected: not in this log
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
BASE_TOML = '[project]\nname = "p"\n\n[rebuild]\ngates = ["true"]\nstop_grace = 1\n'
STATE_LINE = '\n[state]\ndir = "../state"\n'


@pytest.fixture(autouse=True)
def _git_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in GIT_ENV.items():
        monkeypatch.setenv(k, v)


def git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def synth(root: Path, toml: str = BASE_TOML, *, state: bool = True, name: str = "repo") -> Path:
    """A git repository at ``root/name`` holding a minimal Studio project; the state directory is
    ``root/state`` (the first nine events of the spike-3 log) unless `state` is false."""
    top = root / name
    top.mkdir(parents=True)
    git(top, "init", "-q", "-b", "main")
    git(top, "config", "core.autocrlf", "false")
    git(top, "config", "user.name", "Owner")
    git(top, "config", "user.email", "owner@example.com")
    text = toml + (STATE_LINE if state else "\n[state]\nenabled = false\n")
    (top / "studio.toml").write_text(text)
    (top / "REQUIREMENTS.md").write_text("# p\n\n- one rule.\n")
    (top / "spec.py").write_text("")
    (top / "spec.trace.json").write_text("{}")
    git(top, "add", "-A")
    git(top, "commit", "-qm", "init")
    if state:
        d = root / "state"
        d.mkdir(exist_ok=True)
        lines = EVENTS.read_text().splitlines()[:9]
        (d / "events.jsonl").write_text("".join(x + "\n" for x in lines))
    return top


class Srv:
    def __init__(self, srv: Any) -> None:
        self.srv = srv
        self.port = srv.server_address[1]
        self.thread = threading.Thread(target=srv.serve_forever, daemon=True)
        self.thread.start()
        self.closed = False

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
        json_type: bool = True,
        host: str | None = None,
    ) -> tuple[int, Any]:
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        h: dict[str, str] = {}
        if json_type:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        c.putrequest(method, path, skip_host=host is not None)
        if host is not None:
            c.putheader("Host", host)
        for k, v in h.items():
            c.putheader(k, v)
        c.putheader("Content-Length", str(len(data or b"")))
        c.endheaders(data)
        r = c.getresponse()
        out = r.read()
        c.close()
        return r.status, json.loads(out)

    def get(self, path: str) -> tuple[int, Any]:
        return self.call("GET", path)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.srv.shutdown()
            self.srv.server_close()


@pytest.fixture()
def start(tmp_path: Path) -> Iterator[Any]:
    started: list[Srv] = []

    def go(project: Path, *, allow: bool = True) -> Srv:
        s = Srv(make_server(project, allow_apply=allow))
        started.append(s)
        return s

    yield go
    for s in started:
        s.close()


def sha_of(srv: Srv, ident: str = ID, prefix: str = "") -> str:
    st, d = srv.get(f"{prefix}/api/improve/{ident}/draft")
    assert st == 200 and not d.get("refused"), d
    return hashlib.sha256(d["text"].encode()).hexdigest()


def apply_and_wait(
    srv: Srv, body: dict[str, Any] | None = None, prefix: str = ""
) -> dict[str, Any]:
    body = {"draft_sha256": sha_of(srv, prefix=prefix), "gates": False, **(body or {})}
    st, job = srv.call("POST", f"{prefix}/api/improve/{ID}/apply", body)
    assert st == 202 and job["status"] == "running", (st, job)
    st, done = srv.get(f"{prefix}/api/improve/{ID}/apply?wait=60")
    assert st == 200
    return done


def refs(top: Path) -> list[str]:
    return git(top, "for-each-ref", "--format=%(refname) %(objectname)").split("\n")


def tree_hash(top: Path) -> str:
    """The owner's checkout, index, config and HEAD: what an apply must not change."""
    parts = [git(top, "status", "--porcelain"), git(top, "ls-files", "-s")]
    for rel in ("HEAD", "config", "index", "packed-refs"):
        p = top / ".git" / rel
        parts.append(hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "-")
    return "\n".join(parts)


# ------------------------------------------------------------------ read routes


def cli_json(*argv: str) -> Any:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        improve_main([*argv, "--json"])
    return json.loads(out.getvalue())


def test_draft_and_verify_equal_the_cli(tmp_path: Path, start: Any) -> None:
    top = synth(tmp_path)
    srv = start(top)
    where = ["--project", str(top), "--state", str(tmp_path / "state")]
    for ident in (ID, "imp-00000000"):
        st, doc = srv.get(f"/api/improve/{ident}/draft")
        assert st == 200
        assert doc == cli_json("draft", ident, *where)
    assert doc["refused"] and doc["reason"] == "unknown_id"
    st, v = srv.get(f"/api/improve/{ID}/verify")
    assert st == 200 and v == cli_json("verify", ID, *where)
    assert v["refused"] and v["reason"] == "not_closed"
    at = "2026-10-01T12:00:00Z"
    st, v2 = srv.get(f"/api/improve/{ID}/verify?at={at}")
    assert st == 200 and v2 == cli_json("verify", ID, "--at", at, *where)
    # a bad `at` is the CLI's refusal, passed through
    st, v3 = srv.get(f"/api/improve/{ID}/verify?at=yesterday")
    assert st == 200 and v3 == cli_json("verify", ID, "--at", "yesterday", *where)


def test_a_closed_signal_gets_a_verdict_and_no_draft(tmp_path: Path, start: Any) -> None:
    top = synth(tmp_path)
    (tmp_path / "state" / "improvements.jsonl").write_text(
        json.dumps(
            {
                "id": "I-9",
                "signal": "gate:level_regression:X",
                "status": "closed",
                "commit": "abc1234",
                "ts": "2026-10-12T23:08:00Z",
            }
        )
        + "\n"
    )
    srv = start(top)
    st, d = srv.get(f"/api/improve/{ID}/draft")
    assert st == 200 and d["refused"] and d["reason"] == "closed"
    st, v = srv.get(f"/api/improve/{ID}/verify")
    assert st == 200 and not v.get("refused") and v["verdict"]
    where = ["--project", str(top), "--state", str(tmp_path / "state")]
    assert v == cli_json("verify", ID, *where)


def test_reads_write_nothing_and_create_no_state(tmp_path: Path, start: Any) -> None:
    top = synth(tmp_path)
    srv = start(top)
    before = {p: p.read_bytes() for p in (tmp_path / "state").rglob("*") if p.is_file()}
    dirs = sorted(str(p) for p in tmp_path.rglob("*") if p.is_dir() and ".git" not in p.parts)
    for path in (
        f"/api/improve/{ID}/draft",
        f"/api/improve/{ID}/verify",
        "/api/improve/applies",
        f"/api/improve/{ID}/apply",
    ):
        srv.get(path)
    after = {p: p.read_bytes() for p in (tmp_path / "state").rglob("*") if p.is_file()}
    assert before == after
    assert dirs == sorted(
        str(p) for p in tmp_path.rglob("*") if p.is_dir() and ".git" not in p.parts
    )


def test_other_methods_and_paths_are_404(tmp_path: Path, start: Any) -> None:
    srv = start(synth(tmp_path))
    for method, path in (
        ("POST", "/api/improve"),
        ("POST", f"/api/improve/{ID}/draft"),
        ("POST", f"/api/improve/{ID}/verify"),
        ("POST", "/api/improve/applies"),
        ("DELETE", f"/api/improve/{ID}/apply"),
        ("GET", f"/api/improve/{ID}/discard"),
        ("GET", f"/api/improve/{ID}/draft/x"),
        ("GET", f"/api/improve/{ID}"),
    ):
        st, d = srv.call(method, path, {} if method != "GET" else None)
        assert (st, d) == (404, {"detail": "no such route"}), (method, path, st, d)


def test_ids_are_validated(tmp_path: Path, start: Any) -> None:
    srv = start(synth(tmp_path))
    for ident in ("a%20b", "a%2Fb", "..", "-x", "x" * 65, "%2e%2e"):
        for path in (
            f"/api/improve/{ident}/draft",
            f"/api/improve/{ident}/verify",
            f"/api/improve/{ident}/apply",
        ):
            st, d = srv.get(path)
            assert (st, d.get("code")) == (422, "bad_id"), (path, st, d)
    st, d = srv.get(f"/api/improve/{ID}/apply?wait=99")
    assert st == 422 and d["detail"] == "wait must be a number of seconds, 0 to 60"
    st, d = srv.get(f"/api/improve/{ID}/apply?wait=x")
    assert st == 422


def test_applies_listing_when_off_and_on(tmp_path: Path, start: Any) -> None:
    top = synth(tmp_path)
    off = start(top, allow=False)
    st, d = off.get("/api/improve/applies")
    assert st == 200
    assert d == {
        "format": 1,
        "enabled": False,
        "why_disabled": "start Studio with --allow-apply",
        "applies": [],
        "running": None,
        "problems": [],
    }
    off.close()
    on = start(top)
    st, d = on.get("/api/improve/applies")
    assert d["enabled"] is True and d["why_disabled"] is None


def test_no_state_project_is_not_enabled(tmp_path: Path, start: Any) -> None:
    top = synth(tmp_path, state=False)
    srv = start(top)
    st, d = srv.get("/api/improve/applies")
    assert d["enabled"] is False and "durable state" in d["why_disabled"]
    st, d = srv.call("POST", f"/api/improve/{ID}/apply", {"draft_sha256": "0" * 64})
    assert (st, d["code"]) == (409, "no_state")
    st, d = srv.call("POST", f"/api/improve/{ID}/discard", {})
    assert (st, d["code"]) == (409, "no_state")


# ------------------------------------------------------------------ guards


def snap(top: Path, state: Path) -> tuple[str, list[str]]:
    files = sorted(
        f"{p}:{hashlib.sha256(p.read_bytes()).hexdigest()}" for p in state.rglob("*") if p.is_file()
    )
    return tree_hash(top), files


def test_the_guard_ladder(tmp_path: Path, start: Any) -> None:
    top = synth(tmp_path)
    state = tmp_path / "state"
    off = start(top, allow=False)
    sha = sha_of(off)
    before = snap(top, state)
    st, d = off.call("POST", f"/api/improve/{ID}/apply", {"draft_sha256": sha})
    assert (st, d["code"]) == (403, "apply_disabled")
    st, d = off.call("POST", f"/api/improve/{ID}/discard", {})
    assert (st, d["code"]) == (403, "apply_disabled")
    # the flag-off check comes before every other guard, the body included
    st, d = off.call("POST", f"/api/improve/{ID}/apply", raw=b"[]", json_type=False)
    assert (st, d["code"]) == (403, "apply_disabled")
    off.close()
    srv = start(top)
    p = srv.port
    url = f"/api/improve/{ID}/apply"
    body = {"draft_sha256": sha, "gates": False}
    rows = [
        (srv.call("POST", url, body, headers={"Origin": "http://evil.example"}), 403, "bad_origin"),
        (srv.call("POST", url, body, host=f"evil.example:{p}"), 403, "bad_origin"),
        (srv.call("POST", url, body, host="127.0.0.1"), 403, "bad_origin"),
        (srv.call("POST", url, body, headers={"Origin": "null"}), 403, "bad_origin"),
        (
            srv.call("POST", url, body, headers={"Origin": f"http://localhost:{p}"}),
            403,
            "bad_origin",
        ),
        (
            srv.call(
                "POST",
                url,
                raw=json.dumps(body).encode(),
                json_type=False,
                headers={"Content-Type": "text/plain"},
            ),
            415,
            "json_required",
        ),
        (
            srv.call("POST", url, raw=json.dumps(body).encode(), json_type=False),
            415,
            "json_required",
        ),
        (srv.call("POST", url, raw=b"[]"), 400, None),
        (srv.call("POST", url, raw=b"not json"), 400, None),
        (srv.call("POST", "/api/improve/../apply", body), 422, "bad_id"),
        (srv.call("POST", "/api/improve/a%2Fb/apply", body), 422, "bad_id"),
        (srv.call("POST", url, {}), 422, "bad_body"),
        (srv.call("POST", url, {"draft_sha256": "A" * 64}), 422, "bad_body"),
        (srv.call("POST", url, {"draft_sha256": sha, "gates": "yes"}), 422, "bad_body"),
        (srv.call("POST", url, {"draft_sha256": sha, "branch": ""}), 422, "bad_body"),
        (srv.call("POST", url, {"draft_sha256": sha, "branch": "x" * 201}), 422, "bad_body"),
        (srv.call("POST", url, {"draft_sha256": sha, "branch": 5}), 422, "bad_body"),
        (srv.call("POST", url, {"draft_sha256": "0" * 64}), 409, "draft_changed"),
        (
            srv.call("POST", f"/api/improve/{OTHER}/apply", {"draft_sha256": "0" * 64}),
            409,
            "draft_changed",
        ),
    ]
    for (st, d), want_st, want_code in rows:
        assert st == want_st, (st, d)
        assert d.get("code") == want_code, (st, d)
    # the 409 carries the current draft's hash, or null when the draft is refused
    assert rows[-2][0][1]["draft_sha256"] == sha
    assert rows[-1][0][1]["draft_sha256"] is None
    # a 404 for an unknown project, and nothing was written by any refusal
    assert snap(top, state) == before
    assert git(top, "branch", "--list", "improve/*") == ""
    # the control: the right Origin passes every guard
    st, d = srv.call("POST", url, body, headers={"Origin": f"http://127.0.0.1:{p}"})
    assert st == 202, d
    st, d = srv.get(f"{url}?wait=60")
    assert d["exit_code"] == 0


def test_one_apply_or_discard_at_a_time(tmp_path: Path, start: Any) -> None:
    top = synth(tmp_path, BASE_TOML.replace('["true"]', '["sleep 4"]'))
    srv = start(top)
    sha = sha_of(srv)
    st, d = srv.call("POST", f"/api/improve/{ID}/apply", {"draft_sha256": sha})
    assert st == 202
    st, d = srv.call("POST", f"/api/improve/{ID}/apply", {"draft_sha256": sha})
    assert (st, d["code"]) == (409, "apply_running")
    st, d = srv.call("POST", f"/api/improve/{ID}/discard", {})
    assert (st, d["code"]) == (409, "apply_running")
    st, lst = srv.get("/api/improve/applies")
    assert lst["running"]["id"] == ID
    st, done = srv.get(f"/api/improve/{ID}/apply?wait=60")
    assert done["status"] == "done" and done["exit_code"] == 0
    assert done["result"]["gates"]["status"] == "passed"
    st, lst = srv.get("/api/improve/applies")
    assert lst["running"] is None and [a["id"] for a in lst["applies"]] == [ID]


# ------------------------------------------------------------------ apply and discard


def test_apply_without_gates_makes_a_branch_and_changes_nothing_else(
    tmp_path: Path, start: Any
) -> None:
    top = synth(tmp_path)
    srv = start(top)
    st, ev0 = srv.get("/api/improve")
    events0 = (tmp_path / "state" / "events.jsonl").read_bytes()
    owner, refs0 = tree_hash(top), refs(top)
    done = apply_and_wait(srv)
    assert done["status"] == "done" and done["source"] == "job" and done["exit_code"] == 0
    rec = done["result"]
    assert rec["edit"]["action"] == "inserted"
    assert rec["edit"]["line"] == 'level_gate_args = ["--x-repeats", "2"]'
    assert rec["gates"]["status"] == "skipped" and rec["branch"] == f"improve/{ID}"
    assert done["record"] == json.loads((tmp_path / "state" / "applies" / f"{ID}.json").read_text())
    assert done["options"] == {"gates": False, "branch": None}
    # the owner's checkout, index and refs but the new branch are untouched; nothing merged
    assert git(top, "branch", "--list", "--format=%(refname:short)").split() == [
        "improve/" + ID,
        "main",
    ]
    assert git(top, "log", "--oneline", "main").count("\n") == 0
    assert (top / "studio.toml").read_text().count("level_gate_args") == 0
    assert tree_hash(top) == owner
    assert [r for r in refs(top) if r not in refs0] == [
        f"refs/heads/improve/{ID} {done['result']['commit']['sha']}"
    ]
    # Studio's state dir is the CLI's default here: the next commands carry no --state
    assert all("--state" not in s["command"] for s in done["result"]["next"])
    # Studio logged nothing: the event log and GET /api/improve are what they were
    assert (tmp_path / "state" / "events.jsonl").read_bytes() == events0
    st, ev1 = srv.get("/api/improve")
    assert ev1 == ev0


def test_apply_with_a_failing_gate_keeps_the_branch(tmp_path: Path, start: Any) -> None:
    top = synth(tmp_path, BASE_TOML.replace('["true"]', '["true", "false"]'))
    srv = start(top)
    done = apply_and_wait(srv, {"gates": True})
    assert done["exit_code"] == 1
    g = done["result"]["gates"]
    assert g["status"] == "failed" and [r["passed"] for r in g["results"]] == [True, False]
    assert git(top, "rev-parse", "--verify", f"improve/{ID}")


def test_a_refusal_inside_apply_is_a_done_job_with_exit_3(tmp_path: Path, start: Any) -> None:
    top = synth(tmp_path)
    srv = start(top)
    done = apply_and_wait(srv)
    assert done["exit_code"] == 0
    again = apply_and_wait(srv)
    assert again["exit_code"] == 3 and again["result"]["reason"] == "already_applied"
    assert again["result"]["refused"] is True
    # the earlier record is still the live one
    st, lst = srv.get("/api/improve/applies")
    assert [a["id"] for a in lst["applies"]] == [ID]


def test_dirty_checkout_is_refused_with_nothing_written(tmp_path: Path, start: Any) -> None:
    top = synth(tmp_path)
    (top / "REQUIREMENTS.md").write_text("# edited\n")
    srv = start(top)
    before = tree_hash(top)
    done = apply_and_wait(srv)
    assert done["exit_code"] == 3 and done["result"]["reason"] == "dirty_checkout"
    assert tree_hash(top) == before
    assert not (tmp_path / "state" / "applies").exists() or not list(
        (tmp_path / "state" / "applies").iterdir()
    )


def test_discard_then_not_applied_then_merged(tmp_path: Path, start: Any) -> None:
    top = synth(tmp_path)
    srv = start(top)
    apply_and_wait(srv)
    wt = tmp_path / "repo.improve" / ID
    assert wt.is_dir()
    st, d = srv.call("POST", f"/api/improve/{ID}/discard", {})
    assert st == 200 and d["exit_code"] == 0 and d["command"] == "discard" and d["id"] == ID
    assert not wt.exists()
    assert git(top, "branch", "--list", "improve/*") == ""
    kept = tmp_path / "state" / "applies" / f"{ID}.discarded-1.json"
    assert kept.is_file() and not (tmp_path / "state" / "applies" / f"{ID}.json").exists()
    st, lst = srv.get("/api/improve/applies")
    assert lst["applies"] == []  # a discarded record is never listed
    st, d = srv.get(f"/api/improve/{ID}/apply")
    assert (st, d["code"]) == (404, "not_applied")
    st, d = srv.call("POST", f"/api/improve/{ID}/discard", {})
    assert st == 200 and d["exit_code"] == 3 and d["result"]["reason"] == "not_applied"
    # merged: another branch holds the commit
    done = apply_and_wait(srv)
    git(top, "branch", "keep", done["result"]["commit"]["sha"])
    st, d = srv.call("POST", f"/api/improve/{ID}/discard", {})
    assert d["exit_code"] == 3 and d["result"]["reason"] == "merged"
    assert (tmp_path / "state" / "applies" / f"{ID}.json").is_file()


def test_a_restart_answers_from_the_record(tmp_path: Path, start: Any) -> None:
    top = synth(tmp_path)
    srv = start(top)
    apply_and_wait(srv)
    srv.close()
    again = start(top)
    st, d = again.get(f"/api/improve/{ID}/apply")
    assert st == 200
    assert d["source"] == "record" and d["status"] == "done" and d["exit_code"] is None
    assert d["stale"] is False and d["result"] is None and d["record"]["branch"] == f"improve/{ID}"
    # a record that says the gates are running (a crashed Studio) is stale
    path = tmp_path / "state" / "applies" / f"{ID}.json"
    rec = json.loads(path.read_text())
    rec["gates"]["status"] = "running"
    path.write_text(json.dumps(rec))
    st, d = again.get(f"/api/improve/{ID}/apply")
    assert d["stale"] is True
    st, lst = again.get("/api/improve/applies")
    assert lst["applies"][0]["stale"] is True and lst["applies"][0]["gates"] == "running"
    # the owner can discard it
    st, d = again.call("POST", f"/api/improve/{ID}/discard", {})
    assert d["exit_code"] == 0


def test_closing_the_server_interrupts_the_gates(tmp_path: Path) -> None:
    marker = "29.0173"
    top = synth(tmp_path, BASE_TOML.replace('["true"]', f'["sleep {marker}"]'))
    s = Srv(make_server(top, allow_apply=True))
    sha = sha_of(s)
    st, d = s.call("POST", f"/api/improve/{ID}/apply", {"draft_sha256": sha})
    assert st == 202
    t0 = time.monotonic()
    while time.monotonic() - t0 < 20:  # the gate is running once its process is there
        ps = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
        if f"sleep {marker}" in ps:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("the gate never started")
    t1 = time.monotonic()
    s.srv.shutdown()
    s.srv.server_close()
    s.closed = True
    assert time.monotonic() - t1 < 1 + 10 + 5  # stop_grace 1 + 10 s, plus slack
    rec = json.loads((tmp_path / "state" / "applies" / f"{ID}.json").read_text())
    assert rec["gates"]["status"] == "interrupted"
    ps = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
    assert f"sleep {marker}" not in ps
    assert git(top, "rev-parse", "--verify", f"improve/{ID}")  # the branch is kept


# ------------------------------------------------------------------ hub


def test_hub_routes_to_the_named_project(tmp_path: Path) -> None:
    a = synth(tmp_path / "a", name="a")
    b = synth(tmp_path / "b", name="b")
    s = Srv(make_hub_server([("a", a), ("b", b)], default="a", allow_apply=True))
    try:
        done = apply_and_wait(s, prefix="/p/b")
        assert done["exit_code"] == 0
        assert git(b, "branch", "--list", "improve/*") != ""
        assert git(a, "branch", "--list", "improve/*") == ""
        st, la = s.get("/api/improve/applies")
        assert la["applies"] == []
        st, lb = s.get("/p/b/api/improve/applies")
        assert [x["id"] for x in lb["applies"]] == [ID]
        st, d = s.call("POST", f"/p/nobody/api/improve/{ID}/apply", {"draft_sha256": "0" * 64})
        assert (st, d) == (404, {"detail": "no such project"})
        st, d = s.call("POST", f"/p/b/api/improve/{ID}/discard", {})
        assert d["exit_code"] == 0
    finally:
        s.close()


def test_the_lock_is_per_server_not_per_project(tmp_path: Path) -> None:
    slow = BASE_TOML.replace('["true"]', '["sleep 3"]')
    a = synth(tmp_path / "a", slow, name="a")
    b = synth(tmp_path / "b", slow, name="b")
    s = Srv(make_hub_server([("a", a), ("b", b)], default="a", allow_apply=True))
    try:
        sha = sha_of(s)
        st, d = s.call("POST", f"/api/improve/{ID}/apply", {"draft_sha256": sha})
        assert st == 202
        st, d = s.call(
            "POST", f"/p/b/api/improve/{ID}/apply", {"draft_sha256": sha_of(s, prefix="/p/b")}
        )
        assert (st, d["code"]) == (409, "apply_running")
        s.get(f"/api/improve/{ID}/apply?wait=60")
    finally:
        s.close()


def test_the_state_flag_follows_the_hub_name(tmp_path: Path) -> None:
    """A hub name that differs from the directory name: Studio's state directory is not the
    CLI's default, so the next commands carry --state and run verbatim."""
    root = synth(tmp_path, name="dirname")
    (root / "studio.toml").write_text(BASE_TOML)  # no [state] dir: named by the hub
    git(root, "commit", "-qam", "default state")
    where = root / ".lattice" / "state" / "hubname"
    where.mkdir(parents=True)
    (where / "events.jsonl").write_text((tmp_path / "state" / "events.jsonl").read_text())
    s = Srv(make_hub_server([("hubname", root)], default="hubname", allow_apply=True))
    try:
        done = apply_and_wait(s)
        assert done["exit_code"] == 0, done
        close = done["result"]["next"][2]["command"]
        assert f"--state {where}" in close
        assert (where / "applies" / f"{ID}.json").is_file()
    finally:
        s.close()


# ------------------------------------------------------------------ the mock


@pytest.fixture()
def mock() -> Iterator[Srv]:
    s = Srv(make_mock(0))
    yield s
    s.close()


def keytree(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: keytree(x) for k, x in v.items()}
    if isinstance(v, list):
        return [keytree(v[0])] if v else []
    return type(v).__name__


def test_mock_serves_draft_and_verify_like_the_library(mock: Srv) -> None:
    st, d = mock.get(f"/api/improve/{ID}/draft")
    assert st == 200 and d["template"] == "config" and d["toml"].startswith("[rebuild]")
    st, d = mock.get("/api/improve/imp-00000000/draft")
    assert d["refused"] and d["reason"] == "unknown_id"
    st, v = mock.get("/api/improve/I-05/verify")
    assert st == 200 and v["verdict"] == "not_enough_data"
    st, v = mock.get(f"/api/improve/{ID}/verify?at=2026-10-01T12:00:00Z")
    assert v["refused"] and v["reason"] == "not_closed"


def test_mock_apply_runs_then_a_record_then_discard(mock: Srv) -> None:
    sha = sha_of(mock)
    st, job = mock.call("POST", f"/api/improve/{ID}/apply", {"draft_sha256": sha})
    assert st == 202 and job["status"] == "running" and job["exit_code"] is None
    st, d = mock.call("POST", f"/api/improve/{ID}/apply", {"draft_sha256": sha})
    assert (st, d["code"]) == (409, "apply_running")
    st, done = mock.get(f"/api/improve/{ID}/apply?wait=10")
    assert done["status"] == "done" and done["exit_code"] == 0
    rec = done["result"]
    assert rec["gates"]["status"] == "passed" and len(rec["gates"]["results"]) == 2
    assert [s["step"] for s in rec["next"]] == [
        "review",
        "merge",
        "close",
        "verify",
        "tidy",
        "drop",
    ]
    st, lst = mock.get("/api/improve/applies")
    assert lst["enabled"] is True and [a["id"] for a in lst["applies"]] == [ID]
    st, d = mock.call("POST", f"/api/improve/{ID}/discard", {})
    assert d["exit_code"] == 0
    st, lst = mock.get("/api/improve/applies")
    assert lst["applies"] == []
    st, d = mock.get(f"/api/improve/{ID}/apply")
    assert (st, d["code"]) == (404, "not_applied")
    st, d = mock.call("POST", f"/api/improve/{ID}/discard", {})
    assert d["exit_code"] == 3 and d["result"]["reason"] == "not_applied"


def test_mock_second_project_is_disabled_and_the_ladder_matches(mock: Srv) -> None:
    pre = "/p/lattice-studio"
    st, lst = mock.get(f"{pre}/api/improve/applies")
    assert lst["enabled"] is False and lst["why_disabled"] == "start Studio with --allow-apply"
    st, d = mock.call("POST", f"{pre}/api/improve/{ID}/apply", {"draft_sha256": "0" * 64})
    assert (st, d["code"]) == (403, "apply_disabled")
    url = f"/api/improve/{ID}/apply"
    body = {"draft_sha256": sha_of(mock)}
    cases = [
        (
            mock.call("POST", url, body, headers={"Origin": "http://evil.example"}),
            403,
            "bad_origin",
        ),
        (mock.call("POST", url, body, host="evil.example:1"), 403, "bad_origin"),
        (
            mock.call(
                "POST", url, raw=b"{}", json_type=False, headers={"Content-Type": "text/plain"}
            ),
            415,
            "json_required",
        ),
        (mock.call("POST", url, raw=b"[]"), 400, None),
        (mock.call("POST", "/api/improve/a%2Fb/apply", body), 422, "bad_id"),
        (mock.call("POST", url, {}), 422, "bad_body"),
        (
            mock.call("POST", url, {"draft_sha256": body["draft_sha256"], "gates": "yes"}),
            422,
            "bad_body",
        ),
        (mock.call("POST", url, {"draft_sha256": "0" * 64}), 409, "draft_changed"),
    ]
    for (st, d), want_st, want_code in cases:
        assert (st, d.get("code")) == (want_st, want_code), (st, d)
    assert cases[-1][0][1]["draft_sha256"] == body["draft_sha256"]


def test_mock_and_real_answer_the_same_key_trees(tmp_path: Path, start: Any, mock: Srv) -> None:
    """The mock's shapes are the real server's (P8's rule, on the fixture proposal)."""
    srv = start(synth(tmp_path))
    real_sha, mock_sha = sha_of(srv), sha_of(mock)
    for path in (
        f"/api/improve/{ID}/draft",
        "/api/improve/imp-00000000/draft",
        f"/api/improve/{ID}/verify",
        "/api/improve/applies",
    ):
        assert keytree(srv.get(path)[1]) == keytree(mock.get(path)[1]), path
    st, rj = srv.call(
        "POST", f"/api/improve/{ID}/apply", {"draft_sha256": real_sha, "gates": False}
    )
    st, mj = mock.call(
        "POST", f"/api/improve/{ID}/apply", {"draft_sha256": mock_sha, "gates": False}
    )
    assert keytree(rj) == keytree(mj)
    rd = srv.get(f"/api/improve/{ID}/apply?wait=30")[1]
    md = mock.get(f"/api/improve/{ID}/apply?wait=30")[1]
    assert keytree(rd) == keytree(md)
    assert keytree(srv.get("/api/improve/applies")[1]) == keytree(
        mock.get("/api/improve/applies")[1]
    )
    rx = srv.call("POST", f"/api/improve/{ID}/discard", {})[1]
    mx = mock.call("POST", f"/api/improve/{ID}/discard", {})[1]
    assert keytree(rx) == keytree(mx)
