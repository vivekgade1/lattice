# Spike 3, the apply step (`lattice improve apply`): pre-registration

## 1. Status

Date: 2026-10-15. Branch `np-apply` (worktree `lattice-apply`, from `next-phases` a4ab4a1). Written BEFORE
any code of this change exists; nothing of the new behaviour was run. Sources: the code at a4ab4a1, the
committed data of spike 3 and improve2, the OLD `lattice improve draft imp-3e1cd7f9` run on scratch copies,
predicted bytes written by hand from rule 2.4 and hashed in scratch bare clones, `pytest --collect-only`
counts. Results: `2026-10-15-improve-apply-results.md`, data `docs/results/data/2026-10-15-improve-apply/`. No LLM,
no API key, 0 tokens; the command calls no model.

**Scope (the orchestrator's).** `lattice improve apply ID [--project DIR] [--state DIR] [--branch NAME]
[--no-gates] [--json]` and `lattice improve apply --discard ID [--project DIR] [--state DIR] [--json]`:
`lattice/improve/apply.py` (+ `__main__.py`, `lattice/cli.py` usage), `tests/improve/test_apply.py` (a test
per probe row, cheap gates), `dogfood/improve_apply_probes.py`, mutants IA1-IA16. **Not done:** Studio
API/UI, agent code changes, other kinds, merging, closing the ledger.

## 2. Decisions (settled before code)

**2.1 Applicable proposals; the literal is the draft's.** Apply first calls the existing `draft()` on
exactly what `lattice improve draft ID` reads (log, ledger, the project's `studio.toml`): `unknown_id`,
`closed` (`closed by <id> at <ts>`) and `needs_owner` are refused with draft's reason, detail and order.
Only `template == "config"` passes, i.e. the five literals of `draft._literal`, all `[rebuild]`:
`gate:level_*:X` -> `level_gate_args = ["--x-repeats", "2"]`; `gate:node:X` -> `scoped_gate_args +=
["--x-repeats", "1"]`; `gate:node:D`, `gate:level_regression|level_full:D` -> `+= ["--await-cap", "5"]`;
`human:spec_sync` -> `sync_spec = "auto"`. Everything else (`change_request`, including the `cause:*`
knobs whose value the owner decides; `module`; `prereg`) -> `not_config`. The edited line IS the second
line of the draft's `toml` field, byte for byte (the draft already resolved `+=` against the current
list); apply parses it and asserts key and op equal `_literal`'s and the value equals the draft's; a
mismatch is `draft_mismatch` (exit 2, nothing written). The record keeps the draft's `toml`, `current:`
line, `## Expected` body and the `sha256` of the draft text, equal to that of `lattice improve draft ID`
stdout on the same inputs, so the owner can check that what was applied is what they read.

**2.2 Exit codes and refusals.** **0** applied (gates passed, skipped or none configured) or discarded.
**1** applied, a gate failed or timed out (branch, worktree, record kept). **2** cannot run: no project
dir; unreadable log, ledger or `studio.toml`; not a git repository, project outside it, no commit; no
configured identity; a git command failed; record unwritable; interrupted; `draft_mismatch`; usage
errors (`--discard` with `--branch`/`--no-gates`). **3** refused: nothing written, not even the state
dir. Output as draft: stderr `lattice improve: refused: <detail>`; with `--json` also `{"format": 1,
"command": "apply"|"discard", "id", "refused": true, "reason", "detail"}`. Order, first hit wins: (1)
draft's three; (2) `not_config`; (3) `already_applied` (`<state>/applies/<id>.json` exists); (4)
`knob_pending` (another live record edits the same key and its commit is not reachable from the base
tip); (5) the exit-2 git checks; (6) `detached_head`; (7) `dirty_checkout`: `git --no-optional-locks
status --porcelain=v1 --untracked-files=no` non-empty (untracked files ignored: owners keep private
ones; `--no-optional-locks` so `.git/index` is never refreshed); (8) `studio_toml_untracked` (in the
working tree, not in HEAD), `studio_toml_differs` (bytes != HEAD's blob, e.g. skip-worktree); (9)
`bad_branch` (`git check-ref-format --branch` fails, or a `protected_branches` name), `branch_exists`,
`worktree_exists` (the path exists at all); (10) `already_set`, `unsupported_layout`, `edit_check_failed`.

**2.3 Base, branch, worktree.** Base = the branch checked out in the owner's main worktree, at its tip:
its `studio.toml` is the one the draft read and the one Studio loads, so a knob takes effect only once
merged there. `[rebuild] integration_branch` (Streak: `build/streak`, where built code lands) is NOT the
base; when set and different the record warns `[rebuild] integration_branch is <b>; this change is
based on <base>, the branch checked out (Studio reads studio.toml there)`. The integration-branch
precondition is therefore: HEAD on a branch with a commit, a clean tracked tree, `studio.toml` = HEAD's.
Branch: `--branch NAME`, else `improve/<id>`. Worktree: `<parent of the repo top>/<repo name>.improve/
<id>`, a sibling, never inside the repository's working tree, so no `info/exclude` line is needed
(`.lattice/rebuilds/` is inside the repo; `worktrees = "temp"` is not used: the OS may delete it before
review). `git -c core.hooksPath=/dev/null worktree add -q -b <branch> <path> <base sha>`; hooks are off
for every git call (a `post-checkout` hook could touch the owner's checkout). No checkout, merge, reset,
stash or index write in the owner's checkout.

**2.4 The textual TOML edit**, computed in memory from HEAD's blob before any write, then written to the
worktree and checked byte-equal. Lines keep their endings. The header is the one line matching
`^\s*\[\s*rebuild\s*\]\s*(#.*)?$`; none or several while the parsed file defines `rebuild` otherwise
(dotted keys, inline table, only `[rebuild.x]`) -> `unsupported_layout`.
- *Key present* (`^\s*(key|"key")\s*=` before the next header): the value must be single-line, else
  `unsupported_layout` (a multi-line value can hold comments); `key = value` is replaced by the draft's
  line, keeping indentation, the whitespace before a trailing `# comment`, the comment and the ending.
- *Key absent*: the draft's line plus the header's ending, inserted directly AFTER the header (as the
  I-3 hand edit `i3-clone-studio-toml.diff`; immune to multi-line values later in the table).
- *In another table* (`[check] level_gate_args`): untouched, warning `<key> is also set in [<t>]; the
  rebuild loop reads [rebuild] only`. *No `[rebuild]`*: append `<eol>[rebuild]<eol><line><eol>`. *No
  `studio.toml`* (HEAD and working tree): create it as the draft's `toml` + `\n`. *Arrays*: as the draft
  shows them, never reformatted. *Endings*: the header's (`\r\n` in a CRLF file), for a new table the
  first line's; a missing final newline stays missing (the append rule adds one first).
- *`already_set`*: `=` and the current value equals the literal; `+=` and the current list already holds
  the literal's items as a contiguous run (the draft would show them twice).
- *`edit_check_failed`* unless: `tomllib.loads(new)` succeeds, equals the original's parse with
  `rebuild.<key>` = the draft's value and nothing else, and the line diff is 1 replaced or 1 inserted
  line (3 for a new table). A committed blob != computed bytes (eol attribute, filter): warning `the
  committed bytes differ from the edit`.

**2.5 The commit.** `git add -- <file>`; `git commit -q --no-verify` (as the rebuild loop); author and
committer = the repository's configured `user.name`/`user.email` (none: exit 2 before any write); both
dates = `applied_at`, the one clock reading (`datetime.now(UTC)`, `%Y-%m-%dT%H:%M:%SZ`, the format of
`ledger` and `EventLog.append`). `git diff --name-only <base> <commit>` must be the one file, else (and
on any failure after `worktree add`) exactly what this run created is removed, exit 2. Message (exact;
without an Expected: `Predicted: no prediction recorded (the draft has no Expected section)`):
```
improve: [rebuild] <draft line>

Proposal <id> (<kind>), signal <key>.
Draft as of <as_of>; <the draft's current: line>.
Predicted: <the draft's Expected body>
Applied by `lattice improve apply`; not merged. The owner reviews and merges.

Lattice-Improve: <id>
```

**2.6 Gates.** `RebuildConfig(project, HEAD's [rebuild]).gates` (configured, else the project's
`check.sh`), in order, ALL run (as `Rebuilder._gates`). Env `rebuild.gate_env(os.environ, repo=<owner's
repo top>, worktree, project_rel, sys_path=[app] sys_path)`; each `/bin/sh -c <cmd>`, cwd the worktree
root, own session, `[rebuild] gate_timeout` (900 s); a timeout terminates the tree as `_run_gate` does
(`terminate_tree`, `stop_grace`, owner-token containers removed): `exit_code` 124, `timed_out`. Apply
has its own runner and never calls `Rebuilder.gate_round`, which appends `gate_run` events: runs of the
changed config would land in verify's before window. Nothing is logged. Result `{command, exit_code,
passed, timed_out, seconds (2 dp), output_tail (20000 chars)}`; status `passed` | `failed` (exit 1) |
`skipped` (`--no-gates`) | `none_configured` (exit 0, warning `no gate ran`) | `interrupted`
(SIGINT/SIGTERM: tree terminated, record written with the gates so far, exit 2). The gates are a smoke
check (the edited `studio.toml` loads, the project passes); they do not exercise `level_gate_args` (plan
level gates only) and never decide a merge. Untracked non-ignored files afterwards: `untracked_after`.

**2.7 The record**: `<state dir>/applies/<id>.json` (state dir found and created as `ledger` does;
`--state` exactly), written atomically after the commit (`gates.status: "running"`) and at the end, so
no branch exists without a record. JSON `indent=1`, final newline, key order: `format: 1`, `command:
"apply"`, `proposal {id, kind, signal_key, title}`, `draft {template, as_of, toml, current, expected,
sha256}`, `edit {file (repo-relative), table, key, op, old, new, line, line_no, action (replaced |
inserted | created_table | created_file), diff (git diff --no-color --no-ext-diff -U3 base commit)}`,
`base {branch, sha}`, `branch`, `worktree`, `commit {sha, tree, message}`, `applied_at`, `gates {status,
timeout_s, results, untracked_after}`, `integration_branch`, `next [{step, command}]`, `warnings`.
`next`: `review` `git -C <repo> diff <base sha> <branch>`; `merge` `git -C <repo> merge <branch>`
(yourself, after review); `close` `lattice improve ledger --project P [--state S] --signal <key> --status
closed --id <id> --commit <commit sha>` (when the merge lands, before the runs that measure it); `verify`
`lattice improve verify <id> --project P [--state S] --at <YYYY-MM-DDTHH:MM:SSZ the merge landed>`;
`tidy` `git -C <repo> worktree remove <wt>` + `branch -d <branch>`; `drop` `lattice improve apply
--discard <id> ...`. Text: `Lattice improve apply: <id> (<key>)`, `Edit:` (file, line, action, line no,
current), `Branch:` (base sha7, commit sha7), `Worktree:`, `Gates:` (status; each exit, seconds),
`Record:`, `NOT MERGED: lattice never merges, pushes or closes the ledger. Next, yourself:`, the six
steps, warnings. `--json` prints the record.

**2.8 `--discard ID`.** Refusals (exit 3, nothing removed): `not_applied` (no live record: not a branch
apply made); `branch_moved` (tip != the recorded commit: commits beyond it, amend, reset); `merged`
(`git for-each-ref --contains <commit>` names any other ref: heads, remotes, tags); `worktree_dirty`
(tracked changes, or untracked files not in `untracked_after`). Then `git worktree remove --force <wt>`,
`git branch -D <branch>`, the record renamed (bytes unchanged) to `applies/<id>.discarded-<n>.json`. A
missing worktree dir: the branch still goes, warning `run git worktree prune yourself`. The empty
`<repo>.improve/` is left; the owner's checkout, other refs and the ledger are never touched.

**2.9 Idempotence, determinism, safety.** Re-apply: `already_applied` (3), nothing changed; after the
ledger close: `closed` (3); after `--discard`: applies again with the same tree, diff and message. Same
inputs (log, ledger, HEAD, paths) -> identical draft, edit, tree id, message and record bytes except
`applied_at`, the commit sha wherever it appears, gate `seconds` and `output_tail` (no uuid; the
container token is not recorded). Imports: stdlib + `lattice.improve.*`; `lattice.studio.rebuild`/`state`
lazily inside helpers (`lattice --help` start-up unchanged). Subprocesses: `git` and `/bin/sh -c <gate>`
only; no `lattice.llm`, `anthropic`, `claude`. Writes: the new ref and reflog, `.git/worktrees/<id>/`,
objects, the worktree, `<state>/applies/` (with the default state dir also `info/exclude`, as `ledger`).

## 3. Probes (blind)

Each deterministic probe runs **10 times**: pass = 10 of 10 identical (masked as 2.9) plus its rule.
Scratch roots; clones by `git clone` (sources only read), the branch reset in the clone; `--state`
outside the clone. "I-3 9" = the first 9 lines of `i3-events.jsonl`. Synthetic repo = `git init`,
committed `studio.toml`, identity in its config, `core.autocrlf=false`.

**A0 refusals.** Rule: 18 of 18 rows (a-r, 24 cases) exact; on every exit-2/3 row the scratch root's hashes unchanged.
| Row | Input | Exit, reason |
|---|---|---|
| a | `imp-00000000` | 3 `unknown_id` |
| b | I-3 all 18 + V1a's ledger entry, `imp-3e1cd7f9` | 3 `closed`, `closed by I-01 at 2026-10-12T23:08:00Z` |
| c | I-3 9, `imp-56861ff8` (`gate:level_regression:C`) | 3 `needs_owner`, names `lattice/spec/check.py` |
| d | I-3 9 `imp-7fad29b4`, `imp-a8f0a344`; I-3 18 `imp-3e1cd7f9` | 3 `not_config` (module, prereg, prereg) |
| e | V2d-like `cause:timeout` log, `imp-e5092d05` | 3 `not_config` (change_request) |
| f | a second apply of an applied id | 3 `already_applied` |
| g | `level_regression:X` and `level_full:X` logged; `imp-3e1cd7f9` then `imp-50c7f46a` | 3 `knob_pending`, names imp-3e1cd7f9 |
| h | detached HEAD | 3 `detached_head` |
| i | a tracked file modified; staged only | 3 `dirty_checkout` (both) |
| j | control: untracked `private.txt`, `app.db` | 0 applied; both byte-identical |
| k | `studio.toml` untracked | 3 `studio_toml_untracked` |
| l-n | `--branch 'bad..name'`, `--branch master` (protected); `--branch main`, `improve/imp-3e1cd7f9` pre-created; the worktree path pre-created | 3 `bad_branch` x2, `branch_exists` x2, `worktree_exists` |
| o-r | no repository; no commits; no identity (`GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`); unparseable `studio.toml`; `--discard --no-gates` | 2 (each; nothing created) |

**A1 the I-3 apply** (I-3 9 in `--state`, `imp-3e1cd7f9`; the draft: `as_of 2026-10-12T23:07:11Z`, `toml`
`[rebuild]\nlevel_gate_args = ["--x-repeats", "2"]`, `current: not set`, `expected: X 119.5 s -> saving
71.7 s a gate (60%; x_repeats 5 -> 2)`). **A1a dogfood/studio**: clone of the lattice repo, `next-phases`
at a4ab4a1, `--project <root>/lattice/dogfood/studio --no-gates`. Exit 0; branch `improve/imp-3e1cd7f9`
off `next-phases` a4ab4a1; worktree `<root>/lattice.improve/imp-3e1cd7f9`; `inserted`, line 22, file
`dogfood/studio/studio.toml`; `draft.sha256` `c516bc836f33cdc8...`; blob 642c32d -> 3def11c; tree
`ce7f02cf0d0b1156d597a7ca8012591f070c1031`; no warning. Diff:
```
@@ -19,5 +19,6 @@ model = "sonnet"
 # The rebuild loop (lattice/studio/rebuild.py, API.md v1.7): the repository is the lattice
 # checkout, the gates run from its root in the rebuild's worktree.
 [rebuild]
+level_gate_args = ["--x-repeats", "2"]
 repo = "../.."
 gates = ["dogfood/studio/check.sh", "uv run pytest tests/studio -q"]
```
**A1b Streak**: clone of `~/Documents/streak`, `main` at a0db033, `--no-gates` (its gates need Docker,
MySQL and Go: NOT run). Exit 0; base `main` a0db033; worktree `<root>/streak.improve/imp-3e1cd7f9`;
`inserted`, line 22, `studio.toml`; `draft.sha256` `4c4d6bd878d47f0d...`; blob 12b2948 (the I-3 clone's
base too) -> b8a7c2f; tree `c585c3bd71872cd35f7099b3d3097a8d79370038`; gates `skipped`; warning
`[rebuild] integration_branch is build/streak; ...`. Diff = the I-3 hand diff without its comment:
```
@@ -19,6 +19,7 @@ go_src = "/Users/vivekgade/Documents/streak"
 x_repeats = 5
 
 [rebuild]
+level_gate_args = ["--x-repeats", "2"]
 repo = "."
 gates = ["./check.sh"]                          # writes metrics/last-check.json when Studio runs it
 findings_json = "metrics/last-check.json"       # findings by scenario/rule, so areas can be scoped
```
Rule (both): 1 `+` line, 0 `-` lines; `edit.line` = the draft `toml`'s second line; blob and tree ids as
predicted; message = 2.5 filled in; `next.close` carries the commit sha; `git log base..branch` = 1.

**A2 gates.** **a** dogfood real gates (A1a without `--no-gates`; 3 runs, seconds vary): exit 0,
`passed`; `dogfood/studio/check.sh` exit 0, tail `PASS: 0 findings (D 0, X 0); 435 cases run`, ~53 s
(49.7 s at 408 cases, `2026-10-12-multi-results.md`, scaled to 435 cases,
`2026-10-14-linked-questions-results.md`); `uv run pytest tests/studio -q` exit 0, ~190 s (71.3 s for 176
collected, `2026-10-10-studio-improves-lattice-run2.md`, scaled to 467 collected now; weak);
`untracked_after` [] (`.gitignore` covers the caches). Rule: 3 of 3 passed, each `seconds` in (0, 900),
the record = A1a's plus the gates. **b** `gates = ["true", "false"]`: exit 1, `failed`, both ran (0, 1),
branch, worktree, record kept. **c** `["sleep 30"]`, `gate_timeout = 2`: exit 1, `exit_code` 124,
`timed_out`, seconds in [2, 9]. **d** no gates, no `check.sh`: exit 0, `none_configured`, warning. **e**
`["false"]` with `--no-gates`: exit 0, `skipped`, 0 results. **f** `["sleep 30"]`, SIGTERM after 2 s:
exit 2, `interrupted`, no `sleep` left. Rule b-f: 5 of 5.

**A3 safety** (A1a, A1b, A2b, A0j, A5a). Before/after each call: HEAD and `symbolic-ref HEAD`; sha256 of
`.git/{index,HEAD,config,packed-refs,info/exclude}`; every ref but the new one; `git stash list` (one
entry made before); an extra branch `other`; every file of the owner's working tree (tracked and
untracked); the state dir's `events.jsonl`, `improvements.jsonl`. New paths: only `<repo>.improve/<id>/**`,
`<state>/applies/<id>.json`, and in `.git/` `refs/heads/improve/`, `logs/refs/heads/improve/`,
`worktrees/<id>/`, `objects/`. A `git` shim first on `PATH` logs every argv: 0 with `merge`, `push`,
`update-ref`, `reset`, `checkout`, `stash`. Static: imports of 2.9; 0 hits for `claude`, `anthropic`,
`lattice.llm` in `apply.py`; 0 for `3e1cd7f9`, `71.7`, `ce7f02c` in `lattice/improve/`. Rule: 0 changed
hashes, 0 unexpected paths or argv. A3b, default state dir: the one extra change is `info/exclude`
gaining `/.lattice/state/` exactly as `ledger --signal` would.

**A4 edit edge cases** (synthetic repo, a log whose proposal matches, `--no-gates`). Rule: 13 of 13 (a-m); on
each exit-0 row the new parse = old + the one key and every other byte is identical.
| Row | `studio.toml` | Result |
|---|---|---|
| a | `level_gate_args = ["--x-repeats", "3"]  # tuned` | 0 `replaced` -> `["--x-repeats", "2"]  # tuned` |
| b | absent; `[rebuild]   # build` + a comment line under it | 0 `inserted` after the header, comment below |
| c | only `[check] level_gate_args = [...]` | 0 `inserted`; [check] untouched; warning |
| d, e | no `[rebuild]`; no `studio.toml` | 0 `created_table` (3 lines appended); 0 `created_file` (`[rebuild]\nlevel_gate_args = ["--x-repeats", "2"]\n`) |
| f | `gate:node:X`; `scoped_gate_args = ["--layers", "CD"]  # node` | 0 -> `["--layers", "CD", "--x-repeats", "1"]  # node` |
| g | `human:spec_sync`, absent | 0 `inserted` `sync_spec = "auto"` |
| h | X with `["--x-repeats", "2"]`; node:X with `"--x-repeats", "1"` in the list | 3 `already_set` (both) |
| i | the whole file CRLF | 0; inserted line ends `\r\n`; blob = computed bytes |
| j | no final newline, `[rebuild]` mid-file | 0; last byte still not `\n` |
| k, l | multi-line `level_gate_args = [\n "--x-repeats", "3",\n]`; `rebuild = { gates = ["true"] }` | 3 `unsupported_layout` (both) |
| m | `[rebuild]` only inside a `"""` string | 3 `edit_check_failed` |

**A5 `--discard`.** a: after A1a -> 0; worktree and branch gone, `git worktree list` = main only, record
renamed `imp-3e1cd7f9.discarded-1.json` byte-identical, A3 hashes = before the apply. b: never applied ->
3 `not_applied`. c: a commit added on the branch -> 3 `branch_moved`. d: `git branch keep <commit>` -> 3
`merged`. e: a tracked file edited in the worktree -> 3 `worktree_dirty`. Rule: 5 of 5; b-e remove nothing.

**A6 idempotence, determinism.** a: A0f leaves record and branch byte-identical. b: apply, discard,
apply: same tree, diff, message; records equal (masked). c: A1a, A1b on 10 fresh clones at one path,
from two cwds: records identical (masked), tree ids as A1. Rule: 3 of 3.

**A7 the loop closes** (V2's truth: C = 2026-10-01T12:00:00Z; before X 100, 101, 99 at `x_repeats` 5).
Apply `imp-3e1cd7f9` (record: `expected: X 100.0 s -> saving 60.0 s a gate (60%; x_repeats 5 -> 2)`);
the probe, as the owner, `git merge --ff-only`s the branch, runs `next.close` verbatim, appends the
after-events, runs `verify imp-3e1cd7f9 --at 2026-10-01T12:00:00Z`. a (after 40, 41, 39): improved, exit
0, 100.00 -> 40.00 s, delta -60.00, rel -0.600, predicted -60.0, error 0.000, `entry.commit` = the apply
sha. b (100, 99, 101): not_improved, exit 1, error 1.000. c: apply again -> 3 `closed by imp-3e1cd7f9`.
d: `--discard` -> 3 `merged`. Rule: 4 of 4; a and b = improve2's V2a/V2b numbers.

**A8 no regression.** `tests/improve` 111 existing pass (plus `test_apply.py`); `tests/test_cli.py` 21
(incl. `test_help_startup_time`); `improve2_probes.py` 15 of 15; IM1-IM21 21 of 21; `dogfood/studio/
check.sh` PASS 435 cases; `report|import-plan|ledger|verify|draft` (text, `--json`; improve2's D5 inputs
plus V1a, D1b) and their `--help` byte-identical to a4ab4a1 (`git archive`); `lattice improve --help`
differs only by the `apply` lines.

## 4. Mutation plan

IA1 the base branch fast-forwarded to the apply commit -> A3 (base ref), A5d. IA2 the edit written to
the owner's `studio.toml` -> A3 hashes, A1 diff. IA3 apply appends a `closed` ledger line -> A3
(`improvements.jsonl`), A7. IA4 gates run through `gate_round` (a `gate_run` event each) -> A3
(`events.jsonl`), A2b. IA5 `not_config` dropped (a module draft applied) -> A0d, A0e. IA6 a failed gate
still exits 0 -> A2b, A2c. IA7 the line re-derived, `json.dumps(new, separators=(",", ":"))` -> A1 (diff,
blob), A6c. IA8 a replaced line loses its trailing comment -> A4a, A4f. IA9 `--discard` without
`branch_moved` -> A5c. IA10 `--discard` without `merged` -> A5d, A7d. IA11 `subprocess.run(["claude",
"-p", ...])` writes the message -> A3 static. IA12 `already_set` dropped -> A4h. IA13 `already_applied`
dropped (a second branch) -> A0f, A6a. IA14 the inserted line always ends `\n` -> A4i. IA15 the worktree
under `<repo>/.lattice/rebuilds/` -> A3 (working tree, `info/exclude`). IA16 `dirty_checkout` dropped -> A0i.
Rule: 16 of 16 caught by a predicted catcher, IM1-IM21 still caught; a survivor is reported as such.

## 5. Named risks

1. **Apply only records.** A knob can slow or break the owner's plan runs; apply's gates are a smoke
   check that never exercises `level_gate_args`; the effect is measured only by `verify` after real runs.
2. **Heavy, environment-bound gates.** Streak's need Docker, MySQL, Go (A1b is ungated); dogfood's ~4 min.
3. **"The owner merges it"** is the safety and the bottleneck; branches and sibling worktrees pile up.
4. **One knob family**: five literal templates; every other proposal stays text.
5. **Confounders in verify**: the landing time is the owner's word (`--at`); whatever else landed with
   it is credited to it; the ledger's `--commit` is echoed, never checked against history.
6. **Builder bias**: the builder reads these predictions; the predicted bytes were hashed from the stated
   rules; the runner is audited against this document; A3's literal grep.
7. **Base-branch reading**: the checked-out branch, not `integration_branch`; a warning only.
8. **A narrow editor**: refusals for exotic layouts; correctness rests on the parse check; hooks skipped.

## 6. Blind vs post hoc; verification

Every prediction above is blind: a failed one is a finding, never silently fixed; a fix it motivates is
a later, labelled post-hoc commit and the blind numbers stay as first measured.
```
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-apply
uv run pytest tests/improve tests/test_cli.py -q
uv run python dogfood/improve_apply_probes.py   # A0-A8 -> docs/results/data/2026-10-15-improve-apply/
uv run python dogfood/improve2_probes.py        # 15 of 15, unchanged
uv run python dogfood/improve_mutants.py        # control, IM1-IM21, IA1-IA16
dogfood/studio/check.sh                         # PASS, 435 cases
```
Pass: every rule of section 3; all mutants caught; `git diff a4ab4a1 -- lattice/` only in
`lattice/improve/{apply.py (new), __main__.py}` and `lattice/cli.py`; nothing merged, pushed, or written
to a ledger or log by the command in any probe.
