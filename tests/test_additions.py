"""Three post-benchmark additions (spec §5.11 item 13):

1. ``agg.count(x)`` counts the non-null values of ``x`` (``agg.count()`` still counts rows).
2. ``join(left, right, on=[], how=...)`` attaches a right table that has at most one row
   (tracked statically as ``TableT.at_most_one_row``).
3. Named scalar constants: ``name = <column-free scalar expression>``.
"""

from __future__ import annotations

import ast
import datetime
from pathlib import Path

import polars as pl
import pytest

from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.ir import lower
from lattice.profile import Profile
from lattice.runtime.exec import run_source
from lattice.typesys import Scalar, Schema, TableT

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
PROFILE = Profile(schemas={"Items": ITEMS, "Owners": OWNERS})

ITEMS_CSV = (
    "id,name,qty,price,day\n"
    "1,apple,3,1.5,2024-01-02\n"
    "2,pear,0,2.0,2024-02-03\n"
    "3,fig,5,,2024-03-04\n"
    "4,kiwi,,4.0,\n"
    "5,apple,2,1.0,2024-01-05\n"
)
OWNERS_CSV = "id,owner\n1,ann\n2,bob\n9,zed\n"

H = "# lattice: v1\n"
LOAD = 'items = load("items.csv", schema=Items)\n'


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "items.csv").write_text(ITEMS_CSV)
    (tmp_path / "owners.csv").write_text(OWNERS_CSV)
    return tmp_path


def src(body: str) -> str:
    return H + LOAD + body


def diags(body: str):
    return check_source(src(body), PROFILE).diagnostics


def errors(body: str) -> list[str]:
    return [d.code for d in diags(body) if d.severity is Severity.ERROR]


def run(body: str, root: Path) -> pl.DataFrame:
    result, rr = run_source(src(body), PROFILE, root)
    assert result.ok, [d.render() for d in result.diagnostics]
    assert rr is not None
    return rr.outputs["out.parquet"]


def items_frame(root: Path) -> pl.DataFrame:
    return pl.read_csv(
        root / "items.csv",
        schema_overrides={"qty": pl.Int64, "price": pl.Float64, "day": pl.Date},
    )


# ---- 1. agg.count(x) ----------------------------------------------------------


def test_count_of_column_counts_non_null_values(root):
    df = run(
        'g = group(items, by=["name"], rows=agg.count(), n_qty=agg.count(col("qty")), '
        'n_price=agg.count(col("price")))\n'
        'write(g, "out.parquet")\n',
        root,
    )
    assert df.schema == pl.Schema(
        {"name": pl.String, "rows": pl.Int64, "n_qty": pl.Int64, "n_price": pl.Int64}
    )
    by_name = {r["name"]: (r["rows"], r["n_qty"], r["n_price"]) for r in df.iter_rows(named=True)}
    assert by_name == {
        "apple": (2, 2, 2),
        "pear": (1, 1, 1),
        "fig": (1, 1, 0),
        "kiwi": (1, 0, 1),
    }


def test_count_of_column_whole_table_and_empty(root):
    df = run(
        'none = where(items, col("qty") > 100)\n'
        'g = group(none, by=[], n=agg.count(col("qty")))\n'
        'write(g, "out.parquet")\n',
        root,
    )
    assert df.to_dicts() == [{"n": 0}]


def test_count_of_any_type_and_expression(root):
    df = run(
        'g = group(items, by=[], n_day=agg.count(col("day")), '
        'n_big=agg.count(when(col("qty") > 2, then=col("qty"), otherwise=col("qty") * 0)))\n'
        'write(g, "out.parquet")\n',
        root,
    )
    assert df.to_dicts() == [{"n_day": 4, "n_big": 4}]


@pytest.mark.parametrize(
    ("expr", "code"),
    [
        ('agg.count(col("nope"))', "L401"),
        ('agg.count(col("qty"), col("price"))', "L404"),
        ('agg.count(x=col("qty"))', "L404"),
    ],
)
def test_count_argument_errors(expr, code):
    assert errors(f'g = group(items, by=[], n={expr})\nwrite(g, "out.parquet")\n') == [code]


