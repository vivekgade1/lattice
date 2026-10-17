"""The 16 mutants of the go-to place (docs/results/2026-10-16-goto-place-prereg.md, section 6).

    uv run python dogfood/goto_mutants.py --scratch "$SCRATCH/m" --out DIR [--only GM3,GM7]

A scratch copy of the tree (``lattice/`` and ``tests/``) is mutated in place, one text replacement
per mutant (asserted to match exactly once), and restored afterwards. **The control runs first**: the
named killers run on the unmutated copy and must all pass. A mutant is **killed** when at least one
of its killers fails on the mutated copy. Killers are of two kinds, both disclosed in the output:

* ``tests``: named pytest node ids of this spike's tests (fast, deterministic);
* ``probes``: rows of ``dogfood/goto_probes.py`` run on the Streak scratch world (the map probes that
  need no change record: ``--stage dev --no-records``).

The prereg names one probe per mutant; where the named probe needs the question set (scored once) or
the six Docker-checked change records, a named unit test kills it instead, and the output says which.
"""

# ruff: noqa: E501 -- probe tables and mutant texts are data lines
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("GOTO_MUTANTS_REPO", HERE.parent)).resolve()

A = "tests/spec/test_appmap.py"
B = "tests/studio/test_appmap_api.py"
C = "tests/studio/test_impact_ask.py"
D = "tests/studio/test_appmap_ui.py"

