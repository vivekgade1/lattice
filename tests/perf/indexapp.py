"""A FastAPI app for the missing-index check of ``lattice perf --explain`` (tests and probes;
pre-registration ``docs/results/2026-10-15-perf-missing-index-prereg.md``, section 3).

Tables: ``users(id, name, token)`` (ann, ben), ``items(id, user_id, name)``,
``notes(id, item_id, body)``; indexes ``items_user_id`` on ``items(user_id)`` and
``notes_item_id`` on ``notes(item_id)``. Endpoints (each request opens one connection):

* ``POST /login``; ``POST /items`` -> ``{"id"}``; ``POST /items/{id}/notes``;
* ``GET /items``: the caller and ``items WHERE user_id = ? ORDER BY id``: 2 statements;
* ``GET /items/{id}/notes``: the caller, ``items WHERE id = ? AND user_id = ?`` and
  ``notes WHERE item_id = ? ORDER BY id``: 3 statements, whatever N.

The database is a SQLite path or a Postgres / MySQL URL (the driver is imported when used,
through ``lattice.spec.db.open_db``). A MySQL app makes **one connection when the app is made**
(PyMySQL sends ``SET NAMES`` on every connect, which the general log counts as a statement of
the request: with a connection per request the counts were 3 and 4, not 2 and 3).
Factories by variant: :func:`build_db` (clean), :func:`drop_items` and :func:`drop_notes` (the
clean schema, then ``DROP INDEX`` on that database), so ``perf.budget.json`` budgets on them
differ only in the index.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException

SQLITE = (
    "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, token TEXT NOT NULL)",
    "CREATE TABLE items (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL)",
    "CREATE TABLE notes (id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL, body TEXT NOT NULL)",
    "CREATE INDEX items_user_id ON items(user_id)",
    "CREATE INDEX notes_item_id ON notes(item_id)",
)
POSTGRES = (
    "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT NOT NULL, token TEXT NOT NULL)",
    "CREATE TABLE items (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL)",
    "CREATE TABLE notes (id SERIAL PRIMARY KEY, item_id INTEGER NOT NULL, body TEXT NOT NULL)",
    "CREATE INDEX items_user_id ON items(user_id)",
    "CREATE INDEX notes_item_id ON notes(item_id)",
)
MYSQL = (
    "CREATE TABLE users (id INT PRIMARY KEY AUTO_INCREMENT, name VARCHAR(100) NOT NULL, "
    "token VARCHAR(100) NOT NULL)",
    "CREATE TABLE items (id INT PRIMARY KEY AUTO_INCREMENT, user_id INT NOT NULL, "
    "name VARCHAR(100) NOT NULL)",
    "CREATE TABLE notes (id INT PRIMARY KEY AUTO_INCREMENT, item_id INT NOT NULL, "
    "body VARCHAR(255) NOT NULL)",
    "CREATE INDEX items_user_id ON items(user_id)",
    "CREATE INDEX notes_item_id ON notes(item_id)",
)
DDL = {"sqlite": SQLITE, "postgres": POSTGRES, "mysql": MYSQL}
USERS = ((1, "ann", "tok-ann"), (2, "ben", "tok-ben"))

CALLER = "SELECT id FROM users WHERE token = ?"
LOGIN = "SELECT token FROM users WHERE name = ?"
ITEMS = "SELECT id, user_id, name FROM items WHERE user_id = ? ORDER BY id"
ITEM = "SELECT id FROM items WHERE id = ? AND user_id = ?"
NOTES = "SELECT id, item_id, body FROM notes WHERE item_id = ? ORDER BY id"


def kind_of(db: Any) -> str:
    text = str(db)
    if text.startswith(("postgres://", "postgresql://")):
        return "postgres"
    if text.startswith("mysql://"):
        return "mysql"
    return "sqlite"


def _make(db: Any, drop_index: str | None = None) -> Any:
    kind = kind_of(db)
    if kind == "sqlite":
        conn = sqlite3.connect(Path(db))
        try:
            for ddl in SQLITE:
                conn.execute(ddl)
            conn.executemany("INSERT INTO users VALUES (?, ?, ?)", USERS)
            if drop_index:
                conn.execute(f"DROP INDEX {drop_index}")
            conn.commit()
        finally:
            conn.close()
        return db
    from lattice.spec.db import open_db

    conn = open_db(str(db))
    try:
        for ddl in DDL[kind]:
            conn.execute(ddl)
        for row in USERS:
            conn.execute("INSERT INTO users (id, name, token) VALUES (%s, %s, %s)", row)
        if drop_index:
            table = "items" if drop_index == "items_user_id" else "notes"
            suffix = f" ON {table}" if kind == "mysql" else ""
            conn.execute(f"DROP INDEX {drop_index}{suffix}")
    finally:
        conn.close()
    return db


def build_db(db: Any) -> Any:
    """The clean schema (both indexes) and the users ann and ben."""
    return _make(db)


def drop_items(db: Any) -> Any:
    """The clean schema without ``items_user_id``."""
    return _make(db, "items_user_id")


def drop_notes(db: Any) -> Any:
    """The clean schema without ``notes_item_id``."""
    return _make(db, "notes_item_id")


def login(client: Any, handle: str) -> str:
    r = client.post("/login", json={"name": handle})
    if r.status_code != 200:
        raise RuntimeError(f"login {handle}: {r.status_code}")
    return r.json()["token"]


class _Session:
    """One connection: ``rows(sql, params)`` and ``insert(sql, params) -> new id``, with
    ``?`` placeholders for every engine."""

    def __init__(self, db: Any, shared: Any = None) -> None:
        self.kind = kind_of(db)
        self.shared = shared is not None
        if shared is not None:
            self.conn: Any = shared
        elif self.kind == "sqlite":
            self.conn = sqlite3.connect(Path(db), check_same_thread=False,
                                        isolation_level=None)  # fmt: skip
        else:
            from lattice.spec.db import open_db

            self.conn = open_db(str(db))

    def rows(self, sql: str, params: tuple = ()) -> list[tuple]:
        if self.kind == "sqlite":
            return [tuple(r) for r in self.conn.execute(sql, params).fetchall()]
        return self.conn.query(sql.replace("?", "%s"), params)

    def insert(self, sql: str, params: tuple) -> int:
        if self.kind == "sqlite":
            return int(self.conn.execute(sql, params).lastrowid)
        if self.kind == "postgres":
            return int(self.conn.query(sql.replace("?", "%s") + " RETURNING id", params)[0][0])
        self.conn.execute(sql.replace("?", "%s"), params)
        return int(self.conn.scalar("SELECT LAST_INSERT_ID()"))

    def close(self) -> None:
        if not self.shared:
            self.conn.close()


def make_app(db: Any) -> FastAPI:
    app = FastAPI()
    shared: Any = None
    if kind_of(db) == "mysql":
        from lattice.spec.db import open_db

        shared = open_db(str(db))  # made with the app: its SET NAMES precedes every window

    def session() -> _Session:
        return _Session(db, shared)

    def caller(s: _Session, authorization: str | None) -> int:
        _, _, token = (authorization or "").partition(" ")
        rows = s.rows(CALLER, (token,))
        if not rows:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return int(rows[0][0])

    @app.post("/login")
    def do_login(body: dict[str, Any]) -> Any:
        s = session()
        try:
            rows = s.rows(LOGIN, (body.get("name"),))
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
            return {"id": s.insert("INSERT INTO items (user_id, name) VALUES (?, ?)",
                                   (uid, str(body.get("name", ""))))}  # fmt: skip
        finally:
            s.close()

    @app.post("/items/{item_id}/notes", status_code=201)
    def add_note(item_id: int, body: dict[str, Any],
                 authorization: str | None = Header(None)) -> Any:  # fmt: skip
        s = session()
        try:
            uid = caller(s, authorization)
            if not s.rows(ITEM, (item_id, uid)):
                raise HTTPException(status_code=404, detail="no such item")
            return {"id": s.insert("INSERT INTO notes (item_id, body) VALUES (?, ?)",
                                   (item_id, str(body.get("body", ""))))}  # fmt: skip
        finally:
            s.close()

    @app.get("/items")
    def items(authorization: str | None = Header(None)) -> Any:
        s = session()
        try:
            uid = caller(s, authorization)
            return [{"id": r[0], "user_id": r[1], "name": r[2]} for r in s.rows(ITEMS, (uid,))]
        finally:
            s.close()

    @app.get("/items/{item_id}/notes")
    def notes(item_id: int, authorization: str | None = Header(None)) -> Any:
        s = session()
        try:
            uid = caller(s, authorization)
            if not s.rows(ITEM, (item_id, uid)):
                raise HTTPException(status_code=404, detail="no such item")
            return [{"id": r[0], "item_id": r[1], "body": r[2]} for r in s.rows(NOTES, (item_id,))]
        finally:
            s.close()

    return app
