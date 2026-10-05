# Vendored conduit-drf

Phase 2 **held-out** app H3 (Django 4.2 + DRF, simplejwt; plan
`docs/superpowers/plans/2026-10-05-phase2-python-import.md`, §6 and Wave 4). Vendored after
the importer was frozen at `1cda54a`, by an agent that has not seen the importer's output
for any app.

- **Upstream:** https://github.com/yoonge/conduit-drf
- **Commit:** `bcba78764216326c04098c10f32ef148cd45f0e6` (2024-06-02)
- **Paths vendored:** `api/`, `conduit_drf/` (minus what is listed below)
- **License:** MIT, see `LICENSE` (copied unchanged from the repository root; its
  copyright line reads "PDM", the project scaffold's).
- **Date vendored:** 2026-10-05.

**Not vendored:** `api/tests.py` (Django's empty stub), `api/urls_backup.py` and
`api/views_backup.py` (not imported by anything), `conduit_drf/wsgi.py`,
`conduit_drf/asgi.py`, `manage.py`, `pyproject.toml` / `pdm.lock` (Django ~4.2, DRF 3.15,
mysqlclient, simplejwt 5.3; this project's `phase2_apps` group supplies Django 6.1, DRF
3.18 and simplejwt 5.5; no MySQL driver), `README.md`, `.editorconfig`, `.gitignore`.

## Layout

| Path | Origin |
|---|---|
| `conduit_drf/{__init__,settings,urls}.py` | upstream project package (mods below) |
| `api/*` (models, serializers, views, urls, `utils/`, migrations, admin, apps) | upstream (mods below) |
| `__init__.py` (`make_app`, `dispose`), `lattice_settings.py`, `seed.py`, `fidelity.py`, `PROVENANCE.md` | Lattice-owned |
| `../_django_runtime.py` | Lattice-owned, shared by the Phase 2 Django apps |
| `LICENSE` | upstream |

Upstream's `api/utils/` has no `__init__.py` (a namespace package); that is kept.

## Routes (26 method x path, under `/api/`)

| Method | Path | Guard | Notes |
|---|---|---|---|
| GET | `/api/` | `IsAuthenticatedOrReadOnly` | route listing |
| GET, POST | `/api/topics/` | `IsAuthenticatedOrReadOnly` | create: the owner is the body's `user` (not stamped); `tags` are names, created on the fly |
| GET, PUT, DELETE | `/api/topic/{pk}/` | GET open; PUT, DELETE `IsAuthenticated` + `IsAdminOrOwner` (staff or `obj.user`) | |
| GET | `/api/my-topics/`, `/api/my-favorites/` | `IsAuthenticated` | the caller's |
| GET | `/api/profile/{username}/`, `/api/profile/{username}/favorites/` | `IsAuthenticated` | |
| POST | `/api/topic/{pk}/favor/` | `IsAuthenticated` | toggles the caller's favorite |
| GET, POST | `/api/topic/{pk}/comment/` | `IsAuthenticated` | create stamps `user` and `topic` |
| GET, DELETE | `/api/topic/{_id}/comment/{pk}/` | `IsAuthenticated`; DELETE + `IsAdminOrOwner` | |
| GET | `/api/tags/`, `/api/tag/{tag}/` | `IsAuthenticatedOrReadOnly` | |
| GET, POST | `/api/users/` | `IsAuthenticated` | creating a user needs a login |
| GET, PUT, DELETE | `/api/user/{username}/` | `IsAuthenticated`; PUT + `IsAdminOrSelf`; DELETE + `IsAdminUser` | |
| GET, PUT | `/api/settings/` | `IsAuthenticated` | the caller |
| POST | `/api/token/`, `/api/token/refresh/` | none | simplejwt (upstream's `MyTokenObtainPairSerializer`) |

Framework routes (excluded): `/api/auth/login/`, `/api/auth/logout/` (DRF's browsable-API
login pages, `rest_framework.urls`). The admin is commented out upstream.

**Roles and login.** Custom `api.User` (`AbstractUser`, `_id` primary key, `email` as
`USERNAME_FIELD`, `is_staff`, `is_active`). Two roles: staff (`admin`) and plain user.
Login: `POST /api/token/` with JSON `email` + `password` answers `{"access", "refresh",
"msg", "user"}`; requests send `Authorization: Bearer <access>` (simplejwt,
`USER_ID_FIELD = "_id"`). An inactive user cannot get a token (401). Anonymous calls to a
login-only route answer **401**.

## Local modifications

Every vendored `.py` file starts with `# ruff: noqa`. `LATTICE MOD` marks each changed
line (with the original in the comment). 13 lines, all import paths; no model, serializer,
permission or view logic was changed.

| File | Lines | Reason |
|---|---|---|
| `conduit_drf/settings.py` | 43 `"api",` in `INSTALLED_APPS` | package path under `bench/` |
| `conduit_drf/settings.py` | 56 `ROOT_URLCONF`; 163 `DEFAULT_PAGINATION_CLASS`; 173 `SIMPLE_JWT["TOKEN_OBTAIN_SERIALIZER"]` | import path |
| `conduit_drf/urls.py` | 37 `include("api.urls")` | import path |
| `api/apps.py` | 7 `name = 'api'` | package path under `bench/`; the label stays `api`, so `AUTH_USER_MODEL = "api.User"` and the migrations are unchanged |
| `api/urls.py` | 3 `from api import views` | import path |
| `api/serializers.py` | 11-12 (`api.models`, `api.utils.hook`) | import path |
| `api/views.py` | 12, 13, 22, 23 (`api.models`, `api.serializers`, `api.utils.pagination`, `api.utils.permisson`) | import path |

No compatibility fix was needed for Django 6.1 / DRF 3.18.

## Test settings (`lattice_settings.py`)

Imports `conduit_drf/settings.py` and overrides: `DATABASES` (SQLite instead of MySQL on
`localhost:3306`; `make_app` points the connection at its `db_path`), `CACHES` (LocMem),
`EMAIL_BACKEND` (locmem), `SECRET_KEY`, `DEBUG=False` (upstream: `True`), `ALLOWED_HOSTS`
(`testserver`), and `PASSWORD_HASHERS = [MD5]` (speed; hashes still come from
`set_password`). Upstream reads no environment variable and no `.env` file, sends no mail
and calls no outside service.

## Upstream behaviour kept as is

- **Deleting a comment always fails for a permitted caller:** `CommentViewSet.destroy`
  calls `topic.comments.remove(comment)`, but `Comment.topic` is a non-null foreign key,
  whose reverse manager has no `remove` (Django 4.2 behaves the same). The owner's or
  staff's `DELETE /api/topic/{_id}/comment/{pk}/` answers **500**; refusals (401, 403) and
  unknown ids (`{"code": 404}`) answer before that line.
- Most "not found" and "invalid" outcomes answer **HTTP 200** with the real code in the
  body (`{"code": 404, "msg": "Topic not found."}`, `{"code": 400, "error": ...}`).
- `POST /api/topics/` stores the topic for the `user` the body names (any user id), and a
  missing `tags` key is a `KeyError` (500). `PUT /api/topic/{pk}/` likewise.
- `GET /api/profile/{username}/` with an unknown username raises `DoesNotExist` (500).
- `POST /api/users/` needs a login (every `UserViewSet` action is `IsAuthenticated`), and
  `UserWriteSerializer` (`fields = "__all__"`) accepts `is_superuser`.
- `fetch_topics` prints the favorite ids to stdout.

## Seed data (`seed.py`)

`admin` (superuser/staff, id 1), `alice` (2), `bob` (3), `carol` (4, inactive). Tags 1
python, 2 django, 3 web. Topics 1-2 `alice`'s (1 tagged python), 3 `bob`'s (django, web).
Comments: 1 bob on topic 1, 2 alice on topic 3. Favorites: alice -> 3, bob -> 1 (each
counted in `Topic.favorite`). `ROLES = {admin, user: alice}`, `REFUSED = {carol}`.

## Fidelity (`fidelity.py`)

21 scenarios. Upstream has no tests, so they are written from the README, the `api_root`
listing and the view docstrings, then recorded against the running app on the seed above.
