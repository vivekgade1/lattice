"""The live contract check on a server dialect (lattice/contracts/live.py, MySQL): fakes for
Docker, the server and the app for the unit cases (what is started, in which order, what is never
touched), a real child for the SIGTERM grace and the sweep, and one test on a real throwaway MySQL
container (skipped without Docker, PyMySQL or the local ``mysql:8.4`` image).

Pre-registration: docs/results/2026-10-15-live-contract-go-mysql-prereg.md, L1-L9."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lattice.contracts import export as ex
from lattice.contracts import live
from lattice.contracts.live import LiveError
from lattice.spec import Scenario, Step

from .test_live import CLEAN_IMPORTS, NOTES_SPEC, Live, only

OWNER = "live-0123456789abcdef"


# ---- fakes -----------------------------------------------------------------------------------


class Rec:
    """What the fakes saw, in order (``events``) and by kind."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.server_for: list[tuple[Any, ...]] = []
        self.env_at_server_for: dict[str, str] = {}
        self.target_kw: list[dict[str, Any]] = []
        self.clones: list[str] = []
        self.released: list[str] = []
        self.given: list[tuple[str, Any]] = []
        self.sent: list[tuple[str, str, str | None]] = []
        self.tmp: Path = Path()


class FakeServer:
    def __init__(self, rec: Rec) -> None:
        self.rec = rec

    def stop(self) -> None:
        self.rec.events.append("server.stop")


class FakeDbs:
    """``ServerDatabases``: a clone of the seed per call, ``release`` drops one."""

    def __init__(self, rec: Rec) -> None:
        self.rec, self.n = rec, 0

    def __call__(self, variant: str, path: Any = None) -> str:
        assert variant == "seed"
        self.n += 1
        ref = f"mysql://fake/clone{self.n}"
        self.rec.clones.append(ref)
        return ref

    def release(self, ref: Any) -> None:
        self.rec.released.append(ref)

    def close(self) -> None:
        self.rec.events.append("dbs.close")


def _spec(*scenarios: Scenario) -> Any:
    return SimpleNamespace(scenarios=tuple(scenarios))


def _steps(*paths: str, given: dict | None = None) -> Any:
    return _spec(*(
        Scenario(f"s{i}", given=given or {}, steps=(Step("GET", p, "ann", expect_status=200),))
        for i, p in enumerate(paths)
    ))  # fmt: skip


def _imp(endpoint: str) -> dict[str, Any]:
    return {"endpoint": endpoint, "status": [200], "auth": "token", "as_user": "ann",
            "request_fields": [], "response_fields": []}  # fmt: skip


