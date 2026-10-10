# Pre-registration: build from spec, mutation run (2026-10-10)

Written before any of the mutants below was built or run (the six new ones, and the eight
old ones re-run). Not edited afterwards; the outcome is in
`docs/results/2026-10-10-build-from-spec-results.md`.

## What is under test

- **Build from spec (API.md v1.8):** `lattice/studio/areas.py` (sha256 prefix
  `25edc417ac829456`, new), `lattice/studio/rebuild.py` (`e408b7f7387ca4f1`) and its routes
  in `lattice/studio/server.py` (`3e38aa0c905a1a46`): `GET /api/areas`, `GET /api/branches`,
  scoped `POST /api/rebuild {"scope", "branch", "confirm_unsafe_branch"}` with a baseline
  gate run, the regression rule, and target branches (default `build/<project>`).
- **The spec:** `dogfood/studio/spec.py` (`fb558e648386d691`), 27 scenarios, four new for
  build from spec (`areas_and_branches_of_a_new_project`,
  `scoped_builds_accumulate_on_the_integration_branch`, `scoped_build_refusals`,
  `a_named_target_branch_is_created_once`), rules R41-R48 in
  `dogfood/studio/REQUIREMENTS.md`, run by `dogfood/studio/check.sh` (layers D and X) through
  `dogfood/studio/harness.py` (`2663618baf9bfead`, unchanged). The seed project changed: its
  REQUIREMENTS.md has two sections (`Writing notes`: R1, R2; `Reading notes`: R3, R4), so
  every commit id the spec pins was re-recorded (`SEED_HEAD` `44bbe50…`, `STARRED_COMMIT`
  `f6781de…`, `REBUILT_COMMIT` `3cc6b1d…`, new `WRITING_COMMIT` `3a8e280…` and
  `READING_COMMIT` `f513516…`; the last two checked by hand with `git log --name-status
  build/notes` on a real run). `gate.sh` now checks two files (`writing.txt` for
  `scenario:create_a_note`, `reading.txt` for `scenario:read_own_and_other`);
  `stub-rebuild.json` gained per-scope rounds (`writing-notes`: a draft, then
  `writing.txt`; `reading-notes`: `reading.txt` plus deleting `writing.txt`, then restoring
  it); `studio.toml` protects only `release`. On the unpatched code `./check.sh` answers
  `PASS: 0 findings (D 0, X 0); 197 cases run`.
- **How a mutant runs:** as in `docs/results/2026-10-10-dogfood-prereg.md`: the whole
  `lattice/` package (as of the hashes above) is copied to a scratch directory, one patch is
  applied to the copy, and `./check.sh` runs with `LATTICE_STUDIO_PYTHONPATH=<copy>`. The
  working tree is never patched. A control run of the unpatched copy comes first.

## The fourteen mutants

M1-M6 are the exact patches of `docs/results/2026-10-10-dogfood-prereg.md` (server.py; the
code they patch did not change). R-M2 is the exact patch of
`docs/results/2026-10-10-rebuild-prereg.md`. **R-M1 is adapted** (same behaviour, the lines
moved): the gate run now happens in `Rebuilder._gate_round`, so its first half becomes
`gates = self._gates(run, n)` -> `gates = []` there (this also empties the baseline of a
scoped build); its second half is unchanged (`passed = bool(gates) and all(g["exit_code"]
== 0 for g in gates) and not edited` -> `passed = not agent.get("error")`, now inside the
`run.scope is None` branch).

The new ones (all in `lattice/studio/rebuild.py` except B-M4):

| id | behaviour | patch |
|---|---|---|
| B-M1 | a scoped build passes while an in-scope target still fails (the agent's word trusted for the scope) | in `_judge`: `still = [t for t in target_ids if t in cur]` -> `still = []` |
| B-M2 | regressions outside the scope are ignored | in `_judge`: `regressions = [i for i in cur if i not in run.baseline_ids]` -> `regressions = []` |
| B-M3 | a scoped build merges into the owner's current branch | in `_advance`, after the successful `update-ref` (just before its final `return None`): add `_git(repo, "merge", "-q", "--ff-only", sha)` (run in the owner's worktree) |
| B-M4 | the areas list counts scenarios of other areas | `lattice/studio/areas.py`, `derive_areas`: `targets = _uniq(t for rid in rids for t in links.get(rid, []))` -> `targets = _uniq(t for v in links.values() for t in v)` |
| B-M5 | the target branch checked out in the owner's worktree is written without confirmation | in `_target`: `elif name == current:` -> `elif False:` |
| B-M6 | a new branch name that already exists is overwritten | in `_target`: delete `if create and exists: raise RebuildError(409, ..., "branch_exists")`, and `_git(repo, "update-ref", ref, from_sha, "")` -> `_git(repo, "update-ref", ref, from_sha)` (no old-value guard) |

## Predictions (which scenario fails first, and why)

| id | predicted catching scenario / step | why |
|---|---|---|
| M1 | `a_second_accept_is_409` step 4 | as before: the detail becomes "the spec changed since this proposal" |
| M2 | `cancel_after_ready_is_409` step 3 | DELETE on a ready change answers 200 |
| M3 | `a_follow_up_supersedes_its_parent` step 5 | c1 stays `ready`, `superseded_by: null` |
| M4 | `a_stale_proposal_is_409` step 6 | the stale accept answers 200 |
| M5 | `accept_commits_once` step 3 (and every step expecting `STARRED_COMMIT`) | two commits: HEAD differs from the one-commit id |
| M6 | `an_accepted_change_cannot_be_rejected` step 4 | reject after accept answers 200 |
| R-M1 | `a_rebuild_passes_in_round_2_on_its_own_branch` step 7 | round 1 passes with no gate run; also every scoped build ends `gave_up` (baseline: "no gate ran") |
| R-M2 | `a_rebuild_passes_in_round_2_on_its_own_branch` step 8 | the owner's `git_head` moves to the rebuild commit; the scoped scenarios also see it |
| B-M1 | `scoped_builds_accumulate_on_the_integration_branch` step 2 (`GET /api/rebuild/b1`) | round 1 (a draft only) passes: one round, verdict `pass`, a different commit; also `a_named_target_branch_is_created_once` step 2 |
| B-M2 | `scoped_builds_accumulate_on_the_integration_branch` step 7 (`GET /api/rebuild/b2`) | round 1, which deleted `writing.txt`, passes: one round, a different commit (also deleting `writing.txt`) |
| B-M3 | `scoped_builds_accumulate_on_the_integration_branch` step 4 (`GET /api/model`) | the owner's `git_head` becomes `3a8e280` instead of `44bbe50` |
| B-M4 | `areas_and_branches_of_a_new_project` step 1 (`GET /api/areas`) | `writing-notes` and `reading-notes` report 4 scenarios instead of 2 |
| B-M5 | `scoped_build_refusals` step 5 | `branch: {"name": "main"}` answers 200 `{"id": "b1"}` instead of 409 `branch_checked_out` |
| B-M6 | `a_named_target_branch_is_created_once` step 3 | `create: true` on the existing `feature/w` answers 200 instead of 409 `branch_exists` |

**Prediction: layer D catches all 14** (`./check.sh` exits 1 on each). Layer X has nothing to
race and is predicted to find nothing. Any survivor is reported as a survivor; scenarios
added or changed after seeing a result are reported separately as post-hoc and do not count
toward the blind result.
