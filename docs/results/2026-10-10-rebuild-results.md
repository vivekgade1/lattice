# Results: the Studio rebuild loop, and its dogfood check (2026-10-10)

Pre-registration: `docs/results/2026-10-10-rebuild-prereg.md`. It was written before any
mutant below was built or run, and it has not been edited since.

## What was built

- **`lattice/studio/rebuild.py`** (new): the rebuild loop. `[rebuild]` config, the
  `capability` check, `gate_env`, `parse_gate_output`, `brief`, `build_rebuild_argv`, and
  `Rebuilder` (one rebuild at a time per project, run on a background thread).
  - A git worktree on `rebuild/<change>[-n]`, under `<repo>/.lattice/rebuilds/`. The
    directory is listed in `.git/info/exclude`, not `.gitignore`.
  - Each round: an agent step (`claude -p` in the worktree, or the TEST-ONLY stub), then
    the spec-integrity check with restore, then Studio's own gate runs.
  - Possible endings: passed (one commit on the branch, worktree kept, `merge_hint`),
    gave up, or stopped (process group killed, worktree removed, branch removed if it has
    no commit of its own).
- **`lattice/studio/server.py`:**
  - `POST /api/rebuild`, `GET /api/rebuild/{id}` (`?wait=S`), `DELETE /api/rebuild/{id}`
    and `GET /api/rebuild`, under `/p/<name>/api/...` too.
  - `build` on accepted changes in `GET /api/model`.
  - `StudioConfig.rebuild` and `.mcp`.
  - Running rebuilds stop when the server closes.
- **`lattice/studio/hub.py`:** `capabilities.rebuild` is now computed, not hard-coded `false`.
- **Mock parity** (`mock_server.py`):
  - The record gains `branch`, `base`, `worktree` and `merge_hint`; `build` gains `branch`
    and `commit`.
  - A passing mock rebuild no longer moves `git_head`.
  - Both mock projects report `capabilities.rebuild: true`.
- **`API.md` v1.7:** documents all of the above.
- **Web UI:** the passed-rebuild card shows the branch and the merge hint
  (`web/js/views/rebuilds.js`).
- **Tests:** `tests/studio/test_rebuild.py` has 17 tests, run against a fake `claude`
  and the stub agent, one of them a mock-parity test.

## The dogfood check

`dogfood/studio` gained rules R32–R40 (section "Rebuild") and four scenarios. Their trace
links all point to scenarios; three `Unchecked` notes say what stays with the unit tests.

The seed project gained three things:
- `gate.sh`, which passes if and only if `built.txt` exists;
- `stub-rebuild.json`. Round 1 holds for 2 s, writes `progress.txt` and overwrites
  `REQUIREMENTS.md`. Round 2 writes `built.txt`;
- a `[rebuild]` table with the stub agent.

Changing the seed changed its reproducible ids, so they were re-recorded from the real
server (twice, with identical results) and checked with `git log --all --name-status`:

| constant | id | what it pins |
|---|---|---|
| `SEED_HEAD` | `8b8f66a…` | the seed commit (now with `gate.sh`, `stub-rebuild.json`) |
| `STARRED_COMMIT` | `cf22fc5…` | the accept of "Notes can be starred." (M REQUIREMENTS.md, M spec.trace.json), parent `SEED_HEAD` |
| `REBUILT_COMMIT` | `c1ca55b…` | the rebuild commit on `rebuild/c1`, parent `STARRED_COMMIT`, `A built.txt`, `A progress.txt` (REQUIREMENTS.md restored, so it is absent) |

On the final code, `./check.sh` gives `PASS: 0 findings (D 0, X 0); 159 cases run`. Before
the post-hoc step the result was 158 cases. 23 scenarios run in about 20 s.

The four new scenarios:

- `a_rebuild_passes_in_round_2_on_its_own_branch`:
  - Accept c1, POST, then a second POST, which returns 409 "already rebuilding".
  - `GET ?wait=30` returns `passed`.
  - Round 1 fails with two findings: `spec files were edited…` (target `spec`) and the
    gate's `FAIL built.txt is missing` (target `scenario:built`). Its gate exits 1 and its
    `files_changed` is `["progress.txt"]`.
  - Round 2 passes.
  - The commit is `REBUILT_COMMIT`, on branch `rebuild/c1` with base `STARRED_COMMIT`.
  - Afterwards, `GET /api/model` still shows the starred markdown and
    `git_head = cf22fc5`, and `build` is `built` with branch and commit.
  - A DELETE after the end returns 409. Rebuilding again returns 409 "already built".
