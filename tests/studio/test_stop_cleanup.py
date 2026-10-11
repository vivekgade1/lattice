"""Stop and budget hygiene (API.md v1.11): a stopped (or timed-out) gate's whole process
tree goes -- SIGTERM to its group, ``[rebuild] stop_grace`` seconds for the check's own
cleanup, SIGKILL, then every recorded descendant still alive (an app that ignores SIGTERM
in its own session included) -- and the containers labelled with the gate's owner token are
removed, never any other (a FAKE docker CLI: no real Docker); a restart's reconciliation
does the same from the recorded ``pids``; an agent that times out or passes
``node_max_tokens`` keeps the usage the stream showed (a FAKE claude). No LLM."""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from lattice.studio.rebuild import Rebuilder, StreamUsage, load_table, reap_orphans
from lattice.studio.state import proc_info
from tests.studio.seed import cleanup_worktrees, finish, manager, seed_project

CHILD = """import os, signal, sys, time
os.setsid()                                   # its own session: the gate's group kill misses it
signal.signal(signal.SIGTERM, signal.SIG_IGN)  # and it ignores SIGTERM
open(sys.argv[1], "w").write(str(os.getpid()))
time.sleep(300)
"""

FAKE_DOCKER = """#!/bin/sh
echo "$@" >> "$FAKE_DOCKER_DIR/docker.log"
if [ "$1" = ps ]; then
  for a in "$@"; do
    case "$a" in label=lattice.owner=*) tok="${a#label=lattice.owner=}" ;; esac
  done
  if [ -n "$tok" ] && [ -f "$FAKE_DOCKER_DIR/owner.txt" ] && \\
     [ "$(cat "$FAKE_DOCKER_DIR/owner.txt")" = "$tok" ]; then
    echo "lattice-mysql-0bad1dea"
    echo "someone-elses-db"
  fi
fi
exit 0
"""


def gate(d: Path, *, then: str = "sleep 300") -> str:
    return f"""#!/bin/sh
echo "$LATTICE_CONTAINER_OWNER" > "{d}/owner.txt"
"{sys.executable}" "{d}/child.py" "{d}/child.pid" &
{then}
"""


@pytest.fixture()
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "fake"
    d.mkdir()
    (d / "child.py").write_text(CHILD)
    docker = d / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("LATTICE_STUDIO_DOCKER", str(docker))
    monkeypatch.setenv("FAKE_DOCKER_DIR", str(d))
    yield d
    p = d / "child.pid"
    if p.exists():  # never leave the test's own child behind, whatever happened
        try:
            os.kill(int(p.read_text()), signal.SIGKILL)
        except (ProcessLookupError, ValueError):
            pass


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie is gone for our purpose
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(out.stdout.strip()) and not out.stdout.strip().startswith("Z")


