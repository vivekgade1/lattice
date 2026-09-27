"""Query shapes shared by the lifters: ``select(T).where(T.c == v)``, ``session.exec(q)
.first()``, and a package helper that is exactly such a lookup (``get_user_by_email``)."""

from __future__ import annotations

import ast
from collections.abc import Mapping

from lattice.importer.extract import TableModel
from lattice.importer.lifters.base import RouteSource, assigned, is_name


def column_ref(route: RouteSource, node: ast.expr, module: str) -> tuple[TableModel, str] | None:
    """``T.c`` or ``col(T.c)``: (T, c)."""
    if (
        isinstance(node, ast.Call)
        and len(node.args) == 1
        and not node.keywords
        and route.resolves_to(node.func, "sqlmodel:col", module=module)
    ):
        node = node.args[0]
    if isinstance(node, ast.Attribute):
        table = route.table(node.value, module)
        if table is not None and any(c.name == node.attr for c in table.columns):
            return table, node.attr
    return None


def select_of(route: RouteSource, node: ast.expr | None, module: str) -> TableModel | None:
    """``select(T)``: T."""
    if (
        isinstance(node, ast.Call)
        and len(node.args) == 1
        and not node.keywords
        and route.resolves_to(node.func, "sqlmodel:select", module=module)
    ):
        return route.table(node.args[0], module)
    return None


def where_eq(
    route: RouteSource, node: ast.expr | None, module: str
) -> tuple[TableModel, str, ast.expr] | None:
    """``select(T).where(T.c == v)`` (either side): (T, c, v)."""
    match node:
        case ast.Call(
            func=ast.Attribute(value=inner, attr="where"),
            args=[ast.Compare(left=left, ops=[ast.Eq()], comparators=[right])],
            keywords=[],
        ):
            table = select_of(route, inner, module)
            if table is None:
                return None
            for a, b in ((left, right), (right, left)):
                ref = column_ref(route, a, module)
                if ref is not None and ref[0] == table:
                    return table, ref[1], b
    return None


def exec_first(node: ast.expr | None, session: str | None) -> ast.expr | None:
    """``session.exec(q).first()``: q."""
    match node:
        case ast.Call(
            func=ast.Attribute(
                value=ast.Call(
                    func=ast.Attribute(value=ast.Name(id=s), attr="exec"), args=[q], keywords=[]
                ),
                attr="first",
            ),
            args=[],
            keywords=[],
        ) if s == session:
            return q
    return None


def _helper_lookup(
    route: RouteSource,
    fn: ast.FunctionDef,
    module: str,
    bound: Mapping[str, ast.expr],
    session: str | None,
) -> tuple[TableModel, str, ast.expr] | None:
    """A helper whose body is exactly ``q = select(T).where(T.c == p)``, ``r =
    session.exec(q).first()``, ``return r`` (or the inlined forms), where ``session``
    and ``p`` are its parameters: (T, c, the call-site argument for p)."""
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    if not body or not isinstance(body[-1], ast.Return) or body[-1].value is None:
        return None
    env: dict[str, ast.expr] = {}
    for stmt in body[:-1]:
        a = assigned(stmt)
        if a is None:
            return None
        env[a[0]] = a[1]
    ret = body[-1].value
    if isinstance(ret, ast.Name) and ret.id in env:
        ret = env.pop(ret.id)
    for inner in bound:
        q = exec_first(ret, inner)
        if q is None:
            continue
        if isinstance(q, ast.Name) and q.id in env:
            q = env.pop(q.id)
        if env:
            return None  # an assignment the lookup does not use
        found = where_eq(route, q, module)
        if found is None or not isinstance(found[2], ast.Name):
            return None
        table, column, p = found
        if p.id == inner or p.id not in bound:
            return None
        if not is_name(bound[inner], session):
            return None
        return table, column, bound[p.id]
    return None


