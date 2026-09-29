"""P8 — target capabilities (spec 2026-09-28 go-target §3.2): reject programs the chosen
code target cannot run with v1.1 semantics.

* L890 — a `native.<name>(...)` call, `for_each(t, native.x, ...)`, or NativeToken auth,
  on a target without natives.
* L891 — a program of the tabular profile on a target without it.
* L892 — a T2 construct the target does not implement yet: a call (builtin, method or
  stdlib function) outside the target's `t2_calls`, a value of a kind outside its
  `t2_types` (Date, Json, ...), `str()` / an f-string field of a container, and the
  regular expressions Go does not run (`_regex`: a non-literal `re.sub` replacement).
  (`Int ** n`, `round(Int, n)` and `min`/`max` of Int and Float arguments were gated
  until R-widen gave an Int in a Float place one meaning on every target.)

The Python target supports everything, so none of these fire for it."""

from __future__ import annotations

import ast

from lattice.check.context import CheckContext
from lattice.check.t2 import CheckedProcedure, T2Program
from lattice.check.t2 import types as T
from lattice.diagnostics import Span
from lattice.gogen.regex import regex_problem
from lattice.gogen.types import is_container, kinds
from lattice.profile import NativeToken
from lattice.targets import Target, supports_call, supports_type

_HINT = "rewrite with a supported construct, or compile for target 'python'"


def _is_native(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "native"
    )


def run(module: ast.Module, ctx: CheckContext, t2: T2Program | None, target: Target) -> None:
    if target.name == "python":
        return
    api = ctx.profile.api
    if api is None and not target.tabular:
        ctx.error(
            "L891",
            module.body[0] if module.body else Span(1, 1),
            f"tabular programs cannot be compiled to target '{target.name}' yet",
            "compile this program for target 'python'",
        )
        return
    if not target.natives:
        if api is not None and isinstance(api.auth, NativeToken):
            ctx.error(
                "L890",
                module.body[0] if module.body else Span(1, 1),
                f"the profile's auth calls native '{api.auth.native}', and target "
                f"'{target.name}' has no natives",
                "use token-table auth, or compile for target 'python'",
            )
        for node in ast.walk(module):
            if _is_native(node):
                ctx.error(
                    "L890",
                    node,
                    f"'{ast.unparse(node)}' is a native; target '{target.name}' has no natives",
                    "express it in Lattice (a @procedure), or compile for target 'python'",
                )
    if t2 is not None:
        for proc in t2.procedures.values():
            for node in ast.walk(proc.node):
                call = proc.targets.get(id(node))
                if call is None:
                    continue
                if call.startswith("native:") and not target.natives:
                    ctx.error(
                        "L890",
                        node,
                        f"'{call[7:]}' is a native; target '{target.name}' has no natives",
                        "compile for target 'python'",
                    )
                elif not call.startswith("native:") and not supports_call(target, call):
                    ctx.error(
                        "L892",
                        node,
                        f"'{call.split(':', 1)[1]}' is not available on target '{target.name}' yet",
                        "rewrite with a supported call, or compile for target 'python'",
                    )
            _constructs(proc, t2, ctx, target)
        _record_types(t2, ctx, target)


def _constructs(proc: CheckedProcedure, t2: T2Program, ctx: CheckContext, target: Target) -> None:
    """L892 for the values and forms `target` lacks (none on Python: every set is None)."""
    if target.t2_types is None:
        return
    records = {name: rec.fields for name, rec in t2.records.items()}
    reported: set[str] = set()

    def kind_gate(node: ast.AST, t: T.T2Type) -> None:
        for kind in sorted(kinds(t)):
            if not supports_type(target, kind) and kind not in reported:
                reported.add(kind)
                ctx.error(
                    "L892",
                    node,
                    f"values of type {kind} are not available on target '{target.name}' yet",
                    _HINT,
                )

    for _, t in proc.sig.params:
        kind_gate(proc.node, t)
    kind_gate(proc.node, proc.sig.returns)
    types = proc.types
    for node in ast.walk(proc.node):
        t = types.get(id(node))
        if t is not None:
            kind_gate(node, t)
        match node:
            case ast.Call(args=[arg]) if proc.targets.get(id(node)) == "builtin:str":
                _container_text(ctx, target, arg, types.get(id(arg)), records, "str()")
            case ast.FormattedValue(value=value):
                _container_text(ctx, target, value, types.get(id(value)), records, "an f-string")
            case ast.Call(args=[ast.Constant(value=str() as pattern), *rest]) if proc.targets.get(
                id(node), ""
            ).startswith("std:re."):
                _regex(ctx, target, node, proc.targets[id(node)][7:], pattern, rest)


def _regex(
    ctx: CheckContext, target: Target, node: ast.Call, fn: str, pattern: str, rest: list[ast.expr]
) -> None:
    """L892 for the regular expressions the Go engine does not run (lattice.gogen.regex):
    a non-literal `re.sub` replacement (compiled when the code is generated), and a
    pattern whose parse tree uses what the engine lacks (outside the RE2 subset: L926
    already refuses it on every target)."""
    problem = regex_problem(pattern)
    if (
        problem is None
        and fn == "sub"
        and rest
        and not (isinstance(rest[0], ast.Constant) and isinstance(rest[0].value, str))
    ):
        problem = "the replacement must be a string literal"
    if problem is not None:
        ctx.error(
            "L892",
            node,
            f"'re.{fn}' on target '{target.name}': {problem} is not available yet",
            "write the replacement as a string literal, or compile for target 'python'",
        )


def _container_text(
    ctx: CheckContext,
    target: Target,
    node: ast.AST,
    t: T.T2Type | None,
    records: dict[str, tuple[tuple[str, T.T2Type], ...]],
    where: str,
) -> None:
    if t is not None and is_container(t, records):
        ctx.error(
            "L892",
            node,
            f"{where} of a {t} value is not available on target '{target.name}' yet",
            "format the elements yourself (e.g. ', '.join(...)), or compile for target 'python'",
        )


def _record_types(t2: T2Program, ctx: CheckContext, target: Target) -> None:
    if target.t2_types is None:
        return
    for rec in t2.records.values():
        for _, ft in rec.fields:
            missing = sorted(k for k in kinds(ft) if not supports_type(target, k))
            if missing:
                ctx.error(
                    "L892",
                    rec.node,
                    f"values of type {missing[0]} are not available on target '{target.name}' yet",
                    _HINT,
                )
                break