# ---- 2. single-row cross join -------------------------------------------------


def test_share_of_total_matches_polars(root):
    df = run(
        'by_name = group(items, by=["name"], qty=agg.sum(col("qty")))\n'
        'total = group(items, by=[], all_qty=agg.sum(col("qty")))\n'
        'with_total = join(by_name, total, on=[], how="inner")\n'
        'share = derive(with_total, share=col("qty") / col("all_qty"))\n'
        'out = select(share, cols=["name", "share"])\n'
        'write(out, "out.parquet")\n',
        root,
    )
    items = items_frame(root)
    total = items["qty"].sum()
    expected = (
        items.group_by("name", maintain_order=True)
        .agg(pl.col("qty").sum())
        .select("name", (pl.col("qty").cast(pl.Float64) / total).alias("share"))
    )
    assert df.equals(expected)


def test_on_empty_needs_single_row_right():
    ds = diags(
        'owners = load("owners.csv", schema=Owners)\n'
        'j = join(items, owners, on=[], how="inner")\n'
        'write(j, "out.parquet")\n'
    )
    assert [d.code for d in ds if d.severity is Severity.ERROR] == ["L404"]
    assert "at most one row" in ds[0].message
    assert "group(t, by=[]" in ds[0].message


def test_on_empty_name_collision_is_L416():
    body = (
        'total = group(items, by=[], qty=agg.sum(col("qty")))\n'
        'j = join(items, total, on=[], how="inner")\n'
        'write(j, "out.parquet")\n'
    )
    assert errors(body) == ["L416"]


def test_left_cross_join_with_empty_right_keeps_left_rows(root):
    df = run(
        'none = where(items, col("qty") > 100)\n'
        'first = limit(none, n=1)\n'
        'picked = select(first, cols=["price"])\n'
        'renamed = rename(picked, price="top_price")\n'
        'j = join(items, renamed, on=[], how="left")\n'
        'write(j, "out.parquet")\n',
        root,
    )
    assert df.height == 5
    assert df["top_price"].to_list() == [None] * 5
    assert df.schema["top_price"] == pl.Float64
    assert df["id"].to_list() == [1, 2, 3, 4, 5]


def test_inner_cross_join_with_empty_right_is_empty(root):
    df = run(
        'none = where(items, col("qty") > 100)\n'
        'first = limit(none, n=1)\n'
        'picked = select(first, cols=["price"])\n'
        'renamed = rename(picked, price="top_price")\n'
        'j = join(items, renamed, on=[], how="inner")\n'
        'write(j, "out.parquet")\n',
        root,
    )
    assert df.height == 0
    assert df.columns == ["id", "name", "qty", "price", "day", "top_price"]


def test_left_cross_join_with_one_row(root):
    df = run(
        'owners = load("owners.csv", schema=Owners)\n'
        'o = sort(owners, by=["owner"], desc=True)\n'
        'top = limit(o, n=1)\n'
        'best = rename(top, id="owner_id")\n'
        'j = join(items, best, on=[], how="left")\n'
        'write(j, "out.parquet")\n',
        root,
    )
    assert df["owner"].to_list() == ["zed"] * 5
    assert df["owner_id"].to_list() == [9] * 5


def _flags(body: str) -> dict[str, bool]:
    result = check_source(src(body), PROFILE)
    assert result.ok, [d.render() for d in result.diagnostics]
    program = result.program
    assert program is not None
    out: dict[str, bool] = {}

    def visit(stmts):
        for stmt in stmts:
            if isinstance(stmt, ast.Assign):
                t = program.types.get(id(stmt.value))
                if isinstance(t, TableT):
                    out[stmt.targets[0].id] = t.at_most_one_row
            elif isinstance(stmt, ast.FunctionDef):
                visit(stmt.body)

    visit(program.module.body)
    return out


