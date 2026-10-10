"""A toy app as a process over a server database, for tests/spec/test_multidb.py.

It talks to its database with the drivers directly (not through lattice), as any app
would, given the checker's canonical URL in ``$DATABASE_URL``::

    python multidb_app.py migrate URL MODE   # create the schema (the db factory runs it)
    python multidb_app.py serve              # $PORT, $DATABASE_URL, $APP_MODE
    python multidb_app.py worker URL         # the background worker (an Await's service)

**Runs** (Postgres, MySQL): ``POST /configs/{cid}/runs`` (JSON ``{"urgent", "note"}``)
queues a run, ``POST /runs/{rid}/cancel`` cancels a live run, ``GET /runs/{rid}``; the
worker moves queued runs to running, then succeeded. ``$APP_MODE``:

* ``safe``: a config's live runs are unique in the schema (Postgres: a partial unique
  index; MySQL: a unique key on a generated column), a duplicate answered 409;
* ``racy``: check-then-insert with a sleep between (no constraint): concurrent creates
  both pass;
* ``buggy``: a create inserts its run twice (a planted retry bug).

**Events** (ClickHouse): ``POST /events`` (JSON ``{"user_id", "amount", "flagged"}``),
``GET /events/{id}``; ``buggy`` stores ``amount - 100`` (a planted bug).

Callers: bearer ``tok-<handle>`` (``users.token``).
"""

from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit


def dialect(url: str) -> str:
    scheme = urlsplit(url).scheme
    return {"postgresql": "postgres", "postgres": "postgres", "mysql": "mysql",
            "clickhouse": "clickhouse"}[scheme]  # fmt: skip


class Conn:
    """One connection: ``q`` answers rows, ``x`` runs a write and answers the new id."""

    def __init__(self, url: str) -> None:
        self.dialect = dialect(url)
        p = urlsplit(url)
        if self.dialect == "postgres":
            import psycopg

            self.c = psycopg.connect(url, autocommit=True)
            self.unique = psycopg.errors.UniqueViolation
        elif self.dialect == "mysql":
            import pymysql

            self.c = pymysql.connect(host=p.hostname, port=p.port, user=unquote(p.username),
                                     password=unquote(p.password), database=p.path[1:],
                                     autocommit=True)  # fmt: skip
            self.unique = pymysql.err.IntegrityError
        else:
            import clickhouse_connect

            self.c = clickhouse_connect.get_client(host=p.hostname, port=p.port,
                                                   username=unquote(p.username),
                                                   password=unquote(p.password),
                                                   database=p.path[1:])  # fmt: skip
            self.unique = ()

    def q(self, sql: str, params: tuple = ()) -> list[tuple]:
        if self.dialect == "clickhouse":
            return list(self.c.query(sql, parameters=list(params) or None).result_rows)
        with self.c.cursor() as cur:
            cur.execute(sql, params or None)
            return list(cur.fetchall()) if cur.description else []

    def x(self, sql: str, params: tuple = ()) -> Any:
        if self.dialect == "clickhouse":
            self.c.command(sql, parameters=list(params) or None)
            return None
        with self.c.cursor() as cur:
            if self.dialect == "postgres" and sql.lstrip().upper().startswith("INSERT"):
                cur.execute(sql + " RETURNING id", params)
                return cur.fetchone()[0]
            cur.execute(sql, params or None)
            return getattr(cur, "lastrowid", None)

    def close(self) -> None:
        self.c.close()


# ---- schema --------------------------------------------------------------------------------

USERS = "CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(40) NOT NULL, token VARCHAR(40) NOT NULL)"  # noqa: E501
CONFIGS = "CREATE TABLE configs (id INTEGER PRIMARY KEY, name VARCHAR(40) NOT NULL)"


def migrate(url: str, mode: str) -> None:
    conn = Conn(url)
    try:
        if conn.dialect == "postgres":
            conn.x(USERS)
            conn.x(CONFIGS)
            conn.x("CREATE TABLE runs (id SERIAL PRIMARY KEY, config_id INTEGER NOT NULL "
                   "REFERENCES configs(id), status TEXT NOT NULL, urgent BOOLEAN NOT NULL "
                   "DEFAULT FALSE, note TEXT NULL)")  # fmt: skip
            if mode == "safe":
                conn.x("CREATE UNIQUE INDEX one_live_run ON runs(config_id) "
                       "WHERE status IN ('queued', 'running')")  # fmt: skip
        elif conn.dialect == "mysql":
            conn.x(USERS)
            conn.x(CONFIGS)
            unique = ", UNIQUE KEY one_live_run (live_config)" if mode == "safe" else ""
            conn.x("CREATE TABLE runs (id INTEGER AUTO_INCREMENT PRIMARY KEY, config_id "
                   "INTEGER NOT NULL, status VARCHAR(20) NOT NULL, urgent BOOLEAN NOT NULL "
                   "DEFAULT FALSE, note TEXT NULL, live_config INTEGER AS (CASE WHEN status "
                   "IN ('queued', 'running') THEN config_id END) STORED, FOREIGN KEY "
                   f"(config_id) REFERENCES configs(id){unique})")  # fmt: skip
        else:
            conn.x("CREATE TABLE users (id Int64, name String, token String) "
                   "ENGINE = MergeTree ORDER BY id")  # fmt: skip
            conn.x("CREATE TABLE events (id UInt64, user_id Int64, amount Int64, flagged Bool, "
                   "note Nullable(String)) ENGINE = MergeTree ORDER BY id")  # fmt: skip
    finally:
        conn.close()


