"""Final-review regressions for the API checker (N1, N2, m1).

N1: an unowned (public) aggregate over an owned table may group by, filter on and
    aggregate only the columns the schema lists as `public_group`; everything else keeps
    the rows owned (L802 when a non-Admin endpoint returns or checks them).
N2: update() never writes a key column (L404).
m1: an Admin endpoint may insert a Users row (the database assigns its id).
"""

from __future__ import annotations

from textwrap import dedent

import pytest

from bench.api_datasets import (
    ADDRESSES,
    ORDER_ITEMS,
    ORDERS,
    PRODUCTS,
    REVIEWS,
    USERS,
    api_profile,
    schema_description,
)
from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.typesys import Scalar as S
from lattice.typesys import Schema, plain_columns

PROFILE = api_profile()


def check(src: str):
    return check_source("# lattice: v1\n" + dedent(src).lstrip("\n"), PROFILE)


def errors(src: str) -> list[str]:
    return [d.code for d in check(src).diagnostics if d.severity is Severity.ERROR]


def messages(src: str) -> list[str]:
    return [d.message for d in check(src).diagnostics if d.severity is Severity.ERROR]


# ---- N1: the schema annotation ----------------------------------------------------------


def test_public_group_is_declared_for_reviews_and_order_items_only():
    assert REVIEWS.public_group == frozenset({"product_id", "rating"})
    assert ORDER_ITEMS.public_group == frozenset({"product_id"})
    for schema in (USERS, ADDRESSES, ORDERS):
        assert schema.public_group == frozenset()


def test_public_group_is_metadata_outside_schema_equality():
    a = Schema.from_pairs([("id", S.INT), ("x", S.STR)], keys=[("id",)])
    b = Schema.from_pairs([("id", S.INT), ("x", S.STR)], keys=[("id",)], public_group=["x"])
    assert a == b and b.public_group == frozenset({"x"})


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"public_group": ["nope"]}, "public_group"),
        ({"public_group": ["secret"], "private": ["secret"]}, "private"),
        ({"public_group": ["owner_id"], "owner": "owner_id"}, "identif"),
        ({"public_group": ["id"]}, "identif"),
    ],
)
def test_public_group_is_validated(kwargs: dict, needle: str):
    pairs = [("id", S.INT), ("owner_id", S.INT), ("secret", S.STR), ("x", S.STR)]
    with pytest.raises(ValueError, match=needle):
        Schema.from_pairs(pairs, keys=[("id",)], **kwargs)


def test_plain_columns_are_the_public_group():
    assert plain_columns(REVIEWS) == frozenset({"product_id", "rating"})
    assert plain_columns(USERS) == frozenset()
    assert plain_columns(ADDRESSES) == frozenset()
    assert plain_columns(PRODUCTS) == frozenset({"name", "category", "price", "stock", "active"})


def test_schema_description_lists_the_publicly_aggregatable_columns():
    text = schema_description()
    assert "columns that may be aggregated publicly: product_id, rating" in text
    assert "columns that may be aggregated publicly: product_id" in text
    assert text.count("columns that may be aggregated publicly") == 2


# ---- N1: public aggregates over owned tables ---------------------------------------------

USERS_DUMP = """
@endpoint("GET", "/dump", auth=Public)
def dump() -> Page:
    u = fetch(Users)
    g = group(u, by=["email", "name", "role"], n=agg.count())
    p = page(g, limit=5)
    return p
"""

ADDRESSES_DUMP = """
@endpoint("GET", "/dump", auth=Public)
def dump() -> Page:
    a = fetch(Addresses)
    g = group(a, by=["line1", "city", "zip"], n=agg.count())
    p = page(g, limit=3)
    return p
"""

ACCOUNT_ORACLE = """
@endpoint("GET", "/exists", auth=Public)
def exists(email: Str(min_len=1, max_len=100)) -> One | NotFound:
    users = fetch(Users)
    same = where(users, col("email") == email)
    counted = group(same, by=[], n=agg.count())
    known = require(counted, col("n") > 0, else_=NotFound("user"))
    return known
"""


