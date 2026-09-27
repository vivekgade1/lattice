"""Sub-project 1b node N3 (spec 2026-09-27 lattice-loops §3.3, §3.4, §5): checker rules of
`iterate(...)` (tabular profile) and `for_each(...)` with effect natives (API profile).

One positive and one negative test per code:

* L435 -- iterate's step is a `@step` of type `Table[S] -> Table[S]` (and `t` is a
  `Table[S]`);
* L436 -- iterate's `max=` is an Int literal between 1 and 10,000;
* L437 -- a loop construct in the wrong profile: `iterate` in an API program, `for_each`
  in a tabular one;
* L438 -- an effect native is called only through `for_each` (never in an expression),
  and `for_each` calls an effect native;
* L439 -- the input of `for_each` is bounded (one / insert / update of one row, page, or
  limit with n <= 100);
* L876 -- `for_each` needs the backend capability `transactions`;

plus `until=` (the string "fixpoint" or a Bool column expression), the result type of
iterate, for_each as a statement (L502 when assigned, L503 inside a step) and its
argument typing (L402 / L404 / L106).
"""

from __future__ import annotations

import ast
import dataclasses

import pytest

from lattice.api.backend import DIALECTS, SQLITE, Capabilities, SqliteDialect
from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.profile import ApiProfile, NativeSig, Profile
from lattice.typesys import Scalar, Schema, TableT
from tests.api import effects

INT, S, B = Scalar.INT, Scalar.STR, Scalar.BOOL

COUNTERS = Schema.from_pairs([("id", INT), ("x", INT), ("tag", S)], keys=[("id",)])
OTHER = Schema.from_pairs([("id", INT), ("y", INT)], keys=[("id",)])
TABULAR = Profile(schemas={"Counters": COUNTERS, "Other": OTHER})

H = "# lattice: v1\n"
BUMP = (
    "@step\n"
    "def bump(t: Table[Counters]) -> Table[Counters]:\n"
    '    out = derive(t, x=when(col("x") < 3, then=col("x") + 1, otherwise=col("x")))\n'
    "    return out\n\n"
)
LOAD = 'c = load("c.csv", schema=Counters)\n'
WRITE = 'write(done, "out.csv")\n'


def check_tab(body: str, pre: str = BUMP):
    return check_source(H + pre + LOAD + body + WRITE, TABULAR)


def codes_tab(body: str, pre: str = BUMP) -> list[str]:
    return [d.code for d in check_tab(body, pre).diagnostics if d.severity is Severity.ERROR]


def ok_tab(body: str, pre: str = BUMP):
    r = check_tab(body, pre)
    assert r.program is not None, [d.render() for d in r.diagnostics]
    return r


def iterate_type(r) -> TableT:
    for stmt in r.program.module.body:
        if isinstance(stmt, ast.Assign) and stmt.targets[0].id == "done":  # type: ignore[attr-defined]
            t = r.program.types[id(stmt.value)]
            assert isinstance(t, TableT)
            return t
    raise AssertionError("no 'done' binding")


# ---- iterate: well-formed programs -------------------------------------------------------


def test_iterate_fixpoint_checks_and_keeps_the_schema():
    r = ok_tab('done = iterate(bump, c, until="fixpoint", max=50)\n')
    assert iterate_type(r).schema == COUNTERS


def test_iterate_with_a_predicate_checks():
    ok_tab('done = iterate(bump, c, until=col("x") >= 3, max=10)\n')


def test_iterate_max_may_be_a_constant_bound_to_an_int_literal():
    ok_tab('rounds = 10000\ndone = iterate(bump, c, until="fixpoint", max=rounds)\n')


def test_iterate_steps_may_call_other_steps_and_be_nested():
    pre = BUMP + (
        "@step\n"
        "def settle(t: Table[Counters]) -> Table[Counters]:\n"
        '    inner = iterate(bump, t, until="fixpoint", max=5)\n'
        "    return inner\n\n"
    )
    ok_tab('done = iterate(settle, c, until="fixpoint", max=3)\n', pre)


# ---- L435 ------------------------------------------------------------------------------------


