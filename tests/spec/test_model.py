"""The spec model (normalisation, immutability) and `validate` (one test per code)."""

from __future__ import annotations

import dataclasses
import importlib

import pytest

from lattice.profile import Profile
from lattice.spec import (
    Invariant,
    Machine,
    Scenario,
    Spec,
    SpecProblem,
    Step,
    Unchecked,
    validate,
)
from tests.spec import shop

# the package exports the function `validate`, which shadows the submodule attribute
validate_module = importlib.import_module("lattice.spec.validate")

ORDERS_PROGRAM = """
@endpoint("GET", "/orders/{order_id}", auth=Buyer)
def get_order(order_id: Int):
    return one(mine(Order).filter(id == order_id))
"""
PRODUCTS_PROGRAM = """
@endpoint("GET", "/products", auth=Public)
def list_products():
    return Product.filter(is_active == True)

@endpoint("POST", "/products", auth=Vendor)
def create_product(name: Str):
    return insert(Product, name=name)
"""

MACHINE = Machine(
    table="Order",
    column="status",
    edges={
        "pending": ("confirmed", "cancelled"),
        "confirmed": ("shipped", "cancelled"),
        "shipped": ("delivered",),
    },
    initial="pending",
)
INVARIANTS = (
    Invariant(
        "order_total",
        "An order's total is the sum of its lines.",
        "Order.total_amount == sum(OrderItem.quantity * OrderItem.price_at_purchase, "
        "by='order_id')",
    ),
    Invariant("stock", "Stock is never negative.", "Product.stock_quantity >= 0"),
    Invariant("one_vendor", "A user has at most one vendor.", "count(Vendor, by='user_id') <= 1"),
)
SCENARIO = Scenario(
    name="buyer reads own order",
    given={
        "users": [{"id": 1, "email": "a@x", "role": "buyer", "token": "t", "is_active": True}],
        "Order": [{"id": 1, "user_id": 1, "status": "pending", "total_amount": 0.0}],
    },
    steps=[
        Step("GET", "/orders/1", user="alice", expect_status=200, expect_body={"id": 1}),
        Step(
            "POST",
            "/orders",
            user="alice",
            json={"items": [{"product_id": 2, "quantity": 1}]},
            expect_status=409,
            expect_db={
                "orders": {"count": 1},
                "products": {"where": {"id": 2}, "equals": {"stock_quantity": 30}},
            },
        ),
        Step("GET", "/products", params={"limit": 5}),
    ],
)


def _spec(**changes: object) -> Spec:
    base: dict[str, object] = dict(
        profile=shop.profile(),
        programs={"orders": ORDERS_PROGRAM, "products": PRODUCTS_PROGRAM},
        machines=[MACHINE],
        invariants=INVARIANTS,
        scenarios=[SCENARIO],
        unchecked=[Unchecked("The order page shows a spinner.", concerns=["Order", "orders"])],
        users={"alice": ["a@x", "secret"]},
    )
    base.update(changes)
    return Spec(**base)  # type: ignore[arg-type]


def _codes(spec: Spec) -> list[str]:
    return [p.code for p in validate(spec)]


# ---- the model ---------------------------------------------------------------------------


def test_machine_normalises_edges_and_initial() -> None:
    assert MACHINE.edges == (
        ("pending", "confirmed"),
        ("pending", "cancelled"),
        ("confirmed", "shipped"),
        ("confirmed", "cancelled"),
        ("shipped", "delivered"),
    )
    assert MACHINE.initial == ("pending",)
    same = Machine("Order", "status", [["pending", "confirmed"]], ["pending"])
    assert same.edges == (("pending", "confirmed"),) and same.initial == ("pending",)
    assert hash(same) == hash(Machine("Order", "status", (("pending", "confirmed"),), ("pending",)))


def test_machine_states() -> None:
    assert MACHINE.states() == ("pending", "confirmed", "cancelled", "shipped", "delivered")


