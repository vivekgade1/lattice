"""Sub-project 1b node N3 (spec 2026-09-27 lattice-loops §3.4): `for_each(t, native.x,
args...)` with effect natives, through the transactional outbox.

* `for_each` writes one `lattice_outbox` row per input row in the endpoint's transaction
  (the table is created when missing);
* after COMMIT the rows are dispatched in order and marked `done`, or `failed` with the
  error; the response never depends on the dispatch;
* a rolled-back request (a declared error after the outbox insert, a UNIQUE conflict,
  a 500) dispatches nothing and leaves no outbox rows;
* `lattice.api.outbox.retry` (and `python -m lattice.api.outbox retry --db --natives`)
  re-dispatches failed rows: delivery is at-least-once.

Everything runs through the runtime and, request by request, through the generated
FastAPI app in process; the harness-level codegen differential runs the same program.
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from lattice.api import outbox
from lattice.api.codegen import generate_fastapi
from lattice.api.ir import ForEachStep, lower_api
from lattice.api.runtime import build_app
from lattice.check import check_source
from tests.api import effects, effects_natives
from tests.api.effects import NATIVES, NATIVES_FILE, PROFILE, PROGRAM, TOKENS, build_db

ROOT = Path(__file__).resolve().parents[2]
KIT_PATH = ROOT / "bench" / "api_kit" / "platform_kit.py"
_MODULES = ("platform_kit", "generated_effects", "natives")


def _h(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[who]}"}


def _plans():
    check = check_source(PROGRAM, PROFILE)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    return lower_api(check.program)


@pytest.fixture(autouse=True)
def _reset_natives() -> Iterator[None]:
    effects_natives.SENT.clear()
    effects_natives.FAIL_ALWAYS.clear()
    yield
    effects_natives.SENT.clear()
    effects_natives.FAIL_ALWAYS.clear()


@pytest.fixture(scope="module")
def pristine(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("effects") / "pristine.db"
    build_db(path)
    return path


@pytest.fixture
def db(pristine: Path, tmp_path: Path) -> Path:
    path = tmp_path / "app.db"
    shutil.copyfile(pristine, path)
    return path


@pytest.fixture
def client(db: Path) -> Iterator[TestClient]:
    with TestClient(build_app(_plans(), db, natives=NATIVES)) as c:
        yield c


def outbox_rows(db: Path) -> list[tuple] | None:
    """(endpoint, native, args, status, error, attempts, claimed) per outbox row, or None
    when the table does not exist (`claimed`: 1 when the row has a claimed_at)."""
    conn = sqlite3.connect(db)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'lattice_outbox'"
        ).fetchone()
        if exists is None:
            return None
        rows = conn.execute(effects.OUTBOX_AFTER).fetchall()
        return [(e, n, json.loads(a), s, err, k, c) for e, n, a, s, err, k, c in rows]
    finally:
        conn.close()


def args(types: list[str], values: list[object]) -> dict[str, object]:
    return {"types": types, "values": values}


# ---- the plan ------------------------------------------------------------------------------


def test_for_each_lowers_to_an_outbox_step():
    plans = {p.name: p for p in _plans()}
    signup = plans["signup"]
    steps = [s for s in signup.steps if isinstance(s, ForEachStep)]
    assert len(steps) == 1 and steps[0].native == "send_welcome"
    assert "lattice_outbox" in steps[0].sql.text
    # effect natives are dispatched after commit, never registered as SQL functions
    assert all(name != "send_welcome" for name, _ in signup.natives)
    assert [name for name, _ in signup.effects] == ["send_welcome"]


def test_build_app_needs_every_effect_native():
    natives = {k: v for k, v in NATIVES.items() if k != "item_done"}
    with pytest.raises(ValueError, match="item_done"):
        build_app(_plans(), Path("unused.db"), natives=natives)


# ---- dispatch after commit -----------------------------------------------------------------


def test_signup_dispatches_after_commit(client: TestClient, db: Path):
    r = client.post("/users/signup", json={"email": "new@example.com", "full_name": "New"})
    assert r.status_code == 201, r.text
    assert effects_natives.SENT == [("send_welcome", "new@example.com", "New")]
    assert outbox_rows(db) == [
        (
            "signup",
            "send_welcome",
            args(["Str", "Str"], ["new@example.com", "New"]),
            "done",
            None,
            1,
            1,
        )
    ]


def test_typed_arguments_reach_the_native(client: TestClient, db: Path):
    r = client.post("/items", json={"title": "fresh"}, headers=_h("alice"))
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]
    assert effects_natives.SENT == [("item_created", new_id, "fresh", False)]
    r = client.patch("/items/1/done", headers=_h("alice"))
    assert r.status_code == 200 and r.json()["done"] is True
    assert effects_natives.SENT[-1] == ("item_done", 1, dt.date(2000, 1, 5))
    r = client.patch("/items/2/done", headers=_h("alice"))  # a null Date argument
    assert r.status_code == 200
    assert effects_natives.SENT[-1] == ("item_done", 2, None)
    stored = outbox_rows(db)
    assert stored is not None
    assert [row[2] for row in stored] == [
        args(["Int", "Str", "Bool"], [new_id, "fresh", 0]),
        args(["Int", "Date"], [1, "2000-01-05"]),
        args(["Int", "Date"], [2, None]),
    ]
    assert {row[3] for row in stored} == {"done"}


def test_order_is_preserved(client: TestClient, db: Path):
    r = client.get("/items/recent", params={"note": "hi"}, headers=_h("alice"))
    assert r.status_code == 200, r.text
    assert [item["id"] for item in r.json()["items"]] == [4, 2, 1]
    assert effects_natives.SENT == [
        ("item_viewed", 4, "BOOM", "hi"),
        ("item_viewed", 2, "BETA", "hi"),
        ("item_viewed", 1, "ALPHA", "hi"),
    ]
    stored = outbox_rows(db)
    assert stored is not None
    assert [row[2]["values"] for row in stored] == [
        [4, "BOOM", "hi"],
        [2, "BETA", "hi"],
        [1, "ALPHA", "hi"],
    ]


def test_an_empty_input_writes_no_rows(client: TestClient, db: Path):
    r = client.get("/items/recent", headers=_h("carol"))
    assert r.status_code == 200 and r.json()["items"] == []
    assert effects_natives.SENT == []
    assert outbox_rows(db) == []


# ---- a failed dispatch ---------------------------------------------------------------------


def test_a_dispatch_failure_keeps_the_response_and_marks_the_row(client: TestClient, db: Path):
    ok = client.post("/items", json={"title": "fine"}, headers=_h("alice"))
    boom = client.post("/items", json={"title": "boom"}, headers=_h("alice"))
    assert ok.status_code == boom.status_code == 201
    assert boom.json()["title"] == "boom"
    stored = outbox_rows(db)
    assert stored is not None
    assert [(row[3], row[4], row[5]) for row in stored] == [
        ("done", None, 1),
        ("failed", "ValueError: boom refused", 1),
    ]
    # the item itself was committed
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM items WHERE title = 'boom'").fetchone() == (2,)
    finally:
        conn.close()


def test_a_later_row_is_dispatched_after_an_earlier_failure(client: TestClient, db: Path):
    effects_natives.FAIL_ALWAYS.add("item_viewed")
    r = client.get("/items/recent", headers=_h("alice"))
    assert r.status_code == 200
    stored = outbox_rows(db)
    assert stored is not None and [row[3] for row in stored] == ["failed"] * 3


# ---- rollback sends nothing ------------------------------------------------------------------


def test_a_declared_error_after_the_outbox_insert_sends_nothing(client: TestClient, db: Path):
    r = client.post("/items", json={"title": "forbidden"}, headers=_h("alice"))
    assert r.status_code == 400 and r.json()["detail"] == "title refused"
    assert effects_natives.SENT == []
    assert not outbox_rows(db)  # the table's creation was rolled back too
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone() == (4,)
    finally:
        conn.close()


def test_a_unique_conflict_sends_nothing(client: TestClient, db: Path):
    r = client.post("/users/signup", json={"email": "alice@example.com", "full_name": "A"})
    assert r.status_code == 409 and r.json()["detail"] == "email taken"
    assert effects_natives.SENT == []
    assert not outbox_rows(db)


def test_rows_of_earlier_requests_survive_a_rollback(client: TestClient, db: Path):
    client.post("/items", json={"title": "kept"}, headers=_h("alice"))
    client.post("/items", json={"title": "forbidden"}, headers=_h("alice"))
    stored = outbox_rows(db)
    assert stored is not None and [row[2]["values"][1] for row in stored] == ["kept"]
    assert [s[2] for s in effects_natives.SENT] == ["kept"]


def test_a_500_after_the_outbox_insert_sends_nothing(db: Path):
    program = PROGRAM + '''

@endpoint("POST", "/items/strict", auth=Customer)
def create_strict(title: Str(max_len=50), user: CurrentUser) -> Created | NotFound:
    made = insert(Items, owner=user, title=title, done=False)
    for_each(made, native.item_created, col("id"), col("title"), col("done"))
    items = fetch(Items)
    mine = owned(items, by=user)
    only = one(mine, else_=NotFound("no items"))
    return made
'''
    check = check_source(program, PROFILE)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    with TestClient(build_app(lower_api(check.program), db, natives=NATIVES)) as c:
        r = c.post("/items/strict", json={"title": "x"}, headers=_h("bob"))  # bob: 2 items
        assert r.status_code == 500 and "R310" in r.json()["detail"]
        assert effects_natives.SENT == [] and not outbox_rows(db)
        r = c.post("/items/strict", json={"title": "y"}, headers=_h("carol"))  # carol: 1 item
        assert r.status_code == 201
        assert effects_natives.SENT == [("item_created", r.json()["id"], "y", False)]


# ---- retry -------------------------------------------------------------------------------------


def test_retry_redelivers_failed_rows(client: TestClient, db: Path):
    effects_natives.FAIL_ALWAYS.add("item_done")
    assert client.patch("/items/1/done", headers=_h("alice")).status_code == 200
    assert client.post("/items", json={"title": "boom"}, headers=_h("alice")).status_code == 201
    stored = outbox_rows(db)
    assert stored is not None
    assert [(row[1], row[3], row[4]) for row in stored] == [
        ("item_done", "failed", "RuntimeError: item_done is down"),
        ("item_created", "failed", "ValueError: boom refused"),
    ]
    effects_natives.FAIL_ALWAYS.clear()
    report = outbox.retry(db, NATIVES)
    assert (report.done, report.failed) == (1, 1)
    assert effects_natives.SENT == [("item_done", 1, dt.date(2000, 1, 5))]
    stored = outbox_rows(db)
    assert stored is not None
    assert [(row[3], row[4], row[5]) for row in stored] == [
        ("done", None, 2),
        ("failed", "ValueError: boom refused", 2),
    ]
    # done rows are never re-sent
    report = outbox.retry(db, NATIVES)
    assert (report.done, report.failed) == (0, 1)
    assert len(effects_natives.SENT) == 1


def test_retry_without_an_outbox_table_does_nothing(db: Path):
    report = outbox.retry(db, NATIVES)
    assert (report.done, report.failed) == (0, 0)


def test_retry_marks_a_missing_implementation_failed(client: TestClient, db: Path):
    effects_natives.FAIL_ALWAYS.add("send_welcome")
    client.post("/users/signup", json={"email": "n@example.com", "full_name": "N"})
    report = outbox.retry(db, {})
    assert (report.done, report.failed) == (0, 1)
    stored = outbox_rows(db)
    assert stored is not None and stored[0][4] == "no implementation for native 'send_welcome'"


def test_the_retry_command(client: TestClient, db: Path):
    effects_natives.FAIL_ALWAYS.add("send_welcome")
    client.post("/users/signup", json={"email": "n@example.com", "full_name": "N"})
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "lattice.api.outbox",
            "retry",
            "--db",
            str(db),
            "--natives",
            "tests.api.effects_natives",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert "1 done, 0 failed" in out.stdout
    stored = outbox_rows(db)
    assert stored is not None and stored[0][3:6] == ("done", None, 2)


def test_the_retry_command_fails_when_a_row_still_fails(client: TestClient, db: Path):
    client.post("/items", json={"title": "boom"}, headers=_h("alice"))
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "lattice.api.outbox",
            "retry",
            "--db",
            str(db),
            "--natives",
            "tests.api.effects_natives",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 1
    assert "0 done, 1 failed" in out.stdout


# ---- claims, lost marks and the grace period ----------------------------------------------

LATER = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=outbox.GRACE_SECONDS + 60)


def _statuses(db: Path) -> list[tuple[str, int, int]]:
    stored = outbox_rows(db)
    assert stored is not None
    return [(row[3], row[5], row[6]) for row in stored]


def test_rows_are_claimed_before_they_are_sent(client: TestClient, db: Path):
    assert client.patch("/items/1/done", headers=_h("alice")).status_code == 200
    conn = sqlite3.connect(db)
    try:
        (claimed_at,) = conn.execute("SELECT claimed_at FROM lattice_outbox").fetchone()
    finally:
        conn.close()
    assert claimed_at is not None and claimed_at.endswith("Z")
    assert _statuses(db) == [("done", 1, 1)]


def test_a_lost_mark_is_retried_after_the_grace_period(
    client: TestClient, db: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(outbox, "MARK_DONE_SQL", "UPDATE no_such_table SET x = ?, y = ?")
    monkeypatch.setattr(outbox, "MARK_BACKOFF", 0.0)
    with caplog.at_level("WARNING", logger="lattice.outbox"):
        assert client.patch("/items/1/done", headers=_h("alice")).status_code == 200
    assert effects_natives.SENT == [("item_done", 1, dt.date(2000, 1, 5))]
    # sent, but the done mark was lost: the row stays claimed (in flight), and it is logged
    assert _statuses(db) == [("dispatching", 1, 1)]
    assert any("could not mark outbox row" in r.getMessage() for r in caplog.records)
    monkeypatch.undo()
    # a fresh claim is in flight: the default retry leaves it alone
    report = outbox.retry(db, NATIVES)
    assert (report.done, report.failed) == (0, 0)
    assert len(effects_natives.SENT) == 1
    # after the grace period the row is re-sent (at-least-once) and marked done
    report = outbox.retry(db, NATIVES, now=LATER)
    assert (report.done, report.failed) == (1, 0)
    assert len(effects_natives.SENT) == 2
    assert _statuses(db) == [("done", 2, 1)]


def test_a_mark_that_fails_once_is_retried(client: TestClient, db: Path, monkeypatch):
    monkeypatch.setattr(outbox, "MARK_BACKOFF", 0.0)
    effects_natives.FAIL_ALWAYS.add("item_done")
    assert client.patch("/items/1/done", headers=_h("alice")).status_code == 200
    effects_natives.FAIL_ALWAYS.clear()

    class Flaky:
        """A connection whose first done mark fails (e.g. the database is locked)."""

        def __init__(self, conn: sqlite3.Connection) -> None:
            self.conn = conn
            self.failed = False

        def execute(self, sql: str, params: tuple = ()):
            if sql == outbox.MARK_DONE_SQL and not self.failed:
                self.failed = True
                raise sqlite3.OperationalError("database is locked")
            return self.conn.execute(sql, params)

    conn = sqlite3.connect(db, isolation_level=None)
    try:
        rows = conn.execute(outbox.RETRY_SQL, (LATER.isoformat(),)).fetchall()
        flaky = Flaky(conn)
        assert outbox.dispatch(flaky, rows, NATIVES) == (1, 0)
        assert flaky.failed
    finally:
        conn.close()
    assert _statuses(db) == [("done", 2, 1)]


def test_retry_leaves_fresh_pending_and_in_flight_rows_alone(client: TestClient, db: Path):
    assert client.patch("/items/1/done", headers=_h("alice")).status_code == 200
    assert client.patch("/items/2/done", headers=_h("alice")).status_code == 200
    now = dt.datetime.now(dt.UTC).isoformat()
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        # row 1: written, never claimed (its request is about to dispatch it)
        conn.execute(
            "UPDATE lattice_outbox SET status = 'pending', claimed_at = NULL, attempts = 0, "
            "created_at = ? WHERE id = 1",
            (now,),
        )
        # row 2: claimed just now and being sent
        conn.execute(
            "UPDATE lattice_outbox SET status = 'dispatching', claimed_at = ? WHERE id = 2",
            (now,),
        )
    finally:
        conn.close()
    effects_natives.SENT.clear()
    report = outbox.retry(db, NATIVES)
    assert (report.done, report.failed) == (0, 0)
    assert effects_natives.SENT == []
    # --pending takes the pending row whatever its age, never the in-flight one
    report = outbox.retry(db, NATIVES, pending=True)
    assert (report.done, report.failed) == (1, 0)
    assert effects_natives.SENT == [("item_done", 1, dt.date(2000, 1, 5))]
    # both are stale later on
    report = outbox.retry(db, NATIVES, now=LATER)
    assert (report.done, report.failed) == (1, 0)
    assert effects_natives.SENT[-1] == ("item_done", 2, None)


def test_claiming_prevents_a_double_dispatch(client: TestClient, db: Path):
    effects_natives.FAIL_ALWAYS.add("item_done")
    assert client.patch("/items/1/done", headers=_h("alice")).status_code == 200
    effects_natives.FAIL_ALWAYS.clear()
    first = sqlite3.connect(db, isolation_level=None)
    second = sqlite3.connect(db, isolation_level=None)
    try:
        rows = first.execute(outbox.RETRY_SQL, (LATER.isoformat(),)).fetchall()
        assert len(rows) == 1
        raced: list[tuple[int, int]] = []

        def item_done(item_id: int, due: dt.date | None) -> None:
            # a second dispatcher selected the same failed row and races the first
            raced.append(outbox.dispatch(second, rows, NATIVES))
            effects_natives.item_done(item_id, due)

        assert outbox.dispatch(first, rows, {**NATIVES, "item_done": item_done}) == (1, 0)
        assert raced == [(0, 0)]
        # once done, a stale selection claims nothing either
        assert outbox.dispatch(second, rows, NATIVES) == (0, 0)
    finally:
        first.close()
        second.close()
    assert effects_natives.SENT == [("item_done", 1, dt.date(2000, 1, 5))]
    assert _statuses(db) == [("done", 2, 1)]


def test_an_outbox_without_claimed_at_gains_the_column(db: Path):
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        conn.execute(
            'CREATE TABLE "lattice_outbox" ("id" INTEGER PRIMARY KEY, "endpoint" TEXT NOT NULL, '
            '"native" TEXT NOT NULL, "args" TEXT NOT NULL, "status" TEXT NOT NULL, '
            '"error" TEXT, "created_at" TEXT NOT NULL, "attempts" INTEGER NOT NULL)'
        )
        conn.execute(
            "INSERT INTO lattice_outbox VALUES (1, 'x', 'item_done', ?, 'failed', 'e', ?, 1)",
            (json.dumps(args(["Int", "Date"], [1, None])), "2000-01-01T00:00:00.000000Z"),
        )
    finally:
        conn.close()
    with TestClient(build_app(_plans(), db, natives=NATIVES)) as c:
        assert c.patch("/items/2/done", headers=_h("alice")).status_code == 200
    report = outbox.retry(db, NATIVES)
    assert (report.done, report.failed) == (1, 0)
    assert _statuses(db) == [("done", 2, 1), ("done", 1, 1)]


def test_the_retry_command_takes_a_grace_period(client: TestClient, db: Path, monkeypatch):
    monkeypatch.setattr(outbox, "MARK_DONE_SQL", "UPDATE no_such_table SET x = ?, y = ?")
    monkeypatch.setattr(outbox, "MARK_BACKOFF", 0.0)
    assert client.patch("/items/1/done", headers=_h("alice")).status_code == 200
    monkeypatch.undo()
    base = [sys.executable, "-m", "lattice.api.outbox", "retry", "--db", str(db)]
    base += ["--natives", "tests.api.effects_natives"]
    run = dict(cwd=ROOT, capture_output=True, text=True, timeout=60)
    out = subprocess.run(base, **run)  # type: ignore[call-overload]
    assert out.returncode == 0, out.stderr
    assert "retried 0 row(s)" in out.stdout
    out = subprocess.run([*base, "--grace", "0"], **run)  # type: ignore[call-overload]
    assert out.returncode == 0, out.stderr
    assert "1 done, 0 failed" in out.stdout
    assert _statuses(db) == [("done", 2, 1)]


# ---- the generated app -----------------------------------------------------------------------


@pytest.fixture
def apps(tmp_path: Path, monkeypatch) -> Iterator[tuple[TestClient, Path, TestClient, Path]]:
    plans = _plans()
    gen_dir = tmp_path / "gen"
    gen_dir.mkdir()
    shutil.copyfile(KIT_PATH, gen_dir / "platform_kit.py")
    shutil.copyfile(NATIVES_FILE, gen_dir / "natives.py")
    (gen_dir / "generated_effects.py").write_text(generate_fastapi(plans, PROGRAM))
    build_db(gen_dir / "app.db")
    rt_db = tmp_path / "rt.db"
    build_db(rt_db)
    monkeypatch.chdir(gen_dir)
    monkeypatch.syspath_prepend(str(gen_dir))
    for name in _MODULES:
        sys.modules.pop(name, None)
    module = importlib.import_module("generated_effects")
    try:
        with (
            TestClient(build_app(plans, rt_db, natives=NATIVES)) as rt,
            TestClient(module.app) as gen,
        ):
            yield rt, rt_db, gen, gen_dir / "app.db"
    finally:
        for name in _MODULES:
            sys.modules.pop(name, None)


REQUESTS = [
    ("POST", "/users/signup", {}, {"email": "new@example.com", "full_name": "New"}),
    ("POST", "/users/signup", {}, {"email": "alice@example.com", "full_name": "Dup"}),
    ("POST", "/items", _h("alice"), {"title": "fresh"}),
    ("POST", "/items", _h("alice"), {"title": "boom"}),
    ("POST", "/items", _h("alice"), {"title": "forbidden"}),
    ("GET", "/items/recent?note=hi&limit=2", _h("alice"), None),
    ("GET", "/items/recent", _h("bob"), None),
    ("PATCH", "/items/2/done", _h("alice"), None),
    ("PATCH", "/items/3/done", _h("alice"), None),
]


def test_the_generated_app_writes_the_same_outbox(apps):
    rt, rt_db, gen, gen_db = apps
    for method, url, headers, body in REQUESTS:
        a = rt.request(method, url, headers=headers, json=body)
        b = gen.request(method, url, headers=headers, json=body)
        assert (a.status_code, a.json()) == (b.status_code, b.json()), url
        assert outbox_rows(rt_db) == outbox_rows(gen_db), url
    stored = outbox_rows(rt_db)
    assert stored is not None
    assert [row[3] for row in stored] == ["done", "done", "failed", "done", "done", "done", "done"]


def test_the_generated_app_retries_with_its_own_natives(apps):
    _, _, gen, gen_db = apps
    gen_natives = sys.modules["natives"]
    gen_natives.FAIL_ALWAYS.add("item_done")
    assert gen.patch("/items/1/done", headers=_h("alice")).status_code == 200
    stored = outbox_rows(gen_db)
    assert stored is not None and stored[0][3] == "failed"
    gen_natives.FAIL_ALWAYS.clear()
    module = sys.modules["generated_effects"]
    assert module.retry_outbox() == (1, 0)
    stored = outbox_rows(gen_db)
    assert stored is not None and stored[0][3:6] == ("done", None, 2)
    assert gen_natives.SENT[-1] == ("item_done", 1, dt.date(2000, 1, 5))


def test_the_generated_app_recovers_a_lost_mark(apps):
    _, _, gen, gen_db = apps
    module = sys.modules["generated_effects"]
    with pytest.MonkeyPatch.context() as mp:  # not `monkeypatch`: apps chdir'ed with it
        mp.setattr(module, "OUTBOX_DONE", "UPDATE no_such_table SET x = ?, y = ?")
        mp.setattr(module, "OUTBOX_MARK_BACKOFF", 0.0)
        assert gen.patch("/items/1/done", headers=_h("alice")).status_code == 200
    assert _statuses(gen_db) == [("dispatching", 1, 1)]
    assert module.retry_outbox() == (0, 0)  # in flight
    assert module.retry_outbox(now=LATER) == (1, 0)
    assert _statuses(gen_db) == [("done", 2, 1)]
    gen_natives = sys.modules["natives"]
    assert gen_natives.SENT.count(("item_done", 1, dt.date(2000, 1, 5))) == 2


def test_the_generated_app_claims_before_sending(apps):
    _, _, gen, gen_db = apps
    module = sys.modules["generated_effects"]
    gen_natives = sys.modules["natives"]
    gen_natives.FAIL_ALWAYS.add("item_done")
    assert gen.patch("/items/1/done", headers=_h("alice")).status_code == 200
    gen_natives.FAIL_ALWAYS.clear()
    first = module.connect()
    second = module.connect()
    try:
        rows = first.execute(module.OUTBOX_RETRY, (LATER.isoformat(),)).fetchall()
        raced: list[tuple[int, int]] = []
        real = module.EFFECTS["item_done"]

        def item_done(item_id: int, due: dt.date | None) -> None:
            raced.append(module.dispatch_outbox(second, rows))
            real(item_id, due)

        module.EFFECTS["item_done"] = item_done
        try:
            assert module.dispatch_outbox(first, rows) == (1, 0)
        finally:
            module.EFFECTS["item_done"] = real
        assert raced == [(0, 0)]
    finally:
        first.close()
        second.close()
    assert _statuses(gen_db) == [("done", 2, 1)]


def test_the_generated_effects_app_is_ruff_formatted(tmp_path: Path):
    ruff = shutil.which("ruff", path=str(Path(sys.executable).parent)) or shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff is not installed")
    code = generate_fastapi(_plans(), PROGRAM)
    assert "EFFECTS.update(" in code and "conn.execute(OUTBOX_DDL)" in code
    assert "NATIVES.update(" not in code  # effect natives are never SQL functions
    path = tmp_path / "app.py"
    path.write_text(code)
    done = subprocess.run(
        [ruff, "format", "--check", "--isolated", "--line-length", "100", str(path)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_for_each_over_the_caller_row(db: Path):
    program = PROGRAM + '''

@endpoint("POST", "/me/ping", auth=Customer)
def ping(user: CurrentUser) -> One:
    for_each(user, native.send_welcome, col("email"), fn.upper(col("full_name")))
    me = select(user, cols=["id", "email"])
    return me
'''
    check = check_source(program, PROFILE)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    with TestClient(build_app(lower_api(check.program), db, natives=NATIVES)) as c:
        r = c.post("/me/ping", headers=_h("bob"))
        assert r.status_code == 200 and r.json() == {"id": 3, "email": "bob@example.com"}
    assert effects_natives.SENT == [("send_welcome", "bob@example.com", "BOB")]
    stored = outbox_rows(db)
    assert stored is not None and [row[:2] + row[3:] for row in stored] == [
        ("ping", "send_welcome", "done", None, 1, 1)
    ]
