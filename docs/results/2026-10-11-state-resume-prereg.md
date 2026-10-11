# Pre-registration: durable state and resume (API v1.10), mutation run (2026-10-11)

Written before any of the six mutants below was run. Not edited afterwards; the outcome is
in `docs/results/2026-10-11-state-resume-results.md`.

## What is under test

- **Durable state and resume (API.md v1.10):** `lattice/studio/state.py` (sha256 prefix
  `9fadefd11f8b14d9`, new: the store, atomic writes, quarantine, id counters, orphan
  matching), `lattice/studio/plan.py` (`b8cdf530f92fe624`: reconciliation, `resume`,
  `land`, `forget`, anchors, continue/retry with attempts), `lattice/studio/rebuild.py`
  (`151a6290f4f5e505`: build records written through, reconciliation, `continue_build`,
  pid tracking, the stub agent's `run` and invocation log) and `lattice/studio/server.py`
  (`247cd7258503d43b`: change sets persisted, the new routes, the store's location).
- **The suite that must catch them:** `tests/studio/test_state_store.py`
  (`38945a28afb4f608`, 10 tests) and `tests/studio/test_resume.py` (`8b2d80b556716169`,
  9 tests, among them a REAL restart: a Studio server subprocess killed with SIGKILL in the
  middle of level 2 and a second server on the same state directory).
- **How a mutant runs:** `dogfood/studio/state_mutants.py` (`b49d580dad277cf3`): the
  `lattice/`, `tests/` and `dogfood/studio/project_seed/` trees are copied to a scratch
  directory, ONE patch is applied to the copy (its old text must occur exactly once, else
  the harness stops), and the two test files run there with the copy first on
  `PYTHONPATH` (the restart test's servers and the SIGKILL test's writer import the copy
  too). The working tree is never patched. A control run of the unpatched copy comes
  first. Mutants run one at a time. "Caught" = the suite exits non-zero.
- The dogfood spec (`dogfood/studio/check.sh`) is not part of this run: restarts are not
  expressible in its DSL (R63 is Unchecked there).

## The six mutants

| id | behaviour | patch (file: old -> new) |
|---|---|---|
| S-M1 | a state file is written non-atomically (direct write: a kill mid-write leaves a truncated file) | `state.py` `atomic_write`: the temporary file, fsync, `os.replace` and directory fsync are replaced by `os.open(path, O_WRONLY\|O_CREAT\|O_TRUNC)` + the same `os.write` loop on the record itself |
| S-M2 | a plan that was running stays running after a restart (zombie) | `plan.py` `PlanManager._load`: `if rec.get("status") in TRANSIENT:` -> `if False and rec.get("status") in TRANSIENT:` |
| S-M3 | resume re-runs nodes that already passed and landed | `plan.py` `_levels`: `if n["status"] == "passed":` (the skip of a passed node) -> `if n["status"] == "passed" and False:` |
| S-M4 | land advances the branch while it is checked out in the owner's worktree, without confirmation | `plan.py` `_land_ff`: `if name == current and not confirm:` -> `if False and name == current and not confirm:` |
| S-M5 | a corrupt state file crashes startup | `state.py` `StateStore.load`: the `except` that quarantines the file and goes on -> `except (...): raise` |
| S-M6 | orphan cleanup kills by pid without matching the command line | `state.py` `orphan_matches`: `return command == info.get("command") or (bool(match) and match in command)` -> `return True` (the start time is still compared) |

The exact old and new texts are the `MUTANTS` table of `dogfood/studio/state_mutants.py`.

## Predictions

Prediction: **all six caught** by `tests/studio/test_state_store.py` and
`tests/studio/test_resume.py`; the control passes (19 tests).

| id | predicted failing test(s) | why |
|---|---|---|
| S-M1 | `test_a_write_killed_midway_leaves_the_old_file_valid` (certain); `test_sigkill_during_saves_never_tears_a_record` (likely, not required: five SIGKILLs at fixed delays into a loop of 3 MB writes) | the simulated kill (half of the bytes written, then the process "dies") lands in the record itself: the next open quarantines a torn `p1.json` and loads nothing |
| S-M2 | `test_a_running_plan_and_its_orphans_are_reconciled_on_load`; `test_a_studio_killed_mid_plan_resumes_without_rerunning_passed_nodes` | the plan comes back `running` instead of `interrupted` (and its recorded orphan is not terminated); after the real restart `POST .../run` answers 409 for a "running" plan |
| S-M3 | `test_a_gated_level_that_could_not_land_is_landed_without_any_agent`; `test_a_moved_target_is_re_merged_and_re_gated_only`; `test_a_studio_killed_mid_plan_resumes_without_rerunning_passed_nodes` | the run after the land (or after the restart) starts the passed nodes' agents again: the stub's invocation log counts them twice |
| S-M4 | `test_a_gated_level_that_could_not_land_is_landed_without_any_agent` | `land` while `build/notes` is checked out in the owner's worktree fast-forwards it there instead of answering 409 `branch_checked_out` |
| S-M5 | `test_a_corrupt_file_is_quarantined_and_loading_goes_on`; `test_corrupt_state_does_not_crash_startup` | opening the store raises on the corrupt files; the hub answers 500 for the project instead of 200 |
| S-M6 | `test_a_running_plan_and_its_orphans_are_reconciled_on_load` | the decoy (a live process whose pid and start time are recorded but whose command line is not the recorded one) is killed |

No other test is predicted to fail for a mutant; an extra failing test is reported as such,
not counted against the prediction.
