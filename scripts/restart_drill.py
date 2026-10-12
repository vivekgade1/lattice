"""The interrupt-and-resume drill: kill a REAL Studio server process with SIGKILL at three
dangerous moments of a build plan, start a new one on the same project, and check that the
plan resumes from where it stopped.

    uv run python scripts/restart_drill.py [--keep] [A B C]

Each drill runs on its own throwaway copy of ``dogfood/studio/project_seed`` (a fresh git
repository in a temp directory; the TEST-ONLY stub planner and stub agent; no LLM call, no
API key, no Docker) with a real ``python -m lattice.studio`` server:

  A  killed while an agent is in the middle of a node's round
  B  killed while a level's gate is running (the gate process is left behind)
  C  killed while a level-repair round is running (API v1.11)
  F  a fake `claude -p` killed mid-turn: the session is resumed (--resume) with a note
  R  a REAL `claude -p` (haiku) killed mid-turn, same check; spends tokens, so only on request

Checked after the restart, in every drill: the plan is ``interrupted`` with reason
``studio_restarted``; the orphaned process is terminated; ``resume`` names what is skipped
and what runs; after ``POST /api/plan/p1/run`` the plan ends ``passed``; every agent round
that had completed before the kill ran exactly once in all (the stub agent's invocation
log); the owner's branch never moved; no process of the project is left. Exit 0 = all
drills clean. The report is printed as a table and kept as JSON in the temp directory.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "dogfood" / "studio" / "project_seed"
LOG_ENV = "LATTICE_STUB_AGENT_LOG"

# gate.sh with one addition: while $DRILL_HOLD exists, a LEVEL gate (its --only-file is
# level-selection.json) announces itself with $DRILL_HOLD.entered and waits
GATE_HOLD = r"""
case "$only" in
  */level-selection.json)
    if [ -n "$DRILL_HOLD" ] && [ -f "$DRILL_HOLD" ]; then
      : > "$DRILL_HOLD.entered"
      while [ -f "$DRILL_HOLD" ]; do sleep 0.2; done
    fi ;;
