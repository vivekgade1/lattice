"""Soundness regressions for the API checker (reviewer findings B1-B4, M1-M4, minors).

Every program here was accepted by the checker and exploited at runtime (IDOR writes,
cross-owner reads, private-column leaks, unbounded writes, 500s, undeclared 404s).
Each must now be rejected by `check_source`; the safe spellings next to them still check.
"""

from __future__ import annotations

from textwrap import dedent

import pytest

from bench.api_datasets import USERS, api_profile, schema_description
from lattice.check import check_source
from lattice.diagnostics import Severity

PROFILE = api_profile()


def check(src: str):
    return check_source("# lattice: v1\n" + dedent(src).lstrip("\n"), PROFILE)


def errors(src: str) -> list[str]:
    return [d.code for d in check(src).diagnostics if d.severity is Severity.ERROR]


def diag(src: str, code: str):
    found = [d for d in check(src).diagnostics if d.code == code]
    assert found, check(src).diagnostics
    return found[0]


# ---- B1: a derive that overwrites a key column loses the rows' identity --------------

CANCEL_REDERIVED = """
@endpoint("POST", "/orders/{order_id}/cancel", auth=Customer)
def cancel(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    first = limit(mine, n=1)
    d = derive(first, id=order_id)
    target = one(d, else_=NotFound("order"))
    done = update(target, status="cancelled")
    shown = select(done, cols=["id", "status"])
    return shown
"""

DELETE_REDERIVED = """
@endpoint("DELETE", "/orders/{order_id}", auth=Customer)
def rm(order_id: Int(ge=1), user: CurrentUser) -> NoContent:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    d = derive(mine, id=order_id)
    gone = delete(d)
    return gone
"""

ITEM_PINNED_BY_DERIVED_ID = """
@endpoint("POST", "/orders/{order_id}/items", auth=Customer)
def add(
    order_id: Int(ge=1), product_id: Int(ge=1), qty: Int(ge=1, le=10), user: CurrentUser
) -> Created | NotFound:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    first = limit(mine, n=1)
    d = derive(first, id=order_id)
    found = where(d, col("id") == order_id)
    parent = one(found, else_=NotFound("order"))
    item = insert(OrderItems, order_id=order_id, product_id=product_id, qty=qty, unit_price=1.0)
    return item
"""


def test_b1_update_after_key_overwriting_derive_is_rejected():
    errs = errors(CANCEL_REDERIVED)
    assert "L841" in errs
    assert not check(CANCEL_REDERIVED).ok


def test_b1_delete_after_key_overwriting_derive_is_rejected():
    errs = errors(DELETE_REDERIVED)
    assert "L841" in errs


def test_b1_owned_through_insert_pinned_by_derived_key_is_l802():
    assert "L802" in errors(ITEM_PINNED_BY_DERIVED_ID)
    # the pin itself fails too, even once the lookup is owner-checked again
    rechecked = ITEM_PINNED_BY_DERIVED_ID.replace(
        'found = where(d, col("id") == order_id)',
        'again = owned(d, by=user)\n    found = where(again, col("id") == order_id)',
    )
    d = diag(rechecked, "L802")
    assert "OrderItems.order_id" in d.message


@pytest.mark.parametrize(
    "rewrite",
    [
        'd = derive(first, customer_id=col("customer_id") + 0)',
        'd = rename(first, id="order_ref")',
    ],
)
def test_b1_overwriting_or_renaming_identity_columns_blocks_writes(rewrite: str):
    src = CANCEL_REDERIVED.replace("d = derive(first, id=order_id)", rewrite)
    assert "L841" in errors(src)


def test_b1_derive_of_a_plain_column_keeps_the_rows_writable():
    src = CANCEL_REDERIVED.replace(
        "d = derive(first, id=order_id)", 'd = derive(first, note=col("status"))'
    )
    assert errors(src) == []


# ---- B2: owned() must see one fetched table ------------------------------------------

OWNED_OVER_JOIN = """
@endpoint("GET", "/leak", auth=Customer)
def leak(user: CurrentUser) -> Page:
    orders = fetch(Orders)
    reviews = fetch(Reviews)
    r2 = rename(reviews, customer_id="review_customer")
    j = join(orders, r2, on=["id"])
    mine = owned(j, by=user)
    s = select(mine, cols=["id", "customer_id", "review_customer", "body"])
    p = page(s, limit=5)
    return p
"""


