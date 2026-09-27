"""P2 — static name resolution, single assignment, and step purity.

Scopes (all lexical, resolved in one forward walk over the file):

* ``globals_`` — ambient names (every catalog name in ``IMPORTABLE`` and every
  profile schema name, predeclared) and completed steps; visible everywhere.
  ``from lattice.ops/schemas import ...`` is optional: P1 validates imported
  names and P2 treats them as already bound (never unused, never duplicates).
* ``consts``   — module-level named constants (``name = <column-free scalar
  expression>``, see ``is_constant_expr``); parent ``globals_``.
* ``tables``   — module-level table assignments; parent ``consts``.
* one scope per step body — parameters, locals and step-local constants; parent
  ``consts`` (not ``tables``), which is what makes step bodies unable to see module
  tables while still reading module constants defined above them.

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

from lattice.catalog import (
    API_AMBIENT,
    FN_ALIASES,
    IMPORTABLE,
    OP_ALIASES,
    PUBLIC_ROLE,
    SOFT_API_NAMES,
    TABLE_OPS,
    USER_PARAM,
)
from lattice.check.api_info import endpoint_decorator
from lattice.check.astutil import is_constant_expr, is_list_constant_assign
from lattice.check.context import CheckContext

Kind = Literal["ambient", "step", "table", "param", "local", "const"]

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
        ambient = IMPORTABLE | frozenset(ctx.profile.schemas)
        api = ctx.profile.api
        if api is not None:
            # API profile: API ops, response kinds, errors, parameter types, roles and
            # database table names
            ambient |= API_AMBIENT | api.roles | frozenset(api.tables)
        for name in sorted(ambient):
            self.globals_.bindings[name] = Binding(name, "ambient", module, 0, used=True)
        self.consts = Scope(self.globals_)
        self.tables = Scope(self.consts)
        self.step_scopes: list[Scope] = []
        # the soft API names (SOFT_API_NAMES) the current body may bind: those it never
        # calls as an op
        self.soft: frozenset[str] = frozenset()
        self.reported: set[tuple[str, str, int]] = set()
        # module-level table / constant names -> first binding line; later steps -> def line
        for stmt in module.body:
            if isinstance(stmt, ast.Assign):
                name = _target(stmt)
                if name not in self.tables.future and name not in self.consts.future:
                    kind_scope = self.consts if self._is_const(stmt) else self.tables
                    kind_scope.future[name] = stmt.lineno
            elif isinstance(stmt, ast.FunctionDef):
                self.globals_.future.setdefault(stmt.name, stmt.lineno)

    # ---- walk ---------------------------------------------------------------

    def run(self) -> None:
        if self.ctx.profile.api is not None:
            self._reserved_names()
        self.soft = self._bindable_soft_names(self.module)
        for stmt in self.module.body:
            match stmt:
                case ast.ImportFrom():
                    pass  # optional: P1 validated the names, which are ambient anyway
                case ast.Assign():
                    self._uses(stmt.value, self.tables, _StepCtx(None))
                    const = self._is_const(stmt)
                    self._bind(
                        self.consts if const else self.tables,
                        _target(stmt),
                        "const" if const else "table",
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

    def _endpoint_signature(self, fn: ast.FunctionDef, scope: Scope, where: _StepCtx) -> None:
        """Resolve an endpoint's decorator and annotations, then bind its parameters.

        The `auth=` role is not resolved here (P6 reports an unknown role as L801).
        A parameter's name is its wire name (JSON body / query key), so it may be named
        like a Python builtin (`zip`, `id`, `type`, `min`, ...) -- Lattice never uses
        Python builtins -- and like a table op (`limit`, `sort`); every other ambient
        Lattice name stays L205 (it would hide `col`, `fn`, `agg`, an API op, a type,
        an error, a role or a table the body or signature needs). `user` is always
        used. A non-Public endpoint without a `user` parameter still binds `user`
        (P6 reports the missing parameter once, as L801)."""
        deco = endpoint_decorator(fn)
        assert deco is not None
        auth = next((kw.value for kw in deco.keywords if kw.arg == "auth"), None)
        for part in [*deco.args, *(kw.value for kw in deco.keywords if kw.value is not auth)]:
            self._uses(part, scope, where)
        for a in fn.args.args:
            if a.annotation is not None:
                self._uses(a.annotation, scope, where)
        if fn.returns is not None:
            self._uses(fn.returns, scope, where)
        for a in fn.args.args:
            if a.arg in TABLE_OPS and a.arg not in scope.bindings:
                scope.bindings[a.arg] = Binding(a.arg, "param", a, fn.lineno)
            else:
                self._bind(scope, a.arg, "param", a, (scope, self.globals_), wire_name=True)
        user = scope.bindings.get(USER_PARAM)
        if user is not None:
            user.used = True
        elif not (isinstance(auth, ast.Name) and auth.id == PUBLIC_ROLE):
            scope.bindings[USER_PARAM] = Binding(USER_PARAM, "param", fn, fn.lineno, used=True)

    def _is_const(self, stmt: ast.Assign) -> bool:
        """A named constant: a scalar expression, or (API programs) a list constant."""
        if self.ctx.profile.api is not None and is_list_constant_assign(stmt):
            return True
        return is_constant_expr(stmt.value)

    def _bindable_soft_names(self, node: ast.AST) -> frozenset[str]:
        """The soft API names `node` may bind as variables: the ones it never calls."""
        if self.ctx.profile.api is None:
            return frozenset()
        called = {
            n.func.id
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        return SOFT_API_NAMES - called

    def _step(self, fn: ast.FunctionDef) -> None:
        outer_soft, self.soft = self.soft, self._bindable_soft_names(fn)
        try:
            self._step_body(fn)
        finally:
            self.soft = outer_soft

    def _step_body(self, fn: ast.FunctionDef) -> None:
        scope = Scope(self.consts)
        self.step_scopes.append(scope)
        where = _StepCtx(fn.name, fn.lineno)
        for a in fn.args.args:
            scope.future.setdefault(a.arg, fn.lineno)
        for stmt in fn.body:
            if isinstance(stmt, ast.Assign):
                scope.future.setdefault(_target(stmt), stmt.lineno)
        endpoint = self.ctx.profile.api is not None and endpoint_decorator(fn) is not None
        if endpoint:
            self._endpoint_signature(fn, scope, where)
        else:
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
                    kind: Kind = "const" if is_constant_expr(stmt.value) else "local"
                    self._bind(scope, _target(stmt), kind, stmt.targets[0], (scope, self.globals_))
                    if endpoint and _is_effect(stmt.value):
                        # a check or write is used for its effect even if never read
                        b = scope.bindings.get(_target(stmt))
                        if b is not None and b.node is stmt.targets[0]:
                            b.used = True
                case ast.Return(value=ast.Name() as value):
                    self._uses(value, scope, where)
                case ast.Expr(value=value):  # for_each(...) in an endpoint (P1)
                    self._uses(value, scope, where)
                case _:
                    raise TypeError(f"P1 should have rejected {type(stmt).__name__} in a step")
        # the step becomes visible only once its definition is complete
        self._bind(self.globals_, fn.name, "step", fn, (self.globals_, self.tables))
        if endpoint and fn.name in self.globals_.bindings:
            self.globals_.bindings[fn.name].used = True  # endpoints are called by HTTP

    def _reserved_names(self) -> None:
        """L205 (API profile): names starting with `__` are reserved for the SQL the
        runtime generates (the hidden `__ord` row-order column, `__ok_*` check flags,
        `__user_id` bind parameters): no column created by derive/group/rename and no
        endpoint parameter may use one."""
        for node in ast.walk(self.module):
            match node:
                case ast.Call(func=ast.Name(id="derive" | "group" | "window")):
                    for kw in node.keywords:
                        if kw.arg is not None and kw.arg.startswith("__"):
                            self._reserved(kw, kw.arg)
                case ast.Call(func=ast.Name(id="rename")):
                    for kw in node.keywords:
                        v = kw.value
                        if isinstance(v, ast.Constant) and isinstance(v.value, str):
                            if v.value.startswith("__"):
                                self._reserved(v, v.value)
                case ast.FunctionDef() if endpoint_decorator(node) is not None:
                    for a in node.args.args:
                        if a.arg.startswith("__"):
                            self._reserved(a, a.arg)
                case _:
                    pass

    def _reserved(self, node: ast.AST, name: str) -> None:
        self.ctx.error(
            "L205",
            node,
            f"'{name}' is reserved: names starting with '__' are internal",
            f"use a name without the leading underscores, e.g. '{name.lstrip('_') or 'value'}'",
        )

    # ---- bindings -----------------------------------------------------------

    def _bind(
        self,
        scope: Scope,
        name: str,
        kind: Kind,
        node: ast.AST,
        visible: tuple[Scope, ...],
        *,
        wire_name: bool = False,
    ) -> None:
        """Bind `name`; `wire_name` (endpoint parameters) exempts Python builtins."""
        line = getattr(node, "lineno", 1)
        if name in _BUILTINS and not wire_name:
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
            if existing is not None and existing.kind == "ambient" and name in self.soft:
                existing = None  # e.g. `mine = owned(...)` in a body that never calls mine()
            if existing is not None:
                if existing.kind == "ambient" and name in SOFT_API_NAMES:
                    self.ctx.error(
                        "L205",
                        node,
                        f"'{name}' names the operation {name}() this body calls; it "
                        "cannot also be a variable",
                        "use a different name, e.g. 'own'",
                    )
                elif existing.kind == "ambient":
                    self.ctx.error(
                        "L205",
                        node,
                        f"'{name}' shadows a built-in Lattice name",
                        "use a different name",
                    )
                elif existing.kind == "step" and kind != "step":
                    self.ctx.error(
                        "L205",
                        node,
                        f"'{name}' shadows a step (line {existing.line})",
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
        if name in self.consts.future:  # a module constant defined further down
            line = self.consts.future[name]
            self._once(
                "L202",
                name,
                scope,
                node,
                f"'{name}' is used before it is assigned (line {line})",
                "define the constant above the step that uses it"
                if in_step
                else "assign it before this statement",
            )
            self._mark_used(self.consts, name)
            return
        # (4) unknown
        self._once(
            "L203", name, scope, node, f"unknown name '{name}'", self._unknown_hint(name, scope)
        )

    def _unknown_hint(self, name: str, scope: Scope) -> str | None:
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
        scopes = [self.globals_, self.consts, self.tables, *self.step_scopes]
        for scope in scopes:
            for b in scope.all_bindings():
                if b.used:
                    continue
                if b.kind != "ambient":
                    self.ctx.warn(
                        "L204",
                        b.node,
                        f"'{b.name}' is never used",
                        "remove it or use it in a later step",
                    )


_EFFECT_OPS = frozenset({"one", "require", "insert", "update", "delete"})


def _is_effect(value: ast.expr) -> bool:
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in _EFFECT_OPS
    )


def _target(stmt: ast.Assign) -> str:
    match stmt.targets:
        case [ast.Name(id=name)]:
            return name
        case _:
            raise TypeError("P1 guarantees a single Name assignment target")


def run(module: ast.Module, ctx: CheckContext) -> None:
    _Resolver(module, ctx).run()
