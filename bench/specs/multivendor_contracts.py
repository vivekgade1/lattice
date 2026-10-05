"""Contracts for the multi-vendor app's program-less routes (Phase 2, W1-B test bed).

`bench/specs/multivendor.py`'s `SPEC` has programs for 13 of the app's 17 routes. The
other four -- the login form, placing an order, moving an order's status and the
flash-sale purchase -- have no program (see its `UNCHECKED`). Here each gets a
`lattice.spec.Contract`: the route's auth, the table it writes, its ownership scope,
its parameters (with the columns they are stored in, for the table bound and one_of
items) and its error answers. `SPEC` is the hand spec plus these contracts; layer R
derives the routes with programs from the programs themselves.

Read from upstream: `routers/{auth,order}_router.py`, `core/permissions.py`
(`PLACE_ORDER`: buyers; `UPDATE_ORDER_STATUS`: vendors and admins),
`schemas/order.py` (`quantity: int = Field(gt=0)`, `status: OrderStatus`),
`services/order_service.py` (`VALID_TRANSITIONS`; any vendor may move any order).
Seed ids used by the error examples: order 1 is delivered, product 7 (tape measure) has
no stock.
"""

from __future__ import annotations

from dataclasses import replace

from bench.specs.multivendor import ORDER_STATES
from bench.specs.multivendor import SPEC as HAND_SPEC
from lattice.spec import Contract, ContractError, ContractParam, Spec
from lattice.typesys import Scalar

__all__ = ["CONTRACTS", "SPEC"]

ORDER_LINE = (
    ContractParam("items.0.product_id", "body", Scalar.INT, ref="Product"),
    ContractParam("items.0.quantity", "body", Scalar.INT, gt=0, column="OrderItem.quantity"),
)
INSUFFICIENT_STOCK = ContractError(
    409, when="other", example={"json": {"items": [{"product_id": 7, "quantity": 1}]}}
)

CONTRACTS = (
    Contract(
        "POST",
        "/auth/login",
        auth="Public",
        table="User",
        kind="action",
        params=(
            ContractParam("username", "form", Scalar.STR),
            ContractParam("password", "form", Scalar.STR),
        ),
        errors=(
            ContractError(
                401,
                "Incorrect email or password",
                when="other",
                example={"form": {"username": "nobody@example.com", "password": "wrong"}},
            ),
        ),
    ),
    Contract(
        "POST",
        "/orders",
        auth="Roles(Buyer)",
        table="Order",
        kind="create",
        owner_scope="owner",
        params=ORDER_LINE,
        errors=(INSUFFICIENT_STOCK, ContractError(422, when="invalid")),
        response="OrderRead",
    ),
    Contract(
        "PATCH",
        "/orders/{order_id}/status",
        auth="Roles(Vendor, Admin)",
        table="Order",
        kind="update",
        # any vendor moves any buyer's order (UPDATE_ORDER_STATUS has no ownership)
        owner_scope="any",
        params=(
            ContractParam("order_id", "path", Scalar.INT, ref="Order"),
            ContractParam("status", "body", Scalar.STR, one_of=ORDER_STATES, column="Order.status"),
        ),
        errors=(
            ContractError(404, "Order not found", when="not_found"),
            ContractError(
                400,
                "Cannot transition order from delivered to pending",
                when="other",
                example={"path": "/orders/1/status", "json": {"status": "pending"}},
            ),
            ContractError(422, when="invalid"),
        ),
        response="OrderRead",
    ),
    Contract(
        "POST",
        "/orders/flash-sale-purchase",
        auth="Roles(Buyer)",
        table="Order",
        kind="create",
        owner_scope="owner",
        params=(
            ContractParam("product_id", "body", Scalar.INT, ref="Product"),
            ContractParam("quantity", "body", Scalar.INT, gt=0, column="OrderItem.quantity"),
        ),
        errors=(
            ContractError(409, when="other", example={"json": {"product_id": 7, "quantity": 1}}),
            ContractError(422, when="invalid"),
        ),
        response="OrderRead",
    ),
)

SPEC: Spec = replace(HAND_SPEC, contracts=CONTRACTS)
