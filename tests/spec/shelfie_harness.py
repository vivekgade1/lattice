"""Harness of tests/spec/shelfie: how the service is started, how its database is seeded, how callers log in.

``make_app(db_path)`` answers a ProcessApp (the binary, not started). The binary is built once from the
module with ``go build -mod=readonly`` into the system temp directory; the module directory is never written.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
import zlib
from pathlib import Path

from lattice.importer.transport import AuthDriver
from lattice.spec.process import ProcessApp, process_runtime

MODULE = Path(__file__).resolve().parent / "shelfie"
BINARY_ENV = "shelfie_BIN"  # optional: a prebuilt binary, instead of building
# handle -> (login name, password); at least two entries; the password is the one typed at login
USERS = {"ann": ("ann", "pw-ann"), "ben": ("ben", "pw-ben")}
BUILD_TIMEOUT_S = 600


def _source_key() -> str:
    """A short key of the module's sources: the binary is rebuilt only when it changes."""
    crc = 0
    for p in sorted(MODULE.rglob("*")):
        if p.is_file() and (p.suffix == ".go" or p.name in ("go.mod", "go.sum")):
            crc = zlib.crc32(p.relative_to(MODULE).as_posix().encode() + b"\0" + p.read_bytes() + b"\0", crc)
    return f"{crc:08x}"


def binary() -> Path:
    """The built service: ``$shelfie_BIN``, else built (once per source key) into the system temp directory."""
    if env := os.environ.get(BINARY_ENV):
        return Path(env)
    out = Path(tempfile.gettempdir()) / f"build-shelfie-{_source_key()}" / "shelfie"
    if out.is_file():
        return out
    go = shutil.which("go")
    if go is None:
        raise RuntimeError("shelfie: `go` is not on PATH")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".shelfie-{os.getpid()}")
    env = {**os.environ, "GOFLAGS": "-mod=readonly", "GOTOOLCHAIN": "auto"}
    proc = subprocess.run(  # noqa: S603 -- the module's own build
        [go, "build", "-trimpath", "-buildvcs=false", "-o", str(tmp), "."],
        cwd=MODULE, capture_output=True, text=True, check=False, env=env,
        timeout=BUILD_TIMEOUT_S,
    )  # fmt: skip
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"shelfie: go build failed:\n{proc.stderr.strip()}")
    os.replace(tmp, out)
    return out


def make_app(db_path: Path) -> ProcessApp:
    """The service on ``db_path`` (the service creates its own schema when it starts)."""
    return ProcessApp([str(binary())], db_path=db_path, health_path="/health")


RUNTIME = process_runtime(framework="go", auth=AuthDriver.json("/login", USERS))
LOGIN = RUNTIME.auth.login

# The schema exactly as the service creates it on an empty file (``python3 smoke.py OUT shelfie schema`` prints it).
DDL = """
CREATE TABLE `books` (`id` integer PRIMARY KEY AUTOINCREMENT,`shelf_id` integer NOT NULL,`title` text NOT NULL,`author` text NOT NULL,`status` text NOT NULL,`rating` integer,`created_at` datetime,`finished_at` datetime);
CREATE TABLE `notes` (`id` integer PRIMARY KEY AUTOINCREMENT,`user_id` integer NOT NULL,`book_id` integer NOT NULL,`body` text NOT NULL,`created_at` datetime);
CREATE TABLE `shelves` (`id` integer PRIMARY KEY AUTOINCREMENT,`user_id` integer NOT NULL,`name` text NOT NULL,`description` text NOT NULL);
CREATE TABLE `users` (`id` integer PRIMARY KEY AUTOINCREMENT,`username` text NOT NULL,`password` text NOT NULL,`display_name` text NOT NULL);
CREATE INDEX `idx_books_shelf_id` ON `books`(`shelf_id`);
CREATE INDEX `idx_notes_book_id` ON `notes`(`book_id`);
CREATE INDEX `idx_notes_user_id` ON `notes`(`user_id`);
CREATE INDEX `idx_shelves_user_id` ON `shelves`(`user_id`);
CREATE UNIQUE INDEX `idx_users_username` ON `users`(`username`);
"""  # noqa: E501


def build_db(path: Path) -> Path:
    """A seeded database written by hand: the users of ``USERS`` (the stored password is the plain text typed at
    login) and the rows the routes need (see HARNESS_API.md). Answers ``path``."""
    path = Path(path)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        for i, (login_name, password) in enumerate(USERS.values(), 1):
            conn.execute(
                "INSERT INTO users (id, username, password, display_name) VALUES (?, ?, ?, ?)",
                (i, login_name, password, login_name.capitalize()),
            )
        stamp = "2026-01-01 09:00:00.000000"
        conn.executemany(
            "INSERT INTO shelves (id, user_id, name, description) VALUES (?, ?, ?, ?)",
            [(1, 1, "Classics", "Old favourites"), (2, 2, "Science fiction", "Space and beyond")],
        )
        conn.executemany(
            "INSERT INTO books (id, shelf_id, title, author, status, rating, created_at, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, 1, "Middlemarch", "George Eliot", "queued", None, stamp, None),
                (2, 1, "Persuasion", "Jane Austen", "reading", None, stamp, None),
                (3, 2, "Dune", "Frank Herbert", "queued", None, stamp, None),
            ],
        )
        conn.execute(
            "INSERT INTO notes (id, user_id, book_id, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (1, 1, 1, "Recommended by a friend", stamp),
        )
        conn.commit()
    finally:
        conn.close()
    return path
