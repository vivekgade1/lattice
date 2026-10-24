# H2 (.NET, held out): post hoc localisation of the failures

Date: 2026-10-23. Node PH. **Everything in this file is POST HOC.** The scored values of the one scored run
(`docs/results/data/2026-10-23-dotnet-h2-scored/`, ledger `docs/results/data/2026-10-23-dotnet-h2/ledger.jsonl`) are final and
are not moved by anything here (prereg `docs/results/2026-10-23-dotnet-h2-prereg.md` sections 4 and 10.2). Nothing was re-scored: no
runner was started with `--kind scored/rerun/verify`. No file under `lattice/`, `dogfood/` or `tests/`, and no frozen file, was
changed. No model was called. H2 is development data from now on, so every fix suggested below is a description only, to be built and
then tested on a new held-out app.

**Method.** Library at `ffa341e` (the scored commit), .NET SDK 10.0.401. Work was done on scratch copies of `h2.tgz` and `h1.tgz` only:
- `lattice.dncheck.extract_json(<module>, workflow=True, policy=True)` on H2 and H1;
- the rules import as the runner runs it (`python -m lattice.importer <module> --package . --rules-only --framework aspnetcore --out ..`,
  cwd = the overlay root, `PYTHONPATH=<overlay>:<worktree>`);
- `python -m lattice.importer workflows <module> --package . --user mara --user tobias --user ines --database sqlite`;
- the frozen H1 scorer functions (`dogfood/dotnet_probes.py` `score_*`, `item_audit`), imported read-only, applied to the extract and
  to hand-edited copies of it ("counterfactual", CF below);
- three scratch app variants (never part of H2), each changing one idiom with the same behaviour and schema: **V-C** the model-wide
  `SetColumnName` loop replaced by per-property `HasColumnName("snake_name")`; **V-N** V-C plus navigations `Session.User` and
  `Hive.Keeper` with fluent `HasOne(x => x.Nav)`; layer A on H2 with the W3 mutants `m01-m05`, `m16` applied.

Recomputed on the scratch extract, the frozen scorers return exactly the scored values (AUTH 2/13, TABLES 0/168, WIRE 0/20, BOUNDS
0 claims / 34, RAISE precision 0.6818 recall 0.8824), so the scratch extract is the scored extract.

## 1. The short version (post hoc)

Five idioms in the blind H2 app, none of them in H1, account for the failures. Ranked by how many rows each one explains:

