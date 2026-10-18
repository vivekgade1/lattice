# gRPC round 3 (Spike I3): registration reachability, auth that is never Public by default, reconcile scored on library output only: pre-registration

## 1. Status, and the exact gap

2026-10-18. **Written BEFORE any round-3 code; no model was called, no API key was used; nothing is committed or staged.**
This file is the only file this step writes (in `/home/user/lattice/docs/results/`, the `next-phases` checkout; see Q1).
Round 2 lives on branch `spike-i-grpc` (worktree `/home/user/wt/np-grpc`, HEAD `5926008`); it is **not yet merged** into
`next-phases` (HEAD `3679ab2`, r). The relayed user request is "merge and then run round 3": this document takes that as
(a) merge `spike-i-grpc` into `next-phases` first, then (b) build round 3 on the merge commit. It infers no other ruling.

Read while writing (read only): round-2 prereg `2026-10-17-grpc-round2-prereg.md` (with Amendments 1 and 2), round-2 results
`2026-10-17-grpc-round2-results.md` (section 10, the gated kilnworks stage), the extractor `lattice/gocheck/src/goextract_grpc.go`,
`goextract_proto.go`, the wiring walk in `goextract.go` (lines 420-487) and `load.go` (`AllFuncs`, `LitFunc`), and the
kilnworks files `mount.go`, `gate.go`, `main.go` plus the receiver lines of its handlers. Templates: `2026-10-16-change-impact-prereg.md`,
`2026-10-17-typed-conditions-round2-prereg.md`. Labels: **(m)** measured, **(r)** read from code or data while writing this,
everything else a **prediction (p)**.

**This is a development-set round for every corpus it can already see.** `deskd` (round-1 held-out, `freeze.sha256` hash
`8670bf84...`) and **kilnworks** (round-2 held-out, `freeze.sha256` hash `d7a5ea3c642e06b4ccb18983ad185896afec3cae8b15361b7d5941479803868e`,
m, verified while writing this) have both been scored and read by this author. **Both are development data now, never a blind
or held-out claim.** The scope of this round (section 1.3) was chosen by reading kilnworks's failures, so every kilnworks
number in round 3 is tuned-on. The only gated held-out of round 3 is a NEW app written by a NEW separate agent (section 6),
manifest hash recorded before any gated probe, scored once.

**Environment (m, this container, 2026-10-18).** `go` and `uv` work (round 2: `go1.24.7`, `uv 0.11.32`, Python 3.13.16).
No Docker daemon, no Streak checkout. **Docker-free arms (0 and G, Go servers over pure-Go SQLite) are the primary and only
gated arms.** Arm M (Streak/MySQL) and Arm L (build round) are out of scope and reported NOT RUN in the results' first line.
Streaming stays `not supported`.

### 1.1 Round 2, blind vs post hoc (m; not re-scored here, restated so the two are never merged)

