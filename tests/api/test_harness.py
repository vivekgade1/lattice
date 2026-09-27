"""The benchmark harness drives the real runtime (bench.api_runner.run_cases_lattice)."""

from __future__ import annotations

from pathlib import Path

from bench.api_datasets import TOKENS, api_profile, build_db
from bench.api_model import ApiTask, HttpCase
from bench.api_runner import classify, run_cases_lattice
from tests.api.conftest import source

PROGRAM = source(
    """
    @endpoint("POST", "/orders/{order_id}/cancel", auth=Customer)
    def cancel(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound | Conflict:
        orders = fetch(Orders)
        mine = owned(orders, by=user)
        found = where(mine, col("id") == order_id)
        order = one(found, else_=NotFound("order"))
        ok = require(order, col("status") == "pending", else_=Conflict("order is not pending"))
        done = update(ok, status="cancelled")
        shown = select(done, cols=["id", "status"])
        return shown

    @endpoint("GET", "/orders", auth=Customer)
    def list_orders(user: CurrentUser, limit: Int(ge=1, le=100) = 20,
                    offset: Int(ge=0) = 0) -> Page:
        orders = fetch(Orders)
        mine = owned(orders, by=user)
        ordered = sort(mine, by=["id"])
        result = page(ordered, limit=limit, offset=offset)
        return result
    """
)

STATUS_1 = "SELECT status FROM orders WHERE id = 1"  # alice's pending order
TASK = ApiTask(
    "smoke",
    "ownership",
    "cancel an order; list my orders",
    (
        HttpCase(
            name="cancel own pending",
            kind="happy",
            family="ownership",
            method="POST",
            path="/orders/1/cancel",
            token=TOKENS["alice"],
            expect_status=200,
            expect_json={"id": 1, "status": "cancelled"},
            max_queries=3,
            after_sql=STATUS_1,
            after_expect=[["cancelled"]],
        ),
        HttpCase(
            name="list mine",
            kind="happy",
            family="efficiency",
            method="GET",
            path="/orders",
            token=TOKENS["alice"],
            query={"limit": 5},
            expect_status=200,
            expect_json={"limit": 5, "offset": 0},
            max_queries=2,
        ),
        HttpCase(
            name="cancel someone else's",
            kind="trap",
            family="ownership",
            method="POST",
            path="/orders/1/cancel",
            token=TOKENS["bob"],
            expect_status=404,
            after_sql=STATUS_1,
            after_expect=[["pending"]],
        ),
    ),
)


def test_harness_runs_the_runtime_and_counts_queries(tmp_path: Path):
    results = run_cases_lattice(PROGRAM, tmp_path, TASK, db_builder=build_db, profile=api_profile())
    assert results.started, results.start_error
    assert [(c.name, c.passed, c.status, c.queries) for c in results.cases] == [
        ("cancel own pending", True, 200, 3),
        ("list mine", True, 200, 2),
        ("cancel someone else's", True, 404, 2),
    ]
    assert classify(results) == "correct"


def test_harness_reports_a_rejected_program(tmp_path: Path):
    bad = PROGRAM.replace("mine = owned(orders, by=user)", "mine = where(orders, col('id') > 0)")
    results = run_cases_lattice(bad, tmp_path, TASK, db_builder=build_db, profile=api_profile())
    assert not results.started and "L802" in results.start_error
