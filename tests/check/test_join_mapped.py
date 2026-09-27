"""join(..., on={"left_col": "right_col"}): joins on differently-named keys, and the
hints that point list-form joins with mismatched names at the dict form."""

from __future__ import annotations

import ast
from textwrap import dedent

import pytest

from bench.api_datasets import api_profile
from lattice.check import check_source
from lattice.profile import Profile
from lattice.typesys import Scalar, Schema, TableT

INT, F, S = Scalar.INT, Scalar.FLOAT, Scalar.STR

ORDERS = Schema.from_pairs(
    [("id", INT), ("customer_id", INT), ("status", S), ("total", F)], keys=[("id",)]
)
ITEMS = Schema.from_pairs(
    [("id", INT), ("order_id", INT), ("product_id", INT), ("qty", INT)], keys=[("id",)]
)
CUSTOMERS = Schema.from_pairs([("id", INT), ("name", S), ("status", S)], keys=[("id",)])
CODES = Schema.from_pairs([("code", S), ("label", S)], keys=[("code",)])
PROFILE = Profile(
    schemas={"Orders": ORDERS, "Items": ITEMS, "Customers": CUSTOMERS, "Codes": CODES}
)

H = "# lattice: v1\n"
LOADS = (
    'orders = load("orders.csv", schema=Orders)\n'
    'items = load("items.csv", schema=Items)\n'
    'customers = load("customers.csv", schema=Customers)\n'
    'codes = load("codes.csv", schema=Codes)\n'
)


def check(body: str, profile: Profile = PROFILE):
    return check_source(H + LOADS + body + 'write(x, "out.parquet")\n', profile)


def errors(body: str, profile: Profile = PROFILE):
    return [d for d in check(body, profile).diagnostics if d.severity.value == "error"]


def joined_type(body: str) -> TableT:
    result = check(body)
    assert result.program is not None, [d.render() for d in result.diagnostics]
    for stmt in result.program.module.body:
        if isinstance(stmt, ast.Assign) and stmt.targets[0].id == "x":  # type: ignore[union-attr]
            t = result.program.types[id(stmt.value)]
            assert isinstance(t, TableT)
            return t
    raise AssertionError("no x")


# ---- typing -------------------------------------------------------------------------


def test_dict_join_keeps_left_key_name_and_drops_right_keys() -> None:
    per_order = 'units = group(items, by=["order_id"], units=agg.sum(col("qty")))\n'
    t = joined_type(per_order + 'x = join(orders, units, on={"id": "order_id"}, how="left")\n')
    assert t.schema.names() == ("id", "customer_id", "status", "total", "units")
    assert t.schema.get("units") is INT
    assert t.keys == (("id",),)  # right is unique on order_id: left keys survive
    assert t.nullable == frozenset({"units"})


def test_dict_join_matches_rename_then_list_join() -> None:
    mapped = joined_type('x = join(items, orders, on={"order_id": "id"}, how="inner")\n')
    renamed = joined_type(
        'o2 = rename(orders, id="order_id")\nx = join(items, o2, on=["order_id"], how="inner")\n'
    )
    assert mapped == renamed
    assert mapped.keys == renamed.keys and mapped.nullable == renamed.nullable


def test_dict_join_multi_key_and_same_name_pair() -> None:
    t = joined_type(
        'c2 = rename(customers, id="cid")\n'
        'x = join(orders, c2, on={"customer_id": "cid", "status": "status"})\n'
    )
    assert t.schema.names() == ("id", "customer_id", "status", "total", "name")


def test_empty_dict_is_a_cross_join() -> None:
    body = "tot = group(items, by=[], n=agg.count())\nx = join(orders, tot, on={})\n"
    t = joined_type(body)
    assert t.schema.names() == ("id", "customer_id", "status", "total", "n")
    body = "x = join(orders, items, on={})\n"
    assert [d.code for d in errors(body)] == ["L404"]


def test_dict_join_collision_is_L416() -> None:
    # customers.status clashes with orders.status; customers.id is the dropped key
    (d,) = errors('x = join(orders, customers, on={"customer_id": "id"})\n')
    assert d.code == "L416" and "'status'" in d.message


def test_right_key_named_like_a_left_column_is_dropped_not_a_clash() -> None:
    # orders.id is the right key: it is dropped, so it does not clash with items.id
    t = joined_type(
        'o2 = select(orders, cols=["id", "total"])\nx = join(items, o2, on={"order_id": "id"})\n'
    )
    assert t.schema.names() == ("id", "order_id", "product_id", "qty", "total")


def test_dict_join_type_mismatch_is_L402() -> None:
    (d,) = errors('c2 = select(codes, cols=["code"])\nx = join(orders, c2, on={"id": "code"})\n')
    assert d.code == "L402"
    assert "'id'" in d.message and "'code'" in d.message