| stage / probe | blind (first and only scored run) | post hoc | source |
|---|---|---|---|
| Arm 0 dev rows (6) | 6/6 PASS | | results section 3 |
| Arm G dev rows (10) | 10/10 PASS (deskd tuned-on) | | section 4 |
| mutants GR1-GR17 | 17/17 killed (7 only by unit tests, not the named probe) | | section 5 |
| **PG-HO2, kilnworks, by the runner's rule** | **3 of 7** (HO2-TABLES, HO2-REC, HO2-DET) | none | section 10.3 |
| **PG-HO2, kilnworks, strict (verifier's correction)** | **2 of 7** (HO2-TABLES, HO2-DET). HO2-REC is FAIL on library output: `reconcile_with` answered **4 of 20** rpcs; the other 16 were answered by the runner's supplementary `_call_fresh` calls, which the round-2 prereg (scope 1.2(a): "the runner never re-implements reconcile") does not allow to count | none | section 10.3 caveat |
| HO2-RPC | 0.30 / 0.30 (6 of 20; 14 rpcs `not_registered`) FAIL | | |
| HO2-CODES | 0.125 / 1.00 FAIL | | |
| HO2-AUTH | 2 wrong of 3 (`ListStudios`, `LookupGlaze` labelled Public, truth Member) FAIL (hard) | | |
| HO2-FLOWS | 0 recovered, 0 verified, edge recall 0/33, coverage 0/9 FAIL (S2 `no_verified_flow`) | | |
| P2-NW (kilnworks root) | PASS | | |

**Round 3 reports the strict figure (2 of 7) as the round-2 blind result.** No post hoc kilnworks number exists yet; round 3's
PD3-KILN (section 7.2) is the first, and it is labelled post hoc and tuned-on everywhere.

### 1.2 Root causes (r: code read at `c0cee748` / `652e7007`, kilnworks read after its scoring)

1. **Registration reachability.** `isWired` (goextract.go:487) answers from a walk that starts at `main`/`init` and visits
   *function bodies* only (goextract.go:449-483). kilnworks registers `Passport` inside a func literal in a **package-level**
   `var mounts = []mount{...}` and `Projects`/`Pieces` in `mountWorkshop` (named in that initializer), executed by
   `for _, m := range mounts { m(srv, db) }`. Package-level initializers are never walked, `AllFuncs` (load.go:421) never
   enumerates literals outside a function body, and a call through a func-typed variable has no `Callee`. So both
   registrations became `excluded: unwired, "not reachable from main"` and 14 rpcs `not_registered`; the model had 3 routes and
   codes, reconcile coverage and flows collapsed.
2. **Unknown interceptor defaults to Public.** `rpcAuth` (goextract_grpc.go:1320-1386) keeps only interceptors whose kind is
   not `other`; `classifyInterceptor` returns `other` whenever it cannot trace `UNAUTHENTICATED` (kilnworks: `gate.unary`
   refuses through `g.resolve`, a value-receiver method returning a sentinel `errNoTicket`). With no auth interceptor left,
   the default branch (line 1382) says `Public`. Not understanding the auth mechanism produced the unsafe answer.
3. **Reconcile coverage.** `reconcile_with` iterates `model.routes` only (grpc.py ~487), so a mis-extracted or
   declared-but-unregistered rpc is never called by the library (also round-2 deviation 3, `Reports/Backlog`).
4. **Flows.** No flow rule was exercised on kilnworks: with 3 routes there was no writing rpc. Whether F2-F4 work on that app is
   unknown, not failed.

### 1.3 Scope (binding)

- **(1) Extractor registration reachability** (`goextract_grpc.go` only): registrations reached through package-level
  initializers, slices/maps/arrays of func values, calls through func-typed variables, helper functions taking a
  `grpc.ServiceRegistrar`/`*grpc.Server` parameter, value-receiver and promoted methods of the impl, constructors returning
  an interface, and `RegisterService` with a descriptor held in a variable (D1).
- **(2) Unknown interceptor => `unclear`, never `Public`** (D2).
- **(3) HO3-REC scored on library output only**, with a named coverage bound; `reconcile_with` calls every non-streaming rpc
  of the contract, not only `model.routes` (D3).
- **(4) Flow rules and gates unchanged** (`workflows.py` rpc section frozen at round 2; S1/S2 vacuity-aware gates as round 2).
- **Out of scope:** streaming, Arm M, Arm L, layers A/R/X/B over rpcs, new flow rules, the bridge, the proto reader.

## 2. Decisions: extractor reachability (fixed before coding; `goextract_grpc.go`)

**D1.0 A separate grpc-wired set.** Reachability for registrations and interceptors is computed into a new set
`g.grpcWired` (a superset of `g.wired`), used **only** by `findGrpcRegistrations` and `interceptorFuncs`. `g.wired`,
`isWired` and every HTTP path stay byte-identical (the HTTP outputs cannot move; P3-BC, mutant GW11).

**D1.1 Package-level initializers (R1).** When a grpc-wired function references a package-level var `V` (identifier, or
selector into a module package), `V`'s initializer is walked as if it were a body: every func literal in it becomes a
grpc-wired function, and every function named in it (identifier or selector) becomes grpc-wired. Because `LitFunc` needs a
parent (load.go:389), the extractor builds one synthetic parent `Func` per package var (`Name: "var." + V`, file and position
of the var) inside `goextract_grpc.go`; `load.go` and `goextract.go` stay frozen (Q4 if that proves impossible).
Registrations inside those literals are enumerated by `findGrpcRegistrations` (today it iterates `AllFuncs`, which misses them).

**D1.2 Calls through func values (R2).** A call `x(args)` whose `Callee` is nil and whose `x` is a func-typed local, range
variable, parameter or struct field resolves to the set of func values that can flow into `x`: the elements of a composite
literal ranged over (slice, array, map values), a local's single assignment, a struct field set in a composite literal
anywhere in the module, a parameter's arguments at every grpc-wired call site. Bounded by depth 4. Over-approximation is
allowed (it only decides reachability).

**D1.3 Helpers (R3).** A `Register<X>Server(r, impl)` or `r.RegisterService(d, impl)` call counts wherever `r` has a type
that implements `grpc.ServiceRegistrar` (a parameter, a field, an interface value), not only a `*grpc.Server` from
`grpc.NewServer`; the helper is reached by D1.1/D1.2 or an ordinary call.

**D1.4 Impl types (R4).** `implType` follows: a constructor call to its return statements (a constructor whose result type is
an interface: the concrete composite literal or `&T{}` it returns, single concrete type only, else `unread`); `MethodOf`
answers from the impl's full method set (a pointer impl includes value-receiver methods; promoted methods of embedded
structs included, the generated `Unimplemented*Server` embedding still answers `unimplemented` via `isStub`).

**D1.5 Descriptor variables (R5).** `descOf` follows an identifier through a local single assignment (`e.valuesOf`) and a
package var whose value is an `&X`, `X`, selector or another identifier, depth <= 4, before giving up.

**D1.6 Never a confident `not_registered` from an unread shape (R6, the safety rule).** A service is `not_registered` only
when no `Register<X>Server` / `RegisterService` call naming it exists in non-generated, non-`_test.go` code, or every such
call sits in a function with **zero references** anywhere in non-test code (dead code). A registration call that exists, is
referenced, but is not grpc-wired by D1.1-D1.3 makes its rpcs `unread` with `unread: registration_reachability_unknown`
(file:line of the call), and the `excluded` entry keeps today's shape. `unread` still counts as a wrong state in HO3-RPC:
this rule changes a wrong confident claim into an admitted gap, it does not earn points.

