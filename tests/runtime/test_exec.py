import ast
import datetime
from pathlib import Path

import polars as pl
import pytest

from bench.datasets import bench_profile, load_fixtures, write_fixtures
from lattice.check import check_source
from lattice.diagnostics import Span
from lattice.ir import Node, Plan, lower
from lattice.profile import Profile
from lattice.runtime import LatticeInternalError, LatticeRuntimeError
from lattice.runtime.exec import RunResult, RuntimeIssue, execute, run_source
from lattice.typesys import Scalar, Schema

ITEMS = Schema.from_pairs(
    [
        ("id", Scalar.INT),
        ("name", Scalar.STR),
        ("qty", Scalar.INT),
        ("price", Scalar.FLOAT),
        ("day", Scalar.DATE),
    ]
)
OWNERS = Schema.from_pairs([("id", Scalar.INT), ("owner", Scalar.STR)])
WEIRD = Schema.from_pairs([("sku", Scalar.STR), ("unit price", Scalar.FLOAT)])
PROFILE = Profile(schemas={"Items": ITEMS, "Owners": OWNERS, "Weird": WEIRD})

ITEMS_CSV = (
    "id,name,qty,price,day\n"
    "1,apple,3,1.5,2024-01-02\n"
    "2,pear,0,2.0,2024-02-03\n"
    "3,fig,5,NaN,2024-03-04\n"
    "4,kiwi,,4.0,\n"
    "5,apple,2,1.0,2024-01-05\n"
)
OWNERS_CSV = "id,owner\n1,ann\n2,bob\n9,zed\n"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "items.csv").write_text(ITEMS_CSV)
    (tmp_path / "owners.csv").write_text(OWNERS_CSV)
    (tmp_path / "weird.csv").write_text("sku,unit price\na,1.5\nb,2.5\n")
    return tmp_path


def prog(body: str) -> str:
    used = {n.id for n in ast.walk(ast.parse(body)) if isinstance(n, ast.Name)}
    from lattice.catalog import IMPORTABLE

    ops = sorted(used & IMPORTABLE)
    schemas = sorted(used & set(PROFILE.schemas))
    out = "# lattice: v1\n"
    if ops:
        out += f"from lattice.ops import {', '.join(ops)}\n"
    if schemas:
        out += f"from lattice.schemas import {', '.join(schemas)}\n"
    return out + body


def run(body: str, root: Path, profile: Profile = PROFILE) -> RunResult:
    result, rr = run_source(prog(body), profile, root)
    assert result.ok, result.diagnostics
    assert rr is not None
    return rr


def out(body: str, root: Path) -> pl.DataFrame:
    return run(body, root).outputs["out.parquet"]


# ---- end to end ---------------------------------------------------------------


def test_end_to_end_bench_pipeline(tmp_path):
    write_fixtures(tmp_path)
    src = """# lattice: v1
from lattice.ops import load, where, derive, group, write, col, agg
from lattice.schemas import Orders
raw = load("orders.csv", schema=Orders)
paid = where(raw, col("status") == "paid")
priced = derive(paid, revenue=col("qty") * col("unit_price"))
by_customer = group(priced, by=["customer_id"], total=agg.sum(col("revenue")))
write(by_customer, "out.parquet")
"""
    result, rr = run_source(src, bench_profile(), tmp_path)
    assert result.ok and rr is not None
    written = pl.read_parquet(tmp_path / "out.parquet")
    assert written.equals(rr.outputs["out.parquet"])
    orders = load_fixtures(tmp_path)["orders"]
    expected = (
        orders.filter(pl.col("status") == "paid")
        .group_by("customer_id", maintain_order=True)
        .agg((pl.col("qty") * pl.col("unit_price")).sum().alias("total"))
    )
    assert written.equals(expected)
    assert rr.warnings == ()


def test_run_source_with_check_errors_returns_none(root):
    result, rr = run_source("# lattice: v1\nx = 1\n", PROFILE, root)
    assert not result.ok
    assert rr is None


# ---- each op --------------------------------------------------------------------


def test_load_normalizes_nan_and_types(root):
    df = out('t = load("items.csv", schema=Items)\nwrite(t, "out.parquet")\n', root)
    assert df.schema == pl.Schema(
        {"id": pl.Int64, "name": pl.String, "qty": pl.Int64, "price": pl.Float64, "day": pl.Date}
    )
    assert df["price"].to_list() == [1.5, 2.0, None, 4.0, 1.0]
    assert df["qty"].to_list() == [3, 0, 5, None, 2]
    assert df["day"].to_list()[3] is None