| rank | cause | idiom in H2 | rows it explains (gated **bold**) |
|---|---|---|---|
| 1 | **C-COL** column names set by a model-wide loop are not read | `ApiaryDb.cs:56-57` `prop.SetColumnName(Snake(prop.Name))` | **TABLES**, **WIRE**, the import crash, so **PI3**, **IMPL**, the vacuous **PI7**, OWN, PI4, PI5, PR3, PA0, X and the whole mutants arm; part of **PA1** |
| 2 | **C-AUTH** a custom route extension that adds an authenticating endpoint filter is not followed | `Caller.cs:24-45` `RequireSignIn(role?)` -> `AddEndpointFilter`; `Caller.Of(http)` reads `HttpContext.Items` | **AUTH**, **WF**, both RAISE misses, `owner_uses` empty (so derived owners `{}`, OWN, HM1-4), 11 of the 12 refuted items of the import once C-COL is out of the way (**PI3**), and the hollow **JUDGE** pass |
| 3 | **C-MW** a middleware's 400 for unreadable bodies is attached to every route | `Program.cs:26-35` | **RAISE** (all 7 false positives) |
| 4 | **C-PROJ** `derive_dn_policy` names every never-serialised column private | (rule, not an idiom; same failure as H1's H-PA1) | **PA1** |
| 5 | **C-BND** bounds enforced by a hand-written validator helper are not read | `Failure.cs:17-50` `Check.Text/Int/Number/OptionalText` and a problem dictionary | the vacuous **BOUNDS** pass (0 claims, recall 0) |
| 6 | C-FK navigation-less `HasOne<T>().WithMany().HasForeignKey(...)` | `ApiaryDb.cs:30,37,44,52` | 4 of 168 TABLES facts (not decisive) |
| 7 | C-OVL overloads of one name are left ambiguous when the argument's type comes from an unrestored package | `HiveEndpoints.cs:10,12` two `View(...)` overloads | GET /hives/{id} only partly read; `Hive.yard_location` not derived private; HM1 not caught by DN801 even under the truth policy |
| -- | T truth omissions (not library) | the H2 truth lists `401` for `GET /hives` only and `sessions.token` wire as `access_token` | caps RAISE precision once C-AUTH is fixed; 1 of 20 WIRE |

**Vacuous or hollow passes:** H2-BOUNDS (empty claim set), H2-PI7 (ten identical crash tails), H2-JUDGE (a proxy that does not run DN801;
DN801 itself judges 0 of 6 under the derived policy and misses HM1 under the truth policy). H2-UNREAD and H2-ROUTES are true but narrow
(H2-UNREAD is implied by ROUTES 13/13; ROUTES hides 11 duplicated route records). **Meaningful passes: H2-ROUTES (with that caveat),
H2-UNREAD (route level only), and the reported H2-DET.** So of the 5 gated passes, 2 carry information.

## 2. Per failing row

### H2-AUTH: exact 2 of 13 (gate 0.85)
- **Observed.** 11 routes `Public` with `auth_reason: "no authorization evidence"`; truth `Member` (10) and `Admin` (`GET /admin/overview`).
- **App.** Every signed-in route ends in `.RequireSignIn()` (`HiveEndpoints.cs:25,44,51,62,71,87`, `LotEndpoints.cs:30,49,56,86`) or
  `.RequireSignIn(Roles.Inspector)` (`AdminEndpoints.cs:23`). `RequireSignIn` (`Http/Caller.cs:24-45`) is a project extension method on
  `RouteHandlerBuilder` that calls `route.AddEndpointFilter(lambda)`; the lambda reads the `Authorization` header (`:28`), answers 401
  (`:31`, `:40`), 403 on a wrong role (`:41`), and stores the caller with `Caller.Set` -> `ctx.Items["apiary.caller"]` (`:16`, `:43`).
  No `AddAuthentication`, no `RequireAuthorization`, no `[Authorize]`, no fallback policy.
- **Library, three gaps on one path.**
  1. `lattice/dncheck/src/Routes.cs:277-298` (`EvalCall`): a project method is walked only when an argument is a route **group** or an
     endpoint builder (`:280`); `RouteHandlerBuilder` (the receiver here, an `EpVal`) is neither, so the body of `RequireSignIn`, and the
     `AddEndpointFilter` inside it, is never evaluated against the route, and `:298` returns `null` because `RequireSignIn` is not in
     `ConventionNames` (`:69`). Side effect: `:279` evaluates the bound arguments, and `Closure.cs:310` (`MapArgs`) yields the extension
     receiver `app.MapGet(...)` as argument 0, so `MapGet` is evaluated a **second time**: the extract has **24 route records for 13
     routes** (each of the 11 filtered routes twice). The scorer's `ext_route_map` (a dict) collapses them, so H2-ROUTES shows 13.
  2. Even when an endpoint filter is attached directly (`.AddEndpointFilter(...)` on the route, which `Routes.cs:363` does record as a
     `filter` meta and `Auth.cs:321-345` `CollectFilters` turns into a scoped middleware), `Auth.cs:186-201` (`EvalAuth`) counts only
     `authorize`/`anonymous` metas: a filter that reads the Authorization header and refuses 401 is no auth evidence, and
     `HandAuthenticated` (`Auth.cs:310-318`) looks only at the handler's own raises (`Kind != "middleware"`).
  3. `Auth.cs:97-118` (`CollectCaller`) looks for `HttpContext.Items[...] = <project type>` only in `AuthenticationHandler` subclasses and
     conventional middleware classes, not in endpoint filters, so `"apiary.caller"` is not a caller key, `Caller.Of` is not a caller
     function (`dotnet.auth.caller_funcs: []`), and **every route's `owner_uses` is empty** although each handler compares
     `h.OwnerId == me.Id` (e.g. `HiveEndpoints.cs:23,49,57,67`).
  4. (Residual, after 1-3) `Auth.cs:34` `IsAdminName` is `contains "admin"`: the gating role here is `Inspector`, so the admin route would
     read `Member`.
- **H1 contrast.** H1 authenticated with a custom `AuthenticationHandler` plus a fallback policy and `[AllowAnonymous]`/policy attributes
  (H1 `IDIOMS.md`), all of which `Auth.cs` reads (16/16).
- **Minimal generic change (described).** (a) In `EvalCall`, also follow a project method whose receiver/argument value is an `EpVal`
  (a `RouteHandlerBuilder`/`IEndpointConventionBuilder`), binding that parameter to the already-evaluated `EpVal` instead of
  re-evaluating the receiver expression (which also removes the duplicates); conventions applied inside then reach the route. (b) In
  `EvalAuth`, treat a scoped endpoint/MVC filter whose reach reads the Authorization header (or the user) and refuses 401 as `Member`
  evidence, and one that refuses 403 on a role compare whose role value is a constant at the call site as role-gated. (c) In
  `CollectCaller`, include the filters `CollectFilters` found. (d) Optional: treat a route gated by a role that not every user holds as
  `Admin` rather than requiring the substring "admin".
- **CF.** With (a)-(c), auth on the 11 routes `Member`: **12 of 13 = 0.92 (passes 0.85)**; with (d) 13 of 13.

### H2-TABLES: facts 0 of 168 (gate 0.90) and H2-WIRE: 0 of 20 (gate 0.95)
- **Observed.** Tables 5 of 5 found; every column fact wrong; every wire name "x vs None".
- **The EF model is read; the column names are not.** The extract has `ToTable` names, `HasMaxLength`, unique indexes, PKs, nullability and
  correct `wire` names (`json_naming: SnakeCaseLower` from `Program.cs:17`), but each column's `name` (the SQL name) is the C# property
  name (`Id`, `UserId`, `QueenYear` ...).
- **App.** `Data/ApiaryDb.cs:56-57`: after the per-entity configuration, `foreach (var prop in b.Model.GetEntityTypes().SelectMany(t =>
  t.GetProperties())) prop.SetColumnName(Snake(prop.Name));` with a hand-written `Snake` (`:60-69`). The schema `EnsureCreated()` writes is
  snake_case (the harness `DDL` copies it).
- **Library.** `lattice/dncheck/src/Ef.cs:473` `c.Name = pi.ColName ?? (otherNaming ? p.Name : SqlName(p.Name))`: `ColName` comes only
  from `HasColumnName` (`:294`, `:601`) or `[Column]`; `SqlName` (`:457`) snake-cases only when `UseSnakeCaseNamingConvention()` (the
  EFCore.NamingConventions plugin, `:153`) is seen. `SetColumnName` in a model-wide loop is neither read **nor recorded as unread**
  (contrast the plugin case at `:464`, which marks the table `name_unread`). So the wrong names are silent (an N11-type defect not caught
  by H2-UNREAD, which is route level).
- **Scorer.** `score_tables`/`score_wire` (`dogfood/dotnet_probes.py:1043-1088`) match columns by SQL name, exact on SQLite (`cname`,
  `:975`): `Id` != `id`, so no column matches, every fact counts wrong and every wire lookup returns `None`. Not a scorer key mismatch:
  the extract's `name` is documented as the SQL name and is wrong. (Sensitivity: with case-insensitive matching it would still be 89/168 =
  0.53 and 10/20, because multi-word names differ in more than case.)
