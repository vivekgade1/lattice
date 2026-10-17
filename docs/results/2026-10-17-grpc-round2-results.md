# gRPC round 2, development stage: results

2026-10-17. Pre-registration: `2026-10-17-grpc-round2-prereg.md` (with Amendment 1; Amendment 2 appended by this node lists the
hashes below). Worktree `/home/user/wt/np-grpc`, branch `spike-i-grpc` (HEAD `5aa5c72`, round 1 at `cea85fc`). Nothing was
committed or staged. No model was called, no API key was used, no sub-agent was spawned. Data: `docs/results/data/2026-10-17-grpc-round2/`.

**Line 1 (what ran and what did not).** Arm 0: 6 probes, **6 passed, 0 failed**. Arm G (development rows): 10 probes, **10 passed,
0 failed**. Mutants: **17/17 killed** (control green first). **NOT RUN:** Arm M (Streak over MySQL: no Docker daemon, no Streak
checkout), Arm L (the build round), **PG-HO2 / HO2-\*** (the gated held-out stage: the new app does not exist in this node's view; `heldout2/`
was not opened; `--arm H2` of `dogfood/grpc_probes2.py` refuses to run), and the `heldout2` root of P2-NW. **This is a development-stage
result.** `deskd` is development data (seen by round 1's author and scorer and by this round's author), and F2/F3 were chosen after
reading its failures: every `deskd` row below is a tuned-on row, never a blind or held-out claim. The only gated claim of round 2 is the
orchestrator's later HO2 stage (n = 1 new app).

Environment (m): `go1.24.7 linux/amd64`, `uv 0.11.32`, Python 3.13.16 (the round-1 3.13 deviation), no Docker daemon, 4 CPUs shared with
other sessions. Labels: **(m)** measured here, **(p)** the pre-registration's prediction.

## 1. What was built

| file | what |
|---|---|
| `lattice/spec/grpc.py` (changed) | `reconcile_with` (D1: RC1 a login immediately before every non-public call, RC2 forward-sorted and reverse passes on one process, RC3 an isolated rerun of what disagreed); `reconcile(root, model)` is a thin wrapper that reads `studio.toml` and calls it; private `_call_fresh`, `_with_protos`; `run_rpc_flows` (F2 binding at run time, binding record, `unbound`, D3 through `rpc_step_ok`, lifecycle chains through lookup steps) |
| `lattice/importer/workflows.py` (changed, rpc section only) | `lattice.rpc-flows/2`; `RpcView.out_paths/out_lists`; `rpc_enums`; F1 `rpc_edges` (unchanged rule); F2 `natural_fields`, `_lookup_for`, `expand_natural`, `natural_value`; F3 `example_value`/`rpc_message` (bool false, enum first non-zero); F4 `owner_chain`; F6 `id_source`; D3 `rpc_step_ok`, `GUARD_REFUSALS`; `rpc_flows_json` with `unbound` |
| `dogfood/grpc_flow_scorer.py`, `dogfood/grpc_flow_scorer_cases.json` | S1-S4, D4 edge matching; 37 cases |
| `dogfood/grpc_probes2.py`, `dogfood/grpc_mutants2.py` | the Arm 0 and Arm G runner; the 17 mutants |
| `tests/grpc/test_reconcile2.py` (18 tests), `tests/grpc/test_flows2.py` (41 tests) | P2-UNIT: 59 tests |
| `tests/grpc/fixtures/sessions/**` | grpc-go app `sessionsapp` (6 rpcs, SignOut revokes the session, DeleteMe deletes the account), generated code by `buf` + `protoc-gen-go(-grpc)`, `truth.json` hashed before any round-2 reconcile code ran |
| `tests/grpc/fixtures/deskd_links.json` | `links` of the 7 `deskd` truth flows (34 edges), annotated by this node from the truth messages by a stated rule; hashed before PF2-DESK first ran |

