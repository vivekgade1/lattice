"""Fuel-instrumented compilation of checked procedures (spec 1c §4).

`compile_t2(program)` turns the T2 part of a checked API program into the source of one
Python module, run by `lattice.t2.support.t2_load` in a restricted namespace (no
ambient builtins). Only the *checked* AST is compiled, and every call is compiled from
what the checker resolved it to (`CheckedProcedure.targets`):

* a preamble binds exactly the names procedures may reach: the allowlisted builtins
  and errors, the Duration helpers, the imported stdlib names (a module becomes a
  namespace holding only its allowlisted names), the module constants procedures read,
  and each `@record` as a frozen dataclass;
* each procedure becomes `def name(<fuel>, params...)` without decorators or
  annotations, whose body is `<fuel>.enter()` (one step, one level of depth) and the
  original body inside `try: ... finally: <fuel>.leave()`;
* fuel is charged at every `for`/`while` iteration (`<fuel>.tick()` first in the body),
  every comprehension element (the iterable becomes `<fuel>.each(...)`), every call that
  is not a procedure call (`<fuel>.call(f)(args)`: charged, then called), and every
  element a builtin consumes (`sum(xs)` becomes `sum(<fuel>.bulk(xs))`: a sized
  argument is charged its length up front);
* a procedure call passes the fuel on: `helper(<fuel>, x)`;
* size is charged too: an operation whose result grows with an operand (`+`, `*`,
  `**`, `<<` and their augmented forms, `ljust`/`rjust`/`center`/`zfill`, `replace`,
  `join`, format specs, `re.sub`, `json.dumps`) is compiled into a fuel method that
  computes the result's size first, refuses a value larger than the cap (R622) and
  charges it; a regular expression is charged its input's length.

The same source serves the runtime (`lattice.api.runtime`) and the generated app
(`lattice.api.codegen`), so fuel accounting is identical in both.
"""

from __future__ import annotations

import ast
import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from lattice.catalog import T2_STDLIB
from lattice.check import CheckedProgram
from lattice.check.astutil import is_constant_expr, is_list_constant_assign
from lattice.check.t2 import CheckedProcedure, T2Program
from lattice.runtime import LatticeInternalError
from lattice.t2.support import T2_BUILTIN_NAMES

__all__ = ["T2Code", "compile_program", "compile_t2"]

# Arguments a builtin / method consumes whole: target -> positions charged per element.
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
# Operators whose result can grow with an operand: `a op b` becomes `<fuel>.<name>(a, b)`
# (charged the result's size before it is computed; see T2Fuel.grow / bits).
_GROWING_OPS: Mapping[type, str] = {
    ast.Add: "add",
    ast.Mult: "mul",
    ast.Pow: "pow",
    ast.LShift: "lshift",
}
# Methods padding their receiver to a width (the first argument).
_PADDING = frozenset(
    {"method:Str.ljust", "method:Str.rjust", "method:Str.center", "method:Str.zfill"}
)
# Regular expressions: the input (second argument) is charged its length.
_REGEX_SCANS = frozenset(
    {"std:re.fullmatch", "std:re.match", "std:re.search", "std:re.findall", "std:re.split"}
)
# Calls replaced by a fuel method taking the receiver (a method) or the function (a
# std function) first: it computes the result's size before calling.
_SIZED_METHODS: Mapping[str, str] = {"method:Str.replace": "replace", "method:Str.join": "join"}
_SIZED_FUNCTIONS: Mapping[str, str] = {"std:re.sub": "sub", "std:json.dumps": "dumps"}
_HELPERS = ("days", "hours", "minutes", "seconds")
_FUEL = "_lt_fuel"


@dataclass(frozen=True)
class T2Code:
    """The compiled T2 part of a program: the module `source`, the procedures it
    defines with their parameter names in order, and the natives they call."""

    source: str
    procedures: tuple[tuple[str, tuple[str, ...]], ...]
    natives: tuple[str, ...]

    def params(self, name: str) -> tuple[str, ...]:
        return dict(self.procedures)[name]


def compile_program(program: CheckedProgram) -> T2Code | None:
    """The compiled T2 part of a checked program (None when it has no procedures or
    records). Module constants procedures read come from the program's source (list
    constants are gone from the checked T0 module)."""
    if program.t2 is None:
        return None
    return compile_t2(program.t2, ast.parse(program.source))


