"""The multi-vendor spec (bench/specs/multivendor.py) and its ticket deltas
(bench/specs/multivendor_tickets.py): consistent, every program passes the Lattice
checker, every invariant holds on the original app's seed, and the scenarios hold where
something can run them today -- the base scenarios on the original app, the scenarios of
deltas whose endpoints all have programs on the Lattice runtime.

The scenario runner here is a small stand-in for layer D (W2-A): a fresh seed database
per scenario, `expect_body` compared exactly, `expect_db` with one_of values by value."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from bench.import_repos.multivendor import make_app
from bench.import_repos.multivendor.app.core.security import create_access_token
from bench.import_repos.multivendor.seed import build_seed_db
from bench.specs.multivendor import NATIVES, PROFILE, SPEC
from bench.specs.multivendor_tickets import DELTAS, apply
from lattice.api.ir import lower_api
from lattice.api.runtime import build_app
from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.spec import Scenario, Spec, compile_invariant, validate
from lattice.spec.validate import _endpoints

TICKETS = sorted(DELTAS)


def _errors(source: str) -> list[str]:
    result = check_source(source, PROFILE)
    errors = [d.render() for d in result.diagnostics if d.severity is Severity.ERROR]
    if result.program is None and not errors:
        errors = ["refused"]
    return errors


# ---- consistency -------------------------------------------------------------------------


def test_spec_validates() -> None:
    assert [p.render() for p in validate(SPEC)] == []


def test_the_deltas_cover_t31_to_t44() -> None:
    assert [t[:3] for t in TICKETS] == [f"t{n}" for n in range(31, 45)]


@pytest.mark.parametrize("ticket", TICKETS)
def test_spec_with_delta_validates(ticket: str) -> None:
    assert [p.render() for p in validate(apply(SPEC, DELTAS[ticket]))] == []


@pytest.mark.parametrize("ticket", TICKETS)
def test_every_delta_states_its_requirement(ticket: str) -> None:
    delta = DELTAS[ticket]
    assert delta.scenarios, "a delta states its ticket as concrete scenarios"
    applied = apply(SPEC, delta)
    assert len(applied.scenarios) == len(SPEC.scenarios) + len(delta.scenarios)


# ---- programs ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SPEC.programs))
def test_program_passes_the_checker(name: str) -> None:
    assert _errors(SPEC.programs[name]) == []


DELTA_PROGRAMS = [(t, n) for t in TICKETS for n in sorted(DELTAS[t].programs)]


@pytest.mark.parametrize(("ticket", "name"), DELTA_PROGRAMS)
def test_delta_program_passes_the_checker(ticket: str, name: str) -> None:
    assert _errors(DELTAS[ticket].programs[name]) == []


# ---- invariants --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seed_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_seed_db(tmp_path_factory.mktemp("seed") / "seed.db")


def _invariants() -> list[tuple[str, Any]]:
    found = [("base", inv) for inv in SPEC.invariants]
    found += [(t, inv) for t in TICKETS for inv in DELTAS[t].invariants]
    return found


@pytest.mark.parametrize(("where", "inv"), _invariants(), ids=lambda v: getattr(v, "name", v))
def test_invariant_holds_on_the_seed(where: str, inv: Any, seed_db: Path) -> None:
    sql = compile_invariant(inv, PROFILE)
    conn = sqlite3.connect(seed_db)
    try:
        violations = conn.execute(sql).fetchall()
    finally:
        conn.close()
    assert violations == [], f"{where}: {inv.name} is violated by {violations}"


# ---- a small scenario runner -------------------------------------------------------------


def _stored(table: str, column: str, value: Any) -> Any:
    found = SPEC.table(table)
    assert found is not None
    _, _, schema = found
    choices = schema.choices(column)
    stored = dict(schema.one_of_stored).get(column)
    if choices is not None and stored is not None and value in choices:
        return stored[choices.index(value)]
    return value


def _check_db(db: Path, expect: dict[str, Any], where: str) -> None:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        for name, check in expect.items():
            found = SPEC.table(name)
            assert found is not None, name
            table, sql_table, _ = found
            filters = check.get("where", {})
            clause = " AND ".join(f"{c} = ?" for c in filters) or "1"
            args = [_stored(table, c, v) for c, v in filters.items()]
            rows = conn.execute(f"SELECT * FROM {sql_table} WHERE {clause}", args).fetchall()
            if "count" in check:
                assert len(rows) == check["count"], f"{where}: {name} {filters} count"
            if "equals" in check:
                assert rows, f"{where}: no {name} row matches {filters}"
                for row in rows:
                    for c, v in check["equals"].items():
                        want = _stored(table, c, v)
                        assert row[c] == want, f"{where}: {name} {filters} {c}={row[c]!r}"
    finally:
        conn.close()


Login = Callable[[TestClient, str], dict[str, str]]


def _run(scenario: Scenario, client: TestClient, db: Path, login: Login) -> None:
    for index, step in enumerate(scenario.steps, 1):
        where = f"{scenario.name} step {index} ({step.method} {step.path})"
        headers = login(client, step.user) if step.user else {}
        response = client.request(
            step.method, step.path, json=step.json, params=step.params, headers=headers
        )
        assert response.status_code == step.expect_status, f"{where}: {response.text}"
        if step.expect_body is not None:
            assert response.json() == step.expect_body, where
        if step.expect_db is not None:
            _check_db(db, dict(step.expect_db), where)


def _upstream_login(client: TestClient, handle: str) -> dict[str, str]:
    email, password = SPEC.users[handle]
    r = client.post("/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def original(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    db = build_seed_db(tmp_path / "app.db")
    app = make_app(db)
    try:
        with TestClient(app) as client:
            yield client, db
    finally:
        app.state.engine.dispose()


@pytest.mark.parametrize("scenario", SPEC.scenarios, ids=lambda s: s.name)
def test_base_scenario_holds_on_the_original(
    scenario: Scenario, original: tuple[TestClient, Path]
) -> None:
    client, db = original
    _run(scenario, client, db, _upstream_login)


# Deltas whose every scenario endpoint has a program in the spec + delta run on the Lattice
# runtime: a check that the hand-computed expectations agree with the programs.


def _served(spec: Spec) -> set[tuple[str, str]]:
    return {(m, p) for source in spec.programs.values() for _, m, p in _endpoints(source)}


def _matches(path: str, pattern: str) -> bool:
    a, b = path.split("?")[0].strip("/").split("/"), pattern.strip("/").split("/")
    return len(a) == len(b) and all(y.startswith("{") or x == y for x, y in zip(a, b, strict=True))


def _runnable(ticket: str) -> bool:
    spec = apply(SPEC, DELTAS[ticket])
    served = _served(spec)
    return bool(DELTAS[ticket].programs) and all(
        any(m == step.method and _matches(step.path, p) for m, p in served)
        for scenario in DELTAS[ticket].scenarios
        for step in scenario.steps
    )


RUNNABLE = [t for t in TICKETS if _runnable(t)]


def _token_login(db: Path) -> Login:
    def login(client: TestClient, handle: str) -> dict[str, str]:
        conn = sqlite3.connect(db)
        try:
            (uid,) = conn.execute(
                "SELECT id FROM users WHERE email = ?", (SPEC.users[handle][0],)
            ).fetchone()
        finally:
            conn.close()
        return {"Authorization": f"Bearer {create_access_token({'sub': str(uid)})}"}

    return login


def test_some_delta_runs_on_the_runtime() -> None:
    assert RUNNABLE


@pytest.mark.parametrize("ticket", RUNNABLE)
def test_delta_scenarios_hold_on_the_lattice_runtime(ticket: str, tmp_path: Path) -> None:
    spec = apply(SPEC, DELTAS[ticket])
    plans = []
    for source in spec.programs.values():
        checked = check_source(source, PROFILE)
        assert checked.program is not None
        plans += lower_api(checked.program)
    for scenario in DELTAS[ticket].scenarios:
        db = build_seed_db(tmp_path / f"{scenario.name}.db")
        with TestClient(build_app(plans, db, NATIVES)) as client:
            _run(scenario, client, db, _token_login(db))
