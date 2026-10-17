# Change impact engine (Spike D): results

2026-10-16, branch `np-impact` (worktree `lattice-impact`, base bb3ff0c + the pre-registration commit). Pre-registration:
`2026-10-16-change-impact-prereg.md` (with Amendment 1). Data: `docs/results/data/2026-10-16-change-impact/`
(`dev/`, `dev-timing/`, `changeset/` = probes.json + summary.md + raw engine and truth JSON; `mutants.json`,
`freeze.sha256`, `pytest-before.txt`, `pytest-after.txt`, `studio-check-sh.txt`). No commit was made; no model, no
Anthropic key anywhere in the engine. **(m)** = measured. A differing probe is a finding; nothing below was tuned.

**What the engine says about workflows (Amendment 1, ruling 1).** Which workflows fail is mostly not decidable before
the change exists: a flow records today's answers and passes today by construction. Only a changed invariant (T1) and an
exact-prefix contradiction (T2) decide a workflow; every other flow is T3 `may_fail`. On the held-out set T1 found 0
workflow failures and T2 made 0 flow claims, while 8 of the 21 changes had flows that fail on S+ref (c03: 2, c08: 6);
T3 `may_fail` covered 8/8 of them, with 352 `may_fail` listings in all (precision low by design).

## Files (owned only; `git diff --stat` = tools.py +194, `__main__.py` +1, API.md +21; the rest untracked)

| file | lines |
|---|---|
| `lattice/spec/impact_report.py` (inputs, T1, schema, render, CLI) | 1205 |
| `lattice/spec/impact_tier2.py` | 837 |
| `lattice/spec/impact_tier3.py` | 1121 |
| `lattice/mcp/tools.py` (`_has_impact_table`, `_build_impact`, `_impact_view`, additive) | +194 |
| `lattice/spec/__main__.py` (one `AUTHORING` line) | +1 |
| `lattice/studio/API.md` (v1.23, documentation) | +21 |
| `tests/spec/test_impact_report.py` (58 tests), `tests/mcp/test_change_impact.py` (13), `tests/spec/fixtures/impact/` (7 dev changes on minishop) | 1323 / 305 |
| `dogfood/impact_probes.py`, `dogfood/impact_mutants.py`, `dogfood/impact_truth/main.go` | 2210 / 394 / 198 |

Freeze (`freeze.sha256`, taken before the change set was scored; sha256):

```
a3e11f1c266a813a66e33d8943b8773705a419ce8dde806615203d750ae0137c  lattice/spec/impact_report.py
6f86fc6cfb2b7f2a6e485633d810e55782fdfbcfdce71921bff8f9cff560a90a  lattice/spec/impact_tier2.py
22228513beba44fcff4b0d5ab48c662d2010e7ac320b73ac860f134ffb6e2faf  lattice/spec/impact_tier3.py
bb5cfb53f177c2f3daf8df5eae81dbcc2e60497f603552939d7e25f932698c22  lattice/mcp/tools.py
21eab9f36cce4c93cc34fd26a327077b66bfe4defc12ca5572f92c93880a700f  dogfood/impact_probes.py
c117671b031b7a45836e2e0c57f95aa499b5628489b0b1df001cd014713e20f4  dogfood/impact_mutants.py
ab435db1cded46007f77d2f03eee7f76272a8fb4c62a7b7237b0a7115ffce24a  dogfood/impact_truth/main.go
```

