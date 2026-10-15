"""Fake Postgres and ClickHouse servers, drivers and ``docker`` for ``lattice.perf``'s
counting tests and probes: no Docker, no driver package.

A :class:`World` stands in for the machine: servers by port, ``psycopg`` and
``clickhouse_connect`` modules that talk to them (injected into ``sys.modules``) and a
``docker`` that starts them (``lattice.spec.dbserver._docker``). The databases themselves
are in-memory SQLite files (a statement the app sends runs for real, so the answers are
real); what the fakes add is each engine's *statement statistics*, modelled as the
descriptors' SQL expects them:

* **Postgres** keeps ``pg_stat_statements`` per ``(database, normalised text)`` with
  ``calls`` (``%s`` becomes ``$1``...), only when the container was started with
  ``shared_preload_libraries=pg_stat_statements`` and the extension is created; failed
  statements are not counted; the tool's own statements are counted in the database they
  run in, like the real thing (so the descriptor's ``{db}`` filter is what excludes them).
* **ClickHouse** queues one ``system.query_log`` row per finished or failed query, made
  visible by ``SYSTEM FLUSH LOGS`` only; ``now64`` is a strictly increasing microsecond
  clock; a client's creation sends the driver's own two queries.

It models the *tool's* logic (what it sends, binds, reads and expands), not the engines'
real behaviour: a probe on it is never a probe of ``pg_stat_statements`` or ``query_log``.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import types
from collections.abc import Iterator
from typing import Any

POSTGRES_IMAGE_HINT = "postgres"
CLICKHOUSE_IMAGE_HINT = "clickhouse"


class FakeDriverError(Exception):
    """The fake drivers' base error (``psycopg.Error`` / ``ClickHouseError``)."""


def _db_of(url: str) -> str:
    path = url.split("://", 1)[1].split("/", 1)[1] if "/" in url.split("://", 1)[1] else ""
    return path.split("?", 1)[0]


def _port_of(url: str) -> int:
    hostport = url.split("://", 1)[1].split("/", 1)[0].rsplit("@", 1)[-1]
    return int(hostport.rsplit(":", 1)[1])


def _name(text: str) -> str:
    return text.strip().strip('"`')


