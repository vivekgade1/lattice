# gRPC round 5 (Spike I5, "GR"): fix the scoring chain first, then the four toolshed shapes, the three build-loop defects, a gRPC Streak, and a new blind held-out: pre-registration

## 1. Status, inputs and the exact gap

2026-10-18. **Written BEFORE any round-5 code. No model was called, no API key was used.** This file is the only file this step
writes; it is committed alone (explicit path) on branch `np-grpc-round5` in worktree `/Users/vivekgade/Documents/lattice-gr`, cut
from `next-phases` `978b468` (the 2026-10-18 handoff). Nothing is pushed or merged.

Read while writing (read only): `docs/HANDOFF-2026-10-18.md` (sections 3, 5 item 3, 6); the Spike I rounds 1-4 pre-registrations and
results (`2026-10-17-grpc-{prereg,results}.md`, `2026-10-17-grpc-round2-*`, `2026-10-18-grpc-round3-*`, `2026-10-18-grpc-round4-*`); the
Arms M and L results `2026-10-18-grpc-arms-m-l-results.md` (worktree `lattice-ia`, branch `np-i-arms`, commit `828f022`, **not merged
into `next-phases`**); the plan's Spike I addendum (`docs/design/lattice-implemented-logic-spikes-plan.md`); and, for facts only:
`lattice/spec/process.py` (`ProcessApp`, `process_runtime`), `lattice/importer/transport.py` (`AuthDriver.rpc`), `lattice/spec/grpc.py`
(lines 427-450, 553, 595, 677), `lattice/spec/db.py` (the database-reference convention), `lattice/studio/rebuild.py` (`brief`,
`scope_brief`, `spec_names`, `_protected_paths`, `_restore_protected`), `lattice/mcp/guard.py` (`Guard.protected_name`),
`lattice/gocheck/src/goextract_proto.go` (custom options), `goextract_grpc.go` (`fillRPC`, `rpcWrites`), `dogfood/grpc_probes2.py:1362`
and `:869` (`_truth_replay`), `dogfood/grpc_contract4.py:97,116`, the archived toolshed truth and harness
(`archive-frozen-sets-2026-10-18/heldout4-toolshed.tgz`, development data since round 4's post hoc read), and Streak's `studio.toml` and
`harness.py` at `build/streak` (by `git show`, no write). Labels: **(m)** measured while writing, **(r)** read from code or data while
writing, **(v)** a verifier's or results document's corrected figure, everything else a **prediction (p)**.

**Environment (m), different from the handoff's container.** macOS arm64 (the owner's machine); `go` is **go1.26.2 on `PATH`** (there is
**no** `/usr/local/go/bin/go` here, the path the handoff and the toolshed harness fall back to); `uv` 0.11.7; `docker info` answers
(server 29.4.0); the `claude` CLI is at `~/.local/bin/claude`. So Arm M (both rows) and Arm L can run in this round; the runner re-measures
all of this as its first line and any arm whose tool is missing is NOT RUN (never a pass).

**Every corpus anyone has seen is development data**: `deskd`, kilnworks, harborline, **toolshed** (round 4's held-out; its `IDIOMS.md`
and extract were read post hoc in round-4 results section 10), N, T, sessions, and the round-4 fixtures. None is ever labelled blind
again. The gated held-out of round 5 is a NEW app by a NEW author (section 8); PM-SG is a second, separately authored app (section 9).

### 1.1 What stands (v, from the round-4 results and the Arms M/L results; restated, not re-scored)

| item | value | source |
|---|---|---|
| PG-HO4 (toolshed) | **0 of 9 scored, NOT RUN**: run 1 staging symlink broke `load_app`; run 2 `FAILEDPRECONDITION` (S027) in the frozen round-2 reader | round-4 results 10.1 |
| toolshed post hoc RPC | 0.500 / 0.500 (Catalog x5, Members x4 `unread`; Invoice x2 `unread` by D1.3) | 10.3 B |
| toolshed post hoc SAFE | **1** confident-wrong: `SessionService/Login` `Member`, truth `Public` | 10.3 B |
| toolshed post hoc CODES | recall 0.0704 (5 of 71), precision 1.000 (after name normalisation done by hand in the verifier's script) | 10.3 B |
| toolshed post hoc AUTH | 1 wrong, unclear 9 of 19 = 0.474 | 10.3 B |
| toolshed post hoc TABLES / REC | 1.000 via `tables_source: harness_db` / coverage 1.00, agreement 1.00 (with `build_db` patched to return the path) | 10.3 B |
| toolshed post hoc FLOWS | truth flows replaying **0 of 10** (`truth_not_scorable`: the replay ignores `links`) | 10.3 A-B |
| round-4 dev stage | Arm 0 11/12 (REG), Arm G 7/8 (PR3-ALL), mutants 61/62 (GW4-9 survives: the D1.4 adapter clause changes no state) | 1, 5 |
| `goextract_grpc.go` | **749 of 400** lines changed in round 4 (budget missed 1.9x); 2326 lines at `978b468` (m) | 1.1 |
| PM-S (Streak static byte-compat) | **18 of 18 identical** vs `git archive 6a318e5`, `select()` = `gin`, no Docker needed | Arms M/L 2 |
| PM-SG | **NOT RUN**: no grpc-go Streak, no gRPC spec, no truth, no harness, no scorer arm exist | Arms M/L 3 |
| Arm L (toy proto build) | run 1 MISS (false `spec_edited: proto` every round), run 2 PASS in 1 round, USD 0.08 | Arms M/L 4 |

### 1.2 Root causes, corrected (r and v)

**Process and scoring chain (none is in the extractor):**
- **P1 Harness API document.** `HARNESS_API.md` (round-4 Appendix A) said `RUNTIME` is "built with" `AuthDriver.rpc(...)`; it omitted
  `process_runtime` (contract-check rounds 0-1 crashed) and the `build_db` return convention (the delivered `build_db` returned `None`;
  `make_app(None)` -> `ProcessApp(db_path='None')` -> an empty database named `None`; 6 of 6 logins failed). Round-4 Appendix A also
  promised the builder would paste the exact `make_app` return type and `process_runtime` signature "in Amendment 1"; **that never
  happened**. (r) The library is itself inconsistent: `check()` follows `db.py`'s convention (the harness's `app_factory` receives the
  **reference it passed** to `db_factory`; the return value is not used), while `grpc.py:553/595/677` uses `make_app(build_db(path))`.
- **P2 Code names.** The frozen round-2 reader does `str(code).upper()` (`grpc_probes2.py:1362`); the toolshed truth (as its brief
  allowed) used grpc-go `String()` names (`FailedPrecondition`), giving `FAILEDPRECONDITION`, which `validate` refuses (S027). Note (r):
  grpc-go's `codes.Canceled.String()` is `Canceled`, while the `google.rpc.Code` name is `CANCELLED`; no rule-of-case conversion is
  correct, only a table.
