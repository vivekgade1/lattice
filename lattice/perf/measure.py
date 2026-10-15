"""Measuring a budget: the app on a throwaway database, seeded through its API, its
statements counted per request and its requests timed (spike 1a).

**The app** is started exactly as ``lattice check`` starts it (:func:`harness_from_project`
reads the project's ``studio.toml`` through the same code, ``lattice.cli._fill_check_args``
and the checker's own flag parser): the harness's ``db`` factory builds the seed once, every
size gets a fresh copy of it (a server database's clone, :class:`ServerDatabases`; a SQLite
file) and a fresh app (``app`` factory, served by the harness's ``runtime``: a process on a
free port, or FastAPI's in-process transport), and callers log in through the harness's
``login``. The app is stopped and the copy dropped after each size, and the database server
(a container named ``lattice-perf-*``) removed at the end.

**Seeding** goes through the API: each of the budget's seed steps is sent ``count`` times
(``"N"``: the size), as its ``user``; an answer that is not 2xx stops the run (exit 2).

**Counting** (only the measured request is counted; login and seeding come before):

* MySQL: the server's general log (``log_output=TABLE``, ``general_log=1``, switched back
  afterwards). Before each measured request the tool truncates ``mysql.general_log``; after
  the answer it reads the rows of every connection that is not one of the tool's own
  (:func:`app_statements`: the tool's connection ids are excluded, so its ``TRUNCATE`` and
  its read of the log are never counted) whose ``command_type`` is ``Query`` or
  ``Execute``: one row per statement, whether the driver sends text queries or prepared
  statements (``Prepare`` and ``Close stmt`` are not counted). This is the P0 method.
* SQLite with an in-process app (runtime ``fastapi`` or none): every ``sqlite3`` connection
  opened on the measured database file while the app runs gets a trace callback
  (``sqlite3.connect`` is wrapped for the duration); every traced statement is counted,
  transaction control included (``BEGIN``, ``COMMIT``).
* Any other server engine whose descriptor has a ``[perf]`` table with ``counter = "sql"``
  (Postgres: ``pg_stat_statements``; ClickHouse: ``system.query_log``): the engine's own
  statement statistics, read with the descriptor's SQL (:class:`SqlCounter`). The tool's own
  statements are never counted because every statement of the descriptor is bound to the
  measured database (its ``read`` must use ``{db}``). One window per request (``begin``, the
  request, ``flush``, ``read``) and one over the seed phase, which proves the counter is alive:
  an app whose seed requests left no statement in the measured database is ``unmeasured``.
  Process apps are counted too (the server counts, not the app's process).
* Not supported (the report says ``unmeasured``): a process app on SQLite (its SQLite
  library is in another process), an engine whose descriptor has no ``[perf]`` table, and
  a server whose statistics are not available (a ``setup`` or ``check`` statement failed).

**Latency** is the client-side wall time of the request (send to body read), per run.

**The index check** (``explain=ExplainOptions(...)``, ``--explain``; :mod:`lattice.perf.explain`):
per budget, at its largest measured size only, the first run's raw statements are explained on
the measured clone after the size's last counting window (after ``rt.close(app)``, before
``release_db``), so no statement of the check is ever counted. Without ``explain`` nothing of
this runs and every result is what it was.
"""

from __future__ import annotations

import contextlib
import re
import shlex
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lattice.perf import explain as explain_mod
from lattice.perf.budget import (
    Budget,
    BudgetFile,
    Report,
    SizeResult,
    expand,
    judge,
    pluck,
    substitute,
)

CONTAINER_PREFIX = "lattice-perf-"
COUNTED_MYSQL = ("Query", "Execute")


class MeasureError(RuntimeError):
    """The measurement could not run (the CLI's exit code 2)."""


class Unmeasured(Exception):  # noqa: N818 -- a verdict with a reason, not an error
    """Statements cannot be counted here: a report with the ``unmeasured`` verdict and this
    note (exit 2 with the report printed), not a failed run. Raised by :class:`SqlCounter`
    (its ``setup``/``check`` failed; the liveness guard), caught by :func:`measure`."""


# ---- the harness -----------------------------------------------------------------------


@dataclass
class Harness:
    app_factory: Callable[[Any], Any]
    db_factory: Callable[..., Any]
    runtime: Any = None  # an AppRuntime, or None (FastAPI in-process)
    login: Callable[[Any, str], str] | None = None
    database: str = "sqlite"
    database_url: str | None = None
    project: Path | None = None


