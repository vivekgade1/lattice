"""T2 IR -> Go (plan 5a Tasks 8-10): the core statements, expressions and builtins,
the Str methods, the `string` (and every stdlib) constant, regular expressions, and
(Task 10) the list / dict / set / Counter methods, collections.Counter/defaultdict,
math, Date / DateTime / Duration and json -- every T2 call.

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
  `return` inside them sets a control code the code after `rt.Try` acts on;
* a Str method is an `rt` function over code points (`rt.Find`, `rt.Center`, ...);
  `Str.replace`/`Str.join`/`re.sub` are `Sized` (`rt.After(fuel.Step(), rt.Sized*)`:
  the step before the arguments, the sizes after, as `T2Fuel.replace/join/sub`), a
  padding width is `rt.Width`, a regex input `rt.Scan`;
* a regular expression (always a literal) is compiled once per program into a
  package-level `re_<n>` -- Python's own SRE code (`lattice.gogen.regex`), run by the
  port of CPython's engine in rt/regex.go -- and a `re.sub` replacement into `tm_<n>`;
* a dict or set follows CPython's entry layout (rt/omap.go), so one changed while it is
  iterated stops or raises where CPython's does; a dict literal is built as CPython's
  compiler builds it (presized chunks, merged); an Int looked up among Floats is
  compared exactly (never widened);
* `json.dumps` is `Sized` (`rt.SizedDumps`: the step, the value and keywords in the
  written order, then the node charges and sizes); a Json is the value itself (`any`);
  `timedelta(...)` evaluates its keywords as written and accumulates them in CPython's
  order (`rt.Timedelta`).

Values: see `lattice.gogen.types`. Constructs Go lacks are rejected before this runs
(`lattice.check.p8_target`, L892); reaching one here is an internal error.
"""

from __future__ import annotations

import importlib
import math
import struct
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

from lattice.catalog import T2_STDLIB
from lattice.check.t2 import types as T
from lattice.check.t2.types import T2Type
from lattice.gogen.regex import compile_regex, compile_template
from lattice.gogen.types import GoTypes
from lattice.runtime import LatticeInternalError
from lattice.t2ir import nodes as n

__all__ = ["emit_go_t2", "go_float", "go_regex", "go_string"]

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
_PACKAGE_PREFIXES = (
    "P_", "C_", "rec_", "tup_", "eq_", "lt_", "le_", "gt_", "ge_", "re_", "tm_", "xc_",
    "row_", "rowp_", "caller_",  # Row and Caller structs (plan 5a Task 12)
)  # fmt: skip
_INT_OPS: Mapping[str, str] = {"-": "rt.Sub", "//": "rt.FloorDiv", "%": "rt.Mod", ">>": "rt.Shr"}
_BIT_OPS = frozenset({"&", "|", "^"})
_TIME = frozenset({T.DATE, T.DATETIME, T.DURATION})
# The attributes of Date, DateTime and Duration (catalog T2_ATTRS) as Go methods.
_TIME_ATTRS: Mapping[str, str] = {
    "year": "Year()", "month": "Month()", "day": "Day()", "hour": "Hour()",
    "minute": "Minute()", "second": "Second()", "microsecond": "Microsecond()",
    "days": "Days()", "seconds": "Seconds()",
}  # fmt: skip
_ORDER = frozenset({"<", "<=", ">", ">="})


