"""Endpoint plans as Go net/http handlers (plan 5a Task 12).

`emit_go_endpoints(plans, types, module)` is `lattice.api.codegen._EndpointGen`
transliterated to Go over the request runtime `lattice/gogen/rt` (http.go, db.go,
t2io.go): per endpoint an `rt.Endpoint` (the prelude's Endpoint, field for field), one
package-level constant per statement holding the plan's SQL text verbatim, and a handler
running the plan's steps in the generated steps function's order -- the same statements
with the same named binds, the same checks, the same responses. `main.go` serves the
routes in `_specificity` order (literal segments before parameters) with the profile's
settings; routing, the 404/405 answers, authentication, parameter validation and the
per-request transaction are `rt.Serve`'s (the prelude's `serve` / `dispatch`).

Procedures run in the request as the Python app runs them (lattice/t2/support.py):

* `t2_arg` (col, param, const, now, caller, caller_col, is_admin, row): the
  `rt.T2<Scalar>` decoders (`t2_decode`), `rt.T2Opt` for an optional parameter,
  `t2caller(c)` (the Caller struct), a Row / record struct literal;
* `t2_post` (derive, where, select, rename): a loop over the rows with `rt.Cells`,
  `rt.T2Store` (`sql_value(t2_result(...))`) and `rt.T2Holds`;
* `t2_holds`: the same loop; false answers the step's error;
* `t2_call`: the table arguments as `*rt.List` of structs, `rt.T2RowBound` (R621), the
  fields `rt.T2Store`d; a scalar is bound as a parameter with `rt.T2Value`;
* `t2_run`: `rt.T2Run("line:col", ...)`.

Every procedure call gets the request's fuel (`c.Fuel`: `ApiProfile.fuel`,
`max_value_size`). Natives (L890) and for_each's effects are not generated: the checker
rejects them for Go before this runs.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Sequence
from typing import Any

from lattice.api.backend import SQLITE
from lattice.api.codegen import _ambiguous, _auth_sql, _specificity
from lattice.api.ir import (
    CallStep,
    CheckStep,
    EndpointPlan,
    ErrorSpec,
    ForEachStep,
    PostStep,
    ReadStep,
    T2CheckStep,
    WriteStep,
)
from lattice.api.sql import IS_ADMIN, NOW, USER_ID, SqlQuery, caller_bind, sent_bind
from lattice.catalog import LIST_KIND, USER_PARAM
from lattice.check.api_info import MISSING, UNSET, ParamSpec
from lattice.check.t2 import types as T
from lattice.check.t2.types import T2Type
from lattice.gogen.t2 import go_float, go_regex, go_string
from lattice.gogen.types import GoTypes
from lattice.runtime import LatticeInternalError
from lattice.t2ir import nodes as n
from lattice.targets import portable_error_style

__all__ = ["emit_go_endpoints"]

_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
# The rt decoder of each Lattice scalar (t2_decode) and the T2 type it gives.
_DECODERS: dict[str, tuple[str, T2Type]] = {
    "Int": ("rt.T2Int", T.INT),
    "Float": ("rt.T2Float", T.FLOAT),
    "Str": ("rt.T2Str", T.STR),
    "Bool": ("rt.T2Bool", T.BOOL),
    "Date": ("rt.T2Date", T.DATE),
    "DateTime": ("rt.T2DateTime", T.DATETIME),
    "Uuid": ("rt.T2Uuid", T.UUID),
}
_SCALAR_OF = {t: s for s, (_, t) in _DECODERS.items()}

# ---- Go literals ---------------------------------------------------------------------------


def _float(v: float) -> str:
    """A float64 constant: its repr when Go reads it back exactly, else go_float."""
    if math.isfinite(v) and not (v == 0 and math.copysign(1.0, v) < 0):
        text = repr(float(v))
        return text if "e" in text or "." in text else text + ".0"
    return go_float(v)


def _strs(names: Sequence[str]) -> str:
    return "[]string{" + ", ".join(go_string(x) for x in names) + "}"


def _value(v: object) -> str:
    """A bind or default value as the prelude holds it (sql_value applies at bind time)."""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return f"int64({v})"
    if isinstance(v, float):
        return f"float64({_float(v)})"
    if isinstance(v, str):
        return go_string(v)
    if isinstance(v, dt.datetime):
        micros = (v.astimezone(dt.UTC) - _EPOCH) // dt.timedelta(microseconds=1)
        return f"rt.DateTime({micros})"
    if isinstance(v, dt.date):
        return f"rt.Date({v.toordinal()})"
    raise LatticeInternalError(f"gogen: no Go value for {v!r}")


def _ident(text: str) -> str:
    out = re.sub(r"\W+", "_", text, flags=re.ASCII).strip("_")
    return out or "x"


def _param(spec: ParamSpec, null_ok: Sequence[str]) -> str:
    """The prelude's Param(...) as an rt.ParamSpec literal (codegen._param)."""
    if spec.scalar is None:
        raise LatticeInternalError(f"parameter '{spec.name}' has no scalar type")
    parts = [
        f"Name: {go_string(spec.name)}",
        f"Source: {go_string(spec.source)}",
        f"Scalar: {go_string(spec.scalar.value)}",
    ]
    if spec.ge is not None:
        parts.append(f"Ge: rt.F({_float(float(spec.ge))})")
    if spec.le is not None:
        parts.append(f"Le: rt.F({_float(float(spec.le))})")
    if spec.min_len is not None:
        parts.append(f"MinLen: rt.I({spec.min_len})")
    if spec.max_len is not None:
        parts.append(f"MaxLen: rt.I({spec.max_len})")
    if spec.one_of is not None:
        if not all(isinstance(x, str) for x in spec.one_of):
            raise LatticeInternalError(f"gogen: OneOf values of '{spec.name}' are not text")
        parts.append(f"OneOf: {_strs(spec.one_of)}")
    if spec.pattern is not None:
        # compiled here as Python compiles it (re.ASCII), matched by Python's SRE engine
        # (rt/regex.go) with the prelude's re.fullmatch semantics
        parts.append(f"Pattern: {go_regex(spec.pattern)}")
    if spec.default is UNSET:
        parts.append("Default: rt.Unset")
    elif spec.default is None:
        parts.append("Default: rt.Null")
    elif spec.default is not MISSING:
        parts.append(f"Default: {_value(spec.default)}")
    if spec.optional:
        parts.append("Optional: true")
        if spec.name in null_ok:
            parts.append("Nullable: true")
    if spec.email:
        parts.append("Email: true")
    return "{" + ", ".join(parts) + "}"


