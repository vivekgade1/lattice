# Spike I round 1: gRPC unary support, results (Arm 0 and Arm G)

**Arm M: NOT RUN. Arm L: NOT RUN (deferred to node I2 per Q2). Held-out stage (PG-HO): NOT RUN, the held-out app is authored by a separate agent.**
PM-S and PM-SG: NOT RUN (no Streak on this host, no Docker).

Pre-registration: `docs/results/2026-10-17-grpc-prereg.md` (with Amendment 1). Worktree `/home/user/wt/np-grpc`, base `6a318e5`. Nothing was committed or staged. No model calls, no API key. Scored artefacts: `docs/results/data/2026-10-17-grpc/` (`probes-arm0.json`, `probes-armg.json`, `mutants.json`, `freeze*.sha256`, `pytest-*`).

## Summary

| Stage | Passed | Failed | NOT RUN |
|---|---|---|---|
| Arm 0 (static) | 11 | 0 (2 first-run misses, see below) | 0 |
| Arm G (Go + pure-Go SQLite) | 10 | 0 | PG-HO |
| Arm M | | | PM-S, PM-SG |
| Arm L | | | all |
| Mutants | 16 killed of 16 (control green first) | | |

**Missed gates, stated plainly:**

1. **Regression gate missed.** The after-run of the existing suites has one failing id that the baseline does not: `tests/importer/test_frontends.py::test_every_added_field_is_in_the_goldens_added_defaults`. Cause: I added dataclass fields to `Route` (`transport`, `rpc`, `http_bindings`) and `RaiseSite` (`code`), and that existing test requires every new `RepoModel`/`Route`/`RaiseSite` field to be listed in `ADDED_DEFAULTS` in `tests/importer/test_frontend_goldens.py`. Existing tests are frozen, so I did not edit it, and I did not tune after scoring. Fix for a follow-up node: add `transport: "http"`, `rpc: null`, `http_bindings: []`, `code: null` to `ADDED_DEFAULTS` (the goldens themselves stay byte-identical; P0-BC confirms that). The gate "same failing ids before and after" is therefore **FAILED by one id**. Everything else in the existing suites matches (below).
2. **P0-DET first run FAILED (the 10 runs did not all agree).** The bridge's `descriptor_set` was nondeterministic (option field order). Fixed by `canonicalFile` and deterministic marshalling in `describe.go`; rerun 10/10 identical. The fix was made after seeing the failure, so it is a first-run miss, not a first-run pass.
3. **P0-BC first run FAILED.** The go_harness DCX report differed only by a wall-clock note ("in 8.6s"). The probe normalises that note (a probe fix, not a product difference); rerun 73/73 hashes identical.
4. **Prediction misses** (the probes pass their gates; the point predictions were off): PG-WF predicted 3-6 recovered flows, observed 17 (all verified); PG-X cold bridge build predicted 20-90 s, observed 15.9 s with an empty Go cache (3.0 s warm), below range; PG-REC, see table.

## Pytest, before and after (same worktree, same flags)

Command: `uv run python -m pytest tests/spec tests/importer tests/gocheck tests/mcp tests/studio [tests/grpc] -q -rfEs -p no:cacheprovider --continue-on-collection-errors`.
Baseline is `git archive 6a318e5` (no tests/grpc): 7 failed, 98 errors, 2627 passed (105 failing ids, `pytest-baseline-ids.txt`). The collection errors (`NameError: name 'Item'`, python 3.13 vs sqlmodel) pre-exist.
After: 8 failed, 98 errors, 2738 passed, 37 skipped (`pytest-after-ids.txt`). Diff of ids: exactly one added, the id in miss 1. All of `tests/grpc` passes (111 added passing tests).

Lint: `ruff check` and `ruff format --check` clean on every created file and on every edited file that was clean at base; six edited files (ask, authoring, check, lint, race, review) were already unformatted at base and their format-diff size equals base. `gofmt -l` clean; `go vet` clean.

## Arm 0 (static): predicted vs observed

