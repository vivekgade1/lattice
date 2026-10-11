"""Resume (API.md v1.10): a plan or a build that stopped -- a restart, a crash, a branch
that could not move -- goes on from where it stopped. Reconciliation on load (a live record
becomes ``interrupted``; orphan process groups are terminated only when pid, start time and
command line all match), landing a level that passed its full gate but never landed
(``POST /api/plan/{id}/land``: a fast-forward with no agent; a re-merge and the full gate
only when the target moved), retries with a per-node attempts cap, a stand-alone build
continued from its next round, the ``resume`` view on Streak's real p2 record, and a REAL
restart: a Studio server killed with SIGKILL in the middle of level 2 and a new one that
resumes without running the passed nodes' agents again. TEST ONLY stubs (planner, agent,
gate); no real LLM call, no API key, no Docker."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import lattice
from lattice.studio import state as state_mod
from lattice.studio.plan import PlanError, PlanManager, land_waiting, resume_view
from lattice.studio.rebuild import STUB_LOG_ENV, Rebuilder, RebuildError
from lattice.studio.state import StateStore, proc_info
from tests.studio.test_build_plan import _r, integ, node, stub_plans
from tests.studio.test_build_plan import repo as repo  # noqa: F401 -- the fixture
from tests.studio.test_build_scope import git
from tests.studio.test_server import request

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SEED = Path(__file__).resolve().parents[2] / "dogfood" / "studio" / "project_seed"
PKG_ROOT = str(Path(lattice.__file__).resolve().parents[1])


def agent_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def runs_of(path: Path, key: str) -> int:
    """How many agent rounds the stub played for one node (or scope) key."""
    return sum(1 for x in agent_log(path) if x.get("key") == key)


def manager(repo: Path, tmp: Path, **table: Any) -> tuple[PlanManager, Path]:  # noqa: F811
    log = tmp / "agent.log"
    t = {"gates": ["./gate.sh"], "max_rounds": 2, "agent": "stub", "planner": "stub", **table}
    store = StateStore(tmp / "state")
    rb = Rebuilder(repo, t, name="notes", model="sonnet",
                   env={**os.environ, STUB_LOG_ENV: str(log)}, store=store)  # fmt: skip
    return PlanManager(rb, store=store), log


def stub_rounds(repo: Path, scopes: dict[str, Any]) -> None:  # noqa: F811
    doc = json.loads((repo / "stub-rebuild.json").read_text())
    doc["scopes"].update(scopes)
    (repo / "stub-rebuild.json").write_text(json.dumps(doc))
    git(repo, "commit", "-q", "-am", "stub rounds")


def ready_run(pm: PlanManager, body: dict[str, Any]) -> dict[str, Any]:
    pid = pm.create(body)["id"]
    assert pm.get(pid, wait=30)["status"] == "ready"
    pm.approve(pid)
    pm.run(pid)
    rec = pm.get(pid, wait=60)
    assert rec["status"] not in ("running", "planning"), rec
    return rec


def alive(pid: int) -> bool:
    return state_mod._ps(pid) is not None


def wait_for(cond, timeout: float = 30.0, step: float = 0.1) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(step)
    return cond()


WR_PLAN = {
    "nodes": [
        node("w", scope={"area": "writing"}, files=["w.txt"]),
        node("r", scope={"area": "reading"}, deps=["w"], files=["r.txt"]),
        integ("r"),
    ]
}


def checkout_target_during_w(repo: Path) -> None:  # noqa: F811
    """Node w passes its gate, but while it builds the owner checks the target branch out
    in the main worktree: the level's full gate passes and its tip cannot land (p2)."""
    stub_plans(repo, {"areas-writing-reading": WR_PLAN})
    checkout = f"git -C '{repo}' checkout -q build/notes"
    stub_rounds(repo, {"w": {"rounds": [{**_r({"w.txt": "w\n"}), "run": checkout}]}})


# ---------------------------------------------------------------- the resume view (pure)


