"""The missing-index check on real servers (``lattice.perf.explain``): Postgres 16 and MySQL
8.4 in throwaway containers, the indexapp fixture, each variant (clean, index dropped) at
sizes 2 and 6.

Skipped, with the reason, unless the Docker daemon answers and the driver (``psycopg``,
``pymysql``) is importable (a scratch venv layered over the Lattice venv, see the
pre-registration). A skipped test is NOT a pass: ``tests/perf/test_explain.py`` shows the
tool's logic on plan fixtures and a recording connection; these show what the engines'
``EXPLAIN`` answers."""

from __future__ import annotations

import importlib.util
import subprocess
import uuid
from typing import Any

import pytest

from lattice.perf.budget import parse
from lattice.perf.explain import ExplainOptions
from lattice.perf.measure import Harness, measure
from tests.perf import indexapp

NOTES = [
    {"method": "POST", "path": "/items", "user": "ann", "count": 1, "json": {"name": "i"},
     "capture": {"item": "id"}},
    {"method": "POST", "path": "/items/{item}/notes", "user": "ann", "count": "N",
     "json": {"body": "note-{i}"}},
]  # fmt: skip
ITEMS = [{"method": "POST", "path": "/items", "user": "ann", "count": "N",
          "json": {"name": "item-{i}"}}]  # fmt: skip


def _file() -> Any:
    def one(bid: str, endpoint: str, constant: int, steps: list) -> dict:
        return {"id": bid, "endpoint": endpoint, "user": "ann", "sizes": [2, 6],
                "seed": {"items": "N", "steps": steps},
                "queries": {"constant": constant, "per_item": 0}, "latency_ms": None}  # fmt: skip

    return parse({"version": 1, "budgets": [
        one("items", "GET /items", 2, ITEMS),
        one("notes", "GET /items/{item}/notes", 3, NOTES)]})  # fmt: skip


def _reason(module: str) -> str | None:
    from lattice.spec.dbserver import docker_available

    if not docker_available():
        return "the Docker daemon does not answer (start Docker Desktop)"
    if importlib.util.find_spec(module) is None:
        return f"{module} is not installed (use a scratch venv, see the pre-registration)"
    return None


@pytest.fixture
def owner(monkeypatch):
    token = f"explain-test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("LATTICE_CONTAINER_OWNER", token)
    return token


def _left(token: str) -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label=lattice.owner={token}", "--format",
         "{{.Names}}"], capture_output=True, text=True, check=False,
    )  # fmt: skip
    return out.stdout.split()


def _run(engine: str, module: str, factory: Any):
    reason = _reason(module)
    if reason:
        pytest.skip(reason)
    h = Harness(indexapp.make_app, factory, None, indexapp.login, engine)
    return {r.budget.id: r for r in measure(_file(), h, runs=2, explain=ExplainOptions())}


@pytest.mark.parametrize(("engine", "module"), [("postgres", "psycopg"), ("mysql", "pymysql")])
def test_clean_schema_has_no_finding(owner, engine, module):
    got = _run(engine, module, indexapp.build_db)
    for r in got.values():
        assert r.explain["status"] == "checked", r.explain
        assert r.explain["findings"] == [], r.explain
    assert [s.statements for s in got["items"].sizes] == [[2, 2], [2, 2]]
    assert [s.statements for s in got["notes"].sizes] == [[3, 3], [3, 3]]
    assert _left(owner) == []


@pytest.mark.parametrize(("engine", "module"), [("postgres", "psycopg"), ("mysql", "pymysql")])
def test_dropped_index_is_found(owner, engine, module):
    got = _run(engine, module, indexapp.drop_items)
    assert [(f["table"], f["rows"]) for f in got["items"].explain["findings"]] == [("items", 6)]
    assert got["notes"].explain["findings"] == []
    assert got["items"].verdict == "pass"  # the count cannot see it
    got = _run(engine, module, indexapp.drop_notes)
    assert [(f["table"], f["rows"]) for f in got["notes"].explain["findings"]] == [("notes", 6)]
    assert got["items"].explain["findings"] == []
    assert _left(owner) == []