class FakeServer:
    """One server: databases (SQLite in memory) and the statistics of its engine."""

    def __init__(self, kind: str, port: int, admin_db: str, preloaded: bool = True,
                 log_queries: bool = True, container: str | None = None) -> None:  # fmt: skip
        self.kind, self.port, self.container = kind, port, container
        self.admin_db = admin_db
        self.preloaded = preloaded  # postgres: shared_preload_libraries has pgss
        self.log_queries = log_queries  # clickhouse
        self.alive = True
        self.lock = threading.RLock()
        self.databases: dict[str, sqlite3.Connection] = {admin_db: self._new()}
        self.extensions: set[str] = set()  # databases that ran CREATE EXTENSION
        self.pgss: dict[tuple[str, str], int] = {}
        self.queue: list[tuple[str, str, int, str]] = []  # clickhouse (db, query, start, type)
        self.log: list[tuple[str, str, int, str]] = []
        self.events: list[tuple[str, str, str, Any]] = []  # (db, via, sql, params), everything
        self.flushes = 0

    @staticmethod
    def _new() -> sqlite3.Connection:
        return sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)

    def url(self, user: str = "lattice", password: str = "lattice", db: str | None = None) -> str:
        db = db or self.admin_db
        if self.kind == "postgres":
            return f"postgresql://{user}:{password}@127.0.0.1:{self.port}/{db}?sslmode=disable"
        return f"clickhouse://{user}:{password}@127.0.0.1:{self.port}/{db}"

    # -- tables a database holds (for the ClickHouse clone)

    def tables(self, db: str) -> list[tuple[str, str]]:
        rows = (
            self.databases[db]
            .execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name")
            .fetchall()
        )
        return [(r[0], r[1]) for r in rows]

    # -- Postgres

    def pg_run(self, db: str, sql: str, params: Any) -> tuple[list[str], list[tuple]]:
        """One statement of a Postgres connection to ``db``: (column names, rows)."""
        with self.lock:
            self._need_alive()
            self.events.append((db, "sql", sql, params))
            text = " ".join(sql.split())
            plist = list(params) if params is not None else None
            out: tuple[list[str], list[tuple]] = ([], [])
            m = re.match(r'CREATE DATABASE ("?[\w]+"?)(?: TEMPLATE ("?[\w]+"?))?$', text)
            if m:
                new, template = _name(m.group(1)), m.group(2)
                if new in self.databases:
                    raise FakeDriverError(f'database "{new}" already exists')
                self.databases[new] = self._new()
                if template:
                    self.databases[_name(template)].backup(self.databases[new])
            elif text.startswith("DROP DATABASE"):
                m = re.match(r'DROP DATABASE IF EXISTS ("?[\w]+"?)', text)
                gone = _name(m.group(1)) if m else ""
                if gone in self.databases and gone != self.admin_db:
                    self.databases.pop(gone).close()
            elif text.startswith("SELECT pg_terminate_backend"):
                out = (["pg_terminate_backend"], [])
            elif text == "SELECT 1":
                out = (["?column?"], [(1,)])
            elif text.startswith("CREATE EXTENSION"):
                self.extensions.add(db)
            elif text.startswith("SELECT count(*) FROM pg_stat_statements"):
                self._need_pgss(db)
                out = (["count"], [(len(self.pgss),)])
            elif text.startswith("SELECT pg_stat_statements_reset"):
                self._need_pgss(db)
                target = plist[0] if plist else None
                for key in [k for k in self.pgss if k[0] == target]:
                    del self.pgss[key]
                out = (["pg_stat_statements_reset"], [(None,)])
            elif text.startswith("SELECT query, calls FROM pg_stat_statements"):
                self._need_pgss(db)
                target = plist[0] if plist else None
                rows = sorted((q, c) for (d, q), c in self.pgss.items() if d == target)
                out = (["query", "calls"], rows)
            elif text.startswith("SELECT table_name FROM information_schema.tables"):
                out = (["table_name"], [(n,) for n, _ in self.tables(db)])
            else:
                out = self._sqlite(db, sql, plist)
            if self.preloaded:  # a statement that did not fail is counted in its database
                counted = self._normalise(sql)
                self.pgss[(db, counted)] = self.pgss.get((db, counted), 0) + 1
            return out

    def _need_alive(self) -> None:
        if not self.alive:  # a connection made before the server died
            raise FakeDriverError("server closed the connection unexpectedly")

    def _need_pgss(self, db: str) -> None:
        if not self.preloaded:
            raise FakeDriverError(
                'pg_stat_statements must be loaded via "shared_preload_libraries"'
            )
        if db not in self.extensions:
            raise FakeDriverError('relation "pg_stat_statements" does not exist')

    @staticmethod
    def _normalise(sql: str) -> str:
        n = 0

        def number(_m: re.Match[str]) -> str:
            nonlocal n
            n += 1
            return f"${n}"

        return " ".join(re.sub(r"%s", number, sql).split())

    def _sqlite(self, db: str, sql: str, params: list[Any] | None) -> tuple[list[str], list[tuple]]:
        text = sql.replace("%s", "?") if params is not None else sql
        if params is not None:
            text = text.replace("%%", "%")
        try:
            cur = self.databases[db].execute(text, params or [])
        except sqlite3.Error as err:
            raise FakeDriverError(str(err)) from err
        cols = [d[0] for d in cur.description or ()]
        return cols, [tuple(r) for r in cur.fetchall()] if cols else []

    # -- ClickHouse

    def ch_now(self) -> int:
        with self.lock:
            now = time.time_ns() // 1000
            last = getattr(self, "_last_us", 0)
            self._last_us = now if now > last else last + 1
            return int(self._last_us)

    def ch_record(self, db: str, text: str, start: int, ok: bool) -> None:
        if self.log_queries:
            self.queue.append(
                (db, text, start, "QueryFinish" if ok else "ExceptionWhileProcessing")
            )

    def ch_run(self, db: str, text: str, via: str) -> tuple[list[str], list[tuple]]:
        """One ClickHouse query (client-side parameters already formatted into ``text``)."""
        with self.lock:
            self._need_alive()
            self.events.append((db, via, text, None))
            start = self.ch_now()
            shown = f"{text} FORMAT Native" if via == "query" else text
            try:
                out = self._ch_exec(db, text)
            except FakeDriverError:
                self.ch_record(db, shown, start, False)
                raise
            self.ch_record(db, shown, start, True)
            return out

    def _ch_exec(self, db: str, text: str) -> tuple[list[str], list[tuple]]:
        flat = " ".join(text.split())
        if flat == "SYSTEM FLUSH LOGS":
            self.log.extend(self.queue)
            self.queue = []
            self.flushes += 1
            return [], []
        if flat.startswith("SELECT count() FROM system.query_log"):
            return ["count()"], [(0,)]
        if flat.startswith("SELECT toUnixTimestamp64Micro(now64(6))"):
            return ["now"], [(self.ch_now(),)]
        if flat.startswith("SELECT query, 1 FROM system.query_log"):
            want = re.search(r"current_database = '([^']*)'", flat)
            mark = re.search(r">= (\d+)", flat)
            assert want and mark, f"unexpected read: {flat}"
            since = int(mark.group(1))
            rows = sorted(
                (s, q) for d, q, s, t in self.log
                if d == want.group(1) and t != "QueryStart" and s >= since
            )  # fmt: skip
            return ["query", "1"], [(q, 1) for _, q in rows]
        if flat == "SELECT 1":
            return ["1"], [(1,)]
        if flat.startswith("SELECT version()") or flat.startswith("SELECT name, value, readonly"):
            return ["x"], [("24.8",)]
        m = re.match(r'CREATE DATABASE ("?\w+"?)$', flat)
        if m:
            self.databases[_name(m.group(1))] = self._new()
            return [], []
        m = re.match(r'DROP DATABASE IF EXISTS ("?\w+"?)', flat)
        if m:
            gone = _name(m.group(1))
            if gone in self.databases and gone != self.admin_db:
                self.databases.pop(gone).close()
            return [], []
        if flat.startswith("SELECT name, engine FROM system.tables WHERE database ="):
            m = re.search(r"database = '([^']*)'", flat)
            assert m
            return ["name", "engine"], [(n, "MergeTree") for n, _ in self.tables(m.group(1))]
        if flat.startswith("SELECT name FROM system.tables"):
            return ["name"], [(n,) for n, _ in self.tables(db)]
        m = re.match(r'CREATE TABLE ("?\w+"?)\."?(\w+)"? AS ("?\w+"?)\."?(\w+)"?$', flat)
        if m:
            dst, table, src = _name(m.group(1)), m.group(2), _name(m.group(3))
            ddl = dict(self.tables(src))[table]
            self.databases[dst].execute(ddl)
            return [], []
        m = re.match(r'INSERT INTO ("?\w+"?)\."?(\w+)"? SELECT \* FROM ("?\w+"?)\."?(\w+)"?$', flat)
        if m:
            dst, table, src = _name(m.group(1)), m.group(2), _name(m.group(3))
            rows = self.databases[src].execute(f'SELECT * FROM "{table}"').fetchall()
            for r in rows:
                marks = ", ".join("?" for _ in r)
                self.databases[dst].execute(f'INSERT INTO "{table}" VALUES ({marks})', r)
            return [], []
        # the app's own: DDL with an ENGINE clause, selects, inserts
        sql = re.sub(r"\)\s*ENGINE\s*=.*$", ")", flat) if flat.startswith("CREATE TABLE") else text
        try:
            cur = self.databases[db].execute(sql)
        except sqlite3.Error as err:
            raise FakeDriverError(str(err)) from err
        cols = [d[0] for d in cur.description or ()]
        return cols, [tuple(r) for r in cur.fetchall()] if cols else []


