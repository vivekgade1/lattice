"""Type-tolerant comparison between an actual and expected polars DataFrame.

``frames_match`` never raises: any polars error or dtype-class mismatch is
reported as ``(False, reason)`` so a single odd output cannot crash a run.

Dtype classes: integer, float, bool, string, temporal (Date/Datetime compare
at day precision), null (an all-null column is compatible with anything), and
"other" (nested/object types — compatible only with the identical dtype).
Integer-vs-integer columns are compared exactly; any column involving a float
is compared as Float64 with ``rtol``/``atol``. For unordered comparisons rows
are sorted using a rounded copy of the float columns (so tiny float noise
cannot reorder rows), but the comparison itself uses the unrounded values.
"""

from __future__ import annotations

import polars as pl

from lattice.runtime import is_panic

_ROW_INDEX = "__frames_match_row__"
_SORT_DIGITS = 6
_DETAIL_LIMIT = 300


def frames_match(
    actual: pl.DataFrame,
    expected: pl.DataFrame,
    *,
    ordered: bool,
    rtol: float = 1e-6,
    atol: float = 1e-9,
) -> tuple[bool, str]:
    try:
        return _frames_match(actual, expected, ordered=ordered, rtol=rtol, atol=atol)
    except BaseException as exc:  # never raise: report as a mismatch (incl. engine panics)
        if not isinstance(exc, Exception) and not is_panic(exc):
            raise
        message = f"{type(exc).__name__}: {exc}"
        return False, f"comparison error: {message[:_DETAIL_LIMIT]}"


def _dtype_class(dtype: pl.DataType) -> str:
    if dtype == pl.Null:
        return "null"
    if dtype == pl.Boolean:
        return "bool"
    if dtype.is_integer():
        return "int"
    if dtype.is_float() or dtype.is_decimal():
        return "float"
    if dtype == pl.String or dtype == pl.Categorical or dtype == pl.Enum:
        return "str"
    if dtype == pl.Date or dtype == pl.Datetime:
        return "temporal"
    return "other"


def _column_plan(
    name: str, a_dtype: pl.DataType, b_dtype: pl.DataType
) -> tuple[pl.DataType | None, str | None]:
    """Common dtype to compare column ``name`` in, or a dtype-mismatch reason.

    ``None`` as the target dtype means "compare as-is" (identical 'other'
    dtypes).
    """
    a_cls, b_cls = _dtype_class(a_dtype), _dtype_class(b_dtype)
    if a_cls == "null" or b_cls == "null":
        other = b_cls if a_cls == "null" else a_cls
        return _target_for(other, b_dtype if a_cls == "null" else a_dtype), None
    if a_cls == b_cls:
        if a_cls == "other":
            if a_dtype != b_dtype:
                return None, f"column '{name}' dtype mismatch: {a_dtype} vs expected {b_dtype}"
            return None, None
        return _target_for(a_cls, a_dtype), None
    if {a_cls, b_cls} == {"int", "float"}:
        return pl.Float64, None
    return None, f"column '{name}' dtype mismatch: {a_dtype} vs expected {b_dtype}"


def _target_for(cls: str, dtype: pl.DataType) -> pl.DataType | None:
    return {
        "null": pl.String,
        "bool": pl.Boolean,
        "int": pl.Int64,
        "float": pl.Float64,
        "str": pl.String,
        "temporal": pl.String,
    }.get(cls, None if cls == "other" else dtype)


def _cast(series: pl.Series, target: pl.DataType | None) -> pl.Series:
    if target is None:
        return series
    if series.dtype.is_temporal() and target == pl.String:
        return series.cast(pl.Date).cast(pl.String)
    out = series.cast(target)
    if target == pl.Float64:
        out = out.fill_nan(None)
    return out


def _frames_match(
    actual: pl.DataFrame,
    expected: pl.DataFrame,
    *,
    ordered: bool,
    rtol: float,
    atol: float,
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

    targets: dict[str, pl.DataType | None] = {}
    for col in expected.columns:
        target, reason = _column_plan(col, actual.schema[col], expected.schema[col])
        if reason is not None:
            return False, reason
        targets[col] = target

    norm_actual = pl.DataFrame([_cast(actual[c], targets[c]).alias(c) for c in expected.columns])
    norm_expected = pl.DataFrame(
        [_cast(expected[c], targets[c]).alias(c) for c in expected.columns]
    )

    if not ordered:
        norm_actual = _sorted_rows(norm_actual)
        norm_expected = _sorted_rows(norm_expected)

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


def _sorted_rows(frame: pl.DataFrame) -> pl.DataFrame:
    """Rows of ``frame`` sorted by all columns, using rounded floats only as keys."""
    if frame.width == 0 or frame.height == 0:
        return frame
    keys = frame.select(
        [
            pl.col(name).round(_SORT_DIGITS) if dtype == pl.Float64 else pl.col(name)
            for name, dtype in frame.schema.items()
        ]
    ).with_row_index(_ROW_INDEX)
    order = keys.sort(by=list(frame.columns), nulls_last=True, maintain_order=True)[_ROW_INDEX]
    return frame[order.to_list()]
