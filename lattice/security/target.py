"""The target: the app this module starts itself, on a loopback host with a throwaway
database. Guardrail 1 lives here -- there is no code path that targets anything else.

``start_target(project)`` resolves the project's harness exactly as ``lattice check`` does
(:func:`lattice.perf.measure.harness_from_project`), builds a fresh seeded throwaway
database (a SQLite file, or a database on a throwaway MySQL/Postgres container named
``lattice-sec-*``), starts the app on it and yields a :class:`Target`. The target's host is
``127.0.0.1`` for a process app, or ``in-process`` for an ASGI/WSGI app that never opens a
socket. :func:`ensure_safe_target` refuses anything else; :func:`target_from_url` always
refuses -- this module does not accept a URL or a remote host.

Everything the run writes (the database, logs, seed credentials) stays under the run's own
directory or a tempdir the database layer removes; nothing is written elsewhere.
"""

from __future__ import annotations

import contextlib
import sqlite3
import tempfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lattice.importer.transport import Answer, AppRuntime
from lattice.perf.measure import CONTAINER_PREFIX as PERF_PREFIX
from lattice.perf.measure import Harness, MeasureError, harness_from_project
from lattice.security.schema import AttackRequest

CONTAINER_PREFIX = "lattice-sec-"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
IN_PROCESS = "in-process"


class UnsafeTargetError(RuntimeError):
    """A target that is not the loopback app this module starts (guardrail 1)."""


def ensure_safe_target(host: str) -> None:
    """Raise :class:`UnsafeTargetError` unless ``host`` is loopback or the in-process
    sentinel. This is the single check guarding every run."""
    if host != IN_PROCESS and host not in LOOPBACK_HOSTS:
        raise UnsafeTargetError(
            f"refusing to attack host {host!r}: the security module targets only the app it "
            f"starts itself on a loopback host ({', '.join(sorted(LOOPBACK_HOSTS))}) or "
            f"in-process. It does not accept a URL or a remote host."
        )


def target_from_url(url: str) -> Target:  # noqa: ARG001
    """Always refuses: this module has no URL target (guardrail 1)."""
    raise UnsafeTargetError(
        f"refusing a URL target ({url!r}): the security module attacks only the app it starts "
        "itself on a loopback throwaway database, never a URL you pass it."
    )


