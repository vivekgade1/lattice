from __future__ import annotations

import datetime

import polars as pl

from bench.compare import frames_match


def test_identical_frames_match() -> None:
    df = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    ok, reason = frames_match(df, df.clone(), ordered=False)
    assert ok
    assert reason == ""


def test_row_order_ignored_when_unordered() -> None:
    expected = pl.DataFrame({"a": [1, 2, 3]})
    actual = pl.DataFrame({"a": [3, 1, 2]})
    ok, reason = frames_match(actual, expected, ordered=False)
    assert ok, reason


def test_row_order_detected_when_ordered() -> None:
    expected = pl.DataFrame({"a": [1, 2, 3]})
    actual = pl.DataFrame({"a": [3, 1, 2]})
    ok, reason = frames_match(actual, expected, ordered=True)
    assert not ok
    assert reason != ""


def test_column_order_ignored() -> None:
    expected = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    actual = pl.DataFrame({"b": ["x", "y"], "a": [1, 2]})
    ok, reason = frames_match(actual, expected, ordered=True)
    assert ok, reason


def test_int_vs_float_match() -> None:
    expected = pl.DataFrame({"a": [3.0]})
    actual = pl.DataFrame({"a": [3]})
    ok, reason = frames_match(actual, expected, ordered=True)
    assert ok, reason


def test_close_float_matches_within_tolerance() -> None:
    expected = pl.DataFrame({"a": [1.0]})
    actual = pl.DataFrame({"a": [1.0000000001]})
    ok, reason = frames_match(actual, expected, ordered=True)
    assert ok, reason


def test_float_mismatch_names_column_in_reason() -> None:
    expected = pl.DataFrame({"a": [1.0]})
    actual = pl.DataFrame({"a": [1.1]})
    ok, reason = frames_match(actual, expected, ordered=True)
    assert not ok
    assert "'a'" in reason


def test_nan_matches_null() -> None:
    expected = pl.DataFrame({"a": [None]}, schema={"a": pl.Float64})
    actual = pl.DataFrame({"a": [float("nan")]}, schema={"a": pl.Float64})
    ok, reason = frames_match(actual, expected, ordered=True)
    assert ok, reason


def test_datetime_matches_date() -> None:
    expected = pl.DataFrame({"a": [datetime.date(2024, 1, 1)]}, schema={"a": pl.Date})
    actual = pl.DataFrame(
        {"a": [datetime.datetime(2024, 1, 1, 0, 0, 0)]}, schema={"a": pl.Datetime}
    )
    ok, reason = frames_match(actual, expected, ordered=True)
    assert ok, reason


def test_missing_column_reason_lists_it() -> None:
    expected = pl.DataFrame({"a": [1], "x": [2]})
    actual = pl.DataFrame({"a": [1], "y": [2]})
    ok, reason = frames_match(actual, expected, ordered=True)
    assert not ok
    assert "missing" in reason and "'x'" in reason
    assert "extra" in reason and "'y'" in reason


def test_null_vs_null_in_string_column_match() -> None:
    expected = pl.DataFrame({"a": [None, "x"]}, schema={"a": pl.String})
    actual = pl.DataFrame({"a": [None, "x"]}, schema={"a": pl.String})
    ok, reason = frames_match(actual, expected, ordered=True)
    assert ok, reason


def test_row_count_mismatch_reason() -> None:
    expected = pl.DataFrame({"a": [1, 2, 3, 4]})
    actual = pl.DataFrame({"a": [1, 2, 3]})
    ok, reason = frames_match(actual, expected, ordered=True)
    assert not ok
    assert reason == "row count 3 != 4"


# --- Regression tests for review finding M3 -------------------------------


def test_large_ints_compared_exactly_not_via_float() -> None:
    # 2**53 and 2**53 + 1 are equal once cast to Float64.
    expected = pl.DataFrame({"a": [2**53 + 1]}, schema={"a": pl.Int64})
    actual = pl.DataFrame({"a": [2**53]}, schema={"a": pl.Int64})
    ok, reason = frames_match(actual, expected, ordered=True)
    assert not ok
    assert "'a'" in reason


def test_values_within_tolerance_straddling_rounding_boundary_match() -> None:
    # round(6) maps these to 1.0 and 1.000001 although they differ by ~2e-10.
    expected = pl.DataFrame({"a": [1.0000005001]})
    actual = pl.DataFrame({"a": [1.0000004999]})
    ok, reason = frames_match(actual, expected, ordered=False, rtol=1e-8)
    assert ok, reason
    ok, reason = frames_match(actual, expected, ordered=True, rtol=1e-8)
    assert ok, reason


def test_unordered_float_values_differing_below_6dp_but_beyond_tolerance_mismatch() -> None:
    # Rounding before comparison used to hide a 4e-7 relative error.
    expected = pl.DataFrame({"a": [100.0000001]})
    actual = pl.DataFrame({"a": [100.0000004]})
    ok, reason = frames_match(actual, expected, ordered=False, rtol=1e-9, atol=0.0)
    assert not ok
    assert "'a'" in reason


def test_int_vs_float_uses_tolerance() -> None:
    expected = pl.DataFrame({"a": [3.0000000001, 5.0]})
    actual = pl.DataFrame({"a": [5, 3]})
    ok, reason = frames_match(actual, expected, ordered=False)
    assert ok, reason


def test_bool_vs_int_is_a_dtype_mismatch_not_an_exception() -> None:
    expected = pl.DataFrame({"a": [1, 0]})
    actual = pl.DataFrame({"a": [True, False]})
    ok, reason = frames_match(actual, expected, ordered=True)
    assert not ok
    assert "'a'" in reason and "dtype mismatch" in reason


def test_str_vs_int_is_a_dtype_mismatch() -> None:
    expected = pl.DataFrame({"a": [1, 2]})
    actual = pl.DataFrame({"a": ["1", "2"]})
    ok, reason = frames_match(actual, expected, ordered=False)
    assert not ok
    assert "dtype mismatch" in reason


def test_all_null_column_is_compatible_with_any_dtype() -> None:
    expected = pl.DataFrame({"a": [None, None]}, schema={"a": pl.Int64})
    actual = pl.DataFrame({"a": [None, None]})  # pl.Null dtype
    ok, reason = frames_match(actual, expected, ordered=False)
    assert ok, reason


def test_polars_exception_is_reported_not_raised() -> None:
    # Object columns cannot be sorted; frames_match must never raise.
    expected = pl.DataFrame({"a": [object(), object()]}, schema={"a": pl.Object})
    actual = pl.DataFrame({"a": [object(), object()]}, schema={"a": pl.Object})
    ok, reason = frames_match(actual, expected, ordered=False)
    assert not ok
    assert reason


def test_nested_dtype_mismatch_is_reported_not_raised() -> None:
    expected = pl.DataFrame({"a": [[1, 2], [3]]})
    actual = pl.DataFrame({"a": [1, 2]})
    ok, reason = frames_match(actual, expected, ordered=False)
    assert not ok
    assert "dtype mismatch" in reason


def test_panic_during_comparison_is_reported_not_raised(monkeypatch) -> None:
    import bench.compare as compare

    class PanicException(BaseException):
        pass

    def boom(*_a, **_k):
        raise PanicException("out-of-range date")

    monkeypatch.setattr(compare, "_frames_match", boom)
    df = pl.DataFrame({"a": [1]})
    ok, reason = compare.frames_match(df, df, ordered=False)
    assert not ok and "PanicException" in reason
