# Results: module marketplace gaps (G3)

Pre-registration: `2026-10-15-module-market-prereg.md` (with Amendment 1). Branch `np-market`.
Data: `docs/results/data/2026-10-15-marketplace/`.

## What was built
Git-sourced module install (`lattice/modules/git.py`: pinned-sha https sources only, a hardened and
isolated `git` sequence with fsck on, a file/size/name listing check, a clean env and an empty HOME,
`--allow-remote` required), a git-backed module index, the `--engine-file/--engine-dir` flags on `ask` and
`draft` as on `check`, and fail-closed hub isolation: each project's engines live in a registry of its own
so two projects in one hub can no longer see or replace each other's engines (ABAB, BABA, AABB identical
to each project alone; 200 requests, 0 mismatches).

## Result
Orchestrator re-run (2 runs): **64/65 probes pass**. The builder's 10-run execution: 63/65 (one more was
the test below, fixed). Mutants: MG1-MG22 plus a4ab4a1's MM1-MM5 = 27/27 killed (control passes with the two
findings excluded). Blind baselines on a4ab4a1 (31/31 as predicted) show the gaps were real: ask/draft
lacked `mariadb`, B's engines collided with A's in a hub, B silently got A's class.
Regression after the F1 fix: `tests/modules tests/spec/test_engines.py test_ask.py test_draft.py
tests/studio tests/test_cli.py` 716 passed, 4 skipped (the live LLM smoke tests), 0 failed;
`check.sh` PASS 435 cases.

## Findings
**F1 (resolved).** The single assertion changed under Amendment 1 (`test_engines_api.py:130`) could not
pass: `Studio.__init__` called `get_engines()` in the constructing thread, which activated the project
registry there. Orchestrator ruling: edit `lattice/studio/server.py` so the constructor runs
`get_engines()` inside `engines.using(engines.project_registry())` (6 lines). Verified: regression above.
**F2 (open, not fixed).** M-tamper-T12 FAILS its prediction: a custom module's `entry` (Python imported
into the checker) is not announced as CODE in the install summary, only an adapter is. This is the
a4ab4a1 behaviour of `describe()`, which directory-install output must keep byte-identical; changing it is
a separate decision for the owner (the code is only imported when the engine is used, and `--allow-remote`
gates git sources).
**F3.** T6's user message keeps git's last stderr line (`fatal: index-pack failed`) and does not name
fsck as the cause (the probe confirms fsck is the cause).
**F4.** `git@github.com:o/r.git` is told to pin a sha rather than to use https (first-hit-wins, 2.1).
**F5.** Names starting with `_` (`__init__.py`) are refused, so a package adapter cannot come from git.
**Flakes.** M-tamper-T4 differed between runs once under machine load (evidence overwritten, later 22+
runs identical); `tests/studio/test_projects.py` failed once in a loaded run and passed alone 4 times.

## Not done
The descriptor design doc was not updated here (integration step).
