"""list of the caller's rows: ``if caller.<admin flag>: <list-with-count of every row>
else: <the same, both queries filtered on T.<owner> == caller.<key>>`` then the
envelope -> ``fetch`` / ``owned(by=user)`` / ``sort`` / ``page(..., total=True)``.

``owned`` keeps an Admin caller's every row, so the two branches must be the same
listing except for the owner filter (the profile's owner column, compared with the
caller's key) and the test the profile's admin column (see ``_listing``)."""

from __future__ import annotations

import ast

from lattice.importer.extract import RepoModel
from lattice.importer.lifters._listing import listing, page_fragment, tail
from lattice.importer.lifters.base import LiftFragment, RouteSource, attr_of

IDIOM = "if superuser: list all else: list owned (offset/limit + count) -> owned + page(total=True)"


def match(route: RouteSource, model: RepoModel) -> LiftFragment | None:
    body = route.body
    flag = route.admin_column
    if not body or flag is None or not isinstance(body[0], ast.If):
        return None
    test = body[0].test
    admin_first = attr_of(test, route.user) == flag
    admin_last = (
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and attr_of(test.operand, route.user) == flag
    )
    if not (admin_first or admin_last):
        return None
    every, mine = (body[0].body, body[0].orelse) if admin_first else (body[0].orelse, body[0].body)
    a, b = listing(route, list(every)), listing(route, list(mine))
    if a is None or b is None or a.owner_filter or not b.owner_filter:
        return None
    if (a.table, a.order, a.desc, a.offset, a.limit, a.count_var, a.rows_var) != (
        b.table,
        b.order,
        b.desc,
        b.offset,
        b.limit,
        b.count_var,
        b.rows_var,
    ):
        return None
    item = tail(route, list(body[1:]), b)
    if item is None:
        return None
    return page_fragment(
        route, __name__.rsplit(".", 1)[-1], IDIOM, tuple(range(len(body))), b, item
    )
