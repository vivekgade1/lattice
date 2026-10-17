# Go-behind-Python gaps, ranks 2 to 5 of J0 (Spike J, node J1): pre-registration

## 1. Status, and the exact gap

2026-10-17, from `next-phases` 5125da1 (J0 matrix `docs/results/2026-10-17-parity-matrix-j0.md`, plan
`docs/design/lattice-implemented-logic-spikes-plan.md`, Addendum "Spike J"). **Written before any code.** Scope: the J0
gaps ranked 2 to 5, closed for Go before .NET: **(2)** rules-first import and candidate-spec emission for a Go app
through the harness app factory; **(3)** layer A policy derivation from the extract, including the GO805 decision;
**(4)** layer R on Go over SQLite; **(5)** layer B on Go over SQLite. Out of scope: rank 1 (Go lifting), rank 6 (perf),
the server-dialect B/R skip (`check.py:69-71`, it affects every stack), J0 section 4 (a Python inventory), and .NET.
Recon in this container (Linux, go 1.24.7 with the cached 1.25.0 toolchain, no Streak checkout): code reading, the
existing tests, and four throwaway scripts in the scratchpad. No model. **(m)** = measured, **(r)** = recon script (a
blind expectation), all else is a **prediction (p)**. Results go to `2026-10-17-go-parity-gaps-results.md`, with data in
`docs/results/data/2026-10-17-go-parity-gaps/`.

**What the code says (m/r). Six premises.**
1. **`tests/spec/goapp` cannot be imported.** It is plain `net/http` with raw SQL, not Gin + GORM. The Gin front end
   extracts **0 routes and 0 tables** from it (m), and `detect` is False (no gin in `go.mod`). So goapp serves ranks 4
   and 5 (runtime only). Ranks 2 and 3 need a Gin + GORM app that runs on SQLite. No such fixture exists. All five Gin
   fixtures (`tests/gocheck/fixtures/{goextract,app,partial,goextract_workflow}`, `tests/spec/fixtures/implementation/app`)
   have no `go.sum` and are only parsed, never built.
2. **The Python path almost works on Go.** `lift_repo(root, "goextract", f, only=(), frontend=GoGinFrontEnd())` runs on
   the goextract fixture (r): 9 routes, 4 tables, and `candidate_spec` gives 9 contracts, 0 programs, 0 scenarios. Two
   things block it. First, `find_factory` imports a Python `make_app` (`rules.py:141-163`). Second, the profile's
   `ownership` is **`{}`**, although the extract has `owner_uses`: `Item.owner_id` is compared on 3 routes and assigned on
   1, and `Audit.actor` is assigned on 1 (r). The cause is that GORM has no association tag, so `profile_gen` sees no
   users foreign key.
3. **Layer R and layer B need an explicit `login` on a process runtime.** With `runtime=go_harness.RUNTIME` and no
   `login`, R notes "no login given" and B does the same (`check.py:1132`). D, by contrast, uses `runtime.auth.token`
   (`check.py:1677`). With `login=RUNTIME.auth.login` and three hand-written contracts on goapp, R gives (r): 16 items,
   **0 verified, 1 refuted, 15 unexercised**. The refuted item is "no caller of ['Member'] could log in": go_harness's
   profile takes roles from the `name` column. The same counts hold for `GOAPP_BUG=nocheck` and `nocancel`. So R on Go
   needs a harness whose seed and profile give real Member callers. It does not need a core change. **`check.py` and
   `probes.py` stay frozen.**
4. **Layer A on Go takes its policy from the spec only** (`GoPolicy.from_spec`, `gocheck/__init__.py:260`, used by
   `check.py:_layer_a_go`). There is no derivation and no baseline. The default extract carries neither the struct
   fields' `json` names nor `json:"-"`. `private.go` reads them inside gocheck but does not export them. So private
   columns cannot be derived from today's extract bytes.
