"""The vendored multi-vendor marketplace (``bench/import_repos/multivendor``) boots on
SQLite and behaves like upstream: auth, the role/permission table, ownership through
vendors and orders, the order state machine, and stock accounting.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bench.import_repos.multivendor import make_app
from bench.import_repos.multivendor.seed import SEED_USER_CREDENTIALS, build_seed_db

UPSTREAM_PATHS = {
    "/auth/register",
    "/auth/login",
    "/users/me",
    "/vendors",
    "/vendors/me",
    "/vendors/{vendor_id}/verify",
    "/products",
    "/products/{product_id}",
    "/orders",
    "/orders/me",
    "/orders/{order_id}",
    "/orders/{order_id}/status",
    "/orders/flash-sale-purchase",
}


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    db = build_seed_db(tmp_path / "multivendor.db")
    app = make_app(db)
    with TestClient(app) as c:
        yield c
    app.state.engine.dispose()


def _auth(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/auth/login", data={"username": email, "password": SEED_USER_CREDENTIALS[email]}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_app_starts_with_upstream_routes(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    assert paths == UPSTREAM_PATHS


def test_login_and_me(client: TestClient) -> None:
    me = client.get("/users/me", headers=_auth(client, "carol@example.com")).json()
    assert me["email"] == "carol@example.com"
    assert me["role"] == "buyer"
    assert "hashed_password" not in me


def test_bad_password_and_inactive_user_rejected(client: TestClient) -> None:
    bad = client.post("/auth/login", data={"username": "carol@example.com", "password": "nope"})
    assert bad.status_code == 401
    inactive = client.post(
        "/auth/login",
        data={
            "username": "erin@example.com",
            "password": SEED_USER_CREDENTIALS["erin@example.com"],
        },
    )
    assert inactive.status_code == 401
    assert inactive.json()["detail"] == "Account is inactive"


def test_auth_required(client: TestClient) -> None:
    assert client.get("/orders/me").status_code == 401


def test_register_new_user(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={"full_name": "New Person", "email": "new@example.com", "password": "pw123456"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "buyer"
    dup = client.post(
        "/auth/register",
        json={"full_name": "Again", "email": "new@example.com", "password": "pw123456"},
    )
    assert dup.status_code == 400


def test_products_public_and_inactive_hidden(client: TestClient) -> None:
    products = client.get("/products", params={"limit": 100}).json()
    assert products, "seed has products"
    assert all(p["is_active"] for p in products)


def test_buyer_sees_only_own_orders(client: TestClient) -> None:
    carol = _auth(client, "carol@example.com")
    dave = _auth(client, "dave@example.com")
    carol_orders = client.get("/orders/me", headers=carol).json()
    dave_orders = client.get("/orders/me", headers=dave).json()
    assert carol_orders and dave_orders
    assert {o["user_id"] for o in carol_orders} != {o["user_id"] for o in dave_orders}
    # another buyer's order: upstream answers 403, not 404
    response = client.get(f"/orders/{dave_orders[0]['id']}", headers=carol)
    assert response.status_code == 403


def test_admin_reads_any_order(client: TestClient) -> None:
    admin = _auth(client, "admin@example.com")
    dave_orders = client.get("/orders/me", headers=_auth(client, "dave@example.com")).json()
    response = client.get(f"/orders/{dave_orders[0]['id']}", headers=admin)
    assert response.status_code == 200


def test_vendor_updates_only_own_product(client: TestClient) -> None:
    vicky = _auth(client, "vicky@example.com")
    victor = _auth(client, "victor@example.com")
    vicky_vendor = client.get("/vendors/me", headers=vicky).json()
    products = client.get("/products", params={"limit": 100}).json()
    own = next(p for p in products if p["vendor_id"] == vicky_vendor["id"])
    other = next(p for p in products if p["vendor_id"] != vicky_vendor["id"])
    assert (
        client.patch(f"/products/{own['id']}", json={"price": 9.5}, headers=vicky).status_code
        == 200
    )
    denied = client.patch(f"/products/{other['id']}", json={"price": 1.0}, headers=vicky)
    assert denied.status_code == 403
    assert client.get("/vendors/me", headers=victor).status_code == 200


def test_place_order_decrements_stock_and_records_price(client: TestClient) -> None:
    carol = _auth(client, "carol@example.com")
    product = client.get("/products", params={"limit": 100}).json()[0]
    response = client.post(
        "/orders", json={"items": [{"product_id": product["id"], "quantity": 2}]}, headers=carol
    )
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["status"] == "pending"
    assert order["total_amount"] == pytest.approx(product["price"] * 2)
    assert order["items"][0]["price_at_purchase"] == product["price"]
    after = client.get(f"/products/{product['id']}").json()
    assert after["stock_quantity"] == product["stock_quantity"] - 2


def test_vendor_cannot_place_orders(client: TestClient) -> None:
    vicky = _auth(client, "vicky@example.com")
    product = client.get("/products", params={"limit": 100}).json()[0]
    response = client.post(
        "/orders", json={"items": [{"product_id": product["id"], "quantity": 1}]}, headers=vicky
    )
    assert response.status_code == 403


def test_order_status_state_machine(client: TestClient) -> None:
    admin = _auth(client, "admin@example.com")
    carol = _auth(client, "carol@example.com")
    pending = next(
        o for o in client.get("/orders/me", headers=carol).json() if o["status"] == "pending"
    )
    bad = client.patch(
        f"/orders/{pending['id']}/status", json={"status": "delivered"}, headers=admin
    )
    assert bad.status_code == 400
    ok = client.patch(
        f"/orders/{pending['id']}/status", json={"status": "confirmed"}, headers=admin
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "confirmed"
    buyer = client.patch(
        f"/orders/{pending['id']}/status", json={"status": "shipped"}, headers=carol
    )
    assert buyer.status_code == 403


def test_seed_is_deterministic(tmp_path: Path) -> None:
    import sqlite3

    rows = []
    for name in ("a.db", "b.db"):
        db = build_seed_db(tmp_path / name)
        con = sqlite3.connect(db)
        rows.append(
            [
                con.execute(f"select * from {t} order by id").fetchall()
                for t in ("vendors", "products", "orders", "order_items")
            ]
        )
        con.close()
    assert rows[0] == rows[1]
