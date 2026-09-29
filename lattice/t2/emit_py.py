"""T2 IR -> the Python module compiled procedures run as (spec 2026-09-28 go-target §2).

`emit_python(module)` writes the source of one Python module from the IR of a checked
program's procedures and records (`lattice.t2ir.lower.lower_t2`). `lattice.t2.support`
runs it (`t2_load`) in a restricted namespace with no ambient builtins, in the runtime
(`lattice.api.runtime`) and in generated apps (`lattice.api.codegen`) alike.

The module's shape:

* a preamble binds exactly the names procedures may reach: the allowlisted builtins
  and errors, the Duration helpers, the imported stdlib names (a module becomes a
  namespace holding only its allowlisted names), the Int range check and the set type
  (`<fuel>_int`, `<fuel>_set`), the module constants procedures read, and each
  `@record` as a frozen dataclass;
* each procedure becomes `def name(<fuel>, params...)`, whose body is `<fuel>.enter()`
  (one step, one level of depth) and the procedure's body inside
  `try: ... finally: <fuel>.leave()`; a procedure call passes the fuel on.

Fuel is charged exactly where the IR places it (see `lattice.t2ir.lower`): `Tick` is
`<fuel>.tick()`, `Each(it)` `<fuel>.each(it)`, `Bulk(v)` `<fuel>.bulk(v)`,
`Charged(call)` `<fuel>.call(f)(args)` (a method's receiver is evaluated before the
charge, its arguments after), `Grow(op, a, b)` `<fuel>.add/mul/pow/lshift(a, b)`,
`Sized(kind, (call,))` `<fuel>.call(<fuel>.<kind>)(receiver or function, args)`,
`Sized("pad", (n,))` `<fuel>.width(n)`, `Scan(v)` `<fuel>.scan(v)`, a replacement field
with a spec `<fuel>.fmt(v, spec, conversion)`, and a growing augmented assignment binds
its container, index and value to temporaries, runs `<fuel>.aug(op, current, value)`,
then assigns.

Semantics v1.1 (spec 2026-09-28 go-target §3.1): every Int result that can leave the 64-bit
range -- `-` (unary and binary), `//`, the growing operators, and Int-typed `abs`,
`int`, `round`, `sum`, `math.floor/ceil/trunc/gcd` -- goes through `<fuel>_int`
(R623); a `sum` is checked on its result only, as Python's intermediate sums are exact.
Set literals, set comprehensions and `set(...)` build `<fuel>_set` (the
insertion-ordered `T2Set`). `Widen` (R-widen) is `<fuel>_float(x)` for an Int, and
`<fuel>_widen(x, how)` (`t2_widen`) for an optional value, a tuple, an iterator or a
container.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from lattice.catalog import T2_STDLIB
from lattice.check.t2 import types as T
from lattice.check.t2.types import T2Type
from lattice.runtime import LatticeInternalError
from lattice.t2.support import T2_BUILTIN_NAMES
from lattice.t2ir import nodes as n
from lattice.t2ir.lower import _widened

__all__ = ["T2Code", "emit_python"]

_HELPERS = ("days", "hours", "minutes", "seconds")
_FUEL = "_lt_fuel"
_BINOPS: Mapping[str, type[ast.operator]] = {
    "+": ast.Add,
    "-": ast.Sub,
    "*": ast.Mult,
    "/": ast.Div,
    "//": ast.FloorDiv,
    "%": ast.Mod,
    "**": ast.Pow,
    "<<": ast.LShift,
    ">>": ast.RShift,
    "&": ast.BitAnd,
    "|": ast.BitOr,
    "^": ast.BitXor,
}
_UNARY: Mapping[str, type[ast.unaryop]] = {
    "-": ast.USub,
    "+": ast.UAdd,
    "not": ast.Not,
    "~": ast.Invert,
}
_BOOL: Mapping[str, type[ast.boolop]] = {"and": ast.And, "or": ast.Or}
_CMP: Mapping[str, type[ast.cmpop]] = {
    "==": ast.Eq,
    "!=": ast.NotEq,
    "<": ast.Lt,
    "<=": ast.LtE,
    ">": ast.Gt,
    ">=": ast.GtE,
    "in": ast.In,
    "not in": ast.NotIn,
    "is": ast.Is,
    "is not": ast.IsNot,
}
_CONVERSIONS: Mapping[str, int] = {"": -1, "s": 115, "r": 114, "a": 97}
_GROW_METHODS: Mapping[str, str] = {"+": "add", "*": "mul", "**": "pow", "<<": "lshift"}
# Int operators whose result can leave the 64-bit range when the operands are in it
# (`%`, `>>`, `&`, `|`, `^` and `~` cannot).
_RANGED_BINARY = frozenset({"-", "//", *_GROW_METHODS})
# Int-typed calls whose result can leave the 64-bit range.
_RANGED_CALLS = frozenset(
    {
        "builtin:abs",
        "builtin:int",
        "builtin:round",
        "builtin:sum",
        "std:math.floor",
        "std:math.ceil",
        "std:math.trunc",
        "std:math.gcd",
    }
)


@dataclass(frozen=True)
class T2Code:
    """The compiled T2 part of a program: the module `source`, the procedures it
    defines with their parameter names in order, and the natives they call."""

    source: str
    procedures: tuple[tuple[str, tuple[str, ...]], ...]
    natives: tuple[str, ...]

    def params(self, name: str) -> tuple[str, ...]:
        return dict(self.procedures)[name]


def _is_int(t: T2Type) -> bool:
    return T.strip_opt(t) == T.INT


def _exact_key(obj: T2Type, key: T2Type) -> bool:
    """A dict (or Counter) key holding an Int where the keys hold a Float, at any depth:
    looked up exactly (R-widen); a defaultdict or Counter that stores it widens it."""
    match obj:
        case T.DictT(key=k) | T.CounterT(key=k):
            return _widened(key, k) != key
    return False


# ---- names ----------------------------------------------------------------------------------


def _names(node: object, out: set[str]) -> None:
    """Every name an IR subtree binds or reads."""
    match node:
        case n.Var(name=name) | n.NameT(name=name) | n.Walrus(name=name):
            out.add(name)
        case n.Lambda(params=params):
            out.update(params)
        case n.Handler(name=name) if name is not None:
            out.add(name)
    if isinstance(node, tuple):
        for x in node:
            _names(x, out)
    elif hasattr(node, "__dataclass_fields__"):
        for f in node.__dataclass_fields__:  # type: ignore[attr-defined]
            _names(getattr(node, f), out)


def _fresh_fuel_name(module: n.T2Module) -> str:
    """The fuel parameter's name: one no procedure uses."""
    used: set[str] = {r.name for r in module.records} | set(module.imports)
    used |= {name for name, _, _ in module.constants}
    for proc in module.procedures:
        used |= {p for p, _ in proc.params} | set(proc.locals) | {proc.name}
        _names(proc.body, used)
    name, k = _FUEL, 2
    # <fuel>_a1, <fuel>_int, ... are the module's own names: no name may start so
    while name in used or any(u.startswith(name + "_") for u in used):
        name, k = f"{_FUEL}{k}", k + 1
    return name


