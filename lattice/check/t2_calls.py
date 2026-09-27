"""Procedures called from endpoints (spec 1c §3, node T2b): the T0 side of the T0/T2
boundary.

* Scalar calls -- `proc(col("a"), user, fn.now(), row())` as the whole value of a
  derive column, the whole where predicate or the whole require condition -- are typed
  by `ExprTyper` through `scalar_call`. They run per row in the app layer, after the
  read's one statement (the plan marks the read "T2-post").
* Whole-value calls -- `x = call(proc, rows=t, n=limit)` -- are typed by `whole_call`
  for P4: table arguments are `list[Row[T]]` / `list[Record]` parameters, the others
  are values (parameters, constants, user, fn.now()). The value is a table (a list of
  records or rows, or one of them) or a scalar (a name usable like a parameter).

Values cross the boundary only as T0 column types (Int, Float, Str, Bool, Date,
DateTime, Uuid, optionally `| None`), whole rows (`Row[T]`, a record) and the caller.
L881: a call nested in an expression or an argument that needs SQL; L882: effects;
L883: a value T0 cannot carry (or a nullable one where the procedure takes a plain
value); L402/L404: argument types and shapes; L803: private columns."""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lattice.catalog import IS_ADMIN, ROW_FN, USER_PARAM
from lattice.check.nullability import expr_nullable
from lattice.check.t2 import types as T
from lattice.check.t2.model import CheckedProcedure, T2Program
from lattice.profile import ApiProfile
from lattice.typesys import (
    UNKNOWN,
    ColT,
    LitT,
    Scalar,
    Schema,
    TableT,
    UnknownT,
)

if TYPE_CHECKING:
    from lattice.check.context import CheckContext
    from lattice.check.exprs import ExprT, ExprTyper

_SCALARS = frozenset(s.value for s in Scalar)
_FORMS = "a column, an endpoint parameter, a constant, user, user.<column>, fn.now() or row()"


def column_type(t: T.T2Type) -> tuple[Scalar, bool] | None:
    """(scalar, nullable) when a T2 type is a T0 column value, else None."""
    inner = T.strip_opt(t)
    if isinstance(inner, T.Prim) and inner.name in _SCALARS:
        return Scalar(inner.name), isinstance(t, T.Opt)
    return None


def fits(got: Scalar, want: Scalar) -> bool:
    return got is want or (want is Scalar.FLOAT and got is Scalar.INT)


def is_proc_call(node: ast.AST, t2: T2Program | None) -> bool:
    return (
        t2 is not None
        and isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in t2.procedures
    )


def is_row_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == ROW_FN
        and not node.args
        and not node.keywords
    )


def value_form(node: ast.expr, *, columns: bool) -> bool:
    """An argument T0 can hand over as-is: a column (`columns`), a name (parameter or
    constant), a literal, fn.now(), a caller reference."""
    match node:
        case ast.Call(func=ast.Name(id="col"), args=[ast.Constant(value=str())], keywords=[]):
            return columns
        case ast.Name() | ast.Constant():
            return True
        case ast.UnaryOp(op=ast.USub(), operand=ast.Constant()):
            return True
        case ast.Call(
            func=ast.Attribute(value=ast.Name(id="fn"), attr="now"), args=[], keywords=[]
        ):
            return True
        case ast.Attribute(value=ast.Name(id=name)) if name == USER_PARAM:
            return True
        case ast.Call(func=ast.Name(id=name), args=[ast.Name(id=u)], keywords=[]) if (
            name == IS_ADMIN and u == USER_PARAM
        ):
            return True
    return False


def bind_args(
    node: ast.Call, names: list[str], label: str, *, positional: bool
) -> tuple[list[tuple[str, ast.expr]] | None, str | None]:
    """(parameter, argument) pairs, or (None, problem). Every parameter is required."""
    args = list(node.args) if positional else []
    if len(args) > len(names):
        return None, f"{label} takes {len(names)} argument(s), got {len(args)}"
    pairs = list(zip(names, args, strict=False))
    given = {n for n, _ in pairs}
    for kw in node.keywords:
        if kw.arg is None or kw.arg not in names:
            return None, f"{label} has no parameter '{kw.arg}'"
        if kw.arg in given:
            return None, f"{label} got '{kw.arg}' twice"
        given.add(kw.arg)
        pairs.append((kw.arg, kw.value))
    missing = [n for n in names if n not in given]
    if missing:
        return None, f"{label} is missing {', '.join(missing)}"
    return pairs, None


@dataclass(frozen=True)
class RowShape:
    """Where a whole row comes from: the current table's columns, nullability and
    private columns (the rows of a table argument, or the row of a scalar call)."""

    schema: Schema
    nullable: frozenset[str]
    private: frozenset[str]


