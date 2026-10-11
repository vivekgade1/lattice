# Results: build performance in Studio (API v1.11), mutation run and Streak measurements (2026-10-11)

Pre-registration: `docs/results/2026-10-11-perf-studio-prereg.md` (written before any mutant
ran). Runner: `dogfood/studio/perf_mutants.py` (patched copies of the frozen package; hashes
re-checked by the runner and equal to the pre-registered ones: `plan.py` `4094664bd8469bd6`,
`rebuild.py` `fc8ca50c31376397`, `state.py` `db6b187c8b441008`, dogfood spec
`9a3249f55ab46d84`), three mutants at a time; `dogfood/studio/state_mutants.py` for the v1.10
mutants. The working tree was never patched; every pre-registered patch string occurred
exactly once (BG-P-M6 adapted as pre-registered).

## Blind result (the pre-registered run)

Control (unpatched copy): the three new suites pass (27 tests), the dogfood
`PASS: 0 findings (D 0, X 0); 313 cases run`.

| id | behaviour | tests failing | dogfood first failure | as predicted? |
|---|---|---|---|---|
| P-M1 | level-end gate skipped when the node gate passed | 6: race_only_and_cross_area, repairs_are_capped, one_attempt_cap, failure_no_node_owns, without_the_repair_loop, without_scoped_args_nothing_changes | `a_level_gate_failure_is_repaired_and_lands` 5 | yes (and `a_plan_runs_level_by_level` did not catch it, as predicted) |
| P-M2 | the node gate result accepted as the level result | 5 (the five repair-loop tests) | `a_level_gate_failure_is_repaired_and_lands` 5 | yes |
| P-M3 | the scoped selection leaves out the regression set | 3: the_selection_is_the_scope_plus_the_regression_set, the_regression_set_catches_a_regression_in_the_node_gate, the_integration_node_and_a_full_level_gate_are_unscoped | `a_plan_runs_level_by_level` 7 (+ `a_level_gate_failure_is_repaired_and_lands` 7) | yes |
| P-M4 | a failing level gate accepted without repair | 5 (the five repair-loop tests) | `a_level_gate_failure_is_repaired_and_lands` 5 | yes |
| P-M5 | repair attempts not capped (cap + 3) | 2: repairs_are_capped, one_attempt_cap_is_exactly_one | not caught (PASS, 313 cases) | yes |
| P-M6 | stop leaves the gate's app process running | 3: stop_kills_the_gates_whole_tree, a_gate_timeout_kills_the_tree_too, a_plan_stop_during_a_gate_kills_its_tree | not caught (PASS) | yes |

**6 of 6 caught, each by exactly the predicted tests and dogfood steps.** P-M5 and P-M6 are
caught by the tests only, as predicted (the dogfood cannot exhaust repairs or see processes:
R54 and R69 are Unchecked there).

Earlier mutants (dogfood only, as they ran then) and the v1.10 state mutants:

| id | dogfood first failure | as predicted? |
|---|---|---|
| B-M1 | `scoped_builds_accumulate_on_the_integration_branch` 2 (+ named 2, blocked 5, retries 5) | yes |
| B-M2 | `scoped_builds_accumulate_on_the_integration_branch` 7 (+ named 6) | yes |
| B-M5 | `scoped_build_refusals` 5 (+ `plan_refusals` 7) | yes |
| R-M2 | `a_rebuild_passes_in_round_2_on_its_own_branch` 8 (+ 9 other scenarios) | yes |
| BG-P-M3 | `a_level_gate_failure_is_repaired_and_lands` 5 | yes |
| BG-P-M6 (adapted) | `a_level_gate_failure_is_repaired_and_lands` 5 | yes |
| S-M1..S-M6 | `state_mutants.py`: all six caught (control 19 passed) by the same tests as in the v1.10 run | yes |

**No survivor.** 18 of 18 caught.

## Post hoc

None: no test, scenario or code changed after a mutant result.

## Streak measurements (H: read-only, no build)

A detached worktree of Streak `build/streak` at `9417351` under `streak/.lattice/`, Studio's
gate environment built by `lattice.studio.rebuild.gate_env` (`UV_NO_SYNC=1`, Streak's
`.venv`, `PYTHONPATH` = the worktree then `lattice-perf2`, so the modified checker was
imported: checked), `LATTICE_REBUILD=1` (the JSON report), MySQL 8.4 in a throwaway labelled
container per run, runs sequential on an otherwise idle machine. Selections were computed by
the new code (`result_doc`, `build_selection`, `write_selection`, `scoped_command`).

