"""Statement counting beyond MySQL (``lattice.perf.measure.SqlCounter``, the descriptors'
``[perf]`` table; pre-registration ``docs/results/2026-10-15-perf-engines-prereg.md``).

No Docker and no driver package: the fake servers, drivers and ``docker`` of
``tests/perf/fakeengines.py`` stand in (SQLite behind them, each engine's statement
statistics modelled as the descriptors' SQL expects). What these tests show is the TOOL's
logic: the SQL it sends and binds, the rows it expands, the database it filters by, the two
failure kinds, the container's argv. They do not show what ``pg_stat_statements`` or
``system.query_log`` do (``test_counting_docker.py``, skipped without Docker, does)."""

from __future__ import annotations

import copy
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from lattice.perf import measure as measure_mod
from lattice.perf.budget import parse
from lattice.perf.measure import (
    Harness,
    MeasureError,
    SqlCounter,
    Unmeasured,
    bind,
    measure,
)
from lattice.spec import db as spec_db
from lattice.spec import dbserver, engines
from lattice.spec.engines import REGISTRY, EngineError, parse_descriptor
from tests.perf import fakeengines as fe
from tests.perf import serverapp

SEED = {"items": "N", "steps": [{"method": "POST", "path": "/items", "user": "ann",
                                 "count": "N", "json": {"name": "item-{i}"}}]}  # fmt: skip


def _budget(bid: str, endpoint: str, constant: int, per_item: int, seed: Any = SEED) -> dict:
    queries = {"constant": constant, "per_item": per_item}
    return {"id": bid, "endpoint": endpoint, "user": "ann", "seed": seed, "sizes": [3, 7],
            "queries": queries, "latency_ms": None}  # fmt: skip


def _file(*budgets: dict):
    return parse({"version": 1, "budgets": list(budgets)})


def _harness(database: str, app=serverapp.make_app, url: str | None = None) -> Harness:
    return Harness(app, serverapp.build_db, None, serverapp.login, database, url)


@pytest.fixture
def world():
    with fe.installed() as w:
        yield w


# ---- the [perf] table -----------------------------------------------------------------------


def _with_perf(perf: Any, base: str = "postgres") -> dict:
    data = copy.deepcopy(engines.engine(base).to_dict())
    if perf is None:
        data.pop("perf", None)
    else:
        data["perf"] = perf
    return data


SQL_OK = {"counter": "sql", "source": "s", "read": "SELECT q, c FROM t WHERE d = {db}"}


@pytest.mark.parametrize(
    ("perf", "message"),
    [
        (
            {**SQL_OK, "counter": "evil:Code"},
            'E: perf.counter: must be "general_log" or "sql", not \'evil:Code\'',
        ),
        (
            {**SQL_OK, "docker_args": ["--privileged"]},
            "E: [perf] unknown field 'docker_args' (known: counter, source, server_args, setup, "
            "check, begin, mark, flush, read)",
        ),
        (
            {**SQL_OK, "read": "SELECT 1"},
            "E: perf.read: must use {db} (the measured database; the tool's own statements are "
            "excluded by it)",
        ),
        (
            {**SQL_OK, "setup": ["SELECT {db}"]},
            "E: perf.setup: unknown placeholder {db} (known: none)",
        ),
        (
            {**SQL_OK, "check": ["SELECT {mark}"]},
            "E: perf.check: unknown placeholder {mark} (known: none)",
        ),
        (
            {**SQL_OK, "begin": ["SELECT {mark}"]},
            "E: perf.begin: unknown placeholder {mark} (known: {db})",
        ),
        ({**SQL_OK, "mark": "SELECT {db}"}, "E: perf.mark: unknown placeholder {db} (known: none)"),
        (
            {"counter": "general_log", "read": "SELECT {db}"},
            'E: perf.read: only for counter = "sql"',
        ),
        ({**SQL_OK, "read": "SELECT {db}, {mark}"}, "E: perf.read: {mark} needs perf.mark"),
        ({**SQL_OK, "mark": "SELECT 1"}, "E: perf.read: must use {mark} when perf.mark is set"),
        ({"counter": "sql", "source": "s"}, "E: [perf] missing required field 'read'"),
        ({"counter": "sql", "read": "SELECT {db}"}, "E: [perf] missing required field 'source'"),
        ({"source": "s"}, "E: [perf] missing required field 'counter'"),
        ({**SQL_OK, "setup": [""]}, "E: perf.setup: must be a list of non-empty strings"),
        ({**SQL_OK, "server_args": "-c x"}, "E: perf.server_args: must be a list of strings"),
        ("sql", "E: perf: must be a table, not a string"),
    ],
)
def test_perf_section_validation_messages(perf, message):
    with pytest.raises(EngineError) as err:
        parse_descriptor(_with_perf(perf), "E")
    assert str(err.value) == message


