# Go (Gin + GORM) front end for the importer (Spike A): pre-registration

## 1. Status, and the exact gap

2026-10-15, branch `np-goextract` (from `next-phases` 4845ab5). **Written BEFORE any code.** Recon only: code reading;
Streak a0679e9 and 1d00ede archived to scratch; a scratch gocheck copy with a debug `init()` printing `findRoutes`; Streak's
`spec.py` steps listed in Python; a regex parse of `models.go`; aggregate `grep`/`wc` on the held-out checkouts (not read).
No LLM. **(m)** = measured, all else a **prediction (p)**. Results `2026-10-15-go-frontend-results.md`, data alongside.

**Premise check: the code contradicts three premises.**
1. **a0679e9 has 26 registrations, not 20, and implements every spec'd route (m).** 24 `/v1` + `GET /health` + `PUT
   /test/clock` (inside `if d.Config.TestClock`, `internal/httpapi/router.go:49-51`). Home, sync, nudges, dismiss *are*
   registered (`home/routes.go:18`, `sync/routes.go:17`, `nudges/routes.go:16-17`), so are the devices routes
   (`account/routes.go:23-24`). The spec's 26 endpoints (22 `ACCESS` actions + register, login, refresh + `/test/clock`)
   all have a 2xx step and match a registration: **spec-only at a0679e9 = 0 (m)**. The plan's state is Streak's own
   history: 840ca3f..1d00ede ("metrics: plan p2 level 4") have **20 registrations (m)** (18 `/v1`, not 17) and **6
   spec-only routes (m)**: home, sync, nudges, dismiss *and* `POST /v1/devices`, `DELETE /v1/devices/:id`. 1d00ede's
   `spec.py` differs from a0679e9's in 11 lines, no endpoint. The Studio rebuild `streak-b4-a93dcb` is at 9417351.
2. **10 GORM tables, not 8 (m).** `db.Migrate` auto-migrates `SchemaMigration` + the 8 of `db.Models()`
   (`db/db.go:111-118`); `clock.Migrate` adds `TestClockRow` (`test_clock`, spec `TestClock`). `schema_migrations` is
   not in the spec.
3. **`import_rules` cannot take a Go repo**: `find_factory(package)` imports a Python package, `lift_repo` lifts
   Python, the `Seeder` is SQLite (`rules.py:928-937`). The Go path is extract -> `generate_profile` -> `build_spec` (D13).

**Rulings requested.** R-A ground truth = a0679e9 (26 / 24 `/v1` / 0 spec-only) **plus** 1d00ede as the spec-only case
(20 / 18 / 6). R-B D13's candidate-spec path. R-C no foreign key is invented (D3), so generated owners are `unclear`.
R-D the lazy `_load_go` edit in `frontends/__init__.py` (D12). R-E held-out ground truth by a fresh agent (H).

**The gap (gocheck 4845ab5 on a0679e9, empty policy; ~0.01 s, identical over 3 runs and 2 directories; all (m)):**

| fact | today | why |
|---|---|---|
| registrations found (method + tail path) | 26/26 | `routes.go findRoutes` |
| full path exact (`/v1` read) | **2/26** (`/health`, `/test/clock`) | `routerPrefix` reads `CallExpr`/`Ident` only; `r.Member` (selector on a `Routes` parameter) gives `""` |
| distinct handlers of the 24 wrapped routes | **1** (`Handle.func1`) | `FuncRef`'s factory branch (`types.go:860-883`) answers the wrapper's literal; the 24 real handlers are orphan roots |
| middleware on any route | 0 | `authenticate` is a `NewRouter` parameter fed by `main` (`auth.Middleware(d)`); Member is a field of a `Routes{...}` literal |
| `/health`, `/test/clock` | Unreadable | `r.Use(gin.CustomRecovery(...))` is external |
| tables, machines; raise sites, constructors, Body fields | 0 without a spec; none | `Models(pol)`, `machine.go` read the policy; `bounds.go` reads tags/hand bounds on GO803 write paths only |

## 2. Decisions (fixed before coding)

**D1 Program.** Same binary, `package main`: new files `lattice/gocheck/src/goextract*.go`; `main.go` gains only a
dispatch (`gocheck extract`: `{"root"}` on stdin, the JSON below on stdout; no argument = today's path, byte for byte).
Every other existing `.go` file stays byte-identical: extensions are **new functions** over the shared `Load`, `Env`,
`Infer`, `FuncRef`, `gormTag`, `constString`, `reach` (editing `routerPrefix`/`FuncRef` would move GO801-GO808 findings).
The shim builds with the unchanged `build_gocheck()`. Exit 2 bad input, 3 internal error; a parse error is `unread`.

**D2 Output `lattice.goextract/1` and its mapping to `RepoModel`** (no `RepoModel` field is added:
`test_frontends.py:58` ties added fields to the goldens; what has no field stays in the sidecar JSON, kept on
`GoGinFrontEnd.last_extract` and written beside the model):

| key | content | RepoModel |
|---|---|---|
| `module`, `go`, `dialect` | go.mod module and `go` line; `gorm.io/driver/<x>` -> mysql/postgres/sqlite | `app_package`, `dialect`; `framework="gin"`, `orm="gorm"`, `database_url=None` |
| `tables[]` | struct, table, package, file:line, `source`, `migrated_at[]`, columns, `unique_together`, `checks` | `TableModel` (name = struct, module = import path); composite uniques also in `constraints` (kind `unique`) |
| `routes[]` | method, gin path, `{x}` path, file:line, `condition`, `router_chain[]`, handler {func, file, line, `wrappers[]`}, `middleware[]`, auth, params, raises, owner_uses, tables, `tx`, calls, `unread[]` | `Route` (name = handler, module = its import path, `view`, `dependencies=()`, `response_model=None`, `status_code` = the single 2xx constant else None, `guards`, `unread` = joined reasons or None) |
| `constructors[]`, `sentinels[]`, `error_writer`; `auth`; `models[]` | D6; D5; D7 | via `raises`; `AuthModel`; `DataModel` + `body_models` |
| `status_writes[]`, `excluded[]`, `unread[]` | D9, D10 | sidecar only; `excluded_routes` (framework); `Route.unread` |

**D3 Tables (no policy).** A struct is a table iff it is an argument of a reachable `AutoMigrate`/`Migrator().CreateTable`
(through `append`, `...` spreads and a module function returning a `[]any{&T{}...}` literal), has a `TableName()`, or is
a GORM chain's model (`Model(&T{})`, a `Create/First/Take/Find` destination). Name: `TableName()`'s literal (computed:
`unread`), else `plural(toDBName)`. Columns: `column:` tag else `toDBName`; `gorm.Model` expanded, embedded structs
flattened (`flatten`'s rules), `-` skipped. `FieldSpec`: `nullable` = the database's (no `not null`, not `primaryKey`;
pointer-ness ignored); `required` = not nullable and no `default`; `default` = tag text; `primary_key` = `primaryKey`,
else a field `ID` when none is tagged; `unique` = `unique` or a `uniqueIndex` whose name no other field shares (shared
-> `unique_together`); `index` = any `index`/`uniqueIndex`; `max_length` = `size:N` else `(var)char(N)` in `type:`;
`foreign_key` only from `foreignKey`/`references` association tags, **never from naming** (R-C); `type`: string Str,
ints Int, floats Float, bool Bool, `time.Time` Date if `type:date` else DateTime, `uuid.UUID` Uuid, `*T` as T, else None.

**D4 Routes.** Registrations are gocheck's (`GET`...`Handle`/`Match`); a non-constant path is `unread`, never guessed.
The full path is the router chain read back to the engine: `Group(p, mw...)`; `Use(mw...)` only for registrations after
it in the same function; a local assigned once (else `unread`); **struct-field routers**: `x.F` with `x` of a module
struct type takes field `F` of that type's composite literals in the module: one distinct expression resolves (in that
literal's `Env`), two or more are `unread` naming each file:line; a parameter, through each call site (up to 4, one route
per resolved site; gocheck: depth 2, one caller). **Wiring:** a registering function counts only if reachable from a
`main`, by calls or as a function value passed into a parameter the callee calls, including elements of a ranged
variadic/slice parameter (Streak's `features ...Register`, `add(routes)`); unwired routes go to `excluded` ("not reachable
from main"); no `main` package: all wired, with a note. A registration under an `if` keeps the condition's source in
`condition` and stays in the set (`PUT /test/clock`). **Wrappers:** for a handler `W(arg)` where `W` is a module function
whose only returned `FuncLit` calls one of `W`'s func-typed parameters, the handler is `arg` (`FuncRef`, depth 3), `W`
goes in `wrappers` and its literal's refusals become the route's `error_writer` (D6); no parameter called: gocheck's
factory reading; generic `W[T]` the same (type arguments ignored). **Aliasing guard:** a wrapper literal resolved as the
handler of >= 2 routes is an `unread` item on each. Paths: `:x`, `*x` -> `{x}`; scoring identity = (method, path with
every parameter `{}`). `NoRoute`/`NoMethod` -> `excluded` (framework); gin's `Recovery`/`CustomRecovery`/`Logger` are
`external` middleware, not unread.
**D5 Auth by evidence.** A route's chain = group, `Use` and route-level middleware, each resolved through factories and
wrappers. A middleware is **authenticating** iff it has a 401 refusal (D6) **and** records a caller (`c.Set`, directly or
via a module setter). `auth`: `unclear` if a chain middleware is unresolved or the chain is not exact; else `Admin` if an
authenticating or 403-refusing middleware names an admin concept (`mentionsAdmin`); else `Customer` (the importer's single
authenticated role = Streak's `Member`; sidecar `member`) if one is authenticating; else `Public`. A 401 in the handler
(login, refresh) never makes a route Member. `Guard`s: `authenticated` (status, code, line) for the authenticating one,
`custom` for other refusing middleware (Streak: 426 ClientVersion, 429 RateLimited); `auth_reason` names it with
file:line. `AuthModel`: `scheme` bearer when the middleware compares `Authorization` with `"Bearer"`; `users_table` = the
struct it loads; `login_path` = the one Public POST route whose closure calls `bcrypt.CompareHashAndPassword` (else None);
`password_column` = the hash argument's column; `username_field`/`password_field` = that handler's two Body keys (D7) when
exactly one is identifier-like and one password-like.
**D6 Raise sites.** A module function `K` is a **status constructor** iff every return is `&T{Status: s, ...}`/`T{...}`
(`T` a module struct with an int field `Status|Code|HTTPStatus`) or a constructor call, `s` constant (`http.StatusX`,
literal, const) or bound to a parameter of `K` (resolved per call site); the code = the string `Code` field's literal or
argument. Sites in a route's closure (handler + module helpers, depth 4, Body accessor methods included):
`kind="return"` a constructor value returned (also via a returned local); `kind="abort"` `c.AbortWithStatusJSON/
AbortWithStatus/JSON/String(s >= 400)` or a module function forwarding a parameter into one (`httpapi.Fail`);
`kind="handler"` a sentinel (package var `ErrX = errors.New(...)`) the route's error writer maps (`errors.Is(err, ErrX)`
-> a constructor), when the closure returns `ErrX` (e.g. inside `db.OwnedHabit`), at the line of the route's call. The
writer's catch-all 500 is recorded once in `error_writer`, not a site. `RaiseSite(status, detail=code, line, kind)`; the
file stays in the sidecar; an unresolved status is `unread`.
**D7 Request fields.** Bound structs (`c.ShouldBindJSON/ShouldBind/BindJSON(&x)`): a `DataModel` with json names and
`bounds.go`'s binding/validate tag rules. **Body accessor**: a module map type whose methods take a string key and return
`(v, ok[, err])`; each `b.M("lit")` in the closure (helpers handed `b` included) is `RouteParam(source="body")`, type from
`M`'s value result, `required` iff the refusing `if` (its branch returns a 422 site) contains `!ok`; bounds from that `if`:
`v`, `len(v)` or `utf8.RuneCountInString(v)` (directly or via a local) against a constant -> `ge/le/gt/lt`,
`min_length/max_length`; a non-constant bound is `unread`. `c.Query/GetQuery/DefaultQuery("lit")` (and `strconv.Atoi`
of it): `source="query"`, same guard rules. Path parameters: `source="path"`, Str.
**D8 Owner evidence (no name heuristics: `Owned*` helpers are ordinary module functions).** The caller = the result of a
module function reading `c.Get/MustGet(K)` with `K` set by the route's authenticating middleware, or a direct `c.Get*`;
its id = the field matching `(?i)^(user_?id|uid|id)$` (a scalar: itself), followed through locals and **into helper
parameters** (depth 4). `compare`: GORM condition strings parsed for `[qual.]col = ?` / `col IN (?)`, the i-th `?`
binding argument i+1; a caller-id argument gives `OwnerUse(table, col, "compare")`, table = the qualifier's, else the
chain's `Model`/destination type, else `unread`; struct/map conditions and `row.F ==/!= id` in an `if` count the same.
`assign`: a model literal, field assignment or `Create` map setting a column to the caller id. Line = the route's own
call reaching the use.
**D9 Status writes, transactions.** Constant writes to a model `string` column (literal field, `x.F = v`,
`Update("col", v)`, `Updates/UpdateColumns(map|struct)`), `v` a literal, const, or a local whose every assignment in the
function is one (their set); else `unread`. Each: `{table, column, value, root, file, line, guard}`, `root` = the route
reaching it, else the non-route function reached from `main` (the nudger), `guard` = the enclosing condition's text, not
interpreted. A column with >= 2 values is a machine candidate (Spike B). `tx` = the closure reaches `.Transaction(`.

**D10 Never guessed.** Every skipped construct is an `unread` item `{file, line, what, reason}` (what: route, router,
handler, middleware, raise, field, bound, owner, status_write, table): interface dispatch in a closure, non-constant
path/status, reflection. `excluded`: framework handlers, unwired functions, files dropped by build constraints
(`go/build` `Default.MatchFile`, applied in goextract only; `Load` unchanged).
**D11 Determinism.** Arrays sorted by (file, line, key), routes by registration; map keys by `encoding/json`; paths
relative to root with `/`; no absolute path, time, duration, host or toolchain version (`go` from go.mod).

**D12 Shim `lattice/importer/frontends/go_gin.py`** (`name="gin"`; `package` = module dir relative to root, `.`).
`detect`: `<root>/<package>/go.mod` requires gin and `gorm.io/gorm` (no go.mod: False, no raise). `extract`:
`build_gocheck()` + `extract`, mapped per D2. `graph_hints`: all-False. `runtime`: the harness's `RUNTIME` (studio.toml
`[app] runtime`), else `process_runtime(framework="go", auth=AuthDriver.json(...))` from `model.auth`. `reconcile(root,
model)`: imports the harness (`[app] app/db`, `sys.dont_write_bytecode`), strips `LATTICE_*_URL`, opens a throwaway server
(`dbserver.server_for(..., name_prefix="lattice-gox-")`), `build_db`, `make_app`; each route **unauthenticated** (params
`1`, `{}` body on writes) plus a control `<path>/zz-absent`; per route `{status, control, observed: status not in (404,
405)}`, no port or timing; teardown in `finally`. Loaded by `select("gin")`, or by auto-detect after every Python front end
declined (`_load_go`): Python imports never load it. **Never:** network beyond 127.0.0.1, `docker pull`, `go build` in or
any write under the target root (`bin/`, `__pycache__`, `.lattice/`), git on the target, an LLM or API key; a missing
binary, driver or Docker is an error naming it, never a SQLite fallback.
**D13 Candidate spec.** `generate_profile(model)` then `build_spec(model, profile, users={})`: one contract per route; no
lift, probes or mining here (PS only checks it runs). `import_rules` and the lift CLI still refuse Go, unchanged.

## 3. Probes (runner `dogfood/importer_go_probes.py`; blind pass rules)

Clones: `git archive` of Streak a0679e9 (**S**) and 1d00ede (**S4**) into `$SCRATCH`. Ground truth: the registrations
below (m) and an independent regex parse of `models.go` by D3's rules (~40 lines in the runner, written first).
S Public (5): `POST /v1/auth/{register,login,refresh}`, `GET /health`, `PUT /test/clock` (conditional). S Member (21):
`POST /v1/auth/logout`; `GET|PUT|DELETE /v1/me`; `POST /v1/devices`; `DELETE /v1/devices/{}`; `POST|GET /v1/habits`;
`GET|PATCH|DELETE /v1/habits/{}`; `POST|GET /v1/habits/{}/completions`; `DELETE /v1/habits/{}/completions/{}`;
`POST /v1/completions/batch`; `GET /v1/habits/{}/{progress,history}`; `GET /v1/{home,sync,nudges}`;
`POST /v1/nudges/{}/dismiss`.

| id | probe | prediction (p) | pass |
|---|---|---|---|
| PR1 | routes on S, method + full path vs the 26 | recall 26/26, precision 26/26, `exact` 26 | exact, 0 extra |
| PR2 | handlers on S: distinct; file:line = the `func` line (grep); `wrappers=["httpapi.Handle"]` on all but `/health` | 26, 26/26, 25/25 | all exact |
| PR3 | S4 vs its 20; spec-only (spec endpoints minus extracted) on S4 and S | 20/20; S4 = the 6 of section 1; S = [] | exact sets |
| PA | auth on S vs code (Member iff on `r.Member`) and vs the spec's anon column | 21 Customer + 5 Public; 26/26 both | 26/26 both |
| PA2 | `AuthModel` on S | `/v1/auth/login`, User, bearer, hashed_password, email/password | 6/6 |
| PT | tables on S vs the regex parse: nullable, pk, unique, index, default, max_length, type | 10 tables (8 + test_clock + schema_migrations); 67/67 columns, 469/469 facts; `unique_together` 2/2 | exact + count table exact |
| PE | statuses: each spec error cell (endpoint, status >= 400) in `raises` ∪ `guards` | **54 cells, 24 endpoints (m)**; 54/54 | 54/54 |
| PE2 | codes and extras (report) | of the spec's **65 (m)** (status, code) pairs 58-65 found; 25-45 code statuses with no spec cell (19 are Member 429s) | reported |
| PF | spec 422 (endpoint, field) cells found as `RouteParam` | **32 cells (m)**; 31-32 | >= 30/32 |
| PF2 | bounds: name 1..100 (runes), description max 500, target_count ge 1 + le `unread` at `habits/routes.go:55`, sync limit 1..200 | 7/7 | 7/7 |
| PO | `(habits, user_id, compare)` on 14 Member routes (habit by id x3, completions x3, batch, progress, history, list, home, sync, nudges, dismiss via `OwnedNudge`'s `habits.user_id`); `assign` on `POST /v1/habits`, `POST /v1/devices`; a caller-id use per Member route; none on Public | 14/14, 2/2, 21/21, 0 | 14/14, 2/2, >= 20/21, 0 |
| PM | `Nudge.delivery` values | {pending, suppressed} from the 3 routes reaching `nudges.AfterCheckIn` (completions create, remove, batch; `checkin.go:80,87`) ∪ {failed, sent, suppressed} from root `RunDailyPass` = the spec machine's 4 states | exact |
| PS | `generate_profile` + `build_spec` on S | runs; 26 contracts; owners of the `user_id` tables `unclear` (R-C) | runs, 26 |
| PU | unread on S | structural (path not exact, handler unresolved, auth unclear) 0/26; 3-25 detail items | 0/26; every item's file exists and its line holds the named symbol |
| PD | 10 runs on S + 1 from a second clone path | sidecar and `to_json()` byte-identical 11/11; no `/Users`, `/private` | 11/11 |
| PW | tree listing + sha256 of every S file, taken after the runner's `go build`, vs after extract + reconcile | identical | 0 changed, 0 new |
| PX | reconcile on S (Streak harness, Docker MySQL, test mode; the runner builds `bin/` in the clone) | observed 26/26 (21 x 401, `/health` 200, 4 public 4xx); control 404 26/26; 1 `lattice-gox-` container, 0 after | 26/26, 26/26, 0 leaks |
| PX2 | stale binary: built from S minus `r.Member.GET("/home", ...)` (str.replace, count 1), source unchanged | only `GET /v1/home` not observed | exactly 1 |
| PB | byte-compat (below) | all | all |
| PT2 | goextract wall on S (median of 10); cold `build_gocheck` | 0.05-0.5 s; 3-15 s | <= 5 s; <= 60 s |

PT count table (m; cols/nullable/pk/unique/index/default/max_length/unique_together): User 6/0/1/1/1/1/3/0;
NotificationPrefs 6/1/1/0/0/1/3/0; Device 9/0/1/0/2/0/5/0; RefreshToken 8/2/1/1/2/0/2/0; Habit 14/3/1/0/1/2/3/0;
HabitTarget 4/0/1/0/2/0/1/1; Completion 8/2/1/0/1/0/2/0; Nudge 12/3/1/0/2/0/5/1; **total 67/11/8/2/11/4/24/2**. Code wins
over spec, reported, not failures: `devices.push_token` is a non-unique index in code but a spec key; 6 `char(36)` Str
columns are Uuid in the spec (habits.id, habit_targets.habit_id, completions.id/habit_id, nudges.id/habit_id).
**PB:** `git diff 4845ab5 --stat -- lattice/gocheck/src` = `main.go` + new `goextract*.go`; gocheck findings of the old
binary (`git archive 4845ab5 lattice/gocheck/src`) and the new one identical on every `tests/gocheck/fixtures/*` and on S
under `GoPolicy.from_spec(S spec)`; `pytest tests/gocheck tests/importer` passes; `tests/importer/goldens` and
`appmodel, emit_spec, profile_gen, rules, transport, spec/process, dbserver` have an empty diff; `pytest
tests/importer/test_go_gin.py tests/importer/test_frontend_goldens.py` passes in one process (go_gin imported first).

**H, held out (scored once, report-only, never tuned).** `job-scheduler-go` (multidb 00dd1de) and -baseline b8d9fe0,
-min-base-opus 00cbf55, -min-base-sonnet 7ac55d5, -min-spec-opus 7d1a730, -min-spec-sonnet f12365b (clean (m); 14-19
`.go` files, 1.5-1.8k lines each; not read); spec: job-scheduler main ef17cf8. Order: (1) freeze: sha256 of
`lattice/gocheck/src/*.go` + `go_gin.py` recorded in the results; (2) a fresh agent writes each app's ground truth from
the code alone (registrations; tables with D3 column facts), hashed, nothing committed; (3) the runner archives each app
into scratch and extracts once. Predicted ranges per app: route recall 0.80-1.00 (median 1.00), precision 0.90-1.00,
table recall 0.85-1.00, column facts 0.85-1.00, auth vs the spec's access rows 0.60-1.00, raise-status recall on spec
error cells (paths matched modulo prefix) 0.40-0.95, structural unread 0-20%, crashes 0/6. A miss is a generality finding
(risk 1); no fix is re-scored as blind.

## 4. Mutants (`dogfood/importer_go_mutants.py` on a scratch copy; predicted 13/13 killed, control passes first)

| id | mutant | killed by |
|---|---|---|
| GX1 | struct-field router not resolved (group prefix dropped) | PR1 (2/26 exact) |
| GX2 | wrapper not unwrapped (handler = wrapper literal) | PR2 (1 distinct), aliasing `unread` |
| GX3 | Body guards ignored (no bounds, no required) | PF2 |
| GX4 | caller id not followed into helper parameters (`Owned*` evidence lost) | PO (14 -> 3: list, home, sync) |
| GX5 | SQL condition strings not parsed | PO |
| GX6 | middleware ignored for auth (all Public) | PA (21 wrong) |
| GX7 | constructors not resolved (direct `Abort*`/`JSON` only) | PE (401/409/422/429 cells) |
| GX8 | sentinels not mapped (`ErrNotFound` -> 404 lost) | PE (404 cells of `OwnedHabit` routes) |
| GX9 | wiring off (every registering function counts) | fixture test `unwired_register` |
| GX10 | conditional registration dropped | PR1 (25/26) |
| GX11 | composite `uniqueIndex` read as per-column unique | PT (unique 2 -> 6) |
| GX12 | unsorted map iteration / absolute paths | PD |
| GX13 | reconcile counts a 404 as observed | PX2 |

## 5. Risks, and what this cannot show

1. **Idiom overfitting.** D4-D8 were written with Streak open; H is assumed textbook Gin from aggregates only. Mitigated
   by H, by `unread` instead of guesses, and by fixtures for idioms Streak lacks (bound structs, `c.JSON(4xx)`, a dynamic
   route table, a generic wrapper, a build-tagged file, an unwired Register).
2. **Wrapper aliasing** (measured: 1 handler for 24 routes). A literal calling its parameter conditionally, or two
   parameters, reads as a factory: a wrong handler, flagged only when shared.
3. **Generics** (syntactic types; Streak's `NewPage[T, B]`): unresolved -> `unread`. **Dynamic route tables** (`range`
   over `{M, P, H}` structs): non-constant paths -> `unread`, recall drops, not precision. **Build tags**: default
   GOOS/GOARCH; a route built only elsewhere is `excluded`, maybe wrongly.
4. **Constructor/sentinel rules** need a `Status`-like field and `errors.Is` in one writer (`errors.As`, status maps:
   `unread`). **Reconcile shows presence, not behaviour**: 21 routes stop at the middleware; a 200 `NoRoute` defeats it.
5. **Cannot show:** response shapes (`response_model=None`: contracts without response fields), when a refusal fires,
   roles beyond Public/Member/Admin, owners in the generated profile (no foreign keys), other frameworks or databases,
   Linux Docker. **Builder bias:** the builder reads these predictions and the Streak ground truth; only H is blind.

## 6. Blind vs post hoc, and commands

Fixed now. A failed prediction is a **finding**, never silently fixed; fixes are separate post-hoc commits with probes
re-run and deltas reported; H is never re-scored as blind. If R-A changes the refs, PR3 changes by amendment, before code.

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-goextract PYTHONDONTWRITEBYTECODE=1
SCRATCH=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/goextract
DRV=$SCRATCH/../perfengines/perfvenv/bin/python        # pymysql over the Lattice venv (read-only reuse), for PX
cd /Users/vivekgade/Documents/lattice-goextract
git -C /Users/vivekgade/Documents/streak status --short; docker image inspect mysql:8.4 >/dev/null
"$DRV" dogfood/importer_go_probes.py --streak-ref a0679e9 --streak-ref2 1d00ede --runs 10 \
    --scratch "$SCRATCH/p" --out "$SCRATCH/p-data"                         # every S/S4 probe of section 3
uv run python dogfood/importer_go_mutants.py $(seq -f GX%g 1 13)          # control passes; 13 killed
uv run python -m pytest tests/gocheck tests/importer -q -rs               # PB
uv run python -m pytest tests/importer/test_go_gin.py tests/importer/test_frontend_goldens.py -q
shasum -a 256 lattice/gocheck/src/*.go lattice/importer/frontends/go_gin.py   # freeze, recorded BEFORE H
uv run python dogfood/importer_go_probes.py --held-out --scratch "$SCRATCH/h" --out "$SCRATCH/h-data"   # once
uv run ruff check <changed .py>; uv run ruff format --check <created .py>; gofmt -l lattice/gocheck/src
```

**Pass:** every gated row of section 3, 13/13 mutants, PB; H reported against its ranges.

## 7. File ownership (build node)

Owns: new `lattice/gocheck/src/goextract*.go` (by area); `main.go` (the `extract` dispatch, <= 12 lines); new
`lattice/importer/frontends/go_gin.py`; `frontends/__init__.py` (`_load_go`, `select("gin")`, a docstring line; R-D); new
`tests/gocheck/fixtures/goextract/` (risk 1's idioms + `unwired_register`), `tests/gocheck/test_goextract.py`,
`tests/importer/test_go_gin.py`, `dogfood/importer_go_probes.py`, `dogfood/importer_go_mutants.py`; results + data.
**Not touched:** the rest of `lattice/gocheck/` (`build_gocheck` reused), `lattice/importer/{appmodel,emit_spec,
profile_gen,rules,cli,transport,suite}.py`, `lattice/spec/`, existing tests and goldens (an edit is a finding), Streak,
the job-scheduler checkouts, the main checkout (clones/archives in `$SCRATCH` only).

## Amendment 1 (orchestrator rulings, 2026-10-15, before any code)

**R-A approved.** Ground truth is Streak a0679e9 (26 registrations, 24 under `/v1`, 0 spec-only) plus 1d00ede
(20 / 18 / 6 spec-only) as the spec-only case. This corrects the plan document
(`docs/design/lattice-implemented-logic-spikes-plan.md`, which said 20 / 17 / 4 from a level-1 rebuild
directory); the plan is amended at integration, the measured numbers here govern.
**R-B approved** (extract -> `generate_profile` -> `build_spec`; no lift or probes in this spike).
**R-C approved** (no foreign key from naming; owners of `user_id` tables are `unclear`). Reporting that is
the honest answer, not a defect to fix in this spike.
**R-D approved** (the lazy `_load_go` edit in `frontends/__init__.py`, minimal; the existing front ends'
behaviour and the goldens are byte-unchanged, probe PB).
**R-E approved**, tightened: (1) the build node does **not read, run or extract** any `job-scheduler-go*`
source at any time; it implements and tests against Streak and the repo's own fixtures only; (2) the
**orchestrator**, not the builder, performs H: it records the freeze hashes, has a fresh agent write each
held-out ground truth from code alone (hashed), then runs the runner's H stage once; (3) H is report-only,
and a fix made after seeing H is never re-scored as blind.
**Other rulings.** A failed prediction is a finding, not tuned; the sidecar JSON (status writes,
transactions, full unread list) is the only place for fields `RepoModel` lacks (no new `RepoModel` fields);
Docker containers use the `lattice-gox-` prefix and must be 0 after every run; nothing is written into the
Streak checkout or any `job-scheduler*` directory (archives in scratch only; set `sys.dont_write_bytecode`).
