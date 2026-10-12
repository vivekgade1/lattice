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

    # -- project
    def make_project(
        self,
        scopes: dict[str, Any] | None = None,
        rounds: dict[str, Any] | None = None,
        gate_hold: bool = False,
    ) -> None:
        shutil.copytree(SEED, self.project)
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

    def start_plan(self, base: str, scope: Any) -> None:
        assert http("POST", base + "/api/plan", {"scope": scope})[1]["id"] == "p1"
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


DRILLS = {
    "A": ("killed mid agent round", drill_a),
    "B": ("killed mid level gate", drill_b),
    "C": ("killed mid repair round", drill_c),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("drills", nargs="*", choices=[*DRILLS, []], default=list(DRILLS))
    ap.add_argument("--keep", action="store_true", help="keep the temp directory")
    args = ap.parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix="lattice-drill-"))
    report: dict[str, Any] = {}
    bad = 0
    try:
        for key in args.drills or list(DRILLS):
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
