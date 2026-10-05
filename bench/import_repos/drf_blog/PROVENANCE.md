# Vendored drf_blog_api

Phase 2 **held-out** app H1 (Django + DRF, simplejwt; plan
`docs/superpowers/plans/2026-10-05-phase2-python-import.md`, §6 and Wave 4). Vendored after
the importer was frozen at `1cda54a`, by an agent that has not seen the importer's output
for any app.

- **Upstream:** https://github.com/amirpsd/drf_blog_api
- **Commit:** `047e197bc48dba6bc75146de08b987b06ec7f8af` (2024-06-06)
- **Path vendored:** `src/` -> `bench/import_repos/drf_blog/src` (minus what is listed below)
- **License:** MIT, see `LICENSE` (copied unchanged from the repository root; copyright
  amirreza pasandi).
- **Date vendored:** 2026-10-05.

**Not vendored:** `.env` and `.env.db` (never opened; upstream commits them), `src/*/tests/`
(read to write `fidelity.py`), `src/manage.py`, `src/config/wsgi.py`, `src/config/asgi.py`,
`src/locale/`, `src/log/`, `src/media/`, `src/static/`, `requirements/` (Django 5.0 / DRF
3.15 / simplejwt 5.3 / drf-spectacular 0.27 pins; this project's `phase2_apps` group supplies
Django 6.1, DRF 3.18, simplejwt 5.5, drf-spectacular 0.30 and its sidecar), `Dockerfile`,
`docker-compose.yml`, `nginx/`, `.github/`, `endpoints.yaml`, `README.md`.

## Layout

