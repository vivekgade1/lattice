"""Implement fixture: the mini-shop spec as it is today (self-contained)."""

from lattice.profile import ApiProfile, Profile, RoleColumn
from lattice.spec import Invariant, Machine, Scenario, Spec, Step, Unchecked
from lattice.typesys import Scalar, Schema

S = Scalar

# handle -> (id, password, role, active)
USERS = {
    "ann": (1, "pw-ann", "Buyer", 1),
    "ben": (2, "pw-ben", "Buyer", 1),
    "ada": (3, "pw-ada", "Admin", 1),
    "eve": (4, "pw-eve", "Buyer", 0),
}

USER = Schema.from_pairs(
    [
        ("id", S.INT),
        ("name", S.STR),
        ("password", S.STR),
        ("token", S.STR),
        ("role", S.STR),
        ("is_active", S.BOOL),
    ],
    keys=[("id",), ("token",)],
    # the token is not private: POST /login answers the caller's own token
    private=["password"],
)
PRODUCT = Schema.from_pairs(
    [("id", S.INT), ("name", S.STR), ("price", S.FLOAT), ("stock", S.INT)], keys=[("id",)]
)
ORDER = Schema.from_pairs(
    [("id", S.INT), ("user_id", S.INT), ("status", S.STR), ("total", S.FLOAT)],
    keys=[("id",)],
    owner="user_id",
    one_of={"status": ("pending", "paid", "shipped", "cancelled")},
)
ORDER_ITEM = Schema.from_pairs(
    [
        ("id", S.INT),
        ("order_id", S.INT),
        ("product_id", S.INT),
        ("quantity", S.INT),
        ("price", S.FLOAT),
    ],
    keys=[("id",)],
    owner_via=("order_id", "Order"),
)


def profile() -> Profile:
    api = ApiProfile(
        tables={"User": USER, "Product": PRODUCT, "Order": ORDER, "OrderItem": ORDER_ITEM},
        table_names={
            "User": "users",
            "Product": "products",
            "Order": "orders",
            "OrderItem": "order_items",
        },
        roles=frozenset({"Public", "Buyer", "Admin"}),
        users_table="User",
        roles_from=RoleColumn("role"),
        active_column="is_active",
        error_body="fastapi",
        unauthorized_detail="Not authenticated",
    )
    return Profile(schemas={}, api=api)


ORDERS_PROGRAM = """# lattice: v1
ORDER_COLS = ["id", "user_id", "status", "total"]


@endpoint("GET", "/orders", auth=Authenticated)
def list_orders(user: CurrentUser) -> List:
    orders = fetch(Order)
    own = mine(orders, by=user)
    ordered = sort(own, by=["id"])
    shown = select(ordered, cols=ORDER_COLS)
    few = limit(shown, n=100)
    return few


@endpoint("GET", "/orders/{order_id}", auth=Authenticated)
def read_order(order_id: Int, user: CurrentUser) -> One | NotFound:
    orders = fetch(Order)
    own = mine(orders, by=user)
    found = where(own, col("id") == order_id)
    order = one(found, else_=NotFound("Order not found"))
    shown = select(order, cols=ORDER_COLS)
    return shown


@endpoint("POST", "/orders/{order_id}/cancel", auth=Authenticated)
def cancel_order(order_id: Int, user: CurrentUser) -> One | NotFound | Conflict:
    orders = fetch(Order)
    own = mine(orders, by=user)
    found = where(own, col("id") == order_id)
    order = one(found, else_=NotFound("Order not found"))
    pending = require(
        order, col("status") == "pending", else_=Conflict("Order cannot be cancelled")
    )
    done = update(pending, status="cancelled")
    shown = select(done, cols=ORDER_COLS)
    return shown
"""

INVARIANTS = (
    Invariant(
        "order_total",
        "An order's total is the sum of its lines",
        'Order.total == sum(OrderItem.quantity * OrderItem.price, by="order_id")',
    ),
    Invariant("stock_non_negative", "Stock is never negative", "Product.stock >= 0"),
)

SCENARIOS = (
    Scenario(
        "ann cancels her pending order",
        steps=(
            Step(
                "POST",
                "/orders/1/cancel",
                user="ann",
                expect_status=200,
                expect_body={"id": 1, "status": "cancelled"},
                expect_db={"orders": {"where": {"id": 1}, "equals": {"status": "cancelled"}}},
            ),
            Step("POST", "/orders/1/cancel", user="ann", expect_status=409),
        ),
    ),
    Scenario(
        "ann adds a line to a new order",
        given={"orders": [{"id": 10, "user_id": 1, "status": "pending", "total": 0.0}]},
        steps=(
            Step(
                "POST",
                "/orders/10/items",
                user="ann",
                json={"product_id": 2, "quantity": 3},
                expect_status=201,
                expect_body={"order_id": 10, "product_id": 2, "quantity": 3},
                expect_db={
                    "order_items": {
                        "where": {"order_id": 10, "product_id": 2},
                        "equals": {"quantity": 3},
                    }
                },
            ),
        ),
    ),
)


def spec() -> Spec:
    return Spec(
        profile=profile(),
        programs={"orders": ORDERS_PROGRAM},
        machines=(
            Machine(
                "Order",
                "status",
                {"pending": ("paid", "cancelled"), "paid": "shipped"},
                initial="pending",
            ),
        ),
        invariants=INVARIANTS,
        scenarios=SCENARIOS,
        unchecked=(Unchecked("a shipped order cannot be changed", concerns="Order"),),
        users={h: (h, pw) for h, (_, pw, _, _) in USERS.items()},
    )


SPEC = spec()
