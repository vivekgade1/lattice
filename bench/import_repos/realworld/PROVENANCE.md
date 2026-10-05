# Vendored flask-realworld-example-app

Phase 2 **held-out reserve** app (Flask + Flask-SQLAlchemy, flask-jwt-extended,
flask-apispec / webargs / marshmallow, Flask-Bcrypt; plan
`docs/superpowers/plans/2026-10-05-phase2-python-import.md`, §6 and Wave 4). Vendored after
the importer was frozen at `cad36e5`, by an agent that had not seen the importer's output
for any app.

- **Upstream:** https://github.com/gothinkster/flask-realworld-example-app (archived)
- **Commit:** `4b95fb2227dfeb5dd1a45d89b2bf48630b93fd28` (2019-09-01)
- **Paths vendored:** `conduit/` (all of it)
- **License:** MIT, see `LICENSE` (copied unchanged from the repository root; copyright
  2017 Mohamed Aziz Knani).
- **Date vendored:** 2026-10-05.

**Not vendored:** `autoapp.py` (dev entry point), `tests/` (read to write `fidelity.py`),
`README.rst`, `image.png`, `Pipfile`, `Pipfile.lock`, `requirements*.txt`, `setup.cfg`,
`Procfile`, `Vagrantfile`, `.circleci/`, `.travis.yml`, `.idea/`. Upstream has no `.env` or
`.env.example` file.

**Libraries.** Upstream's `Pipfile.lock` pins Flask 1.0.2, Flask-SQLAlchemy 2.3.2,
SQLAlchemy 1.2.9, flask-jwt-extended 3.10.0, PyJWT 1.6.4, marshmallow 2.15.3, webargs 3.0.2,
flask-apispec 0.7.0, unicode-slugify 0.1.3. This project runs it on Flask 3.1.3,
Flask-SQLAlchemy 3.1.1, SQLAlchemy 2.0.54, flask-jwt-extended 4.7.4, PyJWT 2.15.0,
marshmallow 3.26.2, webargs 8.7.1, flask-apispec 0.11.4, Flask-Bcrypt 1.0.1,
Flask-Caching 2.5.1, Flask-Migrate 4.1.0, Flask-Cors 6.0.5, unicode-slugify 0.1.5.
`flask-apispec`, `flask-bcrypt`, `flask-caching`, `flask-migrate` and `unicode-slugify` were
added to the `phase2_apps` group of `pyproject.toml` / `uv.lock` for this app (no existing
pin changed).

## Layout

| Path | Origin |
|---|---|
| `conduit/**/*.py` | upstream (mods below) |
| `conduit/_lattice_compat.py` | Lattice-owned (flask-jwt-extended 3.x names on 4.x) |
| `__init__.py` (`make_app`, `dispose`, `LatticeConfig`), `seed.py`, `fidelity.py`, `PROVENANCE.md` | Lattice-owned |
| `LICENSE` | upstream |

## Routes (19 method x path)

| Method | Path | Guard | Notes |
|---|---|---|---|
| POST | `/api/users` | none | register: `{"user": {username, email, password}}`; answers the user with a token (200); a taken username or email is 422 "User already registered" |
| POST | `/api/users/login` | `jwt_optional` | `{"user": {email, password}}`; a wrong email or password is 404 "User not found" |
| GET, PUT | `/api/user` | `jwt_required` | the caller only |
| GET | `/api/profiles/<username>` | `jwt_optional` | `following` relative to the caller |
| POST, DELETE | `/api/profiles/<username>/follow` | `jwt_required` | |
| GET | `/api/articles` | `jwt_optional` | `?tag=`, `?author=`, `?favorited=`, `?limit=` (20), `?offset=` |
| POST | `/api/articles` | `jwt_required` | author is the caller's profile; slug from the title |
| GET | `/api/articles/feed` | `jwt_required` | articles by profiles the caller follows |
| GET | `/api/articles/<slug>` | `jwt_optional` | |
| PUT, DELETE | `/api/articles/<slug>` | `jwt_required` | looked up by slug **and** `author_id = caller's profile` |
| POST, DELETE | `/api/articles/<slug>/favorite` | `jwt_required` | any article |
| GET | `/api/articles/<slug>/comments` | none | |
| POST | `/api/articles/<slug>/comments` | `jwt_required` | author is the caller's profile |
| DELETE | `/api/articles/<slug>/comments/<cid>` | `jwt_required` | comment looked up by id **and** author = caller's profile |
| GET | `/api/tags` | none | |

