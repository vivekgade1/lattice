# Go parity, round 3 (Spike J, node G3): the round-2 failures, Arm M's findings, the remaining J0 rows, and a new blind held-out: pre-registration

## 1. Status, and the exact gaps

2026-10-18. **Written BEFORE any round-3 code. No model was called, no API key was used.** This file is the only one this step
writes; it is committed alone on branch `np-go-round3` (worktree `/Users/vivekgade/Documents/lattice-g3`, from `next-phases`
978b468). Nothing is pushed or merged. Results go to `docs/results/<date>-go-parity-round3-results.md`, data to
`docs/results/data/2026-10-18-go-parity-round3/` (one directory for every stage, so the global run ledger of section 10 has one home).

Read while writing (read only): `docs/HANDOFF-2026-10-18.md` (sections 1-6), `2026-10-17-parity-matrix-j0.md`,
`2026-10-17-go-parity-gaps-prereg.md`, `2026-10-18-go-parity-gaps-results.md`, `2026-10-18-go-parity-gaps-round2-prereg.md`
(Amendments 1-6) and `-results.md` (sections 1-11), and the Arm M results in the separate worktree
`/Users/vivekgade/Documents/lattice-ma/docs/results/2026-10-18-go-parity-arm-m-results.md` (not on `next-phases`; its data is under
that worktree's `docs/results/data/2026-10-18-go-parity-arm-m/`). Code read in this worktree: `lattice/gocheck/src/{routes,reach,owner,goextract_routes}.go`,
`lattice/gocheck/{__init__,derive}.py`, `lattice/importer/{go_rules,rules,emit_spec}.py`, `lattice/importer/frontends/go_gin.py`,
`lattice/importer/inventory.py`, `lattice/spec/implementation.py` (head), `dogfood/go_parity_s3.py` (`machinery`), the archived S3
fixture and its scored import (`tests/spec/gotickets/**`, `docs/results/data/2026-10-18-go-parity-gaps-round2/scored/`). Read only
with `git show` (nothing written there): Streak `build/streak` a0679e9 (`internal/httpapi/{router,errors,registry,ratelimit}.go`,
`internal/habits/routes.go`, `internal/db/owned.go`, `internal/auth/middleware.go`, `studio.toml`, `harness.py`), and
`~/Documents/job-scheduler-go` 00dd1de (`internal/app/{app,handlers,runs}.go`, `spec/spec.py`, `check.sh`, `harness.py`).

Labels: **(m)** measured by an earlier node and restated, not re-scored; **(r)** read from code or data while writing this, or by a
read-only `git show`/`grep`; everything else is a **prediction (p)**.

**What may be claimed as blind in G3.** S1 (`goapp`), S2 (`goginapp`), S3 (`gotickets`), Streak and job-scheduler have all been run
and their failures read: **every number on them is development data (`dev`)**. They are gated as regressions and as corrected
predictions. The **only blind rows** are on **S4**, a held-out Go app written by a separate agent after the library freeze
(section 4), and the two S3 rows that crashed in round 2 (PI4-3, PR3-3), which are blind with respect to `variant2` and the
re-probe but **not** with respect to S3's seed verdicts (63 items, 35 verified, seen in round 2's mutant control, m).

### 1.1 Where Go stands (m)

| item | value |
|---|---|
| J2 gated rows (47) | 43 PASS, 4 FAIL: **PI3-3** share 0.5556 (35/63) < 0.60; **PI4-3**, **PR3-3** crashed (`ModuleNotFoundError: gotickets_harness`); **PA3-3** JM31 gives no GO801 |
| J2 mutants (35) | 32 killed (30 on the strict reading); survivors JM6 (C5b design limit), JM31 (layer A false negative; layer R caught it), JM34 (its 409 is unreachable by probe) |
| Arm M (Streak a0679e9, MySQL 8.4, owner's machine) | **M1 FAIL**: the rules-first import crashes in the Seeder (a SQLite seed handed to a MySQL-only harness); `not_run:server_dialect` exists nowhere under `lattice/`. **M2 MISS** on the letter: `go_spec_policy` = `from_spec` except `tables` (+`TestClockRow`, +`SchemaMigration`); findings 0 = 0. **M3 PASS** (qualified: normalised sha256 `351c3104c613c54a` across v0..v3). **M4**: E2/E6 change no Streak output |
| Arm M post hoc | a planted `GET /v1/habits/{id}` that loads the habit without the `user_id` filter is **not reported by layer A at any library version**; every route's handler shows as `Handle.func1`. E4 gives `NotificationPrefs.quiet_start/quiet_end/nudge_time` the one-value `choices` `["22:00"]`, `["08:00"]`, `["20:00"]` |
| J0 Go cells | 13 S, 6 P, 1 A at the time of J0; J1/J2 since closed rows 6 and 10 on SQLite (S1) and most of row 3 (S2, S3), not re-graded in a document |

### 1.2 Root causes (r unless marked; each is confirmed or refuted in Amendment 2 before any library code)

Classes: **L** library defect or gap, fixed here; **P** prediction or pass-rule error; **O** process; **R** runner; **F** finding only.

| # | failure | root cause (reading) | class | decision |
|---|---|---|---|---|
| G-a | PI3-3 share 0.556; `R2 POST /queues/{qid}/tickets` and `R2 POST /tickets/{tid}/handoffs` "no probe confirmed or contradicted it" | The scored candidate spec's create contracts carry **only the path param** (r, `s3_import_candidate_spec.py`: `ContractParam('qid','path',...)`, no `title`; handoffs no `to_user_id`), while login carries `username`/`password` through `emit_spec`'s login fallback (r, `emit_spec.py:634-637`). `emit_spec._contract_params` emits body params only for `p.source == "body" and p.type` naming a model of `model.models` (r, `emit_spec.py:624-625`, `_body_params:526-531`); the Go front end hands body fields over without such a model (reading, to confirm). The probe's create body then lacks the required field and the app answers 422. Separately the round-2 band (0.60-0.85) was carried from S2 without counting S3's extra by-construction R3 items (prediction error, m) | L + P | **K3** |
| G-b | JM31 (close drops `AND reporter_id = ?`): no GO801 | `RequireUser` calls `sign(secret, id)`, whose body calls `mac.Write(...)` and `mac.Sum(nil)` on the `hash.Hash` from `hmac.New` (r, `gotickets/api/api.go:58-62`). `reach.opaque` treats a method call on a receiver whose inferred type is neither basic nor `error` as opaque (r, `reach.go:82-85`), so the middleware's reach is incomplete and `checkOwnership` skips every route behind it (`owner.go:174-177`, `mwComplete`). Round 2's post hoc: a trivial `RequireUser` body makes the GO801 appear; replacing only `ParseUint` does not (m) | L | **K2** |
| G-c | PI4-3, PR3-3 crashed | `studio.toml` names the harness with a bare module (`gotickets_harness:make_app`); the CLI import puts the import root (`tests/spec`) on `sys.path` (r, `rules.py:939-940`, `cli.py:161-162`), the frozen S3 runner's in-process `machinery()` puts only the repository root (r, `go_parity_s3.py:233`), and `go_gin._harness_attr` only the module directory (r, `go_gin.py:245`) | R (+F: the library resolves a bare harness differently in-process and in the CLI) | **K7**, Tier 0 |
| G-d1 | Streak: planted unscoped `GET /v1/habits/{id}` not reported | Three causes, all in the checker (`routes.go`, `reach.go`), none in the extractor, which already unwraps them (r: Arm M's extract records `wrappers: ["httpapi.Handle"]` and the real handler `habits.handler.get` for all 24 wrapped routes). (1) The checker's handler is `FuncRef(Handle(h.get))` = the literal inside `Handle` (`Handle.func1`) for **every** wrapped route, and roots are de-duplicated by handler function (r, `routes.go:180-185`), so one root stands for 24 routes. (2) In that literal, `fn(c)` calls a function-typed parameter: a local function value, opaque (r, `reach.go:86-91`), so reach is incomplete and the route is skipped. (3) Every Member route's middleware is `auth.Middleware(d)` and `RateLimited(d, ...)`, both `return httpapi.Handle(func(c) error {...})` (r): the same wrapper literal, the same opacity, plus `ParseAccess` (token signature, cause G-b's class) | L | **K1**, **K2** |
| G-d2 | M1: the import crashes on a MySQL-only app | The importer always seeds SQLite through its `Seeder` (r, `rules.py:267-385`) and hands the path to the harness; layer R itself is whole-table SQLite diffs, skipped on server dialects by `lattice check` (r, `check.py` docstring, "layer B ... and layer R ... are skipped, with a note"), which the importer does not use. The registered `not_run:server_dialect` was never implemented (m) | L | **K4** |
| G-d3 | E4 gives time-of-day columns one-value `choices` | `go_rules.infer_choices` gives `choices` to any string column with a `status_facts` entry, from its default plus the constants written (r, `go_rules.py:188-215`); a column with one constant (a default written once) gets a one-element "state set" | L | **K5** |
| G-d4 | M2 `tables` mismatch | `go_spec_policy` takes `tables = {**derived, **stated}` (r, `derive.py:143`): the derived struct names `TestClockRow` (the spec says `TestClock`) and `SchemaMigration` (not in the spec) are added although they carry no owner, private column or machine | L (letter) | **K6** |
| G-e | J0 rows still Go-behind-Python, and Python-behind-Go (J0 section 4) | Row 4: `lattice check` on Go uses `GoPolicy.from_spec` only (Q3 of rounds 1 and 2: no), GO805 gated. Row 16: a process app on SQLite is `unmeasured`. Row 2 (lifting) absent. Rows 11-15: no Python inventory | L / owner decisions | **K8-K11** |
| G-f | No independent held-out has ever been scored for the Go importer/checker on code written blind to the library | S3 was written to a public truth table (round-2 risk 1) | O | **Tier 4** |

### 1.3 Scope (binding)

**In scope:** K1-K10 (Tiers 0-2), the new S3 re-score runner, the new G3 runners, the Streak (Arm M2) and job-scheduler (Arm J)
rows, the held-out S4 (Tier 4), and K11 (Tier 3) **subject to Q2** (it may be split into its own round).
**Out of scope:** Go lifting (J0 row 2, an owner decision, Q3); the server-dialect R/B port (all stacks; Q4); the workflow layer
for Python (J0 row 11) and impact T3 for Python (row 15); .NET; gRPC (`goextract_grpc.go`, `goextract_proto.go` stay frozen);
GO802 keying (C15) and the T2 detector's blindness to Go's capitalised default JSON keys (JM6), both findings carried forward;
renaming the `Customer` role; `review.py`'s ` #k` ids (F10); F1 (login fallback) and F2 (the FastAPI-shaped default 422 rule).

## 2. Decisions (fixed before coding)

**K1 Wrapper-closure handlers and middleware in the checker (`routes.go`, `reach.go`, `owner.go`).**
- A **wrapper** is the extractor's definition, reused unchanged (`goextract_routes.go:470` `wrapperParam`): a project function `W`
  whose body returns exactly one function literal, and that literal calls exactly one of `W`'s function-typed parameters.
- When a route's handler expression, or any middleware expression (in `Use`, a group's arguments, or a route's leading handlers),
  is a call `W(arg, ...)` to a wrapper and the argument at the wrapper's index resolves (`FuncRef`) to a function (a named
  function, a method value, or a function literal):
  - the route's root is the **argument function** (`habits.handler.get`), and the wrapper literal is added beside it to the
    route's reach set with its call of the parameter resolved to the argument (that call is not opaque and adds no helper depth);
  - for middleware, the bound argument (`auth.Middleware`'s literal, `RateLimited`'s literal) is the middleware function.
