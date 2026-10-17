# gRPC round 2 (state-independent reconcile, rpc flows that verify, a non-vacuous flows gate): pre-registration

## 1. Status, and the exact gap

2026-10-17. Base: branch `spike-i-grpc` at `cea85fc` (round 1, worktree `/home/user/wt/np-grpc`, not merged into
`next-phases` 1955f0b). **Written BEFORE any round-2 code; no model was called; nothing is committed or staged.** Round 1:
`2026-10-17-grpc-prereg.md` (with Amendments 1 and 2), `2026-10-17-grpc-results.md`, data
`docs/results/data/2026-10-17-grpc/` (all in the round-1 worktree). Results of this round:
`2026-10-17-grpc-round2-results.md`, data `docs/results/data/2026-10-17-grpc-round2/`. Labels: **(m)** measured here
or in round 1's data, **(r)** read from round-1 code or data while writing this, everything else a **prediction (p)**.

**This is a development-set round for every corpus it can already see.** The round-1 held-out app `deskd` (31 files,
`freeze.sha256` sha256 `8670bf84cdcc9175797507f05e55e62f11716659dc9a0e7a6368aa14dab729f6`, verified again while writing
this (m)) was seen by the round-1 author when scoring, and by this author while writing this document (its `truth.json`
flows and `harness.py` were read). **So `deskd` is development data for round 2, never a blind or held-out claim.** The
only gated held-out of round 2 is a NEW app written by a NEW separate agent (section 6), hashed before any gated probe,
scored once.

**Environment (m, 2026-10-17, this container).** `go version go1.24.7 linux/amd64`; `uv 0.11.32`; `uv run python
--version` 3.13.16 (round 1 recorded the same 3.13 deviation); `docker info`: no daemon (`/var/run/docker.sock` absent);
no Streak checkout. **Docker-free arms (0 and G over pure-Go SQLite) are the primary and only gated arms.** Arm M
(Streak/MySQL) and Arm L (build round, node I2) are **out of scope** and reported NOT RUN in the first line of the results.
Streaming stays `not supported`.

### 1.1 Round-1 results, blind and post hoc kept apart

| stage / probe | blind (first scored run) | post hoc (after a fix made on seeing the miss) | source |
|---|---|---|---|
| REG (same failing ids) | **FAIL by one id** (`test_every_added_field_is_in_the_goldens_added_defaults`) | not fixed | results "Missed gates" 1 |
| P0-DET | **FAIL** (descriptor-set option order) | PASS 10/10 after `canonicalFile` | miss 2 |
| P0-BC | **FAIL** (wall-clock note in the DCX report) | PASS 73/73 after a probe normalisation | miss 3 |
| other Arm 0 rows (9) | PASS | | |
| Arm G (10 rows) | PASS (PG-WF and PG-X point predictions missed, gates passed) | | |
| mutants | 16/16 | | |
| **PG-HO, held-out `deskd`** | **6/7, not 7/7.** Run 1 crashed before any number (runner, relative proto paths). Run 2 (first scored, scorer `69e49596...`): **HO-REC FAIL** | 7/7 in run 3 (scorer `5406bbbe...`) after the runner re-implemented reconcile with a fresh login per call. **Run 3 is post hoc and is not a blind result.** | held-out section, run history |
| HO-FLOWS (inside PG-HO) | PASS **by the round-1 rule, vacuously**: 38 flows recovered, **0 verified**, 38 failed; truth flows recovered 0/7, covered 0/7 | unchanged (the same in run 3) | held-out findings 1 |
| PG-HO under round 2's scorer rule S1 (section 5), **re-labelled post hoc** | **5/7** (HO-REC FAIL, HO-FLOWS FAIL: vacuous) | 6/7 (HO-FLOWS still FAIL) | this document, a relabel, not a re-run |

**The two causes (verified by an independent agent; r: code read at `cea85fc`).**
1. **HO-REC.** `lattice/spec/grpc.py` `reconcile` (line ~395) logs in **once** (`runtime.auth.token(transport,
   next(iter(users)))`, line 425) and iterates `model.routes` in contract order. `desk.v1.Identity/SignOut` precedes
   `Queues` and `Tickets`, answers OK and revokes that session; every later non-public rpc, `Tickets/MergeTickets`
   included, answers `UNAUTHENTICATED` from the interceptor, which the library records as `answered: true`
   (`code != "UNIMPLEMENTED"`). The round-1 runner's later re-implementation (fresh login per call) **hid the library
   defect**; the library was not fixed (frozen).
