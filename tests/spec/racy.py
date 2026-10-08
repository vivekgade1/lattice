"""Toy FastAPI + SQLite apps for layer X (the concurrency layer) and their specs.

**Runs** (:func:`runs_app`): job configs and their runs. ``POST /configs/{cid}/runs``
queues a run, ``POST /runs/{rid}/cancel`` cancels a live run, ``POST /worker/drain``
claims the oldest queued run, "executes" it (a short sleep) and finishes it. The spec
(:data:`RUNS_SPEC`) says a config has at most one live run (a count bound) and runs move
along ``Run.status``'s machine. Two switches:

* ``safe_create``: False -- check-then-insert with a sleep between the check and the
  insert (two concurrent creates both pass the check); True -- a partial unique index on
  the live runs, and its ``IntegrityError`` answered 409.
* ``safe_finish``: False -- the worker writes ``succeeded`` whatever the run's status is
  now (it overwrites a cancel that came in while it ran); True -- a conditional ``UPDATE
  ... WHERE status = 'running'``.

**Notes** (:func:`notes_app`): each caller's own notes (no shared rows); a note is a draft
until published. Sequentially and concurrently correct.

Every app opens one SQLite connection per request (``check_same_thread=False``), so
several app instances can share one database file.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from lattice.profile import ApiProfile, Profile, RoleColumn
from lattice.spec import Invariant, Machine, Scenario, Spec, Step
from lattice.typesys import Scalar, Schema

S = Scalar
USERS = {"ann": ("ann", "pw-ann"), "ben": ("ben", "pw-ben")}
USER_IDS = {"ann": 1, "ben": 2}
LIVE = ("queued", "running")


def login(client: Any, handle: str) -> str:
    """The bearer token of ``handle`` (tokens are stored in ``users.token``)."""
    return f"tok-{handle}"


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=10, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _caller(conn: sqlite3.Connection, authorization: str | None) -> int:
    scheme, _, token = (authorization or "").partition(" ")
    row = conn.execute("SELECT id FROM users WHERE token = ?", (token,)).fetchone()
    if scheme.lower() != "bearer" or row is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return int(row["id"])


# ---- runs ---------------------------------------------------------------------------------

RUNS_DDL = """
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, token TEXT NOT NULL);
CREATE TABLE configs (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE runs (id INTEGER PRIMARY KEY, config_id INTEGER NOT NULL REFERENCES configs(id),
                   status TEXT NOT NULL);
"""


def build_runs_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(RUNS_DDL)
        for h, i in USER_IDS.items():
            conn.execute("INSERT INTO users VALUES (?, ?, ?)", (i, h, f"tok-{h}"))
        conn.execute("INSERT INTO configs VALUES (1, 'nightly')")
        conn.execute("INSERT INTO configs VALUES (2, 'hourly')")
        conn.commit()
    finally:
        conn.close()
    return path


def _run(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "config_id": row["config_id"], "status": row["status"]}


def runs_app(*, safe_create: bool = True, safe_finish: bool = True, work_s: float = 0.05):
    """The runs app factory ``(db_path) -> FastAPI`` (see the module docstring)."""

    def make_app(db_path: Path) -> FastAPI:
        db = Path(db_path)
        app = FastAPI()
        if safe_create:
            conn = _connect(db)
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS one_live_run ON runs(config_id) "
                    "WHERE status IN ('queued', 'running')"
                )
            finally:
                conn.close()

        @app.post("/configs/{cid}/runs", status_code=201)
        def create_run(cid: int, authorization: str | None = Header(None)) -> Any:
            conn = _connect(db)
            try:
                _caller(conn, authorization)
                if conn.execute("SELECT 1 FROM configs WHERE id = ?", (cid,)).fetchone() is None:
                    raise HTTPException(status_code=404, detail="Not found")
                if safe_create:
                    try:
                        cur = conn.execute(
                            "INSERT INTO runs (config_id, status) VALUES (?, 'queued')", (cid,)
                        )
                    except sqlite3.IntegrityError:
                        raise HTTPException(status_code=409, detail="already live") from None
                else:
                    live = conn.execute(
                        "SELECT COUNT(*) FROM runs WHERE config_id = ? AND status IN "
                        "('queued', 'running')",
                        (cid,),
                    ).fetchone()[0]
                    if live:
                        raise HTTPException(status_code=409, detail="already live")
                    time.sleep(0.01)  # widen the check-then-insert window
                    cur = conn.execute(
                        "INSERT INTO runs (config_id, status) VALUES (?, 'queued')", (cid,)
                    )
                row = conn.execute("SELECT * FROM runs WHERE id = ?", (cur.lastrowid,)).fetchone()
                return _run(row)
            finally:
                conn.close()

        @app.post("/runs/{rid}/cancel")
        def cancel_run(rid: int, authorization: str | None = Header(None)) -> Any:
            conn = _connect(db)
            try:
                _caller(conn, authorization)
                time.sleep(0.01)  # widen the window: the worker claims first
                cur = conn.execute(
                    "UPDATE runs SET status = 'cancelled' WHERE id = ? AND status IN "
                    "('queued', 'running')",
                    (rid,),
                )
                row = conn.execute("SELECT * FROM runs WHERE id = ?", (rid,)).fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail="Not found")
                if cur.rowcount == 0:
                    raise HTTPException(status_code=409, detail="already final")
                return _run(row)
            finally:
                conn.close()

        @app.get("/runs/{rid}")
        def read_run(rid: int, authorization: str | None = Header(None)) -> Any:
            conn = _connect(db)
            try:
                _caller(conn, authorization)
                row = conn.execute("SELECT * FROM runs WHERE id = ?", (rid,)).fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail="Not found")
                return _run(row)
            finally:
                conn.close()

        @app.post("/worker/drain")
        def drain(authorization: str | None = Header(None)) -> Any:
            conn = _connect(db)
            try:
                _caller(conn, authorization)
                row = conn.execute(
                    "SELECT id FROM runs WHERE status = 'queued' ORDER BY id LIMIT 1"
                ).fetchone()
                if row is None:
                    return {"ran": None}
                rid = row["id"]
                claimed = conn.execute(
                    "UPDATE runs SET status = 'running' WHERE id = ? AND status = 'queued'",
                    (rid,),
                ).rowcount
                if not claimed:
                    return {"ran": None}
                time.sleep(work_s)  # the job runs
                if safe_finish:
                    conn.execute(
                        "UPDATE runs SET status = 'succeeded' WHERE id = ? AND status = 'running'",
                        (rid,),
                    )
                else:
                    conn.execute("UPDATE runs SET status = 'succeeded' WHERE id = ?", (rid,))
                done = conn.execute("SELECT * FROM runs WHERE id = ?", (rid,)).fetchone()
                return _run(done)
            finally:
                conn.close()

        return app

    return make_app


USER = Schema.from_pairs(
    [("id", S.INT), ("name", S.STR), ("token", S.STR)], keys=[("id",), ("token",)]
)
CONFIG = Schema.from_pairs([("id", S.INT), ("name", S.STR)], keys=[("id",)])
RUN = Schema.from_pairs(
    [("id", S.INT), ("config_id", S.INT), ("status", S.STR)],
    keys=[("id",)],
    one_of={"status": ("queued", "running", "succeeded", "cancelled")},
)


def _profile(tables: dict[str, Schema], names: dict[str, str]) -> Profile:
    api = ApiProfile(
        tables={"User": USER, **tables},
        table_names={"User": "users", **names},
        roles=frozenset({"Public", "Member"}),
        users_table="User",
        roles_from=RoleColumn("name"),
        error_body="fastapi",
        unauthorized_detail="Not authenticated",
    )
    return Profile(schemas={}, api=api)


RUNS_PROFILE = _profile({"Config": CONFIG, "Run": RUN}, {"Config": "configs", "Run": "runs"})

RUN_MACHINE = Machine(
    "Run",
    "status",
    {"queued": ("running", "cancelled"), "running": ("succeeded", "cancelled")},
    initial=("queued",),
)
ONE_LIVE = Invariant(
    "one_live_run_per_config",
    "A config has at most one queued or running run.",
    'count(Run, by="config_id", where=Run.status in ("queued", "running")) <= 1',
)

CREATE = Step("POST", "/configs/1/runs", "ann", expect_status=201)
DRAIN = Step("POST", "/worker/drain", "ann", expect_status=200)

RUNS_SCENARIOS = (
    Scenario(
        "one_live_run_per_config",
        steps=(
            CREATE,
            Step("POST", "/configs/1/runs", "ben", expect_status=409),
            Step(
                "POST",
                "/runs/1/cancel",
                "ben",
                expect_status=200,
                expect_body={"id": 1, "status": "cancelled"},
            ),
            Step("POST", "/configs/1/runs", "ben", expect_status=201),
        ),
    ),
    Scenario(
        "the_worker_runs_a_queued_run",
        steps=(
            CREATE,
            DRAIN,
            Step("GET", "/runs/1", "ann", expect_body={"id": 1, "status": "succeeded"}),
        ),
    ),
    Scenario(
        "a_cancelled_run_is_never_executed",
        steps=(
            CREATE,
            Step("POST", "/runs/1/cancel", "ann", expect_status=200),
            DRAIN,
            Step("GET", "/runs/1", "ann", expect_body={"id": 1, "status": "cancelled"}),
        ),
    ),
)

RUNS_SPEC = Spec(
    profile=RUNS_PROFILE,
    machines=(RUN_MACHINE,),
    invariants=(ONE_LIVE,),
    scenarios=RUNS_SCENARIOS,
    users=USERS,
)


# ---- notes --------------------------------------------------------------------------------

NOTES_DDL = """
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, token TEXT NOT NULL);
CREATE TABLE notes (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    title TEXT NOT NULL, state TEXT NOT NULL);
"""


def build_notes_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(NOTES_DDL)
        for h, i in USER_IDS.items():
            conn.execute("INSERT INTO users VALUES (?, ?, ?)", (i, h, f"tok-{h}"))
        conn.commit()
    finally:
        conn.close()
    return path


def _note(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "user_id": row["user_id"], "title": row["title"],
            "state": row["state"]}  # fmt: skip


def notes_app(db_path: Path) -> FastAPI:
    db = Path(db_path)
    app = FastAPI()

    @app.post("/notes", status_code=201)
    def create_note(body: dict[str, Any], authorization: str | None = Header(None)) -> Any:
        conn = _connect(db)
        try:
            uid = _caller(conn, authorization)
            cur = conn.execute(
                "INSERT INTO notes (user_id, title, state) VALUES (?, ?, 'draft')",
                (uid, str(body.get("title", ""))),
            )
            return _note(
                conn.execute("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone()
            )
        finally:
            conn.close()

    @app.post("/notes/{nid}/publish")
    def publish(nid: int, authorization: str | None = Header(None)) -> Any:
        conn = _connect(db)
        try:
            uid = _caller(conn, authorization)
            row = conn.execute(
                "SELECT * FROM notes WHERE id = ? AND user_id = ?", (nid, uid)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Not found")
            conn.execute(
                "UPDATE notes SET state = 'published' WHERE id = ? AND state = 'draft'", (nid,)
            )
            return _note(conn.execute("SELECT * FROM notes WHERE id = ?", (nid,)).fetchone())
        finally:
            conn.close()

    return app


NOTE = Schema.from_pairs(
    [("id", S.INT), ("user_id", S.INT), ("title", S.STR), ("state", S.STR)],
    keys=[("id",)],
    owner="user_id",
    one_of={"state": ("draft", "published")},
)

NOTES_SPEC = Spec(
    profile=_profile({"Note": NOTE}, {"Note": "notes"}),
    machines=(Machine("Note", "state", {"draft": ("published",)}, initial=("draft",)),),
    invariants=(
        Invariant("few_notes", "A user has at most 100 notes.", 'count(Note, by="user_id") <= 100'),
    ),
    scenarios=(
        Scenario(
            "a_note_is_published",
            steps=(
                Step("POST", "/notes", "ann", json={"title": "a"}, expect_status=201),
                Step("POST", "/notes/1/publish", "ann", expect_body={"state": "published"}),
                Step("POST", "/notes", "ben", json={"title": "b"}, expect_status=201),
            ),
        ),
    ),
    users=USERS,
)

# for the CLI tests
SAFE_APP = runs_app()
