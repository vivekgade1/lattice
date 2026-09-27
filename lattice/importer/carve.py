"""R3 of the close-the-gap loop (spec 2026-09-27 importer §3.4): **carve-out**.

A carve-out keeps a route in Lattice and moves only the inexpressible piece into a small
typed native called from Lattice. This module is the extension point:

* a :class:`CarveOut` strategy says which natives a route may use
  (:meth:`CarveOut.natives`) -- or why it has none (then R3 is skipped with the reason);
* :func:`carve_profile_source` appends the natives to the generated profile's source, so
  the profile a carve-out is verified against is exactly the one written to
  ``lattice_import/profile.py``.

Two strategies:

* :class:`ProcedureCarveOut` (the default, I5b): the route becomes T0 plus T2
  ``@procedure`` functions holding the logic T0 cannot express. It offers the ladder *plans*,
  tried in order: first a **procedure** plan -- the slice's helpers that are already in
  the T2 subset (pure, typed, allowlisted stdlib) carved mechanically by
  :func:`carve_procedure` (their AST copied into a ``@procedure``, annotations
  translated, no LLM), plus the helpers the LLM must translate itself -- then a
  **native** plan, offered only when a helper needs something outside the T2 subset
  (I/O, a third-party or non-allowlisted module, effects, dynamic dispatch): typed
  natives over exactly those helpers (as :class:`NativeCarveOut` builds them).
* :class:`NativeCarveOut` (I5): the simple case only -- a package helper the route's
  slice reaches, with scalar parameters and result, becomes a typed native wrapping the
  original function (the body is referenced, never copied). Its effect flag comes from
  the helper's own slice (the graph's effect heuristic over every function it reaches):
  an effectful helper is declared ``effect=True`` and must return ``None`` (Lattice runs
  effects through ``for_each`` after commit); an effectful helper with a result is not
  simple and is skipped -- never declared pure.

A carved procedure is never trusted on its own: the route's candidate that calls it
passes the checker and ``verify()`` against the original like every other candidate.
"""

from __future__ import annotations

import ast
import copy
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from lattice.catalog import T2_BUILTINS, T2_ERRORS, T2_HELPERS, T2_STDLIB, T2_TYPE_ALIASES
from lattice.importer.graph import CodeGraph, Slice, route_slice

if TYPE_CHECKING:
    from lattice.profile import Profile

__all__ = [
    "CarveKind",
    "CarveNative",
    "CarveOut",
    "CarvePlan",
    "CarvedProcedure",
    "HelperSource",
    "NativeCarveOut",
    "PlannedCarveOut",
    "ProcedureCarveOut",
    "carve_procedure",
    "carve_profile_source",
    "procedure_program",
    "program_carve",
    "with_procedures",
]

SKIP_TODO = "a native carve-out needs a helper with scalar parameters and result"
SCALARS = {
    "str": ("STR", "str"),
    "int": ("INT", "int"),
    "float": ("FLOAT", "float"),
    "bool": ("BOOL", "bool"),
    "UUID": ("UUID", "uuid.UUID"),
    "uuid.UUID": ("UUID", "uuid.UUID"),
    "datetime": ("DATETIME", "datetime.datetime"),
    "datetime.datetime": ("DATETIME", "datetime.datetime"),
    "date": ("DATE", "datetime.date"),
    "datetime.date": ("DATE", "datetime.date"),
}
LATTICE_TYPES = {
    "STR": "Str",
    "INT": "Int",
    "FLOAT": "Float",
    "BOOL": "Bool",
    "UUID": "Uuid",
    "DATETIME": "DateTime",
    "DATE": "Date",
}


@dataclass(frozen=True)
class CarveNative:
    """A typed native wrapping ``target`` (``module:function``): ``params`` are (name,
    Scalar member name); ``result`` a Scalar member name or None (an effect)."""

    name: str
    target: str
    params: tuple[tuple[str, str], ...]
    result: str | None
    effect: bool = False
    nullable: bool = False
    effect_reason: str = ""

    def signature(self) -> str:
        """As the LLM sees it: ``native.carve_slugify(title: Str) -> Str``."""
        args = ", ".join(f"{n}: {LATTICE_TYPES[t]}" for n, t in self.params)
        if self.effect:
            return f"native.{self.name}({args}) -- an effect: call it only via for_each"
        res = LATTICE_TYPES[self.result] if self.result else "None"
        return f"native.{self.name}({args}) -> {res}{' | null' if self.nullable else ''}"

    def sig_source(self) -> str:
        params = "".join(f"Scalar.{t}, " for _, t in self.params).rstrip(", ")
        parts = [f"({params},)" if len(self.params) == 1 else f"({params})"]
        if self.result is not None:
            parts.append(f"Scalar.{self.result}")
        if self.nullable:
            parts.append("nullable=True")
        if self.effect:
            parts.append("effect=True")
        return f"NativeSig({', '.join(parts)})"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "target": self.target,
            "params": [list(p) for p in self.params],
            "result": self.result,
            "effect": self.effect,
            "nullable": self.nullable,
            "effect_reason": self.effect_reason,
        }