def compile_t2(t2: T2Program, module: ast.Module | None = None) -> T2Code:
    """The instrumented source of every procedure and record of `t2`. `module` is the
    program's parsed source (the constants procedures read are copied from it)."""
    fuel = _fresh_fuel_name(t2)
    lines: list[str] = ["# preamble: the names procedures may reach"]
    for name in T2_BUILTIN_NAMES:
        lines.append(f"{name} = t2_builtin({name!r})")
    for unit in _HELPERS:
        lines.append(f"{unit} = t2_helper({unit!r})")
    for local, qual in t2.imports.items():
        if "." in qual:
            mod, attr = qual.split(".", 1)
            lines.append(f"{local} = t2_std({mod!r}, {attr!r})")
        else:
            names = tuple(sorted(T2_STDLIB[qual]))
            lines.append(f"{local} = t2_std_module({qual!r}, {names!r})")
    for name, value in _constants(t2, module):
        lines.append(f"{name} = {ast.unparse(value)}")
    for rec in t2.records.values():
        fields = tuple(f for f, _ in rec.fields)
        lines.append(f"{rec.name} = t2_record({rec.name!r}, {fields!r}, {dict(rec.defaults)!r})")
    procs: list[tuple[str, tuple[str, ...]]] = []
    natives: set[str] = set()
    for proc in t2.procedures.values():
        fn = _Instrument(proc, fuel).function()
        lines += ["", "", ast.unparse(fn)]
        procs.append((proc.name, tuple(p for p, _ in proc.sig.params)))
        natives |= proc.natives
    source = "\n".join(lines) + "\n"
    compile(source, "<lattice-procedures>", "exec")  # a compile error is a Lattice bug
    return T2Code(source, tuple(procs), tuple(sorted(natives)))


def _constants(t2: T2Program, module: ast.Module | None) -> Iterable[tuple[str, ast.expr]]:
    """The module constants procedures may read, in source order."""
    if module is None or not t2.constants:
        return []
    out: list[tuple[str, ast.expr]] = []
    for stmt in module.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id in t2.constants
            and (is_constant_expr(stmt.value) or is_list_constant_assign(stmt))
        ):
            out.append((stmt.targets[0].id, stmt.value))
    return out


def _fresh_fuel_name(t2: T2Program) -> str:
    """The fuel parameter's name: one no procedure uses."""
    used: set[str] = set(t2.records) | set(t2.imports) | set(t2.constants)
    for proc in t2.procedures.values():
        used |= {n.id for n in ast.walk(proc.node) if isinstance(n, ast.Name)}
        used |= {a.arg for a in ast.walk(proc.node) if isinstance(a, ast.arg)}
    name, n = _FUEL, 2
    # <fuel>_a1, ... are the augmented assignments' temporaries: no name may start so
    while name in used or any(u.startswith(name + "_") for u in used):
        name, n = f"{_FUEL}{n}", n + 1
    return name


