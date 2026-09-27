"""API benchmark harness: expectation matching, case runners (sandboxed FastAPI
and in-process Lattice), classification, arms A/P/B/C, CLI and the api gate.

Everything here is self-contained: a tiny DB builder, a tiny platform kit, tiny
FastAPI scripts, and a fake ``serve_source`` returning a small Starlette app,
so the harness is tested independently of the real datasets/runtime/prompts.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import sys
import textwrap
import types
from pathlib import Path

import pytest

from bench.api_model import ApiTask, HttpCase
from bench.api_runner import (
    CaseResult,
    CaseResults,
    _match_rows,
    _match_value,
    classify,
    evaluate_case,
    run_cases_fastapi,
    run_cases_lattice,
)
from bench.report import (
    evaluate_gate_api,
    read_outcomes,
    render_markdown_api,
    write_outcomes,
)
from bench.runners import TaskOutcome, run_api_baseline, run_api_lattice
from bench.sandbox import sandbox_exec_available
from bench.tasks import suite
from lattice.diagnostics import Diagnostic, Severity, Span
from lattice.llm.client import ScriptedGenerator

# ---------------------------------------------------------------- fixtures --

TOK_A, TOK_B = "tok-a", "tok-b"


def build_tiny_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, name TEXT, role TEXT, token TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, total REAL, status TEXT);
        INSERT INTO users VALUES (1, 'a@x', 'alice', 'Customer', 'tok-a');
        INSERT INTO users VALUES (2, 'b@x', 'bob', 'Customer', 'tok-b');
        INSERT INTO users VALUES (3, 'c@x', 'root', 'Admin', 'tok-admin');
        INSERT INTO orders VALUES (1, 1, 10.0, 'pending');
        INSERT INTO orders VALUES (2, 1, 20.0, 'paid');
        INSERT INTO orders VALUES (3, 2, 30.0, 'pending');
        """
    )
    conn.commit()
    conn.close()


KIT = textwrap.dedent(
    '''
    """Tiny platform kit with the same interface as bench/api_kit/platform_kit.py."""
    import sqlite3

    from fastapi import Depends, FastAPI, Header, HTTPException

    app = FastAPI()


    def _on_connect(conn):
        pass


    def get_db():
        conn = sqlite3.connect("app.db", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _on_connect(conn)
        return conn


    def current_user(authorization: str = Header(None), db=Depends(get_db)):
        token = (authorization or "").removeprefix("Bearer ").strip()
        row = db.execute(
            "SELECT id, role, email, name FROM users WHERE token = ?", (token,)
        ).fetchone()
        if row is None:
            raise HTTPException(401, "unauthorized")
        return dict(row)
    '''
)

_SCRIPT_HEAD = textwrap.dedent(
    """
    from fastapi import Depends
    from fastapi.responses import JSONResponse, Response
    from platform_kit import app, current_user, get_db
    """
)

CORRECT_SCRIPT = _SCRIPT_HEAD + textwrap.dedent(
    """
    @app.get("/orders")
    def list_orders(limit: int = 20, offset: int = 0, user=Depends(current_user),
                    db=Depends(get_db)):
        rows = db.execute(
            "SELECT id, total FROM orders WHERE customer_id = ? ORDER BY id LIMIT ? OFFSET ?",
            (user["id"], limit, offset),
        ).fetchall()
        return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}


    @app.get("/orders/{order_id}")
    def get_order(order_id: int, user=Depends(current_user), db=Depends(get_db)):
        row = db.execute(
            "SELECT id, total FROM orders WHERE id = ? AND customer_id = ?",
            (order_id, user["id"]),
        ).fetchone()
        if row is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return dict(row)


    @app.delete("/orders/{order_id}")
    def delete_order(order_id: int, user=Depends(current_user), db=Depends(get_db)):
        cur = db.execute(
            "DELETE FROM orders WHERE id = ? AND customer_id = ?", (order_id, user["id"])
        )
        db.commit()
        if cur.rowcount == 0:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return Response(status_code=204)
    """
)

# Passes every happy case; fails the IDOR trap and the query-count trap (N+1).
NAIVE_SCRIPT = _SCRIPT_HEAD + textwrap.dedent(
    """
    @app.get("/orders")
    def list_orders(user=Depends(current_user), db=Depends(get_db)):
        ids = [r["id"] for r in db.execute(
            "SELECT id FROM orders WHERE customer_id = ? ORDER BY id", (user["id"],))]
        items = []
        for i in ids:
            total = db.execute("SELECT total FROM orders WHERE id = ?", (i,)).fetchone()[0]
            items.append({"id": i, "total": total})
        return {"items": items, "limit": 20, "offset": 0}


    @app.get("/orders/{order_id}")
    def get_order(order_id: int, user=Depends(current_user), db=Depends(get_db)):
        row = db.execute(
            "SELECT id, total FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if row is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return dict(row)


    @app.delete("/orders/{order_id}")
    def delete_order(order_id: int, user=Depends(current_user), db=Depends(get_db)):
        db.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        db.commit()
        return Response(status_code=204)
    """
)

IMPORT_ERROR_SCRIPT = _SCRIPT_HEAD + 'raise RuntimeError("boom at import")\n'
NO_ROUTES_SCRIPT = _SCRIPT_HEAD + "\n"
CRASH_SCRIPT = _SCRIPT_HEAD + textwrap.dedent(
    """
    @app.get("/orders")
    def list_orders(user=Depends(current_user)):
        return 1 / 0


    @app.get("/orders/{order_id}")
    def get_order(order_id: int):
        return 1 / 0


    @app.delete("/orders/{order_id}")
    def delete_order(order_id: int):
        return 1 / 0
    """
)


def _case(name: str, kind: str, method: str, path: str, status: int, **kw) -> HttpCase:
    return HttpCase(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        family=kw.pop("family", "ownership"),
        method=method,
        path=path,
        token=kw.pop("token", TOK_A),
        expect_status=status,
        **kw,
    )