- `a_rebuild_gives_up_at_max_rounds_and_can_run_again`:
  - With `max_rounds: 1`, the result is `gave_up` with `merge_hint: null`, and `build` is
    `gave_up`.
  - A new POST returns `b2`, which passes on `rebuild/c1-2`.
- `one_rebuild_at_a_time_and_stop`:
  - With two accepted changes, rebuilding c2 while b1 runs returns 409 "a rebuild is
    already running in this project (b1)".
  - DELETE returns `stopped`, with `rounds: []`, `branch: null` and `worktree: null`.
    c1's `build` goes back to `not_built`.
  - A second DELETE returns 409. c2 then rebuilds to `passed` on `rebuild/c2`.
- `rebuild_refusals`: an unknown change returns 404, bad bodies return 422, a ready (not
  accepted) change returns 409, an unknown rebuild returns 404 on GET and DELETE, and a bad
  `wait` returns 422.

`projects_lists_the_served_project` now expects `capabilities.rebuild: true`.

## Mutation run

The method is the same as in the earlier dogfood run. Each mutant is the frozen `lattice/`
package copied to the scratchpad with one patch applied. `./check.sh` runs with
`LATTICE_STUDIO_PYTHONPATH=<copy>`, so the server process imports the copy. The builder is
`scratchpad/mutants-r1/make_mutants.py`, which reuses the exact M1–M6 patches from
`scratchpad/mutants/make_mutants.py`. Control (unpatched copy): `PASS … 158 cases run`.

### Blind (the pre-registered spec, sha256 `ada127d5…`; server `f80af52f…`)

| id | mutant | predicted catch | outcome | exit | as predicted? |
|---|---|---|---|---|---|
| M1 | accept twice allowed | `a_second_accept_is_409` step 4, by detail | caught there: 409, detail "the spec changed since this proposal" | 1 | yes |
| M2 | cancel after ready | `cancel_after_ready_is_409` step 3 | caught there: 200 instead of 409 | 1 | yes |
| M3 | no supersede | `a_follow_up_supersedes_its_parent` step 5 | caught there: c1 `ready` | 1 | yes |
| M4 | stale accept allowed | `a_stale_proposal_is_409` step 6 | caught there: 200 with a commit | 1 | yes |
| M5 | two commits | `accept_commits_once` step 3 plus every step expecting `cf22fc5…` | caught in 9 scenarios (the 5 old ones, `a_project_prefix_serves_the_same_project` and the 3 rebuild scenarios that accept c1): HEAD `258aee9…` | 1 | yes |
| M6 | reject after accept | `an_accepted_change_cannot_be_rejected` step 4 | caught there: 200 instead of 409 | 1 | yes |
| R-M1 | passes without running the gates (trusts the agent) | `a_rebuild_passes_in_round_2_on_its_own_branch` step 7; also `…gives_up…` step 5 | caught at both: one round with verdict `pass`, then `passed` instead of `gave_up` | 1 | yes |
| R-M2 | merges into the current branch | `a_rebuild_passes_in_round_2_on_its_own_branch` step 8 (`git_head`) | **survived**: `PASS … 158 cases run` | 0 | **no** |

**Blind result: 7 of 8 caught, all at their predicted step. R-M2 survived.**

**Why R-M2 survived: a real bug in the server.** By hand (`scratchpad/r1/record.py` with the
R-M2 copy), the merge really happened: the owner's `main` fast-forwarded to `c1ca55b`. But
`GET /api/model` cached `project.git_head` together with the model, and that cache is keyed
on the hash of the three spec files. The scenario reads the model once after the accept,
which fills the cache. The merge does not touch those files, so the model went on reporting
the old HEAD. Any commit that leaves the spec files alone has the same blind spot: the
owner merging a rebuild branch, or committing code in a terminal. `GET /api/projects` reads
HEAD live, so the two endpoints disagreed.

### Post hoc (changes made after seeing the blind result)

