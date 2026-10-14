# Multi-project slice 4b (linked pending questions in consumers): pre-registration

## 1. Status

Date: 2026-10-14. Branch `np-linkedq` (worktree `lattice-linkedq`, from `next-phases` 33d8de1). **Written
BEFORE any code of this change exists**; nothing below was run or measured. Predictions come from committed
data only: `docs/results/data/2026-10-13-contracts-impact/{I0c,I1,I2,I3,I4}.json` (4a's recorded labels,
`extra.per_drift`, `observed`), `data/2026-10-13-contracts{,-streak}/Dn.json` (`links[0].findings`) and the
code at 33d8de1. Results: `docs/results/2026-10-14-linked-questions-results.md`. No LLM, no API key.

**Owner's defaults (not reopened):** a linked question lives in the CONSUMER's Studio state; only the
consumer's owner clears it (resolved, dismissed); it never blocks or alters the provider's accept, build or
anything else. **Out of scope:** 4c (LLM drafting), transitive impact, change-set UI, any web UI change.

## 2. Decisions (fixed before coding)

**D1 Trigger: both, one engine.** (a) **Accept in the provider's Studio**: once `Studio.accept` has
succeeded (files written; committed, or "not a git repository"), it enqueues one notification on a
per-Studio worker thread (`studio-linkedq`, accept order, one at a time) and returns its unchanged
response. A refused or failed accept notifies nothing; builds, plans, landing and `sync-spec` never notify
(the static contract is spec-level). (b) **CLI** `lattice contracts notify PROVIDER --candidate REF|PATH
[--workspace PATH] [--dry-run] [--json]`: spec changes made outside Studio, and re-delivery after a
warning. No MCP tool or API route notifies: only the provider's owner (accepting, or running the CLI)
causes a cross-project write. **Computed from** slice 4a's `run_impact(ws, provider, candidate)`, unchanged
(each opted-in consumer's pin against the candidate). Studio's candidate is the accept's commit SHA
(immutable, so a later accept cannot race it), or the project directory (kind `path`) without git. The
manifest is re-read per notification. **Who:** the Studio's root is matched to a manifest project **by
resolved path** (`Workspace.by_path`), never by Studio name; the workspace is the server's (`--workspace`,
`$LATTICE_WORKSPACE`, walk up), handed to each Studio as `workspace_path` (None for a bare `Studio` in
tests: no trigger). Skipped, nothing written, outcome recorded (D7): `no_workspace`, `not_in_workspace`,
`manifest_error`, `no_consumers` (so a link without `contract`, a consumer outside the workspace and a
transitive consumer get nothing).
**Only `contradiction` creates a question**; `regression`, `unaffected`, GAPs and error rows (`no_pin`,
`bad_pin`, `no_imports`, `bad_imports`) never. Basis (4a data): all 12 contradictions of the two D-series
are real breaks (job D1-D5, Streak D1-D7); of 5 regressions 3 are noise (job D9, Streak D11, D13), 2 real
but unclassifiable (job D8, Streak D10). Contradiction-only: 12 questions, 0 false, 2 breaks unannounced;
with regression: 14, 3 false. Fatigue (risk 4) decides, as 4a's exit code (regression exits 0); the notify
result lists every row's label, so the provider still sees regressions. **Key, idempotence:** `key =
"sha256:" + sha256(canonical JSON [provider, consumer, contract, pin spec_hash, sorted [endpoint, kind,
item] of each BROKEN])`. No candidate ref or hash: a later change leaving the same reliances broken is the
same question (one, not one per accept on a stale pin); a re-pin or another BROKEN set is a new one. **Creation
rule (orchestrator amendment, 2026-10-14):** a notification creates a question unless an `open` question with
the key exists, or the link's `current` already points at a question with the key (the notified state is
unchanged). A resolved or dismissed question is never reopened, and a break that RETURNS after the link
was in another state (clean, or another BROKEN set) is a NEW question: the owner cleared that state
once, the returning break is new information. CHANGED items, details, gaps: not keyed.

**D2 Supersede: derived, never auto-cleared.** Per (provider, contract) the consumer holds
`linked_questions/links/<slug(provider)>.<contract>.json` = `{format: 1, provider, consumer, contract,
current: "lqN"|null, since, provider_after, source}`. After an evaluated row, `current` = the question of
its key (existing or new) for `contradiction`, else null. Written only when `current` changes; never
created with `current` null (a clean provider and a consumer that never had a question: nothing written);
error rows change nothing. **A question is `superseded` iff it is `open` and its link's `current` is not
its id**, computed at read time as `{by: current|null, reason: newer_question|no_contradiction, since}`.
No notification changes a status: an unseen question stays `open`. A key that comes back while its
question is `open` makes that question current again; if that question was resolved or dismissed, a NEW
question is created (see the creation rule in D1). So **the provider never rewrites a question file** (it creates `lqN.json` and
replaces the link file) and the consumer only rewrites question files (resolutions).

**D3 Cross-project write mechanism** (module `lattice/contracts/linked.py`; the first write by one
project's process into another's state: risk 1).
1. **Locating.** Consumer path from the manifest; its `studio.toml` read with `tomllib` only (no
   `StudioConfig`, no harness import). `[state] enabled = false`: skip `consumer_state_disabled`. `[state]
   dir`: that directory. Else `state_dir_path(path, n, ...)` for `n` = `project_name(consumer)` (single
   Studio; hub without workspace), then the manifest name (hub with workspace), deduplicated; the first
   that **exists** wins; two existing: the first, warning `consumer_state_ambiguous`. `state_dir_path`
   (new in `state.py`) is `state_dir_for` minus its side effect. **Never** `state_dir_for` (it appends to
   the consumer's `.git/info/exclude`) nor `StateStore` (it makes `plans/ builds/ changes/`, and
   `allocate` rewrites `meta.json`, which a running consumer Studio holds in memory: a lost update).
2. **A missing state dir is never created by the provider**: `consumer_state_missing`. `lattice contracts
   questions init CONSUMER` (CLI, the consumer's owner, idempotent) creates it through the real
   `state_dir_for` (exclusion included) plus `linked_questions/`; an existing state dir gets
   `linked_questions/` from the first delivery.
3. **Allowed paths in a foreign state dir, exhaustively:** `linked_questions/{.lock, lq<N>.json, links/,
   links/*.json}` and their transient `.<name>.tmp-*`. Never `meta.json`, `plans/`, `builds/`, `changes/`,
   `results/`, `events.jsonl`, the consumer's spec, lock, imports, code or git metadata. `linked_questions`
   or `links` as a symlink or non-directory: skip, `consumer_state_unsafe`.
4. **Atomic, locked.** Files go through `state.atomic_write` (temp, fsync, `os.replace`, dir fsync): a
   reader sees the old file or the new. Every write (delivery, resolve, dismiss, init) holds an exclusive
   `fcntl.flock` on `linked_questions/.lock` (one open per call, so two Studios of one hub process also
   exclude each other) and sweeps stale `.*.tmp-*`, scans keys and ids, allocates, writes. Wait <= 5 s,
   then `consumer_state_busy`. Readers (list, GETs) take no lock and write nothing.
5. **Ids** `lq<N>`: 1 + the highest N among `lq<N>.json*` names (unreadable and `.bad-*` included),
   under the lock; never deleted, never reused; no `meta.json` counter. `KINDS` stays `{plans, builds,
   changes}`: a separate kind with its own allocation, because its writer can be another process.
6. **No quarantine.** An unparsable `lq*.json` is skipped, kept, its id taken, reported as
   `unreadable_question` (renaming it would be a write by a reader or a foreign process); its key is
   unknown, so a duplicate may follow (disclosed). `format` > 1: read, unknown fields kept, warning.
7. **Failure never reaches the accept.** `consumer_state_missing|disabled|unwritable` (any `OSError`)
   `|busy|unsafe` are per-consumer warnings; other consumers still run; a worker exception is caught into
   the outcome's `error`. The accept's response, timing and files stay those of 33d8de1 (Q4).

**D4 The object** `linked_questions/lq<N>.json` (`json.dumps(indent=1)`, every key always present):
`format: 1`, `id`, `key`, `kind: "linked_question"`, `workspace`, `provider`, `consumer`, `contract`,
`status: open|resolved|dismissed`, `label: "contradiction"`, `pinned` (the consumer's pin `{spec_ref,
spec_hash, git_ref, dirty}`: "before"), `provider_after` (the candidate version: "after"), `candidate
{input, kind, resolved}`, `imports_affected` (sorted distinct endpoints of the BROKEN findings),
`findings` (layer 3's shape and order: every BROKEN plus every CHANGED with `relied: true`; unrelied
CHANGED and gaps dropped), `counts` (the row's full `{broken, changed_relied, changed_unrelied, gaps}`),
`text`, `suggestion`, `source {via: "accept"|"cli", change: "c4"|null, commit: SHA|null}`, `created_at`,
`resolution` (null, or `{status, note, at, via: "cli"|"studio"}`). Reads add the derived
`superseded` (D2). `text` = `PROVIDER's accepted contract (spec_hash <12>, commit <8>) contradicts
CONSUMER's pin (spec_hash <12>): N import(s) BROKEN on M endpoint(s)` + one line per finding as `check`
prints it. `suggestion` = `advisory, nothing was changed in CONSUMER: adapt CONSUMER to the findings, then
"lattice contracts pin CONSUMER" and "lattice contracts check CONSUMER"; or ask PROVIDER's owner to
revert. Clear it with "lattice contracts questions resolve|dismiss CONSUMER lqN"`.

**D5 Clearing (the consumer's owner only)**, operationally: a call on the consumer's own surface (the CLI naming it,
or its Studio's `/p/<consumer>/api/...`; MCP is read-only: an agent can list, never clear); nothing on the provider's side changes a status.
`resolve` = handled (adapted, re-pinned, agreed), `dismiss` = not relevant: `open` -> that status,
`resolution {status, note ("" default, a string <= 2000 chars), at, via}`; terminal (no reopen in 4b);
superseded questions clearable; a cleared question is never reopened (a break that returns after another
state is a new question, D1). No re-pin required, no check run (no
subprocess); the result suggests "advisory: nothing was re-pinned; `lattice contracts check CONSUMER`
shows the link now". Errors: `unknown_project`; `unknown_question` (404); `not_open` (409, stored
resolution unchanged); `bad_note` (422); `no_state` (404: missing or disabled state dir).

**D6 Surfaces.**
- **CLI** `lattice contracts questions list CONSUMER [--status open|resolved|dismissed|all] [--json]`,
  `resolve|dismiss CONSUMER ID [--note T] [--json]`, `init CONSUMER`. List: `{format: 1, consumer, state:
  present|missing|disabled, state_dir, questions (id order), counts {open, resolved, dismissed,
  superseded}, problems}`; resolve/dismiss: the updated question. Exits: list 0 (2: manifest error,
  unknown consumer); resolve/dismiss 0, 1 refused (`not_open`, `bad_note`), 2 cannot. Notify: 0, 1 a
  consumer undelivered or in error, 2 the impact could not run (4a's whole-call problems). Notify JSON:
  `format: 1, workspace, provider, candidate, source, advisory: true, dry_run, consumers [{consumer,
  contract, label, error, action: created|existing|none|undelivered|skipped, question, current,
  state_dir, warning}], counts, problems, exit_code, elapsed_s`; `--dry-run` writes nothing.
- **MCP** (in `WORKSPACE_TOOLS`, listed only with a workspace): `linked_question_list {consumer,
  status?}` only (read-only, listed under `--readonly`). **No MCP resolve or dismiss tool exists**
  (orchestrator amendment, from the owner's default 2: cleared by the consumer's owner, and an agent
  clearing a notice is not the owner). Errors are `ToolError`s naming the code. The pinned tool-list
  test is updated (disclosed).
- **Studio API v1.19** (project-scoped, also `/p/<name>/...`; reads its own `store.root/linked_questions`
  per request, no cache): `GET /api/linked-questions[?status=]` (the list body, `consumer` = the Studio's
  name; bad status 422; no store: `state: disabled`); `GET /api/linked-questions/<id>` (404); `POST
  /api/linked-questions/<id>/resolve|dismiss` `{note?}` (200 the question, 404, 409, 422; `via:
  "studio"`); `GET /api/linked-notifications` (provider side: this Studio's accept outcomes since start,
  newest first, <= 20: `{notifications: [{change, commit, status: running|done|skipped, reason,
  started_at, result}]}`). No existing response changes (`/api/model`, `/api/projects`, the accept).
  API.md -> v1.19, one section. **Mock** (`owner-dev`): `scheduler-web` holds `lq1` open (v1.17's link
  findings) and `lq2` dismissed, clears answered in memory; `scheduler` one `done` notification;
  keys-and-types test as for v1.18.
- **Dogfood** (2 rules, one scenario each): **R96** `GET /api/linked-questions` empty (`state: present`,
  counts 0), `lq9` GET/resolve 404, `status=bogus` 422; **R97** `GET /api/linked-notifications` empty
  before any accept, `POST` 404. Seeded and accept-triggered flows are `Unchecked`, as R94/R95.

**D7 Provider-side record**: in the provider Studio's memory and log only (no state file, no
`events.jsonl` event: the improve ranker reads it; no change-record field). Lost on restart; `notify`
re-delivers idempotently.

## 3. Probes

**Global.** Each deterministic probe runs **10 times**; canonical JSON (`elapsed_s`, `created_at`,
`at`, `since`, `started_at` dropped; scratch root -> `$WORK`) and text identical 10 of 10. Workspaces as
4a's runner: `scheduler` (clone of job-scheduler ef17cf8, `spec.tickets:t5`) + `ci-bot`; `streak` (clone
of Streak a0db033) + `streak-widget`; candidate refs `dN` committed with fixed author and dates. Where a
Studio must serve a consumer (Q5, Q6) its scratch copy gets `project_seed`'s `spec.py`, `spec.trace.json`,
`REQUIREMENTS.md` (imports and lock untouched). State dirs come from `questions init` before the window
unless stated; each row runs on a **fresh** consumer state. `K(x)` = row x's key. Pin spec_hash: job
`9bcae0e24da9`, Streak `d44b738522ea` (every committed D row). Runner `dogfood/contracts/linkedq_probes.py`.

**Q0 no-op** (sources: I1/I2 `per_drift`, I3 `observed`). Notify job D6, D7, D10, Streak D8, D9, D12 and
the 11 unaffected controls (J-C1..C4, S-C1..C6, I3x-a): every row `action: none`, **0 files added or
changed** in the consumer's directory after `init` (sha256, mtime; `linked_questions/` stays empty), exit
0; `--dry-run` on job D2: `action: created`, `dry_run: true`, 0 files written. Pass and prediction: 18/18.

**Q1 questions per contradiction.** Pass: every row as predicted, the question's `findings` equal the
committed `Dn.json` `links[0].findings` minus unrelied CHANGED, `pinned.spec_hash` the pin, `id` `lq1`.

| set | rows (source) | predicted |
|---|---|---|
| job D1-D5 | I1 `per_drift`: contradiction | 1 question each; D1: 1 BROKEN, 0 relied; D2-D5: 1 BROKEN + 1 CHANGED*; `imports_affected` 1 endpoint |
| job D8, D9 | I1: regression | 0 questions (contradiction-only rule), `action: none` |
| Streak D1-D7 | I2: contradiction | 1 each; D1 1 BROKEN; D2, D4, D5 1+1; D3 1+1 (2 unrelied dropped); D6 1+1 (4 dropped); D7 7 BROKEN, 0 relied (41 dropped), 7 endpoints |
| Streak D10, D11, D13 | I2: regression | 0 questions |
| I3x-b | I3 `observed`: contradiction/1f | 1 question, 1 BROKEN `endpoint_missing` PUT /test/clock (`EXCLUDED_DETAIL`) |
| I0c stale pin | I0c: contradiction, = D2 | 1 question; `K(I0c) == K(D2)`; run after D2 on the same state: `action: existing`, no write |
| I4 a, c, d, f, g | I4 `observed` | a (D1): ci-bot 1, ci-bot-2 0; c (D4): 1 each, equal findings; d (D8): 0, 0; f: ci-bot-2 not listed, its dir untouched; g: ci-bot-2 `skipped` (`no_pin`), ci-bot 1, exit 1 |
| h `multi` | I4 h | scheduler D2: 1 question (link `scheduler.static`); then streak D13: 0, and `links/streak.static.json` not created |

Keys: `K(job D1..D5)` 5 distinct; `K(Streak D1..D7)` 7 distinct. Totals predicted: job 5 questions of
10 rows, Streak 7 of 13. **Studio trigger** (Streak clone as a real provider Studio, hub with the
workspace; a `ready` change seeded in `changes/c1.json` whose `spec.py` is the Dn edit; `POST
.../accept`; poll `GET /p/streak/api/linked-notifications` until `done`): D2 -> 1 question equal to the
CLI's for D2 except `source {via: accept, change: c1, commit}`; D8 -> 0; D10 -> 0. Pass: 3 of 3.

**Q2 idempotence and supersede** (job, one ci-bot state, in order).

| step | action | predicted |
|---|---|---|
| s1 | notify d2 | created `lq1`; `current` lq1 |
| s2 | notify d2 again | `existing`; 0 files changed (sha256 and mtime) |
| s3 | notify the full D2 SHA; then `d2c4` (D2's and J-C4's edit lists together: other spec_hash, same BROKEN set, derived) | `existing` both; 0 files changed |
| s4 | notify d3 | created `lq2`; `lq1` open, superseded `{by: lq2, newer_question}`; `lq1.json` byte-identical to s1 |
| s5 | notify HEAD (clean) | nothing created; both open; lq1, lq2 superseded `{by: null, no_contradiction}` |
| s6 | notify d2 | `existing` lq1; lq1 not superseded; lq2 superseded by lq1 |
| s7 | dismiss lq1 (CLI), notify d2 | lq1 `dismissed`; notify `existing`, 0 question files changed; 2 questions |
| s8 | notify HEAD (clean) | nothing created; `current` null (the link file rewritten) |
| s9 | notify d2 | created `lq3` (the same key as dismissed `lq1`: the break returned after a clean state); `lq1` stays `dismissed`; 3 questions |
| s8 | notify d8 (regression) | 0 created; `current` null; lq2 still `open` |

Pass: 8 of 8, no status changed by a notification. Prediction: 8 of 8.

**Q3 cross-project write safety.** Pass: all of (a)-(h). (a) Over Q0-Q2, Q5: every consumer file outside
`linked_questions/` byte- and mtime-identical, new files only at D3.3's paths (`.git/info/exclude`
included in the check). (b) A real `StateStore` dir with a plan, a build and change `c1`: after D2
`meta.json`, `plans/`, `builds/`, `changes/` byte-identical; the consumer Studio next allocates `c2`. (c)
`chmod 0555` state dir: `undelivered` `consumer_state_unwritable`, exit 1, other consumers delivered (I4-c
layout). (d) Missing state dir: `consumer_state_missing`, nothing created (`find` empty). (e) `[state] dir
= "st"` in the consumer's `studio.toml`: questions land in `st/linked_questions`; `[state] enabled =
false`: `_disabled`. (f) A reader process loops `json.loads` over `lq*.json` and `links/*.json` while 200
notifications and 200 resolves run (fresh states): 0 parse errors, 0 partial objects. (g) Two notify
processes (two providers into one consumer, 50 rounds): ids unique and contiguous, no file overwritten.
(h) `.lock` held by another process: `consumer_state_busy` after <= 6 s, accept unaffected. Prediction:
pass.

**Q4 the provider's accept is unchanged.** The Q1 Studio-trigger accept (Streak D2), scripted with fixed
`GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`, on this branch and on the main checkout at 33d8de1 (HEAD and
`lattice/` checked first), each with (i) the consumer delivered, (ii) its state dir `0555`, (iii) missing,
(iv) its `.lock` held 30 s. Pass, in all 4: the accept body byte-identical to 33d8de1's (same SHA); spec
files, commit tree and message identical; provider state dir: same file list, `changes/c1.json` equal but
`saved_at`, `events.jsonl` equal but timestamps; median accept latency (10 runs) within 0.3 s of 33d8de1's,
(iv) included, and a second accept during (iv) succeeds. Static: `linked_questions` appears only in
`contracts/linked.py`, `contracts/__main__.py`, `mcp/projects.py`, the new routes, docs and tests (no gate,
build, plan, pin or check reads it). Prediction: pass.

**Q5 clearing parity.** Hub Studio with the workspace (names agree), state from Q2 s4. CLI `--json`, MCP
(stdio, list only) and API: list `all`, `open` (3 bodies equal); CLI and API: resolve `lq1` (note "adapted"), then on a copy dismiss `lq1`; equal
bodies but `via` and `at` (4 checks). Errors in all three: `lq9` -> `unknown_question`/404; resolve
resolved `lq1` -> `not_open`/409 and the stored note unchanged; note of 2001 characters -> `bad_note`/422;
consumer `nobody` -> `unknown_project` (API: `/p/nobody/...` 404); resolving ci-bot's `lq1` through
`/p/scheduler/api/linked-questions/lq1/resolve` -> 404 (the provider's own, empty store). MCP
`tools/list` (also under `--readonly`) holds `linked_question_list` and NO resolve/dismiss tool (2). Mock: same keys and types.
Pass: 11 of 11 + mock. Prediction: pass; in single-project mode only `consumer` may differ (disclosed).

**Q6 durability** (`scripts/restart_drill.py` pattern, a real server process). A hub Studio serving
`ci-bot`; notify d1, d2, d3 (lq1-lq3, lq3 current); dismiss lq1 via API; `SIGKILL`; restart: `GET
/api/linked-questions` byte-identical, order lq1, lq2, lq3; notify d2: `existing`. A writer `SIGKILL`ed at
50 random points: every `lq*.json` parses, no temp file listed, the next write sweeps them. Pass: 3 of 3.

**Q7 no regression.** `probes.py --runs 10` 35/35 (needs `lattice-contracts-base` at c0cdefc),
`streak_probes.py --runs 10 --allow-code-diff` 32/32, `exclude_probes.py --runs 10` 12/12,
`impact_probes.py --runs 10` I0-I8 pass; `pytest tests/contracts tests/workspace tests/mcp tests/studio` 0
failures (`test_state_store.py`, `test_restart_drill.py` unchanged); `dogfood/studio/check.sh` PASS (422 +
R96/R97's); `restart_drill.py` A, B, C, F; on the Q1 workspace `workspace show|validate|impact`,
`contracts export|check|impact` (text, `--json`) and `pin` lock bytes byte-identical to 33d8de1, and
`state_dir_for` paths equal to 33d8de1's for the drill and test projects. Pass: all. **T (reported):**
median CLI notify, 10 runs, threshold <= 10 s, predicted <= 1.5 s (4a's impact 0.67 s plus writes).

## 4. Mutation plan (`dogfood/contracts_mutants.py`, LQ1..LQ18)

One-line changes of the new code; each must be killed by `tests/contracts` (new `test_linked.py`),
`tests/studio`, `tests/mcp`; the probe named also kills it. Prediction: 18 of 18 (LQ14, LQ15 are
probabilistic; a survivor there is a finding, not a pass by default).

| id | mutant | killed by |
|---|---|---|
| LQ1 | a question for every label | Q0 (D6...), Q1 job D8, D9 |
| LQ2 | `regression` also creates a question | Q1 job D8, D9, Streak D10, D11, D13 |
| LQ3 | key check skipped (no idempotence) | Q2 s2 (`lq2` created) |
| LQ4 | key includes the candidate's spec_hash | Q2 s3 `d2c4` (new question) |
| LQ5 | the `current` guard dropped: a dismissed key is recreated while the state is unchanged | Q2 s7 |
| LQ18 | a key present in any status is never recreated (the draft's rule) | Q2 s9 (no `lq3`) |
| LQ6 | the question saved via `StateStore.save` into `changes/` | Q3 (a), (b) |
| LQ7 | ids from `StateStore.allocate` (writes `meta.json`) | Q3 (b) byte-identity, `c2` collision |
| LQ8 | the missing state dir created (`mkdir(parents=True)`) | Q3 (d) |
| LQ9 | state dir computed with the provider's name | Q1 (consumer list empty), Q3 (a) |
| LQ10 | `[state] dir` ignored | Q3 (e) |
| LQ11 | delivery exception propagated by `accept` (run inline) | Q4 (ii): 500 instead of 200 |
| LQ12 | notification run synchronously in `accept` | Q4 (iv) latency >= 5 s |
| LQ13 | a clean notification sets `status: resolved` (auto-clear) | Q2 s5 |
| LQ14 | `atomic_write` replaced by `write_text` | Q3 (f) torn reads |
| LQ15 | no `flock` around allocation | Q3 (g) duplicate ids |
| LQ16 | resolve looks the id up in every workspace project | Q5 `/p/scheduler/...` resolves ci-bot's lq1 |
| LQ17 | an MCP `linked_question_resolve` tool is registered | Q5 `tools/list` (no resolve/dismiss) |

## 5. Named risks

1. **The first cross-project write.** "Each project accepts on its own" held because no process wrote
   another project's files; now a provider's accept writes into a consumer's state. Bounded: D3.3's paths
   only; never a status, spec, lock, plan, build, change, `meta.json`, `.git` or a state dir that does not
   exist; opted-in consumers only; nothing in the consumer reads it for a gate, build, pin or accept. The
   claim becomes "each project accepts and rebuilds on its own; a provider may leave advisory notes in a
   consumer's inbox". Q3/Q4 test the bound.
2. **Two Studios on one state dir** (one project served twice, or single vs hub naming): StateStore has no
   cross-process lock (pre-existing); `consumer_state_ambiguous` can deliver to the dir the running Studio
   does not read. `[state] dir` removes it.
3. **Stale pins.** A lagging pin gets a question at the first notification even if the accepted change
   did not cause the break (I0c); the hash-free key keeps it one question; the text says "your pin".
4. **Notification fatigue.** Contradiction-only, the key and supersede limit it, but never-auto-clear
   lets ignored questions pile up (superseded ones flagged).
5. **Silent misses.** Regression-only breaks (job D8, Streak D10), spec-invisible ones (job D7, D10; Streak
   D9, D12), consumers outside the workspace, without `contract` or state dir, transitive: no question.
6. **Lost outcomes.** A Studio killed between accept and delivery delivers nothing, records nothing (D7);
   re-running `notify` repairs it.
7. **Agents clearing.** None: MCP is list-only (the owner's default 2). A session can see the notices and
   tell the owner; only the CLI or the consumer's Studio clears them.
8. **Builder bias, circularity.** Q1 is 4a's labels mapped by the rule: it shows the rule and plumbing,
   not new detection. Builders will read these predictions.

## 6. Blind vs post hoc, and verification

Fixed now. A failed prediction is a **finding** (prediction error or defect), never fixed silently; blind
numbers stay as first measured, fixes are separate post-hoc commits; runner choices, timings, edge cases met
while coding and test edits (the MCP tool list) go in the results' "Post hoc".

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-linkedq PYTHONDONTWRITEBYTECODE=1
cd /Users/vivekgade/Documents/lattice-linkedq
git -C /Users/vivekgade/Documents/lattice rev-parse HEAD                 # 33d8de1 (Q4, Q7 old code)
git -C /Users/vivekgade/Documents/lattice-contracts-base rev-parse HEAD  # c0cdefc (probes.py W5)
uv run python dogfood/contracts/linkedq_probes.py --runs 10 --scratch "$SCRATCH/q" --out "$SCRATCH/q-data"
uv run python dogfood/contracts/impact_probes.py --runs 10 --scratch "$SCRATCH/i" --out "$SCRATCH/i-data"
uv run python dogfood/contracts/probes.py --runs 10 --scratch "$SCRATCH/c" --out "$SCRATCH/c-data"
uv run python dogfood/contracts/streak_probes.py --runs 10 --allow-code-diff --scratch "$SCRATCH/s" --out "$SCRATCH/s-data"
uv run python dogfood/contracts/exclude_probes.py --runs 10 --scratch "$SCRATCH/x" --out "$SCRATCH/x-data"
uv run python dogfood/contracts_mutants.py                               # control passes; LQ1-LQ18 killed
uv run pytest tests/contracts tests/workspace tests/mcp tests/studio -q  # 0 failures
uv run python scripts/restart_drill.py A B C F                           # pass
dogfood/studio/check.sh                                                  # PASS, 422 + R96/R97's steps
uv run ruff check <changed .py>; uv run ruff format --check <created .py>
```

**Pass:** Q0 18/18, Q1 all rows + 3/3 trigger, Q2 8/8, Q3 (a)-(h), Q4 4/4 + static check, Q5 11/11 + mock,
Q6 3/3, Q7 all, T within threshold, 18/18 mutants, every deterministic probe identical 10/10. Real
job-scheduler and Streak are never written (scratch clones only; `git status` before and after: ` M
uv.lock`, ` M studio.toml`). The recorded run goes to `docs/results/data/2026-10-14-linked-questions/`;
`$SCRATCH` is the session scratchpad.
