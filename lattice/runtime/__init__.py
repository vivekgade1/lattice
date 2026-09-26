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


class LatticeExprError(Exception):
    """A value-dependent failure while compiling an expression (e.g. a step's scalar
    argument out of range); the executor re-raises it as LatticeRuntimeError at the op."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def is_panic(exc: BaseException) -> bool:
    """True for a Rust panic surfaced by a native extension (pyo3's PanicException).

    It derives from BaseException, so ordinary `except Exception` handlers miss it."""
    return type(exc).__name__ == "PanicException"
