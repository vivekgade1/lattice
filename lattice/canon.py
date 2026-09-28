"""Canonical pretty-printer: one spelling per checked program."""

from __future__ import annotations

import ast

from lattice.check import CheckedProgram
from lattice.parse import HEADER


def canonical_source(program: CheckedProgram) -> str:
    """Header plus the module unparsed; comments and layout are normalized away. The
    module is the checker's normalised form (lattice.check.normalise), so ordinary
    Python spellings print as canonical Lattice (`row.x` as `col('x')`, string `+` as
    `fn.concat`, nested ops as `_s<k>_<line>` steps)."""
    return HEADER + "\n" + ast.unparse(program.module) + "\n"
