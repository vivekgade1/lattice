"""Shared AST helpers: annotation shapes, step parameters, call names."""

from __future__ import annotations

import ast
from typing import Literal

from lattice.catalog import EXPR_FUNCS, NAMESPACES
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


# Maximum AST nesting depth. Keeps the recursive checker, IR lowering (deepcopy) and
# expression compiler far from Python's recursion limit. Also bounds an expression's
# depth after named constants are substituted into it (P4).
MAX_DEPTH = 100
# Maximum size (AST nodes) of one statement's value after substituting named constants:
# chained constants (b = a + a, c = b + b, ...) would otherwise grow exponentially.
MAX_EXPANDED_NODES = 10_000

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


def has_endpoint_decorator(fn: ast.FunctionDef) -> bool:
    """`@endpoint(...)` (API profile); P1 rejects it when the profile has no API."""
    return any(
        isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "endpoint"
        for d in fn.decorator_list
    )


def steps_of(module: ast.Module) -> dict[str, ast.FunctionDef]:
    """Module-level step definitions by name (first definition wins); endpoints are
    not steps (they cannot be called)."""
    steps: dict[str, ast.FunctionDef] = {}
    for stmt in module.body:
        if isinstance(stmt, ast.FunctionDef) and not has_endpoint_decorator(stmt):
            steps.setdefault(stmt.name, stmt)
    return steps


def endpoints_of(module: ast.Module) -> list[ast.FunctionDef]:
    return [s for s in module.body if isinstance(s, ast.FunctionDef) and has_endpoint_decorator(s)]


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


# Plain-name functions that may appear in a constant expression (fn.* is an attribute).
_CONST_CALLS = EXPR_FUNCS - {"col"}


def is_constant_expr(node: ast.expr) -> bool:
    """True if an assigned value binds a named constant: a column-free scalar
    expression (no col(), no agg.*, no lists, no table op, step or unknown call).

    This is syntactic, so every pass classifies a statement the same way; whether
    the value is really a well-typed scalar is P4's business."""
    for n in ast.walk(node):
        match n:
            case ast.List() | ast.Tuple():
                return False
            case ast.Call(func=ast.Name(id=name)) if name not in _CONST_CALLS:
                return False  # col(), a table op or step, or an unknown callee
            case ast.Attribute(value=ast.Name(id="agg")):
                return False
            case _:
                pass
    return True


def step_mentions(steps: dict[str, ast.FunctionDef], name: str) -> frozenset[str]:
    """Every column name a step body (and the steps it calls) may read or produce:
    its string constants and keyword names. A conservative over-approximation used to
    carry private / owner lineage through a step called from an endpoint."""
    found: set[str] = set()
    seen: set[str] = set()
    todo = [name]
    while todo:
        current = todo.pop()
        if current in seen or current not in steps:
            continue
        seen.add(current)
        for stmt in steps[current].body:
            for node in ast.walk(stmt):
                match node:
                    case ast.Constant(value=str(text)):
                        found.add(text)
                    case ast.keyword(arg=str(arg)):
                        found.add(arg)
                    case ast.Call(func=ast.Name(id=callee)) if callee in steps:
                        todo.append(callee)
                    case _:
                        pass
    return frozenset(found)
