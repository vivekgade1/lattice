"""The spec of the toy shelf service (.NET real-agent build): 7 scenarios of HTTP steps and one invariant."""

from __future__ import annotations

from lattice.profile import ApiProfile, Profile
from lattice.spec import Invariant, Scenario, Spec, Step
from lattice.typesys import Scalar, Schema

S = Scalar

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


def add(name: str, qty: int = 1, **kw) -> Step:
    return Step("POST", "/items", json={"name": name, "qty": qty}, **kw)


SCENARIOS = (
    Scenario(
        "add_and_get",
        steps=(
            add(
                "widget",
                3,
                expect_status=201,
                expect_body={"id": 1, "name": "widget", "qty": 3},
                expect_db={"Item": {"count": 1}},
            ),
            Step("GET", "/items/1", expect_body={"id": 1, "name": "widget", "qty": 3}),
        ),
    ),
    Scenario(
        "add_empty_name",
        steps=(add("", 1, expect_status=422, expect_db={"Item": {"count": 0}}),),
    ),
    Scenario(
        "add_negative_qty",
        steps=(add("bolt", -1, expect_status=422, expect_db={"Item": {"count": 0}}),),
    ),
    Scenario(
        "add_duplicate_name",
        steps=(
            add("nut", 2, expect_status=201, expect_db={"Item": {"count": 1}}),
            add("nut", 5, expect_status=409, expect_db={"Item": {"count": 1}}),
        ),
    ),
    Scenario(
        "get_missing",
        steps=(Step("GET", "/items/99", expect_status=404),),
    ),
    Scenario(
        "list_oldest_first",
        steps=(
            add("a", 1, expect_status=201),
            add("b", 2, expect_status=201),
            add("c", 3, expect_status=201),
            Step(
                "GET",
                "/items",
                expect_body={
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
            add("gear", 4, expect_status=201, expect_db={"Item": {"count": 1}}),
            Step("DELETE", "/items/1", expect_db={"Item": {"count": 0}}),
            Step("DELETE", "/items/1", expect_status=404),
            Step("GET", "/items/1", expect_status=404),
        ),
    ),
)

SPEC = Spec(profile=PROFILE, invariants=(NON_NEGATIVE,), scenarios=SCENARIOS)
