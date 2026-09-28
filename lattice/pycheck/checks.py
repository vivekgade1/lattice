"""The guarantee checks (round-5 spec 2026-09-28 §3, items 1-6) on ordinary FastAPI +
SQLModel code, and :func:`run_pycheck`.

Every check prefers silence to a guess: a false finding costs the model a repair round,
so each one fires only on a pattern it can read with certainty, and the original app's
own findings (the policy's ``baseline``) are its accepted conventions, never reported.
What each check cannot see is listed with it.

* **PY801 ownership.** A Customer route (not admin-only by dependency or by an explicit
  ``if not <caller>.<admin flag>: raise``) that references an owned table -- directly or
  through the helpers and repository methods it calls -- with no owner evidence for it:
  nowhere (in live code) does it, or a helper it hands the caller or the caller's id,
  compare the owner column with the caller's id, or set it from it. For a table owned
  through a parent, evidence on the parent counts; a link table between owned tables
  needs evidence on every owned parent. Also: a Customer route writing a users-table row
  it fetched by a request value, with no ``!=`` check against the caller.
  *Cannot see:* evidence that exists but is not applied to the query that reaches the
  response (a filter built and then replaced, an ``or`` that widens it, a count taken
  before the filter), an explicit admin branch that should not see everything (allowed by
  the spec), ownership enforced by a helper not handed the caller.
* **PY802 private columns.** A non-admin route whose response model is a table with
  private columns, or a model mapping to such a table that declares one, or that has no
  response model and returns such a table's rows; a dict literal (in the route or the
  helpers it calls) with a private column as a value. *Cannot see:* ``model_dump()`` /
  ``__dict__`` of a row, private values copied through intermediate variables.
* **PY803 unbounded inputs.** An int query parameter with no ``le``/``lt`` used as a
  ``.limit()`` (directly or in a helper it is passed to); a request string or number
  (a body model's field or a scalar parameter) written to a table (constructor,
  ``model_validate``, ``sqlmodel_update``, attribute assignment; directly or in a helper)
  with no ``max_length`` / ``le`` on the request side. ``EmailStr``, enums, ``Literal``
  and fields with a validator count as bounded. *Cannot see:* bounds enforced by hand in
  code, values reaching the database through containers or ``setattr``.
* **PY804 route order.** Two routes of one module with the same method where the earlier
  one's path has a parameter segment where the later one has a literal (and every other
  segment agrees): FastAPI matches the earlier. *Cannot see:* routers of different modules
  (their order is ``include_router``'s).
* **PY805 error conventions.** An ``if`` testing ``<row>.<owner column> != <caller id>``
  that raises another status (or, where the app uses one detail, another detail) than the
  app answers another user's row with; an ``if not <caller>.<admin flag>`` raising another
  status or detail than the app's admin dependency. *Cannot see:* conventions the app
  never states in code (e.g. which lookups are owner-scoped), the order of checks.
* **PY806 naive / aware datetimes.** Ordering comparisons (and subtraction) of a stored
  datetime read from a row (naive on SQLite) with a timezone-aware value
  (``datetime.now(UTC)``, ``.replace(tzinfo=...)``, ``.astimezone()``); a SQL comparison
  of a stored datetime column with a request datetime parameter that was never converted
  (it may carry any UTC offset). A name assigned values of different kinds is not judged.
  *Cannot see:* values crossing function boundaries, awareness decided at run time.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from lattice.importer.extract import DataModel, FieldSpec, Route, TableModel
from lattice.pycheck.findings import Finding
from lattice.pycheck.policy import ErrorAnswer, Policy
from lattice.pycheck.view import (
    AppView,
    FunctionNode,
    bind_args,
    dead_lines,
    http_error,
    raises_http,
    route_key,
)

log = logging.getLogger(__name__)

HELPER_DEPTH = 3  # how deep request values and private reads are followed into helpers
CALLER_PARAM_NAMES = frozenset({"current_user", "caller"})
CALLER_ID_PARAM_NAMES = frozenset({"user_id", "owner_id", "current_user_id", "caller_id"})


# ---- the entry point -------------------------------------------------------------------------


def run_pycheck(app_dir: Path, policy: Policy, *, strict: bool = False) -> list[Finding]:
    """The guarantee findings on the app package at ``app_dir`` (the directory of
    ``policy.package``), minus the policy's baseline, sorted by file and line.

    A file that does not parse is one PY800 finding. Any other failure of the checker
    itself answers no findings (a checker bug must not cost the model a repair round)
    unless ``strict``."""
    try:
        view = AppView(Path(app_dir), policy.package)
    except SyntaxError as e:
        return [_syntax_finding(Path(app_dir), e)]
    try:
        found = all_findings(view, policy)
    except Exception:
        if strict:
            raise
        log.exception("pycheck failed on %s", app_dir)
        return []
    return [f for f in found if f.key not in policy.baseline]


def all_findings(view: AppView, policy: Policy) -> list[Finding]:
    """Every finding, the baseline included."""
    ctx = _Ctx(view, policy)
    for check in (
        check_ownership,
        check_account_writes,
        check_private,
        check_bounds,
        check_route_order,
        check_error_conventions,
        check_datetimes,
    ):
        check(ctx)
    seen: set[str] = set()
    out = []
    for f in sorted(ctx.found, key=lambda f: (f.file, f.line, f.code, f.key)):
        if f.key not in seen:
            seen.add(f.key)
            out.append(f)
    return out


def _syntax_finding(app_dir: Path, e: SyntaxError) -> Finding:
    rel = e.filename or "?"
    try:
        rel = Path(rel).resolve().relative_to(Path(app_dir).resolve()).as_posix()
    except ValueError:
        pass
    return Finding(
        "PY800",
        rel,
        e.lineno or 1,
        f"the file does not parse: {e.msg}",
        "fix the syntax error; the app cannot start",
        key=f"PY800|{rel}",
    )


@dataclass
class _Ctx:
    view: AppView
    policy: Policy

    def __post_init__(self) -> None:
        self.found: list[Finding] = []

    def add(self, code: str, module: str, line: int, message: str, hint: str, key: str) -> None:
        self.found.append(
            Finding(code, self.view.file(module), line, message, hint, key=f"{code}|{key}")
        )


# ---- shared helpers ---------------------------------------------------------------------------


def _caller_name(view: AppView, route: Route) -> str:
    callers = sorted(view.callers(route))
    return callers[0] if callers else "current_user"


def _caller_aliases(fn: FunctionNode, callers: set[str]) -> tuple[set[str], set[str]]:
    """(names holding the caller, names holding the caller's id) in ``fn``: the caller
    parameters, ``u = current_user`` and ``uid = current_user.id``."""
    users, ids = set(callers), set()
    for _ in range(2):
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
                continue
            target, value = node.targets[0], node.value
            if not isinstance(target, ast.Name):
                continue
            if isinstance(value, ast.Name) and value.id in users:
                users.add(target.id)
            elif _is_id_of(value, users):
                ids.add(target.id)
    return users, ids


def _is_id_of(e: ast.expr, users: set[str]) -> bool:
    return (
        isinstance(e, ast.Attribute)
        and e.attr == "id"
        and isinstance(e.value, ast.Name)
        and e.value.id in users
    )


def _is_caller_id(e: ast.expr, users: set[str], ids: set[str]) -> bool:
    return _is_id_of(e, users) or (isinstance(e, ast.Name) and e.id in ids)


def _admin_test(test: ast.expr, users: set[str], column: str) -> bool:
    """``not <caller>.<column>`` (``<caller>.<column> is False`` / ``== False``), alone or
    as an ``or`` operand."""
    tests = test.values if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or) else [test]
    for t in tests:
        if (
            isinstance(t, ast.UnaryOp)
            and isinstance(t.op, ast.Not)
            and _flag(t.operand, users, column)
        ):
            return True
        if (
            isinstance(t, ast.Compare)
            and len(t.ops) == 1
            and isinstance(t.ops[0], ast.Is | ast.Eq)
            and isinstance(t.comparators[0], ast.Constant)
            and t.comparators[0].value is False
            and _flag(t.left, users, column)
        ):
            return True
    return False


def _flag(e: ast.expr, users: set[str], column: str) -> bool:
    return (
        isinstance(e, ast.Attribute)
        and e.attr == column
        and isinstance(e.value, ast.Name)
        and e.value.id in users
    )


def _admin_guarded(view: AppView, route: Route, fn: FunctionNode, policy: Policy) -> bool:
    """Whether the route is admin-only: by dependency, or by an ``if not
    <caller>.<admin flag>: raise`` in its body."""
    if route.auth == "Admin":
        return True
    if policy.admin is None:
        return False
    users, _ = _caller_aliases(fn, view.callers(route))
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.If)
            and _admin_test(node.test, users, policy.admin.column)
            and raises_http(node.body) is not None
        ):
            return True
    return False


def _row_var(table: str) -> str:
    return table.lower() if table.isidentifier() else "row"


def _reachable(
    view: AppView, module: str, fn: FunctionNode, cls: str | None = None
) -> Iterator[tuple[str, FunctionNode]]:
    """``fn`` and the package functions / methods it calls, to :data:`HELPER_DEPTH`."""
    seen: set[int] = set()
    stack = [(module, fn, cls, 0)]
    while stack:
        mod, f, c, d = stack.pop()
        if id(f) in seen:
            continue
        seen.add(id(f))
        yield mod, f
        if d >= HELPER_DEPTH:
            continue
        local = view.local_classes(mod, f, c)
        for node in ast.walk(f):
            if isinstance(node, ast.Call):
                callee = view.callee(mod, node, local)
                if callee is not None and not _is_route_fn(callee.fn):
                    stack.append((callee.module, callee.fn, callee.cls, d + 1))


def _is_route_fn(fn: FunctionNode) -> bool:
    return any(
        isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and d.func.attr in ("get", "post", "put", "patch", "delete", "api_route")
        for d in fn.decorator_list
    )


# ---- PY801 ownership --------------------------------------------------------------------------


def _live_owner_evidence(
    view: AppView, route: Route, fn: FunctionNode, policy: Policy, dead: set[int]
) -> set[str]:
    """Owned tables whose owner column the route's own (live) code compares with, or
    sets from, the caller's id -- through aliases too (``uid = current_user.id``)."""
    users, ids = _caller_aliases(fn, view.callers(route))
    out: set[str] = set()

    def owner_tables(e: ast.expr) -> set[str]:
        if (
            isinstance(e, ast.Call)
            and e.args
            and (view.resolve(route.module, e.func) or "").endswith("col")
        ):
            e = e.args[0]
        if not isinstance(e, ast.Attribute):
            return set()
        table = view.table_of(route.module, e.value)
        if table is not None:
            return {table} if policy.owners.get(table) == e.attr else set()
        return {t for t, col in policy.owners.items() if col == e.attr and t in route.tables}

    for node in ast.walk(fn):
        if getattr(node, "lineno", None) in dead:
            continue
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            a, b = node.left, node.comparators[0]
            if _is_caller_id(a, users, ids):
                out |= owner_tables(b)
            if _is_caller_id(b, users, ids):
                out |= owner_tables(a)
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg and _is_caller_id(kw.value, users, ids):
                    out |= {
                        t for t, col in policy.owners.items() if col == kw.arg and t in route.tables
                    }
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values, strict=True):
                if isinstance(k, ast.Constant) and v is not None and _is_caller_id(v, users, ids):
                    out |= {
                        t
                        for t, col in policy.owners.items()
                        if col == k.value and t in route.tables
                    }
        elif isinstance(node, ast.Assign) and _is_caller_id(node.value, users, ids):
            for target in node.targets:
                out |= owner_tables(target)
    return out


