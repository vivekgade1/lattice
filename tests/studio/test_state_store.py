"""Durable state (API.md v1.10, lattice/studio/state.py): atomic writes (a write killed
between the temporary file and the replace leaves the old record valid), corrupt files
quarantined with a warning (startup goes on), id counters that never repeat after a
restart, a round trip of every record type through a restarted Studio (change sets,
build records, plans), state files git-ignored, and two projects that never share state.
No real LLM call: the stub generator, the stub agent and the stub planner."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from lattice.studio import state as state_mod
from lattice.studio.server import StubGenerator, Studio, make_hub_server
from lattice.studio.state import StateStore, atomic_write, state_dir_for
from tests.studio.test_build_plan import integ, node, stub_plans
from tests.studio.test_build_plan import repo as repo  # noqa: F401 -- the fixture
from tests.studio.test_build_scope import git
from tests.studio.test_server import make_project, request

REBUILD_TOML = (
    '\n[rebuild]\nagent = "stub"\nplanner = "stub"\ngates = ["./gate.sh"]\nmax_rounds = 2\n'
)


class _Killed(BaseException):
    """Stands for SIGKILL arriving in the middle of a write."""


class _HalfWriteOS:
    """The ``os`` module, except that ``write`` writes half its bytes, then the process
    'dies' (the exception). Everything else is the real module."""

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)

    @staticmethod
    def write(fd: int, data: Any) -> int:
        b = bytes(data)
        os.write(fd, b[: max(1, len(b) // 2)])
        raise _Killed()


def _record(n: int, size: int = 2000) -> dict[str, Any]:
    return {"id": "p1", "status": "running", "n": n, "pad": "x" * size}


# ---------------------------------------------------------------- the store


def test_a_write_killed_midway_leaves_the_old_file_valid(tmp_path: Path, monkeypatch) -> None:
    store = StateStore(tmp_path / "state")
    store.save("plans", "p1", _record(1))
    monkeypatch.setattr(state_mod, "os", _HalfWriteOS())
    with pytest.raises(_Killed):
        store.save("plans", "p1", _record(2, 50_000))
    monkeypatch.undo()
    # the record on disk is the old one, whole; no warning, nothing quarantined
    fresh = StateStore(tmp_path / "state")
    loaded = fresh.load("plans")
    assert [r["n"] for r, _p in loaded] == [1] and fresh.warnings == []
    assert sorted(p.name for p in (tmp_path / "state" / "plans").iterdir()) == ["p1.json"]
    # atomic_write itself: the target keeps its bytes when the write dies
    target = tmp_path / "f.json"
    target.write_text('{"ok": true}')
    monkeypatch.setattr(state_mod, "os", _HalfWriteOS())
    with pytest.raises(_Killed):
        atomic_write(target, b'{"ok": false, "pad": "' + b"y" * 10_000 + b'"}')
    monkeypatch.undo()
    assert json.loads(target.read_text()) == {"ok": True}


KILLER = """
import sys
from lattice.studio.state import StateStore
store = StateStore(sys.argv[1])
n = 0
print("ready", flush=True)
while True:
    n += 1
    store.save("plans", "p1", {"id": "p1", "status": "running", "n": n,
                               "pad": "z" * (3_000_000 + n % 7)})
