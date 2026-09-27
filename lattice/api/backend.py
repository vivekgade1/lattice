"""The backend capability model and the SQL dialect interface (spec §3).

A `Dialect` is everything that differs between database backends: how identifiers
are quoted and parameters bound, the SQL spelling of the few constructs that are not
portable (a JSON array bound as a relation, a natural row order, a row number, true
division, floored modulo, the `fn.*` scalar functions, INSERT/UPDATE ... RETURNING),
how Lattice values are stored, how a connection is opened (with the deterministic
functions the compiled SQL calls), and how a transaction starts and ends. The CTE
compiler in `lattice.api.sql` builds the dialect-neutral skeleton and asks the dialect
for every one of those pieces; it emits no backend-specific text itself.

`Capabilities` says what a backend can guarantee; the checker pass P7
(`lattice.check.p7_backend`) rejects programs whose guarantees the profile's backend
cannot provide (L870-L873).

Only `SQLITE` is implemented (sub-project 1); `DIALECTS` maps `ApiProfile.backend`
names to dialects and `dialect_for(name)` resolves one.
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from lattice.profile import NativeSig
from lattice.typesys import Scalar

if TYPE_CHECKING:
    from lattice.api.sql import ReadPipeline, SqlQuery, WriteSpec

__all__ = [
    "DIALECTS",
    "SQLITE",
    "Capabilities",
    "Connection",
    "Dialect",
    "NativeImpl",
    "SqliteDialect",
    "datetime_text",
    "dialect_for",
    "parse_datetime",
    "uuid_text",
]

Connection = Any  # a DB-API 2.0 connection
ConnectHook = Callable[[Any], None]
# A native function's signature and its Python implementation (see `ApiProfile.natives`).
NativeImpl = tuple[NativeSig, Callable[..., object]]


# ---- Uuid and DateTime values ----------------------------------------------------------

_UUID_TEXT = re.compile(
    r"[0-9a-fA-F]{8}(-?)[0-9a-fA-F]{4}\1[0-9a-fA-F]{4}\1[0-9a-fA-F]{4}\1[0-9a-fA-F]{12}"
)


def uuid_text(text: object) -> str | None:
    """The canonical (lowercase, hyphenated) text of a UUID written hyphenated or as 32
    hex digits, or None."""
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
        except ValueError:
            return None
    else:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def datetime_text(value: dt.datetime) -> str:
    """The stored text of a DateTime: fixed-width ISO-8601 in UTC with microseconds and
    a `Z` suffix, so text order is time order (a naive value is UTC)."""
    if value.tzinfo is not None:
        value = value.astimezone(dt.UTC)
    return value.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


@dataclass(frozen=True)
class Capabilities:
    transactions: bool  # multi-statement atomic writes with rollback
    returning: bool  # INSERT/UPDATE/DELETE ... RETURNING
    unique_constraints: bool  # uniqueness enforced by the store
    window_functions: bool  # COUNT(*) OVER () etc.
    json_params: bool  # binding a JSON array as a relation (json_each / equivalent)
    mutations_sync: bool  # UPDATE/DELETE visible immediately
    native_functions: bool  # user functions callable inside queries


@runtime_checkable
class Dialect(Protocol):
    """A database backend. The first six members are the spec §3 surface; the rest
    are the pieces the dialect-neutral compiler and runtime delegate to it."""

    name: str
    capabilities: Capabilities

    def quote(self, ident: str) -> str: ...  # a quoted identifier
    def bind(self, name: str) -> str: ...  # placeholder text for a named bind
    def compile_read(self, pipeline: ReadPipeline) -> SqlQuery: ...
    def compile_write(self, step: WriteSpec) -> SqlQuery: ...
    def connect(
        self,
        target: Any,
        hook: ConnectHook | None = None,
        natives: Mapping[str, NativeImpl] | None = None,
    ) -> Connection: ...

    # ---- SQL pieces the compiler asks for --------------------------------------------
    def positional_bind(self) -> str: ...  # placeholder for a single positional parameter
    def rows_relation(self, bind: str, columns: Sequence[str], ord_col: str) -> str: ...
    def natural_order(self) -> str: ...  # a table's insertion order (no key)
    def row_number(self, order_by: str) -> str: ...  # a fresh 1-based row number
    def true_condition(self) -> str: ...  # a condition that always holds
    def flag(self, expr: str) -> str: ...  # a bound 0/1 flag as a condition
    def divide(self, a: str, b: str) -> str: ...  # true division, null on a zero divisor
    def modulo(self, a: str, b: str) -> str: ...  # floored modulo, null on a zero divisor
    def function(self, name: str, x: str, kw: Mapping[str, str]) -> str | None: ...
    def insert(self, table: str, cols: Sequence[str], vals: Sequence[str], ret: str) -> str: ...
    def update_from(self, table: str, sets: str, source: str, match: str, ret: str) -> str: ...
    def new_uuid(self) -> str: ...  # a fresh uuid4 (an omitted Uuid key on insert)
    def native(self, name: str, args: Sequence[str]) -> str: ...  # native.<name>(args...)

    # ---- values ----------------------------------------------------------------------
    def to_db(self, v: object) -> object: ...  # a Python value as the store keeps it
    def from_db(self, v: object, scalar: Scalar) -> object: ...  # a stored value as Lattice's

    # ---- transactions ----------------------------------------------------------------
    def begin(self, conn: Connection, writes: bool) -> None: ...
    def commit(self, conn: Connection) -> None: ...
    def rollback(self, conn: Connection) -> None: ...  # never raises


# ---- SQLite ----------------------------------------------------------------------------


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


def _date_part(fmt: str) -> Callable[[str, Mapping[str, str]], str]:
    return lambda x, k: f"CAST(strftime('{fmt}', {x}) AS INTEGER)"


_SQLITE_FN: Mapping[str, Callable[[str, Mapping[str, str]], str]] = {
    "lower": lambda x, k: f"lattice_lower({x})",
    "upper": lambda x, k: f"lattice_upper({x})",
    "strip": lambda x, k: f"lattice_strip({x})",
    "len": lambda x, k: f"length({x})",
    "contains": lambda x, k: f"(instr({x}, {k['sub']}) > 0)",
    "starts_with": lambda x, k: f"(substr({x}, 1, length({k['prefix']})) = {k['prefix']})",
    "year": _date_part("%Y"),
    "month": _date_part("%m"),
    "day": _date_part("%d"),
    "to_uuid": lambda x, k: f"lattice_to_uuid({x})",
    "date": lambda x, k: f"substr({x}, 1, 10)",  # stored DateTime text starts YYYY-MM-DD
    "to_datetime": lambda x, k: f"lattice_to_datetime({x}, {k['format']})",
}


def _to_uuid(x: object) -> object:
    return uuid_text(x)


def _to_datetime(x: object, fmt: object) -> object:
    if not isinstance(x, str) or not isinstance(fmt, str):
        return None
    try:
        value = dt.datetime.strptime(x, fmt)
    except ValueError:
        return None
    return datetime_text(value)


def _uuid4() -> str:
    return str(uuid.uuid4())


class SqliteDialect:
    """SQLite: the reference backend. Every capability holds; four deterministic
    Python functions (Unicode-correct lower/upper/strip, floored modulo) are
    registered on each connection; Bool is stored 0/1 and Date as ISO text."""

    name = "sqlite"
    capabilities = Capabilities(
        transactions=True,
        returning=True,
        unique_constraints=True,
        window_functions=True,
        json_params=True,
        mutations_sync=True,
        native_functions=True,
    )

    def __repr__(self) -> str:
        return f"<dialect {self.name}>"

    # ---- identifiers and binds ---------------------------------------------------------

    def quote(self, ident: str) -> str:
        return '"' + ident.replace('"', '""') + '"'

    def bind(self, name: str) -> str:
        return ":" + name

    def positional_bind(self) -> str:
        return "?"

    # ---- compilation -------------------------------------------------------------------

    def compile_read(self, pipeline: ReadPipeline) -> SqlQuery:
        from lattice.api import sql

        return sql.build_read(pipeline, self)

    def compile_write(self, step: WriteSpec) -> SqlQuery:
        from lattice.api import sql

        return sql.build_write(step, self)

    def rows_relation(self, bind: str, columns: Sequence[str], ord_col: str) -> str:
        cols = ", ".join(
            f"json_extract(j.value, '$[{i}]') AS {self.quote(c)}" for i, c in enumerate(columns)
        )
        sep = ", " if cols else ""
        return f"SELECT {cols}{sep}j.key AS {ord_col} FROM json_each({self.bind(bind)}) AS j"

    def natural_order(self) -> str:
        return "rowid"

    def row_number(self, order_by: str) -> str:
        return f"ROW_NUMBER() OVER (ORDER BY {order_by})"

    def true_condition(self) -> str:
        return "1"

    def flag(self, expr: str) -> str:
        return expr

    def divide(self, a: str, b: str) -> str:
        return f"(CAST({a} AS REAL) / {b})"  # SQLite: x / 0 is NULL

    def modulo(self, a: str, b: str) -> str:
        return f"lattice_mod({a}, {b})"

    def function(self, name: str, x: str, kw: Mapping[str, str]) -> str | None:
        build = _SQLITE_FN.get(name)
        return None if build is None else build(x, kw)

    def insert(self, table: str, cols: Sequence[str], vals: Sequence[str], ret: str) -> str:
        if cols:
            listed, values = ", ".join(cols), ", ".join(vals)
            return f"INSERT INTO {table} ({listed}) VALUES ({values}) RETURNING {ret}"
        return f"INSERT INTO {table} DEFAULT VALUES RETURNING {ret}"

    def update_from(self, table: str, sets: str, source: str, match: str, ret: str) -> str:
        return f"UPDATE {table} SET {sets} FROM ({source}) AS s WHERE {match} RETURNING {ret}"

    def new_uuid(self) -> str:
        return "lattice_uuid4()"

    def native(self, name: str, args: Sequence[str]) -> str:
        return f"native_{name}({', '.join(args)})"

    # ---- values ------------------------------------------------------------------------

    def to_db(self, v: object) -> object:
        """Bool is 0/1; Date ISO text; DateTime fixed-width UTC text with `Z`; Uuid its
        canonical lowercase hyphenated text."""
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, dt.datetime):
            return datetime_text(v)
        if isinstance(v, dt.date):
            return v.isoformat()
        if isinstance(v, uuid.UUID):
            return str(v)
        return v

    def from_db(self, v: object, scalar: Scalar) -> object:
        if v is None:
            return None
        match scalar:
            case Scalar.INT:
                return int(v)  # type: ignore[call-overload]
            case Scalar.FLOAT:
                return float(v)  # type: ignore[arg-type]
            case Scalar.BOOL:
                return bool(v)
            case Scalar.UUID:  # canonical text (a 32-hex spelling is normalized)
                return uuid_text(v) or v
            case Scalar.DATETIME:  # canonical text (another ISO spelling is normalized)
                parsed = parse_datetime(v)
                return v if parsed is None else datetime_text(parsed)
            case _:
                return v

    # ---- natives -----------------------------------------------------------------------

    def native_arg(self, v: object, scalar: Scalar) -> object:
        """A stored value as the Python value a native receives: Bool bool, Date
        `datetime.date`, DateTime an aware UTC `datetime.datetime`, Uuid `uuid.UUID`."""
        match scalar:
            case Scalar.BOOL:
                return bool(v)
            case Scalar.INT:
                return int(v)  # type: ignore[call-overload]
            case Scalar.FLOAT:
                return float(v)  # type: ignore[arg-type]
            case Scalar.DATE:
                return dt.date.fromisoformat(str(v))
            case Scalar.DATETIME:
                return parse_datetime(v)
            case Scalar.UUID:
                return uuid.UUID(str(v))
            case _:
                return v

    def native_result(self, name: str, v: object, scalar: Scalar) -> object:
        """A native's Python result as stored (a wrong type is an error)."""
        bad = TypeError(f"native {name} returned {type(v).__name__}, not {scalar.value}")
        match scalar:
            case Scalar.BOOL if isinstance(v, bool):
                return int(v)
            case Scalar.INT if isinstance(v, int) and not isinstance(v, bool):
                return v
            case Scalar.FLOAT if isinstance(v, int | float) and not isinstance(v, bool):
                return float(v)
            case Scalar.STR if isinstance(v, str):
                return v
            case Scalar.DATE if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
                return v.isoformat()
            case Scalar.DATETIME if isinstance(v, dt.datetime | str):
                parsed = parse_datetime(v)
                if parsed is None:
                    raise bad
                return datetime_text(parsed)
            case Scalar.UUID if isinstance(v, uuid.UUID | str):
                text = uuid_text(v)
                if text is None:
                    raise bad
                return text
            case _:
                raise bad

    def native_function(self, name: str, impl: NativeImpl) -> Callable[..., object]:
        """The SQL function for one native: a null argument gives null (without calling
        it); a null result is allowed only for a `nullable` native."""
        sig, fn = impl

        def call(*args: object) -> object:
            if any(a is None for a in args):
                return None
            out = fn(*(self.native_arg(a, s) for a, s in zip(args, sig.params, strict=True)))
            if out is None:
                if sig.nullable:
                    return None
                raise ValueError(f"native {name} returned null")
            return self.native_result(name, out, sig.result)

        return call

    # ---- connections and transactions --------------------------------------------------

    def register_functions(self, conn: sqlite3.Connection) -> None:
        """Install the deterministic functions the compiled SQL calls."""
        conn.create_function("lattice_lower", 1, _lower, deterministic=True)
        conn.create_function("lattice_upper", 1, _upper, deterministic=True)
        conn.create_function("lattice_strip", 1, _strip, deterministic=True)
        conn.create_function("lattice_mod", 2, _mod, deterministic=True)
        conn.create_function("lattice_to_uuid", 1, _to_uuid, deterministic=True)
        conn.create_function("lattice_to_datetime", 2, _to_datetime, deterministic=True)
        conn.create_function("lattice_uuid4", 0, _uuid4)  # not deterministic

    def register_natives(self, conn: sqlite3.Connection, natives: Mapping[str, NativeImpl]) -> None:
        """Install each native as the SQL function `native.<name>` compiles to."""
        for name, impl in natives.items():
            conn.create_function(
                f"native_{name}",
                len(impl[0].params),
                self.native_function(name, impl),
                deterministic=True,
            )

    def connect(
        self,
        target: Any,
        hook: ConnectHook | None = None,
        natives: Mapping[str, NativeImpl] | None = None,
    ) -> sqlite3.Connection:
        """A connection to the database file `target` in autocommit mode (transactions
        are explicit), usable from any thread, with the functions (and `natives`)
        registered; `hook` (if given) sees it last."""
        conn = sqlite3.connect(str(Path(target)), isolation_level=None, check_same_thread=False)
        self.register_functions(conn)
        if natives:
            self.register_natives(conn, natives)
        if hook is not None:
            hook(conn)
        return conn

    def begin(self, conn: sqlite3.Connection, writes: bool) -> None:
        # a writer takes the write lock up front, so its reads and writes are atomic
        conn.execute("BEGIN IMMEDIATE" if writes else "BEGIN")

    def commit(self, conn: sqlite3.Connection) -> None:
        conn.execute("COMMIT")

    def rollback(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass


SQLITE = SqliteDialect()  # satisfies Dialect

DIALECTS: dict[str, Dialect] = {"sqlite": SQLITE}


def dialect_for(name: str) -> Dialect:
    """The dialect registered as `name` (an `ApiProfile.backend`)."""
    dialect = DIALECTS.get(name)
    if dialect is None:
        known = ", ".join(sorted(DIALECTS))
        raise ValueError(f"unknown backend {name!r} (known: {known})")
    return dialect
