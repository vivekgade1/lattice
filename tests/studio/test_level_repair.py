"""The level repair loop (API.md v1.11, lattice/studio/plan.py): a level gate that fails on
the merged tip -- a regression of a check that passed before the level (cross-area), or a
node's own target that passed in its scoped node gate but fails with the full layers
(race-only) -- no longer stops the plan: each failing check is attributed (scope, files, or
a dedicated repair node), the responsible nodes continue their sessions with the level
gate's findings, each verified by its own gate, the level merges again and the gate runs
again, at most ``[rebuild] level_repair_attempts`` times; still failing, the plan ends
``partial`` with ``resume.kind`` ``repair``, and ``POST .../run`` does another bounded
pass. The seed project (tests/studio/seed.py), the stub planner and stub agent; no LLM."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lattice.studio.plan import attribute_failures
from tests.studio.seed import (
    N,
    R,
    W,
    cleanup_worktrees,
    finish,
    git,
    integ,
    manager,
    node,
    run_plan,
    seed_project,
)

CLASH = ["reading-notes", "writing-notes"]  # the seed's stub plan: w-clash and r-clash


@pytest.fixture()
def seed(tmp_path: Path):
    roots: list[Path] = []

    def make(**kw: Any) -> Path:
        root = seed_project(tmp_path, **kw)
        roots.append(root)
        return root

    yield make
    for r in roots:
        cleanup_worktrees(r)


def test_attribution_by_scope_then_files_then_a_repair_node() -> None:
    merged = [{"id": "a", "kind": "area"}, {"id": "b", "kind": "area"},
              {"id": "integration", "kind": "integration"}]  # fmt: skip
    by, unowned, how = attribute_failures(
        [W, N, "file:internal/x/y.go", "title:boom"],
        merged,
        {"a": [W], "b": [R, N], "integration": [W, R, N]},
        {"a": ["internal/x"], "b": ["web/b.go"]},
        {"file:internal/x/y.go": ["internal/x/y.go"]},
    )
    assert by == {"a": [W, "file:internal/x/y.go"], "b": [N]}
    assert unowned == ["title:boom"]
    assert how[W] == {"by": "scope", "nodes": ["a"]}
    assert how["file:internal/x/y.go"] == {"by": "files", "nodes": ["a"]}
    assert how["title:boom"] == {"by": "repair_node", "nodes": []}


def test_a_race_only_and_a_cross_area_failure_are_repaired_and_land(seed) -> None:
    root = seed()
    pm = manager(root)
    before = git(root, "rev-parse", "main")
    rec = run_plan(pm, {"areas": CLASH})
    assert rec["status"] == "passed", rec
    lr = rec["level_runs"][0]
    assert lr["status"] == "passed" and lr["landed"] and lr["merged"] == ["w-clash", "r-clash"]
    (rep,) = lr["repairs"]
    assert rep["attempt"] == 1 and rep["of"] == 2 and rep["outcome"] == "passed"
    assert rep["targets"] == [W, N]
    assert rep["attributed"] == {W: {"by": "scope", "nodes": ["w-clash"]},
                                 N: {"by": "scope", "nodes": ["r-clash"]}}  # fmt: skip
    assert [(x["id"], x["verify"], x["rounds"]) for x in rep["nodes"]] == [
        ("w-clash", "pass", 1),
        ("r-clash", "pass", 1),
    ]
    # the first level gate: the clash is a regression, the race an in-scope failure the
    # scoped node gate (--layers CD) could not see
    first_tip = rep["tip"]
    first = pm.rb.tip_result(first_tip)
    assert first["kind"] == "regression" and sorted(first["failing"]) == [W, N]
    assert lr["gate"]["failing"] == [] and lr["gate"]["mode"] == "regression"
    by = {n["id"]: n for n in rec["nodes"]}
    for nid in ("w-clash", "r-clash"):
        n = by[nid]
        assert n["status"] == "passed" and n["merged"] and n["repairs"] == 1
        assert n["rounds"] == 2
        b = pm.rb.get(n["build_id"])
        assert b["repairs"] == 1 and b["rounds"][-1]["check"]["verdict"] == "pass"
        assert any(e.get("repair") for e in b["timeline"])
        assert any(e.get("repair") for e in n["timeline"])
    assert any(e.get("repair") for e in rec["timeline"])
    assert rec["repair_attempts"] == 1
    # the target holds the repaired code; the owner's branch never moved
    tip = rec["target_tip"]
    assert git(root, "rev-parse", "build/notes") == tip == lr["tip"] != first_tip
    assert git(root, "show", f"{tip}:race.txt") == "calm"
    assert "right.txt" not in git(root, "ls-tree", "--name-only", tip)
    assert git(root, "rev-parse", "main") == before
    # the repair round's prompt carried the level gate's findings
    run = pm.rb.node_run(by["r-clash"]["build_id"])
    assert "fails these checks that passed in isolation" in run.repair_brief
    assert "scenario:no_token" in run.repair_brief and "w-clash" in run.repair_brief


NEVER = {"w-clash": {"rounds": [
    {"files": {"writing.txt": "w\n", "left.txt": "l\n"}}, {"files": {"note.txt": "1"}},
    {"files": {"note.txt": "2"}}, {"files": {"note.txt": "3"}}, {"files": {"note.txt": "4"}}]},
    "r-clash": {"rounds": [
    {"files": {"reading.txt": "r\n", "right.txt": "r\n"}}, {"files": {"other.txt": "1"}},
    {"files": {"other.txt": "2"}}, {"files": {"right.txt": None}}]}}  # fmt: skip


def test_repairs_are_capped_then_partial_and_resume_repairs_again(seed) -> None:
    root = seed(stub=NEVER)
    pm = manager(root, level_repair_attempts=2)
    rec = run_plan(pm, {"areas": CLASH})
    assert rec["status"] == "partial", rec
    lr = rec["level_runs"][0]
    assert [r["attempt"] for r in lr["repairs"]] == [1, 2]  # never more than the cap
    assert [r["outcome"] for r in lr["repairs"]] == ["failed", "failed"]
    assert lr["repair_pending"] and lr["status"] == "failed" and not lr.get("landed")
    assert rec["error"]["kind"] == "repair_exhausted" and rec["error"]["nodes"] == [
        "w-clash",
        "r-clash",
    ]
    assert rec["error"]["targets"] == [N]
    by = {n["id"]: n for n in rec["nodes"]}
    assert by["r-clash"]["status"] == "regressed" and by["r-clash"]["repairs"] == 2
    assert by["integration"]["status"] == "pending"
    assert git(root, "rev-parse", "build/notes") == git(root, "rev-parse", "main")
    res = rec["resume"]
    assert res["available"] and res["kind"] == "repair" and res["from_level"] == 0
    assert "still fails after its repairs" in res["note"]
    # the repair usage and rounds count for the node and the plan
    assert by["r-clash"]["rounds"] == 3 and by["w-clash"]["rounds"] == 1
    assert rec["totals"]["rounds"] == 4 and by["w-clash"].get("repairs") is None
    # resume: another bounded repair pass from the merged nodes, no node starts over
    pm.run(rec["id"])
    rec = finish(pm, rec["id"])
    assert rec["status"] == "passed", rec
    by = {n["id"]: n for n in rec["nodes"]}
    assert len(by["r-clash"]["builds"]) == 1 and len(by["w-clash"]["builds"]) == 1
    assert by["r-clash"]["repairs"] == 3 and rec["repair_attempts"] == 3
    again = rec["level_runs"][1]
    assert again["repair_resume"] and again["repair_pass"] == 2 and again["landed"]
    assert rec["level_runs"][0]["superseded"]
    assert "right.txt" not in git(root, "ls-tree", "--name-only", "build/notes")


def test_one_attempt_cap_is_exactly_one(seed) -> None:
    root = seed(stub=NEVER)
    pm = manager(root, level_repair_attempts=1)
    rec = run_plan(pm, {"areas": CLASH})
    assert rec["status"] == "partial"
    assert len(rec["level_runs"][0]["repairs"]) == 1 and rec["repair_attempts"] == 1


GENERIC_GATE = """#!/bin/sh
only=""
while [ $# -gt 0 ]; do case "$1" in --only-file) only="$2"; shift 2;; *) shift;; esac; done
n=0
[ -f writing.txt ] || { n=$((n+1)); m1="FAIL writing.txt is missing (scenario:create_a_note)"; }
r="(scenario:read_own_and_other)"
[ -f reading.txt ] || { n=$((n+1)); m2="FAIL reading.txt is missing $r"; }
if [ -f a.txt ] && [ -f b.txt ]; then n=$((n+1)); m3="FAIL the merged build does not start"; fi
if [ "$n" = 0 ]; then echo "PASS: 0 findings (G 0)"; exit 0; fi
echo "FAIL: $n findings (G $n)"
for m in "$m1" "$m2" "$m3"; do [ -n "$m" ] && echo "$m"; done
exit 1
"""


def test_a_failure_no_node_owns_gets_a_repair_node(seed) -> None:
    root = seed(
        gate=GENERIC_GATE,
        plans={"areas-writing-notes-reading-notes": {"nodes": [
            node("wa", {"area": "writing-notes"}, files=["a.txt"]),
            node("rb", {"area": "reading-notes"}, files=["b.txt"]),
            integ("wa", "rb")]}},
        stub={"wa": {"rounds": [{"files": {"writing.txt": "w", "a.txt": "a"}}]},
              "rb": {"rounds": [{"files": {"reading.txt": "r", "b.txt": "b"}}]},
              "repair-l0": {"rounds": [{"files": {"b.txt": None}}]}},
    )  # fmt: skip
    pm = manager(root, scoped_gate_args=None, level_gate="full")
    rec = run_plan(pm, {"areas": ["writing-notes", "reading-notes"]})
    assert rec["status"] == "passed", rec
    lr = rec["level_runs"][0]
    (rep,) = lr["repairs"]
    target = "title:FAIL the merged build does not start"
    assert rep["attributed"] == {target: {"by": "repair_node", "nodes": []}}
    by = {n["id"]: n for n in rec["nodes"]}
    rn = by["repair-l0"]
    assert rn["kind"] == "repair" and rn["repair"] and rn["status"] == "passed" and rn["merged"]
    assert rn["level"] == 0 and "repair-l0" in rec["levels"][0]
    assert lr["merged"] == ["wa", "rb", "repair-l0"]
    # the repair node started from the failing merged tip
    assert pm.rb.get(rn["build_id"])["base"] == rep["tip"]
    assert "b.txt" not in git(root, "ls-tree", "--name-only", "build/notes")


def test_without_the_repair_loop_a_level_failure_stops_the_plan(seed) -> None:
    root = seed()
    pm = manager(root, level_repair_attempts=0)
    rec = run_plan(pm, {"areas": CLASH})
    assert rec["status"] == "failed"
    lr = rec["level_runs"][0]
    assert lr["status"] == "failed" and "repairs" not in lr
    assert lr["gate"]["regressions"] == [N] and lr["gate"]["in_scope_failing"] == [W]
    assert rec["resume"]["kind"] == "retry"


def test_the_mock_serves_repairs_and_plan_defaults() -> None:
    import threading

    from lattice.studio.mock_server import make_server
    from tests.studio.test_server import request

    srv = make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    second = base + "/p/lattice-studio"
    try:
        projects = request("GET", base + "/api/projects")[1]
        assert projects[0]["plan_defaults"]["level_gate"] == "regression"
        assert projects[0]["plan_defaults"]["scoped_gate"][:2] == ["--layers", "CD"]
        p3 = request("GET", second + "/api/plan/p3")[1]
        assert p3["status"] == "partial" and p3["error"]["kind"] == "repair_exhausted"
        assert p3["resume"]["kind"] == "repair" and p3["resume"]["available"]
        lr = next(x for x in p3["level_runs"] if x.get("repair_pending"))
        assert [r["outcome"] for r in lr["repairs"]] == ["failed", "failed"]
        assert any(e.get("repair") for e in p3["timeline"])
        assert request("POST", second + "/api/plan/p3/run", {})[1]["status"] == "running"
        for _ in range(4 * len(p3["levels"]) + 3):
            p3 = request("GET", second + "/api/plan/p3")[1]
        assert p3["status"] == "passed", p3
        again = next(x for x in p3["level_runs"] if x.get("repair_resume"))
        assert again["landed"] and again["repairs"][0]["outcome"] == "passed"
        assert p3["repair_attempts"] == 3 and p3["gate_config"]["level_gate"] == "regression"
        # a fresh mock run repairs level 1 once, over one poll, then lands
        assert request("POST", base + "/api/plan", {"scope": "all"})[1]["id"] == "p1"
        rec = request("GET", base + "/api/plan/p1")[1]
        assert rec["planner_usage"]["calls"] == 1
        request("POST", base + "/api/plan/p1/approve", {})
        request("POST", base + "/api/plan/p1/run", {})
        seen_repairing = False
        for _ in range(3 * len(rec["levels"]) + 3):
            rec = request("GET", base + "/api/plan/p1")[1]
            seen_repairing |= any(n["status"] == "repairing" for n in rec["nodes"])
        assert rec["status"] == "passed" and seen_repairing
        assert rec["level_runs"][1]["repairs"][0]["outcome"] == "passed"
    finally:
        srv.shutdown()
        srv.server_close()
