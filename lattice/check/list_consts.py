"""B6 — list constants in API programs (spec 2026-09-27 lattice-loops §4).

A module-level `NAME = ["a", "b", ...]` (a non-empty list of string literals) names a
column list. It is single-assignment like any constant (P2 reports a rebinding as L201)
and usable only as `select(t, cols=NAME)`, in endpoints and steps alike.

This pass runs after P2 (names are resolved, rebindings reported) and before P3: it
replaces every `cols=NAME` of a `select` with a copy of the list, located at the use
(so a wrong column is reported there), reports any other use of the name as L404, and
drops the list constants from the module -- later passes, lowering and codegen see
only list literals, exactly as if the program had spelled them out.
"""

from __future__ import annotations

import ast
import copy

from lattice.check.astutil import is_list_constant_assign
from lattice.check.context import CheckContext


def _local_names(fn: ast.FunctionDef) -> set[str]:
    """Names a function binds itself (parameters and assignment targets)."""
    names = {a.arg for a in fn.args.args}
    for stmt in fn.body:
        if isinstance(stmt, ast.Assign):
            names.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
    return names


def _copy_at(value: ast.List, use: ast.AST) -> ast.List:
    new = copy.deepcopy(value)
    for node in ast.walk(new):
        ast.copy_location(node, use)
    return new


def run(module: ast.Module, ctx: CheckContext) -> None:
    if ctx.profile.api is None:
        return
    lists: dict[str, ast.List] = {}
    kept: list[ast.stmt] = []
    for stmt in module.body:
        if is_list_constant_assign(stmt):
            assert isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name)
            assert isinstance(stmt.value, ast.List)
            lists.setdefault(stmt.targets[0].id, stmt.value)  # the first binding wins
            continue
        kept.append(stmt)
    if not lists:
        return
    for stmt in kept:
        if isinstance(stmt, ast.FunctionDef):
            _expand(stmt, lists, ctx)
        else:
            _misuses(stmt, lists, ctx)
    module.body[:] = kept


def _expand(fn: ast.FunctionDef, lists: dict[str, ast.List], ctx: CheckContext) -> None:
    visible = {name: value for name, value in lists.items() if name not in _local_names(fn)}
    if not visible:
        return
    for node in ast.walk(fn):
        match node:
            case ast.Call(func=ast.Name(id="select"), keywords=keywords):
                for kw in keywords:
                    if kw.arg == "cols" and isinstance(kw.value, ast.Name):
                        value = visible.get(kw.value.id)
                        if value is not None:
                            kw.value = _copy_at(value, kw.value)
            case _:
                pass
    _misuses(fn, visible, ctx)


def _misuses(root: ast.AST, lists: dict[str, ast.List], ctx: CheckContext) -> None:
    reported: set[str] = set()
    for node in ast.walk(root):
        if isinstance(node, ast.Name) and node.id in lists and node.id not in reported:
            if isinstance(node.ctx, ast.Store):
                continue
            reported.add(node.id)
            ctx.error(
                "L404",
                node,
                f"'{node.id}' is a list of column names: use it only as select(t, cols={node.id})",
                "spell the columns out where another op needs them",
            )
