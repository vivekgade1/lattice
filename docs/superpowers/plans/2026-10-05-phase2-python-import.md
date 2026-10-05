# Phase 2 plan: rules-first import for Python projects (FastAPI, Django + DRF, Flask)

> Roadmap: `docs/lattice-direction-executable-spec.md` §6 and §8, Phase 2. Branch: `roadmap`.
> Starts after the Phase 1 exit holds; nothing here touches `bench/results/phase1*`.
>
> **Defaults** (the user may change them; see "Open questions"):
> - specs stay Python-syntax `Spec` modules (`lattice/spec/model.py`);
> - the exit run is CLI-only, with no API key and no LLM (`--no-llm`). The LLM rungs stay
>   available for behaviour coverage, which is reported but not gated;
> - Phase 2 adds no layer A (pycheck) for Django or Flask. Spec checks on those apps run
>   layers R (new, below), B, C and D.
>
> **Process.** The same as Phase 0:
> - tasks run as a dependency graph of agents in isolated worktrees, test-first;
> - agents never run git commands that modify the tree; the orchestrator merges and commits
>   after each gate;
> - every new spec or profile construct follows `docs/lattice-language-rules.md` (semantics
>   cases on every target, or a gate plus a decision-log row).

## Exit criteria

1. **Rule layer.** On each of 3 held-out apps (at least one Django + DRF, at least one
   Flask + SQLAlchemy), measured by `bench/phase2_exit.py` on the importer frozen before
   the apps are vendored:
   - at least 90% of the app's tables have a verified rule layer (§1.1);
   - at least 90% of its routes have a verified rule layer (§1.2).
2. **Fidelity.** Each held-out app's fidelity suite passes (§1.4).
3. **Reported, not gated:**
   - behaviour coverage (routes with a verified program);
   - proposal counts, survival and precision (§4);
   - review time;
   - tokens and cost (0 with `--no-llm`), wall time.
4. **No regression.** The full test suite passes. The FastAPI importer's outputs on the
   template, TaskFlow, JustNotes and multi-vendor apps are byte-identical to the W0
   goldens.

## 1. What "verified rule layer" means

The import builds a **candidate spec**: the profile plus one `Contract` per route (§2.4).
Each rule item in it is then **probed on the running original app**. The probes are
requests made as each seeded role, every one on a fresh copy of the seeded SQLite
database, with the answer and the database diff recorded (the existing
`verify.run_case` machinery). Static extraction only proposes items; it never verifies
one.

Each item gets one verdict:
- **verified:** at least one probe confirms it and none contradicts it;
- **refuted:** some probe contradicts it. The observed fact is proposed as a correction,
  which is re-probed once on a second seed variant. If that passes, the item is
  `verified (corrected)`;
- **unexercised:** no route of the app can reach it (e.g. a column no route writes);
- **unclear:** the front end produced no candidate.

Seeds use the importer's `suite.seed_plan`: an `admin`; `alice`, `bob` and `carol` for the
first non-admin role, with `carol` inactive; one `role_<name>` user for each further role;
and rows of every owned table for both `alice` and `bob`.

### 1.1 Tables

The denominator is the app's own models, plus the users model. Framework tables (Django
`auth_permission`, `django_content_type`, `django_session`, `django_migrations`,
`authtoken_token`; Alembic's version table) are excluded and listed.

| Item | Probe that verifies it |
|---|---|
| **T1 ownership.** One of: `owner(col)`, `owner_via(fk, Parent)`, `shared`, `users` (the identity table), `link` (a many-to-many through table, owned through its owned side). | **owner / owner_via:** `bob` reads, updates or deletes `alice`'s row and gets the not-owned answer, with the DB unchanged. `alice`'s list holds only `alice`'s rows (by key values in the body). A create as `alice` stores `alice` as owner, even when the body names `bob`. **shared:** a non-admin reads another user's row with a 2xx. **users:** login as every role works, and the `me` route (if any) returns the caller. |
| **T2 private columns** | No body of any probe contains the column, and at least one 2xx body returns a row of the table (otherwise the item is unexercised). |
| **T3 bounds** (`Str` min/max length, numeric `ge`/`gt`/`le`/`lt`, from the model, the serializer or the request schema) | For each write route: the boundary value gets a 2xx, and one step beyond gets a 4xx with the DB unchanged. Uses the existing `suite._boundaries`. |
| **T4 one_of** | Every listed value is accepted by some write. An unlisted value gets a 4xx with the DB unchanged. Every stored value after the probes is listed (stored spellings via `one_of_stored`). |
| **T5 keys** (primary key, unique, composite unique) | A second create with the same key gets a non-2xx and adds no row. If the answer is a 500, the key is verified and the 500 is recorded as an app-baseline finding. |

