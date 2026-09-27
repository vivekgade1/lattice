"""Phase B of the API profile: compile lowered endpoint plans to a standalone FastAPI app.

`generate_fastapi(plans)` emits the source of an `app.py` that imports only the standard
library, FastAPI and the benchmark's `platform_kit` (`app`, `get_db`), and serves the
same endpoints as the Phase A runtime (`lattice.api.runtime`) with identical observable
behaviour: statuses, JSON bodies, database effects and the number of SQL statements.

Design of the generated module:

* A fixed prelude — a line-for-line port of the runtime's request pipeline (parameter
  conversion and validation, bearer-token authentication, the per-request transaction,
  the step helpers, response serialization and the four SQLite functions the compiled
  SQL calls).
* Per endpoint: its parameter specs, one module constant per statement holding the
  plan's precompiled SQL text verbatim, a steps function that executes the plan's steps
  in order (every value reaches SQLite as a named bind parameter), and a route decorated
  on the kit's `app`. Routes are registered in the runtime's order (literal path
  segments before parameters).

Authentication is generated inline rather than through the kit's `current_user` /
`require_role` dependencies, because the kit differs from the runtime observably:

1. The kit accepts only the exact prefix `"Bearer "`; the runtime strips the header and
   matches the scheme case-insensitively (`bearer x` and ` Bearer x` authenticate).
2. The kit selects the fixed columns `id, role, email, name`; the runtime reads the
   users table's full row, which a plan binds as the caller's rows (`user`). Reusing the
   kit would need a second SELECT, changing the statement count.
3. The kit resolves the caller on a separate connection opened by its own `get_db`
   dependency (never closed) and before the handler runs; the runtime authenticates on
   the request's single connection, and a missing/malformed header is rejected with 401
   before any connection is opened.

Real-app features are generated the runtime's way: Uuid/DateTime parameters, fn.now()
(one instant per request, bound as `:__now`), UNSET parameters (a "sent" flag bound per
optional parameter), page totals (`split_total`) in the profile's page shape, response
models (the plan's schema is the model's fields in order), the role from a role column
or an Admin flag column, and natives: `generate_fastapi(..., natives_module="natives")`
imports the natives the plans call from that module (which must sit next to the app)
and registers them, like the runtime's functions, on every connection (NativeToken auth
decodes the bearer token with one of them). The profile's Uuid/DateTime storage
spellings become the prelude constants `UUID_STORAGE` / `DATETIME_STORAGE`, and a
`body="form"` endpoint reads URL-encoded form fields (`Endpoint.form`), as the runtime
does.

Effect natives (`for_each`, sub-project 1b) are imported from the natives module too,
into `EFFECTS` (never registered as SQL functions). A for_each step creates the outbox
table when missing and INSERTs its rows in the request's transaction (the plan's SQL,
verbatim); after COMMIT `serve` dispatches them in order with `dispatch_outbox` (a port
of `lattice.api.outbox.dispatch`: claim the row, send it, then mark it done or failed
with the error, trying a failing mark again and then logging it; the response is
already decided), and `retry_outbox()` re-sends failed rows and pending / in-flight
rows older than the grace period from the app itself.

The profile-wide Part B settings (sub-project 1b) become prelude constants as well:
`ERROR_BODY` with `UNAUTHORIZED_DETAIL`/`FORBIDDEN_DETAIL` (the error body style and the
automatic 401/403 texts), `DATETIME_WIRE` (how responses spell a DateTime), and
`ACTIVE_COLUMN`/`INACTIVE_STATUS`/`INACTIVE_DETAIL` (the active check in auth). A write
with `unique_or=Conflict(...)` runs through `write(...)`, which answers the declared 409
on a UNIQUE violation. Every error body, the precedence (401, then the inactive status,
then 403, then 422 validation) and the Admin rule are the runtime's.
Every connection still comes from the kit's `get_db`, so the harness's statement
counting applies identically.
"""

from __future__ import annotations

import ast
import builtins
import datetime as dt
import keyword
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from lattice.api.backend import SQLITE
from lattice.api.ir import (
    CheckStep,
    EndpointPlan,
    ErrorSpec,
    ForEachStep,
    ReadStep,
    WriteStep,
    lower_api,
)
from lattice.api.outbox import (
    ADD_CLAIMED_SQL,
    CLAIM_SQL,
    COLUMNS_SQL,
    CREATE_SQL,
    ERROR_LIMIT,
    EXISTS_SQL,
    GRACE_SECONDS,
    MARK_BACKOFF,
    MARK_DONE_SQL,
    MARK_FAILED_SQL,
    MARK_TRIES,
    RETRY_PENDING_SQL,
    RETRY_SQL,
)
from lattice.api.runtime import auth_sql
from lattice.api.sql import IS_ADMIN, NOW, USER_ID, SqlQuery, sent_bind
from lattice.catalog import USER_PARAM
from lattice.check import CheckResult, check_source
from lattice.check.api_info import MISSING, UNSET, ParamSpec
from lattice.profile import NativeSig, Profile
from lattice.runtime import LatticeInternalError

__all__ = ["compile_source_to_fastapi", "generate_fastapi"]

_METHODS = {"GET": "get", "POST": "post", "PUT": "put", "PATCH": "patch", "DELETE": "delete"}