def test_l435_the_step_must_map_its_schema_to_itself():
    pre = (
        "@step\n"
        "def widen(t: Table[Counters]) -> Table:\n"
        '    out = derive(t, z=col("x") + 1)\n'
        "    return out\n\n"
    )
    assert "L435" in codes_tab('done = iterate(widen, c, until="fixpoint", max=5)\n', pre)


def test_l435_the_step_takes_exactly_one_table_and_no_scalars():
    pre = (
        "@step\n"
        "def add(t: Table[Counters], n: Int) -> Table[Counters]:\n"
        '    out = derive(t, x=col("x") + n)\n'
        "    return out\n\n"
    )
    assert "L435" in codes_tab('done = iterate(add, c, until="fixpoint", max=5)\n', pre)


def test_l435_the_first_argument_must_be_a_step():
    assert "L435" in codes_tab('done = iterate(c, c, until="fixpoint", max=5)\n')


def test_l435_negative_a_schema_preserving_step():
    assert "L435" not in codes_tab('done = iterate(bump, c, until="fixpoint", max=5)\n')


def test_the_table_must_have_the_steps_schema():
    load_other = 'o = load("o.csv", schema=Other)\n'
    found = codes_tab(load_other + 'done = iterate(bump, o, until="fixpoint", max=5)\n')
    assert "L418" in found


# ---- L436 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "10001", "-1", "2.5", '"many"', "True"])
def test_l436_max_must_be_an_int_literal_up_to_10000(value: str):
    assert "L436" in codes_tab(f'done = iterate(bump, c, until="fixpoint", max={value})\n')


def test_l436_negative_the_bounds_are_inclusive():
    assert "L436" not in codes_tab('done = iterate(bump, c, until="fixpoint", max=1)\n')
    assert "L436" not in codes_tab('done = iterate(bump, c, until="fixpoint", max=10000)\n')


# ---- until= ----------------------------------------------------------------------------------


def test_until_must_be_fixpoint_or_a_bool_column_expression():
    assert "L404" in codes_tab('done = iterate(bump, c, until="stable", max=5)\n')
    assert "L404" in codes_tab('done = iterate(bump, c, until=col("x"), max=5)\n')
    assert "L401" in codes_tab('done = iterate(bump, c, until=col("nope") > 1, max=5)\n')


def test_iterate_is_a_unit_that_must_be_bound():
    body = 'iterate(bump, c, until="fixpoint", max=5)\ndone = derive(c, z=col("x"))\n'
    assert "L302" in codes_tab(body)


def test_iterate_positional_shape():
    assert "L303" in codes_tab('done = iterate(bump, c, max=5)\n')
    assert "L303" in codes_tab('done = iterate(bump, until="fixpoint", max=5)\n')


# ---- L437 ------------------------------------------------------------------------------------


API_ITERATE = """# lattice: v1
@step
def keep(t: Table[Users]) -> Table[Users]:
    out = where(t, col("id") > 0)
    return out


@endpoint("GET", "/users", auth=Public)
def users() -> Page:
    all_users = fetch(Users)
    settled = iterate(keep, all_users, until="fixpoint", max=5)
    result = page(settled, limit=10)
    return result
"""


def api_codes(source: str, profile: Profile | None = None) -> list[str]:
    r = check_source(source, profile or effects.PROFILE)
    return [d.code for d in r.diagnostics if d.severity is Severity.ERROR]


def test_l437_iterate_is_not_available_in_api_programs():
    assert "L437" in api_codes(API_ITERATE)


def test_l437_for_each_is_not_available_in_tabular_programs():
    body = 'for_each(c, native.send, col("tag"))\ndone = derive(c, z=col("x"))\n'
    assert "L437" in codes_tab(body)


def test_l437_negative_iterate_in_a_tabular_program():
    assert "L437" not in codes_tab('done = iterate(bump, c, until="fixpoint", max=5)\n')


# ---- for_each (API profile) ------------------------------------------------------------------


def endpoint(body: str, signature: str = "user: CurrentUser", ret: str = "One") -> str:
    lines = "".join(f"    {line}\n" for line in body.strip("\n").splitlines())
    return (
        "# lattice: v1\n"
        '@endpoint("POST", "/x", auth=Customer)\n'
        f"def x({signature}) -> {ret}:\n" + lines
    )


INSERT = 'made = insert(Items, owner=user, title=title, done=False)\n'