def test_dict_join_L420_uses_right_names() -> None:
    lines = 'lines = select(items, cols=["order_id", "qty"])\n'
    (d,) = errors(lines + 'x = join(orders, lines, on={"id": "order_id"})\n')
    assert d.code == "L420"
    assert "(order_id)" in d.message and "'lines'" in d.message
    ok = 'x = join(orders, lines, on={"id": "order_id"}, fanout="allow")\n'
    assert errors(lines + ok) == []


@pytest.mark.parametrize(
    ("on", "code", "text"),
    [
        ('{"id": 1}', "L404", "on="),
        ('{"nope": "order_id"}', "L401", "left"),
        ('{"id": "nope"}', "L401", "right"),
        ('{"id": "order_id", "customer_id": "order_id"}', "L415", "order_id"),
    ],
)
def test_dict_join_errors(on: str, code: str, text: str) -> None:
    body = 'u = group(items, by=["order_id"], n=agg.count())\n'
    ds = errors(body + f"x = join(orders, u, on={on})\n")
    assert [d.code for d in ds] == [code], ds
    assert text in ds[0].message


def test_dict_join_swapped_mapping_hint() -> None:
    body = 'u = group(items, by=["order_id"], n=agg.count())\n'
    ds = errors(body + 'x = join(orders, u, on={"order_id": "id"})\n')
    assert ds and all(d.code == "L401" for d in ds)
    assert 'on={"id": "order_id"}' in (ds[0].hint or "")


def test_dict_literal_elsewhere_is_still_rejected() -> None:
    ds = errors('x = derive(orders, a={"b": "c"})\n')
    assert [d.code for d in ds] == ["L101"]


# ---- hints for list-form joins with differently-named keys ---------------------------------


def test_list_pair_split_across_sides_gets_one_dict_diagnostic() -> None:
    body = 'u = group(items, by=["order_id"], units=agg.sum(col("qty")))\n'
    (d,) = errors(body + 'x = join(orders, u, on=["id", "order_id"], how="left")\n')
    assert d.code == "L401"
    assert "'id'" in d.message and "'order_id'" in d.message
    assert d.hint == 'keys have different names? use on={"id": "order_id"}'


def test_list_key_missing_on_right_hints_child_fk() -> None:
    body = 'u = group(items, by=["order_id"], units=agg.sum(col("qty")))\n'
    (d,) = errors(body + 'x = join(orders, u, on=["id"], how="left")\n')
    assert d.code == "L401" and "right" in d.message
    assert d.hint == 'keys have different names? use on={"id": "order_id"}'


def test_list_key_missing_on_left_hints_parent_id() -> None:
    body = 'u = group(items, by=["order_id"], units=agg.sum(col("qty")))\n'
    (d,) = errors(body + 'x = join(orders, u, on=["order_id"], how="left")\n')
    assert d.code == "L401" and "left" in d.message
    assert d.hint == 'keys have different names? use on={"id": "order_id"}'


def test_list_fk_missing_on_right_hints_parent_id() -> None:
    (d,) = errors('x = join(items, orders, on=["order_id"], how="left")\n')
    assert d.code == "L401" and "right" in d.message
    assert d.hint == 'keys have different names? use on={"order_id": "id"}'


def test_ambiguous_fk_gives_generic_hint() -> None:
    # order_id and product_id could both pair with orders.id
    i2 = 'i2 = select(items, cols=["order_id", "product_id", "qty"])\n'
    (d,) = errors(
        i2 + 'o = select(orders, cols=["id", "total"])\n'
        'x = join(o, i2, on=["id"], fanout="allow")\n'
    )
    assert d.code == "L401"
    assert d.hint == (
        'keys have different names? use on={"id": "<right column>"} '
        'with one of "order_id", "product_id"'
    )


def test_ambiguous_fk_resolved_by_the_table_name() -> None:
    i2 = 'i2 = select(items, cols=["order_id", "product_id", "qty"])\n'
    (d,) = errors(i2 + 'x = join(orders, i2, on=["id"], fanout="allow")\n')
    assert d.hint == 'keys have different names? use on={"id": "order_id"}'


def test_no_dict_hint_without_a_same_typed_counterpart() -> None:
    # codes has no Int *_id / id column: the plain did-you-mean hint stays
    (d,) = errors('x = join(orders, codes, on=["id"])\n')
    assert d.code == "L401"
    assert "different names" not in (d.hint or "")


# ---- API profile -------------------------------------------------------------------------


