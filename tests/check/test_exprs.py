import ast

import pytest

from lattice.check.context import CheckContext
from lattice.check.exprs import ExprTyper
from lattice.diagnostics import Severity
from lattice.typesys import UNKNOWN, ColT, LitT, Scalar

INT, F, S, B, D = Scalar.INT, Scalar.FLOAT, Scalar.STR, Scalar.BOOL, Scalar.DATE


def typer(profile, scalars=None, silent=frozenset()):
    ctx = CheckContext(profile)
    return ctx, ExprTyper(ctx, profile.schemas["Orders"], scalars or {}, silent)


def ty(profile, src, scalars=None, silent=frozenset()):
    """Type one expression; return (type, [codes])."""
    ctx, t = typer(profile, scalars, silent)
    node = ast.parse(src, mode="eval").body
    result = t.type_of(node)
    assert ctx.types[id(node)] == result
    return result, [d.code for d in ctx.diagnostics]


def agg(profile, src):
    ctx, t = typer(profile)
    node = ast.parse(src, mode="eval").body
    return t.type_agg(node), [d.code for d in ctx.diagnostics]


# ---- constants and names ----------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected"),
    [("True", LitT(B)), ("3", LitT(INT)), ("2.5", LitT(F)), ('"x"', LitT(S))],
)
def test_constants(profile, src, expected):
    assert ty(profile, src) == (expected, [])


def test_none_rejected(profile):
    assert ty(profile, "None") == (UNKNOWN, ["L410"])


def test_string_equal_to_column_warns(profile):
    ctx, t = typer(profile)
    node = ast.parse('col("region") == "status"', mode="eval").body
    assert t.type_of(node) == ColT(B)
    (d,) = ctx.diagnostics
    assert d.code == "L305" and d.severity is Severity.WARNING and 'col("status")' in d.hint


def test_scalar_param_name(profile):
    assert ty(profile, "n", {"n": INT}) == (LitT(INT), [])


def test_bare_name_rejected(profile):
    ctx, t = typer(profile)
    assert t.type_of(ast.parse("qty", mode="eval").body) is UNKNOWN
    (d,) = ctx.diagnostics
    assert d.code == "L411" and 'col("qty")' in d.hint


# ---- col --------------------------------------------------------------------


def test_col_ok(profile):
    assert ty(profile, 'col("price")') == (ColT(F), [])


def test_col_unknown_did_you_mean(profile):
    ctx, t = typer(profile)
    assert t.type_of(ast.parse('col("prce")', mode="eval").body) is UNKNOWN
    (d,) = ctx.diagnostics
    assert d.code == "L401" and "price" in d.hint and "'prce'" in d.message


@pytest.mark.parametrize("src", ["col(x)", 'col("a", "b")', 'col(name="a")', "col()"])
def test_col_bad_shape(profile, src):
    assert ty(profile, src) == (UNKNOWN, ["L412"])


# ---- when / coalesce / is_null ---------------------------------------------


def test_when_ok_widens(profile):
    assert ty(profile, 'when(col("qty") > 1, then=col("qty"), otherwise=0.5)') == (ColT(F), [])


def test_when_literal_branches(profile):
    assert ty(profile, 'when(col("qty") > 1, then="a", otherwise="b")') == (ColT(S), [])


@pytest.mark.parametrize(
    "src",
    [
        'when(col("qty") > 1, "a", otherwise="b")',
        'when(col("qty") > 1, then="a")',
        'when(then="a", otherwise="b")',
        'when(col("qty") > 1, then="a", otherwise="b", extra=1)',
    ],
)
def test_when_bad_shape(profile, src):
    assert ty(profile, src) == (UNKNOWN, ["L404"])


def test_when_cond_not_bool(profile):
    assert ty(profile, 'when(col("qty"), then=1, otherwise=2)') == (UNKNOWN, ["L403"])


def test_when_branches_dont_unify(profile):
    assert ty(profile, 'when(col("qty") > 1, then=1, otherwise="x")') == (UNKNOWN, ["L402"])


def test_coalesce_ok(profile):
    assert ty(profile, 'coalesce(col("qty"), 0.0)') == (ColT(F), [])


def test_coalesce_mismatch(profile):
    assert ty(profile, 'coalesce(col("qty"), "x")') == (UNKNOWN, ["L402"])


def test_coalesce_arity(profile):
    assert ty(profile, 'coalesce(col("qty"))') == (UNKNOWN, ["L404"])


def test_is_null_ok(profile):
    assert ty(profile, 'is_null(col("qty"))') == (ColT(B), [])


def test_is_null_arity(profile):
    assert ty(profile, 'is_null(col("qty"), col("price"))') == (UNKNOWN, ["L404"])


# ---- fn.* -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ('fn.abs(col("qty"))', ColT(INT)),
        ('fn.round(col("price"), digits=2)', ColT(F)),
        ('fn.clip(col("qty"), lo=0, hi=10.5)', ColT(F)),
        ('fn.clip(col("qty"), lo=0, hi=10)', ColT(INT)),
        ('fn.upper(col("region"))', ColT(S)),
        ('fn.contains(col("region"), sub="no")', ColT(B)),
        ('fn.concat(col("region"), col("status"))', ColT(S)),
        ('fn.date_diff(col("order_date"), col("order_date"), unit="days")', ColT(INT)),
        ('fn.date_add(col("order_date"), days=n)', ColT(D)),
        ('fn.to_str(col("qty"))', ColT(S)),
        ('fn.len("abc")', LitT(INT)),
    ],
)
def test_fn_ok(profile, src, expected):
    assert ty(profile, src, {"n": INT}) == (expected, [])