# ---- procedures -----------------------------------------------------------------------------


class _Emit:
    """One procedure's IR -> its Python function (an `ast.FunctionDef`)."""

    def __init__(self, module: n.T2Module, proc: n.Procedure | None, fuel: str) -> None:
        self.module = module
        self.proc = proc
        self.fuel = fuel
        self.temps = 0  # augmented assignments' temporaries: <fuel>_a1, <fuel>_a2, ...
        self.locals: Mapping[str, T2Type] = {} if proc is None else proc.locals

    # ---- helpers ----

    def _name(self, name: str) -> ast.Name:
        return ast.Name(name, ast.Load())

    def _fuel(self, method: str, *args: ast.expr) -> ast.Call:
        return ast.Call(ast.Attribute(self._name(self.fuel), method, ast.Load()), list(args), [])

    def _ranged(self, value: ast.expr) -> ast.Call:
        return ast.Call(self._name(f"{self.fuel}_int"), [value], [])

    def _temp(self) -> str:
        self.temps += 1
        return f"{self.fuel}_a{self.temps}"  # the fuel name is fresh, so are these

    def function(self) -> ast.FunctionDef:
        proc = self.proc
        assert proc is not None
        params = [ast.arg(self.fuel), *(ast.arg(p) for p, _ in proc.params)]
        guarded = ast.Try(
            body=self.block(proc.body),
            handlers=[],
            orelse=[],
            finalbody=[ast.Expr(self._fuel("leave"))],
        )
        return ast.FunctionDef(
            name=proc.name,
            args=ast.arguments(
                posonlyargs=[], args=params, kwonlyargs=[], kw_defaults=[], defaults=[]
            ),
            body=[ast.Expr(self._fuel("enter")), guarded],
            decorator_list=[],
            returns=None,
            type_comment=None,
            type_params=[],
        )

    # ---- statements ----

    def block(self, stmts: Iterable[n.Stmt]) -> list[ast.stmt]:
        out: list[ast.stmt] = []
        for s in stmts:
            out.extend(self.stmt(s))
        return out or [ast.Pass()]

    def stmt(self, s: n.Stmt) -> list[ast.stmt]:
        match s:
            case n.Tick():
                return [ast.Expr(self._fuel("tick"))]
            case n.Assign(target=t, value=v):
                return [ast.Assign([self.target(t)], self.expr(v))]
            case n.AugAssign():
                return self.aug_assign(s)
            case n.If(test=t, body=b, orelse=o):
                return [ast.If(self.expr(t), self.block(b), self.block(o) if o else [])]
            case n.For(target=t, iter=it, body=b, orelse=o):
                orelse = self.block(o) if o else []
                return [ast.For(self.target(t), self.expr(it), self.block(b), orelse)]
            case n.While(test=t, body=b, orelse=o):
                return [ast.While(self.expr(t), self.block(b), self.block(o) if o else [])]
            case n.Break():
                return [ast.Break()]
            case n.Continue():
                return [ast.Continue()]
            case n.Pass():
                return [ast.Pass()]
            case n.Return(value=v):
                return [ast.Return(None if v is None else self.expr(v))]
            case n.ExprStmt(value=v):
                return [ast.Expr(self.expr(v))]
            case n.Try(body=b, handlers=hs, orelse=o, final=f):
                handlers = [self.handler(h) for h in hs]
                return [
                    ast.Try(
                        body=self.block(b),
                        handlers=handlers,
                        orelse=self.block(o) if o else [],
                        finalbody=self.block(f) if f else [],
                    )
                ]
            case n.Raise(kind=kind, message=m):
                args = [] if m is None else [self.expr(m)]
                return [ast.Raise(ast.Call(self._name(kind), args, []), None)]
        raise LatticeInternalError(f"emit_py: unsupported statement {type(s).__name__}")

    def handler(self, h: n.Handler) -> ast.ExceptHandler:
        kinds = [self._name(k) for k in h.kinds]
        kind: ast.expr = kinds[0] if len(kinds) == 1 else ast.Tuple(kinds, ast.Load())
        return ast.ExceptHandler(kind, h.name, self.block(h.body))

    def _target_type(self, t: n.AssignTarget) -> T2Type | None:
        match t:
            case n.NameT(name=name):
                return self.locals.get(name)
            case n.IndexT(obj=obj):
                match T.strip_opt(obj.type):
                    case T.ListT(elem=e):
                        return e
                    case T.DictT(val=v):
                        return v
                    case T.CounterT():
                        return T.INT
        return None

    def aug_assign(self, s: n.AugAssign) -> list[ast.stmt]:
        """`t op= v`. A growing op (`+ * ** <<`) binds the target's container and index
        and the value to temporaries (each evaluated once, in that order), checks the
        size (`<fuel>.aug(op, current, value)`), then assigns in place (`xs += ys`
        stays in place). An Int target is assigned `<fuel>_int(current op value)`."""
        op = _BINOPS[s.op]()
        ttype = self._target_type(s.target)
        ranged = ttype is not None and _is_int(ttype) and s.op in _RANGED_BINARY
        t = s.target
        exact = isinstance(t, n.IndexT) and _exact_key(t.obj.type, t.index.type)
        if not s.grow and not ranged and not exact:
            return [ast.AugAssign(self.target(s.target), op, self.expr(s.value))]
        stmts: list[ast.stmt] = []

        def bind(value: ast.expr) -> ast.Name:
            tmp = self._temp()
            stmts.append(ast.Assign([ast.Name(tmp, ast.Store())], value))
            return self._name(tmp)

        target = self.target(s.target)
        if isinstance(target, ast.Subscript):
            container = bind(target.value)
            index = target.slice
            key: ast.expr
            if isinstance(index, ast.Slice):  # xs[a:b] += ...: each bound once
                parts = (index.lower, index.upper, index.step)
                key = ast.Slice(*(None if p is None else bind(p) for p in parts))
            elif exact:  # the key read and written: exact, or widened where a read stores
                assert isinstance(t, n.IndexT) and isinstance(t.obj.type, T.DictT | T.CounterT)
                how = ast.Constant(_widen_how(t.index.type, t.obj.type.key))
                fkey = self._name(f"{self.fuel}_fkey")
                key = bind(ast.Call(fkey, [container, index, how], []))
            else:
                key = bind(index)
            target = ast.Subscript(container, key, ast.Store())
        value: ast.expr = bind(self.expr(s.value)) if s.grow else self.expr(s.value)
        current = _load(target)
        if s.grow:
            name = _GROW_METHODS[s.op]
            stmts.append(ast.Expr(self._fuel("aug", ast.Constant(name), current, value)))
        if ranged:
            stmts.append(ast.Assign([target], self._ranged(ast.BinOp(_load(target), op, value))))
        else:
            stmts.append(ast.AugAssign(target, op, value))
        return stmts

    def target(self, t: n.AssignTarget) -> ast.expr:
        match t:
            case n.NameT(name=name):
                return ast.Name(name, ast.Store())
            case n.TupleTarget(elts=elts):
                return ast.Tuple([self.target(e) for e in elts], ast.Store())
            case n.IndexT(obj=obj, index=index):
                return ast.Subscript(self.expr(obj), self.expr(index), ast.Store())
            case n.AttrT(obj=obj, name=name):
                return ast.Attribute(self.expr(obj), name, ast.Store())
            case n.SliceT(obj=obj, lo=lo, hi=hi, step=step):
                sl = ast.Slice(self._opt(lo), self._opt(hi), self._opt(step))
                return ast.Subscript(self.expr(obj), sl, ast.Store())
        raise LatticeInternalError(f"emit_py: unsupported target {type(t).__name__}")

    # ---- expressions ----

    def _opt(self, e: n.Expr | None) -> ast.expr | None:
        return None if e is None else self.expr(e)

    def expr(self, e: n.Expr) -> ast.expr:  # noqa: C901, PLR0911, PLR0912 - one case per node
        match e:
            case n.Const(value=value):
                return ast.Constant(value)
            case n.Var(name=name):
                return self._name(name)
            case n.Attr(obj=obj, name=name):
                return ast.Attribute(self.expr(obj), name, ast.Load())
            case n.Index(obj=obj, index=index):
                if isinstance(obj.type, T.DictT) and _exact_key(obj.type, index.type):
                    # an exact lookup; a defaultdict stores the key widened (R-widen)
                    fget = self._name(f"{self.fuel}_fget")
                    how = ast.Constant(_widen_how(index.type, obj.type.key))
                    return ast.Call(fget, [self.expr(obj), self.expr(index), how], [])
                return ast.Subscript(self.expr(obj), self.expr(index), ast.Load())
            case n.SliceOf(obj=obj, lo=lo, hi=hi, step=step):
                sl = ast.Slice(self._opt(lo), self._opt(hi), self._opt(step))
                return ast.Subscript(self.expr(obj), sl, ast.Load())
            case n.Unary(op=op, operand=operand, type=t):
                if op == "-" and isinstance(operand, n.Const) and type(operand.value) is int:
                    return ast.UnaryOp(ast.USub(), ast.Constant(operand.value))  # a literal
                node = ast.UnaryOp(_UNARY[op](), self.expr(operand))
                return self._ranged(node) if op == "-" and _is_int(t) else node
            case n.Binary(op=op, left=left, right=right, type=t):
                node = ast.BinOp(self.expr(left), _BINOPS[op](), self.expr(right))
                return self._ranged(node) if op in _RANGED_BINARY and _is_int(t) else node
            case n.Grow(op=op, left=left, right=right, type=t):
                call = self._fuel(_GROW_METHODS[op], self.expr(left), self.expr(right))
                return self._ranged(call) if _is_int(t) else call
            case n.Compare(ops=ops, operands=operands):
                first, *rest = (self.expr(x) for x in operands)
                return ast.Compare(first, [_CMP[o]() for o in ops], rest)
            case n.BoolOp(op=op, values=values):
                return ast.BoolOp(_BOOL[op](), [self.expr(v) for v in values])
            case n.IfExp(test=test, body=body, orelse=orelse):
                return ast.IfExp(self.expr(test), self.expr(body), self.expr(orelse))
            case n.Call():
                return self.call(e, charged=False)
            case n.Charged(call=call, type=t):
                node = self.call(call, charged=True)
                return self._ranged(node) if call.target in _RANGED_CALLS and _is_int(t) else node
            case n.ListLit(elts=elts):
                return ast.List([self.expr(x) for x in elts], ast.Load())
            case n.TupleLit(elts=elts):
                return ast.Tuple([self.expr(x) for x in elts], ast.Load())
            case n.SetLit(elts=elts):
                items = ast.Tuple([self.expr(x) for x in elts], ast.Load())
                return ast.Call(self._name(f"{self.fuel}_set"), [items], [])
            case n.DictLit(keys=keys, values=values):
                ks = [None if k is None else self.expr(k) for k in keys]
                return ast.Dict(ks, [self.expr(v) for v in values])
            case n.Comp():
                return self.comp(e)
            case n.FStr():
                return self.fstr(e)
            case n.Lambda(params=params, body=body):
                args = ast.arguments(
                    posonlyargs=[],
                    args=[ast.arg(p) for p in params],
                    kwonlyargs=[],
                    kw_defaults=[],
                    defaults=[],
                )
                return ast.Lambda(args, self.expr(body))
            case n.Walrus(name=name, value=value):
                return ast.NamedExpr(ast.Name(name, ast.Store()), self.expr(value))
            case n.Each(iter=it):
                return self._fuel("each", self.expr(it))
            case n.Bulk(value=v):
                return self._fuel("bulk", self.expr(v))
            case n.Scan(value=v):
                return self._fuel("scan", self.expr(v))
            case n.Sized():
                return self.sized(e)
            case n.Widen(value=v, type=t):
                return _widen(self.fuel, self.expr(v), v.type, t)
        raise LatticeInternalError(f"emit_py: unsupported expression {type(e).__name__}")

    def comp(self, e: n.Comp) -> ast.expr:
        gens = [
            ast.comprehension(
                self.target(g.target), self.expr(g.iter), [self.expr(c) for c in g.ifs], 0
            )
            for g in e.gens
        ]
        elt = self.expr(e.elt)
        match e.kind:
            case "list":
                return ast.ListComp(elt, gens)
            case "gen":
                return ast.GeneratorExp(elt, gens)
            case "set":
                return ast.Call(self._name(f"{self.fuel}_set"), [ast.GeneratorExp(elt, gens)], [])
            case "dict":
                assert e.value is not None
                return ast.DictComp(elt, self.expr(e.value), gens)
        raise LatticeInternalError(f"emit_py: unsupported comprehension {e.kind}")

    def fstr(self, e: n.FStr) -> ast.JoinedStr:
        values: list[ast.expr] = []
        for part in e.parts:
            if isinstance(part, str):
                values.append(ast.Constant(part))
                continue
            conv = _CONVERSIONS[part.conversion]
            value = self.expr(part.value)
            if part.spec == "":
                values.append(ast.FormattedValue(value, conv, None))
                continue
            spec = (
                self.fstr(part.spec) if isinstance(part.spec, n.FStr) else ast.Constant(part.spec)
            )
            call = self._fuel("fmt", value, spec, ast.Constant(conv))
            values.append(ast.FormattedValue(call, -1, None))
        return ast.JoinedStr(values)

    def callee(self, call: n.Call) -> tuple[ast.expr, list[ast.expr]]:
        """The called expression and the positional arguments (a method's receiver
        becomes the callee's object)."""
        kind, _, name = call.target.partition(":")
        args = [self.expr(a) for a in call.args]
        match kind:
            case "method":
                method = name.rsplit(".", 1)[-1]
                return ast.Attribute(args[0], method, ast.Load()), args[1:]
            case "native":
                return ast.Attribute(self._name("native"), name, ast.Load()), args
            case "std":
                return self._std(name), args
            case "proc":
                return self._name(name), [self._name(self.fuel), *args]
            case "builtin" | "helper" | "record":
                return self._name(name), args
        raise LatticeInternalError(f"emit_py: unknown call target {call.target!r}")

    def _std(self, qual: str) -> ast.expr:
        """`module.name` as the program imported it: the imported name, else the
        imported module's attribute (both bind the same object)."""
        mod, _, attr = qual.partition(".")
        imports = self.module.imports
        for local, q in imports.items():
            if q == qual:
                return self._name(local)
        for local, q in imports.items():
            if q == mod:
                return ast.Attribute(self._name(local), attr, ast.Load())
        raise LatticeInternalError(f"emit_py: {qual} is not imported")

    def call(self, call: n.Call, charged: bool) -> ast.Call:
        func, args = self.callee(call)
        kwargs = [ast.keyword(k, self.expr(v)) for k, v in call.kwargs]
        if charged:
            func = self._fuel("call", func)
        return ast.Call(func, args, kwargs)

    def sized(self, e: n.Sized) -> ast.expr:
        if e.kind == "pad":
            (width,) = e.args
            return self._fuel("width", self.expr(width))
        (call,) = e.args
        assert isinstance(call, n.Call)
        kwargs = [ast.keyword(k, self.expr(v)) for k, v in call.kwargs]
        method = self._fuel("call", ast.Attribute(self._name(self.fuel), e.kind, ast.Load()))
        if call.target.startswith("method:"):  # replace / join: the receiver first
            args = [self.expr(a) for a in call.args]
        else:  # re.sub / json.dumps: the function first
            func, args = self.callee(call)
            args = [func, *args]
        return ast.Call(method, args, kwargs)


