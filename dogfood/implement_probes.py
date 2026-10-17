"""Probes of `lattice spec implement` (docs/results/2026-10-17-implement-verify-prereg.md, s. 4).

The expectations below were written from the pre-registration before any output of the driver was
scored: the verdicts and counts of the table of section 3, the Go fixture's T3 files counted by
hand, the refusal codes of D2, the git guards of D3. A differing probe is a finding with its raw
observation; this runner never edits an expectation to make a probe pass.

Core stage (this container: no Streak checkout, no Docker daemon)::

    uv run python dogfood/implement_probes.py --stage core --scratch "$SCRATCH/p" \\
        --out docs/results/data/2026-10-17-implement-verify/core \\
        [--pytest-before before.txt --pytest-after after.txt]

The Streak arm (SO, SO3, SA, SL, SG) and PAg-MS are recorded NOT RUN with the reason; they are never
described as passed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import random
import re
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("IMPLEMENT_PROBES_REPO", HERE.parent)).resolve()
sys.path.insert(0, str(REPO))
BASE_COMMIT = "6a318e5"  # the pre-registration's base: the byte-compat reference

_spec = importlib.util.spec_from_file_location(
    "implement_ms", REPO / "tests/spec/fixtures/implement/ms.py"
)
ms = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["implement_ms"] = ms
_spec.loader.exec_module(ms)  # type: ignore[union-attr]

from lattice.spec import impact_report as ir  # noqa: E402
from lattice.spec import impact_tier4 as t4  # noqa: E402
from lattice.spec.implementation import route_key  # noqa: E402
from lattice.studio import implement as im  # noqa: E402

PY = sys.executable
ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
ABS_RE = re.compile(r"/home|/tmp|/Users|/private|/var/folders")
SHA40 = re.compile(r"\b[0-9a-f]{40}\b")

RESULTS: list[dict[str, Any]] = []


def result(pid: str, predicted: Any, observed: Any, passed: bool, **extra: Any) -> dict[str, Any]:
    row = {
        "id": pid,
        "status": "PASS" if passed else "FAIL",
        "predicted": predicted,
        "observed": observed,
        **extra,
    }
    RESULTS.append(row)
    print(f"{pid}: {row['status']}")
    return row


def not_run(pid: str, reason: str, predicted: Any = None) -> dict[str, Any]:
    row = {"id": pid, "status": "NOT RUN", "predicted": predicted, "reason": reason}
    RESULTS.append(row)
    print(f"{pid}: NOT RUN ({reason})")
    return row


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---- the shared runs ------------------------------------------------------------------------

PRED = {  # section 3: id -> (verdict, rounds, tiers["4"])
    "i1": ("pass", 1, "run"),
    "i2": ("pass", 2, "run"),
    "i3": ("fail", 1, "run"),
    "i4": ("fail", 1, "run"),
    "i5": ("nothing_to_build", None, "run"),
    "i6": ("nothing_to_build", None, "run"),
    "i7": ("nothing_to_build", None, "run"),
    "i8": ("not_built", 2, "not_run:rebuild_gave_up"),
    "i9": ("not_built", 2, "not_run:rebuild_gave_up"),
    "i11": ("pass", 1, "run"),
}


class World:
    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch
        self.cache: dict[str, Any] = {}
        self.runs: dict[str, Any] = {}

    def get(self, cid: str, tag: str = "", **kw: Any) -> Any:
        key = cid + tag
        if key not in self.runs:
            self.runs[key] = ms.run_change(self.scratch, cid, tag=tag, d_cache=self.cache, **kw)
        return self.runs[key]


def independent_before(world: World, cand_name: str) -> set[tuple[str, str, str]]:
    """The candidate spec plus the flows on the base app, run in this process by `check` itself
    (not through the driver): the set of (layer, rule, case) that fail."""
    from lattice.spec.check import check
    from tests.spec import minishop

    app_dir, package = minishop.write_app(world.scratch / "indep", "ok")
    side = ir.read_side(ms.candidate_dir(cand_name))
    flows = [f for f in ms.flows_doc()["flows"] if f["status"] == "verified"]
    rep = check(
        ir.with_flows(side.spec, flows),
        minishop.app_factory(package),
        minishop.build_db,
        layers="ACD",
        app_dir=app_dir,
        package=package,
        login=minishop.login,
    )
    own, _ = ir._split(rep, {f["id"] for f in flows})
    return {(f.layer, str(f.rule), str(f.case or "")) for f in own}


# ---- probes ---------------------------------------------------------------------------------


def probe_pv(w: World) -> None:
    obs: dict[str, Any] = {}
    ok = True
    for cid, (verdict, rounds, state) in PRED.items():
        r = w.get(cid)
        t = r.tier4
        row = {
            "verdict": t["verdict"],
            "tiers4": r.out.report["tiers"]["4"],
            "code": r.out.code,
            "rounds": (t["rebuild"] or {}).get("rounds"),
            "counts": t["counts"],
        }
        obs[cid] = row
        ok &= (t["verdict"], row["rounds"], row["tiers4"], r.out.code) == (
            verdict,
            rounds,
            state,
            0,
        )
    r10 = w.get("i10")
    obs["i10"] = {"refused": getattr(r10.refusal, "code", None), "out": r10.out is not None}
    ok &= r10.refusal is not None and r10.refusal.code == "spec_contradiction"
    # the counts the table names
    c1 = obs["i1"]["counts"]
    count_miss = []
    exp_counts = {
        "i1": {
            "resolved": 1,
            "flow_changed": 1,
            "unreported": 0,
            "t2_contradicted": 0,
            "regressions": 0,
        },
        "i2": {"resolved": 1, "flow_changed": 1, "unreported": 0},
        "i3": {"resolved": 1, "flow_changed": 2, "unreported": 1},
        "i4": {"resolved": 1, "flow_changed": 2, "unreported": 1},
        "i11": {"resolved": 1, "flow_changed": 1, "unreported": 0},
    }
    for cid, want in exp_counts.items():
        for k, v in want.items():
            if obs[cid]["counts"].get(k) != v:
                count_miss.append((cid, k, v, obs[cid]["counts"].get(k)))
    i1 = w.get("i1").tier4
    shape = (
        [i["target"] for i in i1["resolved"]] == ["scenario:ann cancels her pending order"]
        and [
            (i["flow"], i["reported_as"], i["spec_backed"])
            for i in i1["flows"]
            if i["kind"] == "flow_changed"
        ]
        == [("wf-cancel-twice", "t2_determined", True)]
        and i1["t2_confirmed"] is True
    )
    i3 = w.get("i3").tier4
    i4 = w.get("i4").tier4
    unrep = (
        [
            i["flow"]
            for i in i3["flows"]
            if i["kind"] == "flow_changed" and i.get("reported_as") == "unreported"
        ]
        == ["wf-list"]
        and [
            i["flow"]
            for i in i4["flows"]
            if i["kind"] == "flow_changed" and i.get("reported_as") == "unreported"
        ]
        == ["wf-read-ben"]
        and i3["rebuild"]["gates"][0]["exit_code"] == 0
        and i4["rebuild"]["gates"][0]["exit_code"] == 0
    )
    i11 = json.loads(json.dumps(w.get("i11").tier4))
    i1c = json.loads(json.dumps(i1))
    i11["rebuild"]["agent"] = i1c["rebuild"]["agent"] = "-"
    same_as_i1 = i11 == i1c
    ok_all = ok and shape and unrep and same_as_i1
    exact = sum(1 for k in PRED if obs[k]["verdict"] == PRED[k][0]) + (
        1 if obs["i10"]["refused"] == "spec_contradiction" else 0
    )
    result(
        "PV",
        "11/11 verdicts, rounds and counts as the table of section 3",
        {
            "changes": obs,
            "count_misses": count_miss,
            "i1_shape": shape,
            "i3_i4_unreported": unrep,
            "i11_same_as_i1_but_agent": same_as_i1,
        },
        ok_all and not count_miss,
        verdict_exact=f"{exact}/11",
    )
    del c1


def probe_pr(w: World) -> None:
    obs = {}
    ok = True
    for cid, cand in (
        ("i1", "status_changed"),
        ("i2", "status_changed"),
        ("i11", "status_changed"),
    ):
        t = w.get(cid).tier4
        truth = independent_before(w, cand)
        resolved = {(i["layer"], i["rule"], i["case"] or "") for i in t["resolved"]}
        row = {
            "resolved": sorted(resolved),
            "before_truth": sorted(truth),
            "regressions": len(t["regressions"]),
            "still_failing": len(t["still_failing"]),
            "failing_after": len(t["failing"]),
        }
        obs[cid] = row
        ok &= (
            resolved == truth
            and not t["regressions"]
            and not t["still_failing"]
            and not t["failing"]
        )
    result(
        "PR",
        "i1, i2, i11: resolved = the independent before set; 0 regressions, still failing, failing",
        obs,
        ok,
    )


def probe_pbf(w: World) -> None:
    obs = {}
    for cid in PRED:
        obs[cid] = w.get(cid).tier4["before"]["matches_report"]
    obs["i10"] = None
    applicable = [v for k, v in obs.items() if k != "i10"]
    result(
        "PBF",
        "11/11 true (i10 is refused before a before run exists: not applicable)",
        obs,
        all(v is True for v in applicable) and len(applicable) == 10,
        note="10/10 applicable; i10 has no tier 4 (refused by D2 before any run)",
    )


def probe_pu(w: World) -> None:
    obs = {}
    ok = True
    for cid, fid in (("i3", "wf-list"), ("i4", "wf-read-ben")):
        t = w.get(cid).tier4
        items = [
            i
            for i in t["flows"]
            if i["kind"] == "flow_changed" and i["reported_as"] == "unreported"
        ]
        obs[cid] = {"unreported": [i["flow"] for i in items], "verdict": t["verdict"]}
        ok &= [i["flow"] for i in items] == [fid] and t["verdict"] == "fail"
    result("PU", "i3, i4: 1 unreported each, verdict fail", obs, ok)


def probe_pt2(w: World) -> None:
    t = w.get("i1").tier4
    kinds = {c["kind"]: c["confirmed"] for c in t["t2_check"]}
    confirmed = (
        kinds == {"t2_flow_claim": True, "t2_scenario_claim": True} and t["t2_confirmed"] is True
    )

    def plant(rep: dict[str, Any]) -> None:
        rep["tier2"]["flows"] = [
            *rep["tier2"]["flows"],
            {
                "kind": "flow_fails_after_change",
                "target": "flow:wf-add",
                "status": "determined",
                "claim": "flow:wf-add fails at step 0 after the change",
                "tier": 2,
                "id": "t2-planted",
            },
        ]

    p = w.get("i1", "-t2", report_hook=plant)
    tp = p.tier4
    contradicted = [c for c in tp["t2_check"] if not c["confirmed"]]
    planted = (
        [c["target"] for c in contradicted] == ["flow:wf-add"]
        and tp["t2_confirmed"] is False
        and tp["counts"]["t2_contradicted"] == 1
        and tp["verdict"] == "fail"
    )
    result(
        "PT2",
        "i1: both T2 claims confirmed; a planted wf-add claim is t2_contradicted",
        {
            "i1": kinds,
            "planted": {
                "contradicted": [c["target"] for c in contradicted],
                "verdict": tp["verdict"],
            },
        },
        confirmed and planted,
    )


def probe_psb() -> None:
    fl = {
        "id": "wf",
        "name": "wf",
        "status": "verified",
        "steps": [
            {
                "i": 0,
                "kind": "request",
                "method": "POST",
                "path": "/orders/1/cancel",
                "caller": "ann",
                "json": None,
                "expect_status": 409,
            }
        ],
    }

    def f(status: int | None = 400) -> dict[str, Any]:
        return {
            "layer": "D",
            "rule": "scenario: flow_wf",
            "case": "flow_wf / step 1",
            "details": {"actual": {"status": status}} if status is not None else {},
        }

    def cs(
        k: int = 2,
        status: int = 400,
        path: str = "/orders/1/cancel",
        user: str | None = "ann",
        method: str = "POST",
        scenario: str = "sc",
    ) -> dict[str, Any]:
        return {
            "scenario": scenario,
            "k": k,
            "method": method,
            "path": path,
            "user": user,
            "expect_status": status,
        }

    def sb(finding: dict[str, Any], steps: list[dict[str, Any]], failing: set[Any] = frozenset()):  # type: ignore[assignment]
        return t4.spec_backed(fl, finding, steps, set(failing), route_key)[0]

    table = [
        ("all match", sb(f(), [cs()]), True),
        ("method differs", sb(f(), [cs(method="GET")]), False),
        ("route differs", sb(f(), [cs(path="/orders/1/items")]), False),
        ("user differs", sb(f(), [cs(user="ben")]), False),
        ("status differs", sb(f(), [cs(status=409)]), False),
        ("route template matches another id", sb(f(), [cs(path="/orders/7/cancel")]), True),
        ("details missing", sb(f(None), [cs()]), None),
        (
            "the backing step fails after",
            sb(f(), [cs()], {("D", "scenario: sc", "sc / step 2", "", "")}),
            False,
        ),
    ]
    got = [(n, g, w) for n, g, w in table]
    result(
        "PSB",
        "8/8",
        [{"case": n, "got": g, "want": w} for n, g, w in got],
        all(g == w for _, g, w in got),
    )


def probe_pt3e(scratch: Path) -> None:
    from lattice.spec.impact_tier3 import GoTree
    from tests.spec.test_impact_report import STATUS_EDIT, _gin_inventory, _side

    inv = _gin_inventory(scratch / "go")
    inp = ir.build_inputs(project=None, base_side=_side(), cand_side=_side(STATUS_EDIT))
    inp.inventory = inv
    rep = ir.build_report(inp, tiers="3")
    tree = GoTree(inv)
    # counted by hand from the T3 files of this edit: api/api.go is the only file listed
    cases = [
        (
            "one file in T3 (api/api.go line 98)",
            ["api/api.go"],
            {"api/api.go": [[98, 1]]},
            {"touched": ["api/api.go"], "recall": 1.0, "precision": 1.0, "missed": []},
        ),
        (
            "one file outside T3",
            ["models/models.go"],
            {"models/models.go": [[12, 1]]},
            {
                "touched": ["models/models.go"],
                "recall": 0.0,
                "precision": 0.0,
                "missed": ["models/models.go"],
            },
        ),
        (
            "a test file, the spec and the stub",
            ["api/api_test.go", "spec.py", "stub-rebuild.json"],
            {},
            {"touched": [], "recall": None, "precision": 0.0, "missed": []},
        ),
        (
            "a new file",
            ["api/new.go"],
            {},
            {"touched": ["api/new.go"], "recall": 0.0, "precision": 0.0, "missed": ["api/new.go"]},
        ),
    ]
    obs = []
    ok = True
    for name, names, hunks, want in cases:
        files = t4.edit_files(names, test_only=["stub-rebuild.json"])
        out = t4.t3_vs_edit(rep, {"files": files, "hunks": hunks}, tree=tree)["files"]
        got = {
            "touched": out["touched"],
            "recall": out["recall"],
            "precision": out["precision"],
            "missed": [m["file"] for m in out["missed"]],
        }
        obs.append({"case": name, "got": got, "want": want})
        ok &= got == want
    d1 = t4.t3_vs_edit(
        rep, {"files": ["api/api.go"], "hunks": {"api/api.go": [[98, 1]]}}, tree=tree
    )
    decl_want = {"touched": ["api/api.go:CreateProject"], "recall": 1.0, "precision": 0.2}
    decl = d1["declarations"]
    obs.append(
        {
            "case": "declarations of d1",
            "got": {k: decl[k] for k in ("touched", "recall", "precision")},
            "want": decl_want,
        }
    )
    ok &= {k: decl[k] for k in ("touched", "recall", "precision")} == decl_want
    py = t4.t3_vs_edit(rep, {"files": ["api/api.go"]}, tree=None)["declarations"]
    obs.append({"case": "python: no tree", "got": py, "want": "not_applicable: no extract"})
    ok &= py == "not_applicable: no extract"
    result("PT3E", "4 synthetic diffs exact vs counts by hand", obs, ok)


def probe_ph(w: World) -> None:
    rows = []

    def expect(name: str, code: str, tag: str, **kw: Any) -> None:
        r = w.get("i1", tag, **kw)
        got = getattr(r.refusal, "code", None)
        clean = (
            r.drift() == {"changed": [], "removed": [], "new": []}
            and r.worktrees_after == r.worktrees_before
        )
        rows.append({"case": name, "want": code, "got": got, "left_nothing": clean})

    def tier4_set(rep: dict) -> None:
        rep["tier4"] = {"verdict": "pass"}

    def base_byte(rep: dict) -> None:
        rep["input"]["base"]["spec"] = "0" * 64

    def cand_byte(rep: dict) -> None:
        rep["input"]["candidate"]["requirements"] = "0" * 64

    def inv_hash(rep: dict) -> None:
        rep["input"]["inventory"]["workflows_sha256"] = "0" * 64

    def stale(rep: dict) -> None:
        rep["input"]["inventory"]["stale"] = True

    def eid_of(repo: Path) -> str:
        rep = json.loads((repo.parent / "impact.json").read_text())
        return t4.eid_of(rep["input"]["base"], rep["input"]["candidate"])

    def branch(repo: Path) -> None:
        ms.git(repo, "branch", f"implement/{eid_of(repo)}")

    def protect(repo: Path) -> None:
        toml = repo / "studio.toml"
        toml.write_text(toml.read_text() + f'protected_branches = ["implement/{eid_of(repo)}"]\n')
        ms.git(repo, "commit", "-qam", "protect")

    def work_inside(opts: Any) -> None:
        opts.work = opts.project / "work"

    def out_inside(opts: Any) -> None:
        opts.out = opts.project / "report.json"

    expect("bad_report (tier4 already set)", "bad_report", "-h1", report_hook=tier4_set)
    expect("report_mismatch (base byte)", "report_mismatch", "-h2", report_hook=base_byte)
    expect("report_mismatch (candidate byte)", "report_mismatch", "-h3", report_hook=cand_byte)
    expect("report_mismatch (inventory hash)", "report_mismatch", "-h4", report_hook=inv_hash)
    expect("stale_inventory", "stale_inventory", "-h5", report_hook=stale)
    r10 = w.get("i10")
    rows.append(
        {
            "case": "spec_contradiction",
            "want": "spec_contradiction",
            "got": getattr(r10.refusal, "code", None),
            "left_nothing": r10.worktrees_after == r10.worktrees_before,
        }
    )
    expect("branch_exists", "branch_exists", "-h6", repo_hook=branch)
    expect("protected branch", "branch_protected", "-h7", repo_hook=protect)
    expect("--work inside the project", "bad_paths", "-h8", opts_hook=work_inside)
    expect("--out inside the project", "bad_paths", "-h9", opts_hook=out_inside)
    ok = all(r["want"] == r["got"] and r["left_nothing"] for r in rows)
    result("PH", "10/10 codes", rows, ok and len(rows) == 10)


def processes_naming(path: Path) -> list[str]:
    out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout
    return [ln for ln in out.splitlines() if str(path) in ln and "ps -eo" not in ln]


def probe_pg(w: World) -> None:
    # extra runs: a failing-gate run (i9 again), a raise mid after-run, a tripped guard is PH/test
    extra = {"i9-fg": w.get("i9", "-fg")}

    real: dict[str, Any] = {}

    def raising(worktree: Path, flows: Any, which: str) -> dict:
        if which == "after":
            raise RuntimeError("the app would not start")
        real.setdefault("r", im.subprocess_side_runner(Path(worktree), "."))
        return real["r"](worktree, flows, which)

    extra["i1-raise"] = w.get("i1", "-raise", side_runner=raising)
    rows = {}
    ok = True
    for key, r in {**{c: w.get(c) for c in PRED}, "i10": w.get("i10"), **extra}.items():
        d = r.drift()
        untouched = r.out is None or r.tier4["owner"]["untouched"]
        rows[key] = {
            "drift": d,
            "owner_untouched": untouched,
            "head_same": ms.git(r.repo, "rev-parse", "HEAD") == r.head,
        }
        ok &= (
            d == {"changed": [], "removed": [], "new": []} and untouched and rows[key]["head_same"]
        )
    raised = extra["i1-raise"]
    rows["i1-raise"]["exit_code"] = raised.out.code
    ok &= raised.out.code == 3 and raised.out.report["tiers"]["4"].startswith("not_run:error")
    result(
        "PG",
        "0 changed, 0 new beyond the allowed list (11 runs, a failing-gate run, a raise mid-run)",
        rows,
        ok,
    )


def probe_pnm(w: World) -> None:
    rows = {}
    ok = True
    # i1-ff: the stub script is tracked in the owner's HEAD, so nothing untracked blocks a merge
    runs = {c: w.get(c) for c in ("i1", "i2", "i3", "i4", "i11")}
    runs["i1-ff"] = w.get("i1", "-ff", repo_hook=ms.commit_stub)
    for cid, r in runs.items():
        g = ms.git
        spec_c, build_c = (
            g(r.repo, "rev-parse", f"implement/{r.eid}"),
            g(r.repo, "rev-parse", f"rebuild/{r.eid}"),
        )
        anc = subprocess.run(
            ["git", "-C", str(r.repo), "merge-base", "--is-ancestor", build_c, r.head],
            capture_output=True,
        ).returncode
        row = {
            "b_ancestor_of_head": anc == 0,
            "head_unchanged": g(r.repo, "rev-parse", "HEAD") == r.head,
            "build_parent_is_S": g(r.repo, "rev-parse", f"{build_c}^") == spec_c,
            "one_new_commit": g(r.repo, "rev-list", "--count", f"{spec_c}..{build_c}") == "1",
            "S_parent_is_head": g(r.repo, "rev-parse", f"{spec_c}^") == r.head,
            "branch_unmoved": g(r.repo, "symbolic-ref", "--short", "HEAD") == "main",
        }
        rows[cid] = row
        ok &= (not row["b_ancestor_of_head"]) and all(
            v for k, v in row.items() if k != "b_ancestor_of_head"
        )
    result(
        "PNM",
        "B not an ancestor of HEAD, HEAD unchanged, rebuild/<eid> = S + 1 commit, S^ = HEAD",
        rows,
        ok,
    )


def probe_psc(w: World) -> None:
    def untracked(repo: Path) -> None:
        (repo / "owner-notes.txt").write_text("untracked, not for the spec commit\n")
        (repo / "shop" / "scratch.py").write_text("X = 1\n")

    rows = {}
    ok = True
    spec = {"REQUIREMENTS.md", "spec.py", "spec.trace.json"}
    for key, r in (
        ("i1", w.get("i1")),
        ("i11", w.get("i11")),
        ("i3", w.get("i3")),
        ("i1-untracked", w.get("i1", "-ut", repo_hook=untracked)),
        ("i5", w.get("i5")),
    ):
        sha_ = ms.git(r.repo, "rev-parse", f"implement/{r.eid}")
        names = set(ms.git(r.repo, "diff", "--name-only", f"{sha_}^", sha_).split())
        body = ms.git(r.repo, "log", "-1", "--format=%B", sha_)
        row = {
            "files": sorted(names),
            "extra": sorted(names - spec - {"stub-rebuild.json"}),
            "trailer": f"Lattice-Implement: {r.eid}" in body,
            "has_stub": "stub-rebuild.json" in names,
        }
        rows[key] = row
        ok &= not row["extra"] and row["trailer"] and names <= spec | {"stub-rebuild.json"}
    result(
        "PSC",
        "S changes exactly the 3 spec files (+ stub-rebuild.json in test modes), trailer present",
        rows,
        ok,
    )


def probe_pc(w: World) -> None:
    rows = {}
    ok = True

    def defwork(opts: Any) -> None:
        opts.work = None

    runs = {c: w.get(c) for c in PRED}
    runs["i10"] = w.get("i10")
    runs["i1-defwork"] = w.get("i1", "-dw", opts_hook=defwork)
    for key, r in runs.items():
        built = r.out is not None and (r.tier4["rebuild"] is not None)
        want_kept = 1 if built else 0
        kept = len(r.review_worktrees())
        tmp_left = sorted(p.name for p in r.tmp.iterdir())
        work_left = sorted(p.name for p in r.work.iterdir()) if r.work.exists() else []
        procs = processes_naming(r.repo.parent)
        row = {
            "kept_review_worktrees": kept,
            "want": want_kept,
            "tmp_left": [n.split("-")[0] + "-" + n.split("-")[1] for n in tmp_left],
            "tmp_entries": len(tmp_left),
            "work_left": work_left,
            "live_processes": len(procs),
        }
        rows[key] = row
        ok &= kept == want_kept and len(tmp_left) == want_kept and not work_left and not procs
    result(
        "PC",
        "worktrees after = before + the kept review worktree or + 0; no leftover dir or process",
        rows,
        ok,
    )


def scan(text: str, spec_tree: str | None, build_tree: str | None, shas: list[str]) -> list[str]:
    bad = []
    t = text
    for tree in (spec_tree, build_tree):
        if tree:
            t = t.replace(tree, "")
    if ABS_RE.search(t):
        bad.append("absolute path")
    if ISO_RE.search(t):
        bad.append("ISO time")
    if SHA40.search(t):
        bad.append("40-hex string outside spec_tree/build_tree")
    for s in shas:
        if s and s in text:
            bad.append(f"commit sha {s[:10]}")
    return bad


def probe_pd(w: World, scratch: Path) -> None:
    rows = {}
    ok = True
    for cid in ("i1", "i3", "i11"):
        c = ms.CHANGES[cid]
        cand = ms.candidate_dir(c["candidate"])
        outputs = []
        scans: list[str] = []
        for n in range(10):
            root = (
                scratch / f"pd-{cid}-{n % 2}-{n}"
            )  # two scratch roots (n % 2), a fresh tree each time
            repo = ms.build_ms_repo(root / "repo", c["stub"])
            rep = w.cache.get(c["candidate"]) or ms.d_report(repo, cand, root / "shop")
            w.cache[c["candidate"]] = rep
            imp = root / "impact.json"
            imp.write_text(ir.report_json(rep))
            env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
            env["PYTHONHASHSEED"] = str(random.randrange(1, 2**31))
            env["TMPDIR"] = str(root / "tmp")
            (root / "tmp").mkdir()
            agent = c.get("agent") or "stub"
            argv = [
                PY,
                "-m",
                "lattice.spec",
                "implement",
                "--project",
                str(repo),
                "--impact",
                str(imp),
                "--candidate",
                str(cand),
                "--flows",
                str(ms.FLOWS),
                "--agent",
                agent,
                "--max-rounds",
                str(c["max_rounds"]),
                "--json",
                "--work",
                str(root / "work"),
            ]
            if agent == "oracle":
                argv += ["--ref-diff", str(ms.E_OK_DIFF)]
            p = subprocess.run(argv, capture_output=True, text=True, env=env, cwd=str(repo))
            outputs.append((p.returncode, p.stdout))
            if p.returncode == 0:
                doc = json.loads(p.stdout)
                impl = doc["tier4"]["implementation"] or {}
                shas = [ms.git(repo, "rev-parse", "HEAD")]
                for b in ("implement", "rebuild"):
                    shas.append(
                        subprocess.run(
                            [
                                "git",
                                "-C",
                                str(repo),
                                "rev-parse",
                                f"{b}/{doc['tier4']['implementation']['branch_spec'].split('/')[1]}",
                            ],
                            capture_output=True,
                            text=True,
                        ).stdout.strip()
                    )
                scans += scan(p.stdout, impl.get("spec_tree"), impl.get("build_tree"), shas)
            for wt in ms.worktrees(repo)[1:]:
                subprocess.run(
                    ["git", "-C", str(repo), "worktree", "remove", "--force", wt],
                    capture_output=True,
                )
        first = outputs[0]
        same = all(o == first for o in outputs)
        rows[cid] = {
            "identical": sum(o == first for o in outputs),
            "of": len(outputs),
            "exit_codes": sorted({o[0] for o in outputs}),
            "bytes": len(first[1]),
            "scan": sorted(set(scans)),
        }
        ok &= same and first[0] == 0 and not scans
    result(
        "PD",
        "10/10 identical for i1, i3, i11 (random PYTHONHASHSEED, two roots); no path, time or sha",
        rows,
        ok,
    )


def probe_pm(w: World, scratch: Path) -> None:
    shim = scratch / "pm-bin"
    shim.mkdir(parents=True, exist_ok=True)
    log = scratch / "pm-claude.log"
    envlog = scratch / "pm-env.log"
    (shim / "claude").write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
    (shim / "claude").chmod(0o755)
    old = {k: os.environ.get(k) for k in ("PATH", "ANTHROPIC_API_KEY", im.PROBE_ENVLOG)}
    os.environ["PATH"] = f"{shim}:{os.environ['PATH']}"
    os.environ["ANTHROPIC_API_KEY"] = "sk-probe-not-a-real-key"
    os.environ[im.PROBE_ENVLOG] = str(envlog)
    try:
        runs = [
            ms.run_change(scratch, cid, tag="-pm", d_cache=w.cache) for cid in ("i1", "i11", "i5")
        ]
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    for r in runs:
        r.cleanup()
    calls = len(log.read_text().splitlines()) if log.exists() else 0
    entries = [json.loads(x) for x in envlog.read_text().splitlines()]
    result(
        "PM",
        "0 claude calls in stub and oracle mode; no ANTHROPIC key in any child environment",
        {
            "claude_calls": calls,
            "children_seen": len(entries),
            "kinds": sorted({e["who"] for e in entries}),
            "with_key": sum(1 for e in entries if e["key"]),
        },
        calls == 0
        and entries
        and not any(e["key"] for e in entries)
        and {e["who"] for e in entries} == {"gate", "side"},
    )


def probe_px(w: World) -> None:
    secs = {c: round(w.get(c).seconds, 2) for c in PRED}
    result(
        "PX",
        "<= 30 s per MS change (p: 2-8 s)",
        secs,
        max(secs.values()) <= 30.0,
        max_s=max(secs.values()),
        total_s=round(sum(secs.values()), 1),
    )


# ---- byte compatibility (PBC) --------------------------------------------------------------

FROZEN = """\
ddb005b594e20acbf61fd228d15b73d7b5c331d6eba392999509d0b0de3a7cb7  lattice/studio/rebuild.py
e4b11ec2d0a3082f6e5d9325e6a2d44cb72a55c9838510075572cbc7d1d72522  lattice/studio/areas.py
01a8df5522e5498d02c64c7e07a204bfa09149086fedcd67a9329c36edb515c1  lattice/studio/server.py
428d8d2321afb90209e76af9686ecd720d8cb9a67f8e5bd17df2bff960088ad0  lattice/studio/state.py
44ea3dad50791af04d04264481a768fc03a9331e1f9f98dfc9aa5a4e6ac3b2c6  lattice/studio/session.py
a3e11f1c266a813a66e33d8943b8773705a419ce8dde806615203d750ae0137c  lattice/spec/impact_report.py
6f86fc6cfb2b7f2a6e485633d810e55782fdfbcfdce71921bff8f9cff560a90a  lattice/spec/impact_tier2.py
22228513beba44fcff4b0d5ab48c662d2010e7ac320b73ac860f134ffb6e2faf  lattice/spec/impact_tier3.py
68f13b288cde4a3fa348412495ba560815e003b342ea9cca90b1a779c1bb525e  lattice/spec/check.py
29ded6ecbbddcc55a5d94914cf9bce82fb9ee61a10d3453d70c336565c6090e6  lattice/spec/incremental.py
7012db6e907e9b853402ed6a433b6690e64fd7b82df37ec96ccd0df647a6d2e6  lattice/spec/implementation.py
7c0833aa945e2d0a2596e53881c37484001a7ee9592dc5d317efc7b03523423c  lattice/mcp/tools.py
701f17f333ec918ffadf79904b2c9926a9026a1a1f34c6402ea2083d4172c796  lattice/spec/appmap.py
c6f43564156741afefdb8ac143d658b0ddb0e615d7caa0ca21726772b408797d  lattice/spec/impact_ask.py
90117594e10c61272b8896bfc631623c5d23c4689d8e1853649cf73810baccfd  tests/spec/minishop.py
"""

BC_IMPACT = r"""
import sys, tempfile
from pathlib import Path
from lattice.spec import impact_report as ir
from tests.spec.test_impact_report import STATUS_EDIT, _gin_inventory, _side
FIX = Path("tests/spec/fixtures/impact")
for name in ("reword", "status_changed", "new_request", "bad_invariant", "removed_rule",
             "conflict"):
    inp = ir.build_inputs(project=FIX / "base", candidate=FIX / name, inventory=None)
    sys.stdout.write(name + "\n" + ir.report_json(ir.build_report(inp, tiers="2", baseline="none")))
