"""The harness of the sessions app (``tests/grpc/fixtures/sessions``): how a check starts it.

``make_app(db_path)`` answers a :class:`~lattice.spec.process.ProcessApp` for the gRPC server on a
throwaway SQLite file (``ready="grpc"``). Callers log in by the rpc ``sessions.v1.Sessions/Login``;
``SignOut`` revokes the caller's session and ``DeleteMe`` deletes the caller's account, so what a
later call answers depends on what ran before it (the state reconcile must not depend on).
The binary is built once per source hash with ``go build`` (pure Go, no cgo).
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
PROTOS = (str(HERE / "proto/sessions/v1/sessions.proto"),)
PROTO_PATHS = (str(HERE / "proto"),)


def _cache() -> Path:
    base = os.environ.get("LATTICE_GRPCBRIDGE_CACHE") or str(
        Path.home() / ".cache/lattice/grpcbridge"
    )
    return Path(base) / "sessionsapp"


def source_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(HERE.rglob("*")):
        if p.suffix in (".go", ".mod", ".sum") and p.is_file():
            h.update(p.relative_to(HERE).as_posix().encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def binary() -> Path:
    """The sessions server, built once per source hash (``$LATTICE_GRPC_SESSIONS_BIN`` wins)."""
    if given := os.environ.get("LATTICE_GRPC_SESSIONS_BIN"):
        return Path(given)
    out = _cache() / source_hash()[:20] / "sessionsapp"
    if out.is_file():
        return out
    go = shutil.which("go")
    if go is None:
        raise RuntimeError("the sessions app needs the Go toolchain")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".sessionsapp-{os.getpid()}")
    proc = subprocess.run(  # noqa: S603
        [go, "build", "-trimpath", "-buildvcs=false", "-o", str(tmp), "."],
        cwd=HERE,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CGO_ENABLED": "0", "GOFLAGS": "-mod=readonly"},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"go build of the sessions app failed:\n{proc.stderr.strip()}")
    os.replace(tmp, out)
    return out


def make_app(db_path: Path) -> ProcessApp:
    """The gRPC server on ``db_path`` (not started)."""
    return ProcessApp(
        [str(binary())],
        db_path=db_path,
        env={},
        port_env="PORT",
        db_env="DB_PATH",
        ready="grpc",
        protos=PROTOS,
        proto_paths=PROTO_PATHS,
        startup_timeout=20.0,
    )


RUNTIME = process_runtime(
    framework="go", auth=AuthDriver.rpc("sessions.v1.Sessions", "Login", USERS)
)

DDL = """
CREATE TABLE users (id integer PRIMARY KEY AUTOINCREMENT, name text NOT NULL,
                    password text NOT NULL);
CREATE UNIQUE INDEX idx_users_name ON users(name);
CREATE TABLE sessions (token text PRIMARY KEY, user_id integer NOT NULL);
CREATE INDEX idx_sessions_user_id ON sessions(user_id);
"""


def build_db(path: Path) -> Path:
    """The seeded database: the two tables and the users ann and ben (no session)."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        for name, password in USERS.values():
            conn.execute("INSERT INTO users (name, password) VALUES (?, ?)", (name, password))
        conn.commit()
    finally:
        conn.close()
    return path