def row_problems(
    want: T.T2Type,
    shape: RowShape,
    t2: T2Program,
    api: ApiProfile,
    admin: bool,
) -> list[tuple[str, str, str | None]]:
    """(code, message, hint) for handing rows of `shape` over as `want` (a `Row[T]` or a
    record type)."""
    out: list[tuple[str, str, str | None]] = []
    if isinstance(want, T.RowT):
        table = api.tables[want.table]
        visible = [c for c in table.names() if want.private or c not in table.private]
        lacking = [c for c in visible if shape.schema.get(c) is not table.get(c)]
        if lacking:
            out.append(
                (
                    "L883",
                    f"{want} needs the column(s) {', '.join(lacking)} of {want.table}, "
                    f"which these rows lack or type differently",
                    "pass rows that keep every column of the table (derive/select after "
                    "the call), or take a @record with the fields the procedure needs",
                )
            )
        loose = sorted(c for c in visible if c in shape.nullable and c not in table.nullable)
        if loose:
            out.append(
                (
                    "L883",
                    f"column(s) {', '.join(loose)} may be null here, but {want} says they are not",
                    "take a @record whose fields are '| None'",
                )
            )
        if want.private and not admin:
            out.append(
                (
                    "L803",
                    f"{want} hands private columns of {want.table} to a procedure",
                    "only Admin endpoints may; take Row[T] (private columns excluded)",
                )
            )
        return out
    assert isinstance(want, T.RecordT)
    rec = t2.records[want.name]
    for field_name, ft in rec.fields:
        ct = column_type(ft)
        have = shape.schema.get(field_name)
        if ct is None:
            out.append(
                (
                    "L883",
                    f"record {rec.name} field '{field_name}' is {ft}: rows carry only column "
                    "values",
                    "give the record column-typed fields",
                )
            )
        elif have is None:
            out.append(
                (
                    "L883",
                    f"record {rec.name} needs column '{field_name}', which these rows lack",
                    f"columns: {', '.join(shape.schema.names())}",
                )
            )
        elif not fits(have, ct[0]):
            out.append(
                (
                    "L402",
                    f"record {rec.name} field '{field_name}' is {ft}, the column is {have.value}",
                    None,
                )
            )
        elif field_name in shape.nullable and not ct[1]:
            out.append(
                (
                    "L883",
                    f"column '{field_name}' may be null, but record {rec.name} field "
                    f"'{field_name}' is {ft}",
                    f"declare it '{field_name}: {ft} | None'",
                )
            )
        if field_name in shape.private and not admin:
            out.append(
                (
                    "L803",
                    f"record {rec.name} field '{field_name}' is a private column",
                    "only Admin endpoints hand private columns to a procedure",
                )
            )
    return out


# ---- scalar calls (ExprTyper) ----------------------------------------------------------------


def scalar_call(typer: ExprTyper, node: ast.Call, name: str) -> ExprT:
    """`proc(args...)` in a derive / where / require expression."""
    t2 = typer.t2
    assert t2 is not None
    ctx = typer.ctx
    proc = t2.procedures[name]
    if not typer.endpoint:
        return typer.fail(
            "L881",
            node,
            f"procedure '{name}' is called from endpoint bodies only",
            "call it in the endpoint that calls this step",
        )
    if typer.whole != id(node):
        return typer.fail(
            "L881",
            node,
            f"a procedure call must be the whole value of a derive column, a where "
            f"predicate or a require condition; here '{name}(...)' is part of an expression",
            "compute the whole value in the procedure, e.g. derive(t, x=f(...)), or derive "
            "its result first and use the column",
        )
    if proc.effects:
        return typer.fail(
            "L882",
            node,
            f"procedure '{name}' has effects ({', '.join(sorted(proc.effects))}); it cannot "
            "be called in an expression",
            f"send it once per row after the writes commit: for_each(rows, {name}, ...)",
        )
    result = column_type(proc.sig.returns)
    if result is None:
        return typer.fail(
            "L883",
            node,
            f"procedure '{name}' returns {proc.sig.returns}, which is no column type",
            "return Int, Float, Str, Bool, Date, DateTime or Uuid (or '| None'), or call "
            f"it whole-value: x = call({name}, ...)",
        )
    pairs, problem = bind_args(node, [p for p, _ in proc.sig.params], f"'{name}'", positional=True)
    if pairs is None:
        return typer.fail("L404", node, problem or "", f"signature: {_signature(proc)}")
    ok = True
    want = dict(proc.sig.params)
    for pname, arg in pairs:
        ok = _scalar_arg(typer, proc, pname, want[pname], arg) and ok
    if not ok:
        return UNKNOWN
    ctx.set_type(node.func, UNKNOWN)
    return ColT(result[0])


