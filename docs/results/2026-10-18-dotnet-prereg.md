# .NET (ASP.NET Core + EF Core + MySQL) at parity with Python and Go (Spike J, node DN1): pre-registration

## 1. Status, scope and labels

2026-10-18. **Written BEFORE any .NET code. No model was called, no API key was used.** This file is the only one this step
writes; it is committed alone on branch `np-dotnet` (worktree `/Users/vivekgade/Documents/lattice-dn`, from `next-phases` c75cb48).
Nothing is pushed or merged. Results go to `docs/results/<date>-dotnet-results.md`, data to `docs/results/data/2026-10-18-dotnet/`
(one directory for every stage, so the global run ledger of section 11 has one home). The re-measured parity matrix goes to
`docs/results/<date>-parity-matrix-j0-dotnet.md`.

Read while writing (read only): `docs/design/lattice-implemented-logic-spikes-plan.md` (Spike J addendum: no C# code generator),
`docs/results/2026-10-17-parity-matrix-j0.md` (all 20 rows), `docs/HANDOFF-2026-10-18.md`, `2026-10-18-go-parity-gaps-round2-results.md`
(sections 1-11), `2026-10-18-go-parity-round3-prereg.md` with Amendments 1-2 and `...-round3-tier1-results.md` (both in the worktrees
`lattice-g3b1`/`lattice-g3b2`; **not on `next-phases`**), `docs/lattice-language-rules.md`. Code read in this worktree:
`lattice/importer/frontends/{__init__,go_gin}.py`, `lattice/importer/go_rules.py` (head), `lattice/importer/rules.py` (the `gin` branches),
`lattice/gocheck/{__init__,derive}.py`, `lattice/gocheck/src/goextract*.go` (the JSON types), `lattice/importer/{workflow_run,inventory}.py`
(extraction), `lattice/spec/{implementation,impact_tier3,check,process,db,__main__}.py` (the stack switches), `lattice/importer/workflows.py`
(the extract keys it reads). Read with `git show` only (nothing written there): Streak `main` a0db033 (`spec.py`, `REQUIREMENTS.md`,
`CONTEXT.md`, `studio.toml`).

Labels: **(m)** measured by an earlier node and restated; **(r)** read from code or data, or measured by a recon command, while writing
this; everything else is a **prediction (p)**.

### 1.1 Where .NET stands, and what the environment gives (r unless marked)

| item | value |
|---|---|
| J0 .NET column | **20 of 20 `A`** (m, J0): no `*.csproj`/`*.cs` handling, no front end in `frontends/__init__.py`, the only stack switches are `PYTHON_FRAMEWORKS` and the `"go"` branch of `lattice/spec/check.py:279,402` |
| SDK, runtimes | `dotnet --list-sdks`: 10.0.401 (Homebrew, arm64); runtimes Microsoft.NETCore.App and Microsoft.AspNetCore.App 10.0.12 (no 8.0/9.0 runtime: every app must target `net10.0` or roll forward) |
| reference packs | `$DOTNET_ROOT/packs/Microsoft.AspNetCore.App.Ref/10.0.12/ref/net10.0` (140 dlls, incl. `Mvc.Core`, `Routing`, `Http.Results`, `Authorization`, `Http.Abstractions`) and `Microsoft.NETCore.App.Ref/10.0.12` (167 dlls) |
| Roslyn | `Microsoft.CodeAnalysis.CSharp` 5.9.0 in `~/.nuget/packages` (the owner's smoke test) |
| NuGet (2026-10-18, `dotnet package search`) | `Pomelo.EntityFrameworkCore.MySql` latest **9.0.0** (no EF Core 10 build); Oracle `MySql.EntityFrameworkCore` latest 10.0.9; `Microsoft.EntityFrameworkCore` latest 10.0.13; `FluentValidation` 12.2.0; `FluentValidation.AspNetCore` 11.3.1; `Microsoft.AspNetCore.Authentication.JwtBearer` 10.0.13 |
| provider smoke (scratch only) | `dotnet new web --framework net10.0` + Pomelo 9.0.0 restores EF Core 9.0.0; a minimal API with a `DbContext`, `UseMySql(..., new MySqlServerVersion(8,4,0))`, `MapGroup("/v1").RequireAuthorization()` and a `{id:int}` route **builds with 0 errors** |
| macOS | no `timeout` binary: every runner timeout is a Python `subprocess` timeout |
| Go state | `next-phases` c75cb48 has J1/J2 (m: 43/47 rows, 32/35 mutants). **G3 (Go round 3: K1-K6 built on `np-go3-b1`, K8 GO805 pending in B2) is not merged**: `git merge-base --is-ancestor fe70010 HEAD` is false |

### 1.2 A correction to the brief this step was given

The brief asks for "workflow layer, implementation report and chat/MCP/impact/appmap consumers working on the .NET `RepoModel`
unchanged". **They do not read a `RepoModel`.** They read the `lattice.goextract/1` *document* (r): `workflow_run.py:918-926` and
`:1049-1053` call `GoGinFrontEnd().extract_json` and `model_from_extract`, `inventory.py:81-87` stores the document with
`"frontend": "go_gin"`, `implementation.py:127-150` takes the source set `*.go`/`go.mod`/`go.sum`, `impact_tier3.py:118` lists `*.go`
files, `workflows.py` reads the document's keys (`routes`, `tables`, `middleware`, `owner_uses`, `status_writes`, `param_uses`,
`creates`, `param_literals`, `status_facts`, `auth`, `grpc`). So "unchanged" is registered as: **the consumers' logic is
unchanged; the .NET extractor answers the same document shape (decision N2), and the only consumer edits are a front-end dispatch
for *where the document comes from* and a per-front-end source-file set**, each with a line budget (section 9), with every Go and
Python output byte-identical (section 10).

### 1.3 What may be claimed as blind

**Blind:** only the held-out app **H1** (section 4.3), written by a separate isolated author after the library freeze to a product
brief that names no idiom, and its mutants written by a second separate agent. **Development data (`dev`):** the Arm 0 fixture
(written by the build node), D1 (Streak reproduced in .NET; its author is separate and blind to the extractor, but the build node
develops against it), the optional D2, `dnapp` (section 4.4), and every Go/Python fixture. Dev numbers are gated as development
gates and as predictions; they never support a generalisation claim.

### 1.4 Lessons carried from the Go rounds (each becomes a binding rule below)

| lesson (source) | rule here |
|---|---|
| separate authors, isolated, transcript-audited (gRPC r3/r4, G3) | A1 (D1), A2 (H1), W0, W are four separate `claude` CLI sessions with fixed working directories; 0 reads of `lattice/**`, `docs/**`, `dogfood/**`, `tests/**` (section 4) |
| the held-out brief named idioms (round 2 risk 1) | H1's brief is in product terms only; no routing style, auth mechanism, validation library, ownership pattern or error style is named (4.3) |
| missing harness facts cost a round each (gRPC r4: `build_db` with no `return`, no `process_runtime`) | `HARNESS_API_DOTNET.md` (4.1) states every fact, is checked on D1 before the freeze, and is hashed in Amendment 3 |
| bare harness module crashed in-process runners (J2 PI4-3, PR3-3) | the dotted `tests.spec.<x>_harness:make_app` form is required; every runner also puts the import root on `sys.path` exactly as `rules.py:939-940` does (G3 K7) |
| the runner had a hard-coded NOT RUN arm (J2 section 10) | runner self-test runs every arm's code path against stand-ins; a NOT RUN with prerequisites present fails the self-test (RM1) |
| self-hash disabled once (gRPC r3) | never; no flag exists to skip it (11.2) |
| per-`--out` ledgers made run counts unauditable (gRPC r4) | one global append-only ledger at a fixed path (11.3) |
| Go's capitalised default JSON keys blinded T2 (JM6) | the .NET extract records the effective JSON naming policy and every column's **wire name**; T2-style detectors and truth compare wire names (N5, N7) |
| route keys lost a `/v1` prefix through a struct-field router (G3 M-SM1) | route identity is scored on the full template with every group/controller/area prefix, and a dropped prefix is a library mutant (LM1) |
| interface calls (`d.Clock.Now`) and function-typed params made reach incomplete (G3 M-SM2/3) | DI-registered single implementations resolve (N10); everything else is listed `unread`, and the judged share is a gated row (H-JUDGE) |
| line budgets were missed silently in earlier rounds | budgets are reported per file; a miss is a finding, not a gate (section 9) |
| an unquoted heredoc ate backticks in amendments | amendments are written with `<<'E'` |

### 1.5 Scope (binding)

**In scope:** a Roslyn-based deterministic extractor and checker (`lattice/dncheck`, one `dotnet`-run helper beside `goextract`), the
`FrontEnd` shim `lattice/importer/frontends/dotnet_aspnet.py`, layer A for .NET (DN8xx), the rules-first import, the workflow layer,
implementation report, chat, MCP, impact and app map on .NET extracts, the corpus (D1, H1, `dnapp`, optional D2), the re-measured
parity matrix, byte-compat, determinism, mutants.

**Out of scope (listed, never guessed):** **gRPC for .NET** (`Grpc.AspNetCore`: `MapGrpcService<T>()` registrations are recorded in
`excluded` with kind `grpc` and reason "gRPC for .NET is out of scope in Spike J round 1"; no rpc endpoint, no proto reading; Spike I
stays Go-only this round); a C# code generator (ruled out by the owner, J0 row 20); behaviour lifting from C# (J0 row 2, an owner
decision as for Go); the server-dialect port of layers R and B (all stacks, G3 Q4); Razor Pages, MVC views, Blazor, SignalR hubs
(excluded with reason); Dapper / ADO.NET-only data access (no EF Core: the app is not detected, N12); conventional routing
(`MapControllerRoute`) beyond listing it `unread`; MediatR-style dispatch, source generators' output, reflection-based registration
beyond the named EF calls; multi-tenant `HasQueryFilter` with runtime tenant switching beyond N8; .NET Framework (non-Core) projects;
F# and VB. The Python inventory (J0 rows 11-15 for Python, G3 K11) and Go's own open rows stay with their own rounds.

## 2. Decisions (fixed before coding)

**N1 One helper, built on demand (`lattice/dncheck`).**
- `lattice/dncheck/src/DnCheck.csproj`: a `net10.0` console program, one `PackageReference` (`Microsoft.CodeAnalysis.CSharp` **5.9.0**),
  `RestorePackagesWithLockFile=true` with a committed `packages.lock.json`, restored with `--locked-mode`. Subcommands on stdin JSON,
  as gocheck: `extract` (`{"root", "workflow"?, "policy"?}` -> `lattice.dnextract/1`) and the default check (`{"root", "policy"}` ->
  findings).
- Built by `lattice.dncheck.build_dncheck()` with `dotnet build -c Release --disable-build-servers -nodeReuse:false` **in a copy of
  `src/` under the cache directory** (`$LATTICE_DNCHECK_CACHE`, else `~/.cache/lattice/dncheck/<key>/`), keyed by the sha256 of the
  sources, the lock file and `dotnet --version`; the repository's `src/` is never written (no `obj/`, `bin/` there; PW checks it).
  Run as `dotnet <cache>/<key>/dncheck.dll`. `DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1` always. Keep the newest 4 builds.
- **The target is never restored, built or written.** The compilation is the target project's `*.cs` files (minus `bin/`, `obj/`,
  `Migrations` only for the snapshot rule of N6, and files excluded by `<Compile Remove>`), the implicit global usings the csproj's SDK
  and `<ImplicitUsings>` imply (the fixed lists of `Microsoft.NET.Sdk` and `Microsoft.NET.Sdk.Web`), its `<Using>` items, its global
  using files, and **metadata references to the installed reference packs only** (`Microsoft.NETCore.App.Ref` and
  `Microsoft.AspNetCore.App.Ref` 10.0.12). The target's NuGet packages (EF Core, Pomelo, FluentValidation, JwtBearer) are **not**
  referenced: their APIs are recognised by fully qualified name through `using` directives and Roslyn's error-type names, which keep
  their spelling. So the output is the same whether or not the target was ever restored (DET-RESTORED), and the same on any machine
  with the same SDK. Project-internal symbols (classes, methods, DI registrations, extension methods, lambdas) are bound by the
  semantic model exactly.
- Determinism: files in ordinal path order, `CultureInfo.InvariantCulture` for the whole process, diagnostics by id never by message
  text, symbol display with one fixed `SymbolDisplayFormat`, no absolute path, time, host or SDK path in the output (the SDK version is
  recorded in `"dotnet"`). A missing SDK raises `DotnetToolchainMissing` (layer A then skips with a note, as Go does without `go`); a
  failed helper restore is NOT RUN with its error, never skipped silently.
- Multi-project solutions: the import's `package` is the directory of the one web project (the csproj that N12 detects);
  `<ProjectReference>`d class libraries are added to the compilation as source (their `*.cs`), recursively, deduplicated; a referenced
  project outside the import root is `unread`.

**N2 The extract document: the goextract key set, a .NET schema name.**
- The helper answers `{"schema": "lattice.dnextract/1", ...}` with **every key of `lattice.goextract/1`** (top level: `module`, `go`,
  `dialect`, `main`, `tables`, `routes`, `middleware`, `constructors`, `sentinels`, `error_writer`, `error_writers`, `auth`, `models`,
  `status_writes`, `excluded`, `unread`, `notes`; per route, table, column, param, raise, owner use, guard: the goextract field names and
  meanings; the opt-in workflow keys `param_uses`, `creates`, `param_literals`, `status_facts`, `workflow_unread`, `workflow_notes`; the
  opt-in policy keys `columns[].json`, `params[].parse`/`parse_at`, `raises[].bind`). `go` holds `""`; `"dotnet"` holds the SDK version.
- Values that differ by language keep the key and change only the spelling: `columns[].go_type` holds the C# type as written
  (`string?`, `int`, `NoteStatus`); `handler.func` is `Namespace.Type.Method` (or `Type.<lambda@file:line>` for an inline lambda);
  `handler.module` is the namespace; `file` is relative to the project directory with `/`.
- .NET-only facts go under one extra top-level key `"dotnet"` (`sdk`, `target_framework`, `providers`, `json_naming`, `di`
  registrations, `validation` (what enforces annotations, N5), `exception_map` (N9), `query_filters` (N8), `snapshot` (N6)) and per route
  `"style": "controller" | "minimal"` and `"template"` (the raw ASP.NET template). A Go document never gains a key (PB-X).
- Consumers accept the new schema through one dispatch module (`lattice/importer/extract_source.py`, N16); `go_gin.model_from_extract`
  stays frozen and still refuses anything but `goextract/1`; `dotnet_aspnet.model_from_extract` maps `dnextract/1`, reusing
  `go_gin._field`/`_param` read-only as `go_rules` already reuses `go_gin._harness_attr`.