_PRELUDE = '''\
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import re
import sqlite3
import time
import urllib.parse
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, NamedTuple

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from platform_kit import app, get_db

# ======================================================================================
# Shared request pipeline (a port of lattice/api/runtime.py)
# ======================================================================================


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()  # a parameter without a default value


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()  # an optional parameter that was not sent


@dataclass(frozen=True)
class Param:
    """One endpoint parameter; `scalar` is Int | Float | Str | Bool | Date | DateTime |
    Uuid. `optional`: its default is UNSET (not sent: the column it updates is kept);
    `nullable`: an explicit JSON null for it is accepted (sets NULL)."""

    name: str
    source: str  # "path" | "query" | "body"
    scalar: str
    ge: float | None = None
    le: float | None = None
    min_len: int | None = None
    max_len: int | None = None
    one_of: tuple[str, ...] | None = None
    default: object = MISSING
    optional: bool = False
    nullable: bool = False
    pattern: str | None = None  # a regular expression the whole Str value must match
    email: bool = False  # the pattern is Email's (only the message differs)


@dataclass(frozen=True)
class Endpoint:
    name: str
    auth: str  # the role allowed to call it ("Public": anyone, without a token)
    params: tuple[Param, ...]
    writes: bool  # BEGIN IMMEDIATE when the endpoint writes, else BEGIN
    auth_sql: str  # the caller's row by token
    user_columns: tuple[str, ...]
    user_key: str
    admin_role: str
    role_column: str = "role"  # the caller's role column, unless admin_flag is set
    admin_flag: str | None = None  # a Bool column: true -> admin_role, else flag_role
    flag_role: str = ""
    form: bool = False  # body parameters are URL-encoded form fields, not a JSON object
    effects: bool = False  # for_each: the steps collect outbox rows, sent after COMMIT


class Caller(NamedTuple):
    id: object
    is_admin: int  # bound as :__is_admin
    row: tuple[Any, ...]  # the caller's row (Endpoint.user_columns)


Result = tuple[tuple[str, ...], list[tuple[Any, ...]]]  # (columns, rows)


class ErrorResponse(Exception):
    """An error response: status, JSON error code and detail (None: no detail),
    rendered in the ERROR_BODY style."""

    def __init__(self, status: int, code: str, detail: object = None) -> None:
        super().__init__(status)
        self.status = status
        self.code = code
        self.detail = detail


class Failure(Exception):
    """A data-dependent failure (one() matched several rows): 500 with the message."""


def error(status: int, code: str, detail: str) -> ErrorResponse:
    return ErrorResponse(status, code, detail)


# The error body style: "lattice" ({"error": code} plus "detail" when there is one) or
# "fastapi" ({"detail": ...}, the HTTP status phrase when there is no detail); the
# automatic 401/403 details (None: none in the "lattice" style).
ERROR_BODY = "lattice"
UNAUTHORIZED_DETAIL: str | None = None
FORBIDDEN_DETAIL: str | None = None
# Auth refuses a caller whose ACTIVE_COLUMN (a Bool column of the users table) is not
# true with INACTIVE_STATUS / INACTIVE_DETAIL (None: no active check).
ACTIVE_COLUMN: str | None = None
INACTIVE_STATUS = 400
INACTIVE_DETAIL = "Inactive user"
STATUS_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden"}


def error_body(status: int, code: str, detail: object = None) -> dict[str, object]:
    if ERROR_BODY == "fastapi":
        return {"detail": HTTPStatus(status).phrase if detail is None else detail}
    body: dict[str, object] = {"error": code}
    if detail is not None:
        body["detail"] = detail
    return body


def json_error(status: int, code: str, detail: object = None) -> JSONResponse:
    return JSONResponse(error_body(status, code, detail), status_code=status)


# ---- SQLite functions the compiled SQL calls ------------------------------------------


def _lower(x: object) -> object:
    return x.lower() if isinstance(x, str) else x


def _upper(x: object) -> object:
    return x.upper() if isinstance(x, str) else x


def _strip(x: object) -> object:
    return x.strip() if isinstance(x, str) else x


def _mod(a: object, b: object) -> object:
    if a is None or b is None or b == 0:
        return None
    return a % b  # type: ignore[operator]


# Uuid values are stored as canonical lowercase hyphenated text ("canonical") or 32 hex
# digits ("hex32"); DateTime values as fixed-width UTC text with microseconds, ISO-8601
# with a "Z" ("iso_z") or "YYYY-MM-DD HH:MM:SS.ffffff" ("sql_space"): text order is
# time order. Responses always carry the canonical spellings.
UUID_STORAGE = "canonical"
DATETIME_STORAGE = "iso_z"
_UUID_TEXT = re.compile(
    r"[0-9a-fA-F]{8}(-?)[0-9a-fA-F]{4}\\1[0-9a-fA-F]{4}\\1[0-9a-fA-F]{4}\\1[0-9a-fA-F]{12}"
)


def uuid_text(text: object) -> str | None:
    """The canonical text of a UUID written hyphenated or as 32 hex digits, or None."""
    if isinstance(text, uuid.UUID):
        return str(text)
    if not isinstance(text, str) or not _UUID_TEXT.fullmatch(text):
        return None
    return str(uuid.UUID(text))


def parse_datetime(text: object) -> dt.datetime | None:
    """An ISO-8601 date-time (a naive one is UTC) as an aware UTC datetime, or None."""
    if isinstance(text, dt.datetime):
        value = text
    elif isinstance(text, str):
        try:
            value = dt.datetime.fromisoformat(text)
        except (ValueError, OverflowError):
            return None
    else:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    try:
        return value.astimezone(dt.UTC)
    except (ValueError, OverflowError):  # e.g. 0001-01-01T00:00:00+01:00 is before year 1
        return None


def datetime_text(value: dt.datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(dt.UTC)
    return value.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


# How responses spell a DateTime: "iso_z" (datetime_text) or "naive" (the UTC value
# without a zone, as pydantic serializes a naive datetime).
DATETIME_WIRE = "iso_z"


def wire_datetime(value: dt.datetime) -> str:
    if DATETIME_WIRE == "naive":
        return value.astimezone(dt.UTC).replace(tzinfo=None).isoformat()
    return datetime_text(value)


def stored_uuid(value: object) -> str | None:
    """A UUID as UUID_STORAGE keeps it, or None."""
    text = uuid_text(value)
    if text is None or UUID_STORAGE == "canonical":
        return text
    return text.replace("-", "")


def stored_datetime(value: dt.datetime) -> str:
    """A DateTime as DATETIME_STORAGE keeps it."""
    if DATETIME_STORAGE == "iso_z":
        return datetime_text(value)
    if value.tzinfo is not None:
        value = value.astimezone(dt.UTC)
    return value.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


def _to_uuid(x: object) -> object:
    return stored_uuid(x)


def _to_datetime(x: object, fmt: object) -> object:
    if not isinstance(x, str) or not isinstance(fmt, str):
        return None
    try:
        value = dt.datetime.strptime(x, fmt)
    except ValueError:
        return None
    return stored_datetime(value)


def _uuid4() -> str:
    return stored_uuid(uuid.uuid4()) or ""


# ---- natives: pure Python functions callable from the SQL ---------------------------------


@dataclass(frozen=True)
class Native:
    params: tuple[str, ...]  # scalar names
    result: str
    nullable: bool  # the result may be null
    fn: Callable[..., object]


NATIVES: dict[str, Native] = {}  # filled below when the program calls natives


def _native_arg(v: object, scalar: str) -> object:
    """A stored value as the native receives it (Date/DateTime/Uuid as Python objects)."""
    if scalar == "Bool":
        return bool(v)
    if scalar == "Int":
        return int(v)  # type: ignore[call-overload]
    if scalar == "Float":
        return float(v)  # type: ignore[arg-type]
    if scalar == "Date":
        return dt.date.fromisoformat(str(v))
    if scalar == "DateTime":
        return parse_datetime(v)
    if scalar == "Uuid":
        return uuid.UUID(str(v))
    return v


def _native_result(name: str, v: object, scalar: str) -> object:
    """A native's result as stored (a wrong type is an error)."""
    bad = TypeError(f"native {name} returned {type(v).__name__}, not {scalar}")
    if scalar == "Bool" and isinstance(v, bool):
        return int(v)
    if scalar == "Int" and isinstance(v, int) and not isinstance(v, bool):
        return v
    if scalar == "Float" and isinstance(v, int | float) and not isinstance(v, bool):
        return float(v)
    if scalar == "Str" and isinstance(v, str):
        return v
    if scalar == "Date" and isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return v.isoformat()
    if scalar == "DateTime" and isinstance(v, dt.datetime | str):
        parsed = parse_datetime(v)
        if parsed is None:
            raise bad
        return stored_datetime(parsed)
    if scalar == "Uuid" and isinstance(v, uuid.UUID | str):
        text = stored_uuid(v)
        if text is None:
            raise bad
        return text
    raise bad


def _native_function(name: str, native: Native) -> Callable[..., object]:
    """A null argument gives null; a null result only for a nullable native."""

    def call(*args: object) -> object:
        if any(a is None for a in args):
            return None
        converted = (_native_arg(a, s) for a, s in zip(args, native.params, strict=True))
        out = native.fn(*converted)
        if out is None:
            if native.nullable:
                return None
            raise ValueError(f"native {name} returned null")
        return _native_result(name, out, native.result)

    return call


# ---- the outbox (for_each; a port of lattice/api/outbox.py) ------------------------------

# Effect natives: called once per outbox row after COMMIT, never inside SQL.
EFFECTS: dict[str, Callable[..., object]] = {}  # filled below when the program sends any
OUTBOX_DDL = ""
OUTBOX_COLUMNS = ""
OUTBOX_ADD_CLAIMED = ""
OUTBOX_CLAIM = ""
OUTBOX_DONE = ""
OUTBOX_FAILED = ""
OUTBOX_EXISTS = ""
OUTBOX_RETRY = ""
OUTBOX_RETRY_PENDING = ""
OUTBOX_ERROR_LIMIT = 500
OUTBOX_GRACE = 300  # seconds: a younger claim (or unclaimed row) may still be in flight
OUTBOX_MARK_TRIES = 3  # a failing done / failed mark is tried this often, then logged
OUTBOX_MARK_BACKOFF = 0.05  # seconds before the 2nd try (doubling)
outbox_log = logging.getLogger("lattice.outbox")


def _outbox_args(args: str) -> list[object]:
    """The Python arguments of an outbox row ({"types": [...], "values": [...]})."""
    data = json.loads(args)
    return [
        None if v is None else _native_arg(v, t)
        for t, v in zip(data["types"], data["values"], strict=True)
    ]


def _outbox_claimed_at(conn: sqlite3.Connection) -> None:
    """Add the claimed_at column to an outbox table created without it."""
    if "claimed_at" in {row[0] for row in conn.execute(OUTBOX_COLUMNS).fetchall()}:
        return
    try:
        conn.execute(OUTBOX_ADD_CLAIMED)
    except sqlite3.OperationalError as err:
        if "duplicate column" not in str(err):  # another process added it first
            raise


def _outbox_cutoff(now: dt.datetime, grace: float) -> str:
    return (now - dt.timedelta(seconds=grace)).isoformat()


def _outbox_mark(
    conn: sqlite3.Connection, sql: str, params: tuple[Any, ...], row_id: object
) -> None:
    """A done / failed mark, tried again briefly; still failing, it is logged (the row
    stays claimed, and a retry re-sends it after the grace period)."""
    for attempt in range(OUTBOX_MARK_TRIES):
        try:
            conn.execute(sql, params)
            return
        except sqlite3.Error as err:
            if attempt + 1 == OUTBOX_MARK_TRIES:
                outbox_log.warning(
                    "could not mark outbox row %s (%s: %s); a retry re-sends it after "
                    "the grace period",
                    row_id,
                    type(err).__name__,
                    err,
                )
                return
            time.sleep(OUTBOX_MARK_BACKOFF * 2**attempt)


def dispatch_outbox(
    conn: sqlite3.Connection,
    rows: Sequence[Sequence[Any]],
    now: dt.datetime | None = None,
    grace: float = OUTBOX_GRACE,
) -> tuple[int, int]:
    """Claim each (id, native, args) row, in id order, then call its effect native and
    mark the row done or failed with the error. A row another dispatcher holds is
    skipped. Never raises for a failing native or mark. Returns (done, failed)."""
    done = failed = 0
    if not rows:
        return done, failed
    _outbox_claimed_at(conn)
    for row_id, native, args in sorted(rows, key=lambda r: r[0]):
        stamp = now if now is not None else dt.datetime.now(dt.UTC)
        claimed_at = datetime_text(stamp)
        try:
            claim = (claimed_at, row_id, _outbox_cutoff(stamp, grace))
            claimed = conn.execute(OUTBOX_CLAIM, claim)
        except sqlite3.Error as err:  # not claimed, not sent: a retry takes it later
            outbox_log.warning(
                "could not claim outbox row %s (%s: %s)", row_id, type(err).__name__, err
            )
            continue
        if claimed.rowcount != 1:
            continue  # another dispatcher holds it, or it is done
        fn = EFFECTS.get(native)
        error: str | None = None
        if fn is None:
            error = f"no implementation for native {native!r}"
        else:
            try:
                fn(*_outbox_args(args))
            except Exception as err:  # the effect failed: recorded, retried later
                error = f"{type(err).__name__}: {err}"[:OUTBOX_ERROR_LIMIT]
        if error is None:
            _outbox_mark(conn, OUTBOX_DONE, (row_id, claimed_at), row_id)
            done += 1
        else:
            _outbox_mark(conn, OUTBOX_FAILED, (error, row_id, claimed_at), row_id)
            failed += 1
    return done, failed


def retry_outbox(
    pending: bool = False, grace: float = OUTBOX_GRACE, now: dt.datetime | None = None
) -> tuple[int, int]:
    """Re-send, in id order, the failed outbox rows and the pending / dispatching rows
    claimed (or created) more than `grace` seconds ago; with `pending`, every pending
    row. Returns (done, failed)."""
    now = now if now is not None else dt.datetime.now(dt.UTC)
    conn = connect()
    try:
        if conn.execute(OUTBOX_EXISTS).fetchone() is None:
            return 0, 0
        _outbox_claimed_at(conn)
        sql = OUTBOX_RETRY_PENDING if pending else OUTBOX_RETRY
        rows = conn.execute(sql, (_outbox_cutoff(now, grace),)).fetchall()
        return dispatch_outbox(conn, rows, now, grace)
    finally:
        conn.close()


def connect() -> sqlite3.Connection:
    """The kit's connection, in autocommit mode (transactions are explicit)."""
    conn = get_db()
    conn.row_factory = None
    conn.isolation_level = None
    conn.create_function("lattice_lower", 1, _lower, deterministic=True)
    conn.create_function("lattice_upper", 1, _upper, deterministic=True)
    conn.create_function("lattice_strip", 1, _strip, deterministic=True)
    conn.create_function("lattice_mod", 2, _mod, deterministic=True)
    conn.create_function("lattice_to_uuid", 1, _to_uuid, deterministic=True)
    conn.create_function("lattice_to_datetime", 2, _to_datetime, deterministic=True)
    conn.create_function("lattice_uuid4", 0, _uuid4)  # not deterministic
    for name, native in NATIVES.items():
        conn.create_function(
            f"native_{name}",
            len(native.params),
            _native_function(name, native),
            deterministic=True,
        )
    return conn


# ---- parameters -------------------------------------------------------------------------

# ASCII digits only; a float also allows a fraction and an exponent (no inf/nan/_).
_INT = re.compile(r"[+-]?[0-9]+", re.ASCII)
_FLOAT = re.compile(r"[+-]?(?:[0-9]+\\.?[0-9]*|\\.[0-9]+)(?:[eE][+-]?[0-9]+)?", re.ASCII)
# SQLite INTEGER is a signed 64-bit value: an int outside it cannot be bound.
INT_MIN = -(2**63)
INT_MAX = 2**63 - 1
_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})
_TYPE_NAMES = {
    "Int": "an integer",
    "Float": "a number",
    "Str": "a string",
    "Bool": "a boolean",
    "Date": "an ISO date (YYYY-MM-DD)",
    "DateTime": "an ISO date-time",
    "Uuid": "a UUID",
}


def _convert(spec: Param, raw: object, *, text: bool) -> object:
    """The typed value of one parameter, or raise ValueError(message). `text`: the raw
    value comes from the path or the query string (always a string)."""
    bad = ValueError(f"must be {_TYPE_NAMES[spec.scalar]}")
    if spec.scalar == "Int":
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
    if spec.scalar == "Float":
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
    if spec.scalar == "Str":
        if isinstance(raw, str):
            try:
                raw.encode("utf-8")
            except UnicodeEncodeError:  # a lone surrogate from a JSON body
                raise ValueError("must be valid UTF-8 text") from None
            return raw
        raise bad
    if spec.scalar == "Bool":
        if text and isinstance(raw, str) and raw.lower() in _TRUE | _FALSE:
            return raw.lower() in _TRUE
        if not text and isinstance(raw, bool):
            return raw
        raise bad
    if spec.scalar == "DateTime":  # a naive date-time is UTC
        value_dt = parse_datetime(raw) if isinstance(raw, str) else None
        if value_dt is None:
            raise bad
        return value_dt
    if spec.scalar == "Uuid":  # hyphenated or 32 hex digits, any case
        text_uuid = uuid_text(raw) if isinstance(raw, str) else None
        if text_uuid is None:
            raise bad
        return uuid.UUID(text_uuid)  # bound in UUID_STORAGE
    if isinstance(raw, str):  # Date
        try:
            return dt.date.fromisoformat(raw)
        except ValueError:
            raise bad from None
    raise bad


def _out_of_range() -> ValueError:
    return ValueError(f"must be an integer between {INT_MIN} and {INT_MAX}")


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _constrain(spec: Param, value: object) -> None:
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
        if spec.pattern is not None and not re.fullmatch(spec.pattern, value):
            if spec.email:
                raise ValueError("must be an email address")
            raise ValueError(f"must match the pattern {spec.pattern!r}")


def _default(spec: Param) -> object:
    """A parameter's default value (a Uuid default as a uuid.UUID, like a sent one)."""
    if spec.scalar == "Uuid" and isinstance(spec.default, str):
        return uuid.UUID(spec.default)
    return spec.default


def form_fields(body: bytes, content_type: str | None) -> dict[str, str]:
    """A URL-encoded form body's fields (a repeated field keeps its last value); any
    other content type has no fields."""
    media = (content_type or "").partition(";")[0].strip().lower()
    if media != "application/x-www-form-urlencoded":
        return {}
    return dict(urllib.parse.parse_qsl(body.decode("latin-1"), keep_blank_values=True))


def parse_params(
    params: Sequence[Param],
    path: Mapping[str, str],
    query: Mapping[str, str],
    body: bytes,
    form: bool = False,
    content_type: str | None = None,
) -> dict[str, object]:
    """Every parameter converted and validated; else 422 listing each problem in order."""
    detail: list[dict[str, object]] = []
    values: dict[str, object] = {}
    data: Mapping[str, object] = {}
    if form:
        data = form_fields(body, content_type)
    elif any(p.source == "body" for p in params):
        parsed: object = {}
        if body.strip():
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = None
        if not isinstance(parsed, dict):
            detail.append({"loc": ["body"], "msg": "the body must be a JSON object"})
            raise ErrorResponse(422, "validation", detail)
        data = parsed
    for spec in params:
        source: Mapping[str, object] = {"path": path, "query": query, "body": data}[spec.source]
        loc = [spec.source, spec.name]
        if spec.optional and spec.name in source and source[spec.name] is None:
            # an explicit null: sets NULL, where every column it is written to allows it
            if spec.nullable:
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
            # form fields are text, like a query string
            value = _convert(spec, source[spec.name], text=spec.source != "body" or form)
            _constrain(spec, value)
        except ValueError as err:
            detail.append({"loc": loc, "msg": str(err)})
            continue
        values[spec.name] = value
    if detail:
        raise ErrorResponse(422, "validation", detail)
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


def authenticate(ep: Endpoint, conn: sqlite3.Connection, token: str) -> Caller:
    """The caller by bearer token (one statement): 401 if unknown, 403 if the role may
    not call the endpoint (the admin role may call every endpoint)."""
    row = conn.execute(ep.auth_sql, (token,)).fetchone()
    if row is None:
        raise ErrorResponse(401, "unauthorized", UNAUTHORIZED_DETAIL)
    user_id = row[ep.user_columns.index(ep.user_key)]
    if ACTIVE_COLUMN is not None and not row[ep.user_columns.index(ACTIVE_COLUMN)]:
        raise ErrorResponse(INACTIVE_STATUS, STATUS_CODES[INACTIVE_STATUS], INACTIVE_DETAIL)
    if ep.admin_flag is not None:
        role = ep.admin_role if row[ep.user_columns.index(ep.admin_flag)] else ep.flag_role
    else:
        role = str(row[ep.user_columns.index(ep.role_column)])
    if not (ep.auth == "Public" or role == ep.auth or role == ep.admin_role):
        raise ErrorResponse(403, "forbidden", FORBIDDEN_DETAIL)
    return Caller(user_id, int(role == ep.admin_role), tuple(row))


# ---- steps --------------------------------------------------------------------------------


def sql_value(v: object) -> object:
    """A parameter value as SQLite stores it (Bool 0/1, Date ISO text, DateTime and Uuid
    in DATETIME_STORAGE / UUID_STORAGE; an optional parameter that was not sent is null)."""
    if v is UNSET:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, dt.datetime):
        return stored_datetime(v)
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return stored_uuid(v)
    return v


def sent(values: Mapping[str, object], name: str) -> int:
    """1 when the optional parameter `name` was sent (its update() value applies)."""
    return int(values[name] is not UNSET)


def rows_json(result: Result, columns: Sequence[str]) -> str:
    """Rows of an earlier step, restricted to `columns`, bound as one JSON parameter."""
    cols, rows = result
    index = [list(cols).index(c) for c in columns]
    return json.dumps([[r[i] for i in index] for r in rows])


def run(conn: sqlite3.Connection, sql: str, binds: Mapping[str, object]) -> list[tuple[Any, ...]]:
    return conn.execute(sql, binds).fetchall()


def write(
    conn: sqlite3.Connection, sql: str, binds: Mapping[str, object], conflict: ErrorResponse
) -> list[tuple[Any, ...]]:
    """A write with unique_or=Conflict(...): a UNIQUE violation answers `conflict`."""
    try:
        return run(conn, sql, binds)
    except sqlite3.Error as err:
        if _is_unique_violation(err):
            raise conflict from None
        raise


def split_total(rows: list[tuple[Any, ...]]) -> tuple[list[tuple[Any, ...]], int]:
    """A page(total=True) read: (the page's rows, the total). Each row ends with
    (__in_page, __total); an empty page is one placeholder row with __in_page 0."""
    total = int(rows[0][-1]) if rows else 0
    return [r[:-2] for r in rows if r[-2]], total


def project(result: Result, columns: Sequence[str]) -> list[tuple[Any, ...]]:
    cols, rows = result
    index = [cols.index(c) for c in columns]
    return [tuple(r[i] for i in index) for r in rows]


def check_one(result: Result, err: ErrorResponse, where: str) -> None:
    """one(): exactly one row; none -> the declared error; several -> 500 (R310)."""
    n = len(result[1])
    if n == 0:
        raise err
    if n > 1:
        raise Failure(f"{where}: error R310: one() matched {n} rows; expected exactly one")


def check_require(result: Result, flag: str, err: ErrorResponse) -> None:
    """require(): the condition (computed as column `flag`) holds on every row."""
    if any(not f[0] for f in project(result, [flag])):  # false or null fails
        raise err


def merge_update(
    source: Result,
    returned: list[tuple[Any, ...]],
    columns: tuple[str, ...],
    key: tuple[str, ...],
    set_cols: tuple[str, ...],
) -> Result:
    """The updated rows: `source` with the new values RETURNING gave (key, values...)."""
    k = len(key)
    new = {tuple(r[:k]): r[k:] for r in returned}
    key_idx = [columns.index(c) for c in key]
    rows = []
    for row in project(source, columns):
        updated = new.get(tuple(row[i] for i in key_idx))
        if updated is None:
            rows.append(row)
            continue
        cells = list(row)
        for c, v in zip(set_cols, updated, strict=True):
            if c in columns:
                cells[columns.index(c)] = v
        rows.append(tuple(cells))
    return columns, rows


# ---- responses ----------------------------------------------------------------------------


def _decode(v: object, scalar: str) -> object:
    if v is None:
        return None
    if scalar == "Int":
        return int(v)  # type: ignore[call-overload]
    if scalar == "Float":
        return float(v)  # type: ignore[arg-type]
    if scalar == "Bool":
        return bool(v)
    if scalar == "Uuid":  # canonical text
        return uuid_text(v) or v
    if scalar == "DateTime":  # canonical text
        parsed = parse_datetime(v)
        return v if parsed is None else wire_datetime(parsed)
    return v  # Str; Date stays ISO text


def _finite(value: object) -> object:
    """A non-finite float (an arithmetic overflow) serializes as JSON null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _items(result: Result, columns: Sequence[tuple[str, str]]) -> list[dict[str, object]]:
    names = [c for c, _ in columns]
    return [
        {c: _finite(_decode(v, t)) for (c, t), v in zip(columns, row, strict=True)}
        for row in project(result, names)
    ]


def respond_one(
    result: Result, columns: Sequence[tuple[str, str]], *, status: int, where: str
) -> Response:
    """One / Created: the single returned row as an object."""
    items = _items(result, columns)
    if not items:
        raise ErrorResponse(404, "not_found", "no such row")
    if len(items) > 1:
        raise Failure(
            f"{where}: error R310: the response has {len(items)} rows; expected exactly one"
        )
    return JSONResponse(items[0], status_code=status)


def respond_page(
    result: Result,
    columns: Sequence[tuple[str, str]],
    shape: Sequence[tuple[str, str]],
    limit: object,
    offset: object,
    total: int | None = None,
) -> Response:
    """The Page envelope in the profile's shape ((key, JSON field); "" = omitted)."""
    parts: dict[str, object] = {"items": _items(result, columns), "limit": limit, "offset": offset}
    if total is not None:
        parts["total"] = total
    return JSONResponse({name: parts[key] for key, name in shape if name and key in parts})


def _internal(detail: str) -> JSONResponse:
    return json_error(500, "internal", detail[:500])


UNIQUE_CONFLICT = "a row with the same unique value already exists"


def _is_unique_violation(err: BaseException) -> bool:
    return isinstance(err, sqlite3.IntegrityError) and str(err).startswith(
        "UNIQUE constraint failed"
    )


Steps = Callable[..., Response]  # (conn, values, caller, now[, pending outbox rows])


def serve(
    ep: Endpoint,
    steps: Steps,
    path: Mapping[str, str],
    query: Mapping[str, str],
    body: bytes,
    authorization: str | None,
    content_type: str | None = None,
) -> Response:
    """Authenticate, validate, then run the steps in one transaction; any error
    response rolls back. fn.now() is one instant per request, taken first."""
    now = dt.datetime.now(dt.UTC)
    conn: sqlite3.Connection | None = None
    in_txn = False
    try:
        caller: Caller | None = None
        if ep.auth != "Public":
            token = _token(authorization)
            if token is None:
                raise ErrorResponse(401, "unauthorized", UNAUTHORIZED_DETAIL)
            conn = connect()
            caller = authenticate(ep, conn, token)
        values = parse_params(ep.params, path, query, body, ep.form, content_type)
        if conn is None:
            conn = connect()
        conn.execute("BEGIN IMMEDIATE" if ep.writes else "BEGIN")
        in_txn = True
        pending: list[tuple[Any, ...]] = []
        if ep.effects:
            response = steps(conn, values, caller, now, pending)
        else:
            response = steps(conn, values, caller, now)
        conn.execute("COMMIT")
        in_txn = False
        if pending:  # after COMMIT: the response is already decided
            try:
                dispatch_outbox(conn, pending)
            except Exception:  # e.g. the connection broke: a retry takes the rows later
                pass
        return response
    except ErrorResponse as err:
        return json_error(err.status, err.code, err.detail)
    except Failure as err:
        return _internal(str(err))
    except Exception as err:  # loud: any unexpected failure is a 500
        if _is_unique_violation(err):  # a 409 naming no table or column; rolled back below
            return json_error(409, "conflict", UNIQUE_CONFLICT)
        return _internal(f"{type(err).__name__}: {err}")
    finally:
        if conn is not None:
            if in_txn:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            conn.close()


async def dispatch(request: Request, ep: Endpoint, steps: Steps) -> Response:
    body = await request.body()
    return await run_in_threadpool(
        serve,
        ep,
        steps,
        dict(request.path_params),
        dict(request.query_params),
        body,
        request.headers.get("authorization"),
        request.headers.get("content-type"),
    )


@app.exception_handler(404)
@app.exception_handler(405)
async def http_error(request: Request, exc: Exception) -> Response:
    status = getattr(exc, "status_code", 500)
    code = {404: "not_found", 405: "method_not_allowed"}.get(status, "internal")
    return json_error(status, code)
'''


