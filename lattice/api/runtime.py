"""The Lattice API runtime (Phase A): serve lowered endpoint plans over a SQL backend.

Per request: authenticate (`Authorization: Bearer <token>`, one statement: the token
is looked up in the users table's token column, or decoded to the caller's key by a
native -- `ApiProfile.auth`), refuse an inactive caller (`ApiProfile.active_column`),
check the role, parse and validate the parameters, then
run the plan's steps in order inside one transaction — each read pipeline is one
SELECT, each write one statement, checks cost nothing — and serialize the returned
rows (a response model's fields in its order; a Page in the profile's page shape, with
the total when the page has one; DateTime values in `ApiProfile.datetime_wire`). Any
error response rolls back; its body has the `ApiProfile.error_body` shape. A write with
`unique_or=Conflict(...)` answers that 409 when it hits a UNIQUE constraint (others get
the generic `UNIQUE_CONFLICT`). `fn.now()` is the request's one instant, taken when the
request starts.

Natives (`native.<name>(...)`) are pure Python functions registered as SQL functions
on every connection: `build_app`/`serve_source` take their implementations as
`natives={name: callable}`. Effect natives (`for_each(t, native.x, ...)`) come in the
same mapping but are never SQL functions: a for_each step writes outbox rows in the
request's transaction (`lattice.api.outbox`), and after COMMIT the request dispatches
them in order, marking each done or failed; the response is decided before the
dispatch and never depends on it.

A new connection is opened per request and closed after it (no connection is held
between requests). Every new connection is passed to the module attribute
`on_connect` (a no-op by default) — the benchmark harness replaces it to count
statements.

Everything backend-specific (opening a connection, registering the functions the SQL
calls, starting/ending transactions, value encoding and decoding) is delegated to the
plan's `lattice.api.backend.Dialect` (`EndpointPlan.backend`; SQLite by default), which
also stores Uuid/DateTime values in the plan's spellings (`EndpointPlan.uuid_storage` /
`datetime_storage`, from the profile).

Body parameters come from a JSON object, or -- for `@endpoint(..., body="form")` --
from URL-encoded form fields, converted like query-string text.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import urllib.parse
import uuid
from collections.abc import Callable, Mapping, Sequence
from http import HTTPStatus
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from lattice.api import outbox
from lattice.api.backend import (
    Dialect,
    NativeImpl,
    dialect_for,
    parse_datetime,
    uuid_text,
    wire_datetime,
)
from lattice.api.ir import (
    CallStep,
    CheckStep,
    EndpointPlan,
    ErrorSpec,
    ForEachStep,
    PostStep,
    ReadStep,
    T2CheckStep,
    WriteStep,
    lower_api,
)
from lattice.api.native_context import bind_settings
from lattice.api.sql import bind_values, compile_lookup, decode
from lattice.catalog import (
    AUTHENTICATED,
    EMAIL_MAX_LEN,
    LIST_KIND,
    PUBLIC_ROLE,
    T2_EFFECT_PREFIX,
    USER_PARAM,
)
from lattice.check import CheckResult, check_source
from lattice.check.api_info import MISSING, UNSET, ParamSpec, pattern_problem
from lattice.profile import Profile, json_key_of
from lattice.runtime import LatticeInternalError, LatticeRuntimeError
from lattice.t2 import support as t2s
from lattice.typesys import Scalar, Schema

__all__ = ["ApiRuntimeError", "build_app", "error_body", "on_connect", "serve_source"]

# The JSON error code of each status an error response may have.
STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation",
    500: "internal",
}


def on_connect(conn: Any) -> None:
    """Called with every new database connection (replaceable module attribute)."""


class ApiRuntimeError(LatticeRuntimeError):
    """A data-dependent failure while serving a request (e.g. R310: one() matched
    more than one row). Responds 500; `.render()` gives the located message."""


class _ErrorResponse(Exception):
    """An error response: its status, JSON error code and detail (None: no detail),
    rendered in the plan's error body style (`error_body`)."""

    def __init__(self, status: int, code: str, detail: object = None) -> None:
        super().__init__(status)
        self.status = status
        self.code = code
        self.detail = detail


def error_body(style: str, status: int, code: str, detail: object = None) -> dict[str, object]:
    """The JSON body of an error response (`ApiProfile.error_body`): "lattice" is
    {"error": code} plus "detail" when there is one; "fastapi" is FastAPI's
    {"detail": ...}, the HTTP status phrase when there is no detail. "drf" is "fastapi",
    except a validation error: {field: [msg, ...]} in parameter order, a problem without
    a field (the body is not an object) under "non_field_errors". "json_key(<key>)" is
    "fastapi" under <key>."""
    if style == "drf" and code == "validation" and isinstance(detail, list):
        return drf_fields(detail)
    if style in ("fastapi", "drf"):
        return {"detail": HTTPStatus(status).phrase if detail is None else detail}
    key = json_key_of(style)
    if key is not None:
        return {key: HTTPStatus(status).phrase if detail is None else detail}
    body: dict[str, object] = {"error": code}
    if detail is not None:
        body["detail"] = detail
    return body