def owner_evidence(view: AppView, route: Route, fn: FunctionNode, policy: Policy) -> set[str]:
    """The owned tables the route checks (or sets) against the caller, in live code."""
    dead = dead_lines(fn)
    span = range(fn.lineno, (fn.end_lineno or fn.lineno) + 1)
    out = {
        u.table
        for u in route.owner_uses
        if policy.owners.get(u.table) == u.column and not (u.line in span and u.line in dead)
    }
    return out | _live_owner_evidence(view, route, fn, policy, dead)


def check_ownership(ctx: _Ctx) -> None:
    view, policy = ctx.view, ctx.policy
    for route in view.model.routes:
        if route.auth not in ("Customer", "Public"):
            continue
        fn = view.route_fn(route)
        if fn is None or _admin_guarded(view, route, fn, policy):
            continue
        evidence = owner_evidence(view, route, fn, policy)
        for table in sorted(route.tables):
            missing: list[tuple[str, str]] = []  # (table the caller is not checked on, via)
            if table in policy.owners:
                if table not in evidence:
                    missing.append((table, ""))
            elif table in policy.owner_via:
                parent = policy.owner_via[table][1]
                if parent not in evidence and table not in evidence:
                    missing.append((parent, table))
            elif table in policy.links:
                for _, parent in policy.links[table]:
                    if parent not in evidence:
                        missing.append((parent, table))
            for owned, via in missing:
                _ownership_finding(ctx, route, owned, via)