def _module_names(src: str) -> frozenset[str]:
    """Names bound at module level by `src` (plus the builtins it may call)."""
    names: set[str] = set(dir(builtins))
    for node in ast.parse(src).body:
        match node:
            case ast.FunctionDef(name=n) | ast.AsyncFunctionDef(name=n) | ast.ClassDef(name=n):
                names.add(n)
            case ast.Assign(targets=targets):
                names.update(t.id for t in targets if isinstance(t, ast.Name))
            case ast.AnnAssign(target=ast.Name(id=n)):
                names.add(n)
            case ast.Import(names=aliases) | ast.ImportFrom(names=aliases):
                names.update((a.asname or a.name).split(".")[0] for a in aliases)
    return frozenset(names)


# generated module-level names must not shadow the prelude's (or a builtin)
_RESERVED = _module_names(_PRELUDE)


# ---- rendering helpers -------------------------------------------------------------------
#
# Generated expressions are small trees (calls, dicts, tuples over atoms) rendered in the
# style of `ruff format` (line length 100): on one line when it fits, else exploded one
# element per line with a trailing comma — a form the formatter leaves unchanged.

_WIDTH = 100


@dataclass(frozen=True)
class _Call:
    func: str
    args: tuple[_Node, ...]


@dataclass(frozen=True)
class _Kw:
    name: str
    value: _Node