Not touched: the other 11 files of round 1's `freeze-final.sha256`, `tests/grpc` existing files, the N truth, the 31 `deskd` files, round-1 docs
and data. **Frozen files, hash-checked before and after the arms (m):** round-1 listing minus `grpc.py` (11 files) unchanged, N `truth.json` `debbd0b4...`,
`deskd` `freeze.sha256` `8670bf84...` and its 31 files unchanged, `git diff --name-only HEAD` = exactly `lattice/spec/grpc.py` and
`lattice/importer/workflows.py`, no unexpected untracked file (`frozen_before` / `frozen_after` in both probe files: `ok: true`).

**Hashes frozen before any scored probe (2026-10-17 21:34 UTC; `freeze-lib.sha256`; the hashes of `grpc.py`, `workflows.py`, the scorer and the runner are also recorded inside `probes2-armg.json` and `probes2-arm0.json`, taken at run time):**
`lattice/spec/grpc.py` `814b3bc0d27fb648c7ff90eeb61d53107b4cf48a577d88770ce0e12eb3ba9bde`, `lattice/importer/workflows.py`
`5eba7e8ab6ac62ba8ba4cea31088faab9e149e921a952c6a4837e37b92638098`, `dogfood/grpc_flow_scorer.py` `8d896ee0...`, the cases `512b2ef2...`, the two test files, the
sessions `truth.json` `0e9b0d37...`, `deskd_links.json` `604df939...`. The final listing incl. the runners is `freeze2.sha256`
(`grpc_probes2.py` `7ab7dd3f...`, `grpc_mutants2.py` `a26f5084...`). No library file changed after the freeze.

## 2. Blind and post hoc, kept apart

* **Looks before the freeze (disclosed).** While debugging I ran, before freezing: reconcile on sessions (3 times, once with the round-1
  library as a control), reconcile on `deskd` once, flows on N once and flows on `deskd` once, and read the failure output of nothing else.
  I saw the `deskd` flow counts then (41 recovered, 20 verified, same as the scored run). **No F/RC/S rule was changed after seeing any
  `deskd` number.** Library changes made after those looks: the RC3 trigger widening (deviation 1; made from the *sessions* output) and
  the `after_delete` F1 fix (deviation 2; made from my own unit test); the `deskd` counts were identical before and after the latter.
* **Everything scored below is the first scored run of the frozen code.** Each arm ran once; no probe was re-run after a number was seen;
  the mutant run has its own controls.
* **Round-1 erratum (Q9; recorded here, a note goes into the round-1 results doc at merge).** The round-1 results headline says PG-HO
  7/7; the blind figure is **6/7**, and **5/7** under this round's vacuity rule S1/S2 (HO-REC fail, HO-FLOWS fail: 38 flows recovered, 0
  verified, so the "0 verified-but-failing" gate held vacuously). `s4-rescore.json` re-scores round 1's recorded HO-FLOWS numbers from
  `probes-armh-run2.json` and `probes-armh.json` under S1/S2 (**post hoc, nothing re-run**): both **FAIL** (`no_verified_flow`, coverage 0/7).

## 3. Arm 0 (static): predicted vs observed

