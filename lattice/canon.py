"""Canonical pretty-printer: one spelling per checked program."""

from __future__ import annotations

import ast

from lattice.check import CheckedProgram
from lattice.parse import HEADER


def canonical_source(program: CheckedProgram) -> str:
    """Header plus the module unparsed; comments and layout are normalized away."""
    return HEADER + "\n" + ast.unparse(program.module) + "\n"
