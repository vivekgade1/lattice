# gRPC round 4 (Spike I4): the shapes harborline showed, a schema and login rule decided in advance, and a held-out that is independent by construction: pre-registration

## 1. Status, and the exact gap

2026-10-18. **Written BEFORE any round-4 code; no model was called, no API key was used; nothing is committed or staged.**
This file is the only file this step writes (`/home/user/lattice/docs/results/`, the `next-phases` checkout, HEAD `be26a94`, r:
the merge of `spike-i-grpc` rounds 1-3 into `next-phases`, "held-out gates NOT met ... not a clean blind held-out").

Read while writing (read only): round-3 prereg `2026-10-18-grpc-round3-prereg.md` (with Amendments 1 and 2; the template of this
file), round-3 results `/home/user/wt/np-grpc/docs/results/2026-10-18-grpc-round3-results.md` (sections 1-10 and section 11, the
gated harborline stage), `lattice/spec/grpc.py` (`rpc_login`, lines 366-393; `reconcile_with`, line 459),
`lattice/importer/transport.py` (`AuthDriver.rpc`, lines 345-364: `username_field="name"` default, keyword-only after
`credentials`), `lattice/importer/outside.py` (`db_facts`, line 322: the SQLite schema read from a database file), and
`lattice/importer/frontends/go_grpc.py` (line 139: `tables` taken from the extract JSON only). The task text named "verifier
corrections in the session summary below"; no session summary reached this author, so the corrections used here are the ones the
computed task states (section 1.2, items marked **(v)**) and Q1 asks for the summary. Labels: **(m)** measured, **(r)** read from
code or data while writing this, **(v)** a verifier correction relayed by the task, everything else a **prediction (p)**.

**Every corpus this round can see is development data.** `deskd` (round-1 held-out, `freeze.sha256` `8670bf84...`), kilnworks
(round-2 held-out, `d7a5ea3c...`) and **harborline** (round-3 held-out, `freeze.sha256` sha256
`1c51ee0e5a1c4118205445b0f6d5ef882f5180af519f4ed5df285a9133d718b8`, r from round-3 Amendment 2) have all been scored, and harborline's
code, extract, `IDIOMS.md` and failures were read to choose this round's scope (section 1.3). **None of the three is ever a blind
or held-out claim again.** Every harborline number in round 4 is tuned-on (PD4-HARBOR, reported only). The only gated held-out
of round 4 is a NEW app written by a NEW separate author, dispatched AFTER the round-4 library freeze, isolated from every
grpc document and from `lattice/**` (section 6).

**Environment (m in round 3, assumed unchanged; re-measured by the runner's first line).** `go` and `uv` work (go1.24.7, uv
0.11.32, Python 3.13.16). No Docker daemon, no Streak checkout. **Docker-free arms (0 and G) are the only gated arms.** Arm M
(Streak/MySQL) and Arm L (build round) are out of scope and reported NOT RUN in the results' first line. Streaming stays
`not supported`.

### 1.1 Round 3, as it stands (m, from the round-3 results; restated, not re-scored)

| stage / probe | result | source |
|---|---|---|
| dev rows (16 gated) | 16/16 PASS (deskd, kilnworks tuned-on) | results 3-4 |
| mutants | 35/35 killed (10 only by unit tests) | results 5 |
| PD3-KILN (post hoc) | RPC 1.0/1.0; CODES 0.8/0.941; AUTH 0 wrong, unclear 17/17; TABLES 1.0; REC 1.0/1.0; FLOWS 17/7, edge 11/33, coverage 6/9 | results 4.1 |
| **PG-HO3, harborline** | **2 of 8** (HO3-DET, HO3-NW) | results 11.2 |
| HO3-RPC | 0.68 / 0.68 (Berths x4 `unread`, Vessels x4 **`not_registered`** while truth `implemented`) | 11.3.2-3 |
| HO3-CODES | 0.222 / 0.667 (54 truth, 18 extracted, 12 matched; 6 extra `UNKNOWN`) | 11.3.5 |
| HO3-AUTH | 0 wrong, unclear 22/22 = 1.00 (cap 0.50) | 11.3.4 |
| HO3-TABLES | 0 of 7 (no `CREATE TABLE` in the Go tree) | 11.3.6 |
| HO3-REC | coverage 1.00, agreement 0.04, 24/24 non-absent `login_failed`, S6 `order_vacuous` | 11.3.1 |
| HO3-FLOWS | recovered 0, truth flows replaying 0 of 8, `truth_not_scorable` | 11.3.1 |

### 1.2 Why round 3 is not a clean held-out (v), and the root causes (r, from the round-3 attributions)

**Process failures (v).**
- **P-a Independence.** The harborline author read the round-3 pre-registration and ran **before** the round-3 library freeze
  (contrary to round-3 section 6 "dispatched ... after the round-3 library freeze"). Its harness used a **positional**
  `AuthDriver.rpc(service, method, USERS, "token")` implied by prose, not the keyword-only signature that exists (r:
  `transport.py:345-353`), and crashed run 1.
- **P-b Runner changed after the held-out was opened**, twice (a reader shim, then the runner's own freeze check told to ignore its
  own entry). The scored run used runner `244e5e68...`, not the Amendment-2 bytes `64a988a1...`. The self-hash check was disabled
  for the runner's own entry. Two re-runs happened (run 2 refused, run 3 scored).
- **P-c** Rows were reported as "blind in the sense that no number or rule was changed after seeing any metric", but the runner and
  the author's exposure make them **post hoc runner, non-independent held-out** rows. **Round 4 reports round 3 as "2 of 8, not a
  clean blind held-out"** (the merge commit's wording) everywhere.

**Root causes of the six failing rows (r).**
1. **Generic helper registration not followed (unsafe).** Vessels: `expose[T any](r grpc.ServiceRegistrar, desc *grpc.ServiceDesc,
   impl T)` calls `r.RegisterService(desc, impl)`; the descriptor is the package var `vessels.Descriptor =
   &harborv1.Vessels_ServiceDesc`; the impl comes from `vessels.New(db, opts...)`, a functional-options constructor returning the
   `VesselsServer` interface. The extractor saw no registration at all, so D1.6 never fired and 4 truth-`implemented` rpcs were
   stated `not_registered`: a **confident wrong answer**, the direction the round-3 safety rule exists to prevent.
2. **`init()`-time registration into a map of interface values.** Berths: `registry.Offer(...)` from `init()`, an `InstallFunc`
   adapter, a map of `Installer` values called later through the interface. Reported `unread`
   (`registration_reachability_unknown`, `internal/berths/offer.go:15`): honest, still wrong in HO3-RPC.