"""


def test_sigkill_during_saves_never_tears_a_record(tmp_path: Path) -> None:
    root = tmp_path / "state"
    StateStore(root).save("plans", "p1", {"id": "p1", "status": "ready", "n": 0})
    pkg = str(Path(state_mod.__file__).resolve().parents[2])
    env = {**os.environ, "PYTHONPATH": pkg}
    for delay in (0.25, 0.4, 0.55, 0.7, 0.85):
        proc = subprocess.Popen([sys.executable, "-c", KILLER, str(root)], env=env,
                                stdout=subprocess.PIPE, text=True)  # fmt: skip
        assert proc.stdout is not None and proc.stdout.readline().strip() == "ready"
        time.sleep(delay)
        proc.send_signal(signal.SIGKILL)
        proc.wait(10)
        proc.stdout.close()
        store = StateStore(root)
        loaded = store.load("plans")
        assert store.warnings == [], store.warnings
        assert len(loaded) == 1 and loaded[0][0]["id"] == "p1"
    assert [p.name for p in (root / "plans").iterdir()] == ["p1.json"]


def test_a_corrupt_file_is_quarantined_and_loading_goes_on(tmp_path: Path) -> None:
    root = tmp_path / "state"
    store = StateStore(root)
    store.save("plans", "p1", {"id": "p1", "status": "passed"})
    store.save("plans", "p3", {"id": "p3", "status": "passed", "future_field": {"x": 1}})
    (root / "plans" / "p2.json").write_text('{"schema": 1, "record": {"id": "p2", "stat')
    (root / "plans" / "p4.json").write_text('{"schema": 1, "record": {"id": "p9"}}')
    (root / "meta.json").write_text("not json at all")
    fresh = StateStore(root)
    loaded = fresh.load("plans")
    assert [r["id"] for r, _ in loaded] == ["p1", "p3"]
    assert loaded[1][0]["future_field"] == {"x": 1}  # unknown fields are kept
    files = sorted(w["file"].rsplit("/", 1)[-1] for w in fresh.warnings)
    assert files == ["meta.json", "p2.json", "p4.json"], fresh.warnings
    assert all("moved aside" in w["message"] for w in fresh.warnings)
    names = sorted(p.name for p in (root / "plans").iterdir())
    assert names[:2] == ["p1.json", "p2.json.bad-" + names[1].split(".bad-")[1]]
    assert any(n.startswith("p4.json.bad-") for n in names)
    # ids never repeat: the quarantined p4 still counts
    assert fresh.allocate("plans") == 5


def test_id_counters_survive_a_restart(tmp_path: Path) -> None:
    root = tmp_path / "state"
    s = StateStore(root)
    assert [s.allocate("plans") for _ in range(3)] == [1, 2, 3]
    assert s.allocate("builds") == 1 and s.allocate("changes") == 1
    s2 = StateStore(root)
    assert s2.allocate("plans") == 4 and s2.allocate("builds") == 2
    # a lost meta.json: the files still say which ids were used
    s2.save("changes", "c7", {"id": "c7"})
    (root / "meta.json").unlink()
    s3 = StateStore(root)
    assert s3.allocate("changes") == 8 and s3.allocate("plans") == 1
    # release gives back only the last id, and only when nothing was kept under it
    n = s3.allocate("builds")
    s3.release("builds", n)
    assert s3.allocate("builds") == n
    s3.save("builds", f"b{n}", {"id": f"b{n}"})
    s3.release("builds", n)
    assert s3.allocate("builds") == n + 1


def test_the_state_dir_is_git_ignored_and_outside_the_tracked_tree(tmp_path: Path) -> None:
    root = make_project(tmp_path / "proj", repo=False)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "s@example.com")
    git(root, "config", "user.name", "S")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    d = state_dir_for(root, "proj")
    assert d == root.resolve() / ".lattice" / "state" / "proj"
    st = Studio(root, generator=StubGenerator(), name="proj")
    try:
        assert st.store is not None and st.store.root == d
        cid = st.new_change({"request": "Notes can be starred."})["id"]
        st.get_change(cid, wait=30)
        assert (d / "changes" / f"{cid}.json").is_file() and (d / "meta.json").is_file()
        assert git(root, "status", "--porcelain", "--untracked-files=all") == ""
        assert git(root, "check-ignore", f".lattice/state/proj/changes/{cid}.json")
        assert "/.lattice/state/" in (root / ".git" / "info" / "exclude").read_text()
        assert git(root, "ls-files", ".lattice") == ""
    finally:
        st.close_sessions()
    # [state] dir and [state] enabled
    (root / "studio.toml").write_text(
        (root / "studio.toml").read_text() + '\n[state]\ndir = "../elsewhere"\n'
    )
    st = Studio(root, generator=StubGenerator(), name="proj")
    assert st.store is not None and st.store.root == (root / ".." / "elsewhere").resolve()
    (root / "studio.toml").write_text(
        (root / "studio.toml").read_text().replace('dir = "../elsewhere"', "enabled = false")
    )
    st = Studio(root, generator=StubGenerator(), name="proj")
    assert st.store is None
    assert st.new_change({"request": "x"})["id"] == "c1"  # in memory, as before v1.10


# ---------------------------------------------------------------- round trips through Studio


def _wait_change(st: Studio, cid: str) -> dict[str, Any]:
    rec = st.get_change(cid, wait=30)
    assert rec["status"] not in ("drafting", "checking"), rec
    return rec


def test_change_sets_round_trip_through_a_restart(tmp_path: Path) -> None:
    root = make_project(tmp_path / "p")
    st = Studio(root, generator=StubGenerator(), name="p")
    try:
        c1 = st.new_change({"request": "Notes have a title."})["id"]
        _wait_change(st, c1)
        st.accept(c1)  # accepted, never built
        c2 = st.new_change({"request": "Notes can be pinned."})["id"]
        _wait_change(st, c2)
        st.reject(c2)
        c3 = st.new_change({"request": "Notes can be starred."})["id"]  # ready
        _wait_change(st, c3)
        c4 = st.new_change({"request": "Notes can be archived. stub:fail"})["id"]
        _wait_change(st, c4)
        before = {c: st.get_change(c) for c in (c1, c2, c3, c4)}
        model_before = st.get_model()["changes"]
    finally:
        st.close_sessions()
    assert [c["status"] for c in before.values()] == ["accepted", "rejected", "ready", "failed"]
    st2 = Studio(root, generator=StubGenerator(), name="p")
    try:
        assert {c: st2.get_change(c) for c in (c1, c2, c3, c4)} == before
        assert st2.get_model()["changes"] == model_before
        assert model_before[0]["build"] == {"state": "not_built"}  # survives as not built
        # the ready change can still be accepted after the restart (its proposal was kept)
        out = st2.accept(c3)
        assert out["commit"] and st2.get_change(c3)["status"] == "accepted"
        assert st2.new_change({"request": "Again."})["id"] == "c5"  # ids go on
    finally:
        st2.close_sessions()


def test_builds_and_plans_round_trip_through_a_restart(repo: Path) -> None:  # noqa: F811
    (repo / "studio.toml").write_text((repo / "studio.toml").read_text() + REBUILD_TOML)
    git(repo, "commit", "-q", "-am", "rebuild config")
    stub_plans(repo, {"rules-r4": {"nodes": [node("r", scope={"rules": ["R4"]}), integ("r")]}})
    st = Studio(repo, generator=StubGenerator(), name="notes")
    try:
        b = st.rebuilder.start([], scope={"rules": ["R4"]})["id"]
        build = st.rebuilder.get(b, wait=60)
        assert build["status"] == "gave_up", build  # the stub has no rounds for rules-r4
        pid = st.plans.create({"scope": {"rules": ["R4"]}})["id"]
        assert st.plans.get(pid, wait=30)["status"] == "ready"
        pid2 = st.plans.create({"scope": {"rules": ["R4"]}})["id"]
        assert st.plans.get(pid2, wait=30)["status"] == "ready"
        st.plans.approve(pid2)
        st.plans.run(pid2)
        plan2 = st.plans.get(pid2, wait=60)
        assert plan2["status"] == "passed", plan2
        before = {
            "builds": st.rebuilder.list(),
            "plans": [st.plans.get(p) for p in (pid, pid2)],
            "plan_list": st.plans.list(),
            "areas": st.get_areas(),
            "branches": st.get_branches(),
        }
    finally:
        st.close_sessions()
    st2 = Studio(repo, generator=StubGenerator(), name="notes")
    try:
        after = {
            "builds": st2.rebuilder.list(),
            "plans": [st2.plans.get(p) for p in (pid, pid2)],
            "plan_list": st2.plans.list(),
            "areas": st2.get_areas(),
            "branches": st2.get_branches(),
        }
        assert after == before
        # the passed plan's work is pinned: node commit and gated level tip
        anchors = after["plans"][1]["anchors"]
        assert set(anchors) >= {"r", "L0"}
        refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname)",
                   f"refs/lattice/plans/{pid2}/").splitlines()  # fmt: skip
        assert sorted(refs) == sorted(f"refs/lattice/plans/{pid2}/{k} {v}"
                                      for k, v in anchors.items())  # fmt: skip
        assert st2.plans.create({"scope": {"rules": ["R4"]}})["id"] == "p3"
        assert st2.rebuilder._alloc() == "b3"  # b1 the build, b2 the plan's node
    finally:
        st2.close_sessions()


def test_corrupt_state_does_not_crash_startup(repo: Path) -> None:  # noqa: F811
    st = Studio(repo, generator=StubGenerator(), name="notes")
    cid = st.new_change({"request": "Notes can be starred."})["id"]
    _wait_change(st, cid)
    st.close_sessions()
    d = st.store.root
    (d / "changes" / "c1.json").write_bytes(b"\x00\xff garbage")
    (d / "plans" / "p1.json").write_text("[]")
    srv = make_hub_server([("notes", repo)], generator=StubGenerator())
    import threading

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert request("GET", base + "/api/projects")[1][0]["state_warnings"] == []
        status, model = request("GET", base + "/api/model")
        assert status == 200 and model["changes"] == []
        warnings = request("GET", base + "/api/projects")[1][0]["state_warnings"]
        assert sorted(w["file"].rsplit("/", 1)[-1] for w in warnings) == ["c1.json", "p1.json"]
        assert request("POST", base + "/api/changes", {"request": "x"})[1] == {"id": "c2"}
    finally:
        srv.shutdown()
        srv.server_close()


def test_two_projects_do_not_share_state(tmp_path: Path) -> None:
    mono = tmp_path / "mono"
    a = make_project(mono / "alpha", repo=False)
    b = make_project(mono / "beta", repo=False)
    git(mono, "init", "-q", "-b", "main")
    git(mono, "config", "user.email", "s@example.com")
    git(mono, "config", "user.name", "S")
    git(mono, "add", "-A")
    git(mono, "commit", "-q", "-m", "init")
    for (r, name), other in (((a, "alpha"), "beta"), ((b, "beta"), "alpha")):
        assert state_dir_for(r, name) == mono.resolve() / ".lattice" / "state" / name
        assert state_dir_for(r, name) != state_dir_for(r, other)
    sa = Studio(a, generator=StubGenerator(), name="alpha")
    sb = Studio(b, generator=StubGenerator(), name="beta")
    ca = sa.new_change({"request": "only in alpha"})["id"]
    _wait_change(sa, ca)
    sa.close_sessions()
    sb.close_sessions()
    sa2 = Studio(a, generator=StubGenerator(), name="alpha")
    sb2 = Studio(b, generator=StubGenerator(), name="beta")
    assert [c["id"] for c in sa2.get_model()["changes"]] == ["c1"]
    assert sb2.get_model()["changes"] == []
    assert sb2.new_change({"request": "beta's own"})["id"] == "c1"
    _wait_change(sb2, "c1")
    assert sb2.store is not None and sa2.store is not None
    assert sb2.store.root != sa2.store.root
    assert git(mono, "status", "--porcelain", "--untracked-files=all") == ""
    sa2.close_sessions()
    sb2.close_sessions()


def test_an_interrupted_change_fails_and_can_be_redrafted(repo: Path) -> None:  # noqa: F811
    st = Studio(repo, generator=StubGenerator(), name="notes")
    cid = st.new_change({"request": "Notes can be starred. stub:hold=5"})["id"]
    time.sleep(0.3)
    assert st.get_change(cid)["status"] == "drafting"
    # what a crash leaves: the file says drafting; a new Studio reconciles it
    st2 = Studio(repo, generator=StubGenerator(), name="notes")
    rec = st2.get_change(cid)
    assert rec["status"] == "failed" and rec["error_kind"] == "interrupted"
    assert rec["interrupted_reason"] == "studio_restarted" and rec["interrupted_at"]
    assert rec["request"] == "Notes can be starred. stub:hold=5"
    # re-drafted by a follow-up on it: the same base, its request by default
    new = st2.new_change({"parent": cid, "request": ""})["id"]
    done = _wait_change(st2, new)
    assert done["status"] == "ready" and done["request"] == rec["request"]
    assert st2.get_change(cid)["superseded_by"] == new
    st.close_sessions()
    st2.close_sessions()