class World:
    """The machine: servers, the fake drivers and the fake ``docker``."""

    def __init__(self) -> None:
        self.servers: dict[int, FakeServer] = {}
        self.containers: dict[str, FakeServer] = {}
        self.removed: list[str] = []
        self.docker_calls: list[tuple[str, ...]] = []
        self._next_port = 41000
        self.fail_run = False

    # -- servers

    def add_server(self, kind: str, **kw: Any) -> FakeServer:
        self._next_port += 1
        admin = "postgres" if kind == "postgres" else "default"
        server = FakeServer(kind, self._next_port, admin, **kw)
        self.servers[server.port] = server
        return server

    def server_at(self, url: str) -> FakeServer:
        server = self.servers.get(_port_of(url))
        if server is None or not server.alive:
            raise FakeDriverError(f"connection to server at 127.0.0.1, port {_port_of(url)} "
                                  "failed: Connection refused")  # fmt: skip
        return server

    def live_containers(self) -> list[str]:
        return sorted(n for n, s in self.containers.items() if s.alive)

    def kill(self, container: str) -> None:
        """``docker kill``: the container's server stops answering."""
        self.containers[container].alive = False

    # -- the fake docker

    def docker(self, *args: str, timeout: float = 0.0) -> subprocess.CompletedProcess[str]:
        self.docker_calls.append(tuple(args))
        verb = args[0]
        ok = subprocess.CompletedProcess(list(args), 0, "", "")
        if verb == "run":
            if self.fail_run:
                return subprocess.CompletedProcess(list(args), 125, "", "docker: image not found")
            name = args[args.index("--name") + 1]
            valued = ("--name", "--label", "-p", "-e", "--tmpfs", "--ulimit")
            image_at = next(
                i for i, a in enumerate(args)
                if i > 0 and not a.startswith("-") and (":" in a or "/" in a)
                and args[i - 1] not in valued
            )  # fmt: skip
            image, rest = args[image_at], list(args[image_at + 1 :])
            kind = "clickhouse" if CLICKHOUSE_IMAGE_HINT in image else "postgres"
            server = self.add_server(
                kind,
                preloaded=any("shared_preload_libraries=pg_stat_statements" in a for a in rest),
                container=name,
            )
            self.containers[name] = server
            return subprocess.CompletedProcess(list(args), 0, name + "\n", "")
        if verb == "port":
            server = self.containers[args[1]]
            return subprocess.CompletedProcess(list(args), 0, f"127.0.0.1:{server.port}\n", "")
        if verb == "inspect":
            server = self.containers.get(args[-1])
            running = server is not None and server.alive
            return subprocess.CompletedProcess(
                list(args), 0, "true\n" if running else "false\n", ""
            )
        if verb == "rm":
            name = args[-1]
            server = self.containers.get(name)
            if server is not None:
                server.alive = False
                self.removed.append(name)
            return ok
        return ok  # logs and the rest