def _ownership_finding(ctx: _Ctx, route: Route, owned: str, via: str) -> None:
    view, policy = ctx.view, ctx.policy
    col = policy.owners.get(owned, "owner_id")
    caller = _caller_name(view, route)
    if via:
        message = (
            f"{route.method} {route.path} ({route.name}) reads {via} rows, which link {owned} "
            f"rows, without checking that those {owned} rows are the caller's "
            f"({owned}.{col})"
        )
        hint = (
            f"join {owned} and add `.where({owned}.{col} == {caller}.id)` for the {owned} rows "
            f"{via} links to"
        )
    elif route.auth == "Public":
        message = (
            f"{route.method} {route.path} ({route.name}) reads or writes {owned} rows, which "
            f"belong to their owner ({owned}.{col}), with no signed-in caller at all"
        )
        hint = (
            f"take the caller (`{caller}: CurrentUser`) and filter by it: "
            f"`.where({owned}.{col} == {caller}.id)`"
        )
    else:
        message = (
            f"{route.method} {route.path} ({route.name}) reads or writes {owned} rows "
            f"without checking that they are the caller's ({owned}.{col})"
        )
        hint = f"filter by the caller: add `.where({owned}.{col} == {caller}.id)`"
    if policy.admin is not None:
        hint += f" unless `{caller}.{policy.admin.column}`"
    answer = policy.not_owned.get(owned)
    if answer is not None and not via:
        row = _row_var(owned)
        shown = f'{answer.status} "{answer.detail}"' if answer.detail else str(answer.status)
        hint += f"; for a single row, answer {shown} when `{row}.{col} != {caller}.id`"
    example = policy.examples.get(owned)
    if example and example != route.name:
        hint += f" (as `{example}` does)"
    if policy.admin is not None:
        hint += (
            f"; or, if only admins may call it, add "
            f"`dependencies=[Depends({policy.admin.dependency})]`"
        )
    ctx.add("PY801", route.module, route.line, message, hint, f"{route_key(route)}|{owned}|{via}")


def check_account_writes(ctx: _Ctx) -> None:
    """A Customer route writing a users-table row it fetched by a request value."""
    view, policy = ctx.view, ctx.policy
    users_table = policy.users_table
    if users_table is None:
        return
    for route in view.model.routes:
        if route.auth != "Customer":
            continue
        fn = view.route_fn(route)
        if fn is None or _admin_guarded(view, route, fn, policy):
            continue
        mod = route.module
        users, ids = _caller_aliases(fn, view.callers(route))
        fetched: dict[str, ast.expr | None] = {}
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            found = _fetch_of(view, mod, node.value, users_table)
            if found is False:
                continue
            if found is not None and (
                _is_caller_id(found, users, ids)
                or (isinstance(found, ast.Name) and found.id in users)
            ):
                continue
            fetched[target.id] = found if found is not True else None  # type: ignore[assignment]
        if not fetched:
            continue
        checked = _checked_against_caller(fn, set(fetched), users, ids, fetched)
        for node in _in_order(fn):
            name = _account_write(node, set(fetched) - checked)
            if name is None:
                continue
            caller = _caller_name(view, route)
            hint = (
                f"only the account's owner or an admin may change it: check "
                f"`if {name}.id != {caller}.id: raise HTTPException(status_code=403, ...)`"
            )
            if policy.admin is not None:
                hint = (
                    f"only admins may change another user's account: add "
                    f"`dependencies=[Depends({policy.admin.dependency})]` to the route, or "
                    + hint.split(": ", 1)[1]
                )
            ctx.add(
                "PY801",
                mod,
                node.lineno,
                f"{route.method} {route.path} ({route.name}) changes the {users_table} row "
                f"'{name}', looked up from the request, without an admin or owner check",
                hint,
                f"{route_key(route)}|{users_table}|account",
            )
            break


def _fetch_of(view: AppView, mod: str, value: ast.expr, table: str) -> ast.expr | bool | None:
    """For a lookup of one ``table`` row: its key expression (``session.get(User, k)``),
    True for a query (``session.exec(select(User)...).first()``); False otherwise."""
    if not isinstance(value, ast.Call):
        return False
    func = value.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "get"
        and len(value.args) >= 2
        and view.table_of(mod, value.args[0]) == table
    ):
        return value.args[1]
    if isinstance(func, ast.Attribute) and func.attr in ("first", "one", "one_or_none"):
        for node in ast.walk(value):
            if (
                isinstance(node, ast.Call)
                and (view.resolve(mod, node.func) or "").endswith("select")
                and node.args
                and view.table_of(mod, node.args[0]) == table
            ):
                return True
    return False


def _checked_against_caller(
    fn: FunctionNode,
    names: set[str],
    users: set[str],
    ids: set[str],
    keys: dict[str, ast.expr | None],
) -> set[str]:
    """The fetched names an ``if <row>.id != <caller>.id: raise`` (or its key compared
    the same way) guards."""
    out: set[str] = set()
    key_names = {n: k.id for n, k in keys.items() if isinstance(k, ast.Name)}
    for node in ast.walk(fn):
        if not (isinstance(node, ast.If) and raises_http(node.body) is not None):
            continue
        for cmp in ast.walk(node.test):
            if not (
                isinstance(cmp, ast.Compare)
                and len(cmp.ops) == 1
                and isinstance(cmp.ops[0], ast.NotEq | ast.IsNot)
            ):
                continue
            sides = (cmp.left, cmp.comparators[0])
            for a, b in (sides, sides[::-1]):
                if not (
                    _is_caller_id(b, users, ids) or (isinstance(b, ast.Name) and b.id in users)
                ):
                    continue
                for n in names:
                    if (isinstance(a, ast.Name) and a.id in (n, key_names.get(n))) or (
                        _is_id_of(a, {n})
                    ):
                        out.add(n)
    return out


def _in_order(fn: FunctionNode) -> list[ast.AST]:
    return sorted(
        (n for n in ast.walk(fn) if hasattr(n, "lineno")),
        key=lambda n: (n.lineno, n.col_offset),  # type: ignore[attr-defined]
    )


def _account_write(node: ast.AST, names: set[str]) -> str | None:
    if isinstance(node, ast.Assign | ast.AugAssign):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in targets:
            if (
                isinstance(t, ast.Attribute)
                and isinstance(t.value, ast.Name)
                and t.value.id in names
            ):
                return t.value.id
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "delete" and node.args and isinstance(node.args[0], ast.Name):
            if node.args[0].id in names:
                return node.args[0].id
        if (
            node.func.attr == "sqlmodel_update"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in names
        ):
            return node.func.value.id
    return None


# ---- PY802 private columns --------------------------------------------------------------------


def _maps_to(view: AppView, model: str, table: TableModel) -> bool:
    """A response model describes ``table``'s rows (as the importer's profile reads it):
    the table itself, a shared base class, or the table's name as its prefix."""
    if model == table.name:
        return True
    m = view.models.get(model)
    if m is None:
        return False
    if set(m.bases) & set(table.bases):
        return True
    rest = model[len(table.name) :]
    return model.startswith(table.name) and not rest[:1].islower()