def lookup(
    route: RouteSource,
    node: ast.expr | None,
    *,
    session: str | None = None,
    module: str | None = None,
) -> tuple[TableModel, str, ast.expr] | None:
    """A single-row lookup by one column: inline ``session.exec(select(T).where(T.c ==
    v)).first()`` or a call to a package helper doing exactly that: (T, c, v).
    ``session``/``module``: the session variable and module of the code the call is in
    (default: the route's)."""
    module = module or route.route.module
    session = session if session is not None else route.session
    q = exec_first(node, session)
    if q is not None:
        return where_eq(route, q, module)
    if not isinstance(node, ast.Call):
        return None
    found = route.index.node(route.resolve(node.func, module))
    if found is None or not isinstance(found[1], ast.FunctionDef):
        return None
    helper_module, fn = found
    bound = bind_call(fn, node)
    if bound is None:
        return None
    return _helper_lookup(route, fn, helper_module, bound, session)


def bind_call(fn: ast.FunctionDef, call: ast.Call) -> dict[str, ast.expr] | None:
    """The call's argument for every parameter of ``fn`` (None if one is left to its
    default, or the call does not fit)."""
    a = fn.args
    positional = [*a.posonlyargs, *a.args]
    if a.vararg or a.kwarg or len(call.args) > len(positional):
        return None
    bound: dict[str, ast.expr] = {}
    for p, v in zip(positional, call.args, strict=False):
        bound[p.arg] = v
    names = {p.arg for p in [*positional, *a.kwonlyargs]}
    for k in call.keywords:
        if k.arg is None or k.arg not in names or k.arg in bound:
            return None
        bound[k.arg] = k.value
    return bound if set(bound) == names else None


def _field_call(
    route: RouteSource, table: TableModel, column: str
) -> tuple[str, ast.expr | None] | None:
    """The class-body definition of ``column`` in ``table`` or its package bases:
    (module, the assigned value -- a ``Field(...)`` call, a literal, or None)."""
    todo = [f"{table.module}:{table.name}"]
    seen: set[str] = set()
    while todo:
        qual = todo.pop(0)
        if qual in seen:
            continue
        seen.add(qual)
        found = route.index.node(qual)
        if found is None or not isinstance(found[1], ast.ClassDef):
            continue
        module, cls = found
        for stmt in cls.body:
            if isinstance(stmt, ast.AnnAssign) and is_name(stmt.target, column):
                return module, stmt.value
        for base in cls.bases:
            q = route.index.resolve(module, base)
            if q is not None:
                todo.append(q)
    return None


def _now_function(route: RouteSource, node: ast.expr, module: str) -> bool:
    """``datetime.now`` / ``datetime.utcnow`` or a package function returning
    ``datetime.now(...)`` / ``datetime.utcnow()``."""

    def is_now(expr: ast.expr | None, mod: str) -> bool:
        return (
            isinstance(expr, ast.Attribute)
            and expr.attr in ("now", "utcnow")
            and route.index.resolve(mod, expr.value) == "datetime:datetime"
        )

    if is_now(node, module):
        return True
    found = route.index.node(route.index.resolve(module, node))
    if found is None or not isinstance(found[1], ast.FunctionDef):
        return False
    fmod, fn = found
    body = [
        s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
    ]
    return (
        len(body) == 1
        and isinstance(body[0], ast.Return)
        and isinstance(body[0].value, ast.Call)
        and is_now(body[0].value.func, fmod)
        and not body[0].value.keywords
        and len(body[0].value.args) <= 1
    )


def column_default(route: RouteSource, table: TableModel, column: str) -> tuple[str, object] | None:
    """What an insert that leaves ``column`` out stores: ``("now", None)`` (a
    ``default_factory`` returning the current time), ``("key", None)`` (a
    ``uuid.uuid4`` key factory), ``("literal", v)``; None when there is no default or
    it has no Lattice spelling."""
    found = _field_call(route, table, column)
    if found is None:
        return None
    module, value = found
    if value is None:
        return None
    if isinstance(value, ast.Call) and route.resolves_to(
        value.func, "sqlmodel:Field", module=module
    ):
        kws = {k.arg: k.value for k in value.keywords if k.arg}
        factory = kws.get("default_factory")
        if factory is not None:
            if route.index.resolve(module, factory) == "uuid:uuid4":
                return "key", None
            return ("now", None) if _now_function(route, factory, module) else None
        value = value.args[0] if value.args else kws.get("default")
        if value is None:
            return None
    if isinstance(value, ast.Constant) and value.value is not Ellipsis:
        return "literal", value.value
    return None