def test_perf_section_accepts_a_complete_sql_table_and_round_trips():
    full = {**SQL_OK, "server_args": ["-c", "a=1"], "setup": ["CREATE EXTENSION x"],
            "check": ["SELECT 1"], "begin": ["SELECT reset({db})"], "mark": "SELECT now()",
            "flush": ["FLUSH {db}"],
            "read": "SELECT q, c FROM t WHERE d = {db} AND t >= {mark}"}  # fmt: skip
    e = parse_descriptor(_with_perf(full), "E")
    assert e.perf is not None and e.perf.counter == "sql" and e.perf.mark == "SELECT now()"
    assert parse_descriptor(e.to_dict(), "E") == e
    assert parse_descriptor(_with_perf(None), "E").perf is None
    assert "perf" not in parse_descriptor(_with_perf(None), "E").to_dict()


def test_builtin_descriptors_carry_perf():
    """The pre-registered counters (decisions 4 and 5), written out independently."""
    pg = engines.engine("postgres").perf
    assert pg is not None and pg.counter == "sql"
    assert pg.server_args == ("-c", "shared_preload_libraries=pg_stat_statements",
                              "-c", "pg_stat_statements.track=top",
                              "-c", "pg_stat_statements.track_utility=on")  # fmt: skip
    assert pg.setup == ("CREATE EXTENSION IF NOT EXISTS pg_stat_statements",)
    assert pg.check == ("SELECT count(*) FROM pg_stat_statements",)
    assert pg.begin == ("SELECT pg_stat_statements_reset(0::oid, (SELECT oid FROM pg_database "
                        "WHERE datname = {db}), 0::bigint)",)  # fmt: skip
    assert pg.read == ("SELECT query, calls FROM pg_stat_statements WHERE dbid = (SELECT oid "
                       "FROM pg_database WHERE datname = {db}) AND toplevel ORDER BY query, "
                       "queryid")  # fmt: skip
    assert pg.mark == "" and pg.flush == ()
    assert pg.source == ("pg_stat_statements (top-level statement executions in the measured "
                         "database)")  # fmt: skip
    ch = engines.engine("clickhouse").perf
    assert ch is not None and ch.counter == "sql"
    assert ch.server_args == () and ch.setup == () and ch.begin == ()
    assert ch.check == ("SYSTEM FLUSH LOGS", "SELECT count() FROM system.query_log WHERE 0")
    assert ch.mark == "SELECT toUnixTimestamp64Micro(now64(6))"
    assert ch.flush == ("SYSTEM FLUSH LOGS",)
    assert ch.read == ("SELECT query, 1 FROM system.query_log WHERE current_database = {db} AND "
                       "is_initial_query = 1 AND type != 'QueryStart' AND "
                       "toUnixTimestamp64Micro(query_start_time_microseconds) >= {mark} ORDER BY "
                       "query_start_time_microseconds, event_time_microseconds, query SETTINGS "
                       "final = 0")  # fmt: skip
    assert ch.source == ("system.query_log (initial queries finished or failed in the measured "
                         "database)")  # fmt: skip
    my = engines.engine("mysql").perf
    assert my is not None and my.counter == "general_log"
    for name in ("postgres", "clickhouse", "mysql"):
        e = engines.engine(name)
        assert parse_descriptor(e.to_dict()) == e


