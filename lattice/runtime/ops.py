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
    aggs: Sequence[tuple[str, str, ast.expr | None, ast.expr | None]],
) -> Result:
    guards: list[Guard] = []
    exprs: list[pl.Expr] = []
    for name, func, arg, order in aggs:
        c = compile_agg(func, arg, order)
        guards += c.guards
        exprs.append(c.expr.alias(name))
    if not by:
        return df.select(exprs), guards  # one row, even for an empty input
    return df.group_by(list(by), maintain_order=True).agg(exprs), guards


def join_size(
    left: pl.DataFrame,
    right: pl.DataFrame,
    on: Sequence[str],
    how: str,
    right_on: Sequence[str] | None = None,
) -> int:
    """Exact output row count of `join(left, right, on, how)`, from per-key counts
    (cheap: two group-bys and a join of the count tables; null keys never match).
    With on=[] (a single-row right table) every left row pairs with every right row;
    a left join keeps a left row even when the right table is empty. `right_on`
    names the right key columns when they differ from the left ones (on={"a": "b"})."""
    if not on:
        per_left = right.height if how == "inner" else max(right.height, 1)
        return left.height * per_left
    keys = list(on)
    rkeys = list(right_on) if right_on is not None else keys
    lc = left.group_by(keys).agg(pl.len().cast(pl.Int64).alias("__lattice_nl"))
    rc = right.group_by(rkeys).agg(pl.len().cast(pl.Int64).alias("__lattice_nr"))
    if how == "left":
        m = lc.join(rc, left_on=keys, right_on=rkeys, how="left", coalesce=True)
        r = pl.col("__lattice_nr").fill_null(1)
    else:
        m = lc.join(rc, left_on=keys, right_on=rkeys, how="inner", coalesce=True)
        r = pl.col("__lattice_nr")
    total = m.select((pl.col("__lattice_nl") * r).sum()).item()
    return int(total or 0)