MUTANTS: dict[str, dict[str, Any]] = {
    "GM1": {
        "what": "an implemented_unverified rule (static) reported observed",
        "prereg": "PM1",
        "edits": [
            (
                "lattice/spec/appmap.py",
                '    "implemented_unverified": "static",',
                '    "implemented_unverified": "observed",',
            )
        ],
        "tests": [f"{A}::test_basis_words_are_fixed"],
        "probes": ["PM1"],
    },
    "GM2": {
        "what": "unread dropped from the map",
        "prereg": "PM6",
        "edits": [
            ("lattice/spec/appmap.py", '        "unread": unread,\n', '        "unread": [],\n')
        ],
        "tests": [f"{A}::test_unread_items_are_listed_with_their_code_location"],
        "probes": ["PM6"],
    },
    "GM3": {
        "what": "evidence paths made absolute",
        "prereg": "PD, PM2",
        "edits": [
            (
                "lattice/spec/appmap.py",
                "    return cv.loc(m.group(1), int(m.group(2))) if m else None",
                '    return f"code:{cv.inv.app_root.resolve()}/{m.group(1)}:{int(m.group(2))}" if m else None',
            )
        ],
        "tests": [f"{A}::test_determinism_and_purity"],
        "probes": ["PM2"],
    },
    "GM4": {
        "what": "access auth taken from a path-name heuristic instead of the route row",
        "prereg": "PM1",
        "edits": [
            (
                "lattice/spec/appmap.py",
                '                        "auth": route["auth"] if route is not None else None,',
                '                        "auth": None if route is None else ("Public" if "/auth/" in col or col.endswith("/health") else "Customer"),',
            )
        ],
        "tests": [f"{A}::test_auth_and_refusals_are_the_route_rows_not_a_guess"],
        "probes": ["PM1"],
    },
    "GM5": {
        "what": "staleness ignored (no downgrade, no note)",
        "prereg": "PM4",
        "edits": [
            (
                "lattice/spec/appmap.py",
                '        if f["effective"] != "verified":',
                '        if f["status"] != "verified":',
            ),
            (
                "lattice/spec/appmap.py",
                "    if stale:\n        notes.append",
                "    if False:\n        notes.append",
            ),
        ],
        "tests": [f"{A}::test_stale_downgrades_and_says_so"],
        "probes": ["PM4"],
    },
    "GM6": {
        "what": "flow steps with caller null not joined to the no-token row",
        "prereg": "PM1 (the observed recount)",
        "edits": [
            (
                "lattice/spec/appmap.py",
                'observed.setdefault((str(s["caller"] or "no token"), str(s["route"])), [])',
                'observed.setdefault((str(s["caller"]), str(s["route"])), [])',
            )
        ],
        "tests": [f"{A}::test_a_step_without_a_caller_joins_the_no_token_row"],
        "probes": ["PM1"],
    },
    "GM7": {
        "what": "/api/impact merges tier 3 may_fail flows into tier 1 failing",
        "prereg": "PI, PU (verbatim)",
        "edits": [
            (
                "lattice/spec/impact_ask.py",
                '    body["report"] = report\n    body["tier1_saved"] = saved_tier1(reports, change, report)',
                '    report["tier1"]["failing"] = [*report["tier1"]["failing"], *report["tier3"]["flows"]]\n    body["report"] = report\n    body["tier1_saved"] = saved_tier1(reports, change, report)',
            )
        ],
        "tests": [f"{B}::test_the_report_is_the_cli_report_verbatim"],
        "probes": [],
    },
    "GM8": {
        "what": "the router answers a hypothetical with tier 3 style items as failures",
        "prereg": "PR (the H rows)",
        "edits": [
            (
                "lattice/spec/impact_ask.py",
                "            NO_PREDICTION,\n            explain,",
                '            "These will fail: " + ", ".join(sorted(k for t in touched.values() for v in t.values() for k in v)) + ".",\n            explain,',
            )
        ],
        "tests": [
            f"{C}::test_a_hypothetical_is_never_a_failure_claim",
            f"{C}::test_a_hypothetical_names_its_static_neighbourhood",
        ],
        "probes": [],
    },
    "GM9": {
        "what": "tier1_saved shown on a hash mismatch",
        "prereg": "PIS",
        "edits": [
            (
                "lattice/spec/impact_ask.py",
                "    if differs:\n        return {**out",
                "    if False:\n        return {**out",
            )
        ],
        "tests": [f"{B}::test_tier1_saved_is_shown_only_when_the_hashes_match"],
        "probes": [],
    },
    "GM10": {
        "what": "the mock serves a hand-edited answer (drift from the real server)",
        "prereg": "PP",
        "edits": [
            (
                "lattice/studio/mock_server.py",
                "    def get_appmap(self, section: str | None) -> dict[str, Any]:\n        doc = self.appmap().doc\n",
                '    def get_appmap(self, section: str | None) -> dict[str, Any]:\n        doc = copy.deepcopy(self.appmap().doc)\n        if doc["state"] == "ok":\n            doc["summary"]["rules"]["total"] += 1\n',
            )
        ],
        "tests": [f"{B}::test_the_mock_is_the_real_server_over_the_same_fixture"],
        "probes": [],
    },
    "GM11": {
        "what": "GET /api/appmap writes a cache file under the state directory",
        "prereg": "PW",
        "edits": [
            (
                "lattice/studio/server.py",
                "        doc = self._appmap().doc\n        return appmap_mod.section_of(doc, section) if section else doc",
                '        doc = self._appmap().doc\n        if self.store is not None:\n            (self.store.root / "appmap.cache.json").write_text(json.dumps(doc))\n        return appmap_mod.section_of(doc, section) if section else doc',
            )
        ],
        "tests": [f"{B}::test_get_never_runs_tier_one_or_a_process_and_writes_nothing"],
        "probes": [],
    },
    "GM12": {
        "what": "/api/model gains an appmap key",
        "prereg": "PB",
        "edits": [
            (
                "lattice/studio/server.py",
                '            out = dict(core)\n            out["project"] = {**core["project"], "git_head": head}',
                '            out = dict(core)\n            out["appmap"] = {"state": "hidden"}\n            out["project"] = {**core["project"], "git_head": head}',
            )
        ],
        "tests": [f"{B}::test_existing_routes_gain_no_key"],
        "probes": [],
    },
    "GM13": {
        "what": "the UI word map shows Verified for implemented_unverified",
        "prereg": "PU",
        "edits": [
            (
                "lattice/studio/web/js/views/appmap.js",
                '  implemented_unverified: { word: "Implemented, not verified"',
                '  implemented_unverified: { word: "Verified"',
            )
        ],
        "tests": [
            f"{D}::test_the_fixed_headings_and_words",
            f"{D}::test_the_map_walks_against_the_mock",
        ],
        "probes": [],
    },
    "GM14": {
        "what": "session.py lists change_impact without [impact]",
        "prereg": "PS",
        "edits": [
            (
                "lattice/studio/session.py",
                '    if mode not in ("ask", "explain", "change") or not _has_impact(project):',
                '    if mode not in ("ask", "explain", "change"):',
            )
        ],
        "tests": [
            f"{C}::test_with_impact_the_tool_and_the_paragraph_are_added_to_three_modes",
            f"{C}::test_a_message_builds_the_argv_the_helpers_describe",
        ],
        "probes": [],
    },
    "GM15": {
        "what": "the router calls the generator for a routed question",
        "prereg": "PR (call count), PW (the claude shim)",
        "edits": [
            (
                "lattice/studio/server.py",
                "        return impact_ask.route(question, lookup=self._impact_body, appmap=am)",
                '        self.generator.generate("mutant: a model call", [])\n        return impact_ask.route(question, lookup=self._impact_body, appmap=am)',
            )
        ],
        "tests": [f"{C}::test_a_routed_question_makes_no_model_call"],
        "probes": [],
    },
    "GM16": {
        "what": "GET /api/impact runs tier 1 (observe)",
        "prereg": "PW (containers), PI",
        "edits": [
            (
                "lattice/spec/impact_ask.py",
                '        report = ir.build_report(inp, tiers="23", runner=None, baseline="none")',
                '        report = ir.build_report(inp, tiers="123", runner=ir.harness_runner(project), baseline="none")',
            )
        ],
        "tests": [
            f"{B}::test_get_never_runs_tier_one_or_a_process_and_writes_nothing",
            f"{B}::test_the_report_is_the_cli_report_verbatim",
        ],
        "probes": [],
    },
}