def _envelope_item(model: DataModel) -> str | None:
    lists = [f for f in model.fields if f.type == "List" and f.item]
    return lists[0].item if len(lists) == 1 else None


def _response_model_expr(fn: FunctionNode) -> tuple[bool, ast.expr | None]:
    """(declared, expression) of the route decorator's ``response_model``."""
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Call):
            for kw in dec.keywords:
                if kw.arg == "response_model":
                    return True, kw.value
    return False, None


def _model_names(expr: ast.expr | None) -> list[str]:
    """``X``, ``list[X]``, ``X | None`` -> the class names inside."""
    if expr is None:
        return []
    if isinstance(expr, ast.Name):
        return [expr.id]
    if isinstance(expr, ast.Attribute):
        return [expr.attr]
    if isinstance(expr, ast.Subscript):
        inner = expr.slice.elts if isinstance(expr.slice, ast.Tuple) else [expr.slice]
        return [n for e in inner for n in _model_names(e)]
    if isinstance(expr, ast.BinOp):
        return _model_names(expr.left) + _model_names(expr.right)
    return []


def _public_model(view: AppView, table: TableModel, private: set[str]) -> str | None:
    """A response model of the app mapping to ``table`` without its private columns."""
    for name, m in sorted(view.models.items()):
        if _maps_to(view, name, table) and not private & {f.name for f in m.fields}:
            if name.endswith(("Public", "Response", "Out", "Read")):
                return name
    return None


def private_leaks(
    view: AppView, policy: Policy, *, dicts_only: bool = False
) -> Iterator[tuple[Route, str, int, str, str, str]]:
    """(route, module, line, column, how, table or "") for each private column reaching a
    non-admin response."""
    private = {t: set(cols) for t, cols in policy.private.items() if cols and t in view.tables}
    columns = {c for cols in private.values() for c in cols}
    if not columns:
        return
    for route in view.model.routes:
        if route.auth not in ("Customer", "Public"):
            continue
        fn = view.route_fn(route)
        if fn is None:
            continue
        if not dicts_only:
            declared, expr = _response_model_expr(fn)
            names = (
                _model_names(expr)
                if declared
                else ([route.response_model] if route.response_model else [])
            )
            for name in names:
                if name in private:
                    for c in sorted(private[name]):
                        yield route, route.module, route.line, c, f"response_model={name}", name
                    continue
                model = view.models.get(name)
                if model is None:
                    continue
                shown = [model]
                item = _envelope_item(model)
                if item in view.models:
                    shown.append(view.models[item])
                for m in shown:
                    fields = {f.name for f in m.fields}
                    for t, cols in private.items():
                        if _maps_to(view, m.name, view.tables[t]):
                            for c in sorted(cols & fields):
                                yield route, route.module, route.line, c, f"{m.name}.{c}", t
            if not declared and route.response_model is None and route.returns in private:
                t = route.returns
                for c in sorted(private[t]):
                    yield route, route.module, route.line, c, f"a {t} row", t
        for mod, f in _reachable(view, route.module, fn):
            for node in ast.walk(f):
                if isinstance(node, ast.Dict):
                    for v in node.values:
                        if isinstance(v, ast.Attribute) and v.attr in columns:
                            yield route, mod, v.lineno, v.attr, "dict", ""


def check_private(ctx: _Ctx) -> None:
    view, policy = ctx.view, ctx.policy
    for route, mod, line, col, how, table in private_leaks(view, policy):
        if how == "dict":
            message = (
                f"{route.method} {route.path} ({route.name}) puts the private column {col} "
                "in its response"
            )
            hint = f"leave `{col}` out of the returned dict; only admin routes may return it"
        else:
            message = (
                f"{route.method} {route.path} ({route.name}) answers with {how}, which "
                f"includes the private column {table}.{col}"
            )
            public = _public_model(view, view.tables[table], set(policy.private.get(table, ())))
            hint = (
                f"use `response_model={public}`"
                if public
                else f"use a response model without `{col}`"
            ) + f"; `{col}` never reaches a non-admin response"
        ctx.add("PY802", mod, line, message, hint, f"{route_key(route)}|{col}|{how}")


# ---- PY803 unbounded inputs ---------------------------------------------------------------------

STR_BOUND_MARKS = ("max_length", "constr", "StringConstraints", "Literal")
NUM_BOUND_MARKS = ("le=", "lt=", "conint", "confloat", "Literal")


def _param_bounded(arg: ast.arg, default: ast.expr | None, kind: str) -> bool:
    """A scalar route parameter's own bounds (``Query(le=100)``, ``Annotated[int,
    Query(le=100)]``, ``conint(le=...)``, ``Literal[...]``)."""
    keys = ("max_length",) if kind == "Str" else ("le", "lt")
    parts = [default, arg.annotation]
    for part in parts:
        if part is None:
            continue
        for node in ast.walk(part):
            if isinstance(node, ast.Call) and any(k.arg in keys for k in node.keywords):
                return True
        text = ast.unparse(part)
        marks = STR_BOUND_MARKS if kind == "Str" else NUM_BOUND_MARKS
        if any(m in text for m in marks if "=" not in m):
            return True
    return False


def _args_with_defaults(fn: FunctionNode) -> list[tuple[ast.arg, ast.expr | None]]:
    a = fn.args
    positional = [*a.posonlyargs, *a.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(a.defaults)) + list(
        a.defaults
    )
    return [*zip(positional, defaults, strict=True), *zip(a.kwonlyargs, a.kw_defaults, strict=True)]


def _field_bounded(view: AppView, model_name: str, fs: FieldSpec) -> bool:
    if fs.type not in ("Str", "Int", "Float") or fs.choices:
        return True
    if fs.type == "Str":
        if fs.max_length is not None or any(m in fs.annotation for m in STR_BOUND_MARKS):
            return True
    elif fs.le is not None or fs.lt is not None or any(m in fs.annotation for m in NUM_BOUND_MARKS):
        return True
    return _validated(view, model_name, fs.name)


def _validated(view: AppView, model_name: str, field_name: str) -> bool:
    """A ``@field_validator("<field>")`` / ``@validator`` on the model or a package base."""
    todo = [q for q in view.classes if q.endswith(f":{model_name}")]
    seen: set[str] = set()
    while todo:
        qual = todo.pop()
        if qual in seen or qual not in view.classes:
            continue
        seen.add(qual)
        mod, node = view.classes[qual]
        for stmt in node.body:
            if not isinstance(stmt, FunctionNode):
                continue
            for dec in stmt.decorator_list:
                if isinstance(dec, ast.Call) and any(
                    isinstance(a, ast.Constant) and a.value in (field_name, "*") for a in dec.args
                ):
                    return True
        todo += [q for b in node.bases if (q := view.resolve(mod, b))]
    return False


