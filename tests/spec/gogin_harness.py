"""The harness of ``tests/spec/goginapp`` (Spike J, node J1, arm S2): the goapp service
rewritten on Gin + GORM over ``glebarez/sqlite``, with run ownership and private columns.

``make_app(db_path)`` answers a :class:`~lattice.spec.process.ProcessApp` (the binary, not
started). The binary is the one ``$LATTICE_GOGINAPP_BIN`` names, else built once from the
module (``go build``, ``-mod=readonly``, written under the system temp directory only: the
module directory is never written). ``RUNTIME`` starts it on a free port with ``PORT`` and
``DB_PATH`` set and logs callers in through ``POST /login``.

The rules-first import::

    python -m lattice.importer tests/spec --package goginapp --rules-only --out OUT

reads ``tests/spec/goginapp/studio.toml``, whose ``[app] app`` names ``make_app`` here.
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

MODULE = Path(__file__).resolve().parent / "goginapp"
BINARY_ENV = "LATTICE_GOGINAPP_BIN"
USERS = {"ann": ("ann", "pw-ann"), "ben": ("ben", "pw-ben")}
BUILD_TIMEOUT_S = 600


def _source_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(MODULE.rglob("*")):
        if p.is_file() and (p.suffix == ".go" or p.name in ("go.mod", "go.sum")):
            h.update(p.relative_to(MODULE).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def binary() -> Path:
    """The built service: ``$LATTICE_GOGINAPP_BIN``, else built (once per source hash) into
    the system temp directory. Raises ``RuntimeError`` with ``go``'s output when the build
    fails (no Go toolchain, or the module cannot be downloaded)."""
    if env := os.environ.get(BINARY_ENV):
        return Path(env)
    out = Path(tempfile.gettempdir()) / f"lattice-goginapp-{_source_hash()}" / "goginapp"
    if out.is_file():
        return out
    go = shutil.which("go")
    if go is None:
        raise RuntimeError("goginapp: `go` is not on PATH")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".goginapp-{os.getpid()}")
    env = {**os.environ, "GOFLAGS": "-mod=readonly", "GOTOOLCHAIN": "auto"}
    proc = subprocess.run(  # noqa: S603 -- the fixture's own module
        [go, "build", "-trimpath", "-buildvcs=false", "-o", str(tmp), "."],
        cwd=MODULE, capture_output=True, text=True, check=False, env=env,
        timeout=BUILD_TIMEOUT_S,
    )  # fmt: skip
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"goginapp: go build failed:\n{proc.stderr.strip()}")
    os.replace(tmp, out)
    return out


def make_app(db_path: Path) -> ProcessApp:
    """The Go service on ``db_path`` (the schema is created by the service: AutoMigrate)."""
    return ProcessApp([str(binary())], db_path=db_path, health_path="/health")


RUNTIME = process_runtime(framework="go", auth=AuthDriver.json("/login", USERS))
LOGIN = RUNTIME.auth.login

DDL = """
CREATE TABLE users (id integer PRIMARY KEY AUTOINCREMENT, name text NOT NULL, password text NOT NULL,
                    token text NOT NULL);
CREATE UNIQUE INDEX idx_users_name ON users(name);
CREATE UNIQUE INDEX idx_users_token ON users(token);
CREATE TABLE configs (id integer PRIMARY KEY AUTOINCREMENT, name text NOT NULL);
CREATE TABLE runs (id integer PRIMARY KEY AUTOINCREMENT, config_id integer NOT NULL,
                   user_id integer NOT NULL, status text NOT NULL DEFAULT 'queued');
"""  # noqa: E501


def build_db(path: Path) -> Path:
    """A seeded database by hand (the harness' own seed; the import uses the Seeder's)."""
    path = Path(path)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        for i, (handle, (name, pw)) in enumerate(USERS.items(), 1):
            conn.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (i, name, pw, f"tok-{handle}"))
        conn.execute("INSERT INTO configs VALUES (1, 'nightly')")
        conn.commit()
    finally:
        conn.close()
    return path