TASK = ApiTask(
    id="t_orders",
    family="ownership",
    spec="List, get and delete the caller's own orders.",
    cases=(
        _case(
            "list_mine",
            "happy",
            "GET",
            "/orders",
            200,
            expect_json={"items": [{"id": 2, "total": 20.0}, {"id": 1, "total": 10.0}]},
        ),
        _case("get_mine", "happy", "GET", "/orders/1", 200, expect_json={"id": 1, "total": 10}),
        _case(
            "delete_mine",
            "happy",
            "DELETE",
            "/orders/1",
            204,
            after_sql="SELECT id FROM orders ORDER BY id",
            after_expect=[2, 3],
        ),
        # Runs after delete_mine: passes only if every case gets a fresh DB.
        _case("get_mine_again", "happy", "GET", "/orders/1", 200),
        _case("idor_get", "trap", "GET", "/orders/3", 404),
        _case(
            "list_queries",
            "trap",
            "GET",
            "/orders",
            200,
            family="efficiency",
            max_queries=2,
        ),
    ),
)


@pytest.fixture
def kit_path(tmp_path: Path) -> Path:
    path = tmp_path / "kit" / "platform_kit.py"
    path.parent.mkdir()
    path.write_text(KIT)
    return path


def _by_name(results: CaseResults) -> dict[str, CaseResult]:
    return {c.name: c for c in results.cases}


# ------------------------------------------------------ expectation matching --


def test_json_subset_match_ignores_extra_keys_and_tolerates_float_noise() -> None:
    assert (
        _match_value({"id": 1, "total": 10}, {"id": 1, "total": 10.0000001, "x": 5}, False) is None
    )
    assert "missing" in (_match_value({"id": 1, "y": 2}, {"id": 1}, False) or "")
    assert "$.id" in (_match_value({"id": 1}, {"id": 2}, False) or "")


def test_bool_never_equals_a_number() -> None:
    assert _match_value({"ok": True}, {"ok": 1}, False) is not None
    assert _match_value({"ok": True}, {"ok": True}, False) is None


def test_page_items_are_compared_as_a_list_unordered_unless_ordered() -> None:
    expected = {"items": [{"id": 2}, {"id": 1}]}
    actual = {"items": [{"id": 1, "t": 0}, {"id": 2, "t": 0}], "limit": 20, "offset": 0}
    assert _match_value(expected, actual, False) is None
    assert _match_value(expected, actual, True) is not None
    # Length is significant, even unordered.
    assert "expected 2 items, got 3" in (
        _match_value(expected, {"items": [{"id": 1}, {"id": 2}, {"id": 3}]}, False) or ""
    )
    # A duplicate cannot stand in for a missing element.
    assert _match_value({"items": [{"id": 1}, {"id": 2}]}, {"items": [{"id": 1}, {"id": 1}]}, False)


def test_db_rows_match_positionally_by_column_and_scalars_are_one_column_rows() -> None:
    assert _match_rows([2, 3], [[3], [2]], False) is None
    assert _match_rows([2, 3], [[3], [2]], True) is not None
    assert _match_rows([[1, "a"]], [["a", 1]], False) is not None
    assert _match_rows([[1]], [[1, "extra"]], False) is not None


def _as_dict(case: HttpCase) -> dict:
    return dataclasses.asdict(case)


def test_success_objects_must_have_exactly_the_expected_keys() -> None:
    one = _as_dict(_case("c", "happy", "GET", "/x", 200, expect_json={"id": 1, "total": 10}))
    assert evaluate_case(one, 200, {"id": 1, "total": 10.0}, 1, None, "") == (True, "")
    leaked = evaluate_case(one, 200, {"id": 1, "total": 10, "token": "t"}, 1, None, "")
    assert leaked == (False, "json mismatch at $: unexpected field 'token'")
    created = _as_dict(_case("c", "happy", "POST", "/x", 201, expect_json={"id": 1}))
    assert evaluate_case(created, 201, {"id": 1, "x": 2}, 1, None, "")[1].endswith("'x'")


def test_page_items_must_have_exactly_the_expected_keys_but_the_envelope_is_open() -> None:
    page = _as_dict(
        _case("c", "happy", "GET", "/x", 200, expect_json={"items": [{"id": 1}, {"id": 2}]})
    )
    ok = {"items": [{"id": 2}, {"id": 1}], "limit": 20, "offset": 0}
    assert evaluate_case(page, 200, ok, 1, None, "") == (True, "")
    extra = {"items": [{"id": 2}, {"id": 1, "secret": 1}], "limit": 20, "offset": 0}
    assert not evaluate_case(page, 200, extra, 1, None, "")[0]
    stray = {"items": [{"id": 2}, {"id": 1}], "limit": 20, "offset": 0, "debug": 1}
    assert evaluate_case(page, 200, stray, 1, None, "")[1].endswith("unexpected field 'debug'")
    bounds = _as_dict(_case("c", "happy", "GET", "/x", 200, expect_json={"limit": 50, "offset": 0}))
    assert evaluate_case(
        bounds, 200, {"items": [{"id": 1}], "limit": 50, "offset": 0}, 1, None, ""
    )[0]


def test_error_bodies_stay_subset_matched() -> None:
    err = _as_dict(_case("c", "trap", "GET", "/x", 404, expect_json={"error": "not_found"}))
    assert evaluate_case(err, 404, {"error": "not_found", "detail": "order"}, 1, None, "") == (
        True,
        "",
    )


def test_evaluate_case_checks_crash_status_json_queries_and_db_state_in_order() -> None:
    case = _as_dict(
        _case(
            "c",
            "happy",
            "GET",
            "/x",
            200,
            expect_json={"a": 1},
            max_queries=2,
            after_sql="SELECT 1",
            after_expect=[1],
        )
    )
    assert evaluate_case(case, 200, {"a": 1}, 2, [[1]], "") == (True, "")
    assert evaluate_case(case, 200, {"a": 1}, 2, [[1]], "Traceback")[1].startswith("server error")
    assert evaluate_case(case, 404, {"a": 1}, 2, [[1]], "")[1] == "status 404, expected 200"
    assert evaluate_case(case, 200, None, 2, [[1]], "")[1] == "response body is not JSON"
    assert evaluate_case(case, 200, {"a": 2}, 2, [[1]], "")[1].startswith("json mismatch at $.a")
    assert evaluate_case(case, 200, {"a": 1}, 3, [[1]], "")[1] == "3 queries, max 2"
    assert evaluate_case(case, 200, {"a": 1}, 2, [[2]], "")[1].startswith("db state mismatch")


# ------------------------------------------------------------ classification --


