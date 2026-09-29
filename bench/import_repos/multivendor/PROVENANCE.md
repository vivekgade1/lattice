# Vendored Multi-Vendor Inventory & Order Booking System

Held-out repo #3, vendored for option 1 (`docs/option1-handoff.md`): a harder ticket
family (aggregations with traps; ownership inherited through parent tables) on an app
with a real role column and multi-hop ownership. Different from
`fastapi/full-stack-fastapi-template` (`bench/template_app/`), `bench/import_repos/taskflow`
and `bench/import_repos/justnotes`. Never read by anything under `lattice/importer/`.
Arm L must start from the importer's output for this app; no Lattice is written for it
by hand.

- **Upstream:** https://github.com/CS-Nithin/multi-vendor-inventory-booking-system
- **Commit:** `54fbf34b9e6cb786cb1529c7cb0cc6f3c8708239` (2026-08-09)
- **Path vendored:** `app/` → `bench/import_repos/multivendor/app` (minus `app/tests/`)
- **License:** MIT, see `LICENSE` (copied unchanged from the repository root; copyright
  Nithin CS).
- **Date vendored:** 2026-09-29.

Not vendored: `alembic.ini` + `alembic/` (tables are created by `make_app()` instead),
`app/tests/` (upstream's concurrency test, Postgres-oriented), `requirements.txt` (a
full environment dump, not this app's dependencies; runtime deps come from this
project's `pyproject.toml`), `.env.example`, `README.md`, `ARCHITECTURE.md`,
`senior_architecture_guide.md`, and a stray committed Windows venv `activate.bat`.

## Layout

| Path | Origin |
|---|---|
| `app/routers/*`, `app/services/*`, `app/repositories/*`, `app/schemas/*`, `app/core/{database,dependencies,permissions}.py`, `app/models/__init__.py`, empty `__init__.py` files | upstream, imports rewritten only |
| `app/main.py`, `app/core/{config,security}.py`, `app/models/{user,vendor,product,order,order_item}.py` | upstream with the modifications below |
| `app/_lattice_registry.py` | Lattice-owned (added inside `app/`; see modification 6) |
| `__init__.py` (`make_app`, `sqlite_engine`), `seed.py`, `PROVENANCE.md` | Lattice-owned |
| `LICENSE` | upstream |

## Routes (13, plus `/docs`, `/openapi.json`, `/redoc`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/auth/register` | none | creates a `buyer`; 400 "Email already registered" on a duplicate |
| POST | `/auth/login` | none | OAuth2 form; 401 on bad credentials or an inactive account |
| GET | `/users/me` | bearer | |
| PATCH | `/users/me` | bearer | `full_name`, `email` |
| POST | `/vendors` | bearer | registers the caller as a vendor and sets their role to `vendor` |
| GET | `/vendors/me` | bearer | 404 without a vendor profile |
| PATCH | `/vendors/me` | bearer | |
| PATCH | `/vendors/{vendor_id}/verify` | admin (`VERIFY_VENDOR`) | |
| POST | `/products` | vendor (`MANAGE_OWN_PRODUCTS`) | product belongs to the caller's vendor profile |
| GET | `/products` | none | active products only; `category`, `min_price`, `max_price`, `skip`, `limit≤100` |
| GET | `/products/{product_id}` | none | inactive products are still readable by id |
| PATCH | `/products/{product_id}` | vendor | own products only (403 "You do not own this product") |
| POST | `/orders` | buyer (`PLACE_ORDER`) | decrements stock, records `price_at_purchase`, sets `total_amount` |
| GET | `/orders/me` | buyer or vendor (`VIEW_OWN_ORDERS`) | orders the caller *placed* |
| GET | `/orders/{order_id}` | bearer | admin: any order; others: own order, else **403** (not 404) |
| PATCH | `/orders/{order_id}/status` | vendor or admin (`UPDATE_ORDER_STATUS`) | state machine `pending→confirmed→shipped→delivered`, `pending/confirmed→cancelled`; 400 otherwise |
| POST | `/orders/flash-sale-purchase` | buyer | single item, `SELECT … FOR UPDATE` (a no-op on SQLite) |

**Roles and ownership:** `User.role ∈ {buyer, vendor, admin}` with a fixed permission
table (`app/core/permissions.py`). `Vendor.user_id → users.id` (unique);
`Product.vendor_id → vendors.id` (a product's owner is reached through its vendor);
`Order.user_id → users.id`; `OrderItem.order_id → orders.id`,
`OrderItem.product_id → products.id` (an order line belongs to the buyer through its
order, and to a vendor through its product).

## Upstream behaviour kept as is (not bugs we fix)

- `PATCH /orders/{id}/status` lets *any* vendor move *any* order, including orders with
  none of their products.
- `decrement_stock_optimistic` commits per line, so when a later line of a multi-line
  order fails for stock, the order row and the earlier lines' stock decrements are
  already committed. Tickets must not depend on this path.
- `GET /orders/{id}` answers 403 for another buyer's order (an existence leak), and
  inactive products stay readable by id.
- Stored times are naive UTC (`datetime.utcnow`).

## Local modifications

Every vendored `.py` file starts with a `# ruff: noqa` line (upstream code is not
reformatted or re-linted); `LATTICE MOD` marks a changed line inside an otherwise
unmodified file. No route, service, repository, model or permission *logic* was
changed.

1. **Imports (every file):** `from app.…` / `from app import …` rewritten to
   `from bench.import_repos.multivendor.app.…`, so the package imports under `bench`
   (upstream's top-level package is named `app`, like the other vendored apps').
2. **`app/main.py`:** the module-level `app = FastAPI(...)` and its `include_router`
   calls are wrapped in `create_app()` so each run gets a fresh instance.
3. **`app/core/config.py` + process env (`__init__.py`):** upstream builds
   `settings = Settings()` at import, requiring `DATABASE_URL` and `SECRET_KEY`, and
   reads `env_file=".env"` relative to the *current directory*. Run from this repo's
   root, that `.env` is the host project's (it holds unrelated keys, which
   `BaseSettings` rejects as extra inputs, and which the vendored app must never
   read). `env_file` is set to `None`, and `__init__.py` sets test-safe placeholders
   for `DATABASE_URL` (`sqlite://`) and `SECRET_KEY` via `os.environ.setdefault`
   before importing upstream code. The placeholder engine that `core/database.py`
   builds at import is never used: `make_app()` overrides `get_session`.
4. **`app/core/security.py` (password hashing library):** upstream hashes with
   `passlib.context.CryptContext(schemes=["bcrypt"])`. `passlib` is not a dependency
   here, and passlib 1.7.4 (its last release) is incompatible with `bcrypt>=4.1`
   (installed: 5.0, via `pwdlib[bcrypt]` in the `template` group): it reads the removed
   `bcrypt.__about__` and its wrap-bug self-test hashes a >72-byte secret, which bcrypt
   5 rejects. `hash_password`/`verify_password` call `bcrypt.hashpw`/`bcrypt.checkpw`
   directly — the same `$2b$` bcrypt hashes passlib would produce and accept.
5. **`app/models/{user,order}.py` (SQLModel version compatibility):** `created_at`
   typed plain `datetime` is retyped `pydantic.NaiveDatetime` — the same naive value.
   The installed `sqlmodel` (0.0.47) maps bare `datetime` on table models to a
   `UTCDateTime` column that refuses naive values at insert, which is exactly what
   upstream's `datetime.utcnow` produces. Same fix as TaskFlow's (its PROVENANCE,
   modification 2).
6. **`app/models/*.py` (multi-app process, not upstream's fault):** every
   `table=True` class inherits `TableBase` (from the Lattice-owned
   `app/_lattice_registry.py`) instead of `SQLModel`; names, types and defaults are
   unchanged. This app's `users` table name is TaskFlow's too, and every vendored app
   defines a class named `User`; SQLModel's shared default registry would collide.
   `make_app()`/`seed.py` create tables via
   `_lattice_registry.metadata_registry.metadata.create_all(engine)`.

## SQLite / library-version notes

- `with_for_update()` compiles to nothing on SQLite; the flash-sale route still works,
  without row locking.
- `python-jose` (JWT) is already in the `import_repos` group (added for JustNotes);
  `bcrypt`, `pydantic-settings` and `email-validator` are covered by the `template`
  group. No new dependency.
- Tokens use `datetime.utcnow()` for `exp`, which python-jose treats as UTC, so no
  process-TZ patch is needed (unlike JustNotes).

## Seed data (`seed.py`)

Eight users (admin; vendors Vicky and Victor with verified shops; Vera with an
unverified shop and no products; buyers Carol and Dave; Erin, inactive, with one
order; Frank with none), eight products in four categories (one inactive, two never
sold, one out of stock), and nine orders over January–March 2026 in every status,
several with lines from both vendors and some placed exactly on month boundaries. One
product's price changed after it was bought (`price_at_purchase` 10.00, now 12.00).
Written before any ticket, to be ticket-neutral.
