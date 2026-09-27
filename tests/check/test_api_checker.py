"""The API-profile checker: @endpoint declarations and rules L801-L841."""

from textwrap import dedent

import pytest

from lattice.check import check_source
from lattice.check.api_info import MISSING, ParamSpec
from lattice.diagnostics import Severity
from lattice.profile import ApiProfile, Profile
from lattice.typesys import Scalar, Schema

S = Scalar

USERS = Schema.from_pairs(
    [("id", S.INT), ("email", S.STR), ("role", S.STR), ("token", S.STR), ("password_hash", S.STR)],
    keys=[("id",)],
    private=["password_hash", "token"],
)
ORDERS = Schema.from_pairs(
    [("id", S.INT), ("customer_id", S.INT), ("status", S.STR), ("total", S.FLOAT)],
    keys=[("id",)],
    owner="customer_id",
)
PRODUCTS = Schema.from_pairs(
    [("id", S.INT), ("name", S.STR), ("price", S.FLOAT), ("stock", S.INT)],
    keys=[("id",)],
)
API = ApiProfile(
    tables={"Users": USERS, "Orders": ORDERS, "Products": PRODUCTS},
    table_names={"Users": "users", "Orders": "orders", "Products": "products"},
    roles=frozenset({"Public", "Customer", "Admin"}),
)
PROFILE = Profile(schemas={}, api=API)
TABULAR = Profile(schemas={"Orders": ORDERS})


def check(src: str, profile: Profile = PROFILE):
    return check_source("# lattice: v1\n" + dedent(src).lstrip("\n"), profile)


def codes(src: str, profile: Profile = PROFILE) -> list[str]:
    return [d.code for d in check(src, profile).diagnostics]


def errors(src: str) -> list[str]:
    return [d.code for d in check(src).diagnostics if d.severity is Severity.ERROR]


def diag(src: str, code: str):
    found = [d for d in check(src).diagnostics if d.code == code]
    assert found, check(src).diagnostics
    return found[0]


CANCEL = """
@endpoint("POST", "/orders/{order_id}/cancel", auth=Customer)
def cancel(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound | Conflict:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    found = where(mine, col("id") == order_id)
    order = one(found, else_=NotFound("order"))
    ok = require(order, col("status") == "pending", else_=Conflict("order is not pending"))
    done = update(ok, status="cancelled")
    return done
"""

LIST_MINE = """
@endpoint("GET", "/orders", auth=Customer)
def list_orders(user: CurrentUser, limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0) -> Page:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    ordered = sort(mine, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
"""


# ---- the spec example and basic acceptance ------------------------------------------


def test_cancel_example_checks_clean():
    result = check(CANCEL)
    assert result.diagnostics == ()
    assert result.ok


def test_cancel_endpoint_info():
    program = check(CANCEL).program
    assert program is not None
    info = program.endpoints["cancel"]
    assert (info.name, info.method, info.path, info.auth) == (
        "cancel",
        "POST",
        "/orders/{order_id}/cancel",
        "Customer",
    )
    assert info.success == "One"
    assert info.errors == ("NotFound", "Conflict")
    assert info.params == (
        ParamSpec("order_id", "path", S.INT, ge=1),
        ParamSpec("user", "user", None),
    )
    assert info.node.name == "cancel"
    assert info.span.line == 3  # the def line (after header + decorator)


def test_param_specs_sources_constraints_defaults():
    src = """
    @endpoint("POST", "/products", auth=Admin)
    def add(user: CurrentUser, name: Str(min_len=1, max_len=80), price: Float(ge=0, le=1000.5),
            kind: OneOf("a", "b") = "a", live: Bool = True, stock: Int(ge=0, le=500) = 0
            ) -> Created:
        row = insert(Products, name=name, price=price, stock=stock)
        return row
    """
    result = check(src)
    assert result.ok, result.diagnostics
    params = {p.name: p for p in result.program.endpoints["add"].params}
    assert params["name"] == ParamSpec("name", "body", S.STR, min_len=1, max_len=80)
    assert params["price"] == ParamSpec("price", "body", S.FLOAT, ge=0, le=1000.5)
    assert params["kind"] == ParamSpec("kind", "body", S.STR, one_of=("a", "b"), default="a")
    assert params["live"].default is True
    assert params["stock"].default == 0
    assert params["name"].default is MISSING
    # L204 would warn about unused kind/live: warnings only
    assert all(d.severity is Severity.WARNING for d in result.diagnostics)


