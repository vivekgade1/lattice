"""Studio's improvement signals (spike 3, lattice/improve/events.py; API.md v1.15): each
accounting field on a stub plan of the dogfood seed, whose gate writes a
``lattice spec check --json``-shaped report:

(a) ``layer_seconds`` on gate runs and level runs, copied from the report's ``timings``;
(b) a machine ``cause`` on a give-up (the seed's ``w-broken`` never writes writing.txt; its
    check carries the "spec problem" line of ``lattice.spec.preconditions``) and on a stop;
(c) the owner's actions as timeline and log events (``actor: "owner"``);
(d) the ledger line's ``wall_s`` is the node's own wall, not its level's;
(e) a ``signal_key`` on every finding.
Plus ``GET /api/improve`` on the real server and the mock, and the API.md section."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from lattice.improve.events import EventLog, read_events
from lattice.studio.mock_server import make_server as make_mock
from lattice.studio.plan import PlanManager
from lattice.studio.rebuild import Rebuilder, load_table
from lattice.studio.server import StubGenerator, make_server
from tests.studio.seed import cleanup_worktrees, finish, seed_project
from tests.studio.test_server import make_project, request

ROOT = Path(__file__).resolve().parents[2]

# The gate: gate.sh's checks (writing.txt, reading.txt, left/right clash), --layers and
# --only-file like the real check, and with LATTICE_REBUILD set a JSON report like
# `lattice spec check --json` (timings.layers, findings with details.problems, notes with the
# layer X repeats, unchecked). create_a_note's failure carries the real "spec problem" line.
FAKE_CHECK = r"""
import json, os, sys
from pathlib import Path
from lattice.spec import Scenario, Step
from lattice.spec.preconditions import unestablished
args = sys.argv[1:]
layers, only, repeats = "ABCDX", None, 3
i = 0
while i < len(args):
    a = args[i]
    if a == "--layers": layers = args[i + 1]; i += 2; continue
    if a == "--x-repeats": repeats = int(args[i + 1]); i += 2; continue
    if a == "--only-file": only = json.load(open(args[i + 1])); i += 2; continue
    if a == "--await-cap": i += 2; continue
    i += 1
def sel(name):
    return only is None or f"scenario:{name}" in only
ghost = Scenario("create_a_note", steps=(Step("GET", "/notes", expect_db={"notes": {"count": 1}}),))
found = ["db notes: expected 1 rows, found 0"]
spec_line = unestablished(ghost, 1, {"notes": {"count": 1}}, found)
findings = []
if sel("create_a_note") and not Path("writing.txt").exists():
    findings.append({"layer": "D", "verdict": "silent", "rule": "scenario: create_a_note",
                     "case": "create_a_note / step 1", "key": "D:GET /notes:create_a_note / step 1",
                     "details": {"problems": found + spec_line}})
if sel("read_own_and_other") and not Path("reading.txt").exists():
    findings.append({"layer": "D", "verdict": "silent", "rule": "scenario: read_own_and_other",
                     "case": "read_own_and_other / step 1", "key": "D:GET /notes/1:read",
                     "details": {"problems": ["status: expected 200, got 404"]}})
SECONDS = {"A": 0.01, "B": 0.02, "C": 0.11, "D": 0.42, "X": 0.5 * repeats}
timings = {"total_s": 1.0, "layers": {k: SECONDS[k] for k in "ABCDX" if k in layers}}
notes = [f"layer X: 1 race(s) (1 self-races of 2 copies, 0 pair races), each up to {repeats} "
         "time(s), in 0.1s: R1 not refuted"] if "X" in layers else []
report = {"verdict": "fail" if findings else "pass", "layers": layers,
          "counts": {"D": len(findings)}, "findings": findings, "timings": timings,
          "notes": notes, "unchecked": ["R4: a step cannot send request headers"]}
if os.environ.get("LATTICE_REBUILD"):
    Path("metrics").mkdir(exist_ok=True)
    Path("metrics/last-check.json").write_text(json.dumps(report))
print(("FAIL" if findings else "PASS") + f": {len(findings)} findings (D {len(findings)})")
sys.exit(1 if findings else 0)
"""

HOOK = r"""
import json, sys
from pathlib import Path
a = sys.argv[1:]
phase = a[a.index("--phase") + 1]
llm = json.load(open(a[a.index("--llm-json") + 1]))
Path("metrics").mkdir(exist_ok=True)
with open("metrics/phases.jsonl", "a") as f:
    f.write(json.dumps({"phase": phase, **llm}) + "\n")
