# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, Query
from platform_kit import app, get_db, require_role


@app.get("/admin/orders")
def all_orders(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(require_role("Admin")),
    db=Depends(get_db),
):
    orders = db.execute(
        "SELECT id, customer_id, status, total FROM orders ORDER BY id LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    items = []
    for order in orders:
        customer = db.execute(
            "SELECT name FROM users WHERE id = ?", (order["customer_id"],)
        ).fetchone()
        items.append({**dict(order), "customer_name": customer["name"] if customer else None})
    return {"items": items, "limit": limit, "offset": offset}