5. **GO805 cannot be derived without guessing.** PY805 compares the status raised by an owner-check `if` with the app's
   answer to another user's row (`derive_not_owned`). The extract has `raises[]` (status, code, file, line, kind, in) and
   `owner_uses[]` (how, file, line) per route, but **no link from a guard to the raise it guards** (m,
   `goextract_closure.go:18-51`). Pairing them by line proximity would be a guess.
6. **A runnable Gin + GORM + SQLite app is feasible but needs one module download.** Gin is not in the module cache. gorm
   v1.25.12, glebarez/sqlite v1.11.0 (pure Go) and modernc are cached. `proxy.golang.org` answers 200, and a scratch
   Gin + GORM + glebarez/sqlite program downloaded and built in 17.7 s (m). Docker is installed but **not used by this
   node**. Baseline: `tests/spec/test_process_runtime.py`, `tests/importer/test_go_gin.py` and
   `tests/gocheck/test_gocheck_gates.py` give 34 passed in 22.8 s (m).

## 2. Arms

**Arm S, the primary gate (Docker-free, SQLite, this container).**
- **S1: `tests/spec/goapp`, frozen bytes.** A new harness, `tests/spec/go_harness_rb.py`, imports `go_harness` and adds
  three things: a role-aware seed (`build_db(variant, path)` with Member users `ann` and `ben`, and `variant2` with other
  ids and rows); contracts for the four non-health routes; and Lattice programs for `POST /configs/{cid}/runs`, `POST
  /runs/{rid}/cancel` and `GET /runs/{rid}`. It exports `LOGIN = RUNTIME.auth.login`. This arm gates ranks 4 and 5.
- **S2: `tests/spec/goginapp`, new.** The goapp service rewritten in Gin + GORM over glebarez/sqlite, plus ownership and
  private columns. Its truth is fixed now, in **T-S2** below. A `studio.toml [app]` (`app`, `db`, `runtime`) points at
  `tests/spec/gogin_harness.py`, with a committed `go.mod` and `go.sum`. This arm gates ranks 2 and 3, and runs R and B
  again on the imported spec.
- If `go build` of S2 fails (no download), every S2 row is **NOT RUN** with the error. The gate is then **incomplete**:
  it does not pass, and it is not skipped silently. Static rows (PA4, PB extract) still run, because goextract only
  parses.

**T-S2, the ground truth of goginapp (fixed before the build).**

| element | truth |
|---|---|
| routes (7 registrations) | `GET /health` Public; `POST /login` Public; `POST /configs/:cid/runs` Member; `GET /runs` Member; `GET /runs/:rid` Member; `POST /runs/:rid/cancel` Member; `GET /configs` Member |
| tables (3) | `users` (id pk, name unique, password `json:"-"`, token unique `json:"-"`); `configs` (id, name); `runs` (id, config_id, user_id, status default `queued`) |
| owners | `runs.user_id`: assigned from the caller on create, compared in `Where("id = ? AND user_id = ?")` on get, cancel and list; `configs` shared |
| private | `users: [password, token]` |
| refusals | 401 no or bad token; 404 missing config, or another user's run; 409 a second live run per config, or cancelling a non-queued run; 422 `limit` out of 1..50 (`binding:"omitempty,min=1,max=50"`) |
| machine | `runs.status`: queued -> cancelled |

The auth middleware uses the idiom `tests/gocheck/fixtures/goextract/api/api.go` uses (read-only template), not
`context.WithValue`, which reads as Public (J0 row 1). Passwords are stored the way the import's Seeder writes them. The
build reads `adapt`'s insert hook for the Go model in step 0 and records it; this is not tuned after scoring.

**Arm M (Streak a0679e9 on MySQL 8.4): NOT RUN here.** There is no Streak checkout and no Docker in this node. Rows kept
for the orchestrator's machine: M1 the rules-first import of Streak (R is skipped on MySQL, so the import reports
`layer R: not_run:server_dialect`); M2 the derived GoPolicy versus `from_spec` on Streak's spec (predicted equal, since
the spec declares its 8 models); M3 Streak `lattice check --layers ACD` byte-identical to base. None of these is gated by
this node.

## 3. Decisions (fixed before coding)

