"""uniqueness pre-check: ``if body.f: existing = <lookup by T.f == body.f>; if existing
and existing.<key> != <subject>.<key>: raise HTTPException(409, "...")`` ->
``unique_or=Conflict("...")`` on the ``update``/``insert`` that later writes ``f``.

The lookup is inline (``session.exec(select(T).where(T.f == body.f)).first()``) or a
package helper that is exactly that lookup; ``f`` must be a UNIQUE column of ``T`` and
``T`` the subject's table (the caller's row, or a row the route holds), so the write
hits the UNIQUE constraint exactly when the pre-check would raise. Only 409 maps to
``Conflict``; the ``if body.f`` guard is optional."""

from __future__ import annotations

import ast

from lattice.importer.extract import RepoModel
from lattice.importer.lifters._orm import lookup
from lattice.importer.lifters.base import (
    ERROR_KINDS,
    LiftFragment,
    Modifier,
    RouteSource,
    assigned,
    attr_of,
    error,
    is_name,
)

IDIOM = "lookup by a unique column + raise 409 unless it is the subject -> unique_or=Conflict"


def _conflict_test(test: ast.expr, existing: str, key: str) -> str | None:
    """``existing and existing.<key> != S.<key>`` (or ``existing is not None and ...``):
    the subject variable S."""
    match test:
        case ast.BoolOp(
            op=ast.And(),
            values=[present, ast.Compare(left=left, ops=[ast.NotEq()], comparators=[right])],
        ):
            ok = is_name(present, existing) or (
                isinstance(present, ast.Compare)
                and is_name(present.left, existing)
                and isinstance(present.ops[0], ast.IsNot)
                and isinstance(present.comparators[0], ast.Constant)
                and present.comparators[0].value is None
            )
            if not ok:
                return None
            for a, b in ((left, right), (right, left)):
                if attr_of(a, existing) == key and isinstance(b, ast.Attribute) and b.attr == key:
                    if isinstance(b.value, ast.Name) and b.value.id != existing:
                        return b.value.id
    return None


def _check(
    route: RouteSource, stmts: list[ast.stmt], body_var: str | None, field: str | None
) -> tuple[str, str, str] | None:
    """``existing = <lookup>; if <conflict>: raise 409``: (subject, column, detail)."""
    if len(stmts) != 2:
        return None
    a = assigned(stmts[0])
    if a is None:
        return None
    existing, value = a
    found = lookup(route, value)
    if found is None:
        return None
    table, column, arg = found
    if not (isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name)):
        return None
    if arg.value.id not in route.params_of("body") or arg.attr != column:
        return None
    if body_var is not None and (arg.value.id, arg.attr) != (body_var, field):
        return None
    col = next(c for c in table.columns if c.name == column)
    key = route.key(table)
    guard = route.guard_raise(stmts[1])
    if not col.unique or key is None or guard is None:
        return None
    test, status, detail = guard
    if ERROR_KINDS.get(status) != "Conflict":
        return None
    subject = _conflict_test(test, existing, key.name)
    if subject is None or route.row_table(subject) != table:
        return None
    return subject, column, detail


def match(route: RouteSource, model: RepoModel) -> LiftFragment | None:
    for i, stmt in enumerate(route.body):
        found: tuple[str, str, str] | None = None
        if isinstance(stmt, ast.If) and not stmt.orelse:
            body_var = field = None
            if isinstance(stmt.test, ast.Attribute) and isinstance(stmt.test.value, ast.Name):
                body_var, field = stmt.test.value.id, stmt.test.attr
                found = _check(route, list(stmt.body), body_var, field)
        if found is None and i + 1 < len(route.body):
            found = _check(route, [stmt, route.body[i + 1]], None, None)
            stmts = (i, i + 1)
        else:
            stmts = (i,)
        if found is None:
            continue
        subject, column, detail = found
        return LiftFragment(
            lifter=__name__.rsplit(".", 1)[-1],
            idiom=IDIOM,
            stmts=stmts,
            errors=("Conflict",),
            modifiers=(Modifier("unique_or", error("Conflict", detail), subject, column),),
        )
    return None