def test_the_resume_view_of_streaks_real_p2_record() -> None:
    rec = json.loads((FIXTURES / "streak-p2-land-waiting.json").read_text())
    lw = land_waiting(rec)
    assert lw is not None and lw["level"] == 1 and lw["tip"].startswith("9417351")
    assert lw["merged"] == ["auth"] and lw["regressions"] == []
    owner = "/Users/vivekgade/Documents/streak"
    v = resume_view(rec, checked_out_at=owner, owner_worktree=owner, fast_forward=True)
    assert v["available"] is True and v["kind"] == "land" and v["from_level"] == 1
    assert v["will_skip"] == ["scaffold", "auth"]
    assert v["will_run"][:3] == ["account", "habits-core", "habits-edit"]
    assert v["will_run"][-1] == "integration" and len(v["will_run"]) == 10
    assert v["note"] == (
        "Level 1 passed its check but could not be added to build/streak because that branch "
        "is checked out in your working copy. Switch it away and press Land."
    )
    free = resume_view(rec, checked_out_at=None, owner_worktree=owner, fast_forward=True)
    assert free["kind"] == "land" and free["note"].endswith("It is free now: press Land.")
    moved = resume_view(rec, checked_out_at=None, owner_worktree=owner, fast_forward=False)
    assert "re-runs the full check; no agent runs again" in moved["note"]
    elsewhere = resume_view(rec, checked_out_at="/tmp/wt", owner_worktree=owner)
    assert "checked out in /tmp/wt" in elsewhere["note"]
    # a passed, landed plan has nothing to resume
    done = {**rec, "status": "passed"}
    assert resume_view(done)["available"] is False


# ---------------------------------------------------------------- reconciliation on load


def test_a_running_plan_and_its_orphans_are_reconciled_on_load(repo: Path, tmp_path: Path) -> None:  # noqa: F811
    stub_plans(repo, {"rules-r4": {"nodes": [node("r", scope={"rules": ["R4"]}), integ("r")]}})
    pm, _log = manager(repo, tmp_path)
    pid = pm.create({"scope": {"rules": ["R4"]}})["id"]
    assert pm.get(pid, wait=30)["status"] == "ready"
    pm.approve(pid)
    store = pm.store
    assert store is not None
    # what a crash leaves on disk: the plan running, a node and a level running, two
    # recorded child processes -- a real orphan, and a pid now used by an unrelated process
    orphan = subprocess.Popen(["sleep", "300"], start_new_session=True)
    decoy = subprocess.Popen(["sleep", "301"], start_new_session=True)
    try:
        assert wait_for(lambda: alive(orphan.pid) and alive(decoy.pid), 5)
        info = proc_info(orphan.pid, "gate", "sleep 300")
        fake = {**proc_info(decoy.pid, "agent", "claude"), "command": "claude -p --model opus"}
        rec = pm.get(pid)
        rec["status"] = "running"
        rec["runs"] = 1
        rec["nodes"][0]["status"] = "running"
        rec["level_runs"] = [{"level": 0, "run": 1, "base": "x", "nodes": ["r"],
                              "status": "running", "merged": [], "merge_conflicts": [],
                              "tip": None, "gate": None, "regressions": [], "blame": [],
                              "started": "t", "finished": None}]  # fmt: skip
        rec["pids"] = [info, fake]
        store.save("plans", pid, rec, pm._private(pm.plans[pid]))
        pm2, _ = manager(repo, tmp_path)
        got = pm2.get(pid)
        assert got["status"] == "interrupted" and got["interrupted"] is True
        assert got["interrupted_reason"] == "studio_restarted" and got["interrupted_at"]
        assert got["nodes"][0]["status"] == "interrupted"
        assert got["level_runs"][0]["status"] == "interrupted"
        assert got["pids"] == [] and [k["pid"] for k in got["orphans_killed"]] == [orphan.pid]
        assert any("interrupted while running" in e["event"] for e in got["timeline"])
        assert got["resume"]["available"] and got["resume"]["kind"] == "continue"
        assert wait_for(lambda: orphan.poll() is not None, 10), "the orphan was not terminated"
        assert decoy.poll() is None and alive(decoy.pid), "a pid alone must never be killed"
        # stored: a third Studio sees the same interrupted record (no second reconcile)
        pm3, _ = manager(repo, tmp_path)
        assert pm3.get(pid)["interrupted_at"] == got["interrupted_at"]
    finally:
        for p in (orphan, decoy):
            if p.poll() is None:
                p.kill()
            p.wait(5)


