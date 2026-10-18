# ruff: noqa: E501
"""Mutants of the round-2 implementation (prereg 2026-10-17 round 2, section 4): HY1-HY16 are new, HX1-HX16 are
the round-1 mutants re-applied to the round-2 code (``dogfood/forms_mutants.py`` is not edited; its table is
imported, and a mutant whose text no longer applies is re-expressed here and disclosed).

Each mutant is applied to a scratch copy of the tree (never the repository) and must be caught by a named test
of ``tests/spec/test_forms_r2.py`` (HY) or ``tests/spec/test_forms.py`` (HX). The control (the unmodified copy)
runs the same catchers first and must pass them. The pre-registration names probes (PC, PG1, PNX, PT2w ...) as
catchers; the probes that need the commissioned frozen set cannot run here, so each mutant is killed by a named
unit test and the row says which probe it stands for (disclosed in the results).

    uv run python dogfood/forms_mutants2.py $(seq -f HY%g 1 16) $(seq -f HX%g 1 16) [--scratch DIR] [--out FILE]
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


def _load_r1() -> Any:
    spec = importlib.util.spec_from_file_location(
        "forms_mutants_r1", REPO / "dogfood/forms_mutants.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R1 = _load_r1()
T = "tests/spec/test_forms_r2.py"
FORMS = "lattice/spec/forms.py"
REPORT = "lattice/spec/impact_report.py"
R1.COPY_FILES = (*R1.COPY_FILES, "tests/spec/test_forms_r2.py")
R1.COPY_TREES = (
    "tests/spec/fixtures/forms",
    "tests/spec/fixtures/forms_r2",
    "tests/spec/fixtures/impact",
)

Edit = tuple[str, str, str]
# id -> (description, edits, catchers (pytest node ids), the pre-registered catcher)
HY: dict[str, tuple[str, list[Edit], list[str], str]] = {
    "HY1": (
        "untagged condition nodes refused (0)",
        [
            (
                FORMS,
                '    if not isinstance(c, dict) or "node" in c:\n        return c\n    op = c.get("op")',
                '    if True:\n        return c\n    op = c.get("op")',
            )
        ],
        [
            f"{T}::test_hy1_untagged_conditions_are_not_refused",
            f"{T}::test_pc0_the_tag_is_inferred_from_the_keys",
        ],
        "PC-0, PG5 valid refused",
    ),
    "HY2": (
        "approved_sha computed over the normalised (tagged) form (0)",
        [
            (
                FORMS,
                "self.approved_sha == form_sha(self.kind, self.form)",
                "self.approved_sha == form_sha(self.kind, normalised(self).form)",
            )
        ],
        [f"{T}::test_hy2_approved_sha_is_over_the_form_as_written"],
        "PA: every untagged form reads edited",
    ),
    "HY3": (
        "accepted status not derived, fixed 200 (1)",
        [
            (
                FORMS,
                "        status = accepted_status(self.spec, named)\n",
                "        status = None\n",
            )
        ],
        [
            f"{T}::test_hy3_the_derived_status_is_used_not_a_fixed_200",
            f"{T}::test_pc1_the_accepted_status_comes_from_the_specs_own_step",
        ],
        "PC-1, PG1o",
    ),
    "HY4": (
        "two different 2xx statuses: the first is taken instead of the 200-plus-note fallback (1)",
        [
            (
                FORMS,
                "        return next(iter(got)) if len(got) == 1 else None",
                "        return sorted(got)[0]",
            )
        ],
        [f"{T}::test_hy4_two_different_2xx_statuses_fall_back_to_200_and_a_note"],
        "unit",
    ),
    "HY5": (
        "ownership path row used as a value (round-1 behaviour) (2)",
        [
            (
                FORMS,
                '    row = _row_key(form["row"], [self.named(e) for e in form["endpoints"]])',
                '    row = form["row"]',
            )
        ],
        [
            f"{T}::test_hy5_a_path_row_is_not_used_as_a_value",
            f"{T}::test_pc2_a_request_path_row_is_the_key_of_every_endpoint",
        ],
        "PC-2, PG1o",
    ),
    "HY6": (
        "the key taken from the last placeholder, not the first (2)",
        [
            (
                FORMS,
                "first = next((i for i, t in enumerate(ts) if _PLACEHOLDER.fullmatch(t)), None)",
                "first = max((i for i, t in enumerate(ts) if _PLACEHOLDER.fullmatch(t)), default=None)",
            )
        ],
        [f"{T}::test_hy6_the_key_is_the_value_bound_to_the_first_placeholder"],
        "unit (nested route /orders/{}/items/{})",
    ),
    "HY7": (
        "transition row not completed (3)",
        [
            (
                FORMS,
                "            return named, w, found, row\n",
                '            return named, w, found, dict(form["row"])\n',
            )
        ],
        [
            f"{T}::test_hy7_the_transition_row_is_completed_so_the_given_row_inserts",
            f"{T}::test_pc3_the_transition_row_is_completed",
        ],
        "PC-3, PG1o (an IntegrityError on ok)",
    ),
    "HY8": (
        "completion puts another user's id in the owner column (3, C)",
        [
            (
                FORMS,
                "        v = _user_id(spec, caller) if col == schema.owner else _witness_col(w, named, col)",
                "        v = _user_id(spec, next(h for h in spec.users if h != caller)) if col == schema.owner else _witness_col(w, named, col)",
            )
        ],
        [f"{T}::test_hy8_the_owner_column_gets_the_callers_id"],
        "PG1k on an ownership mutant; unit (re-expressed: ownership does not synthesise rows)",
    ),
    "HY9": (
        "given rows of the counted table replaced, not counted (round-1 template semantics) (4)",
        [
            (
                FORMS,
                "{table: [*have, *rows]} if have or rows else None",
                "{table: rows} if rows else None",
            )
        ],
        [
            f"{T}::test_hy9_given_counted_rows_are_not_replaced",
            f"{T}::test_pc4_given_counted_rows_count_toward_the_limit_and_are_kept",
        ],
        "PC-4",
    ),
    "HY10": (
        "rule B step (2) (foreign key to the placeholder's table) off (4)",
        [
            (
                FORMS,
                "    if ph is not None and (tgt := _fk_target(spec, schema, col)) and tgt == _ph_table(spec, named):",
                "    if False:",
            )
        ],
        [
            f"{T}::test_hy10_a_by_column_is_bound_to_the_first_placeholder",
            f"{T}::test_pc4_counted_rows_are_synthesised_from_a_parent_only_given",
        ],
        "PC-4, PG5 valid refused (F021)",
    ),
    "HY11": (
        "an incomplete row is not F021 at check (generate raises later instead) (4)",
        [
            (
                FORMS,
                '        if len(self.diags) == n and f.kind in ("row_limit", "transition"):',
                "        if False:",
            )
        ],
        [f"{T}::test_hy11_an_incomplete_form_is_f021_at_check_and_never_a_raise_at_generate"],
        "PG4 0 raises, PT2f 0 raises",
    ),
    "HY12": (
        "param bound without stores refused F002 (round-1) (5)",
        [
            (
                FORMS,
                '                self.outcome(f"{ptr}/{k}", form[k])\n\n    def _row_limit(',
                '                self.outcome(f"{ptr}/{k}", form[k])\n'
                '                db = form[k].get("db") if isinstance(form[k], dict) else None\n'
                "                if db is not None and (ref is None or ref.table is None):\n"
                '                    self.err("F002", f"{ptr}/{k}/db", "a db expectation needs a table")\n\n'
                "    def _row_limit(",
            )
        ],
        [f"{T}::test_hy12_a_param_bound_without_stores_is_not_refused"],
        "PC-5, PG5",
    ),
    "HY13": (
        "derived locus dropped: a refused param step asserts no db effect (5)",
        [
            (
                FORMS,
                "            table = derive_table(self.spec, named, base)",
                "            table = None",
            )
        ],
        [
            f"{T}::test_hy13_the_derived_table_is_used",
            f"{T}::test_pc5_a_param_bound_without_stores_is_accepted_and_asserts_unchanged_on_the_derived_table",
        ],
        "PG1 on a refused-write mutant; unit",
    ),
    "HY14": (
        "literal typed before node (round-1 order) (7)",
        [
            (
                FORMS,
                'self.err("F017" if _names_node(v) else "F001", ptr, "not a literal")',
                'self.err("F004", ptr, "not a literal")',
            )
        ],
        [
            f"{T}::test_hy14_the_node_is_checked_before_the_literal_is_typed",
            f"{T}::test_pc7_a_value_that_is_a_node_name_is_f017",
        ],
        "PC-7, PNX",
    ),
    "HY15": (
        "impact_report.py never passes forms_items (round-1)",
        [(REPORT, '**({"forms_items": fi} if (fi := forms_items(base, cand)) else {}),', "**{},")],
        [f"{T}::test_hy15_the_impact_report_gains_exactly_the_candidates_new_form_items"],
        "PT2w",
    ),
    "HY16": (
        "impact_report.py builds forms items with no forms file (adds an item)",
        [
            (
                REPORT,
                "    if base.forms is None and cand.forms is None:\n        return None\n",
                '    if base.forms is None and cand.forms is None:\n        return lambda: [{"kind": "form_contradiction", "id": "x"}]\n',
            )
        ],
        [f"{T}::test_hy16_without_a_forms_file_the_report_is_the_plain_one"],
        "PB (D's 7 fixtures)",
    ),
}
MUTANTS: dict[str, tuple[str, list[Edit], list[str], str]] = {**HY, **R1.MUTANTS}


def _write(out: str, rows: list[dict[str, Any]]) -> None:
    if out:
        Path(out).write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("ids", nargs="*", help="HY1 ... HY16 HX1 ... HX16 (default: all)")
    ap.add_argument("--scratch", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    ids = args.ids or list(MUTANTS)
    scratch = (
        Path(args.scratch) if args.scratch else Path(tempfile.mkdtemp(prefix="forms-mutants2-"))
    )
    scratch.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    ctl = scratch / "control"
    R1.copy_tree(ctl)
    every = sorted({c for i in ids for c in MUTANTS[i][2]})
    t0 = time.perf_counter()
    ok, tail = R1.run_catchers(ctl, every)
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
        R1.copy_tree(tree)
        R1.apply(tree, edits)
        t0 = time.perf_counter()
        passed, tail = R1.run_catchers(tree, catchers)
        dead = not passed
        killed += dead
        rows.append({"id": mid, "mutant": desc, "killed": dead, "catchers": catchers,
                     "preregistered_catcher": named, "tail": tail,
                     "seconds": round(time.perf_counter() - t0, 1)})  # fmt: skip
        print(f"{mid}: {'KILLED' if dead else 'SURVIVED'} -- {desc} [{', '.join(catchers)}]")
        shutil.rmtree(tree, ignore_errors=True)
    print(f"{killed}/{len(ids)} killed")
    _write(args.out, rows)
    return 0 if killed == len(ids) else 1


if __name__ == "__main__":
    raise SystemExit(main())
