"""Scoped gates and per-tip results (API.md v1.11, lattice/studio/rebuild.py + plan.py):
a plan node's gate runs ``<gate> <scoped_gate_args> --only-file <selection>`` where the
selection is its scope's scenarios plus the regression set (the scenarios passing at its
level's start); the level ends with the regression gate (or the full one); the last level
and the integration node run the unscoped full gate; every gate's verdicts are recorded per
tip and never recomputed; the planner learns what is built and Studio drops nodes with
nothing failing; ``plan_defaults`` and ``planner_usage``. The seed project
(tests/studio/seed.py), the stub planner and stub agent; no LLM."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice.studio.hub import StudioHub, discover
from lattice.studio.plan import planner_prompt
from lattice.studio.rebuild import (
    allowed_tools,
    build_selection,
    passing_scenarios,
    result_doc,
    scoped_command,
)
from tests.studio.seed import (
    N,
    R,
    T,
    W,
    cleanup_worktrees,
    git,
    integ,
    manager,
    node,
    run_plan,
    seed_project,
)

ARGS = ["--layers", "CD", "--x-repeats", "1", "--await-cap", "5"]


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


# ---------------------------------------------------------------- pure functions


def test_the_selection_is_the_scope_plus_the_regression_set() -> None:
    sel = build_selection([W, T, "invariant:x"], [W], [T, N])
    assert sel == {"scenarios": [W, T, N], "in_scope": [W, T], "regression_set": [T, N]}
    with pytest.raises(AssertionError, match="missing from the selection"):
        build_selection([T], [W], [N])  # an in-scope failing target not selected: a bug


def test_passing_and_the_regression_result_carry_unselected_failures() -> None:
    assert passing_scenarios([W, "title:x"], [W, T, N, W]) == [T, N]
    doc = result_doc(kind="regression", gates=[], counts={}, findings=[{"title": "t",
                     "target": N}], report=[], trace_json={"links": {"R1": [W], "R2": [T],
                     "R4": [N]}}, gate_cmds=["./gate.sh"], source="s", selected=[T, N],
                     last_failing=[W, R], prev_passing=[T, N])  # fmt: skip
    # create_a_note and read_own_and_other were failing and not selected: carried
    assert sorted(doc["failing"]) == sorted([W, N, R])
    assert doc["carried"] == sorted([W, R]) and doc["passing"] == [T]
    full = result_doc(kind="full", gates=[], counts={}, findings=[], report=[R],
                      trace_json={"links": {"R1": [W]}}, gate_cmds=[], source="s")  # fmt: skip
    assert full["failing"] == [] and full["passing"] == [W, R] and full["carried"] == []


def test_the_scoped_command_and_allowlist() -> None:
    cmd = scoped_command("./check.sh", ARGS, "/w t/.lattice-scope/selection.json")
    assert cmd == ("./check.sh --layers CD --x-repeats 1 --await-cap 5 --only-file "
                   "'/w t/.lattice-scope/selection.json'")  # fmt: skip
    assert f"Bash({cmd}:*)" in allowed_tools([cmd])


# ---------------------------------------------------------------- a scoped plan


def test_a_scoped_plan_passes_with_regression_level_gates(seed) -> None:
    root = seed()
    pm = manager(root)
    assert pm.cfg.scoped_gate_args == ARGS and pm.cfg.level_gate == "regression"
    rec = run_plan(pm, "all")
    assert rec["status"] == "passed", rec
    by = {n["id"]: n for n in rec["nodes"]}
    assert by["tokens"]["nothing_to_build"] and by["integration"]["nothing_to_build"]
    assert rec["gate_config"]["scoped_gate_args"] == ARGS
    # the nodes ran the scoped command on their selection file
    for nid, scope in (("writing", [W, T]), ("reading", [R])):
        b = pm.rb.get(by[nid]["build_id"])
        sg = b["scoped_gate"]
        wt_sel = sg["selection_file"]
        assert sg["command"] == f"./gate.sh {' '.join(ARGS)} --only-file {wt_sel}"
        assert b["gates"] == [sg["command"]] and wt_sel.endswith("/.lattice-scope/selection.json")
        assert sg["in_scope"] == scope and sg["regression_set"] == [T, N]
        assert b["rounds"][0]["gates"][0]["command"] == sg["command"]
    # the selection file never lands in a commit
    assert ".lattice-scope" not in git(root, "ls-tree", "-r", "--name-only", "build/notes")
    assert "/.lattice-scope/" in (root / ".git" / "info" / "exclude").read_text()
    # level gates: regression with full layers, then the final full gate
    lv = rec["level_runs"]
    assert lv[0]["gate"]["mode"] == "regression" and lv[0]["gate"]["selected"] == 2
    assert lv[0]["gate"]["failing"] == [W, R]  # carried: not selected, failing before
    assert lv[1]["gate"]["mode"] == "regression" and lv[1]["gate"]["failing"] == []
    assert rec["final_gate"]["sha"] == rec["target_tip"] and rec["final_gate"]["failing"] == []
    # per-tip results, recorded after every gate
    tip = pm.rb.tip_result(rec["target_tip"])
    assert tip["kind"] == "full" and tip["failing"] == [] and tip["passing"] == [W, T, R, N]
    l0 = pm.rb.tip_result(lv[0]["tip"])
    assert l0["kind"] == "regression" and l0["selected"] == [T, N] and l0["carried"] == [W, R]
    start = pm.rb.tip_result(rec["baseline"]["sha"])
    assert start["kind"] == "full" and start["failing"] == [W, R]
    # areas on the target branch from the tip's results
    assert {a["id"]: (a["failing"], a["status"]) for a in pm.rb.areas(None)} == {
        "writing-notes": (0, "built"),
        "reading-notes": (0, "built"),
        "all": (0, "built"),
    }


def test_the_regression_set_catches_a_regression_in_the_node_gate(seed) -> None:
    root = seed(
        plans={"all": {"nodes": [node("w", {"rules": ["R1", "R2"]}), node("t", {"rules": ["R4"]}),
                                 node("reader", {"rules": ["R3"]}, deps=["w"]),
                                 integ("reader", "t")]}},
        stub={"w": {"rounds": [{"files": {"writing.txt": "w\n"}}]},
              "reader": {"rounds": [{"files": {"reading.txt": "r\n", "writing.txt": None}},
                                    {"files": {"writing.txt": "w\n"}}]}},
    )  # fmt: skip
    pm = manager(root)
    rec = run_plan(pm, "all")
    assert rec["status"] == "passed", rec
    reader = next(n for n in rec["nodes"] if n["id"] == "reader")
    b = pm.rb.get(reader["build_id"])
    assert b["scoped_gate"]["regression_set"] == [W, T, N]
    assert W in b["scoped_gate"]["regression_set"]
    # round 1 deleted writing.txt: the scoped gate saw the regression (it selects W)
    assert len(b["rounds"]) == 2
    assert b["rounds"][0]["regressions"] == [W] and b["rounds"][0]["check"]["verdict"] == "fail"
    assert rec["level_runs"][1].get("repairs") is None


def test_the_integration_node_and_a_full_level_gate_are_unscoped(seed) -> None:
    root = seed()
    pm = manager(root, level_gate="full")
    rb = pm.rb
    rb.plan_active = "p9"
    tip = git(root, "rev-parse", "HEAD")
    baseline = {"findings": [{"title": "FAIL x", "target": W}], "gates": [], "counts": {}}
    scope = {"kind": "all", "id": "integration", "title": "i", "rules": ["R1"], "targets": [W]}
    rid = rb.start_node(plan_id="p9", node_id="integration", scope=scope, tip=tip,
                        target_branch="build/notes", baseline=baseline, model="sonnet",
                        passing=[T, N], node_kind="integration")  # fmt: skip
    b = rb.get(rid, wait=30)
    assert b["gates"] == ["./gate.sh"] and b["scoped_gate"] is None
    rid2 = rb.start_node(plan_id="p9", node_id="writing", scope={**scope, "kind": "rules",
                         "id": "writing"}, tip=tip, target_branch="build/notes",
                         baseline=baseline, model="sonnet", passing=[T, N])  # fmt: skip
    b2 = rb.get(rid2, wait=30)
    assert (
        b2["gates"][0].startswith("./gate.sh --layers CD") and b2["scoped_gate"]["scenarios"] == 3
    )
    rb.plan_active = None
    rec = run_plan(pm, "all")
    assert rec["status"] == "passed"
    assert {lr["gate"]["mode"] for lr in rec["level_runs"] if lr["gate"]} == {"full"}
    assert "final_gate" not in rec  # level_gate full: every level end is the full gate


def test_without_scoped_args_nothing_changes(seed) -> None:
    root = seed()
    pm = manager(root, scoped_gate_args=None, level_gate=None)
    assert pm.cfg.scoped_gate_args is None and pm.cfg.level_gate == "full"
    rec = run_plan(pm, "all")
    assert rec["status"] == "passed"
    by = {n["id"]: n for n in rec["nodes"]}
    b = pm.rb.get(by["writing"]["build_id"])
    assert b["gates"] == ["./gate.sh"] and b["scoped_gate"] is None
    assert all(lr["gate"]["mode"] == "full" for lr in rec["level_runs"] if lr["gate"])


def test_a_gated_tip_is_never_gated_again_and_built_nodes_are_dropped(seed) -> None:
    root = seed()
    pm = manager(root)
    first = run_plan(pm, "all")
    assert first["status"] == "passed"
    pid = pm.create({"scope": "all"})["id"]
    rec = pm.get(pid, wait=30)
    assert rec["status"] == "ready"
    # everything passes at the target's tip: every node dropped at planning, the scaffold too
    assert all(n["status"] == "passed" and n["nothing_to_build"] for n in rec["nodes"]), rec
    assert {n["dropped_at_planning"] for n in rec["nodes"]} == {first["target_tip"]}
    pm.approve(pid)
    pm.run(pid)
    rec = pm.get(pid, wait=30)
    assert rec["status"] == "passed"
    assert rec["baseline"]["reused"] is True and rec["level_runs"] == []


def test_a_scaffold_whose_scope_passes_is_dropped(seed) -> None:
    root = seed(plans={"all": {"nodes": [
        node("scaffold", {"rules": ["R2"]}, kind="area"), node("w", {"rules": ["R1"]}),
        node("r", {"rules": ["R3", "R4"]}), integ("w", "r", "scaffold")]}})  # fmt: skip
    pm = manager(root)
    rec = run_plan(pm, "all")
    by = {n["id"]: n for n in rec["nodes"]}
    assert rec["status"] == "passed" and by["scaffold"]["nothing_to_build"]


def test_the_planner_message_says_what_is_built() -> None:
    scope = {"kind": "all", "areas": ["a", "b"], "rules": ["R1", "R2"], "title": "All"}
    areas = [{"id": "a", "title": "A", "rules": ["R1"], "targets": [W]},
             {"id": "b", "title": "B", "rules": ["R2"], "targets": [T]}]  # fmt: skip
    text = planner_prompt(project="p", scope=scope, areas=areas, trace={"rules": []},
                          data_model=None, file_tree=[], max_parallel=2,
                          built={"a": True, "b": False})  # fmt: skip
    info = json.loads(text.split("## Areas of the scope (JSON)\n\n")[1].split("\n\n## Data")[0])
    assert [(a["id"], a["built"]) for a in info] == [("a", True), ("b", False)]
    assert "`built: true`" in text


def test_plan_defaults_on_get_projects(seed) -> None:
    root = seed()
    hub = StudioHub(discover([root]), lambda r, name: None)
    item = hub.summary("notes")
    assert item["plan_defaults"] == {
        "max_parallel": 2, "models": {"fast": "haiku", "mid": "sonnet", "frontier": "opus"},
        "scoped_gate": ARGS, "level_gate": "regression", "level_gate_args": None,
        "node_timeout": 1800.0, "level_repair_attempts": 2}  # fmt: skip


FAKE_PLANNER = """#!{python}
import json, sys
print(json.dumps({{"type": "system", "subtype": "init", "session_id": "s"}}), flush=True)
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
      "result": json.dumps({{"nodes": []}}), "session_id": "s", "total_cost_usd": 0.5,
      "duration_ms": 900, "num_turns": 2, "usage": {{"input_tokens": 1000,
      "output_tokens": 200, "cache_read_input_tokens": 3000,
      "cache_creation_input_tokens": 50}}}}), flush=True)
