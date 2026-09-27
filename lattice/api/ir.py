"""API IR: lower each checked `@endpoint` into an `EndpointPlan` — ordered steps the
runtime executes in one transaction.

Lowering works in two phases per endpoint:

1. Walk the body (inlining `@step` calls, as the tabular IR does) and build a DAG of
   relational nodes (`lattice.api.sql.Rel`) for every read op; `one`/`require` bind
   an alias of their input's rows; `insert`/`update`/`delete` bind a write result.
   Named constants are substituted into expressions and endpoint parameters become
   `ParamRef`s, so every value reaches SQL as a bind parameter.
2. Emit steps in statement order. A relation is materialized by ONE `ReadStep`
   (one SELECT) the first time a check, a write or the `return` needs its rows;
   later reads over it bind those rows (JSON) instead of re-reading the database.
   Every `require` condition over a relation is computed by that relation's
   ReadStep as an extra flag column, so a check costs no statement of its own.
"""

from __future__ import annotations

import ast
import copy
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from lattice.api.backend import dialect_for
from lattice.api.sql import (
    OwnerIs,
    ParamRef,
    Rel,
    SqlExpr,
    SqlQuery,
    compile_delete,
    compile_insert,
    compile_select,
    compile_update,
    expr_sql,
)
from lattice.catalog import (
    API_ERRORS,
    NATIVE_NS,
    OP_SHAPES,
    PUBLIC_ROLE,
    UNIQUE_OR,
    USER_PARAM,
)
from lattice.check import CheckedProgram
from lattice.check.api_info import EndpointInfo, ParamSpec, user_table
from lattice.check.astutil import is_constant_expr, join_on_pairs, literal_value, steps_of
from lattice.check.p4_types import is_owner_is
from lattice.diagnostics import Span, span_of
from lattice.profile import AdminFlag, ApiProfile, NativeSig, NativeToken, RoleColumn, TokenColumn
from lattice.runtime import LatticeInternalError
from lattice.typesys import Scalar, ScalarParamT, Schema, TableT, uuid_key, via_parent_key

__all__ = [
    "ApiStep",
    "CheckStep",
    "EndpointPlan",
    "ErrorSpec",
    "PageArgs",
    "ParamSpec",
    "ReadStep",
    "WriteStep",
    "lower_api",
]


@dataclass(frozen=True)
class ErrorSpec:
    kind: str  # NotFound | Forbidden | Conflict | BadRequest
    status: int
    detail: str

    @property
    def code(self) -> str:
        """The JSON error code: the kind in snake case (NotFound -> not_found)."""
        return "".join(f"_{c.lower()}" if c.isupper() else c for c in self.kind).lstrip("_")


@dataclass(frozen=True)
class ReadStep:
    """One SELECT: the rows of a relation (columns of `schema`, then `flags`)."""

    id: str
    sql: SqlQuery
    span: Span
    schema: Schema = field(default_factory=lambda: Schema(()))
    flags: tuple[str, ...] = ()  # require() flag columns appended after the schema's


@dataclass(frozen=True)
class CheckStep:
    """one(): `input` must have exactly one row (zero -> error; more -> R310, a 500).
    require(): every row's condition must hold (false or null -> error). The condition
    was computed as column `flag` of step `flag_step`'s rows (row-aligned with input)."""

    id: str
    kind: Literal["one", "require"]
    input: str
    error: ErrorSpec
    cond: SqlExpr | None
    span: Span
    flag_step: str | None = None
    flag: str | None = None


@dataclass(frozen=True)
class WriteStep:
    """insert: one row, RETURNING the table's columns. update: the rows of `input`
    (by `key`), RETURNING the key and the new values, merged into the input rows.
    delete: the rows of `input` (by key); the result is the input rows."""

    id: str
    kind: Literal["insert", "update", "delete"]
    table: str  # SQL table name
    input: str | None
    values: tuple[tuple[str, SqlExpr], ...]
    span: Span
    sql: SqlQuery = field(default_factory=lambda: SqlQuery(""))
    schema: Schema = field(default_factory=lambda: Schema(()))
    key: tuple[str, ...] = ()
    # insert/update(..., unique_or=Conflict("...")): the error answered when the
    # statement hits a UNIQUE constraint (else the generic 409, UNIQUE_CONFLICT).
    conflict: ErrorSpec | None = None


ApiStep = ReadStep | CheckStep | WriteStep


