# Lattice API Profile — design and contracts

> Status: APPROVED (design reviewed in chat 2026-09-27; user asked to proceed end-to-end with agent fan-out) · Builds on `2026-09-26-lattice-design.md` (language), `2026-09-26-lattice-step2-design.md` (bench methodology).

## 1. Goal

Extend Lattice from tabular pipelines to **backend CRUD APIs with business rules**, where four properties hold **by construction** (checked before anything runs):

1. **Auth & ownership** — every endpoint declares who may call it; rows of owned tables never reach a response or a write without an ownership filter (no IDOR); private columns never leave the server.
2. **Input validation** — every parameter is typed and constrained; invalid input always yields 422 before any logic runs; fields written to the database must declare bounds.
3. **Exhaustive errors** — every failure path maps to a declared typed error with a fixed status code.
4. **Query efficiency** — no per-row queries (no loops ⇒ no N+1); every list response is paginated with a bounded limit; each read pipeline is one SQL query.

Then measure, against LLM-written FastAPI code with the same information and repair budget, whether Lattice reduces **silent** API bugs (happy path passes, a trap test fails), and at what cost.

**Phase A** (this spec, built now): language + checker + Lattice runtime (approach 1) + benchmark.
**Phase B** (after A): compile the same checked plan to a FastAPI `app.py`; trusted only after differential testing against the Phase A runtime (§9).

## 2. Language additions (API profile)

A program may contain constants, `@step`s (unchanged) and `@endpoint` functions. No top-level table statements in API programs (L840).

```python
@endpoint("POST", "/orders/{order_id}/cancel", auth=Customer)
def cancel(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound | Conflict:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    order = one(where(mine, col("id") == order_id), else_=NotFound("order"))
    ok = require(order, col("status") == "pending", else_=Conflict("order is not pending"))
    done = update(ok, status="cancelled")
    return done
```

(`one(where(...))` nests a call — P3 nesting rules apply inside endpoints exactly as elsewhere, so the canonical form is `found = where(mine, ...)` then `order = one(found, else_=...)`.)

### 2.1 Endpoint declaration
- `@endpoint(METHOD, PATH, auth=ROLE)`; METHOD ∈ {"GET","POST","PUT","PATCH","DELETE"}; PATH literal with `{name}` segments; `auth` ∈ declared roles (`Public`, `Customer`, `Admin` in the benchmark profile; role names are ambient). Missing/unknown auth → **L801**.
- Parameters: every path segment must be a parameter of the same name (**L804**). Other parameters come from the query string for GET/DELETE and from the JSON body for POST/PUT/PATCH. A parameter may have a literal default (`limit: Int(ge=1, le=100) = 20`); no other defaults.
- `user: CurrentUser` — the authenticated caller as a one-row table with the Users columns; required when `auth` is not `Public` (**L801**), forbidden when it is.
- Parameter types: `Int(ge=, le=)`, `Float(ge=, le=)`, `Str(min_len=, max_len=)`, `OneOf("a", "b", ...)` (a Str restricted to literals), `Bool`, `Date`; bare `Int`/`Float`/`Str` allowed (unbounded). Constraints are literal.
- Return annotation: a success kind followed by the declared errors: `One` (JSON object), `Page` (JSON `{"items": [...], "limit": n, "offset": n}`), `Created` (201 + object), `NoContent` (204). Errors: `NotFound` 404, `Forbidden` 403, `Conflict` 409, `BadRequest` 400. `Unauthorized` 401 (bad/missing token) and 422 (validation) are automatic and never declared.

