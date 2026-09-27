"""partial update: ``row.sqlmodel_update(body.model_dump(exclude_unset=True))`` (the dump
possibly bound to a name first), ``session.add(row)``, ``session.commit()``,
``session.refresh(row)`` -> ``update(row, f=f, ...)`` with every body field an ``UNSET``
parameter (not sent: the column is kept).

``row`` is a row the route holds (``session.get``) or the caller. Every body field must
be a column of that table with the same type; a field the body model makes Optional
over a NOT NULL column is refused with 422 by Lattice (a :class:`NotNull` policy), and a
non-Optional field over a nullable column is not this idiom (Lattice would accept the
null the original refuses)."""

from __future__ import annotations

import ast

from lattice.importer.extract import RepoModel
from lattice.importer.lifters.base import (
    LiftFragment,
    Param,
    RouteSource,
    Step,
    assigned,
    body_fields,
    field_annotation,
    is_name,
)
from lattice.importer.policy import NotNull

IDIOM = "row.sqlmodel_update(body.model_dump(exclude_unset=True)) + commit -> update(..., UNSET)"


def _dump(node: ast.expr | None) -> str | None:
    """``B.model_dump(exclude_unset=True)``: B."""
    match node:
        case ast.Call(
            func=ast.Attribute(value=ast.Name(id=body), attr="model_dump"),
            args=[],
            keywords=[ast.keyword(arg="exclude_unset", value=ast.Constant(value=True))],
        ):
            return body
    return None


def _sqlmodel_update(stmt: ast.stmt) -> tuple[str, ast.expr] | None:
    """``X.sqlmodel_update(<one argument>)``: (X, the argument)."""
    match stmt:
        case ast.Expr(
            value=ast.Call(
                func=ast.Attribute(value=ast.Name(id=row), attr="sqlmodel_update"),
                args=[arg],
                keywords=[],
            )
        ):
            return row, arg
    return None


def match(route: RouteSource, model: RepoModel) -> LiftFragment | None:
    body = route.body
    for i, stmt in enumerate(body):
        found = _sqlmodel_update(stmt)
        if found is None:
            continue
        row, arg = found
        start = i
        source = _dump(arg)
        if source is None and isinstance(arg, ast.Name) and i > 0:
            a = assigned(body[i - 1])
            if a is not None and a[0] == arg.id:
                source, start = _dump(a[1]), i - 1
        if source is None or source not in route.params_of("body"):
            return None
        # session.add(row) (optional), session.commit(), session.refresh(row) (optional)
        j = i + 1
        if j < len(body) and route.session_stmt(body[j], "add", row):
            j += 1
        if not (j < len(body) and route.session_stmt(body[j], "commit")):
            return None
        j += 1
        if j < len(body) and route.session_stmt(body[j], "refresh", row):
            j += 1
        table = route.row_table(row)
        fields = body_fields(route, source)
        if table is None or not fields:
            return None
        columns = {c.name: c for c in table.columns}
        params: list[Param] = []
        policies: list[NotNull] = []
        for f in fields:
            c = columns.get(f.name)
            ann = field_annotation(f)
            if c is None or ann is None or c.type != f.type or c.primary_key:
                return None
            if c.nullable and not f.nullable:
                return None
            if f.nullable and not c.nullable:
                policies.append(NotNull(f.name))
            params.append(Param(f.name, "body", ann, None if f.required else "UNSET"))
        if any(is_name(n, source) for s in body[j:] for n in ast.walk(s)):
            return None  # the body is read again: more than this idiom
        names = tuple(f.name for f in fields)
        return LiftFragment(
            lifter=__name__.rsplit(".", 1)[-1],
            idiom=IDIOM,
            stmts=tuple(range(start, j)),
            steps=(
                Step(
                    "updated",
                    "update",
                    ("{@" + row + "}",),
                    tuple((n, n) for n in names),
                    binds=row,
                    writes=names,
                ),
            ),
            params=tuple(params),
            consumes=(source,),
            policies=tuple(policies),
        )
    return None