def test_at_most_one_row_propagation():
    flags = _flags(
        'total = group(items, by=[], n=agg.count(), q=agg.sum(col("qty")))\n'
        "w = where(total, col(\"n\") > 0)\n"
        'd = derive(w, m=col("n") * 2)\n'
        's = select(d, cols=["m", "q"])\n'
        'r = rename(s, m="mm")\n'
        'o = sort(r, by=["mm"])\n'
        "lim = limit(o, n=5)\n"
        "one = limit(items, n=1)\n"
        "two = limit(items, n=2)\n"
        'keyed = group(items, by=["name"], n=agg.count())\n'
        'owners = load("owners.csv", schema=Owners)\n'
        'kj = join(one, owners, on=["id"], how="inner")\n'
        'cj = join(items, lim, on=[], how="inner")\n'
        'cj1 = join(one, lim, on=[], how="left")\n'
        'write(cj, "out.parquet")\n'
        'write(cj1, "o2.parquet")\n'
        'write(kj, "o3.parquet")\n'
        'write(two, "o4.parquet")\n'
        'write(keyed, "o5.parquet")\n'
    )
    assert flags == {
        "items": False,
        "total": True,
        "w": True,
        "d": True,
        "s": True,
        "r": True,
        "o": True,
        "lim": True,
        "one": True,
        "two": False,
        "keyed": False,
        "owners": False,
        "kj": False,
        "cj": False,
        "cj1": True,
    }


def test_at_most_one_row_through_steps(root):
    body = (
        "@step\n"
        "def totals(t: Table[Items]) -> Table:\n"
        '    g = group(t, by=[], all_qty=agg.sum(col("qty")))\n'
        "    return g\n"
        "@step\n"
        "def concrete(t: Table[Items]) -> Table[Owners]:\n"
        "    one = limit(t, n=1)\n"
        '    o = select(one, cols=["id", "name"])\n'
        '    r = rename(o, name="owner")\n'
        "    return r\n"
        "@step\n"
        "def many(t: Table[Items]) -> Table:\n"
        '    g = group(t, by=["name"], all_qty=agg.sum(col("qty")))\n'
        "    return g\n"
        "tot = totals(items)\n"
        "c = concrete(items)\n"
        "m = many(items)\n"
        'j = join(items, tot, on=[], how="inner")\n'
        'renamed = rename(c, id="owner_id")\n'
        'j2 = join(j, renamed, on=[], how="left")\n'
        'write(j2, "out.parquet")\n'
        'write(m, "o2.parquet")\n'
    )
    flags = _flags(body)
    assert flags["tot"] is True and flags["c"] is True and flags["m"] is False
    df = run(body, root)
    assert df["all_qty"].to_list() == [10] * 5
    assert df["owner"].to_list() == ["apple"] * 5

    bad = body.replace("j = join(items, tot,", "j = join(items, m,")
    codes = [d.code for d in check_source(src(bad), PROFILE).diagnostics if d.severity == "error"]
    assert codes == ["L404"]


def test_step_param_matching_ignores_row_flag(root):
    body = (
        "@step\n"
        "def f(t: Table[Items]) -> Table:\n"
        '    w = where(t, col("qty") > 0)\n'
        "    return w\n"
        "one = limit(items, n=1)\n"
        "r = f(one)\n"
        'write(r, "out.parquet")\n'
    )
    assert run(body, root).height == 1


def test_row_estimate_for_cross_join(root):
    from lattice.runtime.exec import execute

    body = (
        'total = group(items, by=[], all_qty=agg.sum(col("qty")))\n'
        'j = join(items, total, on=[], how="inner")\n'
        'write(j, "out.parquet")\n'
    )
    result = check_source(src(body), PROFILE)
    assert result.program is not None
    plan = lower(result.program)
    from lattice.runtime import LatticeRuntimeError

    with pytest.raises(LatticeRuntimeError) as err:
        execute(plan, root, max_rows=4)
    assert err.value.code == "R201"
    assert execute(plan, root, max_rows=5).outputs["out.parquet"].height == 5


# ---- 3. named constants -------------------------------------------------------