def go_regex(pattern: str) -> str:
    """A Go expression for `pattern` compiled as Python compiles it (`re.compile(pattern,
    re.ASCII)`, lattice.gogen.regex): an `*rt.Regex` run by Python's SRE engine."""
    rx = compile_regex(pattern)
    if rx.error is not None:
        kind, msg = rx.error
        return f"rt.BadRegex({go_string(pattern)}, {go_string(kind)}, {go_string(msg)})"
    code = ", ".join(str(x) for x in rx.code)
    names = ", ".join(f"{go_string(k)}: {v}" for k, v in rx.names)
    return (
        f"rt.NewRegex({go_string(pattern)}, []uint32{{{code}}}, {rx.groups}, "
        f"map[string]int{{{names}}})"
    )


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

    def __init__(self, module: n.T2Module, types: GoTypes | None = None) -> None:
        self.module = module
        records = tuple((r.name, r.fields) for r in module.records)
        if types is None:
            types = GoTypes(records)
        else:
            types.records.update(records)
        self.types = types
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
        self.regexes: dict[str, str] = {}  # pattern -> its package-level var
        self.templates: dict[tuple[str, str], str] = {}  # (pattern, repl) -> its var
        self.decls: list[str] = []

    def regex(self, pattern: str) -> str:
        """The package-level var holding `pattern` compiled (once per program), as
        Python compiles it (re.ASCII; lattice.gogen.regex)."""
        name = self.regexes.get(pattern)
        if name is None:
            name = self.regexes[pattern] = f"re_{len(self.regexes) + 1}"
            value = go_regex(pattern)
            self.decls.append(
                f"// {name}: re.compile({go_string(pattern)}, re.ASCII)\nvar {name} = {value}\n"
            )
        return name

    def template(self, pattern: str, repl: str) -> str:
        """The package-level var holding a re.sub replacement compiled as Python does."""
        key = (pattern, repl)
        name = self.templates.get(key)
        if name is None:
            name = self.templates[key] = f"tm_{len(self.templates) + 1}"
            t = compile_template(pattern, repl)
            if t.error is not None:
                kind, msg = t.error
                value = (
                    f"&rt.Template{{Repl: {go_string(repl)}, ErrKind: {go_string(kind)}, "
                    f"ErrMsg: {go_string(msg)}}}"
                )
            else:
                lits = ", ".join(go_string(x) for x in t.lits)
                groups = ", ".join(str(g) for g in t.groups)
                value = (
                    f"&rt.Template{{Repl: {go_string(repl)}, Lits: []string{{{lits}}}, "
                    f"Groups: []int{{{groups}}}}}"
                )
            self.decls.append(f"var {name} = {value}\n")
        return name

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
        qual = self.m.module.imports.get(name)
        if qual is not None and "." in qual:
            return self.std_constant(*qual.split(".", 1))
        raise LatticeInternalError(f"gogen: unknown name {name!r}")

    def std_constant(self, module: str, name: str) -> tuple[str, T2Type]:
        """A stdlib constant (`string.digits`, `math.pi`): its exact value, copied."""
        entry = T2_STDLIB.get(module, {}).get(name)
        if not isinstance(entry, str):
            raise LatticeInternalError(f"gogen: {module}.{name} is not a constant")
        value = getattr(importlib.import_module(module), name)
        t = T.STR if isinstance(value, str) else T.FLOAT
        return self.const(value, t), t

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
                    case T.CounterT(key=kt):
                        self.emit(
                            f"{o}.Set({self.conv(k, index.type, kt)}, {self.conv(go, t, T.INT)})"
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
                    case T.CounterT(key=kt):
                        et = T.INT
                    case _:
                        raise LatticeInternalError(f"gogen: item assignment on {ot.render()}")
                o, k, cur = self.temp(), self.temp(), self.temp()
                self.emit("{")
                with self.indented():
                    self.emit(f"{o} := {self.expr(obj)}")
                    read = f"{o}.Get({k})"
                    if isinstance(ot, T.DictT | T.CounterT) and _mismatch(index.type, kt):
                        # the key read and written (t2_fkey): exact when present, else
                        # widened where the read stores or counts it; a plain dict's
                        # read raises KeyError
                        p, kk, ok, kok = self.temp(), self.temp(), self.temp(), self.temp()
                        self.emit(f"{p} := {self.expr(index)}")
                        self.emit(f"{kk}, {ok} := ({self.types.exact(index.type, kt)})({p})")
                        widened = self.conv(p, index.type, kt)
                        self.emit(f"{k}, {kok} := rt.DictKeyX({o}, {kk}, {ok}, {widened})")
                        read = f"rt.DictReadX({o}, {k}, {kok}, {p})"
                    else:
                        self.emit(f"{k} := {self.conv(self.expr(index), index.type, kt)}")
                    if s.grow:
                        v = self.temp()
                        self.emit(f"{v} := {self.expr(s.value)}")
                        self.emit(f"{cur} := {read}")
                    else:
                        self.emit(f"{cur} := {read}")
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
            self.emit(f"rt.DictIOr({cur}, {v})")
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
            case n.ListLit(elts=elts, type=T.JSON):  # mixed elements where a Json is expected
                items = ", ".join(self.conv(self.expr(x), x.type, T.JSON) for x in elts)
                return f"any(rt.NewList[any]({items}))"
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
            case n.Sized():
                return self.sized(e)
            case n.Scan(value=v):
                return f"rt.Scan(fuel, {self.expr(v)})"
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
            case T.RowT() | T.Named(name="Caller"):  # a T2Row: its fields (plan 5a Task 12)
                declared = dict(self.types.fields(ot))[name]
                return self.narrow(f"{self.expr(obj)}.F_{name}", declared, t)
            case _ if ot == T.FLOAT and name == "real":
                return self.expr(obj)
            case _ if ot in _TIME and name in _TIME_ATTRS:
                return f"{self.expr(obj)}.{_TIME_ATTRS[name]}"
        if isinstance(obj, n.Var) and self.m.module.imports.get(obj.name) in T2_STDLIB:
            return self.std_constant(self.m.module.imports[obj.name], name)[0]
        raise LatticeInternalError(f"gogen: attribute {name} of {ot.render()}")

    def index(self, e: n.Index) -> str:
        obj, index = e.obj, e.index
        ot = obj.type
        match ot:
            case T.ListT():
                return f"{self.expr(obj)}.Get({self.expr(index)})"
            case T.DictT(key=kt) | T.CounterT(key=kt):
                if _mismatch(index.type, kt):  # exact (a defaultdict stores it widened)
                    return self.probe_call(
                        "rt.DictGetX(d, kk, ok, {w}, p)", ot, self.expr(obj), index, kt, e.type
                    )
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
        if ot == T.JSON:
            return f"rt.JsonIndex({self.expr(obj)}, any({self.expr(index)}))"
        raise LatticeInternalError(f"gogen: index of {ot.render()}")

    def unary(self, e: n.Unary) -> str:
        operand, t = e.operand, e.type
        if e.op == "not":
            return f"!{self.truth(self.expr(operand), operand.type)}"
        if operand.type == T.BOOL and e.op in ("-", "+", "~"):
            go = f"int64(rt.B2I({self.expr(operand)}))"
            return {"-": f"(-{go})", "+": go, "~": f"(^{go})"}[e.op]
        if t == T.DURATION and e.op in ("-", "+"):
            return f"{self.expr(operand)}.Neg()" if e.op == "-" else self.expr(operand)
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
        if _TIME & {at, bt}:
            return self.time_op(op, a, at, b, bt, t)
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

    def time_op(self, op: str, a: str, at: T2Type, b: str, bt: T2Type, t: T2Type) -> str:  # noqa: PLR0911
        """Date / DateTime / Duration arithmetic (datetime's operators), each operand
        evaluated once, left first."""
        D = T.DURATION  # noqa: N806

        def fn(body: str) -> str:
            return (
                f"func(x {self.spell(at)}, y {self.spell(bt)}) {self.spell(t)} "
                f"{{ return {body} }}({a}, {b})"
            )

        match op, at, bt:
            case "+", _, _ if at == D and bt == D:
                return fn("x.Add(y)")
            case "+", _, _ if bt == D:
                return fn("x.AddDur(y)")
            case "+", _, _ if at == D:
                return fn("y.AddDur(x)")
            case "-", _, _ if bt == D and at != D:
                return fn("x.SubDur(y)")
            case "-", _, _ if at == bt:
                return fn("x.Sub(y)")
            case "*", _, _ if at == D:
                return fn("x.MulInt(y)" if bt == T.INT else "x.MulFloat(y)")
            case "*", _, _ if bt == D:
                return fn("y.MulInt(x)" if at == T.INT else "y.MulFloat(x)")
            case "/", _, _ if at == D:
                return fn({T.INT: "x.DivInt(y)", T.FLOAT: "x.DivFloat(y)"}.get(bt, "x.Div(y)"))
            case "//", _, _ if at == D:
                return fn("x.FloorDivInt(y)" if bt == T.INT else "x.FloorDiv(y)")
            case "%", _, _ if at == D and bt == D:
                return fn("x.Mod(y)")
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
            if at == bt and at in (T.INT, T.FLOAT, T.STR, T.DATE, T.DATETIME):
                return f"({a} {op} {b})"
            if at == bt == T.DURATION:
                return f"({a}.Cmp({b}) {op} 0)"
            if isinstance(at, T.TupleT) and isinstance(bt, T.TupleT) and _mismatch(at, bt):
                return self.mixed_order(op, a, at, b, bt)
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
        if T.strip_opt(at) == T.JSON:
            return f"rt.JsonIsNone({a})"  # a Json may hold None
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
        if _mismatch(at, bt):
            # Ints and Floats inside: compared exactly, each item with its own type
            return self.mixed_equal(a, at, b, bt)
        common = at if at == bt else T.join(at, bt)
        if common is None:
            raise LatticeInternalError(f"gogen: {at.render()} == {bt.render()}")
        ca, cb = self.conv(a, at, common), self.conv(b, bt, common)
        if common in (T.INT, T.FLOAT, T.STR, T.BOOL, T.UUID, *_TIME):
            return f"({ca} == {cb})"
        return f"{self.types.eq(common)}({ca}, {cb})"

    def mixed_equal(self, a: str, at: T2Type, b: str, bt: T2Type) -> str:
        """a == b for two optional values or two tuples whose Int and Float items differ
        (Python compares them item by item, exactly)."""
        fn = f"func(x {self.spell(at)}, y {self.spell(bt)}) bool"
        match at, bt:
            case T.Opt(inner=ai), T.Opt(inner=bi):
                test = self.equal("x.V", ai, "y.V", bi)
                body = f"if !x.Ok || !y.Ok {{ return x.Ok == y.Ok }}; return {test}"
            case T.TupleT(elems=xs), T.TupleT(elems=ys):
                if len(xs) != len(ys):
                    body = "return false"
                else:
                    pairs = enumerate(zip(xs, ys, strict=True))
                    tests = [self.equal(f"x.F{i}", p, f"y.F{i}", q) for i, (p, q) in pairs]
                    body = "return " + " && ".join(tests)
            case _:
                raise LatticeInternalError(f"gogen: {at.render()} == {bt.render()}")
        return f"{fn} {{ {body} }}({a}, {b})"

    def mixed_order(self, op: str, a: str, at: T.TupleT, b: str, bt: T.TupleT) -> str:
        """a op b for two tuples whose Int and Float items differ: CPython's tuple
        comparison (the first items that differ decide, exactly; else the lengths)."""
        lines = [f"func(x {self.spell(at)}, y {self.spell(bt)}) bool {{"]
        for i, (p, q) in enumerate(zip(at.elems, bt.elems, strict=False)):
            same = self.equal(f"x.F{i}", p, f"y.F{i}", q)
            decide = self.cmp(op, f"x.F{i}", p, f"y.F{i}", q)
            lines.append(f"if !({same}) {{ return {decide} }}")
        lines.append(f"return {len(at.elems)} {op} {len(bt.elems)} }}({a}, {b})")
        return "; ".join(lines)

    def contains(self, x: str, xt: T2Type, c: str, ct: T2Type) -> str:
        """x in c (x evaluated before c, as Python)."""
        if ct == T.STR:
            return f"func(x, s string) bool {{ return rt.ContainsStr(s, x) }}({x}, {c})"
        elem = self.elem(ct)
        cs = self.spell(ct)
        if isinstance(ct, T.TupleT):
            # element by element, each compared with its own type (Ints and Floats exactly)
            tests = " || ".join(self.equal("x", xt, f"t.F{i}", e) for i, e in enumerate(ct.elems))
            xs_ = self.spell(xt)
            return f"func(x {xs_}, t {cs}) bool {{ return {tests or 'false'} }}({x}, {c})"
        if _mismatch(xt, elem):
            # exactly, as ==: an Int and a Float (at any depth) are never converted
            xs_, es = self.spell(xt), self.spell(elem)
            match ct:
                case T.ListT() | T.IterT():
                    match_fn = f"func(y {es}) bool {{ return {self.equal('x', xt, 'y', elem)} }}"
                    body = (
                        f"c.CountBy({match_fn}) > 0"
                        if isinstance(ct, T.ListT)
                        else f"rt.IterContainsBy(c, {match_fn})"
                    )
                case T.SetT() | T.DictT() | T.CounterT():
                    exact = self.types.exact(xt, elem)
                    body = f"func() bool {{ k, ok := ({exact})(x); return ok && c.Has(k) }}()"
                case _:
                    raise LatticeInternalError(f"gogen: 'in' {ct.render()}")
            return f"func(x {xs_}, c {cs}) bool {{ return {body} }}({x}, {c})"
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
        raise LatticeInternalError(f"gogen: 'in' {ct.render()}")

    def truth(self, go: str, t: T2Type) -> str:  # noqa: PLR0911
        """Python's truth value of a Go value of type t (go evaluated once)."""
        if t == T.BOOL:
            return go
        if t in (T.INT, T.FLOAT):
            return f"({go} != 0)"
        if t in (T.STR, T.UUID):
            return f'({go} != "")'
        if t == T.DURATION:
            return f"{go}.Truth()"
        if t == T.JSON:
            return f"rt.JsonTruth({go})"
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
        if dst == T.JSON:
            return f"any({go})"  # the value itself, shared (rt.jnorm reads optionals)
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
        if t == T.JSON:  # mixed values where a Json is expected: a dict[Str, Json]
            inner = n.DictLit(e.keys, e.values, T.DictT(T.STR, T.JSON))
            return f"any({self.dict_lit(inner)})"
        if not isinstance(t, T.DictT):
            raise LatticeInternalError("gogen: dict literal type")
        go = self.spell(t)
        kv = f"{self.spell(t.key)}, {self.spell(t.val)}"
        d = self.temp()
        parts = [f"func() {go} {{ var {d} {go}"]
        have = False

        def subdict(idx: list[int]) -> None:
            """CPython's codegen_subdict: BUILD_MAP n (presized, every key and value
            evaluated first) or, for more than 15 entries, BUILD_MAP 0 + MAP_ADD each;
            the first one is the dict, a later one is merged into it (DICT_UPDATE)."""
            nonlocal have
            sub = self.temp()
            pairs = [
                (
                    self.conv(self.expr(e.keys[i]), e.keys[i].type, t.key),  # type: ignore[union-attr]
                    self.conv(self.expr(e.values[i]), e.values[i].type, t.val),
                )
                for i in idx
            ]
            if len(idx) * 2 > 30:
                parts.append(f"{sub} := rt.NewDict[{kv}]()")
                parts.extend(f"{sub}.Set({k}, {v})" for k, v in pairs)
            else:
                temps = []
                for k, v in pairs:
                    tk, tv = self.temp(), self.temp()
                    parts.append(f"{tk}, {tv} := {k}, {v}")
                    temps.append((tk, tv))
                parts.append(f"{sub} := rt.NewDictSized[{kv}]({len(idx)})")
                parts.extend(f"{sub}.Set({k}, {v})" for k, v in temps)
            parts.append(f"{d}.Merge({sub})" if have else f"{d} = {sub}")
            have = True

        elements: list[int] = []
        for i, k in enumerate(e.keys):
            if k is None:  # **v
                if elements:
                    subdict(elements)
                    elements = []
                if not have:
                    parts.append(f"{d} = rt.NewDict[{kv}]()")
                    have = True
                v = e.values[i]
                parts.append(f"{d}.Merge({self.conv(self.expr(v), v.type, t)})")
            elif len(elements) * 2 > 30:
                subdict([*elements, i])
                elements = []
            else:
                elements.append(i)
        if elements:
            subdict(elements)
        if not have:
            parts.append(f"{d} = rt.NewDict[{kv}]()")
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
        simple |= {T.DATE: "rt.FmtDate", T.DATETIME: "rt.FmtDateTime", T.JSON: "rt.FmtJson"}
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
            case T.Named(name="Match"):
                tname = '"re.Match"'
            case T.RowT() | T.Named(name="Caller"):
                tname = '"T2Row"'
            case T.Named(name="Error"):
                body = "rt.FmtObj(fuel, sp, e.Kind)"
                return f"func(e *rt.Error, sp string) string {{ return {body} }}({v}, {spec})"
            case _ if t == T.NONE:
                body = "rt.FmtNone(fuel, sp)"
                return f"func(_ struct{{}}, sp string) string {{ return {body} }}({v}, {spec})"
            case _ if t == T.UUID:
                tname = '"UUID"'
            case _ if t == T.DURATION:
                tname = '"datetime.timedelta"'
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
        if t == T.DATE:
            return f"{go}.ISO()"
        if t in (T.DATETIME, T.DURATION):
            return f"{go}.Str()"
        if t == T.JSON:
            return f"rt.JsonStr({go})"
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
            case T.RecordT() | T.RowT() | T.Named(name="Match") | T.Named(name="Caller"):
                return f"{go}.PyRepr()"
        raise LatticeInternalError(f"gogen: str() of {t.render()}")

    def repr_of(self, go: str, t: T2Type) -> str:
        if t == T.STR:
            return f"rt.ReprStr({go})"
        if t == T.UUID:
            return f'("UUID(\'" + {go} + "\')")'
        if t in _TIME:
            return f"{go}.PyRepr()"
        if t == T.JSON:
            return f"rt.JsonRepr({go})"
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
        evaluated here, in order (Go evaluates call arguments left to right)."""
        kind, _, name = c.target.removeprefix("method:").partition(".")
        if kind in ("list", "dict", "set", "Counter"):
            return self.coll_method(kind, name, c, recv)
        args = [self.expr(a) for a in c.args[1:]]
        if kind in ("Date", "DateTime", "Duration"):
            calls = {
                "isoformat": "ISO()" if kind == "Date" else "Isoformat()",
                "weekday": "Weekday()", "isoweekday": "IsoWeekday()",
                "date": "Date()", "timestamp": "Timestamp()", "total_seconds": "TotalSeconds()",
                "strftime": f"Strftime({args[0] if args else ''})",
            }  # fmt: skip
            return f"{recv}.{calls[name]}"
        if kind == "Str":
            return self.str_method(name, recv, args, c)
        if kind == "Match":
            match name, c.args[1:]:
                case "group", []:
                    return f"{recv}.Group0()"
                case "group", [a] if a.type == T.STR:
                    return f"{recv}.GroupName({args[0]})"
                case "group", [_]:
                    return f"{recv}.Group({args[0]})"
                case "start" | "end", []:
                    return f"{recv}.{name.capitalize()}()"
        raise LatticeInternalError(f"gogen: call target {c.target!r} is not available on Go")

    def coll_method(self, kind: str, name: str, c: n.Call, r: str) -> str:  # noqa: C901, PLR0911, PLR0912
        """A list / dict / set / Counter method on the receiver r (its arguments
        evaluated here, in order; an argument consumed whole is `Bulk`)."""
        rt_ = T.strip_opt(c.args[0].type)
        args = c.args[1:]
        kw = dict(c.kwargs)
        res = self.spell(c.type)

        def val(i: int, t: T2Type) -> str:
            return self.conv(self.expr(args[i]), args[i].type, t)

        def probe(el: T2Type) -> bool:  # an Int (at any depth) looked up among Floats
            return bool(args) and _mismatch(args[0].type, el)

        def px(body: str, slot: T2Type, extra: T2Type | None = None) -> str:
            return self.probe_call(
                body, rt_, r, args[0], slot, c.type, args[1] if extra else None, extra
            )

        if isinstance(rt_, T.ListT):
            el = rt_.elem
            eq = self.types.eq(el)
            match name:
                case "append":
                    return f"{r}.Append({val(0, el)})"
                case "extend":
                    return self.extend(r, args[0])
                case "insert":
                    return f"{r}.Insert({self.expr(args[0])}, {val(1, el)})"
                case "pop":
                    return f"{r}.Pop({f'rt.P({self.expr(args[0])})' if args else 'nil'})"
                case "remove" | "index" | "count":
                    fn = {"remove": "RemoveBy", "index": "IndexBy", "count": "CountBy"}[name]
                    if probe(el):
                        match = (
                            f"func(y {self.spell(el)}) bool "
                            f"{{ return {self.equal('x', args[0].type, 'y', el)} }}"
                        )
                        xt = self.spell(args[0].type)
                    else:
                        match = f"func(y {self.spell(el)}) bool {{ return {eq}(y, x) }}"
                        xt = self.spell(el)
                    x = self.expr(args[0]) if probe(el) else val(0, el)
                    return f"func(x {xt}) {res} {{ return {r}.{fn}({match}) }}({x})"
                case "clear" | "reverse" | "copy":
                    return f"{r}.{name.capitalize()}()"
                case "sort":
                    rev = "false"
                    if "reverse" in kw:
                        rev = self.truth(self.expr(kw["reverse"]), kw["reverse"].type)
                    if "key" in kw:
                        fn, kt = self.lambda_fn(kw["key"], el)
                        return f"rt.SortKey({r}, {fn}, {self.types.order(kt, '<')}, {rev})"
                    return f"{r}.Sort({self.types.order(el, '<')}, {rev})"
        elif isinstance(rt_, T.SetT):
            el = rt_.elem
            match name:
                case "add":
                    return f"{r}.Add({val(0, el)})"
                case "discard" | "remove":
                    if probe(el):
                        if name == "discard":
                            return px("rt.SetDiscardX(d, kk, ok)", el)
                        return px("rt.SetRemoveX(d, kk, ok, p)", el)
                    return f"{r}.{name.capitalize()}({val(0, el)})"
                case "clear" | "copy":
                    return f"{r}.{name.capitalize()}()"
                case "update" | "union" | "intersection" | "difference" | "issubset" | "issuperset":
                    fn = {
                        "update": "UpdateCur", "union": "UnionCur",
                        "intersection": "IntersectionCur", "difference": "DifferenceCur",
                        "issubset": "IsSubsetCur", "issuperset": "IsSupersetCur",
                    }[name]  # fmt: skip
                    return f"{r}.{fn}({self.elem_cursor(args[0], el)})"
        elif isinstance(rt_, T.DictT | T.CounterT):
            kt = rt_.key
            vt = rt_.val if isinstance(rt_, T.DictT) else T.INT
            match name:
                case "get" if len(args) == 1:
                    if probe(kt):
                        return px(self.flat_opt("rt.DictGetOptX(d, kk, ok)", vt), kt)
                    return self.flat_opt(f"{r}.GetOpt({val(0, kt)})", vt)
                case "get":
                    if probe(kt):
                        return px("rt.DictGetOrX(d, kk, ok, dflt)", kt, vt)
                    return f"{r}.GetOr({val(0, kt)}, {val(1, vt)})"
                case "pop" if len(args) == 1:
                    if probe(kt):
                        return px("rt.DictPopX(d, kk, ok, p)", kt)
                    return (
                        f"func(k {self.spell(kt)}) {res} {{ return {r}.Pop(k, k) }}({val(0, kt)})"
                    )
                case "pop":
                    if probe(kt):
                        return px("rt.DictPopOrX(d, kk, ok, dflt)", kt, vt)
                    return f"{r}.PopOr({val(0, kt)}, {val(1, vt)})"
                case "setdefault":
                    return f"{r}.SetDefault({val(0, kt)}, {val(1, vt)})"
                case "update":
                    return f"{r}.Update({val(0, T.DictT(kt, vt))})"
                case "clear" | "copy":
                    return f"{r}.{name.capitalize()}()"
                case "keys":
                    return f"rt.KeysView({r})"
                case "values":
                    return f"rt.ValuesView({r})"
                case "items":
                    return f"rt.ItemsView({r}, {self.pair_fn(kt, vt, c.type)})"
                case "most_common":
                    n_ = f"rt.P({self.expr(args[0])})" if args else "nil"
                    assert isinstance(c.type, T.ListT)
                    return f"rt.MostCommon({r}, {n_}, {self.pair_fn(kt, T.INT, c.type)})"
        raise LatticeInternalError(f"gogen: call target {c.target!r} is not available on Go")

    def probe_call(  # noqa: PLR0913
        self,
        body: str,
        ct: T2Type,
        c: str,
        probe: n.Expr,
        slot: T2Type,
        result: T2Type,
        extra: n.Expr | None = None,
        extra_t: T2Type | None = None,
    ) -> str:
        """A lookup of `probe` (holding an Int where `slot` holds a Float) in the
        container c: `body` sees d (the container), p (the probe), kk and ok (the probe
        converted exactly, when a slot value can equal it), {w} (the probe widened, the
        key a store uses) and dflt (the evaluated `extra` argument)."""
        pt = probe.type
        params = f"d {self.spell(ct)}, p {self.spell(pt)}"
        values = f"{c}, {self.expr(probe)}"
        if extra is not None and extra_t is not None:
            params += f", dflt {self.spell(extra_t)}"
            values += f", {self.conv(self.expr(extra), extra.type, extra_t)}"
        widened = self.conv("p", pt, slot) if "{w}" in body else ""
        call = body.replace("{w}", widened)
        exact = self.types.exact(pt, slot)
        return (
            f"func({params}) {self.spell(result)} {{ kk, ok := ({exact})(p); _ = ok; "
            f"return {call} }}({values})"
        )

    def pair_fn(self, kt: T2Type, vt: T2Type, container: T2Type) -> str:
        """A Go func building the (key, value) tuple an items view / most_common yields."""
        tup = self.elem(container)
        assert isinstance(tup, T.TupleT)
        k = self.conv("k", kt, tup.elems[0])
        v = self.conv("v", vt, tup.elems[1])
        go = self.spell(tup)
        return f"func(k {self.spell(kt)}, v {self.spell(vt)}) {go} {{ return {go}{{{k}, {v}}} }}"

    def flat_opt(self, go: str, vt: T2Type) -> str:
        """d.get(k) of a dict whose values are already optional: V | None is V."""
        if not isinstance(vt, T.Opt):
            return go
        inner = self.spell(vt.inner)
        return (
            f"func(o rt.Opt[{self.spell(vt)}]) {self.spell(vt)} {{ if !o.Ok {{ return "
            f"rt.None[{inner}]() }}; return o.V }}({go})"
        )

    def elem_cursor(self, arg: n.Expr, el: T2Type) -> str:
        """A cursor of el elements over an iterable argument (charged as the IR says)."""
        src = self.elem(_strip_charge(arg).type)
        cur = self.cursor_of(arg)
        return cur if src == el else self.map_cursor(cur, src, el)

    def extend(self, r: str, arg: n.Expr) -> str:
        """l.extend(xs): a list, tuple, set or dict is copied first (CPython), anything
        else is appended element by element as it is produced."""
        inner = _strip_charge(arg)
        t = inner.type
        items = {
            T.ListT: "x.Items",
            T.SetT: "x.Items()",
            T.DictT: "x.Keys()",
            T.CounterT: "x.Keys()",
        }
        kind = type(t)
        if isinstance(arg, n.Bulk) and kind in items:
            charge = f"fuel.Bulk(int({self.length('x', t)}))"
            return (
                f"func(x {self.spell(t)}) struct{{}} {{ {charge}; "
                f"return {r}.Extend({items[kind]}) }}"
                f"({self.expr(inner)})"
            )
        return f"{r}.ExtendCur({self.cursor_of(arg)})"

    def str_method(self, name: str, r: str, args: list[str], c: n.Call) -> str:  # noqa: PLR0911
        """A Str method (Str.replace and Str.join are Sized: `sized`)."""
        simple = {
            "lower": "Lower", "upper": "Upper", "casefold": "Casefold", "title": "Title",
            "capitalize": "Capitalize", "splitlines": "StrSplitLines", "isdigit": "IsDigit",
            "isalpha": "IsAlpha", "isalnum": "IsAlnum", "isspace": "IsSpace",
            "islower": "IsLower", "isupper": "IsUpper",
        }  # fmt: skip
        if name in simple:
            return f"rt.{simple[name]}({r})"
        opt = [f"rt.PStr({a})" for a in args]
        match name:
            case "strip" | "lstrip" | "rstrip":
                fn = {"strip": "Strip", "lstrip": "LStrip", "rstrip": "RStrip"}[name]
                return f"rt.{fn}({r}, {opt[0] if args else 'nil'})"
            case "split" | "rsplit":
                fn = "StrSplit" if name == "split" else "StrRSplit"
                sep = opt[0] if args else "nil"
                limit = args[1] if len(args) > 1 else "-1"
                return f"rt.{fn}({r}, {sep}, {limit})"
            case "startswith" | "endswith":
                return f"rt.{'StartsWith' if name == 'startswith' else 'EndsWith'}({r}, {args[0]})"
            case "find" | "rfind" | "count":
                fn = {"find": "Find", "rfind": "RFind", "count": "CountStr"}[name]
                return f"rt.{fn}({r}, {args[0]})"
            case "partition" | "rpartition":
                tup = self.spell(c.type)
                fn = "Partition" if name == "partition" else "RPartition"
                return (
                    f"func(a, b, c string) {tup} {{ return {tup}{{a, b, c}} }}"
                    f"(rt.{fn}({r}, {args[0]}))"
                )
            case "zfill":
                return f"rt.ZFill({r}, {args[0]})"
            case "ljust" | "rjust" | "center":
                fn = {"ljust": "LJust", "rjust": "RJust", "center": "Center"}[name]
                return f"rt.{fn}({r}, {args[0]}, {opt[1] if len(args) > 1 else 'nil'})"
        raise LatticeInternalError(f"gogen: Str.{name} is not available on Go")

    def sized(self, e: n.Sized) -> str:
        """A size-checked call (emit_py's `<fuel>.call(<fuel>.<kind>)(...)`): its step
        is charged before the receiver and arguments are evaluated, its sizes after."""
        if e.kind == "pad":
            (width,) = e.args
            return f"rt.Width(fuel, {self.expr(width)})"
        (call,) = e.args
        if not isinstance(call, n.Call):
            raise LatticeInternalError("gogen: a Sized operation of a non-call")
        match e.kind, call.args:
            case "replace", (s, old, new, *count):
                k = self.expr(count[0]) if count else "-1"
                body = (
                    f"rt.SizedReplace(fuel, {self.expr(s)}, {self.expr(old)}, "
                    f"{self.expr(new)}, {k})"
                )
            case "join", (sep, items):
                if self.elem(items.type) != T.STR:
                    raise LatticeInternalError(f"gogen: join of {items.type.render()}")
                sep_go = self.expr(sep)
                body = f"rt.JoinStr(fuel, {sep_go}, {self.cursor_of(n.Bulk(items, items.type))})"
            case "sub", (n.Const(value=str() as pattern), n.Const(value=str() as repl), s):
                rx, tm = self.m.regex(pattern), self.m.template(pattern, repl)
                body = f"rt.ReSub(fuel, {rx}, {tm}, {self.expr(s)})"
            case "dumps", (value,):
                # the value, then the keywords in the written order, then the sizes
                val = self.temp()
                pre = [f"{val} := {self.conv(self.expr(value), value.type, T.JSON)}"]
                sort_keys, indent = "false", "nil"
                for k, v in call.kwargs:
                    tmp = self.temp()
                    if k == "sort_keys":
                        pre.append(f"{tmp} := {self.truth(self.expr(v), v.type)}")
                        sort_keys = tmp
                    else:
                        pre.append(f"{tmp} := {self.expr(v)}")
                        indent = f"&{tmp}"
                body = (
                    f"func() string {{ {'; '.join(pre)}; "
                    f"return rt.SizedDumps(fuel, {val}, {sort_keys}, {indent}) }}()"
                )
            case _:
                raise LatticeInternalError(f"gogen: {e.kind} is not available on Go")
        return f"rt.After(fuel.Step(), {body})"

    def call(self, c: n.Call) -> str:
        kind, _, name = c.target.partition(":")
        if kind == "proc":
            return self.proc_call(name, c)
        if kind == "record":
            return self.record(name, c)
        if kind == "builtin":
            return self.builtin(name, c)
        if kind == "std" and name.startswith("re."):
            return self.regex_call(name[3:], c)
        if kind == "std" and name.startswith("collections."):
            return self.collections_call(name.removeprefix("collections."), c)
        if kind == "std" and name.startswith("math."):
            return self.math_call(name.removeprefix("math."), c)
        if kind == "std" and name == "datetime.date":
            y, m, d = (self.expr(a) for a in c.args)
            return f"rt.NewDate({y}, {m}, {d})"
        if kind == "std" and name == "datetime.timedelta":
            return self.timedelta(dict(c.kwargs), [k for k, _ in c.kwargs])
        if kind == "helper":  # days(n), hours(n), minutes(n), seconds(n)
            return self.timedelta({name: c.args[0]}, [name])
        if kind == "std" and name == "json.loads":
            return f"rt.JsonLoads({self.expr(c.args[0])})"
        raise LatticeInternalError(f"gogen: call target {c.target!r} is not available on Go")

    def timedelta(self, parts: Mapping[str, n.Expr], written: list[str]) -> str:
        """timedelta(**parts): the arguments evaluated in the written order, then
        accumulated in CPython's (delta_new: seconds, minutes, hours, days, weeks)."""
        temps: dict[str, str] = {}
        pre: list[str] = []
        for unit in written:
            e = parts[unit]
            tmp = self.temp()
            ctor = "rt.DI" if e.type == T.INT else "rt.DF"
            pre.append(f"{tmp} := {ctor}({self.expr(e)})")
            temps[unit] = f"&{tmp}"
        order = ("seconds", "minutes", "hours", "days", "weeks")
        args = ", ".join(temps.get(u, "nil") for u in order)
        return f"func() rt.Duration {{ {'; '.join(pre)}; return rt.Timedelta({args}) }}()"

    def math_call(self, fn: str, c: n.Call) -> str:  # noqa: PLR0911
        """A `math` function (mathmodule.c's special cases, errors and rounding)."""
        args = c.args

        def f(i: int) -> str:
            return self.conv(self.expr(args[i]), args[i].type, T.FLOAT)

        match fn:
            case "floor" | "ceil" | "trunc":
                if args[0].type == T.INT:
                    return self.expr(args[0])  # int.__floor__: the Int itself
                return f"rt.Math{fn.capitalize()}({self.expr(args[0])})"
            case "sqrt" | "exp" | "fabs":
                return f"rt.Math{fn.capitalize()}({f(0)})"
            case "log" | "log10":
                logs = []
                for a in args:
                    suffix = "I" if a.type == T.INT else ""
                    name = "Log" if fn == "log" or logs else "Log10"
                    logs.append(f"rt.Math{name}{suffix}({self.expr(a)})")
                if len(logs) == 1:
                    return logs[0]
                return f"rt.MathLogBase({logs[0]}, {logs[1]})"
            case "pow":
                return f"rt.MathPow({f(0)}, {f(1)})"
            case "isclose":
                return f"rt.MathIsClose({f(0)}, {f(1)})"
            case "gcd":
                return f"rt.MathGcd({self.expr(args[0])}, {self.expr(args[1])})"
        raise LatticeInternalError(f"gogen: math.{fn} is not available on Go")

    def collections_call(self, fn: str, c: n.Call) -> str:
        """collections.Counter(...) and collections.defaultdict(factory)."""
        t = c.type
        if fn == "Counter":
            assert isinstance(t, T.CounterT)
            k = self.spell(t.key)
            if not c.args:
                return f"rt.NewCounter[{k}]()"
            (arg,) = c.args
            src = _strip_charge(arg)
            if isinstance(src.type, T.DictT | T.CounterT):  # a mapping of counts
                charge = "fuel.Bulk(int(m.Len()))" if isinstance(arg, n.Bulk) else ""
                return (
                    f"func(m {self.spell(src.type)}) {self.spell(t)} {{ {charge}; "
                    f"return rt.CounterOfDict(m) }}({self.expr(src)})"
                )
            return f"rt.CounterOf({self.elem_cursor(arg, t.key)})"
        if fn == "defaultdict":
            assert isinstance(t, T.DictT)
            (factory,) = c.args
            if not isinstance(factory, n.Var):
                raise LatticeInternalError("gogen: defaultdict of a non-factory")
            v = self.spell(t.val)
            make = {
                "list": lambda: f"rt.NewList[{self.spell(self.elem(t.val))}]()",
                "set": lambda: f"rt.NewSet[{self.spell(self.elem(t.val))}]()",
                "int": lambda: "int64(0)",
                "float": lambda: "float64(0)",
                "str": lambda: '""',
            }[factory.name]()
            k = self.spell(t.key)
            return (
                f"rt.NewDefaultDict[{k}, {v}](func() {v} {{ return {make} }}, "
                f"{go_string(factory.name)})"
            )
        raise LatticeInternalError(f"gogen: collections.{fn} is not available on Go")

    def regex_call(self, fn: str, c: n.Call) -> str:
        """re.fullmatch / match / search / findall / split (re.sub is Sized): the
        pattern is a literal, compiled once into a package-level var."""
        funcs = {
            "fullmatch": "ReFullmatch", "match": "ReMatch", "search": "ReSearch",
            "findall": "ReFindall", "split": "ReSplit",
        }  # fmt: skip
        match c.args:
            case (n.Const(value=str() as pattern), s) if fn in funcs:
                rx, text = self.m.regex(pattern), self.expr(s)
                elem = self.elem(c.type) if fn in ("findall", "split") else None
                if fn == "findall" and isinstance(elem, T.TupleT):
                    # a pattern of several groups: a tuple of them per match
                    tup = self.spell(elem)
                    items = ", ".join(f"g[{i}]" for i in range(len(elem.elems)))
                    return (
                        f"func(gs [][]string) *rt.List[{tup}] {{ out := rt.NewList[{tup}](); "
                        f"for _, g := range gs {{ out.Append({tup}{{{items}}}) }}; return out }}"
                        f"(rt.ReFindallGroups({rx}, {text}))"
                    )
                if fn == "split" and isinstance(elem, T.Opt):
                    return f"rt.ReSplitGroups({rx}, {text})"  # the groups too, None unmatched
                return f"rt.{funcs[fn]}({rx}, {text})"
        raise LatticeInternalError(f"gogen: re.{fn} is not available on Go")

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
                src = _strip_charge(args[0])
                if name == "set" and isinstance(src.type, T.DictT) and self.elem(src.type) == el:
                    # T2Set(d) is dict.fromkeys(d): presized for an exact dict
                    charge = "fuel.Bulk(int(d.Len())); " if isinstance(args[0], n.Bulk) else ""
                    return (
                        f"func(d {self.spell(src.type)}) {self.spell(t)} {{ {charge}"
                        f"return rt.SetOfDict(d) }}({self.expr(src)})"
                    )
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


def _mismatch(a: T2Type, b: T2Type) -> bool:
    """a and b differ by an Int where the other has a Float, at some depth (a tuple's
    items, an optional value): such values compare exactly, never converted."""
    if {a, b} == {T.INT, T.FLOAT}:
        return True
    match a, b:
        case T.Opt(inner=x), T.Opt(inner=y):
            return _mismatch(x, y)
        case T.Opt(inner=x), _:
            return _mismatch(x, b)
        case _, T.Opt(inner=y):
            return _mismatch(a, y)
        case T.TupleT(elems=xs), T.TupleT(elems=ys):
            return any(_mismatch(x, y) for x, y in zip(xs, ys, strict=False))
    return False


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


def emit_go_t2(module: n.T2Module, package: str = "main", types: GoTypes | None = None) -> str:
    """The Go source of every procedure, record and module constant of `module`, as
    one file of package `package`. `types` (with the API profile's tables and Caller,
    `lattice.gogen.types.api_types`) spells Row and Caller values; the declarations it
    collects -- also the ones spelled for the endpoint handlers before -- are in the
    file."""
    mod = _Module(module, types)
    procs = [_Proc(mod, p).function() for p in module.procedures]
    consts: list[str] = []
    for name, t, value in module.constants:
        e = _Proc(mod, None)
        consts.append(f"var C_{name} {mod.types.spell(t)} = {e.conv(e.expr(value), value.type, t)}")
    for rec in module.records:
        mod.types.record_struct(rec.name)
    body = "\n".join([mod.types.decls(), *mod.decls, *consts, "", *procs])
    if "math." in body:
        mod.imports.add("math")
    imports = "\n".join(f'\t"{i}"' for i in sorted(mod.imports))
    head = "// Code generated by lattice (lattice/gogen/t2.py). DO NOT EDIT.\n\n"
    return f"{head}package {package}\n\nimport (\n{imports}\n)\n\n{body}"