def _scalar_arg(
    typer: ExprTyper, proc: CheckedProcedure, pname: str, want: T.T2Type, arg: ast.expr
) -> bool:
    what = f"'{proc.name}' parameter '{pname}'"
    t2 = typer.t2
    api = typer.ctx.profile.api
    assert t2 is not None and api is not None
    if want == T.CALLER:
        if isinstance(arg, ast.Name) and arg.id == USER_PARAM and typer.caller is not None:
            return True
        typer.fail("L883", arg, f"{what} is the Caller: pass user", "e.g. f(user)")
        return False
    if isinstance(want, T.RowT | T.RecordT):
        if not is_row_call(arg):
            typer.fail("L883", arg, f"{what} is {want}: pass the whole row, row()", None)
            return False
        shape = RowShape(typer.schema, typer.nullable, typer.private)
        problems = row_problems(want, shape, t2, api, typer.admin)
        for code, message, hint in problems:
            typer.fail(code, arg, message, hint)
        return not problems
    ct = column_type(want)
    if ct is None:
        typer.fail(
            "L883",
            arg,
            f"{what} is {want}; a scalar call passes column values",
            f"call it whole-value: x = call({proc.name}, ...)",
        )
        return False
    if not value_form(arg, columns=True):
        typer.fail("L881", arg, f"a procedure argument must be {_FORMS}", _derive_first(pname))
        return False
    if _computed_constant(arg, typer.consts, typer.scalars, typer.ctx):
        return False
    got = typer.type_of(arg)
    if isinstance(got, UnknownT):
        return False
    if not fits(got.scalar, ct[0]):
        typer.fail("L402", arg, f"{what} is {want}, got {got.scalar.value}", None)
        return False
    nullable = expr_nullable(arg, typer.nullable, typer.nullconsts)
    if nullable and not ct[1]:
        typer.fail(
            "L883",
            arg,
            f"{what} is {want}, but this value may be null (None)",
            f"declare the parameter '{pname}: {want} | None', or pass coalesce()d values "
            "(derive them first)",
        )
        return False
    return True


def _computed_constant(
    arg: ast.expr, consts: Mapping[str, object], scalars: Mapping[str, Scalar], ctx: CheckContext
) -> bool:
    """L881: a named constant whose value is computed (not a literal) exists only in SQL
    and cannot be handed to a procedure. (An ill-typed constant was already reported.)"""
    if not (isinstance(arg, ast.Name) and arg.id in consts and consts[arg.id] is None):
        return False
    if arg.id in scalars:
        ctx.error(
            "L881",
            arg,
            f"constant '{arg.id}' is computed in SQL; a procedure takes literal constants",
            f"give '{arg.id}' a literal value, or compute it in the procedure",
        )
    return True


def _derive_first(pname: str) -> str:
    return f'derive the value first (derive(t, {pname}=...)) and pass col("{pname}")'


def _signature(proc: CheckedProcedure) -> str:
    params = ", ".join(f"{p}: {t}" for p, t in proc.sig.params)
    return f"{proc.name}({params}) -> {proc.sig.returns}"


# ---- whole-value calls (P4) ----------------------------------------------------------------


@dataclass(frozen=True)
class ScalarValue:
    """A whole-value call's scalar result: the name is then a value like a parameter."""

    scalar: Scalar
    nullable: bool


CallValue = TableT | ScalarValue | UnknownT


def whole_call(
    call: ast.Call,
    ctx: CheckContext,
    env: Mapping[str, object],
    typer: Callable[[], ExprTyper],
    admin: bool,
    in_endpoint: bool,
) -> CallValue:
    """`call(proc, name=value, ...)`: its table arguments, values and result."""
    t2 = ctx.t2
    api = ctx.profile.api
    assert t2 is not None and api is not None
    if not in_endpoint:
        ctx.error("L881", call, "call() is only allowed in an endpoint body", None)
        return UNKNOWN
    target = call.args[0] if call.args else None
    if not (isinstance(target, ast.Name) and target.id in t2.procedures):
        shown = ast.unparse(target) if target is not None else "nothing"
        ctx.error(
            "L404",
            target or call,
            f"call() takes a procedure first, not {shown}",
            "e.g. lines = call(digest, rows=items)",
        )
        return UNKNOWN
    proc = t2.procedures[target.id]
    if proc.effects:
        ctx.error(
            "L882",
            call,
            f"procedure '{proc.name}' has effects ({', '.join(sorted(proc.effects))}); "
            "call() computes a value",
            f"send it after the writes commit: for_each(rows, {proc.name}, ...)",
        )
        return UNKNOWN
    names = [p for p, _ in proc.sig.params]
    pairs, problem = bind_args(call, names, f"'{proc.name}'", positional=False)
    if pairs is None:
        ctx.error("L404", call, problem or "", f"pass every parameter by name: {_signature(proc)}")
        return UNKNOWN
    ok = True
    want = dict(proc.sig.params)
    for pname, arg in pairs:
        ok = _whole_arg(ctx, t2, api, proc, pname, want[pname], arg, env, typer, admin) and ok
    value = _result(ctx, t2, api, proc, call)
    return value if ok else UNKNOWN


