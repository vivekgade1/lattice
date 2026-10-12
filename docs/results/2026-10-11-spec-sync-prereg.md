# Pre-registration: spec sync in Studio (API v1.12), mutation run (2026-10-11)

Written before any mutant below was run. Phase: node S3 of the roadmap, "spec sync": the spec
files committed at the repository's HEAD are compared with a target branch's (`GET
/api/branches` `spec`, a plan's `spec_drift`) and synced onto it by one commit
(`POST /api/branches/{name}/sync-spec`, `[rebuild] sync_spec = "auto"` before a plan's start
baseline). Code frozen at these sha256[:16] (the runner prints them again):

| file | hash |
|---|---|
| `lattice/studio/rebuild.py` | `2ce432b43e62180b` |
| `lattice/studio/plan.py` | `509b3a3d04565811` |
| `lattice/studio/server.py` | `a461bc9b9d5fc351` |
| `lattice/studio/mock_server.py` | `0f9995e61687f52f` |
| `dogfood/studio/spec.py` (43 scenarios) | `9218d380ccdb4a39` |
| `dogfood/studio/project_seed/studio.toml` | `a696c01e006a21d1` |
| `tests/studio/test_spec_sync.py` | `7747a4ae214eaaf8` |
| `dogfood/studio/sync_mutants.py` (the runner) | `cad98bf0a038188b` |

The web UI files (`lattice/studio/web/js/...`) are outside the mutation run.

## How a mutant runs

`uv run python dogfood/studio/sync_mutants.py --out <dir>` (modelled on `perf_mutants.py`, whose
copy, pytest and dogfood helpers it imports): `lattice/`, `tests/` and the seed are copied to a
scratch directory, the mutant's edits are applied to the copy (each edit's old text must occur
exactly once, else the run aborts before anything runs), then (a)
`tests/studio/test_spec_sync.py` runs with the copy first on `PYTHONPATH` and (b) `./check.sh`
(the dogfood) runs with `LATTICE_STUDIO_PYTHONPATH=<copy>`. Caught = either exits non-zero. A
control run of the unpatched copy comes first; three mutants at a time. The working tree is
never patched. Each mutant is one edit except SY-M5, which is one mutation made of two edits
(the auto sync removed from the run's start, and added after the start baseline).

## The six mutants (exact patches are in `sync_mutants.py`)

| id | behaviour | patch |
|---|---|---|
| SY-M1 | the sync reads the working copy instead of HEAD | `_sync_commit`: `git checkout <head> -- <files>` -> copy each file from the owner's working copy into the temporary worktree and `git add` it |
| SY-M2 | a sync is allowed while the target is checked out | `sync_spec`: `if where is not None: raise ... branch_checked_out` -> `if False:` |
| SY-M3 | the sync commits more than the spec files | `_sync_commit`: `git add -A` after the checkout of the spec files (the temporary worktree has no checkout, so every other file of the branch is staged as deleted) |
| SY-M4 | a sync is allowed while a plan holds the build slot | `sync_spec`: `if busy: raise ... build_running` -> `if False:` |
| SY-M5 | auto mode syncs after the start baseline | `run`: the `_auto_sync` call -> `pass`; `_levels`: after the start baseline's results, `_auto_sync` with the plan's slot released around it (else the sync would refuse `build_running` and the mutant would be caught for the wrong reason) |
| SY-M6 | `nothing_to_sync` creates an empty commit | `sync_spec`: the `nothing_to_sync` refusal -> `commit-tree` of the tip's own tree, the branch advanced to it, and a 200 answer |

## Predictions (blind)

Dogfood steps are numbered from 1 as the checker prints them. "tests" lists the tests of
`test_spec_sync.py` predicted to fail (at least these); a mutant counts as caught if anything
fails.

| id | tests (predicted to fail) | dogfood (first failing scenario / step) |
|---|---|---|
| SY-M1 | `the_sync_is_one_commit_of_the_committed_spec_files_only` (the branch gets the uncommitted rewrite of REQUIREMENTS.md) | **not caught**: no scenario can leave an uncommitted spec edit in the owner's working copy, and after an accept the working copy equals HEAD, so the commit is the same (R70/R71 note this as Unchecked) |
| SY-M2 | `each_refusal_changes_nothing` (main answers `nothing_to_sync`), `the_routes` (the same over HTTP). `an_auto_sync_refusal_is_the_runs_409` is predicted to PASS: the compare-and-swap in `_advance_to` still refuses a branch checked out in a linked worktree with the same code | `spec_sync_refusals` step 1 (`main`: `nothing_to_sync`, not `branch_checked_out`) |
| SY-M3 | `the_sync_is_one_commit_of_the_committed_spec_files_only` (the diff deletes `built.txt`, `gate.sh`, ...), `auto_syncs_before_the_start_baseline` (`gate.sh` is gone from the target, so the start baseline cannot run and the plan fails) | `the_committed_spec_is_synced_onto_the_target_branch` step 9 (the sync's `commit` is not `SYNC_COMMIT`) |
| SY-M4 | `each_refusal_changes_nothing` (with `plan_active` set the sync goes through), `a_running_plan_holds_the_slot` | `spec_sync_is_refused_while_a_plan_runs` step 7 (`nothing_to_sync`, not `build_running`) |
| SY-M5 | `auto_syncs_before_the_start_baseline` (the baseline's sha is the old tip, not the sync commit) | `a_plan_run_syncs_the_spec_first_when_configured` step 10 (`baseline.sha` is `WRITING_COMMIT`, not `SYNC_COMMIT`; the plan may also fail to land because the target moved) |
| SY-M6 | `each_refusal_changes_nothing` (no 409 on the second sync), `the_routes` (200, not 409) | `the_committed_spec_is_synced_onto_the_target_branch` step 12 (200, not 409 `nothing_to_sync`) |

**Prediction: all six caught**, SY-M1 by the tests only, SY-M2..SY-M6 by both the tests and the
dogfood.

Separately (unchanged runners, must keep their numbers): `perf_mutants.py` 12/12 and
`state_mutants.py` 6/6. The seed's `studio.toml` gained `sync_spec = "auto"`, which changed the
seed commit; every recorded commit id of `dogfood/studio/spec.py` was re-recorded (see the
results doc); no patch string of either runner refers to an id.

Any survivor is reported as a survivor, never silently fixed. Tests or scenarios added or
changed after a result are reported as post hoc and do not count toward the blind result.
