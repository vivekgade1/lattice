"""N1 — normalisation of ordinary Python spellings (spec 2026-09-27 lattice-surface-design
§1-2): each accepted spelling checks to exactly the plan of the hand-written canonical
program; spellings that must still fail fail with the user's own span."""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Mapping
from typing import Any

import pytest

from bench.api_datasets import api_profile
from lattice.api.ir import lower_api
from lattice.canon import canonical_source
from lattice.check import CheckResult, check_source
from lattice.diagnostics import Severity, Span
from lattice.ir import lower
from lattice.profile import Profile
from lattice.typesys import Scalar, Schema

ORDERS = Schema.from_pairs(
    [
        ("order_id", Scalar.INT),
        ("customer_id", Scalar.INT),
        ("status", Scalar.STR),
        ("qty", Scalar.INT),
        ("price", Scalar.FLOAT),
        ("region", Scalar.STR),
    ]
)
CUSTOMERS = Schema.from_pairs([("customer_id", Scalar.INT), ("name", Scalar.STR)])
TABULAR = Profile({"Orders": ORDERS, "Customers": CUSTOMERS})

HEAD = '# lattice: v1\norders = load("orders.csv", schema=Orders)\n'
TAIL = 'write(out, "out.parquet")\n'


def _tab(body: str) -> str:
    return HEAD + body + TAIL


