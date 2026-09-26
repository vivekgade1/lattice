"""Regression tests for review findings: every program the checker accepts must run
without LatticeInternalError (or any non-LatticeRuntimeError exception), and the
checker itself must never raise."""

import ast
import datetime
from pathlib import Path

import polars as pl
import pytest

from lattice.catalog import IMPORTABLE
from lattice.check import check_source
from lattice.diagnostics import Severity, Span
from lattice.ir import Node, Plan
from lattice.profile import Profile
from lattice.runtime import LatticeRuntimeError
from lattice.runtime.exec import _resolve, execute, run_source
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
PROFILE = Profile(schemas={"Items": ITEMS, "Owners": OWNERS})

ITEMS_CSV = (
    "id,name,qty,price,day\n"
    "1,apple,3,1.5,2024-01-02\n"
    "2,pear,0,2.0,2024-02-03\n"
    "3,fig,5,NaN,2024-03-04\n"
    "4,kiwi,,4.0,\n"
    "5,apple,2,1.0,2024-01-05\n"
)
OWNERS_CSV = "id,owner\n1,ann\n2,bob\n9,zed\n1,amy\n"

LOAD = 't = load("items.csv", schema=Items)\n'
W = 'write(x, "out.parquet")\n'


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "items.csv").write_text(ITEMS_CSV)
    (tmp_path / "owners.csv").write_text(OWNERS_CSV)
    return tmp_path


def prog(body: str) -> str:
    used = {n.id for n in ast.walk(ast.parse(body)) if isinstance(n, ast.Name)}
    ops = sorted(used & IMPORTABLE)
    schemas = sorted(used & set(PROFILE.schemas))
    out = "# lattice: v1\n"
    if ops:
        out += f"from lattice.ops import {', '.join(ops)}\n"
    if schemas:
        out += f"from lattice.schemas import {', '.join(schemas)}\n"
    return out + body


def diags(body: str, raw: bool = False):
    return check_source(body if raw else prog(body), PROFILE).diagnostics


def errors(body: str, raw: bool = False) -> list[str]:
    return [d.code for d in diags(body, raw) if d.severity is Severity.ERROR]


def run_ok(body: str, root: Path) -> pl.DataFrame:
    result, rr = run_source(prog(body), PROFILE, root)
    assert result.ok, result.diagnostics
    assert rr is not None
    return rr.outputs[next(iter(rr.outputs))]


# ---- B1: non-table names in table positions ------------------------------------------


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        (LOAD + 'x = where(Items, col("qty") > 1)\nwrite(t, "a.parquet")\n' + W, "schema"),
        (LOAD + "x = derive(fn, c=1)\nwrite(t, 'a.parquet')\n" + W, "fn"),
        (LOAD + 'x = select(Int, cols=["a"])\nwrite(t, "a.parquet")\n' + W, "Int"),
        (LOAD + "x = limit(load, n=1)\n" + W, "load"),
        (LOAD + 'x = join(t, Owners, on=["id"])\n' + W, "schema"),
    ],
)
def test_non_table_name_in_table_position_is_L419(body, kind):
    ds = [d for d in diags(body) if d.severity is Severity.ERROR]
    assert [d.code for d in ds] == ["L419"], ds
    assert "expected a table" in ds[0].message


def test_step_name_as_table_is_L419():
    body = (
        "@step\ndef f(a: Table[Items]) -> Table:\n    b = where(a, col('qty') > 1)\n    return b\n"
        + LOAD
        + "y = f(t)\n"
        + "x = where(f, col('qty') > 1)\nwrite(y, 'a.parquet')\n"
        + W
    )
    assert errors(body) == ["L419"]
    body2 = (
        "@step\ndef f(a: Table[Items]) -> Table:\n    b = where(a, col('qty') > 1)\n    return b\n"
        + LOAD
        + "y = f(t)\nx = f(f)\nwrite(y, 'a.parquet')\n"
        + W
    )
    assert errors(body2) == ["L419"]