def test_b2_owned_over_a_join_is_l404():
    d = diag(OWNED_OVER_JOIN, "L404")
    assert "owned()" in d.message
    assert d.hint is not None and "right after fetch, before joining" in d.hint


def test_b2_owned_over_a_where_sort_select_chain_is_ok():
    src = """
    @endpoint("GET", "/orders", auth=Customer)
    def lst(user: CurrentUser) -> Page:
        orders = fetch(Orders)
        paid = where(orders, col("status") == "paid")
        ordered = sort(paid, by=["id"])
        slim = select(ordered, cols=["id", "customer_id", "total"])
        mine = owned(slim, by=user)
        p = page(mine, limit=5)
        return p
    """
    assert errors(src) == []


# ---- B3: private lineage through @step bodies ------------------------------------------

STEP_RENAMES_TOKEN = """
@step
def leak(t: Table[Users]) -> Table:
    r = rename(t, token="secret")
    return r

@endpoint("GET", "/tok", auth=Customer)
def tok(user: CurrentUser) -> One:
    x = leak(user)
    s = select(x, cols=["id", "secret"])
    return s
"""

STEP_DERIVES_HASH = """
@step
def leak(t: Table[Users]) -> Table:
    d = derive(t, secret=col("password_hash"))
    s = select(d, cols=["id", "email", "secret"])
    return s

@endpoint("GET", "/toks", auth=Admin)
def toks(user: CurrentUser) -> Page:
    u = fetch(Users)
    x = leak(u)
    p = page(x, limit=3)
    return p
"""


@pytest.mark.parametrize("src", [STEP_RENAMES_TOKEN, STEP_DERIVES_HASH])
def test_b3_private_columns_stay_private_through_steps(src: str):
    assert "L803" in errors(src)


def test_b3_step_not_touching_private_columns_keeps_others_public():
    src = """
    @step
    def names(t: Table[Users]) -> Table:
        s = select(t, cols=["id", "name"])
        return s

    @endpoint("GET", "/me", auth=Customer)
    def me(user: CurrentUser) -> One:
        x = names(user)
        return x
    """
    assert errors(src) == []


# ---- B4: public aggregates may not expose rows -----------------------------------------

PER_ORDER_STATS = """
@endpoint("GET", "/stats", auth=Public)
def stats() -> Page:
    orders = fetch(Orders)
    d = derive(orders, k=col("id") + 0)
    g = group(d, by=["k"], who=agg.sum(col("customer_id")), total=agg.sum(col("total")))
    p = page(g, limit=5)
    return p
"""


def test_b4_group_by_a_key_derived_column_is_per_row():
    assert errors(PER_ORDER_STATS) == ["L802"]


@pytest.mark.parametrize(
    "agg_expr",
    [
        'agg.sum(col("customer_id"))',
        'agg.mean(col("customer_id"))',
        'agg.count_distinct(col("customer_id"))',
        'agg.count(col("customer_id"))',
    ],
)
def test_b4_any_aggregate_reading_the_owner_keeps_rows_owned(agg_expr: str):
    src = f"""
    @endpoint("GET", "/stats", auth=Public)
    def stats() -> Page:
        orders = fetch(Orders)
        g = group(orders, by=["status"], n=agg.count(), who={agg_expr})
        p = page(g, limit=5)
        return p
    """
    assert errors(src) == ["L802"]


def test_b4_group_by_a_derived_plain_column_stays_owned():
    src = """
    @endpoint("GET", "/stats", auth=Public)
    def stats() -> Page:
        orders = fetch(Orders)
        d = derive(orders, big=col("total") > 100.0)
        g = group(d, by=["big"], n=agg.count())
        p = page(g, limit=5)
        return p
    """
    assert errors(src) == ["L802"]


def test_b4_filter_on_a_key_derived_column_narrows():
    src = """
    @endpoint("GET", "/stats", auth=Public)
    def stats() -> Page:
        orders = fetch(Orders)
        d = derive(orders, k=col("id") + 0)
        one_order = where(d, col("k") == 5)
        g = group(one_order, by=["status"], total=agg.sum(col("total")))
        p = page(g, limit=5)
        return p
    """
    assert errors(src) == ["L802"]


