"""Tests for the API benchmark's data layer: bench/api_datasets.py and
bench/api_kit/platform_kit.py."""

from __future__ import annotations

import filecmp
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from bench.api_datasets import (
    ADDRESSES,
    ORDER_ITEMS,
    ORDERS,
    PRODUCTS,
    REVIEWS,
    ROLES,
    SQL_TYPES,
    TOKENS,
    USER_IDS,
    USERS,
    api_profile,
    build_db,
    schema_description,
)
from bench.api_kit import platform_kit
from lattice.typesys import Scalar

S = Scalar

_SCHEMAS_BY_SQL_NAME = {
    "users": USERS,
    "products": PRODUCTS,
    "orders": ORDERS,
    "order_items": ORDER_ITEMS,
    "reviews": REVIEWS,
    "addresses": ADDRESSES,
}

# Reverse of SQL_TYPES restricted to the "declared" storage type; Bool/Date
# both map onto SQLite's INTEGER/TEXT so we check per-scalar, not by
# collecting the affinity set.
_EXPECTED_SQLITE_TYPE = {
    S.INT: "INTEGER",
    S.FLOAT: "REAL",
    S.STR: "TEXT",
    S.BOOL: "INTEGER",
    S.DATE: "TEXT",
}


# ---------------------------------------------------------------------------
# build_db: determinism, shape, referential integrity, consistency
# ---------------------------------------------------------------------------


def test_build_db_byte_identical_across_runs(tmp_path: Path) -> None:
    path_a = tmp_path / "a.db"
    path_b = tmp_path / "b.db"
    build_db(path_a, seed=21)
    build_db(path_b, seed=21)
    assert filecmp.cmp(path_a, path_b, shallow=False)


def test_build_db_rebuild_in_place_is_stable(tmp_path: Path) -> None:
    """Calling build_db twice at the same path (as a re-run would) produces
    the same bytes, not an accumulation of an old file's artifacts."""
    path = tmp_path / "app.db"
    build_db(path, seed=21)
    first = path.read_bytes()
    build_db(path, seed=21)
    second = path.read_bytes()
    assert first == second


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    path = tmp_path / "app.db"
    build_db(path, seed=21)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _count(db: sqlite3.Connection, table: str) -> int:
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_row_counts(db: sqlite3.Connection) -> None:
    assert _count(db, "users") == 40
    assert _count(db, "products") == 30
    assert _count(db, "orders") == 300
    assert _count(db, "order_items") == 900
    assert _count(db, "reviews") == 120
    assert _count(db, "addresses") == 60


def test_admin_count(db: sqlite3.Connection) -> None:
    assert _count(db, "users") == 40
    admins = db.execute("SELECT COUNT(*) FROM users WHERE role = 'Admin'").fetchone()[0]
    assert admins == 3


def test_products_have_some_inactive_and_some_out_of_stock(db: sqlite3.Connection) -> None:
    inactive = db.execute("SELECT COUNT(*) FROM products WHERE active = 0").fetchone()[0]
    zero_stock = db.execute("SELECT COUNT(*) FROM products WHERE stock = 0").fetchone()[0]
    assert inactive > 0
    assert zero_stock > 0


def test_every_order_status_present(db: sqlite3.Connection) -> None:
    statuses = {row[0] for row in db.execute("SELECT DISTINCT status FROM orders")}
    assert statuses == {"pending", "paid", "shipped", "cancelled"}


def test_every_customer_has_at_least_two_orders_except_a_few(db: sqlite3.Connection) -> None:
    counts = dict(
        db.execute(
            "SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id"
        ).fetchall()
    )
    customer_ids = {
        row[0] for row in db.execute("SELECT id FROM users WHERE role = 'Customer'")
    }
    zero_order = [c for c in customer_ids if counts.get(c, 0) == 0]
    with_orders = [c for c in customer_ids if counts.get(c, 0) > 0]
    assert 0 < len(zero_order) < len(customer_ids)
    assert all(counts[c] >= 2 for c in with_orders)


