"""API benchmark harness (spec 2026-09-27 §5.4): run a task's HTTP cases against
a FastAPI script (inside the sandbox) or a Lattice program (in process), count
SQL statements per request, and classify the result as correct / silent / loud.

Every case runs against a fresh copy of a pristine DB. The pristine DB is
written by the caller-supplied ``db_builder(path)`` (the CLI passes one that
copies the DB it built once per run).

Expectation semantics (``evaluate_case``), checked in this order:

1. the request did not crash (an unhandled exception is status 500);
2. ``status == expect_status``;
3. ``expect_json`` (if set) matches the JSON body (see ``_match_body``): on a 2xx
   expectation every response object (a One/Created row, each Page item) must have
   exactly the expected fields; error bodies are subset-matched;
4. ``queries <= max_queries`` (if set);
5. ``after_sql`` (if set) returns exactly ``after_expect`` from the DB after
   the request (see ``_match_rows``).

The pure helpers between the ``--- driver helpers ---`` markers use only the
standard library: their source is copied verbatim into the sandbox driver, so
both paths share one implementation.
"""

from __future__ import annotations

import contextvars
import dataclasses
import importlib
import inspect
import json
import secrets
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from bench.api_model import ApiTask, HttpCase
from bench.sandbox import run_python_script

DbBuilder = Callable[[Path], None]

_RESULTS_MARK = "__API_RESULTS_"  # + a per-run nonce + "__"
_NONCE_ENV = "LATTICE_API_NONCE"
_DRIVER_NAME = "_api_driver.py"
_PRISTINE = "pristine.db"
_KIT_NAME = "platform_kit.py"
_ERROR_LIMIT = 3000
SERVER_ERROR_PREFIX = "server error: "


@dataclass(frozen=True)
class CaseResult:
    name: str
    kind: str  # "happy" | "trap"
    family: str
    passed: bool
    status: int
    queries: int
    latency_ms: float
    reason: str  # "" when passed; else the first failed expectation


@dataclass(frozen=True)
class CaseResults:
    cases: tuple[CaseResult, ...]
    started: bool
    start_error: str = ""
    # Lattice only: the checker's diagnostics when it rejected the program.
    diagnostics: tuple[Any, ...] = ()
    timed_out: bool = False


# --- driver helpers (stdlib only; copied into the sandbox driver) ---


def _is_counted_statement(sql: str) -> bool:
    """True for a data statement; transaction control / pragmas are not counted."""
    head = sql.lstrip().split(None, 1)
    if not head:
        return False
    return head[0].upper() in ("SELECT", "INSERT", "UPDATE", "DELETE", "WITH", "REPLACE")


def _short(value: object) -> str:
    text = json.dumps(value, default=str) if not isinstance(value, str) else repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def _match_scalar(expected: object, actual: object, where: str) -> str | None:
    import math

    if isinstance(expected, bool) or isinstance(actual, bool):
        if type(expected) is type(actual) and expected == actual:
            return None
    elif isinstance(expected, int | float) and isinstance(actual, int | float):
        if math.isclose(expected, actual, rel_tol=1e-9, abs_tol=1e-6):
            return None
    elif expected == actual:
        return None
    return f"{where}: expected {_short(expected)}, got {_short(actual)}"


def _match_value(
    expected: object, actual: object, ordered: bool, where: str = "$", exact: bool = False
) -> str | None:
    """None when ``actual`` matches ``expected``; else a one-line reason.

    Objects: every expected key present and matching; with ``exact`` the actual
    object must also have no other key (applies to nested objects too). Lists: same
    length, element-wise match -- positional when ``ordered``, otherwise a perfect
    matching between expected and actual elements (multiset).
    """
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return f"{where}: expected an object, got {_short(actual)}"
        for key, value in expected.items():
            if key not in actual:
                return f"{where}.{key}: missing"
            reason = _match_value(value, actual[key], ordered, f"{where}.{key}", exact)
            if reason is not None:
                return reason
        if exact:
            for key in actual:
                if key not in expected:
                    return f"{where}: unexpected field {key!r}"
        return None
    if isinstance(expected, list | tuple):
        if not isinstance(actual, list | tuple):
            return f"{where}: expected a list, got {_short(actual)}"
        if len(expected) != len(actual):
            return f"{where}: expected {len(expected)} items, got {len(actual)}"
        if ordered:
            for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
                reason = _match_value(e, a, ordered, f"{where}[{i}]", exact)
                if reason is not None:
                    return reason
            return None
        return _match_unordered(list(expected), list(actual), where, exact)
    return _match_scalar(expected, actual, where)