def test_bind_makes_placeholders_and_parameters_in_order():
    assert bind("SELECT {mark} {db} {db}", "%s", {"db": "d", "mark": 7}) == (
        "SELECT %s %s %s", [7, "d", "d"])  # fmt: skip
    assert bind("SELECT 1 WHERE x LIKE 'a%%'", "%s", {}) == ("SELECT 1 WHERE x LIKE 'a%'", [])
    assert bind("SELECT '%%' WHERE d = {db}", "%s", {"db": "d"}) == (
        "SELECT '%%' WHERE d = %s", ["d"])  # fmt: skip
    assert bind("SELECT {db}", "?", {"db": "d"}) == ("SELECT ?", ["d"])


# ---- dispatch and the unmeasured verdicts -----------------------------------------------------


def test_descriptor_without_perf_is_unmeasured_with_reason(world):
    data = _with_perf(None)  # an engine a project adds, as docs/examples/engines/mariadb.toml
    data.update(name="mariadb", title="MariaDB", aliases=[], url_schemes=["maria"])
    with REGISTRY.scoped() as reg:
        reg.add(parse_descriptor(data, "mariadb"), source="project")
        assert engines.engine("mariadb").perf is None
        (r,) = measure(_file(_budget("b", "GET /items", 2, 0)), _harness("mariadb"), runs=1)
    assert r.verdict == "unmeasured" and r.sizes == []
    assert r.notes == ["unmeasured: statement counting on MariaDB is not supported (its engine "
                       "descriptor has no [perf] section)"]  # fmt: skip
    assert world.docker_calls == [] and world.servers == {}  # no server was started


def test_unknown_engine_keeps_the_old_note():
    (r,) = measure(_file(_budget("b", "GET /items", 2, 0)), _harness("oracle"), runs=1)
    assert r.notes == ["unmeasured: statement counting on oracle is not supported (MySQL and "
                       "in-process SQLite only)"]  # fmt: skip


def test_dispatch_by_descriptor_counter(world, monkeypatch):
    def counter_for(database: str) -> Any:
        with tempfile.TemporaryDirectory() as d, measure_mod._databases(
            _harness(database), Path(d)
        ) as (_make, counter, _notes):  # fmt: skip
            return type(counter).__name__

    assert counter_for("postgres") == "SqlCounter"
    assert counter_for("postgresql") == "SqlCounter"  # an alias
    assert counter_for("clickhouse") == "SqlCounter"
    # an engine a project adds: the counter follows its descriptor, not its name
    data = copy.deepcopy(engines.engine("postgres").to_dict())
    data.update(name="pgx", aliases=[], url_schemes=["pgx"])
    with REGISTRY.scoped() as reg:
        reg.add(parse_descriptor(data, "pgx"), source="project")
        assert counter_for("pgx") == "SqlCounter"
    # general_log: MySQL's counter, with the server asked for by the engine's name
    asked: list[tuple[str, tuple]] = []

    def fake_server_for(dialect, url=None, name_prefix="lattice-", extra_args=()):
        asked.append((dialect, tuple(extra_args)))
        raise dbserver.DbServerError("stop here")

    monkeypatch.setattr(dbserver, "server_for", fake_server_for)
    data = copy.deepcopy(engines.engine("mysql").to_dict())
    data.update(name="mysql2", aliases=[], url_schemes=["mysql2"])
    with REGISTRY.scoped() as reg:
        reg.add(parse_descriptor(data, "mysql2"), source="project")
        for name in ("mysql", "mysql2"):
            with pytest.raises(MeasureError, match="stop here"):
                counter_for(name)
    assert asked == [("mysql", ()), ("mysql2", ())]