esac
"""


def http(method: str, url: str, body: Any = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method=method, headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def wait_for(cond, timeout: float = 60.0, step: float = 0.2) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    r = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(r.stdout.strip()) and not r.stdout.strip().startswith("Z")


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


class Drill:
    """One throwaway project and its servers."""

    def __init__(self, name: str, tmp: Path) -> None:
        self.name = name
        self.dir = tmp / name
        self.dir.mkdir()
        self.project = self.dir / "notes"
        self.log = self.dir / "agent.log"
        self.srv_log = self.dir / "server.log"
        self.hold = self.dir / "hold"
        self.procs: list[subprocess.Popen] = []
        self.checks: list[tuple[str, bool, str]] = []
        self.facts: dict[str, Any] = {}
        self.env_extra: dict[str, str] = {}

    # -- project
    def make_project(
        self,
        scopes: dict[str, Any] | None = None,
        rounds: dict[str, Any] | None = None,
        gate_hold: bool = False,
        toml: str | None = None,
        gate: str | None = None,
    ) -> None:
        shutil.copytree(SEED, self.project)
        if gate is not None:
            (self.project / "gate.sh").write_text(gate)
            (self.project / "gate.sh").chmod(0o755)
        if toml is not None:
            (self.project / "studio.toml").write_text(toml)
        if scopes is not None:
            (self.project / "stub-plan.json").write_text(json.dumps({"scopes": scopes}))
        if rounds:
            f = self.project / "stub-rebuild.json"
            doc = json.loads(f.read_text())
            doc["scopes"].update(rounds)
            f.write_text(json.dumps(doc))
        if gate_hold:
            g = self.project / "gate.sh"
            text = g.read_text()
            marker = "selected() {"
            assert text.count(marker) == 1
            g.write_text(text.replace(marker, GATE_HOLD + marker))
        git(self.project, "init", "-q", "-b", "main")
        git(self.project, "config", "user.email", "drill@example.com")
        git(self.project, "config", "user.name", "Restart Drill")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-q", "-m", "seed")
        self.main_before = git(self.project, "rev-parse", "main")

    # -- server
    def serve(self) -> str:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
        env.update(self.env_extra)
        env.update(
            {
                "PYTHONPATH": str(ROOT),
                LOG_ENV: str(self.log),
                "DRILL_HOLD": str(self.hold),
                "ANTHROPIC_API_KEY": "",
            }
        )
        err = self.srv_log.open("a")
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "lattice.studio",
                str(self.project),
                "--port",
                str(port),
                "--quiet",
                "--stub-llm",
            ],
            env=env,
            cwd=str(self.project),
            stdout=subprocess.DEVNULL,
            stderr=err,
            start_new_session=True,
        )
        err.close()
        self.procs.append(proc)
        self.srv = proc
        base = f"http://127.0.0.1:{port}"

        def up() -> bool:
            try:
                return http("GET", base + "/api/model")[0] == 200
            except OSError:
                return False

        if not wait_for(up, 60):
            raise RuntimeError("server did not start:\n" + self.srv_log.read_text()[-2000:])
        return base

    def kill_server(self) -> None:
        self.srv.send_signal(signal.SIGKILL)
        self.srv.wait(10)

    # -- helpers
    def plan(self, base: str) -> dict[str, Any]:
        return http("GET", base + "/api/plan/p1")[1]

    def wait_done(self, base: str, timeout: float = 180.0) -> dict[str, Any]:
        """Poll until the plan is neither planning nor running (``?wait`` is capped at 60 s)."""
        t0 = time.time()
        rec = self.plan(base)
        while rec["status"] in ("planning", "running") and time.time() - t0 < timeout:
            rec = http("GET", base + "/api/plan/p1?wait=30")[1]
        return rec

    def completed(self, base: str) -> Counter[tuple[str, int]]:
        """(node, round) of every agent round that had finished, from the build records."""
        out: Counter[tuple[str, int]] = Counter()
        for n in self.plan(base)["nodes"]:
            if not n.get("build_id"):
                continue
            b = http("GET", base + f"/api/rebuild/{n['build_id']}")[1] or {}
            for r in b.get("rounds") or []:
                if r.get("finished"):
                    out[(str(n["id"]), int(r.get("n") or 0))] = 1
        return out

    def agent_runs(self) -> Counter[tuple[str, int]]:
        if not self.log.exists():
            return Counter()
        rows = [json.loads(x) for x in self.log.read_text().splitlines() if x.strip()]
        return Counter((str(r.get("key")), int(r.get("round") or 0)) for r in rows)

    def agent_pid(self, base: str, node: str) -> int | None:
        n = {x["id"]: x for x in self.plan(base)["nodes"]}.get(node)
        if not n or not n.get("build_id"):
            return None
        b = http("GET", base + f"/api/rebuild/{n['build_id']}")[1] or {}
        for p in b.get("pids") or []:
            if p.get("role") == "agent" and p.get("started"):
                return int(p["pid"])
        return None

    def check(self, what: str, ok: bool, evidence: str = "") -> None:
        self.checks.append((what, bool(ok), evidence))

    def start_plan(self, base: str, scope: Any, **extra: Any) -> None:
        assert http("POST", base + "/api/plan", {"scope": scope, **extra})[1]["id"] == "p1"
        assert wait_for(lambda: self.plan(base)["status"] == "ready", 60), self.plan(base)
        assert http("POST", base + "/api/plan/p1/approve", {})[0] == 200
        assert http("POST", base + "/api/plan/p1/run", {})[0] == 200

    def common_after_restart(self, base2: str, orphan: int | None) -> dict[str, Any]:
        rec = self.plan(base2)
        self.check(
            "plan is interrupted with reason studio_restarted",
            rec["status"] == "interrupted" and rec.get("interrupted_reason") == "studio_restarted",
            f"status={rec['status']} reason={rec.get('interrupted_reason')}",
        )
        if orphan is not None:
            self.check(
                "the orphaned process was terminated",
                wait_for(lambda: not alive(orphan), 15),
                f"pid {orphan}",
            )
        v = rec.get("resume") or {}
        self.facts["resume"] = {
            k: v.get(k) for k in ("available", "kind", "from_level", "will_skip", "will_run")
        }
        self.check(
            "resume is available", bool(v.get("available")), json.dumps(self.facts["resume"])
        )
        return rec

    def finish(
        self, base2: str, completed_before: Counter, expect_status: str = "passed"
    ) -> dict[str, Any]:
        self.hold.unlink(missing_ok=True)
        code, out = http("POST", base2 + "/api/plan/p1/run", {})
        self.check("POST run accepted", code == 200, f"{code} {out}")
        rec = self.wait_done(base2)
        self.check(
            f"plan ends {expect_status}",
            rec["status"] == expect_status,
            f"status={rec['status']} error={rec.get('error')}",
        )
        after = self.agent_runs()
        again = {f"{k[0]}#{k[1]}": after[k] for k in completed_before if after[k] != 1}
        self.check("no completed agent round ran again", not again, f"re-run: {again or 'none'}")
        self.facts["agent_runs"] = {f"{k[0]}#{k[1]}": n for k, n in sorted(after.items())}
        self.check(
            "the owner's branch never moved",
            git(self.project, "rev-parse", "main") == self.main_before,
        )
        self.facts["runs"] = rec.get("runs")
        self.facts["wall_s"] = (rec.get("totals") or {}).get("wall_s")
        self.facts["levels"] = [
            (lr.get("level"), lr.get("status")) for lr in rec.get("level_runs") or []
        ]
        return rec

    def cleanup(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(15)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(5)
        left = subprocess.run(["pgrep", "-f", str(self.project)], capture_output=True, text=True)
        pids = [int(x) for x in left.stdout.split() if x.strip() and int(x) != os.getpid()]
        self.check("no process of the project is left", not pids, f"pids {pids}")
        for pid in pids:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except OSError:
                pass


# -- the three drills ------------------------------------------------------------------------


def drill_a(d: Drill) -> None:
    """Killed in the middle of an agent's round (the node `reading`)."""
    flag = d.dir / "release"
    hold_cmd = f"while [ ! -f '{flag}' ]; do sleep 0.2; done"
    d.make_project(
        rounds={
            "reading": {
                "rounds": [
                    {
                        "summary": "reading: wrote reading.txt",
                        "files": {"reading.txt": "read\n"},
                        "run": hold_cmd,
                    }
                ]
            }
        }
    )
    base = d.serve()
    d.start_plan(base, "all")
    wait_for(lambda: d.agent_pid(base, "reading") is not None, 90)
    orphan = d.agent_pid(base, "reading")
    d.check(
        "an agent was running when the server was killed",
        orphan is not None and alive(orphan),
        f"pid {orphan}",
    )
    before = d.completed(base)
    d.facts["completed_before_kill"] = sorted(f"{k[0]}#{k[1]}" for k in before)
    passed = [n["id"] for n in d.plan(base)["nodes"] if n["status"] == "passed"]
    d.facts["passed_at_kill"] = passed
    d.kill_server()
    d.check("the agent outlived its server (a real crash)", orphan is not None and alive(orphan))
    base2 = d.serve()
    rec = d.common_after_restart(base2, orphan)
    by = {n["id"]: n for n in rec["nodes"]}
    d.check(
        "the node that was running is interrupted, passed nodes kept",
        by["reading"]["status"] == "interrupted"
        and all(by[i]["status"] == "passed" for i in passed),
        f"reading={by['reading']['status']} passed={passed}",
    )
    flag.write_text("go")
    done = d.finish(base2, before)
    n = {x["id"]: x for x in done["nodes"]}["reading"]
    d.check(
        "the interrupted node was continued (attempts 2), not restarted from scratch",
        n.get("attempts") == 2,
        f"attempts={n.get('attempts')}",
    )


