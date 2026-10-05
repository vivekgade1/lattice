# Vendored microblog-api

Phase 2 **dev** app (Flask + Alchemical/SQLAlchemy 2 + APIFairy + Flask-HTTPAuth; plan
`docs/superpowers/plans/2026-10-05-phase2-python-import.md`, W1-C). Used to build and tune
the Flask front end; not a held-out app.

- **Upstream:** https://github.com/miguelgrinberg/microblog-api
- **Commit:** `e250d7115b47010ff0e7051d5868485fff4e32fb` (2024-07-07)
- **Paths vendored:** `api/` (minus `api/fake.py`) and `config.py`
- **License:** MIT, see `LICENSE` (copied unchanged from the repository root; copyright
  Miguel Grinberg).
- **Date vendored:** 2026-10-04.

**Not vendored:** `api/fake.py` (Faker-based `flask fake users|posts` CLI commands; no
routes), `microblog.py` (entry point + `flask db upgrade`), `migrations/` + `alembic.ini`
(tables come from `db.create_all()` in `make_app`), `tests/` (read to write
`fidelity.py`), `requirements*.txt`, `pyproject.toml`, `Dockerfile`, `docker-compose.yml`,
`boot.sh`, `Procfile`, `app.json`, `.env.example`, `.flaskenv`, `tox.ini`, `README.md`.

## Layout

| Path | Origin |
|---|---|
| `api/*.py`, `api/templates/reset.{html,txt}`, `config.py` | upstream (import paths and the mods below) |
| `__init__.py` (`make_app`, `dispose`, `LatticeConfig`), `seed.py`, `fidelity.py`, `PROVENANCE.md` | Lattice-owned |
| `../_phase2_probe.py` | Lattice-owned, shared by the Phase 2 apps |
| `LICENSE` | upstream |

## Routes (27 method x path; 25 probe-able)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/tokens` | HTTP Basic (username or email + password) | answers `{"access_token": <jwt>, "refresh_token": null}` + refresh cookie |
| PUT | `/api/tokens` | none (access + refresh token in body/cookie) | refresh; reuse revokes all the user's tokens |
| DELETE | `/api/tokens` | bearer | revoke (expires in 5 s outside testing) |
| POST, PUT | `/api/tokens/reset` | none | reset request (mail suppressed) / reset with a JWT |
| GET, POST | `/api/tokens/oauth2/{provider}` | none | **need the network** (GitHub / Google): excluded and counted |
| POST | `/api/users` | none | register |
| GET | `/api/users`, `/api/users/{id}`, `/api/users/{username}` | bearer | |
| GET, PUT | `/api/me` | bearer | PUT with `password` needs a correct `old_password` (400) |
| GET | `/api/me/following`, `/api/me/followers` | bearer | |
| GET, POST, DELETE | `/api/me/following/{id}` | bearer | 204 / 404 / 409 |
| GET | `/api/users/{id}/following`, `/api/users/{id}/followers`, `/api/users/{id}/posts` | bearer | |
| POST, GET | `/api/posts` | bearer | create stamps `author` |
| GET, PUT, DELETE | `/api/posts/{id}` | bearer | PUT / DELETE: **403** unless the caller is the author |
| GET | `/api/feed` | bearer | own + followed users' posts |

Framework routes (excluded): `GET /` (redirect to docs), `/docs`, `/apispec.json`
(APIFairy), `/static/...`.

**Roles and login.** One role (any registered user); no admin, active or role column.
Login: `POST /api/tokens` with HTTP Basic, then `Authorization: Bearer <access_token>`.
Errors are `{"code", "message", "description"}`; validation errors add `errors` (400).

## Local modifications

Every vendored `.py` file starts with `# ruff: noqa`. `LATTICE MOD` marks each changed
line (with the original in the comment):

1. **Imports** (`api/__init__.py`, `api/app.py`, `api/auth.py`, `api/decorators.py`,
   `api/email.py`, `api/errors.py`, `api/models.py`, `api/posts.py`, `api/schemas.py`,
   `api/tokens.py`, `api/users.py`): `from api...` / `from config ...` rewritten to
   `bench.import_repos.microblog...`. In `api/tokens.py` the marker sits on the line
   before a backslash-continued import.
2. **`config.py`:** `from dotenv import load_dotenv` and `load_dotenv()` commented out.
   Run from this repository, `load_dotenv()` would load the host project's `.env` into
   the process environment; the app must never read it.
3. **`api/app.py`:** the `api.fake` import and `register_blueprint(fake)` commented out
   (`api/fake.py` not vendored: it only adds CLI commands and needs Faker).

No route, schema, model or auth logic was changed.

## Configuration (`__init__.py`)

`LatticeConfig` subclasses upstream `Config` and pins every value upstream reads from the
environment to its documented default (or a test-safe value), so the host environment
cannot change behaviour: the database URL (per `make_app`), `SECRET_KEY`,
`DISABLE_AUTH=False`, token lifetimes, `REFRESH_TOKEN_IN_COOKIE=True`,
`REFRESH_TOKEN_IN_BODY=False`, `USE_CORS=True`, mail settings plus
`MAIL_SUPPRESS_SEND=True`, OAuth2 client ids `None`.

It also replaces `flask_mail.make_msgid` with one that has a fixed domain. Flask-Mail
calls `email.utils.make_msgid()` for every message, and that calls `socket.getfqdn()`,
a reverse DNS lookup that took 35 s on the vendoring machine. The password-reset route
sends its mail from a non-daemon thread, so each reset request kept the process alive
for that long after it was otherwise done.

## Library-version notes

- `marshmallow` is pinned `<4` (and `flask-marshmallow <1.5`, which needs marshmallow 4):
  marshmallow 4 passes a `data_key` keyword to `@validates` methods, which upstream's
  `validate_username(self, value)` does not accept.
- The database extension is `alchemical` (not Flask-SQLAlchemy). Its `db` is a
  module-level singleton whose engine every `create_app` re-initialises: one app at a
  time per process; `dispose()` disposes of that engine.
- `requests` is needed only by the OAuth2 routes.

## Upstream behaviour kept as is

- `DELETE /api/tokens` expires the token 5 seconds later (immediately only when
  `TESTING`), so a revoked token still works for a few seconds.
- Any authenticated user can read every user's email address.
- Stored times are naive UTC.

## Seed data (`seed.py`)

`alice`, `bob`, `carol` (no inactive flag exists). Posts 1-2 by `alice`, 3-4 by `bob`;
`alice` follows `bob`, `carol` follows `alice`. `ROLES = {user}`, `REFUSED = {}`.

## Fidelity (`fidelity.py`)

15 scenarios, written from the upstream API docs and tests and recorded against the
running app on the seed above.
