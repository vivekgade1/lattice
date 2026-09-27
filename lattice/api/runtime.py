"""The Lattice API runtime (Phase A): serve lowered endpoint plans over a SQL backend.

Per request: authenticate (`Authorization: Bearer <token>`, one statement), check the
role, parse and validate the parameters, then run the plan's steps in order inside
one transaction — each read pipeline is one SELECT, each write one statement, checks
cost nothing — and serialize the returned rows. Any error response rolls back.

A new connection is opened per request and closed after it (no connection is held
between requests). Every new connection is passed to the module attribute
`on_connect` (a no-op by default) — the benchmark harness replaces it to count
statements.

Everything backend-specific (opening a connection, registering the functions the SQL
calls, starting/ending transactions, value decoding) is delegated to the plan's
`lattice.api.backend.Dialect` (`EndpointPlan.backend`; SQLite by default).
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from lattice.api.backend import Dialect, dialect_for
from lattice.api.ir import (
    CheckStep,
    EndpointPlan,
    ErrorSpec,
    ReadStep,
    WriteStep,
    lower_api,
)
from lattice.api.sql import bind_values, compile_lookup, decode
from lattice.catalog import PUBLIC_ROLE, USER_PARAM
from lattice.check import CheckResult, check_source
from lattice.check.api_info import MISSING, ParamSpec
from lattice.profile import Profile
from lattice.runtime import LatticeInternalError, LatticeRuntimeError
from lattice.typesys import Scalar

__all__ = ["ApiRuntimeError", "build_app", "on_connect", "serve_source"]


def on_connect(conn: Any) -> None:
    """Called with every new database connection (replaceable module attribute)."""


class ApiRuntimeError(LatticeRuntimeError):
    """A data-dependent failure while serving a request (e.g. R310: one() matched
    more than one row). Responds 500; `.render()` gives the located message."""


class _ErrorResponse(Exception):
    def __init__(self, status: int, body: Mapping[str, object]) -> None:
        super().__init__(status)
        self.status = status
        self.body = body


def _connect(dialect: Dialect, db_path: Path) -> Any:
    # on_connect is looked up at call time: the harness may replace it
    return dialect.connect(db_path, lambda conn: on_connect(conn))


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


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _parse_params(
    plan: EndpointPlan,
    path: Mapping[str, str],
    query: Mapping[str, str],
    body: bytes,
) -> dict[str, object]:
    detail: list[dict[str, object]] = []
    values: dict[str, object] = {}
    data: Mapping[str, object] = {}
    if any(p.source == "body" for p in plan.params):
        parsed: object = {}
        if body.strip():
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = None
        if not isinstance(parsed, dict):
            detail.append({"loc": ["body"], "msg": "the body must be a JSON object"})
            raise _ErrorResponse(422, {"error": "validation", "detail": detail})
        data = parsed
    for spec in plan.params:
        if spec.source == "user":
            continue
        source: Mapping[str, object] = {"path": path, "query": query, "body": data}[spec.source]
        loc = [spec.source, spec.name]
        if spec.name not in source or source[spec.name] is None:
            if spec.default is not MISSING:
                values[spec.name] = spec.default
            else:
                detail.append({"loc": loc, "msg": "field required"})
            continue
        try:
            value = _convert(spec, source[spec.name], text=spec.source != "body")
            _constrain(spec, value)
        except ValueError as err:
            detail.append({"loc": loc, "msg": str(err)})
            continue
        values[spec.name] = value
    if detail:
        raise _ErrorResponse(422, {"error": "validation", "detail": detail})
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


def _authenticate(
    plan: EndpointPlan, dialect: Dialect, conn: Any, token: str
) -> tuple[object, str, tuple[Any, ...]]:
    """(caller id, role, the caller's row as `plan.user_columns`): one statement."""
    sql = compile_lookup(plan.users_table, plan.user_columns, "token", dialect=dialect)
    row = conn.execute(sql, (token,)).fetchone()
    if row is None:
        raise _ErrorResponse(401, {"error": "unauthorized"})
    user_id = row[plan.user_columns.index(plan.user_key)]
    role = str(row[plan.user_columns.index("role")])
    if not (plan.auth == PUBLIC_ROLE or role == plan.auth or role == plan.admin_role):
        raise _ErrorResponse(403, {"error": "forbidden"})
    return user_id, role, tuple(row)


# ---- steps ---------------------------------------------------------------------------------

Result = tuple[tuple[str, ...], list[tuple[Any, ...]]]


def _project(result: Result, columns: Sequence[str]) -> list[tuple[Any, ...]]:
    cols, rows = result
    index = [cols.index(c) for c in columns]
    return [tuple(r[i] for i in index) for r in rows]


def _error(err: ErrorSpec) -> _ErrorResponse:
    return _ErrorResponse(err.status, {"error": err.code, "detail": err.detail})


def _run_steps(
    plan: EndpointPlan,
    dialect: Dialect,
    conn: Any,
    values: Mapping[str, object],
    user_id: object,
    is_admin: bool,
    caller: tuple[Any, ...] | None = None,
) -> dict[str, Result]:
    results: dict[str, Result] = {}
    if caller is not None:
        results[USER_PARAM] = (plan.user_columns, [caller])

    def run(sql: Any) -> list[tuple[Any, ...]]:
        binds = bind_values(
            sql,
            params=values,
            results=results,
            user_id=user_id,
            is_admin=is_admin,
            dialect=dialect,
        )
        return conn.execute(sql.text, binds).fetchall()

    for step in plan.steps:
        match step:
            case ReadStep():
                results[step.id] = (step.schema.names() + step.flags, run(step.sql))
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
                results[step.id] = (step.schema.names(), run(step.sql))
            case WriteStep(kind="update"):
                assert step.input is not None
                returned = run(step.sql)
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
            case _:
                raise LatticeInternalError(f"unknown step {step!r}")
    return results


def _respond(
    plan: EndpointPlan,
    dialect: Dialect,
    results: Mapping[str, Result],
    values: Mapping[str, object],
):
    if plan.success == "NoContent":
        return Response(status_code=204)
    names = plan.schema.names()
    types = dict(plan.schema.columns)
    items = [
        {c: _finite(decode(v, types[c], dialect)) for c, v in zip(names, row, strict=True)}
        for row in _project(results[plan.returns], names)
    ]
    if plan.success == "Page":
        if plan.page is None:
            raise LatticeInternalError(f"Page endpoint '{plan.name}' returns no page()")
        limit = values[plan.page.limit] if isinstance(plan.page.limit, str) else plan.page.limit
        offset = values[plan.page.offset] if isinstance(plan.page.offset, str) else plan.page.offset
        return JSONResponse({"items": items, "limit": limit, "offset": offset})
    if not items:
        raise _ErrorResponse(404, {"error": "not_found", "detail": "no such row"})
    if len(items) > 1:
        raise ApiRuntimeError(
            "R310", plan.span, f"the response has {len(items)} rows; expected exactly one"
        )
    return JSONResponse(items[0], status_code=201 if plan.success == "Created" else 200)


def _finite(value: object) -> object:
    """A non-finite float (an arithmetic overflow such as 1e308 * 10) serializes as
    JSON null: JSON has no inf/NaN, and the value is not an error of the request."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _internal(detail: str) -> JSONResponse:
    return JSONResponse({"error": "internal", "detail": detail[:500]}, status_code=500)


def _serve(
    plan: EndpointPlan,
    db_path: Path,
    path: Mapping[str, str],
    query: Mapping[str, str],
    body: bytes,
    authorization: str | None,
) -> Response:
    dialect = dialect_for(plan.backend)
    conn: Any = None
    in_txn = False
    try:
        user_id: object = None
        is_admin = False
        caller: tuple[Any, ...] | None = None
        if plan.auth != PUBLIC_ROLE:
            token = _token(authorization)
            if token is None:
                raise _ErrorResponse(401, {"error": "unauthorized"})
            conn = _connect(dialect, db_path)
            user_id, role, caller = _authenticate(plan, dialect, conn, token)
            is_admin = role == plan.admin_role
        values = _parse_params(plan, path, query, body)
        if conn is None:
            conn = _connect(dialect, db_path)
        dialect.begin(conn, writes=any(isinstance(s, WriteStep) for s in plan.steps))
        in_txn = True
        results = _run_steps(plan, dialect, conn, values, user_id, is_admin, caller)
        response = _respond(plan, dialect, results, values)
        dialect.commit(conn)
        in_txn = False
        return response
    except _ErrorResponse as err:
        return JSONResponse(dict(err.body), status_code=err.status)
    except LatticeRuntimeError as err:
        return _internal(err.render())
    except Exception as err:  # loud: any unexpected failure is a 500
        return _internal(f"{type(err).__name__}: {err}")
    finally:
        if conn is not None:
            if in_txn:
                dialect.rollback(conn)
            conn.close()


def _handler(plan: EndpointPlan, db_path: Path):
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
        )

    endpoint.__name__ = plan.name
    return endpoint


def _specificity(plan: EndpointPlan) -> list[int]:
    """Literal segments before parameters, so /orders/mine wins over /orders/{id}."""
    return [1 if seg.startswith("{") else 0 for seg in plan.path.split("/")]


async def _http_error(request: Request, exc: Exception) -> Response:
    status = exc.status_code if isinstance(exc, HTTPException) else 500
    code = {404: "not_found", 405: "method_not_allowed"}.get(status, "internal")
    return JSONResponse({"error": code}, status_code=status)


def build_app(plans: Sequence[EndpointPlan], db_path: Path) -> Starlette:
    """A Starlette app serving `plans` against the database at `db_path` (each plan's
    backend dialect opens it)."""
    db = Path(db_path)
    routes = [
        Route(plan.path, _handler(plan, db), methods=[plan.method], name=plan.name)
        for plan in sorted(plans, key=_specificity)
    ]
    return Starlette(routes=routes, exception_handlers={HTTPException: _http_error})


def serve_source(
    source: str, profile: Profile, db_path: Path
) -> tuple[CheckResult, Starlette | None]:
    """Check -> lower -> build. (check result, None) when the program is rejected."""
    result = check_source(source, profile)
    if result.program is None:
        return result, None
    return result, build_app(lower_api(result.program), db_path)
