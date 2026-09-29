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
from lattice.api.outbox import CREATE_SQL, OUTBOX_TABLE
from lattice.api.sql import (
    CallerRef,
    OwnerIs,
    ParamRef,
    Rel,
    RowsValue,
    SqlExpr,
    SqlQuery,
    compile_delete,
    compile_insert,
    compile_insert_many,
    compile_outbox,
    compile_select,
    compile_update,
    expr_sql,
)
from lattice.catalog import (
    API_ERRORS,
    DEFAULT_FUEL,
    DEFAULT_MAX_VALUE_SIZE,
    ELSE_KW,
    FOR_EACH,
    INSERT_MANY,
    IS_ADMIN,
    NATIVE_NS,
    OP_SHAPES,
    PUBLIC_ROLE,
    ROW_FN,
    T2_CALL,
    T2_EFFECT_PREFIX,
    UNIQUE_OR,
    USER_PARAM,
)
from lattice.check import CheckedProgram
from lattice.check.api_info import EndpointInfo, ParamSpec, user_table
from lattice.check.astutil import is_constant_expr, join_on_pairs, literal_value, steps_of
from lattice.check.p4_types import is_owner_is, mentions_caller
from lattice.check.t2 import CheckedProcedure
from lattice.check.t2.types import FLOAT as T2_FLOAT
from lattice.check.t2.types import ListT as T2ListT
from lattice.check.t2.types import RecordT as T2RecordT
from lattice.check.t2.types import RowT as T2RowT
from lattice.check.t2.types import T2Type
from lattice.check.t2.types import strip_opt as t2_strip_opt
from lattice.check.t2_calls import column_type
from lattice.diagnostics import Span, span_of
from lattice.ir import traverse_params, window_params
from lattice.profile import (
    AdminFlag,
    ApiProfile,
    NativeSig,
    NativeToken,
    NoRoles,
    RoleColumn,
    TokenColumn,
)
from lattice.runtime import LatticeInternalError
from lattice.t2.compile import T2Code, compile_program
from lattice.typesys import (
    ColT,
    LitT,
    Scalar,
    ScalarParamT,
    Schema,
    TableT,
    uuid_key,
    via_parent_key,
)

