# Vendored JustNotes backend

Held-out repo #1 for the Lattice importer (I6a; spec:
`docs/archive/superpowers/specs/2026-09-27-lattice-importer-design.md`). Different from
`fastapi/full-stack-fastapi-template` (`bench/template_app/`), which the importer's
lifters are developed against. Never read by anything under `lattice/importer/`.

- **Upstream:** https://github.com/DatMayo/JustNotes
- **Commit:** `6cae6e3a27c0f3fe96d8d4aa4763a18b1ba59f1a` (2026-03-26)
- **Path vendored:** `src/` → `bench/import_repos/justnotes/app`
- **License:** MIT, see `LICENSE` (copied unchanged from the repository root; copyright
  Mario Franze).
- **Date vendored:** 2026-09-27.

Not vendored: `alembic`-less migrations (there are none), `Dockerfile`, `.env.example`,
CI config, the repo's own tests. `requirements.txt` is upstream's pinned lock, not
used here; runtime deps come from this project's `pyproject.toml` (see below).

## Layout

| Path | Origin |
|---|---|
| `app/api/{health,tags,users}.py`, `app/config/*`, `app/database/{connection,keyword_crud,note_crud,tag_crud,user_crud}.py`, `app/utils/*`, empty `__init__.py` files | upstream, unmodified (each gets a `# ruff: noqa` header only) |
| `app/api/auth.py` | upstream, unmodified (`# ruff: noqa` header only) |
| `app/api/notes.py`, `app/main.py`, `app/models/{user,note,tag}.py` | upstream with the modifications below |
| `app/_lattice_registry.py` | Lattice-owned (added inside `app/`, not upstream; see modification 4 below) |
| `__init__.py` (`make_app`, `sqlite_engine`), `seed.py`, `PROVENANCE.md` | Lattice-owned |
| `LICENSE` | upstream |