@dataclass(frozen=True)
class _Dict:
    items: tuple[tuple[_Node, _Node], ...]


@dataclass(frozen=True)
class _Tuple:
    items: tuple[_Node, ...]


_Node = str | _Call | _Kw | _Dict | _Tuple  # a str is an atom (already Python source)


def _flat(node: _Node) -> str:
    match node:
        case str():
            return node
        case _Kw(name=name, value=value):
            return f"{name}={_flat(value)}"
        case _Call(func=func, args=args):
            return f"{func}({', '.join(_flat(a) for a in args)})"
        case _Dict(items=items):
            return "{" + ", ".join(f"{_flat(k)}: {_flat(v)}" for k, v in items) + "}"
        case _Tuple(items=items):
            if len(items) == 1:
                return f"({_flat(items[0])},)"
            return "(" + ", ".join(_flat(i) for i in items) + ")"
    raise LatticeInternalError(f"not a code node: {node!r}")


def _render(node: _Node, indent: str, prefix: str = "", suffix: str = "") -> list[str]:
    """Lines of `prefix + node + suffix` at `indent`, exploded as needed."""
    line = f"{indent}{prefix}{_flat(node)}{suffix}"
    if len(line) <= _WIDTH or isinstance(node, str):
        return [line]
    inner = indent + "    "
    match node:
        case _Kw(name=name, value=value):
            return _render(value, indent, f"{prefix}{name}=", suffix)
        case _Call(func=func, args=args) if args:
            head, close = f"{prefix}{func}(", ")"
            body = [ln for a in args for ln in _render(a, inner, "", ",")]
        case _Dict(items=items) if items:
            head, close = prefix + "{", "}"
            body = [ln for k, v in items for ln in _render(v, inner, f"{_flat(k)}: ", ",")]
        case _Tuple(items=items) if items:
            head, close = prefix + "(", ")"
            body = [ln for i in items for ln in _render(i, inner, "", ",")]
        case _:
            return [line]
    return [indent + head, *body, indent + close + suffix]


