"""``lattice.spec.fidelity`` (Phase 2 W3): an app's recorded scenarios replayed through
its runtime, each step's answer checked and its status class predicted by the spec's
rule oracle. The dev apps' suites run in ``tests/importer/test_phase2_dev_apps.py``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lattice.spec import Scenario, Step
from lattice.spec.fidelity import Caller, predict, run_fidelity, status_classes
from tests.spec.test_check_runtime import _runtime, _spec, make_app, make_db

pytest.importorskip("flask")

ADMIN, ALICE = Caller("Admin"), Caller("Customer")


def test_status_classes():
    assert status_classes(201) == {"ok"}
    assert status_classes(403) == {"unauthenticated", "forbidden", "not_owned"}
    assert status_classes(404) == {"not_found", "not_owned", "method"}
    assert status_classes(503) == {"error"}


def test_the_oracle_predicts_from_the_caller_and_the_contract():
    spec = _spec()
    step = Step("GET", "/admin-only")
    assert predict(spec, step, None) == {"unauthenticated"}
    assert predict(spec, step, ALICE) == {"forbidden"}
    assert predict(spec, step, Caller("Customer", active=False)) == {"unauthenticated",
                                                                      "forbidden"}  # fmt: skip
    assert "ok" in predict(spec, step, ADMIN)
    assert predict(spec, Step("GET", "/nowhere"), ADMIN) == {"method", "not_found"}
    assert "ok" in predict(spec, Step("GET", "/docs"), None, framework=[("GET", "/docs")])


def _headers(client: Any, handle: str) -> dict[str, str]:
    return {"X-Token": f"tok-{handle}"}


def test_a_suite_replays_through_the_runtime(tmp_path: Path):
    spec = _spec()
    report = run_fidelity(spec, spec.scenarios, make_app, make_db, _headers,
                          runtime=_runtime([]), user_ids={"admin": 1, "alice": 2})  # fmt: skip
    assert report.passed, report.to_dict()
    assert len(report.steps) == 3


def test_a_wrong_record_or_a_wrong_prediction_fails(tmp_path: Path):
    spec = _spec()
    wrong = Scenario("alice is the admin", steps=(
        Step("GET", "/admin-only", user="alice", expect_body={"ok": True}),
    ))  # fmt: skip
    report = run_fidelity(spec, (wrong,), make_app, make_db, _headers, runtime=_runtime([]),
                          user_ids={"admin": 1, "alice": 2})  # fmt: skip
    assert not report.passed
    step = report.steps[0]
    assert not step.answer_ok and step.oracle_ok  # the app refuses alice, as predicted
    data = report.to_dict()
    assert data["failed"][0]["problems"][0].startswith("status 403")


def test_no_scenarios_is_the_specs_validity_only():
    report = run_fidelity(_spec(), (), make_app, make_db, _headers)
    assert report.passed and report.notes
