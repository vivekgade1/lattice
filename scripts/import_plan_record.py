"""Import a plan record saved before the durable state store (API v1.10) into a project's
state directory, so Studio can resume it (``POST /api/plan/<id>/run``, ``.../land``).

Usage::

    python scripts/import_plan_record.py --project ~/Documents/streak \\
        --record /tmp/p2-final.json [--dry-run]

What it does, and nothing else:

* reads the record (the JSON ``GET /api/plan/<id>`` answered), refuses one that is not a plan,
  is still planning/running, or whose id already has a state file;
* rebuilds the private part the store keeps next to a record (scope, branch, models,
  planner) from the record itself;
* pins every recorded node commit and gated level tip that exists in the repository as
  ``refs/lattice/plans/<id>/<node|L<n>>`` (a missing commit is only reported);
* writes ``plans/<id>.json`` through the store and raises its id counters past the record's
  plan and build ids.

It never moves a branch, never starts a process and never edits the record's history.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from lattice.studio import state as st

REF_PREFIX = "refs/lattice/plans"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _scope(rec: dict[str, Any]) -> dict[str, Any]:
    sc = dict(rec.get("scope") or {"kind": "all", "areas": [], "rules": [], "title": "All"})
    kind = sc.get("kind")
    if kind == "areas":
        sc["slug"] = "areas-" + "-".join(sc.get("areas") or [])
    elif kind == "rules":
        sc["slug"] = "rules-" + "-".join(sc.get("rules") or [])
    else:
        sc["slug"] = "all"
    return sc


def _max_num(prefix: str, ids: list[str]) -> int:
    nums = [int(m.group(1)) for i in ids if (m := re.fullmatch(rf"{prefix}(\d+)", str(i)))]
    return max(nums, default=0)


def plan_import(project: Path, rec: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    """Import `rec` for the Studio project at `project`; answers a report dict."""
    pid = str(rec.get("id") or "")
    if not re.fullmatch(r"p\d+", pid) or "nodes" not in rec or "level_runs" not in rec:
        raise SystemExit("not a plan record (needs id pN, nodes and level_runs)")
    if rec.get("status") in ("planning", "running"):
        raise SystemExit(f"plan {pid} is {rec['status']}: only a stopped plan can be imported")
    toml = tomllib.loads((project / "studio.toml").read_text(encoding="utf-8"))
    name = str((toml.get("project") or {}).get("name") or project.name)
    sdir = st.state_dir_for(project, name, toml.get("state"), toml.get("rebuild"))
    repo_rel = str((toml.get("rebuild") or {}).get("repo") or ".")
    repo = (project / repo_rel).resolve()
    store = st.StateStore(sdir)
    if store.path("plans", pid).exists():
        raise SystemExit(f"{store.path('plans', pid)} already exists: not overwriting")

    report: dict[str, Any] = {"plan": pid, "state_dir": str(sdir), "pinned": {}, "missing": {}}
    want: dict[str, str] = {}
    for n in rec["nodes"]:
        if n.get("commit"):
            want[str(n["id"])] = str(n["commit"])
    for lr in rec["level_runs"]:
        if lr and lr.get("tip") and lr.get("gate"):
            want[f"L{lr['level']}"] = str(lr["tip"])
    anchors: dict[str, str] = dict(rec.get("anchors") or {})
    for key, sha in want.items():
        if _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
            report["missing"][key] = sha
            continue
        report["pinned"][key] = sha
        anchors[key] = sha
        if not dry_run:
            r = _git(repo, "update-ref", f"{REF_PREFIX}/{pid}/{key}", sha)
            if r.returncode != 0:
                raise SystemExit(f"could not pin {key}: {r.stderr.strip()}")

    rec = json.loads(json.dumps(rec))  # a copy
    rec["anchors"] = anchors
    rec.setdefault("timeline", [])
    rec["timeline"].append({"at": st._now(), "event": f"imported from a pre-v1.10 record ({pid})"})
    private = {"scope": _scope(rec), "branch": rec.get("branch"), "confirm": False,
               "models": dict(rec.get("models") or {}), "planner": "agent", "doc": None,
               "overrides": {}, "created_target": bool(rec.get("created_branch")),
               "run_started_ts": 0.0}  # fmt: skip
    build_ids = [str(n["build_id"]) for n in rec["nodes"] if n.get("build_id")]
    report["counters"] = {"plans": _max_num("p", [pid]), "builds": _max_num("b", build_ids)}
    if not dry_run:
        store.save("plans", pid, rec, private)
        counters = store._meta.setdefault("counters", {})
        for kind, n in report["counters"].items():
            counters[kind] = max(int(counters.get(kind) or 0), n)
        store._write_meta()
    report["written"] = not dry_run
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True, type=Path, help="the Studio project directory")
    ap.add_argument("--record", required=True, type=Path, help="the plan record JSON")
    ap.add_argument("--min-plans", type=int, default=0, help="raise the plan id counter to this")
    ap.add_argument("--min-builds", type=int, default=0, help="raise the build id counter to this")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rec = json.loads(a.record.read_text(encoding="utf-8"))
    rec = rec.get("record", rec) if isinstance(rec, dict) else rec
    rep = plan_import(a.project.resolve(), rec, dry_run=a.dry_run)
    if not a.dry_run and (a.min_plans or a.min_builds):
        store = st.StateStore(Path(rep["state_dir"]))
        counters = store._meta.setdefault("counters", {})
        counters["plans"] = max(int(counters.get("plans") or 0), a.min_plans)
        counters["builds"] = max(int(counters.get("builds") or 0), a.min_builds)
        store._write_meta()
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
