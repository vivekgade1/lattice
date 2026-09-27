"""P6 — API rules (API profile only): auth, ownership, private columns, paths,
parameter bounds, declared errors, response shapes and the SQL subset.

Runs after P4, reading the types it recorded: a table typed UNKNOWN (already
reported) is skipped, so one mistake yields one diagnostic. Returns the metadata of
every endpoint for lowering (`CheckedProgram.endpoints`).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from lattice.catalog import (
    AGG_FUNCS,
    API_AGG_FUNCS,
    API_ENDPOINT_OPS,
    API_ERRORS,
    API_FN_FUNCS,
    FN_SIGS,
    PAGE_LIMIT_MAX,
    PUBLIC_ROLE,
    TABLE_OPS,
    USER_PARAM,
)
from lattice.check.api_info import (
    EndpointInfo,
    ParamSpec,
    endpoint_decorator,
    endpoint_info,
    parse_decl,
    path_params,
    route_shape,
    user_table,
)
from lattice.check.astutil import endpoints_of, is_constant_expr, literal_value, steps_of
from lattice.check.context import CheckContext
from lattice.check.exprs import did_you_mean
from lattice.typesys import Scalar, TableT, via_parent_key

_WRITES = ("insert", "update", "delete")
_TABLE_ARG_OPS = ("update", "delete")


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _assigns(fn: ast.FunctionDef) -> Iterator[tuple[str, ast.expr]]:
    for stmt in fn.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            if isinstance(target, ast.Name):
                yield target.id, stmt.value


class _Api:
    def __init__(self, module: ast.Module, ctx: CheckContext) -> None:
        assert ctx.profile.api is not None
        self.module = module
        self.ctx = ctx
        self.api = ctx.profile.api
        self.steps = steps_of(module)
        self.subset_checked: set[int] = set()
        self.module_consts: dict[str, object] = {}
        for stmt in module.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and is_constant_expr(stmt.value)
            ):
                self.module_consts.setdefault(stmt.targets[0].id, literal_value(stmt.value))

    def run(self) -> dict[str, EndpointInfo]:
        infos: dict[str, EndpointInfo] = {}
        routes: set[tuple[str, str]] = set()
        for fn in endpoints_of(self.module):
            info = endpoint_info(fn)
            if info is None:
                continue  # P1 reported the declaration
            deco = endpoint_decorator(fn)
            assert deco is not None
            shape = route_shape(info.method, info.path)
            if shape in routes:
                self.ctx.error(
                    "L804",
                    deco,
                    f"another endpoint already handles {info.method} {info.path}",
                    "give each endpoint its own method and path",
                )
            routes.add(shape)
            _Endpoint(self, fn, deco, info).check()
            infos.setdefault(fn.name, info)
        return infos

    def check_subset(self, fn: ast.FunctionDef, *, endpoint: bool) -> None:
        """L841: table ops and expressions outside what the API profile compiles to
        SQL, in an endpoint body and (transitively) in the steps it calls."""
        for _, value in _assigns(fn):
            op = _call_name(value)
            if endpoint and op in TABLE_OPS and op not in API_ENDPOINT_OPS:
                hint = (
                    "read database tables with fetch(Table)"
                    if op == "load"
                    else "return the table instead; the endpoint responds with it"
                )
                self._subset_error(value, f"{op}() is not available in API endpoints", hint)
                continue
            for node in ast.walk(value):
                self._subset_expr(node)
            if op in self.steps and id(self.steps[op]) not in self.subset_checked:
                self.subset_checked.add(id(self.steps[op]))
                self.check_subset(self.steps[op], endpoint=False)

    def _subset_expr(self, node: ast.AST) -> None:
        match node:
            case ast.Call(func=ast.Attribute(value=ast.Name(id="fn"), attr=f)) if (
                f in FN_SIGS and f not in API_FN_FUNCS
            ):
                avail = ", ".join(f"fn.{n}" for n in sorted(API_FN_FUNCS))
                self._subset_error(
                    node, f"fn.{f} is not available in API endpoints", f"available: {avail}"
                )
            case ast.Call(func=ast.Attribute(value=ast.Name(id="agg"), attr=a)) if (
                a in AGG_FUNCS and a not in API_AGG_FUNCS
            ):
                avail = ", ".join(f"agg.{n}" for n in sorted(API_AGG_FUNCS))
                self._subset_error(
                    node, f"agg.{a} is not available in API endpoints", f"available: {avail}"
                )
            case _:
                pass

    def _subset_error(self, node: ast.AST, message: str, hint: str) -> None:
        if id(node) in self.subset_checked:
            return
        self.subset_checked.add(id(node))
        self.ctx.error("L841", node, message, hint)


class _Endpoint:
    def __init__(
        self, owner: _Api, fn: ast.FunctionDef, deco: ast.Call, info: EndpointInfo
    ) -> None:
        self.p = owner
        self.ctx = owner.ctx
        self.api = owner.api
        self.fn = fn
        self.deco = deco
        self.info = info
        self.params = {s.name: s for s in info.params}
        self.admin = info.auth == self.api.admin_role
        self.locals = dict(reversed(list(_assigns(fn))))  # first binding wins
        self.reported_ownership = False

    def check(self) -> None:
        self._auth()
        self._path()
        self.p.check_subset(self.fn, endpoint=True)
        self._writes()
        self._errors()
        self._pages()
        self._response()

    # ---- helpers ------------------------------------------------------------------

    def _type(self, node: ast.expr) -> TableT | None:
        t = self.ctx.types.get(id(node))
        return t if isinstance(t, TableT) else None

    def _name_type(self, name: str) -> TableT | None:
        value = self.locals.get(name)
        if value is not None:
            return self._type(value)
        if name == USER_PARAM:
            return user_table(self.api)
        return None

    def _calls(self, op: str) -> Iterator[ast.Call]:
        for _, value in _assigns(self.fn):
            if _call_name(value) == op:
                assert isinstance(value, ast.Call)
                yield value

    def _arg(self, name: str) -> ast.AST:
        return next((a for a in self.fn.args.args if a.arg == name), self.fn)

    # ---- L801 / L804 ----------------------------------------------------------------

    def _auth(self) -> None:
        decl = parse_decl(self.deco)
        roles = ", ".join(sorted(self.api.roles))
        auth = decl.auth if not isinstance(decl, tuple) else None
        if auth is None:
            self.ctx.error(
                "L801",
                self.deco,
                f"endpoint '{self.fn.name}' does not say who may call it",
                f"add auth=<role> to @endpoint(...), one of: {roles}",
            )
            return
        if not isinstance(auth, ast.Name):
            self.ctx.error("L801", auth, "auth= must name a role", f"one of: {roles}")
            return
        if auth.id not in self.api.roles:
            close = did_you_mean(auth.id, sorted(self.api.roles))
            self.ctx.error(
                "L801",
                auth,
                f"unknown role '{auth.id}'",
                f"{close} (roles: {roles})" if close else f"roles: {roles}",
            )
            return
        has_user = USER_PARAM in self.params
        if auth.id == PUBLIC_ROLE and has_user:
            self.ctx.error(
                "L801",
                self._arg(USER_PARAM),
                "a Public endpoint has no authenticated caller",
                "remove the user parameter, or require a role with auth=",
            )
        elif auth.id != PUBLIC_ROLE and not has_user:
            self.ctx.error(
                "L801",
                self.fn,
                f"endpoint '{self.fn.name}' (auth={auth.id}) must take the caller",
                "add a parameter user: CurrentUser",
            )

    def _path(self) -> None:
        names = path_params(self.info.path)
        if names is None:
            self.ctx.error(
                "L804",
                self.deco.args[1],
                f"malformed path {self.info.path!r}",
                'start with "/" and use segments like "orders" or "{order_id}"',
            )
            return
        seen: set[str] = set()
        for n in names:
            if n in seen:
                self.ctx.error(
                    "L804", self.deco.args[1], f"path segment {{{n}}} appears twice", None
                )
            seen.add(n)
            spec = self.params.get(n)
            if spec is None or spec.scalar is None:
                self.ctx.error(
                    "L804",
                    self.deco.args[1],
                    f"path segment {{{n}}} has no parameter named '{n}'",
                    f"add a parameter {n}: Int(ge=1)",
                )

    # ---- L802 / L811 ------------------------------------------------------------------

    def _ownership(self, node: ast.AST, t: TableT, arg_name: str) -> None:
        if self.admin or self.reported_ownership or not t.unchecked_owned:
            return
        self.reported_ownership = True
        fetched = next(
            (
                name
                for name, value in self.locals.items()
                if _call_name(value) == "fetch"
                and isinstance(value, ast.Call)
                and value.args
                and isinstance(value.args[0], ast.Name)
                and value.args[0].id in t.unchecked_owned
            ),
            arg_name,
        )
        tables = ", ".join(sorted(t.unchecked_owned))
        self.ctx.error(
            "L802",
            node,
            f"rows of {tables} reach the caller without an ownership check",
            f"filter with owned({fetched}, by=user) before returning or writing",
        )

    def _writes(self) -> None:
        for op in _TABLE_ARG_OPS:
            for call in self._calls(op):
                if call.args and isinstance(call.args[0], ast.Name):
                    t = self._name_type(call.args[0].id)
                    if t is not None:
                        self._ownership(call, t, call.args[0].id)
        for call in self._calls("insert"):
            self._insert_owner(call)
        for call in self._calls("update"):
            self._update_owner(call)
        self._bounds()

    def _insert_owner(self, call: ast.Call) -> None:
        if self.admin or self.reported_ownership or not call.args:
            return
        table = call.args[0].id if isinstance(call.args[0], ast.Name) else None
        schema = self.api.tables.get(table or "")
        if schema is None:
            return
        given = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        if schema.owner_via is not None:
            self._via_value(call, table or "", schema.owner_via, given.get(schema.owner_via[0]))
            return
        if schema.owner is None:
            return
        if schema.owner in given:
            self.reported_ownership = True
            self.ctx.error(
                "L802",
                call,
                f"insert() sets the owner column '{schema.owner}' of {table} directly",
                f"remove {schema.owner}= and pass owner=user",
            )
        elif "owner" not in given:
            self.reported_ownership = True
            self.ctx.error(
                "L802",
                call,
                f"insert into {table} does not record the caller as its owner",
                f"add owner=user, e.g. insert({table}, owner=user, ...)",
            )

    def _update_owner(self, call: ast.Call) -> None:
        """update() may not hand rows to another owner: the owner column is never set,
        and an owner_via foreign key only to a parent row the caller owns."""
        if self.admin or self.reported_ownership or not call.args:
            return
        t = self._type(call.args[0]) if isinstance(call.args[0], ast.Name) else None
        if t is None and isinstance(call.args[0], ast.Name):
            t = self._name_type(call.args[0].id)
        schema = self.api.tables.get(t.source or "") if t is not None else None
        if schema is None:
            return
        given = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        if schema.owner is not None and schema.owner in given:
            self.reported_ownership = True
            self.ctx.error(
                "L802",
                call,
                f"update() changes the owner column '{schema.owner}' of {t.source}",
                f"remove {schema.owner}=; rows keep their owner",
            )
        elif schema.owner_via is not None and schema.owner_via[0] in given:
            self._via_value(call, t.source or "", schema.owner_via, given[schema.owner_via[0]])

    def _via_value(
        self, call: ast.Call, table: str, via: tuple[str, str], value: ast.expr | None
    ) -> None:
        """L802 unless the foreign key written into an owned-through table is a
        parameter that the endpoint matched to a parent row the caller owns:
        `row = one(where(owned(fetch(Parent)), col(<key>) == param), ...)` spelled in
        canonical steps."""
        fk, parent = via
        if (
            isinstance(value, ast.Name)
            and value.id in self.params
            and self.params[value.id].scalar is not None
            and self._owns_parent_row(value.id, parent)
        ):
            return
        self.reported_ownership = True
        schema = self.api.tables.get(parent)
        key = via_parent_key(schema) if schema is not None else "id"
        shown = value.id if isinstance(value, ast.Name) else fk
        self.ctx.error(
            "L802",
            call,
            f"{table}.{fk} must reference a {parent} row the caller owns",
            f"look it up first: mine = owned(fetch({parent}), by=user); "
            f'found = where(mine, col("{key}") == {shown}); '
            f'parent = one(found, else_=NotFound("...")); then {fk}={shown}',
        )

    def _owns_parent_row(self, param: str, parent: str) -> bool:
        schema = self.api.tables.get(parent)
        if schema is None or not schema.keys:
            return False
        key = via_parent_key(schema)
        for value in self.locals.values():
            if _call_name(value) != "one":
                continue
            t = self._type(value)
            if t is None or t.source != parent or not t.owner_checked:
                continue
            if self._pinned(value, key, param):
                return True
        return False

    def _pinned(self, node: ast.expr | None, key: str, param: str) -> bool:
        """Whether the chain of single-input ops ending at `node` includes a where()
        whose predicate (or one of its `&` conjuncts) is col(key) == param."""
        seen: set[str] = set()
        while isinstance(node, ast.Call) and node.args:
            if _call_name(node) == "where" and len(node.args) > 1:
                if _pins(node.args[1], key, param):
                    return True
            arg = node.args[0]
            if not isinstance(arg, ast.Name) or arg.id in seen:
                return False
            seen.add(arg.id)
            node = self.locals.get(arg.id)
        return False

    def _bounds(self) -> None:
        """L811: parameters written by insert/update, directly or through local
        constants, must be bounded."""
        deps: dict[str, set[str]] = {}
        for name, value in _assigns(self.fn):
            if is_constant_expr(value):
                deps.setdefault(name, self._params_read(value, deps))
        reported: set[str] = set()
        for op in ("insert", "update"):
            for call in self._calls(op):
                for kw in call.keywords:
                    if kw.arg == "owner" and op == "insert":
                        continue
                    for p in sorted(self._params_read(kw.value, deps)):
                        spec = self.params[p]
                        if p in reported or spec.bounded:
                            continue
                        reported.add(p)
                        self.ctx.error(
                            "L811",
                            self._arg(p),
                            f"parameter '{p}' is written to the database but is unbounded",
                            _bound_hint(spec),
                        )

    def _params_read(self, node: ast.expr, deps: dict[str, set[str]]) -> set[str]:
        found: set[str] = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Name):
                if n.id in deps:
                    found |= deps[n.id]
                elif n.id in self.params and self.params[n.id].scalar is not None:
                    found.add(n.id)
        return found

    # ---- L821 / L822 ------------------------------------------------------------------

    def _errors(self) -> None:
        declared = self.info.errors
        raised: set[str] = set()
        for op in ("one", "require"):
            for call in self._calls(op):
                err = _kw(call, "else_")
                name = _call_name(err) if err is not None else None
                if err is None or name not in API_ERRORS:
                    continue
                raised.add(name)
                if name not in declared:
                    shown = " | ".join([self.info.success, *declared, name])
                    self.ctx.error(
                        "L821",
                        err,
                        f"{name} is raised here but not declared in the response",
                        f"declare it: -> {shown}",
                    )
        for name in declared:
            if name not in raised:
                self.ctx.warn(
                    "L822",
                    self.fn.returns if self.fn.returns is not None else self.fn,
                    f"{name} is declared but never raised",
                    f"remove {name} from the response, or raise it with one()/require()",
                )

    # ---- L831 page bounds ------------------------------------------------------------

    def _pages(self) -> None:
        for call in self._calls("page"):
            limit = _kw(call, "limit")
            if limit is not None:
                self._page_limit(limit)
            offset = _kw(call, "offset")
            if offset is not None:
                self._page_offset(offset)

    def _literal(self, node: ast.expr) -> object:
        v = literal_value(node)
        if v is None and isinstance(node, ast.Name):
            return self.p.module_consts.get(node.id)
        return v

    def _page_limit(self, node: ast.expr) -> None:
        hint = f"bound it: limit: Int(ge=1, le={PAGE_LIMIT_MAX}) = 20"
        if isinstance(node, ast.Name) and node.id in self.params:
            spec = self.params[node.id]
            if spec.scalar is not Scalar.INT:
                self.ctx.error("L404", node, "page limit= must be an Int", hint)
            elif spec.le is None or spec.le > PAGE_LIMIT_MAX:
                self.ctx.error(
                    "L831",
                    node,
                    f"page size '{node.id}' may exceed {PAGE_LIMIT_MAX}",
                    hint,
                )
            return
        v = self._literal(node)
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            self.ctx.error(
                "L404",
                node,
                "page limit= must be a positive integer literal or a bounded Int parameter",
                "e.g. limit=20",
            )
        elif v > PAGE_LIMIT_MAX:
            self.ctx.error(
                "L831",
                node,
                f"page limit {v} exceeds {PAGE_LIMIT_MAX}",
                f"use limit={PAGE_LIMIT_MAX} or less",
            )

    def _page_offset(self, node: ast.expr) -> None:
        hint = "e.g. offset: Int(ge=0) = 0, or offset=0"
        if isinstance(node, ast.Name) and node.id in self.params:
            spec = self.params[node.id]
            if spec.scalar is not Scalar.INT or spec.ge is None or spec.ge < 0:
                self.ctx.error("L404", node, "page offset= parameter must be Int(ge=0)", hint)
            return
        v = self._literal(node)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            self.ctx.error(
                "L404",
                node,
                "page offset= must be a non-negative integer literal or an Int(ge=0) parameter",
                hint,
            )

    # ---- the response: L802 / L803 / L831 / L832 / L833 ----------------------------------

    def _response(self) -> None:
        method, success = self.info.method, self.info.success
        if success == "Created" and method != "POST":
            self.ctx.error(
                "L833",
                self.fn.returns if self.fn.returns is not None else self.fn,
                f"Created (201) is for POST endpoints, not {method}",
                "declare -> One",
            )
        elif success == "One" and method == "POST" and any(True for _ in self._calls("insert")):
            self.ctx.warn(
                "L833",
                self.fn.returns if self.fn.returns is not None else self.fn,
                "this POST endpoint inserts a row but responds 200",
                "declare -> Created (201)",
            )
        ret = self.fn.body[-1] if self.fn.body else None
        if not (isinstance(ret, ast.Return) and isinstance(ret.value, ast.Name)):
            return
        name = ret.value.id
        t = self._type(ret.value)
        if t is None:
            return
        if success == "NoContent":
            if _call_name(self.locals.get(name)) not in _WRITES:
                self.ctx.error(
                    "L832",
                    ret,
                    "a NoContent endpoint returns the result of a write",
                    "return the result of update()/delete() (it is discarded)",
                )
            return
        self._ownership(ret, t, name)
        if t.private:
            cols = ", ".join(sorted(t.private))
            keep = [c for c in t.schema.names() if c not in t.private]
            listed = ", ".join(f'"{c}"' for c in keep)
            self.ctx.error(
                "L803",
                ret,
                f"the response would include private column(s): {cols}",
                f"drop them first: shown = select({name}, cols=[{listed}])",
            )
        if success == "Page":
            if t.paged:
                return
            if t.at_most_one_row:
                self.ctx.error(
                    "L832",
                    ret,
                    "a Page endpoint returns page(...), but this is a single row",
                    "declare -> One, or return page(<table>, limit=20)",
                )
            else:
                self.ctx.error(
                    "L831",
                    ret,
                    f"'{name}' can have many rows but is not paginated",
                    f"return page({name}, limit=20)",
                )
            return
        if t.paged:
            self.ctx.error(
                "L832",
                ret,
                f"a {success} endpoint returns one row, but this is a page",
                "declare -> Page",
            )
        elif not t.at_most_one_row:
            self.ctx.error(
                "L832",
                ret,
                f"a {success} endpoint returns one row, but '{name}' can have many",
                f'reduce it to one row: one({name}, else_=NotFound("...")), '
                "or declare -> Page and return page(...)",
            )


def _pins(pred: ast.expr, key: str, param: str) -> bool:
    match pred:
        case ast.BinOp(left=a, op=ast.BitAnd(), right=b):
            return _pins(a, key, param) or _pins(b, key, param)
        case ast.Compare(left=a, ops=[ast.Eq()], comparators=[b]):
            return (_is_col(a, key) and _is_name(b, param)) or (
                _is_col(b, key) and _is_name(a, param)
            )
    return False


def _is_col(node: ast.expr, name: str) -> bool:
    match node:
        case ast.Call(func=ast.Name(id="col"), args=[ast.Constant(value=str(n))], keywords=[]):
            return n == name
    return False


def _is_name(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _bound_hint(spec: ParamSpec) -> str:
    match spec.scalar:
        case Scalar.FLOAT:
            return f"give it bounds, e.g. {spec.name}: Float(ge=0, le=1000)"
        case Scalar.STR:
            return f"give it a maximum length, e.g. {spec.name}: Str(max_len=200)"
        case _:
            return f"give it bounds, e.g. {spec.name}: Int(ge=1, le=100)"


def run(module: ast.Module, ctx: CheckContext) -> dict[str, EndpointInfo]:
    if ctx.profile.api is None:
        return {}
    return _Api(module, ctx).run()