**D1 Rules-first import for Go.** `python -m lattice.importer ROOT --package P --rules-only [--framework gin]` keeps the
same CLI: `cli.py` is unchanged and so is `--help`. `import_rules` gets one additive branch. When the selected front end
is `gin` and no `--app-factory` is given, the factory and its name come from `go_rules.harness_factory(root, package)`,
which reads the `studio.toml [app] app` of the module dir through `go_gin._harness_attr` (read-only reuse). The runtime
is the front end's (the harness `RUNTIME`, else the process runtime with the extracted login). The seed is the Seeder's
own, never the harness `db`. The schema comes from the Seeder's table DDL, as for Python. **Ruling Q2** covers this. A
Python front end goes through `find_factory` exactly as today. The outputs are what `write_rules` writes:
`report.json`/`report.md`, `profile.py`, `candidate_spec.py` and `proposals.json`. A Go import adds `go_policy.json`
(D3) and, in `report.json`, an `inferences[]` key **present only when non-empty and only for Go**. Owner review
(`python -m lattice.importer review DIR`) reads the directory unchanged.

**D2 Owner inference from `owner_uses` (Go only, in memory).** `go_rules.infer_owners(extract, model)` runs before
`generate_profile`. The goextract bytes, the inventory and `workflows.json` are untouched. The rule is Python's ("the one
users key that routes check or set from the caller"), with the foreign key replaced by evidence. Column `c` of table `T`
is `T`'s owner when all four hold:
1. some route's `owner_uses` has `(T, c, compare)` or `(T, c, assign)`;
2. `c`'s Go type is an integer kind matching the users table's primary key;
3. `T` is not the users table;
4. no other column of `T` meets 1 and 2.

Two or more candidate columns make `T` `unclear`, with both columns named. The model copy gives `c` an **inferred**
users foreign key (`users.<pk>`), recorded as `{"table", "column", "evidence": ["code:<file>:<line>", ...], "rule":
"owner_uses"}` in `inferences[]`. A table with no evidence keeps today's answer.

**D3 Layer A policy derivation.** A new `lattice/gocheck/derive.py` provides two functions.
- `derive_go_policy(module_dir, extract=None, ownership=None) -> GoPolicy` builds the policy:
  - `tables` and their SQL names come from the extract.
  - `users_table` is the extract's auth users table.
  - `owners` and `owner_via` are D2's inference, or the `ownership` given.
  - `private` holds the columns whose `json` is `"-"`, read from the opt-in policy extract (D4). They are never guessed
    from names.
  - `admin_routes` are the routes the extract marks Admin.
  - `machines` is **empty**: the original's own writes are not a spec, and the dropped machine is listed.
  - `baseline` holds the keys of `run_gocheck(original, policy)`.
- `go_spec_policy(spec, module_dir, policy=None)` is the counterpart of `spec_policy`. It derives the policy, empties the
  baseline, and lays the spec over it for the tables the spec declares (the spec's owners, `owner_via`, private columns
  and machines win). It also adds `GoPolicy.from_spec`'s admin and open routes.
- **Wiring:** `check.py` is frozen. So `lattice check` on Go keeps `GoPolicy.from_spec` by default, which makes it
  byte-identical by construction. The derived policy is used by the import (`go_policy.json`) and by any caller that
  passes `policy=go_spec_policy(...)`. Making it the default is **Ruling Q3**.

**D4 Opt-in policy extract.** The `gocheck extract` input gains `{"policy": true}`, next to `{"workflow": true}`. It
adds `tables[].columns[].json` (the encoding/json name, `"-"`, or null) and changes nothing else. The default request's
output is byte-identical (PB-X). Only `goextract.go` (the request struct and the switch) and `goextract_tables.go`
change.

**D5 GO805 stays gated, and its text stays byte-identical** (premise 5). Deriving it needs a guard-to-raise link in the
extract (`owner_uses[].raise`), which a later node can add to the D4 opt-in extract. The results report one
informational count, never used by a check: on S2 and the goextract fixture, the number of owner `compare` uses that
have a `raises[]` entry in the same function within 3 lines. **Ruling Q4.**