def _error(e: ErrorSpec) -> str:
    return f"rt.ErrorResponse({e.status}, {go_string(e.code)}, {go_string(e.detail)})"


def _strp(v: str | None) -> str:
    return "nil" if v is None else f"rt.S({go_string(v)})"


# ---- one endpoint ------------------------------------------------------------------------


class _Gen:
    def __init__(
        self,
        plan: EndpointPlan,
        index: int,
        types: GoTypes,
        procs: dict[str, n.Procedure],
        lines: Sequence[str] | None,
    ) -> None:
        self.plan = plan
        self.types = types
        self.procs = procs
        self.lines = lines
        self.base = f"ep{index}_{_ident(plan.name)}"
        self.consts: list[str] = []
        self.body: list[str] = []
        self.depth = 1
        self.temps = 0
        self.names: set[str] = set()

    # ---- pieces ---------------------------------------------------------------------------

    def emit(self, line: str) -> None:
        self.body.append("\t" * self.depth + line)

    def temp(self, base: str = "t") -> str:
        self.temps += 1
        return f"{base}{self.temps}"

    def const(self, base: str, value: str, kind: str = "const") -> str:
        name = f"{self.base}_{_ident(base)}"
        k = 2
        while name in self.names:
            name, k = f"{self.base}_{_ident(base)}_{k}", k + 1
        self.names.add(name)
        self.consts.append(f"{kind} {name} = {value}")
        return name

    def comment(self, line: int, fallback: str) -> None:
        text = ""
        if self.lines is not None and 1 <= line <= len(self.lines):
            text = self.lines[line - 1].strip()
        self.emit(f"// line {line}: {text or fallback}")

    def result(self, step: str) -> str:
        return f"results[{go_string(step)}]"

    def binds(self, sql: SqlQuery) -> list[str]:
        """The statement's named binds (codegen._EndpointGen.binds, in its order)."""
        out = [f"rt.B({go_string(name)}, {_value(value)})" for name, value in sql.binds]
        out += [f"rt.B({go_string('p_' + p)}, p[{go_string(p)}])" for p in sql.params]
        out += [f"rt.B({go_string(sent_bind(p))}, rt.Sent(p, {go_string(p)}))" for p in sql.sent]
        out += [
            f"rt.B({go_string(bind)}, rt.RowsJSON({self.result(step)}, {_strs(cols)}))"
            for bind, step, cols in sql.rows
        ]
        if sql.user:
            out += [f"rt.B({go_string(USER_ID)}, userID(c))"]
            out += [f"rt.B({go_string(IS_ADMIN)}, c.Caller.IsAdminInt())"]
        if sql.now:
            out += [f"rt.B({go_string(NOW)}, c.Now)"]
        out += [
            f"rt.B({go_string(caller_bind(col))}, "
            f"c.Caller.Row[{self.plan.user_columns.index(col)}])"
            for col in sql.caller
        ]
        return out

    def run(self, step_id: str, sql: SqlQuery, conflict: ErrorSpec | None = None) -> str:
        """rt Query / Write of the step's statement: a Go expression of rt.Result."""
        const = self.const(f"{step_id}_sql", go_string(sql.text))
        binds = self.binds(sql)
        if conflict is not None:  # unique_or=Conflict(...)
            return f"c.Write({', '.join([const, _error(conflict), *binds])})"
        return f"c.Query({', '.join([const, *binds])})"

    def cols(self, step_id: str, names: Sequence[str]) -> str:
        return self.const(f"{step_id}_cols", _strs(names), "var")

    # ---- steps ----------------------------------------------------------------------------

    def step(self, step: Any) -> None:  # noqa: C901, PLR0912 - one case per step kind
        match step:
            case ReadStep() if step.sql.total:
                self.emit(f"// SELECT the rows of {step.id!r} and the page total (one statement)")
                cols = self.cols(step.id, step.schema.names() + step.flags)
                rows = self.temp("rows")
                self.emit(
                    f"{rows}, {rows}_total := rt.SplitTotal({self.run(step.id, step.sql)}.Rows)"
                )
                self.emit(f"totals[{go_string(step.id)}] = {rows}_total")
                self.emit(f"{self.result(step.id)} = rt.Result{{Cols: {cols}, Rows: {rows}}}")
            case ReadStep():
                self.emit(f"// SELECT the rows of {step.id!r} (one statement)")
                cols = self.cols(step.id, step.schema.names() + step.flags)
                run = self.run(step.id, step.sql)
                self.emit(f"{self.result(step.id)} = rt.Result{{Cols: {cols}, Rows: {run}.Rows}}")
            case CheckStep(kind="one"):
                self.comment(step.span.line, f"one({step.input})")
                where = go_string(f"{step.span.line}:{step.span.col}")
                self.emit(f"rt.CheckOne({self.result(step.input)}, {_error(step.error)}, {where})")
            case CheckStep(kind="require"):
                if step.flag_step is None or step.flag is None:
                    raise LatticeInternalError(f"require '{step.id}' has no condition flags")
                self.comment(step.span.line, f"require({step.input})")
                self.emit(
                    f"rt.CheckRequire({self.result(step.flag_step)}, {go_string(step.flag)}, "
                    f"{_error(step.error)})"
                )
            case WriteStep(kind="insert"):
                self.comment(step.span.line, f"insert into {step.table}")
                cols = self.cols(step.id, step.schema.names())
                run = self.run(step.id, step.sql, step.conflict)
                self.emit(f"{self.result(step.id)} = rt.Result{{Cols: {cols}, Rows: {run}.Rows}}")
            case WriteStep(kind="update"):
                assert step.input is not None
                self.comment(step.span.line, f"update {step.table}")
                returned = self.temp("returned")
                self.emit(f"{returned} := {self.run(step.id, step.sql, step.conflict)}.Rows")
                if step.missing is not None:  # an owner transfer to a missing user
                    self.emit(
                        f"rt.CheckWritten({self.result(step.input)}, {returned}, "
                        f"{_error(step.missing)})"
                    )
                args = ", ".join(
                    [
                        self.result(step.input),
                        returned,
                        _strs(step.schema.names()),
                        _strs(step.key),
                        _strs([c for c, _ in step.values]),
                    ]
                )
                self.emit(f"{self.result(step.id)} = rt.MergeUpdate({args})")
            case WriteStep(kind="delete"):
                assert step.input is not None
                self.comment(step.span.line, f"delete from {step.table}")
                self.emit(self.run(step.id, step.sql))
                cols = self.cols(step.id, step.schema.names())
                self.emit(
                    f"{self.result(step.id)} = rt.Result{{Cols: {cols}, "
                    f"Rows: rt.Project({self.result(step.input)}, {cols})}}"
                )
            case ForEachStep():
                # natives and effectful procedures are L890 on Go: never reached
                raise LatticeInternalError("gogen: for_each (an effect native) on Go")
            case PostStep():
                self.post(step)
            case T2CheckStep():
                self.t2_check(step)
            case CallStep():
                self.t2_call(step)
            case _:
                raise LatticeInternalError(f"unknown step {step!r}")

    # ---- procedures ---------------------------------------------------------------------------

    def proc(self, name: str) -> n.Procedure:
        proc = self.procs.get(name)
        if proc is None:
            raise LatticeInternalError(f"gogen: unknown procedure {name}")
        return proc

    def decode(self, value: str, scalar: str, want: T2Type) -> str:
        """t2_decode(value, scalar) as a value of the procedure's type `want`."""
        dec, t = _DECODERS[scalar]
        base = T.strip_opt(want)
        if base != t:
            raise LatticeInternalError(
                f"gogen: a {scalar} value handed to a {want.render()} parameter"
            )
        if isinstance(want, T.Opt):
            return f"rt.T2Opt({value}, {dec})"
        return f"{dec}({value})"

    def const_arg(self, v: object, want: T2Type) -> str:
        base = T.strip_opt(want)
        if v is None:
            if not isinstance(want, T.Opt):
                raise LatticeInternalError("gogen: None handed to a non-optional parameter")
            return f"rt.None[{self.types.spell(base)}]()"
        if base == T.BOOL and isinstance(v, bool):
            lit = "true" if v else "false"
        elif base == T.INT and isinstance(v, int) and not isinstance(v, bool):
            lit = f"int64({v})"
        elif base == T.FLOAT and isinstance(v, float):
            lit = f"float64({_float(v)})"
        elif base in (T.STR, T.UUID) and isinstance(v, str):
            lit = go_string(v)
        else:
            raise LatticeInternalError(f"gogen: constant {v!r} for a {want.render()} parameter")
        return f"rt.Some({lit})" if isinstance(want, T.Opt) else lit

    def arg(self, desc: tuple[Any, ...], want: T2Type, cells: str | None) -> str:  # noqa: PLR0911
        """One argument descriptor's value (t2_arg) as a Go expression of type `want`."""
        kind = desc[0]
        if kind == "col":
            if cells is None:
                raise LatticeInternalError("gogen: col() without a row")
            return self.decode(f"{cells}.Get({go_string(desc[1])})", desc[2], want)
        if kind == "param":  # ("param", name[, scalar]): an Int for a Float is widened
            scalar = desc[2] if len(desc) > 2 else _SCALAR_OF.get(T.strip_opt(want))
            if scalar is None:
                raise LatticeInternalError(f"gogen: parameter for a {want.render()} argument")
            return self.decode(f"p[{go_string(desc[1])}]", scalar, want)
        if kind == "const":
            return self.const_arg(desc[1], want)
        if kind == "now":
            return self.wrap("c.Now", T.DATETIME, want)
        if kind == "caller":
            return self.wrap("t2caller(c)", T.CALLER, want)
        if kind == "caller_col":
            return self.decode(f"callerCol(c, {go_string(desc[1])})", desc[2], want)
        if kind == "is_admin":
            return self.wrap("isAdmin(c)", T.BOOL, want)
        if kind == "row":
            if cells is None:
                raise LatticeInternalError("gogen: row() without a row")
            return self.wrap(self.row_value(desc[1], desc[2], desc[3], cells, T.strip_opt(want)),
                             T.strip_opt(want), want)  # fmt: skip
        raise LatticeInternalError(f"unknown procedure argument {desc!r}")

    def wrap(self, go: str, t: T2Type, want: T2Type) -> str:
        if isinstance(want, T.Opt) and not isinstance(t, T.Opt):
            if want.inner != t:
                raise LatticeInternalError(f"gogen: a {t.render()} for {want.render()}")
            return f"rt.Some({go})"
        if want != t:
            raise LatticeInternalError(f"gogen: a {t.render()} for {want.render()}")
        return go

    def row_value(
        self, kind: str, name: str, fields: tuple[tuple[str, str], ...], cells: str, want: T2Type
    ) -> str:
        """t2_row_value: a Row[Table] or a record from the row's stored cells."""
        if kind == "record" and want != T.RecordT(name):
            raise LatticeInternalError(f"gogen: record {name} for {want.render()}")
        if kind == "row" and not (isinstance(want, T.RowT) and want.table == name):
            raise LatticeInternalError(f"gogen: Row[{name}] for {want.render()}")
        declared = dict(self.types.fields(want))
        if [f for f, _ in fields] != list(declared):
            raise LatticeInternalError(f"gogen: the fields of {want.render()} differ")
        parts = [
            f"F_{f}: {self.decode(f'{cells}.Get({go_string(f)})', s, declared[f])}"
            for f, s in fields
        ]
        return f"{self.types.spell(want)}{{{', '.join(parts)}}}"

    def call(self, name: str, args: tuple[tuple[str, Any], ...], cells: str | None) -> str:
        """Evaluate the arguments in descriptor order into temps; the call expression
        (arguments in the procedure's parameter order, the request's fuel first)."""
        proc = self.proc(name)
        want = dict(proc.params)
        temps: dict[str, str] = {}
        for param, desc in args:
            if param not in want:
                raise LatticeInternalError(f"gogen: {name} has no parameter {param}")
            tmp = self.temp("a")
            self.emit(f"{tmp} := {self.arg(desc, want[param], cells)}")
            temps[param] = tmp
        missing = [p for p, _ in proc.params if p not in temps]
        if missing:
            raise LatticeInternalError(f"gogen: {name} called without {missing}")
        return f"P_{name}({', '.join(['c.Fuel', *(temps[p] for p, _ in proc.params)])})"

    def where_of(self, step: PostStep | T2CheckStep | CallStep) -> str:
        return go_string(f"{step.span.line}:{step.span.col}")

    def post(self, step: PostStep) -> None:
        """T2-post (t2_post): the ops over the input's rows, in the app layer."""
        self.emit(f"// T2-post: procedures over the rows of {step.input!r} (app layer)")
        out = self.cols(step.id, step.schema.names())
        self.emit(f"rt.T2Run({self.where_of(step)}, func() {{")
        self.depth += 1
        self.emit(f"src := {self.result(step.input)}")
        self.emit("rows := [][]any{}")
        self.emit("for _, row := range src.Rows {")
        self.depth += 1
        self.emit("cells := rt.NewCells(src.Cols, row)")
        for op in step.ops:
            kind = op[0]
            if kind == "derive":
                _, column, proc, args, scalar = op
                self.emit(f"// derive {column} = {proc}(...)")
                self.emit("{")
                self.depth += 1
                call = self.call(proc, args, "cells")
                self.emit(
                    f"cells.Set({go_string(column)}, rt.T2Store(any({call}), {go_string(scalar)}))"
                )
                self.depth -= 1
                self.emit("}")
            elif kind == "where":
                _, proc, args = op
                self.emit(f"// where {proc}(...)")
                self.emit("{")
                self.depth += 1
                call = self.call(proc, args, "cells")
                self.emit(f"if !rt.T2Holds(any({call})) {{")
                self.emit("\tcontinue")
                self.emit("}")
                self.depth -= 1
                self.emit("}")
            elif kind == "select":
                self.emit(f"cells = cells.Select({_strs(op[1])})")
            elif kind == "rename":
                pairs = ", ".join(f"{{{go_string(a)}, {go_string(b)}}}" for a, b in op[1])
                self.emit(f"cells = cells.Rename([][2]string{{{pairs}}})")
            else:
                raise LatticeInternalError(f"unknown T2 op {kind!r}")
        self.emit(f"rows = append(rows, cells.Row({out}))")
        self.depth -= 1
        self.emit("}")
        self.emit(f"{self.result(step.id)} = rt.Result{{Cols: {out}, Rows: rows}}")
        self.depth -= 1
        self.emit("})")

    def t2_check(self, step: T2CheckStep) -> None:
        """require(t, proc(...)) (t2_holds): the procedure holds on every row."""
        self.comment(step.span.line, f"require({step.input}, {step.proc}(...))")
        holds = self.temp("holds")
        self.emit(f"{holds} := true")
        self.emit(f"rt.T2Run({self.where_of(step)}, func() {{")
        self.depth += 1
        self.emit(f"src := {self.result(step.input)}")
        self.emit("for _, row := range src.Rows {")
        self.depth += 1
        self.emit("cells := rt.NewCells(src.Cols, row)")
        call = self.call(step.proc, step.args, "cells")
        self.emit(f"if !rt.T2Holds(any({call})) {{")
        self.emit(f"\t{holds} = false")
        self.emit("\treturn")
        self.emit("}")
        self.depth -= 1
        self.emit("}")
        self.depth -= 1
        self.emit("})")
        self.emit(f"if !{holds} {{")
        self.emit(f"\trt.Abort({_error(step.error)})")
        self.emit("}")

    def t2_call(self, step: CallStep) -> None:
        """x = call(proc, ...) (t2_call): table arguments as lists of rows / records."""
        self.comment(step.span.line, f"call({step.proc}, ...)")
        proc = self.proc(step.proc)
        want = dict(proc.params)
        self.emit(f"rt.T2Run({self.where_of(step)}, func() {{")
        self.depth += 1
        self.emit("bound := 0")
        temps: dict[str, str] = {}
        for param, desc in step.args:
            if param not in want:
                raise LatticeInternalError(f"gogen: {step.proc} has no parameter {param}")
            tmp = self.temp("a")
            temps[param] = tmp
            if desc[0] != "rows":
                self.emit(f"{tmp} := {self.arg(desc, want[param], None)}")
                continue
            _, src_step, kind, name, fields = desc
            ptype = want[param]
            if not isinstance(ptype, T.ListT):
                raise LatticeInternalError(f"gogen: rows for a {ptype.render()} parameter")
            elem = self.types.spell(ptype.elem)
            src = self.temp("src")
            self.emit(f"{src} := {self.result(src_step)}")
            self.emit(f"bound += len({src}.Rows)")
            self.emit(f"{tmp} := &rt.List[{elem}]{{Items: make([]{elem}, 0, len({src}.Rows))}}")
            self.emit(f"for _, row := range {src}.Rows {{")
            self.depth += 1
            self.emit(f"cells := rt.NewCells({src}.Cols, row)")
            value = self.row_value(kind, name, fields, "cells", ptype.elem)
            self.emit(f"{tmp}.Items = append({tmp}.Items, {value})")
            self.depth -= 1
            self.emit("}")
        missing = [p for p, _ in proc.params if p not in temps]
        if missing:
            raise LatticeInternalError(f"gogen: {step.proc} called without {missing}")
        call = f"P_{step.proc}({', '.join(['c.Fuel', *(temps[p] for p, _ in proc.params)])})"
        self.emit(f"v := {call}")
        if step.result[0] == "scalar":
            target = step.bind if step.bind is not None else step.id
            self.emit(f"p[{go_string(target)}] = rt.T2Value(any(v), {go_string(step.result[1])})")
            self.emit("_ = bound")
        else:
            self.rows_result(step, proc.returns)
        self.depth -= 1
        self.emit("})")

    def rows_result(self, step: CallStep, returns: T2Type) -> None:
        _, fields, many = step.result
        if many:
            if not isinstance(returns, T.ListT):
                raise LatticeInternalError(f"gogen: {step.proc} returns {returns.render()}")
            item_t = returns.elem
            self.emit("items := v.Items")
        else:
            item_t = returns
            if isinstance(returns, T.Opt):  # [None]: getattr(None, field) raises
                item_t = returns.inner
                first = fields[0][0] if fields else ""
                self.emit("if !v.Ok {")
                self.emit(
                    '\trt.Raise("AttributeError", '
                    f"{go_string(f"'NoneType' object has no attribute '{first}'")})"
                )
                self.emit("}")
                self.emit(f"items := []{self.types.spell(item_t)}{{v.V}}")
            else:
                self.emit(f"items := []{self.types.spell(item_t)}{{v}}")
        declared = dict(self.types.fields(item_t))
        for f, _ in fields:
            if f not in declared:
                raise LatticeInternalError(f"gogen: {item_t.render()} has no field {f}")
        self.emit(f"rt.T2RowBound({go_string(step.proc)}, len(items), bound)")
        cells = ", ".join(f"rt.T2Store(any(it.F_{f}), {go_string(s)})" for f, s in fields)
        self.emit("rows := make([][]any, len(items))")
        self.emit("for i, it := range items {")
        self.emit(f"\trows[i] = []any{{{cells}}}")
        self.emit("}")
        cols = self.cols(step.id, [f for f, _ in fields])
        self.emit(f"{self.result(step.id)} = rt.Result{{Cols: {cols}, Rows: rows}}")

    # ---- the response ---------------------------------------------------------------------------

    def respond(self) -> None:
        plan = self.plan
        if plan.success == "NoContent":
            self.emit("return rt.Response{Status: 204, Body: rt.NoContent}")
            return
        pairs = ", ".join(
            f"{{Name: {go_string(c)}, Scalar: {go_string(t.value)}}}" for c, t in plan.schema.columns
        )
        columns = self.const("response", f"[]rt.Col{{{pairs}}}", "var")
        result = self.result(plan.returns)
        if plan.success == "Page":
            if plan.page is None:
                raise LatticeInternalError(f"Page endpoint '{plan.name}' returns no page()")

            def arg(v: int | str) -> str:
                return f"p[{go_string(v)}]" if isinstance(v, str) else f"int64({v})"

            shape = ", ".join(f"{{{go_string(k)}, {go_string(v)}}}" for k, v in plan.page_shape)
            shape_c = self.const("page_shape", f"[][2]string{{{shape}}}", "var")
            total = "nil"
            if plan.page.total_step is not None:
                self.emit(f"total := totals[{go_string(plan.page.total_step)}]")
                total = "&total"
            self.emit(
                f"return rt.RespondPage({result}, {columns}, {shape_c}, "
                f"{arg(plan.page.limit)}, {arg(plan.page.offset)}, {total})"
            )
            return
        if plan.success == LIST_KIND:
            self.emit(f"return rt.RespondList({result}, {columns})")
            return
        status = 201 if plan.success == "Created" else 200
        where = go_string(f"{plan.span.line}:{plan.span.col}")
        self.emit(f"return rt.RespondOne({result}, {columns}, {status}, {where})")

    # ---- the whole endpoint ---------------------------------------------------------------------

    def endpoint(self) -> str:
        plan = self.plan
        params = ", ".join(_param(p, plan.null_ok) for p in plan.params if p.source != "user")
        fields = [
            f"Name: {go_string(plan.name)}",
            f"Method: {go_string(plan.method)}",
            f"Path: {go_string(plan.path)}",
            f"Auth: {go_string(plan.auth)}",
            f"Params: []rt.ParamSpec{{{params}}}",
            f"Writes: {'true' if plan.writes else 'false'}",
            f"AuthSQL: {go_string(_auth_sql(plan))}",
            f"UserColumns: {_strs(plan.user_columns)}",
            f"UserKey: {go_string(plan.user_key)}",
            f"AdminRole: {go_string(plan.admin_role)}",
            f"RoleColumn: {go_string(plan.role_column)}",
        ]
        if plan.admin_flag is not None:
            fields.append(f"AdminFlag: rt.S({go_string(plan.admin_flag)})")
            fields.append(f"FlagRole: {go_string(plan.flag_role)}")
        if plan.fixed_role is not None:
            fields.append(f"FixedRole: rt.S({go_string(plan.fixed_role)})")
        if plan.body == "form":
            fields.append("Form: true")
        ambiguous = _ambiguous(plan)
        if ambiguous:
            message = ambiguous[0].value
            assert isinstance(message, str)
            fields.append(f"Ambiguous: {go_string(_unquote(message))}")
        return "rt.Endpoint{\n" + "".join(f"\t{f},\n" for f in fields) + "}"

    def render(self) -> tuple[str, str, str]:
        """(the endpoint's declarations, its Endpoint variable, its handler name)."""
        plan = self.plan
        self.emit("results := map[string]rt.Result{}")
        if any(isinstance(s, ReadStep) and s.sql.total for s in plan.steps):
            self.emit("totals := map[string]int64{}")
        self.emit("if c.Caller != nil {")
        self.emit(
            f"\t{self.result(USER_PARAM)} = rt.Result{{Cols: {_strs(plan.user_columns)}, "
            "Rows: [][]any{c.Caller.Row}}"
        )
        self.emit("}")
        for step in plan.steps:
            self.step(step)
        self.respond()
        ep_var, handler = f"{self.base}_ep", f"{self.base}_steps"
        rule = "// " + "-" * 86
        out = [
            rule,
            f"// {plan.name}: {plan.method} {plan.path} "
            f"(auth={plan.auth}; Lattice source line {plan.span.line})",
            rule,
            "",
            f"var {ep_var} = {self.endpoint()}",
            "",
            *self.consts,
            "",
            f"// {handler}: {plan.method} {plan.path}, the plan's steps in order.",
            f"func {handler}(c *rt.Ctx, p map[string]any) rt.Response {{",
            *self.body,
            "}",
        ]
        return "\n".join(out), ep_var, handler


