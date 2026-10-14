"""Linked pending questions on the real Studio server and the mock (API v1.19, multi-project slice
4b): the provider's accept notifies the consumers on a background worker without ever changing
its response, the consumer's own Studio lists and clears its notes, and the mock has the real
shapes. Over the synthetic provider and consumer of tests/contracts/helpers.py under ``tmp_path``.
No app is started, no LLM is used."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice import workspace as ws
from lattice.contracts import linked
from lattice.spec.rules import Trace, extract_rules
from lattice.studio.mock_server import make_server as make_mock
from lattice.studio.server import (
    ApiError,
    StubGenerator,
    Studio,
    empty_change,
    files_hash,
    make_hub_server,
)
from tests.contracts.helpers import BASE, SPEC_PY, World, copy_data
from tests.contracts.test_linked import status_404_gone
from tests.studio.test_server import request

MD = "# Provider\n\n- A request without a token answers 401.\n"
Q_KEYS = {
    "format", "id", "key", "kind", "workspace", "provider", "consumer", "contract", "status",
    "label", "pinned", "provider_after", "candidate", "imports_affected", "findings", "counts",
    "text", "suggestion", "source", "created_at", "resolution", "superseded",
}  # fmt: skip
LIST_KEYS = {"format", "consumer", "state", "state_dir", "questions", "counts", "problems"}
NOTE_KEYS = {"change", "commit", "status", "reason", "started_at", "result", "error"}


def serve_bg(srv: Any) -> str:
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def stop(srv: Any) -> None:
    srv.shutdown()
    srv.server_close()


def studio_files(root: Path, spec_text: str) -> Trace:
    """`root` as a Studio project (REQUIREMENTS.md, spec.py, spec.trace.json) with `spec_text`."""
    (root / "REQUIREMENTS.md").write_text(MD)
    (root / "spec.py").write_text(spec_text)
    trace = Trace("REQUIREMENTS.md", extract_rules(MD), {"R1": ["scenario:login"]})
    (root / "spec.trace.json").write_text(trace.to_json())
    return trace


def spec_text(data: list[tuple[str, list[dict]]]) -> str:
    return SPEC_PY.format(data=[(n, list(s)) for n, s in data], error_body="fastapi")


def git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return r.stdout.strip()


class Pair:
    """A pinned workspace: Studio project ``prov`` (a git repository) and ``cons``."""

    def __init__(self, root: Path, repo: bool = True, contract: str | None = "static") -> None:
        self.w = World(root, repo=False, contract=contract)
        self.trace = studio_files(self.w.provider, (self.w.provider / "spec.py").read_text())
        studio_files(self.w.consumer, (self.w.provider / "spec.py").read_text())
        if repo:
            git(self.w.provider, "init", "-q")
            git(self.w.provider, "config", "user.email", "studio@example.com")
            git(self.w.provider, "config", "user.name", "Studio Test")
            git(self.w.provider, "add", "-A")
            git(self.w.provider, "commit", "-q", "-m", "init")
        if contract is not None:
            rc, out = self.w.pin()
            assert rc == 0, out
        self.manifest = self.w.root / ws.MANIFEST
        self.state = self.w.consumer / ".lattice" / "state" / "cons"
        rc, out = self.w.run("questions", "init", "cons")
        assert rc == 0, out

    def change(self, st: Studio, cid: str, data: list[tuple[str, list[dict]]]) -> None:
        text = spec_text(data)
        c = empty_change(cid, f"edit {cid}", None)
        c["status"] = "ready"
        st.changes[cid] = c
        st.private[cid] = {"project_hash": files_hash(st.root), "new_md": MD,
                           "new_module": text, "new_trace": self.trace,
                           "done": threading.Event()}  # fmt: skip

    def questions(self) -> list[dict[str, Any]]:
        return linked.read_state(self.state)[0]


def drift_201() -> list[tuple[str, list[dict]]]:
    data = copy_data(BASE)
    for s in data[1][1]:
        if s["method"] == "POST" and s["path"] == "/jobs/7/runs" and s["expect_status"] == 201:
            s["expect_status"] = 200
    return data


def wait_done(st: Studio, n: int = 1, timeout: float = 60.0) -> list[dict[str, Any]]:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        rows = st.get_linked_notifications()["notifications"]
        if len(rows) >= n and all(r["status"] != "running" for r in rows):
            return rows  # type: ignore[no-any-return]
        time.sleep(0.05)
    raise AssertionError(st.get_linked_notifications())


@pytest.fixture()
def pair(tmp_path: Path) -> Pair:
    return Pair(tmp_path / "w")


def provider_studio(p: Pair, **kw: Any) -> Studio:
    kw.setdefault("workspace_path", p.manifest)
    return Studio(p.w.provider, name="prov", **kw)


# ---------------------------------------------------------------- the provider's accept


def test_an_accept_delivers_and_its_response_is_unchanged(pair: Pair) -> None:
    st = provider_studio(pair)
    assert st.get_linked_notifications() == {"notifications": []}
    pair.change(st, "c1", drift_201())
    acc = st.accept("c1")
    assert set(acc) == {"commit", "message"} and len(acc["commit"]) == 40
    (n,) = wait_done(st)
    assert set(n) == NOTE_KEYS
    assert (n["change"], n["commit"], n["status"], n["reason"], n["error"]) == (
        "c1", acc["commit"], "done", None, None)  # fmt: skip
    res = n["result"]
    assert res["source"] == {"via": "accept", "change": "c1", "commit": acc["commit"]}
    assert res["consumers"][0]["action"] == "created" and res["consumers"][0]["question"] == "lq1"
    assert res["exit_code"] == 0 and res["advisory"] is True
    (q,) = pair.questions()
    assert q["id"] == "lq1" and q["source"] == res["source"] and q["status"] == "open"
    assert q["candidate"]["resolved"] == acc["commit"] == q["provider_after"]["git_ref"]
    assert [f["class"] for f in q["findings"]] == ["BROKEN", "CHANGED"]
    # the provider's own state holds nothing of it (no file, no event)
    files = sorted(
        str(p.relative_to(st.store.root)) for p in st.store.root.rglob("*") if p.is_file()
    )
    assert not [f for f in files if "linked" in f]
    assert "linked" not in (st.store.root / "events.jsonl").read_text()


def test_a_second_accept_of_the_same_break_is_one_question(pair: Pair) -> None:
    st = provider_studio(pair)
    pair.change(st, "c1", drift_201())
    st.accept("c1")
    first = wait_done(st)
    data = drift_201()
    data[1][1].append({"method": "GET", "path": "/health", "user": None, "expect_status": 200})
    pair.change(st, "c2", data)
    st.accept("c2")
    rows = wait_done(st, 2)
    assert [r["change"] for r in rows] == ["c2", "c1"], "newest first"
    assert [r["result"]["consumers"][0]["action"] for r in rows] == ["existing", "created"]
    assert len(pair.questions()) == 1 and first[0]["status"] == "done"


def test_a_refused_accept_notifies_nothing(pair: Pair) -> None:
    st = provider_studio(pair)
    with pytest.raises(ApiError) as e:
        st.accept("c9")
    assert e.value.status == 404
    pair.change(st, "c1", drift_201())
    st.changes["c1"]["status"] = "drafting"
    with pytest.raises(ApiError):
        st.accept("c1")
    time.sleep(0.2)
    assert st.get_linked_notifications() == {"notifications": []} and pair.questions() == []


@pytest.mark.parametrize("why", ["no_workspace", "manifest_error", "not_in_workspace",
                                 "no_consumers"])  # fmt: skip
def test_the_outcomes_that_write_nothing(pair: Pair, tmp_path: Path, why: str) -> None:
    path: Path | None = pair.manifest
    if why == "no_workspace":
        path = None
    elif why == "manifest_error":
        bad = tmp_path / "bad.toml"
        bad.write_text("[[[")
        path = bad
    elif why == "not_in_workspace":
        other = tmp_path / "elsewhere"
        shutil.copytree(pair.w.provider, other)
        st = Studio(other, name="prov", workspace_path=pair.manifest)
        pair.change(st, "c1", drift_201())
        st.accept("c1")
        (n,) = wait_done(st)
        assert (n["status"], n["reason"], n["result"]) == ("skipped", why, None)
        assert pair.questions() == []
        return
    elif why == "no_consumers":
        pair.w.contract = None
        pair.w.write_manifest()
    st = provider_studio(pair, workspace_path=path)
    pair.change(st, "c1", drift_201())
    acc = st.accept("c1")
    assert set(acc) == {"commit", "message"}
    (n,) = wait_done(st)
    assert (n["status"], n["reason"], n["result"]) == ("skipped", why, None)
    assert pair.questions() == []


def test_a_studio_matches_the_manifest_by_path_not_by_name(pair: Pair) -> None:
    st = Studio(pair.w.provider, name="a-completely-different-name", workspace_path=pair.manifest)
    pair.change(st, "c1", drift_201())
    st.accept("c1")
    (n,) = wait_done(st)
    assert n["status"] == "done" and n["result"]["provider"] == "prov"
    assert pair.questions()[0]["provider"] == "prov"


def test_a_failed_delivery_is_a_warning_and_never_the_accepts(pair: Pair) -> None:
    shutil.rmtree(pair.w.consumer / ".lattice")  # no state directory: never created by a provider
    st = provider_studio(pair)
    pair.change(st, "c1", drift_201())
    acc = st.accept("c1")
    assert set(acc) == {"commit", "message"}
    (n,) = wait_done(st)
    row = n["result"]["consumers"][0]
    assert n["status"] == "done" and row["action"] == "undelivered"
    assert row["warning"]["code"] == "consumer_state_missing" and n["result"]["exit_code"] == 1
    assert not (pair.w.consumer / ".lattice").exists()


def test_a_worker_exception_is_recorded_and_the_accept_is_unaffected(
    pair: Pair, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("delivery exploded")

    monkeypatch.setattr(linked, "notify", boom)
    st = provider_studio(pair)
    pair.change(st, "c1", drift_201())
    acc = st.accept("c1")
    assert set(acc) == {"commit", "message"}
    (n,) = wait_done(st)
    assert n["status"] == "done" and n["result"] is None
    assert n["error"] == "RuntimeError: delivery exploded"
    pair.change(st, "c2", status_404_gone())  # the worker is still alive
    monkeypatch.undo()
    st.accept("c2")
    assert len(wait_done(st, 2)) == 2


def test_a_held_lock_never_delays_the_accept(pair: Pair) -> None:
    lock = pair.state / linked.DIR / linked.LOCK
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        st = provider_studio(pair)
        pair.change(st, "c1", drift_201())
        t0 = time.monotonic()
        acc = st.accept("c1")
        took = time.monotonic() - t0
        assert set(acc) == {"commit", "message"} and took < 2.0
        pair.change(st, "c2", status_404_gone())  # a second accept while the first still waits
        t0 = time.monotonic()
        st.accept("c2")
        assert time.monotonic() - t0 < 2.0
        assert st.get_linked_notifications()["notifications"][-1]["status"] == "running"
        rows = wait_done(st, 2, timeout=60)
    finally:
        os.close(fd)
    assert [r["result"]["consumers"][0]["warning"]["code"] for r in rows] == [
        "consumer_state_busy", "consumer_state_busy"]  # fmt: skip
    assert pair.questions() == []


def test_outside_git_the_candidate_is_the_directory(tmp_path: Path) -> None:
    p = Pair(tmp_path / "w", repo=False)
    st = provider_studio(p)
    p.change(st, "c1", drift_201())
    acc = st.accept("c1")
    assert acc["commit"] is None and "not a git repository" in acc["note"]
    (n,) = wait_done(st)
    assert n["commit"] is None and n["status"] == "done"
    (q,) = p.questions()
    assert q["candidate"]["kind"] == "path" and q["source"]["commit"] is None
    assert q["candidate"]["resolved"] == str(p.w.provider.resolve())


def test_the_notification_log_keeps_the_newest_twenty(pair: Pair) -> None:
    st = provider_studio(pair, workspace_path=None)  # skipped outcomes are enough
    for i in range(25):
        st._queue_linked(f"c{i}", None)
    rows = st.get_linked_notifications()["notifications"]
    assert len(rows) == 20 and rows[0]["change"] == "c24" and rows[-1]["change"] == "c5"


# ---------------------------------------------------------------- the consumer's own Studio


@pytest.fixture()
def hub(pair: Pair) -> Iterator[tuple[str, Pair]]:
    srv = make_hub_server(
        [("prov", pair.w.provider), ("cons", pair.w.consumer)],
        generator=StubGenerator(),
        workspace=pair.manifest,
    )
    base = serve_bg(srv)
    try:
        yield base, pair
    finally:
        stop(srv)


def seed(pair: Pair, *cands: str) -> None:
    """Questions in the consumer's store through the CLI (candidate refs of the provider)."""
    for cand in cands:
        rc, out = pair.w.run("notify", "prov", "--candidate", cand)
        assert rc == 0, out


