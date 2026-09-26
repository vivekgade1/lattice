"""P3 — canonical form: one op per statement, no nesting, fixed call shapes, no no-op steps."""

from __future__ import annotations

import ast

from lattice.catalog import IMPORTABLE, OP_SHAPES, TABLE_OPS, TERMINAL_OPS, OpShape
from lattice.check.astutil import step_param_kinds, steps_of
from lattice.check.context import CheckContext


class _Canon:
    def __init__(self, module: ast.Module, ctx: CheckContext) -> None:
        self.module = module
        self.ctx = ctx
        self.steps = steps_of(module)

    def _unit(self, node: ast.AST) -> str | None:
        """The op or step name if `node` is a unit call, else None."""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name in TABLE_OPS or name in self.steps:
                return name
        return None

    def run(self) -> None:
        for stmt in self.module.body:
            match stmt:
                case ast.Assign() | ast.Expr():
                    self._statement(stmt)
                case ast.FunctionDef():
                    for inner in stmt.body:
                        match inner:
                            case ast.Assign():
                                self._statement(inner)
                            case ast.Return():
                                pass
                            case _:
                                raise TypeError("P1 guarantees step bodies are Assign/Return")
                case ast.ImportFrom():
                    pass
                case _:
                    raise TypeError(f"P1 should have rejected {type(stmt).__name__}")

    # ---- statements ---------------------------------------------------------

    def _statement(self, stmt: ast.Assign | ast.Expr) -> None:
        value = stmt.value
        self._check_when(value)
        unit = self._unit(value)
        if unit is None or not isinstance(value, ast.Call):
            if self._unknown_callee(value):
                return  # P2 already reported the unknown name
            self.ctx.error(
                "L302",
                value,
                "each statement must call exactly one table op or step",
                "e.g. 'name = derive(table, new_col=...)'",
            )
            return
        if isinstance(stmt, ast.Expr) and unit not in TERMINAL_OPS:
            self.ctx.error(
                "L302",
                stmt,
                f"the result of '{unit}' must be assigned to a name",
                f"write 'name = {unit}(...)'",
            )
        # an assigned terminal op ('x = write(...)') is P5's L502
        for inner in ast.walk(value):
            if inner is not value and self._unit(inner) is not None:
                self.ctx.error(
                    "L301",
                    inner,
                    "table ops cannot be nested; assign the inner result to its own name first",
                )
        if unit in TABLE_OPS:
            self._op_shape(value, unit, OP_SHAPES[unit])
        else:
            self._step_shape(value, self.steps[unit])

    def _unknown_callee(self, value: ast.expr) -> bool:
        return (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id not in IMPORTABLE
        )

    # ---- call shapes --------------------------------------------------------

    def _table_args(self, args: list[ast.expr]) -> None:
        for a in args:
            if not isinstance(a, ast.Name) and self._unit(a) is None:
                self.ctx.error(
                    "L301",
                    a,
                    "table argument must be a name",
                    "assign the table to a name first and pass the name",
                )

    def _op_shape(self, call: ast.Call, op: str, shape: OpShape) -> None:
        n = len(shape.positional)
        if len(call.args) != n:
            self.ctx.error(
                "L303",
                call,
                f"'{op}' takes {n} positional argument(s) ({', '.join(shape.positional)}), "
                f"got {len(call.args)}",
                "pass other arguments by keyword",
            )
            self._table_args(call.args[: shape.tables])
            return
        self._table_args(call.args[: shape.tables])
        given = {kw.arg for kw in call.keywords if kw.arg is not None}
        for missing in sorted(shape.required_kw - given):
            self.ctx.error(
                "L303",
                call,
                f"'{op}' requires keyword argument '{missing}'",
                f"add {missing}=...",
            )
        if not shape.var_kw:
            allowed = shape.required_kw | shape.optional_kw
            for kw in call.keywords:
                if kw.arg is not None and kw.arg not in allowed:
                    expected = ", ".join(sorted(allowed)) or "none"
                    self.ctx.error(
                        "L303",
                        kw,
                        f"'{op}' has no keyword argument '{kw.arg}'",
                        f"allowed keywords: {expected}",
                    )
        self._no_op(call, op)

    def _step_shape(self, call: ast.Call, fn: ast.FunctionDef) -> None:
        kinds = step_param_kinds(fn)
        tables = [p for p, k in kinds if k == "table"]
        scalars = {p for p, k in kinds if k == "scalar"}
        if len(call.args) != len(tables):
            self.ctx.error(
                "L303",
                call,
                f"step '{fn.name}' takes {len(tables)} positional table argument(s) "
                f"({', '.join(tables)}), got {len(call.args)}",
                "pass scalar parameters by keyword",
            )
            self._table_args(call.args[: len(tables)])
            return
        self._table_args(call.args)
        given = {kw.arg for kw in call.keywords if kw.arg is not None}
        for missing in sorted(scalars - given):
            self.ctx.error(
                "L303",
                call,
                f"step '{fn.name}' requires keyword argument '{missing}'",
                f"add {missing}=...",
            )
        for kw in call.keywords:
            if kw.arg is not None and kw.arg not in scalars:
                self.ctx.error(
                    "L303",
                    kw,
                    f"step '{fn.name}' has no scalar parameter '{kw.arg}'",
                    "scalar parameters: " + (", ".join(sorted(scalars)) or "none"),
                )

    # ---- no-op steps and when() nesting -------------------------------------

    def _no_op(self, call: ast.Call, op: str) -> None:
        kws = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        match op:
            case "where" if isinstance(call.args[1], ast.Constant):
                self.ctx.error(
                    "L304",
                    call,
                    "'where' with a constant predicate is a no-op",
                    "remove this step or filter on a column expression",
                )
            case "derive" | "rename" if not kws:
                self.ctx.error(
                    "L304", call, f"'{op}' without any columns is a no-op", "remove this step"
                )
            case "select" if _is_empty_list(kws.get("cols")):
                self.ctx.error(
                    "L304", call, "'select' with cols=[] is a no-op", "list the columns to keep"
                )
            case "group" if _is_empty_list(kws.get("by")) and set(kws) == {"by"}:
                self.ctx.error(
                    "L304",
                    call,
                    "'group' with no keys and no aggregates is a no-op",
                    "add by=[...] or an aggregate",
                )
            case _:
                pass

    def _check_when(self, value: ast.expr) -> None:
        for node in ast.walk(value):
            if not (isinstance(node, ast.Call) and _is_when(node)):
                continue
            then = next((kw.value for kw in node.keywords if kw.arg == "then"), None)
            if then is None and len(node.args) >= 2:
                then = node.args[1]
            if then is not None and _is_when(then):
                self.ctx.error(
                    "L701",
                    then,
                    "nest when() only in 'otherwise'",
                    "invert the condition so the nested when() goes in otherwise=",
                )


def _is_when(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "when"


def _is_empty_list(node: ast.expr | None) -> bool:
    return isinstance(node, ast.List | ast.Tuple) and not node.elts


def run(module: ast.Module, ctx: CheckContext) -> None:
    _Canon(module, ctx).run()
