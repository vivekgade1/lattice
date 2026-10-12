"""One database interface for the spec checker, four adapters (SQLite, PostgreSQL, MySQL,
ClickHouse).

Every layer that reads or writes the app's database (C's invariants, D's ``expect_db``
and ``given`` rows, an ``Await``'s polling, X's races, R's and B's table reads) goes
through a :class:`Database`, opened from a **database reference** by :func:`open_db`:

* SQLite: the database file's **path** (a ``Path``, or a ``str`` without ``://``) -- the
  reference is what it always was, so every existing harness is unchanged;
* a server dialect: the database's **canonical URL** (below).

The reference is also what the harness receives: ``db_factory(ref)``, ``app_factory(ref)``,
a service's ``(ref) -> ServiceCmd``, and a :class:`~lattice.spec.process.ProcessApp`'s
``db_env``.

**Canonical URLs** (one per dialect; :func:`url_for` translates for a driver)::

    sqlite      /abs/path/to/file.db
    postgres    postgresql://USER:PASS@HOST:PORT/DB?sslmode=disable
    mysql       mysql://USER:PASS@HOST:PORT/DB
    clickhouse  clickhouse://USER:PASS@HOST:HTTP_PORT/DB      (the HTTP interface, 8123)

``url_for(url, driver)``:

==================  ==============================================================
driver              answers
==================  ==============================================================
``canonical``       the URL itself (``psycopg``, ``pgx`` and ``lib/pq`` take it)
``go-pgx``          the canonical Postgres URL (``sslmode=disable`` included)
``go-pq``           the same
``go-mysql``        ``USER:PASS@tcp(HOST:PORT)/DB?parseTime=true`` (go-sql-driver)
``go-clickhouse``   ``http://USER:PASS@HOST:PORT/DB`` (clickhouse-go v2, HTTP)
``go-sqlite``       the path
``sqlalchemy``      ``postgresql+psycopg://``, ``mysql+pymysql://``,
                    ``clickhouse+http://`` (clickhouse-connect's dialect), or
                    ``sqlite:///PATH``
``jdbc``            ``jdbc:postgresql://HOST:PORT/DB?user=..&password=..``,
                    ``jdbc:mysql://...``, ``jdbc:clickhouse://...``
==================  ==============================================================

The server engines are data: each one's URL template, driver forms, SQL flags and
container settings are a descriptor in ``lattice/spec/engines/*.toml`` (:mod:`lattice.spec.engines`,
which a project can extend with its own descriptors); this module reads them from the
registry. SQLite is built in.

Placeholders: ``?`` for SQLite, ``%s`` for the three servers (:attr:`Database.ph`);
identifiers are quoted with ``"`` (SQLite, Postgres, ClickHouse) or backticks (MySQL)
(:meth:`Database.quote_ident`). Errors: the server adapters raise :class:`DatabaseError`;
the SQLite adapter lets ``sqlite3.Error`` through unchanged (catch :data:`DB_ERRORS`).

ClickHouse: inserts are visible as soon as ``INSERT`` returns (``async_insert`` off,
the default). Mutations (``ALTER TABLE ... UPDATE/DELETE``) are asynchronous: an app
under check must run them with ``SETTINGS mutations_sync = 1`` (or use lightweight
``DELETE``), or the checker may read the rows before the mutation lands. The checker's
reads run with the ``final = 1`` setting, so a ``ReplacingMergeTree`` /
``CollapsingMergeTree`` table is read as if with ``FINAL`` (merged).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlsplit

from lattice.spec import engines as _engines

# the built-in dialects (a project's engines join the registry: engines.database_names())
DIALECTS: tuple[str, ...] = (_engines.SQLITE, *_engines.BUILTIN_NAMES)
SERVER_DIALECTS: tuple[str, ...] = _engines.BUILTIN_NAMES

DbRef = Path | str  # a SQLite file's path, or a server database's canonical URL


class DatabaseError(Exception):
    """A server database refused a statement (the driver's error, as text)."""


# what a read of any adapter may raise (SQLite's own errors pass through unchanged)
DB_ERRORS: tuple[type[BaseException], ...] = (DatabaseError, sqlite3.Error)


# ---- references and URLs ---------------------------------------------------------------


def is_url(ref: Any) -> bool:
    return isinstance(ref, str) and "://" in ref


def as_ref(ref: Any) -> DbRef:
    """``ref`` as a database reference: a URL stays a ``str``, anything else is a
    ``Path`` (as the checker always handled a SQLite file)."""
    return ref if is_url(ref) else Path(ref)


def dialect_of(ref: Any) -> str:
    """The dialect a reference names: ``sqlite`` for a path, else by the URL's scheme."""
    if not is_url(ref):
        return "sqlite"
    scheme = urlsplit(str(ref)).scheme.lower().split("+", 1)[0]
    found = _engines.engine_for_scheme(scheme)
    if found is not None:
        return found.name
    if scheme == "sqlite":
        return "sqlite"
    raise ValueError(f"unknown database URL scheme {scheme!r} (in {redact(str(ref))})")


def redact(url: str) -> str:
    """``url`` with its password replaced by ``***``."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@", 1)
    return parts._replace(netloc=netloc).geturl()


class UrlParts:
    """A canonical server URL's pieces."""

    def __init__(self, url: str) -> None:
        p = urlsplit(url)
        self.dialect = dialect_of(url)
        self.host = p.hostname or "127.0.0.1"
        self.port = p.port
        self.user = unquote(p.username) if p.username else None
        self.password = unquote(p.password) if p.password is not None else None
        self.database = unquote(p.path.lstrip("/")) or None
        self.query = dict(parse_qsl(p.query))

    def auth(self) -> str:
        if self.user is None:
            return ""
        pw = "" if self.password is None else ":" + quote(self.password, safe="")
        return quote(self.user, safe="") + pw + "@"


def canonical_url(
    dialect: str,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> str:
    """The canonical URL of ``database`` on a server (the engine's ``url_template``)."""
    entry = _engines.REGISTRY.entry(dialect)
    if entry is None:
        raise ValueError(f"{dialect!r} is not a server dialect")
    return entry.engine.canonical_url(host, port, user, password, database)


def with_database(url: str, database: str) -> str:
    """``url`` naming another database on the same server."""
    p = urlsplit(url)
    return p._replace(path="/" + database).geturl()


def database_name(url: str) -> str | None:
    return UrlParts(url).database


def url_for(ref: DbRef, driver: str = "canonical") -> str:
    """The reference as ``driver`` takes it (the table in the module docstring)."""
    if not is_url(ref):
        path = str(Path(ref).resolve())
        if driver in ("canonical", "go-sqlite", "path"):
            return path
        if driver == "sqlalchemy":
            return f"sqlite:///{path}"
        if driver == "jdbc":
            return f"jdbc:sqlite:{path}"
        raise ValueError(f"no {driver!r} form of a SQLite path")
    url = str(ref)
    u = UrlParts(url)
    if driver == "canonical":
        return url
    # the engine's [drivers] table
    found = _engines.driver_url(_engines.engine(u.dialect), url, u, driver)
    if found is None:
        raise ValueError(f"no {driver!r} form of a {u.dialect} URL")
    return found


# ---- the interface ---------------------------------------------------------------------


class Database:
    """A connection to one database. ``query`` answers tuples, ``query_dicts`` dicts
    (column name -> value); ``execute`` runs a statement and commits it. Use it as a
    context manager, or :meth:`close` it."""

    dialect: str = ""
    ph: str = "%s"  # the parameter placeholder
    url: str  # the reference as a string: the path (SQLite) or the canonical URL

    @property
    def engine(self) -> _engines.EngineDescriptor:
        """The server engine of this database (its descriptor: the SQL of
        :meth:`tables` and :meth:`columns`, ...)."""
        found = self.__dict__.get("_engine")
        if found is None:
            found = _engines.engine(dialect_of(self.url))
        return found

    # -- the per-dialect surface

    def quote_ident(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def _run(self, sql: str, params: Sequence[Any] | None) -> tuple[list[str], list[tuple]]:
        raise NotImplementedError

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        raise NotImplementedError

    def tables(self) -> list[str]:
        """The database's tables (the engine's ``sql.list_tables``)."""
        return [r[0] for r in self.query(self.engine.sql.list_tables)]

    def columns(self, table: str) -> list[str]:
        """``table``'s columns in order (the engine's ``sql.list_columns``)."""
        return [r[0] for r in self.query(self.engine.sql.list_columns, [table])]

    def close(self) -> None:
        raise NotImplementedError

    # -- built on the surface

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        return self._run(sql, params)[1]

    def query_dicts(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cols, rows = self._run(sql, params)
        return [dict(zip(cols, row, strict=True)) for row in rows]

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        rows = self.query(sql, params)
        return rows[0][0] if rows else None

    def insert(self, table: str, rows: Iterable[Mapping[str, Any]]) -> None:
        """Insert ``rows`` (column -> value) into ``table``, each with its own columns."""
        for row in rows:
            cols = list(row)
            names = ", ".join(self.quote_ident(c) for c in cols)
            marks = ", ".join(self.ph for _ in cols)
            self.execute(
                f"INSERT INTO {self.quote_ident(table)} ({names}) VALUES ({marks})",
                [row[c] for c in cols],
            )

    def read_table(self, table: str, order_by: str | None = None) -> list[dict[str, Any]]:
        """Every row of ``table``, in insertion order (SQLite's rowid) or by ``order_by``
        (a server's tables have no row order: the key column, when given)."""
        sql = f"SELECT * FROM {self.quote_ident(table)}"
        if order_by is not None:
            sql += f" ORDER BY {self.quote_ident(order_by)}"
        return self.query_dicts(sql)

    def where_sql(self, where: Mapping[str, Any]) -> tuple[str, list[Any]]:
        """``WHERE`` text and arguments for column equalities (``None`` is ``IS NULL``)."""
        clauses, args = [], []
        for c, v in where.items():
            if v is None:
                clauses.append(f"{self.quote_ident(c)} IS NULL")
            else:
                clauses.append(f"{self.quote_ident(c)} = {self.ph}")
                args.append(v)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), args

    def url_for(self, driver: str = "canonical") -> str:
        return url_for(self.url, driver)

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class SqliteDatabase(Database):
    """A SQLite file; exactly the reads and writes the checker always made (a new
    ``sqlite3`` connection, ``rowid`` order, ``sqlite3.Error`` unwrapped)."""

    dialect = "sqlite"
    ph = "?"

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.url = str(path)
        self.conn = sqlite3.connect(self.path)

    def _run(self, sql: str, params: Sequence[Any] | None) -> tuple[list[str], list[tuple]]:
        cur = self.conn.execute(sql, list(params or ()))
        cols = [d[0] for d in cur.description or ()]
        return cols, [tuple(r) for r in cur]

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        self.conn.execute(sql, list(params))
        self.conn.commit()

    def insert(self, table: str, rows: Iterable[Mapping[str, Any]]) -> None:
        for row in rows:
            cols = list(row)
            marks = ", ".join("?" for _ in cols)
            names = ", ".join(f'"{c}"' for c in cols)
            self.conn.execute(
                f'INSERT INTO "{table}" ({names}) VALUES ({marks})', [row[c] for c in cols]
            )
        self.conn.commit()

    def tables(self) -> list[str]:
        return [
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]

    def columns(self, table: str) -> list[str]:
        return [r[1] for r in self.conn.execute(f"PRAGMA table_info({self.quote_ident(table)})")]

    def read_table(self, table: str, order_by: str | None = None) -> list[dict[str, Any]]:
        cur = self.conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur]

    def close(self) -> None:
        self.conn.close()


class PostgresDatabase(Database):
    """PostgreSQL through psycopg 3 (autocommit)."""

    dialect = "postgres"

    def __init__(self, url: str) -> None:
        import psycopg

        self.url = url
        self._errors = (psycopg.Error,)
        try:
            self.conn = psycopg.connect(url, autocommit=True, connect_timeout=10)
        except psycopg.Error as err:
            raise DatabaseError(f"cannot connect to {redact(url)}: {err}") from err

    def _run(self, sql: str, params: Sequence[Any] | None) -> tuple[list[str], list[tuple]]:
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, list(params) if params else None)  # type: ignore[arg-type]
                if cur.description is None:
                    return [], []
                cols = [d.name for d in cur.description]
                return cols, [tuple(r) for r in cur.fetchall()]
        except self._errors as err:
            raise DatabaseError(str(err).strip()) from err

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        self._run(sql, params)

    def close(self) -> None:
        self.conn.close()


class MysqlDatabase(Database):
    """MySQL through PyMySQL (autocommit)."""

    dialect = "mysql"

    def __init__(self, url: str) -> None:
        import pymysql

        self.url = url
        self._errors = (pymysql.err.MySQLError,)
        u = UrlParts(url)
        try:
            self.conn = pymysql.connect(
                host=u.host,
                port=u.port or _engines.engine(u.dialect).default_port,
                user=u.user or "root",
                password=u.password or "",
                database=u.database,
                autocommit=True,
                connect_timeout=10,
                charset="utf8mb4",
            )
        except pymysql.err.MySQLError as err:
            raise DatabaseError(f"cannot connect to {redact(url)}: {err}") from err

    def quote_ident(self, name: str) -> str:
        return "`" + name.replace("`", "``") + "`"

    def _run(self, sql: str, params: Sequence[Any] | None) -> tuple[list[str], list[tuple]]:
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, list(params) if params else None)
                if cur.description is None:
                    return [], []
                cols = [d[0] for d in cur.description]
                return cols, [tuple(r) for r in cur.fetchall()]
        except self._errors as err:
            raise DatabaseError(str(err)) from err

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        self._run(sql, params)

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001, S110 -- already closed
            pass