| id | prediction | observed (m) | verdict |
|---|---|---|---|
| P2-BC | all identical (p) | AST: every top-level def of `grpc.py` outside the 5 allowed, and the whole HTTP section of `workflows.py` (53 defs), identical to `cea85fc`; round-1 emitter 73/73 outputs identical (gocheck extract on 13 fixtures, RepoModels, select, specs, reports, impact, `--help`, MCP `tools/list`, go_harness DCX, minishop CD); extract + RepoModel of N, T, `deskd` 6/6 identical; HTTP workflow tests 69 passed | **PASS** |
| P2-DET | identical, 10/10 (p) | N and `deskd`: 10 processes each, random `PYTHONHASHSEED`, two roots (a byte copy of the app dir for the second), hashes of extract, static plans, flows (messages, codes, bindings, statuses) and reconcile output identical 10/10, no absolute path, time or port in any text | **PASS** |
| P2-NW | 0 changed, 0 new, 0 calls | N, T, sessions, `deskd`: reconcile x3 and flows x3 (N, `deskd`), a recording `claude` shim on `PATH`: 0 changed, 0 new, 0 calls. `heldout2`: **NOT RUN** (gated stage) | **PASS** (4 roots) |
| P2-UNIT | >= 30 tests green (p: 30-45) | **59** passed (18 + 41); above the predicted range | **PASS** |
| P2-SCORE | >= 30 cases (p: 32-40) | **37**/37 agree: S1 empty denominators (rate, edge recall, coverage), S2 each conjunct alone failing, D4 matching with the field, the step, the consumer code, the producer rpc and the producer field each wrong, truth defects, > 1/3 truth defects, status filter, S3 histograms, S4 | **PASS** |
| REG | the same failing ids (105 baseline) | `pytest tests/spec tests/importer tests/gocheck tests/mcp tests/studio tests/grpc` on `git archive HEAD` (before): 7 failed, 98 errors, 2816 passed, 37 skipped; on the worktree (after): 7 failed, 98 errors, **2875** passed (+59 = the new tests), 37 skipped; the 105 `FAILED/ERROR` lines (95 distinct ids) are the same set. An earlier "before" run in the worktree (started before my edits, ran concurrently with them) gave the same counts; the HEAD-archive run is the one scored | **PASS** |

## 4. Arm G (Go servers over pure-Go SQLite; no Docker): predicted vs observed

| id | prediction (p) | observed (m) | verdict |
|---|---|---|---|
| PR2-N | identical to round 1: 10/10 answered, `ShareNote` and absent UNIMPLEMENTED, bytes equal | 10/10 answered, `ShareNote` UNIMPLEMENTED, absent UNIMPLEMENTED; output **byte-equal** to the `cea85fc` library's, no extra key | **PASS** |
| PR2-DESK (dev) | implemented 17/17 answered, no `auth_refused`; `MergeTickets` 1/1 and `Reports/Backlog` 1/1 UNIMPLEMENTED; absent 1/1 | **17/17** answered, no `auth_refused`, no order disagreement; `MergeTickets` 1/1 UNIMPLEMENTED; absent 1/1. **`Reports/Backlog` is not in the library's output**: the model has no route for a declared-but-unregistered service, and the library calls only `model.routes`. One supplementary `_call_fresh` call (not library output) answered UNIMPLEMENTED. 2.23 s | **PASS on the library output; the prediction's Backlog clause is not observable from the library (a prediction miss, see section 7)** |
| PR2-ORDER | 0 on N; 0-2 on `deskd` (p); >= 1 on sessions (its `DeleteMe`), all resolved; final = truth | N 0; `deskd` **0**; sessions **4** (`DeleteMe`, `Secret`, `SignOut`, `Stub`), all `isolated: true`, 0 `login_failed`, final codes equal `truth.json`'s alone codes | **PASS** (4, not 1: deviation 1) |
| PR2-SESS | round 1: `Stub` answered:true with UNAUTHENTICATED; round 2: `Stub` UNIMPLEMENTED, `Secret` answered, 0 `login_failed` | control (library of `cea85fc` on the sessions fixture): `DeleteMe`, `Secret`, `Stub` all UNAUTHENTICATED answered:true (**the defect reproduced**); round 2: `Stub` UNIMPLEMENTED answered:false, `Secret` OK, `SignOut` OK, `DeleteMe` OK, `Login` INVALID_ARGUMENT, `Health` OK, 0 `login_failed` | **PASS** |
| PF2-N | verified 15-20 (round 1: 17), 0 vbf, 0 unbound; S2 with verified >= 3 | recovered 17, **verified 17** (account 1, after_delete 2, lifecycle 4, owner_guard 5, pair 5), vbf 0, unbound 0; same counts as round 1; edge recall and coverage not gated (N truth has no links) | **PASS** |
| PF2-DESK (dev, tuned-on) | recovered 30-55, verified 8-25, unbound 0-6, failed the rest; edge recall 0.35-0.70; coverage 3-6 of 7; exact 0-1 of 7 | recovered **41**, verified **20**, unverified 0, failed 21, unbound **0**; edge recall **21/34 = 0.618**; coverage **4/7**; exact **0/7**; vbf 0; all 7 truth flows pass their own replay; S2 pass. Failing step codes: INVALID_ARGUMENT 19, FAILED_PRECONDITION 1, PERMISSION_DENIED 1. 10.6 s | **PASS** (every number inside the predicted range) |
| PF2-REPLAY | all pass (p) | N: 17 verified x3 replays on fresh databases, 0 failing; `deskd`: 20 x3, 0 failing | **PASS** |
| PF2-LEAK | 0 | 0 live processes or bridges after reconcile (with RC3 isolated runs) and flows on N, sessions, `deskd`; a forced startup failure: reconcile raised `ProcessStartError`, every flow `failed`, 0 left; 0 stray app or bridge processes in `/proc` | **PASS** |
| PR1 | every row as round 1's final results | `grpc_probes.py --arm 0` and `--arm G` unchanged at the round-2 code (`--base` = `git archive 6a318e5`): Arm 0 11/11 PASS rows still PASS, Arm G 10/10 PASS rows still PASS (PG-HO NOT RUN as in round 1). PG-WF: 17 flows, all verified (round 1's own prediction "3-6 flows" is reported "missed", as in round 1) | **PASS** |
| PX2 | 1.5-8 s; 15-90 s (p) | reconcile on `deskd` **2.23 s** (in range); flows on `deskd` **10.6 s** (below the predicted range; the gate <= 180 s holds) | **PASS** (the flows prediction range missed low) |

