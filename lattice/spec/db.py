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
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

DIALECTS = ("sqlite", "postgres", "mysql", "clickhouse")
SERVER_DIALECTS = ("postgres", "mysql", "clickhouse")

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
    if scheme in ("postgres", "postgresql"):
        return "postgres"
    if scheme in ("mysql", "mariadb"):
        return "mysql"
    if scheme in ("clickhouse", "ch", "http", "https"):
        return "clickhouse"
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
    """The canonical URL of ``database`` on a server (see the module docstring)."""
    auth = f"{quote(user, safe='')}:{quote(password, safe='')}@"
    if dialect == "postgres":
        return f"postgresql://{auth}{host}:{port}/{database}?sslmode=disable"
    if dialect == "mysql":
        return f"mysql://{auth}{host}:{port}/{database}"
    if dialect == "clickhouse":
        return f"clickhouse://{auth}{host}:{port}/{database}"
    raise ValueError(f"{dialect!r} is not a server dialect")


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
    hostport = f"{u.host}:{u.port}" if u.port else u.host
    db = u.database or ""
    if driver == "canonical":
        return url
    if u.dialect == "postgres":
        if driver in ("go-pgx", "go-pq", "psycopg", "libpq"):
            return url
        if driver == "sqlalchemy":
            return "postgresql+psycopg://" + url.split("://", 1)[1]
        if driver == "jdbc":
            q = {"user": u.user or "", "password": u.password or "", **u.query}
            return f"jdbc:postgresql://{hostport}/{db}?{urlencode(q)}"
    if u.dialect == "mysql":
        if driver == "go-mysql":
            auth = ""
            if u.user is not None:
                auth = u.user + ("" if u.password is None else ":" + u.password) + "@"
            q = {"parseTime": "true", **u.query}
            return f"{auth}tcp({hostport})/{db}?{urlencode(q)}"
        if driver == "sqlalchemy":
            return "mysql+pymysql://" + url.split("://", 1)[1]
        if driver == "jdbc":
            q = {"user": u.user or "", "password": u.password or "", **u.query}
            return f"jdbc:mysql://{hostport}/{db}?{urlencode(q)}"
    if u.dialect == "clickhouse":
        if driver == "go-clickhouse":
            return f"http://{u.auth()}{hostport}/{db}"
        if driver == "sqlalchemy":
            return f"clickhouse+http://{u.auth()}{hostport}/{db}"
        if driver == "jdbc":
            q = {"user": u.user or "", "password": u.password or ""}
            return f"jdbc:clickhouse://{hostport}/{db}?{urlencode(q)}"
    raise ValueError(f"no {driver!r} form of a {u.dialect} URL")


# ---- the interface ---------------------------------------------------------------------


class Database:
    """A connection to one database. ``query`` answers tuples, ``query_dicts`` dicts
    (column name -> value); ``execute`` runs a statement and commits it. Use it as a
    context manager, or :meth:`close` it."""

    dialect: str = ""
    ph: str = "%s"  # the parameter placeholder
    url: str  # the reference as a string: the path (SQLite) or the canonical URL

    # -- the per-dialect surface

    def quote_ident(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def _run(self, sql: str, params: Sequence[Any] | None) -> tuple[list[str], list[tuple]]:
        raise NotImplementedError

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        raise NotImplementedError

    def tables(self) -> list[str]:
        raise NotImplementedError

    def columns(self, table: str) -> list[str]:
        raise NotImplementedError

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

    def tables(self) -> list[str]:
        return [
            r[0]
            for r in self.query(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = "
                "current_schema() AND table_type = 'BASE TABLE' ORDER BY table_name"
            )
        ]

    def columns(self, table: str) -> list[str]:
        return [
            r[0]
            for r in self.query(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = "
                "current_schema() AND table_name = %s ORDER BY ordinal_position",
                [table],
            )
        ]

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
                port=u.port or 3306,
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

    def tables(self) -> list[str]:
        return [
            r[0]
            for r in self.query(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = "
                "DATABASE() AND table_type = 'BASE TABLE' ORDER BY table_name"
            )
        ]

    def columns(self, table: str) -> list[str]:
        return [
            r[0]
            for r in self.query(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = "
                "DATABASE() AND table_name = %s ORDER BY ordinal_position",
                [table],
            )
        ]

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
                port=u.port or 8123,
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

    def tables(self) -> list[str]:
        return [
            r[0]
            for r in self.query(
                "SELECT name FROM system.tables WHERE database = currentDatabase() AND "
                "engine NOT LIKE '%View' ORDER BY name"
            )
        ]

    def columns(self, table: str) -> list[str]:
        return [
            r[0]
            for r in self.query(
                "SELECT name FROM system.columns WHERE database = currentDatabase() AND "
                "table = %s ORDER BY position",
                [table],
            )
        ]

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:  # noqa: BLE001, S110 -- already closed
            pass


_ADAPTERS: dict[str, type[Database]] = {
    "postgres": PostgresDatabase,
    "mysql": MysqlDatabase,
    "clickhouse": ClickhouseDatabase,
}


def open_db(ref: Any) -> Database:
    """The adapter for a database reference: a path is a SQLite file, a URL a server's
    database (by its scheme)."""
    dialect = dialect_of(ref)
    if dialect == "sqlite":
        text = str(ref)
        if text.startswith("sqlite:///"):  # SQLAlchemy's form: 4 slashes for an absolute path
            text = text[len("sqlite:///") :]
        return SqliteDatabase(text)
    return _ADAPTERS[dialect](str(ref))


def check_dialect(name: str) -> str:
    wanted = (name or "sqlite").lower()
    if wanted == "postgresql":
        wanted = "postgres"
    if wanted not in DIALECTS:
        raise ValueError(f"unknown database {name!r} (one of {', '.join(DIALECTS)})")
    return wanted


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