def test_for_each_after_an_insert_checks():
    src = endpoint(
        INSERT + 'for_each(made, native.item_created, col("id"), col("title"), col("done"))\n'
        "return made\n",
        "title: Str(max_len=50), user: CurrentUser",
        "Created",
    )
    r = check_source(src, effects.PROFILE)
    assert r.program is not None, [d.render() for d in r.diagnostics]


def test_for_each_arguments_may_be_computed_and_parameters():
    src = endpoint(
        "items = fetch(Items)\n"
        "mine = owned(items, by=user)\n"
        'ordered = sort(mine, by=["id"])\n'
        "shown = page(ordered, limit=limit)\n"
        'for_each(shown, native.item_viewed, col("id"), fn.upper(col("title")), note)\n'
        "return shown\n",
        "note: Str, user: CurrentUser, limit: Int(ge=1, le=100) = 10",
        "Page",
    )
    r = check_source(src, effects.PROFILE)
    assert r.program is not None, [d.render() for d in r.diagnostics]


# ---- L438 ------------------------------------------------------------------------------------


def test_l438_an_effect_native_in_an_expression():
    src = endpoint(
        "items = fetch(Items)\n"
        "mine = owned(items, by=user)\n"
        'sent = derive(mine, ok=native.send_welcome(col("title"), col("title")))\n'
        "one_row = one(sent, else_=NotFound())\n"
        "return one_row\n"
    )
    assert "L438" in api_codes(src)


def test_l438_for_each_needs_an_effect_native():
    src = endpoint(
        INSERT + 'for_each(made, native.shout, col("title"))\nreturn made\n',
        "title: Str(max_len=50), user: CurrentUser",
        "Created",
    )
    assert "L438" in api_codes(src)


def test_l438_negative_a_pure_native_in_an_expression_and_an_effect_via_for_each():
    src = endpoint(
        INSERT
        + 'loud = derive(made, loud=native.shout(col("title")))\n'
        + 'for_each(loud, native.item_created, col("id"), col("loud"), col("done"))\n'
        + "return made\n",
        "title: Str(max_len=50), user: CurrentUser",
        "Created",
    )
    assert "L438" not in api_codes(src)


def test_for_each_argument_types_and_count():
    base = "title: Str(max_len=50), user: CurrentUser"
    wrong_type = endpoint(
        INSERT + 'for_each(made, native.item_created, col("title"), col("title"), col("done"))\n'
        "return made\n",
        base,
        "Created",
    )
    assert "L402" in api_codes(wrong_type)
    wrong_count = endpoint(
        INSERT + 'for_each(made, native.item_created, col("id"))\nreturn made\n', base, "Created"
    )
    assert "L404" in api_codes(wrong_count)
    unknown = endpoint(
        INSERT + 'for_each(made, native.nope, col("id"))\nreturn made\n', base, "Created"
    )
    assert "L106" in api_codes(unknown)
    unknown_col = endpoint(
        INSERT + 'for_each(made, native.item_created, col("id"), col("zzz"), col("done"))\n'
        "return made\n",
        base,
        "Created",
    )
    assert "L401" in api_codes(unknown_col)


def test_for_each_is_a_statement():
    src = endpoint(
        INSERT + 'sent = for_each(made, native.item_created, col("id"), col("title"), '
        'col("done"))\nreturn made\n',
        "title: Str(max_len=50), user: CurrentUser",
        "Created",
    )
    assert "L502" in api_codes(src)


def test_for_each_is_not_allowed_in_a_step():
    src = (
        "# lattice: v1\n"
        "@step\n"
        "def notify(t: Table[Items]) -> Table[Items]:\n"
        '    for_each(t, native.item_created, col("id"), col("title"), col("done"))\n'
        "    return t\n"
    )
    assert "L503" in api_codes(src)