# ---- the server ----------------------------------------------------------------------------

URL = os.environ.get("DATABASE_URL", "")
MODE = os.environ.get("APP_MODE", "safe")


def run_row(row: tuple) -> dict[str, Any]:
    return {"id": row[0], "config_id": row[1], "status": row[2], "urgent": bool(row[3]),
            "note": row[4]}  # fmt: skip


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # quiet
        pass

    def answer(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def caller(self, conn: Conn) -> int | None:
        token = (self.headers.get("Authorization") or "").partition(" ")[2]
        rows = conn.q("SELECT id FROM users WHERE token = %s", (token,))
        return int(rows[0][0]) if rows else None

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            return self.answer(200, {"ok": True})
        conn = Conn(URL)
        try:
            if self.caller(conn) is None:
                return self.answer(401, {"detail": "Not authenticated"})
            if m := re.fullmatch(r"/runs/(\d+)", self.path):
                rows = conn.q("SELECT id, config_id, status, urgent, note FROM runs "
                              "WHERE id = %s", (int(m.group(1)),))  # fmt: skip
                if not rows:
                    return self.answer(404, {"detail": "Not found"})
                return self.answer(200, run_row(rows[0]))
            if m := re.fullmatch(r"/events/(\d+)", self.path):
                rows = conn.q("SELECT id, user_id, amount FROM events WHERE id = %s",
                              (int(m.group(1)),))  # fmt: skip
                if not rows:
                    return self.answer(404, {"detail": "Not found"})
                return self.answer(200, {"id": rows[0][0], "user_id": rows[0][1],
                                         "amount": rows[0][2]})  # fmt: skip
            return self.answer(404, {"detail": "Not found"})
        finally:
            conn.close()

    def do_POST(self) -> None:  # noqa: N802
        conn = Conn(URL)
        try:
            if self.caller(conn) is None:
                return self.answer(401, {"detail": "Not authenticated"})
            body = self.body()
            if m := re.fullmatch(r"/configs/(\d+)/runs", self.path):
                return self.create_run(conn, int(m.group(1)), body)
            if m := re.fullmatch(r"/runs/(\d+)/cancel", self.path):
                rid = int(m.group(1))
                conn.x("UPDATE runs SET status = 'cancelled' WHERE id = %s AND status IN "
                       "('queued', 'running')", (rid,))  # fmt: skip
                rows = conn.q("SELECT id, config_id, status, urgent, note FROM runs "
                              "WHERE id = %s", (rid,))  # fmt: skip
                if not rows:
                    return self.answer(404, {"detail": "Not found"})
                return self.answer(200, run_row(rows[0]))
            if self.path == "/events":
                (nid,) = conn.q("SELECT max(id) + 1 FROM events")[0]
                amount = int(body["amount"]) - (100 if MODE == "buggy" else 0)
                row = [int(nid), int(body["user_id"]), amount, bool(body.get("flagged")),
                       body.get("note")]  # fmt: skip
                cols = ["id", "user_id", "amount", "flagged", "note"]
                conn.c.insert("events", [row], column_names=cols)
                return self.answer(201, {"id": int(nid), "user_id": int(body["user_id"]),
                                         "amount": int(body["amount"])})  # fmt: skip
            return self.answer(404, {"detail": "Not found"})
        finally:
            conn.close()

    def create_run(self, conn: Conn, cid: int, body: dict[str, Any]) -> None:
        if not conn.q("SELECT 1 FROM configs WHERE id = %s", (cid,)):
            return self.answer(404, {"detail": "Not found"})
        args = (cid, bool(body.get("urgent", False)), body.get("note"))
        insert = "INSERT INTO runs (config_id, status, urgent, note) VALUES (%s, 'queued', %s, %s)"
        if MODE != "safe":
            live = conn.q("SELECT COUNT(*) FROM runs WHERE config_id = %s AND status IN "
                          "('queued', 'running')", (cid,))[0][0]  # fmt: skip
            if live:
                return self.answer(409, {"detail": "already live"})
            time.sleep(0.03)  # widen the check-then-insert window
        try:
            rid = conn.x(insert, args)
            if MODE == "buggy":
                conn.x(insert, args)  # the planted bug: a retry inserts the run again
        except conn.unique:
            return self.answer(409, {"detail": "already live"})
        rows = conn.q("SELECT id, config_id, status, urgent, note FROM runs WHERE id = %s",
                      (rid,))  # fmt: skip
        return self.answer(201, run_row(rows[0]))


def worker(url: str) -> None:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    while True:
        conn = Conn(url)
        try:
            conn.x("UPDATE runs SET status = 'running' WHERE status = 'queued'")
            conn.x("UPDATE runs SET status = 'succeeded' WHERE status = 'running'")
        finally:
            conn.close()
        time.sleep(0.05)


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if command == "migrate":
        migrate(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "safe")
    elif command == "worker":
        worker(sys.argv[2])
    else:
        # a listen backlog for layer X's concurrent clients (the default, 5, resets some)
        ThreadingHTTPServer.request_queue_size = 128
        ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