2. **HO-FLOWS.** `workflows.rpc_message`/`example_value` fill every scalar of a writing rpc from type and bounds:
   `queue_slug = "wf-queue_slug"` names no seeded queue -> `OpenTicket` NOT_FOUND in 20 flows; `internal = true` on
   `AddRemark` as a member -> PERMISSION_DENIED in 18 flows. Only `id`/`<x>_id` fields are threaded; a field naming an
   existing row by a natural key, or one gated by a role, has no source. The round-1 gate ("0 verified-but-failing")
   holds trivially when nothing is verified.

### 1.2 Scope (binding)

- **(a)** `reconcile` in the **library** answers each rpc independently of the state left by earlier calls (D1). The
  runner calls the library; it never re-implements reconcile.
- **(b)** rpc flow synthesis and replay verification that can reach `verified` on `deskd`-like apps: id threading,
  natural-key threading from responses or a lookup step, least-privilege example values, ownership chains; recall
  measured against truth flows by a pre-registered edge definition (D2-D4).
- **(c)** a scorer rule: a flows probe with 0 verified flows, or any gated rate with an empty denominator, is a **FAIL**
  (S1, S2).
- **Out of scope:** streaming; Arm M; Arm L; layers A/R/X/B over rpcs (round-1 D9 stands); the Go extractor and the
  bridge (frozen, section 9).

**Rulings requested:** section 12.

## 2. Decisions: reconcile (fixed before coding)

**D1 State-independent reconcile (library, `lattice/spec/grpc.py`).**
- **D1.1 API.** New `reconcile_with(model, *, make_app, build_db, runtime, users, protos=(), proto_paths=(), login=None,
  timeout=30.0)`; `reconcile(root, model)` keeps its signature and becomes a thin wrapper that reads `studio.toml [app]`
  as today and calls `reconcile_with`. This closes round-1 harness gap (c): a harness without `studio.toml` is
  reconciled by the library through `reconcile_with`, not by a runner copy.
- **D1.2 Fresh session per call (rule RC1).** Every non-public rpc is called with a token obtained by a login made
  **immediately before that call** (first user of `USERS`); no token is reused across calls. A login that fails records
  `{code: null, answered: null, reason: "login_failed"}` for that rpc, never a code and never `answered: true`.
- **D1.3 Order check (rule RC2).** The rpcs are called twice on the same process: forward (sorted by
  `<Service>/<Method>`, not contract order) and reverse. An rpc whose code differs between the two passes is re-run
  **isolated** (rule RC3): a fresh database from `build_db`, a fresh process, a fresh login, that rpc alone; the isolated
  answer is reported with `isolated: true`. So no answer depends on which rpcs ran before it: either both orders agree
  or the rpc ran alone.
- **D1.4 Rejected as the primary rule: skipping "destructive session rpcs by contract".** The contract does not say
  which rpc revokes a session or deletes an account; a name list (`SignOut`, `Logout`, `DeleteAccount`) is a guess
  (round-1 D8 forbids guesses). RC1-RC3 need no such list. If the orchestrator prefers a skip rule as well (Q1), it is
  additive: an rpc whose isolated answer differs from both passes is listed `order_sensitive`, still answered isolated.
- **D1.5 Output.** Per rpc `{code, answered}` exactly as round 1; added keys only when not default: `isolated: true`,
  `reason`. `answered` stays `code != "UNIMPLEMENTED"`; plus a top-level `order: {disagreed: [...]}` only when non-empty.
  On N the output is predicted byte-identical to round 1 (no rpc there revokes a session).
- **D1.6 Never** an interceptor refusal (UNAUTHENTICATED) on a non-public rpc counted as `answered` without a note:
  the result lists `auth_refused: [...]` for non-public rpcs that answered UNAUTHENTICATED with a fresh token (a
  reconcile finding, not a pass).

## 3. Decisions: rpc flows (fixed before coding; `lattice/importer/workflows.py` rpc section, `lattice/spec/grpc.py`
`run_rpc_flows`)