def _unquote(py_literal: str) -> str:
    """The text of a Python string literal codegen rendered (_py_str)."""
    import ast  # noqa: PLC0415

    value = ast.literal_eval(py_literal)
    assert isinstance(value, str)
    return value


# ---- the program ---------------------------------------------------------------------------------


def _settings(plans: Sequence[EndpointPlan]) -> str:
    """rt.Settings from the profile-wide plan settings (codegen's _PROFILE_CONSTANTS)."""

    def one(field: str) -> Any:
        values = {getattr(p, field) for p in plans}
        if len(values) > 1:
            raise LatticeInternalError(f"plans disagree on {field}: {sorted(map(repr, values))}")
        return values.pop() if values else None

    if not plans:
        return "rt.Settings{}"
    if not portable_error_style(one("error_body"), one("validation_status")):
        # gated by the checker (L892, Target.error_styles): reaching one is internal
        raise LatticeInternalError(
            f"error_body {one('error_body')!r} / validation_status "
            f"{one('validation_status')} is not available on Go (L892)"
        )
    active = one("active_column")
    fields = [
        f"ErrorBody: {go_string(one('error_body'))}",
        f"UnauthorizedDetail: {_strp(one('unauthorized_detail'))}",
        f"ForbiddenDetail: {_strp(one('forbidden_detail'))}",
        f"ActiveColumn: {_strp(active)}",
        f"InactiveStatus: {one('inactive_status')}",
        f"InactiveDetail: {go_string(one('inactive_detail'))}",
        f"UUIDStorage: {go_string(one('uuid_storage'))}",
        f"DateTimeStorage: {go_string(one('datetime_storage'))}",
        f"DateTimeWire: {go_string(one('datetime_wire'))}",
        f"Fuel: {one('fuel')}",
        f"MaxSize: {one('max_value_size')}",
    ]
    return "rt.Settings{\n" + "".join(f"\t{f},\n" for f in fields) + "}"


