"""Sub-project 1b node N2 (spec 2026-09-27 lattice-loops §3.1, §3.2, §5): checker rules of
`window(...)` + `win.*` and `traverse(...)`.

One positive and one negative test per code:

* L430 -- `window` needs `order=` for its (order-dependent) functions;
* L431 -- `by + order` not proven unique for a tie-sensitive function (a warning in the
  tabular profile, an error in the API profile);
* L432 -- `win.*` outside `window`, or `agg.*` inside it;
* L433 -- `traverse` max_depth: an Int literal 1..1000 or a bounded Int parameter;
* L434 -- `traverse` parent/child: columns of the same type, child a key;
* L874 / L875 -- the backend capabilities `window_functions` / `recursive_queries`;

plus the result types, canonical form (table args are names, L301) and API ownership
(traverse over an owned table needs owned(), L802).
"""

from __future__ import annotations

import dataclasses

import pytest

from bench.api_datasets import api_profile
from lattice.api.backend import DIALECTS, SQLITE, Capabilities, SqliteDialect
from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.profile import Profile
from lattice.typesys import Scalar, Schema, TableT
from tests.api import loops

INT, F, S, D = Scalar.INT, Scalar.FLOAT, Scalar.STR, Scalar.DATE

SALES = Schema.from_pairs(
    [("id", INT), ("region", S), ("day", D), ("amount", INT), ("price", F)],
    keys=[("id",)],
    nullable=["price"],
)
TREE = Schema.from_pairs(
    [("id", INT), ("parent_id", INT), ("name", S)],
    keys=[("id",)],
    nullable=["parent_id"],
)
LINKS = Schema.from_pairs([("src", INT), ("dst", INT), ("label", S)])
PROFILE = Profile(schemas={"Sales": SALES, "Tree": TREE, "Links": LINKS})

H = "# lattice: v1\n"
LOADS = (
    's = load("sales.csv", schema=Sales)\n'
    't = load("tree.csv", schema=Tree)\n'
    'k = load("links.csv", schema=Links)\n'
)


def check(body: str, profile: Profile = PROFILE):
    return check_source(H + LOADS + body, profile)


def diags(body: str, profile: Profile = PROFILE):
    return check(body, profile).diagnostics


def codes(body: str, profile: Profile = PROFILE) -> list[str]:
    return [d.code for d in diags(body, profile) if d.severity is Severity.ERROR]


def warnings(body: str, profile: Profile = PROFILE) -> list[str]:
    return [d.code for d in diags(body, profile) if d.severity is Severity.WARNING]


def result_type(body: str, name: str, profile: Profile = PROFILE) -> TableT:
    r = check(body, profile)
    assert r.program is not None, [d.render() for d in r.diagnostics]
    import ast

    for stmt in r.program.module.body:
        if isinstance(stmt, ast.Assign) and stmt.targets[0].id == name:  # type: ignore[attr-defined]
            t = r.program.types[id(stmt.value)]
            assert isinstance(t, TableT)
            return t
    raise AssertionError(name)


def ok(body: str, profile: Profile = PROFILE) -> None:
    r = check(body, profile)
    assert r.ok, [d.render() for d in r.diagnostics]


W = 'write(x, "out.csv")\n'


# ---- window: result types ----------------------------------------------------------------


def test_window_adds_typed_columns_after_the_input_columns() -> None:
    body = (
        'x = window(s, by=["region"], order=["day", "id"], desc=["day"],\n'
        "    n=win.row_number(), r=win.rank(), dr=win.dense_rank(),\n"
        '    prev=win.lag(col("amount"), offset=1), nxt=win.lead(col("price")),\n'
        '    run=win.cum_sum(col("amount")), runf=win.cum_sum(col("price")),\n'
        '    cnt=win.cum_count(), lo=win.cum_min(col("day")), hi=win.cum_max(col("price")),\n'
        '    avg3=win.mean(col("amount"), rows=3), sum3=win.sum(col("price"), rows=3))\n' + W
    )
    t = result_type(body, "x")
    assert t.schema.columns == SALES.columns + (
        ("n", INT),
        ("r", INT),
        ("dr", INT),
        ("prev", INT),
        ("nxt", F),
        ("run", INT),
        ("runf", F),
        ("cnt", INT),
        ("lo", D),
        ("hi", F),
        ("avg3", F),
        ("sum3", F),
    )
    # lag/lead run past the partition edge; cum_max/mean of a nullable column may be
    # missing; the rest never are
    assert t.nullable == {"price", "prev", "nxt", "hi"}
    assert t.keys == SALES.keys


def test_window_rejects_a_column_that_already_exists() -> None:
    body = 'x = window(s, order=["id"], amount=win.row_number())\n' + W
    assert codes(body) == ["L405"]


