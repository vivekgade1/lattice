"""Phase 2 exit measurement (plan 2026-10-05, §1.3; Wave 3).

    python -m bench.phase2_exit [APP ...] [--out bench/results/phase2] [--work DIR]

For each app (default: every app in :data:`APPS`), in this order and each in its own
process (Django keeps one project per process):

1. **Import**: ``python -m lattice.importer ROOT --package P --rules-only --no-llm``
   (no API key, no model call), timed.
2. **Recompute** the verdicts from ``report.json["rules"]["items"]`` alone (§1.1, §1.2:
   :func:`recompute`) and compare them with the report's table and route verdicts and
   its summary. A disagreement fails the run (exit 1), whatever the percentages.
3. **Fidelity** (§1.4): the corrected spec validates; layer R of it on seed variant 2
   (recorded by the import: ``rules.variant2``) has no finding; and the app's recorded
   scenarios (``bench/import_repos/<app>/fidelity.py``, when it has one) replay through
   its runtime with the rule oracle predicting every step
   (``python -m lattice.spec fidelity``).

Writes ``<out>/<app>.json`` per app and ``<out>/summary.md`` (a Markdown table, plus the
pooled figures), and prints the table. "Verified" counts ``verified`` and ``verified
(corrected)`` items; ``unexercised`` (unreachable-by-probe included) and ``unclear`` do
not. The gate (≥ 90% of tables and of routes per app) is reported, not enforced here:
``tests/importer/test_phase2_dev_apps.py`` enforces it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "bench" / "results" / "phase2"
# app -> the package the import reads
APPS: dict[str, str] = {
    "template": "bench.template_app.app",
    "taskflow": "bench.import_repos.taskflow.app",
    "justnotes": "bench.import_repos.justnotes.app",
    "multivendor": "bench.import_repos.multivendor.app",
    "snippets": "bench.import_repos.snippets",
    "recipes": "bench.import_repos.recipes",
    "microblog": "bench.import_repos.microblog",
    "flasky": "bench.import_repos.flasky",
}
GATE = 0.9
VERIFIED = ("verified", "verified (corrected)")
TIMEOUT = 900


# ---- the recomputation (§1.1, §1.2) --------------------------------------------------------------


def recompute(items: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Table and route verdicts from the per-item records alone.

    A table (the subject of T items) is verified when its T1 is verified, no item of it is
    refuted or unclear, and every T2-T5 item is verified or unexercised; a T1 no probe
    reached makes it ``unverified`` (``unreached``). A route (the subject of R items) is
    verified when its R1 is verified, its R2 (if any) verified, and no R3 item refuted or
    unclear."""
    by: dict[str, list[Mapping[str, Any]]] = {}
    for record in items.values():
        by.setdefault(record["subject"], []).append(record)
    tables: dict[str, dict[str, Any]] = {}
    routes: dict[str, dict[str, Any]] = {}
    for subject, found in by.items():
        kinds = {r["kind"] for r in found}
        if kinds <= {"T1", "T2", "T3", "T4", "T5"}:
            t1 = next((r for r in found if r["kind"] == "T1"), None)
            bad = [r["id"] for r in found if r["verdict"] in ("refuted", "unclear")]
            if t1 is not None and t1["verdict"] == "unexercised":
                verdict, reason = "unverified", "unreached"
            elif bad:
                verdict, reason = "unverified", "items " + ", ".join(bad)
            elif t1 is None or t1["verdict"] not in VERIFIED:
                verdict, reason = "unverified", f"T1 {t1['verdict'] if t1 else 'missing'}"
            else:
                verdict, reason = "verified", ""
            tables[subject] = {"verdict": verdict, "reason": reason}
        else:
            r1 = next((r for r in found if r["kind"] == "R1"), None)
            r2 = next((r for r in found if r["kind"] == "R2"), None)
            bad = [r["id"] for r in found
                   if r["kind"] == "R3" and r["verdict"] in ("refuted", "unclear")]  # fmt: skip
            reasons = []
            if r1 is None or r1["verdict"] not in VERIFIED:
                reasons.append(f"R1 {r1['verdict'] if r1 else 'missing'}")
            if r2 is not None and r2["verdict"] not in VERIFIED:
                reasons.append(f"R2 {r2['verdict']}")
            if bad:
                reasons.append("items " + ", ".join(bad))
            routes[subject] = {"verdict": "unverified" if reasons else "verified",
                               "reason": "; ".join(reasons)}  # fmt: skip
    return {"tables": tables, "routes": routes}