def test_module_constants_in_where_and_derive(root):
    df = run(
        'cutoff = fn.to_date("2024-02-01", format="%Y-%m-%d")\n'
        "min_qty = 1\n"
        "rate = 0.5\n"
        'recent = where(items, (col("day") < cutoff) & (col("qty") >= min_qty))\n'
        'priced = derive(recent, half=col("price") * rate, flag=min_qty)\n'
        'write(priced, "out.parquet")\n',
        root,
    )
    assert df["id"].to_list() == [1, 5]
    assert df["half"].to_list() == [0.75, 0.5]
    assert df["flag"].to_list() == [1, 1]


def test_constants_in_fn_keyword_arguments(root):
    df = run(
        "shift = -30\n"
        "digits = 1\n"
        "unit = \"days\"\n"
        'd = derive(items, earlier=fn.date_add(col("day"), days=shift), '
        'p=fn.round(col("price") / 3, digits=digits), '
        'gap=fn.date_diff(col("day"), fn.date_add(col("day"), days=shift), unit=unit))\n'
        'write(d, "out.parquet")\n',
        root,
    )
    assert df["earlier"].to_list()[0] == datetime.date(2023, 12, 3)
    assert df["p"].to_list()[0] == 0.5
    assert df["gap"].to_list()[0] == 30


def test_chained_constants(root):
    df = run(
        "a = 2\n"
        "b = a * 3\n"
        "c = b + a\n"
        "d = a\n"
        'x = derive(items, c=c, r=fn.round(col("price"), digits=d))\n'
        'write(x, "out.parquet")\n',
        root,
    )
    assert df["c"].to_list() == [8] * 5


def test_constants_in_step_bodies(root):
    df = run(
        "threshold = 2\n"
        "@step\n"
        "def big(t: Table[Items], bump: Int) -> Table:\n"
        "    limit_qty = threshold + bump\n"
        '    w = where(t, col("qty") > limit_qty)\n'
        "    return w\n"
        "k = 1\n"
        "r = big(items, bump=k)\n"
        'write(r, "out.parquet")\n',
        root,
    )
    assert df["id"].to_list() == [3]


def test_step_scalar_argument_may_be_a_literal_constant(root):
    df = run(
        "@step\n"
        "def cheap(t: Table[Items], hi: Float) -> Table:\n"
        '    w = where(t, col("price") < hi)\n'
        "    return w\n"
        "cap = 2\n"
        "r = cheap(items, hi=cap)\n"
        'write(r, "out.parquet")\n',
        root,
    )
    assert df["id"].to_list() == [1, 5]


def test_non_literal_constant_rejected_where_a_literal_is_needed():
    assert errors(
        "n = 1 + 1\n"
        'd = derive(items, p=fn.round(col("price"), digits=n))\n'
        'write(d, "out.parquet")\n'
    ) == ["L404"]
    assert errors(
        "@step\n"
        "def f(t: Table[Items], n: Int) -> Table:\n"
        '    w = where(t, col("qty") > n)\n'
        "    return w\n"
        "m = 1 + 1\n"
        "r = f(items, n=m)\n"
        'write(r, "out.parquet")\n'
    ) == ["L402"]


def test_constant_used_as_table_is_L419():
    ds = diags(
        'n = 3\nw = where(n, col("qty") > 1)\nwrite(w, "out.parquet")\nwrite(items, "o.csv")\n'
    )
    assert [d.code for d in ds] == ["L419"]
    assert "constant" in ds[0].message


def test_table_used_as_constant_is_L411():
    assert errors('x = items + 1\nd = derive(items, y=x)\nwrite(d, "out.parquet")\n') == ["L411"]


def test_constant_referencing_a_column_is_L302():
    assert errors('x = col("qty") * 2\nwrite(items, "out.parquet")\n') == ["L302"]
    assert errors('x = [1, 2]\nwrite(items, "out.parquet")\n') == ["L302"]
    assert errors('x = agg.sum(col("qty"))\nwrite(items, "out.parquet")\n') == ["L302"]


