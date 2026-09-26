"""Type-tolerant comparison between an actual and expected polars DataFrame."""

from __future__ import annotations

import polars as pl


def frames_match(
    actual: pl.DataFrame,
    expected: pl.DataFrame,
    *,
    ordered: bool,
    rtol: float = 1e-6,
    atol: float = 1e-9,
) -> tuple[bool, str]:
    actual_cols = set(actual.columns)
    expected_cols = set(expected.columns)
    if actual_cols != expected_cols:
        missing = sorted(expected_cols - actual_cols)
        extra = sorted(actual_cols - expected_cols)
        return False, f"columns differ: missing {missing}, extra {extra}"

    actual = actual.select(expected.columns)

    if actual.height != expected.height:
        return False, f"row count {actual.height} != {expected.height}"

    norm_actual = _normalize(actual)
    norm_expected = _normalize(expected)

    if not ordered:
        sort_cols = list(expected.columns)
        norm_actual = norm_actual.sort(by=sort_cols, nulls_last=True)
        norm_expected = norm_expected.sort(by=sort_cols, nulls_last=True)

    for col in expected.columns:
        a = norm_actual[col]
        b = norm_expected[col]
        if a.dtype == pl.Float64 and b.dtype == pl.Float64:
            both_null = a.is_null() & b.is_null()
            close = ((a - b).abs() <= (atol + rtol * b.abs())).fill_null(False)
            match = close | both_null
        else:
            match = a.eq_missing(b)
        if not bool(match.all()):
            idx = match.to_list().index(False)
            return False, f"column '{col}' differs at row {idx}: {a[idx]} != {b[idx]}"

    return True, ""


def _normalize(frame: pl.DataFrame) -> pl.DataFrame:
    exprs = []
    for name, dtype in frame.schema.items():
        if dtype.is_numeric():
            expr = pl.col(name).cast(pl.Float64).fill_nan(None).round(6)
        elif dtype.is_temporal():
            expr = pl.col(name).cast(pl.Date).cast(pl.String)
        else:
            expr = pl.col(name).cast(pl.String)
        exprs.append(expr.alias(name))
    return frame.select(exprs)