def test_b4_group_by_status_counting_rows_is_public():
    src = """
    @endpoint("GET", "/stats", auth=Public)
    def stats() -> Page:
        orders = fetch(Orders)
        g = group(orders, by=["status"], n=agg.count(), revenue=agg.sum(col("total")))
        p = page(g, limit=5)
        return p
    """
    assert errors(src) == []


def test_b4_step_computing_owner_values_keeps_them_owned():
    src = """
    @step
    def tag(t: Table[Orders]) -> Table:
        d = derive(t, k=col("customer_id") + 0)
        return d

    @endpoint("GET", "/stats", auth=Public)
    def stats() -> Page:
        orders = fetch(Orders)
        x = tag(orders)
        g = group(x, by=["k"], n=agg.count())
        p = page(g, limit=5)
        return p
    """
    assert errors(src) == ["L802"]


# ---- M1: parameters reaching writes through derive -------------------------------------

UNBOUNDED_VIA_DERIVE = """
@endpoint("PATCH", "/orders/{order_id}", auth=Customer)
def upd(order_id: Int(ge=1), status: Str, user: CurrentUser) -> One | NotFound:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    found = where(mine, col("id") == order_id)
    one_ = one(found, else_=NotFound("order"))
    d = derive(one_, status=status)
    done = update(d, status=col("status"))
    shown = select(done, cols=["id", "status"])
    return shown
"""


def test_m1_unbounded_parameter_written_through_derive_is_l811():
    d = diag(UNBOUNDED_VIA_DERIVE, "L811")
    assert "'status'" in d.message


def test_m1_parameter_only_filtering_is_not_written():
    src = UNBOUNDED_VIA_DERIVE.replace(
        "d = derive(one_, status=status)", 'd = derive(one_, status=col("status"))'
    ).replace("status: Str,", "")
    # the derive is now a no-op (L304), but no parameter reaches the write
    assert "L811" not in errors(src)


# ---- M2: inserts supply every required column and never the key -----------------------


def _insert(values: str) -> str:
    return f"""
    @endpoint("POST", "/reviews", auth=Customer)
    def add(
        id: Int(ge=1, le=10), product_id: Int(ge=1), rating: Int(ge=1, le=5), user: CurrentUser
    ) -> Created:
        r = insert(Reviews, owner=user, {values})
        return r
    """


def test_m2_insert_missing_a_not_null_column_is_l404():
    d = diag(_insert("product_id=product_id, rating=rating"), "L404")
    assert "body" in d.message


def test_m2_insert_setting_the_key_is_l404():
    d = diag(_insert('id=id, product_id=product_id, rating=rating, body="x"'), "L404")
    assert "'id'" in d.message
    assert d.hint is not None and "remove id=" in d.hint


def test_m2_complete_insert_is_ok():
    assert errors(_insert('product_id=product_id, rating=rating, body="x"')) == []


def test_m2_admin_insert_must_supply_the_owner_column():
    src = """
    @endpoint("POST", "/orders", auth=Admin)
    def add(created_at: Date, user: CurrentUser) -> Created:
        r = insert(Orders, status="pending", created_at=created_at, total=0.0)
        return r
    """
    d = diag(src, "L404")
    assert "customer_id" in d.message


# ---- M3: One/Created respond with exactly one row, or declare NotFound -----------------

WHERE_AFTER_ONE = """
@endpoint("GET", "/orders/{order_id}/if-paid", auth=Customer)
def get(order_id: Int(ge=1), user: CurrentUser) -> One | Conflict:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    found = where(mine, col("id") == order_id)
    o = one(found, else_=Conflict("order"))
    p = where(o, col("status") == "paid")
    return p
"""


def test_m3_possibly_empty_one_response_needs_notfound():
    d = diag(WHERE_AFTER_ONE, "L821")
    assert "NotFound" in d.message


def test_m3_declaring_notfound_accepts_the_empty_response():
    src = WHERE_AFTER_ONE.replace("-> One | Conflict:", "-> One | Conflict | NotFound:")
    assert errors(src) == []
    assert "L822" not in [d.code for d in check(src).diagnostics]