@dataclass(frozen=True)
class _Written:
    param: str  # the route parameter
    field: str | None  # the body model's field, or None for a scalar parameter
    module: str
    line: int  # where the route writes it (or calls the helper that does)


def _body_fields(view: AppView, model: str) -> dict[str, FieldSpec]:
    if model in view.models:
        return {f.name: f for f in view.models[model].fields}
    if model in view.tables:
        return {f.name: f for f in view.tables[model].columns}
    return {}


def _written(
    view: AppView,
    mod: str,
    fn: FunctionNode,
    cls: str | None,
    bodies: dict[str, tuple[str, str]],  # local name -> (route param, model)
    scalars: dict[str, str],  # local name -> route param
    line: int | None,
    depth: int,
    out: list[_Written],
) -> None:
    """The request values ``fn`` writes to a table (following helpers it hands them)."""
    column_names = {c.name for t in view.tables.values() for c in t.columns}
    dumps: dict[str, str] = {}  # name -> body local it is a model_dump() of
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and (b := _dump_of(node.value, bodies)) is not None
        ):
            dumps[node.targets[0].id] = b

    def at(node: ast.AST) -> int:
        return line if line is not None else node.lineno  # type: ignore[attr-defined]

    def whole(b: str, node: ast.AST, table: str | None) -> None:
        param, model = bodies[b]
        cols = (
            {c.name for c in view.tables[table].columns} if table in view.tables else column_names
        )
        for f in _body_fields(view, model):
            if f in cols:
                out.append(_Written(param, f, mod, at(node)))

    def values(e: ast.expr, node: ast.AST) -> None:
        for sub in ast.walk(e):
            if (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id in bodies
            ):
                param, model = bodies[sub.value.id]
                if sub.attr in _body_fields(view, model):
                    out.append(_Written(param, sub.attr, mod, at(node)))
            elif isinstance(sub, ast.Name) and sub.id in scalars:
                out.append(_Written(scalars[sub.id], None, mod, at(node)))

    local = view.local_classes(mod, fn, cls)
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            table = view.table_of(mod, func)
            if isinstance(func, ast.Attribute) and func.attr in (
                "model_validate",
                "from_orm",
                "model_construct",
            ):
                t = view.table_of(mod, func.value)
                if t is not None and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Name) and arg.id in bodies:
                        whole(arg.id, node, t)
                    elif isinstance(arg, ast.Name) and arg.id in dumps:
                        whole(dumps[arg.id], node, t)
                    elif (b := _dump_of(arg, bodies)) is not None:
                        whole(b, node, t)
                    for kw in node.keywords:
                        if kw.arg == "update":
                            values(kw.value, node)
                continue
            if isinstance(func, ast.Attribute) and func.attr == "sqlmodel_update" and node.args:
                arg = node.args[0]
                b = dumps.get(arg.id) if isinstance(arg, ast.Name) else _dump_of(arg, bodies)
                if isinstance(arg, ast.Name) and arg.id in bodies:
                    b = arg.id
                if b is not None:
                    whole(b, node, None)
                continue
            if table is not None:
                for kw in node.keywords:
                    if kw.arg is None:
                        b = (
                            _dump_of(kw.value, bodies)
                            if not isinstance(kw.value, ast.Name)
                            else dumps.get(kw.value.id)
                        )
                        if b is not None:
                            whole(b, node, table)
                    elif kw.arg in {c.name for c in view.tables[table].columns}:
                        values(kw.value, node)
                continue
            if depth < HELPER_DEPTH:
                callee = view.callee(mod, node, local)
                if callee is None or _is_route_fn(callee.fn):
                    continue
                inner_b: dict[str, tuple[str, str]] = {}
                inner_s: dict[str, str] = {}
                for pname, arg in bind_args(node, callee).items():
                    if isinstance(arg, ast.Name) and arg.id in bodies:
                        inner_b[pname] = bodies[arg.id]
                    elif isinstance(arg, ast.Name) and arg.id in scalars:
                        inner_s[pname] = scalars[arg.id]
                if inner_b or inner_s:
                    _written(
                        view,
                        callee.module,
                        callee.fn,
                        callee.cls,
                        inner_b,
                        inner_s,
                        at(node),
                        depth + 1,
                        out,
                    )
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr in column_names
                    and not (isinstance(target.value, ast.Name) and target.value.id in bodies)
                ):
                    values(node.value, node)


def _dump_of(e: ast.expr, bodies: dict[str, tuple[str, str]]) -> str | None:
    """``b.model_dump(...)`` / ``b.dict(...)`` of a body name -> that name."""
    if (
        isinstance(e, ast.Call)
        and isinstance(e.func, ast.Attribute)
        and e.func.attr in ("model_dump", "dict")
        and isinstance(e.func.value, ast.Name)
        and e.func.value.id in bodies
    ):
        return e.func.value.id
    return None


def _limit_uses(
    view: AppView,
    mod: str,
    fn: FunctionNode,
    cls: str | None,
    names: dict[str, str],
    line: int | None,
    depth: int,
    out: dict[str, int],
) -> None:
    """Route int parameters (``names``: local -> param) used as ``.limit(<name>)``."""
    local = view.local_classes(mod, fn, cls)
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "limit" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Name) and arg.id in names:
                out.setdefault(names[arg.id], line if line is not None else node.lineno)
            continue
        if depth < HELPER_DEPTH:
            callee = view.callee(mod, node, local)
            if callee is None or _is_route_fn(callee.fn):
                continue
            inner = {
                p: names[a.id]
                for p, a in bind_args(node, callee).items()
                if isinstance(a, ast.Name) and a.id in names
            }
            if inner:
                _limit_uses(
                    view,
                    callee.module,
                    callee.fn,
                    callee.cls,
                    inner,
                    line if line is not None else node.lineno,
                    depth + 1,
                    out,
                )