def test_n1_public_group_by_user_columns_is_rejected():
    assert "L802" in errors(USERS_DUMP)


def test_n1_public_group_by_address_columns_is_rejected():
    assert "L802" in errors(ADDRESSES_DUMP)


def test_n1_account_enumeration_through_a_narrowed_count_is_rejected():
    assert "L802" in errors(ACCOUNT_ORACLE)


def test_n1_customer_endpoints_cannot_aggregate_other_owners_either():
    src = USERS_DUMP.replace("auth=Public", "auth=Customer").replace(
        "def dump()", "def dump(user: CurrentUser)"
    )
    assert "L802" in errors(src)


def test_n1_admin_may_aggregate_any_column():
    src = USERS_DUMP.replace("auth=Public", "auth=Admin").replace(
        "def dump()", "def dump(user: CurrentUser)"
    )
    assert errors(src) == []


def test_n1_whole_table_count_of_an_owned_table_is_public():
    src = """
    @endpoint("GET", "/users/count", auth=Public)
    def count() -> One:
        users = fetch(Users)
        g = group(users, by=[], n=agg.count())
        return g
    """
    assert errors(src) == []


def test_n1_orders_have_no_public_columns():
    src = """
    @endpoint("GET", "/stats", auth=Public)
    def stats() -> Page:
        orders = fetch(Orders)
        g = group(orders, by=["status"], n=agg.count())
        p = page(g, limit=5)
        return p
    """
    assert errors(src) == ["L802"]


def test_n1_public_review_statistics_still_check():
    src = """
    @endpoint("GET", "/ratings", auth=Public)
    def ratings() -> Page:
        reviews = fetch(Reviews)
        good = where(reviews, col("rating") >= 3)
        g = group(good, by=["product_id"], n=agg.count(), avg=agg.mean(col("rating")))
        p = page(g, limit=5)
        return p
    """
    assert errors(src) == []


@pytest.mark.parametrize(
    "agg_expr",
    ['agg.max(col("body"))', 'agg.min(col("body"))', 'agg.count_distinct(col("body"))'],
)
def test_n1_aggregates_read_only_public_columns(agg_expr: str):
    src = f"""
    @endpoint("GET", "/ratings", auth=Public)
    def ratings() -> Page:
        reviews = fetch(Reviews)
        g = group(reviews, by=["product_id"], n=agg.count(), x={agg_expr})
        p = page(g, limit=5)
        return p
    """
    assert errors(src) == ["L802"]


def test_n1_filtering_on_a_non_public_column_narrows():
    src = """
    @endpoint("GET", "/ratings", auth=Public)
    def ratings(q: Str(min_len=1, max_len=50)) -> Page:
        reviews = fetch(Reviews)
        hit = where(reviews, col("body") == q)
        g = group(hit, by=["product_id"], n=agg.count())
        p = page(g, limit=5)
        return p
    """
    assert errors(src) == ["L802"]


def test_n1_order_items_per_product_are_public():
    src = """
    @endpoint("GET", "/bestsellers", auth=Public)
    def best() -> Page:
        items = fetch(OrderItems)
        g = group(items, by=["product_id"], n=agg.count())
        p = page(g, limit=5)
        return p
    """
    assert errors(src) == []
    # the quantities are not public
    summed = src.replace("n=agg.count()", 'n=agg.sum(col("qty"))')
    assert errors(summed) == ["L802"]


# ---- N2: update() never writes a key column ----------------------------------------------

RENUMBER = """
@endpoint("POST", "/orders/{order_id}/renumber", auth=Customer)
def ren(order_id: Int(ge=1), new_id: Int(ge=1, le=100000), user: CurrentUser) -> One | NotFound:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    found = where(mine, col("id") == order_id)
    o = one(found, else_=NotFound("order"))
    u = update(o, id=new_id)
    s = select(u, cols=["id", "status"])
    return s
"""


def test_n2_update_of_the_key_is_rejected():
    assert errors(RENUMBER) == ["L404"]
    assert "update() sets the key column 'id' of Orders" in messages(RENUMBER)
    hint = next(d.hint for d in check(RENUMBER).diagnostics if d.code == "L404")
    assert "keys cannot change; insert a new row instead" in (hint or "")