def drill_b(d: Drill) -> None:
    """Killed while a level's gate runs: every agent of that level had already passed."""
    d.make_project(gate_hold=True)
    d.hold.write_text("hold")
    base = d.serve()
    d.start_plan(base, "all")
    entered = Path(str(d.hold) + ".entered")
    d.check(
        "a level gate was running when the server was killed",
        wait_for(entered.exists, 120),
        "waiting on the gate's announcement",
    )
    gate_pids = [p["pid"] for p in d.plan(base).get("pids") or [] if p.get("role") == "gate"]
    before = d.completed(base)
    d.facts["completed_before_kill"] = sorted(f"{k[0]}#{k[1]}" for k in before)
    d.kill_server()
    orphan = gate_pids[0] if gate_pids else None
    d.check("the gate outlived its server", orphan is not None and alive(orphan), f"pid {orphan}")
    base2 = d.serve()
    rec = d.common_after_restart(base2, orphan)
    d.check(
        "the level run that was gating is interrupted",
        any(lr.get("status") == "interrupted" for lr in rec.get("level_runs") or []),
        str([(lr.get("level"), lr.get("status")) for lr in rec.get("level_runs") or []]),
    )
    d.finish(base2, before)


def drill_c(d: Drill) -> None:
    """Killed in the middle of a level-repair round (a clash found by the level gate)."""
    flag = d.dir / "release"
    hold_cmd = f"while [ ! -f '{flag}' ]; do sleep 0.2; done"
    d.make_project(
        rounds={
            "w-clash": {
                "rounds": [
                    {
                        "summary": "w-clash: writing.txt, left.txt and a racy race.txt",
                        "files": {
                            "writing.txt": "written\n",
                            "left.txt": "left\n",
                            "race.txt": "racy\n",
                        },
                    },
                    {
                        "summary": "w-clash repair: race.txt is calm",
                        "files": {"race.txt": "calm\n"},
                        "run": hold_cmd,
                    },
                    {
                        "summary": "w-clash repair, continued after the restart: race.txt is calm",
                        "files": {"race.txt": "calm\n"},
                    },
                ]
            }
        }
    )
    base = d.serve()
    d.start_plan(base, {"areas": ["reading-notes", "writing-notes"]})
    wait_for(
        lambda: (
            d.agent_pid(base, "w-clash") is not None
            and any(r == 2 for (k, r) in d.agent_runs() if k == "w-clash")
        ),
        120,
    )
    orphan = d.agent_pid(base, "w-clash")
    d.check(
        "a repair round was running when the server was killed",
        orphan is not None and alive(orphan) and d.agent_runs().get(("w-clash", 2), 0) >= 1,
        f"pid {orphan}",
    )
    before = d.completed(base)
    d.facts["completed_before_kill"] = sorted(f"{k[0]}#{k[1]}" for k in before)
    d.facts["node_status_at_kill"] = {n["id"]: n["status"] for n in d.plan(base)["nodes"]}
    d.facts["level_runs_at_kill"] = [
        {k: lr.get(k) for k in ("level", "status")} for lr in d.plan(base).get("level_runs") or []
    ]
    d.kill_server()
    base2 = d.serve()
    rec = d.common_after_restart(base2, orphan)
    d.facts["node_status_after_restart"] = {n["id"]: n["status"] for n in rec["nodes"]}
    d.facts["level_runs_after_restart"] = [
        {k: lr.get(k) for k in ("level", "status")} for lr in rec.get("level_runs") or []
    ]
    flag.write_text("go")
    d.check(
        "the repair state was kept (level not discarded)",
        any(
            lr.get("repairs") or lr.get("repair_pending") or lr.get("repair_resume")
            for lr in rec.get("level_runs") or []
        ),
        json.dumps(
            [
                {k: lr.get(k) for k in ("level", "status", "repair_pending", "repair_resume")}
                for lr in rec.get("level_runs") or []
            ]
        )[:300],
    )
    d.finish(base2, before)