@dataclass(frozen=True)
class PageArgs:
    """page(limit=, offset=): each an int literal or an endpoint parameter name.
    `total`: page(..., total=True); the row count is computed by step `total_step`'s
    statement (`SqlQuery.total`)."""

    limit: int | str
    offset: int | str = 0
    total: bool = False
    total_step: str | None = None


@dataclass(frozen=True)
class EndpointPlan:
    name: str
    method: str
    path: str
    auth: str
    params: tuple[ParamSpec, ...]
    success: Literal["One", "Page", "Created", "NoContent"]
    errors: tuple[str, ...]
    steps: tuple[ApiStep, ...]
    returns: str  # id of the step whose result is the response
    span: Span
    schema: Schema = field(default_factory=lambda: Schema(()))  # response columns
    page: PageArgs | None = None
    admin_role: str = "Admin"
    users_table: str = "users"  # SQL table authenticated against
    user_key: str = "id"
    # the caller's row as the auth lookup reads it: the result of step "user"
    user_columns: tuple[str, ...] = ("id", "role")
    backend: str = "sqlite"  # the dialect the SQL was compiled for (ApiProfile.backend)
    # The Page envelope: (page_shape key, JSON field) in the profile's order; a key
    # mapped to "" is omitted.
    page_shape: tuple[tuple[str, str], ...] = (
        ("items", "items"),
        ("limit", "limit"),
        ("offset", "offset"),
    )
    # @endpoint(..., response="M"): the model's name (plan.schema is then exactly its
    # fields, in its order).
    response: str | None = None
    # Authentication: the users-table column the bearer token is looked up in, or the
    # native decoding the token to the users-table key (ApiProfile.auth).
    token_column: str = "token"
    auth_native: str | None = None
    # The caller's role: the Str column `role_column`, or -- with `admin_flag` (a Bool
    # column) -- admin_role when it is true, else `flag_role` (ApiProfile.roles_from).
    role_column: str = "role"
    admin_flag: str | None = None
    flag_role: str = ""
    # The natives the plan's SQL (and its auth statement) calls, with their signatures.
    natives: tuple[tuple[str, NativeSig], ...] = ()
    # Optional (UNSET) parameters an explicit JSON null may set (every column they are
    # written to is nullable); a null for any other optional parameter is a 422.
    null_ok: tuple[str, ...] = ()
    # @endpoint(..., body="form"): body parameters are form fields, not a JSON object.
    body: str = "json"
    # How Uuid / DateTime values are stored (ApiProfile.uuid_storage/datetime_storage);
    # the plan's SQL literals are already in these spellings.
    uuid_storage: str = "canonical"
    datetime_storage: str = "iso_z"
    # ApiProfile.active_column / inactive_status / inactive_detail: the users-table Bool
    # column auth checks after the caller lookup (None: no active check).
    active_column: str | None = None
    inactive_status: int = 400
    inactive_detail: str = "Inactive user"
    # ApiProfile.error_body and the automatic 401/403 detail texts.
    error_body: str = "lattice"
    unauthorized_detail: str | None = None
    forbidden_detail: str | None = None
    # ApiProfile.datetime_wire: how responses spell DateTime values.
    datetime_wire: str = "iso_z"


# ---- helpers ---------------------------------------------------------------------------


class _Substitute(ast.NodeTransformer):
    def __init__(self, subst: Mapping[str, ast.expr]) -> None:
        self.subst = subst

    def visit_Name(self, node: ast.Name) -> ast.expr:
        value = self.subst.get(node.id)
        if value is None:
            return node
        return ast.copy_location(copy.deepcopy(value), node)


def _str(node: ast.expr) -> str:
    match node:
        case ast.Constant(value=str(v)):
            return v
        case _:
            raise LatticeInternalError(f"expected a string literal, got {ast.dump(node)}")


def _strs(node: ast.expr) -> tuple[str, ...]:
    match node:
        case ast.List(elts=elts) | ast.Tuple(elts=elts):
            return tuple(_str(e) for e in elts)
        case _:
            raise LatticeInternalError(f"expected a list of strings, got {ast.dump(node)}")


def _join_keys(node: ast.expr) -> dict[str, tuple[str, ...]]:
    """join on=: {"on": left key names}, plus "right_on" (the right key names, in the
    same order) when a dict literal pairs differently-named columns."""
    pairs = join_on_pairs(node)
    if pairs is None:
        raise LatticeInternalError(f"expected join keys, got {ast.dump(node)}")
    out = {"on": tuple(a for a, _ in pairs)}
    if any(a != b for a, b in pairs):
        out["right_on"] = tuple(b for _, b in pairs)
    return out