def test_load_reorders_columns_to_schema(root):
    (root / "shuffled.csv").write_text("owner,id,extra\nann,1,x\n")
    df = out('t = load("shuffled.csv", schema=Owners)\nwrite(t, "out.parquet")\n', root)
    assert df.columns == ["id", "owner"]
    assert df.row(0) == (1, "ann")


def test_where_drops_null_predicates(root):
    df = out(
        't = load("items.csv", schema=Items)\n'
        'w = where(t, col("qty") >= 2)\n'
        'write(w, "out.parquet")\n',
        root,
    )
    assert df["id"].to_list() == [1, 3, 5]


def test_derive_select_rename(root):
    df = out(
        't = load("items.csv", schema=Items)\n'
        'd = derive(t, total=col("qty") * col("price"), one=1, tag=fn.upper(col("name")))\n'
        's = select(d, cols=["tag", "total", "one"])\n'
        'r = rename(s, tag="label", one="k")\n'
        'write(r, "out.parquet")\n',
        root,
    )
    assert df.columns == ["label", "total", "k"]
    assert df.schema["k"] == pl.Int64
    assert df["total"].to_list() == [4.5, 0.0, None, None, 2.0]
    assert df["label"].to_list()[0] == "APPLE"


def _raw(src: str, root: Path) -> RunResult:
    """Run source verbatim (no import header is added)."""
    result, rr = run_source(src, PROFILE, root)
    assert result.ok, result.diagnostics
    assert result.diagnostics == ()
    assert rr is not None
    return rr


def test_program_without_imports_runs(root):
    rr = _raw(
        "# lattice: v1\n"
        "@step\n"
        "def stocked(items: Table[Items], k: Int) -> Table:\n"
        '    s = where(items, col("qty") >= k)\n'
        "    return s\n"
        't = load("items.csv", schema=Items)\n'
        "s = stocked(t, k=2)\n"
        'd = derive(s, tag=fn.upper(col("name")))\n'
        'g = group(d, by=["tag"], n=agg.count())\n'
        'write(g, "out.parquet")\n',
        root,
    )
    df = rr.outputs["out.parquet"]
    assert df.rows() == [("APPLE", 2), ("FIG", 1)]


def test_derive_overwrite_same_type_in_place(root):
    df = _raw(
        "# lattice: v1\n"
        't = load("items.csv", schema=Items)\n'
        'd = derive(t, qty=col("qty") + 10)\n'
        'write(d, "out.parquet")\n',
        root,
    ).outputs["out.parquet"]
    assert df.columns == ["id", "name", "qty", "price", "day"]
    assert df.schema["qty"] == pl.Int64
    assert df["qty"].to_list() == [13, 10, 15, None, 12]


def test_derive_overwrite_changes_type_schema_matches(root):
    src = (
        "# lattice: v1\n"
        't = load("items.csv", schema=Items)\n'
        'd = derive(t, qty=col("qty") * 0.5, name=fn.len(col("name")))\n'
        'write(d, "out.parquet")\n'
    )
    result = check_source(src, PROFILE)
    assert result.ok, result.diagnostics
    predicted = [n for n in lower(result.program).nodes if n.op == "derive"][0].schema
    assert predicted.columns == (
        ("id", Scalar.INT),
        ("name", Scalar.INT),
        ("qty", Scalar.FLOAT),
        ("price", Scalar.FLOAT),
        ("day", Scalar.DATE),
    )
    df = _raw(src, root).outputs["out.parquet"]  # the runtime schema assertion passes
    assert df.schema == pl.Schema(
        {"id": pl.Int64, "name": pl.Int64, "qty": pl.Float64, "price": pl.Float64, "day": pl.Date}
    )
    assert df["qty"].to_list() == [1.5, 0.0, 2.5, None, 1.0]


def test_derive_kwargs_see_the_input_table(root):
    df = _raw(
        "# lattice: v1\n"
        't = load("items.csv", schema=Items)\n'
        'd = derive(t, qty=col("qty") * 100, big=col("qty") > 2)\n'
        'write(d, "out.parquet")\n',
        root,
    ).outputs["out.parquet"]
    # 'big' compares the ORIGINAL qty (3, 0, 5, null, 2), not qty * 100
    assert df["big"].to_list() == [True, False, True, None, False]
    assert df["qty"].to_list() == [300, 0, 500, None, 200]


def test_rename_swap(root):
    df = out(
        't = load("owners.csv", schema=Owners)\n'
        'r = rename(t, id="owner", owner="id")\n'
        'write(r, "out.parquet")\n',
        root,
    )
    assert df.columns == ["owner", "id"]
    assert df.schema["owner"] == pl.Int64