def test_the_consumer_studio_lists_what_the_cli_lists(hub: tuple[str, Pair]) -> None:
    base, pair = hub
    status, empty = request("GET", f"{base}/p/cons/api/linked-questions")
    assert status == 200 and set(empty) == LIST_KEYS
    assert (empty["consumer"], empty["state"], empty["questions"]) == ("cons", "present", [])
    assert empty["counts"] == {"open": 0, "resolved": 0, "dismissed": 0, "superseded": 0}
    assert empty["state_dir"] == str(pair.state.resolve())
    git(pair.w.provider, "branch", "pinned")
    pair.w.edit(drift_201())
    git(pair.w.provider, "commit", "-qam", "drift")
    git(pair.w.provider, "branch", "brk")
    pair.w.edit(status_404_gone())
    git(pair.w.provider, "commit", "-qam", "drift 2")
    git(pair.w.provider, "branch", "brk2")
    seed(pair, "brk", "brk2")
    status, body = request("GET", f"{base}/p/cons/api/linked-questions")
    rc, cli = pair.w.run("questions", "list", "cons")
    assert status == 200 and rc == 0 and body == cli
    assert [q["id"] for q in body["questions"]] == ["lq1", "lq2"]
    assert all(set(q) == Q_KEYS for q in body["questions"])
    assert body["questions"][0]["superseded"]["by"] == "lq2"
    assert body["counts"] == {"open": 2, "resolved": 0, "dismissed": 0, "superseded": 1}
    status, only = request("GET", f"{base}/p/cons/api/linked-questions?status=open")
    assert status == 200 and only == body
    status, one = request("GET", f"{base}/p/cons/api/linked-questions/lq2")
    assert status == 200 and one == body["questions"][1]
    status, err = request("GET", f"{base}/p/cons/api/linked-questions/lq9")
    assert status == 404 and err["code"] == "unknown_question"
    status, err = request("GET", f"{base}/p/cons/api/linked-questions?status=bogus")
    assert status == 422 and err["code"] == "bad_status"


