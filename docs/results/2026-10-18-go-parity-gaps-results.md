# Go-behind-Python gaps, ranks 2 to 5 of J0 (Spike J, node J1): results

2026-10-18. Build node, worktree `/home/user/wt/j1` (branch `go-parity-gaps-j1`, from `next-phases` `be26a94`, which now contains
Spikes E, H and I). Pre-registration: `docs/results/2026-10-17-go-parity-gaps-prereg.md` with Amendment 1, followed to the letter
except where section 3 says otherwise. Data: `docs/results/data/2026-10-18-go-parity-gaps/`.

**NOT RUN, first line: Arm M (Streak a0679e9 on MySQL 8.4: M1 the rules-first import of Streak, M2 derived `GoPolicy` against
`from_spec` on Streak's spec, M3 Streak `lattice check --layers ACD` byte-identical) is NOT RUN. There is no Streak checkout and no
Docker in this node (a recording `docker` shim stayed first on `PATH`: 0 calls). No model was called, no API key was used, no
sub-agent was spawned, nothing is committed or staged (`git diff --cached` is empty). A NOT RUN is not a pass.**

**Amendment 1 ends "J1 is pre-registered only; no build is started this round". The orchestrator dispatched this node and the
user's request was "all of it", so the build was done. That is deviation 1 (section 3).**

## 1. Verdict

* Arm S probes, **gated rows: 19 PASS, 7 FAIL, 0 NOT RUN** (26 rows: PI1-PI7, PA1-PA4, PG805, PR1-PR4, PB1-PB3, PW, PX, and PB split into
  PB-F, PB-X, PB-G, PB-P, PB-H). The gate "every gated row of section 4" is **MISSED**.
  * PASS (19): PI1, PI2, PI4, PI7, PA2, PA4, PG805, PR1, PR2, PR4, PB1, PB2, PB3, PB-X, PB-G, PB-P, PB-H, PW, PX.
  * FAIL (7): **PI3** (verified share 0.50 against >= 0.60; 4 refuted against 0-3, one with no correction), **PI5** (no machine proposal),
    **PI6** (`review list` shows 3 corrected items under other ids), **PR3** (the corrected spec's verdict map differs from the import's in
    6 ids), **PA1** (`users_table` is `User`, the pre-registration wrote `users`), **PA3** (neither mutant is seen by layer A), **PB-F**
    (8 of 26 frozen hashes in section 7 differ, because `next-phases` moved; against the re-baseline `be26a94` all 26 are identical).
* Not in the 26: **PB-T** **PASS**: the same failing and skipped ids as base (7 failed, 37 skipped, 101 errors in both; 41 new tests pass, 2868 -> 2909 passed), **PB-D** **PASS**: `dogfood/studio` check, layers DX, 62 scenarios and 509 cases, 0 findings, on both the base tree and this tree (venv python, deviation 10).
* Mutants: **14 of 16 killed** (control first: passes). JM5 and JM6 survive (layer A sees neither edit, F3/F4); JM11 is killed by a unit test only.
* Layer R and layer B on Go over SQLite (the user-visible gain, S1, `tests/spec/goapp` byte-frozen): **R verified 16 of 23 items, 0 refuted;
  B 32 cases, 0 findings on the safe app, both existing bugs found on the right endpoint; both 10 of 10 repeatable.** Rank 4 and rank 5
  are closed for goapp with no core edit, as D6 predicted.
* Rank 2 (rules-first import of a Go app): the import of S2 runs end to end and is 10 of 10 byte-identical. Its quality is below the
  gate (PI3, PI5, PI6, PR3). Rank 3 (layer A policy derivation): the derivation is right on both fixtures (PA1 except one string, PA4, PA2)
  but the derived/imported policy does not make layer A see the two edits (PA3), for two reasons the pre-registration did not foresee
  (F3, F4).

## 2. What was built

New files (all under the worktree; nothing committed):

| file | what | lines |
|---|---|---|
| `lattice/importer/go_rules.py` | `harness_factory` (D1), `infer_owners` (D2), `InferringFrontEnd` | 140 |
| `lattice/gocheck/derive.py` | `derive_go_policy`, `go_spec_policy`, `policy_extract` (D3) | 158 |
| `tests/spec/goginapp/` | S2: Gin + GORM + glebarez/sqlite; `main.go`, `api/api.go`, `models/models.go`, `go.mod`, `go.sum` (116 lines), `studio.toml` | 49 + 196 + 28 |
| `tests/spec/gogin_harness.py` | S2 harness: lazy `go build -mod=readonly` into the system temp dir, `make_app`, `RUNTIME`, `LOGIN`, `build_db` | 104 |
| `tests/spec/go_harness_rb.py` | S1 harness: role-aware seed (`variant2`), 4 contracts, 3 programs, `LOGIN`, `RULES` | 240 |
| `tests/importer/test_go_rules.py`, `tests/gocheck/test_go_derive.py`, `tests/spec/test_go_layers_rb.py` | 18 + 13 + 10 = **41 new tests**, all green | 282 + 195 + 140 |
| `dogfood/go_parity_probes.py`, `go_parity_mutants.py`, `go_parity_posthoc.py` | the runner, the mutants, the post-hoc explanations | 1222 + 402 + 111 |

Edited, additive only (`git diff --numstat`): `lattice/importer/rules.py` +57 -1 (the D1 branch, `_go_policy`, `inferences`, `go_policy.json`),
`lattice/gocheck/src/goextract.go` +12 -1 (the opt-in `policy` request key and the `extractModulePolicy` entry),
`lattice/gocheck/src/goextract_tables.go` +27 (`xColumn.JSON`, `json.RawMessage` with `omitempty`, `jsonFact`).
`cli.py`, `--help`, `check.py`, `probes.py` and every frozen file are unchanged (PB-F re-baselined: 0 changed).

How the decisions were realised:
* **D1** `import_rules`: for the `gin` front end and no `--app-factory`, `go_rules.harness_factory(root, package)` reads `studio.toml [app] app` of the
  module dir through `go_gin._harness_attr`; a Python front end never calls it (unit test, mutant JM11). The front end is wrapped by
  `InferringFrontEnd` so `lift_repo` (which extracts again) sees D2's keys without an edit to `lift.py`.
* **D2** `infer_owners(extract, model)`: the four rules of the pre-registration; "an integer kind matching the users table's key" is read as
  Lattice type `Int` on both (width and signedness are ignored). Two candidates give both columns the inferred key (the profile generator then
  reports two users keys: unclear) and one `inferences[]` entry with `unclear: true` and both names.
* **D3** `derive_go_policy` / `go_spec_policy` as specified. Wiring: `lattice check` keeps `GoPolicy.from_spec` (Q3: no).
* **D4** `{"policy": true}` on `gocheck extract`: `tables[].columns[].json` only; the default bytes are unchanged (PB-X, 7 of 7 dirs; unit test).
* **D5/D6** unchanged: GO805 stays gated (PG805), the harnesses pass `LOGIN`.
* S2 was built **before** any probe ran. The truth T-S2 was not edited. The build used `go mod tidy` (4 test-only `.mod` downloads, 6.8 s) and
  `go build -mod=readonly` (2.7 s warm, 15 s first). **Gin v1.10.0 was already in the module cache** (premise 6 of the pre-registration, "Gin is
  not in the module cache", is stale); the proxy was reached only for four test-dependency modules of the dependency graph. `go.mod` and `go.sum`
  are committed in the tree; the build never writes into the module (PW: 0 changed).

## 3. Deviations from the pre-registration (all of them)

1. **Amendment 1 says no build is started**; this node built (dispatch + user request "all of it").
2. **Base commit.** The pre-registration was written at `5125da1`; the worktree is `be26a94` (Spikes E, H, I merged). Of the 26 frozen-table rows,
   **8 hashes differ from section 7**: `check.py`, `process.py`, `implementation.py`, `__main__.py`, `emit_spec.py`, `workflows.py`,
   `frontends/__init__.py`, and the `gocheck/src/*.go` group (now **22** files, not 20: Spike I added `goextract_grpc.go` and `goextract_proto.go`).
   `goextract.go` moved too (`e97fa18e...` in the pre-registration; Spike I added 2 lines; this node then edited it), `goextract_tables.go` and `rules.py` are unchanged from the pre-registration's base until this node's edits. Recorded as a finding, not re-baselined silently: PB-F is reported twice
   (literal: FAIL; against `be26a94`: PASS, 0 files changed by this node). The 16-hex extract prefixes of section 4 are also stale: all 7 default
   extracts differ from them at `be26a94` (the default extract is byte-identical between `be26a94` and this tree, PB-X 7 of 7).
3. **Role name.** T-S2 says `Member`; the extractor names the authenticated role `Customer` (`goextract_assemble.go:505`). PI1 reads "Member" as
   "the one non-Public role the extractor names" (the evidence is in the row).
4. **S2's login.** To be a login the extractor recognises, `POST /login` calls `bcrypt.CompareHashAndPassword`; the Seeder writes plain passwords
   (Q2, no hasher for Go). `Login` therefore accepts a bcrypt hash or the equal plain value (`err != nil && u.Password != req.Password` refuses). The login answer also
   carries `"user": u` (the model, `Token`/`Password` tagged `json:"-"`), so that a users row reaches a response (T2, GO802). Neither is in T-S2's text.
5. **B on S1 passes `rules=RULES`** (`go_harness_rb.Any422`: a 422 is a 422). With the check's default rule B reports 6 findings on the safe app
   (F2); the CLI has no `--rules`. PB1 "0 findings" holds with that rule. The CLI form used for R and B is otherwise as written (`--login ...:LOGIN`).
6. **Readings fixed before the scored run** (in the runner, `reading` field of each row): PI6 compares `review list` ids with the report's
   not-verified ids exactly; PI7's normaliser replaces `cost.wall_s` and `rules.timing` and leaves `report.md` out (the pre-registration's "timing normaliser the
   Python rules goldens use" does not exist: those goldens drop the whole `rules` section); PR3 compares `report.json`'s final verdicts with layer R of the
   corrected `candidate_spec.py` literally; PA3 calls a mutant's finding *new* when its (code, message) is not among the original's.
7. **`spec_policy` does not exist** at this commit (PB-P's "`spec_policy` on the pycheck fixtures"; also my `derive.py` docstring wrongly names it as
   Python's counterpart). PB-P compares `derive_policy` of the multi-vendor app instead. The docstring is left as frozen (post hoc fix listed in section 9).
8. **PB-P's normaliser** replaces the target path, timestamps, bcrypt salts and JWTs (the Python rules report contains all four), and the base is imported
   twice as a control that the normaliser makes it self-identical (it does).
9. **A shakedown run preceded the scored run.** The first execution of the probe runner was a full run on the frozen library (data:
   `shakedown/probes.json`): 18 PASS / 8 FAIL. It exposed two plumbing bugs in PB-P only (item 8 and item 7); both were fixed in the runner, the
   runner was re-frozen, and the scored run followed. The other 7 failing rows failed identically in both runs. No library, fixture, harness or test file changed
   between the shakedown and the scored run (hashes in `freeze.json`). The mutants runner had a bug (the JM5/JM6 base verdict map taken with the
   mutant binary), found by reading before any mutant was judged, fixed and noted in `freeze.json`.
10. **PB-D** was run with the venv python instead of `uv run` (`check.sh` is `uv run python -m lattice.spec check ...`; `uv run` would try to sync the lock).
11. **PX's "with `go build`"**: the lazy build path of `gogin_harness.binary()` was warm (the unit tests had built the same source hash into the temp dir),
    so the 9.4 s import does not include a build. The build alone is `build_goginapp_s` (2.7 s warm, 15.0 s first time, shakedown). A cold measurement is in section 8.
12. **Exploratory runs before the freeze.** While building, I ran S1's R and B, and one S2 import (`imp0`: 38 items, 19 verified, 4 refuted), to get the fixtures
    running. I saw the S2 numbers before the freeze and did not change S2, the library or any pass rule afterwards (the freeze hashes bear this out).
13. **Docker, `claude`, a model, an API key, sub-agents**: none used.

## 4. Arm S, the gated rows

Sources: `data/.../scored/probes.json` (full evidence), `scored/run.log`. "(p)" are the blind predictions of the pre-registration.

| id | prediction (p) | observed | pass rule | verdict |
|---|---|---|---|---|
| PI1 | `gin`; 7 routes; 7 contracts (health, login Public); 3 tables | `gin`; 7 routes; 7 contracts; Public = `GET /health`, `POST /login`; the other five all `Customer`; tables `configs`, `runs`, `users` | exact vs T-S2 | **PASS** (role name: deviation 3) |
| PI2 | `runs: user_id`, 1 inference, 4 evidence lines (3 compare, 1 assign); configs shared; users not owned | exactly that: `code:api/api.go:125,151,165,182` = 1 assign + 3 compare; owners `{User: None, Config: None, Run: user_id}` | exact | **PASS** |
| PI3 | 28-48 items; verified share 0.65-0.90; refuted 0-3, each with a correction or baseline entry | **38 items** (in range); verified 19 = **0.50** (below the range and the gate); **4 refuted** (above 0-3): 3 are R3 `404 Not found` items answered `422 'bad id'` (F5), each with a correction, and **`R3 GET /runs 400`: no correction** (F6); 15 unexercised, all with a reason | share >= 0.60; every refuted item has a correction or baseline entry with its reason; every unexercised item a reason | **FAIL** |
| PI4 | every seed-verified item still verified on `variant2` | 22 of 22 (1.0); the import's own `variant2` record is not clean (`R3 GET /runs 400` refuted there too) | >= 0.95 | **PASS** |
| PI5 | 1-6 proposals including the machine `runs.status` queued -> cancelled | 3 proposals (in range), **all invariants** (`config_id > 0`, `name` unique, `token` unique), pending; **no machine** (F7) | the machine is proposed; each pending | **FAIL** |
| PI6 | exit 0; every rule item listed | exit 0; 19 listed, 19 not verified in the report; but 3 listed ids are the corrected `... 422 bad id #2` and 3 report ids (`... 404 Not found`) are not listed (F10) | ids equal | **FAIL** |
| PI7 | byte-identical after the normaliser | 10 of 10 identical (walls 9.3-11.4 s) | 10/10 | **PASS** |
| PA1 | tables 3; users_table `users`; owners `{Run: user_id}`; private `{User: [password, token]}`; admin []; machines {}; baseline 0-2 keys | all as predicted **except `users_table` = `User`** (the extract's struct name, which is what the checker compares, `owner.go:153`; D3 says "the extract's auth users table"); baseline 1 key `GO802|POST /login|User.token`, equal to a separate `run_gocheck` | exact except the baseline; baseline = separate run | **FAIL** (one string; F9) |
| PA2 | `Run` not owned; machine present; baseline empty | exactly that (the derived baseline had 1 key) | exact | **PASS** |
| PA3 | GO801 on cancel (JM5), GO802 on the users response (JM6), original 0 outside the baseline | original: 0 findings; **JM5: 0 new findings; JM6: 0 new findings** | exact codes and routes | **FAIL** (F3, F4) |
| PA4 | static: owners `{Item: owner_id, Audit: actor}`, private `{User: [password_hash]}` | exactly that | exact | **PASS** |
| PG805 | `gated_note()` and `GO_GATED` byte-identical; informational pairing count | identical to base. Pairing count (owner `compare` with a `raises[]` entry of the same route/file within 3 lines): goginapp **2 of 3**, goextract **1 of 3**. Never used by a check | identical | **PASS** |
| PR1 | 18-30 items; share 0.55-0.85; refuted 0 | **23 items, 16 verified (0.696), 7 unexercised (all with reasons), 0 refuted, 4 of 4 routes verified** | refuted 0; share >= 0.50; reasons | **PASS** |
| PR2 | JM1 and JM2 each refute >= 1 item on their route | JM1: `R1 GET /runs/{rid}` refuted; JM2: `R3 GET /runs/{rid} 404 Not found` refuted | both | **PASS** |
| PR3 | the same item verdicts as the import's seed run | 38 vs 38 items; **6 ids differ**: the three refuted `404 Not found` items of the import are absent from the corrected spec, and the three `422 bad id #2` items it adds are `verified` there | identical map | **FAIL** (deviation 6; a corrected spec cannot have the import's ids) |
| PR4 | identical verdicts and findings, 10x | 10 of 10 | 10/10 | **PASS** |
| PB1 | 6-30 cases; 0 findings | **32 cases** (outside the range), 0 findings (with `RULES`, deviation 5) | 0 findings; >= 6 cases | **PASS** |
| PB2 | `nocheck` / `nocancel`: a finding on the named endpoint | `nocheck`: 2 findings on `POST /configs/{cid}/runs` (cid=2, as ann and ben); `nocancel`: 2 on `POST /runs/{rid}/cancel` | both | **PASS** |
| PB3 | identical finding sets, 10x | 20 of 20 (safe and `nocheck`) | 10/10 | **PASS** |
| PW | 0 changed, 0 shim calls | 0 changed under `goapp`, `goginapp`, `tests/gocheck/fixtures`, `tests/spec/fixtures/implementation`; 0 `docker`/`claude` calls | all 0 | **PASS** |
| PX | S2 import 15-90 s; R on S1 2-15 s; B on S1 3-20 s | S2 import **9.4 s** (below the range; no build in it, deviation 11), prebuilt mean 10.4 s; R **3.6 s**; B **3.2 s** | <= 180; <= 30; <= 60 | **PASS** |
| PB-F | all 26 frozen hashes as listed | **8 differ** from the pre-registration (deviation 2); **0 differ from `be26a94`** | all | **FAIL** literal; PASS re-baselined |
| PB-X | the default extract byte-identical on the 7 dirs | 7 of 7 identical to the base binary built from `be26a94`'s sources; 0 of 7 equal the pre-registration's 16-hex prefixes (stale) | identical | **PASS** |
| PB-G | workflows static, inventory, layer A findings/notes identical | all identical (workflows.json; extract.json + inventory.json; the implementation fixture's findings and note) | all | **PASS** |
| PB-P | Python rules import, `inferences` absent, policy identical | identical to base after the normaliser; the base twice is self-identical (control); no `inferences`, no `go_policy.json`; `derive_policy` identical | all | **PASS** |
| PB-H | `--help` x3 and MCP `tools/list` identical | all 4 identical | all | **PASS** |

## 5. Predictions missed, plainly

* PI3: verified share 0.50 (p 0.65-0.90) and 4 refuted (p 0-3). The cause is not the Go app (every refuted R3 item but one is the extractor typing the path id `Str`, F5).
* PI5: no machine proposed (p yes). The Seeder gives the `status` column the text `Status 01`..`Status 08` (the extract records `default: "queued"` and the Seeder ignores it),
  so no probe ever cancels a queued run and mining never sees the transition (F7).
* PI6, PR3: the corrected items change ids; the pre-registration's pass rules did not account for corrections.
* PA1: `users_table` (a string); PA3: both edits unseen by layer A (F3, F4), the one probe the pre-registration most expected to show rank 3 working.
* PB1: 32 cases (p 6-30); PX: the S2 import 9.4 s (p 15-90). PR1 and PB2 landed inside their predictions.
* Mutants: JM5 and JM6 (see section 7).

## 6. Findings

Each with the evidence and a one-line proposal for a later node. None was fixed here.

* **F1 (F-LOGIN, premise 3 confirmed).** `check` ignores `runtime.auth.login` for R and B when `login=None` (`check.py` "layer B: no login given", `probes.py`
  "layer R: no login given"). On S1 without `login`: R verified 3, unexercised 20 (with it: 16 and 7) and B silently passes with no callers. The harnesses pass `LOGIN`
  (unit test `test_layer_r_needs_an_explicit_login_finding_f_login`). Proposal: `login = login or runtime.auth.login` in `_layer_r` and `_layer_b`, as D does with `runtime.auth.token`.
* **F2 (F-B422).** The default `ValidationEnvelope` compares 422 bodies as `{"detail": [{"loc"...}]}`; the Go app answers `{"detail": "bad id"}`. B reports 6 findings on the
  safe app (3 endpoints x 2 callers, case `malformed`; unit test). The CLI has no `--rules`. Proposal: `--rules module:RULES` in `__main__`, or a Go default.
* **F3 (F-RECOVERY).** GO801 is silent on S2 because `main.go` has `r.Use(gin.Recovery())`: an outside-package middleware makes the route's middleware unreadable and gocheck prefers silence
  (`owner.go` `checkOwnership`: `mwComplete`). Post hoc (PH1/PH2, a scratch copy without that one line): the original is still 0 outside the baseline and JM5 gives
  `GO801|POST /runs/:rid/cancel|Run`. `gin.Default()` installs Recovery, so the same silence is likely on most real Gin apps (to check on Streak, arm M). Proposal: treat
  `gin.Recovery`, `gin.Logger` and similar library middleware as readable and owner-neutral.
* **F4 (F-PRIVATE-SPEC).** The imported spec declares no private column for `User` (the profile generator derives `private` from response models, and a Go extract has none),
  so `go_spec_policy` (the spec wins for a declared table) drops the derived `password`/`token`; there is also no T2 item in layer R. Post hoc (PH3, PH4): `JM6` is invisible under the
  imported spec's policy **and under the derived one**. With the derived policy and an *empty* baseline the original already has `GO802|POST /login|User.token` (the login answer's
  `access_token: u.Token` reads a private column from a row), JM6 gives the same finding, and a JM6 whose answer is the struct itself gives the same **key** with a different message: a finding is keyed by route and column, so
  a key the baseline accepts hides the mutation, and a struct held inside `gin.H` adds nothing the checker reads. The login answer's own token (T-S2: login returns it) is what makes JM6 unobservable here. Proposal: feed the policy extract's `json:"-"` columns into the generated Go profile's `private`; key GO802 by the leaking expression too.
* **F5 (F-PATH-TYPE).** The extractor types `c.Param(...)` path ids as `Str` (a `strconv.ParseUint` of it is not followed): the not_found probe sends a non-integer id, the app answers
  `422 'bad id'`, and 3 R3 `404 Not found` items are refuted. Their corrections restate the 422 and stay `refuted` after the re-probe (no example triggers them: `unreachable-by-probe`).
* **F6 (F-INVALID-400).** The candidate spec adds `ContractError(400, when='invalid')` for the bounded `limit` beside the extracted `422 'bad limit'`. The probe sends `limit=0`, which
  `binding:"omitempty,min=1,max=50"` lets through as unset (200): `R3 GET /runs 400` is refuted with no correction, and the import's `variant2` check is not clean because of it.
* **F7 (F-SEED).** The Seeder fills a `str` column with `Status 01`... and ignores the extract's `default`. On S2 that makes every seeded run non-queued: cancel always answers 409, the
  conflict and cancel success paths are unreachable (`R3 ... 409`, `R2 POST /configs/{cid}/runs` unexercised), and mining sees no transition. The seeded user token is `Token 01` (login returns it).
* **F8 (F-ROLE).** The authenticated role is `Customer` in every Go import (deviation 3).
* **F9 (F-PREREG).** Stale or inconsistent in the pre-registration: 8 frozen hashes, 7 extract prefixes, premise 6 (Gin was cached), `spec_policy`, the `users_table` of PA1 against D3, and Amendment 1's last sentence.
* **F10 (F-REVIEW).** `review list` shows a corrected item as `<id> #2` and not the refuted original, so a rule-item id listed is not always a report id.
* **F11.** The lazy-build path of `gogin_harness.binary()` writes only under the system temp dir (PW: 0 changed under `tests/spec/goginapp` across the whole run, the cold builds included).
* **F12 (GO805, informational).** Pairing an owner `compare` with a raise within 3 lines in the same file and route: goginapp 2 of 3, goextract 1 of 3. A third compare has no raise nearby;
  the pairing would be a guess in a third of the cases. GO805 stays gated, text unchanged (PG805).

## 7. Mutants

The control (no change; every detector, on an unmutated copy of the tree, 146.9 s) passed first: 0 unit tests failing, every probe the mutants use
returned its scored value. A mutant is **killed when a detector that passes (or fails) on the control flips**; for JM5/JM6 the rule written
into the runner before it ran was "PA3 finds the named code" (R is reported beside it). Data: `data/.../mutants/mutants.json`.

| id | mutant | detector(s) named | observed | killed |
|---|---|---|---|---|
| JM1 | goapp copy: `getRun` skips `caller()` | PR2 | R refutes `R1 GET /runs/{rid}` | yes (probe) |
| JM2 | goapp copy: `getRun` answers 403 for a missing run | PR2 | R refutes `R3 GET /runs/{rid} 404 Not found` | yes (probe) |
| JM3 | goapp `BUG=nocheck` | PB2 | B: 2 findings on `POST /configs/{cid}/runs` | yes (probe) |
| JM4 | goapp `BUG=nocancel` | PB2 | B: 2 findings on `POST /runs/{rid}/cancel` | yes (probe) |
| JM5 | goginapp copy: cancel drops `AND user_id = ?` | PA3 (GO801); R owner-scope item refuted | PA3: **0 new findings** (F3: `gin.Recovery()` silences GO801); R: `R2 POST /runs/{rid}/cancel` goes `verified` -> `unexercised`, **not refuted** (the other user's seeded run is not queued, so the unscoped cancel answers 409, not a usable 200/404) | **NO** |
| JM6 | goginapp copy: `token` loses `json:"-"` | PA3 (GO802); R private item refuted | PA3: **0 new findings** (F4: the imported spec declares no private column; with the derived policy the original's own GO802 on the same route and column has the same key); R: no verdict changes (there is no T2 item) | **NO** |
| JM7 | D2 requires both compare and assign (Audit loses its owner) | PA4; unit `assign_only_owner` | PA4 flips, the unit test flips | yes (probe + unit) |
| JM8 | D2 takes the first of two candidates | unit `two_candidates_unclear` | the unit test flips | yes (unit only) |
| JM9 | `go_spec_policy` keeps the derived baseline | PA2; unit | PA2 flips, the unit test flips | yes (probe + unit) |
| JM10 | the derived owner wins over the spec's declared table | PA2 | PA2 flips | yes (probe) |
| JM11 | `harness_factory` used for Python front ends too | PB-P; unit | **PB-P does not flip** (the multi-vendor package has no `studio.toml`, so the call returns None); the unit test flips | yes (unit only; the named probe misses it) |
| JM12 | private columns from names, not `json:"-"` | PA4; unit | PA4 flips, the unit test flips | yes (probe + unit) |
| JM13 | the `json` key leaks into the default extract | PB-X | PB-X flips (7 dirs differ), the unit test flips | yes (probe + unit) |
| JM14 | GO805 ungated | PG805; PB-G | both flip | yes (probe) |
| JM15 | the import writes `candidate_spec.py` into the target dir | PW | PW flips (the snapshot of `tests/spec/goginapp` changes) | yes (probe) |
| JM16 | `inferences` written as `[]` for every import | PB-P | PB-P flips, the unit test flips | yes (probe + unit) |

**14 of 16 killed.** The prediction was 16 of 16. JM5 and JM6 are the two mutants layer A should see, and are the two the pre-registration most wanted
(rank 3). Two were killed only by a unit test (JM8, JM11), and for JM11 the pre-registration's named probe (PB-P) does not see it. JM5's R side moved an item to
`unexercised`, which is a signal but not the refutation the pre-registration asked for.


## 8. Regression, ruff, timing

**pytest** (`tests/importer tests/pycheck tests/gocheck tests/spec tests/mcp tests/studio -q -rsf --continue-on-collection-errors --maxfail=1000`), before in a
`git archive be26a94` copy, after in the worktree (`data/.../pytest/`):

| | failed | passed | skipped | errors | wall |
|---|---|---|---|---|---|
| before (`be26a94`) | 7 | 2868 | 37 | 101 | 1019 s |
| after | 7 | **2909** (+41) | 37 | 101 | 1007 s |

The same 7 failing ids (`test_import_token_answers.py::...[in_dependency|in_helper]`, `test_mine.py::test_multivendor_recall_and_precision`, 4 in `test_policy.py`), the same
101 ERROR headers (15 collection errors of the `bench/template_app` SQLModel `NameError: Item` and 86 setup errors) and the same 23 skip reasons; a plain run without
`--continue-on-collection-errors` stops at those 15 collection errors in both trees. The failures are the environment's (this container's SQLModel), not this node's.

**ruff** on every touched or created `.py` (`go_rules.py`, `derive.py`, `rules.py`, the three test files, both harnesses, the three dogfood scripts): `ruff check` All checks passed
(`rules.py` was clean before too); `ruff format --check` on the 10 created files: already formatted. `gofmt -l tests/spec/goginapp lattice/gocheck/src`: empty. `go vet` of
`lattice/gocheck/src`: clean. (`# noqa: E501` marks are on the long literal lines of the dogfood scripts, as other dogfood scripts do.)

**Timing** (scored run, idle machine except where noted): S2 import 9.4 s (binary pre-built), R on S1 3.6 s, B on S1 3.2 s; 10 imports 9.3-11.4 s each. Supplementary cold
measurements of the S2 import (not rows): the harness binary absent, Go build cache warm **11.2 s**; Go build cache empty (fresh `GOCACHE`) **39.4 s** (the pre-registered 15-90 s
holds for a cold build). Whole probe run 5 m 53 s; mutants run 7 m 50 s (`data/.../mutants/run.log`).

**Arm M and Docker**: NOT RUN (no Streak, no Docker; `docker`/`claude` shim calls: 0).


## 9. Freeze, files, post hoc

Hashes (`data/.../freeze.json`; sha256 at 2026-10-18 13:50 UTC, **before the first execution of the probe runner**):

| file | sha256 (first 16) |
|---|---|
| `lattice/importer/go_rules.py` | `71839e6b6850eea3` |
| `lattice/gocheck/derive.py` | `e19fde637ae85ccd` |
| `lattice/importer/rules.py` | `0f62f835b2edb1cd` |
| `lattice/gocheck/src/goextract.go` | `598bd04a5224294d` |
| `lattice/gocheck/src/goextract_tables.go` | `9317c70bf200eb5b` |
| `dogfood/go_parity_probes.py` | first `cb4cdaac20fb51226` ; **re-frozen after the shakedown** `a9fe24c9c8a6ed60` (the scored run used this one) |
| `dogfood/go_parity_mutants.py` | first `7f3e45b4cc1a702b` ; before any mutant: `f9f73a3cb760a6e3` |
| `tests/spec/goginapp/*` (6 files), `gogin_harness.py`, `go_harness_rb.py`, the 3 test files | in `freeze.json`; unchanged to the end |

Checked at the end of the node: every library, fixture, harness and test hash equals the freeze; only the two runners differ, by the two recorded edits.

**Files created** (no file was `git add`ed): `lattice/importer/go_rules.py`, `lattice/gocheck/derive.py`, `tests/spec/goginapp/{main.go,api/api.go,models/models.go,go.mod,go.sum,studio.toml}`,
`tests/spec/gogin_harness.py`, `tests/spec/go_harness_rb.py`, `tests/importer/test_go_rules.py`, `tests/gocheck/test_go_derive.py`, `tests/spec/test_go_layers_rb.py`,
`dogfood/go_parity_probes.py`, `dogfood/go_parity_mutants.py`, `dogfood/go_parity_posthoc.py`, this file, `docs/results/data/2026-10-18-go-parity-gaps/**`.
**Files changed:** `lattice/importer/rules.py`, `lattice/gocheck/src/goextract.go`, `lattice/gocheck/src/goextract_tables.go` (additive).

**Post hoc, not gated, not fixed** (`dogfood/go_parity_posthoc.py`, `data/.../posthoc/posthoc.json`, run after the scored run on scratch copies): PH1 JM5 with `r.Use(gin.Recovery())`: 0 new findings; PH2 the same
copy without that one line: the original has 0 findings outside the baseline and JM5 gives `GO801|POST /runs/:rid/cancel|Run`; PH3 JM6: the spec's private columns for `User` are empty (derived: `password`,
`token`), and JM6 gives 0 new findings under the spec's policy and under the derived one; PH4: with the derived policy and no baseline the original, JM6 and a struct-answer JM6 all give the one key `GO802|POST /login|User.token`. The fixture, the library and the pass rules stay as frozen;
the fixes (F3, F4, F7, the stale pre-registration lines, `derive.py`'s wrong `spec_policy` docstring) belong to a later, separate post-hoc commit with its deltas reported.

**To reproduce** (from the worktree, `PY=/home/user/lattice/.venv/bin/python`, `export PYTHONDONTWRITEBYTECODE=1 GOTOOLCHAIN=auto`):
```sh
$PY dogfood/go_parity_probes.py --arm S --scratch "$SCRATCH/p" --out "$SCRATCH/p-data" --base-ref HEAD --runs 10
$PY dogfood/go_parity_mutants.py --scratch "$SCRATCH/m" --out "$SCRATCH/m-data" --base-ref HEAD
$PY dogfood/go_parity_posthoc.py --scratch "$SCRATCH/ph" --out "$SCRATCH/ph-data" --import-dir "$SCRATCH/p/imp/r0"
$PY -m pytest tests/importer/test_go_rules.py tests/gocheck/test_go_derive.py tests/spec/test_go_layers_rb.py -q
$PY -m lattice.importer tests/spec --package goginapp --rules-only --out "$SCRATCH/imp"
```