def _count(group: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    n = sum(v["verdict"] == "verified" for v in group.values())
    total = len(group)
    return {"verified": n, "total": total, "pct": round(100 * n / total, 1) if total else 0.0}


def agreement(rules: Mapping[str, Any]) -> list[str]:
    """Where the recomputation disagrees with the report (empty: it agrees)."""
    mine = recompute(rules["items"])
    out = []
    for group in ("tables", "routes"):
        theirs = {k: v["verdict"] for k, v in rules[group].items()}
        ours = {k: v["verdict"] for k, v in mine[group].items()}
        if theirs != ours:
            diff = sorted(k for k in set(theirs) | set(ours) if theirs.get(k) != ours.get(k))
            out.append(f"{group}: {diff}")
        summary = rules["summary"][group]
        counted = _count(mine[group])
        if (summary["verified"], summary["total"]) != (counted["verified"], counted["total"]):
            out.append(f"summary {group}: report {summary}, recomputed {counted}")
    return out


# ---- one app ----------------------------------------------------------------------------------


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _run(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], cwd=REPO_ROOT, env=_env(),
                          capture_output=True, text=True, timeout=TIMEOUT, check=False)  # fmt: skip


def _fidelity(app: str, package: str, out: Path, rules: Mapping[str, Any]) -> dict[str, Any]:
    variant2 = rules.get("variant2") or {}
    result: dict[str, Any] = {
        "variant2_clean": bool(variant2.get("clean")),
        "variant2_findings": [f["item"] for f in variant2.get("findings", ())],
    }
    vendored = REPO_ROOT / "bench" / "import_repos" / app
    if not (vendored / "fidelity.py").exists():
        result["scenarios"] = None
        result["note"] = "no recorded scenarios (fidelity.py): the spec's validity and variant 2"
        args = ["-c", "from lattice.importer.emit_spec import load_spec; from lattice.spec "
                "import validate; import sys; s = load_spec(sys.argv[1]).spec; "
                "sys.exit(1 if validate(s) else 0)", str(out / "candidate_spec.py")]  # fmt: skip
        done = _run(args)
        result["spec_valid"] = done.returncode == 0
    else:
        base = f"bench.import_repos.{app}"
        done = _run(["-m", "lattice.spec", "fidelity", "--spec", str(out / "candidate_spec.py"),
                     "--import", str(out), "--package", package, "--scenarios",
                     f"{base}.fidelity:SCENARIOS", "--seed", f"{base}.seed", "--json"])  # fmt: skip
        if done.returncode == 2 or not done.stdout.strip():
            result["scenarios"] = {"error": done.stderr[-2000:]}
            result["spec_valid"] = False
        else:
            data = json.loads(done.stdout.strip().splitlines()[-1])
            keys = ("passed", "scenarios", "steps", "answers_ok", "oracle_ok", "failed")
            result["scenarios"] = {k: data[k] for k in keys}
            result["spec_valid"] = data["spec_valid"]
    scenarios = result["scenarios"]
    scenarios_ok = scenarios is None or bool(scenarios.get("passed"))
    result["passed"] = result["spec_valid"] and result["variant2_clean"] and scenarios_ok
    return result