@dataclass
class Target:
    """A started app on a loopback/in-process host and its throwaway database. ``request``
    sends one :class:`AttackRequest`; ``read`` / ``count`` inspect the database for the
    judge's oracles. Not created directly -- use :func:`start_target`."""

    host: str
    base_url: str
    _transport: Any
    _runtime: AppRuntime
    _login: Any
    _database: str
    _db_ref: Any  # a SQLite path, or a server-database URL
    _tokens: dict[str, str]

    # -- requests

    def _auth_headers(self, user: str | None) -> dict[str, str]:
        if user is None:
            return {}
        if user not in self._tokens:
            self._tokens[user] = self._runtime.auth.token(self._transport, user, self._login)
        return dict(self._runtime.auth.present(self._transport, self._tokens[user]))

    def request(self, req: AttackRequest) -> Answer:
        """Send one attack request. The request's own ``headers`` override the auth header
        for their names (so a malformed/absent token can be sent as-is)."""
        headers = self._auth_headers(req.as_user)
        if req.headers:
            headers.update(req.headers)
        return self._transport.request(
            req.method, req.path, params=req.params, json=req.json, headers=headers or None
        )

    # -- database (for the judge's row oracles; tolerant -- a read it cannot do is no finding)

    def read(self, table: str, where: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Rows of ``table`` matching ``where`` (all of them when ``where`` is None), as
        dicts. Returns [] if the database cannot be read."""
        try:
            if self._database == "sqlite":
                return _sqlite_read(Path(str(self._db_ref)), table, where)
            if self._database in ("mysql", "postgres", "clickhouse"):
                return _server_read(str(self._db_ref), self._database, table, where)
        except Exception:  # noqa: BLE001 -- a judge read must never raise into the run
            return []
        return []

    def count(self, table: str, where: Mapping[str, Any] | None = None) -> int:
        return len(self.read(table, where))


def _ident(name: str) -> str:
    """A safe SQL identifier (letters, digits, underscore only)."""
    if not name.replace("_", "").isalnum():
        raise ValueError(f"not a table/column name: {name!r}")
    return name


def _sqlite_read(path: Path, table: str, where: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    clause, params = "", []
    if where:
        clause = " WHERE " + " AND ".join(f'"{_ident(c)}" = ?' for c in where)
        params = list(where.values())
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(f'SELECT * FROM "{_ident(table)}"{clause}', params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _server_read(
    url: str, database: str, table: str, where: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    from lattice.spec.db import open_db

    db = open_db(url)
    try:
        quote = "`" if database == "mysql" else '"'
        clause, params = "", []
        if where:
            clause = " WHERE " + " AND ".join(f"{quote}{_ident(c)}{quote} = {db.ph}" for c in where)
            params = list(where.values())
        cols, rows = db._run(  # noqa: SLF001 -- the adapter's one read method
            f"SELECT * FROM {quote}{_ident(table)}{quote}{clause}", params or None
        )
        return [dict(zip(cols, r, strict=False)) for r in rows]
    finally:
        with contextlib.suppress(Exception):
            db.close()


@contextlib.contextmanager
def _open_db(h: Harness, tmp: Path) -> Iterator[tuple[Any, str, Any]]:
    """(make_db, database kind, cleanup-owner). Mirrors lattice.perf.measure._databases but
    keeps only what a loopback attack run needs."""
    from lattice.spec.check import as_db_factory

    if h.database == "sqlite":
        yield as_db_factory(h.db_factory), "sqlite", None
        return
    if h.database in ("mysql", "postgres", "clickhouse"):
        from lattice.spec.dbserver import DbServerError, ServerDatabases, server_for

        try:
            server, _owned = server_for(h.database, h.database_url, name_prefix=CONTAINER_PREFIX)
        except DbServerError as err:
            raise MeasureError(str(err)) from err
        dbs = ServerDatabases(server, h.db_factory)
        try:
            yield dbs, h.database, server
        finally:
            dbs.close()
            server.stop()
        return
    raise MeasureError(f"the security module does not support database {h.database!r}")


def _host_of(transport: Any) -> tuple[str, str]:
    """(host, base_url) of a started transport: a process app's 127.0.0.1:<port>, else the
    in-process sentinel."""
    app = getattr(transport, "app", None)
    base = getattr(app, "base_url", None)
    if base:
        host = getattr(app, "host", "")
        return host or IN_PROCESS, base
    return IN_PROCESS, "http://testserver"


@contextlib.contextmanager
def start_target_harness(
    h: Harness, seed_hook: Callable[[Any], None] | None = None
) -> Iterator[Target]:
    """Start the app of an already-resolved :class:`Harness` (what :func:`start_target`
    delegates to). Used directly by the tests against the in-repo app; the guardrail
    (:func:`ensure_safe_target`) applies here too. ``seed_hook(db_ref)`` runs after the
    throwaway database is built and before the app starts: the runner uses it to insert the
    spec scenarios' ``given`` rows (the resources attacks target), which the harness seed
    alone may not contain."""
    rt = h.runtime if h.runtime is not None else AppRuntime()
    with tempfile.TemporaryDirectory(prefix=CONTAINER_PREFIX) as d:
        with _open_db(h, Path(d)) as (make_db, database, _owner):
            db = make_db("seed", Path(d) / "attack.db")
            from lattice.spec.check import release_db

            if seed_hook is not None:
                seed_hook(db)
            app = h.app_factory(db)
            transport = None
            try:
                transport = rt.open(app)
                transport.__enter__()
                host, base_url = _host_of(transport)
                ensure_safe_target(host)
                yield Target(
                    host=host,
                    base_url=base_url,
                    _transport=transport,
                    _runtime=rt,
                    _login=h.login,
                    _database=database,
                    _db_ref=db,
                    _tokens={},
                )
            finally:
                if transport is not None:
                    with contextlib.suppress(Exception):
                        transport.__exit__(None, None, None)
                with contextlib.suppress(Exception):
                    rt.close(app)
                with contextlib.suppress(Exception):
                    release_db(make_db, db)


@contextlib.contextmanager
def start_target(
    project: Path | str, seed_hook: Callable[[Any], None] | None = None
) -> Iterator[Target]:
    """Start the project's app on a fresh seeded throwaway database and yield a
    :class:`Target`. Refuses (``UnsafeTargetError``) any host that is not loopback or
    in-process. Stops the app and drops the database on the way out, whatever happened."""
    with start_target_harness(harness_from_project(Path(project)), seed_hook) as target:
        yield target


__all__ = [
    "CONTAINER_PREFIX",
    "IN_PROCESS",
    "LOOPBACK_HOSTS",
    "PERF_PREFIX",
    "Target",
    "UnsafeTargetError",
    "ensure_safe_target",
    "start_target",
    "start_target_harness",
    "target_from_url",
]
