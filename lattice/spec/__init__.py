"""The Lattice spec: an application's profile plus state machines, invariants,
scenarios, endpoint programs and unchecked requirements, validated against itself and
compiled to the checks the checker runs (direction draft §4)."""

from __future__ import annotations

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
from lattice.spec.validate import CODES, SpecProblem, validate

__all__ = [
    "ALLOWED_FORMS",
    "CODES",
    "Invariant",
    "InvariantError",
    "Machine",
    "Scenario",
    "Spec",
    "SpecProblem",
    "Step",
    "Unchecked",
    "compile_invariant",
    "resolve_table",
    "tables_of",
    "validate",
]