def measure(app: str, work: Path) -> dict[str, Any]:
    """Import ``app``, recompute, run its fidelity suite: the app's result record."""
    package = APPS[app]
    out = work / app
    started = time.perf_counter()
    done = _run(["-m", "lattice.importer", str(REPO_ROOT), "--package", package,
                 "--rules-only", "--no-llm", "--no-corpus", "--out", str(out)])  # fmt: skip
    seconds = time.perf_counter() - started
    if done.returncode != 0:
        return {"app": app, "error": done.stderr[-4000:], "import_s": round(seconds, 2)}
    report = json.loads((out / "report.json").read_text())
    rules = report["rules"]
    disagreements = agreement(rules)
    mine = recompute(rules["items"])
    tables, routes = _count(mine["tables"]), _count(mine["routes"])
    items = rules["items"].values()
    unreachable_routes = sorted(
        {
            r["subject"]
            for r in items
            if r["kind"] == "R1" and r["reason"].startswith("unreachable-by-probe")
        }  # fmt: skip
    )
    return {
        "app": app,
        "package": package,
        "framework": rules.get("framework"),
        "tables": tables,
        "routes": routes,
        "unverified_tables": {
            k: v["reason"] for k, v in mine["tables"].items() if v["verdict"] != "verified"
        },  # fmt: skip
        "unverified_routes": {
            k: v["reason"] for k, v in mine["routes"].items() if v["verdict"] != "verified"
        },  # fmt: skip
        "items": dict(rules["summary"]["items"]),
        "corrected": sum(r["verdict"] == "verified (corrected)" for r in items),
        "unreachable_routes": unreachable_routes,
        "unreachable_items": sum(r["reason"].startswith("unreachable-by-probe") for r in items),
        "excluded_framework_routes": sum(
            e["kind"] == "framework" for e in rules.get("excluded_routes", ())
        ),  # fmt: skip
        "baseline": rules.get("baseline", []),
        "mining": rules.get("mining"),
        "agrees": not disagreements,
        "disagreements": disagreements,
        "fidelity": _fidelity(app, package, out, rules),
        "gate": {"tables": tables["pct"] >= 100 * GATE, "routes": routes["pct"] >= 100 * GATE},
        "import_s": round(seconds, 2),
        "stage_s": rules.get("timing"),
        "llm": {"tokens": 0, "cost_usd": 0.0},
    }


# ---- the table ---------------------------------------------------------------------------------


def markdown(results: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "| app | framework | tables verified | routes verified | corrected | unreachable "
        "routes | baseline | fidelity | recompute | import (s) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    pooled = {"tables": [0, 0], "routes": [0, 0]}
    for r in results:
        if "error" in r:
            lines.append(f"| {r['app']} | error | | | | | | | | {r['import_s']} |")
            continue
        for g in pooled:
            pooled[g][0] += r[g]["verified"]
            pooled[g][1] += r[g]["total"]
        t, ro, f = r["tables"], r["routes"], r["fidelity"]
        lines.append(
            f"| {r['app']} | {r['framework']} | {t['verified']}/{t['total']} ({t['pct']}%) | "
            f"{ro['verified']}/{ro['total']} ({ro['pct']}%) | {r['corrected']} | "
            f"{len(r['unreachable_routes'])} | {len(r['baseline'])} | "
            f"{'pass' if f['passed'] else 'fail'} | {'agrees' if r['agrees'] else 'DISAGREES'} | "
            f"{r['import_s']} |"
        )
    pct = {g: round(100 * v / n, 1) if n else 0.0 for g, (v, n) in pooled.items()}
    lines += ["", f"Pooled: tables {pooled['tables'][0]}/{pooled['tables'][1]} "
              f"({pct['tables']}%), routes {pooled['routes'][0]}/{pooled['routes'][1]} "
              f"({pct['routes']}%)."]  # fmt: skip
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.phase2_exit", description=__doc__)
    p.add_argument("apps", nargs="*", help=f"apps (default all: {', '.join(APPS)})")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--work", type=Path, default=None, help="where the imports go")
    args = p.parse_args(argv)
    apps = args.apps or list(APPS)
    unknown = [a for a in apps if a not in APPS]
    if unknown:
        p.error(f"unknown apps {unknown} (known: {', '.join(APPS)})")
    args.out.mkdir(parents=True, exist_ok=True)
    work = args.work or args.out / "imports"
    work.mkdir(parents=True, exist_ok=True)
    results = []
    for app in apps:
        r = measure(app, work)
        (args.out / f"{app}.json").write_text(json.dumps(r, indent=2, default=str) + "\n")
        results.append(r)
    table = markdown(results)
    (args.out / "summary.md").write_text(table)
    print(table)
    failed = [r["app"] for r in results if "error" in r or not r["agrees"]]
    if failed:
        print(f"the exit measurement failed for {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