@pytest.fixture
def rec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rec:
    """Everything `live._child` reaches for a MySQL provider, faked; the child's TMPDIR is
    ``tmp_path/tmp`` and its working directory ``tmp_path``."""
    from lattice.security import target as target_mod

    check_mod = importlib.import_module("lattice.spec.check")  # not the `check` function
    from lattice.spec import dbserver

    r = Rec()
    r.tmp = tmp_path / "tmp"
    r.tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(r.tmp))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(live.OWNER_ENV, OWNER)
    for name in list(os.environ):
        if name.startswith("LATTICE_") and name.endswith("_URL"):
            monkeypatch.delenv(name)
    harness = SimpleNamespace(database="mysql", database_url=None, db_factory=lambda url: url,
                              runtime=None, login=None)  # fmt: skip
    r.harness = harness  # type: ignore[attr-defined]
    r.spec = _steps("/a")  # type: ignore[attr-defined]
    monkeypatch.setattr(live, "_harness", lambda project, settings: r.harness)
    monkeypatch.setattr(ex, "load_spec", lambda project, ref: r.spec)

    def docker_available() -> bool:
        r.events.append("docker_available")
        return True

    def image_present(image: str) -> bool:
        r.events.append(f"image_present:{image}")
        return True

    def missing(requires: Any) -> list[str]:
        r.events.append(f"requirements:{','.join(requires)}")
        return []

    def server_for(dialect: str, url: str | None = None, name_prefix: str = "lattice-",
                   extra_args: Any = ()) -> tuple[Any, bool]:  # fmt: skip
        r.events.append("server_for")
        r.server_for.append((dialect, url, name_prefix))
        r.env_at_server_for = dict(os.environ)
        return FakeServer(r), True

    monkeypatch.setattr(dbserver, "docker_available", docker_available)
    monkeypatch.setattr(dbserver, "server_for", server_for)
    monkeypatch.setattr(dbserver, "ServerDatabases", lambda server, factory: FakeDbs(r))
    monkeypatch.setattr(live, "_image_present", image_present)
    monkeypatch.setattr(live, "_missing_requirements", missing)

    @contextmanager
    def start_target_harness(h: Any, seed_hook: Any = None, **kw: Any) -> Any:
        r.target_kw.append(kw)
        dbs = kw.get("databases")
        db = dbs("seed", Path("unused")) if dbs is not None else "sqlite-file"
        if seed_hook is not None:
            seed_hook(db)

        def request(req: Any) -> Any:
            r.sent.append((req.method, req.path, req.as_user))
            return SimpleNamespace(status=200, body={"ok": True})

        try:
            yield SimpleNamespace(request=request)
        finally:
            if dbs is not None:
                dbs.release(db)

    monkeypatch.setattr(target_mod, "start_target_harness", start_target_harness)
    monkeypatch.setattr(check_mod, "_insert_given",
                        lambda spec, db, given: r.given.append((db, given)))  # fmt: skip
    return r


def child(rec: Rec, *endpoints: str) -> dict[str, Any]:
    job = {"project": str(rec.tmp.parent), "spec": "spec:SPEC", "settings": {},
           "imports": [_imp(e) for e in endpoints]}  # fmt: skip
    return live._child(job)


def fatal(rec: Rec, *endpoints: str) -> live._Fatal:
    with pytest.raises(live._Fatal) as e:
        child(rec, *endpoints)
    return e.value


# ---- L1, L2: one server, a clone per replay --------------------------------------------------


def test_postgres_stays_unsupported_and_the_message_lists_the_supported_set(rec: Rec) -> None:
    rec.harness.database = "postgres"  # type: ignore[attr-defined]
    e = fatal(rec, "GET /a")
    assert e.code == "unsupported_database"
    assert "sqlite, mysql" in e.message and "postgres" in e.message
    assert rec.events == []  # nothing was looked at, nothing started
    assert live.LIVE_DATABASES == ("sqlite", "mysql")


def test_one_server_for_the_run_and_a_clone_per_replay(rec: Rec) -> None:
    rec.spec = _steps("/a", "/b", "/c", given={"notes": [{"id": 1}]})
    out = child(rec, "GET /a", "GET /b", "GET /c")
    assert [o["answered"] for o in out["outcomes"]] == [200, 200, 200]
    assert rec.server_for == [("mysql", None, "lattice-live-")]  # once, never a given URL
    assert len(rec.target_kw) == 3
    shared = rec.target_kw[0]["databases"]
    assert all(kw["databases"] is shared for kw in rec.target_kw)  # the run's one make_db
    assert len(set(rec.clones)) == 3 == len(rec.clones)  # a fresh clone per replay ...
    assert [db for db, _ in rec.given] == rec.clones  # ... seeded with the scenario's given
    assert rec.released == rec.clones  # ... and dropped afterwards
    assert rec.events[-2:] == ["dbs.close", "server.stop"]  # end: clones first, then the server
    assert out["database"] == {"engine": "mysql", "image": "mysql:8.4", "throwaway": True,
                               "ignored": []}  # fmt: skip


def test_imports_on_one_step_share_one_replay_and_one_clone(rec: Rec) -> None:
    rec.spec = _steps("/a")
    child(rec, "GET /a", "GET /a")
    assert len(rec.clones) == 1