def test_rebinding_a_constant_is_L201():
    assert errors('n = 1\nn = 2\nd = derive(items, y=n)\nwrite(d, "out.parquet")\n') == ["L201"]
    assert errors(
        'n = 1\nn = where(items, col("qty") > 1)\nd = derive(n, y=1)\nwrite(d, "out.parquet")\n'
    ) == ["L201"]


def test_constant_named_like_a_builtin_is_L205():
    assert errors('col = 1\nwrite(items, "out.parquet")\n') == ["L205"]


def test_unused_constant_is_L204_warning():
    ds = diags('n = 1\nwrite(items, "out.parquet")\n')
    assert [(d.code, d.severity) for d in ds] == [("L204", Severity.WARNING)]


def test_constant_used_before_definition_is_L202():
    assert errors('w = where(items, col("qty") > n)\nn = 1\nwrite(w, "out.parquet")\n') == [
        "L202"
    ]
    assert errors(
        "@step\n"
        "def f(t: Table[Items]) -> Table:\n"
        '    w = where(t, col("qty") > n)\n'
        "    return w\n"
        "n = 1\n"
        "r = f(items)\n"
        'write(r, "out.parquet")\n'
    ) == ["L202"]


def test_step_body_still_cannot_read_module_tables():
    assert errors(
        "@step\n"
        "def f(t: Table[Items]) -> Table:\n"
        "    j = join(t, items, on=[\"id\"])\n"
        "    return j\n"
        "r = f(items)\n"
        'write(r, "out.parquet")\n'
    ) == ["L603"]


def test_constant_with_type_error_reported_once():
    assert errors(
        'x = fn.upper(3)\nd = derive(items, y=x, z=x)\nw = where(d, col("y") == "A")\n'
        'write(w, "out.parquet")\n'
    ) == ["L402"]


def test_constant_expansion_blowup_is_rejected_once():
    lines = ["c0 = 1\n"] + [f"c{i} = c{i - 1} + c{i - 1}\n" for i in range(1, 30)]
    body = "".join(lines) + 'd = derive(items, y=c29)\nwrite(d, "out.parquet")\n'
    assert errors(body) == ["L003"]


def _names_in_plan(plan) -> set[str]:
    names: set[str] = set()

    def walk(value) -> None:
        if isinstance(value, ast.AST):
            names.update(n.id for n in ast.walk(value) if isinstance(n, ast.Name))
        elif isinstance(value, tuple | list):
            for v in value:
                walk(v)

    for node in plan.nodes:
        for value in node.params.values():
            walk(value)
    return names


def test_ir_has_no_constant_names_left():
    body = (
        "base = 1\n"
        "cutoff = fn.to_date(\"2024-02-01\", format=\"%Y-%m-%d\")\n"
        "@step\n"
        "def f(t: Table[Items], n: Int) -> Table:\n"
        "    local_c = n + base\n"
        '    w = where(t, (col("qty") > local_c) & (col("day") < cutoff))\n'
        '    g = group(w, by=[], s=agg.sum(col("qty") * local_c), c=agg.count(col("qty")))\n'
        "    return g\n"
        "two = base + base\n"
        "r = f(items, n=base)\n"
        'd = derive(items, y=two, z=fn.date_add(col("day"), days=base))\n'
        'j = join(d, r, on=[], how="left")\n'
        'write(j, "out.parquet")\n'
    )
    result = check_source(src(body), PROFILE)
    assert result.program is not None, [d.render() for d in result.diagnostics]
    names = _names_in_plan(lower(result.program))
    assert names <= {"col", "fn", "agg", "when", "coalesce", "is_null"}, names


def test_unknown_op_alias_is_not_a_constant():
    # `head` is an alias hint (L203); the binding must not cascade as a constant
    assert errors('top = head(items, n=2)\nwrite(top, "out.parquet")\n') == ["L203"]


def test_table_alias_reported_once():
    ds = [d for d in diags('x = items\nwrite(x, "out.parquet")\n') if d.severity == "error"]
    assert [d.code for d in ds] == ["L411"]
    assert "is a table" in ds[0].message
