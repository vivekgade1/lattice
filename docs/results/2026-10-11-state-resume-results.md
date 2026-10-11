# Results: durable state and resume (API v1.10), mutation run (2026-10-11)

Pre-registration: `docs/results/2026-10-11-state-resume-prereg.md` (written before any
mutant ran). The six mutants were built by `dogfood/studio/state_mutants.py` as patched
copies of the frozen tree (hashes re-checked by the harness on the copy: `state.py`
`9fadefd11f8b14d9`, `plan.py` `b8cdf530f92fe624`, `rebuild.py` `151a6290f4f5e505`,
`server.py` `247cd7258503d43b`, `test_state_store.py` `38945a28afb4f608`,
`test_resume.py` `8b2d80b556716169`) and run one at a time against
`tests/studio/test_state_store.py` + `tests/studio/test_resume.py`. Every patched text
occurred exactly once; none was adapted. The working tree was never patched.

## Blind result (the pre-registered run)

Control (the unpatched copy): `19 passed`, exit 0.

| id | behaviour | exit | failing tests | as predicted? |
|---|---|---|---|---|
| S-M1 | state file written non-atomically | 1 | `test_a_write_killed_midway_leaves_the_old_file_valid` | yes (the certain prediction). The "likely, not required" SIGKILL test did **not** fail: its five kills did not land inside a write |
| S-M2 | a running plan stays running after a restart | 1 | `test_a_running_plan_and_its_orphans_are_reconciled_on_load`, `test_a_studio_killed_mid_plan_resumes_without_rerunning_passed_nodes` | yes, both |
| S-M3 | resume re-runs passed, landed nodes | 1 | `test_a_studio_killed_mid_plan_resumes_without_rerunning_passed_nodes` | caught, but only by one of the three predicted tests: the two land tests passed (see post hoc) |
| S-M4 | land moves a checked-out branch without confirmation | 1 | `test_a_gated_level_that_could_not_land_is_landed_without_any_agent` | yes |
| S-M5 | a corrupt state file crashes startup | 1 | `test_a_corrupt_file_is_quarantined_and_loading_goes_on`, `test_corrupt_state_does_not_crash_startup` | yes, both |
| S-M6 | orphan cleanup kills by pid without the command line | 1 | `test_a_running_plan_and_its_orphans_are_reconciled_on_load` | yes |

**6 of 6 caught** (the headline prediction holds). No test outside the predictions failed.
Two secondary predictions were wrong: S-M1's SIGKILL test (flagged as probabilistic in the
pre-registration) and S-M3's two land tests.

## Post hoc (after the blind run; nothing was changed in code or tests)

- **Why S-M3 escaped the land tests.** With the skip of passed nodes removed, the run after
  the land does put node `w` back in its level, but `w`'s in-scope checks already pass on
  the landed tip, so `start_node` answers "nothing to build" and no agent runs: the stub's
  invocation log stays at one. Only a node that always runs (the scaffold) or whose checks
  fail again re-invokes its agent; the restart test has a scaffold, so it catches S-M3.
  The mutant's real damage is therefore narrower than its name: re-run scaffolds (and
  re-gated nothing-to-build nodes), not re-run areas. A sharper test would put a scaffold in
  the land plan; it was not added, to keep this run blind.
- **Why the SIGKILL test missed S-M1.** Each save of a 3 MB record is a few milliseconds of
  writing between longer stretches of JSON encoding; five kills at fixed delays did not hit
  that window in this run. The deterministic half-write test is the one that guards
  atomicity; the SIGKILL test stays as a sanity check of the real path (it passes on the
  atomic code in every run so far).

## Gates (final tree)

See the report of the S1 node for the final pytest gate, `dogfood/studio/check.sh`
(39 scenarios, 5 new: R58-R62; R63, restarts, is Unchecked with its reason) and ruff.
