"""Checked T2 AST -> T2 IR (spec 2026-09-28 go-target §2). Types come from the
checker's side table (`CheckedProcedure.types`), calls from its resolved targets;
fuel and size checks are placed here, exactly where `lattice.t2.compile` used to
instrument them, so every emitter charges the same amounts at the same points.

Fuel placement (mirrors `lattice.t2.compile._Instrument`):

* every `for`/`while` body starts with `Tick()`;
* every comprehension iterable is `Each(iter)`;
* a call whose target is not `proc:` is `Charged(Call(...))` -- the step is charged
  after the callee (for a method: the receiver, `args[0]`) is evaluated and before
  the other arguments are; a `proc:` call is a plain `Call` (the callee's entry
  charges);
* the arguments a builtin/method consumes whole (`_CONSUMERS`) are `Bulk(arg)`
  (`min`/`max` only in their one-argument form);
* `+ * ** <<` are `Grow(op, a, b)`; their augmented forms are `AugAssign(grow=True)`;
* `Str.ljust/rjust/center/zfill`: the width argument is `Sized("pad", (width,))`
  inside the charged call (`<fuel>.width(n)`);
* `Str.replace`, `Str.join`, `re.sub`, `json.dumps` are `Sized(kind, (call,))` around
  the uncharged `Call` they replace: the sized operation charges the call's step, its
  scan and its result's size itself (`<fuel>.call(<fuel>.<kind>)(...)`); `Str.join`'s
  items are therefore not `Bulk` (`<fuel>.join` charges them);
* the input (second argument) of `re.fullmatch/match/search/findall/split` is `Scan`;
* `f"{x:spec}"` is `FPart(x, spec, conversion)`; a non-empty spec is charged by the
  emitter (`<fuel>.fmt`), a bare conversion (`{x!r}`) is not.

Statements the checker leaves untyped because they cannot run (after a `return`,
`break`, `continue`, or an endless `while True`) are dropped: they never run, so they
charge nothing.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping

from lattice.check.astutil import is_constant_expr, is_list_constant_assign
from lattice.check.t2 import CheckedProcedure, T2Program
from lattice.check.t2 import types as T
from lattice.check.t2.body import _binop_result
from lattice.check.t2.types import T2Type
from lattice.runtime import LatticeInternalError
from lattice.t2ir import nodes as n

__all__ = ["lower_t2"]

# Arguments a builtin / method consumes whole: target -> positions (in the call's own
# arguments, not counting a method's receiver) charged per element.
_CONSUMERS: Mapping[str, tuple[int, ...]] = {
    "builtin:sum": (0,),
    "builtin:sorted": (0,),
    "builtin:list": (0,),
    "builtin:set": (0,),
    "builtin:dict": (0,),
    "builtin:any": (0,),
    "builtin:all": (0,),
    "builtin:min": (0,),
    "builtin:max": (0,),
    "std:collections.Counter": (0,),
    "method:Str.join": (0,),
    "method:list.extend": (0,),
    "method:set.update": (0,),
    "method:set.union": (0,),
    "method:set.intersection": (0,),
    "method:set.difference": (0,),
    "method:set.issubset": (0,),
    "method:set.issuperset": (0,),
}
# min(a, b) / max(a, b) compare their arguments; only the one-argument form consumes one
_ONE_ARG_ONLY = frozenset({"builtin:min", "builtin:max"})
_OPS: Mapping[type, str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.Mod: "%",
    ast.Pow: "**",
    ast.LShift: "<<",
    ast.RShift: ">>",
    ast.BitAnd: "&",
    ast.BitOr: "|",
    ast.BitXor: "^",
}
# Operators whose result can grow with an operand (charged by size before they run).
_GROWING = frozenset({ast.Add, ast.Mult, ast.Pow, ast.LShift})
_UNARY: Mapping[type, str] = {ast.USub: "-", ast.UAdd: "+", ast.Not: "not", ast.Invert: "~"}
_BOOL: Mapping[type, str] = {ast.And: "and", ast.Or: "or"}
_CMP: Mapping[type, str] = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.In: "in",
    ast.NotIn: "not in",
    ast.Is: "is",
    ast.IsNot: "is not",
}
_CONVERSIONS: Mapping[int, str] = {-1: "", 115: "s", 114: "r", 97: "a"}
# Methods padding their receiver to a width (the first argument).
_PADDING = frozenset(
    {"method:Str.ljust", "method:Str.rjust", "method:Str.center", "method:Str.zfill"}
)
# Calls replaced by a size-checked operation (which also charges the call's step).
_SIZED: Mapping[str, str] = {
    "method:Str.replace": "replace",
    "method:Str.join": "join",
    "std:re.sub": "sub",
    "std:json.dumps": "dumps",
}
# Regular expressions: the input (second argument) is charged its length.
_SCANS = frozenset(
    {"std:re.fullmatch", "std:re.match", "std:re.search", "std:re.findall", "std:re.split"}
)
_TEMP = "_lt_t"


def _unsupported(node: ast.AST) -> LatticeInternalError:
    return LatticeInternalError(f"T2 IR: unsupported {type(node).__name__}")


class _Proc:
    """One checked procedure -> its IR body."""

    def __init__(self, proc: CheckedProcedure) -> None:
        self.proc = proc
        self.locals: dict[str, T2Type] = dict(proc.locals)
        self._used = {x.id for x in ast.walk(proc.node) if isinstance(x, ast.Name)} | {
            a.arg for a in ast.walk(proc.node) if isinstance(a, ast.arg)
        }
        self._temps = 0

    def procedure(self) -> n.Procedure:
        proc = self.proc
        body = self.block(proc.node.body)
        return n.Procedure(
            name=proc.name,
            params=proc.sig.params,
            returns=proc.sig.returns,
            locals=self.locals,
            body=body,
            recursive=proc.is_recursive,
        )

    # ---- helpers ------------------------------------------------------------------

    def ty(self, node: ast.AST) -> T2Type:
        t = self.proc.types.get(id(node))
        if t is None:
            raise LatticeInternalError(f"T2 IR: no type for {ast.dump(node)[:80]}")
        return t

    def _temp(self, t: T2Type) -> str:
        while True:
            self._temps += 1
            name = f"{_TEMP}{self._temps}"
            if name not in self._used and name not in self.locals:
                self.locals[name] = t
                return name

    # ---- statements ---------------------------------------------------------------

    def block(self, stmts: Iterable[ast.stmt], loop: bool = False) -> tuple[n.Stmt, ...]:
        out: list[n.Stmt] = [n.Tick()] if loop else []
        for s in stmts:
            if not self._reached(s):
                break  # the checker stopped here: the rest of the block never runs
            out.extend(self.stmt(s))
            if isinstance(s, ast.Return | ast.Break | ast.Continue):
                break  # nothing after it runs (the checker does not type it either)
        return tuple(out)

    def _reached(self, s: ast.stmt) -> bool:
        """False for a statement the checker did not type because it cannot run: its
        first evaluated expression has no type (a `try`: its first statement's)."""
        head: ast.AST | None
        match s:
            case ast.Try(body=[first, *_]):
                return self._reached(first)
            case ast.Assign(value=v) | ast.AugAssign(value=v) | ast.Expr(value=ast.Call() as v):
                head = v
            case ast.AnnAssign(target=t, value=v):
                head = v if v is not None else t
            case ast.If(test=t) | ast.While(test=t):
                head = t
            case ast.For(iter=it):
                head = it
            case ast.Return(value=v):
                head = v
            case _:
                head = None
        return head is None or id(head) in self.proc.types

    def stmt(self, s: ast.stmt) -> list[n.Stmt]:
        match s:
            case ast.Expr(value=ast.Constant(value=str())):
                return [n.Pass()]  # a docstring: nothing runs
            case ast.Expr(value=v):
                return [n.ExprStmt(self.expr(v))]
            case ast.Assign(targets=[t], value=v):
                return [n.Assign(self.target(t), self.expr(v))]
            case ast.Assign(targets=[first, *rest], value=v):
                return self._chain(first, rest, v)
            case ast.AnnAssign(value=None):
                return [n.Pass()]  # a declaration only: nothing runs
            case ast.AnnAssign(target=t, value=v) if v is not None:
                return [n.Assign(self.target(t), self.expr(v))]
            case ast.AugAssign(target=t, op=op, value=v):
                return [
                    n.AugAssign(self.target(t), _OPS[type(op)], self.expr(v), type(op) in _GROWING)
                ]
            case ast.If(test=t, body=b, orelse=o):
                return [n.If(self.expr(t), self.block(b), self.block(o))]
            case ast.For(target=t, iter=it, body=b, orelse=o):
                return [
                    n.For(self.target(t), self.expr(it), self.block(b, loop=True), self.block(o))
                ]
            case ast.While(test=t, body=b, orelse=o):
                return [n.While(self.expr(t), self.block(b, loop=True), self.block(o))]
            case ast.Break():
                return [n.Break()]
            case ast.Continue():
                return [n.Continue()]
            case ast.Pass():
                return [n.Pass()]
            case ast.Return(value=v):
                return [n.Return(None if v is None else self.expr(v))]
            case ast.Try(body=b, handlers=hs, orelse=o, finalbody=f):
                handlers = tuple(self.handler(h) for h in hs)
                return [n.Try(self.block(b), handlers, self.block(o), self.block(f))]
        raise _unsupported(s)

    def _chain(self, first: ast.expr, rest: list[ast.expr], value: ast.expr) -> list[n.Stmt]:
        """`a = b = v`: Python evaluates `v` once and assigns it to each target, left to
        right. The first target, when a plain name, is read back for the others; else
        the value goes through a fresh temporary."""
        v = self.expr(value)
        out: list[n.Stmt] = []
        if isinstance(first, ast.Name):
            out.append(n.Assign(self.target(first), v))
            source: n.Expr = n.Var(first.id, v.type)
            targets = rest
        else:
            temp = self._temp(v.type)
            out.append(n.Assign(n.NameT(temp), v))
            source = n.Var(temp, v.type)
            targets = [first, *rest]
        out.extend(n.Assign(self.target(t), source) for t in targets)
        return out

    def handler(self, h: ast.ExceptHandler) -> n.Handler:
        match h.type:
            case ast.Name(id=kind):
                kinds: tuple[str, ...] = (kind,)
            case ast.Tuple(elts=elts) if all(isinstance(e, ast.Name) for e in elts):
                kinds = tuple(e.id for e in elts)  # type: ignore[attr-defined]
            case _:
                raise _unsupported(h)
        return n.Handler(kinds, h.name, self.block(h.body))

    def target(self, t: ast.expr) -> n.AssignTarget:
        match t:
            case ast.Name(id=name):
                return n.NameT(name)
            case ast.Tuple(elts=elts) | ast.List(elts=elts):
                return n.TupleTarget(tuple(self.target(e) for e in elts))
            case ast.Subscript(value=obj, slice=ast.Slice(lower=lo, upper=hi, step=st)):
                return n.SliceT(self.expr(obj), self._opt(lo), self._opt(hi), self._opt(st))
            case ast.Subscript(value=obj, slice=index):
                return n.IndexT(self.expr(obj), self.expr(index))
            case ast.Attribute(value=obj, attr=name):
                return n.AttrT(self.expr(obj), name)
        raise _unsupported(t)

    # ---- expressions ----------------------------------------------------------------

    def _opt(self, e: ast.expr | None) -> n.Expr | None:
        return None if e is None else self.expr(e)

    def expr(self, e: ast.expr) -> n.Expr:
        match e:
            case ast.Constant(value=value):
                return n.Const(value, self.ty(e))
            case ast.Name(id=name):
                return n.Var(name, self.ty(e))
            case ast.Attribute(value=obj, attr=name):
                return n.Attr(self.expr(obj), name, self.ty(e))
            case ast.Subscript(value=obj, slice=ast.Slice(lower=lo, upper=hi, step=st)):
                lowered = self.expr(obj)
                return n.SliceOf(lowered, self._opt(lo), self._opt(hi), self._opt(st), self.ty(e))
            case ast.Subscript(value=obj, slice=index):
                return n.Index(self.expr(obj), self.expr(index), self.ty(e))
            case ast.UnaryOp(op=op, operand=operand):
                return n.Unary(_UNARY[type(op)], self.expr(operand), self.ty(e))
            case ast.BinOp(left=left, op=op, right=right):
                node = n.Grow if type(op) in _GROWING else n.Binary
                return node(_OPS[type(op)], self.expr(left), self.expr(right), self.ty(e))
            case ast.BoolOp(op=op, values=values):
                return n.BoolOp(_BOOL[type(op)], tuple(self.expr(v) for v in values), self.ty(e))
            case ast.Compare(left=left, ops=ops, comparators=comps):
                operands = tuple(self.expr(x) for x in (left, *comps))
                return n.Compare(tuple(_CMP[type(o)] for o in ops), operands, self.ty(e))
            case ast.IfExp(test=test, body=body, orelse=orelse):
                return n.IfExp(self.expr(test), self.expr(body), self.expr(orelse), self.ty(e))
            case ast.Call():
                return self.call(e)
            case ast.List(elts=elts):
                return n.ListLit(tuple(self.expr(x) for x in elts), self.ty(e))
            case ast.Set(elts=elts):
                return n.SetLit(tuple(self.expr(x) for x in elts), self.ty(e))
            case ast.Tuple(elts=elts):
                return n.TupleLit(tuple(self.expr(x) for x in elts), self.ty(e))
            case ast.Dict(keys=keys, values=values):
                ks = tuple(None if k is None else self.expr(k) for k in keys)
                return n.DictLit(ks, tuple(self.expr(v) for v in values), self.ty(e))
            case ast.ListComp(elt=elt, generators=gens):
                return n.Comp("list", self.expr(elt), None, self.gens(gens), self.ty(e))
            case ast.SetComp(elt=elt, generators=gens):
                return n.Comp("set", self.expr(elt), None, self.gens(gens), self.ty(e))
            case ast.GeneratorExp(elt=elt, generators=gens):
                return n.Comp("gen", self.expr(elt), None, self.gens(gens), self.ty(e))
            case ast.DictComp(key=key, value=value, generators=gens):
                gs = self.gens(gens)
                return n.Comp("dict", self.expr(key), self.expr(value), gs, self.ty(e))
            case ast.JoinedStr():
                return self.fstr(e)
            case ast.Lambda(args=args, body=body):
                params = tuple(a.arg for a in args.args)
                return n.Lambda(params, self.expr(body), self.ty(e))
            case ast.NamedExpr(target=ast.Name(id=name), value=value):
                return n.Walrus(name, self.expr(value), self.ty(e))
        raise _unsupported(e)

    def gens(self, gens: list[ast.comprehension]) -> tuple[n.Gen, ...]:
        """Each clause's iterable is charged per element (`<fuel>.each`)."""
        out: list[n.Gen] = []
        for g in gens:
            if g.is_async:
                raise _unsupported(g)
            it = self.expr(g.iter)
            ifs = tuple(self.expr(c) for c in g.ifs)
            out.append(n.Gen(self.target(g.target), n.Each(it, it.type), ifs))
        return tuple(out)

    def fstr(self, e: ast.JoinedStr) -> n.FStr:
        parts: list[str | n.FPart] = []
        for v in e.values:
            match v:
                case ast.Constant(value=str() as text):
                    parts.append(text)
                case ast.FormattedValue(value=value, conversion=conv, format_spec=spec):
                    lowered_spec: str | n.FStr = ""
                    if isinstance(spec, ast.JoinedStr):
                        if all(isinstance(p, ast.Constant) for p in spec.values):
                            lowered_spec = "".join(str(p.value) for p in spec.values)  # type: ignore[attr-defined]
                        else:
                            lowered_spec = self.fstr(spec)
                    parts.append(n.FPart(self.expr(value), lowered_spec, _CONVERSIONS[conv]))
                case _:
                    raise _unsupported(v)
        return n.FStr(tuple(parts), self.ty(e))

    def call(self, e: ast.Call) -> n.Expr:
        target = self.proc.targets.get(id(e))
        if target is None:
            raise LatticeInternalError(f"T2 IR: unresolved call {ast.unparse(e)[:80]}")
        method = target.startswith("method:")
        if method and not isinstance(e.func, ast.Attribute):
            raise _unsupported(e.func)
        receiver = [self.expr(e.func.value)] if method else []  # type: ignore[attr-defined]
        own = [self.expr(a) for a in e.args]  # the call's own arguments (no receiver)
        kwargs = tuple((kw.arg, self.expr(kw.value)) for kw in e.keywords if kw.arg is not None)
        if len(kwargs) != len(e.keywords):
            raise _unsupported(e)
        t = self.ty(e)
        if target.startswith("proc:"):
            return n.Call(target, tuple(own), kwargs, t)
        kind = _SIZED.get(target)
        if kind is not None:
            return n.Sized(kind, (n.Call(target, (*receiver, *own), kwargs, t),), t)
        if target in _PADDING and own:
            own[0] = n.Sized("pad", (own[0],), own[0].type)
        if target in _SCANS and len(own) > 1:
            own[1] = n.Scan(own[1], own[1].type)
        positions = _CONSUMERS.get(target, ())
        if target in _ONE_ARG_ONLY and len(e.args) != 1:
            positions = ()
        for i in positions:
            if i < len(own):
                own[i] = n.Bulk(own[i], own[i].type)
        return n.Charged(n.Call(target, (*receiver, *own), kwargs, t), t)


# ---- module constants --------------------------------------------------------------------


def _constants(t2: T2Program, source: ast.Module | None) -> tuple[tuple[str, T2Type, n.Expr], ...]:
    """The module constants procedures may read, in source order -- selected exactly as
    `lattice.t2.compile._constants` selects them, and copied without fuel (the compiled
    module binds them uncharged)."""
    if source is None or not t2.constants:
        return ()
    out: list[tuple[str, T2Type, n.Expr]] = []
    for stmt in source.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id in t2.constants
            and (is_constant_expr(stmt.value) or is_list_constant_assign(stmt))
        ):
            name = stmt.targets[0].id
            out.append((name, t2.constants[name], _const_expr(stmt.value, t2.constants)))
    return tuple(out)