def harness_from_project(project: Path) -> Harness:
    """The harness ``lattice check`` uses for ``project`` (its ``studio.toml``)."""
    from lattice.cli import _fill_check_args
    from lattice.importer.verify import form_login
    from lattice.spec.__main__ import _parser, _runtime, load
    from lattice.spec.check import CheckError
    from lattice.spec.process import process_runtime, returns_process_app

    project = project.resolve()
    if not project.is_dir():
        raise MeasureError(f"no project directory {project}")
    filled = _fill_check_args(project, ["--spec", "unused:SPEC"])  # the spec is not read
    if isinstance(filled, str):
        raise MeasureError(filled.replace("lattice check:", "lattice perf:"))
    try:
        args = _parser().parse_args(["check", *filled])
        login = load(args.login) if args.login else None
        if login is None and args.login_path:
            spec = load("spec:SPEC")
            spec = spec() if callable(spec) and not hasattr(spec, "users") else spec
            login = form_login(args.login_path, dict(spec.users))
        app_factory = load(args.app)
        runtime = _runtime(args.runtime)
        if runtime is None and returns_process_app(app_factory):
            runtime = process_runtime()
        return Harness(app_factory, load(args.db), runtime, login, args.database,
                       args.database_url, project)  # fmt: skip
    except (CheckError, ImportError, AttributeError) as err:
        raise MeasureError(f"the project's harness does not load: {err}") from err
    except SystemExit as err:  # argparse
        raise MeasureError(f"the project's harness flags are not valid ({err})") from err


# ---- counting --------------------------------------------------------------------------


def app_statements(rows: Iterable[tuple[Any, Any, Any]], tool_threads: set[int]) -> list[str]:
    """The app's statements among general_log rows ``(thread_id, command_type, argument)``:
    rows of connections that are not the tool's, of a counted command type."""
    out = []
    for thread, kind, arg in rows:
        if int(thread) in tool_threads:
            continue
        kind = kind.decode() if isinstance(kind, bytes) else str(kind)
        if kind not in COUNTED_MYSQL:
            continue
        out.append(arg.decode(errors="replace") if isinstance(arg, bytes) else str(arg))
    return out


class MysqlCounter:
    """Statements per request from the server's general log (see the module docstring)."""

    def __init__(self, server: Any) -> None:
        self.server = server
        self.conn: Any = None
        self.tool_threads: set[int] = set()
        self.saved: tuple[Any, Any] | None = None
        self.notes: list[str] = []

    def start(self) -> None:
        from lattice.spec.db import open_db

        self.conn = open_db(self.server.url)
        self.tool_threads = {int(self.conn.scalar("SELECT CONNECTION_ID()"))}
        with contextlib.suppress(Exception):  # the dbserver's admin connection (clones)
            self.tool_threads.add(int(self.server.admin().scalar("SELECT CONNECTION_ID()")))
        log, out = self.conn.query("SELECT @@GLOBAL.general_log, @@GLOBAL.log_output")[0]
        self.saved = (log, out)
        self.conn.execute("SET GLOBAL log_output = 'TABLE'")
        self.conn.execute("SET GLOBAL general_log = 1")
        if self.server.container is None:
            self.notes.append("general_log switched on on the given server for the run and "
                              f"restored after (general_log={log}, log_output={out})")  # fmt: skip

    def begin(self) -> None:
        self.conn.execute("TRUNCATE TABLE mysql.general_log")

    def end(self) -> list[str]:
        rows = self.conn.query("SELECT thread_id, command_type, argument FROM mysql.general_log")
        return app_statements(rows, self.tool_threads)

    def stop(self) -> None:
        if self.conn is None:
            return
        with contextlib.suppress(Exception):
            if self.saved is not None:
                log, out = self.saved
                self.conn.execute(f"SET GLOBAL general_log = {int(log)}")
                self.conn.execute("SET GLOBAL log_output = %s", (str(out),))
        with contextlib.suppress(Exception):
            self.conn.close()
        self.conn = None