def _py_str(s: str) -> str:
    """A string literal as `ruff format` writes it (double quotes unless that needs
    more escapes)."""
    quote = "'" if s.count('"') > s.count("'") else '"'
    out = []
    for ch in s:
        if ch == "\\" or ch == quote:
            out.append("\\" + ch)
        elif ch.isprintable():
            out.append(ch)
        else:
            out.append(repr(ch)[1:-1])
    return quote + "".join(out) + quote


def _literal(v: object) -> str:
    """Python source for a constant value (bind values, defaults, bounds)."""
    if isinstance(v, str):
        return _py_str(v)
    if v is None or isinstance(v, bool | int):
        return repr(v)
    if isinstance(v, float):
        return repr(v) if math.isfinite(v) else f'float("{v}")'
    if isinstance(v, dt.datetime):
        v = v.astimezone(dt.UTC)
        parts = [v.year, v.month, v.day, v.hour, v.minute, v.second, v.microsecond]
        return f"dt.datetime({', '.join(map(str, parts))}, tzinfo=dt.UTC)"
    if isinstance(v, dt.date):
        return f"dt.date({v.year}, {v.month}, {v.day})"
    if isinstance(v, tuple):
        return _flat(_Tuple(tuple(_literal(x) for x in v)))
    raise LatticeInternalError(f"no Python literal for {v!r}")