# ---- L439 ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "signature"),
    [
        # every row of a table
        (
            "items = fetch(Items)\nmine = owned(items, by=user)\n"
            'for_each(mine, native.item_viewed, col("id"), col("title"), "x")\n'
            "shown = page(mine, limit=10)\nreturn shown\n",
            "user: CurrentUser",
        ),
        # limit above 100
        (
            "items = fetch(Items)\nmine = owned(items, by=user)\n"
            'top = limit(mine, n=101)\nfor_each(top, native.item_viewed, col("id"), '
            'col("title"), "x")\nshown = page(mine, limit=10)\nreturn shown\n',
            "user: CurrentUser",
        ),
        # an update of every row
        (
            "items = fetch(Items)\nmine = owned(items, by=user)\n"
            "closed = update(mine, done=True)\n"
            'for_each(closed, native.item_viewed, col("id"), col("title"), "x")\n'
            "shown = page(mine, limit=10)\nreturn shown\n",
            "user: CurrentUser",
        ),
    ],
)
def test_l439_the_input_must_be_bounded(body: str, signature: str):
    assert "L439" in api_codes(endpoint(body, signature, "Page"))


@pytest.mark.parametrize(
    "body",
    [
        # one()
        "items = fetch(Items)\nmine = owned(items, by=user)\n"
        'found = where(mine, col("id") == 1)\nrow = one(found, else_=NotFound())\n'
        'for_each(row, native.item_viewed, col("id"), col("title"), "x")\nreturn row\n',
        # update of one row
        "items = fetch(Items)\nmine = owned(items, by=user)\n"
        'found = where(mine, col("id") == 1)\nrow = one(found, else_=NotFound())\n'
        "closed = update(row, done=True)\n"
        'for_each(closed, native.item_viewed, col("id"), col("title"), "x")\nreturn closed\n',
        # limit(n <= 100), and a row-wise op of it
        "items = fetch(Items)\nmine = owned(items, by=user)\n"
        'top = limit(mine, n=100)\nnamed = derive(top, t2=fn.upper(col("title")))\n'
        'for_each(named, native.item_viewed, col("id"), col("t2"), "x")\n'
        'found = where(mine, col("id") == 1)\nrow = one(found, else_=NotFound())\nreturn row\n',
    ],
)
def test_l439_negative_bounded_inputs(body: str):
    found = api_codes(endpoint(body))
    assert "L439" not in found, found


# ---- L876 ------------------------------------------------------------------------------------


def _without(name: str, **caps: bool) -> ApiProfile:
    base = SQLITE.capabilities
    dialect = SqliteDialect()
    dialect.capabilities = dataclasses.replace(base, **caps)  # type: ignore[misc]
    DIALECTS[name] = dialect  # type: ignore[assignment]
    return dataclasses.replace(effects.API, backend=name)


@pytest.fixture
def no_transactions():
    api = _without("notxn", transactions=False)
    yield Profile(schemas={}, api=api)
    DIALECTS.pop("notxn", None)


def test_l876_for_each_needs_transactions(no_transactions: Profile):
    src = endpoint(
        INSERT + 'for_each(made, native.item_created, col("id"), col("title"), col("done"))\n'
        "return made\n",
        "title: Str(max_len=50), user: CurrentUser",
        "Created",
    )
    assert "L876" in api_codes(src, no_transactions)
    assert "L876" not in api_codes(src)


def test_l876_negative_no_for_each(no_transactions: Profile):
    src = endpoint(INSERT + "return made\n", "title: Str(max_len=50), user: CurrentUser", "Created")
    assert "L876" not in api_codes(src, no_transactions)


# ---- the profile ---------------------------------------------------------------------------


def test_effect_natives_have_no_result_and_are_not_nullable():
    with pytest.raises(ValueError):
        dataclasses.replace(
            effects.API, natives={"bad": NativeSig((S,), S, effect=True)}
        )
    with pytest.raises(ValueError):
        dataclasses.replace(effects.API, natives={"bad": NativeSig((S,))})
    sig = NativeSig((S, S), effect=True)
    assert sig.result is None and sig.effect


def test_capabilities_have_transactions():
    assert isinstance(SQLITE.capabilities, Capabilities) and SQLITE.capabilities.transactions


def test_for_each_over_the_caller_and_its_private_columns():
    ok = endpoint(
        'for_each(user, native.send_welcome, col("email"), col("full_name"))\n'
        "items = fetch(Items)\nmine = owned(items, by=user)\n"
        'found = where(mine, col("id") == 1)\nrow = one(found, else_=NotFound())\nreturn row\n',
        ret="One | NotFound",
    )
    assert api_codes(ok) == []
    leak = ok.replace('col("full_name"))', 'col("token"))')
    assert "L803" in api_codes(leak)
