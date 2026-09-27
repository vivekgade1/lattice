"""S6 of the importer's evidence sources (spec 2026-09-27 importer §3.6): the SQL the
original app issues per request while a suite runs, and N+1 detection.

:class:`SqlTracer` hooks every SQLAlchemy engine created while it is installed (class
level events on :class:`sqlalchemy.Engine`):

* a ``connect`` listener sets ``sqlite3.Connection.set_trace_callback`` on each new
  DBAPI connection (and ``before_cursor_execute`` sets it on a connection opened before
  the tracer was installed), so every statement SQLite runs is seen -- including the
  ``BEGIN``/``COMMIT`` the driver issues itself -- with bound values inlined;
* ``before_cursor_execute`` records the statement as issued (``?`` placeholders) and
  its parameters, which are attached to the next traced statement.

Statements are attributed to the request the tracer is inside (:meth:`SqlTracer.case`,
the hook :func:`lattice.importer.verify.run_case` calls around the request only, so a
case's login is not part of it). :func:`trace_suite` runs a suite under a tracer.

:func:`n_plus_one` finds the same statement shape run once per row of an earlier read:
a SELECT shape repeated at least ``threshold`` times with distinct values.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, event

from lattice.importer.sql_lift import binding_key, shape
from lattice.importer.verify import AppFactory, Case, Login, run_suite

__all__ = [
    "NPlusOne",
    "case_bindings",
    "RequestTrace",
    "SqlTracer",
    "TracedStatement",
    "n_plus_one",
    "shape",
    "trace_suite",
]

CONTROL = frozenset({"BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "PRAGMA", "END"})


@dataclass(frozen=True)
class TracedStatement:
    """One statement SQLite ran: ``sql`` as it ran (bound values inlined, from the trace
    callback); ``template``/``params`` as SQLAlchemy issued it (None for statements the
    driver issued itself, like ``BEGIN``)."""

    seq: int
    sql: str
    template: str | None
    params: Any

    @property
    def kind(self) -> str:
        word = self.sql.lstrip().split(None, 1)
        return word[0].upper() if word else ""

    @property
    def is_query(self) -> bool:
        return self.kind not in CONTROL


@dataclass
class RequestTrace:
    case: str
    route: str | None
    method: str
    path: str
    statements: list[TracedStatement] = field(default_factory=list)
    status: int | None = None  # the response status (set by trace_suite)

    def queries(self) -> list[TracedStatement]:
        """The statements other than transaction control and pragmas."""
        return [s for s in self.statements if s.is_query]


class SqlTracer:
    """Installs the engine hooks (a context manager); :meth:`case` / :meth:`request`
    attribute what runs inside them. ``traces``: case name -> :class:`RequestTrace`."""

    def __init__(self) -> None:
        self.traces: dict[str, RequestTrace] = {}
        self._current: RequestTrace | None = None
        self._pending: tuple[str, Any, bool] | None = None
        self._many_index = 0
        self._seq = 0
        self._lock = threading.Lock()
        # sqlite3.Connection takes no weak reference; the tracer is short-lived
        self._hooked: dict[int, sqlite3.Connection] = {}
        self._installed = False

    # -- installation --

    def __enter__(self) -> SqlTracer:
        self.install()
        return self

    def __exit__(self, *exc: object) -> None:
        self.uninstall()

    def install(self) -> None:
        if not self._installed:
            event.listen(Engine, "connect", self._on_connect)
            event.listen(Engine, "before_cursor_execute", self._on_execute)
            self._installed = True

    def uninstall(self) -> None:
        if self._installed:
            event.remove(Engine, "connect", self._on_connect)
            event.remove(Engine, "before_cursor_execute", self._on_execute)
            self._installed = False
        for conn in self._hooked.values():
            try:
                conn.set_trace_callback(None)
            except sqlite3.ProgrammingError:  # already closed
                pass
        self._hooked.clear()
        self._current = None

    def _hook(self, dbapi: Any) -> None:
        if isinstance(dbapi, sqlite3.Connection) and id(dbapi) not in self._hooked:
            dbapi.set_trace_callback(self._on_trace)
            self._hooked[id(dbapi)] = dbapi

    def _on_connect(self, dbapi: Any, _record: Any) -> None:
        self._hook(dbapi)

    def _on_execute(
        self, conn: Any, _cursor: Any, statement: str, params: Any, _ctx: Any, many: bool
    ) -> None:
        self._hook(getattr(conn.connection, "dbapi_connection", None))
        with self._lock:
            self._pending = (statement, params, many)
            self._many_index = 0

    def _on_trace(self, sql: str) -> None:
        with self._lock:
            current = self._current
            word = sql.lstrip().split(None, 1)
            control = bool(word) and word[0].upper() in CONTROL
            template = params = None
            if not control and self._pending is not None:
                template, raw, many = self._pending
                if many:
                    rows = list(raw)
                    params = rows[self._many_index] if self._many_index < len(rows) else None
                    self._many_index += 1
                else:
                    params = raw
                    self._pending = None
                if isinstance(params, list):
                    params = tuple(params)
            if current is None:
                return
            self._seq += 1
            current.statements.append(TracedStatement(self._seq, sql, template, params))

    # -- attribution --

    @contextmanager
    def request(
        self, name: str, method: str = "", path: str = "", route: str | None = None
    ) -> Iterator[RequestTrace]:
        """Attribute the statements run inside to request ``name``."""
        trace = self.traces.setdefault(name, RequestTrace(name, route, method, path))
        with self._lock:
            self._current = trace
            self._pending = None
        try:
            yield trace
        finally:
            with self._lock:
                self._current = None

    @contextmanager
    def case(self, case: Case) -> Iterator[RequestTrace]:
        """The :func:`~lattice.importer.verify.run_case` hook."""
        with self.request(case.name, case.method, case.path, case.route) as trace:
            yield trace


def trace_suite(
    app_factory: AppFactory,
    dbs: Mapping[str, Path],
    cases: Sequence[Case],
    *,
    login: Login | None = None,
    prefix: str = "",
    hash_columns: Sequence[str] = ("hashed_password",),
) -> dict[str, RequestTrace]:
    """Run ``cases`` against the original app (as :func:`verify.run_suite` does) and
    return each case's SQL trace."""
    with SqlTracer() as tracer:
        records = run_suite(
            app_factory,
            dbs,
            cases,
            login=login,
            prefix=prefix,
            hash_columns=hash_columns,
            tracer=tracer,  # type: ignore[arg-type]  # run_case only calls tracer.case()
        )
    for name, record in records.items():
        if name in tracer.traces:
            tracer.traces[name].status = record.status
    return tracer.traces