**N3 Routes.**
- Controllers: a non-abstract public class deriving (transitively) from `ControllerBase` or carrying `[ApiController]`/`[Controller]`,
  not `[NonController]`, is a controller **only when `MapControllers()` (or `MapDefaultControllerRoute`/`MapControllerRoute`) is called
  in the app's startup**; otherwise its actions go to `excluded` ("controllers not mapped"). Attribute routes: `[Route]` on the class
  (inherited from an abstract base when the derived class has none), on the action, `[HttpGet/Post/Put/Patch/Delete/Head/Options(t)]`,
  `[AcceptVerbs]`, `[Area]` with `{area}`; tokens `[controller]` (class name minus `Controller`), `[action]`, `[area]` replaced
  verbatim; a template starting with `/` or `~/` ignores the class prefix; `[NonAction]` and non-public methods skipped. A
  `RouteTokenTransformerConvention` or any `IOutboundParameterTransformer` in `AddControllers(o => ...)` makes affected routes `unread`
  (the transformed spelling is not guessed). Conventional routes (`MapControllerRoute` patterns for actions with no attribute route)
  are `unread`.
- Minimal APIs: `MapGet/MapPost/MapPut/MapPatch/MapDelete/MapMethods` on `WebApplication`, `IEndpointRouteBuilder`, or a
  `RouteGroupBuilder`; `MapGroup(prefix)` chains followed through locals, fields and return values; groups passed to project extension
  methods (`static ... MapX(this IEndpointRouteBuilder|RouteGroupBuilder g)`) and static registration methods followed with the bound
  prefix, one call site at a time (a method called from two places yields its routes under both prefixes). A template that is not a
  compile-time constant (Roslyn `ConstantValue`) is `unread` with its file:line; a registration inside a loop is `unread`; inside an
  `if` it is kept with `condition` (the goextract field) set to the condition's source text.
- Templates: `path` keeps the raw joined ASP.NET template; `path_template` normalises each `{name:constraint=default?}` to `{name}`;
  constraints `int`/`long` set the param `type` to `Int` and `parse: "Int"` (the E1 analogue: the framework answers 404 on a mismatch,
  recorded as the parse refusal), `guid` -> `Uuid`, `bool`, `datetime`, `alpha`, `min/max/range/length/minlength/maxlength/regex`
  -> bounds or `unread` (regex); an optional `{x?}` is one route with the param `required: false`; a catch-all `{*x}`/`{**x}` is
  `unread`. `UsePathBase` with a constant prefix is prepended and noted; a non-constant one makes every route `unread`.
- Framework endpoints go to `excluded` with kind `framework`: `MapHealthChecks`, `MapOpenApi`, `MapStaticAssets`, `MapFallback*`,
  `MapIdentityApi<T>` (but see N4), `MapHub<T>` (SignalR), `MapRazorPages`, `MapBlazorHub`/`MapRazorComponents`; `MapGrpcService<T>`
  with kind `grpc` (1.5). An MVC `Controller` action returning a view (`View()`, `PartialView()`) is `excluded` ("views").

**N4 Auth evidence.**
- Member: `[Authorize]` (class or action, inherited), `.RequireAuthorization(...)` on an endpoint or any enclosing group, a global
  `AuthorizeFilter` in `AddControllers(o => o.Filters.Add(...))` (controllers only), `AddAuthorizationBuilder().SetFallbackPolicy(...)`
  or `options.FallbackPolicy = ...RequireAuthenticatedUser()` (every endpoint without metadata). Public: `[AllowAnonymous]` or
  `.AllowAnonymous()` (wins, as ASP.NET's own evaluation does), or no evidence at all. Admin: `[Authorize(Roles = "<admin>")]`,
  `RequireRole("<admin>")`, or a named policy whose registration (`AddPolicy(name, p => p.RequireRole(...))`, a constant name) requires
  that role; the admin role name is read from the registration; a policy built from a non-constant or a custom
  `IAuthorizationRequirement`/handler is `auth: "Member"` with `auth_reason` naming it **plus** an `unread` entry (never guessed Admin).
- Middleware: `app.Use(...)`, `UseMiddleware<T>()`, `IMiddleware` classes, endpoint filters (`AddEndpointFilter`), MVC filters
  (`IActionFilter`, `IAsyncAuthorizationFilter`, `TypeFilter`) are recorded in `middleware` with their reach; one that reads the request's
  credential and short-circuits with 401/403 is `authenticating` (the goextract meaning). `UseAuthentication`/`UseAuthorization`
  order is recorded; `UseAuthorization` before `UseRouting` in an app that calls both is a note.
- The caller: `HttpContext.User`, a `ClaimsPrincipal` handler parameter, `User` in a controller, read through `FindFirst`,
  `FindFirstValue`, `Identity.Name`, `GetUserId()`-style project extension methods (any project method whose body reads a claim is a
  caller function, the Go `callerFuncs` meaning) and `IHttpContextAccessor`. `auth.caller_keys` holds the claim types read.
- Login: the public POST route that loads the users table by one request field, verifies a password (`PasswordHasher<T>.VerifyHashedPassword`,
  `BCrypt.Net.BCrypt.Verify`, `Rfc2898DeriveBytes`/`CryptographicOperations.FixedTimeEquals` on a stored hash) and answers a token
  field; `login_path`, `username_field`, `password_field` and `token_field` are **wire names** (N5's naming policy). ASP.NET Identity:
  `MapIdentityApi<TUser>()` under a constant prefix is read from a fixed table of its .NET 10 endpoints (`/register`, `/login`
  `{email,password}` -> `accessToken`, `/refresh`, ...), the users table is `AspNetUsers` with `PasswordHash` private, Identity's own
  tables are `excluded` (kind `framework`); a customised Identity (`AddIdentityCore` with a custom store, overridden endpoints) is
  `unread`. (Q13 may rule Identity out of round 1.)

**N5 Model binding and bounds: a bound counts only when something enforces it.**
- Sources: `[FromRoute]`, `[FromQuery]`, `[FromBody]`, `[FromHeader]`, `[FromForm]`, `[FromServices]`/keyed services (dropped),
  `[AsParameters]` (its properties, one level), and the inference rules of each style (controllers with `[ApiController]`: complex
  types from the body, simple types from route then query; minimal APIs: route name match, then query for simple types, body for one
  complex type, services for DI-registered types, special types `HttpContext`, `CancellationToken`, `ClaimsPrincipal` dropped).
- Bounds read: `[Required]`, `[Range(a,b)]`, `[StringLength(max, MinimumLength)]`, `[MaxLength]`, `[MinLength]`, `[Length]`,
  `[AllowedValues]` (choices), `[EmailAddress]`/`[RegularExpression]` (recorded, not bounds), C# `required` members, and **implicit
  required** for non-nullable reference properties when `<Nullable>enable</Nullable>` and MVC's
  `SuppressImplicitRequiredAttributeForNonNullableReferenceTypes` is not set (controllers only); FluentValidation
  `AbstractValidator<T>` rules (`NotEmpty`, `NotNull`, `Length`, `MaximumLength`, `MinimumLength`, `InclusiveBetween`,
  `GreaterThan(OrEqualTo)`, `LessThan(OrEqualTo)`, `IsInEnum`, `Must` -> `unread`), and explicit comparison guards in the handler
  that return one constant 4xx (the Go `Body`-guard rule).
- **Enforcement** (recorded in `dotnet.validation` and per param as `enforced_by`): annotations are enforced on `[ApiController]`
  controllers (automatic 400 `ValidationProblemDetails` unless `SuppressModelStateInvalidFilter`), on minimal APIs **only** when
  `builder.Services.AddValidation()` (.NET 10) is called, and on any style where the handler calls `ModelState.IsValid`/`TryValidateModel`
  /`Validator.TryValidateObject` and refuses. FluentValidation rules are enforced only when the handler (or an endpoint filter in its
  reach) calls `Validate`/`ValidateAsync` on the validator and refuses on failure, or legacy auto-validation
  (`AddFluentValidationAutoValidation`) is on for controllers. **An unenforced annotation gives no `ge/le/min_length/max_length`**:
  it is listed in the route's `unread` as "declared, not enforced" (this is the trap a guess would fall into).
- `raises[].bind` and the module's bind status: 400 for `[ApiController]` and `AddValidation()` (the framework's ProblemDetails), a
  custom `InvalidModelStateResponseFactory` with a constant status that status, a non-constant one `unread`.
- JSON naming: the effective `System.Text.Json` policy from `ConfigureHttpJsonOptions`/`AddJsonOptions`/`AddControllers().AddJsonOptions`
  (`PropertyNamingPolicy` = `CamelCase` (the web default when unset), `SnakeCaseLower`, `SnakeCaseUpper`, `KebabCaseLower`, `null` (as
  declared)); `[JsonPropertyName]` overrides; a custom `JsonNamingPolicy` subclass is `unread` and makes every wire name `unread`. Every
  param, model field, column (`columns[].json` in the policy extract) and auth field carries its wire name. Input matching is
  case-insensitive under the web defaults (recorded, so a probe body never depends on case).

**N6 EF Core tables.**
- A table is an entity type reachable from a `DbContext` subclass (base named `DbContext`, `IdentityDbContext<...>` (N4)) through a
  `DbSet<T>` property, `modelBuilder.Entity<T>()`, an `IEntityTypeConfiguration<T>` class applied explicitly or through
  `ApplyConfigurationsFromAssembly` (every such class in the compilation), or a navigation from another entity.
- **Names, in order:** (1) the migration `*ModelSnapshot.cs`, when one exists, is EF's own record of the final model: table names,
  column names, keys, indexes, max lengths, nullability and defaults come from it; (2) otherwise `ToTable`/`[Table]`, `HasColumnName`/
  `[Column]`, then the conventions: the table name is the **`DbSet<T>` property name** (else the class name), the column name is the
  property name; `UseSnakeCaseNamingConvention()` (EFCore.NamingConventions) applies that package's documented rule and records
  `name_source: "convention:snake_case"`, any other naming plugin makes names `unread`. A disagreement between the snapshot and the
  code's configuration is a note naming both (the snapshot wins: it is what the migrations create).
- Facts: `[Key]`/`HasKey`/convention `Id`/`<Type>Id` (pk), `[Required]`/`IsRequired`/non-nullable value and reference types (nullable),
  `[MaxLength]`/`[StringLength]`/`HasMaxLength` (max_length), `HasIndex(...).IsUnique()`/`[Index(IsUnique = true)]` (unique, unique_together),
  `HasDefaultValue`/`HasDefaultValueSql` (default: the constant, or `unread`), `HasOne/WithMany/HasForeignKey`, `[ForeignKey]`, and
  convention FKs `<Nav>Id` and shadow FKs (foreign_key), `HasCheckConstraint` (checks), `HasConversion<string>()`/`HasConversion<int>()`
  and value converters on enums (the column's storage: `enum_storage: "string" | "int"`; a custom converter `unread`).
- Not read, listed `unread` with file:line: owned types (`OwnsOne/OwnsMany`), complex types, table splitting, TPH/TPT/TPC inheritance,
  keyless entities, views (`ToView`), `HasData` seeding (noted), temporal tables, JSON columns (`ToJson`).
- `dialect` from the provider call in `AddDbContext`/`AddDbContextPool`/`OnConfiguring`: `UseMySql` (Pomelo) and `UseMySQL` (Oracle) ->
  `mysql`, `UseSqlite` -> `sqlite`, `UseNpgsql` -> `postgres`, `UseSqlServer` -> `sqlserver` (no Lattice runtime: noted). Two providers
  chosen by configuration -> `dialect: ""` and `dotnet.providers` lists both.

**N7 Status and state writes.** `status_writes` (goextract meaning) from `entity.Col = <constant>` (an enum member or a string/int
literal) on a tracked entity followed by `SaveChanges[Async]` in the same request reach, from object initialisers of a new entity
(the initial state), and from `ExecuteUpdate[Async](s => s.SetProperty(x => x.Col, <constant>))` (EF 7+), each with its `guard` (an
`if` on the same column that returns one constant 4xx before the write, or the bulk update's `Where` on the column). An enum value is
recorded as the member name **and** its stored value per `enum_storage` (so a layer C invariant on SQL sees what the database holds).
A C# enum column gets `choices` from its members only when it is a status-like column: written with at least two distinct constants
or guarded (the G3 K5 two-value rule).

**N8 Ownership in LINQ.**
- `owner_uses` (`compare`, `assign`; the goextract meaning) from: a lambda predicate in `Where`, `First(OrDefault)`, `Single(OrDefault)`,
  `Any`, `Count`, `ExecuteUpdate/ExecuteDelete`'s preceding `Where`, whose conjunction (`&&` only; `||` or `!` around the term makes it
  `unread`) compares an entity column with the caller (N4); query syntax `from x in db.T where ... select x` read the same way; `Find
  (Async)(id)` followed in the same method by `if (row is null || row.OwnerId != caller) return <4xx>` (the post-load check, `compare`);
  `new T { OwnerId = caller }` / `row.OwnerId = caller` before `Add` (`assign`); a navigation chain `x.Parent.OwnerId == caller`
  (`owner_via` evidence: table, parent, fk column).
- Global query filters: `HasQueryFilter(x => x.OwnerId == <field or property read from an injected service that reads the caller>)` is
  owner evidence for **every** query on that entity, recorded in `dotnet.query_filters`; `IgnoreQueryFilters()` on a query removes it
  for that query. A filter on anything else (soft delete `!x.IsDeleted`) is recorded as a filter, not an owner.
- A predicate passed as an `Expression<Func<T,bool>>` variable, a specification object or a repository method whose body cannot be read
  to the lambda is `unread`; a repository method that can (one level, project code) is read through the call closure (N10).

**N9 Raise sites.** `raises` from: `NotFound()`, `BadRequest()`, `Conflict()`, `Unauthorized()`, `Forbid()` (403), `UnprocessableEntity()`,
`StatusCode(<const>)`, `Problem(statusCode: <const>)`, `ValidationProblem()` (the bind status), and their `Results.*` and `TypedResults.*`
forms, `NoContent`/`Ok`/`Created*`/`Accepted*` (success status codes, `status_code`/`status_codes`); `code`/`detail` from a constant
`detail:`/`title:` argument or an anonymous object's constant `code`/`error`/`message` member. Thrown exceptions: a project exception
type mapped to a status by an `IExceptionHandler` implementation, `UseExceptionHandler(...)` lambda, a custom middleware `catch
(T)` block that sets a constant `StatusCode`, or a `switch` on the exception's type with constant statuses (`ex switch { NotFoundException
=> 404, ... }`) is a raise with `kind: "exception"` and `sentinel` = the type; `AddProblemDetails` with `StatusCodeSelector` read the same
way; an unmapped thrown type is `unread` ("thrown, no constant mapping": the framework answers 500). Result wrappers (`ErrorOr`, `OneOf`,
`FluentResults`) are read one level when a project method maps them to `IResult` with constant statuses; otherwise `unread`.

**N10 Call closure.** `calls` and the reach used by N7-N9 and the checker: project methods called from the handler, followed to
`HELPER_DEPTH = 3` (pycheck's and gocheck's value), through: direct calls, extension methods, local functions, lambdas passed to project
methods, constructor-injected services **resolved through the DI registrations** (`AddScoped/AddTransient/AddSingleton<IService,
Impl>()`, `Add*(typeof(I), typeof(Impl))`, `Add*<Impl>()`, keyed services by constant key): an interface with exactly one registered
implementation resolves to it; zero or two or more registrations, factories with non-trivial lambdas, assembly scanning (Scrutor) and
open generics are `unread` at the call site (never the first match). `async`/`await`, `Task` wrappers and `ConfigureAwait` are
transparent. A call into a type outside the compilation is opaque unless it is a known framework API listed in N3-N9; an opaque call
that receives the `DbContext`, an entity, or `HttpContext` makes the reach incomplete (the gocheck rule), and a route with an
incomplete reach is not judged by DN801 (silence).

**N11 Unread and excluded.** Every construct the extractor recognises the start of but cannot finish (sections N3-N10 name them) is one
`unread` entry `{file, line, what, reason}` (route-level and top-level, the goextract meaning); every endpoint-like registration that is
not a route is one `excluded` entry with its kind. **0 silent drops** is a gated property (H-UNREAD): every truth route is either a
route of the extract or named (file:line) in `unread`/`excluded`.

**N12 The front end `dotnet_aspnet.py` (name `"aspnetcore"`, orm `"efcore"`).**
- `detect(root, package)`: exactly one `*.csproj` directly in `<root>/<package>` (two or more: False with a note naming them; the
  user passes the project directory) that is a web project (`<Project Sdk="Microsoft.NET.Sdk.Web">`, or a `<FrameworkReference
  Include="Microsoft.AspNetCore.App"/>`, or a `PackageReference` to `Microsoft.AspNetCore.*`) **and** references EF Core (a
  `PackageReference` to `Microsoft.EntityFrameworkCore*`, `Pomelo.EntityFrameworkCore.MySql` or `MySql.EntityFrameworkCore`, in the
  project or a `<ProjectReference>`d project, or central package management's `Directory.Packages.props`). The brief's "csproj
  with Microsoft.AspNetCore" alone would miss every `Sdk.Web` project (they name no AspNetCore package), so the SDK form is included.
- Loaded lazily: `frontends._load_dotnet()` runs only on `select("aspnetcore")` or after every Python **and** Go front end declined (Go
  detection needs a `go.mod`, .NET a `*.csproj`: disjoint). A Python or Go import never imports `dotnet_aspnet` (its string literals
  must not join the request suite's Hypothesis pool: the `frontends/__init__` rule; PB-LAZY).
- `extract` -> `RepoModel` with `framework="aspnetcore"`, `orm="efcore"`, models from the DTOs (so `emit_spec._contract_params` sees
  real body models; the Go K3 synthesis is not needed), and `last_extract`/`sidecar()` as Go's.
- `runtime`: the harness's `RUNTIME` (`studio.toml [app] runtime`), else `process_runtime(framework="dotnet", auth=...)` logging in
  through the extracted login route (wire names). `reconcile`: Go's method's contract (each extracted route != 404/405 on the app's
  binary over a throwaway `mysql:8.4` named `lattice-dnx-*`, removed in `finally`).

**N13 Runtime and databases.** `process_runtime(framework="dotnet")` (no `process.py` change: `framework` is a free string, r). The
harness starts a prebuilt app (`dotnet <bin>/<App>.dll` or the apphost) with `PORT`, `ASPNETCORE_ENVIRONMENT=Production`, and the
database in its `db_env`; the app binds `http://127.0.0.1:$PORT` only and answers `/health` 200. On MySQL the harness translates the
canonical URL to an ADO.NET connection string itself (`HARNESS_API_DOTNET.md` gives the function); a library driver entry
`adonet-mysql` in `lattice/spec/engines/mysql.toml` is added **only on Q11 yes** (one line, if its template fields allow it). SQLite:
`Data Source=<absolute path>`. Lattice never builds the app: the runner builds a scratch copy (`dotnet build -c Release --locked-mode`)
and the harness reads the binary path from an environment variable.

**N14 Layer A for .NET (`dncheck`, codes DN800-DN808, the PY8xx meanings).**

| code | guarantee (one meaning, `docs/lattice-language-rules.md` R1) | .NET reading | status |
|---|---|---|---|
| DN800 | the app's code does not parse | Roslyn syntax diagnostics of severity Error in any compiled file (ids only) | active |
| DN801 | ownership | a route (complete reach, N10) that reads, updates or deletes rows of an owned table (policy `owners`/`owner_via`) through a query with no owner evidence (N8) for that table, outside `open_routes`/`admin_routes` | active |
| DN802 | private columns | an entity whose private column (policy) is serialized: the entity, a collection of it, or an anonymous/DTO projection copying the column, returned through `Ok`/`Results.Ok`/`TypedResults.Ok`/`return entity`/`Json`, unless the property carries `[JsonIgnore]` (Never or WhenWritingNull/Default with a provably null value is not claimed) | active |
| DN803 | bounds | a request value (N5) written to a column or used as `Take(n)`/`Skip(n)`/page size with no enforced bound (N5) | active |
| DN804 | route order | moot: endpoint routing orders by template precedence (a literal segment beats a parameter whatever the registration order); two equal-precedence templates throw `AmbiguousMatchException` at request time, which is a note (`ambiguous routes`), not DN804 | **gated** (reason as text, `DN_GATED`) |
| DN805 | error conventions | the module's not-owned answer derived structurally (an owner-scoped lookup whose null/empty result is tested by the next statement and refused with one constant 4xx; every such site sharing one status, else `unclear` and silent); a route refusing an owner-scoped miss with another status. The G3 K8 rule, applied to .NET | active **on Q9 yes**, else gated with reason |
| DN806 | naive/aware datetimes | **active for .NET** (unlike Go, where it is moot): a comparison, subtraction or `Max/Min` between a `DateTime` of known Local kind (`DateTime.Now`, `DateTime.Today`, `.ToLocalTime()`) and one of known Utc kind (`DateTime.UtcNow`, `.ToUniversalTime()`, `DateTimeOffset.UtcDateTime`, `DateTime.SpecifyKind(..., Utc)`), or a `DateTime` of Local/Unspecified kind implicitly converted to `DateTimeOffset` and compared with an offset value; unknown kinds stay silent (EF reads `DateTime` as `Unspecified`: a column read compared with `UtcNow` is **not** claimed) | active |
| DN807 | partial writes | in one request reach, a `SaveChanges[Async]` followed by a refusal (any 4xx raise, a mapped throw) or a second `SaveChanges`, outside `Database.BeginTransaction[Async]` ... `Commit`, `TransactionScope`, or `CreateExecutionStrategy().Execute[Async]` wrapping both | active |
| DN808 | state machines | (only with policy `machines`) a status write (N7) from a guarded state with no edge to the written state; enum members mapped to stored values by `enum_storage` | active |

- Python API mirrors gocheck: `DnPolicy` (the `GoPolicy` fields; `from_spec` the same algorithm), `run_dncheck(src_dir, policy, strict=False)`,
  `all_dn_findings`, `DN_CODES`, `DN_GATED`, `gated_note()`; findings are `lattice.pycheck.Finding`s keyed `DNxxx|<METHOD> <template>|<Table>[.<col>]`
  with the full ASP.NET template (prefixes included). `lattice/dncheck/derive.py`: `policy_extract`, `derive_dn_policy` (owners from
  `owner_uses` by the go_rules D2 four-condition rule, private from `[JsonIgnore]` minus the pk, machines from N7, the original's own
  findings as `baseline`), `dn_spec_policy` with G3 K6's table identity (a derived table equal by SQL name to a declared one is that
  table; undeclared factless tables dropped and listed).
