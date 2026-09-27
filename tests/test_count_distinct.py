"""agg.count_distinct(x): number of distinct non-null values of x per group."""

from pathlib import Path

import polars as pl

from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.profile import Profile
from lattice.runtime.exec import run_source
from lattice.typesys import Scalar, Schema

ORDERS = Schema.from_pairs(
    [("order_id", Scalar.INT), ("region", Scalar.STR), ("customer_id", Scalar.INT)],
    nullable=("region", "customer_id"),
)
PROFILE = Profile(schemas={"Orders": ORDERS})
CSV = "order_id,region,customer_id\n1,n,7\n2,n,7\n3,n,8\n4,s,9\n5,s,\n6,,7\n"


def _run(agg: str, tmp_path: Path, by: str = '["region"]') -> pl.DataFrame:
    src = (
        "# lattice: v1\n"
        'o = load("orders.csv", schema=Orders)\n'
        f"g = group(o, by={by}, n={agg})\n"
        'write(g, "out.parquet")\n'
    )
    (tmp_path / "orders.csv").write_text(CSV)
    result, run = run_source(src, PROFILE, tmp_path)
    assert result.ok, result.diagnostics
    return pl.read_parquet(tmp_path / "out.parquet")


def test_counts_distinct_non_null_values_per_group(tmp_path: Path) -> None:
    out = _run('agg.count_distinct(col("customer_id"))', tmp_path).sort("region", nulls_last=True)
    assert out["region"].to_list() == ["n", "s", None]
    assert out["n"].to_list() == [2, 1, 1]
    assert out.schema["n"] == pl.Int64


def test_whole_table_and_empty(tmp_path: Path) -> None:
    assert _run('agg.count_distinct(col("customer_id"))', tmp_path, by="[]")["n"].to_list() == [3]


def test_type_errors_and_arity() -> None:
    for bad in ("agg.count_distinct()", 'agg.count_distinct(col("region"), col("order_id"))'):
        src = (
            "# lattice: v1\n"
            'o = load("orders.csv", schema=Orders)\n'
            f'g = group(o, by=["region"], n={bad})\n'
            'write(g, "out.parquet")\n'
        )
        codes = [
            d.code for d in check_source(src, PROFILE).diagnostics if d.severity is Severity.ERROR
        ]
        assert codes == ["L404"], (bad, codes)


def test_prompt_documents_count_distinct() -> None:
    from lattice.llm.prompt import BASELINE_SYSTEM, lattice_system_prompt

    assert "agg.count_distinct" in lattice_system_prompt()
    assert "nunique" in BASELINE_SYSTEM  # the pandas arm gets the equivalent hint
