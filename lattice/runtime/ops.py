"""Op implementations over polars DataFrames.

Each row-expression op returns its output frame plus the guards of its
expressions; guards are evaluated by the executor on the op's *input* frame.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path

import polars as pl

from lattice.diagnostics import Span
from lattice.runtime import LatticeInternalError, LatticeRuntimeError
from lattice.runtime.exprs import DTYPES, Guard, compile_agg, compile_expr
from lattice.typesys import Scalar, Schema

Result = tuple[pl.DataFrame, list[Guard]]


def _first_line(err: BaseException) -> str:
    text = str(err).strip()
    return text.splitlines()[0] if text else type(err).__name__


def load(path: Path, shown: str, schema: Schema, span: Span) -> pl.DataFrame:
    """Read a CSV with the declared schema; NaN -> null; any failure -> R101."""
    if not path.is_file():
        raise LatticeRuntimeError("R101", span, f"could not load '{shown}': file not found")
    dtypes = {name: DTYPES[s] for name, s in schema.columns}
    try:
        df = pl.read_csv(path, schema_overrides=dtypes, infer_schema=False)
    except (pl.exceptions.PolarsError, OSError, UnicodeDecodeError) as err:
        raise LatticeRuntimeError(
            "R101", span, f"could not load '{shown}': {_first_line(err)}"
        ) from err
    missing = [n for n in schema.names() if n not in df.columns]
    if missing:
        raise LatticeRuntimeError(
            "R101",
            span,
            f"could not load '{shown}': missing column(s) {', '.join(missing)}",
        )
    floats = [n for n, s in schema.columns if s is Scalar.FLOAT]
    df = df.select(schema.names())
    if floats:
        df = df.with_columns(pl.col(floats).fill_nan(None))
    return df


def where(df: pl.DataFrame, predicate: ast.expr) -> Result:
    c = compile_expr(predicate)
    return df.filter(c.expr), c.guards  # a null predicate drops the row


def derive(df: pl.DataFrame, columns: Sequence[tuple[str, ast.expr]]) -> Result:
    guards: list[Guard] = []
    exprs: list[pl.Expr] = []
    for name, node in columns:
        c = compile_expr(node)
        guards += c.guards
        exprs.append(c.expr.alias(name))
    return df.with_columns(exprs), guards


def select(df: pl.DataFrame, cols: Sequence[str]) -> pl.DataFrame:
    return df.select(list(cols))


def rename(df: pl.DataFrame, mapping: Sequence[tuple[str, str]]) -> pl.DataFrame:
    return df.rename(dict(mapping))


def group(
    df: pl.DataFrame,
    by: Sequence[str],
    aggs: Sequence[tuple[str, str, ast.expr | None]],
) -> Result:
    guards: list[Guard] = []
    exprs: list[pl.Expr] = []
    for name, func, arg in aggs:
        c = compile_agg(func, arg)
        guards += c.guards
        exprs.append(c.expr.alias(name))
    if not by:
        return df.select(exprs), guards  # one row, even for an empty input
    return df.group_by(list(by), maintain_order=True).agg(exprs), guards


def join(left: pl.DataFrame, right: pl.DataFrame, on: Sequence[str], how: str) -> pl.DataFrame:
    match how:
        case "inner" | "left":
            return left.join(right, on=list(on), how=how, coalesce=True)
        case _:
            raise LatticeInternalError(f"unsupported join how={how!r}")


def sort(df: pl.DataFrame, by: Sequence[str], desc: bool) -> pl.DataFrame:
    return df.sort(list(by), descending=desc, nulls_last=True, maintain_order=True)


def limit(df: pl.DataFrame, n: int) -> pl.DataFrame:
    return df.head(n)


def write(df: pl.DataFrame, path: Path, shown: str, span: Span) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".csv":
            df.write_csv(path)
        elif path.suffix == ".parquet":
            df.write_parquet(path)
        else:
            raise LatticeInternalError(f"unsupported output format '{shown}'")
    except (pl.exceptions.PolarsError, OSError) as err:
        raise LatticeRuntimeError(
            "R103", span, f"could not write '{shown}': {_first_line(err)}"
        ) from err
