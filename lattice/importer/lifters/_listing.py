"""The list-with-count block shared by ``page_list`` and ``admin_list``:

    count_statement = select(func.count()).select_from(T)[.where(T.<owner> == caller.<key>)]
    count = session.exec(count_statement).one()
    statement = select(T)[.where(...)].order_by(<T.c | col(T.c).desc()>).offset(skip).limit(limit)
    rows = session.exec(statement).all()

(each query possibly inlined into its ``exec``), and the tail that answers it:

    public = [R.model_validate(row) for row in rows]      # optional
    return Envelope(<list field>=public, <count field>=count)

``skip`` and ``limit`` must be plain ``int`` query parameters with literal defaults
(``limit``'s at most 100); Lattice bounds them (:class:`~lattice.importer.policy.PageBound`).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lattice.importer.extract import TableModel
from lattice.importer.lifters._orm import column_ref, select_of
from lattice.importer.lifters._project import project
from lattice.importer.lifters.base import (
    PAGE_LIMIT_MAX,
    USER,
    LiftFragment,
    Param,
    RouteSource,
    Step,
    assigned,
    attr_of,
    is_name,
    lit,
    method_chain,
    plural,
)
from lattice.importer.policy import PageBound


@dataclass(frozen=True)
class Listing:
    table: TableModel
    owner_filter: bool  # both queries keep only the caller's rows
    order: str
    desc: bool
    offset: str
    limit: str
    count_var: str
    rows_var: str


def _owner_where(route: RouteSource, table: TableModel, cond: ast.expr) -> bool:
    """``T.<owner> == caller.<key>`` (either side)."""
    users = route.users_table
    key = route.key(users) if users is not None else None
    owner = route.owner(table)
    if key is None or owner is None:
        return False
    match cond:
        case ast.Compare(left=left, ops=[ast.Eq()], comparators=[right]):
            for a, b in ((left, right), (right, left)):
                ref = column_ref(route, a, route.route.module)
                if ref == (table, owner) and attr_of(b, route.user) == key.name:
                    return True
    return False


def _methods(chain: list[tuple[str, ast.Call]], allowed: set[str]) -> dict[str, ast.Call] | None:
    out: dict[str, ast.Call] = {}
    for name, call in chain[:-1]:
        if name not in allowed or name in out or call.keywords:
            return None
        out[name] = call
    return out


def _count_query(route: RouteSource, q: ast.expr) -> tuple[TableModel, bool] | None:
    chain = method_chain(q)
    if not chain:
        return None
    inner = chain[-1][1]
    if not (
        route.resolves_to(inner.func, "sqlmodel:select")
        and len(inner.args) == 1
        and isinstance(inner.args[0], ast.Call)
        and not inner.args[0].args
        and isinstance(inner.args[0].func, ast.Attribute)
        and inner.args[0].func.attr == "count"
        and route.resolves_to(inner.args[0].func.value, "sqlmodel:func", "sqlalchemy:func")
    ):
        return None
    methods = _methods(chain, {"select_from", "where"})
    if methods is None or "select_from" not in methods:
        return None
    sf = methods["select_from"].args
    table = route.table(sf[0]) if len(sf) == 1 else None
    if table is None:
        return None
    where = methods.get("where")
    if where is not None and not (
        len(where.args) == 1 and _owner_where(route, table, where.args[0])
    ):
        return None
    return table, where is not None


def _order(route: RouteSource, table: TableModel, node: ast.expr) -> tuple[str, bool] | None:
    desc = False
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("desc", "asc")
        and not node.args
    ):
        desc = node.func.attr == "desc"
        node = node.func.value
    ref = column_ref(route, node, route.route.module)
    return (ref[1], desc) if ref is not None and ref[0] == table else None


def _list_query(
    route: RouteSource, q: ast.expr
) -> tuple[TableModel, bool, str, bool, str, str] | None:
    chain = method_chain(q)
    if not chain:
        return None
    table = select_of(route, chain[-1][1], route.route.module)
    methods = _methods(chain, {"where", "order_by", "offset", "limit"})
    if (
        table is None
        or methods is None
        or set(methods) - {"where"} != {"order_by", "offset", "limit"}
    ):
        return None
    args = {k: c.args for k, c in methods.items()}
    if any(len(a) != 1 for a in args.values()):
        return None
    order = _order(route, table, args["order_by"][0])
    off, lim = args["offset"][0], args["limit"][0]
    if order is None or not isinstance(off, ast.Name) or not isinstance(lim, ast.Name):
        return None
    where = args.get("where")
    if where is not None and not _owner_where(route, table, where[0]):
        return None
    return table, where is not None, order[0], order[1], off.id, lim.id


def _exec(route: RouteSource, node: ast.expr, method: str) -> ast.expr | None:
    """``session.exec(q).<method>()``: q."""
    match node:
        case ast.Call(
            func=ast.Attribute(value=ast.Call(func=ex, args=[q], keywords=[]), attr=m),
            args=[],
            keywords=[],
        ) if m == method:
            if isinstance(ex, ast.Attribute) and ex.attr == "exec" and route.is_session(ex.value):
                return q
    return None


def listing(route: RouteSource, stmts: list[ast.stmt]) -> Listing | None:
    """The list-with-count block (see the module docstring), exactly these statements."""
    env: dict[str, ast.expr] = {}
    for s in stmts:
        a = assigned(s)
        if a is None or a[0] in env:
            return None
        env[a[0]] = a[1]
    count = rows = None
    used: set[str] = set()
    for var, value in env.items():
        for method in ("one", "all"):
            q = _exec(route, value, method)
            if q is None:
                continue
            if isinstance(q, ast.Name) and q.id in env:
                used.add(q.id)
                q = env[q.id]
            if method == "one":
                count = (var, q) if count is None else None
            else:
                rows = (var, q) if rows is None else None
    if count is None or rows is None or set(env) != used | {count[0], rows[0]}:
        return None
    c = _count_query(route, count[1])
    lq = _list_query(route, rows[1])
    if c is None or lq is None:
        return None
    table, owner_filter, order, desc, off, lim = lq
    if c != (table, owner_filter):
        return None
    return Listing(table, owner_filter, order, desc, off, lim, count[0], rows[0])


def _envelope(route: RouteSource) -> tuple[str, str, str] | None:
    """The route's response envelope: (list field, count field, item model)."""
    env = route.data_model(route.route.response_model)
    if env is None:
        return None
    lists = [f for f in env.fields if f.type == "List"]
    ints = [f for f in env.fields if f.type == "Int"]
    total = (route.api.page_shape or {}).get("total")
    if len(lists) != 1 or len(ints) != 1 or len(env.fields) != 2 or lists[0].item is None:
        return None
    if ints[0].name != total or lists[0].name != (route.api.page_shape or {}).get("items"):
        return None
    return lists[0].name, ints[0].name, lists[0].item


