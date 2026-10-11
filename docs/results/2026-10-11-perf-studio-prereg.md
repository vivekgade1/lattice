# Pre-registration: build performance in Studio (API v1.11), mutation run (2026-10-11)

Written before any mutant below was run. Phase: `docs/design/lattice-build-performance-phase.md`
(S2a: scoped gates, the regression level gate, the level repair loop, stop/budget hygiene,
planner/areas). Code frozen at these sha256[:16] (re-checked by the runner on the copy):

| file | hash |
|---|---|
| `lattice/studio/plan.py` | `4094664bd8469bd6` |
| `lattice/studio/rebuild.py` | `fc8ca50c31376397` |
| `lattice/studio/state.py` | `db6b187c8b441008` |
| `lattice/studio/areas.py` | `3f4c725b0ecd0011` |
| `lattice/studio/hub.py` | `6d5008d7749beddd` |
| `lattice/studio/server.py` | `247cd7258503d43b` |
| `dogfood/studio/spec.py` (39 scenarios) | `9a3249f55ab46d84` |
| `tests/studio/test_scoped_gates.py` | `bd5373978e2875fa` |
| `tests/studio/test_level_repair.py` | `51eb668eb6d68086` |
| `tests/studio/test_stop_cleanup.py` | `e9623a082f0514ef` |
| `dogfood/studio/perf_mutants.py` (the runner) | `9a125de4eff89481` |

On this code: `uv run pytest tests/studio tests/mcp tests/test_cli.py tests/spec -q
--ignore=tests/spec/test_db.py --ignore=tests/spec/test_multidb.py` = 933 passed, 5 skipped;
`cd dogfood/studio && ./check.sh` = `PASS: 0 findings (D 0, X 0); 313 cases run`.

## How a mutant runs

`uv run python dogfood/studio/perf_mutants.py --out <dir>`: `lattice/`, `tests/` and the
seed are copied to a scratch directory, ONE patch is applied to the copy (its old text must
occur exactly once, else the run aborts), then (a) the three new suites run with the copy
first on `PYTHONPATH` and (b) `./check.sh` (the dogfood) runs with
`LATTICE_STUDIO_PYTHONPATH=<copy>`. Caught = either exits non-zero. The earlier mutants run
against the dogfood only (as they ran then). A control run of the unpatched copy comes first;
three mutants at a time. The working tree is never patched. Separately,
`uv run python dogfood/studio/state_mutants.py` re-runs the six v1.10 mutants (S-M1..S-M6)
against `tests/studio/test_state_store.py` and `test_resume.py`, unchanged.

## The six new mutants (exact patches are in `perf_mutants.py`)

| id | behaviour | patch |
|---|---|---|
| P-M1 | level-end gate skipped when the node gate passed | `_merge_level`: `gates, counts, findings = rb.gate_round(irun, L + 1)` -> a fake passing gate with no findings |
| P-M2 | the node gate result is accepted as the level result | `_merge_level`: `failing = set(doc["failing"])` -> `failing = set(last_failing) - <the merged nodes' build targets>` |
| P-M3 | the scoped selection leaves out the regression set | `build_selection`: `chosen = list(dict.fromkeys([*scope, *reg]))` -> `chosen = list(dict.fromkeys(scope))` |
| P-M4 | a failing level gate is accepted without repair | `_merge_level`: `if bad or broken:` -> `if broken:` |
| P-M5 | repair attempts are not capped | `_level_end`: `if tries >= cap:` -> `if tries >= cap + 3:` (any bound above the configured cap is what a test can observe; an unbounded loop would only hang) |
| P-M6 | stop leaves the gate's app process running | `_run_gate`'s stop path: `terminate_tree(proc, list(children.values()), ...)` -> `terminate_tree(proc, [], ...)` (only the gate's own group is killed) |

## Predictions (blind)

Dogfood steps are numbered from 1 as the checker prints them. "tests" lists the tests
predicted to fail (at least these); a mutant counts as caught if anything fails.

| id | tests (predicted to fail) | dogfood (first failing scenario / step) |
|---|---|---|
| P-M1 | `test_level_repair`: race_only_and_cross_area, repairs_are_capped, one_attempt_cap, failure_no_node_owns, without_the_repair_loop; `test_scoped_gates`: without_scoped_args_nothing_changes (level 0's fake gate fails nothing, so level 1's nodes have nothing to build) | `a_level_gate_failure_is_repaired_and_lands` step 5 (passes with no repair at the first merged tip). `a_plan_runs_level_by_level` is predicted NOT to catch it: with the regression gate, unselected scenarios keep their failing verdict, so an empty gate looks like the true one there |
| P-M2 | `test_level_repair`: race_only_and_cross_area, repairs_are_capped, one_attempt_cap, failure_no_node_owns, without_the_repair_loop | `a_level_gate_failure_is_repaired_and_lands` step 5 |
| P-M3 | `test_scoped_gates`: the_selection_is_the_scope_plus_the_regression_set, the_regression_set_catches_a_regression_in_the_node_gate (round 1 no longer sees the deleted writing.txt: 1 round, not 2), the_integration_node_and_a_full_level_gate_are_unscoped (`scenarios` 2, not 3) | `a_plan_runs_level_by_level` step 7 (`GET /api/rebuild/b2`: `scenarios` 2, not 3); also `a_level_gate_failure_is_repaired_and_lands` step 7 |
| P-M4 | `test_level_repair`: race_only_and_cross_area, repairs_are_capped, one_attempt_cap, failure_no_node_owns, without_the_repair_loop | `a_level_gate_failure_is_repaired_and_lands` step 5 (lands at the racy, clashing tip) |
| P-M5 | `test_level_repair`: repairs_are_capped (attempt 3 reaches the stub's fixing round: `passed`, not `partial` after [1, 2]), one_attempt_cap_is_exactly_one | not caught (no dogfood scenario exhausts the repairs: R54 is Unchecked there) |
| P-M6 | `test_stop_cleanup`: stop_kills_the_gates_whole_tree, a_gate_timeout_kills_the_tree_too, a_plan_stop_during_a_gate_kills_its_tree | not caught (processes are not observable over HTTP: R69 is Unchecked there) |

**Prediction: all six caught** (P-M1..P-M4 by both the tests and the dogfood, P-M5 and P-M6
by the tests only).

## Earlier mutants re-run (code this phase touched)

Against the dogfood only, with the original patch strings (each must still occur once):
B-M1, B-M2, B-M5 (build-from-spec), R-M2 (rebuild), BG-P-M3 (build-graph P-M3: the level
gate skipped) and BG-P-M6 (build-graph P-M6, **adapted**: the `update-ref` of the target to
the failing merged tip now sits before the failing level's `return {"failed": True, ...}`,
whose text changed). Predictions: B-M1 `scoped_builds_accumulate_on_the_integration_branch`
step 2; B-M2 the same scenario step 7; B-M5 `scoped_build_refusals` step 5; R-M2
`a_rebuild_passes_in_round_2_on_its_own_branch` step 8; BG-P-M3
`a_level_gate_failure_is_repaired_and_lands` step 5 (no longer `a_plan_runs_level_by_level`,
for the reason given under P-M1); BG-P-M6 `a_level_gate_failure_is_repaired_and_lands` step 5
(the repaired tip cannot land: the target moved). S-M1..S-M6: caught, as in
`docs/results/2026-10-11-state-resume-results.md`.

**Prediction: every earlier mutant stays caught.** Any survivor is reported as a survivor.
Tests or scenarios added or changed after a result are reported as post hoc and do not
count toward the blind result.
