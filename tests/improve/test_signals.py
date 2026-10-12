"""Machine codes (lattice/improve/signals.py): every cause code of a build record, and the
finding signal keys (check JSON, gate text, lint, unchecked)."""

from __future__ import annotations

from typing import Any

import pytest

from lattice.improve.events import CAUSES
from lattice.improve.signals import (
    build_cause,
    check_finding_key,
    has_spec_problem,
    lint_key,
    text_finding_key,
    unchecked_key,
)
from lattice.spec import Scenario, Step
from lattice.spec.preconditions import unestablished


def rnd(*targets: str, **extra: Any) -> dict[str, Any]:
    return {
        "check": {"findings": [{"target": t, "title": f"D silent {t}"} for t in targets]},
        **extra,
    }


def rec(status: str, rounds: list[dict[str, Any]], max_rounds: int = 3, **extra: Any):
    return {"status": status, "rounds": rounds, "max_rounds": max_rounds, **extra}


SPEC_LINE = unestablished(
    Scenario("ghost", steps=(Step("GET", "/x", expect_db={"notes": {"count": 1}}),)),
    1,
    {"notes": {"count": 1}},
    ["db notes: expected 1 rows, found 0"],
)[0]


@pytest.mark.parametrize(
    ("record", "stopped_by", "cause"),
    [
        (rec("passed", [rnd()]), None, None),
        (rec("stopped", [rnd("a")]), None, "stopped_by_owner"),
        (rec("stopped", [rnd("a")]), "owner", "stopped_by_owner"),
        (rec("stopped", [rnd("a")]), "studio", "other"),
        (rec("interrupted", [rnd("a")]), None, "other"),
        (rec("gave_up", [rnd("a")], error={"kind": "usage_limit"}), None, "usage_limit"),
        (
            rec(
                "gave_up",
                [
                    rnd("a"),
                    rnd("a"),
                    {"check": {"findings": [{"target": "a", "title": f"D: {SPEC_LINE}"}]}},
                ],
            ),
            None,
            "spec_unsatisfiable",
        ),
        (
            rec(
                "gave_up",
                [
                    rnd("a"),
                    {
                        "check": {
                            "findings": [
                                {
                                    "target": "a",
                                    "title": "D silent a",
                                    "signal_key": "D:spec_problem",
                                }
                            ]
                        }
                    },
                ],
            ),
            None,
            "spec_unsatisfiable",
        ),
        (
            rec("gave_up", [rnd("a", error={"kind": "timeout", "reason": "node_max_tokens"})]),
            None,
            "token_budget",
        ),
        (
            rec(
                "gave_up",
                [rnd("a", status="timeout", error={"kind": "timeout", "reason": "node_timeout"})],
            ),
            None,
            "timeout",
        ),
        (rec("gave_up", [rnd("a", "b"), rnd("b"), rnd("b")]), None, "no_progress"),
        (
            rec("gave_up", [rnd("a"), rnd("x", regressions=["x"], scope_passing=2, scope_total=2)]),
            None,
            "regression",
        ),
        (rec("gave_up", [rnd("a"), rnd("b"), rnd("c")]), None, "repair_exhausted"),
        (rec("gave_up", [rnd("a")], max_rounds=3, error={"kind": "internal"}), None, "other"),
    ],
)
def test_build_cause(record, stopped_by, cause) -> None:
    got = build_cause(record, stopped_by=stopped_by)
    assert got == cause
    assert got is None or got in CAUSES


def test_check_finding_keys() -> None:
    assert check_finding_key({"layer": "A", "code": "G012"}) == "A:G012"
    d = {
        "layer": "D",
        "verdict": "silent",
        "details": {"problems": ["status: expected 201, got 404"]},
    }
    assert check_finding_key(d) == "D:status"
    d["details"]["problems"] = ["db notes: expected 1 rows, found 0", SPEC_LINE]
    assert has_spec_problem(d["details"]["problems"]) and check_finding_key(d) == "D:spec_problem"
    d["details"]["problems"] = ["body: expected {} within []"]
    assert check_finding_key(d) == "D:body"
    assert check_finding_key({"layer": "X", "details": {}}) == "X:race"
    assert check_finding_key({"layer": "C"}) == "C:invariant"
    assert check_finding_key({"layer": "R"}) == "R:rule"
    assert (
        check_finding_key({"layer": "B", "verdict": "loud", "details": {"error": "x"}}) == "B:error"
    )
    assert check_finding_key({"layer": "D", "verdict": "loud"}) == "D:loud"


def test_text_lint_and_unchecked_keys() -> None:
    assert (
        text_finding_key("FAIL writing.txt is missing (scenario:create_a_note)") == "gate:finding"
    )
    assert text_finding_key("[D silent] POST /x (s / step 1): status: got 500") == "D:status"
    assert text_finding_key(f"D silent s / step 2: {SPEC_LINE}") == "D:spec_problem"
    assert text_finding_key("make exited 2", exit_code=2, fallback=True) == "gate:exit_2"
    assert lint_key("L012") == "lint:L012"
    assert unchecked_key("R6: X-Client-Version -- a step cannot send request headers") == (
        "unchecked:request_header"
    )
    assert unchecked_key("R136: the PushSender stub records nothing") == (
        "unchecked:stub_observability"
    )
    assert unchecked_key("R75 is web UI, not observable over HTTP") == "unchecked:web_ui"
    assert unchecked_key("something else entirely") == "unchecked:free_text"
