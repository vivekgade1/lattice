"""The mutation run of API v1.11 (build performance in Studio), pre-registered in
``docs/results/2026-10-11-perf-studio-prereg.md``.

For each mutant: ``lattice/``, ``tests/`` and ``dogfood/studio/project_seed/`` are copied to a
scratch directory, ONE patch is applied to the copy (its old text must occur exactly once),
then (a) the new suites ``tests/studio/test_scoped_gates.py``, ``test_level_repair.py`` and
``test_stop_cleanup.py`` run there with the copy first on ``PYTHONPATH``, and (b) the dogfood
check ``dogfood/studio/check.sh`` runs from the working tree with
``LATTICE_STUDIO_PYTHONPATH=<copy>`` (the served Studio imports the copy; the checker is the
working tree's). A mutant is caught when either fails. The "earlier" mutants (the build-from-
spec and build-graph runs' patches that touch code this phase changed) run against the
dogfood only, as they did then. A control run of the unpatched copy comes first. The working
tree is never patched. ``--jobs N`` runs N mutants at a time (default 3).

Usage: ``uv run python dogfood/studio/perf_mutants.py [--out DIR] [--jobs N] [ID ...]``.
Exit 0 when the control passes and every mutant is caught.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE = ["tests/studio/test_scoped_gates.py", "tests/studio/test_level_repair.py",
         "tests/studio/test_stop_cleanup.py"]  # fmt: skip
FILES = ["lattice/studio/plan.py", "lattice/studio/rebuild.py", "lattice/studio/state.py",
         "lattice/studio/areas.py", "lattice/studio/hub.py", "lattice/studio/server.py",
         "dogfood/studio/spec.py", *SUITE]  # fmt: skip

# id -> (suites: "both" | "dogfood", file, behaviour, old text, new text)
MUTANTS: dict[str, tuple[str, str, str, str, str]] = {
    "P-M1": (
        "both",
        "lattice/studio/plan.py",
        "level-end gate skipped when the node gate passed",
        "            gates, counts, findings = rb.gate_round(irun, L + 1)\n",
        '            gates, counts, findings = [{"command": "skipped", "exit_code": 0, '
        '"seconds": 0.0}], {}, []  # MUTANT P-M1\n',
    ),
    "P-M2": (
        "both",
        "lattice/studio/plan.py",
        "the node gate result is accepted as the level result",
        '            failing = set(doc["failing"])\n',
        "            failing = set(last_failing) - {t for n in merged for t in "
        "self._build_targets(n)}  # MUTANT P-M2\n",
    ),
    "P-M3": (
        "both",
        "lattice/studio/rebuild.py",
        "the scoped selection leaves out the regression set",
        "    chosen = list(dict.fromkeys([*scope, *reg]))\n",
        "    chosen = list(dict.fromkeys(scope))  # MUTANT P-M3\n",
    ),
    "P-M4": (
        "both",
        "lattice/studio/plan.py",
        "a failing level gate is accepted without repair",
        "            if bad or broken:\n",
        "            if broken:  # MUTANT P-M4\n",
    ),
    "P-M5": (
        "both",
        "lattice/studio/plan.py",
        "repair attempts are not capped (3 more than configured)",
        "            if tries >= cap:\n",
        "            if tries >= cap + 3:  # MUTANT P-M5\n",
    ),
    "P-M6": (
        "both",
        "lattice/studio/rebuild.py",
        "stop leaves the gate's app process running (only the gate's own group is killed)",
        "                    terminate_tree(proc, list(children.values()), "
        "grace=self.cfg.stop_grace,\n",
        "                    terminate_tree(proc, [], grace=self.cfg.stop_grace,  # MUTANT P-M6\n",
    ),
    # earlier mutants whose code this phase touched (dogfood only, as they ran then)
    "B-M1": (
        "dogfood",
        "lattice/studio/rebuild.py",
        "a scoped build passes while an in-scope target still fails",
        "        still = [t for t in target_ids if t in cur]\n",
        "        still = []  # MUTANT B-M1\n",
    ),
    "B-M2": (
        "dogfood",
        "lattice/studio/rebuild.py",
        "regressions outside the scope are ignored",
        "        regressions = [i for i in cur if i not in run.baseline_ids]\n",
        "        regressions = []  # MUTANT B-M2\n",
    ),
    "B-M5": (
        "dogfood",
        "lattice/studio/rebuild.py",
        "the target branch checked out in the owner's worktree is written without confirmation",
        "        elif name == current:\n",
        "        elif False:  # MUTANT B-M5\n",
    ),
    "R-M2": (
        "dogfood",
        "lattice/studio/rebuild.py",
        "a rebuild merges into the owner's current branch",
        '            sha = _git_ok(wt, "rev-parse", "HEAD")\n',
        '            sha = _git_ok(wt, "rev-parse", "HEAD")\n'
        '            _git_ok(run.repo, "merge", "-q", "--no-edit", sha)  # MUTANT R-M2\n',
    ),
    "BG-P-M3": (
        "dogfood",
        "lattice/studio/plan.py",
        "the level merge skips the post-merge gate (build-graph P-M3)",
        "            gates, counts, findings = rb.gate_round(irun, L + 1)\n",
        "            gates, counts, findings = [], {}, []  # MUTANT BG-P-M3\n",
    ),
    "BG-P-M6": (
        "dogfood",
        "lattice/studio/plan.py",
        "the target is advanced to a merged tip whose gate failed (build-graph P-M6, adapted)",
        '                return {"failed": True, "repairable": not broken and bool(bad),\n',
        '                _git(rb.repo(), "update-ref", '
        "f\"refs/heads/{plan.record['target_branch']}\", tip)  # MUTANT BG-P-M6\n"
        '                return {"failed": True, "repairable": not broken and bool(bad),\n',
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def copy_tree(dst: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".ruff_cache")
    shutil.copytree(ROOT / "lattice", dst / "lattice", ignore=ignore)
    shutil.copytree(ROOT / "tests", dst / "tests", ignore=ignore)
    shutil.copytree(ROOT / "dogfood" / "studio" / "project_seed",
                    dst / "dogfood" / "studio" / "project_seed", ignore=ignore)  # fmt: skip


def apply(dst: Path, mid: str) -> None:
    _suites, rel, _what, old, new = MUTANTS[mid]
    path = dst / rel
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{mid}: the patched text occurs {n} times in {rel} (must be once)")
    path.write_text(text.replace(old, new), encoding="utf-8")


def _summary(out: str) -> str:
    return next((ln for ln in reversed(out.splitlines())
                 if re.search(r"\d+ (passed|failed)|findings", ln)), "").strip()  # fmt: skip


def run_pytest(dst: Path) -> dict[str, object]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env["PYTHONPATH"] = str(dst)
    t0 = time.monotonic()
    try:
        r = subprocess.run(
            ["uv", "run", "--project", str(ROOT), "pytest", *SUITE, "-q", "-p",
             "no:cacheprovider", "-rf", "--no-header"],
            cwd=str(dst), env=env, capture_output=True, text=True, timeout=900,
        )  # fmt: skip
        out, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = str(e.stdout or "") + "\n[timeout after 900s]", 124
    failed = sorted({m.group(1) for m in re.finditer(r"^FAILED (\S+)", out, re.M)})
    return {"exit": rc, "failed": failed, "summary": _summary(out),
            "seconds": round(time.monotonic() - t0, 1), "tail": out[-2500:]}  # fmt: skip


def run_dogfood(dst: Path) -> dict[str, object]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env["LATTICE_STUDIO_PYTHONPATH"] = str(dst)
    t0 = time.monotonic()
    try:
        r = subprocess.run(["./check.sh"], cwd=str(ROOT / "dogfood" / "studio"), env=env,
                           capture_output=True, text=True, timeout=1200)  # fmt: skip
        out, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = str(e.stdout or "") + "\n[timeout after 1200s]", 124
    first = [m.group(1) for m in re.finditer(r"^\s+\[D \w+\] .*\((\w+ / step \d+)\)", out, re.M)]
    return {"exit": rc, "first_failures": first[:12], "summary": out.splitlines()[0] if out else "",
            "seconds": round(time.monotonic() - t0, 1), "tail": out[:2500]}  # fmt: skip


def one(mid: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"lattice-perf-mutant-{mid}-") as d:
        dst = Path(d)
        copy_tree(dst)
        if mid != "control":
            apply(dst, mid)
        suites = "both" if mid == "control" else MUTANTS[mid][0]
        res: dict[str, object] = {}
        if suites == "both":
            res["pytest"] = run_pytest(dst)
        res["dogfood"] = run_dogfood(dst)
    caught_by = [k for k, v in res.items() if v["exit"] != 0]  # type: ignore[index]
    if mid == "control":
        verdict = "PASS (control)" if not caught_by else "CONTROL FAILED"
    else:
        verdict = "caught" if caught_by else "SURVIVED"
    return {**res, "caught_by": caught_by, "verdict": verdict}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", default=list(MUTANTS))
    ap.add_argument("--out", type=Path, default=None, help="write results.json here")
    ap.add_argument("--jobs", type=int, default=3)
    args = ap.parse_args(argv)
    hashes = {f: sha(ROOT / f) for f in FILES}
    print("hashes:", json.dumps(hashes), flush=True)
    results: dict[str, object] = {"hashes": hashes, "runs": {}}
    control = one("control")
    results["runs"]["control"] = control  # type: ignore[index]
    print(f"control: {control['verdict']}", flush=True)
    ok = control["verdict"] == "PASS (control)"
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {ex.submit(one, mid): mid for mid in args.ids}
        for fut in concurrent.futures.as_completed(futs):
            mid = futs[fut]
            res = fut.result()
            results["runs"][mid] = res  # type: ignore[index]
            ok = ok and res["verdict"] == "caught"
            parts = []
            for k in ("pytest", "dogfood"):
                if k in res:
                    v = res[k]
                    parts.append(f"{k} exit {v['exit']} ({v['summary']}; {v['seconds']}s)")  # type: ignore[index]
            print(f"{mid}: {res['verdict']} by {res['caught_by']}; " + "; ".join(parts), flush=True)
            for t in (res.get("pytest") or {}).get("failed") or []:  # type: ignore[union-attr]
                print(f"    FAILED {t}", flush=True)
            for t in (res.get("dogfood") or {}).get("first_failures") or []:  # type: ignore[union-attr]
                print(f"    dogfood: {t}", flush=True)
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "results.json").write_text(json.dumps(results, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