# ---- the fake driver modules -------------------------------------------------------------


def _psycopg_module(world: World) -> types.ModuleType:
    mod = types.ModuleType("psycopg")

    class Error(FakeDriverError):
        pass

    class OperationalError(Error):
        pass

    class _Column:
        def __init__(self, name: str) -> None:
            self.name = name

    class _Cursor:
        def __init__(self, conn: _Conn) -> None:
            self.conn = conn
            self.description: list[_Column] | None = None
            self._rows: list[tuple] = []

        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def execute(self, sql: str, params: Any = None) -> None:
            try:
                cols, rows = self.conn.server.pg_run(self.conn.db, sql, params)
            except FakeDriverError as err:
                raise Error(str(err)) from err
            self.description = [_Column(c) for c in cols] if cols else None
            self._rows = rows

        def fetchall(self) -> list[tuple]:
            return list(self._rows)

        def fetchone(self) -> tuple | None:
            return self._rows[0] if self._rows else None

    class _Conn:
        def __init__(self, server: FakeServer, db: str) -> None:
            self.server, self.db = server, db

        def cursor(self) -> _Cursor:
            return _Cursor(self)

        def execute(self, sql: str, params: Any = None) -> _Cursor:
            cur = _Cursor(self)
            cur.execute(sql, params)
            return cur

        def close(self) -> None:
            return None

    def connect(url: str, autocommit: bool = False, connect_timeout: int = 10, **_: Any) -> _Conn:
        try:
            server = world.server_at(url)
        except FakeDriverError as err:
            raise OperationalError(str(err)) from err
        db = _db_of(url) or server.admin_db
        if db not in server.databases:
            raise OperationalError(f'database "{db}" does not exist')
        return _Conn(server, db)

    mod.Error = Error  # type: ignore[attr-defined]
    mod.OperationalError = OperationalError  # type: ignore[attr-defined]
    mod.connect = connect  # type: ignore[attr-defined]
    return mod


