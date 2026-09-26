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