def test_window_desc_names_order_columns() -> None:
    body = 'x = window(s, order=["id"], desc=["day"], n=win.row_number())\n' + W
    assert codes(body) == ["L404"]
    body = 'x = window(s, order=["id"], desc=True, n=win.row_number())\n' + W
    assert codes(body) == ["L404"]


def test_window_unknown_columns_are_l401() -> None:
    assert codes('x = window(s, by=["nope"], order=["id"], n=win.row_number())\n' + W) == ["L401"]
    assert codes('x = window(s, order=["id"], n=win.lag(col("nope")))\n' + W) == ["L401"]


def test_window_function_argument_rules() -> None:
    assert codes('x = window(s, order=["id"], n=win.lag(col("amount"), offset=0))\n' + W) == [
        "L404"
    ]
    assert codes('x = window(s, order=["id"], n=win.mean(col("amount")))\n' + W) == ["L404"]
    assert codes('x = window(s, order=["id"], n=win.mean(col("amount"), rows=0))\n' + W) == ["L404"]
    assert codes('x = window(s, order=["id"], n=win.cum_sum(col("region")))\n' + W) == ["L402"]
    assert codes('x = window(s, order=["id"], n=win.row_number(col("id")))\n' + W) == ["L404"]
    assert codes('x = window(s, order=["id"], n=win.nth(col("id")))\n' + W) == ["L106"]


def test_window_without_columns_is_a_no_op() -> None:
    assert codes('x = window(s, order=["id"])\n' + W) == ["L304"]


def test_window_table_argument_must_be_a_name() -> None:
    body = 'x = window(where(s, col("amount") > 1), order=["id"], n=win.row_number())\n' + W
    assert "L301" in codes(body)


# ---- L430 ---------------------------------------------------------------------------------


def test_l430_window_function_needs_order() -> None:
    body = 'x = window(s, by=["region"], n=win.row_number())\n' + W
    (d,) = [d for d in diags(body) if d.code == "L430"]
    assert d.severity is Severity.ERROR and d.hint and "order=" in d.hint
    assert codes('x = window(s, by=["region"], order=[], n=win.cum_count())\n' + W) == ["L430"]


def test_l430_not_reported_with_an_order() -> None:
    ok('x = window(s, by=["region"], order=["id"], n=win.row_number())\n' + W)


# ---- L431 ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "func",
    [
        "win.row_number()",
        'win.lag(col("amount"))',
        'win.lead(col("amount"), offset=2)',
        'win.cum_sum(col("amount"))',
        'win.mean(col("amount"), rows=2)',
    ],
)
def test_l431_warns_on_ties_in_the_tabular_profile(func: str) -> None:
    body = f'x = window(s, by=["region"], order=["day"], v={func})\n' + W
    (d,) = [d for d in diags(body) if d.code == "L431"]
    assert d.severity is Severity.WARNING
    assert d.hint and "key" in d.hint and '"id"' in d.hint
    assert check(body).ok  # a warning only


def test_l431_not_reported_when_order_contains_a_key() -> None:
    body = 'x = window(s, by=["region"], order=["day", "id"], n=win.row_number())\n' + W
    assert "L431" not in warnings(body)
    ok(body)


def test_l431_not_reported_for_tie_safe_rankings() -> None:
    body = 'x = window(s, order=["day"], r=win.rank(), dr=win.dense_rank())\n' + W
    assert "L431" not in warnings(body)


# ---- L432 ---------------------------------------------------------------------------------


def test_l432_win_outside_window() -> None:
    assert codes("x = derive(s, n=win.row_number())\n" + W) == ["L432"]
    assert codes("x = group(s, by=[], n=win.row_number())\n" + W) == ["L432"]
    assert codes("x = where(s, win.row_number() > 1)\n" + W) == ["L432"]


def test_l432_agg_inside_window() -> None:
    assert codes('x = window(s, order=["id"], n=agg.sum(col("amount")))\n' + W) == ["L432"]
    # a plain expression is not a window function either
    assert codes('x = window(s, order=["id"], n=col("amount") * 2)\n' + W) == ["L432"]
    # nor is a win.* nested in a window function's argument
    body = 'x = window(s, order=["id"], n=win.lag(win.row_number()))\n' + W
    assert codes(body) == ["L432"]


def test_l432_not_reported_for_win_inside_window() -> None:
    ok('x = window(s, order=["id"], n=win.cum_sum(col("amount") * 2))\n' + W)


# ---- traverse: result types and shape ------------------------------------------------------


