"""P2 — static name resolution, single assignment, and step purity.

Scopes (all lexical, resolved in one forward walk over the file):

* ``globals_`` — imports and completed steps; visible everywhere.
* ``tables``   — module-level assignments; parent ``globals_``.
* one scope per step body — parameters and locals; parent ``globals_`` (not
  ``tables``), which is what makes step bodies unable to see module tables.

A step's name is entered into ``globals_`` only once its ``def`` is complete,
so a step cannot call itself or any step defined after it.
"""

from __future__ import annotations

import ast
import builtins
import difflib
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

from lattice.catalog import FN_ALIASES, IMPORTABLE, OP_ALIASES, OPS_MODULE, SCHEMAS_MODULE
from lattice.check.context import CheckContext

Kind = Literal["import", "step", "table", "param", "local"]

_BUILTINS = frozenset(dir(builtins))


@dataclass
class Binding:
    name: str
    kind: Kind
    node: ast.AST
    line: int
    used: bool = False


@dataclass
class Scope:
    parent: Scope | None
    bindings: dict[str, Binding] = field(default_factory=dict)
    # names referenced before their binding (already reported); counted as used
    pre_used: set[str] = field(default_factory=set)
    # every name this scope will bind, with the line of its first binding
    future: dict[str, int] = field(default_factory=dict)

    def lookup(self, name: str) -> Binding | None:
        scope: Scope | None = self
        while scope is not None:
            b = scope.bindings.get(name)
            if b is not None:
                return b
            scope = scope.parent
        return None

    def visible_names(self) -> list[str]:
        names: list[str] = []
        scope: Scope | None = self
        while scope is not None:
            names.extend(scope.bindings)
            scope = scope.parent
        return names

    def all_bindings(self) -> Iterator[Binding]:
        yield from self.bindings.values()


@dataclass
class _StepCtx:
    """Where a use occurs: inside step `name` (or at module level if None)."""

    name: str | None
    def_line: int = 0