def _widen_how(src: T2Type, dst: T2Type) -> object:
    """`t2_widen`'s description of converting a value of `src` to `dst` (R-widen)."""
    if src == T.INT and dst == T.FLOAT:
        return "f"
    match src, dst:
        case T.Opt(inner=a), T.Opt(inner=b):
            return ("?", _widen_how(a, b))
        case _, T.Opt(inner=b) if not isinstance(src, T.Opt):
            return _widen_how(src, b)
        case T.TupleT(elems=xs), T.TupleT(elems=ys) if len(xs) == len(ys):
            pairs = zip(xs, ys, strict=True)
            return ("t", tuple(None if a == b else _widen_how(a, b) for a, b in pairs))
        case T.IterT(elem=a), T.IterT(elem=b):
            return ("i", _widen_how(a, b))
        case T.DictT(key=k1, val=v1), T.DictT(key=k2, val=v2):
            hk = None if k1 == k2 else _widen_how(k1, k2)
            return ("d", hk, None if v1 == v2 else _widen_how(v1, v2))
        case T.ListT() | T.SetT() | T.DictT() | T.CounterT(), T.ListT(elem=b):
            a = T.elem_of(src)
            if a is not None:
                return ("l", _widen_how(a, b))
    raise LatticeInternalError(f"emit_py: cannot widen {src.render()} to {dst.render()}")


