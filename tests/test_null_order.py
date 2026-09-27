"""Profile.null_order (tabular): the default place of null keys for `sort` and `window`
order when a sort gives no `nulls=`. "last" (the default) puts them last in either
direction; "smallest" orders null as the smallest value (first ascending, last
descending: SQLite's own order). An explicit `nulls=` wins."""

from pathlib import Path

import polars as pl
import pytest

from lattice.profile import Profile
from lattice.runtime.exec import run_source
from lattice.typesys import Scalar, Schema

ITEMS = Schema.from_pairs(
    [("id", Scalar.INT), ("score", Scalar.FLOAT)], keys=[("id",)], nullable=["score"]
)
CSV = "id,score\n1,5.0\n2,\n3,9.0\n4,1.0\n"


def _profile(null_order: str | None) -> Profile:
    if null_order is None:
        return Profile(schemas={"Items": ITEMS})
    return Profile(schemas={"Items": ITEMS}, null_order=null_order)


def _run(line: str, tmp_path: Path, null_order: str | None) -> pl.DataFrame:
    src = (
        f'# lattice: v1\nt = load("items.csv", schema=Items)\ns = {line}\nwrite(s, "out.parquet")\n'
    )
    (tmp_path / "items.csv").write_text(CSV)
    result, run = run_source(src, _profile(null_order), tmp_path)
    assert result.ok, result.diagnostics
    assert run is not None
    return pl.read_parquet(tmp_path / "out.parquet")


@pytest.mark.parametrize(
    ("null_order", "desc", "ids"),
    [
        (None, False, [4, 1, 3, 2]),
        ("last", False, [4, 1, 3, 2]),
        ("last", True, [3, 1, 4, 2]),
        ("smallest", False, [2, 4, 1, 3]),
        ("smallest", True, [3, 1, 4, 2]),
    ],
)
def test_sort_without_nulls_uses_the_profile_order(
    tmp_path: Path, null_order: str | None, desc: bool, ids: list[int]
) -> None:
    out = _run(f'sort(t, by=["score"], desc={desc})', tmp_path, null_order)
    assert out["id"].to_list() == ids


def test_an_explicit_nulls_wins_over_the_profile(tmp_path: Path) -> None:
    out = _run('sort(t, by=["score"], nulls="last")', tmp_path, "smallest")
    assert out["id"].to_list() == [4, 1, 3, 2]
    out = _run('sort(t, by=["score"], nulls="first")', tmp_path, "last")
    assert out["id"].to_list() == [2, 4, 1, 3]


@pytest.mark.parametrize(
    ("null_order", "n"),
    [("last", {4: 1, 1: 2, 3: 3, 2: 4}), ("smallest", {2: 1, 4: 2, 1: 3, 3: 4})],
)
def test_window_order_uses_the_profile_order(
    tmp_path: Path, null_order: str, n: dict[int, int]
) -> None:
    out = _run('window(t, order=["score", "id"], n=win.row_number())', tmp_path, null_order)
    assert dict(zip(out["id"].to_list(), out["n"].to_list(), strict=True)) == n


def test_window_descending_smallest_puts_nulls_last(tmp_path: Path) -> None:
    line = 'window(t, order=["score", "id"], desc=["score"], n=win.row_number())'
    out = _run(line, tmp_path, "smallest")
    assert dict(zip(out["id"].to_list(), out["n"].to_list(), strict=True)) == {
        3: 1,
        1: 2,
        4: 3,
        2: 4,
    }


def test_an_unknown_null_order_is_refused() -> None:
    with pytest.raises(ValueError, match="null_order"):
        Profile(schemas={}, null_order="first")
