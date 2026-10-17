"""The harness of the notes app (``tests/grpc/fixtures/notes``): how a check starts it.

``make_app(db_path)`` answers a :class:`~lattice.spec.process.ProcessApp` for the gRPC
server on a throwaway SQLite file (``ready="grpc"``: ``Health/Check`` answers SERVING);
``make_mixed_app`` also serves the grpc-gateway on a second port. ``BUG=<name>`` (the
``NOTES_BUG`` environment variable of the check) picks one of the eight deliberate faults.
The binary is built once per source hash with ``go build`` (pure Go, no cgo)::

    python -m lattice.spec check --spec tests.grpc.fixtures.notes.spec:SPEC \\
        --app tests.grpc.fixtures.notes.harness:make_app \\
        --db tests.grpc.fixtures.notes.harness:build_db \\
        --runtime tests.grpc.fixtures.notes.harness:RUNTIME --layers CD
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

from lattice.importer.transport import AuthDriver
from lattice.spec.process import ProcessApp, process_runtime

HERE = Path(__file__).resolve().parent
USERS = {"ann": ("ann", "pw-ann1"), "ben": ("ben", "pw-ben1")}
BUGS = (
    "no_owner",
    "publish_any_state",
    "delete_published",
    "list_all",
    "dup_code",
    "no_priority_check",
    "update_archived",
    "publish_limit_off",
)
PROTOS = (
    str(HERE / "proto/notes/v1/accounts.proto"),
    str(HERE / "proto/notes/v1/notes.proto"),
)
PROTO_PATHS = (str(HERE / "proto"),)


def _cache() -> Path:
    base = os.environ.get("LATTICE_GRPCBRIDGE_CACHE") or str(
        Path.home() / ".cache/lattice/grpcbridge"
    )
    return Path(base) / "notesapp"


def source_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(HERE.rglob("*")):
        if p.suffix in (".go", ".mod", ".sum") and p.is_file():
            h.update(p.relative_to(HERE).as_posix().encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def binary() -> Path:
    """The notes server, built once per source hash (``$LATTICE_GRPC_NOTES_BIN`` wins)."""
    if given := os.environ.get("LATTICE_GRPC_NOTES_BIN"):
        return Path(given)
    out = _cache() / source_hash()[:20] / "notesapp"
    if out.is_file():
        return out
    go = shutil.which("go")
    if go is None:
        raise RuntimeError("the notes app needs the Go toolchain")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".notesapp-{os.getpid()}")
    proc = subprocess.run(  # noqa: S603
        [go, "build", "-trimpath", "-buildvcs=false", "-o", str(tmp), "."],
        cwd=HERE,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CGO_ENABLED": "0", "GOFLAGS": "-mod=readonly"},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"go build of the notes app failed:\n{proc.stderr.strip()}")
    os.replace(tmp, out)
    return out


def make_app(db_path: Path, *, bug: str | None = None) -> ProcessApp:
    """The gRPC server on ``db_path``; ``bug`` (default ``$NOTES_BUG``) a deliberate fault."""
    env = {"BUG": os.environ.get("NOTES_BUG", "") if bug is None else bug}
    return ProcessApp(
        [str(binary())],
        db_path=db_path,
        env=env,
        port_env="PORT",
        db_env="DB_PATH",
        ready="grpc",
        protos=PROTOS,
        proto_paths=PROTO_PATHS,
        startup_timeout=20.0,
    )


def make_mixed_app(db_path: Path, *, bug: str | None = None) -> ProcessApp:
    """The same server with the grpc-gateway on a second port (``HTTP_PORT``): ``port`` is
    the HTTP port, ``grpc_port`` the gRPC one."""
    env = {"BUG": os.environ.get("NOTES_BUG", "") if bug is None else bug}
    return ProcessApp(
        [str(binary())],
        db_path=db_path,
        env=env,
        port_env="HTTP_PORT",
        grpc_port_env="PORT",
        db_env="DB_PATH",
        ready="grpc",
        protos=PROTOS,
        proto_paths=PROTO_PATHS,
        startup_timeout=20.0,
    )


def bugged(bug: str):
    """An app factory for the variant ``bug``."""

    def factory(db_path: Path) -> ProcessApp:
        return make_app(db_path, bug=bug)

    return factory


RUNTIME = process_runtime(framework="go", auth=AuthDriver.rpc("notes.v1.Accounts", "Login", USERS))

DDL = """
CREATE TABLE users (id integer PRIMARY KEY AUTOINCREMENT, name text NOT NULL,
                    password text NOT NULL, token text NOT NULL);
CREATE UNIQUE INDEX idx_users_name ON users(name);
CREATE UNIQUE INDEX idx_users_token ON users(token);
CREATE TABLE notes (id integer PRIMARY KEY AUTOINCREMENT, user_id integer NOT NULL,
                    title text NOT NULL, body text, state text NOT NULL, priority integer,
                    client_rev integer);
CREATE INDEX idx_notes_user_id ON notes(user_id);
"""


def build_db(path: Path) -> Path:
    """The seeded database: the two tables and the users ann and ben."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        for name, password in USERS.values():
            conn.execute(
                "INSERT INTO users (name, password, token) VALUES (?, ?, ?)",
                (name, password, f"tok-{name}"),
            )
        conn.commit()
    finally:
        conn.close()
    return path
