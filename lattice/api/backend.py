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
import functools
import re
import sqlite3
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from lattice.profile import DATETIME_STORAGES, UUID_STORAGES, NativeSig
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
    "DATETIME_STORAGES",
    "UUID_STORAGES",
    "datetime_text",
    "dialect_for",
    "parse_datetime",
    "stored_datetime",
    "stored_uuid",
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
    """The stored text of a DateTime: fixed-width ISO-8601 in UTC with microseconds and
    a `Z` suffix, so text order is time order (a naive value is UTC)."""
    if value.tzinfo is not None:
        value = value.astimezone(dt.UTC)
    return value.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def wire_datetime(text: str, wire: str) -> str:
    """A DateTime as a response spells it (`ApiProfile.datetime_wire`): "iso_z" is
    Lattice's canonical text (`datetime_text`); "naive" the UTC value without a zone
    as pydantic serializes a naive datetime -- `datetime.isoformat()`, microseconds only
    when they are not zero. Text that is not a date-time is returned unchanged."""
    parsed = parse_datetime(text)
    if parsed is None:
        return text
    if wire == "naive":
        return parsed.astimezone(dt.UTC).replace(tzinfo=None).isoformat()
    return datetime_text(parsed)


# How Uuid and DateTime values are stored (`ApiProfile.uuid_storage` /
# `ApiProfile.datetime_storage`). The default is Lattice's canonical text; the others
# let a program run on an existing database written by another stack (e.g. SQLAlchemy
# on SQLite: `CHAR(32)` hex ids and "YYYY-MM-DD HH:MM:SS.ffffff" naive-UTC datetimes).
# Only the stored spelling changes: every spelling is fixed-width (text order is value
# order), responses and natives see the canonical values, and parameters are accepted
# exactly as before.
# UUID_STORAGES: "canonical" ("5b3e...-...", lowercase, hyphenated) | "hex32";
# DATETIME_STORAGES: "iso_z" ("...T09:00:00.000000Z") | "sql_space" ("... 09:00:00.000000").


def stored_uuid(value: object, storage: str = "canonical") -> str | None:
    """A UUID (object, or text hyphenated or 32 hex digits) as `storage` keeps it."""
    text = uuid_text(value)
    if text is None or storage == "canonical":
        return text
    if storage == "hex32":
        return text.replace("-", "")
    raise ValueError(f"unknown uuid_storage {storage!r}")


def stored_datetime(value: dt.datetime, storage: str = "iso_z") -> str:
    """A DateTime (a naive one is UTC) as `storage` keeps it: both spellings are UTC,
    fixed-width, with microseconds."""
    if storage == "iso_z":
        return datetime_text(value)
    if storage == "sql_space":
        if value.tzinfo is not None:
            value = value.astimezone(dt.UTC)
        return value.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")
    raise ValueError(f"unknown datetime_storage {storage!r}")


@dataclass(frozen=True)
class Capabilities:
    transactions: bool  # multi-statement atomic writes with rollback
    returning: bool  # INSERT/UPDATE/DELETE ... RETURNING
    unique_constraints: bool  # uniqueness enforced by the store
    window_functions: bool  # COUNT(*) OVER () etc.
    json_params: bool  # binding a JSON array as a relation (json_each / equivalent)
    mutations_sync: bool  # UPDATE/DELETE visible immediately
    native_functions: bool  # user functions callable inside queries
    # WITH RECURSIVE (traverse); conservatively absent unless a backend declares it
    recursive_queries: bool = False


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
    # `call OVER (PARTITION BY partition ORDER BY order frame)` (empty parts omitted)
    def window(self, call: str, partition: str, order: str, frame: str | None) -> str: ...
    # a ROWS frame ending at the current row: `preceding` rows back (None: unbounded)
    def rows_frame(self, preceding: str | None) -> str: ...
    def with_clause(self, recursive: bool) -> str: ...  # WITH (RECURSIVE) opening CTEs
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

    # ---- errors ----------------------------------------------------------------------
    def is_unique_violation(self, exc: BaseException) -> bool: ...  # a UNIQUE / key conflict


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


def _to_datetime(x: object, fmt: object) -> dt.datetime | None:
    if not isinstance(x, str) or not isinstance(fmt, str):
        return None
    try:
        return dt.datetime.strptime(x, fmt)
    except ValueError:
        return None