def test_get_params_come_from_query():
    program = check(LIST_MINE).program
    assert program is not None
    sources = {p.name: p.source for p in program.endpoints["list_orders"].params}
    assert sources == {"user": "user", "limit": "query", "offset": "query"}


def test_non_api_programs_have_no_endpoints():
    src = 'raw = load("orders.csv", schema=Orders)\nwrite(raw, "out.parquet")\n'
    result = check(src, TABULAR)
    assert result.ok
    assert result.program.endpoints == {}


# ---- non-API profiles are unchanged ---------------------------------------------------


def test_endpoint_is_not_a_decorator_without_api():
    assert "L110" in codes(CANCEL, TABULAR)


def test_api_names_are_not_ambient_without_api():
    src = """
    t = fetch(Orders)
    write(t, "out.parquet")
    """
    assert "L203" in codes(src, TABULAR)


# ---- L801 auth ---------------------------------------------------------------------


def test_l801_missing_auth():
    src = CANCEL.replace(", auth=Customer", "")
    d = diag(src, "L801")
    assert "auth=" in (d.hint or "")
    assert errors(src) == ["L801"]


def test_l801_unknown_role():
    src = CANCEL.replace("auth=Customer", "auth=Customr")
    d = diag(src, "L801")
    assert "Customer" in (d.hint or "")
    assert errors(src) == ["L801"]


def test_l801_user_missing_for_non_public():
    src = CANCEL.replace(", user: CurrentUser", "")
    d = diag(src, "L801")
    assert "user: CurrentUser" in (d.hint or "")
    assert errors(src) == ["L801"]


def test_l801_user_present_for_public():
    src = """
    @endpoint("GET", "/products/{product_id}", auth=Public)
    def get_product(product_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
        products = fetch(Products)
        found = where(products, col("id") == product_id)
        product = one(found, else_=NotFound("product"))
        return product
    """
    assert errors(src) == ["L801"]
    assert errors(src.replace(", user: CurrentUser", "")) == []


# ---- L802 ownership ------------------------------------------------------------------


def test_l802_owned_rows_returned_without_owned():
    src = CANCEL.replace("mine = owned(orders, by=user)\n    ", "").replace(
        "where(mine,", "where(orders,"
    )
    d = diag(src, "L802")
    assert d.hint == "filter with owned(orders, by=user) before returning or writing"
    assert errors(src) == ["L802"]  # one mistake, one diagnostic (at the update)


def test_l802_on_return():
    src = """
    @endpoint("GET", "/orders/{order_id}", auth=Customer)
    def get_order(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
        orders = fetch(Orders)
        found = where(orders, col("id") == order_id)
        order = one(found, else_=NotFound("order"))
        return order
    """
    assert errors(src) == ["L802"]
    assert errors(src.replace("auth=Customer", "auth=Admin")) == []


def test_admin_list_may_skip_owned():
    src = """
    @endpoint("GET", "/admin/orders", auth=Admin)
    def all_orders(user: CurrentUser) -> Page:
        orders = fetch(Orders)
        result = page(orders, limit=50)
        return result
    """
    assert errors(src) == []


def test_l802_delete_without_owned():
    src = """
    @endpoint("DELETE", "/orders/{order_id}", auth=Customer)
    def remove(order_id: Int(ge=1), user: CurrentUser) -> NoContent:
        orders = fetch(Orders)
        found = where(orders, col("id") == order_id)
        gone = delete(found)
        return gone
    """
    assert errors(src) == ["L802"]
    fixed = src.replace(
        "found = where(orders,", "mine = owned(orders, by=user)\n        found = where(mine,"
    )
    assert errors(fixed) == []