- **P3 Links.** `grpc_contract4.py` and round 2's `_truth_replay` build `Rpc(..., s["message"])` and never apply `links`, so every
  link-bearing truth flow fails at its first linked step: FLOWS is not scorable for any such truth (and was not on harborline in round 3
  either; round 3's attribution to login alone was incomplete). The contract check's failure handler also reads `finding.message`, an
  attribute `CheckFinding` lacks. (r) Link forms differ between corpora: `deskd_links.json` uses `{step, field, from_step, from_field}`
  1-based; toolshed uses `{from_step, from_field, to_step, to_field}` with `conventions.step_index_base = 0`.
- **P4 Ledger.** The run ledger lives in `--out`; a second `--out` restarts the count; the run count is auditable only by listing
  directories.
- **P5 Layout.** The runner wanted `<heldout>/freeze.sha256` plus exactly one app directory; the author put `freeze.sha256` in the app;
  the orchestrator's `app -> .` symlink staging broke `p3.load_app` (`resolve()`).
- **P6 Rehearsal.** The H4 arm was exercised only on a stand-in "no app" directory (`--process-test`); it was never run end to end on a
  real-shaped held-out with the brief's truth schema before the freeze, so P2/P3/P5 surfaced only on the scored run.

**Extractor (toolshed post hoc attribution, round-4 results 10.4; r):**
- **X1** Catalog: a function returns a **decorated copy** of the generated `ServiceDesc` (handlers wrapped), passed to
  `RegisterService`: descriptor unresolved (`wire.go:53`), 5 rpcs `unread`, and through D1.3 every unregistered declared service `unread`.
- **X2** Members: a variadic option `WithService(desc, impl)` is **consumed by a builder** that registers while constructing: unresolved
  (`builder.go:57`), 4 rpcs `unread`. X1 + X2 = the 9 `unread` rpcs; with both resolved, D1.3 releases Invoice to `not_registered`.
- **X3** Booking: an error type with a `GRPCStatus() *status.Status` method (round-4 D3.4, optional, **not built**): 0 of 29 booking codes.
  Also seen: harborline Berths (same shape), harborline Vessels (a wrapper whose code is a parameter), toolshed Members
  (`status.New(c, ..).WithDetails(..).Err()` through a helper), toolshed Equipment (a translator inside the decorated descriptor, not an
  interceptor). (m) 15 of toolshed's 71 truth codes are interceptor codes (`interceptor_codes`, `Unauthenticated`), and the extractor
  attributes none of them to rpcs although it reads the interceptor.
- **X4** Auth audience from a **custom proto method option** read at call time (`g.audience(fullMethod) == AUDIENCE_ANONYMOUS`): the gate
  was classed `auth_literal` **with an empty allowlist**, so Login read `Member`: the round's only confident-wrong answer. The defect is
  in the safety rule itself: an exemption mechanism that is not a string allowlist was not routed to `unclear`. (r) `goextract_proto.go`
  records every custom option as `unread: custom_option` and keeps no value.
- **X5** Flows: route-level tables come only from GORM chains (`rpcWrites`, `ci.tables`); there is **no `database/sql` reader anywhere in
  `lattice/gocheck/src`** (r: no SQL-statement parser in any file). Harborline and toolshed handlers use `database/sql`, so no rpc has a
  table, and the frozen flow rules bind nothing (harborline recovered 0; toolshed 0).

**Build loop (Arms M/L section 4; r):**
- **L1** `Rebuilder._restore_protected` compares each `[mcp] protected` entry as a file (`git cat-file -p base:<p>` of a directory is a
  tree listing; `is_file()` is false on the worktree side), so a directory entry reads as edited in every round. `spec_names()` documents
  "a directory means the files under it". (r) The MCP hook has the same gap: `Guard.protected_name` matches a protected path exactly, so a
  write to `proto/x.proto` under a protected `proto` directory is not denied.
- **L2** `studio.toml [grpc] generate` is read by nothing (`grep` of `lattice/` finds no reader of a `[grpc]` table); the toy fixture's
  `check.sh` ran the generator itself.
- **L3** The rebuild brief (`brief`, `scope_brief`) names REQUIREMENTS.md, spec.py and spec.trace.json only; it never names the `.proto`
  files; "never the proto" held only through `[mcp] protected`.

### 1.3 Scope (binding; built in this order, each with a gate)

**Stage A, fix first (scoring chain and harness), in the handoff's order:** (A1) the harness API document, complete (Appendix A), plus a
3-line library consistency fix (H1b, Q3); (A2) code-name normalisation by table in one reader used by the whole runner chain; (A3) `links`
in the truth-flow replay (and the dotted `expect` paths); (A4) a global, hash-chained per-run ledger; plus (A5) the layout rule and (A6) a
contract check whose printout names the failing fact, both required to make A1-A4 effective (P5, P6).

**Stage B, extractor and library:** (S) a behaviour-neutral split of `goextract_grpc.go` and re-registered size budgets (section 3);
(E1) decorated `ServiceDesc` copies; (E2) builder-consumed registration options; (E3) the `GRPCStatus()` error type and the two other
status-construction forms, plus interceptor-code attribution (E3d, an addition beyond the handoff list: Q5); (E4) the proto-option auth
audience, safety rule first; (E5) route-level tables and writes from `database/sql` statements; (L1) directory entries in protected paths
(rebuild and MCP guard); (L2) `[grpc] generate` executed by the loop; (L3) the brief names the protos and the generator.

**Stage M:** PM-S5 (Streak static byte-compat at round-5 code) and PM-SG (a grpc-go Streak with its gRPC spec and truth, written by a
separate author after the freeze). **Stage L:** Arm L re-run on the toy proto with the round-4 failing configuration. **Stage H:** a NEW
blind held-out gRPC Go app, scored once.

**Out of scope:** streaming (stays `not supported`); layers A/R/X/B over rpcs; flow-rule changes (`workflows.py` stays frozen); the bridge;
non-Go servers; `.NET`; reading `go:embed` SQL files for DDL (Q12); the D1.4 adapter clause's removal (Q13); harborline's receiver-indexed
code map (Yard) and its module-wide `nodef` default (Bookings).

## 2. Stage A decisions (fixed now)

**H1 `HARNESS_API.md`, complete (Appendix A, copied verbatim to every author; the only library text an author sees).** It states, with the
exact signatures read from `978b468`: `studio.toml` keys; `USERS`; `RUNTIME = process_runtime(framework="go", auth=AuthDriver.rpc(...))`
(and that `RUNTIME` must be the `process_runtime` result, never the driver); every `ProcessApp` field a gRPC app uses; `build_db(path)`
**returns `path`**; `make_app(db_path)` receives what `build_db` returned and returns a `ProcessApp`; the gRPC readiness, proto and port
fields; the `go` binary via `shutil.which("go")`; the MySQL variant (for PM-SG); the `Rpc` step (for PM-SG's spec). A skeleton harness
(no server code) is included. **Gate P5-HAPI**: a builder-written fixture harness made only from Appendix A's text passes the contract
check (H6), and the three round-4 failure classes (RUNTIME = driver; `build_db` returning `None`; positional `AuthDriver.rpc`) each give a
printout line naming the fact.
- **H1b (library consistency, Q3, default accepted).** In `lattice/spec/grpc.py` at the three sites, `ref = build_db(p)` then
  `make_app(p if ref is None else ref)` (and the same `ref` for the database opened afterwards): `None` means "the path I was given",
  exactly `check()`'s convention (`db.py`). A harness that returns its path is byte-unchanged. Budget <= 6 lines.

**H2 Code names (`dogfood/grpc_truth5.py`, `normalise_code`).** One fixed 17-row table: each `google.rpc.Code` name maps from its
UPPER_SNAKE name, its grpc-go `String()` name (`OK`, `Canceled`, `Unknown`, `InvalidArgument`, `DeadlineExceeded`, `NotFound`,
`AlreadyExists`, `PermissionDenied`, `ResourceExhausted`, `FailedPrecondition`, `Aborted`, `OutOfRange`, `Unimplemented`, `Internal`,
`Unavailable`, `DataLoss`, `Unauthenticated`), the British `CANCELLED` and American `CANCELED` spellings, and the integer 0-16 (or its
decimal string). Matching is exact on those spellings, case-insensitive only for UPPER_SNAKE; anything else raises `TruthError
("unknown gRPC code <x> at <json path>")` **in the contract check, before any manifest**. Applied to every code the chain reads: per-rpc
`codes`, `code_sites[*].code`, `interceptor_codes`, flow `expect.code`. The reader records `codes_normalised: <n>` in its output. The
truth schema /5 (Appendix B) requires UPPER_SNAKE names; the table is defence in depth, not a licence.

**H3 Links and expectations in the truth replay (`grpc_truth5.replay_flow`).** One link form is canonical, `{from_step, from_field,
to_step, to_field}`, indices in `conventions.step_index_base` (0 or 1, required in /5); the reader also accepts deskd's `{step, field,
from_step, from_field}` (1-based) as an alias for dev data. Replay of one flow, on a fresh database built through the harness, one process:
for each step in order, the request is the step's message with every link whose `to_step` is this step applied (`from_field` read as a
dotted path from that earlier step's **response**, numeric segments index lists; `to_field` set as a dotted path in the request, creating
objects), then sent through the library's `GrpcTransport` as the step's `as` handle (token from `RUNTIME.auth`; `null` sends none). A step
passes when the code (H2) equals `expect.code` and every other `expect` key (a dotted response path; final `#` = list length) equals its
value, with int64 accepted as a JSON number or its decimal string. A missing `from_field` value fails that step as `link_unresolved`
(never sends an empty field). Result per flow: `ok`, or the first failing step index and reason class. The recorded concrete steps
(links substituted) are also returned as `Rpc` steps for layer D. Used, byte for byte, by the contract check, the runner and the scorer.

**H4 Global ledger (`grpc_truth5.Ledger`).** One append-only JSONL file per round at a path fixed in the runner's bytes and resolved from
its own location, `docs/results/data/2026-10-18-grpc-round5/ledger.jsonl`; **no flag or `--out` can move it** (only `--process-test`
and `--rehearse` use a scratch ledger, and they refuse the real one). Every invocation of `--arm H5` or `--arm SG` appends a line before
anything else: `{seq, utc, arm, runner_sha256, scorer_sha256, truth5_sha256, target_freeze_sha256, out, status}` with `status` one of
`refused_preflight` (consumes no run), `started`, then `crashed_before_metric` / `scored` appended as a second line; each line carries
`prev_sha256` (sha256 of the previous line's bytes), and the runner refuses to start if the chain does not verify. The run index of a
target = 1 + the number of earlier `started` lines with the same `arm` and `target_freeze_sha256`, **whatever `--out` or runner hash**. The
scorer (S12) reads the run index from the ledger, never from the runner's output. The orchestrator commits the ledger (explicit path)
after every invocation and records its last `prev_sha256` in the next Amendment.

**H5 Layout.** `--app <dir>`: the app directory itself, `freeze.sha256` at its root, the paths in it relative to it, checked with
`sha256sum --check` semantics. Symlinks anywhere in the app tree, or an app given through a symlink, are refused in preflight
(`refused_preflight`, no run consumed). No staging copy is ever made; the extractor, harness loader and replay all use the same
`Path(app).resolve()`.

**H6 Contract check v5 (`dogfood/grpc_contract5.py`).** Runs in a fresh `python -I` process on the finished app, before its manifest,
and prints only these lines (no traceback, no extractor output, no reconcile, no flow recovery): `truth schema ok|<first error>`; `codes ok
(<n> normalised)|<TruthError>`; `import ok|<ExceptionClass>: <message>`; `RUNTIME is process_runtime ok|is <type>`; `build_db returned
the path ok|returned <repr type>`; `make_app returned ProcessApp ok|returned <type>`; `ProcessApp ready=<v> protos=<n> exist ok|<missing
file>`; `server started ok|<exit code, last 5 stderr lines of the app process>`; per handle `login <handle> ok|<code> <server message>`; per
flow `flow <name> ok|step <i>: <reason class> (<code> vs <expected>)`. Fixes `finding.message`. An app passes when every line is `ok`.
Q4 decides whether the author may run it itself (default: yes, from a frozen wheel in its scratch tree, section 8.1).

**H7 Rehearsal (closes P6).** Before the freeze the runner's `--arm H5 --rehearse` and `--arm SG --rehearse` run end to end, with a
scratch ledger, on (a) the frozen toolshed bytes as delivered (truth /4: CamelCase codes, 0-based `to_step` links, `build_db` returning
`None`, freeze inside the app; `--arm H5` only) and (b) the builder-written truth-/5 fixture apps (`harness5/sqlite` for `--arm H5`,
`harness5/mysql` for `--arm SG`); each must produce every row with a value or a named
NOT-SUPPORTED, no crash. (a) is a dev row (PD5-TOOL) and is post hoc by construction.

## 3. Stage B (S): the split and the size budget, decided

Decision: **re-split, then re-register.** Round 4's 749-of-400 miss is reported as a miss and not re-litigated. Step 0 of the build is a
**pure move** of `lattice/gocheck/src/goextract_grpc.go` (2326 lines, m) into three files of the same package: `goextract_grpc.go`
(contract, walk, registrations, `fillRPC`), `goextract_grpc_auth.go` (interceptor classification, allowlists, builder chains, method
sets), `goextract_grpc_codes.go` (errors, translators, sentinels, code maps). Gate **P5-SPLIT**: the multiset of top-level declarations'
source text is identical before and after (a `go/ast` comparison, comments included), `gofmt -l` clean, and `gocheck extract` JSON
byte-identical on N, T, sessions, deskd, kilnworks, harborline, toolshed and every `tests/grpc/fixtures/**` module; the split is its own
commit by the orchestrator (`S0`), and every round-5 budget below is measured against `S0` (`git diff --numstat S0`, insertions +
deletions). No file of the three may exceed 1200 lines at the end of the round (structural gate).

Re-registered budgets, costed per decision from round 4's measured unit costs (rule-plus-machinery, not rule only):

| file | decisions | predicted (p) | budget (gate) |
|---|---|---|---|
| `goextract_grpc*.go` (3 files, total) | E1 70-140, E2 50-110, E3a-c 90-170, E3d 25-60, E4a 20-40, E4b 70-130 | 325-650 (point 520) | **<= 650** |
| `goextract_sqlraw.go` (new) | E5 | 150-280 lines (point 210) | **<= 300 lines** |
| `goextract_proto.go` | E4b: keep rpc-level custom option values | 10-35 (point 20) | **<= 40** |
| `lattice/importer/frontends/go_grpc.py` | pass-through only if needed | 0-15 | **<= 20** |
| `lattice/spec/grpc.py` | H1b | 3-6 | **<= 6** |
| `lattice/studio/rebuild.py` | L1, L2, L3 | 40-90 (point 60) | **<= 90** |
| `lattice/mcp/guard.py` | L1b (Q6) | 10-30 | **<= 30** |

A budget miss is a gated FAIL (row P5-BUDGET), recorded with its hunks; a decision is never traded away to meet a number.

## 4. Stage B extractor decisions (fixed before coding)

D1-D6 of round 4 stand (D1.3 included: Q3 of round 4). Every new reachability stays inside `grpcWalk`; `g.wired`, `isWired` and every
HTTP path stay byte-identical.

**E1 Decorated descriptors.**
- E1.1 A descriptor argument that is the result of a call to a module function `F` (or a method) is resolved when every return of `F`
  is (a) `&pkg.X_ServiceDesc` / `pkg.X_ServiceDesc`, or (b) a local `d` initialised as a **value copy** of exactly one generated
  `X_ServiceDesc` (`d := pkg.X_ServiceDesc`, `var d = *&pkg.X_ServiceDesc`), possibly modified, returned as `d` or `&d`. Depth <= 4
  through further functions.
- E1.2 The service of (b) is X's unless `d.ServiceName` is assigned: a string literal or D2.3 constant gives that name; anything else is
  `unread: descriptor_name_not_literal` (never X's).
- E1.3 The rpc set of (b) is X's generated method set when `d.Methods` is untouched or rebuilt only by ranging over the original's
  `Methods` and appending one element per element (`MethodName` copied); a `Methods` literal built otherwise gives its literal
  `MethodName`s; anything else: the service's rpcs `unread: descriptor_methods_not_literal`.
- E1.4 A function assigned to `Handler` of each copied `MethodDesc` (the wrapper) is a function **every result of that service's handlers
  passes through**: D3.1's translator rule applies to it, for that service only (closes toolshed Equipment's `translate()`).

**E2 Builder-consumed registration options.** An option function whose returned closure stores its arguments in a field of the builder's
config (append of a struct or pair to a slice; a map set), and a constructor or method that ranges over that field and calls
`RegisterService(e.desc, e.impl)` / `Register<X>Server(r, e.impl)` (directly or depth <= 4), resolves **each grpc-wired call site of the
option function** as one registration: descriptor by `followDesc` (+ E1), impl by D1.2. The field-flow helper of round-4 D2.2 is reused,
not duplicated. A stored entry whose argument cannot be resolved keeps the registration unresolved (D1.3 then applies as today). An option
whose stored field no registering builder ranges over registers nothing.

**E3 Status construction and interceptor codes.**
- E3a (round-4 D3.4) A module type `T` with method `GRPCStatus() *status.Status`: a handler return of `T`, `*T`, `&T{...}` or a module
  constructor returning one gets the code of `GRPCStatus`: a literal `status.New(codes.X, ...)` / `status.Newf` in every return -> the union
  of those literals; `status.New(t.f, ...)` with `f` a field -> the literal assigned to `f` **at that return site's** composite literal
  or constructor argument (D3.2's per-site rule), union over sites reaching that handler; otherwise `unread: grpc_code_not_literal`.
  `fmt.Errorf("%w", t)` wrapping keeps it (D3.1's `%w` rule).
- E3b A module function whose every return is `status.Error(c, ...)` / `status.Errorf(c, ...)` / `status.New(c, ...).Err()` with `c` a
  parameter takes the code from each call site's argument (constant at the site, or a parameter of the caller passed a constant at every
  grpc-wired site, depth <= 4).
- E3c `status.New(<code>, ...).WithDetails(...)` with or without the `(s, err)` split, then `.Err()`, is read as `<code>` (the
  `WithDetails` error branch adds nothing).
- E3d **(addition, Q5)** An interceptor classed `auth_literal` (or resolved by E4b) whose refusal returns a literal code (`status.Error
  (codes.Unauthenticated, ...)`, E3a-c) adds that code with `kind: "interceptor"` and the interceptor's `file:line` to **every rpc it
  gates and does not admit** (not to rpcs it classes Public). An interceptor classed `unclear` adds nothing.

**E4 Auth audience from a proto method option.**
- E4a **Safety first (fixes the confident wrong).** `auth_literal` requires that every condition under which the interceptor skips its
  token check was enumerated as a literal method set (D2.3/D2.4) or is absent. If any skip condition reads a value the extractor did not
  resolve to a literal set (a call result, a registry lookup, a map with non-literal keys, an option read not covered by E4b), the
  interceptor is `auth_not_literal` and every rpc it gates is `unclear`. An **empty** allowlist is only literal when the interceptor has
  no skip condition at all. A `Member` produced by an interceptor therefore always implies a fully enumerated bypass.
- E4b **Reading the option.** A skip condition of the form `proto.GetExtension(<m>.Options(), <pkg>.E_<X>) ==|!=|case <pkg>.<Enum>_<V>`
  (`<m>` a `protoreflect.MethodDescriptor` obtained from `info.FullMethod` through `protoregistry.GlobalFiles.FindDescriptorByName`, a type
  assertion, or a helper depth <= 4 returning the value or a bool) resolves to: the extension's full name from the generated `.pb.go`
  (`protoimpl.ExtensionInfo{... Name: "<full>" ...}` literal behind `E_<X>`), the value name `<V>` from the generated enum constant. Per
  rpc, the proto reader's recorded value of `(<full>)` on that rpc decides; an rpc without the option takes the enum's zero value; an rpc
  whose option value the reader could not record is `unclear`.
- E4c `goextract_proto.go`: an rpc-level custom option whose value is a scalar or an enum identifier is recorded as `custom_options:
  {"(<full name>)": <value>}` on that rpc (key omitted when empty); aggregate values stay `unread: custom_option` as today. File- and
  message-level options are unchanged.

**E5 `database/sql` tables and writes (`goextract_sqlraw.go`, called only from the gRPC walk's `fillRPC`).**
- E5.1 Calls: `Exec`, `ExecContext`, `Query`, `QueryContext`, `QueryRow`, `QueryRowContext`, `Prepare`, `PrepareContext` on a receiver
  whose static type is `*sql.DB`, `*sql.Tx` or `*sql.Conn`, or a module interface whose method of that name takes a `string` query (the
  sqlc-style `DBTX`), inside the rpc closure's visited bodies; an interface-typed store reached from a handler is followed by D1.4's
  implementer rule (depth <= 4), for table reading only.
- E5.2 SQL text: a string literal, a constant, a `+` of those, or `fmt.Sprintf` with a constant format whose table positions are literal;
  a table name from `%s`/`%v` or a non-constant is `unread: sql_not_literal` (file:line); no guess.
- E5.3 Tables: identifiers after `FROM`, `JOIN`, `INTO`, `UPDATE`, `DELETE FROM` (quoted forms and `schema.` prefixes stripped, CTE names
  excluded, subqueries scanned), case-folded; added to the rpc's `tables`. Writes: `INSERT` -> `create`, `UPDATE` -> `update`, `DELETE`
  -> `delete`, `INSERT ... ON CONFLICT ... DO UPDATE` / `REPLACE` / `ON DUPLICATE KEY UPDATE` -> `save`, emitted in the existing `writes`
  shape (`table`, `op`, `file`, `line`) so the frozen flow rules consume them unchanged. DDL statements are ignored here (D5 stands).
- E5.4 The HTTP walk never calls the reader (byte-compat; mutant GS5-4).

## 5. Stage B library decisions (build loop; fixed before coding)

- **L1 Directory entries.** In `_restore_protected`, an entry that is a tree at the base commit is compared file by file: every file of
  `git ls-tree -r base:<dir>` must be byte-equal in the worktree, and a non-ignored file under `<dir>` absent at the base counts as an
  edit; any difference names `<dir>/<file>` in `edited`, restores the tree (`git checkout base -- <dir>`) and removes the added files.
  Git-ignored files are left (generated stubs). A file entry behaves as today.
- **L1b (Q6, default accepted)** `Guard.protected_name`: a resolved path **under** a protected directory is protected (named by the
  directory entry); exact-file behaviour unchanged.
- **L2 `[grpc] generate`.** `Rebuilder` reads `studio.toml [grpc] generate` (a command string) and `[grpc] protos` (list). When
  `generate` is set it runs it in the worktree, with the gates' environment and timeout, **after each agent round and before the gates**;
  a non-zero exit is a gate failure with finding `studio:grpc_generate_failed` (command and output tail). Generated files are whatever the
  command writes; an agent edit to them is overwritten by the run. Without `[grpc]`, behaviour and the brief are byte-identical.
- **L3 Brief.** With `[grpc] protos` set, `brief` and `scope_brief` add one section: "The contract also includes these .proto files
  (never edit them): <list>. Generated stubs come only from `<generate>`, which Studio runs before the gates; do not hand-edit generated
  files." The listed protos are added to the protected paths implicitly (L1 applies to them).

## 6. Never

No network beyond loopback (and the Go proxy for builds); Docker only for PM-SG's MySQL (label `lattice.owner=w-gr`, removed after); no
write under any target root, `~/Documents/streak` or the main checkout; no git on a target; no model or API key in extraction, reconcile,
flows, scorer, runner, contract check or ledger; model use only through the `claude` CLI (Arm L, the two authors, the verifier); no edit to
any corpus's `.proto`, spec, truth or harness; no edit to a frozen file (section 12); no flow-rule change; no runner patch that maps a
harness's arguments; the held-out and PM-SG scored once each; no self-hash exception of any kind.

## 7. Scorer rules (`dogfood/grpc_scorer5.py`, pure; imports the round-4 scoring functions unchanged)

S1-S9 as rounds 3-4 (S7: 0 wrong and unclear <= 0.50, a missing rpc counts `unclear`; S8 SAFE confident-wrong directions; S9 an `UNKNOWN`
not in the truth is an extra, an `unread` code is neither). New:
- **S10'** Provenance as round 4's S10, but the run index comes from the ledger (S12) and the hashes from this file's Amendment 2.
- **S11** Every truth code passes H2; the count normalised is reported per row. A code that fails H2 makes the row NOT RUN (it cannot
  happen after a passing contract check).
- **S12** Run index and chain from `ledger.jsonl` (H4); a broken chain labels every row `post_hoc` with reason `ledger_chain_broken`.
- **S13** FLOWS uses round 2's `grpc_flow_scorer.py` unchanged for recovered/verified/edges/coverage, with `truth_flows_passing` computed
  by H3's replay (links applied) instead of round 2's `_truth_replay`.
- **S14 SQLT (reported, never gated):** per-rpc table recall / precision against truth `tables[*].read_by` + `written_by`, case-folded.
- Cases `dogfood/grpc_scorer5_cases.json`: round 4's 58 unchanged plus >= 20 new (S11 each spelling class and a refusal, S12 index across
  two `--out`, a refused preflight not counted, a broken chain, S13 a linked flow that only passes with links, S14).

## 8. The new held-out (HO5)

### 8.1 Author and isolation

One NEW separate agent (not any earlier author, builder, verifier, this author or the orchestrator), run through the `claude` CLI, opus,
dispatched only **after** the round-5 freeze commit (Amendment 2 records the commit id and time; the dispatch time must be later). Its
working directory is a fresh `$SCRATCH5/author-ho5/` containing only `BRIEF.md` (Appendix C verbatim), `HARNESS_API.md` (Appendix A
verbatim), `TRUTH_SCHEMA.md` (Appendix B verbatim) and, if Q4 is accepted, `.venv/` with a frozen wheel of the round-5 tree installed
and one console command `contract5 <app-dir>` (H6). It must not read `/Users/vivekgade/Documents/lattice*`, any `docs/results/*grpc*`,
`lattice/**` sources (including `.venv/**/site-packages/lattice/**`), `dogfood/**`, `tests/**`, any `heldout*` or archive tarball,
`~/Documents/streak`, or the orchestrator's notes. Enforcement: the fixed directory, then an audit of the author's full tool-call
transcript (any path or command naming the patterns above, or reading `site-packages/lattice`) before the manifest; one hit discards the app
and a new author is commissioned (disclosed; the discarded app is never scored). It may use the Go module proxy and public grpc-go docs.

### 8.2 Brief (Appendix C) and the diversity floor

Shape minimums as round 4 (>= 3 registered services, >= 15 unary rpcs, >= 1 `Unimplemented` rpc, >= 1 streaming rpc, >= 1 declared but
unregistered service, >= 2 users of different roles, >= 2 request fields naming a seeded row by a natural key, >= 6 truth flows of >= 3
steps with links, >= 1 login rpc), plus: the author runs its own Go replay (`cmd/replay`) of every truth flow on a fresh database and on
one shared database, and (if Q4) `contract5` until every line is `ok`. **The brief names no idiom**: none of the words or shapes in
round 4's forbidden list, nor descriptors, copies, decorators, builders, options, status types, `GRPCStatus`, `WithDetails`, proto
options, extensions, audiences, `database/sql`, SQL, repositories, migrations, embedding, or "what failed before". It keeps round 4's
neutral sentences ("organise the server the way a team maintaining a mid-sized production service would ... no two services may be wired
into the server by the same code idiom; no two services may report errors by the same idiom; the authentication decision is written in an
idiom of your own choice; put the database schema wherever such a team would; do not copy an example from any document"). Sealed
`IDIOMS.md` after code and truth; diversity floor (>= 3 distinct wiring lines for >= 3 services, >= 3 distinct error lines) checked by
the orchestrator on `IDIOMS.md` alone; `IDIOMS.md` is read by nobody else until HO5 is scored.

### 8.3 Contract check, manifest, order, re-runs (binding)

1. Build done; P5-REH (H7) passes; `freeze5.sha256` of every section-12 "may change" file plus `grpc_truth5.py`, `grpc_probes5.py`,
   `grpc_scorer5.py`, its cases and `grpc_contract5.py` is committed; **Amendment 2** records those hashes, the S0 commit, the freeze commit
   and the ledger's (empty) state, **before** either author is dispatched.
2. Author dispatched; writes the app with `freeze.sha256` at its root and `FILES.txt`; audit runs.
3. The orchestrator runs `grpc_contract5.py` once; on a failing line the author gets only that printout (at most 2 fix rounds, each
   disclosed). Still failing after 2: the app is used as is and the failure recorded as a predicted cause before scoring.
4. **Amendment 3** records the app's `freeze.sha256` sha256, the audit (calls, hits), the contract printout(s), the diversity check.
5. `grpc_probes5.py --arm H5 --app <dir>` checks its own sha256 (never special-cased), the scorer's, `grpc_truth5.py`'s, the contract's,
   `freeze5.sha256`, the app freeze and the ledger chain, appends `started`, runs once. **One re-run, only after a crash before any metric
   with unchanged bytes** (ledger `crashed_before_metric`); a fix needing a byte change is recorded in an Amendment 4 first and labels
   every row `post_hoc`; no third run; a crash on the re-run is NOT RUN for every row.
6. No library byte changes after step 1. Any change after step 4 marks affected rows `post_hoc`, reported beside the first value.

## 9. PM-SG: a grpc-go Streak (Stage M)

**Author.** A separate agent, distinct from the HO5 author, opus via the `claude` CLI, dispatched after Amendment 2 (it may run in
parallel with the HO5 author: max 4 agents at once). Directory `$SCRATCH5/author-sg/` with `BRIEF-SG.md` (Appendix D), `HARNESS_API.md`
(Appendix A, including A.5 MySQL), `TRUTH_SCHEMA.md`, `contract5` (if Q4), and a read-only export of Streak's behaviour:
`git -C ~/Documents/streak archive a0679e9 REQUIREMENTS.md spec.py CONTEXT.md` (Q11: whether the Go source is also given; default **no**,
so the app is a reproduction of behaviour, not a port of code). Same isolation and audit as 8.1, plus no access to `~/Documents/streak`
itself.

**Deliverable.** A grpc-go server reproducing Streak's 24-endpoint behaviour as rpcs over MySQL 8.4 (Streak's database), its `.proto`, a
gRPC form of Streak's spec (`spec.py` with `Rpc` scenarios for the rules it can express; a rule it cannot express listed in
`NOT_EXPRESSED.md`), a harness per Appendix A.5, `truth.json` (/5), a Go replay, a sealed `IDIOMS.md`. No diversity floor (the brief asks
for a natural implementation).

**Database (Q10).** Default: MySQL through Lattice's `dbserver` (`[app] database = "mysql"`), as Streak. (r) `reconcile_with` and
`run_rpc_flows` build SQLite paths (`grpc.py:553/595/677`), so on MySQL **SG-REC and SG-FLOWS are NOT SUPPORTED** this round (a library gap
recorded, not a failure); D5.2 reads SQLite schemas only (`db_facts`), so SG-TABLES depends on DDL being literal in Go source. The
alternative (SQLite) runs every row but measures a Streak that does not exist.

**Scoring** (`--arm SG`, once, same ledger and re-run rules as 8.3, Amendment 3-SG): SG-RPC, SG-SAFE, SG-CODES, SG-AUTH, SG-TABLES at
HO5's thresholds; SG-CD (the author's gRPC spec, layers C and D, on its own app on MySQL: a validity check of the author's work, reported);
SG-DET, SG-NW; SG-SQLT reported.

## 10. Probes and predictions (runner `dogfood/grpc_probes5.py`; numeric pass rules; every arm implemented before the freeze)

### 10.1 Arm 0 (static, gated)

| id | probe | prediction (p) | pass |
|---|---|---|---|
| P5-SPLIT | section 3 pure move | identical decls; extract byte-identical on 7 corpora + all fixtures | **exact** |
| P5-BC | byte-compat promise (section 13) vs `git archive 978b468` | HTTP 73/73 identical; declared gRPC differences only | **all** |
| P5-HAPI | two fixture harnesses written from Appendix A's text only (`harness5/sqlite`, `harness5/mysql` per A.5, Docker) pass H6; the 3 round-4 failure classes each give a named line | 3/3 named; both fixtures all `ok` | **exact** |
| P5-CODE | H2 table: 17 x (UPPER_SNAKE, String(), int, digit string) + both CANCEL spellings + 6 refusals | all mapped, 6 refused | **exact** |
| P5-LINKS | H3 on: deskd (links file, 1-based alias), toolshed truth /4 (0-based), `truth5/` fixture (a flow that passes only with links; a `link_unresolved`; a `#` length; an int64 as string) | every flow as its fixture truth states | **exact** |
| P5-LEDGER | H4: two `--out` on one target -> index 2; third refused; preflight refusal consumes none; tampered line -> refused; `--process-test` refuses the real ledger | 5/5 | **5/5** |
| P5-DESC | fixture `descwrap5/`: value copy, pointer return, ServiceName literal, ServiceName non-literal (unread), Methods rebuilt by range, wrapper translator | each state/code as truth | **exact; 0 S8** |
| P5-BOPT | fixture `builderopt5/`: two option calls, an option whose field no builder ranges (negative), an unresolvable stored impl (D1.3) | exact states | **exact; 0 S8** |
| P5-STATUS | fixture `status5/`: GRPCStatus literal, GRPCStatus field per site, `%w` of it, parameter-code wrapper, WithDetails (both forms), untraced | exact codes, untraced `unread`, 0 UNKNOWN extras | **exact** |
| P5-ICODE | E3d: literal gate code on gated rpcs only; unclear gate adds none | exact | **exact** |
| P5-AUD | fixture `audience5/`: option-based gate (public enum value, other value, absent option = zero value, aggregate value = unclear), an unrecognised skip condition (unclear, never Member), the round-4 empty-allowlist shape | exact; 0 Member behind an unenumerated bypass | **exact; 0 S8** |
| P5-SQL | fixture `sqlraw5/`: literal, constant, concat, Sprintf literal, Sprintf `%s` (unread), `*sql.Tx`, DBTX interface, store reached by interface, CTE, upsert | exact tables/writes | **exact** |
| P5-PROT | L1/L1b unit fixtures: directory entry unchanged (no edit), file changed under it, file added under it (removed), ignored file kept, guard denies a path under the directory | 5/5 | **5/5** |
| P5-GEN | L2: generate runs after the agent and before the gates; failing generate is a named gate failure; no `[grpc]` byte-identical brief and gates | 3/3 | **3/3** |
| P5-BRIEF | L3: brief and scope brief name protos and the generator; protos implicitly protected | exact text | **exact** |
| P5-REH | H7 rehearsal on toolshed as delivered and on the truth-/5 fixture app, `--arm H5` and `--arm SG` | every row valued or NOT-SUPPORTED, 0 crashes | **0 crashes** |
| P5-ARMS | every arm (0, G, M, L, H5, SG) implemented; no `not_run` stub for an implemented arm | true | **true** |
| P5-DET | extract, model, reconcile, flows: 10 processes, random `PYTHONHASHSEED`, two roots, on N, deskd, kilnworks, harborline, toolshed, fixtures | identical | **10/10** |
| P5-NW | no write under any corpus, fixture, Streak, the main checkout; recording `claude` shim | 0/0/0 | **all 0** |
| P5-UNIT | `tests/grpc/test_{split5,descwrap5,builderopt5,status5,audience5,sqlraw5,truth5,ledger5}.py`, `tests/studio/test_rebuild_grpc5.py`, `tests/mcp/test_guard_dirs5.py` | 60-140 tests (rounds 3-4 overshot their ranges) | **all green, >= 50** |
| P5-SCORE | `grpc_scorer5_cases.json` | 78-110 cases | **100%, >= 78** |
| P5-PROC | round 4's 5 process cases re-expressed on H4's ledger + a broken chain + a symlinked app refused | 7/7 | **7/7** |
| P5-BUDGET | section 3 budgets and the 1200-line structural cap | all within (P about 0.55) | **all** |
| REG | `pytest tests/spec tests/importer tests/gocheck tests/mcp tests/studio tests/grpc` (all six, unlike round 4's three) on `git archive 978b468` vs the worktree | same failing ids | **same set** (new tests extra) |

### 10.2 Arm G (dev; tuned-on rows reported only)

| id | probe | prediction (p) | pass |
|---|---|---|---|
| PR5-N / PR5-DESK / PR5-SESS | reconcile | as round 4 (N 10/10; deskd 1.00/1.00; sessions 0 login_failed) | **exact counts** |
| PF5-N / PF5-DESK | flows | N 17/17; deskd 41/20, edge 21/34, coverage 4/7, **unless** E5 adds deskd tables (then declared in Amendment 1 before any held-out step) | **identical or declared** |
| PF5-LEAK | no process/bridge/container after reconcile, flows, forced failure, Arm L, PM-SG teardown | 0 | **0** |
| PR4-ALL | round-4 runner `--arm 0/G` at round-5 code, declared changes listed in Amendment 1 before running | every round-4 PASS row PASS or declared | **all** |
| PX5 | wall: extract toolshed; deskd reconcile; deskd flows; SQL reader overhead on harborline extract | 0.05-2 s; 1-10 s; 5-40 s; <= +20 % | **<= 30 s; 30 s; 180 s; <= +50 %** |
| PD5-KILN (reported) | kilnworks | RPC 1.0/1.0; CODES recall 0.80-0.95 (E3d adds interceptor codes), precision 0.90-1.00; AUTH 0 wrong, unclear 0-0.2; TABLES 1.0; REC 1.0; FLOWS 17/7, 11/33, 6/9 | reported |
| PD5-HARBOR (reported) | harborline | RPC 1.0/1.0; SAFE 0; CODES 0.45-0.80 / 0.90-1.00 (Berths E3a, Vessels E3b; Yard and Bookings unchanged); AUTH 0 wrong, unclear 0-0.1; TABLES 1.0; REC 1.0/1.0; FLOWS recovered 1-40, verified 0-8, edge 0.00-0.50, coverage 0-5 of 8; SQLT recall 0.60-1.00 | reported |
| PD5-TOOL (reported) | toolshed frozen bytes as delivered (freeze `cc524717...` checked), H1b and H2 in effect, no hand edits | RPC 0.91-1.00 / 0.91-1.00 (point 1.00: 22/22, Invoice `not_registered`); SAFE 0; CODES 0.50-0.90 / 0.85-1.00 (point 0.70 / 0.95); AUTH 0 wrong, unclear 0.00-0.11; TABLES 1.0 (`harness_db`); REC 1.00 / 1.00; FLOWS truth replaying 8-10 of 10, recovered 3-60, verified 1-15, edge 0.10-0.60, coverage 1-6 of 10; SQLT 0.60-1.00 / 0.80-1.00 | reported |

### 10.3 Arm M

| id | probe | prediction (p) | pass |
|---|---|---|---|
| PM-S5 | Arms M/L section 2 method at round-5 code vs `git archive 978b468`: extract, RepoModel, `select()`, inventory, report, app map, impact T2+T3 x6, on Streak a0679e9 and 1d00ede | 18 of 18 identical, `gin` | **18/18** |
| SG-RPC | rpc recall / precision | 0.80-1.00 / 0.85-1.00 (point 0.95 / 0.97); P(pass) 0.6 | **>= 0.90 / >= 0.95** |
| SG-SAFE | S8 | 0-2; P(0) 0.8 | **0** |
| SG-CODES | micro recall / precision | 0.30-0.90 / 0.75-1.00 (point 0.60 / 0.92); P 0.3 | **>= 0.70 / >= 0.90** |
| SG-AUTH | S7 | P(0 wrong) 0.85; unclear 0.00-0.80 (point 0.2); P 0.55 | **0 wrong, unclear <= 0.50** |
| SG-TABLES | table recall | bimodal: 0.0 or 0.85-1.00 (MySQL: no D5.2); P 0.5 | **>= 0.90** |
| SG-CD | author's gRPC spec, layers C and D on its app, MySQL | pass (P 0.7) | reported (validity of the author) |
| SG-REC / SG-FLOWS | | NOT SUPPORTED on MySQL (Q10 default) | not a pass |
| SG-DET / SG-NW | 10 processes; no writes | identical; 0 | **10/10; all 0** |

### 10.4 Arm L (model through the `claude` CLI only; sonnet; `--max-turns 40`; cap USD 5.00 on the CLI's estimate, Q9)

Fixture `tests/grpc/fixtures/toybuild5/` = Arms M/L's `toybuild/` from `828f022` with two changes made by the builder before the
freeze: `[mcp] protected` lists the **directory** `proto` (the round-4 failing form) and `check.sh` no longer runs `generate.sh` (the loop
must). The reference solution and its 3 calibration mutants are re-run first (reference PASS, 3/3 mutants fail).

| id | prediction (p) | pass |
|---|---|---|
| L5-RUN | passes in 1 round (P 0.8), <= 3 rounds (P 0.95), USD 0.05-0.40 | **passes in <= 3 rounds** |
| L5-NOFALSE | no round reports `spec_edited` for an unedited protected directory | **0 false reports** |
| L5-G1 | the 13 protected files and the proto directory sha256 base vs tip identical | **identical** |
| L5-G2 | no generated file in the commit except what `[grpc] generate` writes; the loop's generate log shows a run before each gate | **true** |
| L5-G3 | clean `git archive` of the tip, `./generate.sh && ./check.sh` x3 PASS | **3/3** |
| L5-COST | <= USD 5.00 | **<= cap** (a usage limit = NOT RUN) |

### 10.5 Held-out (PG-HO5; scored once after Amendment 3; gates exactly round 4's)

| id | metric | prediction (p) | pass (gated) |
|---|---|---|---|
| HO5-RPC | recall / precision, state included | 0.45-1.00 / 0.50-1.00 (point 0.82 / 0.85); P 0.35 | **>= 0.90 / >= 0.95** |
| HO5-SAFE | S8 count | 0-3 (point 0); P(0) 0.75 | **0** |
| HO5-CODES | micro recall / precision | 0.15-0.85 / 0.70-1.00 (point 0.45 / 0.92); P 0.2 | **>= 0.70 / >= 0.90** |
| HO5-AUTH | S7 | P(0 wrong) 0.85; unclear 0.00-1.00 (point 0.35); P 0.5 | **0 wrong and unclear <= 0.50** |
| HO5-TABLES | recall, `tables_source` reported | 0.80-1.00 (point 1.00); P 0.9 | **>= 0.90** |
| HO5-REC | S5 + S6 + 0 auth_refused + 0 login_failed + isolated | coverage 0.95-1.00, agreement 0.70-1.00 (point 0.95); P 0.65 | **coverage >= 0.95, agreement 1.00, supplementary 0, S6 not FAIL, 0 auth_refused, 0 login_failed** |
| HO5-FLOWS | S13 | truth replaying >= 0.8 of flows (contract-checked); verified 0-10 (point 2); edge 0.00-0.60 (point 0.20); coverage 0-6 (point 2); P 0.3 | **verified >= 1, 0 vbf, edge recall >= 0.30, coverage >= 2** |
| HO5-DET | 10 processes, 2 roots | identical; P 0.95 | **10/10** |
| HO5-NW | app root, shim | 0/0/0; P 0.95 | **all 0** |
| HO5-SQLT | S14 | 0.0-1.0 (point 0.6) | reported |

**Honest priors.** Round 3 predicted P(all 8) 0.15-0.25 and got 2 of 8 (not clean); round 4 was never scored. The author is again asked to
vary wiring and error idioms, and E1-E5 are tuned on toolshed and harborline, so HO5-RPC and HO5-CODES depend on shapes outside what is
built. **P(all 9 gated HO5 rows) 0.03-0.08; P(>= 6 of 9) 0.35-0.45; P(the held-out is scored at all, i.e. no crash on both permitted runs)
0.85** (was the round-4 failure; H1-H7 target it).

**Counts.** Arm 0: 24 gated rows; Arm G: 8 gated + 3 reported; Arm M: PM-S5 + 7 gated SG rows + SG-CD reported + 2 NOT SUPPORTED; Arm L: 6
gated; HO5: 9 gated + 1 reported. Preconditions (not probes): audits, contract checks, dispatch-after-freeze, the diversity floor.

## 11. Mutants (`dogfood/grpc_mutants5.py`, scratch copy, **control first and green**; predicted 34-36 of 36 new killed)

Killed when a gated probe that passes on the control fails, or a named unit test fails (disclosed per mutant).

| id | mutant | predicted killer |
|---|---|---|
| GH5-1 | contract check prints a generic error for `build_db` returning `None` | P5-HAPI |
| GH5-2 | H1b off (`make_app(None)`) | P5-HAPI, PD5-TOOL REC (unit `test_truth5`) |
| GN5-1 | `.upper()` restored in place of the table | P5-CODE, P5-LINKS |
| GN5-2 | `Canceled` not mapped to `CANCELLED` | P5-CODE |
| GN5-3 | an unknown name passed through instead of refused | P5-CODE |
| GK5-L1 | replay ignores links | P5-LINKS |
| GK5-L2 | replay ignores `step_index_base` | P5-LINKS |
| GK5-L3 | replay checks the code only, not dotted `expect` paths | P5-LINKS |
| GK5-L4 | a missing `from_field` sends an empty field instead of `link_unresolved` | P5-LINKS |
| GL5-1 | ledger kept per `--out` (round 4) | P5-LEDGER |
| GL5-2 | chain not verified | P5-LEDGER, P5-PROC |
| GL5-3 | a preflight refusal consumes a run | P5-LEDGER |
| GL5-4 | symlinked app accepted | P5-PROC |
| GW5-1 | E1 off | P5-DESC |
| GW5-2 | E1.2: non-literal ServiceName taken as the original's | P5-DESC (S8) |
| GW5-3 | E1.3: Methods literal ignored (original set kept) | P5-DESC |
| GW5-4 | E1.4: wrapper translator not applied | P5-DESC, P5-STATUS |
| GW5-5 | E2 off | P5-BOPT |
| GW5-6 | E2 resolves only the first option call site | P5-BOPT |
| GW5-7 | E2 registers from an option no builder ranges over | P5-BOPT (negative) |
| GK5-1 | E3a off | P5-STATUS |
| GK5-2 | E3a field code: union over all construction sites, not per return site | P5-STATUS |
| GK5-3 | E3b off | P5-STATUS |
| GK5-4 | E3c WithDetails chain unread | P5-STATUS |
| GK5-5 | E3d adds the gate code to Public rpcs too | P5-ICODE |
| GK5-6 | E3d adds codes from an `unclear` interceptor | P5-ICODE |
| GA5-1 | E4a off (empty allowlist literal: round-4 behaviour) | P5-AUD (S8) |
| GA5-2 | E4b off (option gate read as unclear for all) | P5-AUD |
| GA5-3 | E4b: absent option treated as the public value | P5-AUD (wrong Public) |
| GA5-4 | E4c: a custom option recorded on every rpc of the service | P5-AUD |
| GS5-1 | E5 off | P5-SQL |
| GS5-2 | E5.2: `%s` table read as literal | P5-SQL |
| GS5-3 | E5.3: UPDATE classed `create` | P5-SQL |
| GS5-4 | E5 called from the HTTP walk | P5-BC |
| GR5-1 | L1 off (directory compared as a file) | P5-PROT |
| GR5-2 | L1: added file under a protected directory kept | P5-PROT |
| GR5-3 | L1b off | P5-PROT |
| GR5-4 | L2: generate not run before the gates | P5-GEN |
| GR5-5 | L3: brief omits the protos | P5-BRIEF |
| GP5-1 | S10'/S12: run index taken from the runner's output, not the ledger | P5-SCORE, P5-PROC |

**40 new** (the task's floor is 16), plus **round 4's 61 killed mutants re-applied** (GW4-9 excluded, a known survivor; re-expressed where
text moved, disclosed). Pass: 40/40 new and 61/61 re-applied, killers disclosed (by a probe or a named unit test).

## 12. Files: may change, creates, frozen (hashes r at `978b468`)

**May change (budgets of section 3, measured against S0 for the Go files and `978b468` for the rest):**

| file | sha256 at `978b468` | lines (m) |
|---|---|---|
| `lattice/gocheck/src/goextract_grpc.go` (+ the two split files created at S0) | `3f181be7e7c50204bdadbc0c1a22a0988f4c68fd610817c9782011d540aa4354` | 2326 |
| `lattice/gocheck/src/goextract_proto.go` (E4c only) | `ec4aa3157b407467708bdc341171e94e9a33aedf70ac6a46893d7b41590c9a50` | 1161 |
| `lattice/importer/frontends/go_grpc.py` | `7543c9fabc0e7a9369f760e18d6f2220a712ea010df821bcd496ea81927db799` | 338 |
| `lattice/spec/grpc.py` (H1b only; AST of every other top-level def identical) | `1bd489dffd545aaedd923756a52aaebb84ab89e434f14a37aa048f707a204c80` | 851 |
| `lattice/studio/rebuild.py` (L1-L3 only) | `ddb005b594e20acbf61fd228d15b73d7b5c331d6eba392999509d0b0de3a7cb7` | 3967 |
| `lattice/mcp/guard.py` (L1b only, if Q6) | `d8b7ba22d6393ac66939772d9b82200e0fe8e57fa167272c50feb466f5e834dc` | 573 |

**Creates:** `lattice/gocheck/src/goextract_grpc_auth.go`, `goextract_grpc_codes.go` (S0, pure move), `goextract_sqlraw.go`;
`dogfood/grpc_truth5.py`, `grpc_contract5.py`, `grpc_probes5.py`, `grpc_scorer5.py`, `grpc_scorer5_cases.json`, `grpc_mutants5.py`;
the tests of P5-UNIT; `tests/grpc/fixtures/{descwrap5,builderopt5,status5,audience5,sqlraw5,truth5,harness5,toybuild5}/**` (each
`truth.json` hashed into `freeze-dev5.sha256` before any library code); `docs/results/data/2026-10-18-grpc-round5/**` (ledger included);
the round-5 results doc; this file only by appended Amendments.

**Frozen (empty diff vs `978b468`; the runner checks):** every other `lattice/**` file, in particular `lattice/importer/workflows.py`
(`5eba7e8a...`), `lattice/importer/transport.py` (`04979019...`), `lattice/spec/process.py` (`047be55e...`), `lattice/importer/outside.py`,
`lattice/grpcbridge/**`, every other `lattice/gocheck/src/*.go`; every earlier runner, scorer, contract and mutants file
(`grpc_probes{,2,3,4}.py`, `grpc_scorer{3,4}.py`, `grpc_contract4.py` `6666ac9b...`, `grpc_flow_scorer.py` `8d896ee0...`); every existing
`tests/**` file; every corpus and archive; all earlier docs (errata go in the round-5 results).

## 13. Byte-compat promise (vs `978b468`)

**Identical:** every HTTP output of rounds 1-4's promise (73 files); PM-S5's 18 Streak rows; `reconcile` / `reconcile_with` on N,
sessions, deskd; the rebuild brief, gates and protected list of any project without `[grpc]` and without a directory entry in `[mcp]
protected`. **Declared differences (field-level, checked):** gRPC extracts gain `kind: "interceptor"` codes (E3d) on every corpus with a
literal gate (expected: deskd, kilnworks, harborline, toolshed; N and sessions if they have one); `custom_options` on rpcs of corpora with
scalar rpc options (toolshed); `tables`/`writes` from E5 on `database/sql` corpora (harborline, toolshed; deskd/kilnworks/N listed in
Amendment 1 after the first dev run if they change); any `auth_literal` -> `auth_not_literal` flip from E4a, listed in Amendment 1 before
any held-out step; flows on any corpus whose route tables changed. A difference elsewhere is a finding, not re-labelled.

## 14. Risks, and what this cannot show

1. **Tuned on toolshed and harborline**: E1-E5 were chosen after reading them; PD5-TOOL and PD5-HARBOR look good by construction and are
   never gated. 2. **Open-ended idioms**: the next author's fifth wiring or error shape is `unread` (honest, lowers recall). 3. **E4a can
   lower recall** (more `unclear`) on apps whose bypass was right by luck; that is the intended trade. 4. **E3d raises CODES recall by a
   rule that is mostly about the gate**, so CODES moves partly for auth reasons; reported per kind. 5. **E5 is a lexical SQL reader**, not a
   parser: dynamic SQL is `unread`, a table in a string the reader misparses is a false table (precision); P5-SQL lists the shapes covered.
6. **PM-SG on MySQL** cannot run REC/FLOWS (library gap) and TABLES depends on DDL placement. 7. **The author may run `contract5`** (Q4):
   it gives the author a library path's pass/fail view of its harness (never extractor output); disclosed. 8. **Transcript audits are after
   the fact**; the fixed directory is the primary control. 9. **n = 1 held-out + 1 reproduction**, one grpc-go version, one model family for
   authors. 10. **Arm L is easy by construction** (one sample, sonnet). 11. **Cannot show:** streaming, non-Go servers, .NET, layers A/R/X/B
   on rpcs, flow-rule changes, Streak's live layers.

## 15. Blind vs post hoc, the pass rule, commands, file ownership

**Fixed now:** H1-H7, S, E1-E5, L1-L3, S10'-S14, every prediction, gate, budget and mutant, both briefs, the truth schema, the contract
check's lines, the ledger and re-run rules. A missed prediction is a finding. **Blind rows** are only HO5-* and SG-* rows that S10'/S12
label `blind` (hashes equal Amendment 2, ledger chain intact, run index 1, or 2 after a crash before metric with equal bytes, target freeze
equal Amendment 3/3-SG). Every dev row (PD5-*, PR5-*, PF5-*, deskd, kilnworks, harborline, toolshed) is post hoc in every run. Anything
changed after a target is opened is post hoc, reported beside the first value, never in its place. **Pass of the round:** every gated row of
10.1-10.5, mutants 40/40 and 61/61, P5-BC, REG, PM-S5, and `blind` labels on HO5 and SG rows. NOT RUN and NOT SUPPORTED are not passes.

```sh
export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1
W=/Users/vivekgade/Documents/lattice-gr; SCRATCH=<build node scratchpad>/gr5; D=docs/results/data/2026-10-18-grpc-round5
export LATTICE_GOCHECK_CACHE=$SCRATCH/gocache LATTICE_GRPCBRIDGE_CACHE=$SCRATCH/bridgecache
mkdir -p $SCRATCH/base $SCRATCH/corpora && git -C $W archive 978b468 | tar -x -C $SCRATCH/base          # byte-compat + REG baseline
for t in heldout heldout2 heldout3 heldout4-toolshed; do tar -xzf $W/docs/results/data/archive-frozen-sets-2026-10-18/$t.tgz -C $SCRATCH/corpora; done
git -C $W show 828f022:docs/results/data/2026-10-18-grpc-arms-m-l/toybuild/<file>   # toybuild5 source (Q2), per file
sha256sum tests/grpc/fixtures/{descwrap5,builderopt5,status5,audience5,sqlraw5,truth5}/truth.json > $D/freeze-dev5.sha256   # before library code
# S0: pure move, then P5-SPLIT; orchestrator commits S0 (explicit paths)
uv run python dogfood/grpc_probes5.py --arm 0 --base $SCRATCH/base --corpora $SCRATCH/corpora --out $D/arm0
uv run python dogfood/grpc_probes5.py --arm G --base $SCRATCH/base --corpora $SCRATCH/corpora --out $D/armG
uv run python dogfood/grpc_probes5.py --arm M --streak-rev a0679e9,1d00ede --base $SCRATCH/base --out $D/armM        # PM-S5 (git archive of Streak)
uv run python dogfood/grpc_probes5.py --arm L --fixture tests/grpc/fixtures/toybuild5 --cap-usd 5 --out $D/armL      # claude CLI, sonnet
uv run python dogfood/grpc_probes5.py --arm H5 --rehearse --app $SCRATCH/corpora/toolshed --out $SCRATCH/reh          # P5-REH (scratch ledger)
uv run python dogfood/grpc_probes4.py --arm 0 ...; uv run python dogfood/grpc_probes4.py --arm G ...                 # PR4-ALL
uv run python dogfood/grpc_mutants5.py --scratch $SCRATCH/m --all-new --reapply-round4
uv run python -m pytest tests/spec tests/importer tests/gocheck tests/mcp tests/studio tests/grpc -q -p no:cacheprovider --continue-on-collection-errors
sha256sum <section-12 may-change files> lattice/gocheck/src/goextract_grpc_{auth,codes}.go lattice/gocheck/src/goextract_sqlraw.go \
  dogfood/grpc_truth5.py dogfood/grpc_probes5.py dogfood/grpc_scorer5.py dogfood/grpc_scorer5_cases.json dogfood/grpc_contract5.py > $D/freeze5.sha256
# orchestrator: commit freeze5 + Amendment 2 -> dispatch HO5 and SG authors -> audits -> contract5 -> app freezes + Amendments 3, 3-SG, then once each:
uv run python dogfood/grpc_probes5.py --arm H5 --app <ho5 app> --out $D/ho5      # ledger line committed after
uv run python dogfood/grpc_probes5.py --arm SG --app <sg app> --out $D/sg        # Docker MySQL, label lattice.owner=w-gr
uv run ruff check <changed .py>; uv run ruff format --check <created .py>; gofmt -l lattice/gocheck/src tests/grpc/fixtures
```

**File ownership (build node: sonnet, own worktree off this branch, never commits).** Edits: section 12 "may change" only, within
budgets. Creates: section 12 "creates". Not touched: the frozen list; the authors' scratch trees and their apps; the ledger outside
`--process-test`/`--rehearse`; `~/Documents/streak` (read only through `git archive`/`git show` into scratch); the main checkout. The
orchestrator commits S0, the freeze, the ledger and Amendments with explicit paths only (never `git add -A`, never `git add .`), writes
Amendments with a quoted heredoc (`<<'E'`), and nothing merges without the owner's yes.

## 16. Process lessons of handoff section 6, applied

| lesson | where |
|---|---|
| every harness-API fact in the brief | H1, Appendix A complete now (no "pasted later"), P5-HAPI gate; author may self-check (Q4) |
| runner and scorer complete, every arm, before the freeze | P5-ARMS, P5-REH (end-to-end rehearsal on a real-shaped app), Arms M and L inside the runner |
| never disable the self-hash check | 8.3 step 5, P5-PROC, GL5-* |
| the run ledger must be global | H4, S12, P5-LEDGER |
| do not name idioms in the author's brief | 8.2, Appendix C (forbidden-word list extended) |
| quoted heredocs for amendments | section 15 |
| separate author after the freeze, audited, scored once, independent re-run, blind and post hoc separate | 8.1-8.3, 9, 15 |
| max 4 agents in parallel | HO5 and SG authors may run together (2) after the freeze; the verifier after scoring |

## 17. Questions for the orchestrator

1. **Base and branch.** Build on `np-grpc-round5` (this file on top of `978b468`) in a new build worktree (name?), or commit this file
   to `next-phases` first as rounds 3-4 did?
2. **`np-i-arms` (828f022).** Merge it into `next-phases` first (owner's yes), or have the build node copy `toybuild/` from that commit
   into `tests/grpc/fixtures/toybuild5/` by `git show` (default)?
3. **H1b.** Accept the 3-site `grpc.py` change so `build_db` returning `None` means "the path I was given" (as `check()` already does)?
   Without it, toolshed's delivered harness still fails REC/FLOWS in PD5-TOOL.
4. **Author self-check.** May each author run `contract5` from a frozen wheel in its own scratch `.venv` (pass/fail lines only, reading
   `site-packages/lattice` forbidden and audited)? Default yes; otherwise the 2-round orchestrator-mediated loop of round 4 stays.
5. **E3d (interceptor codes).** It is beyond the handoff's list but is 15 of toolshed's 71 truth codes; keep it in scope (default) or
   strike it?
6. **L1b.** Include `lattice/mcp/guard.py` (paths under a protected directory) in this round (default yes, <= 30 lines)?
7. **Budgets.** Accept the re-split (S0 pure move, budgets measured from S0) and <= 650 / 300 / 40 / 20 / 6 / 90 / 30, with a miss a
   gated FAIL? Or a different decision (no split; one cumulative budget)?
8. **Gates.** Keep round 4's HO thresholds unchanged (proposed) and apply them to SG-*; keep the unclear cap 0.50?
9. **Arm L money.** Approve the USD 5.00 cap (sonnet, `--max-turns 40`) for L5-RUN, as round 4's Arms run did?
10. **PM-SG database.** MySQL via `dbserver` with SG-REC/SG-FLOWS NOT SUPPORTED (default), or SQLite so every row runs?
11. **PM-SG inputs.** Give the SG author Streak's REQUIREMENTS.md, spec.py and CONTEXT.md only (default), or also its Go source?
12. **`go:embed` DDL.** Toolshed's truth labelled its embedded `.sql` schema `ddl: go`; /5 separates `go_literal` and `go_embed`. Should the
    extractor read `//go:embed` SQL for DDL this round (not in scope; D5.2 covers SQLite), given PM-SG on MySQL cannot use D5.2?
13. **D1.4 adapter clause.** GW4-9 showed it changes no state on any built shape; remove it in this round (a behaviour-neutral deletion,
    budget-counted) or leave it?
14. **Rehearsal on toolshed.** P5-REH runs `--arm H5 --rehearse` on toolshed as delivered; confirm this is acceptable given toolshed is dev
    data (its rows are reported as PD5-TOOL, post hoc).
15. **Verifier.** Who runs the independent opus re-run of HO5 and SG (from the committed ledger and freezes), and may it re-run the scorer
    (pure) without consuming a ledger run (proposed: scorer re-runs on the saved raw output are free; runner runs are not)?

## Appendix A: `HARNESS_API.md` (copied verbatim to every author; the only library text it sees)

**A.1 Files.** Your app directory holds `harness.py` and `studio.toml` at its root:

```toml
[app]
app = "harness:make_app"
db = "harness:build_db"
runtime = "harness:RUNTIME"
```

**A.2 What `harness.py` defines.**
- `USERS: dict[str, tuple[str, str]]`: handle -> (login name, password). Every handle must be able to log in on a freshly built database.
- `build_db(path) -> path`: create and seed a SQLite database file at `path` (a `pathlib.Path`; its directory exists), then **return
  `path`**. Do not return `None`; do not create files elsewhere.
- `make_app(db_path) -> ProcessApp`: called with exactly what `build_db` returned. It may build your server (into a temporary directory,
  never into the app tree) and returns a `ProcessApp` describing how to start it. Find Go with `shutil.which("go")`; do not hard-code a path.
- `RUNTIME`: **the value returned by `process_runtime(...)`**, never the `AuthDriver` itself:

```python
from lattice.importer.transport import AuthDriver
from lattice.spec.process import ProcessApp, process_runtime

AUTH = AuthDriver.rpc("pkg.v1.SomeService", "SomeLogin", USERS,
                      username_field="<field>", password_field="<field>", token_field="token")
RUNTIME = process_runtime(framework="go", auth=AUTH)
```

`AuthDriver.rpc(service: str, method: str, credentials: dict[str, tuple[str, str]], *, username_field: str | None = None,
password_field: str | None = None, token_field: str = "token", scheme: str = "bearer") -> AuthDriver`. Everything after `credentials` is
**keyword-only**; positional use is an error. With the two field names left `None` they are discovered from the login request message (the
one string field whose name contains `pass`, and the one other string field); name them if your message has more than two string fields.
`token_field` is a dotted path into the login response. The token is sent as metadata `authorization: Bearer <token>`.

`process_runtime(*, framework: str = "process", auth: AuthDriver | None = None, dispose=(), services=None) -> AppRuntime`; pass
`framework="go"`.

**A.3 `ProcessApp` fields a gRPC app uses** (from `lattice/spec/process.py`; all keyword arguments except `argv`):
`argv: Sequence[str]` (the command); `db_path: Path | str | None` (pass `db_path`, it is exported to your process); `env: Mapping[str,
str]` (added to the inherited environment); `cwd: Path | str | None`; `port_env: str = "PORT"` (the variable carrying the free port your
server must listen on, on 127.0.0.1); `db_env: str = "DB_PATH"` (the variable carrying `db_path`); `ready: str = "http"`, set **`ready=
"grpc"`**: start-up is detected by `grpc.health.v1.Health/Check` answering `SERVING` on that port, so register the standard health service;
`startup_timeout: float = 15.0` (seconds; raise it if your server is slow to start); `protos: Sequence[str]` (your `.proto` files, paths
relative to `proto_root`), `proto_paths: Sequence[str]` (include roots), `proto_root: Path | str | None` (base directory of the two lists);
`grpc_port_env: str | None = None` (only if the app serves HTTP on `port_env` and gRPC on a second port); `stop_timeout: float = 5.0`;
`request_timeout: float = 30.0`; `host: str = "127.0.0.1"`; `inherit_env: bool = True`. Do not set any other attribute.

**A.4 Skeleton (fill in; no server code here).**

```python
from pathlib import Path
import shutil, sqlite3, subprocess, tempfile
from lattice.importer.transport import AuthDriver
from lattice.spec.process import ProcessApp, process_runtime

ROOT = Path(__file__).resolve().parent
USERS = {"<handle>": ("<login name>", "<password>")}
RUNTIME = process_runtime(framework="go", auth=AuthDriver.rpc("<svc>", "<method>", USERS, token_field="token"))

def build_db(path):
    con = sqlite3.connect(path)
    ...  # schema (or apply your own migrations) and seed rows
    con.commit(); con.close()
    return path

def make_app(db_path):
    out = Path(tempfile.mkdtemp()) / "server"
    subprocess.run([shutil.which("go"), "build", "-o", str(out), "./cmd/<server>"], cwd=ROOT, check=True)
    return ProcessApp([str(out)], db_path=str(db_path), ready="grpc",
                      protos=["<a.proto>", ...], proto_paths=["."], proto_root=str(ROOT / "<proto dir>"))
```

**A.5 MySQL variant (PM-SG only).** Add `database = "mysql"` under `[app]`. Then `build_db(db)` receives a database **URL**
`mysql://USER:PASS@HOST:PORT/DB` for an empty database (not a file path), creates the schema and seed rows in it, and returns `db`;
`make_app(db)` receives that URL; translate it for the Go MySQL driver with `from lattice.spec.db import url_for; url_for(db,
"go-mysql")` and pass it to your server in an environment variable of your choice (keep `db_path=db` as well). MySQL is 8.4, started by
Lattice; do not start containers yourself.

**A.6 Spec steps (PM-SG only).** `from lattice.spec import Rpc`: `Rpc(service: str, method: str, user: str | None = None, message: Any =
None, metadata: Mapping[str, str] | None = None, expect_code: str = "OK", expect_message: Any | None = None, expect_db: Mapping[str, Any]
| None = None)`; `service` is the full name (`pkg.v1.Service`), `user` a `USERS` handle (None: no token), `message` the request in proto
field names (64-bit integers within +/-2^53 as JSON numbers), `expect_code` an UPPER_SNAKE `google.rpc.Code` name, `expect_message` a
subset of the response (OK) or of `{"code", "message"}` (an error). Only unary rpcs.

**A.7 Your own check.** `contract5 <your app dir>` (if present in your directory) prints one line per fact above (truth schema, codes,
import, RUNTIME, build_db, make_app, ProcessApp, server start, one login per handle, one replay per truth flow). Hand over only when every
line says `ok`.

## Appendix B: `TRUTH_SCHEMA.md` (`lattice.heldout.truth/5`)

As /4 (per-rpc `rpc`, `service`, `method`, `state` in `implemented | unimplemented | not_registered`, `streaming`, `auth` in `Public |
Member | <role>`, `handler` file:line, `codes`, `code_sites[{code, at, via}]`, `interceptor_codes`, `tables`, `writes`, `effects`; `login:
{service, method, username_field, password_field, token_field}`; `users`; `tables[{name, ddl, read_by, written_by, notes}]`; `flows`), with
these changes: (1) **every code is an UPPER_SNAKE `google.rpc.Code` name** (`OK`, `CANCELLED`, `UNKNOWN`, `INVALID_ARGUMENT`,
`DEADLINE_EXCEEDED`, `NOT_FOUND`, `ALREADY_EXISTS`, `PERMISSION_DENIED`, `RESOURCE_EXHAUSTED`, `FAILED_PRECONDITION`, `ABORTED`,
`OUT_OF_RANGE`, `UNIMPLEMENTED`, `INTERNAL`, `UNAVAILABLE`, `DATA_LOSS`, `UNAUTHENTICATED`); (2) `conventions.step_index_base` is required
(0 or 1); (3) a flow is `{name, steps: [{rpc, as, request, expect: {code, <dotted response path>: value, ...}}], links: [{from_step,
from_field, to_step, to_field}]}`, field paths dotted proto field names, numeric segments index lists, a final `#` a list length; (4)
`tables[*].ddl` is one of `go_literal` (a `CREATE TABLE` string in a `.go` file), `go_embed` (a SQL file embedded into the binary),
`harness` (only in `harness.py`), `migration_tool`, `other`; (5) `schema: "lattice.heldout.truth/5"`.

## Appendix C: `BRIEF.md` (HO5 author; verbatim)

Write a gRPC service in Go (grpc-go, unary and at least one server-streaming rpc) over pure-Go SQLite (no cgo, no Docker; buildable with
the Go module proxy) in `./app/` inside this directory, plus `harness.py` and `studio.toml` exactly as `HARNESS_API.md` says, `truth.json`
exactly as `TRUTH_SCHEMA.md` says, a Go program `cmd/replay` (its own client, no Python) that runs every truth flow on a freshly built
database and also all flows in order on one database and exits 0 only if all pass, `FILES.txt` (every file, one per line), and
`freeze.sha256` at the app root. Minimums: at least 3 registered services, at least 15 unary rpcs, at least 1 rpc answered `Unimplemented`,
at least 1 declared service that is never registered, at least 2 users of different roles, at least 2 request fields that name a seeded row
by a natural key, at least 6 truth flows of at least 3 steps that copy values between steps with links, and a login rpc. Organise the server
the way a team maintaining a mid-sized production service would: packages per area, construction code separate from handlers, and the
indirection such codebases typically have. No two services may be wired into the server by the same code idiom; no two services may report
errors by the same idiom; the authentication decision is written in an idiom of your own choice. Put the database schema wherever such a
team would. Do not copy an example from any document. When code and truth are complete and every check you can run passes, write
`IDIOMS.md`: one line per service's wiring, per service's error reporting, per authentication mechanism, and where the schema lives. Work
only inside this directory.

## Appendix D: `BRIEF-SG.md` (PM-SG author; verbatim)

`streak/` holds the requirements (`REQUIREMENTS.md`), the behavioural spec (`spec.py`, an HTTP spec) and the context (`CONTEXT.md`) of a
habit-tracking service. Re-implement the same behaviour as a gRPC service in Go (grpc-go, unary rpcs) over MySQL 8.4, in `./app/`:
`.proto` files, the server, `harness.py` and `studio.toml` per `HARNESS_API.md` (A.5 MySQL), a gRPC form of the spec (`spec.py` using `Rpc`
steps, A.6) covering every rule you can express and listing the others in `NOT_EXPRESSED.md`, `truth.json` per `TRUTH_SCHEMA.md`, a Go
program `cmd/replay` that runs every truth flow and exits 0 only if all pass, `FILES.txt`, `freeze.sha256` at the app root. Implement it the
way you would naturally implement such a service. When everything is complete, write `IDIOMS.md` (one line per service's wiring, error
reporting, authentication, and where the schema lives). Work only inside this directory.

## Amendment 1 (orchestrator rulings on section 17, 2026-10-18, before any code)
1. Build on `np-grpc-round5` in new build worktrees (named by node); this file stays on that branch and merges with the build.
2. The orchestrator merges `np-i-arms` (docs and data only, 828f022) into `next-phases` now; `np-grpc-round5` then merges
   `next-phases`, and the build node copies `toybuild/` by `git show` into `tests/grpc/fixtures/toybuild5/`.
3. H1b accepted (the 3-site `grpc.py` change).
4. Author self-check accepted: each author may run `contract5` from a frozen wheel in its own scratch `.venv` (pass/fail lines
   only; reading `site-packages/lattice` forbidden and audited).
5. E3d (interceptor codes) stays in scope.
6. L1b: `lattice/mcp/guard.py` is included (<= 30 lines).
7. Budgets and the re-split accepted as registered (<= 650 / 300 / 40 / 20 / 6 / 90 / 30); a miss is a gated FAIL.
8. Round 4's HO thresholds are unchanged and apply to SG-*; the unclear cap stays 0.50.
9. The USD 5.00 cap for L5-RUN is approved (sonnet, `--max-turns 40`, the `claude` CLI only; a usage limit is NOT RUN).
10. PM-SG runs on MySQL; SG-REC and SG-FLOWS are NOT SUPPORTED as registered and are reported as such.
11. The SG author gets Streak's REQUIREMENTS.md, spec.py and CONTEXT.md only, not its Go source.
12. `//go:embed` DDL is not read by the extractor this round (out of scope; reported).
13. The D1.4 adapter clause is removed in this round as a behaviour-neutral deletion, counted in the budget.
14. P5-REH on toolshed as delivered is acceptable; its rows are reported as PD5-TOOL, post hoc.
15. The independent re-run of HO5 and SG is run by a separate opus verifier from the committed ledger and freezes; re-running
    the pure scorer on saved raw output is free, runner runs are not.

## Amendment 2 (Stage B build node's disclosures and orchestrator rulings, 2026-10-18, before the freeze)
**Declared changes (accepted):** E3d interceptor codes change three frozen round-4 tests' observations on the notes app N
(`tests/grpc/test_extract_notes.py::test_extract_matches_the_truth`, `::test_model_lists_rpcs_as_routes_and_bindings_on_the_rpc`,
`tests/grpc/test_proto_reader.py::test_validation_bounds_are_exact`); as in round 4, the orchestrator updates those expectations
to the accepted E3d behaviour (only the interceptor codes may change) and says so. E3b, E1 and E2 also change codes and states
on kilnworks, harborline and toolshed beyond section 13's list (undeclared in P5-BC-EXT): these are **declared now, post hoc**,
and P5-BC's undeclared-diff rows are reported as such (the corpora are development data). The evalSentinel synthetic host (D3 of
the build report) is accepted as a disclosed addition. **Budget:** P5-BUDGET FAILS and stays failed (Go group 924 vs 650); the
round cannot pass in full, and the table says so. **NOT RUN, to be run by the orchestrator's node with the driver environment:** the
MySQL halves of P5-HAPI and P5-REH (pymysql was not importable in the builder's environment), PR4-ALL, the real Arm L run.
PF5-LEAK failed on a stray `notesapp` binary under Stage A's bridgecache (not Stage B's); the orchestrator removes it and the row is
re-measured. Accidents disclosed: one mistaken `git stash` popped within seconds; stray `.venv` directories created and removed.

**Freeze records (orchestrator, before the held-out and SG authors start; the runner's preflight reads these lines):**
- dogfood/grpc_probes5.py sha256 ef05f9368a1c96f2a36d4631c10a6a8532422632dffa9e1b77361263355ead40
- dogfood/grpc_scorer5.py sha256 991cb1dc46623e58d6c457bb5c86277311b32ca6cb7a32f3513eca40ec569ec3
- dogfood/grpc_truth5.py sha256 b08a0759bb49f2ce1c41942cb6f5ee7cc8a4ec9e7d6855c8294608a3440f099b
- dogfood/grpc_contract5.py sha256 f71b9e5d1b6b9caf7ccd25d7548121dde74afeaf1ca9a8257c9978178c372681
- freeze5.sha256 sha256 e92972e2c8c658c34d21ef956f3418d5537a3fb50d24bb8d848e1c0d9db16eea

## Amendment 3 (Stage C results and rulings, orchestrator, 2026-10-18, before the freeze)
Stage C: the three frozen tests' expectations were moved by the node into the tests (a constant `INTERCEPTOR_CODES` and a helper; `truth.json`
untouched, 8 leaves each `+UNAUTHENTICATED`, nothing removed): accepted. REG passes (the same 31 failing ids on both sides). P5-HAPI (MySQL
half), P5-REH, PF5-LEAK and Arm L (7/7, 1 round, USD 0.1195) pass. **PR4-ALL FAILS and stays failed:** P4-BC means "identical to its own
base" and any round that changes the extractor breaks it; it differs on notes (declared), sessions and deskd (undeclared; declared now,
post hoc, as code-list differences from E3d/E3b) and kilnworks and harborline; the `spec check --help` HTTP difference is not from round 5
(identical to 978b468, 73/73). P5-BUDGET also stays failed (924 vs 650). Therefore the round cannot pass in full; the held-out and SG stages
are still run because their result is the information.