# a stand-in for `claude -p`: logs its argv, answers the first call at once (a node that
# passes), holds the second until $FAKE_DIR/release exists (the turn a restart cuts off), and
# answers a --resume call at once; every call writes the files the seed's gate looks for
FAKE_CLAUDE = """#!{python}
import json, os, sys, time
if "--version" in sys.argv:
    print("2.0.0")
    sys.exit(0)
d = os.environ["FAKE_DIR"]
calls = os.path.join(d, "calls.jsonl")
n = len(open(calls).read().splitlines()) + 1 if os.path.exists(calls) else 1
with open(calls, "a") as f:
    f.write(json.dumps({{"n": n, "argv": sys.argv, "cwd": os.getcwd()}}) + "\\n")
sid = "sess-fake-1"
print(json.dumps({{"type": "system", "subtype": "init", "session_id": sid}}), flush=True)
usage = {{"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 0,
         "cache_creation_input_tokens": 0}}
print(json.dumps({{"type": "assistant", "message": {{"id": "m%d" % n, "content": [
    {{"type": "text", "text": "working"}}], "usage": usage}}}}), flush=True)
if n == 2 and "--resume" not in sys.argv:
    while not os.path.exists(os.path.join(d, "release")):
        time.sleep(0.1)
for name in ("writing.txt", "reading.txt"):
    open(name, "w").write("x\\n")
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
                  "result": "done", "session_id": sid, "num_turns": 1,
                  "total_cost_usd": 0.001, "usage": usage}}), flush=True)
"""


