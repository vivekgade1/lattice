"""IR: lower a checked program into a Plan — a topologically ordered DAG of op nodes.

Step calls are inlined: each call site gets its own sub-graph whose node ids are
prefixed with the call-site binding (`"<binding>/<local>"`, composing for nested
steps). Named constants (`name = <column-free scalar expression>`) produce no node:
every expression is deep-copied with constant names replaced by their (already
substituted) value expressions and, inside an inlined body, scalar-parameter names
replaced by literals — so the runtime never sees a constant or parameter name and
the checked AST is never mutated.
"""

from __future__ import annotations

import ast
import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from lattice.catalog import ITERATE, ITERATE_FIXPOINT, OP_SHAPES, TRAVERSE_DEPTH, WINDOW_KW
from lattice.check import CheckedProgram
from lattice.check.astutil import is_constant_expr, join_on_pairs, literal_value, steps_of
from lattice.diagnostics import Span, span_of
from lattice.runtime import LatticeInternalError
from lattice.typesys import Scalar, ScalarParamT, Schema, TableT

ScalarValue = bool | int | float | str
Subst = Mapping[str, ast.expr]  # name -> replacement expression (fully substituted)
# one group() aggregate: (output name, agg function, argument, order= expression)
AggSpec = tuple[str, str, ast.expr | None, ast.expr | None]


@dataclass(frozen=True)
class Node:
    id: str  # module binding, "<call-site>/<local>" for inlined bodies, "_write<k>" for writes
    # a TABLE_OPS name (never a step), or "iterate": params {"body": the step's Plan,
    # "param": the id the body reads the current round's table as, "result": the id of
    # the body's output, "until": "fixpoint" or a Bool expression, "max": rounds}
    op: str
    inputs: tuple[str, ...]  # ids of input nodes, in positional order
    params: Mapping[str, Any]
    schema: Schema | None  # statically predicted output schema; None for write
    span: Span  # call site (for inlined nodes: the call inside the step body)


@dataclass(frozen=True)
class Plan:
    nodes: tuple[Node, ...]  # topological, deterministic (source order after inlining)


class _Substitute(ast.NodeTransformer):
    """Replace constant and scalar-parameter names by (copies of) their values, on an
    already-copied tree. Replacements are fully substituted already, so they are
    not revisited."""

    def __init__(self, subst: Subst) -> None:
        self.subst = subst

    def visit_Name(self, node: ast.Name) -> ast.expr:
        value = self.subst.get(node.id)
        if value is None:
            return node
        return ast.copy_location(copy.deepcopy(value), node)


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


def _join_keys(node: ast.expr) -> dict[str, tuple[str, ...]]:
    """join on=: {"on": left key names}, plus "right_on" (the right key names, in the
    same order) when a dict literal pairs differently-named columns."""
    pairs = join_on_pairs(node)
    if pairs is None:
        raise LatticeInternalError(f"expected join keys, got {ast.dump(node)}")
    out = {"on": tuple(a for a, _ in pairs)}
    if any(a != b for a, b in pairs):
        out["right_on"] = tuple(b for _, b in pairs)
    return out


def _scalar_value(node: ast.expr, subst: Subst) -> ScalarValue:
    """A step's scalar argument: a literal (as `constant_scalar` accepts it, including
    repeated unary minus), an enclosing step's scalar parameter, or a named constant
    bound to a literal."""
    v = literal_value(node)
    if v is None and isinstance(node, ast.Name) and node.id in subst:
        v = literal_value(subst[node.id])
    if v is None:
        raise LatticeInternalError(f"step scalar argument is not a literal: {ast.dump(node)}")
    return v


