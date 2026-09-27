"""P4 — types and schemas.

Walks the module in source order with an environment (table name -> TableT) that
is inherited down the statement list; each op's transfer function synthesizes
the result schema, which is recorded on the call node. Step definitions are typed
when reached and entered into the step table used by later calls.

A named constant (``name = <column-free scalar expression>``) is typed against an
empty schema; well-typed constants join the scalar environment (visible to later
expressions, and module constants to later step bodies).

An UNKNOWN input table (already reported) makes the op result UNKNOWN without
checking the op's expressions, so one mistake yields one diagnostic.
"""

from __future__ import annotations

import ast
import copy
import json
from dataclasses import dataclass, field, replace

from lattice.catalog import (
    API_AMBIENT,
    API_ERRORS,
    API_OP_SHAPES,
    ELSE_KW,
    EXPR_FUNCS,
    FOR_EACH,
    INSERT_MANY,
    IO_OPS,
    IS_ADMIN,
    ITERATE,
    ITERATE_FIXPOINT,
    ITERATE_MAX,
    NAMESPACES,
    NATIVE_NS,
    OP_SHAPES,
    ORDER_AGGS,
    OWNER_IS,
    PUBLIC_ROLE,
    SORT_NULLS,
    T2_CALL,
    TABLE_OPS,
    TRAVERSE_DEPTH,
    TRAVERSE_DIRECTIONS,
    TRAVERSE_MAX_DEPTH,
    TYPE_NAMES,
    UNIQUE_OR,
    UNSET_NAME,
    USER_PARAM,
    WIN_TIE_SAFE,
    WINDOW_KW,
)
from lattice.check.api_info import (
    Decl,
    ParamSpec,
    endpoint_decorator,
    endpoint_params,
    parse_decl,
    user_table,
)
from lattice.check.astutil import (
    MAX_DEPTH,
    MAX_EXPANDED_NODES,
    LiteralValue,
    annotation_schema_name,
    has_endpoint_decorator,
    is_constant_expr,
    join_on_pairs,
    literal_value,
    redefined_steps,
    step_mentions,
    steps_of,
)
from lattice.check.context import CheckContext
from lattice.check.exprs import (
    NULLS_AGGS,
    ExprTyper,
    constant_scalar,
    did_you_mean,
    reported_name_spans,
)
from lattice.check.nullability import expr_nullable
from lattice.check.t2.types import Opt as T2Opt
from lattice.check.t2.types import RecordT as T2RecordT
from lattice.check.t2.types import RowT as T2RowT
from lattice.check.t2_calls import (
    ScalarValue,
    column_type,
    fits,
    is_proc_call,
    is_row_call,
    whole_call,
)
from lattice.diagnostics import span_of
from lattice.typesys import (
    NUMERIC,
    SCALAR_NAMES,
    UNKNOWN,
    ColT,
    LitT,
    Scalar,
    ScalarParamT,
    Schema,
    StepT,
    TableT,
    UnknownT,
    generated_key,
    identity_columns,
    plain_columns,
    uuid_key,
)

TableOrUnknown = TableT | UnknownT
Env = dict[str, TableOrUnknown]


@dataclass
class _Scope:
    """What statements see: tables, scalar names and named constants."""

    env: Env = field(default_factory=dict)  # tables
    scalars: dict[str, Scalar] = field(default_factory=dict)  # scalar params + typed constants
    consts: dict[str, LiteralValue | None] = field(default_factory=dict)  # constant -> literal
    # constant -> (depth, node count) of its value with every constant substituted
    shapes: dict[str, tuple[int, int]] = field(default_factory=dict)
    # constant -> whether its value can be missing (e.g. fn.to_int("x"))
    nullconsts: dict[str, bool] = field(default_factory=dict)

    def for_step(self) -> _Scope:
        """A step body's scope: the module constants defined so far, no tables."""
        return _Scope(
            {}, dict(self.scalars), dict(self.consts), dict(self.shapes), dict(self.nullconsts)
        )

    def bound(self, name: str) -> bool:
        return name in self.env or name in self.consts


JOIN_HOWS = ("inner", "left")
FANOUT_VALUES = ("allow",)


class _DoubleQuoted(ast.NodeTransformer):
    def visit_Constant(self, node: ast.Constant) -> ast.expr:
        if isinstance(node.value, str):
            return ast.Name(json.dumps(node.value))
        return node


def _render(node: ast.expr) -> str:
    """Source-like text of an expression, with strings double-quoted as in the prompt."""
    return ast.unparse(_DoubleQuoted().visit(copy.deepcopy(node)))


def _col_name(node: ast.expr) -> str | None:
    match node:
        case ast.Call(func=ast.Name(id="col"), args=[ast.Constant(value=str(name))]):
            return name
    return None


def _guaranteed_present(pred: ast.expr) -> frozenset[str]:
    """Columns non-null on every row where `pred` is true: from each `&` conjunct that
    is `~is_null(col(x))` or a comparison with `col(x)` as an operand (a comparison
    with a missing value is null, and where() drops null rows)."""
    match pred:
        case ast.BinOp(left=a, op=ast.BitAnd(), right=b):
            return _guaranteed_present(a) | _guaranteed_present(b)
        case ast.UnaryOp(op=ast.Invert(), operand=ast.Call(func=ast.Name(id="is_null"), args=[a])):
            name = _col_name(a)
            return frozenset({name}) if name is not None else frozenset()
        case ast.Compare(left=a, comparators=rest):
            return frozenset(n for n in map(_col_name, [a, *rest]) if n is not None)
    return frozenset()


def _owner_cols(schema: Schema) -> frozenset[str]:
    """The owner-identifying column of a database table (owner or owner_via's key)."""
    if schema.owner is not None:
        return frozenset({schema.owner})
    if schema.owner_via is not None:
        return frozenset({schema.owner_via[0]})
    return frozenset()


def _q(value: str) -> str:
    return json.dumps(value)


def _listed(values: tuple[str, ...]) -> str:
    return ", ".join(_q(v) for v in values)


def _cols(names: tuple[str, ...] | list[str]) -> str:
    return "(" + ", ".join(names) + ")"


def _on_dict(pairs: list[tuple[str, str]]) -> str:
    """on={"a": "b", ...} as source text."""
    return "on={" + ", ".join(f"{json.dumps(a)}: {json.dumps(b)}" for a, b in pairs) + "}"


def _counterparts(name: str, own: TableT, other: TableT) -> list[str]:
    """Columns of `other` that likely hold the same key as `own`'s column `name` under
    another name: a parent's `id` and a child's `<parent>_id` (either way round), of
    the same type. A `*_id` column `own` has as well is an ordinary shared column."""
    scalar = own.schema.get(name)
    if name == "id":
        return [
            c
            for c, s in other.schema.columns
            if c.endswith("_id") and s is scalar and c not in own.schema
        ]
    if name.endswith("_id") and other.schema.get("id") is scalar:
        return ["id"]
    return []


def _dict_hint(
    name: str, own: TableT, missing: TableT, own_arg: ast.expr, side: str, on: list[str]
) -> str | None:
    """The hint for on=[..., name, ...] where `name` is a column of one table (`own`)
    but not of the other (`missing`, the `side` table): the dict form pairing it with
    the likely differently-named key of `missing`."""
    found = _counterparts(name, own, missing)
    if len(found) > 1:
        # prefer the one named after the table: orders.id <-> items.order_id
        words = [own_arg.id.lower()] if isinstance(own_arg, ast.Name) else []
        if own.source is not None:
            words.append(own.source.lower())
        named = [c for c in found if any(w.startswith(c[: -len("_id")]) for w in words)]
        found = named if len(named) == 1 else found
    if not found:
        return None

    def pairs(partner: str) -> list[tuple[str, str]]:
        # `side` is where `name` is missing: `partner` belongs there
        pair = (partner, name) if side == "left" else (name, partner)
        return [pair if n == name else (n, n) for n in on]

    if len(found) == 1:
        return f"keys have different names? use {_on_dict(pairs(found[0]))}"
    choices = ", ".join(json.dumps(c) for c in found)
    placeholder = f"<{side} column>"
    return f"keys have different names? use {_on_dict(pairs(placeholder))} with one of {choices}"


@dataclass(frozen=True)
class _Sig:
    """A step signature that may still contain UNKNOWN parameter types."""

    params: tuple[tuple[str, TableT | ScalarParamT | UnknownT], ...]
    result: TableOrUnknown