def wait_for(pred, timeout: float = 20.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def docker_log(d: Path) -> list[str]:
    p = d / "docker.log"
    return p.read_text().splitlines() if p.exists() else []


def rebuilder(root: Path, **table: Any) -> Rebuilder:
    t = {**load_table(root), "stop_grace": 0.5, **table}
    return Rebuilder(root, t, name="notes", model="sonnet", env=dict(os.environ))


def recorded_child(rb: Rebuilder, rid: str) -> bool:
    return any(p.get("role") == "gate-child" for p in rb.get(rid).get("pids") or [])


def test_stop_kills_the_gates_whole_tree_and_its_containers(tmp_path: Path, fake: Path) -> None:
    root = seed_project(tmp_path, gate=gate(fake))
    rb = rebuilder(root)
    rid = rb.start([], scope={"area": "writing-notes"}, baseline_wait=0.3)["id"]
    wait_for(lambda: (fake / "child.pid").exists() and recorded_child(rb, rid))
    child = int((fake / "child.pid").read_text())
    assert alive(child)
    t0 = time.time()
    assert rb.stop(rid) == {"id": rid, "status": "stopped"}
    wait_for(lambda: not alive(child), timeout=10)
    assert time.time() - t0 < 10
    rec = rb.get(rid)
    assert rec["status"] == "stopped" and rec["pids"] == []
    log = docker_log(fake)
    token = (fake / "owner.txt").read_text().strip()
    assert token.startswith("studio-") and f"ps -a --filter label=lattice.owner={token} " \
        "--format {{.Names}}" in log  # fmt: skip
    assert "rm -f -v lattice-mysql-0bad1dea" in log
    assert not any("someone-elses-db" in x for x in log if x.startswith("rm"))
    cleanup_worktrees(root)


def test_a_gate_timeout_kills_the_tree_too(tmp_path: Path, fake: Path) -> None:
    root = seed_project(tmp_path, gate=gate(fake))
    rb = rebuilder(root, gate_timeout=2)
    rid = rb.start([], scope={"area": "writing-notes"}, baseline_wait=30)["id"]
    rec = rb.get(rid, wait=30)
    assert rec["status"] == "gave_up" and rec["error"]["kind"] == "baseline", rec
    assert rec["baseline"]["gates"][0]["exit_code"] == 124
    assert "timed out after 2s" in rec["baseline"]["gates"][0]["output_tail"]
    child = int((fake / "child.pid").read_text())
    wait_for(lambda: not alive(child), timeout=10)
    assert "rm -f -v lattice-mysql-0bad1dea" in docker_log(fake)
    cleanup_worktrees(root)


def test_a_child_left_by_a_gate_that_exited_is_killed(tmp_path: Path, fake: Path) -> None:
    then = 'sleep 2\necho "FAIL: 1 findings (G 1)"\necho "FAIL x (scenario:create_a_note)"\nexit 1'
    root = seed_project(tmp_path, gate=gate(fake, then=then))
    rb = rebuilder(root, max_rounds=1)
    rid = rb.start([], scope={"area": "writing-notes"}, baseline_wait=60)["id"]
    child = int((fake / "child.pid").read_text())
    wait_for(lambda: not alive(child), timeout=10)  # after the baseline gate exited
    rec = rb.get(rid, wait=30)
    assert rec["baseline"]["findings"][0]["target"] == "scenario:create_a_note"
    cleanup_worktrees(root)


def test_a_plan_stop_during_a_gate_kills_its_tree(tmp_path: Path, fake: Path) -> None:
    root = seed_project(tmp_path, gate=gate(fake))
    pm = manager(root, stop_grace=0.5)
    pid = pm.create({"scope": "all"})["id"]
    assert pm.get(pid, wait=30)["status"] == "ready"
    pm.approve(pid)
    pm.run(pid)
    wait_for(lambda: (fake / "child.pid").exists() and any(
        p.get("role") == "gate-child" for p in pm.get(pid).get("pids") or []))  # fmt: skip
    child = int((fake / "child.pid").read_text())
    assert pm.stop(pid)["status"] == "stopped"
    wait_for(lambda: not alive(child), timeout=10)
    assert pm.get(pid)["pids"] == []
    assert "rm -f -v lattice-mysql-0bad1dea" in docker_log(fake)
    cleanup_worktrees(root)


def test_a_restart_reaps_recorded_children_and_containers(tmp_path: Path, fake: Path) -> None:
    p = subprocess.Popen([sys.executable, str(fake / "child.py"), str(fake / "child.pid")])
    wait_for(lambda: (fake / "child.pid").exists())
    pid = int((fake / "child.pid").read_text())
    info = proc_info(pid, "gate-child", "")
    info["match"] = info["command"]
    (fake / "owner.txt").write_text("studio-1-abc\n")
    stale = {**info, "started": "Mon Jan  1 00:00:00 2001"}  # a reused pid: never killed
    killed = reap_orphans(
        [stale, info, {"pid": 999999, "role": "gate", "containers": "studio-1-abc"}],
        0.5,
        lambda m: None,
    )
    assert {k.get("pid") for k in killed if k.get("role") == "gate-child"} == {pid}
    assert {"container": "lattice-mysql-0bad1dea", "role": "container"} in killed
    p.wait(10)
    assert p.returncode is not None


FAKE_CLAUDE = """#!{python}
import json, os, sys, time
if "--version" in sys.argv:
    print("2.0.0"); sys.exit(0)
out = int(os.environ.get("FAKE_OUT", "50"))
print(json.dumps({{"type": "system", "subtype": "init", "session_id": "sess-1"}}), flush=True)
for i in range(3):
    u = {{"input_tokens": 100, "output_tokens": out, "cache_read_input_tokens": 1000,
         "cache_creation_input_tokens": 10}}
    for block in ({{"type": "text", "text": "working"}}, {{"type": "tool_use", "id": "t%d" % i,
                  "name": "Read", "input": {{}}}}):
        print(json.dumps({{"type": "assistant", "message": {{"id": "m%d" % i,
              "content": [block], "usage": u}}}}), flush=True)
    time.sleep(0.2)
time.sleep(60)
"""


def fake_claude(d: Path) -> str:
    exe = d / "claude"
    exe.write_text(FAKE_CLAUDE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def test_stream_usage_counts_each_message_once() -> None:
    su = StreamUsage()
    m = {"id": "m1", "usage": {"input_tokens": 5, "output_tokens": 7}}
    su.feed({"type": "assistant", "message": m})
    su.feed({"type": "assistant", "message": m})
    su.feed({"type": "assistant", "parent_tool_use_id": "x", "message": {"id": "s",
             "usage": {"output_tokens": 99}}})  # a subagent's: not ours  # fmt: skip
    assert su.totals()["output_tokens"] == 7 and su.output_tokens() == 7


def test_an_agent_timeout_keeps_the_usage_seen(tmp_path: Path) -> None:
    root = seed_project(tmp_path)
    exe = fake_claude(tmp_path)
    t = {**load_table(root), "agent": "claude", "round_timeout": 1.5, "max_rounds": 1}
    rb = Rebuilder(root, t, name="notes", model="sonnet", claude_bin=exe,
                   env={**os.environ, "FAKE_OUT": "50"})  # fmt: skip
    rid = rb.start([], scope={"area": "writing-notes"}, baseline_wait=60)["id"]
    rec = rb.get(rid, wait=60)
    assert rec["status"] == "gave_up", rec
    rnd = rec["rounds"][0]
    assert rnd["status"] == "timeout" and rnd["error"]["kind"] == "timeout"
    assert rnd["error"]["reason"] == "round_timeout"
    assert rnd["agent"]["partial"] is True and rnd["agent"]["output_tokens"] == 150
    assert rnd["agent"]["cache_read_input_tokens"] == 3000
    assert rec["usage"]["output_tokens"] == 150 and rec["usage"]["input_tokens"] == 300
    assert rnd["check"] is not None  # the gates still ran on what it wrote; files kept
    cleanup_worktrees(root)


def test_node_max_tokens_stops_the_agent_with_its_usage(tmp_path: Path) -> None:
    root = seed_project(tmp_path)
    exe = fake_claude(tmp_path)
    pm = manager(root, env={**os.environ, "FAKE_OUT": "80"}, agent="claude",
                 node_max_tokens=100, max_rounds=1, node_timeout=30)  # fmt: skip
    pm.rb.claude_bin = exe
    pid = pm.create({"scope": {"rules": ["R3"]}})["id"]
    assert pm.get(pid, wait=30)["status"] == "ready"
    pm.approve(pid)
    pm.run(pid)
    rec = finish(pm, pid)
    slow = next(n for n in rec["nodes"] if n["id"] == "slow")
    b = pm.rb.get(slow["build_id"])
    rnd = b["rounds"][0]
    assert rnd["status"] == "timeout" and rnd["error"]["reason"] == "node_max_tokens"
    assert rnd["agent"]["output_tokens"] == 160 and rnd["agent"]["partial"]
    assert slow["usage"]["tokens_out"] == 160  # the node's usage is not 0
    assert "Your output budget for this node is 100 tokens" in json.dumps(
        pm.rb.last_argv(slow["build_id"])
    )
    assert "You have 0 minutes" not in json.dumps(pm.rb.last_argv(slow["build_id"]))
    cleanup_worktrees(root)
