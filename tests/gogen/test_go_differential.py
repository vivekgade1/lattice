"""Equivalence gate (spec 2026-09-28 go-target §6, plan 5a Task 13): every program of the
API codegen differential registry that passes check_source(..., target="go") behaves
identically when compiled to Go and when served by the Python runtime: status, full JSON
body (type-strict), SQL statement count, after_sql rows and a digest of the whole
database, on the task's cases and the generated probes (the registry's cases include
them). Programs rejected for Go only with L890 (natives, for_each of an effect,
NativeToken auth) are the expected exclusions, asserted by name.

Both sides go through the same harness code: `run_cases_lattice` (the runtime, in
process) and `run_cases_go` (the Go binary behind `lattice.gogen.proxy`'s ASGI proxy)
both judge each case with `_run_case_in_process`, and the same recording shim of the
codegen differential (`_Shim`) captures the body, after_sql rows and DB digest on each
side; `_mismatches` compares them exactly as it compares the generated FastAPI app.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

import lattice.api.runtime as runtime
from bench.api_model import ApiTask, HttpCase
from bench.api_runner import CaseResults, _is_counted_statement, run_cases_go
from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.gogen.build import build_go_app
from lattice.gogen.proxy import GoServer
from tests.api.test_codegen_differential import (
    PROGRAMS,
    Setup,
    _copier,
    _mismatches,
    _run_runtime,
    _Shim,
    normalize,
    setup_of,
)

pytestmark = pytest.mark.go  # builds and runs Go (skipped without `go`, tests/conftest.py)

# the programs whose only go-target errors are L890 (natives): not Go programs
EXPECTED_EXCLUSIONS = {
    "realapps",
    "partb",
    "effects",
    "gaps",
    "mine",
    "round2",
    "t2",
    "solo",
    "solo_runtime",
    "solo_answers",
    "n1b",
}
# the programs gated for Go by design (L892, docs/lattice-language-rules.md decision log):
# exact role sets (auth=Roles(...) / Authenticated, RoleColumn(values=...))
GATED_EXCLUSIONS = {"rolesets"}
GO_PIDS = sorted(set(PROGRAMS) - EXPECTED_EXCLUSIONS - GATED_EXCLUSIONS)


def _errors(pid: str) -> set[str]:
    source, _task = PROGRAMS[pid]
    result = check_source(source, setup_of(pid).profile, target="go")
    return {d.code for d in result.diagnostics if d.severity is Severity.ERROR}


def test_exclusions_are_exactly_the_native_programs():
    rejected = {pid: codes for pid in PROGRAMS if (codes := _errors(pid))}
    assert set(rejected) == EXPECTED_EXCLUSIONS | GATED_EXCLUSIONS
    assert all(rejected[p] == {"L890"} for p in EXPECTED_EXCLUSIONS), rejected
    assert all(rejected[p] == {"L892"} for p in GATED_EXCLUSIONS), rejected


def test_every_non_native_program_is_in_the_gate():
    """No program leaves the gate unseen: the gate runs every registry program but the
    L890 exclusions, including the ones added for it (edges, t2go)."""
    assert {"edges", "t2go", "hardening", "loops"} <= set(GO_PIDS)
    assert len(GO_PIDS) + len(EXPECTED_EXCLUSIONS) + len(GATED_EXCLUSIONS) == len(PROGRAMS)


# ---- running both sides ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def go_out(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One build directory (and so one cache) for the module."""
    return tmp_path_factory.mktemp("gogate")


def run_go(
    binary: Path, workdir: Path, task: ApiTask, pristine: Path
) -> tuple[CaseResults, list[dict]]:
    """`_run_runtime`'s twin for the Go binary: the results and the shim's records."""
    captured: list[dict] = []

    def wrap(app: Any, db_path: Path, case: HttpCase) -> _Shim:
        return _Shim(app, db_path, case, captured)

    results = run_cases_go(binary, task, db_builder=_copier(pristine), workdir=workdir, wrap=wrap)
    return results, captured