def _strs(names: Sequence[str]) -> _Tuple:
    return _Tuple(tuple(_py_str(n) for n in names))


def _sql_constant(text: str) -> str:
    """The SQL text verbatim as a Python string literal (triple-quoted when possible)."""
    for quote in ('"""', "'''"):
        if quote not in text and "\\" not in text and not text.endswith(quote[0]):
            return quote + text + quote
    return _py_str(text)


def _ident(text: str) -> str:
    out = re.sub(r"\W+", "_", text).strip("_")
    return out or "step"


class _Names:
    """Unique module-level names."""

    def __init__(self) -> None:
        self.used: set[str] = set(_RESERVED)

    def take(self, base: str) -> str:
        name, n = base, 2
        while name in self.used or keyword.iskeyword(name):
            name, n = f"{base}_{n}", n + 1
        self.used.add(name)
        return name


# ---- per-endpoint generation ---------------------------------------------------------------


def _param(spec: ParamSpec, null_ok: Sequence[str] = ()) -> _Call:
    if spec.scalar is None:
        raise LatticeInternalError(f"parameter '{spec.name}' has no scalar type")
    args: list[_Node] = [_py_str(spec.name), _py_str(spec.source), _py_str(spec.scalar.value)]
    for name in ("ge", "le", "min_len", "max_len", "one_of", "pattern"):
        value = getattr(spec, name)
        if value is not None:
            args.append(_Kw(name, _literal(value)))
    if spec.default is UNSET:
        args.append(_Kw("default", "UNSET"))
    elif spec.default is not MISSING:
        args.append(_Kw("default", _literal(spec.default)))
    if spec.optional:
        args.append(_Kw("optional", "True"))
        if spec.name in null_ok:
            args.append(_Kw("nullable", "True"))
    if spec.email:
        args.append(_Kw("email", "True"))
    return _Call("Param", tuple(args))


def _auth_sql(plan: EndpointPlan) -> str:
    """The runtime's authentication statement (the same compile_lookup)."""
    return auth_sql(plan, SQLITE)


