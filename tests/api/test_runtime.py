"""The API runtime end to end: Starlette TestClient against the benchmark database."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import lattice.api.runtime as runtime
from bench.api_datasets import TOKENS, USER_IDS, api_profile
from lattice.api.runtime import ApiRuntimeError, serve_source
from lattice.diagnostics import Span
from lattice.runtime import LatticeRuntimeError
from tests.api.conftest import source

CANCEL = """
@endpoint("POST", "/orders/{order_id}/cancel", auth=Customer)
def cancel(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound | Conflict:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    found = where(mine, col("id") == order_id)
    order = one(found, else_=NotFound("order"))
    ok = require(order, col("status") == "pending", else_=Conflict("order is not pending"))
    done = update(ok, status="cancelled")
    return done
"""

LIST = """
@endpoint("GET", "/orders", auth=Customer)
def list_orders(user: CurrentUser, limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0) -> Page:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    ordered = sort(mine, by=["created_at"], desc=True)
    result = page(ordered, limit=limit, offset=offset)
    shown = select(result, cols=["id", "customer_id", "status", "created_at", "total"])
    return shown
"""

ADDRESSES = """
@endpoint("POST", "/addresses", auth=Customer)
def add_address(user: CurrentUser, line1: Str(min_len=1, max_len=80),
                city: Str(min_len=1, max_len=40), postcode: Str(min_len=5, max_len=5)) -> Created:
    row = insert(Addresses, owner=user, line1=line1, city=city, zip=postcode)
    return row

@endpoint("DELETE", "/addresses/{address_id}", auth=Customer)
def remove_address(address_id: Int(ge=1), user: CurrentUser) -> NoContent | NotFound:
    rows = fetch(Addresses)
    mine = owned(rows, by=user)
    found = where(mine, col("id") == address_id)
    target = one(found, else_=NotFound("address"))
    gone = delete(target)
    return gone
"""

PRODUCTS = """
@endpoint("POST", "/products/{product_id}/restock", auth=Admin)
def restock(product_id: Int(ge=1), qty: Int(ge=1, le=1000), user: CurrentUser) -> One | NotFound:
    p = fetch(Products)
    found = where(p, col("id") == product_id)
    row = one(found, else_=NotFound("product"))
    done = update(row, stock=col("stock") + qty)
    return done

@endpoint("GET", "/products/{product_id}", auth=Public)
def product(product_id: Int(ge=1)) -> One | NotFound:
    p = fetch(Products)
    found = where(p, col("id") == product_id)
    row = one(found, else_=NotFound("product"))
    shown = select(row, cols=["id", "name", "price", "active"])
    return shown

@endpoint("GET", "/products", auth=Public)
def search(category: OneOf("books", "games", "garden", "kitchen", "toys") = "books",
           min_price: Float(ge=0, le=10000) = 0.0, active: Bool = True,
           limit: Int(ge=1, le=100) = 50) -> Page:
    p = fetch(Products)
    in_category = where(p, col("category") == category)
    hit = where(in_category, (col("price") >= min_price) & (col("active") == active))
    ordered = sort(hit, by=["price", "id"])
    result = page(ordered, limit=limit)
    return result
"""


@contextmanager
def app_for(src: str, db: Path) -> Iterator[TestClient]:
    check, app = serve_source(source(src), api_profile(), db)
    assert app is not None, [d.render() for d in check.diagnostics]
    with TestClient(app) as client:
        yield client


def auth(handle: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[handle]}"}


def rows(db: Path, sql: str, *args: object) -> list[tuple]:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def order_of(db: Path, handle: str, status: str) -> int:
    found = rows(
        db,
        "SELECT id FROM orders WHERE customer_id = ? AND status = ? ORDER BY id",
        USER_IDS[handle],
        status,
    )
    assert found, (handle, status)
    return found[0][0]


# ---- the spec §2 cancel example ----------------------------------------------------------


def test_owner_cancels_pending_order(db: Path):
    oid = order_of(db, "alice", "pending")
    with app_for(CANCEL, db) as client:
        r = client.post(f"/orders/{oid}/cancel", headers=auth("alice"))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == oid and body["status"] == "cancelled"
    assert body["customer_id"] == USER_IDS["alice"]
    assert isinstance(body["total"], float) and isinstance(body["created_at"], str)
    assert rows(db, "SELECT status FROM orders WHERE id = ?", oid) == [("cancelled",)]


def test_other_customer_gets_404_and_nothing_changes(db: Path):
    oid = order_of(db, "alice", "pending")
    with app_for(CANCEL, db) as client:
        r = client.post(f"/orders/{oid}/cancel", headers=auth("bob"))
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "detail": "order"}
    assert rows(db, "SELECT status FROM orders WHERE id = ?", oid) == [("pending",)]


def test_admin_may_cancel_any_order(db: Path):
    oid = order_of(db, "bob", "pending")
    with app_for(CANCEL, db) as client:
        r = client.post(f"/orders/{oid}/cancel", headers=auth("admin"))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_non_pending_order_is_a_conflict(db: Path):
    oid = order_of(db, "alice", "shipped")
    with app_for(CANCEL, db) as client:
        r = client.post(f"/orders/{oid}/cancel", headers=auth("alice"))
    assert r.status_code == 409
    assert r.json() == {"error": "conflict", "detail": "order is not pending"}
    assert rows(db, "SELECT status FROM orders WHERE id = ?", oid) == [("shipped",)]


def test_missing_order_is_404(db: Path):
    with app_for(CANCEL, db) as client:
        r = client.post("/orders/999999/cancel", headers=auth("alice"))
    assert r.status_code == 404 and r.json()["error"] == "not_found"


@pytest.mark.parametrize("bad", ["abc", "1.5", "0", "-3"])
def test_bad_id_is_422(db: Path, bad: str):
    with app_for(CANCEL, db) as client:
        r = client.post(f"/orders/{bad}/cancel", headers=auth("alice"))
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "validation"
    assert body["detail"][0]["loc"] == ["path", "order_id"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer"},
    ],
)
def test_missing_or_bad_token_is_401(db: Path, headers: dict[str, str]):
    oid = order_of(db, "alice", "pending")
    with app_for(CANCEL, db) as client:
        r = client.post(f"/orders/{oid}/cancel", headers=headers)
    assert r.status_code == 401 and r.json() == {"error": "unauthorized"}


def test_auth_precedes_validation(db: Path):
    with app_for(CANCEL, db) as client:
        assert client.post("/orders/abc/cancel").status_code == 401


def test_customer_cannot_call_admin_endpoint(db: Path):
    with app_for(PRODUCTS, db) as client:
        r = client.post("/products/1/restock", json={"qty": 5}, headers=auth("alice"))
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}


# ---- pagination ------------------------------------------------------------------------------


def test_list_is_paged_owned_and_stable(db: Path):
    alice = USER_IDS["alice"]
    expected = rows(db, "SELECT id FROM orders WHERE customer_id = ?", alice)
    with app_for(LIST, db) as client:
        first = client.get("/orders", headers=auth("alice"))
        assert first.status_code == 200
        body = first.json()
        assert (body["limit"], body["offset"]) == (20, 0)
        seen: list[int] = []
        for offset in range(0, len(expected) + 3, 3):
            r = client.get("/orders", params={"limit": 3, "offset": offset}, headers=auth("alice"))
            page = r.json()
            assert (page["limit"], page["offset"]) == (3, offset)
            assert len(page["items"]) <= 3
            assert all(item["customer_id"] == alice for item in page["items"])
            seen += [item["id"] for item in page["items"]]
        again = client.get("/orders", params={"limit": 3, "offset": 3}, headers=auth("alice"))
    assert sorted(seen) == sorted(i for (i,) in expected)  # every row once, no duplicates
    assert [i["id"] for i in again.json()["items"]] == seen[3:6]  # stable
    dates = [rows(db, "SELECT created_at FROM orders WHERE id = ?", i)[0][0] for i in seen]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.parametrize("query", [{"limit": 101}, {"limit": 0}, {"offset": -1}, {"limit": "x"}])
def test_page_bounds_are_validated(db: Path, query: dict[str, object]):
    with app_for(LIST, db) as client:
        r = client.get("/orders", params=query, headers=auth("alice"))
    assert r.status_code == 422 and r.json()["error"] == "validation"


def test_admin_sees_every_customers_rows(db: Path):
    with app_for(LIST, db) as client:
        r = client.get("/orders", params={"limit": 100}, headers=auth("admin"))
    owners = {item["customer_id"] for item in r.json()["items"]}
    assert len(owners) > 5


def test_query_params_typed_with_defaults(db: Path):
    with app_for(PRODUCTS, db) as client:
        default = client.get("/products").json()
        games = client.get(
            "/products", params={"category": "games", "min_price": "10.5", "active": "false"}
        )
        bad = client.get("/products", params={"category": "cars"})
        bad_bool = client.get("/products", params={"active": "maybe"})
    expected = rows(
        db,
        "SELECT id FROM products WHERE category = 'books' AND active = 1 ORDER BY price, id",
    )
    assert [p["id"] for p in default["items"]] == [i for (i,) in expected]
    assert all(p["active"] is True for p in default["items"])
    assert games.status_code == 200
    assert all(
        p["category"] == "games" and p["price"] >= 10.5 and p["active"] is False
        for p in games.json()["items"]
    )
    assert bad.status_code == 422 and bad.json()["detail"][0]["loc"] == ["query", "category"]
    assert bad_bool.status_code == 422


# ---- writes -------------------------------------------------------------------------------


def test_insert_creates_owned_row(db: Path):
    before = rows(db, "SELECT COUNT(*) FROM addresses")[0][0]
    payload = {"line1": "1 Main St", "city": "Springfield", "postcode": "12345"}
    with app_for(ADDRESSES, db) as client:
        r = client.post("/addresses", json=payload, headers=auth("carol"))
    assert r.status_code == 201
    body = r.json()
    assert body["customer_id"] == USER_IDS["carol"]
    assert (body["line1"], body["city"], body["zip"]) == ("1 Main St", "Springfield", "12345")
    assert rows(db, "SELECT COUNT(*) FROM addresses")[0][0] == before + 1
    assert rows(
        db, "SELECT customer_id, line1, city, zip FROM addresses WHERE id = ?", body["id"]
    ) == [(USER_IDS["carol"], "1 Main St", "Springfield", "12345")]


@pytest.mark.parametrize(
    "payload",
    [
        {"line1": "x", "city": "y"},  # missing field
        {"line1": "x", "city": "y", "postcode": "123"},  # too short
        {"line1": "", "city": "y", "postcode": "12345"},  # too short
        {"line1": 5, "city": "y", "postcode": "12345"},  # wrong type
    ],
)
def test_insert_validation(db: Path, payload: dict[str, object]):
    before = rows(db, "SELECT COUNT(*) FROM addresses")[0][0]
    with app_for(ADDRESSES, db) as client:
        r = client.post("/addresses", json=payload, headers=auth("carol"))
    assert r.status_code == 422 and r.json()["error"] == "validation"
    assert rows(db, "SELECT COUNT(*) FROM addresses")[0][0] == before


def test_body_must_be_a_json_object(db: Path):
    with app_for(ADDRESSES, db) as client:
        r1 = client.post(
            "/addresses",
            content=b"not json",
            headers={**auth("carol"), "content-type": "application/json"},
        )
        r2 = client.post("/addresses", json=[1, 2], headers=auth("carol"))
    assert r1.status_code == 422 and r2.status_code == 422


# ---- accepted-but-unrepresentable inputs are 422, never 500 (B5) -----------------------------

WIDE = """
@endpoint("POST", "/reviews", auth=Customer)
def review(user: CurrentUser, product_id: Int(ge=1), rating: Int(ge=1, le=5),
           body: Str(max_len=500)) -> Created:
    row = insert(Reviews, owner=user, product_id=product_id, rating=rating, body=body)
    return row

@endpoint("GET", "/scaled", auth=Public)
def scaled(k: Float, n: Int) -> Page:
    p = fetch(Products)
    d = derive(p, scaled=col("price") * k, n=col("id") + n)
    s = sort(d, by=["id"])
    result = page(s, limit=2)
    return result
"""


def _validation_loc(r) -> list[object]:
    assert r.status_code == 422, (r.status_code, r.text)
    body = r.json()
    assert body["error"] == "validation"
    return body["detail"][0]["loc"]


@pytest.mark.parametrize(
    "bad", ["99999999999999999999", "9223372036854775808", "-9223372036854775809", "١٢"]
)
def test_out_of_range_or_non_ascii_path_int_is_422(db: Path, bad: str):
    with app_for(CANCEL, db) as client:
        r = client.post(f"/orders/{bad}/cancel", headers=auth("alice"))
    assert _validation_loc(r) == ["path", "order_id"]


def test_int64_bounds_are_accepted(db: Path):
    with app_for(WIDE, db) as client:
        top = client.get("/scaled", params={"k": "1", "n": str(2**63 - 1 - 100)})
        bottom = client.get("/scaled", params={"k": "1", "n": str(-(2**63))})
    assert top.status_code == 200 and bottom.status_code == 200


def test_out_of_range_query_offset_is_422(db: Path):
    with app_for(LIST, db) as client:
        r = client.get("/orders", params={"offset": "99999999999999999999"}, headers=auth("alice"))
    assert _validation_loc(r) == ["query", "offset"]


@pytest.mark.parametrize(
    "payload",
    [
        b'{"product_id": 99999999999999999999, "rating": 3, "body": "x"}',
        b'{"product_id": 1e300, "rating": 3, "body": "x"}',
    ],
)
def test_out_of_range_body_int_is_422(db: Path, payload: bytes):
    before = rows(db, "SELECT COUNT(*) FROM reviews")[0][0]
    headers = {**auth("alice"), "content-type": "application/json"}
    with app_for(WIDE, db) as client:
        r = client.post("/reviews", content=payload, headers=headers)
    assert _validation_loc(r) == ["body", "product_id"]
    assert rows(db, "SELECT COUNT(*) FROM reviews")[0][0] == before


@pytest.mark.parametrize("k", ["1" + "0" * 400, "1e999", "nan", "inf", "١٢", "1_0"])
def test_unrepresentable_query_float_is_422(db: Path, k: str):
    with app_for(WIDE, db) as client:
        r = client.get("/scaled", params={"k": k, "n": "0"})
    assert _validation_loc(r) == ["query", "k"]


def test_huge_body_number_for_a_float_is_422(db: Path):
    src = """
    @endpoint("POST", "/price", auth=Admin)
    def price(p: Float(ge=0, le=1000), user: CurrentUser) -> Created:
        r = insert(Products, name="n", category="c", price=p, stock=1, active=True)
        return r
    """
    headers = {**auth("admin"), "content-type": "application/json"}
    with app_for(src, db) as client:
        r = client.post("/price", content=b'{"p": 1' + b"0" * 400 + b"}", headers=headers)
        nan = client.post("/price", content=b'{"p": NaN}', headers=headers)
    assert _validation_loc(r) == ["body", "p"]
    assert _validation_loc(nan) == ["body", "p"]


def test_lone_surrogate_string_is_422(db: Path):
    before = rows(db, "SELECT COUNT(*) FROM reviews")[0][0]
    headers = {**auth("alice"), "content-type": "application/json"}
    with app_for(WIDE, db) as client:
        r = client.post(
            "/reviews",
            content=b'{"product_id": 1, "rating": 3, "body": "\\ud800"}',
            headers=headers,
        )
    assert _validation_loc(r) == ["body", "body"]
    assert rows(db, "SELECT COUNT(*) FROM reviews")[0][0] == before


def test_non_finite_result_floats_serialize_as_null(db: Path):
    with app_for(WIDE, db) as client:
        r = client.get("/scaled", params={"k": "1e308", "n": "0"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items and all(item["scaled"] is None for item in items)
    assert all(isinstance(item["price"], float) for item in items)


def test_delete_persists_and_respects_ownership(db: Path):
    alice_addr = rows(db, "SELECT id FROM addresses WHERE customer_id = ?", USER_IDS["alice"])
    assert alice_addr
    aid = alice_addr[0][0]
    with app_for(ADDRESSES, db) as client:
        other = client.delete(f"/addresses/{aid}", headers=auth("bob"))
        assert other.status_code == 404
        assert rows(db, "SELECT COUNT(*) FROM addresses WHERE id = ?", aid) == [(1,)]
        mine = client.delete(f"/addresses/{aid}", headers=auth("alice"))
        assert mine.status_code == 204 and mine.content == b""
        again = client.delete(f"/addresses/{aid}", headers=auth("alice"))
    assert rows(db, "SELECT COUNT(*) FROM addresses WHERE id = ?", aid) == [(0,)]
    assert again.status_code == 404


def test_update_with_expression_persists(db: Path):
    (stock,) = rows(db, "SELECT stock FROM products WHERE id = 3")[0]
    with app_for(PRODUCTS, db) as client:
        r = client.post("/products/3/restock", json={"qty": 7}, headers=auth("admin"))
    assert r.status_code == 200
    assert r.json()["stock"] == stock + 7 and r.json()["id"] == 3
    assert r.json()["active"] in (True, False)
    assert rows(db, "SELECT stock FROM products WHERE id = 3") == [(stock + 7,)]


def test_transaction_rolls_back_when_a_later_require_fails(db: Path):
    src = """
    @endpoint("POST", "/orders/{order_id}/ship", auth=Admin)
    def ship(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound | Conflict:
        orders = fetch(Orders)
        found = where(orders, col("id") == order_id)
        order = one(found, else_=NotFound("order"))
        done = update(order, status="shipped")
        ok = require(done, col("total") < 0.0, else_=Conflict("never"))
        return ok
    """
    oid = order_of(db, "alice", "pending")
    with app_for(src, db) as client:
        r = client.post(f"/orders/{oid}/ship", headers=auth("admin"))
    assert r.status_code == 409 and r.json() == {"error": "conflict", "detail": "never"}
    assert rows(db, "SELECT status FROM orders WHERE id = ?", oid) == [("pending",)]


def test_insert_then_failed_require_leaves_no_row(db: Path):
    src = """
    @endpoint("POST", "/reviews", auth=Customer)
    def review(user: CurrentUser, product_id: Int(ge=1), rating: Int(ge=1, le=5),
               body: Str(max_len=500)) -> Created | BadRequest:
        row = insert(Reviews, owner=user, product_id=product_id, rating=rating, body=body)
        ok = require(row, col("rating") >= 3, else_=BadRequest("too harsh"))
        return ok
    """
    before = rows(db, "SELECT COUNT(*) FROM reviews")[0][0]
    with app_for(src, db) as client:
        bad = client.post(
            "/reviews", json={"product_id": 1, "rating": 1, "body": "meh"}, headers=auth("alice")
        )
        good = client.post(
            "/reviews", json={"product_id": 1, "rating": 4, "body": "ok"}, headers=auth("alice")
        )
    assert bad.status_code == 400 and bad.json()["error"] == "bad_request"
    assert good.status_code == 201 and good.json()["customer_id"] == USER_IDS["alice"]
    assert rows(db, "SELECT COUNT(*) FROM reviews")[0][0] == before + 1


# ---- serialization and failures --------------------------------------------------------------


def test_serialization_includes_only_returned_columns(db: Path):
    with app_for(PRODUCTS, db) as client:
        r = client.get("/products/1")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"id", "name", "price", "active"}
    assert isinstance(body["active"], bool) and isinstance(body["price"], float)


def test_user_row_without_private_columns(db: Path):
    src = """
    @endpoint("GET", "/me", auth=Customer)
    def me(user: CurrentUser) -> One:
        shown = select(user, cols=["id", "email", "name", "role"])
        return shown
    """
    with app_for(src, db) as client:
        r = client.get("/me", headers=auth("bob"))
    assert r.status_code == 200
    assert r.json() == {
        "id": 2,
        "email": "bob@example.com",
        "name": "Bob Martinez",
        "role": "Customer",
    }


def test_one_matching_many_rows_is_a_500(db: Path):
    src = """
    @endpoint("GET", "/customers/{customer_id}/any-order", auth=Admin)
    def any_order(customer_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
        orders = fetch(Orders)
        found = where(orders, col("customer_id") == customer_id)
        row = one(found, else_=NotFound("order"))
        return row
    """
    with app_for(src, db) as client:
        r = client.get(f"/customers/{USER_IDS['alice']}/any-order", headers=auth("admin"))
    assert r.status_code == 500
    assert r.json()["error"] == "internal" and "R310" in r.json()["detail"]


def test_unexpected_database_error_is_a_500(db: Path):
    src = """
    @endpoint("POST", "/addresses", auth=Customer)
    def add(user: CurrentUser, line1: Str(max_len=80)) -> Created:
        row = insert(Addresses, owner=user, line1=line1, city="c", zip="12345")
        return row
    """
    # The checker now rejects inserts that miss NOT NULL columns (L404), so the
    # database error comes from a trigger the program cannot know about.
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TRIGGER no_addresses BEFORE INSERT ON addresses "
        "BEGIN SELECT RAISE(ABORT, 'addresses are frozen'); END"
    )
    conn.commit()
    conn.close()
    before = rows(db, "SELECT COUNT(*) FROM addresses")[0][0]
    with app_for(src, db) as client:
        r = client.post("/addresses", json={"line1": "x"}, headers=auth("alice"))
    assert r.status_code == 500 and r.json()["error"] == "internal"
    assert "addresses are frozen" in r.json()["detail"]
    assert rows(db, "SELECT COUNT(*) FROM addresses")[0][0] == before


def test_unknown_route_is_json_404(db: Path):
    with app_for(CANCEL, db) as client:
        r = client.get("/nope")
        m = client.get("/orders/1/cancel", headers=auth("alice"))
    assert r.status_code == 404 and r.json() == {"error": "not_found"}
    assert m.status_code == 405


def test_rejected_program_builds_no_app(db: Path):
    check, app = serve_source(
        source(CANCEL.replace("mine = owned(orders, by=user)", "mine = orders")), api_profile(), db
    )
    assert app is None and not check.ok


def test_runtime_errors_render():
    err = ApiRuntimeError("R310", Span(3, 5), "one() matched 2 rows")
    assert isinstance(err, LatticeRuntimeError)
    assert err.render() == "3:5: error R310: one() matched 2 rows"


# ---- efficiency: statements per request -------------------------------------------------------


@contextmanager
def counting() -> Iterator[list[str]]:
    statements: list[str] = []
    connections = [0]
    previous = runtime.on_connect

    def hook(conn: sqlite3.Connection) -> None:
        connections[0] += 1
        conn.set_trace_callback(statements.append)

    runtime.on_connect = hook
    try:
        yield statements
    finally:
        runtime.on_connect = previous


def data_statements(statements: list[str]) -> list[str]:
    return [
        s
        for s in statements
        if s.lstrip().split(None, 1)[0].upper() not in ("BEGIN", "COMMIT", "ROLLBACK")
    ]


def test_list_query_count_is_constant_in_rows(db: Path):
    counts = []
    with app_for(LIST, db) as client:
        for limit in (1, 10, 100):
            with counting() as statements:
                r = client.get("/orders", params={"limit": limit}, headers=auth("admin"))
            assert r.status_code == 200 and len(r.json()["items"]) == limit
            counts.append(len(data_statements(statements)))
    # grow the table: 300 more orders; the count must not move
    conn = sqlite3.connect(str(db))
    conn.executemany(
        "INSERT INTO orders (customer_id, status, created_at, total) "
        "VALUES (?, 'paid', '2025-01-01', 1.0)",
        [(USER_IDS["alice"],)] * 300,
    )
    conn.commit()
    conn.close()
    with app_for(LIST, db) as client, counting() as statements:
        r = client.get("/orders", params={"limit": 100}, headers=auth("alice"))
    assert len(r.json()["items"]) == 100
    counts.append(len(data_statements(statements)))
    assert counts == [2, 2, 2, 2]  # auth + one SELECT


def test_cancel_query_count(db: Path):
    oid = order_of(db, "alice", "pending")
    with app_for(CANCEL, db) as client, counting() as statements:
        client.post(f"/orders/{oid}/cancel", headers=auth("alice"))
    data = data_statements(statements)
    assert len(data) == 3  # auth, the read, the update
    assert data[0].lstrip().startswith("SELECT") and "UPDATE" in data[2]


def test_every_connection_goes_through_on_connect(db: Path):
    seen: list[sqlite3.Connection] = []
    previous = runtime.on_connect
    runtime.on_connect = seen.append
    try:
        with app_for(CANCEL + LIST, db) as client:
            client.get("/orders", headers=auth("alice"))
            client.post("/orders/999999/cancel", headers=auth("alice"))
            client.get("/orders")  # 401 before any connection
    finally:
        runtime.on_connect = previous
    assert len(seen) == 2


def test_public_endpoint_needs_no_auth_statement(db: Path):
    with app_for(PRODUCTS, db) as client, counting() as statements:
        r = client.get("/products/2", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 200
    assert len(data_statements(statements)) == 1


def test_reading_the_caller_costs_only_the_auth_statement(db: Path):
    src = """
    @endpoint("GET", "/me", auth=Customer)
    def me(user: CurrentUser) -> One:
        shown = select(user, cols=["id", "name"])
        return shown
    """
    with app_for(src, db) as client, counting() as statements:
        r = client.get("/me", headers=auth("alice"))
    assert r.json() == {"id": 1, "name": "Alice Nguyen"}
    assert len(data_statements(statements)) == 1


def test_selecting_from_a_write_result_costs_nothing(db: Path):
    src = """
    @endpoint("POST", "/reviews", auth=Customer)
    def review(user: CurrentUser, product_id: Int(ge=1), rating: Int(ge=1, le=5),
               body: Str(max_len=500)) -> Created:
        row = insert(Reviews, owner=user, product_id=product_id, rating=rating, body=body)
        shown = select(row, cols=["id", "rating"])
        return shown
    """
    with app_for(src, db) as client, counting() as statements:
        r = client.post(
            "/reviews", json={"product_id": 2, "rating": 5, "body": "great"}, headers=auth("bob")
        )
    assert r.status_code == 201 and set(r.json()) == {"id", "rating"}
    assert len(data_statements(statements)) == 2  # auth + INSERT ... RETURNING


def test_require_on_the_caller(db: Path):
    src = """
    @endpoint("GET", "/customer-only", auth=Customer)
    def customer_only(user: CurrentUser) -> One | Forbidden:
        ok = require(user, col("role") == "Customer", else_=Forbidden("customers only"))
        shown = select(ok, cols=["id"])
        return shown
    """
    with app_for(src, db) as client, counting() as statements:
        mine = client.get("/customer-only", headers=auth("alice"))
    assert mine.status_code == 200 and mine.json() == {"id": 1}
    assert len(data_statements(statements)) == 2  # auth + the flag SELECT over the caller row
    with app_for(src, db) as client:
        admin = client.get("/customer-only", headers=auth("admin"))
    assert admin.status_code == 403
    assert admin.json() == {"error": "forbidden", "detail": "customers only"}


def test_require_on_a_view_of_checked_rows(db: Path):
    src = """
    @endpoint("GET", "/orders/{order_id}/status", auth=Customer)
    def status(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound | Conflict:
        orders = fetch(Orders)
        mine = owned(orders, by=user)
        found = where(mine, col("id") == order_id)
        order = one(found, else_=NotFound("order"))
        slim = select(order, cols=["id", "status"])
        ok = require(slim, col("status") != "cancelled", else_=Conflict("cancelled"))
        return ok
    """
    live = order_of(db, "alice", "paid")
    dead = order_of(db, "alice", "cancelled")
    with app_for(src, db) as client:
        assert client.get(f"/orders/{live}/status", headers=auth("alice")).json() == {
            "id": live,
            "status": "paid",
        }
        assert client.get(f"/orders/{dead}/status", headers=auth("alice")).status_code == 409


# ---- owned through a parent (OrderItems -> Orders) ------------------------------------

ITEMS = """
@endpoint("GET", "/items", auth=Customer)
def my_items(user: CurrentUser, limit: Int(ge=1, le=100) = 100) -> Page:
    items = fetch(OrderItems)
    mine = owned(items, by=user)
    ordered = sort(mine, by=["id"])
    result = page(ordered, limit=limit)
    return result
"""


def test_items_owned_through_their_order(db: Path):
    expected = rows(
        db,
        "SELECT i.id FROM order_items i JOIN orders o ON o.id = i.order_id "
        "WHERE o.customer_id = ? ORDER BY i.id LIMIT 100",
        USER_IDS["alice"],
    )
    assert expected
    with app_for(ITEMS, db) as client:
        with counting() as statements:
            r = client.get("/items", headers=auth("alice"))
        assert r.status_code == 200
        assert [i["id"] for i in r.json()["items"]] == [e[0] for e in expected]
        assert len(data_statements(statements)) == 2  # auth + one SELECT
        dave = client.get("/items", headers=auth("dave"))
        assert dave.json()["items"] == []  # dave has no orders
        admin = client.get("/items", headers=auth("admin"))
    every = rows(db, "SELECT id FROM order_items ORDER BY id LIMIT 100")
    assert [i["id"] for i in admin.json()["items"]] == [e[0] for e in every]


# ---- joins on differently-named keys ----------------------------------------------------

MAPPED_F01 = """
@endpoint("GET", "/orders", auth=Customer)
def list_orders(user: CurrentUser, limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0) -> Page:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    items = fetch(OrderItems)
    my_items = owned(items, by=user)
    units_per_order = group(my_items, by=["order_id"], units=agg.sum(col("qty")))
    joined = join(mine, units_per_order, on={"id": "order_id"}, how="left")
    filled = derive(joined, units=coalesce(col("units"), 0))
    shown = select(filled, cols=["id", "status", "created_at", "total", "units"])
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
"""


def test_mapped_join_is_one_statement_with_the_mapped_condition():
    from lattice.api.ir import ReadStep, lower_api
    from tests.api.conftest import checked

    (plan,) = lower_api(checked(MAPPED_F01))
    (read,) = plan.steps
    assert isinstance(read, ReadStep)
    assert 'ON l."id" = r."order_id"' in read.sql.text
    assert plan.schema.names() == ("id", "status", "created_at", "total", "units")


def test_mapped_join_returns_each_order_with_its_units(db: Path):
    with app_for(MAPPED_F01, db) as client:
        resp = client.get("/orders", headers=auth("bob"), params={"limit": 100})
    assert resp.status_code == 200
    got = [(r["id"], r["units"]) for r in resp.json()["items"]]
    want = rows(
        db,
        "SELECT o.id, COALESCE(SUM(i.qty), 0) FROM orders o "
        "LEFT JOIN order_items i ON i.order_id = o.id WHERE o.customer_id = ? "
        "GROUP BY o.id ORDER BY o.id",
        USER_IDS["bob"],
    )
    assert got == [tuple(r) for r in want] and got


def test_mapped_join_passes_every_f01_case(tmp_path: Path):
    from bench.api_datasets import build_db
    from bench.api_runner import run_cases_lattice
    from bench.api_tasks import API_TASKS

    task = next(t for t in API_TASKS if t.id == "f01")
    results = run_cases_lattice(
        source(MAPPED_F01), tmp_path / "w", task, db_builder=build_db, profile=api_profile()
    )
    assert results.cases and all(c.passed for c in results.cases), [
        (c.name, c.reason) for c in results.cases if not c.passed
    ]
