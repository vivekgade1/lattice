"""Expectations no implementation can satisfy (lattice.spec.preconditions): L012 in the lint,
and the "spec problem" line a failing expect_db gets in the check. The shapes are the two
defects that stopped Streak's first build (2026-10-11): a rejected request expected to leave
no row for a token a step before had registered, and an anonymous request expected to leave
a session row nothing had created."""

from __future__ import annotations

from lattice.spec import Await, Scenario, Spec, Step, check
from lattice.spec.lint import lint
from lattice.spec.preconditions import contradictions, unestablished
from tests.spec import racy
from tests.spec.test_lint import codes, trace


def create(*, status: int = 201, db: dict | None = None, user: str | None = "ann") -> Step:
    return Step("POST", "/configs/1/runs", user, expect_status=status, expect_db=db)


ONE = {"runs": {"where": {"id": 1}, "count": 1}}
NONE = {"runs": {"where": {"id": 1}, "count": 0}}


def test_a_refused_request_cannot_remove_the_row_an_earlier_step_expected() -> None:
    sc = Scenario("s", steps=(create(db=ONE), create(status=409, db=NONE)))
    (c,) = contradictions(sc)
    assert (c.table, c.where, c.step_a, c.step_b) == ("runs", {"id": 1}, 1, 2)
    # and through reads and further refusals
    sc = Scenario("s", steps=(create(db=ONE), Step("GET", "/runs/1", "ann", expect_status=200),
                              create(status=409, db=NONE)))  # fmt: skip
    assert [(c.step_a, c.step_b) for c in contradictions(sc)] == [(1, 3)]


def test_a_write_expecting_success_or_an_await_ends_the_pair() -> None:
    ok_write = Step("DELETE", "/runs/1", "ann", expect_status=204)
    sc = Scenario("s", steps=(create(db=ONE), ok_write, create(status=409, db=NONE)))
    assert contradictions(sc) == []
    wait = Await("worker", {"runs": {"where": {"id": 2}, "count": 1}})
    sc = Scenario("s", steps=(create(db=ONE), wait, create(status=409, db=NONE)))
    assert contradictions(sc) == []


def test_a_different_row_is_no_contradiction() -> None:
    other = {"runs": {"where": {"id": 2}, "count": 0}}
    sc = Scenario("s", steps=(create(db=ONE), create(status=409, db=other)))
    assert contradictions(sc) == []


def test_the_lint_reports_l012_with_a_fix() -> None:
    sc = Scenario("twice", steps=(create(db=ONE), create(status=409, db=NONE)))
    spec = Spec(profile=racy.RUNS_PROFILE, scenarios=(sc,), users=racy.USERS)
    t = trace("# T\n\n- R1 A run is queued.\n", {"R1": ["scenario:twice"]})
    (f,) = codes(lint(spec, t), "L012")
    assert f.element == "scenario:twice" and f.rule_id == "R1"
    assert "step 2 expects no row of runs where {'id': 1}" in f.message
    assert "fresh value" in f.fix


def test_unestablished_names_a_row_nothing_before_the_step_can_create() -> None:
    anon = Scenario("a", steps=(Step("POST", "/configs/1/runs", None, expect_status=401,
                                     expect_db=ONE),))  # fmt: skip
    problems = ["db runs where {'id': 1}: expected 1 rows, found 0"]
    (line,) = unestablished(anon, 1, ONE, problems)
    assert line.startswith("spec problem: nothing before this step puts a row in runs")
    # a signed-in step, a write expecting success, an Await or a given row all establish
    ann = Scenario("a", steps=(Step("GET", "/runs/1", "ann", expect_status=404, expect_db=ONE),))
    assert unestablished(ann, 1, ONE, problems) == []
    given = Scenario("a", given={"runs": [{"id": 1}]}, steps=anon.steps)
    assert unestablished(given, 1, ONE, problems) == []
    # a problem that is not "no such row" is left alone
    assert unestablished(anon, 1, ONE, ["db runs where {'id': 1}: status is 'x'"]) == []


def test_check_says_a_failing_expectation_can_never_be_met() -> None:
    never = Scenario("never_created", steps=(
        Step("POST", "/configs/1/runs", None, expect_status=401, expect_db=ONE),))  # fmt: skip
    created = Scenario("created_first", steps=(create(db=ONE),))
    spec = Spec(profile=racy.RUNS_PROFILE, scenarios=(never, created), users=racy.USERS)
    report = check(spec, racy.runs_app(), racy.build_runs_db, layers="D", login=racy.login)
    failed = {f.rule.removeprefix("scenario: ") for f in report.findings}
    assert failed == {"never_created"}
    reason = report.cases["never_created / step 1"]["reason"]
    assert (
        "expected 1 rows, found 0" in reason and "spec problem: nothing before this step" in reason
    )
    assert "created_first / step 1" in report.cases
    assert "spec problem" not in report.cases["created_first / step 1"]["reason"]
