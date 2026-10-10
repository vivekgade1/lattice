# Results: build from spec, mutation run (2026-10-10)

Pre-registration: `docs/results/2026-10-10-build-from-spec-prereg.md` (written before any
mutant below was built or run). The six new mutants and the eight old ones (M1-M6, R-M1
adapted, R-M2) were built as patched copies of the frozen `lattice/` package (hashes as
pre-registered: `rebuild.py` `e408b7f7387ca4f1`, `areas.py` `25edc417ac829456`,
`server.py` `3e38aa0c905a1a46`) and run with `LATTICE_STUDIO_PYTHONPATH=<copy>
./check.sh` from `dogfood/studio` (spec `fb558e648386d691`, 27 scenarios). The working tree
was never patched.

## Blind result (the pre-registered run)

Control (the unpatched copy): `PASS: 0 findings (D 0, X 0); 197 cases run`, exit 0.

| id | behaviour | exit | findings | first catching step | as predicted? |
|---|---|---|---|---|---|
| M1 | accept of an accepted change | 1 | D 1 | `a_second_accept_is_409` step 4 | yes |
| M2 | cancel of a ready change | 1 | D 1 | `cancel_after_ready_is_409` step 3 | yes |
| M3 | a follow-up does not supersede | 1 | D 1 | `a_follow_up_supersedes_its_parent` step 5 | yes |
| M4 | stale proposal accepted | 1 | D 1 | `a_stale_proposal_is_409` step 6 | yes |
| M5 | accept in two commits | 1 | D 9 | `accept_commits_once` step 3 (+8 accept steps) | yes |
| M6 | reject of an accepted change | 1 | D 1 | `an_accepted_change_cannot_be_rejected` step 4 | yes |
| R-M1 | passes without running the gates (adapted) | 1 | D 4 | `a_rebuild_passes_in_round_2_on_its_own_branch` step 7 (+ gives-up step 5, both scoped scenarios' first record) | yes |
| R-M2 | rebuild merges into the current branch | 1 | D 4 | `a_rebuild_passes_in_round_2_on_its_own_branch` step 8 (+ gives-up step 9, scoped step 4, named-branch step 4) | yes |
| B-M1 | scoped pass with an in-scope target failing | 1 | D 2 | `scoped_builds_accumulate_on_the_integration_branch` step 2 (+ `a_named_target_branch_is_created_once` step 2) | yes |
| B-M2 | regressions outside the scope ignored | 1 | D 2 | `scoped_builds_accumulate_on_the_integration_branch` step 7 (+ named-branch step 6) | yes |
| B-M3 | scoped build merges into the owner's branch | 1 | D 2 | `scoped_builds_accumulate_on_the_integration_branch` step 4 (+ named-branch step 4, `GET /api/branches`) | yes |
| B-M4 | areas count scenarios of other areas | 1 | D 3 | `areas_and_branches_of_a_new_project` step 1 (+ both scoped first records: the wrong area widens the scope) | yes |
| B-M5 | checked-out target written without confirmation | 1 | D 1 | `scoped_build_refusals` step 5 | yes |
| B-M6 | an existing branch name overwritten on create | 1 | D 1 | `a_named_target_branch_is_created_once` step 3 | yes |

**14 of 14 caught by layer D, each first at the pre-registered step.** Layer X found
nothing (nothing to race), as predicted. No survivor.

## Post hoc

None: no scenario was added or changed after a mutant result. Two edits to
`lattice/studio/rebuild.py` came after the base copy was frozen and do not touch any
behaviour the mutants or the spec exercise:

- the module docstring gained the v1.8 paragraph and the stub's `null`/`scopes` notes;
- a bug found while producing a sample record: the internal `_failing` set of the scoped
  verdict leaked into each round record (`**extra`), where the HTTP layer's
  `json.dumps(default=str)` turned it into a string field `"_failing"`. It is now popped
  before the round is recorded; `tests/studio/test_build_scope.py` asserts the record is
  plain JSON. The spec's subset matching could not see an extra key (a gap of the black-box
  check, not a mutant result).

After both edits `./check.sh` on the working tree still answers `PASS: 0 findings (D 0, X
0); 197 cases run`.

## Notes

- The seed changed (two sections, a two-file gate, per-scope stub rounds), so every
  pinned commit id was re-recorded: `SEED_HEAD` `44bbe50…`, `STARRED_COMMIT` `f6781de…`,
  `REBUILT_COMMIT` `3cc6b1d…`, `WRITING_COMMIT` `3a8e280…` (parent `SEED_HEAD`, `A
  draft.txt`, `A writing.txt`), `READING_COMMIT` `f513516…` (parent `WRITING_COMMIT`, `A
  reading.txt`). The last two were checked by hand on a real run with `git log
  --name-status build/notes` (the owner's `main` stayed at `SEED_HEAD`, `SCRATCH.txt` still
  untracked). A build onto a named new branch from the same base produces the same ids
  (same tree, parent, message and fixed dates), which the spec uses.
- Because the seed protects only `release`, B-M5 is caught as `branch_checked_out` on
  `main`; with the default protection (`main` protected) the protected check would have
  masked it. The protected path is checked separately (`release`, create).
