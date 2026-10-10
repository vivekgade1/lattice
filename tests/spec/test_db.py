"""The database interface (lattice.spec.db) and its servers (lattice.spec.dbserver): URLs
and their driver forms, each adapter's helpers, clones, ``expect_db`` comparisons and the
invariants' SQL per dialect. The server tests run on Docker containers (skipped without
Docker)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from lattice.profile import ApiProfile, Profile, RoleColumn
from lattice.spec import Invariant, Spec
from lattice.spec.check import CheckError, _expect_db, _insert_given, _same
from lattice.spec.db import (
    DatabaseError,
    SqliteDatabase,
    as_ref,
    canonical_url,
    database_name,
    dialect_of,
    open_db,
    redact,
    url_for,
)
from lattice.spec.dbserver import DbServer, ServerDatabases
from lattice.spec.invariants import compile_invariant
from lattice.typesys import Scalar, Schema

S = Scalar

# ---- URLs (no server) ------------------------------------------------------------------------

PG = canonical_url("postgres", "127.0.0.1", 5433, "u", "p@ss", "db1")
MY = canonical_url("mysql", "127.0.0.1", 3307, "root", "pw", "db1")
CH = canonical_url("clickhouse", "127.0.0.1", 8124, "u", "pw", "db1")


def test_canonical_urls_and_their_dialects():
    assert PG == "postgresql://u:p%40ss@127.0.0.1:5433/db1?sslmode=disable"
    assert MY == "mysql://root:pw@127.0.0.1:3307/db1"
    assert CH == "clickhouse://u:pw@127.0.0.1:8124/db1"
    assert [dialect_of(u) for u in (PG, MY, CH)] == ["postgres", "mysql", "clickhouse"]
    assert dialect_of(Path("/tmp/x.db")) == "sqlite"
    assert dialect_of("/tmp/x.db") == "sqlite"
    assert as_ref(PG) == PG and as_ref("/tmp/x.db") == Path("/tmp/x.db")
    assert redact(PG) == "postgresql://u:***@127.0.0.1:5433/db1?sslmode=disable"


def test_url_for_translates_for_each_driver():
    assert url_for(MY, "go-mysql") == "root:pw@tcp(127.0.0.1:3307)/db1?parseTime=true"
    assert url_for(MY, "sqlalchemy") == "mysql+pymysql://root:pw@127.0.0.1:3307/db1"
    assert url_for(PG, "go-pgx") == PG
    assert url_for(PG, "sqlalchemy").startswith("postgresql+psycopg://u:p%40ss@")
    assert url_for(PG, "jdbc").startswith("jdbc:postgresql://127.0.0.1:5433/db1?user=u")
    assert url_for(CH, "go-clickhouse") == "http://u:pw@127.0.0.1:8124/db1"
    assert url_for(Path("/tmp/a.db"), "sqlalchemy") == f"sqlite:///{Path('/tmp/a.db').resolve()}"
    with pytest.raises(ValueError):
        url_for(MY, "no-such-driver")


def test_the_sqlite_adapter_reads_as_the_checker_always_did(tmp_path):
    db = tmp_path / "a.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, flag INTEGER, s TEXT)")
    with open_db(db) as a:
        assert isinstance(a, SqliteDatabase) and a.url == str(db)
        a.insert("t", [{"id": 2, "flag": 1, "s": "b"}, {"id": 1, "flag": 0, "s": None}])
        assert a.tables() == ["t"]
        assert a.columns("t") == ["id", "flag", "s"]
        # rowid order (insertion order of a rowid table), whatever order_by says
        assert [r["id"] for r in a.read_table("t", "id")] == [1, 2]
        assert a.query("SELECT s FROM t WHERE flag = ?", [1]) == [("b",)]
        with pytest.raises(sqlite3.Error):  # unwrapped, as before
            a.query("SELECT nope FROM t")


# ---- a spec over the flags table -----------------------------------------------------------

FLAG = Schema.from_pairs(
    [("id", S.INT), ("owner", S.INT), ("active", S.BOOL), ("note", S.STR),
     ("amount", S.FLOAT), ("label", S.STR), ("state", S.STR)],
    keys=[("id",)],
    nullable=["note"],
    one_of={"state": ("open", "closed")},
    one_of_stored={"state": ("OPEN", "CLOSED")},
)  # fmt: skip
USER = Schema.from_pairs([("id", S.INT), ("name", S.STR), ("token", S.STR)], keys=[("id",)])
PROFILE = Profile(
    schemas={},
    api=ApiProfile(
        tables={"User": USER, "Flag": FLAG},
        table_names={"User": "users", "Flag": "flags"},
        roles=frozenset({"Public", "Member"}),
        users_table="User",
        roles_from=RoleColumn("name"),
    ),
)
SPEC = Spec(profile=PROFILE)

DDL = {
    "sqlite": [
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, token TEXT)",
        "CREATE TABLE flags (id INTEGER PRIMARY KEY, owner INTEGER, active BOOLEAN, note TEXT, "
        "amount REAL, label TEXT, state TEXT)",
    ],
    "postgres": [
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, token TEXT)",
        "CREATE TABLE flags (id INTEGER PRIMARY KEY, owner INTEGER, active BOOLEAN NOT NULL, "
        "note TEXT NULL, amount NUMERIC(10, 2), label TEXT, state TEXT)",
    ],
    "mysql": [
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(20), token VARCHAR(20))",
        "CREATE TABLE flags (id INTEGER PRIMARY KEY, owner INTEGER, active BOOLEAN NOT NULL, "
        "note TEXT NULL, amount DECIMAL(10, 2), label VARCHAR(20), state VARCHAR(10))",
    ],
    "clickhouse": [
        "CREATE TABLE users (id Int64, name String, token String) ENGINE = MergeTree ORDER BY id",
        "CREATE TABLE flags (id Int64, owner Int64, active Bool, note Nullable(String), "
        "amount Decimal(10, 2), label String, state String) ENGINE = MergeTree ORDER BY id",
    ],
}
ROWS = [
    {"id": 1, "owner": 7, "active": True, "note": None, "amount": 1.5, "label": "5",
     "state": "OPEN"},
    {"id": 2, "owner": 7, "active": False, "note": "x", "amount": 2, "label": "two",
     "state": "OPEN"},
    {"id": 3, "owner": 8, "active": True, "note": "it's \\ here", "amount": -1, "label": "c",
     "state": "CLOSED"},
]  # fmt: skip


def build(db_ref, dialect: str) -> None:
    with open_db(db_ref) as db:
        for ddl in DDL[dialect]:
            db.execute(ddl)
        db.insert("users", [{"id": 1, "name": "ann", "token": "t1"}])
        db.insert("flags", ROWS)


@pytest.fixture
def sqlite_db(tmp_path):
    path = tmp_path / "flags.db"
    build(path, "sqlite")
    return path


def _expectations(db_ref) -> None:
    """The ``expect_db`` comparisons every dialect must get right."""

    def ok(expect) -> bool:
        return _expect_db(SPEC, db_ref, expect) == []

    # booleans in where and equals
    assert ok({"Flag": {"where": {"active": True}, "count": 2}})
    assert ok({"Flag": {"where": {"active": False}, "count": 1, "equals": {"id": 2}}})
    assert ok({"Flag": {"where": {"id": 1}, "equals": {"active": True}}})
    assert not ok({"Flag": {"where": {"id": 1}, "equals": {"active": False}}})
    # NULL: where None is IS NULL; equals None only matches a NULL
    assert ok({"Flag": {"where": {"note": None}, "count": 1, "equals": {"id": 1}}})
    assert ok({"Flag": {"where": {"id": 1}, "equals": {"note": None}}})
    assert not ok({"Flag": {"where": {"id": 2}, "equals": {"note": None}}})
    # numbers: 1.5 == 1.5, 2 == 2.0; a number is not its text, nor text a number
    assert ok({"Flag": {"where": {"id": 1}, "equals": {"amount": 1.5}}})
    assert ok({"Flag": {"where": {"id": 2}, "equals": {"amount": 2}}})
    assert not ok({"Flag": {"where": {"id": 1}, "equals": {"amount": "1.5"}}})
    assert not ok({"Flag": {"where": {"id": 1}, "equals": {"label": 5}}})
    assert ok({"Flag": {"where": {"id": 1}, "equals": {"label": "5"}}})
    # one-of values in their stored spellings; text with a quote and a backslash
    assert ok({"Flag": {"where": {"state": "open"}, "count": 2}})
    assert ok({"Flag": {"where": {"note": "it's \\ here"}, "count": 1}})
    problems = _expect_db(SPEC, db_ref, {"Flag": {"where": {"owner": 7}, "count": 3}})
    assert problems == ["db Flag where {'owner': 7}: expected 3 rows, found 2"]


INVARIANTS = {
    # count bound with a tuple-free by= and a where= over a Bool column
    "one_active_per_owner": ('count(Flag, by="owner", where=Flag.active) <= 1', set()),
    "at_most_one_open": ('count(Flag, by="state", where=Flag.state == "open") <= 1', {"OPEN"}),
    "non_negative": ("Flag.amount >= 0", {3}),
    "active_or_noted": ("Flag.active or not is_null(Flag.note)", set()),
    "inactive_ones_noted": ('Flag.active == True or Flag.note in ("x", "y")', set()),
    "no_backslash_note": ("is_null(Flag.note) or Flag.note != \"it's \\\\ here\"", {3}),
    "user_balance": ('User.id == sum(Flag.amount, by="owner")', {1}),
}


def _invariants(db_ref, dialect: str) -> None:
    with open_db(db_ref) as db:
        for name, (expr, want) in INVARIANTS.items():
            sql = compile_invariant(Invariant(name, name, expr), PROFILE, dialect)
            rows = db.query_dicts(sql)
            if name == "at_most_one_open":
                got = {r["state"] for r in rows}
                assert [r["count"] for r in rows] == [2], (dialect, sql, rows)
            else:
                got = {r["id"] for r in rows}
            assert got == want, (dialect, name, sql, rows)
        # a second active row for owner 7 breaks the count bound
        if dialect == "clickhouse":  # a mutation: synchronous, or the read may not see it
            db.execute("ALTER TABLE flags UPDATE active = true WHERE id = 2 "
                       "SETTINGS mutations_sync = 1")  # fmt: skip
        else:
            q = db.quote_ident
            db.execute(f"UPDATE {q('flags')} SET {q('active')} = {db.ph} WHERE {q('id')} = "
                       f"{db.ph}", [True, 2])  # fmt: skip
        expr, _ = INVARIANTS["one_active_per_owner"]
        sql = compile_invariant(Invariant("x", "x", expr), PROFILE, dialect)
        rows = db.query_dicts(sql)
        assert [(r["owner"], r["count"]) for r in rows] == [(7, 2)], (dialect, rows)


def test_sqlite_expect_db_and_invariants(sqlite_db):
    _expectations(sqlite_db)
    _invariants(sqlite_db, "sqlite")


def test_the_sqlite_invariant_sql_is_unchanged():
    inv = Invariant("x", "x", 'count(Flag, by="owner", where=Flag.active) <= 1')
    assert compile_invariant(inv, PROFILE) == compile_invariant(inv, PROFILE, "sqlite")
    assert compile_invariant(inv, PROFILE) == (
        'SELECT t."owner" AS "owner", COUNT(*) AS "count" FROM "flags" AS t WHERE '
        't."owner" IS NOT NULL AND ((t."active" = 1)) GROUP BY t."owner" HAVING NOT '
        "(COUNT(*) <= 1)"
    )
    row = compile_invariant(Invariant("y", "y", "Flag.active == False"), PROFILE, "postgres")
    assert row == 'SELECT t.* FROM "flags" AS t WHERE NOT COALESCE((t."active" = FALSE), FALSE)'
    mysql = compile_invariant(Invariant("z", "z", 'Flag.label != "a\\\\b"'), PROFILE, "mysql")
    assert mysql == (
        "SELECT t.* FROM `flags` AS t WHERE NOT COALESCE((t.`label` <> 'a\\\\b'), FALSE)"
    )


# ---- the server adapters -------------------------------------------------------------------


def _fresh(server: DbServer, build_it=True) -> ServerDatabases:
    def factory(url: str) -> None:
        if build_it:
            build(url, server.dialect)

    return ServerDatabases(server, factory)


def test_each_adapter_has_the_crud_helpers(any_server):
    dbs = _fresh(any_server)
    try:
        url = dbs("seed")
        with open_db(url) as db:
            assert db.dialect == any_server.dialect and db.url == url
            assert db.tables() == ["flags", "users"]
            assert db.columns("flags")[:3] == ["id", "owner", "active"]
            rows = db.read_table("flags", "id")
            assert [r["id"] for r in rows] == [1, 2, 3]
            assert bool(rows[0]["active"]) is True and rows[0]["note"] is None
            db.insert("users", [{"id": 2, "name": "ben", "token": "t2"},
                               {"id": 3, "name": "cy", "token": "t3"}])  # fmt: skip
            q = db.quote_ident
            assert db.query(f"SELECT {q('name')} FROM {q('users')} WHERE {q('id')} = {db.ph}",
                            [2]) == [("ben",)]  # fmt: skip
            assert db.scalar(f"SELECT COUNT(*) FROM {q('users')}") == 3
            with pytest.raises(DatabaseError):
                db.query("SELECT no_such_column FROM users")
    finally:
        dbs.close()


def test_clones_are_isolated(any_server):
    dbs = _fresh(any_server)
    try:
        a, b = dbs("seed"), dbs("seed")
        assert a != b
        with open_db(a) as db:
            db.insert("users", [{"id": 9, "name": "only-in-a", "token": "a"}])
        with open_db(b) as db:
            assert db.scalar("SELECT COUNT(*) FROM users") == 1
            db.insert("users", [{"id": 10, "name": "only-in-b", "token": "b"}])
        with open_db(a) as db:
            assert sorted(r[0] for r in db.query("SELECT id FROM users")) == [1, 9]
        template = any_server.url_of(dbs.templates["seed"])
        with open_db(template) as db:
            assert db.scalar("SELECT COUNT(*) FROM users") == 1
        c = dbs("seed")  # a clone after the others' writes is the seed again
        with open_db(c) as db:
            assert db.scalar("SELECT COUNT(*) FROM users") == 1
        dbs.release(a)
        assert database_name(a) not in dbs.live
    finally:
        dbs.close()
    # every database of the run is dropped
    with open_db(any_server.url) as admin:
        if any_server.dialect == "postgres":
            left = admin.query("SELECT datname FROM pg_database WHERE datname LIKE %s",
                               [dbs.run + "%"])  # fmt: skip
        elif any_server.dialect == "mysql":
            left = admin.query("SELECT schema_name FROM information_schema.schemata WHERE "
                               "schema_name LIKE %s", [dbs.run + "%"])  # fmt: skip
        else:
            left = admin.query("SELECT name FROM system.databases WHERE name LIKE %s",
                               [dbs.run + "%"])  # fmt: skip
    assert left == []


def test_expect_db_comparisons_on_each_server(any_server):
    dbs = _fresh(any_server)
    try:
        url = dbs("seed")
        _expectations(url)
        # given rows go in through the adapter (a one-of value in its stored spelling)
        given = {"id": 4, "owner": 9, "active": False, "note": None, "amount": 0, "label": "g",
                 "state": "closed"}  # fmt: skip
        _insert_given(SPEC, url, {"Flag": [given]})
        assert _expect_db(SPEC, url, {"Flag": {"where": {"id": 4},
                                               "equals": {"state": "closed", "active": False,
                                                          "note": None}}}) == []  # fmt: skip
        with pytest.raises(CheckError):
            _insert_given(SPEC, url, {"Nope": [{"id": 1}]})
    finally:
        dbs.close()


def test_invariants_compile_and_run_on_each_server(any_server):
    dbs = _fresh(any_server)
    try:
        _invariants(dbs("seed"), any_server.dialect)
    finally:
        dbs.close()


def test_same_compares_driver_values_as_sqlite_values():
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import UUID

    assert _same(1.5, Decimal("1.50")) and _same(2, Decimal("2.00"))
    assert not _same("2", Decimal("2"))
    assert _same(True, 1) and not _same(True, 0)  # MySQL answers a BOOLEAN as 0/1
    u = "0f8fad5b-d9cb-469f-a165-70867728950e"
    assert _same(u, UUID(u))
    assert _same("2026-06-01T12:00:00Z", datetime(2026, 6, 1, 12, tzinfo=UTC))
    assert _same("2026-06-01T12:00:00Z", datetime(2026, 6, 1, 12))  # naive is UTC
    assert not _same("2026-06-01T12:00:01Z", datetime(2026, 6, 1, 12))


def test_mysql_clones_keep_indexes_generated_columns_keys_views_and_triggers(my):
    def factory(url: str) -> None:
        with open_db(url) as db:
            db.execute("CREATE TABLE parents (id INT PRIMARY KEY)")
            db.execute(
                "CREATE TABLE kids (id INT AUTO_INCREMENT PRIMARY KEY, parent_id INT NOT NULL, "
                "price DECIMAL(10,2), doubled DECIMAL(12,2) AS (price * 2) STORED, "
                "tag VARCHAR(10), UNIQUE KEY one_tag (tag), "
                "FOREIGN KEY (parent_id) REFERENCES parents(id))"
            )
            db.execute("CREATE TABLE audit (n INT)")
            db.execute("CREATE TRIGGER kid_audit AFTER INSERT ON kids FOR EACH ROW "
                       "INSERT INTO audit VALUES (NEW.id)")  # fmt: skip
            db.execute("CREATE VIEW kid_prices AS SELECT id, doubled FROM kids")
            db.insert("parents", [{"id": 1}])
            db.insert("kids", [{"parent_id": 1, "price": 2.5, "tag": "a"}])

    dbs = ServerDatabases(my, factory)
    try:
        url = dbs("seed")
        with open_db(url) as db:
            assert db.query("SELECT doubled FROM kids")[0][0] == 5
            # the trigger's row came along with the data, and the trigger itself works
            assert db.query("SELECT n FROM audit") == [(1,)]
            db.insert("kids", [{"parent_id": 1, "price": 1, "tag": "b"}])
            assert sorted(r[0] for r in db.query("SELECT n FROM audit")) == [1, 2]
            assert db.query("SELECT id FROM kids WHERE tag = 'b'") == [(2,)]  # AUTO_INCREMENT
            assert db.query("SELECT doubled FROM kid_prices WHERE id = 2")[0][0] == 2
            with pytest.raises(DatabaseError, match="Duplicate"):  # the unique key
                db.insert("kids", [{"parent_id": 1, "price": 1, "tag": "a"}])
            with pytest.raises(DatabaseError, match="foreign key"):  # the foreign key
                db.insert("kids", [{"parent_id": 99, "price": 1, "tag": "z"}])
            ddl = db.query("SHOW CREATE TABLE kids")[0][1]
            assert "GENERATED ALWAYS" in ddl and "FOREIGN KEY" in ddl
            view = db.query("SHOW CREATE VIEW kid_prices")[0][1]
            assert dbs.templates["seed"] not in view  # re-pointed at the clone
    finally:
        dbs.close()


def test_clickhouse_refuses_to_clone_a_view(ch):
    def factory(url: str) -> None:
        with open_db(url) as db:
            db.execute("CREATE TABLE t (id Int64) ENGINE = MergeTree ORDER BY id")
            db.execute("CREATE VIEW v AS SELECT id FROM t")

    dbs = ServerDatabases(ch, factory)
    try:
        with pytest.raises(Exception, match="views"):
            dbs("seed")
    finally:
        dbs.close()


def test_clone_timing(any_server, capsys):
    """Each clone of a small seed (2 tables, 4 rows) takes well under a second."""
    dbs = _fresh(any_server)
    try:
        dbs("seed")
        started = time.monotonic()
        for _ in range(10):
            dbs.release(dbs("seed"))
        per_clone = (time.monotonic() - started) / 10
    finally:
        dbs.close()
    with capsys.disabled():
        print(f"\n[clone+drop] {any_server.dialect}: {per_clone * 1000:.0f} ms; "
              f"clone alone {sum(dbs.timings[1:]) / 10 * 1000:.0f} ms")  # fmt: skip
    assert per_clone < 2.0
