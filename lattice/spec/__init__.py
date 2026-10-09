"""The Lattice spec: an application's profile plus state machines, invariants,
scenarios, endpoint programs and unchecked requirements, validated against itself and
compiled to the checks the checker runs (direction draft §4)."""

from __future__ import annotations

from lattice.spec.authoring import access_matrix, requirements_markdown
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
    Await,
    Contract,
    ContractError,
    ContractParam,
    Invariant,
    Machine,
    Scenario,
    Service,
    Spec,
    Step,
    Unchecked,
    resolve_table,
    tables_of,
)
from lattice.spec.report import Accepted, CheckFinding, Report
from lattice.spec.services import ServiceCmd
from lattice.spec.validate import CODES, SpecProblem, validate

__all__ = [
    "ALLOWED_FORMS",
    "Accepted",
    "Await",
    "CODES",
    "CaseContext",
    "CheckError",
    "CheckFinding",
    "Contract",
    "ContractError",
    "ContractParam",
    "Invariant",
    "InvariantError",
    "InvariantMonitor",
    "Machine",
    "Report",
    "Scenario",
    "Service",
    "ServiceCmd",
    "Spec",
    "SpecProblem",
    "Step",
    "Unchecked",
    "access_matrix",
    "cases_for",
    "check",
    "compile_invariant",
    "requirements_markdown",
    "resolve_table",
    "spec_policy",
    "tables_of",
    "validate",
]
