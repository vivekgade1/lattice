"""P5 — effects: where load/write may appear and which paths they may touch."""

from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath

from lattice.catalog import IO_OPS
from lattice.check.context import CheckContext
from lattice.diagnostics import Span

_EXTENSIONS: dict[str, tuple[str, ...]] = {"load": (".csv",), "write": (".csv", ".parquet")}
_PATH_ARG = {"load": 0, "write": 1}
_DRIVE = re.compile(r"^[A-Za-z]:")


def _io_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in IO_OPS:
        return node.func.id
    return None


def _is_relative(path: str) -> bool:
    if not path or "\\" in path or path.startswith(("/", "~")) or _DRIVE.match(path):
        return False
    return ".." not in PurePosixPath(path).parts


class _Effects:
    def __init__(self, module: ast.Module, ctx: CheckContext) -> None:
        self.module = module
        self.ctx = ctx

    def run(self) -> None:
        writes = 0
        for stmt in self.module.body:
            match stmt:
                case ast.Assign(value=value) if _io_name(value) == "write":
                    writes += 1
                    self.ctx.error(
                        "L502",
                        stmt,
                        "write(...) returns nothing; call it as its own statement",
                        'remove the assignment: write(table, "out.parquet")',
                    )
                case ast.Expr(value=value) if _io_name(value) == "write":
                    writes += 1
                case ast.FunctionDef():
                    self._step(stmt)
                    continue
                case _:
                    pass
            for node in ast.walk(stmt):
                self._path(node)
        if writes == 0:
            last = self.module.body[-1] if self.module.body else None
            self.ctx.error(
                "L504",
                last if last is not None else Span(1, 1),
                "program never writes a result",
                'end with write(result, "out.parquet")',
            )

    def _step(self, fn: ast.FunctionDef) -> None:
        for stmt in fn.body:
            for node in ast.walk(stmt):
                op = _io_name(node)
                if op is not None:
                    self.ctx.error(
                        "L503",
                        node,
                        "steps must be pure: load/write are only allowed at module level",
                        f"move the {op}(...) call out of the step and pass tables in",
                    )
                self._path(node)

    def _path(self, node: ast.AST) -> None:
        op = _io_name(node)
        if op is None:
            return
        assert isinstance(node, ast.Call)
        i = _PATH_ARG[op]
        if len(node.args) <= i:
            return  # P3 reported the call shape
        arg = node.args[i]
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            return  # P4 reported the non-literal path
        path = arg.value
        if not _is_relative(path):
            self.ctx.error(
                "L501",
                arg,
                "path must be relative to the working directory",
                'e.g. "orders.csv" or "out.parquet" (no leading /, no .., no backslashes)',
            )
            return
        allowed = _EXTENSIONS[op]
        if not path.lower().endswith(allowed):
            self.ctx.error(
                "L505",
                arg,
                f"{op}() path must end with {' or '.join(allowed)}",
                f'e.g. "{"orders.csv" if op == "load" else "out.parquet"}"',
            )


def run(module: ast.Module, ctx: CheckContext) -> None:
    _Effects(module, ctx).run()
