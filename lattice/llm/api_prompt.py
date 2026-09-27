"""System prompts and task messages for the API benchmark (spec 2026-09-27 §5.5).

Two arms get the same information: the Lattice arm writes an API-profile
program (``@endpoint`` functions), the baseline arm writes a FastAPI + sqlite3
script on ``platform_kit``. Both prompts state the same response conventions,
work the SAME three example problems (none overlaps a benchmark task), and
carry pitfall "Advice" of equal substance that ``hints=False`` removes, exactly
as in the tabular prompts (:mod:`lattice.llm.prompt`). Budgets stay within
±30% of each other (tests/llm/test_api_prompt.py).

``lattice`` must never import ``bench`` at module import time, so the schema
description is imported lazily inside :func:`api_task_message`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from lattice.catalog import (
    API_AGG_FUNCS,
    API_ERRORS,
    API_FN_FUNCS,
    API_OP_SHAPES,
    PAGE_LIMIT_MAX,
)
from lattice.llm.prompt import _OUTPUT_FORMAT_SECTION, _render_fn_sig, _render_op_shape

if TYPE_CHECKING:
    from bench.api_model import ApiTask

ArmKind = Literal["baseline", "lattice"]

# ---------------------------------------------------------------------------
# Shared: response conventions and the three example problems
# ---------------------------------------------------------------------------

# snake_case error names used in JSON error bodies, per declarable error.
ERROR_BODY_NAMES: dict[str, str] = {
    "NotFound": "not_found",
    "Forbidden": "forbidden",
    "Conflict": "conflict",
    "BadRequest": "bad_request",
}


def _error_table() -> str:
    return ", ".join(
        f'`{kind}` {status} (`"{ERROR_BODY_NAMES[kind]}"`)' for kind, status in API_ERRORS.items()
    )


# The same three problems in both arms. They deliberately differ from every
# benchmark task (tests/llm/test_api_prompt.py checks the task suite once it exists).
EXAMPLE_PROBLEMS = (
    "Example 1. `GET /addresses` (Customer): the caller's addresses, ordered by id, "
    "paged with query parameters `limit` (1-50, default 20) and `offset` (>= 0, default 0).",
    "Example 2. `PATCH /products/{product_id}/stock` (Admin): body `stock` (0-10000) sets "
    "the product's stock; respond with the product's id, name and stock; 404 if there is "
    "no such product.",
    "Example 3. `POST /products/{product_id}/reviews` (Customer): body `rating` (1-5) and "
    "`body` (1-500 characters); `product_id` is 1-1000000. 404 if the product does not exist, "
    "403 if the caller never ordered it, 409 if the caller already reviewed it; otherwise "
    "create the review and respond 201 with it.",
)

# ---------------------------------------------------------------------------
# Lattice arm
# ---------------------------------------------------------------------------

_LATTICE_INTRO = (
    # Neutral wording (see lattice.llm.prompt): no "whitelisted", "legal",
    # "rejected", "before it ever runs".
    "Lattice is a small domain-specific language for backend HTTP APIs over a SQLite "
    "database, written in Python syntax using only the declarations, operations and "
    "functions documented below. A validator checks each program's structure, names, "
    "column types, access rules and error declarations; the Lattice runtime then serves "
    "the endpoints over HTTP, turning each read pipeline into one SQL query."
)

_LATTICE_PROGRAM_SECTION = f"""## Program shape

The first line is exactly `# lattice: v1`. A program holds constants, `@step` \
functions and `@endpoint` functions; nothing else at the top level.

```python
@endpoint("POST", "/orders/{{order_id}}/cancel", auth=Customer)
def cancel(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound | Conflict:
    ...
```