**D2 Synthesis rules.** Round 1's plan kinds stay (`pair`, `owner_guard`, `after_delete`, `lifecycle`, `account`).
- **F1 id threading (round 1, kept).** A response `id`/`<x>_id` of an rpc that creates table T feeds a later request
  field `id`, `<singular(T)>_id` or `<T>_id` of an rpc reading or writing T.
- **F2 natural-key threading (new).** A request **string** field `<s>_<col>` with `col != "id"`, where `s` is the
  singular of a table T in the consumer rpc's `tables`, is bound (in this order): (a) from an earlier step's response
  field `col` in a message of an rpc that creates or reads T; else (b) from a **lookup step** inserted before the step
  that needs it: the first implemented rpc in sorted key order that reads T, has no `id`-like input, and answers a
  repeated message field whose element message has a field `col`; it is called with an empty message (as A unless
  public); the value is the smallest `col` among the elements (sorted, deterministic). Else (c) the flow is
  **`unbound`** with `reason: "no_source:<field>"` (a new status, not `failed`).
- **F3 least-privilege example values (new; chosen after seeing `deskd`, disclosed as dev-tuned).** For a field F1/F2
  does not bind: `bool -> false`; enum -> the first value with number != 0 (proto3's 0 is `*_UNSPECIFIED`); string and
  integers as round 1; repeated, map and message fields left out (as round 1).
- **F4 ownership chain (new kind `owner_chain`).** When a consumer C takes `<s>_id` of a parent row created by P, and
  C itself creates a child table, a plan `P as A; C as B with A's id` with intent `refuse` is added (another user must
  not attach a child to A's row). `owner_guard` and `owner_chain` refusals verify only on `NOT_FOUND` or
  `PERMISSION_DENIED` (never `UNAUTHENTICATED`, `INVALID_ARGUMENT` or a fault).
- **F5 lookup steps are recorded** as ordinary steps (`role: "lookup"`), replayed and verified like any other.
- **F6 binding record.** Every recorded step carries `bound: {request_field: {step: n, from_field: name}}` (F1, F2 and
  F2(b) all recorded). Schema bumps to **`lattice.rpc-flows/2`** (Q6): status `verified | unverified | failed | unbound`.

**D3 Verification (round 1, kept and tightened).** `verified` only when every step answered as its intent says (`ok`:
`OK`; `refuse`: a code in `RPC_REFUSALS`, and for guard kinds F4's two codes), no step answered a code in `RPC_FAULTS`,
and the recorded scenario passes a layer-D replay on a fresh database. Determinism: the same plans, messages and
statuses over 10 processes with random `PYTHONHASHSEED` and a second scratch root.

**D4 Recall against truth flows (pre-registered; replaces the round-1 runner's own definition).**
- A truth flow's **edges** are its `links` (section 6 requires them): `(producer rpc, producer response field, consumer
  rpc, consumer request field, consumer expected code)`. A recovered **verified** flow **contains** an edge when it has a
  step pair whose rpcs match, whose consumer's `bound[field]` names that producer step and field, and whose consumer's
  recorded code equals the expected code.
- **Edge recall** = edges contained in at least one verified flow / all edges of passing truth flows.
- **Flow coverage** = truth flows with at least one contained edge / passing truth flows.
- **Exact recall** (reported only) = truth flows whose `(rpc, code)` sequence equals a verified flow's.
- A truth flow that fails its own replay as `Rpc` scenarios is a truth defect: reported, its edges leave the
  denominator; if more than one third of truth flows fail, HO2-FLOWS is FAIL (not scorable).

## 4. Decisions: never

No network target beyond loopback; no Docker; no write under any target root; no git on a target; no LLM or API key in
extraction, reconcile, flows, scorer or runner; no edit to `.proto`, spec, `truth.json` or harness files of any corpus;
no edit to round-1 frozen files (section 9); a held-out score taken once (section 7).

## 5. Scorer rules (`dogfood/grpc_flow_scorer.py`, pure; cases in `dogfood/grpc_flow_scorer_cases.json`, >= 30)

- **S1 vacuity.** Any gated rate whose denominator is 0 is **FAIL** with `reason: "vacuous"` (never 1.0, never PASS).
- **S2 HO2-FLOWS / PF2-* pass rule.** `verified >= 1` **and** `verified_but_failing == 0` **and** edge recall >= the
  row's bound **and** flow coverage >= the row's bound. A pass of the old rule with `verified == 0` is FAIL.
- **S3 reported beside every flows row:** recovered by status (`verified/unverified/failed/unbound`), exact recall, the
  failing step's code histogram, unbound reasons.
- **S4** the scorer reports the round-1 `deskd` HO-FLOWS numbers re-scored under S1/S2 **labelled post hoc** (dev),
  from `probes-armh-run2.json` and `probes-armh.json`, with no re-run of round 1.

## 6. The new held-out (commissioning described; not written here)

**Author.** A NEW separate agent: not the round-1 held-out author, not the round-1 or round-2 builder, not this
author; dispatched by the orchestrator **after this document is committed and before the build node runs any gated
probe**. It is given: round-1 D1/D2 scope (proto grammar, grpc-go idioms read), D1-D4 of this document in prose, the
harness contract, and the truth schema below. It is **not** given N, `deskd`, the library code or any round-1/2 data.

**Brief (fixed now).** A grpc-go app over pure-Go SQLite (GORM + `github.com/glebarez/sqlite` or another pure-Go
driver it pins in its own `go.sum`), buildable here with the Go proxy, no Docker, no cgo, written to
`$SCRATCH/heldout2/<app>/` outside the repository. Shape minimums: >= 3 registered services, >= 15 unary rpcs, >= 1
`Unimplemented` rpc, >= 1 streaming rpc, >= 1 declared-but-unregistered service; **>= 1 rpc that revokes the caller's
session and >= 1 rpc that changes caller-visible state (delete or role change), both sorting before at least one
other non-public rpc** (tests D1); **>= 2 request fields naming a row by a natural key** (slug/code/handle) seeded by
`build_db` (tests F2); **>= 1 role-gated request field** (tests F3); **a parent -> child ownership chain of >= 2
levels** (tests F4); >= 2 users of different roles. Truth `truth.json` (`lattice.heldout.truth/2`): rpcs with state;
codes per implemented handler with file:line; auth per rpc; tables; owner checks; **>= 6 flows of >= 3 steps** with
`as`, `message`, `expect_code`, and **`links: [{step, field, from_step, from_field}]`** on every step that uses an
earlier response; >= 2 flows whose first write needs a seeded natural key. A `harness.py` with `make_app`, `build_db`,
`RUNTIME`, `USERS`, `PROTOS`, `PROTO_PATHS`, `RPC_LOGIN`, **and `make_app` filling `protos`/`proto_paths`/`proto_root`
and `ready="grpc"`**, plus a `studio.toml [app]` (round-1 harness gap 5 closed by the brief; the library also works
without it via D1.1).

**Manifest.** The author writes `FILES.txt`; the orchestrator records `sha256sum` of every listed file (sorted) in
`freeze.sha256` and **the sha256 of `freeze.sha256` in Amendment 1 of this document before any gated probe**. The
builder never opens `heldout2/`; the runner reads it.

## 7. Probes (runner `dogfood/grpc_probes2.py`; blind pass rules)

**Arm 0 (static).**

| id | probe | prediction | pass |
|---|---|---|---|
| P2-BC | byte-compat promise (section 10) on the round-1 corpora | all identical (p) | all |
| P2-DET | flows plans, messages, statuses and reconcile output on N and `deskd`: 10 processes, random hash seeds, second root | identical (p) | 10/10 |
| P2-NW | no write under N, T, `deskd`, the sessions fixture or `heldout2` across reconcile x3 and flows x3; a recording `claude` shim on `PATH` | 0 changed, 0 new, 0 calls | all 0 |
| P2-UNIT | `tests/grpc/test_reconcile2.py`, `tests/grpc/test_flows2.py`: each of RC1-RC3, F1-F6, D3, D4 on unit fixtures | >= 30 tests green (p: 30-45) | all green |
| P2-SCORE | scorer cases: S1 (empty denominators of every rate), S2 (each conjunct alone failing), D4 edge matching (field, step, code each wrong) | >= 30 cases (p: 32-40) | 100% |
| REG | `pytest tests/spec tests/importer tests/gocheck tests/mcp tests/studio tests/grpc` (round-1 flags) | the round-1 after-run failing ids exactly (105 baseline + the one REG id), unless Q5 rules the fix | same set |

**Arm G (Go servers over pure-Go SQLite; no Docker). Dev rows are development data (`deskd` included).**

| id | probe | prediction | pass |
|---|---|---|---|
| PR2-N | library `reconcile` on N | identical to round 1: 10/10 answered, `ShareNote` and absent UNIMPLEMENTED; output bytes equal to round 1's | exact |
| PR2-DESK (dev) | library `reconcile_with` on `deskd` (no runner re-implementation) | implemented 17/17 answered with no `auth_refused`; `MergeTickets` 1/1 and `Reports/Backlog` 1/1 UNIMPLEMENTED; absent 1/1 | 100% |
| PR2-ORDER | forward vs reverse vs isolated on N, `deskd` and the sessions fixture | 0 disagreements on N; 0-2 on `deskd` (p), each resolved by RC3; >= 1 on sessions (its `DeleteMe`), resolved | every disagreement resolved isolated; final codes = truth |
| PR2-SESS | new fixture `tests/grpc/fixtures/sessions/` (grpc-go, 6 rpcs: `Login`, `SignOut`, `DeleteMe`, `Secret`, `Stub` (Unimplemented), `Health`; truth hashed before code): round-1 reconcile (control, from `cea85fc` in scratch) vs round-2 | round 1: `Stub` reported `answered: true` with UNAUTHENTICATED (p, the defect reproduced); round 2: `Stub` UNIMPLEMENTED, `Secret` answered, 0 `login_failed` | round 1 reproduces the defect AND round 2 exact |
| PF2-N | flows on N | verified 15-20 (p; round 1: 17), verified-but-failing 0, unbound 0 | S2 with edge recall and coverage reported (N truth has no links); 0 vbf; verified >= 3 |
| PF2-DESK (dev, tuned-on) | flows on `deskd`; edges from `tests/grpc/fixtures/deskd_links.json` (the builder annotates `links` for the 7 truth flows from the truth messages, hashed before PF2-DESK first runs) | recovered 30-55, **verified 8-25**, unbound 0-6, failed the rest; edge recall 0.35-0.70; coverage 3-6 of 7; exact 0-1 of 7 (p) | S2 with edge recall >= 0.30, coverage >= 2 |
| PF2-REPLAY | every verified flow of PF2-N and PF2-DESK replayed again on a fresh database x3 | all pass (p) | 100% |
| PF2-LEAK | no app or bridge process after reconcile (incl. RC3 isolated runs) and flows, and after a forced startup failure | 0 | 0 |
| PR1 | round-1 `dogfood/grpc_probes.py --arm 0` and `--arm G`, unchanged, at the round-2 code | every row as round 1's final results; PG-WF counts may differ (reported) | all PASS rows still PASS |
| PX2 | wall time: reconcile on `deskd` (two passes + any isolated reruns); flows on `deskd` | 1.5-8 s; 15-90 s (p) | <= 30 s; <= 180 s |

**Held-out (PG-HO2, scored once after the freeze; section 6's app).**

| id | metric | prediction (p) | pass |
|---|---|---|---|
| HO2-RPC | rpc recall / precision, state included | 0.90-1.00 / 0.95-1.00 | >= 0.90 / >= 0.95 |
| HO2-CODES | codes micro recall / precision (implemented rpcs, `OK` excluded, as round 1's runner) | 0.70-0.95 / 0.90-1.00 | >= 0.70 / >= 0.90 |
| HO2-AUTH | wrong / unclear | 0; unclear 0-0.3 | 0 wrong (hard) |
| HO2-TABLES | tables recall | 0.9-1.0 | >= 0.9 |
| HO2-REC | library `reconcile_with`, both orders, isolated reruns | 100%; 1-3 order disagreements, all resolved | 100% of truth states, 0 `auth_refused` on implemented non-public rpcs, every disagreement resolved |
| HO2-FLOWS | D4 and S2 | verified 3-12; edge recall 0.30-0.65 (point 0.45); coverage 2-5 of >= 6; exact 0-1; 0 vbf | **S2: verified >= 1, 0 vbf, edge recall >= 0.30, coverage >= 2 truth flows** |
| HO2-DET | extract, describe, model, reconcile, flows: 10 processes, 2 roots | identical | 10/10 |

**Probe count: 23** (Arm 0: 6; Arm G dev: 10; held-out: 7). The most likely held-out miss (p): HO2-FLOWS edge recall
below 0.30 if the new app names rows by a key shape F2 does not read (a field not of the form `<s>_<col>`, or a lookup
rpc that needs a filter); that is a finding, not tuned.

## 8. Mutants (`dogfood/grpc_mutants2.py`, scratch copy; control green first; predicted 17/17 killed)

| id | mutant | killed by |
|---|---|---|
| GR1 | reconcile reuses one token (round 1's behaviour) | PR2-SESS, PR2-DESK |
| GR2 | fresh token per service, not per call | PR2-SESS |
| GR3 | reverse pass skipped (RC2 off) | PR2-ORDER (sessions), P2-UNIT |
| GR4 | disagreement reported without the isolated rerun (RC3 off) | PR2-ORDER, P2-UNIT |
| GR5 | a failed login recorded as UNAUTHENTICATED `answered: true` | P2-UNIT |
| GR6 | contract order instead of sorted order in the forward pass | P2-DET (order key in output), P2-UNIT |
| GR7 | F2 off (natural keys filled by `example_value`, round 1) | PF2-DESK |
| GR8 | F3 off (`bool -> true`, round 1) | PF2-DESK |
| GR9 | F1 binds an id to a field of another table (`<x>_id` with x != singular(T)) | P2-UNIT, PF2-N |
| GR10 | lookup picks the first element unsorted | P2-DET |
| GR11 | `owner_guard`/`owner_chain` verify on `UNAUTHENTICATED` | P2-UNIT, PF2-DESK |
| GR12 | `verified` set from the live run without the layer-D replay | PF2-REPLAY, P2-UNIT |
| GR13 | a fault code (INTERNAL/UNKNOWN) on a `refuse` step counted as a refusal | P2-UNIT, PF2-N |
| GR14 | scorer: `verified == 0` passes (round 1's rule) | P2-SCORE |
| GR15 | scorer: empty denominator scores 1.0 | P2-SCORE |
| GR16 | scorer: edge matching ignores the consumer code or the bound field | P2-SCORE |
| GR17 | HTTP workflow path touched (an rpc-section helper used by the HTTP planner) | P2-BC |

## 9. Frozen and changeable files (exact)

**May change (round 2's only product edits):**
- `lattice/spec/grpc.py` (round-1 `freeze-final.sha256` `6f6ca113...`): `reconcile` (becomes a wrapper),
  new `reconcile_with`, `run_rpc_flows` and private helpers; nothing else in the file (AST of every other top-level
  def identical, checked by P2-BC).
- `lattice/importer/workflows.py`: **the rpc section only** (from the `# ---- rpc flows (Spike I, D6)` marker to the end
  of `rpc_flows_json`); AST of every HTTP-section def identical to `cea85fc` (P2-BC).

**Stay frozen (empty diff vs `cea85fc`, hashes checked by the runner):**
- The other 11 files of round-1 `freeze-final.sha256`: `lattice/gocheck/src/goextract_proto.go` (`ec4aa315...`),
  `goextract_grpc.go` (`c0cee748...`), `lattice/grpcbridge/{call.go (be517288...), describe.go (541f465e...), main.go
  (4b3f6a03...), go.mod (c943413c...), go.sum (3c6743a8...)}`, the 3 stubs
  (`stubs/google/api/{annotations,http}.proto`, `stubs/validate/validate.proto`), `lattice/importer/frontends/go_grpc.py`
  (`d22aefc6...`).
- Every file of round-1 prereg section 9's frozen list (gocheck sources, `go_gin.py`, `impact_tier2/3.py`, `ask.py`,
  `impact_report.py`, `mcp/tools.py`, `studio/**`, `pyproject.toml`, `uv.lock`, existing tests, goldens, fixtures), and
  every other round-1 additive edit (`appmodel.py`, `transport.py`, `spec/{model,check,process,review,validate,lint,...}
  .py`, `goextract.go`, `implementation.py`, `appmap.py`, `emit_spec.py`).
- Round-1 test, fixture and data files: `tests/grpc/**` existing files (N's `truth.json` `debbd0b4...`),
  `dogfood/grpc_probes.py`, `dogfood/grpc_mutants.py`, `docs/results/data/2026-10-17-grpc/**`,
  `2026-10-17-grpc-prereg.md`, `2026-10-17-grpc-results.md` (errata go in the round-2 results only).
- The 31 `deskd` files (`8670bf84...`), wherever they live (Q2).
- If a round-1 `tests/grpc` test pins the one-token behaviour or the round-1 flow schema, it is **not** edited: its
  failure is a disclosed finding counted in REG (predicted 0-2 such tests, p).

## 10. Byte-compat promise

Versus `cea85fc`: every HTTP output of round-1's promise (gocheck extract on the 13 fixtures, `go_gin` RepoModel,
`select()`, go_harness DCX and minishop CD reports, review/validate/lint on every spec, `build_report`/`build_appmap`,
impact JSON, `--help` texts, MCP `tools/list`); `gocheck extract` JSON on N, T and `deskd`; the `RepoModel` of N and
`deskd`; reconcile output on N; the HTTP workflow layer's output on its existing test fixtures. **Not byte-compatible,
declared:** `lattice.rpc-flows` (`/1 -> /2`, D2 F6) and reconcile output on apps where an order disagreement occurs.

## 11. Risks, and what this cannot show

1. **Dev-tuned rules.** F2 and F3 were chosen after reading `deskd`'s failures; PF2-DESK is tuned-on. Only HO2 tests
   them, once, on one app (n = 1).
2. **The brief tests the fix.** Section 6 tells the new author to include a sign-out and natural keys (Q7); it states
   scope, not code, but it makes HO2-REC and HO2-FLOWS partly a test of what was asked for.
3. **Edge recall depends on the author's `links`.** A missing or wrong link moves the denominator; truth flows that fail
   their replay are removed (D4) and capped at one third.
4. **RC2 compares two orders, not all orders.** State changed by a pair of rpcs that cancels in both orders goes
   unnoticed; RC3 isolates only what disagreed.
5. **Shared-library correlation** (bridge and servers on grpc-go) and interceptor-heavy auth as round 1.
6. **Cannot show:** Streak over gRPC/MySQL (Arm M), the build round (Arm L), streaming, non-Go servers, layers A/R/X/B on
   rpcs.

## 12. Blind vs post hoc, commands, file ownership

Fixed now: D1-D4, F1-F6, S1-S4, every prediction and gate, the brief. A missed prediction is a finding; fixes are
separate post-hoc changes, re-run with deltas, never re-scored as blind. The scorer (`grpc_flow_scorer.py`) and
`grpc_probes2.py --arm H2` are hashed into Amendment 1 **before** `heldout2/truth.json` is opened. **HO2 is scored once.**
A runner crash before any number is seen may be re-run once (disclosed, Q8); anything after a number is seen is post
hoc. **Pass:** every gated row of section 7, mutants 17/17 (by probes or named unit tests, disclosed), P2-BC, REG.

```sh
export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<round-2 worktree>
SCRATCH=/tmp/claude-0/-home-user-lattice/<session>/scratchpad/grpc2
export LATTICE_GOCHECK_CACHE=$SCRATCH/gocache LATTICE_GRPCBRIDGE_CACHE=$SCRATCH/bridgecache
git -C /home/user/wt/np-grpc archive cea85fc | tar -x -C "$SCRATCH/base"           # byte-compat + round-1 control
(cd "$SCRATCH/../heldout" && sha256sum freeze.sha256)                               # 8670bf84... (deskd, dev)
sha256sum tests/grpc/fixtures/sessions/truth.json tests/grpc/fixtures/deskd_links.json > "$SCRATCH/freeze-dev.sha256"
uv run python -m pytest tests/grpc -q
uv run python dogfood/grpc_probes2.py --arm 0 --base "$SCRATCH/base" --scratch "$SCRATCH/p0" --out docs/results/data/2026-10-17-grpc-round2
uv run python dogfood/grpc_probes2.py --arm G --base "$SCRATCH/base" --deskd "$SCRATCH/../heldout/deskd" --scratch "$SCRATCH/pg" --out docs/results/data/2026-10-17-grpc-round2
uv run python dogfood/grpc_probes.py --arm 0 ...; uv run python dogfood/grpc_probes.py --arm G ...   # PR1, round-1 runner unchanged
uv run python dogfood/grpc_mutants2.py --scratch "$SCRATCH/m" $(seq -f GR%g 1 17)
sha256sum lattice/spec/grpc.py lattice/importer/workflows.py dogfood/grpc_flow_scorer.py dogfood/grpc_probes2.py > "$SCRATCH/freeze2.sha256"  # BEFORE HO2
uv run python dogfood/grpc_probes2.py --arm H2 --heldout "$SCRATCH/heldout2" --out .../heldout2   # orchestrator, once
uv run ruff check <changed .py>; uv run ruff format --check <created .py>; gofmt -l tests/grpc/fixtures/sessions
```

**File ownership (build node).** **Edits:** the two files of section 9 "may change"; this file only by appending
Amendment 1 (hashes). **Creates:** `tests/grpc/test_reconcile2.py`, `tests/grpc/test_flows2.py`,
`tests/grpc/fixtures/sessions/**` (app, protos, generated code, harness, `truth.json` hashed before reconcile code),
`tests/grpc/fixtures/deskd_links.json`, (`tests/grpc/fixtures/deskd/**` byte copy only if Q2 rules so),
`dogfood/grpc_probes2.py`, `dogfood/grpc_mutants2.py`, `dogfood/grpc_flow_scorer.py`,
`dogfood/grpc_flow_scorer_cases.json`, `docs/results/2026-10-17-grpc-round2-results.md`,
`docs/results/data/2026-10-17-grpc-round2/**`. **Not touched:** section 9's frozen list; `$SCRATCH/heldout2/` (runner
only); Streak, `job-scheduler*`, the main checkout outside the worktree. No commits by the builder; the orchestrator
stages explicit paths only (never `git add -A`). No API key, no model call anywhere in round 2.

