"""get-or-404: ``x = session.get(T, <path param>)`` then ``if not x: raise
HTTPException(404, "...")`` -> ``fetch`` / ``where`` on the key / ``one(..., else_=NotFound)``.

The key argument must be a path parameter whose type is the table's single key type;
the guard must be the very next statement, raise a mapped status (404 -> ``NotFound``)
with a literal detail, and have no ``else``."""

from __future__ import annotations

import ast

from lattice.importer.extract import RepoModel
from lattice.importer.lifters.base import (
    ERROR_KINDS,
    LiftFragment,
    Param,
    RouteSource,
    Step,
    assigned,
    error,
    is_falsy_test,
    lit,
    plural,
)

IDIOM = "session.get(T, id) + if not x: raise HTTPException(404) -> one(..., else_=NotFound)"


def match(route: RouteSource, model: RepoModel) -> LiftFragment | None:
    body = route.body
    for i, stmt in enumerate(body[:-1]):
        a = assigned(stmt)
        if a is None:
            continue
        var, value = a
        got = route.session_get(value)
        if got is None:
            continue
        table, key_expr = got
        if not isinstance(key_expr, ast.Name) or key_expr.id not in route.params_of("path"):
            return None
        key = route.key(table)
        param = route.param(key_expr.id)
        if key is None or param is None or param.type != key.type or key.type is None:
            return None
        guard = route.guard_raise(body[i + 1])
        if guard is None:
            return None
        test, status, detail = guard
        kind = ERROR_KINDS.get(status)
        if kind is None or not is_falsy_test(test, var):
            return None
        rows = plural(table)
        return LiftFragment(
            lifter=__name__.rsplit(".", 1)[-1],
            idiom=IDIOM,
            stmts=(i, i + 1),
            steps=(
                Step(rows, "fetch", (table.name,)),
                Step(
                    "found", "where", ("{#" + rows + "}", f"col({lit(key.name)}) == {key_expr.id}")
                ),
                Step(var, "one", ("{#found}",), (("else_", error(kind, detail)),), binds=var),
            ),
            params=(Param(key_expr.id, "path", key.type),),
            consumes=(key_expr.id,),
            errors=(kind,),
        )
    return None