- **H1 contrast.** H1 named every column with per-property `HasColumnName("snake_name")` in `IEntityTypeConfiguration` classes (read:
  190/192, 28/28).
- **Minimal generic change (described).** In `Ef.cs`, recognise `SetColumnName`/`SetTableName` (and `HasColumnName` on
  `IMutableProperty`) applied in a loop over `GetEntityTypes()`/`GetProperties()` in `OnModelCreating`/`ConfigureConventions`; when the
  argument is a project `static string F(string)` of the property name, evaluate `F` on each property name with the bounded constant
  evaluator the extractor already uses for other values, or, if it cannot, mark every affected column and table `name_unread` with an
  `unread` entry (never a silent property name). Independently, the importer should reconcile model column names against the schema it
  already builds (`PRAGMA table_info` after `_schema`, see 2.5) and say so when they disagree.
- **CF (extract column names snake-cased, nothing else changed).** TABLES **162/168 = 0.964 (passes)**, WIRE **19/20 = 0.95 (passes, at
  the gate)**. The 6 facts left: 4 FKs (C-FK) and 2 `private` (`users.password`, `hives.yard_location`: the truth's `private` is
  "never returned", the extract's is `[JsonIgnore]`; same as H1's 2/192). The one wire miss is a truth convention (T): `sessions.token`
  is answered as `access_token` by an anonymous object in `/login` (`AccountEndpoints.cs:28`), the extract gives the property's wire
  name `token`.