def join(
    left: pl.DataFrame,
    right: pl.DataFrame,
    on: Sequence[str],
    how: str,
    right_on: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Join on the key columns `on` (left names); `right_on` names the right key
    columns when they differ. The output keeps the left key columns and drops the
    right ones."""
    if not on:
        return _cross_join(left, right, how)
    match how:
        case "inner" | "left":
            # Guarantee (spec §5.7): joins keep the left table's row order, so
            # sort -> join -> limit is deterministic.
            rkeys = list(right_on) if right_on is not None else list(on)
            return left.join(
                right,
                left_on=list(on),
                right_on=rkeys,
                how=how,
                coalesce=True,
                maintain_order="left",
            )
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


def sort(df: pl.DataFrame, by: Sequence[str], desc: bool | Sequence[bool]) -> pl.DataFrame:
    """desc is one direction for every column, or one per `by` column."""
    descending = desc if isinstance(desc, bool) else list(desc)
    return df.sort(list(by), descending=descending, nulls_last=True, maintain_order=True)


def limit(df: pl.DataFrame, n: int) -> pl.DataFrame:
    return df.head(n)


_IDX = "__lattice_idx"


def window(
    df: pl.DataFrame,
    by: Sequence[str],
    order: Sequence[str],
    desc: Sequence[bool],
    columns: Sequence[tuple[str, str, ast.expr | None, int | None]],
) -> Result:
    """window(): each column is a window function over the rows of its `by` partition
    sorted by `order` (nulls last, `desc` per order column; tied rows keep their input
    order: a stable sort). The output keeps `df`'s rows in `df`'s order."""
    guards: list[Guard] = []
    work = df.with_row_index(_IDX)
    if by or order:
        work = work.sort(
            [*by, *order],
            descending=[False] * len(by) + list(desc),
            nulls_last=True,
            maintain_order=True,
        )
    partition = list(by)
    exprs: list[pl.Expr] = []
    # Str cum_min/cum_max: (column, rank column, argument)
    lookups: list[tuple[str, str, pl.Expr]] = []
    for name, func, arg, count in columns:
        x: pl.Expr | None = None
        if arg is not None:
            c = compile_expr(arg)
            guards += c.guards
            x = c.expr
        if func in ("cum_min", "cum_max") and x is not None:
            if work.head(0).select(x.alias("__probe")).dtypes[0] == pl.String:
                # polars has no cum_min/cum_max over text: run them over the values'
                # dense ranks (text order), then map each rank back to its text
                rank = f"__lattice_rank_{len(lookups)}"
                work = work.with_columns(x.rank("dense").alias(rank))
                lookups.append((name, rank, x))
                x = pl.col(rank)
        e = _win_expr(func, x, count, order)
        exprs.append((e.over(partition) if partition else e).alias(name))
    out = work.with_columns(exprs)
    for name, rank, value in lookups:
        mapping = (
            work.select(pl.col(rank).alias("__lattice_r"), value.alias("__lattice_v"))
            .drop_nulls("__lattice_r")
            .unique("__lattice_r")
        )
        out = (
            out.join(
                mapping, left_on=name, right_on="__lattice_r", how="left", maintain_order="left"
            )
            .with_columns(pl.col("__lattice_v").alias(name))
            .drop("__lattice_v")
        )
    drop = [_IDX, *(rank for _, rank, _ in lookups)]
    return out.sort(_IDX).drop(drop), guards


def _win_expr(func: str, x: pl.Expr | None, count: int | None, order: Sequence[str]) -> pl.Expr:
    """One window function over the (sorted) rows of a partition."""
    row_number = pl.int_range(1, pl.len() + 1, dtype=pl.Int64)
    match func:
        case "row_number":
            return row_number
        case "rank" | "dense_rank":
            # a row starts a new rank when its order values differ from the previous
            # row's (nulls equal to nulls: peers)
            first = pl.int_range(pl.len()) == 0
            changed = pl.any_horizontal(
                [pl.col(c).ne_missing(pl.col(c).shift(1)) for c in order]
            )
            new = first | changed
            if func == "dense_rank":
                return new.cast(pl.Int64).cum_sum()
            return pl.when(new).then(row_number).otherwise(None).forward_fill()
        case "cum_count" if x is None:
            return row_number
    if x is None:
        raise LatticeInternalError(f"win.{func} needs an argument")
    match func:
        case "lag":
            return x.shift(count or 1)
        case "lead":
            return x.shift(-(count or 1))
        case "cum_sum":
            return x.fill_null(0).cum_sum()
        case "cum_count":
            return x.is_not_null().cast(pl.Int64).cum_sum()
        case "cum_min":
            return x.cum_min().forward_fill()
        case "cum_max":
            return x.cum_max().forward_fill()
        case "mean":
            return x.cast(pl.Float64).rolling_mean(window_size=_rows(count), min_samples=1)
        case "sum":
            return x.rolling_sum(window_size=_rows(count), min_samples=1).fill_null(0)
        case _:
            raise LatticeInternalError(f"no runtime implementation for win.{func}")


def _rows(count: int | None) -> int:
    if count is None or count < 1:
        raise LatticeInternalError("a moving window needs rows >= 1")
    return count


def traverse(
    edges: pl.DataFrame,
    start: pl.DataFrame,
    parent: str,
    child: str,
    max_depth: int,
    direction: str,
    depth: str,
) -> pl.DataFrame:
    """traverse(): a bounded breadth-first walk. Depth 0 is the (non-null) `child`
    values of `start`; each step follows parent -> child links ("down") or child ->
    parent links ("up") from the nodes first reached in the previous step, so every
    node is reached once, at its minimum depth, and cycles end the walk. The result is
    the `edges` rows whose child was reached, with its depth, in (depth, child) order."""
    if direction not in ("down", "up"):
        raise LatticeInternalError(f"unknown traverse direction {direction!r}")
    dtype = edges.schema[child]
    frontier = start.get_column(child).drop_nulls().unique(maintain_order=True)
    seen: dict[object, int] = {v: 0 for v in frontier.to_list()}
    source, target = (parent, child) if direction == "down" else (child, parent)
    for level in range(1, max_depth + 1):
        if frontier.len() == 0:
            break
        reached = edges.filter(pl.col(source).is_in(frontier.implode())).get_column(target)
        fresh = [v for v in reached.drop_nulls().unique(maintain_order=True).to_list()]
        fresh = [v for v in fresh if v not in seen]
        for v in fresh:
            seen[v] = level
        frontier = pl.Series(fresh, dtype=dtype)
    levels = pl.DataFrame(
        {"__lattice_node": list(seen), depth: list(seen.values())},
        schema={"__lattice_node": dtype, depth: pl.Int64},
    )
    out = edges.join(levels, left_on=child, right_on="__lattice_node", how="inner")
    out = out.select([*edges.columns, depth])
    return out.sort([depth, child], nulls_last=True, maintain_order=True)


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
