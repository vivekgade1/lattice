"""The harness of ``tests/spec/gotickets``: a small ticket service on Gin + GORM over
``glebarez/sqlite``, with ticket ownership, an open/closed machine, handoffs between users and
a private column (``users.password_hash``).

``make_app(db_path)`` answers a :class:`~lattice.spec.process.ProcessApp` (the binary, not
started). The binary is the one ``$LATTICE_GOTICKETS_BIN`` names, else built once from the
module (``go build``, ``-mod=readonly``, written under the system temp directory only: the
module directory is never written). ``RUNTIME`` starts it on a free port with ``PORT`` and
``DB_PATH`` set and logs callers in through ``POST /login`` (a signed bearer token).

The rules-first import::

    python -m lattice.importer tests/spec --package gotickets --rules-only --out OUT

reads ``tests/spec/gotickets/studio.toml``, whose ``[app] app`` names ``make_app`` here.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from lattice.importer.transport import AuthDriver
from lattice.spec.process import ProcessApp, process_runtime

MODULE = Path(__file__).resolve().parent / "gotickets"
BINARY_ENV = "LATTICE_GOTICKETS_BIN"
USERS = {"ann": ("ann", "pw-ann"), "ben": ("ben", "pw-ben")}
QUEUES = ("billing", "support")
BUILD_TIMEOUT_S = 600


def _source_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(MODULE.rglob("*")):
        if p.is_file() and (p.suffix == ".go" or p.name in ("go.mod", "go.sum")):
            h.update(p.relative_to(MODULE).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def binary() -> Path:
    """The built service: ``$LATTICE_GOTICKETS_BIN``, else built (once per source hash) into
    the system temp directory. Raises ``RuntimeError`` with ``go``'s output when the build
    fails (no Go toolchain, or the module cannot be downloaded)."""
    if env := os.environ.get(BINARY_ENV):
        return Path(env)
    out = Path(tempfile.gettempdir()) / f"lattice-gotickets-{_source_hash()}" / "gotickets"
    if out.is_file():
        return out
    go = shutil.which("go")
    if go is None:
        raise RuntimeError("gotickets: `go` is not on PATH")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".gotickets-{os.getpid()}")
    env = {**os.environ, "GOFLAGS": "-mod=readonly", "GOTOOLCHAIN": "auto", "CGO_ENABLED": "0"}
    proc = subprocess.run(  # noqa: S603 -- the fixture's own module
        [go, "build", "-trimpath", "-buildvcs=false", "-o", str(tmp), "."],
        cwd=MODULE, capture_output=True, text=True, check=False, env=env,
        timeout=BUILD_TIMEOUT_S,
    )  # fmt: skip
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"gotickets: go build failed:\n{proc.stderr.strip()}")
    os.replace(tmp, out)
    return out


def make_app(db_path: Path) -> ProcessApp:
    """The Go service on ``db_path`` (the schema is created by the service: AutoMigrate)."""
    return ProcessApp([str(binary())], db_path=db_path, health_path="/health")


RUNTIME = process_runtime(framework="go", auth=AuthDriver.json("/login", USERS))
LOGIN = RUNTIME.auth.login

# The schema exactly as the service's AutoMigrate writes it, so a start on this file migrates
# nothing.
DDL = """
CREATE TABLE `users` (`id` integer PRIMARY KEY AUTOINCREMENT,`name` text,`password_hash` text);
CREATE UNIQUE INDEX `idx_users_name` ON `users`(`name`);
CREATE TABLE `queues` (`id` integer PRIMARY KEY AUTOINCREMENT,`name` text);
CREATE TABLE `tickets` (`id` integer PRIMARY KEY AUTOINCREMENT,`queue_id` integer,`reporter_id` integer,
                        `title` text,`status` text DEFAULT "open");
CREATE INDEX `idx_tickets_reporter_id` ON `tickets`(`reporter_id`);
CREATE INDEX `idx_tickets_queue_id` ON `tickets`(`queue_id`);
CREATE TABLE `handoffs` (`id` integer PRIMARY KEY AUTOINCREMENT,`ticket_id` integer,`from_user_id` integer,
                         `to_user_id` integer);
CREATE INDEX `idx_handoffs_to_user_id` ON `handoffs`(`to_user_id`);
CREATE INDEX `idx_handoffs_from_user_id` ON `handoffs`(`from_user_id`);
CREATE INDEX `idx_handoffs_ticket_id` ON `handoffs`(`ticket_id`);
"""  # noqa: E501


def build_db(path: Path) -> Path:
    """A seeded database by hand (the harness' own seed; the import uses the Seeder's): the two
    users (the password stored as the equal plain value, which ``POST /login`` accepts), two
    queues and no tickets. Answers ``path``."""
    path = Path(path)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        for i, (name, pw) in enumerate(USERS.values(), 1):
            conn.execute("INSERT INTO users (id, name, password_hash) VALUES (?, ?, ?)", (i, name, pw))
        for i, name in enumerate(QUEUES, 1):
            conn.execute("INSERT INTO queues (id, name) VALUES (?, ?)", (i, name))
        conn.commit()
    finally:
        conn.close()
    return path