class SqliteCounter:
    """Statements per request of an in-process app on a SQLite file: a trace callback on
    every connection opened on the measured file while installed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: list[str] = []
        self._watch: Path | None = None
        self._orig: Any = None
        self.notes: list[str] = []

    def watch(self, path: Path | str) -> None:
        self._watch = Path(path).resolve()

    def _trace(self, sql: str) -> None:
        with self._lock:
            self._seen.append(sql)

    def start(self) -> None:
        import sqlite3.dbapi2 as dbapi2

        orig = sqlite3.connect
        self._orig = (orig, dbapi2.connect)
        counter = self

        def connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
            conn = orig(database, *args, **kwargs)
            try:
                same = counter._watch is not None and Path(database).resolve() == counter._watch
            except (TypeError, OSError):
                same = False
            if same:
                conn.set_trace_callback(counter._trace)
            return conn

        sqlite3.connect = connect  # type: ignore[assignment]
        dbapi2.connect = connect  # type: ignore[assignment]

    def begin(self) -> None:
        with self._lock:
            self._seen = []

    def end(self) -> list[str]:
        with self._lock:
            return list(self._seen)

    def stop(self) -> None:
        if self._orig is not None:
            import sqlite3.dbapi2 as dbapi2

            sqlite3.connect, dbapi2.connect = self._orig  # type: ignore[assignment]
            self._orig = None


_BIND = re.compile(r"\{(\w+)\}")


def bind(text: str, placeholder: str, values: dict[str, Any]) -> tuple[str, list[Any]]:
    """A descriptor statement as ``(sql, params)``: every ``{name}`` becomes the engine's
    ``placeholder`` and its value is passed as a parameter, in order of occurrence (never
    spliced into the SQL). A literal ``%`` is written ``%%``: the driver reads it so when
    there are parameters, and it is read here when there are none."""
    names = _BIND.findall(text)
    sql = _BIND.sub(lambda _m: placeholder, text)
    if not names and placeholder == "%s":
        sql = sql.replace("%%", "%")
    return sql, [values[n] for n in names]


class SqlCounter:
    """Statements per request from the engine's own statistics, read with the SQL of the
    descriptor's ``[perf]`` table (``counter = "sql"``; see the module docstring).

    ``start(owned)`` opens the tool's connection (the server's admin database), runs
    ``setup`` only on a container the tool started, then ``check`` (a failure is
    :class:`Unmeasured`); ``watch`` keeps the measured database's name; a window is
    ``begin()`` (``begin`` statements, then ``mark``) and ``end()`` (``flush`` statements,
    then ``read``: each row's statement ``calls`` times, in row order); ``liveness`` is the
    seed phase's guard."""

    def __init__(self, server: Any, engine: Any) -> None:
        self.server = server
        self.engine = engine
        self.perf = engine.perf
        self.conn: Any = None
        self.db = ""
        self.mark: Any = None
        self.liveness_checked = 0
        self.notes: list[str] = []

    def _bind(self, text: str, **values: Any) -> tuple[str, list[Any]]:
        return bind(text, self.engine.sql.placeholder, values)

    def _run(self, text: str, **values: Any) -> None:
        sql, params = self._bind(text, **values)
        self.conn.execute(sql, params)

    def start(self, owned: bool) -> None:
        from lattice.spec.db import DatabaseError, open_db

        self.conn = open_db(self.server.url)
        for text in [*(self.perf.setup if owned else ()), *self.perf.check]:
            try:
                self._run(text)
            except DatabaseError as err:
                lines = str(err).strip().splitlines()
                raise Unmeasured(
                    f"unmeasured: statement counting on {self.engine.title} is not available "
                    f"on this server: {text!r} failed: {lines[0] if lines else 'no message'}"
                ) from err
        if not owned:
            self.notes.append("counting on a given server: setup statements were not run")

    def watch(self, url: Any) -> None:
        from lattice.spec.db import database_name

        self.db = str(database_name(str(url)))

    def begin(self) -> None:
        for text in self.perf.begin:
            self._run(text, db=self.db)
        if self.perf.mark:
            sql, params = self._bind(self.perf.mark)
            self.mark = self.conn.scalar(sql, params)

    def end(self) -> list[str]:
        for text in self.perf.flush:
            self._run(text, db=self.db)
        sql, params = self._bind(self.perf.read, db=self.db, mark=self.mark)
        out: list[str] = []
        for statement, calls in self.conn.query(sql, params):
            text = statement.decode(errors="replace") if isinstance(statement, bytes) else statement
            out.extend([str(text)] * int(calls))
        return out

    def liveness(self, n: int, requests: int) -> None:
        """After the seed phase's window: no statement at all means the counter (or the
        app's use of the database it was given) is dead, so nothing may pass on it."""
        if not self.end():
            raise Unmeasured(
                f"unmeasured: no statement of the app was seen in database {self.db} while "
                f"seeding (N={n}, {requests} seed requests); the app may not use the database "
                "it was given, or its statements are not logged"
            )
        self.liveness_checked += 1

    def stop(self) -> None:
        if self.conn is None:
            return
        with contextlib.suppress(Exception):
            self.conn.close()
        self.conn = None


_LITERAL = re.compile(r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.)*\"|\b\d+(?:\.\d+)?\b")


