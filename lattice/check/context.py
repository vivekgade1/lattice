"""Mutable state shared by the checker passes: diagnostics and the type side table."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lattice.diagnostics import Diagnostic, Severity, Span, span_of
from lattice.profile import Profile
from lattice.typesys import Type

if TYPE_CHECKING:
    from lattice.check.t2.model import T2Program


@dataclass
class CheckContext:
    profile: Profile
    diagnostics: list[Diagnostic] = field(default_factory=list)
    types: dict[int, Type] = field(default_factory=dict)  # id(ast node) -> synthesized type
    # Module names read outside the T0 passes' view (by @procedure bodies): P2 does not
    # report them as unused.
    external_uses: set[str] = field(default_factory=set)
    # The program's checked @procedure / @record part (None without one): the T0 passes
    # read it to type procedure calls in endpoints.
    t2: T2Program | None = None

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
