# Implement with verification (Spike E): results

**Arms run: the Docker-free core only.** The Streak arm (SO, SO3, SA, SL, SG) and PAg-MS are **NOT RUN** (reasons
in section 6). E passes **core only**, and says so (prereg section 8). Nothing NOT RUN is described as passed.

Pre-registration: `2026-10-17-implement-verify-prereg.md` with Amendment 1 (unedited). Worktree
`/home/user/wt/np-implverify`, base `5125da1` (`6a318e5` + the pre-registrations). Builder made no commit and
staged nothing. Data: `docs/results/data/2026-10-17-implement-verify/` (`core/probes.json`, `core/mutants.json`,
`core/runs/*.json` = the output report of each change, `pytest/`, `freeze.sha256`).

## 1. Headline

| row | result |
|---|---|
| Core probes (16 gated) | **16 PASS, 0 FAIL** (PV PR PBF PU PT2 PSB PT3E PH PG PNM PSC PC PX PM PD PBC) |
| Real-agent probe PAg-MS | **NOT RUN** (USD 3.00 budget not approved by Amendment 1) |
| Streak arm SO SO3 SA SL SG | **NOT RUN** (no Streak checkout, no Docker daemon) |
| Mutants | **16/16 killed**, control passed first (catchers are named tests, disclosed in section 5) |
| New tests | 77 pass (`tests/spec/test_impact_tier4.py` 27, `tests/studio/test_implement.py` 50) |
| pytest `tests/spec tests/mcp tests/studio tests/importer tests/gocheck` | baseline 8 failed / 98 errors; after 7 failed / 98 errors; **0 new failing ids** (section 4) |
| ruff | `ruff check lattice dogfood tests/spec tests/studio` clean; `ruff format --check` clean on every file created or edited (existing files I did not touch were not reformatted) |
| Prediction misses | 4 non-gating misses, 2 process findings (section 7) |

Recorded probe stage: 447 s wall for the whole stage; the 10 changes took 44.1 s in all (1.4 to 9.7 s each).

## 2. What was built

Created:
- `lattice/spec/impact_tier4.py` (pure Tier 4 logic: report checks D2, change dict D5, classes, `spec_backed`,
  T2 confirmation, `t3_vs_edit`, verdict D6, merge D7).
- `lattice/studio/implement.py` (driver D1-D4, owner guard D3, CLI `main`; the before and after run child
  `python -m lattice.studio.implement _side`).