def drill_f(d: Drill) -> None:
    """A fake `claude` killed mid-turn (the second node's): the restarted Studio resumes
    that very session (``--resume``) with a continuation note, and runs nothing twice."""
    import stat

    bindir = d.dir / "bin"
    bindir.mkdir()
    exe = bindir / "claude"
    exe.write_text(FAKE_CLAUDE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    fake = d.dir / "fake"
    fake.mkdir()
    d.env_extra = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "FAKE_DIR": str(fake)}
    d.make_project(toml=REAL_TOML)
    base = d.serve()
    d.start_plan(base, {"areas": ["writing-notes", "reading-notes"]}, max_parallel=1)

    def calls() -> list[dict[str, Any]]:
        f = fake / "calls.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def rec_session() -> Any:
        n = {x["id"]: x for x in d.plan(base)["nodes"]}.get("reading")
        if not n or not n.get("build_id"):
            return None
        return (http("GET", base + f"/api/rebuild/{n['build_id']}")[1] or {}).get("session_id")

    ok = wait_for(lambda: len(calls()) >= 2 and bool(rec_session()), 120)
    d.check(
        "the second node's turn was held mid-way and its session id already recorded",
        ok,
        f"calls={len(calls())} session={rec_session()}",
    )
    orphan = d.agent_pid(base, "reading")
    nodes = {x["id"]: x["status"] for x in d.plan(base)["nodes"]}
    d.check("the first node had passed", nodes.get("writing") == "passed", str(nodes))
    d.kill_server()
    base2 = d.serve()
    rec = d.common_after_restart(base2, orphan)
    code, out = http("POST", base2 + "/api/plan/p1/run", {})
    d.check("POST run accepted", code == 200, f"{code} {out}")
    rec = d.wait_done(base2, 120)
    d.check(
        "plan ends passed", rec["status"] == "passed", f"status={rec['status']} {rec.get('error')}"
    )
    cs = calls()
    d.facts["claude_calls"] = [
        {
            "n": c["n"],
            "resume": c["argv"][c["argv"].index("--resume") + 1]
            if "--resume" in c["argv"]
            else None,
            "prompt": c["argv"][-1][:60],
        }
        for c in cs
    ]
    d.check(
        "exactly three agent calls: writing, reading (killed), reading resumed",
        len(cs) == 3,
        f"{len(cs)} calls",
    )
    if len(cs) == 3:
        d.check(
            "the third call resumed the killed turn's session",
            "--resume" in cs[2]["argv"]
            and cs[2]["argv"][cs[2]["argv"].index("--resume") + 1] == "sess-fake-1",
            str(cs[2]["argv"][:4]),
        )
        d.check(
            "and told the agent it was interrupted",
            "Studio was restarted" in cs[2]["argv"][-1],
            cs[2]["argv"][-1][:80],
        )
        d.check(
            "the first node was never called again",
            "--resume" not in cs[0]["argv"] and cs[1]["cwd"] == cs[2]["cwd"] != cs[0]["cwd"],
        )
    r = {x["id"]: x for x in rec["nodes"]}.get("reading", {})
    b = http("GET", base2 + f"/api/rebuild/{r.get('build_id')}")[1] or {}
    tl = [e["event"] for e in b.get("timeline") or []]
    d.check(
        "the timeline says the session was resumed",
        any("session resumed" in e for e in tl),
        str(tl)[:200],
    )
    d.check("the owner's branch never moved", git(d.project, "rev-parse", "main") == d.main_before)


REAL_TOML = """\
[llm]
backend = "stub"
model = "stub"

[rebuild]
agent = "claude"
gates = ["./gate.sh"]
max_rounds = 3
protected_branches = ["release"]
planner = "stub"
models = {fast = "haiku", mid = "haiku", frontier = "haiku"}
node_timeout = 600
scoped_gate_args = ["--layers", "CD", "--x-repeats", "1", "--await-cap", "5"]
level_gate = "regression"
"""