### C-FK (part of H2-TABLES): 4 foreign keys missing
- **App.** `ApiaryDb.cs:30,37,44,52` `e.HasOne<User>().WithMany().HasForeignKey(x => x.UserId)` (no navigation properties anywhere,
  `Domain/Entities.cs`).
- **Library.** `Ef.cs:278-290`: for `HasOne(...)` followed by `WithMany(...)`, `:282` stores `pendingRel = (ei.T, relTarget, ...)` into the
  tuple `(Prin, Dep, DepNav, PrinNav)` (`:329`), i.e. the configured (dependent) entity as principal; `HasForeignKey` (`:284-290`) then
  attaches the key to the principal, which has no such property, and it is dropped in `Finalize` (`:504-509`). H1 did not show it because
  H1 has navigations and `FindForeignKeys` (`:423-440`) finds the same keys by convention (`<Target>Id`).
- **Evidence (variant V-N, extract only).** `Session` with a conventional navigation `User` gets `user_id -> users.id`; `Hive` with
  navigation `Keeper` and fluent `HasForeignKey(x => x.OwnerId)` (no convention match) still gets no foreign key. So the fluent path does
  not attach a key in the `HasOne...WithMany` order (p 0.85 that the swap at `:282` is the whole cause).
- **Change.** Swap the tuple order in the `HasOne` branch (and check the `HasMany...WithOne` branch at `:281` against a fixture with no
  navigation-name convention match). **CF:** with C-COL and C-FK, TABLES 166/168 = 0.988.

### H2-RAISE: precision 0.68, recall 0.88 (gate precision 1.00, recall 0.60)
- **False positives (7):** `400` on `GET /health`, `GET /hives`, `GET /hives/{}`, `DELETE /hives/{}`, `GET /hives/{}/inspections`,
  `GET /lots/{}`, `GET /admin/overview`, each with `kind: "middleware"`, `in: Apiary.Http.Failure.Invalid`.
- **App.** `Program.cs:26-35`: `app.Use(...)` catches `BadHttpRequestException` (an unreadable or wrongly typed JSON body) and answers
  `Failure.Invalid` 400. None of the 7 routes binds a body or a parameter whose binding can fail (`{id:int}` failing its constraint is a
  404 from routing, not this middleware), so the 400 is unreachable there; the truth is right.
- **Library.** `Auth.cs:367-381` (`ApplyMiddleware`) adds every raise of every collected middleware (`AddMw` for `app.Use(lambda)`,
  `Auth.cs:252`) to every route; its only filters are auth-handler / `NonPublicOnly` / `HandleForbiddenAsync` conditions.
- **Misses (2):** `GET /hives 401` and `GET /admin/overview 403` come from the filter of C-AUTH (`Caller.cs:31,40,41`), never attached.
- **Change.** Scope a middleware raise reached only inside `catch (BadHttpRequestException ...)` (or `catch` of the JSON exception) to the
  routes that bind a body/form or a typed non-path parameter. **CF:** precision **1.00**, recall 0.88: **passes**.
- **Interaction with C-AUTH (important).** Once the filter is attached, `ApplyMiddleware` adds its `401` to all 11 filtered routes and
  (because the one filter lambda is one `MwInfo` keyed by its syntax node, `Auth.cs:338`, and `role is not null` is not folded per call
  site) its `403` too. The H2 truth lists `401` only for `GET /hives` and `403` only for the admin route (truth refusals, `Caller.cs`
  lines), whereas H1's author listed `401` for every signed-in route. **CF:** with C-MW and C-AUTH: precision 0.46 (401+403 everywhere) or
  0.63 (403 only where a role is passed), recall 1.00. So H2-RAISE's precision-1.00 gate cannot be met by an extractor that reports the
  filter correctly against this truth: one shared cause (C-AUTH) flips AUTH and WF to pass and RAISE back to fail. This is a truth
  completeness limit (T), not a library defect.