- `tests/spec/test_impact_tier4.py`, `tests/studio/test_implement.py`.
- `tests/spec/fixtures/implement/`: `base/` and six candidate spec dirs (`status_changed reword removed_rule
  new_request conflict bad_invariant`, copies of Spike D's minishop impact fixtures made self-contained, no import
  of `tests.spec.minishop`), `repo/` (gate.py, ms_harness.py, studio.toml, flows.json with 5 verified flows),
  `stubs/` (scripted rounds i1 i2 i3 i4 i8 i9 none), `E_ok.diff`, `ms.py` (builds the mini-shop git repo, runs a dev
  change, snapshots the owner's files).
- `dogfood/implement_probes.py`, `dogfood/implement_mutants.py`.
- This document and its data directory.

Changed (additive, one line): `lattice/spec/__main__.py` `AUTHORING["implement"] = "lattice.studio.implement"`.
`git diff 6a318e5` of every file in D8's last "Never" item is empty (PBC). No existing test, golden or fixture was
edited.

Freeze (taken after the code was final and before the recorded scoring run; `sha256sum -c` still OK after it):
```
cab8265427f6d88663c372c319a51b0f7980f905fbbd7aebf4975f44c43fc60d  lattice/studio/implement.py
b9f88bae84552451d73234adae003ea8dad57b451759b88ed28a8303be976c11  lattice/spec/impact_tier4.py
44fa0d1daf26b79bf12e9b96f5b2f0f38f8ee162fe053a51e8cd4151a189e679  dogfood/implement_probes.py
370a6e31cd876f9f538af4c42642a1ca61e8a339dd5c328f8d1d33aa7b5c4820  dogfood/implement_mutants.py
98fcd43d030117eb95637e380d8d1b3fdbf13a081b483338e38454101dd74196  tests/spec/test_impact_tier4.py
b19be20b5b0ac3789d09910585dc61cf589e2b9790deb9195eecfc0b1a55fdd3  tests/studio/test_implement.py
591a03ed87c3d2721e507ace6c6d371923173f0f4dcd4602ce768fcece4b49e7  tests/spec/fixtures/implement/ms.py
```
The three E files of the prereg's `freeze.sha256` command are the first three lines. `lattice/spec/__main__.py`
(the one additive line) is not in the freeze; it changed once, before the recorded run (section 7, finding F2).

## 3. Probes: predicted vs observed

Recorded run: `uv run python dogfood/implement_probes.py --stage core --scratch $SCRATCH/p --out
docs/results/data/2026-10-17-implement-verify/core --pytest-before before-ids.txt --pytest-after after-ids.txt`.
Every expectation in the runner was written from the pre-registration before the recorded run; the driver was
exercised on the corpus during development (section 7).

| id | prediction (prereg) | observed | result |
|---|---|---|---|
| PV | 11/11 verdicts, rounds, counts as the table of section 3 | 11/11: i1 pass r1, i2 pass r2, i3 fail r1, i4 fail r1, i5-i7 `nothing_to_build`, i8 and i9 `not_built` (`gave_up`, 2 rounds), i10 refused `spec_contradiction`, i11 pass r1. Counts as predicted: i1 resolved 1, `wf-cancel-twice` `flow_changed` `t2_determined` `spec_backed`, `t2_confirmed`; i3 1 unreported (`wf-list`), gates exit 0; i4 1 unreported (`wf-read-ben`), gates exit 0; i11 tier 4 equal to i1's except `rebuild.agent` | PASS |
| PR | i1, i2, i11: `resolved` = the before failing set; 0 regressions, 0 still failing, 0 failing after | `resolved` = `{(D, "scenario: ann cancels her pending order", "... / step 2")}` = the set an in-process `check` of candidate + flows on the base app gives (independent of the driver); the other three counts 0 | PASS |
| PBF | 11/11 `true` | 10/10 `true` on the runs that have a before run; i10 is refused by D2 before any run (no tier 4 exists). The prediction's "11" could not be literally met (finding M1) | PASS (10/10 applicable) |
| PU | i3, i4 `unreported`, verdict `fail` | 2/2 | PASS |
| PT2 | i1: both claims confirmed; planted wf-add claim `t2_contradicted` | `t2_flow_claim` and `t2_scenario_claim` confirmed; planted claim on `wf-add` not confirmed, `t2_contradicted` 1, verdict `fail` | PASS |
| PSB | 8/8 | 8/8 (all match; method, route, user, status differ; template route matches another id; details missing gives `null`; backing step fails after) | PASS |
| PT3E | exact vs counts by hand | exact on 4 diffs (api/api.go in T3: recall 1.0 precision 1.0; models/models.go outside: recall 0.0, missed 1; test file + spec + stub: touched 0; new file: recall 0.0, missed 1) and the declaration check (hunk at api/api.go:98 touches `CreateProject`, decl recall 1.0, precision 0.2; Python: `not_applicable: no extract`) | PASS |
| PH | 10/10 codes | 10/10: `bad_report`, `report_mismatch` x3 (base byte, candidate byte, inventory hash), `stale_inventory`, `spec_contradiction`, `branch_exists`, `branch_protected`, `bad_paths` x2 (`--work`, `--out`); each left no worktree and no changed file | PASS |
| PG | 0 changed, 0 new beyond the allowed list | 0 / 0 on i1-i11, a second failing-gate run (i9, gates exit 1) and a raise mid after-run (exit 3, `not_run:error`); owner snapshot untouched on every run that produced a report. The refused runs of PH also changed nothing | PASS |
| PNM | B not an ancestor of HEAD; HEAD unchanged; `rebuild/<eid>` one commit on `S`; `S^` = HEAD | exact on i1 i2 i3 i4 i11 and on i1 with the stub tracked in HEAD (the case where a fast-forward would have been possible) | PASS |
| PSC | S changes exactly the 3 spec files (+ stub) with the trailer | exact on i1 i11 i3 i5 and on i1 with untracked owner files (`owner-notes.txt`, `shop/scratch.py`): extra files 0, trailer present | PASS |
| PC | worktrees after = before + kept review worktree (1) or + 0; 0 leftover temp dirs; 0 live processes | kept worktree 1 for i1 i2 i3 i4 i8 i9 i11 and the default-`--work` run, 0 for i5 i6 i7 i10; temp entries = kept worktrees; work dir empty; default `--work` removed; 0 processes naming the scratch root | PASS |
| PD | 10/10 identical x {i1, i3, i11}; no path, time, sha | 10/10 each (CLI processes, random `PYTHONHASHSEED`, two scratch roots); scan: no `/home /tmp /Users /private`, no ISO time, no 40-hex outside `spec_tree` and `build_tree`, none of the real commit shas | PASS |
| PBC | all identical / empty / equal | see below | PASS |
| PM | 0 `claude` calls; no key in any child | `claude` shim on PATH called 0 times over i1, i11, i5 with `ANTHROPIC_API_KEY` set in the parent; 7 child environments recorded (gate and before/after run), 0 with a key | PASS |
| PX | <= 30 s each (p 2-8 s) | 1.4 to 9.7 s (i2 9.7 s, i6 and i7 1.8 s); total 44.1 s | PASS (rule); the 2-8 s prediction missed for i2 and the three `nothing_to_build` runs (finding M3) |
| PAg-MS | report-only | **NOT RUN**: the USD 3.00 real-agent budget is not approved by Amendment 1. The `claude` CLI in this container reports `loggedIn: true`, so this is the budget, not the login | NOT RUN |

PBC details (all against `git archive 6a318e5` unless stated):
- `lattice --help`, `lattice spec` usage, `python -m lattice.spec check --help`, `lattice spec impact --help`:
  byte-identical (stdout and stderr).
- Spike D's report on the 7 minishop fixtures (tier 2, `baseline none`) and on the Go implementation fixture
  (tiers 2 and 3): byte-identical (38,872 bytes).
- MCP `INSTRUCTIONS` and `tools/list` for a plain, an implementation and an implementation + impact project:
  byte-identical.
- Frozen sha256 of all 15 files of prereg section 9: all equal. `git diff 6a318e5` of the 12 files of D8's "Never"
  item: empty. `lattice/spec/__main__.py`: exactly the one added line.
- Output report minus `tiers["4"]` and `tier4` byte-equal to the input on all 10 core runs that wrote one.
- `dogfood/studio` (the `check.sh` command, run as `python -m lattice.spec check ...` with `PYTHONPATH` per tree):
  `PASS: 0 findings (D 0, X 0); 509 cases run`, 62 scenarios, on both trees. This stands for the Studio route table,
  `/api/model` and the stub rebuild records (it drives them as a black box); I did not diff the route table or the
  `/api/model` body directly (finding M4 below).
- pytest: same failing ids (section 4).

## 4. pytest, before and after

Command (both runs): `pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck -q -rfE
--continue-on-collection-errors`. `--continue-on-collection-errors` was needed: 12 importer/spec modules fail at
collection with `NameError: name 'Item' is not defined` in this container before any edit.

| run | failed | passed | errors |
|---|---|---|---|
| baseline (no edit) | 8 | 2626 | 98 |
| after run 1 (AUTHORING line appended last) | 9 | 2702 | 98 |
| after run 2 (the recorded one; line inserted before `implementation`) | 7 | 2704 | 98 |

Run 2 vs baseline: 0 new failing ids; `tests/studio/test_projects.py::test_prefix_routing_and_the_bare_default`
failed in the baseline and passed in run 2 (a flaky or order-dependent test; I did not investigate it, and this
work touches nothing it reads). The
remaining 7 failures (`tests/importer`: `test_import_token_answers` x2, `test_mine`, `test_policy` x4) and 98
errors (the `Item` collection error and its dependants in `tests/importer` and `tests/spec`) are the same ids as in
the baseline. Run 1 had 2 new failures (finding F2).

## 5. Mutants (16/16)

Applied to a scratch copy of the tree, never the repository; control (all catchers on the unmodified copy) passed
first. Where the pre-registration names a probe as the catcher, the named test stands for it (the same code paths
on the same corpus), as in Spikes D and F; the probes pass on the unmutated tree and the mutated trees fail the
named tests. All killed.

| id | mutant | killed by |
|---|---|---|
| EM1 | tier 4 from the before run only | `test_an_unexpected_regression...i3_i4`, `test_i1_resolves...` |
| EM2 | after run on `S` instead of `B` | `test_i1_resolves...` |
| EM3 | unreported counted as `t3_may_fail` | `test_an_unexpected_regression...i3_i4` |
| EM4 | verdict ignores regression | `test_regression_fails`, `test_the_verdict_table` |
| EM5 | `git merge rebuild/<eid>` in the owner repo after a pass | `test_a_passed_build_is_not_merged_even_when_nothing_blocks_a_fast_forward` |
| EM6 | spec commit also moves the owner's current branch | `test_the_owners_files_refs_and_index_are_untouched[i1]` |
| EM7 | report hash check skipped (base, candidate, inventory) | `test_report_mismatch_on_base_candidate_and_inventory_bytes` |
| EM8 | `git add -A` in the owner's checkout before the spec commit (interpretation below) | `test_an_untracked_owner_file_never_reaches_the_spec_commit` |
| EM9 | merge also rewrites `summary` | `test_merge_changes_exactly_tiers4_and_tier4` |
| EM10 | `nothing_to_build` starts a rebuild anyway | `test_the_dev_changes_end_as_predicted[i5]` |
| EM11 | `t3_vs_edit` counts test files and the stub | `test_t3_vs_edit_counts_on_four_synthetic_diffs` |
| EM12 | absolute worktree path in tier 4 | `test_the_output_names_no_path_time_host_or_commit` |
| EM13 | `spec_backed` by route only | `test_spec_backed_the_eight_cases` |
| EM14 | after-run worktree not removed | `test_cleanup_leaves_the_review_worktree_only_when_a_build_ran` |
| EM15 | contradicted T2 flow claim not flagged | `test_a_planted_t2_flow_claim_on_an_unchanged_flow_is_contradicted` |
| EM16 | a `gave_up` build gets an after run and a verdict | `test_the_dev_changes_end_as_predicted[i8]`, `[i9]` |

EM8 as written ("the spec commit uses `git add -A`, PSC on a fixture with an untracked owner file") is an
**equivalent mutant** for the as-built design: the spec commit is made in a fresh detached worktree of `HEAD`, which
holds no untracked file of the owner's, so `git add -A` there stages exactly the files E wrote. The mutant I
applied is `git add -A` in the owner's checkout (the only place an untracked owner file exists); it changes the
owner's index and untracked set and is killed. This is a deviation from the row's literal text (section 8, D5).

## 6. NOT RUN, with the reason

| row | reason |
|---|---|
| PAg-MS (real `claude` agent on i1, i3's candidate, `bad_invariant`) | Amendment 1: the USD 3.00 real-agent budget is not approved and needs the owner's yes. The CLI is logged in, no model call was made. The `claude` path is exercised only up to `capability()` (refused `no_rebuild` when the binary is missing) and by the `--usd-cap` poll against a fake rebuilder. |
| SO, SO3 (Streak, oracle; `t3_vs_edit` vs D's PT3 truth) | no Streak checkout and no Docker daemon in this container; Amendment 1: a separate orchestrator step |
| SA (Streak, real agent, 6 changes) | same, and the USD 12.00 budget is not approved |
| SL (leak of `lattice-` containers on Streak) | no Docker daemon; the owner-token cleanup (`owned_containers`) is exercised, but no container exists to leak |
| SG (PG and PNM on every Streak run) | no Streak clone; PG and PNM pass on the mini-shop corpus |

Untested because of those rows: the `--inventory` path end to end, tier 3 on a real change (only the Go fixture's
static report, in PT3E), `t3_may_fail` flow changes against a real T3 (synthetic reports only, risk 6), MySQL
nudger timing (risk 4), and the `claude` agent.

## 7. Findings (every gate or prediction missed, stated plainly)

No gated core probe failed in the recorded run. The following are what did not go as pre-registered; nothing was
tuned after the recorded scoring run.

Predictions missed:
- **M1 (PBF wording).** The prediction was "11/11 `true`". i10 is refused by D2 (`spec_contradiction`) before the
  before run, so it has no tier 4 and no `matches_report`; the achievable count is 10/10. Scored 10/10 applicable.
- **M2 (i11 equals i1).** My first oracle script carried a `summary` key, so `spec_tree` and `build_tree` of i11
  differed from i1's (the committed `stub-rebuild.json` differed) and the predicted "the same tier 4 as i1 except
  `rebuild.agent`" did not hold. Found in a development test, not in the recorded run. The fix was to emit the
  oracle script in the same canonical form as the hand-written stub scripts (no summary); after it i11 equals i1
  except `rebuild.agent`. The prediction as worded needs the two scripts to have the same bytes; it is not a
  property of the oracle.
- **M3 (time).** PX predicted 2-8 s per change and 40-120 s for the full core stage. i2 took 9.7 s, i6 and i7
  1.8 s, i5 1.4 s (below the range: no rebuild). The ten changes took 44 s, inside 40-120 s, but the whole stage
  (probes + 30 CLI runs of PD + two 56 s Studio checks of PBC) took 447 s. The pass rule (<= 30 s each) holds.
- **M4 (PBC scope).** The prereg lists "the Studio route table, `/api/model`, and a stub rebuild record of
  `dogfood/studio`'s seed" as identical. I did not diff the route table or the `/api/model` body. I compared the
  Studio server as a black box through its 62 `dogfood/studio` scenarios on both trees (same result, same 509
  cases) and the frozen sha256 / empty diff of `server.py`, `areas.py`, `rebuild.py`, `state.py`, `session.py`.
  Those files are byte-identical to `6a318e5`, so their behaviour cannot differ; it is an argument, not a direct
  comparison.

Process findings:
- **F1 (development dry runs before the recorded run).** I ran probes and the mutant runner on the corpus during
  development. One bug showed: the PBC Studio comparison masked only `Ns` durations and failed on `start 0.81`
  versus `start 0.39` in the timing line (a bug in my comparison, not in the product). I fixed the masking
  before the freeze. The recorded run is the one after the freeze, with the files in section 2.
- **F2 (the AUTHORING line).** Appending `"implement"` at the end of `AUTHORING` broke two existing tests that pin
  the last four keys (`tests/spec/test_appmap.py::test_the_cli_is_one_authoring_entry_and_help_is_unchanged` and
  `tests/spec/test_impact_report.py::test_lattice_spec_impact_is_one_authoring_entry`: `list(AUTHORING)[-4:] ==
  ["implementation", "rationale", "glossary", "impact"]`). The prereg said "one `AUTHORING` line" without a
  position. I placed the line before `"implementation"` (dispatch does not depend on order); both tests pass
  unedited and `lattice spec` usage is byte-identical. A full pytest rerun followed (section 4, run 2). The first
  full run (run 1) is kept as evidence.
- **F3 (`branch_checked_out` before `branch_exists`).** After a passed build the kept review worktree has
  `rebuild/<eid>` checked out, so a second `implement` on the same candidate is refused `branch_checked_out`, not
  `branch_exists` (the CLI test shows it). `branch_exists` is what an existing, not checked-out branch gives (PH).
  Both are refusals with exit 2; the prereg names `branch_exists` and "protected or checked-out" as codes, so this
  is within D3, not a miss, but a reader should not expect `branch_exists` for a repeat run.

Soundness of D, T2: 0 contradictions on the dev corpus (the planted claim in PT2 is the only one). T3 `may_fail`
and `t3_vs_edit` are not tested end to end here (Python has no T3, risk 6).

## 8. Deviations from the pre-registration

- **D1 (before/after run in a child process).** D4 says the runs use `harness_runner(...)` through `run_tier1(...,
  baseline="none")`. They do, but inside a child process (`python -m lattice.studio.implement _side`, cwd = the
  worktree's project dir, the rebuild gates' environment). In process, the harness modules (`ms_harness`, `shop`)
  are cached in `sys.modules` after the first run, so the "after" run would have imported the "before" code, and
  `_fill_check_args` mutates `sys.path` and cwd. The Python API takes an injected `side_runner` (used by the tests
  to raise mid-run and to trip the guard).
- **D2 (ident without `key`).** D's `_ident` is `(layer, rule, case, key)`; a report's Tier 1 item has no `key`
  (`key` is a function of layer, endpoint and case, or of code and message for layer A). `before.matches_report`
  and the classes use `(layer, rule, case, endpoint, code)`, and for the flows `(kind, flow)`.
- **D3 (`compute` signature).** `impact_tier4.compute(report, before, after, flows, rebuild, edit)` takes extra
  keyword arguments (`cand_steps`, `links`, `route_key`, `implementation`, `owner`, `tree`, `prefix`, `notes`);
  the driver passes them, the pure unit tests pass what they need.
- **D4 (stub script location).** D1 has no flag for the stub script. In `stub` mode the script is read from
  `<project>/stub-rebuild.json` in the owner's checkout (an untracked file in the corpus) and committed in `S`
  (listed as `test_only_files`); a missing file is refused `no_rebuild`.
- **D5 (EM8)** as above, and the catchers of the mutants are named tests, not the probe runs (section 5).
- **D6 (exit 3 states).** Owner guard tripped: `tiers["4"] = "not_run:owner_changed"`, the computed tier 4 kept with
  `owner.untouched = false` and verdict `fail`. A raise in the after run: `not_run:error: ...`, verdict `fail`. A
  raise in the before run: `not_run:error: ...`, verdict `not_built`. The prereg table has no row for a raise.
- **D7 (D report for the corpus).** The mini-shop is Python, so there is no extract and no inventory directory.
  Spike D's report for it is built with D's API and a duck-typed inventory that carries the verified flows
  (`workflows_sha256` = sha256 of `flows.json`); E reads the same file with `--flows`. Tier 3 is `not_requested`
  on the corpus (as the prereg's section 3 and risk 6 say).
- **D8 (probe runner reads a probe hook).** The gate and the before/after child append `{who, key: bool}` to the
  file named by `IMPLEMENT_PROBE_ENVLOG` when it is set (used by PM; unset it does nothing).
- **D9 (`--work` refusal).** `--work` and `--out` are also refused inside the repository's top level (not only the
  project, app root and inventory), so E's worktrees never appear as untracked files in the owner's checkout.

## 9. Reproduce

```sh
export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1
SCRATCH=<scratch>/implement
uv run python -m pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck -q -rfE \
    --continue-on-collection-errors > "$SCRATCH/pytest-after.txt"
uv run python -m pytest tests/spec/test_impact_tier4.py tests/studio/test_implement.py -q
uv run python dogfood/implement_probes.py --stage core --scratch "$SCRATCH/p" \
    --out docs/results/data/2026-10-17-implement-verify/core \
    --pytest-before "$SCRATCH/before-ids.txt" --pytest-after "$SCRATCH/after-ids.txt"
uv run python dogfood/implement_mutants.py --scratch "$SCRATCH/m" $(seq -f EM%g 1 16) \
    --out docs/results/data/2026-10-17-implement-verify/core/mutants.json
```
The Streak arm (`--stage streak`) is not implemented in `implement_probes.py`: it needs the owner's machine, the
frozen D engine and D's change set; it is the orchestrator's step after the core freeze.
