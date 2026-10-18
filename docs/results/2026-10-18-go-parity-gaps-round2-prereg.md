# Go-behind-Python gaps, round 2 (Spike J, node J2): root causes of J1's seven failures, five library fixes, a seed that queues, and a new blind fixture: pre-registration

## 1. Status, and the exact gap

2026-10-18. **Written BEFORE any round-2 code. No model was called, no API key was used, nothing is committed or staged.** This
is the only file this step writes (`/home/user/lattice/docs/results/`, the `next-phases` checkout, HEAD `fc54d75`). J1 is
**not** merged into `next-phases`: its build is commit `193f7f6` on `go-parity-gaps-j1` (r, `git merge-base --is-ancestor`).
`fc54d75` differs from `be26a94` only by the Spike I round-4 pre-registration (r, `git diff --stat`). So every source file
outside J1's change set has the same bytes at `193f7f6` and at `fc54d75`.

Read while writing (read only): the round-1 pre-registration `2026-10-17-go-parity-gaps-prereg.md` with Amendment 1 (the
template for this file); the round-1 results `/home/user/wt/j1/docs/results/2026-10-18-go-parity-gaps-results.md` and its data
(`scored/s2_import_report.json`, `s2_import_candidate_spec.py`, `freeze.json`); the round-2 and round-4 precedents
(`2026-10-17-grpc-round2-prereg.md`, `2026-10-18-grpc-round4-prereg.md`). In J1's tree (read only) I read
`tests/spec/goginapp/**`, `lattice/importer/rules.py` (`Seeder`, lines 267-385), `suite.py` (`seed_plan`, `_seed_value`, lines
295-386), `emit_spec.py` (lines 680-700), `frontends/go_gin.py`, `go_rules.py`, `gocheck/derive.py`, and in `lattice/gocheck/src/`
the files `goextract.go` (the `policy` flag), `goextract_closure.go` (lines 940-975 and 1180-1230), `goextract_assemble.go`
(lines 445-510), `goextract_workflow.go` (header), `routes.go` (lines 130-160), `reach.go` and `owner.go` (lines 160-200).

Labels: **(m)** measured by J1 and verified independently (restated, not re-scored); **(r)** read from code or data while
writing this, or produced by a read-only `git show`/`grep`; everything else is a **prediction (p)**. Round-2 results go to
`docs/results/2026-10-18-go-parity-gaps-round2-results.md`, with data in `docs/results/data/2026-10-18-go-parity-gaps-round2/`.

**What round 2 may claim as blind.** S1 (`tests/spec/goapp`) and S2 (`tests/spec/goginapp`) were both run and their failures
read, so **every S1 and S2 number in round 2 is development data** (`dev`). They are still gated, as regressions and as
corrected predictions. The only blind rows are on **S3**, a new fixture whose truth is fixed in section 4 before anything
exists. Arm S stays Docker-free and is the primary gate. **Arm M (Streak a0679e9 on MySQL 8.4) is NOT RUN** and is reported
as NOT RUN in the results' first line.

### 1.1 Round 1, as it stands (m)

| item | value |
|---|---|
| gated rows (arm S) | **19 of 26 pass**. FAIL: PI3, PI5, PI6, PR3, PA1, PA3, PB-F |
| mutants | **14 of 16 killed**. JM5 and JM6 survive. JM8 and JM11 are killed by unit tests only |
| S1 layer R | 23 items, 16 verified (0.696), 0 refuted, 7 unexercised (all with reasons), 10 of 10 repeatable |
| S1 layer B | 32 cases, 0 findings with `rules=RULES`. **6 false findings with the default rule** (3 endpoints x 2 callers, case `malformed`). The CLI has no `--rules` |
| S2 import | 38 items: 19 verified (0.50), 4 refuted, 15 unexercised. 3 proposals, all invariants. 10 of 10 byte-identical |
| role name | the extractor calls the authenticated role `Customer` (`goextract_assemble.go:505`, r), not `Member` |

### 1.2 Root causes (one per failure; r unless marked)

Classes: **L** library bug or gap, fixed in this round; **X** fixture bug; **P** prediction or pass-rule error, re-registered
as a corrected prediction; **O** process; **F** finding only, not fixed here.

| # | failure | root cause | class | ruling |
|---|---|---|---|---|
| C1 | PI3: 3 refuted `R3 ... 404 Not found` items | Every path parameter is emitted as `Type: "Str"` (`goextract_assemble.go:455,490`). The closure follows `strconv.Atoi/ParseUint` for query values (`goextract_closure.go:955`) but not for `c.Param`. So the not_found probe sends a non-integer id, and the app answers `422 'bad id'` before any lookup | L | **fixed: E1** |
| C2 | PI3: `R3 GET /runs 400` refuted, with no correction | Two bugs. (a) `binding:"omitempty,min=1,max=50"` is read as `ge=1` (`goextract_closure.go:1206-1213` ignore `omitempty`), but Gin's validator skips the zero value, so `limit=0` returns 200. (b) `emit_spec.py:699-700` adds `ContractError(api.validation_status, when='invalid')`. For Gin, `validation_status` is 400, but this app refuses every bind error with 422. Either bug alone would refute the item | L | **fixed: E2 (a), E3 (b)** |
| C3 | PI3/PI5: 409 paths, R2 create and R2 cancel unexercised; no machine proposed | The Seeder fills a text column with no `choices` as `"<Title> NN"` (`suite.py:386`) and ignores `default`. Go columns never carry `choices`, so every seeded run has status `Status 01..08` and none is `queued`. The facts that would give the states already exist in the opt-in workflow extract (`status_facts`: each constant write to a string column, with its `from` states) and in `columns[].default`. The import does not read them | L (Go front-end gap; the Seeder is Python-shared and stays frozen) | **fixed: E4** (the new seed) |
| C4 | PA3 / JM5: GO801 silent | `r.Use(gin.Recovery())`. `FuncRef` of an outside-package call is nil, so `routes.go:148-150` marks the route `Unreadable` and `checkOwnership` skips it. Post hoc, without that one line, JM5 gives `GO801\|POST /runs/:rid/cancel\|Run` (m, PH2). `gin.New()` + `Use(gin.Logger(), gin.Recovery())` is the usual Gin idiom | L (over-conservative) | **fixed: E6** |
| C5 | PA3 / JM6: GO802 not new | (a) The imported spec declares no private column for `User`. `profile_gen` takes `private` from response models, and a Go extract has none. So `go_spec_policy` (the spec wins for a declared table) drops the derived `[password, token]`, and layer R has no T2 item. (b) The mutant cannot be seen by layer A on S2 by construction. T-S2's login returns `access_token: u.Token`, so the original already has `GO802\|POST /login\|User.token`. A finding is keyed by route and column, so the mutant's leak has the baseline's key (m, PH4) | (a) L; (b) X (mutant design) | (a) **fixed: E5**; (b) JM6 **re-registered** onto layer R's T2 item (section 6). The A-side analogue moves to S3 (JM32) |
| C6 | PI6: `review list` shows `... 422 bad id #2` | A consequence of C1. Each wrong correction restates an item that already exists (`R3 ... 422 bad id`), and `review.py` disambiguates the collision with ` #2`. The pass rule ignored corrections | P (+ C1) | **re-registered** (PI6 rule maps through `corrections[]`). The ` #k` id is finding F10, not fixed (`review.py` is frozen and Python-shared) |
| C7 | PR3: 6 ids differ | The same cause: a corrected spec cannot carry the import's pre-correction ids | P (+ C1) | **re-registered** (PR3 compares after the correction map) |
| C8 | PA1: `users_table` = `User` | D3 said "the extract's auth users table", and the checker compares struct names (`owner.go:153`). The prediction wrote the SQL name | P | **re-registered**: `User` |
| C9 | PB-F: 8 of 26 hashes differ | The pre-registration took its baseline at `5125da1`. J1 was built on `be26a94`. No J1 edit touched a frozen file | O | **re-taken at `193f7f6`** (section 8). Recorded in Amendment 2 before any scoring |
| C10 | B on S1 needs `rules=RULES` | The default `ValidationEnvelope` is FastAPI-shaped (`{"detail": [{"loc"...}]}`). goapp answers `{"detail": "bad id"}`. The CLI has no way to pass rules | L (CLI gap) | **fixed: E7** (`--rules`). The default rule is unchanged (F2 stays a finding) |
| C11 | role `Customer` vs `Member` | The extractor's fixed name for the one authenticated role. Renaming it would change every Go extract and Streak | P | **re-registered**: `Customer`. F8 is kept as a finding |
| C12 | JM8 and JM11 killed only by unit tests | No probe had two owner candidates, and PB-P's Python fixture has no `studio.toml` | P (probe design) | **new probes**: PI2-3 (S3 has a two-candidate table) and PB-P2 (a decoy `studio.toml`) |
| C13 | PB1 32 cases (p 6-30); PX import 9.4 s (p 15-90) | Range errors. Gin was already in the module cache (premise 6 was stale) | P | ranges re-registered from (m) |
| C14 | F1: R/B ignore `runtime.auth.login` | Ruled a finding in round-1 Amendment 1 (Q5) | F | finding only (**Q5** asks again) |
| C15 | GO802 keyed by route and column | Fixing it changes every Go baseline key (Streak) | F | finding only |
| C16 | GO805 | No guard-to-raise link in the extract (round-1 premise 5; F12 pairing 2 of 3 and 1 of 3) | F | stays gated, text byte-identical |
| C17 | Amendment 1's "J1 is pre-registered only" vs. the build | Dispatch and owner request overrode it, disclosed as round-1 deviation 1 | O | Round 2 builds only after this file's Amendment 1 says so |