class _EndpointGen:
    def __init__(self, plan: EndpointPlan, names: _Names, lines: Sequence[str] | None) -> None:
        self.plan = plan
        self.names = names
        self.lines = lines
        self.base = _ident(plan.name).upper()
        self.constants: list[str] = []  # module-level constants (SQL text, columns)
        self.body: list[str] = []  # the steps function body

    # ---- pieces ---------------------------------------------------------------------------

    def comment(self, line: int, fallback: str) -> None:
        text = ""
        if self.lines is not None and 1 <= line <= len(self.lines):
            text = self.lines[line - 1].strip()
        self.body.append(f"    # line {line}: {text or fallback}")

    def constant(self, base: str, value: _Node | str) -> str:
        name = self.names.take(f"{self.base}_{base}")
        if isinstance(value, str):
            self.constants.append(f"{name} = {value}")
        else:
            self.constants.append("\n".join(_render(value, "", f"{name} = ")))
        return name

    def emit(self, node: _Node, prefix: str = "") -> None:
        self.body.extend(_render(node, "    ", prefix))

    def binds(self, sql: SqlQuery) -> _Dict:
        """The named parameters of a statement: every value is bound, never spliced."""
        items: list[tuple[_Node, _Node]] = [
            (_py_str(name), _literal(value)) for name, value in sql.binds
        ]
        items += [
            (_py_str(f"p_{p}"), _Call("sql_value", (f"values[{_py_str(p)}]",))) for p in sql.params
        ]
        items += [(_py_str(sent_bind(p)), _Call("sent", ("values", _py_str(p)))) for p in sql.sent]
        items += [
            (_py_str(bind), _Call("rows_json", (f"results[{_py_str(step)}]", _strs(cols))))
            for bind, step, cols in sql.rows
        ]
        if sql.user:
            items += [(_py_str(USER_ID), "user_id"), (_py_str(IS_ADMIN), "is_admin")]
        if sql.now:
            items += [(_py_str(NOW), _Call("sql_value", ("now",)))]
        return _Dict(tuple(items))

    def run(self, step_id: str, sql: SqlQuery, conflict: ErrorSpec | None = None) -> _Call:
        const = self.constant(f"{_ident(step_id).upper()}_SQL", _sql_constant(sql.text))
        if conflict is not None:  # unique_or=Conflict(...)
            return _Call("write", ("conn", const, self.binds(sql), self.error_call(conflict)))
        return _Call("run", ("conn", const, self.binds(sql)))

    def result(self, step_id: str) -> str:
        return f"results[{_py_str(step_id)}]"

    def error(self, step: CheckStep) -> _Call:
        return self.error_call(step.error)

    def error_call(self, e: ErrorSpec) -> _Call:
        return _Call("error", (str(e.status), _py_str(e.code), _py_str(e.detail)))

    # ---- steps ----------------------------------------------------------------------------

    def step(self, step: ReadStep | CheckStep | WriteStep | ForEachStep) -> None:
        match step:
            case ReadStep() if step.sql.total:
                self.body.append(f"    # SELECT the rows of {step.id!r} and the page total (one")
                self.body.append("    # statement)")
                cols = self.constant(
                    f"{_ident(step.id).upper()}_COLUMNS", _strs(step.schema.names() + step.flags)
                )
                self.emit(
                    _Call("split_total", (self.run(step.id, step.sql),)),
                    f"rows, totals[{_py_str(step.id)}] = ",
                )
                self.emit(_Tuple((cols, "rows")), f"{self.result(step.id)} = ")
            case ReadStep():
                self.body.append(f"    # SELECT the rows of {step.id!r} (one statement)")
                cols = self.constant(
                    f"{_ident(step.id).upper()}_COLUMNS", _strs(step.schema.names() + step.flags)
                )
                self.emit(_Tuple((cols, self.run(step.id, step.sql))), f"{self.result(step.id)} = ")
            case CheckStep(kind="one"):
                self.comment(step.span.line, f"one({step.input})")
                where = _py_str(f"{step.span.line}:{step.span.col}")
                self.emit(_Call("check_one", (self.result(step.input), self.error(step), where)))
            case CheckStep(kind="require"):
                if step.flag_step is None or step.flag is None:
                    raise LatticeInternalError(f"require '{step.id}' has no condition flags")
                self.comment(step.span.line, f"require({step.input})")
                args = (self.result(step.flag_step), _py_str(step.flag), self.error(step))
                self.emit(_Call("check_require", args))
            case WriteStep(kind="insert"):
                self.comment(step.span.line, f"insert into {step.table}")
                cols = self.constant(
                    f"{_ident(step.id).upper()}_COLUMNS", _strs(step.schema.names())
                )
                run = self.run(step.id, step.sql, step.conflict)
                self.emit(_Tuple((cols, run)), f"{self.result(step.id)} = ")
            case WriteStep(kind="update"):
                assert step.input is not None
                self.comment(step.span.line, f"update {step.table}")
                self.emit(self.run(step.id, step.sql, step.conflict), "returned = ")
                args = (
                    self.result(step.input),
                    "returned",
                    _strs(step.schema.names()),
                    _strs(step.key),
                    _strs([c for c, _ in step.values]),
                )
                self.emit(_Call("merge_update", args), f"{self.result(step.id)} = ")
            case WriteStep(kind="delete"):
                assert step.input is not None
                self.comment(step.span.line, f"delete from {step.table}")
                self.emit(self.run(step.id, step.sql))
                cols = _strs(step.schema.names())
                project = _Call("project", (self.result(step.input), cols))
                self.emit(_Tuple((cols, project)), f"{self.result(step.id)} = ")
            case ForEachStep():
                self.comment(step.span.line, f"for_each native.{step.native}")
                self.body.append("    # one outbox row per row, sent after COMMIT")
                self.body.append("    conn.execute(OUTBOX_DDL)")
                self.emit(_Call("pending.extend", (self.run(step.id, step.sql),)))
            case _:
                raise LatticeInternalError(f"unknown step {step!r}")

    def respond(self) -> None:
        plan = self.plan
        if plan.success == "NoContent":
            self.body.append("    return Response(status_code=204)")
            return
        pairs = tuple(_Tuple((_py_str(c), _py_str(t.value))) for c, t in plan.schema.columns)
        columns = self.constant("RESPONSE", _Tuple(pairs))
        result = self.result(plan.returns)
        if plan.success == "Page":
            if plan.page is None:
                raise LatticeInternalError(f"Page endpoint '{plan.name}' returns no page()")

            def arg(v: int | str) -> str:
                return f"values[{_py_str(v)}]" if isinstance(v, str) else repr(v)

            shape = self.constant(
                "PAGE_SHAPE",
                _Tuple(tuple(_Tuple((_py_str(k), _py_str(v))) for k, v in plan.page_shape)),
            )
            args: tuple[_Node, ...] = (
                result,
                columns,
                shape,
                arg(plan.page.limit),
                arg(plan.page.offset),
            )
            if plan.page.total_step is not None:
                args += (f"totals[{_py_str(plan.page.total_step)}]",)
            self.emit(_Call("respond_page", args), "return ")
            return
        status = "201" if plan.success == "Created" else "200"
        where = _py_str(f"{plan.span.line}:{plan.span.col}")
        args = (result, columns, _Kw("status", status), _Kw("where", where))
        self.emit(_Call("respond_one", args), "return ")

    # ---- the whole endpoint ---------------------------------------------------------------

    def role_source(self) -> list[_Node]:
        """Endpoint(...) keywords for a role source other than the "role" column."""
        plan = self.plan
        out: list[_Node] = []
        if plan.role_column != "role":
            out.append(_Kw("role_column", _py_str(plan.role_column)))
        if plan.admin_flag is not None:
            out.append(_Kw("admin_flag", _py_str(plan.admin_flag)))
            out.append(_Kw("flag_role", _py_str(plan.flag_role)))
        return out

    def render(self) -> str:
        plan = self.plan
        ep_name = self.names.take(f"{self.base}_ENDPOINT")
        steps_name = self.names.take(f"_{_ident(plan.name)}_steps")
        route_name = self.names.take(_ident(plan.name))
        self.body.append("    results: dict[str, Result] = {}")
        if any(isinstance(s, ReadStep) and s.sql.total for s in plan.steps):
            self.body.append("    totals: dict[str, int] = {}")
        if any(isinstance(s, ReadStep | WriteStep) and s.sql.user for s in plan.steps):
            self.body += [
                "    user_id, is_admin = (None, 0) if caller is None else (caller.id, "
                "caller.is_admin)",
            ]
        self.body.append("    if caller is not None:")
        self.body += _render(
            _Tuple((_strs(plan.user_columns), "[caller.row]")),
            "        ",
            f"{self.result(USER_PARAM)} = ",
        )
        for step in plan.steps:
            self.step(step)
        self.respond()

        endpoint = _Call(
            "Endpoint",
            (
                _Kw("name", _py_str(plan.name)),
                _Kw("auth", _py_str(plan.auth)),
                _Kw(
                    "params",
                    _Tuple(
                        tuple(_param(p, plan.null_ok) for p in plan.params if p.source != "user")
                    ),
                ),
                _Kw("writes", repr(plan.writes)),
                _Kw("auth_sql", _py_str(_auth_sql(plan))),
                _Kw("user_columns", _strs(plan.user_columns)),
                _Kw("user_key", _py_str(plan.user_key)),
                _Kw("admin_role", _py_str(plan.admin_role)),
                *self.role_source(),
                *([_Kw("form", "True")] if plan.body == "form" else []),
                *([_Kw("effects", "True")] if plan.effects else []),
            ),
        )
        rule = "# " + "-" * 86
        out = [
            rule,
            f"# {plan.name}: {plan.method} {plan.path} "
            f"(auth={plan.auth}; Lattice source line {plan.span.line})",
            rule,
            "",
            *_render(endpoint, "", f"{ep_name} = "),
        ]
        for const in self.constants:
            out += ["", const]
        doc = f"{plan.method} {plan.path}: the plan's steps, in order."
        out += [
            "",
            "",
            f"def {steps_name}(",
            "    conn: sqlite3.Connection,",
            "    values: dict[str, object],",
            "    caller: Caller | None,",
            "    now: dt.datetime,",
            *(["    pending: list[tuple[Any, ...]],"] if plan.effects else []),
            ") -> Response:",
            f'    """{doc}"""',
            *self.body,
            "",
            "",
            f"@app.{_METHODS[plan.method]}({_py_str(plan.path)}, name={_py_str(plan.name)})",
            f"async def {route_name}(request: Request) -> Response:",
            f"    return await dispatch(request, {ep_name}, {steps_name})",
        ]
        return "\n".join(out)