def _literal_type(value: object) -> T2Type:
    if isinstance(value, bool):
        return T.BOOL
    if isinstance(value, int):
        return T.INT
    if isinstance(value, float):
        return T.FLOAT
    if isinstance(value, str):
        return T.STR
    if value is None:
        return T.NONE
    raise LatticeInternalError(f"T2 IR: unsupported constant {value!r}")


def _const_expr(e: ast.expr, known: Mapping[str, T2Type]) -> n.Expr:
    """A module constant's value (a scalar constant expression or a list of string
    literals), typed as the checker typed it (`BodyChecker.constant`)."""
    match e:
        case ast.Constant(value=value):
            return n.Const(value, _literal_type(value))
        case ast.Name(id=name) if name in known:
            return n.Var(name, known[name])
        case ast.List(elts=elts):
            items = tuple(_const_expr(x, known) for x in elts)
            return n.ListLit(items, T.ListT(T.STR))
        case ast.UnaryOp(op=op, operand=operand):
            inner = _const_expr(operand, known)
            t = T.BOOL if isinstance(op, ast.Not) else inner.type
            return n.Unary(_UNARY[type(op)], inner, t)
        case ast.BinOp(left=left, op=op, right=right):
            lx, rx = _const_expr(left, known), _const_expr(right, known)
            t = _binop_result(op, lx.type, rx.type, right)
            if t is None:
                raise _unsupported(e)
            return n.Binary(_OPS[type(op)], lx, rx, t)
        case ast.Compare(left=left, ops=ops, comparators=comps):
            operands = tuple(_const_expr(x, known) for x in (left, *comps))
            return n.Compare(tuple(_CMP[type(o)] for o in ops), operands, T.BOOL)
        case ast.BoolOp(op=op, values=values):
            vs = tuple(_const_expr(v, known) for v in values)
            t: T2Type | None = vs[0].type
            for v in vs[1:]:
                t = T.join(t, v.type) if t is not None else None
            if t is None:
                raise _unsupported(e)
            return n.BoolOp(_BOOL[type(op)], vs, t)
        case ast.IfExp(test=test, body=body, orelse=orelse):
            b, o = _const_expr(body, known), _const_expr(orelse, known)
            t = T.join(b.type, o.type)
            if t is None:
                raise _unsupported(e)
            return n.IfExp(_const_expr(test, known), b, o, t)
    raise _unsupported(e)


# ---- the module ---------------------------------------------------------------------------


def lower_t2(t2: T2Program, source: ast.Module | None) -> n.T2Module:
    """The IR of every procedure and record of `t2`. `source` is the program's parsed
    source: the module constants procedures read are lowered from it (none without)."""
    records = tuple(
        n.Record(rec.name, rec.fields, dict(rec.defaults)) for rec in t2.records.values()
    )
    procedures = tuple(_Proc(proc).procedure() for proc in t2.procedures.values())
    natives: set[str] = set()
    for proc in t2.procedures.values():
        natives |= proc.natives
    return n.T2Module(
        procedures=procedures,
        records=records,
        constants=_constants(t2, source),
        imports=dict(t2.imports),
        natives=tuple(sorted(natives)),
    )
