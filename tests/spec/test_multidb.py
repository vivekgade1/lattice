"""check() on server databases, end to end: a tiny process app (tests/spec/multidb_app.py,
the drivers directly, its URL from ``$DATABASE_URL``) against a tiny spec on Postgres,
MySQL and ClickHouse -- layers C, D (with ``given`` rows and an ``Await`` on a worker
service) and X, a correct app passing, a racy one refuted by X, a planted bug caught by
C and D; and the database a check starts for itself (a container), removed after it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice.spec import (
    Await,
    Invariant,
    Machine,
    Report,
    Scenario,
    Service,
    ServiceCmd,
    Spec,
    Step,
    check,
)
from lattice.spec.__main__ import main
from lattice.spec.db import open_db
from lattice.spec.dbserver import DbServer, live_containers
from lattice.spec.process import ProcessApp, live_processes, process_runtime
from lattice.spec.services import live_services
from lattice.typesys import Scalar, Schema
from tests.spec import racy
from tests.spec.conftest import lattice_containers

S = Scalar
APP = Path(__file__).parent / "multidb_app.py"

RUN = Schema.from_pairs(
    [("id", S.INT), ("config_id", S.INT), ("status", S.STR), ("urgent", S.BOOL),
     ("note", S.STR)],
    keys=[("id",)],
    nullable=["note"],
    one_of={"status": ("queued", "running", "succeeded", "cancelled")},
)  # fmt: skip
PROFILE = racy._profile({"Config": racy.CONFIG, "Run": RUN},
                        {"Config": "configs", "Run": "runs"})  # fmt: skip
URGENT_NOT_CANCELLED = Invariant(
    "urgent_runs_are_not_cancelled",
    "An urgent run is never cancelled.",
    'not (Run.urgent and Run.status == "cancelled")',
)
CREATE = Step("POST", "/configs/1/runs", "ann", json={"urgent": False}, expect_status=201,
              expect_db={"Run": {"where": {"config_id": 1}, "count": 1,
                                 "equals": {"status": "queued", "urgent": False,
                                            "note": None}}})  # fmt: skip
SCENARIOS = (
    Scenario(
        "one_live_run_per_config",
        steps=(
            CREATE,
            Step("POST", "/configs/1/runs", "ben", json={}, expect_status=409),
            Step("POST", "/runs/1/cancel", "ben", expect_status=200,
                 expect_body={"id": 1, "status": "cancelled"}),  # fmt: skip
            Step("POST", "/configs/1/runs", "ben", json={}, expect_status=201),
        ),
    ),
    Scenario(
        "an_urgent_run_keeps_its_flag",
        steps=(
            Step("POST", "/configs/2/runs", "ann", json={"urgent": True, "note": "asap"},
                 expect_status=201, expect_body={"urgent": True, "note": "asap"},
                 expect_db={"Run": {"where": {"urgent": True}, "count": 1,
                                    "equals": {"note": "asap", "config_id": 2}}}),  # fmt: skip
        ),
    ),
    Scenario(
        "a_given_run_is_read_back",
        given={"Run": [{"id": 50, "config_id": 2, "status": "succeeded", "urgent": True,
                        "note": None}]},  # fmt: skip
        steps=(
            Step("GET", "/runs/50", "ann",
                 expect_body={"id": 50, "status": "succeeded", "urgent": True, "note": None},
                 expect_db={"Run": {"where": {"note": None}, "count": 1}}),  # fmt: skip
        ),
    ),
    Scenario(
        "the_worker_finishes_a_queued_run",
        steps=(
            CREATE,
            Await("worker", {"Run": {"where": {"id": 1}, "equals": {"status": "succeeded"}}},
                  within_s=10),  # fmt: skip
            Step("GET", "/runs/1", "ann", expect_body={"id": 1, "status": "succeeded"}),
        ),
    ),
)
SPEC = Spec(
    profile=PROFILE,
    machines=(Machine("Run", "status", {"queued": ("running", "cancelled"),
                                             "running": ("succeeded", "cancelled")},
                           initial=("queued",)),),  # fmt: skip
    invariants=(racy.ONE_LIVE, URGENT_NOT_CANCELLED),
    scenarios=SCENARIOS,
    users=racy.USERS,
    services=(Service("worker", "moves queued runs to succeeded"),),
)


def build_db(mode: str):
    """The db factory: the app migrates the empty database it is given (a URL), then the
    seed rows go in through the adapter."""

    def build(url: str) -> None:
        assert isinstance(url, str) and "://" in url
        subprocess.run([sys.executable, str(APP), "migrate", url, mode], check=True)
        with open_db(url) as db:
            db.insert("users", [{"id": i, "name": h, "token": f"tok-{h}"}
                                for h, i in racy.USER_IDS.items()])  # fmt: skip
            if db.dialect != "clickhouse":
                db.insert("configs", [{"id": 1, "name": "nightly"}, {"id": 2, "name": "hourly"}])

    return build


def make_app(mode: str):
    def factory(db: str) -> ProcessApp:
        return ProcessApp([sys.executable, str(APP), "serve"], db_path=db, env={"APP_MODE": mode},
                          db_env="DATABASE_URL", startup_timeout=20)  # fmt: skip

    return factory


def worker(url: str) -> ServiceCmd:
    return ServiceCmd([sys.executable, str(APP), "worker", str(url)], stop_timeout=2)


RUNTIME = process_runtime(services={"worker": worker})


def run(server: DbServer, mode: str, layers: str = "CD", spec: Spec = SPEC, **kw: Any) -> Report:
    kw.setdefault("x_k", 6)
    kw.setdefault("x_repeats", 2)
    kw.setdefault("x_budget", 4)
    return check(spec, make_app(mode), build_db(mode), layers=layers, login=racy.login,
                 runtime=RUNTIME, database=server.dialect, database_url=server.url,
                 **kw)  # fmt: skip


def of(report: Report, layer: str) -> list:
    return [f for f in report.findings if f.layer == layer]


@pytest.fixture(autouse=True)
def nothing_left_running():
    yield
    assert live_services() == [], "a service outlived its check"
    assert live_processes() == [], "a process app outlived its check"


@pytest.fixture(params=["postgres", "mysql"])
def oltp(request: pytest.FixtureRequest, db_servers: dict[str, DbServer]) -> DbServer:
    from tests.spec.conftest import server

    return server(db_servers, request.param)


def _databases(server: DbServer) -> set[str]:
    with open_db(server.url) as db:
        if server.dialect == "postgres":
            return {r[0] for r in db.query("SELECT datname FROM pg_database")}
        if server.dialect == "mysql":
            return {r[0] for r in db.query("SELECT schema_name FROM information_schema.schemata")}
        return {r[0] for r in db.query("SELECT name FROM system.databases")}


# ---- Postgres and MySQL -----------------------------------------------------------------------


def test_a_correct_app_passes_c_d_and_x(oltp):
    before = _databases(oltp)
    report = run(oltp, "safe", layers="CDX")
    assert report.findings == [], report.render()
    assert report.verdict == "pass"
    cases = {k: v["verdict"] for k, v in report.cases.items()}
    assert set(cases.values()) == {"correct"}, cases
    assert "the_worker_finishes_a_queued_run / step 2" in cases  # the Await ran
    notes = "\n".join(report.notes)
    assert "not refuted" in notes and "one_live_run_per_config" in notes, notes
    assert f"database: {oltp.dialect}" in notes and "clone(s)" in notes, notes
    # every database the check created was dropped; no other database touched
    assert _databases(oltp) == before


def test_layer_x_refutes_a_racy_create(oltp):
    report = run(oltp, "racy", layers="X")
    found = [f for f in of(report, "X") if f.details["rule_id"] == "one_live_run_per_config"]
    assert found, report.render()
    assert found[0].endpoint == "POST /configs/1/runs"
    assert found[0].details["rows"][0]["count"] > 1


def test_a_planted_bug_is_caught_by_c_and_d(oltp):
    report = run(oltp, "buggy", layers="CD")
    d = of(report, "D")
    assert d and any("expected 1 rows, found 2" in p for f in d
                     for p in f.details.get("problems", [])), report.render()  # fmt: skip
    c = of(report, "C")
    assert {f.details["invariant"] for f in c} == {"one_live_run_per_config"}, report.render()
    assert c[0].details["rows"][0]["count"] == 2
    assert report.verdict == "fail"


def test_a_service_that_does_not_finish_is_a_d_finding(oltp):
    def lazy(url: str) -> ServiceCmd:  # never touches the database
        return ServiceCmd([sys.executable, "-c", "import time; time.sleep(30)"], stop_timeout=1)

    wait = Await("worker", {"Run": {"where": {"id": 1}, "equals": {"status": "succeeded"}}},
                 within_s=1.5)  # fmt: skip
    scenario = Scenario("the_worker_finishes_a_queued_run", steps=(CREATE, wait))
    spec = Spec(profile=PROFILE, invariants=SPEC.invariants, scenarios=(scenario,),
                users=racy.USERS, services=SPEC.services)  # fmt: skip
    report = run(oltp, "safe", layers="D", spec=spec, services={"worker": lazy})
    d = of(report, "D")
    assert len(d) == 1 and d[0].endpoint == "service worker", report.render()
    assert "did not bring about" in d[0].details["problems"][0]


def test_layers_b_and_r_are_skipped_on_a_server_with_a_note(pg):
    report = run(pg, "safe", layers="BD")
    assert any("layer B skipped on postgres" in n for n in report.notes), report.notes
    assert of(report, "D") == []


def test_the_cli_takes_database_and_database_url(pg, capsys):
    here = "tests.spec.test_multidb"
    code = main(["check", "--spec", f"{here}:SPEC", "--app", f"{here}:SAFE_APP",
                 "--db", f"{here}:SAFE_DB", "--login", "tests.spec.racy:login",
                 "--runtime", f"{here}:RUNTIME", "--layers", "CD", "--database", "postgres",
                 "--database-url", pg.url])  # fmt: skip
    out = capsys.readouterr()
    assert code == 0, out.out + out.err
    assert "database: postgres" in out.out


SAFE_APP = make_app("safe")
SAFE_DB = build_db("safe")


def test_a_check_starts_and_removes_its_own_container(db_servers, monkeypatch):
    monkeypatch.delenv("LATTICE_POSTGRES_URL", raising=False)
    before = set(lattice_containers())
    report = check(SPEC, make_app("safe"), build_db("safe"), layers="D", login=racy.login,
                   runtime=RUNTIME, database="postgres")  # fmt: skip
    assert report.findings == [], report.render()
    assert any("container lattice-postgres-" in n for n in report.notes), report.notes
    assert set(lattice_containers()) == before
    assert set(live_containers()) == {s.container for s in db_servers.values()}


def test_an_unknown_database_is_a_check_error():
    from lattice.spec import CheckError

    with pytest.raises(CheckError, match="unknown database"):
        check(SPEC, make_app("safe"), build_db("safe"), layers="D", database="oracle")
    with pytest.raises(CheckError, match="database_url"):
        check(SPEC, make_app("safe"), build_db("safe"), layers="D", database_url="x://y")


# ---- ClickHouse -------------------------------------------------------------------------------

EVENT = Schema.from_pairs(
    [("id", S.INT), ("user_id", S.INT), ("amount", S.INT), ("flagged", S.BOOL),
     ("note", S.STR)],
    keys=[("id",)],
    nullable=["note"],
)  # fmt: skip
CH_PROFILE = racy._profile({"Event": EVENT}, {"Event": "events"})
CH_SPEC = Spec(
    profile=CH_PROFILE,
    invariants=(
        Invariant("amounts_are_not_negative", "An event's amount is not negative.",
                  "Event.amount >= 0"),
        Invariant("one_flagged_event_per_user", "A user has at most one flagged event.",
                  'count(Event, by="user_id", where=Event.flagged) <= 1'),
    ),  # fmt: skip
    scenarios=(
        Scenario(
            "an_event_is_recorded",
            given={"Event": [{"id": 1, "user_id": 2, "amount": 5, "flagged": False,
                              "note": "given"}]},  # fmt: skip
            steps=(
                Step("POST", "/events", "ann", json={"user_id": 1, "amount": 30, "flagged": True},
                     expect_status=201,
                     expect_db={"Event": {"where": {"user_id": 1, "flagged": True}, "count": 1,
                                          "equals": {"amount": 30, "note": None}}}),
                Step("GET", "/events/2", "ann", expect_body={"id": 2, "amount": 30}),
                Step("GET", "/events/1", "ann", expect_body={"id": 1, "user_id": 2},
                     expect_db={"Event": {"where": {"note": "given"}, "count": 1}}),
            ),  # fmt: skip
        ),
    ),
    users=racy.USERS,
)


def test_clickhouse_counts_where_equals_and_invariants(ch):
    report = run(ch, "safe", layers="CDX", spec=CH_SPEC)
    assert report.findings == [], report.render()
    notes = "\n".join(report.notes)
    assert ("one_flagged_event_per_user not applicable on ClickHouse (no unique constraints "
            "or transactions)") in notes, notes  # fmt: skip
    assert report.races == []


def test_clickhouse_catches_a_planted_bug(ch):
    report = run(ch, "buggy", layers="CD", spec=CH_SPEC)
    d = of(report, "D")
    assert d and "amount is -70, expected 30" in d[0].details["problems"][0], report.render()
    c = of(report, "C")
    assert {f.details["invariant"] for f in c} == {"amounts_are_not_negative"}, report.render()