def test_group_keeps_first_seen_order_and_null_keys(root):
    df = out(
        't = load("items.csv", schema=Items)\n'
        'g = group(t, by=["name"], n=agg.count(), q=agg.sum(col("qty")), '
        'm=agg.mean(col("price")), lo=agg.min(col("day")), hi=agg.max(col("name")))\n'
        'write(g, "out.parquet")\n',
        root,
    )
    assert df["name"].to_list() == ["apple", "pear", "fig", "kiwi"]
    assert df["n"].to_list() == [2, 1, 1, 1]
    assert df["q"].to_list() == [5, 0, 5, 0]  # kiwi: sum over nulls -> 0
    assert df["m"].to_list() == [1.25, 2.0, None, 4.0]
    assert df["lo"].to_list()[0] == datetime.date(2024, 1, 2)
    assert df.schema["n"] == pl.Int64


def test_group_literal_aggregate_counts_rows(root):
    df = out(
        't = load("items.csv", schema=Items)\n'
        'g = group(t, by=["name"], ones=agg.sum(1))\n'
        'write(g, "out.parquet")\n',
        root,
    )
    assert df["ones"].to_list() == [2, 1, 1, 1]


def test_join_inner_and_left(root):
    body = (
        't = load("items.csv", schema=Items)\n'
        'o = load("owners.csv", schema=Owners)\n'
        'i = join(t, o, on=["id"])\n'
        'l = join(t, o, on=["id"], how="left")\n'
        'write(i, "out.parquet")\n'
        'write(l, "left.csv")\n'
    )
    rr = run(body, root)
    inner = rr.outputs["out.parquet"]
    assert inner.columns == ["id", "name", "qty", "price", "day", "owner"]
    assert sorted(inner["id"].to_list()) == [1, 2]
    left = rr.outputs["left.csv"]
    assert left.height == 5
    assert left["owner"].to_list() == ["ann", "bob", None, None, None]
    assert (root / "left.csv").exists()


def test_sort_and_limit(root):
    df = out(
        't = load("items.csv", schema=Items)\n'
        's = sort(t, by=["price", "id"], desc=True)\n'
        "l = limit(s, n=3)\n"
        'write(l, "out.parquet")\n',
        root,
    )
    assert df["id"].to_list() == [4, 2, 1]
    asc = out(
        't = load("items.csv", schema=Items)\ns = sort(t, by=["price"])\nwrite(s, "out.parquet")\n',
        root,
    )
    assert asc["id"].to_list() == [5, 1, 2, 4, 3]  # nulls last


def test_step_inlined_execution(root):
    df = out(
        "@step\n"
        "def big(items: Table[Items], k: Int) -> Table:\n"
        '    kept = where(items, col("qty") >= k)\n'
        "    return kept\n"
        't = load("items.csv", schema=Items)\n'
        "a = big(t, k=3)\n"
        "b = big(t, k=5)\n"
        'j = select(b, cols=["id", "qty"])\n'
        'write(a, "out.parquet")\n'
        'write(j, "b.parquet")\n',
        root,
    )
    assert df["id"].to_list() == [1, 3]


def test_division_by_zero_warning(root):
    rr = run(
        't = load("items.csv", schema=Items)\n'
        'd = derive(t, per=col("price") / col("qty"))\n'
        'write(d, "out.parquet")\n',
        root,
    )
    df = rr.outputs["out.parquet"]
    assert df["per"].to_list() == [0.5, None, None, None, 0.5]
    assert rr.warnings == (RuntimeIssue("R301", Span(5, 5), rr.warnings[0].message, 1),)
    assert "by zero" in rr.warnings[0].message


# ---- review focus -------------------------------------------------------------


def test_empty_table_through_group_join_derive(root):
    rr = run(
        't = load("items.csv", schema=Items)\n'
        'o = load("owners.csv", schema=Owners)\n'
        'e = where(t, col("qty") > 100)\n'
        'd = derive(e, x=col("qty") * 2.0, one=1)\n'
        'j = join(d, o, on=["id"], how="left")\n'
        'g = group(j, by=["owner"], n=agg.count(), s=agg.sum(col("x")))\n'
        'whole = group(e, by=[], n=agg.count(), s=agg.sum(col("qty")), '
        'm=agg.mean(col("price")), lo=agg.min(col("name")), hi=agg.max(col("day")))\n'
        'write(g, "out.parquet")\n'
        'write(whole, "whole.parquet")\n',
        root,
    )
    g = rr.outputs["out.parquet"]
    assert g.height == 0
    assert g.schema == pl.Schema({"owner": pl.String, "n": pl.Int64, "s": pl.Float64})
    whole = rr.outputs["whole.parquet"]
    assert whole.rows() == [(0, 0, None, None, None)]
    assert whole.schema == pl.Schema(
        {"n": pl.Int64, "s": pl.Int64, "m": pl.Float64, "lo": pl.String, "hi": pl.Date}
    )


