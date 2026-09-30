"""RE2-only regular expressions (spec 2026-09-28 go-target §3.1, L926) and ASCII
character classes (`\\d \\w \\s \\b` compiled with `re.ASCII` on the Python target, so
Python and Go agree)."""

from __future__ import annotations

from textwrap import dedent

import pytest

from lattice.check import check_source
from lattice.check.api_info import ParamType, pattern_problem
from lattice.check.re2 import re2_problem
from lattice.typesys import Scalar
from tests.check.t2_support import PROFILE


@pytest.mark.parametrize(
    "pattern",
    [
        r"^[a-z0-9_]+$",
        r"(?P<user>\w+)@(?P<host>\w+)",
        r"\d{3}-\d{4}",
        r"a|b|c",
        r"(ab)+?",
    ],
)
def test_accepted(pattern):
    assert re2_problem(pattern) is None


@pytest.mark.parametrize(
    ("pattern", "word"),
    [
        (r"foo(?=bar)", "lookahead"),
        (r"(?<!x)y", "lookbehind"),
        (r"(a)\1", "backreference"),
        (r"(?P<q>')x(?P=q)", "backreference"),
        (r"(a)?(?(1)b|c)", "conditional"),
        (r"a++", "possessive"),
        (r"(?>ab)", "atomic"),
    ],
)
def test_rejected(pattern, word):
    reason = re2_problem(pattern)
    assert reason is not None and word in reason


def test_invalid_pattern_is_not_reported_here():
    """A pattern that does not even compile is reported elsewhere (L916/L601)."""
    assert re2_problem("[a-z") is None


def test_l926_in_procedure():
    src = dedent(
        """\
        # lattice: v1
        import re

        @procedure
        def ok(s: Str) -> Bool:
            return re.fullmatch(r"(?<=a)b", s) is not None
        """
    )
    result = check_source(src, PROFILE)
    assert "L926" in [d.code for d in result.diagnostics]


def test_l926_at_endpoint_pattern():
    src = dedent(
        """\
        # lattice: v1
        @endpoint("GET", "/users", auth=Admin)
        def find(user: CurrentUser, q: Str(pattern="(?<=a)b")) -> One | NotFound:
            users = fetch(Users)
            found = where(users, col("email") == q)
            row = one(found, else_=NotFound("no such user"))
            shown = select(row, cols=["id", "email"])
            return shown
        """
    )
    result = check_source(src, PROFILE)
    assert "L926" in [d.code for d in result.diagnostics]


def test_digit_class_is_ascii():
    from lattice.t2 import T2Fuel, compile_program, t2_load
    from tests.check.t2_support import PROFILE as T2_PROFILE

    src = dedent(
        """\
        # lattice: v1
        import re

        @procedure
        def digits(s: Str) -> Bool:
            return re.fullmatch(r"\\d+", s) is not None
        """
    )
    result = check_source(src, T2_PROFILE)
    assert result.ok, "\n".join(d.render() for d in result.diagnostics)
    program = result.program
    assert program is not None and program.t2 is not None
    code = compile_program(program)
    assert code is not None
    ns = t2_load(code.source)
    assert ns["digits"](T2Fuel(10_000), "123") is True
    assert ns["digits"](T2Fuel(10_000), "١٢٣") is False  # Arabic-Indic digits


def test_pattern_problem_is_ascii():
    """The runtime's Str(pattern=) match (lattice.api.runtime._constrain, via
    pattern_problem) is ASCII, like the procedure `re` module."""
    spec = ParamType(Scalar.STR, pattern=r"\d+")
    assert pattern_problem(spec, "123") is None
    assert pattern_problem(spec, "١٢٣") is not None


def test_a_pattern_that_cannot_be_ascii_is_l601_not_a_crash():
    """A pattern= is matched with re.ASCII, so it is compiled with it at check time:
    `(?u)` (ASCII and UNICODE flags are incompatible) is L601, and the checker no longer
    crashes on its default."""
    src = dedent(
        """\
        # lattice: v1
        @endpoint("GET", "/users", auth=Admin)
        def find(user: CurrentUser, q: Str(pattern="(?u)[a-z]+") = "a") -> One | NotFound:
            users = fetch(Users)
            found = where(users, col("email") == q)
            row = one(found, else_=NotFound("no such user"))
            shown = select(row, cols=["id", "email"])
            return shown
        """
    )
    for target in ("python", "go"):
        result = check_source(src, PROFILE, target=target)
        l601 = [d for d in result.diagnostics if d.code == "L601"]
        assert l601 and "not a valid regular expression" in l601[0].message, target