def test_an_interrupted_build_is_reconciled_and_its_orphan_killed(
    repo: Path, tmp_path: Path
) -> None:  # noqa: F811
    store = StateStore(tmp_path / "state")
    orphan = subprocess.Popen(["/bin/sh", "-c", "sleep 300"], start_new_session=True)
    try:
        assert wait_for(lambda: alive(orphan.pid), 5)
        store.save("builds", "b1", {
            "id": "b1", "project": "notes", "changes": [], "status": "running", "model": "m",
            "agent": "stub", "max_rounds": 2, "started": "t", "finished": None,
            "rounds": [{"n": 1, "check": {"verdict": "fail", "counts": {}, "findings": []}},
                       {"n": 2, "check": None}],
            "targets": [], "gates": ["./gate.sh"], "base": "x", "branch": "rebuild/x",
            "worktree": None, "merge_hint": None, "usage": {"wall_s": 3.0}, "warnings": [],
            "error": None, "pids": [proc_info(orphan.pid, "agent", "sleep 300")],
        })  # fmt: skip
        rb = Rebuilder(repo, {"gates": ["./gate.sh"], "agent": "stub"}, name="notes",
                       model="m", store=store)  # fmt: skip
        rec = rb.get("b1")
        assert rec["status"] == "interrupted" and rec["interrupted_reason"] == "studio_restarted"
        assert [r["n"] for r in rec["rounds"]] == [1]  # the unfinished round is dropped
        assert rec["orphans_killed"][0]["pid"] == orphan.pid
        assert wait_for(lambda: orphan.poll() is not None, 10)
        assert rb.running() is None  # an interrupted build holds no slot
        with pytest.raises(RebuildError) as e:
            rb.stop("b1")
        assert e.value.status == 409
    finally:
        if orphan.poll() is None:
            orphan.kill()
        orphan.wait(5)


# ---------------------------------------------------------------- landing (the p2 case)