def test_check_failure_is_unmeasured_and_server_stopped(world, monkeypatch):
    """A container whose statistics are not available (pgss not loaded): every budget is
    unmeasured with the reason, the report still comes, and the container is removed."""
    real = dbserver.start_container

    def without_server_args(dialect, *a, extra_args=(), **kw):
        return real(dialect, *a, extra_args=(), **kw)

    monkeypatch.setattr(dbserver, "start_container", without_server_args)
    reports = measure(
        _file(_budget("a", "GET /items", 2, 0), _budget("b", "GET /nodb", 0, 0)),
        _harness("postgres"),
        runs=1,
    )
    assert [r.verdict for r in reports] == ["unmeasured", "unmeasured"]
    for r in reports:
        assert r.sizes == []
        (note,) = r.notes
        assert note.startswith("unmeasured: statement counting on PostgreSQL is not available "
                               "on this server: 'SELECT count(*) FROM pg_stat_statements' "
                               "failed: ")  # fmt: skip
        assert "pg_stat_statements" in note and "shared_preload_libraries" in note
    assert world.live_containers() == [] and len(world.removed) == 1


def test_setup_statements_run_only_on_a_container_the_tool_started(world):
    """Given a server (a URL), nothing is set up: no CREATE EXTENSION, so no pgss."""
    given = world.add_server("postgres", preloaded=True)
    (r,) = measure(_file(_budget("a", "GET /items", 2, 0)), _harness("postgres", url=given.url()),
                   runs=1)  # fmt: skip
    assert r.verdict == "unmeasured"
    assert "not available on this server" in r.notes[0] and "pg_stat_statements" in r.notes[0]
    assert not any("CREATE EXTENSION" in e[2] for e in given.events)
    assert world.docker_calls == []  # no container was started for a given server


# ---- the statements the counter sends -----------------------------------------------------------


