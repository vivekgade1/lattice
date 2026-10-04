"""The Lattice spec: an application's profile plus state machines, invariants,
scenarios, endpoint programs and unchecked requirements, validated against itself and
compiled to the checks the checker runs (direction draft §4)."""

from __future__ import annotations

from lattice.spec.check import (
    CaseContext,
    CheckError,
    InvariantMonitor,
    cases_for,
    check,
    spec_policy,
)
from lattice.spec.invariants import ALLOWED_FORMS, InvariantError, compile_invariant
from lattice.spec.model import (
    Invariant,
    Machine,
    Scenario,
    Spec,
    Step,
    Unchecked,
    resolve_table,
    tables_of,
)
from lattice.spec.report import Accepted, CheckFinding, Report
from lattice.spec.validate import CODES, SpecProblem, validate

__all__ = [
    "ALLOWED_FORMS",
    "Accepted",
    "CODES",
    "CaseContext",
    "CheckError",
    "CheckFinding",
    "Invariant",
    "InvariantError",
    "InvariantMonitor",
    "Machine",
    "Report",
    "Scenario",
    "Spec",
    "SpecProblem",
    "Step",
    "Unchecked",
    "cases_for",
    "check",
    "compile_invariant",
    "resolve_table",
    "spec_policy",
    "tables_of",
    "validate",
]
