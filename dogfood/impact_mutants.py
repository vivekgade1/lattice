"""Mutants of the change-impact engine (docs/results/2026-10-16-change-impact-prereg.md, section 4).

Each mutant is applied to a scratch copy of the tree (never the repository) and must be caught by
the catcher named for it: a pytest node, or the frozen-file check of PB. The control (the
unmodified copy) runs the same catchers first and must pass them. Where the pre-registration
names a probe (PT1, PT2, PU, PD, PW, LEAK, PB) as the catcher, the named unit test stands for it
and the row says so (disclosed in the results).

    uv run python dogfood/impact_mutants.py $(seq -f IX%g 1 16) [--scratch DIR] [--out FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
BASE_COMMIT = "bb3ff0c"
T = "tests/spec/test_impact_report.py"
M = "tests/mcp/test_change_impact.py"
REPORT = "lattice/spec/impact_report.py"
TIER2 = "lattice/spec/impact_tier2.py"
TIER3 = "lattice/spec/impact_tier3.py"
TOOLS = "lattice/mcp/tools.py"
SERVER = "lattice/studio/server.py"
COPY = ("lattice", "tests", "dogfood", "pyproject.toml")

# id -> (description, [(file, old, new), ...], catchers, the pre-registered catcher)
Edit = tuple[str, str, str]
MUTANTS: dict[str, tuple[str, list[Edit], list[str], str]] = {
    "IX1": (
        "T1 drops A findings",
        [
            (
                REPORT,
                "    own, by_flow = _split(cand_report, flow_ids)\n    base_own",
                "    own, by_flow = _split(cand_report, flow_ids)\n"
                '    own = [f for f in own if f.layer != "A"]\n    base_own',
            )
        ],
        [
            f"{T}::test_a_go808_finding_names_its_machine",
            f"{T}::test_tier1_keeps_a_finding_with_no_target",
        ],
        "PT1 on dev fixture edge_removed (GO808)",
    ),
    "IX2": (
        "T1 reuses the base report (no candidate run)",
        [
            (
                REPORT,
                "        cand_report = runner(spec_c, run_layers)",
                "        cand_report = runner(with_flows(inp.base.spec, flows), run_layers)",
            )
        ],
        [
            f"{T}::test_tier1_reword_is_empty_and_status_changed_is_exact",
            f"{T}::test_tier1_runs_the_candidate_not_the_base",
        ],
        "PT1",
    ),
    "IX3": (
        "D4a ignores earlier requests",
        [(TIER2, "tuple(prefix)", "()")],
        [f"{T}::test_tier2_prefix_must_be_identical"],
        "PT2 precision; unit prefix_differs",
    ),
    "IX4": (
        "an added body key counts as incompatible",
        [
            (
                TIER2,
                '    if ca == "map":\n        for k in sorted(set(a) & set(b), key=str):',
                '    if ca == "map":\n        if set(a) != set(b):\n            return path\n'
                "        for k in sorted(set(a) & set(b), key=str):",
            )
        ],
        [
            f"{T}::test_differs_follows_the_checkers_subset",
            f"{T}::test_tier2_an_added_body_key_is_compatible_not_a_claim",
        ],
        "PT2 precision; unit",
    ),
    "IX5": (
        "D4a skips the baseline-passed premise",
        [(TIER2, "if passed is not None and (sc.name, k) in passed:", "if True:")],
        [f"{T}::test_tier2_claims_need_the_premise_that_the_corpus_step_passed"],
        "unit base_prefix_failed (minishop)",
    ),
    "IX6": (
        "T2 reads the candidate run",
        [
            (
                REPORT,
                "passed = impact_tier2.passed_steps(base_report.cases) "
                "if base_report is not None else None",
                "passed = impact_tier2.passed_steps(cand_report.cases)",
            )
        ],
        [f"{T}::test_tier2_the_spec_only_mode_equals_the_full_mode_on_the_determined_set"],
        "PT2i",
    ),
    "IX7": (
        ".funcN and ambiguous matches resolved to the first hit",
        [
            (TIER3, "        if len(hits) == 1:", "        if hits:"),
            (TIER3, "        m = _LITERAL.match(name)\n", "        m = None\n"),
        ],
        [
            f"{T}::test_tier3_ambiguous_and_missing_declarations_are_unread_never_guessed",
            f"{T}::test_tier3_a_function_literal_maps_to_its_enclosing_declaration",
        ],
        "PU",
    ),
    "IX8": (
        "locations from grep of path strings",
        [
            (
                TIER3,
                "    # ---- finish\n",
                "    for key in sorted(seeds.routes):\n"
                '        segs = [s for s in key.partition(" ")[2].split("/") if s and s != "{}"]\n'
                '        lit = segs[-1] if segs else ""\n'
                "        for rel in sorted(inv.source_paths):\n"
                "            for n, text in enumerate(tree.lines(rel) or [], 1):\n"
                "                if lit and lit in text:\n"
                '                    locs.add("direct", rel, n, "grep hit", '
                'chains=[f"grep:{lit}"])\n'
                "    # ---- finish\n",
            )
        ],
        [f"{T}::test_tier3_every_chain_ends_in_a_code_location_and_holds_an_extract_fact"],
        "PU (via must end in an extract fact); PT3 lift",
    ),
    "IX9": (
        "absolute paths or unsorted sets",
        [
            (
                TIER3,
                '        "stale": inv.stale is not None,\n    }',
                '        "stale": inv.stale is not None,\n'
                '        "root": str(inv.app_root),\n    }',
            ),
            (
                TIER3,
                "    for (f, line), it in sorted(locs.items.items()):",
                "    for (f, line), it in locs.items.items():",
            ),
        ],
        [
            f"{T}::test_tier3_report_has_no_absolute_path_or_time",
            f"{T}::test_tier3_a_changed_scenario_points_at_its_route_the_handler_and_the_closure",
        ],
        "PD",
    ),
    "IX10": (
        "the report cached under the inventory",
        [
            (
                REPORT,
                'if __name__ == "__main__":',
                "_orig_build_report = build_report\n_CACHE: dict = {}\n\n\n"
                "def build_report(inp, **kw):\n"
                "    key = (str(inp.inventory.directory) if inp.inventory else None, "
                "kw.get('tiers'))\n"
                "    if key not in _CACHE:\n"
                "        _CACHE[key] = _orig_build_report(inp, **kw)\n"
                "    return _CACHE[key]\n\n\n"
                'if __name__ == "__main__":',
            )
        ],
        [f"{T}::test_the_report_is_not_cached_under_the_inventory"],
        "PW",
    ),
    "IX11": (
        "the Studio pipeline switched to the new runner",
        [
            (
                SERVER,
                'checks["impact"] = impact_json(items, self.last_check)',
                "from lattice.spec import impact_report as _ir\n"
                '                checks["impact"] = '
                '{"verdict": "pass", "failing": [], "via": _ir.SCHEMA}',
            )
        ],
        ["FROZEN"],
        "PB (change record), PS",
    ),
    "IX12": (
        "change_impact listed without [impact]",
        [(TOOLS, "        if _has_impact_table(self.root):", "        if True:")],
        [
            f"{M}::test_the_tool_exists_only_with_the_impact_table",
            f"{M}::test_without_the_table_tools_list_is_byte_identical",
        ],
        "PB",
    ),
    "IX13": (
        "no container cleanup on a check exception",
        [
            (
                REPORT,
                "            remove_owned_containers(token)\n        finally:",
                "            (remove_owned_containers(token) "
                "if sys.exc_info()[0] is None else None)\n"
                "        finally:",
            )
        ],
        [f"{T}::test_a_fault_mid_run_leaves_no_owned_container"],
        "LEAK fault run",
    ),
    "IX14": (
        "a flow's D failure counted as workflow_fails",
        [(REPORT, '"kind": "baseline_drift",', '"kind": "workflow_fails",')],
        [f"{T}::test_tier1_flows_are_run_and_classified"],
        "PT1f",
    ),
    "IX15": (
        "a null-target finding dropped",
        [
            (
                REPORT,
                '    for f in own:\n        obs = "static_checker"',
                "    for f in own:\n        if finding_target(spec, f) is None:\n"
                "            continue\n"
                '        obs = "static_checker"',
            )
        ],
        [f"{T}::test_tier1_keeps_a_finding_with_no_target"],
        "PT1 (an A or untargeted finding in a dev fixture)",
    ),
    "IX16": (
        "StateStore opened to read --change",
        [
            (
                REPORT,
                '    if not re.fullmatch(r"c\\d+", cid):',
                "    from lattice.studio.state import StateStore\n\n"
                "    StateStore(Path(state_dir))\n"
                '    if not re.fullmatch(r"c\\d+", cid):',
            )
        ],
        [f"{T}::test_reading_a_change_never_opens_the_state_store"],
        "PW (the state dir gains a meta.json write)",
    ),
}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def copy_tree(dest: Path) -> None:
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for name in COPY:
        src = REPO / name
        if src.is_dir():
            shutil.copytree(
                src, dest / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git")
            )
        else:
            shutil.copy2(src, dest / name)


def frozen_unchanged(tree: Path) -> list[str]:
    """Frozen files whose bytes differ from bb3ff0c's (PB: an empty diff)."""
    bad = []
    for rel in (
        SERVER,
        "lattice/spec/incremental.py",
        "lattice/spec/check.py",
        "lattice/spec/implementation.py",
    ):
        want = subprocess.run(
            ["git", "-C", str(REPO), "show", f"{BASE_COMMIT}:{rel}"],
            capture_output=True,
            check=True,
        ).stdout
        if sha((tree / rel).read_bytes()) != sha(want):
            bad.append(rel)
    return bad


