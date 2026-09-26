"""Lattice runtime: expression compiler, op implementations, and the plan executor."""

from __future__ import annotations

from lattice.diagnostics import Span


class LatticeInternalError(Exception):
    """An invariant the checker should have guaranteed was violated: always a Lattice bug."""


class LatticeRuntimeError(Exception):
    """A data-dependent failure while executing a checked program (I/O, limits)."""

    def __init__(self, code: str, span: Span, message: str) -> None:
        super().__init__(f"{span.line}:{span.col}: error {code}: {message}")
        self.code = code
        self.span = span
        self.message = message

    def render(self) -> str:
        return f"{self.span.line}:{self.span.col}: error {self.code}: {self.message}"
