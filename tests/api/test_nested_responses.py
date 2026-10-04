"""Nested response models (W1-B): the lowered plans read each response in ONE statement
(the child rows a correlated sub-select of the returned rows' read), and Float values of
child rows survive exactly. The fixture is tests/api/nested.py; the generated FastAPI app
is compared with the runtime in test_codegen_differential."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from lattice.api.ir import EndpointPlan, ReadStep, WriteStep, lower_api
from lattice.api.runtime import build_app
from lattice.check import check_source
from lattice.typesys import Scalar
from tests.api import nested as F


@pytest.fixture(scope="module")
def plans() -> dict[str, EndpointPlan]:
    check = check_source(F.PROGRAM, F.PROFILE)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    return {p.name: p for p in lower_api(check.program)}


@pytest.mark.parametrize("name", ["read_order", "list_orders", "recent_orders", "read_product"])
def test_a_read_is_one_statement(plans: dict[str, EndpointPlan], name: str) -> None:
    plan = plans[name]
    reads = [s for s in plan.steps if isinstance(s, ReadStep)]
    assert len(reads) == 1 and reads[0].id == plan.returns
    field = plan.nested[0].field
    assert reads[0].flags == (field,)
    assert "json_group_array" in reads[0].sql.text


def test_after_a_write_the_nested_lists_are_one_more_read(plans: dict[str, EndpointPlan]) -> None:
    plan = plans["rename_order"]
    kinds = [type(s).__name__ for s in plan.steps]
    assert kinds == ["ReadStep", "CheckStep", "WriteStep", "ReadStep"]
    assert isinstance(plan.steps[2], WriteStep) and plan.steps[3].id == plan.returns


def test_response_fields_keep_the_model_order(plans: dict[str, EndpointPlan]) -> None:
    plan = plans["read_order"]
    assert [n for n, _ in plan.response_fields] == ["id", "status", "items", "total_amount"]
    assert plan.schema.names() == ("id", "status", "total_amount")
    nested = plan.nested[0]
    assert (nested.field, nested.index) == ("items", 2)
    assert nested.schema.columns == (
        ("id", Scalar.INT), ("product_id", Scalar.INT), ("quantity", Scalar.INT),
        ("price_at_purchase", Scalar.FLOAT), ("kind", Scalar.STR),
    )  # fmt: skip


# doubles SQLite's JSON (15 significant digits) and printf would not round-trip
FLOATS = [3.0833333333333335, 1 / 3, 1e300, 1.7976931348623157e308, 5e-324,
          2.5067064911303606e188, -2.2391746079108119e-125, 0.1 + 0.2]  # fmt: skip


@pytest.fixture
def client(tmp_path: Path, plans: dict[str, EndpointPlan]) -> Iterator[TestClient]:
    db = tmp_path / "app.db"
    F.build_db(db)
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO order_items VALUES (?, 12, 1, 1, ?, 'STANDARD', 0)",
        [(200 + i, x) for i, x in enumerate(FLOATS)],
    )
    conn.commit()
    conn.close()
    with TestClient(build_app(tuple(plans.values()), db)) as c:
        yield c


def test_child_floats_are_exact(client: TestClient) -> None:
    r = client.get("/orders/12", headers={"Authorization": f"Bearer {F.TOKENS['ann']}"})
    assert r.status_code == 200
    assert [i["price_at_purchase"] for i in r.json()["items"]] == FLOATS
