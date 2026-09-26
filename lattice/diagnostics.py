"""Diagnostics: spanned, coded messages produced by the parser and checker."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, order=True)
class Span:
    """A 1-based source position."""

    line: int
    col: int


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: Severity
    span: Span
    message: str
    hint: str | None = None

    def render(self) -> str:
        text = (
            f"{self.span.line}:{self.span.col}: {self.severity.value} {self.code}: {self.message}"
        )
        if self.hint:
            text += f" (hint: {self.hint})"
        return text


def span_of(node: ast.AST) -> Span:
    """1-based span of an AST node; Span(1, 1) for nodes without a position."""
    line = getattr(node, "lineno", None)
    col = getattr(node, "col_offset", None)
    if line is None or col is None:
        return Span(1, 1)
    return Span(line, col + 1)


def has_errors(diags: Iterable[Diagnostic]) -> bool:
    return any(d.severity is Severity.ERROR for d in diags)


def sort_key(d: Diagnostic) -> tuple[int, int, str]:
    return (d.span.line, d.span.col, d.code)


def render_all(diags: Iterable[Diagnostic]) -> str:
    return "\n".join(d.render() for d in sorted(diags, key=sort_key))


def render_for_llm(diags: Iterable[Diagnostic]) -> str:
    """One line per diagnostic, errors before warnings, each group in source order."""
    ordered = sorted(diags, key=lambda d: (d.severity is not Severity.ERROR, *sort_key(d)))
    lines = []
    for d in ordered:
        line = f"- {d.code} at line {d.span.line}, col {d.span.col}: {d.message}"
        if d.hint:
            line += f". Fix: {d.hint}"
        lines.append(line)
    return "\n".join(lines)