"""


def test_planner_usage_is_in_the_record_and_the_totals(seed, tmp_path: Path) -> None:
    root = seed()
    exe = tmp_path / "claude"
    exe.write_text(FAKE_PLANNER.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    pm = manager(root, planner="agent")
    pm.rb.claude_bin = str(exe)
    pid = pm.create({"scope": "all"})["id"]
    rec = pm.get(pid, wait=30)
    assert rec["status"] == "ready" and rec["source"] == "fallback"  # an empty plan: invalid
    pu = rec["planner_usage"]
    assert pu["calls"] == 2 and pu["tokens_in"] == 2 * 4050 and pu["tokens_out"] == 400
    assert pu["cache_read_input_tokens"] == 6000 and pu["cost_usd_list_estimate"] == 1.0
    assert pu["wall_s"] > 0
    assert rec["totals"]["tokens_in"] == 8100 and rec["totals"]["cost_usd_list_estimate"] == 1.0


def test_level_gate_args_apply_to_the_regression_level_gate_only(seed) -> None:
    """``[rebuild] level_gate_args``: the regression level gate carries them before
    ``--only-file``; the final full gate and the node gates do not (a level end was about
    two minutes of layer X on Streak)."""
    root = seed()
    pm = manager(root, level_gate="regression", level_gate_args=["--x-repeats", "2"])
    rec = run_plan(pm, "all")
    assert rec["status"] == "passed", rec
    first = rec["level_runs"][0]["gate"]
    assert first["mode"] == "regression"
    cmd = first["gates"][0]["command"]
    assert cmd.startswith("./gate.sh --x-repeats 2 --only-file ") and cmd.endswith(
        "/.lattice-scope/level-selection.json"
    )
    # node gates keep their own scoped args; the final gate is the plain full gate
    node_cmds = [pm.rb.get(n["build_id"])["gates"][0] for n in rec["nodes"] if n.get("build_id")]
    assert node_cmds and all("--x-repeats 2" not in c for c in node_cmds)
    assert rec["final_gate"]["gates"][0]["command"] == "./gate.sh"
    assert pm.cfg.plan_defaults()["level_gate_args"] == ["--x-repeats", "2"]


def test_without_level_gate_args_the_level_gate_is_unchanged(seed) -> None:
    root = seed()
    pm = manager(root, level_gate="regression")
    rec = run_plan(pm, "all")
    cmd = rec["level_runs"][0]["gate"]["gates"][0]["command"]
    assert cmd.startswith("./gate.sh --only-file ") and "--x-repeats" not in cmd
    assert pm.cfg.plan_defaults()["level_gate_args"] is None
    assert rec["gate_config"]["level_gate_args"] is None
