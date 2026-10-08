"""Layer X (lattice.spec.race): the concurrency layer races the scenario steps that write
a count bound's or a machine's table, on toy apps (tests/spec/racy.py)."""

from __future__ import annotations

import pytest

from lattice.spec import Report, check
from lattice.spec.__main__ import main
from lattice.spec.race import machine_violations, reachable
from tests.spec import racy


def run_x(spec, factory, db, **kw) -> Report:
    kw.setdefault("layers", "X")
    return check(spec, factory, db, login=racy.login, **kw)


def x_findings(report: Report) -> list:
    return [f for f in report.findings if f.layer == "X"]


# ---- (a) a racy create refutes the count bound ---------------------------------------------


def test_a_racy_create_refutes_the_count_bound():
    report = run_x(racy.RUNS_SPEC, racy.runs_app(safe_create=False), racy.build_runs_db)
    found = x_findings(report)
    rules = {f.details["rule_id"] for f in found}
    assert "one_live_run_per_config" in rules, report.render()
    f = next(f for f in found if f.details["rule_id"] == "one_live_run_per_config")
    assert f.verdict == "silent"
    assert f.endpoint == "POST /configs/1/runs"
    assert f.details["k"] in (2, 12)
    assert f.details["rows"] and f.details["rows"][0]["count"] > 1
    assert f.key.startswith("X:POST /configs/1/runs:one_live_run_per_config/1")
    assert f.key.endswith(":one_live_run_per_config")
    # the machine is not blamed: every run is still queued
    assert "Run.status" not in rules, report.render()
    text = report.render()
    assert "rule (X: " in text and "A config has at most one queued or running run." in text


# ---- (b) the same app made safe: no X finding ----------------------------------------------


def test_b_the_safe_app_has_no_x_finding_and_is_only_not_refuted():
    report = run_x(racy.RUNS_SPEC, racy.runs_app(), racy.build_runs_db)
    assert x_findings(report) == [], report.render()
    summary = " ".join(n for n in report.notes if n.startswith("layer X"))
    assert "not refuted" in summary and "not verified" in summary
    assert not any(r["outcome"] == "verified" for r in report.races)
    assert report.races and all(r["outcome"] == "not refuted" for r in report.races)


# ---- (c) a status overwrite refutes the machine; the conditional update fixes it -----------


def test_c_a_worker_overwriting_a_cancel_refutes_the_machine():
    report = run_x(racy.RUNS_SPEC, racy.runs_app(safe_finish=False), racy.build_runs_db)
    found = [f for f in x_findings(report) if f.details["rule_id"] == "Run.status"]
    assert found, report.render()
    f = found[0]
    assert f.case == "a_cancelled_run_is_never_executed/2+3"
    assert f.details["k"] == 2
    bad = f.details["rows"][0]
    assert bad["promised"] == "cancelled" and bad["final"] == "succeeded"
    assert [s["path"] for s in f.details["steps"]] == ["/runs/1/cancel", "/worker/drain"]


def test_c_the_conditional_update_gives_no_finding():
    report = run_x(racy.RUNS_SPEC, racy.runs_app(safe_finish=True), racy.build_runs_db)
    assert [f for f in x_findings(report) if f.details["rule_id"] == "Run.status"] == []


# ---- (d) no false positive on an app with no shared rows -----------------------------------


def test_d_no_false_positive_without_shared_rows():
    report = run_x(racy.NOTES_SPEC, racy.notes_app, racy.build_notes_db, layers="CDX")
    assert report.findings == [], report.render()
    assert report.races


# ---- (e) determinism -----------------------------------------------------------------------


def test_e_the_same_spec_and_seed_give_the_same_races():
    a = run_x(racy.RUNS_SPEC, racy.runs_app(), racy.build_runs_db, x_repeats=1)
    b = run_x(racy.RUNS_SPEC, racy.runs_app(), racy.build_runs_db, x_repeats=1)
    assert [r["race"] for r in a.races] == [r["race"] for r in b.races]
    races = [r["race"] for r in a.races]
    # self-races of every racy step, and pairs with the next write of the same table
    assert "one_live_run_per_config/1" in races
    assert "a_cancelled_run_is_never_executed/2+3" in races
    # a GET step is never raced
    assert not any(r.endswith("/3") and r.startswith("the_worker") for r in races)


# ---- selection, budget, options ------------------------------------------------------------


def test_identical_races_are_run_once():
    report = run_x(racy.RUNS_SPEC, racy.runs_app(), racy.build_runs_db, x_repeats=1)
    races = [r["race"] for r in report.races]
    # every scenario's first step is the same create on the same seed: one self-race
    assert sum(r.endswith("/1") for r in races) == 1


def test_the_budget_caps_the_races_and_reports_the_rest():
    report = run_x(racy.RUNS_SPEC, racy.runs_app(), racy.build_runs_db, x_budget=2,
                   x_repeats=1)  # fmt: skip
    assert len(report.races) == 2
    assert any("skipped" in n and "budget" in n for n in report.notes), report.notes


