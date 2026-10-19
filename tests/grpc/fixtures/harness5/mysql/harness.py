"""Harness of the ledger5 MySQL stand-in app, written only from HARNESS_API.md (Appendix A, A.5)."""

import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import pymysql

from lattice.importer.transport import AuthDriver
from lattice.spec.db import url_for
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

SCHEMA = [
    "CREATE TABLE users (name VARCHAR(64) PRIMARY KEY, password VARCHAR(64) NOT NULL)",
    "CREATE TABLE sessions (token VARCHAR(64) PRIMARY KEY, user_name VARCHAR(64) NOT NULL)",
    """CREATE TABLE entries (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        title VARCHAR(200) NOT NULL,
        owner VARCHAR(64) NOT NULL,
        state VARCHAR(16) NOT NULL,
        tags VARCHAR(500) NOT NULL DEFAULT ''
    )""",
]


def build_db(db):
    u = urlparse(db)
    con = pymysql.connect(
        host=u.hostname,
        port=u.port or 3306,
        user=unquote(u.username or ""),
        password=unquote(u.password or ""),
        database=u.path.lstrip("/"),
    )
    with con.cursor() as cur:
        for ddl in SCHEMA:
            cur.execute(ddl)
        cur.executemany("INSERT INTO users VALUES (%s, %s)", list(USERS.values()))
        cur.execute(
            "INSERT INTO entries (title, owner, state) VALUES ('seed-entry', 'ann', 'OPEN')"
        )
    con.commit()
    con.close()
    return db


def make_app(db):
    out = Path(tempfile.mkdtemp(prefix="ledgerd-")) / "ledgerd"
    subprocess.run(
        [shutil.which("go"), "build", "-o", str(out), "./cmd/ledgerd"], cwd=ROOT, check=True
    )
    return ProcessApp(
        [str(out)],
        db_path=db,
        env={"LEDGER_DSN": url_for(db, "go-mysql")},
        ready="grpc",
        protos=["ledger/v1/ledger.proto"],
        proto_paths=["."],
        proto_root=str(ROOT / "proto"),
    )
