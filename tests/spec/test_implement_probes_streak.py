"""Unit tests of the Streak-arm runner of Spike E (``dogfood/implement_probes_streak.py``): the
change-set loader and its manifest check, the SA subset, the SO / SO3 / SL / SG / SA / PAg-MS
scorers on small synthetic reports, the no-merge and file-drift guards on throwaway git
repositories, the claude shims, the scratch ``studio.toml``, the post-checkout hook, the fault
injection, the argument limits and the dry run. No Docker, no model, no Streak checkout."""

# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "dogfood"))
sys.path.insert(0, str(REPO))

import implement_probes_streak as S  # noqa: E402

# ---- builders -----------------------------------------------------------------------------------


def fixture_changeset(root: Path, specs: dict[str, tuple[str, bytes]]) -> Path:
    """A change-set directory: `specs` maps a change id to (kind, ref.diff bytes)."""
    root.mkdir(parents=True, exist_ok=True)
    lines = []
    for cid, (kind, diff) in sorted(specs.items()):
        d = root / cid
        d.mkdir()
        (d / "REQUIREMENTS.md").write_text(f"# {cid}\n")
        (d / "spec.py").write_text("SPEC = None\n")
        (d / "spec.trace.json").write_text("{}\n")
        (d / "ref.diff").write_bytes(diff)
        (d / "meta.json").write_text(
            json.dumps({"id": cid, "kind": kind, "acd_on_S": {"failing": []}, "ref_diff_files": []})
        )
        for f in sorted(d.iterdir()):
            lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {cid}/{f.name}")
    (root / "READY").write_text("")
    (root / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")
    return root


DIFF = b"diff --git a/internal/a.go b/internal/a.go\n"
SET = {
    "c01": ("K1", DIFF),
    "c02": ("K2", DIFF),
    "c03": ("K3", DIFF),
    "c15": ("K15", b""),
    "c16": ("K2", DIFF),
}


def change(root: Path, cid: str = "c01", kind: str = "K1", empty: bool = False) -> S.Change:
    d = root / cid
    d.mkdir(parents=True, exist_ok=True)
    return S.Change(cid, kind, d, {"acd_on_S": {"failing": []}}, d / "ref.diff", empty)


def item(layer: str, rule: str, case: str, endpoint: str = "POST /x") -> dict[str, Any]:
    return {"layer": layer, "rule": rule, "case": case, "endpoint": endpoint}


FAIL_A = item("D", "scenario: a", "a / step 1")
FAIL_B = item("C", "inv", "b / step 2", "GET /y")


def d_report(failing: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"tier1": {"failing": failing if failing is not None else [FAIL_A, FAIL_B]}}


def tier4(**over: Any) -> dict[str, Any]:
    t = {
        "verdict": "pass",
        "rebuild": {"status": "passed", "rounds": 1, "usage": {"cost_usd_list_estimate": 1.5}},
        "before": {"matches_report": True, "failing_count": 2, "flows_run": 59},
        "resolved": [FAIL_A, FAIL_B],
        "still_failing": [],
        "regressions": [],
        "failing": [],
        "flows": [],
        "counts": {"unreported": 0, "regressions": 0},
        "owner": {"untouched": True, "checked": []},
        "implementation": {"branch_spec": "implement/iabc", "branch_build": "rebuild/iabc"},
        "t3_vs_edit": {"status": "run", "files": None, "declarations": None},
    }
    t.update(over)
    return t


def report(t4: dict[str, Any] | None = None, state: str = "run") -> dict[str, Any]:
    return {"tiers": {"4": state}, "tier4": t4 if t4 is not None else tier4()}


def flow_item(
    fid: str, reported: str = "t3_may_fail", backed: bool | None = True
) -> dict[str, Any]:
    return {"kind": "flow_changed", "flow": fid, "reported_as": reported, "spec_backed": backed}


# ---- the change set -----------------------------------------------------------------------------


def test_load_changeset_and_manifest(tmp_path: Path) -> None:
    cs = fixture_changeset(tmp_path / "cs", SET)
    got = S.load_changeset(cs, expected_manifest=None)
    assert [c.id for c in got] == ["c01", "c02", "c03", "c15", "c16"]
    assert [c.empty for c in got] == [False, False, False, True, False]
    assert got[3].kind == "K15"
    # the registered manifest hash is enforced
    with pytest.raises(S.ChangesetError, match="registered"):
        S.load_changeset(cs)
    # a file that differs from the manifest is refused, even with the hash check skipped
    (cs / "c01" / "spec.py").write_text("SPEC = 1\n")
    with pytest.raises(S.ChangesetError, match="differs from the manifest"):
        S.load_changeset(cs, expected_manifest=None)


def test_load_changeset_ready_only_and_missing(tmp_path: Path) -> None:
    cs = fixture_changeset(tmp_path / "cs", SET)
    assert [c.id for c in S.load_changeset(cs, expected_manifest=None, only=["c15", "c02"])] == [
        "c02",
        "c15",
    ]
    with pytest.raises(S.ChangesetError, match="no such change"):
        S.load_changeset(cs, expected_manifest=None, only=["c99"])
    (cs / "READY").unlink()
    with pytest.raises(S.ChangesetError, match="READY"):
        S.load_changeset(cs, expected_manifest=None)


def test_registered_manifest_hash_is_the_briefs() -> None:
    assert S.MANIFEST_SHA256.startswith("52e0e541") and S.MANIFEST_SHA256.endswith("caa3")
    assert len(S.MANIFEST_SHA256) == 64


def test_sa_subset_is_the_first_of_each_kind_by_sorted_id(tmp_path: Path) -> None:
    cs = fixture_changeset(tmp_path / "cs", SET)
    got = S.select_subset(S.load_changeset(cs, expected_manifest=None), ["K1", "K2", "K15", "K9"])
    assert [c.id for c in got] == ["c01", "c02", "c15"]  # c16 is the second K2; K9 has none
    assert S.SA_KINDS == ("K1", "K2", "K5", "K9", "K10", "K15")


# ---- SO -----------------------------------------------------------------------------------------


def test_so_change_pass(tmp_path: Path) -> None:
    r = S.score_so_change(change(tmp_path), d_report(), report(), 0)
    assert r["ok_unreported"] and r["ok_regression"] and r["ok_matches"] and r["ok_resolved"]
    assert r["ok_expected"] and r["resolved_exact"] and r["unreported"] == 0


def test_so_change_resolved_must_equal_the_report_failing_set(tmp_path: Path) -> None:
    r = S.score_so_change(change(tmp_path), d_report(), report(tier4(resolved=[FAIL_A])), 0)
    assert r["ok_resolved"] is False
    extra = tier4(resolved=[FAIL_A, FAIL_B, item("D", "scenario: z", "z / step 1")])
    assert S.score_so_change(change(tmp_path), d_report(), report(extra), 0)["ok_resolved"] is False
    # a still failing check is not exact either
    st = tier4(still_failing=[FAIL_B], resolved=[FAIL_A])
    assert (
        S.score_so_change(change(tmp_path), d_report([FAIL_A]), report(st), 0)["ok_resolved"]
        is False
    )


def test_so_change_unreported_flow_and_regression_fail(tmp_path: Path) -> None:
    t = tier4(flows=[flow_item("f1", "unreported")], verdict="fail")
    r = S.score_so_change(change(tmp_path), d_report(), report(t), 0)
    assert r["unreported"] == 1 and r["ok_unreported"] is False
    t = tier4(regressions=[FAIL_A], verdict="fail")
    r = S.score_so_change(change(tmp_path), d_report(), report(t), 0)
    assert r["regressions"] == 1 and r["ok_regression"] is False
    # counts that disagree with the items are flagged
    t = tier4(flows=[flow_item("f1", "unreported")], counts={"unreported": 0, "regressions": 0})
    assert (
        S.score_so_change(change(tmp_path), d_report(), report(t), 0)["counts_consistent"] is False
    )


def test_so_change_matches_report_must_be_true(tmp_path: Path) -> None:
    for bad in (False, None):
        t = tier4(before={"matches_report": bad})
        assert S.score_so_change(change(tmp_path), d_report(), report(t), 0)["ok_matches"] is False


def test_so_change_error_rows_fail_everything(tmp_path: Path) -> None:
    for out, rc in ((None, 2), (report(), 3)):
        r = S.score_so_change(change(tmp_path), d_report(), out, rc)
        assert r["error"] and not any(
            r[k] for k in ("ok_unreported", "ok_regression", "ok_matches", "ok_resolved")
        )
    assert S.score_so_change(change(tmp_path), None, report(), 0)["error"] == "no D report"


def test_so_control_expects_nothing_to_build(tmp_path: Path) -> None:
    ch = change(tmp_path, "c15", "K15", empty=True)
    ntb = tier4(verdict="nothing_to_build", rebuild=None, resolved=[], counts={})
    r = S.score_so_change(ch, d_report([]), report(ntb), 0)
    assert r["ok_expected"] and r["ok_resolved"] is None and r["ok_matches"]
    built = tier4()  # a control that went on to build
    assert S.score_so_change(ch, d_report([]), report(built), 0)["ok_expected"] is False


def test_so_not_built_build_is_not_resolved_exact(tmp_path: Path) -> None:
    nb = tier4(verdict="not_built", rebuild={"status": "gave_up", "rounds": 3}, resolved=[])
    r = S.score_so_change(change(tmp_path), d_report(), report(nb, "not_run:rebuild_gave_up"), 0)
    assert r["ok_resolved"] is False


def so_rows(tmp_path: Path, n_build: int = 3, flows: dict[str, list[dict[str, Any]]] | None = None):
    rows = []
    for k in range(n_build):
        cid = f"c{k + 1:02d}"
        t = tier4(flows=(flows or {}).get(cid, []))
        rows.append(S.score_so_change(change(tmp_path, cid), d_report(), report(t), 0))
    ctrl = tier4(verdict="nothing_to_build", rebuild=None, resolved=[], counts={})
    rows.append(
        S.score_so_change(change(tmp_path, "c15", "K15", True), d_report([]), report(ctrl), 0)
    )
    return rows


def test_aggregate_so_pass_rule(tmp_path: Path) -> None:
    agg = S.aggregate_so(so_rows(tmp_path))
    assert agg["passed"] is True
    assert agg["gate"] == {
        "unreported_zero": "4/4",
        "regression_zero": "4/4",
        "resolved_exact": "3/3",
        "matches_report": "4/4",
    }
    assert agg["round1_builds"] == "3/3" and agg["controls_nothing_to_build"] == "1/1"


def test_aggregate_so_each_gate_item_fails_alone(tmp_path: Path) -> None:
    ok = so_rows(tmp_path)
    for patch in (
        {"ok_unreported": False},
        {"ok_regression": False},
        {"ok_matches": False},
        {"ok_resolved": False},
    ):
        rows = [dict(r) for r in ok]
        rows[0].update(patch)
        assert S.aggregate_so(rows)["passed"] is False, patch
    assert S.aggregate_so([])["passed"] is False  # nothing scored is not a pass


def test_aggregate_so_predictions(tmp_path: Path) -> None:
    fl = {
        "c01": [flow_item("a"), flow_item("b", backed=False)],
        "c02": [flow_item(f"f{i}") for i in range(6)],
    }
    # the registered shape is c03: 2 and c08: 6
    rows = []
    for cid, items in (("c03", fl["c01"]), ("c08", fl["c02"])):
        rows.append(
            S.score_so_change(change(tmp_path, cid), d_report(), report(tier4(flows=items)), 0)
        )
    agg = S.aggregate_so(rows)
    assert agg["flow_changes"] == {"c03": 2, "c08": 6}
    assert agg["spec_backed"] == "7/8"
    hits = agg["prediction_hits"]
    assert hits["flow_changes_by_change"] and hits["spec_backed_2_to_8_of_8"]
    assert hits["flow_reported_as_all_t3_may_fail"] and not hits["verdict_pass_17_to_19"]


# ---- SO3 ----------------------------------------------------------------------------------------

TRUTH = {
    "edited_files": ["internal/habits/routes.go"],
    "added_files": ["internal/habits/new.go"],
    "removed_files": [],
    "edited_decls": [{"file": "internal/habits/routes.go", "name": "atLimit"}],
}


def t3_tier4(touched: list[str], listed: list[str], decl: Any = None) -> dict[str, Any]:
    hit = set(touched) & set(listed)
    files = {
        "touched": touched,
        "listed": listed,
        "hit": sorted(hit),
        "recall": round(len(hit) / len(touched), 3) if touched else None,
        "precision": round(len(hit) / len(listed), 3) if listed else None,
        "missed": [],
    }
    return tier4(t3_vs_edit={"status": "run", "files": files, "declarations": decl})


def test_relevant_go_matches_the_truth_tool() -> None:
    assert S.relevant_go("internal/habits/routes.go")
    assert S.relevant_go("cmd/server/main.go")
    assert not S.relevant_go("internal/habits/routes_test.go")
    assert not S.relevant_go("go.mod")
    assert not S.relevant_go("harness.py")
    assert not S.relevant_go("pkg/x.go")


def test_so3_file_level_exact(tmp_path: Path) -> None:
    ch = change(tmp_path)
    decl = {
        "touched": ["internal/habits/routes.go:atLimit"],
        "listed": [],
        "recall": 1.0,
        "precision": 0.5,
    }
    t = t3_tier4(
        ["internal/habits/new.go", "internal/habits/routes.go"],
        ["internal/habits/routes.go", "internal/x.go"],
        decl,
    )
    r = S.score_so3_change(ch, report(t), TRUTH)
    assert r["file_exact"] is True and r["declarations"]["equal"] is True
    assert r["hit_edited"] == 1 and r["edited"] == 1


def test_so3_extra_missing_and_other_files(tmp_path: Path) -> None:
    ch = change(tmp_path)
    r = S.score_so3_change(ch, report(t3_tier4(["internal/habits/routes.go"], [])), TRUTH)
    assert r["file_exact"] is False and r["only_in_truth"] == ["internal/habits/new.go"]
    t = t3_tier4(["internal/habits/new.go", "internal/habits/routes.go", "internal/extra.go"], [])
    r = S.score_so3_change(ch, report(t), TRUTH)
    assert r["file_exact"] is False and r["only_in_tier4"] == ["internal/extra.go"]
    # a non-Go file the edit touched (go.mod) is reported, not a mismatch
    t = t3_tier4(["go.mod", "internal/habits/new.go", "internal/habits/routes.go"], [])
    r = S.score_so3_change(ch, report(t), TRUTH)
    assert r["file_exact"] is True and r["other_files_touched"] == ["go.mod"]


def test_so3_control_and_errors(tmp_path: Path) -> None:
    ch = change(tmp_path, "c15", "K15", True)
    ntb = tier4(verdict="nothing_to_build", t3_vs_edit={"status": "not_applicable: no edit"})
    assert S.score_so3_change(ch, report(ntb), None)["file_exact"] is True
    assert S.score_so3_change(change(tmp_path), None, TRUTH)["file_exact"] is False
    assert S.score_so3_change(change(tmp_path), report(), None)["file_exact"] is False
    skipped = tier4(t3_vs_edit={"status": "not_applicable: tier3 not run", "files": None})
    assert S.score_so3_change(change(tmp_path), report(skipped), TRUTH)["file_exact"] is False


def test_aggregate_so3_micro_recall(tmp_path: Path) -> None:
    ch = change(tmp_path)
    t = t3_tier4(
        ["internal/habits/new.go", "internal/habits/routes.go"], ["internal/habits/routes.go"]
    )
    rows = [S.score_so3_change(ch, report(t), TRUTH)]
    agg = S.aggregate_so3(rows)
    assert agg["passed"] and agg["file_exact"] == "1/1"
    assert agg["micro_file_recall"] == 1.0 and agg["micro_file_recall_matches_D"] is False
    assert S.aggregate_so3([])["passed"] is False


def test_so3_declarations_against_both_truths(tmp_path: Path) -> None:
    ch = change(tmp_path)
    decl = {
        "touched": ["internal/habits/routes.go:atLimit"],
        "listed": [],
        "recall": 1.0,
        "precision": 0.5,
    }
    t = t3_tier4(["internal/habits/new.go", "internal/habits/routes.go"], [], decl)
    d_truth = dict(TRUTH, edited_decls=[{"file": "internal/habits/routes.go", "name": "Register"}])
    u0 = dict(TRUTH)  # atLimit only
    r = S.score_so3_change(ch, report(t), d_truth, u0)
    assert r["file_exact"] is True  # the gate is the files, never the declarations
    assert r["declarations"]["equal"] is False
    assert r["declarations"]["only_in_truth"] == ["internal/habits/routes.go:Register"]
    assert r["declarations_u0"]["equal"] is True
    agg = S.aggregate_so3([r])
    assert (
        agg["passed"]
        and agg["declarations_equal"] == "0/1"
        and agg["declarations_equal_u0"] == "1/1"
    )


def test_so_rep_ignores_the_clone_bound_tree_shas() -> None:
    a = tier4(
        implementation={
            "branch_spec": "implement/i",
            "spec_tree": "aaa",
            "build_tree": "bbb",
            "diff_sha256": "d",
        }
    )
    b = tier4(
        implementation={
            "branch_spec": "implement/i",
            "spec_tree": "ccc",
            "build_tree": "ddd",
            "diff_sha256": "d",
        }
    )
    assert S.canon_rep(a) == S.canon_rep(b) and S.rep_diff_keys(a, b) == []
    c = tier4(
        implementation={
            "branch_spec": "implement/i",
            "spec_tree": "ccc",
            "build_tree": "ddd",
            "diff_sha256": "other",
        }
    )
    assert S.canon_rep(a) != S.canon_rep(c) and S.rep_diff_keys(a, c) == ["implementation"]
    d = tier4(verdict="fail")
    assert "verdict" in S.rep_diff_keys(a, d)
    assert S.rep_diff_keys(None, a) is None and S.canon_rep(None) is None


def test_u0_diff_has_no_context_lines(tmp_path: Path) -> None:
    tree = tmp_path / "pristine"
    (tree / "internal").mkdir(parents=True)
    (tree / "internal" / "a.go").write_text("".join(f"line {i}\n" for i in range(1, 30)))
    ref = tmp_path / "c01" / "ref.diff"
    ref.parent.mkdir()
    ref.write_text(
        "diff --git a/internal/a.go b/internal/a.go\n--- a/internal/a.go\n+++ b/internal/a.go\n"
        "@@ -12,7 +12,7 @@\n line 12\n line 13\n line 14\n-line 15\n+line 15 changed\n"
        " line 16\n line 17\n line 18\n"
    )
    text = S.u0_diff(tree, ref, tmp_path / "work").read_text()
    assert "@@ -15 +15 @@" in text and "\n line 14\n" not in text
    assert (
        "changed" not in (tree / "internal" / "a.go").read_text()
    )  # the pristine tree is untouched


# ---- SL -----------------------------------------------------------------------------------------


def fault_rec(**over: Any) -> dict[str, Any]:
    rec = {
        "job": "fault-c14",
        "rc": 3,
        "out": {"tiers": {"4": "not_run:error: SideError: the after run exited 1"}},
        "seen_at_fault": 1,
        "leaked_containers": [],
    }
    rec.update(over)
    return rec


def test_sl_pass_and_leaks() -> None:
    runs = [{"job": "so-c01", "leaked_containers": []}]
    ok = S.score_sl(runs, fault_rec(), ["lattice-x"], [])
    assert ok["pass"] is True
    leak = S.score_sl([{"job": "so-c01", "leaked_containers": ["lattice-a"]}], fault_rec(), [], [])
    assert leak["pass"] is False and leak["observed"]["leaked_per_run"] == {"so-c01": ["lattice-a"]}
    assert S.score_sl(runs, fault_rec(), [], ["lattice-new"])["pass"] is False
    assert S.score_sl(runs, fault_rec(leaked_containers=["lattice-f"]), [], [])["pass"] is False


def test_sl_fault_that_proves_nothing_is_not_a_pass() -> None:
    runs = [{"job": "so-c01", "leaked_containers": []}]
    for bad in (
        fault_rec(seen_at_fault=0),
        fault_rec(rc=0),
        fault_rec(out={"tiers": {"4": "run"}}),
    ):
        assert S.score_sl(runs, bad, [], [])["pass"] is None
    assert S.score_sl(runs, None, [], [])["pass"] is None
    assert S.score_sl(runs, None, [], [], fault_required=False)["pass"] is True


# ---- SG and PNM ---------------------------------------------------------------------------------


def run_git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def git_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    run_git(root, "init", "-q", "-b", "build/streak")
    run_git(root, "config", "user.email", "t@example.com")
    run_git(root, "config", "user.name", "T")
    (root / "a.txt").write_text("a\n")
    run_git(root, "add", "--", "a.txt")
    run_git(root, "commit", "-q", "-m", "base")
    return root


def spec_and_build(repo: Path, eid: str = "iabc") -> tuple[str, str]:
    head = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "checkout", "-q", "--detach")
    (repo / "spec.txt").write_text("s\n")
    run_git(repo, "add", "--", "spec.txt")
    run_git(repo, "commit", "-q", "-m", "spec")
    s = run_git(repo, "rev-parse", "HEAD")
    (repo / "b.txt").write_text("b\n")
    run_git(repo, "add", "--", "b.txt")
    run_git(repo, "commit", "-q", "-m", "build")
    b = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "update-ref", f"refs/heads/implement/{eid}", s)
    run_git(repo, "update-ref", f"refs/heads/rebuild/{eid}", b)
    run_git(repo, "checkout", "-q", "build/streak")
    assert run_git(repo, "rev-parse", "HEAD") == head
    return s, b