def test_a_run_that_replays_nothing_starts_no_server(rec: Rec) -> None:
    out = child(rec, "GET /nowhere")
    assert out["outcomes"][0]["reason"] == "the endpoint is not in the provider's current spec"
    assert rec.events == []  # no precheck, no server, no clone: no Docker needed at all
    assert out["database"]["throwaway"] is True


def test_the_prechecks_run_in_order_at_the_first_replay(rec: Rec) -> None:
    child(rec, "GET /nowhere", "GET /a")
    assert rec.events == ["requirements:pymysql>=1.1", "docker_available",
                          "image_present:mysql:8.4", "server_for", "dbs.close",
                          "server.stop"]  # fmt: skip
    assert rec.sent == [("GET", "/a", "ann")]


# ---- L3: the user's servers are never used ---------------------------------------------------


def test_the_users_servers_are_dropped_from_the_environment_and_ignored(
    rec: Rec, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LATTICE_MYSQL_URL", "mysql://root:secret@10.1.1.1:3306/prod")
    monkeypatch.setenv("LATTICE_POSTGRES_URL", "postgresql://u:p@10.1.1.2/prod")
    rec.harness.database_url = "mysql://root:secret@10.1.1.3:3306/staging"  # type: ignore[attr-defined]
    out = child(rec, "GET /a")
    assert rec.server_for == [("mysql", None, "lattice-live-")]  # the URL was never passed
    seen = {k for k in rec.env_at_server_for if k.startswith("LATTICE_") and k.endswith("_URL")}
    assert seen == set()  # the Go app inherits this environment: no user URL in it
    assert out["database"]["ignored"] == ["LATTICE_MYSQL_URL", "database_url"]
    assert "secret" not in json.dumps(out)  # nothing of the URLs is reported


def test_a_server_that_the_check_did_not_start_is_refused(
    rec: Rec, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lattice.spec import dbserver

    stopped: list[str] = []
    server = SimpleNamespace(stop=lambda: stopped.append("stop"))
    monkeypatch.setattr(dbserver, "server_for", lambda *a, **k: (server, False))
    e = fatal(rec, "GET /a")
    assert e.code == "database_start_failed" and stopped == ["stop"]
    assert rec.clones == []  # nothing was created on it


# ---- L4: the prechecks, in order, and nothing started before they pass -----------------------


def test_driver_missing_comes_first_and_starts_nothing(
    rec: Rec, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live, "_missing_requirements", lambda requires: ["pymysql>=1.1"])
    e = fatal(rec, "GET /a")
    assert e.code == "driver_missing"
    assert "pymysql>=1.1" in e.message and "lattice[db]" in e.message
    assert rec.events == []  # no Docker call, no marker
    assert not (rec.tmp / live.SERVER_MARKER).exists()


def test_docker_unavailable_precedes_the_image_and_the_server(
    rec: Rec, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lattice.spec import dbserver

    monkeypatch.setattr(dbserver, "docker_available", lambda: False)
    e = fatal(rec, "GET /a")
    assert e.code == "docker_unavailable"
    assert not any(ev.startswith(("image_present", "server_for")) for ev in rec.events)
    assert not (rec.tmp / live.SERVER_MARKER).exists()


def test_a_missing_image_is_reported_and_never_pulled(
    rec: Rec, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live, "_image_present", lambda image: False)
    e = fatal(rec, "GET /a")
    assert e.code == "docker_image_missing"
    assert "docker pull mysql:8.4" in e.message  # the command is for the user to run
    assert "server_for" not in rec.events
    assert not (rec.tmp / live.SERVER_MARKER).exists()


def test_the_server_marker_is_written_before_the_container_is_started(
    rec: Rec, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lattice.spec import dbserver

    seen: list[Any] = []

    def server_for(*a: Any, **k: Any) -> Any:
        seen.append(json.loads((rec.tmp / live.SERVER_MARKER).read_text()))
        raise dbserver.DbServerError("docker run mysql:8.4 failed: boom")

    monkeypatch.setattr(dbserver, "server_for", server_for)
    e = fatal(rec, "GET /a")
    assert seen == [{"owner": OWNER}]
    assert e.code == "database_start_failed" and "boom" in e.message


def test_an_app_that_fails_to_start_is_provider_start_failed_and_the_server_goes(
    rec: Rec, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lattice.security import target as target_mod

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("migrate failed")

    monkeypatch.setattr(target_mod, "start_target_harness", boom)
    e = fatal(rec, "GET /a")
    assert e.code == "provider_start_failed" and "migrate failed" in e.message
    assert rec.events[-2:] == ["dbs.close", "server.stop"]


def test_requirement_check_reads_the_installed_version(monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib import metadata

    versions = {"pymysql": "1.2.3", "old": "1.0"}

    def version(name: str) -> str:
        if name in versions:
            return versions[name]
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", version)
    got = live._missing_requirements(["pymysql>=1.1", "old>=1.1", "nothere", "pymysql==9"])
    assert got == ["old>=1.1", "nothere", "pymysql==9"]


# ---- L5: SIGTERM, the grace and the sweep ----------------------------------------------------


def test_a_timeout_gives_the_child_sigterm_and_a_grace_before_the_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "term.txt"
    script = textwrap.dedent("""\
        import signal, sys, time
        def term(*_):
            open(sys.argv[1], "w").write("term")
            sys.exit(0)
        signal.signal(signal.SIGTERM, term)
        open(sys.argv[1] + ".ready", "w").write("1")
        time.sleep(60)
        """)
    real = subprocess.Popen

    def fake_popen(cmd: Any, **kw: Any) -> Any:
        return real([sys.executable, "-c", script, str(out)], **kw)

    monkeypatch.setattr(live.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(live, "TIMEOUT_S", 1.5)
    work = tmp_path / "w"
    work.mkdir()
    t0 = time.monotonic()
    with pytest.raises(LiveError) as e:
        live._spawn({"x": 1}, tmp_path, dict(os.environ), work)
    assert e.value.code == "live_timeout"
    assert out.read_text() == "term"  # it was told to stop and unwound (SIGKILL cannot do this)
    assert time.monotonic() - t0 < live.TERM_GRACE_S


def test_a_child_that_ignores_sigterm_is_killed_after_the_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    real = subprocess.Popen
    monkeypatch.setattr(live.subprocess, "Popen",
                        lambda cmd, **kw: real([sys.executable, "-c", script], **kw))  # fmt: skip
    monkeypatch.setattr(live, "TIMEOUT_S", 1.0)
    monkeypatch.setattr(live, "TERM_GRACE_S", 1.0)
    work = tmp_path / "w"
    work.mkdir()
    t0 = time.monotonic()
    with pytest.raises(LiveError) as e:
        live._spawn({"x": 1}, tmp_path, dict(os.environ), work)
    assert e.value.code == "live_timeout" and time.monotonic() - t0 < 8


def _sleeper() -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                            start_new_session=True)  # fmt: skip


_REAL_RUN = subprocess.run


def _fake_docker(calls: list[list[str]], listed: str = "c1\nc2\n") -> Any:
    """A ``subprocess.run`` that records and fakes the ``docker`` calls and runs the rest."""

    def run(cmd: list[str], **kw: Any) -> Any:
        if cmd[0] != "docker":
            return _REAL_RUN(cmd, **kw)
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout=listed if cmd[1] == "ps" else "", stderr="")

    return run


def test_the_sweep_kills_recorded_apps_and_removes_the_labelled_containers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _sleeper()
    (tmp_path / live.PID_MARKER).write_text(f"{app.pid}\n{app.pid}\n999999999\n")
    (tmp_path / live.SERVER_MARKER).write_text(json.dumps({"owner": OWNER}))
    calls: list[list[str]] = []
    monkeypatch.setattr(live.subprocess, "run", _fake_docker(calls))
    live._sweep(tmp_path, OWNER)
    assert app.wait(timeout=10) == -9  # its group was killed
    assert calls == [
        ["docker", "ps", "-aq", "--filter", f"label=lattice.owner={OWNER}"],
        ["docker", "rm", "-f", "-v", "c1", "c2"],
    ]


def test_the_sweep_with_no_marker_calls_no_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(live.subprocess, "run", _fake_docker(calls))
    live._sweep(tmp_path, OWNER)
    assert calls == []


def test_the_sweep_trusts_only_a_marker_that_names_this_runs_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(live.subprocess, "run", _fake_docker(calls))
    for owner in ("studio-gate-7", "live-ffffffffffffffff"):
        (tmp_path / live.SERVER_MARKER).write_text(json.dumps({"owner": owner}))
        live._sweep(tmp_path, OWNER)
    (tmp_path / live.SERVER_MARKER).write_text(json.dumps({"owner": "studio-gate-7"}))
    live._sweep(tmp_path, "studio-gate-7")  # not a live-<16 hex> token: never swept by label
    assert calls == []


def test_pids_of_a_process_that_is_not_a_session_leader_are_left_alone(
    tmp_path: Path,
) -> None:
    (tmp_path / live.PID_MARKER).write_text(f"{os.getpid()}\n")  # this process: pgid != pid
    live._sweep(tmp_path, OWNER)  # must not kill the test runner (its group is not its pid)


# ---- L7, L8: the SQLite result is byte for byte what it was -----------------------------------


def test_a_sqlite_result_has_no_database_key_and_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setenv("TMPDIR", str(scratch))
    calls: list[list[str]] = []
    monkeypatch.setattr(live.subprocess, "run", _fake_docker(calls))
    res = Live(tmp_path / "w", imports=only(CLEAN_IMPORTS, "GET /admin")).run()
    assert list(res) == ["status", "replayed", "not_replayed", "replays", "findings",
                         "verified_live", "fields_unobservable", "not_checked"]  # fmt: skip
    assert calls == [] and list(scratch.iterdir()) == []  # no docker; markers went with TMPDIR


def test_the_result_of_a_server_dialect_adds_one_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    block = {"engine": "mysql", "image": "mysql:8.4", "throwaway": True, "ignored": []}
    w = Live(tmp_path, imports=only(CLEAN_IMPORTS, "GET /admin"))
    monkeypatch.setattr(live, "_spawn", lambda *a, **k: {
        "ok": True, "database": block,
        "outcomes": [{"scenario": "admin", "step": 2, "answered": 200, "shape": "object",
                      "fields": ["ok"]}]})  # fmt: skip
    res = w.run()
    assert list(res)[-1] == "database" and res["database"] == block
    assert res["replayed"] == 1


# ---- start_target_harness(databases=...) ------------------------------------------------------


def test_start_target_harness_uses_a_given_make_db_and_never_opens_a_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lattice.security import target as target_mod

    seen: list[Any] = []

    class Transport:
        def __enter__(self) -> Transport:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

    runtime = SimpleNamespace(open=lambda app: Transport(), close=lambda app: None,
                              auth=None)  # fmt: skip
    h = SimpleNamespace(database="mysql", database_url="mysql://x@y/z", runtime=runtime,
                        login=None, app_factory=lambda db: seen.append(("app", db)) or object(),
                        db_factory=lambda *a: None)  # fmt: skip

    def no_open(*a: Any, **k: Any) -> Any:
        raise AssertionError("a server was opened")

    monkeypatch.setattr(target_mod, "_open_db", no_open)

    class Dbs:
        def __call__(self, variant: str, path: Any) -> str:
            seen.append(("make", variant))
            return "mysql://fake/c1"

        def release(self, ref: Any) -> None:
            seen.append(("release", ref))

    with target_mod.start_target_harness(h, lambda db: seen.append(("seed", db)),
                                         databases=Dbs()) as t:  # fmt: skip
        assert t._database == "mysql" and t._db_ref == "mysql://fake/c1"
    assert seen == [("make", "seed"), ("seed", "mysql://fake/c1"), ("app", "mysql://fake/c1"),
                    ("release", "mysql://fake/c1")]  # fmt: skip


def test_start_target_harness_databases_is_keyword_only() -> None:
    import inspect

    from lattice.security.target import start_target_harness

    p = inspect.signature(start_target_harness).parameters["databases"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is None


# ---- a real throwaway MySQL (skipped without Docker, PyMySQL or the local image) ---------------


def _docker_mysql_ready() -> str:
    """'' when the real-container test can run, else why not."""
    import importlib.util

    if importlib.util.find_spec("pymysql") is None:
        return "PyMySQL is not installed"
    if shutil.which("docker") is None:
        return "no docker CLI"
    try:
        if subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode:
            return "the Docker daemon does not answer"
        cmd = ["docker", "image", "inspect", "mysql:8.4"]
        if subprocess.run(cmd, capture_output=True, timeout=30).returncode:
            return "the image mysql:8.4 is not local"
    except (OSError, subprocess.SubprocessError):
        return "docker failed"
    return ""


MYSQL_APP = """\
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from lattice.spec.db import open_db


def build_db(url):
    db = open_db(url)
    try:
        db.execute("create table notes (id int primary key, user_id int, text varchar(100))")
    finally:
        db.close()
    return url


def login(client, handle):
    return "tok-" + handle


def make_app(url):
    app = FastAPI()

    @app.get("/notes/{note_id}")
    def note(note_id: int, request: Request):
        if not request.headers.get("authorization"):
            return JSONResponse({"detail": "no token"}, status_code=401)
        db = open_db(url)
        try:
            rows = db.query("select id, text from notes where id = %s", (note_id,))
        finally:
            db.close()
        if not rows:
            return JSONResponse({"detail": "no such note"}, status_code=404)
        return {"id": rows[0][0], "text": rows[0][1]}

    return app
"""


@pytest.mark.skipif(bool(_docker_mysql_ready()), reason=_docker_mysql_ready() or "ready")
def test_a_real_throwaway_mysql_serves_the_replay_and_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def live_containers() -> list[str]:
        cmd = ["docker", "ps", "-a", "--filter", "name=lattice-live-", "--format", "{{.Names}}"]
        out = subprocess.run(cmd, capture_output=True, text=True)
        return sorted(out.stdout.split())

    before = live_containers()
    given = {"notes": [{"id": 10, "user_id": 1, "text": "milk"}]}
    imps = [{"endpoint": "GET /notes/{id}", "status": [200], "auth": "token", "as_user": "ann",
             "response_fields": ["id", "text"]}]  # fmt: skip
    studio = ('[app]\napp = "prov_app:make_app"\ndb = "prov_app:build_db"\n'
              'login = "prov_app:login"\ndatabase = "mysql"\n')  # fmt: skip
    w = Live(tmp_path, spec_text=NOTES_SPEC.format(GIVEN=given), imports=imps, studio=studio)
    (w.prov / "prov_app.py").write_text(MYSQL_APP)
    # a server the user named, unreachable on purpose: any use of it would fail the run
    monkeypatch.setenv("LATTICE_MYSQL_URL", "mysql://root:x@127.0.0.1:1/none")
    res = w.run()
    assert res["findings"] == [] and res["replayed"] == 1
    assert res["replays"][0]["answered"] == 200
    assert res["database"] == {"engine": "mysql", "image": "mysql:8.4", "throwaway": True,
                               "ignored": ["LATTICE_MYSQL_URL"]}  # fmt: skip
    assert live_containers() == before  # the container of this run is gone
