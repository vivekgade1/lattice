"""Shared AST helpers: annotation shapes, step parameters, call names."""

from __future__ import annotations

import ast
from typing import Literal

from lattice.catalog import NAMESPACES
from lattice.typesys import SCALAR_NAMES


def is_table_annotation(node: ast.expr | None) -> bool:
    """`Table` or `Table[Name]`."""
    if isinstance(node, ast.Name):
        return node.id == "Table"
    return annotation_schema_name(node) is not None


def annotation_schema_name(node: ast.expr | None) -> str | None:
    """ "Orders" for `Table[Orders]`; None for anything else."""
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Table"
        and isinstance(node.slice, ast.Name)
    ):
        return node.slice.id
    return None


def is_valid_annotation(node: ast.expr | None) -> bool:
    """A scalar type name, bare `Table`, or `Table[Name]`."""
    if isinstance(node, ast.Name) and node.id in SCALAR_NAMES:
        return True
    return is_table_annotation(node)


def step_param_kinds(fn: ast.FunctionDef) -> tuple[tuple[str, Literal["table", "scalar"]], ...]:
    return tuple(
        (a.arg, "table" if is_table_annotation(a.annotation) else "scalar") for a in fn.args.args
    )


INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1

LiteralValue = bool | int | float | str


def literal_value(node: ast.AST | None) -> LiteralValue | None:
    """Python value of a literal: a bool/int/float/str constant, or a (possibly
    repeatedly) negated numeric constant. None for anything else."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = literal_value(node.operand)
        if isinstance(inner, int | float) and not isinstance(inner, bool):
            return -inner
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, bool | int | float | str):
        return node.value
    return None


def redefined_steps(module: ast.Module) -> frozenset[str]:
    """Step names defined more than once (P2 reports L201; call sites are not re-checked)."""
    seen: set[str] = set()
    dups: set[str] = set()
    for stmt in module.body:
        if isinstance(stmt, ast.FunctionDef):
            (dups if stmt.name in seen else seen).add(stmt.name)
    return frozenset(dups)


def steps_of(module: ast.Module) -> dict[str, ast.FunctionDef]:
    """Module-level step definitions by name (first definition wins)."""
    steps: dict[str, ast.FunctionDef] = {}
    for stmt in module.body:
        if isinstance(stmt, ast.FunctionDef):
            steps.setdefault(stmt.name, stmt)
    return steps


def call_name(call: ast.Call) -> str | None:
    """ "where" for a Name callee; "fn.upper" / "agg.sum" for namespace attributes."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in NAMESPACES
    ):
        return f"{func.value.id}.{func.attr}"
    return None