**D6 Layer R and layer B on Go over SQLite need no core edit.** The harnesses supply `login` (`--login
tests.spec.go_harness_rb:LOGIN` on the CLI). The gap (premise 3: B and R ignore `runtime.auth` when `login` is None) is
recorded as a finding with a one-line proposed fix for a later node. **Ruling Q5.** B on S1 uses hand-written programs.
B on imported Go specs stays empty until rank 1, and the results say so.

**D7 Never.** This node never writes into a fixture or target directory during a run (the import `--out` goes to
scratch), never calls a model or `claude`, never runs Docker (a recording `docker` shim is first on `PATH`), never edits
a frozen file (section 7), and never commits or `git add`s. The orchestrator stages explicit paths.

## 4. Probes (runner `dogfood/go_parity_probes.py`; blind pass rules; arm S)

| id | probe | prediction | pass |
|---|---|---|---|
| PI1 | rules-first import of S2: framework, routes, contracts, tables | `gin`; 7 routes; 7 contracts (health and login Public); 3 tables (p) | exact vs T-S2 |
| PI2 | owners and `inferences[]` of the S2 import | `runs: user_id`, 1 inference, 4 evidence lines (3 compare, 1 assign) (p); configs shared; users not owned | exact |
| PI3 | rule items of the S2 import on the original (seed) | 28-48 items (p); verified share 0.65-0.90 (p); refuted 0-3, each with a correction or a baseline entry | verified share >= **0.60**; every refuted item has a correction or a baseline entry with its reason; every unexercised item has a reason |
| PI4 | the corrected spec re-probed on `variant2` | every seed-verified item is still verified (p) | >= **0.95** of the seed-verified items |
| PI5 | `proposals.json` (mining) | 1-6 proposals, including the machine `runs.status` queued -> cancelled (p) | the machine is proposed; each proposal is pending |
| PI6 | owner review: `python -m lattice.importer review <dir>` | exit 0; every rule item listed | exit 0; item ids equal `report.json`'s |
| PI7 | the S2 import 10x into separate scratch dirs | byte-identical after the timing normaliser the Python rules goldens use (p) | 10/10 |
| PA1 | `derive_go_policy` on S2 | tables 3; users_table `users`; owners `{Run: user_id}`; private `{User: [password, token]}`; admin_routes []; machines {}; baseline 0-2 keys (p) | exact for everything except the baseline; the baseline equals the keys of a separate `run_gocheck` call |
| PA2 | `go_spec_policy` with a spec that declares `Run` with no owner and declares the machine | `Run` not owned; machine present; baseline empty | exact |
| PA3 | edits on a scratch copy of S2 (JM5, JM6) under `go_spec_policy` of S2's candidate spec | GO801 on cancel (JM5); GO802 on the users response (JM6); original: 0 findings outside the baseline | exact codes and routes; original 0 |
| PA4 | static only: `derive_go_policy` on `tests/gocheck/fixtures/goextract` | owners `{Item: owner_id, Audit: actor}` (r: evidence); private `{User: [password_hash]}` (p) | exact |
| PG805 | GO805 gated: `gated_note()` and `GO_GATED` | byte-identical; informational pairing count reported | identical |
| PR1 | layer R on S1 (`go_harness_rb`, the safe app) | 18-30 items (p); verified share 0.55-0.85; refuted 0 (p) | refuted **0**; verified share >= **0.50**; unexercised items have reasons |
| PR2 | layer R on S1, app mutants JM1 and JM2 | each refutes >= 1 item on its route (p) | both killed |
| PR3 | layer R on S2 with the imported `candidate_spec.py` | the same item verdicts as the import's seed run (p) | identical verdict map |
| PR4 | layer R on S1, 10x | identical item verdicts and findings | 10/10 |
| PB1 | layer B on S1, safe app | 6-30 cases (p); 0 findings | **0** findings; >= 6 cases |
| PB2 | layer B on S1, `GOAPP_BUG=nocheck` / `nocancel` (the existing variants) | >= 1 B finding on `POST /configs/{cid}/runs` / on `POST /runs/{rid}/cancel` (p) | both, on the named endpoint |
| PB3 | layer B on S1, 10x | identical finding sets | 10/10 |
| PW | snapshot (sha256, mode, mtime, listing with hidden files) of `tests/spec/goapp`, `tests/spec/goginapp` and the fixtures around every run; recording `docker` and `claude` shims first on `PATH` | 0 changed; 0 shim calls | all 0 |
| PX | wall time: S2 import (with `go build`); R on S1; B on S1 | 15-90 s; 2-15 s; 3-20 s (p) | <= 180 s; <= 30 s; <= 60 s |
| PB | byte compatibility (below) | all identical | all |

