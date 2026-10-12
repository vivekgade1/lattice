# Vendored project-management-api

Phase 2 **held-out** app H2 (Flask 3 + Flask-SQLAlchemy 3, PyJWT, bcrypt; plan
`docs/archive/superpowers/plans/2026-10-05-phase2-python-import.md`, §6 and Wave 4). Vendored after
the importer was frozen at `1cda54a`, by an agent that has not seen the importer's output
for any app.

- **Upstream:** https://github.com/GuilhermeCAz/project-management-api
- **Commit:** `bfff55412e6785ba0222d0dc92e6fc17d489d64c` (2025-10-30)
- **Paths vendored:** `app/` (all of it), `config.py`
- **License:** MIT, see `LICENSE` (copied unchanged from the repository root; copyright
  GuilhermeCAz).
- **Date vendored:** 2026-10-05.

**Not vendored:** `run.py` (dev-server entry point), `tests/` (read to write `fidelity.py`),
`docs/`, `postman_collection.json`, `README.md`, `TASK.md`, `DESIGN_RATIONALE.md`,
`.env.example`, `.python-version`, `pyproject.toml`, `requirements.txt`, `uv.lock` (Flask
3.1.2 / Flask-SQLAlchemy 3.1.1 / PyJWT 2.10 / bcrypt 5.0 / loguru 0.7.3 pins; this
project's `phase2_apps` group supplies Flask 3.1.3, Flask-SQLAlchemy 3.1.1, PyJWT 2.15,
bcrypt 5.0 and loguru 0.7.3).

## Layout

| Path | Origin |
|---|---|
| `app/**/*.py`, `config.py` | upstream (mods below) |
| `__init__.py` (`make_app`, `dispose`, `LatticeConfig`), `seed.py`, `fidelity.py`, `PROVENANCE.md` | Lattice-owned |
| `LICENSE` | upstream |

## Routes (23 method x path)

| Method | Path | Guard | Notes |
|---|---|---|---|
| GET | `/` | none | API description |
| POST | `/auth/login` | none | JSON email + password -> `access_token`, `refresh_token`, `user` |
| POST | `/auth/register` | none | creates a user; `user_type` defaults to `employee` (a caller may register as `manager`) |
| POST | `/auth/refresh` | refresh token in the body | |
| POST | `/auth/logout` | `token_required` | |
| GET | `/auth/verify` | its own Bearer check | |
| GET | `/users`, `/users/{id}` | `token_required` | `?user_type=`, `?limit=`, `?offset=`, `?include_projects=` |
| POST | `/users` | `manager_required` | default password `defaultpassword123` |
| PUT, DELETE | `/users/{id}` | `manager_required` | delete cascades to projects and tasks |
| GET | `/projects`, `/projects/{id}` | none | `?user_id=`, `?include_tasks=` |
| POST | `/projects` | `manager_required` | owner from the body's `user_id` (must exist) |
| PUT, DELETE | `/projects/{id}` | `manager_required` | any project |
| GET | `/tasks` | `token_required` | `?status=`, `?project_id=` |
| POST | `/tasks` | `token_required` | any project (body `project_id`) |
| GET | `/tasks/{id}` | none | |
| PUT, DELETE | `/tasks/{id}` | `token_required` | any task |
| GET | `/projects/{id}/tasks` | none | |
| POST | `/projects/{id}/tasks` | `manager_required` | |

Framework route (excluded): `/static/...`.

**Roles and login.** `users.user_type` is the enum `UserType` (`manager`, `employee`).
`token_required` / `manager_required` read `Authorization: Bearer <access JWT>` (PyJWT
HS256 with `SECRET_KEY`): no header or a non-Bearer header is 401, a bad or expired token
401, a non-manager on a `manager_required` route 403 with
`{"error": "Access denied. Manager role required.", "user_type": "employee"}`. There is no
active flag. Projects carry an owner (`user_id`), but no route scopes by it: anyone reads
every project, managers change any project, and any logged-in user changes any task.

## Local modifications

Every vendored `.py` file starts with `# ruff: noqa`. `LATTICE MOD` marks each changed
line (with the original in the comment). 40 lines: 38 import paths and 2 `.env` lines; no
model, validator, decorator or route logic was changed.

| File | Lines | Reason |
|---|---|---|
| `config.py` | 11 `from dotenv import load_dotenv`, 13 `load_dotenv()` | disable `.env` loading (both lines commented out) |
| `app/__init__.py` | 22, 23, 39-42 (`app.logging_config`, `config`, the four blueprints) | import path (`app` -> `bench.import_repos.projmgmt.app`, `config` -> `bench.import_repos.projmgmt.config`) |
| `app/middleware/__init__.py` | 4 | import path |
| `app/middleware/auth.py` | 14, 63, 125 (`app.users.models`, the two in-function `app.auth.services` imports) | import path |
| `app/auth/routes.py` | 9-13 | import path |
| `app/auth/services.py` | 17-19 | import path |
| `app/auth/validators.py` | 6 | import path |
| `app/users/models.py` | 12 | import path |
| `app/users/routes.py` | 10-13 | import path |
| `app/users/validators.py` | 8 | import path |
| `app/projects/models.py` | 9 | import path |
| `app/projects/routes.py` | 9-12 | import path |
| `app/tasks/models.py` | 10 | import path |
| `app/tasks/routes.py` | 9-14 | import path |
| `app/tasks/validators.py` | 14 | import path |

No compatibility fix was needed (upstream targets Flask 3 / SQLAlchemy 2).

## Test configuration (`__init__.py`)

Upstream's `create_app()` takes no argument and picks `config.config[os.getenv('FLASK_ENV',
'development')]` (development: a SQLite file inside the source tree). `make_app(db_path)`
registers a per-call subclass of upstream's `Config` (fixed `SECRET_KEY`, `DEBUG=False`,
`SQLALCHEMY_DATABASE_URI=sqlite:///<db_path>`) under a private name, sets `FLASK_ENV` to
that name only while `create_app()` runs and restores it, then removes the name.
`create_app()` itself creates the tables (`db.create_all()`). Dispose is
`db.engine.dispose()`.

`create_app()` runs upstream's `setup_logging()`, which routes all logging to a queued
loguru sink on **stdout**. `make_app` replaces that sink with a synchronous stderr sink
(WARNING and above), so machine-read stdout stays clean; no route behaves differently.
Upstream sends no mail and calls no outside service.

## Upstream behaviour kept as is

- `created_at` / `updated_at` defaults on `users` and `projects` are `datetime.now(UTC)`
  evaluated once at import (every row gets the process start time); `tasks` uses a lambda.
- `POST /auth/register` lets a caller choose `user_type: "manager"`.
- Project ownership (`user_id`) is not enforced by any route.
- `PUT /projects/{id}` validates but ignores a `user_id` in the body.
- Unexpected errors answer 500 with `{"error": "... failed: ..."}`.

## Seed data (`seed.py`)

`role_manager` (Maya, manager, id 1), `alice` (id 2) and `bob` (id 3), employees; passwords
hashed with bcrypt by `User.set_password`. Projects 1-2 owned by `alice`, 3 by `bob`.
Tasks 1 (pending) and 2 (in_progress) on project 1, 3 (completed) on project 2, 4 (pending)
on project 3. `ROLES = {manager: role_manager, employee: alice}`, `REFUSED = {}` (no active
flag).

## Fidelity (`fidelity.py`)

19 scenarios, written from the upstream tests (`tests/test_*.py`), README and route
docstrings, then recorded against the running app on the seed above.
