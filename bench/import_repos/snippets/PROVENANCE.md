# Vendored DRF tutorial ("snippets")

Phase 2 **dev** app (Django + DRF; plan `docs/superpowers/plans/2026-10-05-phase2-python-import.md`,
W1-C). Used to build and tune the Django front end; not a held-out app.

- **Upstream:** https://github.com/encode/django-rest-framework/tree/main/docs/tutorial
  (the code listings of tutorial parts 1, 4, 5 and 6, as the tutorial leaves the project
  at the end of part 6)
- **Commit:** `41764f1f8cfb6d40b77b58c97cf9aeaab57c6b5c` (2026-09-30)
- **License:** BSD-3-Clause, see `LICENSE` (DRF's `LICENSE.md`, copied unchanged;
  copyright Encode OSS Ltd).
- **Date vendored:** 2026-10-04.

## Where the code comes from

The tutorial is documentation, not a repository: its project is built by running
`django-admin startproject tutorial`, `manage.py startapp snippets`, and pasting the
listings. That is what was done here, with Django 6.1.1:

| Path | Origin |
|---|---|
| `snippets/models.py` | tutorial part 1 (`Snippet`, `LANGUAGE_CHOICES`, `STYLE_CHOICES`) + part 4 (`owner`, `highlighted`, `save()`) |
| `snippets/serializers.py` | part 5 (`HyperlinkedModelSerializer` versions) |
| `snippets/permissions.py` | part 4 (`IsOwnerOrReadOnly`) |
| `snippets/views.py` | part 6 (`SnippetViewSet`, `UserViewSet`) |
| `snippets/urls.py` | part 6 (`DefaultRouter`) |
| `tutorial/urls.py` | part 1 (root urlconf) + part 4 (`api-auth/`) |
| `tutorial/settings.py` | `startproject` output + part 1 (`INSTALLED_APPS`) + part 5 (`REST_FRAMEWORK` pagination) |
| `snippets/apps.py`, empty `__init__.py` files | `startapp` / `startproject` output |
| `snippets/migrations/0001_initial.py` | `manage.py makemigrations snippets` (as part 4 instructs), Django 6.1.1, pygments 2.21 |
| `__init__.py` (`make_app`, `dispose`), `lattice_settings.py`, `seed.py`, `fidelity.py`, `PROVENANCE.md` | Lattice-owned |
| `../_django_runtime.py`, `../_phase2_probe.py` | Lattice-owned, shared by the Phase 2 apps |
| `LICENSE` | upstream |

`encode/rest-framework-tutorial` (the tutorial's companion repository) was **not** used:
it carries no license file.

**Not vendored:** `manage.py`, `tutorial/{asgi,wsgi}.py`, and the `startapp` stubs
`snippets/{admin,tests}.py` (unused by the tutorial); the tutorial's intermediate
versions (function views of parts 1-2, generic views of part 3, `api_root` of part 5).

## Routes (10 method x path)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/` | none | DRF `DefaultRouter` API root |
| GET | `/snippets/` | none | paginated, 10 a page |
| POST | `/snippets/` | authenticated | `perform_create` stamps `owner=request.user` |
| GET | `/snippets/{pk}/` | none | |
| PUT, PATCH, DELETE | `/snippets/{pk}/` | authenticated + owner | `IsOwnerOrReadOnly`: 403 for non-owners, superusers included |
| GET | `/snippets/{pk}/highlight/` | none | HTML (`StaticHTMLRenderer`) |
| GET | `/users/`, `/users/{pk}/` | none | read-only |

Framework routes (excluded): `api-auth/login/`, `api-auth/logout/` (browsable-API session
login), and the router's format-suffix duplicates of every route
(`/snippets.json`, `/snippets/{pk}.api`, ...).

**Roles and login.** `django.contrib.auth.User`: superuser / plain user, `is_active`.
DRF defaults: `SessionAuthentication` then `BasicAuthentication`; no token. Probes log in
with HTTP Basic (`username:password`). Because `SessionAuthentication` comes first and
has no `WWW-Authenticate` header, DRF answers **403** (not 401) to an anonymous or
bad-credential write.

## Local modifications

Every file starts with `# ruff: noqa`. `LATTICE MOD` marks each changed line:

| File | Line(s) | Reason |
|---|---|---|
| `snippets/apps.py` | `name = 'bench.import_repos.snippets.snippets'` | package path under `bench/` (was `'snippets'`) |
| `snippets/apps.py` | `label = 'snippets'` (added) | keeps the app label the upstream name gave |
| `snippets/serializers.py` | `from bench.import_repos.snippets.snippets.models import Snippet` | import path (was `from snippets.models ...`) |
| `snippets/views.py` | the three `from bench.import_repos.snippets.snippets... import` lines | import path |
| `snippets/urls.py` | `from bench.import_repos.snippets.snippets import views` | import path |
| `tutorial/urls.py` | `include("bench.import_repos.snippets.snippets.urls")` | import path (was `"snippets.urls"`) |
| `tutorial/settings.py` | `'bench.import_repos.snippets.snippets'` in `INSTALLED_APPS` | package path under `bench/` |
| `tutorial/settings.py` | `ROOT_URLCONF = 'bench.import_repos.snippets.tutorial.urls'` | import path |

No model, serializer, permission or view logic was changed.

## Test settings (`lattice_settings.py`)

Imports `tutorial/settings.py` and overrides: `DATABASES` (SQLite placeholder; `make_app`
points the connection at its `db_path`), `CACHES` (LocMem), `MAILERS` (locmem; Django
6.1's replacement for `EMAIL_BACKEND`), `SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS`
(`testserver`), and `PASSWORD_HASHERS = [MD5]` (DRF Basic auth hashes on every request;
PBKDF2 at ~1M iterations would dominate probe time; hashes still come from
`make_password`). Nothing reads the environment or a `.env` file.

## Upstream behaviour kept as is

- Superusers get no exemption from `IsOwnerOrReadOnly` (they cannot edit others' snippets).
- `UserViewSet` lists every user, inactive ones included, to anonymous callers.
- `GET /users/` paginates an unordered queryset (DRF's `UnorderedObjectListWarning`).
- The `language` / `style` choices come from the installed pygments; another pygments
  version changes the choice lists (and `makemigrations --check` would report it).

## Seed data (`seed.py`)

`admin` (superuser), `alice`, `bob`, `carol` (inactive). Snippets 1-2 owned by `alice`,
3-4 by `bob`, 5 by `admin`. `ROLES = {admin, user}`, `REFUSED = {carol}`.

## Fidelity (`fidelity.py`)

15 scenarios, written from the tutorial text (sample requests in parts 4-6) and recorded
against the running app on the seed above.