def test_records_are_frozen_and_hashable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        MACHINE.table = "Product"  # type: ignore[misc]
    hash(INVARIANTS[0])
    hash(SCENARIO)
    hash(SCENARIO.steps[1])
    unchecked = Unchecked("x", concerns=["Order"])
    assert unchecked.concerns == ("Order",)
    hash(unchecked)


def test_scenario_and_step_normalise() -> None:
    assert isinstance(SCENARIO.steps, tuple) and len(SCENARIO.steps) == 3
    step = SCENARIO.steps[2]
    assert step.method == "GET" and step.user is None and step.expect_status == 200
    assert step.params == {"limit": 5} and step.json is None and step.expect_db is None
    assert SCENARIO.given is not None and SCENARIO.given["users"][0]["id"] == 1
    assert Scenario("empty", None, ()).steps == ()


def test_spec_normalises() -> None:
    spec = _spec()
    assert isinstance(spec.machines, tuple) and isinstance(spec.scenarios, tuple)
    assert isinstance(spec.unchecked, tuple) and isinstance(spec.invariants, tuple)
    assert spec.programs["orders"] == ORDERS_PROGRAM
    assert spec.users["alice"] == ("a@x", "secret")
    with pytest.raises(TypeError):
        spec.programs["x"] = ""  # type: ignore[index]
    empty = Spec(profile=shop.profile())
    assert empty.programs == {} and empty.users == {} and empty.machines == ()


def test_spec_table_lookup() -> None:
    spec = _spec()
    assert spec.table("Order") == ("Order", "orders", shop.ORDER)
    assert spec.table("orders") == ("Order", "orders", shop.ORDER)
    assert spec.table("nope") is None


# ---- validation --------------------------------------------------------------------------


def test_valid_spec_has_no_problems() -> None:
    assert validate(_spec()) == []


def test_every_code_is_documented() -> None:
    doc = validate_module.__doc__ or ""
    for code in validate_module.CODES:
        assert code in doc


def test_problem_shape() -> None:
    (problem,) = validate(_spec(machines=[Machine("Nope", "status", ())]))
    assert isinstance(problem, SpecProblem)
    assert problem.code == "S002" and "Nope" in problem.message and problem.hint
    assert problem.code in problem.render()


def test_s001_profile_without_api() -> None:
    assert _codes(Spec(profile=Profile(schemas={}))) == ["S001"]


def test_s002_unknown_table() -> None:
    assert _codes(_spec(machines=[Machine("Nope", "status", ())])) == ["S002"]
    assert _codes(_spec(invariants=[Invariant("i", "s", "Nope.x > 0")])) == ["S002"]
    bad_given = Scenario("g", {"nope": [{"id": 1}]}, ())
    assert _codes(_spec(scenarios=[bad_given])) == ["S002"]
    bad_db = Scenario("d", None, [Step("GET", "/x", expect_db={"nope": {"count": 0}})])
    assert _codes(_spec(scenarios=[bad_db])) == ["S002"]


def test_s003_unknown_column() -> None:
    assert _codes(_spec(machines=[Machine("Order", "state", ())])) == ["S003"]
    bad_given = Scenario("g", {"orders": [{"id": 1, "nope": 2}]}, ())
    assert _codes(_spec(scenarios=[bad_given])) == ["S003"]
    where = {"orders": {"where": {"nope": 1}, "count": 0}}
    bad_db = Scenario("d", None, [Step("GET", "/x", expect_db=where)])
    assert _codes(_spec(scenarios=[bad_db])) == ["S003"]
    equals = {"orders": {"where": {"id": 1}, "equals": {"nope": 1}}}
    bad_eq = Scenario("e", None, [Step("GET", "/x", expect_db=equals)])
    assert _codes(_spec(scenarios=[bad_eq])) == ["S003"]


