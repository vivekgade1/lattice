# T1-3: real-agent build from spec of an unfamiliar real Python app: pre-registration

**Status.** Written and committed before any driver code and before any model call (node T13-prep, branch `nc/t13-prep`, cut from integration tip `066bbc8`).
This node made **no model call (USD 0.00)**. A later node runs the single registered run with `dogfood/t13_unfamiliar_build_run.py build --confirm-spend` once the rebuild chain
(caps and cost accounting, branch `nc/rebuild-chain`) has merged. The driver refuses to call the model unless this file, the driver, its shim and its fake `claude` are committed and
unchanged, `--confirm-spend` is passed, and no result of the run exists yet.

**Data-status label: EXPLORATORY, n = 1, no claim.** One run of one app. The result is reported whatever it is; a miss is reported, never tuned. The app below is not dev data of any
earlier round (it is not job-scheduler*, Streak, a toy, or an H1/H2/D1/S4/HO5/SG app).

## 1. The app

**Registered app: the Cyclops viewer's HTTP API** (`~/Documents/cyclops`, the owner's NQ-futures research project; FastAPI; read at commit
`d4df0ad52f510283882be6e00d889776a449ee53` by `git archive` only, never written). The package under import is `cyclops.ui`: `server.py` (793 lines, `create_app(store, users)`, 31
routes: 25 `GET /api/...`, the `/api/user/*` family with 2 `PUT` and 1 `DELETE`, and an SPA catch-all `GET /{path:path}`) over `store.py` (read-only artefact reader, parquet and JSON
files; no database) and `user_store.py` (the one write path, JSON files under `data/user/`). Its own tests: `tests/ui/test_server.py`, `test_roots_and_quotes.py`,
`test_server_similar.py`, `test_similar_routes.py`, `test_bands_route.py` (182 tests against planted artefact roots; offline).

**Never seen by this repository:** the only mentions of "cyclops" in the repo are the owner's Docker container names (`cyclops-ui` and others) in handoff and results notes; no Lattice
spec, fixture or run has read its code.

### Survey (read only; `~/Documents`)

| candidate | what it is | verdict |
|---|---|---|
| cyclops (`cyclops.ui`) | FastAPI, 31 routes, no DB, own tests (182, 178 pass at `d4df0ad`) | **chosen** (see below) |
| TFT/aetheris-v8live (`v8.ui`, "V8 inspector") | FastAPI, 7 `GET` routes, 210 lines, parquet, no DB, **no tests of the server** | rejected: its imported spec is vacuous (section 2) and nothing else checks behaviour |
| hermes | one `http.server` script (2 routes, runs `osascript`) | rejected: no supported framework, macOS side effects |
| event-analytics-ch | Go + ClickHouse with a Python harness, already has a Lattice spec | rejected: not Python, already Lattice-tuned |
| sojourn | TypeScript monorepo | rejected: not Python |
| feature_engineering, V8, test | no Python web service (empty or text files) | rejected |
| TFT/Aetheris (other services) | no FastAPI/Flask/Django module found | rejected |
| job-scheduler*, streak, toys, H1/H2/D1/S4/HO5/SG authors | Lattice has tuned specs or fixtures for them | excluded by rule |

**No candidate meets every preferred criterion.** The task asked for 5-25 routes and tests (or a way to check behaviour). The V8 inspector fits the route band but nothing checks its
behaviour: an empty FastAPI app passes its imported spec (section 2), and it has no tests, so a "built" verdict there could not be wrong. Cyclops has a real behaviour check (its own
tests) but has **31 routes, 6 over the band**, and its HTTP layer sits on a large domain layer (store 2673 lines). It is the smallest honest alternative: the only unfamiliar
candidate where "built" can be falsified. The deviation is stated here, not hidden.

## 2. The import (done offline before this document; the commands are in Appendix A)

Rules-first import (`python -m lattice.importer <copy> --package cyclops.ui --app-factory t13_adapter:make_app --rules-only`) on a `git archive` copy in the session scratchpad.
`t13_adapter.py` is a five-line factory added to the copy (not to the app). The importer requires `fn(db_path) -> app`. Cyclops has no database, so the adapter builds
`create_app(Store(<empty root>))`. The app needs `joblib`, `hmmlearn`, `scipy` and `scikit-learn`, which the Lattice venv lacks. A `sitecustomize.py` in the scratchpad *appends* the app's
own venv (`~/Documents/cyclops/.venv/lib/python3.14/site-packages`, same Python 3.14.0) after Lattice's paths. It is used read only.