def test_l802_insert_owned_table_without_owner():
    src = """
    @endpoint("POST", "/orders", auth=Customer)
    def create(total: Float(ge=0, le=10000), user: CurrentUser) -> Created:
        row = insert(Orders, status="pending", total=total)
        return row
    """
    d = diag(src, "L802")
    assert "owner=user" in (d.hint or "")
    assert errors(src) == ["L802"]
    assert errors(src.replace("insert(Orders,", "insert(Orders, owner=user,")) == []


def test_l802_through_a_step():
    src = """
    @step
    def pending(t: Table[Orders]) -> Table:
        p = where(t, col("status") == "pending")
        return p

    @endpoint("GET", "/pending", auth=Customer)
    def list_pending(user: CurrentUser) -> Page:
        orders = fetch(Orders)
        p = pending(orders)
        result = page(p, limit=20)
        return result
    """
    assert errors(src) == ["L802"]


def test_unowned_table_needs_no_owned():
    src = """
    @endpoint("GET", "/products", auth=Public)
    def products(limit: Int(ge=1, le=100) = 20) -> Page:
        all_products = fetch(Products)
        result = page(all_products, limit=limit)
        return result
    """
    assert check(src).diagnostics == ()


# ---- L803 private columns ---------------------------------------------------------------


def test_l803_private_column_returned():
    src = """
    @endpoint("GET", "/me", auth=Customer)
    def me(user: CurrentUser) -> One:
        return user
    """
    d = diag(src, "L803")
    assert "password_hash" in d.message and "token" in d.message
    assert "select" in (d.hint or "")
    assert errors(src) == ["L803"]


def test_select_excluding_private_is_ok():
    src = """
    @endpoint("GET", "/me", auth=Customer)
    def me(user: CurrentUser) -> One:
        public = select(user, cols=["id", "email", "role"])
        return public
    """
    assert check(src).diagnostics == ()


def test_l803_private_value_copied_under_new_name():
    src = """
    @endpoint("GET", "/me", auth=Customer)
    def me(user: CurrentUser) -> One:
        leaked = derive(user, secret=fn.upper(col("token")))
        public = select(leaked, cols=["id", "secret"])
        return public
    """
    assert errors(src) == ["L803"]


# ---- L804 paths ---------------------------------------------------------------------


def test_l804_path_segment_without_param():
    src = CANCEL.replace("def cancel(order_id: Int(ge=1),", "def cancel(oid: Int(ge=1),").replace(
        "== order_id", "== oid"
    )
    d = diag(src, "L804")
    assert "order_id" in d.message
    assert errors(src) == ["L804"]


def test_l804_duplicate_route():
    second = (
        CANCEL.replace("def cancel(", "def cancel_again(")
        .replace("{order_id}", "{id2}")
        .replace("(order_id:", "(id2:")
        .replace("== order_id", "== id2")
    )
    assert errors(CANCEL + "\n" + second) == ["L804"]


def test_l804_malformed_path():
    src = CANCEL.replace('"/orders/{order_id}/cancel"', '"orders/{order_id}"')
    assert errors(src) == ["L804"]


# ---- L811 bounds ---------------------------------------------------------------------


def test_l811_unbounded_param_written():
    src = """
    @endpoint("PATCH", "/products/{product_id}", auth=Admin)
    def restock(product_id: Int(ge=1), qty: Int, user: CurrentUser) -> One | NotFound:
        products = fetch(Products)
        found = where(products, col("id") == product_id)
        product = one(found, else_=NotFound("product"))
        done = update(product, stock=col("stock") + qty)
        return done
    """
    d = diag(src, "L811")
    assert d.hint == "give it bounds, e.g. qty: Int(ge=1, le=100)"
    assert errors(src) == ["L811"]
    assert errors(src.replace("qty: Int,", "qty: Int(ge=1, le=100),")) == []


def test_l811_unbounded_str_insert_via_constant():
    src = """
    @endpoint("POST", "/products", auth=Admin)
    def add(name: Str, user: CurrentUser) -> Created:
        label = fn.upper(name)
        row = insert(Products, name=label, price=1.0, stock=0)
        return row
    """
    d = diag(src, "L811")
    assert "max_len" in (d.hint or "")
    assert errors(src.replace("name: Str,", "name: Str(max_len=80),")) == []


