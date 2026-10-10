# Results: build plans (the build graph), mutation run (2026-10-10)

Pre-registration: `docs/results/2026-10-10-build-graph-prereg.md` (written before any mutant
below was built or run). The six new mutants (P-M1..P-M6) and the fourteen old ones (M1-M6,
R-M1 adapted as before, R-M2, B-M1..B-M6) were built as patched copies of the frozen
`lattice/` package (hashes as pre-registered and re-checked on the copy: `plan.py`
`2e43b30fdbe203b7`, `rebuild.py` `015f2dea635eed1b`, `server.py` `4b91e0ab0d998ce5`,
`areas.py` `25edc417ac829456`) and run with `LATTICE_STUDIO_PYTHONPATH=<copy> ./check.sh`
from `dogfood/studio` (spec `bad4917af6a518c6`, 34 scenarios), four at a time. The working
tree was never patched. Every pre-registered patch string occurred exactly once; none was
adapted.

## Blind result (the pre-registered run)

Control (the unpatched copy): `PASS: 0 findings (D 0, X 0); 260 cases run`, exit 0.

| id | behaviour | exit | findings | first catching step (and other scenarios) | as predicted? |
|---|---|---|---|---|---|
| M1 | accept of an accepted change | 1 | D 1 | `a_second_accept_is_409` 4 | yes |
| M2 | cancel of a ready change | 1 | D 1 | `cancel_after_ready_is_409` 3 | yes |
| M3 | a follow-up does not supersede | 1 | D 1 | `a_follow_up_supersedes_its_parent` 5 | yes |
| M4 | stale proposal accepted | 1 | D 1 | `a_stale_proposal_is_409` 6 | yes |
| M5 | accept in two commits | 1 | D 9 | `accept_commits_once` 3 (+8 accept steps) | yes |
| M6 | reject of an accepted change | 1 | D 1 | `an_accepted_change_cannot_be_rejected` 4 | yes |
| R-M1 | passes without running the gates | 1 | D 7 | `a_rebuild_passes_in_round_2_on_its_own_branch` 7 (+ gives-up 5, both scoped 2, plan 6, regression 5, blocked 5) | yes |
| R-M2 | rebuild merges into the current branch | 1 | D 7 | `a_rebuild_passes_in_round_2_on_its_own_branch` 8 (+ gives-up 9, scoped 4, named 4, plan 7, regression 8, blocked 8) | yes (first step); the plan scenario catches at step 7 (`main` moved, in `GET /api/branches`), earlier than the predicted `UNCHANGED` step |
| B-M1 | scoped pass with an in-scope target failing | 1 | D 3 | `scoped_builds_accumulate_on_the_integration_branch` 2 (+ named 2, blocked 5) | yes |
| B-M2 | regressions inside a build ignored | 1 | D 2 | `scoped_builds_accumulate_on_the_integration_branch` 7 (+ named 6) | yes |
| B-M3 | a build/level end also ff-merges the owner's branch | 1 | D 4 | `scoped_builds_accumulate_on_the_integration_branch` 4 (+ named 4, plan 7, blocked 8) | yes (first step); plan caught at step 7 rather than 9, and the blocked scenario (step 8) was not predicted |
| B-M4 | areas count other areas' scenarios | 1 | D 5 | `areas_and_branches_of_a_new_project` 1 (+ scoped 2, named 2, plan 8, blocked 7) | yes |
| B-M5 | checked-out target written without confirmation | 1 | D 2 | `scoped_build_refusals` 5 (+ `plan_refusals` 7) | yes |
| B-M6 | an existing branch name overwritten on create | 1 | D 1 | `a_named_target_branch_is_created_once` 3 | yes |
| P-M1 | overlapping write sets run in one level | 1 | D 1 | `overlapping_siblings_are_serialised` 2 | yes |
| P-M2 | a node whose dependency failed runs anyway | 1 | D 1 | `a_failed_node_blocks_its_dependents_not_its_siblings` 5 | yes |
| P-M3 | the level merge skips the post-merge full gate | 1 | D 3 | `a_plan_runs_level_by_level` 6 (+ regression 5, blocked 5) | yes |
| P-M4 | a plan runs without approval | 1 | D 3 | `a_plan_runs_level_by_level` 3 (+ overlapping 5, invalid 5) | yes |
| P-M5 | rules covered twice or not at all accepted | 1 | D 1 | `an_invalid_plan_falls_back` 2 | yes |
| P-M6 | the target advanced to a regressed merged tip | 1 | D 1 | `a_regression_at_the_full_gate_fails_the_level` 8 | yes |