print("appended", phase)
"""

GATE = f"#!/bin/sh\nexec {sys.executable} - \"$@\" <<'PY'\n{FAKE_CHECK}\nPY\n"
STUB = {
    "w-broken": {
        "rounds": [
            {
                "hold": 1,
                "summary": f"w-broken {k}: no writing.txt",
                "files": {"attempt.txt": f"{k}\n"},
            }
            for k in (1, 2, 3)
        ]
    }
}


def _manager(root: Path, tmp: Path, **table: Any) -> tuple[PlanManager, Path]:
    hook = tmp / "hook.py"
    hook.write_text(HOOK)
    log = tmp / "events.jsonl"
    t = {
        **load_table(root),
        "findings_json": "metrics/last-check.json",
        "metrics_hook": f"{sys.executable} {hook}",
        **table,
    }
    rb = Rebuilder(
        root, t, name="notes", model="sonnet", env=dict(os.environ), events=EventLog(log, "notes")
    )
    return PlanManager(rb), log


@pytest.fixture(scope="module")
def give_up_plan(tmp_path_factory):
    """rules R1, R3, R4: w-broken gives up (3 rounds, the spec-problem line each time), its
    sibling r-ok passes, t-dep is blocked, the integration node runs last."""
    tmp = tmp_path_factory.mktemp("events")
    root = seed_project(tmp, stub=STUB, gate=GATE)
    pm, log = _manager(root, tmp)
    pid = pm.create({"scope": {"rules": ["R1", "R3", "R4"]}})["id"]
    assert pm.get(pid, wait=30)["status"] == "ready"
    pm.approve(pid)
    pm.run(pid)
    rec = finish(pm, pid)
    events = read_events(log).events
    yield {"pm": pm, "rec": rec, "events": events, "root": root}
    cleanup_worktrees(root)


def _node(rec: dict[str, Any], nid: str) -> dict[str, Any]:
    return next(n for n in rec["nodes"] if n["id"] == nid)


def test_a_layer_seconds_copied_from_the_report(give_up_plan) -> None:
    rec, events = give_up_plan["rec"], give_up_plan["events"]
    gates = [e for e in events if e["kind"] == "gate_run"]
    assert gates, "every gate run is logged"
    node_gates = [e for e in gates if e["gate"] == "node"]
    level = [e for e in gates if e["gate"] in ("level", "baseline", "final")]
    # node gates run --layers CD (the seed's scoped_gate_args): C and D only
    assert node_gates and all(e["layer_seconds"] == {"C": 0.11, "D": 0.42} for e in node_gates)
    assert all(e["mode"] == "scoped" and e["selected"] for e in node_gates)
    # the start baseline and level gates run every layer; X with the default 3 repeats
    full = {"A": 0.01, "B": 0.02, "C": 0.11, "D": 0.42, "X": 1.5}
    assert level and all(e["layer_seconds"] == full for e in level)
    assert all(e.get("x_repeats") == 3 for e in level)
    base = next(e for e in gates if e["gate"] == "baseline")
    assert base["sha"] and base["reused"] is False and rec["baseline"]["layer_seconds"] == full
    gated = [lr for lr in rec["level_runs"] if lr.get("gate")]
    assert gated and all(lr["gate"]["layer_seconds"] == full for lr in gated)
    assert all(g["layer_seconds"] == full for lr in gated for g in lr["gate"]["gates"])


def test_a_layer_seconds_absent_without_a_report(tmp_path: Path) -> None:
    root = seed_project(tmp_path)  # the seed's own gate.sh writes no JSON report
    try:
        pm, log = _manager(root, tmp_path, findings_json=None, metrics_hook=None)
        pid = pm.create({"scope": {"rules": ["R3"]}})["id"]
        pm.get(pid, wait=30)
        pm.approve(pid)
        pm.run(pid)
        finish(pm, pid)
        gates = [e for e in read_events(log).events if e["kind"] == "gate_run"]
        assert gates and not any("layer_seconds" in e for e in gates)
    finally:
        cleanup_worktrees(root)


def test_b_cause_on_a_give_up(give_up_plan) -> None:
    rec, events, pm = give_up_plan["rec"], give_up_plan["events"], give_up_plan["pm"]
    wb = _node(rec, "w-broken")
    assert wb["status"] == "gave_up" and wb["cause"] == "spec_unsatisfiable"
    build = pm.rb.get(wb["build_id"])
    assert build["cause"] == "spec_unsatisfiable"
    assert build["timeline"][-1] == {
        **build["timeline"][-1],
        "event": "gave_up",
        "cause": "spec_unsatisfiable",
    }
    end = [e for e in events if e["kind"] == "node_ended" and e.get("node") == "w-broken"]
    assert [(e["status"], e["cause"], e["rounds"]) for e in end] == [
        ("gave_up", "spec_unsatisfiable", 3)
    ]
    assert any(e.get("cause") == "spec_unsatisfiable" for e in wb["timeline"])
    ok = _node(rec, "r-ok")
    assert ok["status"] == "passed" and ok.get("cause") is None
    assert all(
        "cause" not in e for e in events if e["kind"] == "node_ended" and e["status"] == "passed"
    )


def test_b_cause_on_an_owner_stop(tmp_path: Path) -> None:
    root = seed_project(tmp_path)
    try:
        pm, log = _manager(root, tmp_path, findings_json=None, metrics_hook=None)
        pid = pm.create({"scope": {"rules": ["R3"]}})["id"]  # node "slow": holds 30 s
        pm.get(pid, wait=30)
        pm.approve(pid)
        pm.run(pid)
        t0 = time.time()
        while (
            not any(n["status"] == "running" and n.get("build_id") for n in pm.get(pid)["nodes"])
            and time.time() - t0 < 30
        ):
            time.sleep(0.1)
        pm.stop(pid)
        rec = finish(pm, pid)
        slow = next(n for n in rec["nodes"] if n.get("build_id"))
        assert slow["status"] == "stopped" and slow["cause"] == "stopped_by_owner"
        evs = read_events(log).events
        assert any(e["kind"] == "node_ended" and e["cause"] == "stopped_by_owner" for e in evs)
        assert [e["what"] for e in evs if e["kind"] == "human"] == ["approve", "run", "stop"]
        assert any(
            e.get("actor") == "owner" and e["event"].startswith("stopped (was")
            for e in rec["timeline"]
        )
    finally:
        cleanup_worktrees(root)


def test_c_owner_actions_are_timeline_and_log_events(give_up_plan) -> None:
    rec, events = give_up_plan["rec"], give_up_plan["events"]
    humans = [(e["what"], e["actor"], e["plan"]) for e in events if e["kind"] == "human"]
    assert humans == [("approve", "owner", rec["id"]), ("run", "owner", rec["id"])]
    owner = [e["event"] for e in rec["timeline"] if e.get("actor") == "owner"]
    assert owner[0].startswith("approved (version") and owner[1].startswith("run 1 started")
    kinds = [e["kind"] for e in events]
    assert kinds.count("plan_started") == 1 and kinds.count("plan_ended") == 1
    ended = next(e for e in events if e["kind"] == "plan_ended")
    assert ended["status"] == rec["status"]


def test_d_the_ledger_line_has_the_node_s_own_wall(give_up_plan) -> None:
    rec, events, pm = give_up_plan["rec"], give_up_plan["events"], give_up_plan["pm"]
    ok = _node(rec, "r-ok")
    own = pm.rb.get(ok["build_id"])["usage"]["wall_s"]
    assert ok["metrics"]["llm"]["wall_s"] == own, "the ledger line: the build's own wall"
    end = next(e for e in events if e["kind"] == "node_ended" and e.get("node") == "r-ok")
    assert end["wall_s"] == own
    lr = next(lr for lr in rec["level_runs"] if "r-ok" in lr["nodes"])

    def ts(s: str) -> float:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").timestamp()

    level_wall = ts(lr["finished"]) - ts(lr["started"])
    assert own < level_wall - 1, (own, level_wall)  # the sibling w-broken holds 3 x 1 s
    wb = _node(rec, "w-broken")
    assert wb["metrics"]["llm"]["wall_s"] == pm.rb.get(wb["build_id"])["usage"]["wall_s"]


def test_e_every_finding_has_a_signal_key(give_up_plan) -> None:
    rec, events, pm = give_up_plan["rec"], give_up_plan["events"], give_up_plan["pm"]
    build = pm.rb.get(_node(rec, "w-broken")["build_id"])
    findings = [f for r in build["rounds"] for f in r["check"]["findings"]]
    assert findings and all(f.get("signal_key") for f in findings)
    assert {f["signal_key"] for f in findings} == {"D:spec_problem"}
    fc = [e for e in events if e["kind"] == "finding_class"]
    assert {"signal_key": "D:spec_problem", "count": 1}.items() <= next(
        e for e in fc if e.get("node") == "w-broken"
    ).items()
    snap = [e for e in fc if e.get("snapshot")]
    assert snap and snap[0]["signal_key"] == "unchecked:request_header" and snap[0]["count"] == 1
    assert len(snap) == 1, "an unchanged unchecked list is logged once"


def test_the_ranker_reads_the_live_log(give_up_plan) -> None:
    from lattice.improve.analyze import analyze

    rep = analyze(give_up_plan["events"], top=50)
    keys = [p["signal_key"] for p in rep["proposals"]]
    assert "cause:spec_unsatisfiable" in keys and "gate:node:D" in keys
    p = next(p for p in rep["proposals"] if p["signal_key"] == "cause:spec_unsatisfiable")
    assert p["target_paths"] == ["lattice/spec/lint.py", "lattice/spec/preconditions.py"]


# ---------------------------------------------------------------- the server and the mock


def serve(srv: Any) -> str:
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def stop(srv: Any) -> None:
    srv.shutdown()
    srv.server_close()


def test_get_improve_and_change_decisions_on_the_real_server(tmp_path: Path) -> None:
    project = make_project(tmp_path / "proj")
    srv = make_server(project, 0, generator=StubGenerator())
    base = serve(srv)
    try:
        status, out = request("GET", base + "/api/improve")
        assert status == 200 and set(out) == {"proposals", "events", "method", "blind_spots"}
        assert out["proposals"] == [] and out["events"] == 0
        assert "Frequency x cost" in out["method"] and len(out["blind_spots"]) >= 3
        assert request("POST", base + "/api/improve", {})[0] == 404
        status, made = request("POST", base + "/api/changes", {"request": "Notes can be starred."})
        assert status in (200, 201, 202), made
        cid = made["id"]
        request("GET", base + f"/api/changes/{cid}?wait=30")
        assert request("POST", base + f"/api/changes/{cid}/reject", {})[0] == 200
        status, out = request("GET", base + "/api/improve")
        assert out["events"] == 2
        (p,) = out["proposals"]
        assert p["signal_key"] == "change:rejected" and p["score_class"] == "C"
        log = next((project / ".lattice" / "state").glob("*/events.jsonl"))
        evs = read_events(log).events
        assert [(e["kind"], e.get("what") or e.get("decision")) for e in evs] == [
            ("human", "change_reject"),
            ("change", "rejected"),
        ]
        assert evs[0]["actor"] == "owner" and evs[1]["source"] == "studio"
    finally:
        stop(srv)


def test_the_mock_serves_the_same_shape() -> None:
    srv = make_mock(0)
    base = serve(srv)
    try:
        status, out = request("GET", base + "/api/improve")
        assert status == 200 and set(out) == {"proposals", "events", "method", "blind_spots"}
        top = out["proposals"][0]
        assert top["signal_key"] == "gate:level_regression:X" and top["kind"] == "change_set"
        assert top["expected"]["saving_s_per_gate"] == 72.0
        assert any(p["status"] == "needs_owner" for p in out["proposals"])
        assert not any(p["signal_key"] == "cause:spec_unsatisfiable" for p in out["proposals"])
        assert request("POST", base + "/api/improve", {})[0] in (404, 405)
    finally:
        stop(srv)


def test_api_md_documents_v1_15() -> None:
    text = (ROOT / "lattice" / "studio" / "API.md").read_text()
    sec = text[text.index("## v1.15 additions") :]
    for s in (
        "GET /api/improve",
        "events.jsonl",
        "layer_seconds",
        "cause",
        "signal_key",
        "actor",
        "improvements.jsonl",
        "needs_owner",
        "blind_spots",
        "UI",
        "mock",
        "Read-only",
    ):
        assert s in sec, s
    assert json.dumps  # keep the import used when the section grows examples