def test_referential_integrity(db: sqlite3.Connection) -> None:
    user_ids = {row[0] for row in db.execute("SELECT id FROM users")}
    product_ids = {row[0] for row in db.execute("SELECT id FROM products")}
    order_ids = {row[0] for row in db.execute("SELECT id FROM orders")}

    for row in db.execute("SELECT customer_id FROM orders"):
        assert row[0] in user_ids
    for row in db.execute("SELECT order_id, product_id FROM order_items"):
        assert row[0] in order_ids
        assert row[1] in product_ids
    for row in db.execute("SELECT product_id, customer_id FROM reviews"):
        assert row[0] in product_ids
        assert row[1] in user_ids
    for row in db.execute("SELECT customer_id FROM addresses"):
        assert row[0] in user_ids


def test_order_total_matches_its_items(db: sqlite3.Connection) -> None:
    rows = db.execute(
        """
        SELECT o.id, o.total, COALESCE(SUM(oi.qty * oi.unit_price), 0.0)
        FROM orders o LEFT JOIN order_items oi ON oi.order_id = o.id
        GROUP BY o.id
        """
    ).fetchall()
    assert rows, "expected orders to exist"
    for order_id, total, items_sum in rows:
        assert round(total, 2) == round(items_sum, 2), order_id


def test_tokens_resolve_to_the_right_users_and_roles(db: sqlite3.Connection) -> None:
    expected_roles = {
        "alice": "Customer",
        "bob": "Customer",
        "carol": "Customer",
        "dave": "Customer",
        "admin": "Admin",
    }
    for handle, token in TOKENS.items():
        row = db.execute("SELECT id, role FROM users WHERE token = ?", (token,)).fetchone()
        assert row is not None, handle
        assert row[0] == USER_IDS[handle]
        assert row[1] == expected_roles[handle]


def test_alice_bob_carol_have_orders_dave_does_not(db: sqlite3.Connection) -> None:
    for handle in ("alice", "bob", "carol"):
        uid = USER_IDS[handle]
        n = db.execute("SELECT COUNT(*) FROM orders WHERE customer_id = ?", (uid,)).fetchone()[0]
        assert n >= 2, handle
    dave_orders = db.execute(
        "SELECT COUNT(*) FROM orders WHERE customer_id = ?", (USER_IDS["dave"],)
    ).fetchone()[0]
    assert dave_orders == 0


def test_private_columns_never_null_and_tokens_unique(db: sqlite3.Connection) -> None:
    rows = db.execute("SELECT token, password_hash FROM users").fetchall()
    assert all(t is not None and h is not None for t, h in rows)
    tokens = [t for t, _ in rows]
    assert len(tokens) == len(set(tokens))


# ---------------------------------------------------------------------------
# Schema metadata: owner/private, and Schema <-> actual SQLite columns/types
# ---------------------------------------------------------------------------


def test_owner_metadata() -> None:
    assert USERS.owner is None
    assert PRODUCTS.owner is None
    assert ORDERS.owner == "customer_id"
    assert ORDER_ITEMS.owner is None  # owned through its parent order
    assert ORDER_ITEMS.owner_via == ("order_id", "Orders")
    assert REVIEWS.owner == "customer_id"
    assert ADDRESSES.owner == "customer_id"


def test_private_metadata() -> None:
    assert USERS.private == frozenset({"password_hash", "token"})
    for schema in (PRODUCTS, ORDERS, ORDER_ITEMS, REVIEWS, ADDRESSES):
        assert schema.private == frozenset()


def test_keys_metadata() -> None:
    for schema in (USERS, PRODUCTS, ORDERS, ORDER_ITEMS, REVIEWS, ADDRESSES):
        assert schema.keys == (("id",),)


def test_sql_types_documents_every_scalar() -> None:
    assert set(SQL_TYPES) == set(Scalar)


def test_schemas_match_actual_sqlite_columns(db: sqlite3.Connection) -> None:
    for table, schema in _SCHEMAS_BY_SQL_NAME.items():
        cols = db.execute(f"PRAGMA table_info({table})").fetchall()
        actual_names = [c[1] for c in cols]
        actual_types = {c[1]: c[2].upper() for c in cols}
        assert actual_names == list(schema.names()), table
        for name, scalar in schema.columns:
            assert actual_types[name] == _EXPECTED_SQLITE_TYPE[scalar], (table, name)