def test_s004_machine_column_not_one_of() -> None:
    assert _codes(_spec(machines=[Machine("Product", "category", ())])) == ["S004"]


def test_s005_machine_state_outside_one_of() -> None:
    edge = Machine("Order", "status", [("pending", "lost")])
    assert _codes(_spec(machines=[edge])) == ["S005"]
    initial = Machine("Order", "status", [("pending", "shipped")], initial=("draft",))
    assert _codes(_spec(machines=[initial])) == ["S005"]


def test_s006_two_machines_for_one_column() -> None:
    assert _codes(_spec(machines=[MACHINE, Machine("Order", "status", ())])) == ["S006"]


def test_s007_invariant_does_not_parse() -> None:
    assert _codes(_spec(invariants=[Invariant("i", "s", "Product.price >")])) == ["S007"]


def test_s008_invariant_form() -> None:
    assert _codes(_spec(invariants=[Invariant("i", "s", "max(Product.price) > 0")])) == ["S008"]


def test_s009_invariant_types() -> None:
    assert _codes(_spec(invariants=[Invariant("i", "s", 'Order.status == "lost"')])) == ["S009"]


def test_s010_duplicate_names() -> None:
    twice = [*INVARIANTS, Invariant("stock", "again", "Product.price > 0")]
    assert _codes(_spec(invariants=twice)) == ["S010"]
    assert _codes(_spec(scenarios=[SCENARIO, SCENARIO])) == ["S010"]


def test_s011_unknown_user() -> None:
    scenario = Scenario("u", None, [Step("GET", "/orders/1", user="bob")])
    assert _codes(_spec(scenarios=[scenario])) == ["S011"]


@pytest.mark.parametrize(
    "step",
    [
        Step("FETCH", "/orders"),
        Step("GET", "orders"),
        Step("GET", "/orders", expect_status=99),
        Step("GET", "/orders", expect_db={"orders": {"cnt": 1}}),
        Step("GET", "/orders", expect_db={"orders": {"where": {"id": 1}}}),
        Step("GET", "/orders", expect_db={"orders": {"count": "1"}}),
        Step("GET", "/orders", expect_db={"orders": {"equals": "x"}}),
    ],
)
def test_s012_malformed_step(step: Step) -> None:
    assert _codes(_spec(scenarios=[Scenario("m", None, [step])])) == ["S012"]


def test_s012_malformed_given() -> None:
    assert _codes(_spec(scenarios=[Scenario("g", {"orders": [1]}, ())])) == ["S012"]
    assert _codes(_spec(scenarios=[Scenario("g", {"orders": {"id": 1}}, ())])) == ["S012"]


def test_s013_duplicate_program_name() -> None:
    programs = [("orders", ORDERS_PROGRAM), ("orders", PRODUCTS_PROGRAM)]
    assert _codes(_spec(programs=programs)) == ["S013"]


def test_s014_endpoint_in_two_programs() -> None:
    programs = {"orders": ORDERS_PROGRAM, "again": ORDERS_PROGRAM.replace("get_order", "g2")}
    assert _codes(_spec(programs=programs)) == ["S014"]
    same_name = ORDERS_PROGRAM.replace("/orders/{order_id}", "/o/{order_id}")
    assert _codes(_spec(programs={"orders": ORDERS_PROGRAM, "again": same_name})) == ["S014"]


def test_s015_malformed_user() -> None:
    assert _codes(_spec(users={"alice": ("a@x",)})) == ["S015"]
    assert _codes(_spec(users={"alice": ("a@x", 1)})) == ["S015"]


def test_s016_unknown_concern() -> None:
    unchecked = [Unchecked("x", concerns=["Order.nope"]), Unchecked("y", concerns=["shipping"])]
    assert _codes(_spec(unchecked=unchecked)) == ["S016", "S016"]
    known = Unchecked("z", concerns=["Order.status", "products", "list_products"])
    assert _codes(_spec(unchecked=[known])) == []
