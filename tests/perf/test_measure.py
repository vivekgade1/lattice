"""lattice.perf.measure and the CLI: real runs against in-process FastAPI + SQLite apps
(tests/perf/perfapp.py and tests/spec/racy.py's notes app), statement counting through the
sqlite3 trace callback, the MySQL counter's filtering (with a fake connection), and the
exit codes (0 pass, 1 over budget, 2 cannot run)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

from lattice import cli
from lattice.perf.__main__ import main as perf_main
from lattice.perf.budget import parse
from lattice.perf.measure import Harness, MeasureError, MysqlCounter, SqliteCounter, measure
from tests.perf import perfapp
from tests.spec import racy

HERE = Path(__file__).resolve().parent


def _budget(endpoint, constant, per_item, sizes=(2, 6), path="/items", **extra):
    return {
        "id": extra.pop("id", "b"), "endpoint": endpoint, "user": "ann",
        "seed": {"items": "N", "steps": [{"method": "POST", "path": path, "user": "ann",
                                          "count": "N", "json": {"name": "item-{i}"}}]},
        "sizes": list(sizes), "queries": {"constant": constant, "per_item": per_item},
        "latency_ms": extra.pop("latency_ms", None), **extra,
    }  # fmt: skip


def _file(*budgets, **top):
    return parse({"version": 1, "budgets": list(budgets), **top})


PERFAPP = Harness(perfapp.make_app, perfapp.build_db, None, perfapp.login, "sqlite")


def test_constant_endpoint_counts_exactly():
    (r,) = measure(_file(_budget("GET /items", 2, 0)), PERFAPP, runs=3)
    assert [s.statements for s in r.sizes] == [[2, 2, 2], [2, 2, 2]]
    assert r.fit.to_dict() == {"constant": 2, "per_item": 0, "exact": True}
    assert r.verdict == "pass"
    assert all(len(s.latency_ms) == 3 and s.latency() for s in r.sizes)


def test_n_plus_one_is_fitted_and_fails_a_constant_budget():
    (r,) = measure(_file(_budget("GET /items_n1", 2, 0)), PERFAPP, runs=2, sizes=[3, 7])
    assert [s.statements for s in r.sizes] == [[5, 5], [9, 9]]
    assert r.fit.to_dict() == {"constant": 2, "per_item": 1, "exact": True}
    assert r.verdict == "fail"


def test_statement_shapes_on_request():
    (r,) = measure(_file(_budget("GET /items_n1", 2, 1)), PERFAPP, runs=1, want_shapes=True)
    assert r.sizes[0].shapes == [(1, "SELECT id FROM users WHERE token = ?"),
                                 (1, "SELECT * FROM items WHERE user_id = ? ORDER BY id"),
                                 (2, "SELECT name FROM users WHERE id = ?")]  # fmt: skip


def test_seeding_and_login_are_not_counted_and_the_patch_is_removed():
    before = sqlite3.connect
    measure(_file(_budget("GET /items", 2, 0)), PERFAPP, runs=1)
    assert sqlite3.connect is before
    import sqlite3.dbapi2 as dbapi2

    assert dbapi2.connect is before


def test_in_repo_racy_notes_app():
    """tests/spec/racy.py's notes app: publishing note 1 costs 4 statements whatever N."""
    h = Harness(racy.notes_app, racy.build_notes_db, None, racy.login, "sqlite")
    b = _budget("POST /notes/1/publish", 4, 0, path="/notes", sizes=(1, 10))
    (r,) = measure(_file(b), h, runs=3)
    assert [s.statements for s in r.sizes] == [[4, 4, 4], [4, 4, 4]]
    assert r.verdict == "pass" and r.fit.exact


def test_a_failing_seed_step_stops_the_run():
    b = _budget("GET /items", 2, 0, path="/nope")
    with pytest.raises(MeasureError, match="seed step POST /nope #1 answered 404"):
        measure(_file(b), PERFAPP, runs=1)


def test_a_failing_endpoint_stops_the_run():
    with pytest.raises(MeasureError, match="answered 500"):
        measure(_file(_budget("GET /broken", 2, 0)), PERFAPP, runs=1)


def test_process_app_on_sqlite_is_unmeasured():
    from lattice.spec.process import process_runtime

    h = Harness(perfapp.make_app, perfapp.build_db, process_runtime(), perfapp.login, "sqlite")
    (r,) = measure(_file(_budget("GET /items", 2, 0)), h, runs=1)
    assert r.verdict == "unmeasured" and r.sizes == []
    assert any("process app on SQLite is not supported" in n for n in r.notes)


def test_sizes_override_needs_two():
    with pytest.raises(MeasureError, match="two or more"):
        measure(_file(_budget("GET /items", 2, 0)), PERFAPP, sizes=[5])


def test_sqlite_counter_traces_only_the_watched_file(tmp_path):
    c = SqliteCounter()
    c.start()
    try:
        c.watch(tmp_path / "a.db")
        a = sqlite3.connect(tmp_path / "a.db")
        b = sqlite3.connect(tmp_path / "b.db")
        c.begin()
        a.execute("SELECT 1")
        b.execute("SELECT 2")
        assert c.end() == ["SELECT 1"]
        a.close()
        b.close()
    finally:
        c.stop()