| Probe | Predicted | Observed | Result |
|---|---|---|---|
| P0-PARSE reader vs protocompile | 0 disagreements | 0 (4 services, 25 rpcs, 32 messages, 95 fields, 3 enums) | PASS |
| P0-UNREAD | 10/10 unread items by kind, file, line | 10/10 | PASS |
| P0-VAL bounds | all expected bounds | all match `expected_bounds.json` | PASS |
| P0-EXT extractor vs hand-written truth (`truth.json`, sha256 debbd0b4...) | 0 diffs | 0 | PASS |
| P0-MODEL | rpcs are routes, bindings listed on the rpc | 11 routes, 2 bindings on rpcs (13 if bindings were routes; GX11 kills that) | PASS |
| P0-SPEC | Rpc/validate/lint tests | 16 tests green | PASS |
| P0-EMIT | emitted spec round-trips | green | PASS |
| P0-SITES | every `isinstance(Step/Await)` site handled | 51/51 sites, 52 tests incl. a completeness test | PASS |
| P0-DET | 10 runs, 2 roots identical | first run FAIL, rerun 10/10 (miss 2) | PASS after fix |
| P0-NW no write, no network, no LLM | 0 changed, 0 new, 0 claude calls | 0, 0, 0 | PASS |
| P0-BC byte-compat vs baseline tree | 0 differences | first run FAIL (timing note), rerun 73 hashes, 0 differences (miss 3) | PASS after probe fix |

## Arm G (Go servers over pure-Go SQLite, no Docker): predicted vs observed

| Probe | Predicted | Observed | Result |
|---|---|---|---|
| PG-BR bridge | describe/call/health/probe, loopback only | as predicted; non-loopback refused | PASS |
| PG-REC reconcile | implemented rpcs answer, unimplemented answer UNIMPLEMENTED | 10/10 implemented answered; ShareNote (stub) and an absent rpc UNIMPLEMENTED. Fixture shape gives 11 unary / 10 implemented, not the prereg's 9/8 (see inconsistencies) | PASS (count prediction off) |
| PG-D layer D | 0 findings on the ok app, each of 8 bugs localised | 0 findings; 8/8 localised; layer C also caught no_priority_check and publish_limit_off | PASS |
| PG-C layer C | invariants break on named bugs | as predicted | PASS |
| PG-J message form | `lattice.grpcjson/1` (int64, enums by name, unpopulated emitted) | as predicted | PASS |
| PG-MIX gin client only app stays Gin | select answers gin | yes | PASS |
| PG-AUTH | interceptor public set drives auth | yes | PASS |
| PG-WF flow recovery | 3-6 flows | 17 flows, all verified by layer-D replay, 0 verified-but-failing | PASS (prediction range MISSED) |
| PG-LEAK | no process left after failed start | none left | PASS |
| PG-X performance | p50 call <= few ms; cold bridge build <= 180 s (point 20-90 s) | call p50 0.57 ms, p95 1.0 ms; dev spec check median 3.65 s (quiet run 1.23 s); bridge build 3.0 s warm cache, 15.9 s empty cache | PASS (build below point range) |
| PG-HO held-out app | | | NOT RUN |
| PM-S, PM-SG | | | NOT RUN (no Streak, no Docker) |

## Mutants (scratch copy; control = 44 tests green before any mutation)

| Id | Mutant | Killed by | Result |
|---|---|---|---|
| GX1 | repeated/optional read as single | P0-PARSE | KILLED |
| GX2 | streaming rpc kept unary | P0-UNREAD, P0-EXT | KILLED |
| GX3 | Unimplemented stub reported implemented | P0-EXT | KILLED |
| GX4 | status.Errorf code ignored | P0-EXT | KILLED |
| GX5 | interceptor public set ignored | P0-EXT | KILLED |
| GX6 | `return nil, err` not followed | P0-EXT | KILLED |
| GX7 | non-literal code guessed | P0-EXT | KILLED |
| GX8 | `"grpc": null` emitted for HTTP modules | P0-BC unit test | KILLED |
| GX9 | `Route.transport` serialised for HTTP | P0-BC unit test | KILLED |
| GX10 | detect true on a gRPC client import | P0-BC unit test | KILLED |
| GX11 | gateway binding as second endpoint | P0-MODEL | KILLED |
| GX12 | Rpc passes regardless of code | PG-D | KILLED |
| GX13 | int64 kept as strings | PG-J | KILLED |
| GX14 | layer C skipped after an rpc | PG-C | KILLED |
| GX15 | bridge dials non-loopback | PG-BR | KILLED |
| GX16 | no process-group stop after failed start | PG-LEAK | KILLED |