**D2 Auth: unknown interceptor => `unclear`.**
- **D2.1 Benign vs unknown.** An interceptor `classifyInterceptor` labels `other` is **benign** only when every `return` of its
  body (and every assignment to a named error result in a deferred literal) returns the handler call's results, or returns an
  error whose code is traced to a code outside `{UNAUTHENTICATED, PERMISSION_DENIED}`, and `errOfFunc` reports no unread error
  source. Otherwise its kind becomes **`auth_unknown`** with `reason` naming the first untraced return (file:line). (r:
  kilnworks `steady` is benign: `return next(...)` plus a deferred INTERNAL; `gate.unary` is `auth_unknown`: `return nil, err`
  from `g.resolve`.)
- **D2.2 Decision table.** `rpcAuth` is unchanged when exactly one `auth_literal` interceptor exists (benign ones ignored,
  round-2 behaviour, N's `LogInterceptor` case). If any `auth_unknown` or `auth_not_literal` interceptor guards the rpc and no
  literal interceptor decides it, the rpc is `unclear` (a handler-level UNAUTHENTICATED helper still yields `Member`, as
  today). **`Public` requires either a literal allowlist naming the method, or no interceptor at all except benign ones and no
  handler-level check.** The default branch of line 1382 is reached only in that last case.
- **D2.3 Optional improvement, allowed but not required:** tracing a sentinel returned through a value-receiver method call
  (`g.resolve`) so that `gate.unary` becomes `auth_literal`/`auth_not_literal`. If done, it is inside the same line budget.
  Its effect on kilnworks is post hoc (PD3-KILN).

## 3. Decisions: reconcile coverage (fixed before coding; `lattice/spec/grpc.py`, `reconcile_with` only)

**D3.1 Every contract rpc.** `reconcile_with` calls every **unary** method of every service in the descriptor set it is given
(`protos`/`proto_paths`, already passed for the bridge), sorted by `<Service>/<Method>`, not only `model.routes`; RC1-RC3 apply
to all of them unchanged. An rpc that is not a model route gets the added key `route: false` (not default) and its auth is
taken as non-public (fresh login, RC1). Streaming methods are listed `{code: null, answered: null, reason: "streaming"}`
only when not a route (keeps N's bytes; Q6). `(absent)` as round 2.
**D3.2 Output.** Per rpc `{code, answered}` exactly as round 2; added keys only when not default (`route`, `isolated`,
`reason`); `auth_refused` and `order` as round 2. On N, predicted byte-identical to round 2 (N declares no service it does not
register and has no streaming rpc: r, prediction). On `deskd`, `Reports/Backlog` now appears (declared change).
**D3.3 The runner never calls an rpc in the scored path.** `grpc_probes3.py` contains no `_call_fresh` or bridge call in
HO3-REC; it records `supplementary_calls: 0` and the scorer FAILs the row if the count is not 0.

## 4. Decisions: never

No network target beyond loopback; no Docker; no write under any target root; no git on a target; no LLM or API key in
extraction, reconcile, flows, scorer or runner; no edit to `.proto`, spec, `truth.json` or harness files of any corpus; no edit
to the frozen list (section 9); no flow rule change; the held-out scored once; kilnworks and `deskd` never re-labelled blind.

## 5. Scorer rules (`dogfood/grpc_scorer3.py`, pure; round-2 `grpc_flow_scorer.py` reused unchanged for HO3-FLOWS)

- **S1, S2, S3** as round 2 (vacuity FAIL, flows conjunction, reported histograms).
- **S5 REC on library output.** Denominator = truth rpcs that are not streaming (all states) plus `(absent)`. Coverage = rpcs
  in `reconcile_with`'s output / denominator. Agreement = covered rpcs whose final code class matches the truth state
  (`implemented`: answered and not UNIMPLEMENTED; `unimplemented` and `not_registered`: UNIMPLEMENTED; absent: UNIMPLEMENTED).
  Empty denominator: FAIL `vacuous`. `supplementary_calls != 0`: FAIL.
- **S6 REC order non-vacuity.** If the truth lists any rpc with `effects` other than `none` (section 6 schema), that rpc must
  appear in both passes of the library's output (not only isolated, not missing); else FAIL `order_vacuous`. If the truth
  lists none, reported `not_exercised` (the row can still pass; disclosed).
- **S7 AUTH.** Denominator = truth rpcs with state `implemented` or `unimplemented` (all of them, not only those the extractor
  found; a missing rpc counts as `unclear`). `wrong` = an extracted `Public` where truth is non-public, or `Member` where truth
  is public. Pass = `wrong == 0` **and** `unclear / denominator <= 0.50` (an all-unclear answer is not a pass). Empty
  denominator: FAIL.
- Cases in `dogfood/grpc_scorer3_cases.json`, >= 20: S5 (coverage below bound, a supplementary call, empty denominator,
  each state class wrong), S6 (missing pass, isolated-only, not_exercised), S7 (one wrong each direction, unclear at 0.50
  and at 0.51, a missing rpc, empty denominator).

## 6. The new held-out (commissioning described; not written here)

**Who.** One **new** separate agent: not the round-1 (`deskd`) or round-2 (kilnworks) held-out author, not any round-1, 2 or 3
builder, not this author, not the orchestrator. Dispatched by the orchestrator through the `claude` CLI (no API key,
`--max-budget-usd` capped, Q8) **after this document's Amendment 1 is committed and after the round-3 library freeze
(`freeze3.sha256`), before any gated probe.** A usage limit is NOT RUN and retried later, never replaced by the builder.

**It is given only:** round-1 D1/D2 scope in prose (proto grammar, grpc-go server idioms are read), D1-D4 of round 2 in
prose, the harness contract, the truth schema below, and the brief below. **It must not read:** N, `deskd`, kilnworks,
the sessions fixture, `lattice/**`, `dogfood/**`, `tests/grpc/**`, any `docs/results/*grpc*` file or data directory. The
orchestrator runs it in a fresh scratch tree containing only the permitted texts (a listing is stored with the manifest), so
the exclusion is enforced, not requested.

**Brief (fixed now; deliberately generic, Amendment 1 Q7 of round 2 stands).** A grpc-go app over pure-Go SQLite (no cgo,
no Docker, buildable with the Go proxy), written to `$SCRATCH/heldout3/<app>/`. Shape minimums as round 2 (>= 3 registered
services, >= 15 unary rpcs, >= 1 `Unimplemented` rpc, >= 1 streaming rpc, >= 1 declared-but-unregistered service, >= 2 users
of different roles, >= 2 request fields naming a seeded row by a natural key, >= 6 truth flows of >= 3 steps with `links`).
**Anti-overfitting (how idioms vary without naming the round-3 shapes):**
- "Organise the server the way a team maintaining a mid-sized production service would: packages per area, construction
  code separate from handlers, the indirection such codebases typically have. **No two services may be wired into the server
  by the same code idiom**, and the authentication decision must be written in an idiom of your own choice. Do not copy an
  example from any document; choose idioms you have seen in real grpc-go code."
- The brief names **none** of: closures, slices of functions, helper or `mount` functions, value receivers, descriptor
  variables, interceptor chains, method-name maps, generated method-name constants, sessions revoked, roles changed. It does
  not mention that registration or auth was a failure cause.
- **After** the code and truth are complete, the author writes `IDIOMS.md`: one line per service (how it is wired) and one
  per auth mechanism, in its own words. `IDIOMS.md` is in the manifest, and **the runner and builder do not read it before
  scoring**; it is read after HO3 to attribute misses per idiom (reported, post hoc).
- A diversity floor the orchestrator checks without reading code semantics: `IDIOMS.md` lists >= 3 distinct wiring lines for
  >= 3 services; if not, the set is returned to the author before the manifest (disclosed).

**Truth (`lattice.heldout.truth/3`)** = round-2's `/2` plus, per rpc, `effects: none | revokes_session | deletes_caller |
changes_caller_role | other` (S6). `conventions.step_index_base` stated. Every truth flow replays on its own (`Rpc` scenarios)
before the manifest; the author fixes or drops a failing one first.

**Manifest and order (binding).** (1) Round-3 build done; `sha256sum` of the files of section 9 "may change" plus
`dogfood/grpc_scorer3.py`, its cases and `dogfood/grpc_probes3.py` (with `--arm H3` built) into `freeze3.sha256`, copied to
`docs/results/data/2026-10-18-grpc-round3/`. (2) The agent writes `heldout3/` and `FILES.txt`. (3) The orchestrator records
`sha256sum` of every listed file (sorted) as `heldout3/freeze.sha256`, copies it to the data directory, and commits it with
`freeze3.sha256`, recording **the sha256 of both files in Amendment 2 of this document before any gated probe.** (4) The
runner checks both hashes first (a mismatch aborts) and runs `--arm H3` **once**. (5) A runner crash before any metric is
written may be re-run once (disclosed); a runner layout mismatch is NOT RUN, fixed in the runner only, re-hashed, disclosed;
the library is never edited after step 1.

## 7. Probes (runner `dogfood/grpc_probes3.py`; numeric pass rules, blind)

### 7.1 Arm 0 (static)

| id | probe | prediction (p) | pass |
|---|---|---|---|
| P3-BC | byte-compat promise (section 10) vs the merge commit | all identical | **all** |
| P3-WIRE | new dev fixture `tests/grpc/fixtures/wiring/` (builder-written, tuned-on): one service per R1-R5 shape plus negatives (an unreferenced `Register` function; one referenced only from `_test.go`; a referenced but un-resolvable call through an opaque `interface{}` value) | positives `implemented`; dead and test-only `not_registered`; opaque `unread` with `registration_reachability_unknown` | **exact on every service and rpc state** |
| P3-AUTHU | new dev fixture `tests/grpc/fixtures/authwire/`: benign logger; benign recover-to-INTERNAL; untraced-sentinel gate; literal allowlist plus logger (N-shaped); no interceptor | `Public`/`Member` only where a literal allowlist or no-interceptor case decides; untraced gate `unclear` | **exact**; 0 `Public` on any rpc behind an `auth_unknown` interceptor |
| P3-DET | extract, model, reconcile, flows on N, `deskd`, kilnworks (dev), `wiring`, `authwire`: 10 processes, random `PYTHONHASHSEED`, two roots | identical | **10/10** |
| P3-NW | no write under N, T, sessions, `deskd`, kilnworks, `heldout3`; recording `claude` shim | 0 changed, 0 new, 0 calls | **all 0** |
| P3-UNIT | `tests/grpc/test_wiring3.py`, `test_auth3.py`, `test_reconcile3.py`: R1-R6, D2.1-D2.2, D3.1-D3.3 | 25-45 tests | **all green, >= 25** |
| P3-SCORE | `grpc_scorer3_cases.json` (S5-S7) | 20-30 cases | **100%, >= 20** |
| REG | `pytest tests/spec tests/importer tests/gocheck tests/mcp tests/studio tests/grpc` on `git archive <merge>` vs the worktree | same failing ids | **same set** (new tests extra) |

### 7.2 Arm G (Go servers over pure-Go SQLite; dev rows are development data)

| id | probe | prediction (p) | pass |
|---|---|---|---|
| PR3-N | library `reconcile` on N | byte-equal to round 2 | **exact** |
| PR3-DESK (dev) | library `reconcile_with` on `deskd`, library output only | 19/19 non-streaming truth rpcs + absent covered; `Reports/Backlog` now UNIMPLEMENTED from the library; 0 `auth_refused` | **S5 coverage 1.00, agreement 1.00** |
| PR3-SESS | sessions fixture, round-2 rows | as round 2 (`Stub` UNIMPLEMENTED, 0 `login_failed`, 4 disagreements isolated) | **exact** |
| PF3-N, PF3-DESK | flows on N and `deskd` (library unchanged for flows; model of both unchanged) | N 17/17 verified; `deskd` 41 recovered / 20 verified, edge recall 21/34, coverage 4/7, as round 2 | **identical counts to round 2** |
| PF3-LEAK | no app/bridge process after reconcile (incl. non-route rpcs and isolated reruns), flows, forced startup failure | 0 | **0** |
| PR2-ALL | round-2 runner `grpc_probes2.py --arm 0/G` unchanged at round-3 code | every round-2 PASS row still PASS except PR2-DESK's output bytes (declared, D3.2) | **all other PASS rows PASS** |
| PX3 | wall: extract on kilnworks; reconcile on `deskd`; flows on `deskd` | 0.5-4 s; 2-10 s; 8-30 s | **<= 30 s; <= 30 s; <= 180 s** |
| PD3-KILN (**post hoc, tuned-on, never gated**) | kilnworks re-scored by `--arm H3`'s scorer with round-3 code, freeze `d7a5ea3c...` checked; each row with its round-2 strict value and delta | RPC 0.95-1.00 / 0.95-1.00; CODES 0.55-0.95 / 0.90-1.00; AUTH 0 wrong, unclear 0.0-0.5 (all Catalog/Passport/workshop rpcs `unclear` unless D2.3 is done); TABLES 1.0; REC coverage 1.00, agreement 0.95-1.00; FLOWS recovered 10-60, verified 0-15, edge recall 0.00-0.50, coverage 0-5 of 9 | **reported only** |

### 7.3 Held-out (PG-HO3, scored once after Amendment 2; section 6's app)

| id | metric | prediction (p) | pass (gated) |
|---|---|---|---|
| HO3-RPC | rpc recall / precision, state included (`unread` counts wrong) | 0.70-1.00 / 0.75-1.00 (point 0.90 / 0.95) | **>= 0.90 / >= 0.95** |
| HO3-CODES | codes micro recall / precision (implemented, OK excluded) | 0.45-0.90 / 0.85-1.00 | **>= 0.70 / >= 0.90** |
| HO3-AUTH | S7 | wrong 0 (p 0.8); unclear 0.00-0.60 | **0 wrong (hard) and unclear <= 0.50** |
| HO3-TABLES | tables recall | 0.85-1.00 | **>= 0.90** |
| HO3-REC | S5 + S6 + 0 `auth_refused` on truth-implemented non-public rpcs + every disagreement resolved isolated | coverage 0.95-1.00; agreement 0.85-1.00; 0-3 disagreements | **coverage >= 0.95, agreement 1.00, supplementary calls 0, S6 not FAIL, 0 `auth_refused`, all disagreements isolated** |
| HO3-FLOWS | D4 + S2, round-2 scorer unchanged | recovered 5-60; **verified 0-12 (point 4)**; edge recall 0.00-0.55 (point 0.25); coverage 0-5 (point 2); 0 vbf | **verified >= 1, 0 vbf, edge recall >= 0.30, coverage >= 2** |
| HO3-DET | extract, describe, model, reconcile, flows: 10 processes, 2 roots | identical | **10/10** |
| HO3-NW | `heldout3` root, recording shim | 0, 0, 0 | **all 0** |

**Probe count: 26** (Arm 0: 8; Arm G dev: 10, of which PD3-KILN reported only; held-out: 8). Gated: 25.

**Honest predictions.** HO3-FLOWS is the most likely miss: the flow rules were tuned on `deskd`, and kilnworks never exercised
them; a new app may name rows, gate fields or chain ownership in shapes F2-F4 do not read (P(HO3-FLOWS pass) about 0.35).
HO3-RPC can still miss if the new author picks a wiring idiom outside R1-R5 (R6 then reports `unread`, which is honest but
still wrong): P(pass) about 0.6. HO3-AUTH: P(0 wrong) about 0.8, P(unclear <= 0.50) about 0.6 (an idiom D2 cannot read leaves
every non-allowlisted rpc unclear). **P(all 8 held-out rows pass) about 0.15-0.25.**

## 8. Mutants (`dogfood/grpc_mutants3.py`, scratch copy; control green first; predicted 18/18 new + 17/17 round-2 re-applied)

Killed when a gated probe that passes on the control fails or a named unit test fails (disclosed per mutant, as round 2).

| id | mutant | predicted killer |
|---|---|---|
| GW1 | R1 off: package-level initializers not walked | P3-WIRE, P3-UNIT |
| GW2 | R2 off: calls through func values unresolved | P3-WIRE |
| GW3 | R2 takes only the first element of a ranged literal | P3-WIRE (the second element's service) |
| GW4 | R3 off: registration on a `ServiceRegistrar` parameter ignored | P3-WIRE |
| GW5 | R4 off: value-receiver methods missing from a pointer impl's method set | P3-WIRE (`unimplemented` instead of `implemented`) |
| GW6 | R4: a constructor returning an interface not followed | P3-WIRE |
| GW7 | R5 off: descriptor variable not followed | P3-WIRE |
| GW8 | R6 off: an unresolved referenced registration reported `not_registered` | P3-WIRE (opaque case), P3-UNIT |
| GW9 | R6 too broad: an unreferenced `Register` function counted as registered | P3-WIRE (dead case) |
| GW10 | a `_test.go` reference counts as wiring | P3-WIRE (test-only case) |
| GW11 | `grpcWired` merged into `g.wired` (HTTP reachability changes) | P3-BC |
| GU1 | D2 off: no interceptor decided => `Public` (round-2 behaviour) | P3-AUTHU |
| GU2 | every `other` interceptor treated as `auth_unknown` (benign check skipped) | P3-AUTHU (logger, recover cases) |
| GU3 | an untraced `return nil, err` counted benign | P3-AUTHU |
| GC1 | `reconcile_with` iterates `model.routes` only (round-2 behaviour) | PR3-DESK, P3-UNIT |
| GC2 | non-route rpcs called with the first login reused (RC1 off for them) | P3-UNIT |
| GS1 | scorer S5: supplementary calls ignored / coverage over the library's own rpcs, not truth's | P3-SCORE |
| GS2 | scorer S7: an all-unclear AUTH passes | P3-SCORE |

**Mutant count: 18 new** (GW1-GW11, GU1-GU3, GC1-GC2, GS1-GS2) **+ 17 round-2 (GR1-GR17) re-applied = 35.** A GR mutant that
no longer applies textually is re-expressed and disclosed.

## 9. Frozen and changeable files (exact; hashes r at `spike-i-grpc` `5926008`; re-checked at the merge commit)

**May change, with line budgets (`git diff --numstat` insertions + deletions against the merge commit):**

| file | round-2 sha256 | what | budget |
|---|---|---|---|
| `lattice/gocheck/src/goextract_grpc.go` | `c0cee748cc5a32d8b4d599fe51fdaeab24c1a30e631e3c2267b9e4a8b20f22af` | D1.0-D1.6, D2 (and D2.3 if done): `findGrpcRegistrations`, a new `grpcWired` walker, `implType`, `descOf`, `classifyInterceptor`/`interceptorFuncs`, `rpcAuth` | **<= 300** |
| `lattice/spec/grpc.py` | `814b3bc0d27fb648c7ff90eeb61d53107b4cf48a577d88770ce0e12eb3ba9bde` | `reconcile_with` and private helpers only (D3); AST of every other top-level def identical | **<= 80** |
| `lattice/importer/frontends/go_grpc.py` | `d22aefc60c5210ac515d1d8d0dc865d6dc803b38c45fd44f6ccd0f1d8c2f4ca4` | only if the new `unread` reason or the `auth_unknown` kind needs a mapping (r: line 153 already drops non-implemented states, line 173 reads `auth` with an `unclear` default) | **<= 15 (p: 0)** |

**Creates:** `dogfood/grpc_probes3.py`, `dogfood/grpc_mutants3.py`, `dogfood/grpc_scorer3.py`, `dogfood/grpc_scorer3_cases.json`,
`tests/grpc/test_wiring3.py`, `test_auth3.py`, `test_reconcile3.py`, `tests/grpc/fixtures/wiring/**`,
`tests/grpc/fixtures/authwire/**` (each with `truth.json` hashed before any round-3 extractor code runs), the round-3 results
doc and `docs/results/data/2026-10-18-grpc-round3/**`; this file only by appended Amendments.

**Stays frozen (empty diff vs the merge commit; the runner checks the hashes):**
- `lattice/gocheck/src/goextract_proto.go` `ec4aa3157b407467708bdc341171e94e9a33aedf70ac6a46893d7b41590c9a50`,
  `goextract.go` `652e7007380ef7fa3e16b77ac937fb300ab5023899c2e81408cfa7a2a28c376a`, `load.go`
  `72a35ec417e522243f7583f8cb7f605c5f0261a81ce28ee7d057fa5af4cbd17f`, and every other `lattice/gocheck/**` file.
- `lattice/importer/workflows.py` `5eba7e8ab6ac62ba8ba4cea31088faab9e149e921a952c6a4837e37b92638098` (flow rules unchanged, scope 4).
- `dogfood/grpc_flow_scorer.py` `8d896ee0ad1c22fd46ff9f38876c08e8df03b756906f0b40bcaadf0a0446fdd3` and its cases (HO3-FLOWS reuses it).
- `dogfood/grpc_probes2.py` `0193add7...` (as last run), `grpc_mutants2.py`, round-1 runners, the bridge (`lattice/grpcbridge/**`), the 3 stubs.
- `tests/grpc/**` existing files, incl. N `truth.json` `debbd0b4...`, sessions `truth.json` `0e9b0d37...`, `deskd_links.json` `604df939...`.
- Corpora outside the repo: `deskd` (`8670bf84...`, 31 files), kilnworks (`d7a5ea3c...`, 27 files): read only, never edited.
- All round-1 and round-2 docs and data directories (errata go in the round-3 results only).

## 10. Byte-compat promise

Versus the merge commit: every HTTP output of round-1/2's promise (gocheck extract on the 13 HTTP fixtures, `go_gin`
RepoModel, `select()`, DCX/CD reports, review/validate/lint, `build_report`/`build_appmap`, impact JSON, `--help`, MCP
`tools/list`) **byte-identical** (D1.0 keeps `g.wired` untouched); `gocheck extract` JSON and `RepoModel` on N, T, sessions and
`deskd` **byte-identical** (p: they use direct registration and either a literal allowlist or no unknown interceptor; a
difference is a finding, not re-labelled); reconcile output on N and sessions byte-identical; flows output on N and `deskd`
byte-identical. **Declared not byte-compatible:** extract/model on kilnworks (the intended fix); `reconcile_with` output on
`deskd` (adds `Reports/Backlog` with `route: false`) and on any app with a declared-but-unregistered service; a new interceptor
kind `auth_unknown` and a new unread reason `registration_reachability_unknown` appear only where those cases occur. No new key
in any existing JSON output otherwise.

## 11. Risks, and what this cannot show

1. **Tuned on kilnworks.** R1-R5 were chosen after reading kilnworks; PD3-KILN will look good by construction and is never
   gated. Only HO3 (n = 1 app) tests generality.
2. **The idiom list is open-ended.** A new author may wire services in a shape outside R1-R5 (reflection, a registry keyed by
   string, code generation, a DI framework). R6 turns that into `unread` (honest), but HO3-RPC still fails. That is the
   expected failure mode, not a reason to widen gates.
3. **Generic brief, real difficulty.** The brief asks for idiom diversity without naming shapes; it is harder than round 2's
   effective difficulty and makes HO3-RPC/AUTH misses likely; the `IDIOMS.md` floor ensures diversity exists but cannot force
   difficulty to be fair.
4. **D2 trades recall for safety.** More `unclear` is expected; S7's 0.50 cap keeps "everything unclear" from passing, but an
   app with a single unread gate fails AUTH by design.
5. **D3 calls rpcs that are not routes** with a fresh login; a non-route rpc that changes state is covered by RC2/RC3 as any
   other, but a declared-but-unregistered rpc answers UNIMPLEMENTED by construction, so it adds coverage, not risk.
6. **Flows are untested twice.** kilnworks never exercised F2-F4; if HO3-RPC passes and HO3-FLOWS fails, round 3 gives the
   first real reading of the round-2 flow rules on an unseen app, and it may be negative.
7. **One author per held-out, shared grpc-go library** (round-2 risk 5), n = 1.
8. **Cannot show:** Streak/MySQL (Arm M), the build round (Arm L), streaming, non-Go servers, layers A/R/X/B on rpcs.

## 12. Blind vs post hoc, and commands

Fixed now: D1-D3, S5-S7, every prediction, gate and bound, the brief, the mutant list. A missed prediction is a finding.
HO3 is scored **once** after Amendment 2; a fix after that is a separate post-hoc change, re-run with deltas, never blind.
PD3-KILN and every `deskd` row are post hoc/dev in every run. **Pass:** every gated row of 7.1-7.3, mutants 35/35 (by probes or
named unit tests, disclosed), P3-BC, REG. Arm M and Arm L are NOT RUN, which is not a pass.

```sh
export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1
SCRATCH=<build node scratchpad>/grpc3
export LATTICE_GOCHECK_CACHE=$SCRATCH/gocache LATTICE_GRPCBRIDGE_CACHE=$SCRATCH/bridgecache
git archive <merge> | tar -x -C "$SCRATCH/base"                                  # byte-compat + REG baseline
sha256sum tests/grpc/fixtures/{wiring,authwire}/truth.json > "$SCRATCH/freeze-dev3.sha256"   # before extractor code
uv run python -m pytest tests/grpc -q
uv run python dogfood/grpc_probes3.py --arm 0 --base "$SCRATCH/base" --deskd <deskd> --kiln <kilnworks> --out docs/results/data/2026-10-18-grpc-round3
uv run python dogfood/grpc_probes3.py --arm G --base "$SCRATCH/base" --deskd <deskd> --kiln <kilnworks> --out ...   # PD3-KILN labelled post hoc
uv run python dogfood/grpc_probes2.py --arm 0 ...; uv run python dogfood/grpc_probes2.py --arm G ...               # PR2-ALL
uv run python dogfood/grpc_mutants3.py --scratch "$SCRATCH/m" $(seq -f GW%g 1 11) GU1 GU2 GU3 GC1 GC2 GS1 GS2 $(seq -f GR%g 1 17)
sha256sum lattice/gocheck/src/goextract_grpc.go lattice/spec/grpc.py lattice/importer/frontends/go_grpc.py \
  dogfood/grpc_scorer3.py dogfood/grpc_scorer3_cases.json dogfood/grpc_probes3.py > "$SCRATCH/freeze3.sha256"      # BEFORE heldout3
# orchestrator: commission heldout3 (section 6), commit freeze3.sha256 + heldout3 freeze.sha256 (Amendment 2), then once:
uv run python dogfood/grpc_probes3.py --arm H3 --heldout "$SCRATCH/../heldout3" --freeze .../freeze3.sha256 --out .../heldout3
uv run ruff check <changed .py>; uv run ruff format --check <created .py>; gofmt -l lattice/gocheck/src tests/grpc/fixtures/{wiring,authwire}
```

**File ownership (build node).** Edits: section 9 "may change" only, within budgets. Creates: section 9 "creates". Not
touched: the frozen list; `heldout3/` (runner only; the builder never opens it, nor `IDIOMS.md`); Streak; the main checkout
outside the build worktree. No commits by the builder; the orchestrator stages explicit paths only (never `git add -A`).

## 13. Questions for the orchestrator

- **Q1 Merge and location.** The request reads "merge and then run round 3". Confirm: merge `spike-i-grpc` (`5926008`) into
  `next-phases` first (with the round-1 erratum note appended to the round-1 results doc, as round-2 Amendment 1 Q9 ruled, and
  the round-2 strict figure 2/7 recorded in a note on the round-2 results), then build round 3 in a new worktree from the merge
  commit (path?). This file was written in `/home/user/lattice`; copy it into the build worktree or commit it here first?
- **Q2 Strict round-2 figure.** Adopt 2 of 7 (HO2-REC FAIL on library output) as the round-2 blind result everywhere, with the
  runner-rule 3 of 7 kept as a footnote?
- **Q3 R6.** Accept `unread` (not `not_registered`) for a referenced registration the extractor cannot reach, knowing it does
  not raise HO3-RPC?
- **Q4 Loader frozen.** D1.1 builds synthetic parents inside `goextract_grpc.go` so `load.go`/`goextract.go` stay frozen. If
  that proves impossible, may the builder spend <= 20 lines in `load.go` (disclosed, guarded by P3-BC), or stop and report?
- **Q5 S7 bound.** Unclear cap 0.50 on HO3-AUTH: tighter (0.30), looser, or reported only?
- **Q6 D3.** Call declared-but-unregistered and mis-extracted rpcs from the descriptor set (adds `route: false`), and list
  non-route streaming rpcs with `reason: "streaming"`? Coverage bound 0.95: right number?
- **Q7 Brief.** Approve the generic "no two services wired by the same idiom" clause and the post-hoc `IDIOMS.md` (written after
  the code, unread until scoring), with no shape named?
- **Q8 Commissioning.** The USD cap per call and in all for the new author; and confirm it may not be any earlier author.
- **Q9 Optional D2.3** (trace sentinels through value-receiver method calls): allow inside the 300-line budget, or forbid so
  round 3 tests only the safe default?
- **Q10 Flows.** Keep `workflows.py` frozen even though HO3-FLOWS is predicted to fail more likely than not (scope 4), or allow
  a flow change, which would make round 3's flows numbers dev-tuned on nothing seen?

## Amendment 1 (orchestrator rulings, 2026-10-18; defaults, overturnable)

Q1: the owner's "merge and then run round 3" merged Spike H (3679ab2); Spike I stays unmerged on `spike-i-grpc` until round 3 is scored; round 3 builds in the existing worktree `/home/user/wt/np-grpc` after `next-phases` is merged into it. Q2: adopt the strict 2 of 7 as the round-2 blind figure. Q3: accepted (R6). Q4: the builder may spend up to 20 lines in `load.go` if the synthetic parents prove impossible, recorded as a finding. Q5: the unclear cap on HO3-AUTH is 0.50, gated. Q6: yes; 0.95 coverage bound. Q7: approved. Q8: the author is a Claude Code subagent with no API key and no per-call USD cap, and is none of the earlier authors. Q9: allowed inside the 300-line budget. Q10: `workflows.py` stays frozen.