def _widen(fuel: str, value: ast.expr, src: T2Type, dst: T2Type) -> ast.expr:
    if src == T.INT and dst == T.FLOAT:
        return ast.Call(ast.Name(f"{fuel}_float", ast.Load()), [value], [])
    how = ast.Constant(_widen_how(src, dst))
    return ast.Call(ast.Name(f"{fuel}_widen", ast.Load()), [value, how], [])


def _load(target: ast.expr) -> ast.expr:
    """A store target (a name or a subscript of temporaries) read back."""
    match target:
        case ast.Name(id=name):
            return ast.Name(name, ast.Load())
        case ast.Subscript(value=value, slice=sl):
            return ast.Subscript(value, sl, ast.Load())
    raise LatticeInternalError(f"emit_py: cannot read back {ast.dump(target)}")


# ---- the module -----------------------------------------------------------------------------


def emit_python(module: n.T2Module) -> T2Code:
    """The Python source of every procedure and record of `module`."""
    fuel = _fresh_fuel_name(module)
    lines: list[str] = ["# preamble: the names procedures may reach"]
    for name in T2_BUILTIN_NAMES:
        lines.append(f"{name} = t2_builtin({name!r})")
    for unit in _HELPERS:
        lines.append(f"{unit} = t2_helper({unit!r})")
    for local, qual in module.imports.items():
        if "." in qual:
            mod, attr = qual.split(".", 1)
            lines.append(f"{local} = t2_std({mod!r}, {attr!r})")
        else:
            names = tuple(sorted(T2_STDLIB[qual]))
            lines.append(f"{local} = t2_std_module({qual!r}, {names!r})")
    lines.append(f"{fuel}_int = t2_int")
    lines.append(f"{fuel}_set = t2_builtin('set')")
    lines.append(f"{fuel}_float = t2_builtin('float')")
    lines.append(f"{fuel}_widen = t2_widen")
    lines.append(f"{fuel}_fget = t2_fget")
    lines.append(f"{fuel}_fkey = t2_fkey")
    for name, _, value in module.constants:
        # copied as written: a constant is evaluated once, uncharged, when loaded
        lines.append(f"{name} = {ast.unparse(_plain(value, fuel))}")
    for rec in module.records:
        fields = tuple(f for f, _ in rec.fields)
        lines.append(f"{rec.name} = t2_record({rec.name!r}, {fields!r}, {dict(rec.defaults)!r})")
    procs: list[tuple[str, tuple[str, ...]]] = []
    for proc in module.procedures:
        fn = ast.fix_missing_locations(_Emit(module, proc, fuel).function())
        lines += ["", "", ast.unparse(fn)]
        procs.append((proc.name, tuple(p for p, _ in proc.params)))
    source = "\n".join(lines) + "\n"
    compile(source, "<lattice-procedures>", "exec")  # a compile error is a Lattice bug
    return T2Code(source, tuple(procs), module.natives)


