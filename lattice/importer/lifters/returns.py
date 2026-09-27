"""return a row: ``return row`` for a row the route holds (``session.get``, a created
row) -> that row, projected to the response model when its table has private columns
(the caller's own row is the ``me`` lifter's)."""

from __future__ import annotations

import ast

from lattice.importer.extract import RepoModel
from lattice.importer.lifters._project import project
from lattice.importer.lifters.base import LiftFragment, RouteSource

IDIOM = "return row -> the row (private columns selected away)"


def match(route: RouteSource, model: RepoModel) -> LiftFragment | None:
    if not route.body:
        return None
    last = route.body[-1]
    if not (isinstance(last, ast.Return) and isinstance(last.value, ast.Name)):
        return None
    var = last.value.id
    if var == route.user:
        return None
    table = route.row_table(var)
    if table is None:
        return None
    found = project(route, table, "{@" + var + "}")
    if found is None:
        return None
    steps, returned = found
    return LiftFragment(
        lifter=__name__.rsplit(".", 1)[-1],
        idiom=IDIOM,
        stmts=(len(route.body) - 1,),
        steps=steps,
        returns=returned,
    )
