# Lattice sub-project 1 — Language extensions for real apps (design + contracts)

> Status: APPROVED in chat 2026-09-27. Part of the import → Lattice → emit roadmap: (1) this, (2) importer, (3) SQLModel emitter + edit loop, (4) backends (Postgres/MySQL → MongoDB → ClickHouse). Builds on `2026-09-27-lattice-api-design.md`.

## 1. Goal
Make Lattice able to express what a typical FastAPI + SQLModel service does — proven on the official full-stack FastAPI template's `items`, `users` and `login` routes — and make the database layer dialect-neutral with a backend capability model, so later backends plug in without weakening guarantees.

## 2. Language additions

| Addition | Surface | Semantics | Checker |
|---|---|---|---|
| `Uuid` scalar | param/column type `Uuid`; literal via `fn.to_uuid("…")` | stored as canonical lowercase text; path/query/body values must parse as UUID (422 otherwise); `insert` generates `uuid4` for a `Uuid` key column when not given | type rules; comparisons only `==`/`!=` |
| `DateTime` scalar | type `DateTime`; `fn.now()`; `fn.date(dt)`; `fn.to_datetime(s, format=…)` | ISO-8601 text with timezone `Z`; `fn.now()` is evaluated **once per request** (all uses equal); ordered comparisons | type rules |
| Page with total and app page shape | `page(t, limit=…, offset=…, total=True)`; `ApiProfile.page_shape: Mapping[str, str]` (keys `items`, `total`, `limit`, `offset` → JSON field names; a key mapped to `""` is omitted) | total = row count before limit/offset, computed in the **same statement** (window `COUNT(*) OVER ()` or equivalent); default shape `{"items":"items","limit":"limit","offset":"offset"}` | `total=True` requires the shape to include `total`; L831 unchanged |
| Declared response models | `ApiProfile.responses: Mapping[str, tuple[str, ...]]` (name → ordered field names); `@endpoint(..., response="ItemPublic")` | serialization emits exactly those fields (for Page: each item) | **L834** returned columns ≠ declared fields (missing or extra) |
| Partial update | param default `UNSET` (ambient name); `update(t, col=param, ...)` | a column whose value is exactly an `UNSET`-able param that was not sent is left unchanged; an explicit JSON `null` sets NULL only if the column is nullable (else 422) | `UNSET` only as a param default (L404 elsewhere); expressions over an `UNSET`-able param are L404 (use the param directly) |
| Faithful 403 ownership | predicate `owner_is(user)` usable in `require(t, owner_is(user), else_=Forbidden(...))` | true for rows whose owner (or owned-through parent owner) is the caller; Admin always true | L802 accepts `require(..., owner_is(user))` as an ownership check; **L823 warning** "reveals that other users' rows exist (403); owned() answers 404" |
| Typed native functions | `ApiProfile.natives: Mapping[str, NativeSig]`; call `native.<name>(args...)` in expressions | pure scalar functions implemented in Python, registered with the database connection (SQLite `create_function`) so reads stay one statement | arity/types per `NativeSig`; natives can't take tables; L841 unaffected for natives |
| Pluggable auth | `ApiProfile.auth: AuthScheme` — `TokenColumn(column="token")` (today's behaviour) or `NativeToken(native="decode_token")` (bearer token → user key via a native) | 401 on missing/invalid token; then load the user row by key | — |
| Stored spellings (added for §4) | `ApiProfile.uuid_storage`: `"canonical"` (default) \| `"hex32"`; `ApiProfile.datetime_storage`: `"iso_z"` (default) \| `"sql_space"` | only how the database keeps Uuid/DateTime values changes (32 hex digits; naive-UTC `"YYYY-MM-DD HH:MM:SS.ffffff"`), so a program runs on a database another stack wrote (SQLAlchemy on SQLite); parameters, responses and natives still see canonical values; honoured by the dialect (binds, `fn.to_uuid`/`fn.to_datetime`, generated keys, native results) and by generated apps (`UUID_STORAGE`/`DATETIME_STORAGE`) | profile validation (unknown spelling → `ValueError`) |
| Form-encoded body (added for §4) | `@endpoint(..., body="form")` (default `"json"`) | body parameters are `application/x-www-form-urlencoded` fields (e.g. an OAuth2 password login), converted like query text; any other content type has no fields (required ones → 422) | **L601** for a value other than `"json"`/`"form"` or `body="form"` on GET/DELETE |

## 3. Backend capability model and dialect interface

```python
# lattice/api/backend.py
@dataclass(frozen=True)
class Capabilities:
    transactions: bool          # multi-statement atomic writes with rollback
    returning: bool             # INSERT/UPDATE/DELETE ... RETURNING
    unique_constraints: bool    # uniqueness enforced by the store
    window_functions: bool      # COUNT(*) OVER () etc.
    json_params: bool           # binding a JSON array as a relation (json_each / equivalent)
    mutations_sync: bool        # UPDATE/DELETE visible immediately
    native_functions: bool      # user functions callable inside queries
class Dialect(Protocol):
    name: str
    capabilities: Capabilities
    def quote(self, ident: str) -> str: ...
    def bind(self, name: str) -> str: ...                     # placeholder text for a named bind
    def compile_read(self, pipeline) -> SqlQuery: ...          # existing CTE compiler, parameterized by dialect
    def compile_write(self, step) -> SqlQuery: ...
    def connect(self, target) -> Connection: ...               # opens a DB-API connection, registers natives
SQLITE: Dialect   # the current behaviour, factored out of lattice/api/sql.py
# ApiProfile gains: backend: str = "sqlite"
```
- `lattice/api/sql.py` becomes dialect-parameterized; only `SQLITE` is implemented in sub-project 1 (Postgres/MySQL/Mongo/ClickHouse come in sub-project 4), but no SQLite-specific text remains outside the dialect object.
- New checker pass `lattice/check/p7_backend.py`: rejects programs the profile's backend cannot run with the language's guarantees — **L870** (multi-write endpoint without `transactions`), **L871** (`total=True` without `window_functions`), **L872** (write whose result is returned without `returning` and without a re-read strategy), **L873** (natives on a backend without `native_functions`). For SQLite all capabilities are true.

## 4. Proof target — the FastAPI template
- `bench/template_app/`: the upstream template backend (`fastapi/full-stack-fastapi-template`, `backend/app`, MIT) vendored at a pinned commit with a SQLite database URL override, runnable in-process for tests.
- `bench/template_profile.py`: `template_profile()` — Lattice ApiProfile for its `User`/`Item` tables (Uuid keys, DateTime `created_at`, `Item.owner_id` owner, `User.hashed_password` private, `is_superuser` → Admin role mapping), page shape `{"items":"data","total":"count","limit":"","offset":""}`, response models `ItemPublic`, `ItemsPublic` items, `UserPublic` (plus `Token`, `Message`), natives `hash_password`, `verify_password`, `create_access_token`, `decode_token` (wrapping the template's own `core/security.py`), auth `NativeToken("decode_token")`, and the template's stored spellings `uuid_storage="hex32"`, `datetime_storage="sql_space"` so both sides run on byte-identical seeded databases. `template_program()` joins the fixtures into one program; `template_plans()` checks and lowers it.
- `tests/fixtures/template/*.lattice.py`: hand-written Lattice for items CRUD (read_items with page+total and 403-faithful read/update/delete), users (`/users/me`, `update_user_me` partial update, admin list) and login (`/login/access-token` via natives, `body="form"`). Every authenticated endpoint repeats the template's inactive-user check (`require(user, col("is_active"), else_=BadRequest("Inactive user"))`).
- **Acceptance:** a differential test runs a request suite (happy paths, other user's item, superuser, missing ids, bad UUIDs, partial updates, pagination incl. total, login success/failure) against the template app and against the Lattice programs on identical SQLite databases; responses (status + JSON, ignoring generated UUID/timestamp values via placeholders) and DB effects must match. Known intentional deviations (e.g. unbounded `limit` in the template) are listed explicitly in the test as policy findings, not silently tolerated.
- **Result** (`tests/template/test_template_differential.py`; summary in `docs/results/2026-09-26-bench-smoke.md` "Template proof"): the runtime and the generated FastAPI app answer all 53 suite cases (+7 extra: inactive user, token for a user that does not exist) identically; against the template, 44/53 suite cases match status, body and DB effects (8 exact, 36 up to three named representation rules: DateTime wire spelling, the `{"error", "detail"}` error envelope, the 422 envelope with the same `loc`s) and 9 are named deviations (7 policy, 2 language gaps: the 409 email-uniqueness check over an `UNSET` parameter, and email format validation).

## 5. Out of scope
Side-effecting natives (email), ORM relationship loading beyond joins, migrations, non-SQLite backends (sub-project 4), the importer (2), the SQLModel emitter (3).

## 6. Testing
Checker unit tests per rule; SQL-vs-polars differential for Uuid/DateTime/page-total; runtime tests; FastAPI codegen differential kept green (generated apps support all new features); the template differential (§4).