def _str_constant(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dups(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n in seen and n not in out:
            out.append(n)
        seen.add(n)
    return out


class _Types:
    def __init__(self, module: ast.Module, ctx: CheckContext) -> None:
        self.module = module
        self.ctx = ctx
        self.api = ctx.profile.api
        self.schemas = (
            {**ctx.profile.schemas, **self.api.tables}
            if self.api is not None
            else ctx.profile.schemas
        )
        self.step_defs = steps_of(module)
        self.redefined = redefined_steps(module)
        self.silent = TABLE_OPS | frozenset(self.step_defs)
        if self.api is not None:
            self.silent |= API_AMBIENT
        self.reported = reported_name_spans(ctx)
        self.sigs: dict[str, _Sig] = {}
        self.in_endpoint = False  # typing an @endpoint body (API profile)
        self.endpoint_admin = False  # ... of an auth=Admin endpoint
        # T2: the program's procedures, and those whose result may be None
        self.t2 = ctx.t2 if self.api is not None else None
        self.null_calls = frozenset(
            name
            for name, p in (self.t2.procedures.items() if self.t2 is not None else ())
            if isinstance(p.sig.returns, T2Opt)
        )
        # the parameters of the endpoint being typed (their bounds: L433)
        self.param_specs: dict[str, ParamSpec] = {}
        self.natives = self.api.natives if self.api is not None else {}
        # natives whose result may be missing (L421 nullability)
        self.null_natives = frozenset(n for n, sig in self.natives.items() if sig.nullable)
        # natives that may read private columns without revealing them (verifiers)
        self._verifiers = frozenset(n for n, sig in self.natives.items() if sig.reads_private)

    def run(self) -> dict[str, StepT]:
        scope = _Scope()
        for stmt in self.module.body:
            match stmt:
                case ast.Assign(targets=[ast.Name(id=name)]):
                    self._assign(name, stmt.value, scope)
                case ast.Expr():
                    self._unit(stmt.value, scope)
                case ast.FunctionDef() if self.api is not None and has_endpoint_decorator(stmt):
                    self._endpoint(stmt, scope.for_step())
                case ast.FunctionDef():
                    self._step(stmt, scope.for_step())
                case ast.ImportFrom():
                    pass
                case _:
                    raise TypeError(f"P1 should have rejected {type(stmt).__name__}")
        steps: dict[str, StepT] = {}
        for name, sig in self.sigs.items():
            st = self._step_t(sig)
            if st is not None:
                steps[name] = st
        return steps

    # ---- helpers ------------------------------------------------------------

    def _typer(self, schema: Schema, scope: _Scope, t: TableT | None = None) -> ExprTyper:
        """An expression typer over `schema` (the table `t`, whose nullable and private
        columns procedure calls read)."""
        nullconsts = dict(scope.nullconsts)
        for spec in self.param_specs.values() if self.in_endpoint else ():
            if spec.optional:  # not sent: a procedure receives None
                nullconsts[spec.name] = True
        return ExprTyper(
            self.ctx,
            schema,
            scope.scalars,
            self.silent,
            self.reported,
            consts=scope.consts,
            tables=scope.env,
            natives=self.natives,
            caller=self._caller(scope),
            nullable=t.nullable if t is not None else frozenset(),
            nullconsts=nullconsts,
            private=t.private if t is not None else frozenset(),
            admin=self.endpoint_admin,
            endpoint=self.in_endpoint,
        )

    def _caller(self, scope: _Scope) -> Schema | None:
        """The users table when an endpoint body has its caller (`user: CurrentUser`)
        in scope: `user.<column>` references its columns."""
        if self.api is None or not self.in_endpoint:
            return None
        t = scope.env.get(USER_PARAM)
        if not isinstance(t, TableT) or t.source != self.api.users_table:
            return None
        return self.api.tables.get(self.api.users_table)

    def _assign(self, name: str, value: ast.expr, scope: _Scope) -> None:
        """One assignment: a named constant or a unit call. The first binding of a
        name wins (P2 reported any rebinding)."""
        if is_constant_expr(value):
            self._constant(name, value, scope)
            return
        if self.t2 is not None and _call_name(value) == T2_CALL:
            self._whole_call(name, value, scope)
            return
        t = self._unit(value, scope) or UNKNOWN
        if not scope.bound(name):
            scope.env[name] = t
        elif name not in scope.env:
            scope.env[name] = UNKNOWN  # a constant rebound as a table: silence table uses

    def _constant(self, name: str, value: ast.expr, scope: _Scope) -> None:
        shape = self._expanded_ok(value, scope)
        vt = self._typer(Schema(()), scope).type_of(value) if shape is not None else UNKNOWN
        if scope.bound(name):
            # rebinding (P2's L201): the first binding wins; a table rebound as a
            # constant also silences the name's uses as a value
            scope.consts.setdefault(name, None)
            return
        match vt:
            case LitT(scalar=s) if shape is not None:
                scope.scalars[name] = s
                scope.shapes[name] = shape
                scope.nullconsts[name] = expr_nullable(
                    value, frozenset(), scope.nullconsts, self.null_natives
                )
                lit = literal_value(value)
                if lit is None and isinstance(value, ast.Name):
                    lit = scope.consts.get(value.id)  # `b = a` with a bound to a literal
                scope.consts[name] = lit
            case ColT():  # unreachable: a constant expression has no col()
                self.ctx.error("L302", value, "a constant cannot reference a column", None)
                scope.consts[name] = None
            case _:
                scope.consts[name] = None  # already reported; uses are silent

    def _expanded(self, node: ast.AST, scope: _Scope) -> tuple[int, int]:
        """(depth, node count) of `node` once named constants are substituted."""
        if isinstance(node, ast.Name) and node.id in scope.shapes:
            return scope.shapes[node.id]
        depth, size = 0, 1
        for child in ast.iter_child_nodes(node):
            d, n = self._expanded(child, scope)
            depth, size = max(depth, d), size + n
        return depth + 1, size

    def _expanded_ok(self, value: ast.expr, scope: _Scope) -> tuple[int, int] | None:
        """The expansion shape of a statement's value, or None (after one L003) if
        substituting its named constants makes it too deep or too large."""
        if not scope.shapes:
            return self._expanded(value, scope)
        depth, size = self._expanded(value, scope)
        if depth > MAX_DEPTH or size > MAX_EXPANDED_NODES:
            self.ctx.error(
                "L003",
                value,
                "expression is too large once its named constants are substituted",
                "compute the value in fewer, simpler constants",
            )
            return None
        return depth, size

    def _silenced(self, node: ast.AST) -> bool:
        return span_of(node) in self.reported

    @staticmethod
    def _step_t(sig: _Sig) -> StepT | None:
        params: list[tuple[str, TableT | ScalarParamT]] = []
        for name, t in sig.params:
            if isinstance(t, UnknownT):
                return None
            params.append((name, t))
        return StepT(tuple(params), sig.result)

    def _non_table_kind(self, name: str, scope: _Scope) -> tuple[str, str] | None:
        """(kind, hint) if `name` is positively known to be something other than a table."""
        if name in scope.consts:
            if name not in scope.scalars:
                return None  # an ill-typed constant: its statement was already reported
            return "constant", "pass a table name here; constants are scalar values"
        if name in scope.scalars:
            return "scalar parameter", "pass a table name here"
        if self.api is not None and name in self.api.tables:
            return "database table", f"fetch it first: t = fetch({name})"
        if name in self.schemas:
            return "schema", f'load it first: t = load("file.csv", schema={name})'
        if name in self.step_defs:
            return "step", f"call it first: t = {name}(...), then pass t"
        if name in TYPE_NAMES:
            return "type", "pass a table name here"
        if name in TABLE_OPS:
            return "op", f"call it first: t = {name}(...), then pass t"
        if name in NAMESPACES or (self.api is not None and name == NATIVE_NS):
            return "namespace", "pass a table name here"
        if name in EXPR_FUNCS or name == "step" or (self.api is not None and name == OWNER_IS):
            return "function", "pass a table name here"
        if self.api is not None and name == UNSET_NAME:
            return None  # P6 reports UNSET outside a parameter default (L404)
        return None  # otherwise unresolved: P2 reported it (L202/L203/L603/L604)

    def _table_arg(self, arg: ast.expr, scope: _Scope) -> TableOrUnknown:
        if not isinstance(arg, ast.Name):
            return UNKNOWN  # not a name: P3 reported it
        t = scope.env.get(arg.id)
        if t is not None:
            return t
        kind = self._non_table_kind(arg.id, scope)
        if kind is not None and not self._silenced(arg):
            self.ctx.error("L419", arg, f"expected a table, got {kind[0]} '{arg.id}'", kind[1])
        return UNKNOWN

    def _str_list(self, node: ast.expr, what: str) -> list[str] | None:
        if isinstance(node, ast.List | ast.Tuple):
            names = [_str_constant(e) for e in node.elts]
            if all(n is not None for n in names):
                return [n for n in names if n is not None]
        self.ctx.error(
            "L404",
            node,
            f"'{what}' must be a list of column names as strings",
            f'e.g. {what}=["region"]',
        )
        return None

    def _require_columns(
        self, names: list[str], schema: Schema, node: ast.AST, side: str = ""
    ) -> bool:
        ok = True
        for n in names:
            if n not in schema:
                ok = False
                where = f" in the {side} table" if side else ""
                self.ctx.error(
                    "L401",
                    node,
                    f"unknown column '{n}'{where}; table has {{{', '.join(schema.names())}}}",
                    did_you_mean(n, schema.names()),
                )
        return ok

    def _no_dups(self, names: list[str], node: ast.AST) -> bool:
        dups = _dups(names)
        for d in dups:
            self.ctx.error("L415", node, f"column '{d}' is listed twice", "list each column once")
        return not dups

    # ---- statements ---------------------------------------------------------

    def _unit(self, value: ast.expr, scope: _Scope) -> TableOrUnknown | None:
        """Type one statement's unit call; None for write (no result)."""
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)):
            return UNKNOWN  # P3 already reported the non-unit statement
        if self._expanded_ok(value, scope) is None:
            self.ctx.set_type(value, UNKNOWN)
            return UNKNOWN
        name = value.func.id
        if name == ITERATE:
            if self.api is not None:  # P5's L437
                self.ctx.set_type(value, UNKNOWN)
                return UNKNOWN
            t = self._iterate(value, scope)
            self.ctx.set_type(value, t)
            return t
        if self.t2 is not None and name == INSERT_MANY:
            t = self._insert_many(value, scope)
            self.ctx.set_type(value, t)
            return t
        if self.api is not None:
            # API ops outside an endpoint (P5's L503) and load/write inside one (P6's
            # L841) are reported elsewhere
            if name in API_OP_SHAPES and self.in_endpoint:
                return self._api_op(value, name, scope)
            if name in API_OP_SHAPES or (name in IO_OPS and self.in_endpoint):
                self.ctx.set_type(value, UNKNOWN)
                return None if name == "write" else UNKNOWN
        if name in OP_SHAPES:
            return self._op(value, name, scope)
        if name in self.step_defs:
            t = self._call_step(value, name, scope)
            self.ctx.set_type(value, t)
            return t
        return UNKNOWN

    def _op(self, call: ast.Call, op: str, scope: _Scope) -> TableOrUnknown | None:
        shape = OP_SHAPES[op]
        kws = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        shape_ok = len(call.args) == len(shape.positional) and shape.required_kw <= kws.keys()
        if op == "write":
            if shape_ok:
                self._table_arg(call.args[0], scope)
                self._write(call)
            return None
        result: TableOrUnknown = UNKNOWN
        if shape_ok:  # otherwise P3 reported the call shape
            inputs = [self._table_arg(a, scope) for a in call.args[: shape.tables]]
            tables = [t for t in inputs if isinstance(t, TableT)]
            if len(tables) == len(inputs):
                if self.t2 is not None and tables:
                    self._t2_rows_rules(call, op, tables)
                result = self._transfer(call, op, tables, kws, scope)
                if self.api is not None and isinstance(result, TableT) and tables:
                    result = self._api_meta(call, op, tables, result)
                if self.t2 is not None and isinstance(result, TableT) and tables:
                    result = replace(result, t2_rows=self._t2_rows_of(call, op, tables))
        self.ctx.set_type(call, result)
        return result

    def _transfer(
        self,
        call: ast.Call,
        op: str,
        tables: list[TableT],
        kws: dict[str, ast.expr],
        scope: _Scope,
    ) -> TableOrUnknown:
        match op:
            case "load":
                return self._load(call, kws["schema"])
            case "where":
                return self._where(call, tables[0], scope)
            case "derive":
                return self._derive(call, tables[0], scope)
            case "select":
                return self._select(tables[0], kws["cols"])
            case "rename":
                return self._rename(call, tables[0])
            case "group":
                return self._group(call, tables[0], kws["by"], scope)
            case "join":
                return self._join(call, tables[0], tables[1], kws)
            case "sort":
                return self._sort(tables[0], kws, scope)
            case "limit":
                return self._limit(tables[0], kws["n"])
            case "window":
                return self._window(call, tables[0], kws, scope)
            case "traverse":
                return self._traverse(call, tables[0], tables[1], kws, scope)
            case _:
                raise TypeError(f"no transfer function for op '{op}'")

    # ---- transfer functions ---------------------------------------------------

    def _load(self, call: ast.Call, schema_node: ast.expr) -> TableOrUnknown:
        if _str_constant(call.args[0]) is None:
            self.ctx.error(
                "L404", call.args[0], "load() path must be a string literal", 'e.g. "orders.csv"'
            )
        available = ", ".join(sorted(self.schemas)) or "none"
        match schema_node:
            case ast.Name(id=name) if name in self.schemas:
                schema = self.schemas[name]
                return TableT(schema, keys=schema.keys, nullable=schema.nullable)
            case ast.Name() if self._silenced(schema_node):
                return UNKNOWN
            case ast.Name(id=name):
                self.ctx.error(
                    "L414", schema_node, f"unknown schema '{name}'", f"available: {available}"
                )
            case ast.Constant(value=str(name)):
                self.ctx.error(
                    "L414",
                    schema_node,
                    "schema= takes a schema name, not a string",
                    f"write schema={name}",
                )
            case _:
                self.ctx.error(
                    "L414", schema_node, "schema= must name a schema", f"available: {available}"
                )
        return UNKNOWN

    def _where(self, call: ast.Call, t: TableT, scope: _Scope) -> TableOrUnknown:
        pred = call.args[1]
        pt = self._typer(t.schema, scope, t).type_whole(pred)
        match pt:
            case ColT(scalar=Scalar.BOOL) | UnknownT():
                pass
            case LitT() if isinstance(pred, ast.Constant):
                pass  # P3 reported the constant predicate
            case LitT(scalar=Scalar.BOOL) if mentions_caller(pred):
                pass  # a condition on the caller (e.g. is_admin(user)): per request
            case LitT():
                self.ctx.error(
                    "L304",
                    pred,
                    "predicate does not reference any column",
                    'compare a column, e.g. col("qty") > n',
                )
            case ColT(scalar=s):
                self.ctx.error(
                    "L403",
                    pred,
                    f"where() predicate must be Bool, got {s.value}",
                    'compare it, e.g. col("x") > 0',
                )
        # the predicate never changes the schema; rows where it is null are dropped,
        # so a column it guarantees present is no longer nullable
        present = _guaranteed_present(pred) & t.nullable
        return replace(t, nullable=t.nullable - present) if present else t

    def _derive(self, call: ast.Call, t: TableT, scope: _Scope) -> TableOrUnknown:
        # Every expression reads the INPUT table: a later keyword does not see an earlier
        # keyword's new value. An existing name is replaced in place (with the new type).
        typer = self._typer(t.schema, scope, t)
        cols: list[tuple[str, Scalar]] = []
        nullable = set(t.nullable)
        unknown = False
        for kw in call.keywords:
            assert kw.arg is not None  # P1 rejects **kwargs
            vt = typer.type_whole(kw.value)
            if isinstance(vt, UnknownT):
                unknown = True
            else:
                cols.append((kw.arg, vt.scalar))
                if expr_nullable(
                    kw.value, t.nullable, scope.nullconsts, self.null_natives, self.null_calls
                ):
                    nullable.add(kw.arg)
                else:
                    nullable.discard(kw.arg)
        if unknown:
            return UNKNOWN
        overwritten = {n for n, _ in cols if n in t.schema}
        keys = tuple(k for k in t.keys if not overwritten.intersection(k))
        return TableT(t.schema.with_columns(cols), t.at_most_one_row, keys, frozenset(nullable))

    def _select(self, t: TableT, cols_node: ast.expr) -> TableOrUnknown:
        names = self._str_list(cols_node, "cols")
        if names is None or not self._require_columns(names, t.schema, cols_node):
            return UNKNOWN
        self._no_dups(names, cols_node)
        kept = set(names)
        return TableT(
            t.schema.project(dict.fromkeys(names)),
            t.at_most_one_row,
            tuple(k for k in t.keys if kept.issuperset(k)),
            t.nullable & kept,
        )

    def _rename(self, call: ast.Call, t: TableT) -> TableOrUnknown:
        mapping: dict[str, str] = {}
        ok = True
        for kw in call.keywords:
            assert kw.arg is not None
            new = _str_constant(kw.value)
            if not self._require_columns([kw.arg], t.schema, kw):
                ok = False
            if new is None:
                ok = False
                self.ctx.error(
                    "L404", kw.value, "rename() new names must be string literals", 'e.g. a="b"'
                )
            if new is not None and kw.arg in t.schema:
                mapping[kw.arg] = new
        kept = {n for n in t.schema.names() if n not in mapping}
        taken: set[str] = set()
        for kw in call.keywords:
            new = mapping.get(kw.arg or "")
            if new is None:
                continue
            if new in taken or new in kept:
                ok = False
                self.ctx.error(
                    "L405",
                    kw,
                    f"column '{new}' already exists",
                    "choose a new name or rename first",
                )
            taken.add(new)
        if not ok:
            return UNKNOWN
        return TableT(
            t.schema.renamed(mapping),
            t.at_most_one_row,
            tuple(tuple(mapping.get(c, c) for c in k) for k in t.keys),
            frozenset(mapping.get(c, c) for c in t.nullable),
        )

    def _group(self, call: ast.Call, t: TableT, by_node: ast.expr, scope: _Scope) -> TableOrUnknown:
        by = self._str_list(by_node, "by")
        ok = by is not None and self._require_columns(by, t.schema, by_node)
        ok = ok and self._no_dups(by or [], by_node)
        typer = self._typer(t.schema, scope)
        aggs: list[tuple[str, Scalar]] = []
        null_aggs: set[str] = set()
        for kw in call.keywords:
            if kw.arg == "by" or kw.arg is None:
                continue
            at = typer.type_agg(kw.value)
            if at is not None and self._agg_nullable(kw.value, t, scope, whole_table=not by):
                null_aggs.add(kw.arg)
            if by is not None and kw.arg in by:
                ok = False
                self.ctx.error(
                    "L405",
                    kw,
                    f"column '{kw.arg}' already exists as a group key",
                    "choose a new name for the aggregate",
                )
            elif at is None:
                ok = False
            else:
                aggs.append((kw.arg, at))
        if not ok or by is None:
            return UNKNOWN
        # by=[] yields exactly one row (a whole-table aggregate); the group keys are
        # unique in the result (the empty key for by=[])
        return TableT(
            t.schema.project(by).extend(aggs),
            at_most_one_row=not by,
            keys=(tuple(by),),
            nullable=(t.nullable & set(by)) | null_aggs,
        )

    def _agg_nullable(self, node: ast.expr, t: TableT, scope: _Scope, *, whole_table: bool) -> bool:
        """Whether a (well-typed) aggregate's result can be missing; reports L421 when
        mean/min/max reads a nullable expression without a null policy."""
        assert isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        fn = node.func.attr
        if fn in ORDER_AGGS:
            # the picked row's value may be missing; every order value may be missing;
            # a whole-table aggregate over an empty input has no row to pick
            return whole_table or any(
                expr_nullable(e, t.nullable, scope.nullconsts, self.null_natives)
                for e in [node.args[0], node.keywords[0].value]
            )
        if fn not in NULLS_AGGS:
            return False  # sum/count are never missing
        arg = node.args[0]
        if (
            not node.keywords
            and self.ctx.profile.enforce_nullable
            and expr_nullable(arg, t.nullable, scope.nullconsts, self.null_natives)
        ):
            text = _render(arg)
            match arg:
                case ast.Call(func=ast.Name(id="col"), args=[ast.Constant(value=str(c))]):
                    what = c
                case _:
                    what = text
            at = self.ctx.types.get(id(arg))
            numeric = isinstance(at, ColT | LitT) and at.scalar in NUMERIC
            fill = (
                f"coalesce({text}, 0.0)) to count missing as 0"
                if numeric
                else f"coalesce({text}, <value>)) to use a value for missing ones"
            )
            self.ctx.error(
                "L421",
                node,
                f"aggregating '{what}' which can be missing: say how to treat missing values",
                f'use agg.{fn}({fill}, or agg.{fn}({text}, nulls="skip") to ignore missing values',
            )
        return True  # an all-missing group (or empty input) gives a missing result

    def _join(
        self, call: ast.Call, left: TableT, right: TableT, kws: dict[str, ast.expr]
    ) -> TableOrUnknown:
        how = kws.get("how")
        if how is not None and _str_constant(how) not in JOIN_HOWS:
            self.ctx.error("L404", how, 'join how= must be "inner" or "left"', 'e.g. how="left"')
        fanout = kws.get("fanout")
        fanout_ok = fanout is None or _str_constant(fanout) in FANOUT_VALUES
        if not fanout_ok:
            assert fanout is not None
            self.ctx.error(
                "L404",
                fanout,
                'join fanout= must be "allow"',
                'pass fanout="allow" only if repeating left rows is intended',
            )
        on_node = kws["on"]
        pairs = self._join_pairs(on_node)
        if pairs is None:
            return UNKNOWN
        if not pairs:
            return self._cross_join(call, left, right, on_node)
        left_on = [a for a, _ in pairs]
        right_on = [b for _, b in pairs]
        mapped = isinstance(on_node, ast.Dict)
        if mapped:
            ok = self._mapped_columns(pairs, left, right, on_node)
            ok = self._no_dups(left_on, on_node) and ok
            ok = self._no_dups(right_on, on_node) and ok
        else:
            ok = self._join_columns(call, left_on, left, right, on_node)
            ok = self._no_dups(left_on, on_node) and ok
        if not ok:
            return UNKNOWN
        for a, b in pairs:
            ta, tb = left.schema.get(a), right.schema.get(b)
            if ta is not tb:
                ok = False
                lt, rt = ta.value if ta else "?", tb.value if tb else "?"
                message = (
                    f"join key '{a}' is {lt} on the left and '{b}' is {rt} on the right"
                    if mapped
                    else f"join key '{a}' is {lt} on the left and {rt} on the right"
                )
                self.ctx.error(
                    "L402", on_node, message, "convert one side with derive + fn.to_* first"
                )
        extra = [(n, s) for n, s in right.schema.columns if n not in right_on]
        clashes = [n for n, _ in extra if n in left.schema]
        if clashes:
            ok = False
            listed = ", ".join(f"'{n}'" for n in clashes)
            what = f"column {listed} exists" if len(clashes) == 1 else f"columns {listed} exist"
            self.ctx.error(
                "L416",
                call,
                f"{what} in both tables; rename one side first",
                f'e.g. rename(table, {clashes[0]}="{clashes[0]}_2")',
            )
        if not ok:
            return UNKNOWN
        unique = any(set(k) <= set(right_on) for k in right.keys)
        if not unique and fanout is None and self.ctx.profile.enforce_keys:
            r_name = call.args[1].id if isinstance(call.args[1], ast.Name) else "right"
            known = ", ".join(_cols(k) for k in right.keys) or "none"
            self.ctx.error(
                "L420",
                call,
                f"join can repeat rows of the left table: '{r_name}' is not unique on "
                f"{_cols(right_on)}; known keys of it: {known}",
                f"reduce it to one row per {_cols(right_on)} first, e.g. group({r_name}, "
                'by=[...], ...), or pass fanout="allow" if repeating rows is intended',
            )
            return UNKNOWN
        extra_names = {n for n, _ in extra}
        how_s = _str_constant(how) if how is not None else "inner"
        right_null = extra_names if how_s == "left" else right.nullable & extra_names
        return TableT(
            left.schema.extend(extra),
            keys=left.keys if unique else (),
            nullable=left.nullable | right_null,
        )

    def _join_pairs(self, node: ast.expr) -> tuple[tuple[str, str], ...] | None:
        """join on=: a list of column names (the same name on both sides) or a dict
        literal mapping a left column name to a right column name."""
        pairs = join_on_pairs(node)
        if pairs is not None:
            return pairs
        if isinstance(node, ast.Dict):
            self.ctx.error(
                "L404",
                node,
                "join on= must map left column names to right column names, as strings",
                'e.g. on={"id": "order_id"}',
            )
            return None
        self.ctx.error(
            "L404",
            node,
            "'on' must be a list of column names as strings",
            'e.g. on=["region"], or on={"id": "order_id"} when the key names differ',
        )
        return None

    def _mapped_columns(
        self,
        pairs: tuple[tuple[str, str], ...],
        left: TableT,
        right: TableT,
        node: ast.expr,
    ) -> bool:
        """on={left: right}: every left name in the left table, every right name in the
        right table. When the dict reads the wrong way round, the hint swaps it."""
        swapped = all(b in left.schema and a in right.schema for a, b in pairs)
        swap_hint = (
            "the dict maps a left column to a right column: use "
            + _on_dict([(b, a) for a, b in pairs])
            if swapped
            else None
        )
        ok = True
        for side, names, t in (
            ("left", [a for a, _ in pairs], left),
            ("right", [b for _, b in pairs], right),
        ):
            for n in names:
                if n not in t.schema:
                    ok = False
                    self.ctx.error(
                        "L401",
                        node,
                        f"unknown column '{n}' in the {side} table; "
                        f"table has {{{', '.join(t.schema.names())}}}",
                        swap_hint or did_you_mean(n, t.schema.names()),
                    )
        return ok

    def _join_columns(
        self, call: ast.Call, on: list[str], left: TableT, right: TableT, node: ast.expr
    ) -> bool:
        """on=[...]: every name in both tables. A key missing on one side whose partner
        on the other side has a different name (orders.id = items.order_id) gets a hint
        naming the dict form."""
        ls, rs = left.schema, right.schema
        only_left = [n for n in on if n in ls and n not in rs]
        only_right = [n for n in on if n in rs and n not in ls]
        neither = [n for n in on if n not in ls and n not in rs]
        if (
            only_left
            and not neither
            and len(only_left) == len(only_right)
            and all(ls.get(a) is rs.get(b) for a, b in zip(only_left, only_right, strict=True))
        ):
            # on=["id", "order_id"]: one pair of names, SQL-style, split over the sides
            partner = dict(zip(only_left, only_right, strict=True))
            pairs = [(n, partner[n]) for n in on if n not in only_right]
            listed = ", ".join(
                [f"'{n}' (only in the left table)" for n in only_left]
                + [f"'{n}' (only in the right table)" for n in only_right]
            )
            self.ctx.error(
                "L401",
                node,
                f"join on= lists {listed}; a list names key columns that both tables have",
                f"keys have different names? use {_on_dict(pairs)}",
            )
            return False
        ok = True
        for side, t, other in (("left", left, right), ("right", right, left)):
            for n in on:
                if n in t.schema:
                    continue
                ok = False
                hint = did_you_mean(n, t.schema.names())
                if n in other.schema:
                    own_arg = call.args[1 if side == "left" else 0]
                    found = _dict_hint(n, other, t, own_arg, side, on)
                    hint = found or hint
                self.ctx.error(
                    "L401",
                    node,
                    f"unknown column '{n}' in the {side} table; "
                    f"table has {{{', '.join(t.schema.names())}}}",
                    hint,
                )
        return ok

    def _cross_join(
        self, call: ast.Call, left: TableT, right: TableT, on_node: ast.expr
    ) -> TableOrUnknown:
        """join(left, right, on=[]): attach a right table of at most one row to every
        left row; the result has at most as many rows as `left`."""
        if not right.at_most_one_row:
            self.ctx.error(
                "L404",
                on_node,
                "join with on=[] needs a right table with at most one row, "
                "e.g. a group(t, by=[], ...) total",
                'list the key columns, e.g. on=["id"]',
            )
            return UNKNOWN
        clashes = [n for n in right.schema.names() if n in left.schema]
        if clashes:
            listed = ", ".join(f"'{n}'" for n in clashes)
            what = f"column {listed} exists" if len(clashes) == 1 else f"columns {listed} exist"
            self.ctx.error(
                "L416",
                call,
                f"{what} in both tables; rename one side first",
                f'e.g. rename(table, {clashes[0]}="{clashes[0]}_2")',
            )
            return UNKNOWN
        how = next((kw.value for kw in call.keywords if kw.arg == "how"), None)
        left_join = _str_constant(how) == "left" if how is not None else False
        right_null = frozenset(right.schema.names()) if left_join else right.nullable
        return TableT(
            left.schema.extend(right.schema.columns),
            left.at_most_one_row,
            left.keys,
            left.nullable | right_null,
        )

    def _sort(self, t: TableT, kws: dict[str, ast.expr], scope: _Scope) -> TableOrUnknown:
        by = self._str_list(kws["by"], "by")
        if by is not None:
            self._require_columns(by, t.schema, kws["by"])
        desc = kws.get("desc")
        if desc is not None and not _valid_sort_desc(desc, by, literal_only=self.api is None):
            self.ctx.error(
                "L404",
                desc,
                "sort desc= must be True, False, or a list with one True/False per by= column",
                'e.g. desc=True, or by=["revenue", "id"], desc=[True, False] for revenue '
                "descending with ties by id ascending",
            )
        elif desc is not None and self.api is not None:
            entries = desc.elts if isinstance(desc, ast.List | ast.Tuple) else [desc]
            for entry in entries:
                self._sort_direction(entry, t, scope)
        nulls = kws.get("nulls")
        if nulls is not None and literal_value(nulls) not in SORT_NULLS:
            listed = ", ".join(f'"{n}"' for n in SORT_NULLS)
            self.ctx.error(
                "L404",
                nulls,
                f"sort nulls= must be one of {listed} (a literal)",
                'e.g. nulls="smallest": nulls first ascending, last descending',
            )
        return t

    def _sort_direction(self, entry: ast.expr, t: TableT, scope: _Scope) -> None:
        """One desc= entry of an API sort: a Bool literal, or a Bool computed from
        parameters and literals only (one direction for the whole request)."""
        if isinstance(entry, ast.Constant) and isinstance(entry.value, bool):
            return
        match self._typer(t.schema, scope).type_of(entry):
            case LitT(scalar=Scalar.BOOL) | UnknownT():
                pass
            case ColT():
                self.ctx.error(
                    "L404",
                    entry,
                    "a sort direction applies to the whole request: compute it from "
                    "parameters and literals, with no columns",
                    'e.g. desc=[order == "newest"] with order: OneOf("newest", "oldest")',
                )
            case LitT(scalar=s):
                self.ctx.error(
                    "L404",
                    entry,
                    f"a sort direction must be Bool, got {s.value}",
                    'compare it, e.g. desc=[order == "desc"]',
                )

    def _limit(self, t: TableT, n: ast.expr) -> TableOrUnknown:
        value = n.value if isinstance(n, ast.Constant) else None
        if not (isinstance(value, int) and not isinstance(value, bool) and value > 0):
            self.ctx.error("L417", n, "limit n= must be a positive integer literal", "e.g. n=10")
        if value == 1 and not isinstance(value, bool):
            return replace(t, at_most_one_row=True)
        return t

    def _window(
        self, call: ast.Call, t: TableT, kws: dict[str, ast.expr], scope: _Scope
    ) -> TableOrUnknown:
        """window(t, by=[...], order=[...], desc=[...], name=win.<f>(...), ...): t's rows
        in t's order, plus one column per window function, computed over the rows of
        each `by` partition sorted by `order` (nulls last; `desc` names the descending
        order columns)."""
        ok = True
        lists: dict[str, list[str]] = {}
        for key in ("by", "order"):
            node = kws.get(key)
            if node is None:
                lists[key] = []
                continue
            names = self._str_list(node, key)
            if names is None:
                ok = False
                continue
            ok = self._require_columns(names, t.schema, node) and ok
            ok = self._no_dups(names, node) and ok
            lists[key] = names
        by, order = lists.get("by", []), lists.get("order", [])
        desc = kws.get("desc")
        if desc is not None and "order" in lists:
            names = _str_list_of(desc) if isinstance(desc, ast.List | ast.Tuple) else None
            if (
                names is None
                or len(names) != len(desc.elts)  # type: ignore[attr-defined]
                or not set(names) <= set(order)
            ):
                ok = False
                self.ctx.error(
                    "L404",
                    desc,
                    "window desc= must list the order= columns sorted descending",
                    'e.g. order=["created_at", "id"], desc=["created_at"]',
                )
        typer = self._typer(t.schema, scope)
        new: list[tuple[str, Scalar]] = []
        nullable = set(t.nullable)
        funcs: list[str] = []
        for kw in call.keywords:
            if kw.arg is None or kw.arg in WINDOW_KW:
                continue
            typed = typer.type_win(kw.value)
            if kw.arg in t.schema:
                ok = False
                self.ctx.error(
                    "L405",
                    kw,
                    f"column '{kw.arg}' already exists",
                    "choose a new name for the window column",
                )
            if typed is None:
                ok = False
                continue
            func, scalar = typed
            funcs.append(func)
            new.append((kw.arg, scalar))
            if self._win_nullable(kw.value, func, t, scope):
                nullable.add(kw.arg)
        if funcs and "order" in lists and not order:
            ok = False
            self.ctx.error(
                "L430",
                call,
                "window functions need an order: which row comes before which",
                'add order=[...] with the columns that order the rows, e.g. order=["day", "id"]',
            )
        elif ok and order and any(f not in WIN_TIE_SAFE for f in funcs):
            self._window_ties(call, t, by, order, [f for f in funcs if f not in WIN_TIE_SAFE])
        if not ok:
            return UNKNOWN
        return TableT(t.schema.extend(new), t.at_most_one_row, t.keys, frozenset(nullable))

    def _window_ties(
        self, call: ast.Call, t: TableT, by: list[str], order: list[str], funcs: list[str]
    ) -> None:
        """L431: tie-sensitive window functions need `by + order` to identify a row."""
        cols = set(by) | set(order)
        if any(set(k) <= cols for k in t.keys):
            return
        names = list(dict.fromkeys(funcs))
        shown = ", ".join(f"win.{f}" for f in names)
        verb = "depends" if len(names) == 1 else "depend"
        key = next(iter(t.keys), None)
        if key is not None:
            extra = [c for c in key if c not in cols]
            listed = ", ".join(json.dumps(c) for c in [*order, *extra])
            hint = f"add a key column to order=, e.g. order=[{listed}]"
        else:
            hint = "add a key column (columns that identify a row) to order="
        message = (
            f"by + order {_cols(by + order)} may have ties, so {shown} {verb} on how tied "
            "rows are ordered"
        )
        if self.api is not None:
            self.ctx.error("L431", call, message + " (not deterministic in SQL)", hint)
        else:
            self.ctx.warn("L431", call, message + " (here: their input order)", hint)

    def _win_nullable(self, node: ast.expr, func: str, t: TableT, scope: _Scope) -> bool:
        """Whether a window column may be missing: lag/lead past the partition edge;
        cum_min/cum_max/mean of a nullable argument (until/unless a value is seen)."""
        if func in ("lag", "lead"):
            return True
        if func in ("cum_min", "cum_max", "mean"):
            assert isinstance(node, ast.Call)
            return expr_nullable(node.args[0], t.nullable, scope.nullconsts, self.null_natives)
        return False

    def _traverse(
        self,
        call: ast.Call,
        edges: TableT,
        start: TableT,
        kws: dict[str, ast.expr],
        scope: _Scope,
    ) -> TableOrUnknown:
        """traverse(edges, start, parent=, child=, max_depth=N, direction="down"): the
        rows of `edges` reachable from the `child` values of `start` (depth 0) by
        following parent -> child links (child -> parent for "up") at most N times,
        each node once at its minimum depth, plus a `depth` column."""
        ok = True
        names: dict[str, str] = {}
        for key in ("parent", "child"):
            name = _str_constant(kws[key])
            if name is None:
                ok = False
                self.ctx.error(
                    "L404",
                    kws[key],
                    f"traverse {key}= must be a column name as a string",
                    f'e.g. {key}="{"parent_id" if key == "parent" else "id"}"',
                )
            else:
                names[key] = name
        direction = kws.get("direction")
        if direction is not None and _str_constant(direction) not in TRAVERSE_DIRECTIONS:
            ok = False
            self.ctx.error(
                "L404",
                direction,
                'traverse direction= must be "down" (towards children) or "up" '
                "(towards ancestors)",
                'e.g. direction="up"',
            )
        ok = self._max_depth(kws["max_depth"], scope) and ok
        if not ok or len(names) != 2:
            return UNKNOWN
        parent, child = names["parent"], names["child"]
        if not self._require_columns([parent, child], edges.schema, call):
            return UNKNOWN
        pt, ct = edges.schema.get(parent), edges.schema.get(child)
        assert pt is not None and ct is not None
        if pt is not ct:
            self.ctx.error(
                "L434",
                call,
                f"traverse links parent '{parent}' ({pt.value}) to child '{child}' "
                f"({ct.value}); they must have the same type",
                "name the column holding the parent's key as parent= and the key as child=",
            )
            return UNKNOWN
        if (child,) not in edges.keys:
            known = ", ".join(_cols(k) for k in edges.keys) or "none"
            self.ctx.error(
                "L434",
                call,
                f"traverse child '{child}' must be a key of the edges table (one row per "
                f"node); known keys: {known}",
                "walk a table whose child column is its key, e.g. child=\"id\"",
            )
            return UNKNOWN
        st = start.schema.get(child)
        if st is not ct:
            has = f"has it as {st.value}" if st is not None else "lacks it"
            self.ctx.error(
                "L434",
                call.args[1],
                f"the start table must have the child column '{child}' ({ct.value}); it {has}",
                "start from rows of the edges table, e.g. root = where(edges, col(...) == ...)",
            )
            return UNKNOWN
        if TRAVERSE_DEPTH in edges.schema:
            self.ctx.error(
                "L405",
                call,
                f"column '{TRAVERSE_DEPTH}' already exists; traverse adds it",
                f'rename it first, e.g. rename(t, {TRAVERSE_DEPTH}="level")',
            )
            return UNKNOWN
        return TableT(
            edges.schema.extend([(TRAVERSE_DEPTH, Scalar.INT)]),
            keys=edges.keys,
            nullable=edges.nullable,
        )

    def _max_depth(self, node: ast.expr, scope: _Scope) -> bool:
        """L433: max_depth= is an Int literal (or a constant bound to one) between 1 and
        TRAVERSE_MAX_DEPTH, or an endpoint Int parameter bounded within that range."""
        hint = f"e.g. max_depth=10, or a parameter depth: Int(ge=1, le={TRAVERSE_MAX_DEPTH})"
        what = "max_depth"
        if isinstance(node, ast.Name) and self.in_endpoint and node.id in self.param_specs:
            spec = self.param_specs[node.id]
            if (
                spec.scalar is Scalar.INT
                and not spec.optional
                and spec.ge is not None
                and spec.ge >= 1
                and spec.le is not None
                and spec.le <= TRAVERSE_MAX_DEPTH
            ):
                self.ctx.set_type(node, LitT(Scalar.INT))
                return True
            what = f"parameter '{node.id}'"
        else:
            v = literal_value(node)
            if v is None and isinstance(node, ast.Name):
                if node.id in scope.consts and node.id not in scope.scalars:
                    return False  # an ill-typed constant: already reported
                v = scope.consts.get(node.id)
                if node.id in scope.scalars and node.id not in scope.consts:
                    what = f"parameter '{node.id}'"
                elif v is None and self._silenced(node):
                    return False
            if (
                isinstance(v, int)
                and not isinstance(v, bool)
                and 1 <= v <= TRAVERSE_MAX_DEPTH
            ):
                self.ctx.set_type(node, LitT(Scalar.INT))
                return True
        self.ctx.error(
            "L433",
            node,
            f"traverse {what} must be an Int between 1 and {TRAVERSE_MAX_DEPTH} "
            "known before the walk starts",
            hint,
        )
        return False

    # ---- loop constructs ------------------------------------------------------

    def _iterate(self, call: ast.Call, scope: _Scope) -> TableOrUnknown:
        """iterate(step, t, until="fixpoint" | <Col[Bool]>, max=N): `step` applied to
        `t` round after round (at most N rounds) until a round returns its own input
        (as a multiset of rows) or every row satisfies the predicate. The step is a
        `Table[S] -> Table[S]` (L435); the result has the schema S."""
        kws = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        if len(call.args) != 2 or not {"until", "max"} <= kws.keys():
            return UNKNOWN  # P3 reported the call shape
        step_node, table_node = call.args
        ok = self._iterate_max(kws["max"], scope)
        schema = self._iterate_step(step_node)
        t = self._table_arg(table_node, scope)
        if schema is None or not isinstance(t, TableT):
            return UNKNOWN
        assert isinstance(step_node, ast.Name)
        if t.schema != schema.schema:
            self.ctx.error(
                "L418",
                table_node,
                f"step '{step_node.id}' expects {schema.schema.render()}, "
                f"got {t.schema.render()}",
                "derive/select/rename the table to match first",
            )
            return UNKNOWN
        ok = self._iterate_until(kws["until"], t, scope) and ok
        if not ok:
            return UNKNOWN
        # the result is the input (a predicate already true) or a step output: only
        # what both guarantee
        keys = tuple(k for k in schema.keys if k in t.keys)
        return TableT(t.schema, keys=keys, nullable=t.nullable | schema.nullable)

    def _iterate_step(self, node: ast.expr) -> TableT | None:
        """The result type of iterate's step, when it is a `Table[S] -> Table[S]` step
        (else L435, or None silently when the step itself was reported)."""
        hint = (
            "define '@step def settle(t: Table[S]) -> Table[S]: ...' (one table "
            "parameter, the same schema out) and pass its name"
        )
        if not (isinstance(node, ast.Name) and node.id in self.step_defs):
            if isinstance(node, ast.Name) and self._silenced(node):
                return None
            self.ctx.error("L435", node, "iterate's first argument must name a @step", hint)
            return None
        sig = self.sigs.get(node.id)
        if sig is None or node.id in self.redefined:
            return None  # defined later / redefined: P2 reported it
        tables = [pt for _, pt in sig.params if not isinstance(pt, ScalarParamT)]
        if any(isinstance(pt, UnknownT) for _, pt in sig.params) or not isinstance(
            sig.result, TableT
        ):
            return None  # the step's own signature or body was reported
        if len(sig.params) != 1 or len(tables) != 1:
            self.ctx.error(
                "L435",
                node,
                f"iterate's step '{node.id}' must take exactly one table parameter (it takes "
                f"{len(tables)} table(s) and {len(sig.params) - len(tables)} scalar(s))",
                hint,
            )
            return None
        param = tables[0]
        assert isinstance(param, TableT)
        if sig.result.schema != param.schema:
            self.ctx.error(
                "L435",
                node,
                f"iterate's step '{node.id}' maps {param.schema.render()} to "
                f"{sig.result.schema.render()}; a round must keep the schema",
                hint,
            )
            return None
        return sig.result

    def _iterate_max(self, node: ast.expr, scope: _Scope) -> bool:
        """L436: max= is an Int literal (or a constant bound to one), 1..ITERATE_MAX."""
        v = literal_value(node)
        if v is None and isinstance(node, ast.Name):
            if node.id in scope.consts and node.id not in scope.scalars:
                return False  # an ill-typed constant: already reported
            v = scope.consts.get(node.id)
            if v is None and node.id not in scope.scalars and self._silenced(node):
                return False
        if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= ITERATE_MAX:
            self.ctx.set_type(node, LitT(Scalar.INT))
            return True
        self.ctx.error(
            "L436",
            node,
            f"iterate max= must be an Int literal between 1 and {ITERATE_MAX:,}",
            "e.g. max=50: the most rounds the loop may take before it fails with R610",
        )
        return False

    def _iterate_until(self, node: ast.expr, t: TableT, scope: _Scope) -> bool:
        """until= is "fixpoint" or a Bool column expression over the step's schema."""
        hint = f'until="{ITERATE_FIXPOINT}", or a Bool column expression such as col("x") >= 3'
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == ITERATE_FIXPOINT:
                return True
            self.ctx.error(
                "L404", node, f'iterate until= must be "{ITERATE_FIXPOINT}" or a predicate', hint
            )
            return False
        vt = self._typer(t.schema, scope).type_of(node)
        match vt:
            case UnknownT():
                return False
            case ColT(scalar=Scalar.BOOL):
                return True
            case _:
                self.ctx.error(
                    "L404",
                    node,
                    f"iterate until= must be a Bool column expression, got "
                    f"{'a constant' if isinstance(vt, LitT) else vt.scalar.value}",
                    hint,
                )
                return False

    def _for_each(self, call: ast.Call, scope: _Scope) -> None:
        """for_each(t, native.<name>, args...): an effect native (L438) whose arguments
        are typed like native.<name>(args...) against t's columns."""
        if len(call.args) < 2 or call.keywords:
            return  # P3 reported the call shape
        t = self._table_arg(call.args[0], scope)
        native = call.args[1]
        if self.t2 is not None and isinstance(native, ast.Name) and native.id in self.t2.procedures:
            self._for_each_procedure(call, native.id, t, scope)
            return
        if not (isinstance(native, ast.Attribute) and isinstance(native.value, ast.Name)):
            return  # P3 reported it
        name = native.attr
        sig = self.natives.get(name)
        if sig is None:
            known = sorted(n for n, s in self.natives.items() if s.effect)
            listed = "effect natives: " + ", ".join(known) if known else "the profile declares none"
            self.ctx.error(
                "L106",
                native,
                f"unknown native function native.{name}",
                did_you_mean(name, known) or listed,
            )
            return
        usage = f"for_each(rows, native.{name}, {', '.join(p.value for p in sig.params)})"
        if not sig.effect:
            self.ctx.error(
                "L438",
                native,
                f"native.{name} is a pure function; for_each sends an effect native",
                f"use native.{name}(...) in derive(...), or declare an effect native "
                "(NativeSig(..., effect=True))",
            )
            return
        args = call.args[2:]
        if len(args) != len(sig.params):
            self.ctx.error(
                "L404",
                call,
                f"native.{name} takes {len(sig.params)} argument(s), got {len(args)}",
                f"write {usage}",
            )
            return
        if not isinstance(t, TableT):
            return
        typer = self._typer(t.schema, scope)
        for i, (arg, want) in enumerate(zip(args, sig.params, strict=True), start=1):
            vt = typer.type_of(arg)
            if isinstance(vt, UnknownT):
                continue
            if not (vt.scalar is want or (want is Scalar.FLOAT and vt.scalar is Scalar.INT)):
                self.ctx.error(
                    "L402",
                    arg,
                    f"native.{name} expects {want.value} for argument {i}, got {vt.scalar.value}",
                    f"write {usage}",
                )

    def _write(self, call: ast.Call) -> None:
        if _str_constant(call.args[1]) is None:
            self.ctx.error(
                "L404", call.args[1], "write() path must be a string literal", 'e.g. "out.parquet"'
            )

    # ---- steps ----------------------------------------------------------------

    def _step(self, fn: ast.FunctionDef, scope: _Scope) -> None:
        params: list[tuple[str, TableT | ScalarParamT | UnknownT]] = []
        for a in fn.args.args:
            # a parameter named like an earlier module constant is P2's L201; the
            # parameter wins inside the body
            scope.consts.pop(a.arg, None)
            scope.shapes.pop(a.arg, None)
            scope.nullconsts.pop(a.arg, None)
            ann = a.annotation
            if isinstance(ann, ast.Name) and ann.id in SCALAR_NAMES:
                scope.scalars[a.arg] = SCALAR_NAMES[ann.id]
                params.append((a.arg, ScalarParamT(scope.scalars[a.arg])))
                continue
            scope.scalars.pop(a.arg, None)
            pt = self._table_annotation(ann, a)
            if isinstance(pt, TableT):
                # the body is checked once for every caller: no argument's keys are
                # assumed (L420 needs a key the body establishes itself)
                pt = TableT(pt.schema, nullable=pt.schema.nullable)
            scope.env[a.arg] = pt
            params.append((a.arg, pt))
        result: TableOrUnknown = UNKNOWN
        for stmt in fn.body:
            match stmt:
                case ast.Assign(targets=[ast.Name(id=name)]):
                    self._assign(name, stmt.value, scope)
                case ast.Return(value=ast.Name(id=name) as ret):
                    result = scope.env.get(name, UNKNOWN)
                    kind = None if name in scope.env else self._non_table_kind(name, scope)
                    if kind is not None and not self._silenced(ret):
                        self.ctx.error(
                            "L602",
                            ret,
                            f"a step must return a table local or parameter, "
                            f"got {kind[0]} '{name}'",
                            "return the name of the step's final table",
                        )
                case _:
                    raise TypeError("P1 guarantees step bodies are Assign/Return <name>")
        result = self._declared_result(fn, result)
        sig = _Sig(tuple(params), result)
        if fn.name not in self.sigs:  # first definition wins (P2 reported the rebinding)
            self.sigs[fn.name] = sig
            st = self._step_t(sig)
            if st is not None:
                self.ctx.set_type(fn, st)

    def _table_annotation(self, ann: ast.expr | None, where: ast.arg) -> TableOrUnknown:
        name = annotation_schema_name(ann)
        if name is None:
            self.ctx.error(
                "L601",
                where,
                "step parameters need a concrete schema, e.g. Table[Orders]",
                "write Table[SchemaName]",
            )
            return UNKNOWN
        return self._schema_ref(ann.slice)  # type: ignore[union-attr]

    def _schema_ref(self, node: ast.expr) -> TableOrUnknown:
        assert isinstance(node, ast.Name)
        if node.id in self.schemas:
            return TableT(self.schemas[node.id])
        if not self._silenced(node):
            available = ", ".join(sorted(self.schemas)) or "none"
            self.ctx.error("L414", node, f"unknown schema '{node.id}'", f"available: {available}")
        return UNKNOWN

    def _declared_result(self, fn: ast.FunctionDef, inferred: TableOrUnknown) -> TableOrUnknown:
        if annotation_schema_name(fn.returns) is None:
            return inferred  # bare `Table`: inferred from the body
        declared = self._schema_ref(fn.returns.slice)  # type: ignore[union-attr]
        if isinstance(declared, UnknownT):
            return inferred
        if isinstance(inferred, TableT) and inferred.schema != declared.schema:
            self.ctx.error(
                "L605",
                fn.returns,  # type: ignore[arg-type]
                f"step '{fn.name}' returns {inferred.schema.render()} "
                f"but declares {declared.schema.render()}",
                "fix the body or change the return annotation (bare Table infers it)",
            )
            return declared  # callers see the declared schema: no cascade past the step
        if isinstance(inferred, TableT):
            return replace(inferred, schema=declared.schema)
        return TableT(declared.schema)

    def _call_step(self, call: ast.Call, name: str, scope: _Scope) -> TableOrUnknown:
        sig = self.sigs.get(name)
        if sig is None or name in self.redefined:
            return UNKNOWN  # defined later / recursive / redefined: P2 reported it
        tables = [(p, t) for p, t in sig.params if not isinstance(t, ScalarParamT)]
        scalar_params = {p: t.scalar for p, t in sig.params if isinstance(t, ScalarParamT)}
        if len(call.args) != len(tables):
            return UNKNOWN  # P3 reported the call shape
        arg_types: list[TableT] = []
        for arg, (p, pt) in zip(call.args, tables, strict=True):
            at = self._table_arg(arg, scope)
            if isinstance(at, TableT):
                arg_types.append(at)
            if isinstance(at, TableT) and isinstance(pt, TableT) and at.schema != pt.schema:
                self.ctx.error(
                    "L418",
                    arg,
                    f"step '{name}' parameter '{p}' expects {pt.schema.render()}, "
                    f"got {at.schema.render()}",
                    "derive/select/rename the table to match first",
                )
        for kw in call.keywords:
            want = scalar_params.get(kw.arg or "")
            if want is not None:
                self._scalar_arg(name, kw, want, scope)
        if self.in_endpoint and isinstance(sig.result, TableT) and len(arg_types) == len(tables):
            return self._api_meta(call, "step", arg_types, sig.result)
        return sig.result

    def _scalar_arg(self, step: str, kw: ast.keyword, want: Scalar, scope: _Scope) -> None:
        value = kw.value
        got = constant_scalar(value)
        if got is None and isinstance(value, ast.Name):
            if value.id in scope.consts:
                if value.id not in scope.scalars:
                    return  # an ill-typed constant: already reported
                if scope.consts[value.id] is None:
                    self.ctx.error(
                        "L402",
                        value,
                        f"step '{step}' parameter '{kw.arg}' expects a {want.value} literal, "
                        f"scalar parameter or constant bound to a literal; '{value.id}' "
                        "is computed",
                        None,
                    )
                    return
            got = scope.scalars.get(value.id)
            if got is None and self._silenced(value):
                return
        if got is None:
            self.ctx.error(
                "L402",
                value,
                f"step '{step}' parameter '{kw.arg}' expects a {want.value} literal, "
                "scalar parameter or constant bound to a literal",
                None,
            )
        elif not (got is want or (want is Scalar.FLOAT and got is Scalar.INT)):
            self.ctx.error(
                "L402",
                value,
                f"step '{step}' parameter '{kw.arg}' expects {want.value}, got {got.value}",
                None,
            )
            return
        if got is not None:
            self.ctx.set_type(value, LitT(got))

    # ---- endpoints (API profile) ------------------------------------------------

    def _endpoint(self, fn: ast.FunctionDef, scope: _Scope) -> None:
        assert self.api is not None
        deco = endpoint_decorator(fn)
        decl = parse_decl(deco) if deco is not None else None
        if not isinstance(decl, Decl):
            return  # P1 reported it
        caller = user_table(self.api)
        params = endpoint_params(fn, decl.method, decl.path)
        self.param_specs = {p.name: p for p in params}
        for spec in params:
            scope.consts.pop(spec.name, None)
            scope.shapes.pop(spec.name, None)
            scope.nullconsts.pop(spec.name, None)
            if spec.scalar is None:
                scope.env[spec.name] = caller if caller is not None else UNKNOWN
            else:
                scope.scalars[spec.name] = spec.scalar
                if spec.nullable:  # `p: T | None = None` is None when not given
                    scope.nullconsts[spec.name] = True
        public =isinstance(decl.auth, ast.Name) and decl.auth.id == PUBLIC_ROLE
        admin = self.api.admin_role
        self.endpoint_admin = isinstance(decl.auth, ast.Name) and decl.auth.id == admin
        if not public and all(p.name != USER_PARAM for p in params):
            # a missing `user` parameter is P6's L801; type its uses anyway
            scope.env[USER_PARAM] = caller if caller is not None else UNKNOWN
        if caller is not None and isinstance(scope.env.get(USER_PARAM), TableT):
            for column in caller.nullable:  # user.<column> may be missing
                scope.nullconsts[f"{USER_PARAM}.{column}"] = True
        self.in_endpoint = True
        try:
            for stmt in fn.body:
                match stmt:
                    case ast.Assign(targets=[ast.Name(id=name)]):
                        self._assign(name, stmt.value, scope)
                    case ast.Expr(value=ast.Call(func=ast.Name(id=op)) as call) if op == FOR_EACH:
                        self._for_each(call, scope)
                    case ast.Return(value=ast.Name(id=name) as ret):
                        t = scope.env.get(name, UNKNOWN)
                        kind = None if name in scope.env else self._non_table_kind(name, scope)
                        if kind is not None and not self._silenced(ret):
                            self.ctx.error(
                                "L602",
                                ret,
                                f"an endpoint must return a table, got {kind[0]} '{name}'",
                                "return the name of the table to respond with",
                            )
                        self.ctx.set_type(ret, t)
                    case _:
                        pass  # P1 reported it
        finally:
            self.in_endpoint = False

    def _db_table(self, node: ast.expr) -> tuple[str, Schema] | None:
        """The database table named by fetch()/insert()'s first argument."""
        assert self.api is not None
        if isinstance(node, ast.Name) and node.id in self.api.tables:
            return node.id, self.api.tables[node.id]
        if isinstance(node, ast.Name) and self._silenced(node):
            return None
        available = ", ".join(sorted(self.api.tables)) or "none"
        self.ctx.error("L414", node, "expected a database table name", f"available: {available}")
        return None

    def _api_op(self, call: ast.Call, op: str, scope: _Scope) -> TableOrUnknown:
        shape = API_OP_SHAPES[op]
        kws = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        result: TableOrUnknown = UNKNOWN
        if len(call.args) == len(shape.positional) and shape.required_kw <= kws.keys():
            inputs = [self._table_arg(a, scope) for a in call.args[: shape.tables]]
            tables = [t for t in inputs if isinstance(t, TableT)]
            if len(tables) == len(inputs):
                if self.t2 is not None and tables and tables[0].t2_rows:
                    self._t2_rows_rules(call, op, tables)
                result = self._api_transfer(call, op, tables, kws, scope)
        self.ctx.set_type(call, result)
        return result

    def _api_transfer(
        self,
        call: ast.Call,
        op: str,
        tables: list[TableT],
        kws: dict[str, ast.expr],
        scope: _Scope,
    ) -> TableOrUnknown:
        match op:
            case "fetch":
                found = self._db_table(call.args[0])
                if found is None:
                    return UNKNOWN
                name, schema = found
                owned = schema.owner is not None or schema.owner_via is not None
                return TableT(
                    schema,
                    keys=schema.keys,
                    nullable=schema.nullable,
                    owner=schema.owner,
                    owner_via=schema.owner_via,
                    owned_from=frozenset({name}) if owned else frozenset(),
                    owner_cols=_owner_cols(schema),
                    private=schema.private,
                    source=name,
                    plain=plain_columns(schema),
                )
            case "accessible" | "owned" | "mine":
                return self._owned(call, op, tables[0], kws["by"])
            case "one":
                self._else(kws["else_"])
                return replace(tables[0], at_most_one_row=True, paged=False, page_total=False)
            case "require":
                self._else(kws["else_"])
                if is_owner_is(call.args[1]):
                    return self._owner_is(call.args[1], tables[0])
                self._condition(call.args[1], tables[0], scope, "require()")
                return tables[0]
            case "page":
                return replace(tables[0], paged=True, page_total=self._page_total(kws))
            case "insert":
                return self._insert(call, scope)
            case "update":
                return self._update(call, tables[0], scope)
            case "delete":
                self._write_source(call, tables[0], "delete")
                return tables[0]
            case _:
                raise TypeError(f"no transfer function for API op '{op}'")

    def _page_total(self, kws: dict[str, ast.expr]) -> bool:
        """page(..., total=True|False): a bool literal; True needs the profile's page
        shape to carry a "total" field."""
        assert self.api is not None
        node = kws.get("total")
        if node is None:
            return False
        v = node.value if isinstance(node, ast.Constant) else None
        if not isinstance(v, bool):
            self.ctx.error("L404", node, "page total= must be True or False", "e.g. total=True")
            return False
        if v and not self.api.page_shape.get("total"):
            fields = ", ".join(k for k, f in self.api.page_shape.items() if f) or "none"
            self.ctx.error(
                "L404",
                node,
                "page(total=True) needs the profile's page shape to include a 'total' field",
                f"this profile's pages carry: {fields}; remove total=True",
            )
            return False
        return v

    def _owner_is(self, cond: ast.Call, t: TableT) -> TableOrUnknown:
        """require(t, owner_is(user), else_=...): every row is the caller's (Admin
        callers pass), so the result is owner-checked like owned(t, by=user)."""
        match cond:
            case ast.Call(args=[ast.Name(id=name) as arg], keywords=[]) if name == USER_PARAM:
                pass
            case ast.Call(args=[ast.Name() as arg], keywords=[]) if self._silenced(arg):
                return UNKNOWN
            case _:
                self.ctx.error(
                    "L404",
                    cond,
                    "owner_is() takes the caller: owner_is(user)",
                    'e.g. require(row, owner_is(user), else_=Forbidden("not yours"))',
                )
                return UNKNOWN
        if t.owner is None and t.owner_via is None:
            owned = sorted(n for n, s in self.schemas.items() if s.owner is not None or s.owner_via)
            self.ctx.error(
                "L404",
                cond,
                "owner_is() needs rows of a table with an owner column; this table has none",
                "remove the check: only " + (", ".join(owned) or "no tables") + " are per-user",
            )
            return UNKNOWN
        self.ctx.set_type(cond, ColT(Scalar.BOOL))
        return replace(t, owner_checked=True)

    def _condition(self, pred: ast.expr, t: TableT, scope: _Scope, what: str) -> None:
        pt = self._typer(t.schema, scope, t).type_whole(pred)
        match pt:
            case ColT(scalar=Scalar.BOOL) | UnknownT():
                pass
            case LitT(scalar=Scalar.BOOL) if mentions_caller(pred):
                pass  # a condition on the caller (e.g. ~is_admin(user)): per request
            case LitT(scalar=Scalar.BOOL):
                self.ctx.error(
                    "L304",
                    pred,
                    f"{what} condition does not reference any column",
                    'compare a column, e.g. col("status") == "pending"',
                )
            case ColT(scalar=s) | LitT(scalar=s):
                self.ctx.error(
                    "L403",
                    pred,
                    f"{what} condition must be Bool, got {s.value}",
                    'compare it, e.g. col("status") == "pending"',
                )

    def _unique_or(self, node: ast.expr) -> None:
        """unique_or= must construct a Conflict: Conflict("detail") (or Conflict())."""
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Conflict"
            and not node.keywords
            and len(node.args) <= 1
            and all(_str_constant(a) is not None for a in node.args)
        ):
            return
        self.ctx.error(
            "L404",
            node,
            "unique_or= must be a Conflict with an optional message: the 409 answered "
            "when the write hits a UNIQUE constraint",
            'e.g. unique_or=Conflict("email already registered")',
        )

    def _else(self, node: ast.expr) -> None:
        """else_= must construct a declarable error: NotFound("detail")."""
        match node:
            case ast.Call(func=ast.Name(id=name), args=args, keywords=[]) if (
                name in API_ERRORS
                and len(args) <= 1
                and all(_str_constant(a) is not None for a in args)
            ):
                return
            case ast.Call(func=ast.Name() as func) if self._silenced(func):
                return
        errs = ", ".join(API_ERRORS)
        self.ctx.error(
            "L404",
            node,
            f"else_= must be an error with an optional message: one of {errs}",
            'e.g. else_=NotFound("order")',
        )

    def _owned(self, call: ast.Call, op: str, t: TableT, by: ast.expr) -> TableOrUnknown:
        """accessible(t, by=user) (alias owned) / mine(t, by=user): the rows the caller
        may access / the caller's own rows. Either one is an ownership filter (mine() is
        the stricter)."""
        if not (isinstance(by, ast.Name) and by.id == USER_PARAM):
            if not self._silenced(by):
                self.ctx.error("L404", by, f"{op}() filters by the caller", "write by=user")
            return UNKNOWN
        if t.owner is None and t.owner_via is None:
            owned = sorted(n for n, s in self.schemas.items() if s.owner is not None or s.owner_via)
            self.ctx.error(
                "L404",
                call,
                f"{op}() needs rows of a table with an owner column; this table has none",
                f"remove {op}(): only " + (", ".join(owned) or "no tables") + " are per-user",
            )
            return UNKNOWN
        return replace(t, owner_checked=True)

    def _compatible(self, value: ast.expr, got: Scalar, want: Scalar, column: str) -> None:
        if not (got is want or (want is Scalar.FLOAT and got is Scalar.INT)):
            self.ctx.error(
                "L402",
                value,
                f"column '{column}' is {want.value}, got {got.value}",
                None,
            )

    def _insert(self, call: ast.Call, scope: _Scope) -> TableOrUnknown:
        found = self._db_table(call.args[0])
        if found is None:
            return UNKNOWN
        name, schema = found
        typer = self._typer(Schema(()), scope)
        for kw in call.keywords:
            assert kw.arg is not None
            if kw.arg == UNIQUE_OR:
                self._unique_or(kw.value)
                continue
            if kw.arg == "owner":
                if not (isinstance(kw.value, ast.Name) and kw.value.id == USER_PARAM):
                    self.ctx.error("L404", kw.value, "insert() owner= must be user", "owner=user")
                elif schema.owner_via is not None:
                    fk, parent = schema.owner_via
                    self.ctx.error(
                        "L404",
                        kw,
                        f"'{name}' is owned through {fk} -> {parent}, not by an owner column",
                        f"remove owner=user; set {fk}= to the id of a {parent} row the caller owns",
                    )
                elif schema.owner is None:
                    self.ctx.error(
                        "L404",
                        kw,
                        f"'{name}' has no owner column",
                        "remove owner=user",
                    )
                continue
            if not self._require_columns([kw.arg], schema, kw):
                continue
            if self._null_write(kw, schema, name):
                continue
            self._one_of_write(kw, schema, name)
            vt = typer.type_of(kw.value)
            want = schema.get(kw.arg)
            if not isinstance(vt, UnknownT) and want is not None:
                self._compatible(kw.value, vt.scalar, want, kw.arg)
        self._insert_columns(call, name, schema)
        return TableT(
            schema,
            at_most_one_row=True,
            keys=schema.keys,
            nullable=schema.nullable,
            owner=schema.owner,
            owner_via=schema.owner_via,
            owner_cols=_owner_cols(schema),
            owner_checked=True,  # the caller's own new row (P6 enforces owner=user)
            private=schema.private,
            source=name,
            plain=plain_columns(schema),
        )

    def _insert_columns(self, call: ast.Call, name: str, schema: Schema) -> None:
        """L404: an insert never sets the key the database assigns, and sets every other
        column that is not nullable (the owner column through owner=user)."""
        given = {kw.arg for kw in call.keywords if kw.arg is not None}
        key = generated_key(schema)
        if key is not None and key in given:
            kw = next(k for k in call.keywords if k.arg == key)
            self.ctx.error(
                "L404",
                kw,
                f"insert() sets the key column '{key}' of {name}",
                f"remove {key}=; the database assigns a new {key}",
            )
        if key is not None and "owner" in given and schema.owner == key:
            self.ctx.error(
                "L404",
                call,
                f"rows of {name} are owned through their key '{key}'; "
                "insert() cannot create one for the caller",
                "remove owner=user",
            )
        # the owner column is P6's (L802 without owner=user; L404 for Admin); a Uuid
        # key is filled with a new uuid4 when not given
        missing = [
            c
            for c in schema.names()
            if c not in (key, schema.owner, uuid_key(schema))
            and c not in schema.nullable
            and c not in given
        ]
        if missing:
            listed = ", ".join(f"'{c}'" for c in missing)
            hint = "add " + ", ".join(f"{c}=<value>" for c in missing)
            self.ctx.error(
                "L404",
                call,
                f"insert into {name} does not set required column(s) {listed}",
                hint,
            )

    def _write_source(self, call: ast.Call, t: TableT, op: str) -> Schema | None:
        """The database table update()/delete() writes, or None after one L841."""
        assert self.api is not None
        schema = self.api.tables.get(t.source) if t.source is not None else None
        if schema is None:
            self.ctx.error(
                "L841",
                call,
                f"{op}() needs rows of one database table",
                f"{op} the result of fetch/where/accessible/one on that table, not of a join, "
                "group, rename or step",
            )
            return None
        names = set(t.schema.names())
        if not any(set(k) <= names for k in schema.keys):
            keys = " or ".join(_cols(k) for k in schema.keys) or "(none declared)"
            self.ctx.error(
                "L841",
                call,
                f"{op}() cannot identify the rows: the table lacks the key {keys}",
                f"keep the key columns before calling {op}()",
            )
            return None
        return schema

    def _update(self, call: ast.Call, t: TableT, scope: _Scope) -> TableOrUnknown:
        schema = self._write_source(call, t, "update")
        typer = self._typer(t.schema, scope)
        key_cols = {c for k in schema.keys for c in k} if schema is not None else set()
        given = {kw.arg for kw in call.keywords}
        transfer = "owner" in given or (
            schema is not None and schema.owner is not None and schema.owner in given
        )
        for kw in call.keywords:
            assert kw.arg is not None
            if kw.arg == UNIQUE_OR:
                self._unique_or(kw.value)
                continue
            if kw.arg == ELSE_KW:
                if transfer:
                    self._else(kw.value)
                else:
                    self.ctx.error(
                        "L404",
                        kw,
                        "update() takes else_= only with an owner transfer (owner=...): the "
                        "error answered when the new owner does not exist",
                        "remove else_=; check rows with one(..., else_=...) or require(...)",
                    )
                continue
            if kw.arg == "owner":
                if schema is not None:
                    self._new_owner(kw, t, schema, scope, given)
                continue
            if schema is not None and not self._require_columns([kw.arg], schema, kw):
                continue
            if kw.arg in key_cols:
                # a new key would orphan child rows, collide with another row (UNIQUE)
                # and no longer identify the row the response reads back
                self.ctx.error(
                    "L404",
                    kw,
                    f"update() sets the key column '{kw.arg}' of {t.source}",
                    "keys cannot change; insert a new row instead",
                )
                continue
            if schema is not None and self._null_write(kw, schema, t.source or ""):
                continue
            if schema is not None:
                self._one_of_write(kw, schema, t.source or "")
            vt = typer.type_of(kw.value)
            want = schema.get(kw.arg) if schema is not None else None
            if not isinstance(vt, UnknownT) and want is not None:
                self._compatible(kw.value, vt.scalar, want, kw.arg)
        return t

    def _new_owner(
        self, kw: ast.keyword, t: TableT, schema: Schema, scope: _Scope, given: set[str | None]
    ) -> None:
        """update(t, owner=<new owner>): the caller (`user`), a one-row users table, or
        a value of the users table's key type (P6 checks who may transfer and that the
        new owner exists)."""
        assert self.api is not None
        table, value = t.source or "", kw.value
        if schema.owner_via is not None:
            fk, parent = schema.owner_via
            self.ctx.error(
                "L404",
                kw,
                f"'{table}' is owned through {fk} -> {parent}, not by an owner column",
                f"remove owner=; set {fk}= to the id of a {parent} row instead",
            )
            return
        if schema.owner is None:
            self.ctx.error("L404", kw, f"'{table}' has no owner column", "remove owner=")
            return
        if schema.owner in given:
            self.ctx.error(
                "L404",
                kw,
                f"update() sets the owner twice: owner= and {schema.owner}=",
                "keep one of them, e.g. owner=",
            )
            return
        users = self.api.tables.get(self.api.users_table)
        key = users.keys[0][0] if users is not None and users.keys else None
        if isinstance(value, ast.Name) and value.id == USER_PARAM:
            return  # the caller
        if isinstance(value, ast.Name) and value.id in scope.env:
            rows = scope.env[value.id]
            if isinstance(rows, UnknownT):
                return
            if not (
                rows.source == self.api.users_table
                and rows.at_most_one_row
                and key is not None
                and key in rows.schema
            ):
                self.ctx.error(
                    "L404",
                    value,
                    f"owner= takes one row of {self.api.users_table} (its {key or 'key'}), "
                    f"the caller (user), or a {self.api.users_table} key value",
                    f'e.g. target = one(where(fetch({self.api.users_table}), col("email") == '
                    f'email), else_=NotFound("...")); update(t, owner=target)',
                )
            return
        want = schema.get(schema.owner)
        vt = self._typer(t.schema, scope).type_of(value)
        if not isinstance(vt, UnknownT) and want is not None:
            self._compatible(value, vt.scalar, want, schema.owner)

    def _one_of_write(self, kw: ast.keyword, schema: Schema, table: str) -> None:
        """L407: a OneOf column (Schema.one_of) is written only a listed literal or an
        endpoint parameter declared OneOf(...) with listed values -- so a request with
        any other value is refused at the API boundary (422), before the write."""
        column, value = kw.arg or "", kw.value
        allowed = schema.choices(column)
        if allowed is None:
            return
        where = f"column '{column}' of {table} holds only {_listed(allowed)}"
        hint = f'a parameter `{column}: OneOf({", ".join(_q(v) for v in allowed)})`'
        match value:
            case ast.Constant(value=str() as text):
                if text not in allowed:
                    self.ctx.error("L407", kw, f"{where}, not {_q(text)}", f"write {hint[2:]}")
                return
            case ast.Name(id=name) if self.in_endpoint and name in self.param_specs:
                spec = self.param_specs[name]
                if spec.one_of is None:
                    self.ctx.error(
                        "L407",
                        kw,
                        f"{where}; parameter '{name}' may be any text",
                        f"declare {hint[2:]} (or a subset), so another value is a 422",
                    )
                elif extra := [v for v in spec.one_of if v not in allowed]:
                    self.ctx.error(
                        "L407",
                        kw,
                        f"{where}; parameter '{name}' also allows {_listed(tuple(extra))}",
                        f"remove {', '.join(map(_q, extra))} from its OneOf(...)",
                    )
                return
            case _:
                self.ctx.error(
                    "L407",
                    kw,
                    f"{where}; the written value is not known to be one of them",
                    f"write a listed literal or {hint}",
                )

    def _null_write(self, kw: ast.keyword, schema: Schema, table: str) -> bool:
        """None as an insert()/update() value: a null for a nullable column, L410 for a
        required one (True: the value was None and is handled). Also L410: a `| None`
        parameter written directly to a required column."""
        column, value = kw.arg or "", kw.value
        if isinstance(value, ast.Constant) and value.value is None:
            want = schema.get(column)
            if column not in schema.nullable:
                self.ctx.error(
                    "L410",
                    kw,
                    f"column '{column}' of {table} is not nullable; it cannot be set to None",
                    "write a value (only nullable columns take None)",
                )
            elif want is not None:
                self.ctx.set_type(value, LitT(want))
            return True
        spec = self.param_specs.get(value.id) if isinstance(value, ast.Name) else None
        if (
            self.in_endpoint
            and spec is not None
            and spec.nullable
            and column not in schema.nullable
        ):
            self.ctx.error(
                "L410",
                kw,
                f"column '{column}' of {table} is not nullable, but parameter "
                f"'{spec.name}' may be None (declared `| None`)",
                f"make '{spec.name}' required or give it a default value, or write "
                f"coalesce({spec.name}, <value>)",
            )
        return False

    def _api_meta(self, call: ast.Call, op: str, inputs: list[TableT], out: TableT) -> TableT:
        """Carry the API metadata (owner, ownership, private columns, source, page)
        from an op's input tables to its result."""
        if op == "join":
            # on={"id": "order_id"}: the right key columns are dropped, their values
            # live on in the left key columns -- carry the right table's metadata over
            # under the left names, exactly as a rename + list-form join would
            on_node = next((kw.value for kw in call.keywords if kw.arg == "on"), None)
            back = {b: a for a, b in join_on_pairs(on_node) or () if a != b}
            if back and len(inputs) == 2:
                inputs = [inputs[0], _rename_meta(inputs[1], back)]
        t = inputs[0]
        names = set(out.schema.names())
        owned_from = frozenset().union(*(i.owned_from for i in inputs))
        unchecked = frozenset().union(*(i.unchecked_owned for i in inputs))
        private = frozenset().union(*(i.private for i in inputs)) & names
        owner = next((i.owner for i in inputs if i.owner is not None and i.owner in names), None)
        via = next(
            (i.owner_via for i in inputs if i.owner_via is not None and i.owner_via[0] in names),
            None,
        )
        owner_cols = frozenset().union(*(i.owner_cols for i in inputs))
        plain = frozenset().union(*(i.plain for i in inputs))
        narrowed = any(i.narrowed for i in inputs)
        source: str | None = None
        paged = page_total = False
        # an identity column (key / owner / owner_via) was given new values: the rows
        # no longer identify database rows, nor are they known to be the caller's
        rebound = False
        kws = [kw for kw in call.keywords if kw.arg is not None]
        match op:
            case "where" if is_proc_call(call.args[1], self.t2):
                # a procedure filter runs over the read's rows in the app layer: a page
                # stays that page; the procedure may single out an owner (row())
                source, paged, page_total = t.source, t.paged, t.page_total
                narrowed = narrowed or bool(t.owned_from)
            case "where" | "sort" | "limit":
                source = t.source
                if op == "where" and t.owned_from and len(call.args) > 1:
                    # filtering on anything but a plain (publicly aggregatable) stored
                    # column -- a key, an owner column, an email, a value derived from
                    # them, ... -- may single out one owner: a count over the result
                    # would be an oracle for that owner's data
                    narrowed = narrowed or _reads_other(call.args[1], t.plain)
                if op == "sort" and t.owned_from:
                    # ordering by such a column (then limit) singles out rows, too
                    by = _str_list_of(next((kw.value for kw in kws if kw.arg == "by"), None))
                    narrowed = narrowed or not set(by) <= t.plain
            case "select":
                source, paged, page_total = t.source, t.paged, t.page_total
            case "window":
                # new columns only (the input's metadata carries over, like derive); a
                # window over a page is not that page any more (page it again)
                new = {kw.arg for kw in kws if kw.arg not in WINDOW_KW}
                private = t.private | {
                    kw.arg
                    for kw in kws
                    if kw.arg in new and _reads(kw.value, t.private, self._verifiers)
                }
                owner_cols = t.owner_cols | {
                    kw.arg for kw in kws if kw.arg in new and _reads(kw.value, t.owner_cols)
                }
                source = t.source
            case "derive":
                overwritten = {kw.arg for kw in kws}
                private = (t.private - overwritten) | {
                    kw.arg
                    for kw in kws
                    if kw.arg
                    and (
                        _reads(kw.value, t.private, self._verifiers)
                        or self._passes_private_row(kw.value, t)
                    )
                }
                owner_cols = (t.owner_cols - overwritten) | {
                    kw.arg for kw in kws if kw.arg and _reads(kw.value, t.owner_cols)
                }
                plain = t.plain - overwritten
                owner = t.owner if t.owner not in overwritten else None
                via = t.owner_via if via is not None and via[0] not in overwritten else None
                paged, page_total = t.paged, t.page_total
                if overwritten & self._identity(t):
                    rebound = True
                else:
                    source = t.source
            case "rename":
                mapping = {kw.arg: _str_constant(kw.value) or kw.arg for kw in kws if kw.arg}
                private = frozenset(mapping.get(c, c) for c in t.private)
                owner_cols = frozenset(mapping.get(c, c) for c in t.owner_cols)
                plain = frozenset(mapping.get(c, c) for c in t.plain)
                owner = mapping.get(t.owner, t.owner) if t.owner is not None else None
                if t.owner_via is not None:
                    via = (mapping.get(t.owner_via[0], t.owner_via[0]), t.owner_via[1])
                paged, page_total = t.paged, t.page_total
            case "group":
                by = set(_str_list_of(next((kw.value for kw in kws if kw.arg == "by"), None)))
                private = (t.private & names) | {
                    kw.arg
                    for kw in kws
                    if kw.arg != "by" and _reads(kw.value, t.private, self._verifiers)
                }
                plain = t.plain & by
                if not self._per_owner_groups(t, kws):
                    # every group mixes the rows of many owners: not owner-private rows
                    owned_from = unchecked = frozenset()
                    owner, via, owner_cols, narrowed = None, None, frozenset(), False
                else:
                    owner_cols = (t.owner_cols & names) | {
                        kw.arg for kw in kws if kw.arg != "by" and _reads(kw.value, t.owner_cols)
                    }
            case "join":
                pairs = join_on_pairs(next((kw.value for kw in kws if kw.arg == "on"), None))
                on = [a for a, _ in pairs or ()]
                if owned_from and not set(on) <= plain:
                    narrowed = True
            case "step":
                # a step cannot read the database, so with one-row inputs every op in
                # it (joins included) yields at most one row
                if all(i.at_most_one_row for i in inputs):
                    out = replace(out, at_most_one_row=True)
                # step bodies are typed without API metadata: a step that mentions a
                # private / owner-identifying column of its inputs may have copied it
                # into any output column (rename, derive, group, ...)
                assert isinstance(call.func, ast.Name)
                mentioned = step_mentions(self.step_defs, call.func.id)
                if mentioned & frozenset().union(*(i.private for i in inputs)):
                    private = frozenset(names)
                if mentioned & owner_cols:
                    owner_cols = frozenset(names)
                plain = plain - mentioned
                columns = frozenset().union(*(i.schema.names() for i in inputs))
                if mentioned & (columns - frozenset().union(*(i.plain for i in inputs))):
                    narrowed = True
            case _:
                pass
        return replace(
            out,
            owner=owner,
            owner_via=via,
            owned_from=owned_from,
            owner_checked=bool(owned_from) and not unchecked and not rebound,
            owner_cols=owner_cols & names,
            narrowed=narrowed and bool(owned_from),
            private=private & names,
            source=source,
            paged=paged,
            page_total=page_total,
            plain=plain & names,
        )

    # ---- T2: procedures in endpoints (lattice.check.t2_calls) --------------------------

    def _t2_op(self, call: ast.Call, op: str) -> str | None:
        """The kind of T2-post op ("filter" / "derive") when this op calls a procedure."""
        if op == "where" and len(call.args) > 1 and is_proc_call(call.args[1], self.t2):
            return "filter"
        if op == "derive" and any(is_proc_call(kw.value, self.t2) for kw in call.keywords):
            return "derive"
        return None

    def _t2_rows_rules(self, call: ast.Call, op: str, tables: list[TableT]) -> None:
        """L880 / L881: what may follow rows a procedure computed in the app layer."""
        rows = next((t.t2_rows for t in tables if t.t2_rows), "")
        name = ast.unparse(call.args[0]) if call.args else "the table"
        t2_op = self._t2_op(call, op)
        if t2_op == "filter" and tables[0].page_total:
            self.ctx.error(
                "L880",
                call,
                f"a procedure filter after page(..., total=True): the total counts '{name}' "
                "before the filter, in SQL, and the page would be filtered in Python",
                "filter with a T0 condition before page(), or page without total=True",
            )
            return
        if not rows or op in ("select", "rename", "one", "require", "update", "delete"):
            return
        if op in ("page", "limit"):
            if rows == "filtered":
                self.ctx.error(
                    "L880",
                    call,
                    f"a procedure filter before {op}(): '{name}' is filtered in the app layer, "
                    f"so {op}() (and its total) would be computed in Python",
                    f"filter with a T0 condition (where(t, col(...) ...)) before {op}(), or "
                    f"apply the procedure filter after {op}()",
                )
            elif rows == "value":
                self.ctx.error(
                    "L881",
                    call,
                    f"{op}() over '{name}', a procedure's value: there is no SQL read to page",
                    f"{op} the rows you pass to the procedure instead",
                )
            return
        if t2_op == "filter":
            return
        if t2_op == "derive" and all(is_proc_call(kw.value, self.t2) for kw in call.keywords):
            return
        self.ctx.error(
            "L881",
            call,
            f"'{op}' needs SQL, but '{name}' holds rows a procedure computed in the app "
            "layer (after the read's one statement)",
            f"do the {op}() before the procedure step, or compute it in the procedure",
        )

    def _t2_rows_of(self, call: ast.Call, op: str, tables: list[TableT]) -> str:
        rows = next((t.t2_rows for t in tables if t.t2_rows), "")
        t2_op = self._t2_op(call, op)
        if t2_op == "filter":
            return "value" if rows == "value" else "filtered"
        if t2_op == "derive":
            return rows or "post"
        return rows

    def _passes_private_row(self, node: ast.expr, t: TableT) -> bool:
        """A procedure call handing the whole row, private columns included, to a
        procedure (Row(T, private=True), or a record with a private column)."""
        if self.t2 is None or not is_proc_call(node, self.t2):
            return False
        assert isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        proc = self.t2.procedures[node.func.id]
        names = [p for p, _ in proc.sig.params]
        pairs = list(zip(names, node.args, strict=False))
        pairs += [(kw.arg, kw.value) for kw in node.keywords if kw.arg is not None]
        want = dict(proc.sig.params)
        for pname, arg in pairs:
            ptype = want.get(pname)
            if not is_row_call(arg) or ptype is None:
                continue
            if isinstance(ptype, T2RowT) and ptype.private:
                return True
            if isinstance(ptype, T2RecordT):
                fields = {f for f, _ in self.t2.records[ptype.name].fields}
                if fields & t.private:
                    return True
        return False

    def _whole_call(self, name: str, value: ast.expr, scope: _Scope) -> None:
        """name = call(proc, ...): a table (records or rows) or a scalar value."""
        assert isinstance(value, ast.Call)
        result = whole_call(
            value,
            self.ctx,
            scope.env,
            lambda: self._typer(Schema(()), scope),
            self.endpoint_admin,
            self.in_endpoint,
        )
        if scope.bound(name):
            return  # rebinding (P2's L201): the first binding wins
        if isinstance(result, ScalarValue):
            self.ctx.set_type(value, UNKNOWN)
            scope.scalars[name] = result.scalar  # a value of the request, like a parameter
            scope.nullconsts[name] = result.nullable
            return
        self.ctx.set_type(value, result)
        if isinstance(result, TableT):
            scope.env[name] = result
        else:
            scope.consts[name] = None  # already reported: its uses are silent

    def _insert_many(self, call: ast.Call, scope: _Scope) -> TableOrUnknown:
        """insert_many(T, rows=x[, owner=user][, unique_or=Conflict(...)]): every row of
        the table x (a procedure's value) as a new row of T, in one statement."""
        if not self.in_endpoint:
            self.ctx.error("L881", call, "insert_many() is only allowed in an endpoint body", None)
            return UNKNOWN
        kws = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        if len(call.args) != 1 or "rows" not in kws:
            return UNKNOWN  # P3 reported the call shape
        found = self._db_table(call.args[0])
        rows_node = kws["rows"]
        t = self._table_arg(rows_node, scope)
        if found is None or not isinstance(t, TableT):
            return UNKNOWN
        name, schema = found
        if UNIQUE_OR in kws:
            self._unique_or(kws[UNIQUE_OR])
        owner = kws.get("owner")
        if owner is not None:
            if not (isinstance(owner, ast.Name) and owner.id == USER_PARAM):
                self.ctx.error("L404", owner, "insert_many() owner= must be user", "owner=user")
            elif schema.owner is None or schema.owner_via is not None:
                self.ctx.error("L404", owner, f"'{name}' has no owner column", "remove owner=user")
        given = set(t.schema.names())
        for c in t.schema.names():
            want = schema.get(c)
            got = t.schema.get(c)
            if want is None:
                self.ctx.error(
                    "L401",
                    rows_node,
                    f"the rows have column '{c}', which {name} lacks",
                    f"{name} has {', '.join(schema.names())}; select or rename the rows first",
                )
            elif got is not None and not fits(got, want):
                self.ctx.error(
                    "L402",
                    rows_node,
                    f"column '{c}' of {name} is {want.value}, the rows' is {got.value}",
                    None,
                )
            elif c in t.nullable and c not in schema.nullable:
                self.ctx.error(
                    "L410",
                    rows_node,
                    f"column '{c}' of {name} is not nullable, but the rows' may be null",
                    "make the record field non-optional",
                )
            elif (allowed := schema.choices(c)) is not None:
                self.ctx.error(
                    "L407",
                    rows_node,
                    f"column '{c}' of {name} holds only {_listed(allowed)}; the rows' "
                    "values are not known to be among them",
                    f"insert with insert({name}, {c}=...) from a OneOf parameter or a literal",
                )
        key = generated_key(schema)
        if key is not None and key in given:
            self.ctx.error(
                "L404",
                rows_node,
                f"insert_many() sets the key column '{key}' of {name}",
                f"drop the field {key}; the database assigns a new {key}",
            )
        missing = [
            c
            for c in schema.names()
            if c not in (key, schema.owner, uuid_key(schema))
            and c not in schema.nullable
            and c not in given
        ]
        if missing:
            listed = ", ".join(f"'{c}'" for c in missing)
            self.ctx.error(
                "L404",
                call,
                f"insert_many into {name} does not set required column(s) {listed}",
                "give the rows these columns (record fields)",
            )
        return TableT(
            schema,
            keys=schema.keys,
            nullable=schema.nullable,
            owner=schema.owner,
            owner_via=schema.owner_via,
            owner_cols=_owner_cols(schema),
            owner_checked=True,  # the caller's own new rows (P6 enforces owner=user)
            private=schema.private,
            source=name,
            plain=plain_columns(schema),
            bounded=True,
        )

    def _for_each_procedure(
        self, call: ast.Call, name: str, t: TableOrUnknown, scope: _Scope
    ) -> None:
        """for_each(t, proc, args...): an effectful procedure sent once per row after
        COMMIT, its arguments computed like an effect native's."""
        assert self.t2 is not None
        proc = self.t2.procedures[name]
        if not proc.effects:
            self.ctx.error(
                "L882",
                call.args[1],
                f"procedure '{name}' is pure; for_each sends effects",
                f"compute its value per row instead: derive(t, x={name}(...))",
            )
            return
        wants: list[tuple[str, tuple[Scalar, bool]]] = []
        for pname, ptype in proc.sig.params:
            ct = column_type(ptype)
            if ct is None:
                self.ctx.error(
                    "L883",
                    call.args[1],
                    f"'{name}' parameter '{pname}' is {ptype}; for_each sends column values",
                    "take column-typed parameters (Int, Str, Date, ...)",
                )
                return
            wants.append((pname, ct))
        args = call.args[2:]
        if len(args) != len(wants):
            shown = ", ".join(p for p, _ in wants)
            self.ctx.error(
                "L404",
                call,
                f"procedure '{name}' takes {len(wants)} argument(s), got {len(args)}",
                f"write for_each(rows, {name}, {shown})",
            )
            return
        if not isinstance(t, TableT):
            return
        typer = self._typer(t.schema, scope, t)
        for arg, (pname, (scalar, nullable)) in zip(args, wants, strict=True):
            vt = typer.type_of(arg)
            if isinstance(vt, UnknownT):
                continue
            if not fits(vt.scalar, scalar):
                self.ctx.error(
                    "L402",
                    arg,
                    f"'{name}' parameter '{pname}' is {scalar.value}, got {vt.scalar.value}",
                    None,
                )
            elif not nullable and expr_nullable(
                arg, t.nullable, typer.nullconsts, self.null_natives, self.null_calls
            ):
                self.ctx.error(
                    "L883",
                    arg,
                    f"'{name}' parameter '{pname}' is {scalar.value}, but this value may be null",
                    f"declare it '{pname}: {scalar.value} | None'",
                )

    def _identity(self, t: TableT) -> frozenset[str]:
        """Columns of `t` identifying its rows or their owner: its keys, the owner /
        owner_via column, and the identity columns of the database table it reads."""
        cols = {c for k in t.keys for c in k}
        if t.owner is not None:
            cols.add(t.owner)
        if t.owner_via is not None:
            cols.add(t.owner_via[0])
        schema = self.schemas.get(t.source) if t.source is not None else None
        if schema is not None:
            cols |= identity_columns(schema)
        return frozenset(cols)

    def _per_owner_groups(self, t: TableT, kws: list[ast.keyword]) -> bool:
        """Whether group(t, by=..., aggs) can still yield rows of a single owner. Its
        rows mix many owners (ownership is dropped) only when `t` derives from an owned
        table, is neither narrowed (no where/join on a column outside the plain ones)
        nor at most one row, every `by` column is plain and every aggregate reads only
        plain columns. Plain columns are the unmodified stored columns the schema
        declares publicly aggregatable (`Schema.public_group`; never a key, owner or
        private column), so a table without that annotation has no public aggregate
        but the unfiltered whole-table `group(t, by=[], n=agg.count())`."""
        if not t.owned_from:
            return False
        if t.narrowed or t.at_most_one_row:
            return True
        by_node = next((kw.value for kw in kws if kw.arg == "by"), None)
        if not set(_str_list_of(by_node)) <= t.plain:
            return True
        return any(_reads_other(kw.value, t.plain) for kw in kws if kw.arg != "by")


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    return None


