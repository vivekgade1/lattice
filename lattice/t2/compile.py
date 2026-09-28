"""Compilation of checked procedures (spec 1c §4, spec 2026-09-28 go-target §2).

`compile_t2(program)` lowers the T2 part of a checked API program to T2 IR
(`lattice.t2ir.lower`) and emits it as the source of one Python module
(`lattice.t2.emit_py`, which documents the module's shape and where fuel is charged).
`lattice.t2.support.t2_load` runs that source in a restricted namespace (no ambient
builtins). The same source serves the runtime (`lattice.api.runtime`) and the generated
app (`lattice.api.codegen`), so fuel accounting is identical in both.
"""

from __future__ import annotations

import ast

from lattice.check import CheckedProgram
from lattice.check.t2 import T2Program
from lattice.t2.emit_py import T2Code, emit_python
from lattice.t2ir.lower import lower_t2

__all__ = ["T2Code", "compile_program", "compile_t2"]


def compile_program(program: CheckedProgram) -> T2Code | None:
    """The compiled T2 part of a checked program (None when it has no procedures or
    records). Module constants procedures read come from the program's source (list
    constants are gone from the checked T0 module)."""
    if program.t2 is None:
        return None
    return compile_t2(program.t2, ast.parse(program.source))


def compile_t2(t2: T2Program, module: ast.Module | None = None) -> T2Code:
    """The Python source of every procedure and record of `t2`, emitted from its T2 IR.
    `module` is the program's parsed source (the constants procedures read come from
    it)."""
    return emit_python(lower_t2(t2, module))
