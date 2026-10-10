# Pre-registration: build plans (the build graph), mutation run (2026-10-10)

Written before any of the mutants below was built or run (the six new ones, and the
fourteen old ones re-run). Not edited afterwards; the outcome is in
`docs/results/2026-10-10-build-graph-results.md`.

## What is under test

- **Build plans (API.md v1.9):** `lattice/studio/plan.py` (sha256 prefix
  `2e43b30fdbe203b7`, new; its planner prompt `lattice/studio/prompts/epg_plan.md`
  `15c72eda8173156c`), `lattice/studio/rebuild.py` (`015f2dea635eed1b`: node builds that run
  concurrently inside a plan, the build slot `plan_active`, `_target(dry=True)`,
  `_advance_to`, deferred node metrics hooks) and the routes in `lattice/studio/server.py`
  (`4b91e0ab0d998ce5`): `POST /api/plan`, `GET /api/plan/{id}[?wait=S]`, `GET /api/plans`,
  `POST /api/plan/{id}/revise|approve|run`, `DELETE /api/plan/{id}`.
  `lattice/studio/areas.py` (`25edc417ac829456`) is unchanged.
- **The spec:** `dogfood/studio/spec.py` (`bad4917af6a518c6`), 34 scenarios, seven new for
  build plans (`a_plan_runs_level_by_level`, `overlapping_siblings_are_serialised`,
  `an_invalid_plan_falls_back`, `a_regression_at_the_full_gate_fails_the_level`,
  `a_failed_node_blocks_its_dependents_not_its_siblings`, `a_running_plan_can_be_stopped`,
  `plan_refusals`), rules R49-R57 in `dogfood/studio/REQUIREMENTS.md`
  (`ca64eddf771a8088`; trace `887526e9a291524b`), run by `dogfood/studio/check.sh` (layers
  D and X) through `dogfood/studio/harness.py` (`2663618baf9bfead`, unchanged).
- **The seed changed** (not its REQUIREMENTS.md: the two areas and R1-R4 stay):
  `gate.sh` (`a1912cdd05558985`) gained a third check that passes unless `left.txt` and
  `right.txt` both exist (`scenario:no_token`); `stub-rebuild.json` (`f5e67a36198effca`)
  gained per-node rounds (`scaffold`, `writing`, `reading`, `w-clash`, `r-clash`,
  `w-broken`, `r-ok`, `slow`); `stub-plan.json` (`50805275eeec968b`, new) holds one planner
  document per plan scope; `studio.toml` sets `[rebuild] planner = "stub"`. So every pinned
  commit id was re-recorded: `SEED_HEAD` `e691f93…`, `STARRED_COMMIT` `0fa3822…`,
  `REBUILT_COMMIT` `e437e4e…`, `WRITING_COMMIT` `6bbdb4d…`, `READING_COMMIT` `7c15f8a…`,
  and the new `PLAN_TIP` `a1d0deb…` (the target branch after the plan `all`: the level-0
  merge of `scaffold`, then the level-1 merges of `writing` then `reading`; checked by hand
  with `git log --graph --all` on a run of the same seed).
  On the unpatched code `./check.sh` answers `PASS: 0 findings (D 0, X 0); 260 cases run`
  (three runs, one of them concurrent with the pytest gate).
- **How a mutant runs:** as before: the whole `lattice/` package (as of the hashes above) is
  copied to a scratch directory, one patch is applied to the copy, and `./check.sh` runs
  with `LATTICE_STUDIO_PYTHONPATH=<copy>`. The working tree is never patched. A control run
  of the unpatched copy comes first.

## The twenty mutants

M1-M6 (`server.py`), R-M1 (adapted, as in the build-from-spec run), R-M2 and B-M1..B-M6 are
the exact patches of `docs/results/2026-10-10-build-from-spec-prereg.md` (each patched
string still occurs exactly once in the files above; nothing is re-adapted). Note: R-M1's
`gates = []` in `Rebuilder._gate_round` now also empties a plan's full gates, and B-M3's
extra merge sits in `_advance_to`, which a plan's level end also uses.

The new ones (all in `lattice/studio/plan.py`):

