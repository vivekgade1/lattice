"""The customer's legacy acceptance scenarios: visible to you, and part of the gate (see CONTEXT.md)."""

from __future__ import annotations

from lattice.spec import Rpc, Scenario

VISIBLE_SCENARIOS = (
    Scenario(
        "legacy_zero_qty_refused",
        steps=(
            Rpc(
                "toy.v1.Shelf",
                "AddItem",
                None,
                {"name": "washer", "qty": 0},
                expect_code="INVALID_ARGUMENT",
                expect_db={"Item": {"count": 0}},
            ),
        ),
    ),
)