class SqliteDialect:
    """SQLite: the reference backend. Every capability holds; four deterministic
    Python functions (Unicode-correct lower/upper/strip, floored modulo) are
    registered on each connection; Bool is stored 0/1 and Date as ISO text. Uuid and
    DateTime values are stored in the given spellings (`UUID_STORAGES`,
    `DATETIME_STORAGES`; canonical text by default)."""

    name = "sqlite"
    capabilities = Capabilities(
        transactions=True,
        returning=True,
        unique_constraints=True,
        window_functions=True,
        json_params=True,
        mutations_sync=True,
        native_functions=True,
        recursive_queries=True,
    )

    def __init__(self, uuid_storage: str = "canonical", datetime_storage: str = "iso_z") -> None:
        if uuid_storage not in UUID_STORAGES:
            raise ValueError(f"unknown uuid_storage {uuid_storage!r} (known: {UUID_STORAGES})")
        if datetime_storage not in DATETIME_STORAGES:
            raise ValueError(
                f"unknown datetime_storage {datetime_storage!r} (known: {DATETIME_STORAGES})"
            )
        self.uuid_storage = uuid_storage
        self.datetime_storage = datetime_storage

    def with_storage(self, uuid_storage: str, datetime_storage: str) -> SqliteDialect:
        """This dialect storing Uuid/DateTime values in the given spellings."""
        return _sqlite_with_storage(uuid_storage, datetime_storage)

    def __repr__(self) -> str:
        if (self.uuid_storage, self.datetime_storage) == ("canonical", "iso_z"):
            return f"<dialect {self.name}>"
        return f"<dialect {self.name} uuid={self.uuid_storage} datetime={self.datetime_storage}>"

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

    def window(self, call: str, partition: str, order: str, frame: str | None) -> str:
        parts = [
            *([f"PARTITION BY {partition}"] if partition else []),
            *([f"ORDER BY {order}"] if order else []),
            *([frame] if frame else []),
        ]
        return f"{call} OVER ({' '.join(parts)})"

    def rows_frame(self, preceding: str | None) -> str:
        start = "UNBOUNDED" if preceding is None else preceding
        return f"ROWS BETWEEN {start} PRECEDING AND CURRENT ROW"

    def with_clause(self, recursive: bool) -> str:
        return "WITH RECURSIVE" if recursive else "WITH"

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
        """Bool is 0/1; Date ISO text; DateTime fixed-width UTC text (`Z`-suffixed ISO
        by default); Uuid its canonical lowercase hyphenated text by default."""
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, dt.datetime):
            return stored_datetime(v, self.datetime_storage)
        if isinstance(v, dt.date):
            return v.isoformat()
        if isinstance(v, uuid.UUID):
            return stored_uuid(v, self.uuid_storage)
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
                return stored_datetime(parsed, self.datetime_storage)
            case Scalar.UUID if isinstance(v, uuid.UUID | str):
                text = stored_uuid(v, self.uuid_storage)
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
        us, ds = self.uuid_storage, self.datetime_storage

        def to_uuid(x: object) -> object:
            return stored_uuid(x, us)

        def to_datetime(x: object, fmt: object) -> object:
            value = _to_datetime(x, fmt)
            return None if value is None else stored_datetime(value, ds)

        def uuid4() -> str:
            return stored_uuid(uuid.uuid4(), us) or ""

        conn.create_function("lattice_to_uuid", 1, to_uuid, deterministic=True)
        conn.create_function("lattice_to_datetime", 2, to_datetime, deterministic=True)
        conn.create_function("lattice_uuid4", 0, uuid4)  # not deterministic

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

    def is_unique_violation(self, exc: BaseException) -> bool:
        # UNIQUE columns and primary keys alike ("UNIQUE constraint failed: t.c")
        return isinstance(exc, sqlite3.IntegrityError) and str(exc).startswith(
            "UNIQUE constraint failed"
        )


SQLITE = SqliteDialect()  # satisfies Dialect

DIALECTS: dict[str, Dialect] = {"sqlite": SQLITE}


@functools.cache
def _sqlite_with_storage(uuid_storage: str, datetime_storage: str) -> SqliteDialect:
    if (uuid_storage, datetime_storage) == ("canonical", "iso_z"):
        return SQLITE
    return SqliteDialect(uuid_storage, datetime_storage)


def dialect_for(
    name: str, *, uuid_storage: str = "canonical", datetime_storage: str = "iso_z"
) -> Dialect:
    """The dialect registered as `name` (an `ApiProfile.backend`), storing Uuid and
    DateTime values in the given spellings (`ApiProfile.uuid_storage` /
    `ApiProfile.datetime_storage`)."""
    dialect = DIALECTS.get(name)
    if dialect is None:
        known = ", ".join(sorted(DIALECTS))
        raise ValueError(f"unknown backend {name!r} (known: {known})")
    if (uuid_storage, datetime_storage) == ("canonical", "iso_z"):
        return dialect
    with_storage = getattr(dialect, "with_storage", None)
    if with_storage is None:
        raise ValueError(f"backend {name!r} supports only the canonical Uuid/DateTime storage")
    return with_storage(uuid_storage, datetime_storage)