A table is **verified** when:
- T1 is verified;
- no item is refuted (uncorrected) or unclear;
- every T2–T5 item is verified or unexercised.

A table that no route reaches counts as unverified, with reason `unreached`.

### 1.2 Routes

The denominator is every (method, path) the front end finds, minus framework routes,
which are listed. Framework routes are: Django admin, DRF's `api-auth` login pages, schema
and docs views, static files, and FastAPI's `/docs`, `/redoc` and `/openapi.json`.

| Item | Probe that verifies it |
|---|---|
| **R1 auth / roles** (`Public`, `Authenticated`, `Roles(...)`, `Admin`) | Requests anonymously, with a malformed token, as an inactive user, and as every role, on two different targets. The set of callers not refused (401/403) equals the inferred set. Data-dependent refusals belong to R2. |
| **R2 ownership scoping** (any route reading or writing an owned table) | The T1 probes, run through this route: the not-owned answer (status, plus the detail when the code has a literal one) with the DB unchanged, list filtering, and owner stamping on create. `n/a` for routes touching no owned table. |
| **R3 error answers** | Every error site the front end found in the route and its auth path (`raise HTTPException`, `abort(...)`, DRF `NotFound` / `PermissionDenied` / `ValidationError`, `get_object_or_404`, `return ..., 4xx`) is triggered and answered with the predicted status (and detail when literal). A site may instead be marked `unreachable-by-probe` with a reason, counted in the report. No probe of the route answers an unpredicted 5xx. |

A route is **verified** when R1 is verified, R2 is verified or n/a, and R3 has no
refuted site and no unpredicted 5xx. Branch coverage comes from `verify.BranchTracer`,
which works on any Python function, so it covers Django and Flask views too.

### 1.3 The measurement

The verdicts are written per item into `report.json["rules"]`, with each verdict's probe
case names and reason. `bench/phase2_exit.py`:
- recomputes table and route verdicts from those item records and fails if they disagree
  with the summary;
- runs the fidelity suite;
- writes `bench/results/phase2/<app>.json` plus a Markdown table.

### 1.4 The fidelity suite

`bench/import_repos/<app>/fidelity.py` defines `SCENARIOS`: 15–25 `Scenario`s, written
from the upstream README and the upstream tests by the agent that vendors the app. That
agent never sees the importer's output. The suite **passes** when:
- the emitted spec validates;
- `python -m lattice.spec check --layers RBD` reports 0 findings against the original
  app, except the baseline recorded at import (each entry with a reason). This uses
  **seed variant 2**, with different row counts and ids from the import's seed, so a spec
  fitted to one seed fails;
- for every scenario step, the spec's rule oracle (§2.4) predicts the step's status class
  (ok / unauthenticated / forbidden / not-owned / invalid / conflict). Where a verified
  program exists, the Lattice runtime gives the same answer.

## 2. Architecture

```
 FastAPI+SQLModel ─┐                                            ┌─ profile_gen (generalised)
 Django+DRF ───────┼─► AppModel (= RepoModel + framework fields) ┼─ contracts (per route)
 Flask+SQLAlchemy ─┘   + AppRuntime (factory, transport,         ├─ mining → proposals
                         auth driver, db builder, dispose)       └─ lift/verify (behaviour)
                                     │
             candidate Spec ──► layer R probes on the original (lattice/spec/probes.py)
                                     │
             verdicts + proposals ──► report.json/md, proposals.json ──► review CLI ──► spec.py
```

### 2.1 The app model

`RepoModel` stays the app model; `AppModel` is an alias for it. Fields are added, each
with a default that reproduces today's FastAPI output:
- `framework` (`"fastapi"`) and `orm` (`"sqlmodel"`);
- `Route.guards: tuple[Guard, ...]`. A `Guard` is `kind` (`authenticated` / `role` /
  `admin` / `object_owner` / `custom`), `source`, `status`, `detail`, `roles` and `line`.
  FastAPI routes keep `auth` / `dependencies`, and `guards` stays `()` for them;