- `@endpoint(METHOD, PATH, auth=Role)`: METHOD is "GET", "POST", "PUT", "PATCH" or \
"DELETE"; PATH is a literal whose `{{name}}` segments are parameters of the same name; \
`auth` is `Public`, `Customer` or `Admin` (Admin may call every endpoint; Customer \
endpoints accept Customer and Admin).
- Parameters: path segments come from the path; the others come from the query string \
for GET/DELETE and from the JSON body for POST/PUT/PATCH. Types: `Int(ge=, le=)`, \
`Float(ge=, le=)`, `Str(min_len=, max_len=)`, `OneOf("a", "b", ...)` (a Str limited to \
those values), `Bool`, `Date`, or bare `Int`/`Float`/`Str` (unbounded). Constraints are \
literals. A literal default is allowed: `limit: Int(ge=1, le=100) = 20`.
- `user: CurrentUser` is the authenticated caller as a one-row table with the Users \
columns (`col("id")`, `col("role")`, ...). Required when `auth` is not `Public`, \
absent when it is.
- Return annotation: one success kind, then every error the body can raise. Success: \
`One` (200, the single returned row as a JSON object), `Page` (200, \
`{{"items": [...], "limit": n, "offset": n}}`), `Created` (201, the inserted row; POST \
only), `NoContent` (204, empty body; return the result of `update`/`delete`/`insert`). \
Errors: {_error_table()}; the JSON body is `{{"error": "<name>", "detail": "<message>"}}`.
- Automatic responses, never declared: 401 `{{"error": "unauthorized"}}` for a missing \
or unknown token, 403 for a role not allowed by `auth`, 422 \
`{{"error": "validation", "detail": [...]}}` for a parameter that is missing, of the \
wrong type or outside its constraints (checked before the body runs).
- The body runs in one transaction: an error response rolls back every write."""

_API_OP_DOCS: dict[str, tuple[str, str]] = {
    "fetch": ("fetch(Table)", "all rows of a database table (read lazily, set-based)"),
    "owned": (
        "owned(t, by=user)",
        "keep the rows whose owner column is the caller's id, or, for a table owned "
        "through a parent (e.g. OrderItems), whose parent row is the caller's (Admin "
        "callers keep every row)",
    ),
    "mine": (
        "mine(t, by=user)",
        "keep only the caller's own rows, like `owned` but for Admin callers too",
    ),
    "one": (
        'one(t, else_=NotFound("thing"))',
        "exactly one row; zero rows respond with that error",
    ),
    "require": (
        'require(t, <Bool expr>, else_=Conflict("why"))',
        "respond with the error if any row of t fails the condition; else t unchanged",
    ),
    "page": (
        "page(t, limit=..., offset=...)",
        f"a page of rows; `limit` a literal <= {PAGE_LIMIT_MAX} or an Int parameter with "
        f"`le` <= {PAGE_LIMIT_MAX}; `offset` (optional) a literal or an `Int(ge=0)` parameter",
    ),
    "insert": (
        "insert(Table, owner=user, col=<expr>, ...)",
        "insert one row (id assigned automatically) and return it; `owner=user` for "
        "tables with an owner column, never otherwise",
    ),
    "update": (
        "update(t, col=<expr>, ...)",
        'set columns on the rows of t (by key); returns them updated, e.g. `stock=col("stock") '
        "- qty`",
    ),
    "delete": ("delete(t)", "delete the rows of t; returns them"),
}

_TABLE_OPS_IN_ENDPOINTS = ("where", "derive", "select", "rename", "group", "join", "sort", "limit")

_TABLE_OP_DOCS: dict[str, str] = {
    "where": "keep rows where the Bool predicate is true (null drops the row)",
    "derive": "add or replace columns",
    "select": "keep exactly the listed columns, in that order",
    "rename": "rename columns",
    "group": "one row per `by` group (by=[]: one row overall) with the aggregates",
    "join": 'on shared column names (`on=["product_id"]`), or `on={"id": "order_id"}` '
    "to match a left column with a differently named right one (the left name is kept); "
    "how 'inner' or 'left'",
    "sort": "ascending unless desc=True",
    "limit": "first n rows",
}


def _lattice_ops_section() -> str:
    if set(_API_OP_DOCS) != set(API_OP_SHAPES):
        raise AssertionError("_API_OP_DOCS out of sync with API_OP_SHAPES")
    lines = ["## Operations", "", "Endpoint operations (call each exactly this way):", ""]
    for op in API_OP_SHAPES:
        shape, doc = _API_OP_DOCS[op]
        lines.append(f"- `{shape}`: {doc}")
    lines += ["", "Table operations also available in endpoints:", ""]
    for op in _TABLE_OPS_IN_ENDPOINTS:
        lines.append(f"- `{_render_op_shape(op)}`: {_TABLE_OP_DOCS[op]}")
    return "\n".join(lines)


def _lattice_expressions_section() -> str:
    fns = "; ".join(_render_fn_sig(name) for name in sorted(API_FN_FUNCS))
    aggs = ", ".join(f"`agg.{name}`" for name in sorted(API_AGG_FUNCS))
    return (
        "## Expressions\n\n"
        'Column expressions use only: `col("name")`; literals; endpoint parameters and '
        "constants as plain values; `+ - * / %`; comparisons `== != < <= > >=`; `&`, `|`, "
        "`~` on Bool (never `and`/`or`/`not`); `when(cond, then=a, otherwise=b)`; "
        "`coalesce(a, b)`; `is_null(a)`. No loops, `if`, lambdas, comprehensions, method "
        "calls, subscripts or f-strings anywhere.\n\n"
        f"Functions: {fns}.\n\n"
        f"Aggregates (only as `group()` keyword values): {aggs}; `agg.count()` counts rows; "
        "`agg.at_min(x, of=o)` / `agg.at_max(x, of=o)` give `x` on the group's row with the "
        "smallest / largest `o`."
    )


# Loop-shaped work and the Part B additions (spec 2026-09-27 lattice-loops §4, §5): one
# short bullet per construct, one inline example each (the three fenced examples stay
# the only Examples). The baseline guide carries the same information in SQL/FastAPI
# terms (_BASELINE_LOOPS_SECTION).
API_WINDOW_EXAMPLE = (
    'numbered = window(visible, by=["status"], order=["created_at", "id"], '
    'n=win.row_number(), running=win.cum_sum(col("total")))'
)
API_TRAVERSE_EXAMPLE = (
    'start = where(categories, col("id") == category_id)',
    'tree = traverse(categories, start, parent="parent_id", child="id", max_depth=10)',
)
API_FOR_EACH_EXAMPLE = 'for_each(created, native.send_welcome, col("email"))'
API_UNIQUE_EXAMPLE = 'unique_or=Conflict("email taken")'
API_LIST_EXAMPLE = ('PUBLIC = ["id", "email", "name"]', "select(created, cols=PUBLIC)")

_LATTICE_LOOPS_SECTION = f"""## Row-by-row work, formats and uniqueness