def test_unbounded_param_only_filtered_is_ok():
    src = """
    @endpoint("GET", "/products/{product_id}", auth=Public)
    def get_product(product_id: Int) -> One | NotFound:
        products = fetch(Products)
        found = where(products, col("id") == product_id)
        product = one(found, else_=NotFound("product"))
        return product
    """
    assert errors(src) == []


# ---- L821 / L822 errors -----------------------------------------------------------------


def test_l821_undeclared_error():
    src = CANCEL.replace("-> One | NotFound | Conflict:", "-> One | NotFound:")
    d = diag(src, "L821")
    assert "Conflict" in d.message
    assert "| Conflict" in (d.hint or "")
    assert errors(src) == ["L821"]


def test_l822_declared_error_never_raised_is_a_warning():
    src = CANCEL.replace(
        "-> One | NotFound | Conflict:", "-> One | NotFound | Conflict | Forbidden:"
    )
    result = check(src)
    assert [d.code for d in result.diagnostics] == ["L822"]
    assert result.diagnostics[0].severity is Severity.WARNING
    assert result.ok


# ---- L831 / L832 / L833 response shapes -------------------------------------------------


def test_page_list_ok():
    assert check(LIST_MINE).diagnostics == ()


def test_l831_list_without_page():
    src = """
    @endpoint("GET", "/orders", auth=Customer)
    def list_orders(user: CurrentUser) -> Page:
        orders = fetch(Orders)
        mine = owned(orders, by=user)
        return mine
    """
    d = diag(src, "L831")
    assert d.hint == "return page(mine, limit=20)"
    assert errors(src) == ["L831"]


def test_l831_page_limit_too_large():
    src = LIST_MINE.replace("limit=limit", "limit=500")
    assert "L831" in errors(src)
    src2 = LIST_MINE.replace("le=100) = 20", "le=1000) = 20")
    assert errors(src2) == ["L831"]
    src3 = LIST_MINE.replace("Int(ge=1, le=100) = 20", "Int = 20")
    assert errors(src3) == ["L831"]


def test_l832_one_needs_one_row():
    src = """
    @endpoint("GET", "/orders", auth=Customer)
    def list_orders(user: CurrentUser) -> One:
        orders = fetch(Orders)
        mine = owned(orders, by=user)
        return mine
    """
    d = diag(src, "L832")
    assert "one(" in (d.hint or "")
    assert errors(src) == ["L832"]


def test_l832_page_with_one_row():
    src = CANCEL.replace("-> One |", "-> Page |")
    assert errors(src) == ["L832"]


def test_l832_nocontent_returns_write_result():
    src = """
    @endpoint("DELETE", "/orders/{order_id}", auth=Customer)
    def remove(order_id: Int(ge=1), user: CurrentUser) -> NoContent:
        orders = fetch(Orders)
        mine = owned(orders, by=user)
        found = where(mine, col("id") == order_id)
        return found
    """
    assert errors(src) == ["L832"]


def test_l833_created_for_get():
    src = """
    @endpoint("GET", "/products/{product_id}", auth=Public)
    def get_product(product_id: Int(ge=1)) -> Created | NotFound:
        products = fetch(Products)
        found = where(products, col("id") == product_id)
        product = one(found, else_=NotFound("product"))
        return product
    """
    assert errors(src) == ["L833"]


def test_l833_post_insert_declared_one_warns():
    src = """
    @endpoint("POST", "/orders", auth=Customer)
    def create(total: Float(ge=0, le=10000), user: CurrentUser) -> One:
        row = insert(Orders, owner=user, status="pending", total=total)
        return row
    """
    result = check(src)
    assert [(d.code, d.severity) for d in result.diagnostics] == [("L833", Severity.WARNING)]


# ---- L840 / L841 -----------------------------------------------------------------------


def test_l840_top_level_statements():
    src = CANCEL + "\norders = fetch(Orders)\n"
    assert errors(src) == ["L840"]


def test_top_level_constants_are_allowed():
    src = "MAX_PAGE = 50\n" + LIST_MINE.replace("le=100) = 20", "le=100) = MAX_PAGE")
    # a default must be a literal: constants are not
    assert errors(src) == ["L601"]
    src_ok = 'PENDING = "pending"\n' + CANCEL.replace('== "pending"', "== PENDING")
    assert errors(src_ok) == []


