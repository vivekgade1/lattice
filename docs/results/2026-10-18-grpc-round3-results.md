# gRPC round 3 (Spike I3), development stage: results

2026-10-18. Build node, worktree `/home/user/wt/np-grpc` (branch `spike-i-grpc`, built on the merge commit
`0b92f00` = `next-phases` merged into `spike-i-grpc`). Pre-registration:
`docs/results/2026-10-18-grpc-round3-prereg.md` with Amendment 1, followed to the letter except where section 8 below says otherwise.

**NOT RUN, first line: the gated held-out stage (PG-HO3: HO3-RPC, HO3-CODES, HO3-AUTH, HO3-TABLES, HO3-REC, HO3-FLOWS, HO3-DET, HO3-NW)
is NOT RUN. This node never created, opened, listed or scored `heldout3/`. Arm M (Streak/MySQL), Arm L (the build round) and everything
that needs Docker are NOT RUN (no Docker daemon, no Streak checkout); a NOT RUN is not a pass.** No model was called, no API key was used,
nothing is committed or staged (`git diff --cached` is empty; the orchestrator stages explicit paths). Streaming stays `not supported`.

**Every `deskd` and kilnworks number below is development data** (both were scored and read by earlier rounds; kilnworks's failures chose this round's scope).
PD3-KILN is post hoc, tuned-on and never a gate; no library file was changed after the first recorded run of the probes (section 8.12).

## 1. Verdict of the dev stage

* Gated dev rows: **16 of 16 PASS** (Arm 0: P3-BC, P3-WIRE, P3-AUTHU, P3-DET, P3-NW, P3-UNIT, P3-SCORE, REG; Arm G: PR3-N, PR3-DESK, PR3-SESS, PF3-N, PF3-DESK, PF3-LEAK, PR2-ALL, PX3).
  PD3-KILN (6 reported sub-rows) is reported, not gated. Mutants **35 of 35 killed** (18 new, 17 round-2 re-applied; 10 killed only by unit tests, not by a probe).
* Predictions missed (plainly, section 7): the unit-test count (73 against 25-45), N's streaming rpc (the prereg said N has none), PD3-KILN AUTH
  (**unclear 17 of 17, predicted 0.0-0.5; by the S7 rule this row would FAIL**), PD3-KILN flow coverage (6 of 9, predicted 0-5), the wall of the
  kilnworks extract and the deskd reconcile (below the predicted range), the named killer of GW11.
* The library changed in exactly two files: `lattice/gocheck/src/goextract_grpc.go` (297 of 300 lines) and `lattice/spec/grpc.py` (32 of 80); `go_grpc.py` 0 of 15.

## 2. What was built

| file | change | budget (insertions + deletions vs `0b92f00`) | used |
|---|---|---|---|
| `lattice/gocheck/src/goextract_grpc.go` | D1.0-D1.1, D1.4 (constructor), D1.5, D1.6, D2.1-D2.3, `rpcAuth` | 300 | 297 |
| `lattice/spec/grpc.py` | `reconcile_with` only (D3.1-D3.2); one nested helper, no new top-level def | 80 | 32 |
| `lattice/importer/frontends/go_grpc.py` | none | 15 (p: 0) | 0 |

Created: `dogfood/grpc_scorer3.py` (S5-S7) and `grpc_scorer3_cases.json` (30 cases), `dogfood/grpc_probes3.py`, `dogfood/grpc_mutants3.py`,
`tests/grpc/test_wiring3.py` (32 tests), `test_auth3.py` (29), `test_reconcile3.py` (12), the dev fixtures `tests/grpc/fixtures/wiring/` (11 services, 22 rpcs) and
`tests/grpc/fixtures/authwire/<9 cases>`, this file and `docs/results/data/2026-10-18-grpc-round3/**`.

