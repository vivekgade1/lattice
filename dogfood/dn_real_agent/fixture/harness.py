"""How a check starts the shelf service: ``make_app`` / ``build_db`` / ``RUNTIME`` (see CONTEXT.md)."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from lattice.spec.process import ProcessApp, process_runtime

HERE = Path(__file__).resolve().parent

DDL = """
CREATE TABLE items (id integer PRIMARY KEY AUTOINCREMENT, name text NOT NULL, qty integer NOT NULL);
CREATE UNIQUE INDEX idx_items_name ON items(name);
"""


def make_app(db_path: Path) -> ProcessApp:
    dotnet = shutil.which("dotnet") or "dotnet"
    return ProcessApp(
        [dotnet, str(HERE / "bin" / "out" / "dnbuild.dll")],
        db_path=db_path,
        env={},
        cwd=str(HERE / "bin" / "out"),
        health_path="/health",
    )


def build_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        conn.commit()
    finally:
        conn.close()
    return path


RUNTIME = process_runtime(framework="dotnet")