def drf_fields(detail: list[Any]) -> dict[str, object]:
    """A validation detail ([{"loc": [source, name], "msg": ...}, ...]) as Django REST
    framework's {name: [msg, ...]}; an entry without a name ({"loc": ["body"]}) goes
    under "non_field_errors"."""
    fields: dict[str, list[object]] = {}
    for entry in detail:
        loc = entry["loc"]
        key = loc[-1] if len(loc) > 1 else "non_field_errors"
        fields.setdefault(key, []).append(entry["msg"])
    return dict(fields)


def _json_error(style: str, status: int, code: str, detail: object = None) -> JSONResponse:
    return JSONResponse(error_body(style, status, code, detail), status_code=status)


def plan_dialect(plan: EndpointPlan) -> Dialect:
    """The dialect a plan's SQL was compiled for (backend and Uuid/DateTime storage)."""
    return dialect_for(
        plan.backend, uuid_storage=plan.uuid_storage, datetime_storage=plan.datetime_storage
    )


def _connect(dialect: Dialect, db_path: Path, natives: Mapping[str, NativeImpl]) -> Any:
    # on_connect is looked up at call time: the harness may replace it
    return dialect.connect(db_path, lambda conn: on_connect(conn), natives)


# ---- parameters ------------------------------------------------------------------------

# ASCII digits only (`\d` would accept any Unicode decimal digit, which `int()` then
# converts); a float additionally allows a fraction and an exponent, but no `_`,
# `inf` or `nan` spellings.
_INT = re.compile(r"[+-]?[0-9]+", re.ASCII)
_FLOAT = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", re.ASCII)
# SQLite INTEGER is a signed 64-bit value: an int outside it cannot be bound.
INT_MIN = -(2**63)
INT_MAX = 2**63 - 1
_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})
_TYPE_NAMES = {
    Scalar.INT: "an integer",
    Scalar.FLOAT: "a number",
    Scalar.STR: "a string",
    Scalar.BOOL: "a boolean",
    Scalar.DATE: "an ISO date (YYYY-MM-DD)",
    Scalar.DATETIME: "an ISO date-time",
    Scalar.UUID: "a UUID",
}


def _convert(spec: ParamSpec, raw: object, *, text: bool) -> object:
    """The typed value of one parameter, or raise ValueError(message). `text`: the raw
    value comes from the path or the query string (always a string).

    Every accepted value is representable in SQLite: an Int must lie in the signed
    64-bit range, a Float must be finite, and a Str must be encodable as UTF-8 (a
    JSON body may carry a lone surrogate such as "\\ud800"); anything else is a 422.
    """
    want = spec.scalar
    bad = ValueError(f"must be {_TYPE_NAMES[want]}") if want is not None else ValueError()
    match want:
        case Scalar.INT:
            number: int
            if text and isinstance(raw, str) and _INT.fullmatch(raw.strip()):
                try:
                    number = int(raw.strip())
                except ValueError:  # beyond Python's int-from-string digit limit
                    raise _out_of_range() from None
            elif not text and isinstance(raw, int) and not isinstance(raw, bool):
                number = raw
            elif not text and isinstance(raw, float) and raw.is_integer():
                number = int(raw)
            else:
                raise bad
            if not INT_MIN <= number <= INT_MAX:
                raise _out_of_range()
            return number
        case Scalar.FLOAT:
            value: float
            try:
                if text and isinstance(raw, str) and _FLOAT.fullmatch(raw.strip()):
                    value = float(raw.strip())
                elif not text and isinstance(raw, int | float) and not isinstance(raw, bool):
                    value = float(raw)  # a huge JSON integer overflows here
                else:
                    raise bad
            except OverflowError:
                raise bad from None
            if not math.isfinite(value):
                raise bad
            return value
        case Scalar.STR:
            if isinstance(raw, str):
                try:
                    raw.encode("utf-8")
                except UnicodeEncodeError:
                    raise ValueError("must be valid UTF-8 text") from None
                return raw
            raise bad
        case Scalar.BOOL:
            if text and isinstance(raw, str) and raw.lower() in _TRUE | _FALSE:
                return raw.lower() in _TRUE
            if not text and isinstance(raw, bool):
                return raw
            raise bad
        case Scalar.DATE:
            if isinstance(raw, str):
                try:
                    return dt.date.fromisoformat(raw)
                except ValueError:
                    raise bad from None
            raise bad
        case Scalar.DATETIME:  # a naive date-time is UTC
            value_dt = parse_datetime(raw) if isinstance(raw, str) else None
            if value_dt is None:
                raise bad
            return value_dt
        case Scalar.UUID:  # hyphenated or 32 hex digits, any case
            text_uuid = uuid_text(raw) if isinstance(raw, str) else None
            if text_uuid is None:
                raise bad
            return uuid.UUID(text_uuid)  # bound in the dialect's Uuid storage
        case _:
            raise LatticeInternalError(f"parameter '{spec.name}' has no scalar type")


