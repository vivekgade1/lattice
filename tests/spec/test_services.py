"""Background services (lattice.spec.services): a scenario's ``Await`` step starts a
service the spec names, through the harness's ``ServiceCmd``, polls the database until
its ``expect_db`` holds, and always stops the service. On the runs app of
tests/spec/racy.py (in process) and on a stdlib HTTP copy of it (a ``ProcessApp``)."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from lattice.spec import (
    Await,
    CheckError,
    Report,
    Scenario,
    Service,
    ServiceCmd,
    Spec,
    Step,
    check,
    requirements_markdown,
    validate,
)
from lattice.spec.__main__ import main
from lattice.spec.process import ProcessApp, live_processes, process_runtime
from lattice.spec.services import live_services
from tests.spec import racy

HERE = Path(__file__).parent
WORKER = HERE / "services_worker.py"
HTTP_APP = HERE / "services_http.py"

DONE = {"runs": {"where": {"id": 1}, "equals": {"status": "succeeded"}}}
SCENARIO = Scenario(
    "the_worker_finishes_a_queued_run",
    steps=(
        racy.CREATE,
        Await("worker", DONE, within_s=5, note="the worker drains the queue"),
        Step("GET", "/runs/1", "ann", expect_body={"id": 1, "status": "succeeded"}),
    ),
)
SPEC = Spec(
    profile=racy.RUNS_PROFILE,
    machines=(racy.RUN_MACHINE,),
    invariants=(racy.ONE_LIVE,),
    scenarios=(SCENARIO,),
    users=racy.USERS,
    services=(Service("worker", "drains queued runs"),),
)


def services(mode: str = "work", pidfile: Path | None = None, stop_timeout: float = 2.0):
    env = {"WORKER_MODE": mode}
    if pidfile is not None:
        env["WORKER_PIDFILE"] = str(pidfile)

    def worker(db: Path) -> ServiceCmd:
        return ServiceCmd([sys.executable, str(WORKER), str(db)], env=env,
                          stop_timeout=stop_timeout)  # fmt: skip

    return {"worker": worker}


# for the CLI test
CLI_SERVICES = services()


def run(spec: Spec = SPEC, layers: str = "D", app: Any = None, **kw: Any) -> Report:
    kw.setdefault("services", services())
    return check(spec, app or racy.runs_app(), racy.build_runs_db, layers=layers,
                 login=racy.login, **kw)  # fmt: skip


def of(report: Report, layer: str) -> list:
    return [f for f in report.findings if f.layer == layer]


def _gone(pid: int, wait: float = 3.0) -> bool:
    deadline = time.monotonic() + wait
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)


def _pids(pidfile: Path) -> list[int]:
    return [int(x) for x in pidfile.read_text().split()]


@pytest.fixture(autouse=True)
def nothing_left_running():
    yield
    assert live_services() == [], "a service outlived its check"
    assert live_processes() == [], "a process app outlived its check"


# ---- the model ------------------------------------------------------------------------------


def test_await_is_frozen_hashable_and_json_serialisable():
    step = Await("worker", {"runs": {"count": 1}}, within_s=2)
    assert hash(step) == hash(Await("worker", {"runs": {"count": 2}}, within_s=2))
    with pytest.raises(AttributeError):
        step.service = "x"  # type: ignore[misc]
    import json

    assert json.loads(json.dumps(step.expect_db)) == {"runs": {"count": 1}}


def test_spec_services_normalise():
    names = Spec(profile=racy.RUNS_PROFILE, services="worker").services
    assert names == (Service("worker"),)
    spec = Spec(profile=racy.RUNS_PROFILE, services={"worker": "drains", "scheduler": ""})
    assert spec.service_names() == ("worker", "scheduler")
    assert spec.services[0].doc == "drains"
    assert Spec(profile=racy.RUNS_PROFILE, services=["a", Service("b")]).service_names() == (
        "a",
        "b",
    )


# ---- validation -----------------------------------------------------------------------------


def _codes(spec: Spec) -> list[str]:
    return [p.code for p in validate(spec)]


def test_the_spec_is_valid():
    assert validate(SPEC) == []


def test_s024_an_await_names_an_undeclared_service():
    spec = replace(SPEC, services=())
    (problem,) = validate(spec)
    assert problem.code == "S024" and "'worker'" in problem.message
    spec = replace(SPEC, scenarios=(Scenario("s", steps=(Await("scheduler", DONE, 1),)),))
    assert _codes(spec) == ["S024"]


@pytest.mark.parametrize(
    "step",
    [
        Await("worker", {}, within_s=1),
        Await("worker", DONE, within_s=0),
        Await("worker", DONE, within_s=-1.5),
        Await("worker", DONE, within_s="5"),  # type: ignore[arg-type]
    ],
)
def test_s025_a_malformed_await(step: Await):
    spec = replace(SPEC, scenarios=(Scenario("s", steps=(step,)),))
    assert _codes(spec) == ["S025"]


def test_s025_a_service_declared_twice_or_unnamed():
    assert _codes(replace(SPEC, services=("worker", "worker"))) == ["S025"]
    assert _codes(replace(SPEC, services=("worker", ""))) == ["S025"]


def test_an_await_expect_db_is_checked_like_a_steps():
    bad = Await("worker", {"nope": {"count": 1}, "runs": {"where": {"x": 1}, "count": 1}}, 1)
    spec = replace(SPEC, scenarios=(Scenario("s", steps=(bad,)),))
    assert _codes(spec) == ["S002", "S003"]
    shape = Await("worker", {"runs": {"where": {"id": 1}}}, 1)
    assert _codes(replace(SPEC, scenarios=(Scenario("s", steps=(shape,)),))) == ["S012"]


def test_a_harness_missing_a_declared_service_is_a_check_error():
    with pytest.raises(CheckError, match="worker"):
        run(services={})
    with pytest.raises(CheckError, match="worker"):
        run(services={"scheduler": services()["worker"]})


# ---- layer D, in process --------------------------------------------------------------------


def test_an_await_passes_when_the_service_does_its_job(tmp_path):
    pidfile = tmp_path / "pids"
    report = run(services=services(pidfile=pidfile))
    assert report.verdict == "pass", report.render()
    case = report.cases["the_worker_finishes_a_queued_run / step 2"]
    assert case["verdict"] == "correct" and case["endpoint"] == "service worker"
    # the later step ran, and saw what the service did
    assert report.cases["the_worker_finishes_a_queued_run / step 3"]["verdict"] == "correct"
    assert all(_gone(p) for p in _pids(pidfile))


def test_an_await_fails_when_the_service_does_nothing(tmp_path):
    pidfile = tmp_path / "pids"
    spec = replace(SPEC, scenarios=(replace(SCENARIO, steps=(
        racy.CREATE, Await("worker", DONE, within_s=0.6), *SCENARIO.steps[2:])),))  # fmt: skip
    started = time.monotonic()
    report = run(spec, services=services("noop", pidfile))
    assert time.monotonic() - started < 10
    (finding,) = of(report, "D")
    assert finding.verdict == "silent"
    assert finding.endpoint == "service worker"
    assert finding.case == "the_worker_finishes_a_queued_run / step 2"
    head = finding.details["problems"][0]
    assert head.startswith(
        "service worker did not bring about runs where {'id': 1} equals "
        "{'status': 'succeeded'} within 0.6s"
    ), head
    assert "last observed: {\"runs where {'id': 1}\": 1}" in head
    assert finding.details["observed"] == {"runs where {'id': 1}": 1}
    assert any("status is 'queued'" in p for p in finding.details["problems"])
    # the scenario stopped at the failing step
    assert "the_worker_finishes_a_queued_run / step 3" not in report.cases
    text = report.render()
    assert "[D silent] service worker (the_worker_finishes_a_queued_run / step 2)" in text
    assert "service worker did not bring about" in text
    assert all(_gone(p) for p in _pids(pidfile))
    # the timings put the wait where it went: the Await ran out its within_s
    (s,) = report.timings["scenarios"]
    assert s["awaits"] == 1 and 0.6 <= s["await_s"] <= s["steps_s"] + 1e-3


def test_a_service_ignoring_sigterm_is_killed(tmp_path):
    pidfile = tmp_path / "pids"
    spec = replace(SPEC, scenarios=(replace(SCENARIO, steps=(
        racy.CREATE, Await("worker", DONE, within_s=1.0))),))  # fmt: skip
    report = run(spec, services=services("stubborn", pidfile, stop_timeout=0.3))
    assert len(of(report, "D")) == 1
    assert all(_gone(p) for p in _pids(pidfile))


def test_the_whole_process_group_is_stopped(tmp_path):
    pidfile = tmp_path / "pids"
    report = run(services=services("spawn", pidfile, stop_timeout=0.5))
    assert report.verdict == "pass", report.render()
    pids = _pids(pidfile)
    assert len(pids) == 2
    assert all(_gone(p) for p in pids), "the service's child outlived it"


def test_a_crashing_service_is_loud_with_its_stderr():
    report = run(services=services("crash"))
    (finding,) = of(report, "D")
    assert finding.verdict == "loud"
    assert finding.details["exit_code"] == 3
    assert "cannot open the queue" in finding.details["stderr"]
    assert finding.details["problems"][0].startswith("service worker exited with code 3")


def test_a_service_that_cannot_start_is_loud():
    def missing(db: Path) -> ServiceCmd:
        return ServiceCmd([str(HERE / "no-such-worker")])

    report = run(services={"worker": missing})
    (finding,) = of(report, "D")
    assert finding.verdict == "loud" and "could not start" in finding.details["problems"][0]


def test_the_service_is_stopped_when_the_wait_raises(tmp_path, monkeypatch):
    import importlib

    check_module = importlib.import_module("lattice.spec.check")
    pidfile = tmp_path / "pids"

    def broken(*args: Any, **kw: Any) -> list[str]:
        deadline = time.monotonic() + 10  # once the service is up
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        raise RuntimeError("the poll broke")

    monkeypatch.setattr(check_module, "_expect_db", broken)
    spec = replace(SPEC, scenarios=(replace(SCENARIO, steps=(
        Step("GET", "/runs/9", "ann", expect_status=404),
        Await("worker", DONE, within_s=2))),))  # fmt: skip
    report = run(spec, services=services("work", pidfile))
    (finding,) = of(report, "D")
    assert finding.verdict == "loud" and "the poll broke" in finding.details["problems"][0]
    assert all(_gone(p) for p in _pids(pidfile))


def test_steps_may_follow_an_await_and_await_again():
    spec = replace(SPEC, scenarios=(Scenario("two_waits", steps=(
        racy.CREATE,
        Await("worker", DONE, within_s=5),
        Step("POST", "/configs/1/runs", "ben", expect_status=201),
        Await("worker", {"runs": {"where": {"status": "succeeded"}, "count": 2}}, within_s=5),
        Step("GET", "/runs/2", "ben", expect_body={"id": 2, "status": "succeeded"}),
    )),))  # fmt: skip
    report = run(spec)
    assert report.verdict == "pass", report.render()
    assert len([c for c in report.cases if c.startswith("two_waits")]) == 5


# ---- layer C after an Await -----------------------------------------------------------------


def test_layer_c_checks_invariants_on_what_the_service_wrote():
    spec = replace(SPEC, scenarios=(Scenario("violating", steps=(
        racy.CREATE,
        Await("worker", {"runs": {"where": {"config_id": 2}, "count": 2}}, within_s=5),
    )),))  # fmt: skip
    # no unique index on the live runs: the service can break the invariant
    report = run(spec, layers="CD", app=racy.runs_app(safe_create=False),
                 services=services("violate"))  # fmt: skip
    assert of(report, "D") == [], report.render()
    (finding,) = of(report, "C")
    assert finding.details["invariant"] == "one_live_run_per_config"
    assert finding.case == "violating / step 2"
    assert finding.endpoint == "service worker"
    assert finding.request == {"await": "worker", "within_s": 5,
                               "expect_db": {"runs": {"where": {"config_id": 2}, "count": 2}}}


def test_layer_c_is_quiet_after_a_well_behaved_service():
    report = run(layers="CD")
    assert report.verdict == "pass", report.render()


# ---- layer X ignores Await ------------------------------------------------------------------


def test_layer_x_never_races_an_await(tmp_path):
    pidfile = tmp_path / "pids"
    report = run(layers="X", services=services(pidfile=pidfile), x_k=3, x_repeats=1)
    assert not pidfile.exists(), "layer X started the service"
    assert report.races, report.render()
    for race in report.races:
        assert all(s["step"] == 1 for s in race["steps"]), race
    assert any("step 2 awaits the worker service" in n for n in report.notes), report.notes


def test_layer_x_does_not_need_the_harness():
    report = check(SPEC, racy.runs_app(), racy.build_runs_db, layers="X", login=racy.login,
                   x_k=3, x_repeats=1)  # fmt: skip
    assert of(report, "X") == [], report.render()


# ---- the requirements view ------------------------------------------------------------------


def test_requirements_markdown_renders_an_await_as_prose():
    text = requirements_markdown(SPEC, title="Runs")
    assert "## Background services" in text and "- **worker**: drains queued runs" in text
    assert (
        "  - the worker service brings about runs where {'id': 1} equals "
        "{'status': 'succeeded'} within 5s (the worker drains the queue)"
    ) in text


# ---- the process runtime --------------------------------------------------------------------


def make_process_app(db_path: Path) -> ProcessApp:
    return ProcessApp([sys.executable, str(HTTP_APP)], db_path=db_path, db_env="DB_PATH",
                      startup_timeout=15, stop_timeout=2)  # fmt: skip


def test_an_await_through_a_process_app(tmp_path):
    pidfile = tmp_path / "pids"
    runtime = process_runtime(services=services(pidfile=pidfile))
    report = check(SPEC, make_process_app, racy.build_runs_db, layers="CD", login=racy.login,
                   runtime=runtime)  # fmt: skip
    assert report.verdict == "pass", report.render()
    assert report.cases["the_worker_finishes_a_queued_run / step 3"]["verdict"] == "correct"
    assert all(_gone(p) for p in _pids(pidfile))


def test_a_failing_await_through_a_process_app(tmp_path):
    pidfile = tmp_path / "pids"
    spec = replace(SPEC, scenarios=(replace(SCENARIO, steps=(
        racy.CREATE, Await("worker", DONE, within_s=1.0))),))  # fmt: skip
    runtime = process_runtime(services=services("noop", pidfile))
    report = check(spec, make_process_app, racy.build_runs_db, layers="D", login=racy.login,
                   runtime=runtime)  # fmt: skip
    (finding,) = of(report, "D")
    assert "service worker did not bring about" in finding.details["problems"][0]
    assert all(_gone(p) for p in _pids(pidfile))


def test_check_services_are_laid_over_the_runtimes():
    runtime = process_runtime(services={"worker": services("noop")["worker"]})
    report = check(SPEC, make_process_app, racy.build_runs_db, layers="D", login=racy.login,
                   runtime=runtime, services=services())  # fmt: skip
    assert report.verdict == "pass", report.render()


# ---- the CLI --------------------------------------------------------------------------------


def _cli(*extra: str) -> list[str]:
    return ["check", "--spec", "tests.spec.test_services:SPEC", "--app",
            "tests.spec.racy:SAFE_APP", "--db", "tests.spec.racy:build_runs_db", "--layers",
            "D", "--login", "tests.spec.racy:login", *extra]  # fmt: skip


def test_the_cli_takes_services(capsys):
    assert main(_cli("--services", "tests.spec.test_services:CLI_SERVICES")) == 0
    assert "PASS" in capsys.readouterr().out


def test_the_cli_without_services_cannot_run(capsys):
    assert main(_cli()) == 2
    assert "worker" in capsys.readouterr().err
