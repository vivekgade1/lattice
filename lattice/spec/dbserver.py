"""Database servers for the spec checker: a throwaway Docker container per check run, or
a server the caller already has; and the per-scenario databases cloned on it.

**A server** (:func:`server_for`) is, in order of preference:

* the URL given (``check(..., database_url=...)``, ``--database-url``), else
* ``$LATTICE_<DIALECT>_URL`` (``LATTICE_POSTGRES_URL``, ``LATTICE_MYSQL_URL``,
  ``LATTICE_CLICKHOUSE_URL``), else
* a new container (:func:`start_container`): ``docker run`` (the CLI; no SDK) of the
  dialect's image with no volume (the data directory is a tmpfs), its port published on
  a random free port of 127.0.0.1, waited on until a connection answers ``SELECT 1``,
  and removed (``docker rm -f -v``) when the check ends, when it fails to start, and by
  an ``atexit`` guard. Container names start ``lattice-``; with ``$LATTICE_CONTAINER_OWNER``
  set the container also carries the label ``lattice.owner=<value>`` (Lattice Studio's
  per-gate token, so a stopped gate's container is removed and nothing else).

On a given server the checker only creates and drops databases with names of its own
(``lattice_<run>_...``), so it never touches another database. The URL names an account
that may create databases (Postgres: ``CREATEDB``; MySQL: ``CREATE``/``DROP`` on
``*.*``), and its database is the one the admin connection opens (Postgres
``postgres``, ClickHouse ``default``).

**Databases** (:class:`ServerDatabases`, the server dialects' ``make_db``): the harness's
``db_factory`` runs once per variant, on an empty database created for it (the
*template*): it has the app create its schema and inserts the seed rows. Every scenario,
race and seed check then gets a **clone** of the template, dropped when it is done:

* Postgres: ``CREATE DATABASE clone TEMPLATE seed`` (other connections to the template
  are terminated first);
* MySQL: ``CREATE DATABASE clone``, then per table ``SHOW CREATE TABLE`` replayed in the
  clone (it keeps indexes, generated columns, CHECK and foreign keys -- ``CREATE TABLE
  ... LIKE`` keeps indexes and generated columns but drops foreign keys) and ``INSERT
  ... SELECT`` of the non-generated columns, with foreign key checks off; then views
  (their definitions re-pointed at the clone) and triggers;
* ClickHouse: ``CREATE DATABASE clone``, then per table ``CREATE TABLE clone.t AS
  seed.t`` and ``INSERT INTO clone.t SELECT * FROM seed.t``. Views, materialized views
  and dictionaries are not cloned (a CheckError names them).
"""

from __future__ import annotations

import atexit
import contextlib
import inspect
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from lattice.spec.db import (
    SERVER_DIALECTS,
    Database,
    DatabaseError,
    canonical_url,
    database_name,
    dialect_of,
    is_url,
    open_db,
    redact,
    with_database,
)

IMAGES = {
    "postgres": "postgres:16-alpine",
    "mysql": "mysql:8.4",
    "clickhouse": "clickhouse/clickhouse-server:24.8-alpine",
}
PORTS = {"postgres": 5432, "mysql": 3306, "clickhouse": 8123}
DATA_DIRS = {
    "postgres": "/var/lib/postgresql/data",
    "mysql": "/var/lib/mysql",
    "clickhouse": "/var/lib/clickhouse",
}
USER = "lattice"
PASSWORD = "lattice"  # noqa: S105 -- a throwaway container's, on 127.0.0.1 only
ADMIN_DB = {"postgres": "postgres", "mysql": "mysql", "clickhouse": "default"}
START_TIMEOUT = 120.0
NAME_PREFIX = "lattice-"
# a label value for the containers this process starts (``lattice.owner=<value>``): Lattice
# Studio sets it per gate run, so it removes exactly the containers a stopped gate left
OWNER_ENV = "LATTICE_CONTAINER_OWNER"


class DbServerError(RuntimeError):
    """A database server could not be started or reached."""