### 2.2 Operations inside endpoints (in addition to existing table ops and expressions)
| Op | Meaning | Result |
|---|---|---|
| `fetch(T)` | set-based read of table T (nothing is loaded until the pipeline runs) | `TableT` with T's keys, nullable, owner, private |
| `owned(t, by=user)` | keep rows whose owner column equals the caller's id; callers with role `Admin` see all rows | same table, marked owner-checked |
| `one(t, else_=Err(...))` | exactly one row; zero rows → that error; more than one → runtime 500 (R310) | one-row table |
| `require(t, cond, else_=Err(...))` | if any row of t fails `cond`, respond with the error | t |
| `page(t, limit=, offset=)` | `limit`: literal ≤ 100 or a param with `le ≤ 100`; `offset`: literal or `Int(ge=0)` param (default 0) | page |
| `insert(T, owner=user, col=expr, ...)` | insert one row; `owner=user` required for owned tables; expressions may use params, constants, literals | one-row table of the inserted row |
| `update(t, col=expr, ...)` | update the rows of t (by key); returns updated rows | t with new values |
| `delete(t)` | delete the rows of t; returns the deleted rows | t |
| `return name` | exactly one, last statement | response |

Expressions inside endpoints may reference endpoint parameters and constants as scalars (like step scalar params). The API profile supports this expression subset in SQL: literals, params, `col`, arithmetic, comparisons, `& | ~`, `is_null`, `coalesce`, `when`, `fn.lower/upper/strip/len/contains/starts_with/year/month/day`, `agg.sum/count/count_distinct/min/max/mean`. Anything else inside an endpoint → **L841** "not available in API endpoints".

### 2.3 Checker rules
| Code | Rule |
|---|---|
| L801 | endpoint lacks `auth`, unknown role, `user: CurrentUser` missing for non-Public / present for Public |
| L802 | rows derived from an owned table reach a `return`, `update`, or `delete` without passing through `owned(..., by=user)` (unless `auth=Admin`); `insert` into an owned table without `owner=user` |
| L803 | a private column is present in the returned table |
| L804 | path segment without a same-named parameter, or duplicate endpoint (method, path) |
| L811 | a parameter written via `insert`/`update` (directly or in an expression) has an unbounded numeric type or unbounded `Str` |
| L821 | an error raised by `one`/`require` is not declared in the return annotation |
| L822 | declared error never raised (warning) |
| L831 | the returned table can have many rows but is not the result of `page(...)`, or `page` limit exceeds 100 |
| L832 | success kind mismatch: `One`/`Created` need a one-row table (`one`, `insert`, or at-most-one-row), `Page` needs `page`, `NoContent` needs `delete`/`update` result not returned... (i.e. `NoContent` endpoints `return` a write result, which is discarded) |
| L833 | `Created` used for a non-POST endpoint, or POST that inserts but declares `One` (warning) |
| L840 | top-level table statements in an API program |
| L841 | construct not supported inside endpoints |

All existing rules (names, canonical form, types, L420 fan-out, L421 nulls, etc.) apply inside endpoint bodies.

## 3. Profile additions (contracts)

```python
# lattice/typesys.py — Schema gains (both compare=False):
owner: str | None = None          # column holding the owning user's id
private: frozenset[str] = frozenset()
# Schema.from_pairs(pairs, *, keys=(), nullable=(), owner=None, private=())
# lattice/profile.py — new
@dataclass(frozen=True)
class ApiProfile:
    tables: Mapping[str, Schema]          # schema name -> schema (DB tables)
    table_names: Mapping[str, str]        # schema name -> SQL table name
    roles: frozenset[str]                 # e.g. {"Public", "Customer", "Admin"}
    admin_role: str = "Admin"
    users_table: str = "Users"            # schema name of the users table (has id, role)
# Profile gains: api: ApiProfile | None = None
```

## 4. IR and runtime contracts (Phase A)