# a gate a real agent can honestly satisfy: the handlers exist in notes.py (the seed's gate.sh
# wants marker files only the stub agent writes, which a real agent rightly refuses to fake)
REAL_GATE = """\
#!/bin/sh
only=""
while [ $# -gt 0 ]; do
  case "$1" in
    --only-file) only="$2"; shift 2 ;;
    --layers|--x-repeats|--await-cap) shift 2 ;;
    *) shift ;;
  esac
done
selected() { [ -z "$only" ] && return 0; grep -q "\"scenario:$1\"" "$only"; }
n=0
out=""
add() { n=$((n + 1)); out="$out$1
"; }
if selected create_a_note; then
  grep -q "def create_note" notes.py 2>/dev/null ||
    add "FAIL no create_note (scenario:create_a_note)"
fi
if selected read_own_and_other; then
  grep -q "def read_note" notes.py 2>/dev/null ||
    add "FAIL no read_note (scenario:read_own_and_other)"
fi
if [ "$n" = 0 ]; then echo "PASS: 0 findings (G 0)"; exit 0; fi
echo "FAIL: $n findings (G $n)"
printf '%s' "$out"
exit 1
"""


def drill_r(d: Drill) -> None:
    """A REAL `claude -p` agent (haiku, a few cents at list price) killed mid-round: the
    restarted Studio continues the node's own session (``--resume``) and does not run the
    node that had passed again. Not part of the default run: it spends real tokens."""
    d.make_project(toml=REAL_TOML, gate=REAL_GATE)
    base = d.serve()
    d.start_plan(base, {"areas": ["writing-notes", "reading-notes"]}, max_parallel=1)

    def node(rec: dict[str, Any], i: str) -> dict[str, Any]:
        return {n["id"]: n for n in rec["nodes"]}[i]

    def session_of(i: str) -> str | None:
        n = node(d.plan(base), i)
        if not n.get("build_id"):
            return None
        return (http("GET", base + f"/api/rebuild/{n['build_id']}")[1] or {}).get("session_id")

    def ready() -> bool:
        rec = d.plan(base)
        if rec["status"] not in ("running",):
            raise RuntimeError(
                f"plan ended {rec['status']} before the kill window: {rec.get('error')}"
            )
        return (
            node(rec, "writing")["status"] == "passed"
            and node(rec, "reading")["status"] == "running"
            and d.agent_pid(base, "reading") is not None
            and bool(session_of("reading"))
        )

    ok = wait_for(ready, 420, 0.25)
    d.check(
        "a real agent was in the middle of reading's round",
        ok,
        "waiting for writing passed + reading agent",
    )
    if not ok:
        return
    orphan = d.agent_pid(base, "reading")
    rec = d.plan(base)
    w = node(rec, "writing")
    before = {
        "writing_build": w["build_id"],
        "writing_rounds": w.get("rounds"),
        "writing_tokens_out": (w.get("usage") or {}).get("tokens_out"),
        "reading_session": session_of("reading"),
        "reading_status": node(rec, "reading")["status"],
        "totals_tokens_out": (rec.get("totals") or {}).get("tokens_out"),
    }
    d.facts["before_kill"] = before
    d.kill_server()
    d.check("the agent outlived its server", orphan is not None and alive(orphan), f"pid {orphan}")
    base2 = d.serve()
    rec = d.common_after_restart(base2, orphan)
    by = {n["id"]: n for n in rec["nodes"]}
    d.check(
        "reading is interrupted, writing kept",
        by["reading"]["status"] == "interrupted" and by["writing"]["status"] == "passed",
        f"reading={by['reading']['status']} writing={by['writing']['status']}",
    )
    b = http("GET", base2 + f"/api/rebuild/{by['reading']['build_id']}")[1] or {}
    d.check(
        "the agent's session id was kept in the record",
        b.get("session_id") == before["reading_session"],
        f"{before['reading_session']}",
    )
    code, out = http("POST", base2 + "/api/plan/p1/run", {})
    d.check("POST run accepted", code == 200, f"{code} {out}")
    rec = d.wait_done(base2, 600)
    d.check(
        "plan ends passed",
        rec["status"] == "passed",
        f"status={rec['status']} error={rec.get('error')}",
    )
    by = {n["id"]: n for n in rec["nodes"]}
    w2 = by["writing"]
    d.check(
        "the passed node was not run again",
        w2["build_id"] == before["writing_build"]
        and w2.get("rounds") == before["writing_rounds"]
        and (w2.get("usage") or {}).get("tokens_out") == before["writing_tokens_out"],
        f"build {w2['build_id']} rounds {w2.get('rounds')} "
        f"tokens_out {(w2.get('usage') or {}).get('tokens_out')}",
    )
    r2 = by["reading"]
    d.check(
        "the interrupted node was continued (attempts 2)",
        r2.get("attempts") == 2,
        f"attempts={r2.get('attempts')}",
    )
    b2 = http("GET", base2 + f"/api/rebuild/{r2['build_id']}")[1] or {}
    resumed = [e["event"] for e in b2.get("timeline") or [] if "resum" in e["event"].lower()]
    log_lines = [x for x in d.srv_log.read_text().splitlines() if "resum" in x.lower()]
    d.check(
        "the session was resumed, not started fresh",
        bool(resumed or log_lines),
        (resumed or log_lines or ["no 'resume' line"])[0][:160],
    )
    node_out = sum(int((n.get("usage") or {}).get("tokens_out") or 0) for n in rec["nodes"])
    t = rec.get("totals") or {}
    d.check(
        "plan totals equal the sum of its nodes across the restart",
        int(t.get("tokens_out") or 0) == node_out,
        f"totals {t.get('tokens_out')} vs nodes {node_out}",
    )
    try:
        landed = "def read_note" in git(d.project, "show", "build/notes:notes.py")
    except RuntimeError:
        landed = False
    d.check("reading's handler is on the target branch (build/notes)", landed)
    d.check("the owner's branch never moved", git(d.project, "rev-parse", "main") == d.main_before)
    d.facts["after"] = {
        "reading_session": b2.get("session_id"),
        "reading_rounds": r2.get("rounds"),
        "tokens_out": t.get("tokens_out"),
        "cost_usd_list_estimate": t.get("cost_usd_list_estimate"),
        "wall_s": t.get("wall_s"),
        "resume_evidence": (resumed or log_lines)[:3],
    }


