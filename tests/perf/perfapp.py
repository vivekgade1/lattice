"""A tiny FastAPI + SQLite app for lattice.perf's tests: users with bearer tokens, items
owned by a user. ``GET /items`` reads the caller's items in one statement (2 per request
with the caller lookup); ``GET /items_n1`` also reads each item's owner again, one statement
per item (2 + N); ``GET /broken`` answers 500. Each request opens one connection in
autocommit mode, so no BEGIN/COMMIT is traced."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException

DDL = """
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, token TEXT NOT NULL);
CREATE TABLE items (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL);
"""


def build_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        conn.execute("INSERT INTO users VALUES (1, 'ann', 'tok-ann'), (2, 'ben', 'tok-ben')")
        conn.commit()
    finally:
        conn.close()
    return path


def login(client: Any, handle: str) -> str:
    r = client.post("/login", json={"name": handle})
    if r.status_code != 200:
        raise RuntimeError(f"login {handle}: {r.status_code}")
    return r.json()["token"]


def make_app(db_path: Path) -> FastAPI:
    db = Path(db_path)
    app = FastAPI()

    def connect() -> sqlite3.Connection:
        conn = sqlite3.connect(db, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def caller(conn: sqlite3.Connection, authorization: str | None) -> int:
        _, _, token = (authorization or "").partition(" ")
        row = conn.execute("SELECT id FROM users WHERE token = ?", (token,)).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return int(row["id"])

    @app.post("/login")
    def do_login(body: dict[str, Any]) -> Any:
        conn = connect()
        try:
            row = conn.execute("SELECT token FROM users WHERE name = ?",
                               (body.get("name"),)).fetchone()  # fmt: skip
            if row is None:
                raise HTTPException(status_code=400, detail="no such user")
            return {"token": row["token"]}
        finally:
            conn.close()

    @app.post("/items", status_code=201)
    def create(body: dict[str, Any], authorization: str | None = Header(None)) -> Any:
        conn = connect()
        try:
            uid = caller(conn, authorization)
            cur = conn.execute("INSERT INTO items (user_id, name) VALUES (?, ?)",
                               (uid, str(body.get("name", ""))))  # fmt: skip
            return {"id": cur.lastrowid}
        finally:
            conn.close()

    @app.get("/items")
    def items(authorization: str | None = Header(None)) -> Any:
        conn = connect()
        try:
            uid = caller(conn, authorization)
            rows = conn.execute("SELECT * FROM items WHERE user_id = ? ORDER BY id", (uid,))
            return [dict(r) for r in rows.fetchall()]
        finally:
            conn.close()

    @app.get("/items_n1")
    def items_n1(authorization: str | None = Header(None)) -> Any:
        conn = connect()
        try:
            uid = caller(conn, authorization)
            out = []
            for r in conn.execute("SELECT * FROM items WHERE user_id = ? ORDER BY id",
                                  (uid,)).fetchall():  # fmt: skip
                owner = conn.execute("SELECT name FROM users WHERE id = ?",
                                     (r["user_id"],)).fetchone()  # fmt: skip
                out.append({**dict(r), "owner": owner["name"]})
            return out
        finally:
            conn.close()

    @app.get("/broken")
    def broken() -> Any:
        raise HTTPException(status_code=500, detail="broken")

    return app