def _out_of_range() -> ValueError:
    return ValueError(f"must be an integer between {INT_MIN} and {INT_MAX}")


def _constrain(spec: ParamSpec, value: object) -> None:
    if spec.one_of is not None and value not in spec.one_of:
        raise ValueError("must be one of " + ", ".join(repr(v) for v in spec.one_of))
    if isinstance(value, int | float) and not isinstance(value, bool):
        if spec.ge is not None and value < spec.ge:
            raise ValueError(f"must be >= {_num(spec.ge)}")
        if spec.le is not None and value > spec.le:
            raise ValueError(f"must be <= {_num(spec.le)}")
    if isinstance(value, str):
        if spec.min_len is not None and len(value) < spec.min_len:
            raise ValueError(f"must have at least {spec.min_len} characters")
        if spec.max_len is not None and len(value) > spec.max_len:
            raise ValueError(f"must have at most {spec.max_len} characters")
        if spec.email and len(value) > EMAIL_MAX_LEN:
            raise ValueError(f"must have at most {EMAIL_MAX_LEN} characters")
        if (mismatch := pattern_problem(spec, value)) is not None:
            raise ValueError(mismatch)


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _default(spec: ParamSpec) -> object:
    """A parameter's default value (a Uuid default as a `uuid.UUID`, like a sent one)."""
    if spec.scalar is Scalar.UUID and isinstance(spec.default, str):
        return uuid.UUID(spec.default)
    return spec.default


FORM_TYPE = "application/x-www-form-urlencoded"


def form_fields(body: bytes, content_type: str | None) -> dict[str, str]:
    """The fields of a form body (`@endpoint(..., body="form")`): a URL-encoded body is
    parsed (a repeated field keeps its last value); any other content type has no
    fields, so its required parameters are missing (422)."""
    media = (content_type or "").partition(";")[0].strip().lower()
    if media != FORM_TYPE:
        return {}
    return dict(urllib.parse.parse_qsl(body.decode("latin-1"), keep_blank_values=True))


def _parse_params(
    plan: EndpointPlan,
    path: Mapping[str, str],
    query: Mapping[str, str],
    body: bytes,
    content_type: str | None = None,
) -> dict[str, object]:
    detail: list[dict[str, object]] = []
    values: dict[str, object] = {}
    data: Mapping[str, object] = {}
    form = plan.body == "form"
    if form:
        data = form_fields(body, content_type)
    elif any(p.source == "body" for p in plan.params):
        parsed: object = {}
        if body.strip():
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = None
        if not isinstance(parsed, dict):
            detail.append({"loc": ["body"], "msg": "the body must be a JSON object"})
            raise _ErrorResponse(plan.validation_status, "validation", detail)
        data = parsed
    for spec in plan.params:
        if spec.source == "user":
            continue
        source: Mapping[str, object] = {"path": path, "query": query, "body": data}[spec.source]
        loc = [spec.source, spec.name]
        if spec.optional and spec.name in source and source[spec.name] is None:
            # an explicit null: sets NULL, where every column it is written to allows it
            if spec.name in plan.null_ok:
                values[spec.name] = None
            else:
                detail.append({"loc": loc, "msg": "must not be null"})
            continue
        if spec.name not in source or source[spec.name] is None:
            if spec.default is not MISSING:
                values[spec.name] = _default(spec)
            else:
                detail.append({"loc": loc, "msg": "field required"})
            continue
        try:
            text = spec.source != "body" or form  # form fields are text, like a query
            value = _convert(spec, source[spec.name], text=text)
            _constrain(spec, value)
        except ValueError as err:
            detail.append({"loc": loc, "msg": str(err)})
            continue
        values[spec.name] = value
    if detail:
        raise _ErrorResponse(plan.validation_status, "validation", detail)
    return values


# ---- auth ---------------------------------------------------------------------------------


