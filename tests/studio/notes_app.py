"""A FastAPI + SQLite app for the incremental toy's notes spec (tests/spec/incremental_toy.py,
v1): POST /notes, GET /notes/{id}, DELETE /notes/{id}; text 1-200 characters; at most
`limit` notes per user (v1 says 3); bearer tokens stored in users.token. The Studio server
tests hook it up through a project's studio.toml."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Response

DDL = """
CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, password TEXT NOT NULL,
                    token TEXT NOT NULL UNIQUE);
CREATE TABLE notes (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    text TEXT NOT NULL);
"""
SEED = (
    (1, "ann@example.com", "pw-ann", "tok-ann"),
    (2, "ben@example.com", "pw-ben", "tok-ben"),
)


def build_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        conn.executemany("INSERT INTO users VALUES (?, ?, ?, ?)", SEED)
        conn.commit()
    finally:
        conn.close()
    return path


def login(client: Any, handle: str) -> str:
    return f"tok-{handle}"


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=10, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _caller(conn: sqlite3.Connection, authorization: str | None) -> int:
    scheme, _, token = (authorization or "").partition(" ")
    row = conn.execute("SELECT id FROM users WHERE token = ?", (token,)).fetchone()
    if scheme.lower() != "bearer" or row is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return int(row["id"])


def _note(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "user_id": row["user_id"], "text": row["text"]}


def notes_app(limit: int = 3):
    """The app factory ``(db_path) -> FastAPI``."""

    def make_app(db_path: Path) -> FastAPI:
        db = Path(db_path)
        app = FastAPI()

        @app.post("/notes", status_code=201)
        def create(body: dict[str, Any], authorization: str | None = Header(None)) -> Any:
            conn = _connect(db)
            try:
                uid = _caller(conn, authorization)
                text = body.get("text")
                if not isinstance(text, str) or not 1 <= len(text) <= 200:
                    raise HTTPException(status_code=422, detail="text must be 1-200 characters")
                n = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id = ?", (uid,)).fetchone()
                if n[0] >= limit:
                    raise HTTPException(status_code=409, detail="too many notes")
                cur = conn.execute("INSERT INTO notes (user_id, text) VALUES (?, ?)", (uid, text))
                row = conn.execute("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone()
                return _note(row)
            finally:
                conn.close()

        def own(conn: sqlite3.Connection, nid: int, uid: int) -> sqlite3.Row:
            row = conn.execute(
                "SELECT * FROM notes WHERE id = ? AND user_id = ?", (nid, uid)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Not found")
            return row

        @app.get("/notes/{nid}")
        def read(nid: int, authorization: str | None = Header(None)) -> Any:
            conn = _connect(db)
            try:
                return _note(own(conn, nid, _caller(conn, authorization)))
            finally:
                conn.close()

        @app.delete("/notes/{nid}", status_code=204)
        def delete(nid: int, authorization: str | None = Header(None)) -> Response:
            conn = _connect(db)
            try:
                row = own(conn, nid, _caller(conn, authorization))
                conn.execute("DELETE FROM notes WHERE id = ?", (row["id"],))
                return Response(status_code=204)
            finally:
                conn.close()

        return app

    return make_app
