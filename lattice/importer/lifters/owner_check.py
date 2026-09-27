"""superuser-or-owner: ``if not <caller>.<admin flag> and (<row>.<owner> != <caller>.<key>):
raise HTTPException(403, "...")`` -> ``require(row, owner_is(user), else_=Forbidden(...))``.

``owner_is(user)`` admits the owner and Admin callers, so the check must be exactly
"neither the admin flag (the superuser dependency's column) nor the owner": the flag is
the profile's admin column, the compared column the table's owner column (from the
profile), the other side the caller's key. The two conjuncts and the two sides of the
comparison may come in either order, and the check may be spelled ``not (<caller>.<admin
flag> or <row>.<owner> == <caller>.<key>)`` or with ``not (... == ...)`` for ``!=``;
anything else (no admin bypass, another column, ``or``) is not this idiom."""

from __future__ import annotations

import ast

from lattice.importer.extract import RepoModel
from lattice.importer.lifters.base import (
    ERROR_KINDS,
    USER,
    LiftFragment,
    RouteSource,
    Step,
    attr_of,
    error,
)

IDIOM = "if not caller.is_superuser and row.owner != caller.id: raise 403 -> require(owner_is)"


def _owner_compare(
    route: RouteSource, node: ast.expr, caller: str, op: type[ast.cmpop] = ast.NotEq
) -> tuple[str, str] | None:
    """``row.<col> != caller.<key>`` (either side; ``==`` with ``op=ast.Eq``): (row
    variable, col)."""
    users = route.users_table
    key = route.key(users) if users is not None else None
    if key is None:
        return None
    match node:
        case ast.Compare(left=left, ops=[found], comparators=[right]) if isinstance(found, op):
            for a, b in ((left, right), (right, left)):
                if attr_of(b, caller) == key.name and isinstance(a, ast.Attribute):
                    if isinstance(a.value, ast.Name) and a.value.id != caller:
                        return a.value.id, a.attr
    return None


def _not_owner(route: RouteSource, node: ast.expr, caller: str) -> tuple[str, str] | None:
    """"not the owner": ``row.col != caller.id`` or ``not (row.col == caller.id)``."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _owner_compare(route, node.operand, caller, ast.Eq)
    return _owner_compare(route, node, caller)


def _denied(route: RouteSource, test: ast.expr, caller: str, flag: str) -> tuple[str, str] | None:
    """The guard "neither an admin nor the owner", in any of its equivalent spellings:
    ``not caller.flag and <not owner>`` (either order), or ``not (caller.flag or
    row.col == caller.id)`` (either order): (row variable, owner column)."""
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And) and len(test.values) == 2:
        for not_admin, compare in (test.values, test.values[::-1]):
            if (
                isinstance(not_admin, ast.UnaryOp)
                and isinstance(not_admin.op, ast.Not)
                and attr_of(not_admin.operand, caller) == flag
            ):
                found = _not_owner(route, compare, caller)
                if found is not None:
                    return found
        return None
    match test:
        case ast.UnaryOp(op=ast.Not(), operand=ast.BoolOp(op=ast.Or(), values=[a, b])):
            for admin, owner in ((a, b), (b, a)):
                if attr_of(admin, caller) == flag:
                    found = _owner_compare(route, owner, caller, ast.Eq)
                    if found is not None:
                        return found
    return None


def match(route: RouteSource, model: RepoModel) -> LiftFragment | None:
    caller, flag = route.user, route.admin_column
    if caller is None or flag is None:
        return None
    for i, stmt in enumerate(route.body):
        guard = route.guard_raise(stmt)
        if guard is None:
            continue
        test, status, detail = guard
        found = _denied(route, test, caller, flag)
        if found is None:
            continue
        var, column = found
        table = route.row_table(var)
        kind = ERROR_KINDS.get(status)
        if table is None or kind != "Forbidden" or route.owner(table) != column:
            return None
        return LiftFragment(
            lifter=__name__.rsplit(".", 1)[-1],
            idiom=IDIOM,
            stmts=(i,),
            steps=(
                Step(
                    "allowed",
                    "require",
                    ("{@" + var + "}", f"owner_is({USER})"),
                    (("else_", error(kind, detail)),),
                    binds=var,
                ),
            ),
            errors=(kind,),
        )
    return None
