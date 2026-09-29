"""T2 IR -> Go (plan 5a Task 8): the core statements, expressions and builtins.

`emit_go_t2(module, package)` writes one Go file from the IR of a checked program's
procedures (`lattice.t2ir.lower.lower_t2`). It calls the Go runtime `lattice.dev/rt`
(lattice/gogen/rt) and must give Python's results exactly -- the same values, the same
errors, and the same fuel charged at the same points. It mirrors `lattice.t2.emit_py`
node by node:

* each procedure is `func P_<name>(fuel *rt.Fuel, <params>) <result>`, starting with
  `fuel.Enter(); defer fuel.Leave()`; a `proc:` call passes the fuel on;
* fuel is charged where the IR says: `Tick` is `fuel.Tick()`; `Charged(call)` is
  `rt.After(fuel.Step(), call)` (the step before the arguments); `Each(it)` charges per
  element as it is produced (`rt.Each`), `Bulk(v)` a sized value's length up front and a
  one-shot iterator per element (`rt.BulkIter`); `Grow` and a growing `AugAssign` check
  sizes as `T2Fuel.add/mul/pow/lshift/aug` (`rt.Grow*`, `rt.Size*`, `rt.Bits*`); a
  replacement field with a spec charges its numbers (`rt.Fmt*`);
* Int arithmetic is range-checked (R623) by the runtime (`rt.Add`, `rt.Sub`, ...); a
  `sum` of Ints only on its result (`rt.SumInt`);
* iteration follows CPython's iterators: a list is iterated live, a dict or set raises
  RuntimeError when it changes size while iterated, `range` is re-iterable while
  `enumerate`/`zip`/`reversed`/a generator expression are one-shot; a generator
  expression is lazy (`rt.FlatMap`), a list/set/dict comprehension a loop;
* evaluation order is Python's: an assignment evaluates its value before the target's
  container and index; a growing augmented assignment evaluates the container, the
  index, the value, then reads the current value;
* `try`/`except`/`else` becomes `rt.Try` with closures: a `break`, `continue` or
  `return` inside them sets a control code the code after `rt.Try` acts on.

Values: see `lattice.gogen.types`. Constructs Go lacks are rejected before this runs
(`lattice.check.p8_target`, L892); reaching one here is an internal error.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

from lattice.check.t2 import types as T
from lattice.check.t2.types import T2Type
from lattice.gogen.types import GoTypes
from lattice.runtime import LatticeInternalError
from lattice.t2ir import nodes as n

__all__ = ["emit_go_t2", "go_float", "go_string"]

# Go keywords, predeclared identifiers and the names generated code uses unqualified:
# a Lattice name equal to one of these is renamed.
_GO_RESERVED = frozenset(
    """break default func interface select case defer go map struct chan else goto package
    switch const fallthrough if range type continue for import return var bool byte
    complex64 complex128 error float32 float64 int int8 int16 int32 int64 rune string uint
    uint8 uint16 uint32 uint64 uintptr true false iota nil any comparable append cap clear
    close complex copy delete imag len make max min new panic print println real recover
    fuel rt math strings _""".split()
)
# Prefixes of package-level generated names (procedures, constants, records, tuples,
# helpers): a local starting with one is renamed, so it cannot shadow them.
_PACKAGE_PREFIXES = ("P_", "C_", "rec_", "tup_", "eq_", "lt_", "le_", "gt_", "ge_")
_INT_OPS: Mapping[str, str] = {"-": "rt.Sub", "//": "rt.FloorDiv", "%": "rt.Mod", ">>": "rt.Shr"}
_BIT_OPS = frozenset({"&", "|", "^"})
_ORDER = frozenset({"<", "<=", ">", ">="})


def go_string(s: str) -> str:
    """A Go string literal (ASCII source: other characters as \\u / \\U escapes)."""
    out = ['"']
    for ch in s:
        c = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif 0x20 <= c < 0x7F:
            out.append(ch)
        elif c < 0x80:
            out.append(f"\\x{c:02x}")
        elif 0xD800 <= c <= 0xDFFF:
            raise LatticeInternalError("gogen: a lone surrogate cannot be a Go string")
        elif c <= 0xFFFF:
            out.append(f"\\u{c:04x}")
        else:
            out.append(f"\\U{c:08x}")
    out.append('"')
    return "".join(out)


def go_float(f: float) -> str:
    """A Go float64 expression with exactly the value f (inf, nan, -0.0 included)."""
    if math.isnan(f):
        return "math.NaN()"
    if math.isinf(f):
        return "math.Inf(1)" if f > 0 else "math.Inf(-1)"
    if f == 0.0 and math.copysign(1.0, f) < 0:
        return "math.Copysign(0, -1)"
    bits = struct.unpack("<Q", struct.pack("<d", f))[0]
    return f"math.Float64frombits(0x{bits:016x})"


def _python_names(node: object, out: set[str]) -> None:
    match node:
        case n.Var(name=name) | n.NameT(name=name) | n.Walrus(name=name):
            out.add(name)
        case n.Lambda(params=params):
            out.update(params)
        case n.Handler(name=name) if name is not None:
            out.add(name)
    if isinstance(node, tuple):
        for x in node:
            _python_names(x, out)
    elif hasattr(node, "__dataclass_fields__"):
        for f in node.__dataclass_fields__:  # type: ignore[attr-defined]
            _python_names(getattr(node, f), out)


def _is_simple(go: str) -> bool:
    """A Go expression without calls: evaluating it has no effect, so its evaluation
    order against other operands does not matter."""
    return "(" not in go


@dataclass
class _Frame:
    """A Go function body the statements are emitted into: the procedure itself, or a
    closure passed to rt.Try (whose break/continue/return set `ctl`)."""

    closure: bool
    ctl: str = ""
    loops: int = 0
    used: set[int] = field(default_factory=set)  # 1 break, 2 continue, 3 return


class _Module:
    """What every procedure's emitter shares: the types and the generated names."""

    def __init__(self, module: n.T2Module) -> None:
        self.module = module
        self.types = GoTypes((r.name, r.fields) for r in module.records)
        self.procs = {p.name: p for p in module.procedures}
        self.records = {r.name: r for r in module.records}
        self.constants = {name: t for name, t, _ in module.constants}
        self.imports: set[str] = {"lattice.dev/rt"}
        names: set[str] = set()
        for p in module.procedures:
            names |= {x for x, _ in p.params} | set(p.locals)
            _python_names(p.body, names)
        prefix, k = "t_", 2
        while any(x.startswith(prefix) for x in names):
            prefix, k = f"t{k}_", k + 1
        self.temp_prefix = prefix
        self.names = names

    def local_names(self, proc: n.Procedure) -> dict[str, str]:
        names: set[str] = {x for x, _ in proc.params} | set(proc.locals)
        _python_names(proc.body, names)
        taken = set(self.names)
        out: dict[str, str] = {}
        for name in sorted(names):
            if name in _GO_RESERVED or name.startswith(_PACKAGE_PREFIXES):
                k = 1
                while f"x_{name}_{k}" in taken:
                    k += 1
                out[name] = f"x_{name}_{k}"
                taken.add(out[name])
            else:
                out[name] = name
        return out


