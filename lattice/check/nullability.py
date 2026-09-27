"""Syntactic nullability of column expressions (for the aggregate null policy, L421).

An expression is nullable when it can produce a missing value on some row: it reads
a column that may be missing, or calls an `fn.*` that yields null on bad input.
The rules are deliberately simple and syntactic; they run only on expressions P4
has already typed successfully.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping

NULL_PRODUCING_FNS = frozenset({"to_date", "to_int", "to_float", "log", "sqrt"})


def expr_nullable(
    node: ast.expr,
    nullable_cols: frozenset[str],
    consts_nullable: Mapping[str, bool] | None = None,
) -> bool:
    consts = consts_nullable or {}
    match node:
        case ast.Call(func=ast.Name(id="col"), args=[ast.Constant(value=str(name))]):
            return name in nullable_cols
        case ast.Constant():
            return False
        case ast.Name(id=name):
            return consts.get(name, False)
        case ast.Call(func=ast.Name(id="coalesce"), args=[a, b]):
            return expr_nullable(a, nullable_cols, consts) and expr_nullable(
                b, nullable_cols, consts
            )
        case ast.Call(func=ast.Name(id="when"), keywords=kws):
            return any(
                expr_nullable(kw.value, nullable_cols, consts)
                for kw in kws
                if kw.arg in ("then", "otherwise")
            )
        case ast.Call(func=ast.Name(id="is_null")):
            return False
        case ast.Call(func=ast.Attribute(value=ast.Name(id="fn"), attr=f), args=args):
            return f in NULL_PRODUCING_FNS or any(
                expr_nullable(a, nullable_cols, consts) for a in args
            )
        case ast.BinOp(left=a, right=b):
            return expr_nullable(a, nullable_cols, consts) or expr_nullable(
                b, nullable_cols, consts
            )
        case ast.Compare(left=a, comparators=rest):
            return any(expr_nullable(x, nullable_cols, consts) for x in [a, *rest])
        case ast.UnaryOp(operand=a):
            return expr_nullable(a, nullable_cols, consts)
        case _:
            return False