Framework route (excluded): `/static/<path:filename>`.

**Auth.** `Authorization: Token <JWT>` (`JWT_HEADER_TYPE = 'Token'`), access tokens from
flask-jwt-extended whose identity is `users.id`. No header is 401 "Missing Authorization
Header"; a malformed token 422; another scheme (`Bearer`) 401. There are no roles, no admin
and no active flag: every registered user is the same kind of user.

**Ownership.** `article.author_id` and `comment.author_id` reference `userprofile.id` (one
profile per user, created at registration), not `users.id`. A non-author's update of an
article answers 404 "Article not found". Non-owners' deletes are upstream 500s (below).

## Local modifications

Every vendored `.py` file starts with `# ruff: noqa`. `LATTICE MOD` marks each changed or
added line (with the original in the comment). 40 lines: 19 import paths, 20
compatibility lines and 1 comment line that marks the line after it. No model, schema field, guard or route logic was changed.

**Import paths (19 lines):** `conduit.` -> `bench.import_repos.realworld.conduit.`

| File | Lines |
|---|---|
| `conduit/app.py` | 5, 7, 8, 9 |
| `conduit/utils.py` | 4 |
| `conduit/extensions.py` | 48 |
| `conduit/articles/models.py` | 9, 11 |
| `conduit/articles/serializers.py` | 6 |
| `conduit/articles/views.py` | 11, 12 |
| `conduit/user/models.py` | 6, 7 |
| `conduit/user/views.py` | 10, 11, 12 |
| `conduit/profile/models.py` | 4 |
| `conduit/profile/views.py` | 8, 9 |

**Compatibility with current libraries (20 lines plus 1 marker comment):**

| File | Lines | Change | Reason |
|---|---|---|---|
| `conduit/extensions.py` | 10, 11 | `from flask_sqlalchemy import SQLAlchemy, Model` -> `SQLAlchemy` plus `from flask_sqlalchemy.model import Model` | Flask-SQLAlchemy 3 no longer exports `Model` at the top level |
| `conduit/extensions.py` | 51 | `jwt.user_loader_callback_loader(jwt_identity)` -> `jwt.user_lookup_loader(lambda _jwt_header, jwt_data: jwt_identity(jwt_data['sub']))` | flask-jwt-extended 4 renamed the hook and passes `(header, claims)`; the identity is the `sub` claim, as before |
| `conduit/user/models.py` | 15 | `db.Binary(128)` -> `db.LargeBinary(128)` | SQLAlchemy 1.4/2 removed the `Binary` alias of `LargeBinary` |
| `conduit/app.py` | 41-44 | the three `cors.init_app(<blueprint>)` calls run only while the `user` blueprint has never been registered (`if not user.views.blueprint._got_registered_once:`) | Flask >= 2.3 refuses to add an `after_request` hook to a blueprint that is already registered, so a second `create_app()` in one process (upstream's own test fixture does one per test) failed. The CORS hooks live on the module-level blueprints, so adding them once per process gives every app the same hooks |
| `conduit/articles/models.py` 6, `conduit/profile/models.py` 2, `conduit/articles/views.py` 8, `conduit/profile/views.py` 6, `conduit/user/views.py` 6-7 | 6 lines | `jwt_required`, `jwt_optional` and `current_user` imported from `conduit/_lattice_compat.py` instead of `flask_jwt_extended` (`create_access_token` still from `flask_jwt_extended`) | flask-jwt-extended 4 made `jwt_required` a factory (`@jwt_required()`), removed `jwt_optional` (now `@jwt_required(optional=True)`) and raises `RuntimeError` for `current_user` outside a JWT-decorated view, where 3.x answered `None`. Upstream relies on `None` when `GET /api/articles/<slug>/comments` (undecorated) serialises `UserProfile.following`. `_lattice_compat.py` gives the 3.x behaviour, nothing more |
| `conduit/articles/views.py` | 26, 117 | `use_kwargs({...})` -> `use_kwargs(..., location='query')` on `GET /api/articles` and `GET /api/articles/feed` | webargs 3 looked these filters up in the query string by default; webargs 8 / flask-apispec 0.11 read only the JSON body unless told otherwise, so `?author=` etc. were ignored |
| `conduit/articles/views.py` | 120 (comment), 121 | `Article.query.join(current_user.profile.follows)` -> `...join(current_user.profile.follows.subquery())` | SQLAlchemy 1.x turned a `Query` join target into a subquery implicitly; 2.0 raises `InvalidRequestError` (the feed answered 500 for everyone) |
| `conduit/user/serializers.py` 36, `conduit/articles/serializers.py` 37, 73 | 3 added lines | `unknown = 'exclude'` in `Meta` of `UserSchema`, `ArticleSchema` (inherited by `ArticleSchemas`) and `CommentSchema` | marshmallow 2 ignored unknown fields; marshmallow 3 rejects them (422) unless `unknown = EXCLUDE` |

## Test configuration (`__init__.py`)

`make_app(db_path)` calls upstream's `create_app(config_object)` with a per-call subclass of
upstream's `Config` (`LatticeConfig`), then `db.create_all()` (upstream ships no migrations;
its tests create the tables the same way). Dispose is `db.engine.dispose()`.