class CarveOut(Protocol):
    """An R3 strategy: the natives ``route`` (``module:function``) may use, or ``()``
    and why none."""

    def natives(
        self, graph: CodeGraph, route: str, known: Collection[str]
    ) -> tuple[Sequence[CarveNative], str]: ...


def _annotation(node: ast.expr | None) -> tuple[str, bool] | None:
    """(Scalar member, nullable) of a scalar annotation; None when not scalar."""
    if node is None:
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        for a, b in ((node.left, node.right), (node.right, node.left)):
            if isinstance(b, ast.Constant) and b.value is None:
                got = _annotation(a)
                return (got[0], True) if got else None
        return None
    if isinstance(node, ast.Subscript) and ast.unparse(node.value) in (
        "Optional",
        "typing.Optional",
    ):
        got = _annotation(node.slice)
        return (got[0], True) if got else None
    text = ast.unparse(node)
    if text in SCALARS:
        return SCALARS[text][0], False
    return None


class NativeCarveOut:
    """The simple carve-out (see the module docstring)."""

    prefix = "carve_"

    def natives(
        self, graph: CodeGraph, route: str, known: Collection[str]
    ) -> tuple[Sequence[CarveNative], str]:
        s = route_slice(graph, route)
        found: list[CarveNative] = []
        skipped: list[str] = []
        for q in s.functions:
            if q == s.route or q in s.dependencies or q in known:
                continue
            sym = graph.symbols[q]
            if sym.kind != "function":
                continue
            native, why = self.native(graph, q)
            if native is None:
                skipped.append(f"{q}: {why}")
            else:
                found.append(native)
        if found:
            return tuple(found), ""
        detail = "; ".join(skipped) if skipped else "the slice reaches no helper function"
        return (), f"no simple helper to carve out ({detail}). {SKIP_TODO}"

    def native(self, graph: CodeGraph, qual: str) -> tuple[CarveNative | None, str]:
        sym = graph.symbols[qual]
        fn = _function(graph, qual)
        if fn is None:
            return None, "source not found"
        if fn.args.vararg or fn.args.kwarg or fn.args.kwonlyargs:
            return None, "variadic or keyword-only parameters"
        params: list[tuple[str, str]] = []
        for a in (*fn.args.posonlyargs, *fn.args.args):
            got = _annotation(a.annotation)
            if got is None or got[1]:
                return None, f"parameter {a.arg!r} is not a required scalar"
            params.append((a.arg, got[0]))
        if fn.args.defaults:
            return None, "parameters with defaults"
        returns = fn.returns
        none_result = returns is None or (
            isinstance(returns, ast.Constant) and returns.value is None
        )
        result = None if none_result else _annotation(returns)
        if not none_result and result is None:
            return None, "the result is not a scalar"
        own = route_slice(graph, qual)
        if own.dynamic:
            return None, "dynamic dispatch in the helper"
        effects = "; ".join(f"{f}: {why}" for f, why in own.effects)
        if effects and not none_result:
            return None, f"effectful ({effects}) and returns a value"
        if not effects and none_result:
            return None, "no result and no effect"
        return (
            CarveNative(
                name=self.prefix + sym.name.replace(".", "_"),
                target=qual,
                params=tuple(params),
                result=None if result is None else result[0],
                effect=bool(effects),
                nullable=bool(result and result[1]),
                effect_reason=effects,
            ),
            "",
        )


