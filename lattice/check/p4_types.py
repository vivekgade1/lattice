"""P4 — types and schemas.

Walks the module in source order with an environment (table name -> TableT) that
is inherited down the statement list; each op's transfer function synthesizes
the result schema, which is recorded on the call node. Step definitions are typed
when reached and entered into the step table used by later calls.

An UNKNOWN input table (already reported) makes the op result UNKNOWN without
checking the op's expressions, so one mistake yields one diagnostic.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lattice.catalog import EXPR_FUNCS, NAMESPACES, OP_SHAPES, TABLE_OPS, TYPE_NAMES
from lattice.check.astutil import annotation_schema_name, redefined_steps, steps_of
from lattice.check.context import CheckContext
from lattice.check.exprs import ExprTyper, constant_scalar, did_you_mean, reported_name_spans
from lattice.diagnostics import span_of
from lattice.typesys import (
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
)

TableOrUnknown = TableT | UnknownT
Env = dict[str, TableOrUnknown]
Scalars = dict[str, Scalar]

JOIN_HOWS = ("inner", "left")


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
        self.schemas = ctx.profile.schemas
        self.step_defs = steps_of(module)
        self.redefined = redefined_steps(module)
        self.silent = TABLE_OPS | frozenset(self.step_defs)
        self.reported = reported_name_spans(ctx)
        self.sigs: dict[str, _Sig] = {}

    def run(self) -> dict[str, StepT]:
        env: Env = {}
        for stmt in self.module.body:
            match stmt:
                case ast.Assign(targets=[ast.Name(id=name)]):
                    env.setdefault(name, self._unit(stmt.value, env, {}) or UNKNOWN)
                case ast.Expr():
                    self._unit(stmt.value, env, {})
                case ast.FunctionDef():
                    self._step(stmt)
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

    def _typer(self, schema: Schema, scalars: Scalars) -> ExprTyper:
        return ExprTyper(self.ctx, schema, scalars, self.silent, self.reported)

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

    def _non_table_kind(self, name: str, scalars: Scalars) -> tuple[str, str] | None:
        """(kind, hint) if `name` is positively known to be something other than a table."""
        if name in scalars:
            return "scalar parameter", "pass a table name here"
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

    def _table_arg(self, arg: ast.expr, env: Env, scalars: Scalars) -> TableOrUnknown:
        if not isinstance(arg, ast.Name):
            return UNKNOWN  # not a name: P3 reported it
        t = env.get(arg.id)
        if t is not None:
            return t
        kind = self._non_table_kind(arg.id, scalars)
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

    def _unit(self, value: ast.expr, env: Env, scalars: Scalars) -> TableOrUnknown | None:
        """Type one statement's unit call; None for write (no result)."""
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)):
            return UNKNOWN  # P3 already reported the non-unit statement
        name = value.func.id
        if name in OP_SHAPES:
            return self._op(value, name, env, scalars)
        if name in self.step_defs:
            t = self._call_step(value, name, env, scalars)
            self.ctx.set_type(value, t)
            return t
        return UNKNOWN

    def _op(self, call: ast.Call, op: str, env: Env, scalars: Scalars) -> TableOrUnknown | None:
        shape = OP_SHAPES[op]
        kws = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        shape_ok = len(call.args) == len(shape.positional) and shape.required_kw <= kws.keys()
        if op == "write":
            if shape_ok:
                self._table_arg(call.args[0], env, scalars)
                self._write(call)
            return None
        result: TableOrUnknown = UNKNOWN
        if shape_ok:  # otherwise P3 reported the call shape
            inputs = [self._table_arg(a, env, scalars) for a in call.args[: shape.tables]]
            tables = [t for t in inputs if isinstance(t, TableT)]
            if len(tables) == len(inputs):
                result = self._transfer(call, op, tables, kws, scalars)
        self.ctx.set_type(call, result)
        return result

    def _transfer(
        self,
        call: ast.Call,
        op: str,
        tables: list[TableT],
        kws: dict[str, ast.expr],
        scalars: Scalars,
    ) -> TableOrUnknown:
        match op:
            case "load":
                return self._load(call, kws["schema"])
            case "where":
                return self._where(call, tables[0], scalars)
            case "derive":
                return self._derive(call, tables[0], scalars)
            case "select":
                return self._select(tables[0], kws["cols"])
            case "rename":
                return self._rename(call, tables[0])
            case "group":
                return self._group(call, tables[0], kws["by"], scalars)
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
                return TableT(self.schemas[name])
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

    def _where(self, call: ast.Call, t: TableT, scalars: Scalars) -> TableOrUnknown:
        pred = call.args[1]
        pt = self._typer(t.schema, scalars).type_of(pred)
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
        return t  # the predicate never changes the schema

    def _derive(self, call: ast.Call, t: TableT, scalars: Scalars) -> TableOrUnknown:
        # Every expression reads the INPUT table: a later keyword does not see an earlier
        # keyword's new value. An existing name is replaced in place (with the new type).
        typer = self._typer(t.schema, scalars)
        cols: list[tuple[str, Scalar]] = []
        unknown = False
        for kw in call.keywords:
            assert kw.arg is not None  # P1 rejects **kwargs
            vt = typer.type_of(kw.value)
            if isinstance(vt, UnknownT):
                unknown = True
            else:
                cols.append((kw.arg, vt.scalar))
        return UNKNOWN if unknown else TableT(t.schema.with_columns(cols))

    def _select(self, t: TableT, cols_node: ast.expr) -> TableOrUnknown:
        names = self._str_list(cols_node, "cols")
        if names is None or not self._require_columns(names, t.schema, cols_node):
            return UNKNOWN
        self._no_dups(names, cols_node)
        return TableT(t.schema.project(dict.fromkeys(names)))

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
        return TableT(t.schema.renamed(mapping)) if ok else UNKNOWN

    def _group(
        self, call: ast.Call, t: TableT, by_node: ast.expr, scalars: Scalars
    ) -> TableOrUnknown:
        by = self._str_list(by_node, "by")
        ok = by is not None and self._require_columns(by, t.schema, by_node)
        ok = ok and self._no_dups(by or [], by_node)
        typer = self._typer(t.schema, scalars)
        aggs: list[tuple[str, Scalar]] = []
        for kw in call.keywords:
            if kw.arg == "by" or kw.arg is None:
                continue
            at = typer.type_agg(kw.value)
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
        return TableT(t.schema.project(by).extend(aggs))

    def _join(
        self, call: ast.Call, left: TableT, right: TableT, kws: dict[str, ast.expr]
    ) -> TableOrUnknown:
        how = kws.get("how")
        if how is not None and _str_constant(how) not in JOIN_HOWS:
            self.ctx.error("L404", how, 'join how= must be "inner" or "left"', 'e.g. how="left"')
        on_node = kws["on"]
        on = self._str_list(on_node, "on")
        if on is None:
            return UNKNOWN
        if not on:
            self.ctx.error("L404", on_node, "join needs at least one key column", None)
            return UNKNOWN
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
        return TableT(left.schema.extend(extra)) if ok else UNKNOWN

    def _sort(self, t: TableT, kws: dict[str, ast.expr]) -> TableOrUnknown:
        by = self._str_list(kws["by"], "by")
        if by is not None:
            self._require_columns(by, t.schema, kws["by"])
        desc = kws.get("desc")
        if desc is not None and not (
            isinstance(desc, ast.Constant) and isinstance(desc.value, bool)
        ):
            self.ctx.error("L404", desc, "sort desc= must be True or False", None)
        return t

    def _limit(self, t: TableT, n: ast.expr) -> TableOrUnknown:
        value = n.value if isinstance(n, ast.Constant) else None
        if not (isinstance(value, int) and not isinstance(value, bool) and value > 0):
            self.ctx.error("L417", n, "limit n= must be a positive integer literal", "e.g. n=10")
        return t

    def _write(self, call: ast.Call) -> None:
        if _str_constant(call.args[1]) is None:
            self.ctx.error(
                "L404", call.args[1], "write() path must be a string literal", 'e.g. "out.parquet"'
            )

    # ---- steps ----------------------------------------------------------------

    def _step(self, fn: ast.FunctionDef) -> None:
        params: list[tuple[str, TableT | ScalarParamT | UnknownT]] = []
        env: Env = {}
        scalars: Scalars = {}
        for a in fn.args.args:
            ann = a.annotation
            if isinstance(ann, ast.Name) and ann.id in SCALAR_NAMES:
                scalars[a.arg] = SCALAR_NAMES[ann.id]
                params.append((a.arg, ScalarParamT(scalars[a.arg])))
                continue
            pt = self._table_annotation(ann, a)
            env[a.arg] = pt
            params.append((a.arg, pt))
        result: TableOrUnknown = UNKNOWN
        for stmt in fn.body:
            match stmt:
                case ast.Assign(targets=[ast.Name(id=name)]):
                    env.setdefault(name, self._unit(stmt.value, env, scalars) or UNKNOWN)
                case ast.Return(value=ast.Name(id=name) as ret):
                    result = env.get(name, UNKNOWN)
                    kind = None if name in env else self._non_table_kind(name, scalars)
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
        if isinstance(inferred, TableT) and inferred != declared:
            self.ctx.error(
                "L605",
                fn.returns,  # type: ignore[arg-type]
                f"step '{fn.name}' returns {inferred.schema.render()} "
                f"but declares {declared.schema.render()}",
                "fix the body or change the return annotation (bare Table infers it)",
            )
        return declared  # callers see the declared schema: no cascade past the step

    def _call_step(self, call: ast.Call, name: str, env: Env, scalars: Scalars) -> TableOrUnknown:
        sig = self.sigs.get(name)
        if sig is None or name in self.redefined:
            return UNKNOWN  # defined later / recursive / redefined: P2 reported it
        tables = [(p, t) for p, t in sig.params if not isinstance(t, ScalarParamT)]
        scalar_params = {p: t.scalar for p, t in sig.params if isinstance(t, ScalarParamT)}
        if len(call.args) != len(tables):
            return UNKNOWN  # P3 reported the call shape
        for arg, (p, pt) in zip(call.args, tables, strict=True):
            at = self._table_arg(arg, env, scalars)
            if isinstance(at, TableT) and isinstance(pt, TableT) and at != pt:
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
                self._scalar_arg(name, kw, want, scalars)
        return sig.result

    def _scalar_arg(self, step: str, kw: ast.keyword, want: Scalar, scalars: Scalars) -> None:
        value = kw.value
        got = constant_scalar(value)
        if got is None and isinstance(value, ast.Name):
            got = scalars.get(value.id)
            if got is None and self._silenced(value):
                return
        if got is None:
            self.ctx.error(
                "L402",
                value,
                f"step '{step}' parameter '{kw.arg}' expects a {want.value} literal "
                "or scalar parameter",
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


def run(module: ast.Module, ctx: CheckContext) -> dict[str, StepT]:
    return _Types(module, ctx).run()