`LatticeConfig` pins:
- `SQLALCHEMY_DATABASE_URI = sqlite:///<db_path>` (upstream: Postgres in `ProdConfig`, a
  SQLite file in the source tree in `DevConfig`);
- `SECRET_KEY` (upstream reads `CONDUIT_SECRET` from the environment), `DEBUG = False`,
  `TESTING = False`, `ENV = "prod"`;
- `BCRYPT_LOG_ROUNDS = 4`, upstream's own `TestConfig` value (speed only);
- **compatibility:** `CACHE_TYPE = "SimpleCache"` (Flask-Caching 2 dropped the `'simple'`
  alias; no route uses the cache) and `JWT_VERIFY_SUB = False` (upstream's JWT identity is
  the integer user id; PyJWT >= 2.10 rejects a non-string `sub` on decode unless this
  flask-jwt-extended 4.7 option is off; PyJWT 1.6 did not check it).

**`.env`.** Upstream has no `.env` loading of its own (no python-dotenv, no
`load_dotenv`); it reads `CONDUIT_SECRET` and `DATABASE_URL` from `os.environ` at import,
and `LatticeConfig` overrides both values, so the host environment changes nothing.
`flask run` would load a `.env` through Flask's CLI, but the CLI is never used here.
Upstream sends no mail and calls no outside service, so nothing needed stubbing.

## Upstream behaviour kept as is (defects included)

- `DELETE /api/articles/<slug>` by someone other than the author, or for an unknown slug,
  answers **500** (`None.delete()`); the article survives.
- `DELETE /api/articles/<slug>/comments/<cid>` by someone other than the comment's author,
  or for an unknown id, answers **500**; the comment survives.
- `POST /api/articles` with a title whose slug exists answers **500** (`IntegrityError` on
  `article.slug`); missing `title`/`body`/`description` answers 500 (`TypeError`), as does
  register without `password` and any body without its envelope (`KeyError`).
- `GET /api/articles?favorited=<username>` filters on an unjoined `users` table: it lists
  every favorited article whenever that username exists.
- `Article.favorited` and `Article.is_favourite` query all articles, not this one:
  `favorited` is true on every article once the caller has favorited exactly one, and
  `favourite()` adds nothing once the caller has favorited any article (a user can hold
  one favorite at a time).
- Login with a wrong password answers 404 "User not found" (not 401/422).
- Register answers 200, not 201; `PUT /api/user` answers `"token": ""`.
- `followers_assoc` declares its foreign keys against `userprofile.user_id` while the
  relationship stores `userprofile.id` (equal in the seed; SQLite does not enforce the
  key).

## Seed data (`seed.py`)

`alice` (id 1), `bob` (id 2), `carol` (id 3), each with a `userprofile` (ids 1-3); passwords
hashed by `User.set_password` (Flask-Bcrypt). Articles: `how-to-train-your-dragon` (1) and
`alice-in-wonderland` (2) by alice, `bob-builds-things` (3) by bob; tags `dragons`,
`training`, `stories`. Comment 1 by bob on article 1, comment 2 by alice on article 3. alice
follows bob; bob has favorited article 1. `ROLES = {user: alice}`, `REFUSED = {}` (no
active flag), `AUTH_PROBE = GET /api/user`.

## Fidelity (`fidelity.py`)

22 scenarios, written from the upstream tests (`tests/test_authentication.py`,
`test_profile.py`, `test_articles.py`), the README and the RealWorld API spec, then
recorded against the running app on the seed above.
