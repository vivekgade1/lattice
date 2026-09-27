"""Compile checked column-expression ASTs into polars expressions (never eval/exec).

Operations that can fail on a row (division by zero, math-domain errors, failed
casts) yield null for that row; each such site contributes a *guard*: a Boolean
expression that is true on exactly the rows where the null was produced by the
guard, so the executor can count them and emit a runtime warning (spec §5.7).
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import polars as pl

from lattice.catalog import DATE_ADD_DAYS_MAX, ROUND_DIGITS_MAX
from lattice.check.astutil import literal_value
from lattice.runtime import LatticeExprError, LatticeInternalError
from lattice.typesys import Scalar

DTYPES: Mapping[Scalar, pl.DataType] = {
    Scalar.INT: pl.Int64(),
    Scalar.FLOAT: pl.Float64(),
    Scalar.STR: pl.String(),
    Scalar.BOOL: pl.Boolean(),
    Scalar.DATE: pl.Date(),
}

Guard = tuple[str, pl.Expr]


@dataclass
class Compiled:
    expr: pl.Expr
    guards: list[Guard] = field(default_factory=list)


F64 = pl.Float64


def _only_when(cond: pl.Expr, guards: list[Guard]) -> list[Guard]:
    """Restrict guards to the rows where `cond` holds."""
    return [(code, cond & g) for code, g in guards]


def literal_only(node: ast.expr) -> bool:
    """True if the expression references no column (it is constant across rows)."""
    return not any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "col"
        for n in ast.walk(node)
    )


class _Compiler:
    def __init__(self) -> None:
        self.guards: list[Guard] = []

    def sub(self, node: ast.expr) -> tuple[pl.Expr, list[Guard]]:
        """Compile a sub-expression, returning its guards separately."""
        inner = _Compiler()
        expr = inner.emit(node)
        return expr, inner.guards

    def emit(self, node: ast.expr) -> pl.Expr:
        if isinstance(node, ast.UnaryOp):
            folded = literal_value(node)  # fold -1, --2.0, -9223372036854775808 to a literal
            if folded is not None:
                node = ast.Constant(folded)
        match node:
            case ast.Constant(value=bool(v)):
                return pl.lit(v, dtype=pl.Boolean)
            case ast.Constant(value=int(v)):
                return pl.lit(v, dtype=pl.Int64)
            case ast.Constant(value=float(v)):
                return pl.lit(v, dtype=pl.Float64)
            case ast.Constant(value=str(v)):
                return pl.lit(v, dtype=pl.String)
            case ast.Call(func=ast.Name(id="col"), args=[ast.Constant(value=str(name))]):
                return pl.col(name)
            case ast.Call(func=ast.Name(id="when"), args=[cond], keywords=kws):
                return self._when(cond, {kw.arg: kw.value for kw in kws})
            case ast.Call(func=ast.Name(id="coalesce"), args=[a, b]):
                ea = self.emit(a)
                eb, gb = self.sub(b)
                self.guards += _only_when(ea.is_null(), gb)
                return pl.coalesce(ea, eb)
            case ast.Call(func=ast.Name(id="is_null"), args=[a]):
                return self.emit(a).is_null()
            case ast.Call(func=ast.Attribute(value=ast.Name(id="fn"), attr=name)):
                return self._fn(name, node)
            case ast.BinOp(left=left, op=op, right=right):
                return self._binop(self.emit(left), op, self.emit(right))
            case ast.Compare(left=left, ops=[op], comparators=[right]):
                return self._compare(self.emit(left), op, self.emit(right))
            case ast.UnaryOp(op=ast.USub(), operand=operand):
                return -self.emit(operand)
            case ast.UnaryOp(op=ast.Invert(), operand=operand):
                return ~self.emit(operand)
            case _:
                raise LatticeInternalError(f"cannot compile expression {ast.dump(node)}")

    # ---- structure ------------------------------------------------------------

    def _when(self, cond: ast.expr, kws: Mapping[str | None, ast.expr]) -> pl.Expr:
        if set(kws) != {"then", "otherwise"}:
            raise LatticeInternalError("when() without then=/otherwise=")
        c = self.emit(cond)
        then, g_then = self.sub(kws["then"])
        other, g_other = self.sub(kws["otherwise"])
        taken = c.fill_null(False)
        self.guards += _only_when(taken, g_then)
        self.guards += _only_when(~taken, g_other)
        return pl.when(c).then(then).otherwise(other)

    def _binop(self, left: pl.Expr, op: ast.operator, right: pl.Expr) -> pl.Expr:
        match op:
            case ast.Add():
                return left + right
            case ast.Sub():
                return left - right
            case ast.Mult():
                return left * right
            case ast.Div():
                zero = right == 0
                self.guards.append(("R301", zero))
                return pl.when(zero).then(None).otherwise(left.cast(F64) / right)
            case ast.Mod():
                zero = right == 0
                self.guards.append(("R301", zero))
                return pl.when(zero).then(None).otherwise(left % right)
            case ast.BitAnd():
                return left & right
            case ast.BitOr():
                return left | right
            case _:
                raise LatticeInternalError(f"unsupported operator {type(op).__name__}")

    @staticmethod
    def _compare(left: pl.Expr, op: ast.cmpop, right: pl.Expr) -> pl.Expr:
        match op:
            case ast.Eq():
                return left == right
            case ast.NotEq():
                return left != right
            case ast.Lt():
                return left < right
            case ast.LtE():
                return left <= right
            case ast.Gt():
                return left > right
            case ast.GtE():
                return left >= right
            case _:
                raise LatticeInternalError(f"unsupported comparison {type(op).__name__}")

    # ---- fn library -------------------------------------------------------------

    def _fn(self, name: str, node: ast.Call) -> pl.Expr:
        args = [self.emit(a) for a in node.args]
        kw = {k.arg: _literal(k.value) for k in node.keywords if k.arg is not None}
        builder = _FN.get(name)
        if builder is None:
            raise LatticeInternalError(f"no runtime implementation for fn.{name}")
        return builder(self, args, kw)

    def guarded(self, code: str, ok: pl.Expr, value: pl.Expr) -> pl.Expr:
        """`value` where `ok` holds, else null; rows with ok == False are counted."""
        self.guards.append((code, ~ok))
        return pl.when(ok).then(value).otherwise(None)

    def cast_checked(self, x: pl.Expr, result: pl.Expr) -> pl.Expr:
        self.guards.append(("R303", x.is_not_null() & result.is_null()))
        return result


def _date_add_days(node: ast.expr) -> int:
    days = _int(node)
    if abs(days) > DATE_ADD_DAYS_MAX:
        raise LatticeExprError(
            "R103", f"fn.date_add days= must be within ±{DATE_ADD_DAYS_MAX}, got {days}"
        )
    return days


def _literal(node: ast.expr) -> object:
    """Python value of a literal keyword argument (checked to be a literal)."""
    v = literal_value(node)
    if v is None:
        raise LatticeInternalError(f"keyword argument is not a literal: {ast.dump(node)}")
    return v


Args = list[pl.Expr]
Kw = Mapping[str | None, object]
_Builder = Callable[[_Compiler, Args, Kw], pl.Expr]


def _one(args: Args) -> pl.Expr:
    if len(args) != 1:
        raise LatticeInternalError("expected one argument")
    return args[0]


def _two(args: Args) -> tuple[pl.Expr, pl.Expr]:
    if len(args) != 2:
        raise LatticeInternalError("expected two arguments")
    return args[0], args[1]


def _log(c: _Compiler, a: Args, k: Kw) -> pl.Expr:
    x = _one(a)
    return c.guarded("R302", x > 0, x.cast(F64).log())


def _sqrt(c: _Compiler, a: Args, k: Kw) -> pl.Expr:
    x = _one(a)
    return c.guarded("R302", x >= 0, x.cast(F64).sqrt())


def _to_date(c: _Compiler, a: Args, k: Kw) -> pl.Expr:
    x = _one(a)
    return c.cast_checked(x, x.str.to_date(str(k["format"]), strict=False))


def _to_int(c: _Compiler, a: Args, k: Kw) -> pl.Expr:
    x = _one(a)
    return c.cast_checked(x, x.cast(pl.Int64, strict=False))


def _to_float(c: _Compiler, a: Args, k: Kw) -> pl.Expr:
    x = _one(a)
    return c.cast_checked(x, x.cast(pl.Float64, strict=False))


def _round(c: _Compiler, a: Args, k: Kw) -> pl.Expr:
    digits = _int(k["digits"])
    if not 0 <= digits <= ROUND_DIGITS_MAX:
        raise LatticeExprError(
            "R103", f"fn.round digits= must be between 0 and {ROUND_DIGITS_MAX}, got {digits}"
        )
    return _one(a).round(digits)


def _clip(c: _Compiler, a: Args, k: Kw) -> pl.Expr:
    lo, hi = _num(k["lo"]), _num(k["hi"])
    x = _one(a)
    if isinstance(lo, float) or isinstance(hi, float):
        return x.cast(F64).clip(float(lo), float(hi))  # a Float bound makes the result Float
    return x.clip(lo, hi)


def _num(v: object) -> int | float:
    if isinstance(v, int | float) and not isinstance(v, bool):
        return v
    raise LatticeInternalError(f"expected a number, got {v!r}")


def _int(v: object) -> int:
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    raise LatticeInternalError(f"expected an int, got {v!r}")


_FN: Mapping[str, _Builder] = {
    "abs": lambda c, a, k: _one(a).abs(),
    "round": _round,
    "floor": lambda c, a, k: _one(a).floor(),
    "ceil": lambda c, a, k: _one(a).ceil(),
    "log": _log,
    "sqrt": _sqrt,
    "clip": _clip,
    "lower": lambda c, a, k: _one(a).str.to_lowercase(),
    "upper": lambda c, a, k: _one(a).str.to_uppercase(),
    "strip": lambda c, a, k: _one(a).str.strip_chars(),
    "len": lambda c, a, k: _one(a).str.len_chars().cast(pl.Int64),
    "contains": lambda c, a, k: _one(a).str.contains(str(k["sub"]), literal=True),
    "starts_with": lambda c, a, k: _one(a).str.starts_with(str(k["prefix"])),
    "replace": lambda c, a, k: _one(a).str.replace_all(str(k["old"]), str(k["new"]), literal=True),
    "concat": lambda c, a, k: pl.concat_str(list(_two(a))),
    "year": lambda c, a, k: _one(a).dt.year().cast(pl.Int64),
    "month": lambda c, a, k: _one(a).dt.month().cast(pl.Int64),
    "day": lambda c, a, k: _one(a).dt.day().cast(pl.Int64),
    "date_diff": lambda c, a, k: (_two(a)[0] - _two(a)[1]).dt.total_days().cast(pl.Int64),
    "date_add": lambda c, a, k: _one(a) + pl.duration(days=_date_add_days(k["days"])),
    "to_date": _to_date,
    "to_int": _to_int,
    "to_float": _to_float,
    "to_str": lambda c, a, k: _one(a).cast(pl.String),
}


def compile_expr(node: ast.expr) -> Compiled:
    c = _Compiler()
    expr = c.emit(node)
    return Compiled(expr, c.guards)


def _per_row(node: ast.expr) -> Compiled:
    """Compile an aggregate's operand; a literal-only one is broadcast to every row."""
    c = compile_expr(node)
    return Compiled(pl.repeat(c.expr, pl.len()), c.guards) if literal_only(node) else c