- `lattice/spec/check.py` gains one branch: `runtime.framework == "dotnet"` (or a `dn_src`) -> `_layer_a_dotnet` (the `_layer_a_go`
  shape: `DnPolicy.from_spec`, findings with `layer="A"`, a missing SDK a note). Unfreezing `check.py` is Q10.

**N15 The rules-first import (`lattice/importer/dotnet_rules.py`, `rules.py` branches).** The Go path with .NET inputs: `harness_factory`
(from `studio.toml [app] app`, dotted form), `InferringFrontEnd` applying, each recorded in `inferences[]` with its `rule`: `owner_uses`
(the D2 rule), `route_constraint` (path params typed by N3 constraints: the `path_parse` analogue), `bind_status` (N5), `enum_choices`
(N7's status-like enums, default first), `json_ignore` (private columns), `json_naming` (the wire-name map the profile uses, so a T2 item
looks for `passwordHash` or `password_hash`, never `PasswordHash` unless that is the wire name). `patch_profile` reuse for
`validation_status` and `private`. On a server-database app (`studio.toml [app] database` = `mysql`/`postgres`) the import does what G3
K4 does: `rules.status = not_run:server_dialect`, contracts and inferences only, exit 0, no Docker, no SQLite file (reused from G3 if
merged, Q1; otherwise implemented for the .NET branch within `rules.py`'s budget).

**N16 Workflow layer and consumers.**
- `lattice/importer/extract_source.py` (new, <= 90 lines): `front_end_for(root, package)` (the Go front end when it detects, else the
  .NET one when it detects, else a `LookupError`), `extract_json(root, package, workflow=False)`, `model_from_extract(doc)` dispatching
  on `schema`, `source_suffixes(frontend)` (`go_gin`: `.go`, `go.mod`, `go.sum`; `dotnet_aspnet`: `.cs`, `.csproj`, `.sln`, `.slnx`,
  `Directory.Build.props`, `Directory.Packages.props`, `packages.lock.json`, `global.json`, minus `bin/`, `obj/`).
- `workflow_run.py`: `extract()` and the runtime use the dispatch; `"framework"` comes from the front end (`gin` stays `gin`).
  `inventory.py`: `"frontend"` from the dispatch (`go_gin` stays `go_gin`). `implementation.py`: `source_files` takes the suffix set
  from the inventory's `frontend` (a Go inventory reads exactly today's set). `impact_tier3.py`: the code-file suffix from the
  inventory's `frontend`. `workflows.py`, `appmap.py`, `ask.py`, `mcp/tools.py` (`_CODE_EXT` already has `.cs`, r), `impact_report.py`,
  `review.py`, `emit_spec.py`, `mine.py` are **frozen**: the claim is that they work on a `dnextract/1` document unchanged.
- The C# `Workflow` pass fills `param_uses` (a route param used as a key in a lookup of table T), `creates` (`db.Add`, `db.T.Add`,
  `AddRange` of a new T, with the key source), `param_literals`, `status_facts` (N7 with `from` states), `workflow_unread`.

**N17 Never.** No write into a fixture, a target, Streak (`~/Documents/streak`) or the main checkout (`~/Documents/lattice`); Streak's
three input files reach A1 as copies made with `git show main:<file>` into A1's directory. No model, no `claude` except the four separate
authors/writers (section 4) dispatched by the orchestrator through the `claude` CLI; no API key anywhere. No `docker` outside the rows that
name it (a recording shim is first on `PATH` everywhere else). No `dotnet build`/`restore` inside a target tree or inside
`lattice/dncheck/src` (copies only). No edit to a frozen file. Build nodes never commit and never `git add`.

## 3. Stages, gates and the agent graph (at most 4 agents in parallel)

| stage | node(s) | model | depends on | gate |
|---|---|---|---|---|
| 0a | **Amendment 1**: the orchestrator's rulings on section 13 | — | this file | — |
| 0b | **A1** writes D1 (Streak in .NET) in an isolated directory; contract check (4.2); **W0** writes D1's six dev mutants (6.3), hashed | A1: per Q5; W0: sonnet, `claude` CLI | Amendment 1 | D1 contract (4.2) |
| 0c | **B0** step 0: the Arm 0 fixture `tests/dncheck/fixtures/dnparity1/` + `truth.json` written **before library code**; recon scripts RC1-RC6 (section 5.1) | sonnet, own worktree | Amendment 1 | none; outputs hashed |
| 0d | **Amendment 2**: hashes of `dnparity1/**`, D1, D1's truth and W0's patches, RC outputs, any decision RC refutes (re-registered here, before code) | — | 0b, 0c | — |
| 1 | **B1** the helper core and `extract` (N1-N11), `lattice/dncheck/__init__.py` (build/run/extract) | sonnet, own worktree | Amendment 2 | Stage 1 rows (5.2), dev |
| 2-5 (parallel) | **B2** shim + rules-first import (N12, N15); **B3** layer A (N14); **B4** workflow pass + consumers (N16) + `dnapp` (4.4, Q8); **R** the runners `dogfood/dotnet_probes.py`, `dogfood/dotnet_mutants.py`, stand-ins | sonnet, four worktrees, files disjoint (section 9) | Stage 1 gate | Stage 2-5 rows (5.3-5.6), dev |
| freeze | orchestrator merges B1-B4 + R, runs `--self-test` on every arm, records **Amendment 3** (sha256 of every changed/created file, both runners, stand-ins, `HARNESS_API_DOTNET.md`, the H1 brief, the ledger path), commits it **before** A2 is dispatched | — | 2-5 | self-test 100%; RM1, RM2 caught |
| 6a | **A2** writes H1 (isolated) -> contract check (4.3) -> **W** writes H1's 16 mutants -> **Amendment 4** (H1 and patch hashes) | sonnet, `claude` CLI | Amendment 3 | H1 contract |
| 6b | one scored run of every arm (orchestrator) -> **V** independent opus re-run -> the parity matrix J0-dn document (V checks every cell) -> results | V: opus | Amendment 4 | Stage 6 rows (5.7-5.9) |

Every stage reports its own verdict. **DN1 passes** when every gated row of Stages 1-6, every mutant gate, both runner mutants and the
byte-compat promise pass. A partial merge of passing stages is a question (Q16).

## 4. Corpus

### 4.1 `HARNESS_API_DOTNET.md` (orchestrator-written, checked on D1, hashed in Amendment 3; given to A1 and A2)
Every fact, complete: `build_db(path_or_url)` creates the schema and the seed rows and **returns its argument**; `make_app(path_or_url)
-> ProcessApp(argv, db_path, env, cwd, health_path="/health", startup_timeout, port_env="PORT", db_env, host="127.0.0.1",
inherit_env, stop_timeout, request_timeout)` with every field explained; `RUNTIME = process_runtime(framework="dotnet",
auth=AuthDriver.json(<login path>, USERS, username_field=..., password_field=..., token_path=(...), scheme="bearer"))`; `LOGIN`;
`USERS` and the Seeder's plain-password convention (the importer's seed users and how their passwords are stored); `SERVICES` (for a
background worker: the `lattice.spec` services contract) when the product has one; the `studio.toml [app]` keys (`app`, `db`, `login`,
`runtime`, `services`, `database`) with the **dotted** module form `tests.spec.<x>_harness:make_app` required; the MySQL URL -> ADO.NET
connection string function (Pomelo and Oracle forms) and the SQLite `Data Source=` form; `PORT` binding on 127.0.0.1 only; the app
must start in under 10 s and never write outside its database and a temp directory; SDK 10.0.401, `net10.0`, `packages.lock.json`
committed, `dotnet build -c Release --locked-mode` must pass from a clean copy; both MySQL providers' restore facts (1.1); MySQL 8.4
throwaway containers come from Lattice (`lattice.spec.dbserver`), the app never starts one.

