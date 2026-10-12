# Results: spec sync in Studio (API v1.12), 2026-10-11

Pre-registration: `docs/results/2026-10-11-spec-sync-prereg.md` (written, with the hashes, before
any mutant ran). Branch `spec-sync` (worktree `lattice-sync`), based on `roadmap` e93faea.

## What was built

- `lattice/studio/rebuild.py`: `Rebuilder.spec_names` (SPEC_FILES + `[mcp] protected`),
  `spec_diff` (committed blobs via `git ls-tree -r` of HEAD and of the branch, cached per
  commit pair), `spec_drift`, `sync_spec` (refusals in order: unknown body field 422,
  `branch_name_invalid` 422, `branch_protected` 409, `no_branch` 404, `branch_checked_out` 409,
  `build_running` 409, `nothing_to_sync` 409; then the CAS refusals of `_advance_to`) and
  `_sync_commit` (a temporary detached `--no-checkout` worktree, `read-tree <tip>`, `git checkout
  <HEAD> -- <files>` / `git rm --cached` for files gone at HEAD, one commit, worktree removed in
  `finally`); `GET /api/branches` entries gain `spec`; `[rebuild] sync_spec`.
- `lattice/studio/plan.py`: `spec_drift` and the drift warning at create and on every read while
  not running; `[rebuild] sync_spec = "auto"` syncs in `POST /api/plan/{id}/run` before the run
  takes the slot (so before the start baseline), timeline `spec synced from <branch> at <sha7>
  (<files>)`, a refusal is the run's 409.
- `lattice/studio/server.py`: `POST /api/branches/{name}/sync-spec` (name raw with slashes or
  `%2F`-escaped).
- UI: `specSyncBanner` in `views/plan.js` (used by the plan view and by `views/areas.js`'s Build
  view for the chosen target), `api.syncSpec`, CSS `.sp-sync` (quiet, the `pv-resume.is-quiet`
  look). No `confirm()`, no new dependency.
- Mock: fixture branch `build/spec-behind` in every project; the same fields, endpoint and codes.
- Docs: API.md `## v1.12 additions (spec sync)` (with its mock section), the title now v1.12;
  `docs/lattice-reference.md` gained a Studio config key table with `[rebuild] sync_spec`.
- Dogfood: R70-R75 in `dogfood/studio/REQUIREMENTS.md`, trace links, four scenarios
  (`the_committed_spec_is_synced_onto_the_target_branch`, `spec_sync_refusals`,
  `spec_sync_is_refused_while_a_plan_runs`, `a_plan_run_syncs_the_spec_first_when_configured`),
  R75 (UI) unchecked, partial-coverage notes for R70-R74.

## The seed changed: every recorded commit id was re-recorded

