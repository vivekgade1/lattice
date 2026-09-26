"""Mutable state shared by the checker passes: diagnostics and the type side table."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from lattice.diagnostics import Diagnostic, Severity, Span, span_of
from lattice.profile import Profile
from lattice.typesys import Type


@dataclass
class CheckContext:
    profile: Profile
    diagnostics: list[Diagnostic] = field(default_factory=list)
    types: dict[int, Type] = field(default_factory=dict)  # id(ast node) -> synthesized type

    def _add(
        self, code: str, sev: Severity, where: ast.AST | Span, message: str, hint: str | None
    ) -> None:
        span = where if isinstance(where, Span) else span_of(where)
        self.diagnostics.append(Diagnostic(code, sev, span, message, hint))

    def error(self, code: str, node: ast.AST | Span, message: str, hint: str | None = None) -> None:
        self._add(code, Severity.ERROR, node, message, hint)

    def warn(self, code: str, node: ast.AST | Span, message: str, hint: str | None = None) -> None:
        self._add(code, Severity.WARNING, node, message, hint)

    def error_count(self) -> int:
        return sum(1 for d in self.diagnostics if d.severity is Severity.ERROR)

    def set_type(self, node: ast.AST, t: Type) -> Type:
        self.types[id(node)] = t
        return t
