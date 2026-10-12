# Vendored flasky (`app/api` only)

Phase 2 **dev** app (Flask + Flask-SQLAlchemy + Flask-HTTPAuth; plan
`docs/archive/superpowers/plans/2026-10-05-phase2-python-import.md`, W1-C). Used to build and tune
the Flask front end; not a held-out app.

- **Upstream:** https://github.com/miguelgrinberg/flasky
- **Commit:** `3beedd640b9146b0bd65c8c2ecf402b01798bc33` (2025-04-06)
- **Paths vendored:** `app/api/`, `app/models.py`, `app/exceptions.py`, `app/__init__.py`
  (cut down, below), `config.py`
- **License:** MIT, see `LICENSE` (copied unchanged from the repository root; copyright
  Miguel Grinberg).
- **Date vendored:** 2026-10-04.

**Not vendored:** the HTML application (`app/main/`, `app/auth/`, `app/templates/`,
`app/static/`, `app/decorators.py`, `app/email.py`, `app/fake.py`), `flasky.py`,
`migrations/` (tables come from `db.create_all()` in `make_app`), `tests/` (read to write
`fidelity.py`), `requirements*`, `Dockerfile`, `docker-compose.yml`, `boot.sh`,
`Procfile`, `README.md`.

## Layout

| Path | Origin |
|---|---|
| `app/api/*.py`, `app/models.py`, `app/exceptions.py`, `config.py` | upstream (mods below) |
| `app/__init__.py` | upstream, cut down to the API (mods below) |
| `app/_lattice_compat.py` | Lattice-owned (`TimedJSONWebSignatureSerializer` stand-in) |
| `__init__.py` (`make_app`, `dispose`, `LatticeConfig`), `seed.py`, `fidelity.py`, `PROVENANCE.md` | Lattice-owned |
| `../_phase2_probe.py` | Lattice-owned, shared by the Phase 2 apps |
| `LICENSE` | upstream |

## Routes (12 method x path, all under `/api/v1`)

| Method | Path | Guard (besides login + confirmed) | Notes |
|---|---|---|---|
| POST | `/tokens/` | email + password, not a token | `{"token", "expiration": 3600}` |
| GET | `/posts/`, `/posts/{id}` | | paginated (20) |
| POST | `/posts/` | `permission_required(WRITE)` | stamps `author`; empty body 400 |
| PUT | `/posts/{id}` | `WRITE`, and author or `ADMIN` | else 403 "Insufficient permissions" |
| GET | `/users/{id}`, `/users/{id}/posts/`, `/users/{id}/timeline/` | | |
| GET | `/comments/`, `/comments/{id}`, `/posts/{id}/comments/` | | |
| POST | `/posts/{id}/comments/` | `permission_required(COMMENT)` | stamps `author` and `post` |

Framework route (excluded): `/static/...`.

**Roles and login.** `roles` rows with a permission bitmask (`Permission`: FOLLOW 1,
COMMENT 2, WRITE 4, MODERATE 8, ADMIN 16): `User` (7, the default), `Moderator` (15),
`Administrator` (31); `Role.insert_roles()` creates them (run by `make_app`). Every API
route runs `before_request` under `HTTPBasicAuth.login_required`: HTTP Basic with
email + password, **or** with a token as the username and an empty password. An
unconfirmed account (`confirmed=False`) gets 403 "Unconfirmed account". Login for probes:
`POST /api/v1/tokens/` with Basic email:password, then `Authorization: Basic
base64(<token>:)`. No API route uses `MODERATE`, so a Moderator acts as a User here.

## Local modifications

Every vendored `.py` file starts with `# ruff: noqa`. `LATTICE MOD` marks each changed
line (with the original in the comment):

1. **Imports:** `from app.exceptions import ValidationError` -> the `bench...` path in
   `app/models.py` and `app/api/errors.py`; `from config import config` in
   `app/__init__.py`. Relative imports (`from .. import db`) are unchanged.
2. **`app/__init__.py` (cut down to the API):** the HTML-only extensions
   (Flask-Bootstrap, Flask-Moment, Flask-PageDown) and Flask-Mail (used only by the
   unvendored `app/email.py`) are commented out with their `init_app` calls, as are the
   `main` and `auth` blueprint registrations. `db`, `login_manager` (the models use it)
   and the `api` blueprint are unchanged.
3. **`app/models.py`:** `from itsdangerous import TimedJSONWebSignatureSerializer as
   Serializer` now imports `app/_lattice_compat.TimedJSONWebSignatureSerializer`.
   itsdangerous 2.1 removed the class; the stand-in keeps the interface flasky uses
   (`Serializer(key, expires_in)`, `dumps() -> bytes`, `loads()` raising on bad or expired
   tokens, expiry enforced by serializers built without `expires_in`).
4. **`config.py`:** `ProductionConfig.SERVER_NAME = os.environ['SERVER_NAME']` ->
   `os.environ.get(...)`: the class body runs at import, so the module could not be
   imported without that variable. `ProductionConfig` is not used.

No route, model, permission or auth logic was changed.

## Configuration (`__init__.py`)

Upstream's `create_app(config_name)` looks the config class up in `config.config`.
`make_app` registers a per-call subclass of upstream `Config` (`LatticeConfig`: database
URL, `SECRET_KEY`, mail settings, `FLASKY_ADMIN=None`, `SSL_REDIRECT=False`, i.e. every
environment-derived value pinned) under a private name, builds the app, and removes the
name. `flasky.py` (not vendored) loaded a `.env` next to itself; nothing here does.

## Library-version notes

Upstream pins Flask 0.12 / Flask-SQLAlchemy 2.2 / SQLAlchemy 1.1 / itsdangerous 0.24 /
Flask-HTTPAuth 3.2 / bleach 2.0 / Markdown 2.6; this runs on Flask 3.1, Flask-SQLAlchemy
3.1 (SQLAlchemy 2.0), itsdangerous 2.2, Flask-HTTPAuth 4.8, bleach 6.4, Markdown 3.11
with only mod 3. `Query.get()` emits SQLAlchemy's legacy-API warning; `datetime.utcnow`
emits a deprecation warning.

## Upstream behaviour kept as is

- A missing record answers Flask's HTML 404 page: the JSON 404 handler lived in the
  unvendored `app/main/errors.py` (upstream's `test_404` expects JSON).
- A Moderator cannot edit others' posts through the API (only `ADMIN` bypasses
  authorship); comment moderation is HTML-only.
- `POST /posts/{id}/comments/` checks `COMMENT` but anyone confirmed can read every
  comment, disabled ones included.

## Seed data (`seed.py`)

`admin` (Administrator), `role_moderator` (Moderator), `alice`, `bob` (User), `carol`
(User, unconfirmed). Posts 1-2 by `alice`, 3-4 by `bob`; comments by `bob` on post 1,
`alice` and `role_moderator` (disabled) on post 3; everyone follows themselves (upstream
`User.__init__`), `alice` follows `bob`. `ROLES = {Administrator, Moderator, User}`,
`REFUSED = {carol}`.

## Fidelity (`fidelity.py`)

15 scenarios, written from the upstream API tests and recorded against the running app
on the seed above.
