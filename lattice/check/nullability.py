"""Syntactic nullability of column expressions (for the aggregate null policy, L421).

An expression is nullable when it can produce a missing value on some row: it reads
a column that may be missing, calls an `fn.*` that yields null on bad input, or calls
a native function declared `nullable` (`nullable_natives`).
The rules are deliberately simple and syntactic; they run only on expressions P4
has already typed successfully.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping

NULL_PRODUCING_FNS = frozenset(
    {"to_date", "to_int", "to_float", "log", "sqrt", "to_uuid", "to_datetime"}
)


def expr_nullable(
    node: ast.expr,
    nullable_cols: frozenset[str],
    consts_nullable: Mapping[str, bool] | None = None,
    nullable_natives: frozenset[str] = frozenset(),
) -> bool:
    consts = consts_nullable or {}
    natives = nullable_natives

    def rec(n: ast.expr, cols: frozenset[str] = nullable_cols) -> bool:
        return expr_nullable(n, cols, consts, natives)

    match node:
        case ast.Call(func=ast.Name(id="col"), args=[ast.Constant(value=str(name))]):
            return name in nullable_cols
        case ast.Constant():
            return False
        case ast.Name(id=name):
            return consts.get(name, False)
        case ast.Attribute(value=ast.Name(id=name), attr=attr):
            # a caller reference `user.<column>`: nullable like that users column
            # (P4 records "user.<column>" for the endpoint's nullable user columns)
            return consts.get(f"{name}.{attr}", False)
        case ast.Call(func=ast.Name(id="coalesce"), args=[a, b]):
            return rec(a) and rec(b)
        case ast.Call(func=ast.Name(id="when"), args=[cond], keywords=kws):
            branches = {kw.arg: kw.value for kw in kws}
            # when(is_null(E), then=a, otherwise=b): b only runs where E is present
            # (and symmetrically then= under ~is_null(E))
            present = {
                "otherwise": _is_null_arg(cond),
                "then": _is_null_arg(cond.operand)
                if isinstance(cond, ast.UnaryOp) and isinstance(cond.op, ast.Invert)
                else None,
            }
            return any(
                _nullable_where_present(branches[b], present[b], nullable_cols, rec)
                for b in ("then", "otherwise")
                if b in branches
            )
        case ast.Call(func=ast.Name(id="is_null")):
            return False
        case ast.Call(func=ast.Attribute(value=ast.Name(id="fn"), attr=f), args=args):
            return f in NULL_PRODUCING_FNS or any(rec(a) for a in args)
        case ast.Call(func=ast.Attribute(value=ast.Name(id="native"), attr=f), args=args):
            return f in natives or any(rec(a) for a in args)
        case ast.BinOp(left=a, right=b):
            return rec(a) or rec(b)
        case ast.Compare(left=a, comparators=rest):
            return any(rec(x) for x in [a, *rest])
        case ast.UnaryOp(operand=a):
            return rec(a)
        case _:
            return False


def _is_null_arg(node: ast.expr) -> ast.expr | None:
    """E for an `is_null(E)` call, else None."""
    match node:
        case ast.Call(func=ast.Name(id="is_null"), args=[e], keywords=[]):
            return e
        case _:
            return None


def _nullable_where_present(
    node: ast.expr,
    present: ast.expr | None,
    nullable_cols: frozenset[str],
    rec: Callable[[ast.expr, frozenset[str]], bool],
) -> bool:
    """Nullability of `node` on rows where the expression `present` is known non-null:
    `node` is non-null if it is `present` itself; if `present` is a column, that
    column counts as non-null inside `node`."""
    if present is None:
        return rec(node, nullable_cols)
    if ast.dump(node) == ast.dump(present):
        return False
    match present:
        case ast.Call(func=ast.Name(id="col"), args=[ast.Constant(value=str(name))]):
            return rec(node, nullable_cols - {name})
        case _:
            return rec(node, nullable_cols)