def _function(graph: CodeGraph, qual: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    sym = graph.symbols[qual]
    lines = graph.sources[sym.module].splitlines()
    text = "\n".join(lines[sym.line - 1 : sym.end_line])
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node
    return None


def carve_profile_source(base: str, natives: Sequence[CarveNative]) -> str:
    """``base`` (a generated profile's source) with ``natives`` added: a wrapper each,
    registered in ``NATIVES``, and ``profile()`` extended with their signatures."""
    if not natives:
        return base
    out = [
        base.rstrip("\n"),
        "",
        "",
        "# ---- carve-out natives (lattice.importer R3): typed wrappers over original helpers ----",
        "",
        "import importlib as _carve_importlib",
        "",
    ]
    for n in natives:
        module, _, func = n.target.partition(":")
        args = ", ".join(p for p, _ in n.params)
        out += [
            "",
            f"def {n.name}({args}):",
            f'    """Wraps {n.target} (R3 carve-out; '
            f'{"effect: " + n.effect_reason if n.effect else "pure"})."""',
            f"    return _carve_importlib.import_module({module!r}).{func}({args})",
            "",
            "",
            f"NATIVES[{n.name!r}] = {n.name}",
        ]
    out += [
        "",
        "_generated_profile = profile",
        "",
        "",
        "def profile() -> Profile:",
        '    """The generated profile with the carve-out natives."""',
        "    import dataclasses as _dc",
        "",
        "    p = _generated_profile()",
        "    assert p.api is not None",
        "    natives = dict(p.api.natives)",
    ]
    out += [f"    natives[{n.name!r}] = {n.sig_source()}" for n in natives]
    out += ["    return _dc.replace(p, api=_dc.replace(p.api, natives=natives))", ""]
    return "\n".join(out)


def used_natives(source: str, natives: Sequence[CarveNative]) -> tuple[CarveNative, ...]:
    """The carve-out natives a Lattice source calls (``native.<name>``)."""
    names = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "native"
        ):
            names.add(node.attr)
    return tuple(n for n in natives if n.name in names)


def slice_of(graph: CodeGraph, route: str) -> Slice:
    return route_slice(graph, route)


# ---- T2 procedure carve-out (I5b) ------------------------------------------------------------

CarveKind = Literal["procedure", "native"]
HEADER = "# lattice: v1\n"
PY_TYPES = {
    "UUID": "Uuid",
    "uuid.UUID": "Uuid",
    "datetime.datetime": "DateTime",
    "datetime.date": "Date",
    "datetime.timedelta": "Duration",
    "Decimal": "Float",
}
T2_NAMES = frozenset({"Int", "Float", "Str", "Bool", "Date", "DateTime", "Duration", "Uuid"})
GENERICS = {
    "list": "list", "List": "list", "typing.List": "list",
    "dict": "dict", "Dict": "dict", "typing.Dict": "dict",
    "set": "set", "Set": "set", "typing.Set": "set",
    "tuple": "tuple", "Tuple": "tuple", "typing.Tuple": "tuple",
}  # fmt: skip
AMBIENT = frozenset({*T2_BUILTINS, *T2_HELPERS, *T2_ERRORS})


@dataclass(frozen=True)
class CarvedProcedure:
    """A helper carved mechanically into a ``@procedure``: ``source`` is the procedure;
    ``support`` the imports and constants it reads (Lattice statements); ``calls`` the
    other carved procedures it calls (by name)."""

    name: str
    origin: str
    source: str
    support: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()

    @property
    def lines(self) -> int:
        return len(self.source.strip().splitlines())

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "origin": self.origin, "lines": self.lines,
                "support": list(self.support), "calls": list(self.calls)}  # fmt: skip


@dataclass(frozen=True)
class HelperSource:
    """A helper within reach of T2 that the LLM must translate (``why`` it was not
    carved mechanically)."""

    origin: str
    source: str
    why: str


@dataclass(frozen=True)
class CarvePlan:
    """One R3 attempt kind: ``procedure`` (T2: mechanical procedures offered, helpers to
    translate) or ``native`` (typed natives over helpers outside the T2 subset)."""

    kind: CarveKind
    procedures: tuple[CarvedProcedure, ...] = ()
    translate: tuple[HelperSource, ...] = ()
    natives: tuple[CarveNative, ...] = ()
    why: str = ""


class PlannedCarveOut(Protocol):
    """An R3 strategy offering plans in the order the ladder tries them, and a note on
    what it could not offer."""

    def plans(
        self, graph: CodeGraph, route: str, known: Collection[str], profile: Profile
    ) -> tuple[Sequence[CarvePlan], str]: ...