def test_write_of_schema_name_is_L419():
    assert errors(LOAD + 'write(t, "a.parquet")\nwrite(Items, "out.parquet")\n') == ["L419"]


def test_unknown_name_in_table_position_reports_once():
    assert errors(LOAD + 'x = where(nope, col("qty") > 1)\nwrite(t, "a.parquet")\n' + W) == ["L203"]


@pytest.mark.parametrize("ret", ["n", "Items"])
def test_step_must_return_a_table_name(ret):
    body = (
        "@step\ndef f(a: Table[Items], n: Int) -> Table:\n"
        "    b = where(a, col('qty') > n)\n"
        f"    return {ret}\n" + LOAD + "x = f(t, n=1)\n" + W
    )
    codes = errors(body)
    assert codes and set(codes) <= {"L602", "L419"}, codes


# ---- M1a: fn keyword arguments must be literals -------------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        'fn.date_add(col("day"), days=5 % 0)',
        'fn.date_add(col("day"), days=fn.to_int("x"))',
        'fn.round(col("price"), digits=1 % 0)',
        'fn.clip(col("qty"), lo=1 % 0, hi=3)',
        'fn.contains(col("name"), sub=fn.to_str(1))',
        'fn.contains(col("name"), sub=when(True, then="a", otherwise="b"))',
        'fn.to_date(col("name"), format=fn.concat("%Y", "-%m-%d"))',
    ],
)
def test_fn_keyword_must_be_a_literal(expr):
    assert errors(LOAD + f"x = derive(t, c={expr})\n" + W) == ["L404"]


def test_fn_keyword_negative_literal_and_param_still_allowed(root):
    df = run_ok(LOAD + 'x = derive(t, c=fn.date_add(col("day"), days=-(3)))\n' + W, root)
    assert df["c"][0] == datetime.date(2023, 12, 30)
    body = (
        "@step\ndef f(a: Table[Items], n: Int) -> Table:\n"
        "    b = derive(a, c=fn.date_add(col('day'), days=n))\n"
        "    return b\n" + LOAD + "x = f(t, n=-1)\n" + W
    )
    df = run_ok(body, root)
    assert df["c"][0] == datetime.date(2024, 1, 1)


# ---- M1b: fn.round digits ------------------------------------------------------------


def test_round_negative_digits_is_rejected():
    assert errors(LOAD + 'x = derive(t, c=fn.round(col("price"), digits=-1))\n' + W) == ["L404"]


def test_round_negative_digits_via_param_is_runtime_error(root):
    body = (
        "@step\ndef f(a: Table[Items], n: Int) -> Table:\n"
        "    b = derive(a, c=fn.round(col('price'), digits=n))\n"
        "    return b\n" + LOAD + "x = f(t, n=-1)\n" + W
    )
    assert errors(body) == []
    with pytest.raises(LatticeRuntimeError):
        run_source(prog(body), PROFILE, root)


# ---- M1c: out-of-range Int literals ---------------------------------------------------


@pytest.mark.parametrize(
    "stmt",
    [
        "x = limit(t, n=99999999999999999999999)",
        "x = derive(t, c=99999999999999999999999)",
        "x = derive(t, c=col('qty') + 9223372036854775808)",
        'x = derive(t, c=fn.date_add(col("day"), days=999999999999999999999))',
    ],
)
def test_int_literal_out_of_range_is_rejected(stmt):
    codes = errors(LOAD + stmt + "\n" + W)
    assert codes and set(codes) <= {"L404", "L417"}, codes


def test_int64_min_literal_is_accepted(root):
    df = run_ok(LOAD + "x = derive(t, c=-9223372036854775808)\n" + W, root)
    assert df["c"][0] == -9223372036854775808


# ---- M1d: write path extensions ------------------------------------------------------


@pytest.mark.parametrize("path", ["OUT.PARQUET", "out.Csv", "sub/Out.CSV"])
def test_write_extension_is_case_insensitive(path, root):
    result, rr = run_source(prog(LOAD + f'write(t, "{path}")\n'), PROFILE, root)
    assert result.ok and rr is not None
    written = root / path
    df = pl.read_csv(written) if path.lower().endswith(".csv") else pl.read_parquet(written)
    assert df.height == 5