def _results(*passes: tuple[str, bool], started: bool = True) -> CaseResults:
    cases = tuple(
        CaseResult(f"c{i}", kind, "f", ok, 200, 1, 1.0, "" if ok else "x")
        for i, (kind, ok) in enumerate(passes)
    )
    return CaseResults(cases=cases, started=started)


def _with_status(results: CaseResults, name: str, status: int, reason: str) -> CaseResults:
    cases = tuple(
        dataclasses.replace(c, status=status, reason=reason) if c.name == name else c
        for c in results.cases
    )
    return dataclasses.replace(results, cases=cases)


def test_classify_a_5xx_on_a_trap_case_is_loud() -> None:
    # Spec §5.4: silent = the app answered a trap case with a wrong non-5xx
    # response; a server error is visible to monitoring, hence loud.
    base = _results(("happy", True), ("trap", False))
    assert classify(_with_status(base, "c1", 500, "server error: boom")) == "loud"
    assert classify(_with_status(base, "c1", 503, "status 503, expected 404")) == "loud"
    assert classify(_with_status(base, "c1", 200, "status 200, expected 404")) == "silent"
    assert classify(_with_status(base, "c1", 404, "json mismatch at $.x")) == "silent"


def test_classify_correct_silent_loud() -> None:
    assert classify(_results(("happy", True), ("trap", True))) == "correct"
    assert classify(_results(("happy", True), ("trap", False))) == "silent"
    assert classify(_results(("happy", False), ("trap", True))) == "loud"
    assert classify(_results(("happy", False), ("trap", False))) == "loud"
    assert classify(CaseResults(cases=(), started=False, start_error="boom")) == "loud"


# ------------------------------------------------------ FastAPI (sandboxed) --


def test_fastapi_correct_script_passes_every_case(tmp_path: Path, kit_path: Path) -> None:
    results = run_cases_fastapi(
        CORRECT_SCRIPT, tmp_path / "w", TASK, db_builder=build_tiny_db, kit_path=kit_path
    )
    assert results.started, results.start_error
    by = _by_name(results)
    assert all(c.passed for c in results.cases), [c.reason for c in results.cases]
    assert by["list_mine"].queries == 2  # auth + one list query
    assert by["get_mine_again"].status == 200  # fresh DB per case
    assert by["delete_mine"].status == 204
    assert all(c.latency_ms > 0 for c in results.cases)
    assert classify(results) == "correct"
    assert (tmp_path / "w" / "solution.py").read_text() == CORRECT_SCRIPT
    assert (tmp_path / "w" / "platform_kit.py").exists()


def test_fastapi_naive_script_is_silent_idor_and_n_plus_one(tmp_path: Path, kit_path: Path) -> None:
    results = run_cases_fastapi(
        NAIVE_SCRIPT, tmp_path / "w", TASK, db_builder=build_tiny_db, kit_path=kit_path
    )
    by = _by_name(results)
    assert by["idor_get"].reason == "status 200, expected 404"
    assert by["list_queries"].queries == 4  # auth + ids + one per order
    assert by["list_queries"].reason == "4 queries, max 2"
    assert all(c.passed for c in results.cases if c.kind == "happy")
    assert classify(results) == "silent"


def test_fastapi_import_error_is_a_start_failure(tmp_path: Path, kit_path: Path) -> None:
    results = run_cases_fastapi(
        IMPORT_ERROR_SCRIPT, tmp_path / "w", TASK, db_builder=build_tiny_db, kit_path=kit_path
    )
    assert not results.started
    assert "boom at import" in results.start_error
    assert classify(results) == "loud"


def test_fastapi_unhandled_exception_is_a_500_with_the_traceback(
    tmp_path: Path, kit_path: Path
) -> None:
    results = run_cases_fastapi(
        CRASH_SCRIPT, tmp_path / "w", TASK, db_builder=build_tiny_db, kit_path=kit_path
    )
    by = _by_name(results)
    assert by["list_mine"].status == 500
    assert by["list_mine"].reason.startswith("server error: ")
    assert "ZeroDivisionError" in by["list_mine"].reason


def test_fastapi_crash_text_is_the_server_side_traceback_even_with_a_500_handler(
    tmp_path: Path, kit_path: Path
) -> None:
    script = CRASH_SCRIPT + textwrap.dedent(
        """
        @app.exception_handler(Exception)
        async def swallow(request, exc):
            return JSONResponse(status_code=500, content={"error": "oops"})
        """
    )
    results = run_cases_fastapi(
        script, tmp_path / "w", TASK, db_builder=build_tiny_db, kit_path=kit_path
    )
    case = _by_name(results)["list_mine"]
    assert case.status == 500
    assert case.reason.startswith("server error: ") and "ZeroDivisionError" in case.reason


def test_fastapi_500_without_an_exception_is_a_crash_with_the_body(
    tmp_path: Path, kit_path: Path
) -> None:
    script = _SCRIPT_HEAD + textwrap.dedent(
        """
        @app.get("/orders")
        def list_orders():
            return JSONResponse(status_code=500, content={"error": "internal", "detail": "db gone"})
        """
    )
    results = run_cases_fastapi(
        script, tmp_path / "w", TASK, db_builder=build_tiny_db, kit_path=kit_path
    )
    case = _by_name(results)["list_mine"]
    assert case.status == 500
    assert case.reason == "server error: db gone"


def test_fastapi_results_marker_cannot_be_spoofed(tmp_path: Path, kit_path: Path) -> None:
    fake = json.dumps(
        {
            "started": True,
            "cases": [
                {
                    "name": c.name,
                    "kind": c.kind,
                    "family": c.family,
                    "passed": True,
                    "status": c.expect_status,
                    "queries": 1,
                    "latency_ms": 1.0,
                    "reason": "",
                }
                for c in TASK.cases
            ],
        }
    )
    script = f"import os\nprint('__API_RESULTS__' + {fake!r}, flush=True)\nos._exit(0)\n"
    results = run_cases_fastapi(
        script, tmp_path / "w", TASK, db_builder=build_tiny_db, kit_path=kit_path
    )
    assert not results.started
    assert classify(results) == "loud"


