"""Statement counting on real servers (``lattice.perf``, the descriptors' ``[perf]`` table):
Postgres (``pg_stat_statements``) and ClickHouse (``system.query_log``) in throwaway
containers, an app that talks to them through the real drivers (``tests/perf/serverapp.py``).

Skipped, with the reason, unless the Docker daemon answers and ``psycopg`` and
``clickhouse-connect`` are importable (``uv venv`` a scratch environment with
``"psycopg[binary]>=3.2" "clickhouse-connect>=0.8"``, see the pre-registration, section 3).
A skipped test is NOT a pass: ``tests/perf/test_counting.py`` shows the tool's logic on fake
servers; these show what the engines' own statistics do."""

from __future__ import annotations

import importlib.util
import subprocess
import uuid
from typing import Any

import pytest

from lattice.perf.budget import parse
from lattice.perf.measure import Harness, measure
from tests.perf import serverapp

SEED = {"items": "N", "steps": [{"method": "POST", "path": "/items", "user": "ann",
                                 "count": "N", "json": {"name": "item-{i}"}}]}  # fmt: skip


def _file() -> Any:
    def one(bid: str, endpoint: str, constant: int, per_item: int) -> dict:
        queries = {"constant": constant, "per_item": per_item}
        return {"id": bid, "endpoint": endpoint, "user": "ann", "seed": SEED, "sizes": [3, 7],
                "queries": queries, "latency_ms": None}  # fmt: skip

    return parse({"version": 1, "budgets": [
        one("items", "GET /items", 2, 0), one("n1-tight", "GET /items_n1", 2, 0),
        one("n1", "GET /items_n1", 2, 1), one("nodb", "GET /nodb", 0, 0)]})  # fmt: skip


def _reason(*modules: str) -> str | None:
    from lattice.spec.dbserver import docker_available

    if not docker_available():
        return "the Docker daemon does not answer (start Docker Desktop)"
    for m in modules:
        if importlib.util.find_spec(m) is None:
            return f"{m} is not installed (use a scratch venv, see the pre-registration)"
    return None


@pytest.fixture
def owner(monkeypatch):
    token = f"perf-test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("LATTICE_CONTAINER_OWNER", token)
    return token


def _left(token: str) -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label=lattice.owner={token}", "--format",
         "{{.Names}}"], capture_output=True, text=True, check=False,
    )  # fmt: skip
    return out.stdout.split()


def _run(engine: str, module: str, process: bool = False):
    reason = _reason(module)
    if reason:
        pytest.skip(reason)
    runtime = None
    app = serverapp.make_app
    if process:
        from lattice.spec.process import process_runtime

        runtime, app = process_runtime(), serverapp.make_process_app
    h = Harness(app, serverapp.build_db, runtime, serverapp.login, engine)
    return measure(_file(), h, runs=3, want_shapes=True)


def _check_counts(reports: list) -> None:
    by = {r.budget.id: r for r in reports}
    assert [s.statements for s in by["items"].sizes] == [[2] * 3, [2] * 3]
    assert [s.statements for s in by["n1"].sizes] == [[5] * 3, [9] * 3]
    assert [s.statements for s in by["nodb"].sizes] == [[0] * 3, [0] * 3]
    assert by["n1"].fit.to_dict() == {"constant": 2, "per_item": 1, "exact": True}
    assert [r.verdict for r in reports] == ["pass", "fail", "pass", "pass"]
    assert any("per_item 1 > budget 0" in n for n in by["n1-tight"].notes)


def test_pg_counts_exactly(owner):
    reports = _run("postgres", "psycopg")
    _check_counts(reports)
    shapes = next(r for r in reports if r.budget.id == "n1").sizes[0].shapes
    assert shapes == [
        (1, "SELECT * FROM items WHERE user_id = $? ORDER BY id"),
        (1, "SELECT id FROM users WHERE token = $?"),
        (3, "SELECT name FROM users WHERE id = $?"),
    ]
    assert _left(owner) == []


def test_pg_process_app_counts_the_same(owner):
    _check_counts(_run("postgres", "psycopg", process=True))
    assert _left(owner) == []


def test_ch_counts_exactly(owner):
    reports = _run("clickhouse", "clickhouse_connect")
    _check_counts(reports)
    shapes = next(r for r in reports if r.budget.id == "n1").sizes[0].shapes
    assert sorted(c for c, _ in shapes) == [1, 1, 3]
    assert all(s.startswith("SELECT ") for _, s in shapes)
    assert _left(owner) == []


def test_no_perf_container_left(owner):
    """After a measured run and a run that cannot happen (the seed fails), no container of
    this run's owner token remains (removal in ``finally``)."""
    _run("postgres", "psycopg")
    assert _left(owner) == []

    def broken_seed(url: str) -> str:
        raise RuntimeError("seed failed")

    h = Harness(serverapp.make_app, broken_seed, None, serverapp.login, "postgres")
    with pytest.raises(RuntimeError, match="seed failed"):
        measure(_file(), h, runs=1)
    assert _left(owner) == []