@pytest.mark.parametrize(
    "stmt",
    ['write(t, ".parquet")', 'write(t, "sub/.CSV")', 'write(t, ".csv")'],
)
def test_write_path_with_empty_stem_is_L505(stmt):
    assert errors(LOAD + stmt + "\n") == ["L505"]


def test_load_path_with_empty_stem_is_L505():
    assert errors('t = load(".csv", schema=Items)\nwrite(t, "out.parquet")\n') == ["L505"]


# ---- M1e: nested unary minus in step scalar arguments --------------------------------


def test_double_negation_step_argument_runs(root):
    body = (
        "@step\ndef f(a: Table[Items], n: Int, r: Float) -> Table:\n"
        "    b = derive(a, c=col('qty') + n, d=col('price') * r)\n"
        "    return b\n" + LOAD + "x = f(t, n=--1, r=--2.0)\n" + W
    )
    df = run_ok(body, root)
    assert df["c"][0] == 4
    assert df["d"][0] == 3.0


# ---- M1f: control characters in paths ---------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        't = load("items\\x00.csv", schema=Items)\nwrite(t, "out.parquet")\n',
        LOAD + 'write(t, "out\\x00.parquet")\n',
        LOAD + 'write(t, "out\\n.parquet")\n',
    ],
)
def test_control_characters_in_paths_are_L501(body):
    assert errors(body) == ["L501"]


def test_resolve_with_nul_is_runtime_error(tmp_path):
    with pytest.raises(LatticeRuntimeError) as exc:
        _resolve(tmp_path.resolve(), "a\x00b.csv", Span(1, 1))
    assert exc.value.code == "R102"


# ---- minor 1: fn.clip result type ------------------------------------------------------


def test_clip_int_column_with_float_bounds_is_float(root):
    df = run_ok(LOAD + 'x = derive(t, c=fn.clip(col("qty"), lo=0.5, hi=2.5))\n' + W, root)
    assert df.schema["c"] == pl.Float64
    assert df["c"].to_list() == [2.5, 0.5, 2.5, None, 2.0]


def test_clip_int_column_with_int_bounds_stays_int(root):
    df = run_ok(LOAD + 'x = derive(t, c=fn.clip(col("qty"), lo=1, hi=3))\n' + W, root)
    assert df.schema["c"] == pl.Int64


def test_clip_float_param_bound_is_float(root):
    body = (
        "@step\ndef f(a: Table[Items], lo: Float) -> Table:\n"
        "    b = derive(a, c=fn.clip(col('qty'), lo=lo, hi=4))\n"
        "    return b\n" + LOAD + "x = f(t, lo=1)\n" + W
    )
    df = run_ok(body, root)
    assert df.schema["c"] == pl.Float64


# ---- minor 4: deep nesting ----------------------------------------------------------------


def test_deep_nesting_is_one_L003():
    body = LOAD + "x = derive(t, c=" + "-" * 5000 + "col('qty'))\n" + W
    ds = diags(body)
    assert [(d.code, d.span) for d in ds] == [("L003", Span(1, 1))]


def test_parser_stack_overflow_is_L003():
    src = "# lattice: v1\nx = " + "-" * 200000 + "1\n"
    ds = check_source(src, PROFILE).diagnostics
    assert [d.code for d in ds] == ["L003"]


def test_moderate_nesting_still_runs(root):
    body = LOAD + "x = derive(t, c=" + "-" * 40 + "col('qty'))\n" + W
    df = run_ok(body, root)
    assert df["c"][0] == 3


# ---- minor 5: no-ops, Date params, type params ----------------------------------------------


def test_sort_with_empty_by_is_L304():
    assert errors(LOAD + "x = sort(t, by=[])\n" + W) == ["L304"]


def test_identity_rename_is_L304():
    assert errors(LOAD + 'x = rename(t, qty="qty")\n' + W) == ["L304"]