- `Route.view`: the qualified handler, a `Class.method` for class-based views;
- `RaiseSite.kind` (`http`, `abort`, `drf`, `return`);
- `AuthModel` on the model: the login route and how it is called (`form` / `json`), where
  the token sits in the answer, the header scheme (`Bearer` / `Token` / `JWT`), the
  password hasher, a token minter, and the users model's email, password, active, admin
  and role columns.

### 2.2 Front ends: `lattice/importer/frontends/`

| Module | Responsibility |
|---|---|
| `__init__.py` | The `FrontEnd` protocol: `detect(root, package)`, `extract(root, package) -> AppModel`, `runtime(root, package) -> AppRuntime`. Also the registry, and `select(framework="auto")`. |
| `pyindex.py` | The shared package index, moved from `extract.py`: `_Index`, import resolution, `_Type`, `FieldSpec` typing, literal helpers. |
| `fastapi.py` | Today's `extract.py` body, moved without change. `extract.py` becomes a re-export shim, so every existing import keeps working. |
| `sqlalchemy_orm.py` | Tables from the live `MetaData` (`db.Model.metadata` / `Base.metadata`): types, `String(n)`, `Enum` with its stored names, nullability, primary key, `UniqueConstraint`, `ForeignKey`, `CheckConstraint` text (to mining). Source lines from the AST. |
| `django_orm.py` | Tables from `apps.get_models()` and `_meta`: `db_table`, `max_length`, validators (`Min`/`MaxValueValidator`, `MinLengthValidator`), `choices`, `unique`, `unique_together` / `UniqueConstraint`, foreign key and many-to-many through tables, `CheckConstraint` (to mining), the `AUTH_USER_MODEL` columns. |
| `django_drf.py` | Routes from `get_resolver()` walked recursively, with `callback.cls` / `.actions` for viewsets and routers. Per view, from the AST plus DRF settings defaults: `permission_classes` (built-ins mapped: `IsAuthenticated`, `IsAdminUser`, `AllowAny`, `IsAuthenticatedOrReadOnly`; custom classes' `has_permission` / `has_object_permission` analysed for `request.user.<col>` and `obj.<col> == request.user`), `authentication_classes`, `get_queryset` filters on `request.user` (owner compare), `perform_create` / `serializer.save(user=...)` (owner assign), serializer field bounds and `read_only` / `write_only` (private candidates), `get_object_or_404`, `raise NotFound`, pagination. |
| `flask_app.py` | Routes from `app.url_map.iter_rules()` and `view_functions` (blueprints, `MethodView` / Flask-RESTful `view_class`). Guards from decorators: Flask-Login `login_required`, flask-jwt-extended `jwt_required`, Flask-HTTPAuth `login_required(role=)`, APIFairy `authenticate`, and package decorators whose wrapper returns or aborts 401/403 before calling `f` (classified `authenticated` / `role` / `custom`; role sets confirmed by R1 probes, as `_probe_role_checker` does today). Owner evidence from `filter_by(<col>=<caller>)`, `<row>.<col> != <caller>` followed by `abort(403/404)`, and `<row>.<col> = <caller>`, where a caller is `current_user`, `g.current_user`, `get_jwt_identity()` or a token-auth `current_user()`. Bounds from marshmallow fields (`Length`, `Range`, `OneOf`). |

Model schemas come from the **live** metadata, because the app is imported to run
anyway. Routes and their evidence come from the AST, as today. `outside.reconcile`
compares both with `db_facts` of the migrated SQLite database.

### 2.3 What is FastAPI-specific today, and the refactor

| Place | FastAPI / SQLModel assumption | Refactor (W1-A) |
|---|---|---|
| `extract.py` (all of it) | decorators, `Depends`, `HTTPException`, `table=True` | moves to `frontends/fastapi.py`; the shim re-exports |
| `graph.py` `_is_route`, `_is_table`, `depends` edges | FastAPI / SQLModel predicates | a `GraphHints` object from the front end; the FastAPI hints equal today's |
| `adapt.py` (`_kind`, `_errors`, `auth_spec`, OAuth2 form login, `_insert_hook`) | HTTPException sites, form login, SQLModel insert | reads `AppModel.auth` and `RaiseSite.kind`; the insert hook comes from `AppRuntime` |
| `verify.run_case`, `spec/check.py` `_tokens` and `_run_scenario`, `lift.py` | `fastapi.testclient.TestClient`, `Bearer` header, `app.state.engine.dispose()` | `Transport` + `AuthDriver` + `AppRuntime.dispose`; the ASGI transport is today's TestClient |
| `verify.raise_sites`, `_is_http_exception` | `raise HTTPException` only | the front end's error-site predicate |
| `suite.sqlmodel_insert_hook` | SQLModel sessions | per-ORM hook: SQLModel (today's), SQLAlchemy session, Django `Model.objects.bulk_create` |
| `outside.openapi_facts` | `app.openapi()` | optional; `None` for Flask (and Django without drf-spectacular) |
| `trace.SqlTracer` | SQLAlchemy engine events | Django: `connection_created` hooks `set_trace_callback`; behaviour layer only |
| `policy.py`, `profile_gen` `error_body='fastapi'` | FastAPI error bodies, 422 validation | profile knobs (W1-D) |
| `pycheck/view.py`, `derive.py` | `extract()` | unchanged; layer A stays FastAPI-only |

**Behaviour preservation.** W0 snapshots the current outputs before any move. W1-A
moves code mechanically: shims re-export, and default arguments keep today's behaviour.
A golden test fails on any byte change. The suites under `tests/importer/` and
`tests/spec/` are not edited, except for import-path additions.

### 2.4 Contracts and the rule oracle (spec-side, shared)

Add `Contract(method, path, auth, table, kind, owner_scope, params, errors, response)` to
`lattice/spec/model.py`, with `Spec.contracts`. It is the contract-only route of §6.1: a
spec-level record with no code generation. It is still subject to the language rules: a
decision-log row stating that no target compiles it.

`lattice/spec/probes.py` turns a Spec (profile plus contracts) into probe cases with
**predicted** status classes and DB effects: the rule oracle. Layer **R** in
`lattice.spec check` runs them against an app. The importer verifies a candidate spec by
running layer R on the original. A later edit is checked by the same layer, so import
verification and change checking share one engine.

## 3. Running Django and Flask apps under test

- **Transport.** `lattice/importer/transport.py` defines `Transport.request(method, path,
  params, json, form, headers) -> Answer(status, headers, body)`:
  - **ASGI:** today's Starlette TestClient (FastAPI);
  - **WSGI (Flask):** `httpx.Client(transport=httpx.WSGITransport(app,
    raise_app_exceptions=False), base_url="http://testserver")`. httpx 0.28 is
    installed, so no new dependency and no WSGI→ASGI adapter;
  - **Django:** `django.test.Client` (CSRF checks off, as Django's own tests run), wrapped
    to `Answer`. `ALLOWED_HOSTS` includes `testserver`.
- **One Django project per process.** Django's settings and app registry are global.
  The import CLI already runs as one process per app, and tests for Django apps run the
  CLI or a small probe worker in a subprocess (as `test_import_held_out.py` does).
- **App factory.** Each vendored app gets a Lattice-owned `__init__.py` `make_app(db_path)`,
  following the existing convention:
  - **Flask:** `create_app()` with `SQLALCHEMY_DATABASE_URI=sqlite:///<db_path>`; dispose
    is `db.engine.dispose()`;
  - **Django:** a Lattice-owned `lattice_settings.py` imports the upstream settings and
    overrides `DATABASES` (SQLite at a placeholder), `CACHES` (LocMem),
    `EMAIL_BACKEND` (locmem), `SECRET_KEY` and `DEBUG=False`. `make_app` points
    `connections["default"]` at `db_path`, closes connections and returns
    `get_wsgi_application()`. Dispose is `connections.close_all()`, run before the DB
    snapshot.
- **Schema and seeds.** The schema is built once per app as a template DB (Django:
  `call_command("migrate", run_syncdb=True)`; SQLAlchemy: `metadata.create_all`), then
  copied per variant. Seeds use the existing `suite.sql_seed` over `TableSpec`s, with
  passwords hashed by the app's own hasher (`make_password`, `generate_password_hash`,
  bcrypt). Django many-to-many through tables are seeded as link tables.
- **Logging in as each role** (`AuthDriver`):
  1. through the app's own login route (form or JSON, with the token path in the answer);
  2. else a minter: simplejwt `AccessToken.for_user`, DRF `Token.objects.create`,
     flask-jwt-extended `create_access_token` inside an app context, or the app's own
     token function found by the front end;
  3. else a session (Django `force_login` cookie, Flask-Login `remember` cookie).

  Each role's user comes from `AuthModel`: Django's `is_staff` / `is_superuser`, a role
  enum column, or a boolean flag (`author`).
- **Layer C** needs only the SQLite file. Invariant SQL runs after each request, once the
  app has been disposed. Postgres-only features (`select_for_update`, `ArrayField`,
  `JSONField` operators) are recorded as baseline notes, and routes that need them are
  unverified with a reason.

## 4. Invariant and state-machine mining (`lattice/importer/mine.py`)

Candidates, each with its provenance:
1. **Database constraints:** `CheckConstraint` and `PositiveIntegerField` (`col >= 0`),
   conditional `UniqueConstraint`, and one-to-one foreign keys (`count(T, by=fk) <= 1`).
2. **Model and serializer validators:** Django `clean()`, DRF `validate_<f>` /
   `validate()`, marshmallow `@validates`, SQLAlchemy `@validates`. The simple
   comparisons (`if self.a > self.b: raise ...`) are translated into an `Invariant.expr`.
   Anything else becomes an `Unchecked` proposal quoting the source.
3. **Observed data**, Daikon-style templates over rows the **app** wrote during the probe
   suite (never our own seed rows, which would be tautologies):
   - `c >= 0`, `c > 0`, `c != ''`;
   - `a <= b` for same-typed column pairs;
   - `count(T, by=fk) <= 1`;
   - `parent.total == sum(child.x * child.y, by=fk)` where the names suggest it.
4. **State machines:** transition maps in code (dicts of state to allowed states, as in
   multi-vendor's `VALID_TRANSITIONS`), guarded assignments to a `one_of` column, and the
   transitions observed in probe diffs.

Every candidate is compiled (`compile_invariant`) and run on every probe snapshot. A
refuted candidate is dropped, and only counted. A surviving one becomes a **proposal**
in `proposals.json`, with:
- a stable id;
- its plain-language sentence;
- its source;
- its evidence (rows and requests seen);
- `status: pending`.

Nothing is auto-approved. Precision is measured on multi-vendor against the hand-reviewed
`bench/specs/multivendor.py` invariants and machine (recall and precision reported).

## 5. The import report and review workflow

`python -m lattice.importer <root> --package P --app-factory mod:fn [--framework auto|fastapi|django|flask] --rules-only --out DIR`
writes:
- `report.json` / `report.md`: the existing behaviour sections plus `rules` (per item:
  verdict, probes, reason), `coverage.rules`, `excluded_routes` and `baseline`;
- `proposals.json`;
- `candidate_spec.py`.

`python -m lattice.importer review DIR`, as subcommands:

| Subcommand | Effect |
|---|---|
| `list [--kind invariant|machine|unchecked|rule] [--status pending]` | proposals and unverified/inferred rule items |
| `show ID` | the sentence, expression, source lines and evidence |
| `accept ID... / reject ID... --reason TEXT` | appends to `review.jsonl` (time, user, decision); never rewrites history |
| `edit ID --expr EXPR` | a revised proposal, re-probed before it can be accepted |
| `emit --out spec.py` | writes the spec module: profile + verified programs + contracts + accepted proposals + `Unchecked` entries; each item commented `verified` / `inferred` / `proposed-accepted`; `spec_meta.json` holds the per-item status |
| `status` | the counts, and whether `emit` is current with `review.jsonl` |

Verified rule items go into the spec without a review step. Inferred items (unexercised
or corrected) are listed for review and kept unless rejected. Mined proposals go in only
when accepted.

## 6. The apps (candidates, for the user's confirmation)

Every app is checked: permissive license file present, SQLite-capable (Django ORM or
SQLAlchemy), and no network service needed once the overrides above are applied.
Route counts are approximate (method × path, before exclusions), from each repo's URL
or route files on 2026-10-04.

| Role | App | Framework | URL | License | Last push | Routes | Rules worth testing |
|---|---|---|---|---|---|---|---|
| **held-out H1** | drf_blog_api | Django + DRF, simplejwt | https://github.com/amirpsd/drf_blog_api | MIT | 2024-06-05 | ~24 | superuser / author flag / user; blog owned by `author`; custom permission classes; OTP login through the cache (the minter path) |
| **held-out H2** | project-management-api | Flask 3 + Flask-SQLAlchemy 3, PyJWT | https://github.com/GuilhermeCAz/project-management-api | MIT | 2025-10-31 | ~22 | `UserType` enum manager / employee; projects owned by `user_id`, tasks through a project; custom `token_required` / `manager_required` decorators |
| **held-out H3** | conduit-drf | Django 4.2 + DRF, simplejwt | https://github.com/yoonge/conduit-drf | MIT | 2024-06-02 | ~27 | `IsAdminOrOwner` / `IsAdminOrSelf` (staff vs owner); topics, comments, favourites (many-to-many); MySQL in settings, swapped to SQLite |
| held-out reserve | flask-realworld-example-app | Flask + Flask-SQLAlchemy, flask-jwt-extended | https://github.com/gothinkster/flask-realworld-example-app | MIT | 2022-09-16 (archived) | ~19 | author-owned articles and comments, `Token` header scheme; old pins need compatibility changes |
| dev (Django) | DRF tutorial "snippets" | Django + DRF | https://github.com/encode/django-rest-framework/tree/main/docs/tutorial | BSD-3 (DRF) | active | ~8 | `owner`, `IsOwnerOrReadOnly`, session / basic auth |
| dev (Django) | recipe-app-api | Django + DRF, Token auth | https://github.com/LondonAppDeveloper/recipe-app-api | MIT | 2024-11-22 | ~16 | tags, ingredients and recipes owned by `user`; many-to-many; `get_queryset` scoping; `perform_create` stamping; Django 2.1 pins → Django 5 |
| dev (Flask) | microblog-api | Flask + Flask-SQLAlchemy + APIFairy | https://github.com/miguelgrinberg/microblog-api | MIT | 2024-08-10 | ~26 | posts owned by `author` (403 on edit); token table; OAuth2 provider routes need the network, so they are excluded and counted |
| dev (Flask) | flasky (`app/api` blueprint) | Flask + Flask-SQLAlchemy + Flask-HTTPAuth | https://github.com/miguelgrinberg/flasky | MIT | 2025-04-06 | ~14 API | permission-bitmask roles (Moderator, Administrator); `permission_required`; author-owned posts; Flask 0.12 pins → Flask 3 |

**Held-out discipline.**
- H1–H3 and the reserve are vendored only in W4, after the importer commit is frozen and
  recorded in the results log. Front-end agents never open them.
- While choosing candidates, the plan author read these repos' file trees, URL/route
  files and permission classes. No importer code was written from them.
- If H1–H3 fail for a reason the user judges unrelated to import (e.g. an app that cannot
  run on SQLite), the reserve replaces that app, and the swap is logged.

## Graph

```
W0 goldens ─► wave 1 (parallel)                 wave 2 (parallel)                     wave 3           wave 4
              W1-A core refactor + transport ─┬► W2-A Django ORM + DRF front end ──┐
              W1-B Contract + probes + layer R ┼► W2-B SQLAlchemy + Flask front end ─┼► W3 pipeline, ─► W4 freeze,
              W1-C vendor dev apps ───────────┤   W2-C mining + proposals ─────────┤   dev-app gate,    vendor held-out,
              W1-D profile error knobs ───────┘   W2-D report, review CLI, emit ───┘   exit script      run once, results
```

## Wave 0: goldens (serial, small)

**Files:**
- `tests/importer/goldens/{template,taskflow,justnotes,multivendor}/`: RepoModel JSON,
  profile source, suite case list (names, requests), and `report.json` with `--no-llm`,
  with volatile fields stripped;
- `tests/importer/test_frontend_goldens.py`.

**Gate:** green on the current code. Every later wave keeps it green.

## Wave 1

### W1-A: shared core refactor and transport

**Files:**
- `lattice/importer/frontends/{__init__,pyindex,fastapi}.py`, with `extract.py` as a shim;
- `lattice/importer/transport.py` (`Transport`, `Answer`, `AsgiTransport`,
  `WsgiTransport`, `AuthDriver`, `AppRuntime`);
- edits to `verify.py`, `adapt.py`, `graph.py`, `suite.py`, `lift.py` and
  `spec/check.py` that thread the runtime through, with defaults equal to today.

**Tests (first):**
- `tests/importer/test_transport.py`: one toy ASGI app and one toy WSGI app answer
  identical `Case`s identically, through `run_case`;
- the `Token` / `JWT` header schemes;
- dispose hooks are called.

**Gate:** W0 goldens byte-identical; full suite green.

### W1-B: `Contract`, the rule oracle and layer R

**Files:**
- `lattice/spec/{model,validate}.py` (`Contract`, validation codes);
- `lattice/spec/probes.py`;
- `lattice/spec/check.py` (layer `R`);
- `lattice/spec/report.py` (item verdicts);
- `tests/spec/test_probes.py`.

**Tests:**
- on multi-vendor with its hand spec plus contracts, layer R reports 0 findings;
- mutants of the app (in `tmp_path` copies: drop an owner filter, widen a role, remove a
  bound, swallow a unique check) are each refuted by the expected item;
- every verdict kind has a test;
- the Go/TS gate row is added to the language-rules decision log.

### W1-C: vendor the dev apps

**Files:** `bench/import_repos/{snippets,recipes,microblog,flasky}/`, each with:
- `LICENSE` (upstream, unchanged);
- `PROVENANCE.md` (upstream URL, commit, license, date, what is not vendored, `LATTICE
  MOD` lines);
- `__init__.py` `make_app(db_path)`;
- `lattice_settings.py` (Django apps);
- `seed.py`, `fidelity.py`.

Also a new `pyproject.toml` group, `phase2_apps`: `django`, `djangorestframework`,
`djangorestframework-simplejwt`, `django-filter`, `flask`, `flask-sqlalchemy`,
`flask-httpauth`, `flask-jwt-extended`, `marshmallow`, `apifairy`. Upstream code is
unmodified except for imports and compatibility, each marked with a reason.

**Tests:** `tests/bench/test_phase2_apps.py`, one subprocess per Django app. Each app:
- boots under its transport;
- seeds;
- logs in as every role;
- answers one route;
- leaves no file outside `tmp_path`.

### W1-D: profile knobs for non-FastAPI error conventions

**Files:** `lattice/profile.py`, `lattice/check/*`, `lattice/api/runtime.py`,
`lattice/api/codegen.py`, the target gates.

Add `ApiProfile.validation_status` (422 default, 400 for DRF and Flask) and
`error_body="drf"` (`{"detail": ...}` plus 400 `{field: [msgs]}`) or `"json_key(<key>)"`.

**Tests:**
- runtime semantics cases;
- a differential fixture;
- Go/TS supported or gated with a decision-log row.

## Wave 2

### W2-A: Django ORM + DRF front end

**Files:**
- `lattice/importer/frontends/{django_orm,django_drf}.py`;
- `tests/importer/django/test_extract_{snippets,recipes}.py`;
- `tests/importer/django/test_runtime.py`.

**Tests (first, on the dev apps only):**
- the expected AppModel facts: tables and their columns, bounds, choices and keys;
- routes with their guards;
- owner compare/assign evidence;
- many-to-many link tables;
- `AuthModel` (Token / session / simplejwt).

**Gate:** each dev app's candidate spec passes layer R at ≥ 90% of tables and routes,
with every failure explained in the report.

### W2-B: SQLAlchemy + Flask front end

**Files:**
- `lattice/importer/frontends/{sqlalchemy_orm,flask_app}.py`;
- `tests/importer/flask/test_extract_{microblog,flasky}.py`;
- `tests/importer/flask/test_runtime.py`.

**Tests:**
- as W2-A, plus decorator classification (library and package decorators);
- the role sets confirmed by probes (flasky permission bitmask);
- `CheckConstraint` passed to mining.

**Gate:** as W2-A, on microblog and flasky.

### W2-C: mining and proposals

**Files:** `lattice/importer/mine.py`, `tests/importer/test_mine.py`.

**Tests:**
- each template on a hand-built SQLite file;
- seed rows are never mined;
- refuted candidates are dropped;
- on multi-vendor: the `Order.status` machine and the stock / price / total invariants
  of `bench/specs/multivendor.py` are proposed (recall recorded), with precision
  reported.

### W2-D: report, review CLI, spec emission

**Files:**
- `lattice/importer/{review,emit_spec}.py`;
- `report.py` (`rules` section, schema version bump);
- `cli.py` (`--framework`, `--rules-only`, the `review` subcommand);
- `tests/importer/test_review_cli.py`, `test_emit_spec.py`.

**Tests:**
- accept / reject / edit / emit on a fixture import directory;
- `review.jsonl` is append-only;
- the emitted `spec.py` validates and round-trips;
- the report is consistent with `spec_meta.json`;
- FastAPI reports unchanged apart from the added `rules` section, which the goldens
  ignore.

## Wave 3: pipeline, dev-app gate, exit script

**Files:**
- `lattice/importer/rules.py` (orchestration: front end → profile + contracts → layer R
  → corrections → mining → report);
- `bench/phase2_exit.py`;
- `lattice/spec/__main__.py` (`fidelity` subcommand);
- `tests/importer/test_phase2_dev_apps.py` (subprocess per app).

**Gate:**
- all four dev apps and the four FastAPI apps reach ≥ 90% tables and routes verified;
- their fidelity suites pass;
- the exit script's recomputation agrees with the reports;
- the full suite is green.

Then: a results-log entry, and the importer commit is frozen.

## Wave 4: held-out run

1. A fresh agent vendors H1–H3 (and the reserve, unopened) per W1-C, including
   `fidelity.py` from the upstream README and tests. It sees nothing under
   `bench/imports/` or any importer output.
2. Run, once each: `python -m lattice.importer ... --no-llm --rules-only`, then
   `bench/phase2_exit.py`.
3. Record the frozen-run numbers as **the** result. Fixes made after looking go into a
   separately labelled post-hoc run.
4. Results-log entry with the metric set (route and table %, behaviour %, proposals,
   wall time, review time for one manual review pass).

## Risks and open questions

**Risks**
1. **Probe completeness, not inference, limits the score.** Routes whose guards depend on
   state the seed lacks (an OTP in the cache, an `author` flag, data that only a workflow
   creates) can't be probed. Mitigation: seeds per role and flag, minters for auth, and
   reporting `unreachable-by-probe` explicitly. If H1's OTP login can't be minted, about
   10% of its routes are at risk.
2. **Django's process-global state.** Settings, the app registry and connections make
   per-case isolation and multi-app tests fragile. Mitigation: one project per process,
   explicit connection close before snapshots, and a template DB copied per case.
   Watch the cost of `migrate` (once per app) and Django startup per subprocess.
3. **Shimming may hide real coupling.** Moving `extract.py` behind a shim while
   `adapt` / `lift` / `pycheck` still assume FastAPI shapes (`Dependency`, `Customer` auth
   strings) risks a long tail of FastAPI-only branches in "shared" code. Mitigation: the
   W0 goldens, plus Django/Flask apps running through every shared module in W3, not
   just extract.
4. **Contamination of the held-out set.** All four held-out candidates were skimmed during
   selection. Mitigation: vendoring is late and done by a separate agent.

**Defaults taken by the orchestrator (2026-10-04); the user may override them:**
1. H1–H3 as proposed, with the Flask reserve kept in reserve.
2. The exit counts the frozen first run only. A post-fix run is reported but needs a
   fresh held-out app to count.
3. Probe-decisive auth counts as verified: verification *is* probing (§1). Static
   inference only proposes.
4. 90% applies **per app**, the stricter reading. Pooled figures are reported too.
5. Layer A for Django/Flask stays out of Phase 2.

**Open questions for the user (as asked)**
1. Confirm H1–H3 and the reserve, or pick others. Two Django apps and one Flask app are
   proposed; swap H3 for the Flask reserve if Flask should weigh more.
2. Should the exit gate apply to the frozen first run only (this plan's default), with
   any post-fix run reported but needing a fresh held-out app to count?
3. May probe-observed auth (static inference unclear, probes decisive on two targets)
   count as verified, or must static inference and the probe agree?
4. Is per-app ≥ 90% for tables *and* routes the reading of the exit, or pooled over the
   three apps?
5. Should layer A (pycheck on the Django/Flask AST) stay out of Phase 2, as defaulted
   here, or be added?