def _t2_annotation(node: ast.expr | None) -> ast.expr | None:
    """A Python annotation in the T2 type language, or None when it has no T2 type."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and node.value is None:
        return ast.Constant(None)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _t2_annotation(ast.parse(node.value, mode="eval").body)
        except SyntaxError:
            return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _t2_annotation(node.left), _t2_annotation(node.right)
        return None if left is None or right is None else ast.BinOp(left, ast.BitOr(), right)
    text = ast.unparse(node)
    if isinstance(node, ast.Name | ast.Attribute):
        if text in T2_NAMES:
            return ast.Name(text)
        name = T2_TYPE_ALIASES.get(text) or PY_TYPES.get(text)
        return ast.Name(name) if name else None
    if isinstance(node, ast.Subscript):
        base = ast.unparse(node.value)
        if base in ("Optional", "typing.Optional"):
            inner = _t2_annotation(node.slice)
            return None if inner is None else ast.BinOp(inner, ast.BitOr(), ast.Constant(None))
        if base in GENERICS:
            parts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            args = [_t2_annotation(p) for p in parts]
            if any(a is None for a in args):
                return None
            sl: ast.expr = ast.Tuple(args, ast.Load()) if len(args) > 1 else args[0]  # type: ignore[arg-type]
            return ast.Subscript(ast.Name(GENERICS[base]), sl, ast.Load())
    return None


@dataclass
class _ModuleNames:
    imports: dict[str, tuple[str, str | None, str, int]] = field(default_factory=dict)
    constants: dict[str, tuple[str, int]] = field(default_factory=dict)
    functions: set[str] = field(default_factory=set)


def _module_names(graph: CodeGraph, module: str) -> _ModuleNames:
    """A module's top-level imports (local -> (module, attr, statement, line)),
    literal constants (name -> (statement, line)) and function names."""
    out = _ModuleNames()
    try:
        tree = ast.parse(graph.sources[module])
    except (KeyError, SyntaxError):
        return out
    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for a in stmt.names:
                local = a.asname or a.name.split(".")[0]
                text = f"import {a.name}" + (f" as {a.asname}" if a.asname else "")
                out.imports[local] = (a.name if a.asname else local, None, text, stmt.lineno)
        elif isinstance(stmt, ast.ImportFrom) and stmt.level == 0 and stmt.module:
            for a in stmt.names:
                local = a.asname or a.name
                text = f"from {stmt.module} import {a.name}" + (
                    f" as {a.asname}" if a.asname else ""
                )
                out.imports[local] = (stmt.module, a.name, text, stmt.lineno)
        elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            out.functions.add(stmt.name)
        elif isinstance(stmt, ast.Assign | ast.AnnAssign) and stmt.value is not None:
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            if len(targets) == 1 and isinstance(targets[0], ast.Name):
                try:
                    ast.literal_eval(stmt.value)
                except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                    continue
                name = targets[0].id
                out.constants[name] = (f"{name} = {ast.unparse(stmt.value)}", stmt.lineno)
    return out


def _free_names(fn: ast.FunctionDef) -> list[str]:
    """Names the body reads that it does not bind (annotations excluded), in order."""
    skip: set[int] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.AnnAssign):
            skip |= {id(n) for n in ast.walk(node.annotation)}
    bound = {a.arg for a in ast.walk(fn) if isinstance(a, ast.arg)}
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    out: list[str] = []
    for stmt in fn.body:
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and id(node) not in skip
                and node.id not in bound
                and node.id not in AMBIENT
                and node.id not in out
            ):
                out.append(node.id)
    return out


def _outside_reason(graph: CodeGraph, qual: str) -> str:
    own = route_slice(graph, qual)
    if own.dynamic:
        return "dynamic dispatch: " + "; ".join(f"{f.function}: {f.kind}" for f in own.dynamic)
    if own.effects:
        return "effectful (" + "; ".join(f"{f}: {why}" for f, why in own.effects) + ")"
    return ""


def carve_procedure(graph: CodeGraph, qual: str) -> tuple[CarvedProcedure | None, str, bool]:
    """``qual`` (``module:function``) carved mechanically into a ``@procedure``: its AST
    copied, its annotations translated to the T2 type language, the allowlisted imports
    and literal constants it reads carried as support; no LLM. Returns (procedure, "",
    False), or (None, why, outside): ``outside`` when the helper needs something outside
    the T2 subset (effects, dynamic dispatch, a non-allowlisted module) -- a native's
    job; otherwise the LLM may still translate it. The result is not yet checked: the
    caller checks it (:func:`procedure_program`) against the profile."""
    sym = graph.symbols.get(qual)
    if sym is None or sym.kind != "function":
        return None, "not a module-level function", False
    fn = _function(graph, qual)
    if fn is None:
        return None, "source not found", False
    if isinstance(fn, ast.AsyncFunctionDef):
        return None, "async (outside the T2 subset)", True
    outside = _outside_reason(graph, qual)
    if outside:
        return None, outside, True
    names = _module_names(graph, sym.module)
    support: list[tuple[int, str]] = []
    calls: list[str] = []
    for name in _free_names(fn):
        if name in names.imports:
            module, attr, text, line = names.imports[name]
            root = module.split(".")[0]
            if module in T2_STDLIB:
                support.append((line, text))
            elif root == graph.package.split(".")[0] and attr is not None:
                target = graph.symbols.get(f"{module}:{attr}")
                if target is None or target.kind != "function" or name != attr:
                    return None, f"refers to {module}.{attr}, not a package helper function", False
                calls.append(attr)
            elif root == "typing":
                continue
            else:
                return None, (
                    f"uses the module {module!r}, outside the T2 standard-library allowlist"
                ), True  # fmt: skip
        elif name in names.functions:
            calls.append(name)
        elif name in names.constants:
            text, line = names.constants[name]
            support.append((line, text))
        else:
            return None, f"refers to {name!r}: not a literal constant, helper or module", False
    if fn.decorator_list:
        return None, "the helper is decorated", False
    a = fn.args
    if a.vararg or a.kwarg or a.kwonlyargs or a.posonlyargs:
        return None, "variadic, keyword-only or positional-only parameters", False
    if a.defaults:
        return None, "parameters with defaults", False
    new = copy.deepcopy(fn)
    for arg in new.args.args:
        t2 = _t2_annotation(arg.annotation)
        if t2 is None:
            return None, f"parameter {arg.arg!r} has no T2 type annotation", False
        arg.annotation = t2
    returns = _t2_annotation(new.returns)
    if returns is None or (isinstance(returns, ast.Constant) and returns.value is None):
        return None, "the result has no T2 type annotation (or is None)", False
    new.returns = returns
    for node in ast.walk(new):
        if isinstance(node, ast.AnnAssign) and node is not new:
            t2 = _t2_annotation(node.annotation)
            if t2 is None:
                why = f"the local annotation {ast.unparse(node.annotation)} has no T2 type"
                return None, why, False
            node.annotation = t2
    new.decorator_list = [ast.Name("procedure")]
    source = ast.unparse(ast.fix_missing_locations(new)) + "\n"
    return (
        CarvedProcedure(
            name=fn.name,
            origin=qual,
            source=source,
            support=tuple(t for _, t in sorted(set(support))),
            calls=tuple(dict.fromkeys(calls)),
        ),
        "",
        False,
    )


def _closure(
    roots: Iterable[str], by_name: Mapping[str, CarvedProcedure], exclude: Collection[str] = ()
) -> tuple[list[CarvedProcedure], list[str]]:
    """The procedures ``roots`` need (transitively; in dependency order) and the names
    no carved procedure provides."""
    out: dict[str, CarvedProcedure] = {}
    missing: list[str] = []

    def visit(name: str) -> None:
        if name in out or name in exclude:
            return
        p = by_name.get(name)
        if p is None:
            if name not in missing:
                missing.append(name)
            return
        out[name] = p
        for c in p.calls:
            visit(c)

    for r in roots:
        visit(r)
    return list(out.values()), missing


def _support(procs: Sequence[CarvedProcedure]) -> list[str]:
    lines = list(dict.fromkeys(s for p in procs for s in p.support))
    return [s for s in lines if s.startswith(("import ", "from "))] + [
        s for s in lines if not s.startswith(("import ", "from "))
    ]


def procedure_program(procs: Sequence[CarvedProcedure]) -> str:
    """A Lattice program holding just ``procs`` (with their support)."""
    parts = ["\n".join(_support(procs))] if _support(procs) else []
    parts += [p.source.strip("\n") for p in procs]
    return HEADER + "\n\n\n".join(parts) + "\n"


def with_procedures(source: str, procs: Sequence[CarvedProcedure]) -> str:
    """``source`` (a candidate) with the carved procedures it calls but does not define
    (transitively) and their support prepended; unchanged when it calls none."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.ClassDef)}
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    by_name = {p.name: p for p in procs}
    needed, _ = _closure(sorted(used & set(by_name) - defined), by_name, exclude=defined)
    if not needed:
        return source
    present = {ast.unparse(n) for n in tree.body}
    support = [s for s in _support(needed) if ast.unparse(ast.parse(s).body[0]) not in present]
    parts = ["\n".join(support)] if support else []
    parts += [p.source.strip("\n") for p in needed]
    return "\n\n\n".join([*parts, source.strip("\n")]) + "\n"