| run | command | scenarios | wall | layers (s) |
|---|---|---|---|---|
| start baseline (full, ABCDX, `x_repeats 5`) | `./check.sh` | 156 | **412.7 s** | A 0.02, C 0.33, D 289.5, X 116.8 |
| node gate, devices R31-R34 (scoped) | `./check.sh --layers CD --x-repeats 1 --await-cap 5 --only-file …/selection.json` | 120 | **21.5 s** | C 0.33, D 15.4 |
| level gate, regression (full layers, no cap) | `./check.sh --only-file …/level-selection.json` | 120 | **138.2 s** | A 0.02, C 0.30, D 15.0, X 116.9 |

- **Start baseline**: 40 failing (all layer D), 116 passing: the regression set.
- **Devices node selection**: its rules link `device_registration`, `access__add_device__as_ann`,
  `device_push_token_takeover`, `access__delete_device__as_ann` (all 4 failing at this tip) and
  `invariant:one_device_per_push_token`; selection = 4 in scope + 116 regression set = 120
  scenarios. **Expected 21.1 s** from the full run's own per-scenario timings (container 4.1 +
  seed 0.3 + teardown 0.3 = 4.7 s, layer D of the 120 selected 15.1 s, C 0.3 s, +1 s go build
  and start), about 17 s from G1's averages (5.1 s container + 120 x 0.084 s); **measured
  21.5 s** (check `total_s` 21.2). Findings: exactly the 4 in-scope device scenarios (404: the
  area is not built); **no regression-set scenario was missed** (all 116 ran, per the report's
  `selection.scenarios`) and none failed. `--await-cap 5` capped 0 steps here (the 9 nudger
  `Await` scenarios fail at this tip and are not in this scope, so they are not selected).
- **Level gate (regression)**: passing-before (116) plus the landed devices scope (4) = 120
  scenarios with the full layers: 138.2 s, the same 4 device findings. Layer D falls from
  289.5 s to 15.0 s (the 9 failing `Await` scenarios are not selected); **layer X does not
  shrink** (116.9 s): it is capped by `x_budget = 60` races, and 120 scenarios still fill the
  budget ("42 race(s) skipped over the budget of 60").
- Per level, the gate cost goes from 3 full gates (~1240 s: baseline, round, level end) to one
  node gate per round (~21 s) plus one regression level gate (~138 s); the next level's start
  baseline is the level gate's recorded per-tip result (not run again).
- Cleanup: the worktree was removed (`git worktree remove --force`, `prune`); afterwards no
  `lattice-*` container existed (`docker ps -a`; the other containers on the machine were not
  touched) and no `streak-api` process ran (`pgrep`). Streak's `git status` was clean.

## Notes

- **Re-recorded ids.** The seed's `gate.sh`, `stub-rebuild.json` and `studio.toml` changed,
  so the seed commit and every id built on it moved: `SEED_HEAD` `312d817…`, `STARRED_COMMIT`
  `09a13e9…`, `REBUILT_COMMIT` `0405099…`, `WRITING_COMMIT` `29f04ef…`, `READING_COMMIT`
  `cc93b87…`, `PLAN_TIP` `645c777…` (level 0 merges `scaffold`, level 1 `writing` then
  `reading`), and the new `CLASH_FAILED_TIP` `6a1fac2…` (the first merge of `w-clash` and
  `r-clash`) and `REPAIR_TIP` `12a5610…` (the re-merge of their repair commits `32b769e`
  "build: Writing notes (repair 1 after the level gate)" on `84a8725`, and `cec3f85` on
  `9931881`), recorded from the real server and checked by hand with `git log --graph --all`
  on a run of the same seed (files at `REPAIR_TIP`: `race.txt` calm, no `right.txt`).
- The dogfood's old `a_regression_at_the_full_gate_fails_the_level` became
  `a_level_gate_failure_is_repaired_and_lands` (R67); R54 (exhaustion, the loop off) moved
  to Unchecked with the tests that cover it; R64-R69 are new (R69 Unchecked).
- A finding while measuring: with the regression level gate the saving is layer D's waits,
  not layer X; making X cheaper at level ends needs a lower `x_repeats` or `x_budget` there
  (not done: the level gate keeps the configured layers, as specified).

## Orchestrator verification (independent re-run, 2026-10-11)

Re-run by the orchestrator, not the build agent: the full suite (934 passed, 5 skipped),
the dogfood gate (PASS, 313 cases, 0 findings), ruff check (clean) and the mutants (control
PASS; 12 of 12 in `perf_mutants.py` and S-M1..S-M6 in `state_mutants.py` caught, by the same
tests and dogfood steps as above). All eleven frozen hashes in the prereg equal the tested
files. The prereg records 933 passed; the final count is 934. The extra test is in a file
outside the hashed set (`tests/studio/test_build_plan.py` / `test_projects.py`, which the
build agent updated after writing the prereg); no hashed file changed.