## 13. Questions for the orchestrator

- **Q1 (D1).** Approve RC1 (fresh login per non-public call) + RC2 (forward/reverse) + RC3 (isolated rerun on
  disagreement) as the state-independence rule, with name-based skipping of destructive session rpcs rejected as the
  primary rule (additive `order_sensitive` listing only if you want it)?
- **Q2.** `deskd` as dev corpus: copy its 31 files byte-identically into `tests/grpc/fixtures/deskd/` (reproducible
  tests; the copy is frozen) or keep it in scratch and reference it by `8670bf84...` (default: scratch, by hash)?
- **Q3 (F3).** Accept the least-privilege example values, chosen after seeing `deskd`, as a disclosed dev-tuned rule?
- **Q4 (S2, HO2-FLOWS).** Bounds: verified >= 1, 0 verified-but-failing, edge recall >= 0.30, coverage >= 2 truth flows;
  and the D4 edge definition (rpc pair + bound field + consumer code). Tighter or looser?
- **Q5 (REG).** The round-1 REG miss (`ADDED_DEFAULTS` in `tests/importer/test_frontend_goldens.py`) needs an edit to a
  frozen existing test. Fix it in round 2 (one disclosed edit, REG then expects the 105 baseline ids) or leave it to a
  merge node (default: leave)?