def _whole_arg(
    ctx: CheckContext,
    t2: T2Program,
    api: ApiProfile,
    proc: CheckedProcedure,
    pname: str,
    want: T.T2Type,
    arg: ast.expr,
    env: Mapping[str, object],
    typer: Callable[[], ExprTyper],
    admin: bool,
) -> bool:
    what = f"'{proc.name}' parameter '{pname}'"
    if isinstance(want, T.ListT) and isinstance(want.elem, T.RowT | T.RecordT):
        t = env.get(arg.id) if isinstance(arg, ast.Name) else None
        if not isinstance(t, TableT):
            if isinstance(t, UnknownT):
                return False
            ctx.error("L883", arg, f"{what} is {want}: pass a table name", None)
            return False
        shape = RowShape(t.schema, t.nullable, t.private)
        problems = row_problems(want.elem, shape, t2, api, admin)
        for code, message, hint in problems:
            ctx.error(code, arg, message, hint)
        return not problems
    if want == T.CALLER:
        if isinstance(arg, ast.Name) and arg.id == USER_PARAM and USER_PARAM in env:
            return True
        ctx.error("L883", arg, f"{what} is the Caller: pass user", None)
        return False
    ct = column_type(want)
    if ct is None:
        ctx.error(
            "L883",
            arg,
            f"{what} is {want}, which an endpoint cannot pass",
            "take list[Row[T]] or list[<record>] for rows, or column-typed values",
        )
        return False
    if not value_form(arg, columns=False):
        ctx.error(
            "L881",
            arg,
            "a call() argument must be a table name, an endpoint parameter, a constant, "
            "user, user.<column> or fn.now()",
            "compute other values in the procedure",
        )
        return False
    t = env.get(arg.id) if isinstance(arg, ast.Name) else None
    if isinstance(t, TableT):
        ctx.error("L883", arg, f"{what} is {want}; '{arg.id}' is a table", None)
        return False
    tp = typer()
    if _computed_constant(arg, tp.consts, tp.scalars, ctx):
        return False
    got = tp.type_of(arg)
    if isinstance(got, UnknownT):
        return False
    if not isinstance(got, LitT | ColT) or not fits(got.scalar, ct[0]):
        ctx.error("L402", arg, f"{what} is {want}, got {got.scalar.value}", None)
        return False
    if expr_nullable(arg, frozenset(), tp.nullconsts) and not ct[1]:
        ctx.error(
            "L883",
            arg,
            f"{what} is {want}, but this value may be null (None)",
            f"declare the parameter '{pname}: {want} | None'",
        )
        return False
    return True


def _result(
    ctx: CheckContext, t2: T2Program, api: ApiProfile, proc: CheckedProcedure, call: ast.Call
) -> CallValue:
    ret = proc.sig.returns
    ct = column_type(ret)
    if ct is not None:
        return ScalarValue(*ct)
    many = isinstance(ret, T.ListT)
    item = ret.elem if isinstance(ret, T.ListT) else ret
    if isinstance(item, T.RecordT):
        rec = t2.records[item.name]
        cols: list[tuple[str, Scalar]] = []
        nullable: set[str] = set()
        for f, ft in rec.fields:
            fct = column_type(ft)
            if fct is None:
                ctx.error(
                    "L883",
                    call,
                    f"'{proc.name}' returns {ret}; record {rec.name} field '{f}' is {ft}, "
                    "which no column holds",
                    "give the record column-typed fields",
                )
                return UNKNOWN
            cols.append((f, fct[0]))
            if fct[1]:
                nullable.add(f)
        schema = Schema.from_pairs(cols, nullable=sorted(nullable))
        return _value_table(schema, frozenset(nullable), frozenset(), many)
    if isinstance(item, T.RowT):
        table = api.tables[item.table]
        names = [c for c in table.names() if item.private or c not in table.private]
        schema = table.project(dict.fromkeys(names))
        private = table.private & frozenset(names)
        return _value_table(schema, table.nullable & frozenset(names), private, many)
    ctx.error(
        "L883",
        call,
        f"'{proc.name}' returns {ret}, which an endpoint cannot use",
        "return a column value, a record or Row[T], or a list of records or rows",
    )
    return UNKNOWN


def _value_table(
    schema: Schema, nullable: frozenset[str], private: frozenset[str], many: bool
) -> TableT:
    return TableT(
        schema,
        at_most_one_row=not many,
        nullable=nullable,
        private=private,
        t2_rows="value",
        bounded=True,
    )