## Routes (30, plus `/docs`, `/openapi.json`, `/redoc`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/auth/register` | none | creates a `User` |
| POST | `/auth/login` | none | OAuth2 password grant, returns a JWT |
| GET | `/auth/me` | bearer | |
| GET | `/user/list` | bearer | |
| GET | `/health` | none | |
| GET | `/notes` | bearer | current user's notes, with owner info |
| POST | `/notes/create` | bearer | owned resource: `Note.owner_id` |
| GET | `/notes/public` | none | notes with `isPublic=true` |
| GET | `/notes/{id}` | bearer | 404 if missing |
| PUT | `/notes/{id}` | bearer | 403 if not the owner |
| DELETE | `/notes/{id}` | bearer | 403 if not the owner |
| GET | `/notes/{id}/summarize` | bearer | **LLM-backed, not exercised** (see below) |
| GET | `/notes/{id}/extend` | bearer | LLM-backed, not exercised |
| GET | `/notes/{id}/translate/{target_language}` | bearer | LLM-backed, not exercised |
| GET | `/notes/{id}/auto-tag` | bearer | LLM-backed, not exercised |
| GET | `/notes/{id}/keywords` | bearer | LLM-backed, not exercised (also see the DB-backed keyword-link route below, same path, different router — upstream's own naming collision, kept as-is) |
| GET | `/notes/{id}/sentiment` | bearer | LLM-backed, not exercised |
| GET | `/notes/{id}/related` | bearer | LLM-backed, not exercised |
| GET | `/tags` | bearer | current user's tags |
| POST | `/tags` | bearer | owned resource: `Tag.owner_id` |
| DELETE | `/tags/{tag_id}` | bearer | |
| POST | `/notes/{note_id}/tags/{tag_id}` | bearer | many-to-many link |
| DELETE | `/notes/{note_id}/tags/{tag_id}` | bearer | many-to-many unlink |
| GET | `/notes/{note_id}/tags` | bearer | |
| GET | `/keywords` | bearer | current user's keywords |
| POST | `/keywords` | bearer | owned resource: `Keyword.owner_id` |
| DELETE | `/keywords/{keyword_id}` | bearer | |
| POST | `/notes/{note_id}/keywords/{keyword_id}` | bearer | many-to-many link |
| DELETE | `/notes/{note_id}/keywords/{keyword_id}` | bearer | many-to-many unlink |
| GET | `/notes/{note_id}/keywords` | bearer | |

Owned resources: `Note.owner_id`, `Tag.owner_id`, `Keyword.owner_id`, all `-> User.id`.

## Local modifications

Every vendored `.py` file starts with a `# ruff: noqa` line (upstream code is not
reformatted or re-linted); `LATTICE MOD` marks a changed line inside an otherwise
unmodified file. No route, CRUD, model, or security *logic* was changed.

1. **`app/main.py`:** construction wrapped in `create_app()` so each held-out run
   gets a fresh `FastAPI` instance (upstream: one module-level `app`, plus an
   unconditional `create_db_and_tables()` call as an import side effect against the
   relative path `sqlite:///db.sqlite` — dropped; table creation now happens inside
   `make_app()` below, against the caller's own path). The
   `if __name__ == "__main__": uvicorn.run(...)` entry point is dropped (a CLI
   runner, not a route).
2. **`app/api/notes.py` (no external LLM service vendored):** the module-level
   `import requests` is moved inside `_call_llm_api`, the one function that uses it.
   That helper backs the optional `/notes/{id}/{summarize,extend,translate,auto-tag,
   keywords,sentiment,related}` routes, which call a local LLM server at
   `settings.llm_service_url` (default `http://localhost:1234/api/v1/chat`, not
   stood up here) — `requests` isn't in this project's dependencies, and the deferred
   import means the rest of the app (including every route the tests exercise) needs
   nothing new. Calling one of those LLM routes without a real server still fails,
   exactly as upstream intended (503/504/500 from `_call_llm_api`'s own
   `except requests.exceptions.*` handling) — just with `ModuleNotFoundError` instead
   of a network error if `requests` truly isn't installed; installing `requests` (not
   part of this project) would make those routes work like upstream's.
3. **Process env, not a vendored file (`bench/import_repos/justnotes/__init__.py`,
   `_ensure_process_tz_utc`):** `app/utils/jwt.py` mints tokens with
   `datetime.now() + timedelta(...)` — naive, local time — and python-jose's
   `jwt.encode` treats a naive "exp" claim as already UTC. On a server configured for
   UTC (the common deployment default) this is harmless; off UTC it stamps every
   token with the wrong epoch and `jwt.decode` immediately raises "Signature has
   expired", 401-ing every authenticated route. `make_app()` sets `TZ=UTC` for the
   process (`time.tzset()`) before building the app — the "env" half of the allowed
   patch surface, so `app/utils/jwt.py` itself is untouched.
4. **New dependency (`python-jose`, added to the `import_repos` group in
   `pyproject.toml`):** `app/utils/jwt.py` imports `from jose import JWTError, jwt`
   unconditionally — the whole auth system needs it, so it could not be stubbed the
   way `requests` was. `sqlmodel`, `pydantic-settings`, `pwdlib` (bcrypt) and
   `email-validator` are already covered by the existing `template` group.
5. **`app/models/{user,note,tag}.py` (multi-app process, not upstream's fault):**
   every `table=True` class's base is `TableBase` (from the new `app/_lattice_registry.py`,
   Lattice-owned) instead of `SQLModel` directly; field names, types, and defaults are
   unchanged. `bench/template_app`, this repo, and `bench/import_repos/taskflow` are
   all vendored into *one* test process, and SQLModel's table classes share one global
   registry/`MetaData` by default. Two distinct collisions showed up: this app's
   `User` has no explicit `__tablename__`, so it defaults to `"user"` -- identical to
   `template_app.User`'s -- raising `sqlalchemy.exc.InvalidRequestError: Table 'user'
   is already defined for this MetaData instance` at class-definition time; separately,
   `Note.owner`/`User.notes` resolve their `Relationship(...)` forward-refs ("User",
   "Note") by simple class name against the whole shared registry, and with
   `bench/import_repos/taskflow` *also* defining a class named `User` (its table is
   named `"users"`, so no table-name clash there, but the Python class name still
   collides), that raised `sqlalchemy.exc.InvalidRequestError: Multiple classes found
   for path 'User' in the registry of this declarative base`. `TableBase` gives this
   app's four tables (`User`, `Note`, `Tag`, `Keyword`, plus the `NoteTagLink`/
   `NoteKeywordLink` link tables) their own `sqlalchemy.orm.registry()`, so
   `make_app()`/`seed.py` create tables via `_lattice_registry.metadata_registry.
   metadata.create_all(engine)`, not the shared `SQLModel.metadata`. Nothing about a
   single-app import of this repo (`bench.import_repos.justnotes` alone) would ever
   need this — it exists purely because this monorepo vendors more than one SQLModel
   app into one process.

## SQLite / library-version notes

- `Note`, `Tag`, and `Keyword` timestamps (`createdAt`/`updatedAt`) are Unix
  timestamps (`int`, via `time.time()`), not database `datetime` columns — no SQLModel
  version issue there (contrast `bench/import_repos/taskflow`).
- `python-jose` 3.5.0 (upstream's pin) is what's installed; no compatibility issue
  found running under this project's fastapi/pydantic versions.