### H2-BOUNDS: precision 1.00 from 0 claims, recall 0 of 34 (gate precision 1.00): a vacuous pass
- **App.** Bodies are nullable records (`Http/Dtos.cs:4-8`, so no `[Required]`/`[Range]`); every bound is enforced by the hand-written
  `Check` collector (`Failure.cs:17-50`): `Text(field, value, min, max)` (`:23-29`), `OptionalText` (`:31-36`), `Int` (`:38-43`),
  `Number` (`:45-50`), each writing `Problems[field]`, refused later by `if (!check.Ok) return Failure.Invalid(check)`
  (`AccountEndpoints.cs:17-19`, `HiveEndpoints.cs:30-35,76-80`, `LotEndpoints.cs:34-38`); plus inline problem-guards that write the
  dictionary instead of returning (`AccountEndpoints.cs:18`, `LotEndpoints.cs:62-64`).
- **Library.** `dotnet.validation` = `{annotations_enforced_on: [], fluentvalidation: [], minimal_api_validation: false}`;
  `Binding.cs:317-358` (`GuardBounds`) reads only `if (<comparison on a body property>) return <constant 4xx>` in the handler
  (`:332` requires the `if` to refuse), so a comparison inside a helper, or an `if` that records a problem for a later refusal, is
  not read. Every body param reads `required: false` with no bounds. Not recorded as unread either.
- **H1 contrast.** H1 used DataAnnotations under `[ApiController]` (read: 34/34, precision 1.00).
- **Change.** (a) Summarise project validator helpers: a method whose parameters `(string field, T value, T min, T max)` reach a
  comparison `value < min || value > max` (or a `Length` of it) that writes a problem keyed by `field`; at each call site with constant
  arguments, bind the bound to the wire field named by the string literal (the H2 field names are the wire names). (b) Accept a
  "collect then refuse" guard: an `if` whose body writes into a collection that a later `if (!x.Ok) return <4xx>` (or `.Count > 0`) on the
  same collection refuses. **CF (hand-derived claims of (a)+(b)):** 33 claims, precision 1.00, **recall 0.97** (33/34; the one miss is
  `queen_year le = DateTime.UtcNow.Year`, not a constant).

### H2-PA1: derived private outside the truth (gate 0 wrong)
- **Observed.** `User: Id, Name, Password; Session: Id, UserId; Lot: OwnerId`; owners `{}`.
- **Library.** `lattice/dncheck/derive.py:111-119`: private = columns with `json == "-"` (no response reads them, `Projection.cs`). This is
  the H1 H-PA1 failure again ("the projection rule over-derives"). On H2 the over-claimed columns are indeed never serialised, but the
  truth's `private`/`not_returned` lists only the secrets (`users.password`, `hives.yard_location`).
- **Second cause (C-COL).** The names are property names, so even `Password` does not equal the truth's `password`. With C-COL fixed,
  `id, name, user_id, owner_id` are still outside the truth: PA1 still fails (p 0.95).
- **Under-claim (C-OVL, not gated by PA1).** `Hive.YardLocation` has `json: "yard_location"` (not private) because the projection of
  `GET /hives/{id}` goes through an ambiguous `View` call (below).
- **The owners half passes vacuously**: owners `{}` follows from C-AUTH(3) (`owner_uses` empty), not from a correct derivation.
- **Change.** Derive `private` only from explicit evidence (`[JsonIgnore]`, a column read only by the login check, a hash-like type) and
  report "never returned" as a separate, non-policy fact.

### H2-PI3, H2-IMPL (gated) and OWN, PI4, PI5, PR3, PA0, X (reported), and the mutants arm: the import crash
- **Observed.** `sqlite3.OperationalError: table sessions has no column named UserId` in `lattice/importer/suite.py:484` (`sql_seed`),
  every row that calls `import_dir` (`dogfood/dotnet_probes.py:3512-3519`), and the mutants runner's control import
  (`dogfood/dotnet_h2_mutants.py:132`).
- **Root cause = C-COL.** `rules.py:356` calls `sql_seed(..., schema_hook=self._schema)`; `_schema` (`rules.py:358-370`) lets the app
  create its own schema (`EnsureCreated`, snake_case), then `sql_seed` inserts with the extracted `TableSpec` column names (the property
  names) (`suite.py:448-501`). `users (Id, Name, Password, Role)` inserts **succeed** because SQLite identifiers are case-insensitive;
  `sessions.UserId` is the first name that differs by more than case (`user_id`), so it fails there. The name `UserId` is exactly the
  wrong fact of H2-TABLES.
