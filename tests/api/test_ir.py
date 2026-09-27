"""Lowering checked endpoints to EndpointPlans (lattice.api.ir)."""

from __future__ import annotations

from lattice.api.ir import (
    CheckStep,
    EndpointPlan,
    ErrorSpec,
    PageArgs,
    ReadStep,
    WriteStep,
    lower_api,
)
from lattice.check.api_info import ParamSpec
from lattice.typesys import Scalar
from tests.api.conftest import checked

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

LIST = """
@endpoint("GET", "/orders", auth=Customer)
def list_orders(user: CurrentUser, limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0) -> Page:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    ordered = sort(mine, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    shown = select(result, cols=["id", "status"])
    return shown
"""

INSERT = """
@endpoint("POST", "/addresses", auth=Customer)
def add_address(user: CurrentUser, line1: Str(min_len=1, max_len=80),
                city: Str(min_len=1, max_len=40), postcode: Str(min_len=5, max_len=5)) -> Created:
    row = insert(Addresses, owner=user, line1=line1, city=city, zip=postcode)
    return row
"""


def one_plan(src: str) -> EndpointPlan:
    plans = lower_api(checked(src))
    assert len(plans) == 1
    return plans[0]


def kinds(plan: EndpointPlan) -> list[tuple[str, str]]:
    return [(type(s).__name__, s.id) for s in plan.steps]


def test_cancel_plan_shape():
    plan = one_plan(CANCEL)
    assert (plan.name, plan.method, plan.path, plan.auth) == (
        "cancel",
        "POST",
        "/orders/{order_id}/cancel",
        "Customer",
    )
    assert plan.params == (
        ParamSpec("order_id", "path", Scalar.INT, ge=1),
        ParamSpec("user", "user", None),
    )
    assert plan.success == "One" and plan.errors == ("NotFound", "Conflict")
    assert kinds(plan) == [
        ("ReadStep", "found"),
        ("CheckStep", "order"),
        ("CheckStep", "ok"),
        ("WriteStep", "done"),
    ]
    read, one, req, write = plan.steps
    assert isinstance(read, ReadStep) and isinstance(write, WriteStep)
    assert isinstance(one, CheckStep) and isinstance(req, CheckStep)
    assert read.sql.params == ("order_id",) and read.sql.user
    assert read.flags == ("__ok_ok",)
    assert one.kind == "one" and one.input == "found" and one.cond is None
    assert one.error == ErrorSpec("NotFound", 404, "order")
    assert req.kind == "require" and req.input == "found"
    assert (req.flag_step, req.flag) == ("found", "__ok_ok")
    assert req.cond is not None and '"status" =' in req.cond
    assert req.error == ErrorSpec("Conflict", 409, "order is not pending")
    assert req.error.code == "conflict" and one.error.code == "not_found"
    assert (write.kind, write.table, write.input, write.key) == (
        "update",
        "orders",
        "found",
        ("id",),
    )
    assert [c for c, _ in write.values] == ["status"]
    assert write.sql.text.lstrip().startswith("WITH") and "UPDATE" in write.sql.text
    assert write.sql.rows == (("__r0", "found", write.schema.names()),)
    assert plan.returns == "done"
    assert plan.schema.names() == ("id", "customer_id", "status", "created_at", "total")


def test_read_pipeline_is_one_select_with_bound_values():
    plan = one_plan(CANCEL)
    read = plan.steps[0]
    assert isinstance(read, ReadStep)
    text = read.sql.text
    assert text.count("SELECT") == text.count("AS (SELECT") + 1  # CTEs + one final SELECT
    assert "pending" not in text and "cancelled" not in text  # literals are bound
    assert ("__l0", "pending") in read.sql.binds


def test_list_plan_page_args_and_schema():
    plan = one_plan(LIST)
    assert kinds(plan) == [("ReadStep", "shown")]
    assert plan.page == PageArgs("limit", "offset")
    assert plan.schema.names() == ("id", "status")
    assert plan.steps[0].sql.params == ("limit", "offset")
    assert "LIMIT :p_limit OFFSET :p_offset" in plan.steps[0].sql.text


def test_page_literal_args_and_default_offset():
    plan = one_plan(
        """
        @endpoint("GET", "/products", auth=Public)
        def products() -> Page:
            p = fetch(Products)
            result = page(p, limit=10)
            return result
        """
    )
    assert plan.page == PageArgs(10, 0)
    assert plan.steps[0].sql.params == ()


def test_insert_plan_sets_owner_from_caller():
    plan = one_plan(INSERT)
    assert kinds(plan) == [("WriteStep", "row")]
    (write,) = plan.steps
    assert isinstance(write, WriteStep)
    assert (write.kind, write.table, write.input) == ("insert", "addresses", None)
    assert [c for c, _ in write.values] == ["line1", "city", "zip"]
    assert '"customer_id"' in write.sql.text and ":__user_id" in write.sql.text
    assert write.sql.params == ("line1", "city", "postcode") and write.sql.user
    assert plan.success == "Created" and plan.returns == "row"