- **Q6.** Bump to `lattice.rpc-flows/2` (round-1 output is unmerged, so no released consumer)?
- **Q7 (section 6).** May the brief name a session-revoking rpc, natural keys, a role-gated field and an ownership
  chain as required shapes (it tests the fixes directly; risk 2), or should the author get only the D1/D2 scope?
- **Q8.** Held-out rerun policy: one rerun allowed only for a runner crash before any metric is written; agreed?
- **Q9.** The round-1 results headline says PG-HO 7/7; the blind figure is 6/7 (5/7 under S1). Record the erratum only in
  the round-2 results (round-1 docs frozen), or also amend the round-1 results doc at merge?
- **Q10.** Worktree for the build: a new worktree from `spike-i-grpc` `cea85fc` (path?), with round 1 merged into
  `next-phases` first or after round 2?

## Amendment 1 (orchestrator rulings, 2026-10-17; defaults, overturnable)

Q1: approved (RC1-RC3; name-based skipping rejected as the main rule). Q2: `deskd` stays in scratch, referenced by hash 8670bf84. Q3: accepted as a disclosed post-hoc rule. Q4: accepted. Q5: already fixed on the branch (cea85fc's parent commit 'tests: list Spike I's added fields in ADDED_DEFAULTS'); not part of round 2. Q6: yes, `lattice.rpc-flows/2`. Q7: no: the new held-out author gets only the D1/D2 scope and a requirement of idiom diversity, not the specific shapes. Q8: yes, one rerun, only for a runner crash before any metric is written. Q9: the erratum (blind PG-HO 6/7, 5/7 under the new vacuity rule) is recorded in the round-2 results and in a note appended to the round-1 results doc at merge time. Q10: build from `spike-i-grpc`; round 1 merges only after round 2.