def _unsupported(plan: EndpointPlan) -> str | None:
    """Why the plan cannot run on Go (the checker's L890 refuses these first)."""
    if plan.method not in _METHODS:
        return f"unsupported method {plan.method!r}"
    if plan.backend != SQLITE.name:
        return f"the Go target supports the sqlite backend only, not {plan.backend!r}"
    if plan.auth_native is not None or plan.token_invalid or plan.token_unknown:
        return "NativeToken authentication (a native)"
    if plan.natives or plan.effects or plan.t2_effects:
        return "natives"
    if any(isinstance(s, ForEachStep) for s in plan.steps):
        return "for_each"
    return None


_HELPERS = """\
// userID is the caller's key (:__user_id), nil without a caller.
func userID(c *rt.Ctx) any {
	if c.Caller == nil {
		return nil
	}
	return c.Caller.ID
}

// isAdmin is T2Request.is_admin: the caller has the admin role.
func isAdmin(c *rt.Ctx) bool { return c.Caller != nil && c.Caller.IsAdmin }

// callerCol is the caller's stored value of a users column (nil without a caller).
func callerCol(c *rt.Ctx, col string) any {
	if c.Caller == nil {
		return nil
	}
	return c.Caller.Cols[col]
}
"""


def _caller_fn(types: GoTypes, plans: Sequence[EndpointPlan]) -> str:
    """t2caller: T2Request.caller() -- the users row's public columns decoded, then the
    key as `id`, `is_admin` and the role."""
    if types.caller is None:
        return ""
    keys = {p.user_key for p in plans}
    if len(keys) > 1:
        raise LatticeInternalError("plans disagree on the users key")
    key = keys.pop() if keys else "id"
    parts = []
    for f, t in types.caller:
        if f == "is_admin":
            parts.append("F_is_admin: isAdmin(c)")
        elif f == "role":
            parts.append("F_role: role")
        else:
            scalar = _SCALAR_OF.get(T.strip_opt(t))
            if scalar is None:
                raise LatticeInternalError(f"gogen: Caller field {f} is {t.render()}")
            col = key if f == "id" else f
            dec, _ = _DECODERS[scalar]
            value = f"cols[{go_string(col)}]"
            parts.append(
                f"F_{f}: "
                + (f"rt.T2Opt({value}, {dec})" if isinstance(t, T.Opt) else f"{dec}({value})")
            )
    body = ",\n\t\t".join(parts)
    return (
        "// t2caller is the Caller a procedure receives (T2Request.caller).\n"
        f"func t2caller(c *rt.Ctx) {types.spell(T.CALLER)} {{\n"
        '\tcols, role := map[string]any{}, ""\n'
        "\tif c.Caller != nil {\n\t\tcols, role = c.Caller.Cols, c.Caller.Role\n\t}\n"
        f"\treturn {types.spell(T.CALLER)}{{\n\t\t{body},\n\t}}\n}}\n"
    )


