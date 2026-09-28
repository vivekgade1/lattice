"""P8 — target capabilities (spec 2026-09-28 go-target §3.2): reject programs the chosen
code target cannot run with v1.1 semantics.

* L890 — a `native.<name>(...)` call, `for_each(t, native.x, ...)`, or NativeToken auth,
  on a target without natives.
* L891 — a program of the tabular profile on a target without it.
* L892 — a T2 call (builtin, method or stdlib function) the target does not implement yet.

The Python target supports everything, so none of these fire for it."""

from __future__ import annotations

import ast

from lattice.check.context import CheckContext
from lattice.check.t2 import T2Program
from lattice.diagnostics import Span
from lattice.profile import NativeToken
from lattice.targets import Target, supports_call


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
                        f"'{call.split(':', 1)[1]}' is not available on target "
                        f"'{target.name}' yet",
                        "rewrite with a supported call, or compile for target 'python'",
                    )
