"""Column-expression typing: col/when/coalesce/is_null/fn.*/agg.* and operators.

Types are synthesized bottom-up and recorded in the context's side table; the
schema and scalar parameters in scope are inherited from the enclosing op.
An operand typed UNKNOWN (already reported) makes its parent UNKNOWN without a
new diagnostic, so one mistake yields one diagnostic.
"""

from __future__ import annotations

import ast
import difflib
from collections.abc import Collection, Mapping

from lattice.catalog import (
    AGG_FUNCS,
    DATE_ADD_DAYS_MAX,
    DATE_DIFF_UNITS,
    FN_ALIASES,
    FN_SIGS,
    ORDER_AGGS,
    ROUND_DIGITS_MAX,
)
from lattice.check.astutil import LiteralValue, literal_value
from lattice.check.context import CheckContext
from lattice.diagnostics import Severity, Span, span_of
from lattice.typesys import NUMERIC, ORDERED, UNKNOWN, ColT, LitT, Scalar, Schema, UnknownT, unify

ExprT = ColT | LitT | UnknownT

# aggregates that take a null policy (nulls="skip"), and the accepted policies
NULLS_AGGS = frozenset({"mean", "min", "max"})
NULL_POLICIES = frozenset({"skip"})

# Use-site name errors reported by P2; a Name at such a span is typed UNKNOWN silently.
NAME_ERROR_CODES = frozenset({"L202", "L203", "L603", "L604"})

_EXAMPLE_LITERAL = {
    Scalar.INT: "2",
    Scalar.FLOAT: "1.5",
    Scalar.STR: '"text"',
    Scalar.BOOL: "True",
    Scalar.DATE: '"2024-01-01"',
}
_ARITH = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Mod: "%"}
_CMP_EQ = {ast.Eq: "==", ast.NotEq: "!="}
_CMP_ORD = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}


def reported_name_spans(ctx: CheckContext) -> frozenset[Span]:
    """Spans of names that an earlier pass already reported as unresolved."""
    return frozenset(
        d.span
        for d in ctx.diagnostics
        if d.severity is Severity.ERROR and d.code in NAME_ERROR_CODES
    )


def constant_scalar(node: ast.expr) -> Scalar | None:
    """Scalar of a literal: a bool/int/float/str constant or a negated number."""
    v = literal_value(node)
    if isinstance(v, bool):
        return Scalar.BOOL
    if isinstance(v, int):
        return Scalar.INT
    if isinstance(v, float):
        return Scalar.FLOAT
    if isinstance(v, str):
        return Scalar.STR
    return None


def did_you_mean(name: str, candidates: list[str] | tuple[str, ...]) -> str | None:
    close = difflib.get_close_matches(name, candidates, n=1)
    return f"did you mean '{close[0]}'?" if close else None


def _scalars(ts: frozenset[Scalar]) -> str:
    return "{" + ", ".join(sorted(s.value for s in ts)) + "}"


def _wrap(scalar: Scalar, operands: list[ColT | LitT]) -> ColT | LitT:
    """LitT if every operand is constant, else ColT."""
    return LitT(scalar) if all(isinstance(o, LitT) for o in operands) else ColT(scalar)


def _known(ts: list[ExprT]) -> list[ColT | LitT] | None:
    """The operand types if none is UNKNOWN, else None."""
    out: list[ColT | LitT] = []
    for t in ts:
        if isinstance(t, UnknownT):
            return None
        out.append(t)
    return out


