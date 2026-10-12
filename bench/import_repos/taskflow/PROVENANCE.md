# Vendored TaskFlow API backend

Held-out repo #2 for the Lattice importer (I6a; spec:
`docs/archive/superpowers/specs/2026-09-27-lattice-importer-design.md`). Different from
`fastapi/full-stack-fastapi-template` (`bench/template_app/`) and from
`bench/import_repos/justnotes`. Never read by anything under `lattice/importer/`.

- **Upstream:** https://github.com/PhilipOkeke/taskflow-backend-api
- **Commit:** `8c53dbcc06887996cf1b8123f442ad1f3d7a17d7` (2026-09-19)
- **Path vendored:** `app/` → `bench/import_repos/taskflow/app`
- **License:** MIT, see `LICENSE` (copied unchanged from the repository root;
  copyright Philip Okeke).
- **Date vendored:** 2026-09-27.

Not vendored: `alembic.ini` + `migrations/` (Postgres-only; unused here), `Dockerfile`,
`docker-compose.yml`, `render.yaml`, `.github/`, the repo's own `tests/`.

## Layout

| Path | Origin |
|---|---|
| `app/auth.py`, `app/auth_routes.py`, `app/config.py`, `app/crud.py`, `app/database.py`, `app/routes.py`, `app/__init__.py` | upstream, imports rewritten only |
| `app/main.py`, `app/models.py` | upstream with the modifications below |
| `app/_lattice_registry.py` | Lattice-owned (added inside `app/`, not upstream; see modification 3 below) |
| `__init__.py` (`make_app`), `seed.py`, `PROVENANCE.md` | Lattice-owned |
| `LICENSE` | upstream |

## Routes (8, plus `/`, `/health`, `/docs`, `/openapi.json`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/auth/register` | none | creates a `User`; 409 on duplicate email |
| POST | `/api/v1/auth/token` | none | OAuth2 password grant, returns a JWT |
| GET | `/api/v1/auth/me` | bearer | |
| POST | `/api/v1/tasks` | bearer | owned resource: `Task.owner_id` |
| GET | `/api/v1/tasks` | bearer | filter by status/priority/search, paginated |
| GET | `/api/v1/tasks/{task_id}` | bearer | 404 if missing or not owned |
| PATCH | `/api/v1/tasks/{task_id}` | bearer | partial update; 404 if not owned |
| DELETE | `/api/v1/tasks/{task_id}` | bearer | 404 if not owned |

Owned resource: `Task.owner_id -> User.id`.

## Local modifications

Every vendored `.py` file starts with a `# ruff: noqa` line (upstream code is not
reformatted or re-linted). No route, CRUD, model, or security *logic* was changed;
the app has no email, LLM, or other external service to stub in the first place, and
the upstream `create_app(settings: Settings | None = None)` factory already accepts
a caller-supplied SQLite URL and secret key, so no DB/env patch was needed either --
both modifications below are compatibility fixes for running this app in the same
process as the other apps vendored under `bench/`, on this project's installed
`sqlmodel`.

1. **Imports (`auth.py`, `auth_routes.py`, `crud.py`, `main.py`, `routes.py`):**
   `from app.…` / `from app import …` rewritten to
   `from bench.import_repos.taskflow.app.…` / `from bench.import_repos.taskflow.app
   import …`, so the package imports as `bench.import_repos.taskflow.app` (same
   reason as `bench/template_app`: upstream's own top-level package is also named
   `app`).
2. **`app/models.py` (SQLModel version compatibility):** every field upstream typed
   plain `datetime` (`User.created_at`, `Task.created_at`, `Task.updated_at`,
   `TaskBase.due_date`, and the same fields mirrored on `TaskPublic`/`TaskUpdate`) is
   retyped `pydantic.NaiveDatetime` — the *same* naive value, no behaviour change.
   This repo pins `sqlmodel==0.0.39`; the `sqlmodel` installed here (0.0.47) added a
   `UTCDateTime` SQLAlchemy column type for bare `datetime` fields on table models
   that raises `ValueError: Datetime values must have timezone information... or
   annotate the field with NaiveDatetime for naive storage` at insert time --
   exactly what upstream's own `utc_now()` produces, by its own docstring, "for
   database compatibility". `NaiveDatetime` is the fix the error message itself
   names, confirmed against `sqlmodel/main.py` (`if issubclass(type_, NaiveDatetime):
   return None` vs `return UTCDateTime()`) and `sqlmodel/sql/sqltypes.py`. Without
   this, registering a user or creating a task raises `StatementError` on `session.
   commit()` on every SQLModel version installed here.
3. **`app/main.py` + `app/models.py` (multi-app process, not upstream's fault):**
   `User`/`Task` inherit `TableBase` (from the new `app/_lattice_registry.py`,
   Lattice-owned) instead of `SQLModel` directly, and the lifespan's table creation
   in `main.py` calls `_lattice_registry.metadata_registry.metadata.create_all(engine)`
   instead of `SQLModel.metadata.create_all(engine)`; field names, types, defaults,
   and the lifespan's behaviour are otherwise unchanged. `bench/template_app`, this
   repo, and `bench/import_repos/justnotes` are all vendored into *one* test process,
   and SQLModel's table classes share one global registry by default, keyed for
   name-based lookups by their plain Python class name -- every one of these three
   apps happens to define a class named `User`. Even though this app's own
   relationships are plain `Field(foreign_key=...)` (no `Relationship()` string to
   resolve), simply having a same-named `User` class registered alongside
   `bench/import_repos/justnotes`'s (whose `Note.owner`/`User.notes` *do* use
   `Relationship()` with a forward-ref string) is enough: configuring any one
   pending mapper configures all of them together, and raised
   `sqlalchemy.exc.InvalidRequestError: Multiple classes found for path 'User' in
   the registry of this declarative base`. `TableBase` gives this app's `User`/`Task`
   their own `sqlalchemy.orm.registry()`, isolating them from every other vendored
   app. Nothing about a single-app import of this repo (`bench.import_repos.taskflow`
   alone) would ever need this -- it exists purely because this monorepo vendors more
   than one SQLModel app into one process.

## SQLite / library-version notes

- `database.py`'s `build_engine` already sets `check_same_thread=False` for any
  `sqlite` URL, and `models.py`'s `NaiveDatetime` patch (above) is the only compat fix
  needed; `fastapi==0.141.1` upstream pins exactly the version installed here.
- `pyjwt` and `pwdlib[argon2]` (upstream's JWT and password-hashing libraries) are
  already covered by the `template` dependency group; no new dependency needed.