def test_clearing_through_the_consumers_own_routes(hub: tuple[str, Pair]) -> None:
    base, pair = hub
    pair.w.edit(drift_201())
    git(pair.w.provider, "commit", "-qam", "drift")
    git(pair.w.provider, "branch", "brk")
    seed(pair, "brk")
    stored = pair.state / linked.DIR / "lq1.json"
    status, out = request("POST", f"{base}/p/cons/api/linked-questions/lq1/resolve",
                          {"note": "adapted"})  # fmt: skip
    assert status == 200 and out["status"] == "resolved" and set(out) == Q_KEYS
    res = out["resolution"]
    assert (res["status"], res["note"], res["via"]) == ("resolved", "adapted", "studio")
    kept = stored.read_bytes()
    status, err = request("POST", f"{base}/p/cons/api/linked-questions/lq1/dismiss", {})
    assert status == 409 and err["code"] == "not_open" and stored.read_bytes() == kept
    status, err = request("POST", f"{base}/p/cons/api/linked-questions/lq1/resolve",
                          {"note": "x" * 2001})  # fmt: skip
    assert status == 422 and err["code"] == "bad_note"
    status, err = request("POST", f"{base}/p/cons/api/linked-questions/lq9/resolve", {})
    assert status == 404 and err["code"] == "unknown_question"
    # the provider's own Studio holds no such question: its store is empty, so a clear is a 404
    status, err = request("POST", f"{base}/p/prov/api/linked-questions/lq1/resolve", {})
    assert status == 404 and err["code"] == "unknown_question"
    assert stored.read_bytes() == kept
    status, err = request("POST", f"{base}/p/cons/api/linked-questions/lq1", {})
    assert status == 404
    status, err = request("POST", f"{base}/p/cons/api/linked-questions/lq1/reopen", {})
    assert status == 404