def _plain(e: n.Expr, fuel: str) -> ast.expr:
    """A module constant's value (a constant expression or a list of literals) as
    Python, without fuel or range checks (the preamble copies it as written; a widened
    Int is converted, R-widen)."""
    match e:
        case n.Const(value=value):
            return ast.Constant(value)
        case n.Var(name=name):
            return ast.Name(name, ast.Load())
        case n.ListLit(elts=elts):
            return ast.List([_plain(x, fuel) for x in elts], ast.Load())
        case n.Unary(op=op, operand=operand):
            return ast.UnaryOp(_UNARY[op](), _plain(operand, fuel))
        case n.Binary(op=op, left=left, right=right):
            return ast.BinOp(_plain(left, fuel), _BINOPS[op](), _plain(right, fuel))
        case n.Compare(ops=ops, operands=operands):
            first, *rest = (_plain(x, fuel) for x in operands)
            return ast.Compare(first, [_CMP[o]() for o in ops], rest)
        case n.BoolOp(op=op, values=values):
            return ast.BoolOp(_BOOL[op](), [_plain(v, fuel) for v in values])
        case n.IfExp(test=test, body=body, orelse=orelse):
            return ast.IfExp(_plain(test, fuel), _plain(body, fuel), _plain(orelse, fuel))
        case n.Widen(value=v, type=t):
            return _widen(fuel, _plain(v, fuel), v.type, t)
    raise LatticeInternalError(f"emit_py: unsupported constant {type(e).__name__}")