def tail(route: RouteSource, stmts: list[ast.stmt], found: Listing) -> str | None:
    """``[R.model_validate(x) for x in rows]`` (optional) + ``return Envelope(...)``:
    the item model R."""
    env = _envelope(route)
    if env is None or not stmts or not isinstance(stmts[-1], ast.Return):
        return None
    items_field, count_field, item = env
    rows = found.rows_var
    if len(stmts) == 2:
        match stmts[0]:
            case ast.Assign(
                targets=[ast.Name(id=public)],
                value=ast.ListComp(
                    elt=ast.Call(
                        func=ast.Attribute(value=ast.Name(id=cls), attr="model_validate"),
                        args=[ast.Name(id=x)],
                        keywords=[],
                    ),
                    generators=[
                        ast.comprehension(target=ast.Name(id=x2), iter=it, ifs=[], is_async=0)
                    ],
                ),
            ) if x == x2 and is_name(it, rows) and cls == item:
                rows = public
            case _:
                return None
    elif len(stmts) != 1:
        return None
    match stmts[-1].value:
        case ast.Call(func=ast.Name(id=cls), args=[], keywords=kws) if (
            cls == route.route.response_model
        ):
            given = {k.arg: k.value for k in kws}
            if (
                set(given) == {items_field, count_field}
                and is_name(given[items_field], rows)
                and is_name(given[count_field], found.count_var)
            ):
                return item
    return None


def page_params(
    route: RouteSource, found: Listing
) -> tuple[tuple[Param, ...], tuple[PageBound, ...]] | None:
    """``skip: Int(ge=0) = <default>``, ``limit: Int(ge=0, le=100) = <default>``."""
    out: list[Param] = []
    policies: list[PageBound] = []
    for name, le in ((found.offset, None), (found.limit, PAGE_LIMIT_MAX)):
        p = route.param(name)
        if p is None or p.source != "query" or p.annotation != "int" or p.default is None:
            return None
        try:
            default = ast.literal_eval(p.default)
        except (ValueError, SyntaxError):
            return None
        if type(default) is not int or default < 0 or (le is not None and default > le):
            return None
        bounds = "ge=0" + (f", le={le}" if le is not None else "")
        out.append(Param(name, "query", f"Int({bounds})", str(default)))
        policies.append(PageBound(name, 0, le))
    return tuple(out), tuple(policies)


def page_fragment(
    route: RouteSource, lifter: str, idiom: str, stmts: tuple[int, ...], found: Listing, item: str
) -> LiftFragment | None:
    pp = page_params(route, found)
    if pp is None:
        return None
    params, policies = pp
    rows = plural(found.table)
    steps: list[Step] = [Step(rows, "fetch", (found.table.name,))]
    current = "{#" + rows + "}"
    if found.owner_filter:
        # the rows the caller may access (an Admin sees every row): not "mine"
        steps.append(Step("visible", "accessible", (current,), (("by", USER),)))
        current = "{#visible}"
    projected = project(route, found.table, current, name="public", response=item)
    if projected is None:
        return None
    steps += projected[0]
    current = projected[1]
    sort_kw: tuple[tuple[str, str], ...] = (("by", f"[{lit(found.order)}]"),)
    if found.desc:
        sort_kw += (("desc", "True"),)
    steps.append(Step("ordered", "sort", (current,), sort_kw))
    steps.append(
        Step(
            "result",
            "page",
            ("{#ordered}",),
            (("limit", found.limit), ("offset", found.offset), ("total", "True")),
        )
    )
    return LiftFragment(
        lifter=lifter,
        idiom=idiom,
        stmts=stmts,
        steps=tuple(steps),
        params=params,
        consumes=(found.offset, found.limit),
        returns="{#result}",
        success="Page",
        response=item,
        policies=policies,
    )