| id | behaviour | patch |
|---|---|---|
| P-M1 | overlapping write sets run in the same level (validator disabled) | in `validate_plan`'s level loop: `if _any(o):` -> `if False and _any(o):` |
| P-M2 | a node whose dependency failed is run anyway | in `_levels`: `bad = [d for d in n["depends_on"] if nodes[d]["status"] != "passed"]` -> `bad = []` |
| P-M3 | the level merge skips the post-merge full gate | in `_merge_level`: `gates, counts, findings = rb.gate_round(irun, L + 1)` -> `gates, counts, findings = [], {}, []` |
| P-M4 | a plan runs without approval | in `run`: `if status not in ("approved", *RESUMABLE):` -> `if status not in ("approved", "ready", *RESUMABLE):` |
| P-M5 | plan scope rules covered twice or not at all are accepted | in `validate_plan`'s coverage loop: `got = cover.get(r, [])` -> `got = [r]` |
| P-M6 | the target branch is advanced to a merged tip whose full gate regressed | in `_merge_level`, before the regression branch's `return {"failed": True}`: add `_git(rb.repo(), "update-ref", f"refs/heads/{plan.record['target_branch']}", tip)` |

## Predictions (which scenario fails first, and why)

Steps are numbered from 1 as the checker prints them.

| id | predicted catching scenario / step | why |
|---|---|---|
| M1-M6 | as in the build-from-spec results (`a_second_accept_is_409` 4, `cancel_after_ready_is_409` 3, `a_follow_up_supersedes_its_parent` 5, `a_stale_proposal_is_409` 6, `accept_commits_once` 3 (+ every accept step), `an_accepted_change_cannot_be_rejected` 4) | the plan scenarios use no change set |
| R-M1 | `a_rebuild_passes_in_round_2_on_its_own_branch` step 7 (+ the scoped scenarios, and every plan run: the start baseline sees no gate and the plan ends `failed`) | no gate runs anywhere |
| R-M2 | `a_rebuild_passes_in_round_2_on_its_own_branch` step 8 (+ the scoped scenarios; the plan scenarios' `UNCHANGED` steps, since node commits merge into the owner's checkout too) | the owner's `git_head` moves |
| B-M1 | `scoped_builds_accumulate_on_the_integration_branch` step 2 (+ named-branch step 2; plan nodes pass round 1, e.g. `w-broken` passes: blocked scenario step 5) | in-scope failures ignored |
| B-M2 | `scoped_builds_accumulate_on_the_integration_branch` step 7 (+ named-branch step 6) | regressions inside a node build ignored; the plan scenarios have no such round |
| B-M3 | `scoped_builds_accumulate_on_the_integration_branch` step 4 (+ named-branch step 4; `a_plan_runs_level_by_level` step 9, the owner's checkout fast-forwarded by the level end) | a ff-merge in the owner's worktree |
| B-M4 | `areas_and_branches_of_a_new_project` step 1 (+ as before) | areas count other areas' scenarios |
| B-M5 | `scoped_build_refusals` step 5 (+ `plan_refusals` step 7: `branch: main` accepted) | the checked-out target is not refused |
| B-M6 | `a_named_target_branch_is_created_once` step 3 | an existing branch overwritten on create |
| P-M1 | `overlapping_siblings_are_serialised` step 2 (+ step 4, the revised plan) | `writing` and `reading` stay on level 0: no `serialised_because`, no `serialise` auto-edit, levels `[[writing, reading], [integration]]` |
| P-M2 | `a_failed_node_blocks_its_dependents_not_its_siblings` step 5 (+ step 7, areas) | `t-dep` runs (nothing to build: `passed`, not `blocked`) and the integration node runs and builds |
| P-M3 | `a_plan_runs_level_by_level` step 6 (+ step 7; `a_regression_at_the_full_gate_fails_the_level` step 5; blocked scenario step 5) | with no findings after level 0 the level-1 nodes have nothing to build (no branches, another `target_tip`); the clash is never seen, so the regression plan `passed` |
| P-M4 | `a_plan_runs_level_by_level` step 3 (+ steps 4-6; `overlapping_siblings_are_serialised` step 5; `an_invalid_plan_falls_back` step 5) | `run` on a ready plan answers 200 `running` instead of 409 |
| P-M5 | `an_invalid_plan_falls_back` step 2 | the stub's plan (R1 twice, R3 never) is accepted: `source: agent`, nodes `a`, `b` |
| P-M6 | `a_regression_at_the_full_gate_fails_the_level` step 8 | `build/notes` points at the regressed merge tip instead of `SEED_HEAD` (the record's `target_tip` is unchanged, so step 5 still passes) |

**Prediction: layer D catches all 20** (`./check.sh` exits 1 on each). Layer X has nothing to
race and is predicted to find nothing. Any survivor is reported as a survivor; scenarios
added or changed after seeing a result are reported separately as post-hoc and do not count
toward the blind result.