def test_l841_fn_outside_subset():
    src = """
    @endpoint("GET", "/orders", auth=Customer)
    def list_orders(user: CurrentUser) -> Page:
        orders = fetch(Orders)
        mine = owned(orders, by=user)
        scored = derive(mine, t=fn.round(col("total"), digits=1))
        result = page(scored, limit=20)
        return result
    """
    d = diag(src, "L841")
    assert "fn.round" in d.message
    assert errors(src) == ["L841"]


def test_l841_date_diff_in_endpoint():
    src = """
    @endpoint("GET", "/orders", auth=Customer)
    def list_orders(user: CurrentUser, since: Date) -> Page:
        orders = fetch(Orders)
        mine = owned(orders, by=user)
        aged = derive(mine, age=fn.date_diff(since, since, unit="days"))
        result = page(aged, limit=20)
        return result
    """
    assert errors(src) == ["L841"]


def test_l841_load_in_endpoint():
    src = """
    @endpoint("GET", "/orders", auth=Customer)
    def list_orders(user: CurrentUser) -> Page:
        orders = load("orders.csv", schema=Orders)
        result = page(orders, limit=20)
        return result
    """
    assert errors(src) == ["L841"]


def test_l841_in_step_called_from_endpoint():
    src = """
    @step
    def rounded(t: Table[Products]) -> Table:
        r = derive(t, price=fn.round(col("price"), digits=0))
        return r

    @endpoint("GET", "/products", auth=Public)
    def products() -> Page:
        all_products = fetch(Products)
        r = rounded(all_products)
        result = page(r, limit=20)
        return result
    """
    assert errors(src) == ["L841"]


def test_api_ops_not_allowed_in_steps():
    src = """
    @step
    def everything(t: Table[Products]) -> Table:
        r = fetch(Products)
        return r
    """
    assert "L503" in errors(src)


# ---- typing inside endpoints -----------------------------------------------------------


def test_require_condition_must_be_bool():
    src = CANCEL.replace('col("status") == "pending"', 'col("status")')
    assert errors(src) == ["L403"]


def test_fetch_unknown_table():
    src = CANCEL.replace("fetch(Orders)", "fetch(Ordrs)")
    assert errors(src) == ["L203"]


def test_owned_needs_a_table():
    src = CANCEL.replace("owned(orders,", "owned(Orders,")
    d = diag(src, "L419")
    assert "fetch" in (d.hint or "")
    assert errors(src) == ["L419"]


def test_owned_on_table_without_owner():
    src = """
    @endpoint("GET", "/products", auth=Customer)
    def products(user: CurrentUser) -> Page:
        all_products = fetch(Products)
        mine = owned(all_products, by=user)
        result = page(mine, limit=20)
        return result
    """
    assert errors(src) == ["L404"]


def test_insert_unknown_column_and_type():
    base = """
    @endpoint("POST", "/products", auth=Admin)
    def add(name: Str(max_len=80), user: CurrentUser) -> Created:
        row = insert(Products, VALUE)
        return row
    """
    assert errors(base.replace("VALUE", "nam=name")) == ["L401"]
    assert errors(base.replace("VALUE", "price=name")) == ["L402"]
    assert errors(base.replace("VALUE", "name=name")) == []


def test_update_type_mismatch():
    src = CANCEL.replace('status="cancelled"', "status=1")
    assert errors(src) == ["L402"]


def test_else_must_be_an_error():
    src = CANCEL.replace('else_=NotFound("order")', 'else_="order"')
    assert errors(src) == ["L404"]


def test_nesting_rules_apply():
    src = CANCEL.replace(
        'found = where(mine, col("id") == order_id)\n    order = one(found,',
        'order = one(where(mine, col("id") == order_id),',
    )
    assert "L301" in errors(src)


def test_params_named_like_ops_are_allowed():
    assert errors(LIST_MINE) == []  # `limit` is a parameter


def test_step_defaults_still_rejected():
    src = """
    @step
    def f(t: Table[Products], n: Int = 1) -> Table:
        r = limit(t, n=5)
        return r
    """
    assert "L601" in errors(src)


