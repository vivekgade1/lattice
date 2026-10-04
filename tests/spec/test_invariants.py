"""compile_invariant: each allowed form returns exactly the violating rows of a
hand-built SQLite database, and every other form is refused with a clear error."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lattice.spec import Invariant, InvariantError, compile_invariant
from tests.spec import shop


def _inv(expr: str) -> Invariant:
    return Invariant(name="rule", sentence="a rule", expr=expr)


def _violations(conn: sqlite3.Connection, expr: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return list(conn.execute(compile_invariant(_inv(expr), shop.profile())))


def _ids(conn: sqlite3.Connection, expr: str) -> list[int]:
    return sorted(row["id"] for row in _violations(conn, expr))


# ---- row rules ---------------------------------------------------------------------------

PRODUCTS = [
    # id, vendor_id, name, description, category, price, stock, is_active, created_at
    (1, 1, "pen", "a pen", "office", 2.5, 10, 1, "2024-01-01 00:00:00"),
    (2, 1, "ink", None, "office", 0.0, 0, 1, "2024-01-01 00:00:00"),
    (3, 1, "pad", "a pad", "office", 4.0, -1, 0, "2024-01-01 00:00:00"),
    (4, 1, "cup", "a cup", "kitchen", 3.0, None, 1, "2024-01-01 00:00:00"),
    (5, 1, "mug", "a mug", "kitchen", -1.0, 5, 0, "2024-01-01 00:00:00"),
]


@pytest.fixture
def products(tmp_path: Path) -> sqlite3.Connection:
    return shop.build_db(tmp_path / "p.db", {"products": PRODUCTS})


def test_row_rule_ge(products: sqlite3.Connection) -> None:
    # NULL stock violates: a rule holds only where it is known to be true
    assert _ids(products, "Product.stock_quantity >= 0") == [3, 4]


def test_row_rule_holds_returns_nothing(products: sqlite3.Connection) -> None:
    assert _ids(products, "Product.id > 0") == []


def test_row_rule_null_allowed_with_is_null(products: sqlite3.Connection) -> None:
    expr = "is_null(Product.stock_quantity) or Product.stock_quantity >= 0"
    assert _ids(products, expr) == [3]


def test_row_rule_and(products: sqlite3.Connection) -> None:
    expr = "Product.price > 0 and Product.stock_quantity >= 0"
    assert _ids(products, expr) == [2, 3, 4, 5]


def test_row_rule_not_and_is_null(products: sqlite3.Connection) -> None:
    assert _ids(products, "not is_null(Product.description)") == [2]


def test_row_rule_in_and_not_in(products: sqlite3.Connection) -> None:
    assert _ids(products, 'Product.category in ("office",)') == [4, 5]
    assert _ids(products, 'Product.category not in ["kitchen"]') == [4, 5]


def test_row_rule_bool_column(products: sqlite3.Connection) -> None:
    assert _ids(products, "Product.is_active") == [3, 5]
    assert _ids(products, "Product.is_active == True") == [3, 5]
    assert _ids(products, "not Product.is_active") == [1, 2, 4]


def test_row_rule_arithmetic_and_negative_literal(products: sqlite3.Connection) -> None:
    assert _ids(products, "Product.price * Product.stock_quantity <= 20") == [1, 4]
    assert _ids(products, "Product.price >= -0.5") == [5]


def test_row_rule_column_against_column(products: sqlite3.Connection) -> None:
    assert _ids(products, "Product.price <= Product.stock_quantity") == [3, 4]


def test_row_rule_returns_the_whole_row(products: sqlite3.Connection) -> None:
    (row,) = _violations(products, "Product.price < 4")
    assert row["id"] == 3 and row["name"] == "pad" and row["price"] == 4.0


ORDERS_STATUS = [
    (1, 1, "PENDING", 0.0),
    (2, 1, "SHIPPED", 0.0),
    (3, 1, "pending", 0.0),  # not the stored spelling of any state
    (4, 1, "CANCELLED", 0.0),
]


@pytest.fixture
def statuses(tmp_path: Path) -> sqlite3.Connection:
    return shop.build_db(tmp_path / "s.db", {"orders": ORDERS_STATUS})


def test_one_of_compares_the_stored_spelling(statuses: sqlite3.Connection) -> None:
    assert _ids(statuses, 'Order.status in ("pending", "shipped")') == [3, 4]
    assert _ids(statuses, 'Order.status != "cancelled"') == [4]
    assert _ids(statuses, 'Order.status == "pending"') == [2, 3, 4]
    assert _ids(statuses, 'Order.status not in ("cancelled",)') == [4]


def test_one_of_stored_on_users_role(tmp_path: Path) -> None:
    users = [(1, "a@x", "BUYER", "t1", 1), (2, "b@x", "ADMIN", "t2", 1)]
    conn = shop.build_db(tmp_path / "u.db", {"users": users})
    assert _ids(conn, 'User.role != "admin"') == [2]


# ---- aggregate equality ------------------------------------------------------------------

ORDERS = [
    (1, 1, "PENDING", 30.0),  # 2 * 10 + 1 * 10 = 30: holds
    (2, 1, "PENDING", 25.0),  # lines sum to 30: violates
    (3, 1, "PENDING", 0.0),  # no lines, sums to 0: holds
    (4, 1, "PENDING", 5.0),  # no lines, sums to 0: violates
    (5, 1, "PENDING", 0.3),  # 0.1 + 0.2 within tolerance: holds
    (6, 1, "PENDING", 1e9),  # 1e9 + 0.5: relative difference 5e-10, holds
    (7, 1, "PENDING", 100.0),  # 100.001: violates
    (8, 1, "PENDING", None),  # unknown total: violates
]
ITEMS = [
    (1, 1, 1, 2, 10.0),
    (2, 1, 2, 1, 10.0),
    (3, 2, 1, 3, 10.0),
    (4, 5, 1, 1, 0.1),
    (5, 5, 2, 1, 0.2),
    (6, 6, 1, 1, 1e9),
    (7, 6, 2, 1, 0.5),
    (8, 7, 1, 1, 100.001),
    (9, 8, 1, 1, 1.0),
]
TOTAL = "Order.total_amount == sum(OrderItem.quantity * OrderItem.price_at_purchase, by='order_id')"


@pytest.fixture
def orders(tmp_path: Path) -> sqlite3.Connection:
    return shop.build_db(tmp_path / "o.db", {"orders": ORDERS, "order_items": ITEMS})


def test_aggregate_equality(orders: sqlite3.Connection) -> None:
    assert _ids(orders, TOTAL) == [2, 4, 7, 8]


def test_aggregate_equality_either_side(orders: sqlite3.Connection) -> None:
    flipped = (
        "sum(OrderItem.quantity * OrderItem.price_at_purchase, by='order_id') == Order.total_amount"
    )
    assert _ids(orders, flipped) == [2, 4, 7, 8]


def test_aggregate_equality_reports_the_expected_sum(orders: sqlite3.Connection) -> None:
    rows = {row["id"]: row for row in _violations(orders, TOTAL)}
    assert rows[2]["expected"] == pytest.approx(30.0)
    assert rows[4]["expected"] == 0
    assert rows[2]["total_amount"] == 25.0


def test_aggregate_equality_holds(tmp_path: Path) -> None:
    conn = shop.build_db(
        tmp_path / "ok.db",
        {"orders": [(1, 1, "PENDING", 30.0), (2, 1, "PENDING", 0.0)], "order_items": ITEMS[:2]},
    )
    assert _ids(conn, TOTAL) == []


# ---- count bound -------------------------------------------------------------------------

VENDORS = [
    (1, 1, "a"),
    (2, 1, "b"),  # user 1 has two vendors
    (3, 2, "c"),
    (4, None, "d"),  # vendors without a user are not one user's
    (5, None, "e"),
]


@pytest.fixture
def vendors(tmp_path: Path) -> sqlite3.Connection:
    return shop.build_db(tmp_path / "v.db", {"vendors": VENDORS})


def _groups(conn: sqlite3.Connection, expr: str) -> list[tuple]:
    return sorted(tuple(row) for row in _violations(conn, expr))


def test_count_bound(vendors: sqlite3.Connection) -> None:
    assert _groups(vendors, 'count(Vendor, by="user_id") <= 1') == [(1, 2)]
    (row,) = _violations(vendors, 'count(Vendor, by="user_id") <= 1')
    assert row["user_id"] == 1 and row["count"] == 2


def test_count_bound_other_operators(vendors: sqlite3.Connection) -> None:
    assert _groups(vendors, 'count(Vendor, by="user_id") < 2') == [(1, 2)]
    assert _groups(vendors, '1 >= count(Vendor, by="user_id")') == [(1, 2)]
    assert _groups(vendors, 'count(Vendor, by="user_id") == 1') == [(1, 2)]
    assert _groups(vendors, 'count(Vendor, by="user_id") <= 2') == []


# ---- refusals ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "code", "words"),
    [
        ("Product.price >", "S007", "parse"),
        ("Product.price if True else 0", "S008", "allowed forms"),
        ("max(Product.price) > 0", "S008", "allowed forms"),
        ("Product.price > 0 > 1", "S008", "allowed forms"),
        ("x > 0", "S008", "Table.column"),
        ("Product.price == None", "S008", "is_null"),
        ("sum(OrderItem.quantity, by='order_id') > 0", "S008", "allowed forms"),
        ("count(Vendor, by='user_id') <= Vendor.id", "S008", "int"),
        ("Order.total_amount == sum(OrderItem.quantity)", "S008", "by="),
        ("Missing.price > 0", "S002", "Missing"),
        ("Product.nope > 0", "S003", "nope"),
        ("count(Vendor, by='nope') <= 1", "S003", "nope"),
        ("Order.total_amount == sum(OrderItem.quantity, by='nope')", "S003", "nope"),
        ("Product.price > Order.total_amount", "S008", "one table"),
        ('Order.status == "lost"', "S009", "lost"),
        ('Order.status < "pending"', "S009", "stored"),
        ('Product.created_at > "2024-01-01"', "S009", "DateTime"),
        ("Product.name > 0", "S009", "Str"),
        ("Order.total_amount == sum(OrderItem.order_id == 1, by='order_id')", "S008", ""),
        ("Order.status == sum(OrderItem.quantity, by='order_id')", "S009", "numeric"),
    ],
)
def test_refused(expr: str, code: str, words: str) -> None:
    with pytest.raises(InvariantError) as info:
        compile_invariant(_inv(expr), shop.profile())
    assert info.value.code == code
    assert words in str(info.value)


def test_refusal_names_the_allowed_forms() -> None:
    with pytest.raises(InvariantError) as info:
        compile_invariant(_inv("lambda: 1"), shop.profile())
    text = str(info.value)
    assert "row rule" in text and "sum(" in text and "count(" in text