- **Reproduced** on a scratch copy (same traceback, exit 1). **Variant V-C** (only the column idiom changed): the import exits 0 in 46 s,
  68 rule items: 9 verified, 6 verified (corrected), 12 refuted, 41 unexercised, **item-audit share 0.22** (gate 0.50), refuted without
  correction: `T1 User` and `R1` of all 11 signed-in routes ("callers answered against the auth: anonymous refused": the candidate spec
  says `Public` for every route, C-AUTH). Owner inferences: none (C-AUTH(3)). `T1 User` "no caller of ['Admin', 'Customer'] could log
  in": the profile's default role names; the app's roles `Keeper`/`Inspector` are not read (the role compare lives in the unread
  filter). Logging in itself works on V-C (signed-in probes answer 404 "hive not found", not 401).
- **So:** fixing C-COL alone turns the crash into a measured PI3 that still fails (0.22, 12 refuted); C-AUTH is the next blocker.
  IMPL, OWN, PI4, PI5, PR3, PA0 are then measured, with unknown values (not run here: they need runner code paths).
- **Change (generic, importer side, in addition to C-COL).** After `schema_hook`, read the real schema and map each `TableSpec`
  column to the actual column (exact, then case-insensitive, then underscore-insensitive), failing with a clear "model column X has no
  schema column" message instead of a raw `OperationalError`; the import is the one place that has both the model and the real schema.

### H2-WF: 0 verified (gate >= 1 multi-step verified)
- Reproduced: `workflows: 4 flows (0 verified, 0 unverified, 4 failed)`; each fails at step 0, e.g. `POST /hives answered 401
  (unauthorized): status 401 is not in the code evidence [201, 400, 409]`, with `"caller": null`. The observed run calls routes the extract
  says are `Public` without a token. **Cause: C-AUTH** (independent of the import crash; the workflow pass seeds through the harness).

## 3. Rows that passed: which carry information