class _Proc:
    """One procedure's IR -> its Go function."""

    def __init__(self, mod: _Module, proc: n.Procedure | None) -> None:
        self.m = mod
        self.types = mod.types
        self.proc = proc
        self.lines: list[str] = []
        self.depth = 1
        self.temps = 0
        self.scopes: list[dict[str, T2Type]] = []
        self.frames: list[_Frame] = [_Frame(closure=False)]
        self.ret_used = False
        self.gonames: dict[str, str] = {} if proc is None else mod.local_names(proc)

    # ---- helpers -------------------------------------------------------------------------

    def spell(self, t: T2Type) -> str:
        return self.types.spell(t)

    def temp(self) -> str:
        self.temps += 1
        return f"{self.m.temp_prefix}{self.temps}"

    def emit(self, line: str) -> None:
        self.lines.append("\t" * self.depth + line)

    @contextmanager
    def indented(self) -> Iterator[None]:
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1

    @contextmanager
    def capture(self) -> Iterator[list[str]]:
        saved = self.lines
        self.lines = []
        try:
            yield self.lines
        finally:
            self.lines = saved

    def goname(self, name: str) -> str:
        return self.gonames.get(name, name)

    def declared(self, name: str) -> tuple[str, T2Type]:
        """The Go name and declared type of a Lattice name in scope."""
        for scope in reversed(self.scopes):
            if name in scope:
                return self.goname(name), scope[name]
        if self.proc is not None and name in self.proc.locals:
            return self.goname(name), self.proc.locals[name]
        if name in self.m.constants:
            return f"C_{name}", self.m.constants[name]
        raise LatticeInternalError(f"gogen: unknown name {name!r}")

    # ---- the function ----------------------------------------------------------------------

    def function(self) -> str:
        proc = self.proc
        assert proc is not None
        params = ", ".join(
            ["fuel *rt.Fuel", *(f"{self.goname(p)} {self.spell(t)}" for p, t in proc.params)]
        )
        result = " " + self.spell(proc.returns)  # a None procedure returns struct{}
        with self.capture() as body:
            self.block(proc.body)
        head = [
            f"func P_{proc.name}({params}){result} {{",
            "\tfuel.Enter()",
            "\tdefer fuel.Leave()",
        ]
        param_names = {p for p, _ in proc.params}
        for name, t in sorted(proc.locals.items()):
            if name in param_names:
                continue
            go = self.goname(name)
            head.append(f"\tvar {go} {self.spell(t)}")
            head.append(f"\t_ = {go}")
        if self.ret_used:
            head.append(f"\tvar {self.m.temp_prefix}ret {self.spell(proc.returns)}")
        tail: list[str] = []
        if isinstance(proc.returns, T.Opt):
            tail.append(f"\treturn rt.None[{self.spell(proc.returns.inner)}]()")
        elif proc.returns == T.NONE:
            tail.append("\treturn struct{}{}")
        else:
            tail.append('\tpanic("lattice: a procedure ended without returning")')
        return "\n".join([*head, *body, *tail, "}"]) + "\n"

    # ---- statements ------------------------------------------------------------------------

    def block(self, stmts: tuple[n.Stmt, ...]) -> None:
        for s in stmts:
            self.stmt(s)

    def stmt(self, s: n.Stmt) -> None:  # noqa: C901, PLR0912 - one case per statement
        match s:
            case n.Tick():
                self.emit("fuel.Tick()")
            case n.Pass():
                pass
            case n.Assign(target=t, value=v):
                self.assign(t, v)
            case n.AugAssign():
                self.aug_assign(s)
            case n.If(test=test, body=body, orelse=orelse):
                self.emit(f"if {self.truth(self.expr(test), test.type)} {{")
                with self.indented():
                    self.block(body)
                if orelse:
                    self.emit("} else {")
                    with self.indented():
                        self.block(orelse)
                self.emit("}")
            case n.For(target=target, iter=it, body=body, orelse=orelse):
                if orelse:
                    raise LatticeInternalError("gogen: for-else")
                self.for_loop(target, it, body)
            case n.While(test=test, body=body, orelse=orelse):
                if orelse:
                    raise LatticeInternalError("gogen: while-else")
                self.emit(f"for {self.truth(self.expr(test), test.type)} {{")
                self.loop_body(body)
                self.emit("}")
            case n.Break():
                self.jump(1)
            case n.Continue():
                self.jump(2)
            case n.Return(value=v):
                self.ret(v)
            case n.ExprStmt(value=v):
                self.emit(f"_ = {self.expr(v)}")
            case n.Try():
                self.try_stmt(s)
            case n.Raise(kind=kind, message=m):
                msg = '""' if m is None else self.str_of(self.expr(m), m.type)
                self.emit(f"rt.Raise({go_string(kind)}, {msg})")
            case _:
                raise LatticeInternalError(f"gogen: unsupported statement {type(s).__name__}")

    def loop_body(self, body: tuple[n.Stmt, ...]) -> None:
        frame = self.frames[-1]
        frame.loops += 1
        with self.indented():
            self.block(body)
        frame.loops -= 1

    def for_loop(self, target: n.AssignTarget, it: n.Expr, body: tuple[n.Stmt, ...]) -> None:
        nxt, x, ok = self.temp(), self.temp(), self.temp()
        elem = self.elem(it.type)
        self.emit(f"for {nxt} := {self.cursor_of(it)}; ; {{")
        with self.indented():
            self.emit(f"{x}, {ok} := {nxt}()")
            self.emit(f"if !{ok} {{")
            self.emit("\tbreak")
            self.emit("}")
            self.bind(target, x, elem, declare=False)
        self.loop_body(body)
        self.emit("}")

    def jump(self, code: int) -> None:
        frame = self.frames[-1]
        if frame.loops > 0:
            self.emit("break" if code == 1 else "continue")
            return
        if not frame.closure:
            raise LatticeInternalError("gogen: break/continue outside a loop")
        frame.used.add(code)
        self.emit(f"{frame.ctl} = {code}")
        self.emit("return")

    def ret(self, v: n.Expr | None) -> None:
        proc = self.proc
        assert proc is not None
        frame = self.frames[-1]
        if v is None:
            v = n.Const(None, T.NONE)  # a bare return is `return None`
        value = self.conv(self.expr(v), v.type, proc.returns)
        if not frame.closure:
            self.emit(f"return {value}")
            return
        self.ret_used = True
        self.emit(f"{self.m.temp_prefix}ret = {value}")
        frame.used.add(3)
        self.emit(f"{frame.ctl} = 3")
        self.emit("return")

    def dispatch(self, used: set[int], ctl: str) -> None:
        """After rt.Try: act on the control code its closures set."""
        for code in sorted(used):
            self.emit(f"if {ctl} == {code} {{")
            with self.indented():
                if code == 3:
                    self.ret_code()
                else:
                    self.jump(code)
            self.emit("}")

    def ret_code(self) -> None:
        """`return` of the value a closure stored (t_ret)."""
        proc = self.proc
        assert proc is not None
        frame = self.frames[-1]
        if not frame.closure:
            self.emit(f"return {self.m.temp_prefix}ret")
            return
        frame.used.add(3)
        self.emit(f"{frame.ctl} = 3")
        self.emit("return")

    def closure(self, ctl: str, stmts: tuple[n.Stmt, ...], frame: _Frame) -> list[str]:
        self.frames.append(frame)
        with self.capture() as lines, self.indented():
            self.block(stmts)
        self.frames.pop()
        return lines

    def try_stmt(self, s: n.Try) -> None:
        if s.final:
            raise LatticeInternalError("gogen: finally")
        ctl, done = self.temp(), self.temp()
        frame = _Frame(closure=True, ctl=ctl)
        handlers: list[tuple[n.Handler, str, list[str]]] = []
        with self.indented():  # the closures sit inside the block and rt.Try(...)
            body = self.closure(ctl, s.body, frame)
            for h in s.handlers:
                e = self.temp()
                self.scopes.append({h.name: T.ERROR} if h.name else {})
                with self.capture() as hl, self.indented():
                    if h.name:
                        go = self.goname(h.name)
                        self.emit(f"{go} := {e}")
                        self.emit(f"_ = {go}")
                hl += self.closure(ctl, h.body, frame)
                self.scopes.pop()
                handlers.append((h, e, hl))
        self.emit("{")
        with self.indented():
            if frame.used:
                self.emit(f"{ctl} := 0")
            if s.orelse:
                self.emit(f"{done} := false")
            self.emit("rt.Try(func() {")
            self.lines.extend(body)
            if s.orelse:
                self.emit(f"\t{done} = true")
            self.emit("}" + ("," if handlers else ")"))
            for i, (h, e, hl) in enumerate(handlers):
                kinds = ", ".join(go_string(k) for k in h.kinds)
                self.emit(f"\trt.Handler{{Kinds: []string{{{kinds}}}, Fn: func({e} *rt.Error) {{")
                self.lines.extend("\t" + line for line in hl)
                self.emit("\t}}" + ("," if i < len(handlers) - 1 else ")"))
            if s.orelse:
                self.emit(f"if {done} {{")
                with self.indented():
                    self.block(s.orelse)
                self.emit("}")
            self.dispatch(frame.used, ctl)
        self.emit("}")

    # ---- assignment --------------------------------------------------------------------------

    def assign(self, target: n.AssignTarget, value: n.Expr) -> None:
        go = self.expr(value)
        if isinstance(target, n.NameT):
            name, dt = self.declared(target.name)
            self.emit(f"{name} = {self.conv(go, value.type, dt)}")
            return
        tmp = self.temp()  # the value first, then the target's container and index
        self.emit(f"{tmp} := {go}")
        self.bind(target, tmp, value.type, declare=False)

    def bind(self, target: n.AssignTarget, go: str, t: T2Type, *, declare: bool) -> None:
        """Assign the (evaluated, plain) Go value `go` of type t to a target; `declare`
        declares new names (a comprehension's own variables)."""
        match target:
            case n.NameT(name=name):
                if declare:
                    self.scopes[-1][name] = t
                    gn = self.goname(name)
                    self.emit(f"{gn} := {go}")
                    self.emit(f"_ = {gn}")
                else:
                    gn, dt = self.declared(name)
                    self.emit(f"{gn} = {self.conv(go, t, dt)}")
            case n.TupleTarget(elts=elts):
                parts = self.unpack(go, t, len(elts))
                for elt, (pgo, pt) in zip(elts, parts, strict=True):
                    self.bind(elt, pgo, pt, declare=declare)
            case n.IndexT(obj=obj, index=index):
                ot = T.strip_opt(obj.type)
                o = self.expr(obj)
                k = self.expr(index)
                match ot:
                    case T.ListT(elem=e):
                        self.emit(f"{o}.Set({k}, {self.conv(go, t, e)})")
                    case T.DictT(key=kt, val=vt):
                        self.emit(
                            f"{o}.Set({self.conv(k, index.type, kt)}, {self.conv(go, t, vt)})"
                        )
                    case _:
                        raise LatticeInternalError(f"gogen: item assignment on {ot.render()}")
            case _:
                raise LatticeInternalError(f"gogen: unsupported target {type(target).__name__}")

    def unpack(self, go: str, t: T2Type, count: int) -> list[tuple[str, T2Type]]:
        """The parts of `a, b, ... = value` (the value evaluated once, into a temp)."""
        tmp = self.temp()
        match t:
            case T.TupleT(elems=es):
                if len(es) != count:
                    raise LatticeInternalError("gogen: tuple arity")
                self.emit(f"{tmp} := {go}")
                return [(f"{tmp}.F{i}", e) for i, e in enumerate(es)]
            case T.ListT(elem=e):
                self.emit(f"{tmp} := rt.Unpack({go}.Items, {count})")
            case _ if t == T.STR:
                e = T.STR
                self.emit(f"{tmp} := rt.UnpackIter(rt.Runes({go}), {count})")
            case T.IterT(elem=e):
                self.emit(f"{tmp} := rt.UnpackIter(rt.Collect({go}.Cursor()).Items, {count})")
            case _:
                raise LatticeInternalError(f"gogen: cannot unpack {t.render()}")
        return [(f"{tmp}[{i}]", e) for i in range(count)]

    def aug_assign(self, s: n.AugAssign) -> None:
        """`t op= v` in emit_py's order: a growing op evaluates the target's container and
        index, then the value, then checks the size and reads the current value; any
        other op reads the current value before evaluating the value."""
        match s.target:
            case n.NameT(name=name):
                gn, tt = self.declared(name)
                if s.grow:
                    v = self.temp()
                    self.emit(f"{v} := {self.expr(s.value)}")
                    self.aug_op(gn, tt, s.op, v, s.value.type, store=lambda r: f"{gn} = {r}")
                else:
                    value = self.expr(s.value)
                    self.aug_op(gn, tt, s.op, value, s.value.type, store=lambda r: f"{gn} = {r}")
            case n.IndexT(obj=obj, index=index):
                ot = T.strip_opt(obj.type)
                match ot:
                    case T.ListT(elem=et):
                        kt = T.INT
                    case T.DictT(key=kt, val=et):
                        pass
                    case _:
                        raise LatticeInternalError(f"gogen: item assignment on {ot.render()}")
                o, k, cur = self.temp(), self.temp(), self.temp()
                self.emit("{")
                with self.indented():
                    self.emit(f"{o} := {self.expr(obj)}")
                    self.emit(f"{k} := {self.conv(self.expr(index), index.type, kt)}")
                    if s.grow:
                        v = self.temp()
                        self.emit(f"{v} := {self.expr(s.value)}")
                        self.emit(f"{cur} := {o}.Get({k})")
                    else:
                        self.emit(f"{cur} := {o}.Get({k})")
                        v = self.expr(s.value)
                    self.aug_op(
                        cur, et, s.op, v, s.value.type, store=lambda r: f"{o}.Set({k}, {r})"
                    )
                self.emit("}")
            case _:
                raise LatticeInternalError(
                    f"gogen: augmented assignment to {type(s.target).__name__}"
                )

    def aug_op(self, cur: str, t: T2Type, op: str, v: str, vt: T2Type, store) -> None:  # noqa: ANN001
        """cur op= v for a current value of type t (cur is a plain name)."""
        if isinstance(t, T.ListT):
            if op == "+":
                if isinstance(vt, T.ListT):
                    self.emit(f"rt.SizeAdd(fuel, {cur}.Len(), {v}.Len())")
                    self.emit(f"{cur}.Extend({v}.Items)")
                else:
                    self.emit(f"{cur}.Extend(rt.Collect({self.cursor(v, vt)}).Items)")
            elif op == "*":
                self.emit(f"rt.SizeRepeat(fuel, {cur}.Len(), {v})")
                self.emit(f"rt.IMulList({cur}, {v})")
            else:
                raise LatticeInternalError(f"gogen: list {op}=")
            self.emit(store(cur))
            return
        if isinstance(t, T.SetT):
            self.emit(f"rt.ISetOp({cur}, {v}, '{op}')")
            self.emit(store(cur))
            return
        if isinstance(t, T.DictT) and op == "|":
            self.emit(f"rt.DictUpdate({cur}, {v})")
            self.emit(store(cur))
            return
        # immutable values: cur op v, then stored
        self.emit(store(self.binop(op, cur, t, v, vt, t, grow=op in ("+", "*", "**", "<<"))))

    # ---- expressions -----------------------------------------------------------------------

    def expr(self, e: n.Expr) -> str:  # noqa: C901, PLR0911, PLR0912 - one case per node
        match e:
            case n.Const(value=value, type=t):
                return self.const(value, t)
            case n.Var(name=name, type=t):
                go, dt = self.declared(name)
                return self.narrow(go, dt, t)
            case n.Attr(obj=obj, name=name, type=t):
                return self.attr(obj, name, t)
            case n.Index():
                return self.index(e)
            case n.SliceOf(obj=obj, lo=lo, hi=hi, step=step):
                bounds = ", ".join(
                    "nil" if b is None else f"rt.P({self.expr(b)})" for b in (lo, hi, step)
                )
                if obj.type == T.STR:
                    return f"rt.SliceStr({self.expr(obj)}, {bounds})"
                if isinstance(obj.type, T.ListT):
                    return f"{self.expr(obj)}.Slice({bounds})"
                raise LatticeInternalError(f"gogen: slice of {obj.type.render()}")
            case n.Unary():
                return self.unary(e)
            case n.Binary(op=op, left=left, right=right, type=t):
                return self.binop(
                    op, self.expr(left), left.type, self.expr(right), right.type, t, grow=False
                )
            case n.Grow(op=op, left=left, right=right, type=t):
                return self.binop(
                    op, self.expr(left), left.type, self.expr(right), right.type, t, grow=True
                )
            case n.Compare():
                return self.compare(e)
            case n.BoolOp():
                return self.boolop(e)
            case n.IfExp(test=test, body=body, orelse=orelse, type=t):
                rt_ = self.spell(t)
                return (
                    f"func() {rt_} {{ if {self.truth(self.expr(test), test.type)} {{ return "
                    f"{self.conv(self.expr(body), body.type, t)} }}; return "
                    f"{self.conv(self.expr(orelse), orelse.type, t)} }}()"
                )
            case n.Call():
                return self.call(e)
            case n.Charged(call=call):
                return self.charged(call)
            case n.ListLit(elts=elts, type=T.ListT(elem=el)):
                items = ", ".join(self.conv(self.expr(x), x.type, el) for x in elts)
                return f"rt.NewList[{self.spell(el)}]({items})"
            case n.SetLit(elts=elts, type=T.SetT(elem=el)):
                items = ", ".join(self.conv(self.expr(x), x.type, el) for x in elts)
                return f"rt.NewSet[{self.spell(el)}]({items})"
            case n.TupleLit(elts=elts, type=T.TupleT(elems=es)):
                items = ", ".join(
                    self.conv(self.expr(x), x.type, et) for x, et in zip(elts, es, strict=True)
                )
                return f"{self.spell(e.type)}{{{items}}}"
            case n.DictLit():
                return self.dict_lit(e)
            case n.Comp():
                return self.comp(e)
            case n.FStr():
                return self.fstr(e)
            case n.Widen(value=v, type=t):
                return self.conv(self.expr(v), v.type, t)  # R-widen
            case n.Each() | n.Bulk():
                raise LatticeInternalError("gogen: Each/Bulk outside an iteration")
        raise LatticeInternalError(f"gogen: unsupported expression {type(e).__name__}")

    def const(self, value: object, t: T2Type) -> str:
        if value is None:
            return "struct{}{}"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int):
            if t == T.FLOAT:
                return go_float(float(value))
            return f"int64({value})"
        if isinstance(value, float):
            return go_float(value)
        if isinstance(value, str):
            return go_string(value)
        raise LatticeInternalError(f"gogen: constant {value!r}")

    def narrow(self, go: str, declared: T2Type, t: T2Type) -> str:
        """A read of a name or field declared `declared` that the checker narrowed to t."""
        if t == T.NONE and declared != T.NONE:
            return "struct{}{}"
        if isinstance(declared, T.Opt) and not isinstance(t, T.Opt):
            return f"{go}.V"
        return go

    def attr(self, obj: n.Expr, name: str, t: T2Type) -> str:
        ot = T.strip_opt(obj.type)
        match ot:
            case T.RecordT(name=rec):
                declared = dict(self.m.records[rec].fields)[name]
                return self.narrow(f"{self.expr(obj)}.F_{name}", declared, t)
            case _ if ot == T.FLOAT and name == "real":
                return self.expr(obj)
        raise LatticeInternalError(f"gogen: attribute {name} of {ot.render()}")

    def index(self, e: n.Index) -> str:
        obj, index = e.obj, e.index
        ot = obj.type
        match ot:
            case T.ListT():
                return f"{self.expr(obj)}.Get({self.expr(index)})"
            case T.DictT(key=kt):
                return f"{self.expr(obj)}.Get({self.conv(self.expr(index), index.type, kt)})"
            case T.TupleT(elems=es):
                i = _int_literal(index)
                if i is not None:
                    if i < 0:
                        i += len(es)
                    return f"{self.expr(obj)}.F{i}"
                et = e.type
                items = ", ".join(self.conv(f"t.F{j}", x, et) for j, x in enumerate(es))
                return (
                    f"func(t {self.spell(ot)}, i int64) {self.spell(et)} {{ return rt.IndexSlice("
                    f'[]{self.spell(et)}{{{items}}}, i, "tuple index out of range") }}'
                    f"({self.expr(obj)}, {self.expr(index)})"
                )
        if ot == T.STR:
            return f"rt.IndexStr({self.expr(obj)}, {self.expr(index)})"
        raise LatticeInternalError(f"gogen: index of {ot.render()}")

    def unary(self, e: n.Unary) -> str:
        operand, t = e.operand, e.type
        if e.op == "not":
            return f"!{self.truth(self.expr(operand), operand.type)}"
        if operand.type == T.BOOL and e.op in ("-", "+", "~"):
            go = f"int64(rt.B2I({self.expr(operand)}))"
            return {"-": f"(-{go})", "+": go, "~": f"(^{go})"}[e.op]
        if e.op == "-":
            if isinstance(operand, n.Const) and type(operand.value) is int and t == T.INT:
                return f"int64({-operand.value})"  # a literal (-9223372036854775808 too)
            if t == T.INT:
                return f"rt.Neg({self.expr(operand)})"
            return f"(-{self.expr(operand)})"
        if e.op == "+":
            return self.expr(operand)
        if e.op == "~":
            return f"(^{self.expr(operand)})"
        raise LatticeInternalError(f"gogen: unary {e.op}")

    def binop(  # noqa: C901, PLR0911, PLR0912 - one case per operator and type
        self, op: str, a: str, at: T2Type, b: str, bt: T2Type, t: T2Type, *, grow: bool
    ) -> str:
        """a op b (a, b: Go expressions of the operand types; t: the result type).
        `grow`: a size-checked + * ** << (charged like T2Fuel.add/mul/pow/lshift)."""
        if at == T.INT and bt == T.INT:
            match op:
                case "+":
                    return f"rt.Add({a}, {b})"
                case "*":
                    return f"rt.GrowMulInt(fuel, {a}, {b})" if grow else f"rt.Mul({a}, {b})"
                case "**":
                    if t == T.FLOAT:
                        return f"rt.PowIntF({a}, {b})"
                    return f"rt.GrowPowInt(fuel, {a}, {b})" if grow else f"rt.Pow({a}, {b})"
                case "<<":
                    return f"rt.GrowShlInt(fuel, {a}, {b})" if grow else f"rt.Shl({a}, {b})"
                case "/":
                    return f"rt.TrueDivInt({a}, {b})"
                case _ if op in _INT_OPS:
                    return f"{_INT_OPS[op]}({a}, {b})"
                case _ if op in _BIT_OPS:
                    return f"({a} {op} {b})"
        if at in T.NUMERIC and bt in T.NUMERIC:
            fa, fb = self.conv(a, at, T.FLOAT), self.conv(b, bt, T.FLOAT)
            match op:
                case "+" | "-" | "*":
                    # the explicit conversion rounds: Go may otherwise fuse x * y + z
                    # into one multiply-add (arm64), which Python never does
                    return f"float64({fa} {op} {fb})"
                case "/":
                    return f"rt.DivF({fa}, {fb})"
                case "//":
                    return f"rt.FloorDivF({fa}, {fb})"
                case "%":
                    return f"rt.ModF({fa}, {fb})"
                case "**":
                    return f"rt.PowF({fa}, {fb})"
        if at == T.BOOL and bt == T.BOOL and op in _BIT_OPS:
            return f"rt.{ {'&': 'AndB', '|': 'OrB', '^': 'XorB'}[op] }({a}, {b})"
        if at == T.STR:
            if op == "+" and bt == T.STR:
                return f"rt.GrowAddStr(fuel, {a}, {b})" if grow else f"({a} + {b})"
            if op == "*" and bt == T.INT:
                return f"rt.GrowMulStr(fuel, {a}, {b})" if grow else f"rt.RepeatStr({a}, {b})"
        if at == T.INT and bt == T.STR and op == "*":
            return (
                f"func(k int64, s string) string {{ return rt.GrowMulStr(fuel, s, k) }}({a}, {b})"
            )
        if isinstance(at, T.ListT):
            if op == "+" and isinstance(bt, T.ListT):
                return f"rt.GrowAddList(fuel, {a}, {b})"
            if op == "*" and bt == T.INT:
                return f"rt.GrowMulList(fuel, {a}, {b})"
        if at == T.INT and isinstance(bt, T.ListT) and op == "*":
            go = self.spell(bt)
            return f"func(k int64, l {go}) {go} {{ return rt.GrowMulList(fuel, l, k) }}({a}, {b})"
        if isinstance(at, T.TupleT) and isinstance(bt, T.TupleT) and op == "+":
            assert isinstance(t, T.TupleT)
            n_a, n_b = len(at.elems), len(bt.elems)
            items = [f"x.F{i}" for i in range(n_a)] + [f"y.F{i}" for i in range(n_b)]
            conv = ", ".join(
                self.conv(go, st, dt)
                for go, st, dt in zip(items, (*at.elems, *bt.elems), t.elems, strict=True)
            )
            return (
                f"func(x {self.spell(at)}, y {self.spell(bt)}) {self.spell(t)} {{ "
                f"rt.SizeAdd(fuel, {n_a}, {n_b}); return {self.spell(t)}{{{conv}}} }}({a}, {b})"
            )
        if isinstance(at, T.SetT) and isinstance(bt, T.SetT):
            method = {
                "|": "Union",
                "&": "Intersection",
                "-": "Difference",
                "^": "SymmetricDifference",
            }
            return f"{a}.{method[op]}({b})"
        if isinstance(at, T.DictT) and isinstance(bt, T.DictT) and op == "|":
            return f"rt.DictUnion({a}, {b})"
        raise LatticeInternalError(f"gogen: {at.render()} {op} {bt.render()}")

    # ---- comparisons and truth ---------------------------------------------------------

    def compare(self, e: n.Compare) -> str:
        ops, operands = e.ops, e.operands
        if len(ops) == 1:
            a, b = operands
            return self.cmp(ops[0], self.expr(a), a.type, self.expr(b), b.type)
        # a < b < c: each operand evaluated once, the chain stops at the first false
        prev = self.temp()
        lines = [f"func() bool {{ {prev} := {self.expr(operands[0])}"]
        for i, op in enumerate(ops, start=1):
            cur = self.temp()
            lines.append(f"{cur} := {self.expr(operands[i])}")
            test = self.cmp(op, prev, operands[i - 1].type, cur, operands[i].type)
            lines.append(f"if !({test}) {{ return false }}")
            prev = cur
        lines.append("return true }()")
        return "; ".join(lines)

    def cmp(self, op: str, a: str, at: T2Type, b: str, bt: T2Type) -> str:  # noqa: C901, PLR0911
        if op in ("is", "is not"):
            test = self.is_none(a, at, b, bt)
            return test if op == "is" else f"!{test}"
        if op in ("in", "not in"):
            test = self.contains(a, at, b, bt)
            return test if op == "in" else f"!{test}"
        if op in ("==", "!="):
            test = self.equal(a, at, b, bt)
            return test if op == "==" else f"!{test}"
        if op in _ORDER:
            if at == T.INT and bt == T.FLOAT:
                return f"rt.CmpIFOp({a}, {b}, {go_string(op)})"
            if at == T.FLOAT and bt == T.INT:
                return f"rt.CmpFIOp({a}, {b}, {go_string(op)})"
            if at == bt and at in (T.INT, T.FLOAT, T.STR):
                return f"({a} {op} {b})"
            common = T.join(at, bt)
            if common is None:
                raise LatticeInternalError(f"gogen: {at.render()} {op} {bt.render()}")
            ca, cb = self.conv(a, at, common), self.conv(b, bt, common)
            return f"{self.types.order(common, op)}({ca}, {cb})"
        raise LatticeInternalError(f"gogen: comparison {op}")

    def is_none(self, a: str, at: T2Type, b: str, bt: T2Type) -> str:
        if bt != T.NONE:
            a, at, b, bt = b, bt, a, at  # None is x
        if bt != T.NONE:
            raise LatticeInternalError("gogen: 'is' compares only with None")
        if isinstance(at, T.Opt):
            return (
                f"!{a}.Ok"
                if _is_simple(a)
                else f"func(o {self.spell(at)}) bool {{ return !o.Ok }}({a})"
            )
        answer = "true" if at == T.NONE else "false"
        return f"func(_ {self.spell(at)}) bool {{ return {answer} }}({a})"

    def equal(self, a: str, at: T2Type, b: str, bt: T2Type) -> str:
        if T.NONE in (at, bt):
            return self.is_none(a, at, b, bt)
        if at == T.INT and bt == T.FLOAT:
            return f'rt.CmpIFOp({a}, {b}, "==")'
        if at == T.FLOAT and bt == T.INT:
            return f'rt.CmpFIOp({a}, {b}, "==")'
        sa, sb = T.strip_opt(at), T.strip_opt(bt)
        if isinstance(at, T.Opt) != isinstance(bt, T.Opt):
            # an optional value and a plain one: equal when present and equal
            if isinstance(bt, T.Opt):
                a, at, b, bt, sa, sb = b, bt, a, at, sb, sa
            inner = at.inner  # type: ignore[union-attr]
            return (
                f"func(o {self.spell(at)}, v {self.spell(bt)}) bool {{ return o.Ok && "
                f"{self.equal('o.V', inner, 'v', bt)} }}({a}, {b})"
            )
        common = at if at == bt else T.join(at, bt)
        if common is None:
            raise LatticeInternalError(f"gogen: {at.render()} == {bt.render()}")
        ca, cb = self.conv(a, at, common), self.conv(b, bt, common)
        if common in (T.INT, T.FLOAT, T.STR, T.BOOL, T.UUID):
            return f"({ca} == {cb})"
        return f"{self.types.eq(common)}({ca}, {cb})"

    def contains(self, x: str, xt: T2Type, c: str, ct: T2Type) -> str:
        """x in c (x evaluated before c, as Python)."""
        if ct == T.STR:
            return f"func(x, s string) bool {{ return rt.ContainsStr(s, x) }}({x}, {c})"
        elem = self.elem(ct)
        cs = self.spell(ct)
        if xt == T.FLOAT and elem == T.INT:
            # exactly, as Python: only an integral Float in the Int range equals an Int
            inner = self.contains("i", T.INT, "c", ct)
            return (
                f"func(x float64, c {cs}) bool {{ i, ok := rt.FloatAsInt(x); "
                f"return ok && {inner} }}({x}, {c})"
            )
        xs = self.spell(elem)
        xg = self.conv(x, xt, elem)
        eq = self.types.eq(elem)
        match ct:
            case T.ListT():
                body = f"rt.ListContains(c, x, {eq})"
                return f"func(x {xs}, c {cs}) bool {{ return {body} }}({xg}, {c})"
            case T.SetT() | T.DictT() | T.CounterT():
                return f"func(x {xs}, c {cs}) bool {{ return c.Has(x) }}({xg}, {c})"
            case T.IterT():
                return f"func(x {xs}, c {cs}) bool {{ return c.Contains(x, {eq}) }}({xg}, {c})"
            case T.TupleT(elems=es):
                items = ", ".join(self.conv(f"t.F{i}", e, elem) for i, e in enumerate(es))
                return (
                    f"func(x {xs}, t {cs}) bool {{ return rt.SliceContains([]{xs}{{{items}}}, x, "
                    f"{self.types.eq(elem)}) }}({xg}, {c})"
                )
        raise LatticeInternalError(f"gogen: 'in' {ct.render()}")

    def truth(self, go: str, t: T2Type) -> str:  # noqa: PLR0911
        """Python's truth value of a Go value of type t (go evaluated once)."""
        if t == T.BOOL:
            return go
        if t in (T.INT, T.FLOAT):
            return f"({go} != 0)"
        if t in (T.STR, T.UUID):
            return f'({go} != "")'
        match t:
            case T.ListT() | T.SetT() | T.DictT() | T.CounterT():
                return f"({go}.Len() != 0)"
            case T.Opt(inner=inner):
                test = self.truth("o.V", inner)
                return f"func(o {self.spell(t)}) bool {{ return o.Ok && {test} }}({go})"
            case T.TupleT(elems=es):
                return (
                    f"func(_ {self.spell(t)}) bool {{ return {'true' if es else 'false'} }}({go})"
                )
        answer = "false" if t == T.NONE else "true"
        return f"func(_ {self.spell(t)}) bool {{ return {answer} }}({go})"

    def boolop(self, e: n.BoolOp) -> str:
        values = e.values
        if e.type == T.BOOL and all(v.type == T.BOOL for v in values):
            joiner = " && " if e.op == "and" else " || "
            return "(" + joiner.join(self.expr(v) for v in values) + ")"
        # the operand that decides is the value (x or default)
        rt_ = self.spell(e.type)
        parts: list[str] = []
        for i, v in enumerate(values):
            if i == len(values) - 1:
                parts.append(f"return {self.conv(self.expr(v), v.type, e.type)} }}()")
                break
            b = self.temp()
            parts.append(f"{b} := {self.expr(v)}")
            test = self.truth(b, v.type)
            if e.op == "and":
                test = f"!{test}"
            vt = v.type
            if e.op == "or" and isinstance(vt, T.Opt) and not isinstance(e.type, T.Opt):
                b, vt = f"{b}.V", vt.inner  # truthy, so present (the checker's narrowing)
            parts.append(f"if {test} {{ return {self.conv(b, vt, e.type)} }}")
        return f"func() {rt_} {{ " + "; ".join(parts)

    # ---- conversions -------------------------------------------------------------------------

    def conv(self, go: str, src: T2Type, dst: T2Type) -> str:  # noqa: PLR0911
        """A value of type src where dst is expected (Int -> Float, T -> T | None, ...)."""
        if src == dst or src is T.UNK or dst is T.UNK:
            return go
        if isinstance(dst, T.Opt):
            inner = dst.inner
            none = f"rt.None[{self.spell(inner)}]()"
            if src == T.NONE:
                if go == "struct{}{}":
                    return none
                return f"func(_ struct{{}}) {self.spell(dst)} {{ return {none} }}({go})"
            if isinstance(src, T.Opt):
                if self.spell(src) == self.spell(dst):
                    return go
                some = f"rt.Some({self.conv('o.V', src.inner, inner)})"
                return (
                    f"func(o {self.spell(src)}) {self.spell(dst)} {{ if !o.Ok {{ return {none} }}; "
                    f"return {some} }}({go})"
                )
            return f"rt.Some[{self.spell(inner)}]({self.conv(go, src, inner)})"
        if dst == T.FLOAT and src == T.INT:
            return f"float64({go})"
        if isinstance(dst, T.TupleT) and isinstance(src, T.TupleT):
            items = ", ".join(
                self.conv(f"t.F{i}", s, d)
                for i, (s, d) in enumerate(zip(src.elems, dst.elems, strict=True))
            )
            d = self.spell(dst)
            return f"func(t {self.spell(src)}) {d} {{ return {d}{{{items}}} }}({go})"
        if isinstance(dst, T.IterT) and isinstance(src, T.IterT):
            if self.spell(src) == self.spell(dst):
                return go
            f = self.conv("x", src.elem, dst.elem)
            return (
                f"rt.NewIter(rt.MapCursor({go}.Cursor(), func(x {self.spell(src.elem)}) "
                f"{self.spell(dst.elem)} {{ return {f} }}))"
            )
        if self.spell(src) == self.spell(dst):
            return go  # e.g. a Counter as a dict[K, Int]
        if isinstance(dst, T.ListT) and isinstance(src, T.ListT | T.SetT | T.DictT | T.CounterT):
            # R-widen: a container iterated where its elements are used as dst's
            el = self.elem(src)
            return f"rt.Collect({self.map_cursor(self.cursor(go, src), el, dst.elem)})"
        if isinstance(dst, T.DictT) and isinstance(src, T.DictT):
            # R-widen: a dict spread into a dict literal of wider keys / values
            k = self.conv("p.Key", src.key, dst.key)
            v = self.conv("p.Value", src.val, dst.val)
            pair = f"rt.Pair[{self.spell(src.key)}, {self.spell(src.val)}]"
            return (
                f"rt.CollectDict(rt.IterSlice({go}.Items()), func(p {pair}) "
                f"({self.spell(dst.key)}, {self.spell(dst.val)}) {{ return {k}, {v} }})"
            )
        raise LatticeInternalError(f"gogen: cannot convert {src.render()} to {dst.render()}")

    # ---- literals and comprehensions -------------------------------------------------------

    def dict_lit(self, e: n.DictLit) -> str:
        t = e.type
        if not isinstance(t, T.DictT):
            raise LatticeInternalError("gogen: dict literal type")
        go = self.spell(t)
        d = self.temp()
        parts = [f"func() {go} {{ {d} := rt.NewDict[{self.spell(t.key)}, {self.spell(t.val)}]()"]
        for k, v in zip(e.keys, e.values, strict=True):
            if k is None:
                parts.append(f"rt.DictUpdate({d}, {self.conv(self.expr(v), v.type, t)})")
            else:
                key = self.conv(self.expr(k), k.type, t.key)
                parts.append(f"{d}.Set({key}, {self.conv(self.expr(v), v.type, t.val)})")
        parts.append(f"return {d} }}()")
        return "; ".join(parts)

    def elem(self, t: T2Type) -> T2Type:
        el = T.elem_of(t)
        if el is None:
            raise LatticeInternalError(f"gogen: {t.render()} is not iterable")
        return el

    def cursor(self, go: str, t: T2Type) -> str:
        """An rt.Cursor over the (evaluated once) iterable go of type t."""
        match t:
            case T.ListT():
                return f"rt.IterList({go})"
            case T.SetT():
                return f"rt.IterSet({go})"
            case T.DictT() | T.CounterT():
                return f"rt.IterDict({go})"
            case T.IterT():
                return f"{go}.Cursor()"
            case T.TupleT(elems=es):
                el = self.elem(t)
                items = ", ".join(self.conv(f"t.F{i}", x, el) for i, x in enumerate(es))
                return (
                    f"func(t {self.spell(t)}) rt.Cursor[{self.spell(el)}] {{ return "
                    f"rt.IterSlice([]{self.spell(el)}{{{items}}}) }}({go})"
                )
        if t == T.STR:
            return f"rt.IterStr({go})"
        raise LatticeInternalError(f"gogen: cannot iterate {t.render()}")

    def map_cursor(self, cur: str, src: T2Type, dst: T2Type) -> str:
        """A cursor of src elements as one of dst elements (Int -> Float, ...)."""
        fn = f"func(x {self.spell(src)}) {self.spell(dst)} {{ return {self.conv('x', src, dst)} }}"
        return f"rt.MapCursor({cur}, {fn})"

    def length(self, go: str, t: T2Type) -> str:
        if t == T.STR:
            return f"rt.Len({go})"
        if isinstance(t, T.TupleT):
            return f"func(_ {self.spell(t)}) int64 {{ return {len(t.elems)} }}({go})"
        return f"{go}.Len()"

    def cursor_of(self, e: n.Expr) -> str:
        """An rt.Cursor over an iterable argument, charging as the IR says (Each: per
        element; Bulk: a sized value's length up front, an iterator per element)."""
        match e:
            case n.Each(iter=it):
                return f"rt.Each(fuel, {self.cursor_of(it)})"
            case n.Bulk(value=v):
                t = v.type
                if isinstance(t, T.IterT):
                    return f"rt.BulkIter(fuel, {self.expr(v)}).Cursor()"
                el = self.spell(self.elem(t))
                charge = f"fuel.Bulk(int({self.length('x', t)}))"
                return (
                    f"func(x {self.spell(t)}) rt.Cursor[{el}] {{ {charge}; "
                    f"return {self.cursor('x', t)} }}({self.expr(v)})"
                )
        return self.cursor(self.expr(e), e.type)

    def comp(self, e: n.Comp) -> str:
        if e.kind == "gen":
            return self.gen(e)
        t = e.type
        go = self.spell(t)
        match e.kind, t:
            case "list", T.ListT(elem=el):
                init = f"rt.NewList[{self.spell(el)}]()"
            case "set", T.SetT(elem=el):
                init = f"rt.NewSet[{self.spell(el)}]()"
            case "dict", T.DictT(key=kt, val=vt):
                init = f"rt.NewDict[{self.spell(kt)}, {self.spell(vt)}]()"
            case _:
                raise LatticeInternalError(f"gogen: {e.kind} comprehension of {t.render()}")
        out = self.temp()
        saved_depth = self.depth
        self.depth = 0
        self.scopes.append({})
        with self.capture() as lines:
            self.emit(f"func() {go} {{")
            self.depth += 1
            self.emit(f"{out} := {init}")
            closers = 0
            for g in e.gens:
                nxt, x, ok = self.temp(), self.temp(), self.temp()
                self.emit(f"for {nxt} := {self.cursor_of(g.iter)}; ; {{")
                self.depth += 1
                closers += 1
                self.emit(f"{x}, {ok} := {nxt}()")
                self.emit(f"if !{ok} {{ break }}")
                self.bind(g.target, x, self.elem(_strip_charge(g.iter).type), declare=True)
                for cond in g.ifs:
                    self.emit(f"if !{self.truth(self.expr(cond), cond.type)} {{ continue }}")
            match t:
                case T.ListT(elem=el):
                    self.emit(f"{out}.Append({self.conv(self.expr(e.elt), e.elt.type, el)})")
                case T.SetT(elem=el):
                    self.emit(f"{out}.Add({self.conv(self.expr(e.elt), e.elt.type, el)})")
                case T.DictT(key=kt, val=vt):
                    assert e.value is not None
                    k = self.conv(self.expr(e.elt), e.elt.type, kt)
                    v = self.conv(self.expr(e.value), e.value.type, vt)
                    self.emit(f"{out}.Set({k}, {v})")
            for _ in range(closers):
                self.depth -= 1
                self.emit("}")
            self.emit(f"return {out}")
            self.depth -= 1
            self.emit("}()")
        self.scopes.pop()
        self.depth = saved_depth
        return "\n".join(lines)

    def gen(self, e: n.Comp) -> str:
        """A generator expression: lazy, one-shot. The first clause's iterable is
        evaluated now (as Python does), the rest as elements are asked for."""
        t = e.type
        if not isinstance(t, T.IterT):
            raise LatticeInternalError("gogen: generator type")
        el = self.spell(t.elem)
        self.scopes.append({})
        saved_depth = self.depth
        self.depth = 0

        def clause(i: int) -> str:
            if i == len(e.gens):
                value = self.conv(self.expr(e.elt), e.elt.type, t.elem)
                return f"return rt.One(func() {el} {{ return {value} }})"
            g = e.gens[i]
            x = self.temp()
            et = self.elem(_strip_charge(g.iter).type)
            source = self.cursor_of(g.iter)  # in the scope of the clauses before this one
            with self.capture() as lines:
                self.bind(g.target, x, et, declare=True)
                for cond in g.ifs:
                    test = self.truth(self.expr(cond), cond.type)
                    self.emit(f"if !{test} {{ return rt.Empty[{el}]() }}")
            inner = clause(i + 1)
            body = "\n".join([*lines, inner])
            return (
                f"return rt.FlatMap({source}, func({x} {self.spell(et)}) "
                f"rt.Cursor[{el}] {{\n{body}\n}})"
            )

        text = clause(0)
        self.depth = saved_depth
        self.scopes.pop()
        return f"rt.Gen(func() rt.Cursor[{el}] {{\n{text}\n}}())"

    # ---- f-strings and text -------------------------------------------------------------------

    def fstr(self, e: n.FStr) -> str:
        parts: list[str] = []
        for p in e.parts:
            if isinstance(p, str):
                parts.append(go_string(p))
            else:
                parts.append(self.fpart(p))
        if not parts:
            return '""'
        return "(" + " + ".join(parts) + ")"

    def fpart(self, p: n.FPart) -> str:  # noqa: PLR0911
        v, t = self.expr(p.value), p.value.type
        spec: str | None = None
        if isinstance(p.spec, n.FStr):
            spec = self.fstr(p.spec)
        elif p.spec != "":
            spec = go_string(p.spec)
        if p.conversion:
            text = {"s": self.str_of, "r": self.repr_of, "a": self.ascii_of}[p.conversion](v, t)
            return text if spec is None else f"rt.FmtStr(fuel, {text}, {spec})"
        if spec is None:
            return self.str_of(v, t)
        return self.fmt(v, t, spec)

    def fmt(self, v: str, t: T2Type, spec: str) -> str:  # noqa: PLR0911
        """format(v, spec) with the spec's size check (the value, then the spec, are
        evaluated first)."""
        simple = {
            T.INT: "rt.FmtInt",
            T.FLOAT: "rt.FmtFloat",
            T.BOOL: "rt.FmtBool",
            T.STR: "rt.FmtStr",
        }
        if t in simple:
            return f"{simple[t]}(fuel, {v}, {spec})"
        go = self.spell(t)
        match t:
            case T.Opt(inner=inner):
                present = self.fmt("o.V", inner, "sp")
                return (
                    f"func(o {go}, sp string) string {{ if !o.Ok {{ return "
                    f"rt.FmtNone(fuel, sp) }}; return {present} }}({v}, {spec})"
                )
            case T.RecordT(name=name):
                tname = go_string(name)
            case T.Named(name="Error"):
                body = "rt.FmtObj(fuel, sp, e.Kind)"
                return f"func(e *rt.Error, sp string) string {{ return {body} }}({v}, {spec})"
            case _ if t == T.NONE:
                body = "rt.FmtNone(fuel, sp)"
                return f"func(_ struct{{}}, sp string) string {{ return {body} }}({v}, {spec})"
            case _ if t == T.UUID:
                tname = '"UUID"'
            case _:
                raise LatticeInternalError(f"gogen: format of {t.render()}")
        return (
            f"func(_ {go}, sp string) string {{ return rt.FmtObj(fuel, sp, {tname}) }}({v}, {spec})"
        )

    def str_of(self, go: str, t: T2Type) -> str:  # noqa: PLR0911
        """str(value) for a scalar, an optional scalar, a caught error or a record."""
        if t == T.INT:
            return f"rt.StrInt({go})"
        if t == T.FLOAT:
            return f"rt.ReprFloat({go})"
        if t == T.BOOL:
            return f"rt.StrBool({go})"
        if t in (T.STR, T.UUID):
            return go
        if t == T.NONE:
            return (
                '"None"'
                if go == "struct{}{}"
                else f'func(_ struct{{}}) string {{ return "None" }}({go})'
            )
        match t:
            case T.Opt(inner=inner):
                return (
                    f"rt.ReprOpt({go}, func(v {self.spell(inner)}) string {{ return "
                    f"{self.str_of('v', inner)} }})"
                )
            case T.Named(name="Error"):
                return f"{go}.Msg"
            case T.RecordT():
                return f"{go}.PyRepr()"
        raise LatticeInternalError(f"gogen: str() of {t.render()}")

    def repr_of(self, go: str, t: T2Type) -> str:
        if t == T.STR:
            return f"rt.ReprStr({go})"
        if t == T.UUID:
            return f'("UUID(\'" + {go} + "\')")'
        match t:
            case T.Opt(inner=inner):
                return (
                    f"rt.ReprOpt({go}, func(v {self.spell(inner)}) string {{ return "
                    f"{self.repr_of('v', inner)} }})"
                )
            case T.Named(name="Error"):
                return f"rt.ReprError({go})"
        return self.str_of(go, t)

    def ascii_of(self, go: str, t: T2Type) -> str:
        return f"rt.EscapeNonASCII({self.repr_of(go, t)})"

    # ---- calls ------------------------------------------------------------------------------

    def charged(self, c: n.Call) -> str:
        """`Charged(call)`: one step charged before the call. A method call evaluates
        its receiver first, then charges, then evaluates its arguments (emit_py's
        `<fuel>.call(recv.m)(args)`); any other call charges before its arguments
        (`rt.After(fuel.Step(), ...)`: Go evaluates call arguments left to right)."""
        if not c.target.startswith("method:"):
            return f"rt.After(fuel.Step(), {self.call(c)})"
        recv = c.args[0]
        r = self.temp()
        body = self.method(c, r)
        return (
            f"func() {self.spell(c.type)} {{ {r} := {self.expr(recv)}; fuel.Tick(); "
            f"return {body} }}()"
        )

    def method(self, c: n.Call, recv: str) -> str:
        """A method call on the (evaluated) receiver `recv`, its other arguments
        evaluated here. No method is available on Go yet (plan 5a Tasks 9-10)."""
        raise LatticeInternalError(f"gogen: call target {c.target!r} is not available on Go")

    def call(self, c: n.Call) -> str:
        kind, _, name = c.target.partition(":")
        if kind == "proc":
            return self.proc_call(name, c)
        if kind == "record":
            return self.record(name, c)
        if kind == "builtin":
            return self.builtin(name, c)
        raise LatticeInternalError(f"gogen: call target {c.target!r} is not available on Go")

    def ordered_args(
        self, names: list[str], types: list[T2Type], c: n.Call, defaults: Mapping[str, str]
    ) -> tuple[list[str], list[str]]:
        """The Go arguments of a call in parameter order, each converted to its
        parameter type, and the statements evaluating them first in the written order
        when keywords reorder them (Python evaluates arguments as written)."""
        written: list[tuple[str, n.Expr]] = list(zip(names, c.args, strict=False))
        written += list(c.kwargs)
        by_name = {p: e for p, e in written}
        in_order = [p for p, _ in written] == [p for p in names if p in by_name]
        pre: list[str] = []
        values: dict[str, str] = {}
        for p, e in written:
            go = self.conv(self.expr(e), e.type, types[names.index(p)])
            if in_order or _is_simple(go):
                values[p] = go
            else:
                tmp = self.temp()
                pre.append(f"{tmp} := {go}")
                values[p] = tmp
        out: list[str] = []
        for p in names:
            if p in values:
                out.append(values[p])
            elif p in defaults:
                out.append(defaults[p])
            else:
                raise LatticeInternalError(f"gogen: missing argument {p}")
        return pre, out

    @staticmethod
    def _wrap(pre: list[str], result_type: str, expr: str) -> str:
        if not pre:
            return expr
        return f"func() {result_type} {{ {'; '.join(pre)}; return {expr} }}()"

    def proc_call(self, name: str, c: n.Call) -> str:
        callee = self.m.procs.get(name)
        if callee is None:
            raise LatticeInternalError(f"gogen: unknown procedure {name}")
        names = [p for p, _ in callee.params]
        types = [t for _, t in callee.params]
        pre, args = self.ordered_args(names, types, c, {})
        call = f"P_{name}({', '.join(['fuel', *args])})"
        return self._wrap(pre, self.spell(callee.returns), call)

    def record(self, name: str, c: n.Call) -> str:
        rec = self.m.records.get(name)
        if rec is None:
            raise LatticeInternalError(f"gogen: unknown record {name}")
        names = [f for f, _ in rec.fields]
        types = [t for _, t in rec.fields]
        defaults = {
            f: self.conv(self.const(v, _literal_type(v)), _literal_type(v), dict(rec.fields)[f])
            for f, v in rec.defaults.items()
        }
        pre, args = self.ordered_args(names, types, c, defaults)
        go = f"rec_{name}"
        self.types.record_struct(name)
        fields = ", ".join(f"F_{f}: {a}" for f, a in zip(names, args, strict=True))
        return self._wrap(pre, go, f"{go}{{{fields}}}")

    def lambda_fn(self, lam: n.Expr, param: T2Type) -> tuple[str, T2Type]:
        """A key= lambda as a Go func of one parameter of type param."""
        if not isinstance(lam, n.Lambda) or len(lam.params) != 1:
            raise LatticeInternalError("gogen: key= must be a one-parameter lambda")
        (p,) = lam.params
        self.scopes.append({p: param})
        body = self.expr(lam.body)
        self.scopes.pop()
        kt = lam.body.type
        return (
            f"func({self.goname(p)} {self.spell(param)}) {self.spell(kt)} {{ return {body} }}",
            kt,
        )

    def builtin(self, name: str, c: n.Call) -> str:  # noqa: C901, PLR0911, PLR0912
        args, kwargs, t = c.args, dict(c.kwargs), c.type
        match name:
            case "len":
                (x,) = args
                return self.length(self.expr(x), x.type)
            case "abs":
                (x,) = args
                if x.type == T.INT:
                    return f"rt.Abs({self.expr(x)})"
                self.m.imports.add("math")
                return f"math.Abs({self.expr(x)})"
            case "round":
                x = args[0]
                if len(args) == 1:
                    return (
                        f"rt.RoundHalfEven({self.expr(x)})" if x.type == T.FLOAT else self.expr(x)
                    )
                if x.type != T.FLOAT:
                    raise LatticeInternalError(
                        "gogen: round(Int, n) (lowering widens the Int: R-widen)"
                    )
                return f"rt.RoundF({self.expr(x)}, {self.expr(args[1])})"
            case "min" | "max":
                return self.min_max(name, c)
            case "sum":
                return self.sum(c)
            case "sorted":
                (xs,) = args
                el = self.elem(_strip_charge(xs).type)
                cur = self.cursor_of(xs)
                rev = "false"
                if "reverse" in kwargs:
                    rev = self.truth(self.expr(kwargs["reverse"]), kwargs["reverse"].type)
                if "key" in kwargs:
                    fn, kt = self.lambda_fn(kwargs["key"], el)
                    return f"rt.SortedKey({cur}, {fn}, {self.types.order(kt, '<')}, {rev})"
                return f"rt.Sorted({cur}, {self.types.order(el, '<')}, {rev})"
            case "range":
                vals = [self.expr(a) for a in args]
                if len(vals) == 1:
                    return f"rt.Range(0, {vals[0]}, 1)"
                if len(vals) == 2:
                    return f"rt.Range({vals[0]}, {vals[1]}, 1)"
                return f"rt.Range({vals[0]}, {vals[1]}, {vals[2]})"
            case "enumerate":
                assert isinstance(t, T.IterT) and isinstance(t.elem, T.TupleT)
                tup = self.spell(t.elem)
                el = self.spell(t.elem.elems[1])
                start = (
                    self.expr(args[1])
                    if len(args) > 1
                    else (self.expr(kwargs["start"]) if "start" in kwargs else "0")
                )
                return (
                    f"rt.Enumerate({self.cursor_of(args[0])}, {start}, func(i int64, v {el}) {tup} "
                    f"{{ return {tup}{{i, v}} }})"
                )
            case "zip":
                assert isinstance(t, T.IterT) and isinstance(t.elem, T.TupleT)
                tup = self.spell(t.elem)
                es = t.elem.elems
                params = ", ".join(f"p{i} {self.spell(e)}" for i, e in enumerate(es))
                vals = ", ".join(f"p{i}" for i in range(len(es)))
                curs = ", ".join(self.cursor_of(a) for a in args)
                return (
                    f"rt.Zip{len(args)}({curs}, func({params}) {tup} {{ return {tup}{{{vals}}} }})"
                )
            case "reversed":
                (xs,) = args
                return f"rt.Reversed({self.expr(xs)})"
            case "any" | "all":
                (xs,) = args
                return f"rt.{name.capitalize()}({self.cursor_of(xs)})"
            case "int":
                (x,) = args
                if x.type == T.STR:
                    return f"rt.ParseInt({self.expr(x)})"
                if x.type == T.FLOAT:
                    return f"rt.IntOfFloat({self.expr(x)})"
                if x.type == T.BOOL:
                    return f"int64(rt.B2I({self.expr(x)}))"
                return self.expr(x)
            case "float":
                (x,) = args
                if x.type == T.STR:
                    return f"rt.ParseFloat({self.expr(x)})"
                if x.type == T.BOOL:
                    return f"float64(rt.B2I({self.expr(x)}))"
                return self.conv(self.expr(x), x.type, T.FLOAT)
            case "str":
                if not args:
                    return '""'
                (x,) = args
                return self.str_of(self.expr(x), x.type)
            case "list" | "set":
                el = self.elem(t)
                if not args:
                    return f"rt.New{name.capitalize()}[{self.spell(el)}]()"
                fn = "Collect" if name == "list" else "CollectSet"
                src_el = self.elem(_strip_charge(args[0]).type)
                cur = self.cursor_of(args[0])
                if src_el != el:
                    cur = self.map_cursor(cur, src_el, el)
                return f"rt.{fn}({cur})"
            case "dict":
                assert isinstance(t, T.DictT)
                if not args:
                    return f"rt.NewDict[{self.spell(t.key)}, {self.spell(t.val)}]()"
                src_el = self.elem(_strip_charge(args[0]).type)
                if not isinstance(src_el, T.TupleT) or len(src_el.elems) != 2:
                    raise LatticeInternalError("gogen: dict() of non-pairs")
                k = self.conv("p.F0", src_el.elems[0], t.key)
                v = self.conv("p.F1", src_el.elems[1], t.val)
                return (
                    f"rt.CollectDict({self.cursor_of(args[0])}, func(p {self.spell(src_el)}) "
                    f"({self.spell(t.key)}, {self.spell(t.val)}) {{ return {k}, {v} }})"
                )
        raise LatticeInternalError(f"gogen: builtin {name} is not available on Go")

    def min_max(self, name: str, c: n.Call) -> str:
        t = c.type
        better = self.types.order
        op = "<" if name == "min" else ">"
        if len(c.args) == 1:
            (xs,) = c.args
            el = self.elem(_strip_charge(xs).type)
            cur = self.cursor_of(xs)
            if el != t:
                cur = self.map_cursor(cur, el, t)
        else:
            items = ", ".join(self.conv(self.expr(a), a.type, t) for a in c.args)
            cur = f"rt.IterSlice([]{self.spell(t)}{{{items}}})"
        key = dict(c.kwargs).get("key")
        if key is not None:
            fn, kt = self.lambda_fn(key, t)
            return f"rt.MinMaxKey({cur}, {fn}, {better(kt, op)}, {go_string(name)})"
        return f"rt.MinMax({cur}, {better(t, op)}, {go_string(name)})"

    def sum(self, c: n.Call) -> str:
        xs = c.args[0]
        start = c.args[1] if len(c.args) > 1 else None
        el = self.elem(_strip_charge(xs).type)
        cur = self.cursor_of(xs)
        if c.type == T.INT:
            s = "0" if start is None else self.expr(start)
            return f"rt.SumInt({cur}, {s})"
        if el == T.INT:
            cur = self.map_cursor(cur, T.INT, T.FLOAT)
        if start is None:
            return f"rt.SumFloatV({cur}, 0, true)"
        if start.type == T.INT:
            return f"rt.SumFloatV({cur}, float64({self.expr(start)}), true)"
        return f"rt.SumFloatV({cur}, {self.expr(start)}, false)"


