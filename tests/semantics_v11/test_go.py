"""The shared v1.1 semantics suite on the Go target (plan 5a Task 8): every case the
checker accepts for Go must give exactly the expected value on Go."""

import pytest

from lattice.check import check_source
from tests.check.t2_support import PROFILE
from tests.semantics_v11.cases import CASES, semantics_cases_missing_on
from tests.semantics_v11.gorun import (
    expect_holds,
    python_fuel_left,
    run_go_detailed,
    run_python_budget,
)

GO_CASES = [c for c in CASES if check_source(c.source_with_header, PROFILE, target="go").ok]


@pytest.fixture(scope="module")
def go_run():
    return run_go_detailed(GO_CASES)


@pytest.mark.parametrize("case", GO_CASES, ids=lambda c: c.name)
def test_go_matches_expectation(case, go_run):
    """Strictly (`gorun.strict`): the same types, float bits, orders and error
    messages as Python, and the case's expected value."""
    got = go_run[0][case.name]
    assert got == run_python_budget(case, case.budget)[0]
    assert expect_holds(got, case.expect), (got, case.expect)


@pytest.mark.parametrize("case", GO_CASES, ids=lambda c: c.name)
def test_go_charges_the_same_fuel(case, go_run):
    """Every step and size charge happens on Go exactly as on Python: the fuel left
    at the end is the same (-1 once exhausted)."""
    assert go_run[1][case.name] == python_fuel_left(case)


def test_coverage_floor():
    """Each Go task raises this floor; Task 10 ends at 100%. Task 9 (strings and
    regular expressions, fix round 1): 220 of 284 cases."""
    assert len(GO_CASES) >= 220


def test_only_l892_gated_cases_are_missing_on_go():
    """Nothing is skipped silently: a case Go does not run is one the checker refuses
    for Go with L892 alone (a construct the Go target does not implement yet), and
    every other case is in GO_CASES."""
    missing = semantics_cases_missing_on("go")
    assert set(missing) == {c.name for c in CASES} - {c.name for c in GO_CASES}
    for case in CASES:
        if case.name not in missing:
            continue
        result = check_source(case.source_with_header, PROFILE, target="go")
        assert result.diagnostics, case.name
        assert {d.code for d in result.diagnostics} == {"L892"}, (case.name, result.diagnostics)
        assert check_source(case.source_with_header, PROFILE).ok, case.name
