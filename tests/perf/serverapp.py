"""A tiny FastAPI app on a Postgres or ClickHouse server, for ``lattice.perf``'s counting
tests and probes (the ``perfapp.py`` pattern: users with bearer tokens, items owned by a user).

* ``POST /login`` (1 statement), ``POST /items`` (the caller lookup + the insert),
* ``GET /items``: the caller and the caller's items, 2 statements;
* ``GET /items_n1``: also one owner lookup per item, ``2 + N`` statements;
* ``GET /nodb``: no statement; ``GET /health``: none; ``GET /broken``: 500.

The engine is read from the database URL. Postgres: ``psycopg``, one autocommit connection
per request. ClickHouse: ``clickhouse-connect``, **one client made when the app is made**
(the driver's own queries on creation come before any window). Item ids are uuids (the app
makes them). The drivers are imported when first used, so this module imports without them.

``make_app(url)`` is the in-process app; ``make_process_app(url)`` the same app as a process
(``python serverapp.py`` under uvicorn: ``DB_PATH`` carries the URL, ``PORT`` the port).
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI, Header, HTTPException

from lattice.spec.process import ProcessApp

PG = (
    "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, token TEXT NOT NULL)",
    "CREATE TABLE items (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL)",
)
CH = (
    "CREATE TABLE users (id Int64, name String, token String) ENGINE = MergeTree ORDER BY id",
    "CREATE TABLE items (id String, user_id Int64, name String) ENGINE = MergeTree ORDER BY id",
)
USERS = [[1, "ann", "tok-ann"], [2, "ben", "tok-ben"]]
CALLER = "SELECT id FROM users WHERE token = %s"
OWNER = "SELECT name FROM users WHERE id = %s"
ITEMS = "SELECT * FROM items WHERE user_id = %s ORDER BY id"


def engine_of(url: str) -> str:
    scheme = urlsplit(url).scheme.lower()
    if scheme in ("postgres", "postgresql"):
        return "postgres"
    if scheme in ("clickhouse", "ch", "http", "https"):
        return "clickhouse"
    raise ValueError(f"serverapp: no engine for {scheme!r} URLs")


def _client(url: str) -> Any:
    import clickhouse_connect

    u = urlsplit(url)
    return clickhouse_connect.get_client(
        host=u.hostname or "127.0.0.1", port=u.port or 8123,
        username=unquote(u.username or "default"), password=unquote(u.password or ""),
        database=u.path.lstrip("/") or "default", connect_timeout=10,
        autogenerate_session_id=False,
    )  # fmt: skip


def build_db(url: str) -> str:
    """The schema and two users (ann, ben) on the empty database ``url``."""
    if engine_of(url) == "postgres":
        import psycopg

        conn = psycopg.connect(url, autocommit=True)
        try:
            for ddl in PG:
                conn.execute(ddl)
            for row in USERS:
                conn.execute("INSERT INTO users (id, name, token) VALUES (%s, %s, %s)", row)
        finally:
            conn.close()
        return url
    client = _client(url)
    try:
        for ddl in CH:
            client.command(ddl)
        client.insert("users", USERS, column_names=["id", "name", "token"])
    finally:
        client.close()
    return url


def login(client: Any, handle: str) -> str:
    r = client.post("/login", json={"name": handle})
    if r.status_code != 200:
        raise RuntimeError(f"login {handle}: {r.status_code}")
    return r.json()["token"]


def make_app(url: str) -> FastAPI:
    app = FastAPI()
    url = str(url)

    if engine_of(url) == "postgres":
        import psycopg

        class Session:
            def __init__(self) -> None:
                self.conn = psycopg.connect(url, autocommit=True)

            def query(self, sql: str, params: tuple = ()) -> list[tuple]:
                with self.conn.cursor() as cur:
                    cur.execute(sql, list(params))
                    return [tuple(r) for r in cur.fetchall()]

            def insert_item(self, item_id: str, user_id: int, name: str) -> None:
                self.conn.execute("INSERT INTO items (id, user_id, name) VALUES (%s, %s, %s)",
                                  [item_id, user_id, name])  # fmt: skip

            def close(self) -> None:
                self.conn.close()

        def session() -> Any:
            return Session()
    else:
        shared = _client(url)  # one client for the app's life

        class Shared:
            def query(self, sql: str, params: tuple = ()) -> list[tuple]:
                result = shared.query(sql, parameters=list(params))
                return [tuple(r) for r in result.result_rows]

            def insert_item(self, item_id: str, user_id: int, name: str) -> None:
                shared.insert("items", [[item_id, user_id, name]],
                              column_names=["id", "user_id", "name"])  # fmt: skip

            def close(self) -> None:
                return None

        def session() -> Any:
            return Shared()

    def caller(s: Any, authorization: str | None) -> int:
        _, _, token = (authorization or "").partition(" ")
        rows = s.query(CALLER, (token,))
        if not rows:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return int(rows[0][0])

    @app.get("/health")
    def health() -> Any:
        return {"ok": True}

    @app.post("/login")
    def do_login(body: dict[str, Any]) -> Any:
        s = session()
        try:
            rows = s.query("SELECT token FROM users WHERE name = %s", (body.get("name"),))
            if not rows:
                raise HTTPException(status_code=400, detail="no such user")
            return {"token": rows[0][0]}
        finally:
            s.close()

    @app.post("/items", status_code=201)
    def create(body: dict[str, Any], authorization: str | None = Header(None)) -> Any:
        s = session()
        try:
            uid = caller(s, authorization)
            item_id = str(uuid.uuid4())
            s.insert_item(item_id, uid, str(body.get("name", "")))
            return {"id": item_id}
        finally:
            s.close()

    @app.get("/items")
    def items(authorization: str | None = Header(None)) -> Any:
        s = session()
        try:
            uid = caller(s, authorization)
            return [{"id": r[0], "user_id": r[1], "name": r[2]} for r in s.query(ITEMS, (uid,))]
        finally:
            s.close()

    @app.get("/items_n1")
    def items_n1(authorization: str | None = Header(None)) -> Any:
        s = session()
        try:
            uid = caller(s, authorization)
            out = []
            for r in s.query(ITEMS, (uid,)):
                owner = s.query(OWNER, (r[1],))
                out.append({"id": r[0], "user_id": r[1], "name": r[2], "owner": owner[0][0]})
            return out
        finally:
            s.close()

    @app.get("/nodb")
    def nodb() -> Any:
        return {"ok": True}

    @app.get("/broken")
    def broken() -> Any:
        raise HTTPException(status_code=500, detail="broken")

    return app


def make_process_app(url: str) -> ProcessApp:
    """The app as a process (the harness's ``app`` factory for a process runtime)."""
    return ProcessApp(
        [sys.executable, str(Path(__file__).resolve())], db_path=url, port_env="PORT",
        db_env="DB_PATH", health_path="/health", startup_timeout=30.0,
    )  # fmt: skip


def main() -> None:  # pragma: no cover -- the process app's entry point
    import uvicorn

    uvicorn.run(make_app(os.environ["DB_PATH"]), host="127.0.0.1", port=int(os.environ["PORT"]),
                log_level="warning")  # fmt: skip


if __name__ == "__main__":  # pragma: no cover
    main()