def _strip_charge(e: n.Expr) -> n.Expr:
    while isinstance(e, n.Each | n.Bulk):
        e = e.iter if isinstance(e, n.Each) else e.value
    return e


def _int_literal(e: n.Expr) -> int | None:
    match e:
        case n.Const(value=int() as v) if not isinstance(v, bool):
            return v
        case n.Unary(op="-", operand=n.Const(value=int() as v)) if not isinstance(v, bool):
            return -v
    return None


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
    raise LatticeInternalError(f"gogen: default {value!r}")


# ---- the module -------------------------------------------------------------------------------


def emit_go_t2(module: n.T2Module, package: str = "main") -> str:
    """The Go source of every procedure, record and module constant of `module`, as
    one file of package `package`."""
    mod = _Module(module)
    procs = [_Proc(mod, p).function() for p in module.procedures]
    consts: list[str] = []
    for name, t, value in module.constants:
        e = _Proc(mod, None)
        consts.append(f"var C_{name} {mod.types.spell(t)} = {e.conv(e.expr(value), value.type, t)}")
    for rec in module.records:
        mod.types.record_struct(rec.name)
    body = "\n".join([mod.types.decls(), *consts, "", *procs])
    if "math." in body:
        mod.imports.add("math")
    imports = "\n".join(f'\t"{i}"' for i in sorted(mod.imports))
    head = "// Code generated by lattice (lattice/gogen/t2.py). DO NOT EDIT.\n\n"
    return f"{head}package {package}\n\nimport (\n{imports}\n)\n\n{body}"