def test_check_pnm_exact(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    head = run_git(repo, "rev-parse", "HEAD")
    spec_and_build(repo)
    r = S.check_pnm(repo, ("implement/iabc", "rebuild/iabc"), head)
    assert r["ok"] is True and r["applicable"] is True and r["one_new_commit"] is True


def test_check_pnm_detects_a_merge_and_a_moved_head(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    head = run_git(repo, "rev-parse", "HEAD")
    spec_and_build(repo)
    run_git(repo, "merge", "-q", "--ff-only", "refs/heads/rebuild/iabc")  # E must never do this
    r = S.check_pnm(repo, ("implement/iabc", "rebuild/iabc"), head)
    assert r["ok"] is False and r["head_unchanged"] is False and r["build_not_in_head"] is False


def test_check_pnm_without_a_build_and_with_missing_refs(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    head = run_git(repo, "rev-parse", "HEAD")
    assert S.check_pnm(repo, None, head) == {
        "head_unchanged": True,
        "applicable": False,
        "ok": True,
    }
    r = S.check_pnm(repo, ("implement/none", "rebuild/none"), head)
    assert r["ok"] is False and "error" in r


def test_check_pnm_build_with_two_commits_fails(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    head = run_git(repo, "rev-parse", "HEAD")
    spec_and_build(repo)
    run_git(repo, "checkout", "-q", "--detach", "refs/heads/rebuild/iabc")
    (repo / "c.txt").write_text("c\n")
    run_git(repo, "add", "--", "c.txt")
    run_git(repo, "commit", "-q", "-m", "second build commit")
    run_git(repo, "update-ref", "refs/heads/rebuild/iabc", run_git(repo, "rev-parse", "HEAD"))
    run_git(repo, "checkout", "-q", "build/streak")
    r = S.check_pnm(repo, ("implement/iabc", "rebuild/iabc"), head)
    assert r["ok"] is False and r["one_new_commit"] is False


def test_streak_state_keeps_the_leading_space_of_status(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    (repo / "a.txt").write_text("changed\n")
    st = S.streak_state(repo)
    assert st["status"] == [" M a.txt"] and st["branch"] == "build/streak"
    assert len(st["head"]) == 40


def test_owner_files_and_drift(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    (repo / "bin").mkdir()
    (repo / "bin" / "streak-api").write_text("binary")
    before = S.owner_files(repo)
    assert "a.txt" in before and "bin/streak-api" in before and ".git/HEAD" in before
    assert not any(k.startswith(".git/objects") for k in before)
    assert S.snapshot_changes(before, S.owner_files(repo), []) == {
        "changed": [],
        "removed": [],
        "new": [],
    }
    spec_and_build(repo)  # the driver's own refs are allowed, nothing else may appear
    after = S.owner_files(repo)
    drift = S.snapshot_changes(
        before, after, {".git/refs/heads/implement/iabc", ".git/refs/heads/rebuild/iabc"}
    )
    assert drift["new"] == [] and drift["removed"] == []
    # HEAD and the index went through checkout --detach and back: their bytes may differ in mtime
    (repo / "a.txt").write_text("changed\n")
    (repo / "stray.txt").write_text("x")
    drift = S.snapshot_changes(after, S.owner_files(repo), [])
    assert "a.txt" in drift["changed"] and drift["new"] == ["stray.txt"]


def sg_rec(**over: Any) -> dict[str, Any]:
    rec = {
        "job": "so-c01",
        "out": report(),
        "drift": {"changed": [], "removed": [], "new": []},
        "pnm": {"ok": True},
        "worktrees_left": 0,
    }
    rec.update(over)
    return rec


def test_sg_run_and_aggregate() -> None:
    st = {"head": "h", "branch": "build/streak", "status": [" M studio.toml"]}
    assert S.score_sg_run(sg_rec())["ok"] is True
    agg = S.aggregate_sg([sg_rec(), sg_rec(job="so-c02")], st, dict(st))
    assert agg["passed"] and agg["runs"] == "2/2" and agg["streak_checkout_unchanged"]
    bad_drift = sg_rec(drift={"changed": ["a.txt"], "removed": [], "new": []})
    assert S.score_sg_run(bad_drift)["ok"] is False
    assert S.score_sg_run(sg_rec(pnm={"ok": False}))["ok"] is False
    t = tier4(owner={"untouched": False, "checked": []})
    assert S.score_sg_run(sg_rec(out=report(t)))["ok"] is False
    assert S.score_sg_run(sg_rec(out=None, drift=None))["ok"] is False
    # the owner's Streak checkout changing fails the row on its own
    moved = dict(st, status=[])
    assert S.aggregate_sg([sg_rec()], st, moved)["passed"] is False
    assert S.aggregate_sg([], st, st)["passed"] is False


# ---- SA, budget, PAg-MS -------------------------------------------------------------------------


def test_budget_never_exceeds_the_total(tmp_path: Path) -> None:
    b = S.Budget(12.0)
    caps = [b.acquire(6 - k) for k in range(4)]  # four jobs start at once
    assert caps == [2.0, 2.0, 2.0, 2.0] and b.reserved == 8.0
    b.release(caps[0], 1.2)
    nxt = b.acquire(2)
    assert nxt == round((12.0 - 1.2 - 6.0) / 2, 2)
    b.release(nxt, nxt)
    for c in caps[1:]:
        b.release(c, c)
    assert b.spent <= 12.0 + 1e-9
    assert S.Budget(0.03).acquire(1) == 0.0  # nothing worth starting


def test_sa_aggregate(tmp_path: Path) -> None:
    rows = []
    for cid, kind in (("c01", "K1"), ("c02", "K2"), ("c05", "K5")):
        out = report(tier4())
        rows.append(S.score_sa_change(change(tmp_path, cid, kind), out, 0))
    ctrl = tier4(verdict="nothing_to_build", rebuild=None, counts={})
    rows.append(S.score_sa_change(change(tmp_path, "c15", "K15", True), report(ctrl), 0))
    rows.append(
        S.score_sa_change(change(tmp_path, "c09", "K9"), None, None, "the USD cap is spent")
    )
    agg = S.aggregate_sa(rows, 5)
    assert agg["passed_builds"] == 3 and agg["ok_controls"] == 1 and agg["ran"] == 4
    assert agg["passed_incl_controls"] == "4/5" and agg["not_run"] == ["c09"]
    assert agg["cost_usd"] == 4.5 and agg["prediction_hits"]["cost_3_to_10"] is True
    assert agg["regressions_on_passed"] == 0


def test_sa_not_run_reasons() -> None:
    assert S.sa_not_run_reason({"not_run": "the USD cap is spent"}) == "the USD cap is spent"
    assert "usage limit" in S.sa_not_run_reason({"usage_limit": True})
    assert "not logged in" in S.sa_not_run_reason({"not_logged_in": True})
    assert S.sa_not_run_reason({"error": "D's engine exited 2"}) is None  # an error is not hidden


def test_sa_failed_run_and_needs_owner(tmp_path: Path) -> None:
    bad = S.score_sa_change(change(tmp_path), None, 2)
    assert "error" in bad
    t = tier4(verdict="needs_owner", flows=[flow_item("f")])
    rows = [S.score_sa_change(change(tmp_path), report(t), 0)]
    agg = S.aggregate_sa(rows, 1)
    assert agg["needs_owner_on_passed"] == 1


def test_pagms_aggregate() -> None:
    i1 = S.score_pagms_run("i1", report(tier4()), None)
    i3 = S.score_pagms_run(
        "i3", report(tier4(verdict="fail", flows=[flow_item("w", "unreported")])), None
    )
    bad = S.score_pagms_run("bad_invariant", report(tier4(verdict="not_built")), None)
    agg = S.aggregate_pagms([i1, i3, bad])
    assert agg["prediction_hits"]["i1_pass"] and not agg["prediction_hits"]["unreported_0"]
    assert agg["prediction_hits"]["bad_invariant_not_built_or_pass_0_regressions"]
    assert agg["cost_usd"] == 4.5
    skipped = S.aggregate_pagms([i1, {"run": "i3", "not_run": "usage limit"}])
    assert skipped["not_run"] == ["i3"] and skipped["ran"] == ["i1"]
    assert S.score_pagms_run("i1", None, "refused")["error"] == "refused"


# ---- the claude CLI -----------------------------------------------------------------------------


def test_usage_limit_detection() -> None:
    assert S.usage_limit_in("", "Claude AI usage limit reached|1760000000")
    assert S.usage_limit_in("", "You've hit your limit")
    ev = json.dumps(
        {"type": "result", "is_error": True, "result": "Your credit balance is too low"}
    )
    assert S.usage_limit_in(ev + "\n", "")
    # prose that merely says "resets" is not a limit, a 429 rate limit is not one either
    prose = json.dumps({"type": "assistant", "message": {"content": "this resets the counter"}})
    assert not S.usage_limit_in(prose + "\n", "")
    assert not S.usage_limit_in("", "rate limit reached, retrying")
    assert not S.usage_limit_in("", "")


def test_login_failure_detection() -> None:
    assert S.login_failure_in("", "Not logged in. Please run /login")
    ev = json.dumps({"type": "result", "is_error": True, "result": "Invalid API key"})
    assert S.login_failure_in(ev, "")
    assert not S.login_failure_in("", "all good")


def test_fail_shim_logs_and_exits_1(tmp_path: Path) -> None:
    d = S.write_shim(tmp_path / "shim", "fail", None)
    p = subprocess.run([str(d / "claude"), "-p", "hello"], capture_output=True, text=True)
    assert p.returncode == 1
    assert S.shim_report(d) == {"calls": 1, "usage_limit": False, "not_logged_in": False}


def test_tee_shim_passes_through_and_detects_a_limit(tmp_path: Path) -> None:
    real = tmp_path / "real-claude"
    real.write_text(
        '#!/bin/sh\ncat > /dev/null\necho \'{"type":"result","is_error":true,'
        '"result":"Claude usage limit reached"}\'\necho oops >&2\nexit 7\n'
    )
    real.chmod(0o755)
    d = S.write_shim(tmp_path / "shim", "tee", str(real))
    p = subprocess.run(
        [str(d / "claude"), "-p"], input="prompt", capture_output=True, text=True, check=False
    )
    assert p.returncode == 7  # the real exit code survives the pipe
    assert "usage limit reached" in p.stdout and "oops" in p.stderr
    rep = S.shim_report(d)
    assert rep["calls"] == 1 and rep["usage_limit"] is True
    with pytest.raises(ValueError):
        S.write_shim(tmp_path / "x", "tee", None)


# ---- the scratch clone --------------------------------------------------------------------------

STREAK_TOML = """[project]
name = "streak"

[check]
layers = "ABCDX"
go_src = "/Users/someone/Documents/streak"

[rebuild]
repo = "."
gates = ["./check.sh"]
max_rounds = 3

[llm]
model = "sonnet"
"""


def test_scratch_studio_toml(tmp_path: Path) -> None:
    clone, inv = tmp_path / "S", tmp_path / "inv"
    out = S.scratch_studio_toml(STREAK_TOML, clone=clone, inventory=inv)
    import tomllib

    data = tomllib.loads(out)
    assert data["check"]["go_src"] == str(clone)
    assert data["rebuild"]["round_timeout"] == 1800 and data["rebuild"]["gate_timeout"] == 2400
    assert data["rebuild"]["gates"] == ["./check.sh"] and data["rebuild"]["max_rounds"] == 3
    assert data["implementation"] == {"inventory": str(inv), "app_root": str(clone)}
    assert data["impact"] == {}
    acd = tomllib.loads(S.scratch_studio_toml(STREAK_TOML, clone=clone, inventory=inv, gate="acd"))
    assert acd["rebuild"]["gates"] == ["./check.sh --layers ACD"]
    with pytest.raises(ValueError):
        S.scratch_studio_toml(STREAK_TOML, clone=clone, inventory=inv, gate="other")
    with pytest.raises(ValueError):
        S.scratch_studio_toml("[project]\nname='x'\n", clone=clone, inventory=inv)


def test_post_checkout_hook_builds_the_binary_in_every_worktree(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    (repo / "cmd" / "server").mkdir(parents=True)
    (repo / "cmd" / "server" / "main.go").write_text("package main\n")
    (repo / ".gitignore").write_text("bin/\n")
    run_git(repo, "add", "--", "cmd", ".gitignore")
    run_git(repo, "commit", "-q", "-m", "app")
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(S.POST_CHECKOUT)
    hook.chmod(0o755)
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "go").write_text(
        '#!/bin/sh\n# fake go build -o OUT ./cmd/server\nout="$3"\nmkdir -p "$(dirname "$out")"\n'
        'echo "built from $(git rev-parse --short HEAD)" > "$out"\n'
    )
    (fake / "go").chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}"}
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(wt), "HEAD"],
        check=True,
        env=env,
    )
    assert (wt / "bin" / "streak-api").read_text().startswith("built from ")
    assert run_git(wt, "status", "--porcelain") == ""  # bin/ is ignored: nothing to commit
    # a build that fails never fails the checkout
    (fake / "go").write_text("#!/bin/sh\nexit 1\n")
    wt2 = tmp_path / "wt2"
    r = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(wt2), "HEAD"],
        capture_output=True,
        env=env,
    )
    assert r.returncode == 0 and not (wt2 / "bin" / "streak-api").exists()


def test_fault_sitecustomize_raises_on_the_second_side_process(tmp_path: Path) -> None:
    fdir = tmp_path / "fault"
    fdir.mkdir()
    (fdir / "sitecustomize.py").write_text(S.FAULT_SITECUSTOMIZE)
    docker = tmp_path / "bin"
    docker.mkdir()
    (docker / "docker").write_text("#!/bin/sh\necho lattice-fake-container\n")
    (docker / "docker").chmod(0o755)
    env = {
        **os.environ,
        "PYTHONPATH": f"{fdir}{os.pathsep}{REPO}",
        "PATH": f"{docker}{os.pathsep}{os.environ['PATH']}",
        "FAULT_COUNTER": str(tmp_path / "counter"),
        "FAULT_SEEN": str(tmp_path / "seen"),
        "FAULT_AFTER": "3",
        "LATTICE_CONTAINER_OWNER": "tok",
    }
    probe = (
        "import sys, importlib\n"
        "C = importlib.import_module('lattice.spec.check')\n"
        "calls = 0\n"
        "try:\n"
        "    for _ in range(5):\n"
        "        try:\n"
        "            C._run_scenario()\n"
        "        except TypeError:\n"
        "            pass  # the real function was reached (wrong arguments): not faulted\n"
        "        calls += 1\n"
        "except RuntimeError as e:\n"
        "    print('raised after', calls, e)\n"
    )

    def run(argv_tail: str) -> str:
        p = subprocess.run(
            [sys.executable, "-c", probe, argv_tail], capture_output=True, text=True, env=env
        )
        assert p.returncode == 0, p.stderr
        return p.stdout.strip()

    assert run("not-a-side-process") == ""  # a process that is no `_side` is untouched
    assert (tmp_path / "counter").exists() is False
    assert run("_side") == ""  # the first side process (the before run) is left alone
    assert (tmp_path / "counter").read_text() == "1"
    assert run("_side").startswith("raised after 2 injected fault in the after run")
    assert (tmp_path / "seen").read_text() == "lattice-fake-container"


# ---- arguments, the dry run ---------------------------------------------------------------------


def args_for(tmp_path: Path, *extra: str) -> list[str]:
    return ["--scratch", str(tmp_path / "s"), "--out", str(tmp_path / "o"), *extra]


def test_validate_args_limits(tmp_path: Path) -> None:
    ap = S.parser()
    streak = tmp_path / "streak"
    streak.mkdir()
    ok = ap.parse_args(["--agent", "claude", "--changeset", "x", *args_for(tmp_path)])
    ok.usd_cap = 12.0
    assert S.validate_args(ok, streak) is None
    ok.usd_cap = 12.5
    assert "hard cap" in (S.validate_args(ok, streak) or "")
    pg = ap.parse_args(["--agent", "pagms", *args_for(tmp_path)])
    pg.usd_cap = 3.5
    assert "hard cap" in (S.validate_args(pg, streak) or "")
    pg.usd_cap = 3.0
    assert S.validate_args(pg, streak) is None  # PAg-MS needs no change set
    par = ap.parse_args(
        ["--agent", "oracle", "--changeset", "x", "--parallel", "5", *args_for(tmp_path)]
    )
    assert "--parallel" in (S.validate_args(par, streak) or "")
    inside = ap.parse_args(
        [
            "--agent",
            "oracle",
            "--changeset",
            "x",
            "--scratch",
            str(streak / "s"),
            "--out",
            str(tmp_path / "o"),
        ]
    )
    assert "Streak checkout" in (S.validate_args(inside, streak) or "")
    none = ap.parse_args(["--agent", "oracle", *args_for(tmp_path)])
    assert "--changeset" in (S.validate_args(none, streak) or "")


def test_argv_builders(tmp_path: Path) -> None:
    common = dict(
        clone=tmp_path / "S", report=tmp_path / "r.json", cdir=tmp_path / "c01", inv=tmp_path / "inv",
        work=tmp_path / "w", out=tmp_path / "o.json",
    )  # fmt: skip
    oracle = S.implement_argv(
        **common, agent="oracle", ref_diff=tmp_path / "ref.diff", usd_cap=None
    )
    assert (
        oracle[3:5] == ["spec", "implement"]
        and "--ref-diff" in oracle
        and "--usd-cap" not in oracle
    )
    claude = S.implement_argv(**common, agent="claude", ref_diff=None, usd_cap=2.0)
    assert claude[claude.index("--agent") + 1] == "claude"
    assert (
        claude[claude.index("--max-rounds") + 1] == "3"
        and claude[claude.index("--model") + 1] == "sonnet"
    )
    assert claude[claude.index("--usd-cap") + 1] == "2" and "--ref-diff" not in claude
    d = S.d_argv(tmp_path / "S", tmp_path / "c01", tmp_path / "inv", tmp_path / "i.json")
    assert d[3:5] == ["spec", "impact"] and d[d.index("--tiers") + 1] == "123"
    assert d[d.index("--baseline") + 1] == "run"


def test_child_env_has_no_key_and_the_driver_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    tmp = tmp_path / "t"
    tmp.mkdir()
    shim = tmp_path / "shim"
    env = S.child_env(tmp, owner="tok", shim=shim)
    assert not any(k.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_")) for k in env)
    assert env["LATTICE_CONTAINER_OWNER"] == "tok" and env["TMPDIR"] == str(tmp)
    assert env["PATH"].split(os.pathsep)[0] == str(shim)
    assert env["GOFLAGS"] == "-mod=readonly" and env["GOPROXY"] == "off"
    assert env["UV_NO_SYNC"] == "1"
    if sys.prefix != sys.base_prefix:
        assert env["UV_PROJECT_ENVIRONMENT"] == sys.prefix


def test_freeze_status_and_engine_drift(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    fz = tmp_path / "freeze.sha256"
    fz.write_text(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  a.py\n")
    assert S.freeze_status(tmp_path, "freeze.sha256")["ok"] is True
    f.write_text("x = 2\n")
    st = S.freeze_status(tmp_path, "freeze.sha256")
    assert st["ok"] is False and st["files"] == {"a.py": "MISMATCH"}
    assert S.freeze_status(tmp_path, "missing.sha256")["ok"] is False
    # the core freeze of this checkout holds (the runner refuses to start otherwise)
    assert S.freeze_status()["ok"] is True


def test_dry_run_lists_every_rows_inputs_and_touches_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    cs = fixture_changeset(tmp_path / "cs", SET)
    streak = git_repo(tmp_path / "streak")
    before = S.streak_state(streak)
    code = S.main(
        [
            "--stage", "streak", "--dry-run", "--agent", "oracle", "--changeset", str(cs),
            "--allow-other-changeset", "--streak", str(streak), "--streak-ref", "HEAD",
            *args_for(tmp_path),
        ]
    )  # fmt: skip
    assert code == 0
    rep = json.loads((tmp_path / "o" / "dry-run.json").read_text())
    assert rep["changeset"]["changes"] == 5 and rep["streak"]["ref_resolves"] is True
    rows = rep["rows"]
    assert set(rows) == {"SO", "SO3", "SO-rep", "SA", "SL", "SG", "PAg-MS"}
    assert [i["id"] for i in rows["SO"]["inputs"]] == ["c01", "c02", "c03", "c15", "c16"]
    ctrl = next(i for i in rows["SO"]["inputs"] if i["id"] == "c15")
    assert "claude" in ctrl["agent"] and ctrl["expected"] == "nothing_to_build"
    assert [c["id"] for c in rows["SA"]["subset"]] == ["c01", "c02", "c15"]
    assert rows["SO3"]["controls"] == ["c15"] and rows["SA"]["usd_cap_total"] == 12.0
    assert [r["name"] for r in rows["PAg-MS"]["runs"]] == ["i1", "i3", "bad_invariant"]
    assert all(r["candidate_dir_exists"] for r in rows["PAg-MS"]["runs"])
    assert rep["freeze"]["ok"] is True and rep["runner_sha256"]
    assert "implement_oracle" in rep["commands"] and rep["commands"]["truth"][2].endswith("main.go")
    assert S.streak_state(streak) == before  # nothing written into the Streak stand-in
    assert not (tmp_path / "s").exists()  # no scratch clone was made


def test_main_refuses_the_wrong_manifest_and_a_bad_cap(tmp_path: Path) -> None:
    cs = fixture_changeset(tmp_path / "cs", SET)
    base = ["--stage", "streak", "--dry-run", "--changeset", str(cs), *args_for(tmp_path)]
    assert S.main([*base, "--agent", "oracle"]) == 2  # not the registered manifest
    assert S.main([*base, "--agent", "claude", "--usd-cap", "13", "--allow-other-changeset"]) == 2