### 4.2 D1: Streak in ASP.NET Core (dev; author A1)
- **Inputs, only:** Streak `main` a0db033's `REQUIREMENTS.md` (259 lines, r), `spec.py` (1829 lines, r: 9 tables incl. `test_clock`, 90
  scenarios, an access matrix, one machine `Nudge.delivery` pending -> sent | failed | suppressed), `CONTEXT.md` (28 lines), plus
  `HARNESS_API_DOTNET.md`. Not the Go implementation, not its harness, not any Lattice source.
- **Brief (dev, so coverage may be required):** C# on ASP.NET Core (.NET 10 SDK), EF Core with migrations, MySQL 8.4 as the product
  database, **controllers and minimal APIs mixed** (each style serving at least a third of the routes), the test clock and the nudger
  worker as `CONTEXT.md` describes, wire names exactly as the spec states, organised as a production service. Q3 decides whether a
  second provider (SQLite, selected by an environment variable) is required for the import arms.
- A1 may run one fixed `check.sh` the orchestrator provides (`python -m lattice.spec check --spec spec:SPEC --layers CD --database mysql
  --runtime <its harness>:RUNTIME`, Docker), which prints scenario names and pass/fail only (Q5). A1 never runs the importer, layer A,
  workflows or anything of `lattice.dncheck` (it does not exist yet).
- **Deliverables:** the project (`tests/spec/dnstreak/`, no `bin/`/`obj/`), `tests/spec/dnstreak_harness.py`, `studio.toml`, and
  `truth.json` in the schema of 4.5 with a file:line for every fact.
- **Contract check (before Amendment 2):** clean-copy `dotnet build -c Release --locked-mode` 0 errors; the harness smoke (`build_db`
  returns its argument; `make_app` starts; `/health` 200; every seed user logs in); `check.sh` on MySQL: **every access-matrix case and
  >= 85 of 90 scenarios pass** (Q6), the failing names frozen in Amendment 2; every truth route answers neither 404 nor 405 to an
  authenticated call; `truth.json` validates and every file:line exists. One author fix round is allowed and logged.

### 4.3 H1: the held-out (blind; author A2, after Amendment 3)
**Brief rules (binding; the orchestrator writes the brief, hashed in Amendment 3).**
1. Product terms only: "a small multi-user HTTP JSON service in C# on ASP.NET Core (.NET 10 SDK) with EF Core over SQLite
   (`Microsoft.EntityFrameworkCore.Sqlite`), 10-16 routes, a login, per-user data on at least two tables, at least one table whose rows
   belong to a user through a parent row, a column that is never returned, a status column with at least two transitions, request
   validation; organise it the way you would organise a production service." The domain is the author's choice and must not be
   habits, tickets, runs, jobs or notes. (Q3 may add MySQL as a second provider.)
2. **No idiom is named:** no mention of controllers, minimal APIs, route groups, filters, middleware, policies, claims, token formats,
   hashing, validation libraries or attributes, JSON attributes or naming policies, DI, repositories, exception handling style,
   query filters, migrations versus `EnsureCreated`, or anything from section 2. No Lattice internals.
3. `HARNESS_API_DOTNET.md` complete (4.1); dotted harness module form required.
4. Deliverables: the module (`tests/spec/<author's name>/`), its harness, `studio.toml`, `truth.json` (4.5).
5. One fixed scratch directory; the orchestrator audits every tool call from the transcript: 0 accesses outside it, 0 reads of
   `lattice/**`, `docs/**`, `dogfood/**`, `tests/**` of the repository.

**Contract check (before scoring; one logged author fix round):** clean-copy build 0 errors and 0 warnings promoted to errors; the
harness smoke; every truth route answers neither 404 nor 405 to an authenticated call; `truth.json` validates and every file:line exists.
**Nothing of Lattice runs on H1 before the scored run.**

### 4.4 `dnapp` (dev; build node B4, on Q8 yes)
A .NET port of `tests/spec/goapp` (the Go S1 app) on SQLite, <= 450 lines of C#, answering the same API so that `tests/spec/go_harness_rb.py`'s
spec, programs and rules run against it through a new `tests/spec/dn_harness_rb.py` (same spec object imported, a different `make_app`).
It carries the same `GOAPP_BUG`-style variants (`nocheck`, `nocancel`) under `DNAPP_BUG`. Purpose: layers B and R on a .NET app with
hand-written programs (J0 rows 6 and 10), compared item by item with Go S1 (m: R 23 items, 16 verified; B 32 cases, 0 findings).

### 4.5 `truth.json` schema (fixed now; D1 and H1)
`routes[{method, template (full, as registered), auth: Public|Member|Admin, style: controller|minimal, file, line}]`;
`tables[{entity, table, columns[{name (SQL), wire (JSON name or null if never serialized), private, nullable, unique, pk, max_length,
fk}]}]`; `owners {entity: column}`; `owner_via {entity: [parent_entity, fk_column]}`; `machines {"Entity.column": {initial, edges[[from,
to]], storage: string|int}}`; `refusals[{route, status, detail, cause: auth|not_found|not_owned|invalid|conflict|other, file, line}]`;
`bounds[{route, field (wire), required, ge, le, min_length, max_length, enforced_by, file, line}]`; `bind_status`; `json_naming`;
`login {path, fields (wire), token_field}`; `seed_users[{name, role}]`; `not_returned[{entity, column}]`.

**Matching rules (fixed now, so no scoring choice is made after a number is seen):** a route matches when the method is equal and the
templates are equal after lowercasing literal segments, replacing every `{...}` by `{}` and dropping a trailing `/`; a table by SQL
name (case-insensitive on MySQL, exact on SQLite); a column fact by (table, column, fact); an owner by (entity, column); a refusal by
(route, status); a bound by (route, field, bound kind, value).

### 4.6 D2 (optional; Q7)
A job-scheduler-style second dev app by a separate author A3 from `~/Documents/job-scheduler-go`'s spec in product terms (membership
tenancy). Rows reported only, never gated. Default: not in round 1.

## 5. Probes, predictions and pass rules

Runner: `dogfood/dotnet_probes.py --arm 0,D,N,H,M,X` (Arm 0 fixture, D1, `dnapp`, H1, MySQL rows, cross); `--self-test`; mutants
`dogfood/dotnet_mutants.py`. Every value below is a prediction (p) unless labelled.

### 5.1 Stage 0 recon (B0; outputs hashed in Amendment 2; not gated, a refuted assumption re-registers its decision before code)
| id | question | expected (p) |
|---|---|---|
| RC1 | a Roslyn compilation of D1 with only the two reference packs binds every project-internal call and leaves EF/Pomelo types as named error types | yes; 0 project symbols unbound |
| RC2 | the implicit usings of `Microsoft.NET.Sdk.Web` for SDK 10.0.401 (read from the SDK's targets) | the fixed list recorded byte-for-byte |
| RC3 | D1's snapshot exists and states snake_case column names | yes if A1 used migrations (Q3) |
| RC4 | D1's JSON naming policy | `SnakeCaseLower` or per-property `[JsonPropertyName]` (the spec's wire names are snake_case, r) |
| RC5 | helper build wall time cold / warm | 20-60 s cold (restore of Roslyn from the cache), < 1 s warm |
| RC6 | extract wall time on D1 | 2-8 s |