def test_a_gated_level_that_could_not_land_is_landed_without_any_agent(
    repo: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    checkout_target_during_w(repo)
    pm, log = manager(repo, tmp_path)
    main = git(repo, "rev-parse", "main")
    rec = ready_run(pm, {"scope": {"areas": ["writing", "reading"]}})
    pid = rec["id"]
    assert rec["status"] == "failed", rec
    lr = rec["level_runs"][0]
    assert lr["status"] == "failed" and lr["error"]["kind"] == "branch_checked_out"
    assert lr["regressions"] == [] and lr["tip"] and lr["gated"] is True
    assert rec["nodes"][0]["status"] == "regressed" and not rec["nodes"][0]["merged"]
    assert git(repo, "rev-parse", "build/notes") == main  # the target never moved
    # the gated tip and the node commit are pinned in the repository
    assert git(repo, "rev-parse", f"refs/lattice/plans/{pid}/L0") == lr["tip"]
    assert git(repo, "rev-parse", f"refs/lattice/plans/{pid}/w") == rec["nodes"][0]["commit"]
    v = rec["resume"]
    assert v["kind"] == "land" and v["available"] and v["from_level"] == 0
    assert v["will_skip"] == ["w"] and v["will_run"] == ["r", "integration"]
    assert v["note"] == (
        "Level 0 passed its check but could not be added to build/notes because that branch "
        "is checked out in your working copy. Switch it away and press Land."
    )
    w_runs = runs_of(log, "w")
    assert w_runs == 1
    # still checked out: land and run refuse, nothing moves
    with pytest.raises(PlanError) as e:
        pm.land(pid)
    assert e.value.status == 409 and e.value.code == "branch_checked_out"
    with pytest.raises(PlanError) as e:
        pm.run(pid)
    assert e.value.status == 409 and e.value.code == "branch_checked_out"
    assert git(repo, "rev-parse", "build/notes") == main
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "build/notes"
    # the owner switches away; Land fast-forwards the target to the gated tip, no agent
    git(repo, "checkout", "-q", "main")
    out = pm.land(pid)
    assert out == {"id": pid, "status": "partial", "landed": True, "remerge": False,
                   "target_tip": lr["tip"]}  # fmt: skip
    assert git(repo, "rev-parse", "build/notes") == lr["tip"]
    assert runs_of(log, "w") == w_runs
    rec = pm.get(pid)
    assert (
        rec["level_runs"][0]["landed_by"] == "resume" and rec["level_runs"][0]["status"] == "passed"
    )
    assert rec["level_runs"][0]["land_error"]["kind"] == "branch_checked_out"
    assert rec["nodes"][0]["status"] == "passed" and rec["nodes"][0]["merged"]
    assert rec["resume"]["kind"] == "continue" and rec["resume"]["from_level"] == 1
    with pytest.raises(PlanError) as e:
        pm.land(pid)
    assert (e.value.status, e.value.code) == (409, "nothing_to_land")
    # Run goes on with level 1; w's agent never runs again
    pm.run(pid)
    rec = pm.get(pid, wait=60)
    assert rec["status"] == "passed", rec
    assert runs_of(log, "w") == w_runs and runs_of(log, "r") == 1
    assert git(repo, "show", "build/notes:r.txt") == "r"
    assert git(repo, "rev-parse", "main") == main  # the owner's branch never moved


def test_a_moved_target_is_re_merged_and_re_gated_only(repo: Path, tmp_path: Path) -> None:  # noqa: F811
    checkout_target_during_w(repo)
    pm, log = manager(repo, tmp_path)
    rec = ready_run(pm, {"scope": {"areas": ["writing", "reading"]}})
    pid = rec["id"]
    gated = rec["level_runs"][0]["tip"]
    # the owner commits on the checked-out target, then switches away: no fast-forward
    (repo / "owner.txt").write_text("mine\n")
    git(repo, "add", "owner.txt")
    git(repo, "commit", "-q", "-m", "owner work on the target")
    moved_to = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    v = pm.get(pid)["resume"]
    assert v["kind"] == "land" and "has moved since" in v["note"]
    w_runs = runs_of(log, "w")
    out = pm.land(pid)
    assert out["status"] == "running" and out["remerge"] is True and out["landed"] is False
    rec = pm.get(pid, wait=60)
    assert rec["status"] == "partial", rec
    again = rec["level_runs"][-1]
    assert (
        again["remerge"] is True and again["landed_by"] == "resume" and again["status"] == "passed"
    )
    assert again["merged"] == ["w"] and again["regressions"] == []
    tip = git(repo, "rev-parse", "build/notes")
    assert tip == again["tip"] != gated
    assert git(repo, "merge-base", "--is-ancestor", moved_to, tip) == ""  # history kept
    assert git(repo, "show", "build/notes:owner.txt") == "mine"
    assert git(repo, "show", "build/notes:w.txt") == "w"
    assert runs_of(log, "w") == w_runs  # no agent ran for the landing
    pm.run(pid)
    rec = pm.get(pid, wait=60)
    assert rec["status"] == "passed" and runs_of(log, "w") == w_runs


# ---------------------------------------------------------------- retries and the cap


def test_retries_carry_the_earlier_findings_and_stop_at_the_cap(repo: Path, tmp_path: Path) -> None:  # noqa: F811
    stub_plans(repo, {"rules-r2": {"nodes": [node("broken", scope={"rules": ["R2"]}),
                                             integ("broken")]}})  # fmt: skip
    pm, log = manager(repo, tmp_path, max_rounds=1, max_attempts=2)
    rec = ready_run(pm, {"scope": {"rules": ["R2"]}})
    pid = rec["id"]
    n = rec["nodes"][0]
    assert rec["status"] == "failed" and n["status"] == "gave_up" and n["attempts"] == 1
    v = rec["resume"]
    assert v["kind"] == "retry" and v["available"] and "attempt 2 of 2" in v["note"]
    pm.run(pid)
    rec = pm.get(pid, wait=60)
    n = rec["nodes"][0]
    assert n["status"] == "gave_up" and n["attempts"] == 2 and len(n["builds"]) == 2
    assert n["previous_findings"] and "scenario:create_a_note" in " ".join(n["previous_findings"])
    note = pm.rb.runs[n["builds"][1]].plan_note
    assert "A previous attempt ended with these findings:" in note
    assert n["rounds"] == 2 and rec["totals"]["rounds"] == 2  # metrics add up across attempts
    assert pm.rb.get(n["builds"][1])["attempts"] == 2
    v = rec["resume"]
    assert v["available"] is False and "already had 2 attempts" in v["note"]
    with pytest.raises(PlanError) as e:
        pm.run(pid)
    assert e.value.status == 409 and "max_attempts = 2" in e.value.detail
    assert runs_of(log, "broken") == 2


# ---------------------------------------------------------------- stand-alone builds


def test_an_interrupted_build_continues_from_its_next_round(repo: Path, tmp_path: Path) -> None:  # noqa: F811
    flag = tmp_path / "go"
    stub_rounds(repo, {"rules-r2": {"rounds": [
        _r({"draft.txt": "1"}),
        {**_r({"w.txt": "w\n"}), "run": f"while [ ! -f '{flag}' ]; do sleep 0.1; done"},
    ]}})  # fmt: skip
    log = tmp_path / "agent.log"
    env = {**os.environ, STUB_LOG_ENV: str(log)}
    table = {"gates": ["./gate.sh"], "max_rounds": 3, "agent": "stub"}
    store = StateStore(tmp_path / "state")
    rb1 = Rebuilder(repo, table, name="notes", model="m", env=env, store=store)
    rid = rb1.start([], scope={"rules": ["R2"]})["id"]
    assert wait_for(lambda: any(p["role"] == "agent" for p in rb1.get(rid).get("pids") or []))
    child = next(p for p in rb1.get(rid)["pids"] if p["role"] == "agent")
    # "the crash": this Rebuilder stops writing and its thread is cut off; a new one loads
    rb1.store = None
    rb1._cleanup_stopped = lambda _run: None  # type: ignore[method-assign]
    rb1.runs[rid].stop.set()
    rb2 = Rebuilder(repo, table, name="notes", model="m", env=env, store=store)
    rec = rb2.get(rid)
    assert rec["status"] == "interrupted" and [r["n"] for r in rec["rounds"]] == [1]
    assert wait_for(lambda: not alive(child["pid"]), 10)
    rb1.runs[rid].thread.join(15)
    flag.write_text("go")
    with pytest.raises(RebuildError):
        rb2.stop(rid)
    out = rb2.continue_build(rid)
    assert out == {"id": rid, "status": "running", "from_round": 2, "attempts": 2}
    rec = rb2.get(rid, wait=60)
    assert rec["status"] == "passed", rec
    assert [r["n"] for r in rec["rounds"]] == [1, 2] and rec["attempts"] == 2
    assert any(e["event"].startswith("continued from round 2") for e in rec["timeline"])
    assert [x["round"] for x in agent_log(log) if x["build"] == rid] == [1, 2, 2]
    with pytest.raises(RebuildError) as e:
        rb2.continue_build(rid)
    assert e.value.status == 409


# ---------------------------------------------------------------- a REAL restart


def _free_port() -> int:
    for port in range(8900, 9000):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port in 8900-8999")


def _serve(project: Path, env: dict[str, str], logf: Path) -> tuple[subprocess.Popen, str]:
    port = _free_port()
    err = logf.open("a")
    proc = subprocess.Popen(
        [sys.executable, "-m", "lattice.studio", str(project), "--port", str(port), "--quiet",
         "--stub-llm"],
        env=env, cwd=str(project), stdout=subprocess.DEVNULL, stderr=err, start_new_session=True,
    )  # fmt: skip
    err.close()
    base = f"http://127.0.0.1:{port}"

    def up() -> bool:
        try:
            return request("GET", base + "/api/model")[0] == 200
        except OSError:
            return False

    assert wait_for(up, 60, 0.2), logf.read_text()[-3000:]
    return proc, base


RESTART_PLAN = {
    "nodes": [
        node("scaffold", "scaffold", files=["scaffold.txt"]),
        node("writing", scope={"area": "writing-notes"}, deps=["scaffold"], files=["writing.txt"]),
        node("reading", scope={"area": "reading-notes"}, deps=["writing"], files=["reading.txt"]),
        integ("reading"),
    ]
}


def test_a_studio_killed_mid_plan_resumes_without_rerunning_passed_nodes(tmp_path: Path) -> None:
    project = tmp_path / "notes"
    shutil.copytree(SEED, project)
    flag = tmp_path / "flag"
    (project / "stub-plan.json").write_text(json.dumps({"scopes": {"all": RESTART_PLAN}}))
    (project / "stub-rebuild.json").write_text(json.dumps({"rounds": [], "scopes": {
        "scaffold": {"rounds": [_r({"scaffold.txt": "s\n"})]},
        "writing": {"rounds": [_r({"writing.txt": "w\n"})]},
        "reading": {"rounds": [{**_r({"reading.txt": "r\n"}),
                                "run": f"while [ ! -f '{flag}' ]; do sleep 0.2; done"}]},
    }}))  # fmt: skip
    git(project, "init", "-q", "-b", "main")
    git(project, "config", "user.email", "restart@example.com")
    git(project, "config", "user.name", "Restart Test")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "seed")
    log = tmp_path / "agent.log"
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env.update({"PYTHONPATH": PKG_ROOT, STUB_LOG_ENV: str(log), "ANTHROPIC_API_KEY": ""})
    srv_log = tmp_path / "server.log"
    procs: list[subprocess.Popen] = []
    orphan_pid = None
    try:
        srv, base = _serve(project, env, srv_log)
        procs.append(srv)
        assert request("POST", base + "/api/plan", {"scope": "all"})[1] == {
            "id": "p1",
            "status": "planning",
        }
        rec = request("GET", base + "/api/plan/p1?wait=30")[1]
        assert rec["status"] == "ready" and rec["levels"] == [
            ["scaffold"],
            ["writing"],
            ["reading"],
            ["integration"],
        ]
        assert request("POST", base + "/api/plan/p1/approve", {})[0] == 200
        assert request("POST", base + "/api/plan/p1/run", {})[0] == 200

        def blocked() -> bool:
            nonlocal orphan_pid
            plan = request("GET", base + "/api/plan/p1")[1]
            n = {x["id"]: x for x in plan["nodes"]}["reading"]
            if n["status"] != "running" or not n.get("build_id"):
                return False
            b = request("GET", base + f"/api/rebuild/{n['build_id']}")[1]
            agents = [p for p in b.get("pids") or [] if p["role"] == "agent" and p["started"]]
            if agents:
                orphan_pid = agents[0]["pid"]
            return bool(agents)

        assert wait_for(blocked, 90, 0.2), srv_log.read_text()[-3000:]
        assert orphan_pid is not None and alive(orphan_pid)
        assert runs_of(log, "scaffold") == 1 and runs_of(log, "writing") == 1
        srv.send_signal(signal.SIGKILL)  # the crash
        srv.wait(10)
        assert alive(orphan_pid)  # its agent is left behind, as after a real crash
        srv2, base2 = _serve(project, env, srv_log)
        procs.append(srv2)
        rec = request("GET", base2 + "/api/plan/p1")[1]
        assert rec["status"] == "interrupted" and rec["interrupted"] is True
        assert rec["interrupted_reason"] == "studio_restarted"
        by = {n["id"]: n for n in rec["nodes"]}
        assert [by[i]["status"] for i in ("scaffold", "writing", "reading", "integration")] == [
            "passed",
            "passed",
            "interrupted",
            "pending",
        ]
        assert by["scaffold"]["merged"] and by["writing"]["merged"]
        assert [lr["status"] for lr in rec["level_runs"]] == ["passed", "passed", "interrupted"]
        v = rec["resume"]
        assert v["available"] and v["kind"] == "continue" and v["from_level"] == 2
        assert v["will_skip"] == ["scaffold", "writing"]
        assert v["will_run"] == ["reading", "integration"]
        assert wait_for(lambda: not alive(orphan_pid), 10), "the orphan agent was not killed"
        build = request("GET", base2 + f"/api/rebuild/{by['reading']['build_id']}")[1]
        assert build["status"] == "interrupted"
        assert [k["pid"] for k in build["orphans_killed"]] == [orphan_pid]
        flag.write_text("go")
        assert request("POST", base2 + "/api/plan/p1/run", {}) == (
            200,
            {"id": "p1", "status": "running"},
        )
        rec = request("GET", base2 + "/api/plan/p1?wait=60")[1]
        assert rec["status"] == "passed", rec
        assert runs_of(log, "scaffold") == 1 and runs_of(log, "writing") == 1
        assert runs_of(log, "reading") == 2
        by = {n["id"]: n for n in rec["nodes"]}
        assert by["reading"]["attempts"] == 2 and by["scaffold"]["attempts"] == 1
        assert rec["runs"] == 2 and rec["attempts"] == 2
        assert git(project, "show", "build/notes:reading.txt") == "r"
        assert git(project, "rev-parse", "main") == git(project, "rev-parse", "HEAD")
        assert git(project, "status", "--porcelain", "--untracked-files=all") == ""
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(15)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(5)
        if orphan_pid is not None and alive(orphan_pid):
            os.killpg(orphan_pid, signal.SIGKILL)