### 1.3 Scope (binding)

In scope: E1-E7 below; the new fixture S3 and the static fixtures `goparity2` and `goparity2mixed`; the corrected predictions
for S1 and S2; and a new probe runner, a mutant runner and tests. **Out of scope:** rank 1 (Go lifting), the server-dialect B/R
skip, a Python inventory, .NET, GO805, F1, F2's default rule, GO802 keying, renaming `Customer`, `review.py`, every
`goextract_grpc.go`/gRPC file (Spike I round 4 owns them), Streak, Docker and arm M.

## 2. Decisions (fixed before coding)

**E1 Path-parameter parse (opt-in fact; Go import only).**
- Under the existing `{"policy": true}` request (the `g.policy` flag, so `goextract.go` is not edited), each route param gains
  `"parse": "Int"` exactly when, in the route's closure (the same walk that builds `params`):
  1. `strconv.Atoi`, `ParseInt` or `ParseUint` is applied to `c.Param("<name>")`, directly or through one local string;
  2. the error result is bound to a name, not `_`;
  3. the error branch returns a constant 4xx refusal.
- In any other case the key is absent, including when the error is ignored. The default extract is unchanged. It cannot
  carry the fact, because 5 of the 7 PB-X dirs parse `c.Param` (r, grep) and their bytes would change.
- `InferringFrontEnd` retypes those `RouteParam`s to `Int` in memory. Each one is recorded in `inferences[]` as
  `{"rule": "path_parse", "route", "param", "evidence": ["code:<file>:<line>"]}`.

**E2 `omitempty` bounds (default extract; a correctness fix).**
- In the binding/validate tag reader (`goextract_closure.go`), take a tag with `omitempty` and a positive lower bound `L`
  (`min`/`gte` = L, `gt` = L-1, or `min` on a string length):
  - **L == 1:** the bound becomes 0 for numbers and null for string `min_length`. This is exact: the zero value is
    "unset" and passes.
  - **L > 1:** the lower bound becomes null, plus one route `unread` entry: `omitempty: the valid set {zero} ∪ [L, ...] is not
    an interval`.
- Upper bounds and every tag without `omitempty` are unchanged.
- Byte-compat: no `binding:`/`validate:` tag with `omitempty` exists in the 7 PB-X dirs or in goapp (r, grep). So their
  default bytes stay identical (p). goginapp's default extract changes in one value (`GET /runs limit ge 1 -> 0`, p).
  Streak is unknown (arm M).

**E3 Bind-refusal status (opt-in fact + Go import).**
- Under `{"policy": true}`, a `raises[]` entry in the error branch of `c.ShouldBind*` or `c.Bind*` (the calls the closure
  already names, `goextract_closure.go:692-694`) gains `"bind": true`.
