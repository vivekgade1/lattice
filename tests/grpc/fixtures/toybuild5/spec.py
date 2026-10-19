"""The spec of the toy shelf service (Spike I, Arm L): 7 scenarios of ``Rpc`` steps and one invariant."""

from __future__ import annotations

from harness import PROTOS

from lattice.profile import ApiProfile, Profile
from lattice.spec import Invariant, Rpc, Scenario, Spec
from lattice.typesys import Scalar, Schema

S = Scalar
SHELF = "toy.v1.Shelf"

ITEM = Schema.from_pairs(
    [("id", S.INT), ("name", S.STR), ("qty", S.INT)],
    keys=[("id",), ("name",)],
)
PROFILE = Profile(
    schemas={},
    api=ApiProfile(
        tables={"Item": ITEM},
        table_names={"Item": "items"},
        roles=frozenset({"Public"}),
        error_body="fastapi",
    ),
)
NON_NEGATIVE = Invariant("qty_non_negative", "A stored quantity is never negative.", "Item.qty >= 0")


def add(name: str, qty: int = 1, **kw) -> Rpc:
    return Rpc(SHELF, "AddItem", None, {"name": name, "qty": qty}, **kw)


SCENARIOS = (
    Scenario(
        "add_and_get",
        steps=(
            add(
                "widget",
                3,
                expect_message={"id": 1, "name": "widget", "qty": 3},
                expect_db={"Item": {"count": 1}},
            ),
            Rpc(SHELF, "GetItem", None, {"id": 1}, expect_message={"id": 1, "name": "widget", "qty": 3}),
        ),
    ),
    Scenario(
        "add_empty_name",
        steps=(add("", 1, expect_code="INVALID_ARGUMENT", expect_db={"Item": {"count": 0}}),),
    ),
    Scenario(
        "add_negative_qty",
        steps=(add("bolt", -1, expect_code="INVALID_ARGUMENT", expect_db={"Item": {"count": 0}}),),
    ),
    Scenario(
        "add_duplicate_name",
        steps=(
            add("nut", 2, expect_db={"Item": {"count": 1}}),
            add("nut", 5, expect_code="ALREADY_EXISTS", expect_db={"Item": {"count": 1}}),
        ),
    ),
    Scenario(
        "get_missing",
        steps=(Rpc(SHELF, "GetItem", None, {"id": 99}, expect_code="NOT_FOUND"),),
    ),
    Scenario(
        "list_oldest_first",
        steps=(
            add("a", 1),
            add("b", 2),
            add("c", 3),
            Rpc(
                SHELF,
                "ListItems",
                None,
                {},
                expect_message={
                    "items": [
                        {"id": 1, "name": "a", "qty": 1},
                        {"id": 2, "name": "b", "qty": 2},
                        {"id": 3, "name": "c", "qty": 3},
                    ]
                },
            ),
        ),
    ),
    Scenario(
        "remove_then_again",
        steps=(
            add("gear", 4, expect_db={"Item": {"count": 1}}),
            Rpc(SHELF, "RemoveItem", None, {"id": 1}, expect_db={"Item": {"count": 0}}),
            Rpc(SHELF, "RemoveItem", None, {"id": 1}, expect_code="NOT_FOUND"),
            Rpc(SHELF, "GetItem", None, {"id": 1}, expect_code="NOT_FOUND"),
        ),
    ),
)

SPEC = Spec(profile=PROFILE, invariants=(NON_NEGATIVE,), scenarios=SCENARIOS, protos=PROTOS)