- Roots are keyed per **(route, bound argument)**, not per handler function, so 24 Streak routes are 24 roots.
- One level only. A wrapper whose argument is itself a wrapper call, a wrapper whose argument does not resolve, or a function that
  calls two parameters keeps today's behaviour (unread, silent).
- Factory closures (`api.CloseTicket(db)` returning a literal, S3) are unchanged: they already resolve.

**K2 Outside-type method calls (`reach.go` `opaque`).**
- A method call `x.M(args)` whose receiver's type is declared outside the app module (inferred from an import, or `x` is a local
  bound to the result of an outside-package call such as `hmac.New`) is **not opaque**, unless the receiver's type or any
  argument's type is `gin.Context`, `gorm.DB`, `database/sql` `DB`/`Tx`/`Conn`/`Stmt`, or an app model (`ModelOf`). This is the
  package-function rule that already exists (`reach.go:73-81`) applied to methods.
- A receiver whose type cannot be inferred at all stays opaque (silence over guessing). Amendment 2 records which kind `Infer`
  gives `mac` in S3 and the token helper of Streak; if it is "unknown", K2's second clause is re-registered there, before code.
- Amendment 2 also lists, from a read-only scan with the 978b468 checker plus a debug dump in a scratch copy, every route of every
  existing `tests/gocheck/fixtures/*`, `tests/spec/goapp`, `goginapp`, `gotickets` and the implementation fixture whose reach K1 or
  K2 would turn complete. The layer-A byte-compat promise (section 8) is "identical except the listed routes".

**K3 Request-body fields in Go create/update contracts (Go import only, `go_rules.py`).**
- Step 0 (Amendment 2, before code): the builder names the exact line where a Go route's body fields stop reaching
  `_contract_params`, from a read-only run on S3.