```python
# lattice/api/ir.py
@dataclass(frozen=True)
class ParamSpec: name: str; source: Literal["path","query","body","user"]; scalar: Scalar | None
    ge: float | None = None; le: float | None = None; min_len: int | None = None; max_len: int | None = None
    one_of: tuple[str, ...] | None = None; default: object = MISSING
@dataclass(frozen=True)
class EndpointPlan: name: str; method: str; path: str; auth: str; params: tuple[ParamSpec, ...]
    success: Literal["One","Page","Created","NoContent"]; errors: tuple[str, ...]
    steps: tuple[ApiStep, ...]   # ordered; see below
    returns: str                 # id of the step whose result is the response
    span: Span
ApiStep = ReadStep | CheckStep | WriteStep
@dataclass(frozen=True) class ReadStep: id: str; sql: SqlQuery; span: Span     # one SELECT per read pipeline (fetch → table ops → owned → page)
@dataclass(frozen=True) class CheckStep: id: str; kind: Literal["one","require"]; input: str; error: ErrorSpec; cond: SqlExpr | None; span: Span
@dataclass(frozen=True) class WriteStep: id: str; kind: Literal["insert","update","delete"]; table: str; input: str | None; values: tuple[tuple[str, SqlExpr], ...]; span: Span
@dataclass(frozen=True) class ErrorSpec: kind: str; status: int; detail: str
def lower_api(program: CheckedProgram) -> tuple[EndpointPlan, ...]
# lattice/api/sql.py — compiles table-op pipelines + expression subset to parameterized SQLite SQL
@dataclass(frozen=True) class SqlQuery: text: str; params: tuple[str, ...]  # named request params / constants referenced
# lattice/api/runtime.py
def build_app(plans: Sequence[EndpointPlan], db_path: Path) -> starlette.applications.Starlette
def serve_source(source: str, profile: Profile, db_path: Path) -> tuple[CheckResult, Starlette | None]
```
Runtime semantics: auth from `Authorization: Bearer <token>` → `SELECT id, role FROM users WHERE token = ?` (401 if absent/unknown; 403 if role not permitted — `Admin` may call every endpoint, `Customer` endpoints accept Customer and Admin, `Public` accepts anyone); validation → 422 `{"error": "validation", "detail": [...]}`; steps run in order inside one transaction (commit on success, rollback on any error response); read steps execute their single SQL query; check steps raise their `ErrorSpec` → JSON `{"error": "<kind snake>", "detail": "..."}` with the status; writes executed with parameters; response serialization: One/Created → object of the returned row, Page → `{"items": [...], "limit", "offset"}`, NoContent → 204 empty. Dates as ISO strings. Any unexpected exception → 500 `{"error": "internal"}` (loud).

## 5. Benchmark contracts

### 5.1 Data (`bench/api_datasets.py`)
Deterministic SQLite commerce DB: `users(id, email, name, role, token, password_hash)` [private: password_hash, token], `customers`? — merged into users (role Customer), `products(id, name, category, price, stock, active)`, `orders(id, customer_id→users, status ∈ pending|paid|shipped|cancelled, created_at, total)` [owner: customer_id], `order_items(id, order_id, product_id, qty, unit_price)` [owner via order — v1: owned tables are those with a direct owner column; order_items rows are reached through orders], `reviews(id, product_id, customer_id, rating 1–5, body)` [owner: customer_id], `addresses(id, customer_id, line1, city, zip)` [owner: customer_id]. ~40 users (3 admins), 30 products, 300 orders, 900 items. `def build_db(path: Path, seed: int = 21) -> None`, `def api_profile() -> Profile`, `TOKENS: Mapping[str, str]` (fixture user handle → token, e.g. "alice", "bob", "admin").

### 5.2 Tasks (`bench/api_tasks.py`)
```python
@dataclass(frozen=True)
class HttpCase: name: str; kind: Literal["happy","trap"]; family: str
    method: str; path: str; token: str | None; query: Mapping[str, object] = {}; json: Mapping[str, object] | None = None
    expect_status: int; expect_json: Mapping[str, object] | None = None   # subset match; for Page, items compared as a list (order significant only if case.ordered)
    ordered: bool = False; max_queries: int | None = None; after_sql: str | None = None; after_expect: list | None = None  # DB state check after the request
@dataclass(frozen=True)
class ApiTask: id: str; family: str; spec: str   # natural-language endpoint spec(s)
    cases: tuple[HttpCase, ...]
API_TASKS: tuple[ApiTask, ...]
```
~40 tasks: families `ownership` (IDOR), `validation`, `errors`, `efficiency` (N+1 / unbounded lists), plus `control` (plain CRUD with no special trap). Each case runs against a fresh copy of the DB. Every trap task ships `tests/fixtures/api/<id>/lattice.py`, `fastapi_correct.py`, `fastapi_naive.py` (naive passes all happy cases, fails ≥1 trap case).

