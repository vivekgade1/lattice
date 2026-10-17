"""Mutants of `lattice spec implement` (docs/results/2026-10-17-implement-verify-prereg.md, s. 5).

Each mutant is applied to a scratch copy of the tree (never the repository) and must be caught by
the catchers named for it: pytest nodes of tests/spec/test_impact_tier4.py and
tests/studio/test_implement.py. The control (the unmodified copy) runs the same catchers first and
must pass them. Where the pre-registration names a probe (PV, PR, PU, PT2, PSB, PT3E, PH, PG, PNM,
PSC, PC, PD, PBC) as the catcher, the named test stands for it and the row says so (disclosed in
the results): the tests exercise the same code paths on the same mini-shop corpus.

    uv run python dogfood/implement_mutants.py $(seq -f EM%g 1 16) [--scratch DIR] [--out FILE]
"""

from __future__ import annotations

import argparse
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
IMPL = "lattice/studio/implement.py"
TIER4 = "lattice/spec/impact_tier4.py"
U = "tests/spec/test_impact_tier4.py"
E = "tests/studio/test_implement.py"
COPY = ("lattice", "tests", "dogfood", "pyproject.toml")

Edit = tuple[str, str, str]
# id -> (description, [(file, old, new), ...], catchers, the pre-registered catcher)
MUTANTS: dict[str, tuple[str, list[Edit], list[str], str]] = {
    "EM1": (
        "Tier 4 built from the before run only (no after run)",
        [(IMPL, 'after = _side(top, runner, b_sha, after_wt, flows, "after")', "after = before")],
        [
            f"{E}::test_an_unexpected_regression_on_an_uncovered_route_fails_the_gate_i3_i4",
            f"{E}::test_i1_resolves_the_failing_scenario_and_the_flow_that_repeats_it_changes_as_reported",
        ],
        "PV i3/i4, PR",
    ),
    "EM2": (
        "after run on S instead of B",
        [
            (
                IMPL,
                'after = _side(top, runner, b_sha, after_wt, flows, "after")',
                'after = _side(top, runner, s_sha, after_wt, flows, "after")',
            )
        ],
        [
            f"{E}::test_i1_resolves_the_failing_scenario_and_the_flow_that_repeats_it_changes_as_reported"
        ],
        "PR (resolved empty on i1)",
    ),
    "EM3": (
        "unreported flow changes counted as t3_may_fail",
        [
            (
                TIER4,
                '                else "t3_may_fail"\n'
                '                if f"flow:{fid}" in t3_flows\n'
                '                else "unreported"\n',
                '                else "t3_may_fail"\n',
            )
        ],
        [f"{E}::test_an_unexpected_regression_on_an_uncovered_route_fails_the_gate_i3_i4"],
        "PU",
    ),
    "EM4": (
        "the verdict ignores regression",
        [(TIER4, '        or counts["regressions"]\n', "")],
        [f"{U}::test_regression_fails", f"{U}::test_the_verdict_table"],
        "unit regression_fails (a synthetic after run)",
    ),
    "EM5": (
        "git merge rebuild/<eid> in the owner's repo after a pass",
        [
            (
                IMPL,
                '            hint = rec.get("merge_hint")\n',
                '            hint = rec.get("merge_hint")\n'
                '            if rec["status"] == "passed":\n'
                '                _git(top, "merge", "--no-edit", "-q", rec["branch"])\n',
            )
        ],
        [f"{E}::test_a_passed_build_is_not_merged_even_when_nothing_blocks_a_fast_forward"],
        "PNM, PG",
    ),
    "EM6": (
        "the spec commit also moves the owner's current branch",
        [
            (
                IMPL,
                '        shas["spec"] = s_sha\n',
                '        shas["spec"] = s_sha\n        _git(top, "update-ref", "HEAD", s_sha)\n',
            )
        ],
        [f"{E}::test_the_owners_files_refs_and_index_are_untouched[i1]"],
        "PG, PH",
    ),
    "EM7": (
        "the report hash check skipped",
        [
            (TIER4, 'if inp.get("base") != dict(base):', "if False:"),
            (TIER4, 'if inp.get("candidate") != dict(cand):', "if False:"),
            (TIER4, "if rec.get(key) != inventory.get(key):", "if False:"),
        ],
        [
            f"{U}::test_report_mismatch_on_base_candidate_and_inventory_bytes",
            f"{E}::test_refusals_bad_report_and_report_mismatch",
        ],
        "PH (report_mismatch x3)",
    ),
    "EM8": (
        "git add -A in the owner's checkout (the whole project dir) before the spec commit",
        [
            (
                IMPL,
                '        _git_ok(spec_wt, "add", "--", *paths)\n',
                '        _git_ok(top, "add", "-A")\n'
                '        _git_ok(spec_wt, "add", "--", *paths)\n',
            )
        ],
        [f"{E}::test_an_untracked_owner_file_never_reaches_the_spec_commit"],
        "PSC (a fixture with an untracked owner file)",
    ),
    "EM9": (
        "the merge also rewrites summary",
        [
            (
                TIER4,
                '    out["tier4"] = dict(tier4)\n    return out',
                '    out["tier4"] = dict(tier4)\n    out["summary"] = {}\n    return out',
            )
        ],
        [
            f"{U}::test_merge_changes_exactly_tiers4_and_tier4",
            f"{E}::test_the_output_is_the_input_report_but_for_two_keys",
        ],
        "PBC (report minus T4 byte-equal)",
    ),
    "EM10": (
        "nothing_to_build starts a rebuild anyway",
        [(IMPL, "if not t4.failing_set(before) and not t2_claims:", "if False:")],
        [f"{E}::test_the_dev_changes_end_as_predicted[i5]"],
        "PV i5-i7",
    ),
    "EM11": (
        "t3_vs_edit counts test files and stub-rebuild.json",
        [(TIER4, "if n not in skip and not _TEST_FILE.search(n)", "if True")],
        [f"{U}::test_t3_vs_edit_counts_on_four_synthetic_diffs"],
        "PT3E",
    ),
    "EM12": (
        "an absolute worktree path in tier4",
        [
            (
                IMPL,
                '                    "branch_build": rec["branch"],\n'
                '                    "build_tree"',
                '                    "branch_build": rec["branch"],\n'
                '                    "worktree": rec.get("worktree"),\n'
                '                    "build_tree"',
            )
        ],
        [f"{E}::test_the_output_names_no_path_time_host_or_commit"],
        "PD",
    ),
    "EM13": (
        "spec_backed matches by route only (ignores user and status)",
        [
            (
                TIER4,
                '            and (cs.get("user") or None) == (step.get("caller") or None)\n'
                '            and cs.get("expect_status") == status\n',
                "",
            )
        ],
        [f"{U}::test_spec_backed_the_eight_cases"],
        "PSB",
    ),
    "EM14": (
        "the after-run worktree is not removed",
        [
            (
                IMPL,
                "    finally:\n        _worktree_remove(top, wt)\n\n\ndef _wait(",
                '    finally:\n        if which == "before":\n'
                "            _worktree_remove(top, wt)\n\n\ndef _wait(",
            ),
            (IMPL, "for wt in (spec_wt, before_wt, after_wt):", "for wt in (spec_wt, before_wt):"),
        ],
        [f"{E}::test_cleanup_leaves_the_review_worktree_only_when_a_build_ran"],
        "PC",
    ),
    "EM15": (
        "a contradicted T2 flow claim is not flagged",
        [(TIER4, '"confirmed": fid in changed,', '"confirmed": True,')],
        [f"{U}::test_a_planted_t2_flow_claim_on_an_unchanged_flow_is_contradicted"],
        "PT2",
    ),
    "EM16": (
        "a gave_up build gets an after run (on S) and a verdict",
        [
            (IMPL, 'if rec["status"] != "passed":', "if False:"),
            (
                IMPL,
                'b_sha = rec["commit"]["sha"]',
                'b_sha = (rec.get("commit") or {}).get("sha") or s_sha',
            ),
        ],
        [
            f"{E}::test_the_dev_changes_end_as_predicted[i8]",
            f"{E}::test_the_dev_changes_end_as_predicted[i9]",
        ],
        "PV i8/i9",
    ),
}


