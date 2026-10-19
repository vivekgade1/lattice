"""How a check starts the toy shelf server: ``make_app`` / ``build_db`` / ``RUNTIME`` (see CONTEXT.md)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lattice.spec.process import ProcessApp, process_runtime

HERE = Path(__file__).resolve().parent
PROTOS = ("proto/toy/v1/shelf.proto",)
PROTO_PATHS = ("proto",)

DDL = """
CREATE TABLE items (id integer PRIMARY KEY AUTOINCREMENT, name text NOT NULL, qty integer NOT NULL);
CREATE UNIQUE INDEX idx_items_name ON items(name);
"""


def make_app(db_path: Path) -> ProcessApp:
    return ProcessApp(
        [str(HERE / "bin" / "toy-server")],
        db_path=db_path,
        port_env="PORT",
        db_env="DB_PATH",
        ready="grpc",
        protos=tuple(str(HERE / p) for p in PROTOS),
        proto_paths=tuple(str(HERE / p) for p in PROTO_PATHS),
        startup_timeout=20.0,
    )


def build_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        conn.commit()
    finally:
        conn.close()
    return path


RUNTIME = process_runtime(framework="go")