def mentions_caller(node: ast.AST) -> bool:
    """The expression reads the caller: `user.<attr>` or `is_admin(...)`."""
    for n in ast.walk(node):
        match n:
            case ast.Attribute(value=ast.Name(id=name)) if name == USER_PARAM:
                return True
            case ast.Call(func=ast.Name(id=name)) if name == IS_ADMIN:
                return True
    return False


def is_owner_is(node: ast.expr) -> bool:
    """A call `owner_is(...)` (the whole condition of a require())."""
    return (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == OWNER_IS
    )


def _rename_meta(t: TableT, mapping: dict[str, str]) -> TableT:
    """`t` with the column names in its API metadata renamed by `mapping`."""

    def m(c: str) -> str:
        return mapping.get(c, c)

    return replace(
        t,
        owner=m(t.owner) if t.owner is not None else None,
        owner_via=(m(t.owner_via[0]), t.owner_via[1]) if t.owner_via is not None else None,
        owner_cols=frozenset(map(m, t.owner_cols)),
        private=frozenset(map(m, t.private)),
        plain=frozenset(map(m, t.plain)),
    )


def _str_list_of(node: ast.expr | None) -> list[str]:
    """The string constants of a list/tuple literal (empty for anything else)."""
    if isinstance(node, ast.List | ast.Tuple):
        return [v for v in map(_str_constant, node.elts) if v is not None]
    return []


