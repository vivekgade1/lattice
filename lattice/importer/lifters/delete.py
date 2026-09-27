"""delete: ``session.delete(row)``, ``session.commit()``, ``return M(<field>="...")`` ->
``delete(row)`` then a one-field message row (``derive`` + ``select``).

``row`` must be a row the route holds (``session.get``); ``M`` a request/response model
with exactly one Str field, the route's response model, built with a literal."""

from __future__ import annotations

import ast

from lattice.importer.extract import RepoModel
from lattice.importer.lifters.base import LiftFragment, RouteSource, Step, lit, str_list

IDIOM = "session.delete(row) + commit + return Message(...) -> delete + message row"


def message(route: RouteSource, stmt: ast.stmt) -> tuple[str, str] | None:
    """``return M(field="literal")`` for the route's one-Str-field response model:
    (field, text)."""
    match stmt:
        case ast.Return(value=ast.Call(func=ast.Name(id=cls), args=[], keywords=[kw])):
            model = route.data_model(cls)
            if model is None or cls != route.route.response_model or kw.arg is None:
                return None
            if [(f.name, f.type) for f in model.fields] != [(kw.arg, "Str")]:
                return None
            if not (isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)):
                return None
            if route.resolve(ast.Name(cls)) != next(
                (f"{m.module}:{m.name}" for m in route.model.models if m.name == cls), None
            ):
                return None
            return kw.arg, kw.value.value
    return None


def match(route: RouteSource, model: RepoModel) -> LiftFragment | None:
    body = route.body
    for i, stmt in enumerate(body[:-2]):
        call = route.session_call(stmt.value, "delete") if isinstance(stmt, ast.Expr) else None
        if call is None or len(call.args) != 1 or not isinstance(call.args[0], ast.Name):
            continue
        row = call.args[0].id
        if row == route.user or route.row_table(row) is None:
            return None
        if not route.session_stmt(body[i + 1], "commit"):
            return None
        said = message(route, body[i + 2])
        if said is None or i + 3 != len(body):
            return None
        field, text = said
        return LiftFragment(
            lifter=__name__.rsplit(".", 1)[-1],
            idiom=IDIOM,
            stmts=(i, i + 1, i + 2),
            steps=(
                Step("gone", "delete", ("{@" + row + "}",)),
                Step("said", "derive", ("{#gone}",), ((field, lit(text)),)),
                Step("message", "select", ("{#said}",), (("cols", str_list([field])),)),
            ),
            returns="{#message}",
        )
    return None