**20 of 20 caught by layer D, each first at the pre-registered step.** Layer X found nothing
(nothing to race), as predicted. No survivor. The checker reports the first failing step of
each scenario only, so the "(+ step n)" parts of predictions inside an already-failing
scenario (P-M1 step 4, P-M4 steps 4-6, P-M3 step 7) are not observable and are not counted
either way. Two secondary predictions were off in the step, not in the outcome (R-M2 and
B-M3 in the plan scenario: caught two steps earlier, at `GET /api/branches`, where the
owner's `main` shows the moved head), and B-M3 was also caught by the blocked scenario,
which the pre-registration did not name.

## Post hoc

None: no scenario was added or changed after a mutant result, and no code changed after the
base copy was frozen.

## Notes

- **Re-recorded ids.** The seed's `gate.sh`, `stub-rebuild.json` and `studio.toml` changed
  and `stub-plan.json` is new, so the seed commit and every id built on it moved:
  `SEED_HEAD` `e691f93…`, `STARRED_COMMIT` `0fa3822…`, `REBUILT_COMMIT` `e437e4e…` (base
  `STARRED_COMMIT`; `A progress.txt`, `A reading.txt`, `A writing.txt` in the record's
  `name_status`), `WRITING_COMMIT` `6bbdb4d…` (base `SEED_HEAD`; `A draft.txt`, `A
  writing.txt`), `READING_COMMIT` `7c15f8a…` (base `WRITING_COMMIT`; `A reading.txt`): read
  from the real server's records when they first mismatched; the tree contents are pinned
  by the spec's `files_changed`. `PLAN_TIP` `a1d0deb…` was checked by hand with `git log
  --graph --all` on a run of the same seed with the harness's git identity and dates:
  `a1d0deb` = merge of `rebuild/p1-reading` (`d878736 build: Reading own notes`) onto
  `dd7b611` = merge of `rebuild/p1-writing` (`1d689e1 build: Writing notes`) onto `ff26f56`
  = merge of `rebuild/p1-scaffold` (`d84b8bf build: Shared scaffold`) onto `e691f93 seed`.
  Because the merge order is part of the hash, `PLAN_TIP` also pins "merged in plan node
  order".
- **The seed kept its two areas.** The task suggested 3-4 sections; the seed's
  REQUIREMENTS.md was left alone so the 27 earlier scenarios (their area lists and rule ids
  R1-R5) stand unchanged. The shape asked for (a scaffold, two independent nodes, one
  dependent node, an integration node) comes from rule-scoped nodes instead (`writing`:
  area writing-notes; `reading`: R3; `tokens`: R4 after `reading`).
- **What the dogfood cannot see** (in `tests/studio/test_build_plan.py`, 33 tests): the real
  planner's argv and isolation against a fake claude (read-only tools, no API env, the
  re-ask with the issues, the fallback reasons), sibling concurrency (overlapping start and
  finish times), merge conflicts, resume, revise with feedback, the metrics hook per node
  and per plan, branch creation at run time, the validator matrix and the fallback on
  Streak's real trace (148 rules, each covered once).
- `./check.sh` on the working tree after the run: `PASS: 0 findings (D 0, X 0); 260 cases
  run`. The pytest gate (`tests/studio tests/mcp tests/test_cli.py tests/spec`, without the
  two db files): 859 passed, 5 skipped.