def test_x_is_opt_in_and_the_default_layers_are_abcd():
    report = check(racy.RUNS_SPEC, racy.runs_app(safe_create=False), racy.build_runs_db,
                   login=racy.login, layers="D")  # fmt: skip
    assert "X" not in report.layers
    assert x_findings(report) == []


def _overlapping(error: Exception):
    """An app factory whose ``/worker/drain`` raises ``error`` when two drains overlap
    (on any app instance of the process), and answers normally one at a time."""
    import asyncio
    import threading

    lock = threading.Lock()
    state = {"n": 0}

    def factory(db_path):
        app = racy.runs_app()(db_path)

        @app.middleware("http")
        async def crash_when_overlapping(request, call_next):
            if request.url.path != "/worker/drain":
                return await call_next(request)
            with lock:
                state["n"] += 1
            try:
                await asyncio.sleep(0.05)
                with lock:
                    overlapping = state["n"] > 1
                if overlapping:
                    raise error
                return await call_next(request)
            finally:
                with lock:
                    state["n"] -= 1

        return app

    return factory


def test_an_app_failing_under_concurrency_is_a_loud_5xx_finding():
    factory = _overlapping(RuntimeError("boom under load"))
    report = run_x(racy.RUNS_SPEC, factory, racy.build_runs_db, x_repeats=1)
    loud = [f for f in x_findings(report) if f.verdict == "loud"]
    assert loud, report.render()
    assert loud[0].details["rule_id"] == "no_server_error_under_concurrency"
    assert "boom under load" in str(loud[0].details["answers"])
    assert loud[0].key.endswith(":no_server_error_under_concurrency")


def test_a_step_failing_sequentially_too_is_not_a_race_finding():
    def always_fails(db_path):
        app = racy.runs_app()(db_path)

        @app.middleware("http")
        async def crash(request, call_next):
            # ann's cancel always fails; ben's works, so the cancel endpoint writes runs
            # and ann's cancel is still raced against the create before it
            if request.url.path == "/runs/1/cancel" and "tok-ann" in str(
                request.headers.get("authorization")
            ):
                raise RuntimeError("always")
            return await call_next(request)

        return app

    report = run_x(racy.RUNS_SPEC, always_fails, racy.build_runs_db, x_repeats=1)
    assert "a_cancelled_run_is_never_executed/1+2" in [r["race"] for r in report.races]
    assert not [f for f in x_findings(report) if f.verdict == "loud"], report.render()


def test_an_engine_that_cannot_share_threads_is_a_note_not_a_finding():
    import sqlite3

    error = sqlite3.ProgrammingError(
        "SQLite objects created in a thread can only be used in that same thread."
    )
    report = run_x(racy.RUNS_SPEC, _overlapping(error), racy.build_runs_db, x_repeats=1)
    assert not [f for f in x_findings(report) if f.verdict == "loud"], report.render()
    assert any("same thread" in n for n in report.notes), report.notes


def test_cli_accepts_layer_x(capsys):
    code = main([
        "check", "--spec", "tests.spec.racy:RUNS_SPEC", "--app", "tests.spec.racy:SAFE_APP",
        "--db", "tests.spec.racy:build_runs_db", "--login", "tests.spec.racy:login",
        "--layers", "X", "--x-k", "4", "--x-repeats", "1", "--x-budget", "3",
    ])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0, out
    assert "layer X" in out


# ---- the machine rule ----------------------------------------------------------------------


def test_reachable_and_the_machine_rule():
    m = racy.RUN_MACHINE
    assert reachable(m, "queued") == {"queued", "running", "succeeded", "cancelled"}
    assert reachable(m, "cancelled") == {"cancelled"}
    before = {1: "running"}
    after = {1: "succeeded"}
    # a promise of cancelled, then succeeded: refuted
    bad = machine_violations(m, before, after, promises=[(1, "cancelled")])
    assert bad and bad[0]["final"] == "succeeded"
    # pre-race running -> succeeded, promised running: fine
    assert machine_violations(m, before, after, promises=[(1, "running")]) == []
    # a new row must start from an initial state
    assert machine_violations(m, {}, {2: "succeeded"}, promises=[]) == []
    assert machine_violations(racy.RUN_MACHINE, {1: "cancelled"}, {1: "queued"}, promises=[])


@pytest.mark.parametrize("k", [3])
def test_x_k_sets_the_self_race_width(k):
    report = run_x(racy.RUNS_SPEC, racy.runs_app(safe_create=False), racy.build_runs_db,
                   x_k=k, x_repeats=2)  # fmt: skip
    f = next(f for f in x_findings(report) if f.details["rule_id"] == "one_live_run_per_config"
             and "+" not in f.case)  # fmt: skip
    assert f.details["k"] == k