def test_steps_are_inlined_and_constants_bound():
    plan = one_plan(
        """
        BIG = 1000.0

        @step
        def expensive(t: Table[Orders], floor: Float) -> Table[Orders]:
            out = where(t, col("total") >= floor)
            return out

        @endpoint("GET", "/orders/big", auth=Customer)
        def big(user: CurrentUser, limit: Int(ge=1, le=50) = 10) -> Page:
            cap = BIG * 3
            orders = fetch(Orders)
            mine = owned(orders, by=user)
            costly = expensive(mine, floor=BIG)
            capped = where(costly, col("total") < cap)
            result = page(capped, limit=limit)
            return result
        """
    )
    assert kinds(plan) == [("ReadStep", "result")]
    sql = plan.steps[0].sql
    assert "1000" not in sql.text
    assert sorted(v for _, v in sql.binds) == [0, 3, 1000.0, 1000.0]  # offset 0, 3, BIG twice


def test_rows_read_after_a_check_are_bound_not_reread():
    plan = one_plan(
        """
        @endpoint("GET", "/orders/{order_id}/items", auth=Customer)
        def items(order_id: Int(ge=1), user: CurrentUser) -> Page | NotFound:
            orders = fetch(Orders)
            mine = owned(orders, by=user)
            found = where(mine, col("id") == order_id)
            order = one(found, else_=NotFound("order"))
            ids = select(order, cols=["id"])
            keyed = rename(ids, id="order_id")
            lines = fetch(OrderItems)
            my_lines = owned(lines, by=user)
            joined = join(my_lines, keyed, on=["order_id"])
            result = page(joined, limit=100)
            return result
        """
    )
    assert kinds(plan) == [("ReadStep", "found"), ("CheckStep", "order"), ("ReadStep", "result")]
    second = plan.steps[2].sql
    assert second.rows == (("__r0", "found", plan.steps[0].schema.names()),)
    # the checked order is bound, not re-read; "orders" appears only in the ownership
    # subquery of the owned-through-Orders items
    assert second.text.count('"orders"') == 1 and '"order_items"' in second.text
    assert '"order_id" IN (SELECT "id" FROM "orders"' in second.text


def test_require_on_a_write_result_reads_its_flags():
    plan = one_plan(
        """
        @endpoint("POST", "/orders/{order_id}/ship", auth=Admin)
        def ship(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound | Conflict:
            orders = fetch(Orders)
            found = where(orders, col("id") == order_id)
            order = one(found, else_=NotFound("order"))
            done = update(order, status="shipped")
            ok = require(done, col("total") < 0.0, else_=Conflict("never"))
            return ok
        """
    )
    assert kinds(plan) == [
        ("ReadStep", "found"),
        ("CheckStep", "order"),
        ("WriteStep", "done"),
        ("ReadStep", "ok#cond"),
        ("CheckStep", "ok"),
    ]
    check = plan.steps[4]
    assert isinstance(check, CheckStep)
    assert (check.input, check.flag_step) == ("done", "ok#cond")
    assert plan.returns == "done"


def test_delete_plan_and_multiple_endpoints_in_order():
    plans = lower_api(
        checked(
            INSERT
            + """
@endpoint("DELETE", "/addresses/{address_id}", auth=Customer)
def remove(address_id: Int(ge=1), user: CurrentUser) -> NoContent | NotFound:
    rows = fetch(Addresses)
    mine = owned(rows, by=user)
    found = where(mine, col("id") == address_id)
    target = one(found, else_=NotFound("address"))
    gone = delete(target)
    return gone
"""
        )
    )
    assert [p.name for p in plans] == ["add_address", "remove"]
    remove = plans[1]
    assert kinds(remove) == [("ReadStep", "found"), ("CheckStep", "target"), ("WriteStep", "gone")]
    write = remove.steps[2]
    assert isinstance(write, WriteStep)
    assert write.kind == "delete" and write.key == ("id",)
    assert write.sql.text.lstrip().startswith("WITH") and "DELETE FROM" in write.sql.text


def test_error_without_message_gets_a_default_detail():
    plan = one_plan(
        """
        @endpoint("GET", "/products/{product_id}", auth=Public)
        def product(product_id: Int(ge=1)) -> One | NotFound:
            p = fetch(Products)
            found = where(p, col("id") == product_id)
            row = one(found, else_=NotFound())
            return row
        """
    )
    check = plan.steps[1]
    assert isinstance(check, CheckStep)
    assert check.error == ErrorSpec("NotFound", 404, "not found")