inp = ir.build_inputs(project=FIX / "base", candidate=FIX / "base", inventory=None)
sys.stdout.write("base\n" + ir.report_json(ir.build_report(inp, tiers="2", baseline="none")))
inv = _gin_inventory(Path(tempfile.mkdtemp()))
inp = ir.build_inputs(project=None, base_side=_side(), cand_side=_side(STATUS_EDIT))
inp.inventory = inv
sys.stdout.write("go\n" + ir.report_json(ir.build_report(inp, tiers="23")))
"""

BC_MCP = r"""
import json, sys, tempfile
from pathlib import Path
from lattice.mcp.server import INSTRUCTIONS
from lattice.mcp.tools import Toolbox
from tests.mcp.test_change_impact import with_impact
from tests.mcp.test_implementation_tools import FakeBackend, make_project
root = Path(tempfile.mkdtemp())
both, _, _ = make_project(root / "both"); with_impact(both)
impl, _, _ = make_project(root / "impl")
plain, _, _ = make_project(root / "plain", implementation=False)
sys.stdout.write(INSTRUCTIONS + "\n")
for p in (plain, impl, both):
    sys.stdout.write(json.dumps(Toolbox(FakeBackend(p)).list_tools(), sort_keys=True) + "\n")
"""


def run_tree(
    tree: Path, code: str, cwd: Path | None = None, args: list[str] | None = None
) -> tuple[int, bytes]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
    env.update(PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", UV_NO_SYNC="1", COLUMNS="100")
    p = subprocess.run(
        [PY, "-c", code, *(args or [])], cwd=str(cwd or tree), env=env, capture_output=True
    )
    return p.returncode, p.stdout + b"\x00" + p.stderr


def probe_pbc(
    w: World, scratch: Path, before_ids: Path | None, after_ids: Path | None, studio: bool
) -> None:
    base = scratch / "base-tree"
    arc = subprocess.run(
        ["git", "-C", str(REPO), "archive", BASE_COMMIT], capture_output=True, check=True
    ).stdout
    base.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(arc)) as tf:
        tf.extractall(base, filter="data")
    rows: dict[str, Any] = {}
    ok = True

    def same(name: str, code: str, cwd_rel: str = ".", args: list[str] | None = None) -> None:
        nonlocal ok
        a = run_tree(REPO, code, REPO / cwd_rel, args)
        b = run_tree(base, code, base / cwd_rel, args)
        rows[name] = {"identical": a == b, "bytes": len(a[1]), "exit": a[0]}
        ok &= a == b and a[0] in (0, 2) and len(a[1]) > 1

    cli = "import sys; from lattice.cli import main; sys.exit(main(sys.argv[1:]))"
    spec = "import sys; from lattice.spec.__main__ import main; sys.exit(main(sys.argv[1:]))"
    same("lattice --help", cli, args=["--help"])
    same("lattice spec (usage)", cli, args=["spec"])
    same("python -m lattice.spec check --help", spec, args=["check", "--help"])
    same("lattice spec impact --help", cli, args=["spec", "impact", "--help"])
    same("lattice spec impact (7 fixtures tiers 2 + the Go fixture tiers 2,3)", BC_IMPACT)
    same("MCP instructions and tools/list (plain, implementation, impact configs)", BC_MCP)
    # the frozen files: sha256 at the pre-registration, in the working tree
    frozen = {}
    for ln in FROZEN.strip().splitlines():
        want, rel = ln.split()
        frozen[rel] = sha((REPO / rel).read_bytes()) == want
    rows["frozen sha256 (prereg section 9)"] = {
        "all_equal": all(frozen.values()),
        "differ": [k for k, v in frozen.items() if not v],
    }
    ok &= all(frozen.values())
    never = [
        "lattice/studio/rebuild.py",
        "lattice/studio/areas.py",
        "lattice/studio/server.py",
        "lattice/studio/session.py",
        "lattice/studio/state.py",
        "lattice/spec/impact_report.py",
        "lattice/spec/impact_tier2.py",
        "lattice/spec/impact_tier3.py",
        "lattice/spec/implementation.py",
        "lattice/spec/check.py",
        "lattice/spec/incremental.py",
        "lattice/mcp/tools.py",
    ]
    diff = subprocess.run(
        ["git", "-C", str(REPO), "diff", BASE_COMMIT, "--", *never], capture_output=True, text=True
    ).stdout
    rows["empty diff of the D8 'Never' files vs " + BASE_COMMIT] = {"empty": diff == ""}
    ok &= diff == ""
    # the one additive edit
    ad = subprocess.run(
        ["git", "-C", str(REPO), "diff", BASE_COMMIT, "--", "lattice/spec/__main__.py"],
        capture_output=True,
        text=True,
    ).stdout
    added = [
        ln
        for ln in ad.splitlines()
        if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
    ]
    rows["lattice/spec/__main__.py: one additive line"] = {"changed_lines": added}
    ok &= added == ['+    "implement": "lattice.studio.implement",']
    # the output report minus tiers["4"] and tier4 is byte-equal to the input on every core run
    same_runs = {}
    for cid in list(PRED):
        r = w.get(cid)

        def strip(rep: dict[str, Any]) -> str:
            d = {k: v for k, v in rep.items() if k != "tier4"}
            d["tiers"] = {k: v for k, v in rep["tiers"].items() if k != "4"}
            return ir.report_json(d)

        same_runs[cid] = strip(r.out.report) == strip(r.report_in)
    rows["output minus tiers[4] and tier4 == input (core runs)"] = same_runs
    ok &= all(same_runs.values())
    # the Studio server as a black box: its 62 scenarios (stub rebuilds, routes, /api/model)
    if studio:
        out = {}
        for label, tree in (("current", REPO), ("base", base)):
            env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
            env.update(PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", UV_NO_SYNC="1")
            p = subprocess.run(
                [
                    PY,
                    "-m",
                    "lattice.spec",
                    "check",
                    "--spec",
                    "spec:SPEC",
                    "--app",
                    "harness:make_app",
                    "--db",
                    "harness:build_db",
                    "--runtime",
                    "harness:RUNTIME",
                    "--layers",
                    "DX",
                ],
                cwd=str(tree / "dogfood/studio"),
                env=env,
                capture_output=True,
                text=True,
            )
            lines = [
                ln
                for ln in (p.stdout + p.stderr).splitlines()
                if ln.startswith(("scenarios:", "PASS", "FAIL", "verdict"))
            ]
            out[label] = {
                "exit": p.returncode,
                "lines": [re.sub(r"\d+\.\d+s?", "N", ln) for ln in lines],
            }
        rows["dogfood/studio (check.sh's command) on both trees"] = out
        ok &= (
            out["current"]["exit"] == out["base"]["exit"] == 0
            and out["current"]["lines"] == out["base"]["lines"]
        )
    else:
        rows["dogfood/studio"] = "skipped by --no-studio"
    if before_ids and after_ids and before_ids.exists() and after_ids.exists():
        b, a = (set(p.read_text().split("\n")) - {""} for p in (before_ids, after_ids))
        rows["pytest (spec, mcp, studio, importer, gocheck): same failing ids"] = {
            "before": len(b),
            "after": len(a),
            "new_failures": sorted(a - b),
            "fixed": sorted(b - a),
        }
        ok &= a <= b
    else:
        rows["pytest same failing ids"] = (
            "not scored here (run after the edits; see the results doc)"
        )
    result("PBC", "all identical / empty / equal", rows, ok)


# ---- NOT RUN rows --------------------------------------------------------------------------


def not_run_rows() -> None:
    no_streak = "no Streak checkout and no Docker daemon here (amendment 1, Q4: orchestrator step)"
    not_run(
        "PAg-MS",
        "the USD 3.00 real-agent budget is not approved (amendment 1); no model run",
    )
    for pid in ("SO", "SO3", "SA", "SL", "SG"):
        not_run(pid, no_streak)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stage", choices=("core",), default="core")
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="", help="comma list of probe ids")
    ap.add_argument("--pytest-before", type=Path)
    ap.add_argument("--pytest-after", type=Path)
    ap.add_argument("--no-studio", action="store_true")
    args = ap.parse_args(argv)
    scratch = Path(args.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    only = {x for x in args.only.split(",") if x}
    w = World(scratch)

    def want(pid: str) -> bool:
        return not only or pid in only

    t0 = time.perf_counter()
    steps = [
        ("PV", lambda: probe_pv(w)),
        ("PR", lambda: probe_pr(w)),
        ("PBF", lambda: probe_pbf(w)),
        ("PU", lambda: probe_pu(w)),
        ("PT2", lambda: probe_pt2(w)),
        ("PSB", probe_psb),
        ("PT3E", lambda: probe_pt3e(scratch)),
        ("PH", lambda: probe_ph(w)),
        ("PG", lambda: probe_pg(w)),
        ("PNM", lambda: probe_pnm(w)),
        ("PSC", lambda: probe_psc(w)),
        ("PC", lambda: probe_pc(w)),
        ("PX", lambda: probe_px(w)),
        ("PM", lambda: probe_pm(w, scratch)),
        ("PD", lambda: probe_pd(w, scratch)),
        (
            "PBC",
            lambda: probe_pbc(
                w, scratch, args.pytest_before, args.pytest_after, not args.no_studio
            ),
        ),
    ]
    try:
        for pid, fn in steps:
            if want(pid):
                fn()
        if not only:
            not_run_rows()
    finally:
        for r in w.runs.values():
            r.cleanup()
    (out / "probes.json").write_text(
        json.dumps(RESULTS, indent=1, sort_keys=True, default=str) + "\n"
    )
    # the per-change reports (canonical, as the driver wrote them)
    runs = out / "runs"
    runs.mkdir(exist_ok=True)
    for key, r in w.runs.items():
        if r.out is not None:
            (runs / f"{key}.json").write_text(ir.report_json(r.out.report))
    counts = {s: sum(1 for x in RESULTS if x["status"] == s) for s in ("PASS", "FAIL", "NOT RUN")}
    print(f"{counts} in {time.perf_counter() - t0:.0f}s")
    return 0 if not counts["FAIL"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