- Fix, in `InferringFrontEnd` (the frozen `emit_spec.py` and `go_gin.py` stay untouched): for each route whose extract carries
  bound body fields, synthesise one in-memory request model `<Struct>` (the Go struct's own name; `<METHOD><path>Body` when the
  struct is anonymous) with those fields, their types, `required`, bounds and `choices` as the extract states them, and point the
  route's body `RouteParam` at it (`source="body"`, `type=<model>`). Recorded as `{"rule": "body_model", "route", "model",
  "fields", "evidence"}` in `inferences[]`.
- A field the extract does not bound stays unbounded; nothing is guessed from names.

**K4 The import on a server-database app: an honest `not_run:server_dialect` (`rules.py`, Go branch only).** Decided: **skip**, not
a MySQL seed path. Reason: a MySQL seed would still leave layer R unable to run (whole-table SQLite diffs), so it buys nothing
without the server-dialect R port, which affects every stack (Q4).
- Detection: the module's `studio.toml` `[app] database` is `mysql`, `postgres` or `clickhouse` (the key Studio and Streak
  already use, r). No new CLI option (`cli.py` stays frozen); no guess from the harness's behaviour.
- Then the import does not build a `Seeder`, start the app, run layer R, or mine. It writes `report.json` with
  `rules: {"status": "not_run:server_dialect", "dialect": "<d>", "items": {}}` and no verdicts, `report.md` whose first line says
  layer R was not run and why, `profile.py`, `candidate_spec.py` (contracts and inferences; no verdict comments), `proposals.json`
  as an empty list with `"not_run": "server_dialect"`, `go_policy.json`, and `inferences[]`. Exit 0. No Docker call, no SQLite file.
- `review list` on that directory lists 0 items and exits 0.

**K5 State choices need two values (`go_rules.infer_choices`).** A string column gets `choices` only when the default plus the
constant values written or named as `from` states give **at least two distinct values**. Otherwise no `choices` and no
`status_facts` inference. Nothing else changes (S2 `Run.status`, S3 `Ticket.status` keep theirs, p).

**K6 Spec-table identity in `go_spec_policy` (`derive.py`).**
- A derived table whose SQL name equals a declared spec table's SQL name **is** that table (the derived schema-name key is
  dropped).
- A derived table the spec does not declare and that carries **no** derived fact (owner, `owner_via`, private column) is dropped
  from `tables` and listed by a new helper `undeclared_tables(spec, module_dir)`; the import's `go_policy.json` gains
  `"undeclared_tables": [...]` only when non-empty.
- A derived table that carries a fact and is not declared is kept (a check stays alive).

**K7 Runner-side harness resolution (new runners only).** Every G3 runner puts the import root on `sys.path` exactly as
`rules.py:939-940` does before any in-process `machinery()`. The library difference (G-c, F) is **not** fixed in G3 unless Q5 says
so; if Q5 says yes, `go_rules.harness_factory` and `rules._runtime` also try the import root (<= 12 lines, `go_rules.py`).

**K8 GO805 for Go (Tier 2; `goextract_owner.go`, `goextract_raise.go`, `lattice/gocheck/notowned.py` new, `gocheck/__init__.py`).**
- Opt-in extract (`{"policy": true}` only; default bytes unchanged): each owner `compare` use gains `raise: {status, file, line}`
  when the comparison is in the `Where` of a GORM chain whose error result (`.Error`, `err :=`, `RowsAffected == 0`) is tested by
  the **next** statement and that branch returns one constant 4xx refusal. Structural only; no line-proximity pairing.
- Derivation: the module's not-owned answer = the single status shared by every linked raise; disagreement = `unclear` (GO805
  stays silent for that module, with a note).
- Check: a route whose owner-scoped lookup refuses with another status than the module's answer gives
  `GO805|<METHOD path>|not_owned` (warning, as PY805). GO805 leaves `GO_GATED`; its text moves to `GO_CODES`.

**K9 `lattice check` on Go defaults to `go_spec_policy` (Tier 2; only if Q6 says yes; `check.py` <= 10 lines).** Arm M measured that
the findings are equal on Streak (0 = 0, m); G3 measures it on every Go fixture (PB-G).

**K10 The re-graded parity matrix J0' (Tier 2; a document).** `docs/results/<date>-parity-matrix-j0prime.md`: every J0 row re-graded
for Python and Go at the G3 commit, each cell citing a file:line and a test or a results row; .NET stays `A`.

**K11 A Python inventory (Tier 3; subject to Q2).** `python -m lattice.cli import inventory <root> --package P` for a FastAPI,
Django + DRF or Flask app: a new `lattice/importer/pyinventory.py` writes `extract.json` in the goextract shape the readers need
(routes, auth, tables, raises, owner uses where the Python model states them), `inventory.json` with `frontend: "python_<fw>"`, and a
source set of `*.py`, `pyproject.toml`, `requirements*.txt`. `implementation.py` takes the source set from `frontend`. Target: J0
rows 12 (implementation report), 13 (`spec ask` `impl:`), 14 (app map) become S for Python. Rows 11 and 15 stay as they are.

**K12 Never.** No write into a fixture, target, Streak (`~/Documents/streak`), job-scheduler (`~/Documents/job-scheduler-go`) or the
main checkout; Streak and job-scheduler are used only through `git archive` copies in scratch. No model, no `claude` (except the
separate held-out author and mutant writer, Tier 4, dispatched by the orchestrator through the `claude` CLI), no API key, no
`docker` outside the rows that name it (a recording shim is first on `PATH` everywhere else). No edit to a frozen file. The build
nodes never commit and never `git add`.

## 3. Tiers (an agent graph; at most 4 agents in parallel)

| tier | node(s) | model | depends on | gate (all rows of the tier, section 5) |
|---|---|---|---|---|
| 0 | **T0** S3 re-score: writes `dogfood/go_parity_s3r.py` + test; the orchestrator freezes it (Amendment 2), runs it once on the **round-2 library** (978b468, before any G3 library edit), an opus verifier re-runs it | sonnet build, opus verify | Amendment 1 | T0 rows |
| 0' | **RC** step-0 confirmations G-a..G-d4 (read-only scripts on scratch copies; outputs hashed into Amendment 2) | sonnet (same node as Tier 1) | Amendment 1 | none (a refuted cause re-registers its decision in Amendment 2 before code) |
| 1 | **B1** K1-K7 + runners `go_parity_probes3.py`, `go_parity_mutants3.py` (all arms, including H against stand-ins) | sonnet, own worktree | RC, Amendment 2 | Tier 1 rows |
| 2 | **B2** K8-K10 (K9 only on Q6 yes) | sonnet, own worktree, files disjoint from B1 | Amendment 2 | Tier 2 rows |
| 3 | **B3** K11 (only on Q2 = in this round) | sonnet, own worktree, files disjoint from B1/B2 | Amendment 2 | Tier 3 rows |
| freeze | orchestrator merges B1-B3 into one tree, runs the runner self-test on every arm, records **Amendment 3** (every changed file, runners, harness API brief, ledger path) | — | B1-B3 | self-test 100% |
| 4 | **A** held-out author (separate, isolated) -> contract check -> **W** mutant writer (separate) -> scored run once (orchestrator) -> **V** independent opus re-run | author and W: sonnet via `claude` CLI; V: opus | Amendment 3 committed | Tier 4 rows |

## 4. The held-out S4 (Tier 4)

**Author brief rules (binding; the orchestrator writes the brief, hashed in Amendment 3).**
1. Task in product terms only: "a small multi-user HTTP JSON service in Go, Gin + GORM over `github.com/glebarez/sqlite`, 10-16
   routes, a login, per-user data on at least two tables, at least one table whose rows belong to a user through a parent row, a
   column that is never returned, a status column with at least two transitions, and request validation; organise it the way you
   would organise a production service." The domain is the author's choice and must not be tickets, runs, habits or jobs.
2. **No idiom is named**: no mention of wrappers, closures, middleware styles, error-returning handlers, token formats, hashing,
   `json:"-"`, helper packages, how ownership is checked, or anything from sections 1.2 or 2. No Lattice internals.
3. Every harness fact, complete, in a `HARNESS_API.md` the orchestrator writes from `lattice/spec/process.py` and
   `tests/spec/gogin_harness.py` and checks on S2 before the freeze: `build_db(path)` **returns** the path; `make_app(path) ->
   ProcessApp` with every `ProcessApp` field named; `RUNTIME = process_runtime(framework="go", ...)`; `LOGIN`; the seed users and
   the Seeder's plain-password convention; the `studio.toml [app]` keys with the **dotted** form (`tests.spec.<x>_harness:make_app`)
   as the required form; `go.mod`/`go.sum` committed, `go build -mod=readonly`, Go 1.26.2, modules from the proxy.
4. Deliverables: the module, the harness, `studio.toml`, and `truth.json` in the schema below, with a file:line for every fact.
5. One fixed scratch directory; the orchestrator audits every tool call from the transcript (0 accesses outside it, and 0 reads of
   `lattice/**`, `docs/**`, `dogfood/**`, `tests/**` of the repository).

**`truth.json` schema (fixed now).** `routes[{method, path, auth: Public|Member|Admin, file, line}]`; `tables[{struct, table,
columns[{name, json, private}]}]`; `owners {struct: column}`; `owner_via {struct: [parent_struct, fk_column]}`;
`machines {"Struct.column": {initial, edges[[from, to]]}}`; `refusals[{route, status, detail, cause:
auth|not_found|not_owned|invalid|conflict|other, file, line}]`; `bind_status`; `login {path, fields}`; `seed_users[{name, role}]`.

**Contract check (before scoring; one author fix round allowed, logged).** `go build`; `go vet`; the harness smoke (`build_db`
returns its argument; `make_app` starts; `/health` 200; every seed user logs in); every truth route answers neither 404 nor 405 to
an authenticated call (the `reconcile` rule); `truth.json` validates and every file:line exists. Nothing of Lattice runs on S4 before
the scored run.

**Mutant writer W (after the contract check, before scoring).** A second separate agent receives only S4 and `truth.json` and the
operator table of section 6.2, writes 16 patch files by the instance rule given there, builds each, and never runs Lattice. The
patches are hashed into **Amendment 4** before the scored run.

## 5. Probes (runner: `go_parity_s3r.py` for Tier 0, `go_parity_probes3.py --arm 0,S,M,J,P,H,X` for the rest)

### 5.1 Tier 0: S3 re-score on the round-2 library (S3 is dev; these two rows were registered blind in round 2 and never computed)

| id | probe | prediction (p) | pass |
|---|---|---|---|
| T0-PI4 | round 2's PI4-3: the import's corrected spec on `variant2` | 35 seed-verified; 34-35 still verified | >= **0.95** (round 2's rule) |
| T0-PR3 | round 2's PR3-3: layer R with the imported `candidate_spec.py` | identical verdict map, 63 = 63, 0 corrections | identical after round 2's correction map |
| T0-CTL | in-process `machinery()` (root on `sys.path`) vs the CLI import's final verdicts on the seed | identical (63 items, 35 verified) | identical |
| T0-FORM | the same on a scratch copy of S3 whose `studio.toml` uses the dotted form | identical to T0-CTL | identical |
| T0-FREEZE | `freeze.sha256` (21), `s3runner.sha256` (3), `s3.freeze.sha256` (8 files) and the new runner's own entry, before any metric | all OK | all; else abort, no metric |

### 5.2 Tier 1, Arm 0 (static fixtures; a new parse-only `tests/gocheck/fixtures/goparity3/` with `truth.json`, written before library code)

`goparity3` holds: (w1) a route `r.GET("/w/:id", Handle(h.getW))` through a one-level wrapper, unscoped read of an owned table; (w2)
the same, scoped; (w3) a two-level wrapper `Handle(Wrap2(h.getW))`, unscoped; (m1) a guard middleware returned through the
wrapper that verifies a header with `hmac.New(...).Write/Sum`, in front of an unscoped route; (m2) a guard that passes the
`*gin.Context` to a method of an outside type, in front of an unscoped route; (b1) a create binding a named body struct with
`title binding:"required,max=40"`; (c1) a string column with one constant write; (c2) a string column with two.

| id | probe | prediction (p) | pass |
|---|---|---|---|
| P0-WRAP | layer A on goparity3 | GO801 on w1; none on w2; none on w3 (silent, two levels) | exact |
| P0-OPQ | layer A on goparity3 | GO801 on the m1 route; none on the m2 route | exact |
| P0-BODY | K3 on goparity3 b1 | contract param `title` body Str required `max_len=40`; one `body_model` inference | exact |
| P0-CHOICES2 | K5 on goparity3, S2, S3 | c1 no choices; c2 two choices; S2 `Run.status` (`queued`, `cancelled`); S3 `Ticket.status` (`open`, `closed`) | exact |
| P0-REG | round-2 static rows P0-PARSE, P0-OMIT, P0-BIND, P0-LIBMW, P0-PRIV, PA4 re-expressed on the G3 runner | as round 2 (m) | exact |

### 5.3 Tier 1, Arm S (S1, S2, S3; dev)

| id | probe | prediction (p) | pass |
|---|---|---|---|
| PS-PI3-3 | S3 import, rule items on the seed (K3) | 63-70 items; verified share **0.60-0.68**; refuted 0-2, each with a correction; `R2 POST /queues/{qid}/tickets` and `R2 POST /tickets/{tid}/handoffs` verified | share >= **0.60** (not lowered); both R2 creates verified; every refuted item has a correction or a baseline entry; every unexercised item a reason |
| PS-PI4-3 | the corrected spec on `variant2` | every seed-verified item still verified | >= 0.95 |
| PS-PR3-3 | R with the imported spec | the same verdict map | identical after the correction map |
| PS-JM31 | S3 copy, close drops `AND reporter_id = ?`, under `go_spec_policy` of S3's candidate spec (K2) | `GO801\|POST /tickets/:tid/close\|Ticket`; original 0 outside the baseline | exact key; original 0 |
| PS-ST2 | S3 copy, get drops the reporter filter | `GO801\|GET /tickets/:tid\|Ticket` | exact |
| PS-ST3 | S3 copy, reopen drops the reporter filter | `GO801\|POST /tickets/:tid/reopen\|Ticket` | exact |
| PS-S3A | layer A on the S3 original, `from_spec` of the candidate spec and `go_spec_policy` | 0 findings each | 0 |
| PS-S2 | S2 regressions PI1-PI7, PR3, PR-T2, PA1-PA3 (round-2 corrected predictions) | as round 2 (m) except PI3: 41-44 items, share 0.61-0.68 (K3 may add body items) | round 2's rules |
| PS-S1 | S1 regressions PR1, PR2, PR4, PB1, PB1-CLI, PB2, PB3 | as round 2 (m) | round 2's rules |

### 5.4 Tier 1, Arm M2 (Streak a0679e9 from `git archive build/streak`; layer A and the import are Docker-free; M-R1 needs Docker)

| id | probe | prediction (p) | pass |
|---|---|---|---|
| M-A0 | layer A on the Streak original, `GoPolicy.from_spec(SPEC)` | **0 findings** (range 0-2) | **0**; every finding is listed and classified; a false positive fails the row |
| M-A1 | judged routes (root and middleware reach complete), informational | 978b468: 0-1 of 26; G3: 18-24 of the 24 wrapped routes | reported |
| M-SM1 | scratch copy: `GET /v1/habits/{id}` loads through an unscoped `AnyHabit(tx, id)` (Arm M's mutant, byte-identical) | `GO801\|GET /v1/habits/:id\|Habit` | exact |
| M-SM2 | scratch copy: `PATCH /v1/habits/{id}` the same | `GO801\|PATCH /v1/habits/:id\|Habit` | exact |
| M-SM3 | scratch copy: `GET /v1/habits/{id}/completions` loads the habit unscoped (`owner_via` Completion -> Habit) | a GO801 on that route, table `Habit` or `Completion` | a GO801 on that route |
| M-SM4 | scratch copy: `POST /v1/nudges/{id}/dismiss` with `OwnedNudge` replaced by an unscoped `Where("id = ?")` (`owner_via` through a JOIN) | GO801 on that route (p 0.6) | reported |
| M-I1 | `python -m lattice.importer <copy> --package . --rules-only --framework gin` (K4) | exit 0; `rules.status = not_run:server_dialect`, `dialect = mysql`; 26 routes, 26 contracts, 0 rule items; `proposals.json` empty with the reason; 0 docker calls; no SQLite file under the scratch tree | exact |
| M-I2 | inferences of that import | 4 `owner_uses` (NotificationPrefs, Device, RefreshToken, Habit; `user_id`), 2 `json_dash`, `status_facts` only for `Nudge.delivery` (`failed, pending, sent, suppressed`); no `choices` on `quiet_start`, `quiet_end`, `nudge_time` (K5) | exact |
| M-P1 | Arm M's M2 again (K6) | `go_spec_policy(SPEC, copy) == GoPolicy.from_spec(SPEC)` in **every** field, `tables` included; `undeclared_tables` = `[SchemaMigration]` plus `TestClockRow` unless its SQL name equals the spec's `TestClock` (Amendment 2 reads it, r) | equal in every field |
| M-R1 | `lattice check --layers ACD --database mysql` (Arm M's command, Docker) | exit 0, pass, 0 findings, 480 cases; normalised sha256 = `351c3104c613c54a` | pass with 0 findings; a normalised difference is listed and allowed only in layer A's notes |

### 5.5 Tier 1, Arm J (job-scheduler-go 00dd1de from `git archive`; SQLite; Docker-free)

job-scheduler checks access through a membership table (`appAccess`, r) and its spec declares **no owner column** (r), so GO801 has
nothing to judge there by design. These rows measure what each layer says about the same defect class; only J-0 and J-A are gated.

| id | probe | prediction (p) | pass |
|---|---|---|---|
| J-0 | the owner's `check.sh` command without `go build` into the copy's tree (`--spec spec.tickets:t5 --layers ABCDX` on SQLite) | pass, 0 findings; normalised report identical to the 978b468 library | identical |
| J-A | layer A on the original | 0 findings; judged-route count reported | 0 |
| J-SM1 | scratch copy: `jobRun` drops `AND application_id = ?` | layer A 0 (by design); B or D reports it (p 0.5) | reported |
| J-SM2 | scratch copy: `appAccess` skips the membership lookup | layer A 0 (by design); D reports it (p 0.8: the spec's "not a member: as if it did not exist" scenario) | reported |

### 5.6 Tier 2 (K8-K10)

| id | probe | prediction (p) | pass |
|---|---|---|---|
| P2-805A | K8 derivation on S2, S3, goparity3, Streak | S2 404 (3 linked raises); S3 404 (6); Streak `unclear` or none (its refusals are error values, not constants) | exact for S2, S3; Streak reported |
| P2-805B | S3 copy: get answers 403 on a scoped miss | `GO805\|GET /tickets/:tid\|not_owned` | exact |
| P2-805C | originals S1, S2, S3, Streak, job-scheduler | 0 GO805 | 0 |
| P2-805D | `gated_note()` and `GO_GATED` | GO805 removed from the gated list, GO804/GO806 text byte-identical | as stated |
| P2-DEF | K9 (if Q6 yes): `lattice check` on goapp, goginapp, gotickets, the implementation fixture, Streak (layer A) with the new default | findings identical to `from_spec` on all five | identical |
| P2-J0 | K10: every cell of J0' cites a file:line that exists at the G3 commit and a test or results row | 100% | 100% (an independent check by V) |

### 5.7 Tier 3 (K11; only if Q2 = in this round)

| id | probe | prediction (p) | pass |
|---|---|---|---|
| P3-INV | `import inventory` on `bench/import_repos/multivendor` and the FastAPI rules fixture | `inventory.json` with `frontend: python_fastapi`, route and table counts equal to the `RepoModel` | exact |
| P3-IMPL | `implementation` report on each with the spec imported from the same app | runs; 0 `missing`/`extra` route rows | 0 |
| P3-IMPLM | the same with one route removed from the spec | exactly 1 `extra` route row naming it | exact |
| P3-ASK | `spec ask` digest has an `impl:` section | present | present |
| P3-MAP | app map state is not `inventory_missing` | true | true |
| P3-GO | every Go inventory, implementation report, app map and impact T3 output on the existing fixtures | byte-identical | identical |

### 5.8 Tier 4, Arm H (held-out S4; blind; scored once)

| id | probe | prediction (p) | pass |
|---|---|---|---|
| H-PI1 | import: routes and auth vs `truth.json` | route recall 0.90-1.00; auth exact on 0.85-1.00 of the routes | recall >= **0.90**; auth >= **0.85**; 0 extra routes |
| H-PI2 | owners | every truth owner inferred or `unclear` naming it (recall 0.5-1.0); **0 confident-wrong owners** | 0 confident-wrong |
| H-PI3 | rule items on the seed | verified share **0.50-0.70**; refuted 0-4 | share >= **0.50**; every refuted item has a correction or a baseline entry; every unexercised item a reason |
| H-PI4 | corrected spec on `variant2` | >= 0.95 retained | >= 0.95 |
| H-PI5 | proposals | the truth machine's first edge proposed (p 0.7) | reported |
| H-PI7 | import 10x | identical | 10/10 |
| H-PR3 | R with the imported spec | the same verdict map | identical after the correction map |
| H-PA1 | `derive_go_policy` | private columns ⊆ truth private (0 public column marked private); owners ⊆ truth owners | 0 wrong |
| H-PA0 | layer A on the original under `go_spec_policy` of its candidate spec | 0 findings outside the baseline (0 false GO801/GO802 on truth-safe code) | 0 |
| H-JUDGE | share of truth owner-scoped routes judged by layer A (complete reach) | 0.6-1.0 | >= **0.75** |
| H-X | wall time of the import (binary prebuilt) | 8-40 s | <= 180 s |

### 5.9 Cross-arm

| id | probe | prediction (p) | pass |
|---|---|---|---|
| PW | snapshot (sha256, mode, mtime, listing with hidden files) of goapp, goginapp, gotickets, goparity2*, goparity3, `tests/spec/fixtures/implementation`, S4, the Streak and job-scheduler copies' sources; recording `docker`/`claude` shims | 0 changed; 0 shim calls outside M-R1 | all 0 |
| PB-F | section 8's frozen table at 978b468 | all identical | all |
| PB-X | default `gocheck extract` on the 7 round-1 dirs, goapp and `tests/gocheck/fixtures/runs` | 9 of 9 identical to 978b468 | all |
| PB-G | layer A findings and notes on every existing gocheck fixture, goapp, goginapp, gotickets and the implementation app; workflows and inventory on `goextract_workflow`; `test_process_runtime.py` | identical except the routes Amendment 2 lists (K1, K2) | as stated |
| PB-P | Python rules imports (J1 deviation-8 normaliser), no `inferences`, `derive_policy`; PB-P2 (decoy `studio.toml`) | identical | all |
| PB-N | stack-neutral outputs: `tests/contracts`, `tests/improve`, `tests/studio/test_rebuild.py` results and the contracts exported for goginapp's and gotickets' imported specs | identical | all |
| PB-H | `lattice --help`, `python -m lattice.importer --help`, `python -m lattice.spec check --help`, MCP `tools/list` | identical | all |
| PB-T | `pytest tests/importer tests/pycheck tests/gocheck tests/spec tests/mcp tests/studio -q -rsf --continue-on-collection-errors -p no:cacheprovider` | the same failing, skipped and error ids as 978b468 (the 7 known failures, section 3 of the handoff), plus the new tests passing | same ids |
| PB-D | `dogfood/studio/check.sh` (venv python) | passes, same step count | pass |

**Gated row count:** Tier 0: 5; Arm 0: 5; Arm S: 9; Arm M2: 8 (M-SM4 reported); Arm J: 2; Tier 2: 5 or 6; Tier 3: 6 (if in);
Arm H: 10 (H-PI5 reported); cross: 10. The runner prints the exact list it gates before the first metric.

**Why these thresholds.** S3's 0.60 and 0.95 are round 2's and are not lowered. The held-out's 0.50 is S1's floor for an app whose
idioms are unknown (S3 reached 0.556 before K3, m). H-JUDGE 0.75 is the generalisation claim of K1/K2: three in four owner-scoped
routes of code written blind must be judged at all. 0 false positives (M-A0, H-PA0, PS-S3A) because a layer-A finding on a passing
app fails the owner's build gate.

## 6. Mutants (control first in every runner; a mutant is killed when a gated detector that passes on the control flips, or a named unit test flips)

### 6.1 Library mutants (`go_parity_mutants3.py`; predicted 18/18; gate 18/18)

| id | mutant | predicted killer |
|---|---|---|
| LM1 | K1 off (handler root = the wrapper literal, round-2 behaviour) | P0-WRAP; M-SM1 |
| LM2 | K1 roots de-duplicated per handler function again | M-SM2 (PATCH shares the root with GET) |
| LM3 | K1 binds two levels (w3 judged) | P0-WRAP |
| LM4 | K1 for handlers only, not middleware | M-SM1 (middleware reach stays incomplete) |
| LM5 | K2 off | P0-OPQ; PS-JM31 |
| LM6 | K2 too broad: a receiver handed `*gin.Context` is not opaque | P0-OPQ (m2 gains GO801) |
| LM7 | K2 too broad: an uninferable receiver is not opaque | unit `uninferable_receiver_stays_opaque` |
| LM8 | K3 off | P0-BODY; PS-PI3-3 |
| LM9 | K3 drops `required` | P0-BODY |
| LM10 | K4 off (the Seeder runs on a server-dialect app) | M-I1 |
| LM11 | K4 on for a SQLite `studio.toml` (S2 reports `not_run`) | PS-S2 (PI3) |
| LM12 | K5 off (one-value choices back) | P0-CHOICES2; M-I2 |
| LM13 | K5 needs three values | P0-CHOICES2 (S2 loses its choices) |
| LM14 | K6 off | M-P1 |
| LM15 | K6 drops an undeclared table that carries an owner | unit `undeclared_owned_table_is_kept` |
| LM16 | K8 pairs by line proximity instead of the next-statement link | P2-805A (goparity3 decoy) |
| LM17 | K8 derives a status from a disagreeing module | unit `disagreeing_raises_are_unclear` |
| LM18 | K7 off in the new runner (bare-module crash returns) | T0-CTL (on a bare-form copy) |

Runner mutants (process): **RM1** the runner reports an arm `not_run` with every prerequisite present -> the self-test fails;
**RM2** the self-hash check skips one entry -> the freeze test fails. Both must be caught before Amendment 3.

### 6.2 Held-out mutants (written by W, section 4; gate >= **13 of 16**, and the class gates below)

Instance rule: in `truth.json` order, the first and the last route that fits the operator (when only one fits, it is used once and
the next operator in this table gets three instances; W records each substitution).

| ids | operator | gated detector | class gate |
|---|---|---|---|
| HM1-2 | drop the owner filter of an owner-scoped read | layer A GO801 on that route | >= 3 of HM1-4 |
| HM3-4 | drop the owner filter of an owner-scoped write or state change | layer A GO801 on that route | (with HM1-2) |
| HM5-6 | expose a never-returned column on a route that answers its row | GO802 on that route and column | 2/2 |
| HM7-8 | remove the login requirement from a Member route | layer R `R1` of that route refuted | 2/2 |
| HM9-10 | answer another status (403) where truth says 404 not_found | layer R that 404 item refuted | 2/2 |
| HM11 | drop the parent-ownership check of an `owner_via` table | reported (A sees it only if the imported spec has `owner_via`, which the import does not infer, p survive) | — |
| HM12-13 | drop a state guard (`conflict`) of a transition | reported (p: 0-1 killed, JM34's class) | — |
| HM14 | loosen a validation bound (remove the upper bound) | reported (R3 422 item, p kill) | — |
| HM15 | take the owner from the request body on create | reported (p 0.5) | — |
| HM16 | widen a list route's owner filter to every row | layer A GO801 or layer R | reported |

Predicted: 12-15 of 16 killed.

### 6.3 Dev mutants on the regression arms (gated as listed in section 5)

ST1 = PS-JM31, ST2 = PS-ST2, ST3 = PS-ST3 (gate 3/3); SM1-SM3 (gate 3/3), SM4 reported; J-SM1, J-SM2 reported. Re-applied on the
G3 runner with round 2's detectors (the frozen round-2 runners pin round 2's library hashes and cannot run on the G3 tree):
JM5, JM17, JM18 (layer A), JM19, JM22, JM24, JM26-JM29 (importer), JM32, JM33 (S3). Predicted 12/12, gate 12/12. JM6 and JM34
are not re-applied (their survival is explained in round 2 and not addressed by G3).

**Mutant total:** 18 library + 16 held-out + 3 S3 + 4 Streak + 2 job-scheduler + 12 re-applied = **55** (plus 2 runner mutants).

## 7. File ownership and line budgets (`git diff --numstat` insertions + deletions vs 978b468)

| node | file | sha256 at 978b468 (r) | decision | budget |
|---|---|---|---|---|
| B1 | `lattice/gocheck/src/routes.go` | `a37807678492691610da61e8c648acc310c0fcaa86b367a34b43c401115fab8e` | K1 | <= 60 |
| B1 | `lattice/gocheck/src/reach.go` | `891a8052a913c5d1463b1d02f99a306226ea0bfb6ad05754b8be4009787b092c` | K1 (bound call), K2 | <= 45 |
| B1 | `lattice/gocheck/src/owner.go` | `c060a0dd2787580cbb82d4ecfb8c13bbdf35d35dff784df5d226e32428daf226` | K1 (pass the root's binding to `reach`) | <= 10 |
| B1 | `lattice/importer/go_rules.py` | `180148d8b75b9175414488a4e7721117c97f90673675be4f09ab5099ab1f8c12` | K3, K5 (K7 library part only on Q5 yes, +12) | <= 120 |
| B1 | `lattice/importer/rules.py` | `5a987b818cf0de415db1a0bd610caa0c0ce7b33ee7f2d388ea1ded4fee57b6ff` | K4 | <= 45 |
| B1 | `lattice/gocheck/derive.py` | `2e8ca90f855daaa80131b3f61288ea8eeb6c3b8d4b8308f7cf02fef7f137db74` | K6 | <= 35 |
| B2 | `lattice/gocheck/src/goextract_owner.go` | `bd358798d9854264bda77e62227c44be2e87cbc4ec15f6732ab22c64b598e086` | K8 opt-in link | <= 50 (with raise.go) |
| B2 | `lattice/gocheck/src/goextract_raise.go` | `1f8db0956e87bcc5392f4c08fb38d525267b0280733463b04acd7197a9d8e5ad` | K8 | (above) |
| B2 | `lattice/gocheck/__init__.py` | `d3cdb84ddd40c7b06ca0f8bf833fdc91234df6b7c1cc39ece3ab060bed0bdb5b` | K8 (ungate, merge GO805 findings) | <= 40 |
| B2 | `lattice/spec/check.py` | `1c1e84e278e8c7397a40118ffa86476a9cfde93afd76d52cbbcffc81ef887691` | K9, **only on Q6 yes** | <= 10 |
| B3 | `lattice/importer/inventory.py` | `55849494b1ece51ed04a84e34f87da3f65c4fcc02b9523ac9269009e754f2e3b` | K11 | <= 40 |
| B3 | `lattice/spec/implementation.py` | `89bc16f4679a77f34505fafbca4d33a4a021968aec93407e272a852b0fc319b8` | K11 (source set by frontend) | <= 40 |

**Creates.** T0: `dogfood/go_parity_s3r.py`, `tests/spec/test_go_parity_s3r.py`. B1: `tests/gocheck/fixtures/goparity3/**` (with
`truth.json`; written **before** library code and hashed in Amendment 2), `tests/gocheck/test_go_wrap3.py`,
`tests/importer/test_go_rules3.py`, `tests/gocheck/test_go_derive3.py`, `dogfood/go_parity_probes3.py`,
`dogfood/go_parity_mutants3.py`, `dogfood/g3_standin/**` (stand-in truths for the runner self-test). B2:
`lattice/gocheck/notowned.py` (<= 140 lines), `tests/gocheck/test_go805.py`, the J0' document. B3: `lattice/importer/pyinventory.py`
(<= 350 lines), `tests/importer/test_pyinventory.py`. Tier 4 (orchestrator copies byte-for-byte after delivery): S4 under
`tests/spec/<author's name>/` and its harness. The results doc and data. This file changes only by appended Amendments.

Each builder edits only its rows and creates only its creates; a needed edit elsewhere stops the node and goes to the orchestrator.
The builders never commit; the orchestrator stages explicit paths (never `git add -A` or `git add .`).

## 8. Frozen files and the byte-compat promise

**Frozen (empty diff vs 978b468; values r, `git show 978b468:<path> | shasum -a 256`).**

| file | sha256 |
|---|---|
| lattice/spec/check.py (unless Q6 yes) | 1c1e84e278e8c7397a40118ffa86476a9cfde93afd76d52cbbcffc81ef887691 |
| lattice/spec/probes.py | 7f4fca836922179fcf68806ee7f36713b7d453914f3d39ace7fac9ce495b86e6 |
| lattice/spec/process.py | 047be55e9f421700efab2441525121c8387167fb53edcd5991ebdc4592cdebf1 |
| lattice/spec/__main__.py | 81beadf5b8040420af6f1d05b1f8b202f213430df8853a5346a506414c1a24e1 |
| lattice/spec/appmap.py | c7e2d5fe2589c0eb176f34153b352534ea3c01385a4b4894be4ed52ba0abf590 |
| lattice/spec/ask.py | c8e1e7d24020e6f4e4bebe2bbdd695e729e4fd0d856f3c38c7a6e97eadf245f2 |
| lattice/spec/impact_tier3.py | 22228513beba44fcff4b0d5ab48c662d2010e7ac320b73ac860f134ffb6e2faf |
| lattice/importer/emit_spec.py | c166e7313184c8a2c2d5b282eb3d392c5df16d4f1bd565b2ebff93d4229791b0 |
| lattice/importer/suite.py | e40bca823dd3a7de166b11f4e0715b65801d0b490529e72d2d0f70dc21c4c83c |
| lattice/importer/lift.py | 1e964e24aee0cfcc921848f7605deafb68e3cbe55996e4119ccc11c4031c6999 |
| lattice/importer/profile_gen.py | 414fc40431f64e12b3225be373192ded8362fff804480d93cb3d9011a196421c |
| lattice/importer/cli.py | 803d1d80ac7231ffe43b5b7ed42f97544a46ef8b6d1cf5b79d77a468052470ad |
| lattice/importer/review.py | b548263a7d3e5bea0fec1e31bafc2b4c561ec91f4cd42f97d59f65d057a14ba5 |
| lattice/importer/report.py | 0955a69a0d57b3d63348c7388b71ee5ad902da2db2247ed141370d2afaacd175 |
| lattice/importer/mine.py | b3399f96dd67fd5f0a487e36fe23d21d067755f95b5835f98efaeafeacc036eb |
| lattice/importer/workflow_run.py | 9a3da5a40fc0354986c9714b1d551d0a7d2105ec58d17fa44e0fa8f25ce106b0 |
| lattice/importer/workflows.py | 5eba7e8ab6ac62ba8ba4cea31088faab9e149e921a952c6a4837e37b92638098 |
| lattice/importer/frontends/go_gin.py | 9f24e9bb5ac8b442cb92cc47ce1b66424b082d728f48e56579783c1bf038a913 |
| lattice/importer/frontends/\_\_init\_\_.py | ae5cfcbb227207eb174e749310e4084b2c7a3d31e0157191c06ddd6211cba677 |
| lattice/gocheck/src/goextract_closure.go | b4ea9b8ffc4569ac2267e7201b536276f14f7cfde00ca5f7addc20167834d2be |
| lattice/gocheck/src/goextract_assemble.go | 69e4c0350c62e058754daefa004d0398ae366545d30cf0bf52d9b5afeb1f6a50 |
| lattice/gocheck/src/goextract_grpc.go (Spike I) | 3f181be7e7c50204bdadbc0c1a22a0988f4c68fd610817c9782011d540aa4354 |
| lattice/gocheck/src/*.go except routes.go, reach.go, owner.go, goextract_owner.go, goextract_raise.go (19 files, `cat` in `LC_ALL=C ls` order, which includes the three rows above) | 80d2acc274248fc1a780da2a1490caa64eed5dfe9c5c0b16cfca039561422614 |
| lattice/pycheck/derive.py | 63fe9d21936451c52785e71ec183f27c2be8b2aadbf6863d3bced4b0eb317040 |
| lattice/pycheck/checks.py | 14603fcc11aeecec0eaaef90417ae70c020026b45828b421ed259cc91886eea0 |
| dogfood/go_parity_probes.py | a9fe24c9c8a6ed60f069940aab7fac8582967dd34e8b4f89ca5f10eb680adc76 |
| dogfood/go_parity_mutants.py | f9f73a3cb760a6e38429d134e645fc69de9a92d48c29f9cd35123fafdcd2a9ac |
| dogfood/go_parity_probes2.py | f654c40efe1fe463bc1cb564299486c6a5cb9fa8ee41d221b642e55eacc552f5 |
| dogfood/go_parity_mutants2.py | 9d894fd8995757dfac08f548bbd7fb6a6170914014abe1a516446167b308dd12 |
| dogfood/go_parity_s3.py | cd32e3e6853f00ca9d7eff2ebc7256eaee7a1f8f01b2b6b011b50f50b0dfe32d |
| dogfood/go_parity_s3_mutants.py | 6bae418297d98dfbbd5a2e74065ae03a38ea202baf9e67ce72c60b5a4d029f8d |
| tests/spec/go_harness.py | 061d3823255b2a4ae63d9311f31c68b5adb6be0043fb9f30afe798e60a93ff03 |
| tests/spec/go_harness_rb.py | d69467ca7d66bc97ac760a0ca702e014ca3d7ef1608453a851527cbe10320c77 |
| tests/spec/gogin_harness.py | 1fb26ead614b7dcdfe522ab1d7952070e8006d88c42d39e5535e8d3f6b231caf |
| tests/spec/gotickets_harness.py | 00afd4ea4146cf58b5ec2bf330337695dc121112d0a77d0464a977c2d3d0dd41 |
| tests/spec/goapp/** (all files, `cat` in `LC_ALL=C sort`ed `find` order) | 60a66b5262a79f500034fd887a0ff6706d1080856ef6038f715c8cd39e8148d2 |
| tests/spec/goginapp/** (same rule) | a5a56b17e5acc6b3cde972513dfde0a469a409d7b00e5a7a6a1b4938ce130264 |
| tests/spec/gotickets/** (same rule) | f75d1e3f48571fe14b0848cd68b62570b04925360d651d339d4b8b1ef503e89f |

Also frozen, hashed by the runner into Amendment 2: every `tests/gocheck/fixtures/*` at 978b468, every existing test and golden, the
round-2 freeze files under `docs/results/data/2026-10-18-go-parity-gaps-round2/`, .NET, Studio, MCP and the mock. Before the
freeze the runner recomputes every value above at 978b468; a differing value is printed and recorded beside this one in
Amendment 2, never silently replaced.

**Byte-compat promise vs 978b468.**
- **Byte-identical:** the default extract (PB-X, 9 dirs); every Python rules import and `derive_policy`; every help text and MCP
  `tools/list`; S1's R and B reports; `test_process_runtime.py`; workflows and inventory on `goextract_workflow`; every Go inventory,
  implementation report, app map and impact output; the stack-neutral outputs of PB-N; the S2 and S3 policy extracts except K8's
  opt-in `raise` key.
- **Declared not byte-compatible:** layer A on the routes Amendment 2 lists (K1, K2); the S2/S3 imports (K3, K5; `inferences[]`
  gains `body_model`); `go_policy.json` only where `undeclared_tables` is non-empty; Streak's import (it now completes, K4);
  `gated_note()` (K8: GO805 leaves it); `lattice check` on Go only if Q6 says yes; Python inventories (new, K11).

A difference anywhere else is a finding and fails PB; it is never relabelled.

## 9. Risks, and what this cannot show

1. **K1/K2 may turn silence into false positives.** Routes that were skipped are now judged; a helper deeper than `HELPER_DEPTH`
   (3) or ownership in a way GO801 does not model gives a false GO801 (M-A0, H-PA0 guard it). Silence stays the answer for what
   cannot be read; K2 must not make an uninferable call readable.
2. **Middleware evidence can mask a defect.** `ownerEvidence` reads the handler's and the middleware's reach together (`owner.go`
   `all := fns + mw`); if Streak's `auth.Middleware` mentions `user_id` of an owned table, M-SM1 could stay silent for that reason.
   A miss is reported with the cause, not tuned.
3. **K3's synthesised model may differ from what Python's front ends emit** (field order, nested structs). Nested body structs are
   followed one level; deeper ones are listed as unread.
4. **K4 is a skip, not a parity fix.** A MySQL-only Go app gets contracts and owners but no verified rules until the server-dialect
   R port exists (Q4). Streak's import is honest, not complete.
5. **K5 can drop a real state column** whose code writes only one constant (the other states come from input). That column keeps
   the round-1 seed, as before E4.
6. **S4 is one app.** A pass shows the fixes reach one blind code base; a failure localises a missing idiom. The author may choose
   idioms G3 already handles; the brief cannot force variety without naming idioms (rule 2), so H-JUDGE is the honest measure.
7. **The round-2 runners cannot run on the G3 tree** (their self-checks pin round 2's library). Regression rows are re-expressed on
   the G3 runner by importing their row functions read-only (never their `main()`); where a function cannot be imported without its
   freeze check, its code is copied into the G3 runner and disclosed.
8. **Tier 0 is not fully blind** (section 1). **The held-out mutant writer sees the truth**, by design; it never runs Lattice.
9. **Cannot show:** R or B on MySQL (Q4); Go lifting; GO802 keying; the T2 capitalised-key gap (JM6); Python workflows (row 11) and
   Python impact T3 (row 15); .NET; gRPC.

## 10. Process rules (binding)

1. **Order.** (1) This file + Amendment 1 (rulings) committed. (2) **Tier 0**: T0 writes `go_parity_s3r.py`; Amendment 2a records its
   hash; the orchestrator runs it **once** on the 978b468 library; the verifier re-runs it once. (3) **Step 0** (B1): `goparity3/**`
   and its `truth.json` before any library code; the RC scripts for G-a..G-d4 and the K2 route list; **Amendment 2** records their
   hashes, outputs and any re-registered decision. (4) Library code and runners; dev runs on Arms 0, S, M2, J (dev data) allowed and
   disclosed. (5) **Runner completeness before the freeze:** `go_parity_probes3.py --self-test` executes **every arm's code path**,
   Arm H included, against stand-ins (`dogfood/g3_standin/`: S2 and S3 with truths in the S4 schema) and fails if any row returns
   NOT RUN while its prerequisites are present (no hard-coded NOT RUN anywhere: RM1). (6) **Amendment 3**: sha256 of every changed
   and created file, both runners, the stand-ins, `HARNESS_API.md` and the author brief; committed **before** the author is
   dispatched. (7) Tier 4: author -> contract check -> W -> **Amendment 4** (S4 and the 16 patch hashes) -> one scored run of all arms
   -> V's independent re-run -> results.
2. **Self-hash checks are never disabled.** Every runner verifies every entry of its freeze file, its own entry included, before the
   first metric, and aborts on a mismatch. There is no flag to skip it; no entry may be excluded, shimmed or edited. (gRPC round 3
   precedent, handoff section 3.)
3. **Global run ledger.** One append-only file, `docs/results/data/2026-10-18-go-parity-round3/ledger.jsonl`, at a path fixed in
   every runner (not derived from `--out`). Each run appends `{ts, runner, runner_sha256, freeze_sha256, arm, kind:
   shakedown|scored|rerun|verify|mutants, out, lattice_file, status: started|crash_before_metric|completed}` at start and at end. A
   scored run refuses to start for an arm that already has a `completed` scored entry; a `rerun` is allowed once and only when the
   previous scored entry ended `crash_before_metric`, with byte-identical frozen files; `verify` is allowed once (V). The ledger's
   sha256 is in the results. The runner also records `lattice.__file__` and refuses to run if it is not under the worktree (the main
   venv must not import the main checkout's `lattice`).
4. **Re-runs.** One, only after a crash before any metric, frozen bytes unchanged. Any byte change after Amendment 3 makes every
   row `post_hoc`, reported beside the first value. No third run.
5. **NOT RUN.** A row is NOT RUN only when a named prerequisite is missing (no Go, no Docker for M-R1, no download); a crash or a
   failed edit on a real fixture counts as FAIL (round-2 Amendment 6 rule a). A NOT RUN is never a pass, and the gate is then incomplete.
6. **Blind vs post hoc.** Every prediction here is fixed now. A missed prediction is a finding. Truths (T-S2, T-S3, `goparity3`,
   S4's `truth.json`) and pass rules stay as written. Anything read after a scored number (a cause, a re-run with a fix) is labelled
   post hoc, goes in a separate section and a separate commit, with its deltas, and never moves a gated value.
7. **Pass.** Every gated row of its tier, the mutant gates (18/18 library; 3/3 S3; SM1-SM3; 12/12 re-applied; held-out >= 13/16 with
   its class gates), the runner mutants caught, and the byte-compat promise. Each tier reports its own verdict; G3 passes when Tiers
   0, 1, 4 and (if in) 2 and 3 pass.

```sh
WT=/Users/vivekgade/Documents/lattice-g3; cd "$WT"
export PYTHONDONTWRITEBYTECODE=1 UV_NO_SYNC=1 PYTHONPATH="$WT" PATH=/opt/homebrew/bin:$PATH   # go 1.26.2 (r)
PY=/Users/vivekgade/Documents/lattice/.venv/bin/python          # shared venv; PYTHONPATH makes it import this worktree
SCRATCH=<node scratchpad>/g3; mkdir -p "$SCRATCH/shims"         # recording docker/claude shims first on PATH
git status --short                                               # empty at start
git archive 978b468 | tar -x -C "$SCRATCH/base"                  # PB base
git -C /Users/vivekgade/Documents/streak archive build/streak | tar -x -C "$SCRATCH/streak"            # read-only source
git -C /Users/vivekgade/Documents/job-scheduler-go archive 00dd1de | tar -x -C "$SCRATCH/jobsched"    # read-only source
# Tier 0 (on the 978b468 library, before any G3 library edit)
shasum -a 256 -c docs/results/data/2026-10-18-go-parity-gaps-round2/freeze.sha256
PATH="$SCRATCH/shims:$PATH" $PY dogfood/go_parity_s3r.py --base "$SCRATCH/base" --freeze "$SCRATCH/t0.sha256" \
  --out docs/results/data/2026-10-18-go-parity-round3/t0
# Tiers 1-2 (dev, then frozen)
$PY dogfood/go_parity_probes3.py --self-test --base "$SCRATCH/base"
PATH="$SCRATCH/shims:$PATH" $PY dogfood/go_parity_probes3.py --arm 0,S,M,J,P,H,X --base "$SCRATCH/base" \
  --streak "$SCRATCH/streak" --jobsched "$SCRATCH/jobsched" --freeze "$SCRATCH/g3.sha256" --runs 10 \
  --out docs/results/data/2026-10-18-go-parity-round3/scored
$PY dogfood/go_parity_mutants3.py --base "$SCRATCH/base" --scratch "$SCRATCH/m" --freeze "$SCRATCH/g3.sha256"
# M-R1 only: Docker, a python with lattice[db] (PyMySQL), LATTICE_CONTAINER_OWNER=w-g3, LATTICE_MYSQL_URL unset
$PY -m pytest tests/importer tests/pycheck tests/gocheck tests/spec tests/mcp tests/studio -q -rsf \
  --continue-on-collection-errors -p no:cacheprovider
$PY -m ruff check <changed .py>; $PY -m ruff format --check <created .py>; gofmt -l lattice/gocheck/src tests/gocheck/fixtures/goparity3
```

## 11. Questions for the orchestrator

1. **Q1 Where to build.** B1, B2, B3 each in their own worktree from `np-go-round3` (this file's commit), merged by the orchestrator
   before the freeze? Or one worktree with B1 then B2 in sequence (fewer merges, no parallelism)?
2. **Q2 Tier 3 (Python inventory, K11).** In this round (one more node, about 450 lines, two unfrozen Python-shared files), or split
   into its own pre-registered round after G3 merges? Recommended: split; G3 then gates Tiers 0, 1, 2, 4 only.
3. **Q3 Go lifting (J0 row 2).** Confirm it stays an owner decision outside G3.
4. **Q4 Server-dialect layer R/B** (whole-table diffs through the database adapter, all stacks). K4 skips honestly; should a separate
   node pre-register the port, and is a MySQL seed path for the importer then wanted?
5. **Q5 Bare harness module names.** Fix the library (`harness_factory` and `_runtime` also try the import root, <= 12 lines), or keep
   K7 runner-only with G-c a finding? Default here: runner-only, and the S4 brief requires the dotted form.
6. **Q6 K9.** Unfreeze `check.py` (<= 10 lines) so `lattice check` on Go defaults to `go_spec_policy` (asked twice before; Arm M found
   equal findings on Streak)? Default here: no; P2-DEF is then reported, not gated.
7. **Q7 Tier 0 timing.** Run the S3 re-score before any G3 library edit (default; it scores round 2's library), or fold it into the G3
   scored run (scoring the G3 library instead)? Both are reported if run; only one may be the scored value for PI4-3/PR3-3.
8. **Q8 Held-out author and mutant writer models.** Sonnet through the `claude` CLI (default), or opus? Same isolation and transcript
   audit as Spike I round 4.
9. **Q9 M-R1 (Docker) and Arm J.** Confirm both run on the owner's machine in the scored run (Docker 29.4.0 and go 1.26.2 were present
   for Arm M, m), and that M-R1 may start one throwaway `mysql:8.4` container per run.
10. **Q10 job-scheduler's membership tenancy.** GO801 cannot judge an app whose spec declares no owner column (J-SM1/J-SM2 are reported
    only). Should a later round model "access through a membership table" in the policy, or is it out of the Go parity goal?
11. **Q11 Held-out gates.** Accept H-PI3 >= 0.50, H-JUDGE >= 0.75 and the held-out mutant gate >= 13/16 with class gates, or set
    them differently before the freeze?
12. **Q12 Merge.** G3 merges into `next-phases` only on the owner's yes; is a partial merge (passing tiers only) acceptable, as for J1?

## Amendment 1 (orchestrator rulings on section 11, 2026-10-18, before any code)
- **Q1:** B1, B2 and B3 (when B3 exists) each in their own worktree from `np-go-round3`; the orchestrator merges them before
  the freeze. At most 4 agents run in parallel.
- **Q2:** Tier 3 (K11, the Python inventory) is split into its own pre-registered round after G3 merges. G3 gates Tiers 0, 1, 2
  and 4 only; B3 is not built in this round.
- **Q3:** Go lifting (J0 row 2) stays an owner decision outside G3.
- **Q4:** the server-dialect layer R/B port is a separate node to be pre-registered later; no MySQL seed path now (K4 skips
  honestly).
- **Q5:** K7 stays runner-only; the S4 brief requires the dotted harness module form; G-c remains a reported finding.
- **Q6:** K9 not done: `check.py` stays frozen and P2-DEF is reported, not gated (the owner may reopen it).
- **Q7:** Tier 0 (the S3 re-score of PI4-3 and PR3-3) runs on the round-2 library BEFORE any G3 library edit, and that is the
  scored value; any later G3-library value is reported as a separate row.
- **Q8:** the held-out author and mutant writer are sonnet through the `claude` CLI, with the same isolation and transcript audit
  as Spike I round 4.
- **Q9:** confirmed: M-R1 and Arm J run on the owner's machine in the scored run, and M-R1 may start one throwaway `mysql:8.4`
  container per run (label `lattice.owner`, removed after).
- **Q10:** job-scheduler membership tenancy is out of the Go parity goal for G3 (J-SM1/J-SM2 are reported only); a later round may
  model it.
- **Q11:** accepted: H-PI3 >= 0.50, H-JUDGE >= 0.75, held-out mutant gate >= 13/16 with the class gates.
- **Q12:** G3 merges into `next-phases` after the orchestrator's independent re-run; a partial merge of the passing tiers is
  acceptable (as for J1), with the failed rows stated in the merge message and the results.

## Amendment 2 (Tier 1 build node's confirm/refute of section 1.2 and orchestrator rulings, 2026-10-18, before the freeze)
**Causes, read from the code before any library edit (the build node's table, accepted):** G-a confirmed with a second cause (the Go front end hands
each body field over as a scalar body param; K3 builds the body model; `R2 POST /tickets/{tid}/handoffs` stays unexercised because
the route creates a Handoff whose owner is unclear: two FKs to users); G-b confirmed but incomplete (`mac := hmac.New(...)` infers
`unknown`, and conversions to a type literal like `[]byte(sig)` were opaque: K2c added); G-c runner-side (done in Tier 0); G-d1 (1) and
(2) confirmed, (3) refuted (Streak's `r.Member` struct-field router means the middleware is never read and route keys lack `/v1`; further
causes: `d.Clock.Now(ctx)` interface calls, `body(r)` function-typed parameter of `NewPage`, `(*string)(nil)`, helper depth); G-d2, G-d3,
G-d4 confirmed. **Rulings:** (1) the registered pass rules that failed on the dev data stay failed and are reported as failed
(PS-PI3-3 share 0.597 < 0.60; M-SM1 strict key; M-SM2; M-SM3; M-I1's "review list lists 0 items" clause; PB-T for the declared test
change); re-registrations proposed by the build node (M-SM1/M-SM2 key without `/v1`, M-A1 count 25 bound routes, M-SM2..SM4 not
detectable within K1/K2, M-I1 drop the clause) are accepted as additional rows labelled post hoc, never in place of the registered
ones. (2) The declared update of `tests/importer/test_go_rules2.py` (the inferences list gains `body_model`: 8, not 7) is made
by the orchestrator. (3) K2 generalised to an outside-rooted receiver chain plus K2c is accepted, with the limit stated: any wrapper
that calls a method on the error returned by its own parameter stays silent (it matters for the held-out). (4) The fixture's
revision 2 (recorded before any probe ran; revision 1's hash is kept) is accepted. (5) LM16/LM17 and the Tier 2 rows wait for B2.
(6) The build node's `pkill -f` cleanup and one unintended unshimmed pytest run (throwaway containers, removed) are disclosed.