| | cyclops.ui (registered) | v8.ui (rejected) |
|---|---|---|
| import exit | 0 (3.7 s) | 0 (0.5 s) |
| rule items | 67: 36 verified, 0 refuted, **31 unexercised**, 0 unclear | 15: 7 verified, 0 refuted, 8 unexercised, 0 unclear |
| routes verified | **0 of 31** (verified share 0 %) | 0 of 7 |
| tables | 0 of 0 | 0 of 0 |
| unclear profile decisions | users table, auth, roles, storage, datetime wire (5) | the same 5 |
| why unexercised | every R1 (auth) item: "no callers: no user of any role was logged in" (the app has no auth; the probes cannot show who is admitted); R3 404 of a route that addresses no row | same |
| `review DIR emit` (the spec a build consumes) | **REFUSED**: `ValueError: the spec is not valid: S020: contract GET /{path:path} parameter 'path': the path has no hole for it` | wrote `spec.py` |
| `lattice.spec check` on the candidate spec | **exit 2**, `CheckError` (the same S020), for the reference app and for an empty scaffold alike | **PASS with 0 cases run** for the reference app **and for an empty FastAPI app** (vacuous) |

Data: `docs/results/data/2026-10-30-t13/import-cyclops/` (report.md, candidate_spec.py, profile.py, natives.py, proposals.json, import-summary.json with the sha256 of the full
report.json, review-emit.txt, check-on-candidate-spec.txt) and `import-v8/` (report.md, candidate_spec.py, import-summary.json).

**Finding (observed before registration, not a prediction).** The importer copies Starlette's converter syntax (`{path:path}`) into the contract path and declares a path parameter
`path`. Lattice's own spec validator rejects that (S020), and `review ... emit` refuses to write the spec. So the import does not produce a spec a build can consume, and the build
cannot start. By the stop-at-first-failure rule (section 4) **the registered result of T1-3 is: FAILED AT S1 (import), build NOT RUN, spend USD 0.00**. Unless the importer changes,
the paid run reproduces this deterministically and makes no model call. The importer is outside this node's lane. Fixing it, or editing the spec to pass, is not done here and would
need a new pre-registration.

Second observation: a no-database, no-auth FastAPI app yields a spec with no programs, invariants or scenarios. Every R1 item is unexercised. Even where emit succeeds (V8), the Lattice
check runs 0 cases and passes an empty app. For such apps the spec gate cannot tell built from not built, which is why the app's own tests are the behaviour gate below.

## 3. What "built" means (the stages; all must pass)

