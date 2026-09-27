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
    EXPR_FUNCS,
    IO_OPS,
    NAMESPACES,
    OP_SHAPES,
    ORDER_AGGS,
    PUBLIC_ROLE,
    TABLE_OPS,
    TYPE_NAMES,
    USER_PARAM,
)
from lattice.check.api_info import (
    Decl,
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


def _cols(names: tuple[str, ...] | list[str]) -> str:
    return "(" + ", ".join(names) + ")"


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

    def _typer(self, schema: Schema, scope: _Scope) -> ExprTyper:
        return ExprTyper(
            self.ctx,
            schema,
            scope.scalars,
            self.silent,
            self.reported,
            consts=scope.consts,
            tables=scope.env,
        )

    def _assign(self, name: str, value: ast.expr, scope: _Scope) -> None:
        """One assignment: a named constant or a unit call. The first binding of a
        name wins (P2 reported any rebinding)."""
        if is_constant_expr(value):
            self._constant(name, value, scope)
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
                scope.nullconsts[name] = expr_nullable(value, frozenset(), scope.nullconsts)
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
        if name in NAMESPACES:
            return "namespace", "pass a table name here"
        if name in EXPR_FUNCS or name == "step":
            return "function", "pass a table name here"
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
                result = self._transfer(call, op, tables, kws, scope)
                if self.api is not None and isinstance(result, TableT) and tables:
                    result = self._api_meta(call, op, tables, result)
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
                return self._sort(tables[0], kws)
            case "limit":
                return self._limit(tables[0], kws["n"])
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
        pt = self._typer(t.schema, scope).type_of(pred)
        match pt:
            case ColT(scalar=Scalar.BOOL) | UnknownT():
                pass
            case LitT() if isinstance(pred, ast.Constant):
                pass  # P3 reported the constant predicate
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
        typer = self._typer(t.schema, scope)
        cols: list[tuple[str, Scalar]] = []
        nullable = set(t.nullable)
        unknown = False
        for kw in call.keywords:
            assert kw.arg is not None  # P1 rejects **kwargs
            vt = typer.type_of(kw.value)
            if isinstance(vt, UnknownT):
                unknown = True
            else:
                cols.append((kw.arg, vt.scalar))
                if expr_nullable(kw.value, t.nullable, scope.nullconsts):
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
                expr_nullable(e, t.nullable, scope.nullconsts)
                for e in [node.args[0], node.keywords[0].value]
            )
        if fn not in NULLS_AGGS:
            return False  # sum/count are never missing
        arg = node.args[0]
        if (
            not node.keywords
            and self.ctx.profile.enforce_nullable
            and expr_nullable(arg, t.nullable, scope.nullconsts)
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
        on = self._str_list(on_node, "on")
        if on is None:
            return UNKNOWN
        if not on:
            return self._cross_join(call, left, right, on_node)
        ok = self._require_columns(on, left.schema, on_node, "left")
        ok = self._require_columns(on, right.schema, on_node, "right") and ok
        ok = self._no_dups(on, on_node) and ok
        if not ok:
            return UNKNOWN
        for key in on:
            a, b = left.schema.get(key), right.schema.get(key)
            if a is not b:
                ok = False
                self.ctx.error(
                    "L402",
                    on_node,
                    f"join key '{key}' is {a.value if a else '?'} on the left "
                    f"and {b.value if b else '?'} on the right",
                    "convert one side with derive + fn.to_* first",
                )
        extra = [(n, s) for n, s in right.schema.columns if n not in on]
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
        unique = any(set(k) <= set(on) for k in right.keys)
        if not unique and fanout is None and self.ctx.profile.enforce_keys:
            r_name = call.args[1].id if isinstance(call.args[1], ast.Name) else "right"
            known = ", ".join(_cols(k) for k in right.keys) or "none"
            self.ctx.error(
                "L420",
                call,
                f"join can repeat rows of the left table: '{r_name}' is not unique on "
                f"{_cols(on)}; known keys of it: {known}",
                f"reduce it to one row per {_cols(on)} first, e.g. group({r_name}, by=[...], "
                '...), or pass fanout="allow" if repeating rows is intended',
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

    def _sort(self, t: TableT, kws: dict[str, ast.expr]) -> TableOrUnknown:
        by = self._str_list(kws["by"], "by")
        if by is not None:
            self._require_columns(by, t.schema, kws["by"])
        desc = kws.get("desc")
        if desc is not None and not _valid_sort_desc(desc, by):
            self.ctx.error(
                "L404",
                desc,
                "sort desc= must be True, False, or a list with one True/False per by= column",
                'e.g. desc=True, or by=["revenue", "id"], desc=[True, False] for revenue '
                "descending with ties by id ascending",
            )
        return t

    def _limit(self, t: TableT, n: ast.expr) -> TableOrUnknown:
        value = n.value if isinstance(n, ast.Constant) else None
        if not (isinstance(value, int) and not isinstance(value, bool) and value > 0):
            self.ctx.error("L417", n, "limit n= must be a positive integer literal", "e.g. n=10")
        if value == 1 and not isinstance(value, bool):
            return replace(t, at_most_one_row=True)
        return t

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
        for spec in params:
            scope.consts.pop(spec.name, None)
            scope.shapes.pop(spec.name, None)
            scope.nullconsts.pop(spec.name, None)
            if spec.scalar is None:
                scope.env[spec.name] = caller if caller is not None else UNKNOWN
            else:
                scope.scalars[spec.name] = spec.scalar
        public = isinstance(decl.auth, ast.Name) and decl.auth.id == PUBLIC_ROLE
        if not public and all(p.name != USER_PARAM for p in params):
            # a missing `user` parameter is P6's L801; type its uses anyway
            scope.env[USER_PARAM] = caller if caller is not None else UNKNOWN
        self.in_endpoint = True
        try:
            for stmt in fn.body:
                match stmt:
                    case ast.Assign(targets=[ast.Name(id=name)]):
                        self._assign(name, stmt.value, scope)
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
            case "owned":
                return self._owned(call, tables[0], kws["by"])
            case "one":
                self._else(kws["else_"])
                return replace(tables[0], at_most_one_row=True, paged=False)
            case "require":
                self._condition(call.args[1], tables[0], scope, "require()")
                self._else(kws["else_"])
                return tables[0]
            case "page":
                return replace(tables[0], paged=True)
            case "insert":
                return self._insert(call, scope)
            case "update":
                return self._update(call, tables[0], scope)
            case "delete":
                self._write_source(call, tables[0], "delete")
                return tables[0]
            case _:
                raise TypeError(f"no transfer function for API op '{op}'")

    def _condition(self, pred: ast.expr, t: TableT, scope: _Scope, what: str) -> None:
        pt = self._typer(t.schema, scope).type_of(pred)
        match pt:
            case ColT(scalar=Scalar.BOOL) | UnknownT():
                pass
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

    def _owned(self, call: ast.Call, t: TableT, by: ast.expr) -> TableOrUnknown:
        if not (isinstance(by, ast.Name) and by.id == USER_PARAM):
            if not self._silenced(by):
                self.ctx.error("L404", by, "owned() filters by the caller", "write by=user")
            return UNKNOWN
        if t.owner is None and t.owner_via is None:
            owned = sorted(n for n, s in self.schemas.items() if s.owner is not None or s.owner_via)
            self.ctx.error(
                "L404",
                call,
                "owned() needs rows of a table with an owner column; this table has none",
                "remove owned(): only " + (", ".join(owned) or "no tables") + " are per-user",
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
        # the owner column is P6's (L802 without owner=user; L404 for Admin)
        missing = [
            c
            for c in schema.names()
            if c not in (key, schema.owner) and c not in schema.nullable and c not in given
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
                f"{op} the result of fetch/where/owned/one on that table, not of a join, "
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
        for kw in call.keywords:
            assert kw.arg is not None
            if schema is not None and not self._require_columns([kw.arg], schema, kw):
                continue
            vt = typer.type_of(kw.value)
            want = schema.get(kw.arg) if schema is not None else None
            if not isinstance(vt, UnknownT) and want is not None:
                self._compatible(kw.value, vt.scalar, want, kw.arg)
        return t

    def _api_meta(self, call: ast.Call, op: str, inputs: list[TableT], out: TableT) -> TableT:
        """Carry the API metadata (owner, ownership, private columns, source, page)
        from an op's input tables to its result."""
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
        paged = False
        # an identity column (key / owner / owner_via) was given new values: the rows
        # no longer identify database rows, nor are they known to be the caller's
        rebound = False
        kws = [kw for kw in call.keywords if kw.arg is not None]
        match op:
            case "where" | "sort" | "limit":
                source = t.source
                if op == "where" and t.owned_from and len(call.args) > 1:
                    # filtering on anything but a plain stored column (a key, an owner
                    # column, a value derived from them, ...) may single out one owner
                    narrowed = narrowed or _reads_other(call.args[1], t.plain)
            case "select":
                source, paged = t.source, t.paged
            case "derive":
                overwritten = {kw.arg for kw in kws}
                private = (t.private - overwritten) | {
                    kw.arg for kw in kws if kw.arg and _reads(kw.value, t.private)
                }
                owner_cols = (t.owner_cols - overwritten) | {
                    kw.arg for kw in kws if kw.arg and _reads(kw.value, t.owner_cols)
                }
                plain = t.plain - overwritten
                owner = t.owner if t.owner not in overwritten else None
                via = t.owner_via if via is not None and via[0] not in overwritten else None
                paged = t.paged
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
                paged = t.paged
            case "group":
                by = set(_str_list_of(next((kw.value for kw in kws if kw.arg == "by"), None)))
                private = (t.private & names) | {
                    kw.arg for kw in kws if kw.arg != "by" and _reads(kw.value, t.private)
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
                on = _str_list_of(next((kw.value for kw in kws if kw.arg == "on"), None))
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
            plain=plain & names,
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
        table, is neither narrowed nor at most one row, every `by` column is plain (an
        unmodified stored column that is not a key, owner-identifying or private), and
        no aggregate reads an owner-identifying or private column."""
        if not t.owned_from:
            return False
        if t.narrowed or t.at_most_one_row:
            return True
        by_node = next((kw.value for kw in kws if kw.arg == "by"), None)
        if not set(_str_list_of(by_node)) <= t.plain:
            return True
        exposing = t.owner_cols | t.private
        return any(_reads(kw.value, exposing) for kw in kws if kw.arg != "by")


def _str_list_of(node: ast.expr | None) -> list[str]:
    """The string constants of a list/tuple literal (empty for anything else)."""
    if isinstance(node, ast.List | ast.Tuple):
        return [v for v in map(_str_constant, node.elts) if v is not None]
    return []


def _reads(node: ast.expr, columns: frozenset[str]) -> bool:
    """Whether an expression reads any of `columns` via col("...")."""
    return any(_col_name(n) in columns for n in ast.walk(node) if isinstance(n, ast.Call))


def _reads_other(node: ast.expr, columns: frozenset[str]) -> bool:
    """Whether an expression reads a column outside `columns`."""
    return any(
        name is not None and name not in columns
        for name in (_col_name(n) for n in ast.walk(node) if isinstance(n, ast.Call))
    )


def run(module: ast.Module, ctx: CheckContext) -> dict[str, StepT]:
    return _Types(module, ctx).run()


def _valid_sort_desc(desc: ast.expr, by: list[str] | None) -> bool:
    """desc= is a bool literal, or a list/tuple of bool literals, one per by= column."""

    def is_bool(n: ast.expr) -> bool:
        return isinstance(n, ast.Constant) and isinstance(n.value, bool)

    if is_bool(desc):
        return True
    if isinstance(desc, (ast.List, ast.Tuple)) and all(is_bool(e) for e in desc.elts):
        return by is None or len(desc.elts) == len(by)
    return False