### 5.3 FastAPI baseline scaffolding (`bench/api_kit/platform_kit.py`, copied into the workdir)
```python
app = FastAPI()                           # the script adds routes to this app
def get_db() -> sqlite3.Connection        # dependency; the harness instruments it for query counting
def current_user(authorization: str = Header(None), db = Depends(get_db)) -> dict  # 401 if missing/unknown; returns {"id", "role", "email", "name"}
def require_role(*roles: str) -> Callable  # dependency factory; 403 if role not allowed (Admin always allowed)
```
It authenticates only; ownership, validation beyond FastAPI types, error mapping and pagination are the script's job (as with Lattice's program).

### 5.4 Harness (`bench/api_runner.py`)
- `run_cases_fastapi(script: str, workdir: Path, task: ApiTask) -> CaseResults` — runs inside the sandbox subprocess: copies DB per case, imports the script's `app`, issues each case through Starlette `TestClient`, counts SQL statements via `sqlite3.Connection.set_trace_callback`, returns JSON results.
- `run_cases_lattice(source: str, workdir: Path, task: ApiTask) -> CaseResults` — in-process via `serve_source` + TestClient, same counting.
- `@dataclass CaseResult: name, kind, family, passed: bool, status: int, queries: int, latency_ms: float, reason: str`.
- Classification: app didn't start / checker rejected / any happy case failed → **loud**; all happy pass and a trap fails → **silent**; all pass → **correct**.
- Arms A (FastAPI one-shot), P (FastAPI + repair on start-up / crash / all-happy-fail with the error text only — hidden cases never revealed), B (Lattice + repair on checker/runtime errors), C (Lattice first-shot). Same TaskOutcome + metrics as bench2, plus `mean_queries`, `max_queries`, `mean_latency_ms` per task.
- CLI: `python -m bench.cli run --suite api --arms A,P,B,C ...` and the step-2 style report (families as rows) with a gate: silent(B) ≤ 0.5·silent(P) on trap families; acc(B) ≥ acc(P) − 0.02; efficiency: mean queries/request on efficiency-family list endpoints B ≤ P.

### 5.5 Prompts
Both arms get the same endpoint spec, DB schema (tables, columns, types, keys, owner column, private columns, roles and the auth scheme), and 3 worked examples solving the same 3 example problems (not overlapping any task). Pitfall advice exists in both (for the hints condition) and is removable with `--no-hints` exactly as in bench2. Prompt budgets within ±30%.

## 6. Testing
Unit tests per checker rule (positive + negative), SQL compiler tests (each op/expression against SQLite results vs. a polars reference), runtime tests (auth, 422, errors, transactions rollback, pagination, query counting = one query per read pipeline), and the three-solution fairness proof per trap task. Offline e2e over all tasks and arms with scripted responses.

## 7. Error handling
Checker never raises; runtime maps every failure to a status code; unexpected exceptions → 500 (counted loud). Harness isolates per task (harness_error).

## 8. Risks
- SQL compilation scope: restricted expression subset (L841) keeps it tractable.
- Friction (step-0 lesson): measure first-shot pass per rule; hints in diagnostics give the exact fix.
- Fairness: identical scaffolding capability (auth only) and information; naive-solution proof per trap.

## 9. Phase B (after A)
`lattice/api/codegen.py`: `generate_fastapi(plans) -> str` producing an `app.py` using the same `platform_kit`. Differential test: for every task's reference Lattice program, run all HTTP cases through the Phase A runtime and through the generated app; responses (status + JSON) and query counts must match exactly. Only then can the benchmark optionally run Lattice arms on the generated app (arm `B/gen`).