def test_m3_exactly_one_row_needs_no_notfound():
    src = """
    @endpoint("GET", "/me/count", auth=Customer)
    def cnt(user: CurrentUser) -> One:
        orders = fetch(Orders)
        mine = owned(orders, by=user)
        g = group(mine, by=[], n=agg.count())
        shown = derive(g, twice=col("n") * 2)
        return shown
    """
    assert errors(src) == []


# ---- M4: Users is owned by its own key; role is admin-only -----------------------------


def test_m4_users_schema_metadata():
    assert USERS.owner == "id"
    assert USERS.admin_write == frozenset({"role"})
    text = schema_description()
    assert "owner column: id" in text
    assert "role" in text.split("- Users")[1].split("- Products")[0]


def test_m4_customer_updating_another_user_is_l802():
    src = """
    @endpoint("POST", "/promote/{uid}", auth=Customer)
    def promote(uid: Int(ge=1), user: CurrentUser) -> One | NotFound:
        users = fetch(Users)
        found = where(users, col("id") == uid)
        target = one(found, else_=NotFound("user"))
        done = update(target, name="x")
        shown = select(done, cols=["id", "name"])
        return shown
    """
    assert "L802" in errors(src)


def test_m4_customer_listing_users_is_l802():
    src = """
    @endpoint("GET", "/users", auth=Customer)
    def users(user: CurrentUser) -> Page:
        u = fetch(Users)
        s = select(u, cols=["id", "email", "name"])
        p = page(s, limit=3)
        return p
    """
    assert errors(src) == ["L802"]


def test_m4_self_escalation_is_l802():
    src = """
    @endpoint("PATCH", "/me", auth=Customer)
    def me(name: Str(max_len=50), user: CurrentUser) -> One:
        done = update(user, name=name, role="Admin")
        shown = select(done, cols=["id", "name", "role"])
        return shown
    """
    d = diag(src, "L802")
    assert "role" in d.message


def test_m4_admin_may_write_role_and_customer_may_rename_self():
    admin = """
    @endpoint("PATCH", "/users/{uid}/role", auth=Admin)
    def set_role(
        uid: Int(ge=1), role: OneOf("Customer", "Admin"), user: CurrentUser
    ) -> One | NotFound:
        users = fetch(Users)
        found = where(users, col("id") == uid)
        target = one(found, else_=NotFound("user"))
        done = update(target, role=role)
        shown = select(done, cols=["id", "role"])
        return shown
    """
    assert errors(admin) == []
    me = """
    @endpoint("PATCH", "/me", auth=Customer)
    def me(name: Str(max_len=50), user: CurrentUser) -> One:
        done = update(user, name=name)
        shown = select(done, cols=["id", "name"])
        return shown
    """
    assert errors(me) == []


def test_m4_owned_users_is_the_callers_row():
    src = """
    @endpoint("GET", "/me2", auth=Customer)
    def me2(user: CurrentUser) -> Page:
        users = fetch(Users)
        mine = owned(users, by=user)
        s = select(mine, cols=["id", "email"])
        p = page(s, limit=1)
        return p
    """
    assert errors(src) == []


# ---- minors ----------------------------------------------------------------------------


def test_private_value_oracle_in_predicate_is_l803():
    src = """
    @endpoint("GET", "/probe", auth=Public)
    def probe_(prefix: Str(max_len=64)) -> Page:
        u = fetch(Users)
        f = where(u, fn.starts_with(col("token"), prefix=prefix))
        s = select(f, cols=["id"])
        p = page(s, limit=100)
        return p
    """
    assert "L803" in errors(src)


@pytest.mark.parametrize(
    "stmt",
    [
        'f = sort(user, by=["token"])',
        'f = require(user, col("password_hash") == "x", else_=Forbidden("no"))',
        'f = group(user, by=["id"], n=agg.count_distinct(col("token")))',
    ],
)
def test_private_value_oracle_in_customer_endpoints_is_l803(stmt: str):
    src = f"""
    @endpoint("GET", "/probe", auth=Customer)
    def probe_(user: CurrentUser) -> One | Forbidden:
        {stmt}
        s = select(user, cols=["id"])
        return s
    """
    assert "L803" in errors(src)