def case_bindings(
    case: Case,
    path_template: str | None = None,
    *,
    caller: Any = None,
    defaults: Mapping[str, Any] | None = None,
) -> dict[Any, str]:
    """Which request input each value of ``case`` came from, keyed by
    :func:`~lattice.importer.sql_lift.binding_key` (the ``bindings`` of a
    :class:`~lattice.importer.sql_lift.LiftContext`): the caller's key is ``"user"``,
    then path parameters (matched against ``path_template``), body fields, query
    parameters and the ``defaults`` of parameters the case did not send, in that order
    of precedence. ``None`` is bound only when one field alone sent it."""
    layers: list[Mapping[str, Any]] = [defaults or {}, case.params or {}]
    if isinstance(case.json, Mapping):
        layers.append(case.json)
    layers.append(case.form or {})
    if path_template is not None:
        names = re.findall(r"{(\w+)(?::[^}]*)?}", path_template)
        pattern = re.sub(
            r"{\w+(?::[^}]*)?}",
            "([^/]+)",
            re.escape(path_template).replace(r"\{", "{").replace(r"\}", "}"),
        )
        m = re.fullmatch(pattern, case.path)
        if m:
            layers.append(dict(zip(names, m.groups(), strict=True)))
    out: dict[Any, str] = {}
    nulls = [n for layer in layers[1:] for n, v in layer.items() if v is None]
    if len(nulls) == 1:  # an explicit null is an input when only one field sent it
        out[None] = nulls[0]
    for layer in layers:
        for name, value in layer.items():
            if value is not None and not isinstance(value, dict | list):
                out[binding_key(value)] = name
    if caller is not None:
        out[binding_key(caller)] = "user"
    return out


# ---- shapes and N+1 --------------------------------------------------------------------------


@dataclass(frozen=True)
class NPlusOne:
    """``shape`` ran ``count`` times (``distinct`` different value sets) in one request,
    after a read of shape ``after`` -- one statement per row of that read."""

    case: str
    shape: str
    count: int
    distinct: int
    after: str | None


def n_plus_one(trace: RequestTrace, threshold: int = 3) -> list[NPlusOne]:
    """The SELECT shapes of ``trace`` run at least ``threshold`` times with at least
    ``threshold`` distinct values (a refresh of one row is not a repeat per row)."""
    selects = [s for s in trace.queries() if s.kind == "SELECT"]
    by_shape: dict[str, list[TracedStatement]] = {}
    for s in selects:
        by_shape.setdefault(shape(s.sql), []).append(s)
    found = []
    for sh, runs in by_shape.items():
        distinct = len({r.sql for r in runs})
        if len(runs) < threshold or distinct < threshold:
            continue
        first = runs[0].seq
        before = [shape(s.sql) for s in selects if s.seq < first and shape(s.sql) != sh]
        found.append(NPlusOne(trace.case, sh, len(runs), distinct, before[-1] if before else None))
    return found
