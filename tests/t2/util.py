"""Helpers for the T2b tests: check a program with the T2 test profile, compile and
load its procedures."""

from __future__ import annotations

from textwrap import dedent

from lattice.check import CheckResult, check_source
from lattice.t2 import compile_program, t2_load
from tests.check.t2_support import PROFILE


def checked(src: str, profile=PROFILE) -> CheckResult:
    result = check_source("# lattice: v1\n" + dedent(src).lstrip("\n"), profile)
    assert result.ok, "\n".join(d.render() for d in result.diagnostics)
    return result


def load(src: str, natives=None, profile=PROFILE) -> dict:
    result = checked(src, profile)
    program = result.program
    assert program is not None and program.t2 is not None
    code = compile_program(program)
    assert code is not None
    return t2_load(code.source, natives)