Held-out (PG-HO2, 7 rows): **NOT RUN**, by design.

`deskd` flows by kind and status (m, `flows-deskd.json`): pair 6 verified / 11 failed; owner_guard 8 / 7; after_delete 3 / 3; lifecycle 2 / 0; owner_chain 1 / 0.
Lookup step `desk.v1.Queues/ListQueues` is step 1 of the 23 plans that open a ticket (queue slug `billing`), recorded with `role: lookup`; the 18 `AddRemark`-first plans have none.
Truth edges contained (m): ticket_lifecycle 3/9, owner_isolation 8/9, delete_then_gone 5/5, reopen_limit 5/8, unserved_and_bounds 0/3;
`public_and_anonymous` and `sign_up_and_in` have no edges, so they cannot be covered under D4's literal denominator (coverage 4/7).
Distinct step signatures among the 20 verified flows: 19 (`owner_chain` repeats an `owner_guard`'s steps for `AddRemark`; the kinds differ).

## 5. Mutants (`dogfood/grpc_mutants2.py`; scratch copy; control green first; predicted 17/17)

Control (unmodified copy): all 9 distinct checks rc 0. A mutant is KILLED when any of its checks changes verdict from the control's. Every
listed check is run, so the table says which ones fired. "P2-UNIT" = `tests/grpc/test_reconcile2.py` + `test_flows2.py`.

| id | mutant | killed (m) | by | the pre-registration's killer fired? |
|---|---|---|---|---|
| GR1 | one token reused | yes | PR2-SESS probe, P2-UNIT | PR2-SESS yes; PR2-DESK not run in the mutant harness |
| GR2 | token per service | yes | PR2-SESS probe, P2-UNIT | yes |
| GR3 | reverse pass skipped | yes | P2-UNIT only | **no: PR2-ORDER passes** (the `login_failed` trigger isolates the same rpcs) |
| GR4 | no isolated rerun | yes | PR2-ORDER probe, P2-UNIT | yes |
| GR5 | failed login = UNAUTHENTICATED answered | yes | P2-UNIT | yes |
| GR6 | contract order in the forward pass | yes | P2-UNIT | P2-DET cannot fire (a deterministic order); the named unit test did |
| GR7 | F2 off | yes | PF2-DESK probe, P2-UNIT | yes |
| GR8 | F3 off (bool true) | yes | P2-UNIT | **no: PF2-DESK passes** (its AddRemark-first plans already fail) |
| GR9 | F1 binds an id to another table's field | yes | P2-UNIT | P2-UNIT yes; **PF2-N passes** |
| GR10 | lookup takes the first element | yes | P2-UNIT | **no: P2-DET passes** (the server's order is stable) |
| GR11 | guards verify on UNAUTHENTICATED | yes | P2-UNIT | P2-UNIT yes; **PF2-DESK passes** |
| GR12 | verified without the layer-D replay | yes | P2-UNIT (`test_d3_a_flow_the_replay_disagrees...`) | P2-UNIT yes; **PF2-REPLAY passes** |
| GR13 | a fault counted as a refusal | yes | P2-UNIT | P2-UNIT yes; **PF2-N passes** |
| GR14 | scorer: verified == 0 passes | yes | P2-SCORE | yes |
| GR15 | scorer: empty denominator scores 1.0 | yes | P2-SCORE | yes |
| GR16 | scorer: edge matching ignores the consumer code | yes | P2-SCORE | yes |
| GR17 | HTTP path touched (rpc summary shape in the HTTP `summary`) | yes | the AST gate of P2-BC (`p2-bc-ast`) | P2-BC yes; the HTTP workflow tests (69) **do not** notice |

**17/17 killed, by probes or named unit tests** (the pre-registration's pass wording). 7 of the 17 are killed only by the unit tests, not
by the probe the table of section 8 named; those probe-named killers did not fire: GR3-PR2-ORDER, GR8-PF2-DESK, GR9 and GR13-PF2-N, GR6 and
GR10-P2-DET, GR11-PF2-DESK, GR12-PF2-REPLAY.

## 6. Deviations (stated plainly)

1. **RC3 trigger widened.** The pre-registration says an rpc whose code differs between the two passes is rerun isolated. On a database
   where `DeleteMe` runs first (sorted order), the first pass leaves no account to log in as, so both passes give `login_failed` for
   `SignOut`, `Secret`, `Stub`: they *agree*, and the literal rule would have left three `login_failed` finals, contradicting PR2-SESS's own
   expectation (0 `login_failed`). The implemented rule: also rerun isolated an rpc whose forward pass could not log in. Made after
   seeing the sessions output (not `deskd`'s). It makes sessions list 4 disagreements, not 1, and makes GR3 invisible to PR2-ORDER.
2. **F1 leak in round 1's `after_delete`, fixed.** The round-1 code bound a deleted row's id to *any* id input of the reading rpc
   (`r.id_inputs[0]`) when none matched; my own unit test for F1/GR9 found it (an `Audit` rpc taking `user_id` got a ticket id). Now only
   `id`, `<singular>_id` or `<table>_id` qualify (the prereg's F1). No effect on N or `deskd` counts.
3. **`Reports/Backlog`** is not in `reconcile_with`'s output (see PR2-DESK). A not-registered rpc is not a route of the model.
4. **Second root of P2-DET on N** copies the app directory for extraction and reconcile (studio.toml, harness, protos); the flows run
   through the original harness module. For `deskd` the whole harness is loaded from the copy.
5. **Mutant killers.** GR6 and GR10 name P2-DET, which cannot fire on a deterministic server; the unit tests kill them (section 5).
6. **`owner_chain` is added whenever F4's condition holds**, as written; on `deskd` it duplicates an `owner_guard`'s steps for `AddRemark`.
   Verified-flow counts include both (20 flows, 19 distinct step lists).
7. **`--arm H2` is not built.** It refuses to run. The held-out arm needs the new truth schema in hand; it is the orchestrator's stage.
   `heldout2/` was never opened. P2-NW's `heldout2` root and PG-HO2 are NOT RUN. The prereg's "hash `grpc_probes2.py --arm H2` before opening
   `heldout2/truth.json`" therefore waits for that stage; this node's `freeze2.sha256` covers the dev-stage runner only.
8. **P2-NW** includes the sessions fixture and `deskd`; the `deskd` check runs against its scratch directory, read-only.
9. **Scorer cases.** One case of mine (a truth flow failing its own replay) first expected a pass with 1 of 2 truth flows defective (50% >
   1/3); I corrected my expectation (a third flow) before any scoring. Not tuning.
10. **REG baseline** is a run of `git archive HEAD` (not the round-1 counts: the merge of `next-phases` added tests); Q5's fix is in HEAD, so
    the baseline has 105 failing lines (95 distinct ids), the prereg's "105 baseline", with no extra id.
11. **`reconcile_with` has no `root`/`proto_root` argument**; the protos are named by absolute path (the runner does this for `deskd`).
12. `timeout` of `reconcile`/`reconcile_with` is unused, as in round 1.

## 7. Gates missed and prediction misses (plainly)

* **Gates of section 7 (dev rows): none missed.** Arm 0 6/6, Arm G 10/10.
* **Prediction misses (p), all non-gating:** PR2-DESK's `Reports/Backlog` clause (deviation 3); PX2 flows 10.6 s below the predicted 15-90 s;
  P2-UNIT 59 above the predicted 30-45; PR2-ORDER on sessions 4 vs the predicted "its `DeleteMe`"; PG-WF "3-6 flows" (round 1's own, 17 observed);
  7 mutants' probe-named killers did not fire (section 5).
* **Not a miss but a ceiling:** coverage 4/7 can never exceed 5/7 on `deskd` under D4's literal denominator (2 truth flows have no edges).

## 8. Findings from the dev data (observations; nothing was tuned on them)

* **18 of the 21 `deskd` failures are `AddRemark` plans** (INVALID_ARGUMENT at step 1: 3 after_delete, 7 owner_guard, 8 pair). `AddRemark` creates `ticket_notes`, and the
  closure's `tables` of every ticket rpc lists `ticket_notes`, so F1 reads `AddRemark` as a *producer* for them and runs it without a ticket.
  The cause is round 1's edge definition (`tables` is over-inclusive), not F2/F3. A candidate post-hoc fix: require the producer's created
  table to be one the consumer reads by its id, or drop producers whose own request needs an id of a table that no step creates.
* The remaining failures are legitimate refusals the planner expects as `ok`: `CloseTicket` as a member (PERMISSION_DENIED), `ReopenTicket`
  on an open ticket (FAILED_PRECONDITION), `UpdateTicket` with only an id (INVALID_ARGUMENT: F3 leaves `optional` fields out, as round 1 did).
* The lookup step worked as designed: `ListQueues` (public, first in sorted order that reads `queues` and answers a repeated message with a
  `slug`) supplies `billing`; the closed `legacy` queue is never picked (`include_closed` false).
* Edge recall 0.618 comes from `OpenTicket -> {Get, Hold, Resolve, Delete, AddRemark, ListRemarks}` pairs and the owner guards; the 13 missed
  edges are the truth flows' state-dependent codes (`CloseTicket` ok/denied, `ReopenTicket` refused states, `UpdateTicket`).
* The vacuity rule did its job on round 1's numbers (S4): the same 38-failed result that passed round 1's rule fails S2.

## 9. What a later stage still needs

The gated HO2 stage: a new app and `links` truth by a new agent; `--arm H2` in `grpc_probes2.py` (HO2-RPC/CODES/AUTH/TABLES/REC/FLOWS/DET)
built before `heldout2/truth.json` is opened; the hash of the library listing in Amendment 1; HO2 scored once. This node's frozen library
and scorer hashes (section 1) are what that stage should check. Arm M and Arm L remain out of scope.

Commands (from the pre-registration, section 12): `uv run python dogfood/grpc_probes2.py --arm 0 --base <cea85fc> --deskd <heldout/deskd> --reg-before ... --reg-after ...`;
`--arm G ... --base6a <6a318e5>`; `uv run python dogfood/grpc_mutants2.py --scratch ... --base ... --deskd ... GR1..GR17`;
`ruff check` and `ruff format --check` clean on every touched or created `.py` (before: the two library files clean at HEAD); `gofmt -l tests/grpc/fixtures/sessions` empty.