def emit_go_endpoints(
    plans: Sequence[EndpointPlan],
    types: GoTypes,
    module: n.T2Module | None = None,
    source: str | None = None,
) -> tuple[str, str]:
    """(handlers.go, main.go) of package main serving `plans`. `types` spells the T2
    values the handlers build (Row, Caller, records); `module` is the program's T2 IR
    (the procedures' signatures) when the plans run procedures. The procedures and the
    type declarations are `lattice.gogen.t2.emit_go_t2`'s file (with the same `types`,
    emitted after this)."""
    for plan in plans:
        why = _unsupported(plan)
        if why is not None:
            raise LatticeInternalError(f"gogen: endpoint '{plan.name}': {why}")
    procs = {p.name: p for p in module.procedures} if module is not None else {}
    lines = source.splitlines() if source is not None else None
    ordered = sorted(plans, key=_specificity)
    parts: list[str] = []
    routes: list[str] = []
    uses_caller = False
    for i, plan in enumerate(ordered):
        text, ep_var, handler = _Gen(plan, i, types, procs, lines).render()
        uses_caller |= "t2caller(c)" in text
        parts.append(text)
        routes.append(f"\t{{EP: {ep_var}, Handle: {handler}}},")
    listing = "\n".join(
        f"//   line {p.span.line:>4}: {p.method:<6} {p.path} -> {p.name}" for p in ordered
    )
    body = "\n\n".join(parts)
    helpers = _HELPERS + ("\n" + _caller_fn(types, plans) if uses_caller else "")
    handlers = (
        "// Code generated by lattice (lattice/gogen/endpoints.py). DO NOT EDIT.\n\n"
        "package main\n\n"
        + ('import (\n\t"math"\n\n\t"lattice.dev/rt"\n)\n\nvar _ = math.Inf\n\n'
           if "math." in body else 'import "lattice.dev/rt"\n\n')
        + "// routes, in registration order (literal path segments before parameters;\n"
        "// Lattice source line of each endpoint):\n"
        f"{listing}\n"
        f"var routes = []rt.Route{{\n{chr(10).join(routes)}\n}}\n\n"
        "// settings are the profile's (error bodies, auth, storage, the T2 budget).\n"
        f"var settings = {_settings(plans)}\n\n"
        f"{helpers}\n{body}\n"
    )  # fmt: skip
    main = (
        "// Code generated by lattice (lattice/gogen/endpoints.py). DO NOT EDIT.\n\n"
        "package main\n\n"
        'import "lattice.dev/rt"\n\n'
        "// main: app --db <path> --addr <host:port> prints LISTENING <host:port> and serves.\n"
        "func main() { rt.Serve(routes, settings) }\n"
    )
    return handlers, main