def test_traverse_adds_depth_and_keeps_the_edges_columns() -> None:
    body = (
        'root = where(t, col("id") == 1)\n'
        'x = traverse(t, root, parent="parent_id", child="id", max_depth=5)\n' + W
    )
    tt = result_type(body, "x")
    assert tt.schema.columns == TREE.columns + (("depth", INT),)
    assert tt.keys == (("id",),)
    assert tt.nullable == {"parent_id"}
    up = body.replace("max_depth=5", 'max_depth=5, direction="up"')
    ok(up)


def test_traverse_start_must_be_a_name() -> None:
    body = (
        'x = traverse(t, where(t, col("id") == 1), parent="parent_id", child="id", max_depth=3)\n'
    )
    assert "L301" in codes(body + W)


def test_traverse_direction_is_down_or_up() -> None:
    body = 'x = traverse(t, t, parent="parent_id", child="id", max_depth=3, direction="left")\n'
    assert codes(body + W) == ["L404"]


def test_traverse_requires_its_keywords() -> None:
    assert codes('x = traverse(t, t, parent="parent_id", child="id")\n' + W) == ["L303"]


def test_traverse_depth_column_must_be_new() -> None:
    prof = Profile(
        schemas={
            **PROFILE.schemas,
            "Tree": Schema.from_pairs(
                [*TREE.columns, ("depth", INT)], keys=[("id",)], nullable=["parent_id"]
            ),
        }
    )
    body = 'x = traverse(t, t, parent="parent_id", child="id", max_depth=3)\n'
    assert codes(body + W, prof) == ["L405"]


# ---- L433 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "1001", "-1", "2.5", '"3"', "True"])
def test_l433_max_depth_out_of_range_or_not_an_int(value: str) -> None:
    body = f'x = traverse(t, t, parent="parent_id", child="id", max_depth={value})\n' + W
    (d,) = [d for d in diags(body) if d.code == "L433"]
    assert d.severity is Severity.ERROR and d.hint


def test_l433_step_parameter_is_unbounded() -> None:
    body = (
        "@step\n"
        "def walk(tree: Table[Tree], d: Int) -> Table:\n"
        '    out = traverse(tree, tree, parent="parent_id", child="id", max_depth=d)\n'
        "    return out\n"
        "x = walk(t, d=3)\n" + W
    )
    assert "L433" in codes(body)


@pytest.mark.parametrize("value", ["1", "1000", "DEPTH"])
def test_l433_literal_or_constant_in_range(value: str) -> None:
    ok(f'DEPTH = 7\nx = traverse(t, t, parent="parent_id", child="id", max_depth={value})\n' + W)


# ---- L434 ---------------------------------------------------------------------------------


def test_l434_child_must_be_a_key() -> None:
    body = 'x = traverse(k, k, parent="src", child="dst", max_depth=3)\n' + W
    (d,) = [d for d in diags(body) if d.code == "L434"]
    assert "key" in d.message


def test_l434_parent_and_child_same_type() -> None:
    body = 'x = traverse(t, t, parent="name", child="id", max_depth=3)\n' + W
    assert codes(body) == ["L434"]


def test_l434_start_needs_the_child_column_of_the_same_type() -> None:
    body = (
        'names = select(t, cols=["name"])\n'
        'x = traverse(t, names, parent="parent_id", child="id", max_depth=3)\n' + W
    )
    assert codes(body) == ["L434"]


def test_l434_unknown_columns_are_l401() -> None:
    body = 'x = traverse(t, t, parent="up", child="id", max_depth=3)\n' + W
    assert codes(body) == ["L401"]


def test_l434_not_reported_for_a_keyed_child() -> None:
    ok('x = traverse(t, t, parent="parent_id", child="id", max_depth=3)\n' + W)


# ---- API profile: L431 as an error, L433 parameters, L802 ownership -------------------------

API = loops.PROFILE


def api_codes(body: str, profile: Profile = API) -> list[str]:
    r = check_source(H + body, profile)
    return [d.code for d in r.diagnostics if d.severity is Severity.ERROR]


EXPENSES_WINDOW = """
@endpoint("GET", "/x", auth=Customer)
def x(user: CurrentUser) -> Page:
    expenses = fetch(Expenses)
    mine = owned(expenses, by=user)
    ranked = window(mine, by=["category_id"], order={order}, n=win.row_number())
    result = page(ranked, limit=20)
    return result
"""


def test_l431_is_an_error_in_the_api_profile() -> None:
    r = check_source(H + EXPENSES_WINDOW.format(order='["day"]'), API)
    (d,) = [d for d in r.diagnostics if d.code == "L431"]
    assert d.severity is Severity.ERROR and not r.ok


def test_l431_api_ok_with_a_key_in_order() -> None:
    assert api_codes(EXPENSES_WINDOW.format(order='["day", "id"]')) == []