def test_fn_alias(profile):
    ctx, t = typer(profile)
    assert t.type_of(ast.parse('fn.length(col("region"))', mode="eval").body) is UNKNOWN
    (d,) = ctx.diagnostics
    assert d.code == "L702" and "len" in d.hint


def test_fn_unknown(profile):
    ctx, t = typer(profile)
    t.type_of(ast.parse('fn.uppr(col("region"))', mode="eval").body)
    (d,) = ctx.diagnostics
    assert d.code == "L106" and "upper" in d.hint


@pytest.mark.parametrize(
    ("src", "code"),
    [
        ('fn.upper(col("region"), col("status"))', "L404"),  # arity
        ('fn.upper(col("qty"))', "L402"),  # arg type
        ('fn.round(col("price"))', "L404"),  # missing keyword
        ('fn.upper(col("region"), x=1)', "L404"),  # unexpected keyword
        ('fn.round(col("price"), digits=col("qty"))', "L404"),  # keyword not literal
        ('fn.round(col("price"), digits=1.5)', "L402"),  # keyword literal of wrong type
        ('fn.date_diff(col("order_date"), col("order_date"), unit="weeks")', "L404"),
    ],
)
def test_fn_errors(profile, src, code):
    assert ty(profile, src) == (UNKNOWN, [code])


def test_agg_outside_group(profile):
    assert ty(profile, 'agg.sum(col("qty"))') == (UNKNOWN, ["L413"])


def test_bare_namespace_attribute(profile):
    assert ty(profile, "fn.upper") == (UNKNOWN, ["L411"])


# ---- other calls ------------------------------------------------------------


def test_silent_call(profile):
    assert ty(profile, "derive(t, a=1)", silent=frozenset({"derive"})) == (UNKNOWN, [])


def test_non_expression_call(profile):
    assert ty(profile, "Int(3)") == (UNKNOWN, ["L411"])


# ---- operators --------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ('col("qty") + 1', ColT(INT)),
        ('col("qty") * col("price")', ColT(F)),
        ('col("qty") % 2', ColT(INT)),
        ('col("qty") / 2', ColT(F)),
        ("1 + 2", LitT(INT)),
        ('(col("qty") > 1) & (col("price") < 2.0)', ColT(B)),
        ('(col("qty") > 1) | (col("status") == "x")', ColT(B)),
        ('col("region") < "m"', ColT(B)),
        ('col("order_date") >= col("order_date")', ColT(B)),
        ('-col("price")', ColT(F)),
        ('~(col("qty") > 1)', ColT(B)),
        ('col("qty") == 2.0', ColT(B)),
    ],
)
def test_operators_ok(profile, src, expected):
    assert ty(profile, src)[0] == expected


@pytest.mark.parametrize(
    "src",
    [
        'col("qty") - col("region")',
        'col("region") / 2',
        'col("qty") & col("qty")',
        'col("qty") == "x"',
        'col("region") < 1',
        'col("status") > col("order_date")',
        '-col("region")',
        '~col("qty")',
    ],
)
def test_operators_errors(profile, src):
    assert ty(profile, src) == (UNKNOWN, ["L402"])


def test_str_plus_str_hint(profile):
    ctx, t = typer(profile)
    t.type_of(ast.parse('col("region") + col("status")', mode="eval").body)
    (d,) = ctx.diagnostics
    assert d.code == "L402" and "fn.concat" in d.hint and "Str" in d.message


def test_float_equality_warns(profile):
    ctx, t = typer(profile)
    t.type_of(ast.parse('col("price") == 1.5', mode="eval").body)
    (d,) = ctx.diagnostics
    assert d.code == "L406" and d.severity is Severity.WARNING


def test_bool_and_int_one_error(profile):
    ctx, t = typer(profile)
    t.type_of(ast.parse('(col("qty") > 1) & col("qty")', mode="eval").body)
    assert [d.code for d in ctx.diagnostics] == ["L402"]


def test_unknown_suppresses_cascade(profile):
    assert ty(profile, 'col("zz") + 1') == (UNKNOWN, ["L401"])
    assert ty(profile, 'fn.upper(col("zz")) == "A"') == (UNKNOWN, ["L401"])


def test_list_rejected(profile):
    assert ty(profile, '["a"]') == (UNKNOWN, ["L411"])


def test_subexpressions_recorded(profile):
    ctx, t = typer(profile)
    node = ast.parse('col("qty") + 1', mode="eval").body
    t.type_of(node)
    assert ctx.types[id(node.left)] == ColT(INT)
    assert ctx.types[id(node.right)] == LitT(INT)


# ---- aggregates -------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ('agg.sum(col("qty"))', INT),
        ('agg.sum(col("price"))', F),
        ('agg.mean(col("qty"))', F),
        ('agg.min(col("region"))', S),
        ('agg.max(col("order_date"))', D),
        ("agg.count()", INT),
        ('agg.count(col("qty"))', INT),
        ('agg.count(col("region"))', INT),
    ],
)
def test_agg_ok(profile, src, expected):
    assert agg(profile, src) == (expected, [])


@pytest.mark.parametrize(
    ("src", "code"),
    [
        ('col("qty")', "L413"),
        ('agg.total(col("qty"))', "L106"),
        ('agg.count(col("qty"), col("price"))', "L404"),
        ('agg.count(col("zz"))', "L401"),
        ("agg.sum()", "L404"),
        ('agg.sum(col("region"))', "L402"),
        ('agg.mean(col("status"))', "L402"),
        ('agg.max(col("qty") > 1)', "L402"),
        ('agg.sum(col("zz"))', "L401"),
    ],
)
def test_agg_errors(profile, src, code):
    assert agg(profile, src) == (None, [code])