def _bool(node: ast.expr) -> bool:
    match node:
        case ast.Constant(value=bool(v)):
            return v
        case _:
            raise LatticeInternalError("sort desc= is not a bool literal")


def _int(node: ast.expr) -> int:
    v = literal_value(node)
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    raise LatticeInternalError(f"expected an int literal, got {ast.dump(node)}")


def _sql_text(node: ast.expr) -> SqlExpr:
    """A readable SQL rendering of an expression (for plans and reports)."""
    return expr_sql(node)


# ---- values bound to names in an endpoint body ------------------------------------------


@dataclass(frozen=True)
class _RelVal:
    rel: str


@dataclass(frozen=True)
class _StepVal:
    step: str  # a write result


_Val = _RelVal | _StepVal


@dataclass(frozen=True)
class _Check:
    id: str
    kind: Literal["one", "require"]
    input: _Val
    error: ErrorSpec
    cond: ast.expr | None
    span: Span


@dataclass(frozen=True)
class _Write:
    id: str
    kind: Literal["insert", "update", "delete"]
    table: str  # schema name
    input: _Val | None
    values: tuple[tuple[str, ast.expr], ...]
    owner: bool
    schema: Schema
    span: Span
    conflict: ErrorSpec | None = None  # unique_or=Conflict(...)


@dataclass(frozen=True)
class _Return:
    value: _Val
    span: Span


_Event = _Check | _Write | _Return