def shape(sql: str) -> str:
    """A statement with its literals replaced by ``?`` and its whitespace collapsed."""
    return re.sub(r"\s+", " ", _LITERAL.sub("?", sql)).strip()


def shapes_of(statements: list[str]) -> list[tuple[int, str]]:
    """(count, shape) in order of first occurrence."""
    counts = Counter(shape(s) for s in statements)
    seen: dict[str, None] = {}
    for s in statements:
        seen.setdefault(shape(s), None)
    return [(counts[s], s) for s in seen]


# ---- the run ---------------------------------------------------------------------------


def _in_process(runtime: Any) -> bool:
    from lattice.spec.check import PYTHON_FRAMEWORKS

    return runtime is None or (runtime.framework in PYTHON_FRAMEWORKS and not runtime.one_server)


def prepare(command: str, project: Path) -> None:
    """The budget file's ``prepare`` command, in the project directory (a failure is a
    MeasureError)."""
    try:
        out = subprocess.run(command, shell=True, cwd=str(project), capture_output=True,  # noqa: S602
                             text=True, timeout=900, check=False)  # fmt: skip
    except subprocess.TimeoutExpired as err:
        raise MeasureError(f"prepare {shlex.quote(command)} timed out") from err
    if out.returncode != 0:
        raise MeasureError(f"prepare {shlex.quote(command)} failed ({out.returncode}): "
                           f"{(out.stderr or out.stdout).strip()[-2000:]}")  # fmt: skip


@contextlib.contextmanager
def _databases(h: Harness, tmp: Path) -> Iterator[tuple[Callable[..., Any], Any, list[str]]]:
    """(make_db, counter or None, notes) for the harness's database; cleans up after."""
    from lattice.spec import engines
    from lattice.spec.check import as_db_factory

    notes: list[str] = []
    engine = None if h.database == "sqlite" else engines.find(h.database)
    perf = None if engine is None else engine.perf
    if engine is not None and perf is not None and perf.counter == "general_log":
        from lattice.spec.dbserver import DbServerError, ServerDatabases, server_for

        try:
            server, owned = server_for(engine.name, h.database_url, name_prefix=CONTAINER_PREFIX)
        except DbServerError as err:
            raise MeasureError(str(err)) from err
        dbs = ServerDatabases(server, h.db_factory)
        counter = MysqlCounter(server)
        try:
            counter.start()
            notes.append(f"database: {server.describe()}; statements from the general log "
                         "(Query + Execute rows of the app's connections)")  # fmt: skip
            yield dbs, counter, notes + counter.notes
        finally:
            counter.stop()
            dbs.close()
            server.stop()
        return
    if engine is not None and perf is not None:  # counter = "sql"
        from lattice.spec.dbserver import DbServerError, ServerDatabases, server_for

        try:
            server, owned = server_for(engine.name, h.database_url, name_prefix=CONTAINER_PREFIX,
                                       extra_args=perf.server_args)  # fmt: skip
        except DbServerError as err:
            raise MeasureError(str(err)) from err
        dbs = ServerDatabases(server, h.db_factory)
        sql_counter = SqlCounter(server, engine)
        try:
            try:
                sql_counter.start(owned)
            except Unmeasured as err:
                yield dbs, None, [str(err)]
                return
            notes.append(f"database: {server.describe()}; statements from {perf.source}")
            yield dbs, sql_counter, notes + sql_counter.notes
        finally:
            sql_counter.stop()
            dbs.close()
            server.stop()
        return
    if engine is not None:  # a server engine whose descriptor has no [perf] table
        yield (as_db_factory(h.db_factory), None,
               [f"unmeasured: statement counting on {engine.title} is not supported (its "
                "engine descriptor has no [perf] section)"])  # fmt: skip
        return
    if h.database == "sqlite":
        make = as_db_factory(h.db_factory)
        if _in_process(h.runtime):
            counter = SqliteCounter()
            counter.start()
            try:
                yield (
                    make,
                    counter,
                    [
                        *notes,
                        "database: SQLite file; statements from a sqlite3 "
                        "trace callback (every statement, BEGIN/COMMIT included)",
                    ],
                )
            finally:
                counter.stop()
            return
        yield (
            make,
            None,
            [
                *notes,
                "unmeasured: statement counting for a process app on SQLite "
                "is not supported (the app's SQLite library is in another process)",
            ],
        )
        return
    yield (as_db_factory(h.db_factory), None,
           [f"unmeasured: statement counting on {h.database} is not supported (MySQL and "
            "in-process SQLite only)"])  # fmt: skip


