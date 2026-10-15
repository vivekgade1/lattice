"""The missing-index check (``lattice perf --explain``, ``lattice.perf.explain``;
pre-registration ``docs/results/2026-10-15-perf-missing-index-prereg.md``).

No server and no driver: the SQLite path runs for real (a FastAPI app on a SQLite file, and
plain files for the classifier); MySQL and Postgres are shown by plan fixtures in each
engine's documented format and by a recording connection (the SQL the tool sends). What the
real servers answer is ``tests/perf/test_explain_docker.py`` (skipped without Docker)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from lattice.importer.transport import AppRuntime
from lattice.perf import explain as ex
from lattice.perf.__main__ import main as perf_main
from lattice.perf.budget import check_exit, explain_summary, parse, render
from lattice.perf.measure import Harness, SqliteCounter, measure, measure_size
from tests.perf import indexapp

HERE = Path(__file__).resolve().parent
USER_ITEMS = "SELECT id, user_id, name FROM items WHERE user_id = ? ORDER BY id"


def _budgets() -> list[dict]:
    steps = {"method": "POST", "path": "/items", "user": "ann", "count": "N",
             "json": {"name": "item-{i}"}}  # fmt: skip
    notes = [
        {"method": "POST", "path": "/items", "user": "ann", "count": 1, "json": {"name": "i"},
         "capture": {"item": "id"}},
        {"method": "POST", "path": "/items/{item}/notes", "user": "ann", "count": "N",
         "json": {"body": "note-{i}"}},
    ]  # fmt: skip
    return [
        {"id": "items", "endpoint": "GET /items", "user": "ann",
         "seed": {"items": "N", "steps": [steps]}, "sizes": [2, 6],
         "queries": {"constant": 2, "per_item": 0}, "latency_ms": None},
        {"id": "notes", "endpoint": "GET /items/{item}/notes", "user": "ann",
         "seed": {"items": "N", "steps": notes}, "sizes": [2, 6],
         "queries": {"constant": 3, "per_item": 0}, "latency_ms": None},
    ]  # fmt: skip


def _file(**top: Any):
    return parse({"version": 1, "budgets": _budgets(), **top})


def _harness(factory=indexapp.build_db, runtime: Any = None) -> Harness:
    return Harness(indexapp.make_app, factory, runtime, indexapp.login, "sqlite")


def _by_id(reports: list) -> dict:
    return {r.budget.id: r for r in reports}


def _opts(min_rows: int | None = None) -> ex.ExplainOptions:
    return ex.ExplainOptions(min_rows)


# ---- the SQLite path, end to end ------------------------------------------------------------


def test_sqlite_clean_has_no_finding():
    reports = measure(_file(), _harness(), runs=2, explain=_opts())
    for r in reports:
        e = r.explain
        assert e["status"] == "checked" and e["findings"] == [] and e["size"] == 6
        assert e["engine"] == "sqlite" and e["min_rows"] == 6 and e["skipped"] == []
    e = _by_id(reports)["items"].explain
    assert e["tables"] == {"users": 2} and e["below_threshold"] == 1
    assert explain_summary(reports)["verdict"] == "clean"


def test_sqlite_drop_index_is_found():
    r = _by_id(measure(_file(), _harness(indexapp.drop_items), runs=2, explain=_opts()))
    (f,) = r["items"].explain["findings"]
    assert (f["table"], f["rows"], f["access"]) == ("items", 6, "SCAN items")
    assert f["sql"] == USER_ITEMS
    assert r["notes"].explain["findings"] == []  # items holds 1 row there
    assert [x.verdict for x in r.values()] == ["pass", "pass"]  # the counts do not see it
    r = _by_id(measure(_file(), _harness(indexapp.drop_notes), runs=2, explain=_opts()))
    (f,) = r["notes"].explain["findings"]
    assert (f["table"], f["rows"], f["access"]) == ("notes", 6, "SCAN notes")
    assert r["items"].explain["findings"] == []


def test_min_rows_zero_checks_every_table():
    reports = measure(_file(), _harness(), runs=1, explain=_opts(0))
    for r in reports:
        (f,) = r.explain["findings"]
        assert (f["table"], f["rows"], f["access"]) == ("users", 2, "SCAN users")
        assert r.explain["min_rows"] == 0 and r.explain["below_threshold"] == 0


def _db_with_rows(tmp_path: Path, rows: int, index: bool) -> Path:
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT)")
    if index:
        conn.execute("CREATE INDEX iu ON items(user_id)")
    conn.executemany("INSERT INTO items (user_id, name) VALUES (?, ?)",
                     [(i, f"n{i}") for i in range(rows)])  # fmt: skip
    conn.commit()
    conn.close()
    return path


def test_threshold_boundary_counts(tmp_path):
    """A table with exactly min_rows rows is a finding; one fewer is not (counted below)."""
    db = _db_with_rows(tmp_path, 6, index=False)
    sql = ["SELECT * FROM items WHERE user_id = 3"]
    at = ex.explain_statements("sqlite", db, sql, n=6)
    assert [(f["table"], f["rows"]) for f in at["findings"]] == [("items", 6)]
    over = ex.explain_statements("sqlite", db, sql, n=6, min_rows=7)
    assert over["findings"] == [] and over["below_threshold"] == 1 and over["min_rows"] == 7


def test_unindexed_but_without_where_is_not_a_finding(tmp_path):
    db = _db_with_rows(tmp_path, 8, index=False)
    got = ex.explain_statements("sqlite", db, ["SELECT * FROM items"], n=1)
    assert got["findings"] == [] and got["status"] == "checked" and got["explained"] == 1


def test_a_join_without_an_index_is_found_through_the_alias(tmp_path):
    """SQLite builds an automatic index for a join key without an index: that is a finding
    on the table (its alias resolved), whatever the WHERE."""
    path = tmp_path / "j.db"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE a (id INTEGER PRIMARY KEY, k INT);"
                       "CREATE TABLE b (id INTEGER PRIMARY KEY, k INT);")  # fmt: skip
    conn.executemany("INSERT INTO a (k) VALUES (?)", [(i,) for i in range(9)])
    conn.executemany("INSERT INTO b (k) VALUES (?)", [(i,) for i in range(9)])
    conn.commit()
    conn.close()
    sql = "SELECT * FROM a x, b y WHERE x.k = y.k"
    got = ex.explain_statements("sqlite", path, [sql], n=9)
    assert {f["table"] for f in got["findings"]} == {"a", "b"}, got
    assert any(" AUTOMATIC " in f["access"] for f in got["findings"])


def test_unresolved_name_is_skipped_with_a_reason(tmp_path):
    db = _db_with_rows(tmp_path, 8, index=False)
    # a derived table the optimiser cannot flatten (GROUP BY): its plan row names ``s``
    hard = "SELECT * FROM (SELECT user_id, count(*) c FROM items GROUP BY user_id) s WHERE s.c > 0"
    got = ex.explain_statements("sqlite", db, [hard], n=1)
    assert got["status"] == "unchecked"  # its only statement was not explained
    assert got["skipped"][0]["reason"].startswith(ex.UNRESOLVED)
    assert got["explained"] == 0


def test_kinds_ignored_and_deduplicated(tmp_path):
    db = _db_with_rows(tmp_path, 3, index=False)
    stmts = ["BEGIN", "INSERT INTO items (user_id, name) VALUES (1, 'a')", "PRAGMA user_version",
             "SELECT * FROM items WHERE user_id = 1", "SELECT * FROM items WHERE user_id = 2",
             "COMMIT", "UPDATE items SET name = 'z' WHERE user_id = 1",
             "DELETE FROM items WHERE user_id = 9"]  # fmt: skip
    got = ex.explain_statements("sqlite", db, stmts, n=1)
    assert got["ignored"] == 4  # BEGIN, INSERT, PRAGMA, COMMIT
    assert got["explained"] == 3  # the two SELECTs are one shape
    assert sorted(f["sql"] for f in got["findings"]) == [
        "DELETE FROM items WHERE user_id = ?",
        "SELECT * FROM items WHERE user_id = ?",
        "UPDATE items SET name = ? WHERE user_id = ?",
    ]


def test_unexpanded_placeholders_still_explain(tmp_path):
    db = _db_with_rows(tmp_path, 5, index=False)
    got = ex.explain_statements("sqlite", db, ["SELECT * FROM items WHERE user_id = ?"], n=1)
    assert got["status"] == "checked" and [f["table"] for f in got["findings"]] == ["items"]


# ---- safety --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "multi"),
    [
        ("SELECT 1", False),
        ("SELECT 1;", False),
        ("SELECT 1 ;  ", False),
        ("SELECT 1; DELETE FROM items", True),
        ("SELECT ';' FROM t", False),
        ('SELECT ";" FROM "a;b"', False),
        ("SELECT `a;b` FROM t", False),
        ("SELECT 'it''s; fine' FROM t", False),
        ("SELECT 'a\\'; DELETE FROM t; --'", True),  # a PG literal ends at the backslash's quote
        ("SELECT 1; SELECT 2;", True),
    ],
)
def test_semicolon_guard(sql, multi):
    assert ex.multi_statement(sql) is multi


def test_multi_statement_is_skipped_not_run(tmp_path, monkeypatch):
    db = _db_with_rows(tmp_path, 4, index=False)
    monkeypatch.setattr(ex, "EXTRA_STATEMENTS", ["SELECT 1; DELETE FROM items"])
    got = ex.explain_statements("sqlite", db, ["SELECT * FROM items WHERE user_id = 1"], n=1)
    assert got["skipped"] == [{"sql": "SELECT ?; DELETE FROM items", "reason": ex.MULTI}]
    assert got["status"] == "partial" and got["explained"] == 1
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone() == (4,)
    conn.close()


def test_explain_connection_is_read_only(tmp_path):
    db = _db_with_rows(tmp_path, 2, index=False)
    conn = ex.open_explainer("sqlite", db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.conn.execute("INSERT INTO items (user_id, name) VALUES (1, 'x')")
        assert conn.count("items") == 2
    finally:
        conn.close()


class _Recorder:
    """A server connection double (``lattice.spec.db.Database``'s surface) that records the
    SQL it is sent and answers from ``answers`` (a plan for an EXPLAIN, a count)."""

    def __init__(self, plan: Any, counts: dict[str, int] | None = None, fail: str | None = None):
        self.sent: list[str] = []
        self.plan, self.counts, self.fail = plan, counts or {}, fail

    def quote_ident(self, name: str) -> str:
        return f'"{name}"'

    def execute(self, sql: str, params=()) -> None:
        self.sent.append(sql)

    def _plan(self, sql: str) -> Any:
        if self.fail:
            raise RuntimeError(self.fail)
        return self.plan

    def query(self, sql: str, params=()):
        self.sent.append(sql)
        return [(self._plan(sql),)]

    def query_dicts(self, sql: str, params=()):
        self.sent.append(sql)
        return self._plan(sql)

    def scalar(self, sql: str, params=()):
        self.sent.append(sql)
        table = sql.split("FROM", 1)[1].strip().strip('"')
        if table not in self.counts:
            raise RuntimeError(f'relation "{table}" does not exist')
        return self.counts[table]

    def close(self) -> None:
        pass


# Postgres: EXPLAIN (GENERIC_PLAN, FORMAT JSON) in its documented shape
PG_PK_FILTER = [{"Plan": {"Node Type": "Index Scan", "Scan Direction": "Forward",
                          "Index Name": "items_pkey", "Relation Name": "items", "Alias": "items",
                          "Filter": "(user_id = $1)", "Plan Rows": 1}}]  # fmt: skip
PG_SEQ_FILTER = [{"Plan": {"Node Type": "Seq Scan", "Relation Name": "items", "Alias": "items",
                           "Filter": "(user_id = $1)"}}]  # fmt: skip
PG_SERVED = [{"Plan": {"Node Type": "Index Scan", "Index Name": "items_user_id",
                       "Relation Name": "items", "Alias": "items",
                       "Index Cond": "(user_id = $1)"}}]  # fmt: skip
PG_SERVED_FILTER = [{"Plan": {"Node Type": "Index Scan", "Index Name": "items_user_id",
                              "Relation Name": "items", "Index Cond": "(user_id = $1)",
                              "Filter": "(name = $2)"}}]  # fmt: skip
PG_NESTED = [{"Plan": {"Node Type": "Nested Loop", "Plans": [
    {"Node Type": "Index Scan", "Relation Name": "users", "Index Cond": "(token = $1)"},
    {"Node Type": "Seq Scan", "Relation Name": "notes", "Alias": "n",
     "Filter": "(item_id = $2)"}]}}]  # fmt: skip
PG_NO_FILTER = [{"Plan": {"Node Type": "Seq Scan", "Relation Name": "items"}}]
PG_UPDATE = [{"Plan": {"Node Type": "ModifyTable", "Operation": "Update", "Relation Name": "items",
                       "Plans": [{"Node Type": "Seq Scan", "Relation Name": "items",
                                  "Filter": "(name = $1)"}]}}]  # fmt: skip


def test_pg_full_index_scan_filter():
    """A primary-key scan with a Filter and no Index Cond is a full read (with
    enable_seqscan = off the planner picks it for an ORDER BY id): a finding, as is a Seq
    Scan with a Filter; a filter on top of an Index Cond is not."""
    assert ex.pg_candidates(PG_PK_FILTER) == [
        ("items", "Index Scan on items, Filter: (user_id = $1)")
    ]
    assert ex.pg_candidates(PG_SEQ_FILTER) == [
        ("items", "Seq Scan on items, Filter: (user_id = $1)")
    ]
    assert ex.pg_candidates(PG_SERVED) == []
    assert ex.pg_candidates(PG_SERVED_FILTER) == []
    assert ex.pg_candidates(PG_NO_FILTER) == []
    assert ex.pg_candidates(PG_NESTED) == [("notes", "Seq Scan on notes, Filter: (item_id = $2)")]
    assert ex.pg_candidates(PG_UPDATE) == [("items", "Seq Scan on items, Filter: (name = $1)")]
    assert ex.pg_candidates(json.dumps(PG_PK_FILTER)) == ex.pg_candidates(PG_PK_FILTER)


def test_pg_explain_session_statements(monkeypatch):
    """The Postgres session: read only, a timeout and enable_seqscan = off before any
    EXPLAIN (GENERIC_PLAN, FORMAT JSON), sent with no parameters; then counts."""
    rec = _Recorder(PG_PK_FILTER, {"items": 6})
    monkeypatch.setattr("lattice.spec.db.open_db", lambda url: rec)
    got = ex.explain_statements("postgres", "postgresql://x/db", [USER_ITEMS.replace("?", "$1")],
                                n=6)  # fmt: skip
    assert rec.sent == [
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
        "SET statement_timeout = '5s'",
        "SET enable_seqscan = off",
        "EXPLAIN (GENERIC_PLAN, FORMAT JSON) "
        "SELECT id, user_id, name FROM items WHERE user_id = $1 ORDER BY id",
        'SELECT COUNT(*) FROM "items"',
    ]
    assert [(f["table"], f["rows"]) for f in got["findings"]] == [("items", 6)]
    assert got["status"] == "checked" and got["engine"] == "postgres"


def test_everything_sent_is_explain_set_or_count(monkeypatch):
    rec = _Recorder([{"id": 1, "table": "items", "type": "ALL", "key": None,
                      "Extra": "Using where"}], {"items": 9})  # fmt: skip
    monkeypatch.setattr("lattice.spec.db.open_db", lambda url: rec)
    stmts = ["SELECT * FROM items WHERE a = 1", "DELETE FROM items WHERE a = 2"]
    ex.explain_statements("mysql", "mysql://x/db", stmts, n=1)
    assert rec.sent[:2] == ["SET SESSION TRANSACTION READ ONLY",
                            "SET SESSION max_seeks_for_key = 1"]  # fmt: skip
    assert rec.sent[2].startswith("EXPLAIN FORMAT=TRADITIONAL SELECT")
    for sql in rec.sent:
        assert sql.startswith(("EXPLAIN ", "SET ", "SELECT COUNT(*) FROM "))


def test_mysql_plan_rows_classified():
    """type ALL or index with Using where / Using join buffer; not without them, not a
    served lookup, not a derived table."""

    def row(**kw: Any) -> dict:
        return {"id": 1, "select_type": "SIMPLE", "table": "items", "type": "ALL", "key": None,
                "Extra": "Using where", **kw}  # fmt: skip

    assert ex.mysql_candidates([row()]) == [("items", "type=ALL key=NULL Extra=Using where")]
    assert ex.mysql_candidates(
        [row(type="index", key="PRIMARY", Extra="Using where; Using index")]
    ) == [("items", "type=index key=PRIMARY Extra=Using where; Using index")]
    assert ex.mysql_candidates([row(Extra="Using join buffer (hash join)")])
    assert ex.mysql_candidates([row(Extra=None)]) == []  # a bare ALL without a predicate
    assert ex.mysql_candidates([row(Extra="Using filesort")]) == []
    assert ex.mysql_candidates([row(type="ref", key="items_user_id", Extra=None)]) == []
    assert ex.mysql_candidates([row(type="range", Extra="Using index condition")]) == []
    assert ex.mysql_candidates([row(table="<derived2>")]) == []
    assert ex.mysql_candidates([row(table="<subquery3>")]) == []
    assert (
        ex.mysql_candidates([row(table=None, type=None, Extra="no matching row in const table")])
        == []
    )


def test_mysql_alias_resolution(monkeypatch):
    rec = _Recorder([{"id": 1, "table": "i", "type": "ALL", "key": None, "Extra": "Using where"}],
                    {"items": 7})  # fmt: skip
    monkeypatch.setattr("lattice.spec.db.open_db", lambda url: rec)
    got = ex.explain_statements("mysql", "mysql://x/db",
                                ["SELECT * FROM items AS i WHERE i.a = 1"], n=7)  # fmt: skip
    assert [(f["table"], f["rows"]) for f in got["findings"]] == [("items", 7)]


def test_explain_error_is_a_skip_with_the_first_line(monkeypatch):
    rec = _Recorder(None, {}, fail="syntax error near $1\nLINE 1: second line")
    monkeypatch.setattr("lattice.spec.db.open_db", lambda url: rec)
    got = ex.explain_statements("postgres", "postgresql://x/db", ["SELECT $1"], n=1)
    assert got["skipped"] == [
        {"sql": "SELECT $?", "reason": "EXPLAIN failed: syntax error near $1"}
    ]
    assert got["status"] == "unchecked" and got["explained"] == 0


@pytest.mark.parametrize(
    ("sql", "want"),
    [
        ("SELECT * FROM items i WHERE i.id = 1", {"i": "items"}),
        ("SELECT * FROM items AS i JOIN notes n ON n.item_id = i.id", {"i": "items", "n": "notes"}),
        ("SELECT * FROM items x, notes y WHERE x.id = y.item_id", {"x": "items", "y": "notes"}),
        ("UPDATE items AS t SET name = 'a' WHERE t.id = 1", {"t": "items"}),
        ("DELETE FROM items WHERE id = 1", {}),
        ("SELECT * FROM items WHERE id = 1 ORDER BY id", {}),
        ('SELECT * FROM "items" "i" WHERE i.id = 1', {"i": "items"}),
        ("SELECT * FROM main.items i WHERE i.id = 1", {"i": "items"}),
        ("SELECT * FROM (SELECT 1) s", {}),
    ],
)
def test_alias_map(sql, want):
    assert ex.alias_map(sql) == want


@pytest.mark.parametrize(
    ("sql", "kind"),
    [("select 1", "SELECT"), ("  (SELECT 1)", "SELECT"), ("/* c */ UPDATE t SET a=1", "UPDATE"),
     ("-- c\nDELETE FROM t", "DELETE"), ("WITH x AS (SELECT 1) SELECT * FROM x", "WITH"),
     ("INSERT INTO t VALUES (1)", "INSERT"), ("", "")],
)  # fmt: skip
def test_first_keyword(sql, kind):
    assert ex.first_keyword(sql) == kind


# ---- the run: placement, counts, report ------------------------------------------------------


def test_explain_runs_at_largest_size():
    (r, _) = measure(_file(), _harness(indexapp.drop_items), runs=1, explain=_opts())
    assert r.explain["size"] == 6 and [f["table"] for f in r.explain["findings"]] == ["items"]
    assert [s.explain is not None for s in r.sizes] == [False, True]
    # with --sizes the largest of those
    (r2, _) = measure(_file(), _harness(indexapp.drop_items), runs=1, sizes=[3, 4], explain=_opts())
    assert r2.explain["size"] == 4 and [f["rows"] for f in r2.explain["findings"]] == [4]


def test_counts_identical_with_explain(tmp_path, monkeypatch):
    """The statements counted are the same with and without the check, and the check runs
    after the last counting window and the app's close, once, before the clone is dropped."""
    plain = measure(_file(), _harness(indexapp.drop_items), runs=3)
    with_explain = measure(_file(), _harness(indexapp.drop_items), runs=3, explain=_opts())
    for a, b in zip(plain, with_explain, strict=True):
        assert [s.statements for s in a.sizes] == [s.statements for s in b.sizes]
        assert a.verdict == b.verdict and a.explain is None and b.explain is not None

    events: list[str] = []

    class Spy(SqliteCounter):
        def begin(self) -> None:
            events.append("begin")
            super().begin()

        def end(self) -> list[str]:
            events.append("end")
            return super().end()

    real = ex.explain_statements

    def spy_explain(*a: Any, **k: Any) -> dict:
        events.append("explain")
        return real(*a, **k)

    rt = AppRuntime(dispose=(lambda app: events.append("close"),))
    h = _harness(indexapp.drop_items, rt)
    from lattice.spec.check import as_db_factory

    monkeypatch.setattr(ex, "explain_statements", spy_explain)
    counter = Spy()
    counter.start()
    try:
        b = _file().budgets[0]
        res = measure_size(b, 4, h, as_db_factory(h.db_factory), counter, tmp_path, 3, False,
                           _opts())  # fmt: skip
    finally:
        counter.stop()
    assert events == ["begin", "end"] * 3 + ["close", "explain"]
    assert res.statements == [2, 2, 2] and res.explain["findings"]
    assert "raw_statements" not in res.to_dict() and "explain" not in res.to_dict()


def test_explain_refuses_a_database_the_run_did_not_make(tmp_path):
    other = tmp_path / "elsewhere.db"
    indexapp.build_db(other)
    h = Harness(indexapp.make_app, lambda variant, path: other, None, indexapp.login, "sqlite")
    from lattice.perf.measure import MeasureError

    with pytest.raises(MeasureError, match="not in the run's temporary directory"):
        measure(_file(), h, runs=1, explain=_opts())


def test_report_without_explain_has_no_explain_key():
    (r, _) = measure(_file(), _harness(), runs=1)
    assert "explain" not in r.to_dict() and r.explain is None
    assert "explain" not in render([r])


def test_report_json_and_text_with_explain():
    reports = measure(_file(), _harness(indexapp.drop_items), runs=1, explain=_opts())
    d = reports[0].to_dict()
    assert list(d)[-1] == "explain"
    assert list(d["explain"]) == [
        "size",
        "engine",
        "status",
        "reason",
        "min_rows",
        "explained",
        "ignored",
        "below_threshold",
        "skipped",
        "tables",
        "findings",
    ]
    assert list(d["explain"]["findings"][0]) == ["table", "rows", "access", "sql"]
    text = render(reports)
    assert "  explain at N=6 (sqlite): 2 statement(s) explained, 0 skipped, 1 finding(s)" in text
    assert ("  - missing index: items (6 rows) is read by a full scan [SCAN items] in: "
            f"{USER_ITEMS}") in text  # fmt: skip
    assert text.rstrip().endswith(
        "explain: findings (1 finding(s) in 1 budget(s), 0 unchecked or partial)"
    )
    assert text.index("perf: pass") < text.index("explain: findings")


# ---- unsupported engines and unmeasured budgets -----------------------------------------------


def test_unsupported_engine_is_unchecked_and_exit_2():
    from tests.perf import fakeengines as fe
    from tests.perf import serverapp

    seed = {"items": "N", "steps": [{"method": "POST", "path": "/items", "user": "ann",
                                     "count": "N", "json": {"name": "item-{i}"}}]}  # fmt: skip
    budgets = parse({"version": 1, "budgets": [{
        "id": "items", "endpoint": "GET /items", "user": "ann", "seed": seed, "sizes": [3, 7],
        "queries": {"constant": 2, "per_item": 0}, "latency_ms": None}]})  # fmt: skip
    with fe.installed():
        h = Harness(serverapp.make_app, serverapp.build_db, None, serverapp.login, "clickhouse")
        (r,) = measure(budgets, h, runs=1, explain=_opts())
    assert r.verdict == "pass"  # counted
    assert r.explain["status"] == "unchecked"
    assert r.explain["reason"] == "explain is not supported on ClickHouse"
    assert r.explain["size"] == 7 and r.explain["findings"] == []
    assert check_exit([r], explain=True) == 2 and check_exit([r]) == 0
    assert "  explain: unchecked: explain is not supported on ClickHouse" in render([r])


def test_process_app_on_sqlite_is_unmeasured_and_unchecked():
    from lattice.spec.process import process_runtime

    h = _harness(runtime=process_runtime())
    (r, _) = measure(_file(), h, runs=1, explain=_opts())
    assert r.verdict == "unmeasured" and r.sizes == []
    assert r.explain["status"] == "unchecked" and r.explain["reason"] == "the budget is unmeasured"
    assert check_exit([r], explain=True) == 2


# ---- the CLI ----------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_sys_path():
    saved = list(sys.path)
    yield
    sys.path[:] = saved


def _project(tmp_path: Path, factory: str = "build_db", runtime: str | None = None,
             budgets: list[dict] | None = None) -> Path:  # fmt: skip
    mod = f"indexapp_{uuid.uuid4().hex[:8]}"
    d = tmp_path / "proj"
    d.mkdir(parents=True)
    shutil.copy(HERE / "indexapp.py", d / f"{mod}.py")
    toml = f'[app]\napp = "{mod}:make_app"\ndb = "{mod}:{factory}"\nlogin = "{mod}:login"\n'
    if runtime:
        toml += f'runtime = "{runtime}"\n'
    (d / "studio.toml").write_text(toml)
    (d / "perf.budget.json").write_text(
        json.dumps({"version": 1, "budgets": budgets or _budgets()})
    )
    return d


def test_cli_explain_exit_codes(tmp_path, capsys, monkeypatch):
    clean = _project(tmp_path / "a")
    assert perf_main(["check", "--project", str(clean), "--runs", "1", "--explain"]) == 0
    out = capsys.readouterr().out
    assert out.rstrip().endswith(
        "explain: clean (0 finding(s) in 0 budget(s), 0 unchecked or partial)"
    )
    assert perf_main(["check", "--project", str(clean), "--runs", "1"]) == 0  # no flag: count only
    assert "explain" not in capsys.readouterr().out

    bad = _project(tmp_path / "b", "drop_items")
    assert perf_main(["check", "--project", str(bad), "--runs", "1"]) == 0  # counts cannot see it
    capsys.readouterr()
    assert perf_main(["check", "--project", str(bad), "--runs", "1", "--explain"]) == 1
    assert "missing index: items (6 rows)" in capsys.readouterr().out
    assert perf_main(["measure", "--project", str(bad), "--runs", "1", "--explain"]) == 0
    capsys.readouterr()

    # a statement that cannot be explained makes the budget partial: nothing passes unchecked
    monkeypatch.setattr(ex, "EXTRA_STATEMENTS", ["SELECT 1; DELETE FROM items"])
    assert perf_main(["check", "--project", str(clean), "--runs", "1", "--explain"]) == 2
    out = capsys.readouterr().out
    assert "not explained: more than one statement" in out
    assert "explain: unchecked (0 finding(s) in 0 budget(s), 2 unchecked or partial)" in out
    monkeypatch.setattr(ex, "EXTRA_STATEMENTS", [])

    proc = _project(tmp_path / "c", runtime="process")
    assert perf_main(["check", "--project", str(proc), "--runs", "1", "--explain"]) == 2
    capsys.readouterr()
    # a budget over its count still exits 1, a finding too; unmeasured plus a finding is 1
    tight = _budgets()
    tight[0]["queries"] = {"constant": 1, "per_item": 0}
    over = _project(tmp_path / "d", budgets=tight)
    assert perf_main(["check", "--project", str(over), "--runs", "1", "--explain"]) == 1


def test_cli_explain_json_and_flags(tmp_path, capsys):
    d = _project(tmp_path, "drop_notes")
    assert perf_main(["check", "--project", str(d), "--runs", "1", "--explain", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert list(data)[-1] == "explain"
    assert data["explain"] == {"verdict": "findings", "findings": 1, "budgets": 1, "unchecked": 0}
    by = {r["budget_id"]: r for r in data["reports"]}
    assert by["notes"]["explain"]["findings"][0]["table"] == "notes"
    assert by["items"]["explain"]["findings"] == []
    assert perf_main(["check", "--project", str(d), "--runs", "1", "--explain", "--json",
                      "--explain-min-rows", "0"]) == 1  # fmt: skip
    data = json.loads(capsys.readouterr().out)
    assert data["explain"]["findings"] == 3  # items: users; notes: users and notes
    assert perf_main(["check", "--project", str(d), "--explain-min-rows", "3"]) == 2
    assert "--explain-min-rows needs --explain" in capsys.readouterr().err
    assert perf_main(["check", "--project", str(d), "--explain", "--explain-min-rows", "-1"]) == 2