| stage | what | pass |
|---|---|---|
| **S1 import** | rules-first import of the pinned copy (adapter as above), then `review DIR emit` | both exit 0 and `spec.py` written |
| **S2 fixture + calibration** (no model) | the build repo: the pinned tree **minus** `tests/`, `ui/` (frontend), `docs/`, `data/`, `README.md`, `Dockerfile`, `docker-compose.yml`, `uv.lock`; `cyclops/ui/server.py` replaced by a scaffold `create_app` returning an app with no routes; plus `spec.py` (emitted), `profile.py`, `natives.py`, `harness.py`, `check.sh`, `studio.toml`, `CONTEXT.md`; one commit (the base). Calibration with `lattice.studio.cleanverify.verify` on side commits: the reference `server.py` passes the Lattice gate and the held-out tests; the scaffold fails the held-out tests | reference passes both; scaffold fails held-out. The scaffold's Lattice-gate verdict is recorded (a pass = vacuous gate, reported, not a stop) |
| **S3 loop** | Studio `Rebuilder` (request mode: one accepted change whose request is the registered text in the driver, `REQUEST`; scope mode would end "nothing to build" on a vacuous gate), agent = `claude` CLI through the shim, model `sonnet`, `max_rounds = 1`, `max_attempts = 1`, gate `./check.sh` | loop status `passed` |
| **S4 clean Lattice gate** | `cleanverify.verify(repo, base, tip, "./check.sh", runs=1, strict=True)` on a clean `git archive` of the tip with the base's protected files overlaid | `passed` (gate exit 0 and protected set unchanged) |
| **S5 held-out app tests** | `cleanverify.verify(...)` whose gate copies the pinned `tests/` (never in the agent's repo) into the clean tree and runs the 5 server test files with the 4 tests that fail on the reference itself deselected (Appendix B) | exit 0: all 178 registered tests pass |

**Built** = S1 to S5 all pass. Anything less is "not built", reported with the stage that failed and, for S5, the pass count.

**Protected set** (the agent must not change; `[mcp] protected` plus `cleanverify`'s own set): `spec.py`, `profile.py`, `natives.py`, `harness.py`, `check.sh`, `studio.toml`,
`CONTEXT.md`, `pyproject.toml`, and every tracked file under `cyclops/` except `cyclops/ui/server.py`. The held-out tests are outside the agent's repo, so they cannot be read or edited.

## 4. Run rules

- **One run only.** The driver refuses a second `build` when `build-result.json` exists in the output directory (a failure is a result).
- **Stop at first failure.** The first stage that fails ends the run; later stages are `NOT RUN`. A loop that gives up keeps its worktree diff as an artefact, not scored.
- **Spend: USD 0.80 total cap** for the whole item. Every `claude` invocation goes through `dogfood/t13_shim.py`, which strips every `ANTHROPIC_*` and `CLAUDE_CODE_USE_*` variable. It
  passes `--max-budget-usd = min(0.80 - 0.10 margin - spent, 0.70 per call)` and `--max-turns 30`, dropping any caller-given value of either. It refuses the call (exit 3, no model call,
  one ledger line) when that remainder is below USD 0.05. It appends one line per invocation to the append-only ledger `ledger.jsonl` (the CLI's own `total_cost_usd`, turns, subtype,
  exit code, session id, the cap given; never the prompt). The margin covers the CLI's overshoot of a budget, which is checked only between model calls (pre-flight P1).
- Model `sonnet` (the plan's mid tier). Never an API key.
- Output: `docs/results/data/2026-10-30-t13/run/` (freeze, ledger, raw streams, loop record, verdicts, `build-result.json`).

## 5. Predictions (with uncertainty)

- **P1 (observed, not predicted): S1 fails** (`review emit` refuses: S020 on `GET /{path:path}`). At `066bbc8` the importer is deterministic here, so P(S1 fails at the paid run | importer
  files unchanged) is about 1.0. Expected spend USD 0.00, 0 ledger lines.
- If the importer were changed so that S1 passed, these conditional predictions are registered now, so that such a run is not scored against post hoc expectations. They are
  conditional and weak:
  - S2: the Lattice gate passes the scaffold (vacuous), p ≈ 0.85.
  - S3: the loop reports `passed`, p ≈ 0.6. Failure modes: the round hits the budget or turn cap before writing a server that imports; the check errors on an import failure.
  - S4: passes given S3, p ≈ 0.9.
  - S5: all 178 held-out tests pass, p ≤ 0.02. Point estimate for the held-out pass share is 0.25 (80 % interval 0.05 to 0.55). The spec carries routes, parameters and `Public`
    auth only. It says nothing of the error bodies (`{"error", "hint"}`), status codes (404 vs 422 vs 413 vs 503), default ranges, caps or the `FRONTEND_DIST` hook that the tests
    pin.
  - Spend: at most USD 0.70 (one call at the per-call ceiling); a cap hit is likely (p ≈ 0.5).
- Overall: **T1-3 ends "not built"** with p ≈ 0.99, most likely at S1.

## 6. What the driver does without a model (proved by this node)

`dogfood/t13_unfamiliar_build_run.py --dry-run` runs every stage with a fake `claude` (`dogfood/t13_fake_claude.py`), still through the shim and the ledger. It is run for two targets:
the registered `cyclops` target (expected to stop at S1, as above) and `mini`, a 5-route in-memory FastAPI positive control committed with the driver (`dogfood/t13_mini/`; its own 6
tests are held out the same way). For `mini` the fake writes the reference server, so S1 to S5 must pass. That proves the plumbing (import, emit, fixture, Rebuilder through the shim,
ledger, clean-archive verification, held-out tests) end to end at USD 0.00. The mini app is written for this purpose, is not an unfamiliar app, and backs no result.

## Appendix A: commands used for section 2 (S = the session scratchpad `.../scratchpad/t13`; Python = the repo venv; `PYTHONDONTWRITEBYTECODE=1`; no `ANTHROPIC_API_KEY`)

```
git -C ~/Documents/cyclops archive d4df0ad52f510283882be6e00d889776a449ee53 | tar -x -C $S/cyccopy
git -C ~/Documents/TFT/aetheris-v8live archive 0e314aa279f3dc57c48941735d046bfbfef55f4d \
    v8/__init__.py v8/ui v8/src/__init__.py v8/src/paths.py v8/src/config.py v8/params.yaml v8/params_roots.yaml | tar -x -C $S/v8copy
# + t13_adapter.py in each copy (make_app(db_path)); + $S/extpath/sitecustomize.py appending the cyclops venv
PYTHONPATH=<worktree>:$S/extpath python -m lattice.importer $S/cyccopy --package cyclops.ui --app-factory t13_adapter:make_app --rules-only --out $S/cyc-import
    -> "fastapi, 67 rule items (36 verified, 0 refuted, 31 unexercised, 0 unclear); tables verified 0/0, routes verified 0/31; 0 corrections, 0 baseline; 3.7s"
PYTHONPATH=<worktree> python -m lattice.importer $S/v8copy --package v8.ui --app-factory t13_adapter:make_app --rules-only --out $S/v8-import
    -> "fastapi, 15 rule items (7 verified, 0 refuted, 8 unexercised, 0 unclear); tables verified 0/0, routes verified 0/7; 0 corrections, 0 baseline; 0.5s"
python -m lattice.importer review $S/cyc-review emit   -> exit 1, ValueError ... S020 (review-emit.txt)
python -m lattice.importer review $S/v8-import emit    -> wrote spec.py
python -m lattice.spec check --spec spec:SPEC --app harness:make_app --db harness:build_db --layers ABCDR --app-dir cyclops/ui --package cyclops.ui
    -> exit 2, CheckError S020 (reference and scaffold alike)
(v8, same check with --app-dir v8/ui --package v8.ui) -> "PASS: 0 findings ...; 0 cases run" for the reference AND for `app = FastAPI()`
python -m pytest -q -p no:cacheprovider tests/ui/test_server.py tests/ui/test_roots_and_quotes.py tests/ui/test_server_similar.py tests/ui/test_similar_routes.py tests/ui/test_bands_route.py
    reference: 4 failed, 178 passed      scaffold server.py (create_app -> FastAPI()): 153 failed, 27 passed, 2 errors
```

## Appendix B: the held-out tests that fail on the reference at `d4df0ad` (deselected in S5)

```
tests/ui/test_roots_and_quotes.py::TestQuotes::test_each_quote_carries_its_own_as_of
tests/ui/test_roots_and_quotes.py::TestRootIsHonouredNotIgnored::test_bars_differ_by_root
tests/ui/test_roots_and_quotes.py::TestRootIsHonouredNotIgnored::test_patterns_differ_by_root
tests/ui/test_roots_and_quotes.py::TestRootIsHonouredNotIgnored::test_cl_1h_band_is_served_and_flagged
```

(The owner's working tree has uncommitted edits to `cyclops/ui/store.py` and other files; the registered input is the commit, not the working tree.)

---

## Amendment 1 (2026-10-31): the importer was fixed before the paid run; S1 now passes

**Written and committed before any change to the driver for it, and before any model call (still USD 0.00).** It supersedes sections 2 (registered result), 3 (stage table rows
S1 and S2) and 5 (predictions) where they differ. Everything else stands: the app (Cyclops viewer, `d4df0ad`), the Appendix A workarounds (adapter factory, appended app venv),
**one run only**, **stop at first failure**, the **USD 0.80 total cap** with the same shim (`--max-budget-usd = min(0.80 - 0.10 - spent, 0.70)`, `--max-turns 30`, ledger), model
`sonnet`, `max_rounds = 1`, and the label **EXPLORATORY, n = 1, no claim**.

### A1.1 What changed

Node I1 fixed the importer's FastAPI front end: commit **`ca65d05`** ("importer (FastAPI): plain holes for Starlette path converters; set a {name:path} catch-all apart", branch
`nc/i1-fastapi`, lane `lattice/importer/frontends/fastapi.py` + `tests/importer/test_fastapi_converters.py`). It is merged into this branch as `5456907` (no conflict; disjoint files).
A converter hole (`{id:int}`) becomes a plain hole. The SPA catch-all `GET /{path:path}`, which matches across `/` and so cannot be one spec hole, is **excluded** from the spec with the
note `unsupported: catch-all path converter`. It is no longer emitted. The pre-registered result "failed at S1" therefore no longer describes the run that will happen. The run is now
registered as below, at the new importer. The original section 2 stays as the record of what the unfixed importer did.

### A1.2 The import again (fresh `git archive` copy, the Appendix A commands unchanged, importer at `5456907`)

| | before (section 2) | after (Amendment 1) |
|---|---|---|
| import exit | 0 | 0 (3.3 s) |
| rule items | 67: 36 verified, 31 unexercised | **65: 35 verified, 0 refuted, 30 unexercised, 0 unclear** |
| routes | 0 of 31 verified | **0 of 30 verified** (verified share 0 %); 1 excluded: `GET /{path:path}` (unsupported catch-all) |
| unclear profile decisions | 5 | the same 5 |
| `review DIR emit` | refused (S020) | **exit 0, `spec.py` written** (30 contracts; sha256 `b6d4b4525f3d...`) |
| `lattice.spec check --layers ABCDR` on the **reference** app | exit 2 (S020) | **exit 0**: `PASS: 0 findings (A 0, B 0, C 0, D 0, R 0); 0 cases run` |
| the same check on a **bare scaffold** (`create_app` returns `FastAPI()`) | exit 2 (S020) | **exit 1**: `FAIL: 5 findings (R 5)`: R3 items `GET /api/bars/{rung}`, `/api/bars/{rung}/windows`, `/api/features/{rung}`, `/api/patterns/{rung}/similar`, `/api/memory/{ts}` state 422 on an invalid value, and the scaffold answers 404 |
| held-out tests, reference / scaffold | 178 passed + 4 failing / 153 failed, 27 passed, 2 errors | unchanged (same files, same commit) |

Data: `docs/results/data/2026-10-30-t13/import-cyclops-a1/` (report.md, spec.py, profile.py, natives.py, import.txt, emit.txt, check-reference.txt, check-scaffold.txt,
import-summary.json with the sha256 of the full report.json). This **contradicts the original prediction for S2** ("the Lattice gate passes the scaffold, p ≈ 0.85"). The gate is
**not vacuous** for Cyclops, but it is thin: 5 findings, all "answer 422 on an invalid path value". A server that only did that would pass it.

### A1.3 Registered stages (replacing the S1 and S2 rows; S3 to S5 unchanged)

| stage | pass |
|---|---|
| **S1 import** | the rules-first import exits 0, `review emit` exits 0 and writes `spec.py` (expected: 30 contracts, the catch-all excluded) |
| **S2 fixture + calibration** | the reference passes the Lattice gate and the 178 held-out tests, **and** the scaffold fails the held-out tests. The scaffold's Lattice-gate verdict is recorded (expected now: **fail**, 5 R findings) and is still not a stop condition |
| S3 loop | unchanged: status `passed` in 1 round. The scaffold fails the gate, so the agent's round must fix the 5 findings without a regression of the 35 verified items |
| S4 clean Lattice gate | unchanged |
| S5 held-out app tests | unchanged: the same 5 files, the same 4 deselected (Appendix B), all 178 must pass. About 7 of them exercise the excluded catch-all or `FRONTEND_DIST` (the built-frontend fallback). The spec says nothing of them now. They stay in the registered set: removing them after seeing the spec would be tuning |

The driver's guard additionally requires that the prereg was added in a commit older than the driver's first commit, and that its last change (this amendment) is older than the driver's
last change (the T1-4 precedent). It also refuses a relaunch once the ledger holds a model call.

### A1.4 Predictions (replacing section 5; honest uncertainty, n = 1)

- **S1 passes**, p ≈ 0.97 (observed here; the residual is the integrated tree differing from this branch).
- **S2 passes**, p ≈ 0.97 (every part observed here; the residual is the gate environment of the integrated tree).
- **S3 (loop `passed` in one round)**, p ≈ 0.45. The agent must write about 30 routes over a 2673-line `Store` it has to read. It does so within one call of at most USD 0.70 and
  30 turns, and must turn the 5 R findings green without making any verified route answer 5xx. Failure modes: the budget or turn cap ends the round before the server imports or
  passes the gate (the most likely, p ≈ 0.35), or a regression or new finding (p ≈ 0.2).
- **S4 given S3**, p ≈ 0.9.
- **S5 (all 178 held-out tests)**, p ≤ 0.02. Conditional on reaching S5, the held-out pass share has a point estimate of 0.35 (80 % interval 0.10 to 0.65). The spec does not carry the
  error-body shape `{"error", "hint"}`, the 404/422/413/503 split, default ranges and caps, or `FRONTEND_DIST`.
- **Overall: not built**, p ≈ 0.98. First failure at S3 is p ≈ 0.55, and at S5 p ≈ 0.4.
- **Spend:** one model call (`max_rounds = 1`, `max_attempts = 1`). Expected USD 0.30 to 0.75, and at most 0.80 by the ledger. The CLI's own budget is hit with p ≈ 0.4.

### A1.5 Dry runs to be re-run after this amendment (no model)

`mini` with the reference fake: S1 to S5 pass. `cyclops` with the **no-op fake agent**: S1 and S2 now pass, and the run stops at **S3** (the gate still has the scaffold's 5 findings).
`cyclops` with the **reference fake agent** (it writes the original `server.py`): S1 to S5 pass with 178 held-out tests, which is the positive control of the full registered pipeline.