TREE_WALK = """
@endpoint("GET", "/c/{{id}}", auth=Public)
def c(id: Int(ge=1){extra}) -> Page:
    cats = fetch(Categories)
    root = where(cats, col("id") == id)
    tree = traverse(cats, root, parent="parent_id", child="id", max_depth={depth})
    result = page(tree, limit=100)
    return result
"""


def test_l433_bounded_int_parameter() -> None:
    assert api_codes(TREE_WALK.format(extra=", d: Int(ge=1, le=20) = 5", depth="d")) == []


@pytest.mark.parametrize(
    "param", ["d: Int(ge=1) = 5", "d: Int(ge=0, le=20) = 5", "d: Int(ge=1, le=1001) = 5"]
)
def test_l433_unbounded_or_out_of_range_parameter(param: str) -> None:
    assert api_codes(TREE_WALK.format(extra=f", {param}", depth="d")) == ["L433"]


FOLDER_WALK = """
@endpoint("GET", "/f/{{id}}", auth=Customer)
def f(id: Int(ge=1), user: CurrentUser) -> Page:
    folders = fetch(Folders)
    {edges}
    root = where(edges, col("id") == id)
    tree = traverse(edges, root, parent="parent_id", child="id", max_depth=10)
    result = page(tree, limit=100)
    return result
"""


def test_traverse_over_an_owned_table_needs_owned() -> None:
    edges = "edges = select(folders, cols=['id', 'parent_id', 'owner_id', 'name'])"
    assert api_codes(FOLDER_WALK.format(edges=edges)) == ["L802"]


def test_traverse_over_owned_rows_is_accepted() -> None:
    assert api_codes(FOLDER_WALK.format(edges="edges = owned(folders, by=user)")) == []


# ---- L874 / L875: backend capabilities -------------------------------------------------------


@pytest.fixture
def limited(monkeypatch: pytest.MonkeyPatch):
    """A profile on a registered SQLite-like backend lacking the given capabilities."""

    def make(name: str, **caps: bool) -> Profile:
        attrs = {"name": name, "capabilities": dataclasses.replace(SQLITE.capabilities, **caps)}
        monkeypatch.setitem(DIALECTS, name, type("Limited", (SqliteDialect,), attrs)())
        assert API.api is not None
        return dataclasses.replace(API, api=dataclasses.replace(API.api, backend=name))

    return make


def test_sqlite_has_recursive_queries() -> None:
    assert SQLITE.capabilities.recursive_queries is True
    assert "recursive_queries" in {f.name for f in dataclasses.fields(Capabilities)}


def test_l874_window_without_window_functions(limited) -> None:
    profile = limited("nowin", window_functions=False)
    assert api_codes(EXPENSES_WINDOW.format(order='["day", "id"]'), profile) == ["L874"]
    # traverse needs another capability
    assert api_codes(TREE_WALK.format(extra="", depth="5"), profile) == []


def test_l875_traverse_without_recursive_queries(limited) -> None:
    profile = limited("norec", recursive_queries=False)
    assert api_codes(TREE_WALK.format(extra="", depth="5"), profile) == ["L875"]
    assert api_codes(EXPENSES_WINDOW.format(order='["day", "id"]'), profile) == []


def test_l874_l875_never_fire_for_sqlite() -> None:
    assert api_codes(EXPENSES_WINDOW.format(order='["day", "id"]')) == []
    assert api_codes(TREE_WALK.format(extra="", depth="5")) == []


def test_bench_profile_accepts_window_in_endpoints() -> None:
    body = """
@endpoint("GET", "/orders/ranked", auth=Customer)
def ranked(user: CurrentUser) -> Page:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    ranked = window(mine, order=["created_at", "id"], running=win.cum_sum(col("total")))
    result = page(ranked, limit=20)
    return result
"""
    assert api_codes(body, api_profile()) == []


def test_window_over_a_private_column_is_l803() -> None:
    body = """
@endpoint("GET", "/me", auth=Customer)
def me(user: CurrentUser) -> Page:
    users = fetch(Users)
    mine = owned(users, by=user)
    ranked = window(mine, order=["token", "id"], n=win.row_number())
    shown = select(ranked, cols=["id", "n"])
    result = page(shown, limit=10)
    return result
"""
    assert api_codes(body) == ["L803"]
    assert api_codes(body.replace('["token", "id"]', '["name", "id"]')) == []


def test_window_column_reading_a_private_column_is_private() -> None:
    body = """
@endpoint("GET", "/me", auth=Admin)
def me(user: CurrentUser) -> Page:
    users = fetch(Users)
    ranked = window(users, order=["id"], prev=win.lag(col("token")))
    shown = select(ranked, cols=["id", "prev"])
    result = page(shown, limit=10)
    return result
"""
    assert api_codes(body) == ["L803"]