def check_bounds(ctx: _Ctx) -> None:
    view, policy = ctx.view, ctx.policy
    for route in view.model.routes:
        fn = view.route_fn(route)
        if fn is None:
            continue
        mod = route.module
        params = {p.name: p for p in route.params}
        args = {a.arg: (a, d) for a, d in _args_with_defaults(fn)}
        # limits
        ints = {
            name: name
            for name, p in params.items()
            if p.source == "query"
            and p.type == "Int"
            and not p.choices
            and name in args
            and not _param_bounded(*args[name], "Int")
        }
        uses: dict[str, int] = {}
        _limit_uses(view, mod, fn, None, ints, None, 0, uses)
        for name, line in sorted(uses.items()):
            ctx.add(
                "PY803",
                mod,
                line,
                f"{route.method} {route.path} ({route.name}) uses the query parameter "
                f"'{name}' as a row limit, but it has no upper bound",
                f"bound it: `{name}: Annotated[int, Query(ge=1, le={policy.page_limit_max})]"
                f" = ...` (or `.limit(min({name}, {policy.page_limit_max}))`)",
                # keyed by the name, not the route: a limit the original leaves unbounded
                # is the app's convention for every list route
                f"limit|{name}",
            )
        # writes
        bodies = {
            name: (name, p.type)
            for name, p in params.items()
            if p.source == "body" and p.type and (p.type in view.models or p.type in view.tables)
        }
        scalars = {
            name: name
            for name, p in params.items()
            if p.source in ("query", "body")
            and p.type in ("Str", "Int", "Float")
            and not p.choices
            and name in args
            and not _param_bounded(*args[name], p.type)
        }
        written: list[_Written] = []
        _written(view, mod, fn, None, bodies, scalars, None, 0, written)
        reported: set[tuple[str, str | None]] = set()
        for w in sorted(written, key=lambda w: w.line):
            if (w.param, w.field) in reported:
                continue
            if w.field is not None:
                model = bodies[w.param][1]
                fs = _body_fields(view, model).get(w.field)
                if fs is None or _field_bounded(view, model, fs):
                    continue
                reported.add((w.param, w.field))
                example = (
                    f"{w.field}: str = Field(max_length=255)"
                    if fs.type == "Str"
                    else f"{w.field}: {fs.type.lower()} = Field(ge=0, le=1000)"
                )
                ctx.add(
                    "PY803",
                    mod,
                    w.line,
                    f"{route.method} {route.path} ({route.name}) writes the request field "
                    f"{model}.{w.field} to the database, but it is unbounded",
                    f"give it a bound in {model}: `{example}`",
                    # keyed by the model field: one the original writes unbounded is the
                    # app's accepted convention, whichever route writes it
                    f"field|{model}.{w.field}",
                )
            else:
                p = params[w.param]
                reported.add((w.param, None))
                bound = "max_length=255" if p.type == "Str" else "ge=0, le=1000"
                kind = {"Str": "str", "Int": "int", "Float": "float"}[p.type or "Str"]
                ctx.add(
                    "PY803",
                    mod,
                    w.line,
                    f"{route.method} {route.path} ({route.name}) writes the request parameter "
                    f"'{w.param}' to the database, but it is unbounded",
                    f"bound it: `{w.param}: Annotated[{kind}, Query({bound})]` (or a body "
                    f"model field with `Field({bound})`)",
                    f"{route_key(route)}|{w.param}",
                )


# ---- PY804 route order -----------------------------------------------------------------------


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _is_param(seg: str) -> bool:
    return seg.startswith("{") and seg.endswith("}")


def swallows(first: str, later: str) -> bool:
    """Whether a request for the literal parts of ``later`` matches ``first``."""
    a, b = _segments(first), _segments(later)
    if len(a) != len(b) or a == b:
        return False
    literal_under_param = False
    for x, y in zip(a, b, strict=True):
        if _is_param(x):
            if ":path}" in x:
                return False
            if not _is_param(y):
                literal_under_param = True
        elif x != y:
            return False
    return literal_under_param


def _decorator_line(fn: FunctionNode | None, fallback: int) -> int:
    if fn is None or not fn.decorator_list:
        return fallback
    return min(d.lineno for d in fn.decorator_list)


def check_route_order(ctx: _Ctx) -> None:
    view = ctx.view
    groups: dict[tuple[str, str | None, str], list[Route]] = {}
    for route in view.model.routes:
        groups.setdefault((route.module, route.enclosing, route.method), []).append(route)
    for routes in groups.values():
        routes.sort(key=lambda r: r.line)
        for j, later in enumerate(routes):
            for first in routes[:j]:
                if not swallows(first.path, later.path):
                    continue
                line = _decorator_line(view.route_fn(later), later.line)
                ctx.add(
                    "PY804",
                    later.module,
                    line,
                    f"{later.method} {later.path} ({later.name}) is declared after "
                    f"{first.name} ({first.method} {first.path}), which matches its requests "
                    "first",
                    f"declare `{later.name}` before `{first.name}`: FastAPI tries routes in "
                    "declaration order",
                    f"{later.method} {later.path}|{first.path}",
                )
                break


# ---- PY805 error conventions -------------------------------------------------------------------


@dataclass(frozen=True)
class OwnerGuard:
    """An ``if`` whose test says a row is not the caller's, and what its body raises."""

    module: str
    function: str
    line: int
    tables: tuple[str, ...]  # the owned tables the row may be of
    row: str  # the row expression's source (``item``)
    status: int | None
    detail: str | None


def owner_guards(view: AppView, policy: Policy) -> Iterator[OwnerGuard]:
    owner_cols = set(policy.owners.values())
    if not owner_cols:
        return
    route_by_fn = {(r.module, r.name, r.line): r for r in view.model.routes}
    for mod, fn, _ in view.functions():
        route = route_by_fn.get((mod, fn.name, fn.lineno))
        callers = view.callers(route) if route is not None else set()
        for a, _d in _args_with_defaults(fn):
            ann = view.table_of(mod, a.annotation) if a.annotation is not None else None
            if a.arg in CALLER_PARAM_NAMES or (ann is not None and ann == policy.users_table):
                callers.add(a.arg)
        users, ids = _caller_aliases(fn, callers)
        ids |= {a.arg for a, _ in _args_with_defaults(fn) if a.arg in CALLER_ID_PARAM_NAMES}
        typed = _row_types(view, mod, fn)
        for node in ast.walk(fn):
            if not isinstance(node, ast.If):
                continue
            for row, col in _not_owner_tests(node.test, users, ids, owner_cols):
                tables = _row_tables(view, mod, row, col, typed, policy)
                if not tables:
                    continue
                status, detail = http_error(mod, view, raises_http(node.body))
                yield OwnerGuard(
                    mod, fn.name, node.lineno, tables, ast.unparse(row), status, detail
                )


def _not_owner_tests(
    test: ast.expr, users: set[str], ids: set[str], owner_cols: set[str]
) -> Iterator[tuple[ast.expr, str]]:
    """(row expression, owner column) for each ``<row>.<owner col> != <caller id>`` in
    ``test`` (also ``is not`` and ``not (<row>.<col> == <caller id>)``)."""
    for node in ast.walk(test):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            inner = node.operand
            if (
                isinstance(inner, ast.Compare)
                and len(inner.ops) == 1
                and isinstance(inner.ops[0], ast.Eq)
            ):
                yield from _owner_side(inner, users, ids, owner_cols)
        if (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.NotEq | ast.IsNot)
        ):
            yield from _owner_side(node, users, ids, owner_cols)