class _Instrument(ast.NodeTransformer):
    """One procedure's checked AST -> its instrumented function (a copy; the checked
    tree is never changed)."""

    def __init__(self, proc: CheckedProcedure, fuel: str) -> None:
        self.proc = proc
        self.fuel = fuel
        self.temps = 0  # augmented assignments' temporaries: <fuel>_a1, <fuel>_a2, ...
        memo: dict[int, object] = {}
        self.fn: ast.FunctionDef = copy.deepcopy(proc.node, memo)
        # the checker's call targets are keyed by the original nodes: re-key them
        self.targets: dict[int, str] = {}
        for key, target in proc.targets.items():
            new = memo.get(key)
            if new is not None:
                self.targets[id(new)] = target

    def _fuel(self, method: str, *args: ast.expr) -> ast.Call:
        func = ast.Attribute(ast.Name(self.fuel, ast.Load()), method, ast.Load())
        return ast.Call(func, list(args), [])

    def _attr(self, method: str) -> ast.Attribute:
        return ast.Attribute(ast.Name(self.fuel, ast.Load()), method, ast.Load())

    def _stmt(self, method: str) -> ast.stmt:
        return ast.Expr(self._fuel(method))

    def function(self) -> ast.FunctionDef:
        fn = self.fn
        flat: list[ast.stmt] = [self.visit(s) for s in fn.body]
        args = fn.args
        for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            a.annotation = None
        args.args.insert(0, ast.arg(self.fuel))
        guarded = ast.Try(body=flat or [ast.Pass()], handlers=[], orelse=[], finalbody=[])
        guarded.finalbody = [self._stmt("leave")]
        new = ast.FunctionDef(
            name=fn.name,
            args=args,
            body=[self._stmt("enter"), guarded],
            decorator_list=[],
            returns=None,
            type_comment=None,
            type_params=[],
        )
        return ast.fix_missing_locations(ast.copy_location(new, fn))

    # ---- statements ---------------------------------------------------------------

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.stmt:
        if node.value is None:
            return ast.copy_location(ast.Pass(), node)  # a declaration only: nothing runs
        value = self.visit(node.value)
        target = self.visit(node.target)
        return ast.copy_location(ast.Assign([target], value), node)

    def visit_For(self, node: ast.For) -> ast.For:
        self.generic_visit(node)
        node.body.insert(0, self._stmt("tick"))
        return node

    def visit_While(self, node: ast.While) -> ast.While:
        self.generic_visit(node)
        node.body.insert(0, self._stmt("tick"))
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        raise LatticeInternalError("a nested def in a checked procedure")

    def _temp(self) -> str:
        self.temps += 1
        return f"{self.fuel}_a{self.temps}"  # the fuel name is fresh, so are these

    def visit_AugAssign(self, node: ast.AugAssign) -> ast.stmt | list[ast.stmt]:
        """`t op= v` for a growing op: the operands evaluated once into temporaries,
        the size checked (`<fuel>.aug(op, t, v)`), then the augmented assignment itself
        (so `xs += ys` stays in place)."""
        self.generic_visit(node)
        name = _GROWING_OPS.get(type(node.op))
        if name is None:
            return node
        stmts: list[ast.stmt] = []

        def bind(value: ast.expr) -> ast.Name:
            tmp = self._temp()
            stmts.append(ast.Assign([ast.Name(tmp, ast.Store())], value))
            return ast.Name(tmp, ast.Load())

        target = node.target
        if isinstance(target, ast.Subscript):
            container = bind(target.value)
            index = target.slice
            key: ast.expr
            if isinstance(index, ast.Slice):  # xs[a:b] += ...: each bound once
                parts = (index.lower, index.upper, index.step)
                key = ast.Slice(*(None if p is None else bind(p) for p in parts))
            else:
                key = bind(index)
            target = ast.Subscript(container, key, ast.Store())
        value = bind(node.value)
        current = copy.copy(target)
        current.ctx = ast.Load()
        check = self._fuel("aug", ast.Constant(name), current, value)
        stmts.append(ast.Expr(check))
        stmts.append(ast.AugAssign(target, node.op, value))
        for st in stmts:
            ast.copy_location(st, node)
        return stmts

    # ---- expressions ----------------------------------------------------------------

    def visit_comprehension(self, node: ast.comprehension) -> ast.comprehension:
        self.generic_visit(node)
        node.iter = self._fuel("each", node.iter)
        return node

    def visit_BinOp(self, node: ast.BinOp) -> ast.expr:
        self.generic_visit(node)
        name = _GROWING_OPS.get(type(node.op))
        if name is None:
            return node
        return ast.copy_location(self._fuel(name, node.left, node.right), node)

    def visit_FormattedValue(self, node: ast.FormattedValue) -> ast.FormattedValue:
        """`f"{v!c:spec}"` with a spec: `f"{<fuel>.fmt(v, spec, c)}"` (the spec's width
        and precision charged and capped before formatting)."""
        self.generic_visit(node)
        if node.format_spec is None:
            return node
        spec = node.format_spec
        call = self._fuel("fmt", node.value, spec, ast.Constant(node.conversion))
        return ast.copy_location(ast.FormattedValue(call, -1, None), node)

    def visit_Call(self, node: ast.Call) -> ast.expr:
        target = self.targets.get(id(node))
        self.generic_visit(node)
        if target is not None and target.startswith("proc:"):
            node.args.insert(0, ast.Name(self.fuel, ast.Load()))
            return node
        if target in _SIZED_METHODS and isinstance(node.func, ast.Attribute):
            method = self._fuel("call", self._attr(_SIZED_METHODS[target]))
            return ast.copy_location(ast.Call(method, [node.func.value, *node.args], []), node)
        if target in _SIZED_FUNCTIONS:
            method = self._fuel("call", self._attr(_SIZED_FUNCTIONS[target]))
            call = ast.Call(method, [node.func, *node.args], node.keywords)
            return ast.copy_location(call, node)
        if target in _PADDING and node.args:
            node.args[0] = self._fuel("width", node.args[0])
        if target in _REGEX_SCANS and len(node.args) > 1:
            node.args[1] = self._fuel("scan", node.args[1])
        positions = _CONSUMERS.get(target or "", ())
        if target in _ONE_ARG_ONLY and len(node.args) != 1:
            positions = ()
        for i in positions:
            if i < len(node.args):
                # a sized value passes unchanged (charged its length): Counter(d) of a
                # dict and xs.extend(xs) keep their meaning
                node.args[i] = self._fuel("bulk", node.args[i])
        node.func = self._fuel("call", node.func)
        return node