def _token(header: str | None) -> str | None:
    if header is None:
        return None
    scheme, _, token = header.strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def auth_sql(plan: EndpointPlan, dialect: Dialect) -> str:
    """The authentication statement: the caller's row by token column, or by key (or
    the NativeToken lookup column) = the auth native applied to the token -- its OneOf
    columns decoded from their stored spellings (the caller's row holds values)."""
    if plan.auth_native is not None:
        return compile_lookup(
            plan.users_table,
            plan.user_columns,
            plan.token_lookup or plan.user_key,
            native=plan.auth_native,
            dialect=dialect,
            stored=plan.user_stored,
        )
    return compile_lookup(
        plan.users_table,
        plan.user_columns,
        plan.token_column,
        dialect=dialect,
        stored=plan.user_stored,
    )


def ambiguous_caller(column: str | None) -> str:
    """R311's message: the decoded token matched more than one users row."""
    return f"ambiguous caller: the token's {column} matches more than one user; expected one"


def caller_role(plan: EndpointPlan, row: Sequence[Any]) -> str:
    """The caller's role: the profile's one role (NoRoles), else from the role column
    or the admin flag column."""
    if plan.fixed_role is not None:
        return plan.fixed_role
    if plan.admin_flag is not None:
        flagged = row[plan.user_columns.index(plan.admin_flag)]
        return plan.admin_role if flagged else plan.flag_role
    stored = str(row[plan.user_columns.index(plan.role_column)])
    if plan.role_values:  # RoleColumn(values=...): a value outside the map is no role
        return dict(plan.role_values).get(stored, "")
    return stored


def role_allowed(plan: EndpointPlan, role: str) -> bool:
    """Whether a caller of this role may call the endpoint: exactly `plan.roles` when set
    (auth=Roles(...) / Authenticated), else the endpoint's role or the admin role."""
    if plan.auth in (PUBLIC_ROLE, AUTHENTICATED):  # anyone / any authenticated caller
        return True
    if plan.roles is not None:
        return role in plan.roles
    return role in (plan.auth, plan.admin_role)


def _authenticate(
    plan: EndpointPlan, dialect: Dialect, conn: Any, token: str
) -> tuple[object, str, tuple[Any, ...]]:
    """(caller id, role, the caller's row as `plan.user_columns`): one statement.
    Unknown caller: 401; several rows (NativeToken unique="runtime"): 500, R311; an
    inactive one (`plan.active_column` not true): the plan's inactive status; a role
    that may not call the endpoint: 403 -- in that order."""
    rows = conn.execute(auth_sql(plan, dialect), (token,)).fetchmany(2)
    if not rows:
        raise _no_caller(plan, dialect, conn, token)
    if len(rows) > 1:  # NativeToken(unique="runtime"): never pick one of them
        raise ApiRuntimeError("R311", plan.span, ambiguous_caller(plan.token_lookup))
    row = rows[0]
    user_id = row[plan.user_columns.index(plan.user_key)]
    if plan.active_column is not None and not row[plan.user_columns.index(plan.active_column)]:
        status = plan.inactive_status
        raise _ErrorResponse(status, STATUS_CODES[status], plan.inactive_detail)
    role = caller_role(plan, row)
    if not role_allowed(plan, role):
        raise _ErrorResponse(403, "forbidden", plan.forbidden_detail)
    return user_id, role, tuple(row)


# ---- steps ---------------------------------------------------------------------------------

Result = tuple[tuple[str, ...], list[tuple[Any, ...]]]


def _project(result: Result, columns: Sequence[str]) -> list[tuple[Any, ...]]:
    cols, rows = result
    index = [cols.index(c) for c in columns]
    return [tuple(r[i] for i in index) for r in rows]


def _unauthorized(plan: EndpointPlan) -> _ErrorResponse:
    return _ErrorResponse(401, "unauthorized", plan.unauthorized_detail)


def decode_sql(plan: EndpointPlan, dialect: Dialect) -> str:
    """The auth native applied to the token (NativeToken.invalid / .unknown tell an
    undecodable token from one naming no user)."""
    assert plan.auth_native is not None
    return "SELECT " + dialect.native(plan.auth_native, [dialect.positional_bind()])


def _no_caller(plan: EndpointPlan, dialect: Dialect, conn: Any, token: str) -> _ErrorResponse:
    """A token naming no caller: NativeToken.invalid when the native decodes it to null,
    NativeToken.unknown when the value matches no row -- else the 401."""
    if plan.auth_native is None or (plan.token_invalid is None and plan.token_unknown is None):
        return _unauthorized(plan)
    decoded = conn.execute(decode_sql(plan, dialect), (token,)).fetchone()[0]
    answer = plan.token_invalid if decoded is None else plan.token_unknown
    if answer is None:
        return _unauthorized(plan)
    status, detail = answer
    return _ErrorResponse(status, STATUS_CODES[status], detail)


