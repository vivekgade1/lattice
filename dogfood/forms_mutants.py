"""Mutants of the typed-conditions implementation (prereg 2026-10-17, section 4: HX1-HX16).

Each mutant is applied to a scratch copy of the tree (never the repository) and must be caught by
the named test of ``tests/spec/test_forms.py``. The control (the unmodified copy) runs the same
catchers first and must pass them. The pre-registration names probes (PG1, PT2f, PA, PB, PW ...)
as catchers; the probes that need the separate agent's frozen set cannot run here, so each mutant
is killed by a named unit test and the row says which probe it stands for (disclosed in the
results).

    uv run python dogfood/forms_mutants.py $(seq -f HX%g 1 16) [--scratch DIR] [--out FILE]
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
T = "tests/spec/test_forms.py"
FORMS = "lattice/spec/forms.py"
TIER2 = "lattice/spec/forms_tier2.py"
COPY_DIRS = ("lattice",)
COPY_FILES = (
    "tests/conftest.py",
    "tests/spec/__init__.py",
    "tests/spec/conftest.py",
    "tests/spec/minishop.py",
    "tests/spec/test_forms.py",
    "pyproject.toml",
)
COPY_TREES = ("tests/spec/fixtures/forms",)

Edit = tuple[str, str, str]
# id -> (description, edits, catchers (pytest node ids), the pre-registered catcher)
MUTANTS: dict[str, tuple[str, list[Edit], list[str], str]] = {
    "HX1": (
        "a refused step's db: unchanged dropped",
        [
            (
                FORMS,
                'return outcome if "db" in outcome else {**outcome, "db": "unchanged"}',
                "return outcome",
            )
        ],  # fmt: skip
        [f"{T}::test_hx1_a_refused_step_expects_the_database_unchanged"],
        "PG1 on a partial_write-like mutant; unit",
    ),
    "HX2": (
        "ge generated as gt (boundary off by one)",
        [
            (
                FORMS,
                '    if op == "ge":\n        return v, v - one',
                '    if op == "ge":\n        return v + one, v',
            )
        ],  # fmt: skip
        [
            f"{T}::test_hx2_ge_is_generated_at_the_bound_not_past_it",
            f"{T}::test_bound_values_int_float_in_len",
        ],
        "PG1 (at_bound fails on ok); unit",
    ),
    "HX3": (
        "INT tightening ignored (gt 3, lt 4 on INT satisfiable)",
        [
            (
                TIER2,
                '    if integer:\n        if op == "gt":',
                '    if False:\n        if op == "gt":',
            )
        ],
        [f"{T}::test_class_a_int_tightening_gt3_lt4_hx3"],
        "PT2f recall (class a)",
    ),
    "HX4": (
        "two limits with different where treated as the same count",
        [
            (
                TIER2,
                '    if sorted(a["by"]) != sorted(b["by"]) or F.canon(a.get("where") or {})'
                " != F.canon(\n"
                '        b.get("where") or {}\n    ):\n',
                '    if sorted(a["by"]) != sorted(b["by"]):\n',
            )
        ],  # fmt: skip
        [f"{T}::test_class_b_different_where_is_a_different_count_hx4"],
        "PT2f precision",
    ),
    "HX5": (
        "the ownership generator omits the anon case",
        [(FORMS, '                ("anon", None, form["refused_anon"]),\n', "")],
        [f"{T}::test_hx5_the_ownership_generator_has_the_anon_case_and_catches_the_auth_hole"],
        "PG1k on sec:auth (the test runs the generated check on the sec auth app)",
    ),
    "HX6": (
        "the transition generator omits the refused_<s> cases",
        [
            (
                FORMS,
                "        for s in sorted(set(states)):",
                "        for s in sorted(set(states) & set(frm)):",
            )
        ],  # fmt: skip
        [f"{T}::test_hx6_the_transition_generator_refuses_every_other_state"],
        "PG1k on sec:state-like mutants; unit",
    ),
    "HX7": (
        "a proposal generates checks",
        [
            (
                FORMS,
                "        return usable(self.resolved)",
                "        return [Resolved(f, 'current', f.rule) "
                "for f in [*self.approved, *self.proposals]]",
            )
        ],
        [f"{T}::test_a_proposal_generates_nothing_hx7"],
        "PA",
    ),
    "HX8": (
        "an edited approved form still generates",
        [(FORMS, "r.state in APPROVED_STATES]", 'r.state in (*APPROVED_STATES, "edited")]')],
        [f"{T}::test_an_edited_approved_form_generates_nothing_hx8"],
        "PA",
    ),
    "HX9": (
        'the check output gains "forms": null when no file exists',
        [
            (
                FORMS,
                '    if gen is not None:\n        d["forms"] = {"generated": gen.names()}',
                '    d["forms"] = None if gen is None else {"generated": gen.names()}',
            )
        ],  # fmt: skip
        [f"{T}::test_check_with_forms_without_a_forms_file_is_the_plain_check"],
        "PB",
    ),
    "HX10": (
        "generated names from set iteration order",
        [(FORMS, "        for s in sorted(set(states)):", "        for s in set(states):")],
        [f"{T}::test_generate_is_identical_under_different_hash_seeds_hx10"],
        "PG3",
    ),
    "HX11": (
        "a now() / sum node accepted",
        [
            (
                FORMS,
                "            if k not in allowed:",
                "            if k not in allowed and k not in NOT_EXPR_KEYS:",
            )
        ],  # fmt: skip
        [f"{T}::test_hx11_a_now_or_sum_node_is_refused"],
        "PNX, PG5",
    ),
    "HX12": (
        "contradiction items emitted without the witness self-check",
        [(TIER2, "            w = _witness_value(ca, cb, scalar)", '            w = (None, "a")')],
        [f"{T}::test_class_a_witness_self_check_suppresses_a_false_pair_hx12"],
        "PT2x (a planted false pair)",
    ),
    "HX13": (
        "row_limit below case given max rows instead of max - 1",
        [(FORMS, '("below", mx - 1, accepted)', '("below", mx, accepted)')],
        [f"{T}::test_hx13_the_below_case_has_one_row_fewer_than_the_limit"],
        "PG1o",
    ),
    "HX14": (
        "the generated count invariant uses < for <=",
        [(FORMS, 'expr += f") <= {mx}"', 'expr += f") < {mx}"')],
        [f"{T}::test_hx14_the_count_invariant_is_at_most_not_below"],
        "PG1o",
    ),
    "HX15": (
        "one_of membership not type-checked (F005 skipped)",
        [
            (
                FORMS,
                "if ref is not None and ref.one_of is not None and v not in ref.one_of:",
                "if False:",
            )
        ],  # fmt: skip
        [f"{T}::test_hx15_a_one_of_value_outside_the_column_is_refused"],
        "PG5",
    ),
    "HX16": (
        "generate writes a cache file next to the forms",
        [
            (
                FORMS,
                "                gen = generate(spec, use)\n",
                "                gen = generate(spec, use)\n"
                '                paths.forms.with_suffix(".cache").write_text("x")\n',
            )
        ],  # fmt: skip
        [f"{T}::test_generate_writes_no_cache_file_hx16"],
        "PW",
    ),
}


def copy_tree(dest: Path) -> None:
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".git")
    for name in COPY_DIRS + COPY_TREES:
        shutil.copytree(REPO / name, dest / name, ignore=ignore)
    for name in COPY_FILES:
        (dest / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, dest / name)


def apply(tree: Path, edits: list[Edit]) -> None:
    for rel, old, new in edits:
        p = tree / rel
        text = p.read_text()
        if text.count(old) != 1:
            raise AssertionError(f"{rel}: the text to mutate is not unique/gone: {old[:70]!r}")
        p.write_text(text.replace(old, new))


def run_catchers(tree: Path, catchers: list[str]) -> tuple[bool, str]:
    env = dict(os.environ, PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", UV_NO_SYNC="1")
    env.pop("ANTHROPIC_API_KEY", None)
    p = subprocess.run(
        [sys.executable, "-m", "pytest", *catchers, "-x", "-q", "-p", "no:cacheprovider"],
        cwd=tree, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    return p.returncode == 0, "\n".join((p.stdout + p.stderr).strip().splitlines()[-6:])


def _write(out: str, rows: list[dict[str, Any]]) -> None:
    if out:
        Path(out).write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("ids", nargs="*", help="HX1 ... HX16 (default: all)")
    ap.add_argument("--scratch", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    ids = args.ids or list(MUTANTS)
    scratch = (
        Path(args.scratch) if args.scratch else Path(tempfile.mkdtemp(prefix="forms-mutants-"))
    )
    scratch.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    ctl = scratch / "control"
    copy_tree(ctl)
    every = sorted({c for i in ids for c in MUTANTS[i][2]})
    t0 = time.perf_counter()
    ok, tail = run_catchers(ctl, every)
    rows.append({"id": "control", "pass": ok, "catchers": every, "tail": tail,
                 "seconds": round(time.perf_counter() - t0, 1)})  # fmt: skip
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
            }  # fmt: skip
        )
        print(f"{mid}: {'KILLED' if dead else 'SURVIVED'} -- {desc} [{', '.join(catchers)}]")
        shutil.rmtree(tree, ignore_errors=True)
    print(f"{killed}/{len(ids)} killed")
    _write(args.out, rows)
    return 0 if killed == len(ids) else 1


if __name__ == "__main__":
    raise SystemExit(main())