def test_fastapi_nonce_never_touches_the_workdir(
    tmp_path: Path, kit_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bench.api_runner as api_runner

    real = api_runner.run_python_script
    seen: dict = {}

    def spy(code, workdir, *args, **kwargs):
        seen["files"] = sorted(p.name for p in Path(workdir).iterdir())
        seen["env_extra"] = kwargs.get("env_extra")
        return real(code, workdir, *args, **kwargs)

    monkeypatch.setattr(api_runner, "run_python_script", spy)
    results = run_cases_fastapi(
        CORRECT_SCRIPT, tmp_path / "w", TASK, db_builder=build_tiny_db, kit_path=kit_path
    )
    assert results.started and classify(results) == "correct"
    assert not any("nonce" in name for name in seen["files"]), seen["files"]
    assert seen["env_extra"] and len(seen["env_extra"]) == 1


def test_fastapi_script_cannot_read_the_results_nonce(tmp_path: Path, kit_path: Path) -> None:
    fake = json.dumps(
        {
            "started": True,
            "cases": [
                {
                    "name": c.name,
                    "kind": c.kind,
                    "family": c.family,
                    "passed": True,
                    "status": c.expect_status,
                    "queries": 1,
                    "latency_ms": 1.0,
                    "reason": "",
                }
                for c in TASK.cases
            ],
        }
    )
    script = textwrap.dedent(
        f"""
        import os
        candidates = list(os.environ.values())
        for name in os.listdir("."):
            try:
                candidates.append(open(name).read().strip())
            except Exception:
                pass
        for value in candidates:
            print("__API_RESULTS_" + value + "__" + {fake!r}, flush=True)
        os._exit(0)
        """
    )
    results = run_cases_fastapi(
        script, tmp_path / "w", TASK, db_builder=build_tiny_db, kit_path=kit_path
    )
    assert not results.started
    assert classify(results) == "loud"


def test_fastapi_timeout_is_reported(tmp_path: Path, kit_path: Path) -> None:
    script = "import time\ntime.sleep(30)\n"
    results = run_cases_fastapi(
        script, tmp_path / "w", TASK, db_builder=build_tiny_db, kit_path=kit_path, timeout_s=2
    )
    assert not results.started and results.timed_out
    assert "time limit" in results.start_error


def test_fastapi_script_sees_no_inherited_environment(
    tmp_path: Path, kit_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    script = _SCRIPT_HEAD + textwrap.dedent(
        """
        import os

        @app.get("/env")
        def env():
            return {"key": os.environ.get("ANTHROPIC_API_KEY")}
        """
    )
    task = ApiTask(
        id="t_env",
        family="control",
        spec="",
        cases=(_case("env", "happy", "GET", "/env", 200, expect_json={"key": None}),),
    )
    results = run_cases_fastapi(
        script, tmp_path / "w", task, db_builder=build_tiny_db, kit_path=kit_path
    )
    assert results.cases[0].passed, results.cases[0].reason


@pytest.mark.skipif(not sandbox_exec_available(), reason="needs macOS sandbox-exec")
def test_fastapi_script_cannot_write_outside_its_workdir(tmp_path: Path, kit_path: Path) -> None:
    escape = tmp_path / "escape.txt"
    script = _SCRIPT_HEAD + f"open({str(escape)!r}, 'w').write('x')\n"
    results = run_cases_fastapi(
        script, tmp_path / "w", TASK, db_builder=build_tiny_db, kit_path=kit_path
    )
    assert not escape.exists()
    assert not results.started


def test_fastapi_script_cannot_tamper_with_the_pristine_db_for_later_cases(
    tmp_path: Path, kit_path: Path
) -> None:
    # Truncating pristine.db from the app does not affect later cases: the
    # driver restores app.db from bytes read before any model code ran.
    script = CORRECT_SCRIPT + "\nopen('pristine.db', 'wb').close()\n"
    results = run_cases_fastapi(
        script, tmp_path / "w", TASK, db_builder=build_tiny_db, kit_path=kit_path
    )
    assert all(c.passed for c in results.cases), [c.reason for c in results.cases]


# ------------------------------------------------------ Lattice (in process) --


@dataclasses.dataclass(frozen=True)
class FakeCheck:
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(d.severity is Severity.ERROR for d in self.diagnostics)


L802 = Diagnostic(
    code="L802",
    severity=Severity.ERROR,
    span=Span(3, 5),
    message="rows of Orders reach `return` without owned(..., by=user)",
)


def make_fake_runtime() -> types.SimpleNamespace:
    """A stand-in for lattice.api.runtime: ``serve_source`` + ``on_connect``.

    Source "GOOD" serves the correct endpoints, "NAIVE" the IDOR / N+1 ones,
    "REJECT" is rejected by the "checker", "BOOM" makes serve raise.
    """
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    runtime = types.SimpleNamespace(on_connect=lambda conn: None, served=[])

    def serve_source(source: str, profile: object, db_path: Path):
        runtime.served.append((source.strip(), profile, db_path))
        source = source.strip()
        if source == "REJECT":
            return FakeCheck((L802,)), None
        if source == "BOOM":
            raise RuntimeError("runtime exploded")
        if source == "CRASH":

            async def internal(request):
                detail = "3:5: error R310: one() matched 2 rows; expected exactly one"
                return JSONResponse({"error": "internal", "detail": detail}, status_code=500)

            async def raising(request):
                raise ZeroDivisionError("fake runtime bug")

            crash_app = Starlette(
                routes=[
                    Route("/orders", internal, methods=["GET"]),
                    Route("/orders/{order_id}", raising, methods=["GET", "DELETE"]),
                ]
            )
            return FakeCheck(), crash_app
        if source == "HANG":

            def hang(request):  # sync: Starlette runs it in a worker thread
                import time as _time

                _time.sleep(3)
                return JSONResponse({})

            return FakeCheck(), Starlette(routes=[Route("/orders", hang, methods=["GET"])])
        naive = source == "NAIVE"

        def connect() -> sqlite3.Connection:
            conn = sqlite3.connect(db_path)
            runtime.on_connect(conn)  # looked up at call time, like the runtime
            return conn

        def auth(conn: sqlite3.Connection, request) -> int | None:
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            row = conn.execute("SELECT id FROM users WHERE token = ?", (token,)).fetchone()
            return None if row is None else row[0]

        async def list_orders(request):
            conn = connect()
            uid = auth(conn, request)
            if naive:
                ids = [
                    r[0]
                    for r in conn.execute("SELECT id FROM orders WHERE customer_id = ?", (uid,))
                ]
                items = [
                    {
                        "id": i,
                        "total": conn.execute(
                            "SELECT total FROM orders WHERE id = ?", (i,)
                        ).fetchone()[0],
                    }
                    for i in ids
                ]
            else:
                items = [
                    {"id": i, "total": t}
                    for i, t in conn.execute(
                        "SELECT id, total FROM orders WHERE customer_id = ?", (uid,)
                    )
                ]
            return JSONResponse({"items": items, "limit": 20, "offset": 0})

        async def get_order(request):
            conn = connect()
            uid = auth(conn, request)
            oid = int(request.path_params["order_id"])
            sql = "SELECT id, total FROM orders WHERE id = ?" + (
                "" if naive else " AND customer_id = ?"
            )
            args = (oid,) if naive else (oid, uid)
            row = conn.execute(sql, args).fetchone()
            if row is None:
                return JSONResponse({"error": "not_found", "detail": "order"}, status_code=404)
            return JSONResponse({"id": row[0], "total": row[1]})

        async def delete_order(request):
            conn = connect()
            uid = auth(conn, request)
            oid = int(request.path_params["order_id"])
            conn.execute("DELETE FROM orders WHERE id = ? AND customer_id = ?", (oid, uid))
            conn.commit()
            return Response(status_code=204)

        app = Starlette(
            routes=[
                Route("/orders", list_orders, methods=["GET"]),
                Route("/orders/{order_id}", get_order, methods=["GET"]),
                Route("/orders/{order_id}", delete_order, methods=["DELETE"]),
            ]
        )
        return FakeCheck(), app

    runtime.serve_source = serve_source
    return runtime


def _run_lattice_cases(source: str, tmp_path: Path, runtime=None) -> CaseResults:
    runtime = runtime or make_fake_runtime()
    return run_cases_lattice(
        source,
        tmp_path / "w",
        TASK,
        db_builder=build_tiny_db,
        profile="PROFILE",
        serve=runtime.serve_source,
        hooks=runtime,
    )


def test_lattice_good_program_passes_every_case_with_query_counting(tmp_path: Path) -> None:
    runtime = make_fake_runtime()
    original = runtime.on_connect
    results = _run_lattice_cases("GOOD", tmp_path, runtime)
    assert results.started
    assert all(c.passed for c in results.cases), [c.reason for c in results.cases]
    assert _by_name(results)["list_mine"].queries == 2
    assert classify(results) == "correct"
    # The hook is restored, the profile and a per-case fresh DB are passed through.
    assert runtime.on_connect is original
    assert {p for _, p, _ in runtime.served} == {"PROFILE"}
    assert len({db for _, _, db in runtime.served}) == len(TASK.cases)


def test_lattice_500_on_a_case_is_a_crash_with_the_runtime_error_text(tmp_path: Path) -> None:
    runtime = make_fake_runtime()
    results = run_cases_lattice(
        "CRASH",
        tmp_path / "w",
        dataclasses.replace(TASK, cases=TASK.cases[:2]),
        db_builder=build_tiny_db,
        profile="PROFILE",
        serve=runtime.serve_source,
        hooks=runtime,
    )
    by = _by_name(results)
    assert by["list_mine"].status == 500
    assert by["list_mine"].reason == (
        "server error: 3:5: error R310: one() matched 2 rows; expected exactly one"
    )
    assert by["get_mine"].status == 500
    assert by["get_mine"].reason.startswith("server error: ")
    assert "ZeroDivisionError: fake runtime bug" in by["get_mine"].reason


def test_lattice_hang_is_a_loud_timeout(tmp_path: Path) -> None:
    runtime = make_fake_runtime()
    results = run_cases_lattice(
        "HANG",
        tmp_path / "w",
        dataclasses.replace(TASK, cases=TASK.cases[:1]),
        db_builder=build_tiny_db,
        profile="PROFILE",
        serve=runtime.serve_source,
        hooks=runtime,
        timeout_s=0.5,
    )
    assert not results.started and results.timed_out
    assert "time limit" in results.start_error
    assert classify(results) == "loud"
    assert runtime.on_connect is not None  # the hook was restored


def test_abandoned_lattice_worker_never_pollutes_a_later_runs_query_counts(
    tmp_path: Path,
) -> None:
    import threading

    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    release, polluted = threading.Event(), threading.Event()
    runtime = types.SimpleNamespace(on_connect=lambda conn: None)

    def serve_source(source: str, profile: object, db_path: Path):
        def connect() -> sqlite3.Connection:
            conn = sqlite3.connect(db_path)
            runtime.on_connect(conn)  # looked up at call time, like the runtime
            return conn

        if source == "HANG_THEN_QUERY":

            def hang(request):  # outlives its run, then queries during the next one
                release.wait(10)
                conn = connect()
                for _ in range(5):
                    conn.execute("SELECT 1 FROM orders").fetchall()
                conn.close()
                polluted.set()
                return JSONResponse({})

            return FakeCheck(), Starlette(routes=[Route("/orders", hang, methods=["GET"])])

        def one_query(request):
            release.set()
            polluted.wait(10)
            conn = connect()
            conn.execute("SELECT id FROM orders").fetchall()
            conn.close()
            return JSONResponse({})

        return FakeCheck(), Starlette(routes=[Route("/orders", one_query, methods=["GET"])])

    task = ApiTask(
        id="t_hang",
        family="control",
        spec="",
        cases=(_case("list", "happy", "GET", "/orders", 200),),
    )
    hung = run_cases_lattice(
        "HANG_THEN_QUERY",
        tmp_path / "a",
        task,
        db_builder=build_tiny_db,
        profile="PROFILE",
        serve=serve_source,
        hooks=runtime,
        timeout_s=0.3,
    )
    assert hung.timed_out
    later = run_cases_lattice(
        "GOOD",
        tmp_path / "b",
        task,
        db_builder=build_tiny_db,
        profile="PROFILE",
        serve=serve_source,
        hooks=runtime,
    )
    assert polluted.is_set()
    assert later.started and later.cases[0].queries == 1


def test_arm_b_repairs_a_happy_case_500_with_the_runtime_error(tmp_path: Path) -> None:
    outcome, gen = _lattice(["CRASH", "GOOD"], tmp_path)
    assert outcome.correct and outcome.rounds == 2
    repair = gen.calls[1][1][-1]["content"]
    assert "R310" in repair
    assert "idor_get" not in repair and "/orders/3" not in repair


def test_lattice_naive_program_is_silent(tmp_path: Path) -> None:
    results = _run_lattice_cases("NAIVE", tmp_path)
    by = _by_name(results)
    assert by["list_queries"].queries == 4
    assert not by["idor_get"].passed
    assert classify(results) == "silent"


def test_lattice_checker_rejection_is_a_start_failure_with_diagnostics(tmp_path: Path) -> None:
    results = _run_lattice_cases("REJECT", tmp_path)
    assert not results.started
    assert results.diagnostics == (L802,)
    assert "L802" in results.start_error
    assert classify(results) == "loud"


def test_lattice_serve_exception_is_a_start_failure(tmp_path: Path) -> None:
    results = _run_lattice_cases("BOOM", tmp_path)
    assert not results.started and not results.diagnostics
    assert "runtime exploded" in results.start_error


# ---------------------------------------------------------------- the arms --


class StubPrompts:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def api_lattice_system_prompt(self, hints: bool = True) -> str:
        self.calls.append(("lattice_system", hints))
        return "LATTICE SYSTEM"

    def api_baseline_system_prompt(self, hints: bool = True) -> str:
        self.calls.append(("baseline_system", hints))
        return "BASELINE SYSTEM"

    def api_task_message(self, task: ApiTask, arm_kind: str) -> str:
        self.calls.append(("task", task.id, arm_kind))
        return f"TASK {task.id} for {arm_kind}"


def _fenced(code: str) -> str:
    return f"```python\n{code}\n```\n"


def _baseline(texts, tmp_path, kit_path, arm="P", **kw) -> tuple[TaskOutcome, ScriptedGenerator]:
    gen = ScriptedGenerator([_fenced(t) if t is not None else "no code here" for t in texts])
    outcome = run_api_baseline(
        TASK,
        gen,
        tmp_path / "w",
        db_builder=build_tiny_db,
        kit_path=kit_path,
        arm=arm,
        max_repairs=2,
        prompts=kw.pop("prompts", StubPrompts()),
        **kw,
    )
    return outcome, gen


def test_arm_p_repairs_a_start_failure_with_the_error_text_only(
    tmp_path: Path, kit_path: Path
) -> None:
    prompts = StubPrompts()
    outcome, gen = _baseline(
        [IMPORT_ERROR_SCRIPT, CORRECT_SCRIPT], tmp_path, kit_path, prompts=prompts, hints=False
    )
    assert outcome.correct and outcome.failure is None
    assert outcome.rounds == 2
    assert outcome.diagnostics_per_round == (("start_error",), ())
    system, messages = gen.calls[1]
    assert system == "BASELINE SYSTEM"
    assert messages[0]["content"] == "TASK t_orders for baseline"
    feedback = messages[-1]["content"]
    assert feedback.startswith("The app failed: start_error. Error output:")
    assert "boom at import" in feedback
    assert feedback.endswith("Return the complete corrected script in one ```python block.")
    assert ("baseline_system", False) in prompts.calls


def test_arm_p_never_repairs_a_silent_failure(tmp_path: Path, kit_path: Path) -> None:
    outcome, gen = _baseline([NAIVE_SCRIPT, CORRECT_SCRIPT], tmp_path, kit_path)
    assert outcome.rounds == 1 and len(gen.calls) == 1
    assert not outcome.correct and outcome.failure == "wrong_result"
    assert set(outcome.failed_cases) == {"idor_get", "list_queries"}
    assert outcome.max_queries == 4
    assert outcome.mean_queries > 0 and outcome.mean_latency_ms > 0
    assert outcome.exec_s > 0
    assert outcome.family == "ownership" and outcome.tier == "api"
    assert outcome.program_lines > 0
    assert outcome.diagnostics_per_round == ((),)


def test_arm_p_repairs_all_happy_failing_without_revealing_cases(
    tmp_path: Path, kit_path: Path
) -> None:
    outcome, gen = _baseline([NO_ROUTES_SCRIPT, CORRECT_SCRIPT], tmp_path, kit_path)
    assert outcome.correct and outcome.rounds == 2
    feedback = gen.calls[1][1][-1]["content"]
    assert "every basic request failed" in feedback and "404" in feedback
    for case in TASK.cases:
        assert case.name not in feedback
    assert "/orders" not in feedback


def test_arm_p_repairs_a_crash_with_the_traceback(tmp_path: Path, kit_path: Path) -> None:
    outcome, gen = _baseline([CRASH_SCRIPT, CORRECT_SCRIPT], tmp_path, kit_path)
    assert outcome.correct and outcome.rounds == 2
    assert outcome.diagnostics_per_round == (("crash",), ())
    assert "ZeroDivisionError" in gen.calls[1][1][-1]["content"]


def test_arm_p_trap_case_500_is_loud_final_and_never_surfaced(
    tmp_path: Path, kit_path: Path
) -> None:
    raising = CORRECT_SCRIPT.replace(
        "    if row is None:\n"
        '        return JSONResponse(status_code=404, content={"error": "not_found"})\n'
        "    return dict(row)",
        "    if row is None:\n        raise KeyError(order_id)\n    return dict(row)",
    )
    assert raising != CORRECT_SCRIPT
    outcome, gen = _baseline([raising, CORRECT_SCRIPT], tmp_path, kit_path)
    assert not outcome.correct and outcome.rounds == 1
    assert outcome.failure == "server_error"
    assert outcome.diagnostics_per_round == (("server_error",),)
    assert len(gen.calls) == 1


def test_arm_p_repairs_a_response_without_code(tmp_path: Path, kit_path: Path) -> None:
    outcome, _ = _baseline([None, CORRECT_SCRIPT], tmp_path, kit_path)
    assert outcome.correct and outcome.diagnostics_per_round == (("no_code",), ())


def test_arm_p_gives_up_after_max_repairs(tmp_path: Path, kit_path: Path) -> None:
    outcome, _ = _baseline([IMPORT_ERROR_SCRIPT] * 3, tmp_path, kit_path)
    assert outcome.rounds == 3 and outcome.failure == "start_error"
    assert "boom at import" in outcome.detail


def test_arm_a_is_single_shot(tmp_path: Path, kit_path: Path) -> None:
    outcome, gen = _baseline([IMPORT_ERROR_SCRIPT, CORRECT_SCRIPT], tmp_path, kit_path, arm="A")
    assert outcome.arm == "A" and outcome.rounds == 1 and len(gen.calls) == 1
    assert outcome.failure == "start_error" and not outcome.correct


def _lattice(texts, tmp_path, arm="B", runtime=None, **kw) -> tuple[TaskOutcome, ScriptedGenerator]:
    runtime = runtime or make_fake_runtime()
    gen = ScriptedGenerator([_fenced(t) for t in texts])
    outcome = run_api_lattice(
        TASK,
        gen,
        tmp_path / "w",
        db_builder=build_tiny_db,
        profile="PROFILE",
        arm=arm,
        max_repairs=2,
        prompts=kw.pop("prompts", StubPrompts()),
        serve=runtime.serve_source,
        hooks=runtime,
        **kw,
    )
    return outcome, gen


def test_arm_b_repairs_on_checker_diagnostics(tmp_path: Path) -> None:
    outcome, gen = _lattice(["REJECT", "GOOD"], tmp_path)
    assert outcome.correct and outcome.rounds == 2 and outcome.check_passed
    assert outcome.diagnostics_per_round == (("L802",), ())
    system, messages = gen.calls[1]
    assert system == "LATTICE SYSTEM"
    assert messages[0]["content"] == "TASK t_orders for lattice"
    feedback = messages[-1]["content"]
    assert feedback.startswith("The program failed Lattice checks:")
    assert "L802" in feedback
    assert feedback.endswith("Return the complete corrected program in one ```python block.")
    assert (tmp_path / "w" / "program_round1.py").read_text().strip() == "REJECT"


def test_arm_b_repairs_on_a_runtime_start_error(tmp_path: Path) -> None:
    outcome, gen = _lattice(["BOOM", "GOOD"], tmp_path)
    assert outcome.correct and outcome.rounds == 2
    feedback = gen.calls[1][1][-1]["content"]
    assert feedback.startswith("The program failed at run time: start_error: ")
    assert "runtime exploded" in feedback


def test_arm_b_never_repairs_a_silent_failure(tmp_path: Path) -> None:
    outcome, gen = _lattice(["NAIVE", "GOOD"], tmp_path)
    assert outcome.rounds == 1 and len(gen.calls) == 1
    assert outcome.failure == "wrong_result" and outcome.check_passed
    assert outcome.max_queries == 4 and "idor_get" in outcome.failed_cases


def test_arm_b_static_failure_after_budget(tmp_path: Path) -> None:
    outcome, _ = _lattice(["REJECT"] * 3, tmp_path)
    assert outcome.rounds == 3 and outcome.failure == "static"
    assert outcome.check_passed is False
    assert outcome.mean_queries == 0.0 and outcome.failed_cases == ()


def test_arm_c_is_single_shot(tmp_path: Path) -> None:
    outcome, gen = _lattice(["REJECT", "GOOD"], tmp_path, arm="C")
    assert outcome.arm == "C" and outcome.rounds == 1 and len(gen.calls) == 1
    assert outcome.failure == "static" and not outcome.correct


def test_arm_b_good_first_shot(tmp_path: Path) -> None:
    outcome, _ = _lattice(["GOOD"], tmp_path)
    assert outcome.correct and outcome.rounds == 1 and outcome.failure is None
    assert outcome.mean_queries > 0 and outcome.exec_s > 0


def test_api_outcome_fields_round_trip_through_jsonl(tmp_path: Path) -> None:
    outcome, _ = _lattice(["NAIVE"], tmp_path)
    path = tmp_path / "o.jsonl"
    write_outcomes(path, [outcome])
    (back,) = read_outcomes(path)
    assert back == outcome
    assert isinstance(back.failed_cases, tuple)


# ---------------------------------------------------------------- suite ----


def test_api_suite_is_empty_or_api_tasks() -> None:
    tasks = suite("api")
    assert all(isinstance(t, ApiTask) for t in tasks)


def test_api_suite_loads_the_api_tasks_module(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("bench.api_tasks")
    fake.API_TASKS = (TASK,)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "bench.api_tasks", fake)
    assert suite("api") == (TASK,)


# ---------------------------------------------------------------- gate -----


def _o(
    arm: str, family: str, *, correct=False, failure=None, q=0.0, rounds=1, model="m", task=None
):
    return TaskOutcome(
        task_id=task or f"{family}-{arm}-{correct}-{failure}",
        arm=arm,  # type: ignore[arg-type]
        tier="api",
        correct=correct,
        check_passed=None,
        rounds=rounds,
        output_tokens=0,
        input_tokens=0,
        latency_s=0.0,
        failure=None if correct else failure,
        detail="",
        family=family,
        model=model,
        mean_queries=q,
        max_queries=int(q),
        mean_latency_ms=1.0 if q else 0.0,
        exec_s=0.1 if q else 0.0,
    )


def _passing_outcomes() -> list[TaskOutcome]:
    return [
        _o("P", "ownership", failure="wrong_result", q=2),
        _o("P", "efficiency", failure="wrong_result", q=12, task="f01"),
        _o("P", "control", correct=True, q=2),
        _o("B", "ownership", correct=True, q=2),
        _o("B", "efficiency", correct=True, q=2, task="f01"),
        _o("B", "control", correct=True, q=2, rounds=3),
    ]


def test_api_gate_passes() -> None:
    gate = evaluate_gate_api(_passing_outcomes())
    assert gate.passed, gate.checks
    names = [name for name, _, _ in gate.checks]
    assert names == [
        "silent_failure_reduction",
        "accuracy_parity",
        "query_efficiency",
        "same_model",
        "max_repair_rounds",
    ]
    detail = dict((n, d) for n, _, d in gate.checks)["query_efficiency"]
    assert detail == (
        "efficiency family (1 task ran in both arms): mean queries/request "
        "B = 2.00 (need <= P = 12.00)"
    )


def test_api_gate_uses_api_trap_families_not_bench2() -> None:
    outcomes = _passing_outcomes() + [_o("B", "validation", failure="wrong_result", q=1)] * 3
    checks = {n: ok for n, ok, _ in evaluate_gate_api(outcomes).checks}
    assert not checks["silent_failure_reduction"]


def test_api_gate_fails_on_more_queries_accuracy_model_and_rounds() -> None:
    base = _passing_outcomes()
    more_queries = base + [_o("B", "efficiency", correct=True, q=50, task="f01")]
    assert not dict((n, ok) for n, ok, _ in evaluate_gate_api(more_queries).checks)[
        "query_efficiency"
    ]
    worse = base + [_o("B", "control", failure="static")] * 10
    assert not dict((n, ok) for n, ok, _ in evaluate_gate_api(worse).checks)["accuracy_parity"]
    two_models = base + [_o("P", "control", correct=True, model="other")]
    assert not dict((n, ok) for n, ok, _ in evaluate_gate_api(two_models).checks)["same_model"]
    too_many = base + [_o("B", "control", correct=True, rounds=4)]
    assert not dict((n, ok) for n, ok, _ in evaluate_gate_api(too_many).checks)["max_repair_rounds"]


def test_api_gate_query_check_ignores_outcomes_whose_cases_never_ran() -> None:
    outcomes = _passing_outcomes() + [_o("B", "efficiency", failure="static")]
    assert dict((n, ok) for n, ok, _ in evaluate_gate_api(outcomes).checks)["query_efficiency"]


def test_api_gate_query_check_pairs_tasks_that_ran_in_both_arms() -> None:
    # B's cheap f02 has no P counterpart that ran (P's f02 never started), and P's
    # f03 has no B counterpart: both are left out, so only f01 is compared.
    outcomes = _passing_outcomes() + [
        _o("B", "efficiency", correct=True, q=1, task="f02"),
        _o("P", "efficiency", failure="start_error", task="f02"),
        _o("P", "efficiency", failure="wrong_result", q=1, task="f03"),
    ]
    detail = dict((n, d) for n, _, d in evaluate_gate_api(outcomes).checks)["query_efficiency"]
    assert detail == (
        "efficiency family (1 task ran in both arms): mean queries/request "
        "B = 2.00 (need <= P = 12.00)"
    )
    unpaired = [
        _o("B", "efficiency", correct=True, q=1, task="f02"),
        _o("P", "efficiency", failure="wrong_result", q=9, task="f03"),
    ]
    checks = {n: (ok, d) for n, ok, d in evaluate_gate_api(unpaired).checks}
    assert checks["query_efficiency"] == (
        False,
        "no efficiency-family task ran its cases in both arm B and arm P",
    )


def test_api_report_has_family_rows_query_columns_and_gate() -> None:
    report = render_markdown_api(_passing_outcomes())
    assert report.startswith("# Lattice Benchmark Report — API suite")
    assert "| B | efficiency |" in report
    assert "Mean queries / request | Max queries | Mean request latency (ms) |" in report
    assert "`query_efficiency`" in report
    assert "**Result: PASS**" in report


# ---------------------------------------------------------------- CLI ------


def _install_fake_modules(monkeypatch: pytest.MonkeyPatch, runtime) -> types.SimpleNamespace:
    calls = types.SimpleNamespace(built=[], profiles=0)
    tasks_mod = types.ModuleType("bench.api_tasks")
    tasks_mod.API_TASKS = (TASK,)  # type: ignore[attr-defined]
    data_mod = types.ModuleType("bench.api_datasets")

    def build_db(path: Path, seed: int = 21) -> None:
        calls.built.append((Path(path), seed))
        build_tiny_db(path)

    def api_profile() -> str:
        calls.profiles += 1
        return "PROFILE"

    data_mod.build_db = build_db  # type: ignore[attr-defined]
    data_mod.api_profile = api_profile  # type: ignore[attr-defined]
    prompt_mod = types.ModuleType("lattice.llm.api_prompt")
    stub = StubPrompts()
    prompt_mod.api_lattice_system_prompt = stub.api_lattice_system_prompt  # type: ignore[attr-defined]
    prompt_mod.api_baseline_system_prompt = stub.api_baseline_system_prompt  # type: ignore[attr-defined]
    prompt_mod.api_task_message = stub.api_task_message  # type: ignore[attr-defined]
    for name, mod in (
        ("bench.api_tasks", tasks_mod),
        ("bench.api_datasets", data_mod),
        ("lattice.llm.api_prompt", prompt_mod),
        ("lattice.api.runtime", runtime),  # the fake runtime namespace itself
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    return calls


def test_cli_suite_api_runs_all_arms_and_writes_the_api_report(
    tmp_path: Path, kit_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bench.cli

    runtime = make_fake_runtime()
    calls = _install_fake_modules(monkeypatch, runtime)
    monkeypatch.setattr(bench.cli, "API_KIT_PATH", kit_path)

    offline = tmp_path / "offline"
    for arm, text in {
        "A": _fenced(NAIVE_SCRIPT),
        "P": _fenced(IMPORT_ERROR_SCRIPT) + "\n=====\n" + _fenced(NAIVE_SCRIPT),
        "B": _fenced("REJECT") + "\n=====\n" + _fenced("GOOD"),
        "C": _fenced("GOOD"),
    }.items():
        (offline / arm).mkdir(parents=True)
        (offline / arm / "t_orders.txt").write_text(text)
    out = tmp_path / "out"

    assert (
        bench.cli.main(
            [
                "run",
                "--suite",
                "api",
                "--arms",
                "A,P,B,C",
                "--offline",
                str(offline),
                "--out",
                str(out),
            ]
        )
        == 0
    )

    assert calls.built == [(out / "fixtures" / "app.db", 21)]
    assert (out / "fixtures" / "app.db").exists()
    outcomes = {o.arm: o for o in read_outcomes(out / "outcomes.jsonl")}
    assert outcomes["A"].failure == "wrong_result" and outcomes["A"].rounds == 1
    assert outcomes["P"].failure == "wrong_result" and outcomes["P"].rounds == 2
    assert outcomes["B"].correct and outcomes["B"].rounds == 2
    assert outcomes["C"].correct and outcomes["C"].rounds == 1
    assert outcomes["B"].mean_queries > 0  # counted through the runtime's on_connect hook
    report = (out / "report.md").read_text()
    assert report.startswith("# Lattice Benchmark Report — API suite")
    assert "`query_efficiency`" in report

    # Re-rendering from outcomes.jsonl picks the api report too.
    assert bench.cli.main(["report", str(out)]) == 0
    assert (out / "report.md").read_text().startswith("# Lattice Benchmark Report — API suite")
    json.loads((out / "outcomes.jsonl").read_text().splitlines()[0])