The auto-sync scenario needs `[rebuild] sync_spec = "auto"`, which Studio reads from the
project's committed `studio.toml`, so the seed's `studio.toml` gained that line. The seed commit
changed, and with it every id recorded in `dogfood/studio/spec.py`. They were re-recorded from
the real server (each scenario replayed without assertions against `python -m lattice.studio
--stub-llm` with the harness's fixed git identity and dates) and checked by hand with `git log
--graph --all --name-status`:

| constant | old | new | checked |
|---|---|---|---|
| `SEED_HEAD` | `312d817f…` | `6ad9f48ca3578194cc702af980b84da259a86d16` | root commit, the 7 seed files |
| `STARRED_COMMIT` | `09a13e96…` | `f372a593ce33766b430348683f614469494eec06` | parent SEED_HEAD, M REQUIREMENTS.md, spec.trace.json |
| `REBUILT_COMMIT` | `04050993…` | `37163703f2580004853d91dc422f824f4c172f62` | parent STARRED, A progress.txt, reading.txt, writing.txt |
| `WRITING_COMMIT` | `29f04ef7…` | `c15cbb303a97efa936a6018111f52b3926a6f845` | parent SEED_HEAD, A draft.txt, writing.txt |
| `READING_COMMIT` | `cc93b875…` | `04e0ae929beb561410a4d2cef4873fdc568f6776` | parent WRITING, A reading.txt |
| `PLAN_TIP` | `645c7774…` | `569ffcaf70f9190700b918ad8546610c158d9ffd` | level 0 merge of scaffold, level 1 merges of writing then reading |
| `CLASH_FAILED_TIP` | `6a1fac2d…` | `e4b250526acd7a24c98f9d7f71f689d59b266cb0` | first level-0 merges (w-clash, r-clash) |
| `REPAIR_TIP` | `12a56100…` | `85edcac51d657a188f63822c38bd087417b4fb4f` | level 0 merged again after both repair commits; build/notes |
| `SYNC_COMMIT` (new) | | `94da6cdf11f0b765bf8c687052d5be6ed3821854` | parent WRITING, M REQUIREMENTS.md, spec.trace.json only, `spec: sync from main f372a59` + `Lattice-Spec-Sync: f372a593…`; no temporary worktree left |

No id appears anywhere else (grep over lattice/, tests/, dogfood/, scripts/, docs/); no patch
string of `perf_mutants.py` or `state_mutants.py` refers to one.

## Verification

| check | result |
|---|---|
| `pytest tests/studio tests/mcp tests/test_cli.py tests/spec -q --ignore=tests/spec/test_db.py --ignore=tests/spec/test_multidb.py` | 963 passed, 5 skipped (baseline 950 passed, 5 skipped; +13 = `test_spec_sync.py`). One existing test changed: `test_build_scope.py::test_branches_listing` compares the listing exactly, so its expected entries gained `spec` |
| `cd dogfood/studio && ./check.sh` | `PASS: 0 findings (D 0, X 0); 358 cases run` (baseline 313 cases; 43 scenarios, 4 new) |
| `sync_mutants.py` (blind, frozen hashes as pre-registered) | control PASS; **6/6 caught** (table below) |
| `sync_mutants.py` (post hoc rerun, fixture fix) | control PASS; 6/6 caught with the same failing tests and dogfood steps as the blind run, and no pytest errors |
| `perf_mutants.py` | control PASS; **12/12 caught** (P-M1..P-M6, B-M1, B-M2, B-M5, R-M2, BG-P-M3, BG-P-M6; P-M5 and P-M6 by pytest only, as pre-registered then; dogfood 358 cases) |
| `state_mutants.py` | control PASS (19 passed); **6/6 caught** (S-M1..S-M6) |
| `scripts/restart_drill.py` | A, B, C, F: PASS (3.5 s, 3.3 s, 2.6 s, 2.3 s) |
| ruff check (changed .py) / ruff format --check (new files) | clean / clean |

### The six mutants (blind run)

| id | prediction | tests that failed | dogfood (first failing step) | verdict |
|---|---|---|---|---|
| SY-M1 working copy instead of HEAD | tests only | `the_sync_is_one_commit_of_the_committed_spec_files_only` | none (PASS, 358 cases), as predicted | caught (pytest) |
| SY-M2 checked-out target allowed | both | `each_refusal_changes_nothing`, `the_routes` (`an_auto_sync_refusal_is_the_runs_409` passed, as predicted) | `spec_sync_refusals` / step 1 | caught |
| SY-M3 `git add -A` | both | `the_sync_is_one_commit…`, `auto_syncs_before_the_start_baseline`, and beyond the prediction `a_plan_carries_spec_drift_and_a_warning`, `each_refusal_changes_nothing`, `mcp_protected_files_and_a_file_absent_on_one_side`, `the_routes` | `the_committed_spec_is_synced_onto_the_target_branch` / step 9; `a_plan_run_syncs_the_spec_first_when_configured` / step 10 | caught |
| SY-M4 sync while a plan holds the slot | both | `a_running_plan_holds_the_slot`, `each_refusal_changes_nothing` | `spec_sync_is_refused_while_a_plan_runs` / step 7 | caught |
| SY-M5 auto after the baseline | both | `auto_syncs_before_the_start_baseline`, and beyond the prediction `an_auto_sync_refusal_is_the_runs_409` (the run no longer syncs, so it no longer refuses) | `a_plan_run_syncs_the_spec_first_when_configured` / step 10 | caught |
| SY-M6 empty commit instead of `nothing_to_sync` | both | `each_refusal_changes_nothing`, `the_routes` | `the_committed_spec_is_synced_onto_the_target_branch` / step 12; also `spec_sync_is_refused_while_a_plan_runs` / step 9 | caught |

Every prediction held; the extra failing tests are listed above. **A finding about the test
suite, not the code:** in the blind run several mutants also reported 1-3 pytest *errors*. The
fixture's teardown checked the system temp dir for a leftover `lattice-spec-sync-*` directory,
and the runner runs three mutants at once, so one mutant's in-flight sync showed up in another's
check. No verdict depends on these errors (every mutant has failing tests). Post hoc, the fixture
now points `tempfile.tempdir` at the test's own directory and checks that directory; the
post-hoc rerun is in the table above and does not count toward the blind result.

## Browser check

The Claude-in-Chrome extension was not connected (three attempts: "Browser extension is not
connected"), so the UI was checked with the locally installed headless Chromium
(`~/Library/Caches/ms-playwright/chromium_headless_shell-1234`, already on disk) driven over the
DevTools protocol from a Node 22 script, against `python -m lattice.studio.mock_server --port
8795`:

- Build view, default project, target `build/spec-behind` (1280 px): the banner reads "The spec
  on main is newer than build/spec-behind (REQUIREMENTS.md, spec.trace.json)." with one button,
  "Sync spec". Pressing it: toast "Synced the spec onto build/spec-behind: 2 files, one commit.",
  `GET /api/branches` then says `in_sync: true` for it, and a second POST answers 409
  `nothing_to_sync`.
- **Bug found and fixed:** in that first run the banner did not clear after the sync (the redraw
  key was reset to the "no banner" value, so the redraw was skipped). Fixed in `areas.js`; rerun:
  the banner clears in the Build view and in the plan view.
- Plan view (a plan onto `build/spec-behind`): the banner is shown under the actions, the drift
  warning is not repeated in the warnings list; Sync spec clears it (the plan reloads with
  `spec_drift: null`).
- 400 px wide (second project): the banner wraps, `scrollWidth` = `clientWidth` = 400 (no
  horizontal scroll), before and after the sync.
- Inline refusal: with a scoped build running, Sync spec shows "build/spec-behind cannot be
  synced while a build or plan runs. Sync it when that has finished." in red under the button;
  after the build finished, Sync spec succeeded and the banner cleared.
- No console errors or exceptions in any run. The mock server and the headless browser were
  stopped afterwards.

Not checked in a browser: the banner hidden while a plan *runs* (the code hides it when
`state.planActive.status === "running"`; the static test checks the condition is there).

## Decisions not in the brief

- Refusal order: `branch_protected` is checked before `no_branch`, so a protected name is refused
  whether or not the branch exists (the seed's protected `release` cannot be created through the
  API, so this is also what makes the refusal observable in the dogfood).
- `build_running` is checked after `branch_checked_out`, so in the dogfood the checked-out `main`
  answers `branch_checked_out` and the slot is tested on an existing, free branch.
- The sync takes no build slot of its own: a build or plan starting during a sync is safe because
  both sides land with a compare-and-swap `update-ref`.
- The temporary worktree is `--no-checkout` with `read-tree <tip>`: only the spec files are ever
  written (cheap on a large repository), and a stray `git add -A` would stage deletions (which is
  what makes SY-M3 loud).
- In auto mode, a target whose spec is the same, or that does not exist yet, is left alone
  (`nothing_to_sync` is not a refusal of the run); a target to be created is compared with its
  `from` commit for `spec_drift`.
- `spec_drift` holds exactly `{head, files}`; the UI takes the current branch's name from `GET
  /api/branches`.
- The sync commit body names the files ("The spec files … as committed on <branch> at <sha10>,
  synced by Lattice Studio."); the Claude co-author trailer is not added (no agent made it).
- The mock does not simulate `sync_spec = "auto"`.