class ClickhouseDatabase(Database):
    """ClickHouse through clickhouse-connect (HTTP). Reads run with ``final = 1``."""

    dialect = "clickhouse"
    READ_SETTINGS: Mapping[str, Any] = {"final": 1}

    def __init__(self, url: str) -> None:
        import clickhouse_connect
        from clickhouse_connect.driver.exceptions import ClickHouseError

        self.url = url
        self._errors = (ClickHouseError,)
        u = UrlParts(url)
        try:
            self.client = clickhouse_connect.get_client(
                host=u.host,
                port=u.port or _engines.engine(u.dialect).default_port,
                username=u.user or "default",
                password=u.password or "",
                database=u.database or "default",
                connect_timeout=10,
                autogenerate_session_id=False,
            )
        except ClickHouseError as err:
            raise DatabaseError(f"cannot connect to {redact(url)}: {err}") from err

    def _run(self, sql: str, params: Sequence[Any] | None) -> tuple[list[str], list[tuple]]:
        try:
            result = self.client.query(
                sql, parameters=list(params) if params else None, settings=self.READ_SETTINGS
            )
        except self._errors as err:
            raise DatabaseError(str(err).strip()) from err
        return list(result.column_names), [tuple(r) for r in result.result_rows]

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        try:
            self.client.command(sql, parameters=list(params) if params else None)
        except self._errors as err:
            raise DatabaseError(str(err).strip()) from err

    def insert(self, table: str, rows: Iterable[Mapping[str, Any]]) -> None:
        by_cols: dict[tuple[str, ...], list[list[Any]]] = {}
        for row in rows:
            by_cols.setdefault(tuple(row), []).append(list(row.values()))
        try:
            for cols, data in by_cols.items():
                self.client.insert(table, data, column_names=list(cols))
        except self._errors as err:
            raise DatabaseError(str(err).strip()) from err

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:  # noqa: BLE001, S110 -- already closed
            pass