class _FakeConn:
    """A MySQL connection double: records statements; the log read answers ``log``."""

    def __init__(self, thread: int, log: list[tuple]):
        self.thread, self.log, self.sent = thread, log, []

    def scalar(self, sql, params=()):
        self.sent.append(sql)
        return self.thread

    def query(self, sql, params=()):
        self.sent.append(sql)
        if "@@GLOBAL" in sql:
            return [(0, "FILE")]
        return list(self.log)

    def execute(self, sql, params=()):
        self.sent.append(sql if not params else (sql, params))

    def close(self):
        pass


def test_mysql_counter_truncates_counts_app_rows_and_restores(monkeypatch):
    log = [(41, "Query", "SELECT thread_id, command_type, argument FROM mysql.general_log"),
           (50, "Prepare", "SELECT * FROM t WHERE id = ?"),
           (50, "Execute", "SELECT * FROM t WHERE id = 1"),
           (50, "Close stmt", ""), (42, "Query", "CREATE DATABASE x")]  # fmt: skip
    tool, admin = _FakeConn(41, log), _FakeConn(42, [])

    class Server:
        url, container = "mysql://root:x@127.0.0.1:1/mysql", "lattice-perf-mysql-0"

        def admin(self):
            return admin

    monkeypatch.setattr("lattice.spec.db.open_db", lambda url: tool)
    c = MysqlCounter(Server())
    c.start()
    assert c.tool_threads == {41, 42}
    assert "SET GLOBAL general_log = 1" in tool.sent
    c.begin()
    assert tool.sent[-1] == "TRUNCATE TABLE mysql.general_log"
    assert c.end() == ["SELECT * FROM t WHERE id = 1"]
    c.stop()
    assert "SET GLOBAL general_log = 0" in tool.sent
    assert ("SET GLOBAL log_output = %s", ("FILE",)) in tool.sent


# ---- the CLI ------------------------------------------------------------------------------


def _project(tmp_path: Path, budgets: list[dict] | None, **top) -> Path:
    mod = f"perfapp_{uuid.uuid4().hex[:8]}"
    d = tmp_path / "proj"
    d.mkdir()
    shutil.copy(HERE / "perfapp.py", d / f"{mod}.py")
    (d / "studio.toml").write_text(
        f'[app]\napp = "{mod}:make_app"\ndb = "{mod}:build_db"\nlogin = "{mod}:login"\n'
    )
    if budgets is not None:
        (d / "perf.budget.json").write_text(json.dumps({"version": 1, "budgets": budgets, **top}))
    return d


@pytest.fixture(autouse=True)
def _restore_sys_path():
    saved = list(sys.path)
    yield
    sys.path[:] = saved


def test_cli_check_pass_is_exit_0(tmp_path, capsys):
    d = _project(tmp_path, [_budget("GET /items", 2, 0)])
    assert perf_main(["check", "--project", str(d), "--runs", "2"]) == 0
    out = capsys.readouterr().out
    assert "->  PASS" in out and out.strip().endswith("perf: pass (1 pass, 0 fail, 0 unmeasured)")


def test_cli_check_over_budget_is_exit_1_and_measure_is_0(tmp_path, capsys):
    d = _project(tmp_path, [_budget("GET /items_n1", 2, 0)])
    assert perf_main(["check", "--project", str(d), "--runs", "2"]) == 1
    assert "->  FAIL" in capsys.readouterr().out
    assert perf_main(["measure", "--project", str(d), "--runs", "1"]) == 0


def test_cli_json_report(tmp_path, capsys):
    d = _project(tmp_path, [_budget("GET /items_n1", 2, 1, id="n1")])
    assert perf_main(["check", "--project", str(d), "--runs", "2", "--json", "--sizes", "1,4"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "pass" and data["command"] == "check"
    (r,) = data["reports"]
    assert r["budget_id"] == "n1" and r["fit"] == {"constant": 2, "per_item": 1, "exact": True}
    assert [s["n"] for s in r["sizes"]] == [1, 4] and r["sizes"][1]["statements"] == [6, 6]


def test_cli_cannot_run_is_exit_2(tmp_path, capsys):
    d = _project(tmp_path, None)
    assert perf_main(["check", "--project", str(d)]) == 2  # no budget file
    assert "cannot read the budget file" in capsys.readouterr().err
    (d / "perf.budget.json").write_text('{"version": 1, "budgets": [{"id": "x"}]}')
    assert perf_main(["check", "--project", str(d)]) == 2  # invalid
    assert "invalid budget file" in capsys.readouterr().err
    (d / "perf.budget.json").write_text(json.dumps({"version": 1, "budgets": [
        _budget("GET /items", 2, 0)]}))  # fmt: skip
    assert perf_main(["check", "--project", str(d), "--id", "zzz"]) == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "perf.budget.json").write_text((d / "perf.budget.json").read_text())
    assert perf_main(["check", "--project", str(empty)]) == 2  # no harness
    assert "missing" in capsys.readouterr().err
    assert perf_main(["check", "--bogus"]) == 2


def test_cli_prepare_failure_is_exit_2(tmp_path, capsys):
    d = _project(tmp_path, [_budget("GET /items", 2, 0)], prepare="exit 3")
    assert perf_main(["check", "--project", str(d)]) == 2
    assert "prepare 'exit 3' failed (3)" in capsys.readouterr().err


def test_lattice_perf_dispatch(tmp_path, capsys):
    d = _project(tmp_path, [_budget("GET /items_n1", 2, 0)])
    assert cli.main(["perf", "check", "--project", str(d), "--runs", "1"]) == 1
    assert cli.main(["perf", "--help"]) == 0