def _error(err: ErrorSpec) -> _ErrorResponse:
    return _ErrorResponse(err.status, err.code, err.detail)


def _run_steps(
    plan: EndpointPlan,
    dialect: Dialect,
    conn: Any,
    values: Mapping[str, object],
    user_id: object,
    is_admin: bool,
    caller: tuple[Any, ...] | None = None,
    now: dt.datetime | None = None,
    pending: list[tuple[Any, ...]] | None = None,
    t2: dict[str, object] | None = None,
) -> tuple[dict[str, Result], dict[str, int]]:
    """Every step's rows, and the total of each page(total=True) read (by step id).
    Outbox rows written by for_each steps are appended to `pending` (id, native, args)
    for the dispatch after COMMIT. `t2`: the program's loaded procedures (T2 steps run
    them on the request's one fuel budget)."""
    results: dict[str, Result] = {}
    totals: dict[str, int] = {}
    caller_row: dict[str, object] | None = None
    if caller is not None:
        results[USER_PARAM] = (plan.user_columns, [caller])
        caller_row = dict(zip(plan.user_columns, caller, strict=True))
    values = dict(values)  # whole-value calls bind their scalar results here
    fuel: t2s.T2Fuel | None = None
    request: t2s.T2Request | None = None
    if plan.uses_t2:
        if t2 is None:
            raise LatticeInternalError(f"plan '{plan.name}' runs procedures, none are loaded")
        fuel = t2s.T2Fuel(plan.fuel, max_size=plan.max_value_size)
        role = caller_role(plan, caller) if caller is not None else ""
        request = t2s.T2Request(
            values, UNSET, caller_row, plan.t2_caller, plan.user_key, is_admin, role, now
        )

    def encode(v: object) -> object:
        return dialect.to_db(v)

    def write(step: WriteStep) -> list[tuple[Any, ...]]:
        """A write statement; with unique_or=Conflict(...), a UNIQUE violation answers
        the declared 409 (the transaction is rolled back by the caller)."""
        try:
            return run(step.sql)
        except Exception as err:
            if step.conflict is not None and dialect.is_unique_violation(err):
                raise _error(step.conflict) from None
            raise

    def run(sql: Any) -> list[tuple[Any, ...]]:
        binds = bind_values(
            sql,
            params=values,
            results=results,
            user_id=user_id,
            is_admin=is_admin,
            now=now,
            dialect=dialect,
            caller=caller_row,
        )
        return conn.execute(sql.text, binds).fetchall()

    for step in plan.steps:
        match step:
            case ReadStep():
                rows = run(step.sql)
                if step.sql.total:  # trailing (__in_page, __total); an empty page is 1 row
                    totals[step.id] = int(rows[0][-1]) if rows else 0
                    rows = [r[:-2] for r in rows if r[-2]]
                results[step.id] = (step.schema.names() + step.flags, rows)
            case CheckStep(kind="one"):
                n = len(results[step.input][1])
                if n == 0:
                    raise _error(step.error)
                if n > 1:
                    raise ApiRuntimeError(
                        "R310", step.span, f"one() matched {n} rows; expected exactly one"
                    )
            case CheckStep(kind="require"):
                if step.flag_step is None or step.flag is None:
                    raise LatticeInternalError(f"require '{step.id}' has no condition flags")
                flags = _project(results[step.flag_step], [step.flag])
                if any(not f[0] for f in flags):  # false or null fails
                    raise _error(step.error)
            case WriteStep(kind="insert"):
                results[step.id] = (step.schema.names(), write(step))
            case WriteStep(kind="update"):
                assert step.input is not None
                returned = write(step)
                if step.missing is not None and len(returned) < len(results[step.input][1]):
                    raise _error(step.missing)  # an owner transfer to a missing user
                k = len(step.key)
                new = {tuple(r[:k]): r[k:] for r in returned}
                names = step.schema.names()
                set_cols = [c for c, _ in step.values]
                key_idx = [names.index(c) for c in step.key]
                rows = []
                for row in _project(results[step.input], names):
                    updated = new.get(tuple(row[i] for i in key_idx))
                    if updated is None:
                        rows.append(row)
                        continue
                    cells = list(row)
                    for c, v in zip(set_cols, updated, strict=True):
                        if c in names:
                            cells[names.index(c)] = v
                    rows.append(tuple(cells))
                results[step.id] = (names, rows)
            case WriteStep(kind="delete"):
                assert step.input is not None
                run(step.sql)
                names = step.schema.names()
                results[step.id] = (names, _project(results[step.input], names))
            case ForEachStep():
                conn.execute(step.ddl)  # the outbox table, created in this transaction
                written = run(step.sql)
                if pending is not None:
                    pending.extend(written)
            case PostStep() | T2CheckStep() | CallStep():
                assert t2 is not None and fuel is not None and request is not None
                try:
                    _t2_step(step, t2, fuel, request, results, values, encode)
                except t2s.T2Error as err:
                    t2s.t2_report(f"{step.span.line}:{step.span.col}", err)
                    raise ApiRuntimeError(err.code, step.span, err.message) from None
            case _:
                raise LatticeInternalError(f"unknown step {step!r}")
    return results, totals