class _Recording:
    """``open_db`` wrapped: the statements ``SqlCounter`` sends on its own connection."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.sent: list[tuple[str, str, Any]] = []
        real = spec_db.open_db
        outer = self

        class Conn:
            def __init__(self, inner: Any) -> None:
                self.inner = inner

            def _note(self, how: str, sql: str, params: Any) -> None:
                outer.sent.append((how, sql, list(params) if params else []))

            def execute(self, sql: str, params: Any = ()) -> None:
                self._note("execute", sql, params)
                return self.inner.execute(sql, params)

            def query(self, sql: str, params: Any = ()) -> Any:
                self._note("query", sql, params)
                return self.inner.query(sql, params)

            def scalar(self, sql: str, params: Any = ()) -> Any:
                self._note("scalar", sql, params)
                return self.inner.scalar(sql, params)

            def close(self) -> None:
                self.inner.close()

        monkeypatch.setattr(spec_db, "open_db", lambda ref: Conn(real(ref)))


def _counter(world: fe.World, engine: str, owned: bool = True) -> tuple[SqlCounter, Any]:
    e = engines.engine(engine)
    server, owned_by_us = dbserver.server_for(
        engine, None if owned else world.add_server(engine).url(),
        name_prefix="lattice-perf-", extra_args=e.perf.server_args,
    )  # fmt: skip
    assert owned_by_us is owned
    return SqlCounter(server, e), server


def test_sql_counter_sends_descriptor_statements_in_order(world, monkeypatch):
    rec = _Recording(monkeypatch)
    pg, server = _counter(world, "postgres")
    pg.start(True)
    pg.watch("postgresql://lattice:lattice@127.0.0.1:1/lattice_ab12cd34_c1?sslmode=disable")
    pg.begin()
    assert pg.end() == []
    perf = engines.engine("postgres").perf
    db = "lattice_ab12cd34_c1"
    assert rec.sent == [
        ("execute", perf.setup[0], []),
        ("execute", perf.check[0], []),
        ("execute", perf.begin[0].replace("{db}", "%s"), [db]),
        ("query", perf.read.replace("{db}", "%s"), [db]),
    ]
    assert db != server.engine.server.admin_db  # never the tool's own database
    pg.stop()
    server.stop()

    rec.sent.clear()
    ch, server = _counter(world, "clickhouse")
    ch.start(True)
    ch.watch("clickhouse://lattice:lattice@127.0.0.1:1/lattice_ab12cd34_c2")
    ch.begin()
    assert ch.end() == []
    perf = engines.engine("clickhouse").perf
    mark = ch.mark
    assert isinstance(mark, int) and mark > 0
    assert rec.sent == [
        ("execute", perf.check[0], []),
        ("execute", perf.check[1], []),
        ("scalar", perf.mark, []),
        ("execute", perf.flush[0], []),
        ("query", perf.read.replace("{db}", "%s").replace("{mark}", "%s"),
         ["lattice_ab12cd34_c2", mark]),
    ]  # fmt: skip
    ch.stop()
    server.stop()


class _Rows:
    """A stand-in connection whose ``read`` answers fixed rows."""

    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows

    def execute(self, sql: str, params: Any = ()) -> None:
        return None

    def query(self, sql: str, params: Any = ()) -> list[tuple]:
        return list(self.rows)

    def scalar(self, sql: str, params: Any = ()) -> Any:
        return 1

    def close(self) -> None:
        return None


def test_sql_counter_expands_calls(world):
    pg, server = _counter(world, "postgres")
    pg.conn = _Rows([("SELECT a", 3), ("SELECT b", 1), ("SELECT c", 0), (b"SELECT d", 2)])
    assert pg.end() == ["SELECT a"] * 3 + ["SELECT b"] + ["SELECT d"] * 2
    server.stop()


def test_sql_counter_on_given_server_skips_setup(world, monkeypatch):
    rec = _Recording(monkeypatch)
    perf = engines.engine("postgres").perf
    # a given server without pgss: only the check is sent, and it fails (unmeasured)
    given, _server = _counter(world, "postgres", owned=False)
    with pytest.raises(Unmeasured):
        given.start(False)
    assert [s[1] for s in rec.sent] == [perf.check[0]]
    # a container the tool started: setup, then check
    rec.sent.clear()
    started, server = _counter(world, "postgres")
    started.start(True)
    assert [s[1] for s in rec.sent] == [perf.setup[0], perf.check[0]]
    assert started.notes == []
    started.stop()
    server.stop()
    # a given server where counting works: the setup is NOT run and a note says so
    rec.sent.clear()
    ok = world.add_server("postgres", preloaded=True)
    ok.extensions.add(ok.admin_db)  # someone created the extension already
    counter = SqlCounter(dbserver.DbServer("postgres", ok.url()), engines.engine("postgres"))
    counter.start(False)
    assert [s[1] for s in rec.sent] == [perf.check[0]]
    assert counter.notes == ["counting on a given server: setup statements were not run"]
    counter.stop()


# ---- the liveness guard ------------------------------------------------------------------------


def _silent_app_factory(tmp_path: Path):
    """An app that ignores the database it is given (its data is a SQLite file of its own)
    and answers 2xx."""
    import sqlite3

    from fastapi import FastAPI

    file = tmp_path / "own.db"
    conn = sqlite3.connect(file, check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS items (name TEXT)")

    def make_app(url: str) -> FastAPI:
        app = FastAPI()

        @app.post("/login")
        def login(body: dict) -> Any:
            return {"token": "t"}

        @app.post("/items", status_code=201)
        def create(body: dict) -> Any:
            conn.execute("INSERT INTO items VALUES (?)", (str(body.get("name")),))
            return {"id": 1}

        @app.get("/items")
        def items() -> Any:
            return [r[0] for r in conn.execute("SELECT name FROM items")]

        @app.get("/nodb")
        def nodb() -> Any:
            return {"ok": True}

        return app

    return make_app


@pytest.mark.parametrize("engine", ["postgres", "clickhouse"])
def test_liveness_guard_marks_budget_unmeasured(world, tmp_path, engine):
    h = Harness(_silent_app_factory(tmp_path), serverapp.build_db, None, serverapp.login, engine)
    (r,) = measure(_file(_budget("b", "GET /items", 5, 5)), h, runs=2)
    assert r.verdict == "unmeasured" and r.sizes == [] and r.fit is None
    (_db, note) = r.notes
    assert note.startswith("unmeasured: no statement of the app was seen in database lattice_")
    assert "while seeding (N=3, 3 seed requests); the app may not use the database it was " \
           "given, or its statements are not logged" in note  # fmt: skip
    assert world.live_containers() == []


def test_liveness_guard_is_per_budget(world, tmp_path):
    """On an app that ignores its database, the budget whose seed left nothing is
    unmeasured and a budget with no seed request is judged (and says it was not guarded)."""
    h = Harness(_silent_app_factory(tmp_path), serverapp.build_db, None, serverapp.login,
                "postgres")  # fmt: skip
    no_seed = {"items": "N", "steps": []}
    seeded, free = measure(
        _file(_budget("items", "GET /items", 2, 0), _budget("free", "GET /nodb", 0, 0, no_seed)),
        h, runs=2,
    )  # fmt: skip
    assert (seeded.verdict, free.verdict) == ("unmeasured", "pass")
    assert free.notes[-1] == "the counter's liveness was not checked (no seed request)"


def test_liveness_not_checked_without_seed_requests(world):
    no_seed = {"items": "N", "steps": []}
    (r,) = measure(_file(_budget("a", "GET /nodb", 0, 0, seed=no_seed)), _harness("postgres"),
                   runs=2)  # fmt: skip
    assert r.verdict == "pass"
    assert r.notes[-1] == "the counter's liveness was not checked (no seed request)"
    (r,) = measure(_file(_budget("a", "GET /nodb", 0, 0)), _harness("postgres"), runs=2)
    assert "liveness" not in " ".join(r.notes)


# ---- counting through the real adapters (fake servers) ------------------------------------------


@pytest.mark.parametrize("engine", ["postgres", "clickhouse"])
def test_counts_on_a_simulated_server(world, engine):
    reports = measure(
        _file(_budget("items", "GET /items", 2, 0), _budget("n1-tight", "GET /items_n1", 2, 0),
              _budget("n1", "GET /items_n1", 2, 1), _budget("nodb", "GET /nodb", 0, 0)),
        _harness(engine), runs=3, want_shapes=True,
    )  # fmt: skip
    by = {r.budget.id: r for r in reports}
    assert [s.statements for s in by["items"].sizes] == [[2] * 3, [2] * 3]
    assert [s.statements for s in by["n1"].sizes] == [[5] * 3, [9] * 3]
    assert [s.statements for s in by["nodb"].sizes] == [[0] * 3, [0] * 3]
    assert [r.verdict for r in reports] == ["pass", "fail", "pass", "pass"]
    assert by["n1-tight"].fit.to_dict() == {"constant": 2, "per_item": 1, "exact": True}
    counts = sorted(c for c, _ in by["n1"].sizes[0].shapes)
    assert counts == [1, 1, 3]
    assert world.live_containers() == []


def test_pg_shapes_follow_read_order(world):
    (r,) = measure(_file(_budget("n1", "GET /items_n1", 2, 1)), _harness("postgres"), runs=1,
                   want_shapes=True)  # fmt: skip
    assert r.sizes[0].shapes == [
        (1, "SELECT * FROM items WHERE user_id = $? ORDER BY id"),
        (1, "SELECT id FROM users WHERE token = $?"),
        (3, "SELECT name FROM users WHERE id = $?"),
    ]


def test_the_tools_statements_are_not_counted(world, monkeypatch):
    """A noisy tool: statements on the measuring and on the admin connection inside a
    window are not the app's, because the read is bound to the measured database."""

    class Noisy(SqlCounter):
        def begin(self) -> None:
            super().begin()
            for _ in range(5):
                self.conn.query("SELECT 1")
                self.server.admin().query("SELECT 1")

    monkeypatch.setattr(measure_mod, "SqlCounter", Noisy)
    for engine in ("postgres", "clickhouse"):
        (r,) = measure(_file(_budget("b", "GET /items", 2, 0)), _harness(engine), runs=2)
        assert [s.statements for s in r.sizes] == [[2, 2], [2, 2]], engine


