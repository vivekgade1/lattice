# 2026-10-10: Studio improves Lattice (one real end-to-end run)

**Outcome: B (gave up after round 1), but not because the agent failed.** The real drafting run
produced a correct spec change. The real coding agent produced a correct, minimal code change and
a test. Then the rebuild loop **crashed on a Studio bug** (`git add` pathspec, now fixed here,
uncommitted). It also showed that **the `uv run pytest tests/studio -q` gate cannot pass inside
Studio's own gate environment** (a self-referential env leak that needs an owner decision). Both
problems are real findings that only a real self-hosted run could surface. The stub e2e tests did
not catch either.

## Setup

- Throwaway clone: `/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/4a12ca95-cf29-48df-94c4-af809da90b17/scratchpad/lattice-clone`
  (`git clone --local`, branch `roadmap`, base f9a0981, local identity "Lattice Studio <studio@lattice.local>").
  Project `dogfood/studio`, `[rebuild] repo = "../.."` resolved to the clone root.
- Studio started from the owner's checkout venv: `uv run lattice studio <clone>/dogfood/studio --port 8841`
  (no `--stub-llm`; model sonnet). `/api/projects` returned `lattice-studio` with `capabilities {session: true, rebuild: true}`.
- Credentials: the shell, the Studio process and the running agent process (`ps eww`) had **no
  `ANTHROPIC_*` / `CLAUDE_CODE_USE_*` variables**. All calls went through the `claude` CLI subscription login.
- Gate env probe, run during round 1 with the agent's exact env: `import lattice` and `lattice.studio.server`
  resolved to `<clone>/.lattice/rebuilds/lattice-studio-b1-75d483/lattice/...` (the worktree), **not** the
  owner's checkout. PYTHONPATH = worktree, worktree/dogfood/studio, then the owner's checkout (LATTICE_ROOT,
  by design). `UV_PROJECT_ENVIRONMENT=VIRTUAL_ENV=/Users/vivekgade/Documents/lattice/.venv`, `UV_NO_SYNC=1`.
  The test-only probe server further down shows a trap here: with `python -c` and cwd = the owner's
  checkout, `''` comes before PYTHONPATH, so the owner's code would be imported. The gates are not
  affected, because they run with cwd = the worktree.

## Pre-checks (real CLI, haiku, scratch dir; total $0.0062)

The argv was rebuild.py's own (`--setting-sources "" --strict-mcp-config --tools Read,Grep,Glob,Edit,Write,Bash
--permission-mode acceptEdits --allowedTools ...`) run under `env -i`. Gate stand-in: `sh gate.sh ...`.

| # | allowlist | command | outcome |
|---|---|---|---|
| A | comma-joined, `Bash(sh gate.sh -q:*)` | `sh gate.sh -q` | ALLOWED (rule with spaces parses as one rule) |
| A | same | `curl ... https://example.com` | DENIED |
| A | same | `rm victim.txt` (inside cwd) | **ALLOWED**: acceptEdits auto-approves file commands in the cwd |
| A | same | `sh gate.sh -q && rm victim2.txt` | ALLOWED (each part allowed) |
| A | same | `ls` | ALLOWED (read-only) |
| B | no Bash rules (control) | `sh gate.sh -q`, `python3 -c`, `git init`, `rm ../outside.txt` | all DENIED |
| C | comma-joined, `Bash(sh gate.sh a,b:*)` | `sh gate.sh a,b` | ALLOWED: **a comma inside the parentheses does not split the rule** |
| D | separate argv words | both gates | ALLOWED; python3/git/rm-outside DENIED |
| E | `Bash(sh gate.sh -q:*)` | `gate && python3 -c ...` | DENIED |
| E | same | `gate --extra; echo chained` | ALLOWED (prefix rule plus a read-only echo) |
| E | same | `gate $(python3 ...)` | DENIED ("nested command can't be checked") |

Verdict: **no fix needed for (a) or (b)** in CLI 2.1.296. Joining rules with commas works, even
with spaces and commas inside a rule. Caveats for the owner:
1. acceptEdits lets the agent run `rm`/`mv`/`cp`/`mkdir` **inside the worktree**. That is no more
   power than Edit/Write, but it is broader than "you have no other shell commands" in the rules prompt.