# ---------------------------------------------------------------- the mock (parity)


def test_the_mock_serves_resume_fields_and_fixtures() -> None:
    import threading

    from lattice.studio.mock_server import make_server

    srv = make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    second = base + "/p/lattice-studio"
    try:
        listing = request("GET", second + "/api/plans")[1]
        assert [(p["id"], p["status"]) for p in listing][:2] == [
            ("p1", "interrupted"),
            ("p2", "partial"),
        ]
        assert listing[0]["resume"]["kind"] == "continue" and listing[0]["interrupted"] is True
        rec = request("GET", second + "/api/plan/p2")[1]
        v = rec["resume"]
        assert v["kind"] == "land" and v["available"]
        assert "checked out in your working copy. Switch it away and press Land." in v["note"]
        assert rec["interrupted"] is False and rec["attempts"] == 1
        assert isinstance(rec["timeline"], list) and rec["timeline"]
        status, body = request("POST", second + "/api/plan/p2/land", {})
        assert status == 409 and body["code"] == "branch_checked_out"
        status, body = request("POST", second + "/api/plan/p2/land",
                               {"confirm_unsafe_branch": True})  # fmt: skip
        assert status == 200 and body["landed"] is True and body["status"] == "partial"
        assert request("POST", second + "/api/plan/p2/land", {})[1]["code"] == "nothing_to_land"
        assert request("GET", second + "/api/plan/p2")[1]["resume"]["kind"] == "continue"
        p1 = request("GET", second + "/api/plan/p1")[1]
        kept = {n["id"] for n in p1["nodes"] if n["status"] == "passed"}
        assert kept and p1["resume"]["will_skip"] == [i for lvl in p1["levels"] for i in lvl
                                                      if i in kept]  # fmt: skip
        assert request("POST", second + "/api/plan/p1/run", {})[1]["status"] == "running"
        for _ in range(4 * len(p1["levels"]) + 2):
            p1 = request("GET", second + "/api/plan/p1")[1]
        assert p1["status"] == "passed"
        assert all(n["attempts"] == 1 for n in p1["nodes"] if n["id"] in kept)
        assert request("DELETE", second + "/api/plan/p2?forget=true")[1]["status"] == "forgotten"
        assert request("GET", second + "/api/plan/p2")[0] == 404
        # the default project: a new plan carries the fields too
        assert request("POST", base + "/api/plan", {"scope": "all"})[1]["id"] == "p1"
        fresh = request("GET", base + "/api/plan/p1")[1]
        assert fresh["resume"]["available"] is False and fresh["interrupted"] is False
        assert request("POST", base + "/api/plan/p1/land", {})[0] == 409
    finally:
        srv.shutdown()
        srv.server_close()