def test_api_profile_shape() -> None:
    profile = api_profile()
    assert profile.schemas == {}
    assert profile.outputs is None
    api = profile.api
    assert api is not None
    assert api.roles == frozenset({"Public", "Customer", "Admin"}) == ROLES
    assert set(api.tables) == {
        "Users",
        "Products",
        "Orders",
        "OrderItems",
        "Reviews",
        "Addresses",
    }
    assert api.table_names == {
        "Users": "users",
        "Products": "products",
        "Orders": "orders",
        "OrderItems": "order_items",
        "Reviews": "reviews",
        "Addresses": "addresses",
    }
    assert api.tables["Users"] is USERS
    assert api.tables["Orders"] is ORDERS
    assert api.admin_role == "Admin"
    assert api.users_table == "Users"


def test_schema_description_mentions_every_table_role_and_auth_scheme() -> None:
    text = schema_description()
    for sql_name in ("users", "products", "orders", "order_items", "reviews", "addresses"):
        assert sql_name in text
    for role in ("Public", "Customer", "Admin"):
        assert role in text
    assert "password_hash" in text
    assert "token" in text
    assert "customer_id" in text
    assert "Authorization" in text
    assert "Bearer" in text
    assert "401" in text
    assert "403" in text
    assert "owned through order_id → Orders" in text


# ---------------------------------------------------------------------------
# platform_kit: auth-only FastAPI scaffolding
# ---------------------------------------------------------------------------


@pytest.fixture()
def kit_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A tiny app built on platform_kit, run with cwd set to a tmp dir that
    holds app.db (get_db() connects to "app.db" relative to the cwd)."""
    build_db(tmp_path / platform_kit.DB_FILENAME, seed=21)
    monkeypatch.chdir(tmp_path)

    tiny_app = FastAPI()
    tiny_app.exception_handlers = dict(platform_kit.app.exception_handlers)

    @tiny_app.get("/whoami")
    def whoami(
        user: dict = Depends(platform_kit.current_user),  # noqa: B008 -- idiomatic FastAPI DI
    ) -> dict:
        return {"user": user}

    require_admin = platform_kit.require_role("Admin")
    require_customer = platform_kit.require_role("Customer")

    @tiny_app.get("/admin-only")
    def admin_only(user: dict = Depends(require_admin)) -> dict:  # noqa: B008
        return {"user": user}

    @tiny_app.get("/customer-only")
    def customer_only(user: dict = Depends(require_customer)) -> dict:  # noqa: B008
        return {"user": user}

    with TestClient(tiny_app) as client:
        yield client


def test_current_user_401_without_token(kit_client: TestClient) -> None:
    resp = kit_client.get("/whoami")
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_current_user_401_with_unknown_token(kit_client: TestClient) -> None:
    resp = kit_client.get("/whoami", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_current_user_returns_the_right_user(kit_client: TestClient) -> None:
    resp = kit_client.get("/whoami", headers={"Authorization": f"Bearer {TOKENS['alice']}"})
    assert resp.status_code == 200
    user = resp.json()["user"]
    assert user["id"] == USER_IDS["alice"]
    assert user["role"] == "Customer"
    assert user["email"] == "alice@example.com"
    assert user["name"]


def test_require_role_403_for_wrong_role(kit_client: TestClient) -> None:
    resp = kit_client.get(
        "/admin-only", headers={"Authorization": f"Bearer {TOKENS['alice']}"}
    )
    assert resp.status_code == 403
    assert resp.json() == {"error": "forbidden"}


def test_require_role_admin_always_allowed(kit_client: TestClient) -> None:
    resp = kit_client.get(
        "/customer-only", headers={"Authorization": f"Bearer {TOKENS['admin']}"}
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["role"] == "Admin"


def test_require_role_allows_permitted_role(kit_client: TestClient) -> None:
    resp = kit_client.get(
        "/customer-only", headers={"Authorization": f"Bearer {TOKENS['bob']}"}
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["role"] == "Customer"
