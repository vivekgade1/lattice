import pytest

from tests.semantics_v11.cases import CASES, run_python


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_python(case):
    assert run_python(case) == case.expect