- `window(t, by=[...], order=[...], desc=[...], name=win.<f>(...), ...)` keeps every \
row and adds values over its `by` group in `order` (nulls last): `win.row_number()`, \
`win.rank()`, `win.dense_rank()`; `win.lag(x, offset=1)` / `win.lead(x, offset=1)` \
(null past the edge); `win.cum_sum(x)`, `win.cum_count()`, `win.cum_min(x)`, \
`win.cum_max(x)` (running); `win.mean(x, rows=N)` / `win.sum(x, rows=N)` (the row and \
the N-1 before it). `by` + `order` must be unique: end `order` with a key. E.g. \
`{API_WINDOW_EXAMPLE}`.
- `traverse(edges, start, parent="parent_id", child="id", max_depth=N)`: the rows of \
`edges` reachable from the rows of `start` (a name) through `parent` -> `child` links \
(`direction="up"`: the ancestors), each once at its smallest `depth` (a new Int column, \
0 = start), in one query; cycle-safe; `max_depth` a literal 1-1000 or a bounded Int \
parameter; call `owned` first on an owned table. E.g. `{API_TRAVERSE_EXAMPLE[0]}` then \
`{API_TRAVERSE_EXAMPLE[1]}`.
- `for_each(t, native.<name>, <expr>, ...)`, a statement, calls an effect native the task \
declares (e.g. send an email) once per row of `t` after the endpoint commits, in order; \
`t` comes from `one`, `insert`, `update` or `page`/`limit` with n <= 100; a failure never \
changes the response (delivery is at-least-once). E.g. `{API_FOR_EACH_EXAMPLE}`.
- Parameter formats: `Email(max_len=)` (an email address) and `Str(pattern=r"...")` \
(the whole value matches the regular expression); a bad value answers 422.
- `insert(...)` / `update(...)` take `{API_UNIQUE_EXAMPLE}`: a write that hits a unique \
column answers that 409.
- A top-level list of column names, e.g. `{API_LIST_EXAMPLE[0]}`, can be reused as \
`{API_LIST_EXAMPLE[1]}`."""

# Optional values, text search, time and ordering (the edit-slice gap features): one
# short bullet per feature, each with an inline example; the baseline guide carries the
# same information in SQL/FastAPI terms (_BASELINE_OPTIONAL_SECTION).
API_OPTIONAL_EXAMPLES = (
    'where(visible, is_null(since) | (col("created_at") >= since))',
    'require(t, ~sent(title) | (col("created_at") >= fn.datetime_add(fn.now(), hours=-24)), '
    'else_=Forbidden("too late"))',
    'fn.contains(col("title"), sub=q, ignore_case=True)',
    'fn.datetime_diff(fn.now(), col("created_at"), unit="hours")',
    "update(t, deleted_at=None)",
    'sort(t, by=["created_at"], desc=[order == "newest"], nulls="smallest")',
)

_LATTICE_OPTIONAL_SECTION = f"""## Optional values, search, time and ordering

