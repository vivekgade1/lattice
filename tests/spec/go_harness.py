"""The harness a project writes to check a compiled (non-Python) service: the toy Go app of
``tests/spec/goapp`` (see its ``main.go``). It is also what the CLI test runs::

    python -m lattice.spec check --spec tests.spec.go_harness:SPEC \\
        --app tests.spec.go_harness:make_app --db tests.spec.go_harness:build_db \\
        --runtime tests.spec.go_harness:RUNTIME --layers DCX

``make_app(db_path)`` answers a :class:`~lattice.spec.process.ProcessApp` (the binary,
not started); ``RUNTIME`` starts it on a free port with ``PORT`` and ``DB_PATH`` set,
logs callers in through its ``POST /login`` and stops it after every app.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from lattice.importer.transport import AuthDriver
from lattice.profile import ApiProfile, Profile, RoleColumn
from lattice.spec import Invariant, Machine, Scenario, Spec, Step
from lattice.spec.process import ProcessApp, process_runtime
from lattice.typesys import Scalar, Schema

S = Scalar
USERS = {"ann": ("ann", "pw-ann"), "ben": ("ben", "pw-ben")}
BINARY_ENV = "LATTICE_GOAPP_BIN"  # the built binary (the test fixture sets it)


def make_app(db_path: Path) -> ProcessApp:
    """The Go service on ``db_path``; ``GOAPP_MODE`` / ``GOAPP_BUG`` pick a variant."""
    env = {"MODE": os.environ.get("GOAPP_MODE", "safe"), "BUG": os.environ.get("GOAPP_BUG", "")}
    return ProcessApp([os.environ[BINARY_ENV]], db_path=db_path, env=env, health_path="/health")


RUNTIME = process_runtime(framework="go", auth=AuthDriver.json("/login", USERS))

DDL = """
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, password TEXT NOT NULL,
                    token TEXT NOT NULL);
CREATE TABLE configs (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE runs (id INTEGER PRIMARY KEY, config_id INTEGER NOT NULL REFERENCES configs(id),
                   status TEXT NOT NULL);
"""


def build_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        for i, (h, (name, pw)) in enumerate(USERS.items(), 1):
            conn.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (i, name, pw, f"tok-{h}"))
        conn.execute("INSERT INTO configs VALUES (1, 'nightly')")
        conn.commit()
    finally:
        conn.close()
    return path


USER = Schema.from_pairs(
    [("id", S.INT), ("name", S.STR), ("password", S.STR), ("token", S.STR)],
    keys=[("id",), ("token",)],
)
CONFIG = Schema.from_pairs([("id", S.INT), ("name", S.STR)], keys=[("id",)])
RUN = Schema.from_pairs(
    [("id", S.INT), ("config_id", S.INT), ("status", S.STR)],
    keys=[("id",)],
    one_of={"status": ("queued", "cancelled")},
)
PROFILE = Profile(
    schemas={},
    api=ApiProfile(
        tables={"User": USER, "Config": CONFIG, "Run": RUN},
        table_names={"User": "users", "Config": "configs", "Run": "runs"},
        roles=frozenset({"Public", "Member"}),
        users_table="User",
        roles_from=RoleColumn("name"),
        error_body="fastapi",
        unauthorized_detail="Not authenticated",
    ),
)
ONE_LIVE = Invariant(
    "one_live_run_per_config",
    "A config has at most one queued run.",
    'count(Run, by="config_id", where=Run.status == "queued") <= 1',
)
SPEC = Spec(
    profile=PROFILE,
    invariants=(ONE_LIVE,),
    machines=(Machine("Run", "status", {"queued": ("cancelled",)}, initial=("queued",)),),
    scenarios=(
        Scenario(
            "one_live_run_per_config",
            steps=(
                Step("POST", "/configs/1/runs", "ann", expect_status=201),
                Step("POST", "/configs/1/runs", "ben", expect_status=409),
                Step(
                    "POST",
                    "/runs/1/cancel",
                    "ben",
                    expect_body={"id": 1, "status": "cancelled"},
                    expect_db={"Run": {"where": {"id": 1}, "equals": {"status": "cancelled"}}},
                ),
                Step("POST", "/configs/1/runs", "ben", expect_status=201),
            ),
        ),
        Scenario(
            "a_run_is_read",
            steps=(
                Step("POST", "/configs/1/runs", "ann", expect_status=201),
                Step("GET", "/runs/1", "ann", expect_body={"id": 1, "status": "queued"}),
                Step("GET", "/runs/1", expect_status=401),
            ),
        ),
    ),
    users=USERS,
)
