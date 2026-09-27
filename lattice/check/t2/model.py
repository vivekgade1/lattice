"""What the T2 checker hands to T2b (fuel-instrumented compilation, runtime, codegen,
T0 integration): checked procedures and records, with their typed side tables."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass, field

from lattice.check.t2.types import T2Type
from lattice.diagnostics import Span


@dataclass(frozen=True)
class ProcSig:
    """A procedure's signature: annotated parameters (in order), the declared return
    type, and the declared effects (profile capabilities; empty = pure)."""

    name: str
    params: tuple[tuple[str, T2Type], ...]
    returns: T2Type
    effects: frozenset[str] = frozenset()

    def param(self, name: str) -> T2Type | None:
        return dict(self.params).get(name)


@dataclass(frozen=True)
class CheckedProcedure:
    """A type-checked `@procedure`.

    `types`: the typed AST side table, id(expression node) -> its type, for every
    expression of the body (annotations excluded). `targets`: id(ast.Call) -> what the
    call resolved to, one of "proc:<name>", "record:<name>", "builtin:<name>",
    "helper:<name>" (days/hours/...), "std:<module>.<name>", "method:<kind>.<name>",
    "native:<name>" -- T2b charges fuel and builds globals from these. `locals`: the
    declared type of every local variable (parameters included). `calls`: the
    procedures this one calls directly; `natives`: the natives it calls;
    `is_recursive`: it is on a cycle of the call graph (fuel bounds the depth)."""

    name: str
    node: ast.FunctionDef
    sig: ProcSig
    types: Mapping[int, T2Type]
    targets: Mapping[int, str]
    locals: Mapping[str, T2Type]
    calls: frozenset[str]
    natives: frozenset[str]
    is_recursive: bool
    span: Span

    @property
    def effects(self) -> frozenset[str]:
        return self.sig.effects

    @property
    def pure(self) -> bool:
        return not self.sig.effects


@dataclass(frozen=True)
class RecordDef:
    """A `@record` value type: frozen fields in declaration order, literal defaults."""

    name: str
    node: ast.ClassDef
    fields: tuple[tuple[str, T2Type], ...]
    defaults: Mapping[str, object] = field(default_factory=dict)

    def field_type(self, name: str) -> T2Type | None:
        return dict(self.fields).get(name)


@dataclass(frozen=True)
class T2Program:
    """The T2 part of a checked API program.

    `imports`: local name -> qualified allowlisted stdlib name ("math",
    "datetime.timedelta"); `constants`: the module constants procedures may read ->
    their types; `nodes`: the T2 top-level statements (imports, records, procedures)
    in source order -- they are not in CheckedProgram.module, which holds the T0 view."""

    procedures: Mapping[str, CheckedProcedure]
    records: Mapping[str, RecordDef]
    imports: Mapping[str, str]
    constants: Mapping[str, T2Type]
    nodes: tuple[ast.stmt, ...]