def _owner_side(
    cmp: ast.Compare, users: set[str], ids: set[str], owner_cols: set[str]
) -> Iterator[tuple[ast.expr, str]]:
    a, b = cmp.left, cmp.comparators[0]
    for x, y in ((a, b), (b, a)):
        if isinstance(x, ast.Attribute) and x.attr in owner_cols and _is_caller_id(y, users, ids):
            yield x.value, x.attr


def _row_types(view: AppView, mod: str, fn: FunctionNode) -> dict[str, str]:
    """Names in ``fn`` holding one row of a known table."""
    out: dict[str, str] = {}
    for a, _ in _args_with_defaults(fn):
        t = view.table_of(mod, a.annotation) if a.annotation is not None else None
        if t is not None:
            out[a.arg] = t
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target, value = node.targets[0], node.value
        if isinstance(target, ast.Name) and isinstance(value, ast.Call):
            for t in view.tables:
                if _fetch_of(view, mod, value, t) is not False:
                    out[target.id] = t
                    break
    return out


def _row_tables(
    view: AppView, mod: str, row: ast.expr, col: str, typed: dict[str, str], policy: Policy
) -> tuple[str, ...]:
    if isinstance(row, ast.Name) and row.id in typed:
        t = typed[row.id]
        return (t,) if policy.owners.get(t) == col else ()
    return tuple(sorted(t for t, c in policy.owners.items() if c == col))


def check_error_conventions(ctx: _Ctx) -> None:
    view, policy = ctx.view, ctx.policy
    for g in owner_guards(view, policy):
        if g.status is None:
            continue
        answers = {policy.not_owned[t] for t in g.tables if t in policy.not_owned}
        if len(answers) != 1:
            continue
        (answer,) = answers
        if g.status == answer.status and (answer.detail is None or g.detail == answer.detail):
            continue
        shown = f'{answer.status} "{answer.detail}"' if answer.detail else str(answer.status)
        got = f'{g.status} "{g.detail}"' if g.detail else str(g.status)
        table = g.tables[0] if len(g.tables) == 1 else "/".join(g.tables)
        ctx.add(
            "PY805",
            g.module,
            g.line,
            f"{g.function} answers another user's {table} with {got}; this app answers it "
            f"with {shown}",
            f"raise HTTPException(status_code={answer.status}"
            + (f', detail="{answer.detail}"' if answer.detail else ", ...")
            + f") when `{g.row}` is not the caller's -- this app answers {shown}",
            f"{view.file(g.module)}:{g.function}|{table}|{g.status}|{g.detail}",
        )
    admin = policy.admin
    if admin is None or admin.status is None:
        return
    route_by_fn = {(r.module, r.name, r.line): r for r in view.model.routes}
    for mod, fn, _ in view.functions():
        route = route_by_fn.get((mod, fn.name, fn.lineno))
        callers = view.callers(route) if route is not None else set()
        callers |= {a.arg for a, _ in _args_with_defaults(fn) if a.arg in CALLER_PARAM_NAMES}
        users, _ = _caller_aliases(fn, callers)
        for node in ast.walk(fn):
            if not (isinstance(node, ast.If) and _admin_test(node.test, users, admin.column)):
                continue
            status, detail = http_error(mod, view, raises_http(node.body))
            if status is None:
                continue
            if status == admin.status and (admin.detail is None or detail == admin.detail):
                continue
            shown = f'{admin.status} "{admin.detail}"' if admin.detail else str(admin.status)
            got = f'{status} "{detail}"' if detail else str(status)
            ctx.add(
                "PY805",
                mod,
                node.lineno,
                f"{fn.name} refuses a non-admin with {got}; this app's admin check "
                f"({admin.dependency}) answers {shown}",
                f"use the app's admin check: `dependencies=[Depends({admin.dependency})]` on "
                f"the route (it answers {shown}), or raise exactly that",
                f"{view.file(mod)}:{fn.name}|admin|{status}|{detail}",
            )


def derive_not_owned(view: AppView, policy: Policy) -> dict[str, ErrorAnswer]:
    """table -> the original's answer to another user's row: the one status its owner
    checks raise; failing any, 404 (with its one detail) when the routes that scope the
    table to the caller raise 404s and never a 403 (an owner-scoped lookup)."""
    guards: dict[str, list[tuple[int, str | None]]] = {}
    for g in owner_guards(view, policy):
        if g.status is None:
            continue
        for t in g.tables:
            guards.setdefault(t, []).append((g.status, g.detail))
    out: dict[str, ErrorAnswer] = {}
    for table in policy.owners:
        seen = guards.get(table, [])
        if seen:
            statuses = {s for s, _ in seen}
            details = {d for _, d in seen}
            if len(statuses) == 1:
                (status,) = statuses
                out[table] = ErrorAnswer(status, details.pop() if len(details) == 1 else None)
            continue
        raised: list[tuple[int | None, str | None]] = []
        for route in view.model.routes:
            fn = view.route_fn(route)
            if (
                fn is None
                or route.auth != "Customer"
                or table not in owner_evidence(view, route, fn, policy)
            ):
                continue
            for mod, f in _reachable(view, route.module, fn):
                for node in ast.walk(f):
                    if isinstance(node, ast.Raise):
                        raised.append(http_error(mod, view, node))
        statuses = {s for s, _ in raised}
        if 403 in statuses or 404 not in statuses:
            continue
        details = {d for s, d in raised if s == 404}
        out[table] = ErrorAnswer(404, details.pop() if len(details) == 1 else None)
    return out


# ---- PY806 naive / aware datetimes -------------------------------------------------------------

AWARE, NAIVE, SQLCOL, RAW = "aware", "naive", "sqlcol", "raw"


def check_datetimes(ctx: _Ctx) -> None:
    view, policy = ctx.view, ctx.policy
    dt_columns = {
        (t.name, c.name): c for t in view.tables.values() for c in t.columns if c.type == "DateTime"
    }
    if not dt_columns:
        return
    naive_names = {
        c for (_, c), spec in dt_columns.items() if policy.naive_datetimes or not spec.tz_aware
    }
    route_by_fn = {(r.module, r.name, r.line): r for r in view.model.routes}
    for mod, fn, _ in view.functions():
        route = route_by_fn.get((mod, fn.name, fn.lineno))
        raw: set[str] = set()
        body_params: set[str] = set()
        if route is not None:
            args = {a.arg: a for a, _ in _args_with_defaults(fn)}
            for p in route.params:
                if p.source == "body":
                    body_params.add(p.name)
                if (
                    p.source in ("query", "body")
                    and p.type == "DateTime"
                    and "Naive" not in p.annotation
                    and p.name in args
                ):
                    raw.add(p.name)
        _DatetimeScan(ctx, view, policy, mod, fn, raw, body_params, dt_columns, naive_names).run()


