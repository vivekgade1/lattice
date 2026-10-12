# Vendored recipe-app-api

Phase 2 **dev** app (Django + DRF, Token auth; plan
`docs/archive/superpowers/plans/2026-10-05-phase2-python-import.md`, W1-C). Used to build and tune
the Django front end; not a held-out app.

- **Upstream:** https://github.com/LondonAppDeveloper/recipe-app-api
- **Commit:** `36b5c348d9723fa57e8e03d23fc6d3b0308586a8` (2024-11-22)
- **Path vendored:** `app/` -> `bench/import_repos/recipes/app` (minus tests, see below)
- **License:** MIT, see `LICENSE` (copied unchanged from the repository root).
- **Date vendored:** 2026-10-04.

**Not vendored:** `app/*/tests/` (read to write `fidelity.py`), `app/manage.py`,
`app/app/wsgi.py`, `app/core/management/` (`wait_for_db`, a Docker start-up helper),
`app/.flake8`, `Dockerfile`, `docker-compose.yml`, `.travis.yml`, `requirements.txt`
(Django 2.1 / DRF 3.9 / psycopg2 / Pillow 5 pins; this project's `phase2-apps` group
supplies Django 6.1, DRF 3.18 and Pillow 12), `README.md`.

## Layout

| Path | Origin |
|---|---|
| `app/app/{__init__,settings,urls}.py` | upstream project package (settings: import paths only) |
| `app/core/{__init__,admin,apps,models}.py`, `app/core/migrations/*` | upstream (`core`: custom `User`, `Tag`, `Ingredient`, `Recipe`) |
| `app/user/*`, `app/recipe/*` (minus tests) | upstream |
| `app/__init__.py` | Lattice-owned (upstream `app/` is a directory, not a package) |
| `__init__.py` (`make_app`, `dispose`), `lattice_settings.py`, `seed.py`, `fidelity.py`, `PROVENANCE.md` | Lattice-owned |
| `../_django_runtime.py`, `../_phase2_probe.py` | Lattice-owned, shared by the Phase 2 apps |
| `LICENSE` | upstream |

## Routes (17 method x path)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/user/create/` | none | `password` write-only, min length 5 |
| POST | `/api/user/token/` | none | JSON `email` + `password`; answers `{"token": ...}`; 400 on bad credentials or an inactive user |
| GET, PUT, PATCH | `/api/user/me/` | token | the caller |
| GET | `/api/recipe/` | none | `DefaultRouter` API root |
| GET, POST | `/api/recipe/tags/` | token | caller's rows only (`get_queryset`), ordered `-name`; `?assigned_only=1`; create stamps `user` |
| GET, POST | `/api/recipe/ingredients/` | token | as tags |
| GET, POST | `/api/recipe/recipes/` | token | caller's rows only; `?tags=1,2&ingredients=3`; create stamps `user` |
| GET, PUT, PATCH, DELETE | `/api/recipe/recipes/{pk}/` | token | another user's recipe is **404** (filtered queryset) |
| POST | `/api/recipe/recipes/{pk}/upload-image/` | token | multipart `image`; writes under `MEDIA_ROOT` |

Framework routes (excluded): `/admin/...` and the router's format-suffix duplicates.

**Roles and login.** Custom `core.User` (`AUTH_USER_MODEL`, email as username,
`is_active`, `is_staff`, `is_superuser`). Two roles: superuser/staff (`admin`) and plain
user; no API route treats them differently. Login: `POST /api/user/token/` (JSON), then
`Authorization: Token <key>` (DRF `TokenAuthentication`; the `authtoken_token` table is
the framework's). Anonymous calls answer **401**.

## Local modifications

Every vendored `.py` file starts with `# ruff: noqa`. `LATTICE MOD` marks each changed
line (with the original in the comment):

| File | Lines | Reason |
|---|---|---|
| `app/app/settings.py` | `'core'`, `'user'`, `'recipe'` in `INSTALLED_APPS`; `ROOT_URLCONF` | package path under `bench/` |
| `app/app/urls.py` | `include('user.urls')`, `include('recipe.urls')` | import path |
| `app/core/apps.py`, `app/user/apps.py`, `app/recipe/apps.py` | `name = 'bench.import_repos.recipes.app.<app>'` | package path; the label stays `core` / `user` / `recipe`, so `AUTH_USER_MODEL = 'core.User'` and the migrations are unchanged |
| `app/core/admin.py` | `from core import models` | import path |
| `app/core/migrations/0005_recipe_image.py` | `import core.models`; `upload_to=core.models.recipe_image_file_path` | import path |
| `app/recipe/views.py` | `from core.models ...`; `from recipe import serializers` | import path |
| `app/recipe/serializers.py` | `from core.models ...` | import path |
| `app/recipe/urls.py` | `from recipe import views` | import path |
| `app/user/urls.py` | `from user import views` | import path |
| `app/user/views.py` | `from user.serializers ...` | import path |
| `app/user/serializers.py` | `ugettext_lazy` -> `gettext_lazy` | removed in Django 4.0 |

No model, serializer, permission or view logic was changed.

## Test settings (`lattice_settings.py`)

Imports `app/app/settings.py` and overrides: `DATABASES` (SQLite instead of Postgres from
`DB_*` env vars; `make_app` points the connection at its `db_path`), `MEDIA_ROOT` /
`STATIC_ROOT` (upstream: `/vol/web/...` Docker volumes; `make_app` moves `MEDIA_ROOT` to
`<db_path's directory>/media`), `CACHES` (LocMem), `MAILERS` (locmem), `SECRET_KEY`,
`DEBUG=False`, `ALLOWED_HOSTS` (`testserver`), `DEFAULT_AUTO_FIELD = AutoField` (what
Django 2.1 used and the upstream migrations record), and `PASSWORD_HASHERS = [MD5]`
(speed; hashes still come from `make_password`). Nothing reads the environment or a
`.env` file.

## Upstream behaviour kept as is

- A recipe may reference **another user's** tags and ingredients by id
  (`PrimaryKeyRelatedField(queryset=Tag.objects.all())`): `alice` can attach `bob`'s tag.
- `?ingredients=1,2` returns a recipe once per matching ingredient (no `distinct()`).
- `?tags=x` with a non-integer answers 500 (`int()` on the query string).
- Another user's recipe is 404, not 403.

## Seed data (`seed.py`)

`admin` (superuser), `alice`, `bob`, `carol` (inactive). Tags 1-2 and ingredients 1-2, 6
(`Pepper`, on no recipe) owned by `alice`; tags 3-4 and ingredients 3-5 by `bob`; tag 5
by `carol`. Recipes 1-2 `alice`'s, 3-4 `bob`'s, with tag and ingredient links.
`ROLES = {admin, user}`, `REFUSED = {carol}`.

## Fidelity (`fidelity.py`)

17 scenarios, written from the upstream tests and recorded against the running app on
the seed above.