def _match_unordered(expected: list, actual: list, where: str, exact: bool = False) -> str | None:
    """Perfect bipartite matching (Kuhn's algorithm) of expected to actual items."""
    fits = [
        [j for j, a in enumerate(actual) if _match_value(e, a, False, where, exact) is None]
        for e in expected
    ]
    owner: dict[int, int] = {}

    def assign(i: int, seen: set[int]) -> bool:
        for j in fits[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in owner or assign(owner[j], seen):
                owner[j] = i
                return True
        return False

    for i in range(len(expected)):
        if not assign(i, set()):
            return f"{where}: no matching item for expected {_short(expected[i])} (unordered)"
    return None


def _match_body(expected: object, actual: object, ordered: bool, success: bool) -> str | None:
    """Match a response body. A success (2xx) body is a response object -- a One /
    Created row, or a page whose ``items`` are rows -- and every row must have
    exactly the expected fields (a leaked or extra field fails). The page envelope
    may carry only ``items``, ``limit`` and ``offset``; ``limit``/``offset`` are
    checked when expected. Error bodies are subset-matched (their ``detail`` text is
    free-form)."""
    if not success:
        return _match_value(expected, actual, ordered)
    envelope = ("items", "limit", "offset")
    if isinstance(expected, dict) and expected and all(k in envelope for k in expected):
        if not isinstance(actual, dict):
            return f"$: expected an object, got {_short(actual)}"
        for key in actual:
            if key not in ("items", "limit", "offset"):
                return f"$: unexpected field {key!r}"
        for key, value in expected.items():
            if key not in actual:
                return f"$.{key}: missing"
            reason = _match_value(value, actual[key], ordered, f"$.{key}", key == "items")
            if reason is not None:
                return reason
        return None
    return _match_value(expected, actual, ordered, "$", True)


def _match_rows(expected: list, actual: list, ordered: bool) -> str | None:
    """Compare DB rows: a bare scalar is a one-column row; columns positional;
    row order significant only when ``ordered``."""

    def as_record(row: object) -> dict:
        cells = list(row) if isinstance(row, list | tuple) else [row]
        record: dict = {str(i): cell for i, cell in enumerate(cells)}
        record["#columns"] = len(cells)
        return record

    return _match_value(
        [as_record(r) for r in expected], [as_record(r) for r in actual], ordered, "after_sql"
    )


def _server_error(status: int, body: object, text: str, raised: str) -> str:
    """The crash text of one response ("" for none), the same for both arms.

    ``raised`` is the server-side traceback when the app raised (captured around
    the ASGI app, so an exception handler that turns it into a 500 hides nothing);
    otherwise a 5xx response is a crash whose text is the body's ``detail`` string
    (the Lattice runtime puts the rendered runtime error there) or the raw body.
    Nothing here comes from the case's expectations."""
    if raised:
        return raised
    if status < 500:
        return ""
    if isinstance(body, dict) and isinstance(body.get("detail"), str) and body["detail"]:
        return body["detail"]
    return f"the app responded with status {status} and body {text[:500]!r}"


def evaluate_case(
    case: dict,
    status: int,
    body: object,
    queries: int,
    after_rows: list | None,
    crash: str,
) -> tuple[bool, str]:
    """(passed, reason) for one case; ``case`` is ``dataclasses.asdict(HttpCase)``."""
    if crash:
        return False, "server error: " + crash  # == SERVER_ERROR_PREFIX (driver copy)
    if status != case["expect_status"]:
        return False, f"status {status}, expected {case['expect_status']}"
    if case.get("expect_json") is not None:
        if body is None:
            return False, "response body is not JSON"
        success = 200 <= int(case["expect_status"]) < 300
        reason = _match_body(case["expect_json"], body, bool(case.get("ordered")), success)
        if reason is not None:
            return False, "json mismatch at " + reason
    limit = case.get("max_queries")
    if limit is not None and queries > limit:
        return False, f"{queries} queries, max {limit}"
    if case.get("after_sql"):
        if after_rows is None:
            return False, "after_sql could not be run"
        reason = _match_rows(
            list(case.get("after_expect") or []), after_rows, bool(case.get("ordered"))
        )
        if reason is not None:
            return False, "db state mismatch at " + reason
    return True, ""


def resolve_expected_status(case: dict, status: int) -> dict:
    """``case`` with ``expect_status_any`` applied (spec §5.6): when the case lists
    alternative statuses and ``status`` is one of them, it stands in for
    ``expect_status``; otherwise (always, for explicit cases) ``case`` itself."""
    allowed = case.get("expect_status_any")
    if allowed and status in allowed:
        return {**case, "expect_status": status}
    return case


def evaluate_http_case(
    case: dict,
    status: int,
    body: object,
    queries: int,
    after_rows: list | None,
    crash: str,
) -> tuple[bool, str]:
    """``evaluate_case`` on ``resolve_expected_status(case, status)``: every other
    expectation is checked unchanged."""
    case = resolve_expected_status(case, status)
    return evaluate_case(case, status, body, queries, after_rows, crash)


# --- end driver helpers ---

_HELPERS = (
    _is_counted_statement,
    _short,
    _match_scalar,
    _match_value,
    _match_unordered,
    _match_body,
    _match_rows,
    _server_error,
    evaluate_case,
    resolve_expected_status,
    evaluate_http_case,
)

_DRIVER_TEMPLATE = '''\
"""Generated API case driver: runs solution.app against each case (sandboxed)."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import traceback

sys.path.insert(0, os.getcwd())
sys.dont_write_bytecode = True

# The results marker carries a per-run nonce, passed in an environment variable that
# is read and removed before any model code runs (never a file in the app's working
# directory), so the app cannot print a fake result line.
MARK = __MARK_PREFIX__ + os.environ.pop(__NONCE_ENV__, "") + "__"
CASES = json.loads(__CASES__)
ERROR_LIMIT = __ERROR_LIMIT__
with open(__PRISTINE__, "rb") as _f:
    PRISTINE = _f.read()

__HELPERS__

_real_connect = sqlite3.connect
_counter = [0]


def _trace(sql):
    if _is_counted_statement(sql):
        _counter[0] += 1


def _counting_connect(*args, **kwargs):
    conn = _real_connect(*args, **kwargs)
    conn.set_trace_callback(_trace)
    return conn


sqlite3.connect = _counting_connect


def _tail(text):
    return text[-ERROR_LIMIT:]


def _fresh_db():
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            os.remove("app.db" + suffix)
        except FileNotFoundError:
            pass
    with open("app.db", "wb") as f:
        f.write(PRISTINE)


def _fresh_app():
    for name in ("solution", "platform_kit"):
        sys.modules.pop(name, None)
    import platform_kit

    original = getattr(platform_kit, "_on_connect", None)

    def _on_connect(conn):
        if original is not None:
            original(conn)
        conn.set_trace_callback(_trace)

    platform_kit._on_connect = _on_connect
    import solution

    app = getattr(solution, "app", None)
    return app if app is not None else platform_kit.app


def _run_case(case, app):
    from starlette.testclient import TestClient

    headers = {}
    if case["token"] is not None:
        headers["Authorization"] = "Bearer " + case["token"]
    status, body, crash = 500, None, ""
    raised = []

    async def guarded(scope, receive, send):
        # Starlette's ServerErrorMiddleware re-raises after sending the 500, so the
        # server-side traceback is captured here; the client just sees the 500.
        try:
            await app(scope, receive, send)
        except BaseException:
            raised.append(_tail(traceback.format_exc()))
            raise

    _counter[0] = 0
    started = time.perf_counter()
    try:
        with TestClient(guarded, raise_server_exceptions=False) as client:
            response = client.request(
                case["method"],
                case["path"],
                params=case["query"] or None,
                json=case["json"],
                headers=headers,
            )
        status = response.status_code
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = None
        crash = _server_error(status, body, response.text, raised[-1] if raised else "")
    except BaseException:
        crash = raised[-1] if raised else _tail(traceback.format_exc())
    latency_ms = (time.perf_counter() - started) * 1000.0
    queries = _counter[0]
    after_rows = None
    if case["after_sql"] and not crash:
        try:
            conn = _real_connect("app.db")
            try:
                after_rows = [list(r) for r in conn.execute(case["after_sql"]).fetchall()]
            finally:
                conn.close()
        except Exception:
            after_rows = None
    case = resolve_expected_status(case, status)
    passed, reason = evaluate_case(case, status, body, queries, after_rows, crash)
    return {
        "name": case["name"], "kind": case["kind"], "family": case["family"],
        "passed": passed, "status": status, "queries": queries,
        "latency_ms": latency_ms, "reason": reason,
    }


def main():
    _fresh_db()
    try:
        _fresh_app()
    except BaseException:
        print(MARK + json.dumps({"started": False, "error": _tail(traceback.format_exc())}))
        return
    results = []
    for case in CASES:
        _fresh_db()
        try:
            app = _fresh_app()
        except BaseException:
            crash = _tail(traceback.format_exc())
            results.append({
                "name": case["name"], "kind": case["kind"], "family": case["family"],
                "passed": False, "status": 500, "queries": 0, "latency_ms": 0.0,
                "reason": "server error: " + crash,
            })
            continue
        results.append(_run_case(case, app))
    print(MARK + json.dumps({"started": True, "cases": results}))


main()
'''


def _case_dict(case: HttpCase) -> dict:
    data = dataclasses.asdict(case)
    data["query"] = dict(case.query or {})
    return data


def driver_source(task: ApiTask) -> str:
    """The self-contained driver script run inside the sandbox for ``task``."""
    helpers = "\n\n".join(inspect.getsource(fn) for fn in _HELPERS)
    cases = json.dumps([_case_dict(c) for c in task.cases], default=str)
    return (
        _DRIVER_TEMPLATE.replace("__HELPERS__", helpers)
        .replace("__MARK_PREFIX__", repr(_RESULTS_MARK))
        .replace("__NONCE_ENV__", repr(_NONCE_ENV))
        .replace("__CASES__", repr(cases))
        .replace("__ERROR_LIMIT__", str(_ERROR_LIMIT))
        .replace("__PRISTINE__", repr(_PRISTINE))
    )


def _parse_driver_output(stdout: str, nonce: str) -> dict | None:
    mark = f"{_RESULTS_MARK}{nonce}__"
    for line in reversed(stdout.splitlines()):
        if line.startswith(mark):
            try:
                return json.loads(line[len(mark) :])
            except ValueError:
                return None
    return None


def run_cases_fastapi(
    script: str,
    workdir: Path,
    task: ApiTask,
    *,
    db_builder: DbBuilder,
    kit_path: Path,
    timeout_s: float = 60,
) -> CaseResults:
    """Run ``task``'s cases against a FastAPI ``script`` inside the sandbox.

    Writes ``solution.py``, ``platform_kit.py`` (copied from ``kit_path``) and a
    pristine DB into ``workdir``, then runs a generated driver under
    ``bench.sandbox.run_python_script``. Per case the driver restores ``app.db``
    from the pristine bytes (read before any model code runs), re-imports
    ``platform_kit`` and ``solution`` fresh, counts data statements on every
    SQLite connection (``platform_kit._on_connect`` is wrapped and
    ``sqlite3.connect`` is patched, both installing a trace callback), and sends
    the request through Starlette's ``TestClient``.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "solution.py").write_text(script)
    shutil.copyfile(kit_path, workdir / _KIT_NAME)
    pristine = workdir / _PRISTINE
    pristine.unlink(missing_ok=True)
    db_builder(pristine)
    nonce = secrets.token_hex(16)
    result = run_python_script(
        driver_source(task),
        workdir,
        timeout_s=timeout_s,
        script_name=_DRIVER_NAME,
        env_extra={_NONCE_ENV: nonce},
    )
    payload = _parse_driver_output(result.stdout, nonce)
    if payload is None:
        if result.timed_out:
            return CaseResults(
                cases=(),
                started=False,
                start_error=f"the app exceeded the {timeout_s:g}s time limit",
                timed_out=True,
            )
        error = result.stderr.strip() or f"driver exited with code {result.returncode}"
        return CaseResults(cases=(), started=False, start_error=error[-_ERROR_LIMIT:])
    if not payload.get("started"):
        return CaseResults(cases=(), started=False, start_error=str(payload.get("error", "")))
    return CaseResults(
        cases=tuple(CaseResult(**c) for c in payload["cases"]),
        started=True,
    )


# The Lattice runtime's ``on_connect`` hook is module-global state; serialize
# every in-process run that replaces it.
_LATTICE_LOCK = threading.Lock()

# The run a thread's work belongs to: set in each run's worker thread and carried by
# the context into the threads serving its requests. A run's counting hook counts only
# connections opened under its own token, so a timed-out (abandoned, still running)
# worker never adds its queries to a later run's counts.
_RUN_TOKEN: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "lattice_api_run", default=None
)


def _render_start_error(exc: BaseException) -> str:
    render = getattr(exc, "render", None)
    if callable(render):
        try:
            return str(render())
        except Exception:
            pass
    return f"{type(exc).__name__}: {exc}"


def _run_case_in_process(app: Any, case: HttpCase, db_path: Path, counter: list[int]) -> CaseResult:
    import traceback

    from starlette.testclient import TestClient

    headers = {"Authorization": f"Bearer {case.token}"} if case.token is not None else {}
    status, body, crash = 500, None, ""
    raised: list[str] = []

    async def guarded(scope: Any, receive: Any, send: Any) -> None:
        # Same capture as the sandbox driver: the arms see a server error alike.
        try:
            await app(scope, receive, send)
        except BaseException:
            raised.append(traceback.format_exc()[-_ERROR_LIMIT:])
            raise

    counter[0] = 0
    started = time.perf_counter()
    try:
        with TestClient(guarded, raise_server_exceptions=False) as client:
            response = client.request(
                case.method,
                case.path,
                params=dict(case.query) or None,
                json=case.json,
                headers=headers,
            )
        status = response.status_code
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = None
        crash = _server_error(status, body, response.text, raised[-1] if raised else "")
    except Exception:
        crash = raised[-1] if raised else traceback.format_exc()[-_ERROR_LIMIT:]
    latency_ms = (time.perf_counter() - started) * 1000.0
    queries = counter[0]
    after_rows = None
    if case.after_sql and not crash:
        try:
            conn = sqlite3.connect(db_path)
            try:
                after_rows = [list(r) for r in conn.execute(case.after_sql).fetchall()]
            finally:
                conn.close()
        except sqlite3.Error:
            after_rows = None
    passed, reason = evaluate_http_case(_case_dict(case), status, body, queries, after_rows, crash)
    return CaseResult(
        name=case.name,
        kind=case.kind,
        family=case.family,
        passed=passed,
        status=status,
        queries=queries,
        latency_ms=latency_ms,
        reason=reason,
    )


def _fresh_copy(pristine: Path, target: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(f"{target}{suffix}").unlink(missing_ok=True)
    shutil.copyfile(pristine, target)


def run_cases_lattice(
    source: str,
    workdir: Path,
    task: ApiTask,
    *,
    db_builder: DbBuilder,
    profile: Any,
    serve: Callable[..., tuple[Any, Any]] | None = None,
    hooks: Any = None,
    timeout_s: float = 60,
) -> CaseResults:
    """Run ``task``'s cases against a Lattice API program, in process.

    ``serve(source, profile, db_path) -> (CheckResult, app | None)`` defaults to
    ``lattice.api.runtime.serve_source``; it is called once per case with a
    fresh DB copy. ``hooks`` is the object whose ``on_connect(conn)`` the runtime
    calls for every new SQLite connection (default: the runtime module); it is
    replaced for the duration of the run with a wrapper that installs the
    statement-counting trace callback, then restored.

    The cases run in a worker thread with a wall-clock budget of ``timeout_s`` for
    the whole run (like the sandboxed FastAPI driver's): past it the run is a loud
    timeout (not started, ``timed_out``). A hung worker cannot be killed; it is a
    daemon thread and is abandoned. Every run has its own token (``_RUN_TOKEN``):
    the counting hook traces only connections opened by that run's worker, so an
    abandoned worker never adds queries to a later run's counts.
    """
    if serve is None:
        runtime = importlib.import_module("lattice.api.runtime")
        serve = runtime.serve_source
        if hooks is None:
            hooks = runtime

    workdir.mkdir(parents=True, exist_ok=True)
    pristine = workdir / _PRISTINE
    pristine.unlink(missing_ok=True)
    db_builder(pristine)

    counter = [0]
    token = object()  # this run's identity (see _RUN_TOKEN)
    live = [True]

    def trace(sql: str) -> None:
        if live[0] and _is_counted_statement(sql):
            counter[0] += 1

    done: list[CaseResults] = []
    failed: list[BaseException] = []

    def work() -> None:
        _RUN_TOKEN.set(token)
        try:
            done.append(_lattice_cases(source, workdir, task, profile, serve, counter))
        except BaseException as exc:  # re-raised in the calling thread
            failed.append(exc)

    with _LATTICE_LOCK:
        previous = getattr(hooks, "on_connect", None) if hooks is not None else None

        def on_connect(conn: sqlite3.Connection) -> None:
            if previous is not None:
                previous(conn)
            if _RUN_TOKEN.get() is token:
                conn.set_trace_callback(trace)

        if hooks is not None:
            hooks.on_connect = on_connect
        try:
            worker = threading.Thread(target=work, name=f"lattice-cases-{task.id}", daemon=True)
            worker.start()
            worker.join(timeout_s)
            if worker.is_alive():
                return CaseResults(
                    cases=(),
                    started=False,
                    start_error=f"the app exceeded the {timeout_s:g}s time limit",
                    timed_out=True,
                )
        finally:
            live[0] = False  # an abandoned worker's connections stop counting
            if hooks is not None:
                hooks.on_connect = previous
    if failed:
        raise failed[0]
    return done[0]


def _lattice_cases(
    source: str,
    workdir: Path,
    task: ApiTask,
    profile: Any,
    serve: Callable[..., tuple[Any, Any]],
    counter: list[int],
) -> CaseResults:
    from lattice.diagnostics import render_for_llm

    results: list[CaseResult] = []
    for index, case in enumerate(task.cases):
        db_path = workdir / f"case_{index:02d}.db"
        _fresh_copy(workdir / _PRISTINE, db_path)
        try:
            try:
                check, app = serve(source, profile, db_path)
            except Exception as exc:
                return CaseResults(cases=(), started=False, start_error=_render_start_error(exc))
            if not check.ok:
                return CaseResults(
                    cases=(),
                    started=False,
                    start_error=render_for_llm(check.diagnostics),
                    diagnostics=tuple(check.diagnostics),
                )
            if app is None:
                return CaseResults(cases=(), started=False, start_error="the runtime built no app")
            results.append(_run_case_in_process(app, case, db_path, counter))
        finally:
            for suffix in ("", "-journal", "-wal", "-shm"):
                Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    return CaseResults(cases=tuple(results), started=True)


def run_cases_go(
    binary: Path,
    task: ApiTask,
    *,
    db_builder: DbBuilder,
    workdir: Path | None = None,
    wrap: Callable[[Any, Path, HttpCase], Any] | None = None,
) -> CaseResults:
    """Run ``task``'s cases against a Go binary built by
    ``lattice.gogen.build.build_go_app``, with the semantics of ``run_cases_fastapi``
    and ``run_cases_lattice``: every case on a fresh copy of the pristine DB, judged by
    ``evaluate_http_case``, through the same in-process client (``_run_case_in_process``,
    here over ``lattice.gogen.proxy``'s ASGI proxy). ``queries`` is the sum of the
    responses' ``X-Lattice-Sql-Count`` (the server runs with LATTICE_TRACE_SQL=1; a
    followed redirect counts both requests, as the trace callback does).

    ``wrap(app, db_path, case)``, when given, wraps each case's ASGI app (a test's
    recording shim). A binary that does not start is a loud start error."""
    import tempfile

    from lattice.gogen.proxy import go_app_factory

    with tempfile.TemporaryDirectory(prefix="lattice-go-cases-") as tmp:
        work = Path(workdir) if workdir is not None else Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        pristine = work / _PRISTINE
        pristine.unlink(missing_ok=True)
        db_builder(pristine)
        counter = [0]
        factory = go_app_factory(binary, sql_counter=counter)
        results: list[CaseResult] = []
        for index, case in enumerate(task.cases):
            db_path = work / f"case_{index:02d}.db"
            _fresh_copy(pristine, db_path)
            app = factory(db_path)
            try:
                try:
                    app.server.start()
                except Exception as exc:
                    error = _render_start_error(exc)
                    return CaseResults(cases=(), started=False, start_error=error)
                served = app if wrap is None else wrap(app, db_path, case)
                results.append(_run_case_in_process(served, case, db_path, counter))
            finally:
                app.close()
                for suffix in ("", "-journal", "-wal", "-shm"):
                    Path(f"{db_path}{suffix}").unlink(missing_ok=True)
        return CaseResults(cases=tuple(results), started=True)


Classification = Literal["correct", "silent", "loud"]


def is_server_error(case: CaseResult) -> bool:
    """A case the app answered with a 5xx, or during which it raised."""
    return case.status >= 500 or case.reason.startswith(SERVER_ERROR_PREFIX)


def classify(results: CaseResults) -> Classification:
    """Spec §5.4 / §7. Loud: the app did not start (or timed out), a happy case
    failed, or any case got a server error (5xx or an unhandled exception -- visible
    to monitoring). Silent: every failure is a trap case the app answered with a
    wrong non-5xx response. Correct: every case passed."""
    if not results.started:
        return "loud"
    failed = [c for c in results.cases if not c.passed]
    if any(c.kind == "happy" or is_server_error(c) for c in failed):
        return "loud"
    if failed:
        return "silent"
    return "correct"


def query_stats(results: CaseResults) -> tuple[float, int, float]:
    """(mean queries per request, max queries, mean latency ms) over the cases run."""
    cases = results.cases
    if not cases:
        return 0.0, 0, 0.0
    return (
        sum(c.queries for c in cases) / len(cases),
        max(c.queries for c in cases),
        sum(c.latency_ms for c in cases) / len(cases),
    )


def summarize_mapping(results: CaseResults) -> Mapping[str, int]:
    """Counts of passed/failed happy and trap cases (for details and messages)."""
    counts = {"happy_passed": 0, "happy_failed": 0, "trap_passed": 0, "trap_failed": 0}
    for c in results.cases:
        counts[f"{c.kind}_{'passed' if c.passed else 'failed'}"] += 1
    return counts