3. **Interceptor passed as a method value through options.** `keeper.Unary` via `WithUnary(...)` into a
   `ChainUnaryInterceptor` assembled from options; classed `auth_not_literal`, so every rpc `unclear` (cap 0.50 failed). Its
   allowlist is a fluent builder `gate.Anonymous(...).AndService(...)` over generated `*_FullMethodName` constants and whole-service
   prefixes.
4. **CODES.** Bookings: sentinel errors (`%w`-wrapped) translated by a table in the `fault.Wire` interceptor (15 codes missed, 5
   `UNKNOWN` placeholders). Yard: `status.Error(codeFor[r], ...)` over an enum-keyed map (10 codes missed, `unread:
   grpc_code_not_literal` at `internal/yard/reasons.go:30`, one `UNKNOWN`).
5. **TABLES and FLOWS: the schema exists only in `harness.py`.** The Go tree has no `CREATE TABLE`; the extractor derives tables
   from Go source only (r: `go_grpc.py:139`), so TABLES was 0/7 by construction and the flow rules (which bind fields to tables)
   had nothing to bind. **Round 3's HO3-TABLES prediction 0.85-1.00 was a prediction error**: made without checking where the
   held-out schema lives, on a brief that never fixed the DDL location. Round 4 acknowledges it and decides the rule in advance
   (D5).
6. **REC and FLOWS: harness/library login mismatch.** `harbor.v1.LoginRequest` names the field `username`; the harness passed no
   `username_field`; the library default is `"name"` (r: `grpc.py:371`, `transport.py:351`), so every login sent `{name,
   password}` and failed. This is decided here from the proto's login message (D6), **not** by a runner patch.

Also carried: round-3 deviation 8.7 (a registration held in a package variable read only by unreached code reads
`not_registered`, should be `unread`), unfixed, folded into D1.3.

### 1.3 Scope (binding)

- **(1) Registration** (`goextract_grpc.go`): generic helpers and their instantiations, functional-options constructors returning
  an interface, interface-method dispatch over values stored at `init()`, func-type adapters; and a **module-wide safety rule**
  that makes a confident `not_registered` impossible while any registration call in the module is unresolved (D1).
- **(2) Auth** (`goextract_grpc.go`): interceptors as method values and through option functions/fields; allowlists from
  generated method-name constants, builder chains and service prefixes (D2). The round-3 rule "unknown => `unclear`, never
  `Public`" stands unchanged.
- **(3) Codes** (`goextract_grpc.go`): sentinel tables in interceptors, map lookups with constant keys, and no `UNKNOWN`
  placeholder for an untraced code (D3).
- **(4) Tables** (`go_grpc.py`): the schema comes from the Go source when it has DDL, else from the harness database (D5).
- **(5) Login** (`grpc.py`, `transport.py`): `username_field` / `password_field` discovered from the proto's login request
  message when the harness does not name them (D6).
- **(6) Process** (section 6.4): a held-out that cannot read the protocol, a contract check before its manifest, frozen and
  self-verified runner/scorer hashes recorded before it is opened, exactly one permitted re-run.
- **Out of scope:** streaming, Arm M, Arm L, layers A/R/X/B over rpcs, new flow rules (`workflows.py` stays frozen), the bridge,
  the proto reader, Streak, Docker.

## 2. Decisions: extractor (fixed before coding; `lattice/gocheck/src/goextract_grpc.go` only)

All new reachability stays inside round 3's `grpcWalk`; `g.wired`, `isWired` and every HTTP path stay byte-identical (D1.0 of
round 3 stands; P4-BC, mutant GW11 re-applied).

