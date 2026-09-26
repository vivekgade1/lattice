"""IR: lower a checked program into a Plan — a topologically ordered DAG of op nodes.

Step calls are inlined: each call site gets its own sub-graph whose node ids are
prefixed with the call-site binding (`"<binding>/<local>"`, composing for nested
steps). Expressions inside an inlined body are deep-copied with scalar-parameter
names replaced by constants, so the checked AST is never mutated.
"""

from __future__ import annotations

import ast
import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lattice.catalog import OP_SHAPES
from lattice.check import CheckedProgram
from lattice.check.astutil import literal_value, steps_of
from lattice.diagnostics import Span, span_of
from lattice.runtime import LatticeInternalError
from lattice.typesys import Scalar, ScalarParamT, Schema, TableT

ScalarValue = bool | int | float | str


@dataclass(frozen=True)
class Node:
    id: str  # module binding, "<call-site>/<local>" for inlined bodies, "_write<k>" for writes
    op: str  # a TABLE_OPS name (never a step)
    inputs: tuple[str, ...]  # ids of input nodes, in positional order
    params: Mapping[str, Any]
    schema: Schema | None  # statically predicted output schema; None for write
    span: Span  # call site (for inlined nodes: the call inside the step body)


@dataclass(frozen=True)
class Plan:
    nodes: tuple[Node, ...]  # topological, deterministic (source order after inlining)


class _Substitute(ast.NodeTransformer):
    """Replace scalar-parameter names with constants (on an already-copied tree)."""

    def __init__(self, scalars: Mapping[str, ScalarValue]) -> None:
        self.scalars = scalars

    def visit_Name(self, node: ast.Name) -> ast.expr:
        if node.id in self.scalars:
            return ast.copy_location(ast.Constant(self.scalars[node.id]), node)
        return node


def _str(node: ast.expr) -> str:
    match node:
        case ast.Constant(value=str(v)):
            return v
        case _:
            raise LatticeInternalError(f"expected a string literal, got {ast.dump(node)}")


def _strs(node: ast.expr) -> tuple[str, ...]:
    match node:
        case ast.List(elts=elts) | ast.Tuple(elts=elts):
            return tuple(_str(e) for e in elts)
        case _:
            raise LatticeInternalError(f"expected a list of strings, got {ast.dump(node)}")


def _scalar_value(node: ast.expr, scalars: Mapping[str, ScalarValue]) -> ScalarValue:
    """A step's scalar argument: a literal (as `constant_scalar` accepts it, including
    repeated unary minus) or an enclosing step's scalar parameter."""
    v = literal_value(node)
    if v is not None:
        return v
    match node:
        case ast.Name(id=name) if name in scalars:
            return scalars[name]
        case _:
            raise LatticeInternalError(f"step scalar argument is not a literal: {ast.dump(node)}")