class _Endpoint:
    def __init__(
        self,
        program: CheckedProgram,
        api: ApiProfile,
        info: EndpointInfo,
        consts: Mapping[str, ast.expr],
        step_consts: Mapping[str, Mapping[str, ast.expr]],
    ) -> None:
        self.program = program
        self.api = api
        self.dialect = dialect_for(
            getattr(api, "backend", "sqlite"),
            uuid_storage=getattr(api, "uuid_storage", "canonical"),
            datetime_storage=getattr(api, "datetime_storage", "iso_z"),
        )
        self.info = info
        self.step_defs = steps_of(program.module)
        self.step_consts = step_consts
        self.rels: dict[str, Rel] = {}
        self.events: list[_Event] = []
        self.step_schemas: dict[str, Schema] = {}
        self.subst: dict[str, ast.expr] = dict(consts)
        self.env: dict[str, _Val] = {}
        self.page_of: dict[str, PageArgs] = {}  # rel id -> the page() it is (a view of)
        self.page_rel: dict[str, str] = {}  # rel id -> the page() rel it is (a view of)
        self.val_types: dict[_Val, TableT] = {}  # the checker's type of each bound value
        # optional parameter -> whether every column it is written to is nullable
        self.null_uses: dict[str, bool] = {p.name: True for p in info.params if p.optional}
        for spec in info.params:
            self.subst.pop(spec.name, None)
            if spec.scalar is not None:
                self.subst[spec.name] = ParamRef(spec.name, spec.optional)
        users = api.tables.get(api.users_table)
        if users is not None and any(p.source == "user" for p in info.params):
            key = users.keys[0][0] if users.keys else "id"
            self.rels[USER_PARAM] = Rel(
                USER_PARAM,
                "user",
                (),
                {"table": api.table_names[api.users_table], "key": key},
                users,
            )
            self.env[USER_PARAM] = _RelVal(USER_PARAM)
            caller = user_table(api)
            if caller is not None:
                self.val_types[_RelVal(USER_PARAM)] = caller

    # ---- phase 1 ---------------------------------------------------------------------

    def _expr(self, node: ast.expr, subst: Mapping[str, ast.expr]) -> ast.expr:
        if not any(isinstance(n, ast.Name) and n.id in subst for n in ast.walk(node)):
            return node
        out = _Substitute(subst).visit(copy.deepcopy(node))
        return ast.fix_missing_locations(out)

    def _schema(self, call: ast.Call) -> Schema:
        t = self.program.types.get(id(call))
        if not isinstance(t, TableT):
            raise LatticeInternalError(f"no table type recorded at {span_of(call)}")
        return t.schema

    def _type(self, call: ast.Call) -> TableT:
        t = self.program.types.get(id(call))
        if not isinstance(t, TableT):
            raise LatticeInternalError(f"no table type recorded at {span_of(call)}")
        return t

    def _rel_of(self, val: _Val) -> str:
        """The relation id reading a value (a write result becomes a `rows` relation)."""
        if isinstance(val, _RelVal):
            return val.rel
        rid = f"{val.step}#rows"
        if rid not in self.rels:
            schema = self.step_schemas[val.step]
            self.rels[rid] = Rel(rid, "rows", (), {"step": val.step}, schema)
        return rid

    def _schema_of(self, val: _Val) -> Schema:
        if isinstance(val, _RelVal):
            return self.rels[val.rel].schema
        return self.step_schemas[val.step]

    def lower_body(self) -> None:
        fn = self.info.node
        for stmt in fn.body:
            match stmt:
                case ast.Assign(targets=[ast.Name(id=name)], value=value) if is_constant_expr(
                    value
                ):
                    self.subst[name] = self._expr(value, self.subst)
                case ast.Assign(targets=[ast.Name(id=name)], value=ast.Call() as call):
                    self.subst.pop(name, None)
                    self.env[name] = self._unit(call, name, self.env, self.subst, "")
                    self._note_type(self.env[name], call)
                case ast.Return(value=ast.Name(id=name)) if name in self.env:
                    self.events.append(_Return(self.env[name], span_of(stmt)))
                case _:
                    raise LatticeInternalError(
                        f"unexpected statement in endpoint '{fn.name}' at {span_of(stmt)}"
                    )

    def _note_type(self, val: _Val, node: ast.expr) -> None:
        t = self.program.types.get(id(node))
        if isinstance(t, TableT):
            self.val_types.setdefault(val, t)

    def _owner_is(self, val: _Val, node: ast.expr) -> OwnerIs:
        """owner_is(user) over the rows `val` (whose type has the owner metadata)."""
        t = self.val_types.get(val)
        if t is None:
            t = self.program.types.get(id(node))  # type: ignore[assignment]
        if not isinstance(t, TableT):
            raise LatticeInternalError(f"no table type for owner_is() at {span_of(node)}")
        owner, via = self._owner(t)
        return OwnerIs(owner, via)

    def _owner(self, t: TableT) -> tuple[str | None, tuple[str, str, str, str] | None]:
        """(owner column, None) or (None, (fk, parent table, parent key, parent owner))."""
        if t.owner is not None:
            return t.owner, None
        if t.owner_via is None:
            raise LatticeInternalError("an ownership check over rows without an owner column")
        fk, parent_name = t.owner_via
        parent = self.api.tables[parent_name]
        if parent.owner is None or not parent.keys:
            raise LatticeInternalError(f"owner_via parent {parent_name} has no owner")
        return None, (
            fk,
            self.api.table_names[parent_name],
            via_parent_key(parent),
            parent.owner,
        )

    def _note_nulls(self, schema: Schema, values: tuple[tuple[str, ast.expr], ...]) -> None:
        """Record, per optional parameter, whether the columns it is written to accept
        null (an explicit JSON null for it)."""
        for column, value in values:
            if isinstance(value, ParamRef) and value.optional:
                ok = column in schema.nullable
                self.null_uses[value.name] = self.null_uses.get(value.name, True) and ok

    def _unit(
        self,
        call: ast.Call,
        binding: str,
        env: Mapping[str, _Val],
        subst: Mapping[str, ast.expr],
        prefix: str,
    ) -> _Val:
        if not isinstance(call.func, ast.Name):
            raise LatticeInternalError(f"not a unit call: {ast.dump(call.func)}")
        op = call.func.id
        kws = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        rid = prefix + binding
        unique_or = kws.pop(UNIQUE_OR, None) if op in ("insert", "update") else None
        conflict = _error(unique_or) if unique_or is not None else None

        def table_arg(node: ast.expr) -> _Val:
            if isinstance(node, ast.Name) and node.id in env:
                return env[node.id]
            raise LatticeInternalError(f"unresolved table argument {ast.dump(node)}")

        match op:
            case "one" | "require":
                val = table_arg(call.args[0])
                cond = self._expr(call.args[1], subst) if op == "require" else None
                if op == "require" and is_owner_is(call.args[1]):
                    cond = self._owner_is(val, call.args[0])
                self.events.append(
                    _Check(rid, op, val, _error(kws["else_"]), cond, span_of(call))  # type: ignore[arg-type]
                )
                return val
            case "insert":
                table = call.args[0]
                if not isinstance(table, ast.Name):
                    raise LatticeInternalError("insert() table is not a name")
                schema = self.api.tables[table.id]
                values = tuple((k, self._expr(v, subst)) for k, v in kws.items() if k != "owner")
                self._note_nulls(schema, values)
                self.step_schemas[rid] = self._schema(call)
                self.events.append(
                    _Write(
                        rid,
                        "insert",
                        table.id,
                        None,
                        values,
                        "owner" in kws,
                        schema,
                        span_of(call),
                        conflict,
                    )
                )
                return _StepVal(rid)
            case "update" | "delete":
                val = table_arg(call.args[0])
                t = self._type(call)
                if t.source is None:
                    raise LatticeInternalError(f"{op}() rows have no source table")
                values = tuple((k, self._expr(v, subst)) for k, v in kws.items())
                self._note_nulls(self.api.tables[t.source], values)
                self.step_schemas[rid] = self._schema_of(val)
                self.events.append(
                    _Write(
                        rid,
                        op,  # type: ignore[arg-type]
                        t.source,
                        val,
                        values,
                        False,
                        self.api.tables[t.source],
                        span_of(call),
                        conflict,
                    )
                )
                return _StepVal(rid)
            case _ if op in self.step_defs:
                return self._inline(call, op, rid, env, subst)
            case _:
                inputs = tuple(self._rel_of(table_arg(a)) for a in call.args[: _tables(op)])
                params = self._params(op, call, kws, subst)
                self.rels[rid] = Rel(rid, op, inputs, params, self._schema(call))
                if op == "page":
                    self.page_of[rid] = PageArgs(
                        _page_arg(params["limit"]),
                        _page_arg(params.get("offset", 0)),
                        bool(params.get("total", False)),
                    )
                    self.page_rel[rid] = rid
                elif op in ("derive", "select", "rename") and inputs[0] in self.page_of:
                    self.page_of[rid] = self.page_of[inputs[0]]
                    self.page_rel[rid] = self.page_rel[inputs[0]]
                val = _RelVal(rid)
                self._note_type(val, call)
                return val

    def _params(
        self, op: str, call: ast.Call, kws: Mapping[str, ast.expr], subst: Mapping[str, ast.expr]
    ) -> dict[str, Any]:
        match op:
            case "fetch":
                name = call.args[0]
                if not isinstance(name, ast.Name):
                    raise LatticeInternalError("fetch() table is not a name")
                schema = self.api.tables[name.id]
                order = schema.keys[0] if schema.keys else ()
                return {"table": self.api.table_names[name.id], "order": order}
            case "owned":
                owner, via = self._owner(self._type(call))
                return {"owner": owner} if via is None else {"via": via}
            case "page":
                out: dict[str, Any] = {"limit": self._page_value(kws["limit"], subst)}
                if "offset" in kws:
                    out["offset"] = self._page_value(kws["offset"], subst)
                if "total" in kws and literal_value(kws["total"]) is True:
                    out["total"] = True
                return out
            case "where":
                return {"predicate": self._expr(call.args[1], subst)}
            case "derive":
                return {
                    "columns": tuple(
                        (kw.arg, self._expr(kw.value, subst))
                        for kw in call.keywords
                        if kw.arg is not None
                    )
                }
            case "select":
                return {"cols": _strs(kws["cols"])}
            case "rename":
                return {
                    "mapping": tuple(
                        (kw.arg, _str(kw.value)) for kw in call.keywords if kw.arg is not None
                    )
                }
            case "group":
                return {"by": _strs(kws["by"]), "aggs": self._aggs(call, subst)}
            case "join":
                how = _str(kws["how"]) if "how" in kws else "inner"
                return {**_join_keys(kws["on"]), "how": how}
            case "sort":
                desc = kws.get("desc")
                flag: bool | tuple[bool, ...]
                match desc:
                    case None:
                        flag = False
                    case ast.List(elts=elts) | ast.Tuple(elts=elts):
                        flag = tuple(_bool(e) for e in elts)
                    case _:
                        flag = _bool(desc)
                return {"by": _strs(kws["by"]), "desc": flag}
            case "limit":
                return {"n": _int(self._expr(kws["n"], subst))}
            case _:
                raise LatticeInternalError(f"no API lowering for op '{op}'")

    def _page_value(self, node: ast.expr, subst: Mapping[str, ast.expr]) -> int | ParamRef:
        value = self._expr(node, subst)
        if isinstance(value, ParamRef):
            return value
        return _int(value)

    def _aggs(
        self, call: ast.Call, subst: Mapping[str, ast.expr]
    ) -> tuple[tuple[str, str, ast.expr | None, ast.expr | None], ...]:
        out = []
        for kw in call.keywords:
            if kw.arg is None or kw.arg == "by":
                continue
            match kw.value:
                case ast.Call(func=ast.Attribute(value=ast.Name(id="agg"), attr=func), args=[]):
                    out.append((kw.arg, func, None, None))
                case ast.Call(
                    func=ast.Attribute(value=ast.Name(id="agg"), attr=func), args=[a], keywords=ks
                ):
                    order = next((k.value for k in ks if k.arg == "of"), None)
                    out.append(
                        (
                            kw.arg,
                            func,
                            self._expr(a, subst),
                            None if order is None else self._expr(order, subst),
                        )
                    )
                case _:
                    raise LatticeInternalError(f"not an aggregate: {ast.dump(kw.value)}")
        return tuple(out)

    def _inline(
        self,
        call: ast.Call,
        name: str,
        binding: str,
        env: Mapping[str, _Val],
        subst: Mapping[str, ast.expr],
    ) -> _Val:
        fn = self.step_defs[name]
        sig = self.program.steps.get(name)
        if sig is None:
            raise LatticeInternalError(f"no signature for step '{name}'")
        kws = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        inner_env: dict[str, _Val] = {}
        inner_subst: dict[str, ast.expr] = dict(self.step_consts.get(name, {}))
        table_args = iter(call.args)
        for pname, pt in sig.params:
            match pt:
                case TableT():
                    inner_subst.pop(pname, None)
                    arg = next(table_args, None)
                    if not (isinstance(arg, ast.Name) and arg.id in env):
                        raise LatticeInternalError(f"bad table argument for '{pname}'")
                    inner_env[pname] = env[arg.id]
                case ScalarParamT(scalar=s):
                    value = self._expr(kws[pname], subst)
                    lit = literal_value(value)
                    if s is Scalar.FLOAT and isinstance(lit, int) and not isinstance(lit, bool):
                        value = ast.Constant(float(lit))
                    inner_subst[pname] = value
                case _:
                    raise LatticeInternalError(f"bad parameter type for '{pname}'")
        prefix = f"{binding}/"
        result: _Val | None = None
        for stmt in fn.body:
            match stmt:
                case ast.Assign(targets=[ast.Name(id=local)], value=value) if is_constant_expr(
                    value
                ):
                    inner_subst[local] = self._expr(value, inner_subst)
                case ast.Assign(targets=[ast.Name(id=local)], value=ast.Call() as body_call):
                    inner_subst.pop(local, None)
                    inner_env[local] = self._unit(body_call, local, inner_env, inner_subst, prefix)
                    self._note_type(inner_env[local], body_call)
                case ast.Return(value=ast.Name(id=local)) if local in inner_env:
                    result = inner_env[local]
                case _:
                    raise LatticeInternalError(f"unexpected statement in step '{name}'")
        if result is None:
            raise LatticeInternalError(f"step '{name}' has no return")
        self._note_type(result, call)
        return result

    # ---- phase 2 ---------------------------------------------------------------------

    def emit(self) -> EndpointPlan:
        flags: dict[str, list[tuple[str, ast.expr]]] = {}
        for ev in self.events:
            if isinstance(ev, _Check) and ev.cond is not None and isinstance(ev.input, _RelVal):
                flags.setdefault(ev.input.rel, []).append((_flag(ev.id), ev.cond))
        steps: list[ApiStep] = []
        materialized: dict[str, tuple[str, tuple[str, ...]]] = {}
        step_flags: dict[str, set[str]] = {}  # ReadStep id -> require flags it computes
        total_step: dict[str, str] = {}  # page(total=True) rel id -> the step counting it
        if USER_PARAM in self.rels:  # the auth lookup reads the caller's whole row
            materialized[USER_PARAM] = (USER_PARAM, self.rels[USER_PARAM].schema.names())

        def view_of(rid: str) -> str | None:
            """The step whose rows `rid` is a column subset of: a chain of select()s
            over rows already materialized costs no statement."""
            r = rid
            while True:
                if r in materialized:
                    return materialized[r][0]
                rel = self.rels[r]
                if rel.op == "rows":
                    return str(rel.params["step"])
                if rel.op != "select":
                    return None
                r = rel.inputs[0]

        def rows_of(val: _Val, span: Span) -> str:
            if isinstance(val, _StepVal):
                return val.step
            rid = val.rel
            if rid in materialized:
                return materialized[rid][0]
            rel = self.rels[rid]
            extra = flags.get(rid, [])
            view = None if extra else view_of(rid)
            if view is not None:
                materialized[rid] = (view, rel.schema.names())
                return view
            # a page(total=True) (or a view of one) counts its input in the same
            # statement, the first time it is read
            page = self.page_rel.get(rid)
            total_of: str | None = None
            if page is not None and self.page_of[page].total and page not in total_step:
                total_of = self.rels[page].inputs[0]
                total_step[page] = rid
            sql = compile_select(
                self.rels,
                rid,
                materialized=materialized,
                extra=extra,
                total_of=total_of,
                dialect=self.dialect,
            )
            steps.append(ReadStep(rid, sql, span, rel.schema, tuple(n for n, _ in extra)))
            step_flags[rid] = {n for n, _ in extra}
            materialized[rid] = (rid, rel.schema.names())
            return rid

        returns: str | None = None
        response = Schema(())
        page: PageArgs | None = None
        for ev in self.events:
            match ev:
                case _Check():
                    source = rows_of(ev.input, ev.span)
                    flag_step: str | None = None
                    flag: str | None = None
                    cond: SqlExpr | None = None
                    if ev.cond is not None:
                        cond = _sql_text(ev.cond)
                        flag = _flag(ev.id)
                        if flag in step_flags.get(source, ()):
                            flag_step = source
                        else:  # rows read earlier (the caller, a write, a view): one SELECT
                            rid = self._rel_of(ev.input)
                            flag_step = f"{ev.id}#cond"
                            sql = compile_select(
                                self.rels,
                                rid,
                                materialized=materialized,
                                extra=[(flag, ev.cond)],
                                dialect=self.dialect,
                            )
                            schema = self._schema_of(ev.input)
                            steps.append(ReadStep(flag_step, sql, ev.span, schema, (flag,)))
                    steps.append(
                        CheckStep(ev.id, ev.kind, source, ev.error, cond, ev.span, flag_step, flag)
                    )
                case _Write():
                    steps.append(self._write(ev, rows_of, materialized))
                case _Return():
                    returns = rows_of(ev.value, ev.span)
                    response = self._schema_of(ev.value)
                    if isinstance(ev.value, _RelVal):
                        page = self.page_of.get(ev.value.rel)
                        if page is not None and page.total:
                            counted = total_step.get(self.page_rel[ev.value.rel])
                            if counted is None:
                                raise LatticeInternalError("page(total=True) was never read")
                            page = replace(page, total_step=counted)
        if returns is None:
            raise LatticeInternalError(f"endpoint '{self.info.name}' has no return")
        if self.info.response is not None:
            response = _reorder(response, self.api.responses[self.info.response])
        users = self.api.tables.get(self.api.users_table)
        token_column, auth_native = "token", None
        match self.api.auth:
            case TokenColumn(column=column):
                token_column = column
            case NativeToken(native=native):
                auth_native = native
        role_column, admin_flag, flag_role = "role", None, ""
        match self.api.roles_from:
            case RoleColumn(column=column):
                role_column = column
            case AdminFlag(column=column, role=role):
                admin_flag, flag_role = column, role
        return EndpointPlan(
            self.info.name,
            self.info.method,
            self.info.path,
            self.info.auth,
            self.info.params,
            self.info.success,
            self.info.errors,
            tuple(steps),
            returns,
            self.info.span,
            response,
            page,
            self.api.admin_role,
            self.api.table_names.get(self.api.users_table, "users"),
            users.keys[0][0] if users is not None and users.keys else "id",
            users.names() if users is not None else ("id", "role"),
            self.dialect.name,
            tuple(self.api.page_shape.items()),
            self.info.response,
            token_column,
            auth_native,
            role_column,
            admin_flag,
            flag_role,
            self._natives(auth_native),
            tuple(name for name, ok in self.null_uses.items() if ok),
            body=self.info.body,
            uuid_storage=getattr(self.api, "uuid_storage", "canonical"),
            datetime_storage=getattr(self.api, "datetime_storage", "iso_z"),
            active_column=getattr(self.api, "active_column", None),
            inactive_status=getattr(self.api, "inactive_status", 400),
            inactive_detail=getattr(self.api, "inactive_detail", "Inactive user"),
            error_body=getattr(self.api, "error_body", "lattice"),
            unauthorized_detail=getattr(self.api, "unauthorized_detail", None),
            forbidden_detail=getattr(self.api, "forbidden_detail", None),
            datetime_wire=getattr(self.api, "datetime_wire", "iso_z"),
        )

    def _natives(self, auth_native: str | None) -> tuple[tuple[str, NativeSig], ...]:
        """The natives this endpoint calls (its body, the steps it may inline, and the
        authentication statement), in name order."""
        names: set[str] = set()
        roots: list[ast.AST] = [self.info.node, *self.step_defs.values()]
        for root in roots:
            for n in ast.walk(root):
                match n:
                    case ast.Call(func=ast.Attribute(value=ast.Name(id=ns), attr=name)) if (
                        ns == NATIVE_NS and name in self.api.natives
                    ):
                        names.add(name)
                    case _:
                        pass
        if auth_native is not None and self.info.auth != PUBLIC_ROLE:
            names.add(auth_native)
        return tuple((n, self.api.natives[n]) for n in sorted(names))

    def _write(
        self,
        ev: _Write,
        rows_of: Any,
        materialized: Mapping[str, tuple[str, tuple[str, ...]]],
    ) -> WriteStep:
        table = self.api.table_names[ev.table]
        values = tuple((c, _sql_text(e)) for c, e in ev.values)
        if ev.kind == "insert":
            owner = ev.schema.owner if ev.owner else None
            key = uuid_key(ev.schema)
            given = {c for c, _ in ev.values} | ({owner} if owner is not None else set())
            sql = compile_insert(
                table,
                ev.values,
                ev.schema.names(),
                owner=owner,
                generate=key if key is not None and key not in given else None,
                dialect=self.dialect,
            )
            schema = self.step_schemas[ev.id]
            return WriteStep(
                ev.id, "insert", table, None, values, ev.span, sql, schema, conflict=ev.conflict
            )
        assert ev.input is not None
        source = rows_of(ev.input, ev.span)
        schema = self._schema_of(ev.input)
        cols = set(schema.names())
        key = next((k for k in ev.schema.keys if set(k) <= cols), None)
        if key is None:
            raise LatticeInternalError(f"{ev.kind}() rows lack the key of {ev.table}")
        src = (source, schema.names())
        if ev.kind == "update":
            sql = compile_update(table, key, src, ev.values, dialect=self.dialect)
        else:
            sql = compile_delete(table, key, src, dialect=self.dialect)
        return WriteStep(
            ev.id, ev.kind, table, source, values, ev.span, sql, schema, key, ev.conflict
        )