How the decisions were realised (one line each):
* **D1.0** a separate walk (`grpcWalk`, in `goextract_grpc.go`): the HTTP walker's set `g.wired` plus every package variable a wired function reads. `g.wired`, `isWired` and every HTTP path are not touched.
* **D1.1** a package variable's initializer is walked as a body through a synthetic parent `Func` built in `goextract_grpc.go` (`load.go` and `goextract.go` stay frozen; the Q4 allowance was not used). Registrations and interceptors are read from those bodies too.
* **D1.2** has no code of its own: every function named in a walked body (including a walked initializer) is wired by the HTTP walker's value rule, which the new walk reuses, so a call through a slice, map, field or parameter of func values needs no resolver (section 8.1).
* **D1.3** nothing to add: registrations are matched by the `Register<X>Server` name, whatever the first argument's type (section 8.2).
* **D1.4** a constructor whose result is an interface is followed to the one concrete type it returns (else `unread`); the impl's method set (value receivers, promoted methods) was already read by `MethodOf`.
* **D1.5** `followDesc`: a local's single value or a non-literal package variable, up to four steps, before `descOf` reads the literal.
* **D1.6** a `Register` call in a function with no reference anywhere in non-test code (an identifier or selector naming it) is dead: `not_registered`. One in a function that is referenced but not reached makes its rpcs `unread` with `registration_reachability_unknown` (file:line of the call); the `excluded` entry keeps today's shape.
* **D2.1** an interceptor that traces no `UNAUTHENTICATED` is benign only when every return (and every deferred-literal assignment to its error result) is the handler's own answer or an error traced to another code than `UNAUTHENTICATED` / `PERMISSION_DENIED`, with no unread error source; otherwise it is `auth_unknown` with the first untraced return as `reason`.
* **D2.2** unchanged table plus: `Public` only from a literal allowlist or from no interceptor but benign ones; one literal interceptor beside an unread one still says `Member` for what it refuses and `unclear` for what it would let through.
* **D2.3** done (Q9): a package-level sentinel returned by bare identifier is traced (`errNoTicket` through the value-receiver `g.resolve`).
* **D3.1-D3.3** `reconcile_with` calls every unary rpc of the contract its transport describes (`route: false` on a non-route, fresh login, RC2/RC3 as before); the runner has no call of its own in the scored path (`supplementary_calls: 0`, enforced by S5 and by a unit test that reads the runner's source).

## 3. Arm 0 (static)

| id | prediction (p) | observed | pass rule | verdict |
|---|---|---|---|---|
| P3-BC | all identical | 73 HTTP outputs of the round-1/2 emitter, extract + RepoModel of N, T, sessions and deskd, `reconcile` bytes of N and sessions, flows/plans/extract/reconcile of N and flows/plans/extract of deskd: all identical to `0b92f00`; the `grpc.py` AST outside `reconcile_with`, `workflows.py` and `go_grpc.py` identical; 69 HTTP workflow tests green. Declared difference seen: deskd `reconcile_with` gains `Reports/Backlog`. | all | **PASS** |
| P3-WIRE | positives `implemented`; dead and test-only `not_registered`; opaque `unread` with `registration_reachability_unknown` | 22 of 22 rpc states equal the hand-written truth (hashed before extractor code); Opaque `unread` with that reason; excluded registrations Dead, Gone, Opaque; the gateway list stays empty (D1.0). Round-2 code on the same fixture: 12 of 22 right. | exact on every service and rpc state | **PASS** |
| P3-AUTHU | `Public`/`Member` only where a literal allowlist or no-interceptor case decides; untraced gate `unclear` | 9 of 9 cases exact (auth and interceptor kinds); 0 `Public` behind an `auth_unknown` interceptor. Round-2 code: 5 of 9 cases right, 5 rpcs labelled `Public` that are not. | exact; 0 `Public` behind `auth_unknown` | **PASS** |
| P3-DET | identical | 10 of 10 identical over two roots on N, wiring, authwire, deskd, kilnworks (extract, model, plans, and flows + reconcile where the app runs); no absolute path, time or port in any artifact. | 10/10 | **PASS** |
| P3-NW | 0 changed, 0 new, 0 calls | 0 changed, 0 new, 0 `claude` calls under N, T, sessions, wiring, authwire, deskd, kilnworks (reconcile x3 and flows x3 where the app runs). `heldout3`: NOT RUN (never looked in). | all 0 | **PASS** |
| P3-UNIT | 25-45 tests | 73 tests green (32 + 29 + 12): **outside the predicted range**, above the >= 25 bound. | all green, >= 25 | **PASS** |
| P3-SCORE | 20-30 cases | 30 of 30 cases agree. | 100%, >= 20 | **PASS** |
| REG | same failing ids | before (`git archive 0b92f00`): 7 failed, 2987 passed, 37 skipped, 98 errors; after: 7 failed, **3060 passed** (+73 new), 37 skipped, 98 errors; the same 95 failing ids (the environment's collection errors and 7 known failures). | same set | **PASS** |
| PM-S / PM-SG (Arm M) | | Arm M needs a Streak checkout and a Docker MySQL: out of scope (prereg 1); no docker daemon here | | **NOT RUN** |
| Arm L | | the build round is its own node; out of scope | | **NOT RUN** |
| PG-HO3 (HO3-*) | | the gated held-out stage: a new app by a new agent, scored once by the orchestrator after the freeze; this node never opened heldout3/ | | **NOT RUN** |

The frozen-file check (hashes of `goextract_proto.go`, `goextract.go`, `load.go`, `workflows.py`, `grpc_flow_scorer.py`, `grpc_probes2.py`, the three truth files, the deskd and kilnworks freezes, and the diff of everything else against `0b92f00`) ran before and after each arm: **ok before, ok after** (`probes3-arm0.json`, `probes3-armg.json`). All 97 other frozen files (`lattice/gocheck/**` but `goextract_grpc.go`, `lattice/grpcbridge/**`, round-1/2 runners and mutants, existing `tests/grpc/**`, `workflows.py`, the flow scorer) were also re-hashed against the merge archive at the end: identical (`freeze-frozen.sha256`).

Lint: `ruff check` and `ruff format --check` clean on every touched or created `.py` (`lattice/spec/grpc.py`, `dogfood/grpc_probes3.py`, `grpc_mutants3.py`, `grpc_scorer3.py`, the three new test files); `gofmt -l` clean on `lattice/gocheck/src` and both new fixtures; `go vet` of the extractor package clean.

## 4. Arm G (Go servers over pure-Go SQLite; `deskd` is development data)

| id | prediction (p) | observed | pass rule | verdict |
|---|---|---|---|---|
| PR3-N | byte-equal to round 2 | 10 of 10 answered; bytes equal the merge commit's `reconcile` on N (N has a streaming rpc, `WatchNotes`, which stays unlisted: section 8.4) | exact | **PASS** |
| PR3-DESK (dev) | 19/19 non-streaming truth rpcs + absent covered; `Reports/Backlog` UNIMPLEMENTED from the library; 0 `auth_refused` | S5 coverage 1.0, agreement 1.0 over 20 (19 rpcs + `(absent)`); `Reports/Backlog` {code UNIMPLEMENTED, route false} from the library; auth_refused []; no order disagreement | S5 coverage 1.00, agreement 1.00 | **PASS** |
| PR3-SESS | as round 2: `Stub` UNIMPLEMENTED, 0 `login_failed`, 4 disagreements isolated | codes equal the truth (alone); 0 login_failed; DeleteMe, Secret, SignOut, Stub disagree and are settled isolated; bytes equal the merge commit's | exact | **PASS** |
| PF3-N | 17/17 verified | recovered 17, verified 17, vbf 0; differences from round 2: none | identical counts to round 2 | **PASS** |
| PF3-DESK (dev) | 41 recovered / 20 verified, edge recall 21/34, coverage 4/7 | recovered 41, verified 20, failed 21, edge recall 21/34, coverage 4/7, vbf 0; differences from round 2: none | identical counts to round 2 | **PASS** |
| PF3-LEAK | 0 | 0 live processes or bridges after reconcile (incl. the non-route rpc and the isolated reruns) and flows, 0 after a forced startup failure, 0 stray in /proc | 0 | **PASS** |
| PR2-ALL | every round-2 PASS row still PASS except PR2-DESK's output bytes | round 2's `grpc_probes2.py --arm 0` 6/6 PASS rows still PASS; `--arm G` 9 of 10 (PR2-DESK fails, as declared: its row demands exactly one unimplemented model route and the output now lists `Reports/Backlog`); run un-nested (section 8.10) | all other PASS rows PASS | **PASS** |
| PX3 | 0.5-4 s; 2-10 s; 8-30 s | extract on kilnworks 0.03 s; reconcile on deskd 2.14 s; flows on deskd 11.6 s (first run 6.1 s, second 17.4 s, this one 11.6 s: load-dependent) | <= 30 s; <= 30 s; <= 180 s | **PASS** |
| PM-S / PM-SG (Arm M) | | Arm M needs a Streak checkout and a Docker MySQL: out of scope (prereg 1); no docker daemon here | | **NOT RUN** |
| Arm L | | the build round is its own node; out of scope | | **NOT RUN** |
| PG-HO3 (HO3-*) | | the gated held-out stage: a new app by a new agent, scored once by the orchestrator after the freeze; this node never opened heldout3/ | | **NOT RUN** |

### 4.1 PD3-KILN (post hoc, tuned-on, never gated): kilnworks re-scored with the held-out scorers and round-3 code

The scope of this round was chosen by reading kilnworks's failures, so every number here is tuned-on. Each row beside round 2's strict value.

| row | round 2 (strict, blind) | predicted (p) | observed (round 3) | in the predicted range? |
|---|---|---|---|---|
| RPC recall / precision | 0.30 / 0.30 | 0.95-1.00 / 0.95-1.00 | 1.0 / 1.0 (20 of 20 states right; streaming `not supported`) | yes |
| CODES recall / precision | 0.125 / 1.00 | 0.55-0.95 / 0.90-1.00 | 0.8 / 0.941 | yes |
| AUTH wrong / unclear | 2 wrong of 3 | 0 wrong; unclear 0.0-0.5 | **0 wrong; unclear 17 of 17 = 1.0** (S7 would FAIL: `unclear_above_cap`) | **no** (0 wrong yes; unclear no) |
| TABLES recall | pass | 1.0 | 1.0 | yes |
| REC coverage / agreement | 4 of 20 rpcs from the library (16 by the runner) | 1.00 / 0.95-1.00 | 1.0 / 1.0 (20 of 20 from the library, `supplementary_calls` 0; 1 order disagreement, `StepDown`, settled isolated; S6 `not_exercised`: the /2 truth has no `effects`) | yes |
| FLOWS recovered / verified / edge recall / coverage | 0 / 0 / 0 of 33 / 0 of 9 | 10-60 / 0-15 / 0.00-0.50 / 0-5 of 9 | 17 / 7 / 11 of 33 = 0.3333 / **6 of 9** (vbf 0; S2 would pass) | coverage 6 is **above** the predicted 0-5; the rest yes |

Why AUTH is `unclear` everywhere (read, not tuned): D2.3 now traces `errNoTicket` through `g.resolve`, so `gate.unary` is an auth interceptor (it no longer reads as benign), but its allowlist
`openDoors` is a map keyed by `healthpb.Health_Check_FullMethodName` and `Health_Watch_FullMethodName`, constants of a package outside the module that the extractor cannot read; the key set is
not literal, the interceptor is `auth_not_literal`, and every rpc behind it is `unclear` (never `Public`; round 2's two wrong `Public` labels are gone). **The same shape in the new held-out app would fail HO3-AUTH on the 0.50 cap by construction.**
Flows: all 10 failed flows answer `NOT_FOUND` at their failing step; this is the first reading of the round-2 flow rules on an app they were not tuned on: 7 of 17 verified, 11 of 33 truth edges contained.

## 5. Mutants (`dogfood/grpc_mutants3.py`, scratch copy, control green first): 35 of 35 killed

| id | mutant | killed by | pre-registered killer | note |
|---|---|---|---|---|
| GW1 | R1 off: package-level initializers are not walked | KILLED: p3-wire, pytest: test_wiring3.py | P3-WIRE, P3-UNIT |  |
| GW2 | R2 off: a function named as a value in a walked initializer is not followed | KILLED: p3-wire, pytest: test_wiring3.py | P3-WIRE | D1.2 has no code of its own (the HTTP walker's value rule, applied to a walked initializer, realizes it): the mutant removes that rule |
| GW3 | R2 takes only the first element of a ranged literal | KILLED: p3-wire, pytest: test_wiring3.py | P3-WIRE (the second element's service) | re-expressed over the walked initializer, where the elements of a ranged literal are read |
| GW4 | R3 off: a registration on a grpc.ServiceRegistrar parameter is ignored | KILLED: p3-wire, pytest: test_wiring3.py | P3-WIRE | the extractor matches Register calls by name, so R3 is not a filter; the mutant adds the filter the rule rules out |
| GW5 | R4: methods promoted from an embedded struct are missing from the impl's method set | KILLED: p3-wire, pytest: test_wiring3.py | P3-WIRE (unimplemented instead of implemented) | re-expressed: value-receiver methods were already in MethodOf's set, so the mutant cuts the promotion depth |
| GW6 | R4: a constructor returning an interface is not followed | KILLED: p3-wire, pytest: test_wiring3.py | P3-WIRE |  |
| GW7 | R5 off: a descriptor held in a variable is not followed | KILLED: p3-wire, pytest: test_wiring3.py | P3-WIRE |  |
| GW8 | R6 off: a referenced registration the walk cannot reach is reported not_registered | KILLED: p3-wire, pytest: test_wiring3.py | P3-WIRE (opaque case), P3-UNIT |  |
| GW9 | R6 too broad: an unreferenced Register function counts as referenced | KILLED: p3-wire, pytest: test_wiring3.py | P3-WIRE (dead case) |  |
| GW10 | a _test.go reference counts as wiring | KILLED: p3-wire, pytest: test_wiring3.py | P3-WIRE (test-only case) | load.go never reads _test.go files, so the mutant (made on the scratch copy only) lets the loader read them |
| GW11 | the gRPC walk is merged into g.wired (HTTP and gateway reachability change) | KILLED: p3-wire, pytest: test_wiring3.py | P3-BC | P3-BC's inputs have no gRPC-only function whose HTTP reading would move; the gateway call in the wiring fixture is what shows it |
| GU1 | D2 off: an interceptor that was not read decides nothing (round 2: Public) | KILLED: p3-authu, pytest: test_auth3.py | P3-AUTHU |  |
| GU2 | every other interceptor is auth_unknown (the benign check is skipped) | KILLED: p3-authu, pytest: test_auth3.py | P3-AUTHU (logger, recover cases) |  |
| GU3 | an untraced `return nil, err` counts as benign | KILLED: p3-authu, pytest: test_auth3.py | P3-AUTHU |  |
| GC1 | reconcile_with iterates model.routes only (round 2) | KILLED: pr3-desk, pytest: test_reconcile3.py | PR3-DESK, P3-UNIT |  |
| GC2 | non-route rpcs are called without a fresh login (RC1 off for them) | KILLED: pytest: test_reconcile3.py | P3-UNIT |  |
| GS1 | scorer S5: supplementary calls are ignored | KILLED: p3-score | P3-SCORE |  |
| GS2 | scorer S7: an all-unclear AUTH passes | KILLED: p3-score | P3-SCORE |  |
| GR1 | reconcile reuses one token (round 1's behaviour) | KILLED: pr2-sess, pytest: test_reconcile2.py test_flows2.py | PR2-SESS, PR2-DESK |  |
| GR2 | a fresh token per service, not per call | KILLED: pr2-sess, pytest: test_reconcile2.py test_flows2.py | PR2-SESS |  |
| GR3 | the reverse pass is skipped (RC2 off) | KILLED: pytest: test_reconcile2.py test_flows2.py | PR2-ORDER (sessions), P2-UNIT |  |
| GR4 | a disagreement is reported without the isolated rerun (RC3 off) | KILLED: pr2-order, pytest: test_reconcile2.py test_flows2.py | PR2-ORDER, P2-UNIT |  |
| GR5 | a failed login is recorded as UNAUTHENTICATED answered:true | KILLED: pytest: test_reconcile2.py test_flows2.py | P2-UNIT |  |
| GR6 | the contract's order instead of the sorted order in the forward pass | KILLED: pytest: test_reconcile2.py test_flows2.py | P2-DET (order key in output), P2-UNIT |  |
| GR7 | F2 off: natural keys filled by example_value (round 1) | KILLED: pf2-desk, pytest: test_reconcile2.py test_flows2.py | PF2-DESK |  |
| GR8 | F3 off: bool -> true (round 1) | KILLED: pytest: test_reconcile2.py test_flows2.py | PF2-DESK |  |
| GR9 | F1 binds an id to a field of another table | KILLED: pytest: test_reconcile2.py test_flows2.py | P2-UNIT, PF2-N |  |
| GR10 | the lookup picks the first element, unsorted | KILLED: pytest: test_reconcile2.py test_flows2.py | P2-DET |  |
| GR11 | owner_guard / owner_chain verify on UNAUTHENTICATED | KILLED: pytest: test_reconcile2.py test_flows2.py | P2-UNIT, PF2-DESK |  |
| GR12 | verified is set from the live run without the layer-D replay | KILLED: pytest: test_reconcile2.py test_flows2.py | PF2-REPLAY, P2-UNIT |  |
| GR13 | a fault code on a refuse step is counted as a refusal | KILLED: pytest: test_reconcile2.py test_flows2.py | P2-UNIT, PF2-N |  |
| GR14 | scorer: verified == 0 passes (round 1's rule) | KILLED: p2-score | P2-SCORE |  |
| GR15 | scorer: an empty denominator scores 1.0 | KILLED: p2-score | P2-SCORE |  |
| GR16 | scorer: edge matching ignores the consumer's code | KILLED: p2-score | P2-SCORE |  |
| GR17 | the HTTP workflow path touched (the rpc helper's summary shape in the HTTP summary) | KILLED: p2-bc-ast | P2-BC |  |

Killed only by a unit test, not by a probe (10): GC2, GR3, GR5, GR6, GR8, GR9, GR10, GR11, GR12, GR13 (GR3, GR5, GR6, GR8-GR13 and the new GC2, which the prereg named P3-UNIT for; round 2 recorded 7 of 17 killed by unit tests rather than the probe it named, a different count). A mutant whose text no longer applied as worded was re-expressed over the code that exists (column "note"; section 8.5).

## 6. Not run

* **PG-HO3 / HO3-*: NOT RUN** (above). The runner's `--arm H3` exists and was rehearsed end to end on a scratch copy of kilnworks with a made-up Amendment 2 in a scratch prereg (not the real `heldout3`, not the real prereg): it verified the hashes, read a `/2` truth with `step_index_base: 0`, scored all eight rows and wrote its files (HO3-AUTH failed there, as PD3-KILN says it must). `h3_truth` was also exercised by hand on synthetic `/3` truths (dict and list forms, base 0 and base 1, `effects`). Nothing of that rehearsal is a result.
* **Arm M** (Streak over MySQL), **Arm L** (build round): NOT RUN, out of scope.
* **P3-NW on `heldout3`**: NOT RUN here by construction (never looked in).

## 7. Predictions missed, and gates missed

Gates missed: **none of the 16 gated dev rows failed.** The held-out gates are not this node's.

Predictions that did not hold (a finding each):
1. **P3-UNIT: 73 tests** against a predicted 25-45 (the bound, >= 25, holds).
2. **"N has no streaming rpc" (prereg 3.2) is false**: `notes.v1.Notes/WatchNotes` is streaming. Listing non-route streaming rpcs as D3.1 words it changed N's `reconcile` output and failed the round-2 test `test_notes_reconcile_has_round_ones_shape`; the clause "(keeps N's bytes)" and PR3-N's "exact" decided it (section 8.4).
3. **PD3-KILN AUTH unclear 1.00** (predicted 0.0-0.5) and **PD3-KILN flow coverage 6 of 9** (predicted 0-5).
4. PX3: kilnworks extract 0.03 s (predicted 0.5-4 s) and deskd reconcile 1.4-2.1 s (predicted 2-10 s) are below the ranges, faster, not slower.
5. GW11's named killer (P3-BC) did not kill it; P3-WIRE and the unit tests did (section 8.6).
6. The prereg's R-list was longer than the code needed (D1.2, D1.3 and the value-receiver half of D1.4 were already satisfied): the fixture's Epsilon, Zeta and Eta services (a func field, a variadic func argument, a registrar field) pass on round-2 code too.

## 8. Deviations (each disclosed; none was used to move a number)

1. **D1.2 has no resolver.** Everything named in a body the HTTP walker's value rule reaches, and in a walked initializer, is wired; calls through func-typed locals, ranged elements, fields and parameters add nothing the rule does not already give. A dead composite literal elsewhere in the module is not read (the spec's "anywhere in the module" would call a dead literal live).
2. **D1.3 is not a filter.** The extractor matches `Register<X>Server` / `RegisterService` by name (round 2), so a registration on any registrar-typed value counts. GW4 was written as the filter the rule rules out.
3. **R4**: value-receiver and promoted methods were already in `MethodOf`'s set; only the constructor-returning-interface half needed code. GW5 cuts the promotion depth instead.
4. **D3.1 streaming listing dropped** (section 7.2): a non-route streaming rpc is neither called nor listed. S5's denominator excludes streaming rpcs, so no score depends on it.
5. **Mutants re-expressed**: GW2 (the value rule in a walked initializer), GW3 (only the first element of a walked initializer is read), GW4 (a filter on `grpc.ServiceRegistrar` parameters), GW5 (promotion depth), GW10 (the scratch copy's `load.go` reads `_test.go` files; no test file exists in the loaded module otherwise). GR1-GR17 applied as worded.
6. **GW11** is killed by P3-WIRE and `test_d1_0_the_gateway_walk_is_not_widened`, not by P3-BC: none of P3-BC's inputs has a function that only the gRPC walk reaches and whose HTTP reading would move. The gateway call in the wiring fixture (`mountWorkshop`) was added to the fixture after `truth.json` was hashed (the truth does not mention it; the assertion lives in P3-WIRE's detail and the unit test); a first mutants run with the earlier fixture survived GW11, the final run in `mutants3.json` kills it.
7. **R6 is incomplete for a registration held in a package variable that only unreached code reads**: the variable is not walked, so the registration is not found and the rpcs read `not_registered` where D1.6 says `unread`. Found by my own check after the first recorded run; **not fixed** (no library change after scoring; 3 lines of budget left). Shown by a throwaway module, not by a test. Flag for a post hoc change.
8. **Extensions beyond the letter, inside the budget**: the interceptor search finds a `grpc.UnaryInterceptor` / `ChainUnaryInterceptor` call in any wired function, not only inside `grpc.NewServer(...)` (server options built in another function, `opts_split`); D2.2 lets a literal interceptor beside an unread one say `Member` for what it refuses (the `mixed` case).
9. **S6** needs the "both passes" fact the library output does not expose; it is read as "present with an answer" (a missing rpc, a `login_failed` or a code-less entry is `order_vacuous`; an `isolated` entry is reported, not failed).
10. **PR2-ALL run un-nested.** Run as a child of this runner, round 2's PF2-LEAK scanned `/proc`, found the parent's command line (`--deskd .../deskd`) and failed with "2 stray processes": a measurement artifact, not a leak. Round 2's runner was therefore run directly (`pr2all/arm0.log`, `pr2all/armg.log`) and the round-3 row reads its output (`--pr2all-from`). Round 2's own "frozen files ok" line is False there (it forbids edits to files this round is allowed to change); it is not a row.
11. **The first REG baseline was invalid and thrown away**: it ran in the worktree while I was editing it. The recorded baseline is `git archive 0b92f00` run with the worktree's interpreter. The 98 collection errors are the environment's (`bench/template_app`), identical before and after.
12. **A dry run of the kilnworks rows** (plumbing only) was made before the recorded run; no library file changed afterwards. The runner changed twice after the first recorded Arm 0/G run (the un-nested PR2-ALL source; a `mkdir`); Arm 0 and Arm G were re-run with the final runner and are the recorded ones. `grpc_flow_scorer.py` is reused unchanged for flows.

## 9. For the orchestrator

* **Freeze before `heldout3` is written:** `docs/results/data/2026-10-18-grpc-round3/freeze3.sha256` (six files, sha256 of the file itself `e58444d8eca4019870822fd82aa43746f0810e76550fa098c3ba234d7d09a73c`) is the prereg section 6 step 1 listing (the committed copy the runner compares with is this same file). `freeze-dev3.sha256` (the two dev truths, hashed before any extractor code), `freeze-build-extra.sha256` (mutants runner, tests, fixtures) and `freeze-frozen.sha256` (97 frozen files) are also there. If any of the six files changes, the listing and Amendment 2 must be redone.
* Then: commission the new author (section 6 of the prereg), record `heldout3/freeze.sha256` and both hashes in Amendment 2, run `--arm H3` once. The runner refuses to score if Amendment 2 does not hold both hashes, if either listing differs from its committed copy in the data directory (`freeze3.sha256`, `heldout3.freeze.sha256`) or from the files on disk, or if anything is staged. It expects one app directory under `heldout3/` with `harness.py` and `truth.json`, and `freeze.sha256` at the top of `heldout3/` (or inside the app directory).
* Known risks for HO3, from what the dev stage showed: HO3-AUTH on any gate that keys its allowlist with constants the extractor cannot resolve (section 4.1); HO3-RPC on a wiring idiom outside R1-R6 (reported `unread`, still wrong) and on the section 8.7 gap; HO3-FLOWS (10 of 17 kilnworks flows fail with NOT_FOUND).
* Commands (the prereg's, with the paths used): `export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1`; `uv run python dogfood/grpc_probes3.py --arm 0|G --base <git archive 0b92f00> [--base-r1 <cea85fc> --base6a <6a318e5> | --pr2all-from DIR] --deskd <deskd> --kiln <dir holding kilnworks> --scratch S --out docs/results/data/2026-10-18-grpc-round3 --reg-before B --reg-after A`; `uv run python dogfood/grpc_mutants3.py --scratch S --deskd <deskd> --base <0b92f00> --base-r1 <cea85fc> $(seq -f GW%g 1 11) GU1 GU2 GU3 GC1 GC2 GS1 GS2 $(seq -f GR%g 1 17)`.

## 10. Hashes

| file | sha256 |
|---|---|
| `lattice/gocheck/src/goextract_grpc.go` | `b2eda6fd8264445a172685ccb40c2bc1559c0effd43e5342743f83eef1a9315e` |
| `lattice/spec/grpc.py` | `edd41b1f3c28817975b66f9fad65219d45f9e7a33579ede6c52acbdfcef527d3` |
| `lattice/importer/frontends/go_grpc.py` | `d22aefc60c5210ac515d1d8d0dc865d6dc803b38c45fd44f6ccd0f1d8c2f4ca4` |
| `dogfood/grpc_scorer3.py` | `019db509ded84f48ef95e7bfe7788a949305611a35ab3ae15706303298ea50be` |
| `dogfood/grpc_scorer3_cases.json` | `be3ccbcbc61817bd76f6bbd8873e8da40f4574b4204169a0040f7690a2ae88db` |
| `dogfood/grpc_probes3.py` | `64a988a1dca7d075245720b16652390efb205bf7cc9f322d14bf63cc20cbd036` |
| `tests/grpc/fixtures/wiring/truth.json` (before any extractor code) | `138cb5e4bb227aa58de1e51d5e825fd6a8ccbb359b5d929e65c13f97b7d2fbb2` |
| `tests/grpc/fixtures/authwire/truth.json` (before any extractor code) | `35a3139d798141618feef56fe05498c6113e71a4a9c191d8ba5849c3a71b0695` |
| deskd `freeze.sha256` (checked, unchanged) | `8670bf84cdcc9175797507f05e55e62f11716659dc9a0e7a6368aa14dab729f6` |
| kilnworks `freeze.sha256` (checked, unchanged) | `d7a5ea3c642e06b4ccb18983ad185896afec3cae8b15361b7d5941479803868e` |

Data: `docs/results/data/2026-10-18-grpc-round3/` (`probes3-arm0.json`, `probes3-armg.json`, `mutants3.json`, `pr2all/`, `pytest-before.txt`, `pytest-after.txt` and their ids, `reconcile-deskd.json`, `reconcile-kilnworks.json`, `flows-*.json`, `round2-code-on-round3-fixtures.json`, logs).