def test_non_identifier_column_name(root):
    df = out(
        't = load("weird.csv", schema=Weird)\n'
        'd = derive(t, doubled=col("unit price") * 2)\n'
        'w = where(d, col("unit price") > 2.0)\n'
        'write(w, "out.parquet")\n',
        root,
    )
    assert df.columns == ["sku", "unit price", "doubled"]
    assert df.rows() == [("b", 2.5, 5.0)]


def test_bad_date_is_r101_at_load_line(root):
    (root / "bad.csv").write_text("id,name,qty,price,day\n1,a,1,1.0,2024-13-45\n")
    src = prog(
        't = load("items.csv", schema=Items)\n'
        'b = load("bad.csv", schema=Items)\n'
        'write(b, "out.parquet")\n'
    )
    with pytest.raises(LatticeRuntimeError) as exc:
        run_source(src, PROFILE, root)
    assert exc.value.code == "R101"
    assert exc.value.span.line == 5
    assert "bad.csv" in exc.value.message


def test_missing_file_is_r101(root):
    with pytest.raises(LatticeRuntimeError) as exc:
        run('t = load("nope.csv", schema=Items)\nwrite(t, "out.parquet")\n', root)
    assert exc.value.code == "R101"


def test_missing_column_is_r101(root):
    (root / "short.csv").write_text("id\n1\n")
    with pytest.raises(LatticeRuntimeError) as exc:
        run('t = load("short.csv", schema=Owners)\nwrite(t, "out.parquet")\n', root)
    assert exc.value.code == "R101"
    assert "owner" in exc.value.message


# ---- manual plans: sandbox, internal checks, limits -------------------------------


def _load_node(path: str, schema: Schema = OWNERS) -> Node:
    return Node("t", "load", (), {"path": path}, schema, Span(2, 5))


def test_path_escape_is_r102(root):
    plan = Plan((_load_node("../owners.csv"),))
    with pytest.raises(LatticeRuntimeError) as exc:
        execute(plan, root)
    assert exc.value.code == "R102"
    w = Node("_write0", "write", ("t",), {"path": "/tmp/x.parquet"}, None, Span(3, 1))
    with pytest.raises(LatticeRuntimeError) as exc:
        execute(Plan((_load_node("owners.csv"), w)), root)
    assert exc.value.code == "R102"


def test_schema_mismatch_is_internal_e901(root):
    wrong = Schema.from_pairs([("id", Scalar.INT), ("owner", Scalar.STR), ("x", Scalar.INT)])
    plan = Plan(
        (
            _load_node("owners.csv"),
            Node("s", "select", ("t",), {"cols": ("id", "owner")}, wrong, Span(3, 5)),
        )
    )
    with pytest.raises(LatticeInternalError, match="E901"):
        execute(plan, root)


def test_unknown_op_is_internal_error(root):
    plan = Plan((_load_node("owners.csv"), Node("x", "pivot", ("t",), {}, OWNERS, Span(3, 1))))
    with pytest.raises(LatticeInternalError):
        execute(plan, root)


def test_row_limit_r201(root):
    with pytest.raises(LatticeRuntimeError) as exc:
        execute(Plan((_load_node("owners.csv"),)), root, max_rows=2)
    assert exc.value.code == "R201"


def test_time_limit_r202(root):
    plan = Plan(
        (
            _load_node("owners.csv"),
            Node("l", "limit", ("t",), {"n": 1}, OWNERS, Span(3, 5)),
        )
    )
    with pytest.raises(LatticeRuntimeError) as exc:
        execute(plan, root, time_limit_s=0.0)
    assert exc.value.code == "R202"


def test_execute_does_not_mutate_ast(root):
    src = prog(
        "@step\n"
        "def big(items: Table[Items], k: Int) -> Table:\n"
        '    kept = where(items, col("qty") >= k)\n'
        "    return kept\n"
        't = load("items.csv", schema=Items)\n'
        "a = big(t, k=3)\n"
        'd = derive(a, r=col("price") / col("qty"))\n'
        'write(d, "out.parquet")\n'
    )
    result = check_source(src, PROFILE)
    assert result.program is not None
    before = ast.dump(result.program.module, include_attributes=True)
    execute(lower(result.program), root)
    assert ast.dump(result.program.module, include_attributes=True) == before