def _clickhouse_modules(world: World) -> dict[str, types.ModuleType]:
    root = types.ModuleType("clickhouse_connect")
    driver = types.ModuleType("clickhouse_connect.driver")
    exc = types.ModuleType("clickhouse_connect.driver.exceptions")

    class ClickHouseError(FakeDriverError):
        pass

    class _Result:
        def __init__(self, cols: list[str], rows: list[tuple]) -> None:
            self.column_names, self.result_rows = cols, rows

    def literal(value: Any) -> str:
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, int | float):
            return str(value)
        return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"

    class _Client:
        def __init__(self, server: FakeServer, db: str) -> None:
            self.server, self.database = server, db
            # the driver's own queries when a client is made (they precede every window)
            for text in ("SELECT version(), timezone()",
                         "SELECT name, value, readonly FROM system.settings WHERE name "
                         "IN ('max_block_size')"):  # fmt: skip
                server.ch_run(db, text, "query")

        def _text(self, sql: str, parameters: Any) -> str:
            if parameters:
                return sql % tuple(literal(p) for p in parameters)
            return sql

        def query(self, sql: str, parameters: Any = None, settings: Any = None) -> _Result:
            try:
                cols, rows = self.server.ch_run(self.database, self._text(sql, parameters), "query")
            except FakeDriverError as err:
                raise ClickHouseError(str(err)) from err
            return _Result(cols, rows)

        def command(self, sql: str, parameters: Any = None) -> Any:
            try:
                _, rows = self.server.ch_run(self.database, self._text(sql, parameters), "command")
            except FakeDriverError as err:
                raise ClickHouseError(str(err)) from err
            return rows[0][0] if rows else None

        def insert(self, table: str, data: list[list[Any]], column_names: list[str]) -> None:
            names = ",".join(f"`{c}`" for c in column_names)
            text = f"INSERT INTO `{table}` ({names}) FORMAT Native"
            server = self.server
            with server.lock:
                server._need_alive()
                server.events.append((self.database, "insert", text, data))
                start = server.ch_now()
                try:
                    marks = ", ".join("?" for _ in column_names)
                    cols = ", ".join(f'"{c}"' for c in column_names)
                    server.databases[self.database].executemany(
                        f'INSERT INTO "{table}" ({cols}) VALUES ({marks})', data
                    )
                except sqlite3.Error as err:
                    server.ch_record(self.database, text, start, False)
                    raise ClickHouseError(str(err)) from err
                server.ch_record(self.database, text, start, True)

        def close(self) -> None:
            return None

    def get_client(host: str = "127.0.0.1", port: int = 8123, username: str = "default",
                   password: str = "", database: str = "default", **_: Any) -> _Client:  # fmt: skip
        server = world.servers.get(int(port))
        if server is None or not server.alive:
            raise ClickHouseError(f"cannot connect to {host}:{port}: Connection refused")
        if database not in server.databases:
            raise ClickHouseError(f"Database {database} does not exist")
        return _Client(server, database)

    exc.ClickHouseError = ClickHouseError  # type: ignore[attr-defined]
    root.get_client = get_client  # type: ignore[attr-defined]
    root.driver = driver  # type: ignore[attr-defined]
    driver.exceptions = exc  # type: ignore[attr-defined]
    return {
        "clickhouse_connect": root,
        "clickhouse_connect.driver": driver,
        "clickhouse_connect.driver.exceptions": exc,
    }


@contextlib.contextmanager
def installed(world: World | None = None) -> Iterator[World]:
    """``world``'s fake ``psycopg``, ``clickhouse_connect`` and ``docker`` in place for the
    block (``sys.modules``, ``lattice.spec.dbserver._docker``, ``shutil.which``), restored
    after. ``clickhouse``'s ``http``/``https`` URL schemes are untouched."""
    from lattice.spec import dbserver

    world = world or World()
    mods = {"psycopg": _psycopg_module(world), **_clickhouse_modules(world)}
    saved_mods = {name: sys.modules.get(name) for name in mods}
    saved_docker, saved_which = dbserver._docker, shutil.which

    def which(cmd: str, *a: Any, **k: Any) -> str | None:
        return "/usr/local/bin/docker" if cmd == "docker" else saved_which(cmd, *a, **k)

    sys.modules.update(mods)
    dbserver._docker = world.docker  # type: ignore[assignment]
    shutil.which = which  # type: ignore[assignment]
    try:
        yield world
    finally:
        shutil.which = saved_which  # type: ignore[assignment]
        dbserver._docker = saved_docker
        for name, old in saved_mods.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
