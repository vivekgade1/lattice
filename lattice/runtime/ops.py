"""Op implementations over polars DataFrames.

Each row-expression op returns its output frame plus the guards of its
expressions; guards are evaluated by the executor on the op's *input* frame.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

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


def join_size(left: pl.DataFrame, right: pl.DataFrame, on: Sequence[str], how: str) -> int:
    """Exact output row count of `join(left, right, on, how)`, from per-key counts
    (cheap: two group-bys and a join of the count tables; null keys never match).
    With on=[] (a single-row right table) every left row pairs with every right row;
    a left join keeps a left row even when the right table is empty."""
    if not on:
        per_left = right.height if how == "inner" else max(right.height, 1)
        return left.height * per_left
    keys = list(on)
    lc = left.group_by(keys).agg(pl.len().cast(pl.Int64).alias("__lattice_nl"))
    rc = right.group_by(keys).agg(pl.len().cast(pl.Int64).alias("__lattice_nr"))
    if how == "left":
        m = lc.join(rc, on=keys, how="left", coalesce=True)
        r = pl.col("__lattice_nr").fill_null(1)
    else:
        m = lc.join(rc, on=keys, how="inner", coalesce=True)
        r = pl.col("__lattice_nr")
    total = m.select((pl.col("__lattice_nl") * r).sum()).item()
    return int(total or 0)


def join(left: pl.DataFrame, right: pl.DataFrame, on: Sequence[str], how: str) -> pl.DataFrame:
    if not on:
        return _cross_join(left, right, how)
    match how:
        case "inner" | "left":
            return left.join(right, on=list(on), how=how, coalesce=True)
        case _:
            raise LatticeInternalError(f"unsupported join how={how!r}")


def _cross_join(left: pl.DataFrame, right: pl.DataFrame, how: str) -> pl.DataFrame:
    """join(on=[]): the checker guarantees `right` has at most one row. Every left row
    gets the right row's values; with an empty right table an inner join is empty and
    a left join keeps every left row with typed nulls for the right columns."""
    if how not in ("inner", "left"):
        raise LatticeInternalError(f"unsupported join how={how!r}")
    if right.height > 1:
        raise LatticeInternalError(f"join on=[] with a {right.height}-row right table")
    if right.height == 0 and how == "left":
        return left.with_columns(
            pl.lit(None, dtype=dtype).alias(name) for name, dtype in right.schema.items()
        )
    return left.join(right, how="cross", maintain_order="left_right")


def sort(df: pl.DataFrame, by: Sequence[str], desc: bool) -> pl.DataFrame:
    return df.sort(list(by), descending=desc, nulls_last=True, maintain_order=True)


def limit(df: pl.DataFrame, n: int) -> pl.DataFrame:
    return df.head(n)


def write(df: pl.DataFrame, path: Path, shown: str, span: Span) -> None:
    # the format comes from the written path as the program spells it (case-insensitive),
    # never from the resolved path, which may differ through a symlink
    suffix = PurePosixPath(shown).suffix.lower()
    if suffix not in (".csv", ".parquet"):
        raise LatticeRuntimeError("R103", span, f"could not write '{shown}': unsupported format")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if suffix == ".csv":
            df.write_csv(path)
        else:
            df.write_parquet(path)
    except (pl.exceptions.PolarsError, OSError) as err:
        raise LatticeRuntimeError(
            "R103", span, f"could not write '{shown}': {_first_line(err)}"
        ) from err
