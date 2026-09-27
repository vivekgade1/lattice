"""agg.at_max / agg.at_min(x, of=o): value where o is largest / smallest; first/last rejected."""

from pathlib import Path

import polars as pl

from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.profile import Profile
from lattice.runtime.exec import run_source
from lattice.typesys import Scalar, Schema

PRICES = Schema.from_pairs(
    [("product_id", Scalar.INT), ("valid_from", Scalar.DATE), ("price", Scalar.FLOAT)]
)
PROFILE = Profile(schemas={"Prices": PRICES})
CSV = "product_id,valid_from,price\n1,2024-01-01,10.0\n1,2024-06-01,7.0\n2,2024-03-01,5.0\n"


def _program(agg: str, *, pre_sort: bool = False) -> str:
    src = '# lattice: v1\np = load("prices.csv", schema=Prices)\n'
    source_table = "p"
    if pre_sort:
        src += 'ps = sort(p, by=["product_id", "valid_from"], desc=[False, True])\n'
        source_table = "ps"
    return src + (
        f'g = group({source_table}, by=["product_id"], v={agg})\n'
        'write(g, "out.parquet")\n'
    )


def _values(src: str, tmp_path: Path) -> list[float]:
    (tmp_path / "prices.csv").write_text(CSV)
    result, run = run_source(src, PROFILE, tmp_path)
    assert result.ok, result.diagnostics
    return pl.read_parquet(tmp_path / "out.parquet").sort("product_id")["v"].to_list()


def test_at_max_is_value_at_latest(tmp_path: Path) -> None:
    src = _program('agg.at_max(col("price"), of=col("valid_from"))')
    assert _values(src, tmp_path) == [7.0, 5.0]


def test_at_min_is_value_at_earliest(tmp_path: Path) -> None:
    src = _program('agg.at_min(col("price"), of=col("valid_from"))')
    assert _values(src, tmp_path) == [10.0, 5.0]


def test_an_earlier_sort_does_not_change_the_pick(tmp_path: Path) -> None:
    src = _program('agg.at_max(col("price"), of=col("valid_from"))', pre_sort=True)
    assert _values(src, tmp_path) == [7.0, 5.0]


def test_first_and_last_are_rejected_with_a_pointer_to_the_new_names() -> None:
    for old, new in (("first", "at_min"), ("last", "at_max")):
        src = _program(f'agg.{old}(col("price"), order=col("valid_from"))')
        errors = [d for d in check_source(src, PROFILE).diagnostics if d.severity is Severity.ERROR]
        assert [d.code for d in errors] == ["L702"], (old, errors)
        assert f"agg.{new}" in (errors[0].hint or "")


def test_wrong_keyword_is_one_L404() -> None:
    src = _program('agg.at_max(col("price"), order=col("valid_from"))')
    codes = [d.code for d in check_source(src, PROFILE).diagnostics if d.severity is Severity.ERROR]
    assert codes == ["L404"]


def test_prompt_uses_new_names_and_warns_about_sort() -> None:
    from lattice.llm.prompt import lattice_system_prompt

    text = lattice_system_prompt()
    assert 'agg.at_max(col("price"), of=col("valid_from"))' in text
    assert "agg.first" not in text and "agg.last" not in text
    assert "earlier sort" in text