def _reads(
    node: ast.expr, columns: frozenset[str], verifiers: frozenset[str] = frozenset()
) -> bool:
    """Whether an expression reads any of `columns` via col("..."), not counting the
    arguments of verifier natives (`native.<v>(...)` with NativeSig.reads_private)."""
    return bool(cols_read(node, verifiers) & columns)


def cols_read(node: ast.expr, verifiers: frozenset[str] = frozenset()) -> frozenset[str]:
    """The columns an expression reads via col("..."), except inside the arguments of
    verifier natives (their result reveals nothing about the columns they read)."""
    found: set[str] = set()
    todo: list[ast.AST] = [node]
    while todo:
        n = todo.pop()
        match n:
            case ast.Call(func=ast.Attribute(value=ast.Name(id=ns), attr=attr)) if (
                ns == NATIVE_NS and attr in verifiers
            ):
                continue
            case ast.Call(func=ast.Name(id="col"), args=[ast.Constant(value=str(name))]):
                found.add(name)
            case _:
                todo.extend(ast.iter_child_nodes(n))
    return frozenset(found)


def _reads_other(node: ast.expr, columns: frozenset[str]) -> bool:
    """Whether an expression reads a column outside `columns`."""
    return any(
        name is not None and name not in columns
        for name in (_col_name(n) for n in ast.walk(node) if isinstance(n, ast.Call))
    )


def run(module: ast.Module, ctx: CheckContext) -> dict[str, StepT]:
    return _Types(module, ctx).run()


def _valid_sort_desc(desc: ast.expr, by: list[str] | None, *, literal_only: bool = True) -> bool:
    """desc= is a bool literal, or a list/tuple of bool literals, one per by= column.
    Without `literal_only` (API programs) an entry may also be an expression: a
    direction computed per request, typed by `_sort_direction`."""

    def is_bool(n: ast.expr) -> bool:
        if isinstance(n, ast.Constant) and isinstance(n.value, bool):
            return True
        return not literal_only and not isinstance(n, ast.Constant | ast.List | ast.Tuple)

    if is_bool(desc):
        return True
    if isinstance(desc, (ast.List, ast.Tuple)) and all(is_bool(e) for e in desc.elts):
        return by is None or len(desc.elts) == len(by)
    return False
