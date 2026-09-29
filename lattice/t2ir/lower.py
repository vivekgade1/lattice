"""Checked T2 AST -> T2 IR (spec 2026-09-28 go-target §2). Types come from the
checker's side table (`CheckedProcedure.types`), calls from its resolved targets;
fuel and size checks are placed here, exactly where the former AST instrumenter
(replaced by `lattice.t2.emit_py`) charged them, so every emitter charges the same
amounts at the same points.

Fuel placement (as the former instrumenter placed it):

* every `for`/`while` body starts with `Tick()`;
* every comprehension iterable is `Each(iter)`;
* a call whose target is not `proc:` is `Charged(Call(...))`; a `proc:` call is a
  plain `Call` (the callee's entry charges);
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

When the step is charged (the order R620 is raised in, relative to other charges):

* `Charged(Call(method:..., (receiver, *args)))` evaluates the receiver first, then
  charges, then evaluates the other arguments (`<fuel>.call(recv.m)(args)`); a
  `Charged` non-method call charges, then evaluates its arguments;
* `Sized(kind, (call,))` for replace/join/sub/dumps charges its step *before* the
  receiver and arguments are evaluated (`<fuel>.call(<fuel>.<kind>)(recv, args)`),
  then checks the size after they are;
* `AugAssign(grow=True)` evaluates the target's container and index, then the value,
  then runs the size check (`<fuel>.aug`), then the assignment.

Explicit widening (R-widen, docs/lattice-language-rules.md): wherever the checker's
types put an Int-typed value where a Float is expected, the value is wrapped in
`Widen` (an Int literal becomes a Float literal), so every target converts at the same
points and no Float-typed place ever holds an int. The sites, each read off the
checker's side tables:

* an assignment or annotated assignment (the variable's declared type), a
  tuple-unpacking or `for` target (each name's declared type against the type the
  checker bound it to; such a target goes through temporaries), an item store (the
  container's element / value type, and a dict's key type);
* `return` (the declared return type), a procedure's or record's arguments (the
  parameter / field types) and a record's defaults;
* a builtin's, method's or stdlib function's arguments: the parameter types of the
  overload the checker chose, with its type variables bound as it bound them. An
  iterable whose elements are used as Floats is widened as a whole: a tuple
  element-wise, a list, set or dict (its keys) into a fresh list, an iterator lazily;
* list / set / dict literal elements (the literal's element types, a `**d` entry
  included), the branches of an if-expression and the operands of `and` / `or` (their
  result type);
* a tuple indexed with a non-literal index or iterated (`for`, a comprehension, `in`:
  its element type), an `in` probe (the container's element type) and a dict lookup's
  key;
* `sum` of Floats without a start gets the start `0.0` (an empty sum is `0.0`), and
  `Int ** n` typed Float (a non-literal `n`) raises both operands to Floats: it is
  Float arithmetic.

The one-argument `round(Int)` is left exact (its result is an Int), and comparisons are
not widening sites (an Int and a Float compare exactly).

Statements the checker leaves untyped because they cannot run (after a `return`,
`break`, `continue`, an endless `while True`, or an `if` whose branches all return)
are dropped: they never run, so they charge nothing. A statement is untyped when it
holds expressions and none of them is in the checker's side table.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping

from lattice.catalog import T2_BUILTINS, T2_HELPERS, T2_METHODS, T2_STDLIB, StdSig
from lattice.check.astutil import is_constant_expr, is_list_constant_assign, literal_value
from lattice.check.t2 import CheckedProcedure, T2Program
from lattice.check.t2 import types as T
from lattice.check.t2.body import _binop_result, _empty_kind, _fits, sig_type
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
# Methods whose first argument is only compared with the receiver's elements or keys
# (a lookup): an Int there is compared exactly with Float elements, never widened.
_PROBES = frozenset(
    {
        "method:list.index",
        "method:list.count",
        "method:list.remove",
        "method:dict.get",
        "method:dict.pop",
        "method:set.discard",
        "method:set.remove",
    }
)


def _exact_probe(probe: T2Type, slot: T2Type | None) -> bool:
    """An Int looked up among Floats: compared exactly (as `==`), not widened."""
    return probe == T.INT and slot == T.FLOAT


def _key_type(t: T2Type) -> T2Type | None:
    match t:
        case T.DictT(key=k) | T.CounterT(key=k):
            return k
    return None


def _has_float_elems(t: T2Type) -> bool:
    return isinstance(t, T.TupleT) and T.FLOAT in t.elems


def _param(pattern: str, b: T.Bindings) -> T2Type:
    p = sig_type(pattern)
    return p if isinstance(p, T.Pseudo) else T.substitute(p, b, lambda _: T.UNK)


def _widened(src: T2Type, dst: T2Type) -> T2Type:
    """`src` with each Int position where `dst` holds a Float made a Float (the shape
    of `src` kept: `T | None` stays optional, a tuple keeps its arity)."""
    d = T.strip_opt(dst)
    if src == T.INT:
        return T.FLOAT if d == T.FLOAT else src
    match src:
        case T.Opt(inner=i):
            return T.opt(_widened(i, d))
        case T.TupleT(elems=es) if isinstance(d, T.TupleT) and len(d.elems) == len(es):
            return T.TupleT(tuple(_widened(a, b) for a, b in zip(es, d.elems, strict=True)))
        case T.IterT(elem=e) if isinstance(d, T.IterT):
            return T.IterT(_widened(e, d.elem))
        case T.DictT(key=k, val=v) if isinstance(d, T.DictT):
            return T.DictT(_widened(k, d.key), _widened(v, d.val))
    return src


def _coerce(e: n.Expr, dst: T2Type) -> n.Expr:
    """`e` where a value of `dst` is expected: widened (R-widen) when `e`'s type holds an
    Int where `dst` holds a Float, else `e` itself. Literals are widened in place."""
    t = _widened(e.type, dst)
    if t == e.type:
        return e
    match e:
        case n.Const(value=int() as v) if not isinstance(v, bool) and t == T.FLOAT:
            return n.Const(float(v), T.FLOAT)
        case n.Unary(op="-", operand=n.Const(value=int() as v)) if (
            not isinstance(v, bool) and t == T.FLOAT
        ):
            return n.Const(float(-v), T.FLOAT)  # float(-v): -0 is 0.0, not -0.0
        case n.TupleLit(elts=elts) if isinstance(t, T.TupleT):
            items = tuple(_coerce(x, et) for x, et in zip(elts, t.elems, strict=True))
            return n.TupleLit(items, t)
    return n.Widen(e, t)


def _coerce_iter(e: n.Expr, el: T2Type) -> n.Expr:
    """An iterable `e` whose elements are used as values of `el`: a tuple's elements
    widened (in place for a literal), a list / set / dict (its keys) / Counter into a
    fresh list of widened elements (a literal in place), an iterator lazily. `e` itself
    when nothing widens."""
    t = e.type
    match t:
        case T.TupleT(elems=es):
            return _coerce(e, T.TupleT(tuple(el for _ in es)))
        case T.IterT():
            return _coerce(e, T.IterT(el))
        case T.ListT(elem=x) | T.SetT(elem=x) | T.DictT(key=x) | T.CounterT(key=x):
            w = _widened(x, el)
            if w == x:
                return e
            if isinstance(e, n.ListLit):
                return n.ListLit(tuple(_coerce(v, w) for v in e.elts), T.ListT(w))
            if isinstance(e, n.SetLit):
                return n.SetLit(tuple(_coerce(v, w) for v in e.elts), T.SetT(w))
            return n.Widen(e, T.ListT(w))
    return e


def _pow_operands(op: ast.operator, lx: n.Expr, rx: n.Expr, t: T2Type) -> tuple[n.Expr, n.Expr]:
    """The operands of a binary operator typed `t`: `Int ** n` typed Float (a
    non-literal or negative `n`) is Float arithmetic, so both Ints are widened. Any
    other operator (and a power with a Float operand) is left as written: mixed Int and
    Float arithmetic already converts the Int on every target, and `Int / Int` is
    correctly rounded from the exact quotient."""
    if isinstance(op, ast.Pow) and t == T.FLOAT and lx.type == T.INT and rx.type == T.INT:
        return _coerce(lx, T.FLOAT), _coerce(rx, T.FLOAT)
    return lx, rx


def _overloads(target: str) -> tuple[StdSig, ...] | None:
    """The overloads the checker resolved a builtin / helper / stdlib / method call
    against (None for other calls)."""
    kind, _, name = target.partition(":")
    match kind:
        case "builtin":
            return T2_BUILTINS.get(name)
        case "helper":
            return T2_HELPERS.get(name)
        case "std" if name != "collections.defaultdict":  # a factory; nothing to widen
            mod, _, attr = name.partition(".")
            entry = T2_STDLIB.get(mod, {}).get(attr)
            return entry if isinstance(entry, tuple) else None
        case "method":
            owner, _, method = name.rpartition(".")
            return T2_METHODS.get(owner, {}).get(method)
    return None


def _receiver_vars(recv: T2Type) -> dict[str, T2Type]:
    """The type variables a method's receiver fixes (as the checker's _call_method)."""
    match T.strip_opt(recv):
        case T.ListT(elem=e) | T.SetT(elem=e):
            return {"T": e}
        case T.DictT(key=k, val=v):
            return {"K": k, "V": v}
        case T.CounterT(key=k):
            return {"T": k, "K": k, "V": T.INT}
    return {}


def _unsupported(node: ast.AST) -> LatticeInternalError:
    return LatticeInternalError(f"T2 IR: unsupported {type(node).__name__}")


class _Proc:
    """One checked procedure -> its IR body."""

    def __init__(self, proc: CheckedProcedure, program: T2Program | None = None) -> None:
        self.proc = proc
        # the parameter types of the procedures and records it may call (R-widen)
        self.params: dict[str, tuple[tuple[str, T2Type], ...]] = {}
        if program is not None:
            self.params.update({f"proc:{k}": p.sig.params for k, p in program.procedures.items()})
            self.params.update({f"record:{k}": r.fields for k, r in program.records.items()})
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
        """False for a statement the checker did not type because it cannot run: it
        holds expressions, and none of them has a type. A reached statement always has
        a typed expression (the checker types what it checks), except a docstring and
        one without expressions (`pass`, `break`, a bare `return`), which do nothing."""
        if (
            isinstance(s, ast.Expr)
            and isinstance(s.value, ast.Constant)
            and isinstance(s.value.value, str)
        ):
            return True  # a docstring: never typed
        exprs = [e for e in ast.walk(s) if isinstance(e, ast.expr)]
        return not exprs or any(id(e) in self.proc.types for e in exprs)

    def stmt(self, s: ast.stmt) -> list[n.Stmt]:
        match s:
            case ast.Expr(value=ast.Constant(value=str())):
                return [n.Pass()]  # a docstring: nothing runs
            case ast.Expr(value=v):
                return [n.ExprStmt(self.expr(v))]
            case ast.Assign(targets=[t], value=v):
                return self.store(t, self.expr(v))
            case ast.Assign(targets=[first, *rest], value=v):
                return self._chain(first, rest, v)
            case ast.AnnAssign(value=None):
                return [n.Pass()]  # a declaration only: nothing runs
            case ast.AnnAssign(target=t, value=v) if v is not None:
                return self.store(t, self.expr(v))
            case ast.AugAssign(target=t, op=op, value=v):
                return [
                    n.AugAssign(self.target(t), _OPS[type(op)], self.expr(v), type(op) in _GROWING)
                ]
            case ast.If(test=t, body=b, orelse=o):
                return [n.If(self.expr(t), self.block(b), self.block(o))]
            case ast.For(target=t, iter=it, body=b, orelse=o):
                target, after = self.bind_target(t)
                body = self.block(b, loop=True)  # Tick first, then the widened names
                body = (body[0], *after, *body[1:])
                return [n.For(target, self.iterable(self.expr(it)), body, self.block(o))]
            case ast.While(test=t, body=b, orelse=o):
                return [n.While(self.expr(t), self.block(b, loop=True), self.block(o))]
            case ast.Break():
                return [n.Break()]
            case ast.Continue():
                return [n.Continue()]
            case ast.Pass():
                return [n.Pass()]
            case ast.Return(value=v):
                if v is None:
                    return [n.Return(None)]
                return [n.Return(_coerce(self.expr(v), self.proc.sig.returns))]
            case ast.Try(body=b, handlers=hs, orelse=o, finalbody=f):
                handlers = tuple(self.handler(h) for h in hs)
                return [n.Try(self.block(b), handlers, self.block(o), self.block(f))]
        raise _unsupported(s)

    def _chain(self, first: ast.expr, rest: list[ast.expr], value: ast.expr) -> list[n.Stmt]:
        """`a = b = v`: Python evaluates `v` once and assigns it to each target, left to
        right. The first target, when a plain name, is read back for the others; else
        (or when a target widens the value, R-widen) the value goes through a fresh
        temporary."""
        v = self.expr(value)
        out: list[n.Stmt] = []
        if any(self._widens(t, v.type) for t in (first, *rest)):
            temp = self._temp(v.type)
            out.append(n.Assign(n.NameT(temp), v))
            for t in (first, *rest):
                out.extend(self.store(t, n.Var(temp, v.type)))
            return out
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

    # ---- stores (R-widen: the value becomes the type of the place it is stored in) ------

    def store(self, t: ast.expr, v: n.Expr) -> list[n.Stmt]:
        """`t = v` for a lowered value `v`, widened to the target's type."""
        match t:
            case ast.Name(id=name):
                return [n.Assign(n.NameT(name), _coerce(v, self.locals.get(name, T.UNK)))]
            case ast.Subscript(value=obj, slice=index) if not isinstance(index, ast.Slice):
                return [n.Assign(self.target(t), _coerce(v, self._item(obj)))]
        target, after = self.bind_target(t)
        return [n.Assign(target, v), *after]

    def _item(self, obj: ast.expr) -> T2Type:
        """The type of an item stored into the container `obj`."""
        match self.ty(obj):
            case T.ListT(elem=e):
                return e
            case T.DictT(val=v):
                return v
            case T.CounterT():
                return T.INT
        return T.UNK

    def _widens(self, t: ast.expr, vt: T2Type) -> bool:
        """Storing a value of type `vt` into target `t` widens (some part of) it."""
        match t:
            case ast.Name(id=name):
                return _widened(vt, self.locals.get(name, T.UNK)) != vt
            case ast.Subscript(value=obj, slice=index) if not isinstance(index, ast.Slice):
                return _widened(vt, self._item(obj)) != vt
            case ast.Tuple(elts=elts) | ast.List(elts=elts):
                return any(self._widens(e, self.ty(e)) for e in elts)
        return False

    def bind_target(self, t: ast.expr) -> tuple[n.AssignTarget, list[n.Stmt]]:
        """An unpacking or `for` target, and the stores to run after it binds. When a
        name (or item) of it widens the value the checker bound it to, every name of it
        binds a fresh temporary instead, and the stores assign them, widened, in the
        target's order (so an item's container and index are evaluated as late as
        Python evaluates them)."""
        if not isinstance(t, ast.Tuple | ast.List | ast.Name | ast.Subscript) or not (
            self._widens(t, self.ty(t))
        ):
            return self.target(t), []
        after: list[n.Stmt] = []

        def via_temp(x: ast.expr) -> n.AssignTarget:
            if isinstance(x, ast.Tuple | ast.List):
                return n.TupleTarget(tuple(via_temp(e) for e in x.elts))
            src = self.ty(x)  # the type the checker bound to this name / item
            temp = self._temp(src)
            after.extend(self.store(x, n.Var(temp, src)))
            return n.NameT(temp)

        return via_temp(t), after

    def target(self, t: ast.expr) -> n.AssignTarget:
        match t:
            case ast.Name(id=name):
                return n.NameT(name)
            case ast.Tuple(elts=elts) | ast.List(elts=elts):
                return n.TupleTarget(tuple(self.target(e) for e in elts))
            case ast.Subscript(value=obj, slice=ast.Slice(lower=lo, upper=hi, step=st)):
                return n.SliceT(self.expr(obj), self._opt(lo), self._opt(hi), self._opt(st))
            case ast.Subscript(value=obj, slice=index):
                o = self.expr(obj)
                return n.IndexT(o, self._key(o, self.expr(index)))
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
                return self.index(e, obj, index)
            case ast.UnaryOp(op=op, operand=operand):
                return n.Unary(_UNARY[type(op)], self.expr(operand), self.ty(e))
            case ast.BinOp(left=left, op=op, right=right):
                node = n.Grow if type(op) in _GROWING else n.Binary
                lx, rx, t = self.expr(left), self.expr(right), self.ty(e)
                return node(_OPS[type(op)], *_pow_operands(op, lx, rx, t), t)
            case ast.BoolOp(op=op, values=values):
                t = self.ty(e)
                vs = tuple(_coerce(self.expr(v), t) for v in values)
                return n.BoolOp(_BOOL[type(op)], vs, t)
            case ast.Compare(left=left, ops=ops, comparators=comps):
                operands = tuple(self.expr(x) for x in (left, *comps))
                if len(ops) == 1 and isinstance(ops[0], ast.In | ast.NotIn):
                    operands = self._membership(*operands)
                return n.Compare(tuple(_CMP[type(o)] for o in ops), operands, self.ty(e))
            case ast.IfExp(test=test, body=body, orelse=orelse):
                t, c = self.ty(e), self.expr(test)
                b, o = _coerce(self.expr(body), t), _coerce(self.expr(orelse), t)
                return n.IfExp(c, b, o, t)
            case ast.Call():
                return self.call(e)
            case ast.List(elts=elts):
                t = self.ty(e)
                el = t.elem if isinstance(t, T.ListT) else T.UNK
                return n.ListLit(tuple(_coerce(self.expr(x), el) for x in elts), t)
            case ast.Set(elts=elts):
                t = self.ty(e)
                el = t.elem if isinstance(t, T.SetT) else T.UNK
                return n.SetLit(tuple(_coerce(self.expr(x), el) for x in elts), t)
            case ast.Tuple(elts=elts):
                return n.TupleLit(tuple(self.expr(x) for x in elts), self.ty(e))
            case ast.Dict(keys=keys, values=values):
                t = self.ty(e)
                kt, vt = (t.key, t.val) if isinstance(t, T.DictT) else (T.UNK, T.UNK)
                ks = tuple(None if k is None else _coerce(self.expr(k), kt) for k in keys)
                vs = tuple(
                    _coerce(self.expr(v), t if k is None else vt)  # **d: the whole dict
                    for k, v in zip(keys, values, strict=True)
                )
                return n.DictLit(ks, vs, t)
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

    def index(self, e: ast.expr, obj: ast.expr, index: ast.expr) -> n.Expr:
        """`obj[index]`: a dict's key widened to its key type; a tuple indexed with a
        non-literal index holds its element type (the join of its elements) at every
        position, so it is widened to that first."""
        o, i, t = self.expr(obj), self.expr(index), self.ty(e)
        k = literal_value(index)
        if isinstance(o.type, T.TupleT) and not (isinstance(k, int) and not isinstance(k, bool)):
            o = _coerce(o, T.TupleT(tuple(t for _ in o.type.elems)))
        if _exact_probe(i.type, _key_type(o.type)):
            return n.Index(o, i, t)  # a lookup is exact (a store widens the key)
        return n.Index(o, self._key(o, i), t)

    @staticmethod
    def _key(obj: n.Expr, key: n.Expr) -> n.Expr:
        """A dict's (or Counter's) key, widened to its key type."""
        match obj.type:
            case T.DictT(key=k) | T.CounterT(key=k):
                return _coerce(key, k)
        return key

    @staticmethod
    def iterable(e: n.Expr) -> n.Expr:
        """An iterated value: a tuple's elements are its element type (their join)."""
        if isinstance(e.type, T.TupleT):
            el = T.elem_of(e.type)
            if el is not None:
                return _coerce_iter(e, el)
        return e

    @staticmethod
    def _membership(x: n.Expr, c: n.Expr) -> tuple[n.Expr, n.Expr]:
        """`x in c`: the probe widened to the container's element type, and a tuple's
        elements to their join (a Float probe into Int elements stays exact)."""
        if c.type == T.STR or isinstance(c.type, T.Opt):
            return x, c
        el = T.elem_of(c.type)
        if el is None:
            return x, c
        if (x.type == T.INT and el == T.FLOAT) or (
            x.type in T.NUMERIC and _has_float_elems(c.type)
        ):
            return x, c  # Ints and Floats compare exactly, as `==` (nothing is widened)
        return _coerce(x, el), _Proc.iterable(c)

    def gens(self, gens: list[ast.comprehension]) -> tuple[n.Gen, ...]:
        """Each clause's iterable is charged per element (`<fuel>.each`)."""
        out: list[n.Gen] = []
        for g in gens:
            if g.is_async:
                raise _unsupported(g)
            it = self.iterable(self.expr(g.iter))
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
        if target.startswith(("proc:", "record:")):
            params = dict(self.params.get(target, ()))
            own = [_coerce(a, params.get(p, T.UNK)) for a, p in zip(own, params, strict=False)]
            kwargs = tuple((k, _coerce(v, params.get(k, T.UNK))) for k, v in kwargs)
        else:
            receiver, own, kwargs = self._widen_args(target, e, receiver, own, kwargs)
            if target == "builtin:sum" and len(own) == 1 and t == T.FLOAT:
                own.append(n.Const(0.0, T.FLOAT))  # sum of Floats starts at 0.0 (R-widen)
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

    def _widen_args(
        self,
        target: str,
        e: ast.Call,
        receiver: list[n.Expr],
        own: list[n.Expr],
        kwargs: tuple[tuple[str, n.Expr], ...],
    ) -> tuple[list[n.Expr], list[n.Expr], tuple[tuple[str, n.Expr], ...]]:
        """A builtin's / method's / stdlib function's arguments widened to the
        parameter types of the overload the checker chose (its type variables bound as
        the checker bound them: `_fits` over the same argument types)."""
        overloads = _overloads(target)
        if overloads is None or (target == "builtin:round" and len(own) == 1):
            return receiver, own, kwargs  # round(Int) is an exact Int
        fixed = _receiver_vars(receiver[0].type) if receiver else {}

        def typed(a: ast.expr) -> T2Type | None:
            return None if isinstance(a, ast.Lambda) or _empty_kind(a) else self.ty(a)

        arg_types = [typed(a) for a in e.args]
        kw_types = {kw.arg or "": typed(kw.value) for kw in e.keywords}
        for sig in overloads:
            b = T.Bindings(dict(fixed))
            if _fits(sig, arg_types, kw_types, b, frozenset(fixed)):
                break
        else:
            raise LatticeInternalError(f"T2 IR: no overload of {target} fits {ast.unparse(e)}")

        def widen(a: n.Expr, pattern: str) -> n.Expr:
            p = sig_type(pattern)
            if isinstance(p, T.Pseudo):
                if p.name != "Iterable":
                    return a  # Key[T]: a lambda
                return _coerce_iter(a, T.substitute(p.arg, b, lambda _: T.UNK))
            return _coerce(a, T.substitute(p, b, lambda _: T.UNK))

        params = [*sig.params, *sig.optional]
        own = [
            a
            if i == 0 and target in _PROBES and _exact_probe(a.type, _param(p, b))
            else widen(a, p)
            for i, (a, p) in enumerate(zip(own, params, strict=False))
        ]
        kwargs = tuple((k, widen(v, sig.kwargs[k])) for k, v in kwargs)
        return receiver, own, kwargs