def compare(
    pid: str, source: str, task: ApiTask, setup: Setup, go_out: Path, tmp: Path
) -> tuple[list[str], tuple]:
    """(the mismatches, the runs) of `task` on the runtime and on Go."""
    tmp.mkdir(parents=True, exist_ok=True)
    pristine = tmp / "pristine.db"
    setup.build(pristine)
    built = build_go_app(source, setup.profile, go_out)
    on_runtime = _run_runtime(source, tmp / "rt", task, pristine, setup)
    on_go = run_go(built.binary, tmp / "go", task, pristine)
    runs = (*on_runtime, *on_go)
    return _mismatches(pid, runs, task), runs


@pytest.mark.parametrize("pid", GO_PIDS)
def test_go_equals_runtime(pid: str, go_out: Path, tmp_path: Path):
    source, task = PROGRAMS[pid]
    problems, runs = compare(pid, source, task, setup_of(pid), go_out, tmp_path)
    _, _, go_results, _ = runs
    assert len(go_results.cases) == len(task.cases) > 0
    assert problems == [], "\n".join(problems[:20])


# ---- the statements themselves, on the programs with T2 steps ------------------------------

T2_PIDS = ("t2go", "hardening")


def _request(client: Any, case: HttpCase) -> None:
    headers = {"Authorization": f"Bearer {case.token}"} if case.token is not None else {}
    client.request(
        case.method,
        case.path,
        params=dict(case.query) or None,
        json=case.json,
        headers=headers,
        follow_redirects=True,
    )


def _runtime_statements(
    source: str, setup: Setup, case: HttpCase, db: Path, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    """The data statements one request runs on the runtime, as the harness's trace
    callback sees them (SQLite's expanded text)."""
    seen: list[str] = []

    def trace(sql: str) -> None:
        if _is_counted_statement(sql):
            seen.append(sql)

    monkeypatch.setattr(runtime, "on_connect", lambda conn: conn.set_trace_callback(trace))
    check, app = runtime.serve_source(
        source, setup.profile, db, natives=setup.natives, settings=setup.settings
    )
    assert app is not None, [d.render() for d in check.diagnostics]
    with TestClient(app, raise_server_exceptions=False) as client:
        seen.clear()  # the request's statements only (as _run_case_in_process counts)
        _request(client, case)
    return seen


def _go_statements(binary: Path, case: HttpCase, db: Path, log: Path) -> list[str]:
    """The data statements one request runs on Go (LATTICE_TRACE_SQL_LOG)."""
    with (
        GoServer(binary, db, sql_log=log) as server,
        httpx.Client(base_url=server.base_url, timeout=300) as client,
    ):
        _request(client, case)
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.mark.parametrize("pid", T2_PIDS)
def test_t2_endpoints_run_the_same_statements(
    pid: str, go_out: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """X-Lattice-Sql-Count counts exactly the statements the Python trace sees: on every
    case of the programs with T2 steps (procedures in derive / where / require, call,
    insert_many of a procedure's rows), the Go app runs the same statements, with the
    same bound values, in the same order (values generated per request normalized)."""
    source, task = PROGRAMS[pid]
    setup = setup_of(pid)
    pristine = tmp_path / "pristine.db"
    setup.build(pristine)
    built = build_go_app(source, setup.profile, go_out)
    problems, ran = [], 0
    for i, case in enumerate(task.cases):
        rt_db, go_db = tmp_path / f"rt{i}.db", tmp_path / f"go{i}.db"
        shutil.copyfile(pristine, rt_db)
        shutil.copyfile(pristine, go_db)
        want = _runtime_statements(source, setup, case, rt_db, monkeypatch)
        got = _go_statements(built.binary, case, go_db, tmp_path / f"go{i}.log")
        ran += bool(want)
        if normalize(got) != normalize(want):
            problems.append(f"{case.name}: runtime {want!r} vs go {got!r}")
    assert problems == [], "\n".join(problems[:10])
    assert ran >= len(task.cases) // 3  # most cases reach SQL (not only 401s and 422s)
