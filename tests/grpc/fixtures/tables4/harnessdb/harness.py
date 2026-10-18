"""The harness of t4harness: the database schema lives here and nowhere in the Go tree."""

from __future__ import annotations

# ruff: noqa: E501 -- docstrings and data lines
import sqlite3
from pathlib import Path


def build_db(path: Path) -> Path:
    if Path(__file__).resolve().parent in Path(path).resolve().parents:
        raise RuntimeError("the database was asked for under the target root")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, balance REAL DEFAULT 0);
        CREATE TABLE sessions (token TEXT PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id));
        CREATE TABLE orders (id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), total INTEGER NOT NULL, paid BOOLEAN);
        INSERT INTO accounts (name, balance) VALUES ('ada', 10.5);
        """
    )
    conn.commit()
    conn.close()
    return Path(path)


def make_app(db_path: Path) -> None:
    raise NotImplementedError("fixture: extraction only")
