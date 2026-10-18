# ruff: noqa: E501
"""Mutants of the round-3 implementation (prereg 2026-10-18 round 3, section 6): HZ1-HZ15 are new, HY1-HY16 (round 2)
and HX1-HX16 (round 1) are re-applied (``dogfood/forms_mutants2.py`` and ``forms_mutants.py`` are not edited; their
tables are imported, and a mutant whose text no longer applies is re-expressed here and disclosed).

Each mutant is applied to a scratch copy of the tree (never the repository) and must be caught by a named test of
``tests/spec/test_forms_r3.py`` (HZ), ``tests/spec/test_forms_r2.py`` (HY) or ``tests/spec/test_forms.py`` (HX). The
control (the unmodified copy) runs the same catchers first and must pass them. The pre-registration names probes
(PC3, PR1, PD2, PH ...) as catchers; the probes that need the commissioned frozen set or a full run cannot be the
catcher of record here, so each mutant is killed by a named unit test and the row says which probe it stands for
(disclosed in the results). HZ15 needs ``runner3.sha256`` (written at the freeze), so the mutants run after it.

    uv run python dogfood/forms_mutants3.py $(seq -f HZ%g 1 15) $(seq -f HY%g 1 16) $(seq -f HX%g 1 16) [--scratch DIR] [--out FILE]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]


def _load_r2() -> Any:
    spec = importlib.util.spec_from_file_location(
        "forms_mutants_r2", REPO / "dogfood/forms_mutants2.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R2 = _load_r2()
R1 = R2.R1
T = "tests/spec/test_forms_r3.py"
FORMS = "lattice/spec/forms.py"
TIER2 = "lattice/spec/forms_tier2.py"
PROBES3 = "dogfood/forms_probes3.py"
DATA = "docs/results/data/2026-10-18-typed-conditions-round3"
R1.COPY_FILES = (
    *R1.COPY_FILES,
    T,
    "dogfood/forms_probes.py",
    PROBES3,
    "dogfood/forms_reader3.py",
    "dogfood/forms_mutants3.py",
    f"{DATA}/runner3.sha256",
)
R1.COPY_TREES = (*R1.COPY_TREES, "tests/spec/fixtures/forms_r3")

Edit = tuple[str, str, str]
# id -> (description, edits, catchers (pytest node ids), the pre-registered catcher)
HZ: dict[str, tuple[str, list[Edit], list[str], str]] = {
    "HZ1": (
        "a path bound emits no case (round-2 behaviour) (a)",
        [(FORMS, "    if len(cs) != len(ts) or ph not in ts:\n", "    if True:\n")],
        [
            f"{T}::test_pc3_a_path_placeholder_bound_substitutes_the_value_at_the_placeholder",
            f"{T}::test_hz1_a_path_bound_emits_both_cases_and_no_note",
        ],
        "PC3, PD2 (m_read_clamp x R9), unit",
    ),
    "HZ2": (
        "beyond uses the bound value itself (off by one) (a)",
        [
            (
                FORMS,
                "            w = _place(base, where, name, value, named) if where else None\n",
                "            value = at if where == 'path' else value\n            w = _place(base, where, name, value, named) if where else None\n",
            )
        ],
        [f"{T}::test_hz2_beyond_is_the_value_one_step_past_the_bound"],
        "PC3, unit",
    ),
    "HZ3": (
        "substitution into the last placeholder, not the named one (nested route) (a)",
        [
            (
                FORMS,
                "    cs[ts.index(ph)] = str(value)\n",
                "    cs[max(i for i, t in enumerate(ts) if t.startswith('{'))] = str(value)\n",
            )
        ],
        [f"{T}::test_hz3_the_named_placeholder_is_substituted_not_the_last_or_the_first"],
        "unit (PC3 nested case)",
    ),
    "HZ4": (
        "a path bound's at_bound expects the refused outcome (a)",
        [
            (
                FORMS,
                '            ("at_bound", at, accepted),\n',
                '            ("at_bound", at, _refused(refused) if where == "path" else accepted),\n',
            )
        ],
        [f"{T}::test_hz4_the_at_bound_case_of_a_path_bound_expects_the_accepted_outcome"],
        "unit (PG1o-style on ok), PD2",
    ),
    "HZ5": (
        "a path bound's beyond asserts no db effect even when a locus is derivable (a)",
        [
            (
                FORMS,
                '            if where == "path" and tf and first == [name]',
                "            if False and tf and first == [name]",
            )
        ],
        [f"{T}::test_hz5_the_beyond_case_of_a_path_bound_asserts_the_derived_locus"],
        "PD2 (m_cancel_clamp x R10), unit",
    ),
    "HZ6": (
        "a query-key bound is ignored (path only) (a)",
        [
            (
                FORMS,
                '    if where == "query":\n        out.params = dict(out.params or {})\n',
                '    if where == "query_ignored":\n        out.params = dict(out.params or {})\n',
            )
        ],
        [f"{T}::test_hz6_a_query_key_bound_is_placed_in_the_query"],
        "PC3 query case",
    ),
    "HZ7": (
        "a path value the type cannot hold is not refused F021 at check (a)",
        [
            (
                FORMS,
                '            if any(v is not None and re.search(r"^$|[/?#]", str(v)) for v in vals):\n',
                "            if False:\n",
            )
        ],
        [f"{T}::test_hz7_a_path_value_the_placeholder_cannot_hold_is_f021_at_check_not_a_raise"],
        "PG4 (0 raises), unit; re-expressed: a raise at generate cannot be injected by one text edit",
    ),
    "HZ8": (
        "outcome db.equals typed before node (round-2 order) (d)",
        [
            (
                FORMS,
                '                    code = "F017" if _names_node(v) else "F001"\n',
                '                    code = "F004"\n',
            )
        ],
        [
            f"{T}::test_hz8_the_node_is_checked_before_the_outcome_value_is_called_a_literal",
            f"{T}::test_pc3_an_outcome_db_value_that_is_a_node_name_is_f017_and_another_non_literal_f001",
        ],
        "PNX on PD2 (N12, N13), PC3",
    ),
    "HZ9": (
        "cause 7 applied to db.equals but not to given rows (d)",
        [
            (
                FORMS,
                "            return self.value_lit(ptr, scalar, v)\n",
                "            self.err('F004', ptr, 'not a literal')\n            return False\n",
            )
        ],
        [f"{T}::test_hz9_given_row_values_follow_the_same_order"],
        "unit",
    ),
    "HZ10": (
        "the (b) fix reverted: an ownership refusal's db assertion is dropped silently (b)",
        [
            (
                FORMS,
                '                    self.notes.append(f"{f.id}: db_unchecked:{case}_{slug}")\n',
                "                    pass\n",
            )
        ],
        [f"{T}::test_hz10_a_refusal_with_no_assertable_db_is_a_note"],
        "unit (Amendment 2: replaces the 30-line owner-row synthesis); PD2 reports m_cancel_sneaky x R19 as K7",
    ),
    "HZ11": (
        "the (c) fix reverted: counted rows share the value on a sibling-counted column (c)",
        [
            (
                FORMS,
                "        spread = sorted((sib & set(proto)) - set(locus) - {key})\n",
                "        spread: list[str] = []\n",
            )
        ],
        [
            f"{T}::test_hz11_counted_rows_are_pairwise_distinct_on_a_column_a_sibling_row_limit_counts_by"
        ],
        "PD2 (m_pair2 x R15), unit",
    ),
    "HZ12": (
        "class e skips the cases that came from a path or query bound (e)",
        [
            (
                TIER2,
                "    for fid, (gen, rule) in gens.items():\n",
                "    for fid, (gen, rule) in gens.items():\n        if str(next(f for f, _ in forms if f.id == fid).form.get('field', {}).get('path', '')).startswith(('path.', 'query.')):\n            continue\n",
            )
        ],
        [f"{T}::test_hz12_class_e_compares_a_path_bound_case_with_an_ownership_case"],
        "PT2e, PD2 (P24, P25)",
    ),
    "HZ13": (
        "class e reports two cases on different paths as one request (e)",
        [
            (
                TIER2,
                "                req = canon_request(st.method, st.path, st.user, st.json, st.params)\n                entries.append(",
                "                req = canon_request(st.method, '', st.user, st.json, st.params)\n                entries.append(",
            )
        ],
        [f"{T}::test_hz13_two_cases_on_different_paths_are_not_one_request"],
        "PT2f precision on PD2 / dev pairs, unit",
    ),
    "HZ14": (
        "forms generate adds a key (path_bound) to every scenario view (byte-compat)",
        [
            (
                FORMS,
                '        "name": s.name,\n        "given": s.given,\n',
                '        "name": s.name,\n        "path_bound": False,\n        "given": s.given,\n',
            )
        ],
        [f"{T}::test_hz14_the_generated_view_has_no_new_key"],
        "PR1, PB",
    ),
    "HZ15": (
        "runner: a scorer edit after the freeze (one limit changed)",
        [(PROBES3, "ARM_LIMIT_S = 240\n", "ARM_LIMIT_S = 241\n")],
        [f"{T}::test_hz15_the_runner_files_match_runner3_sha256"],
        "PH (runner3.sha256 mismatch aborts the frozen stage)",
    ),
}
MUTANTS: dict[str, tuple[str, list[Edit], list[str], str]] = {**HZ, **R2.MUTANTS}


def _write(out: str, rows: list[dict[str, Any]]) -> None:
    if out:
        Path(out).write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("ids", nargs="*", help="HZ1 ... HZ15 HY1 ... HY16 HX1 ... HX16 (default: all)")
    ap.add_argument("--scratch", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    ids = args.ids or list(MUTANTS)
    scratch = (
        Path(args.scratch) if args.scratch else Path(tempfile.mkdtemp(prefix="forms-mutants3-"))
    )
    scratch.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    ctl = scratch / "control"
    R1.copy_tree(ctl)
    every = sorted({c for i in ids for c in MUTANTS[i][2]})
    t0 = time.perf_counter()
    ok, tail = R1.run_catchers(ctl, every)
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
        R1.copy_tree(tree)
        try:
            R1.apply(tree, edits)
        except AssertionError as err:
            rows.append(
                {
                    "id": mid,
                    "mutant": desc,
                    "killed": False,
                    "not_applied": str(err),
                    "catchers": catchers,
                }
            )
            print(f"{mid}: NOT APPLIED -- {err}")
            shutil.rmtree(tree, ignore_errors=True)
            continue
        t0 = time.perf_counter()
        passed, tail = R1.run_catchers(tree, catchers)
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


if __name__ == "__main__":
    raise SystemExit(main())