def compile_agg(func: str, arg: ast.expr | None, order: ast.expr | None = None) -> Compiled:
    """An aggregate over a group (or the whole frame). Literal-only arguments are
    broadcast to one value per row first, so agg.sum(1) counts rows like SQL.
    agg.count() counts rows; agg.count(x) counts the non-null values of x.
    agg.at_min/agg.at_max(x, of=o) take x on the row with the smallest/largest
    non-null o (ties: the earliest row); null if every o is null."""
    if func == "count" and arg is None:
        return Compiled(pl.len().cast(pl.Int64))
    if arg is None:
        raise LatticeInternalError(f"agg.{func} needs an argument")
    inner = _per_row(arg)
    x = inner.expr
    if func in ("at_min", "at_max"):
        if order is None:
            raise LatticeInternalError(f"agg.{func} needs of=")
        o = _per_row(order)
        present = o.expr.is_not_null()
        # a stable sort keeps tied rows in input order, so .first() is the earliest
        picked = (
            x.filter(present)
            .sort_by(o.expr.filter(present), descending=func == "at_max", maintain_order=True)
            .first()
        )
        return Compiled(picked, inner.guards + o.guards)
    match func:
        case "sum":
            out = x.sum()
        case "mean":
            out = x.mean()
        case "min":
            out = x.min()
        case "max":
            out = x.max()
        case "count":
            out = x.count().cast(pl.Int64)  # polars count() excludes nulls
        case _:
            raise LatticeInternalError(f"unknown aggregate agg.{func}")
    return Compiled(out, inner.guards)