def _ok(status: int) -> bool:
    return 200 <= status < 300


def _explain_target(h: Harness, make_db: Any, db: Any, tmp: Path) -> str | None:
    """Why ``db`` is not a database this run made and may be explained (None: it is)."""
    live = getattr(make_db, "live", None)
    if live is not None:  # a server's clone: never a template or a database not cloned here
        from lattice.spec.db import database_name

        if database_name(str(db)) not in live:
            return "the database is not a clone this run made"
        return None
    try:
        if not Path(str(db)).resolve().is_relative_to(tmp.resolve()):
            return "the database file is not in the run's temporary directory"
    except OSError as err:
        return f"the database file cannot be resolved ({err})"
    return None


def _explain_size(
    budget: Budget, n: int, h: Harness, make_db: Any, db: Any, tmp: Path,
    raw: list[str], opts: Any,
) -> dict[str, Any]:  # fmt: skip
    """The explain object of the measured clone ``db`` (see :mod:`lattice.perf.explain`)."""
    from lattice.spec import engines

    min_rows = n if opts.min_rows is None else opts.min_rows
    engine = h.database
    if engine not in explain_mod.SUPPORTED:
        try:
            title = engines.find(engine).title
        except Exception:  # noqa: BLE001 -- an engine the registry does not know
            title = engine
        return explain_mod.unchecked(f"explain is not supported on {title}", engine, n, min_rows)
    refused = _explain_target(h, make_db, db, tmp)
    if refused is not None:
        raise MeasureError(f"budget {budget.id!r} N={n}: refusing to explain: {refused}")
    return explain_mod.explain_statements(engine, db, raw, n, opts.min_rows)


def measure_size(
    budget: Budget, n: int, h: Harness, make_db: Callable[[str, Path], Any], counter: Any,
    tmp: Path, runs: int, want_shapes: bool = False, explain_at: Any = None,
) -> SizeResult:  # fmt: skip
    """One size: a fresh database and app, the seed, then ``runs`` measured requests.
    ``explain_at`` (an :class:`~lattice.perf.explain.ExplainOptions`): explain the first
    run's statements on the clone once the app is closed (see the module docstring)."""
    from lattice.importer.transport import AppRuntime
    from lattice.spec.check import release_db

    rt = h.runtime if h.runtime is not None else AppRuntime()
    result = SizeResult(n)
    db = make_db("seed", tmp / f"{budget.id}-{n}.db")
    try:
        if isinstance(counter, (SqliteCounter, SqlCounter)):
            counter.watch(db)
        app = h.app_factory(db)
        try:
            with rt.open(app) as client:
                tokens: dict[str, str] = {}

                def headers(user: str | None) -> dict[str, str]:
                    if user is None:
                        return {}
                    if user not in tokens:
                        try:
                            tokens[user] = rt.auth.token(client, user, h.login)
                        except Exception as err:  # noqa: BLE001 -- cannot run
                            raise MeasureError(f"cannot log in as {user!r}: {err}") from err
                    return dict(rt.auth.present(client, tokens[user]))

                seeds = sum(step.times(n) for step in budget.steps)
                guard = isinstance(counter, SqlCounter) and seeds > 0
                if guard:
                    counter.begin()  # one window over the seed phase (login included)
                captured: dict[str, str] = {}  # seeded ids ({name} of a step's capture)
                for step in budget.steps:
                    for i in range(1, step.times(n) + 1):
                        new = lambda: str(uuid.uuid4())  # noqa: E731
                        path = substitute(expand(step.path, i, new), captured, path=True)
                        body = substitute(expand(step.json, i, new), captured)
                        r = client.request(step.method, path, json=body, headers=headers(step.user))
                        if not _ok(r.status_code):
                            raise MeasureError(
                                f"budget {budget.id!r} N={n}: seed step {step.method} {path} "
                                f"#{i} answered {r.status_code}: {r.text[:300]}"
                            )
                        if i == 1 and step.capture:
                            try:
                                payload = r.json()
                            except ValueError:
                                payload = None
                            for name, key in step.capture:
                                value = pluck(payload, key)
                                if value is None:
                                    raise MeasureError(
                                        f"budget {budget.id!r} N={n}: seed step "
                                        f"{step.method} {path} #1: capture {name}: response "
                                        f"has no {key}"
                                    )
                                captured[name] = value
                if guard:
                    counter.liveness(n, seeds)
                h_req = headers(budget.user)
                endpoint_path = substitute(budget.path, captured, path=True)
                for run in range(runs):
                    counter.begin()
                    t0 = time.perf_counter()
                    r = client.request(budget.method, endpoint_path, headers=h_req)
                    _ = r.content
                    ms = (time.perf_counter() - t0) * 1000
                    stmts = counter.end()
                    if not _ok(r.status_code):
                        raise MeasureError(f"budget {budget.id!r} N={n} run {run + 1}: "
                                           f"{budget.endpoint} answered {r.status_code}: "
                                           f"{r.text[:300]}")  # fmt: skip
                    result.statements.append(len(stmts))
                    result.latency_ms.append(round(ms, 3))
                    if run == 0 and want_shapes:
                        result.shapes = shapes_of(stmts)
                    if run == 0 and explain_at is not None:
                        result.raw_statements = list(stmts)
        finally:
            rt.close(app)
        if explain_at is not None and result.raw_statements is not None:
            # after the last counting window and the app's close, before the clone is dropped
            result.explain = _explain_size(budget, n, h, make_db, db, tmp,
                                           result.raw_statements, explain_at)  # fmt: skip
    finally:
        release_db(make_db, db)
    return result