- `p: T | None = None` (e.g. `since: DateTime | None = None`): a parameter that is None \
when not sent; test it with `is_null(p)`, e.g. `{API_OPTIONAL_EXAMPLES[0]}`. None means \
"not given"; `= UNSET` means "leave the column unchanged" (update values only).
- `sent(p)`: a Bool, true when the request included the `= UNSET` parameter `p` (even \
as null); usable in any condition, e.g. `{API_OPTIONAL_EXAMPLES[1]}`.
- `{API_OPTIONAL_EXAMPLES[2]}`: a case-insensitive substring match (both sides \
lowercased, Unicode); `sub=""` matches every string.
- DateTime arithmetic: `fn.datetime_add(x, days=, hours=, minutes=, seconds=)` (at least \
one; negative is earlier) and `fn.datetime_diff(end, start, unit=)` (whole "days", \
"hours", "minutes" or "seconds", truncated toward zero), e.g. \
`{API_OPTIONAL_EXAMPLES[3]}`; `fn.now()` is one instant per request.
- `{API_OPTIONAL_EXAMPLES[4]}` (or `insert(T, c=None)`) writes null into a nullable column.
- `sort` directions may be Bool expressions of parameters, and `nulls=` places nulls: \
"last" (default), "first", or "smallest" (first ascending, last descending), e.g. \
`{API_OPTIONAL_EXAMPLES[5]}`."""

# owned() vs mine() (the thin slice's "my items" trap); the baseline guide carries the
# same information in SQL terms (BASELINE_OWN_ROWS).
LATTICE_OWN_ROWS = (
    "- `owned(t, by=user)` = rows the caller may access (admins: all rows). "
    "`mine(t, by=user)` = the caller's own rows only (admins too) — use it for "
    "'my …' features."
)

_LATTICE_RULES_SECTION = f"## Rules\n\n{LATTICE_OWN_ROWS}\n" + """\
- No imports are needed: all operations, `col`, `when`, `coalesce`, `is_null`, `fn`, \
`agg`, `endpoint`, `step`, the parameter types, success kinds, errors, roles, \
`CurrentUser` and the table names (`Users`, `Orders`, ...) are always available.
- Each statement assigns one operation or `@step` call to a new name. Never reassign a \
name or use a built-in name (operation, function, type or table, e.g. `where`, \
`Orders`) as a variable or step name. A parameter's name is its query/JSON field name, \
so parameters may also be named like a Python builtin (`zip`, `id`, `type`) or `limit`/`sort`.
- Never nest calls: table arguments are plain names (`found = where(...)` then \
`order = one(found, ...)`, not `one(where(...))`).
- The body ends with exactly one `return <name>`. Names bound by `one`, `require`, \
`insert`, `update` and `delete` may stay unused (they act by their effect).
- `update` and `delete` need rows of one database table that still contain its key \
column (after `fetch`, `owned`, `where`, `one`, `select`, ...; not after `join`, `group` \
or `rename`).
- `join(l, r, on=K)` needs `r` unique on `K` (a table key inside `K`, or `r` from \
`group(..., by=K)`; with `on={"id": "order_id"}`, unique on the right names); \
`join(l, r, on=[])` attaches a one-row `r` to every row of `l`.
- Columns listed as private never appear in a response: `select` the others."""

# Pitfall advice, removable for the no-hints condition (the reference stays).
LATTICE_ADVICE: tuple[str, ...] = (
    "- Ownership: for a table with an owner column or owned through a parent, call "
    "`owned(t, by=user)` right after `fetch` and before `where`/`one`/`update`/`delete`/"
    "`return`, so another customer's row answers 404 exactly like a missing one; "
    "`insert` into an owned table passes `owner=user` (never set the owner column "
    "yourself); an aggregate over many customers' rows is public only if it groups by, "
    "filters on and aggregates only the table's columns listed as \"may be aggregated "
    'publicly" (e.g. review counts per product).',
    "- Bounds: every parameter written by `insert`/`update` needs both bounds: "
    "`Int(ge=, le=)`, `Float(ge=, le=)`, `Str(min_len=, max_len=)` or `OneOf(...)`, "
    "using the limits the task states, so out-of-range input answers 422 and never "
    "reaches the database.",
    "- Errors: declare every error the body raises in the return annotation, and only "
    "those: `one(..., else_=NotFound(...))` for a missing row, `require(...)` with "
    "`Conflict`, `Forbidden` or `BadRequest` for a business rule, in the order the "
    "task lists the checks.",
    "- Lists and related data: `sort` list responses by a key, then return "
    "`page(ordered, limit=limit, offset=offset)` with `limit: Int(ge=1, le=...)` "
    "(le <= 100); fetch related data set-based with `join` and `group` (e.g. a count "
    "per order) instead of one lookup per row.",
)

LATTICE_EXAMPLES: tuple[str, ...] = (
    """# lattice: v1