def test_private_column_in_admin_predicate_is_allowed():
    src = """
    @endpoint("GET", "/probe", auth=Admin)
    def probe_(prefix: Str(max_len=64), user: CurrentUser) -> Page:
        u = fetch(Users)
        f = where(u, fn.starts_with(col("token"), prefix=prefix))
        s = select(f, cols=["id"])
        p = page(s, limit=100)
        return p
    """
    assert errors(src) == []


STATUS_ORACLE = """
@endpoint("POST", "/orders/{order_id}/cancel", auth=Customer)
def cancel(order_id: Int(ge=1), user: CurrentUser) -> One | Conflict:
    orders = fetch(Orders)
    found = where(orders, col("id") == order_id)
    one_ = one(found, else_=Conflict("none"))
    ok = require(one_, col("status") == "pending", else_=Conflict("not pending"))
    mine = owned(ok, by=user)
    done = update(mine, status="cancelled")
    shown = select(done, cols=["id", "status"])
    return shown
"""


def test_status_code_oracle_check_before_owned_is_l802():
    d = diag(STATUS_ORACLE, "L802")
    assert "Orders" in d.message


@pytest.mark.parametrize("name", ["__ord", "__x", "__ok_a"])
def test_dunder_column_names_are_reserved(name: str):
    src = f"""
    @endpoint("GET", "/p", auth=Public)
    def p_() -> Page:
        t = fetch(Products)
        d = derive(t, {name}=0 - col("id"))
        p = page(d, limit=3)
        return p
    """
    assert "L205" in errors(src)


def test_dunder_rename_and_aggregate_names_are_reserved():
    src = """
    @endpoint("GET", "/p", auth=Public)
    def p_() -> Page:
        t = fetch(Products)
        r = rename(t, name="__name")
        g = group(r, by=["category"], __n=agg.count())
        p = page(g, limit=3)
        return p
    """
    assert errors(src).count("L205") == 2


def test_m2_insert_into_a_table_owned_by_its_key_is_l404():
    src = """
    @endpoint("POST", "/users", auth=Customer)
    def add(email: Str(max_len=80), user: CurrentUser) -> Created:
        r = insert(Users, owner=user, email=email, name="n", role="Customer", token="t",
                   password_hash="h")
        return r
    """
    errs = errors(src)
    assert "L404" in errs
    assert any("owned through their key" in d.message for d in check(src).diagnostics)


def test_m3_step_over_the_caller_is_exactly_one_row():
    src = """
    @step
    def slim(t: Table[Users]) -> Table:
        s = select(t, cols=["id", "name"])
        return s

    @endpoint("GET", "/me", auth=Customer)
    def me(user: CurrentUser) -> One:
        x = slim(user)
        return x
    """
    assert errors(src) == []
    filtered = src.replace(
        's = select(t, cols=["id", "name"])',
        'w = where(t, col("name") == "x")\n        s = select(w, cols=["id", "name"])',
    )
    assert errors(filtered) == ["L821"]


def test_prompt_and_fixture_rules_keep_the_safe_review_check():
    src = """
    @endpoint("POST", "/products/{product_id}/my-review", auth=Customer)
    def review(product_id: Int(ge=1), user: CurrentUser) -> One | Conflict:
        reviews = fetch(Reviews)
        mine = owned(reviews, by=user)
        same = where(mine, col("product_id") == product_id)
        counted = group(same, by=[], n=agg.count())
        fresh = require(counted, col("n") == 0, else_=Conflict("already reviewed"))
        return fresh
    """
    assert errors(src) == []
    # without owned(), counting everyone's reviews of the product is a public aggregate
    everyone = src.replace("mine = owned(reviews, by=user)\n        ", "").replace(
        "where(mine,", "where(reviews,"
    )
    assert errors(everyone) == []
    # ... but a count narrowed to one customer's rows is per-owner: a check over it is L802
    theirs = everyone.replace(
        'col("product_id") == product_id',
        '(col("product_id") == product_id) & (col("customer_id") == 3)',
    )
    assert errors(theirs) == ["L802"]