1. **Fix** (`lattice/studio/server.py`, `Studio.get_model`): `git_head` is now read live on
   every call (one `git rev-parse`). Regression test:
   `tests/studio/test_server.py::test_git_head_follows_a_commit_that_leaves_the_spec_files_alone`.
   It fails on the frozen pre-fix copy (`'10e2a35' != '10e2a35'`) and passes after the fix.
2. **Scenario step:** `a_rebuild_passes_in_round_2_on_its_own_branch` gained a
   `GET /api/projects` step that expects `git_head = cf22fc5`. It reads HEAD live, so it
   catches a merge even without the fix.

Re-run on a fresh frozen copy that includes the fix (`scratchpad/mutants-r1-posthoc/`):

| | control | M1 | M2 | M3 | M4 | M5 | M6 | R-M1 | R-M2 |
|---|---|---|---|---|---|---|---|---|---|
| exit | 0 (159 cases) | 1 | 1 | 1 | 1 | 1 (9 findings) | 1 | 1 | 1 |

In this run R-M2 is caught at its pre-registered step 8 (`GET /api/model`: `git_head`
`c1ca55b` instead of `cf22fc5`), and also in
`a_rebuild_gives_up_at_max_rounds_and_can_run_again` step 9. **Post hoc: 8 of 8 caught.**
This number does not replace the blind 7 of 8.

## Other verification

- `tests/studio/test_rebuild.py`:
  - pass in round 2, with the findings fed into the round-2 prompt via `--resume`;
  - gave up at `max_rounds`, and a rebuild again on `-2`;
  - DELETE during the agent step kills its process group, including a `sleep 60`
    grandchild, and removes the worktree and branch;
  - a spec or gate-script edit fails the round and is restored;
  - the owner's HEAD and `git status` stay unchanged throughout, and nothing is merged;
  - one rebuild at a time;
  - the argv: `acceptEdits`, Bash limited to `Bash(./gate.sh:*)`, `--resume` only from
    round 2, the MCP server `--readonly` and the guard on the worktree;
  - `ANTHROPIC_*` and `CLAUDE_CODE_USE_*` are stripped even when set;
  - the gate imports from the worktree;
  - a `uv run` gate uses the main `.venv` without syncing it (site-packages unchanged) and
    imports the worktree's module;
  - a usage limit becomes an error round and the rebuild gives up;
  - a crashing agent is an error round;
  - the stub agent with temp worktrees;
  - the routes under `/p/<name>/api/rebuild`, with their 404/409/422;
  - the capability flag.
- By hand, on the lattice repo itself: a detached worktree with
  `gate_env(project_rel="dogfood/studio")`. Running `uv run python -c "import lattice…"`
  there resolved `lattice` and `lattice.studio.server` to the worktree. The main
  `.venv/site-packages` listing and its `_editable_impl_lattice.pth` (still pointing at the
  main checkout) were unchanged, and no `.venv` was created in the worktree. That worktree
  was removed afterwards.

## Decisions for the owner

- **The gate scripts are protected too.** If a gate's first token is a tracked file
  (`./check.sh`), it is treated like the spec files: an edit fails the round and the file
  is restored. This goes beyond "spec/requirements/trace" and stops an agent from passing
  by editing the gate.
- **After a passed rebuild, `app.last_check` and `git_head` do not move** on the real
  server: they describe the owner's checkout until the owner merges. The mock still
  previews the rebuilt code and a passing check. That gap is intentional for the UI demo;
  change it if the mock should match the real server exactly.
- **Build state is in memory.** After a restart, accepted changes show `not_built` (the
  change records themselves are in memory too). The rebuild branches stay in git. Restoring
  build state from existing branches was skipped.
- **A usage limit ends the rebuild** (`gave_up`, `error.kind: usage_limit`). Other agent
  failures count as a round and the loop continues.
- **Not verified with the real `claude` CLI** (this node made no real LLM calls): that
  `--allowedTools 'Bash(uv run pytest tests/studio -q:*)'`, a rule with spaces inside the
  parentheses, parses as one rule, and that `acceptEdits` with that allowlist denies other
  Bash commands in `-p` mode. A gate command that contains a comma would split the
  allowlist; none of the configured gates do.