- `InferringFrontEnd` handles two cases:
  - **The module has at least one bind raise and they all share one constant status `s`:** the Go `ApiProfile.validation_status`
    becomes `s` in memory. It is recorded as `{"rule": "bind_status", "status": s, "evidence": [...]}`.
  - **The statuses disagree:** nothing changes (Gin's 400 stays), and one entry `{"rule": "bind_status", "unclear": true,
    "statuses": [...]}` is recorded.
- `emit_spec.py` stays frozen. On S2 it then emits `ContractError(422, when='invalid')` (p).

**E4 State choices from `status_facts` (the new seed; Go import only).**
- `InferringFrontEnd` also runs the existing opt-in workflow extract (`go_gin.py` `workflow=True`, read-only reuse).
- A string column with no `choices` and at least one `status_facts` entry gets these `choices`: its `columns[].default` first
  (when it has one), then the other constant values written to it or named as `from` states, sorted and without repeats. Only
  facts whose `(table, column)` matches count.
- It is recorded as `{"rule": "status_facts", "table", "column", "choices", "evidence"}`.
- The Seeder (`suite.py`, `rules.py:Seeder`) is **not edited**. It already cycles `choices[i % n]` (`suite.py:368-369`).
- S2 prediction: `runs.status` choices `("queued", "cancelled")`, so half the seeded runs are queued (p). Q3 offers the
  fixture-side alternative.

**E5 Private columns from `json:"-"` (Go import only).**
- `private(T)` = the columns of `T` whose policy-extract `json` is `"-"`, minus the primary key. Names are never used.
- The columns go into the generated profile's `api.tables[T].private` before `candidate_spec.py` is written:
  - through a `RepoModel` input that `profile_gen` already reads, if one exists (the builder names it in Amendment 2, before
    library code);
  - otherwise in `rules.py`'s Go branch after `generate_profile` (additive).
- It is recorded as `{"rule": "json_dash", "table", "columns"}`.
- Then `go_spec_policy` keeps them, and layer R gets T2 items.

**E6 Owner-neutral Gin library middleware (layer A, `routes.go`).**
- Some middleware expressions are skipped: they are neither appended to `Middleware` nor mark the route `Unreadable`. This
  holds in `Use`, in a group's arguments, and in a route's leading handlers. The expressions skipped are calls to these
  functions of the package `github.com/gin-gonic/gin`, and only these: `Recovery`, `RecoveryWithWriter`, `CustomRecovery`,
  `CustomRecoveryWithWriter`, `Logger`, `LoggerWithWriter`, `LoggerWithFormatter`, `LoggerWithConfig`.
- Every other unresolved middleware keeps today's behaviour, including one from another module. Silence stays the
  answer to the unknown.
- No fixture in `tests/**` or `lattice/studio/**` calls `Use(gin.…)` except goginapp (r, grep). So layer A output is
  byte-identical on all of them (p).

**E7 `--rules MODULE:ATTR` on `python -m lattice.spec check` (`__main__.py` only).**
- It imports the attribute (a sequence of rules) and passes it as `rules=`.
- Without the flag, behaviour and output are identical. `--help` gains exactly that one option. The other two `--help`
  outputs and MCP `tools/list` are unchanged.
- The default rule for Go is unchanged: F2 stays a finding.

**E8 Never (as round-1 D7).**
- No write into a fixture or target directory during a run (imports go to scratch).
- No model, no `claude`, no API key, no Docker (recording shims stay first on `PATH`).
- No edit to a frozen file.
- No `git add` and no commit by the builder.
- No edit to any J1 data directory. Errata go in the round-2 results.

## 3. Arms

- **Arm 0 (static, no app runs).** E1-E6 facts on `tests/gocheck/fixtures/goparity2/` (new) and `goparity2mixed/` (new); both
  are parse-only modules with no `go.sum`, as the existing gocheck fixtures. Also PA4 and PG805 as regressions.
- **Arm S1 (`tests/spec/goapp`, bytes frozen; dev).** R and B regressions, plus B through the CLI with `--rules`.
- **Arm S2 (`tests/spec/goginapp`, bytes frozen at `193f7f6`; dev).** The J1 rows again with corrected predictions. **S2 is
  not edited**: T-S2 stays its truth, and its known design limit (C5b) is stated, not fixed.
- **Arm S3 (`tests/spec/gotickets`, new; the blind arm).** Truth T-S3 in section 4. Same modules as goginapp (Gin v1.10.0,
  GORM, glebarez/sqlite, x/crypto; no new download, p). Harness `tests/spec/gotickets_harness.py` (the `gogin_harness.py`
  pattern: lazy `go build -mod=readonly` into the system temp dir; `make_app`, `RUNTIME`, `LOGIN`, `build_db`).
- If `go build` of S2 or S3 fails, that arm's rows are **NOT RUN** with the error. The gate is then incomplete, not passed.
- **Arm M: NOT RUN** (no Streak checkout, no Docker). It keeps round 1's M1-M3, and adds M4: whether Streak uses explicit
  `Use(gin.…)` and `omitempty` bounds, i.e. how far E2 and E6 change its outputs.

## 4. T-S3, the truth of `gotickets` (fixed now; the fixture is written from this, before any library code)

| element | truth |
|---|---|
| main | `r := gin.New(); r.Use(gin.LoggerWithWriter(io.Discard), gin.Recovery())`. GORM `AutoMigrate`. Env `PORT`, `DB_PATH`, `TICKETS_SECRET` (constant default) |
| routes (11) | `GET /health` Public; `POST /login` Public; `GET /queues` Public; `GET /me`; `POST /queues/:qid/tickets`; `GET /tickets`; `GET /tickets/:tid`; `POST /tickets/:tid/close`; `POST /tickets/:tid/reopen`; `POST /tickets/:tid/handoffs`; `GET /handoffs`. The last 8 are behind `RequireUser` (extractor role: `Customer`) |
| tables (4) | `users` (id uint pk, name size 64 unique, password_hash size 128 `json:"-"`); `queues` (id, name); `tickets` (id, queue_id uint index, reporter_id uint index, title size 80, status size 16 `default:open`); `handoffs` (id, ticket_id, from_user_id, to_user_id, all uint index). No GORM association tags |
| auth | login: body `{username, password}` (`binding:"required"`), `bcrypt.CompareHashAndPassword` or an equal plain value (the Seeder's convention, as S2). The answer is `{"access_token": "<id>.<hex hmac-sha256(id)>", "token_type": "bearer"}`, with no user struct and no stored token. `RequireUser`: bearer, split, verify the HMAC, `db.First(&u, id)`, else 401 `Not authenticated`; `c.Set("uid", u.ID)`. Handlers read `c.GetUint("uid")` |
| `GET /me` | 200, the caller's `models.User` struct |
| create ticket | `strconv.Atoi(c.Param("qid"))` err -> 400 `bad id`; missing queue -> 404 `Not found`; body `title binding:"required,max=80"` err -> 422 `invalid body`; `Where("queue_id = ? AND reporter_id = ? AND status = ?", qid, uid, "open").Count` > 0 -> 409 `already open`; `Create(&Ticket{QueueID, ReporterID: uid, Title, Status: "open"})` -> 201 |
| list tickets | query `page_size` `form:"page_size" binding:"omitempty,min=1,max=100"`; `status` `binding:"omitempty,oneof=open closed"`. A bind error -> 422 `invalid query`. Rows from `Where("reporter_id = ?", uid)` plus the status filter -> 200 |
| get / close / reopen | `strconv.Atoi(c.Param("tid"))` err -> 400 `bad id`; `Where("id = ? AND reporter_id = ?", tid, uid).First` err -> 404 `Not found`. Close: status != `open` -> 409 `not open`, else `Update("status", "closed")` -> 200. Reopen: status != `closed` -> 409 `not closed`, else `Update("status", "open")` -> 200 |
| handoff | 400 `bad id`; ticket scoped `Where("id = ? AND reporter_id = ?")` -> 404 `Not found`; body `to_user_id uint binding:"required,min=1"` err -> 422 `invalid body`; missing target user -> 404 `No such user`; `Create(&Handoff{TicketID, FromUserID: uid, ToUserID})` -> 201 |
| list handoffs | `Where("from_user_id = ? OR to_user_id = ?", uid, uid)` -> 200 |
| owners | `tickets.reporter_id`: 1 assign (create) and 6 compares (create's open check, list, get, close, reopen, handoff) = 7 evidence lines. `handoffs`: **unclear**, with candidates `from_user_id` (assign + compare) and `to_user_id` (compare). `queues` shared; `users` not owned |
| private | `User: [password_hash]` |
| machine | `tickets.status`: created `open`; `open -> closed`; `closed -> open` |
| bind status | every bind refusal is 422 |

The fixture follows this text. Any deviation the builder finds necessary is a finding in the results, and T-S3 is never
rewritten. `tests/spec/gotickets/truth.json` restates this table in machine form and is hashed in Amendment 2 together with
the fixture.

## 5. Probes (runner `dogfood/go_parity_probes2.py`; blind pass rules; all arms Docker-free)

### 5.1 Arm 0 (static)

| id | probe | prediction (p) | pass |
|---|---|---|---|
| P0-PARSE | E1 on `goparity2` (`/a/:id` Atoi with a 400 refusal; `/b/:id` Atoi with `_` error; `/c/:id` no parse) and on S2/S3 | `a` gets `parse: Int`; `b` and `c` have no key. S2: `cid`, `rid` x2 (3 params). S3: `qid`, `tid` x4 (5 params) | exact |
| P0-OMIT | E2 on `goparity2` (`omitempty,min=1,max=10`; `omitempty,min=3,max=10`; `min=1,max=10`) and S2 `limit`, S3 `page_size` | ge 0/le 10; ge null/le 10 + 1 unread; ge 1/le 10. S2 ge 0/le 50. S3 ge 0/le 100 | exact |
| P0-BIND | E3 on `goparity2` (all bind refusals 422), `goparity2mixed` (400 and 422), S2, S3 | 422; unchanged + `unclear` entry; 422; 422 | exact |
| P0-LIBMW | layer A on `goparity2`: an owned table, one unscoped route under `Use(gin.Recovery(), gin.LoggerWithWriter(...))`, one under `ext.Guard()` from an unresolvable import | GO801 on the first route; none on the second | exact |
| P0-CHOICES | E4 on S2 and S3 | `Run.status` (`queued`, `cancelled`); `Ticket.status` (`open`, `closed`); no other column | exact |
| P0-PRIV | E5 on S2, S3 and `tests/gocheck/fixtures/goextract` | `User: [password, token]`; `User: [password_hash]`; `User: [password_hash]` | exact |
| PA4 | as round 1 (dev) | owners `{Item: owner_id, Audit: actor}`; private `{User: [password_hash]}` | exact |
| PG805 | `gated_note()`, `GO_GATED`; informational pairing count on S2, S3 and goextract | byte-identical; S2 2 of 3 (m), goextract 1 of 3 (m), S3 reported | identical |

### 5.2 Arm S1 (dev; goapp bytes frozen)

| id | probe | prediction | pass |
|---|---|---|---|
| PR1 | layer R, `go_harness_rb` | 23 items, 16 verified, 0 refuted (m-based) | refuted **0**; share >= **0.50**; reasons |
| PR2 | JM1, JM2 | each refutes >= 1 item on its route | both |
| PR4 | R 10x | identical | 10/10 |
| PB1 | layer B, `rules=RULES` (Python API) | 28-40 cases; 0 findings | 0 findings; >= 6 cases |
| PB1-CLI | `python -m lattice.spec check --layers B --login ...:LOGIN --rules tests.spec.go_harness_rb:RULES`; and without `--rules` (reported only) | identical finding set to PB1 (0); without the flag: the 6 F2 findings (m) | the `--rules` run equals PB1 exactly |
| PB2 | `nocheck` / `nocancel` | 2 findings on each named endpoint (m) | >= 1 on each named endpoint |
| PB3 | B 10x | identical | 10/10 |

### 5.3 Arm S2 (dev; goginapp bytes frozen; corrected predictions)

| id | probe | prediction | pass |
|---|---|---|---|
| PI1 | import: framework, routes, contracts, tables | `gin`; 7; 7 (Public: health, login; the other 5 `Customer`); 3 | exact |
| PI2 | owners and `inferences[]` | `Run: user_id` with 4 evidence lines (1 assign, 3 compare). `inferences[]` has **7** entries: 1 `owner_uses`, 3 `path_parse`, 1 `bind_status` (422), 1 `status_facts`, 1 `json_dash`. E2 is an extract fix and records no inference | exact |
| PI3 | rule items on the seed | 38-44 items; verified share **0.62-0.82**; refuted 0-1; corrections 0 | share >= **0.60**; every refuted item has a correction or a baseline entry with its reason; every unexercised item has a reason |
| PI4 | the corrected spec on `variant2` | every seed-verified item still verified | >= **0.95** |
| PI5 | `proposals.json` | 2-6 proposals, including the machine `runs.status` queued -> cancelled | the machine is proposed; each proposal is pending |
| PI6 | `review list` | exit 0; ids equal the report's not-verified ids (no corrections, so literal) | exit 0; ids equal after mapping each `corrections[]` item to its `corrected` id, with review's ` #k` suffix for the k-th collision |
| PI7 | import 10x | identical after J1's normaliser (`cost.wall_s`, `rules.timing`; `report.md` left out) | 10/10 |
| PR3 | layer R with the imported `candidate_spec.py` | the same verdict map as the import's final verdicts | identical after the same correction map as PI6 |
| PR-T2 | T2 items of the import (E5) | `T2 User.password` and `T2 User.token` both **verified** (the login answer's `user` row lacks both keys) | both present; neither refuted |
| PA1 | `derive_go_policy` | tables 3; users_table **`User`**; owners `{Run: user_id}`; private `{User: [password, token]}`; admin []; machines {}; baseline = 1 key `GO802\|POST /login\|User.token` (m) | exact; the baseline equals a separate `run_gocheck` |
| PA2 | as round 1 | `Run` not owned; machine present; baseline empty | exact |
| PA3 | JM5, JM6 under `go_spec_policy` of S2's candidate spec | JM5: `GO801` on `POST /runs/:rid/cancel` `Run` (E6). JM6: **0 new findings** (C5b, stated in advance). Original: 0 outside the baseline | JM5's code and route exact; JM6 reported; original 0 |

Corrected reasoning for PI3: C1 moves 3 items to verified, and C2 moves the invalid item to verified. C3 makes `409 already
live`, `409 already final`, R2 create and R2 cancel reachable. E5 adds 2 T2 items. The `422 bad id` (`other`), `500`, and
`T5 (id)` items stay unexercised by construction.

### 5.4 Arm S3 (blind; scored once after Amendment 3)

| id | probe | prediction | pass |
|---|---|---|---|
| PI1-3 | import | `gin`; 11 routes; 11 contracts; Public = health, login, `GET /queues`; the other 8 `Customer`; 4 tables | exact vs T-S3 (with `Customer`) |
| PI2-3 | owners and `inferences[]` | `Ticket: reporter_id` with 7 evidence lines (1 assign, 6 compare); `Handoff` **unclear**, naming both `from_user_id` and `to_user_id`; `Queue` shared; `User` not owned. Inferences: 2 `owner_uses` (1 unclear), 5 `path_parse`, 1 `bind_status` (422), 1 `status_facts`, 1 `json_dash` = **10** | exact |
| PI3-3 | rule items on the seed | 50-80 items; verified share **0.60-0.85**; refuted 0-3 | share >= **0.60**; every refuted item has a correction or a baseline entry with its reason; every unexercised item has a reason |
| PI4-3 | the corrected spec on `variant2` | every seed-verified item still verified | >= **0.95** |
| PI5-3 | proposals | 2-8; the machine `tickets.status` with `open -> closed` and `closed -> open` | `open -> closed` is proposed; each proposal is pending |
| PI6-3 | `review list` | exit 0 | the PI6 rule |
| PI7-3 | import 10x | identical | 10/10 |
| PR3-3 | R with the imported spec | the same verdict map | the PR3 rule |
| PA1-3 | `derive_go_policy` | tables 4; users_table `User`; owners `{Ticket: reporter_id}` (Handoff not owned); private `{User: [password_hash]}`; admin []; machines {}; baseline **0** keys | exact; the baseline equals a separate run |
| PA3-3 | JM31, JM32 under `go_spec_policy` of S3's candidate spec | JM31: `GO801` on `POST /tickets/:tid/close` `Ticket`; JM32: `GO802` on `GET /me` `User.password_hash`; original: 0 outside the baseline | exact codes and routes; original 0 |

### 5.5 Cross-arm

| id | probe | prediction | pass |
|---|---|---|---|
| PW | snapshot (sha256, mode, mtime, listing with hidden files) of goapp, goginapp, gotickets, `tests/gocheck/fixtures/**`, `tests/spec/fixtures/implementation/**`; recording `docker`/`claude` shims | 0 changed; 0 calls | all 0 |
| PX | S3 import (binary prebuilt; the cold build is measured separately); R on S1; B on S1 | 8-40 s; 2-10 s; 2-10 s | <= 180; <= 30; <= 60 |
| PB-F | section 8's frozen table, hashed at `193f7f6` | all identical | all |
| PB-X | default `gocheck extract` on the 7 round-1 dirs and goapp vs `193f7f6` | 8 of 8 identical | all |
| PB-G | workflows static run and inventory on `goextract_workflow`; layer A findings/notes on the implementation app and on every `tests/gocheck/fixtures/*` with a policy test; `test_process_runtime.py` reports | identical | all |
| PB-P | Python rules imports (normaliser of J1 deviation 8); no `inferences`, no `go_policy.json`; `derive_policy` | identical | all |
| PB-P2 | the multi-vendor Python fixture copied to scratch with a decoy `studio.toml [app] app` | `report.json` `app_factory` is the Python `make_app`, as without the decoy | identical |
| PB-H | `lattice --help`, `python -m lattice.importer --help`, MCP `tools/list` identical; `python -m lattice.spec check --help` differs by exactly the `--rules` option | as stated | as stated |
| PB-T | `pytest tests/importer tests/pycheck tests/gocheck tests/spec tests/mcp tests/studio -q -rsf --continue-on-collection-errors` | the same failing, skipped and error ids as `193f7f6` (7 / 37 / 101 in round 1, m), plus the new tests passing | same ids |
| PB-D | `dogfood/studio/check.sh` (venv python, J1 deviation 10) | passes, same step count | pass, or NOT RUN with the reason (the gate is then incomplete) |

**Probe count: 47 gated rows** (Arm 0: 8, S1: 7, S2: 12, S3: 10, cross: 10).

**Why these thresholds.** S2 and S3 keep round 1's 0.60 and 0.95. They were not lowered after the failures. S1 keeps 0.50
and refuted 0. Unexercised-by-construction items (`T5 (id)`, `500`, the `422 bad id` items that have no example) bound the
share from above. Refuted 0-3 on S3 allows the extractor's unknowns on a new fixture, and each refuted item still needs a
correction or a baseline entry.

## 6. Mutants (`dogfood/go_parity_mutants2.py`, scratch copies; the control passes first; predicted 35/35)

A mutant is killed when a gated probe that passes on the control flips, or when a named unit test flips. Each is disclosed.

| id | mutant | predicted killer |
|---|---|---|
| JM17 | E6 off: Gin library middleware marks the route unreadable (round-1 behaviour) | P0-LIBMW; PA3 (JM5); PA3-3 |
| JM18 | E6 too broad: any outside-package middleware is treated as readable | P0-LIBMW (the `ext.Guard()` route gains GO801) |
| JM19 | E1 off: no path retyping | P0-PARSE; PI3 (404 items refuted) |
| JM20 | E1 ignores condition 2: a parse with a `_` error retypes too | P0-PARSE (`/b/:id`) |
| JM21 | E1's `parse` key leaks into the default extract | PB-X (5 of 8 dirs differ) |
| JM22 | E2 off: `omitempty` ignored | P0-OMIT; PI3 (the invalid item refuted) |
| JM23 | E2: `omitempty,min=3` mapped to ge 0 | P0-OMIT (case 2) |
| JM24 | E3 off: `validation_status` stays 400 | P0-BIND; PI3 |
| JM25 | E3 on a mixed module: the first status wins | P0-BIND (`goparity2mixed`) |
| JM26 | E4 off: no choices (`Status 01..`) | P0-CHOICES; PI5; PI5-3 |
| JM27 | E4 uses only the `from` states (S2: `queued` only) | P0-CHOICES |
| JM28 | E5 off: no private columns in the profile | P0-PRIV; PR-T2 |
| JM29 | E5 also takes columns with a json name (every string column) | P0-PRIV |
| JM30 | E7: `--rules` parsed but not passed | PB1-CLI |
| JM31 | gotickets copy: close drops `AND reporter_id = ?` | PA3-3 (GO801); R2 close refuted (reported) |
| JM32 | gotickets copy: `password_hash` loses `json:"-"` | PA3-3 (GO802 on `GET /me`); `T2 User.password_hash` refuted |
| JM33 | gotickets copy: get answers 403 for another caller's ticket | R on S3: the 404 not_found/not_owned item refuted |
| JM34 | gotickets copy: reopen accepts an open ticket (no 409) | R on S3: `R3 POST /tickets/{tid}/reopen 409 not closed` refuted |
| JM35 | an E1-E5 inference entry written for a Python import | PB-P (`inferences` absent) |

**Re-applied: JM1-JM16 of round 1**, with the same edits and these re-registered detectors:
- **JM5:** PA3 GO801 (E6), and R `R2 POST /runs/{rid}/cancel` refuted (E4 queues another caller's run).
- **JM6:** `PR-T2` on the mutant copy (`T2 User.token` refuted). Its PA3 side is predicted **not** to flip (C5b), and that is
  disclosed.
- **JM8:** PI2-3 (Handoff unclear), plus the unit test.
- **JM11:** PB-P2, plus the unit test.

A re-applied mutant that no longer applies textually is re-expressed and disclosed. **Mutant count: 19 new + 16 re-applied
= 35.**

## 7. Line budgets (`git diff --numstat` insertions + deletions vs `193f7f6`)

| file | sha256 at `193f7f6` (r) | what | budget |
|---|---|---|---|
| `lattice/importer/go_rules.py` (140 lines) | `71839e6b6850eea315820ea741fe4c4cef54ab501d395102dcfdefa29598a05a` | E1, E3, E4, E5 (in-memory model and profile), their inference entries | **<= 220** |
| `lattice/importer/rules.py` | `0f62f835b2edb1cdc6121a7320cfb8c688a934a62835d1f43b9985f2fc87dacd` | the E5 fallback in the Go branch only | **<= 25** |
| `lattice/gocheck/derive.py` | `e19fde637ae85ccdce787299ffe502bd8c875743a595eac381a1d8fe856e8096` | the wrong `spec_policy` docstring (J1 deviation 7) | **<= 6** |
| `lattice/gocheck/src/goextract_closure.go` | `ed95d0fb9d93b6f9ae903f298340bed4877c6a9f122817e4fe6cff7f04361a4e` | E2; the E1 and E3 facts under `g.policy` | **<= 80** |
| `lattice/gocheck/src/goextract_assemble.go` | `8061f875ad063d07e45b02380f9c974c6f00075fa4db520b49f6323cba5e6607` | emits `params[].parse` and `raises[].bind` under `g.policy` | **<= 30** |
| `lattice/gocheck/src/routes.go` | `005614c24c6ff54d976ef09b2d9b2bda4177a404356c37a3d297dffe43722050` | E6 | **<= 30** |
| `lattice/spec/__main__.py` | `94542f34058145783670ca8cf95e83e6016bcc8aa03688b9af259d28af141c24` | E7 | **<= 15** |
| J1's tests (`test_go_rules.py`, `test_go_derive.py`, `test_go_layers_rb.py`) | J1 freeze | only assertions of a round-1 behaviour that an E-decision changes, each listed with its decision | **<= 40 total** |

**Creates:** `tests/spec/gotickets/**` (with `truth.json`), `tests/spec/gotickets_harness.py`,
`tests/gocheck/fixtures/{goparity2,goparity2mixed}/**` (with `truth.json`), `tests/importer/test_go_rules2.py`,
`tests/gocheck/test_go_derive2.py`, `tests/spec/test_go_layers2.py`, `dogfood/go_parity_probes2.py`,
`dogfood/go_parity_mutants2.py`, the round-2 results and data. This file changes only by appended Amendments.

## 8. Frozen files and the byte-compat promise

**Frozen (empty diff vs `193f7f6`; the runner checks every hash against `git archive 193f7f6` before any metric; values r
from `git show 193f7f6:<path> | sha256sum`).**

| file | sha256 at `193f7f6` |
|---|---|
| lattice/spec/check.py | 1c1e84e278e8c7397a40118ffa86476a9cfde93afd76d52cbbcffc81ef887691 |
| lattice/spec/probes.py | 7f4fca836922179fcf68806ee7f36713b7d453914f3d39ace7fac9ce495b86e6 |
| lattice/spec/process.py | 047be55e9f421700efab2441525121c8387167fb53edcd5991ebdc4592cdebf1 |
| lattice/spec/implementation.py | 89bc16f4679a77f34505fafbca4d33a4a021968aec93407e272a852b0fc319b8 |
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
| lattice/importer/inventory.py | 55849494b1ece51ed04a84e34f87da3f65c4fcc02b9523ac9269009e754f2e3b |
| lattice/importer/frontends/go_gin.py | 9f24e9bb5ac8b442cb92cc47ce1b66424b082d728f48e56579783c1bf038a913 |
| lattice/importer/frontends/\_\_init\_\_.py | ae5cfcbb227207eb174e749310e4084b2c7a3d31e0157191c06ddd6211cba677 |
| lattice/gocheck/\_\_init\_\_.py | d3cdb84ddd40c7b06ca0f8bf833fdc91234df6b7c1cc39ece3ab060bed0bdb5b |
| lattice/gocheck/src/goextract.go | 598bd04a5224294d56bb5c82f9cf18a4e9043d9961ad025e8c260843789840e5 |
| lattice/gocheck/src/goextract_tables.go | 9317c70bf200eb5bb719ffc11ef4d550e6f25ab464996c59321ff0b989381984 |
| lattice/gocheck/src/goextract_workflow.go | 3eb32761adfb313dc4ced351f62ad85aeb98110c86c43257ef0e212668385a42 |
| lattice/gocheck/src/goextract_grpc.go (Spike I round 4 owns it) | b2eda6fd8264445a172685ccb40c2bc1559c0effd43e5342743f83eef1a9315e |
| lattice/gocheck/src/*.go except goextract.go, goextract_tables.go, goextract_closure.go, goextract_assemble.go, routes.go (18 files, `cat` in `LC_ALL=C ls` order, which includes the three rows above) | 97c86f08b2b65a2d7c09e900d25accf440135fde740be3260c4887cd629c0939 |
| lattice/pycheck/derive.py | 63fe9d21936451c52785e71ec183f27c2be8b2aadbf6863d3bced4b0eb317040 |
| lattice/pycheck/checks.py | 14603fcc11aeecec0eaaef90417ae70c020026b45828b421ed259cc91886eea0 |
| tests/spec/goapp/main.go | cb90b6fbfc9a699b39a58d1c0811799710e528d93baaa8f2c435aa2ec1194d66 |
| tests/spec/go_harness.py | 061d3823255b2a4ae63d9311f31c68b5adb6be0043fb9f30afe798e60a93ff03 |
| tests/spec/go_harness_rb.py | d69467ca7d66bc97ac760a0ca702e014ca3d7ef1608453a851527cbe10320c77 |
| tests/spec/gogin_harness.py | 1fb26ead614b7dcdfe522ab1d7952070e8006d88c42d39e5535e8d3f6b231caf |
| tests/spec/goginapp/main.go | 42de032be4a9b5ed041a2835f4f4759bf1053b57ec83977ab454af1deb92e6d6 |
| tests/spec/goginapp/api/api.go | f32579d2f72c8e258c5b01ce85ff042e0aaa4061817f74710878f80545c504fb |
| tests/spec/goginapp/models/models.go | e74ce71c0c23dc91405d2642c9f1e3d198d94afae2a3a778b2623caea6e31624 |
| tests/spec/test_process_runtime.py | fd2bfeab04a9d2e92a03027d441aed0ac882c34136625df759119bcd8d16cafa |
| dogfood/go_parity_probes.py (round-1 runner) | a9fe24c9c8a6ed60f069940aab7fac8582967dd34e8b4f89ca5f10eb680adc76 |
| dogfood/go_parity_mutants.py (round-1 runner) | f9f73a3cb760a6e38429d134e645fc69de9a92d48c29f9cd35123fafdcd2a9ac |

Also frozen, with no hash listed here (the runner records each one in Amendment 2): `goapp/go.{mod,sum}`,
`goginapp/{go.mod,go.sum,studio.toml}`, every `tests/gocheck/fixtures/*` that exists at `193f7f6`, every existing test and golden,
.NET, Studio, MCP and the mock. Before freezing, the runner recomputes every value in this table at `193f7f6`. A value that
differs from this table is printed, and Amendment 2 records it beside the value given here. It is never silently replaced.

**Byte-compat promise vs `193f7f6`.**
- **Byte-identical:**
  - the default extract on the 7 round-1 PB-X dirs and goapp;
  - every layer A output on every existing gocheck fixture and on the implementation app;
  - workflows and inventory on `goextract_workflow`;
  - every Python rules import and `derive_policy`;
  - `lattice --help`, the importer `--help`, MCP `tools/list`;
  - S1's R and B reports under the same invocation;
  - `test_process_runtime.py`.
- **Declared not byte-compatible:**
  - goginapp's default extract (E2, one value);
  - goginapp's policy extract (E1, E3 keys);
  - layer A on goginapp (E6: routes become readable; the original keeps 0 findings outside its 1-key baseline, p);
  - the S2 import (every E-decision; intended);
  - `python -m lattice.spec check --help` (E7: one option).

  A difference anywhere else is a finding. It is not relabelled.

## 9. Risks, and what this cannot show

1. **S3 is written by the build node from T-S3**, as S2 was. Its idioms were chosen knowing E1-E6. It is blind only in that
   its truth and predictions are fixed here before it exists. It is not an independent held-out (**Q2**).
2. **E4 is a convention:** default first, then the written states. A Go app whose states are not constant writes to a string
   column (a typed enum through a helper, a map) gets no choices and keeps the round-1 seed. That is honest, but it lowers
   coverage.
3. **E3 can be wrong for a module that binds in a helper with its own status.** It refuses on disagreement and never guesses.
4. **E6 is a fixed list.** A Gin app that wraps `gin.Recovery()` in its own function is still silent. That is intended
   (silence over guessing).
5. **C5b is not fixed:** GO802 keying hides a mutation on a route whose baseline already reads the column.
6. **The `OR` owner query in T-S3 (`GET /handoffs`) is the extractor's least-known idiom.** PI2-3 may miss
   `to_user_id`. That is a blind prediction, and a miss is a finding.
7. **Cannot show:** Streak or MySQL (arm M); B on imported Go apps (rank 1); GO805; F1; server dialects; .NET.

## 10. Process rules (binding)

1. **Order.**
   1. This file plus Amendment 1 (the rulings) is committed first.
   2. Step 0: the builder writes `gotickets/**`, `goparity2/**`, `goparity2mixed/**` and their `truth.json` **before any
      library code**. It may run `go build` and `go vet` of S3 only, plus one contract smoke (start the binary, `GET /health`
      200, one seeded login 200; pass/fail lines only, recorded). The runner's hash check at `193f7f6` also runs.
      **Amendment 2** (build node; hashes only) records the step-0 hashes, the runner's `193f7f6` table, the E5 path chosen, and
      the 7 + goapp default-extract hashes. It is committed before library code.
   3. Library code. Dev runs on Arm 0, S1 and S2 are allowed. **S3 is never imported, extracted, checked or probed** before the
      freeze, beyond step 0.
   4. **Amendment 3** records the sha256 of every changeable file, both new runners, and each harness and test file. It is
      committed **before the scored run**.
   5. The scored run, once over all arms.
   6. The orchestrator re-runs it independently once.
2. **Re-runs.**
   - One re-run is allowed, **only after a crash before any metric is computed**, and only with byte-identical frozen files.
   - Any byte change after Amendment 3 makes every row `post_hoc`, reported beside the first value.
   - There is no third run.
3. **Self-hash checks are never disabled.** The runner verifies every entry of Amendment 3, its own entry included, before the
   first metric, and aborts on a mismatch. No entry may be excluded, shimmed or skipped. (Round-3 gRPC precedent P-b.)
4. **Shakedowns** may run Arm 0, S1 and S2 before Amendment 3. A shakedown never touches S3. Every shakedown is disclosed with
   its data.
5. **A missed prediction is a finding.** T-S3, T-S2 and every pass rule stay as written. Fixes after scoring go in separate
   post-hoc commits, with their deltas reported.
6. **Pass:**
   - every gated row of section 5 on Arm 0, S1, S2 and S3 (47);
   - mutants 35/35 (by probes or named unit tests, disclosed);
   - the byte-compat promise.

   Arm M is NOT RUN, and that is not a pass.

```sh
cd <worktree>; export PYTHONDONTWRITEBYTECODE=1 GOTOOLCHAIN=auto UV_NO_SYNC=1
SCRATCH=<build node scratchpad>/j2; PY=.venv/bin/python; mkdir -p "$SCRATCH/shims"   # recording docker/claude shims first on PATH
git status --short                                                                   # empty at start
git archive 193f7f6 | tar -x -C "$SCRATCH/base"                                      # PB-F/PB-X/PB-G/PB-P/PB-T base
sha256sum tests/spec/gotickets/** tests/gocheck/fixtures/goparity2*/** > "$SCRATCH/freeze-fixtures2.sha256"   # step 0 -> Amendment 2
(cd tests/spec/gotickets && go build -o "$SCRATCH/gotickets" .)                     # failure => S3 NOT RUN
PATH="$SCRATCH/shims:$PATH" $PY dogfood/go_parity_probes2.py --arm 0,S1,S2 --base "$SCRATCH/base" --out "$SCRATCH/dev"   # dev only
sha256sum <section 7 files> dogfood/go_parity_probes2.py dogfood/go_parity_mutants2.py tests/spec/gotickets_harness.py > "$SCRATCH/freeze2.sha256"  # -> Amendment 3
PATH="$SCRATCH/shims:$PATH" $PY dogfood/go_parity_probes2.py --arm 0,S1,S2,S3 --base "$SCRATCH/base" --freeze "$SCRATCH/freeze2.sha256" --runs 10 --out docs/results/data/2026-10-18-go-parity-gaps-round2/scored
$PY dogfood/go_parity_mutants2.py --base "$SCRATCH/base" --scratch "$SCRATCH/m" $(seq -f JM%g 1 35)
$PY -m pytest tests/importer tests/pycheck tests/gocheck tests/spec tests/mcp tests/studio -q -rsf --continue-on-collection-errors
.venv/bin/ruff check <changed .py>; .venv/bin/ruff format --check <created .py>; gofmt -l lattice/gocheck/src tests/spec/gotickets tests/gocheck/fixtures/goparity2*
```

**File ownership.**
- The builder edits only section 7's files, within budget, and creates only section 7's "Creates".
- The builder never commits. The orchestrator stages explicit paths, never `git add -A` or `git add .`.

## 11. Questions for the orchestrator

- **Q1 Where to build.** J1 is on `go-parity-gaps-j1` (`193f7f6`) and is not merged into `next-phases`. Should round 2:
  - continue in `/home/user/wt/j1` on that branch, or
  - use a new worktree `/home/user/wt/j2` (branch `go-parity-gaps-j2` from `193f7f6`)?

  And should this file be committed on `next-phases` or on the J2 branch? Either way the baseline commit for every hash stays
  `193f7f6`.
- **Q2 S3 authorship.** Should the build node write S3 from T-S3 (the default; not independent, risk 1), or should a separate
  author write it, isolated from `lattice/**` and every `*go-parity*` document as in gRPC round 4? The second would make the S3
  rows a true held-out, at the cost of a brief and a contract check.
- **Q3 The new seed.** Accept E4 (a library fix: choices from `status_facts`; the Seeder is unedited)? The alternative is a
  fixture-side seed: a goginapp harness `insert_hook` that queues runs. That leaves the Go import's gap open for every Go app.
- **Q4 E7.** Accept unfreezing `lattice/spec/__main__.py` (<= 15 lines; one `--help` option) for `--rules`, or keep C10 as a
  finding with the Python API as the only path?
- **Q5 F1 (login fallback).** Keep round-1 Q5 (finding only, harnesses pass `LOGIN`), or fix `login = login or
  runtime.auth.login` in `check.py` now? That unfreezes `check.py`, and Python runtimes whose `auth.login` is set might change.
- **Q6 Opt-in vs default for E1 and E3.** Confirm that both facts live only in the `{"policy": true}` extract. E1 in the
  default extract would change 5 of the 7 PB-X dirs (r).
- **Q7 E2 in the default extract** (a correctness fix; 0 of the 7 dirs and goapp affected, r; Streak unknown). Accept, or move it
  to the opt-in extract too?
- **Q8 S2 rows.** S2 is dev data. Should its 12 rows stay **gated** (the default here, as corrected predictions and
  regressions), or be reported only, with the gate on Arm 0, S1, S3 and cross-arm?
- **Q9 JM6.** Accept re-registering its detector from PA3 to layer R's T2 item (C5b), with the A-side test moved to S3 (JM32)?
- **Q10 Concurrency with Spike I round 4** (`/home/user/wt/i4`). I4 freezes every `lattice/gocheck/**` file except
  `goextract_grpc.go` at `be26a94`. J2 edits `goextract_closure.go`, `goextract_assemble.go` and `routes.go`. Which merges
  first, and should I4's frozen list be read as "unchanged by I4" so that J1/J2's edits do not fail its PB check?
- **Q11 Arm M** (M1-M4): who runs it, on which machine, and is it required before J1 and J2 merge?
- **Q12 GO802 keying** (C15) and F10 (`review` ` #k` ids): confirm they stay findings only and are out of J2.

## Amendment 1 (orchestrator rulings, 2026-10-18; defaults, overturnable)

Q1: build in a new worktree `/home/user/wt/j2` (branch `go-parity-gaps-j2`, from `go-parity-gaps-j1` 193f7f6 plus `next-phases`); this file is committed on `next-phases` first. Q2: S3 (`gotickets`) is written by a separate isolated author dispatched only after the library freeze is committed (same isolation and transcript-audit rules as Spike I round 4); the build node does not write S3 and reports every S3 row NOT RUN until the author delivers. Q3: accepted (E4). Q4: accepted (unfreeze `__main__.py` for `--rules`). Q5: F1 stays a finding. Q6: confirmed. Q7: accepted. Q8: yes. Q9: accepted. Q10: merge order is Spike I round 4, then J1, then J2; conflicts in the frozen lists are resolved by the orchestrator at merge time and recorded; J2 builds on its own branch and never edits Spike I's files. Q11: Arm M waits for a session on the owner's computer (Streak, MySQL and Docker) and is not required before merging as a partial. Q12: confirmed out of scope. Process: Amendment 2 records fixture hashes before library code, Amendment 3 records the library freeze before the S3 author is dispatched; one scored run; one re-run only after a crash before any metric; self-hash checks are never disabled.

## Amendment 2 and 3 (hashes before the S3 author, 2026-10-18)

The dev-stage build is committed on `go-parity-gaps-j2` (b1f8e56). Fixture hashes (written before library code): `fixtures.sha256` sha256 = 2e6b65dbbe666a366fd02a2b2c66fe312619ae003ee0f95bf1a284bc8ec79893. Library, test and runner freeze: `freeze.sha256` sha256 = 800fc1f7131ca9969bef0496541e3ff9e7a232ab9aa5a4cc0d9f23d0322891f0 (21 entries, runner included; self-check passed). Dev-stage results: 37 of 47 gated rows pass, 0 fail, 10 S3 rows NOT RUN; 30 of 35 mutants killed, JM6 survives (its re-registered detector did not flip), JM31-JM34 NOT RUN; `goextract_closure.go` used 102 lines against an 80-line budget (BUDGET MISSED, recorded); PI3 share 0.6098 against a predicted 0.62-0.82 (gate 0.60 passes by 0.0098). The S3 author (a separate agent, not the builder) is dispatched only after this amendment is committed; it receives T-S3 (section 4), the S2 fixture as a structural reference and a complete harness API (including that `build_db(path)` must RETURN the path and that `RUNTIME` is a `process_runtime(...)`), works in a single scratch directory, and its tool calls are audited from the transcript afterwards; S3 rows are scored once, and a fixture deviation is a finding, never a rewrite of T-S3.

## Amendment 4 (S3 delivered, 2026-10-18)

The isolated S3 author (dispatched 2026-10-18T19:52:40Z, after the library freeze of Amendment 3 committed at af28044) delivered `gotickets` (11 routes, 4 tables, owner evidence for `tickets.reporter_id` at 7 lines, truth.json with 87 file:line references; its own throwaway client ran 76 checks, 0 failed). Transcript audit: 20 tool calls, 0 accesses outside its scratch directory (the pattern hits are its own stub `lattice` package inside that directory). Delivered `freeze.sha256` sha256 = 0a27192ddd13b9baf9b97f22603424db9e56a41a5218290a00fc332c635fc85d (the 9 files `tests/spec/gotickets/*` and `tests/spec/gotickets_harness.py`, copied byte-for-byte into the build tree). The author notes two behaviours that follow from T-S3 as written: `GET /tickets?page_size=0` answers 200 (omitempty skips the zero value) and the stored hash itself is accepted as a password. Those are findings, not changes to T-S3. S3 rows and mutants JM31-JM34 are scored ONCE from the frozen library and runner (freeze.sha256 of Amendment 3); one re-run only after a crash before any metric, with the runner's bytes unchanged; the self-hash check is never disabled.

## Amendment 5 (S3 runner gap, 2026-10-18)

Finding (orchestrator process defect, found by the S3 runner and confirmed by an independent re-run): the round-2 runners frozen in Amendment 3 (`go_parity_probes2.py`, `go_parity_mutants2.py`) hard-code NOT RUN for every S3 row and for JM31-JM34 (`s3_rows()` returns `not_run` unconditionally; `GOTICKETS_ABSENT`), because the S3 scoring code was not required to exist before the freeze. No S3 metric was computed and no scored run was consumed (0 scored runs across all out directories). Ruling: the frozen runners are NOT edited. Two NEW files, `dogfood/go_parity_s3.py` (the 10 S3 rows PI1-3 ... PA3-3, PI2-3, and the S3 sub-claims of P0-PARSE, P0-OMIT, P0-BIND, P0-CHOICES, P0-PRIV, PG805 and PX) and `dogfood/go_parity_s3_mutants.py` (JM31-JM34), are written by a build node that must NOT open `tests/spec/gotickets`, `tests/spec/gotickets_harness.py` or the author's scratch directory: it works only from the T-S3 table in section 4 above, the frozen library and the frozen runners' conventions, and may test its code on `goginapp` (S2). Their sha256 is recorded in Amendment 6 before the fixture is first scored. The new runners import the frozen ones read-only and re-check the Amendment 3 freeze at start; their own bytes are never disabled from the self-check. The S3 rows are then scored ONCE; one re-run only after a crash before any metric, without changing the new runners' bytes. The rows remain blind with respect to the library (frozen) and the fixture (separate author); they are NOT blind with respect to T-S3, which was public to the builder by design. Minor correction to Amendment 4: of its 9 hashed entries, 8 are delivered files and the ninth is the author's `FILES.txt` listing.

## Amendment 6 (S3 runner hashes recorded before the fixture is scored, 2026-10-18)

The S3 runners were written by a build node in a worktree without the fixture (branch `go-parity-gaps-j2-s3runner`, committed before the fixture's first use by them). Hashes: `dogfood/go_parity_s3.py` cd32e3e6853f00ca9d7eff2ebc7256eaee7a1f8f01b2b6b011b50f50b0dfe32d; `dogfood/go_parity_s3_mutants.py` 6bae418297d98dfbbd5a2e74065ae03a38ea202baf9e67ce72c60b5a4d029f8d; `tests/spec/test_go_parity_s3.py` 1f804b10ffbcc9aac54c970935677792940fd81ea64cd3884c6674d4abbd387a; `s3runner.sha256` 35524804d13e541a5f8cde1e625907f740ca383511465283692a77b4055a461b. The builder's 11 listed ambiguities are ruled as it read them, with two tally rules: (a) a row or mutant reported NOT RUN because the S3 import or an edit failed on the real fixture counts as a FAIL (or a surviving mutant) in the gate tally and is explained; only a missing prerequisite (no Go, no download) is a true NOT RUN; (b) PA3-3 requires the expected (code, key) among the mutant's new findings, as the frozen PA3 does. Scored once; one re-run only after a crash before any metric, without changing runner bytes.