def open_db(ref: Any) -> Database:
    """The adapter for a database reference: a path is a SQLite file, a URL a server's
    database (by its scheme: the engine's ``adapter``, a built-in class or a project's
    ``module:Class``)."""
    dialect = dialect_of(ref)
    if dialect == "sqlite":
        text = str(ref)
        if text.startswith("sqlite:///"):  # SQLAlchemy's form: 4 slashes for an absolute path
            text = text[len("sqlite:///") :]
        return SqliteDatabase(text)
    engine = _engines.engine(dialect)
    db = engine.adapter_class()(str(ref))
    db.__dict__["_engine"] = engine
    if db.dialect != engine.name:  # a built-in adapter serving a project's engine
        db.dialect = engine.name
    return db


def check_dialect(name: str) -> str:
    """The ``--database`` value ``name`` as a dialect: ``sqlite`` or a registered server
    engine (an alias resolved: ``postgresql`` is ``postgres``)."""
    wanted = (name or "sqlite").lower()
    if wanted == "sqlite":
        return wanted
    found = _engines.find(wanted)
    if found is None:
        raise ValueError(
            f"unknown database {name!r} (one of {', '.join(_engines.database_names())})"
        )
    return found.name


__all__ = [
    "DB_ERRORS",
    "DIALECTS",
    "SERVER_DIALECTS",
    "ClickhouseDatabase",
    "Database",
    "DatabaseError",
    "DbRef",
    "MysqlDatabase",
    "PostgresDatabase",
    "SqliteDatabase",
    "as_ref",
    "canonical_url",
    "check_dialect",
    "dialect_of",
    "is_url",
    "open_db",
    "redact",
    "url_for",
    "with_database",
]