def test_the_provider_side_routes(hub: tuple[str, Pair]) -> None:
    base, _pair = hub
    status, body = request("GET", f"{base}/p/prov/api/linked-notifications")
    assert status == 200 and body == {"notifications": []}
    assert request("POST", f"{base}/p/prov/api/linked-notifications", {})[0] == 404
    assert request("GET", f"{base}/p/nobody/api/linked-questions")[0] == 404
    status, own = request("GET", f"{base}/p/prov/api/linked-questions")
    assert status == 200 and own["consumer"] == "prov" and own["questions"] == []


def test_a_studio_without_durable_state_answers_disabled(tmp_path: Path) -> None:
    p = Pair(tmp_path / "w")
    (p.w.consumer / "studio.toml").write_text("[state]\nenabled = false\n")
    srv = make_hub_server([("cons", p.w.consumer)], generator=StubGenerator())
    base = serve_bg(srv)
    try:
        status, body = request("GET", f"{base}/api/linked-questions")
        assert status == 200 and set(body) == LIST_KEYS
        assert (body["state"], body["state_dir"], body["questions"]) == ("disabled", None, [])
        status, err = request("GET", f"{base}/api/linked-questions/lq1")
        assert status == 404 and err["code"] == "no_state"
        status, err = request("POST", f"{base}/api/linked-questions/lq1/resolve", {})
        assert status == 404 and err["code"] == "no_state"
    finally:
        stop(srv)