__all__ = [
    "ApiStep",
    "CallStep",
    "PostStep",
    "T2CheckStep",
    "CheckStep",
    "EndpointPlan",
    "ErrorSpec",
    "ForEachStep",
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
    # update(t, owner=..., else_=E): an owner transfer guarded in its statement (only
    # to an existing users row); fewer rows written than `input` has answers E.
    missing: ErrorSpec | None = None


@dataclass(frozen=True)
class ForEachStep:
    """for_each(t, native.x, args...): ONE INSERT (`sql`) of an outbox row per row of
    step `input`, whose columns `args` hold the native's arguments (in its parameter
    order), after creating the outbox table when it is missing (`ddl`). The INSERT
    returns (id, native, args) per row; the rows are dispatched after COMMIT."""

    id: str
    native: str
    input: str
    args: tuple[str, ...]
    sql: SqlQuery
    span: Span
    ddl: str = CREATE_SQL


@dataclass(frozen=True)
class PostStep:
    """T2-post: the rows of step `input` (a read's one statement) run through the
    row-wise procedure ops `ops` in the app layer, giving rows of `schema`. The op and
    argument descriptors are plain tuples (`lattice.t2.support.t2_post`)."""

    id: str
    input: str
    ops: tuple[Any, ...]
    span: Span
    schema: Schema = field(default_factory=lambda: Schema(()))


@dataclass(frozen=True)
class T2CheckStep:
    """require(t, proc(...), else_=E): procedure `proc` holds on every row of step
    `input` (false or None: E)."""

    id: str
    input: str
    proc: str
    args: tuple[Any, ...]  # (parameter, argument descriptor) pairs
    error: ErrorSpec
    span: Span


@dataclass(frozen=True)
class CallStep:
    """x = call(proc, ...): a whole-value procedure call. `args`: (parameter,
    descriptor) pairs, a table argument being ("rows", step, kind, name, fields).
    `result`: ("rows", fields, many) -- the value becomes this step's rows (`schema`)
    -- or ("scalar", scalar) -- the value is bound like a parameter, `values[bind]`."""

    id: str
    proc: str
    args: tuple[Any, ...]
    result: tuple[Any, ...]
    span: Span
    schema: Schema = field(default_factory=lambda: Schema(()))
    bind: str | None = None


T2Step = PostStep | T2CheckStep | CallStep
ApiStep = ReadStep | CheckStep | WriteStep | ForEachStep | PostStep | T2CheckStep | CallStep


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
    # NativeToken.invalid / .unknown: (status, detail) for a token the auth native
    # decodes to null / whose decoded value matches no users row (None: the 401).
    token_invalid: tuple[int, str] | None = None
    token_unknown: tuple[int, str] | None = None
    # ApiProfile.datetime_wire: how responses spell DateTime values.
    datetime_wire: str = "iso_z"
    # The effect natives the plan's for_each steps send (never SQL functions).
    effects: tuple[tuple[str, NativeSig], ...] = ()
    # T2: the program's compiled procedures (shared by every plan of the program; None
    # without procedures), the request's fuel budget (ApiProfile.fuel), the caller's
    # columns a procedure sees as Caller (public users-table columns, with their
    # scalars), and the effectful procedures for_each sends: (name, parameters).
    t2: T2Code | None = None
    fuel: int = DEFAULT_FUEL
    t2_caller: tuple[tuple[str, str], ...] = ()
    t2_effects: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # ApiProfile.roles_from=NoRoles(role): every caller's role (no column is read).
    fixed_role: str | None = None
    # NativeToken(lookup=col): the unique users column the decoded token is matched
    # against (None: the key, `user_key`).
    token_lookup: str | None = None
    # NativeToken(unique="runtime"): the lookup column is not declared unique, so the
    # authentication statement may match several rows -- an ambiguous caller (R311, 500).
    token_runtime_unique: bool = False
    # The users table's OneOf columns the database spells differently: the
    # authentication statement decodes them, so the caller's row (bound as user.<col>,
    # the role check, the Caller of procedures) holds the values, never the spellings.
    user_stored: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()
    # ApiProfile.max_value_size: the largest value a procedure may build (R622).
    max_value_size: int = DEFAULT_MAX_VALUE_SIZE
    # NativeContext: every profile native that reads settings -- (native, ((setting,
    # scalar), ...)) -- by name, so the natives of steps and procedures are bound too.
    native_settings: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()

    @property
    def t2_post_reads(self) -> tuple[str, ...]:
        """The reads marked "T2-post": procedures run over their rows in the app layer,
        after their one statement."""
        return tuple(s.input for s in self.steps if isinstance(s, PostStep))

    @property
    def uses_t2(self) -> bool:
        """The plan runs procedures in the request (not only after COMMIT)."""
        return any(isinstance(s, PostStep | T2CheckStep | CallStep) for s in self.steps)

    @property
    def writes(self) -> bool:
        """The plan writes (a write step, or an outbox row): its transaction takes the
        write lock up front."""
        return any(isinstance(s, WriteStep | ForEachStep) for s in self.steps)


# ---- helpers ---------------------------------------------------------------------------


def _stored_param(schema: Schema) -> dict[str, Any]:
    """A read's `stored` parameter: the table's OneOf columns spelled differently in the
    database (decoded in the SELECT), or nothing."""
    stored = schema.stored_spellings()
    return {"stored": stored} if stored else {}


def native_settings(api: ApiProfile) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    """Every native of the profile that reads settings, with each setting's scalar."""
    settings = getattr(api, "settings", {})
    return tuple(
        (name, tuple((s, settings[s].value) for s in sig.settings))
        for name, sig in sorted(api.natives.items())
        if getattr(sig, "settings", ())
    )


class _Substitute(ast.NodeTransformer):
    def __init__(self, subst: Mapping[str, ast.expr]) -> None:
        self.subst = subst

    def visit_Name(self, node: ast.Name) -> ast.expr:
        value = self.subst.get(node.id)
        if value is None:
            return node
        return ast.copy_location(copy.deepcopy(value), node)


class _RowRef(ast.expr):
    """`r.x` in a write/derive value, lowered in phase 1: column `column` of the one row
    of value `val` (the checker made sure r has exactly one row). Emission turns it into
    a RowsValue over r's materialized rows: a scalar subquery in the statement."""

    _fields = ()

    def __init__(self, val: _Val | None = None, column: str = "") -> None:
        super().__init__()
        self.val = val
        self.column = column

    def __deepcopy__(self, memo: dict[int, Any]) -> _RowRef:
        return ast.copy_location(_RowRef(self.val, self.column), self)


class _RowRefs(ast.NodeTransformer):
    """r.x (r a table value of the endpoint, not the caller) -> _RowRef."""

    def __init__(self, env: Mapping[str, _Val]) -> None:
        self.env = env

    def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
        name = node.value.id if isinstance(node.value, ast.Name) else None
        if name is not None and name != USER_PARAM and name in self.env:
            return ast.copy_location(_RowRef(self.env[name], node.attr), node)
        return self.generic_visit(node)  # type: ignore[return-value]


def _has_row_ref(value: Any) -> bool:
    if isinstance(value, ast.AST):
        return any(isinstance(n, _RowRef) for n in ast.walk(value))
    if isinstance(value, tuple | list):
        return any(_has_row_ref(v) for v in value)
    return False


def _map_row_refs(value: Any, lower: Any) -> Any:
    """`value` (an expression, or tuples/lists of them) with every _RowRef lowered."""
    if isinstance(value, _RowRef):
        return lower(value)
    if isinstance(value, ast.AST):
        if not _has_row_ref(value):
            return value

        class _Lower(ast.NodeTransformer):
            def visit(self, node: ast.AST) -> Any:
                if isinstance(node, _RowRef):
                    return ast.copy_location(lower(node), node)
                return super().visit(node)

        return _Lower().visit(copy.deepcopy(value))  # the plan's own copy stays as it is
    if isinstance(value, tuple):
        return tuple(_map_row_refs(v, lower) for v in value)
    if isinstance(value, list):
        return [_map_row_refs(v, lower) for v in value]
    return value


class _Caller(ast.NodeTransformer):
    """user.<column> / user.is_admin / is_admin(user) -> CallerRef (binds of the
    request: the caller's row was read by authentication)."""

    def __init__(self, key: str) -> None:
        self.key = key

    def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
        if isinstance(node.value, ast.Name) and node.value.id == USER_PARAM:
            if node.attr == IS_ADMIN:
                ref = CallerRef(None, admin=True)
            else:
                ref = CallerRef(node.attr, key=node.attr == self.key)
            return ast.copy_location(ref, node)
        return self.generic_visit(node)  # type: ignore[return-value]

    def visit_Call(self, node: ast.Call) -> ast.expr:
        if isinstance(node.func, ast.Name) and node.func.id == IS_ADMIN:
            return ast.copy_location(CallerRef(None, admin=True), node)
        return self.generic_visit(node)  # type: ignore[return-value]


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
    kind: Literal["insert", "update", "delete", "insert_many"]
    table: str  # schema name
    input: _Val | None
    values: tuple[tuple[str, ast.expr], ...]
    owner: bool
    schema: Schema
    span: Span
    conflict: ErrorSpec | None = None  # unique_or=Conflict(...)
    missing: ErrorSpec | None = None  # update(t, owner=..., else_=...)
    owner_from: _Val | None = None  # update(t, owner=<one-row users table>)


@dataclass(frozen=True)
class _Return:
    value: _Val
    span: Span


@dataclass(frozen=True)
class _ForEach:
    id: str
    input: _Val
    native: str
    sig: NativeSig
    args: tuple[ast.expr, ...]  # lowered (constants and parameters substituted)
    span: Span


@dataclass(frozen=True)
class _T2Check:
    id: str
    input: _Val
    proc: str
    args: tuple[Any, ...]
    error: ErrorSpec
    span: Span


@dataclass(frozen=True)
class _T2Call:
    id: str
    proc: str
    # (parameter, descriptor); a table argument is ("rows", _Val, kind, name, fields)
    args: tuple[Any, ...]
    result: tuple[Any, ...]
    span: Span
    schema: Schema
    bind: str | None


@dataclass(frozen=True)
class _Post:
    """A relation computed in the app layer (T2-post): the rows of the SQL relation
    `base` (one statement) through the procedure ops `ops`, giving `schema`."""

    base: str
    ops: tuple[Any, ...]
    schema: Schema
    span: Span  # the first procedure op (where a R620 is located)


_Event = _Check | _Write | _Return | _ForEach | _T2Check | _T2Call


class _Endpoint:
    def __init__(
        self,
        program: CheckedProgram,
        api: ApiProfile,
        info: EndpointInfo,
        consts: Mapping[str, ast.expr],
        step_consts: Mapping[str, Mapping[str, ast.expr]],
        t2: T2Code | None = None,
    ) -> None:
        self.program = program
        self.api = api
        self.t2 = t2
        self.post: dict[str, _Post] = {}  # rel id -> its T2-post computation
        self.dialect = dialect_for(
            getattr(api, "backend", "sqlite"),
            uuid_storage=getattr(api, "uuid_storage", "canonical"),
            datetime_storage=getattr(api, "datetime_storage", "iso_z"),
        )
        self.info = info
        # ApiProfile.null_order: where null keys go in a sort without nulls= and in
        # window order (resolved here, so the SQL -- and the generated app -- carry it)
        self.null_order: str = getattr(api, "null_order", "last")
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
        # the users table's key: user.<key> is the caller's id (:__user_id)
        self.user_key = users.keys[0][0] if users is not None and users.keys else "id"
        if users is not None and any(p.source == "user" for p in info.params):
            key = self.user_key
            self.rels[USER_PARAM] = Rel(
                USER_PARAM,
                "user",
                (),
                {
                    "table": api.table_names[api.users_table],
                    "key": key,
                    **_stored_param(users),
                },
                users,
            )
            self.env[USER_PARAM] = _RelVal(USER_PARAM)
            caller = user_table(api)
            if caller is not None:
                self.val_types[_RelVal(USER_PARAM)] = caller

    # ---- phase 1 ---------------------------------------------------------------------

    def _expr(self, node: ast.expr, subst: Mapping[str, ast.expr]) -> ast.expr:
        out = node
        if any(isinstance(n, ast.Name) and n.id in subst for n in ast.walk(node)):
            out = ast.fix_missing_locations(_Substitute(subst).visit(copy.deepcopy(node)))
        if mentions_caller(out):
            out = _Caller(self.user_key).visit(copy.deepcopy(out) if out is node else out)
        if any(
            isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id in self.env
            and n.value.id != USER_PARAM
            for n in ast.walk(out)
        ):  # r.x: a value of a one-row table (N1b)
            out = _RowRefs(self.env).visit(copy.deepcopy(out) if out is node else out)
        return out

    def _lower_row_refs(self, value: Any, rows_of: Any, span: Span) -> Any:
        """`value` with every _RowRef read from its row's materialized step."""

        def lower(ref: _RowRef) -> RowsValue:
            assert ref.val is not None
            step = rows_of(ref.val, span)
            return RowsValue(step, self._schema_of(ref.val).names(), ref.column)

        return _map_row_refs(value, lower)

    def _resolve_rels(
        self, rid: str, rows_of: Any, materialized: Mapping[str, Any], span: Span
    ) -> None:
        """Lower the _RowRefs of `rid` and the relations it reads (not yet materialized):
        their rows are read first, so the statement binds them."""
        stack, seen = [rid], set()
        while stack:
            r = stack.pop()
            if r in seen or r in materialized or r not in self.rels:
                continue
            seen.add(r)
            rel = self.rels[r]
            if _has_row_ref(tuple(rel.params.values())):
                params = {k: self._lower_row_refs(v, rows_of, span) for k, v in rel.params.items()}
                self.rels[r] = replace(rel, params=params)
            stack.extend(rel.inputs)

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
                case ast.Assign(
                    targets=[ast.Name(id=name)], value=ast.Call(func=ast.Name(id=op)) as call
                ) if self.t2 is not None and op == T2_CALL:
                    self.subst.pop(name, None)
                    self._t2_call(name, call)
                case ast.Assign(targets=[ast.Name(id=name)], value=ast.Call() as call):
                    self.subst.pop(name, None)
                    self.env[name] = self._unit(call, name, self.env, self.subst, "")
                    self._note_type(self.env[name], call)
                case ast.Return(value=ast.Name(id=name)) if name in self.env:
                    self.events.append(_Return(self.env[name], span_of(stmt)))
                case ast.Expr(value=ast.Call(func=ast.Name(id=op)) as call) if op == FOR_EACH:
                    self._for_each(call)
                case _:
                    raise LatticeInternalError(
                        f"unexpected statement in endpoint '{fn.name}' at {span_of(stmt)}"
                    )

    def _for_each(self, call: ast.Call) -> None:
        table, native = call.args[0], call.args[1]
        if not (isinstance(table, ast.Name) and table.id in self.env):
            raise LatticeInternalError("for_each() table is not a bound name")
        k = sum(isinstance(ev, _ForEach) for ev in self.events)
        args = tuple(self._expr(a, self.subst) for a in call.args[2:])
        if isinstance(native, ast.Name) and self._procedure(native.id) is not None:
            # an effectful procedure: its outbox rows carry its column-typed arguments
            proc = self._procedure(native.id)
            assert proc is not None
            scalars = tuple(_column(t)[0] for _, t in proc.sig.params)
            sig = NativeSig(scalars, effect=True)
            name = T2_EFFECT_PREFIX + native.id
            self.events.append(
                _ForEach(f"_for_each{k}", self.env[table.id], name, sig, args, span_of(call))
            )
            return
        if not isinstance(native, ast.Attribute):
            raise LatticeInternalError("for_each() native is not native.<name>")
        sig = self.api.natives[native.attr]
        self.events.append(
            _ForEach(f"_for_each{k}", self.env[table.id], native.attr, sig, args, span_of(call))
        )

    # ---- T2: procedures (lattice.check.t2_calls; descriptors: lattice.t2.support) --------

    def _procedure(self, name: str) -> CheckedProcedure | None:
        t2 = self.program.t2
        return t2.procedures.get(name) if t2 is not None else None

    def _is_proc_call(self, node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and self._procedure(node.func.id) is not None
        )

    def _t2_post(self, call: ast.Call, op: str, env: Mapping[str, _Val]) -> bool:
        """A procedure op (derive / where calling a procedure), or any op over rows
        computed in the app layer (the checker allows select, rename, page, limit)."""
        if op == "where" and len(call.args) > 1 and self._is_proc_call(call.args[1]):
            return True
        if op == "derive" and any(self._is_proc_call(kw.value) for kw in call.keywords):
            return True
        first = call.args[0] if call.args else None
        val = env.get(first.id) if isinstance(first, ast.Name) else None
        return isinstance(val, _RelVal) and val.rel in self.post

    def _post_op(
        self,
        call: ast.Call,
        op: str,
        rid: str,
        val: _Val,
        kws: Mapping[str, ast.expr],
        subst: Mapping[str, ast.expr],
    ) -> _Val:
        """A T2-post relation: SQL does everything up to `base` in one statement; the
        procedure ops (and select/rename after them) run over its rows. page/limit
        after a procedure derive commute into the SQL (the derive keeps every row)."""
        rel_in = self._rel_of(val)
        prev = self.post.get(rel_in)
        base, ops = (prev.base, prev.ops) if prev is not None else (rel_in, ())
        in_schema = self._schema_of(val)
        schema = self._schema(call)
        view = rel_in  # the relation a page view is read through
        match op:
            case "derive":
                sql_cols = [kw for kw in call.keywords if not self._is_proc_call(kw.value)]
                if sql_cols:  # the SQL columns of the same derive, computed in the read
                    if prev is not None:
                        raise LatticeInternalError("a SQL derive over T2-post rows")
                    cols = tuple((kw.arg, self._expr(kw.value, subst)) for kw in sql_cols)
                    types = [(kw.arg, self._scalar_of(kw.value)) for kw in sql_cols]
                    sql_id = f"{rid}#sql"
                    sql_schema = self.rels[base].schema.with_columns(types)  # type: ignore[arg-type]
                    params = {"columns": cols}
                    self.rels[sql_id] = Rel(sql_id, "derive", (base,), params, sql_schema)
                    if base in self.page_of:
                        self.page_of[sql_id] = self.page_of[base]
                        self.page_rel[sql_id] = self.page_rel[base]
                    base = sql_id
                for kw in call.keywords:
                    if self._is_proc_call(kw.value):
                        assert isinstance(kw.value, ast.Call)
                        proc, args = self._t2_args(kw.value, in_schema, subst)
                        scalar = schema.get(kw.arg or "")
                        assert scalar is not None
                        ops += (("derive", kw.arg, proc, args, scalar.value),)
            case "where":
                proc, args = self._t2_args(call.args[1], in_schema, subst)
                ops += (("where", proc, args),)
            case "select":
                ops += (("select", _strs(kws["cols"])),)
            case "rename":
                mapping = tuple((kw.arg, _str(kw.value)) for kw in call.keywords if kw.arg)
                ops += (("rename", mapping),)
            case "page" | "limit":
                params = self._params(op, call, kws, subst)
                sql_id = f"{rid}#sql"
                self.rels[sql_id] = Rel(sql_id, op, (base,), params, self.rels[base].schema)
                if op == "page":
                    self.page_of[sql_id] = PageArgs(
                        _page_arg(params["limit"]),
                        _page_arg(params.get("offset", 0)),
                        bool(params.get("total", False)),
                    )
                    self.page_rel[sql_id] = sql_id
                base = view = sql_id
            case _:
                raise LatticeInternalError(f"'{op}' over T2-post rows")
        self.post[rid] = _Post(base, ops, schema, prev.span if prev else span_of(call))
        self.rels[rid] = Rel(rid, "rows", (), {"step": rid}, schema)
        page_src = view if view in self.page_of else base
        if page_src in self.page_of:
            self.page_of[rid] = self.page_of[page_src]
            self.page_rel[rid] = self.page_rel[page_src]
        out = _RelVal(rid)
        self._note_type(out, call)
        return out

    def _scalar_of(self, node: ast.expr) -> Scalar:
        t = self.program.types.get(id(node))
        if isinstance(t, ColT | LitT):
            return t.scalar
        raise LatticeInternalError(f"no scalar type recorded at {span_of(node)}")

    def _t2_args(
        self, node: ast.expr, schema: Schema, subst: Mapping[str, ast.expr]
    ) -> tuple[str, tuple[Any, ...]]:
        """(procedure, (parameter, descriptor) pairs) of a scalar call over rows of
        `schema`."""
        assert isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        proc = self._procedure(node.func.id)
        assert proc is not None
        names = [p for p, _ in proc.sig.params]
        pairs = list(zip(names, node.args, strict=False))
        pairs += [(kw.arg, kw.value) for kw in node.keywords if kw.arg is not None]
        want = dict(proc.sig.params)
        args = tuple(
            (p, self._t2_value(a, want[p], schema, subst))
            for p, a in sorted(pairs, key=lambda pa: names.index(pa[0]))
        )
        return proc.name, args

    def _t2_value(
        self, node: ast.expr, want: T2Type, schema: Schema | None, subst: Mapping[str, ast.expr]
    ) -> tuple[Any, ...]:
        """The descriptor of one procedure argument (`schema`: the current row's). An
        Int handed to a Float parameter arrives as a float (R-widen): a constant is
        converted here, a parameter or column value when it is read (the descriptor
        names the Float scalar)."""
        widen = t2_strip_opt(want) == T2_FLOAT
        if isinstance(node, ast.Name) and node.id == USER_PARAM:
            return ("caller",)
        if _is_named_call(node, ROW_FN):
            if schema is None:
                raise LatticeInternalError("row() without a row")
            return ("row", *self._row_shape(want, schema))
        value = self._expr(node, subst)
        match value:
            case ast.Constant(value=v):
                return ("const", float(v) if widen and type(v) is int else v)
            case ast.UnaryOp(op=ast.USub(), operand=ast.Constant(value=v)):
                return ("const", float(-v) if widen and type(v) is int else -v)  # type: ignore[operator]
            case ParamRef(name=name):
                return ("param", name, "Float") if widen else ("param", name)
            case CallerRef(admin=True):
                return ("is_admin",)
            case CallerRef(column=str(column)):
                users = self.api.tables[self.api.users_table]
                scalar = users.get(column)
                assert scalar is not None
                return ("caller_col", column, "Float" if widen else scalar.value)
            case ast.Call(func=ast.Attribute(value=ast.Name(id="fn"), attr="now")):
                return ("now",)
            case ast.Call(func=ast.Name(id="col"), args=[ast.Constant(value=str(name))]):
                scalar = schema.get(name) if schema is not None else None
                if scalar is None:
                    raise LatticeInternalError(f"procedure argument col('{name}') is no column")
                return ("col", name, "Float" if widen else scalar.value)
        raise LatticeInternalError(f"unsupported procedure argument {ast.dump(value)}")

    def _row_shape(self, want: T2Type, schema: Schema) -> tuple[str, str, tuple[Any, ...]]:
        """(kind, name, ((column, scalar), ...)) of a whole row handed over as `want`."""
        if isinstance(want, T2RowT):
            table = self.api.tables[want.table]
            names = [c for c in table.names() if want.private or c not in table.private]
            fields = tuple((c, table.get(c).value) for c in names)  # type: ignore[union-attr]
            return "row", want.table, fields
        if isinstance(want, T2RecordT):
            t2 = self.program.t2
            assert t2 is not None
            rec = t2.records[want.name]
            fields = tuple((f, _column(ft)[0].value) for f, ft in rec.fields)
            return "record", rec.name, fields
        raise LatticeInternalError(f"a row handed over as {want}")

    def _t2_call(self, name: str, call: ast.Call) -> None:
        """name = call(proc, ...): a CallStep; its value becomes rows (a step result)
        or a value bound like a parameter."""
        target = call.args[0]
        assert isinstance(target, ast.Name)
        proc = self._procedure(target.id)
        assert proc is not None
        want = dict(proc.sig.params)
        order = [p for p, _ in proc.sig.params]
        args: list[tuple[str, tuple[Any, ...]]] = []
        for kw in sorted(call.keywords, key=lambda k: order.index(k.arg or "")):
            assert kw.arg is not None
            ptype = want[kw.arg]
            if isinstance(ptype, T2ListT) and isinstance(ptype.elem, T2RowT | T2RecordT):
                if not (isinstance(kw.value, ast.Name) and kw.value.id in self.env):
                    raise LatticeInternalError("call() table argument is not a bound name")
                val = self.env[kw.value.id]
                shape = self._row_shape(ptype.elem, self._schema_of(val))
                args.append((kw.arg, ("rows", val, *shape)))
            else:
                args.append((kw.arg, self._t2_value(kw.value, ptype, None, self.subst)))
        t = self.program.types.get(id(call))
        span = span_of(call)
        if isinstance(t, TableT):
            fields = tuple((c, s.value) for c, s in t.schema.columns)
            result: tuple[Any, ...] = ("rows", fields, not t.at_most_one_row)
            self.step_schemas[name] = t.schema
            self.env[name] = _StepVal(name)
            self.val_types[self.env[name]] = t
            self.events.append(_T2Call(name, proc.name, tuple(args), result, span, t.schema, None))
            return
        scalar, _ = _column(proc.sig.returns)
        self.subst[name] = ParamRef(name)
        self.events.append(
            _T2Call(name, proc.name, tuple(args), ("scalar", scalar.value), span, Schema(()), name)
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
        unique_or = kws.pop(UNIQUE_OR, None) if op in ("insert", "update", INSERT_MANY) else None
        conflict = _error(unique_or) if unique_or is not None else None

        def table_arg(node: ast.expr) -> _Val:
            if isinstance(node, ast.Name) and node.id in env:
                return env[node.id]
            raise LatticeInternalError(f"unresolved table argument {ast.dump(node)}")

        match op:
            case "require" if self._is_proc_call(call.args[1]):
                val = table_arg(call.args[0])
                proc, args = self._t2_args(call.args[1], self._schema_of(val), subst)
                self.events.append(
                    _T2Check(rid, val, proc, args, _error(kws["else_"]), span_of(call))
                )
                return val
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
                schema = self.api.tables[t.source]
                missing = _error(kws.pop(ELSE_KW)) if ELSE_KW in kws else None
                new_owner = kws.pop("owner", None)
                values = tuple((k, self._expr(v, subst)) for k, v in kws.items())
                owner_from: _Val | None = None
                if new_owner is not None:  # an owner transfer: owner= sets the owner column
                    if schema.owner is None:
                        raise LatticeInternalError(f"owner= on {t.source}, which has no owner")
                    if isinstance(new_owner, ast.Name) and new_owner.id in env:
                        if new_owner.id == USER_PARAM:  # the caller
                            values += ((schema.owner, CallerRef(self.user_key, key=True)),)
                        else:  # a one-row users table: its key, in the statement
                            owner_from = env[new_owner.id]
                    else:
                        values += ((schema.owner, self._expr(new_owner, subst)),)
                self._note_nulls(schema, values)
                self.step_schemas[rid] = self._schema_of(val)
                self.events.append(
                    _Write(
                        rid,
                        op,  # type: ignore[arg-type]
                        t.source,
                        val,
                        values,
                        False,
                        schema,
                        span_of(call),
                        conflict,
                        missing,
                        owner_from,
                    )
                )
                return _StepVal(rid)
            case _ if op == INSERT_MANY and self.t2 is not None:
                table = call.args[0]
                if not isinstance(table, ast.Name):
                    raise LatticeInternalError("insert_many() table is not a name")
                schema = self.api.tables[table.id]
                self.step_schemas[rid] = self._schema(call)
                self.events.append(
                    _Write(
                        rid,
                        "insert_many",
                        table.id,
                        table_arg(kws["rows"]),
                        (),
                        "owner" in kws,
                        schema,
                        span_of(call),
                        conflict,
                    )
                )
                return _StepVal(rid)
            case _ if op in self.step_defs:
                return self._inline(call, op, rid, env, subst)
            case _ if self.t2 is not None and self._t2_post(call, op, env):
                return self._post_op(call, op, rid, table_arg(call.args[0]), kws, subst)
            case _:
                inputs = tuple(self._rel_of(table_arg(a)) for a in call.args[: _tables(op)])
                params = self._params(op, call, kws, subst)
                rel_op = "owned" if op == "accessible" else op  # the same filter
                self.rels[rid] = Rel(rid, rel_op, inputs, params, self._schema(call))
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
                return {
                    "table": self.api.table_names[name.id],
                    "order": order,
                    **_stored_param(schema),
                }
            case "accessible" | "owned" | "mine":
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
                # a direction is a bool, or a Bool expression over parameters and
                # literals (the request's direction, computed in SQL)
                def direction(node: ast.expr) -> bool | ast.expr:
                    value = self._expr(node, subst)
                    lit = literal_value(value)
                    return lit if isinstance(lit, bool) else value

                desc = kws.get("desc")
                flag: bool | ast.expr | tuple[bool | ast.expr, ...]
                match desc:
                    case None:
                        flag = False
                    case ast.List(elts=elts) | ast.Tuple(elts=elts):
                        flag = tuple(direction(e) for e in elts)
                    case _:
                        flag = direction(desc)
                out = {"by": _strs(kws["by"]), "desc": flag}
                if "nulls" in kws:  # absent: the profile's null order
                    out["nulls"] = _str(kws["nulls"])
                elif self.null_order != "last":
                    out["nulls"] = self.null_order
                return out
            case "limit":
                return {"n": _int(self._expr(kws["n"], subst))}
            case "window":
                return window_params(call, kws, lambda e: self._expr(e, subst), self.null_order)
            case "traverse":
                return traverse_params(kws, self._page_value(kws["max_depth"], subst))
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
            if isinstance(ev, _ForEach) and _computed(ev.args) and isinstance(ev.input, _RelVal):
                # computed arguments ride on the input's own SELECT, like require flags
                flags.setdefault(ev.input.rel, []).extend(_arg_columns(ev))
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
            post = self.post.get(rid)
            if post is not None:  # T2-post: the base read's one statement, then the ops
                source = rows_of(_RelVal(post.base), span)
                steps.append(PostStep(rid, source, post.ops, post.span, post.schema))
                materialized[rid] = (rid, post.schema.names())
                return rid
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
            self._resolve_rels(rid, rows_of, materialized, span)
            if total_of is not None:
                self._resolve_rels(total_of, rows_of, materialized, span)
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
                            self._resolve_rels(rid, rows_of, materialized, ev.span)
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
                case _T2Check():
                    source = rows_of(ev.input, ev.span)
                    steps.append(T2CheckStep(ev.id, source, ev.proc, ev.args, ev.error, ev.span))
                case _T2Call():
                    args = tuple(
                        (p, ("rows", rows_of(a[1], ev.span), *a[2:]) if a[0] == "rows" else a)
                        for p, a in ev.args
                    )
                    steps.append(
                        CallStep(ev.id, ev.proc, args, ev.result, ev.span, ev.schema, ev.bind)
                    )
                case _ForEach():
                    source = rows_of(ev.input, ev.span)
                    columns: tuple[str, ...]
                    if not _computed(ev.args):
                        columns = tuple(_col_name(a) or "" for a in ev.args)
                    else:
                        extra = _arg_columns(ev)
                        columns = tuple(n for n, _ in extra)
                        if not set(columns) <= step_flags.get(source, set()):
                            # rows read earlier (a write, the caller, a view): one SELECT
                            rid = self._rel_of(ev.input)
                            args_step = f"{ev.id}#args"
                            self._resolve_rels(rid, rows_of, materialized, ev.span)
                            sql = compile_select(
                                self.rels,
                                rid,
                                materialized=materialized,
                                extra=extra,
                                dialect=self.dialect,
                            )
                            schema = self._schema_of(ev.input)
                            steps.append(ReadStep(args_step, sql, ev.span, schema, columns))
                            source = args_step
                    outbox = compile_outbox(
                        OUTBOX_TABLE,
                        self.info.name,
                        ev.native,
                        [p.value for p in ev.sig.params],
                        (source, columns),
                        dialect=self.dialect,
                    )
                    steps.append(ForEachStep(ev.id, ev.native, source, columns, outbox, ev.span))
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
        token_column, auth_native, token_lookup = "token", None, None
        runtime_unique = False
        token_invalid: tuple[int, str] | None = None
        token_unknown: tuple[int, str] | None = None
        match self.api.auth:
            case TokenColumn(column=column):
                token_column = column
            case NativeToken(native=native, lookup=lookup, unique=unique):
                auth_native, token_lookup = native, lookup
                runtime_unique = unique == "runtime"
                token_invalid = getattr(self.api.auth, "invalid", None)
                token_unknown = getattr(self.api.auth, "unknown", None)
        role_column, admin_flag, flag_role, fixed_role = "role", None, "", None
        match self.api.roles_from:
            case RoleColumn(column=column):
                role_column = column
            case AdminFlag(column=column, role=role):
                admin_flag, flag_role = column, role
            case NoRoles(role=role):
                fixed_role = role
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
            token_invalid=token_invalid,
            token_unknown=token_unknown,
            datetime_wire=getattr(self.api, "datetime_wire", "iso_z"),
            effects=tuple(
                (name, self.api.natives[name])
                for name in sorted({ev.native for ev in self.events if isinstance(ev, _ForEach)})
                if not name.startswith(T2_EFFECT_PREFIX)
            ),
            t2=self.t2,
            fuel=getattr(self.api, "fuel", DEFAULT_FUEL),
            t2_caller=self._t2_caller(),
            t2_effects=self._t2_effects(),
            fixed_role=fixed_role,
            token_lookup=token_lookup,
            token_runtime_unique=runtime_unique,
            native_settings=native_settings(self.api),
            user_stored=users.stored_spellings() if users is not None else (),
            max_value_size=getattr(self.api, "max_value_size", DEFAULT_MAX_VALUE_SIZE),
        )

    def _t2_caller(self) -> tuple[tuple[str, str], ...]:
        """The users-table columns a procedure sees on the Caller (its public ones)."""
        if self.t2 is None:
            return ()
        users = self.api.tables.get(self.api.users_table)
        if users is None:
            return ()
        return tuple((c, s.value) for c, s in users.columns if c not in users.private)

    def _t2_effects(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """The effectful procedures this endpoint's for_each steps send."""
        names = sorted(
            {
                ev.native[len(T2_EFFECT_PREFIX) :]
                for ev in self.events
                if isinstance(ev, _ForEach) and ev.native.startswith(T2_EFFECT_PREFIX)
            }
        )
        out = []
        for name in names:
            proc = self._procedure(name)
            assert proc is not None
            out.append((name, tuple(p for p, _ in proc.sig.params)))
        return tuple(out)

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
        if _has_row_ref(ev.values):  # r.x: read r's row first (a scalar subquery)
            ev = replace(ev, values=self._lower_row_refs(ev.values, rows_of, ev.span))
        table = self.api.table_names[ev.table]
        values = tuple((c, _sql_text(e)) for c, e in ev.values)
        if ev.kind == "insert_many":  # every row of a procedure's value, one statement
            assert ev.input is not None
            source = rows_of(ev.input, ev.span)
            cols = self._schema_of(ev.input).names()
            owner = ev.schema.owner if ev.owner else None
            key = uuid_key(ev.schema)
            given = set(cols) | ({owner} if owner is not None else set())
            sql = compile_insert_many(
                table,
                (source, cols),
                ev.schema.names(),
                owner=owner,
                generate=key if key is not None and key not in given else None,
                dialect=self.dialect,
            )
            schema = self.step_schemas[ev.id]
            return WriteStep(
                ev.id, "insert", table, None, (), ev.span, sql, schema, conflict=ev.conflict
            )
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
                stored=ev.schema.stored_spellings(),
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
        written = ev.values
        if ev.owner_from is not None and ev.schema.owner is not None:
            # the new owner: the key of the one row of a users table read earlier
            owner_step = rows_of(ev.owner_from, ev.span)
            owner_cols = self._schema_of(ev.owner_from).names()
            written += ((ev.schema.owner, RowsValue(owner_step, owner_cols, self.user_key)),)
            values = tuple((c, _sql_text(e)) for c, e in written)
        if ev.kind == "update":
            exists = None
            if ev.missing is not None and ev.schema.owner is not None:
                users = self.api.table_names[self.api.users_table]
                exists = (users, self.user_key, ev.schema.owner)
            sql = compile_update(
                table,
                key,
                src,
                written,
                exists=exists,
                dialect=self.dialect,
                stored=ev.schema.stored_spellings(),
            )
        else:
            sql = compile_delete(table, key, src, dialect=self.dialect)
        return WriteStep(
            ev.id,
            ev.kind,
            table,
            source,
            values,
            ev.span,
            sql,
            schema,
            key,
            ev.conflict,
            ev.missing,
        )


def _is_named_call(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name


def _column(t: T2Type) -> tuple[Scalar, bool]:
    """(scalar, nullable) of a column-typed procedure value (the checker made sure)."""
    ct = column_type(t)
    if ct is None:
        raise LatticeInternalError(f"{t} is no column type")
    return ct


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


def _col_name(node: ast.expr) -> str | None:
    """The column a bare col("name") reads, else None."""
    match node:
        case ast.Call(func=ast.Name(id="col"), args=[ast.Constant(value=str(name))], keywords=[]):
            return name
    return None


def _computed(args: tuple[ast.expr, ...]) -> bool:
    """Some for_each argument is more than a bare column (so SQL computes it)."""
    return any(_col_name(a) is None for a in args)


def _arg_columns(ev: _ForEach) -> list[tuple[str, ast.expr]]:
    """The extra columns computing a for_each's arguments, in parameter order."""
    return [(f"__arg{ev.id}_{i}", a) for i, a in enumerate(ev.args)]


def _tables(op: str) -> int:
    if op in OP_SHAPES:
        return OP_SHAPES[op].tables
    return {"fetch": 0, "accessible": 1, "owned": 1, "mine": 1, "page": 1}[op]


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
    t2 = compile_program(program)  # the procedures, compiled once for every plan
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
                ep = _Endpoint(program, api, by_node[id(stmt)], consts, step_consts, t2)
                ep.lower_body()
                plans.append(ep.emit())
            case ast.FunctionDef(name=name):
                step_consts.setdefault(name, dict(consts))
            case _:
                pass
    return tuple(plans)