### 5.2 Stage 1: extractor (Arm 0 exact; D1 dev)
`dnparity1` (parse-only; never built; written before library code) holds, each with its truth: (c1) an `[ApiController]` controller under
`[Route("api/[controller]")]` with `[Authorize]`, one `[AllowAnonymous]` action, `{id:int}` routes; (c2) a derived controller inheriting
an abstract base's `[Route]`; (m1) `MapGroup("/v1").RequireAuthorization()` with a nested `MapGroup("/admin").RequireAuthorization(p =>
p.RequireRole("Admin"))`; (m2) a project extension `MapTodos(this RouteGroupBuilder g)`; (m3) a route under `if (app.Environment.IsDevelopment())`;
(m4) a method-group handler; (m5) `MapMethods` PATCH; (m6) a non-constant template; (x1) `MapGrpcService<T>()`, `MapHub<T>()`, `MapHealthChecks`;
(x2) a conventional `MapControllerRoute` for an attribute-less controller; (b1) one DTO used by a controller (enforced), by a minimal API
without `AddValidation` (not enforced) and with a FluentValidation validator called in the handler (enforced); (b2) implicit required
under `<Nullable>enable</Nullable>`; (e1) entities `User` (`PasswordHash` `[JsonIgnore]`), `Note` (owner `OwnerId`, enum `Status` with
`HasConversion<string>()`), `Tag` (owned through `Note`), an `IEntityTypeConfiguration<Note>` with a unique index and `ToTable("notes")`, a
TPH pair, an owned type; (o1)-(o4) owner predicates: conjunction, find-then-check, unscoped find, a global query filter plus one
`IgnoreQueryFilters()`; (s1)-(s2) a guarded status write and an `ExecuteUpdateAsync` transition; (r1) every N9 raise form, an
`IExceptionHandler` switch, an unmapped throw; (d1) one service with one registration, one interface with two; (dt1) `DateTime.Now` vs
`DateTime.UtcNow`, and a `DateTimeOffset` comparison; (pw1) two `SaveChangesAsync` with a refusal between, and the same inside
`BeginTransactionAsync`; (j1) `SnakeCaseLower` naming.

| id | probe | prediction (p) | pass |
|---|---|---|---|
| P0-ROUTES | routes of `dnparity1` vs truth (method, full template, style, condition) | exact | exact |
| P0-AUTH | auth per route | exact (Public/Member/Admin; the admin route Admin) | exact |
| P0-EXCL | `excluded` and `unread` route-level entries | grpc, hub, health excluded; m6, x2 unread; nothing else | exact |
| P0-BIND | params, sources, bounds, `enforced_by`, bind status 400 | exact; the minimal-API copy has no bounds and one "declared, not enforced" unread per annotation | exact |
| P0-TABLES | tables, SQL names, column facts, `enum_storage`, wire names | exact; TPH and owned type `unread` | exact |
| P0-OWNER | `owner_uses`, `query_filters`, `owner_via` evidence | exact | exact |
| P0-STATE | `status_writes` with guards; `status_facts` | exact | exact |
| P0-RAISE | raises (status, code, kind, sentinel) per route | exact; the unmapped throw `unread` | exact |
| P0-DI | calls through one registration resolved; the two-registration call `unread` | exact | exact |
| P0-JSON | `dotnet.json_naming` and every wire name | `SnakeCaseLower`; exact | exact |
| DET-10 | extract 10x on `dnparity1` and D1 | 10/10 byte-identical | 10/10 |
| DET-RESTORED | extract on a D1 copy after `dotnet restore` + build (obj/, bin/ present) vs a clean copy | identical | identical |
| DET-PATH | extract on D1 copied to two different absolute paths, and under `LANG=de_DE.UTF-8` | identical | identical |
| D-ROUTES | D1 routes vs truth (4.5 matching) | recall 1.00, precision 1.00 (26 spec endpoints, m at a0679e9, plus `/health` if it is a route) | recall **1.00**, 0 extra (dev; Spike A's Streak gate) |
| D-AUTH | D1 auth | exact on 26/26 (p 0.8); else >= 0.95 | >= **0.95** |
| D-TABLES | D1 tables and column facts | 9/9 tables; column facts 0.95-1.00 | tables 9/9; facts >= **0.95** |
| D-WIRE | D1 wire names vs truth `wire` | 1.00 | >= **0.98** |
| D-OWN | D1 owner evidence: every truth owner-scoped route has an `owner_uses` or query-filter entry for its table | 0.85-1.00 | >= **0.85**; 0 evidence on a table truth says is not owned |
| D-RAISE | D1 refusals (route, status) | recall 0.75-0.95; precision 0.95-1.00 | precision >= **0.95**; recall >= **0.70** |
| D-UNREAD | every D1 truth route is a route or named in `unread`/`excluded` | 0 silent drops | **0** |
| D-X | extract wall on D1 (helper prebuilt) | 2-8 s | <= **60 s** |

### 5.3 Stage 2: front end
| id | probe | prediction (p) | pass |
|---|---|---|---|
| F-DETECT | `select(None, root, pkg)` on D1, `dnparity1`, a `Sdk.Web` project without EF Core, a two-csproj directory, every Python and Go fixture | aspnetcore, aspnetcore, LookupError, LookupError with the note, unchanged answers for the others | exact |
| F-MODEL | `model_from_extract` on D1: routes, tables, models (DTOs as body models), auth (wire names), `framework`/`orm` | equal to the extract's counts; login path, fields and token field exact vs truth | exact |
| F-RT | `runtime()` with the harness and without it (`process_runtime(framework="dotnet")`) | as stated | exact |
| F-RECON | `reconcile` on D1 (MySQL 8.4, Docker) | every extracted route observed (!= 404/405); control 404; container removed | all observed; 0 leftover containers |
| PB-LAZY | after a FastAPI import, a Flask import and a Go import: `"lattice.importer.frontends.dotnet_aspnet" not in sys.modules`; the request suite's literal pool unchanged | as stated | exact |

### 5.4 Stage 3: layer A
| id | probe | prediction (p) | pass |
|---|---|---|---|
| A0-DN | `dncheck` on `dnparity1` under its truth policy | DN801 on o3 and on the `IgnoreQueryFilters` route only; DN802 on the route returning `Note` with a private column copied by projection (not on `User` with `[JsonIgnore]`); DN803 on the minimal-API copy writing the unenforced field; DN806 on dt1's first case only; DN807 on pw1's first case only; DN808 on the planted bad edge; DN800 on a deliberately unparsable file in a sibling fixture `dnparity1bad` | exact keys |
| A0-GATE | `gated_note()`, `DN_GATED` | DN804 (and DN805 unless Q9) with their texts | as stated |
| A-D1 | layer A on D1 under `DnPolicy.from_spec(Streak SPEC)` and under `dn_spec_policy` | **0 findings** (range 0-2) | **0**; each finding listed and classified; a false positive fails the row |
| A-JUDGE-D1 | share of D1's truth owner-scoped routes judged (complete reach) | 0.70-0.95 | reported (dev) |
| A-DM | D1 dev mutants DM1-DM4 (6.3) | each killed by its registered detector | 4/4 |
| A-CHECK | `python -m lattice.spec check --layers A --runtime dotnet` (or the harness runtime) on D1 | runs layer A through `_layer_a_dotnet`; 0 findings; without the SDK on `PATH`: a note, exit as today | as stated |
| A-DERIVE | `derive_dn_policy` on `dnparity1` and D1 | owners and private exactly the truth's; baseline empty on D1 | exact |
| A-805 | (Q9 yes) the not-owned derivation on `dnparity1` and D1; a D1 copy answering 403 on a scoped miss | `dnparity1` 404; D1 404 (p 0.7) or `unclear`; `DN805|GET /v1/habits/{id}|not_owned` on the copy | exact on the fixture and the copy |

### 5.5 Stage 4: rules-first import (D1 with the SQLite provider if Q3 gives one; `dnapp`; H1 in 5.7)
| id | probe | prediction (p) | pass |
|---|---|---|---|
| I-D1-1 | `python -m lattice.importer <D1 copy> --package . --rules-only --framework aspnetcore` (SQLite): framework, routes, contracts, tables | aspnetcore; 26 or 27 routes and as many contracts (26 spec endpoints, m at a0679e9's spec and recounted on a0db033 in Amendment 2, plus `/health` when it is a route rather than a framework health check); 9 tables | exact vs truth |
| I-D1-2 | `inferences[]` | `owner_uses` for every truth owner table, `json_ignore` for the private columns, `enum_choices` `Nudge.delivery`, `route_constraint` per constrained param, `bind_status` 400, `json_naming` 1 | exact counts vs truth |
| I-D1-3 | rule items on the seed | 60-110 items; verified share **0.50-0.70**; refuted 0-4 | share >= **0.50**; every refuted item has a correction or a baseline entry; every unexercised item a reason |
| I-D1-4 | the corrected spec on `variant2` | >= 0.95 retained | >= **0.95** |
| I-D1-5 | `proposals.json` | includes the machine `Nudge.delivery` (p 0.6) | reported |
| I-D1-6 | `review list` | exit 0; ids = the not-verified ids | exact |
| I-D1-7 | the import 10x | identical after the J1 timing normaliser | 10/10 |
| I-D1-PR3 | layer R with the imported `candidate_spec.py` | the same verdict map | identical after the correction map |
| I-D1-T2 | T2 items of the private columns | present and keyed by wire name; verified | all verified |
| I-D1-M | the same import on the MySQL `studio.toml` | `rules.status = not_run:server_dialect`, `dialect = mysql`, the I-D1-1 contract count, 0 rule items, 0 docker calls, no SQLite file | exact |
| N-R1 | (Q8) layer R on `dnapp` with `go_harness_rb`'s spec and rules | 23 items (m, Go S1), verified share 0.60-0.80, refuted 0 | refuted 0; share within **0.10** of Go S1's 0.6957 |
| N-R2 | (Q8) R on `DNAPP_BUG` copies (the JM1/JM2 analogues) | each refutes >= 1 item on `GET /runs/{rid}` | both |
| N-B1 | (Q8) layer B on `dnapp`, safe and `nocheck`/`nocancel` | 0 findings safe; >= 1 on the bugged route each | as stated |
| N-10 | (Q8) R and B 10x | identical | 10/10 |

### 5.6 Stage 5: workflow layer and consumers (D1 dev)
| id | probe | prediction (p) | pass |
|---|---|---|---|
| W-D1-1 | `workflows` on D1 (static, then observed on MySQL through the harness, Docker): the Streak flows register -> login -> create habit -> check in -> progress; batch completion; device registration; the `Nudge.delivery` machine | 4/4 flows and the machine recovered (p 0.6), else 3/4 | >= **3 of 4** flows and the machine |
| W-D1-2 | replay | 0 flows marked `verified` fail on replay | **0** |
| W-D1-3 | `workflows --static` 10x | identical | 10/10 |
| W-D1-4 | Docker teardown | 0 leftover containers | 0 |
| C-INV | `import inventory` on D1 | `frontend: dotnet_aspnet`; route and table counts equal the extract; source set = `.cs`/`.csproj`/... minus `bin/`, `obj/` | exact |
| C-IMPL | `implementation` report on D1 with Streak's `spec.py` | 26 required rules' routes `implemented` (verified where a workflow covers them); 0 `required-not-implemented`; health `implemented-not-required` | exact |
| C-IMPLM | the same with one route removed from a spec copy; and with a D1 copy missing two routes | exactly 1 `implemented-not-required` naming it; exactly 2 `required-not-implemented` | exact |
| C-CITE | every `code:<path>:<line>` citation in the report, `workflows.json`, the app map and the chat answers of C-ASK resolves (file exists, line exists, quoted text matches) | 100% | **100%** |
| C-ASK | `spec ask` on D1's inventory: the digest has an `impl:` section; three fixed questions ("is there a sync endpoint?", "can I check in a habit?", "who can dismiss a nudge?") answer with the expected kinds (`not_implemented`, `implemented`, `implemented` with the auth) | as stated (the questions are written here, before any code) | kinds exact; citations per C-CITE |
| C-MCP | MCP `implementation_get`, `workflow_get`, `workflow_find("check in")` on D1 | non-empty, `.cs` citations | as stated |
| C-MAP | app map on D1 | state not `inventory_missing`; rules, workflows, machine and access cells present | as stated |
| C-IMP | `lattice spec impact` T3 on the first 5 changes of the Spike D Streak change set (archived; hash in Amendment 2; if not retrievable, 5 changes written by W0 in spec terms) | every change lists >= 1 `code:*.cs:<line>` location; all resolvable | 5/5 |
| C-PERF | `lattice perf` on D1 over MySQL (server counting) | runs; statement counts reported | runs |
| C-LIVE | `lattice contracts live` on D1 over MySQL | runs; 0 contract violations on the original | 0 |
| C-IMPROVE | `lattice improve report` over a D1 check's event log | runs; byte-identical 2x | runs; identical |
| C-REBUILD | the Studio rebuild loop on a scratch copy of D1 with a scripted agent (no model) and the gate `dotnet build && ./check.sh` | gate runs; the loop records rounds as for Go | runs (row 19 `P` unless Q12 approves a real-agent arm) |

### 5.7 Stage 6, Arm H: held-out H1 (blind; scored once)
| id | probe | prediction (p) | pass |
|---|---|---|---|
| H-ROUTES | routes vs truth | recall 0.85-1.00; 0 extra | recall >= **0.90**; **0 extra** |
| H-AUTH | auth exact share of matched routes | 0.80-1.00 | >= **0.85** |
| H-TABLES | table recall; column-fact accuracy | 1.00; 0.85-1.00 | tables >= **0.90**; facts >= **0.90** |
| H-WIRE | wire names | 0.90-1.00 | >= **0.95** |
| H-OWN | owners inferred | every truth owner inferred or `unclear` naming it (recall 0.5-1.0); **0 confident-wrong** | 0 confident-wrong |
| H-BOUNDS | bounds vs truth | precision 1.00 (0 bound claimed that truth says is not enforced); recall 0.5-0.9 | precision **1.00**; recall reported |
| H-RAISE | refusals (route, status) | precision 0.90-1.00; recall 0.55-0.90 | precision >= **0.95**; recall >= **0.60** |
| H-UNREAD | 0 silent drops (N11) | 0 | **0** |
| H-PI3 | rule items on the seed | verified share 0.45-0.70; refuted 0-4 | share >= **0.50**; refuted items each with a correction or baseline entry; unexercised items each with a reason |
| H-PI4 | corrected spec on `variant2` | >= 0.95 | >= **0.95** |
| H-PI5 | proposals include the truth machine's first edge | p 0.6 | reported |
| H-PI7 | import 10x | identical | 10/10 |
| H-PR3 | R with the imported spec | the same verdict map | identical after the correction map |
| H-PA1 | `derive_dn_policy` | private ⊆ truth private; owners ⊆ truth owners | 0 wrong |
| H-PA0 | layer A on the original under `dn_spec_policy` of its candidate spec | 0 findings outside the baseline | **0** |
| H-JUDGE | share of truth owner-scoped routes judged by DN801 (complete reach) | 0.55-0.95 | >= **0.75** |
| H-WF | workflows (static + observed on SQLite) | >= 1 multi-step flow verified; 0 verified flows fail on replay | **0** failing |
| H-IMPL | implementation report with the imported spec | 0 missing/extra route rows | **0** |
| H-DET | extract and layer A 10x | identical | 10/10 |
| H-X | import wall (helper prebuilt) | 15-90 s | <= **240 s** |

### 5.8 MySQL rows (Docker, owner's machine; Arm M)
| id | probe | prediction (p) | pass |
|---|---|---|---|
| M-R1 | `lattice check --layers ACDX --database mysql` on D1 with its harness | exit 0, pass; layer A ran (0 findings); scenario pass count equal to the contract check's | as stated; a normalised difference is listed |
| M-R2 | the same 10x (layers A, C, D) | identical after the normaliser | 10/10 |

### 5.9 Cross-arm (byte-compat, environment)
| id | probe | prediction (p) | pass |
|---|---|---|---|
| PW | snapshot (sha256, mode, mtime, listing incl. hidden files) of every fixture, D1, H1, `dnapp`, `lattice/dncheck/src`, the Streak copy's inputs; recording `docker`/`claude`/`dotnet` shims where a row names none | 0 changed; 0 shim calls outside the rows naming them | all 0 |
| PB-F | section 10's frozen table at the base | all identical | all |
| PB-X | default `gocheck extract` on the 7 round-1 dirs, goapp, `tests/gocheck/fixtures/runs`, goginapp, gotickets | identical to the base | all |
| PB-G | Go outputs: layer A findings and notes on every gocheck fixture, goapp, goginapp, gotickets, the implementation fixture; `workflows --static` and `inventory` on `goextract_workflow`; implementation report, app map, impact T3 and `spec ask` digests on the Go fixtures; `test_process_runtime.py` | identical | all |
| PB-P | Python rules imports (J1 normaliser), `derive_policy`, `pycheck` findings, FastAPI/Django/Flask front-end goldens | identical | all |
| PB-N | stack-neutral outputs: `tests/contracts`, `tests/improve`, `tests/studio/test_rebuild.py`, contracts exported for goginapp's imported spec | identical | all |
| PB-H | `lattice --help`, `python -m lattice.importer --help`, `python -m lattice.spec --help`, `check --help`, MCP `tools/list` | identical except `check --help` gaining `dotnet` in the `--runtime` text (declared, Q10) | as stated |
| PB-NODN | with `dotnet` removed from `PATH`: every Python and Go command of PB-G/PB-P identical; a .NET import fails with `DotnetToolchainMissing`; layer A on a .NET runtime is a note | as stated | as stated |
| PB-T | `pytest tests/importer tests/pycheck tests/gocheck tests/spec tests/mcp tests/studio tests/dncheck -q -rsf --continue-on-collection-errors -p no:cacheprovider` | the base's failing, skipped and error ids (the 7 known failures, handoff section 3) plus the new tests passing | same ids |
| PB-D | `dogfood/studio/check.sh` (venv python) | passes, same step count | pass |

**Gated row count:** Stage 1: 21 (10 Arm 0, 3 DET, 8 D1); Stage 2: 5; Stage 3: 6 (+1 on Q9; A-JUDGE-D1 reported); Stage 4: 9 (+4 on Q8;
I-D1-5 reported); Stage 5: 16; Arm H: 19 (H-PI5 reported); Arm M: 2; cross: 10; P-J0 (section 7): 1. Total 89 (94 with Q8 and Q9). The
runner prints the exact gated list before the first metric, and that list, hashed in Amendment 3, is the gate.

**Why these thresholds.** D1 is dev and the build node tunes on it: Spike A's 100% route gate applies. H1's route 0.90/auth 0.85/
H-PI3 0.50/H-JUDGE 0.75 are G3's S4 thresholds (Amendment 1 Q11 there), unchanged so the two stacks are judged alike. H-BOUNDS precision
1.00 and 0 confident-wrong owners because a wrong bound or owner becomes a wrong rule in the candidate spec. 0 layer-A findings on the
originals because a false finding on a passing app fails the owner's build gate.

## 6. Mutants (control first in every runner; killed = a gated detector that passes on the control flips, or a named unit test flips)

### 6.1 Library mutants (`dogfood/dotnet_mutants.py`; predicted 24/24; gate **24/24**)
| id | mutant | predicted killer |
|---|---|---|
| LM1 | `MapGroup` prefix dropped when a group crosses a method boundary | P0-ROUTES; D-ROUTES |
| LM2 | `[controller]` token not replaced | P0-ROUTES |
| LM3 | a base controller's `[Route]` not inherited | P0-ROUTES |
| LM4 | `[AllowAnonymous]` ignored | P0-AUTH |
| LM5 | `RequireAuthorization` on a group not propagated to nested groups | P0-AUTH; D-AUTH |
| LM6 | a `RequireRole` policy read as Member | P0-AUTH |
| LM7 | minimal-API annotations treated as enforced without `AddValidation` | P0-BIND; A0-DN (DN803 lost) |
| LM8 | FluentValidation rules ignored | P0-BIND |
| LM9 | implicit required (nullable enable) ignored | P0-BIND |
| LM10 | the class name used as the table name instead of the `DbSet` property name | P0-TABLES |
| LM11 | `IEntityTypeConfiguration<T>` classes not applied | P0-TABLES (unique index, `notes`) |
| LM12 | `[JsonIgnore]` not read | P0-TABLES; A0-DN (DN802 on `User`); A-DERIVE |
| LM13 | only the first conjunct of an `&&` predicate read | P0-OWNER |
| LM14 | the find-then-check owner pattern not read | P0-OWNER; A0-DN (false DN801 on o2) |
| LM15 | global query filters ignored | P0-OWNER; A0-DN |
| LM16 | DI single-registration resolution off | P0-DI; A-D1 judged set (A-DM DM1) |
| LM17 | DI picks the first of two registrations | P0-DI; unit `two_registrations_stay_unread` |
| LM18 | the exception-handler mapping off | P0-RAISE |
| LM19 | JSON naming policy ignored (wire = property name) | P0-JSON; D-WIRE; I-D1-T2 |
| LM20 | DN806 fires on `DateTimeOffset` comparisons | A0-DN |
| LM21 | DN807 ignores the transaction scope | A0-DN |
| LM22 | an absolute path in the extract | DET-PATH |
| LM23 | the .NET front end registered eagerly (Python imports load it) | PB-LAZY; PB-P |
| LM24 | the consumer dispatch always picks the Go front end | C-INV; W-D1-1 |

Runner mutants: **RM1** the runner reports an arm `not_run` with every prerequisite present -> the self-test fails; **RM2** the self-hash
check skips one entry -> the freeze test fails. Both caught before Amendment 3.

### 6.2 Held-out mutants (written by W from H1 and its `truth.json` only; gate **>= 13 of 16** with the class gates)
Instance rule: in `truth.json` order, the first and the last route that fits the operator (when only one fits it is used once and the next
operator gets three instances; W records each substitution). W builds each patch (0 errors) and never runs Lattice.

| ids | operator | gated detector | class gate |
|---|---|---|---|
| HM1-2 | drop the owner condition of an owner-scoped read | DN801 on that route | >= 3 of HM1-4 |
| HM3-4 | drop the owner condition of an owner-scoped write or state change | DN801 on that route | (with HM1-2) |
| HM5-6 | expose a never-returned column on a route that answers its row | DN802 on that route and column | 2/2 |
| HM7-8 | remove the login requirement from a Member route | layer R `R1` of that route refuted | 2/2 |
| HM9-10 | answer 403 where truth says 404 `not_found` | layer R's 404 item refuted | 2/2 |
| HM11 | drop the parent-ownership check of an `owner_via` table | reported (p survive: the import does not infer `owner_via`) | — |
| HM12-13 | drop a state guard (`conflict`) of a transition | reported (p 0-1 killed; JM34's class) | — |
| HM14 | remove an upper bound from a validated field | reported (p kill through an R3 422/400 item) | — |
| HM15 | take the owner from the request body on create | reported (p 0.5) | — |
| HM16 | widen a list route's owner condition to every row | DN801 or layer R | reported |

Predicted 12-15 of 16 killed.

### 6.3 D1 dev mutants (written by W0 from D1 and its truth only, before library code; hashed in Amendment 2)
DM1 `GET` of a habit drops the owner condition (DN801 on that route); DM2 the habit `PATCH` drops it (DN801); DM3 the current-user route
returns the user entity with the password hash serialized (DN802); DM4 a Member route loses its auth (layer R `R1` refuted, SQLite
import arm; on MySQL-only D1: layer D's access-matrix case fails in M-R1); DM5 a 404 answered as 403 (layer R's 404 item or layer D's
scenario); DM6 the nudge delivery guard removed (reported). Gate **5/5** for DM1-DM5.

**Mutant total:** 24 library + 16 held-out + 6 dev = **46** (plus 2 runner mutants); gated: 24 + (>= 13 of 16 with class gates) + 5.

## 7. The parity matrix re-measured (J0-dn; written in Stage 6b, every cell checked by V)
The 20 J0 rows, three columns, each cell `S`/`P`/`A` with a file:line that exists at the DN1 commit **and** a test or a results row of
this round or an earlier one; Python and Go cells re-graded from the newest merged evidence (G3's J0' if merged), changed only where
evidence changed; every J0 cell that was `P`/`A` for Go or Python is listed as closed or open with its reason (plan item 4). Pass:
**100%** of cells cite existing proof (P-J0, gated, checked by V).

Predicted .NET column (p): row 1 importer S; 2 lifting **A** (owner decision, as Go); 3 candidate spec / rules-first **P** (S on
SQLite, `not_run:server_dialect` on MySQL: the Go state); 4 layer A **S** (DN800-803, 806-808 active; DN804 moot-gated; DN805 per
Q9); 5 runtime S; 6 layer B **P** (S on `dnapp` with hand-written programs, Q8; no programs on imports; skipped on server dialects,
as Go); 7 C S; 8 D S; 9 X S; 10 R **S on SQLite** (skipped on server dialects, as every stack); 11 workflows S; 12 implementation
report S; 13 chat S; 14 app map S; 15 impact **P** (T1-T3; T4 `implement` on .NET not run this round); 16 perf **P** (server counting;
a SQLite process app `unmeasured`, as Go); 17 contracts S; 18 improve S; 19 build-from-spec **P** (scripted loop only unless Q12);
20 code generation **A** (ruled out). Counts: **S 13, P 5, A 2** (range S 11-14).

## 8. Grading honesty
A .NET cell graded `S` only on dev data is marked "(dev)"; a cell whose only evidence is a crash, a NOT RUN or a skipped arm is `P`
or `A`, never `S`. A miss on H1 localises an idiom the extractor does not read; it is not tuned after the score (11.6).

## 9. File ownership and line budgets (insertions + deletions vs the base; budgets are reported, a miss is a finding)

**Edits** (base sha256 at c75cb48, r; Amendment 1 recomputes them at the actual base, printing any difference beside these):

| node | file | sha256 at c75cb48 | decision | budget |
|---|---|---|---|---|
| B2 | `lattice/importer/frontends/__init__.py` | `ae5cfcbb227207eb174e749310e4084b2c7a3d31e0157191c06ddd6211cba677` | `_load_dotnet`, detection order | <= 20 |
| B2 | `lattice/importer/rules.py` | `5a987b818cf0de415db1a0bd610caa0c0ce7b33ee7f2d388ea1ded4fee57b6ff` | N15 branches (+K4 analogue if G3 not merged) | <= 45 |
| B3 | `lattice/spec/check.py` | `1c1e84e278e8c7397a40118ffa86476a9cfde93afd76d52cbbcffc81ef887691` | N14 branch, **Q10** | <= 40 |
| B3 | `lattice/spec/__main__.py` | `81beadf5b8040420af6f1d05b1f8b202f213430df8853a5346a506414c1a24e1` | `--runtime dotnet`, **Q10** | <= 8 |
| B3 | `lattice/importer/check_invocation.py` | `9e53098bcced34d97e065d06fea8e50d2dfb7baf09f5918614288345fea5d364` | `dotnet` runtime ref | <= 8 |
| B4 | `lattice/importer/workflow_run.py` | `9a3da5a40fc0354986c9714b1d551d0a7d2105ec58d17fa44e0fa8f25ce106b0` | N16 dispatch | <= 25 |
| B4 | `lattice/importer/inventory.py` | `55849494b1ece51ed04a84e34f87da3f65c4fcc02b9523ac9269009e754f2e3b` | N16 dispatch | <= 20 |
| B4 | `lattice/spec/implementation.py` | `89bc16f4679a77f34505fafbca4d33a4a021968aec93407e272a852b0fc319b8` | source set by frontend | <= 30 |
| B4 | `lattice/spec/impact_tier3.py` | `22228513beba44fcff4b0d5ab48c662d2010e7ac320b73ac860f134ffb6e2faf` | code suffix by frontend | <= 20 |
| B4 | `lattice/spec/engines/mysql.toml` | `f0bd70e6a87ea067ff0e0fb5f7d28fda462d097d93f4331cc4000b5cd8874143` | `adonet-mysql`, **only on Q11 yes** | <= 2 |

**Creates.** B0: `tests/dncheck/fixtures/dnparity1/**`, `dnparity1bad/**` (with `truth.json`; before library code), `dogfood/dn_recon/rc*.py`.
B1: `lattice/dncheck/__init__.py` (build, run, `extract_json`, `DotnetToolchainMissing`, `DnCheckError`; <= 250 lines),
`lattice/dncheck/src/{DnCheck.csproj, packages.lock.json, Program.cs, Load.cs, Model.cs, Json.cs, Routes.cs, Auth.cs, Binding.cs, Ef.cs,
Owner.cs, Raises.cs, State.cs, Closure.cs, Assemble.cs}` (<= 5,200 lines of C# together; per file <= 800), `tests/dncheck/test_dnextract*.py`.
B2: `lattice/importer/frontends/dotnet_aspnet.py` (<= 420), `lattice/importer/dotnet_rules.py` (<= 320), `tests/importer/test_dotnet_aspnet.py`,
`tests/importer/test_dotnet_rules.py`. B3: `lattice/dncheck/policy.py` (`DnPolicy`, `DN_CODES`, `DN_GATED`, `run_dncheck`, `gated_note`; <= 350),
`lattice/dncheck/derive.py` (<= 200), `lattice/dncheck/src/Check{Owner,Private,Bounds,Errors,Datetime,Partial,Machine}.cs` (<= 1,800 together),
`tests/dncheck/test_dncheck_layer_a.py`, `test_dn_derive.py`. B4: `lattice/dncheck/src/Workflow.cs` (<= 700), `lattice/importer/extract_source.py`
(<= 90), `tests/importer/test_dotnet_workflows.py`, `tests/spec/test_dotnet_consumers.py`, and on Q8 `tests/spec/dnapp/**`, `tests/spec/dn_harness_rb.py`.
R: `dogfood/dotnet_probes.py`, `dogfood/dotnet_mutants.py`, `dogfood/dn_standin/**` (stand-in truths in the 4.5 schema: `dnparity1` and D1
re-labelled as the H arm for the self-test), `tests/dogfood/test_dotnet_runner.py`. Orchestrator: `HARNESS_API_DOTNET.md`, the A1/A2/W0/W
briefs, the D1 `check.sh` (all under `docs/results/data/2026-10-18-dotnet/briefs/`). Authors' deliverables are copied byte-for-byte by the
orchestrator after their contract checks: `tests/spec/dnstreak/**`, `tests/spec/dnstreak_harness.py`, `tests/spec/<h1>/**`, its harness.
A `.gitignore` entry for `bin/` and `obj/` under `tests/spec/dn*` and `lattice/dncheck/src` (orchestrator).

Shared C# boundary: B1 owns the loader and model types (`Load.cs`, `Model.cs`, `Closure.cs`) and publishes their API at the Stage 1 gate;
B3 and B4 only add files. A needed edit outside a node's rows stops the node and goes to the orchestrator. Builders never commit; the
orchestrator stages explicit paths (never `git add -A` or `git add .`).

## 10. Frozen files and the byte-compat promise

**Frozen (empty diff vs the base; values r at c75cb48, recomputed in Amendment 1):**

| file | sha256 |
|---|---|
| lattice/importer/frontends/go_gin.py | 9f24e9bb5ac8b442cb92cc47ce1b66424b082d728f48e56579783c1bf038a913 |
| lattice/importer/frontends/go_grpc.py | 7543c9fabc0e7a9369f760e18d6f2220a712ea010df821bcd496ea81927db799 |
| lattice/importer/go_rules.py | 180148d8b75b9175414488a4e7721117c97f90673675be4f09ab5099ab1f8c12 |
| lattice/importer/emit_spec.py | c166e7313184c8a2c2d5b282eb3d392c5df16d4f1bd565b2ebff93d4229791b0 |
| lattice/importer/lift.py | 1e964e24aee0cfcc921848f7605deafb68e3cbe55996e4119ccc11c4031c6999 |
| lattice/importer/suite.py | e40bca823dd3a7de166b11f4e0715b65801d0b490529e72d2d0f70dc21c4c83c |
| lattice/importer/appmodel.py | e11e5dcd8ca57762c316dccebd2ea98afa480b18994adb21b774f690b8225987 |
| lattice/importer/transport.py | 04979019468e5d4fd9dc33c7d98ef62c32c5ae41fd5211421ec43ca4cca9ae4a |
| lattice/importer/workflows.py | 5eba7e8ab6ac62ba8ba4cea31088faab9e149e921a952c6a4837e37b92638098 |
| lattice/importer/mine.py | b3399f96dd67fd5f0a487e36fe23d21d067755f95b5835f98efaeafeacc036eb |
| lattice/importer/review.py | b548263a7d3e5bea0fec1e31bafc2b4c561ec91f4cd42f97d59f65d057a14ba5 |
| lattice/importer/report.py | 0955a69a0d57b3d63348c7388b71ee5ad902da2db2247ed141370d2afaacd175 |
| lattice/importer/cli.py | 803d1d80ac7231ffe43b5b7ed42f97544a46ef8b6d1cf5b79d77a468052470ad |
| lattice/importer/profile_gen.py | 414fc40431f64e12b3225be373192ded8362fff804480d93cb3d9011a196421c |
| lattice/spec/probes.py | 7f4fca836922179fcf68806ee7f36713b7d453914f3d39ace7fac9ce495b86e6 |
| lattice/spec/process.py | 047be55e9f421700efab2441525121c8387167fb53edcd5991ebdc4592cdebf1 |
| lattice/spec/appmap.py | c7e2d5fe2589c0eb176f34153b352534ea3c01385a4b4894be4ed52ba0abf590 |
| lattice/spec/ask.py | c8e1e7d24020e6f4e4bebe2bbdd695e729e4fd0d856f3c38c7a6e97eadf245f2 |
| lattice/spec/db.py | 4ff6c5c940de8eafb60ffb5244c4cf64954daa5e890d5ac0f6a58514cdcefe5f |
| lattice/spec/impact_report.py | aa29aede06cce15ea74d32aebb964b71dcbdee5d1f70e02a012ac50028558a41 |
| lattice/mcp/tools.py | 7c0833aa945e2d0a2596e53881c37484001a7ee9592dc5d317efc7b03523423c |
| lattice/perf/measure.py | 3e62797e46bd7f06944895959c84332a8a16e0f278e1cc244fa80e62019849db |
| lattice/gocheck/__init__.py | d3cdb84ddd40c7b06ca0f8bf833fdc91234df6b7c1cc39ece3ab060bed0bdb5b |
| lattice/gocheck/derive.py | 2e8ca90f855daaa80131b3f61288ea8eeb6c3b8d4b8308f7cf02fef7f137db74 |
| lattice/gocheck/src/** (25 files incl. go.mod, `git show` of each in `LC_ALL=C sort` order, concatenated) | 5c88637731a675b8233bacbe789cc6ad5d5b963acd51596d3a45a635f68f0bc8 |
| lattice/pycheck/derive.py | 63fe9d21936451c52785e71ec183f27c2be8b2aadbf6863d3bced4b0eb317040 |
| lattice/pycheck/checks.py | 14603fcc11aeecec0eaaef90417ae70c020026b45828b421ed259cc91886eea0 |
| lattice/pycheck/findings.py | cf5018d21cc17d35f9a1914e57aeba1347230acc0faf51d076447e266477d40b |

Also frozen, hashed by the runner into Amendment 2: every `tests/gocheck/fixtures/*`, `tests/spec/{goapp,goginapp,gotickets}/**` and their
harnesses, every existing test and golden, every earlier round's freeze files, Studio, the mock server and `API.md`. If G3 merges before
Amendment 1 (Q1), the Go and `rules.py`/`go_rules.py`/`derive.py` values are those of the merged base.

**Byte-compat promise vs the base.**
- **Byte-identical:** every Go output (PB-X, PB-G), every Python output (PB-P), the stack-neutral outputs (PB-N), help texts and MCP
  `tools/list` (PB-H, one declared exception), `gated_note()` of gocheck, and every Python/Go output with `dotnet` absent (PB-NODN).
- **Declared not byte-compatible:** `check --help` (`dotnet` in the `--runtime` text, Q10); new outputs for .NET apps (all new).

A difference anywhere else is a finding and fails PB; it is never relabelled.

## 11. Process rules (binding)

1. **Order.** This file -> Amendment 1 -> (A1 then W0; B0 in parallel with both) -> Amendment 2 -> B1 -> Stage 1 gate -> (B2, B3, B4, R) -> merge, self-test
   -> Amendment 3 (committed before A2 is dispatched) -> A2 -> contract check -> W -> Amendment 4 -> one scored run of every arm -> V -> J0-dn
   -> results. Dev runs on Arm 0, D1, `dnapp` before the freeze are allowed and disclosed (`kind: shakedown`).
2. **Self-hash checks are never disabled.** Every runner verifies every entry of its freeze file, its own entry included, before the first
   metric, and aborts on a mismatch. No flag skips it; no entry may be excluded, shimmed or edited.
3. **Global run ledger.** One append-only file, `docs/results/data/2026-10-18-dotnet/ledger.jsonl`, at a path fixed in every runner. Each run
   appends `{ts, runner, runner_sha256, freeze_sha256, arm, kind: shakedown|scored|rerun|verify|mutants, out, lattice_file, dotnet_sdk, status:
   started|crash_before_metric|completed}` at start and end. A scored run refuses an arm that has a `completed` scored entry; a `rerun` is
   allowed once and only after `crash_before_metric` with byte-identical frozen files; `verify` once (V). The runner records
   `lattice.__file__` and refuses to run if it is not under this worktree.
4. **Re-runs.** One, only after a crash before any metric, frozen bytes unchanged. A byte change after Amendment 3 makes every later row
   `post_hoc`, reported beside the first value. No third run.
5. **NOT RUN.** Only when a named prerequisite is missing (no SDK, no Docker for an M/F-RECON/W observed row, an offline NuGet restore of the
   helper); a crash or a failed patch on a real fixture is FAIL (round-2 Amendment 6 rule a). A NOT RUN is never a pass; the gate is then
   incomplete.
6. **Blind vs post hoc.** Every prediction here is fixed now. A missed prediction is a finding. Truths (`dnparity1`, D1's and H1's
   `truth.json`) and pass rules stay as written; matching rules are 4.5's. Anything read after a scored number (a cause, a re-run with a fix) is
   labelled post hoc, goes in a separate section and a separate commit with its deltas, and never moves a gated value.
7. **Author isolation audit.** For A1, A2, W0, W: the transcript is audited (tool calls, paths); one access outside the fixed directory or one
   read of the repository's `lattice/**`, `docs/**`, `dogfood/**`, `tests/**` voids that deliverable (a new author is dispatched, the event is
   reported).
8. **Pass.** Every gated row of its stage; the mutant gates (24/24 library, >= 13/16 held-out with class gates, 5/5 dev); RM1 and RM2 caught;
   P-J0 100%; the byte-compat promise.

```sh
WT=/Users/vivekgade/Documents/lattice-dn; cd "$WT"
export PYTHONDONTWRITEBYTECODE=1 UV_NO_SYNC=1 PYTHONPATH="$WT" PATH=/opt/homebrew/bin:$PATH
export DOTNET_ROOT=/opt/homebrew/opt/dotnet/libexec DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1   # SDK 10.0.401 (r)
PY=/Users/vivekgade/Documents/lattice/.venv/bin/python          # shared venv; PYTHONPATH makes it import this worktree
SCRATCH=<node scratchpad>/dn; mkdir -p "$SCRATCH/shims"         # recording docker/claude/dotnet shims first on PATH where a row names none
git status --short                                               # empty at start
git archive <base> | tar -x -C "$SCRATCH/base"                   # PB base
for f in REQUIREMENTS.md spec.py CONTEXT.md; do git -C ~/Documents/streak show main:$f > "$SCRATCH/a1/$f"; done   # A1's inputs (read-only source)
dotnet --version                                                 # 10.0.401, else every .NET row NOT RUN
$PY -c "from lattice.dncheck import build_dncheck; print(build_dncheck())"   # helper, built in the cache copy
$PY dogfood/dotnet_probes.py --self-test --base "$SCRATCH/base"
PATH="$SCRATCH/shims:$PATH" $PY dogfood/dotnet_probes.py --arm 0,D,N,H,M,X --base "$SCRATCH/base" \
  --d1 tests/spec/dnstreak --h1 tests/spec/<h1> --freeze "$SCRATCH/dn.sha256" --runs 10 \
  --out docs/results/data/2026-10-18-dotnet/scored
$PY dogfood/dotnet_mutants.py --base "$SCRATCH/base" --scratch "$SCRATCH/m" --freeze "$SCRATCH/dn.sha256"
# Arm M, F-RECON, W-D1 observed, C-PERF, C-LIVE: Docker, lattice[db] (PyMySQL), LATTICE_CONTAINER_OWNER=w-dn, LATTICE_MYSQL_URL unset
$PY -m pytest tests/importer tests/pycheck tests/gocheck tests/spec tests/mcp tests/studio tests/dncheck -q -rsf \
  --continue-on-collection-errors -p no:cacheprovider
$PY -m ruff check <changed .py>; $PY -m ruff format --check <created .py>; dotnet format lattice/dncheck/src --verify-no-changes  # (in the cache copy)
```

## 12. Risks, and what this cannot show

1. **No semantic binding of EF Core/Pomelo types.** Recognising `DbSet<T>`, `Where`, `SaveChanges`, `UseMySql` by name (N1) can be
   fooled by a project type of the same name; the extractor requires the matching `using` and an error-type (unbound) symbol, and a
   project-declared type of that name makes the construct `unread`. The alternative (referencing the target's restored packages) would
   tie the output to the machine's NuGet cache (DET-RESTORED guards the choice).
2. **DI is everywhere in .NET.** Services injected through interfaces are the ordinary idiom; N10 resolves single registrations only.
   Assembly scanning, decorators, factories and MediatR will leave reach incomplete and DN801 silent; H-JUDGE >= 0.75 is the honest
   measure and may fail on an app organised around a mediator.
3. **Annotations without enforcement.** Minimal APIs ignore data annotations unless `AddValidation()` is called; N5 refuses to count them.
   A held-out that validates through a library or filter N5 does not name yields fewer bounds (recall), not wrong ones (precision 1.00).
4. **Names.** Snapshot-first naming (N6) depends on migrations; an `EnsureCreated` app with a naming plugin other than snake_case gets
   `unread` names, and every table-level row on it weakens. JSON naming through a custom policy makes wire names `unread`.
5. **D1 is large and written by a model.** A Streak reproduction that fails some scenarios is still dev data; its truth is the author's and
   may contain errors (a truth defect is reported as such, never fixed after the score).
6. **MySQL-only apps get no rule items** (`not_run:server_dialect`) until the server-dialect R port exists (all stacks); Q3's SQLite
   provider is the workaround for D1 and H1, and EF provider differences (MySQL's case-insensitive collation, SQLite type affinity) can make a
   rule verified on SQLite and false on MySQL. That is reported, not hidden.
7. **Enums stored as int** make layer C invariants and machines read numbers; N7 maps members to stored values, and a custom converter is
   `unread`.
8. **One held-out app.** A pass shows the extractor reaches one blind code base; a failure names a missing idiom. The brief cannot force
   variety without naming idioms (rule 2).
9. **Toolchain and network.** The helper's first build restores Roslyn from NuGet (cached on this machine, r); the corpus needs NuGet for
   EF Core and providers; offline machines get NOT RUN. SDK 10.0.401 only; another SDK changes the implicit-using list (RC2) and is a new
   pinning decision.
10. **Cannot show:** gRPC for .NET; C# lifting (row 2); layer B on imported .NET apps (no programs); R/B on MySQL; T4 implement on .NET;
    a real-agent .NET build (unless Q12); Razor/Blazor/SignalR; .NET Framework.

## 13. Questions for the orchestrator

1. **Q1 Base.** Wait for G3 (Go round 3: K3 body models, K4 `not_run:server_dialect`, K6 table identity, K8 GO805) to merge into
   `next-phases`, and rebase `np-dotnet` before Amendment 1? Recommended: yes; otherwise B2 implements the K4 analogue inside its
   `rules.py` budget and DN805 stays gated.
2. **Q2 The extract schema (N2).** Accept `lattice.dnextract/1` with the full goextract key set plus one `dotnet` key, and the
   `extract_source.py` dispatch? The rejected alternative, a renamed neutral schema for both stacks, changes every Go byte.
3. **Q3 Databases of the corpus.** D1: MySQL 8.4 primary **plus** a SQLite provider selected by an environment variable, so the
   rules-first import (layer R) can run on D1? H1: SQLite primary (as G3's S4), MySQL optional? Recommended: yes to both.
4. **Q4 MySQL provider.** Pomelo 9.0.0 (EF Core 9 on `net10.0`; restores and builds, r) as the plan says, Oracle
   `MySql.EntityFrameworkCore` 10.0.9 (EF Core 10), or the author's choice? Recommended: the author's choice for D1, both recognised (N6).
5. **Q5 Author models, budgets, tools.** A1 (D1, a 3-6k-line app passing 90 scenarios) on opus through the `claude` CLI with a turn cap,
   A2/W0/W on sonnet; may A1 run the fixed `check.sh` (layers C, D; scenario names and pass/fail only)? Recommended: yes, with a
   reported USD-equivalent figure from the CLI and no API key.
6. **Q6 D1 acceptance.** Every access-matrix case and >= 85 of 90 scenarios on MySQL; if A1 cannot reach it in its rounds, accept D1 as
   dev with the failing list frozen, or dispatch a second author?
7. **Q7 D2.** Include a job-scheduler-style second dev app (author A3, reported rows only), or skip in round 1? Recommended: skip.
8. **Q8 `dnapp`.** Build the .NET port of Go S1 for layers B and R parity (J0 rows 6, 10)? Recommended: yes (B4, <= 450 lines of C#).
9. **Q9 DN805.** Build the structural not-owned derivation now (G3 K8's rule), or keep it gated until K8's results are in? Recommended:
   build only if K8 passed in G3; otherwise gated with its reason.
10. **Q10 Unfreeze `check.py` (<= 40 lines) and `__main__.py` (<= 8)** so `lattice check` runs layer A on a .NET runtime and accepts
    `--runtime dotnet`? Without it, layer A for .NET exists only through `lattice.dncheck` directly and row 4 is `P`.
11. **Q11 `adonet-mysql` driver line** in `lattice/spec/engines/mysql.toml`, or harness-side translation only (default)?
12. **Q12 Build-from-spec (row 19).** Scripted rebuild loop only (row stays `P`), or an approved real-agent build of a toy .NET spec with a
    USD cap (prior real-agent budgets were not approved)?
13. **Q13 ASP.NET Identity.** Read `MapIdentityApi`/`IdentityDbContext` from a fixed table in round 1 (N4), or list them `unread`?
14. **Q14 Held-out gates.** Accept H-ROUTES >= 0.90 with 0 extra, H-AUTH >= 0.85, H-PI3 >= 0.50, H-JUDGE >= 0.75, H-BOUNDS precision 1.00,
    and the mutant gate >= 13/16 with class gates (G3's S4 values)?
15. **Q15 Docker.** Confirm Arm M, F-RECON, W-D1 observed, C-PERF and C-LIVE run on the owner's machine with one throwaway `mysql:8.4`
    container per run (label `lattice.owner`, removed after).
16. **Q16 Merge.** DN1 merges into `next-phases` only on the owner's yes; is a partial merge of passing stages acceptable (as for J1/G3)?
17. **Q17 Repository placement.** D1 and H1 under `tests/spec/` (3-6k and 1-2k lines of C#, no `bin/`/`obj/`), or archived as tarballs under
    `docs/results/data/` with only their truth in `tests/`?
18. **Q18 Where to build.** B1 alone, then B2, B3, B4 and R each in their own worktree from `np-dotnet` (four in parallel), merged by the
    orchestrator before the freeze?

## Amendment 1 (orchestrator rulings on section 13, 2026-10-18, before any code)
Q1: yes: the build starts only after Go round 3 (G3) is merged into `next-phases`; `np-dotnet` is rebased onto that tip first. Q2: accepted
(`lattice.dnextract/1` with the goextract key set plus one `dotnet` key, and the `extract_source.py` dispatch; Go bytes unchanged).
Q3: yes to both (D1 on MySQL 8.4 plus an environment-selected SQLite provider; H1 on SQLite, MySQL optional). Q4: the author's choice
for D1 (Pomelo 9.0.0 or Oracle `MySql.EntityFrameworkCore` 10.0.9), both recognised. Q5: yes: A1 on opus through the `claude` CLI with a
turn cap, A2/W0/W on sonnet; A1 may run the fixed `check.sh` (scenario names and pass/fail only); the CLI's list estimate is reported; no
API key. Q6: if A1 cannot reach the acceptance in its rounds, D1 is accepted as development data with its failing list frozen. Q7: skip
D2. Q8: yes, build `dnapp` (<= 450 lines of C#). Q9: build DN805 only if G3's K8 passed; else gated with the reason. Q10: yes, unfreeze
`check.py` (<= 40 lines) and `__main__.py` (<= 8) so `lattice check` runs layer A on a .NET runtime (equal support is the owner's
goal). Q11: harness-side translation only. Q12: an approved real-agent build of a toy .NET spec with a USD 5 cap through the `claude`
CLI (a usage limit is NOT RUN). Q13: read `MapIdentityApi`/`IdentityDbContext` from a fixed table in round 1. Q14: accepted (G3's S4
values). Q15: confirmed (one throwaway `mysql:8.4` container per run, label `lattice.owner`, removed after). Q16: DN1 merges into
`next-phases` after the orchestrator's independent re-run; a partial merge of passing stages is acceptable with the failures stated.
Q17: D1 and H1 are archived as tarballs under `docs/results/data/` with only their truth under `tests/` (no `bin/`/`obj/` in the
repository). Q18: yes: B1 alone, then B2, B3, B4 and R each in their own worktree from `np-dotnet`, merged by the orchestrator before
the freeze.

## Amendment 2, part 1 (B0's fixture and the harness doc; orchestrator, 2026-10-19, before D1 and any library code)
**Frozen:** the Arm 0 fixture `tests/dncheck/fixtures/dnparity1/**` (25 files; `freeze-arm0.sha256` sha256 `a865ce4d27c8a13cce782788bb4a4b528cf783e7022f688118069e1fb9921309`; truth.json sha256
`4a81fd6baf5b3b5b92af039f5a85283ca6192b650b0e791bf94c4b1a426a4b38`) was written before any library code; it parses with 0 Roslyn errors, compiles with 0 errors and 0 warnings on SDK 10.0.401, and ASP.NET's own endpoint table
contains all 39 truth routes (public/member agree with the runtime metadata); 246 truth file:line entries verified. The recon scripts are `dogfood/dotnet_recon/` (RC1-RC6).
`HARNESS_API_DOTNET.md` (sha256 `d5cf9fb4edffcac3858196c18edc025446f94f099b71a1a41f1eb2e95bfcb0f6`) was verified on scratch ASP.NET apps through Lattice's real runtime (SQLite and MySQL, both providers).
**Re-registered decisions:** (1) **RC1 refuted its prediction:** 2 project calls (`_notifier.NotifyAsync(note)`, `new TagView(t.Id, t.Label)`) stay unbound when an argument has an EF-derived error type; each has exactly one project candidate in
`CandidateSymbols`. Decision N-bind: a call whose overload resolution fails is treated as BOUND to its single project candidate when exactly one project candidate exists (else unread), so the DI call closure (N10) and DTO constructors work. (2) **The restore command:** the prereg's
`dotnet build -c Release --locked-mode` is invalid on SDK 10.0.401 (`MSB1001`); the rule everywhere (lines 297, 380, 396 included) is `dotnet build -c Release -p:RestoreLockedMode=true` (or `dotnet restore --locked-mode`). (3) `process_runtime(framework="dotnet")` works today (framework is a free
string; layer A is skipped with a note until the .NET checker exists); `--runtime dotnet` is added by the Q10 edit. (4) Provider pairings: Pomelo 9.0.0 needs `Microsoft.EntityFrameworkCore.Sqlite` 9.0.x; Oracle `MySql.EntityFrameworkCore` 10.0.9 needs Sqlite 10.0.x (9.0.21 and 10.0.13 avoid
the NU1903 warning); one MySQL connection string works for both (`SslMode=Disabled;AllowPublicKeyRetrieval=True`). (5) MySQL 8.4.11 has `lower_case_table_names=0` and a strict `sql_mode`, which the harness relaxes on Lattice's throwaway server (documented). (6) **D1 route check:** Streak's fixed seed has no habits, so routes
with a path parameter may answer 404 (never 405) in the contract check; every other truth route must answer neither 404 nor 405. (7) **D1 source of the spec:** Streak's `REQUIREMENTS.md`, `spec.py` and `CONTEXT.md` at `a0679e9` (the ref used for every other Streak corpus), not main. (8) B3 adds the sibling fixture `dnparity1bad` (DN800).
Part 2 (D1, its truth and W0's patches, the recon outputs on D1) follows before B1 starts.

## Amendment 2, part 2 (D1, its truth, W0's patches and the recon on D1; orchestrator, 2026-10-19, before any library code)
**D1 (`dnstreak`):** written by a separate isolated agent (opus) from Streak's REQUIREMENTS.md, spec.py and CONTEXT.md at `a0679e9` and `HARNESS_API_DOTNET.md` only; the transcript audit found 0 reads of the repository and these
disclosed deviations: `docker images`/`docker ps` listings, one `ls` of the hidden tools directory with its output discarded, and use of the real HOME so Docker's configuration was found. Its truth was rewritten once into the
section 4.5 schema (the harness doc lacked it: our omission; the doc gets a section 8 with the schema before the H1 author sees it). Contract check, all six items PASS (a separate node, no author file edited): freeze 54 files verified (author freeze sha256 `a3978c350e32c04e52dd631b637deeb05b743e06bf027ae60711401851c4b889`);
clean-copy `dotnet build -c Release -p:RestoreLockedMode=true` 0 errors and 0 warnings; harness smoke on SQLite and MySQL (26 of 26); behavioural check on MySQL 8.4: access matrix 59 of 59, scenarios 96 of 97 (the spec at `a0679e9` has 97 scenarios, not 90), steps 478 of 479;
the **frozen failing name** is `access_token_expires_after_15_minutes / step 4` (the checker logs ann in on first use, after moving the clock to 12:14, so a 15-minute token is still valid at 12:16); all 26 truth routes answer neither 404 nor 405; truth validates with 0 errors and all 443 file:line references exist.
Archived: `d1.tgz` sha256 `7c48d5a3b617b943ab98cc23a03fd337f98df222df3368d21e6221bafa30725c` (the project, harness, FILES.txt, freeze, IDIOMS.md); truth `tests/dncheck/d1/truth.json` sha256 `34000111109953605bb877f6c22e880892b75e37d4825928c5bac36470b9d029`.
**W0's six dev mutants** (isolated sonnet; DM1-DM6; each builds; manifest sha256 `3e671f4650e077ed6c892406a493cac644adf5b7ac2eec84ec562d4db7da5a7f`; `d1-mutants/`): none was run before this amendment. DM4's honest limit: every Member route already returns 401 without a token, so removing the requirement from `POST /v1/auth/logout` only changes expired/invalid-token answers and the rate limit; DM6 has no HTTP route (the daily pass), so only a scenario driving the nudger can see it (report-only, as registered).
**Recon on D1 (hashes in `freeze-d1.sha256`; not gated):** RC1 verdict **NO**: with only the two reference packs 66 project-symbol invocations stay unbound (479 bound, 331 framework-bound, 2445 unbound in all; 13 of 50 project-type constructors unbound), because an EF-derived argument or receiver type makes overload resolution fail
(`UserTime.ZoneOrUtc(user.Timezone)`, `tx.CommitAsync(ct)` ...). **Re-registered decision N-bind2** (before code, extending ruling 1): a call or construction that fails overload resolution is resolved lexically by (declaring type if known, name, arity) against the project's own declarations: exactly one candidate means bound, several means `unread` with the call site and the candidate names; nothing is guessed. RC2: the 16 implicit usings are confirmed on D1 (`global_usings` identical to B0's list). RC3: snapshots exist for both providers (142 columns, 138 snake_case; the 4 `Id` columns are not: the extractor reads names from the snapshot and the model, it does not assume a casing). RC4: `SnakeCaseLower` (one assignment, 0 `[JsonPropertyName]` overrides). RC5 and RC6 are measured on the real helper in B1.

## Amendment 3 (the freeze; orchestrator, 2026-10-21, after Stages 1-5 and before H1 exists)
**State frozen:** `np-dotnet` at `c125a98` (B1 helper, B2 front end and import, B3 layer A, B4 workflow and consumers, R runners, S shakedown fixes and round 2). Freeze file `docs/results/data/2026-10-18-dotnet/freeze-h1.sha256`
(85 entries, sha256 `b9954b061c2b7d52956c1ad6df8a7cc8adb04bf92eb263263508fa70809f56aa`; `shasum -c` passes): both runners, the mutants runner, the runner test, every `dn_standin` file, the whole B1/B2/B3/B4/S library
(C# helper including `Required.cs` and `Projection.cs`), `HARNESS_API_DOTNET.md`, `TRUTH_SCHEMA_DOTNET.md`, `d1.tgz`, `BRIEF-H1.md` and `BRIEF-W.md`. The once-only ledger is `docs/results/data/2026-10-18-dotnet/ledger.jsonl` (shakedown runs left none).
Both runners' self-tests pass on the frozen tree (`SELF-TEST OK`, RM1 and RM2 caught; the probes self-test with the real Streak extract and change set, the mutants self-test with the real Streak extract).

**Rulings.**
1. Q9 is **gated off**: K8 was refuted in Go round 3, so DN805 is not built (DN804 is moot); A-805 is reported, not gated. **Gated rows: 93** (not 94).
2. B3's twelve deviations are adopted (DN801 judged per query with a narrower "complete reach", two exemptions for credential lookups, `machines` empty in `derive_dn_policy`, `private` derived from `[JsonIgnore]` only plus the projection rule below, DN807/DN803 limits as stated in the B3 report).
3. S added `Required.cs`: a body accessor is required when the handler refuses a missing value with a 4xx, evaluated by a small bounded evaluator (anything undecided stays optional). D1 dev: 17 of 17 truth `required` facts found; H-BOUNDS on the D1 stand-in precision **0.85** against the registered **1.00** -- a **miss**, left as is (a narrower rule would be tuning to D1's truth). The gate stays 1.00 on H1.
4. Layer R login: D1's login needs a field the model cannot express. `Callers.json_login` (`importer/rules.py`, +17 lines) now reaches `probes.py`'s existing `ProbeContext.json_login`; B2's transport wrapper (`login_fields`) stays for the harness-driven flows. `review.py` got `candidate_item_ids` (deterministic numbering of duplicate `#n` ids): **`review.py` is a frozen-table file, so PB-F is expected to FAIL on exactly that file**; Python and Go outputs were checked byte-identical by `tests/importer` and `tests/gocheck` (pass). This is a declared departure from "frozen files byte-unchanged", not a hidden one.
5. PB-T is expected to fail one Go test (`test_go_parity_probes3.py ...805d_discriminates_its_base`, which pins `check.py`'s sha256): the Q10 edit to `check.py`. Declared.
6. Runner-side changes made after the first shakedown (glue, not gates): normaliser masks bcrypt hashes, three-part JWTs and token-like values in `report.json`/`proposals.json` evidence (raw files untouched); F-RECON's absent-route control is no longer absorbed by a parametrised sibling; W-D1-1 reads the machine as `table.column`; `workflows --observed` passes the harness users; C-IMP uses the Spike D change set c01-c05.
7. Prediction errors recorded (the registered texts stay in the table; the rows are reported as run): C-IMPL "health implemented-not-required" (Streak's spec has a health rule R3); C-ASK "sync is not_implemented" (D1 implements `GET /v1/sync`); C-IMPLM "exactly 2 required-not-implemented" (a removed route backs 2-4 rules); `bind_status` 422 vs the library's 400 (not gated); DM4 predicted killed (its detector is not layer A; every Member route already answers 401).

**D1 dev results at the freeze (shakedown, 93 gated rows; H-arm rows need H1).** Passing: all 10 P0 rows, DET, D-*, F-DETECT/MODEL/RT/RECON, A-D1, A-CHECK, I-D1-1/4/6/7/M, W-D1-2/3/4, C-INV/CITE/ASK/MCP/MAP/IMP/PERF/LIVE/IMPROVE/REBUILD, N-R1/R2/B1/10, M-R1/R2, PB-LAZY/X/G/P/N/H/NODN/D, PW, library mutants **24 of 24 killed**.
**Failing, stated:** A-DM (DM1-3 killed, DM4 not), A-DERIVE and I-D1-2 and I-D1-T2 (D1 hides columns through hand-written response records: `private` is not derived for D1; a bounded projection rule exists but does not close it), I-D1-3 and I-D1-PR3 (259 items, verified share **0.255** against >= 0.50), W-D1-1 (before the runner's machine-id fix the machine read false; after it the row was **not re-run**), C-IMPL and C-IMPLM (prediction errors, item 7), PB-F and PB-T (declared, items 4-5).
**Known risks for H1, stated before it exists:** private columns hidden through response records; machine edges written through tuples; low verified share of the rules import; DN801 judged share; dev fixes were fitted on D1, so a drop on H1 is expected and will be reported, not tuned.

**Process for what follows (binding, unchanged from sections 4.3, 6.2 and 11):** A2 (isolated, opus, fixed scratch dir, transcript audited) writes H1 from `BRIEF-H1.md`, `HARNESS_API_DOTNET.md` and `TRUTH_SCHEMA_DOTNET.md` only; contract check with one logged fix round; nothing of Lattice runs on H1 before the scored run. W (isolated, sonnet) writes HM1-16 from H1 and its truth only, using `BRIEF-W.md`. Amendment 4 hashes H1, its truth, the mutants and their patches. Every arm is scored once (ledger above), then an independent verifier re-runs. A change to any frozen file after this point voids the freeze and is reported.
**NOT RUN at this point:** Arm H, the HM mutants on a real app, the independent verifier, the real-agent toy build, the J0-dn parity matrix, `review list` determinism beyond what I-D1-6/7 show.

## Amendment 4 (H1, its truth, W's mutants; orchestrator, 2026-10-21, before any Lattice run on H1)
**H1 (`outlay`, an expense-report service; author A2, opus, isolated directory `~/Documents/dn-h1-author`, input only `BRIEF-H1.md`, `HARNESS_API_DOTNET.md`, `TRUTH_SCHEMA_DOTNET.md`):** 16 routes, 4 tables (`users`, `expense_reports`, `payout_methods`, `expense_lines`; the last owned through its report), 51 refusals, 15 bounds, one status machine (`expense_reports.status`), roles Member and Admin, bearer login, controllers over services with a typed exception handler; two never-returned private columns.
Transcript audit (42 tool calls): 0 reads of repository content; 2 disclosed items, both harmless (a `cat` of the output file of the author's own background command; an `ls ../` inside its own tree). One fix round used (the author's own e2e test found two bugs, a sort after a projection that EF cannot translate and timestamps without `Z`). Contract check (run independently by the orchestrator, **nothing of Lattice ran**): `freeze.sha256` 42 of 42 verify; the author's `smoke.py` is byte-identical to the one in the harness doc and gives **58 PASS, ALL CHECKS PASSED** (clean-copy locked build 0 errors 0 warnings, harness, start, logins, every truth route neither 404 nor 405, services); `truth.json` 120 file:line references all resolve; no `bin/` or `obj/`. `truth.json` carries extra keys (`covers`, `note`, `json_naming_exceptions`); the schema allows extras.
Archived: `h1.tgz` sha256 `8538f867fd5fdb8defbde7c883faf59f8b6754364cda96bc3246c21a279863b3` (the module and `tests/spec/outlay_harness.py`, 58 files), `h1-truth.json` sha256 `682b739af0572377dd0f4995f5712bce81fe0890acb05d062a550fae24401c75`.
**W's 16 mutants (sonnet, isolated `~/Documents/dn-h1-w`, input H1 and its truth only; 14 tool calls audited, 0 out-of-directory reads; one disclosed slip: a truncated print was saved to a harness file that was never opened):** each patch applies with `patch -p1` (re-checked by the orchestrator) and builds with 0 errors and 0 warnings; none was run. Manifest `h1-mutants.manifest.sha256` (17 files) sha256 `43e8c6998e0c538ed9272699c5be975295add63c2ed5d3884c3d08b461e874bd`; files in `h1-mutants/`.
Interpretations W had to make (registered here, not changed): `owner_read` fits only `GET /reports/{reportId:int}` (HM1; HM2-4 are `owner_write`: PATCH, DELETE, submit); HM1 also reads the lines directly because `ListLines` re-checks the owner; HM7 and HM8 answer **500**, not 401 (`[AllowAnonymous]` reaches `User.UserId()`, which throws), so layer R's `R1` sees a 5xx; HM10 limits the 403 to reject; HM13 drops two spots of `Decide`; HM14 uses the login email `max_length`; HM11 and HM16 are single instances; HM5 exposes `password` on the login answer and HM6 `account_number` on a create answer.
**Scored runs (binding, nothing changes now):** the frozen runners, `freeze-h1.sha256` verified first; one run of arms 0, D, N, H, M, X (`DN:<arm>` ledger keys); one run of the mutants runner with the HM suite on H1; an independent verifier (opus) re-runs from the archived inputs afterwards. `P-J0` needs the J0-dn document of Stage 6b, which does not exist yet: it is reported NOT RUN with that named prerequisite, and the matrix is written after the scores with every cell checked against the run files. Predictions already registered in 5.7 and 6.2 stand; I add no new one after seeing H1 (I have not seen its truth beyond the counts above).

## Amendment 5 (second freeze file; orchestrator, 2026-10-21, before any scored run)
The first scored launch was refused by the runner's self-hash check, before any metric and without a ledger entry (`ledger.jsonl` does not exist): `freeze-h1.sha256` lists this prereg, to which Amendments 3 and 4 were appended after it was written, and it does not list the H1 archives. Corrective, no code changed: `freeze-h1-final.sha256` re-hashes the same must-list **plus** `h1.tgz`, `h1-truth.json`, `h1-mutants.manifest.sha256` and `h1-mutants/**`, and this prereg as it stands after this amendment. The orchestrator checks that every entry of `freeze-h1.sha256` other than this prereg has the identical hash in `freeze-h1-final.sha256`; the new file's own sha256 is recorded in its commit message and in the results document (it cannot be written here, because this prereg is listed in it). The scored runs use `freeze-h1-final.sha256`.
**Correction to the paragraph above (orchestrator, same day, caught before the second launch):** the refused launch **did** write a ledger: `ledger.jsonl` held 12 entries (six `started`, six `crash_before_metric`, run id `f25028297b60`, all with `FreezeError`), no metric computed, no row run, no output produced. The runner's own rules then refuse both a new `scored` run (the arms have an entry) and a `rerun` (the freeze file bytes differ, as they must). Ruling: the 12 entries are kept unchanged as `docs/results/data/2026-10-18-dotnet/ledger-refused-launch-1.jsonl` (committed evidence), and the scored runs start a fresh `ledger.jsonl`. This is a departure from the once-only rule, caused by the orchestrator's omission and justified only because nothing was measured; the verifier is asked to check that the archived file holds only `started` and `crash_before_metric` entries and no `completed`. The runner and every library byte are unchanged.