def test_a_hub_accept_notifies_and_the_consumer_studio_lists_it(hub: tuple[str, Pair]) -> None:
    """End to end over HTTP: the accept route, the worker, the notification, the consumer."""
    base, pair = hub
    seeded = provider_studio(pair)  # a ready change in the provider's durable state
    pair.change(seeded, "c1", drift_201())
    seeded._save_change("c1")
    status, acc = request("POST", f"{base}/p/prov/api/changes/c1/accept", {})
    assert status == 200 and set(acc) == {"commit", "message"}
    t0 = time.monotonic()
    while True:
        status, notes = request("GET", f"{base}/p/prov/api/linked-notifications")
        rows = notes["notifications"]
        if rows and rows[0]["status"] != "running":
            break
        assert time.monotonic() - t0 < 60, notes
        time.sleep(0.05)
    assert status == 200 and rows[0]["status"] == "done" and rows[0]["commit"] == acc["commit"]
    status, body = request("GET", f"{base}/p/cons/api/linked-questions")
    assert status == 200 and [q["id"] for q in body["questions"]] == ["lq1"]
    assert body["questions"][0]["source"] == {"via": "accept", "change": "c1",
                                              "commit": acc["commit"]}  # fmt: skip


# ---------------------------------------------------------------- the mock


def test_the_mock_has_the_real_shapes(hub: tuple[str, Pair]) -> None:
    base, pair = hub
    pair.w.edit(drift_201())
    git(pair.w.provider, "commit", "-qam", "drift")
    git(pair.w.provider, "branch", "brk")
    seed(pair, "brk")
    _s, real = request("GET", f"{base}/p/cons/api/linked-questions")
    srv = make_mock()
    mbase = serve_bg(srv)
    try:
        _s, mock = request("GET", f"{mbase}/p/scheduler-web/api/linked-questions")
        _s, bare = request("GET", f"{mbase}/api/linked-questions")
        _s, other = request("GET", f"{mbase}/p/lattice-studio/api/linked-questions")
        _s, openq = request("GET", f"{mbase}/p/scheduler-web/api/linked-questions?status=open")
        assert request("GET", f"{mbase}/p/scheduler-web/api/linked-questions?status=x")[0] == 422
        _s, one = request("GET", f"{mbase}/p/scheduler-web/api/linked-questions/lq1")
        assert request("GET", f"{mbase}/p/scheduler-web/api/linked-questions/lq9")[0] == 404
        _s, notes = request("GET", f"{mbase}/api/linked-notifications")
        _s, none = request("GET", f"{mbase}/p/lattice-studio/api/linked-notifications")
        assert request("POST", f"{mbase}/api/linked-notifications", {})[0] == 404
        s1, cleared = request("POST", f"{mbase}/p/scheduler-web/api/linked-questions/lq1/resolve",
                              {"note": "ok"})  # fmt: skip
        s2, again = request("POST", f"{mbase}/p/scheduler-web/api/linked-questions/lq1/dismiss", {})
        s3, big = request("POST", f"{mbase}/p/scheduler-web/api/linked-questions/lq2/dismiss",
                          {"note": "x" * 2001})  # fmt: skip
        s4, gone = request("POST", f"{mbase}/p/scheduler-web/api/linked-questions/lq9/resolve", {})
        _s, after = request("GET", f"{mbase}/p/scheduler-web/api/linked-questions")
    finally:
        stop(srv)
    assert set(mock) == LIST_KEYS == set(real)
    assert {k: type(v).__name__ for k, v in mock.items()} == {
        k: type(v).__name__ for k, v in real.items()}  # fmt: skip
    assert [q["id"] for q in mock["questions"]] == ["lq1", "lq2"]
    assert [q["status"] for q in mock["questions"]] == ["open", "dismissed"]
    for q in mock["questions"]:
        assert set(q) == Q_KEYS == set(real["questions"][0])
        both = [k for k in q if q[k] is not None and real["questions"][0][k] is not None]
        assert {k: type(q[k]).__name__ for k in both} == {
            k: type(real["questions"][0][k]).__name__ for k in both
        }
    assert mock["counts"] == {"open": 1, "resolved": 0, "dismissed": 1, "superseded": 0}
    assert [q["id"] for q in openq["questions"]] == ["lq1"] and one == mock["questions"][0]
    assert bare["consumer"] == "job-scheduler-studio" and bare["questions"] == []
    assert other["questions"] == [] and other["state"] == "present"
    (note,) = notes["notifications"]
    assert set(note) == NOTE_KEYS and note["status"] == "done"
    assert note["result"]["consumers"][0]["question"] == "lq1"
    assert none == {"notifications": []}
    assert (
        s1 == 200 and cleared["resolution"]["via"] == "studio" and cleared["status"] == "resolved"
    )
    assert s2 == 409 and again["code"] == "not_open"
    assert (
        s3 == 422 and big["code"] == "bad_note" and s4 == 404 and gone["code"] == "unknown_question"
    )
    assert [q["status"] for q in after["questions"]] == ["resolved", "dismissed"]
    # the real server's resolution has the same keys
    real_json = json.loads(json.dumps(cleared["resolution"]))
    assert set(real_json) == {"status", "note", "at", "via"}