def api_src(join_line: str, extra: str = "") -> str:
    return "# lattice: v1\n" + dedent(
        f"""
        @endpoint("GET", "/orders", auth=Customer)
        def list_orders(
            user: CurrentUser, limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0
        ) -> Page:
            orders = fetch(Orders)
            mine = owned(orders, by=user)
            items = fetch(OrderItems)
            my_items = owned(items, by=user)
            units_per_order = group(my_items, by=["order_id"], units=agg.sum(col("qty")))
            {join_line}
            filled = derive(joined, units=coalesce(col("units"), 0))
            shown = select(filled, cols=["id", "status", "created_at", "total", "units"])
            ordered = sort(shown, by=["id"])
            result = page(ordered, limit=limit, offset=offset)
            return result
        """
    ).lstrip("\n")


SMOKE = 'joined = join(mine, units_per_order, on=["id", "order_id"], how="left")'
MAPPED = 'joined = join(mine, units_per_order, on={"id": "order_id"}, how="left")'


def test_api_smoke_program_gets_dict_hint() -> None:
    result = check_source(api_src(SMOKE), api_profile())
    errs = [d for d in result.diagnostics if d.severity.value == "error"]
    assert [d.code for d in errs] == ["L401"], [d.render() for d in errs]
    assert errs[0].hint == 'keys have different names? use on={"id": "order_id"}'


def test_api_mapped_program_checks() -> None:
    result = check_source(api_src(MAPPED), api_profile())
    assert result.program is not None, [d.render() for d in result.diagnostics]


_ME = (
    "users = fetch(Users)\nme = owned(users, by=user)\n"
    "orders = fetch(Orders)\nmine = owned(orders, by=user)\n"
)
_ITEMS = "items = fetch(OrderItems)\norders = fetch(Orders)\nmine = owned(orders, by=user)\n"
PARITY = [
    # (dict-form body, equivalent rename + list-form body, expected error codes)
    (
        _ME + 'x = join(mine, me, on={"customer_id": "id"})\n'
        'shown = select(x, cols=["id", "token"])\n',
        _ME + 'm2 = rename(me, id="customer_id")\nx = join(mine, m2, on=["customer_id"])\n'
        'shown = select(x, cols=["id", "token"])\n',
        ["L803"],
    ),
    (
        _ME + 'x = join(mine, me, on={"customer_id": "id"})\n'
        'shown = select(x, cols=["id", "name"])\n',
        _ME + 'm2 = rename(me, id="customer_id")\nx = join(mine, m2, on=["customer_id"])\n'
        'shown = select(x, cols=["id", "name"])\n',
        [],
    ),
    (
        # the caller's items: inner join to their own orders on the owner_via column
        _ITEMS + 'x = join(items, mine, on={"order_id": "id"})\n'
        'shown = select(x, cols=["id", "qty"])\n',
        _ITEMS + 'm2 = rename(mine, id="order_id")\nx = join(items, m2, on=["order_id"])\n'
        'shown = select(x, cols=["id", "qty"])\n',
        ["L802"],
    ),
    (
        "items = fetch(OrderItems)\norders = fetch(Orders)\n"
        'x = join(items, orders, on={"order_id": "id"})\n'
        'shown = group(x, by=["status"], n=agg.count())\n',
        "items = fetch(OrderItems)\norders = fetch(Orders)\n"
        'o2 = rename(orders, id="order_id")\nx = join(items, o2, on=["order_id"])\n'
        'shown = group(x, by=["status"], n=agg.count())\n',
        ["L802"],
    ),
]


def _endpoint(body: str) -> str:
    lines = [
        *body.strip("\n").split("\n"),
        "result = page(shown, limit=limit, offset=offset)",
        "return result",
    ]
    return (
        "# lattice: v1\n"
        '@endpoint("GET", "/things", auth=Customer)\n'
        "def things(user: CurrentUser, limit: Int(ge=1, le=100) = 20, "
        "offset: Int(ge=0) = 0) -> Page:\n" + "\n".join("    " + ln for ln in lines) + "\n"
    )


@pytest.mark.parametrize(
    ("mapped", "renamed", "expected"), PARITY, ids=["private", "owner", "via", "group"]
)
def test_api_metadata_parity_with_rename_and_list_join(
    mapped: str, renamed: str, expected: list[str] | None
) -> None:
    def codes(body: str) -> list[str]:
        result = check_source(_endpoint(body), api_profile())
        return sorted(d.code for d in result.diagnostics if d.severity.value == "error")

    assert codes(mapped) == codes(renamed)
    if expected is not None:
        assert codes(mapped) == expected


def test_api_join_on_a_private_right_column_is_L803() -> None:
    body = (
        _ME + 'tokens = select(me, cols=["token"])\n'
        'x = join(mine, tokens, on={"status": "token"}, fanout="allow")\n'
        'shown = select(x, cols=["id", "total"])\n'
    )
    result = check_source(_endpoint(body), api_profile())
    errs = [d for d in result.diagnostics if d.severity.value == "error"]
    assert [d.code for d in errs] == ["L803"], [d.render() for d in errs]
    assert "token" in errs[0].message
