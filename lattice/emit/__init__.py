"""Emitters: Lattice programs back to code in a host repository.

``thin`` (thin-slice plan node E2) generates the program's routes with the FastAPI codegen
and mounts them into the vendored FastAPI template in place of its own routers.
"""

from lattice.emit.thin import (
    EmitError,
    EmitResult,
    SchemaEvolutionError,
    emit_into_template,
    evolve_schema,
    make_emitted_app,
)

__all__ = [
    "EmitError",
    "EmitResult",
    "SchemaEvolutionError",
    "emit_into_template",
    "evolve_schema",
    "make_emitted_app",
]
