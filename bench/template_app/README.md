# Vendored FastAPI full-stack template backend

Proof target for Lattice (spec: `docs/superpowers/specs/2026-09-27-lattice-realapps-design.md` §4).

- **Upstream:** https://github.com/fastapi/full-stack-fastapi-template
- **Commit:** `cb740b656d7a0a6c5e12c7bf8e50343ec94ee9c7`
- **Path vendored:** `backend/app` → `bench/template_app/app`
- **License:** MIT, see `LICENSE` (copied unchanged from the repository root).

## Layout

| Path | Origin |
|---|---|
| `app/models.py`, `app/crud.py`, `app/core/security.py`, `app/api/main.py`, `app/api/routes/{items,login,users,utils,private}.py`, `app/core/db.py`, empty `__init__.py` files | upstream, imports rewritten only |
| `app/core/config.py`, `app/api/deps.py`, `app/utils.py`, `app/main.py` | upstream with the modifications below |
| `__init__.py` (`make_app`, `sqlite_engine`), `seed.py`, `README.md` | Lattice-owned |
| `LICENSE` | upstream |

Not vendored: `alembic/`, `alembic.ini`, `email-templates/`, `initial_data.py`,
`backend_pre_start.py`, `tests_pre_start.py`, the backend tests, the frontend, sentry.

## Usage

```python
from bench.template_app import make_app            # make_app(db_path) -> FastAPI
from bench.template_app.seed import build_template_db, TEMPLATE_USERS
app = make_app(build_template_db("seed.db"))
```

`make_app` opens a SQLite engine on `db_path` (`check_same_thread=False` for
TestClient's worker threads), runs `SQLModel.metadata.create_all`, builds the upstream
app via `create_app()` and overrides the template's `deps.get_db` dependency with a
session on that engine. The engine is exposed as `app.state.engine`.

## Local modifications

Every vendored `.py` file starts with a `# ruff: noqa` line so the repo's lint rules
(100-column lines, import sorting, pyupgrade) are not applied to upstream code; that
header is the only change to otherwise-unmodified files besides imports. Changed lines
inside files are marked `LATTICE MOD`.

1. **Imports (all files):** `from app.…` / `from app import …` rewritten to
   `from bench.template_app.app.…` / `from bench.template_app.app import …` so the
   package imports as `bench.template_app.app`.
2. **`core/config.py` (configuration only):**
   - `env_file="../.env"` → `env_file=None`, and `env_prefix="TEMPLATE_APP_"`, so the
     settings never read a stray `.env` relative to the cwd or unrelated `SECRET_KEY` /
     `DATABASE_URL` env vars; override with `TEMPLATE_APP_*`.
   - `DATABASE_URL: PostgresDsn` (required) → `DATABASE_URL: str = "sqlite://"`; the
     psycopg-driver validator is kept; the "changethis" password check iterates hosts
     only for `postgresql…` URLs.
   - Defaults for previously required fields: `SECRET_KEY =
     "lattice-template-app-test-secret-key-000000000000"` (deterministic test key),
     `PROJECT_NAME = "Full Stack FastAPI Project"`, `FIRST_SUPERUSER =
     "admin@example.com"`, `FIRST_SUPERUSER_PASSWORD = "adminpass123"`.
   - With no `SMTP_HOST`, `settings.emails_enabled` is False (upstream logic, unchanged).
3. **`api/deps.py`:**
   - `except InvalidTokenError, ValidationError:` (PEP 758 form, Python 3.14-only) →
     `except (InvalidTokenError, ValidationError):` — same semantics, parses on 3.12+.
   - `session.get(User, token_data.sub)` → `session.get(User, uuid.UUID(token_data.sub)
     if token_data.sub else None)`. SQLAlchemy's non-native `Uuid` type (SQLite) rejects
     a `str` key, where psycopg/Postgres upstream accepts it. Tokens minted by the app
     always carry a UUID `sub`.
4. **`utils.py` (no email sending):** the `emails` and `jinja2` imports are removed;
   `render_email_template` returns a deterministic plain-text string instead of rendering
   `email-templates/*.html`; `send_email` appends `{email_to, subject, html_content}` to
   the module-level `OUTBOX` list instead of asserting `emails_enabled` and sending over
   SMTP. Token helpers are unchanged.
5. **`main.py`:** `sentry_sdk` import/init removed; the app construction is wrapped in
   `create_app()` (module-level `app = create_app()` kept) so each test gets a fresh
   `FastAPI`; the `app.frontend("/", directory=FRONTEND_DIR)` mount is dropped (no
   frontend is vendored).

No route, CRUD, model or security logic was changed. The `private` router is still only
included when `FASTAPI_ENV == "development"` (not the default here).

## SQLite behaviour worth knowing

- `DateTime(timezone=True)` has no zone storage on SQLite: `created_at` is stored as
  `YYYY-MM-DD HH:MM:SS.ffffff` and returned naive (no `+00:00`) after a refresh.
- `uuid.UUID` columns are stored as 32-char hex (`CHAR(32)`), not the dashed form.
- Booleans are stored as `0`/`1`.
