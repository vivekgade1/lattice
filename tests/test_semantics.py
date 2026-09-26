"""One test per row of spec §5.7 (written runtime semantics), plus the §5.10 null rules."""

from pathlib import Path

import polars as pl
import pytest

from lattice.check import check_source
from lattice.profile import Profile
from lattice.runtime.exec import RunResult, run_source
from lattice.typesys import Scalar, Schema

T = Schema.from_pairs(
    [("k", Scalar.STR), ("a", Scalar.INT), ("x", Scalar.FLOAT), ("s", Scalar.STR)]
)
PROFILE = Profile(schemas={"T": T})
CSV = "k,a,x,s\np,4,2.0,10\np,0,NaN,x\nq,,0.0,\nq,-3,,7\n"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "t.csv").write_text(CSV)
    return tmp_path


def run(body: str, root: Path, imports: str) -> RunResult:
    src = (
        "# lattice: v1\n"
        f"from lattice.ops import {imports}\n"
        "from lattice.schemas import T\n"
        't = load("t.csv", schema=T)\n' + body
    )
    result, rr = run_source(src, PROFILE, root)
    assert result.ok, [d.render() for d in result.diagnostics]
    assert rr is not None
    return rr


def test_division_by_zero_is_null_plus_warning(root):
    rr = run(
        'd = derive(t, q=col("x") / col("a"), r=col("a") % col("a"))\nwrite(d, "out.parquet")\n',
        root,
        "load, derive, write, col",
    )
    df = rr.outputs["out.parquet"]
    assert df["q"].to_list() == [0.5, None, None, None]
    assert df["r"].to_list() == [0, None, None, 0]
    assert [(w.code, w.count) for w in rr.warnings] == [("R301", 2)]
    assert rr.warnings[0].span.line == 5


def test_nan_is_null_after_load(root):
    rr = run('write(t, "out.parquet")\n', root, "load, write")
    x = rr.outputs["out.parquet"]["x"]
    assert x.to_list() == [2.0, None, 0.0, None]
    assert x.is_nan().fill_null(False).sum() == 0


def test_null_propagates_in_arithmetic(root):
    rr = run(
        'd = derive(t, y=col("a") + 1, z=col("x") * 2)\nwrite(d, "out.parquet")\n',
        root,
        "load, derive, write, col",
    )
    df = rr.outputs["out.parquet"]
    assert df["y"].to_list() == [5, 1, None, -2]
    assert df["z"].to_list() == [4.0, None, 0.0, None]


def test_where_treats_null_predicate_as_false(root):
    rr = run(
        'w = where(t, (col("a") > -10) | (col("x") > 100.0))\nwrite(w, "out.parquet")\n',
        root,
        "load, where, write, col",
    )
    # row 3 has a null `a`: null | false -> null -> dropped
    assert rr.outputs["out.parquet"]["a"].to_list() == [4, 0, -3]


def test_bool_connectives_only_on_bool():
    src = (
        "# lattice: v1\n"
        "from lattice.ops import load, where, write, col\n"
        "from lattice.schemas import T\n"
        't = load("t.csv", schema=T)\n'
        'w = where(t, col("a") & (col("x") > 1.0))\n'
        'write(w, "out.parquet")\n'
    )
    r = check_source(src, PROFILE)
    assert not r.ok
    assert [d.code for d in r.diagnostics] == ["L402"]


def test_aggregates_over_empty_input(root):
    rr = run(
        'e = where(t, col("a") > 100)\n'
        'g = group(e, by=[], n=agg.count(), s=agg.sum(col("a")), sx=agg.sum(col("x")), '
        'm=agg.mean(col("x")), lo=agg.min(col("a")), hi=agg.max(col("k")))\n'
        'per_key = group(e, by=["k"], n=agg.count())\n'
        'write(g, "out.parquet")\n'
        'write(per_key, "per_key.parquet")\n',
        root,
        "load, where, group, write, col, agg",
    )
    g = rr.outputs["out.parquet"]
    assert g.rows() == [(0, 0, 0.0, None, None, None)]
    assert rr.outputs["per_key.parquet"].height == 0


def test_aggregates_skip_nulls(root):
    rr = run(
        'q = where(t, col("k") == "q")\n'
        'g = group(q, by=["k"], s=agg.min(col("s")), n=agg.count(), m=agg.mean(col("a")))\n'
        'write(g, "out.parquet")\n',
        root,
        "load, where, group, write, col, agg",
    )
    assert rr.outputs["out.parquet"].rows() == [("q", "7", 2, -3.0)]


def test_evaluation_order_is_deterministic(root):
    body = (
        'd = derive(t, y=col("a") * 2)\n'
        'g = group(d, by=["k"], total=agg.sum(col("y")), n=agg.count())\n'
        'o = sort(d, by=["k"])\n'
        'write(g, "out.parquet")\n'
        'write(o, "sorted.parquet")\n'
    )
    imports = "load, derive, group, sort, write, col, agg"
    first = run(body, root, imports)
    second = run(body, root, imports)
    for path in ("out.parquet", "sorted.parquet"):
        assert first.outputs[path].equals(second.outputs[path])
    # group keeps first-seen key order; sort is stable
    assert first.outputs["out.parquet"]["k"].to_list() == ["p", "q"]
    assert first.outputs["sorted.parquet"]["a"].to_list() == [4, 0, None, -3]


def test_float_equality_warns_but_runs(root):
    src = (
        "# lattice: v1\n"
        "from lattice.ops import load, where, write, col\n"
        "from lattice.schemas import T\n"
        't = load("t.csv", schema=T)\n'
        'w = where(t, col("x") == 2.0)\n'
        'write(w, "out.parquet")\n'
    )
    result, rr = run_source(src, PROFILE, root)
    assert result.ok
    assert [(d.code, d.severity.value) for d in result.diagnostics] == [("L406", "warning")]
    assert rr is not None
    assert rr.outputs["out.parquet"].height == 1


def test_math_domain_errors_are_null_plus_warning(root):
    rr = run(
        'd = derive(t, l=fn.log(col("a")), r=fn.sqrt(col("x")))\nwrite(d, "out.parquet")\n',
        root,
        "load, derive, write, col, fn",
    )
    df = rr.outputs["out.parquet"]
    assert df["l"].to_list()[1:] == [None, None, None]
    assert df["r"].to_list() == [pytest.approx(2.0**0.5), None, 0.0, None]
    assert [(w.code, w.count) for w in rr.warnings] == [("R302", 2)]


def test_failed_casts_are_null_plus_warning(root):
    rr = run(
        'd = derive(t, n=fn.to_int(col("s")))\nwrite(d, "out.parquet")\n',
        root,
        "load, derive, write, col, fn",
    )
    assert rr.outputs["out.parquet"]["n"].to_list() == [10, None, None, 7]
    assert [(w.code, w.count) for w in rr.warnings] == [("R303", 1)]


def test_output_matches_predicted_schema(root):
    src = (
        "# lattice: v1\n"
        "from lattice.ops import load, derive, write, col, when\n"
        "from lattice.schemas import T\n"
        't = load("t.csv", schema=T)\n'
        'd = derive(t, w=when(col("a") > 0, then=1, otherwise=0.5), one=1, f=col("a") / 2)\n'
        'write(d, "out.parquet")\n'
    )
    _, rr = run_source(src, PROFILE, root)
    assert rr is not None
    written = pl.read_parquet(root / "out.parquet")
    assert written.schema["w"] == pl.Float64
    assert written.schema["one"] == pl.Int64
    assert written.schema["f"] == pl.Float64