| Path | Origin |
|---|---|
| `src/config/{__init__,settings,urls,logger}.py`, `src/config/utils/custom_pagination.py` | upstream project package (mods below) |
| `src/account/`, `src/blog/`, `src/comment/` (minus tests), `src/extensions/`, `src/permissions.py` | upstream (mods below) |
| `src/__init__.py` | Lattice-owned (upstream `src/` is Django's `BASE_DIR`, not a package) |
| `_lattice_env.py` | Lattice-owned stand-in for `decouple.config` (fixed values; no `.env`, no environment) |
| `__init__.py` (`make_app`, `dispose`), `lattice_settings.py`, `seed.py`, `fidelity.py`, `PROVENANCE.md` | Lattice-owned |
| `../_django_runtime.py` | Lattice-owned, shared by the Phase 2 Django apps |
| `LICENSE` | upstream |

Upstream's `account/api/`, `blog/api/`, `comment/api/` and `config/utils/` have no
`__init__.py` (namespace packages); that is kept.

## Routes (30 method x path)

| Method | Path | Guard | Notes |
|---|---|---|---|
| GET | `/account/api/` | `IsSuperUser` | users list; `?author=`, `?search=`, `?ordering=` |
| GET, PUT, PATCH, DELETE | `/account/api/users/{pk}/` | `IsSuperUser` | 404 for an unknown pk |
| GET, PUT, PATCH | `/account/api/profile/` | `IsAuthenticated` | the caller; `phone` read-only |
| POST | `/account/api/login/` | none, throttled 5/hour | registered phone -> `{"otp": ...}` |
| POST | `/account/api/register/` | none, throttled 5/hour | new phone -> `{"otp": ...}` |
| POST | `/account/api/verify/` | none, throttled 8/hour | OTP -> simplejwt `refresh`/`access` (creates the user on register) |
| POST | `/account/api/verify-two-step-password/` | none | after `verify` for a user with a two-step password |
| POST | `/account/api/create-two-step-password/`, `/account/api/change-two-step-password/` | `IsAuthenticated` | |
| DELETE | `/account/api/delete-account/` | `IsAuthenticated` | |
| GET | `/blog/api/` | none (default `IsSuperUserOrReadOnly`) | published blogs only |
| POST | `/blog/api/create/` | `IsSuperUserOrAuthor` | stamps `author`; a non-superuser's blog is forced to draft, not special |
| GET, PUT, PATCH, DELETE | `/blog/api/{slug}/` | `IsSuperUserOrAuthorOrReadOnly` (object) | published blogs only; an author's update forces draft |
| GET | `/blog/api/like/{pk}/` | `IsAuthenticated` | toggles the caller's like (a GET that writes) |
| GET | `/blog/api/category/list/`, `/blog/api/category/{slug}/` | none | active categories only |
| GET | `/comment/api/{pk}/` | none (default) | comments of a published blog |
| POST | `/comment/api/create/` | `IsAuthenticated` | stamps `user` |
| PUT, DELETE | `/comment/api/update-delete/{pk}/` | `IsAuthenticated` | another user's comment is **404** (`get_object_or_404(..., user=request.user)`) |
| POST | `/api/token/refresh/`, `/api/token/verify/` | none | simplejwt |

Framework routes (excluded): `/admin/...`, `/api/schema/`, `/api/schema/swagger-ui/`,
`/api/schema/redoc/` (drf-spectacular).

**Roles and login.** Custom `account.User` (`AUTH_USER_MODEL`, `phone` as username, an
unusable password). Three roles: superuser (`is_superuser`), author (`author=True`), plain
user. There is no `is_active` column (`AbstractBaseUser.is_active` is the constant `True`),
so no user can be made inactive. Login is two-step through the app's own routes:
`POST /account/api/login/` `{"phone"}` answers `{"otp": "<6 digits>"}` (upstream returns the
code instead of sending an SMS: "in debug mode we return the otp code", and it does so
whatever `DEBUG` is), then `POST /account/api/verify/` `{"code"}` answers simplejwt tokens;
requests then send `Authorization: Bearer <access>`. Authentication classes are Session then
JWT, so an anonymous call answers **403** (not 401).

## Local modifications

Every vendored `.py` file starts with `# ruff: noqa`. `LATTICE MOD` marks each changed
line (with the original in the comment). 31 lines, all import paths or `.env` loading; no
model, serializer, permission or view logic was changed.

| File | Lines | Reason |
|---|---|---|
| `src/config/settings.py` | 15 `from decouple import config` | disable `.env` loading: python-decouple searches for a `.env` next to the caller and in every parent directory, and reads `os.environ`; replaced by `_lattice_env.config` (fixed values) |
| `src/config/logger.py` | 2 `from decouple import config` | same |
| `src/config/urls.py` | 30 `from decouple import config` | same |
| `src/config/settings.py` | 44-47 `INSTALLED_APPS` (`blog`, `account`, `comment`, `extensions`) | package path under `bench/`; the labels stay `blog` / `account` / `comment` / `extensions`, so `AUTH_USER_MODEL = 'account.User'` and the migrations are unchanged |
| `src/config/settings.py` | 66 `ROOT_URLCONF`; 185 `DEFAULT_PERMISSION_CLASSES` (`permissions.IsSuperUserOrReadOnly`); 207 `DEFAULT_PAGINATION_CLASS` | import path |
| `src/config/urls.py` | 34-36 `include('account.urls')`, `include('blog.urls')`, `include('comment.urls')` | import path |
| `src/account/apps.py`, `src/blog/apps.py`, `src/comment/apps.py` | 7 `name = ...` | package path under `bench/`; the label stays the last component |
| `src/blog/apps.py` | 10 `import blog.signals` | import path |
| `src/account/urls.py`, `src/blog/urls.py`, `src/comment/urls.py` | 7 / 8 / 7 `include("<app>.api.urls")` | import path |
| `src/account/api/views.py` | 30 `from extensions.utils ...`, 31 `from permissions import IsSuperUser` | import path |
| `src/account/api/send_otp.py` | 8 `from extensions.utils ...` | import path |
| `src/blog/models.py` | 7 `from extensions.utils ...` | import path |
| `src/blog/signals.py` | 6 `from extensions.utils ...` | import path |
| `src/blog/api/serializers.py` | 4 `from blog.models ...` | import path |
| `src/blog/api/views.py` | 13 `from blog.models ...`, 20 `from permissions import (` | import path |
| `src/comment/api/views.py` | 10 `from blog.models ...` | import path |
| `src/blog/migrations/0001_initial.py` | 8 `import extensions.utils`, 42 `upload_to=extensions.utils.upload_file_path` | import path |

No compatibility fix was needed for Django 6.1 / DRF 3.18.

## Test settings (`lattice_settings.py`, `_lattice_env.py`)

`_lattice_env.config` answers `SECRET_KEY`, `DEBUG="False"` (upstream's non-debug branch:
`DB_*` database, `STATIC_ROOT`, no static URL patterns), `ALLOWED_HOSTS`, `DB_*` (SQLite
placeholders) and `EMAIL_BACKEND` (locmem). `lattice_settings.py` imports the upstream
settings and overrides `DATABASES` (SQLite instead of Postgres; `make_app` points the
connection at its `db_path`), `CACHES` (LocMem, which is also upstream's default: OTP codes
and throttle counters live there), `EMAIL_BACKEND` (locmem), `SECRET_KEY`, `DEBUG=False`,
`ALLOWED_HOSTS` (`testserver`), `MEDIA_ROOT` / `STATIC_ROOT` (off the vendored tree;
`make_app` moves `MEDIA_ROOT` next to the database), `LOGGING` (upstream adds a
`FileHandler` on `src/log/app.log` inside the vendored tree; console only here) and
`PASSWORD_HASHERS = [MD5]` (speed; two-step passwords are still hashed by `set_password`).
Nothing reads the environment or a `.env` file. No mail or SMS is sent by upstream (the
OTP is returned in the answer), so no outside service needs stubbing.

`make_app` clears the cache on every call: each app starts like a fresh server process
(upstream's LocMem cache is per process). The throttle rates are upstream's, unchanged.

## Upstream behaviour kept as is

- The OTP is returned in the login / register answer (no SMS).
- `GET /blog/api/like/{pk}/` writes (toggles a like).
- An author's `PUT` of their own published blog turns it into a draft, after which the
  detail route answers 404 (it reads published blogs only).
- Another user's comment is 404 on update/delete, not 403.
- `UserProfileSerializer`'s `phone` is read-only; `UserDetailUpdateDeleteSerializer`
  exposes every column but `password` (including `is_superuser`, `groups`).
- Login, register and verify are throttled per client IP (5/hour, 5/hour, 8/hour).
- `account/api/serializers.py` has an invalid escape (`"\d"`) in a regex string: a
  `SyntaxWarning` on import, same behaviour.

## Seed data (`seed.py`)

`admin` (superuser, id 1), `alice` and `bob` (authors, ids 2-3), `role_user` (plain, id 4);
phones `98910000000{1-4}`. Categories 1 Technology, 2 Lifestyle, 3 Archived (inactive),
4 Python (child of 1). Blogs: 1 `alice-on-django` (alice, published), 2 `alice-draft-notes`
(alice, draft), 3 `bob-on-python` (bob, published, special), 4 `bob-draft-notes` (bob,
draft), 5 `archived-news` (admin, published). Comments: 1 bob on blog 1, 2 alice on blog 3,
3 role_user on blog 1 (reply to 1). Likes: role_user on 1, alice on 3.
`ROLES = {admin, author: alice, user: role_user}`, `REFUSED = {}` (no user can be inactive).

## Fidelity (`fidelity.py`)

23 scenarios, written from the upstream tests (`src/*/tests/test_views.py`) and README,
then recorded against the running app on the seed above.
