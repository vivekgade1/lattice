"""A small FastAPI + SQLModel shop and its Lattice spec, for the check engine's tests.

The app (package ``<name>``, written by :func:`write_app` under a directory and imported
from there) has users with bearer tokens stored in ``users.token`` (``POST /login``
answers the token), products with stock, orders owned by a user (``user_id``) and order
lines owned through their order. Routes: list / read / cancel the caller's orders and
add a line to a pending order (which moves stock and the order's total).

The spec (:func:`spec`) has programs for the three order routes (layer B), the
``Order.status`` machine, two invariants (an order's total is the sum of its lines, stock
never negative; layer C) and scenarios (layer D).

Broken variants (:data:`BROKEN`) each break one layer's guarantee:

* ``partial_write`` (A): ``add_item`` commits the line before the stock check (PY807);
* ``wrong_answer`` (B): ``read_order`` shows another user's order;
* ``total_drift`` (C): ``add_item`` does not add the line to the order's total;
* ``wrong_quantity`` (D): ``add_item`` stores quantity 1 whatever was asked.
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lattice.profile import ApiProfile, Profile, RoleColumn
from lattice.spec import Invariant, Machine, Scenario, Spec, Step, Unchecked
from lattice.typesys import Scalar, Schema

S = Scalar

# ---- the app ---------------------------------------------------------------------------

MODELS = """\
from sqlalchemy.orm import registry
from sqlmodel import Field, SQLModel


class TableBase(SQLModel, registry=registry()):
    pass


class User(TableBase, table=True):
    __tablename__ = "users"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    password: str
    token: str
    role: str = "Buyer"
    is_active: bool = True


class Product(TableBase, table=True):
    __tablename__ = "products"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    price: float
    stock: int


class Order(TableBase, table=True):
    __tablename__ = "orders"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    status: str = "pending"
    total: float = 0.0


class OrderItem(TableBase, table=True):
    __tablename__ = "order_items"
    id: int | None = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="orders.id")
    product_id: int = Field(foreign_key="products.id")
    quantity: int
    price: float


class OrderPublic(SQLModel):
    id: int
    user_id: int
    status: str
    total: float


class ItemCreate(SQLModel):
    product_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=100)


class OrderItemPublic(SQLModel):
    id: int
    order_id: int
    product_id: int
    quantity: int
    price: float
"""

DEPS = """\
from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlmodel import Session, create_engine, select

from PKG.models import User

engine = create_engine("sqlite:///shop.db")


def get_db() -> Generator[Session]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_db)]


def get_current_user(
    session: SessionDep, authorization: Annotated[str | None, Header()] = None
) -> User:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = session.exec(select(User).where(User.token == token)).first()
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
"""

ROUTES = """\
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException
from sqlmodel import select

from PKG.deps import CurrentUser, SessionDep
from PKG.models import ItemCreate, Order, OrderItem, OrderItemPublic, OrderPublic, Product, User

router = APIRouter()


@router.post("/login")
def login(
    session: SessionDep, username: Annotated[str, Form()], password: Annotated[str, Form()]
) -> Any:
    user = session.exec(select(User).where(User.name == username)).first()
    if not user or user.password != password:
        raise HTTPException(status_code=400, detail="Incorrect name or password")
    return {"access_token": user.token}


@router.get("/orders", response_model=list[OrderPublic])
def list_orders(session: SessionDep, current_user: CurrentUser) -> Any:
    statement = select(Order).where(Order.user_id == current_user.id)
    return session.exec(statement.order_by(Order.id).limit(100)).all()


@router.get("/orders/{order_id}", response_model=OrderPublic)
def read_order(session: SessionDep, current_user: CurrentUser, order_id: int) -> Any:
    order = session.get(Order, order_id)
    if not order or order.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.post("/orders/{order_id}/cancel", response_model=OrderPublic)
def cancel_order(session: SessionDep, current_user: CurrentUser, order_id: int) -> Any:
    order = session.get(Order, order_id)
    if not order or order.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "pending":
        raise HTTPException(status_code=409, detail="Order cannot be cancelled")
    order.status = "cancelled"
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