| row | scored | verdict (post hoc) | why |
|---|---|---|---|
| H2-ROUTES | 13/13, 0 extra | **meaningful**, with a caveat | all 13 routes found with the right templates; the extract holds 24 records (11 duplicates from C-AUTH(1)) that the scorer's dict hides |
| H2-UNREAD | n = 0 | true but **implied by ROUTES** | it only asks whether each truth route is extracted or named unread; the silent defects of this run (auth filter, column names, validator bounds, swapped FKs) are not route level and none is recorded in `unread` (the extract has 2 unread entries: the ambiguous `View` and the `Lot.State` write) |
| H2-BOUNDS | precision 1.00 | **vacuous** | 0 claims; recall 0 of 34 (C-BND) |
| H2-PI7 | 10 of 10 identical | **vacuous** | `h2_pi7` (`dogfood/dotnet_h2_probes.py:236`) compares `{"error": stderr[-300:]}` for a failed import; all ten imports crashed with the same tail (reproduced 3/3 on a scratch copy). On V-C the timing-masked trees are 3/3 identical, so a real pass is plausible once the import runs (p 0.7 for 10/10), but H2 did not measure it. (Prereg 10.3 says anything that raises is a FAIL; the runner's code compared the crash instead) |
| H2-JUDGE | 6/6 | **hollow** | `method: "proxy: the extract"`: the library offers no judged list (`dotnet_probes.py:1680-1700`), so the proxy counts owner-scoped routes that are extracted without an "incomplete/opaque" unread. DN801 itself: under the derived policy owners are `{}` (C-AUTH(3)), so it judges **0 of 6**; under the **truth** policy (post hoc, layer A on mutants) it fires on HM2 (`GET /lots/{id}`), HM3 (`DELETE /hives/{id}`), HM16 (`GET /hives`) but **not on HM1** (`GET /hives/{id}`, whose reach has the ambiguous `View`, C-OVL), and HM4's route already has a DN801 on the original (the post-update re-read `LotEndpoints.cs:84` without the owner), so it adds nothing there |
| H2-DET (reported) | 10/10 | **meaningful** | extract and layer A ran and were byte-identical |

## 4. Causal graph (post hoc; p = my probability that the row would flip to pass on H2 if only the named causes were fixed)

```
C-COL ──> extract column names ──> TABLES (p .95) , WIRE (p .80, lands exactly on 0.95)
   └────> import seed crash ─────> PI7 becomes real (p .70 pass) ; X (p .90 pass, 46 s)
                                └> PI3 (p .02 pass alone: V-C share 0.22) ; IMPL, OWN, PI4, PI5, PR3, PA0 measured (values unknown)
                                └> mutants control import runs (arm gate p .05: derived owners {} still blind, C-AUTH(3))
C-AUTH ─> route auth ───────────> AUTH (p .90; 12/13) ; WF (p .60) ; R1 items of the import (11 refuted -> likely verified)
   ├────> caller key / owner_uses -> derived owners, OWN, PA1 owners half, HM1-4 DN801, JUDGE (real value)
   └────> filter raises ─────────> RAISE recall +2 but precision falls (truth T): RAISE fails (p .90 fail)
C-MW ───> RAISE (p .85 pass alone; p .10 together with C-AUTH)
C-PROJ (+C-COL) ─> PA1 (p .80 if private is limited to explicit evidence)
C-BND ──> BOUNDS made meaningful (p .85 pass with recall 0.97)
C-FK ───> TABLES 0.964 -> 0.988 (no gate change)
C-OVL ──> JUDGE true value, HM1/HM6, Hive.yard_location private
C-COL + C-AUTH ─> PI3 (p .30 pass), IMPL (p .40), WF (p .65)
```

Shared vs independent: **C-COL and C-AUTH are two independent root causes that between them account for 6 of the 8 gated failures**
(TABLES, WIRE, PI3, IMPL via C-COL; AUTH, WF via C-AUTH; PI3 again via C-AUTH once C-COL is fixed) and for both hollow passes
that depend on running code (PI7, JUDGE). RAISE (C-MW + truth T), PA1 (C-PROJ, as in H1) and BOUNDS (C-BND) are independent single
causes. No gated failure traces to the runners' code; the runners' only defect found is that H2-PI7 treats identical crashes as identical
trees, which turned a crash into a pass.

## 5. What in H2's idiom differs from H1's (both read by the same extractor)

| concern | H1 (`outlay`, read correctly) | H2 (`apiary`, missed) |
|---|---|---|
| routes | attribute-routed controllers + `MapControllers()` | minimal APIs in `Map*` extension methods |
| auth | custom `AuthenticationHandler`, fallback policy, `[AllowAnonymous]`, named policy | custom `RouteHandlerBuilder` extension adding an endpoint filter; caller in `HttpContext.Items` |
| roles | `Admin` policy role | `Inspector` role passed as an argument |
| column names | per-property `HasColumnName("snake")` | model-wide loop `SetColumnName(Snake(prop.Name))` |
| foreign keys | navigations + fluent `HasOne(nav)` (convention finds them) | no navigations, `HasOne<T>().WithMany().HasForeignKey(fk)` |
| validation | DataAnnotations under `[ApiController]` | hand-written `Check` collector, problems dictionary |
| errors | typed exceptions + `IExceptionHandler` | one `Failure` result factory; a body-read middleware |

Each H2 idiom is ordinary ASP.NET Core / EF Core code; none is exotic. The extractor was fitted on D1 and H1 (prereg section 2), and
every miss here is an idiom outside that set, as the prereg's risk section anticipated for the rules import but not for the extractor
rows (predicted P(pass) 0.80-0.85 each).

## 6. Reproduction (scratch only)

```
tar -xzf docs/results/data/2026-10-23-dotnet-h2/h2.tgz -C $S/h2 ; touch $S/h2/tests/spec/__init__.py
# extract
PYTHONPATH=<worktree> python -c "from lattice.dncheck import extract_json; ..."   # extract_json(<module>, workflow=True, policy=True)
# the import crash
cd $S/h2 && PYTHONPATH=$S/h2:<worktree> python -m lattice.importer $S/h2/tests/spec/apiary --package . --rules-only \
   --framework aspnetcore --out $S/imp1          # -> sqlite3.OperationalError: table sessions has no column named UserId
# the workflow failure
PYTHONPATH=$S/h2:<worktree> python -m lattice.importer workflows $S/h2/tests/spec/apiary --package . --out $S/wf1 \
   --user mara --user tobias --user ines --database sqlite   # -> 4 flows, 0 verified, 4 failed (step 0 answered 401)
```
The counterfactual scoring imports `dogfood/dotnet_probes.py` read-only and calls `score_tables/score_wire/score_auth/score_raise/
score_bounds` on edited copies of the extract; the variants V-C and V-N are copies of the H2 module with the edits named in the method.