2. The `:*` prefix rule allows any extra arguments to a gate, for example `uv run pytest ... -p <module>`.
   This matters little, because the agent writes code that the gates execute anyway: **gates are not a
   sandbox**.

In the real run the agent did hit denials, for `cat`, `env`, `head` in pipelines, heredoc `cat >>`
and pytest with other arguments. It worked around them with Read/Edit and with the exact gate commands.

## Timeline

| time (local) | event |
|---|---|
| 12:16:42 | Studio up on :8841, `GET /api/changes` -> 404 "no such route" (bare and `/p/lattice-studio/...`) |
| 12:17:00 | `POST /api/changes` -> c1 |
| 12:19:41 | c1 `ready` (161 s: edit 16 s, draft 124 s in 2 draft rounds, lint 0.03 s, impact 20 s) |
| 12:19:50 | accept -> commit a33c518 in the clone (owner's HEAD still f9a0981) |
| 12:19:59 | `POST /api/rebuild {changes:[c1], max_rounds:3}` -> b1, branch `rebuild/c1`, worktree under `<clone>/.lattice/rebuilds/` |
| 12:26:34 | b1 `gave_up` after round 1 with `error.kind = internal` (the agent had finished; Studio crashed before running the gates) |

## Phase 1: the draft (c1)

- Rule added: **R5** "GET /api/changes answers 200 with a JSON array of the changes, newest first, each with
  `id`, `status` and `request`; it answers `[]` when there are none." Placed correctly under "Change sets".
- Scenario added: `the_changes_list_is_newest_first`. It covers: empty list `[]`, then POST c1, GET list
  `[{c1}]`, then POST c2, GET list `[{c2},{c1}]`. That covers newest-first, the empty case and the field set.
  View diff: one new cell `no token x GET /api/changes: · -> 200`.
- `checks.valid = true`, no problems, **no conflicts**, no questions.
- Lint: 4x L008, all on **existing** rules (R6, R13, R14, R33 mention "background" with no Await). None
  is on R5. These are pre-existing debt, not caused by this change.
- Impact: `fail`, 1 failing target labelled **`not_built_yet`**: `[D silent] GET /api/changes (step 1)`. Correct.
- Verdict: the draft is right. No follow-up was needed, so it was accepted.
- Accept: exactly one commit a33c518 on the clone's `roadmap`. It touches only `dogfood/studio/{REQUIREMENTS.md
  (+1), spec.py (+15), spec.trace.json (+93/-84)}`. The trace churn is mostly renumbering (R5 inserted, later
  rule ids shift), so it is noisy to review. The commit message carried the session trailer.

## Phase 2: the rebuild (b1)

| round | agent summary | files changed | gates (Studio) | findings | cost |
|---|---|---|---|---|---|
| 1 | "`GET /api/changes` now answers 200 ... `check.sh` passes, but the pytest gate does not: 172 passed, 1 failed (`test_a_uv_run_gate_imports_the_worktree_and_never_syncs`) ... looks like an `UV_PROJECT_ENVIRONMENT` value inherited from my sandbox ... I left it alone." | `lattice/studio/server.py` +10, `tests/studio/test_server.py` +11 | **never ran**: Studio raised `RuntimeError: git add -A -- . ... :(exclude).pytest_cache: The following paths are ignored by one of your .gitignore files` | none recorded (`check: null`) | Studio recorded **$0.00** (the usage was lost with the crash). Estimate from the session transcript: **~$0.35** (22 sonnet messages, 44 in / 5 053 out / 478 522 cache-read / 34 590 cache-write tokens) |

Rounds 2 and 3 never ran, because the internal error ended the rebuild. The branch `rebuild/c1` has **no commit**.
The agent's edits are uncommitted in the worktree (kept).

### Gates re-run by hand on the agent's worktree (same env as the agent)

| gate | base a33c518 (spec only) | agent's worktree |
|---|---|---|
| `dogfood/studio/check.sh` | **exit 1**: `FAIL: 1 finding (D 1)`, `scenario: the_changes_list_is_newest_first` | **exit 0**: `PASS: 0 findings (D 0, X 0); 166 cases run` (20 s) |
| `uv run pytest tests/studio -q` | the one test below fails here too | **exit 1**: 172 passed, 1 skipped, **1 failed**: `test_a_uv_run_gate_imports_the_worktree_and_never_syncs` (71 s) |
| that test with `UV_PROJECT_ENVIRONMENT`/`VIRTUAL_ENV` unset | passes | passes |

So the Lattice gate caught exactly what it should: red before, green after. The pytest gate is
**unpassable inside Studio's gate env, whatever code the agent writes**. The failing test was already
there before this change.

### Diff review (agent's change, `lattice/studio/server.py` + `tests/studio/test_server.py`)

- Route: `Studio.list_changes()` returns `[{id, status, request}]` over `reversed(self.changes.values())`
  under `self.lock`. It is wired as `if path == "/api/changes": return self._dispatch(st.list_changes)`
  in `do_GET`, before the `/api/changes/<id>` branch. **Correct and minimal.** Newest first relies on
  dict insertion order (c1, c2, ...), which matches how ids are issued.
- Prefix: probed with the worktree's code on a stub-LLM Studio (port 8842, a copy of the project, no LLM cost).
  `GET /api/changes` returned `[]`, then `[{c2,...},{c1,...}]`. `/p/lattice-studio/api/changes` returned the
  same, and `/p/nope/api/changes` returned 404 "no such project". **The project prefix is respected**,
  because the hub strips it generically. `?x=1` is ignored and returns 200.
- Test added: `test_changes_list_is_newest_first`. It checks `[]` and then one change. It does **not**
  check the ordering with two changes (despite its name); the dogfood scenario covers that.
- Collateral edits: none. Spec files untouched (`spec_edited` empty, diff shows only the two files).
- Not in the spec and not decided: superseded, cancelled and rejected changes are listed too (probably fine).
  There is no pagination.

## Studio bugs found by this run

1. **FIXED (uncommitted, owner's working tree): `git add` pathspec crash.** `_junk_pathspecs()` added literal
   `:(exclude).pytest_cache` and similar specs. When such a path exists **and** is gitignored, git exits 1
   ("paths are ignored"). The repo's `.gitignore` lists `.pytest_cache`, and pytest gates create it in the
   worktree, so **every rebuild of this repo with a pytest gate would crash** in the snapshot after the
   agent and in the commit. Fix: glob-only excludes (`:(exclude,glob)**/<junk>` plus `**/<junk>/**`; `**/`
   also matches the root, a `.venv` symlink is still excluded). Regression test:
   `tests/studio/test_rebuild.py::test_snapshot_ignores_gitignored_junk_dirs`. It fails with the old
   pathspecs (verified) and passes with the fix; `tests/studio/test_rebuild.py` gives 18 passed; ruff is clean.
   The clone was **not** changed: its branch base stays as Studio committed it.
2. **NOT FIXED, needs owner decision: self-referential gate env leak.** `gate_env` uses
   `env.setdefault("UV_PROJECT_ENVIRONMENT", <repo>/.venv)`. Inside a gate, the env already carries the outer
   `UV_PROJECT_ENVIRONMENT`, so the inner test rebuilder keeps the owner's venv instead of its tmp project's
   `.venv`, and `test_a_uv_run_gate_imports_the_worktree_and_never_syncs` fails. Options: (a) test isolation:
   `monkeypatch.delenv("UV_PROJECT_ENVIRONMENT"/"VIRTUAL_ENV")` in that test. (b) Make the code match its
   docstring ("the main checkout's .venv, else the running venv"): the repo's own `.venv` wins over an
   inherited value. The agent diagnosed this correctly in round 1 but rightly did not touch rebuild code it
   was not asked to change.
3. **Usage is lost on an internal error.** The record shows `usage.cost_usd = 0` and round `agent` absent,
   although the agent ran 6.5 min (~$0.35). Cost accounting under-reports exactly when things go wrong.
4. **The UI on gave-up with an internal error is misleading** (screenshot): round 1 still shows "Agent working"
   with a spinner, the header says "1 round of up to 3 · 1s" (it was 6 min 35 s), and the banner says
   "The checks still fail", although no check ran. The error text itself is shown. There were zero console errors.

## Costs and metrics

| phase | wall time | cost | tokens | outcome |
|---|---|---|---|---|
| pre-checks (5 haiku turns) | ~1.5 min | $0.006 | small | rule syntax OK, comma OK |
| drafting c1 (sonnet) | 161 s | ~$0.59 ($0.531 draft, "cli_list_estimate", + ~$0.06 edit step, which has no cost field) | edit 3 946 in / 3 273 out; draft 76 556 in (63 338 cache-write, 13 214 cache-read) / 27 612 out | ready, valid, no conflicts |
| accept | <1 s | $0 | n/a | 1 commit, spec files only |
| rebuild b1 round 1 (sonnet) | 6 min 35 s | ~$0.35 (estimate; Studio recorded $0) | 5 053 out, 478 522 cache-read, 34 590 cache-write | crashed (Studio bug #1) |
| **total** | ~11 min of LLM time | **~$0.95** | | under the $8 cap |

Program size: the agent's change is +21 lines (server +10, test +11). The spec change is +1 requirement line,
+15 spec.py lines and 93/84 trace lines.

**What the agent got wrong first:** nothing in the feature code. It wasted turns on Bash commands
the allowlist denies (5 denials). It could not confirm its env hypothesis because `env` is denied.
**What the gates caught:** check.sh caught the missing route on base (D finding) and turned green
on the change. pytest caught a real infrastructure defect (bug #2), not a defect in the agent's code.
**What Lattice's spec/gate would NOT have caught:**
- The spec's scenario runs against Studio with the stub LLM, so the drafted request text is the stub's.
- The list's exact key set is checked only through the scenario's body, if the matcher compares the full body;
  extra keys are not explicitly forbidden.
- Concurrency and ordering under simultaneous POSTs, and superseded or cancelled changes in the list, are not specified.
- The UI does not consume the endpoint, and nothing checks that it should.
- Bugs #1, #3 and #4 are in rebuild.py and the web UI, which the dogfood spec does not cover (layers "DX" only).
  The rebuild tests use a fake agent and tmp repos, where neither a gitignored `.pytest_cache` nor a nested
  gate env arises.

## Honest limitations

A single run of one tiny change with one model (sonnet) and one agent attempt. The gates are
self-referential (Studio's tests gate a change to Studio, run by Studio), and that is exactly what
produced bug #2. The round-1 cost is my estimate from the transcript at list prices, not a Studio
record. No round 2 or 3 was attempted: the owner's cap was 1 drafting run and up to 3 rounds, and the
running server could not be fixed without a restart that would lose the in-memory c1 (change sets are
not persisted). Re-drafting would have been a second drafting run, so I stopped. The patch file is
`git diff` output from the worktree, because Studio never committed: use `git apply`, not `git am`.

## For the owner to review

```sh
C=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/4a12ca95-cf29-48df-94c4-af809da90b17/scratchpad/lattice-clone
W=$C/.lattice/rebuilds/lattice-studio-b1-75d483
git -C $C log --oneline -2 roadmap          # a33c518 spec commit on top of f9a0981
git -C $C show --stat a33c518                # the accepted spec change
git -C $W diff                               # the agent's uncommitted code change (= the patch file)
# bring both into a branch of your checkout when you decide to:
git fetch $C roadmap:studio-c1-spec          # gets a33c518
git switch studio-c1-spec && git apply docs/results/2026-10-10-studio-improves-lattice.patch
# Studio bug #1 fix (uncommitted in your tree):
git diff lattice/studio/rebuild.py tests/studio/test_rebuild.py
```

To get a passing run, land fix #1 and choose a fix for #2. Then re-run the same request: one draft
plus up to 3 rounds is about $1 by this run's numbers.

Screenshots (light mode, zero console errors):
`.../scratchpad/v1-shots/rebuild-card-gave-up-light.jpg` (the Rebuild card in the gave-up state).
