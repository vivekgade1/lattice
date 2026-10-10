# Pre-registration: the Studio rebuild loop, mutation run (2026-10-10)

Written before any of the mutants below was built or run (the new ones, and the six old
ones re-run). Not edited afterwards; the outcome is in
`docs/results/2026-10-10-rebuild-results.md`.

## What is under test

- **The rebuild loop:** `lattice/studio/rebuild.py` (sha256 prefix `100e794b92ec581e`) and
  its routes in `lattice/studio/server.py` (sha256 prefix `f80af52fae5af4cc`).
- **The spec:** `dogfood/studio/spec.py` (sha256 prefix `ada127d5fc27a00a`), 23 scenarios,
  four of them new for the rebuild (`a_rebuild_passes_in_round_2_on_its_own_branch`,
  `a_rebuild_gives_up_at_max_rounds_and_can_run_again`, `one_rebuild_at_a_time_and_stop`,
  `rebuild_refusals`), run by `dogfood/studio/check.sh` (layers D and X) through
  `dogfood/studio/harness.py` (sha256 prefix `2663618baf9bfead`, unchanged). The seed
  project gained `gate.sh` (passes iff `built.txt` exists), `stub-rebuild.json` (round 1
  holds 2 s, writes `progress.txt` and overwrites `REQUIREMENTS.md`; round 2 writes
  `built.txt`) and a `[rebuild]` table with the TEST-ONLY stub agent. On the unpatched
  code `./check.sh` answers `PASS: 0 findings (D 0, X 0); 158 cases run` (three runs in a
  row).
- **How a mutant runs:** as in `docs/results/2026-10-10-dogfood-prereg.md`: the whole
  `lattice/` package (as of the hashes above) is copied to a scratch directory, one patch
  is applied to the copy, and `./check.sh` runs with `LATTICE_STUDIO_PYTHONPATH=<copy>`
  (the server process imports the copy; the checker keeps the working tree). The working
  tree is never patched. A control run of the unpatched copy comes first.

## The eight mutants

M1-M6 are the exact patches of `docs/results/2026-10-10-dogfood-prereg.md` (all in
`lattice/studio/server.py`; the code they patch did not change). The two new ones:

| id | behaviour | patch (in `lattice/studio/rebuild.py`, `Rebuilder._loop` / `_commit`) |
|---|---|---|
| R-M1 | rebuild passes without running the gates (trusts the agent) | `gates = [] if run.stop.is_set() else self._gates(run, n)` becomes `gates = []`, and `passed = bool(gates) and all(g["exit_code"] == 0 for g in gates) and not edited` becomes `passed = not agent.get("error")` |
| R-M2 | rebuild merges into the current branch | in `_commit`, right after `sha = _git_ok(wt, "rev-parse", "HEAD")`, add `_git_ok(run.repo, "merge", "-q", "--no-edit", sha)` (the owner's checkout fast-forwards to the rebuild commit) |

## Predictions (which scenario fails first, and why)

| id | predicted catching scenario / step | why |
|---|---|---|
| M1 | `a_second_accept_is_409` step 4 | as before: the detail becomes "the spec changed since this proposal" (caught by the body only) |
| M2 | `cancel_after_ready_is_409` step 3 | DELETE on a ready change answers 200 |
| M3 | `a_follow_up_supersedes_its_parent` step 5 | c1 stays `ready`, `superseded_by: null` |
| M4 | `a_stale_proposal_is_409` step 6 | the stale accept answers 200 |
| M5 | `accept_commits_once` step 3, and every step expecting the commit id `cf22fc5…` (now also in the three rebuild scenarios that accept c1) | two commits: HEAD differs from the one-commit id |
| M6 | `an_accepted_change_cannot_be_rejected` step 4 | reject after accept answers 200 |
| R-M1 | `a_rebuild_passes_in_round_2_on_its_own_branch` step 7 (the `GET /api/rebuild/b1?wait=30`) | round 1 passes (no gate ran, the stub reported no error), so the record has one round with verdict `pass` and no gate entries, and its commit differs from `c1ca55b…` (message "round 1 of 3", no `built.txt`). Also `a_rebuild_gives_up_at_max_rounds_and_can_run_again` step 5 (`passed` instead of `gave_up`). |
| R-M2 | `a_rebuild_passes_in_round_2_on_its_own_branch` step 8 (the `GET /api/model` after the pass) | the record is unchanged, but the owner's `git_head` becomes `c1ca55b` instead of `cf22fc5` |

**Prediction: layer D catches all 8** (`./check.sh` exits 1 on each). Layer X has nothing to
race and is predicted to find nothing. Any survivor is reported as a survivor; scenarios
added or changed after seeing a result are reported separately as post-hoc and do not
count toward the blind result.