def run_catchers(tree: Path, catchers: list[str]) -> tuple[bool, str]:
    """(all catchers pass, the tail of the output)."""
    ok, tail = True, ""
    env = dict(os.environ, PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", UV_NO_SYNC="1")
    env.pop("ANTHROPIC_API_KEY", None)
    tests = [c for c in catchers if c != "FROZEN"]
    if tests:
        p = subprocess.run(
            [sys.executable, "-m", "pytest", *tests, "-x", "-q", "-p", "no:cacheprovider"],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        ok = p.returncode == 0
        tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-6:])
    if "FROZEN" in catchers:
        diff = frozen_unchanged(tree)
        if diff:
            ok = False
            tail += f"\nfrozen files changed: {diff}"
    return ok, tail


def apply(tree: Path, edits: list[Edit]) -> None:
    for rel, old, new in edits:
        p = tree / rel
        text = p.read_text()
        if old not in text:
            raise AssertionError(f"{rel}: the text to mutate is gone: {old[:70]!r}")
        p.write_text(text.replace(old, new))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", help="IX1 ... IX16 (default: all)")
    ap.add_argument("--scratch", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    ids = args.ids or list(MUTANTS)
    scratch = (
        Path(args.scratch) if args.scratch else Path(tempfile.mkdtemp(prefix="impact-mutants-"))
    )
    scratch.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    # the control: the unmodified copy passes every catcher
    ctl = scratch / "control"
    copy_tree(ctl)
    every = sorted({c for i in ids for c in MUTANTS[i][2]})
    t0 = time.perf_counter()
    ok, tail = run_catchers(ctl, every)
    rows.append(
        {
            "id": "control",
            "pass": ok,
            "catchers": every,
            "tail": tail,
            "seconds": round(time.perf_counter() - t0, 1),
        }
    )
    print(f"control: {'ok' if ok else 'FAILED'}")
    if not ok:
        print(tail)
        _write(args.out, rows)
        return 1
    killed = 0
    for mid in ids:
        desc, edits, catchers, named = MUTANTS[mid]
        tree = scratch / mid
        copy_tree(tree)
        apply(tree, edits)
        t0 = time.perf_counter()
        passed, tail = run_catchers(tree, catchers)
        dead = not passed
        killed += dead
        rows.append(
            {
                "id": mid,
                "mutant": desc,
                "killed": dead,
                "catchers": catchers,
                "preregistered_catcher": named,
                "tail": tail,
                "seconds": round(time.perf_counter() - t0, 1),
            }
        )
        print(f"{mid}: {'KILLED' if dead else 'SURVIVED'} -- {desc} [{', '.join(catchers)}]")
        shutil.rmtree(tree, ignore_errors=True)
    print(f"{killed}/{len(ids)} killed")
    _write(args.out, rows)
    return 0 if killed == len(ids) else 1


def _write(out: str, rows: list[dict[str, Any]]) -> None:
    if out:
        Path(out).write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