def _checker_errors(source: str, profile: Profile) -> list[str]:
    from lattice.check import check_source
    from lattice.diagnostics import Severity

    try:
        checked = check_source(source, profile)
    except Exception as exc:  # noqa: BLE001 -- a checker crash refuses the carve
        return [f"the checker failed: {type(exc).__name__}: {exc}"]
    errors = [d.render() for d in checked.diagnostics if d.severity is Severity.ERROR]
    if checked.program is None and not errors:
        errors = ["the program was not accepted"]
    return errors


def _helper_source(graph: CodeGraph, qual: str) -> str:
    sym = graph.symbols[qual]
    lines = graph.sources[sym.module].splitlines()
    return "\n".join(lines[sym.line - 1 : sym.end_line]).strip("\n") + "\n"


class ProcedureCarveOut:
    """The T2 carve-out (see the module docstring): a procedure plan, then a native plan
    only for helpers outside the T2 subset."""

    def __init__(self, native: NativeCarveOut | None = None) -> None:
        self.native = native or NativeCarveOut()

    def helpers(self, graph: CodeGraph, route: str, known: Collection[str]) -> list[str]:
        s = route_slice(graph, route)
        return [
            q
            for q in s.functions
            if q != s.route
            and q not in s.dependencies
            and q not in known
            and graph.symbols[q].kind == "function"
        ]

    def plans(
        self, graph: CodeGraph, route: str, known: Collection[str], profile: Profile
    ) -> tuple[Sequence[CarvePlan], str]:
        carved: dict[str, CarvedProcedure] = {}
        outside: dict[str, str] = {}
        translate: dict[str, str] = {}
        for q in self.helpers(graph, route, known):
            proc, why, out = carve_procedure(graph, q)
            if proc is not None:
                carved[q] = proc
            elif out:
                outside[q] = why
            else:
                translate[q] = why
        self._check(graph, carved, outside, translate, profile)
        plans = [
            CarvePlan(
                "procedure",
                procedures=tuple(carved.values()),
                translate=tuple(
                    HelperSource(q, _helper_source(graph, q), why) for q, why in translate.items()
                ),
            )
        ]
        natives: list[CarveNative] = []
        skipped: list[str] = []
        for q, why in outside.items():
            native, not_simple = self.native.native(graph, q)
            if native is None:
                skipped.append(f"{q}: outside the T2 subset ({why}) and not a simple native "
                               f"({not_simple})")  # fmt: skip
            else:
                natives.append(native)
        if natives:
            reasons = "; ".join(f"{q}: {outside[q]}" for q in outside)
            plans.append(
                CarvePlan("native", natives=tuple(natives),
                          why=f"the logic needs code outside the T2 subset ({reasons})")
            )  # fmt: skip
        return tuple(plans), "; ".join(skipped)

    def _check(
        self,
        graph: CodeGraph,
        carved: dict[str, CarvedProcedure],
        outside: dict[str, str],
        translate: dict[str, str],
        profile: Profile,
    ) -> None:
        """Drop the carved procedures the T2 checker refuses (each with the procedures it
        calls), or that call a helper not carved, until the rest check."""
        name_of = {p.name: q for q, p in carved.items()}
        changed = True
        while changed:
            changed = False
            by_name = {p.name: p for p in carved.values()}
            for q, p in list(carved.items()):
                procs, missing = _closure([p.name], by_name)
                if missing:
                    del carved[q]
                    changed = True
                    bad = [name_of.get(m, m) for m in missing]
                    out = [b for b in bad if b in outside]
                    if out:
                        outside[q] = f"calls {', '.join(out)}, outside the T2 subset"
                    else:
                        translate[q] = f"calls {', '.join(bad)}, which is not carved"
                    continue
                errors = _checker_errors(procedure_program(procs), profile)
                if errors:
                    del carved[q]
                    changed = True
                    translate[q] = "the T2 checker refuses the mechanical carve: " + "; ".join(
                        errors[:3]
                    )


def program_carve(source: str) -> tuple[int, int]:
    """(number of procedures, their lines) in a Lattice source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0, 0
    count = lines = 0
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and any(
            (d.id if isinstance(d, ast.Name) else getattr(getattr(d, "func", None), "id", None))
            == "procedure"
            for d in node.decorator_list
        ):
            count += 1
            first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            lines += (node.end_lineno or node.lineno) - first + 1
    return count, lines
