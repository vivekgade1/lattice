"""Mutable state shared by the checker passes: diagnostics and the type side table."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lattice.diagnostics import Diagnostic, Fix, Severity, Span, span_of
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
    # N1: the `a + b` nodes P4 typed as Str concatenations (id -> node); replaced by
    # fn.concat(a, b) right after P4 (lattice.check.normalise.resolve_concats). The
    # nodes are kept alive here so their ids stay unique while `types` refers to them.
    concats: dict[int, ast.BinOp] = field(default_factory=dict)
    # N1b: ids of the `r.x` row-value reads the normaliser found in endpoint write/derive
    # values (P1 accepts them; P4 types them; P6 checks their source row)
    row_reads: frozenset[int] = frozenset()
    # N1: the fresh names the normaliser bound to lifted nested ops (name -> line of the
    # statement they came from); hints never name them
    fresh: Mapping[str, int] = field(default_factory=dict)

    def _add(
        self,
        code: str,
        sev: Severity,
        where: ast.AST | Span,
        message: str,
        hint: str | None,
        fix: Fix | None = None,
    ) -> None:
        span = where if isinstance(where, Span) else span_of(where)
        self.diagnostics.append(Diagnostic(code, sev, span, message, hint, fix))

    def error(
        self,
        code: str,
        node: ast.AST | Span,
        message: str,
        hint: str | None = None,
        fix: Fix | None = None,
    ) -> None:
        self._add(code, Severity.ERROR, node, message, hint, fix)

    def warn(self, code: str, node: ast.AST | Span, message: str, hint: str | None = None) -> None:
        self._add(code, Severity.WARNING, node, message, hint)

    def error_count(self) -> int:
        return sum(1 for d in self.diagnostics if d.severity is Severity.ERROR)

    def set_type(self, node: ast.AST, t: Type) -> Type:
        self.types[id(node)] = t
        return t