class ExprTyper:
    def __init__(
        self,
        ctx: CheckContext,
        schema: Schema,
        scalars: Mapping[str, Scalar],
        silent_calls: frozenset[str] = frozenset(),
        reported: frozenset[Span] | None = None,
        consts: Mapping[str, LiteralValue | None] | None = None,
        tables: Collection[str] = (),
    ) -> None:
        """`scalars`: scalar step parameters and well-typed named constants in scope.
        `consts`: every named constant in scope -> its plain literal value (None when
        computed, e.g. `b = a * 2`); a constant absent from `scalars` already
        produced a diagnostic, so its uses are typed UNKNOWN silently.
        `tables`: table names in scope (for a precise message when one is misused)."""
        self.ctx = ctx
        self.schema = schema
        self.scalars = scalars
        self.silent_calls = silent_calls
        self.reported = reported_name_spans(ctx) if reported is None else reported
        self.consts = consts or {}
        self.tables = tables

    # ---- public -------------------------------------------------------------

    def type_of(self, node: ast.expr) -> ExprT:
        t = self._synth(node)
        self.ctx.set_type(node, t)
        return t

    def type_agg(self, node: ast.expr) -> Scalar | None:
        result = self._agg(node)
        self.ctx.set_type(node, UNKNOWN if result is None else ColT(result))
        return result

    # ---- dispatch -----------------------------------------------------------

    def _synth(self, node: ast.expr) -> ExprT:
        match node:
            case ast.Constant():
                return self._constant(node)
            case ast.Name(id=name):
                return self._name(node, name)
            case ast.Call(func=ast.Name(id=name)):
                return self._named_call(node, name)
            case ast.Call(func=ast.Attribute(value=ast.Name(id="fn"), attr=attr)):
                return self._fn(node, attr)
            case ast.Call(func=ast.Attribute(value=ast.Name(id="agg"))):
                return self._err(
                    "L413",
                    node,
                    "aggregates are only allowed as group() keyword values",
                    "use derive for row-wise values and group(..., name=agg.sum(...)) to aggregate",
                )
            case ast.Attribute(value=ast.Name(id=ns), attr=attr):
                return self._err(
                    "L411", node, f"'{ns}.{attr}' is not a column expression", "call it"
                )
            case ast.BinOp():
                return self._binop(node)
            case ast.Compare():
                return self._compare(node)
            case ast.UnaryOp():
                return self._unary(node)
            case ast.List() | ast.Tuple():
                return self._err("L411", node, "lists are only allowed for by=/on=/cols=", None)
            case _:
                raise TypeError(f"P1 should have rejected {type(node).__name__}")

    def _err(self, code: str, node: ast.AST, message: str, hint: str | None) -> UnknownT:
        self.ctx.error(code, node, message, hint)
        return UNKNOWN

    # ---- leaves -------------------------------------------------------------

    def _constant(self, node: ast.Constant) -> ExprT:
        t = constant_scalar(node)
        if t is None:
            if node.value is None:
                return self._err(
                    "L410",
                    node,
                    "None is not allowed; use is_null(...) or coalesce(...)",
                    None,
                )
            return self._err(
                "L411",
                node,
                f"{type(node.value).__name__} literals are not column expressions",
                None,
            )
        if t is Scalar.STR and node.value in self.schema:
            self.ctx.warn(
                "L305",
                node,
                f"string '{node.value}' is also a column name",
                f'use col("{node.value}") to reference the column',
            )
        return LitT(t)

    def _name(self, node: ast.Name, name: str) -> ExprT:
        s = self.scalars.get(name)
        if s is not None:
            return LitT(s)
        if name in self.consts or span_of(node) in self.reported:
            return UNKNOWN  # an ill-typed constant or an unresolved name: already reported
        if name in self.tables:
            return self._err(
                "L411",
                node,
                f"'{name}' is a table, not a value",
                'reference a column with col("name"); pass tables to ops by name',
            )
        return self._err("L411", node, f"'{name}' is not a column expression", f'use col("{name}")')

    # ---- calls --------------------------------------------------------------

    def _named_call(self, node: ast.Call, name: str) -> ExprT:
        match name:
            case "col":
                return self._col(node)
            case "when":
                return self._when(node)
            case "coalesce":
                return self._coalesce(node)
            case "is_null":
                return self._is_null(node)
            case _ if name in self.silent_calls or span_of(node.func) in self.reported:
                return UNKNOWN
            case _:
                return self._err("L411", node, f"'{name}(...)' is not a column expression", None)

    def _col(self, node: ast.Call) -> ExprT:
        match node:
            case ast.Call(args=[ast.Constant(value=str(name))], keywords=[]):
                pass
            case _:
                return self._err(
                    "L412",
                    node,
                    "col() takes exactly one column name as a string",
                    'e.g. col("price")',
                )
        t = self.schema.get(name)
        if t is None:
            names = self.schema.names()
            return self._err(
                "L401",
                node,
                f"unknown column '{name}'; table has {{{', '.join(names)}}}",
                did_you_mean(name, names),
            )
        return ColT(t)

    def _arity(
        self, node: ast.Call, what: str, n_args: int, kws: frozenset[str], usage: str
    ) -> bool:
        given = sorted(kw.arg or "" for kw in node.keywords)
        if len(node.args) == n_args and given == sorted(kws):
            return True
        self.ctx.error("L404", node, f"{what} has the wrong arguments", f"write {usage}")
        return False

    def _when(self, node: ast.Call) -> ExprT:
        usage = "when(cond, then=..., otherwise=...)"
        if not self._arity(node, "when()", 1, frozenset({"then", "otherwise"}), usage):
            return UNKNOWN
        kws = {kw.arg: kw.value for kw in node.keywords}
        cond = self.type_of(node.args[0])
        then = self.type_of(kws["then"])
        other = self.type_of(kws["otherwise"])
        bad_cond = False
        if not isinstance(cond, UnknownT) and cond.scalar is not Scalar.BOOL:
            self.ctx.error(
                "L403",
                node.args[0],
                f"when() condition must be Bool, got {cond.scalar.value}",
                'compare a column, e.g. col("qty") > 1',
            )
            bad_cond = True
        known = _known([cond, then, other])
        if known is None or bad_cond:
            return UNKNOWN
        _, a, b = known
        u = unify(a.scalar, b.scalar)
        if u is None:
            return self._err(
                "L402",
                node,
                f"when() branches have different types: {a.scalar.value} and {b.scalar.value}",
                "convert one branch with fn.to_str/fn.to_int/fn.to_float",
            )
        return _wrap(u, known)

    def _coalesce(self, node: ast.Call) -> ExprT:
        if not self._arity(node, "coalesce()", 2, frozenset(), "coalesce(a, b)"):
            return UNKNOWN
        known = _known([self.type_of(a) for a in node.args])
        if known is None:
            return UNKNOWN
        a, b = known
        u = unify(a.scalar, b.scalar)
        if u is None:
            return self._err(
                "L402",
                node,
                f"coalesce() arguments have different types: {a.scalar.value} and {b.scalar.value}",
                None,
            )
        return _wrap(u, known)

    def _is_null(self, node: ast.Call) -> ExprT:
        if not self._arity(node, "is_null()", 1, frozenset(), "is_null(a)"):
            return UNKNOWN
        known = _known([self.type_of(node.args[0])])
        if known is None:
            return UNKNOWN
        return _wrap(Scalar.BOOL, known)

    def _fn(self, node: ast.Call, name: str) -> ExprT:
        sig = FN_SIGS.get(name)
        if sig is None:
            canonical = FN_ALIASES.get(name)
            if canonical is not None:
                return self._err(
                    "L702", node, f"fn.{name} is not in the library", f"use fn.{canonical}"
                )
            return self._err(
                "L106", node, f"unknown function fn.{name}", did_you_mean(name, list(FN_SIGS))
            )
        if len(node.args) != len(sig.params):
            return self._err(
                "L404",
                node,
                f"fn.{name} takes {len(sig.params)} positional argument(s), got {len(node.args)}",
                None,
            )
        args = [self.type_of(a) for a in node.args]
        ok = True
        for i, (t, allowed) in enumerate(zip(args, sig.params, strict=True), start=1):
            if not isinstance(t, UnknownT) and t.scalar not in allowed:
                ok = False
                self.ctx.error(
                    "L402",
                    node.args[i - 1],
                    f"fn.{name} expects {_scalars(allowed)} for argument {i}, got {t.scalar.value}",
                    "convert with fn.to_int/fn.to_float/fn.to_str or fn.to_date",
                )
        ok = self._fn_keywords(node, name, sig.kwargs) and ok
        known = _known(args)
        if known is None or not ok:
            return UNKNOWN
        result = sig.result if sig.result is not None else known[0].scalar
        if name == "clip" and any(
            self.ctx.types.get(id(kw.value)) == LitT(Scalar.FLOAT) for kw in node.keywords
        ):
            result = Scalar.FLOAT  # a Float bound makes the result Float (runtime casts)
        return _wrap(result, known)

    def _fn_keywords(self, node: ast.Call, name: str, expected: Mapping[str, Scalar]) -> bool:
        given = {kw.arg: kw for kw in node.keywords if kw.arg is not None}
        ok = True
        for missing in sorted(set(expected) - set(given)):
            ok = False
            self.ctx.error(
                "L404",
                node,
                f"fn.{name} requires keyword argument '{missing}'",
                f"add {missing}=...",
            )
        for arg, kw in given.items():
            want = expected.get(arg)
            if want is None:
                ok = False
                allowed = ", ".join(sorted(expected)) or "none"
                self.ctx.error(
                    "L404",
                    kw,
                    f"fn.{name} has no keyword argument '{arg}'",
                    f"allowed keywords: {allowed}",
                )
                continue
            ok = self._literal_kw(name, arg, kw.value, want) and ok
        return ok

    def _literal_kw(self, fn_name: str, arg: str, value: ast.expr, want: Scalar) -> bool:
        s = constant_scalar(value)
        t: ExprT
        if s is not None:
            # a plain literal: no L305 check, it is not in a column position
            t = LitT(s)
            self.ctx.set_type(value, t)
        elif isinstance(value, ast.Name):
            # a scalar step parameter or a named constant (substituted when lowered)
            t = self.type_of(value)
            if (
                not isinstance(t, UnknownT)
                and value.id in self.consts
                and self.consts[value.id] is None
            ):
                self.ctx.error(
                    "L404",
                    value,
                    f"fn.{fn_name} keyword '{arg}' must be a literal, a scalar step parameter "
                    f"or a constant bound to a literal; '{value.id}' is computed",
                    f"e.g. {arg}={_EXAMPLE_LITERAL[want]}",
                )
                return False
        else:
            self.ctx.error(
                "L404",
                value,
                f"fn.{fn_name} keyword '{arg}' must be a literal, a scalar step parameter "
                "or a constant bound to a literal",
                f"e.g. {arg}={_EXAMPLE_LITERAL[want]}",
            )
            return False
        if isinstance(t, UnknownT):
            return False
        if isinstance(t, ColT):
            self.ctx.error("L404", value, f"'{arg}' must be a literal", f"e.g. {arg}=2")
            return False
        if not (t.scalar is want or (want is Scalar.FLOAT and t.scalar is Scalar.INT)):
            self.ctx.error(
                "L402",
                value,
                f"fn.{fn_name} keyword '{arg}' expects {want.value}, got {t.scalar.value}",
                None,
            )
            return False
        lit = self.literal_of(value)
        if fn_name == "date_diff" and arg == "unit":
            if lit not in DATE_DIFF_UNITS:
                units = ", ".join(f'"{u}"' for u in sorted(DATE_DIFF_UNITS))
                self.ctx.error("L404", value, "unsupported date_diff unit", f"use unit={units}")
                return False
        if fn_name == "round" and arg == "digits":
            v = lit
            if isinstance(v, int) and not 0 <= v <= ROUND_DIGITS_MAX:
                self.ctx.error(
                    "L404",
                    value,
                    f"fn.round digits= must be between 0 and {ROUND_DIGITS_MAX}, got {v}",
                    "e.g. digits=2",
                )
                return False
        if fn_name == "date_add" and arg == "days":
            v = lit
            if isinstance(v, int) and abs(v) > DATE_ADD_DAYS_MAX:
                self.ctx.error(
                    "L404",
                    value,
                    f"fn.date_add days= must be within ±{DATE_ADD_DAYS_MAX}, got {v}",
                    "e.g. days=30",
                )
                return False
        return True

    def literal_of(self, value: ast.expr) -> LiteralValue | None:
        """The value of a literal, or of a named constant bound to a literal."""
        v = literal_value(value)
        if v is None and isinstance(value, ast.Name):
            return self.consts.get(value.id)
        return v

    # ---- operators ----------------------------------------------------------

    def _binop(self, node: ast.BinOp) -> ExprT:
        left, right = self.type_of(node.left), self.type_of(node.right)
        known = _known([left, right])
        if known is None:
            return UNKNOWN
        a, b = known[0].scalar, known[1].scalar
        match node.op:
            case ast.Add() | ast.Sub() | ast.Mult() | ast.Mod():
                sym = _ARITH[type(node.op)]
                if a in NUMERIC and b in NUMERIC:
                    both_int = a is Scalar.INT and b is Scalar.INT
                    return _wrap(Scalar.INT if both_int else Scalar.FLOAT, known)
                hint = None
                if sym == "+" and a is Scalar.STR and b is Scalar.STR:
                    hint = "join strings with fn.concat(a, b)"
                elif sym == "-" and a is Scalar.DATE and b is Scalar.DATE:
                    hint = 'use fn.date_diff(a, b, unit="days")'
                return self._mismatch(node, sym, a, b, hint)
            case ast.Div():
                if a in NUMERIC and b in NUMERIC:
                    return _wrap(Scalar.FLOAT, known)
                return self._mismatch(node, "/", a, b, None)
            case ast.BitAnd() | ast.BitOr():
                sym = "&" if isinstance(node.op, ast.BitAnd) else "|"
                if a is Scalar.BOOL and b is Scalar.BOOL:
                    return _wrap(Scalar.BOOL, known)
                return self._mismatch(node, sym, a, b, "both sides must be Bool comparisons")
            case _:
                raise TypeError(f"P1 should have rejected operator {type(node.op).__name__}")

    def _mismatch(
        self, node: ast.AST, sym: str, a: Scalar, b: Scalar, hint: str | None
    ) -> UnknownT:
        return self._err("L402", node, f"'{sym}' cannot combine {a.value} and {b.value}", hint)

    def _compare(self, node: ast.Compare) -> ExprT:
        if len(node.ops) != 1:
            raise TypeError("P1 should have rejected chained comparisons")
        op = node.ops[0]
        left, right = self.type_of(node.left), self.type_of(node.comparators[0])
        known = _known([left, right])
        if known is None:
            return UNKNOWN
        a, b = known[0].scalar, known[1].scalar
        if type(op) in _CMP_EQ:
            if unify(a, b) is None:
                return self._mismatch(node, _CMP_EQ[type(op)], a, b, None)
            if Scalar.FLOAT in (a, b):
                self.ctx.warn(
                    "L406",
                    node,
                    "float equality is fragile",
                    "compare with a tolerance, e.g. fn.abs(a - b) < 0.001",
                )
            return _wrap(Scalar.BOOL, known)
        if type(op) in _CMP_ORD:
            if (a in NUMERIC and b in NUMERIC) or (a == b and a in ORDERED):
                return _wrap(Scalar.BOOL, known)
            return self._mismatch(node, _CMP_ORD[type(op)], a, b, None)
        raise TypeError(f"P1 should have rejected comparison {type(op).__name__}")

    def _unary(self, node: ast.UnaryOp) -> ExprT:
        t = self.type_of(node.operand)
        if isinstance(t, UnknownT):
            return UNKNOWN
        match node.op:
            case ast.USub():
                if t.scalar in NUMERIC:
                    return _wrap(t.scalar, [t])
                return self._err("L402", node, f"'-' cannot apply to {t.scalar.value}", None)
            case ast.Invert():
                if t.scalar is Scalar.BOOL:
                    return _wrap(Scalar.BOOL, [t])
                return self._err("L402", node, f"'~' cannot apply to {t.scalar.value}", None)
            case _:
                raise TypeError(f"P1 should have rejected unary {type(node.op).__name__}")

    # ---- aggregates ---------------------------------------------------------

    @staticmethod
    def _agg_keywords(node: ast.Call, attr: str) -> bool:
        """Only mean/min/max take a keyword: at most one `nulls=`."""
        if not node.keywords:
            return True
        return attr in NULLS_AGGS and [kw.arg for kw in node.keywords] == ["nulls"]

    def _agg(self, node: ast.expr) -> Scalar | None:
        match node:
            case ast.Call(func=ast.Attribute(value=ast.Name(id="agg"), attr=attr)):
                pass
            case _:
                self.ctx.error(
                    "L413",
                    node,
                    'group() keyword values must be aggregates like agg.sum(col("x"))',
                    None,
                )
                return None
        if attr not in AGG_FUNCS:
            self.ctx.error(
                "L106", node, f"unknown aggregate agg.{attr}", did_you_mean(attr, sorted(AGG_FUNCS))
            )
            return None
        if attr == "count":
            if not node.args and not node.keywords:
                return Scalar.INT  # counts rows
            if len(node.args) != 1 or node.keywords:
                self.ctx.error(
                    "L404",
                    node,
                    "agg.count takes no argument (count rows) or one column expression "
                    "(count its non-null values)",
                    'write agg.count() or agg.count(col("x"))',
                )
                return None
            counted = self.type_of(node.args[0])  # any scalar type
            return None if isinstance(counted, UnknownT) else Scalar.INT
        if attr in ORDER_AGGS:
            return self._order_agg(node, attr)
        if len(node.args) != 1 or not self._agg_keywords(node, attr):
            self.ctx.error("L404", node, f"agg.{attr} takes exactly one column expression", None)
            return None
        for kw in node.keywords:
            if not (isinstance(kw.value, ast.Constant) and kw.value.value in NULL_POLICIES):
                self.ctx.error(
                    "L404",
                    kw.value,
                    f'agg.{attr} nulls= must be "skip"',
                    f'write agg.{attr}(<expr>, nulls="skip") to ignore missing values',
                )
                return None
        t = self.type_of(node.args[0])
        if isinstance(t, UnknownT):
            return None
        allowed = ORDERED if attr in ("min", "max") else NUMERIC
        if t.scalar not in allowed:
            self.ctx.error(
                "L402",
                node,
                f"agg.{attr} expects {_scalars(allowed)}, got {t.scalar.value}",
                None,
            )
            return None
        return Scalar.FLOAT if attr == "mean" else t.scalar

    def _order_agg(self, node: ast.Call, attr: str) -> Scalar | None:
        """agg.first/agg.last(x, order=o): x's value on the row with the smallest /
        largest o. x may be any scalar type; o must be ordered."""
        if len(node.args) != 1 or [kw.arg for kw in node.keywords] != ["order"]:
            self.ctx.error(
                "L404",
                node,
                f"agg.{attr} takes one column expression and an order= column expression",
                f'write agg.{attr}(col("x"), order=col("date"))',
            )
            return None
        value = self.type_of(node.args[0])
        order = self.type_of(node.keywords[0].value)
        if isinstance(value, UnknownT) or isinstance(order, UnknownT):
            return None
        if order.scalar not in ORDERED:
            self.ctx.error(
                "L402",
                node.keywords[0].value,
                f"agg.{attr} order= expects {_scalars(ORDERED)}, got {order.scalar.value}",
                None,
            )
            return None
        return value.scalar