class _Lowerer:
    def __init__(self, program: CheckedProgram) -> None:
        self.program = program
        self.step_defs = steps_of(program.module)
        self.nodes: list[Node] = []
        self.writes = 0

    def run(self) -> Plan:
        env: dict[str, str] = {}
        for stmt in self.program.module.body:
            match stmt:
                case ast.ImportFrom() | ast.FunctionDef():
                    pass
                case ast.Assign(targets=[ast.Name(id=name)], value=ast.Call() as call):
                    out = self._unit(call, name, env, {}, "")
                    if out is None:
                        raise LatticeInternalError("write() result was bound to a name")
                    env[name] = out
                case ast.Expr(value=ast.Call() as call):
                    self._unit(call, None, env, {}, "")
                case _:
                    raise LatticeInternalError(f"unexpected statement {type(stmt).__name__}")
        return Plan(tuple(self.nodes))

    # ---- units ----------------------------------------------------------------

    def _unit(
        self,
        call: ast.Call,
        binding: str | None,
        env: Mapping[str, str],
        scalars: Mapping[str, ScalarValue],
        prefix: str,
    ) -> str | None:
        match call.func:
            case ast.Name(id=name) if name in OP_SHAPES:
                return self._op(call, name, binding, env, scalars, prefix)
            case ast.Name(id=name) if name in self.step_defs:
                if binding is None:
                    raise LatticeInternalError(f"step call '{name}' is not bound to a name")
                return self._inline(call, name, binding, env, scalars, prefix)
            case _:
                raise LatticeInternalError(f"not a unit call: {ast.dump(call.func)}")

    def _expr(self, node: ast.expr, scalars: Mapping[str, ScalarValue]) -> ast.expr:
        if not scalars:
            return node  # module level: nothing to substitute; the executor never mutates
        copied = copy.deepcopy(node)
        out = _Substitute(scalars).visit(copied)
        return ast.fix_missing_locations(out)

    def _op(
        self,
        call: ast.Call,
        op: str,
        binding: str | None,
        env: Mapping[str, str],
        scalars: Mapping[str, ScalarValue],
        prefix: str,
    ) -> str | None:
        shape = OP_SHAPES[op]
        inputs: list[str] = []
        for arg in call.args[: shape.tables]:
            match arg:
                case ast.Name(id=name) if name in env:
                    inputs.append(env[name])
                case _:
                    raise LatticeInternalError(f"unresolved table argument {ast.dump(arg)}")
        kws = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        params: dict[str, Any]
        match op:
            case "load":
                params = {"path": _str(call.args[0])}
            case "where":
                params = {"predicate": self._expr(call.args[1], scalars)}
            case "derive":
                params = {
                    "columns": tuple(
                        (kw.arg, self._expr(kw.value, scalars))
                        for kw in call.keywords
                        if kw.arg is not None
                    )
                }
            case "select":
                params = {"cols": _strs(kws["cols"])}
            case "rename":
                params = {
                    "mapping": tuple(
                        (kw.arg, _str(kw.value)) for kw in call.keywords if kw.arg is not None
                    )
                }
            case "group":
                params = {"by": _strs(kws["by"]), "aggs": self._aggs(call, scalars)}
            case "join":
                how = _str(kws["how"]) if "how" in kws else "inner"
                params = {"on": _strs(kws["on"]), "how": how}
            case "sort":
                desc = kws.get("desc")
                match desc:
                    case None:
                        flag = False
                    case ast.Constant(value=bool(v)):
                        flag = v
                    case _:
                        raise LatticeInternalError("sort desc= is not a bool literal")
                params = {"by": _strs(kws["by"]), "desc": flag}
            case "limit":
                match kws["n"]:
                    case ast.Constant(value=int(n)) if not isinstance(n, bool):
                        params = {"n": n}
                    case _:
                        raise LatticeInternalError("limit n= is not an int literal")
            case "write":
                params = {"path": _str(call.args[1])}
            case _:
                raise LatticeInternalError(f"no lowering for op '{op}'")

        if op == "write":
            node_id = f"_write{self.writes}"
            self.writes += 1
            schema = None
        else:
            if binding is None:
                raise LatticeInternalError(f"{op}() result is not bound to a name")
            node_id = prefix + binding
            t = self.program.types.get(id(call))
            if not isinstance(t, TableT):
                raise LatticeInternalError(f"no table type recorded for {op}() at {span_of(call)}")
            schema = t.schema
        self.nodes.append(Node(node_id, op, tuple(inputs), params, schema, span_of(call)))
        return None if op == "write" else node_id

    def _aggs(
        self, call: ast.Call, scalars: Mapping[str, ScalarValue]
    ) -> tuple[tuple[str, str, ast.expr | None], ...]:
        out: list[tuple[str, str, ast.expr | None]] = []
        for kw in call.keywords:
            if kw.arg is None or kw.arg == "by":
                continue
            match kw.value:
                case ast.Call(func=ast.Attribute(value=ast.Name(id="agg"), attr=func), args=[]):
                    out.append((kw.arg, func, None))
                case ast.Call(func=ast.Attribute(value=ast.Name(id="agg"), attr=func), args=[a]):
                    out.append((kw.arg, func, self._expr(a, scalars)))
                case _:
                    raise LatticeInternalError(f"not an aggregate: {ast.dump(kw.value)}")
        return tuple(out)

    # ---- steps ----------------------------------------------------------------

    def _inline(
        self,
        call: ast.Call,
        name: str,
        binding: str,
        env: Mapping[str, str],
        scalars: Mapping[str, ScalarValue],
        prefix: str,
    ) -> str:
        fn = self.step_defs[name]
        sig = self.program.steps.get(name)
        if sig is None:
            raise LatticeInternalError(f"no signature for step '{name}'")
        kws = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        inner_env: dict[str, str] = {}
        inner_scalars: dict[str, ScalarValue] = {}
        table_args = iter(call.args)
        for pname, pt in sig.params:
            match pt:
                case TableT():
                    arg = next(table_args, None)
                    match arg:
                        case ast.Name(id=a) if a in env:
                            inner_env[pname] = env[a]
                        case _:
                            raise LatticeInternalError(f"bad table argument for '{pname}'")
                case ScalarParamT(scalar=s):
                    value = _scalar_value(kws[pname], scalars)
                    if s is Scalar.FLOAT and isinstance(value, int) and not isinstance(value, bool):
                        value = float(value)  # Int literal accepted for Float: widen now
                    inner_scalars[pname] = value
                case _:
                    raise LatticeInternalError(f"bad parameter type for '{pname}'")
        inner_prefix = f"{prefix}{binding}/"
        result: str | None = None
        for stmt in fn.body:
            match stmt:
                case ast.Assign(targets=[ast.Name(id=local)], value=ast.Call() as body_call):
                    out = self._unit(body_call, local, inner_env, inner_scalars, inner_prefix)
                    if out is None:
                        raise LatticeInternalError("write() inside a step")
                    inner_env[local] = out
                case ast.Return(value=ast.Name(id=local)) if local in inner_env:
                    result = inner_env[local]
                case _:
                    raise LatticeInternalError(f"unexpected step statement in '{name}'")
        if result is None:
            raise LatticeInternalError(f"step '{name}' has no return")
        return result


def lower(program: CheckedProgram) -> Plan:
    return _Lowerer(program).run()