class _Resolver:
    def __init__(self, module: ast.Module, ctx: CheckContext) -> None:
        self.module = module
        self.ctx = ctx
        self.globals_ = Scope(None)
        self.tables = Scope(self.globals_)
        self.step_scopes: list[Scope] = []
        self.reported: set[tuple[str, str, int]] = set()
        # module-level table names -> first binding line; later steps -> def line
        for stmt in module.body:
            if isinstance(stmt, ast.Assign):
                self.tables.future.setdefault(_target(stmt), stmt.lineno)
            elif isinstance(stmt, ast.FunctionDef):
                self.globals_.future.setdefault(stmt.name, stmt.lineno)

    # ---- walk ---------------------------------------------------------------

    def run(self) -> None:
        for stmt in self.module.body:
            match stmt:
                case ast.ImportFrom():
                    for alias in stmt.names:
                        self._bind(self.globals_, alias.name, "import", alias, (self.globals_,))
                case ast.Assign():
                    self._uses(stmt.value, self.tables, _StepCtx(None))
                    self._bind(
                        self.tables,
                        _target(stmt),
                        "table",
                        stmt.targets[0],
                        (self.globals_, self.tables),
                    )
                case ast.Expr():
                    self._uses(stmt.value, self.tables, _StepCtx(None))
                case ast.FunctionDef():
                    self._step(stmt)
                case _:
                    raise TypeError(f"P1 should have rejected {type(stmt).__name__}")
        self._report_unused()

    def _step(self, fn: ast.FunctionDef) -> None:
        scope = Scope(self.globals_)
        self.step_scopes.append(scope)
        where = _StepCtx(fn.name, fn.lineno)
        for a in fn.args.args:
            scope.future.setdefault(a.arg, fn.lineno)
        for stmt in fn.body:
            if isinstance(stmt, ast.Assign):
                scope.future.setdefault(_target(stmt), stmt.lineno)
        # decorator and annotations are resolved before parameters are bound
        for deco in fn.decorator_list:
            self._uses(deco, scope, where)
        for a in fn.args.args:
            if a.annotation is not None:
                self._uses(a.annotation, scope, where)
        if fn.returns is not None:
            self._uses(fn.returns, scope, where)
        for a in fn.args.args:
            self._bind(scope, a.arg, "param", a, (scope, self.globals_))
        for stmt in fn.body:
            match stmt:
                case ast.Assign():
                    self._uses(stmt.value, scope, where)
                    self._bind(
                        scope, _target(stmt), "local", stmt.targets[0], (scope, self.globals_)
                    )
                case ast.Return(value=ast.Name() as value):
                    self._uses(value, scope, where)
                case _:
                    raise TypeError(f"P1 should have rejected {type(stmt).__name__} in a step")
        # the step becomes visible only once its definition is complete
        self._bind(self.globals_, fn.name, "step", fn, (self.globals_, self.tables))

    # ---- bindings -----------------------------------------------------------

    def _bind(
        self,
        scope: Scope,
        name: str,
        kind: Kind,
        node: ast.AST,
        visible: tuple[Scope, ...],
    ) -> None:
        line = getattr(node, "lineno", 1)
        if kind != "import" and name in _BUILTINS:
            self.ctx.error(
                "L205",
                node,
                f"'{name}' shadows a Python builtin",
                "use a more descriptive name",
            )
        else:
            existing = None
            for s in visible:
                existing = existing or s.lookup(name)
            if existing is not None:
                if existing.kind in ("import", "step") and kind not in ("import", "step"):
                    what = "an imported name" if existing.kind == "import" else "a step"
                    self.ctx.error(
                        "L205",
                        node,
                        f"'{name}' shadows {what} (line {existing.line})",
                        "use a different name",
                    )
                else:
                    self.ctx.error(
                        "L201",
                        node,
                        f"'{name}' is already bound (line {existing.line}); "
                        "every name is bound exactly once",
                        "use a new descriptive name",
                    )
                existing.used = existing.used or name in scope.pre_used
                return
        if name in scope.bindings:  # a builtin-named duplicate: first binding wins
            return
        scope.bindings[name] = Binding(name, kind, node, line, used=name in scope.pre_used)

    # ---- uses ---------------------------------------------------------------

    def _uses(self, expr: ast.AST, scope: Scope, where: _StepCtx) -> None:
        names = [n for n in ast.walk(expr) if isinstance(n, ast.Name)]
        names.sort(key=lambda n: (n.lineno, n.col_offset))
        for n in names:
            self._use(n, scope, where)
        # load(..., schema="Orders") is reported by P4 (L414, hint schema=Orders); count
        # the misuse as a use of the imported schema so it is not also reported unused.
        for call in ast.walk(expr):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "load"
            ):
                continue
            for kw in call.keywords:
                if kw.arg == "schema" and isinstance(kw.value, ast.Constant):
                    b = self.globals_.lookup(str(kw.value.value))
                    if b is not None and b.kind == "import":
                        b.used = True

    def _use(self, node: ast.Name, scope: Scope, where: _StepCtx) -> None:
        name = node.id
        b = scope.lookup(name)
        if b is not None:
            b.used = True
            return
        in_step = where.name is not None
        # (1) self-reference inside a step
        if in_step and name == where.name:
            self._once(
                "L604",
                name,
                scope,
                node,
                f"step '{name}' cannot call itself; recursion is not allowed",
                "express the repeated work as separate steps",
            )
            self._mark_used(self.globals_, name)
            return
        # (2) a step body reading a module-level table
        if in_step and name in self.tables.future:
            self._once(
                "L603",
                name,
                scope,
                node,
                f"step bodies cannot read module-level table '{name}'",
                "pass it as a parameter",
            )
            self._mark_used(self.tables, name)
            return
        # (3) bound later in the same scope, or a step defined later
        if name in self.globals_.future and not self.globals_.lookup(name):
            anchor = where.def_line if in_step else node.lineno
            self._once(
                "L604",
                name,
                scope,
                node,
                f"step '{name}' is used before it is defined",
                f"move the definition above line {anchor}",
            )
            self._mark_used(self.globals_, name)
            return
        if name in scope.future:
            self._once(
                "L202",
                name,
                scope,
                node,
                f"'{name}' is used before it is assigned (line {scope.future[name]})",
                "assign it before this statement",
            )
            self._mark_used(scope, name)
            return
        # (4) unknown
        self._once(
            "L203", name, scope, node, f"unknown name '{name}'", self._unknown_hint(name, scope)
        )

    def _unknown_hint(self, name: str, scope: Scope) -> str | None:
        if name in IMPORTABLE:
            return f"add it to 'from {OPS_MODULE} import ...'"
        if name in self.ctx.profile.schemas:
            return f"add it to 'from {SCHEMAS_MODULE} import ...'"
        if name in OP_ALIASES:
            return f"the op is called '{OP_ALIASES[name]}'"
        if name in FN_ALIASES:
            return f"use 'fn.{FN_ALIASES[name]}'"
        close = difflib.get_close_matches(name, scope.visible_names(), n=1)
        return f"did you mean '{close[0]}'?" if close else None

    def _mark_used(self, scope: Scope, name: str) -> None:
        b = scope.bindings.get(name)
        if b is not None:
            b.used = True
        else:
            scope.pre_used.add(name)

    def _once(
        self, code: str, name: str, scope: Scope, node: ast.AST, msg: str, hint: str | None
    ) -> None:
        key = (code, name, id(scope))
        if key in self.reported:
            return
        self.reported.add(key)
        self.ctx.error(code, node, msg, hint)

    # ---- unused -------------------------------------------------------------

    def _report_unused(self) -> None:
        scopes = [self.globals_, self.tables, *self.step_scopes]
        for scope in scopes:
            for b in scope.all_bindings():
                if b.used:
                    continue
                if b.kind == "import":
                    self.ctx.error(
                        "L306",
                        b.node,
                        f"imported name '{b.name}' is never used",
                        "remove it from the import list",
                    )
                else:
                    self.ctx.warn(
                        "L204",
                        b.node,
                        f"'{b.name}' is never used",
                        "remove it or use it in a later step",
                    )


def _target(stmt: ast.Assign) -> str:
    match stmt.targets:
        case [ast.Name(id=name)]:
            return name
        case _:
            raise TypeError("P1 guarantees a single Name assignment target")


def run(module: ast.Module, ctx: CheckContext) -> None:
    _Resolver(module, ctx).run()