# ---- module constants --------------------------------------------------------------------


def _constants(t2: T2Program, source: ast.Module | None) -> tuple[tuple[str, T2Type, n.Expr], ...]:
    """The module constants procedures may read, in source order -- selected exactly as
    the former instrumenter selected them, and copied without fuel (the emitted
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
            return n.Binary(_OPS[type(op)], *_pow_operands(op, lx, rx, t), t)
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
            return n.BoolOp(_BOOL[type(op)], tuple(_coerce(v, t) for v in vs), t)
        case ast.IfExp(test=test, body=body, orelse=orelse):
            b, o = _const_expr(body, known), _const_expr(orelse, known)
            t = T.join(b.type, o.type)
            if t is None:
                raise _unsupported(e)
            return n.IfExp(_const_expr(test, known), _coerce(b, t), _coerce(o, t), t)
    raise _unsupported(e)


def _defaults(
    fields: tuple[tuple[str, T2Type], ...], defaults: Mapping[str, object]
) -> dict[str, object]:
    """A record's literal defaults, an Int default of a Float field as a float (R-widen)."""
    types = dict(fields)
    out: dict[str, object] = {}
    for f, v in defaults.items():
        widen = T.strip_opt(types.get(f, T.UNK)) == T.FLOAT
        out[f] = float(v) if widen and isinstance(v, int) and not isinstance(v, bool) else v
    return out


# ---- the module ---------------------------------------------------------------------------


def lower_t2(t2: T2Program, source: ast.Module | None) -> n.T2Module:
    """The IR of every procedure and record of `t2`. `source` is the program's parsed
    source: the module constants procedures read are lowered from it (none without)."""
    records = tuple(
        n.Record(rec.name, rec.fields, _defaults(rec.fields, rec.defaults))
        for rec in t2.records.values()
    )
    procedures = tuple(_Proc(proc, t2).procedure() for proc in t2.procedures.values())
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