def _t2_step(
    step: PostStep | T2CheckStep | CallStep,
    ns: dict[str, object],
    fuel: t2s.T2Fuel,
    request: t2s.T2Request,
    results: dict[str, Result],
    values: dict[str, object],
    encode: Callable[[object], object],
) -> None:
    """One T2 step: procedures over rows already read (lattice.t2.support)."""
    match step:
        case PostStep():
            cols, rows = results[step.input]
            out = step.schema.names()
            rows = t2s.t2_post(ns, fuel, request, cols, rows, step.ops, out, encode)
            results[step.id] = (out, rows)
        case T2CheckStep():
            cols, rows = results[step.input]
            if not t2s.t2_holds(ns, fuel, request, cols, rows, step.proc, step.args):
                raise _error(step.error)
        case CallStep():
            value = t2s.t2_call(
                ns, fuel, request, step.proc, step.args, results, step.result, encode
            )
            if step.bind is not None:
                values[step.bind] = value
            else:
                results[step.id] = value  # type: ignore[assignment]


def _respond(
    plan: EndpointPlan,
    dialect: Dialect,
    results: Mapping[str, Result],
    values: Mapping[str, object],
    totals: Mapping[str, int] | None = None,
):
    if plan.success == "NoContent":
        return Response(status_code=204)
    fields = plan.response_fields
    items = [
        {c: _value(plan, dialect, v, t) for (c, t), v in zip(fields, row, strict=True)}
        for row in _project(results[plan.returns], [c for c, _ in fields])
    ]
    if plan.success == LIST_KIND:  # a JSON array of the (bounded) rows
        return JSONResponse(items)
    if plan.success == "Page":
        if plan.page is None:
            raise LatticeInternalError(f"Page endpoint '{plan.name}' returns no page()")
        limit = values[plan.page.limit] if isinstance(plan.page.limit, str) else plan.page.limit
        offset = values[plan.page.offset] if isinstance(plan.page.offset, str) else plan.page.offset
        parts: dict[str, object] = {"items": items, "limit": limit, "offset": offset}
        if plan.page.total_step is not None:
            parts["total"] = (totals or {})[plan.page.total_step]
        envelope = {name: parts[key] for key, name in plan.page_shape if name and key in parts}
        return JSONResponse(envelope)
    if not items:
        raise _ErrorResponse(404, "not_found", "no such row")
    if len(items) > 1:
        raise ApiRuntimeError(
            "R310", plan.span, f"the response has {len(items)} rows; expected exactly one"
        )
    return JSONResponse(items[0], status_code=201 if plan.success == "Created" else 200)


def _value(plan: EndpointPlan, dialect: Dialect, v: object, t: Scalar | Schema) -> object:
    """A response field's value: a column's decoded value, or a Nested field's list of
    child objects (a JSON array read in the same statement; Float values as exact text,
    `float_text`)."""
    if isinstance(t, Scalar):
        return _wire(plan, decode(v, t, dialect), t)
    rows = json.loads(v) if isinstance(v, str) else []
    return [
        {c: _wire(plan, decode(_json_cell(r[c], s), s, dialect), s) for c, s in t.columns}
        for r in rows
    ]


def _json_cell(v: object, scalar: Scalar) -> object:
    """A nested row's JSON value as the database row would hold it: a Float's exact text
    back to a float."""
    if scalar is Scalar.FLOAT and isinstance(v, str):
        return float(v)
    return v


def _wire(plan: EndpointPlan, value: object, scalar: Scalar) -> object:
    """A decoded value as the response carries it: a DateTime in the plan's wire
    spelling, a non-finite float as null."""
    if scalar is Scalar.DATETIME and isinstance(value, str) and plan.datetime_wire != "iso_z":
        return wire_datetime(value, plan.datetime_wire)
    return _finite(value)