def copy_tree(dest: Path) -> None:
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for name in COPY:
        src = REPO / name
        if src.is_dir():
            shutil.copytree(
                src,
                dest / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".venv"),
            )
        else:
            shutil.copy2(src, dest / name)


def apply(tree: Path, edits: list[Edit]) -> None:
    for rel, old, new in edits:
        p = tree / rel
        text = p.read_text()
        if text.count(old) != 1:
            raise AssertionError(
                f"{rel}: the text to mutate occurs {text.count(old)}x: {old[:70]!r}"
            )
        p.write_text(text.replace(old, new))


def run_catchers(tree: Path, catchers: list[str]) -> tuple[bool, str]:
    """(all catchers pass, the tail of the output)."""
    env = dict(os.environ, PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", UV_NO_SYNC="1")
    env.pop("ANTHROPIC_API_KEY", None)
    p = subprocess.run(
        [sys.executable, "-m", "pytest", *catchers, "-q", "-p", "no:cacheprovider", "-x"],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-6:])
    return p.returncode == 0, tail


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", help="EM1 ... EM16 (default: all)")
    ap.add_argument("--scratch", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    ids = args.ids or list(MUTANTS)
    scratch = Path(args.scratch) if args.scratch else Path(tempfile.mkdtemp(prefix="impl-mutants-"))
    scratch.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
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
    shutil.rmtree(ctl, ignore_errors=True)
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