def test_a_dead_server_is_cannot_run_not_unmeasured(world):
    """The server dying mid-run is an exception (the CLI's exit 2 on stderr)."""
    killed: list[str] = []

    def app_that_kills(url: str):
        if not killed:
            killed.append("x")
            for name in world.live_containers():
                world.kill(name)
        return serverapp.make_app(url)

    h = Harness(app_that_kills, serverapp.build_db, None, serverapp.login, "postgres")
    with pytest.raises(spec_db.DatabaseError, match="closed the connection|cannot connect"):
        measure(_file(_budget("b", "GET /items", 2, 0)), h, runs=1)
    assert len(world.removed) == 1  # the dead container was still removed (docker rm)


# ---- the container's command --------------------------------------------------------------------

_BASE = ["run", "-d", "--name", "lattice-perf-{e}-00000000", "--label",
         "lattice.spec=throwaway", "-p", "127.0.0.1::{port}"]  # fmt: skip
ARGV = {
    "postgres": [*_BASE, "--tmpfs", "/var/lib/postgresql/data:rw", "-e", "POSTGRES_USER=lattice",
                 "-e", "POSTGRES_PASSWORD=lattice", "-e", "POSTGRES_DB=postgres",
                 "postgres:16-alpine", "-c", "fsync=off", "-c", "synchronous_commit=off",
                 "-c", "full_page_writes=off", "-c", "max_connections=300"],
    "mysql": [*_BASE, "--tmpfs", "/var/lib/mysql:rw", "-e", "MYSQL_ROOT_PASSWORD=lattice",
              "mysql:8.4", "--innodb-flush-log-at-trx-commit=0", "--skip-log-bin",
              "--sync-binlog=0", "--max-connections=300", "--innodb-doublewrite=OFF"],
    "clickhouse": [*_BASE, "--tmpfs", "/var/lib/clickhouse:rw", "-e", "CLICKHOUSE_USER=lattice",
                   "-e", "CLICKHOUSE_PASSWORD=lattice", "-e",
                   "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1", "--ulimit", "nofile=262144:262144",
                   "clickhouse/clickhouse-server:24.8-alpine"],
}  # fmt: skip
PORTS = {"postgres": 5432, "mysql": 3306, "clickhouse": 8123}