DRILLS = {
    "A": ("killed mid agent round", drill_a),
    "B": ("killed mid level gate", drill_b),
    "C": ("killed mid repair round", drill_c),
    "F": ("fake claude killed mid turn, session resumed", drill_f),
    "R": ("REAL agent killed mid round (spends tokens; not in the default run)", drill_r),
}
DEFAULT = ["A", "B", "C", "F"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("drills", nargs="*", choices=list(DRILLS), default=DEFAULT)
    ap.add_argument("--keep", action="store_true", help="keep the temp directory")
    args = ap.parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix="lattice-drill-"))
    report: dict[str, Any] = {}
    bad = 0
    try:
        for key in args.drills or DEFAULT:
            title, fn = DRILLS[key]
            d = Drill(key, tmp)
            t0 = time.time()
            try:
                fn(d)
            except Exception as exc:  # noqa: BLE001 -- a drill that cannot run is a failure
                d.check("the drill ran to the end", False, f"{type(exc).__name__}: {exc}")
            finally:
                d.cleanup()
            ok = all(c[1] for c in d.checks)
            bad += not ok
            report[key] = {
                "title": title,
                "ok": ok,
                "seconds": round(time.time() - t0, 1),
                "checks": [{"check": c, "ok": o, "evidence": e} for c, o, e in d.checks],
                "facts": d.facts,
            }
            verdict = "PASS" if ok else "FAIL"
            print(f"\n== Drill {key}: {title}: {verdict} ({report[key]['seconds']} s)")
            for c, o, e in d.checks:
                print(f"  [{'ok' if o else 'FAIL'}] {c}" + (f"  ({e})" if e else ""))
            for k, v in d.facts.items():
                print(f"      {k}: {json.dumps(v)}")
        out = tmp / "report.json"
        out.write_text(json.dumps(report, indent=1))
        print(f"\nreport: {out}")
    finally:
        if not args.keep and bad == 0:
            shutil.rmtree(tmp, ignore_errors=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