def _specificity(plan: EndpointPlan) -> list[int]:
    """Literal segments before parameters, so /orders/mine wins over /orders/{id}
    (the runtime's route order)."""
    return [1 if seg.startswith("{") else 0 for seg in plan.path.split("/")]


_HEADER = '''\
"""Generated from a Lattice API program by lattice.api.codegen -- do not edit.

A standalone FastAPI app over the benchmark's platform_kit (app, get_db) that reproduces
the Lattice API runtime exactly: authentication, parameter validation, one transaction
per request, the plan's precompiled SQL with every value bound as a parameter, error
responses and serialization.

Authentication is inline rather than the kit's current_user/require_role: the runtime
reads the caller's full row on the request's own connection (one statement) and
accepts the scheme "bearer" in any case; the kit reads fixed columns on a second
connection and accepts only "Bearer ".
"""

'''


def _natives_section(plans: Sequence[EndpointPlan], module: str, names: _Names) -> tuple[str, str]:
    """(the import of the natives the plans call or send from `module`, their
    registration: pure natives in NATIVES, effect natives in EFFECTS)."""
    natives: dict[str, NativeSig] = {}
    effects: dict[str, NativeSig] = {}
    for plan in plans:
        natives.update(plan.natives)
        effects.update(plan.effects)
    if not natives and not effects:
        return "", ""
    aliases = {name: names.take(f"native_{name}") for name in sorted({*natives, *effects})}
    imported = [f"{name} as {alias}" for name, alias in aliases.items()]
    line = f"from {module} import {', '.join(imported)}"
    if len(line) > _WIDTH:
        line = "\n".join([f"from {module} import (", *(f"    {i}," for i in imported), ")"])
    entries = tuple(
        (
            _py_str(name),
            _Call(
                "Native",
                (
                    _strs([s.value for s in sig.params]),
                    _py_str(sig.result.value),
                    repr(sig.nullable),
                    aliases[name],
                ),
            ),
        )
        for name, sig in sorted(natives.items())
    )
    parts: list[str] = []
    rule = "# " + "-" * 86
    if natives:
        register = "\n".join(_render(_Call("NATIVES.update", (_Dict(entries),)), ""))
        header = f"{rule}\n# Natives (pure Python functions registered on every connection)\n{rule}"
        parts.append(header + "\n\n" + register)
    if effects:
        sent = tuple((_py_str(name), aliases[name]) for name in sorted(effects))
        register = "\n".join(_render(_Call("EFFECTS.update", (_Dict(sent),)), ""))
        header = f"{rule}\n# Effect natives (for_each: sent from the outbox after COMMIT)\n{rule}"
        parts.append(header + "\n\n" + register)
    return line + "\n", "\n\n\n".join(parts)


# Prelude constants set from the (profile-wide) plan settings: (constant, plan field).
_PROFILE_CONSTANTS = (
    ("UUID_STORAGE", "uuid_storage"),
    ("DATETIME_STORAGE", "datetime_storage"),
    ("DATETIME_WIRE", "datetime_wire"),
    ("ERROR_BODY", "error_body"),
    ("UNAUTHORIZED_DETAIL", "unauthorized_detail"),
    ("FORBIDDEN_DETAIL", "forbidden_detail"),
    ("ACTIVE_COLUMN", "active_column"),
    ("INACTIVE_STATUS", "inactive_status"),
    ("INACTIVE_DETAIL", "inactive_detail"),
)


def _set_constant(prelude: str, name: str, value: str) -> str:
    """The prelude with module constant `name` (one `NAME[: type] = ...` line) set."""
    pattern = re.compile(rf"^({re.escape(name)}(?:: [^=]+)? = ).*$", re.MULTILINE)
    new, n = pattern.subn(lambda m: m.group(1) + value, prelude, count=1)
    if n != 1:
        raise LatticeInternalError(f"the prelude has no constant {name}")
    return new


def generate_fastapi(
    plans: Sequence[EndpointPlan], source: str | None = None, natives_module: str = "natives"
) -> str:
    """The source of a standalone FastAPI `app.py` serving `plans` exactly as the
    Phase A runtime does. With the Lattice `source`, each check and write is annotated
    with the source line it was lowered from. When the plans call natives, the app
    imports them by name from the module `natives_module` (placed next to the app) and
    registers them on every connection."""
    if not natives_module.replace(".", "_").isidentifier():
        raise LatticeInternalError(f"natives_module {natives_module!r} is not a module name")
    for plan in plans:
        if plan.method not in _METHODS:
            raise LatticeInternalError(f"unsupported method {plan.method!r}")
        # the generated app runs over the platform kit's SQLite connection and
        # registers the SQLite dialect's functions itself
        if plan.backend != SQLITE.name:
            raise LatticeInternalError(
                f"FastAPI generation supports the sqlite backend only, not {plan.backend!r}"
            )
    lines = source.splitlines() if source is not None else None
    names = _Names()
    ordered = sorted(plans, key=_specificity)
    routes = [f"#   line {p.span.line:>4}: {p.method:<6} {p.path} -> {p.name}" for p in ordered]
    header = _HEADER + "# Routes in registration order (Lattice source line of each endpoint):\n"
    imports, register = _natives_section(ordered, natives_module, names)
    kit = "from platform_kit import app, get_db\n"
    prelude = _PRELUDE.replace(kit, kit + imports, 1)
    for name, value in (
        ("OUTBOX_DDL", CREATE_SQL),
        ("OUTBOX_COLUMNS", COLUMNS_SQL),
        ("OUTBOX_ADD_CLAIMED", ADD_CLAIMED_SQL),
        ("OUTBOX_CLAIM", CLAIM_SQL),
        ("OUTBOX_DONE", MARK_DONE_SQL),
        ("OUTBOX_FAILED", MARK_FAILED_SQL),
        ("OUTBOX_EXISTS", EXISTS_SQL),
        ("OUTBOX_RETRY", RETRY_SQL),
        ("OUTBOX_RETRY_PENDING", RETRY_PENDING_SQL),
        ("OUTBOX_ERROR_LIMIT", ERROR_LIMIT),
        ("OUTBOX_GRACE", GRACE_SECONDS),
        ("OUTBOX_MARK_TRIES", MARK_TRIES),
        ("OUTBOX_MARK_BACKOFF", MARK_BACKOFF),
    ):
        literal = _sql_constant(value) if isinstance(value, str) else _literal(value)
        prelude = _set_constant(prelude, name, literal)
    for name, field in _PROFILE_CONSTANTS:
        values = {getattr(p, field) for p in plans}
        if len(values) > 1:
            raise LatticeInternalError(f"plans disagree on {field}: {sorted(map(repr, values))}")
        if values:
            prelude = _set_constant(prelude, name, _literal(values.pop()))
    parts = [header + "\n".join(routes) + "\n\n" + prelude.rstrip("\n")]
    if register:
        parts.append(register)
    parts += [_EndpointGen(plan, names, lines).render() for plan in ordered]
    return "\n\n\n".join(parts) + "\n"


def compile_source_to_fastapi(
    source: str, profile: Profile, natives_module: str = "natives"
) -> tuple[CheckResult, str | None]:
    """Check -> lower -> generate. (check result, None) when the program is rejected."""
    result = check_source(source, profile)
    if result.program is None:
        return result, None
    return result, generate_fastapi(lower_api(result.program), source, natives_module)
