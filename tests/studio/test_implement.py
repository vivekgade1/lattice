"""`lattice spec implement` (lattice/studio/implement.py; Spike E, docs/results/
2026-10-17-implement-verify-prereg.md) end to end on the mini-shop corpus of
tests/spec/fixtures/implement: the rebuild loop with the scripted stub and oracle agents in a
throwaway git repository, the before and after runs in worktrees, the guards and the refusals. No
model is called; the Streak arm is not here."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice.spec import impact_report as ir
from lattice.spec import impact_tier4 as t4
from lattice.spec.impact_tier4 import Refusal
from lattice.studio import implement as im

_spec = importlib.util.spec_from_file_location(
    "implement_ms", Path(__file__).resolve().parents[1] / "spec/fixtures/implement/ms.py"
)
ms = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["implement_ms"] = ms
_spec.loader.exec_module(ms)  # type: ignore[union-attr]

EXPECT = {  # id -> (verdict, tiers["4"], rebuild status, rounds)
    "i1": ("pass", "run", "passed", 1),
    "i2": ("pass", "run", "passed", 2),
    "i3": ("fail", "run", "passed", 1),
    "i4": ("fail", "run", "passed", 1),
    "i5": ("nothing_to_build", "run", None, None),
    "i6": ("nothing_to_build", "run", None, None),
    "i7": ("nothing_to_build", "run", None, None),
    "i8": ("not_built", "not_run:rebuild_gave_up", "gave_up", 2),
    "i9": ("not_built", "not_run:rebuild_gave_up", "gave_up", 2),
    "i11": ("pass", "run", "passed", 1),
}


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory):
    scratch = tmp_path_factory.mktemp("implement")
    cache: dict[str, Any] = {}
    runs: dict[str, ms.Run] = {}

    def get(cid: str, **kw: Any) -> ms.Run:
        key = cid + kw.get("tag", "")
        if key not in runs:
            runs[key] = ms.run_change(scratch, cid, d_cache=cache, **kw)
        return runs[key]

    get.scratch = scratch  # type: ignore[attr-defined]
    get.cache = cache  # type: ignore[attr-defined]
    yield get
    for r in runs.values():
        r.cleanup()


@pytest.mark.parametrize("cid", sorted(EXPECT))
def test_the_dev_changes_end_as_predicted(world, cid):
    r = world(cid)
    assert r.refusal is None, r.refusal
    verdict, state, status, rounds = EXPECT[cid]
    assert r.out.code == 0 and r.out.report["tiers"]["4"] == state
    assert r.tier4["verdict"] == verdict
    if status is None:
        assert r.tier4["rebuild"] is None and r.tier4["implementation"] is None
    else:
        assert r.tier4["rebuild"]["status"] == status and r.tier4["rebuild"]["rounds"] == rounds
    assert r.tier4["owner"]["untouched"] is True and r.tier4["before"]["matches_report"] is True


def test_a_conflicting_candidate_is_refused_before_any_agent_is_spent(world):
    r = world("i10")
    assert r.out is None and r.refusal.code == "spec_contradiction"
    assert r.worktrees_after == r.worktrees_before and r.drift() == {
        "changed": [], "removed": [], "new": []}  # fmt: skip


def test_i1_resolves_the_failing_scenario_and_the_flow_that_repeats_it_changes_as_reported(world):
    t = world("i1").tier4
    assert [i["target"] for i in t["resolved"]] == ["scenario:ann cancels her pending order"]
    assert t["resolved"][0]["rules"] == ["R1"]
    assert t["regressions"] == t["still_failing"] == t["failing"] == []
    changed = [i for i in t["flows"] if i["kind"] == "flow_changed"]
    assert [(i["flow"], i["reported_as"], i["spec_backed"]) for i in changed] == [
        ("wf-cancel-twice", "t2_determined", True)
    ]
    assert t["t2_confirmed"] is True and t["counts"]["unexpected_regressions"] == 0
    assert t["counts"]["flow_unchanged"] == 4 and t["counts"]["flows_run"] == 5
    assert t["rebuild"]["gates"] == [{"command": "./gate.py", "exit_code": 0}]


def test_i2_passes_in_round_two_with_the_same_tier4_as_i1(world):
    a, b = world("i1").tier4, world("i2").tier4
    assert a["rebuild"]["rounds"] == 1 and b["rebuild"]["rounds"] == 2
    for key in ("resolved", "flows", "t2_check", "counts", "verdict"):
        assert a[key] == b[key], key


def test_an_oracle_build_has_the_same_tier4_as_the_stub_build_except_the_agent(world):
    a, b = world("i1").tier4, world("i11").tier4
    assert a["rebuild"]["agent"] == "stub" and b["rebuild"]["agent"] == "oracle"
    a2, b2 = json.loads(json.dumps(a)), json.loads(json.dumps(b))
    a2["rebuild"]["agent"] = b2["rebuild"]["agent"] = "-"
    assert a2 == b2


def test_an_unexpected_regression_on_an_uncovered_route_fails_the_gate_i3_i4(world):
    for cid, flow_id in (("i3", "wf-list"), ("i4", "wf-read-ben")):
        t = world(cid).tier4
        assert t["rebuild"]["status"] == "passed" and t["rebuild"]["gates"][0]["exit_code"] == 0
        (item,) = [
            i for i in t["flows"] if i["kind"] == "flow_changed" and i["flow"] != "wf-cancel-twice"
        ]
        assert item["flow"] == flow_id and item["reported_as"] == "unreported"
        assert t["verdict"] == "fail" and t["counts"]["unreported"] == 1
        assert t["counts"]["unexpected_regressions"] == 1 and t["regressions"] == []


def test_a_build_that_gave_up_has_no_after_run_and_no_verdict_beyond_not_built(world):
    for cid in ("i8", "i9"):
        r = world(cid)
        t = r.tier4
        assert t["verdict"] == "not_built" and t["failing"] == t["flows"] == t["resolved"] == []
        assert t["implementation"]["build_tree"] is None and t["implementation"]["files"] == []
        assert t["before"]["failing_count"] == 1 and t["counts"] == {}
        assert r.out.report["tiers"]["4"] == "not_run:rebuild_gave_up"


def test_i8_the_spec_edit_is_restored_and_fails_both_rounds(world):
    t = world("i8").tier4
    assert t["rebuild"]["rounds"] == 2 and t["rebuild"]["gates"][0]["exit_code"] == 0
    # the gate passed both rounds: the rounds failed on the integrity check alone (EM16)


# ---- never merges; the owner's checkout is untouched (PG, PNM) -------------------------------


@pytest.mark.parametrize("cid", sorted(EXPECT))
def test_the_owners_files_refs_and_index_are_untouched(world, cid):
    r = world(cid)
    d = r.drift()
    assert d == {"changed": [], "removed": [], "new": []}, d
    assert r.tier4["owner"]["untouched"] is True
    assert ms.git(r.repo, "rev-parse", "HEAD") == r.head
    assert ms.git(r.repo, "status", "--porcelain").split() in (["??", "stub-rebuild.json"], [])


def test_a_passed_build_is_one_commit_on_the_spec_commit_and_is_never_merged(world):
    r = world("i1")
    git = ms.git
    spec_c = git(r.repo, "rev-parse", f"implement/{r.eid}")
    build_c = git(r.repo, "rev-parse", f"rebuild/{r.eid}")
    assert git(r.repo, "rev-parse", f"{spec_c}^") == r.head
    assert git(r.repo, "rev-parse", f"{build_c}^") == spec_c
    assert git(r.repo, "rev-list", "--count", f"{spec_c}..{build_c}") == "1"
    ancestor = subprocess.run(["git", "-C", str(r.repo), "merge-base", "--is-ancestor",
                               build_c, r.head], capture_output=True)  # fmt: skip
    assert ancestor.returncode == 1
    assert git(r.repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert r.out.hint and "Studio never merges" in r.out.hint


def is_ancestor(repo: Path, a: str, b: str) -> bool:
    cmd = ["git", "-C", str(repo), "merge-base", "--is-ancestor", a, b]
    return subprocess.run(cmd, capture_output=True).returncode == 0


def test_a_passed_build_is_not_merged_even_when_nothing_blocks_a_fast_forward(world):
    r = world("i1", tag="-ff", repo_hook=ms.commit_stub)
    assert r.tier4["verdict"] == "pass" and r.out.code == 0
    build_c = ms.git(r.repo, "rev-parse", f"rebuild/{r.eid}")
    assert ms.git(r.repo, "rev-parse", "HEAD") == r.head
    assert ms.git(r.repo, "rev-parse", "main") == r.head
    assert not is_ancestor(r.repo, build_c, "main")
    # a fast-forward WOULD have been possible: the build descends from the owner's HEAD
    assert is_ancestor(r.repo, "main", build_c)
    assert r.drift() == {"changed": [], "removed": [], "new": []}


def test_the_spec_commit_changes_exactly_the_spec_files_and_the_stub_with_its_trailer(world):
    r = world("i1")
    sha = ms.git(r.repo, "rev-parse", f"implement/{r.eid}")
    names = set(ms.git(r.repo, "diff", "--name-only", f"{sha}^", sha).split())
    assert names <= {"REQUIREMENTS.md", "spec.py", "spec.trace.json", "stub-rebuild.json"}
    assert {"REQUIREMENTS.md", "spec.py", "stub-rebuild.json"} <= names
    assert f"Lattice-Implement: {r.eid}" in ms.git(r.repo, "log", "-1", "--format=%B", sha)
    assert r.out.report["tier4"]["implementation"]["files"] == ["shop/routes.py"]


def test_an_untracked_owner_file_never_reaches_the_spec_commit(world):
    def drop(repo: Path) -> None:
        (repo / "notes.txt").write_text("not for the spec commit\n")
        (repo / "shop" / "scratch.py").write_text("X = 1\n")

    r = world("i1", tag="-untracked", repo_hook=drop)
    sha = ms.git(r.repo, "rev-parse", f"implement/{r.eid}")
    assert "notes.txt" not in ms.git(r.repo, "ls-tree", "-r", "--name-only", sha).split()
    assert "shop/scratch.py" not in ms.git(r.repo, "ls-tree", "-r", "--name-only", sha).split()
    assert r.drift() == {"changed": [], "removed": [], "new": []}
    assert "?? notes.txt" in ms.git(r.repo, "status", "--porcelain")  # still untracked, unstaged


def test_cleanup_leaves_the_review_worktree_only_when_a_build_ran(world):
    for cid in ("i1", "i3", "i8", "i9", "i11"):
        r = world(cid)
        assert len(r.review_worktrees()) == 1, cid
        assert sorted(p.name for p in r.work.iterdir()) == [], cid
    for cid in ("i5", "i6", "i7", "i10"):
        r = world(cid)
        assert r.worktrees_after == r.worktrees_before, cid
        assert not r.work.exists() or sorted(r.work.iterdir()) == [], cid


def test_the_output_is_the_input_report_but_for_two_keys(world):
    for cid in ("i1", "i5", "i8", "i3"):
        r = world(cid)
        out = r.out.report
        strip = lambda rep: {  # noqa: E731
            k: (({a: b for a, b in v.items() if a != "4"}) if k == "tiers" else v)
            for k, v in rep.items()
            if k != "tier4"
        }
        assert ir.report_json(strip(out)) == ir.report_json(strip(r.report_in)), cid
        assert out["summary"] == r.report_in["summary"] and out["tier1"] == r.report_in["tier1"]


def test_the_output_names_no_path_time_host_or_commit(world):
    r = world("i3")
    text = ir.report_json(r.out.report)
    assert not re.search(r"/home|/tmp|/Users|/private|127\.0\.0\.1", text)
    assert not re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text)
    shas = {
        r.head,
        *(ms.git(r.repo, "rev-parse", b) for b in (f"implement/{r.eid}", f"rebuild/{r.eid}")),
    }
    assert not [s for s in shas if s in text]
    impl = r.out.report["tier4"]["implementation"]
    stripped = text.replace(impl["spec_tree"], "").replace(impl["build_tree"], "")
    assert not re.search(r"\b[0-9a-f]{40}\b", stripped)


def test_two_runs_in_different_places_give_the_same_bytes(world, tmp_path):
    other = ms.run_change(tmp_path, "i1", d_cache=world.cache)
    try:
        assert ir.report_json(world("i1").out.report) == ir.report_json(other.out.report)
    finally:
        other.cleanup()


# ---- the refusals (D2) -----------------------------------------------------------------------


def refuse(world, tag: str, **kw: Any) -> Refusal:
    r = world("i1", tag=tag, **kw)
    assert r.out is None and r.refusal is not None, tag
    assert r.drift() == {"changed": [], "removed": [], "new": []}
    assert r.worktrees_after == r.worktrees_before
    return r.refusal  # type: ignore[no-any-return]


def test_refusals_bad_report_and_report_mismatch(world):
    def tier4_set(rep: dict) -> None:
        rep["tier4"] = {"verdict": "pass"}

    def bad_schema(rep: dict) -> None:
        rep["schema"] = "lattice.impact/9"

    assert refuse(world, "-r1", report_hook=tier4_set).code == "bad_report"
    assert refuse(world, "-r2", report_hook=bad_schema).code == "bad_report"

    def base_byte(rep: dict) -> None:
        rep["input"]["base"]["spec"] = "0" * 64

    def cand_byte(rep: dict) -> None:
        rep["input"]["candidate"]["requirements"] = "0" * 64

    def inventory(rep: dict) -> None:
        rep["input"]["inventory"]["workflows_sha256"] = "0" * 64

    assert refuse(world, "-r3", report_hook=base_byte).code == "report_mismatch"
    assert refuse(world, "-r4", report_hook=cand_byte).code == "report_mismatch"
    assert refuse(world, "-r5", report_hook=inventory).code == "report_mismatch"


def test_refusals_stale_inventory_and_a_missing_stub(world):
    def stale(rep: dict) -> None:
        rep["input"]["inventory"]["stale"] = True

    assert refuse(world, "-stale", report_hook=stale).code == "stale_inventory"

    def no_stub(repo: Path) -> None:
        (repo / "stub-rebuild.json").unlink()

    assert refuse(world, "-nostub", repo_hook=no_stub).code == "no_rebuild"


def _make_branch(repo: Path, kind: str) -> None:
    rep = json.loads((repo.parent / "impact.json").read_text())
    eid = t4.eid_of(rep["input"]["base"], rep["input"]["candidate"])
    ms.git(repo, "branch", f"{kind}/{eid}")


def test_refusals_an_existing_spec_or_build_branch(world):
    r = refuse(world, "-exists", repo_hook=lambda repo: _make_branch(repo, "implement"))
    assert r.code == "branch_exists"
    r = refuse(world, "-exists2", repo_hook=lambda repo: _make_branch(repo, "rebuild"))
    assert r.code == "branch_exists"


def test_refusals_protected_branch_and_paths_inside_the_project(world):
    def protect(repo: Path) -> None:
        rep = json.loads((repo.parent / "impact.json").read_text())
        eid = t4.eid_of(rep["input"]["base"], rep["input"]["candidate"])
        toml = repo / "studio.toml"
        toml.write_text(toml.read_text() + f'protected_branches = ["implement/{eid}"]\n')
        ms.git(repo, "commit", "-qam", "protect")

    assert refuse(world, "-prot", repo_hook=protect).code == "branch_protected"

    def work_inside(opts: im.Options) -> None:
        opts.work = opts.project / "work"

    def out_inside(opts: im.Options) -> None:
        opts.out = opts.project / "out.json"

    assert refuse(world, "-w", opts_hook=work_inside).code == "bad_paths"
    assert refuse(world, "-o", opts_hook=out_inside).code == "bad_paths"


# ---- failures: a raise mid after-run, a gate that fails, a tripped owner guard -----------------


def test_a_raise_in_the_after_run_is_exit_3_with_the_report_written_and_nothing_left(world):
    calls: list[str] = []
    real = {}

    def runner(worktree: Path, flows: Any, which: str) -> dict:
        calls.append(which)
        if which == "after":
            raise RuntimeError("the app would not start")
        real.setdefault("r", im.subprocess_side_runner(Path(worktree), "."))
        return real["r"](worktree, flows, which)

    r = world("i1", tag="-raise", side_runner=runner)
    assert r.out.code == 3 and calls == ["before", "after"]
    assert r.out.report["tiers"]["4"].startswith("not_run:error: RuntimeError")
    t = r.tier4
    assert t["verdict"] == "fail" and t["owner"]["untouched"] is True
    assert r.drift() == {"changed": [], "removed": [], "new": []}
    assert len(r.review_worktrees()) == 1 and sorted(r.work.iterdir()) == []


def test_a_raise_in_the_before_run_is_exit_3_not_built(world):
    def runner(worktree: Path, flows: Any, which: str) -> dict:
        raise RuntimeError("no app")

    r = world("i1", tag="-rb", side_runner=runner)
    assert r.out.code == 3 and r.tier4["verdict"] == "not_built"
    assert r.out.report["tiers"]["4"].startswith("not_run:error")
    assert r.worktrees_after == r.worktrees_before and not list(r.work.iterdir())


def test_a_tripped_owner_guard_is_a_fail_and_exit_3(world):
    def meddle(worktree: Path, flows: Any, which: str) -> dict:
        subprocess.run(["git", "-C", str(world.scratch / "i1-guard" / "repo"), "commit", "-q",
                        "--allow-empty", "-m", "meddle"], check=True)  # fmt: skip
        return im.subprocess_side_runner(Path(worktree), ".")(worktree, flows, which)

    r = world("i1", tag="-guard", side_runner=meddle)
    assert r.out.code == 3 and r.out.report["tiers"]["4"] == "not_run:owner_changed"
    t = r.tier4
    assert t["owner"]["untouched"] is False and t["verdict"] == "fail"
    assert any("head" in n for n in t["notes"])


# ---- no model, no key -------------------------------------------------------------------------


def test_the_stub_and_oracle_modes_call_no_model_and_children_see_no_api_key(tmp_path, monkeypatch):
    shim = tmp_path / "bin"
    shim.mkdir()
    log = tmp_path / "claude.log"
    (shim / "claude").write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
    (shim / "claude").chmod(0o755)
    envlog = tmp_path / "env.log"
    monkeypatch.setenv("PATH", f"{shim}:{os.environ['PATH']}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv(im.PROBE_ENVLOG, str(envlog))
    cache: dict[str, Any] = {}
    for cid in ("i1", "i11"):
        r = ms.run_change(tmp_path, cid, d_cache=cache)
        assert r.tier4["verdict"] == "pass"
        r.cleanup()
    assert not log.exists()
    entries = [json.loads(x) for x in envlog.read_text().splitlines()]
    assert {e["who"] for e in entries} == {"gate", "side"} and len(entries) == 6
    assert not any(e["key"] for e in entries)


# ---- the CLI ----------------------------------------------------------------------------------


def test_the_cli_runs_i1_and_writes_the_report(tmp_path):
    root = tmp_path / "cli"
    repo = ms.build_ms_repo(root / "repo", "i1")
    imp = root / "impact.json"
    cand = ms.candidate_dir("status_changed")
    imp.write_text(ir.report_json(ms.d_report(repo, cand, root / "shop")))
    out = root / "out" / "report.json"
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    proc = subprocess.run(
        [sys.executable, "-m", "lattice.spec", "implement", "--project", str(repo), "--impact",
         str(imp), "--candidate", str(ms.candidate_dir("status_changed")), "--flows", str(ms.FLOWS),
         "--agent", "stub", "--max-rounds", "3", "--json", "--out", str(out)],
        capture_output=True, text=True, env=env, cwd=str(repo),
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    rep = json.loads(proc.stdout)
    assert rep["tiers"]["4"] == "run" and rep["tier4"]["verdict"] == "pass"
    assert out.read_text() == proc.stdout
    assert "spec commit" in proc.stderr or "commit" in proc.stderr
    assert "Studio never merges" in proc.stderr
    refused = subprocess.run(
        [sys.executable, "-m", "lattice.spec", "implement", "--project", str(repo), "--impact",
         str(imp), "--candidate", str(ms.candidate_dir("status_changed")), "--flows", str(ms.FLOWS),
         "--agent", "stub"],
        capture_output=True, text=True, env=env, cwd=str(repo),
    )  # fmt: skip
    assert refused.returncode == 2 and "branch_checked_out" in refused.stderr
    ms.git(repo, "worktree", "prune")
    for wt in ms.worktrees(repo)[1:]:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", wt])


# ---- the smaller parts ------------------------------------------------------------------------


def test_a_studio_change_record_is_read_as_a_plain_file_and_its_request_is_used(world, tmp_path):
    from lattice.spec.impact_tier4 import change_dict

    base = ir.read_side(ms.HERE / "base")
    cand = ir.read_side(ms.candidate_dir("status_changed"))
    sd = tmp_path / "state"
    (sd / "changes").mkdir(parents=True)
    rec = {
        "record": {"id": "c7", "status": "ready", "request": "Cancelling twice answers 400."},
        "private": {
            "base": {"markdown": base.markdown, "module": base.module, "trace": base.trace_text},
            "new_md": cand.markdown, "new_module": cand.module, "new_trace": cand.trace_text,
        },
    }  # fmt: skip
    (sd / "changes" / "c7.json").write_text(json.dumps(rec))
    before = {p.name: p.read_bytes() for p in sd.rglob("*") if p.is_file()}
    repo = ms.build_ms_repo(tmp_path / "repo", "i1")
    inp = ir.build_inputs(project=repo, change="c7", state_dir=sd)
    opts = im.Options(project=repo, impact=tmp_path / "x", change="c7", state_dir=sd)
    assert im._request_of(opts, inp) == "Cancelling twice answers 400."
    assert (
        inp.kind == "change"
        and inp.cand.hashes() == ir.read_side(ms.candidate_dir("status_changed")).hashes()
    )
    ch = change_dict("iabc", {"diff": {"rules": {}}}, request=im._request_of(opts, inp),
                     markdown_diff="", before={"findings": []})  # fmt: skip
    assert ch["request"] == "Cancelling twice answers 400."
    assert {p.name: p.read_bytes() for p in sd.rglob("*") if p.is_file()} == before
    assert not (sd / "meta.json").exists()  # StateStore was never opened


def test_flows_come_from_an_inventory_or_a_flows_file_and_a_stale_inventory_is_flagged(tmp_path):
    from tests.spec.test_impact_report import _gin_inventory

    inv = _gin_inventory(tmp_path / "ok")
    flows, hashes, stale = im._load_flows(im.Options(project=tmp_path, impact=tmp_path), inv)
    assert flows and not stale and hashes["workflows_sha256"] == inv.workflows_sha256
    assert all(f["status"] == "verified" for f in flows)
    stale_inv = _gin_inventory(tmp_path / "stale", stale=True)
    flows, hashes, stale = im._load_flows(im.Options(project=tmp_path, impact=tmp_path), stale_inv)
    assert flows == [] and stale is True
    flows, hashes, stale = im._load_flows(
        im.Options(project=tmp_path, impact=tmp_path, flows=ms.FLOWS), None
    )
    assert (
        len(flows) == 5
        and hashes["extract_sha256"] is None
        and len(hashes["workflows_sha256"]) == 64
    )
    with pytest.raises(Refusal) as e:
        im._load_flows(
            im.Options(project=tmp_path, impact=tmp_path, flows=tmp_path / "nope.json"), None
        )
    assert e.value.code == "bad_inputs"


def git_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    ms.git(root, "init", "-q", "-b", "main")
    ms.git(root, "config", "user.email", "t@example.com")
    ms.git(root, "config", "user.name", "T")
    return root


def test_the_oracle_script_holds_the_post_diff_content_of_every_touched_file(tmp_path):
    repo = git_repo(tmp_path / "r")
    (repo / "app").mkdir()
    (repo / "app" / "a.py").write_text("A = 1\nB = 2\n")
    (repo / "app" / "gone.py").write_text("X = 1\n")
    ms.git(repo, "add", "-A")
    ms.git(repo, "commit", "-qm", "base")
    new = tmp_path / "wc"
    new.mkdir()
    ms.git(tmp_path, "clone", "-q", str(repo), str(new / "c"))
    c = new / "c"
    (c / "app" / "a.py").write_text("A = 1\nB = 3\n")
    (c / "app" / "gone.py").unlink()
    (c / "app" / "new.py").write_text("N = 1\n")
    ms.git(c, "add", "-A")
    diff = ms.git(c, "diff", "--cached", "--no-color")
    work = tmp_path / "w"
    work.mkdir()
    script = im.oracle_script(repo, ".", diff + "\n", work)
    assert script == {"rounds": [{"files": {
        "app/a.py": "A = 1\nB = 3\n", "app/gone.py": None, "app/new.py": "N = 1\n"}}]}  # fmt: skip
    assert sorted(work.iterdir()) == []  # the scratch copy is gone
    assert (repo / "app" / "a.py").read_text() == "A = 1\nB = 2\n"  # the repository was not touched
    with pytest.raises(Refusal) as e:
        im.oracle_script(
            repo, ".", diff.replace("B = 3", "B = 9").replace("-B = 2", "-B = 5") + "\n", work
        )
    assert e.value.code == "bad_inputs"
    with pytest.raises(Refusal) as e:
        im.oracle_script(repo, "sub", diff + "\n", work)
    assert e.value.code == "bad_inputs"  # a diff outside the project


def test_hunks_of_a_u0_diff_are_read_from_the_old_side():
    diff = (
        "diff --git a/api/api.go b/api/api.go\n--- a/api/api.go\n+++ b/api/api.go\n"
        "@@ -98 +98 @@\n-x\n+y\n@@ -120,3 +120,0 @@\n-a\n-b\n-c\n"
        "diff --git a/m.go b/m.go\n--- a/m.go\n+++ b/m.go\n@@ -0,0 +1,2 @@\n+a\n+b\n"
    )
    assert im._parse_hunks(diff) == {"api/api.go": [[98, 1], [120, 3]], "m.go": [[0, 0]]}


class FakeRebuilder:
    """A rebuild that runs for `ends` polls and costs `cost` USD; `stop` ends it as stopped."""

    def __init__(self, cost: float, ends: int = 3) -> None:
        self.cost, self.ends, self.stopped, self.calls = cost, ends, False, 0

    def get(self, rid: str, wait: float = 0.0) -> dict:
        self.calls += 1
        status = "stopped" if self.stopped else ("passed" if self.calls >= self.ends else "running")
        return {"status": status, "usage": {"cost_usd_list_estimate": self.cost}}

    def stop(self, rid: str) -> None:
        self.stopped = True


def test_a_usd_cap_stops_a_claude_build_that_passes_it_and_not_one_under_it(tmp_path):
    from lattice.studio import rebuild as rb

    cfg = rb.RebuildConfig(tmp_path, {"gates": ["true"]})

    def wait(fake: FakeRebuilder, agent: str, cap: float | None) -> tuple[dict, list[str]]:
        notes: list[str] = []
        opts = im.Options(project=tmp_path, impact=tmp_path, agent=agent, usd_cap=cap)
        return im._wait(fake, "r1", cfg, opts, notes, 0.0), notes

    over = FakeRebuilder(4.0)
    rec, notes = wait(over, "claude", 3.0)
    assert rec["status"] == "stopped" and over.stopped and "USD 3 cap" in notes[0]
    under = FakeRebuilder(1.0)
    rec, notes = wait(under, "claude", 3.0)
    assert rec["status"] == "passed" and not under.stopped and notes == []
    stub = FakeRebuilder(99.0)  # the cap is a claude-mode guard: a stub build is never capped
    rec, notes = wait(stub, "stub", 3.0)
    assert rec["status"] == "passed" and not stub.stopped


def test_the_claude_agent_is_refused_without_the_claude_binary(tmp_path):
    repo = ms.build_ms_repo(tmp_path / "repo", None)
    cand = ms.candidate_dir("status_changed")
    imp = tmp_path / "impact.json"
    imp.write_text(ir.report_json(ms.d_report(repo, cand, tmp_path / "shop")))
    opts = im.Options(project=repo, impact=imp, candidate=cand, flows=ms.FLOWS, agent="claude",
                      work=tmp_path / "w", claude_bin=str(tmp_path / "no-claude"))  # fmt: skip
    with pytest.raises(Refusal) as e:
        im.implement(opts)
    assert e.value.code == "no_rebuild" and "claude" in e.value.detail
    assert ms.worktrees(repo) == [str(repo)] or len(ms.worktrees(repo)) == 1
    with pytest.raises(Refusal) as e:
        im.implement(
            im.Options(project=repo, impact=imp, candidate=cand, flows=ms.FLOWS, agent="oracle")
        )
    assert e.value.code == "bad_inputs"  # oracle needs --ref-diff