def measure(
    budgets: BudgetFile, h: Harness, sizes: list[int] | None = None, runs: int = 10,
    ids: list[str] | None = None, want_shapes: bool = False, explain: Any = None,
) -> list[Report]:  # fmt: skip
    """Every budget (or those ``ids`` names) measured and judged. ``explain``: an
    :class:`~lattice.perf.explain.ExplainOptions` to add the index check (each report's
    ``explain`` object, at the budget's largest size); None: not run."""
    chosen = [b for b in budgets.budgets if not ids or b.id in ids]
    if ids:
        unknown = sorted(set(ids) - {b.id for b in budgets.budgets})
        if unknown:
            raise MeasureError(f"no budget with id {', '.join(unknown)}")
    if runs < 1:
        raise MeasureError("--runs must be at least 1")
    if sizes is not None and len(set(sizes)) < 2:
        raise MeasureError("--sizes needs two or more distinct sizes (the fit needs them)")
    if budgets.prepare and h.project is not None:
        prepare(budgets.prepare, h.project)
    reports: list[Report] = []
    with tempfile.TemporaryDirectory(prefix="lattice-perf-") as d, \
            _databases(h, Path(d)) as (make_db, counter, notes):  # fmt: skip
        for b in chosen:
            ns = sorted(set(sizes)) if sizes is not None else list(b.sizes)
            if counter is None:
                r = Report(b, [], notes=list(notes))
                if explain is not None:
                    r.explain = explain_mod.unchecked("the budget is unmeasured")
                reports.append(r)
                continue
            results = []
            checked = getattr(counter, "liveness_checked", 0)  # SqlCounter's guard
            try:
                for n in ns:
                    at = explain if n == max(ns) else None  # the index check: largest size
                    results.append(
                        measure_size(b, n, h, make_db, counter, Path(d), runs, want_shapes, at)
                    )
            except Unmeasured as err:  # the liveness guard: no size of this budget counts
                r = Report(b, [], notes=[*notes, str(err)])
                if explain is not None:
                    r.explain = explain_mod.unchecked("the budget is unmeasured")
                reports.append(r)
                continue
            after = []
            if isinstance(counter, SqlCounter) and counter.liveness_checked == checked:
                after.append("the counter's liveness was not checked (no seed request)")
            reports.append(
                judge(
                    b,
                    results,
                    notes=[
                        *notes,
                        f"{runs} run(s) per size, one fresh app and database per size",
                        *after,
                        *[
                            f"seeded id: {{{name}}} from seed step {k} (key {key})"
                            for k, step in enumerate(b.steps, 1)
                            for name, key in step.capture
                        ],
                    ],
                )
            )
            if explain is not None:
                last = results[-1]
                reports[-1].explain = last.explain or explain_mod.unchecked(
                    "no statement was explained", h.database, last.n
                )
    return reports


__all__ = [
    "CONTAINER_PREFIX",
    "Harness",
    "MeasureError",
    "MysqlCounter",
    "SqlCounter",
    "SqliteCounter",
    "Unmeasured",
    "app_statements",
    "bind",
    "harness_from_project",
    "measure",
    "measure_size",
    "shape",
]
