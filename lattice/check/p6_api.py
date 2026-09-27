"""P6 — API rules (API profile only): auth, ownership, private columns, paths,
parameter bounds, declared errors, response shapes and the SQL subset.

Runs after P4, reading the types it recorded: a table typed UNKNOWN (already
reported) is skipped, so one mistake yields one diagnostic. Returns the metadata of
every endpoint for lowering (`CheckedProgram.endpoints`).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import replace

from lattice.catalog import (
    AGG_FUNCS,
    API_AGG_FUNCS,
    API_ENDPOINT_OPS,
    API_ERRORS,
    API_FN_FUNCS,
    ELSE_KW,
    FN_SIGS,
    FOR_EACH,
    FOR_EACH_MAX_ROWS,
    PAGE_LIMIT_MAX,
    PUBLIC_ROLE,
    SENT,
    TABLE_OPS,
    UNIQUE_OR,
    UNSET_NAME,
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
from lattice.check.astutil import (
    endpoints_of,
    is_constant_expr,
    join_on_pairs,
    literal_value,
    step_mentions,
    steps_of,
)
from lattice.check.context import CheckContext
from lattice.check.exprs import did_you_mean
from lattice.check.p4_types import cols_read, is_owner_is
from lattice.profile import AdminFlag
from lattice.typesys import Scalar, TableT, generated_key, uuid_key, via_parent_key

_WRITES = ("insert", "update", "delete")
_TABLE_ARG_OPS = ("update", "delete")
# ops that narrow the rows of one table without changing their values
_ROW_FILTERS = frozenset({"where", "sort", "select", "limit"})
# ops a parent-row lookup for an owned-through write may pass through (_pinned)
_PIN_CHAIN = frozenset(
    {
        "fetch",
        "accessible",
        "owned",
        "mine",
        "where",
        "sort",
        "select",
        "limit",
        "one",
        "require",
        "derive",
    }
)
# ops whose result has exactly as many rows as their (first) input
_ROW_WISE = frozenset(
    {"derive", "select", "rename", "sort", "require", "update", "delete", "window"}
)


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
        self.verifiers = frozenset(n for n, sig in self.api.natives.items() if sig.reads_private)
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
        self._unset_placement()
        self._sent_in_steps()
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
            checked = _Endpoint(self, fn, deco, info)
            checked.check()
            infos.setdefault(fn.name, replace(info, page_total=checked.page_total()))
        return infos

    def _unset_placement(self) -> None:
        """L404: `UNSET` is only an endpoint parameter's default (`p: Str = UNSET`)."""
        allowed: set[int] = set()
        for fn in endpoints_of(self.module):
            allowed.update(id(d) for d in fn.args.defaults)
        for node in ast.walk(self.module):
            if isinstance(node, ast.Name) and node.id == UNSET_NAME and id(node) not in allowed:
                self.ctx.error(
                    "L404",
                    node,
                    "UNSET is only allowed as the default of an endpoint parameter",
                    "declare an optional parameter, e.g. title: Str(max_len=100) = UNSET, "
                    "and pass it to update(t, title=title)",
                )

    def _sent_in_steps(self) -> None:
        """L404: sent(p) tests an endpoint's own optional parameter, so it appears only
        in an endpoint body."""
        for step in self.steps.values():
            for node in ast.walk(step):
                if _call_name(node) == SENT:
                    self.ctx.error(
                        "L404",
                        node,
                        "sent() is only allowed in an endpoint body",
                        "test the endpoint's optional parameter there, e.g. "
                        "require(t, ~sent(title) | ..., else_=Forbidden(...))",
                    )

    def check_subset(self, fn: ast.FunctionDef, *, endpoint: bool) -> None:
        """L841: table ops and expressions outside what the API profile compiles to
        SQL, in an endpoint body and (transitively) in the steps it calls."""
        for call in _for_each_calls(fn):  # its arguments are computed in SQL, too
            for node in ast.walk(call):
                self._subset_expr(node)
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
        self._owned_calls()
        self._owner_is_inputs()
        self._checks_before_owned()
        self._owner_is_checks()
        self._private_reads()
        self._optional_params()
        self._writes()
        self._errors()
        self._pages()
        self._for_each_inputs()
        self._response()
        self._response_model()

    def page_total(self) -> bool:
        """Whether the returned table is a page(..., total=True) (for lowering)."""
        ret = self.fn.body[-1] if self.fn.body else None
        if self.info.success != "Page" or not isinstance(ret, ast.Return):
            return False
        t = self._type(ret.value) if ret.value is not None else None
        return t is not None and t.paged and t.page_total

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

    # ---- L439 --------------------------------------------------------------------------

    def _for_each_inputs(self) -> None:
        """L439: for_each sends one effect per input row, so its input must be bounded:
        the result of one(), insert(), an update() of one row, a page(), or limit(n=N)
        with N <= FOR_EACH_MAX_ROWS -- or a row filter / row-wise op of such rows.
        L802: its rows must also have passed an ownership check, like returned rows."""
        for call in _for_each_calls(self.fn):
            if len(call.args) < 2:
                continue  # P3 reported the call shape
            arg = call.args[0]
            t = self._name_type(arg.id) if isinstance(arg, ast.Name) else None
            if not isinstance(arg, ast.Name) or t is None:
                continue  # P3/P4 reported it
            # L802: for_each hands each row's columns to an effect, so its rows reach the
            # outside world like a returned table does (a statement, never seen by _response)
            self._ownership(call, t, arg.id)
            if not self._bounded(arg.id, set()):
                self.ctx.error(
                    "L439",
                    arg,
                    f"for_each over '{arg.id}' may send an unbounded number of effects",
                    "pass rows of one(...), insert(...), an update of one row, page(...) or "
                    f"limit(t, n=N) with N <= {FOR_EACH_MAX_ROWS}",
                )

    def _bounded(self, name: str, seen: set[str]) -> bool:
        t = self._name_type(name)
        if t is not None and (t.at_most_one_row or t.paged):
            return True
        value = self.locals.get(name)
        if value is None or name in seen or not isinstance(value, ast.Call) or not value.args:
            return False
        seen.add(name)
        op = _call_name(value)
        if op == "limit":
            n = literal_value(_kw(value, "n")) if _kw(value, "n") is not None else None
            if isinstance(n, int) and not isinstance(n, bool) and 0 < n <= FOR_EACH_MAX_ROWS:
                return True
        if op in _ROW_WISE | _ROW_FILTERS | {"one", "page"}:
            src = value.args[0]
            return isinstance(src, ast.Name) and self._bounded(src.id, seen)
        return False

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
            elif spec.optional:
                self.ctx.error(
                    "L404",
                    self._arg(n),
                    f"path parameter '{n}' is always sent; it cannot default to UNSET",
                    "remove '= UNSET'",
                )
            elif spec.nullable:
                self.ctx.error(
                    "L404",
                    self._arg(n),
                    f"path parameter '{n}' is always sent; it cannot be None",
                    "remove '| None = None'",
                )

    # ---- owned() placement (L404), checks before owned() (L802) ------------------------

    def _owned_calls(self) -> None:
        """accessible() (alias owned) / mine() filter by ONE owner column, so they must
        see the rows of one fetched table: fetch(T), possibly narrowed by
        where/sort/select/limit."""
        for op in ("accessible", "owned", "mine"):
            for call in self._calls(op):
                if self._type(call) is None or not call.args:
                    continue  # P4 reported it
                if self._over_one_fetch(call.args[0]):
                    continue
                self.ctx.error(
                    "L404",
                    call,
                    f"{op}() must filter the rows of one fetched table",
                    f"apply {op}() to each table right after fetch, before joining: "
                    f"{'own' if op == 'mine' else 'visible'} = {op}(fetched, by=user)",
                )

    def _owner_is_inputs(self) -> None:
        """require(t, owner_is(user)) checks ONE owner column (or owner_via key) in SQL,
        so -- like owned() -- it must see the rows of one fetched table: a join would
        let the other table's rows through unchecked."""
        for call in self._owner_is_requires():
            if self._type(call) is None:
                continue  # P4 reported it
            if self._over_one_fetch(call.args[0], also=frozenset({"one"})):
                continue
            self.ctx.error(
                "L404",
                call.args[1],
                "owner_is() must check the rows of one fetched table",
                "check each table before joining: "
                "row = one(where(fetch(T), ...)); checked = require(row, owner_is(user), ...)",
            )

    def _over_one_fetch(self, node: ast.expr, also: frozenset[str] = frozenset()) -> bool:
        seen: set[str] = set()
        while isinstance(node, ast.Name) and node.id not in seen:
            seen.add(node.id)
            value = self.locals.get(node.id)
            op = _call_name(value) if value is not None else None
            if op == "fetch":
                return True
            if op not in _ROW_FILTERS | also or not isinstance(value, ast.Call) or not value.args:
                return False
            node = value.args[0]
        return False

    def _owner_is_requires(self) -> Iterator[ast.Call]:
        for call in self._calls("require"):
            if len(call.args) > 1 and is_owner_is(call.args[1]):
                yield call

    def _checks_before_owned(self) -> None:
        """one()/require() over rows of an owned table not yet passed through owned()
        answer differently for another customer's row than for a missing one (a
        status-code oracle): L802, like returning those rows. Exempt: the ownership
        check require(t, owner_is(user), ...) itself, and a one() whose result it
        checks directly (the faithful 404-then-403 lookup, warned as L823)."""
        exempt: set[int] = set()
        for call in self._owner_is_requires():
            exempt.add(id(call))
            arg = call.args[0]
            value = self.locals.get(arg.id) if isinstance(arg, ast.Name) else None
            if _call_name(value) == "one":
                exempt.add(id(value))
        for _, value in _assigns(self.fn):
            if _call_name(value) not in ("one", "require") or id(value) in exempt:
                continue
            assert isinstance(value, ast.Call)
            arg = value.args[0] if value.args else None
            if isinstance(arg, ast.Name):
                t = self._name_type(arg.id)
                if t is not None:
                    self._ownership(value, t, arg.id)

    def _owner_is_checks(self) -> None:
        """L823 (warning): require(t, owner_is(user), else_=Forbidden(...)) tells a
        caller that another user's row exists (403), where owned() answers 404."""
        if self.admin:
            return
        for call in self._owner_is_requires():
            if self._type(call) is None:
                continue  # P4 reported the check
            self.ctx.warn(
                "L823",
                call,
                "require(..., owner_is(user)) reveals that other users' rows exist (403); "
                "accessible() answers 404",
                "keep it only when the 403 is the intended contract; otherwise filter with "
                "accessible(t, by=user) before one()",
            )

    # ---- UNSET: optional parameters ---------------------------------------------------

    def _sent_calls(self) -> set[int]:
        """L404: sent(p) names an optional (`= UNSET`) parameter of this endpoint.
        Returns the ids of the argument names of the valid calls."""
        ok: set[int] = set()
        for node in ast.walk(self.fn):
            if _call_name(node) != SENT:
                continue
            assert isinstance(node, ast.Call)
            arg = node.args[0] if len(node.args) == 1 and not node.keywords else None
            if not isinstance(arg, ast.Name):
                continue  # P4 reported it
            spec = self.params.get(arg.id)
            if spec is not None and spec.optional:
                ok.add(id(arg))
                continue
            if spec is not None and spec.nullable:
                hint = (
                    f"'{arg.id}' is None when not given: test it with is_null({arg.id})"
                )
            elif spec is not None and spec.scalar is not None:
                hint = (
                    f"'{arg.id}' is required, so it is always sent; declare it "
                    f"{arg.id}: ... = UNSET to make it optional"
                )
            else:
                hint = "name a parameter declared with the default UNSET"
            self.ctx.error(
                "L404",
                node,
                f"sent({arg.id}) is only for an optional parameter (default UNSET)",
                hint,
            )
        return ok

    def _optional_params(self) -> None:
        """L404: an optional parameter (default UNSET) is used only directly as an
        update() value, or an insert() value of a nullable column, or as the argument
        of sent(): anywhere else (inside an expression, a filter, a page bound, a step
        argument, ...) it has no value when it is not sent."""
        sent_args = self._sent_calls()
        optional = {n for n, spec in self.params.items() if spec.optional}
        if not optional:
            return
        allowed: set[int] = set(sent_args)
        reported: set[str] = set()
        for call in self._calls("update"):
            for kw in call.keywords:
                if isinstance(kw.value, ast.Name) and kw.value.id in optional:
                    allowed.add(id(kw.value))
        for call in self._calls("insert"):
            table = call.args[0].id if call.args and isinstance(call.args[0], ast.Name) else ""
            schema = self.api.tables.get(table)
            for kw in call.keywords:
                if not (isinstance(kw.value, ast.Name) and kw.value.id in optional):
                    continue
                allowed.add(id(kw.value))
                if kw.arg == "owner" or schema is None or kw.arg not in schema:
                    continue  # P4 reported it
                if kw.arg not in schema.nullable and kw.value.id not in reported:
                    reported.add(kw.value.id)
                    self.ctx.error(
                        "L404",
                        kw,
                        f"insert() sets the required column '{kw.arg}' of {table} from "
                        f"'{kw.value.id}', which is optional (default UNSET)",
                        f"make '{kw.value.id}' required (remove '= UNSET') or give it a "
                        "default value",
                    )
        for stmt in self.fn.body:
            for node in ast.walk(stmt):
                if not (isinstance(node, ast.Name) and node.id in optional):
                    continue
                if id(node) in allowed or node.id in reported:
                    continue
                reported.add(node.id)
                self.ctx.error(
                    "L404",
                    node,
                    f"'{node.id}' is optional (default UNSET): use it only directly as an "
                    "update() value, e.g. update(t, col=" + node.id + f"), or test sent({node.id})",
                    f"to compute with it or filter by it, make '{node.id}' required or give "
                    "it a default value",
                )

    # ---- L803: private values as an oracle ---------------------------------------------

    def _private_reads(self) -> None:
        """Outside Admin endpoints no filter, sort, join, aggregate, check or written
        value may read a private column: the response (or its status) would reveal it.
        Nor may a for_each argument: the effect would carry it out of the database."""
        if self.admin:
            return
        for call in _for_each_calls(self.fn):
            table = call.args[0] if call.args else None
            t = self._name_type(table.id) if isinstance(table, ast.Name) else None
            if t is None or not t.private:
                continue
            read = frozenset().union(*(cols_read(a) for a in call.args[2:])) & t.private
            if read:
                self.ctx.error(
                    "L803",
                    call,
                    f"for_each sends private column(s) {', '.join(sorted(read))} to an effect "
                    "native, outside the database",
                    "pass the other columns; only Admin endpoints may read private columns",
                )
                return
        for _, value in _assigns(self.fn):
            op = _call_name(value)
            if op is None:
                continue
            assert isinstance(value, ast.Call)
            read = self._private_read(op, value)
            if read:
                cols = ", ".join(sorted(read))
                self.ctx.error(
                    "L803",
                    value,
                    f"{op}() reads private column(s) {cols}; the response would reveal "
                    "their values",
                    "only Admin endpoints may filter, sort, join or aggregate on private "
                    "columns; use the other columns",
                )
                return

    def _private_read(self, op: str, call: ast.Call) -> frozenset[str]:
        tables = [self._name_type(a.id) for a in call.args if isinstance(a, ast.Name)]
        private = frozenset().union(*(t.private for t in tables if t is not None))
        if not private and op not in self.p.steps:
            return frozenset()
        kws = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        verifiers = self.p.verifiers
        match op:
            case "where" | "require" if len(call.args) > 1:
                return cols_read(call.args[1], verifiers) & private
            case "sort":
                return frozenset(_strs(kws.get("by"))) & private
            case "join":
                pairs = join_on_pairs(kws.get("on")) or ()
                return frozenset(c for pair in pairs for c in pair) & private
            case "group":
                read = frozenset(_strs(kws.get("by")))
                for name, v in kws.items():
                    if name != "by" and v is not None:
                        read |= cols_read(v, verifiers)
                return read & private
            case "window":
                read = frozenset(_strs(kws.get("by"))) | frozenset(_strs(kws.get("order")))
                for name, v in kws.items():
                    if name not in ("by", "order", "desc") and v is not None:
                        read |= cols_read(v, verifiers)
                return read & private
            case "traverse":
                links = {_str_value(kws.get("parent")), _str_value(kws.get("child"))}
                return frozenset(c for c in links if c is not None) & private
            case "update":
                read = frozenset()
                for v in kws.values():
                    if v is not None:
                        read |= cols_read(v, verifiers)
                return read & private
            case _ if op in self.p.steps:
                args = [t for t in tables if t is not None]
                inputs = frozenset().union(*(t.private for t in args))
                return step_mentions(self.p.steps, op) & inputs
        return frozenset()

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
            f"filter with accessible({fetched}, by=user) before returning or writing",
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
        for op in ("insert", "update"):
            for call in self._calls(op):
                self._admin_write(op, call)
        self._bounds()

    def _admin_write(self, op: str, call: ast.Call) -> None:
        """L802: only Admin endpoints write a schema's admin-only columns (a role)."""
        if self.admin or self.reported_ownership or not call.args:
            return
        if op == "insert":
            table = call.args[0].id if isinstance(call.args[0], ast.Name) else None
        else:
            t = self._type(call)
            table = t.source if t is not None else None
        schema = self.api.tables.get(table or "")
        if schema is None:
            return
        admin_write = set(schema.admin_write)
        given = {kw.arg for kw in call.keywords if kw.arg is not None}
        if table == self.api.users_table:
            # the column callers' roles come from (AdminFlag / RoleColumn) is always
            # admin-only: writing it would let a caller grant themselves Admin. Only a
            # literal non-admin value (a signup's is_superuser=False) is harmless.
            role_col = self.api.roles_from.column
            value = next((kw.value for kw in call.keywords if kw.arg == role_col), None)
            if value is not None and not self._non_admin_role(value):
                admin_write.add(role_col)
        written = sorted(given & admin_write)
        if written:
            self.reported_ownership = True
            cols = ", ".join(written)
            self.ctx.error(
                "L802",
                call,
                f"{op}() writes the admin-only column(s) {cols} of {table}",
                f"only auth=Admin endpoints may set {cols}; remove "
                + ", ".join(f"{c}=" for c in written),
            )

    def _non_admin_role(self, value: ast.expr) -> bool:
        """A literal that cannot make its row an Admin: False for an AdminFlag column,
        a role other than the admin role for a RoleColumn."""
        if not isinstance(value, ast.Constant):
            return False
        if isinstance(self.api.roles_from, AdminFlag):
            return value.value is False
        return isinstance(value.value, str) and value.value != self.api.admin_role

    def _insert_owner(self, call: ast.Call) -> None:
        if not call.args:
            return
        table = call.args[0].id if isinstance(call.args[0], ast.Name) else None
        schema = self.api.tables.get(table or "")
        if schema is None:
            return
        given = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        if self.admin:
            owner = schema.owner
            # a table owned through its own generated key (Users) gets a new id from the
            # database (or a new uuid4): its owner is the new row itself, nothing to set
            if owner is not None and owner in (generated_key(schema), uuid_key(schema)):
                return
            if owner is not None and owner not in given and "owner" not in given:
                self.ctx.error(
                    "L404",
                    call,
                    f"insert into {table} does not set required column(s) '{owner}'",
                    f"pass owner=user, or {owner}=<user id>",
                )
            return
        if self.reported_ownership:
            return
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
        """update() hands rows to another owner (owner=, or the owner column by name)
        only in an Admin endpoint or for rows that passed an ownership check
        (accessible/owned, mine, owner_is): L802 otherwise. An owner_via foreign key
        points only to a parent row the caller owns."""
        if not call.args:
            return
        t = self._type(call.args[0]) if isinstance(call.args[0], ast.Name) else None
        if t is None and isinstance(call.args[0], ast.Name):
            t = self._name_type(call.args[0].id)
        schema = self.api.tables.get(t.source or "") if t is not None else None
        if t is None or schema is None:
            return
        given = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        new_owner = given.get("owner")
        if new_owner is None and schema.owner is not None:
            new_owner = given.get(schema.owner)
        if new_owner is not None and schema.owner is not None:
            self._new_owner_exists(call, new_owner, ELSE_KW in given)
        if self.admin or self.reported_ownership:
            return
        if new_owner is not None and schema.owner is not None and not t.owner_checked:
            self.reported_ownership = True
            self.ctx.error(
                "L802",
                call,
                f"update() changes the owner column '{schema.owner}' of {t.source} rows "
                "the caller was not checked to own",
                "check the rows first: accessible(t, by=user) after fetch, or "
                'require(row, owner_is(user), else_=Forbidden("...")); or make the endpoint '
                "auth=Admin",
            )
        elif schema.owner_via is not None and schema.owner_via[0] in given:
            self._via_value(call, t.source or "", schema.owner_via, given[schema.owner_via[0]])

    def _new_owner_exists(self, call: ast.Call, value: ast.expr, declared: bool) -> None:
        """The new owner of a transfer must exist. The caller does (`user`,
        `user.<key>`), and so does a users row read by one() (possibly through
        require()); any other new owner needs `else_=` -- the error answered, in the
        same statement, when no users row has that key (L404)."""
        if declared or self._existing_user(value):
            return
        self.ctx.error(
            "L404",
            value,
            "the new owner must exist: update() answers an error when no "
            f"{self.api.users_table} row has this key",
            'declare it: update(t, owner=..., else_=NotFound("User not found"))',
        )

    def _existing_user(self, value: ast.expr) -> bool:
        users = self.api.tables.get(self.api.users_table)
        key = users.keys[0][0] if users is not None and users.keys else None
        match value:
            case ast.Name(id=name) if name == USER_PARAM:
                return True
            case ast.Attribute(value=ast.Name(id=name), attr=attr) if name == USER_PARAM:
                return attr == key
            case ast.Name(id=name) if name in self.locals:
                node: ast.expr | None = self.locals[name]
                while _call_name(node) == "require" and isinstance(node, ast.Call):
                    arg = node.args[0] if node.args else None
                    node = self.locals.get(arg.id) if isinstance(arg, ast.Name) else None
                return _call_name(node) == "one"
        return False

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
            f"look it up first: visible = accessible(fetch({parent}), by=user); "
            f'found = where(visible, col("{key}") == {shown}); '
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
            op = _call_name(node)
            if op not in _PIN_CHAIN or (
                op == "derive" and any(kw.arg == key for kw in node.keywords)
            ):
                return False  # a step/join/rename, or a derive of the key, rewrote it
            if op == "where" and len(node.args) > 1:
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
        # table local -> parameters its column values may hold (through derive, group,
        # step arguments, update values, ...), however indirectly
        carried: dict[str, set[str]] = {}
        for name, value in _assigns(self.fn):
            if is_constant_expr(value):
                deps.setdefault(name, self._params_read(value, deps))
                continue
            if not isinstance(value, ast.Call) or name in carried:
                continue
            held: set[str] = set()
            for a in value.args:
                if isinstance(a, ast.Name):
                    held |= carried.get(a.id, set())
            if _call_name(value) not in ("where", "require", "one", "page", "sort", "limit"):
                for kw in value.keywords:
                    if kw.arg is not None and kw.arg != "owner":
                        held |= self._params_read(kw.value, deps)
            carried[name] = held
        reported: set[str] = set()
        for op in ("insert", "update"):
            for call in self._calls(op):
                source = call.args[0] if op == "update" and call.args else None
                for kw in call.keywords:
                    if kw.arg == "owner" and op == "insert":
                        continue
                    written = self._params_read(kw.value, deps)
                    if isinstance(source, ast.Name) and _cols_read(kw.value):
                        written |= carried.get(source.id, set())
                    for p in sorted(written):
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
        empty = self._may_respond_empty()
        if empty is not None:
            # the runtime answers an empty One/Created response with 404 NotFound
            raised.add("NotFound")
            if "NotFound" not in declared:
                shown = " | ".join([self.info.success, *declared, "NotFound"])
                self.ctx.error(
                    "L821",
                    empty,
                    "the response may have no row, which answers NotFound (404), "
                    "but NotFound is not declared",
                    f"declare it: -> {shown}; or respond with exactly one row "
                    '(one(..., else_=NotFound("...")), insert(), user, group(..., by=[]))',
                )
        for op, keyword in (
            ("one", "else_"),
            ("require", "else_"),
            ("insert", UNIQUE_OR),
            ("update", UNIQUE_OR),
            ("update", ELSE_KW),
        ):
            for call in self._calls(op):
                err = _kw(call, keyword)
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
                    f"remove {name} from the response, or raise it with one()/require()"
                    + (" or unique_or=" if name == "Conflict" else ""),
                )

    def _may_respond_empty(self) -> ast.Return | None:
        """The `return` of a One/Created endpoint whose single-row response may have no
        row at all (e.g. a where() after one(), or owned() of a single row)."""
        if self.info.success not in ("One", "Created"):
            return None
        ret = self.fn.body[-1] if self.fn.body else None
        if not (isinstance(ret, ast.Return) and isinstance(ret.value, ast.Name)):
            return None
        t = self._type(ret.value)
        if t is None or t.paged or not t.at_most_one_row:
            return None  # not a single row: L832 reports it
        return None if self._exactly_one(ret.value, set()) else ret

    def _exactly_one(
        self,
        node: ast.expr,
        seen: set[str],
        scope: dict[str, ast.expr] | None = None,
        base: frozenset[str] | None = None,
    ) -> bool:
        """Whether a table local statically has exactly one row: one(), insert(), the
        caller, group(..., by=[]), and row-wise ops / left joins / steps of those.
        `scope`/`base` are a step body's locals and its exactly-one table parameters."""
        if scope is None:
            scope = self.locals
            base = frozenset({USER_PARAM}) if USER_PARAM in self.params else frozenset()
        assert base is not None
        if not isinstance(node, ast.Name) or node.id in seen:
            return False
        seen = seen | {node.id}
        value = scope.get(node.id)
        if value is None:
            return node.id in base
        op = _call_name(value)
        if not isinstance(value, ast.Call) or op is None:
            return False

        def one(arg: ast.expr | None) -> bool:
            return arg is not None and self._exactly_one(arg, seen, scope, base)

        first = value.args[0] if value.args else None
        match op:
            case "one" | "insert":
                return True
            case "group":
                by = _kw(value, "by")
                return (isinstance(by, ast.List | ast.Tuple) and not by.elts) or one(first)
            case "limit":
                return one(first)
            case "join":
                if len(value.args) < 2 or _kw(value, "fanout") is not None:
                    return False
                how = literal_value(_kw(value, "how"))
                if join_on_pairs(_kw(value, "on")):
                    return how == "left" and one(first)
                return one(first) and (how == "left" or one(value.args[1]))
            case _ if op in _ROW_WISE:
                return one(first)
            case _ if op in self.p.steps:
                fn = self.p.steps[op]
                tables = [a.arg for a in fn.args.args if _is_table_param(a)]
                inner = frozenset(p for p, arg in zip(tables, value.args, strict=False) if one(arg))
                ret = fn.body[-1] if fn.body else None
                if not (isinstance(ret, ast.Return) and isinstance(ret.value, ast.Name)):
                    return False
                body = dict(reversed(list(_assigns(fn))))
                return self._exactly_one(ret.value, set(), body, inner)
        return False

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
            hint = (
                f"drop them first: shown = select({name}, cols=[{listed}])"
                if keep
                else "every column may hold private data (a step that reads a private "
                "column makes all of its output private): select the public columns "
                "before calling the step"
            )
            self.ctx.error(
                "L803",
                ret,
                f"the response would include private column(s): {cols}",
                hint,
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

    # ---- L834: declared response models ------------------------------------------------

    def _response_model(self) -> None:
        """@endpoint(..., response="Model"): the returned columns are exactly the
        model's fields (any order); for a Page, the columns of each item."""
        name = self.info.response
        if name is None:
            return
        where = next(kw.value for kw in self.deco.keywords if kw.arg == "response")
        fields = self.api.responses.get(name)
        if fields is None:
            known = sorted(self.api.responses)
            self.ctx.error(
                "L834",
                where,
                f"unknown response model '{name}'",
                did_you_mean(name, known)
                or ("models: " + ", ".join(known) if known else "the profile declares none"),
            )
            return
        if self.info.success == "NoContent":
            self.ctx.error(
                "L834",
                where,
                "a NoContent endpoint has no response body to shape",
                f'remove response="{name}"',
            )
            return
        ret = self.fn.body[-1] if self.fn.body else None
        if not (isinstance(ret, ast.Return) and isinstance(ret.value, ast.Name)):
            return
        t = self._type(ret.value)
        if t is None:
            return
        returned = t.schema.names()
        missing = [f for f in fields if f not in returned]
        extra = [c for c in returned if c not in fields]
        if not missing and not extra:
            return
        parts = []
        if missing:
            parts.append("missing " + ", ".join(f"'{f}'" for f in missing))
        if extra:
            parts.append("extra " + ", ".join(f"'{c}'" for c in extra))
        listed = ", ".join(f'"{f}"' for f in fields)
        self.ctx.error(
            "L834",
            ret,
            f"the returned columns do not match response model '{name}': " + "; ".join(parts),
            f"return exactly its fields, e.g. select(..., cols=[{listed}]) "
            "(derive or rename the missing ones first)",
        )


def _is_table_param(a: ast.arg) -> bool:
    """A step parameter annotated Table[...] (scalar parameters are keywords)."""
    return isinstance(a.annotation, ast.Subscript)


def _for_each_calls(fn: ast.FunctionDef) -> Iterator[ast.Call]:
    """The for_each(...) statements of an endpoint body."""
    for stmt in fn.body:
        match stmt:
            case ast.Expr(value=ast.Call(func=ast.Name(id=name)) as call) if name == FOR_EACH:
                yield call
            case _:
                pass


def _strs(node: ast.expr | None) -> list[str]:
    if isinstance(node, ast.List | ast.Tuple):
        return [
            e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
    return []


def _str_value(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _cols_read(node: ast.expr) -> frozenset[str]:
    """The columns an expression reads via col("...")."""
    return frozenset(
        n.args[0].value
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "col"
        and n.args
        and isinstance(n.args[0], ast.Constant)
        and isinstance(n.args[0].value, str)
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
