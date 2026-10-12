"""The mutation run of API v1.12 (spec sync in Studio), pre-registered in
``docs/results/2026-10-11-spec-sync-prereg.md``.

Modelled on ``perf_mutants.py``: for each mutant, ``lattice/``, ``tests/`` and
``dogfood/studio/project_seed/`` are copied to a scratch directory and the mutant's edits are
applied to the copy (each edit's old text must occur exactly once; SY-M5 is one mutation made
of two edits: the auto sync removed from the run's start and added after the start baseline),
then (a) ``tests/studio/test_spec_sync.py`` runs there with the copy first on ``PYTHONPATH``
and (b) the dogfood check ``dogfood/studio/check.sh`` runs from the working tree with
``LATTICE_STUDIO_PYTHONPATH=<copy>`` (the served Studio imports the copy; the checker is the
working tree's). A mutant is caught when either fails. A control run of the unpatched copy
comes first. The working tree is never patched. ``--jobs N`` runs N mutants at a time.

Usage: ``uv run python dogfood/studio/sync_mutants.py [--out DIR] [--jobs N] [ID ...]``.
Exit 0 when the control passes and every mutant is caught.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import perf_mutants  # noqa: E402
from perf_mutants import ROOT, copy_tree, run_dogfood, run_pytest, sha  # noqa: E402

SUITE = ["tests/studio/test_spec_sync.py"]
perf_mutants.SUITE = SUITE  # perf_mutants.run_pytest runs the module's SUITE
FILES = ["lattice/studio/rebuild.py", "lattice/studio/plan.py", "lattice/studio/server.py",
         "lattice/studio/mock_server.py", "dogfood/studio/spec.py",
         "dogfood/studio/project_seed/studio.toml", *SUITE,
         "dogfood/studio/sync_mutants.py"]  # fmt: skip

R, P = "lattice/studio/rebuild.py", "lattice/studio/plan.py"

# id -> (behaviour, [(file, old text, new text), ...])
# fmt: off
MUTANTS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "SY-M1": (
        "the sync reads the owner's working copy instead of HEAD",
        [(R,
          '                _git_ok(wt, "checkout", head, "--", *present)\n',
          "                for p in present:  # MUTANT SY-M1: the working copy, not HEAD\n"
          "                    (wt / p).parent.mkdir(parents=True, exist_ok=True)\n"
          "                    shutil.copyfile(repo / p, wt / p)\n"
          '                _git_ok(wt, "add", "--", *present)\n')],
    ),
    "SY-M2": (
        "a sync is allowed while the target is checked out",
        [(R,
          "        if where is not None:\n"
          '            raise RebuildError(409, f"branch {name} is checked out in {where}; '
          'Studio only "\n',
          "        if False:  # MUTANT SY-M2\n"
          '            raise RebuildError(409, f"branch {name} is checked out in {where}; '
          'Studio only "\n')],
    ),
    "SY-M3": (
        "the sync commits more than the spec files (git add -A)",
        [(R,
          '                _git_ok(wt, "checkout", head, "--", *present)\n',
          '                _git_ok(wt, "checkout", head, "--", *present)\n'
          '            _git_ok(wt, "add", "-A")  # MUTANT SY-M3\n')],
    ),
    "SY-M4": (
        "a sync is allowed while a plan holds the build slot",
        [(R,
          "        if busy:\n"
          '            raise RebuildError(409, f"a build is running in this project ({busy}); '
          'sync the "\n',
          "        if False:  # MUTANT SY-M4\n"
          '            raise RebuildError(409, f"a build is running in this project ({busy}); '
          'sync the "\n')],
    ),
    "SY-M5": (
        "auto mode syncs after the start baseline instead of before",
        [(P,
          "            self._auto_sync(plan)  # API v1.12: before the start baseline\n",
          "            pass  # MUTANT SY-M5: moved after the start baseline\n"),
         (P,
          '        self._results(name, start_sha, branch_tip, last_failing, f"plan {pid} start")\n',
          '        self._results(name, start_sha, branch_tip, last_failing, f"plan {pid} start")\n'
          '        if self.cfg.sync_spec == "auto":  # MUTANT SY-M5: after the start baseline\n'
          "            with self.lock:\n"
          "                self.rb.plan_active = None\n"
          "            try:\n"
          "                self._auto_sync(plan)\n"
          "            finally:\n"
          "                with self.lock:\n"
          "                    self.rb.plan_active = pid\n")],
    ),
    "SY-M6": (
        "nothing_to_sync creates an empty commit",
        [(R,
          "        if not files:\n"
          '            raise RebuildError(409, f"the spec on {name} is the same as on '
          '{source} at "\n'
          '                               f"{head[:7]}: nothing to sync", "nothing_to_sync")'
          "  # fmt: skip\n",
          "        if not files:  # MUTANT SY-M6: an empty commit\n"
          '            empty = _git_ok(repo, "commit-tree", "-p", tip, "-m", f"spec: sync from '
          '{source} {head[:7]}\\n\\n{SYNC_TRAILER}: {head}\\n", f"{tip}^{{tree}}")\n'
          "            self._advance_to(repo, name, tip, empty, False)\n"
          '            return {"branch": name, "synced_from": head, "files": [], "commit": empty,\n'
          '                    "previous_tip": tip}\n')],
    ),
}

# fmt: on


def apply(dst: Path, mid: str) -> None:
    for rel, old, new in MUTANTS[mid][1]:
        path = dst / rel
        text = path.read_text(encoding="utf-8")
        n = text.count(old)
        if n != 1:
            raise SystemExit(f"{mid}: the patched text occurs {n} times in {rel} (must be once)")
        path.write_text(text.replace(old, new), encoding="utf-8")


def one(mid: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"lattice-sync-mutant-{mid}-") as d:
        dst = Path(d)
        copy_tree(dst)
        if mid != "control":
            apply(dst, mid)
        res = {"pytest": run_pytest(dst), "dogfood": run_dogfood(dst)}
    caught_by = [k for k, v in res.items() if v["exit"] != 0]
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
    for mid in args.ids:  # every patch applies before anything runs
        with tempfile.TemporaryDirectory(prefix="lattice-sync-mutant-dry-") as d:
            for rel in {e[0] for e in MUTANTS[mid][1]}:
                (Path(d) / rel).parent.mkdir(parents=True, exist_ok=True)
                (Path(d) / rel).write_text((ROOT / rel).read_text(encoding="utf-8"))
            apply(Path(d), mid)
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
                v = res[k]
                parts.append(f"{k} exit {v['exit']} ({v['summary']}; {v['seconds']}s)")  # type: ignore[index]
            print(f"{mid}: {res['verdict']} by {res['caught_by']}; " + "; ".join(parts), flush=True)
            for t in res["pytest"].get("failed") or []:  # type: ignore[union-attr]
                print(f"    FAILED {t}", flush=True)
            for t in res["dogfood"].get("first_failures") or []:  # type: ignore[union-attr]
                print(f"    dogfood: {t}", flush=True)
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "results.json").write_text(json.dumps(results, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
