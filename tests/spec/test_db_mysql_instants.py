"""A spec's ``given`` instants on MySQL: the spec writes them as ISO 8601 UTC text
(``2026-03-01T00:00:00Z``), which a strict MySQL 8.4 refuses for a DATETIME or TIMESTAMP
column (error 1292); :class:`~lattice.spec.db.MysqlDatabase` inserts them as the naive UTC
literal (``2026-03-01 00:00:00``) into those columns only. The conversion and the column
choice run without a server (a fake PyMySQL connection); the round trip runs on the
session's MySQL container (skipped without Docker or PyMySQL)."""

from __future__ import annotations

from datetime import datetime

import pytest

from lattice.spec.check import _same
from lattice.spec.db import MysqlDatabase, naive_utc_instant, open_db
from lattice.spec.dbserver import ServerDatabases

# ---- the conversion (no server) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "naive"),
    [
        ("2026-03-01T00:00:00Z", "2026-03-01 00:00:00"),
        ("2026-03-01T12:30:45+00:00", "2026-03-01 12:30:45"),
        ("2026-03-01T12:30:45.250000Z", "2026-03-01 12:30:45.250000"),
        ("2026-03-01T12:30:45.5+00:00", "2026-03-01 12:30:45.5"),
    ],
)
def test_a_utc_instant_becomes_the_naive_utc_literal(text, naive):
    assert naive_utc_instant(text) == naive


@pytest.mark.parametrize(
    "value",
    [
        "2026-03-01",  # a date
        "2026-03-01 00:00:00",  # already naive
        "2026-03-01T00:00:00",  # no zone: MySQL takes it as it is
        "2026-03-01T00:00:00+02:00",  # another offset: not UTC, left to MySQL
        "2026-03-01T00:00:00.1234567Z",  # more digits than MySQL keeps
        "at 2026-03-01T00:00:00Z",  # text that merely contains an instant
        "2026-03-01T00:00:00Z ",
        "ann",
        "",
        None,
        7,
        1.5,
        True,
        datetime(2026, 3, 1),
    ],
)
def test_anything_else_is_left_as_it_is(value):
    assert naive_utc_instant(value) is value


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn
        self.description: list[tuple[str]] | None = None
        self._rows: list[tuple] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def execute(self, sql: str, params: list | None) -> None:
        self.conn.calls.append((sql, params))
        if "information_schema" in sql:
            self.description = [("COLUMN_NAME",)]
            self._rows = [(c,) for c in self.conn.timed]
        else:
            self.description, self._rows = None, []

    def fetchall(self) -> list[tuple]:
        return self._rows


class _Conn:
    """PyMySQL's surface that MysqlDatabase uses, recording each statement."""

    def __init__(self, timed: list[str]) -> None:
        self.timed = timed  # the table's DATETIME/TIMESTAMP columns
        self.calls: list[tuple[str, list | None]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)


def _fake_mysql(timed: list[str]) -> MysqlDatabase:
    db = MysqlDatabase.__new__(MysqlDatabase)
    db.url = "mysql://root:pw@127.0.0.1:3306/app"
    db._errors = ()
    db.conn = _Conn(timed)  # type: ignore[assignment]
    return db


def _inserts(db: MysqlDatabase) -> list[tuple[str, list | None]]:
    return [c for c in db.conn.calls if c[0].startswith("INSERT")]  # type: ignore[attr-defined]


def test_mysql_inserts_an_instant_into_a_datetime_column_as_naive_utc():
    db = _fake_mysql(["created_at", "Expires_At"])
    db.insert(
        "sessions",
        [
            {"id": 1, "created_at": "2026-03-01T00:00:00Z", "note": "2026-03-01T00:00:00Z"},
            {"id": 2, "expires_at": "2026-04-01T00:00:00.5+00:00", "note": "ann"},
        ],
    )
    assert _inserts(db) == [
        ("INSERT INTO `sessions` (`id`, `created_at`, `note`) VALUES (%s, %s, %s)",
         [1, "2026-03-01 00:00:00", "2026-03-01T00:00:00Z"]),
        ("INSERT INTO `sessions` (`id`, `expires_at`, `note`) VALUES (%s, %s, %s)",
         [2, "2026-04-01 00:00:00.5", "ann"]),
    ]  # fmt: skip
    asked = [c for c in db.conn.calls if "information_schema" in c[0]]  # type: ignore[attr-defined]
    assert len(asked) == 1 and asked[0][1] == ["sessions"]
    assert "'datetime'" in asked[0][0] and "'timestamp'" in asked[0][0]


def test_mysql_leaves_a_text_column_holding_an_instant_alone():
    db = _fake_mysql([])  # no DATETIME/TIMESTAMP column
    db.insert("notes", [{"id": 1, "body": "2026-03-01T00:00:00Z"}])
    assert _inserts(db) == [
        ("INSERT INTO `notes` (`id`, `body`) VALUES (%s, %s)", [1, "2026-03-01T00:00:00Z"]),
    ]


def test_mysql_rows_without_an_instant_are_inserted_as_before():
    """No instant-like value: no extra query, the statements of the base adapter."""
    db = _fake_mysql(["created_at"])
    db.insert("users", iter([{"id": 1, "name": "ann", "created_at": None}]))
    assert db.conn.calls == [  # type: ignore[attr-defined]
        ("INSERT INTO `users` (`id`, `name`, `created_at`) VALUES (%s, %s, %s)",
         [1, "ann", None]),
    ]  # fmt: skip


# ---- the round trip on a MySQL container --------------------------------------------------------


@pytest.fixture
def pymysql_driver() -> None:
    pytest.importorskip("pymysql", reason="PyMySQL is not installed (the `db` extra)")


def test_a_utc_instant_round_trips_through_a_strict_mysql(pymysql_driver, my):
    def factory(url: str) -> None:
        with open_db(url) as db:
            db.execute(
                "CREATE TABLE events (id INT PRIMARY KEY, at DATETIME NOT NULL, "
                "stamp TIMESTAMP(6) NULL, note VARCHAR(40))"
            )

    dbs = ServerDatabases(my, factory)
    try:
        url = dbs("seed")
        with open_db(url) as db:
            assert "STRICT_TRANS_TABLES" in db.scalar("SELECT @@SESSION.sql_mode")
            db.insert(
                "events",
                [{"id": 1, "at": "2026-03-01T00:00:00Z", "stamp": "2026-03-01T12:30:45.25Z",
                  "note": "2026-03-01T00:00:00Z"},
                 {"id": 2, "at": "2026-03-02T08:00:00+00:00", "note": "plain"}],
            )  # fmt: skip
            rows = db.read_table("events", "id")
        assert rows[0]["at"] == datetime(2026, 3, 1)
        assert _same("2026-03-01T00:00:00Z", rows[0]["at"])
        assert _same("2026-03-01T12:30:45.25Z", rows[0]["stamp"])
        assert rows[0]["note"] == "2026-03-01T00:00:00Z"  # text stays text
        assert rows[1]["at"] == datetime(2026, 3, 2, 8) and rows[1]["stamp"] is None
    finally:
        dbs.close()