**PB checks, against `git archive 5125da1` in the same directory layout:**
- **PB-F:** every file in section 7's frozen table has its listed sha256.
- **PB-X:** the default `gocheck extract` JSON is byte-identical on the 7 module dirs: `tests/gocheck/fixtures/{goextract,
  app, partial, goextract_workflow}`, `tests/spec/fixtures/implementation/app`, `lattice/studio/mock/appmap/app` and
  `tests/spec/goapp`. In this checkout their 16-hex sha256 prefixes are `00f49cb06e6b3c33`, `a8841a6f3fb1b58b`,
  `b02496cd2c3a5e19`, `81532e404272d09e`, `d30a96cc735d8df5`, `d30a96cc735d8df5` and `e95d65e81ca79469` (m). The
  runner recomputes the full hashes at base.
- **PB-G:** these Go outputs are byte-identical: the `workflows` static run and `inventory` on `goextract_workflow`; the
  layer A findings and notes of `python -m lattice.spec check` on `tests/spec/fixtures/implementation/app` with its spec;
  and `test_process_runtime.py`'s reports.
- **PB-P:** these Python outputs are byte-identical: the rules-first import of the Python rules fixtures used by
  `tests/importer/test_rules.py` (normalised as those tests do); `report.json` has no `inferences` key; and
  `spec_policy` on the pycheck fixtures.
- **PB-H:** these are identical: `lattice --help`, `python -m lattice.importer --help`, `python -m lattice.spec check
  --help`, and MCP `tools/list`.
- **PB-T:** `pytest tests/importer tests/pycheck tests/gocheck tests/spec tests/mcp tests/studio -q -rs` shows the same
  failing and skipped ids as base, plus the new tests passing.
- **PB-D:** `dogfood/studio/check.sh` passes with the same step count. If its environment is absent in this container,
  that row is NOT RUN and stated.

**Why these thresholds.** R's verified share is bounded by what probes can reach on a three-route toy. Items such as
"no route creates User rows" stay unexercised by construction, hence 0.50 on S1 and 0.60 on S2, which has more routes.
Refuted 0 on S1 holds because the harness spec is written to be true of the safe app; a refutation there is a harness
bug or a probe bug, and either is a finding. PI4's 0.95 allows one seed-dependent item.

## 5. Mutants (`dogfood/go_parity_mutants.py`, on scratch copies; the control passes first; predicted 16/16)

| id | mutant | killed by |
|---|---|---|
| JM1 | goapp copy: `getRun` skips `caller()` | PR2 (auth item refuted) |
| JM2 | goapp copy: `getRun` answers 403 for a missing run | PR2 (404 `not_found` item refuted) |
| JM3 | goapp `BUG=nocheck` (existing variant) | PB2 |
| JM4 | goapp `BUG=nocancel` (existing variant) | PB2 |
| JM5 | goginapp copy: cancel drops `AND user_id = ?` | PA3 (GO801); R owner-scope item refuted on the copy |
| JM6 | goginapp copy: `token` loses `json:"-"` | PA3 (GO802); R private item refuted |
| JM7 | D2 requires both compare and assign (Audit loses its owner) | PA4; unit `assign_only_owner` |
| JM8 | D2 picks the first of two candidate columns instead of `unclear` | unit `two_candidates_unclear` |
| JM9 | `go_spec_policy` keeps the derived baseline | PA2 (baseline must be empty); unit |
| JM10 | the derived owner wins over the spec's declared table | PA2 |
| JM11 | `harness_factory` used for Python front ends too | PB-P; unit (FastAPI fixture without `studio.toml`) |
| JM12 | private columns from name heuristics (`password`, `token`) instead of `json` | unit fixture `json:"password"` stays public; PA4 |
| JM13 | the `json` key leaks into the default extract | PB-X |
| JM14 | GO805 ungated by line-proximity pairing | PG805; PB-G |
| JM15 | the import writes `candidate_spec.py` into the target dir | PW |
| JM16 | `inferences` written as `[]` for every import | PB-P |

