"""R3 of the close-the-gap loop (spec 2026-09-27 importer §3.4): **carve-out**.

A carve-out keeps a route in Lattice and moves only the inexpressible piece into a small
typed native called from Lattice. This module is the extension point:

* a :class:`CarveOut` strategy says which natives a route may use
  (:meth:`CarveOut.natives`) -- or why it has none (then R3 is skipped with the reason);
* :func:`carve_profile_source` appends the natives to the generated profile's source, so
  the profile a carve-out is verified against is exactly the one written to
  ``lattice_import/profile.py``.

Today's strategy, :class:`NativeCarveOut`, is the simple case only: a package helper the
route's code-graph slice reaches, with scalar parameters and result, becomes a typed
native wrapping the original function (the body is referenced, never copied). Its
effect flag comes from the helper's own slice (the graph's effect heuristic over every
function it reaches): an effectful helper is declared ``effect=True`` and must return
``None`` (Lattice runs effects through ``for_each`` after commit); an effectful helper
with a result is not simple and is skipped -- never declared pure.

TODO(I5b): the general carve-out -- splitting a route into Lattice plus T2
``@procedure`` pieces (state machines, arbitrary library calls) -- implements
:class:`CarveOut` in the next node.
"""

from __future__ import annotations

import ast
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Protocol

from lattice.importer.graph import CodeGraph, Slice, route_slice

__all__ = [
    "CarveNative",
    "CarveOut",
    "NativeCarveOut",
    "carve_profile_source",
]

SKIP_TODO = "TODO(I5b): the T2 @procedure carve-out is not implemented yet"
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