**D1 Registration.**
- **D1.1 Generic helpers.** A `RegisterService(d, impl)` / `Register<X>Server(r, impl)` call inside a function whose descriptor or
  impl is a **parameter** (generic or not) is resolved per grpc-wired call site of that function: the descriptor argument through
  round 3's `followDesc` (package var, local single assignment, `&X`, depth <= 4), the impl argument through D1.2. Each call site
  is one registration (file:line of the call site, `via` the helper's file:line). A type parameter `T` adds nothing: the argument's
  static type decides.
- **D1.2 Constructors.** Round 3's "constructor returning an interface: the one concrete type it returns" is extended to a
  constructor that builds a local (`s := &impl{...}` or `var s impl`), applies option functions to it (`for _, o := range opts {
  o(s) }`), and returns it: the concrete type is the local's. More than one concrete type returned: `unread`.
- **D1.3 Module-wide safety rule (replaces round-3 D1.6's trigger).** After D1.1-D1.4, if **any** `RegisterService` /
  `Register<X>Server` call in non-generated, non-`_test.go`, referenced code has a service the extractor could not determine
  (descriptor unresolved) or sits in referenced but unreached code (including a package var read only by unreached code: round-3
  deviation 8.7), then **every** declared service with no resolved registration is `unread` with
  `unread: registration_reachability_unknown` (file:line of each unresolved call), not `not_registered`. `not_registered` requires
  that every registration call in the module is resolved to a service and none names the declared one. An unreferenced function
  (zero references) stays dead, as round 3.
- **D1.4 Interface dispatch and adapters.** A method call `x.M(...)` on an interface-typed value inside a grpc-wired function
  (round 3 gives no callee) reaches `M` of every **module** concrete type that implements the interface **and** is converted to,
  assigned to, or stored as that interface (a composite literal, a conversion `T(f)`, an element/value of a map or slice literal or
  an `m[k] = v` / `append` in a grpc-wired or `init` function). A func type with a method calling the receiver (`type
  InstallFunc func(...)`, `func (f InstallFunc) Install(r) { f(r) }`) reaches every function value converted to it. Bounded at
  depth 4. Over-approximation decides reachability only.

**D2 Auth (round-3 D2 stands; additions).**
- **D2.1 Method values.** An interceptor given as a method value `x.M` resolves to method `M` of `x`'s static type (value or pointer
  receiver).
- **D2.2 Options and fields.** An interceptor argument to `grpc.UnaryInterceptor` / `ChainUnaryInterceptor` that is a field read
  (`c.unary`, `c.unary...`) resolves to every value assigned or appended to that field in a grpc-wired function or in a closure
  returned by an option function (`func WithUnary(f ...) Option { return func(c *cfg) { c.unary = append(c.unary, f...) } }`)
  whose call is grpc-wired. Depth <= 4.
- **D2.3 Generated constants.** A `<Svc>_<Method>_FullMethodName` constant (or any string constant) defined in a module file
  (generated `_grpc.pb.go` included) is a literal; the standard `grpc.health.v1.Health/Check` and `/Watch` constants of
  `google.golang.org/grpc/health/grpc_health_v1` are a fixed two-entry table (kilnworks's case).
- **D2.4 Builder allowlists.** A chain of method calls on one value whose methods each only add their string arguments to a field
  of the receiver (slice append, map set) builds a literal allowlist: the union of the arguments. An entry is a **prefix** only when
  the gate's check on that field uses `strings.HasPrefix` (read, not assumed); otherwise exact match. An entry the extractor
  cannot make literal leaves the interceptor `auth_not_literal` (round 3, `unclear`).
- **D2.5 Unchanged:** `Public` requires a literal allowlist naming (or prefix-matching, D2.4) the method, or no interceptor but
  benign ones and no handler-level check. Never a `Public` from an unread mechanism.

**D3 Codes.**
- **D3.1 Sentinel tables.** An interceptor (or a function every handler result passes through) that maps errors to codes by a
  literal table (`map[error]codes.Code`, a slice of `{err, code}` structs, or a `switch` / `if` chain with `errors.Is(err, S)`
  returning `status.Error(<literal code>, ...)`) gives a handler that returns sentinel `S`, or `fmt.Errorf("...%w...", S)`, the
  table's code for `S`. A sentinel absent from the table gets the table's default if it is literal, else `unread`.
- **D3.2 Constant-key maps.** `status.Error(m[k], ...)` / `status.Errorf(m[k], ...)` with `m` a package map literal and `k` a
  constant at the return site, or a parameter whose every grpc-wired call site passes a constant, gives `m[k]` (union over sites).
- **D3.3 No placeholder.** An error whose code is not traced is `unread: grpc_code_not_literal` (file:line), **never** `UNKNOWN`
  (round 3 emitted 6 `UNKNOWN` extras on harborline; they cost precision and were not admitted as gaps). A literal
  `codes.Unknown` in the source still yields `UNKNOWN`.
- **D3.4 Optional, allowed inside the budget:** a module error type with a `GRPCStatus() *status.Status` method returning
  `status.New(<literal>, ...)`; and a wrapper function whose every return is `status.Errorf(c, ...)` with `c` a parameter (code from
  the call site). Both appear in harborline (Berths, Vessels), so their effect there is post hoc.

## 3. Decisions: schema and login (fixed before coding)

**D5 Tables (`lattice/importer/frontends/go_grpc.py`).** Decision: **the Go source first, the harness database second**, as the
Python front ends read the SQLite schema of a built database (r: `outside.db_facts`).
- D5.1 If the extract's `tables` list is non-empty, it is used as today (byte-identical; no harness is run).
- D5.2 If it is empty **and** `studio.toml` names a `db` harness, the front end calls that `build_db` once into a fresh temporary
  directory **outside the target root**, reads the table names and columns with `db_facts`, deletes the directory, and records the
  tables with the added key `tables_source: "harness_db"` (absent when the source is Go). A table has no Go struct then
  (`structs.get(t, t)` as today). A `build_db` failure is `unread: harness_db_failed` (no tables), never an exception out of the
  model.
- D5.3 Route-level `tables` (which rpc reads or writes which table) still come only from the extractor's reading of SQL in Go
  code; D5 does not invent them. Flow rules (frozen) see whatever tables and route tables exist.
- **Acknowledged:** HO4-TABLES measured through D5.2 measures the reader of the author's own harness, not the extractor; the
  results report which branch decided (`tables_source`) and the row's meaning differs accordingly.

**D6 Login fields (`lattice/spec/grpc.py` `rpc_login`; `lattice/importer/transport.py` `AuthDriver.rpc` defaults only).**
- D6.1 `username_field` and `password_field` default to `None` (not `"name"` / `"password"`); an explicit value always wins
  (byte-compat for every harness that names them).
- D6.2 With `None`, the fields are discovered from the **login request message** of `service`/`method` in the contract (the
  transport's described descriptor set; the bridge `describe` op exists, r: `grpcbridge/call.go:44`): the password field is the
  one string field whose name contains `pass` (case-insensitive); the username field is the one other string field, or, if several
  remain, the one named exactly one of `username`, `user`, `name`, `login`, `email`, `handle` (first in that order).
  Zero or two candidates for either: the login raises `login_fields_ambiguous` (named fields), which `reconcile_with` already turns
  into `login_failed` (r: `_call_fresh`), never a silent guess.
- D6.3 `token_field` keeps its default `"token"` (not in scope; the harborline response named `token`, r 11.3.1).
- D6.4 The signature stays keyword-only after `credentials` (no positional acceptance). The held-out author is given the exact
  signatures (Appendix A), and the contract check (section 6.3) catches a mismatch before the manifest.

## 4. Decisions: never

No network target beyond loopback; no Docker; no write under any target root (D5.2's database lives in a temp dir outside it);
no git on a target; no LLM or API key in extraction, reconcile, flows, scorer, runner or contract check; no edit to `.proto`,
spec, `truth.json` or harness files of any corpus; no edit to the frozen list (section 9); no flow rule change; no runner patch
that maps a harness's arguments (round 3's fix 1 is not repeated for the held-out); the held-out scored once; `deskd`, kilnworks
and harborline never re-labelled blind.

## 5. Scorer rules (`dogfood/grpc_scorer4.py`, pure; round-2 `grpc_flow_scorer.py` reused unchanged for HO4-FLOWS)

- **S1, S2, S3, S5, S6, S7** as round 3 (S7: 0 wrong and unclear <= 0.50; a missing rpc counts `unclear`).
- **S8 SAFE.** Confident wrong states: extracted `not_registered` or `unimplemented` where truth is `implemented`; `implemented`
  where truth is `not_registered` or `unimplemented`; `Public` where truth is non-public; `Member` where truth is public. Pass =
  count 0. `unread` and `unclear` are never counted here (they are counted, as wrong, in RPC and AUTH).
- **S9 CODES precision.** An extracted `UNKNOWN` that the truth does not list is an extra (as round 3); an `unread` code is neither
  match nor extra (it lowers recall only).
- **S10 Provenance label (process).** The scorer, not the runner, labels every held-out row. It reads the runner's and its own
  sha256 recorded in Amendment 2 of this file, and the runner hash, scorer hash, run index and held-out hash the runner wrote into
  its output. Label `blind` only if: both hashes equal Amendment 2, the run index is 1, or 2 after a crash-before-metric with
  identical runner bytes (section 6.4), and the held-out hash equals Amendment 2. Any other case: every row `post_hoc` with the
  reason. The label is not a flag the runner can set.
- Cases in `dogfood/grpc_scorer4_cases.json`, >= 24: S8 (each wrong direction; `unread` not counted), S9 (an `UNKNOWN` extra, an
  `unread` not an extra), S10 (each hash mismatch, run index 2 after a crash with equal bytes, run index 2 with changed bytes, run
  index 3), plus the round-3 S5-S7 cases unchanged.

## 6. The new held-out (commissioning described; not written here)

### 6.1 Who, and isolation enforced (fixes P-a)

One **new** separate agent: not the `deskd`, kilnworks or harborline author, not any round-1 to 4 builder, not this author, not the
orchestrator, not a verifier. Dispatched by the orchestrator **after** the round-4 library freeze (`freeze4.sha256`, step 1 of
6.4) **is committed**, and the dispatch time and the freeze commit id are recorded in Amendment 2 (dispatch time later than the
commit's time, checked).

It runs in a fresh scratch tree `$SCRATCH4/author/` that contains **only**: `BRIEF.md` (6.2, verbatim), `HARNESS_API.md`
(Appendix A of this file, copied verbatim; the author reads that copy, never this file), and `TRUTH_SCHEMA.md` (6.2). **It must not
read** any `docs/results/*grpc*` file (prereg or results of any round), `lattice/**`, `dogfood/**`, `tests/**`, `deskd`,
kilnworks, harborline, any `heldout*` directory, or the orchestrator's notes. Enforcement, not request: (a) the author's working
directory and every path it is given are under `$SCRATCH4/author/`; (b) **after** it finishes and **before** the manifest, the
orchestrator audits the author's full tool-call transcript for any path or command touching `/home/user/lattice`, `/home/user/wt`,
`docs/results`, `heldout`, `deskd`, `kilnworks`, `harborline` or `lattice/`; a single hit discards the app and a new author is
commissioned (disclosed; the discarded app is never scored). The audit result (hit count, transcript length) is in Amendment 2.
It may read the public grpc-go and Go module documentation through the Go proxy (needed to build).

### 6.2 Brief, truth schema (fixed now; generic)

**Brief.** A grpc-go app over pure-Go SQLite (no cgo, no Docker, buildable with the Go proxy), in `$SCRATCH4/author/heldout4/<app>/`,
with a Python `harness.py` that follows `HARNESS_API.md` exactly. Shape minimums as round 3 (>= 3 registered services, >= 15 unary
rpcs, >= 1 `Unimplemented` rpc, >= 1 streaming rpc, >= 1 declared-but-unregistered service, >= 2 users of different roles, >= 2
request fields naming a seeded row by a natural key, >= 6 truth flows of >= 3 steps with `links`, >= 1 login rpc).
- "Organise the server the way a team maintaining a mid-sized production service would: packages per area, construction code
  separate from handlers, the indirection such codebases typically have. **No two services may be wired into the server by the
  same code idiom; no two services may report errors by the same idiom**; the authentication decision is written in an idiom of
  your own choice. Put the database schema wherever such a team would. Do not copy an example from any document."
- The brief names **none** of: closures, slices or maps of functions, generic helpers, `init` registries, adapters, mount/expose
  helpers, value receivers, descriptor variables, option functions, method values, interceptor chains, method-name constants,
  allowlist builders, prefixes, sentinel tables, code maps, where DDL lives, login field names. It does not say what failed before.
- The author also writes a **Go** replay (`cmd/replay`, its own client, no Python library) that runs every truth flow against its
  own server and exits 0 only if all pass; a failing flow is fixed or dropped before handing over.
- **After** code and truth are complete: `IDIOMS.md` (one line per service wiring, per error idiom, per auth mechanism, plus where
  the schema lives). Sealed: the runner and builder do not read it before scoring; it is read after HO4 (post hoc attribution).
- Diversity floor (checked by the orchestrator on `IDIOMS.md` without reading code): >= 3 distinct wiring lines for >= 3 services
  and >= 3 distinct error lines; otherwise returned to the author before the manifest (disclosed).

**Truth (`lattice.heldout.truth/4`)** = round-3's `/3` (per-rpc `effects`) plus: `login: {service, method, username_field,
password_field, token_field}`; per table `ddl: go | harness | other`; `conventions.step_index_base` stated.

### 6.3 Contract check before the manifest (`dogfood/grpc_contract4.py`, frozen in step 1; fixes P-a's crash class)

The orchestrator runs it once on the finished app, before hashing. It imports `harness.py` (`python -I`, a fresh process), builds
the database and the app exactly as `studio.toml` names them, logs in as every `USERS` handle through the harness's own
`RUNTIME.auth` (D6 applies), and replays each truth flow through the library's `Rpc` scenarios. It runs **no extractor, no model,
no reconcile, no flows recovery**, and prints only: import ok, build ok, per-handle login ok/failed (with the error class), per-flow
replay ok/failed (with the step index). On any failure, the author receives only that printout and may fix its harness or truth
(at most 2 rounds; each disclosed in Amendment 2). Still failing after 2: the app is used as is, and the failure is recorded as a
predicted cause in Amendment 2 before scoring.

### 6.4 Manifest, order and re-runs (binding; fixes P-b, P-c)

1. Round-4 build done. `sha256sum` of the section-9 "may change" files plus `dogfood/grpc_scorer4.py`, its cases,
   `dogfood/grpc_probes4.py` (with `--arm H4` built) and `dogfood/grpc_contract4.py` into `freeze4.sha256`, copied to
   `docs/results/data/2026-10-18-grpc-round4/` and committed (orchestrator, explicit paths). **Amendment 2 records, before the
   author is dispatched, the sha256 of `grpc_probes4.py`, `grpc_scorer4.py`, `grpc_contract4.py` and `freeze4.sha256`.**
2. The author is dispatched (6.1), writes the app, passes 6.3, writes `FILES.txt`; the transcript audit runs.
3. The orchestrator records `sha256sum` of every listed file (sorted) as `heldout4/freeze.sha256`, copies and commits it, and
   records its sha256, the audit result and the contract-check printout in Amendment 3, **before the held-out is opened** by any
   runner or person other than its author.
4. The runner checks, first, its **own** sha256 against Amendment 2 (never disabled, never special-cased; a runner whose own entry
   is excluded from its check is a defect), the scorer's, `freeze4.sha256` and the held-out listing; any mismatch aborts before any
   metric. It writes the hashes and the run index into its output, then runs `--arm H4` once.
5. **Exactly one re-run is permitted, and only for a crash before any metric is written**, with the runner bytes unchanged (a
   transient cause: timeout, port, disk). If the crash needs a runner change, the change is made in `dogfood/` only, its hash is
   recorded in an Amendment 4 **before** the re-run, and **every** row of that run is labelled `post_hoc` by S10. No second
   re-run in any case; a crash on the re-run is NOT RUN for every row.
6. The library is never edited after step 1. Any change of any kind after step 3 (runner, scorer, reader, harness mapping) marks
   every affected row `post_hoc`, reported beside the first run's value, never in its place.

## 7. Probes (runner `dogfood/grpc_probes4.py`; numeric pass rules, blind)

### 7.1 Arm 0 (static)

| id | probe | prediction (p) | pass |
|---|---|---|---|
| P4-BC | byte-compat promise (section 10) vs `be26a94` | all identical | **all** |
| P4-WIRE | new dev fixture `tests/grpc/fixtures/wiring4/` (builder-written, truth hashed before extractor code): a generic helper with a descriptor var and an options constructor; an `init` registry of interface values with a func adapter; an implementer of the same interface never instantiated (negative); an unresolvable descriptor beside an unregistered declared service (D1.3 negative); a package var read only by unreached code (round-3 deviation 8.7) | positives `implemented`; the un-instantiated implementer's service `not_registered`; the D1.3 case `unread`, not `not_registered`; 8.7 case `unread` | **exact on every service and rpc state; 0 S8 wrong** |
| P4-AUTHI | new dev fixture `tests/grpc/fixtures/authwire4/`: method-value interceptor; option-field chain; allowlist of generated constants; builder chain; prefix entry with `HasPrefix`; prefix-looking entry with exact `==` check; an unresolvable builder argument | allowlisted `Public`, others `Member`; the `==` case not prefix-matched; unresolvable `unclear` | **exact; 0 `Public` behind an unread mechanism** |
| P4-CODES | new dev fixture `tests/grpc/fixtures/codes4/`: interceptor map table; `errors.Is` switch; `%w`-wrapped sentinel; sentinel missing from the table with and without literal default; constant-key map; parameter-key map; an untraced error | each listed code; untraced `unread`, 0 `UNKNOWN` placeholders | **exact; 0 `UNKNOWN` not in the truth** |
| P4-TABLES | two dev fixtures: DDL in Go (round-3 path) and DDL only in `harness.py` | Go: as round 3, no `tables_source` key; harness: all tables, `tables_source: harness_db`; temp dir removed | **exact; 0 writes under either root** |
| P4-LOGIN | dev fixture `tests/grpc/fixtures/login4/`: login messages `{username,password}`, `{name,password}`, `{email,passcode}`, `{tenant,user,password}`, `{a,b}` (ambiguous), and an explicit `username_field` | discovered `username`, `name`, `email`/`passcode`, `user`, `login_fields_ambiguous`, explicit wins | **exact** |
| P4-DET | extract, model, reconcile, flows on N, `deskd`, kilnworks, harborline (dev), the new fixtures: 10 processes, random `PYTHONHASHSEED`, two roots | identical | **10/10** |
| P4-NW | no write under N, T, sessions, `deskd`, kilnworks, harborline, fixtures, `heldout4`; recording `claude` shim | 0 changed, 0 new, 0 calls | **all 0** |
| P4-UNIT | `tests/grpc/test_wiring4.py`, `test_auth4.py`, `test_codes4.py`, `test_tables4.py`, `test_login4.py` | 40-90 tests (round 3 predicted 25-45 and wrote 73) | **all green, >= 40** |
| P4-SCORE | `grpc_scorer4_cases.json` | 30-45 cases | **100%, >= 24** |
| P4-PROC | process self-test on a scratch copy: a runner byte changed (abort before metric); its own entry removed from its check (abort); a forced crash then re-run with equal bytes (`blind`); a re-run with changed bytes (`post_hoc`); a third run (refused) | each as stated | **5/5** |
| REG | `pytest tests/spec tests/importer tests/gocheck tests/mcp tests/studio tests/grpc` on `git archive be26a94` vs the worktree | same failing ids | **same set** (new tests extra) |

### 7.2 Arm G (dev; every row is development data)

| id | probe | prediction (p) | pass |
|---|---|---|---|
| PR4-N | library `reconcile` on N | byte-equal to round 3 (N names its login fields or uses `name`, which D6.2 discovers: p) | **exact** |
| PR4-DESK | `reconcile_with` on `deskd`, library output only | as round 3: S5 1.00 / 1.00, 0 `auth_refused` | **exact counts** |
| PR4-SESS | sessions fixture | as round 3 | **exact** |
| PF4-N, PF4-DESK | flows on N and `deskd` | N 17/17; deskd 41 / 20, edge 21/34, coverage 4/7 | **identical counts to round 3** |
| PF4-LEAK | no process or bridge after reconcile, flows, a forced startup failure, a D5.2 harness-db build | 0 | **0** |
| PR3-ALL | round-3 runner `grpc_probes3.py --arm 0/G` (`244e5e68...`) at round-4 code | every round-3 PASS row still PASS | **all PASS** (a declared byte change, if any, listed in Amendment 1 before running) |
| PX4 | wall: extract on harborline; reconcile on `deskd`; flows on `deskd`; D5.2 on the harness fixture | 0.03-2 s; 1-10 s; 5-30 s; 0.2-5 s | **<= 30; <= 30; <= 180; <= 30 s** |
| PD4-KILN (**post hoc, tuned-on, reported only**) | kilnworks by `--arm H4`'s scorer, freeze `d7a5ea3c...` | RPC 1.0/1.0; CODES 0.8-0.95 / 0.94-1.0; AUTH 0 wrong, unclear 0.0-0.2 (D2.3 health table makes `openDoors` literal); TABLES 1.0; REC 1.0/1.0; FLOWS as round 3 (17/7, 11/33, 6/9) | **reported** |
| PD4-HARBOR (**post hoc, tuned-on, reported only**) | harborline by `--arm H4`'s scorer, freeze `1c51ee0e...` checked; its positional `AuthDriver.rpc` call handled by the round-3 reader shim in the dev path only (disclosed; never on `heldout4`) | RPC 0.92-1.00 / 0.92-1.00; CODES 0.55-0.90 / 0.85-1.00; AUTH 0 wrong, unclear 0.0-0.40; TABLES 1.0 (`harness_db`); REC coverage 1.00, agreement 0.85-1.00 (D6 finds `username`); FLOWS recovered 5-60, verified 0-8, edge recall 0.00-0.50, coverage 0-5 of 8; S8 0 | **reported** |

### 7.3 Held-out (PG-HO4, scored once after Amendment 3; section 6's app)

Gates are round 3's, not loosened; HO4-SAFE is new.

| id | metric | prediction (p) | pass (gated) |
|---|---|---|---|
| HO4-RPC | rpc recall / precision, state included (`unread` wrong) | 0.55-1.00 / 0.60-1.00 (point 0.85 / 0.88) | **>= 0.90 / >= 0.95** |
| HO4-SAFE | S8 confident-wrong count (rpc states and auth) | 0-4 (point 0; P(0) about 0.7) | **0** |
| HO4-CODES | micro recall / precision (implemented, OK excluded) | 0.25-0.85 / 0.75-1.00 (point 0.50 / 0.92) | **>= 0.70 / >= 0.90** |
| HO4-AUTH | S7 | wrong 0 (P 0.85); unclear 0.00-1.00 (point 0.55) | **0 wrong and unclear <= 0.50** |
| HO4-TABLES | tables recall, `tables_source` reported | 0.85-1.00 (point 1.00; P(pass) about 0.85, most of it the D5.2 reader if the author puts DDL outside Go) | **>= 0.90** |
| HO4-REC | S5 + S6 + 0 `auth_refused` + disagreements isolated + 0 `login_failed` on truth-implemented non-public rpcs | coverage 0.95-1.00; agreement 0.60-1.00 (point 0.92); P(login works) about 0.8 | **coverage >= 0.95, agreement 1.00, supplementary 0, S6 not FAIL, 0 `auth_refused`, 0 `login_failed`, all isolated** |
| HO4-FLOWS | D4 + S2, round-2 scorer unchanged | recovered 0-60; verified 0-10 (point 3); edge recall 0.00-0.50 (point 0.20); coverage 0-5 (point 2); 0 vbf | **verified >= 1, 0 vbf, edge recall >= 0.30, coverage >= 2** |
| HO4-DET | extract, describe, model, reconcile, flows: 10 processes, 2 roots | identical | **10/10** |
| HO4-NW | `heldout4` root, recording shim | 0, 0, 0 | **all 0** |

**Probe count: 31** (Arm 0: 12; Arm G dev: 10, of which PD4-KILN and PD4-HARBOR reported only; held-out: 9). **Gated: 29.**
Preconditions, not probes: the transcript audit (6.1), the contract check (6.3), the dispatch-after-freeze check (6.1).

**Honest predictions.** Round 3 predicted P(all 8) 0.15-0.25 and got 2 of 8; this round's prior is lower, not higher. The new
author is told to vary wiring **and** error idioms, so HO4-RPC and HO4-CODES depend on shapes outside D1-D3 again: P(HO4-RPC pass)
about 0.45, P(HO4-CODES pass) about 0.25. HO4-AUTH: P(0 wrong) about 0.85, P(unclear <= 0.50) about 0.4 (one unread gate still fails
it by design). HO4-SAFE: about 0.7 (D1.3 should make `not_registered` safe; a wrong `unimplemented` from an unread method set is
the remaining route). HO4-TABLES about 0.85. HO4-REC about 0.4 (agreement 1.00 is strict; D6 may meet an ambiguous login message).
HO4-FLOWS about 0.25 (the flow rules have one unconfounded unseen reading, kilnworks: edge recall 0.33, coverage 6/9). HO4-DET and
HO4-NW about 0.95 each. **P(all 9 held-out rows pass) about 0.03-0.08. P(>= 5 of 9) about 0.35.**

## 8. Mutants (`dogfood/grpc_mutants4.py`, scratch copy; control green first; predicted 27/27 new + 35/35 re-applied)

Killed when a gated probe that passes on the control fails or a named unit test fails (disclosed per mutant).

| id | mutant | predicted killer |
|---|---|---|
| GW4-1 | D1.1 off: a register call with a parameter descriptor/impl is not resolved at call sites | P4-WIRE, P4-UNIT |
| GW4-2 | D1.1 resolves only the first call site of a helper | P4-WIRE (second service) |
| GW4-3 | D1.2 off: an options-applying constructor returning an interface is not followed | P4-WIRE |
| GW4-4 | D1.3 off: an unresolved registration does not block `not_registered` (round-3 behaviour) | P4-WIRE (S8), P4-UNIT |
| GW4-5 | D1.3 too broad: an unreferenced function's unresolved call also blocks `not_registered` | P4-WIRE (dead case) |
| GW4-6 | round-3 deviation 8.7 left: a package var read only by unreached code is not counted | P4-WIRE |
| GW4-7 | D1.4 off: interface method calls unresolved | P4-WIRE (init registry) |
| GW4-8 | D1.4 too broad: implementers never instantiated also reached | P4-WIRE (negative) |
| GW4-9 | D1.4 func-type adapter method not followed | P4-WIRE |
| GU4-1 | D2.1 off: method-value interceptors unresolved | P4-AUTHI |
| GU4-2 | D2.2 off: option-field flow not followed | P4-AUTHI |
| GU4-3 | D2.3 off: generated `FullMethodName` constants not literal | P4-AUTHI |
| GU4-4 | D2.4: prefix assumed without `HasPrefix` evidence | P4-AUTHI (wrong `Public`) |
| GU4-5 | D2.4: prefix entries matched exactly even with `HasPrefix` | P4-AUTHI |
| GU4-6 | D2.4: an unresolvable builder argument dropped (allowlist treated as literal) | P4-AUTHI (`Public` behind unread) |
| GK4-1 | D3.1 off: interceptor sentinel tables not read | P4-CODES |
| GK4-2 | D3.1: `%w` wrapping not followed | P4-CODES |
| GK4-3 | D3.2 off: constant-key map lookups not read | P4-CODES |
| GK4-4 | D3.3 off: `UNKNOWN` placeholder restored for untraced errors | P4-CODES |
| GT4-1 | D5: harness-db tables override non-empty Go tables | P4-BC, P4-TABLES |
| GT4-2 | D5.2 builds the database under the target root | P4-TABLES, P4-NW |
| GL4-1 | D6 off: default `username_field="name"` restored | P4-LOGIN |
| GL4-2 | D6.2: ambiguous message picks the first string field | P4-LOGIN |
| GL4-3 | D6.1: discovery overrides an explicit field | P4-LOGIN, P4-BC |
| GS4-1 | scorer S10: a runner hash mismatch still labelled `blind` | P4-SCORE, P4-PROC |
| GS4-2 | scorer S8: `unread` counted as confident wrong / `not_registered` vs `implemented` not counted | P4-SCORE |
| GS4-3 | runner: its own entry excluded from its freeze check (round 3's fix 2) | P4-PROC |

**Mutant count: 27 new** (GW4-1..9, GU4-1..6, GK4-1..4, GT4-1..2, GL4-1..3, GS4-1..3) **+ 35 re-applied** (round 3's GW1-GW11,
GU1-GU3, GC1-GC2, GS1-GS2 and GR1-GR17) **= 62.** A re-applied mutant that no longer applies textually is re-expressed and disclosed.

## 9. Frozen and changeable files (exact; hashes r at `be26a94`)

**May change, with line budgets (`git diff --numstat` insertions + deletions against `be26a94`):**

| file | sha256 at `be26a94` | what | budget |
|---|---|---|---|
| `lattice/gocheck/src/goextract_grpc.go` (1651 lines) | `b2eda6fd8264445a172685ccb40c2bc1559c0effd43e5342743f83eef1a9315e` | D1.1-D1.4, D2.1-D2.4, D3.1-D3.3 (D3.4 optional) | **<= 400** |
| `lattice/importer/frontends/go_grpc.py` | `d22aefc60c5210ac515d1d8d0dc865d6dc803b38c45fd44f6ccd0f1d8c2f4ca4` | D5 only | **<= 50** |
| `lattice/spec/grpc.py` | `edd41b1f3c28817975b66f9fad65219d45f9e7a33579ede6c52acbdfcef527d3` | `rpc_login` and one private helper (D6); AST of every other top-level def identical | **<= 50** |
| `lattice/importer/transport.py` | `5f7e74dfca1b90f739d5165530094eb8f64be9fe468794c89cd23897fa9acd86` | `AuthDriver.rpc` defaults `None` (D6.1); nothing else | **<= 6** |

**Creates:** `dogfood/grpc_probes4.py`, `grpc_scorer4.py`, `grpc_scorer4_cases.json`, `grpc_mutants4.py`, `grpc_contract4.py`;
`tests/grpc/test_{wiring4,auth4,codes4,tables4,login4}.py`; `tests/grpc/fixtures/{wiring4,authwire4,codes4,tables4,login4}/**`
(each `truth.json` hashed into `freeze-dev4.sha256` before any round-4 library code); the round-4 results doc and
`docs/results/data/2026-10-18-grpc-round4/**`; this file only by appended Amendments.

**Stays frozen (empty diff vs `be26a94`; the runner checks the hashes):** `goextract_proto.go` `ec4aa315...`, `goextract.go`
`652e7007...`, `load.go` `72a35ec4...` and every other `lattice/gocheck/**` file; `lattice/importer/workflows.py` `5eba7e8a...`;
`lattice/importer/outside.py` (D5 calls `db_facts`, never edits it); `dogfood/grpc_flow_scorer.py` `8d896ee0...` and cases;
`dogfood/grpc_probes3.py` `244e5e68...`, `grpc_scorer3.py` `019db509...`, `grpc_scorer3_cases.json` `be3ccbcb...`, every earlier
runner and mutants file; `lattice/grpcbridge/**`; existing `tests/grpc/**`; corpora `deskd`, kilnworks, harborline (read only); all
round-1 to 3 docs and data directories (errata go in the round-4 results only).

## 10. Byte-compat promise

Versus `be26a94`: every HTTP output of rounds 1-3's promise **byte-identical**; `gocheck extract` JSON and `RepoModel` on N, T,
sessions, `deskd` and kilnworks **byte-identical** (p: direct or round-3 shapes, literal allowlists or round-3 kinds, DDL in Go);
`reconcile` / `reconcile_with` on N, sessions and `deskd` byte-identical (p: each harness names its login fields, or its login
message makes D6.2 discover the same field it used); flows on N and `deskd` byte-identical. **Declared not byte-compatible:**
extract/model on harborline (the intended fix); kilnworks AUTH (if D2.3's health table makes its allowlist literal: listed in
Amendment 1 after the first dev run, before any held-out step); `UNKNOWN` codes replaced by `unread` wherever round 3 emitted a
placeholder; the key `tables_source` only on a D5.2 model; the error `login_fields_ambiguous` only where D6.2 cannot decide. A
difference elsewhere is a finding, not re-labelled.

## 11. Risks, and what this cannot show

1. **Tuned on harborline.** D1-D3, D5, D6 were chosen after reading it; PD4-HARBOR looks good by construction and is never gated.
2. **Open-ended idioms, now for errors too.** The brief asks for distinct error idioms; D3 reads four; a fifth shape is `unread`
   (honest, lowers recall). Expected failure mode, not a reason to widen gates.
3. **D5.2 can make TABLES nearly trivial** when the author puts DDL in the harness; reported via `tables_source`.
4. **D6 is a heuristic** over field names; it refuses rather than guesses, which converts an ambiguous login into `login_failed`
   and an HO4-REC FAIL.
5. **The contract check runs the library's login and `Rpc` replay on the held-out before scoring.** It exposes only pass/fail lines,
   no extractor output; it is still a look at the app by a library path, disclosed.
6. **Isolation is audited after the fact.** A transcript audit can miss an indirect read (a shell glob); the scratch-only working
   tree is the primary control.
7. **n = 1 app, one author, one shared grpc-go library** (round-2 risk 5).
8. **Cannot show:** Streak/MySQL, the build round, streaming, non-Go servers, layers A/R/X/B on rpcs, flow-rule changes.

## 12. Blind vs post hoc, and commands

Fixed now: D1-D6, S8-S10, every prediction, gate and bound, the brief, the contract check's outputs, the re-run rule, the mutant
list. A missed prediction is a finding. HO4 is scored **once** after Amendment 3; any later change is post hoc (S10), reported
beside the first value. PD4-KILN, PD4-HARBOR and every `deskd` row are dev/post hoc in every run. **Pass:** every gated row of
7.1-7.3, mutants 62/62 (by probes or named unit tests, disclosed), P4-BC, REG, and S10 labels `blind` on the held-out rows. Arm M and
Arm L NOT RUN, which is not a pass.

```sh
export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1
SCRATCH=<build node scratchpad>/grpc4
export LATTICE_GOCHECK_CACHE=$SCRATCH/gocache LATTICE_GRPCBRIDGE_CACHE=$SCRATCH/bridgecache
git archive be26a94 | tar -x -C "$SCRATCH/base"                                       # byte-compat + REG baseline
sha256sum tests/grpc/fixtures/{wiring4,authwire4,codes4,tables4,login4}/truth.json > "$SCRATCH/freeze-dev4.sha256"  # before library code
uv run python -m pytest tests/grpc -q
uv run python dogfood/grpc_probes4.py --arm 0 --base "$SCRATCH/base" --deskd <deskd> --kiln <kilnworks> --harbor <harborline> --out docs/results/data/2026-10-18-grpc-round4
uv run python dogfood/grpc_probes4.py --arm G --base "$SCRATCH/base" --deskd <deskd> --kiln <kilnworks> --harbor <harborline> --out ...  # PD4-* post hoc
uv run python dogfood/grpc_probes3.py --arm 0 ...; uv run python dogfood/grpc_probes3.py --arm G ...                                       # PR3-ALL, un-nested
uv run python dogfood/grpc_mutants4.py --scratch "$SCRATCH/m" $(seq -f GW4-%g 1 9) $(seq -f GU4-%g 1 6) $(seq -f GK4-%g 1 4) GT4-1 GT4-2 \
  GL4-1 GL4-2 GL4-3 GS4-1 GS4-2 GS4-3 $(seq -f GW%g 1 11) GU1 GU2 GU3 GC1 GC2 GS1 GS2 $(seq -f GR%g 1 17)
sha256sum lattice/gocheck/src/goextract_grpc.go lattice/importer/frontends/go_grpc.py lattice/spec/grpc.py lattice/importer/transport.py \
  dogfood/grpc_scorer4.py dogfood/grpc_scorer4_cases.json dogfood/grpc_probes4.py dogfood/grpc_contract4.py > "$SCRATCH/freeze4.sha256"
# orchestrator: commit freeze4 + Amendment 2 (hashes) -> dispatch author (6.1) -> contract check (6.3) -> audit -> heldout4 freeze + Amendment 3, then once:
uv run python dogfood/grpc_probes4.py --arm H4 --heldout <heldout4> --freeze .../freeze4.sha256 --out .../heldout4
uv run ruff check <changed .py>; uv run ruff format --check <created .py>; gofmt -l lattice/gocheck/src tests/grpc/fixtures/{wiring4,authwire4,codes4,tables4,login4}
```

**File ownership (build node).** Edits: section 9 "may change" only, within budgets. Creates: section 9 "creates". Not touched:
the frozen list; `heldout4/` and its `IDIOMS.md` (runner only); the author's scratch tree; Streak. No commits by the builder; the
orchestrator stages explicit paths only (never `git add -A`, never `git add .`).

## Appendix A: `HARNESS_API.md` (copied verbatim to the author; the only library text it sees)

A harness is a Python module `harness.py` next to `studio.toml`, which names `[app] app = "harness:make_app"`, `db =
"harness:build_db"`, optionally `runtime = "harness:RUNTIME"`. The module defines `USERS: dict[str, tuple[str, str]]` (handle ->
(login name, password)) and `RUNTIME`, built with:

```python
from lattice.importer.transport import AuthDriver
AuthDriver.rpc(service: str, method: str, credentials: dict[str, tuple[str, str]], *,
               username_field: str | None = None, password_field: str | None = None,
               token_field: str = "token", scheme: str = "bearer") -> AuthDriver
```

Everything after `credentials` is **keyword-only**; passing it positionally is an error. With `username_field` / `password_field`
left `None`, the fields are taken from the login request message (one string field containing `pass`, one other string field);
name them explicitly if your message has more than two string fields. `token_field` is a dotted path into the response. The
token is sent as `authorization: Bearer <token>`. `build_db(path)` creates and seeds a SQLite file at `path`; `make_app(db_path)`
returns how to start the server. (The builder pastes the exact `make_app` return type and `process_runtime` signature from the
frozen code into this appendix in Amendment 1, unchanged in meaning, before the freeze.)

## 13. Questions for the orchestrator

- **Q1 Verifier corrections.** The task referred to "verifier corrections in the session summary below"; no summary reached this
  author. Section 1.2 (v) uses only what the task stated (author read the prereg and ran before the freeze; runner changed after
  opening; self-check disabled). Are there further corrections (e.g. a different HO3 figure or a row re-labelled) to add?
- **Q2 Where to build.** Round 4 builds on `be26a94` (`next-phases`, already merged with Spike I rounds 1-3). A new worktree and
  branch (name?), or reuse `/home/user/wt/np-grpc` after fast-forwarding it? Commit this file in `next-phases` first?
- **Q3 D1.3 strength.** The module-wide rule turns every unregistered declared service into `unread` while any registration call is
  unresolved; on an app with one opaque registration, the genuinely unregistered service loses its correct `not_registered`
  (an RPC miss, never S8). Accept that trade?
- **Q4 D5 (schema).** Accept "Go DDL first, harness database second" with `tables_source` reported, knowing HO4-TABLES then partly
  measures a reader of the author's harness? Alternative: gate TABLES only when `truth.tables[*].ddl == go`, report otherwise.
- **Q5 D6 (login).** Change the library default to discovery (D6.1 `None`) rather than keep `"name"`? The change touches
  `transport.py` (6 lines) outside the round-3 change set.
- **Q6 Contract check (6.3).** Allow the library's login and truth-flow replay on the held-out before the manifest (pass/fail only,
  at most 2 fix rounds), or require the author's Go replay only and accept round-3-style crash risk?
- **Q7 Isolation.** Is a transcript audit available for the dispatched author (tool-call log), and can its working directory be
  restricted to `$SCRATCH4/author/`? If not, how should independence be enforced?
- **Q8 Brief.** Approve adding "no two services report errors by the same idiom" (raises difficulty for CODES) and the Go replay?
- **Q9 Gates.** Keep round 3's thresholds unchanged and add HO4-SAFE (0 confident-wrong) as gated? Is the unclear cap still 0.50?
- **Q10 Budgets.** 400 lines for `goextract_grpc.go` (round 3 used 297 of 300) with D3.4 optional: enough, or split D3 into a
  later round?
- **Q11 Re-run rule.** Confirm: one re-run only after a crash before any metric; unchanged bytes keep `blind`, changed bytes force
  `post_hoc` on every row; no third run under any circumstance.
- **Q12 PD4-HARBOR shim.** Harborline's harness passes `token_field` positionally; allow the round-3 reader shim on the dev path
  only (never on `heldout4`), or score PD4-HARBOR without it (REC/FLOWS then crash or fail)?

## Amendment 1 (orchestrator rulings, 2026-10-18; defaults, overturnable)

Q1: no further corrections exist beyond those in section 1.2. Q2: build in the new worktree `/home/user/wt/i4` (branch `spike-i-grpc-round4`, from be26a94 plus this file); this file is committed on `next-phases` first. Q3: accepted (D1.3). Q4: accepted: Go DDL first, harness database second; HO4-TABLES is scored and the `tables_source` is reported beside it. Q5: accepted (6 lines in `transport.py`). Q6: accepted: pass/fail output only, at most 2 fix rounds, each recorded. Q7: the orchestrator audits the author's tool calls after the fact from the agent transcript (journal.jsonl and the agent's own jsonl); the brief fixes its working directory to `$SCRATCH4/author/` and forbids reading `/home/user/lattice`, `/home/user/wt` and any `docs/results/*grpc*`; any violation is disclosed and marks the held-out `compromised`. Sequence: the build and library freeze come first, and the author is dispatched only after `freeze4.sha256` is committed. Q8: approved. Q9: confirmed (HO4-SAFE gated, cap 0.50). Q10: 400 lines for `goextract_grpc.go`. Q11: confirmed. Q12: the shim is allowed on the dev row PD4-HARBOR only.