16/16 killed. GX8-GX10 are killed by the fast named unit test `tests/grpc/test_bc.py` (hash comparison to `baseline-hashes.json`), not by the full P0-BC emitter run; disclosed. GX6 and GX15 first failed to compile; fixed and a Go build check added to the runner before scoring.

## Files created

`lattice/gocheck/src/goextract_proto.go`, `lattice/gocheck/src/goextract_grpc.go`; `lattice/grpcbridge/{go.mod,go.sum,main.go,describe.go,call.go,stubs/...}`; `lattice/importer/frontends/go_grpc.py`; `lattice/spec/grpc.py`; `dogfood/grpc_probes.py`, `dogfood/grpc_mutants.py`; `tests/grpc/**` (tests, oracle, fixtures notes/protos/gin_client_only); `docs/results/data/2026-10-17-grpc/**`; this doc.

## Files changed (additive)

`lattice/gocheck/src/goextract.go` (omitempty `grpc` key, one call); `lattice/importer/{appmodel,frontends/__init__,transport,workflows,emit_spec}.py`; `lattice/spec/{__init__,model,validate,lint,check,process,review,authoring,preconditions,fidelity,race,implementation,appmap}.py`; and two **frozen** files under Amendment Q8 (guards only): `lattice/spec/ask.py` (`isinstance(step, Await | Rpc)`, Rpc arms in `_step_json` and `_claim_key`) and `lattice/spec/impact_tier2.py` (three `isinstance` guards). These two are findings for the orchestrator. Untouched: pyproject, uv.lock, go_gin.py, gocheck/__init__.py, impact_tier3.py, mcp/tools.py, studio/**, existing tests, goldens, fixtures.

Freeze hashes (before scoring gated probes, unchanged at the end): `freeze.sha256`, `freeze-final.sha256`.

## Deviations from the prereg

- Python 3.13 environment (prereg said 3.12).
- One bridge process per transport (lazy), not per check.
- `validate(spec, root=None)` kwarg plus a pure-Python proto service reader in `spec/grpc.py` for S026-S029.
- `ProcessApp` extra fields (`ready`, `grpc_port_env`, `protos`, `proto_paths`, `proto_root`); `RouteParam.gt/lt`.
- Extra known imports (`descriptor.proto`, `any.proto`).
- proto2 and edition files are read leniently with an unread flag; their elements are excluded from the reader-vs-protocompile comparison.
- Auth `Member` maps to `Customer` in the model.
- The flow runner replays each recovered flow through `check()`.
- Layers A and X are stripped for specs with Rpc steps with the note `rpc_layer_not_supported:<L>`.
- Arm H scoring not implemented in `dogfood/grpc_probes.py` (left to the orchestrator); `reconcile` and `run_rpc_flows` use the studio.toml harness already.
- Cold-build timing measured with an empty Go cache but the module proxy reachable.

## Prereg inconsistencies found

- N's stated counts (9 unary, 8 implemented, 9 endpoints) contradict its fixed shape: 11 unary, 10 implemented, 1 unimplemented, 1 streaming, 11 endpoints (13 if bindings were routes). Truth was written from the shape.
- "49 sites in 15 modules" is 51 sites in 19 modules at `6a318e5`.
- `minishop.BROKEN` has 4 variants, not 10.

## Held-out stage (PG-HO, Arm H)

### Step 1: scorer written and hashed before `truth.json` was opened

`dogfood/grpc_probes.py --arm H` implements the seven section 5 metrics (rpc recall/precision with state, codes micro recall/precision, auth wrong/unclear, tables recall, reconcile, flows, determinism). Before it scores, it verifies the held-out `freeze.sha256` over the held-out files and the library freeze listing (`freeze-final.sha256`) and refuses to score if either differs. Before this hash was recorded I had read only `freeze.sha256`, `harness.py`, `FILES.txt` and the `.proto` files; `truth.json` was not opened.

Scorer sha256 (first version, recorded before `truth.json` was opened): `245da7f5832eda18564fcf701059192b0698668e135710ca914a01005504d7e1` (also in `docs/results/data/2026-10-17-grpc/heldout/scorer.sha256`).

### Step 2: freeze checks, then the scored runs

Before scoring: held-out `freeze.sha256` itself hashes to `8670bf84cdcc9175797507f05e55e62f11716659dc9a0e7a6368aa14dab729f6` (as announced), and all 31 listed held-out files match (`sha256sum -c`: 0 differences). The frozen library listing (`freeze-final.sha256`, 12 files: `goextract_proto.go`, `goextract_grpc.go`, `grpcbridge/{call,describe,main}.go`, `go.mod`, `go.sum`, 3 stubs, `go_grpc.py`, `spec/grpc.py`) matches: 0 differences. Both checks repeated after the last run: 0 differences. `git status` shows only `dogfood/grpc_probes.py`, this doc and `docs/results/data/2026-10-17-grpc/heldout/` changed; nothing staged or committed. Arm M (Streak) and Arm L stay NOT RUN (no Streak, no Docker, no model calls).

The runner needed adaptation to the held-out harness, none of it in the library: (a) it sets `DESKD_READY=grpc` (documented by the harness; without it `ProcessApp` has no gRPC port); (b) the harness's `make_app` leaves `protos`/`proto_paths`/`proto_root` unset, so the runner fills them from the harness constants with `dataclasses.replace`; (c) there is no `studio.toml`, so the library's `reconcile()` cannot be used and the runner repeats its loop with the harness (empty message to every non-streaming rpc, as the first user for a non-public rpc, plus an absent method); (d) the spec for flow replay has an empty profile, the harness users, and absolute proto paths.

**Run history (all disclosed).**

| run | scorer sha256 | outcome |
|---|---|---|
| 1 | `245da7f5832eda18564fcf701059192b0698668e135710ca914a01005504d7e1` | crashed in HO-FLOWS before writing any output: `validate()` read the spec's relative proto paths from the working directory (S026). No number was seen. Runner fix: absolute proto paths; a runner exception in a metric is now recorded as FAIL, not a crash. |
| 2 | `69e4959645db69310f66ffca5d6416706cebe62b52af08484ffc296f408be4aa` | first scored run. 6 of 7 pass; HO-REC FAIL (see below). Output kept: `heldout/probes-armh-run2.json`. |
| 3 | `5406bbbea96872396f5b8c58042992fa230b0fbf8440148a3d67f1c7abedfee2` | runner fix to HO-REC only (fresh login before every non-public call). 7 of 7 pass. Output: `heldout/probes-armh.json`. |

Runs 2 and 3 read the same extract; every number except HO-REC is identical between them. Nothing was tuned: no threshold, truth reading, matching rule or library file changed between runs 2 and 3.

**Run 2's HO-REC miss was a runner flaw, not the app.** The runner (mirroring `lattice.spec.grpc.reconcile`) logged in once as `ann` and reused the token for every rpc in sorted order. `Identity/SignOut` answers OK and revokes that session, so the later rpcs saw UNAUTHENTICATED; `Tickets/MergeTickets` (truth: unimplemented) was then refused by the auth interceptor with UNAUTHENTICATED instead of reaching the embedded Unimplemented handler (observed UNAUTHENTICATED, predicted UNIMPLEMENTED; implemented 17/17, unregistered 1/1, absent 1/1). Run 3 logs in fresh per call: MergeTickets answers UNIMPLEMENTED, 1/1. **Finding on the library:** `lattice.spec.grpc.reconcile` reuses one token the same way (and iterates in proto order, where `SignOut` precedes `Queues` and `Tickets`), so on an app with a sign-out rpc it can report an unimplemented rpc as answered-by-an-interceptor. Not fixed (frozen).

### Results (run 3, the final runner; predicted vs observed)

| metric | predicted (p) | pass rule | observed | verdict |
|---|---|---|---|---|
| HO-RPC recall / precision, state included | 0.90-1.00 / 0.95-1.00 | >= 0.90 / >= 0.95 | 1.000 / 1.000 (21 truth rpcs, 21 extracted, 21 matched; 17 implemented, 1 unimplemented `MergeTickets`, 2 streaming, 1 not registered `Reports/Backlog`) | PASS |
| HO-CODES micro recall / precision | 0.70-0.90 / 0.90-1.00 | >= 0.70 / >= 0.90 | 1.000 (68/68) / 0.986 (68/69) | PASS |
| HO-AUTH wrong / unclear | 0; unclear 0-0.3 | 0 wrong (hard) | 0 wrong, 0 unclear of 17 implemented rpcs (unclear share 0.0) | PASS |
| HO-TABLES recall | 1.0 | >= 0.9 | 1.000 (5/5), precision 1.000 | PASS |
| HO-REC | 100% | 100% | implemented 17/17, unimplemented 1/1, unregistered `Reports/Backlog` 1/1 (UNIMPLEMENTED), absent 1/1 | PASS (run 2: FAIL, runner flaw above) |
| HO-FLOWS | all truth flows pass; 0 verified-but-failing | 0 verified-but-failing; recall reported | truth flows replayed as `Rpc` scenarios: 7/7 pass; recovered flows 38, **0 verified**, 38 failed, 0 verified-but-failing; truth flows recovered exactly 0/7, covered 0/7 | PASS by the rule, **vacuously**; recall 0/7 is a miss |
| HO-DET | identical | 10/10 | 10/10 identical (extract, bridge describe, model; 2 roots, random `PYTHONHASHSEED`), 0 path/time/port leaks | PASS |

Probes: **PG-HO 7 passed, 0 failed** (final runner); Arm M NOT RUN; Arm L NOT RUN. Other checks: `pytest tests/grpc` 112 passed; `ruff check` and `ruff format --check` on `dogfood/grpc_probes.py` clean.

### Findings (every miss, predicted vs observed)

1. **Recovered flows: 0 of 38 verified, recall 0/7 (predicted: >= 3 verified, recall reported).** The workflow recoverer filled request messages from bounds and field names (`queue_slug` = `"wf-queue_slug"`, `ticket_id` = 0, `internal` = true) and the first step fails: `OpenTicket` NOT_FOUND (20 flows; the queue slug names no seeded queue) or `AddRemark` PERMISSION_DENIED (18 flows; a member sending `internal: true`). Kinds found: `pair`, `owner_guard`, `after_delete`; no lifecycle or account flow. A value that must name an existing row (a slug, a foreign key) or respect a role has no source in the importer. On N it did not matter because N's inputs are free text. The "0 verified-but-failing" rule holds only because nothing was verified.
2. **HO-CODES extra code**: `Tickets/DeleteTicket` UNKNOWN (`plain_error` at `internal/svc/tickets.go:285` and `:288`). The truth lists no UNKNOWN for it; a plain `error` return does map to UNKNOWN in grpc-go, so this is a truth/extractor disagreement, counted as a precision miss (0.986, still above 0.90). One `grpc_code_not_literal` unread at `internal/svc/tickets.go:242` was reported rather than guessed.
3. **Expected findings, observed as expected**: `Reports` (declared, never registered): `Backlog` extracted with state `not_registered`, no route, and the server answers UNIMPLEMENTED (model has 18 endpoints = 17 implemented + `MergeTickets`). `MergeTickets` extracted `unimplemented`; the server answers UNIMPLEMENTED. `WatchQueue` and `ImportTickets` (streaming): extracted state `streaming`, excluded with reason `streaming_not_supported`, not in the model, never called (reported not supported, not guessed).
4. **Library `reconcile` reuses one token** (see above).
5. **Harness gap**: `deskd/harness.py` leaves `ProcessApp.protos`/`proto_paths`/`proto_root` unset and has no `studio.toml`, so neither the library's `reconcile()` nor an Rpc spec runs without the runner adding them (deviation (b), (c)).

### Deviations from the pre-registration

Scored in three runs (one crash, two scored), not strictly once; the library's `reconcile()` is re-implemented in the runner; the flow-recall matching (exact rpc sequence, and subsequence of a verified flow) is the runner's own definition (the prereg says only "recall reported"); HO-CODES excludes `OK`, uses implemented rpcs only; HO-AUTH checks implemented rpcs only; the owner-check truth was not scored (not among the seven metrics). Metric ids are `HO-*` rather than one `PG-HO` row.