def _argv_of(monkeypatch, engine: str, **kw: Any) -> list[str]:
    import shutil

    seen: list[tuple[str, ...]] = []

    def docker(*args: str, timeout: float = 0.0):
        seen.append(args)
        return subprocess.CompletedProcess(list(args), 1, "", "stop")

    monkeypatch.setattr(dbserver, "_docker", docker)
    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **k: "/usr/bin/docker")
    monkeypatch.setattr("secrets.token_hex", lambda n: "00000000")
    monkeypatch.delenv("LATTICE_CONTAINER_OWNER", raising=False)
    with pytest.raises(dbserver.DbServerError):
        dbserver.start_container(engine, name_prefix="lattice-perf-", **kw)
    return [a for a in seen[0]]


@pytest.mark.parametrize("engine", ["postgres", "mysql", "clickhouse"])
def test_start_container_argv_unchanged_without_extra_args(monkeypatch, engine):
    expected = [a.format(e=engine, port=PORTS[engine]) for a in ARGV[engine]]
    assert _argv_of(monkeypatch, engine) == expected
    assert _argv_of(monkeypatch, engine, extra_args=()) == expected  # the default, spelled out


def test_extra_args_follow_server_args(monkeypatch):
    perf = engines.engine("postgres").perf
    base = [a.format(e="postgres", port=5432) for a in ARGV["postgres"]]
    argv = _argv_of(monkeypatch, "postgres", extra_args=perf.server_args)
    assert argv == [*base, *perf.server_args]
    assert argv[argv.index("postgres:16-alpine") + 1 :] == [
        *engines.engine("postgres").server.args, *perf.server_args]  # fmt: skip
    # and the perf run asks for them (a started container only)
    asked: list[dict] = []

    def fake_server_for(dialect, url=None, name_prefix="lattice-", extra_args=()):
        asked.append({"dialect": dialect, "extra_args": tuple(extra_args)})
        raise dbserver.DbServerError("stop here")

    monkeypatch.setattr(dbserver, "server_for", fake_server_for)
    with pytest.raises(MeasureError, match="stop here"):
        measure(_file(_budget("b", "GET /items", 2, 0)), _harness("postgres"), runs=1)
    assert asked == [{"dialect": "postgres", "extra_args": perf.server_args}]