## 6. Risks, and what this cannot show

1. **The fixture is written by this spike.** S2's idioms are chosen to be readable, and it is not Streak. Streak (arm M)
   and a held-out Gin app are the real tests; neither runs here. PA4 uses an existing fixture as a weak, static check.
2. **Seeder and Go conventions.** The Seeder writes the schema and hashed or plain passwords the Python way. GORM's column
   names, defaults (`created_at`) or a password scheme may disagree. That shows as refuted login or T1 items (PI3). It is
   fixed only in the fixture's declared convention (step 0), never by changing the Seeder.
3. **The inferred foreign key exists only in the profile.** Probes may then assert a reference the database does not
   enforce. Each is listed in `inferences[]`, and an R item refuted on that basis is a finding.
4. **One module download.** If S2 cannot build, the primary gate is incomplete (section 2). A vendored module tree is
   **Ruling Q1**.
5. **The derivation is not the default** (D3). Parity with Python's `spec_policy` is reached only by an explicit policy,
   until Q3 is ruled.
6. **This node cannot show:** MySQL or Streak behaviour (arm M NOT RUN); B on imported Go apps (rank 1); GO805; R or B on
   server dialects; Python-behind-Go rows 11-15.

## 7. Files, frozen hashes, blind vs post hoc, commands

**Owns:**
- **New code:** `lattice/importer/go_rules.py` (`harness_factory`, `infer_owners`) and `lattice/gocheck/derive.py`
  (`derive_go_policy`, `go_spec_policy`).
- **Edits:** `lattice/importer/rules.py`, additive only: the D1 branch, the D2 call, `go_policy.json`, and `inferences`
  for Go. `lattice/gocheck/src/goextract.go` and `goextract_tables.go`: the D4 opt-in only.
- **New fixtures and harnesses:** `tests/spec/goginapp/` (`main.go`, packages, `go.mod`, `go.sum`, `studio.toml`),
  `tests/spec/gogin_harness.py` and `tests/spec/go_harness_rb.py`.
- **New tests:** `tests/importer/test_go_rules.py`, `tests/gocheck/test_go_derive.py` and
  `tests/spec/test_go_layers_rb.py`.
- **New dogfood:** `dogfood/go_parity_probes.py` and `dogfood/go_parity_mutants.py`.
- **Docs:** the results doc and its data.

**Frozen (sha256 at 5125da1, clean tree, m):**

