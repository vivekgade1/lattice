"""Harness of the ledger5 stand-in app, written only from HARNESS_API.md (Appendix A)."""

import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from lattice.importer.transport import AuthDriver
from lattice.spec.process import ProcessApp, process_runtime

ROOT = Path(__file__).resolve().parent
USERS = {"ann": ("ann", "pw-ann-1"), "bob": ("bob", "pw-bob-2")}
AUTH = AuthDriver.rpc(
    "ledger.v1.Accounts",
    "Login",
    USERS,
    username_field="name",
    password_field="password",
    token_field="token",
)
RUNTIME = process_runtime(framework="go", auth=AUTH)


def build_db(path):
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE users (name TEXT PRIMARY KEY, password TEXT NOT NULL);
        CREATE TABLE sessions (token TEXT PRIMARY KEY, user_name TEXT NOT NULL);
        CREATE TABLE entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            owner TEXT NOT NULL,
            state TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT ''
        );
        """
    )
    con.executemany("INSERT INTO users VALUES (?, ?)", [v for v in USERS.values()])
    con.execute("INSERT INTO entries (title, owner, state) VALUES ('seed-entry', 'ann', 'OPEN')")
    con.commit()
    con.close()
    return path


def make_app(db_path):
    out = Path(tempfile.mkdtemp(prefix="ledgerd-")) / "ledgerd"
    subprocess.run(
        [shutil.which("go"), "build", "-o", str(out), "./cmd/ledgerd"], cwd=ROOT, check=True
    )
    return ProcessApp(
        [str(out)],
        db_path=str(db_path),
        ready="grpc",
        protos=["ledger/v1/ledger.proto"],
        proto_paths=["."],
        proto_root=str(ROOT / "proto"),
    )
