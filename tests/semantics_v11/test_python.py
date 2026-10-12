import pytest

from tests.semantics_v11.cases import CASES, Err, Exc, run_python
from tests.semantics_v11.strict import strict


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_python(case):
    got = run_python(case)
    assert got == case.expect
    if not isinstance(case.expect, Err | Exc):
        # strictly: an int is not a float (1 is not 1.0), -0.0 is not 0.0
        assert strict(got) == strict(case.expect)