def test_date_step_parameter_is_L601():
    body = (
        "@step\ndef f(a: Table[Items], d: Date) -> Table:\n"
        "    b = where(a, col('day') > d)\n"
        "    return b\n" + LOAD + "x = f(t, d='2024-01-01')\n" + W
    )
    assert "L601" in errors(body)
    assert "L402" not in errors(body)


def test_pep695_type_params_on_step_is_L601():
    body = (
        "@step\ndef f[T](a: Table[Items]) -> Table:\n"
        "    b = where(a, col('qty') > 1)\n"
        "    return b\n" + LOAD + "x = f(t)\n" + W
    )
    assert errors(body) == ["L601"]


# ---- minor 6: cascades --------------------------------------------------------------------


def test_join_collisions_are_one_L416():
    body = LOAD + 'u = load("items.csv", schema=Items)\nx = join(t, u, on=["id"])\n' + W
    ds = [d for d in diags(body) if d.severity is Severity.ERROR]
    assert [d.code for d in ds] == ["L416"]
    for col in ("name", "qty", "price", "day"):
        assert col in ds[0].message


def test_schema_as_string_is_only_L414():
    src = (
        "# lattice: v1\nfrom lattice.ops import load, write\nfrom lattice.schemas import Items\n"
        't = load("items.csv", schema="Items")\nwrite(t, "out.parquet")\n'
    )
    ds = [d for d in diags(src, raw=True) if d.severity is Severity.ERROR]
    assert [d.code for d in ds] == ["L414"]
    assert "schema=Items" in (ds[0].hint or "")


def test_ellipsis_step_body_is_one_L602():
    body = "@step\ndef f(a: Table[Items]) -> Table: ...\n" + LOAD + "x = f(t)\n" + W
    assert errors(body) == ["L602"]


def test_redefined_step_call_sites_have_no_L303():
    body = (
        "@step\ndef f(a: Table[Items]) -> Table:\n    b = where(a, col('qty') > 1)\n    return b\n"
        "@step\ndef f(a: Table[Items], n: Int) -> Table:\n"
        "    b = where(a, col('qty') > n)\n    return b\n" + LOAD + "x = f(t, n=1)\n" + W
    )
    codes = errors(body)
    assert "L201" in codes
    assert "L303" not in codes


# ---- minor 9: join row-limit estimate -------------------------------------------------------


def _join_plan(max_ok: bool) -> Plan:
    span = Span(1, 1)
    joined = ITEMS.extend([("owner", Scalar.STR)])
    return Plan(
        (
            Node("a", "load", (), {"path": "items.csv"}, ITEMS, span),
            Node("b", "load", (), {"path": "owners.csv"}, OWNERS, Span(2, 1)),
            Node("j", "join", ("a", "b"), {"on": ("id",), "how": "left"}, joined, Span(3, 1)),
            Node("_write0", "write", ("j",), {"path": "out.parquet"}, None, Span(4, 1)),
        )
    )


def test_join_estimate_raises_R201_before_executing(root, monkeypatch):
    from lattice.runtime import ops

    called = []
    real = ops.join
    monkeypatch.setattr(ops, "join", lambda *a, **k: called.append(1) or real(*a, **k))
    with pytest.raises(LatticeRuntimeError) as exc:
        execute(_join_plan(False), root, max_rows=5)  # left join yields 6 rows
    assert exc.value.code == "R201"
    assert exc.value.span == Span(3, 1)
    assert called == []  # the join itself never ran
    rr = execute(_join_plan(True), root, max_rows=6)
    assert rr.outputs["out.parquet"].height == 6


def test_join_size_estimate_is_exact():
    from lattice.runtime.ops import join_size

    left = pl.DataFrame({"k": [1, 1, 2, None, 3]})
    right = pl.DataFrame({"k": [1, 1, 1, 2, None], "v": [1, 2, 3, 4, 5]})
    for how in ("inner", "left"):
        expected = left.join(right, on=["k"], how=how, coalesce=True).height
        assert join_size(left, right, ["k"], how) == expected