def _fingerprint(obj: Any) -> Any:
    """A plan as plain data: spans dropped (layouts differ), AST nodes dumped."""
    if isinstance(obj, Span):
        return None
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            f.name: _fingerprint(getattr(obj, f.name))
            for f in dataclasses.fields(obj)
            if f.name != "span"
        }
    if isinstance(obj, ast.AST):
        return ast.dump(obj)
    if isinstance(obj, Mapping):
        return {str(k): _fingerprint(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_fingerprint(v) for v in obj]
    return obj


def _ok(src: str, profile: Profile) -> CheckResult:
    r = check_source(src, profile)
    errors = [d.render() for d in r.diagnostics if d.severity is Severity.ERROR]
    assert r.program is not None, errors
    return r


def _same_tabular_plan(spelled: str, canonical: str) -> CheckResult:
    a, b = _ok(_tab(spelled), TABULAR), _ok(_tab(canonical), TABULAR)
    assert a.program is not None and b.program is not None
    assert _fingerprint(lower(a.program)) == _fingerprint(lower(b.program))
    assert a.normalisations, "the spelled program should have been normalised"
    assert not b.normalisations, "the canonical program needs no normalisation"
    return a


def _kinds(r: CheckResult) -> set[str]:
    return {kind for kind, _ in r.normalisations}


# ---- positive: one test per spelling, plan-equal to the canonical program -----------------


def test_row_attribute_is_col():
    r = _same_tabular_plan(
        "out = where(orders, orders.qty > 2)\n",
        'out = where(orders, col("qty") > 2)\n',
    )
    assert _kinds(r) == {"attribute"}


def test_schema_attribute_through_filters_is_col():
    r = _same_tabular_plan(
        "big = where(orders, Orders.qty > 2)\nout = sort(big, by=[Orders.qty])\n",
        'big = where(orders, col("qty") > 2)\nout = sort(big, by=["qty"])\n',
    )
    assert _kinds(r) == {"attribute"}


def test_attribute_in_column_lists_and_aggregates():
    _same_tabular_plan(
        "out = group(orders, by=[orders.region], total=agg.sum(orders.qty))\n",
        'out = group(orders, by=["region"], total=agg.sum(col("qty")))\n',
    )


def test_string_plus_is_concat_left_folded():
    r = _same_tabular_plan(
        'out = derive(orders, label=col("region") + "-" + col("status"))\n',
        'out = derive(orders, label=fn.concat(fn.concat(col("region"), "-"), col("status")))\n',
    )
    assert _kinds(r) == {"concat"}


def test_numeric_plus_stays_plus():
    r = _ok(_tab('out = derive(orders, t=col("qty") + 1)\n'), TABULAR)
    assert not r.normalisations


def test_f_string_is_concat():
    r = _same_tabular_plan(
        "out = derive(orders, label=f\"{col('region')}-{col('status')}\")\n",
        'out = derive(orders, label=fn.concat(fn.concat(col("region"), "-"), col("status")))\n',
    )
    assert "f_string" in _kinds(r)


def test_and_or_not_are_bool_operators():
    r = _same_tabular_plan(
        'out = where(orders, col("qty") > 1 and not col("region") == "EU" or col("price") < 2.0)\n',
        'out = where(orders, ((col("qty") > 1) & ~(col("region") == "EU"))'
        ' | (col("price") < 2.0))\n',
    )
    assert _kinds(r) == {"bool_op", "not"}


def test_three_way_and_folds_left():
    _same_tabular_plan(
        'out = where(orders, col("qty") > 1 and col("qty") < 9 and col("price") > 0.5)\n',
        'out = where(orders, ((col("qty") > 1) & (col("qty") < 9)) & (col("price") > 0.5))\n',
    )


def test_if_expression_is_when():
    r = _same_tabular_plan(
        'out = derive(orders, size="big" if col("qty") > 5 else "small")\n',
        'out = derive(orders, size=when(col("qty") > 5, then="big", otherwise="small"))\n',
    )
    assert _kinds(r) == {"if_exp"}


@pytest.mark.parametrize(
    ("spelled", "canonical"),
    [
        ('col("region").lower()', 'fn.lower(col("region"))'),
        ('col("region").upper()', 'fn.upper(col("region"))'),
        ('col("region").strip()', 'fn.strip(col("region"))'),
        ('col("region").startswith("E")', 'fn.starts_with(col("region"), prefix="E")'),
        ('col("region").replace("E", "e")', 'fn.replace(col("region"), old="E", new="e")'),
        ("orders.region.lower()", 'fn.lower(col("region"))'),
    ],
)
def test_string_methods_are_fn_calls(spelled: str, canonical: str):
    r = _same_tabular_plan(
        f"out = derive(orders, x={spelled})\n", f"out = derive(orders, x={canonical})\n"
    )
    assert "method" in _kinds(r)


@pytest.mark.parametrize(
    ("spelled", "canonical"),
    [
        ('len(col("region"))', 'fn.len(col("region"))'),
        ('abs(col("qty"))', 'fn.abs(col("qty"))'),
        ('round(col("price"), 2)', 'fn.round(col("price"), digits=2)'),
    ],
)
def test_builtins_are_fn_calls(spelled: str, canonical: str):
    r = _same_tabular_plan(
        f"out = derive(orders, x={spelled})\n", f"out = derive(orders, x={canonical})\n"
    )
    assert _kinds(r) == {"builtin"}


def test_in_is_substring():
    r = _same_tabular_plan(
        'out = where(orders, "E" in col("region"))\n',
        'out = where(orders, fn.contains(col("region"), sub="E"))\n',
    )
    assert _kinds(r) == {"in_substring"}


def test_not_in_is_negated_substring():
    _same_tabular_plan(
        'out = where(orders, "E" not in col("region"))\n',
        'out = where(orders, ~fn.contains(col("region"), sub="E"))\n',
    )


def test_in_list_is_membership():
    r = _same_tabular_plan(
        'out = where(orders, col("region") in ["EU", "US"])\n',
        'out = where(orders, (col("region") == "EU") | (col("region") == "US"))\n',
    )
    assert _kinds(r) == {"in_list"}


def test_is_none_is_is_null():
    r = _same_tabular_plan(
        'out = where(orders, col("price") is None or col("price") is not None)\n',
        'out = where(orders, is_null(col("price")) | ~is_null(col("price")))\n',
    )
    assert "is_none" in _kinds(r)


def test_eq_none_is_is_null_with_a_warning():
    r = _same_tabular_plan(
        'out = where(orders, col("price") == None)\n',
        'out = where(orders, is_null(col("price")))\n',
    )
    warnings = [d for d in r.diagnostics if d.code == "L306"]
    assert len(warnings) == 1
    assert warnings[0].severity is Severity.WARNING
    assert warnings[0].span == Span(3, 21)
    assert "is_null" in (warnings[0].hint or "")


def test_nested_ops_split_into_fresh_steps():
    # the statement is on line 3: fresh names _s<k>_3, innermost first
    r = _same_tabular_plan(
        'out = sort(where(orders, col("qty") > 1), by=["qty"])\n',
        '_s1_3 = where(orders, col("qty") > 1)\nout = sort(_s1_3, by=["qty"])\n',
    )
    assert _kinds(r) == {"split"}


def test_split_in_write_and_step_return():
    src = (
        "# lattice: v1\n"
        "@step\n"
        "def big(t: Table[Orders]) -> Table:\n"
        "    return where(t, t.qty > 1)\n"
        'write(big(load("orders.csv", schema=Orders)), "out.parquet")\n'
    )
    canonical = (
        "# lattice: v1\n"
        "@step\n"
        "def big(t: Table[Orders]) -> Table:\n"
        '    _s1_4 = where(t, col("qty") > 1)\n'
        "    return _s1_4\n"
        '_s2_5 = load("orders.csv", schema=Orders)\n'
        "_s3_5 = big(_s2_5)\n"
        'write(_s3_5, "out.parquet")\n'
    )
    a, b = _ok(src, TABULAR), _ok(canonical, TABULAR)
    assert a.program is not None and b.program is not None
    assert _fingerprint(lower(a.program)) == _fingerprint(lower(b.program))


def test_fresh_names_avoid_user_names():
    r = _ok(
        _tab(
            '_s1_4 = where(orders, col("qty") > 0)\n'
            'out = sort(where(_s1_4, col("qty") > 1), by=["qty"])\n'
        ),
        TABULAR,
    )
    assert r.program is not None
    names = [s.targets[0].id for s in r.program.module.body if isinstance(s, ast.Assign)]
    assert len(names) == len(set(names))


def test_when_positional_and_underscore_keywords():
    canonical = 'out = derive(orders, s=when(col("qty") > 5, then="big", otherwise="small"))\n'
    for spelled in (
        'when(col("qty") > 5, "big", "small")',
        'when(col("qty") > 5, "big", otherwise="small")',
        'when(col("qty") > 5, then_="big", otherwise_="small")',
        'when(col("qty") > 5, then="big", else_="small")',
    ):
        r = _same_tabular_plan(f"out = derive(orders, s={spelled})\n", canonical)
        assert _kinds(r) == {"when_keywords"}


def test_chained_comparison_is_conjunction():
    r = _same_tabular_plan(
        'out = where(orders, 1 < col("qty") <= 5)\n',
        'out = where(orders, (1 < col("qty")) & (col("qty") <= 5))\n',
    )
    assert _kinds(r) == {"chained_compare"}


# ---- API programs ----------------------------------------------------------------------------

API_SPELLED = """# lattice: v1
@endpoint("PATCH", "/products/{product_id}/rename", auth=Admin)
def rename_product(
    product_id: Int(ge=1), suffix: Str(min_len=1, max_len=20), user: CurrentUser
) -> One | NotFound:
    product = one(where(fetch(Products), Products.id == product_id), else_=NotFound("product"))
    updated = update(product, name=product.name.strip() + " " + suffix)
    return updated
"""

API_CANONICAL = """# lattice: v1
@endpoint("PATCH", "/products/{product_id}/rename", auth=Admin)
def rename_product(
    product_id: Int(ge=1), suffix: Str(min_len=1, max_len=20), user: CurrentUser
) -> One | NotFound:
    _s1_6 = fetch(Products)
    _s2_6 = where(_s1_6, col("id") == product_id)
    product = one(_s2_6, else_=NotFound("product"))
    updated = update(product, name=fn.concat(fn.concat(fn.strip(col("name")), " "), suffix))
    return updated
"""


def test_api_endpoint_plan_equals_canonical():
    a, b = _ok(API_SPELLED, api_profile()), _ok(API_CANONICAL, api_profile())
    assert a.program is not None and b.program is not None
    assert _fingerprint(lower_api(a.program)) == _fingerprint(lower_api(b.program))
    assert _kinds(a) == {"split", "attribute", "method", "concat"}


def test_api_concat_compiles_to_sql_concatenation():
    r = _ok(API_CANONICAL, api_profile())
    assert r.program is not None
    texts = [
        s.sql.text
        for plan in lower_api(r.program)
        for s in plan.steps
        if getattr(s, "sql", None) is not None
    ]
    assert any("||" in t for t in texts), texts


def test_api_is_none_on_optional_parameter():
    spelled = """# lattice: v1
@endpoint("GET", "/products", auth=Public)
def list_products(category: Str(max_len=20) | None = None, limit: Int(ge=1, le=50) = 20) -> Page:
    products = fetch(Products)
    shown = where(products, category is None or products.category == category)
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit)
    return result
"""
    canonical = spelled.replace(
        "category is None or products.category == category",
        'is_null(category) | (col("category") == category)',
    )
    a, b = _ok(spelled, api_profile()), _ok(canonical, api_profile())
    assert a.program is not None and b.program is not None
    assert _fingerprint(lower_api(a.program)) == _fingerprint(lower_api(b.program))


# ---- negative: spellings that must still fail ----------------------------------------------


def test_plus_on_str_and_int_is_l402_at_the_plus():
    r = check_source(_tab('out = derive(orders, x=col("region") + col("qty"))\n'), TABULAR)
    errors = [d for d in r.diagnostics if d.severity is Severity.ERROR]
    assert [d.code for d in errors] == ["L402"]
    assert errors[0].span == Span(3, 24)
    assert "'+' cannot combine Str and Int" in errors[0].message


def test_attribute_on_another_table_is_still_l106():
    src = _tab(
        'customers = load("customers.csv", schema=Customers)\n'
        'out = where(orders, customers.name == "x")\n'
    )
    r = check_source(src, TABULAR)
    errors = [d for d in r.diagnostics if d.severity is Severity.ERROR]
    assert [d.code for d in errors] == ["L106"]
    assert errors[0].span == Span(4, 21)


def test_attribute_on_a_non_row_is_still_l106():
    src = _tab("n = 3\nout = where(orders, n.real > 1)\n")
    r = check_source(src, TABULAR)
    assert [d.code for d in r.diagnostics if d.severity is Severity.ERROR] == ["L106"]


def test_and_on_non_bool_is_a_type_error():
    r = check_source(_tab('out = where(orders, col("qty") and col("price"))\n'), TABULAR)
    assert [d.code for d in r.diagnostics if d.severity is Severity.ERROR] == ["L402"]


def test_method_on_non_string_is_a_type_error():
    r = check_source(_tab('out = derive(orders, x=col("qty").lower())\n'), TABULAR)
    errors = [d for d in r.diagnostics if d.severity is Severity.ERROR]
    assert [d.code for d in errors] == ["L402"]
    assert errors[0].span == Span(3, 24)


def test_unknown_method_is_still_l105():
    r = check_source(_tab('out = derive(orders, x=col("region").endswith("x"))\n'), TABULAR)
    assert [d.code for d in r.diagnostics if d.severity is Severity.ERROR] == ["L105"]


def test_statement_level_if_is_still_rejected():
    r = check_source(_tab('if True:\n    out = where(orders, col("qty") > 1)\n'), TABULAR)
    assert "L127" in [d.code for d in r.diagnostics]


def test_fresh_names_never_appear_in_diagnostics():
    # L832 names the returned table, here a fresh name for the returned where(...)
    src = """# lattice: v1
@endpoint("GET", "/products/cheap", auth=Public)
def cheap() -> One:
    products = fetch(Products)
    return where(products, products.price < 5.0)
"""
    r = check_source(src, api_profile())
    assert [d.code for d in r.diagnostics if d.severity is Severity.ERROR] == ["L832"]
    for d in r.diagnostics:
        assert "_s1_" not in d.message and "_s1_" not in (d.hint or ""), d.render()
    assert any("part of the statement at line 5" in d.message for d in r.diagnostics)


def test_normalised_error_span_is_the_users_span():
    # a type error inside a split-off op points at the user's text, not at a fresh name
    r = check_source(_tab('out = sort(where(orders, orders.region > 1), by=["qty"])\n'), TABULAR)
    errors = [d for d in r.diagnostics if d.severity is Severity.ERROR]
    assert [d.code for d in errors] == ["L402"]
    assert errors[0].span == Span(3, 26)


# ---- canonical printing ----------------------------------------------------------------------


def test_canonical_printing_round_trips():
    src = _tab(
        'out = sort(where(orders, orders.region.lower() == "eu" and orders.qty > 1), '
        "by=[orders.qty])\n"
    )
    first = _ok(src, TABULAR)
    assert first.program is not None
    canon = canonical_source(first.program)
    assert "fn.lower(col('region'))" in canon and "_s1_3" in canon
    second = _ok(canon, TABULAR)
    assert second.program is not None
    assert not second.normalisations
    assert canonical_source(second.program) == canon
    assert _fingerprint(lower(first.program)) == _fingerprint(lower(second.program))


def test_canonical_printing_shows_concat():
    first = _ok(_tab('out = derive(orders, x=col("region") + "!")\n'), TABULAR)
    assert first.program is not None
    assert "fn.concat(col('region'), '!')" in canonical_source(first.program)


# ---- procedures are T2: never normalised ----------------------------------------------------


def test_procedure_bodies_are_not_normalised():
    src = """# lattice: v1
@procedure
def label(name: Str) -> Str:
    return name.lower() + "!" if len(name) > 0 else "none"

@endpoint("GET", "/products/{product_id}", auth=Public)
def show(product_id: Int(ge=1)) -> One | NotFound:
    products = fetch(Products)
    found = where(products, col("id") == product_id)
    named = derive(found, label=label(col("name")))
    product = one(named, else_=NotFound("product"))
    return product
"""
    r = _ok(src, api_profile())
    assert not r.normalisations


# ---- existing programs are untouched (their ASTs, hence plans, are byte-identical) ----------


def _reference_programs() -> list[tuple[str, str, Profile]]:
    import re
    from pathlib import Path

    from bench.datasets import bench_profile
    from lattice.llm.api_prompt import LATTICE_EXAMPLES
    from lattice.llm.prompt import lattice_system_prompt

    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    out = [
        (str(p), p.read_text(), api_profile())
        for p in sorted((fixtures / "api").glob("*/lattice.py"))
    ]
    out += [
        (str(p), p.read_text(), bench_profile())
        for p in sorted((fixtures / "lattice_solutions").glob("*.py"))
    ]
    out += [(f"api example {i}", s, api_profile()) for i, s in enumerate(LATTICE_EXAMPLES)]
    blocks = re.findall(r"```python\n(.*?)```", lattice_system_prompt(), re.DOTALL)
    out += [(f"tabular example {i}", s, bench_profile()) for i, s in enumerate(blocks)]
    return out


def test_reference_programs_need_no_normalisation():
    programs = _reference_programs()
    assert len(programs) > 100
    for name, source, profile in programs:
        r = check_source(source, profile)
        assert r.ok, name
        assert r.normalisations == (), name


def test_no_rewrite_returns_the_same_statements():
    from lattice.check.normalise import normalise

    body = ast.parse(_tab('out = where(orders, col("qty") > 1)\n')).body
    result = normalise(body, api=False)
    assert result.records == ()
    assert all(a is b for a, b in zip(result.body, body, strict=True))