MANIFEST.sha256 of the change set: 52e0e541e2e2f1347f38d0c3f2000d68ecbe94d64e884d6bc02dfff7d523caa3 (matches the coordinator's).

## Regression and lint (m)

* `pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck`: before 2428 passed, 2 failed, 29 errors,
  5 skipped (788.7 s); after 2499 passed (+71 new), 2 failed, 29 errors, 5 skipped (771.6 s). The failing/erroring id
  sets are identical (31 ids: 17 + 12 DB-driver `ModuleNotFoundError`s in test_db/test_multidb, `test_outside` flake,
  one test_multidb container case).
* `dogfood/studio/check.sh`: PASS, 0 findings, 481 cases. ruff check and ruff format --check on every created or
  changed .py: clean; `gofmt -l` empty and `go vet` clean on `impact_truth`.
* Unit tests: 71 (58 + 13), 5.5 s. Mutants: **16/16 killed** (15 by named unit tests; IX11 by the frozen-file
  hash check of PB), control passes first; `mutants.json`.

## Probes: predicted vs observed

Dev stage = seven changes I wrote on S (a0679e9) from the pre-registered kinds (not the held-out set), H-nat, byte-compat,
no-write, leak. C stage = the held-out set (21 changes, 15 kinds K1-K15, all 21 survive the ref check, 0 excluded;
c15 and c21 have an empty `ref.diff`, S+ref = S by the coordinator's ruling). MySQL 8.4 containers, Streak
unchanged (its ` M studio.toml` predates this work).

| id | prediction | observed (m) | result |
|---|---|---|---|
| PT1 (C) | exact on all; control 0; T1 non-empty on 11-15 of C | 22/22 exact (21 + control), 0 false, 0 missed; **non-empty on 19 of 21** | PASS; the count prediction was wrong (19, not 11-15) |
| PT1 (dev) | exact | 7/7 exact, 0 false, 0 missed (111-finding invariant case included) | PASS |
| PT1x | 10/10 identical | C c01-c03: 10/10 each; dev0-2: 10/10 each | PASS |
| PT1l | 100% | C 47/47, dev 112/112 | PASS |
| PT1f | 0 `workflow_fails` on base; planted copy is `baseline_drift` | 59 flows run, 0 `workflow_fails`, 0 drift on base; the planted flow (201 -> 418) is the only `baseline_drift` | PASS |
| PT2 | precision 1.0; recall 0.10-0.40 | C: 4 claims, 4 correct (precision 1.0, step agreement 4/4); **recall 4/42 = 0.095** (below the predicted band); dev: 1/1 | PASS (precision); recall reported, below prediction |
| PT2i | same determined set | all 21 + 7 identical (spec-only claims are `conditional`, full are `determined`, as designed) | PASS |
| PT2w | 0 claims on Streak: vacuous | 0 claims; flow truth is non-empty for c03 (2 flows) and c08 (6): the claims are vacuously precise, recall 0/8 | PASS (vacuous, stated) |
| PT2r | as designed; Streak `not_applicable` | cancel->404 program: one `runtime_disagreement`; two harmless program edits: none; Streak: `not_applicable: 0 programs` | PASS |
| PT2d | complete | C 21/21, dev 7/7 | PASS |
| PT3 (C) | file recall 0.85-1.0, decl recall 0.65-0.90; precision files 0.20-0.50, decls 0.05-0.25; pass: files >= 0.80, decls >= 0.60, precision >= 2x baseline, median <= 12, at most 1 change with zero true files | file recall **0.864** (in band); decl recall **0.628** (below the band, above 0.60); file precision **0.109** vs all-files baseline 0.029 (3.8x; below the band); decl precision **0.043** (below); median files **8**; **2 changes with zero true files (c14, c19: both `nudger.go`)**; total non-test files in S: 36 (the pre-registration said 42) | **FAIL** (on the "at most 1" criterion; every other criterion holds) |
| PT3w | recall >= 0.8 | 8/8 = 1.0 (352 `may_fail` listed over 21 changes) | PASS |
| PU (C) | 100% | 767/768 locations pass `check_code`; **1 fails**: c06 `internal/nudges/checkin.go:94`, "the symbol 'AfterCheckIn' is not on" that line (a status-write location carries its enclosing function as `symbol`; the line is inside it, not its declaration); 0 extract `unread` missing; the 2 literals map to `NewRouter` and `setTestClock` | **FAIL** |
| PU (dev) | 100% | 214/214, literals ok | PASS |
| PD (C) | 10/10 identical, none | 20/21 clean; **c04 flagged**: the scan found `2026-03-04T12:00` in the report; it is spec text (a changed rule: "clock 2026-03-04T12:00:00Z"), identical in all 11 runs and in the second root; no path, host or run time | **FAIL by the scan's letter**, a false positive of the ISO scan |
| PD (dev) | | 7/7 clean | PASS |
| PN (H-nat) | T1 35 failing; resolved = {`access__logout__as_anon`}; T2 0; T3 3-5/5 files | T1 35/35 exact, resolved exactly `access__logout__as_anon`, T2 0 claims; **T3 files 1/5** (`cmd/server/main.go` only), decls 0/5 (prediction missed; 4 added files kept out) | T1 exact; T3 report-only miss |
| PI | identical except kind/change | identical (the record's own `new_*` written out as a directory) | PASS |
| PS | same (target, label, rules) | Studio `checks.impact.failing` = report T1 = [(`scenario:password_length_bounds`, `not_built_yet`, [R9])] | PASS |
| PW | all 0 | 0 changed, 0 new, 0 shim calls over CLI x10 + 1 full, MCP x10 readonly (10 observe refused with `readonly:`, 20 answered) + 1 non-readonly observe | PASS |
| LEAK | 0 | 0 owned containers after a fault run (`RuntimeError`, exit 3) and an MCP timeout (25 s); 0 after the C stage | PASS (after the fix below) |
| PB | all | help (4 commands), instructions and tools/list in 8 configs, ask --json, route table, `/api/model`+`/api/ask`, change record, goextract output bytes on S: identical to bb3ff0c; `incremental.py`, `server.py`, `check.py`, `implementation.py`, `gocheck/`, `session.py`, `cli.py`, `importer/` have an empty diff; tools/list with `[impact]` = base + 1 (plain and with `[implementation]`); pytest same failing ids | PASS |
| PX | T2+T3 <= 3 s; T1 <= 180 s | T2+T3 0.1 s (below the predicted 0.3-1.5 s); T1 with 2 ACD runs + 59 flows 70.3-73.4 s | PASS |

C stage wall time 4665 s. Dev T1 runs: candidate 34.6 s, base 30.6 s (S, ACD). H-nat engine 590-609 s (S4's 10 `Await`
scenarios run to their timeout).

## Findings (raw data in `changeset/`, `dev/`)

1. **PT3 fails on the "at most 1 change with zero true files" criterion (2: c14, c19).** Both edit `nudger.go`
   (a nudger rule). Their changed targets are scenarios with `Await` steps on the nudger service and no machine
   change, so no seed reaches the nudger: the extract has no route to it. The pre-registration named this wiring
   limit; the cost was 2 of 21.
2. **PU: one symbol miss (c06).** For a `status_write` location the engine puts the enclosing function's name as
   `symbol` on the write's line. A fix (symbol only when the line is the declaration line) was not made: the
   change set had been scored.
3. **PD: c04's ISO "leak" is rule text from the REQUIREMENTS diff**, not a run time. The scan cannot tell them
   apart; the engine's output contains no wall-clock value.
4. **T2 recall 0.095**, below its band (a prediction miss, not a pass criterion): most changes alter an expectation
   whose request has no identical prefix in the corpus or no incompatible answer, or are new requests.
5. **T3 precision is low** (files 0.109, declarations 0.043) as the pre-registration said it would be reported;
   lift over the all-files baseline 3.8x. T3 file recall on H-nat is 1/5 (`push.go`, `models.go`, `nudger.go`,
   `account/routes.go` are not reachable from a closure).
6. **T1 was non-empty on 19 of 21** (predicted 11-15): the change set is rich in checks the app fails today.
7. S has 36 non-test Go files under `cmd/` and `internal/`, not 42 (the all-files baseline uses 36).

## Post hoc corrections (all before the change set was run; none after seeing C)

| what | old | new |
|---|---|---|
| runner `independent_check` | ran with `cwd` = the app clone, so `spec:SPEC` imported the clone's own spec (PT1: 112 false) | copies only REQUIREMENTS.md/spec.py/spec.trace.json into a fresh directory used as `cwd` and first on `PYTHONPATH` |
| runner PU, literals | `Resolved.line == decl.start` (go/parser start includes the doc comment) | line within the declaration's range |
| runner fault script | `import lattice.spec.check as C` (the package exports a function of that name) | `importlib.import_module("lattice.spec.check")` |
| runner PS | removed only `[implementation]` from the clone's toml (a second `[state]` table: TOML error) | removes `[implementation]`, `[state]`, `[impact]` |
| runner PI | compared against the dev dir (different trace hash) | writes the record's own `new_md/new_module/new_trace` and compares those |
| runner PB tools check | compared the S clone's lists | four configs (plain/impl, with/without `[impact]`) in the base tree and this tree |
| runner empty `ref.diff` | `patch` on it | S+ref = S (coordinator's ruling) |
| mutant IX8 | grepped the whole route path (no hit in the fixture: survived) | greps the last path segment (killed) |
| engine `owned_containers` | always minted its own owner token; an MCP-timeout kill left the tool's token unmatched (LEAK found 1 container) | keeps the caller's `LATTICE_CONTAINER_OWNER` if set (unit test added) |
| lint | `# fmt: skip` lines, long lines | ruff format, wrapped |

## Decisions the pre-registration did not fix

* `--tiers 1` with no `[app]`: `not_run:no_app`, exit 0 (exit 3 only when T1 started and raised).
* No `REQUIREMENTS.md` on a side: empty rule diff with every trace rule unchanged, plus a note.
* `--baseline FILE` is a check `--json` report; its claims are `conditional`. The runner's `baseline_report` helper
  writes one. The default MCP pure read uses `--baseline none`, so T2 there rests on verified flows only.
* A rule whose linked targets did not change is `compatible_expectation`; a rule with no links, `no_linked_check`;
  contracts, services and machines are `invariant_or_machine`.
* L011 pairs new in the candidate (absent from the base) are listed as `spec_contradiction`.
* D4c replays only scenarios touching endpoints whose program text changed, reusing `check._tokens`,
  `_insert_given` and `_template_of` read-only; the CLI offers it only on a sqlite-compatible harness.
* T3 seeds: step-level change detection for scenarios; machine writes filtered by the changed edges' states;
  `expect_db` columns and `profile` changes seed struct/column lines; insertion uses the longest whole-segment
  prefix beyond the common router prefix, else `main`.
* Item `file/line`: spec line from an AST literal `Scenario(...)`; Streak builds many scenarios by helpers, so most
  T1/T2 items carry `file: null` with `why`.
* PW's MCP snapshot places the candidate inside the clone (the tool takes project-relative directories) and snapshots
  with it in place.
* The C-stage LEAK row counts containers owned by tokens `impact-<12 hex>` / `impactp-*`; another agent's containers
  (`impact-author`) are not counted. The dev stage ran concurrently with the C stage; PW, PX and LEAK were rerun
  alone afterwards (`dev-timing/`).

## Commands

```
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=.../lattice/.venv PYTHONPATH=.../lattice-impact PYTHONDONTWRITEBYTECODE=1
uv run pytest tests/spec/test_impact_report.py tests/mcp/test_change_impact.py -q          # 71 passed, 5.5 s
uv run python dogfood/impact_mutants.py --scratch $S/mut --out .../mutants.json              # 16/16, 15 s
uv run pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck -q -rfE        # 771.6 s
sh dogfood/studio/check.sh                                                                   # PASS 481
"$DRV" dogfood/impact_probes.py --stage dev --scratch $S/p --out $S/p-data --only PT1,...    # all but C, LEAK/PW/PX alone
"$DRV" dogfood/impact_probes.py --stage changeset --changeset $S/changeset --scratch $S/c --out $S/c-data   # 4665 s, once
```

## Orchestrator verification and rulings
- The change set was authored by a separate agent and hashed by the orchestrator before the gated probes (manifest
  `52e0e541...caa3`); the builder's runner scored it once with the frozen engine (this is the builder's run on the
  orchestrator's go, not an orchestrator-run score). All 7 freeze hashes verified OK at commit time.
- Independent re-checks: 71 unit tests pass; mutants 16/16 killed; ruff clean on the changed files; Tier 1 re-compared
  from the stored raw engine and truth JSON with a separate script: 22/22 equal, 19 of 21 changes with non-empty
  truth (matches PT1). A fresh runner re-run on a four-change subset (c01, c05, c14, c15, 2286 s) reproduced PT1 5/5
  exact, PT1x identical, PD clean, LEAK clean; PT3 failed again on c14's zero true files (the same limitation).
- **Gates not met, stated plainly (not tuned):** PT3 (2 changes with zero true files against at most 1: c14 and c19,
  the nudger wiring limit), PU (1 of 768 locations: c06 `checkin.go:94`, a symbol line inside the function), PD (c04's
  ISO scan hit is rule text from the requirements diff, a scan false positive, but the gate's letter is failed).
- **T2 recall is 0.095 (4 of 42), below the predicted 0.10-0.40; precision 1.0 (4 of 4).** Which workflows fail is
  mostly not decidable before the change exists: Tier 1 is exact, Tier 2 decides little, Tier 3 lists `may_fail`
  with unknowns, and Spike E turns them into observations.