def _reorder(schema: Schema, fields: tuple[str, ...]) -> Schema:
    """The response columns as a response model's fields, in the model's order (the
    checker's L834 guarantees they are the same set)."""
    types = dict(schema.columns)
    missing = [f for f in fields if f not in types]
    if missing or len(fields) != len(types):
        raise LatticeInternalError(f"response columns {sorted(types)} are not {list(fields)}")
    return Schema(tuple((f, types[f]) for f in fields))


def _flag(check_id: str) -> str:
    return f"__ok_{check_id}"


def _tables(op: str) -> int:
    if op in OP_SHAPES:
        return OP_SHAPES[op].tables
    return {"fetch": 0, "owned": 1, "page": 1}[op]


def _page_arg(v: int | ParamRef) -> int | str:
    return v.name if isinstance(v, ParamRef) else v


def _error(node: ast.expr) -> ErrorSpec:
    match node:
        case ast.Call(func=ast.Name(id=kind), args=args) if kind in API_ERRORS:
            detail = _str(args[0]) if args else _default_detail(kind)
            return ErrorSpec(kind, API_ERRORS[kind], detail)
        case _:
            raise LatticeInternalError(f"else_= is not an error: {ast.dump(node)}")


def _default_detail(kind: str) -> str:
    return "".join(f" {c.lower()}" if c.isupper() else c for c in kind).strip()


def lower_api(program: CheckedProgram) -> tuple[EndpointPlan, ...]:
    """One plan per endpoint, in source order."""
    api = program.profile.api
    if api is None:
        raise LatticeInternalError("lower_api() needs an API profile")
    consts: dict[str, ast.expr] = {}
    step_consts: dict[str, dict[str, ast.expr]] = {}
    plans: list[EndpointPlan] = []
    by_node = {id(info.node): info for info in program.endpoints.values()}
    for stmt in program.module.body:
        match stmt:
            case ast.Assign(targets=[ast.Name(id=name)], value=value) if is_constant_expr(value):
                if any(isinstance(n, ast.Name) and n.id in consts for n in ast.walk(value)):
                    value = ast.fix_missing_locations(
                        _Substitute(consts).visit(copy.deepcopy(value))
                    )
                consts[name] = value
            case ast.FunctionDef() if id(stmt) in by_node:
                ep = _Endpoint(program, api, by_node[id(stmt)], consts, step_consts)
                ep.lower_body()
                plans.append(ep.emit())
            case ast.FunctionDef(name=name):
                step_consts.setdefault(name, dict(consts))
            case _:
                pass
    return tuple(plans)