def _finite(value: object) -> object:
    """A non-finite float (an arithmetic overflow such as 1e308 * 10) serializes as
    JSON null: JSON has no inf/NaN, and the value is not an error of the request."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _internal(style: str, detail: str) -> JSONResponse:
    return _json_error(style, 500, "internal", detail[:500])


# The 409 body of a write that hits a UNIQUE constraint (or a duplicate key). The detail
# names no table or column: the database's message is not part of the API.
UNIQUE_CONFLICT = "a row with the same unique value already exists"


def _unique_conflict(style: str) -> JSONResponse:
    return _json_error(style, 409, "conflict", UNIQUE_CONFLICT)


def _serve(
    plan: EndpointPlan,
    db_path: Path,
    path: Mapping[str, str],
    query: Mapping[str, str],
    body: bytes,
    authorization: str | None,
    natives: Mapping[str, NativeImpl] | None = None,
    content_type: str | None = None,
    effects: Mapping[str, Callable[..., object]] | None = None,
    t2: dict[str, object] | None = None,
) -> Response:
    dialect = plan_dialect(plan)
    impls = natives or {}
    pending: list[tuple[Any, ...]] = []
    now = dt.datetime.now(dt.UTC)  # fn.now(): one instant per request
    conn: Any = None
    in_txn = False
    try:
        user_id: object = None
        is_admin = False
        caller: tuple[Any, ...] | None = None
        if plan.auth != PUBLIC_ROLE:
            token = _token(authorization)
            if token is None:
                raise _unauthorized(plan)
            conn = _connect(dialect, db_path, impls)
            user_id, role, caller = _authenticate(plan, dialect, conn, token)
            is_admin = role == plan.admin_role
        values = _parse_params(plan, path, query, body, content_type)
        if conn is None:
            conn = _connect(dialect, db_path, impls)
        dialect.begin(conn, writes=plan.writes)
        in_txn = True
        results, totals = _run_steps(
            plan, dialect, conn, values, user_id, is_admin, caller, now, pending, t2
        )
        response = _respond(plan, dialect, results, values, totals)
        dialect.commit(conn)
        in_txn = False
        if pending:  # after COMMIT: the response is already decided
            _dispatch(conn, pending, effects or {})
        return response
    except _ErrorResponse as err:
        return _json_error(plan.error_body, err.status, err.code, err.detail)
    except LatticeRuntimeError as err:
        return _internal(plan.error_body, err.render())
    except Exception as err:  # loud: any unexpected failure is a 500
        if dialect.is_unique_violation(err):
            return _unique_conflict(plan.error_body)  # rolled back below
        return _internal(plan.error_body, f"{type(err).__name__}: {err}")
    finally:
        if conn is not None:
            if in_txn:
                dialect.rollback(conn)
            conn.close()


def _dispatch(
    conn: Any, pending: list[tuple[Any, ...]], effects: Mapping[str, Callable[..., object]]
) -> None:
    """Send a committed request's outbox rows; a failure is recorded in its row, never
    raised (the response does not depend on it)."""
    try:
        outbox.dispatch(conn, pending, effects)
    except Exception:  # e.g. the connection broke: a retry takes the rows later
        pass


def _handler(
    plan: EndpointPlan,
    db_path: Path,
    natives: Mapping[str, NativeImpl],
    effects: Mapping[str, Callable[..., object]] | None = None,
    t2: dict[str, object] | None = None,
):
    async def endpoint(request: Request) -> Response:
        body = await request.body()
        return await run_in_threadpool(
            _serve,
            plan,
            db_path,
            dict(request.path_params),
            dict(request.query_params),
            body,
            request.headers.get("authorization"),
            natives,
            request.headers.get("content-type"),
            effects,
            t2,
        )

    endpoint.__name__ = plan.name
    return endpoint


def _specificity(plan: EndpointPlan) -> list[int]:
    """Literal segments before parameters, so /orders/mine wins over /orders/{id}."""
    return [1 if seg.startswith("{") else 0 for seg in plan.path.split("/")]


def _http_error(style: str):
    """Routing errors (unknown path 404, wrong method 405) in the error body style:
    no detail in the "lattice" style, the status phrase in the "fastapi" style (as
    FastAPI answers them)."""

    async def handler(request: Request, exc: Exception) -> Response:
        status = exc.status_code if isinstance(exc, HTTPException) else 500
        code = {404: "not_found", 405: "method_not_allowed"}.get(status, "internal")
        return _json_error(style, status, code)

    return handler


def native_impls(
    plans: Sequence[EndpointPlan], natives: Mapping[str, Callable[..., object]] | None
) -> dict[str, NativeImpl]:
    """The natives the plans call, each with its signature and implementation; a
    native without an implementation is a ValueError (before serving anything)."""
    impls: dict[str, NativeImpl] = {}
    for plan in plans:
        for name, sig in plan.natives:
            fn = (natives or {}).get(name)
            if fn is None:
                raise ValueError(
                    f"native {name!r} has no implementation: pass natives={{{name!r}: ...}}"
                )
            impls[name] = (sig, fn)
    return impls


def effect_impls(
    plans: Sequence[EndpointPlan], natives: Mapping[str, Callable[..., object]] | None
) -> dict[str, Callable[..., object]]:
    """The effect natives the plans' for_each steps send, each with its implementation
    (from the same `natives` mapping); a missing one is a ValueError."""
    impls: dict[str, Callable[..., object]] = {}
    for plan in plans:
        for name, _ in plan.effects:
            fn = (natives or {}).get(name)
            if fn is None:
                raise ValueError(
                    f"effect native {name!r} has no implementation: pass natives={{{name!r}: ...}}"
                )
            impls[name] = fn
    return impls


def load_procedures(
    plans: Sequence[EndpointPlan], natives: Mapping[str, Callable[..., object]] | None
) -> dict[str, object] | None:
    """The program's procedures (`EndpointPlan.t2`, one module for every plan) loaded
    in their restricted namespace, with the natives they call; None without any. A
    native without an implementation is a ValueError."""
    codes = {plan.t2.source: plan.t2 for plan in plans if plan.t2 is not None}
    if not codes:
        return None
    if len(codes) > 1:
        raise ValueError("the plans come from different programs (their procedures differ)")
    (code,) = codes.values()
    impls: dict[str, object] = {}
    for name in code.natives:
        fn = (natives or {}).get(name)
        if fn is None:
            raise ValueError(
                f"native {name!r} (called by a procedure) has no implementation: pass "
                f"natives={{{name!r}: ...}}"
            )
        impls[name] = fn
    return t2s.t2_load(code.source, impls)


def bind_native_settings(
    plans: Sequence[EndpointPlan],
    natives: Mapping[str, Callable[..., object]] | None,
    settings: Mapping[str, object] | None,
) -> dict[str, Callable[..., object]]:
    """`natives` with each one the plans use (in SQL, as an effect or from a procedure)
    that reads settings bound to its NativeContext (lattice.api.native_context); a
    missing or mistyped setting is a ValueError."""
    used = {n for plan in plans for n, _ in (*plan.natives, *plan.effects)}
    used |= {n for plan in plans if plan.t2 is not None for n in plan.t2.natives}
    declared = {name: wanted for plan in plans for name, wanted in plan.native_settings}
    wanted = tuple((n, w) for n, w in sorted(declared.items()) if n in used)
    bound = bind_settings(dict(natives or {}), wanted, settings or {})
    return bound  # type: ignore[return-value]


def build_app(
    plans: Sequence[EndpointPlan],
    db_path: Path,
    natives: Mapping[str, Callable[..., object]] | None = None,
    settings: Mapping[str, object] | None = None,
) -> Starlette:
    """A Starlette app serving `plans` against the database at `db_path` (each plan's
    backend dialect opens it). `natives` maps each native the plans call to its
    Python implementation (registered as a SQL function on every connection);
    `settings` holds the values of the profile's settings the natives read (each such
    native is called with its read-only NativeContext first).

    Routing is Starlette's (FastAPI's): a path is matched exactly as declared, and a
    request that differs only by a trailing slash is redirected (307) to it."""
    db = Path(db_path)
    natives = bind_native_settings(plans, natives, settings)
    impls = native_impls(plans, natives)
    effects = effect_impls(plans, natives)
    t2 = load_procedures(plans, natives)
    if t2 is not None:
        for plan in plans:
            for name, params in plan.t2_effects:
                budget = plan.fuel
                effects[T2_EFFECT_PREFIX + name] = t2s.t2_effect(
                    t2, budget, name, params, plan.max_value_size
                )
    routes = [
        Route(
            plan.path,
            _handler(plan, db, impls, effects, t2),
            methods=[plan.method],
            name=plan.name,
        )
        for plan in sorted(plans, key=_specificity)
    ]
    styles = {plan.error_body for plan in plans}
    if len(styles) > 1:
        raise ValueError(f"the plans disagree on the error body style: {sorted(styles)}")
    style = styles.pop() if styles else "lattice"
    return Starlette(routes=routes, exception_handlers={HTTPException: _http_error(style)})


def serve_source(
    source: str,
    profile: Profile,
    db_path: Path,
    natives: Mapping[str, Callable[..., object]] | None = None,
    settings: Mapping[str, object] | None = None,
) -> tuple[CheckResult, Starlette | None]:
    """Check -> lower -> build. (check result, None) when the program is rejected."""
    result = check_source(source, profile)
    if result.program is None:
        return result, None
    return result, build_app(lower_api(result.program), db_path, natives, settings)