class _DatetimeScan:
    def __init__(
        self,
        ctx: _Ctx,
        view: AppView,
        policy: Policy,
        mod: str,
        fn: FunctionNode,
        raw: set[str],
        body_params: set[str],
        dt_columns: dict,
        naive_names: set[str],
    ) -> None:
        self.ctx, self.view, self.policy, self.mod, self.fn = ctx, view, policy, mod, fn
        self.raw_params = raw
        self.body_params = body_params
        self.dt_columns = dt_columns
        self.naive_names = naive_names
        self.names: dict[str, str | None] = {p: RAW for p in raw}
        assigned: dict[str, list[ast.expr]] = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        assigned.setdefault(t.id, []).append(node.value)
            elif isinstance(node, ast.AugAssign | ast.AnnAssign) and isinstance(
                node.target, ast.Name
            ):
                assigned.setdefault(node.target.id, []).append(node.value)  # type: ignore[arg-type]
            elif isinstance(node, ast.For | ast.comprehension | ast.With | ast.NamedExpr):
                for t in ast.walk(getattr(node, "target", None) or ast.Pass()):
                    if isinstance(t, ast.Name):
                        assigned.setdefault(t.id, []).append(ast.Constant(None))
        # a reassigned request parameter is converted (or at least no longer raw)
        for p in raw:
            if p in assigned:
                self.names[p] = None
        # a name's kind: what every assignment agrees on (two passes settle chains)
        for _ in range(2):
            for name, values in assigned.items():
                if name in raw:
                    continue
                kinds = {
                    self.kind(v)
                    for v in values
                    if v is not None and not (isinstance(v, ast.Name) and v.id == name)  # x = x
                }
                self.names[name] = kinds.pop() if len(kinds) == 1 else None

    def kind(self, e: ast.expr) -> str | None:
        if isinstance(e, ast.Name):
            return self.names.get(e.id)
        if isinstance(e, ast.Attribute):
            table = self.view.table_of(self.mod, e.value)
            if table is not None:
                stored = (table, e.attr) in self.dt_columns
                return SQLCOL if stored and self.policy.naive_datetimes else None
            # a row's stored datetime (a request body's field is the request's value)
            if e.attr in self.naive_names and not (
                isinstance(e.value, ast.Name) and e.value.id in self.body_params
            ):
                return NAIVE
            return None
        if isinstance(e, ast.Call):
            return self._call_kind(e)
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add | ast.Sub):
            a, b = self.kind(e.left), self.kind(e.right)
            dated = {k for k in (a, b) if k in (AWARE, NAIVE)}
            if len(dated) == 1 and (a is None or b is None):
                return dated.pop()
            return None
        if isinstance(e, ast.IfExp):
            a, b = self.kind(e.body), self.kind(e.orelse)
            return a if a == b else None
        return None

    def _call_kind(self, e: ast.Call) -> str | None:
        func = e.func
        qual = self.view.resolve(self.mod, func) or ""
        if qual.endswith("col") and e.args:
            return SQLCOL if self.kind(e.args[0]) == SQLCOL else None
        if isinstance(func, ast.Attribute):
            if func.attr == "now" and _is_datetime_class(self.view, self.mod, func.value):
                has_tz = e.args or any(k.arg == "tz" for k in e.keywords)
                return AWARE if has_tz and not _none_arg(e) else NAIVE
            if func.attr == "utcnow" and _is_datetime_class(self.view, self.mod, func.value):
                return NAIVE
            if func.attr == "replace":
                for kw in e.keywords:
                    if kw.arg == "tzinfo":
                        is_none = isinstance(kw.value, ast.Constant) and kw.value.value is None
                        return NAIVE if is_none else AWARE
                return self.kind(func.value)
            if func.attr == "astimezone":
                return AWARE
            if func.attr == "fromtimestamp" and _is_datetime_class(self.view, self.mod, func.value):
                has_tz = len(e.args) > 1 or any(k.arg == "tz" for k in e.keywords)
                return AWARE if has_tz else NAIVE
        return None

    def run(self) -> None:
        dead = dead_lines(self.fn)
        for node in ast.walk(self.fn):
            if getattr(node, "lineno", None) in dead:
                continue
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                for op, a, b in zip(node.ops, operands, operands[1:], strict=False):
                    if isinstance(op, ast.Lt | ast.LtE | ast.Gt | ast.GtE):
                        self._pair(node, a, b)
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
                self._pair(node, node.left, node.right)

    def _pair(self, node: ast.AST, a: ast.expr, b: ast.expr) -> None:
        ka, kb = self.kind(a), self.kind(b)
        kinds = {ka, kb}
        line = node.lineno  # type: ignore[attr-defined]
        where = f"{self.view.file(self.mod)}:{self.fn.name}|{ast.unparse(node)}"
        if kinds == {AWARE, NAIVE}:
            naive, aware = (a, b) if ka == NAIVE else (b, a)
            n, w = ast.unparse(naive), ast.unparse(aware)
            self.ctx.add(
                "PY806",
                self.mod,
                line,
                f"'{n}' is a naive datetime (stored datetimes read back without a timezone) "
                f"and '{w}' is timezone-aware: comparing or subtracting them raises TypeError",
                f"make both aware -- `{n}.replace(tzinfo=UTC)` when its tzinfo is None -- or "
                f"compare with a naive UTC value: `datetime.now(UTC).replace(tzinfo=None)`",
                where,
            )
        elif kinds == {SQLCOL, RAW}:
            column, param = (a, b) if ka == SQLCOL else (b, a)
            p, c = ast.unparse(param), ast.unparse(column)
            self.ctx.add(
                "PY806",
                self.mod,
                line,
                f"the request datetime '{p}' may carry any UTC offset, but {c} is stored as "
                "naive UTC: the SQL comparison mixes them",
                f"convert it first: `if {p}.tzinfo is not None: "
                f"{p} = {p}.astimezone(UTC).replace(tzinfo=None)`",
                where,
            )


def _is_datetime_class(view: AppView, mod: str, e: ast.expr) -> bool:
    q = view.resolve(mod, e) or ""
    return (
        q in ("datetime:datetime", "datetime.datetime")
        or (isinstance(e, ast.Attribute) and e.attr == "datetime")
        or q.endswith(":datetime")
    )


def _none_arg(e: ast.Call) -> bool:
    args = [*e.args, *(k.value for k in e.keywords if k.arg == "tz")]
    return bool(args) and all(isinstance(a, ast.Constant) and a.value is None for a in args)


__all__ = [
    "OwnerGuard",
    "all_findings",
    "derive_not_owned",
    "owner_evidence",
    "owner_guards",
    "private_leaks",
    "run_pycheck",
    "swallows",
]