@endpoint("GET", "/addresses", auth=Customer)
def list_addresses(
    user: CurrentUser, limit: Int(ge=1, le=50) = 20, offset: Int(ge=0) = 0
) -> Page:
    addresses = fetch(Addresses)
    visible = owned(addresses, by=user)
    ordered = sort(visible, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
""",
    """# lattice: v1
@endpoint("PATCH", "/products/{product_id}/stock", auth=Admin)
def set_stock(
    product_id: Int(ge=1), stock: Int(ge=0, le=10000), user: CurrentUser
) -> One | NotFound:
    products = fetch(Products)
    found = where(products, col("id") == product_id)
    slim = select(found, cols=["id", "name", "stock"])
    product = one(slim, else_=NotFound("product"))
    updated = update(product, stock=stock)
    return updated
""",
    """# lattice: v1
@endpoint("POST", "/products/{product_id}/reviews", auth=Customer)
def post_review(
    product_id: Int(ge=1, le=1000000),
    rating: Int(ge=1, le=5),
    body: Str(min_len=1, max_len=500),
    user: CurrentUser,
) -> Created | NotFound | Forbidden | Conflict:
    products = fetch(Products)
    wanted = where(products, col("id") == product_id)
    product = one(wanted, else_=NotFound("product"))
    items = fetch(OrderItems)
    visible_items = owned(items, by=user)
    this_product = where(visible_items, col("product_id") == product_id)
    times_bought = group(this_product, by=[], n=agg.count())
    ordered = require(times_bought, col("n") > 0, else_=Forbidden("product not ordered"))
    reviews = fetch(Reviews)
    visible_reviews = owned(reviews, by=user)
    same = where(visible_reviews, col("product_id") == product_id)
    existing = group(same, by=[], n=agg.count())
    fresh = require(existing, col("n") == 0, else_=Conflict("already reviewed"))
    review = insert(Reviews, owner=user, product_id=product_id, rating=rating, body=body)
    return review
""",
)


def _examples_section(solutions: tuple[str, ...]) -> str:
    parts = ["## Examples"]
    for problem, code in zip(EXAMPLE_PROBLEMS, solutions, strict=True):
        parts.append(problem)
        parts.append(f"```python\n{code.rstrip()}\n```")
    return "\n\n".join(parts)


def _advice_section(bullets: tuple[str, ...]) -> str:
    return "## Advice\n\n" + "\n".join(bullets)


def api_lattice_system_prompt(hints: bool = True) -> str:
    """Arms B/C system prompt; ``hints=False`` drops the Advice section."""
    sections = [
        _LATTICE_INTRO,
        _LATTICE_PROGRAM_SECTION,
        _lattice_ops_section(),
        _lattice_expressions_section(),
        _LATTICE_LOOPS_SECTION,
        _LATTICE_OPTIONAL_SECTION,
        _LATTICE_RULES_SECTION,
    ]
    if hints:
        sections.append(_advice_section(LATTICE_ADVICE))
    sections += [_examples_section(LATTICE_EXAMPLES), _OUTPUT_FORMAT_SECTION]
    return "\n\n".join(sections) + "\n"


# ---------------------------------------------------------------------------
# Baseline (FastAPI + sqlite3) arm
# ---------------------------------------------------------------------------

_BASELINE_INTRO = (
    "You are an expert backend engineer. Write one Python 3 script that adds HTTP "
    "endpoints to a FastAPI app over a SQLite database, using the provided `platform_kit` "
    "module for authentication and database access. The script is imported once, "
    "unattended, in a fresh directory holding `platform_kit.py` and the database "
    "`app.db`; its endpoints are then called over HTTP and their responses (status codes, "
    "JSON bodies and database effects) are compared with the specification, so follow "
    "the task and the conventions below precisely."
)

_BASELINE_KIT_SECTION = """## platform_kit

`from platform_kit import app, get_db, current_user, require_role` gives you:

- `app`: the FastAPI application. Add every route to it (`@app.get(...)`, \
`@app.post(...)`, ...); never create another `FastAPI()`.
- `get_db()`: a dependency returning a `sqlite3.Connection` to `app.db` with \
`row_factory = sqlite3.Row` (`dict(row)` gives a row's columns). Use it through \
`Depends(get_db)`, use `?` placeholders for every value, and call `db.commit()` after \
your writes.
- `current_user`: a dependency returning the caller as `{"id", "role", "email", \
"name"}`; it answers 401 `{"error": "unauthorized"}` for a missing or unknown \
`Authorization: Bearer <token>`.
- `require_role("Customer")`: a dependency factory like `current_user` that also \
answers 403 `{"error": "forbidden"}` unless the caller has one of the given roles \
(Admin may call every endpoint). Public endpoints use no auth dependency.

The kit only authenticates: ownership checks, input validation, error responses and \
pagination are the script's job."""

# The same information as LATTICE_OWN_ROWS, in SQL terms.
BASELINE_OWN_ROWS = (
    "- For the caller's own rows only ('my …' features), filter by "
    "`owner_id == current_user.id` (admins too); the access filter "
    "`(owner_id = ? OR ? = 'Admin')` lets superusers see all rows."
)

_BASELINE_CONVENTIONS_SECTION = f"""## Conventions

- Parameters: path segments come from the path; the others come from the query \
string for GET/DELETE and from the JSON body (a pydantic model) for POST/PUT/PATCH.
- Success responses: a single row is its JSON object (200); a list is \
`{{"items": [...], "limit": n, "offset": n}}` (200); a created row is its JSON object \
with status 201 (`status_code=201` on the route); a delete or update without a body \
is `Response(status_code=204)`.
- Errors: {_error_table()}, each as \
`JSONResponse(status_code=404, content={{"error": "not_found", "detail": "order"}})`.
- Invalid input (missing, wrong type or outside its constraints) answers 422 \
`{{"error": "validation", "detail": [...]}}`. Register this handler once per script:

```python
@app.exception_handler(RequestValidationError)
async def invalid_input(request: Request, exc: RequestValidationError) -> JSONResponse:
    detail = jsonable_encoder(exc.errors())
    return JSONResponse(status_code=422, content={{"error": "validation", "detail": detail}})
```

- Rows of a table with an owner column belong to that user; Admin callers may see \
and change every row. Columns listed as private never appear in a response.
{BASELINE_OWN_ROWS}
- Everything a request writes succeeds or fails together: commit only after every \
check passed.
- Dates are ISO `YYYY-MM-DD` strings. Import only the standard library, `fastapi`, \
`pydantic` and `platform_kit`; no network, no files other than through `get_db`."""

# The same information as _LATTICE_LOOPS_SECTION, for SQL and FastAPI.
_BASELINE_LOOPS_SECTION = """## Row-by-row work, formats and uniqueness

- Per-row values over ordered rows: SQL window functions \
`... OVER (PARTITION BY ... ORDER BY ...)`: `ROW_NUMBER()`, `RANK()`, `DENSE_RANK()`; \
`LAG(x, 1)` / `LEAD(x, 1)` (NULL past the edge); `SUM(x)`, `COUNT(*)`, `MIN(x)`, `MAX(x)` \
(running); `AVG(x)` / `SUM(x)` with `ROWS BETWEEN N-1 PRECEDING AND CURRENT ROW`. End \
`ORDER BY` with a key so ties cannot change the result; nulls last: `ORDER BY x IS NULL, \
x`. E.g. `ROW_NUMBER() OVER (PARTITION BY status ORDER BY created_at, id) AS n`.
- Tree walks (parent/child columns): one `WITH RECURSIVE` query from the start rows with \
a depth counter and `WHERE w.depth < N`, then `MIN(depth)` per id (cycle-safe; each row \
once at its smallest depth, 0 = start); apply the ownership filter inside. E.g. \
`WITH RECURSIVE w(id, depth) AS (SELECT id, 0 FROM categories WHERE id = ? UNION \
SELECT c.id, w.depth + 1 FROM categories c JOIN w ON c.parent_id = w.id WHERE w.depth \
< 10) SELECT id, MIN(depth) AS depth FROM w GROUP BY id`.
- Side effects the task provides (e.g. send an email): call it once per row only after \
`db.commit()`, in order, for at most 100 rows, catching its errors so the response never \
depends on it (it may be retried: at-least-once). E.g. `for r in rows:` \
`try: send_welcome(r["email"])` `except Exception: pass`.
- Formats: `Field(pattern=r"...")` (the whole value must match); for an email use an \
email pattern such as `r"[^@\\s]+@[^@\\s]+\\.[A-Za-z]{2,}"`; a bad value answers 422.
- A write that hits a unique column raises `sqlite3.IntegrityError`: answer 409 with the \
JSON error body.
- A module-level list of column names, e.g. `PUBLIC = ["id", "email", "name"]`, can be \
reused in every `SELECT` (`", ".join(PUBLIC)`)."""

# The same information as _LATTICE_OPTIONAL_SECTION, for SQL and FastAPI.
_BASELINE_OPTIONAL_SECTION = """## Optional values, search, time and ordering

- An optional parameter that is None when not sent: `since: datetime | None = None`; \
filter with `WHERE (:since IS NULL OR created_at >= :since)`. None means "not given"; \
a partial update keeps every column whose field was not sent \
(`payload.model_dump(exclude_unset=True)`).
- Whether a body field was sent (even as null): `"title" in payload.model_fields_set`; \
usable in any check, e.g. reject a late title change with 403.
- Case-insensitive substring: `instr(py_lower(title), py_lower(:q)) > 0` after \
`db.create_function("py_lower", 1, lambda s: s if s is None else s.lower())` (SQLite's \
`lower()` folds ASCII only); an empty `q` matches every string.
- Time: `datetime.now(timezone.utc)` once per request; `now - timedelta(hours=24)`; \
whole units truncated toward zero: `int((end - start) / timedelta(hours=1))`; compare \
with stored values in UTC and their stored text format.
- `UPDATE items SET deleted_at = NULL WHERE id = ?` clears a nullable column.
- A sort direction from a parameter: pick `"DESC"` or `"ASC"` in code from a fixed \
choice (never paste input into SQL); SQLite puts NULLs first ascending and last \
descending; `ORDER BY x IS NULL, x` puts them last."""

# Pitfall advice, removable for the no-hints condition (the reference stays).
BASELINE_ADVICE: tuple[str, ...] = (
    "- Ownership: for a table with an owner column put the check in the SQL WHERE "
    "clause (`WHERE id = ? AND (customer_id = ? OR ? = 'Admin')`), and for a table owned "
    "through a parent check the parent's owner (JOIN it); never fetch or change a row "
    "by id alone, and answer 404 when the row is missing or belongs to someone else; an "
    "INSERT sets the owner column to the caller's id; an aggregate over many customers' "
    "rows is public only if it groups by, filters on and aggregates only the table's "
    'columns listed as "may be aggregated publicly" (e.g. review counts per product).',
    "- Bounds: every input written to the database needs both bounds: "
    "`Field(ge=, le=)`, `Field(min_length=, max_length=)` or `Literal[...]` in the "
    "body model (`Query(...)`/`Path(...)` for other parameters), using the limits the "
    "task states, so out-of-range input answers 422 and never reaches the database.",
    "- Errors: map a missing row to 404 (test `fetchone()` for `None`, or "
    "`rowcount == 0`) instead of letting it fail with a 500; answer a business rule "
    "with 409, 403 or 400 as the task says, with the JSON error body, in the order the "
    "task lists the checks.",
    "- Lists and related data: add `ORDER BY` a key and `LIMIT ? OFFSET ?` to every "
    "list query, with `limit` bounded (le <= 100); fetch related data in one statement "
    "with JOIN / GROUP BY (e.g. a count per order) instead of one query per row.",
)

_BASELINE_IMPORTS = """import sqlite3
from typing import Annotated

from fastapi import Depends, Path, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from platform_kit import app, get_db, require_role


@app.exception_handler(RequestValidationError)
async def invalid_input(request: Request, exc: RequestValidationError) -> JSONResponse:
    detail = jsonable_encoder(exc.errors())
    return JSONResponse(status_code=422, content={"error": "validation", "detail": detail})


def error(status: int, name: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": name, "detail": detail})
"""

BASELINE_EXAMPLES: tuple[str, ...] = (
    _BASELINE_IMPORTS
    + """

@app.get("/addresses")
def list_addresses(
    user: Annotated[dict, Depends(require_role("Customer"))],
    db: Annotated[sqlite3.Connection, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    rows = db.execute(
        "SELECT id, customer_id, line1, city, zip FROM addresses"
        " WHERE customer_id = ? OR ? = 'Admin' ORDER BY id LIMIT ? OFFSET ?",
        (user["id"], user["role"], limit, offset),
    ).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}
""",
    _BASELINE_IMPORTS
    + """

class StockIn(BaseModel):
    stock: int = Field(ge=0, le=10000)


@app.patch("/products/{product_id}/stock")
def set_stock(
    product_id: Annotated[int, Path(ge=1)],
    payload: StockIn,
    user: Annotated[dict, Depends(require_role("Admin"))],
    db: Annotated[sqlite3.Connection, Depends(get_db)],
):
    cur = db.execute("UPDATE products SET stock = ? WHERE id = ?", (payload.stock, product_id))
    if cur.rowcount == 0:
        return error(404, "not_found", "product")
    db.commit()
    row = db.execute("SELECT id, name, stock FROM products WHERE id = ?", (product_id,))
    return dict(row.fetchone())
""",
    _BASELINE_IMPORTS
    + """

class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    body: str = Field(min_length=1, max_length=500)


@app.post("/products/{product_id}/reviews", status_code=201)
def post_review(
    product_id: Annotated[int, Path(ge=1, le=1000000)],
    payload: ReviewIn,
    user: Annotated[dict, Depends(require_role("Customer"))],
    db: Annotated[sqlite3.Connection, Depends(get_db)],
):
    mine = {"pid": product_id, "uid": user["id"], "role": user["role"]}
    if db.execute("SELECT 1 FROM products WHERE id = :pid", mine).fetchone() is None:
        return error(404, "not_found", "product")
    bought = db.execute(
        "SELECT COUNT(*) FROM order_items i JOIN orders o ON o.id = i.order_id"
        " WHERE i.product_id = :pid AND (o.customer_id = :uid OR :role = 'Admin')",
        mine,
    ).fetchone()[0]
    if bought == 0:
        return error(403, "forbidden", "product not ordered")
    existing = db.execute(
        "SELECT COUNT(*) FROM reviews"
        " WHERE product_id = :pid AND (customer_id = :uid OR :role = 'Admin')",
        mine,
    ).fetchone()[0]
    if existing > 0:
        return error(409, "conflict", "already reviewed")
    cur = db.execute(
        "INSERT INTO reviews (product_id, customer_id, rating, body) VALUES (?, ?, ?, ?)",
        (product_id, user["id"], payload.rating, payload.body),
    )
    db.commit()
    row = db.execute("SELECT * FROM reviews WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)
""",
)


def api_baseline_system_prompt(hints: bool = True) -> str:
    """Arms A/P system prompt; ``hints=False`` drops the Advice section."""
    sections = [
        _BASELINE_INTRO,
        _BASELINE_KIT_SECTION,
        _BASELINE_CONVENTIONS_SECTION,
        _BASELINE_LOOPS_SECTION,
        _BASELINE_OPTIONAL_SECTION,
    ]
    if hints:
        sections.append(_advice_section(BASELINE_ADVICE))
    sections += [
        _examples_section(BASELINE_EXAMPLES),
        _OUTPUT_FORMAT_SECTION.replace("program", "script"),
    ]
    return "\n\n".join(sections) + "\n"


# ---------------------------------------------------------------------------
# Task message (identical in both arms but for one line)
# ---------------------------------------------------------------------------

ARM_NOTES: dict[str, str] = {
    "lattice": "Write a Lattice program; the Lattice runtime serves its endpoints.",
    "baseline": "Write a Python script whose routes are defined on `platform_kit.app`.",
}


def api_task_message(task: ApiTask, arm_kind: ArmKind) -> str:
    """The user-turn message: the task's endpoint spec, the database schema
    (``bench.api_datasets.schema_description()``, identical in both arms) and one
    arm-specific line saying how the code is served."""
    if arm_kind not in ARM_NOTES:
        raise ValueError(f"unknown arm kind: {arm_kind!r}")
    from bench.api_datasets import schema_description

    return "\n\n".join([task.spec.strip(), schema_description(), ARM_NOTES[arm_kind]])