| file | sha256 |
|---|---|
| lattice/spec/check.py | 68f13b288cde4a3fa348412495ba560815e003b342ea9cca90b1a779c1bb525e |
| lattice/spec/probes.py | 7f4fca836922179fcf68806ee7f36713b7d453914f3d39ace7fac9ce495b86e6 |
| lattice/spec/process.py | ca402a564352c836a857426668c55971ece47616fbda584664b361d21abc823b |
| lattice/spec/implementation.py | 7012db6e907e9b853402ed6a433b6690e64fd7b82df37ec96ccd0df647a6d2e6 |
| lattice/spec/__main__.py | e7ca4bf4a0bf39036f2101db4f0c1463f7eb2094d97c6eb336ab5555b109a383 |
| lattice/importer/emit_spec.py | e3c764a21ce3279e1259c764800a011b761a49db47b0d2c50ac466428e58482f |
| lattice/importer/lift.py | 1e964e24aee0cfcc921848f7605deafb68e3cbe55996e4119ccc11c4031c6999 |
| lattice/importer/profile_gen.py | 414fc40431f64e12b3225be373192ded8362fff804480d93cb3d9011a196421c |
| lattice/importer/cli.py | 803d1d80ac7231ffe43b5b7ed42f97544a46ef8b6d1cf5b79d77a468052470ad |
| lattice/importer/review.py | b548263a7d3e5bea0fec1e31bafc2b4c561ec91f4cd42f97d59f65d057a14ba5 |
| lattice/importer/report.py | 0955a69a0d57b3d63348c7388b71ee5ad902da2db2247ed141370d2afaacd175 |
| lattice/importer/mine.py | b3399f96dd67fd5f0a487e36fe23d21d067755f95b5835f98efaeafeacc036eb |
| lattice/importer/workflow_run.py | 9a3da5a40fc0354986c9714b1d551d0a7d2105ec58d17fa44e0fa8f25ce106b0 |
| lattice/importer/workflows.py | beb576d70e0e742047e63554821692d6540a69eb3ad00d01dadfb12ccbd5dd3a |
| lattice/importer/inventory.py | 55849494b1ece51ed04a84e34f87da3f65c4fcc02b9523ac9269009e754f2e3b |
| lattice/importer/frontends/go_gin.py | 9f24e9bb5ac8b442cb92cc47ce1b66424b082d728f48e56579783c1bf038a913 |
| lattice/importer/frontends/\_\_init\_\_.py | 7bc4c98c37c7500552e51f99e5e793f9bb54bf3d7082560f078ca7703ce6fe7e |
| lattice/gocheck/\_\_init\_\_.py | d3cdb84ddd40c7b06ca0f8bf833fdc91234df6b7c1cc39ece3ab060bed0bdb5b |
| lattice/gocheck/src/*.go except goextract.go and goextract_tables.go (20 files, `cat` in `ls` order) | b8662ae544cdd2e6c7a497be8acd1f6530a89a57f4635cf6266501dc209491af |
| lattice/pycheck/derive.py | 63fe9d21936451c52785e71ec183f27c2be8b2aadbf6863d3bced4b0eb317040 |
| lattice/pycheck/checks.py | 14603fcc11aeecec0eaaef90417ae70c020026b45828b421ed259cc91886eea0 |
| tests/spec/goapp/main.go | cb90b6fbfc9a699b39a58d1c0811799710e528d93baaa8f2c435aa2ec1194d66 |
| tests/spec/goapp/go.mod | 697277b1e62960bad82e825879ef5310937f5c69ec89366a73b6361c16f900f0 |
| tests/spec/goapp/go.sum | a52d37317a0824cf7a43ebab8c40f876246bf7c5bd2004fc6a252cf7b97c06da |
| tests/spec/go_harness.py | 061d3823255b2a4ae63d9311f31c68b5adb6be0043fb9f30afe798e60a93ff03 |
| tests/spec/test_process_runtime.py | fd2bfeab04a9d2e92a03027d441aed0ac882c34136625df759119bcd8d16cafa |

The two edited Go files at base: `goextract.go` e97fa18e13cbb08f1b2dbd6f4e3c107caf3ecf00f543a9e928c92c8afe06df2e and
`goextract_tables.go` 45339c5eb1c97c0aa9f55ff0640a92224fdc6f19bd2daf4a2038ab16021d4702. `rules.py` at base:
294e2a585192bd847bbf08bf58442abe289eff845ce35a00babeaa923b0b4d26. These are not touched either: .NET, Studio, MCP, the
mock and web UI, existing tests and goldens (any edit is a finding), Streak and the owner's checkout.

**Blind vs post hoc.** Everything in this document is fixed now, and a missed prediction is a finding. Fixes go in
separate post-hoc commits, rerun with the deltas reported. T-S2 is the truth for S2: if the built fixture deviates from
it, that is a finding, never a reason to rewrite the truth. The orchestrator re-runs the probes independently once after
the freeze. **Freeze:** sha256 of `go_rules.py`, `derive.py`, `rules.py`, the two Go files and the probe runner,
recorded before the scored run. **Pass:** every gated row of section 4 on arm S, mutants 16/16 (by probes or the named
unit tests, disclosed), and PB. Arm M rows are reported as NOT RUN.

```sh
cd /home/user/lattice; export PYTHONDONTWRITEBYTECODE=1 GOTOOLCHAIN=auto
SCRATCH=/tmp/claude-0/-home-user-lattice/307195c7-09d5-5df9-b82b-62010344228d/scratchpad/j1
PY=.venv/bin/python; mkdir -p "$SCRATCH/shims"     # recording docker/claude shims, first on PATH
git status --short                                  # expected empty at start (m)
sha256sum -c "$SCRATCH/frozen.sha256"               # PB-F, from section 7
(cd tests/spec/goginapp && go build -o "$SCRATCH/goginapp" .)   # S2 build; failure => S2 rows NOT RUN
PATH="$SCRATCH/shims:$PATH" $PY dogfood/go_parity_probes.py --arm S --scratch "$SCRATCH/p" --out "$SCRATCH/p-data"
$PY dogfood/go_parity_mutants.py $(seq -f JM%g 1 16)
$PY -m pytest tests/importer/test_go_rules.py tests/gocheck/test_go_derive.py tests/spec/test_go_layers_rb.py -q
$PY -m pytest tests/importer tests/pycheck tests/gocheck tests/spec tests/mcp tests/studio -q -rs   # PB-T
$PY -m lattice.importer tests/spec --package goginapp --rules-only --out "$SCRATCH/imp"            # PI1 by hand
.venv/bin/ruff check <changed .py>; .venv/bin/ruff format --check <created .py>; gofmt -l tests/spec/goginapp lattice/gocheck/src
```

## 8. Questions for the orchestrator

- **Q1. Module source for S2.** Commit only `go.mod`/`go.sum` and download Gin once (proxy reachable, 17.7 s), or
  vendor the module tree (about 5-10 MB, fully offline)? The default here is download, with a NOT RUN on failure.
- **Q2. Where the import gets its schema and seed.** The Seeder's DDL and seed (Python parity, the default here), or the
  harness `db` for the schema (GORM's own) with the Seeder's rows on top?
- **Q3. Should `lattice check` on Go default to `go_spec_policy`** (derived policy plus the spec overlay, as Python does)
  in this node? `check.py` stays frozen here, and the change is left for a node that measures the delta on Streak (arm
  M).
- **Q4. GO805.** Is it accepted that GO805 stays gated, with its text unchanged? Or should J1 add `owner_uses[].raise`
  to the opt-in policy extract and implement GO805 on it (a goextract change, default bytes unchanged, plus about 4 more
  mutants)?
- **Q5. The login gap** (premise 3: R and B ignore `runtime.auth` when no `login` is given). Should it stay a recorded
  finding with harnesses passing `LOGIN`, or be fixed in `check.py` here (unfreezing it, with PB-G as the guard)?
- **Q6.** Is it confirmed that rank 1 (Go lifting), the server-dialect B/R skip and J0 section 4 (a Python inventory)
  are out of J1? And which node owns the server-dialect R/B work, which affects every stack?
- **Q7. Who runs arm M** (the Streak import, Streak layer A byte-compat, MySQL), and on which machine, before J1 is
  merged?

## Amendment 1 (orchestrator rulings, 2026-10-17; defaults, overturnable)

Q1: download Gin once, NOT RUN on failure. Q2: the Seeder's DDL and seed (Python parity). Q3: no; check.py stays frozen. Q4: GO805 stays gated with its text unchanged. Q5: recorded finding, harnesses pass LOGIN; check.py is not unfrozen. Q6: confirmed out of scope: rank 1, the server-dialect B/R skip and a Python inventory. J1 is pre-registered only; no build is started this round.