@router.post("/orders/{order_id}/items", response_model=OrderItemPublic, status_code=201)
def add_item(
    session: SessionDep, current_user: CurrentUser, order_id: int, item_in: ItemCreate
) -> Any:
    order = session.get(Order, order_id)
    if not order or order.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "pending":
        raise HTTPException(status_code=409, detail="Order is not pending")
    product = session.get(Product, item_in.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    if product.stock < item_in.quantity:
        raise HTTPException(status_code=409, detail="Insufficient stock")
    line = OrderItem(
        order_id=order.id, product_id=product.id, quantity=item_in.quantity,
        price=product.price,
    )
    product.stock = product.stock - item_in.quantity
    order.total = order.total + item_in.quantity * product.price
    session.add(line)
    session.add(product)
    session.add(order)
    session.commit()
    session.refresh(line)
    return line
"""

MAIN = """\
from fastapi import FastAPI
from sqlmodel import Session, create_engine

from PKG import routes
from PKG.deps import get_db


def make_app(db_path) -> FastAPI:
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    app = FastAPI()
    app.state.engine = engine

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = session_override
    app.include_router(routes.router)
    return app
"""

FILES = {
    "__init__.py": "",
    "models.py": MODELS,
    "deps.py": DEPS,
    "routes.py": ROUTES,
    "main.py": MAIN,
}

# variant -> (file, old text, new text): each breaks one layer's guarantee
BROKEN: dict[str, tuple[str, str, str]] = {
    # A: the line is committed before the stock check, which can still refuse (PY807)
    "partial_write": (
        "routes.py",
        """    if product.stock < item_in.quantity:
        raise HTTPException(status_code=409, detail="Insufficient stock")
    line = OrderItem(
        order_id=order.id, product_id=product.id, quantity=item_in.quantity,
        price=product.price,
    )
""",
        """    line = OrderItem(
        order_id=order.id, product_id=product.id, quantity=item_in.quantity,
        price=product.price,
    )
    session.add(line)
    session.commit()
    if product.stock < item_in.quantity:
        raise HTTPException(status_code=409, detail="Insufficient stock")
""",
    ),
    # B: another user's order is shown
    "wrong_answer": (
        "routes.py",
        """    order = session.get(Order, order_id)
    if not order or order.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Order not found")
    return order
""",
        """    order = session.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order
""",
    ),
    # C: the order's total misses the new line
    "total_drift": (
        "routes.py",
        "    order.total = order.total + item_in.quantity * product.price\n",
        "",
    ),
    # D: the line's quantity is always 1 (stock and total follow it, so no invariant breaks)
    "wrong_quantity": (
        "routes.py",
        """    line = OrderItem(
        order_id=order.id, product_id=product.id, quantity=item_in.quantity,
        price=product.price,
    )
    product.stock = product.stock - item_in.quantity
    order.total = order.total + item_in.quantity * product.price
""",
        """    line = OrderItem(
        order_id=order.id, product_id=product.id, quantity=1, price=product.price,
    )
    product.stock = product.stock - 1
    order.total = order.total + product.price
""",
    ),
}


def sources(variant: str = "ok", edit: tuple[str, str, str] | None = None) -> dict[str, str]:
    """The app's files, with the ``variant``'s edit (or ``edit``: (file, old, new))."""
    files = dict(FILES)
    change = edit if edit is not None else BROKEN.get(variant)
    if change is not None:
        rel, old, new = change
        assert old in files[rel], variant
        files[rel] = files[rel].replace(old, new)
    return files


def write_app(
    root: Path, variant: str = "ok", edit: tuple[str, str, str] | None = None
) -> tuple[Path, str]:
    """The app ``variant`` (or the original with ``edit``) as a fresh package under
    ``root`` (on ``sys.path``): (package directory, package name)."""
    package = f"minishop_{variant}_{uuid.uuid4().hex[:8]}"
    pkg = root / package
    pkg.mkdir(parents=True)
    for rel, src in sources(variant, edit).items():
        (pkg / rel).write_text(src.replace("PKG", package))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    return pkg, package


def app_factory(package: str) -> Callable[[Path], Any]:
    return importlib.import_module(f"{package}.main").make_app


# ---- the database ----------------------------------------------------------------------

DDL = """
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, password TEXT NOT NULL,
                    token TEXT NOT NULL UNIQUE, role TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL);
CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, price FLOAT NOT NULL,
                       stock INTEGER NOT NULL);
CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                     status TEXT NOT NULL, total FLOAT NOT NULL);
CREATE TABLE order_items (id INTEGER PRIMARY KEY,
                          order_id INTEGER NOT NULL REFERENCES orders(id),
                          product_id INTEGER NOT NULL REFERENCES products(id),
                          quantity INTEGER NOT NULL, price FLOAT NOT NULL);
"""

# handle -> (id, password, role, active)
USERS = {
    "ann": (1, "pw-ann", "Buyer", 1),
    "ben": (2, "pw-ben", "Buyer", 1),
    "ada": (3, "pw-ada", "Admin", 1),
    "eve": (4, "pw-eve", "Buyer", 0),
}
SEED = {
    "users": [(i, h, pw, f"tok-{h}", role, active) for h, (i, pw, role, active) in USERS.items()],
    "products": [(1, "pen", 2.5, 10), (2, "ink", 4.0, 5)],
    "orders": [
        (1, 1, "pending", 5.0),
        (2, 1, "paid", 4.0),
        (3, 2, "pending", 0.0),
        (4, 3, "pending", 2.5),
    ],
    "order_items": [(1, 1, 1, 2, 2.5), (2, 2, 2, 1, 4.0), (3, 4, 1, 1, 2.5)],
}


def build_db(path: Path) -> Path:
    """The seeded database at ``path`` (a one-argument db factory)."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        for table, rows in SEED.items():
            for row in rows:
                marks = ", ".join("?" for _ in row)
                conn.execute(f"INSERT INTO {table} VALUES ({marks})", row)
        conn.commit()
    finally:
        conn.close()
    return path


# ---- the spec --------------------------------------------------------------------------

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


def login(client: Any, handle: str) -> str:
    """Log in through the app's own ``POST /login``."""
    _, password, _, _ = USERS[handle]
    r = client.post("/login", data={"username": handle, "password": password})
    if r.status_code != 200:
        raise RuntimeError(f"login of {handle!r} failed: {r.status_code} {r.text}")
    return r.json()["access_token"]