def env_for(tree: Path, scratch: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", GOTO_PROBES_REPO=str(tree),
               IMPLEMENTATION_PROBES_REPO=str(tree), GOTO_MUTANTS_REPO=str(tree))  # fmt: skip
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def run_tests(tree: Path, scratch: Path, ids: list[str]) -> dict[str, Any]:
    if not ids:
        return {"ran": False, "failed": [], "summary": "no tests"}
    p = subprocess.run(
        ["uv", "run", "python", "-m", "pytest", *ids, "-q", "--no-header", "-p", "no:cacheprovider", "-x", "-rf"],
        capture_output=True, text=True, env=env_for(tree, scratch), cwd=tree, timeout=1800, check=False,
    )  # fmt: skip
    failed = [ln.split()[1] for ln in p.stdout.splitlines() if ln.startswith("FAILED ")]
    last = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-200:]
    return {"ran": True, "rc": p.returncode, "failed": failed, "summary": last}


def run_probes(tree: Path, scratch: Path, ids: list[str], label: str) -> dict[str, Any]:
    if not ids:
        return {"ran": False, "failed": [], "summary": "no probes"}
    out = scratch / f"probes-{label}"
    shutil.rmtree(out, ignore_errors=True)
    py = os.environ.get("GOTO_DRIVER_PY", sys.executable)
    p = subprocess.run(
        [py, str(HERE / "goto_probes.py"), "--stage", "dev", "--no-records", "--scratch", str(scratch / f"world-{label}"),
         "--out", str(out), "--only", ",".join(ids)],
        capture_output=True, text=True, env=env_for(tree, scratch), cwd=tree, timeout=1800, check=False,
    )  # fmt: skip
    failed = []
    with_json = out / "probes.json"
    if with_json.is_file():
        data = json.loads(with_json.read_text())
        failed = [
            r["id"] for r in data["probes"] if r.get("ran") and not r.get("pass") and r["id"] in ids
        ]
        got = sorted(r["id"] for r in data["probes"] if r["id"] in ids)
    else:
        failed, got = ["(runner failed)"], []
    return {
        "ran": True,
        "rc": p.returncode,
        "failed": failed,
        "ids_seen": got,
        "summary": p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-300:],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only")
    args = ap.parse_args(argv)
    scratch = Path(args.scratch).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    tree = scratch / "tree"
    shutil.rmtree(tree, ignore_errors=True)
    tree.mkdir(parents=True)
    for d in ("lattice", "tests", "docs/results/data/2026-10-15-workflow-layer"):
        src = REPO / d
        if src.is_dir():
            shutil.copytree(
                src,
                tree / d,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
            )
    for f in ("pyproject.toml",):
        if (REPO / f).is_file():
            shutil.copy(REPO / f, tree / f)
    want = [m for m in MUTANTS if not args.only or m in args.only.split(",")]
    results: dict[str, Any] = {}
    killers_tests = sorted({t for m in want for t in MUTANTS[m]["tests"]})
    killers_probes = sorted({p for m in want for p in MUTANTS[m]["probes"]})
    t0 = time.perf_counter()
    print(f"control: {len(killers_tests)} tests, probes {killers_probes}", flush=True)
    control = {
        "tests": run_tests(tree, scratch, killers_tests),
        "probes": run_probes(tree, scratch, killers_probes, "control"),
    }
    control_ok = (
        not control["tests"]["failed"]
        and control["tests"].get("rc", 0) == 0
        and not control["probes"]["failed"]
    )
    results["control"] = {**control, "passes": control_ok}
    print("control", "PASS" if control_ok else "FAIL", flush=True)
    if not control_ok:
        (out / "mutants.json").write_text(json.dumps(results, indent=1))
        return 1
    killed = 0
    for mid in want:
        m = MUTANTS[mid]
        originals: dict[Path, str] = {}
        try:
            for rel, old, new in m["edits"]:
                f = tree / rel
                text = originals.setdefault(f, f.read_text())
                cur = f.read_text()
                assert cur.count(old) == 1, (
                    f"{mid}: the text to mutate matches {cur.count(old)} times in {rel}: {old[:70]!r}"
                )
                f.write_text(cur.replace(old, new))
            tr = run_tests(tree, scratch, m["tests"])
            pr = run_probes(tree, scratch, m["probes"], mid)
        finally:
            for f, text in originals.items():
                f.write_text(text)
        by = (
            ["test " + t for t in tr["failed"]]
            or (
                ["tests (collection or import error)"]
                if tr.get("rc", 0) not in (0, None) and tr["ran"]
                else []
            )
        ) + ["probe " + p for p in pr["failed"]]
        is_killed = bool(by)
        killed += is_killed
        results[mid] = {
            "what": m["what"],
            "prereg_killer": m["prereg"],
            "killed": is_killed,
            "killed_by": by,
            "tests": tr,
            "probes": pr,
        }
        print(
            f"{mid:<5} {'KILLED' if is_killed else 'SURVIVED':<9} {m['what']}  <- {', '.join(by) or '-'}",
            flush=True,
        )
    results["summary"] = {
        "mutants": len(want),
        "killed": killed,
        "seconds": round(time.perf_counter() - t0, 1),
    }
    (out / "mutants.json").write_text(json.dumps(results, indent=1))
    print(f"killed {killed}/{len(want)}")
    return 0 if killed == len(want) else 1


if __name__ == "__main__":
    sys.exit(main())