class _Lowerer:
    def __init__(self, program: CheckedProgram) -> None:
        self.program = program
        self.step_defs = steps_of(program.module)
        # step name -> the module constants bound before its definition
        self.step_consts: dict[str, dict[str, ast.expr]] = {}
        self.nodes: list[Node] = []
        self.writes = 0

    def run(self) -> Plan:
        env: dict[str, str] = {}
        consts: dict[str, ast.expr] = {}
        for stmt in self.program.module.body:
            match stmt:
                case ast.ImportFrom():
                    pass
                case ast.FunctionDef(name=name):
                    self.step_consts.setdefault(name, dict(consts))
                case ast.Assign(targets=[ast.Name(id=name)], value=value) if is_constant_expr(
                    value
                ):
                    consts[name] = self._expr(value, consts)
                case ast.Assign(targets=[ast.Name(id=name)], value=ast.Call() as call):
                    out = self._unit(call, name, env, consts, "")
                    if out is None:
                        raise LatticeInternalError("write() result was bound to a name")
                    env[name] = out
                case ast.Expr(value=ast.Call() as call):
                    self._unit(call, None, env, consts, "")
                case _:
                    raise LatticeInternalError(f"unexpected statement {type(stmt).__name__}")
        return Plan(tuple(self.nodes))

    # ---- units ----------------------------------------------------------------

    def _unit(
        self,
        call: ast.Call,
        binding: str | None,
        env: Mapping[str, str],
        subst: Subst,
        prefix: str,
    ) -> str | None:
        match call.func:
            case ast.Name(id=name) if name in OP_SHAPES:
                return self._op(call, name, binding, env, subst, prefix)
            case ast.Name(id=name) if name == ITERATE:
                if binding is None:
                    raise LatticeInternalError("iterate() result is not bound to a name")
                return self._iterate(call, binding, env, subst, prefix)
            case ast.Name(id=name) if name in self.step_defs:
                if binding is None:
                    raise LatticeInternalError(f"step call '{name}' is not bound to a name")
                return self._inline(call, name, binding, env, subst, prefix)
            case _:
                raise LatticeInternalError(f"not a unit call: {ast.dump(call.func)}")

    def _expr(self, node: ast.expr, subst: Subst) -> ast.expr:
        if not any(isinstance(n, ast.Name) and n.id in subst for n in ast.walk(node)):
            return node  # nothing to substitute; the executor never mutates
        copied = copy.deepcopy(node)
        out = _Substitute(subst).visit(copied)
        return ast.fix_missing_locations(out)

    def _op(
        self,
        call: ast.Call,
        op: str,
        binding: str | None,
        env: Mapping[str, str],
        subst: Subst,
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
                params = {"predicate": self._expr(call.args[1], subst)}
            case "derive":
                params = {
                    "columns": tuple(
                        (kw.arg, self._expr(kw.value, subst))
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
                params = {"by": _strs(kws["by"]), "aggs": self._aggs(call, subst)}
            case "join":
                how = _str(kws["how"]) if "how" in kws else "inner"
                # fanout="allow" only silences the static L420 check; unused at runtime
                params = {**_join_keys(kws["on"]), "how": how}
                if "fanout" in kws:
                    params["fanout"] = _str(kws["fanout"])
            case "sort":
                desc = kws.get("desc")
                match desc:
                    case None:
                        flag = False
                    case ast.Constant(value=bool(v)):
                        flag = v
                    case ast.List(elts=elts) | ast.Tuple(elts=elts):
                        flag = tuple(_bool_literal(e) for e in elts)
                    case _:
                        raise LatticeInternalError("sort desc= is not a bool literal or list")
                params = {"by": _strs(kws["by"]), "desc": flag}
            case "limit":
                match kws["n"]:
                    case ast.Constant(value=int(n)) if not isinstance(n, bool):
                        params = {"n": n}
                    case _:
                        raise LatticeInternalError("limit n= is not an int literal")
            case "window":
                params = window_params(call, kws, lambda e: self._expr(e, subst))
            case "traverse":
                params = traverse_params(kws, _int(self._expr(kws["max_depth"], subst)))
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

    def _aggs(self, call: ast.Call, subst: Subst) -> tuple[AggSpec, ...]:
        """(name, func, argument, order) per aggregate; `order` is agg.at_min/at_max's
        of= expression, else None."""
        out: list[AggSpec] = []
        for kw in call.keywords:
            if kw.arg is None or kw.arg == "by":
                continue
            match kw.value:
                case ast.Call(func=ast.Attribute(value=ast.Name(id="agg"), attr=func), args=[]):
                    out.append((kw.arg, func, None, None))
                case ast.Call(
                    func=ast.Attribute(value=ast.Name(id="agg"), attr=func),
                    args=[a],
                    keywords=kws,
                ):
                    order = next((k.value for k in kws if k.arg == "of"), None)
                    out.append(
                        (
                            kw.arg,
                            func,
                            self._expr(a, subst),
                            None if order is None else self._expr(order, subst),
                        )
                    )
                case _:
                    raise LatticeInternalError(f"not an aggregate: {ast.dump(kw.value)}")
        return tuple(out)

    def _iterate(
        self,
        call: ast.Call,
        binding: str,
        env: Mapping[str, str],
        subst: Subst,
        prefix: str,
    ) -> str:
        """iterate(step, t, until=, max=): one node whose body is the step inlined over
        a placeholder input (`<binding>/@in`), lowered as its own Plan."""
        step, table = call.args
        if not (isinstance(step, ast.Name) and isinstance(table, ast.Name) and table.id in env):
            raise LatticeInternalError("iterate() arguments are not a step and a table")
        kws = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        node_id = prefix + binding
        param = f"{node_id}/@in"
        outer, self.nodes = self.nodes, []
        try:
            fn = self.step_defs[step.id]
            local = fn.args.args[0].arg
            inner_call = ast.Call(ast.Name(step.id), [ast.Name(local)], [])
            result = self._inline(inner_call, step.id, binding, {local: param}, subst, prefix)
            body = Plan(tuple(self.nodes))
        finally:
            self.nodes = outer
        until = kws["until"]
        t = self.program.types.get(id(call))
        if not isinstance(t, TableT):
            raise LatticeInternalError(f"no table type recorded for iterate() at {span_of(call)}")
        params = {
            "body": body,
            "param": param,
            "result": result,
            "until": ITERATE_FIXPOINT
            if isinstance(until, ast.Constant) and until.value == ITERATE_FIXPOINT
            else self._expr(until, subst),
            "max": _int(self._expr(kws["max"], subst)),
        }
        self.nodes.append(
            Node(node_id, ITERATE, (env[table.id],), params, t.schema, span_of(call))
        )
        return node_id

    # ---- steps ----------------------------------------------------------------

    def _inline(
        self,
        call: ast.Call,
        name: str,
        binding: str,
        env: Mapping[str, str],
        subst: Subst,
        prefix: str,
    ) -> str:
        fn = self.step_defs[name]
        sig = self.program.steps.get(name)
        if sig is None:
            raise LatticeInternalError(f"no signature for step '{name}'")
        kws = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        inner_env: dict[str, str] = {}
        # module constants visible to the body, overridden by parameters and locals
        inner_subst: dict[str, ast.expr] = dict(self.step_consts.get(name, {}))
        table_args = iter(call.args)
        for pname, pt in sig.params:
            match pt:
                case TableT():
                    inner_subst.pop(pname, None)
                    arg = next(table_args, None)
                    match arg:
                        case ast.Name(id=a) if a in env:
                            inner_env[pname] = env[a]
                        case _:
                            raise LatticeInternalError(f"bad table argument for '{pname}'")
                case ScalarParamT(scalar=s):
                    value = _scalar_value(kws[pname], subst)
                    if s is Scalar.FLOAT and isinstance(value, int) and not isinstance(value, bool):
                        value = float(value)  # Int literal accepted for Float: widen now
                    inner_subst[pname] = ast.Constant(value)
                case _:
                    raise LatticeInternalError(f"bad parameter type for '{pname}'")
        inner_prefix = f"{prefix}{binding}/"
        result: str | None = None
        for stmt in fn.body:
            match stmt:
                case ast.Assign(targets=[ast.Name(id=local)], value=value) if is_constant_expr(
                    value
                ):
                    inner_subst[local] = self._expr(value, inner_subst)
                case ast.Assign(targets=[ast.Name(id=local)], value=ast.Call() as body_call):
                    inner_subst.pop(local, None)
                    out = self._unit(body_call, local, inner_env, inner_subst, inner_prefix)
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


def _bool_literal(node: ast.expr) -> bool:
    match node:
        case ast.Constant(value=bool(v)):
            return v
        case _:
            raise LatticeInternalError("sort desc= list element is not a bool literal")


def _int(node: ast.expr) -> int:
    v = literal_value(node)
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    raise LatticeInternalError(f"expected an int literal, got {ast.dump(node)}")


# one window() column: (name, win function, argument or None, offset= / rows= or None)
WinSpec = tuple[str, str, ast.expr | None, int | None]


def window_params(
    call: ast.Call, kws: Mapping[str, ast.expr], expr: Callable[[ast.expr], ast.expr]
) -> dict[str, Any]:
    """window(): {"by", "order", "desc" (one bool per order column), "columns"
    (WinSpec per window column, in keyword order)}. `expr` substitutes an argument's
    constants (and, in the API IR, its parameters)."""
    order = _strs(kws["order"]) if "order" in kws else ()
    descending = set(_strs(kws["desc"])) if "desc" in kws else set()
    columns: list[WinSpec] = []
    for kw in call.keywords:
        if kw.arg is None or kw.arg in WINDOW_KW:
            continue
        match kw.value:
            case ast.Call(func=ast.Attribute(value=ast.Name(id="win"), attr=func), args=args):
                arg = expr(args[0]) if args else None
                count = next((_int(k.value) for k in kw.value.keywords), None)
                columns.append((kw.arg, func, arg, count))
            case _:
                raise LatticeInternalError(f"not a window function: {ast.dump(kw.value)}")
    return {
        "by": _strs(kws["by"]) if "by" in kws else (),
        "order": order,
        "desc": tuple(c in descending for c in order),
        "columns": tuple(columns),
    }


def traverse_params(kws: Mapping[str, ast.expr], max_depth: object) -> dict[str, Any]:
    """traverse(): {"parent", "child", "max_depth" (an int, or an API parameter
    reference), "direction", "depth" (the added column)}."""
    direction = _str(kws["direction"]) if "direction" in kws else "down"
    return {
        "parent": _str(kws["parent"]),
        "child": _str(kws["child"]),
        "max_depth": max_depth,
        "direction": direction,
        "depth": TRAVERSE_DEPTH,
    }