@pytest.mark.parametrize(
    "ann",
    ["Table[Orders]", "Int(gt=1)", "Str(max_len=1.5)", "OneOf(1, 2)", "Int(ge=5, le=1)", "Bool()"],
)
def test_bad_param_annotations(ann):
    src = CANCEL.replace("order_id: Int(ge=1)", f"order_id: {ann}")
    assert errors(src) == ["L601"]


@pytest.mark.parametrize("ret", ["Int", "One | Gone", "NotFound | One", "One | Page"])
def test_bad_return_annotations(ret):
    src = CANCEL.replace("One | NotFound | Conflict", ret)
    assert errors(src)[0] == "L601"


def test_bad_method():
    assert errors(CANCEL.replace('"POST"', '"FETCH"')) == ["L601"]


def test_user_must_be_named_user():
    src = CANCEL.replace("user: CurrentUser", "me: CurrentUser")
    assert errors(src)[0] == "L601"


def test_check_source_never_raises_on_odd_endpoints():
    for src in [
        "@endpoint()\ndef f() -> One:\n    return x\n",
        '@endpoint("GET", "/", auth=Public)\ndef f() -> One:\n    return f\n',
        '@endpoint("GET", "/", auth=Public)\ndef f() -> NoContent:\n    x = delete(y)\n'
        "    return x\n",
        '@endpoint("GET", "/", auth=Public)\ndef f(user: CurrentUser) -> One:\n'
        "    a = one(user, else_=NotFound())\n    return a\n",
        '@endpoint("GET", "/", auth="Admin")\ndef f() -> One:\n    return f\n',
    ]:
        check(src)  # must not raise


def test_step_on_one_row_keeps_one_row_and_private_columns():
    src = """
    @step
    def tagged(t: Table[Users]) -> Table:
        r = derive(t, tag=fn.upper(col("role")))
        return r

    @endpoint("GET", "/me", auth=Customer)
    def me(user: CurrentUser) -> One:
        r = tagged(user)
        return r
    """
    assert errors(src) == ["L803"]
    fixed = src.replace(
        "r = tagged(user)", 's = tagged(user)\n        r = select(s, cols=["id", "tag"])'
    )
    assert check(fixed).diagnostics == ()


def test_existence_check_binding_is_not_unused():
    src = """
    @endpoint("POST", "/products/{product_id}/restock", auth=Admin)
    def restock(product_id: Int(ge=1, le=1000000), user: CurrentUser) -> One | NotFound:
        products = fetch(Products)
        found = where(products, col("id") == product_id)
        product = one(found, else_=NotFound("product"))
        done = update(product, stock=100)
        return done
    """
    assert check(src).diagnostics == ()


def test_duplicate_endpoint_name():
    second = LIST_MINE.replace('"/orders"', '"/orders2"')
    assert errors(LIST_MINE + second) == ["L201"]


def test_cancel_checks_against_the_benchmark_profile():
    from bench.api_datasets import api_profile

    assert check(CANCEL, api_profile()).diagnostics == ()


def test_l811_int_needs_only_a_lower_bound():
    # ids and counts written to the DB need ge=; an arbitrary upper bound is not required
    src = """
    @endpoint("POST", "/reviews", auth=Customer)
    def review(product_id: Int(ge=1), rating: Int(ge=1, le=5), user: CurrentUser) -> Created:
        row = insert(Reviews, owner=user, product_id=product_id, rating=rating, body="ok")
        return row
    """
    assert errors(src) == []
    assert errors(src.replace("product_id: Int(ge=1)", "product_id: Int")) == ["L811"]


def test_l811_float_still_needs_both_bounds():
    src = """
    @endpoint("PATCH", "/products/{product_id}", auth=Admin)
    def reprice(product_id: Int(ge=1), price: Float(ge=0.0), user: CurrentUser) -> One | NotFound:
        products = fetch(Products)
        found = where(products, col("id") == product_id)
        product = one(found, else_=NotFound("product"))
        done = update(product, price=price)
        return done
    """
    assert errors(src) == ["L811"]