def test_n2_update_of_an_owned_through_key_is_rejected():
    src = """
    @endpoint("PATCH", "/items/{item_id}", auth=Customer)
    def mv(item_id: Int(ge=1), new_id: Int(ge=1, le=100000), user: CurrentUser) -> One | NotFound:
        items = fetch(OrderItems)
        mine = owned(items, by=user)
        found = where(mine, col("id") == item_id)
        o = one(found, else_=NotFound("item"))
        u = update(o, id=new_id)
        s = select(u, cols=["id", "order_id"])
        return s
    """
    assert errors(src) == ["L404"]


def test_n2_update_of_the_key_with_an_expression_is_rejected():
    src = """
    @endpoint("POST", "/orders/{order_id}/cancel", auth=Customer)
    def cancel(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound | Conflict:
        orders = fetch(Orders)
        mine = owned(orders, by=user)
        found = where(mine, col("id") == order_id)
        order = one(found, else_=NotFound("order"))
        ok = require(order, col("status") == "pending", else_=Conflict("not pending"))
        done = update(ok, status="cancelled", id=col("id") + 1)
        shown = select(done, cols=["id", "status"])
        return shown
    """
    assert errors(src) == ["L404"]


def test_n2_admin_cannot_update_a_key_either():
    src = RENUMBER.replace("auth=Customer", "auth=Admin").replace(
        "mine = owned(orders, by=user)\n    found = where(mine,", "found = where(orders,"
    )
    assert errors(src) == ["L404"]


# ---- m1: Admin inserts a user --------------------------------------------------------------

ADMIN_NEW_USER = """
@endpoint("POST", "/users", auth=Admin)
def mk(
    email: Str(min_len=1, max_len=50), token: Str(min_len=1, max_len=50), user: CurrentUser
) -> Created:
    r = insert(Users, email=email, name="n", role="Customer", token=token, password_hash="x")
    s = select(r, cols=["id", "email"])
    return s
"""


def test_m1_admin_may_insert_a_user_without_an_id():
    assert errors(ADMIN_NEW_USER) == []


def test_m1_non_admin_insert_into_users_stays_rejected():
    src = ADMIN_NEW_USER.replace("auth=Admin", "auth=Customer").replace('role="Customer", ', "")
    assert errors(src) != []
    with_owner = src.replace("insert(Users, ", "insert(Users, owner=user, ")
    assert errors(with_owner) != []


def test_m1_admin_still_cannot_set_the_user_id():
    src = ADMIN_NEW_USER.replace("insert(Users, ", "insert(Users, id=7, ")
    assert "L404" in errors(src)


def test_n1_unowned_columns_joined_to_public_statistics_stay_plain():
    # Products is not owned: its columns carry no owner information, so grouping the
    # reviews joined to them by the product's name is still a public aggregate
    src = """
    @endpoint("GET", "/products/ratings", auth=Public)
    def ratings(limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0) -> Page:
        products = fetch(Products)
        slim = select(products, cols=["id", "name"])
        keyed = rename(slim, id="product_id")
        reviews = fetch(Reviews)
        joined = join(keyed, reviews, on=["product_id"], how="left", fanout="allow")
        stats = group(
            joined, by=["product_id", "name"], n=agg.count(col("rating")),
            avg=agg.mean(col("rating")),
        )
        ordered = sort(stats, by=["product_id"])
        result = page(ordered, limit=limit, offset=offset)
        return result
    """
    assert errors(src) == []


def test_n1_sorting_by_a_non_public_column_narrows():
    src = """
    @endpoint("GET", "/top", auth=Public)
    def top() -> One:
        reviews = fetch(Reviews)
        ordered = sort(reviews, by=["body"])
        first = limit(ordered, n=5)
        g = group(first, by=[], best=agg.max(col("rating")))
        return g
    """
    assert errors(src) == ["L802"]
    assert errors(src.replace('by=["body"]', 'by=["rating"]')) == []