def docker_available() -> bool:
    """Whether the ``docker`` CLI is installed and its daemon answers."""
    if shutil.which("docker") is None:
        return False
    try:
        out = subprocess.run(  # noqa: S603, S607
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0


# ---- containers --------------------------------------------------------------------------

_LIVE: set[str] = set()
_STARTED: list[str] = []  # every container this process started, removed or not
_LIVE_LOCK = threading.Lock()


def _docker(*args: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def remove_container(name: str) -> None:
    """``docker rm -f -v name`` (best effort) and forget it."""
    with contextlib.suppress(Exception):
        _docker("rm", "-f", "-v", name, timeout=60)
    with _LIVE_LOCK:
        _LIVE.discard(name)


def live_containers() -> list[str]:
    """The containers this process started and has not removed."""
    with _LIVE_LOCK:
        return sorted(_LIVE)


def started_containers() -> list[str]:
    """Every container this process started (removed or not)."""
    with _LIVE_LOCK:
        return list(_STARTED)


@atexit.register
def remove_all() -> None:
    """Remove every container still running (the interpreter's exit guard)."""
    for name in live_containers():
        remove_container(name)


def _run_args(dialect: str) -> tuple[list[str], list[str]]:
    """(``docker run`` options, the server's own arguments)."""
    data = DATA_DIRS[dialect]
    opts = ["--tmpfs", f"{data}:rw"]
    if dialect == "postgres":
        opts += ["-e", f"POSTGRES_USER={USER}", "-e", f"POSTGRES_PASSWORD={PASSWORD}",
                 "-e", "POSTGRES_DB=postgres"]  # fmt: skip
        # a throwaway database: durability off, connections for layer X's clients
        args = ["-c", "fsync=off", "-c", "synchronous_commit=off", "-c", "full_page_writes=off",
                "-c", "max_connections=300"]  # fmt: skip
        return opts, args
    if dialect == "mysql":
        opts += ["-e", f"MYSQL_ROOT_PASSWORD={PASSWORD}"]
        args = ["--innodb-flush-log-at-trx-commit=0", "--skip-log-bin", "--sync-binlog=0",
                "--max-connections=300", "--innodb-doublewrite=OFF"]  # fmt: skip
        return opts, args
    opts += ["-e", f"CLICKHOUSE_USER={USER}", "-e", f"CLICKHOUSE_PASSWORD={PASSWORD}",
             "-e", "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1",
             "--ulimit", "nofile=262144:262144"]  # fmt: skip
    return opts, []


def _admin_user(dialect: str) -> str:
    return "root" if dialect == "mysql" else USER


def start_container(
    dialect: str,
    image: str | None = None,
    timeout: float = START_TIMEOUT,
    name_prefix: str = NAME_PREFIX,
) -> DbServer:
    """A new throwaway server of ``dialect`` (see the module docstring); its name starts
    ``name_prefix`` (``lattice-``; the perf run uses ``lattice-perf-``)."""
    if dialect not in SERVER_DIALECTS:
        raise DbServerError(f"{dialect!r} is not a server dialect")
    if shutil.which("docker") is None:
        raise DbServerError(
            f"--database {dialect} needs Docker (the docker CLI is not installed), or a "
            f"server URL: --database-url or $LATTICE_{dialect.upper()}_URL"
        )
    if not re.fullmatch(r"lattice-[a-z0-9-]*", name_prefix):
        raise DbServerError(f"container name prefix {name_prefix!r} must start 'lattice-'")
    name = f"{name_prefix}{dialect}-{secrets.token_hex(4)}"
    opts, args = _run_args(dialect)
    cport = PORTS[dialect]
    labels = ["--label", "lattice.spec=throwaway"]
    owner = os.environ.get(OWNER_ENV, "")
    if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", owner):  # who to clean up after (Studio's gates)
        labels += ["--label", f"lattice.owner={owner}"]
    cmd = ["run", "-d", "--name", name, *labels,
           "-p", f"127.0.0.1::{cport}", *opts, image or IMAGES[dialect], *args]  # fmt: skip
    with _LIVE_LOCK:
        _LIVE.add(name)
        _STARTED.append(name)
    try:
        out = _docker(*cmd, timeout=300)
        if out.returncode != 0:
            raise DbServerError(f"docker run {image or IMAGES[dialect]} failed: "
                                f"{out.stderr.strip() or out.stdout.strip()}")  # fmt: skip
        port = _host_port(name, cport)
        url = canonical_url(dialect, "127.0.0.1", port, _admin_user(dialect), PASSWORD,
                            ADMIN_DB[dialect])  # fmt: skip
        server = DbServer(dialect, url, container=name)
        server.wait_ready(timeout)
        return server
    except BaseException:
        remove_container(name)
        raise


def _host_port(name: str, cport: int) -> int:
    deadline = time.monotonic() + 30
    while True:
        out = _docker("port", name, f"{cport}/tcp", timeout=30)
        for line in out.stdout.splitlines():
            m = re.search(r":(\d+)\s*$", line.strip())
            if m:
                return int(m.group(1))
        if time.monotonic() > deadline:
            raise DbServerError(f"container {name}: no published port for {cport} "
                                f"({out.stderr.strip()})")  # fmt: skip
        time.sleep(0.2)


def _container_state(name: str) -> tuple[bool, str]:
    out = _docker("inspect", "-f", "{{.State.Running}}", name, timeout=30)
    running = out.stdout.strip() == "true"
    logs = ""
    if not running:
        logs = _docker("logs", "--tail", "40", name, timeout=30).stderr[-3000:]
    return running, logs


# ---- a server ----------------------------------------------------------------------------


@dataclass(eq=False)
class DbServer:
    """A database server the checker creates its databases on (``url``: the admin URL,
    canonical; ``container``: the throwaway container's name, None for a given
    server)."""

    dialect: str
    url: str
    container: str | None = None
    _admin: Database | None = field(default=None, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def wait_ready(self, timeout: float = START_TIMEOUT) -> None:
        deadline = time.monotonic() + timeout
        last = "no answer"
        checked = 0.0
        while True:
            try:
                with open_db(self.url) as db:
                    db.scalar("SELECT 1")
                return
            except (DatabaseError, OSError) as err:
                last = str(err).splitlines()[0] if str(err) else type(err).__name__
            now = time.monotonic()
            if self.container and now - checked > 2:
                checked = now
                running, logs = _container_state(self.container)
                if not running:
                    raise DbServerError(f"container {self.container} exited before it "
                                        f"answered; its log:\n{logs}")  # fmt: skip
            if now > deadline:
                raise DbServerError(f"{self.dialect} at {redact(self.url)} did not answer "
                                    f"within {timeout:g}s (last: {last})")  # fmt: skip
            time.sleep(0.25)

    def describe(self) -> str:
        where = f"container {self.container}" if self.container else redact(self.url)
        return f"{self.dialect} ({where})"

    def url_of(self, database: str) -> str:
        return with_database(self.url, database)

    def admin(self) -> Database:
        if self._admin is None:
            self._admin = open_db(self.url)
        return self._admin

    def _admin_do(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            try:
                self.admin().execute(sql, params)
            except DatabaseError:
                # a stale admin connection: once more on a new one
                self._reset_admin()
                self.admin().execute(sql, params)

    def _reset_admin(self) -> None:
        if self._admin is not None:
            with contextlib.suppress(Exception):
                self._admin.close()
        self._admin = None

    def q(self, name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise DbServerError(f"refusing a database name {name!r}")
        return f"`{name}`" if self.dialect == "mysql" else f'"{name}"'

    # -- databases

    def create(self, name: str) -> str:
        """Create the empty database ``name``; its canonical URL."""
        self._admin_do(f"CREATE DATABASE {self.q(name)}")
        return self.url_of(name)

    def drop(self, name: str) -> None:
        if self.dialect == "postgres":
            self._admin_do(f"DROP DATABASE IF EXISTS {self.q(name)} WITH (FORCE)")
        elif self.dialect == "clickhouse":
            self._admin_do(f"DROP DATABASE IF EXISTS {self.q(name)} SYNC")
        else:
            self._admin_do(f"DROP DATABASE IF EXISTS {self.q(name)}")

    def clone(self, template: str, name: str) -> str:
        """A new database ``name`` with ``template``'s tables and rows; its URL."""
        with self._lock:
            if self.dialect == "postgres":
                self._admin_do(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (template,),
                )
                self._admin_do(f"CREATE DATABASE {self.q(name)} TEMPLATE {self.q(template)}")
            elif self.dialect == "mysql":
                self._clone_mysql(template, name)
            else:
                self._clone_clickhouse(template, name)
        return self.url_of(name)

    def _clone_mysql(self, template: str, name: str) -> None:
        db = self.admin()
        src, dst = self.q(template), self.q(name)
        db.execute(f"CREATE DATABASE {dst}")
        try:
            objects = db.query(
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_schema = %s ORDER BY table_name",
                (template,),
            )
            tables = [t for t, kind in objects if kind == "BASE TABLE"]
            views = [t for t, kind in objects if kind == "VIEW"]
            db.execute("SET SESSION foreign_key_checks = 0")
            db.execute(f"USE {dst}")
            try:
                for t in tables:
                    ddl = db.query(f"SHOW CREATE TABLE {src}.`{t}`")[0][1]
                    db.execute(ddl)
                for t in tables:
                    cols = [
                        r[0]
                        for r in db.query(
                            "SELECT column_name FROM information_schema.columns WHERE "
                            "table_schema = %s AND table_name = %s AND extra NOT LIKE "
                            "'%%GENERATED%%' ORDER BY ordinal_position",
                            (template, t),
                        )
                    ]
                    names = ", ".join(f"`{c}`" for c in cols)
                    db.execute(
                        f"INSERT INTO {dst}.`{t}` ({names}) SELECT {names} FROM {src}.`{t}`"
                    )
                for v in views:
                    ddl = db.query(f"SHOW CREATE VIEW {src}.`{v}`")[0][1]
                    db.execute(ddl.replace(f"{src}.", f"{dst}."))
                for (trigger,) in db.query(
                    "SELECT trigger_name FROM information_schema.triggers "
                    "WHERE trigger_schema = %s",
                    (template,),
                ):
                    ddl = db.query(f"SHOW CREATE TRIGGER {src}.`{trigger}`")[0][2]
                    db.execute(ddl.replace(f"{src}.", f"{dst}."))
            finally:
                db.execute("SET SESSION foreign_key_checks = 1")
                db.execute(f"USE {self.q(database_name(self.url) or 'mysql')}")
        except BaseException:
            with contextlib.suppress(Exception):
                db.execute(f"DROP DATABASE IF EXISTS {dst}")
            raise

    def _clone_clickhouse(self, template: str, name: str) -> None:
        db = self.admin()
        src, dst = self.q(template), self.q(name)
        rows = db.query(
            "SELECT name, engine FROM system.tables WHERE database = %s ORDER BY name",
            (template,),
        )
        unsupported = [f"{t} ({e})" for t, e in rows if "View" in e or e == "Dictionary"]
        if unsupported:
            raise DbServerError("ClickHouse: views, materialized views and dictionaries are "
                                f"not cloned: {', '.join(unsupported)}")  # fmt: skip
        db.execute(f"CREATE DATABASE {dst}")
        try:
            for t, _ in rows:
                db.execute(f'CREATE TABLE {dst}."{t}" AS {src}."{t}"')
                db.execute(f'INSERT INTO {dst}."{t}" SELECT * FROM {src}."{t}"')
        except BaseException:
            with contextlib.suppress(Exception):
                db.execute(f"DROP DATABASE IF EXISTS {dst} SYNC")
            raise

    def terminate(self, name: str) -> None:
        """End other sessions on database ``name`` (Postgres; a no-op elsewhere)."""
        if self.dialect == "postgres":
            self._admin_do(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )

    def stop(self) -> None:
        """Close the admin connection; remove the container (if this server is one)."""
        self._reset_admin()
        if self.container is not None:
            remove_container(self.container)


def server_for(
    dialect: str, url: str | None = None, name_prefix: str = NAME_PREFIX
) -> tuple[DbServer, bool]:
    """The server of ``dialect`` (see the module docstring) and whether the check owns it
    (a container it started, named ``name_prefix...``, removed when the check ends)."""
    given = url or os.environ.get(f"LATTICE_{dialect.upper()}_URL")
    if given:
        found = dialect_of(given)
        if found != dialect:
            raise DbServerError(f"--database {dialect} with a {found} URL ({redact(given)})")
        server = DbServer(dialect, given)
        server.wait_ready(30)
        return server, False
    return start_container(dialect, name_prefix=name_prefix), True


# ---- the per-scenario databases ------------------------------------------------------------


def _arity_two(factory: Callable[..., Any]) -> bool:
    try:
        params = [
            p
            for p in inspect.signature(factory).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty
        ]
        return len(params) >= 2
    except (TypeError, ValueError):
        return True


class ServerDatabases:
    """A server dialect's ``make_db(variant, path) -> url``: each variant's seed built
    once (``db_factory(url)`` or ``db_factory(variant, url)`` on an empty database), then
    a clone of it per call; :meth:`release` drops a clone, :meth:`close` every database
    this run created. ``timings`` holds every clone's seconds."""

    def __init__(self, server: DbServer, db_factory: Callable[..., Any]) -> None:
        self.server = server
        self.dialect = server.dialect
        self.factory = db_factory
        self.two = _arity_two(db_factory)
        self.run = f"lattice_{secrets.token_hex(4)}"
        self.templates: dict[str, str] = {}
        self.live: set[str] = set()
        self.timings: list[float] = []
        self.seed_seconds: dict[str, float] = {}
        self._n = 0
        self._lock = threading.Lock()

    def _name(self, what: str) -> str:
        with self._lock:
            self._n += 1
            return f"{self.run}_{what}{self._n}"

    def template(self, variant: str) -> str:
        if variant in self.templates:
            return self.templates[variant]
        if not self.two and variant != "seed":
            from lattice.spec.check import CheckError

            raise CheckError(f"database variant {variant!r}: the db factory takes only a path")
        name = self._name("t")
        started = time.monotonic()
        url = self.server.create(name)
        self.templates[variant] = name
        result = self.factory(variant, url) if self.two else self.factory(url)
        if hasattr(result, "close") and not isinstance(result, str):
            result.close()
        self.server.terminate(name)
        self.seed_seconds[variant] = time.monotonic() - started
        return name

    def __call__(self, variant: str, path: Any = None) -> str:
        template = self.template(variant)
        name = self._name("c")
        started = time.monotonic()
        url = self.server.clone(template, name)
        self.timings.append(time.monotonic() - started)
        with self._lock:
            self.live.add(name)
        return url

    def release(self, ref: Any) -> None:
        """Drop a clone this run made (anything else: a no-op)."""
        if not is_url(ref):
            return
        name = database_name(str(ref))
        with self._lock:
            if name not in self.live:
                return
            self.live.discard(name)
        self.server.drop(name)  # type: ignore[arg-type]

    def close(self) -> None:
        for name in [*sorted(self.live), *self.templates.values()]:
            with contextlib.suppress(Exception):
                self.server.drop(name)
        self.live.clear()
        self.templates.clear()

    def timing_note(self) -> str:
        n = len(self.timings)
        if not n:
            return f"database: {self.server.describe()}; no clones"
        mean = sum(self.timings) / n * 1000
        seed = ", ".join(f"{v} {s:.2f}s" for v, s in self.seed_seconds.items())
        return (f"database: {self.server.describe()}; seed built once ({seed}), "
                f"{n} clone(s), {mean:.0f} ms each on average")  # fmt: skip


__all__ = [
    "IMAGES",
    "DbServer",
    "DbServerError",
    "ServerDatabases",
    "docker_available",
    "live_containers",
    "remove_all",
    "remove_container",
    "server_for",
    "started_containers",
    "start_container",
]
